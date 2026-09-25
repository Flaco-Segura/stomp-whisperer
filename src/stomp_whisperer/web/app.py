"""FastAPI app: JSON API for the pedal plus the static front-end."""

from __future__ import annotations

import threading
from collections import deque
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..effects import INDEX_FILE, EffectFormatError, EffectLibrary, parse_effect_file, parse_effect_index
from ..patch import (PARAM_COUNT, Effect, Patch, PatchFormatError, format_name, param_limit,
                     parse_patch)
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
                self._slots = source.read_slots()
            slots = self._slots
        effect_sync.request(e.id for entry in slots if entry["patch"] for e in entry["patch"].effects)
        return slots


class PedalSource:
    """Patches read live from the pedal over USB MIDI."""

    sandbox = False

    def port(self) -> str | None:
        return find_pedal_port()

    def read_slots(self) -> list[dict]:
        return read_all_slots()


class DumpSource:
    """Sandbox: patches read from `patch_NNN.bin` dumps as if the pedal were connected.

    Effect metadata comes only from the cached `EffectLibrary`; nothing is read over MIDI.
    """

    sandbox = True

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def port(self) -> str:
        return f"Sandbox: {self.directory}"

    def read_slots(self) -> list[dict]:
        dumps = {}
        for path in self.directory.glob("patch_*.bin"):
            try:
                dumps[int(path.stem.removeprefix("patch_"))] = path
            except ValueError:
                continue
        if not dumps:
            raise PedalNotFoundError(f"No patch_NNN.bin dumps in {self.directory}")
        # `list --save` skips empty slots, so gaps up to the last dump are empty slots.
        return [_slot_entry(slot, dumps[slot].read_bytes() if slot in dumps else b"", True)
                for slot in range(1, max(dumps) + 1)]


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
        if source.sandbox:
            return  # no pedal to read effect files from
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
        "labels": param.labels,
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
    body["sandbox"] = source.sandbox
    body["factory"] = entry["slot"] <= FACTORY_SLOTS
    body["max_effects"] = MAX_EFFECTS
    body["chain"] = [_effect_json(i, e) for i, e in enumerate(patch.effects, 1)] if patch else []
    return body


effect_sync = EffectSync()
cache = PatchCache()
source: PedalSource | DumpSource = PedalSource()


def use_sandbox(directory: Path) -> None:
    """Serve patches from dump files instead of the pedal."""
    global source
    source = DumpSource(directory)
    cache.clear()


def _cached_slots(refresh: bool = False) -> list[dict]:
    try:
        return cache.slots(refresh)
    except PedalNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc


@app.get("/api/status")
def status() -> dict:
    port = source.port()
    if port is None:
        cache.clear()  # a different pedal (or edited patches) may come back
        effect_sync.forget_pedal()
    return {"connected": port is not None, "port": port, "sandbox": source.sandbox}


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


@app.get("/api/effects")
def list_effects() -> list[dict]:
    """Every effect in the library, in the pedal's order (the id's high byte is its category)."""
    return [{"id": f"{info.id:08x}", "name": info.name, "group": info.group}
            for info in sorted(effect_sync.library, key=lambda info: info.id)]


# ---------- sandbox editing (in memory only; never sent to the pedal) ----------

# Slots 1–85 hold Zoom's factory patches: their effects can be tweaked, toggled and
# reordered, but they can't be renamed or have effects added, removed or replaced.
FACTORY_SLOTS = 85
MAX_EFFECTS = 6


class EffectChange(BaseModel):
    enabled: bool | None = None
    id: str | None = None  # hex, as the API reports it; resets the parameters to defaults
    params: dict[int, int] | None = None  # parameter index -> raw value


class Rename(BaseModel):
    name: str


class Move(BaseModel):
    to: int  # new 1-based position


def _edit(slot: int, position: int | None, edit, structural: bool = False) -> dict:
    """Apply `edit(patch, index)` to a cached patch and return its new detail."""
    if not source.sandbox:
        raise HTTPException(status_code=403, detail="Patches can only be edited in sandbox mode")
    slots = _cached_slots()
    if not 1 <= slot <= len(slots):
        raise HTTPException(status_code=404, detail=f"No patch slot {slot}")
    entry = slots[slot - 1]
    patch: Patch | None = entry["patch"]
    if patch is None:
        raise HTTPException(status_code=409, detail=f"Slot {slot} has no readable patch")
    if structural and slot <= FACTORY_SLOTS:
        raise HTTPException(status_code=403, detail="Factory patches can't be renamed or have "
                                                    "effects added, removed or replaced")
    with pedal_lock:
        if position is not None and not 1 <= position <= len(patch.effects):
            raise HTTPException(status_code=404, detail=f"No effect at position {position}")
        edit(patch, None if position is None else position - 1)
        patch.effect_ids = [e.id for e in patch.effects]
    return _detail_json(entry)


def _add_effect(patch: Patch, _index) -> None:
    if len(patch.effects) >= MAX_EFFECTS:
        raise HTTPException(status_code=409, detail=f"A patch holds at most {MAX_EFFECTS} effects")
    patch.effects.append(Effect(id=0, enabled=False, params=[0] * PARAM_COUNT))


def _change_effect(change: EffectChange):
    def edit(patch: Patch, index: int) -> None:
        effect = patch.effects[index]
        info = effect_sync.library.get(effect.id)
        if change.id is not None:
            try:
                info = effect_sync.library.get(int(change.id, 16))
            except ValueError:
                info = None
            if info is None:
                raise HTTPException(status_code=422, detail=f"Unknown effect {change.id}")
        elif change.enabled is not None and effect.id == 0:
            raise HTTPException(status_code=409, detail="Choose an effect for this slot first")
        # Validate everything before changing anything.
        params = change.params or {}
        for param_index, value in params.items():
            if not 0 <= param_index < PARAM_COUNT:
                raise HTTPException(status_code=422, detail=f"No parameter {param_index + 1}")
            known = info.params[param_index] if info and param_index < len(info.params) else None
            limit = known.max if known and known.max is not None else param_limit(param_index)
            if not 0 <= value <= limit:
                raise HTTPException(status_code=422,
                                    detail=f"Parameter {param_index + 1} must be 0 to {limit}")

        if change.id is not None:
            defaults = [p.default or 0 for p in info.params]
            effect.id, effect.enabled = info.id, True
            effect.params = (defaults + [0] * PARAM_COUNT)[:PARAM_COUNT]
        if change.enabled is not None:
            effect.enabled = change.enabled
        for param_index, value in params.items():
            effect.params[param_index] = value
    return edit


def _remove_effect(patch: Patch, index: int) -> None:
    if len(patch.effects) == 1:
        raise HTTPException(status_code=409, detail="A patch needs at least one effect slot")
    patch.effects.pop(index)


@app.patch("/api/patches/{slot}")
def rename_patch(slot: int, rename: Rename) -> dict:
    try:
        name = format_name(rename.name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    def edit(patch: Patch, _index) -> None:
        patch.name = name
    return _edit(slot, None, edit, structural=True)


@app.post("/api/patches/{slot}/effects")
def add_effect(slot: int) -> dict:
    return _edit(slot, None, _add_effect, structural=True)


@app.patch("/api/patches/{slot}/effects/{position}")
def change_effect(slot: int, position: int, change: EffectChange) -> dict:
    return _edit(slot, position, _change_effect(change), structural=change.id is not None)


@app.delete("/api/patches/{slot}/effects/{position}")
def remove_effect(slot: int, position: int) -> dict:
    return _edit(slot, position, _remove_effect, structural=True)


@app.post("/api/patches/{slot}/effects/{position}/move")
def move_effect(slot: int, position: int, move: Move) -> dict:
    def edit(patch: Patch, index: int) -> None:
        if not 1 <= move.to <= len(patch.effects):
            raise HTTPException(status_code=422, detail=f"No position {move.to}")
        patch.effects.insert(move.to - 1, patch.effects.pop(index))
    return _edit(slot, position, edit)


# Mounted last so /api/* routes take precedence.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
