import pytest
from fastapi.testclient import TestClient

from stomp_whisperer.effects import EffectInfo, EffectLibrary, Param
from stomp_whisperer.pedal import PatchInfo
from stomp_whisperer.web import app as web_app

client = TestClient(web_app.app)

DYN_DRIVE = EffectInfo(id=0x03000080, file="DYNDRIVE.ZD2", name="DYN Drive", group="DRIVE",
                       params=[Param("Gain", "Adjusts the gain.", max=100, default=78),
                               Param("Mode", max=1, default=1, labels=["COMBO", "STACK"])])


class FakeSync(web_app.EffectSync):
    """Never talks to the pedal; knows only the effects in its library."""

    def __init__(self, library):
        super().__init__(library)
        self.requested = []

    def request(self, effect_ids, urgent=False):
        self.requested.append((list(effect_ids), urgent))


@pytest.fixture(autouse=True)
def fake_effects(monkeypatch, tmp_path):
    library = EffectLibrary(tmp_path / "effects.json")
    library.add(DYN_DRIVE)
    sync = FakeSync(library)
    monkeypatch.setattr(web_app, "effect_sync", sync)
    return sync


def test_status_disconnected(monkeypatch):
    monkeypatch.setattr(web_app, "find_pedal_port", lambda: None)
    assert client.get("/api/status").json() == {"connected": False, "port": None, "sandbox": False}


def test_status_connected(monkeypatch):
    monkeypatch.setattr(web_app, "find_pedal_port", lambda: "ZOOM MS Plus Series:0")
    body = client.get("/api/status").json()
    assert body["connected"] is True
    assert body["port"] == "ZOOM MS Plus Series:0"


def test_serves_index():
    response = client.get("/")
    assert response.status_code == 200
    assert "StompWhisperer" in response.text


def _fake_slots():
    import struct

    def chunk(tag, payload):
        return tag + struct.pack("<I", len(payload)) + payload

    def record(effect_id, enabled, first_param):
        return (int(enabled) | effect_id << 1 | first_param << 30).to_bytes(24, "little")

    header = struct.pack("<4sIIII6s10s", b"PTCF", 0, 1, 2, 0, b"\x00" * 6, b"Drive Echo")
    data = (header + struct.pack("<2I", 0x03000080, 0x08000060)
            + chunk(b"TXE1", b"Drive into delay.\x00\x00")
            + chunk(b"EDTB", record(0x03000080, False, 64) + record(0x08000060, True, 279)))
    return [web_app._slot_entry(1, data, True), web_app._slot_entry(2, b"", True)]


def test_list_patches(monkeypatch):
    monkeypatch.setattr(web_app, "read_all_slots", _fake_slots)
    web_app.cache.clear()
    first, second = client.get("/api/patches").json()
    assert first["name"] == "Drive Echo"
    assert first["effect_count"] == 2
    assert first["effects"] == [{"id": "03000080", "enabled": False, "name": "DYN Drive"},
                                {"id": "08000060", "enabled": True, "name": None}]
    assert second["name"] is None and second["error"] == "Slot is empty"


def test_patch_detail(monkeypatch, fake_effects):
    monkeypatch.setattr(web_app, "read_all_slots", _fake_slots)
    web_app.cache.clear()
    body = client.get("/api/patches/1").json()
    assert body["description"] == "Drive into delay."
    known, unknown = body["chain"]
    assert (known["name"], known["group"]) == ("DYN Drive", "DRIVE")
    assert known["params"] == [
        {"name": "Gain", "explanation": "Adjusts the gain.", "value": 64, "display": "64",
         "max": 100, "default": 78, "range": "0 to 100", "labels": None, "options": None,
         "center": None},
        {"name": "Mode", "explanation": "", "value": 0, "display": "COMBO",
         "max": 1, "default": 1, "range": "COMBO to STACK", "labels": ["COMBO", "STACK"],
         "options": ["COMBO", "STACK"],
         "center": None},
    ]
    assert unknown["name"] is None
    assert len(unknown["params"]) == 12 and unknown["params"][0]["value"] == 279
    assert fake_effects.requested[-1] == ([0x03000080, 0x08000060], True)
    assert client.get("/api/patches/3").status_code == 404


def test_patches_without_pedal(monkeypatch):
    def no_pedal():
        raise web_app.PedalNotFoundError("not found")

    monkeypatch.setattr(web_app, "read_all_slots", no_pedal)
    web_app.cache.clear()
    assert client.get("/api/patches").status_code == 503


def _dump_patch(name: bytes) -> bytes:
    import struct

    header = struct.pack("<4sIIII6s10s", b"PTCF", 0, 1, 1, 0, b"\x00" * 6, name.ljust(10))
    record = (1 | 0x03000080 << 1 | 64 << 30).to_bytes(24, "little")
    return (header + struct.pack("<I", 0x03000080)
            + struct.pack("<4sI", b"EDTB", len(record)) + record)


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    (tmp_path / "patch_001.bin").write_bytes(_dump_patch(b"Clean"))
    (tmp_path / "patch_003.bin").write_bytes(_dump_patch(b"Lead"))
    monkeypatch.setattr(web_app, "source", web_app.DumpSource(tmp_path))
    monkeypatch.setattr(web_app, "find_pedal_port", lambda: pytest.fail("pedal was used"))
    web_app.cache.clear()
    return tmp_path


def test_sandbox_status(sandbox):
    assert client.get("/api/status").json() == {
        "connected": True, "port": f"Sandbox: {sandbox}", "sandbox": True}


def test_sandbox_patches_come_from_dumps(sandbox):
    first, second, third = client.get("/api/patches").json()
    assert (first["name"], third["name"]) == ("Clean", "Lead")
    assert second["error"] == "Slot is empty"
    detail = client.get("/api/patches/1").json()
    assert detail["chain"][0]["name"] == "DYN Drive"
    assert detail["chain"][0]["info_pending"] is False


def test_sandbox_never_fetches_effects(sandbox, tmp_path):
    sync = web_app.EffectSync(EffectLibrary(tmp_path / "effects.json"))
    sync.request([0x08000060])
    assert not sync.is_pending(0x08000060)


@pytest.fixture
def user_patch(sandbox):
    """Slot 86, the first one past the factory patches."""
    (sandbox / "patch_086.bin").write_bytes(_dump_patch(b"Mine"))
    web_app.cache.clear()
    return 86


def _chain(body):
    return [(fx["id"], fx["enabled"]) for fx in body["chain"]]


def test_list_effects():
    assert client.get("/api/effects").json() == [
        {"id": "03000080", "name": "DYN Drive", "group": "DRIVE"}]


def test_live_edits_stay_in_memory_until_saved(live_user_slot):
    body = client.patch(f"/api/patches/{live_user_slot}/effects/1", json={"enabled": True}).json()
    assert body["chain"][0]["enabled"] is True
    assert body["unsaved"] is True and body["savable"] is True
    assert client.get("/api/patches").json()[live_user_slot - 1]["unsaved"] is True


class FakePedal:
    """Stands in for the MIDI pedal: slots are byte strings, writes are recorded."""

    def __init__(self, slots: dict[int, bytes]):
        self.slots = slots
        self.uploads = []

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        pass

    def pc_mode_on(self):
        pass

    def pc_mode_off(self):
        pass

    def patch_check(self):
        return PatchInfo(count=100, patch_size=848, bank_size=10)

    def download_patch(self, location, bank_size):
        return bytearray(self.slots[location]), True

    def upload_patch(self, location, info, data):
        self.uploads.append((location, data))
        self.slots[location] = data
        return data


@pytest.fixture
def live_user_slot(monkeypatch):
    """Slot 86 (the first user patch) read live from a fake pedal."""
    def slots():
        drive_echo = _fake_slots()[0]
        return [web_app._slot_entry(n, b"", True) for n in range(1, 86)] + [
            {**drive_echo, "slot": 86}]
    monkeypatch.setattr(web_app, "read_all_slots", slots)
    web_app.cache.clear()
    return 86


def test_save_writes_the_edited_patch(monkeypatch, live_user_slot, fake_effects):
    fake_effects.library.add(EffectInfo(id=0x08000060, file="DELAY.ZD2", name="Delay",
                                        group="DELAY", params=[]))
    pedal = FakePedal({})
    monkeypatch.setattr(web_app, "Pedal", pedal)
    client.patch(f"/api/patches/{live_user_slot}/effects/1", json={"enabled": True})
    body = client.post(f"/api/patches/{live_user_slot}/save").json()
    assert body["unsaved"] is False
    [(location, data)] = pedal.uploads
    assert location == live_user_slot
    assert [(e.id, e.enabled) for e in web_app.parse_patch(data).effects] == [
        (0x03000080, True), (0x08000060, True)]
    # Discarding reads the slot back from the pedal.
    client.patch(f"/api/patches/{live_user_slot}/effects/1", json={"enabled": False})
    body = client.post(f"/api/patches/{live_user_slot}/revert").json()
    assert body["unsaved"] is False and body["chain"][0]["enabled"] is True


def test_save_as_another_slot(monkeypatch, fake_effects):
    def slots():
        drive_echo = _fake_slots()[0]
        return [{**drive_echo, "slot": n} if n in (21, 86) else web_app._slot_entry(n, b"", True)
                for n in range(1, 90)]
    monkeypatch.setattr(web_app, "read_all_slots", slots)
    web_app.cache.clear()
    fake_effects.library.add(EffectInfo(id=0x08000060, file="DELAY.ZD2", name="Delay",
                                        group="DELAY", params=[]))
    original = web_app.encode_patch(web_app.cache.slots()[20]["patch"])
    pedal = FakePedal({21: original})
    monkeypatch.setattr(web_app, "Pedal", pedal)

    # A factory patch, tweaked, goes to a user slot; the factory one reads as before.
    client.patch("/api/patches/21/effects/1", json={"enabled": True})
    body = client.post("/api/patches/21/save", json={"to": 88}).json()
    assert body["slot"] == 88 and body["unsaved"] is False
    assert body["chain"][0]["enabled"] is True
    assert [location for location, _data in pedal.uploads] == [88]
    source = client.get("/api/patches/21").json()
    assert source["unsaved"] is False and source["chain"][0]["enabled"] is False

    assert client.post("/api/patches/21/save", json={"to": 85}).status_code == 403
    assert client.post("/api/patches/21/save", json={"to": 90}).status_code == 404
    assert len(pedal.uploads) == 1


def test_save_waits_for_unknown_effects(monkeypatch, live_user_slot):
    monkeypatch.setattr(web_app, "Pedal", FakePedal({}))
    assert client.post(f"/api/patches/{live_user_slot}/save").status_code == 409


def test_factory_patches_are_never_saved(monkeypatch, live_user_slot):
    pedal = FakePedal({})
    monkeypatch.setattr(web_app, "Pedal", pedal)
    assert client.post("/api/patches/1/save").status_code == 403
    assert pedal.uploads == []


def test_sandbox_never_saves(user_patch):
    assert client.post(f"/api/patches/{user_patch}/save").status_code == 403
    assert client.get(f"/api/patches/{user_patch}").json()["savable"] is False


def test_add_and_choose_effect(user_patch):
    body = client.post(f"/api/patches/{user_patch}/effects").json()
    assert body["factory"] is False and body["sandbox"] is True
    assert _chain(body) == [("03000080", True), ("00000000", False)]
    body = client.patch(f"/api/patches/{user_patch}/effects/2", json={"id": "03000080"}).json()
    new = body["chain"][1]
    assert (new["name"], new["enabled"]) == ("DYN Drive", True)
    assert [p["value"] for p in new["params"]] == [78, 1]  # defaults
    summary = client.get("/api/patches").json()[user_patch - 1]
    assert summary["effect_count"] == 2


def test_change_params_and_toggle(user_patch):
    url = f"/api/patches/{user_patch}/effects/1"
    body = client.patch(url, json={"enabled": False, "params": {"0": 90, "1": 1}}).json()
    fx = body["chain"][0]
    assert fx["enabled"] is False
    assert [(p["value"], p["display"]) for p in fx["params"]] == [(90, "90"), (1, "STACK")]
    # Out of range: nothing changes.
    response = client.patch(url, json={"enabled": True, "params": {"0": 101}})
    assert response.status_code == 422
    assert client.get(f"/api/patches/{user_patch}").json()["chain"][0]["enabled"] is False


def test_move_and_remove(user_patch):
    client.post(f"/api/patches/{user_patch}/effects")
    body = client.post(f"/api/patches/{user_patch}/effects/2/move", json={"to": 1}).json()
    assert _chain(body) == [("00000000", False), ("03000080", True)]
    body = client.delete(f"/api/patches/{user_patch}/effects/1").json()
    assert _chain(body) == [("03000080", True)]
    assert client.delete(f"/api/patches/{user_patch}/effects/1").status_code == 409


def test_patch_holds_at_most_six_effects(user_patch):
    for _ in range(5):
        client.post(f"/api/patches/{user_patch}/effects")
    assert client.post(f"/api/patches/{user_patch}/effects").status_code == 409


def test_factory_patches_keep_their_effects(sandbox):
    assert client.get("/api/patches/1").json()["factory"] is True
    assert client.post("/api/patches/1/effects").status_code == 403
    assert client.delete("/api/patches/1/effects/1").status_code == 403
    assert client.patch("/api/patches/1/effects/1", json={"id": "03000080"}).status_code == 403
    body = client.patch("/api/patches/1/effects/1", json={"enabled": False}).json()
    assert body["chain"][0]["enabled"] is False


def test_rename_user_patch(user_patch):
    body = client.patch(f"/api/patches/{user_patch}", json={"name": "OverDrive +Delay"}).json()
    assert body["name"] == "OverDrive +Delay"
    assert client.get("/api/patches").json()[user_patch - 1]["name"] == "OverDrive +Delay"
    response = client.patch(f"/api/patches/{user_patch}", json={"name": "Señal"})
    assert response.status_code == 422
    assert client.patch("/api/patches/1", json={"name": "Mine now"}).status_code == 403


def test_refresh_discards_edits(user_patch):
    client.patch(f"/api/patches/{user_patch}/effects/1", json={"enabled": False})
    client.get("/api/patches?refresh=true")
    assert client.get(f"/api/patches/{user_patch}").json()["chain"][0]["enabled"] is True


def test_sandbox_without_dumps(monkeypatch, tmp_path):
    monkeypatch.setattr(web_app, "source", web_app.DumpSource(tmp_path))
    web_app.cache.clear()
    assert client.get("/api/patches").status_code == 503


def test_static_files_are_revalidated():
    assert client.get("/app.js").headers["cache-control"] == "no-cache"


def test_center_of_signed_parameters():
    band = Param("100Hz", max=4, labels=["-2", "-1", "0", "+1", "+2"])
    assert web_app._center(band) == 2
    assert web_app._center(Param("Gain", max=100)) is None
    assert web_app._center(Param("Mode", max=1, labels=["COMBO", "STACK"])) is None
