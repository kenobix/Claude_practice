"""DNS classification integration using hand-built wire bytes and expectations."""

import json
import struct
import subprocess
import sys

import pytest

from pcap_analyzer.models import Config
from pcap_analyzer.packet import decode
from pcap_analyzer.report import analyze, render_json, render_table
from test_flows import frame
from test_phase1 import assert_consistent, capture, saved


QUESTION = b"\x01x\x00\x00\x01\x00\x01"


def message(*, flags=0x0100, qd=1, an=0, body=QUESTION):
    return struct.pack("!6H", 1234, flags, qd, an, 0, 0) + body


@pytest.mark.parametrize("sport,dport", [(53000, 53), (53, 53000), (53, 53)])
@pytest.mark.parametrize("flags", [0x0100, 0x8180, 0x8183, 0x8380])
def test_valid_dns_flags_and_both_port_directions(tmp_path, sport, dport, flags):
    data = frame(protocol=17, sport=sport, dport=dport, payload=message(flags=flags))
    path = saved(tmp_path, capture([(data, len(data), 1700000000, 123)]))
    result = analyze(path)
    assert_consistent(result)
    assert result["classification_indices"]["parsed"] == [1]
    assert result["diagnostics"] == []
    assert result["summary"]["start_ns"] == 1700000000000123000
    # A single packet cannot establish a matched pair, including NXDOMAIN/TC.
    eligible = sport == 53 if flags & 0x8000 else dport == 53
    events = result["dns"]["events"]
    assert len(events) == int(eligible)
    if events:
        assert events[0]["status"] == ("unmatched_response" if flags & 0x8000 else "window_incomplete")
        assert events[0]["response_time_ns"] is None


@pytest.mark.parametrize("payload,state,reason", [
    (b"", "malformed", "dns_header_incomplete"),
    (message(body=b"\x01"), "malformed", "dns_label_incomplete"),
    (message(an=1), "malformed", "dns_name_offset_invalid"),
    (message(an=1, body=QUESTION + b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) + b"abc"),
     "malformed", "dns_rdata_incomplete"),
    (message(body=QUESTION + b"x"), "malformed", "dns_trailing_bytes"),
    (message(flags=0x0900), "unsupported", "dns_opcode_unsupported"),
    (message(qd=0), "unsupported", "dns_question_count_unsupported"),
    (message(qd=2), "unsupported", "dns_question_count_unsupported"),
    (message(body=b"\x40"), "unsupported", "dns_label_type_unsupported"),
    (message(an=1025), "unsupported", "dns_record_work_limit"),
    (message(an=1, body=QUESTION + b"\xc0\x0d"), "unsupported", "dns_pointer_target_unverified"),
])
def test_dns_failure_preserves_flow_and_safe_diagnostics(tmp_path, payload, state, reason):
    data = frame(protocol=17, dport=53, payload=payload)
    path = saved(tmp_path, capture([(data, len(data), 1700000000, 0)]))
    result = analyze(path)
    assert_consistent(result)
    assert result["complete"] is True
    assert result["classification_indices"][state] == [1]
    assert result["diagnostics"] == [{"code": "P_" + state.upper(), "reason": reason, "packet_index": 1}]
    assert result["summary"]["protocol_counts"]["udp"] == 1
    assert result["summary"]["unattributed_packet_count"] == 0
    assert result["flows"][0]["a_to_b"]["captured_bytes"] == len(data)
    assert render_json(result) == render_json(analyze(path))


@pytest.mark.parametrize("case,state,eligible", [
    ("other_port", "parsed", True), ("tcp", "parsed", True),
    ("fragment", "unsupported", False), ("short_payload", "truncated", True),
    ("short_tail", "truncated", True), ("invalid_time", "malformed", True),
    ("snaplen", "malformed", False),
    ("udp_length", "malformed", False), ("short_header", "truncated", False),
])
def test_non_candidates_never_enter_dns_decoder(monkeypatch, case, state, eligible):
    def forbidden(_):
        raise AssertionError("non-candidate entered DNS structure decoder")
    monkeypatch.setattr("pcap_analyzer.packet.parse_message", forbidden)
    data = frame(protocol=17, dport=53, payload=b"bad")
    wirelen, snaplen, timestamp = len(data), 65535, 1700000000000000000
    if case == "other_port":
        data = frame(protocol=17, dport=5353, payload=b"bad")
        wirelen = len(data)
    elif case == "tcp":
        data = frame(dport=53, payload=b"bad")
        wirelen = len(data)
    elif case == "fragment":
        data = data[:20] + b"\x20\x00" + data[22:]
    elif case == "short_payload":
        data = data[:-1]
    elif case == "short_tail":
        wirelen += 1
    elif case == "invalid_time":
        timestamp = None
    elif case == "snaplen":
        snaplen = len(data) - 1
    elif case == "udp_length":
        data = data[:38] + b"\x00\x08" + data[40:]
    elif case == "short_header":
        data = data[:38]
    record = decode(data, index=1, timestamp_ns=timestamp, caplen=len(data),
                    wirelen=wirelen, snaplen=snaplen)
    assert record.classification == state
    assert record.flow_eligible is eligible


def test_padding_outside_udp_is_not_dns_trailing_bytes(tmp_path):
    data = frame(protocol=17, dport=53, payload=message()) + b"padding"
    result = analyze(saved(tmp_path, capture([(data, len(data), 1, 0)])))
    assert_consistent(result)
    assert result["classification_indices"]["parsed"] == [1]
    assert result["summary"]["captured_bytes"] == 68  # 42 + 19 DNS + 7 padding.


def test_compressed_records_in_all_sections_enter_packet_decoder(tmp_path):
    # Owner pointers refer to the question's validated label at offset 12.
    rr = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) + b"\xcb\x00\x71\x07"
    dns = struct.pack("!6H", 1234, 0x8180, 1, 1, 1, 1) + QUESTION + rr * 3
    data = frame(protocol=17, sport=53, dport=53000, payload=dns)
    result = analyze(saved(tmp_path, capture([(data, len(data), 1, 0)])))
    assert_consistent(result)
    assert result["classification_indices"]["parsed"] == [1]
    assert result["diagnostics"] == []
    assert result["summary"]["captured_bytes"] == 109  # 42 + 19 + 3*16.


def test_mixed_dns_cli_counts_limits_and_determinism(tmp_path):
    packets = [frame(protocol=17, dport=53, payload=payload) for payload in
               (message(), b"bad", message(qd=0))]
    path = saved(tmp_path, capture([(p, len(p), 1700000000, i) for i, p in enumerate(packets)]))
    result = analyze(path)
    assert_consistent(result)
    assert result["summary"]["classification_counts"] == {"parsed": 1, "malformed": 1, "unsupported": 1, "truncated": 0}
    assert result["summary"]["packet_count"] == 3
    assert result["summary"]["captured_bytes"] == result["summary"]["original_bytes"] == 167  # 61+45+61.
    assert result["flows"][0]["a_to_b"]["packet_indices"] == [1, 2, 3]
    assert result["summary"]["unattributed_captured_bytes"] == 0
    assert "DNS structure" in render_table(result)
    assert "dns_header_incomplete" in render_table(result)
    command = [sys.executable, "-m", "pcap_analyzer", str(path), "--format", "json"]
    first = subprocess.run(command, capture_output=True, text=True)
    second = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == second.returncode == 0
    assert first.stderr == second.stderr == ""
    assert first.stdout == second.stdout == render_json(result) + "\n"
    assert json.loads(first.stdout) == result
    stopped = analyze(path, Config(max_diagnostics=1))
    assert_consistent(stopped)
    assert stopped["complete"] is False
    assert stopped["summary"]["packet_count"] == 3
    assert stopped["diagnostics"][-1]["code"] == "E_LIMIT_DIAGNOSTICS"
    assert stopped["summary"]["captured_bytes"] == 167
    limited = subprocess.run(command + ["--max-diagnostics", "1"], capture_output=True, text=True)
    assert limited.returncode == 2
    assert "E_LIMIT_DIAGNOSTICS" in limited.stderr
    assert json.loads(limited.stdout) == stopped
