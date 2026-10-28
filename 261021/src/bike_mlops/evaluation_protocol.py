"""Frozen time-split protocols and local evaluation-window consumption ledger."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from bike_mlops.capital_data import validate_capital_manifest
from bike_mlops.capital_data import DEFAULT_RAW_ARCHIVE_DIR
from bike_mlops.config import FEATURE_CONTRACT_VERSION, PROJECT_ROOT
from bike_mlops.data import sha256_file
from bike_mlops.features import FEATURE_COLUMNS

PROTOCOL_SCHEMA_VERSION = 1
LEDGER_SCHEMA_VERSION = 1
DEFAULT_LEGACY_REGISTRY = PROJECT_ROOT / "configs/evaluation-legacy-windows.json"
DEFAULT_LEDGER = PROJECT_ROOT / "data/evaluations/consumption-ledger.json"


def _canonical_json(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def protocol_sha256(protocol: dict) -> str:
    body = {key: value for key, value in protocol.items() if key != "protocol_sha256"}
    return hashlib.sha256(_canonical_json(body)).hexdigest()


def _naive_hour(value: str | pd.Timestamp, name: str) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is not None or stamp != stamp.floor("h"):
        raise ValueError(f"{name} must be a timezone-naive hour boundary")
    return stamp


def build_capital_protocol(
    csv_path: Path,
    manifest_path: Path,
    *,
    training_cutoff: str,
    validation_start: str,
    validation_end: str,
    evaluation_start: str,
    evaluation_end: str,
    incumbent_version: str | int,
    incumbent_artifact_sha256: str,
    incumbent_quality_gate: str,
    candidate_recipe: dict,
    raw_dir: Path = DEFAULT_RAW_ARCHIVE_DIR,
) -> dict:
    """Build a pre-candidate split protocol from the validated dataset pair."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest, frame = validate_capital_manifest(manifest, csv_path, raw_dir)
    data_sha = sha256_file(csv_path)
    manifest_sha = sha256_file(manifest_path)
    cutoff = _naive_hour(training_cutoff, "training_cutoff")
    val_start = _naive_hour(validation_start, "validation_start")
    val_end = _naive_hour(validation_end, "validation_end")
    eval_start = _naive_hour(evaluation_start, "evaluation_start")
    eval_end = _naive_hour(evaluation_end, "evaluation_end")
    if not val_start < val_end or not eval_start < eval_end:
        raise ValueError("Validation and evaluation windows must be non-empty and ordered")
    if cutoff > val_start:
        raise ValueError("training_cutoff must be no later than validation_start")
    if eval_start < val_end:
        raise ValueError("evaluation window must start at or after the validation window ends")
    if not isinstance(incumbent_artifact_sha256, str) or len(incumbent_artifact_sha256) != 64:
        raise ValueError("A verified incumbent artifact SHA-256 is required")
    try:
        int(incumbent_artifact_sha256, 16)
    except ValueError as error:
        raise ValueError("incumbent_artifact_sha256 must be lowercase hexadecimal") from error
    if incumbent_artifact_sha256 != incumbent_artifact_sha256.lower():
        raise ValueError("incumbent_artifact_sha256 must be lowercase hexadecimal")
    if int(incumbent_version) <= 0 or incumbent_quality_gate != "passed":
        raise ValueError("The incumbent must be an explicitly selected passed model version")

    timestamps = frame["timestamp"]
    delay = 3
    training_mask = (timestamps < val_start) & (timestamps + pd.Timedelta(hours=delay) <= cutoff)
    validation_mask = (timestamps >= val_start) & (timestamps < val_end)
    evaluation_mask = (timestamps >= eval_start) & (timestamps < eval_end)
    if not training_mask.any() or not validation_mask.any() or not evaluation_mask.any():
        raise ValueError("The selected CSV must contain at least one row in each protocol split")
    latest_validation_target = timestamps.loc[validation_mask].max()
    decision_time = latest_validation_target + pd.Timedelta(hours=delay)
    if eval_start < decision_time:
        raise ValueError(
            "evaluation_start precedes the time when every validation label is available "
            f"({decision_time.isoformat(sep=' ')})"
        )
    latest_training_label = timestamps.loc[training_mask].max() + pd.Timedelta(hours=delay)
    if latest_training_label > cutoff:
        raise AssertionError("Protocol training split contains a label unavailable at training_cutoff")
    if (validation_mask & evaluation_mask).any() or (training_mask & validation_mask).any():
        raise AssertionError("Protocol splits overlap")

    protocol = {
        "schema_version": PROTOCOL_SCHEMA_VERSION,
        "protocol_name": "capital-bikeshare-rolling-candidate-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "profile": manifest["processing"]["contract_version"],
            "target_contract": manifest["processing"]["contract_version"],
            "data_sha256": data_sha,
            "source_manifest_sha256": manifest_sha,
            "data_window": manifest["data_window"],
        },
        "features": {
            "contract_version": FEATURE_CONTRACT_VERSION,
            "columns": list(FEATURE_COLUMNS),
        },
        "candidate_recipe": candidate_recipe,
        "label_availability": {"delay_hours": delay, "rule": "target timestamp + 3 hours (experiment assumption)"},
        "training": {
            "cutoff": cutoff.isoformat(sep=" "),
            "latest_included_label_available_at": latest_training_label.isoformat(sep=" "),
            "rows": int(training_mask.sum()),
        },
        "validation": {
            "start_inclusive": val_start.isoformat(sep=" "),
            "end_exclusive": val_end.isoformat(sep=" "),
            "rows": int(validation_mask.sum()),
            "latest_target_hour": latest_validation_target.isoformat(sep=" "),
            "latest_label_available_at": decision_time.isoformat(sep=" "),
        },
        "selection_decision_time": decision_time.isoformat(sep=" "),
        "evaluation": {
            "start_inclusive": eval_start.isoformat(sep=" "),
            "end_exclusive": eval_end.isoformat(sep=" "),
            "rows": int(evaluation_mask.sum()),
            "purpose": "one_paired_out_of_time_comparison_after_candidate_selection",
        },
        "incumbent": {
            "model_version": str(incumbent_version),
            "artifact_sha256": incumbent_artifact_sha256,
            "quality_gate": incumbent_quality_gate,
        },
        "selection_gate": {
            "rule": "candidate validation MAE must strictly beat incumbent and previous-day/weekday-hour baseline",
            "final_evaluation_used_for_selection": False,
        },
        "split_counts": {
            "train": int(training_mask.sum()),
            "validation": int(validation_mask.sum()),
            "reserved_evaluation": int(evaluation_mask.sum()),
            "train_validation_overlap": 0,
            "validation_evaluation_overlap": 0,
        },
        "historic_source_aggregate_statistics_were_seen": True,
        "limitations": [
            "The label delay is a simulation assumption, not a measured feed SLA.",
            "Monthly archive min/max bounds and contiguous archive names do not prove that the publisher omitted no rides.",
            "The source target differs from the UCI incumbent target definition.",
            "The reserved period is retrospective and must be consumed once as one candidate/incumbent pair.",
        ],
    }
    protocol["protocol_sha256"] = protocol_sha256(protocol)
    return protocol


def validate_protocol(protocol: object) -> dict:
    if not isinstance(protocol, dict):
        raise ValueError("Evaluation protocol must be a JSON object")
    if protocol.get("schema_version") != PROTOCOL_SCHEMA_VERSION:
        raise ValueError("Unsupported evaluation protocol schema version")
    if not isinstance(protocol.get("protocol_sha256"), str) or protocol["protocol_sha256"] != protocol_sha256(protocol):
        raise ValueError("Evaluation protocol SHA-256 mismatch; the frozen protocol was modified")
    required = ("dataset", "features", "candidate_recipe", "training", "validation", "evaluation", "incumbent", "selection_decision_time")
    if any(key not in protocol for key in required):
        raise ValueError("Evaluation protocol is missing required fields")
    decision = _naive_hour(protocol["selection_decision_time"], "selection_decision_time")
    eval_start = _naive_hour(protocol["evaluation"]["start_inclusive"], "evaluation.start_inclusive")
    eval_end = _naive_hour(protocol["evaluation"]["end_exclusive"], "evaluation.end_exclusive")
    validation_start = _naive_hour(protocol["validation"]["start_inclusive"], "validation.start_inclusive")
    validation_end = _naive_hour(protocol["validation"]["end_exclusive"], "validation.end_exclusive")
    latest_label = _naive_hour(protocol["validation"]["latest_label_available_at"], "validation.latest_label_available_at")
    if validation_start >= validation_end or eval_start >= eval_end:
        raise ValueError("Evaluation protocol windows are not ordered")
    if eval_start < validation_end:
        raise ValueError("Evaluation window overlaps the validation window")
    if decision < latest_label or eval_start < decision:
        raise ValueError("Evaluation starts before all validation labels are available")
    delay = protocol.get("label_availability", {}).get("delay_hours")
    if isinstance(delay, bool) or not isinstance(delay, int) or delay < 0:
        raise ValueError("Evaluation protocol label delay must be a non-negative integer")
    latest_target = _naive_hour(protocol["validation"].get("latest_target_hour"), "validation.latest_target_hour")
    if latest_label != latest_target + pd.Timedelta(hours=delay):
        raise ValueError("Evaluation protocol validation label arrival does not match its delay rule")
    cutoff = _naive_hour(protocol["training"]["cutoff"], "training.cutoff")
    latest_training_label = _naive_hour(
        protocol["training"].get("latest_included_label_available_at"),
        "training.latest_included_label_available_at",
    )
    if latest_training_label > cutoff or cutoff > validation_start:
        raise ValueError("Evaluation protocol training labels were unavailable or cutoff crossed validation start")
    if not isinstance(protocol["candidate_recipe"], dict) or not isinstance(
        protocol["candidate_recipe"].get("hyperparameters"), dict
    ):
        raise ValueError("Evaluation protocol must freeze the candidate model recipe and hyperparameters")
    split_counts = protocol.get("split_counts")
    if not isinstance(split_counts, dict) or any(split_counts.get(key) != protocol[section].get("rows") for key, section in (
        ("train", "training"), ("validation", "validation"), ("reserved_evaluation", "evaluation")
    )):
        raise ValueError("Evaluation protocol split counts do not match the declared windows")
    return protocol


def save_protocol(protocol: dict, output: Path) -> Path:
    validated = validate_protocol(protocol)
    assert_protocol_windows_available(validated)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(validated, indent=2, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.link(temporary, output)
    except FileExistsError as error:
        raise FileExistsError(f"Refusing to overwrite frozen evaluation protocol: {output}") from error
    finally:
        temporary.unlink(missing_ok=True)
    return output


def read_protocol(path: Path) -> dict:
    try:
        protocol = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Could not read evaluation protocol {path}: {error}") from error
    return validate_protocol(protocol)


def _overlap(left_start: str, left_end: str, right_start: str, right_end: str) -> bool:
    return pd.Timestamp(left_start) < pd.Timestamp(right_end) and pd.Timestamp(right_start) < pd.Timestamp(left_end)


def evaluation_window_key(protocol: dict, purpose: str) -> str:
    window = protocol["validation"] if purpose == "validation_selection" else protocol["evaluation"]
    identity = {
        "data_sha256": protocol["dataset"]["data_sha256"],
        "target_contract": protocol["dataset"]["target_contract"],
        "start_inclusive": window["start_inclusive"],
        "end_exclusive": window["end_exclusive"],
    }
    return hashlib.sha256(_canonical_json(identity)).hexdigest()


def _load_ledger(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": LEDGER_SCHEMA_VERSION, "records": []}
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Evaluation consumption ledger is unreadable: {error}") from error
    if not isinstance(ledger, dict) or ledger.get("schema_version") != LEDGER_SCHEMA_VERSION or not isinstance(ledger.get("records"), list):
        raise ValueError("Evaluation consumption ledger has an unsupported or invalid schema")
    return ledger


def _atomic_write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _mutate_ledger(path: Path, mutation):
    lock = path.with_suffix(path.suffix + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(f"Evaluation ledger is locked by another writer: {lock}") from error
        os.ftruncate(lock_fd, 0)
        os.write(lock_fd, f"pid={os.getpid()}\n".encode())
        os.fsync(lock_fd)
        ledger = _load_ledger(path)
        result = mutation(ledger)
        _atomic_write_json(path, ledger)
        return result
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def _legacy_conflict(protocol: dict, purpose: str, legacy_path: Path) -> dict | None:
    window = protocol["validation"] if purpose == "validation_selection" else protocol["evaluation"]
    if not legacy_path.exists():
        return None
    legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
    if not isinstance(legacy, dict) or not isinstance(legacy.get("windows"), list):
        raise ValueError("Legacy evaluation registry must be a JSON object with a windows list")
    for record in legacy["windows"]:
        if not isinstance(record, dict):
            raise ValueError("Legacy evaluation registry entries must be objects")
        if record.get("target_contract") != protocol["dataset"]["target_contract"]:
            continue
        if _overlap(window["start_inclusive"], window["end_exclusive"], record["start_inclusive"], record["end_exclusive"]):
            return record
    return None


def assert_protocol_windows_available(
    protocol: dict,
    ledger_path: Path = DEFAULT_LEDGER,
    legacy_path: Path = DEFAULT_LEGACY_REGISTRY,
) -> None:
    protocol = validate_protocol(protocol)
    for purpose in ("validation_selection", "reserved_final_evaluation"):
        legacy_conflict = _legacy_conflict(protocol, purpose, legacy_path)
        if legacy_conflict:
            raise ValueError(
                "Cannot freeze a protocol over a previously used legacy window: "
                f"{legacy_conflict.get('record_id', 'legacy')}"
            )
        window = protocol["validation"] if purpose == "validation_selection" else protocol["evaluation"]
        if ledger_path.exists():
            ledger = _load_ledger(ledger_path)
            for previous in ledger["records"]:
                if previous.get("target_contract") == protocol["dataset"]["target_contract"] and _overlap(
                    window["start_inclusive"], window["end_exclusive"],
                    previous["start_inclusive"], previous["end_exclusive"],
                ):
                    raise ValueError(
                        "Cannot freeze a protocol over a previously consumed/started ledger window: "
                        f"{previous.get('consumption_key')}"
                    )


def begin_evaluation(
    protocol: dict,
    purpose: str,
    candidate_artifact_sha256: str | None,
    ledger_path: Path = DEFAULT_LEDGER,
    legacy_path: Path = DEFAULT_LEGACY_REGISTRY,
) -> str:
    protocol = validate_protocol(protocol)
    if purpose not in {"validation_selection", "reserved_final_evaluation"}:
        raise ValueError("Evaluation purpose must be validation_selection or reserved_final_evaluation")
    conflict = _legacy_conflict(protocol, purpose, legacy_path)
    if conflict:
        raise ValueError(
            "This time range overlaps a previously used legacy evaluation window: "
            f"{conflict.get('record_id', 'legacy')} ({conflict['start_inclusive']}..{conflict['end_exclusive']})"
        )
    key = evaluation_window_key(protocol, purpose)
    window = protocol["validation"] if purpose == "validation_selection" else protocol["evaluation"]
    attempt_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    def mutation(ledger):
        for previous in ledger["records"]:
            if previous.get("target_contract") != protocol["dataset"]["target_contract"]:
                continue
            if not _overlap(
                window["start_inclusive"], window["end_exclusive"],
                previous["start_inclusive"], previous["end_exclusive"],
            ):
                continue
            if previous.get("consumption_key") != key or previous.get("status") != "reviewed_reopen":
                raise ValueError(
                    "Evaluation window overlaps an already consumed/started period in the local ledger "
                    f"(status={previous.get('status')}, key={previous.get('consumption_key')})"
                )
        record = next((item for item in ledger["records"] if item.get("consumption_key") == key), None)
        if record is None:
            record = {
                "consumption_key": key,
                "data_sha256": protocol["dataset"]["data_sha256"],
                "target_contract": protocol["dataset"]["target_contract"],
                "start_inclusive": window["start_inclusive"],
                "end_exclusive": window["end_exclusive"],
                "purpose": purpose,
                "attempts": [],
                "status": "unused",
            }
            ledger["records"].append(record)
        elif record["status"] != "reviewed_reopen":
            raise ValueError(
                f"Evaluation window was already consumed or started (status={record['status']}, key={key})"
            )
        attempt = {
            "attempt_id": attempt_id,
            "status": "started",
            "protocol_sha256": protocol["protocol_sha256"],
            "candidate_artifact_sha256": candidate_artifact_sha256,
            "incumbent_artifact_sha256": protocol["incumbent"]["artifact_sha256"],
            "started_at_utc": now,
            "events": [{"status": "started", "at_utc": now}],
        }
        record["attempts"].append(attempt)
        record["status"] = "started"
        return attempt_id

    return _mutate_ledger(ledger_path, mutation)


def finish_evaluation(
    attempt_id: str,
    status: str,
    ledger_path: Path = DEFAULT_LEDGER,
    detail: str | None = None,
    candidate_artifact_sha256: str | None = None,
) -> None:
    if status not in {"completed", "failed"}:
        raise ValueError("Ledger finish status must be completed or failed")
    now = datetime.now(timezone.utc).isoformat()

    def mutation(ledger):
        for record in ledger["records"]:
            for attempt in record["attempts"]:
                if attempt["attempt_id"] == attempt_id:
                    if attempt["status"] != "started":
                        raise ValueError("Only a started evaluation attempt can be finished")
                    attempt["status"] = status
                    attempt["finished_at_utc"] = now
                    if candidate_artifact_sha256 is not None:
                        attempt["candidate_artifact_sha256"] = candidate_artifact_sha256
                    if detail:
                        attempt["detail"] = detail[:1000]
                    attempt["events"].append({"status": status, "at_utc": now})
                    record["status"] = status
                    return None
        raise ValueError(f"Evaluation ledger has no started attempt {attempt_id}")

    _mutate_ledger(ledger_path, mutation)


def review_reopen_evaluation(
    consumption_key: str,
    operator: str,
    reason: str,
    ledger_path: Path = DEFAULT_LEDGER,
) -> None:
    if not operator.strip() or not reason.strip():
        raise ValueError("An operator and review reason are required")
    now = datetime.now(timezone.utc).isoformat()

    def mutation(ledger):
        for record in ledger["records"]:
            if record["consumption_key"] == consumption_key:
                if record["status"] not in {"failed", "started"}:
                    raise ValueError(f"Only failed or interrupted windows can be reviewed (status={record['status']})")
                record["events"] = record.get("events", [])
                record["events"].append({
                    "status": "reviewed_reopen",
                    "at_utc": now,
                    "operator": operator.strip(),
                    "reason": reason.strip(),
                })
                record["status"] = "reviewed_reopen"
                return None
        raise ValueError(f"Evaluation ledger has no consumption key {consumption_key}")

    _mutate_ledger(ledger_path, mutation)


def read_ledger(path: Path = DEFAULT_LEDGER) -> dict:
    return _load_ledger(path)
