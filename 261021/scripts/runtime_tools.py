"""Explicit command tools: runtime_tools."""
from __future__ import annotations


# ========================================================================
# register_capital_candidate
# ========================================================================

import argparse
import json
from pathlib import Path

from bike_mlops.staging import register_blocked_capital_candidate


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Register a validation/final-passed Capital model as a deployment-blocked staging candidate"
    )
    parser.add_argument("--selection-report", type=Path, required=True)
    parser.add_argument("--final-report", type=Path, required=True)
    args = parser.parse_args()
    result = register_blocked_capital_candidate(args.selection_report, args.final_report)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def _entry_register_capital_candidate():
    main()


# ========================================================================
# seed_capital_staging
# ========================================================================

import argparse as _seed_capital_staging_argparse
import json as _seed_capital_staging_json
import os
from pathlib import Path as _seed_capital_staging_Path
from urllib.parse import quote
from urllib.parse import urlsplit

from bike_mlops.cli import _load_profile_hourly
from bike_mlops.staging import verify_candidate_for_staging
from bike_mlops.staging import seed_verified_candidate, verify_seeded_candidate
from bike_mlops.evaluation import sha256_file
from bike_mlops.config import DATA_PROFILE_ID, DATASET_MANIFEST, DEFAULT_DATASET, TARGET_CONTRACT_ID, MODEL_NAME
from bike_mlops.db import Database


def _seed_capital_staging_main() -> None:
    parser = _seed_capital_staging_argparse.ArgumentParser(
        description="Seed only the isolated Capital staging database from the verified profile dataset"
    )
    parser.add_argument("--port", type=int, default=55434)
    parser.add_argument("--selection-report", type=_seed_capital_staging_Path, required=True)
    parser.add_argument("--final-report", type=_seed_capital_staging_Path, required=True)
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--verify-only", action="store_true", help="Verify complete existing observations without seeding")
    args = parser.parse_args()
    if DATA_PROFILE_ID != "capital-bikeshare":
        raise SystemExit("Set DATA_PROFILE_ID=capital-bikeshare before seeding staging")
    password = os.getenv("APP_DB_PASSWORD")
    if not password:
        raise SystemExit("APP_DB_PASSWORD must be set in the environment; it is not printed or stored by this script")
    if args.port != 55434:
        raise SystemExit("The isolated Capital staging PostgreSQL port is fixed at 55434")

    tracking_uri = os.getenv("MLFLOW_TRACKING_URI", "")
    uri = urlsplit(tracking_uri)
    if (uri.scheme != "http" or uri.hostname not in {"localhost", "127.0.0.1"} or uri.port != 5001
            or uri.username or uri.password or uri.path not in {"", "/"} or uri.query or uri.fragment):
        raise SystemExit("MLFLOW_TRACKING_URI must point to local Capital MLflow at port 5001")
    if not args.model_version.isdecimal() or int(args.model_version) <= 0:
        raise SystemExit("A positive model version is required")
    evidence = verify_candidate_for_staging(args.selection_report, args.final_report)
    if (sha256_file(DEFAULT_DATASET) != evidence["selection"]["data_sha256"]
            or sha256_file(DATASET_MANIFEST) != evidence["selection"]["data_source"]["source_manifest_sha256"]):
        raise ValueError("Staging CSV or manifest does not match the verified candidate")
    frame = _load_profile_hourly(DEFAULT_DATASET)
    from mlflow.tracking import MlflowClient
    registered = MlflowClient(tracking_uri=tracking_uri, registry_uri=tracking_uri).get_model_version(MODEL_NAME, args.model_version)

    encoded_password = quote(password, safe="")
    database_url = f"postgresql://bike_app:{encoded_password}@127.0.0.1:{args.port}/bikeops"
    database = Database(database_url, DATA_PROFILE_ID, TARGET_CONTRACT_ID)
    database.initialize()
    operation = verify_seeded_candidate if args.verify_only else seed_verified_candidate
    result = operation(database, frame, DEFAULT_DATASET, DATASET_MANIFEST, evidence, registered)
    print(_seed_capital_staging_json.dumps({
        "database": "isolated-capital-staging",
        "operation": "verify" if args.verify_only else "seed",
        "csv_path": str(DEFAULT_DATASET),
        "manifest_path": str(DATASET_MANIFEST),
        **result,
    }, indent=2, ensure_ascii=False))


def _entry_seed_capital_staging():
    _seed_capital_staging_main()


# ========================================================================
# prepare_capital_operational_plan
# ========================================================================

import argparse as _prepare_capital_operational_plan_argparse
import json as _prepare_capital_operational_plan_json
import os as _prepare_capital_operational_plan_os
import tempfile
from pathlib import Path as _prepare_capital_operational_plan_Path

from bike_mlops.operational_validation import check_candidate_training_window
from bike_mlops.operational import build_operational_plan, local_registry_url, save_operational_plan, validate_plan_registration
from bike_mlops.staging import verify_candidate_for_staging as _prepare_capital_operational_plan_verify_candidate_for_staging
from bike_mlops.config import DATA_PROFILE_ID as _prepare_capital_operational_plan_DATA_PROFILE_ID, MODEL_NAME as _prepare_capital_operational_plan_MODEL_NAME
from bike_mlops.operational import _machine_and_container_resources


def _prepare_capital_operational_plan_main() -> None:
    parser = _prepare_capital_operational_plan_argparse.ArgumentParser(description="Freeze the Capital staging operational plan without sending predictions")
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--selection-report", type=_prepare_capital_operational_plan_Path, required=True)
    parser.add_argument("--final-report", type=_prepare_capital_operational_plan_Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=_prepare_capital_operational_plan_Path, required=True)
    parser.add_argument("--api-url", default="http://127.0.0.1:8110")
    args = parser.parse_args()
    if _prepare_capital_operational_plan_DATA_PROFILE_ID != "capital-bikeshare" or _prepare_capital_operational_plan_MODEL_NAME != "bike-demand-capital":
        raise ValueError("Set DATA_PROFILE_ID=capital-bikeshare and use the Capital model name")
    version = args.model_version
    if not version.isdecimal() or int(version) <= 0:
        raise ValueError("A positive explicit model version is required")
    tracking_uri = local_registry_url(_prepare_capital_operational_plan_os.getenv("MLFLOW_TRACKING_URI", ""))

    window = check_candidate_training_window(
        args.selection_report, args.final_report, args.candidate_id, start=args.start, end=args.end,
    )
    evidence = _prepare_capital_operational_plan_verify_candidate_for_staging(args.selection_report, args.final_report)
    if (evidence["candidate_id"] != args.candidate_id
            or evidence["selection_report_sha256"] != window["candidate_report_sha256"]
            or evidence["final_report_sha256"] != window["final_report_sha256"]
            or evidence["evaluation_protocol_sha256"] != window["evaluation_protocol_sha256"]):
        raise ValueError("Candidate evidence changed after the training-window check")
    from mlflow.tracking import MlflowClient
    import mlflow
    import mlflow.artifacts

    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_registry_uri(tracking_uri)
    registered = MlflowClient(tracking_uri=tracking_uri, registry_uri=tracking_uri).get_model_version(_prepare_capital_operational_plan_MODEL_NAME, version)
    validate_plan_registration(evidence, registered)
    with tempfile.TemporaryDirectory(prefix="capital-plan-artifact-") as directory:
        artifact_dir = _prepare_capital_operational_plan_Path(mlflow.artifacts.download_artifacts(
            artifact_uri=f"models:/{_prepare_capital_operational_plan_MODEL_NAME}/{version}", dst_path=directory,
        ))
        resources = _machine_and_container_resources()
        try:
            resources["host_load_average"] = list(_prepare_capital_operational_plan_os.getloadavg())
        except (OSError, AttributeError):
            resources["host_load_average"] = None
        plan = build_operational_plan(evidence=evidence, window=window, registered=registered,
                                      artifact_dir=artifact_dir, api_url=args.api_url,
                                      registry_uri=tracking_uri, resource_context=resources)
        output = save_operational_plan(plan, args.output)
    print(_prepare_capital_operational_plan_json.dumps({"plan_path": str(output), "plan_sha256": plan["plan_sha256"],
                      "candidate_id": plan["identity"]["candidate_id"], "model_version": version,
                      "status": "plan_frozen_no_predictions_sent"}, indent=2, ensure_ascii=False))


def _entry_prepare_capital_operational_plan():
    _prepare_capital_operational_plan_main()


# ========================================================================
# benchmark_capital_runtime
# ========================================================================

import argparse as _benchmark_capital_runtime_argparse
import json as _benchmark_capital_runtime_json
from pathlib import Path as _benchmark_capital_runtime_Path

from bike_mlops.operational_validation import check_candidate_training_window as _benchmark_capital_runtime_check_candidate_training_window
from bike_mlops.config import DATA_PROFILE_ID as _benchmark_capital_runtime_DATA_PROFILE_ID, DEFAULT_DATABASE_URL, TARGET_CONTRACT_ID as _benchmark_capital_runtime_TARGET_CONTRACT_ID
from bike_mlops.db import Database as _benchmark_capital_runtime_Database


def _benchmark_capital_runtime_main() -> None:
    parser = _benchmark_capital_runtime_argparse.ArgumentParser(
        description="Validate a Capital replay window or run its frozen, auditable staging measurement"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check-input-only", action="store_true",
        help="Verify candidate evidence and the training window without sending prediction requests",
    )
    mode.add_argument(
        "--run-measurement", action="store_true",
        help="Run the fixed 50 warmup + 1,000 sequential request plan against isolated staging",
    )
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--selection-report", type=_benchmark_capital_runtime_Path, required=True)
    parser.add_argument("--final-report", type=_benchmark_capital_runtime_Path, required=True)
    parser.add_argument("--plan", type=_benchmark_capital_runtime_Path,
                        help="Frozen operational plan; required for --run-measurement")
    parser.add_argument("--start", default="2026-04-01 00:00:00",
                        help="Input-only check start; ignored during plan-bound measurement")
    parser.add_argument("--end", default="2026-04-08 00:00:00",
                        help="Input-only check end; ignored during plan-bound measurement")
    args = parser.parse_args()

    if _benchmark_capital_runtime_DATA_PROFILE_ID != "capital-bikeshare" or _benchmark_capital_runtime_TARGET_CONTRACT_ID != "capital-trip-starts-hourly-v1":
        parser.error("Set DATA_PROFILE_ID=capital-bikeshare before using this Capital-only command")
    if args.check_input_only:
        training_window = _benchmark_capital_runtime_check_candidate_training_window(
            args.selection_report, args.final_report, args.candidate_id, start=args.start, end=args.end
        )
        print(_benchmark_capital_runtime_json.dumps({"status": "training_window_verified", **training_window},
                         indent=2, ensure_ascii=False))
        return
    if args.plan is None:
        parser.error("--plan is required with --run-measurement")

    # The runner independently pins the plan, candidate evidence, PostgreSQL snapshot,
    # request source, persisted audit rows and result ledger before it can return completed.
    from bike_mlops.operational_runner import run_staging_measurement

    result = run_staging_measurement(
        args.plan, args.selection_report, args.final_report,
        _benchmark_capital_runtime_Database(DEFAULT_DATABASE_URL, _benchmark_capital_runtime_DATA_PROFILE_ID, _benchmark_capital_runtime_TARGET_CONTRACT_ID),
    )
    print(_benchmark_capital_runtime_json.dumps(result, indent=2, ensure_ascii=False))
    if result.get("status") != "completed":
        raise SystemExit(1)


def _entry_benchmark_capital_runtime():
    _benchmark_capital_runtime_main()


# ========================================================================
# recheck_capital_measurement
# ========================================================================

"""Recheck saved measurement numbers without changing the ledger or approving deployment."""


import argparse as _recheck_capital_measurement_argparse
import json as _recheck_capital_measurement_json

from bike_mlops.operational_validation import recheck_linked_measurement


def _recheck_capital_measurement_main() -> None:
    parser = _recheck_capital_measurement_argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--attempt-id", required=True)
    args = parser.parse_args()
    print(_recheck_capital_measurement_json.dumps(recheck_linked_measurement(args.candidate_id, args.attempt_id), ensure_ascii=False, indent=2))


def _entry_recheck_capital_measurement():
    _recheck_capital_measurement_main()


# ========================================================================
# record_capital_operational_evidence
# ========================================================================

import argparse as _record_capital_operational_evidence_argparse
import json as _record_capital_operational_evidence_json

from bike_mlops.operational_validation import record_completed_operational_evidence


def _record_capital_operational_evidence_main() -> None:
    parser = _record_capital_operational_evidence_argparse.ArgumentParser(
        description="Attach a linked, rechecked operational report to one exact blocked Capital MLflow version"
    )
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--attempt-id", required=True)
    args = parser.parse_args()
    print(_record_capital_operational_evidence_json.dumps(record_completed_operational_evidence(args.candidate_id, args.attempt_id),
                     ensure_ascii=False, indent=2))


def _entry_record_capital_operational_evidence():
    _record_capital_operational_evidence_main()


# ========================================================================
# prepare_capital_deployment_evidence
# ========================================================================

"""Publish evidence without reviewing, approving, or switching traffic."""


import argparse as _prepare_capital_deployment_evidence_argparse
import json as _prepare_capital_deployment_evidence_json
from pathlib import Path as _prepare_capital_deployment_evidence_Path

from bike_mlops.deployment import publish_deployment_evidence


def _prepare_capital_deployment_evidence_main():
    parser = _prepare_capital_deployment_evidence_argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--final-report", type=_prepare_capital_deployment_evidence_Path, required=True)
    for category in ("monitoring", "recovery", "restore", "metadata"):
        parser.add_argument(f"--{category}-report", type=_prepare_capital_deployment_evidence_Path, required=True)
    args = parser.parse_args()
    paths = {category: getattr(args, f"{category}_report")
             for category in ("monitoring", "recovery", "restore", "metadata")}
    result = publish_deployment_evidence(args.candidate_id, args.attempt_id, args.final_report, paths)
    print(_prepare_capital_deployment_evidence_json.dumps(result, indent=2))


def _entry_prepare_capital_deployment_evidence():
    _prepare_capital_deployment_evidence_main()


# ========================================================================
# record_capital_deployment_decision
# ========================================================================

"""Record a human operational review or a separate deployment approval."""


import argparse as _record_capital_deployment_decision_argparse
import json as _record_capital_deployment_decision_json
import os as _record_capital_deployment_decision_os
from urllib.parse import urlsplit as _record_capital_deployment_decision_urlsplit

from bike_mlops.deployment import record_deployment_approval, record_operational_review
from bike_mlops.config import DATA_PROFILE_ID as _record_capital_deployment_decision_DATA_PROFILE_ID, MODEL_NAME as _record_capital_deployment_decision_MODEL_NAME


def _client():
    uri = _record_capital_deployment_decision_os.getenv("MLFLOW_TRACKING_URI", "")
    parsed = _record_capital_deployment_decision_urlsplit(uri)
    if (_record_capital_deployment_decision_DATA_PROFILE_ID != "capital-bikeshare" or _record_capital_deployment_decision_MODEL_NAME != "bike-demand-capital" or parsed.scheme != "http"
            or parsed.hostname not in {"localhost", "127.0.0.1"}
            or parsed.port != 5001 or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment):
        raise ValueError("Set DATA_PROFILE_ID=capital-bikeshare and local Capital MLflow URI at port 5001")
    if _record_capital_deployment_decision_os.getenv("MLFLOW_REGISTRY_URI", uri).rstrip("/") != uri.rstrip("/"):
        raise ValueError("MLFLOW_REGISTRY_URI must match MLFLOW_TRACKING_URI")
    from mlflow.tracking import MlflowClient

    return MlflowClient(tracking_uri=uri, registry_uri=uri)


def _record_capital_deployment_decision_main() -> None:
    parser = _record_capital_deployment_decision_argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    review = subparsers.add_parser("review", help="record the independent operational review")
    review.add_argument("--version", required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--decision", choices=("passed", "rejected"), required=True)
    review.add_argument("--reason", required=True)
    approve = subparsers.add_parser("approve", help="record separate reasoned deployment approval")
    approve.add_argument("--version", required=True)
    approve.add_argument("--approver", required=True)
    approve.add_argument("--reason", required=True)
    args = parser.parse_args()
    client = _client()
    if args.action == "review":
        result = record_operational_review(
            client, args.version, args.reviewer, args.decision, args.reason
        )
    else:
        result = record_deployment_approval(client, args.version, args.approver, args.reason)
    print(_record_capital_deployment_decision_json.dumps(result, indent=2, sort_keys=True))


def _entry_record_capital_deployment_decision():
    _record_capital_deployment_decision_main()


# ========================================================================
# inspect_capital_operational_reports
# ========================================================================

"""Inspect operational attempt evidence without modifying or adopting reports."""


import argparse as _inspect_capital_operational_reports_argparse
import json as _inspect_capital_operational_reports_json

from bike_mlops.operational_validation import inspect_operational_reports


def _inspect_capital_operational_reports_main() -> None:
    parser = _inspect_capital_operational_reports_argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-id", required=True)
    args = parser.parse_args()
    print(_inspect_capital_operational_reports_json.dumps(inspect_operational_reports(args.candidate_id), ensure_ascii=False, indent=2))


def _entry_inspect_capital_operational_reports():
    _inspect_capital_operational_reports_main()


# ========================================================================
# inspect_capital_pending_audit
# ========================================================================

"""Inspect an ambiguous pending request in the isolated staging DB without retrying."""


import argparse as _inspect_capital_pending_audit_argparse
import json as _inspect_capital_pending_audit_json
import os as _inspect_capital_pending_audit_os
from urllib.parse import quote as _inspect_capital_pending_audit_quote

from bike_mlops.operational_runner import inspect_pending_audit
from bike_mlops.config import DATA_PROFILE_ID as _inspect_capital_pending_audit_DATA_PROFILE_ID, TARGET_CONTRACT_ID as _inspect_capital_pending_audit_TARGET_CONTRACT_ID
from bike_mlops.db import Database as _inspect_capital_pending_audit_Database


def _inspect_capital_pending_audit_main() -> None:
    parser = _inspect_capital_pending_audit_argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--attempt-id", required=True)
    args = parser.parse_args()
    if _inspect_capital_pending_audit_DATA_PROFILE_ID != "capital-bikeshare":
        raise SystemExit("Set DATA_PROFILE_ID=capital-bikeshare")
    password = _inspect_capital_pending_audit_os.getenv("APP_DB_PASSWORD")
    if not password:
        raise SystemExit("APP_DB_PASSWORD must be set in the environment")
    url = f"postgresql://bike_app:{_inspect_capital_pending_audit_quote(password, safe='')}@127.0.0.1:55434/bikeops"
    database = _inspect_capital_pending_audit_Database(url, _inspect_capital_pending_audit_DATA_PROFILE_ID, _inspect_capital_pending_audit_TARGET_CONTRACT_ID)
    print(_inspect_capital_pending_audit_json.dumps(inspect_pending_audit(args.candidate_id, args.attempt_id, database), indent=2, ensure_ascii=False))


def _entry_inspect_capital_pending_audit():
    _inspect_capital_pending_audit_main()


# ========================================================================
# operational_attempt_ledger
# ========================================================================

"""Inspect operational measurement attempts or review an interrupted retry."""



import argparse as _operational_attempt_ledger_argparse
import json as _operational_attempt_ledger_json
from pathlib import Path as _operational_attempt_ledger_Path

from bike_mlops.operational import read_operational_ledger, review_operational_retry


def _operational_attempt_ledger_main() -> None:
    parser = _operational_attempt_ledger_argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="Show local candidate attempts and reviews")
    review = commands.add_parser("review-retry", help="Record reasoned review after a failed/interrupted attempt")
    review.add_argument("--candidate-id", required=True)
    review.add_argument("--operator", required=True)
    review.add_argument("--reason", required=True)
    review.add_argument("--next-plan", type=_operational_attempt_ledger_Path, required=True)
    args = parser.parse_args()
    if args.command == "list":
        print(_operational_attempt_ledger_json.dumps(read_operational_ledger(), ensure_ascii=False, indent=2))
    else:
        review_operational_retry(args.candidate_id, args.operator, args.reason, next_plan_path=args.next_plan)
        print(_operational_attempt_ledger_json.dumps({"candidate_id": args.candidate_id, "status": "reviewed_reopen"}, ensure_ascii=False))


def _entry_operational_attempt_ledger():
    _operational_attempt_ledger_main()


def _dispatch_cli():
    import sys as _dispatch_sys
    commands = {'register': _entry_register_capital_candidate, 'seed': _entry_seed_capital_staging, 'plan': _entry_prepare_capital_operational_plan, 'benchmark': _entry_benchmark_capital_runtime, 'recheck': _entry_recheck_capital_measurement, 'record-operational': _entry_record_capital_operational_evidence, 'evidence': _entry_prepare_capital_deployment_evidence, 'decision': _entry_record_capital_deployment_decision, 'inspect': _entry_inspect_capital_operational_reports, 'pending-audit': _entry_inspect_capital_pending_audit, 'attempt-ledger': _entry_operational_attempt_ledger}
    if len(_dispatch_sys.argv) < 2 or _dispatch_sys.argv[1] in {"-h", "--help"}:
        print("Usage: runtime_tools.py <command> [options]\nCommands: register, seed, plan, benchmark, recheck, record-operational, evidence, decision, inspect, pending-audit, attempt-ledger")
        return 0
    command = _dispatch_sys.argv.pop(1)
    if command not in commands:
        raise SystemExit("Unknown command: " + command)
    return commands[command]()


if __name__ == "__main__":
    raise SystemExit(_dispatch_cli())
