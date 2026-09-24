import json
import struct

import pytest

from stomp_whisperer.effects import (EffectFormatError, EffectInfo, EffectLibrary, Param,
                                     parse_effect_file, parse_effect_index)


def _group(group: int, entries: list[tuple[str, int]]) -> bytes:
    body = b">>>\x00" + bytes([group]) + b"\x00" * 21
    for name, effect_id in entries:
        body += struct.pack("<12sx4sxBI3x", name.encode(), b"1.10", 1, effect_id)
    return body + b"<<<\x00" + bytes([group]) + b"\x00" * 21


def _index(*groups: bytes) -> bytes:
    header = b">>>\x00" + b"\x00" * 22 + b"BLANK.ZD2".ljust(12, b"\x00") + b"\x00" * 6 + b"\x01" \
        + b"\x00" * 7 + b"<<<\x00" + b"\x00" * 22
    return header + b"".join(groups)


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return tag + struct.pack("<I", len(payload)) + payload


def _zd2(effect_id: int, name: bytes, group: bytes, params: list[dict] | bytes | None) -> bytes:
    head = bytearray(b"ZDLF" + b"\x00" * 92)
    head += struct.pack("<I", effect_id) + name.ljust(11, b"\x00") + group.ljust(11, b"\x00")
    head += b"\x00" * 6
    body = _chunk(b"ICON", b"\x01\x02") + _chunk(b"TXE1", b"Warm drive.\x00")
    body += _chunk(b"DATA", b"\xff" * 40)
    if isinstance(params, list):
        params = json.dumps({"Parameters": params}).encode()
    if params is not None:
        body += _chunk(b"PRME", params + b"\x00")
    return bytes(head) + body


def test_parse_effect_index():
    data = _index(_group(1, [("COMP.ZD2", 0x01000010)]),
                  _group(3, [("DYNDRIVE.ZD2", 0x03000080), ("TS_DRIVE.ZD2", 0x03000030)]))
    assert parse_effect_index(data) == {
        0x01000010: "COMP.ZD2", 0x03000080: "DYNDRIVE.ZD2", 0x03000030: "TS_DRIVE.ZD2",
    }


def test_parse_effect_file():
    params = [{"name": "Gain", "explanation": "Adjusts the gain."}, {"name": "VOL"}]
    info = parse_effect_file(_zd2(0x03000080, b"DYN Drive", b"DRIVE", params), "DYNDRIVE.ZD2")
    assert (info.id, info.name, info.group) == (0x03000080, "DYN Drive", "DRIVE")
    assert info.description == "Warm drive."
    assert info.params == [Param("Gain", "Adjusts the gain."), Param("VOL", "")]


def test_tolerates_trailing_comma_in_parameter_list():
    prme = b'{"Parameters":[{"name":"Gain"},\r\n  ]\r\n}'
    data = _zd2(0x04000111, b"KRAMPUS", b"PREAMP", prme)
    assert parse_effect_file(data).params == [Param("Gain", "")]


def test_unreadable_parameter_list_keeps_the_name():
    data = _zd2(0x04000111, b"KRAMPUS", b"PREAMP", b'{"Parameters":[{name:Gain}]}')
    info = parse_effect_file(data)
    assert (info.name, info.params) == ("KRAMPUS", [])


def test_eleven_char_name_without_terminator():
    info = parse_effect_file(_zd2(0x09000010, b"HD Hall Rev", b"REVERB", None))
    assert info.name == "HD Hall Rev"
    assert info.params == []


def test_rejects_non_zd2():
    with pytest.raises(EffectFormatError):
        parse_effect_file(b"\x00" * 200)


def test_library_round_trip(tmp_path):
    path = tmp_path / "cache" / "effects.json"
    library = EffectLibrary(path)
    library.add(EffectInfo(0x03000080, "DYNDRIVE.ZD2", "DYN Drive", "DRIVE", "", [Param("Gain")]))
    library.save()
    reloaded = EffectLibrary(path)
    assert 0x03000080 in reloaded
    assert reloaded.get(0x03000080).params == [Param("Gain", "")]
