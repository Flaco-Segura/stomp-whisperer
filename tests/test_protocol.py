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


def test_patch_upload_mirrors_download_reply():
    data = bytes(range(200))
    body = protocol.patch_upload(location=23, bank_size=10, data=data)
    assert body[:12] == [0x52, 0x00, protocol.DEVICE_ID, 0x45, 0x00, 0x00, 2, 0, 2, 0,
                         200 & 0x7F, 200 >> 7]
    assert all(byte < 0x80 for byte in body)
    # Decoded the way the pedal's reply to patch_download is.
    assert bytes(protocol.unpack_7to8(body[12:12 + 200 + 200 // 7 + 1])) == data
    import binascii
    assert protocol.decode_checksum(bytearray(body)) ^ 0xFFFFFFFF == binascii.crc32(data)
