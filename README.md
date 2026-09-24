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
    effect with its on/off state, name, group, description and its named parameters. The selected slot is kept
    in the URL (`#slot-21`).
  - `<amp-knob>` web component (`knob.js`): amp/stompbox-style rotary control with drag,
    wheel, keyboard and double-click-to-reset. Not used on the page yet; meant for binding
    to real parameters once their names and ranges are known.

- Effect names come from the pedal itself (`stomp_whisperer.effects`), using read-only
  file access (`Pedal.list_files` / `Pedal.download_file`): `FLST_SEQ.ZT2` maps effect IDs
  to `*.ZD2` files, and each ZD2 holds the effect's name, group, English description and a
  JSON list of its parameters (`PRME` chunk) in the order patches store their values. A ZD2
  is ~34 KB and takes ~3 s over MIDI, so the web server reads them in a background thread
  (the open patch's effects first) and keeps only the metadata in
  `~/.cache/stomp-whisperer/effects.json`; binaries are never stored. Effect ID `0x0` is an
  empty slot.
- Parameter ranges, defaults and display labels (`stomp_whisperer.effect_params`): a ZD2's
  `DATA` chunk is a TI C6000 ELF whose `.const` section holds one 0x38-byte descriptor per
  parameter (name, max, default, display function). Display functions are DSP code, so
  they're interpreted by symbol name: `disp_prm_*` label tables (COMBO/STACK, 20Hz…20kHz),
  offset rules (`offset_minus12_05` → −12…+12 in 0.5 steps, `off_to_100` → OFF, 0…100) and
  tempo-synced ranges (numbers followed by note values: 1/16, 1/8T, 1/8., 1/4×2…). The
  `*_Sync` rules (delay times in 1 ms then 10 ms steps, LFO rates, where synced notes start)
  were read from the functions disassembled with a `tic6x-elf` build of GNU objdump; a rule
  only applies when the parameter's range matches the one it was read from. The web UI shows
  each value as the pedal would, with its range in the tooltip.

**Next steps (in order):**
1. Confirm the note glyphs against the pedal's screen: the order is certain, but whether
   `\x18` is an eighth or a quarter note (and so every synced label) is still inferred.
2. Grow the web UI on top of the (still read-only) `Pedal` API: bind `<amp-knob>` to real
   parameters in the detail view now that ranges are known; drag-and-drop for rearranging
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
