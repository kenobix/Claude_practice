"""Incremental packet/flow aggregation and deterministic renderers."""

import json
from dataclasses import asdict

from . import __version__
from .flows import FlowCollector
from .models import Config, InputError, PROTOCOLS, STATES
from .reader import PcapSource


def analyze(path, config: Config | None = None) -> dict:
    config = config or Config()
    flows = FlowCollector(config.max_flows)
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
            "schema_version": "0.3.0", "phase": "2a",
            "input": {"sha256": source.sha256, "size_bytes": source.size_bytes,
                      "timestamp_precision": "ns" if source.nano else "us", "linktype": 1},
            "tool": {"name": "pcap-analyzer", "version": __version__},
            "config": {"version": "0.3", **asdict(config)},
            "complete": True, "summary": summary,
            "classification_indices": {state: [] for state in STATES},
            "flows": [],
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
                first_regression = regressed and report["dns"]["time_analysis"] != "disabled_timestamp_regression"
                if first_regression:
                    report["dns"]["time_analysis"] = "disabled_timestamp_regression"
                # Account for this record before any diagnostic-limit failure.
                flows.add(record)
                if record.reason:
                    diagnostic("P_" + record.classification.upper(), record.reason, record.packet_index)
                if first_regression:
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
        report["flows"] = flows.to_list()
        summary["flow_count"] = len(report["flows"])
        summary["unattributed_packet_count"] = flows.unattributed["packets"]
        summary["unattributed_captured_bytes"] = flows.unattributed["captured_bytes"]
        summary["unattributed_original_bytes"] = flows.unattributed["original_bytes"]
        report["unattributed_indices"] = flows.unattributed["packet_indices"]
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
    rows.extend([("Flows", s["flow_count"]),
                 ("Unattributed packets", s["unattributed_packet_count"]),
                 ("Unattributed captured bytes", s["unattributed_captured_bytes"]),
                 ("Unattributed original bytes", s["unattributed_original_bytes"]),
                 ("DNS structure", "checked for complete UDP port-53 candidates"),
                 ("DNS matching", "not implemented (Phase 2b)")])
    lines = [f"{label:<28} {value if value is not None else 'n/a'}" for label, value in rows]
    if report["flows"]:
        lines.append("Flows (endpoint aggregates; not TCP connections):")
    for flow in report["flows"]:
        a, b = flow["a"], flow["b"]
        lines.append(f"  {'TCP' if flow['protocol'] == 6 else 'UDP'} {a['ip']}:{a['port']} <-> {b['ip']}:{b['port']}")
        for direction in ("a_to_b", "b_to_a"):
            d = flow[direction]
            lines.append(f"    {direction}: packets={d['packets']} captured={d['captured_bytes']} original={d['original_bytes']} packet_indices={d['packet_indices']}")
        if flow["tcp_flag_counts"] is not None:
            flags = " ".join(f"{flag}={count}" for flag, count in flow["tcp_flag_counts"].items())
            lines.append(f"    TCP flags: {flags}")
    for item in report["diagnostics"]:
        index = f" packet #{item['packet_index']}" if item["packet_index"] is not None else ""
        lines.append(f"{item['code']}{index}: {item['reason']}")
    lines.append("Captured/original lengths describe this recording, not measured link traffic.")
    return "\n".join(lines)
