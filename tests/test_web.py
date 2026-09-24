from fastapi.testclient import TestClient

from stomp_whisperer.web import app as web_app

client = TestClient(web_app.app)


def test_status_disconnected(monkeypatch):
    monkeypatch.setattr(web_app, "find_pedal_port", lambda: None)
    assert client.get("/api/status").json() == {"connected": False, "port": None}


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
    assert first["effects"] == [{"id": "03000080", "enabled": False},
                                {"id": "08000060", "enabled": True}]
    assert second["name"] is None and second["error"] == "Slot is empty"


def test_patch_detail(monkeypatch):
    monkeypatch.setattr(web_app, "read_all_slots", _fake_slots)
    web_app.cache.clear()
    body = client.get("/api/patches/1").json()
    assert body["description"] == "Drive into delay."
    assert [fx["params"][0] for fx in body["chain"]] == [64, 279]
    assert client.get("/api/patches/3").status_code == 404


def test_patches_without_pedal(monkeypatch):
    def no_pedal():
        raise web_app.PedalNotFoundError("not found")

    monkeypatch.setattr(web_app, "read_all_slots", no_pedal)
    web_app.cache.clear()
    assert client.get("/api/patches").status_code == 503


def test_static_files_are_revalidated():
    assert client.get("/app.js").headers["cache-control"] == "no-cache"
