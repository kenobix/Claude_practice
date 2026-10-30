"""Publish complete content-addressed evidence for a blocked Capital version."""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from bike_mlops.capital_deployment_evidence import (
    CASES, MODEL, READINESS_POLICY, binding, canonical_bytes, document, evidence_identity,
    reference_tags, validate_bundle, verified_registered_evidence,
)


def publish_deployment_evidence(candidate_id: str, attempt_id: str, final_path: Path,
                                prerequisite_paths: dict[str, Path]) -> dict:
    from bike_mlops import capital_operational_ledger as ledger
    from bike_mlops import capital_operational_recovery as recovery
    from bike_mlops.capital_operational_plan import local_registry_url
    from bike_mlops.capital_operational_registry import _version_record_lock
    from bike_mlops.capital_staging import verify_candidate_for_staging
    from bike_mlops.candidate_artifacts import sha256_file
    from bike_mlops.config import DATA_PROFILE_ID, PROJECT_ROOT

    if DATA_PROFILE_ID != "capital-bikeshare" or not re.fullmatch(r"[0-9a-f]{64}", candidate_id):
        raise ValueError("Deployment evidence publishing requires a Capital candidate ID")
    if not recovery._canonical_attempt(attempt_id) or set(prerequisite_paths) != set(CASES):
        raise ValueError("A completed attempt and all prerequisite report paths are required")
    with recovery._existing_lock(ledger.DEFAULT_LOCK_DIR / f"{candidate_id}.lock"):
        with recovery._existing_lock(ledger.DEFAULT_LEDGER.with_suffix(ledger.DEFAULT_LEDGER.suffix + ".lock")):
            state = ledger.read_operational_ledger(ledger.DEFAULT_LEDGER)
            record = next((r for r in state["records"] if r["candidate_id"] == candidate_id), None)
            attempt = next((a for a in record["attempts"] if a["attempt_id"] == attempt_id), None) if record else None
            if attempt is None or attempt.get("status") != "completed":
                raise ValueError("Deployment evidence needs a completed ledger-linked operational attempt")
            plan, status = recovery._plan_check(attempt, candidate_id)
            if plan is None or status != "matched":
                raise ValueError("The ledger-linked operational plan differs")
            expected_report = f"data/reports/capital-bikeshare/operational-{attempt_id}.json"
            descriptor = attempt.get("report")
            if not isinstance(descriptor, dict) or descriptor.get("path") != expected_report:
                raise ValueError("Operational report is not the canonical completed ledger report")
            report_path = PROJECT_ROOT / expected_report
            report, report_hash, issue = recovery._read_report(report_path)
            if issue or report_hash != descriptor.get("sha256"):
                raise ValueError("Ledger-linked operational report differs")
            if recovery._report_error(report, attempt, candidate_id, plan, linked=True):
                raise ValueError("Operational report does not match its attempt")
            selection_path = PROJECT_ROOT / "data/candidates" / candidate_id / "candidate-report.json"
            verified = verify_candidate_for_staging(selection_path, final_path)
            final_path = verified["final_report_path"]
            uri = local_registry_url(os.getenv("MLFLOW_TRACKING_URI", ""))
            if uri != plan["registry_uri"] or os.getenv("MLFLOW_REGISTRY_URI", uri).rstrip("/") != uri:
                raise ValueError("Tracking/registry URI must match the frozen Capital plan")
            from mlflow.tracking import MlflowClient

            version = str(plan["identity"]["model_version"])
            with _version_record_lock(MODEL, version):
                client = MlflowClient(tracking_uri=uri, registry_uri=uri)
                registered = client.get_model_version(MODEL, version)
                if (registered.name != MODEL or str(registered.version) != version or registered.status != "READY"
                        or registered.tags.get("deployment_permission") != "blocked"):
                    raise ValueError("Evidence may only be attached to the exact READY blocked version")
                tags = registered.tags
                identity = evidence_identity(tags, version, registered.run_id)
                paths = {
                    "candidate": selection_path, "final": final_path,
                    "protocol": selection_path.parent / "evaluation-protocol.json",
                    "validation_rows": selection_path.parent / "validation-predictions.csv",
                    "final_rows": PROJECT_ROOT / verified["final"]["evaluation_window"]["row_level_predictions"],
                    "plan": PROJECT_ROOT / attempt["plan_path"], "operational": report_path,
                    **prerequisite_paths,
                }
                bodies, resolved_paths = {}, {}
                for name, path in paths.items():
                    path = Path(path)
                    resolved = path.resolve(strict=True)
                    if (path.is_symlink() or not resolved.is_file()
                            or not resolved.is_relative_to((PROJECT_ROOT / "data").resolve())):
                        raise ValueError("Deployment evidence inputs must be regular project data files")
                    bodies[name] = document(resolved.read_bytes())
                    resolved_paths[name] = resolved
                bundle = {"schema_version": 1, "kind": "capital-deployment-evidence",
                          "policy": READINESS_POLICY, "identity": identity, "reports": bodies}
                validate_bundle(bundle, tags, version, registered.run_id)
                for name, path in resolved_paths.items():
                    if sha256_file(path) != bodies[name]["sha256"]:
                        raise ValueError("Deployment evidence file changed during preparation")
                body = canonical_bytes(bundle)
                refs = reference_tags(body, registered.run_id)
                current = client.get_model_version(MODEL, version)
                if (binding(current.tags) != binding(tags) or current.status != "READY"
                        or current.run_id != registered.run_id or current.tags.get("deployment_permission") != "blocked"):
                    raise ValueError("Registry evidence changed before publication")
                if any(k in current.tags and current.tags[k] != v for k, v in refs.items()):
                    raise ValueError("Refusing to overwrite a different deployment evidence bundle")
                with tempfile.TemporaryDirectory(prefix="capital-evidence-publish-") as directory:
                    path = Path(directory) / "bundle.json"
                    path.write_bytes(body)
                    client.log_artifact(registered.run_id, str(path),
                                        artifact_path=refs["capital_deployment_evidence_path"].rsplit("/", 1)[0])
                for key, value in refs.items():
                    if current.tags.get(key) != value:
                        client.set_model_version_tag(MODEL, version, key, value)
                after = client.get_model_version(MODEL, version)
                verified_registered_evidence(client, version, after.tags)
                if (after.tags.get("deployment_permission") != "blocked"
                        or after.tags.get("operational_gate_status") != tags.get("operational_gate_status")):
                    raise RuntimeError("Evidence publication unexpectedly changed a deployment gate")
                return {"status": "deployment_evidence_recorded", "model_version": version,
                        "bundle_sha256": refs["capital_deployment_evidence_sha256"],
                        "deployment_permission": "blocked"}
