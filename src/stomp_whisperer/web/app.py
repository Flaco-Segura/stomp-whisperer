"""FastAPI app: JSON API for the pedal plus the static front-end."""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from ..patch import Effect, Patch, PatchFormatError, parse_patch
from ..pedal import Pedal, PedalNotFoundError, find_pedal_port

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="StompWhisperer")


class PatchCache:
    """Patches read from the pedal, kept until a refresh or a disconnect.

    Reading all 100 slots takes about a second, so the list is read once and the
    detail view is served from memory. The lock serialises MIDI access: FastAPI
    runs sync endpoints on a thread pool.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._slots: list[dict] | None = None

    def clear(self) -> None:
        with self._lock:
            self._slots = None

    def slots(self, refresh: bool = False) -> list[dict]:
        with self._lock:
            if self._slots is None or refresh:
                self._slots = read_all_slots()
            return self._slots


def read_all_slots() -> list[dict]:
    slots = []
    with Pedal() as pedal:
        pedal.pc_mode_on()
        try:
            info = pedal.patch_check()
            for slot in range(1, info.count + 1):
                data, checksum_ok = pedal.download_patch(slot, info.bank_size)
                slots.append(_slot_entry(slot, bytes(data), checksum_ok))
        finally:
            pedal.pc_mode_off()
    return slots


def _slot_entry(slot: int, data: bytes, checksum_ok: bool) -> dict:
    entry = {"slot": slot, "checksum_ok": checksum_ok, "patch": None, "error": None}
    if not data:
        entry["error"] = "Slot is empty"
        return entry
    try:
        entry["patch"] = parse_patch(data)
    except PatchFormatError as exc:
        entry["error"] = str(exc)
    return entry


def _effect_json(position: int, effect: Effect) -> dict:
    return {
        "position": position,
        "id": f"{effect.id:08x}",
        "empty": effect.id == 0,
        "enabled": effect.enabled,
        "params": effect.params,
    }


def _description(patch: Patch) -> str:
    raw = patch.chunks.get("TXE1", b"")
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()


def _summary_json(entry: dict) -> dict:
    patch: Patch | None = entry["patch"]
    effects = [e for e in patch.effects if e.id != 0] if patch else []
    return {
        "slot": entry["slot"],
        "name": patch.display_name if patch else None,
        "effect_count": len(effects),
        "effects": [{"id": f"{e.id:08x}", "enabled": e.enabled} for e in effects],
        "checksum_ok": entry["checksum_ok"],
        "error": entry["error"],
    }


def _detail_json(entry: dict) -> dict:
    body = _summary_json(entry)
    patch: Patch | None = entry["patch"]
    body["description"] = _description(patch) if patch else ""
    body["chain"] = [_effect_json(i, e) for i, e in enumerate(patch.effects, 1)] if patch else []
    return body


cache = PatchCache()


def _cached_slots(refresh: bool = False) -> list[dict]:
    try:
        return cache.slots(refresh)
    except PedalNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc


@app.get("/api/status")
def status() -> dict:
    port = find_pedal_port()
    if port is None:
        cache.clear()  # a different pedal (or edited patches) may come back
    return {"connected": port is not None, "port": port}


@app.get("/api/patches")
def list_patches(refresh: bool = False) -> list[dict]:
    return [_summary_json(entry) for entry in _cached_slots(refresh)]


@app.get("/api/patches/{slot}")
def get_patch(slot: int) -> dict:
    slots = _cached_slots()
    if not 1 <= slot <= len(slots):
        raise HTTPException(status_code=404, detail=f"No patch slot {slot}")
    return _detail_json(slots[slot - 1])


# Mounted last so /api/* routes take precedence.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
