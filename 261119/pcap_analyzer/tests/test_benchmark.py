"""Validate the benchmark contract, not speed thresholds on shared CI hosts."""

import copy
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

from pcap_analyzer.report import analyze
from test_phase1 import assert_consistent

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/benchmark.py"
spec = importlib.util.spec_from_file_location("pcap_benchmark", SCRIPT)
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def test_recipe_boundaries_and_reproducibility(tmp_path):
    path = tmp_path / "recipe.pcap"
    for cycles in (1, 3000):
        bench.generate(path, cycles)
        assert path.stat().st_size == 24 + 263 * cycles
    bench.generate(path, 2)
    original = path.read_bytes()
    bench.generate(path, 2)
    assert path.read_bytes() == original
    report = analyze(path)
    assert_consistent(report)
    bench.verify(report, 2)
    assert report["classification_indices"] == {
        "parsed": [1, 2, 6, 7], "unsupported": [3, 8],
        "truncated": [4, 9], "malformed": [5, 10]}
    assert report["flows"][0]["a_to_b"]["packet_indices"] == [1, 4, 6, 9]
    assert report["flows"][0]["b_to_a"]["packet_indices"] == [2, 7]
    for edit in ("summary", "direction", "incomplete", "dns"):
        broken = copy.deepcopy(report)
        if edit == "summary":
            broken["summary"]["captured_bytes"] += 1
        elif edit == "direction":
            broken["flows"][0]["a_to_b"]["captured_bytes"] += 1
        elif edit == "incomplete":
            broken["complete"] = False
        else:
            broken["dns"]["query_count"] = 1
        with pytest.raises(ValueError):
            bench.verify(broken, 2)


@pytest.mark.parametrize("value", [0, 3001, True, 1.5])
def test_bad_scale_rejected(tmp_path, value):
    with pytest.raises(ValueError):
        bench.generate(tmp_path / "bad.pcap", value)
    assert not (tmp_path / "bad.pcap").exists()


def test_actual_cli_measurement_and_determinism():
    result = bench.benchmark(scales=(1,), repeats=2)
    first, second = result["results"][0]["runs"]
    assert first["wall_time_ns"] > 0 and first["peak_rss_kib"] > 0
    assert first["output_bytes"] > 0
    assert first["output_sha256"] == second["output_sha256"]
    assert result["results"][0]["expected_summary"]["packet_count"] == 5


def test_timeout_kills_and_reaps_child(tmp_path):
    path = tmp_path / "input.pcap"
    bench.generate(path, 1)
    with pytest.raises(TimeoutError):
        bench.run_cli(path, tmp_path / "report.json", 1e-12)


@pytest.mark.parametrize("program", ["raise SystemExit(2)", "import sys; sys.stderr.write('warning')"])
def test_bad_child_never_recorded_as_success(tmp_path, monkeypatch, program):
    popen = subprocess.Popen
    monkeypatch.setattr(bench.subprocess, "Popen", lambda command, **kw:
                        popen([sys.executable, "-c", program], **kw))
    with pytest.raises(ValueError, match="CLI failed"):
        bench.run_cli(tmp_path / "unused", tmp_path / "report.json", 5)


@pytest.mark.parametrize("kwargs", [{"repeats": 0}, {"repeats": 6}, {"timeout": 0}, {"timeout": 121}])
def test_measurement_limits(kwargs):
    with pytest.raises(ValueError):
        bench.benchmark(**kwargs)


def test_cli_failure_no_partial_json(monkeypatch, capsys):
    monkeypatch.setattr(bench, "benchmark", lambda **kwargs: (_ for _ in ()).throw(ValueError("failed")))
    assert bench.main([]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "failed\n"


def test_changed_output_is_rejected(monkeypatch):
    from pcap_analyzer.report import render_json
    calls = []
    def measurement(path, output, timeout):
        output.write_text(render_json(analyze(path)))
        calls.append(1)
        return {"output_sha256": str(len(calls))}
    monkeypatch.setattr(bench, "run_cli", measurement)
    with pytest.raises(ValueError, match="non-deterministic"):
        bench.benchmark(scales=(1,), repeats=2)
