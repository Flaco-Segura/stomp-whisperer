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
