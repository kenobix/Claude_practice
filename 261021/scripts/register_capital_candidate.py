from __future__ import annotations

import argparse
import json
from pathlib import Path

from bike_mlops.capital_staging import register_blocked_capital_candidate


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Register a validation/final-passed Capital model as a deployment-blocked staging candidate"
    )
    parser.add_argument("--selection-report", type=Path, required=True)
    parser.add_argument("--final-report", type=Path, required=True)
    args = parser.parse_args()
    result = register_blocked_capital_candidate(args.selection_report, args.final_report)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
