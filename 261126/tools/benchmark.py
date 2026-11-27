"""Measure the language pipeline in-process, without trace or program output."""

import argparse
import hashlib
import json
import os
import platform
import sys
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from unittest.mock import patch

from tiny_language import vm
from tiny_language.compiler import compile_program
from tiny_language.interpreter import interpret
from tiny_language.parser import parse
from tiny_language.resolver import resolve
from tiny_language.scanner import scan
from tiny_language.type_checker import check_types

PROJECT = Path(__file__).resolve().parents[1]
BENCHMARKS = ("arithmetic_loop", "branches", "variables")
ENGINES = ("ast", "vm")


def reject_output(line: str) -> None:
    raise AssertionError(f"Unexpected benchmark output: {line!r}")


def measure(engine: str, source: str) -> dict[str, int]:
    clock = time.perf_counter_ns
    original_validate = vm.validate
    validation_ns = 0

    def timed_validate(code):
        nonlocal validation_ns
        started = clock()
        original_validate(code)
        validation_ns = clock() - started

    # Time the validation that execute normally performs. Keep it in the end-to-end total.
    validation = patch.object(vm, "validate", timed_validate) if engine == "vm" else nullcontext()
    with validation:
        start = clock()
        tokens = scan(source)
        program = parse(tokens)
        parsed = clock()
        resolution = resolve(program)
        check_types(program, resolution)
        checked = clock()
        if engine == "ast":
            compiled = checked
            interpret(program, resolution, reject_output)
        else:
            code = compile_program(program, resolution, tokens[-1].position)
            compiled = clock()
            vm.execute(code, reject_output)
        done = clock()
    return {
        "parse_ns": parsed - start,
        "resolve_type_ns": checked - parsed,
        "compile_ns": compiled - checked,
        "validate_ns": validation_ns,
        "execute_ns": done - compiled - validation_ns,
        "total_ns": done - start,
    }


def cpu_name() -> str:
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition(":")
            if key.strip() == "model name":
                return value.strip()
    return platform.processor() or platform.machine()


def measure_suite(warmups: int, repeats: int) -> dict:
    results = {}
    for name in BENCHMARKS:
        raw = (PROJECT / "benchmarks" / f"{name}.tiny").read_bytes()
        source = raw.decode("utf-8")
        trials = {engine: [] for engine in ENGINES}
        for iteration in range(warmups + repeats):
            order = ENGINES if iteration % 2 == 0 else ENGINES[::-1]
            for engine in order:
                sample = measure(engine, source)
                if iteration >= warmups:
                    trials[engine].append(sample)
        medians = {
            engine: {key: median(sample[key] for sample in samples) for key in samples[0]}
            for engine, samples in trials.items()
        }
        results[name] = {
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "samples": trials,
            "median": medians,
        }
    runtime_sources = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((PROJECT / "tiny_language").glob("*.py"))
    }
    return {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python": platform.python_implementation() + " " + platform.python_version(),
            "os": platform.platform(),
            "distribution": platform.freedesktop_os_release().get("PRETTY_NAME")
            if platform.system() == "Linux" else None,
            "cpu": cpu_name(),
            "logical_cpus": os.cpu_count(),
            "cpu_affinity": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
        },
        "clock": {
            "name": "time.perf_counter_ns",
            "implementation": time.get_clock_info("perf_counter").implementation,
            "resolution_seconds": time.get_clock_info("perf_counter").resolution,
        },
        "warmups": warmups,
        "repeats": repeats,
        "order": "Alternating AST/VM and VM/AST, starting with AST/VM at warmup 1.",
        "runtime_source_sha256": runtime_sources,
        "workloads": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warmups", type=int, default=4)
    parser.add_argument("--repeats", type=int, default=15)
    parser.add_argument("--output", type=Path, help="Write the JSON report to this path.")
    args = parser.parse_args()
    if args.warmups < 0 or args.repeats < 1:
        parser.error("warmups must be nonnegative and repeats must be positive")
    result = json.dumps(measure_suite(args.warmups, args.repeats), indent=2) + "\n"
    if args.output is None:
        sys.stdout.write(result)
    else:
        args.output.write_text(result, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
