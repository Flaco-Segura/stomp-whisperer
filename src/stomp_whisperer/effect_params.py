"""Parameter ranges and display labels, read from an effect's DSP program.

A ZD2's DATA chunk is a TI C6000 ELF program. Its ``.const`` section holds a
table of 0x38-byte descriptors, one per edit slot, starting with an "OnOff" entry
and then one for the effect itself; the remaining entries are the parameters, in
the order patches store their values:

    offset  size  field
    0       12    name (ASCII, NUL padded)
    12      4     maximum value (uint32 LE; the minimum is always 0)
    16      4     default value
    ...
    36      4     address of the display function, or 0 for a plain number

The display function turns a stored value into the text the pedal shows. It is
DSP code, so it isn't run; its symbol name says what it does:

- ``GetString_X`` reads fixed-width labels from a ``disp_prm_...`` table with one
  entry per value. Table names don't always match the function (``CmpAtk`` uses
  ``disp_prm_attack``), so a table whose size fits the range exactly is used when
  no name matches.
- ``GetString_offset_N`` / ``offset_minusN`` / ``offset_minusN_05`` shift the value
  (and ``_05`` scales it by 0.5); ``..._off_to_N`` shows 0 as OFF.
- ``GetString_..._Sync`` shows a number range and then tempo-synced note values
  from a ``disp_prm_..._BPM_sync`` table, drawn with the pedal's own note glyphs.
  Their thresholds and scales were read from the disassembled functions and are
  listed in ``_SYNC_RULES``; every effect sharing one of these names has the same
  code (checked with a TI C6000 objdump).

Anything else (e.g. delay times, whose scale depends on another parameter) is left
as the stored number rather than guessed.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass

_DESCRIPTOR_SIZE = 0x38
_TABLE_MARKER = b"OnOff\x00"
_HEADER_ENTRIES = 2  # "OnOff" and the effect's own entry

_SHT_SYMTAB = 2
_STT_OBJECT = 1
_STT_FUNC = 2

# e.g. "offset_1", "offset1", "offset_10", "offset_minus10", "offset_minus12_05"
_OFFSET_RULE = re.compile(r"^offset_?(minus)?(\d+)(?:_(\d+))?$", re.IGNORECASE)
_OFF_THEN_NUMBER = re.compile(r"off_to_(\d+)$", re.IGNORECASE)


@dataclass(frozen=True)
class _SyncRule:
    """How a ``GetString_..._Sync`` function turns a value into text.

    Values below `sync_from` are numbers: value + `offset`, except that above
    `fine_until` each step is worth 10 (delay times: 1 ms steps, then 10 ms steps).
    From `sync_from` on, the value picks a note from the BPM sync table, starting
    at entry `first_note`.
    """
    maximum: int  # the rule was verified for parameters with this range only
    sync_from: int
    offset: int = 0
    fine_until: int | None = None
    first_note: int = 0


_SYNC_RULES = {
    "1_5000_Sync": _SyncRule(962, sync_from=940, offset=1, fine_until=598, first_note=1),
    "1_2000_Sync": _SyncRule(762, sync_from=740, offset=1, fine_until=598, first_note=1),
    "10_2500_Sync": _SyncRule(745, sync_from=731, offset=10, fine_until=589, first_note=1),
    "offset_10_Sync": _SyncRule(835, sync_from=821, offset=10, fine_until=589, first_note=1),
    "0_100_Sync": _SyncRule(-1, sync_from=101),
    "0_50_Sync": _SyncRule(78, sync_from=51),
    "10_100_Sync": _SyncRule(100, sync_from=91, offset=10),
    "ofst_1_50_Sync": _SyncRule(77, sync_from=50, offset=1),
}
_ANY_MAXIMUM = -1

# The pedal's font draws note values with control characters; shortest first.
_NOTE_GLYPHS = {"\x16": "1/32", "\x17": "1/16", "\x18": "1/8", "\x19": "1/4", "\x1a": "1/2"}
_SHORTER_NOTE = {"1/16": "1/32", "1/8": "1/16", "1/4": "1/8", "1/2": "1/4"}

# Labels can be generated for any range, but a table that long isn't useful to ship.
MAX_LABELS = 5000


@dataclass
class ParamSpec:
    name: str
    max: int
    default: int
    labels: list[str] | None  # text shown by the pedal for each value 0..max, if known


@dataclass
class _Symbol:
    name: str
    value: int
    size: int
    kind: int


class _Elf:
    """Just enough of an ELF32 little-endian reader for sections and symbols."""

    def __init__(self, data: bytes) -> None:
        if data[:4] != b"\x7fELF" or data[4] != 1 or data[5] != 1:
            raise ValueError("not a 32-bit little-endian ELF file")
        self.data = data
        (shoff,) = struct.unpack_from("<I", data, 0x20)
        shentsize, shnum, shstrndx = struct.unpack_from("<HHH", data, 0x2E)
        headers = [struct.unpack_from("<10I", data, shoff + i * shentsize) for i in range(shnum)]
        names_offset = headers[shstrndx][4]
        self.sections = []
        for h in headers:
            name = self._string(names_offset, h[0])
            # (name, type, addr, offset, size, link)
            self.sections.append((name, h[1], h[3], h[4], h[5], h[6]))

    def _string(self, table_offset: int, index: int) -> str:
        start = table_offset + index
        end = self.data.index(b"\x00", start)
        return self.data[start:end].decode("ascii", errors="replace")

    def section(self, name: str):
        """First section with this name that has contents in the file."""
        for section in self.sections:
            if section[0] == name and section[4] and section[1] != 8:  # 8 = NOBITS
                return section
        return None

    def symbols(self) -> list[_Symbol]:
        symbols = []
        for _name, kind, _addr, offset, size, link in self.sections:
            if kind != _SHT_SYMTAB:
                continue
            strtab_offset = self.sections[link][3]
            for pos in range(offset, offset + size, 16):
                name_index, value, sym_size, info, _other, _shndx = struct.unpack_from(
                    "<IIIBBH", self.data, pos)
                if info & 0x0F in (_STT_OBJECT, _STT_FUNC):
                    symbols.append(_Symbol(self._string(strtab_offset, name_index),
                                           value, sym_size, info & 0x0F))
        return symbols


def _cstring(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("latin-1").strip()


def _table_key(name: str) -> str:
    return name.lstrip("_").lower()


def _format_number(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.1f}"


def _note_label(raw: str) -> str | None:
    """Translate a note-glyph label: "\x19" -> "1/4", "\x19 3" -> "1/8T" (triplets
    fitting in that note), "\x18." -> "1/8." (dotted), "\x19x4" -> "1/4×4"."""
    glyph, rest = raw[:1], raw[1:].strip()
    note = _NOTE_GLYPHS.get(glyph)
    if note is None:
        return raw if raw.isprintable() else None
    if rest == "3":
        return f"{_SHORTER_NOTE.get(note, note)}T"
    if rest == ".":
        return f"{note}."
    if rest.startswith("x") and rest[1:].isdigit():
        return f"{note}×{rest[1:]}"
    return note if not rest else None


def _sync_labels_for(rule: _SyncRule, maximum: int, notes: list[str]) -> list[str] | None:
    if rule.maximum not in (maximum, _ANY_MAXIMUM):
        return None
    labels = []
    for value in range(maximum + 1):
        if value >= rule.sync_from:
            index = value - rule.sync_from + rule.first_note
            if index >= len(notes):
                return None
            labels.append(notes[index])
            continue
        number = value
        if rule.fine_until is not None and value > rule.fine_until:
            number = rule.fine_until + 1 + (value - rule.fine_until - 1) * 10
        labels.append(str(number + rule.offset))
    return labels


def _labels_from_rule(rule: str, maximum: int, notes: list[str] | None) -> list[str] | None:
    if rule in _SYNC_RULES:
        return _sync_labels_for(_SYNC_RULES[rule], maximum, notes) if notes else None
    if rule == "1_300_Sync" and maximum == 300:  # SlapBack: 1..300 ms, then tempo sync
        return [str(v + 1) for v in range(300)] + ["Sync"]
    if rule == "DelayType" and maximum == 2 and notes and len(notes) > 7:  # SlapBack SubDv
        return [notes[7], notes[6], "P-P"]
    if match := _OFFSET_RULE.match(rule):
        offset = int(match[2]) * (-1 if match[1] else 1)
        step = int(match[3]) / 10 if match[3] else 1  # "_05" = steps of 0.5
        signed = bool(match[1])  # values that go below zero read better as +/-
        labels = []
        for value in range(maximum + 1):
            number = value * step + offset
            text = _format_number(number)
            labels.append(f"+{text}" if signed and number > 0 else text)
        return labels
    if match := _OFF_THEN_NUMBER.search(rule):
        return ["OFF"] + [str(v) for v in range(maximum)]
    return None


def _split_labels(raw: bytes, count: int, deref) -> list[str] | None:
    if not raw or count <= 0 or len(raw) % count:
        return None
    width = len(raw) // count
    cells = [raw[i * width:(i + 1) * width] for i in range(count)]
    if width == 4:  # a table of pointers to strings elsewhere in .const
        pointed = [deref(struct.unpack("<I", cell)[0]) for cell in cells]
        if all(p is not None for p in pointed):
            cells = pointed
    labels = [_note_label(_cstring(cell)) for cell in cells]
    return labels if all(labels) else None


def read_param_specs(program: bytes) -> list[ParamSpec]:
    """Decode the parameter descriptors of an effect's DSP program (a ZD2 DATA chunk)."""
    elf = _Elf(program)
    const = elf.section(".const")
    if const is None:
        return []
    _name, _kind, const_addr, const_offset, const_size, _link = const
    const_data = program[const_offset:const_offset + const_size]

    symbols = elf.symbols()
    functions = {s.value: s.name for s in symbols if s.kind == _STT_FUNC}
    tables = {_table_key(s.name)[len("disp_prm_"):]: s for s in symbols
              if s.kind == _STT_OBJECT and _table_key(s.name).startswith("disp_prm_")}

    start = const_data.find(_TABLE_MARKER)
    if start == -1:
        return []
    table = next((s for s in symbols
                  if s.kind == _STT_OBJECT and s.value == const_addr + start and s.size), None)
    count = table.size // _DESCRIPTOR_SIZE if table else 0

    def const_bytes(symbol: _Symbol) -> bytes:
        begin = symbol.value - const_addr
        return const_data[begin:begin + symbol.size] if 0 <= begin < len(const_data) else b""

    def deref(address: int) -> bytes | None:
        begin = address - const_addr
        return const_data[begin:begin + 16] if 0 <= begin < len(const_data) else None

    sync_tables = [t for key, t in tables.items() if key.endswith("bpm_sync")]
    sync_labels = None
    if len(sync_tables) == 1:
        raw = const_bytes(sync_tables[0])
        for width in (5, 8, 6, 4):  # entry width varies between effects
            if len(raw) % width == 0:
                sync_labels = _split_labels(raw, len(raw) // width, deref)
                if sync_labels:
                    break
    plain_tables = {key: t for key, t in tables.items() if not key.endswith("bpm_sync")}

    def table_labels(suffix: str, maximum: int) -> list[str] | None:
        if (table := plain_tables.get(suffix.lower())) is not None:
            raw = const_bytes(table)
            if labels := _split_labels(raw, maximum + 1, deref):
                return labels
            # Some tables hold more labels than the parameter uses; the range starts at 0.
            for width in range(4, 17):
                if len(raw) % width == 0 and len(raw) // width > maximum + 1:
                    labels = _split_labels(raw, len(raw) // width, deref)
                    if labels:
                        return labels[:maximum + 1]
            return None
        # Names don't always match; accept a single table that fits the range exactly.
        fitting = [labels for t in plain_tables.values()
                   if (labels := _split_labels(const_bytes(t), maximum + 1, deref))]
        return fitting[0] if len(fitting) == 1 else None

    specs = []
    for index in range(_HEADER_ENTRIES, count):
        entry = const_data[start + index * _DESCRIPTOR_SIZE:start + (index + 1) * _DESCRIPTOR_SIZE]
        name = _cstring(entry[:12])
        maximum, default = struct.unpack_from("<II", entry, 12)
        (display_addr,) = struct.unpack_from("<I", entry, 36)

        labels = None
        rule = functions.get(display_addr, "") if display_addr else ""
        if rule and maximum < MAX_LABELS:
            suffix = re.sub(r"^_*GetString_?", "", rule)
            labels = _labels_from_rule(suffix, maximum, sync_labels)
            if labels is None and not suffix.endswith("Sync") and suffix != "DelayType":
                labels = table_labels(suffix, maximum)
        specs.append(ParamSpec(name=name, max=maximum, default=default, labels=labels))
    return specs
