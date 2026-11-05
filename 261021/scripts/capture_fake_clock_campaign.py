"""Capture fixed synthetic reader evidence only; never grant clock qualification."""
import argparse
from pathlib import Path
import signal
import sys

from bike_mlops.clock_campaign_capture import FAULTS, capture_fake
from bike_mlops.clock_campaign_schedule import SCENARIOS


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


if __name__ == "__main__":
    sys.exit(main())
