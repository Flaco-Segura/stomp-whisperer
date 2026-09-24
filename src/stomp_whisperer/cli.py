"""Command-line entry point: `stomp-whisperer <command>`."""

from __future__ import annotations

import argparse
import sys

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stomp-whisperer")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("info", help="Connect and print patch storage info").set_defaults(func=cmd_info)
    subparsers.add_parser("current", help="Dump the currently active patch").set_defaults(func=cmd_current)

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
