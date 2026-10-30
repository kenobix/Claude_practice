"""Freeze training-only PSI bins and bind them to a Capital candidate.

Hashes detect inconsistent evidence; local files and registry administrators are
trusted. No later evaluation data is used to select bins or thresholds.
"""
from __future__ import annotations

import hashlib
import copy
import math
import tempfile
from pathlib import Path

import pandas as pd

from bike_mlops.capital_deployment_evidence import canonical_bytes, digest, strict_json
from bike_mlops.features import FEATURE_COLUMNS, feature_frame_for_dataset
from bike_mlops.monitoring_psi_v2 import PSI_ALGORITHM_V2, evaluate_psi_reference, make_psi_reference
from bike_mlops.profiles import load_data_profile

PROFILE = load_data_profile("capital-bikeshare")
FEATURES = ("lag_24", "lag_168", "hour")
POLICY = {"policy_id": "capital-training-monitor-v1", "algorithm": PSI_ALGORITHM_V2,
          "bins": 10, "minimum_reference_count": 24, "minimum_current_count": 24,
          "psi_investigation_value": 0.2, "error_investigation_ratio": 1.25,
          "features": list(FEATURES)}
REFERENCE_PATH = "evidence/monitor-reference.json"
MAX_BYTES = 32 * 1024 * 1024


def frozen_monitor_policy() -> dict:
    return {"policy": copy.deepcopy(POLICY), "policy_sha256": digest(POLICY)}


def validate_frozen_monitor_policy(protocol: dict) -> None:
    if canonical_bytes(protocol.get("monitoring_reference_policy")) != canonical_bytes(frozen_monitor_policy()):
        raise ValueError("Capital fitting requires the frozen training monitoring policy in its protocol")


def row_keys_sha256(frame: pd.DataFrame) -> str:
    body = "".join(f"{t.isoformat(sep=' ')}\t{int(y)}\n" for t, y in zip(frame["timestamp"], frame["cnt"]))
    return hashlib.sha256(body.encode()).hexdigest()


def _identity(selection: dict) -> dict:
    result = {key: selection[key] for key in (
        "data_profile_id", "target_contract_id", "feature_contract_version", "feature_columns",
        "data_sha256", "evaluation_protocol_sha256", "training_row_keys_sha256", "training_cutoff",
    )}
    result.update(candidate_model_sha256=selection["candidate_artifact"]["sha256"],
                  source_manifest_sha256=selection["data_source"]["source_manifest_sha256"])
    if (result["data_profile_id"] != PROFILE["profile_id"]
            or result["target_contract_id"] != PROFILE["target_contract_id"]
            or result["feature_contract_version"] != PROFILE["feature_contract_version"]
            or result["feature_columns"] != list(FEATURE_COLUMNS)):
        raise ValueError("Training monitoring reference has a different profile/feature contract")
    for key, value in result.items():
        if key.endswith("sha256") and (not isinstance(value, str) or len(value) != 64
                                       or any(c not in "0123456789abcdef" for c in value)):
            raise ValueError(f"Training reference requires a SHA-256: {key}")
    return result


def make_training_reference(selection: dict, training: pd.DataFrame, features: pd.DataFrame) -> dict:
    identity = _identity(selection)
    training, features = training.reset_index(drop=True), features.reset_index(drop=True)
    times = pd.to_datetime(training["timestamp"], errors="raise")
    cutoff = pd.Timestamp(identity["training_cutoff"])
    validation_start = pd.Timestamp(selection["validation_window"]["start"])
    delay = PROFILE["label_availability"]["delay_hours"]
    counts = pd.to_numeric(training["cnt"], errors="raise")
    if (not len(training) or times.dt.tz is not None or cutoff.tzinfo is not None
            or validation_start.tzinfo is not None or times.isna().any()
            or pd.isna(cutoff) or pd.isna(validation_start)
            or not counts.map(math.isfinite).all() or (counts < 0).any() or (counts % 1 != 0).any()
            or not times.is_unique or not times.is_monotonic_increasing
            or (times != times.dt.floor("h")).any() or (times >= validation_start).any()
            or (times + pd.Timedelta(hours=delay) > cutoff).any()
            or len(training) != selection["split_counts"]["train"]
            or row_keys_sha256(training) != identity["training_row_keys_sha256"]
            or len(features) != len(training) or list(features.columns) != list(FEATURE_COLUMNS)):
        raise ValueError("Training reference rows differ from the candidate training split")
    # Hash the features actually supplied to fit, preserving NaN explicitly.
    feature_rows = [[None if pd.isna(value) else float(value) for value in row]
                    for row in features.itertuples(index=False, name=None)]
    if any(value is not None and not math.isfinite(value) for row in feature_rows for value in row):
        raise ValueError("Training reference features contain non-finite values")
    ref_id = digest(identity)
    reference = {
        "schema_version": 1, "kind": "capital-training-monitor-reference",
        "policy": copy.deepcopy(POLICY), "identity": identity,
        "training_window": {"start_inclusive": times.min().isoformat(sep=" "),
                            "end_exclusive": (times.max() + pd.Timedelta(hours=1)).isoformat(sep=" "),
                            "latest_label_available_at": (times.max() + pd.Timedelta(hours=delay)).isoformat(sep=" "),
                            "label_delay_hours": delay, "rows": len(training)},
        "training_features_sha256": digest({"columns": list(FEATURE_COLUMNS), "rows": feature_rows}),
        "features": {name: make_psi_reference(features[name], version=PSI_ALGORITHM_V2,
                     feature_name=name, reference_id=ref_id, bins=POLICY["bins"],
                     minimum_count=POLICY["minimum_reference_count"]) for name in FEATURES},
        "feature_missing_rates": {name: float(features[name].isna().mean()) for name in FEATURES},
    }
    reference["reference_sha256"] = digest(reference)
    return reference


def validate_training_reference(reference: dict, selection: dict, *, frame: pd.DataFrame | None = None) -> None:
    if (type(reference.get("schema_version")) is not int or reference.get("schema_version") != 1
            or reference.get("kind") != "capital-training-monitor-reference"
            or canonical_bytes(reference.get("policy")) != canonical_bytes(POLICY)
            or reference.get("identity") != _identity(selection)
            or reference.get("reference_sha256") != digest({k: v for k, v in reference.items() if k != "reference_sha256"})):
        raise ValueError("Training monitoring reference policy, hash, or candidate identity differs")
    components = reference.get("features")
    if not isinstance(components, dict) or set(components) != set(FEATURES):
        raise ValueError("Training reference is missing monitored features")
    for name, component in components.items():
        if (component.get("feature_name") != name or component.get("reference_id") != digest(reference["identity"])
                or component.get("requested_bins") != POLICY["bins"]
                or component.get("minimum_count") != POLICY["minimum_reference_count"]):
            raise ValueError("Training reference component differs from the fixed policy")
        evaluate_psi_reference(component, [], version=PSI_ALGORITHM_V2)
    window = reference.get("training_window", {})
    if (window.get("rows") != selection["split_counts"]["train"]
            or window.get("label_delay_hours") != PROFILE["label_availability"]["delay_hours"]
            or pd.Timestamp(window["latest_label_available_at"]) > pd.Timestamp(selection["training_cutoff"])):
        raise ValueError("Training reference window differs from training evidence")
    if frame is not None:
        times = frame["timestamp"]
        mask = ((times < pd.Timestamp(selection["validation_window"]["start"]))
                & (times + pd.Timedelta(hours=PROFILE["label_availability"]["delay_hours"])
                   <= pd.Timestamp(selection["training_cutoff"])))
        expected = make_training_reference(selection, frame.loc[mask], feature_frame_for_dataset(frame).loc[mask])
        if canonical_bytes(expected) != canonical_bytes(reference):
            raise ValueError("Training reference bins/features differ from the original training rows")


def read_candidate_reference(selection: dict, bundle: Path, *, frame: pd.DataFrame | None = None) -> dict:
    descriptor = selection.get("monitor_reference")
    if not isinstance(descriptor, dict) or descriptor.get("path") != "monitor-reference.json":
        raise ValueError("Candidate has no frozen training monitoring reference")
    path = bundle / descriptor["path"]
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError("Training reference must be a regular bounded file")
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != descriptor.get("sha256"):
        raise ValueError("Training monitoring reference file SHA-256 mismatch")
    reference = strict_json(body.decode())
    validate_training_reference(reference, selection, frame=frame)
    if descriptor != reference_descriptor(reference, body):
        raise ValueError("Training reference descriptor differs from its content")
    return reference


def reference_descriptor(reference: dict, body: bytes) -> dict:
    return {"path": "monitor-reference.json", "sha256": hashlib.sha256(body).hexdigest(),
            "reference_sha256": reference["reference_sha256"], "algorithm": PSI_ALGORITHM_V2, "schema_version": 1}


def reference_registry_tags(selection: dict) -> dict:
    descriptor = selection.get("monitor_reference")
    if descriptor is None:  # Historical candidates are not silently upgraded.
        return {}
    return {"monitor_reference_sha256": descriptor["sha256"],
            "monitor_reference_content_sha256": descriptor["reference_sha256"],
            "monitor_reference_path": REFERENCE_PATH, "monitor_psi_algorithm": PSI_ALGORITHM_V2}


def registered_training_reference(client, version: str, *, data_sha256: str, manifest_path: Path,
                                  frame: pd.DataFrame) -> tuple[dict, dict, dict]:
    from bike_mlops.candidate_artifacts import candidate_identity, sha256_file
    import json

    if not version.isdecimal() or str(int(version)) != version or int(version) < 1:
        raise ValueError("Monitoring requires a positive exact model version")
    before = client.get_model_version(PROFILE["model_name"], version)
    if (before.name != PROFILE["model_name"] or str(before.version) != version or before.status != "READY"
            or not before.run_id or before.tags.get("data_sha256") != data_sha256
            or before.tags.get("data_profile_id") != PROFILE["profile_id"]
            or before.tags.get("target_contract_id") != PROFILE["target_contract_id"]
            or before.tags.get("feature_contract_version") != PROFILE["feature_contract_version"]
            or before.tags.get("monitor_reference_path") != REFERENCE_PATH
            or before.tags.get("monitor_psi_algorithm") != PSI_ALGORITHM_V2):
        raise ValueError("Registered version lacks the matching Capital training reference")
    tags = dict(before.tags)
    artifact_sha = tags.get("artifact_sha256")
    if (not isinstance(artifact_sha, str) or len(artifact_sha) != 64
            or any(c not in "0123456789abcdef" for c in artifact_sha)):
        raise ValueError("Registered model must have a verified artifact SHA-256")
    with tempfile.TemporaryDirectory(prefix="capital-monitor-reference-") as directory:
        def download(artifact_path):
            path = Path(client.download_artifacts(before.run_id, artifact_path, directory))
            if (path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES
                    or not path.resolve().is_relative_to(Path(directory).resolve())):
                raise ValueError("Monitoring evidence download is not a regular temporary file")
            return path.read_bytes()
        body = download("evidence/candidate-report.json")
        if hashlib.sha256(body).hexdigest() != tags.get("candidate_report_sha256"):
            raise ValueError("Registered candidate report SHA-256 mismatch")
        selection = strict_json(body.decode())
        ci = candidate_identity(selection)
        cid = hashlib.sha256(json.dumps(ci, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        if (ci != selection.get("candidate_identity") or cid != selection.get("candidate_id")
                or cid != tags.get("candidate_id") or selection["data_sha256"] != data_sha256
                or selection["candidate_artifact"]["sha256"] != tags.get("candidate_model_sha256")
                or selection["evaluation_protocol_sha256"] != tags.get("evaluation_protocol_sha256")
                or sha256_file(manifest_path) != selection["data_source"]["source_manifest_sha256"]):
            raise ValueError("Monitoring candidate, manifest, data, or artifact identity differs")
        reference_body = download(REFERENCE_PATH)
        reference = strict_json(reference_body.decode())
        if (selection.get("monitor_reference") != reference_descriptor(reference, reference_body)
                or any(tags.get(k) != v for k, v in reference_registry_tags(selection).items())
                or not reference_registry_tags(selection)):
            raise ValueError("Registered monitoring reference SHA-256/descriptor mismatch")
        validate_training_reference(reference, selection, frame=frame)
    after = client.get_model_version(PROFILE["model_name"], version)
    if (after.name != before.name or str(after.version) != version or after.status != "READY"
            or after.run_id != before.run_id or after.tags != tags):
        raise ValueError("Registered monitoring evidence changed during retrieval")
    return reference, selection, tags
