"""FastAPI app: JSON API for the pedal plus the static front-end."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from ..pedal import find_pedal_port

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="StompWhisperer")


@app.get("/api/status")
def status() -> dict:
    port = find_pedal_port()
    return {"connected": port is not None, "port": port}


# Mounted last so /api/* routes take precedence.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
