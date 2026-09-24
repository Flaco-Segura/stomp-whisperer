# StompWhisperer

A desktop application to browse, manage, and build effect chains on a Zoom MS-50G+ multi-effects pedal over USB MIDI, using the pedal's own built-in effect library — a friendlier alternative to editing patches directly on the device's small screen.

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

**Next steps (in order):**
1. Add a CLI command to iterate and dump **all** patch slots (not just the current one),
   decoding at least the patch name from each.
2. Start decoding the effect-chain structure inside a patch (which effect modules are
   active, order, parameters) — needed before any write support makes sense.
3. Build a **PySide6** desktop UI on top of the (still read-only) `Pedal` API — chosen
   over Textual/Tkinter/web app for native drag-and-drop support when rearranging effect
   chains across slots.
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

### Planned Python dependencies

- `python-rtmidi` — USB MIDI I/O (SysEx messages) on Linux via ALSA.

## Known Risks

- Writing effect/patch data to the pedal carries a documented risk of leaving it unresponsive if malformed or incompatible data is sent (see project notes / commit history for details). Read-only features (listing existing patches) are considered low-risk and are the first development milestone. Write features will only compose patches from the pedal's own built-in effect library — never import binaries from other Zoom models.
