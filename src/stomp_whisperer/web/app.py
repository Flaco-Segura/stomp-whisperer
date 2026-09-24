"""FastAPI app: JSON API for the pedal plus the static front-end."""

from __future__ import annotations

import threading
from collections import deque
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from ..effects import INDEX_FILE, EffectFormatError, EffectLibrary, parse_effect_file, parse_effect_index
from ..patch import Effect, Patch, PatchFormatError, parse_patch
from ..pedal import Pedal, PedalNotFoundError, find_pedal_port

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="StompWhisperer")

# One MIDI conversation at a time: FastAPI runs sync endpoints on a thread pool
# and effect files are fetched from a background thread.
pedal_lock = threading.Lock()


@app.middleware("http")
async def revalidate_every_time(request, call_next):
    # Without this, browsers may keep running a stale app.js after an update.
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-cache")
    return response


class PatchCache:
    """Patches read from the pedal, kept until a refresh or a disconnect.

    Reading all 100 slots takes about a second, so the list is read once and the
    detail view is served from memory.
    """

    def __init__(self) -> None:
        self._slots: list[dict] | None = None

    def clear(self) -> None:
        with pedal_lock:
            self._slots = None

    def slots(self, refresh: bool = False) -> list[dict]:
        with pedal_lock:
            if self._slots is None or refresh:
                self._slots = read_all_slots()
            slots = self._slots
        effect_sync.request(e.id for entry in slots if entry["patch"] for e in entry["patch"].effects)
        return slots


class EffectSync:
    """Fetches effect names and parameter names from the pedal's ZD2 files.

    Each file takes ~3 s to read over MIDI, so this runs in a background thread,
    one file per pedal-lock hold so patch reads are never blocked for long.
    Results are persisted by `EffectLibrary`; only effects not seen before are read.
    """

    def __init__(self, library: EffectLibrary | None = None) -> None:
        self._library = library
        self._queue: deque[int] = deque()
        self._failed: set[int] = set()
        self._files: dict[int, str] | None = None
        self._state = threading.Condition()
        self._thread: threading.Thread | None = None

    @property
    def library(self) -> EffectLibrary:
        if self._library is None:
            self._library = EffectLibrary()
        return self._library

    def is_pending(self, effect_id: int) -> bool:
        with self._state:
            return effect_id in self._queue

    def request(self, effect_ids, urgent: bool = False) -> None:
        """Queue effects whose metadata is missing; urgent ones jump the queue."""
        with self._state:
            for effect_id in dict.fromkeys(effect_ids):
                if effect_id == 0 or effect_id in self.library or effect_id in self._failed:
                    continue
                if effect_id in self._queue:
                    if not urgent:
                        continue
                    self._queue.remove(effect_id)
                if urgent:
                    self._queue.appendleft(effect_id)
                else:
                    self._queue.append(effect_id)
            if self._queue and (self._thread is None or not self._thread.is_alive()):
                self._thread = threading.Thread(target=self._run, name="effect-sync", daemon=True)
                self._thread.start()

    def forget_pedal(self) -> None:
        """Drop per-connection state; a different pedal may have other effects installed."""
        with self._state:
            self._queue.clear()
            self._failed.clear()
            self._files = None

    def _run(self) -> None:
        while True:
            with self._state:
                if not self._queue:
                    return
                effect_id = self._queue[0]
            try:
                info = self._fetch(effect_id)
            except (PedalNotFoundError, TimeoutError, OSError):
                self.forget_pedal()  # pedal went away; the next patch read re-queues
                return
            except EffectFormatError:
                info = None
            with self._state:
                if info is not None:
                    self.library.add(info)
                    self.library.save()
                else:
                    self._failed.add(effect_id)
                if self._queue and self._queue[0] == effect_id:
                    self._queue.popleft()
                elif effect_id in self._queue:
                    self._queue.remove(effect_id)

    def _fetch(self, effect_id: int):
        with pedal_lock, Pedal() as pedal:
            pedal.pc_mode_on()
            try:
                if self._files is None:
                    self._files = parse_effect_index(pedal.download_file(INDEX_FILE))
                file = self._files.get(effect_id)
                if file is None:
                    return None  # not installed on this pedal
                return parse_effect_file(pedal.download_file(file), file)
            finally:
                pedal.pc_mode_off()


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
    body = {
        "position": position,
        "id": f"{effect.id:08x}",
        "empty": effect.id == 0,
        "enabled": effect.enabled,
        "name": None,
        "group": None,
        "description": "",
        "info_pending": False,
        # Unnamed until the effect's file is read; then only the params it defines.
        "params": [{"name": None, "explanation": "", "value": v, "display": str(v),
                    "max": None, "default": None, "range": None, "options": None,
                    "center": None}
                   for v in effect.params],
    }
    if effect.id == 0:
        return body
    info = effect_sync.library.get(effect.id)
    if info is None:
        body["info_pending"] = effect_sync.is_pending(effect.id)
        return body
    body.update(name=info.name, group=info.group, description=info.description)
    body["params"] = [_param_json(p, v) for p, v in zip(info.params, effect.params)]
    return body


# Parameters with this many positions or fewer are switches (Mode, Ratio…), not knobs.
MAX_SWITCH_POSITIONS = 6


def _param_json(param, value: int) -> dict:
    if param.max is None:
        value_range = None
    elif param.labels:
        value_range = f"{param.labels[0]} to {param.labels[-1]}"
    else:
        value_range = f"0 to {param.max}"
    return {
        "name": param.name,
        "explanation": param.explanation,
        "value": value,
        "display": param.display(value),
        "max": param.max,
        "default": param.default,
        "range": value_range,
        "options": ([param.display(v) for v in range(param.max + 1)]
                    if param.max is not None and param.max < MAX_SWITCH_POSITIONS else None),
        "center": _center(param),
    }


def _center(param) -> int | None:
    """Value shown as 0 on a -N…+N parameter, where its arc should start."""
    labels = param.labels
    if labels and labels[0].startswith("-") and "0" in labels:
        return labels.index("0")
    return None


def _description(patch: Patch) -> str:
    raw = patch.chunks.get("TXE1", b"")
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()


def _summary_json(entry: dict) -> dict:
    patch: Patch | None = entry["patch"]
    effects = [e for e in patch.effects if e.id != 0] if patch else []
    library = effect_sync.library
    return {
        "slot": entry["slot"],
        "name": patch.display_name if patch else None,
        "effect_count": len(effects),
        "effects": [{"id": f"{e.id:08x}", "enabled": e.enabled,
                     "name": info.name if (info := library.get(e.id)) else None}
                    for e in effects],
        "checksum_ok": entry["checksum_ok"],
        "error": entry["error"],
    }


def _detail_json(entry: dict) -> dict:
    body = _summary_json(entry)
    patch: Patch | None = entry["patch"]
    body["description"] = _description(patch) if patch else ""
    body["chain"] = [_effect_json(i, e) for i, e in enumerate(patch.effects, 1)] if patch else []
    return body


effect_sync = EffectSync()
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
        effect_sync.forget_pedal()
    return {"connected": port is not None, "port": port}


@app.get("/api/patches")
def list_patches(refresh: bool = False) -> list[dict]:
    return [_summary_json(entry) for entry in _cached_slots(refresh)]


@app.get("/api/patches/{slot}")
def get_patch(slot: int) -> dict:
    slots = _cached_slots()
    if not 1 <= slot <= len(slots):
        raise HTTPException(status_code=404, detail=f"No patch slot {slot}")
    entry = slots[slot - 1]
    if entry["patch"]:
        effect_sync.request((e.id for e in entry["patch"].effects), urgent=True)
    return _detail_json(entry)


# Mounted last so /api/* routes take precedence.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
