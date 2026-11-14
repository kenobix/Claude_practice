"""Product components: evaluation."""
from __future__ import annotations


# ========================================================================
# candidate_artifacts
# ========================================================================

"""Immutable local storage for validation-selected candidate models."""



import gzip
import hashlib
import json
import os
import tarfile
import tempfile
from pathlib import Path
from pathlib import PurePosixPath

import pandas as pd

from bike_mlops.config import PROJECT_ROOT

TRUSTED_SKOPS_TYPES = ("sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor",)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_candidate_model(path: Path, expected_sha256: str):
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise ValueError(f"Candidate artifact SHA-256 mismatch: expected {expected_sha256}, got {actual}")
    import skops.io as sio

    untrusted = sio.get_untrusted_types(file=path)
    unexpected = sorted(set(untrusted).difference(TRUSTED_SKOPS_TYPES))
    if unexpected:
        raise ValueError(f"Candidate artifact contains unreviewed Python types: {unexpected}")
    model = sio.load(path, trusted=list(TRUSTED_SKOPS_TYPES))
    return model, actual


def _source_files(project_root: Path):
    excluded = {".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", "data", "build", "dist"}
    for path in sorted(project_root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(project_root)
        if any(part in excluded or part.endswith(".egg-info") for part in relative.parts):
            continue
        if relative.name == ".env" or relative.name.startswith(".env."):
            continue
        yield path, relative


def source_tree_inventory(project_root: Path | None = None) -> tuple[str, int]:
    """Hash and count the exact regular source files included in the candidate snapshot."""
    project_root = project_root or PROJECT_ROOT
    digest = hashlib.sha256()
    count = 0
    for source, relative in _source_files(project_root):
        encoded_path = relative.as_posix().encode("utf-8")
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        digest.update(source.stat().st_size.to_bytes(8, "big"))
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        count += 1
    return digest.hexdigest(), count


def source_tree_sha256(project_root: Path | None = None) -> str:
    return source_tree_inventory(project_root)[0]


def _write_source_snapshot(destination: Path, project_root: Path | None = None) -> None:
    project_root = project_root or PROJECT_ROOT
    with destination.open("wb") as raw_file:
        with gzip.GzipFile(fileobj=raw_file, mode="wb", filename="", mtime=0, compresslevel=9) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for source, relative in _source_files(project_root):
                    info = archive.gettarinfo(str(source), arcname=relative.as_posix())
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = 0
                    with source.open("rb") as stream:
                        archive.addfile(info, stream)


def source_snapshot_inventory(snapshot_path: Path) -> tuple[str, int]:
    """Validate and hash the source files inside a candidate snapshot."""
    digest = hashlib.sha256()
    count = 0
    with tarfile.open(snapshot_path, "r:gz") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("Candidate source snapshot paths must be unique and sorted")
        for member in members:
            relative = PurePosixPath(member.name)
            if relative.is_absolute() or ".." in relative.parts or not member.isfile():
                raise ValueError(f"Candidate source snapshot contains an unsafe entry: {member.name!r}")
            encoded_path = relative.as_posix().encode("utf-8")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError(f"Candidate source snapshot entry cannot be read: {member.name!r}")
            digest.update(len(encoded_path).to_bytes(8, "big"))
            digest.update(encoded_path)
            digest.update(member.size.to_bytes(8, "big"))
            read_size = 0
            with stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    read_size += len(chunk)
                    digest.update(chunk)
            if read_size != member.size:
                raise ValueError(f"Candidate source snapshot entry size changed: {member.name!r}")
            count += 1
    return digest.hexdigest(), count


def candidate_identity(report: dict) -> dict:
    identity = {
        "data_sha256": report["data_sha256"],
        "source_manifest_sha256": report["data_source"].get("source_manifest_sha256"),
        "source_tree_sha256": report["source_revision"]["source_tree_sha256"],
        "source_file_count": report["source_revision"]["source_file_count"],
        "environment": report["environment"].get("direct_dependencies"),
        "feature_contract_version": report["feature_contract_version"],
        "feature_columns": report["feature_columns"],
        "hyperparameters": report["hyperparameters"],
        "training_cutoff": report["training_cutoff"],
        "validation_window": report["validation_window"],
        "incumbent_artifact_sha256": report["comparison"]["incumbent_artifact_sha256"],
        "evaluation_protocol_sha256": report.get("evaluation_protocol_sha256"),
        "training_row_keys_sha256": report["training_row_keys_sha256"],
        "validation_row_keys_sha256": report["validation_row_keys_sha256"],
        "candidate_artifact_sha256": report["candidate_artifact"]["sha256"],
        "validation_predictions_sha256": report["validation_predictions"]["sha256"],
        "source_snapshot_sha256": report["source_snapshot"]["sha256"],
        "evaluation_protocol_artifact_sha256": (
            report.get("evaluation_protocol_artifact", {}).get("sha256")
            if isinstance(report.get("evaluation_protocol_artifact"), dict)
            else None
        ),
        "quality_gate_passed": report["quality_gate_passed"],
        "quality_gate": report["quality_gate"],
        "validation": report["validation"],
        "comparison": report["comparison"],
    }
    # Preserve historical candidate IDs; new references become part of the ID.
    if "monitor_reference" in report:
        identity["monitor_reference"] = report["monitor_reference"]
    return identity


def persist_candidate_bundle(
    estimator,
    report: dict,
    validation_predictions: pd.DataFrame,
    *,
    protocol_bytes: bytes | None = None,
    training_frame: pd.DataFrame | None = None,
    training_features: pd.DataFrame | None = None,
) -> dict:
    """Atomically publish the estimator, validation evidence, protocol, and source snapshot."""
    import skops.io as sio

    expected_source_sha = report["source_revision"]["source_tree_sha256"]
    if source_tree_sha256(PROJECT_ROOT) != expected_source_sha:
        raise ValueError("Source tree changed after the candidate report was created")
    parent = PROJECT_ROOT / "data/candidates"
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".candidate-build-", dir=parent) as temporary_name:
        staging = Path(temporary_name)
        model_path = staging / "model.skops"
        prediction_path = staging / "validation-predictions.csv"
        snapshot_path = staging / "source-snapshot.tar.gz"
        sio.dump(estimator, model_path)
        validation_predictions.to_csv(prediction_path, index=False, float_format="%.17g")
        _write_source_snapshot(snapshot_path, PROJECT_ROOT)
        if source_tree_sha256(PROJECT_ROOT) != expected_source_sha:
            raise ValueError("Source tree changed while the candidate bundle was being created")
        snapshot_tree_sha, snapshot_file_count = source_snapshot_inventory(snapshot_path)
        if (
            snapshot_tree_sha != expected_source_sha
            or snapshot_file_count != report["source_revision"]["source_file_count"]
        ):
            raise ValueError("Candidate source snapshot does not match the source-tree hash in the report")
        artifact_sha = sha256_file(model_path)
        predictions_sha = sha256_file(prediction_path)
        snapshot_sha = sha256_file(snapshot_path)
        protocol_sha = None
        if protocol_bytes is not None:
            protocol_path = staging / "evaluation-protocol.json"
            protocol_path.write_bytes(protocol_bytes)
            protocol_sha = sha256_file(protocol_path)
        final_dir = parent / "pending"
        bundle_report = {
            **report,
            "candidate_artifact": {"path": "model.skops", "sha256": artifact_sha, "serialization": "skops"},
            "validation_predictions": {
                "path": "validation-predictions.csv",
                "sha256": predictions_sha,
                "rows": int(len(validation_predictions)),
                "label_available_at_column": "label_available_at",
            },
            "source_snapshot": {"path": "source-snapshot.tar.gz", "sha256": snapshot_sha},
            "evaluation_protocol_artifact": (
                {"path": "evaluation-protocol.json", "sha256": protocol_sha} if protocol_sha else None
            ),
            "candidate_bundle": None,
        }
        if report.get("data_profile_id") == "capital-bikeshare":
            from bike_mlops.monitoring import make_training_reference, reference_descriptor
            from bike_mlops.deployment import canonical_bytes

            if training_frame is None or training_features is None:
                raise ValueError("Capital candidate persistence requires the exact training rows/features")
            reference = make_training_reference(bundle_report, training_frame, training_features)
            reference_body = canonical_bytes(reference)
            (staging / "monitor-reference.json").write_bytes(reference_body)
            bundle_report["monitor_reference"] = reference_descriptor(reference, reference_body)
        identity = candidate_identity(bundle_report)
        candidate_id = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        final_dir = parent / candidate_id
        if final_dir.exists():
            raise FileExistsError(
                f"Candidate bundle already exists and is immutable: {final_dir}; do not silently replace prior evidence"
            )
        bundle_report.update({
            "candidate_id": candidate_id,
            "candidate_identity": identity,
            "candidate_bundle": str(final_dir.relative_to(PROJECT_ROOT)),
        })
        report_path = staging / "candidate-report.json"
        report_path.write_text(json.dumps(bundle_report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.rename(staging, final_dir)
    return {
        "candidate_id": candidate_id,
        "candidate_bundle": str(final_dir.relative_to(PROJECT_ROOT)),
        "candidate_report": str((final_dir / "candidate-report.json").relative_to(PROJECT_ROOT)),
        "candidate_artifact_sha256": artifact_sha,
        "validation_predictions_sha256": predictions_sha,
        "source_snapshot_sha256": snapshot_sha,
        "evaluation_protocol_sha256": protocol_sha,
    }


# ========================================================================
# evaluation_protocol
# ========================================================================

"""Frozen time-split protocols and local evaluation-window consumption ledger."""



import fcntl
import hashlib as _evaluation_protocol_hashlib
import json as _evaluation_protocol_json
import os as _evaluation_protocol_os
import tempfile as _evaluation_protocol_tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path as _evaluation_protocol_Path

import pandas as _evaluation_protocol_pd

from bike_mlops.data import validate_capital_manifest
from bike_mlops.data import DEFAULT_RAW_ARCHIVE_DIR
from bike_mlops.config import FEATURE_CONTRACT_VERSION, PROJECT_ROOT as _evaluation_protocol_PROJECT_ROOT
from bike_mlops.data import sha256_file as _evaluation_protocol_sha256_file
from bike_mlops.data import FEATURE_COLUMNS
from bike_mlops.config import _profiles_load_data_profile as load_data_profile

PROTOCOL_SCHEMA_VERSION = 2
LEGACY_PROTOCOL_SCHEMA_VERSION = 1
LEDGER_SCHEMA_VERSION = 1
DEFAULT_LEGACY_REGISTRY = _evaluation_protocol_PROJECT_ROOT / "configs/evaluation-legacy-windows.json"
DEFAULT_LEDGER = _evaluation_protocol_PROJECT_ROOT / "data/evaluations/consumption-ledger.json"

BOOTSTRAP_QUALITY_GATE_POLICY = {
    "policy_id": "capital-bootstrap-baseline-mae-rmse-v1",
    "selection": {
        "reference": "historical_baseline_only",
        "conditions": [
            "candidate.mae < historical_baseline.mae",
            "candidate.rmse <= historical_baseline.rmse",
        ],
    },
    "final_evaluation": {
        "reference": "historical_baseline_only",
        "conditions": [
            "candidate.mae < historical_baseline.mae",
            "candidate.rmse <= historical_baseline.rmse",
        ],
    },
    "segments": "diagnostic_only; report counts, candidate MAE, baseline MAE, and delta",
    "operational_gate": "separate_evidence_required; not established by accuracy metrics",
}
BOOTSTRAP_SELECTION_RULE = (
    "candidate validation MAE must be strictly below historical baseline MAE "
    "and candidate RMSE must not exceed historical baseline RMSE"
)
BOOTSTRAP_QUALITY_GATE_POLICY_SHA256 = _evaluation_protocol_hashlib.sha256(
    _evaluation_protocol_json.dumps(
        BOOTSTRAP_QUALITY_GATE_POLICY,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
).hexdigest()


def _canonical_json(value: dict) -> bytes:
    return _evaluation_protocol_json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def protocol_sha256(protocol: dict) -> str:
    body = {key: value for key, value in protocol.items() if key != "protocol_sha256"}
    return _evaluation_protocol_hashlib.sha256(_canonical_json(body)).hexdigest()


def _naive_hour(value: str | _evaluation_protocol_pd.Timestamp, name: str) -> _evaluation_protocol_pd.Timestamp:
    stamp = _evaluation_protocol_pd.Timestamp(value)
    if stamp.tzinfo is not None or stamp != stamp.floor("h"):
        raise ValueError(f"{name} must be a timezone-naive hour boundary")
    return stamp


def build_capital_protocol(
    csv_path: _evaluation_protocol_Path,
    manifest_path: _evaluation_protocol_Path,
    *,
    training_cutoff: str,
    validation_start: str,
    validation_end: str,
    evaluation_start: str,
    evaluation_end: str,
    incumbent_version: str | int | None,
    incumbent_artifact_sha256: str | None,
    incumbent_quality_gate: str | None,
    candidate_recipe: dict,
    raw_dir: _evaluation_protocol_Path = DEFAULT_RAW_ARCHIVE_DIR,
) -> dict:
    """Build a pre-candidate split protocol from the validated dataset pair."""
    manifest = _evaluation_protocol_json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest, frame = validate_capital_manifest(manifest, csv_path, raw_dir)
    if manifest.get("manifest_schema_version") != 3 or "timezone_database" not in manifest:
        raise ValueError(
            "New Capital evaluation protocols require manifest schema 3 with a locked timezone database identity; "
            "reaggregate into new output paths"
        )
    data_sha = _evaluation_protocol_sha256_file(csv_path)
    manifest_sha = _evaluation_protocol_sha256_file(manifest_path)
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
    incumbent_values = (incumbent_version, incumbent_artifact_sha256, incumbent_quality_gate)
    has_incumbent = any(value is not None for value in incumbent_values)
    if has_incumbent and not all(value is not None for value in incumbent_values):
        raise ValueError("An incumbent must be supplied as a complete version/hash/passed-gate tuple")
    if has_incumbent:
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
    data_profile = load_data_profile("capital-bikeshare")
    delay = data_profile["label_availability"]["delay_hours"]
    training_mask = (timestamps < val_start) & (timestamps + _evaluation_protocol_pd.Timedelta(hours=delay) <= cutoff)
    validation_mask = (timestamps >= val_start) & (timestamps < val_end)
    evaluation_mask = (timestamps >= eval_start) & (timestamps < eval_end)
    if not training_mask.any() or not validation_mask.any() or not evaluation_mask.any():
        raise ValueError("The selected CSV must contain at least one row in each protocol split")
    latest_validation_target = timestamps.loc[validation_mask].max()
    decision_time = latest_validation_target + _evaluation_protocol_pd.Timedelta(hours=delay)
    if eval_start < decision_time:
        raise ValueError(
            "evaluation_start precedes the time when every validation label is available "
            f"({decision_time.isoformat(sep=' ')})"
        )
    latest_training_label = timestamps.loc[training_mask].max() + _evaluation_protocol_pd.Timedelta(hours=delay)
    if latest_training_label > cutoff:
        raise AssertionError("Protocol training split contains a label unavailable at training_cutoff")
    if (validation_mask & evaluation_mask).any() or (training_mask & validation_mask).any():
        raise AssertionError("Protocol splits overlap")

    from bike_mlops.monitoring import frozen_monitor_policy

    protocol = {
        "monitoring_reference_policy": frozen_monitor_policy(),
        "schema_version": LEGACY_PROTOCOL_SCHEMA_VERSION if has_incumbent else PROTOCOL_SCHEMA_VERSION,
        "protocol_name": "capital-bikeshare-rolling-candidate-v1" if has_incumbent else "capital-bikeshare-bootstrap-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "data_profile_id": data_profile["profile_id"],
            "profile": manifest["processing"]["contract_version"],
            "target_contract": manifest["processing"]["contract_version"],
            "data_sha256": data_sha,
            "source_manifest_sha256": manifest_sha,
            "data_window": manifest["data_window"],
            "timezone_database": manifest["timezone_database"],
        },
        "features": {
            "contract_version": FEATURE_CONTRACT_VERSION,
            "columns": list(FEATURE_COLUMNS),
        },
        "candidate_recipe": candidate_recipe,
        "label_availability": {
            "delay_hours": delay,
            "rule": f"target timestamp + {delay} hours (experiment assumption)",
        },
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
        "incumbent": (
            {
                "model_version": str(incumbent_version),
                "artifact_sha256": incumbent_artifact_sha256,
                "quality_gate": incumbent_quality_gate,
            }
            if has_incumbent
            else None
        ),
        "selection_gate": (
            {
                "rule": "candidate validation MAE must strictly beat incumbent and previous-day/weekday-hour baseline",
                "final_evaluation_used_for_selection": False,
            }
            if has_incumbent
            else {
                "policy_id": BOOTSTRAP_QUALITY_GATE_POLICY["policy_id"],
                "policy_sha256": BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
                "policy": BOOTSTRAP_QUALITY_GATE_POLICY,
                "rule": BOOTSTRAP_SELECTION_RULE,
                "final_evaluation_used_for_selection": False,
            }
        ),
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
    schema_version = protocol.get("schema_version")
    if "monitoring_reference_policy" in protocol:
        from bike_mlops.monitoring import validate_frozen_monitor_policy
        validate_frozen_monitor_policy(protocol)
    if schema_version not in {LEGACY_PROTOCOL_SCHEMA_VERSION, PROTOCOL_SCHEMA_VERSION}:
        raise ValueError("Unsupported evaluation protocol schema version")
    if not isinstance(protocol.get("protocol_sha256"), str) or protocol["protocol_sha256"] != protocol_sha256(protocol):
        raise ValueError("Evaluation protocol SHA-256 mismatch; the frozen protocol was modified")
    required = ("dataset", "features", "candidate_recipe", "training", "validation", "evaluation", "incumbent", "selection_decision_time")
    if any(key not in protocol for key in required):
        raise ValueError("Evaluation protocol is missing required fields")
    if schema_version == LEGACY_PROTOCOL_SCHEMA_VERSION:
        if not isinstance(protocol.get("incumbent"), dict):
            raise ValueError("Legacy evaluation protocol must identify its incumbent model")
    else:
        gate = protocol.get("selection_gate")
        if protocol.get("protocol_name") != "capital-bikeshare-bootstrap-v1" or protocol.get("incumbent") is not None:
            raise ValueError("Schema 2 is reserved for the incumbent-free Capital bootstrap protocol")
        if (
            not isinstance(gate, dict)
            or gate.get("policy_id") != BOOTSTRAP_QUALITY_GATE_POLICY["policy_id"]
            or gate.get("policy_sha256") != BOOTSTRAP_QUALITY_GATE_POLICY_SHA256
            or gate.get("policy") != BOOTSTRAP_QUALITY_GATE_POLICY
            or gate.get("rule") != BOOTSTRAP_SELECTION_RULE
            or gate.get("final_evaluation_used_for_selection") is not False
        ):
            raise ValueError("Bootstrap quality gate policy is missing, unsupported, or modified")
    if (
        not isinstance(protocol["dataset"], dict)
        or protocol["dataset"].get("data_profile_id") != "capital-bikeshare"
        or protocol["dataset"].get("target_contract") != "capital-trip-starts-hourly-v1"
    ):
        raise ValueError("Evaluation protocol data profile/target contract is unsupported")
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
    if latest_label != latest_target + _evaluation_protocol_pd.Timedelta(hours=delay):
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


def save_protocol(protocol: dict, output: _evaluation_protocol_Path) -> _evaluation_protocol_Path:
    validated = validate_protocol(protocol)
    assert_protocol_windows_available(validated)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = _evaluation_protocol_json.dumps(validated, indent=2, ensure_ascii=False) + "\n"
    with _evaluation_protocol_tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent, delete=False) as stream:
        temporary = _evaluation_protocol_Path(stream.name)
        stream.write(payload)
        stream.flush()
        _evaluation_protocol_os.fsync(stream.fileno())
    try:
        _evaluation_protocol_os.link(temporary, output)
    except FileExistsError as error:
        raise FileExistsError(f"Refusing to overwrite frozen evaluation protocol: {output}") from error
    finally:
        temporary.unlink(missing_ok=True)
    return output


def read_protocol(path: _evaluation_protocol_Path) -> dict:
    try:
        protocol = _evaluation_protocol_json.loads(path.read_text(encoding="utf-8"))
    except (OSError, _evaluation_protocol_json.JSONDecodeError) as error:
        raise ValueError(f"Could not read evaluation protocol {path}: {error}") from error
    return validate_protocol(protocol)


def _overlap(left_start: str, left_end: str, right_start: str, right_end: str) -> bool:
    return _evaluation_protocol_pd.Timestamp(left_start) < _evaluation_protocol_pd.Timestamp(right_end) and _evaluation_protocol_pd.Timestamp(right_start) < _evaluation_protocol_pd.Timestamp(left_end)


def evaluation_window_key(protocol: dict, purpose: str) -> str:
    window = protocol["validation"] if purpose == "validation_selection" else protocol["evaluation"]
    identity = {
        "data_sha256": protocol["dataset"]["data_sha256"],
        "target_contract": protocol["dataset"]["target_contract"],
        "start_inclusive": window["start_inclusive"],
        "end_exclusive": window["end_exclusive"],
    }
    return _evaluation_protocol_hashlib.sha256(_canonical_json(identity)).hexdigest()


def _load_ledger(path: _evaluation_protocol_Path) -> dict:
    if not path.exists():
        return {"schema_version": LEDGER_SCHEMA_VERSION, "records": []}
    try:
        ledger = _evaluation_protocol_json.loads(path.read_text(encoding="utf-8"))
    except (OSError, _evaluation_protocol_json.JSONDecodeError) as error:
        raise ValueError(f"Evaluation consumption ledger is unreadable: {error}") from error
    if not isinstance(ledger, dict) or ledger.get("schema_version") != LEDGER_SCHEMA_VERSION or not isinstance(ledger.get("records"), list):
        raise ValueError("Evaluation consumption ledger has an unsupported or invalid schema")
    return ledger


def _atomic_write_json(path: _evaluation_protocol_Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with _evaluation_protocol_tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = _evaluation_protocol_Path(stream.name)
            _evaluation_protocol_json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            _evaluation_protocol_os.fsync(stream.fileno())
        _evaluation_protocol_os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _mutate_ledger(path: _evaluation_protocol_Path, mutation):
    lock = path.with_suffix(path.suffix + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = _evaluation_protocol_os.open(lock, _evaluation_protocol_os.O_CREAT | _evaluation_protocol_os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(f"Evaluation ledger is locked by another writer: {lock}") from error
        _evaluation_protocol_os.ftruncate(lock_fd, 0)
        _evaluation_protocol_os.write(lock_fd, f"pid={_evaluation_protocol_os.getpid()}\n".encode())
        _evaluation_protocol_os.fsync(lock_fd)
        ledger = _load_ledger(path)
        result = mutation(ledger)
        _atomic_write_json(path, ledger)
        return result
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            _evaluation_protocol_os.close(lock_fd)


def _legacy_conflict(protocol: dict, purpose: str, legacy_path: _evaluation_protocol_Path) -> dict | None:
    window = protocol["validation"] if purpose == "validation_selection" else protocol["evaluation"]
    if not legacy_path.exists():
        return None
    legacy = _evaluation_protocol_json.loads(legacy_path.read_text(encoding="utf-8"))
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
    ledger_path: _evaluation_protocol_Path = DEFAULT_LEDGER,
    legacy_path: _evaluation_protocol_Path = DEFAULT_LEGACY_REGISTRY,
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
    ledger_path: _evaluation_protocol_Path = DEFAULT_LEDGER,
    legacy_path: _evaluation_protocol_Path = DEFAULT_LEGACY_REGISTRY,
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
        incumbent = protocol.get("incumbent")
        attempt = {
            "attempt_id": attempt_id,
            "status": "started",
            "protocol_sha256": protocol["protocol_sha256"],
            "candidate_artifact_sha256": candidate_artifact_sha256,
            "incumbent_artifact_sha256": incumbent["artifact_sha256"] if incumbent else None,
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
    ledger_path: _evaluation_protocol_Path = DEFAULT_LEDGER,
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
    ledger_path: _evaluation_protocol_Path = DEFAULT_LEDGER,
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


def read_ledger(path: _evaluation_protocol_Path = DEFAULT_LEDGER) -> dict:
    return _load_ledger(path)


# ========================================================================
# quality_gates
# ========================================================================

"""Explicit, reportable quality-gate calculations for Capital bootstrap evaluations."""



import math
from numbers import Real

_quality_gates_BOOTSTRAP_QUALITY_GATE_POLICY = BOOTSTRAP_QUALITY_GATE_POLICY
_quality_gates_BOOTSTRAP_QUALITY_GATE_POLICY_SHA256 = BOOTSTRAP_QUALITY_GATE_POLICY_SHA256


def _read_metrics(name: str, metrics: object) -> tuple[dict[str, float] | None, list[str]]:
    if not isinstance(metrics, dict):
        return None, [f"{name} metrics are missing or are not an object"]
    values = {}
    errors = []
    for key in ("mae", "rmse"):
        value = metrics.get(key)
        if isinstance(value, bool) or not isinstance(value, Real):
            errors.append(f"{name}.{key} is missing or is not numeric")
            continue
        number = float(value)
        if not math.isfinite(number) or number < 0:
            errors.append(f"{name}.{key} must be finite and non-negative")
            continue
        values[key] = number
    return (values if not errors else None), errors


def baseline_accuracy_gate(candidate: object, baseline: object, *, period: str) -> dict:
    """Apply the frozen strict-MAE / non-worsening-RMSE policy without coercing bad data."""
    candidate_values, candidate_errors = _read_metrics("candidate", candidate)
    baseline_values, baseline_errors = _read_metrics("historical_baseline", baseline)
    errors = [*candidate_errors, *baseline_errors]
    if errors:
        return {
            "period": period,
            "status": "not_evaluable",
            "passed": False,
            "policy_id": _quality_gates_BOOTSTRAP_QUALITY_GATE_POLICY["policy_id"],
            "policy_sha256": _quality_gates_BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
            "candidate": candidate_values,
            "historical_baseline": baseline_values,
            "conditions": {},
            "reasons": errors,
        }

    assert candidate_values is not None and baseline_values is not None
    mae_better = candidate_values["mae"] < baseline_values["mae"]
    rmse_not_worse = candidate_values["rmse"] <= baseline_values["rmse"]
    conditions = {
        "candidate_mae_strictly_below_baseline": {
            "passed": mae_better,
            "candidate": candidate_values["mae"],
            "historical_baseline": baseline_values["mae"],
            "operator": "<",
        },
        "candidate_rmse_not_above_baseline": {
            "passed": rmse_not_worse,
            "candidate": candidate_values["rmse"],
            "historical_baseline": baseline_values["rmse"],
            "operator": "<=",
        },
    }
    passed = mae_better and rmse_not_worse
    reasons = []
    if not mae_better:
        reasons.append("candidate MAE must be strictly below historical baseline MAE")
    if not rmse_not_worse:
        reasons.append("candidate RMSE must not exceed historical baseline RMSE")
    return {
        "period": period,
        "status": "passed" if passed else "failed",
        "passed": passed,
        "policy_id": _quality_gates_BOOTSTRAP_QUALITY_GATE_POLICY["policy_id"],
        "policy_sha256": _quality_gates_BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
        "candidate": candidate_values,
        "historical_baseline": baseline_values,
        "conditions": conditions,
        "reasons": reasons,
    }
