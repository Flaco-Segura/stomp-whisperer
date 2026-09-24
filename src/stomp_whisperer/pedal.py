"""MIDI transport and high-level operations for talking to the pedal."""

from __future__ import annotations

import binascii
import time
from dataclasses import dataclass

import rtmidi

from . import protocol


class PedalNotFoundError(RuntimeError):
    """Raised when no matching MIDI ports are visible to the system."""


@dataclass
class PatchInfo:
    count: int
    patch_size: int
    bank_size: int


def _find_port(get_ports, hint: str) -> tuple[int | None, str | None]:
    for index, name in enumerate(get_ports()):
        if hint in name:
            return index, name
    return None, None


def find_pedal_port() -> str | None:
    """Return the pedal's MIDI input port name if it is plugged in, without opening it."""
    midi_in = rtmidi.MidiIn()
    try:
        _index, name = _find_port(midi_in.get_ports, protocol.PORT_NAME_HINT)
        return name
    finally:
        midi_in.delete()


class Pedal:
    """Read-only-first client for the Zoom MS Plus series SysEx protocol."""

    def __init__(self) -> None:
        self._midi_in = rtmidi.MidiIn()
        self._midi_out = rtmidi.MidiOut()
        self._midi_in.ignore_types(sysex=False)

    def connect(self) -> None:
        in_index, in_name = _find_port(self._midi_in.get_ports, protocol.PORT_NAME_HINT)
        out_index, out_name = _find_port(self._midi_out.get_ports, protocol.PORT_NAME_HINT)
        if in_index is None or out_index is None:
            self._release()
            raise PedalNotFoundError(
                f"No MIDI port matching '{protocol.PORT_NAME_HINT}' found. "
                "Is the pedal connected and powered on?"
            )
        self._midi_in.open_port(in_index)
        self._midi_out.open_port(out_index)
        self.in_name = in_name
        self.out_name = out_name

    def close(self) -> None:
        self._midi_in.close_port()
        self._midi_out.close_port()
        self._release()

    def _release(self) -> None:
        # Each MidiIn/MidiOut is an ALSA sequencer client, and ALSA allows only 64
        # user clients system-wide; a long-running server must free them explicitly.
        self._midi_in.delete()
        self._midi_out.delete()

    def __enter__(self) -> "Pedal":
        self.connect()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def _send_recv(self, body: list[int], timeout: float = 2.0) -> bytearray | None:
        self._midi_out.send_message([0xF0, *body, 0xF7])

        deadline = time.time() + timeout
        while time.time() < deadline:
            message = self._midi_in.get_message()
            if message is not None:
                data, _delta_time = message
                return bytearray(data[1:-1])  # strip F0 / F7
            time.sleep(0.01)
        return None

    def pc_mode_on(self) -> bytearray | None:
        return self._send_recv(protocol.pc_mode_on())

    def pc_mode_off(self) -> bytearray | None:
        return self._send_recv(protocol.pc_mode_off())

    def patch_check(self) -> PatchInfo:
        reply = self._send_recv(protocol.patch_check())
        if reply is None:
            raise TimeoutError("Pedal did not respond to patch_check")
        count = reply[5] * 128 + reply[4]
        patch_size = reply[7] * 128 + reply[6]
        bank_size = reply[11] * 128 + reply[10]
        return PatchInfo(count=count, patch_size=patch_size, bank_size=bank_size)

    def _decode_patch_reply(self, reply: bytearray, header_len: int) -> tuple[bytearray, bool]:
        length = reply[header_len - 1] * 128 + reply[header_len - 2]
        if length == 0:
            return bytearray(), True
        raw = reply[header_len:header_len + length + int(length / 7) + 1]
        data = protocol.unpack_7to8(raw)
        checksum = protocol.decode_checksum(reply)
        ok = (checksum ^ 0xFFFFFFFF) == binascii.crc32(data)
        return data, ok

    def download_current_patch(self) -> tuple[bytearray, bool]:
        reply = self._send_recv(protocol.patch_download_current())
        if reply is None:
            raise TimeoutError("Pedal did not respond to patch_download_current")
        return self._decode_patch_reply(reply, header_len=8)

    def download_patch(self, location: int, bank_size: int) -> tuple[bytearray, bool]:
        reply = self._send_recv(protocol.patch_download(location, bank_size))
        if reply is None:
            raise TimeoutError(f"Pedal did not respond to patch_download({location})")
        return self._decode_patch_reply(reply, header_len=12)

    # ---------- files ----------

    def _file_reply(self, body: list[int], what: str) -> bytearray:
        reply = self._send_recv(body)
        if reply is None:
            raise TimeoutError(f"Pedal did not respond to {what}")
        return reply

    def list_files(self, pattern: str = "*") -> list[str]:
        """Names of the files in the pedal's storage (effects, icons, IRs, indexes)."""
        names = []
        reply = self._file_reply(protocol.file_find_first(pattern), "file_find_first")
        while reply[4] == 0x04:  # 0x04 = a file matched
            names.append(bytes(reply[14:26]).split(b"\x00", 1)[0].decode("ascii", errors="replace"))
            reply = self._file_reply(protocol.file_find_next(pattern), "file_find_next")
        self._send_recv(protocol.file_find_end())
        return names

    def download_file(self, name: str) -> bytes:
        """Read a whole file from the pedal's storage, verifying each block's CRC32."""
        self._file_reply(protocol.file_open_read(name), f"file_open_read({name})")
        data = bytearray()
        try:
            while True:
                self._file_reply(protocol.file_sync(), "file_sync")
                self._file_reply(protocol.file_read_block(), "file_read_block")
                reply = self._file_reply(protocol.file_sync(), "file_sync")
                length = reply[9] * 128 + reply[8]
                if reply[4] != 0x04 or length == 0:
                    break
                block = protocol.unpack_7to8(reply[10:10 + length + length // 7 + 1])
                if (protocol.decode_checksum(reply) ^ 0xFFFFFFFF) != binascii.crc32(block):
                    raise IOError(f"Checksum mismatch while reading {name}")
                data += block
        finally:
            for body in protocol.file_close():
                self._send_recv(body)
        return bytes(data)
