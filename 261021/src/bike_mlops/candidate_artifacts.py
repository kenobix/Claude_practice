"""Immutable local storage for validation-selected candidate models."""

from __future__ import annotations

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
            from bike_mlops.training_monitor_reference import make_training_reference, reference_descriptor
            from bike_mlops.capital_deployment_evidence import canonical_bytes

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
