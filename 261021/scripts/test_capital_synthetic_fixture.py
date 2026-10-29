"""Generate and validate a Capital candidate solely inside a disposable workspace.

No Docker, external dataset, real evaluation ledger, or registry is used.
The temporary workspace is removed after this standalone verification.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import tempfile
from pathlib import Path


def _run_child() -> None:
    root = Path(__file__).resolve().parents[1]
    temporary_root = Path(tempfile.gettempdir()).resolve()
    if (root.parent != temporary_root or not root.name.startswith(("capital-synthetic-test-", "capital-system-harness-"))
            or not (root / ".synthetic-fixture-owned").is_file()):
        raise RuntimeError("Synthetic fixture may only run in its disposable marked workspace")
    import contextlib
    import io
    import zipfile
    import pandas as pd
    from bike_mlops.config import PROJECT_ROOT, DEFAULT_DATASET, DATASET_MANIFEST, DATA_PROFILE_ID
    from bike_mlops.capital_benchmark_inputs import check_candidate_training_window
    from bike_mlops.evaluation_protocol import build_capital_protocol, save_protocol
    from bike_mlops.training import HYPERPARAMETERS, train_candidate
    from scripts.prepare_capitalbikeshare import aggregate_archives
    from scripts import evaluate_capitalbikeshare_holdout

    if PROJECT_ROOT.resolve() != root or DATA_PROFILE_ID != "capital-bikeshare":
        raise RuntimeError("Synthetic fixture resolved an unexpected project root or profile")
    raw = root / "data/raw/capitalbikeshare/monthly"
    raw.mkdir(parents=True, exist_ok=False)
    lines = ["ride_id,started_at"]
    ride_id = 0
    for timestamp in pd.date_range("2024-01-01", "2024-02-01", freq="h", inclusive="left"):
        count = 5 + timestamp.hour % 12 + (0 if timestamp.dayofweek >= 5 else 25)
        for _ in range(count):
            lines.append(f"ride-{ride_id},{timestamp:%Y-%m-%d %H:%M:%S}")
            ride_id += 1
    with zipfile.ZipFile(raw / "202401-capitalbikeshare-tripdata.zip", "x", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("trips.csv", "\n".join(lines) + "\n")
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        aggregate_archives(["202401"], pd.Timestamp("2024-01-01"), pd.Timestamp("2024-02-01"),
                           DEFAULT_DATASET, DATASET_MANIFEST, raw_dir=raw)
        protocol = build_capital_protocol(DEFAULT_DATASET, DATASET_MANIFEST,
            training_cutoff="2024-01-15 00:00:00", validation_start="2024-01-15 00:00:00",
            validation_end="2024-01-22 00:00:00", evaluation_start="2024-01-22 03:00:00",
            evaluation_end="2024-01-29 00:00:00", incumbent_version=None, incumbent_artifact_sha256=None,
            incumbent_quality_gate=None, candidate_recipe={"model_family": "scikit-learn HistGradientBoostingRegressor in a Pipeline",
                                                          "hyperparameters": HYPERPARAMETERS}, raw_dir=raw)
        protocol_path = root / "data/evaluations/synthetic-protocol.json"
        save_protocol(protocol, protocol_path)
        report = train_candidate(data_path=DEFAULT_DATASET, incumbent_version=None, source_manifest_path=DATASET_MANIFEST,
                                 register=False, evaluation_protocol_path=protocol_path)
        if not report["quality_gate_passed"]:
            raise RuntimeError("Synthetic candidate did not pass the predefined validation gate")
        selection = root / report["candidate_artifacts"]["candidate_report"]
        output = root / "data/reports/capital-bikeshare/synthetic-final.json"
        sys.argv = ["evaluate_capitalbikeshare_holdout.py", "--csv", str(DEFAULT_DATASET),
                    "--source-manifest", str(DATASET_MANIFEST), "--selection-report", str(selection), "--output", str(output)]
        evaluate_capitalbikeshare_holdout.main()
        final = json.loads(output.read_text())
        if not final["final_accuracy_gate"]["passed"]:
            raise RuntimeError("Synthetic candidate did not pass the predefined final gate")
        window = check_candidate_training_window(selection, output, report["candidate_artifacts"]["candidate_id"],
            start="2024-01-07 22:00:00", end="2024-01-14 22:00:00")
        if window["hours"] != 168 or window["latest_replay_label_available_at"] != "2024-01-15 00:00:00":
            raise RuntimeError("Synthetic measurement window violates the training cutoff")
    summary = {"status": "passed", "data": "synthetic", "rides": ride_id,
               "candidate_id": report["candidate_artifacts"]["candidate_id"],
               "validation_gate": report["validation_gate_status"],
               "final_gate": final["final_accuracy_gate"]["status"],
               "measurement_window_hours": window["hours"],
               "latest_label_available_at": window["latest_replay_label_available_at"],
               "registry_writes": 0, "prediction_requests": 0,
               "deployment_permission": "blocked"}
    (root / "synthetic-fixture-result.json").write_text(json.dumps(summary), encoding="utf-8")


def _run_parent() -> None:
    from capital_system_harness import copy_workspace, prepare_synthetic_workspace, SOURCE_ROOT
    with tempfile.TemporaryDirectory(prefix="capital-synthetic-test-") as directory:
        root = Path(directory).resolve()
        copy_workspace(SOURCE_ROOT, root)
        summary = prepare_synthetic_workspace(root)
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    if root.exists():
        raise RuntimeError("Synthetic workspace was not removed")
    print("Disposable synthetic workspace removed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--internal-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.internal_child:
        _run_child()
        return
    previous = signal.getsignal(signal.SIGTERM)
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt("Synthetic fixture received SIGTERM")
    signal.signal(signal.SIGTERM, interrupted)
    try:
        _run_parent()
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    main()
