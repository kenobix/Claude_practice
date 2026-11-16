"""Incremental Phase 1 aggregation and deterministic renderers."""

import json
from dataclasses import asdict

from . import __version__
from .models import Config, InputError, PROTOCOLS, STATES
from .reader import PcapSource


def analyze(path, config: Config | None = None) -> dict:
    config = config or Config()
    with PcapSource(path, config) as source:
        summary = {
            "packet_count": 0, "captured_bytes": 0, "original_bytes": 0,
            "classification_counts": dict.fromkeys(STATES, 0),
            "protocol_counts": dict.fromkeys(PROTOCOLS, 0),
            "parsed_ratio": None, "start_ns": None, "end_ns": None,
            "duration_ns": None, "ipv4_fragment_count": 0,
            "timestamp_invalid_count": 0,
        }
        report = {
            "schema_version": "0.2.0", "phase": 1,
            "input": {"sha256": source.sha256, "size_bytes": source.size_bytes,
                      "timestamp_precision": "ns" if source.nano else "us", "linktype": 1},
            "tool": {"name": "pcap-analyzer", "version": __version__},
            "config": {"version": "0.2", **asdict(config)},
            "complete": True, "summary": summary,
            "classification_indices": {state: [] for state in STATES},
            "flows": None,
            "dns": {"status": "not_implemented", "time_analysis": "not_implemented", "events": None},
            "diagnostics": [],
        }
        previous_timestamp = None

        def diagnostic(code, reason, index):
            if len(report["diagnostics"]) >= config.max_diagnostics:
                raise InputError("E_LIMIT_DIAGNOSTICS", "diagnostic limit reached: " + reason, index)
            report["diagnostics"].append({"code": code, "reason": reason, "packet_index": index})

        try:
            for record in source:
                summary["packet_count"] += 1
                summary["captured_bytes"] += record.captured_len
                summary["original_bytes"] += record.original_len
                summary["classification_counts"][record.classification] += 1
                summary["protocol_counts"][record.protocol] += 1
                report["classification_indices"][record.classification].append(record.packet_index)
                summary["ipv4_fragment_count"] += record.protocol == "ipv4_fragment"
                regressed = False
                if record.timestamp_ns is None:
                    summary["timestamp_invalid_count"] += 1
                else:
                    t = record.timestamp_ns
                    summary["start_ns"] = t if summary["start_ns"] is None else min(summary["start_ns"], t)
                    summary["end_ns"] = t if summary["end_ns"] is None else max(summary["end_ns"], t)
                    regressed = previous_timestamp is not None and t < previous_timestamp
                    previous_timestamp = t
                if record.reason:
                    diagnostic("P_" + record.classification.upper(), record.reason, record.packet_index)
                if regressed and report["dns"]["time_analysis"] != "disabled_timestamp_regression":
                    report["dns"]["time_analysis"] = "disabled_timestamp_regression"
                    diagnostic("W_TIMESTAMP_REGRESSION", "timestamps regress in file order; DNS timing must remain disabled", record.packet_index)
        except OSError:
            report["complete"] = False
            report["diagnostics"].append(InputError("E_INPUT", "input read failed", summary["packet_count"] + 1).diagnostic())
        except InputError as error:
            report["complete"] = False
            report["diagnostics"].append(error.diagnostic())
        count = summary["packet_count"]
        if count:
            summary["parsed_ratio"] = summary["classification_counts"]["parsed"] / count
        if summary["start_ns"] is not None:
            summary["duration_ns"] = summary["end_ns"] - summary["start_ns"]
        report["diagnostics"].sort(key=lambda item: (item["packet_index"] or 0, item["code"]))
        return report


def render_json(report: dict) -> str:
    return json.dumps(report, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False)


def render_table(report: dict) -> str:
    s = report["summary"]
    rows = [("Input processing", "complete" if report["complete"] else "INCOMPLETE"),
            ("Packets", s["packet_count"]), ("Captured bytes", s["captured_bytes"]),
            ("Original bytes (recorded)", s["original_bytes"]),
            ("Timestamp precision", report["input"]["timestamp_precision"]),
            ("Start timestamp (ns)", s["start_ns"]), ("End timestamp (ns)", s["end_ns"]),
            ("Duration (ns)", s["duration_ns"])]
    rows.extend([("Parsed ratio", s["parsed_ratio"]),
                 ("Invalid timestamps", s["timestamp_invalid_count"])])
    rows.extend((state, s["classification_counts"][state]) for state in STATES)
    rows.extend((protocol, s["protocol_counts"][protocol]) for protocol in PROTOCOLS)
    rows.extend([("Flow aggregation", "not implemented (Phase 2)"),
                 ("DNS matching", "not implemented (Phase 2)")])
    lines = [f"{label:<28} {value if value is not None else 'n/a'}" for label, value in rows]
    for item in report["diagnostics"]:
        index = f" packet #{item['packet_index']}" if item["packet_index"] is not None else ""
        lines.append(f"{item['code']}{index}: {item['reason']}")
    lines.append("Captured/original lengths describe this recording, not measured link traffic.")
    return "\n".join(lines)
