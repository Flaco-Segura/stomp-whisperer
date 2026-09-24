"""Effect metadata read from the pedal's own effect files.

The pedal stores each built-in effect as a ``*.ZD2`` file, indexed by
``FLST_SEQ.ZT2``. Layouts based on mungewell/zoom-zt2's ``zoomzt2.py``:
https://github.com/mungewell/zoom-zt2

FLST_SEQ.ZT2 is a list of groups. Each group starts with b">>>\\0" + group byte +
21 bytes of padding, holds 26-byte effect entries, and ends with b"<<<\\0" + group
byte + 21 bytes of padding. An effect entry is:

    size  field
    12    file name (ASCII, NUL padded)
    1     NUL
    4     version (ASCII, e.g. "1.10")
    1     NUL
    1     installed flag
    4     effect id (uint32 LE, same value patches use)
    3     zeros

A ZD2 file starts with b"ZDLF" and a fixed header holding the effect id (uint32 LE
at offset 96), its display name (11 bytes at 100) and its group name (11 bytes at
111). Tagged chunks follow (4-byte tag, uint32 LE length, payload), starting at
"ICON". Of those, TXE1 is the English description and PRME is JSON listing the
parameters (name + English explanation) in the order patches store their values.

Only this metadata is kept; the effect binaries themselves are never stored.
"""

from __future__ import annotations

import json
import os
import re
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path

INDEX_FILE = "FLST_SEQ.ZT2"

_GROUP_START = b">>>\x00"
_GROUP_END = b"<<<\x00"
_GROUP_HEADER_SIZE = 26
_ENTRY = struct.Struct("<12sx4sxBI3x")

_ZD2_MAGIC = b"ZDLF"
_ZD2_ID_OFFSET = 96
_ZD2_NAME_OFFSET = 100
_ZD2_GROUP_OFFSET = 111
_ZD2_NAME_SIZE = 11

# Some of Zoom's PRME lists end with a trailing comma ("},\r\n  ]"), which isn't valid JSON.
_TRAILING_COMMA = re.compile(r",(\s*[\]}])")


class EffectFormatError(ValueError):
    """Raised when an effect file or the effect index cannot be decoded."""


@dataclass
class Param:
    name: str
    explanation: str = ""


@dataclass
class EffectInfo:
    id: int
    file: str
    name: str
    group: str = ""
    description: str = ""
    params: list[Param] = field(default_factory=list)


def _cstring(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()


def parse_effect_index(data: bytes) -> dict[int, str]:
    """Map effect id -> ZD2 file name, from FLST_SEQ.ZT2."""
    files: dict[int, str] = {}
    offset = data.find(_GROUP_START, 1)  # skip the file header, which uses the same marker
    while offset != -1 and data[offset:offset + 4] == _GROUP_START:
        offset += _GROUP_HEADER_SIZE
        while offset + _ENTRY.size <= len(data) and data[offset:offset + 4] != _GROUP_END:
            raw_name, _version, _installed, effect_id = _ENTRY.unpack_from(data, offset)
            files[effect_id] = _cstring(raw_name)
            offset += _ENTRY.size
        offset = data.find(_GROUP_START, offset)
    if not files:
        raise EffectFormatError(f"no effects found in {INDEX_FILE}")
    return files


def _chunks(data: bytes, offset: int) -> dict[str, bytes]:
    chunks: dict[str, bytes] = {}
    while offset + 8 <= len(data):
        tag = data[offset:offset + 4]
        if not tag.isalnum():
            break
        (length,) = struct.unpack_from("<I", data, offset + 4)
        chunks[tag.decode("ascii")] = data[offset + 8:offset + 8 + length]
        offset += 8 + length
    return chunks


def parse_effect_file(data: bytes, file: str = "") -> EffectInfo:
    """Decode the name, group, description and parameter names of a ZD2 file."""
    if data[:4] != _ZD2_MAGIC or len(data) < _ZD2_GROUP_OFFSET + _ZD2_NAME_SIZE:
        raise EffectFormatError(f"{file or 'effect file'} is not a ZD2 file")

    (effect_id,) = struct.unpack_from("<I", data, _ZD2_ID_OFFSET)
    name = _cstring(data[_ZD2_NAME_OFFSET:_ZD2_NAME_OFFSET + _ZD2_NAME_SIZE])
    group = _cstring(data[_ZD2_GROUP_OFFSET:_ZD2_GROUP_OFFSET + _ZD2_NAME_SIZE])

    icon = data.find(b"ICON", _ZD2_GROUP_OFFSET)
    chunks = _chunks(data, icon) if icon != -1 else {}

    params: list[Param] = []
    if "PRME" in chunks:
        try:
            spec = json.loads(_TRAILING_COMMA.sub(r"\1", _cstring(chunks["PRME"])) or "{}")
        except json.JSONDecodeError:
            spec = {}  # keep the name and description even if the parameter list is unreadable
        params = [Param(name=p.get("name", ""), explanation=p.get("explanation", ""))
                  for p in spec.get("Parameters", [])]

    return EffectInfo(id=effect_id, file=file, name=name, group=group,
                      description=_cstring(chunks.get("TXE1", b"")), params=params)


def default_cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "stomp-whisperer" / "effects.json"


class EffectLibrary:
    """Effect metadata keyed by id, persisted as JSON so each file is read only once."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_cache_path()
        self._effects: dict[int, EffectInfo] = {}
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return
        for item in raw.get("effects", []):
            item["params"] = [Param(**p) for p in item.get("params", [])]
            info = EffectInfo(**item)
            self._effects[info.id] = info

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = {"effects": [asdict(e) for e in sorted(self._effects.values(), key=lambda e: e.id)]}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(body, indent=1, ensure_ascii=False))
        tmp.replace(self.path)

    def get(self, effect_id: int) -> EffectInfo | None:
        return self._effects.get(effect_id)

    def add(self, info: EffectInfo) -> None:
        self._effects[info.id] = info

    def __contains__(self, effect_id: int) -> bool:
        return effect_id in self._effects
