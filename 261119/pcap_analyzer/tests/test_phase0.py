"""Check the environment and synthetic inputs, not the future analyzer."""

import importlib.util
import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError
from pcap_analyzer.offline import configure_offline

configure_offline()

from scapy.layers.dns import DNS
from scapy.layers.inet import IP, TCP, UDP
from scapy.layers.l2 import Ether
from scapy.utils import RawPcapReader

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = json.loads((ROOT / "expected/fixtures.json").read_text())
spec = importlib.util.spec_from_file_location("fixture_generator", ROOT / "scripts/generate_fixtures.py")
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


@pytest.fixture
def fixtures(tmp_path):
    generator.generate(tmp_path)
    return tmp_path


def test_generation_is_deterministic_and_committed(fixtures, tmp_path):
    second = tmp_path / "second"
    generated = generator.generate(second)
    assert len(generated) == 14
    for name, data in generated.items():
        assert (fixtures / name).read_bytes() == data
        assert (ROOT / "fixtures" / name).read_bytes() == data


def test_fixture_generation_needs_no_sockets(tmp_path):
    code = """
import runpy
import socket
import sys
def deny(*args, **kwargs):
    raise AssertionError('socket creation is forbidden in fixture generation')
socket.socket = deny
sys.argv = [sys.argv[1], '--output', sys.argv[2]]
runpy.run_path(sys.argv[0], run_name='__main__')
"""
    result = subprocess.run([sys.executable, "-c", code,
                             str(ROOT / "scripts/generate_fixtures.py"), str(tmp_path)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert len(list(tmp_path.iterdir())) == 14


def test_baseline_raw_container_matches_hand_calculation(fixtures):
    data = (fixtures / "baseline.pcap").read_bytes()
    expected = EXPECTED["baseline.pcap"]
    assert len(data) == expected["file_bytes"]
    assert struct.unpack("<IHHIIII", data[:24]) == (0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)
    pos = 24
    lengths, offsets, wirelens = [], [], []
    while pos < len(data):
        sec, usec, caplen, wirelen = struct.unpack("<IIII", data[pos:pos+16])
        pos += 16
        assert len(data[pos:pos+caplen]) == caplen
        lengths.append(caplen)
        wirelens.append(wirelen)
        offsets.append((sec - 1700000000) * 1000000 + usec)
        pos += caplen
    assert pos == len(data)
    assert lengths == expected["lengths"]
    assert offsets == expected["offset_us"]
    assert sum(lengths) == expected["captured_bytes"]
    assert sum(wirelens) == expected["original_bytes"]


def test_scapy_reads_baseline_and_dns_evidence(fixtures):
    with RawPcapReader(str(fixtures / "baseline.pcap")) as reader:
        records = list(reader)
        assert not reader.nano
        assert reader.linktype == 1
    assert len(records) == 10
    assert [m.caplen for _, m in records] == EXPECTED["baseline.pcap"]["lengths"]
    assert all(len(data) == m.caplen == m.wirelen for data, m in records)
    packets = [Ether(data) for data, _ in records]
    assert [i + 1 for i, p in enumerate(packets) if TCP in p] == [1, 2, 3, 10]
    assert packets[3][DNS].qd[0].qname == b"example.test."
    assert packets[4][DNS].an[0].rdata == "203.0.113.7"
    assert packets[6][UDP].payload.original == packets[7][UDP].payload.original
    assert [(p[DNS].id, int(p[DNS].qr)) for p in packets if DNS in p] == [(1, 0), (1, 1), (2, 0), (3, 0), (3, 0), (3, 1)]


@pytest.mark.parametrize("name", ["nano.pcap", "big_endian.pcap"])
def test_timestamp_metadata(fixtures, name):
    with RawPcapReader(str(fixtures / name)) as reader:
        _, metadata = next(reader)
        ns = metadata.sec * 1000000000 + metadata.usec * (1 if reader.nano else 1000)
        assert ns == EXPECTED[name]["timestamp_ns"]
        assert ("ns" if reader.nano else "us") == EXPECTED[name]["precision"]


@pytest.mark.parametrize("name", ["capture_truncated.pcap", "unsupported_arp.pcap", "fragment.pcap"])
def test_boundary_fixture_metadata(fixtures, name):
    with RawPcapReader(str(fixtures / name)) as reader:
        records = list(reader)
    assert len(records) == EXPECTED[name]["records"]
    data, metadata = records[0]
    assert len(data) == metadata.caplen == EXPECTED[name]["captured_bytes"]
    if "original_bytes" in EXPECTED[name]:
        assert metadata.wirelen == EXPECTED[name]["original_bytes"]
    if name == "fragment.pcap":
        assert Ether(data)[IP].flags.MF


def test_cutoff_fixtures_encode_actual_short_reads(fixtures):
    data = (fixtures / "record_cutoff.pcap").read_bytes()
    assert struct.unpack("<IIII", data[24:40])[2:] == (54, 54)
    assert len(data[40:]) == 40
    assert len((fixtures / "header_cutoff.pcap").read_bytes()[24:]) == 8
    assert (fixtures / "empty.pcap").read_bytes() == b""
    with RawPcapReader(str(fixtures / "header_only.pcap")) as reader:
        assert list(reader) == []


def test_length_conflict_fixture_encodes_complete_body(fixtures):
    name = "malformed_length.pcap"
    data = (fixtures / name).read_bytes()
    expected = EXPECTED[name]
    assert struct.unpack("<IIII", data[24:40])[2:] == (expected["declared_caplen"], expected["declared_wirelen"])
    assert len(data[40:]) == expected["declared_caplen"]


def test_backward_fixture_preserves_file_order(fixtures):
    with RawPcapReader(str(fixtures / "backward.pcap")) as reader:
        records = list(reader)
    assert records[0][1].usec == 0
    assert records[0][1].sec > records[1][1].sec
    assert sum(len(data) for data, _ in records) == 160


def test_unsupported_container_identity(fixtures):
    assert struct.unpack("<I", (fixtures / "unsupported_linktype.pcap").read_bytes()[20:24])[0] == 101
    assert (fixtures / "unsupported_pcapng.pcapng").read_bytes()[:4] == b"\x0a\x0d\x0d\x0a"


def test_cli_startup_and_baseline_analysis():
    for args in (["--version"], ["--help"], []):
        result = subprocess.run([sys.executable, "-m", "pcap_analyzer", *args], capture_output=True, text=True)
        assert result.returncode == 0
    result = subprocess.run([sys.executable, "-m", "pcap_analyzer", str(ROOT / "fixtures/baseline.pcap")], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stderr == ""
    assert "Captured bytes" in result.stdout
    assert "680" in result.stdout


def test_report_schema_and_manual_example():
    # Keep the original Phase 0 future-report contract under its archived schema.
    schema = json.loads((ROOT / "docs/report.phase0.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    example = json.loads((ROOT / "expected/baseline.report.json").read_text())
    validator = Draft202012Validator(schema)
    validator.validate(example)
    summary = example["summary"]
    assert sum(summary["classification_counts"].values()) == summary["packet_count"] == 10
    assert sum(d["captured_bytes"] for f in example["flows"] for d in [f["a_to_b"], f["b_to_a"]]) == summary["captured_bytes"] == 680
    assert summary["duration_ns"] == summary["end_ns"] - summary["start_ns"] == 8000000000
    for event in example["dns"]["events"]:
        assert all(1 <= i <= 10 for i in event["query_indices"] + event["response_indices"])
    example["dns"]["events"][0]["response_time_ms"] = None
    with pytest.raises(ValidationError):
        validator.validate(example)
