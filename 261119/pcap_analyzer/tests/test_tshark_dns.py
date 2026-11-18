"""Replay actual TShark reference rows; normal CI never invokes TShark."""

import copy
import hashlib
import json
import subprocess
import sys

import pytest

from test_phase1 import ROOT, FIXTURES, assert_consistent

sys.path.insert(0, str(ROOT / "scripts"))
import compare_dns_tshark as comparison

REFERENCE = json.loads((ROOT / "docs/tshark_dns.result.json").read_text())


def inputs(name, tmp_path):
    generated = comparison.synthetic_captures()
    if name in generated:
        path = tmp_path / name
        path.write_bytes(generated[name])
    else:
        path = FIXTURES / name
    with comparison.PcapSource(path, comparison.Config()) as source:
        records = list(source)
    report = comparison.analyze(path)
    rows = copy.deepcopy(next(r["rows"] for r in REFERENCE["results"] if r["fixture"] == name))
    return path, records, rows, report


@pytest.mark.parametrize("name", tuple(comparison.EXPECTED))
def test_replay_independent_tshark_rows_against_recipe_and_hand_expectations(name, tmp_path):
    path, records, rows, report = inputs(name, tmp_path)
    reference = next(r for r in REFERENCE["results"] if r["fixture"] == name)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == reference["sha256"]
    assert_consistent(report)
    actual = comparison.evaluate(records, rows, report, comparison.EXPECTED[name])
    assert actual["differences"] == []
    assert actual["analyzer"] == reference["analyzer"]
    assert actual["matched_latencies"] == reference["matched_latencies"]
    if name == "dns_metadata.pcap":
        assert [(e["packet_index"], e["classification"], e["tshark_dns_id"]) for e in actual["exclusions"]] == [
            (10, "unsupported", 15), (11, "truncated", 16), (12, "malformed", None)]


@pytest.mark.parametrize("field,value", [("dns.id", 99), ("dns.qry.name", "other.test"),
                                          ("dns.qry.class", 3), ("dns.flags.rcode", 3),
                                          ("dns.flags.truncated", True), ("dns.time", 25000001),
                                          ("dns.response_to", 3), ("dns.response_in", 9)])
def test_changed_independent_field_or_latency_is_not_hidden(field, value, tmp_path):
    _, records, rows, report = inputs("baseline.pcap", tmp_path)
    rows[3 if field == "dns.response_in" else 4][field] = value
    result = comparison.evaluate(records, rows, report, comparison.EXPECTED["baseline.pcap"])
    assert result["differences"]


def test_missing_dns_row_and_incorrect_analyzer_state_are_differences(tmp_path):
    _, records, rows, report = inputs("baseline.pcap", tmp_path)
    report["dns"]["events"][-1]["status"] = "matched"
    report["dns"]["events"][-1]["response_time_ns"] = 20000000
    result = comparison.evaluate(records, rows[:-1], report, comparison.EXPECTED["baseline.pcap"])
    assert any(d["field"] == "packet_count" for d in result["differences"])
    assert any(d["field"] == "analyzer.events" for d in result["differences"])


def test_exact_signed_time_and_strict_dns_field_parser():
    values = ["1", "1700000000.900000000", "198.51.100.53", "192.0.2.1", "53", "53000",
              "0x0001", "True", "example.test", "1", "0x0001", "0", "False", "1", "", "-0.100000000"]
    row = comparison.parse_rows("\t".join(values) + "\n")[0]
    assert row["dns.time"] == -100000000 and row["dns.id"] == row["dns.qry.class"] == 1
    assert comparison.signed_ns("0.000000123") == 123
    values[7] = "maybe"
    with pytest.raises(ValueError):
        comparison.parse_rows("\t".join(values))
    with pytest.raises(ValueError):
        comparison.parse_rows("1\t2")


def test_display_name_comparison_is_explicitly_limited():
    assert comparison.ordinary_name((b"ALPHA", b"test", b"")) == "alpha.test"
    for labels in ((b"a.b", b""), (b"\xff", b""), (b"alpha",)):
        with pytest.raises(ValueError):
            comparison.ordinary_name(labels)


@pytest.mark.parametrize("matched,code", [(True, 0), (False, 1)])
def test_comparison_cli_exit_codes(monkeypatch, capsys, matched, code):
    monkeypatch.setattr(sys, "argv", ["compare_dns_tshark.py"])
    monkeypatch.setattr(comparison, "run_suite", lambda _: {"matched": matched})
    assert comparison.main() == code
    assert json.loads(capsys.readouterr().out)["matched"] is matched


def test_missing_tshark_is_failure_and_command_always_reads_offline(tmp_path):
    args = comparison.field_args(FIXTURES / "baseline.pcap")
    assert "-2" in args and "-n" in args and "-r" in args
    assert not {"-i", "-f", "-Y", "-N", "-w"}.intersection(args)
    result = subprocess.run([sys.executable, str(ROOT / "scripts/compare_dns_tshark.py"),
                             "--tshark-root", str(tmp_path)], text=True, capture_output=True, timeout=10)
    assert result.returncode == 2 and result.stdout == ""


@pytest.mark.parametrize("failure", ["exit", "stderr", "timeout"])
def test_tool_errors_and_warnings_are_not_accepted(monkeypatch, tmp_path, failure):
    def failed(args, **kwargs):
        assert "-r" in args and "-n" in args
        assert kwargs["env"]["WIRESHARK_PLUGIN_DIR"] == kwargs["env"]["WIRESHARK_CONFIG_DIR"]
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 30)
        return subprocess.CompletedProcess(args, 2 if failure == "exit" else 0, "", "synthetic diagnostic")

    monkeypatch.setattr(comparison.subprocess, "run", failed)
    expected = subprocess.TimeoutExpired if failure == "timeout" else ValueError
    with pytest.raises(expected):
        comparison.run_suite(tmp_path)
