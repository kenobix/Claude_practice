from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from bike_mlops.config import PROJECT_ROOT
from bike_mlops.operational_benchmark import run_capital_operational_benchmark, write_benchmark_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the fixed local Capital API operational benchmark")
    parser.add_argument("--api-url", default="http://127.0.0.1:8110")
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--start", default="2026-04-01 00:00:00")
    parser.add_argument("--end", default="2026-04-08 00:00:00")
    parser.add_argument(
        "--output",
        type=Path,
        help="New JSON path under data/reports/capital-bikeshare/; defaults to a UTC-stamped path",
    )
    args = parser.parse_args()
    output = args.output or (
        PROJECT_ROOT / "data/reports/capital-bikeshare"
        / f"operational-benchmark-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    )
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite operational benchmark report: {output}")
    report = run_capital_operational_benchmark(
        api_url=args.api_url,
        model_version=args.model_version,
        candidate_id=args.candidate_id,
        start=args.start,
        end=args.end,
    )
    write_benchmark_report(output, report)
    print(json.dumps({"report_path": str(output), **report}, indent=2, ensure_ascii=False))
    if report["operational_gate"]["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
