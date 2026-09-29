"""Golden files: what this implementation produces for the saved patch dumps.

A port of the patch and protocol code (e.g. to TypeScript for the browser) can check
itself against these files: same input bytes, same parse, same encoded bytes, same
SysEx messages. Every byte string is lowercase hex without spaces.

Layout of the output directory:

    manifest.json        format version, source commit and the list of patch cases
    protocol.json        fixed SysEx messages, name layouts and parameter limits
    patches/NNN.bin      the dump, copied as saved (stale padding included)
    patches/NNN.json     its parse, re-encoding, SysEx messages and edit cases

An edit case lists operations to apply, in order, to the parsed patch, followed by
`encode_patch(patch, preamp)`:

    {"op": "toggle", "index": i}              flip effect i on/off
    {"op": "set_params", "index": i, "params": [...]}
    {"op": "rename", "name": "..."}           already laid out by format_name
    {"op": "reverse"}                         reverse the effect order
    {"op": "remove", "index": i}
    {"op": "add", "effect": {...}}            append a new effect (origin None)
"""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

from . import protocol
from .patch import (PARAM_COUNT, PRM2_PREAMP_SLOTS, Effect, Patch, encode_patch, format_name,
                    param_limit, parse_patch)

FORMAT_VERSION = 1
MAX_EFFECTS = 6  # what a MS-50G+ patch holds; encode_patch itself allows 11

_NAME_CASES = ["  Big   Lead ", "OverDrive +Delay", "Smoking On The Window",
               "Abcdefghij Klmnopqrstuvwxyz", "x" * 28, "", "   ", "Señal", "x" * 29]


def _effect_json(effect: Effect) -> dict:
    return {"id": effect.id, "enabled": effect.enabled, "params": effect.params,
            "extra": effect.extra, "origin": effect.origin}


def _patch_json(patch: Patch) -> dict:
    return {
        "name": patch.name,
        "display_name": patch.display_name,
        "version": patch.version,
        "target": patch.target,
        "reserved": patch.reserved.hex(),
        "effect_ids": patch.effect_ids,
        "effects": [_effect_json(e) for e in patch.effects],
        "chunks": {tag: payload.hex() for tag, payload in patch.chunks.items()},
    }


def _preamp_bit(patch: Patch, index: int) -> bool:
    prm2 = int.from_bytes(patch.chunks.get("PRM2", b""), "little")
    return bool(prm2 >> (PRM2_PREAMP_SLOTS + index) & 1)


def _apply(patch: Patch, op: dict) -> None:
    kind = op["op"]
    if kind == "toggle":
        effect = patch.effects[op["index"]]
        effect.enabled = not effect.enabled
    elif kind == "set_params":
        patch.effects[op["index"]].params = list(op["params"])
    elif kind == "rename":
        patch.name = op["name"]
    elif kind == "reverse":
        patch.effects.reverse()
    elif kind == "remove":
        del patch.effects[op["index"]]
    elif kind == "add":
        e = op["effect"]
        patch.effects.append(Effect(id=e["id"], enabled=e["enabled"], params=list(e["params"]),
                                    extra=e["extra"]))
    else:
        raise ValueError(f"unknown edit operation {kind!r}")


def _edit_cases(patch: Patch, slot: int, donor: tuple[Effect, bool] | None) -> list[dict]:
    """(name, ops, preamp) for the edits that make sense on this patch."""
    count = len(patch.effects)
    cases: list[tuple[str, list[dict], list[bool | None] | None]] = []
    if count:
        cases.append(("toggle_first", [{"op": "toggle", "index": 0}], None))
        cases.append(("first_params_max", [{"op": "set_params", "index": 0,
                                            "params": [param_limit(i) for i in range(PARAM_COUNT)]}],
                      None))
    cases.append(("rename", [{"op": "rename", "name": format_name(f"Golden Test {slot:03d}")}], None))
    if count >= 2:
        cases.append(("reverse", [{"op": "reverse"}], None))
        cases.append(("remove_first", [{"op": "remove", "index": 0}], None))
    if donor is not None and count < MAX_EFFECTS:
        effect, is_preamp = donor
        added = {"id": effect.id, "enabled": True, "params": effect.params, "extra": 0}
        cases.append(("add_effect", [{"op": "add", "effect": added}],
                      [None] * count + [is_preamp]))
        if count >= 1:
            cases.append(("add_then_reverse", [{"op": "add", "effect": added}, {"op": "reverse"}],
                          [is_preamp] + [None] * count))

    results = []
    for name, ops, preamp in cases:
        edited = copy.deepcopy(patch)
        for op in ops:
            _apply(edited, op)
        encoded = encode_patch(edited, preamp)
        results.append({"name": name, "ops": ops, "preamp": preamp, "encoded": encoded.hex(),
                        "reparsed": _patch_json(parse_patch(encoded))})
    return results


def _donor_for(patch: Patch, patches: list[Patch]) -> tuple[Effect, bool] | None:
    """An effect from another patch that this one doesn't use, with its preamp flag."""
    used = {e.id for e in patch.effects}
    for other in patches:
        for index, effect in enumerate(other.effects):
            if effect.id and effect.id not in used:
                return effect, _preamp_bit(other, index)
    return None


def _protocol_json(bank_size: int) -> dict:
    def hexed(message: list[int]) -> str:
        return bytes(message).hex()

    names = []
    for text in _NAME_CASES:
        try:
            names.append({"input": text, "stored": format_name(text)})
        except ValueError:
            names.append({"input": text, "error": True})

    return {
        "device_id": protocol.DEVICE_ID,
        "bank_size": bank_size,
        "messages": {
            "pc_mode_on": hexed(protocol.pc_mode_on()),
            "pc_mode_off": hexed(protocol.pc_mode_off()),
            "editor_mode_on": hexed(protocol.editor_mode_on()),
            "editor_mode_off": hexed(protocol.editor_mode_off()),
            "patch_check": hexed(protocol.patch_check()),
            "patch_download_current": hexed(protocol.patch_download_current()),
            "file_find_first": hexed(protocol.file_find_first()),
            "file_find_next": hexed(protocol.file_find_next()),
            "file_find_end": hexed(protocol.file_find_end()),
            "file_open_read_FLST_SEQ.ZT2": hexed(protocol.file_open_read("FLST_SEQ.ZT2")),
            "file_sync": hexed(protocol.file_sync()),
            "file_read_block": hexed(protocol.file_read_block()),
            "file_close": [hexed(m) for m in protocol.file_close()],
        },
        "format_name": names,
        "param_limits": [param_limit(i) for i in range(PARAM_COUNT)],
    }


def _source_commit() -> str | None:
    root = Path(__file__).resolve().parent
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                                text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "."], cwd=root,
                               capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return f"{commit}-dirty" if dirty else commit


def _write_json(path: Path, body: dict) -> None:
    path.write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n")


def export(dumps: Path, out: Path, bank_size: int = 10) -> int:
    """Write the golden files for every patch_NNN.bin in `dumps`; returns how many."""
    files = sorted(dumps.glob("patch_*.bin"))
    if not files:
        raise FileNotFoundError(f"no patch_NNN.bin dumps in {dumps}")
    raws = [f.read_bytes() for f in files]
    patches = [parse_patch(raw) for raw in raws]

    (out / "patches").mkdir(parents=True, exist_ok=True)
    cases = []
    for file, raw, patch in zip(files, raws, patches):
        slot = int(file.stem.removeprefix("patch_"))
        encoded = encode_patch(patch)
        length = int.from_bytes(raw[4:8], "little")
        packed = protocol.pack_8to7(raw)
        body = {
            "slot": slot,
            "input": f"{slot:03d}.bin",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "declared_length": length,
            "parsed": _patch_json(patch),
            "encoded": encoded.hex(),
            "roundtrip": encoded == raw[:length],
            "sysex": {
                "patch_download": bytes(protocol.patch_download(slot, bank_size)).hex(),
                "packed_8to7": bytes(packed).hex(),
                "unpacks_back": bytes(protocol.unpack_7to8(packed)) == raw,
                "checksum": bytes(protocol.encode_checksum(raw)).hex(),
                "patch_upload_encoded": bytes(protocol.patch_upload(slot, bank_size, encoded)).hex(),
            },
            "edits": _edit_cases(patch, slot, _donor_for(patch, patches)),
        }
        (out / "patches" / body["input"]).write_bytes(raw)
        _write_json(out / "patches" / f"{slot:03d}.json", body)
        cases.append({"slot": slot, "name": patch.display_name, "roundtrip": body["roundtrip"],
                      "edits": len(body["edits"])})

    _write_json(out / "protocol.json", _protocol_json(bank_size))
    _write_json(out / "manifest.json", {"format_version": FORMAT_VERSION,
                                        "source_commit": _source_commit(),
                                        "patches": cases})
    return len(files)
