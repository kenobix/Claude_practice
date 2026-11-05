"""Inspect saved timestamp integers only; no live clock or service access."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

from bike_mlops.clock_campaign_review import review_clock_campaign


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON object key")
        value[key] = item
    return value


def _reject_constant(value):
    raise ValueError("Non-finite JSON constant")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
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
            temporary = Path(stream.name)
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
        print("Clock-campaign input/output invalid or unavailable; no qualification established", file=sys.stderr)
        return 2
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
