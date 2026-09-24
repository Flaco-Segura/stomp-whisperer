import struct

import pytest

from stomp_whisperer.patch import PatchFormatError, parse_patch


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return tag + struct.pack("<I", len(payload)) + payload


def _make_patch(name: bytes, ids: list[int], version: int = 1, extra: bytes = b"") -> bytes:
    header = struct.pack("<4sIIII6s10s", b"PTCF", 0, version, len(ids), 0x040000,
                         b"\x00" * 6, name.ljust(10, b" "))
    body = struct.pack(f"<{len(ids)}I", *ids)
    body += _chunk(b"TXJ1", b"") + _chunk(b"TXE1", b"desc") + _chunk(b"EDTB", b"\x00" * 24 * len(ids))
    return header + body + extra


def test_parse_header_name_and_ids():
    patch = parse_patch(_make_patch(b"CleanBoost", [0x10, 0x20]))
    assert patch.name == "CleanBoost"
    assert patch.effect_ids == [0x10, 0x20]
    assert patch.target == 0x040000
    assert patch.chunks["TXE1"] == b"desc"


def test_name_chunk_overrides_header_name():
    data = _make_patch(b"Short", [1], version=2, extra=_chunk(b"NAME", b"LongerName32\x00\x00\x00\x00"))
    assert parse_patch(data).name == "LongerName32"


def test_rejects_non_ptcf():
    with pytest.raises(PatchFormatError):
        parse_patch(b"\x00" * 64)
