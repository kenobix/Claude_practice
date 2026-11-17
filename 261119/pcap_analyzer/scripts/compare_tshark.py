"""Compare committed synthetic fixtures with offline TShark field extraction.

This developer tool deliberately accepts no capture path or arbitrary TShark
options. It never converts unsupported inputs or compares DNS window states.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from pcap_analyzer.models import Config, InputError
from pcap_analyzer.reader import PcapSource

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ("baseline.pcap", "header_only.pcap", "nano.pcap", "big_endian.pcap",
            "backward.pcap", "capture_truncated.pcap", "fragment.pcap", "unsupported_arp.pcap")
FIELDS = ("frame.number", "frame.time_epoch", "frame.cap_len", "frame.len",
          "ip.src", "ip.dst", "ip.proto", "tcp.srcport", "tcp.dstport",
          "udp.srcport", "udp.dstport", "tcp.flags")
PREFERENCES = ("ip.defragment:FALSE", "tcp.desegment_tcp_streams:FALSE",
               "tcp.analyze_sequence_numbers:FALSE")


def epoch_ns(value):
    """Exact nonnegative decimal seconds, never a binary float conversion."""
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,9})?", value):
        raise ValueError("invalid TShark epoch timestamp")
    seconds, _, fraction = value.partition(".")
    return int(seconds) * 1_000_000_000 + int(fraction.ljust(9, "0"))


def parse_rows(output):
    rows = []
    for index, line in enumerate(output.splitlines(), 1):
        values = line.split("\t")
        if len(values) != len(FIELDS):
            raise ValueError("unexpected TShark field count")
        row = dict(zip(FIELDS, values))
        for field in FIELDS:
            if field == "frame.time_epoch":
                row[field] = epoch_ns(row[field])
            elif field not in ("ip.src", "ip.dst"):
                row[field] = int(row[field], 16 if field == "tcp.flags" else 10) if row[field] else None
        if row["frame.number"] != index:
            raise ValueError("TShark frame numbers must be contiguous from 1")
        if any(row[field] is None or row[field] < 0 for field in ("frame.cap_len", "frame.len")):
            raise ValueError("invalid TShark frame lengths")
        rows.append(row)
    return rows


def compare(records, rows):
    """Require all common fields, with explicit exclusions for L4 boundaries."""
    differences, exclusions = [], []
    if len(records) != len(rows):
        differences.append({"field": "packet_count", "analyzer": len(records), "tshark": len(rows)})
    for record, row in zip(records, rows):
        expected = {"frame.number": record.packet_index, "frame.time_epoch": record.timestamp_ns,
                    "frame.cap_len": record.captured_len, "frame.len": record.original_len,
                    "ip.src": record.src_ip or "", "ip.dst": record.dst_ip or "",
                    "ip.proto": record.ip_protocol}
        if record.flow_eligible:
            transport = "tcp" if record.ip_protocol == 6 else "udp"
            expected.update({transport + ".srcport": record.src_port, transport + ".dstport": record.dst_port})
            if transport == "tcp":
                expected["tcp.flags"] = record.tcp_flags
        else:
            exclusions.append({"packet_index": record.packet_index,
                               "fields": "L4 ports/flags", "reason": record.reason})
        for field, value in expected.items():
            if row[field] != value:
                differences.append({"packet_index": record.packet_index, "field": field,
                                    "analyzer": value, "tshark": row[field]})
    return differences, exclusions


def field_args(path):
    args = ["-n", "-r", str(path), "-T", "fields", "-E", "occurrence=f"]
    for preference in PREFERENCES:
        args.extend(("-o", preference))
    for field in FIELDS:
        args.extend(("-e", field))
    return args


def run_suite(tshark_root):
    tshark_root = Path(tshark_root).resolve()
    executable = tshark_root / "usr/bin/tshark"
    if not executable.is_file():
        raise ValueError("TShark executable is missing; see docs/tshark_comparison.md")
    # Isolate personal profiles and use only the extracted distribution data.
    with tempfile.TemporaryDirectory(prefix="pcap-tshark-") as config:
        env = {**{k: v for k, v in os.environ.items() if not k.startswith("WIRESHARK_")},
               "LC_ALL": "C", "TZ": "UTC",
               "LD_LIBRARY_PATH": str(tshark_root / "usr/lib/x86_64-linux-gnu"),
               "WIRESHARK_CONFIG_DIR": config,
               "WIRESHARK_PLUGIN_DIR": config, "WIRESHARK_EXTCAP_DIR": config,
               "WIRESHARK_DATA_DIR": str(tshark_root / "usr/share/wireshark")}

        def run(args):
            result = subprocess.run([str(executable), *args], env=env, capture_output=True,
                                    text=True, encoding="utf-8", errors="strict", timeout=30)
            if result.returncode:
                raise ValueError("TShark failed: " + result.stderr.strip())
            return result

        version = run(["--version"]).stdout
        results = []
        for name in FIXTURES:
            path = ROOT / "fixtures" / name
            with PcapSource(path, Config()) as source:
                records = list(source)
            result = run(field_args(path))
            rows = parse_rows(result.stdout)
            differences, exclusions = compare(records, rows)
            results.append({"fixture": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                            "packet_count": len(rows), "captured_bytes": sum(r["frame.cap_len"] for r in rows),
                            "original_bytes": sum(r["frame.len"] for r in rows),
                            "rows": rows, "differences": differences, "exclusions": exclusions,
                            "stderr": result.stderr})
        return {"tshark_version": version, "fields": FIELDS, "preferences": PREFERENCES,
                "name_resolution": False, "display_filter": None, "capture_filter": None,
                "results": results, "matched": all(not r["differences"] for r in results)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tshark-root", type=Path, default=ROOT / ".venv/tshark/root")
    args = parser.parse_args()
    try:
        result = run_suite(args.tshark_root)
    except (OSError, ValueError, InputError, subprocess.TimeoutExpired) as error:
        parser.exit(2, str(error) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True))
    return 0 if result["matched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
