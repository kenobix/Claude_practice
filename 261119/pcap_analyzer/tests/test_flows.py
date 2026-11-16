"""Flow expectations from fixed wire bytes, independent of the collector."""

import json
import struct
import subprocess
import sys
from ipaddress import IPv4Address

import pytest
from jsonschema import Draft202012Validator, ValidationError

from pcap_analyzer.models import Config
from pcap_analyzer.reader import PcapSource, RawPcapReader
from pcap_analyzer.report import analyze, render_json, render_table
from test_phase1 import ROOT, FIXTURES, SCHEMA, assert_consistent, capture, saved


def frame(*, src="192.0.2.2", dst="192.0.2.10", sport=40000, dport=443,
          protocol=6, flags=2, options=b"", payload=b"", declared_payload=None):
    if protocol == 6:
        transport = struct.pack("!HHIIHHHH", sport, dport, 1, 0,
                                ((5 + len(options) // 4) << 12) | flags, 8192, 0, 0) + options
    else:
        transport = struct.pack("!HHHH", sport, dport, 8 + (len(payload) if declared_payload is None else declared_payload), 0)
    payload_size = len(payload) if declared_payload is None else declared_payload
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(transport) + payload_size,
                     1, 0, 64, protocol, 0, IPv4Address(src).packed, IPv4Address(dst).packed)
    return bytes.fromhex("0200000000020200000000010800") + ip + transport + payload


def report_for(tmp_path, frames, *, wirelens=None, config=None):
    records = [(data, len(data) if wirelens is None else wirelens[i], 1700000000 + i, 0)
               for i, data in enumerate(frames)]
    return analyze(saved(tmp_path, capture(records)), config)


def test_entire_baseline_flow_contract():
    expected = json.loads((ROOT / "expected/baseline.flows.json").read_text())
    report = analyze(FIXTURES / "baseline.pcap")
    assert report == expected
    assert_consistent(report)
    assert report["flows"][0]["tcp_flag_counts"] == {"F": 0, "S": 2, "R": 0, "P": 0, "A": 3, "U": 0, "E": 0, "C": 0, "N": 0}


def test_bidirectional_key_uses_packed_ip_not_display_order(tmp_path):
    forward = frame(flags=2)
    reverse = frame(src="192.0.2.10", dst="192.0.2.2", sport=443, dport=40000, flags=18)
    report = report_for(tmp_path, [reverse, forward, forward])
    assert_consistent(report)
    flow, = report["flows"]
    assert flow["a"] == {"ip": "192.0.2.2", "port": 40000}
    assert flow["b"] == {"ip": "192.0.2.10", "port": 443}
    assert flow["a_to_b"] == {"packets": 2, "captured_bytes": 108, "original_bytes": 108, "packet_indices": [2, 3]}
    assert flow["b_to_a"] == {"packets": 1, "captured_bytes": 54, "original_bytes": 54, "packet_indices": [1]}
    assert flow["tcp_flag_counts"]["S"] == 3
    assert flow["tcp_flag_counts"]["A"] == 1


@pytest.mark.parametrize("change", [{"sport": 40001}, {"dport": 444}, {"protocol": 17}, {"src": "192.0.2.3"}, {"dst": "192.0.2.11"}])
def test_key_separates_each_endpoint_component_and_protocol(tmp_path, change):
    report = report_for(tmp_path, [frame(), frame(**change)])
    assert_consistent(report)
    assert report["summary"]["flow_count"] == 2


@pytest.mark.parametrize("equal_ports", [False, True])
def test_same_ip_port_order_and_identical_endpoints(tmp_path, equal_ports):
    port = 53 if equal_ports else 40000
    packets = [frame(src="192.0.2.2", dst="192.0.2.2", sport=port, dport=53),
               frame(src="192.0.2.2", dst="192.0.2.2", sport=53, dport=port)]
    report = report_for(tmp_path, packets)
    assert_consistent(report)
    flow, = report["flows"]
    assert flow["a"]["port"] == 53
    assert flow["b"]["port"] == port
    assert flow["a_to_b"]["packet_indices"] == ([1, 2] if equal_ports else [2])
    assert flow["b_to_a"]["packet_indices"] == ([] if equal_ports else [1])


@pytest.mark.parametrize("flags", [0, 1, 2, 4, 8, 16, 32, 64, 128, 256, 18, 0x1ff])
def test_tcp_flags_count_each_bit_including_syn_ack_and_ns(tmp_path, flags):
    report = report_for(tmp_path, [frame(flags=flags)])
    assert_consistent(report)
    assert report["flows"][0]["tcp_flag_counts"] == {
        name: int(bool(flags & bit)) for name, bit in
        [("F", 1), ("S", 2), ("R", 4), ("P", 8), ("A", 16),
         ("U", 32), ("E", 64), ("C", 128), ("N", 256)]}


def test_udp_bidirectional_flags_are_null(tmp_path):
    report = report_for(tmp_path, [frame(protocol=17, payload=b"abc"),
                                  frame(src="192.0.2.10", dst="192.0.2.2", sport=443, dport=40000, protocol=17)])
    assert_consistent(report)
    flow, = report["flows"]
    assert flow["tcp_flag_counts"] is None
    assert flow["a_to_b"]["captured_bytes"] == 45
    assert flow["b_to_a"]["captured_bytes"] == 42


@pytest.mark.parametrize("protocol,options", [(6, b""), (6, b"\x01" * 4), (17, b"")])
def test_truncated_payload_with_complete_headers_is_attributed(tmp_path, protocol, options):
    data = frame(protocol=protocol, options=options, declared_payload=10)
    report = report_for(tmp_path, [data], wirelens=[len(data) + 10])
    assert_consistent(report)
    assert report["summary"]["classification_counts"]["truncated"] == 1
    flow, = report["flows"]
    assert flow["a_to_b"]["captured_bytes"] == len(data)
    assert flow["a_to_b"]["original_bytes"] == len(data) + 10
    assert report["summary"]["unattributed_packet_count"] == 0


def test_complete_header_with_only_recorded_wire_truncation_is_attributed(tmp_path):
    report = report_for(tmp_path, [frame()], wirelens=[60])
    assert_consistent(report)
    assert report["summary"]["classification_counts"]["truncated"] == 1
    assert report["flows"][0]["a_to_b"]["original_bytes"] == 60


@pytest.mark.parametrize("kind", ["tcp_short", "tcp_options_short", "udp_short", "ip_short", "tcp_offset_invalid", "udp_length_invalid", "caplen_wirelen", "caplen_snaplen", "fragment", "fragment_truncated", "arp"])
def test_ineligible_packets_are_unattributed(tmp_path, kind):
    data = bytearray(frame())
    wirelen, snaplen = 54, 65535
    if kind == "tcp_short":
        data = data[:40]
    elif kind == "tcp_options_short":
        data[46] = 0x60
        data[16:18] = struct.pack("!H", 44)
        wirelen = 58
    elif kind == "udp_short":
        data = bytearray(frame(protocol=17))[:40]
        wirelen = 42
    elif kind == "ip_short":
        data = data[:25]
    elif kind == "tcp_offset_invalid":
        data[46] = 0x40
    elif kind == "udp_length_invalid":
        data = bytearray(frame(protocol=17))
        data[38:40] = b"\x00\x07"
        wirelen = 42
    elif kind == "caplen_wirelen":
        wirelen = 53
    elif kind == "caplen_snaplen":
        snaplen = 40
    elif kind in ("fragment", "fragment_truncated"):
        data[20:22] = b"\x20\x00"
        wirelen = 60 if kind == "fragment_truncated" else 54
    else:
        data = bytearray((FIXTURES / "unsupported_arp.pcap").read_bytes()[40:])
        wirelen = 42
    report = analyze(saved(tmp_path, capture([(bytes(data), wirelen, 1, 0)], snaplen=snaplen)))
    assert_consistent(report)
    assert report["flows"] == []
    assert report["unattributed_indices"] == [1]
    assert report["summary"]["unattributed_captured_bytes"] == len(data)
    assert report["summary"]["unattributed_original_bytes"] == wirelen


def test_malformed_timestamp_does_not_discard_valid_flow_headers(tmp_path):
    path = saved(tmp_path, capture([(frame(), 54, 1, 1000000)]))
    report = analyze(path)
    assert_consistent(report)
    assert report["summary"]["classification_counts"]["malformed"] == 1
    assert report["summary"]["start_ns"] is None
    assert report["flows"][0]["a_to_b"]["packets"] == 1
    with PcapSource(path, Config()) as source:
        record = next(iter(source))
    assert record.flow_eligible and record.tcp_flags == 2


def test_mixed_packets_and_original_lengths_partition_exactly(tmp_path):
    packets = [frame(), frame(declared_payload=10), frame()[:40],
               (FIXTURES / "unsupported_arp.pcap").read_bytes()[40:]]
    report = report_for(tmp_path, packets, wirelens=[54, 64, 54, 42])
    assert_consistent(report)
    assert report["summary"]["captured_bytes"] == 190
    assert report["summary"]["original_bytes"] == 214
    assert report["flows"][0]["a_to_b"]["packet_indices"] == [1, 2]
    assert report["flows"][0]["a_to_b"]["captured_bytes"] == 108
    assert report["summary"]["unattributed_captured_bytes"] == 82
    assert report["unattributed_indices"] == [3, 4]


def test_flow_limit_counts_trigger_packet_as_unattributed_and_stops(tmp_path, monkeypatch):
    packets = [frame(), frame(sport=40001), frame(sport=40002), frame()]
    calls = []
    original = RawPcapReader._read_packet
    def tracked(self, size):
        calls.append(size)
        return original(self, size)
    monkeypatch.setattr(RawPcapReader, "_read_packet", tracked)
    report = report_for(tmp_path, packets, config=Config(max_flows=2))
    assert_consistent(report)
    assert report["complete"] is False
    assert report["summary"]["flow_count"] == 2
    assert report["summary"]["packet_count"] == 3
    assert report["summary"]["captured_bytes"] == 162
    assert report["unattributed_indices"] == [3]
    assert report["diagnostics"][-1]["code"] == "E_LIMIT_FLOWS"
    assert report["diagnostics"][-1]["packet_index"] == 3
    assert calls == [54, 54, 54]


def test_existing_flow_allowed_at_capacity_and_exact_limit_passes(tmp_path):
    report = report_for(tmp_path, [frame(), frame(sport=40001), frame()], config=Config(max_flows=2))
    assert_consistent(report)
    assert report["complete"] is True
    assert report["flows"][0]["a_to_b"]["packet_indices"] == [1, 3]
    assert Config().max_flows == 100000
    assert Config(max_flows=100000).max_flows == 100000


@pytest.mark.parametrize("value", [0, -1, 100001, True, 1.0])
def test_invalid_flow_limits(value):
    with pytest.raises(ValueError):
        Config(max_flows=value)


def test_diagnostic_limit_preserves_flow_accounting(tmp_path):
    report = report_for(tmp_path, [frame(), frame()], wirelens=[60, 60], config=Config(max_diagnostics=1))
    assert_consistent(report)
    assert report["complete"] is False
    assert report["diagnostics"][-1]["code"] == "E_LIMIT_DIAGNOSTICS"
    assert report["flows"][0]["a_to_b"]["captured_bytes"] == 108


def test_flow_sort_and_same_input_json_are_deterministic(tmp_path):
    path = saved(tmp_path, capture([(data, len(data), i, 0) for i, data in enumerate(
        [frame(protocol=17), frame(sport=40001), frame(), frame(src="192.0.2.10", dst="192.0.2.2", sport=443, dport=40000)])]))
    first = analyze(path)
    assert_consistent(first)
    assert render_json(first) == render_json(analyze(path))
    assert [(f["protocol"], f["a"]["port"]) for f in first["flows"]] == [(6, 40000), (6, 40001), (17, 40000)]


def test_cli_flow_table_json_and_limit():
    base = [sys.executable, "-m", "pcap_analyzer", str(FIXTURES / "baseline.pcap")]
    table = subprocess.run(base, capture_output=True, text=True, timeout=10)
    assert table.returncode == 0
    assert "192.0.2.1:40000 <-> 198.51.100.53:443" in table.stdout
    assert "a_to_b: packets=2 captured=108 original=108" in table.stdout
    assert "S=2" in table.stdout and "A=3" in table.stdout
    limited = subprocess.run([*base, "--format", "json", "--max-flows", "1"], capture_output=True, text=True, timeout=10)
    assert limited.returncode == 2 and "E_LIMIT_FLOWS" in limited.stderr
    report = json.loads(limited.stdout)
    assert_consistent(report)
    assert report["summary"]["packet_count"] == 4
    assert report["summary"]["unattributed_captured_bytes"] == 72
    invalid = subprocess.run([*base, "--max-flows", "100001"], capture_output=True, text=True, timeout=10)
    assert invalid.returncode == 2 and invalid.stdout == ""


@pytest.mark.parametrize("mutation", ["missing_direction", "missing_flag", "udp_flags", "null_flows"])
def test_schema_rejects_incomplete_flow_contract(mutation):
    report = analyze(FIXTURES / "baseline.pcap")
    if mutation == "missing_direction":
        del report["flows"][0]["b_to_a"]
    elif mutation == "missing_flag":
        del report["flows"][0]["tcp_flag_counts"]["S"]
    elif mutation == "udp_flags":
        report["flows"][1]["tcp_flag_counts"] = report["flows"][0]["tcp_flag_counts"]
    else:
        report["flows"] = None
    with pytest.raises(ValidationError):
        Draft202012Validator(SCHEMA).validate(report)
