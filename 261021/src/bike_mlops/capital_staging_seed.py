"""Bind staging seed input to verified candidate evidence and registry metadata."""

from pathlib import Path

from bike_mlops.candidate_artifacts import sha256_file
from bike_mlops.capital_staging import _registration_tags, validate_staging_model_tags
from bike_mlops.features import available_observation_frame


def _validate_candidate_inputs(csv_path: Path, manifest_path: Path, evidence: dict, registered) -> tuple[str, str]:
    selection = evidence["selection"]
    if registered.status != "READY":
        raise ValueError("Staging registry version is not READY")
    tags = _registration_tags(evidence)
    validate_staging_model_tags(registered.tags, tags)
    data_hash = sha256_file(csv_path)
    manifest_hash = sha256_file(manifest_path)
    if data_hash != selection["data_sha256"] or manifest_hash != selection["data_source"]["source_manifest_sha256"]:
        raise ValueError("Staging CSV or manifest does not match the verified candidate")
    return data_hash, manifest_hash


def seed_verified_candidate(database, frame, csv_path: Path, manifest_path: Path, evidence: dict, registered) -> dict:
    """Reject mismatched inputs before atomically inserting any observations."""
    data_hash, manifest_hash = _validate_candidate_inputs(csv_path, manifest_path, evidence, registered)
    inserted = database.seed_staging_observations(available_observation_frame(frame), data_hash)
    return {"rows_in_source": len(frame), "new_observations_inserted": inserted, "data_sha256": data_hash,
            "manifest_sha256": manifest_hash, "candidate_id": evidence["candidate_id"],
            "candidate_report_sha256": evidence["selection_report_sha256"],
            "model_version": str(registered.version)}


def verify_seeded_candidate(database, frame, csv_path: Path, manifest_path: Path, evidence: dict, registered) -> dict:
    """Check a seeded snapshot without modifying observations."""
    data_hash, manifest_hash = _validate_candidate_inputs(csv_path, manifest_path, evidence, registered)
    count = database.verify_staging_observations(available_observation_frame(frame), data_hash)
    return {"verified_rows": count, "data_sha256": data_hash, "manifest_sha256": manifest_hash,
            "candidate_id": evidence["candidate_id"], "candidate_report_sha256": evidence["selection_report_sha256"],
            "model_version": str(registered.version)}
