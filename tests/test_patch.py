import struct

import pytest

from stomp_whisperer.patch import PatchFormatError, format_name, parse_patch


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


def _edtb_record(effect_id: int, enabled: bool, params: list[int]) -> bytes:
    bits, shift = int(enabled), 1
    bits |= effect_id << shift
    shift += 29
    for value, width in zip(params, (12, 12, 12, 12, 12, 8, 8, 8, 12, 12, 12, 12)):
        bits |= value << shift
        shift += width
    return bits.to_bytes(24, "little")


def test_decodes_effects_from_edtb():
    header = struct.pack("<4sIIII6s10s", b"PTCF", 0, 1, 2, 0, b"\x00" * 6, b"Chain".ljust(10))
    edtb = (_edtb_record(0x03000080, False, [100, 0, 50])
            + _edtb_record(0x08000060, True, [1, 2, 3, 4, 5, 255, 7, 8, 4095]))
    data = header + struct.pack("<2I", 0x03000080, 0x08000060) + _chunk(b"EDTB", edtb)

    first, second = parse_patch(data).effects
    assert (first.id, first.enabled, first.params[:3]) == (0x03000080, False, [100, 0, 50])
    assert (second.id, second.enabled) == (0x08000060, True)
    assert second.params == [1, 2, 3, 4, 5, 255, 7, 8, 4095, 0, 0, 0]


def test_ignores_bytes_past_declared_length():
    patch = _make_patch(b"Tidy", [1])
    stale = _chunk(b"NAME", b"Leftover Name\x00\x00\x00")
    data = patch[:4] + struct.pack("<I", len(patch)) + patch[8:] + stale
    assert parse_patch(data).name == "Tidy"


def test_display_name_collapses_padding():
    data = _make_patch(b"x", [1], version=2, extra=_chunk(b"NAME", b"Polyphonic    Octaver   \x00"))
    assert parse_patch(data).display_name == "Polyphonic Octaver"


@pytest.mark.parametrize("text, stored", [
    ("  Big   Lead ", "Big Lead"),
    ("OverDrive +Delay", "OverDrive     +Delay"),  # as Zoom stores it
    ("Smoking On The Window", "Smoking On    The Window"),
    ("Abcdefghij Klmnopqrstuvwxyz", "Abcdefghij Klmnopqrstuvwxyz"),  # no space fits: split at 14
    ("x" * 28, "x" * 28),
])
def test_format_name(text, stored):
    assert format_name(text) == stored


@pytest.mark.parametrize("text", ["", "   ", "Señal", "x" * 29])
def test_format_name_rejects(text):
    with pytest.raises(ValueError):
        format_name(text)
