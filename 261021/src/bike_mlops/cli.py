from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from bike_mlops.config import (
    DATA_PROFILE,
    DATA_PROFILE_ID,
    DATASET_MANIFEST,
    DATASET_ARCHIVE,
    DEFAULT_DATASET,
    DEFAULT_DATABASE_URL,
    DEFAULT_MLFLOW_URI,
    FEATURE_CONTRACT_VERSION,
    MODEL_NAME,
    PROJECT_ROOT,
    REPORT_DIR,
    RUNTIME_DIR,
    TARGET_CONTRACT_ID,
)
from bike_mlops.data import audit_hourly, download_dataset, load_hourly, sha256_file
from bike_mlops.db import Database, canonical_time, stable_hash
from bike_mlops.deployment_gates import require_deployment_eligible
from bike_mlops.features import FEATURE_COLUMNS, available_observation_frame, feature_frame_for_dataset
from bike_mlops.monitoring import score_monitor_windows
from bike_mlops.monitor_persistence import persist_monitor_run
from bike_mlops.monitoring_window import resolve_monitor_window


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _database() -> Database:
    database = Database(os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL))
    database.initialize()
    return database


def _load_profile_hourly(data_path: Path) -> pd.DataFrame:
    if DATA_PROFILE_ID != "capital-bikeshare":
        return load_hourly(data_path)
    if data_path.resolve() != DEFAULT_DATASET.resolve():
        raise ValueError("Capital data operations must use the active profile's configured CSV path")
    try:
        manifest = json.loads(DATASET_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Could not read the active Capital source manifest: {error}") from error
    from bike_mlops.capital_data import validate_capital_manifest

    manifest, frame = validate_capital_manifest(manifest, data_path)
    if manifest["processing"]["contract_version"] != TARGET_CONTRACT_ID:
        raise ValueError("Capital source manifest target contract differs from the active profile")
    return frame


def _seed_observations(database: Database, data_path: Path) -> tuple[int, str, pd.DataFrame]:
    frame = _load_profile_hourly(data_path)
    data_hash = sha256_file(data_path)
    observations = available_observation_frame(frame)
    added = database.add_observations(observations, data_hash)
    return added, data_hash, frame


def command_data_download(args) -> None:
    if DATA_PROFILE_ID != "uci-bike-sharing":
        raise ValueError(
            "The built-in downloader is only for UCI. Capital data must first be prepared from monthly archives "
            "with scripts/prepare_capitalbikeshare.py and a matching manifest."
        )
    _print(download_dataset(args.archive, args.csv, args.manifest))


def command_data_audit(args) -> None:
    frame = _load_profile_hourly(args.csv)
    audit = audit_hourly(frame, sha256_file(args.csv))
    report_path = args.output or REPORT_DIR / "data-audit.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _print({"report_path": report_path, **audit})


def command_observations_seed(args) -> None:
    database = _database()
    added, data_hash, frame = _seed_observations(database, args.csv)
    _print({"inserted": added, "rows": len(frame), "data_sha256": data_hash})


def command_train(args) -> None:
    from bike_mlops.training import train

    report = train(
        args.csv,
        register=not args.no_register,
        evaluate_final_holdout=args.evaluate_final_holdout,
    )
    report_path = args.output or REPORT_DIR / f"training-{report.get('registration', {}).get('run_id', 'unregistered')}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _print({"report_path": report_path, **report})


def command_candidate(args) -> None:
    from bike_mlops.training import train_candidate

    report = train_candidate(
        data_path=args.csv,
        incumbent_version=args.incumbent_version,
        validation_start=args.validation_start,
        validation_end=args.validation_end,
        training_cutoff=args.training_cutoff,
        source_manifest_path=args.source_manifest,
        register=False,
        evaluation_protocol_path=args.evaluation_protocol,
    )
    report_path = PROJECT_ROOT / report["candidate_artifacts"]["candidate_report"]
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(report, output, indent=2, ensure_ascii=False)
            output.write("\n")
        report_path = args.output
    _print({"report_path": report_path, **report})


def command_runtime_init(args) -> None:
    version = str(args.version)
    if int(version) <= 0:
        raise ValueError("Runtime requires a positive, explicitly registered model version")
    if not args.approver.strip() or not args.reason.strip():
        raise ValueError("Bootstrap requires a named approver and a reason")
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    state_path = RUNTIME_DIR / "deployment-state.json"
    if state_path.exists():
        raise FileExistsError("Runtime is already initialized; use deploy promote instead")
    registered_version = _model_registry_client().get_model_version(MODEL_NAME, int(version))
    if registered_version.status != "READY":
        raise ValueError(f"Model version {version} is not READY in MLflow")
    quality_gate = registered_version.tags.get("quality_gate")
    _assert_registered_model_profile(registered_version)
    require_deployment_eligible(DATA_PROFILE_ID, registered_version.tags)
    if quality_gate != "passed" and not args.diagnostic:
        raise ValueError("Initial runtime model did not pass the validation quality gate")
    if not registered_version.tags.get("artifact_sha256"):
        raise ValueError("Initial runtime model has no artifact SHA-256")
    (RUNTIME_DIR / "blue.env").write_text(f"MODEL_VERSION={version}\n", encoding="utf-8")
    (RUNTIME_DIR / "green.env").write_text(f"MODEL_VERSION={version}\n", encoding="utf-8")
    caddyfile = RUNTIME_DIR / "Caddyfile"
    _write_atomic(caddyfile, ":8080 {\n  reverse_proxy api-blue:8000\n}\n")
    deployment_id = str(uuid.uuid4())
    database = _database()
    inserted, data_hash, _ = _seed_observations(database, DEFAULT_DATASET)
    now = datetime.now().astimezone().isoformat()
    state = {
        "active_slot": "blue",
        "active_version": version,
        "data_profile_id": DATA_PROFILE_ID,
        "target_contract_id": TARGET_CONTRACT_ID,
        "deployment_mode": "approved" if quality_gate == "passed" else "diagnostic",
        "previous_versions": [],
        "last_deployment_id": deployment_id,
        "approver": args.approver,
        "reason": args.reason,
        "initialized_at": datetime.now().astimezone().isoformat(),
        "note": "Runtime seed; start compose stack and verify /version before accepting traffic.",
    }
    _write_atomic(state_path, json.dumps(state, indent=2) + "\n")
    try:
        with database.transaction(immediate=database.sqlite) as connection:
            database.execute(
                connection,
                "INSERT INTO deployments(deployment_id,status,phase,data_profile_id,target_contract_id,"
                "from_slot,to_slot,from_version,to_version,approver,reason,error,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    deployment_id, "completed", "bootstrap" if quality_gate == "passed" else "bootstrap_diagnostic",
                    DATA_PROFILE_ID, TARGET_CONTRACT_ID, None, "blue", None, version,
                    args.approver, args.reason, None, now, now,
                ),
            )
    except Exception:
        state_path.unlink(missing_ok=True)
        raise
    _print(
        {
            "runtime_dir": RUNTIME_DIR,
            "deployment_id": deployment_id,
            "active_slot": "blue",
            "model_version": version,
            "observations_inserted": inserted,
            "data_sha256": data_hash,
        }
    )


def _url_json(url: str, payload: dict | None = None, timeout: float = 5.0) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def command_replay(args) -> None:
    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)
    if start.tzinfo or end.tzinfo or start >= end:
        raise ValueError("Replay start/end must be naive timestamps with start < end")
    if start != start.floor("h") or end != end.floor("h"):
        raise ValueError("Replay boundaries must align to the start of an hour")
    database = _database()
    inserted, data_hash, frame = _seed_observations(database, args.csv)
    version = str(args.model_version)
    if int(version) <= 0:
        raise ValueError("Replay requires a positive explicit registered model version")
    api_url = args.api_url.rstrip("/")
    serving = _url_json(f"{api_url}/version")
    if str(serving.get("model_version")) != version:
        raise RuntimeError(
            f"API serves model version {serving.get('model_version')}; requested version is {version}"
        )
    if not _version_matches_active_profile(serving):
        raise RuntimeError("API data profile/target contract differs from the selected replay data")
    selected = frame.loc[(frame["timestamp"] >= start) & (frame["timestamp"] < end)]
    if args.limit:
        selected = selected.head(args.limit)
    replay_id = f"{data_hash[:12]}-{start:%Y%m%d%H}-{end:%Y%m%d%H}"
    sent = 0
    errors = 0
    error_status_counts = {}
    latencies_ms = []
    for row in selected.itertuples(index=False):
        timestamp = pd.Timestamp(row.timestamp)
        body = {
            "request_id": f"replay:{replay_id}:v{version}:{timestamp:%Y%m%dT%H}",
            "target_hour": timestamp.isoformat(),
            "as_of": timestamp.isoformat(),
            "feature_contract_version": FEATURE_CONTRACT_VERSION,
            "data_profile_id": DATA_PROFILE_ID,
            "target_contract_id": TARGET_CONTRACT_ID,
        }
        request_started = time.perf_counter()
        try:
            _url_json(f"{api_url}/predict", body, timeout=args.timeout)
        except urllib.error.HTTPError as error:
            errors += 1
            status_code = str(error.code)
            error_status_counts[status_code] = error_status_counts.get(status_code, 0) + 1
            error.read()
        latencies_ms.append((time.perf_counter() - request_started) * 1000.0)
        sent += 1
        if args.progress_every and sent % args.progress_every == 0:
            print(f"replay requests: {sent}/{len(selected)}", file=sys.stderr)
    summary = {
        "replay_id": replay_id,
        "data_profile_id": DATA_PROFILE_ID,
        "target_contract_id": TARGET_CONTRACT_ID,
        "model_version": version,
        "data_sha256": data_hash,
        "start": start,
        "end_exclusive": end,
        "prediction_requests": sent,
        "api_http_errors": errors,
        "api_http_error_rate": errors / sent if sent else None,
        "api_http_error_status_counts": error_status_counts,
        "api_request_latency_ms": {
            "p50": float(np.percentile(latencies_ms, 50)) if latencies_ms else None,
            "p95": float(np.percentile(latencies_ms, 95)) if latencies_ms else None,
            "measurement": "client-observed wall time for POST /predict; includes local network and DB/model work",
        },
        "observations_inserted": inserted,
        "observation_rows": len(frame),
        "api_url": api_url,
        "limitations": "Historical replay requests; source rows carry available_at and are filtered at prediction time.",
    }
    report_path = REPORT_DIR / f"replay-{replay_id}-v{version}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    summary["report_path"] = report_path
    _print(summary)


def command_monitor(args) -> None:
    frame = load_hourly(args.csv)
    data_hash = sha256_file(args.csv)
    version = str(args.model_version)
    start, end, window_source = resolve_monitor_window(
        replay_id=args.replay_id, model_version=version, data_sha256=data_hash,
        profile_id=DATA_PROFILE_ID, target_contract_id=TARGET_CONTRACT_ID,
        report_dir=REPORT_DIR, start=args.start, end=args.end,
    )
    database = _database()
    request_prefix = f"replay:{args.replay_id}:v{version}:"
    predictions = pd.DataFrame(
        database.list_predictions(start.isoformat(sep=" "), end.isoformat(sep=" "), version, request_prefix)
    )
    if predictions.empty:
        raise ValueError("No persisted predictions matched this replay ID, version, and time window")
    with database.session() as connection:
        source_hashes = {
            row["data_hash"]
            for row in database.execute(connection, "SELECT DISTINCT data_hash FROM observations").fetchall()
        }
    if data_hash not in source_hashes:
        raise ValueError("Operation database does not contain observations from the requested source CSV hash")
    source_counts = dict(zip(frame["timestamp"], frame["cnt"].astype(int)))
    for prediction in predictions.itertuples(index=False):
        target = pd.Timestamp(prediction.target_hour)
        feature_sources = {}
        for lag_hours in (24, 168):
            source_timestamp = target - pd.Timedelta(hours=lag_hours)
            count = source_counts.get(source_timestamp)
            feature_sources[f"lag_{lag_hours}"] = (
                {
                    "timestamp": canonical_time(source_timestamp),
                    "cnt": int(count),
                    "data_hash": data_hash,
                }
                if count is not None
                else None
            )
        if stable_hash(feature_sources) != prediction.data_version_sha256:
            raise ValueError(
                f"Persisted feature source hash does not match the selected CSV for {prediction.target_hour}"
            )
    observations = available_observation_frame(frame)
    reference_config = DATA_PROFILE.get("monitor_reference")
    if not isinstance(reference_config, dict):
        raise ValueError(
            f"Data profile {DATA_PROFILE_ID!r} has no frozen monitoring reference; "
            "monitoring must wait until the candidate training reference is approved and recorded"
        )
    reference_start = pd.Timestamp(reference_config["start_inclusive"])
    reference_end = pd.Timestamp(reference_config["end_exclusive"])
    if reference_start.tzinfo is not None or reference_end.tzinfo is not None or reference_start >= reference_end:
        raise ValueError("The profile monitoring reference has invalid local-time boundaries")
    reference_mask = (frame["timestamp"] >= reference_start) & (frame["timestamp"] < reference_end)
    if not reference_mask.any():
        raise ValueError("Selected CSV does not contain the profile's frozen monitoring reference period")
    reference = feature_frame_for_dataset(frame).loc[reference_mask].reset_index(drop=True)
    client = _model_registry_client()
    registered_version = client.get_model_version(MODEL_NAME, int(version))
    candidate_validation_mae = registered_version.tags.get("validation_mae")
    validation_mae = float(candidate_validation_mae) if candidate_validation_mae else None
    windows = score_monitor_windows(
        predictions,
        observations,
        reference,
        start,
        end,
        window_hours=args.window_hours,
        minimum_labels=args.minimum_labels,
        validation_mae=validation_mae,
    )
    monitor_run_id = str(uuid.uuid4())
    output = args.output or REPORT_DIR / f"monitor-{args.replay_id}-v{version}-{monitor_run_id}.csv"
    summary = windows.iloc[-1].to_dict()
    summary = {
        key: (None if isinstance(value, (float, np.floating)) and pd.isna(value) else value)
        for key, value in summary.items()
    }
    report = {
        "replay_id": args.replay_id,
        "model_name": MODEL_NAME,
        "model_version": version,
        "data_sha256": data_hash,
        "prediction_rows": int(len(predictions)),
        "window_count": int(len(windows)),
        "data_profile_id": DATA_PROFILE_ID,
        "target_contract_id": TARGET_CONTRACT_ID,
        "monitor_period": {"start_inclusive": start.isoformat(sep=" "),
                           "end_exclusive": end.isoformat(sep=" "), **window_source},
        "reference": {
            **reference_config,
            "data_sha256": data_hash,
        },
        "performance_reference_mae": validation_mae,
        "thresholds": {
            "minimum_label_count": args.minimum_labels,
            "error_investigation_ratio": 1.25,
            "psi_investigation_value": 0.2,
            "window_hours": args.window_hours,
        },
        "automatic_promotion": False,
        "latest_window": summary,
        "csv_report": output.name,
        "interpretation": (
            "Labels are scored only after available_at. Distribution change and error increase are separate signals."
        ),
    }
    persisted = persist_monitor_run(database, windows, output, report, run_id=monitor_run_id)
    _print(
        {
            **persisted,
            "replay_id": args.replay_id,
            "windows": len(windows),
            "latest_window": summary,
            "late_labels_are_excluded_until_available_at": True,
            "distribution_change_is_not_equivalent_to_error_increase": True,
            "candidate_validation_mae_threshold_reference": validation_mae,
        }
    )


def _model_registry_client():
    import mlflow
    from mlflow.tracking import MlflowClient

    uri = os.getenv("MLFLOW_TRACKING_URI", DEFAULT_MLFLOW_URI)
    if uri:
        mlflow.set_tracking_uri(uri)
    return MlflowClient(tracking_uri=mlflow.get_tracking_uri())


def _assert_registered_model_profile(model_version) -> None:
    registered_profile = model_version.tags.get("data_profile_id")
    registered_target = model_version.tags.get("target_contract_id")
    if DATA_PROFILE_ID != "uci-bike-sharing" and (
        registered_profile != DATA_PROFILE_ID or registered_target != TARGET_CONTRACT_ID
    ):
        raise ValueError(
            f"Model version profile/target contract does not match active runtime "
            f"{DATA_PROFILE_ID}/{TARGET_CONTRACT_ID}"
        )
    if registered_profile and registered_profile != DATA_PROFILE_ID:
        raise ValueError("Model version belongs to a different data profile")
    if registered_target and registered_target != TARGET_CONTRACT_ID:
        raise ValueError("Model version belongs to a different target contract")


def _deployment_row(
    database, deployment_id: str, phase: str, status_value: str = "in_progress",
    error=None, probe: dict | None = None,
):
    now = datetime.now().astimezone().isoformat()
    with database.transaction(immediate=database.sqlite) as connection:
        database.execute(
            connection,
            "UPDATE deployments SET phase=?,status=?,error=?,probe_json=COALESCE(?,probe_json),updated_at=? "
            "WHERE deployment_id=?",
            (
                phase, status_value, error,
                json.dumps(probe, sort_keys=True) if probe is not None else None,
                now, deployment_id,
            ),
        )


def _compose(*args, check=True):
    command = ["docker", "compose"]
    if DATA_PROFILE_ID == "capital-bikeshare":
        command.extend(
            ["-p", "bike-mlops-capital", "-f", "compose.yaml", "-f", "compose.capital.yaml"]
        )
    command.extend(args)
    return subprocess.run(
        command, cwd=PROJECT_ROOT, check=check, text=True,
        stdout=subprocess.PIPE if check else None, stderr=subprocess.STDOUT if check else None,
    )


def _proxy_url() -> str:
    return f"http://127.0.0.1:{DATA_PROFILE['runtime_ports']['proxy']}"


def _version_matches_active_profile(version_payload: dict) -> bool:
    # Legacy UCI images predate profile fields; UCI remains the default profile.
    if (
        DATA_PROFILE_ID == "uci-bike-sharing"
        and version_payload.get("data_profile_id") is None
        and version_payload.get("target_contract_id") is None
    ):
        return True
    return (
        version_payload.get("data_profile_id") == DATA_PROFILE_ID
        and version_payload.get("target_contract_id") == TARGET_CONTRACT_ID
    )


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def _wait_ready(url: str, version: str, quality_gate: str, timeout_seconds: int = 90) -> dict:
    until = time.monotonic() + timeout_seconds
    last_error = None
    while time.monotonic() < until:
        try:
            ready = _url_json(f"{url}/ready", timeout=3)
            actual = _url_json(f"{url}/version", timeout=3)
            if not _version_matches_active_profile(actual):
                raise RuntimeError("Candidate API data profile/target contract does not match this deployment")
            if str(actual.get("model_version")) != version:
                raise RuntimeError(f"Expected model version {version}, got {actual.get('model_version')}")
            if actual.get("quality_gate_status") != quality_gate:
                raise RuntimeError(
                    f"Expected quality gate {quality_gate}, got {actual.get('quality_gate_status')}"
                )
            return {"ready": ready, "version": actual}
        except Exception as error:
            last_error = error
            time.sleep(2)
    raise TimeoutError(f"Candidate did not become ready at {url}: {last_error}")


def _deployment_probe(url: str, version: str, deployment_id: str, frame: pd.DataFrame) -> dict:
    from bike_mlops.model import load_registered_model, predict_non_negative

    feature_frame = feature_frame_for_dataset(frame)
    complete_lags = (feature_frame["lag_24_missing"] == 0) & (feature_frame["lag_168_missing"] == 0)
    eligible = frame.index[complete_lags]
    if len(eligible) == 0:
        raise ValueError("No profile-specific probe row with both exact lag features is available")
    index = int(eligible[0])
    target = pd.Timestamp(frame.loc[index, "timestamp"])
    feature_values = feature_frame.loc[index, list(FEATURE_COLUMNS)].to_dict()
    model, model_hash, model_gate = load_registered_model(MODEL_NAME, version)
    if model_gate not in {"passed", "failed"}:
        raise ValueError(f"Registered model version {version} has an invalid quality-gate status")
    expected = predict_non_negative(model, pd.DataFrame([feature_values], columns=FEATURE_COLUMNS))
    payload = {
        "request_id": f"deploy-probe:{deployment_id}",
        "target_hour": target.isoformat(),
        "as_of": target.isoformat(),
        "feature_contract_version": FEATURE_CONTRACT_VERSION,
        "data_profile_id": DATA_PROFILE_ID,
        "target_contract_id": TARGET_CONTRACT_ID,
    }
    actual = _url_json(f"{url}/predict", payload, timeout=10)
    if str(actual.get("model_version")) != version:
        raise RuntimeError("Candidate probe response reports a different model version")
    if actual.get("artifact_sha256") != model_hash:
        raise RuntimeError("Candidate probe response reports a different model artifact hash")
    if actual.get("model_quality_gate") != model_gate:
        raise RuntimeError("Candidate probe response reports a different quality-gate status")
    actual_prediction = float(actual["prediction"])
    if not np.isclose(expected, actual_prediction, rtol=1e-6, atol=1e-5):
        raise RuntimeError(
            f"Offline/API parity check failed: expected {expected}, API returned {actual_prediction}"
        )
    return {
        "data_profile_id": DATA_PROFILE_ID,
        "target_contract_id": TARGET_CONTRACT_ID,
        "target_hour": target.isoformat(),
        "offline_prediction": expected,
        "api_prediction": actual_prediction,
        "absolute_difference": abs(expected - actual_prediction),
        "tolerance": {"relative": 1e-6, "absolute": 1e-5},
        "model_version": version,
        "artifact_sha256": model_hash,
    }


def _promote(version: str, approver: str, reason: str, rollback: bool, diagnostic: bool) -> dict:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = RUNTIME_DIR / "deployment.lock"
    with lock_path.open("a+") as lock_file:
        import fcntl

        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        state_path = RUNTIME_DIR / "deployment-state.json"
        if not state_path.exists():
            raise RuntimeError("Run 'mlops-bikectl runtime init --version N' before deployment")
        state = _read_json(state_path)
        old_slot = state["active_slot"]
        old_version = str(state["active_version"])
        old_state_contents = state_path.read_text(encoding="utf-8")
        live_before = _url_json(f"{_proxy_url()}/version", timeout=5)
        if str(live_before.get("model_version")) != old_version:
            raise RuntimeError(
                f"Runtime state says {old_version} but the proxy serves {live_before.get('model_version')}; run deploy recover"
            )
        if not _version_matches_active_profile(live_before):
            raise RuntimeError("Live proxy serves a different data profile/target contract")
        if not approver.strip() or not reason.strip():
            raise ValueError("Promotion requires a named approver and a non-empty reason")
        if version == old_version:
            raise ValueError("Requested model version is already active")
        if rollback and version not in state.get("previous_versions", []):
            raise ValueError("Rollback target has no recorded history as a previously active model")
        client = _model_registry_client()
        model_version = client.get_model_version(MODEL_NAME, int(version))
        _assert_registered_model_profile(model_version)
        if model_version.status != "READY":
            raise ValueError(f"Model version {version} is not READY in MLflow")
        if not model_version.tags.get("artifact_sha256"):
            raise ValueError(f"Model version {version} has no artifact SHA-256")
        require_deployment_eligible(DATA_PROFILE_ID, model_version.tags)
        quality_gate = model_version.tags.get("quality_gate")
        if quality_gate != "passed" and not (rollback or diagnostic):
            raise ValueError("Model version did not pass the validation quality gate; use --diagnostic only for a controlled local test")
        if quality_gate != "passed" and rollback and not diagnostic:
            # A manual rollback to a previously active diagnostic version remains diagnostic.
            diagnostic = True
        new_slot = "green" if old_slot == "blue" else "blue"
        service = f"api-{new_slot}"
        port_key = "api_green" if new_slot == "green" else "api_blue"
        host_port = DATA_PROFILE["runtime_ports"][port_key]
        old_caddyfile = f":8080 {{\n  reverse_proxy api-{old_slot}:8000\n}}\n"
        new_caddyfile = f":8080 {{\n  reverse_proxy api-{new_slot}:8000\n}}\n"
        deployment_id = str(uuid.uuid4())
        database = _database()
        if not DEFAULT_DATASET.exists():
            raise FileNotFoundError(
                f"Prepare and audit the active profile CSV before model promotion: {DEFAULT_DATASET}"
            )
        _, replay_data_hash, replay_frame = _seed_observations(database, DEFAULT_DATASET)
        now = datetime.now().astimezone().isoformat()
        with database.transaction(immediate=database.sqlite) as connection:
            database.execute(
                connection,
                "INSERT INTO deployments(deployment_id,status,phase,data_profile_id,target_contract_id,"
                "from_slot,to_slot,from_version,to_version,approver,reason,error,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    deployment_id, "in_progress", "prepared", DATA_PROFILE_ID, TARGET_CONTRACT_ID,
                    old_slot, new_slot, old_version,
                    version, approver, reason, None, now, now,
                ),
            )
        (RUNTIME_DIR / f"{new_slot}.env").write_text(f"MODEL_VERSION={version}\n", encoding="utf-8")
        try:
            _compose("up", "-d", "--build", "--no-deps", service)
            candidate = _wait_ready(f"http://127.0.0.1:{host_port}", version, quality_gate)
            probe = _deployment_probe(f"http://127.0.0.1:{host_port}", version, deployment_id, replay_frame)
            _deployment_row(database, deployment_id, "candidate_ready", probe=probe)
            _write_atomic(RUNTIME_DIR / "Caddyfile", new_caddyfile)
            _compose("exec", "-T", "caddy", "caddy", "validate", "--config", "/runtime/Caddyfile", "--adapter", "caddyfile")
            _compose("exec", "-T", "caddy", "caddy", "reload", "--config", "/runtime/Caddyfile", "--adapter", "caddyfile")
            _deployment_row(database, deployment_id, "proxy_switched")
            active = _url_json(f"{_proxy_url()}/version", timeout=5)
            if str(active.get("model_version")) != version:
                raise RuntimeError(f"Proxy verification returned model version {active.get('model_version')}")
            if not _version_matches_active_profile(active):
                raise RuntimeError("Proxy switched to a different data profile/target contract")
            state["active_slot"] = new_slot
            state["active_version"] = version
            state["deployment_mode"] = "approved" if quality_gate == "passed" else "diagnostic"
            state.setdefault("previous_versions", []).insert(0, old_version)
            state["previous_versions"] = list(dict.fromkeys(state["previous_versions"]))[:10]
            state["last_deployment_id"] = deployment_id
            state["updated_at"] = datetime.now().astimezone().isoformat()
            _write_atomic(state_path, json.dumps(state, indent=2) + "\n")
            _deployment_row(database, deployment_id, "completed", "completed")
            return {
                "deployment_id": deployment_id,
                "status": "completed",
                "old_version": old_version,
                "new_version": version,
                "active_slot": new_slot,
                "deployment_mode": state["deployment_mode"],
                "candidate": candidate,
                "probe": probe,
                "data_sha256": replay_data_hash,
                "proxy_version": active,
                "approver": approver,
                "reason": reason,
                "rollback": rollback,
            }
        except Exception as error:
            try:
                _write_atomic(RUNTIME_DIR / "Caddyfile", old_caddyfile)
                _compose("exec", "-T", "caddy", "caddy", "reload", "--config", "/runtime/Caddyfile", "--adapter", "caddyfile", check=False)
                _write_atomic(state_path, old_state_contents)
            finally:
                _deployment_row(database, deployment_id, "failed", "failed", str(error))
            raise


def command_deploy_promote(args) -> None:
    approver = args.approver or getpass.getuser()
    _print(_promote(str(args.version), approver, args.reason, args.rollback, args.diagnostic))


def command_deploy_recover(args) -> None:
    state_path = RUNTIME_DIR / "deployment-state.json"
    state = _read_json(state_path)
    if DATA_PROFILE_ID != "uci-bike-sharing" and (
        state.get("data_profile_id") != DATA_PROFILE_ID
        or state.get("target_contract_id") != TARGET_CONTRACT_ID
    ):
        raise RuntimeError("Runtime state belongs to a different data profile/target contract")
    database = _database()
    with database.session() as connection:
        rows = database.execute(
            connection,
            "SELECT * FROM deployments WHERE status='in_progress' ORDER BY created_at DESC",
        ).fetchall()
    if not rows:
        _print({"status": "no_incomplete_deployments", "active_version": state["active_version"]})
        return
    row = dict(rows[0])
    actual = _url_json(f"{_proxy_url()}/version", timeout=5)
    if not _version_matches_active_profile(actual):
        raise RuntimeError("Proxy serves a different data profile/target contract; recovery stopped")
    actual_version = str(actual.get("model_version"))
    if actual_version == str(row["to_version"]):
        _write_atomic(
            RUNTIME_DIR / "Caddyfile",
            f":8080 {{\n  reverse_proxy api-{row['to_slot']}:8000\n}}\n",
        )
        _compose("exec", "-T", "caddy", "caddy", "validate", "--config", "/runtime/Caddyfile", "--adapter", "caddyfile")
        _compose("exec", "-T", "caddy", "caddy", "reload", "--config", "/runtime/Caddyfile", "--adapter", "caddyfile")
        actual = _url_json(f"{_proxy_url()}/version", timeout=5)
        if str(actual.get("model_version")) != str(row["to_version"]):
            raise RuntimeError("Proxy version changed during recovery; stopped without finalizing")
        state["active_slot"] = row["to_slot"]
        state["active_version"] = str(row["to_version"])
        state["deployment_mode"] = "approved" if actual.get("quality_gate_status") == "passed" else "diagnostic"
        state.setdefault("previous_versions", []).insert(0, str(row["from_version"]))
        state["previous_versions"] = list(dict.fromkeys(state["previous_versions"]))[:10]
        state["last_deployment_id"] = row["deployment_id"]
        _write_atomic(state_path, json.dumps(state, indent=2) + "\n")
        _deployment_row(database, row["deployment_id"], "completed_after_restart", "completed")
        resolution = "proxy is on candidate; deployment journal and runtime state finalized"
    elif actual_version == str(row["from_version"]):
        _write_atomic(
            RUNTIME_DIR / "Caddyfile",
            f":8080 {{\n  reverse_proxy api-{row['from_slot']}:8000\n}}\n",
        )
        _compose("exec", "-T", "caddy", "caddy", "validate", "--config", "/runtime/Caddyfile", "--adapter", "caddyfile")
        _compose("exec", "-T", "caddy", "caddy", "reload", "--config", "/runtime/Caddyfile", "--adapter", "caddyfile")
        actual = _url_json(f"{_proxy_url()}/version", timeout=5)
        if str(actual.get("model_version")) != str(row["from_version"]):
            raise RuntimeError("Old version could not be re-established at the proxy")
        _deployment_row(
            database,
            row["deployment_id"],
            "retry_candidate",
            "failed",
            "Interrupted before proxy switch; old version is still active, safe to retry manually.",
        )
        resolution = "old version remains active; candidate deployment can be retried"
    else:
        raise RuntimeError(
            f"Proxy serves unrecognized version {actual_version}; stopped for manual recovery"
        )
    _print({"deployment_id": row["deployment_id"], "resolution": resolution, "proxy_version": actual})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mlops-bikectl")
    subparsers = parser.add_subparsers(dest="command", required=True)

    data = subparsers.add_parser("data", help="Download and inspect the source dataset")
    data_sub = data.add_subparsers(dest="data_command", required=True)
    download = data_sub.add_parser("download")
    download.add_argument("--archive", type=Path, default=DATASET_ARCHIVE)
    download.add_argument("--csv", type=Path, default=DEFAULT_DATASET)
    download.add_argument("--manifest", type=Path, default=DATASET_MANIFEST)
    download.set_defaults(func=command_data_download)
    audit = data_sub.add_parser("audit")
    audit.add_argument("--csv", type=Path, default=DEFAULT_DATASET)
    audit.add_argument("--output", type=Path)
    audit.set_defaults(func=command_data_audit)

    observations = subparsers.add_parser("observations", help="Import timestamped source observations")
    observations_sub = observations.add_subparsers(dest="observations_command", required=True)
    seed = observations_sub.add_parser("seed")
    seed.add_argument("--csv", type=Path, default=DEFAULT_DATASET)
    seed.set_defaults(func=command_observations_seed)

    training = subparsers.add_parser("train", help="Fit, evaluate, and register one model candidate")
    training.add_argument("--csv", type=Path, default=DEFAULT_DATASET)
    training.add_argument("--output", type=Path)
    training.add_argument("--no-register", action="store_true")
    training.add_argument(
        "--evaluate-final-holdout",
        action="store_true",
        help="Score the fixed initial holdout once for the designated initial run",
    )
    training.set_defaults(func=command_train)

    candidate = subparsers.add_parser(
        "candidate", help="Fit a point-in-time candidate against its frozen incumbent or baseline-only protocol"
    )
    candidate.add_argument("--csv", type=Path, required=True)
    candidate.add_argument("--incumbent-version")
    candidate.add_argument("--training-cutoff")
    candidate.add_argument("--validation-start")
    candidate.add_argument("--validation-end")
    candidate.add_argument(
        "--source-manifest",
        type=Path,
        required=True,
        help="Manifest that identifies the selected CSV by SHA-256",
    )
    candidate.add_argument(
        "--evaluation-protocol",
        type=Path,
        required=True,
        help="Frozen, hashed split and decision-time protocol created before candidate fitting",
    )
    candidate.add_argument("--output", type=Path)
    candidate.add_argument("--no-register", action="store_true", required=True)
    candidate.set_defaults(func=command_candidate)

    runtime = subparsers.add_parser("runtime", help="Prepare local blue-green runtime files")
    runtime_sub = runtime.add_subparsers(dest="runtime_command", required=True)
    runtime_init = runtime_sub.add_parser("init")
    runtime_init.add_argument("--version", required=True)
    runtime_init.add_argument("--approver", default=getpass.getuser())
    runtime_init.add_argument("--reason", required=True)
    runtime_init.add_argument("--diagnostic", action="store_true")
    runtime_init.set_defaults(func=command_runtime_init)

    deploy = subparsers.add_parser("deploy", help="Manually approve, promote, or reconcile a model version")
    deploy_sub = deploy.add_subparsers(dest="deploy_command", required=True)
    promote = deploy_sub.add_parser("promote")
    promote.add_argument("--version", required=True)
    promote.add_argument("--approver")
    promote.add_argument("--reason", required=True)
    promote.add_argument("--rollback", action="store_true")
    promote.add_argument("--diagnostic", action="store_true")
    promote.set_defaults(func=command_deploy_promote)
    recover = deploy_sub.add_parser("recover")
    recover.set_defaults(func=command_deploy_recover)

    replay = subparsers.add_parser("replay", help="Send historical hourly requests to a pinned API")
    replay.add_argument("--csv", type=Path, default=DEFAULT_DATASET)
    replay.add_argument("--model-version", required=True)
    replay.add_argument("--start", default="2012-07-01 00:00:00")
    replay.add_argument("--end", default="2013-01-01 00:00:00")
    replay.add_argument("--api-url", default=_proxy_url())
    replay.add_argument("--timeout", type=float, default=10)
    replay.add_argument("--limit", type=int, default=0)
    replay.add_argument("--progress-every", type=int, default=250)
    replay.set_defaults(func=command_replay)

    monitor = subparsers.add_parser("monitor", help="Score delayed-label and feature-drift windows")
    monitor.add_argument("--csv", type=Path, default=DEFAULT_DATASET)
    monitor.add_argument("--replay-id", required=True)
    monitor.add_argument("--model-version", required=True)
    monitor.add_argument("--start", help="Start (inclusive); requires --end. Omit both to use the replay report")
    monitor.add_argument("--end", help="End (exclusive); requires --start. Omit both to use the replay report")
    monitor.add_argument("--window-hours", type=int, default=168)
    monitor.add_argument("--minimum-labels", type=int, default=24)
    monitor.add_argument("--output", type=Path)
    monitor.set_defaults(func=command_monitor)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except (ValueError, RuntimeError, OSError, urllib.error.URLError, subprocess.CalledProcessError) as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    main()
