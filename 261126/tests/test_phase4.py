import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import benchmark, compare


class GeneratedProgramsTests(unittest.TestCase):
    def test_seed_reproduces_full_corpus_and_independent_expectations(self):
        corpus = compare.cases(261126)
        self.assertEqual(corpus, compare.cases(261126))
        self.assertNotEqual(corpus, compare.cases(261127))
        with tempfile.TemporaryDirectory() as temp:
            report = compare.compare(corpus, 261126, Path(temp))
            self.assertEqual(report["failures"], [], sorted(path.name for path in Path(temp).iterdir()))
        self.assertEqual((report["passed"], report["normal"], report["runtime_error"]), (60, 44, 16))

    def test_equal_but_incorrect_results_and_resource_errors_are_saved(self):
        case = compare.cases(261126)[0]
        wrong = compare.Outcome("", 3, {"code": "E_LIMIT", "phase": "runtime"})
        with tempfile.TemporaryDirectory() as temp, patch.object(compare, "run_cli", return_value=wrong):
            directory = Path(temp)
            report = compare.compare([case], 261126, directory)
            self.assertEqual(report["failures"], [case.name])
            stem = directory / f"261126-{case.name}"
            self.assertEqual(stem.with_suffix(".tiny").read_bytes(), case.source.encode("utf-8"))
            saved = json.loads(stem.with_suffix(".json").read_bytes())
            self.assertEqual(saved["ast"], saved["vm"])
            self.assertNotEqual(saved["expected"], saved["vm"])


class BenchmarkTests(unittest.TestCase):
    def test_workloads_finish_with_independently_calculated_state(self):
        # 1680 = 420 * 4 = 240 * 7, for the sums of i/4 and i%7.
        arithmetic = 3 * 1680 * 1679 // 2 - 4 * 420 * 419 // 2 + 240 * 21
        additions = {
            "arithmetic_loop": ("print total; print i;", f"{arithmetic}\n1680\n"),
            "branches": ("print total; print i;", "-700\n1400\n"),
            "variables": ("print a; print b; print c; print i;", "2\n3\n1\n1600\n"),
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "observe.tiny"
            for name, (tail, expected) in additions.items():
                source = (benchmark.PROJECT / "benchmarks" / f"{name}.tiny").read_bytes()
                path.write_bytes(source + b"\n" + tail.encode("ascii"))
                for engine in benchmark.ENGINES:
                    with self.subTest(workload=name, engine=engine):
                        result = compare.run_cli(engine, path)
                        self.assertEqual(result, compare.Outcome(expected))

    def test_measurement_keeps_validation_and_reports_nonoverlapping_phases(self):
        for name in benchmark.BENCHMARKS:
            source = (benchmark.PROJECT / "benchmarks" / f"{name}.tiny").read_text(encoding="utf-8")
            for engine in benchmark.ENGINES:
                with self.subTest(workload=name, engine=engine):
                    sample = benchmark.measure(engine, source)
                    self.assertEqual(sample["total_ns"],
                                     sum(v for k, v in sample.items() if k != "total_ns"))
                    self.assertGreater(sample["execute_ns"], 0)
                    self.assertEqual(sample["validate_ns"] > 0, engine == "vm")
                    self.assertEqual(sample["compile_ns"] > 0, engine == "vm")
