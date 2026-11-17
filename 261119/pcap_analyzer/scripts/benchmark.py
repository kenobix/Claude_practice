"""Bounded, offline Linux CLI benchmark; generated captures live in a temp dir."""

import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import struct
import subprocess
import sys
import tempfile
import time

from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[1]


def generate(path, cycles):
    if type(cycles) is not int or not 1 <= cycles <= 3000:
        raise ValueError("cycles must be an integer in 1..3000")
    ethernet = bytes.fromhex("0200000000020200000000010800")
    def udp(reverse=False, missing=False):
        src, dst = bytes([192, 0, 2, 1]), bytes([198, 51, 100, 53])
        ports = (40000, 9000)
        if reverse:
            src, dst, ports = dst, src, ports[::-1]
        size = 12 if missing else 8
        ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + size, 0, 0, 64, 17, 0, src, dst)
        return ethernet + ip + struct.pack("!4H", *ports, size, 0)
    forward, backward = udp(), udp(True)
    rows = [(forward, 42), (backward, 42), (ethernet[:12] + b"\x08\x06", 14),
            (udp(missing=True), 46), (forward + b"\x00", 42)]
    with path.open("wb") as stream:
        stream.write(struct.pack("<IHHIIII", 0xA1B23C4D, 2, 4, 0, 0, 65535, 1))
        for index in range(5 * cycles):
            data, wire = rows[index % 5]
            stream.write(struct.pack("<4I", 1700000000, index, len(data), wire))
            stream.write(data)


def verify(report, cycles):
    """Hand-derived five-frame recipe; never learn expected counts from output."""
    try:
        Draft202012Validator(json.loads((ROOT / "docs/report.schema.json").read_text())).validate(report)
    except ValidationError as error:
        raise ValueError("CLI output does not satisfy report schema") from error
    s = report["summary"]
    expected = {"packet_count": 5 * cycles, "captured_bytes": 183 * cycles,
                "original_bytes": 186 * cycles, "flow_count": 1,
                "unattributed_packet_count": 2 * cycles,
                "unattributed_captured_bytes": 57 * cycles,
                "unattributed_original_bytes": 56 * cycles,
                "parsed_ratio": 0.4,
                "classification_counts": {"parsed": 2 * cycles, "unsupported": cycles,
                                          "truncated": cycles, "malformed": cycles}}
    if not report["complete"] or any(s[key] != value for key, value in expected.items()):
        raise ValueError("incomplete report or workload summary mismatch")
    flow, = report["flows"]
    for name, counts in (("a_to_b", (2, 84, 88)), ("b_to_a", (1, 42, 42))):
        direction = flow[name]
        if [direction[key] for key in ("packets", "captured_bytes", "original_bytes")] != [n * cycles for n in counts]:
            raise ValueError("direction totals mismatch")
    for field, unassigned in (("captured_bytes", "unattributed_captured_bytes"),
                              ("original_bytes", "unattributed_original_bytes")):
        if sum(flow[d][field] for d in ("a_to_b", "b_to_a")) + s[unassigned] != s[field]:
            raise ValueError("byte partition mismatch")
    if report["dns"]["query_count"] or report["dns"]["response_count"]:
        raise ValueError("unexpected DNS observations")
    return expected


def run_cli(path, output, timeout):
    """wait4 gives THIS child's Linux ru_maxrss in KiB, not cumulative children."""
    if sys.platform != "linux" or not hasattr(os, "wait4"):
        raise ValueError("measurement requires Linux wait4 (ru_maxrss KiB)")
    command = [sys.executable, "-m", "pcap_analyzer", str(path), "--format", "json"]
    with output.open("wb") as stdout, tempfile.TemporaryFile() as stderr:
        start = time.perf_counter_ns()
        process = subprocess.Popen(command, stdout=stdout, stderr=stderr, cwd=ROOT)
        try:
            while True:
                pid, status, usage = os.wait4(process.pid, os.WNOHANG)
                if pid:
                    process.returncode = os.waitstatus_to_exitcode(status)
                    break
                if (time.perf_counter_ns() - start) / 1e9 > timeout:
                    raise TimeoutError("CLI measurement timed out")
                time.sleep(0.01)
        finally:
            if process.returncode is None:
                process.kill()
                _, status, _ = os.wait4(process.pid, 0)
                process.returncode = os.waitstatus_to_exitcode(status)
        elapsed = time.perf_counter_ns() - start
        stderr.seek(0)
        if process.returncode or stderr.read():
            raise ValueError("CLI failed or wrote diagnostics to stderr")
    return {"wall_time_ns": elapsed, "peak_rss_kib": usage.ru_maxrss,
            "output_bytes": output.stat().st_size,
            "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest()}


def benchmark(scales=(100, 1000, 3000), repeats=3, timeout=60):
    if type(repeats) is not int or not 1 <= repeats <= 5:
        raise ValueError("repeats must be in 1..5")
    if not 0 < timeout <= 120:
        raise ValueError("timeout must be in (0, 120]")
    results = []
    with tempfile.TemporaryDirectory(prefix="pcap-benchmark-") as directory:
        path, output = Path(directory) / "input.pcap", Path(directory) / "report.json"
        for cycles in scales:
            generate(path, cycles)
            runs = []
            for _ in range(repeats):
                measurement = run_cli(path, output, timeout)
                expected = verify(json.loads(output.read_text()), cycles)
                runs.append(measurement)
            if len({r["output_sha256"] for r in runs}) != 1:
                raise ValueError("non-deterministic CLI output")
            results.append({"cycles": cycles, "input_bytes": path.stat().st_size,
                            "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                            "expected_summary": expected, "runs": runs})
    return {"measurement_version": 1, "measured_at_utc": datetime.now(timezone.utc).isoformat(),
            "python": platform.python_version(), "tool_version": version("pcap-analyzer"),
            "scapy_version": version("scapy"),
            "os": platform.system(), "kernel": platform.release(), "machine": platform.machine(),
            "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "results": results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args(argv)
    try:
        result = benchmark(repeats=args.repeats, timeout=args.timeout)
    except (OSError, ValueError, TimeoutError) as error:
        print(str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
