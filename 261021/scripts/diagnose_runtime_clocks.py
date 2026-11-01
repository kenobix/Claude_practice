"""Record bounded local clock observations without DB, network or registry access."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New evidence directory; must not exist")
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
            stream.write(json.dumps(row, sort_keys=True) + "\n")
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
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({key: summary[key] for key in ("samples", "elapsed_ns", "backward_steps")}), flush=True)
    print("anomalies=" + str(len(anomalies)), flush=True)


if __name__ == "__main__":
    main()
