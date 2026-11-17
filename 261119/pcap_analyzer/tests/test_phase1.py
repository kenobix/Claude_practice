"""Phase 1 behavior against hand-defined bytes and independent expected values."""

import hashlib
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from pcap_analyzer.models import Config, InputError
from pcap_analyzer.reader import PcapSource, RawPcapReader
from pcap_analyzer.report import analyze, render_json, render_table

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
EXPECTED = json.loads((ROOT / "expected/fixtures.json").read_text())
SCHEMA = json.loads((ROOT / "docs/report.schema.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA)

# Header-only synthetic wire bytes. Checksums are zero and deliberately unverified.
ETH = bytes.fromhex("0200000000020200000000010800")
TCP = struct.pack("!HHIIBBHHH", 40000, 443, 1, 0, 0x50, 2, 8192, 0, 0)
UDP = struct.pack("!HHHH", 53000, 53, 8, 0)


def ipv4(payload=TCP, proto=6, *, version_ihl=0x45, total=None, frag=0):
    return ETH + struct.pack("!BBHHHBBH4s4s", version_ihl, 0,
                             20 + len(payload) if total is None else total,
                             1, frag, 64, proto, 0,
                             bytes([192, 0, 2, 1]), bytes([198, 51, 100, 53])) + payload


def capture(records, *, endian="<", nano=False, version=(2, 4), snaplen=65535, linktype=1):
    header = struct.pack(endian + "IHHIIII", 0xA1B23C4D if nano else 0xA1B2C3D4,
                         *version, 0, 0, snaplen, linktype)
    return header + b"".join(struct.pack(endian + "IIII", sec, fraction, len(data), wirelen) + data
                              for data, wirelen, sec, fraction in records)


def saved(tmp_path, content):
    p = tmp_path / "input.pcap"
    p.write_bytes(content)
    return p


def one(tmp_path, frame, *, cap_wire=None, fraction=0, **options):
    return saved(tmp_path, capture([(frame, len(frame) if cap_wire is None else cap_wire,
                                     1700000000, fraction)], **options))


def assert_consistent(report):
    VALIDATOR.validate(report)
    summary = report["summary"]
    n = summary["packet_count"]
    assert sum(summary["classification_counts"].values()) == n
    assert sum(summary["protocol_counts"].values()) == n
    refs = report["classification_indices"]
    assert sorted(i for group in refs.values() for i in group) == list(range(1, n + 1))
    assert all(len(refs[key]) == value for key, value in summary["classification_counts"].items())
    assert all(d["packet_index"] is None or 1 <= d["packet_index"] <= n + 1 for d in report["diagnostics"])
    directions = [flow[key] for flow in report["flows"] for key in ("a_to_b", "b_to_a")]
    assert summary["flow_count"] == len(report["flows"])
    assert sum(d["packets"] for d in directions) + summary["unattributed_packet_count"] == n
    assert sum(d["captured_bytes"] for d in directions) + summary["unattributed_captured_bytes"] == summary["captured_bytes"]
    assert sum(d["original_bytes"] for d in directions) + summary["unattributed_original_bytes"] == summary["original_bytes"]
    assert all(d["packets"] == len(d["packet_indices"]) for d in directions)
    assert len(report["unattributed_indices"]) == summary["unattributed_packet_count"]
    flow_refs = [i for d in directions for i in d["packet_indices"]]
    assert sorted(flow_refs + report["unattributed_indices"]) == list(range(1, n + 1))
    assert report["dns"]["status"] == "not_implemented"


def test_active_schema_and_entire_baseline_contract():
    Draft202012Validator.check_schema(SCHEMA)
    expected = json.loads((ROOT / "expected/baseline.phase1.json").read_text())
    result = analyze(FIXTURES / "baseline.pcap")
    # Preserve every Phase 1 metric while allowing the versioned flow extension.
    assert {key: result["summary"][key] for key in expected["summary"]} == expected["summary"]
    for key in ("input", "classification_indices", "complete", "dns", "diagnostics"):
        assert result[key] == expected[key]
    assert_consistent(result)
    assert result["input"]["sha256"] == hashlib.sha256((FIXTURES / "baseline.pcap").read_bytes()).hexdigest()
    assert "680" in render_table(result)


@pytest.mark.parametrize("name", [p.name for p in sorted(FIXTURES.iterdir())])
def test_all_phase0_fixture_expectations(name):
    expected = EXPECTED[name]
    if name in ("empty.pcap", "unsupported_linktype.pcap", "unsupported_pcapng.pcapng"):
        with pytest.raises(InputError) as error:
            analyze(FIXTURES / name)
        assert error.value.code == expected["error"]
        return
    report = analyze(FIXTURES / name)
    assert_consistent(report)
    summary = report["summary"]
    assert summary["packet_count"] == expected["records"]
    for key in ("captured_bytes", "original_bytes"):
        if key in expected:
            assert summary[key] == expected[key]
    if "classification" in expected:
        assert summary["classification_counts"][expected["classification"]] == 1
    if "timestamp_ns" in expected:
        assert summary["start_ns"] == summary["end_ns"] == expected["timestamp_ns"]
        assert report["input"]["timestamp_precision"] == expected["precision"]
    if "error" in expected:
        assert report["complete"] is False
        assert report["diagnostics"][-1]["code"] == expected["error"]
        assert report["diagnostics"][-1]["packet_index"] == expected["packet_index"]
    else:
        assert report["complete"] is True
    if name == "fragment.pcap":
        assert summary["ipv4_fragment_count"] == 1
        assert summary["protocol_counts"]["udp"] == 0
    if name == "backward.pcap":
        assert report["dns"]["time_analysis"] == expected["dns_time_analysis"]
        assert summary["start_ns"] == 1700000000900000000
        assert summary["end_ns"] == 1700000001000000000
        assert summary["duration_ns"] == 100000000


@pytest.mark.parametrize("endian,nano", [("<", False), (">", False), ("<", True), (">", True)])
def test_all_magic_formats_integer_time(tmp_path, endian, nano):
    report = analyze(one(tmp_path, ipv4(), fraction=123, endian=endian, nano=nano))
    assert_consistent(report)
    assert report["summary"]["start_ns"] == 1700000000000000000 + (123 if nano else 123000)


@pytest.mark.parametrize("content,code", [
    (b"", "E_FORMAT"), (b"\x00" * 24, "E_FORMAT"),
    (capture([])[:23], "E_FORMAT"),
    (capture([], version=(2, 3)), "E_FORMAT"),
    (capture([], snaplen=0), "E_FORMAT"),
    (capture([], linktype=113), "E_LINKTYPE"),
    (capture([], linktype=0x04000001), "E_LINKTYPE"),
    (b"\x1f\x8b" + b"\x00" * 22, "E_FORMAT_UNSUPPORTED"),
])
def test_global_header_rejection(tmp_path, content, code):
    with pytest.raises(InputError) as error:
        analyze(saved(tmp_path, content))
    assert error.value.code == code


@pytest.mark.parametrize("frame,wirelen,snaplen,state,protocol", [
    (ipv4(), 54, 65535, "parsed", "tcp"),
    (ipv4(UDP, 17), 42, 65535, "malformed", "udp"),  # Empty port-53 DNS body.
    (ipv4() + b"\x00" * 6, 60, 65535, "parsed", "tcp"),
    (ipv4()[:40], 54, 65535, "truncated", "tcp"),
    (ipv4() + b"\x00", 54, 65535, "malformed", "tcp"),
    (ipv4(), 54, 40, "malformed", "tcp"),
    (b"", 0, 65535, "truncated", "unknown"),
    (ETH[:13], 13, 65535, "truncated", "unknown"),
    (ETH + b"\x45" * 5, 19, 65535, "truncated", "unknown"),
    (ipv4(version_ihl=0x44), 54, 65535, "malformed", "unknown"),
    (ipv4(version_ihl=0x65), 54, 65535, "malformed", "unknown"),
    (ipv4(total=10), 54, 65535, "malformed", "unknown"),
    (ipv4(total=100), 54, 65535, "truncated", "tcp"),
    (ipv4(TCP[:10], total=30), 44, 65535, "malformed", "tcp"),
    (ipv4(TCP[:12] + b"\x40" + TCP[13:]), 54, 65535, "malformed", "tcp"),
    (ipv4(TCP[:12] + b"\x60" + TCP[13:]), 54, 65535, "malformed", "tcp"),
    (ipv4(TCP[:12] + b"\x60" + TCP[13:], total=44), 54, 65535, "truncated", "tcp"),
    (ipv4(UDP[:4] + b"\x00\x07" + UDP[6:], 17), 42, 65535, "malformed", "udp"),
    (ipv4(UDP[:4] + b"\x00\x09" + UDP[6:], 17), 42, 65535, "malformed", "udp"),
    (ipv4(b"abcd", 17), 38, 65535, "malformed", "udp"),
    (ipv4(b"abcd", 1), 38, 65535, "unsupported", "ipv4_other"),
    (ipv4(UDP, 17, frag=0x2000), 42, 65535, "unsupported", "ipv4_fragment"),
    (ipv4(b"abcd", 17, frag=1), 38, 65535, "unsupported", "ipv4_fragment"),
    (ETH[:12] + b"\x86\xdd" + b"\x00" * 40, 54, 65535, "unsupported", "non_ipv4"),
    (ETH[:12] + b"\x81\x00" + b"\x00" * 40, 54, 65535, "unsupported", "non_ipv4"),
    (ETH[:12] + b"\x08\x06", 24, 65535, "truncated", "non_ipv4"),
    (ipv4(version_ihl=0x44)[:25], 54, 65535, "malformed", "unknown"),
])
def test_header_classification_and_precedence(tmp_path, frame, wirelen, snaplen, state, protocol):
    report = analyze(one(tmp_path, frame, cap_wire=wirelen, snaplen=snaplen))
    assert_consistent(report)
    assert report["summary"]["classification_counts"][state] == 1
    assert report["summary"]["protocol_counts"][protocol] == 1
    assert report["summary"]["captured_bytes"] == len(frame)
    assert report["summary"]["original_bytes"] == wirelen


def test_options_and_endpoints_in_packet_record(tmp_path):
    tcp = TCP[:12] + b"\x60" + TCP[13:] + b"\x01" * 4
    frame = ipv4(b"\x01" * 4 + tcp, version_ihl=0x46)
    with PcapSource(one(tmp_path, frame), Config()) as source:
        record = next(iter(source))
    assert record.classification == "parsed"
    assert (record.packet_index, record.timestamp_ns, record.captured_len, record.original_len) == (1, 1700000000000000000, 62, 62)
    assert (record.src_ip, record.dst_ip, record.src_port, record.dst_port, record.ip_protocol) == ("192.0.2.1", "198.51.100.53", 40000, 443, 6)


@pytest.mark.parametrize("nano,fraction", [(False, 1000000), (True, 1000000000)])
def test_invalid_timestamp_is_malformed_and_excluded_from_range(tmp_path, nano, fraction):
    report = analyze(one(tmp_path, ipv4(), nano=nano, fraction=fraction))
    assert_consistent(report)
    summary = report["summary"]
    assert summary["classification_counts"]["malformed"] == 1
    assert summary["timestamp_invalid_count"] == 1
    assert summary["start_ns"] is summary["end_ns"] is summary["duration_ns"] is None


def test_record_limit_stops_before_scapy_reads_next_record(monkeypatch):
    calls = []
    original = RawPcapReader._read_packet
    def tracked(self, size):
        calls.append(size)
        return original(self, size)
    monkeypatch.setattr(RawPcapReader, "_read_packet", tracked)
    report = analyze(FIXTURES / "baseline.pcap", Config(max_records=1))
    assert_consistent(report)
    assert not report["complete"]
    assert report["summary"]["packet_count"] == 1
    assert report["summary"]["captured_bytes"] == 54
    assert report["diagnostics"][-1]["code"] == "E_LIMIT_RECORDS"
    assert calls == [54]
    assert analyze(FIXTURES / "baseline.pcap", Config(max_records=10))["complete"]


def test_byte_limit_rejects_before_raw_reader_and_exact_size_passes(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("must reject size before RawPcapReader")
    with monkeypatch.context() as context:
        context.setattr("pcap_analyzer.reader.RawPcapReader", forbidden)
        with pytest.raises(InputError) as error:
            analyze(FIXTURES / "baseline.pcap", Config(max_input_bytes=863))
        assert error.value.code == "E_LIMIT_BYTES"
    assert analyze(FIXTURES / "baseline.pcap", Config(max_input_bytes=864))["complete"]


def test_packet_limit_and_large_record_not_sliced_at_scapy_mtu(tmp_path):
    frame = ipv4() + b"\x00" * 70000
    path = one(tmp_path, frame, snaplen=100000)
    report = analyze(path)
    assert_consistent(report)
    assert report["summary"]["captured_bytes"] == 70054
    assert report["summary"]["classification_counts"]["parsed"] == 1
    stopped = analyze(path, Config(max_packet_bytes=70053))
    assert_consistent(stopped)
    assert stopped["summary"]["packet_count"] == 0
    assert stopped["diagnostics"][-1]["code"] == "E_LIMIT_PACKET_BYTES"
    assert analyze(path, Config(max_packet_bytes=70054))["complete"]


def test_huge_declared_caplen_rejected_without_allocation(tmp_path, monkeypatch):
    path = saved(tmp_path, capture([]) + struct.pack("<IIII", 1, 0, 0xffffffff, 0xffffffff))
    def forbidden(*args, **kwargs):
        raise AssertionError("must validate declared length before allocation")
    monkeypatch.setattr(RawPcapReader, "_read_packet", forbidden)
    report = analyze(path)
    assert_consistent(report)
    assert report["summary"]["packet_count"] == 0
    assert report["diagnostics"][0]["code"] == "E_RECORD_DATA"


def test_partial_tail_retains_only_complete_records(tmp_path):
    valid = capture([(ipv4(), 54, 1, 0)])
    report = analyze(saved(tmp_path, valid + struct.pack("<IIII", 2, 0, 54, 54) + ipv4()[:40]))
    assert_consistent(report)
    assert not report["complete"]
    assert report["summary"]["packet_count"] == 1
    assert report["summary"]["captured_bytes"] == 54
    assert report["diagnostics"][0]["packet_index"] == 2
    assert "caplen=54" in report["diagnostics"][0]["reason"]
    assert "40 bytes" in report["diagnostics"][0]["reason"]


def test_diagnostic_limit_is_explicit_and_bounded(tmp_path):
    data = capture([(b"", 0, i, 0) for i in range(10)])
    report = analyze(saved(tmp_path, data), Config(max_diagnostics=1))
    assert_consistent(report)
    assert not report["complete"]
    assert report["summary"]["packet_count"] == 2
    assert len(report["diagnostics"]) == 2  # one normal diagnostic + terminal error
    assert report["diagnostics"][-1]["code"] == "E_LIMIT_DIAGNOSTICS"


@pytest.mark.parametrize("settings", [
    {"max_records": 0}, {"max_records": 1000001}, {"max_records": True},
    {"max_input_bytes": -1}, {"max_input_bytes": 104857601},
    {"max_packet_bytes": 1048577}, {"max_diagnostics": 10001},
])
def test_invalid_limits(settings):
    with pytest.raises(ValueError):
        Config(**settings)


def test_same_input_determinism_and_additive_counts(tmp_path):
    first = render_json(analyze(FIXTURES / "baseline.pcap"))
    assert first == render_json(analyze(FIXTURES / "baseline.pcap"))
    records = [(ipv4(), 54, 1, 0)]
    a = analyze(saved(tmp_path, capture(records)))
    b = analyze(saved(tmp_path, capture(records + [(ipv4(UDP, 17), 42, 1, 0)])))
    assert b["summary"]["packet_count"] - a["summary"]["packet_count"] == 1
    assert b["summary"]["captured_bytes"] - a["summary"]["captured_bytes"] == 42
    assert b["summary"]["original_bytes"] - a["summary"]["original_bytes"] == 42


@pytest.mark.parametrize("name,exit_code,has_json,code", [
    ("baseline.pcap", 0, True, None), ("header_only.pcap", 0, True, None),
    ("empty.pcap", 2, False, "E_FORMAT"),
    ("record_cutoff.pcap", 2, True, "E_RECORD_DATA"),
    ("header_cutoff.pcap", 2, True, "E_RECORD_HEADER"),
    ("unsupported_linktype.pcap", 2, False, "E_LINKTYPE"),
    ("unsupported_pcapng.pcapng", 2, False, "E_FORMAT_UNSUPPORTED"),
    ("capture_truncated.pcap", 0, True, None),
    ("malformed_length.pcap", 0, True, None),
])
def test_cli_exit_codes_json_and_stderr(name, exit_code, has_json, code):
    result = subprocess.run([sys.executable, "-m", "pcap_analyzer", str(FIXTURES / name), "--format", "json"],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == exit_code
    if has_json:
        report = json.loads(result.stdout)
        assert_consistent(report)
        assert report["complete"] == (exit_code == 0)
    else:
        assert result.stdout == ""
    if code:
        assert code in result.stderr
    else:
        assert result.stderr == ""


def test_cli_limits_and_invalid_option():
    command = [sys.executable, "-m", "pcap_analyzer", str(FIXTURES / "baseline.pcap"), "--format", "json", "--max-records"]
    limited = subprocess.run([*command, "1"], capture_output=True, text=True, timeout=10)
    assert limited.returncode == 2
    assert_consistent(json.loads(limited.stdout))
    assert "E_LIMIT_RECORDS" in limited.stderr
    invalid = subprocess.run([*command, "0"], capture_output=True, text=True, timeout=10)
    assert invalid.returncode == 2 and invalid.stdout == ""


def test_file_only_analysis_with_socket_creation_forbidden():
    code = """
import socket, sys
def deny(*args, **kwargs):
    raise AssertionError('analysis must not create sockets')
socket.socket = deny
from pcap_analyzer.report import analyze
assert analyze(sys.argv[1])['summary']['packet_count'] == 10
"""
    result = subprocess.run([sys.executable, "-c", code, str(FIXTURES / "baseline.pcap")], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_nonregular_missing_input_and_readonly_unchanged(tmp_path):
    for path in (tmp_path, tmp_path / "missing"):
        with pytest.raises(InputError) as error:
            analyze(path)
        assert error.value.code == "E_INPUT"
    path = one(tmp_path, ipv4())
    original = path.read_bytes()
    path.chmod(0o444)
    assert analyze(path)["complete"]
    assert path.read_bytes() == original
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    result = subprocess.run([sys.executable, "-m", "pcap_analyzer", str(fifo), "--format", "json"], capture_output=True, text=True, timeout=10)
    assert result.returncode == 2 and result.stdout == ""
    assert "E_INPUT" in result.stderr


def test_changed_input_is_detected(tmp_path):
    path = one(tmp_path, ipv4())
    with PcapSource(path, Config()) as source:
        records = iter(source)
        next(records)
        with path.open("ab") as stream:
            stream.write(b"x")
        with pytest.raises(InputError) as error:
            next(records)
        assert error.value.code == "E_INPUT_CHANGED"


def test_schema_rejects_unimplemented_results_and_unknown_fields():
    example = analyze(FIXTURES / "baseline.pcap")
    example["flows"] = None
    with pytest.raises(ValidationError):
        VALIDATOR.validate(example)
    example["flows"] = []
    example["summary"]["invented_metric"] = 0
    with pytest.raises(ValidationError):
        VALIDATOR.validate(example)
