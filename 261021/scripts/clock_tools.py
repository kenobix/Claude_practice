"""Explicit command tools: clock_tools."""
from __future__ import annotations


# ========================================================================
# capture_fake_clock_campaign
# ========================================================================

"""Capture fixed synthetic reader evidence only; never grant clock qualification."""
import argparse
from pathlib import Path
import signal
import sys

from bike_mlops.clock import FAULTS, capture_fake
from bike_mlops.clock import SCENARIOS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--fault", choices=sorted(FAULTS), default="none")
    parser.add_argument("--schedule", choices=sorted(SCENARIOS), default=None,
                        help="Opt-in virtual QPC fixed-period fixture; no real waiting/clock observation")
    args = parser.parse_args()
    def interrupted(signum, frame):
        raise InterruptedError("Synthetic capture interrupted")
    previous = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        plan_hash = capture_fake(args.output_dir, fault=args.fault, schedule=args.schedule)
        print("synthetic_plan_sha256=" + plan_hash)
        return 0  # Capture completion only; not qualification or offline numerical status.
    except (ValueError, OSError, UnicodeError, RecursionError):
        print("Synthetic capture incomplete; partial journal retained; no qualification", file=sys.stderr)
        return 2
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _entry_capture_fake_clock_campaign():
    sys.exit(main())


# ========================================================================
# derive_clock_campaign
# ========================================================================

"""Reconstruct a synthetic campaign from raw evidence; no real clock qualification."""
import argparse as _derive_clock_campaign_argparse
from pathlib import Path as _derive_clock_campaign_Path
import sys as _derive_clock_campaign_sys

from bike_mlops.clock import derive_raw, publish_json


def _derive_clock_campaign_main():
    parser = _derive_clock_campaign_argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=_derive_clock_campaign_Path, required=True)
    parser.add_argument("--output", type=_derive_clock_campaign_Path, required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    args = parser.parse_args()
    try:
        if args.input_dir.resolve() in args.output.resolve().parents:
            raise ValueError("Derived output must be outside the original evidence directory")
        value, report = derive_raw(args.input_dir, expected_plan_sha256=args.expected_plan_sha256)
        publish_json(args.output, value)
        print("synthetic_offline_checks_status=" + report["offline_checks_status"])
        return 1 if report["clock_qualification_status"] == "failed" else 2
    except (ValueError, OSError, UnicodeError, RecursionError, StopIteration, KeyError):
        print("Synthetic raw evidence invalid or unavailable; no qualification", file=_derive_clock_campaign_sys.stderr)
        return 2


def _entry_derive_clock_campaign():
    _derive_clock_campaign_sys.exit(_derive_clock_campaign_main())


# ========================================================================
# review_clock_campaign
# ========================================================================

"""Inspect saved timestamp integers only; no live clock or service access."""


import argparse as _review_clock_campaign_argparse
import hashlib
import json
import os
from pathlib import Path as _review_clock_campaign_Path
import stat
import sys as _review_clock_campaign_sys
import tempfile

from bike_mlops.clock import review_clock_campaign


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON object key")
        value[key] = item
    return value


def _reject_constant(value):
    raise ValueError("Non-finite JSON constant")


def _review_clock_campaign_main() -> int:
    parser = _review_clock_campaign_argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--input", type=_review_clock_campaign_Path, required=True)
    parser.add_argument("--output", type=_review_clock_campaign_Path, required=True)
    args = parser.parse_args()
    temporary = None
    try:
        if args.output.exists():
            raise ValueError("Refusing to overwrite an existing report")
        # Read through one descriptor and reject changes visible during reading.
        descriptor = os.open(args.input, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("Input must be a regular file")
            if before.st_size > 32 * 1024 * 1024:
                raise ValueError("Input exceeds 32 MiB limit")
            payload = stream.read(32 * 1024 * 1024 + 1)
            after = os.fstat(stream.fileno())
        identity = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        if identity(before) != identity(after) or identity(after) != identity(args.input.stat(follow_symlinks=False)):
            raise ValueError("Input changed while reading")
        if len(payload) != before.st_size:
            raise ValueError("Input size changed or exceeds limit")
        value = json.loads(payload, object_pairs_hook=_object, parse_constant=_reject_constant)
        report = review_clock_campaign(value, expected_plan_sha256=args.expected_plan_sha256)
        report["input_bytes"] = len(payload)
        report["input_sha256"] = hashlib.sha256(payload).hexdigest()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=args.output.parent, delete=False) as stream:
            temporary = _review_clock_campaign_Path(stream.name)
            json.dump(report, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, args.output)
        print(json.dumps({"offline_checks_status": report["offline_checks_status"],
                          "clock_qualification_status": report["clock_qualification_status"],
                          "input_sha256": report["input_sha256"]}))
        # Whole qualification never passes here, even when the archived checks do.
        return 1 if report["clock_qualification_status"] == "failed" else 2
    except (ValueError, OSError, UnicodeError, RecursionError):
        # Do not echo input data, JSON values or absolute operator paths.
        print("Clock-campaign input/output invalid or unavailable; no qualification established", file=_review_clock_campaign_sys.stderr)
        return 2
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _entry_review_clock_campaign():
    _review_clock_campaign_sys.exit(_review_clock_campaign_main())


# ========================================================================
# review_clock_rate_phase
# ========================================================================

"""Inspect saved timestamp integers only; no live clock or service access."""


import argparse as _review_clock_rate_phase_argparse
import hashlib as _review_clock_rate_phase_hashlib
import json as _review_clock_rate_phase_json
import os as _review_clock_rate_phase_os
from pathlib import Path as _review_clock_rate_phase_Path
import stat as _review_clock_rate_phase_stat
import sys as _review_clock_rate_phase_sys
import tempfile as _review_clock_rate_phase_tempfile

from bike_mlops.clock import review_clock_rate_phase


def _review_clock_rate_phase_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON object key")
        value[key] = item
    return value


def _review_clock_rate_phase_reject_constant(value):
    raise ValueError("Non-finite JSON constant")


def _review_clock_rate_phase_main() -> int:
    parser = _review_clock_rate_phase_argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=_review_clock_rate_phase_Path, required=True)
    parser.add_argument("--output", type=_review_clock_rate_phase_Path, required=True)
    args = parser.parse_args()
    temporary = None
    try:
        if args.output.exists():
            raise ValueError("Refusing to overwrite an existing report")
        # Read through one descriptor and reject changes visible during reading.
        descriptor = _review_clock_rate_phase_os.open(args.input, _review_clock_rate_phase_os.O_RDONLY | _review_clock_rate_phase_os.O_NOFOLLOW | _review_clock_rate_phase_os.O_NONBLOCK)
        with _review_clock_rate_phase_os.fdopen(descriptor, "rb") as stream:
            before = _review_clock_rate_phase_os.fstat(stream.fileno())
            if not _review_clock_rate_phase_stat.S_ISREG(before.st_mode):
                raise ValueError("Input must be a regular file")
            if before.st_size > 16 * 1024 * 1024:
                raise ValueError("Input exceeds 16 MiB limit")
            payload = stream.read(16 * 1024 * 1024 + 1)
            after = _review_clock_rate_phase_os.fstat(stream.fileno())
        identity = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        if identity(before) != identity(after) or identity(after) != identity(args.input.stat(follow_symlinks=False)):
            raise ValueError("Input changed while reading")
        if len(payload) != before.st_size:
            raise ValueError("Input size changed or exceeds limit")
        value = _review_clock_rate_phase_json.loads(payload, object_pairs_hook=_review_clock_rate_phase_object, parse_constant=_review_clock_rate_phase_reject_constant)
        report = review_clock_rate_phase(value)
        report["input_bytes"] = len(payload)
        report["input_sha256"] = _review_clock_rate_phase_hashlib.sha256(payload).hexdigest()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with _review_clock_rate_phase_tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=args.output.parent, delete=False) as stream:
            temporary = _review_clock_rate_phase_Path(stream.name)
            _review_clock_rate_phase_json.dump(report, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            _review_clock_rate_phase_os.fsync(stream.fileno())
        _review_clock_rate_phase_os.link(temporary, args.output)
        print(_review_clock_rate_phase_json.dumps({"rate_component_status": report["rate_component_status"],
                          "clock_qualification_status": report["clock_qualification_status"],
                          "input_sha256": report["input_sha256"]}))
        return {"passed": 0, "failed": 1, "incomplete": 2}[report["rate_component_status"]]
    except (ValueError, OSError, UnicodeError):
        # Do not echo input data, JSON values or absolute operator paths.
        print("Clock-rate input/output invalid or unavailable; no qualification established", file=_review_clock_rate_phase_sys.stderr)
        return 2
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _entry_review_clock_rate_phase():
    _review_clock_rate_phase_sys.exit(_review_clock_rate_phase_main())


# ========================================================================
# clock_raw_bundle
# ========================================================================

"""Export/review a local synthetic raw ZIP. This command never qualifies a real clock."""
import argparse as _clock_raw_bundle_argparse
from pathlib import Path as _clock_raw_bundle_Path
import signal as _clock_raw_bundle_signal
import sys as _clock_raw_bundle_sys
import zipfile

from bike_mlops import clock as raw
from bike_mlops.clock import export_bundle, review_bundle


def _clock_raw_bundle_main():
    parser = _clock_raw_bundle_argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--export-dir", type=_clock_raw_bundle_Path)
    mode.add_argument("--review-archive", type=_clock_raw_bundle_Path)
    parser.add_argument("--output", type=_clock_raw_bundle_Path, required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    args = parser.parse_args()
    def interrupt(signum, frame):
        raise InterruptedError("Synthetic bundle interrupted")
    previous = {sig: _clock_raw_bundle_signal.signal(sig, interrupt) for sig in (_clock_raw_bundle_signal.SIGINT, _clock_raw_bundle_signal.SIGTERM)}
    try:
        if args.export_dir is not None:
            result = export_bundle(args.export_dir, args.output,
                                   expected_plan_sha256=args.expected_plan_sha256)
            print(raw.encode(result).decode(), end="")
            return 0  # Archive publication only; no clock approval.
        result = review_bundle(args.review_archive,
                               expected_plan_sha256=args.expected_plan_sha256)
        if args.output.resolve() == args.review_archive.resolve():
            raise ValueError("Report must not replace archive")
        raw.publish_json(args.output, result)
        print(raw.encode({"offline_checks_status": result["review"]["offline_checks_status"],
                          "clock_qualification_status": result["review"]["clock_qualification_status"]}).decode(), end="")
        return 1 if result["review"]["clock_qualification_status"] == "failed" else 2
    except (ValueError, TypeError, OSError, UnicodeError, RecursionError, StopIteration,
            KeyError, zipfile.BadZipFile, zipfile.LargeZipFile):
        print("Synthetic bundle invalid or unavailable; no qualification", file=_clock_raw_bundle_sys.stderr)
        return 2
    finally:
        for sig, handler in previous.items():
            _clock_raw_bundle_signal.signal(sig, handler)


def _entry_clock_raw_bundle():
    _clock_raw_bundle_sys.exit(_clock_raw_bundle_main())


# ========================================================================
# diagnose_runtime_clocks
# ========================================================================

"""Record bounded local clock observations without DB, network or registry access."""


import argparse as _diagnose_runtime_clocks_argparse
from datetime import datetime, timezone
import json as _diagnose_runtime_clocks_json
from pathlib import Path as _diagnose_runtime_clocks_Path
import time


def _diagnose_runtime_clocks_main():
    parser = _diagnose_runtime_clocks_argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=_diagnose_runtime_clocks_Path, required=True, help="New evidence directory; must not exist")
    parser.add_argument("--samples", type=int, default=10000)
    parser.add_argument("--interval", type=float, default=0.001)
    args = parser.parse_args()
    if not 2 <= args.samples <= 100000 or not 0 <= args.interval <= 1:
        parser.error("samples must be 2..100000 and interval 0..1 seconds")
    args.output.mkdir(parents=True, exist_ok=False)
    clocks = {"realtime_ns": time.CLOCK_REALTIME, "monotonic_ns": time.CLOCK_MONOTONIC}
    for name in ("BOOTTIME", "MONOTONIC_RAW"):
        if hasattr(time, "CLOCK_" + name):
            clocks[name.lower() + "_ns"] = getattr(time, "CLOCK_" + name)
    backward = {name: 0 for name in clocks}
    anomalies, previous, first = [], None, None
    with (args.output / "samples.jsonl").open("x") as stream:
        for index in range(args.samples):
            row = {"index": index, "utc": datetime.now(timezone.utc).isoformat(),
                   **{name: time.clock_gettime_ns(clock) for name, clock in clocks.items()}}
            stream.write(_diagnose_runtime_clocks_json.dumps(row, sort_keys=True) + "\n")
            if previous:
                differences = {name: row[name] - previous[name] for name in clocks}
                for name, delta in differences.items():
                    backward[name] += delta < 0
                spread = max(differences.values()) - min(differences.values())
                if min(differences.values()) < 0 or spread > 10000000:
                    anomalies.append({"before": previous, "after": row, "deltas_ns": differences})
            first = first or row
            previous = row
            if index + 1 < args.samples:
                time.sleep(args.interval)
    summary = {"scope": "local_clock_observations_only", "samples": args.samples,
               "interval_seconds": args.interval, "first": first, "last": previous,
               "elapsed_ns": {name: previous[name] - first[name] for name in clocks},
               "backward_steps": backward, "anomalies": anomalies,
               "limitation": "Finite observations cannot establish the clocks at the historical failure."}
    (args.output / "summary.json").write_text(_diagnose_runtime_clocks_json.dumps(summary, indent=2) + "\n")
    print(_diagnose_runtime_clocks_json.dumps({key: summary[key] for key in ("samples", "elapsed_ns", "backward_steps")}), flush=True)
    print("anomalies=" + str(len(anomalies)), flush=True)


def _entry_diagnose_runtime_clocks():
    _diagnose_runtime_clocks_main()


def _dispatch_cli():
    import sys as _dispatch_sys
    commands = {'capture-fake': _entry_capture_fake_clock_campaign, 'derive': _entry_derive_clock_campaign, 'review': _entry_review_clock_campaign, 'rate-review': _entry_review_clock_rate_phase, 'bundle': _entry_clock_raw_bundle, 'diagnose': _entry_diagnose_runtime_clocks}
    if len(_dispatch_sys.argv) < 2 or _dispatch_sys.argv[1] in {"-h", "--help"}:
        print("Usage: clock_tools.py <command> [options]\nCommands: capture-fake, derive, review, rate-review, bundle, diagnose")
        return 0
    command = _dispatch_sys.argv.pop(1)
    if command not in commands:
        raise SystemExit("Unknown command: " + command)
    return commands[command]()


if __name__ == "__main__":
    raise SystemExit(_dispatch_cli())
