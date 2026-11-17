"""Report integration against hand-built records and independent expectations."""

import copy
import json
import subprocess
import sys

import pytest
from jsonschema import ValidationError

from pcap_analyzer.models import Config
from pcap_analyzer.report import analyze, render_json, render_table
from test_dns_packets import message
from test_flows import frame
from test_phase1 import ROOT, FIXTURES, VALIDATOR, assert_consistent, capture, saved


def dns_frame(*, response=False, sport=53000, flags=None, payload=None):
    args = dict(protocol=17, sport=sport, dport=53,
                payload=message(flags=flags if flags is not None else (0x8180 if response else 0x0100))
                if payload is None else payload)
    if response:
        args.update(src="192.0.2.10", dst="192.0.2.2", sport=53, dport=sport)
    return frame(**args)


def report_at(tmp_path, rows, config=None, tail=b""):
    records = [(data, len(data), 1700000000 + ns // 1_000_000_000, ns % 1_000_000_000)
               for ns, data in rows]
    report = analyze(saved(tmp_path, capture(records, nano=True) + tail), config)
    assert_consistent(report)
    check_dns_partition(report)
    return report


def check_dns_partition(report):
    dns = report["dns"]
    if dns["time_analysis"] != "enabled":
        assert dns["events"] == [] and dns["event_counts"] is None
        return
    events = dns["events"]
    assert sum(dns["event_counts"].values()) == len(events)
    assert all(dns["event_counts"][state] == sum(e["status"] == state for e in events)
               for state in dns["event_counts"])
    queries = [i for e in events for i in e["query_indices"]]
    responses = [i for e in events for i in e["response_indices"]]
    refs = queries + responses + dns["unassigned_indices"]
    assert len(refs) == len(set(refs)) == dns["query_count"] + dns["response_count"]
    assert all(1 <= i <= report["summary"]["packet_count"] for i in refs)
    groups = {e["query_group"]: e for e in events if e["query_indices"]}
    for event in events:
        if event["status"] == "additional_response":
            assert event["query_indices"] == []
            assert groups[event["query_group"]]["key"] == event["key"]


def test_baseline_dns_matches_independent_expected_json():
    result = analyze(FIXTURES / "baseline.pcap")
    assert result["dns"] == json.loads((ROOT / "expected/baseline.dns.json").read_text())
    assert_consistent(result)
    check_dns_partition(result)
    assert render_json(result) == render_json(analyze(FIXTURES / "baseline.pcap"))
    assert result["schema_version"] == "0.4.0" and result["phase"] == "2b"
    assert result["config"]["max_dns_keys"] == 100000
    assert "response_time_ms=25.0" in render_table(result)


@pytest.mark.parametrize("offset,statuses", [(5_000_000_000, ["matched"]),
                                             (5_000_000_001, ["no_response_observed", "unmatched_response"])])
def test_closed_boundary_integer_ns_and_negative_response(tmp_path, offset, statuses):
    result = report_at(tmp_path, [(0, dns_frame()), (offset, dns_frame(response=True, flags=0x8383))])
    events = result["dns"]["events"]
    assert [e["status"] for e in events] == statuses
    assert (events[-1]["rcode"], events[-1]["tc"]) == (3, True)
    if statuses == ["matched"]:
        assert events[0]["response_time_ns"] == 5_000_000_000
        assert events[0]["response_time_ms"] == 5000


@pytest.mark.parametrize("end,status", [(4_999_999_999, "window_incomplete"),
                                        (5_000_000_000, "no_response_observed")])
def test_non_dns_capture_end_controls_no_response_status(tmp_path, end, status):
    result = report_at(tmp_path, [(0, dns_frame()), (end, frame())])
    assert result["dns"]["events"][0]["status"] == status


def test_ns_precision_and_additional_response_group_reference(tmp_path):
    result = report_at(tmp_path, [(0, dns_frame()), (1, dns_frame(response=True)),
                                 (2, dns_frame(response=True, flags=0x8383))])
    primary, extra = result["dns"]["events"]
    assert primary["response_time_ns"] == 1 and primary["response_time_ms"] == 0.000001
    assert extra["status"] == "additional_response" and extra["query_group"] == 1
    assert extra["query_indices"] == [] and extra["response_indices"] == [3]
    assert extra["response_time_ns"] is extra["response_time_ms"] is None
    assert (extra["rcode"], extra["tc"]) == (3, True)


def test_reuse_ambiguity_has_no_latency_and_linear_reference_output(tmp_path):
    rows = [(i, dns_frame()) for i in range(100)]
    rows += [(100 + i, dns_frame(response=True)) for i in range(100)]
    result = report_at(tmp_path, rows)
    primary, *additional = result["dns"]["events"]
    assert primary["status"] == "ambiguous" and primary["query_indices"] == list(range(1, 101))
    assert len(additional) == 99
    assert sum(len(e["query_indices"]) + len(e["response_indices"]) for e in result["dns"]["events"]) == 200
    assert all(e["response_time_ns"] is None for e in result["dns"]["events"])


def test_regression_discards_previously_closed_and_orphan_events_but_keeps_counts(tmp_path):
    result = report_at(tmp_path, [(0, dns_frame()), (1, dns_frame(response=True)),
                                 (6_000_000_000, frame()), (6_000_000_001, dns_frame(response=True)),
                                 (5_000_000_000, frame()), (7_000_000_000, dns_frame())])
    assert result["dns"]["time_analysis"] == "disabled_timestamp_regression"
    assert result["dns"]["query_count"] == result["dns"]["response_count"] == 2
    assert result["diagnostics"] == [{"code": "W_TIMESTAMP_REGRESSION",
                                      "reason": "timestamps regress in file order; DNS timing must remain disabled",
                                      "packet_index": 5}]
    assert result["complete"] and "discarded" in render_table(result)


def test_dns_limit_preserves_history_and_flow_byte_partition():
    result = analyze(FIXTURES / "baseline.pcap", Config(max_dns_keys=1))
    assert_consistent(result)
    check_dns_partition(result)
    assert not result["complete"] and result["summary"]["packet_count"] == 6
    assert result["dns"]["status"] == "partial" and result["dns"]["unassigned_indices"] == [6]
    assert (result["dns"]["query_count"], result["dns"]["response_count"]) == (2, 1)
    assert result["dns"]["events"][0]["status"] == "matched"
    assert result["diagnostics"][-1]["code"] == "E_LIMIT_DNS"
    assert result["summary"]["unattributed_captured_bytes"] == 0


def test_dns_observation_budget_applies_to_orphans_but_not_non_dns(tmp_path, monkeypatch):
    monkeypatch.setattr("pcap_analyzer.dns_report.MAX_DNS_OBSERVATIONS", 2)
    response = dns_frame(response=True)
    rows = [(0, response), (1, response), (2, frame())]
    config = Config(max_dns_keys=1)
    exact = report_at(tmp_path, rows, config)
    assert exact["complete"] and exact["dns"]["event_counts"]["unmatched_response"] == 2
    stopped = report_at(tmp_path, rows + [(3, response), (4, frame())], config)
    assert not stopped["complete"]
    assert stopped["summary"]["packet_count"] == 4
    assert stopped["dns"]["response_count"] == 3
    assert stopped["dns"]["events"] == exact["dns"]["events"]
    assert stopped["dns"]["unassigned_indices"] == [4]
    assert stopped["diagnostics"][-1]["code"] == "E_LIMIT_DNS_OBSERVATIONS"
    assert stopped["diagnostics"][-1]["packet_index"] == 4


def test_dns_observation_budget_preserves_pending_window_and_regression(tmp_path, monkeypatch):
    monkeypatch.setattr("pcap_analyzer.dns_report.MAX_DNS_OBSERVATIONS", 1)
    rows = [(1, dns_frame())]
    stopped = report_at(tmp_path, rows + [(2, dns_frame(response=True))])
    assert not stopped["complete"]
    assert stopped["dns"]["events"][0]["status"] == "window_incomplete"
    assert stopped["dns"]["unassigned_indices"] == [2]
    regressed = report_at(tmp_path, rows + [(0, dns_frame(response=True)), (2, dns_frame())])
    assert regressed["complete"]
    assert regressed["dns"]["time_analysis"] == "disabled_timestamp_regression"
    assert regressed["dns"]["query_count"] == 2 and regressed["dns"]["response_count"] == 1
    assert regressed["dns"]["unassigned_indices"] == []


@pytest.mark.parametrize("config,packets,code", [(Config(max_flows=1), 4, "E_LIMIT_FLOWS"),
                                                (Config(max_records=4), 4, "E_LIMIT_RECORDS")])
def test_other_limit_partial_results_include_last_counted_query(config, packets, code):
    result = analyze(FIXTURES / "baseline.pcap", config)
    assert_consistent(result)
    check_dns_partition(result)
    assert result["summary"]["packet_count"] == packets
    assert result["diagnostics"][-1]["code"] == code
    assert result["dns"]["events"][0]["status"] == "window_incomplete"
    assert result["dns"]["events"][0]["query_indices"] == [4]


def test_partial_tail_does_not_extend_waiting_window(tmp_path):
    result = report_at(tmp_path, [(0, dns_frame())], tail=b"\x00")
    assert not result["complete"] and result["dns"]["status"] == "partial"
    assert result["dns"]["events"][0]["status"] == "window_incomplete"
    assert result["diagnostics"][-1]["code"] == "E_RECORD_HEADER"


def test_diagnostic_limit_and_regression_invalidate_even_on_stopping_record(tmp_path):
    bad = dns_frame(payload=b"")
    result = report_at(tmp_path, [(0, dns_frame()), (1, dns_frame(response=True)),
                                 (6_000_000_000, bad), (5_000_000_000, bad)], Config(max_diagnostics=1))
    assert not result["complete"] and result["dns"]["time_analysis"] == "disabled_timestamp_regression"
    assert result["diagnostics"][-1]["code"] == "E_LIMIT_DIAGNOSTICS"


def test_invalid_timestamp_excluded_without_inventing_regression(tmp_path):
    query, response = dns_frame(), dns_frame(response=True)
    records = [(query, len(query), 10, 0), (response, len(response), 10, 1_000_000_000),
               (response, len(response), 10, 1)]
    result = analyze(saved(tmp_path, capture(records, nano=True)))
    assert_consistent(result)
    check_dns_partition(result)
    assert result["dns"]["query_count"] == result["dns"]["response_count"] == 1
    assert result["dns"]["events"][0]["response_time_ns"] == 1
    assert result["summary"]["timestamp_invalid_count"] == 1


@pytest.mark.parametrize("limit", [0, -1, 100001, True, 1.5])
def test_config_rejects_invalid_dns_limits(limit):
    with pytest.raises(ValueError, match="max_dns_keys"):
        Config(max_dns_keys=limit)


def test_cli_json_table_limit_and_bad_limit():
    base = [sys.executable, "-m", "pcap_analyzer", str(FIXTURES / "baseline.pcap")]
    first = subprocess.run(base + ["--format", "json"], capture_output=True, text=True, timeout=10)
    second = subprocess.run(base + ["--format", "json"], capture_output=True, text=True, timeout=10)
    assert first.returncode == 0 and first.stdout == second.stdout and first.stderr == ""
    assert json.loads(first.stdout)["dns"]["events"][0]["response_time_ms"] == 25
    limited = subprocess.run(base + ["--format", "json", "--max-dns-keys", "1"], capture_output=True, text=True, timeout=10)
    assert limited.returncode == 2 and "E_LIMIT_DNS" in limited.stderr
    VALIDATOR.validate(json.loads(limited.stdout))
    bad = subprocess.run(base + ["--max-dns-keys", "100001"], capture_output=True, text=True, timeout=10)
    assert bad.returncode == 2 and bad.stdout == ""
    table = subprocess.run(base, capture_output=True, text=True, timeout=10)
    assert table.returncode == 0 and "DNS matched" in table.stdout and "response_time_ms=25.0" in table.stdout


@pytest.mark.parametrize("mutation", ["latency_missing", "ambiguous_latency", "response_metadata", "disabled_events", "partial_status"])
def test_schema_rejects_inconsistent_dns_contract(mutation):
    result = copy.deepcopy(analyze(FIXTURES / "baseline.pcap"))
    dns = result["dns"]
    if mutation == "latency_missing":
        dns["events"][0]["response_time_ns"] = None
    elif mutation == "ambiguous_latency":
        dns["events"][2]["response_time_ns"] = 1
    elif mutation == "response_metadata":
        dns["events"][0]["tc"] = None
    elif mutation == "disabled_events":
        dns["time_analysis"] = "disabled_timestamp_regression"
    else:
        dns["status"] = "partial"
    with pytest.raises(ValidationError):
        VALIDATOR.validate(result)


def test_simultaneous_flow_and_dns_limits_account_for_triggering_packet():
    result = analyze(FIXTURES / "baseline.pcap", Config(max_flows=2, max_dns_keys=1))
    assert_consistent(result)
    check_dns_partition(result)
    assert result["summary"]["packet_count"] == 6
    assert result["summary"]["unattributed_captured_bytes"] == 72
    assert result["dns"]["unassigned_indices"] == [6]
    assert result["diagnostics"][-1]["code"] == "E_LIMIT_FLOWS"


def test_read_error_keeps_prefix_dns_evidence(tmp_path, monkeypatch):
    from pcap_analyzer.reader import PcapSource
    original = PcapSource.__iter__

    def broken(source):
        for record in original(source):
            yield record
            raise OSError("private-path-must-not-leak")

    monkeypatch.setattr(PcapSource, "__iter__", broken)
    result = report_at(tmp_path, [(0, dns_frame()), (1, dns_frame(response=True))])
    assert result["dns"]["events"][0]["status"] == "window_incomplete"
    assert result["diagnostics"][-1]["code"] == "E_INPUT"
    assert "private-path" not in render_json(result)
