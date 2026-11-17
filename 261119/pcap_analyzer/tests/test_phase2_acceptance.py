"""Phase 2 CLI acceptance: independent counts and adversarial matching cases."""

import json
import struct
import subprocess
import sys

import pytest

from test_dns_report import check_dns_partition
from test_flows import frame
from test_phase1 import ROOT, FIXTURES, assert_consistent, capture

MATRIX = json.loads((ROOT / "expected/phase2.fixtures.json").read_text())


def cli(path, *args):
    return subprocess.run([sys.executable, "-m", "pcap_analyzer", str(path), *args],
                          capture_output=True, text=True, timeout=10)


def read_report(path):
    result = cli(path, "--format", "json")
    assert result.returncode == 0 and result.stderr == ""
    report = json.loads(result.stdout)
    assert_consistent(report)
    check_dns_partition(report)
    return report


@pytest.mark.parametrize("name", sorted(MATRIX))
def test_committed_fixture_cli_phase2_contract(name):
    assert set(MATRIX) == {p.name for p in FIXTURES.iterdir()}
    expected = MATRIX[name]
    result = cli(FIXTURES / name, "--format", "json")
    assert result.returncode == expected["exit"]
    if "error" in expected:
        assert result.stderr.startswith(expected["error"] + ":")
    else:
        assert result.stderr == ""
    if "flows" not in expected:
        assert result.stdout == ""
        return
    report = json.loads(result.stdout)
    assert_consistent(report)
    check_dns_partition(report)
    summary, dns = report["summary"], report["dns"]
    assert report["complete"] is (expected["exit"] == 0)
    assert summary["flow_count"] == expected["flows"]
    assert [summary["unattributed_" + key] for key in
            ("packet_count", "captured_bytes", "original_bytes")] == expected["unattributed"]
    assert (dns["query_count"], dns["response_count"]) == (expected["queries"], expected["responses"])
    if expected.get("disabled"):
        assert dns["time_analysis"] == "disabled_timestamp_regression"
    elif name == "baseline.pcap":
        assert dns == json.loads((ROOT / "expected/baseline.dns.json").read_text())
    else:
        assert dns["time_analysis"] == "enabled" and dns["events"] == []
    if expected.get("error"):
        assert report["diagnostics"][-1]["code"] == expected["error"]


def dns_packet(*, response=False, port=53000, label=b"x", qtype=1, qclass=1):
    # DNS ID 7 is intentionally shared by ALL cases; no Scapy generator or
    # analyzer key/decoder is used to produce the wire question or expectations.
    payload = struct.pack("!6H", 7, 0x8180 if response else 0x0100, 1, 0, 0, 0)
    payload += bytes([len(label)]) + label + b"\x00" + struct.pack("!HH", qtype, qclass)
    options = dict(protocol=17, sport=port, dport=53, payload=payload)
    if response:
        options.update(src="192.0.2.10", dst="192.0.2.2", sport=53, dport=port)
    return frame(**options)


def save_rows(tmp_path, rows):
    # ns offsets, captured frame, recorded wire length; fixed documentation IPs.
    records = [(data, wirelen, 1700000000 + ns // 1_000_000_000, ns % 1_000_000_000)
               for ns, data, wirelen in rows]
    path = tmp_path / "acceptance.pcap"
    path.write_bytes(capture(records, nano=True))
    return path


def test_same_id_port_name_type_class_collisions_and_safe_display(tmp_path):
    cases = [{"label": b"X"}, {"port": 53001}, {"label": b"y"},
             {"qtype": 28}, {"qclass": 3}, {"label": b"A.\x1b\\"}]
    packets = [dns_packet(**case) for case in cases]
    packets += [dns_packet(response=True, **{**case, "label": case.get("label", b"x").lower()})
                for case in reversed(cases)]
    path = save_rows(tmp_path, [(i, packet, len(packet)) for i, packet in enumerate(packets)])
    result = read_report(path)
    events = result["dns"]["events"]
    assert result["summary"]["captured_bytes"] == result["summary"]["original_bytes"] == 738
    assert result["summary"]["flow_count"] == 2
    assert result["dns"]["query_count"] == result["dns"]["response_count"] == 6
    assert [(e["status"], e["query_indices"], e["response_indices"], e["response_time_ns"])
            for e in events] == [("matched", [1], [12], 11), ("matched", [2], [11], 9),
                                 ("matched", [3], [10], 7), ("matched", [4], [9], 5),
                                 ("matched", [5], [8], 3), ("matched", [6], [7], 1)]
    assert [(f["a_to_b"]["captured_bytes"], f["b_to_a"]["captured_bytes"])
            for f in result["flows"]] == [(308, 308), (61, 61)]
    assert events[-1]["key"]["labels_hex"] == ["612e1b5c", ""]
    assert events[-1]["key"]["name"] == r"a\046\027\092."
    table = cli(path)
    assert table.returncode == 0 and r"a\046\027\092." in table.stdout
    assert "\x1b" not in table.stdout
    first, second = cli(path, "--format", "json"), cli(path, "--format", "json")
    assert first.returncode == second.returncode == 0
    assert first.stderr == second.stderr == "" and first.stdout == second.stdout


def test_orphan_fragment_truncation_late_response_and_reuse_do_not_false_match(tmp_path):
    query, response = dns_packet(), dns_packet(response=True)
    fragment = response[:20] + b"\x20\x00" + response[22:]
    malformed = frame(protocol=17, src="192.0.2.10", dst="192.0.2.2", sport=53, dport=53000)
    syn = frame(flags=2)
    synack = frame(src="192.0.2.10", dst="192.0.2.2", sport=443, dport=40000, flags=18)
    rows = [(0, response, 61), (1, query, 61), (2, fragment, 61),
            (3, response[:-1], 61), (4, malformed, 42), (6_000_000_000, syn, 54),
            (6_000_000_001, response, 61), (6_000_000_002, query, 61),
            (6_000_000_003, response, 61), (7_000_000_000, synack, 54)]
    result = read_report(save_rows(tmp_path, rows))
    summary = result["summary"]
    assert (summary["packet_count"], summary["captured_bytes"], summary["original_bytes"]) == (10, 576, 577)
    assert result["classification_indices"] == {"parsed": [1, 2, 6, 7, 8, 9, 10],
                                                "unsupported": [3], "truncated": [4], "malformed": [5]}
    assert result["unattributed_indices"] == [3]
    assert (summary["unattributed_captured_bytes"], summary["unattributed_original_bytes"]) == (61, 61)
    tcp, udp = result["flows"]
    assert (tcp["a_to_b"]["packet_indices"], tcp["b_to_a"]["packet_indices"]) == ([6], [10])
    assert (tcp["tcp_flag_counts"]["S"], tcp["tcp_flag_counts"]["A"]) == (2, 1)
    assert (udp["a_to_b"]["captured_bytes"], udp["b_to_a"]["captured_bytes"],
            udp["b_to_a"]["original_bytes"]) == (122, 285, 286)
    assert result["dns"]["query_count"] == 2 and result["dns"]["response_count"] == 3
    assert [(e["status"], e["query_indices"], e["response_indices"], e["response_time_ns"])
            for e in result["dns"]["events"]] == [
                ("unmatched_response", [], [1], None), ("no_response_observed", [2], [], None),
                ("unmatched_response", [], [7], None), ("matched", [8], [9], 1)]
