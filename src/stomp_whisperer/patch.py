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


class PatchFormatError(ValueError):
    """Raised when patch data does not look like a PTCF patch."""


@dataclass
class Effect:
    id: int
    enabled: bool
    params: list[int]


@dataclass
class Patch:
    name: str
    version: int
    target: int
    effect_ids: list[int]
    effects: list[Effect] = field(default_factory=list)
    chunks: dict[str, bytes] = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        """Name with the pedal's two-line padding collapsed to single spaces."""
        return " ".join(self.name.split())


def _decode_name(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace").rstrip()


def _decode_effect(record: bytes) -> Effect:
    bits = int.from_bytes(record, "little")
    enabled = bool(bits & 1)
    bits >>= 1
    effect_id = bits & ((1 << _ID_BITS) - 1)
    bits >>= _ID_BITS
    params = []
    for width in _PARAM_BITS:
        params.append(bits & ((1 << width) - 1))
        bits >>= width
    return Effect(id=effect_id, enabled=enabled, params=params)


def _decode_effects(edtb: bytes) -> list[Effect]:
    return [_decode_effect(edtb[i:i + _EDTB_RECORD_SIZE])
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

    _magic, length, version, fx_count, target, _unknown, raw_name = _HEADER.unpack_from(data)
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
                 effect_ids=effect_ids, effects=effects, chunks=chunks)
