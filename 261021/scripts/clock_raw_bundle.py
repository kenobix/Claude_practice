"""Export/review a local synthetic raw ZIP. This command never qualifies a real clock."""
import argparse
from pathlib import Path
import signal
import sys
import zipfile

from bike_mlops import clock_campaign_capture as raw
from bike_mlops.clock_raw_bundle import export_bundle, review_bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--export-dir", type=Path)
    mode.add_argument("--review-archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    args = parser.parse_args()
    def interrupt(signum, frame):
        raise InterruptedError("Synthetic bundle interrupted")
    previous = {sig: signal.signal(sig, interrupt) for sig in (signal.SIGINT, signal.SIGTERM)}
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
        print("Synthetic bundle invalid or unavailable; no qualification", file=sys.stderr)
        return 2
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    sys.exit(main())
