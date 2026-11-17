"""Offline DNS comparison on fixed documentation-only captures and recipes."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile

from compare_tshark import ROOT, PREFERENCES, epoch_ns
from jsonschema import Draft202012Validator, ValidationError
from pcap_analyzer.dns import observation
from pcap_analyzer.models import Config, InputError
from pcap_analyzer.reader import PcapSource
from pcap_analyzer.report import analyze

FIELDS = ("frame.number", "frame.time_epoch", "ip.src", "ip.dst", "udp.srcport", "udp.dstport",
          "dns.id", "dns.flags.response", "dns.qry.name", "dns.qry.type", "dns.qry.class",
          "dns.flags.rcode", "dns.flags.truncated", "dns.response_to", "dns.response_in", "dns.time")
EXPECTED = json.loads((ROOT / "expected/tshark.dns_cases.json").read_text())


def packet(ident=10, *, response=False, name=b"alpha.test", qtype=1, qclass=1,
           rcode=0, tc=False, fragment=False, dns=True, payload=None):
    """Hand-packed Ethernet/IPv4/UDP; no Scapy or analyzer DNS encoder."""
    source, destination = bytes([192, 0, 2, 1]), bytes([198, 51, 100, 53])
    sport, dport = 53000 + ident, 53 if dns else 9
    if response:
        source, destination, sport, dport = destination, source, dport, sport
    flags = (0x8180 if response else 0x0100) | rcode | (0x0200 if tc else 0)
    question = b"".join(bytes([len(label)]) + label for label in name.split(b".")) + b"\0"
    body = struct.pack("!6H", ident, flags, 1, 0, 0, 0) + question + struct.pack("!HH", qtype, qclass) if dns else b""
    if payload is not None:
        body = payload
    udp = struct.pack("!4H", sport, dport, 8 + len(body), 0) + body
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 1, 0x2000 if fragment else 0,
                     64, 17, 0, source, destination)
    return bytes.fromhex("0200000000020200000000010800") + ip + udp


def synthetic_captures():
    query = lambda ident, **kw: packet(ident, **kw)
    response = lambda ident, **kw: packet(ident, response=True, **kw)
    truncated = response(16)[:-1]
    metadata = [(0, query(10, name=b"Alpha.test")),
                (25_000_000, response(10, name=b"ALPHA.test", rcode=3)),
                (26_000_000, response(10, rcode=3)),
                (1_000_000_000, query(11, qtype=28)),
                (1_000_000_123, response(11, qtype=28, tc=True)),
                (2_000_000_000, query(12, qclass=3)),
                (7_000_000_000, response(12, qclass=3)),
                (7_000_000_001, response(13)), (8_000_000_000, query(14)),
                (9_000_000_000, response(15, fragment=True)),
                (9_000_000_001, truncated, len(truncated) + 1),
                (9_000_000_002, packet(17, payload=b"\x00\x11"))]
    late = [(0, query(20)), (5_000_000_001, response(20)),
            (6_000_000_000, query(20)), (6_000_000_001, response(20)),
            (7_000_000_000, query(21)), (8_000_000_000, response(21)),
            (8_500_000_000, query(21)), (13_000_000_000, packet(dns=False))]

    def capture(rows):
        output = bytearray(struct.pack("<IHHIIII", 0xA1B23C4D, 2, 4, 0, 0, 65535, 1))
        for ns, data, *wirelen in rows:
            seconds, fraction = divmod(ns, 1_000_000_000)
            output.extend(struct.pack("<4I", 1700000000 + seconds, fraction, len(data),
                                      wirelen[0] if wirelen else len(data)))
            output.extend(data)
        return bytes(output)

    return {"dns_metadata.pcap": capture(metadata), "dns_late_reuse.pcap": capture(late)}


def signed_ns(value):
    return -epoch_ns(value[1:]) if value.startswith("-") else epoch_ns(value)


def parse_rows(output):
    rows = []
    for index, line in enumerate(output.splitlines(), 1):
        values = line.split("\t")
        if len(values) != len(FIELDS):
            raise ValueError("invalid TShark DNS field count")
        row = dict(zip(FIELDS, values))
        for field, value in row.items():
            if field == "frame.time_epoch":
                row[field] = epoch_ns(value)
            elif field == "dns.time":
                row[field] = signed_ns(value) if value else None
            elif field in ("dns.flags.response", "dns.flags.truncated"):
                if value not in ("", "True", "False"):
                    raise ValueError("invalid TShark DNS boolean")
                row[field] = {"": None, "True": True, "False": False}[value]
            elif field not in ("ip.src", "ip.dst", "dns.qry.name"):
                row[field] = int(value, 16 if value.startswith("0x") else 10) if value else None
        if row["frame.number"] != index:
            raise ValueError("noncontiguous TShark DNS frame numbers")
        rows.append(row)
    return rows


def field_args(path):
    args = ["-n", "-2", "-r", str(path), "-T", "fields", "-E", "occurrence=f"]
    for preference in PREFERENCES:
        args.extend(("-o", preference))
    for field in FIELDS:
        args.extend(("-e", field))
    return args


def ordinary_name(labels):
    # Printed TShark names are used only for this fixed ordinary ASCII subset.
    # Never reconstruct production DNS keys from this display string.
    if not labels or labels[-1] != b"" or any(not re.fullmatch(rb"[A-Za-z0-9-]+", label) for label in labels[:-1]):
        raise ValueError("DNS comparison requires ordinary ASCII labels")
    return ".".join(label.decode("ascii").lower() for label in labels[:-1])


def compact_events(dns):
    return [[e["status"], e["query_indices"], e["response_indices"], e["response_time_ns"], e["rcode"], e["tc"]]
            for e in dns["events"]]


def evaluate(records, rows, report, expected):
    differences, excluded, latencies = [], [], []

    def check(field, wanted, observed, index=None):
        if wanted != observed:
            differences.append({"field": field, "packet_index": index, "expected": wanted, "observed": observed})

    check("packet_count", len(records), len(rows))
    eligible = []
    for record, row in zip(records, rows):
        index = record.packet_index
        check("frame.number", index, row["frame.number"], index)
        check("frame.time_epoch", record.timestamp_ns, row["frame.time_epoch"], index)
        item = observation(record)
        if item is None:
            if row["dns.id"] is not None or 53 in (record.src_port, record.dst_port):
                excluded.append({"packet_index": index, "classification": record.classification,
                                 "reason": record.reason, "tshark_dns_id": row["dns.id"]})
            continue
        eligible.append(row)
        question = record.dns_question
        values = {"ip.src": record.src_ip, "ip.dst": record.dst_ip,
                  "udp.srcport": record.src_port, "udp.dstport": record.dst_port,
                  "dns.id": question.dns_id, "dns.flags.response": question.is_response,
                  "dns.qry.name": ordinary_name(question.name.labels),
                  "dns.qry.type": question.qtype, "dns.qry.class": question.qclass,
                  "dns.flags.truncated": question.tc,
                  "dns.flags.rcode": question.rcode if question.is_response else None}
        for field, value in values.items():
            observed = row[field].lower() if field == "dns.qry.name" else row[field]
            check(field, value, observed, index)

    dns = report["dns"]
    summary = report["summary"]
    measured = {"packet_count": summary["packet_count"], "captured_bytes": summary["captured_bytes"],
                "original_bytes": summary["original_bytes"], "classification_counts": summary["classification_counts"],
                "query_count": dns["query_count"], "response_count": dns["response_count"],
                "time_analysis": dns["time_analysis"], "events": compact_events(dns)}
    for field, value in expected["analyzer"].items():
        check("analyzer." + field, value, measured[field])
    links = [[r["frame.number"], r["dns.response_to"], r["dns.time"]]
             for r in eligible if r["dns.flags.response"]]
    check("tshark.response_links", expected["tshark_response_links"], links)
    by_index = {r["frame.number"]: r for r in rows}
    for event in dns["events"]:
        if event["response_indices"]:
            index = event["response_indices"][0]
            row = by_index.get(index, {})
            check("event.rcode", event["rcode"], row.get("dns.flags.rcode"), index)
            check("event.tc", event["tc"], row.get("dns.flags.truncated"), index)
        if event["status"] == "matched":
            query, response = event["query_indices"][0], event["response_indices"][0]
            row = by_index.get(response, {})
            check("matched.response_to", query, row.get("dns.response_to"), response)
            check("matched.response_in", response, by_index.get(query, {}).get("dns.response_in"), query)
            check("matched.time_ns", event["response_time_ns"], row.get("dns.time"), response)
            latencies.append({"query": query, "response": response, "analyzer_ns": event["response_time_ns"],
                              "tshark_ns": row.get("dns.time")})
    return {"differences": differences, "exclusions": excluded, "matched_latencies": latencies,
            "analyzer": measured, "tshark_response_links": links,
            "explained_differences": expected["explained_differences"]}


def run_suite(tshark_root):
    root = Path(tshark_root).resolve()
    with tempfile.TemporaryDirectory(prefix="pcap-dns-compare-") as temporary:
        directory = Path(temporary)
        env = {**{k: v for k, v in os.environ.items() if not k.startswith("WIRESHARK_")},
               "LC_ALL": "C", "TZ": "UTC", "LD_LIBRARY_PATH": str(root / "usr/lib/x86_64-linux-gnu"),
               "WIRESHARK_CONFIG_DIR": temporary, "WIRESHARK_PLUGIN_DIR": temporary,
               "WIRESHARK_EXTCAP_DIR": temporary, "WIRESHARK_DATA_DIR": str(root / "usr/share/wireshark")}

        def run(args):
            result = subprocess.run([str(root / "usr/bin/tshark"), *args], env=env, capture_output=True,
                                    text=True, encoding="utf-8", errors="strict", timeout=30)
            if result.returncode or result.stderr:
                raise ValueError("TShark DNS comparison failed: " + result.stderr.strip())
            return result.stdout

        captures = [ROOT / "fixtures" / name for name in ("baseline.pcap", "backward.pcap")]
        for name, data in synthetic_captures().items():
            path = directory / name
            path.write_bytes(data)
            captures.append(path)
        results = []
        for path in captures:
            with PcapSource(path, Config()) as source:
                records = list(source)
            rows = parse_rows(run(field_args(path)))
            report = analyze(path)
            Draft202012Validator(json.loads((ROOT / "docs/report.schema.json").read_text())).validate(report)
            results.append({"fixture": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                            "rows": rows, **evaluate(records, rows, report, EXPECTED[path.name])})
        return {"tshark_version": run(["--version"]), "fields": FIELDS, "preferences": PREFERENCES,
                "passes": 2, "name_resolution": False, "display_filter": None, "capture_filter": None,
                "results": results, "matched": all(not r["differences"] for r in results)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tshark-root", type=Path, default=ROOT / ".venv/tshark/root")
    args = parser.parse_args()
    try:
        result = run_suite(args.tshark_root)
    except (OSError, ValueError, InputError, ValidationError, subprocess.TimeoutExpired) as error:
        parser.exit(2, str(error) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True))
    return 0 if result["matched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
