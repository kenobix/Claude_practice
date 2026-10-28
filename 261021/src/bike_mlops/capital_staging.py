"""Register and load Capital candidates in an isolated, non-deployable staging lane."""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from pathlib import Path

import pandas as pd

from bike_mlops.candidate_artifacts import (
    candidate_identity,
    load_candidate_model,
    sha256_file,
    source_snapshot_inventory,
)
from bike_mlops.config import DATA_PROFILE_ID, MODEL_NAME, PROJECT_ROOT, TARGET_CONTRACT_ID
from bike_mlops.evaluation_protocol import (
    BOOTSTRAP_QUALITY_GATE_POLICY,
    BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
    evaluation_window_key,
    read_ledger,
    read_protocol,
)
from bike_mlops.model import directory_sha256
from bike_mlops.quality_gates import baseline_accuracy_gate
from bike_mlops.training import SKOPS_TRUSTED_TYPES, regression_metrics


def _project_file(path: Path, *, prefix: str) -> Path:
    resolved = path.resolve(strict=True)
    try:
        relative = resolved.relative_to(PROJECT_ROOT.resolve())
    except ValueError as error:
        raise ValueError(f"Evidence file must be inside the project: {path}") from error
    if not relative.as_posix().startswith(prefix):
        raise ValueError(f"Evidence file must be under {prefix}: {path}")
    if not resolved.is_file():
        raise ValueError(f"Evidence path is not a regular file: {path}")
    return resolved


def _read_json(path: Path, description: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Could not read {description}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{description} must contain a JSON object")
    return value


def _verify_final_row_evidence(
    final: dict,
    final_path: Path,
    protocol: dict,
    *,
    candidate_artifact_sha256: str,
    evaluation_protocol_sha256: str,
    data_sha256: str,
    target_contract_id: str,
) -> None:
    """Tie final metrics to the saved prediction rows and completed consumption record."""
    window = final.get("evaluation_window")
    if not isinstance(window, dict):
        raise ValueError("Final report is missing its evaluation window")
    row_path_value = window.get("row_level_predictions")
    if not isinstance(row_path_value, str) or not row_path_value.startswith(
        "data/reports/capital-bikeshare/"
    ):
        raise ValueError("Final report does not identify canonical row-level prediction evidence")
    row_path = _project_file(PROJECT_ROOT / row_path_value, prefix="data/reports/capital-bikeshare/")
    if sha256_file(row_path) != window.get("row_level_predictions_sha256"):
        raise ValueError("Final row-level prediction evidence SHA-256 differs from its report")

    try:
        rows = pd.read_csv(row_path)
        required_columns = {
            "timestamp", "label_available_at", "actual", "candidate_prediction", "baseline_prediction"
        }
        if set(rows.columns) != required_columns:
            raise ValueError("Final row-level prediction evidence has an unsupported schema")
        timestamps = pd.to_datetime(rows["timestamp"], errors="raise")
        labels_available_at = pd.to_datetime(rows["label_available_at"], errors="raise")
        if timestamps.dt.tz is not None or labels_available_at.dt.tz is not None:
            raise ValueError("Final row-level evidence timestamps must remain timezone-naive")
        if timestamps.isna().any() or labels_available_at.isna().any():
            raise ValueError("Final row-level evidence contains missing timestamps")
    except (OSError, pd.errors.ParserError, pd.errors.EmptyDataError, TypeError) as error:
        raise ValueError(f"Could not read final row-level prediction evidence: {error}") from error

    protocol_window = protocol.get("evaluation", {})
    expected_rows = protocol_window.get("rows")
    start = pd.Timestamp(protocol_window.get("start_inclusive"))
    end = pd.Timestamp(protocol_window.get("end_exclusive"))
    delay = protocol.get("label_availability", {}).get("delay_hours")
    if (
        isinstance(expected_rows, bool)
        or not isinstance(expected_rows, int)
        or expected_rows <= 0
        or len(rows) != expected_rows
        or window.get("rows") != expected_rows
        or window.get("start_inclusive") != protocol_window.get("start_inclusive")
        or window.get("end_exclusive") != protocol_window.get("end_exclusive")
    ):
        raise ValueError("Final row-level evidence count/window differs from the frozen protocol")
    if (
        not timestamps.is_monotonic_increasing
        or not timestamps.is_unique
        or (timestamps != timestamps.dt.floor("h")).any()
        or (timestamps < start).any()
        or (timestamps >= end).any()
        or not labels_available_at.equals(timestamps + pd.to_timedelta(delay, unit="h"))
    ):
        raise ValueError("Final row-level timestamps or label-availability times differ from the frozen protocol")

    actual = pd.to_numeric(rows["actual"], errors="raise")
    candidate_prediction = pd.to_numeric(rows["candidate_prediction"], errors="raise")
    baseline_prediction = pd.to_numeric(rows["baseline_prediction"], errors="raise")
    for label, values in (
        ("actual", actual),
        ("candidate_prediction", candidate_prediction),
        ("baseline_prediction", baseline_prediction),
    ):
        if not values.map(math.isfinite).all() or (values < 0).any():
            raise ValueError(f"Final row-level evidence contains invalid {label} values")
    if (actual % 1 != 0).any():
        raise ValueError("Final row-level actual labels must be integer counts")

    expected_metrics = {
        "candidate": regression_metrics(actual, candidate_prediction),
        "historical_baseline": regression_metrics(actual, baseline_prediction),
    }
    for section, metrics in expected_metrics.items():
        reported = final.get(section, {}).get("model")
        if not isinstance(reported, dict):
            raise ValueError(f"Final report is missing {section} metrics")
        for metric_name, value in metrics.items():
            recorded = reported.get(metric_name)
            if (
                isinstance(recorded, bool)
                or not isinstance(recorded, (int, float))
                or not math.isfinite(float(recorded))
                or not math.isclose(float(recorded), value, rel_tol=1e-12, abs_tol=1e-12)
            ):
                raise ValueError(
                    f"Final report {section}.{metric_name} does not match its saved row-level evidence"
                )

    ledger = read_ledger()
    key = evaluation_window_key(protocol, "reserved_final_evaluation")
    records = [record for record in ledger.get("records", []) if record.get("consumption_key") == key]
    report_sha256 = sha256_file(final_path)
    if len(records) != 1:
        raise ValueError("Final evaluation has no unique consumption-ledger record")
    record = records[0]
    if (
        record.get("status") != "completed"
        or record.get("purpose") != "reserved_final_evaluation"
        or record.get("data_sha256") != data_sha256
        or record.get("target_contract") != target_contract_id
        or record.get("start_inclusive") != protocol_window.get("start_inclusive")
        or record.get("end_exclusive") != protocol_window.get("end_exclusive")
    ):
        raise ValueError("Final evaluation ledger does not match the frozen protocol window")
    matching_attempts = [
        attempt
        for attempt in record.get("attempts", [])
        if attempt.get("status") == "completed"
        and attempt.get("protocol_sha256") == evaluation_protocol_sha256
        and attempt.get("candidate_artifact_sha256") == candidate_artifact_sha256
        and attempt.get("detail") == report_sha256
    ]
    if not matching_attempts:
        raise ValueError("Final report hash is not recorded by a completed candidate evaluation attempt")


def verify_candidate_for_staging(selection_path: Path, final_path: Path) -> dict:
    """Verify immutable candidate and final-evaluation evidence before registry writes."""
    if DATA_PROFILE_ID != "capital-bikeshare" or MODEL_NAME != "bike-demand-capital":
        raise ValueError("Capital staging registration requires DATA_PROFILE_ID=capital-bikeshare")
    selection_path = _project_file(selection_path, prefix="data/candidates/")
    final_path = _project_file(final_path, prefix="data/reports/capital-bikeshare/")
    selection = _read_json(selection_path, "candidate report")
    final = _read_json(final_path, "final-evaluation report")

    candidate_id = selection.get("candidate_id")
    if not isinstance(candidate_id, str) or len(candidate_id) != 64 or selection_path.name != "candidate-report.json":
        raise ValueError("Selection report must be the canonical candidate-report.json")
    expected_bundle = (PROJECT_ROOT / "data/candidates" / candidate_id).resolve()
    if selection_path.parent != expected_bundle or selection.get("candidate_bundle") != f"data/candidates/{candidate_id}":
        raise ValueError("Candidate report is not in its canonical immutable candidate bundle")
    try:
        identity = candidate_identity(selection)
    except (KeyError, TypeError) as error:
        raise ValueError(f"Candidate report lacks identity fields: {error}") from error
    identity_bytes = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    if identity != selection.get("candidate_identity") or hashlib.sha256(identity_bytes).hexdigest() != candidate_id:
        raise ValueError("Candidate ID does not match the frozen report identity")

    artifact = selection.get("candidate_artifact")
    protocol_artifact = selection.get("evaluation_protocol_artifact")
    if not isinstance(artifact, dict) or artifact.get("path") != "model.skops":
        raise ValueError("Candidate report does not identify the canonical skops artifact")
    artifact_path = selection_path.parent / "model.skops"
    if sha256_file(artifact_path) != artifact.get("sha256"):
        raise ValueError("Candidate model artifact SHA-256 differs from its report")
    if not isinstance(protocol_artifact, dict) or protocol_artifact.get("path") != "evaluation-protocol.json":
        raise ValueError("Candidate report does not identify its frozen protocol")
    protocol_path = selection_path.parent / "evaluation-protocol.json"
    if sha256_file(protocol_path) != protocol_artifact.get("sha256"):
        raise ValueError("Candidate evaluation-protocol artifact SHA-256 differs from its report")
    protocol = read_protocol(protocol_path)
    if (
        protocol.get("schema_version") != 2
        or protocol.get("incumbent") is not None
        or protocol.get("protocol_sha256") != selection.get("evaluation_protocol_sha256")
        or protocol.get("dataset", {}).get("data_profile_id") != "capital-bikeshare"
        or protocol.get("dataset", {}).get("target_contract") != TARGET_CONTRACT_ID
        or protocol.get("dataset", {}).get("data_sha256") != selection.get("data_sha256")
        or protocol.get("dataset", {}).get("source_manifest_sha256")
        != selection.get("data_source", {}).get("source_manifest_sha256")
        or selection.get("data_profile_id") != "capital-bikeshare"
        or selection.get("target_contract_id") != TARGET_CONTRACT_ID
        or selection.get("training_cutoff") != protocol.get("training", {}).get("cutoff")
        or selection.get("validation_window") != {
            "start": protocol.get("validation", {}).get("start_inclusive"),
            "end_exclusive": protocol.get("validation", {}).get("end_exclusive"),
        }
        or protocol.get("selection_gate", {}).get("policy_id") != BOOTSTRAP_QUALITY_GATE_POLICY["policy_id"]
        or protocol.get("selection_gate", {}).get("policy_sha256") != BOOTSTRAP_QUALITY_GATE_POLICY_SHA256
    ):
        raise ValueError("Candidate protocol is not the supported baseline-only bootstrap policy")
    predictions_path = selection_path.parent / "validation-predictions.csv"
    predictions_info = selection.get("validation_predictions")
    if (
        not isinstance(predictions_info, dict)
        or predictions_info.get("path") != "validation-predictions.csv"
        or sha256_file(predictions_path) != predictions_info.get("sha256")
    ):
        raise ValueError("Candidate validation-prediction evidence is missing or changed")
    snapshot_path = selection_path.parent / "source-snapshot.tar.gz"
    snapshot_info = selection.get("source_snapshot")
    if (
        not isinstance(snapshot_info, dict)
        or snapshot_info.get("path") != "source-snapshot.tar.gz"
        or sha256_file(snapshot_path) != snapshot_info.get("sha256")
    ):
        raise ValueError("Candidate source snapshot is missing or changed")
    snapshot_tree_sha, snapshot_file_count = source_snapshot_inventory(snapshot_path)
    if (
        snapshot_tree_sha != selection.get("source_revision", {}).get("source_tree_sha256")
        or snapshot_file_count != selection.get("source_revision", {}).get("source_file_count")
    ):
        raise ValueError("Candidate source snapshot does not match the frozen source-tree identity")
    if not selection.get("quality_gate_passed") or selection.get("validation_gate_status") != "passed":
        raise ValueError("A candidate that failed validation cannot enter operational staging")
    validation_gate = selection.get("comparison", {}).get("validation_gate")
    recomputed_validation_gate = baseline_accuracy_gate(
        selection.get("validation", {}).get("model"),
        selection.get("validation", {}).get("baseline"),
        period="validation_selection",
    )
    if (
        selection.get("comparison", {}).get("reference") != "historical_baseline_only"
        or selection.get("comparison", {}).get("incumbent_model_version") is not None
        or selection.get("comparison", {}).get("incumbent_artifact_sha256") is not None
        or selection.get("quality_gate_policy_id") != BOOTSTRAP_QUALITY_GATE_POLICY["policy_id"]
        or selection.get("quality_gate_policy_sha256") != BOOTSTRAP_QUALITY_GATE_POLICY_SHA256
        or validation_gate != recomputed_validation_gate
        or recomputed_validation_gate.get("passed") is not True
    ):
        raise ValueError("Candidate validation report does not prove the fixed baseline-only selection gate")

    final_gate = final.get("final_accuracy_gate")
    recomputed_final_gate = baseline_accuracy_gate(
        final.get("candidate", {}).get("model"),
        final.get("historical_baseline", {}).get("model"),
        period="final_evaluation",
    )
    expected_policy = BOOTSTRAP_QUALITY_GATE_POLICY["policy_id"]
    expected_policy_sha = BOOTSTRAP_QUALITY_GATE_POLICY_SHA256
    final_window = final.get("evaluation_window", {})
    protocol_window = protocol.get("evaluation", {})
    expected_final_source = {
        "csv_sha256": selection.get("data_sha256"),
        "source_manifest_sha256": selection.get("data_source", {}).get("source_manifest_sha256"),
    }
    final_source = final.get("data_source", {})
    if (
        final.get("candidate_id") != candidate_id
        or final.get("candidate_artifact_sha256") != artifact.get("sha256")
        or final.get("selection_report") != selection_path.relative_to(PROJECT_ROOT).as_posix()
        or final.get("evaluation_protocol_sha256") != selection.get("evaluation_protocol_sha256")
        or any(final_source.get(key) != value for key, value in expected_final_source.items())
        or final_window.get("start_inclusive") != protocol_window.get("start_inclusive")
        or final_window.get("end_exclusive") != protocol_window.get("end_exclusive")
        or final_window.get("rows") != protocol_window.get("rows")
        or final_window.get("used_for_candidate_selection") is not False
        or final.get("data_profile_id") != "capital-bikeshare"
        or final.get("target_contract_id") != TARGET_CONTRACT_ID
        or final.get("validation_gate_status") != "passed"
        or final.get("quality_gate_policy_id") != expected_policy
        or final.get("quality_gate_policy_sha256") != expected_policy_sha
        or final_gate != recomputed_final_gate
        or recomputed_final_gate.get("passed") is not True
        or final.get("operational_gate_status") != "not_evaluated"
        or final.get("deployment_permission", {}).get("status") != "blocked"
        or final.get("model_fit_performed_during_evaluation") is not False
    ):
        raise ValueError("Final report does not prove a passed, same-candidate bootstrap evaluation with deployment blocked")

    _verify_final_row_evidence(
        final,
        final_path,
        protocol,
        candidate_artifact_sha256=artifact["sha256"],
        evaluation_protocol_sha256=selection["evaluation_protocol_sha256"],
        data_sha256=selection["data_sha256"],
        target_contract_id=TARGET_CONTRACT_ID,
    )

    return {
        "candidate_id": candidate_id,
        "selection_report_path": selection_path,
        "selection_report_sha256": sha256_file(selection_path),
        "final_report_path": final_path,
        "final_report_sha256": sha256_file(final_path),
        "candidate_artifact_path": artifact_path,
        "candidate_artifact_sha256": artifact["sha256"],
        "evaluation_protocol_sha256": selection["evaluation_protocol_sha256"],
        "quality_gate_policy_id": expected_policy,
        "quality_gate_policy_sha256": expected_policy_sha,
        "selection": selection,
        "final": final,
    }


def _registration_tags(evidence: dict) -> dict[str, str]:
    selection = evidence["selection"]
    return {
        "data_profile_id": "capital-bikeshare",
        "target_contract_id": TARGET_CONTRACT_ID,
        "quality_gate": "passed",
        "validation_gate_status": "passed",
        "final_accuracy_gate_status": "passed",
        "operational_gate_status": "not_evaluated",
        "deployment_permission": "blocked",
        "quality_gate_policy_id": evidence["quality_gate_policy_id"],
        "quality_gate_policy_sha256": evidence["quality_gate_policy_sha256"],
        "candidate_id": evidence["candidate_id"],
        "candidate_model_sha256": evidence["candidate_artifact_sha256"],
        "candidate_report_sha256": evidence["selection_report_sha256"],
        "final_report_sha256": evidence["final_report_sha256"],
        "evaluation_protocol_sha256": evidence["evaluation_protocol_sha256"],
        "data_sha256": str(selection["data_sha256"]),
        "feature_contract_version": str(selection["feature_contract_version"]),
    }


def _staging_environment(evidence: dict, version: str, registered_artifact_sha256: str) -> str:
    values = {
        "MODEL_VERSION": version,
        "CAPITAL_STAGING_MODEL_VERSION": version,
        "CAPITAL_STAGING_CANDIDATE_ID": evidence["candidate_id"],
        "CAPITAL_STAGING_CANDIDATE_ARTIFACT_SHA256": evidence["candidate_artifact_sha256"],
        "CAPITAL_STAGING_SELECTION_REPORT_SHA256": evidence["selection_report_sha256"],
        "CAPITAL_STAGING_FINAL_REPORT_SHA256": evidence["final_report_sha256"],
        "CAPITAL_STAGING_EVALUATION_PROTOCOL_SHA256": evidence["evaluation_protocol_sha256"],
        "CAPITAL_STAGING_REGISTERED_ARTIFACT_SHA256": registered_artifact_sha256,
    }
    return "".join(f"{key}={value}\n" for key, value in values.items())


def _write_staging_environment(evidence: dict, version: str, registered_artifact_sha256: str) -> Path:
    path = PROJECT_ROOT / "data/runtime-capital-staging/staging.env"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.resolve(strict=False).relative_to(PROJECT_ROOT.resolve())
    except ValueError as error:
        raise ValueError("Staging identity file must remain inside the project directory") from error
    contents = _staging_environment(evidence, version, registered_artifact_sha256)
    if path.exists():
        if path.read_text(encoding="utf-8") != contents:
            raise FileExistsError(f"Refusing to replace a different staging identity file: {path}")
    else:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(contents)
    return path


def validate_staging_model_tags(tags: object, expected: dict[str, str]) -> None:
    """Require exact staging evidence and reject any promotion-eligible registration."""
    if not isinstance(tags, dict):
        raise ValueError("Staging registry metadata must be a tag mapping")
    required = {
        "data_profile_id": "capital-bikeshare",
        "target_contract_id": TARGET_CONTRACT_ID,
        "quality_gate": "passed",
        "validation_gate_status": "passed",
        "final_accuracy_gate_status": "passed",
        "operational_gate_status": "not_evaluated",
        "deployment_permission": "blocked",
        "quality_gate_policy_id": BOOTSTRAP_QUALITY_GATE_POLICY["policy_id"],
        "quality_gate_policy_sha256": BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
    }
    required.update(expected)
    mismatches = [key for key, value in required.items() if tags.get(key) != value]
    if mismatches:
        raise ValueError("Staging registry identity is missing or differs: " + ", ".join(mismatches))


def register_blocked_capital_candidate(selection_path: Path, final_path: Path) -> dict:
    """Register an accuracy-passed candidate with operational and deployment gates blocked."""
    evidence = verify_candidate_for_staging(selection_path, final_path)
    model, _ = load_candidate_model(
        evidence["candidate_artifact_path"], evidence["candidate_artifact_sha256"]
    )

    import mlflow
    import mlflow.sklearn
    from mlflow.exceptions import MlflowException
    from mlflow.tracking import MlflowClient

    tracking_uri = os.getenv("MLFLOW_TRACKING_URI")
    if not tracking_uri:
        raise ValueError("MLFLOW_TRACKING_URI must point to the isolated Capital MLflow server")
    from urllib.parse import urlsplit

    parsed_tracking_uri = urlsplit(tracking_uri)
    if (
        parsed_tracking_uri.scheme != "http"
        or parsed_tracking_uri.hostname not in {"127.0.0.1", "localhost"}
        or parsed_tracking_uri.port != 5001
        or parsed_tracking_uri.path not in {"", "/"}
        or parsed_tracking_uri.query
        or parsed_tracking_uri.fragment
    ):
        raise ValueError("Blocked candidate registration is restricted to local Capital MLflow at port 5001")
    mlflow.set_tracking_uri(tracking_uri)
    client = MlflowClient(tracking_uri=tracking_uri)
    tags = _registration_tags(evidence)

    try:
        client.get_registered_model(MODEL_NAME)
    except MlflowException as error:
        if error.error_code != "RESOURCE_DOES_NOT_EXIST":
            raise
        client.create_registered_model(MODEL_NAME)

    versions = client.search_model_versions(f"name = '{MODEL_NAME}'")
    existing = [version for version in versions if version.tags.get("candidate_id") == evidence["candidate_id"]]
    if len(existing) > 1:
        raise RuntimeError("Multiple MLflow versions already claim this immutable candidate ID")
    if existing:
        registered = existing[0]
        conflicts = [key for key, value in tags.items() if registered.tags.get(key) != value]
        if conflicts:
            raise ValueError("Existing MLflow candidate registration has conflicting evidence tags: " + ", ".join(conflicts))
        if registered.status != "READY":
            raise RuntimeError(f"Existing candidate registration is not READY: {registered.status}")
        version_number = str(registered.version)
    else:
        experiment_name = "bike-demand-capital-staging"
        experiment = mlflow.get_experiment_by_name(experiment_name)
        experiment_id = experiment.experiment_id if experiment else mlflow.create_experiment(experiment_name)
        with mlflow.start_run(experiment_id=experiment_id) as run:
            mlflow.set_tags({**tags, "staging_registration": "blocked_candidate"})
            mlflow.log_artifact(str(evidence["selection_report_path"]), artifact_path="evidence")
            mlflow.log_artifact(str(evidence["final_report_path"]), artifact_path="evidence")
            model_info = mlflow.sklearn.log_model(
                model,
                name="sklearn-model",
                skops_trusted_types=list(SKOPS_TRUSTED_TYPES),
            )
            if not model_info.model_uri:
                raise RuntimeError("MLflow did not return a URI for the candidate model")
            try:
                client.get_registered_model(MODEL_NAME)
            except MlflowException as error:
                if error.error_code != "RESOURCE_DOES_NOT_EXIST":
                    raise
                client.create_registered_model(MODEL_NAME)
            created = client.create_model_version(
                name=MODEL_NAME,
                source=model_info.model_uri,
                run_id=run.info.run_id,
                tags=tags,
                model_id=model_info.model_id,
            )
            version_number = str(created.version)

    for _ in range(60):
        registered = client.get_model_version(MODEL_NAME, version_number)
        if registered.status == "READY":
            break
        if registered.status == "FAILED_REGISTRATION":
            raise RuntimeError(f"MLflow candidate registration failed: {registered.status_message}")
        time.sleep(1)
    else:
        raise TimeoutError("Timed out waiting for blocked candidate registration")

    version_tags = registered.tags
    conflicts = [key for key, value in tags.items() if version_tags.get(key) != value]
    if conflicts:
        raise ValueError("Registered MLflow model version lost required staging tags: " + ", ".join(conflicts))
    import mlflow.artifacts

    model_uri = f"models:/{MODEL_NAME}/{version_number}"
    model_path = Path(mlflow.artifacts.download_artifacts(artifact_uri=model_uri))
    if not (model_path / "MLmodel").is_file():
        raise ValueError("Registered staging model artifact is missing MLmodel")
    registered_artifact_sha256 = directory_sha256(model_path)
    existing_artifact_sha256 = version_tags.get("artifact_sha256")
    if existing_artifact_sha256 and existing_artifact_sha256 != registered_artifact_sha256:
        raise ValueError("Registered staging model artifact hash changed")
    if not existing_artifact_sha256:
        client.set_model_version_tag(MODEL_NAME, version_number, "artifact_sha256", registered_artifact_sha256)
    staging_env = _write_staging_environment(evidence, version_number, registered_artifact_sha256)
    return {
        "model_name": MODEL_NAME,
        "model_version": version_number,
        "candidate_id": evidence["candidate_id"],
        "candidate_artifact_sha256": evidence["candidate_artifact_sha256"],
        "registered_artifact_sha256": registered_artifact_sha256,
        "candidate_report_sha256": evidence["selection_report_sha256"],
        "final_report_sha256": evidence["final_report_sha256"],
        "operational_gate_status": "not_evaluated",
        "deployment_permission": "blocked",
        "staging_environment_file": str(staging_env.relative_to(PROJECT_ROOT)),
        "tracking_uri": tracking_uri,
    }


def load_staging_registered_model():
    """Load only the exact blocked Capital candidate pre-registered for this staging service."""
    if (
        DATA_PROFILE_ID != "capital-bikeshare"
        or os.getenv("DATA_PROFILE_ID") != "capital-bikeshare"
        or MODEL_NAME != "bike-demand-capital"
    ):
        raise ValueError("The staging API requires DATA_PROFILE_ID=capital-bikeshare")
    required_environment = {
        "CAPITAL_STAGING_MODEL_VERSION": "a positive model version",
        "CAPITAL_STAGING_CANDIDATE_ID": "a candidate ID",
        "CAPITAL_STAGING_CANDIDATE_ARTIFACT_SHA256": "a candidate artifact SHA-256",
        "CAPITAL_STAGING_SELECTION_REPORT_SHA256": "a selection report SHA-256",
        "CAPITAL_STAGING_FINAL_REPORT_SHA256": "a final report SHA-256",
        "CAPITAL_STAGING_EVALUATION_PROTOCOL_SHA256": "an evaluation protocol SHA-256",
        "CAPITAL_STAGING_REGISTERED_ARTIFACT_SHA256": "a registered artifact SHA-256",
    }
    missing = [name for name in required_environment if not os.getenv(name)]
    if missing:
        raise ValueError("Staging API identity is incomplete: " + ", ".join(missing))
    version = os.environ["CAPITAL_STAGING_MODEL_VERSION"]
    if not version.isdecimal() or int(version) <= 0 or os.getenv("MODEL_VERSION") != version:
        raise ValueError("Staging model version must be positive and match MODEL_VERSION")

    import mlflow
    import mlflow.sklearn
    from mlflow.tracking import MlflowClient

    tracking_uri = os.getenv("MLFLOW_TRACKING_URI")
    if not tracking_uri:
        raise ValueError("MLFLOW_TRACKING_URI is required")
    mlflow.set_tracking_uri(tracking_uri)
    registered = MlflowClient(tracking_uri=tracking_uri).get_model_version(MODEL_NAME, version)
    if registered.status != "READY":
        raise ValueError(f"Staging candidate registration is not READY: {registered.status}")
    expected_tags = {
        "candidate_id": os.environ["CAPITAL_STAGING_CANDIDATE_ID"],
        "candidate_model_sha256": os.environ["CAPITAL_STAGING_CANDIDATE_ARTIFACT_SHA256"],
        "candidate_report_sha256": os.environ["CAPITAL_STAGING_SELECTION_REPORT_SHA256"],
        "final_report_sha256": os.environ["CAPITAL_STAGING_FINAL_REPORT_SHA256"],
        "evaluation_protocol_sha256": os.environ["CAPITAL_STAGING_EVALUATION_PROTOCOL_SHA256"],
        "artifact_sha256": os.environ["CAPITAL_STAGING_REGISTERED_ARTIFACT_SHA256"],
    }
    validate_staging_model_tags(registered.tags, expected_tags)
    model_uri = f"models:/{MODEL_NAME}/{version}"
    model_path = Path(mlflow.artifacts.download_artifacts(artifact_uri=model_uri))
    if not (model_path / "MLmodel").is_file():
        raise ValueError("Registered staging model is missing MLmodel")
    actual_hash = directory_sha256(model_path)
    if actual_hash != expected_tags["artifact_sha256"]:
        raise ValueError("Staging model artifact SHA-256 does not match the pinned environment identity")
    model = mlflow.sklearn.load_model(str(model_path))
    return model, version, actual_hash, "passed", None
