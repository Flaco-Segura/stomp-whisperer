"""Low-level SysEx protocol helpers for the Zoom "MS Plus" pedal series
(MS-50G+, MS-60B+, MS-70CDR+, MS-80IR+).

Reverse-engineered by the mungewell/zoom-zt2 project:
https://github.com/mungewell/zoom-zt2

All functions here work with SysEx *bodies* (no leading 0xF0 / trailing 0xF7).
"""

PORT_NAME_HINT = "ZOOM MS Plus Series"

# Device ID shared by the whole "Plus" series (not model-specific).
DEVICE_ID = 0x6E


def pc_mode_on() -> list[int]:
    return [0x52, 0x00, DEVICE_ID, 0x52]


def pc_mode_off() -> list[int]:
    return [0x52, 0x00, DEVICE_ID, 0x53]


def editor_mode_on() -> list[int]:
    return [0x52, 0x00, DEVICE_ID, 0x50]


def editor_mode_off() -> list[int]:
    return [0x52, 0x00, DEVICE_ID, 0x51]


def patch_check() -> list[int]:
    return [0x52, 0x00, DEVICE_ID, 0x44]


def patch_download_current() -> list[int]:
    return [0x52, 0x00, DEVICE_ID, 0x64, 0x13]


def patch_download(location: int, bank_size: int) -> list[int]:
    bank = (location - 1) // bank_size
    loc = location - (bank * bank_size) - 1
    return [
        0x52, 0x00, DEVICE_ID, 0x46, 0x00, 0x00,
        bank & 0x7F, bank >> 7,
        loc & 0x7F, loc >> 7,
    ]


def unpack_7to8(packet: bytearray) -> bytearray:
    """Unpack 7-bit-per-byte MIDI SysEx payload back into 8-bit data.

    Every 8th byte carries the high bit of the following 7 bytes.
    """
    data = bytearray()
    loop = -1
    hibits = 0
    for byte in packet:
        if loop != -1:
            if hibits & (2 ** loop):
                data.append(128 + byte)
            else:
                data.append(byte)
            loop -= 1
        else:
            hibits = byte
            loop = 6
    return data


def pack_8to7(data: bytes) -> bytearray:
    """Pack 8-bit data into 7-bit MIDI-safe bytes (inverse of unpack_7to8)."""
    packet = bytearray()
    encode = bytearray(b"\x00")

    for byte in data:
        encode[0] = encode[0] + ((byte & 0x80) >> len(encode))
        encode.append(byte & 0x7F)
        if len(encode) > 7:
            packet += encode
            encode = bytearray(b"\x00")

    if len(encode) > 1:
        packet += encode

    return packet


def decode_checksum(reply: bytearray) -> int:
    """Decode the 5-byte, 7-bit-packed CRC32 trailer used on patch replies."""
    return (
        reply[-5]
        + (reply[-4] << 7)
        + (reply[-3] << 14)
        + (reply[-2] << 21)
        + ((reply[-1] & 0x0F) << 28)
    )


# ---------- file access (the pedal's internal storage) ----------
#
# Only the read side is implemented: listing and downloading. Effect binaries
# live here as *.ZD2 files, indexed by FLST_SEQ.ZT2.

FILE_CMD = 0x60


def _file_name(name: str) -> list[int]:
    return [*name.encode("ascii"), 0x00]


def file_find_first(pattern: str = "*") -> list[int]:
    return [0x52, 0x00, DEVICE_ID, FILE_CMD, 0x25, 0x00, 0x00, *_file_name(pattern)]


def file_find_next(pattern: str = "*") -> list[int]:
    return [0x52, 0x00, DEVICE_ID, FILE_CMD, 0x26, 0x00, 0x00, *_file_name(pattern)]


def file_find_end() -> list[int]:
    return [0x52, 0x00, DEVICE_ID, FILE_CMD, 0x27]


def file_open_read(name: str) -> list[int]:
    return [0x52, 0x00, DEVICE_ID, FILE_CMD, 0x20, 0x02, *([0x00] * 9), *_file_name(name)]


def file_sync() -> list[int]:
    return [0x52, 0x00, DEVICE_ID, FILE_CMD, 0x05, 0x00]


def file_read_block() -> list[int]:
    return [0x52, 0x00, DEVICE_ID, FILE_CMD, 0x22, 0x14, 0x2F, 0x60, 0x00, 0x0C, 0x00, 0x04,
            0x00, 0x00, 0x00]


def file_close() -> list[list[int]]:
    """Closing takes two messages, sent in order."""
    return [
        [0x52, 0x00, DEVICE_ID, FILE_CMD, 0x21, 0x40, 0x00, 0x00, 0x00, 0x00],
        [0x52, 0x00, DEVICE_ID, FILE_CMD, 0x09],
    ]
