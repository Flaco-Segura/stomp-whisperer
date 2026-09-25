"""Command-line entry point: `stomp-whisperer <command>`."""

from __future__ import annotations

import argparse
import sys

from pathlib import Path

from .patch import PatchFormatError, parse_patch
from .pedal import Pedal, PedalNotFoundError


def cmd_info(_args: argparse.Namespace) -> int:
    with Pedal() as pedal:
        print(f"Connected: IN='{pedal.in_name}' OUT='{pedal.out_name}'")
        pedal.pc_mode_on()
        try:
            info = pedal.patch_check()
            print(f"Patches: {info.count} total, {info.bank_size} banks, "
                  f"{info.patch_size} bytes per patch")
        finally:
            pedal.pc_mode_off()
    return 0


def cmd_current(_args: argparse.Namespace) -> int:
    with Pedal() as pedal:
        pedal.pc_mode_on()
        try:
            data, checksum_ok = pedal.download_current_patch()
            print(f"{len(data)} bytes received, checksum ok={checksum_ok}")
            print(data.hex(" "))
        finally:
            pedal.pc_mode_off()
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    save_dir: Path | None = args.save
    if save_dir is not None:
        save_dir.mkdir(parents=True, exist_ok=True)

    with Pedal() as pedal:
        pedal.pc_mode_on()
        try:
            info = pedal.patch_check()
            for location in range(1, info.count + 1):
                data, checksum_ok = pedal.download_patch(location, info.bank_size)
                if not data:
                    print(f"{location:3d}  (empty)")
                    continue
                try:
                    patch = parse_patch(data)
                    chain = "  ".join(f"{'+' if fx.enabled else '-'}{fx.id:08x}"
                                      for fx in patch.effects)
                    summary = f"{patch.display_name:<28} {chain}"
                except PatchFormatError as exc:
                    summary = f"(undecodable: {exc})"
                flag = "" if checksum_ok else "  [BAD CHECKSUM]"
                print(f"{location:3d}  {summary}{flag}")
                if save_dir is not None:
                    (save_dir / f"patch_{location:03d}.bin").write_bytes(data)
        finally:
            pedal.pc_mode_off()
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .web import app as web_app

    if args.sandbox is not None:
        if not any(args.sandbox.glob("patch_*.bin")):
            print(f"Error: no patch_NNN.bin dumps in {args.sandbox} "
                  "(save them with `stomp-whisperer list --save DIR`)", file=sys.stderr)
            return 1
        web_app.use_sandbox(args.sandbox)
        print(f"Sandbox mode: serving patches from {args.sandbox}, the pedal is not used")
    print(f"StompWhisperer UI: http://{args.host}:{args.port}")
    uvicorn.run(web_app.app, host=args.host, port=args.port, log_level="warning")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stomp-whisperer")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("info", help="Connect and print patch storage info").set_defaults(func=cmd_info)
    subparsers.add_parser("current", help="Dump the currently active patch").set_defaults(func=cmd_current)

    list_parser = subparsers.add_parser("list", help="List every patch slot with its name")
    list_parser.add_argument("--save", type=Path, metavar="DIR",
                             help="Also save each raw patch as DIR/patch_NNN.bin")
    list_parser.set_defaults(func=cmd_list)

    serve_parser = subparsers.add_parser("serve", help="Start the local web UI")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument("--sandbox", type=Path, nargs="?", const=Path("dumps"), metavar="DIR",
                              help="Simulate a connected pedal with the dumps in DIR "
                                   "(default: dumps)")
    serve_parser.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except PedalNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except TimeoutError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
