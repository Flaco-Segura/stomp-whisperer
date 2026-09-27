"""Decoding of the "PTCF" patch format stored on the pedal.

Layout based on mungewell/zoom-zt2's `decode_preset.py`:
https://github.com/mungewell/zoom-zt2

    offset  size  field
    0       4     b"PTCF"
    4       4     length (uint32 LE)
    8       4     version (uint32 LE)
    12      4     fx_count (uint32 LE)
    16      4     target (bitfield: which pedal models the patch is for)
    20      6     unknown
    26      10    name (ASCII, space/NUL padded)
    36      4*n   effect ids (uint32 LE, one per effect slot)
    ...           chunks: TXJ1, TXE1, EDTB, PPRM/PRM2, NAME (version > 1)

Each chunk is a 4-byte tag followed by a uint32 LE length and its payload.
The optional NAME chunk holds the full (possibly longer) patch name.

The EDTB chunk holds one 24-byte record per effect slot. Read as a 192-bit
little-endian integer, from the least significant bit up:

    bits  field
    1     enabled
    29    effect id (same value as in the header id list)
    12    param1 .. param5 (12 bits each)
    8     param6 .. param8 (8 bits each)
    12    param9 .. param12 (12 bits each)
    30    unknown

Only the first `length` bytes of a download are the patch: the pedal pads the
rest of the slot with leftovers from whatever it held before.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

MAGIC = b"PTCF"
_HEADER = struct.Struct("<4sIIII6s10s")
_EDTB_RECORD_SIZE = 24
_ID_BITS = 29
_PARAM_BITS = (12, 12, 12, 12, 12, 8, 8, 8, 12, 12, 12, 12)
PARAM_COUNT = len(_PARAM_BITS)


def param_limit(index: int) -> int:
    """Largest value the EDTB record can store for parameter `index` (0-based)."""
    return (1 << _PARAM_BITS[index]) - 1


class PatchFormatError(ValueError):
    """Raised when patch data does not look like a PTCF patch."""


@dataclass
class Effect:
    id: int
    enabled: bool
    params: list[int]
    extra: int = 0  # the record's 30 unknown top bits, written back as read
    origin: int | None = None  # position (0-based) in the patch as read; None if added since


@dataclass
class Patch:
    name: str
    version: int
    target: int
    effect_ids: list[int]
    effects: list[Effect] = field(default_factory=list)
    chunks: dict[str, bytes] = field(default_factory=dict)
    reserved: bytes = bytes(6)  # the header's unknown bytes, written back as read

    @property
    def display_name(self) -> str:
        """Name with the pedal's two-line padding collapsed to single spaces."""
        return " ".join(self.name.split())


# The pedal shows a name on two lines of 14 characters; Zoom pads the first line
# with spaces so words aren't split across them ("OverDrive     +Delay").
NAME_LINE = 14
NAME_LENGTH = 2 * NAME_LINE


def format_name(text: str) -> str:
    """Lay out a new patch name the way Zoom's own are stored.

    Words that don't fit the first line move to the second; a name with no space to
    break at is split at the line end. Raises ValueError if it can't fit or be shown.
    """
    name = " ".join(text.split())
    if not name:
        raise ValueError("The name can't be empty")
    if any(not " " <= char <= "~" for char in name):
        raise ValueError("Only plain ASCII letters, digits and symbols can be shown on the pedal")
    if len(name) <= NAME_LINE:
        return name
    cut = name.rfind(" ", 0, NAME_LINE)  # a full first line would run into the second
    splits = [(name[:cut], name[cut + 1:])] if cut > 0 else []
    splits.append((name[:NAME_LINE], name[NAME_LINE:].lstrip()))
    for first, second in splits:
        if len(second) <= NAME_LINE:
            return first.ljust(NAME_LINE) + second
    raise ValueError(f"The name doesn't fit the pedal's two lines of {NAME_LINE} characters")


def _decode_name(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace").rstrip()


def _decode_effect(record: bytes, origin: int | None = None) -> Effect:
    bits = int.from_bytes(record, "little")
    enabled = bool(bits & 1)
    bits >>= 1
    effect_id = bits & ((1 << _ID_BITS) - 1)
    bits >>= _ID_BITS
    params = []
    for width in _PARAM_BITS:
        params.append(bits & ((1 << width) - 1))
        bits >>= width
    return Effect(id=effect_id, enabled=enabled, params=params, extra=bits, origin=origin)


def _decode_effects(edtb: bytes) -> list[Effect]:
    return [_decode_effect(edtb[i:i + _EDTB_RECORD_SIZE], i // _EDTB_RECORD_SIZE)
            for i in range(0, len(edtb) - _EDTB_RECORD_SIZE + 1, _EDTB_RECORD_SIZE)]


def _read_chunks(data: bytes, offset: int) -> dict[str, bytes]:
    chunks: dict[str, bytes] = {}
    while offset + 8 <= len(data):
        tag = data[offset:offset + 4]
        if not tag.isalnum():
            break  # padding / trailing garbage
        (length,) = struct.unpack_from("<I", data, offset + 4)
        start = offset + 8
        chunks[tag.decode("ascii")] = bytes(data[start:start + length])
        offset = start + length
    return chunks


def parse_patch(data: bytes) -> Patch:
    if len(data) < _HEADER.size or data[:4] != MAGIC:
        raise PatchFormatError("missing PTCF header")

    _magic, length, version, fx_count, target, reserved, raw_name = _HEADER.unpack_from(data)
    if _HEADER.size <= length <= len(data):
        data = data[:length]
    ids_end = _HEADER.size + 4 * fx_count
    if ids_end > len(data):
        raise PatchFormatError(f"fx_count={fx_count} exceeds patch size")
    effect_ids = list(struct.unpack_from(f"<{fx_count}I", data, _HEADER.size))

    chunks = _read_chunks(data, ids_end)
    name = _decode_name(chunks["NAME"]) if "NAME" in chunks else _decode_name(raw_name)

    effects = _decode_effects(chunks.get("EDTB", b""))

    return Patch(name=name, version=version, target=target,
                 effect_ids=effect_ids, effects=effects, chunks=chunks, reserved=reserved)


# ---------- encoding ----------
#
# PRM2 holds 32 bytes of patch-wide settings. Read as a 256-bit little-endian
# integer (layout from mungewell/zoom-zt2's decode_preset.py), some fields name
# effect slots, so they must follow the effects when these are moved or removed:
# 11-bit fields with one bit per slot, and the index of the slot being edited.
# On a MS-50G+ only the preamp field is ever set: it marks exactly the effects of
# the PREAMP group (checked against all 100 patches of a real pedal).
_PRM2_SIZE = 32
PRM2_PREAMP_SLOTS = 161
_PRM2_SLOT_FIELDS = (22, 33, 55, 66, PRM2_PREAMP_SLOTS, 172, 183)
_PRM2_SLOT_MASK = (1 << 11) - 1
_PRM2_EDIT_SLOT = 85
_PRM2_EDIT_MASK = 0b111
_CHUNK_ORDER = ("TXJ1", "TXE1", "EDTB", "PRM2", "NAME")


def _encode_effect(effect: Effect) -> bytes:
    bits, shift = int(effect.enabled), 1
    bits |= effect.id << shift
    shift += _ID_BITS
    for width, value in zip(_PARAM_BITS, effect.params):
        bits |= value << shift
        shift += width
    bits |= effect.extra << shift
    return bits.to_bytes(_EDTB_RECORD_SIZE, "little")


def _remap_prm2(prm2: bytes, effects: list[Effect], preamp: list[bool | None] | None) -> bytes:
    if len(prm2) != _PRM2_SIZE:
        return prm2
    bits = int.from_bytes(prm2, "little")
    for shift in _PRM2_SLOT_FIELDS:
        old = (bits >> shift) & _PRM2_SLOT_MASK
        new = 0
        for index, effect in enumerate(effects):
            if shift == PRM2_PREAMP_SLOTS and preamp is not None and preamp[index] is not None:
                flag = preamp[index]
            else:
                flag = effect.origin is not None and bool(old >> effect.origin & 1)
            new |= flag << index
        bits = bits & ~(_PRM2_SLOT_MASK << shift) | new << shift
    edited = (bits >> _PRM2_EDIT_SLOT) & _PRM2_EDIT_MASK
    new_edited = next((i for i, e in enumerate(effects) if e.origin == edited), 0)
    bits = bits & ~(_PRM2_EDIT_MASK << _PRM2_EDIT_SLOT) | new_edited << _PRM2_EDIT_SLOT
    return bits.to_bytes(_PRM2_SIZE, "little")


def encode_patch(patch: Patch, preamp: list[bool | None] | None = None) -> bytes:
    """Build the PTCF bytes for `patch`: the inverse of `parse_patch`.

    Chunks other than EDTB, PRM2 and NAME are written back as read. `preamp` says,
    per effect, whether it belongs to the PREAMP group; where it's None (or it's
    omitted) an effect keeps the flag it was read with.
    """
    if len(patch.effects) > 11:
        raise ValueError("A patch holds at most 11 effect slots")
    if preamp is not None and len(preamp) != len(patch.effects):
        raise ValueError("One preamp flag is needed per effect")
    for effect in patch.effects:
        if not 0 <= effect.id < 1 << _ID_BITS:
            raise ValueError(f"Effect id {effect.id:#x} doesn't fit")
        if len(effect.params) != PARAM_COUNT or any(
                not 0 <= value <= param_limit(i) for i, value in enumerate(effect.params)):
            raise ValueError(f"Parameters out of range for effect {effect.id:#x}")

    raw_name = patch.name.encode("ascii").ljust(NAME_LENGTH, b" ")
    chunks = dict(patch.chunks)
    chunks["EDTB"] = b"".join(_encode_effect(e) for e in patch.effects)
    if "PRM2" in chunks:
        chunks["PRM2"] = _remap_prm2(chunks["PRM2"], patch.effects, preamp)
    if "NAME" in chunks or patch.version > 1:
        chunks["NAME"] = raw_name.ljust((len(raw_name) + 4) & ~3, b"\x00")
    ordered = [tag for tag in _CHUNK_ORDER if tag in chunks]
    ordered += [tag for tag in chunks if tag not in ordered]

    ids = [e.id for e in patch.effects]
    body = struct.pack(f"<{len(ids)}I", *ids)
    body += b"".join(tag.encode("ascii") + struct.pack("<I", len(chunks[tag])) + chunks[tag]
                     for tag in ordered)
    length = _HEADER.size + len(body)
    header = _HEADER.pack(MAGIC, length, patch.version, len(ids), patch.target,
                          patch.reserved, raw_name[:10])
    return header + body
