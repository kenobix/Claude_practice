"""Comparison harness contracts; TShark itself is an explicit optional check."""

import importlib.util
import subprocess
import sys

import pytest

from pcap_analyzer.models import Config, InputError
from pcap_analyzer.reader import PcapSource
from test_phase1 import ROOT, FIXTURES

spec = importlib.util.spec_from_file_location("compare_tshark", ROOT / "scripts/compare_tshark.py")
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


@pytest.mark.parametrize("text,expected", [("0", 0), ("1700000000.000000123", 1700000000000000123),
                                          ("1700000000.000123", 1700000000000123000),
                                          ("1.1", 1100000000)])
def test_exact_epoch_conversion(text, expected):
    assert comparison.epoch_ns(text) == expected


@pytest.mark.parametrize("text", ["nan", "1e9", "-1", "1.0000000001", "1.", ""])
def test_reject_invalid_epoch(text):
    with pytest.raises(ValueError):
        comparison.epoch_ns(text)


def line(**changes):
    # Hand-defined baseline SYN: no analyzer output used to build the row.
    values = ["1", "1700000000.000000000", "54", "54", "192.0.2.1", "198.51.100.53",
              "6", "40000", "443", "", "", "0x0002"]
    row = dict(zip(comparison.FIELDS, values))
    row.update(changes)
    return "\t".join(row[field] for field in comparison.FIELDS) + "\n"


def baseline_record():
    with PcapSource(FIXTURES / "baseline.pcap", Config()) as source:
        return next(iter(source))


def test_common_fields_match_and_real_differences_are_not_suppressed():
    record = baseline_record()
    rows = comparison.parse_rows(line())
    assert comparison.compare([record], rows) == ([], [])
    rows[0]["tcp.flags"] = 18
    differences, excluded = comparison.compare([record], rows)
    assert differences == [{"packet_index": 1, "field": "tcp.flags", "analyzer": 2, "tshark": 18}]
    assert excluded == []
    assert comparison.compare([record], [])[0] == [{"field": "packet_count", "analyzer": 1, "tshark": 0}]


@pytest.mark.parametrize("output", ["1\t2\n", line(**{"frame.number": "2"}),
                                   line(**{"frame.cap_len": "-1"}),
                                   line(**{"frame.time_epoch": "nan"})])
def test_bad_tshark_output_is_rejected(output):
    with pytest.raises(ValueError):
        comparison.parse_rows(output)


def test_truncated_l4_exclusion_does_not_hide_l3_or_length_mismatch():
    with PcapSource(FIXTURES / "capture_truncated.pcap", Config()) as source:
        record = next(iter(source))
    # Partial TCP source/destination may be decoded by TShark, but full TCP
    # header is required by the analyzer. Compare L3 and frame metadata still.
    rows = comparison.parse_rows(line(**{"frame.cap_len": "40", "tcp.flags": ""}))
    differences, exclusions = comparison.compare([record], rows)
    assert differences == []
    assert exclusions == [{"packet_index": 1, "fields": "L4 ports/flags",
                           "reason": "capture_shorter_than_original"}]
    rows[0]["ip.src"] = "192.0.2.99"
    assert comparison.compare([record], rows)[0][0]["field"] == "ip.src"


def test_offline_command_is_fixed_and_missing_tool_fails(tmp_path):
    args = comparison.field_args(FIXTURES / "baseline.pcap")
    assert "-n" in args and args[args.index("-r") + 1].endswith("baseline.pcap")
    assert not {"-i", "-w", "-f", "-Y", "-N"}.intersection(args)
    assert tuple(args[i + 1] for i, arg in enumerate(args) if arg == "-e") == comparison.FIELDS
    result = subprocess.run([sys.executable, str(ROOT / "scripts/compare_tshark.py"),
                             "--tshark-root", str(tmp_path)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 2 and result.stdout == ""
    assert "TShark executable is missing" in result.stderr


@pytest.mark.parametrize("failure", ["nonzero", "timeout"])
def test_external_tool_failures_do_not_become_success(tmp_path, monkeypatch, failure):
    executable = tmp_path / "usr/bin/tshark"
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setenv("WIRESHARK_RUN_FROM_BUILD_DIRECTORY", "1")

    def failed(command, **kwargs):
        env = kwargs["env"]
        assert "WIRESHARK_RUN_FROM_BUILD_DIRECTORY" not in env
        assert env["WIRESHARK_CONFIG_DIR"] == env["WIRESHARK_PLUGIN_DIR"]
        assert kwargs["timeout"] == 30
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 30)
        return subprocess.CompletedProcess(command, 1, "", "synthetic tool failure")

    monkeypatch.setattr(comparison.subprocess, "run", failed)
    expected = subprocess.TimeoutExpired if failure == "timeout" else ValueError
    with pytest.raises(expected):
        comparison.run_suite(tmp_path)


def test_analyzer_input_error_is_an_explicit_comparison_failure(monkeypatch, capsys):
    def failed(_):
        raise InputError("E_RECORD_DATA", "synthetic incomplete fixture")

    monkeypatch.setattr(comparison, "run_suite", failed)
    monkeypatch.setattr(sys, "argv", ["compare_tshark.py"])
    with pytest.raises(SystemExit) as error:
        comparison.main()
    assert error.value.code == 2
    output = capsys.readouterr()
    assert output.out == "" and "synthetic incomplete fixture" in output.err
