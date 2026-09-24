# StompWhisperer

An application (local web UI) to browse, manage, and build effect chains on a Zoom MS-50G+ multi-effects pedal over USB MIDI, using the pedal's own built-in effect library — a friendlier alternative to editing patches directly on the device's small screen.

## Getting Started

First-time setup (see [Technical Requirements](#technical-requirements) for system packages):

```bash
cd ~/Repos/stomp-whisperer
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Running the web UI

```bash
cd ~/Repos/stomp-whisperer
source .venv/bin/activate
stomp-whisperer serve
```

Then open http://127.0.0.1:8000 in your browser. Stop the server with `Ctrl+C`.

- Without activating the virtualenv: `~/Repos/stomp-whisperer/.venv/bin/stomp-whisperer serve`
- If port 8000 is busy, pick another one: `stomp-whisperer serve --port 8080`

If the pedal is not connected, the page shows a "Conecta tu pedal" modal that closes by
itself as soon as the pedal is detected over USB.

### CLI commands

```bash
stomp-whisperer info                 # connect and print patch storage info
stomp-whisperer current              # dump the currently active patch (hex)
stomp-whisperer list [--save DIR]    # list every patch slot; optionally save raw .bin dumps
```

## Status

Read-only SysEx communication with the pedal is working end-to-end, verified against
real hardware (a MS-50G+ over USB, seen on Linux as `ZOOM MS Plus Series`).

**Done:**
- Project scaffolding: `src/stomp_whisperer` package, `pyproject.toml`, `pytest` tests,
  installable in editable mode (`pip install -e .`).
- `stomp_whisperer.protocol`: SysEx message builders + 7-bit/8-bit pack/unpack helpers,
  based on the reverse-engineered protocol from
  [mungewell/zoom-zt2](https://github.com/mungewell/zoom-zt2) (device ID `0x6E`,
  shared across the whole "Plus" series: MS-50G+, MS-60B+, MS-70CDR+, MS-80IR+).
- `stomp_whisperer.pedal.Pedal`: MIDI transport (`python-rtmidi`), PC-mode handshake,
  `patch_check`, `download_current_patch`, `download_patch` (CRC32-verified).
- CLI (`stomp-whisperer info` / `stomp-whisperer current`) confirmed working live:
  100 patches, 10 banks, 848 bytes/patch; current patch downloads with a valid checksum.
- `stomp_whisperer.patch`: dependency-free PTCF patch parser (name, version, target,
  effect IDs, raw chunks). Unit-tested with synthetic data; **not yet verified on hardware**.
- CLI `stomp-whisperer list [--save DIR]`: iterates every slot, prints its name and effect
  count, and optionally saves raw `.bin` dumps for offline decoding.

- Local web UI skeleton (`stomp-whisperer serve` → http://127.0.0.1:8000): FastAPI serving
  plain HTML/JS/CSS from `src/stomp_whisperer/web/static/` (no build step).
  - `GET /api/status` reports whether the pedal's MIDI port is visible; the page polls it
    every 2 s and shows a blocking "Conecta tu pedal" modal until it appears (or a
    "server offline" variant if the backend stops).
  - `<amp-knob>` web component (`knob.js`): amp/stompbox-style rotary control with drag,
    wheel, keyboard and double-click-to-reset. Currently shown as a preview only, not
    wired to pedal parameters.

**Next steps (in order):**
1. Run `stomp-whisperer list --save dumps/` against the pedal to verify the name decoding
   and collect real patch binaries.
2. Start decoding the effect-chain structure inside a patch (which effect modules are
   active, order, parameters) — needed before any write support makes sense.
3. Grow the web UI on top of the (still read-only) `Pedal` API: patch list, then effect
   chain view with `<amp-knob>` bound to real parameters; drag-and-drop for rearranging
   effect chains via vendored SortableJS. (Replaces the earlier PySide6 plan — too heavy.)
4. Only after read support is solid: design patch *writing* (composing patches from the
   pedal's own built-in effect library only — never importing effect binaries from other
   Zoom models, see Known Risks below).

No UI code exists yet — everything so far is the CLI/library.

## Technical Requirements

### System

- **OS**: Linux (developed and tested on Ubuntu 24.04.5 LTS, kernel 6.8)
- **Python**: 3.12
- **`python3.12-venv`**: required system package to create the project's virtual environment (`python3 -m venv` fails without it — `ensurepip` is not bundled in Ubuntu's default Python install). Install with:
  ```
  sudo apt install python3.12-venv
  ```

### Hardware

- **Zoom MS-50G+** multi-effects pedal, connected via USB. The pedal exposes itself as a class-compliant USB MIDI device (no proprietary Zoom drivers needed for transport).

### Python dependencies

- `python-rtmidi` — USB MIDI I/O (SysEx messages) on Linux via ALSA.
- `fastapi` + `uvicorn` — local web UI server.
- Dev: `pip install -e ".[dev]"` adds `pytest` and `httpx` (for FastAPI's test client).

## Known Risks

- Writing effect/patch data to the pedal carries a documented risk of leaving it unresponsive if malformed or incompatible data is sent (see project notes / commit history for details). Read-only features (listing existing patches) are considered low-risk and are the first development milestone. Write features will only compose patches from the pedal's own built-in effect library — never import binaries from other Zoom models.
