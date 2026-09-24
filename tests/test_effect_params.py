import struct

from stomp_whisperer.effect_params import read_param_specs

CONST_ADDR = 0x80000000


def _descriptor(name: bytes, maximum: int, default: int, display: int = 0) -> bytes:
    entry = bytearray(0x38)
    entry[:len(name)] = name
    struct.pack_into("<II", entry, 12, maximum, default)
    struct.pack_into("<I", entry, 36, display)
    return bytes(entry)


def _elf(const: bytes, symbols: list[tuple[str, int, int, int]]) -> bytes:
    """A minimal ELF32 LE with .const, .symtab, .strtab and .shstrtab.
    symbols: (name, value, size, type) with type 1 = OBJECT, 2 = FUNC."""
    strtab = b"\x00"
    symtab = b"\x00" * 16
    for name, value, size, kind in symbols:
        symtab += struct.pack("<IIIBBH", len(strtab), value, size, kind, 0, 1)
        strtab += name.encode() + b"\x00"
    shstrtab = b"\x00.const\x00.symtab\x00.strtab\x00.shstrtab\x00"

    body = bytearray(0x34)
    offsets = []
    for blob in (const, symtab, strtab, shstrtab):
        offsets.append(len(body))
        body += blob
    shoff = len(body)
    sections = [
        (0, 0, 0, 0, 0, 0),
        (1, 1, CONST_ADDR, offsets[0], len(const), 0),        # .const (PROGBITS)
        (8, 2, 0, offsets[1], len(symtab), 3),                # .symtab -> .strtab
        (16, 3, 0, offsets[2], len(strtab), 0),               # .strtab
        (24, 3, 0, offsets[3], len(shstrtab), 0),             # .shstrtab
    ]
    for name, kind, addr, offset, size, link in sections:
        body += struct.pack("<10I", name, kind, 0, addr, offset, size, link, 0, 0, 0)
    body[:6] = b"\x7fELF\x01\x01"
    struct.pack_into("<I", body, 0x20, shoff)
    struct.pack_into("<HHH", body, 0x2E, 40, len(sections), 4)
    return bytes(body)


def _program(params: list[bytes], tables: dict[str, bytes], functions: dict[str, int]) -> bytes:
    table = _descriptor(b"OnOff", 1, 0) + _descriptor(b"Effect", 0xFFFFFFFF, 0) + b"".join(params)
    const = bytearray(table)
    symbols = [("EffectTable", CONST_ADDR, len(table), 1)]
    for name, raw in tables.items():
        symbols.append((name, CONST_ADDR + len(const), len(raw), 1))
        const += raw
    symbols += [(name, address, 0, 2) for name, address in functions.items()]
    return _elf(bytes(const), symbols)


def test_range_default_and_plain_numbers():
    specs = read_param_specs(_program([_descriptor(b"Gain", 100, 78)], {}, {}))
    assert [(s.name, s.max, s.default, s.labels) for s in specs] == [("Gain", 100, 78, None)]


def test_labels_from_table_matched_by_name():
    program = _program([_descriptor(b"Mode", 1, 1, display=0x40)],
                       {"disp_prm_LpHp": b"COMBO\x00STACK\x00"}, {"_GetString_LpHp": 0x40})
    assert read_param_specs(program)[0].labels == ["COMBO", "STACK"]


def test_labels_from_the_only_table_that_fits():
    program = _program([_descriptor(b"ATTCK", 1, 0, display=0x40)],
                       {"disp_prm_attack": b"SLOW\x00\x00\x00\x00FAST\x00\x00\x00\x00"},
                       {"GetString_CmpAtk": 0x40})
    assert read_param_specs(program)[0].labels == ["SLOW", "FAST"]


def test_offset_rules():
    program = _program([
        _descriptor(b"100Hz", 48, 24, display=0x40),
        _descriptor(b"Bass", 20, 10, display=0x80),
        _descriptor(b"IN1", 101, 61, display=0xC0),
    ], {}, {"GetString_offset_minus12_05": 0x40, "_GetString_offset_minus10": 0x80,
            "_GetString_Input_off_to_100": 0xC0})
    geq, bass, level = read_param_specs(program)
    assert (geq.labels[0], geq.labels[17], geq.labels[24], geq.labels[48]) == ("-12", "-3.5", "0", "+12")
    assert (bass.labels[0], bass.labels[10], bass.labels[20]) == ("-10", "0", "+10")
    assert (level.labels[0], level.labels[1], level.labels[101]) == ("OFF", "0", "100")


def test_sync_rule_numbers_then_note_values():
    sync = b"\x16\x00\x00\x00\x00" + b"\x19 3\x00\x00" + b"\x18.\x00\x00\x00" + b"\x19x2\x00\x00"
    program = _program([_descriptor(b"Rate", 53, 11, display=0x40)],
                       {"disp_prm_BPM_sync": sync}, {"GetString_ofst_1_50_Sync": 0x40})
    labels = read_param_specs(program)[0].labels
    assert labels[:2] == ["1", "2"] and labels[49] == "50"
    assert labels[50:] == ["1/32", "1/8T", "1/8.", "1/4×2"]


def test_sync_rule_that_does_not_fit_stays_raw():
    sync = b"\x16\x00\x00\x00\x00" * 4
    program = _program([_descriptor(b"Time", 962, 946, display=0x40)],
                       {"disp_prm_BPM_sync": sync}, {"GetString_1_5000_Sync": 0x40})
    assert read_param_specs(program)[0].labels is None


def test_program_without_descriptor_table():
    assert read_param_specs(_elf(b"\x00" * 16, [])) == []
