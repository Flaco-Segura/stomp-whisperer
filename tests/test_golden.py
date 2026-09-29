import copy
import json

import pytest

from stomp_whisperer.golden import _apply, export
from stomp_whisperer.patch import Effect, Patch, encode_patch, parse_patch


def _dump(name: str, ids: list[int]) -> bytes:
    patch = Patch(name=name, version=2, target=0x040000, effect_ids=ids,
                  effects=[Effect(id=i, enabled=True, params=[n] * 12, origin=n)
                           for n, i in enumerate(ids)],
                  chunks={"TXJ1": b"", "TXE1": b"desc\x00\x00\x00\x00", "EDTB": b"",
                          "PRM2": (1 << 161).to_bytes(32, "little")})
    return encode_patch(patch) + b"stale padding"


def test_export_writes_replayable_cases(tmp_path):
    dumps = tmp_path / "dumps"
    dumps.mkdir()
    (dumps / "patch_001.bin").write_bytes(_dump("Two", [0x07000010, 0x0A000020]))
    (dumps / "patch_002.bin").write_bytes(_dump("One", [0x03000080]))

    assert export(dumps, tmp_path / "golden") == 2

    manifest = json.loads((tmp_path / "golden" / "manifest.json").read_text())
    assert [p["roundtrip"] for p in manifest["patches"]] == [True, True]
    for slot in ("001", "002"):
        case = json.loads((tmp_path / "golden" / "patches" / f"{slot}.json").read_text())
        raw = (tmp_path / "golden" / "patches" / f"{slot}.bin").read_bytes()
        assert bytes.fromhex(case["encoded"]) == raw[:case["declared_length"]]
        names = {edit["name"] for edit in case["edits"]}
        assert {"toggle_first", "rename", "add_effect"} <= names
        for edit in case["edits"]:  # what a port does: replay the ops, then encode
            patch = copy.deepcopy(parse_patch(raw))
            for op in edit["ops"]:
                _apply(patch, op)
            assert encode_patch(patch, edit["preamp"]).hex() == edit["encoded"]


def test_export_needs_dumps(tmp_path):
    with pytest.raises(FileNotFoundError):
        export(tmp_path, tmp_path / "golden")
