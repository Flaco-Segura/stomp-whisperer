from stomp_whisperer import protocol


def test_pack_unpack_roundtrip():
    original = bytes(range(0, 256, 3))  # mix of values with/without high bit
    packed = protocol.pack_8to7(original)
    unpacked = protocol.unpack_7to8(packed)
    assert bytes(unpacked[: len(original)]) == original


def test_pack_7bit_safe():
    original = bytes([0xFF, 0x80, 0x7F, 0x00])
    packed = protocol.pack_8to7(original)
    assert all(byte < 0x80 for byte in packed)
