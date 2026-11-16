"""Startup contract only: never imply an analysis has been performed."""

import argparse
import sys

from . import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline PCAP analyzer (Phase 0 scaffold)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("input", nargs="?", help="saved classic pcap (Phase 1; not implemented)")
    args = parser.parse_args(argv)
    if args.input is None:
        parser.print_help()
        return 0
    print("E_NOT_IMPLEMENTED: PCAP analysis is scheduled for Phase 1", file=sys.stderr)
    return 3
