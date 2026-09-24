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

If the pedal is not connected, the page shows a "Connect your pedal" modal that closes by
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
  effect IDs, raw chunks) plus the `EDTB` effect chain: per slot, effect ID, on/off flag and
  12 raw parameter values. Verified against all 100 patches of a real MS-50G+ (names decode
  correctly; EDTB IDs match the header IDs). Parsing is bounded by the header's `length`
  field, since the pedal pads each slot with stale bytes from its previous contents.
- CLI `stomp-whisperer list [--save DIR]`: iterates every slot, prints its name and effect
  chain (`+`/`-` = on/off, then the effect ID in hex), and optionally saves raw `.bin` dumps.

- Local web UI skeleton (`stomp-whisperer serve` → http://127.0.0.1:8000): FastAPI serving
  plain HTML/JS/CSS from `src/stomp_whisperer/web/static/` (no build step).
  - `GET /api/status` reports whether the pedal's MIDI port is visible; the page polls it
    every 2 s and shows a blocking "Connect your pedal" modal until it appears (or a
    "server offline" variant if the backend stops).
  - Patch browser: once the pedal is connected the page reads every slot (`GET /api/patches`,
    ~1.3 s for 100 slots, cached server-side until *Refresh* or a disconnect) and lists them
    with their name and one LED per effect (lit = on). Clicking a slot shows its detail
    (`GET /api/patches/{slot}`): description and the effect chain in signal order, each
    effect with its on/off state, ID and 12 raw parameter values. The selected slot is kept
    in the URL (`#slot-21`).
  - `<amp-knob>` web component (`knob.js`): amp/stompbox-style rotary control with drag,
    wheel, keyboard and double-click-to-reset. Not used on the page yet; meant for binding
    to real parameters once their names and ranges are known.

**Next steps (in order):**
1. Map effect IDs to names and parameter names/ranges. Observed so far: ID `0x0` is an empty
   slot, and the top byte looks like the category (`0x03` drive, `0x06` mod, `0x08` delay,
   `0x09` reverb). Factory patches 1–20 each hold a single effect named after the patch,
   which gives a first set of known IDs.
2. Grow the web UI on top of the (still read-only) `Pedal` API: show effect names and
   bind `<amp-knob>` to real parameters in the detail view; drag-and-drop for rearranging
   effect chains via vendored SortableJS. (Replaces the earlier PySide6 plan — too heavy.)
3. Only after read support is solid: design patch *writing* (composing patches from the
   pedal's own built-in effect library only — never importing effect binaries from other
   Zoom models, see Known Risks below).

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
