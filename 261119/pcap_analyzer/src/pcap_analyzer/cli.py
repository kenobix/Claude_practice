"""Read saved input only; diagnostics never contaminate JSON stdout."""

import argparse
import sys

from . import __version__
from .models import Config, InputError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline classic pcap analyzer (Phase 2a: flows)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("input", nargs="?", help="saved uncompressed classic pcap, Ethernet link type 1")
    parser.add_argument("--format", choices=("json", "table"), default="table")
    defaults = Config()
    for name in ("max_input_bytes", "max_records", "max_packet_bytes", "max_diagnostics", "max_flows"):
        parser.add_argument("--" + name.replace("_", "-"), type=int, default=getattr(defaults, name),
                            help=f"lower the {name} limit (default/hard maximum: %(default)s)")
    args = parser.parse_args(argv)
    if args.input is None:
        parser.print_help()
        return 0
    try:
        config = Config(**{name: getattr(args, name) for name in
                           ("max_input_bytes", "max_records", "max_packet_bytes", "max_diagnostics", "max_flows")})
    except ValueError as error:
        parser.error(str(error))
    from .report import analyze, render_json, render_table

    try:
        report = analyze(args.input, config)
    except InputError as error:
        print(f"{error.code}: {error.reason}", file=sys.stderr)
        return 2
    print(render_json(report) if args.format == "json" else render_table(report))
    if not report["complete"]:
        for item in report["diagnostics"]:
            if item["code"].startswith("E_"):
                print(f"{item['code']}: {item['reason']}", file=sys.stderr)
        return 2
    return 0
