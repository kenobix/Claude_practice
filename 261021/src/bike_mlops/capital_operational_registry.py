"""Attach immutable operational evidence references to one blocked MLflow version.

This records evidence only. It deliberately never changes operational_gate_status
or deployment_permission and cannot approve or promote a model.
"""
from __future__ import annotations

import fcntl
import hashlib
import os
import re
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from bike_mlops import capital_operational_ledger as ledger
from bike_mlops import capital_operational_recovery as recovery
from bike_mlops.capital_measurement_recheck import recheck_measurement_records
from bike_mlops.config import DATA_PROFILE_ID, MODEL_NAME, PROJECT_ROOT, TARGET_CONTRACT_ID
from bike_mlops.evaluation_protocol import BOOTSTRAP_QUALITY_GATE_POLICY, BOOTSTRAP_QUALITY_GATE_POLICY_SHA256
from bike_mlops.profiles import load_data_profile

_OPERATIONAL_TAG_PREFIX = "capital_operational_"


@contextmanager
def _version_record_lock(model_name: str, version: str):
    """Serialize cooperating evidence writers for one exact local model version."""
    if not re.fullmatch(r"bike-demand-capital", model_name) or not version.isdecimal() or int(version) <= 0:
        raise ValueError("Invalid Capital registry version identity")
    directory = PROJECT_ROOT / "data/evaluations/capital-bikeshare/registry-record-locks"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (directory.is_symlink() or not directory.resolve().is_relative_to(PROJECT_ROOT.resolve())
            or directory.stat().st_mode & 0o077):
        raise PermissionError("Registry record lock directory permissions must exclude group and other users")
    path = directory / f"{model_name}-v{version}.lock"
    flags = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise PermissionError("Registry record lock must be a private regular file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another operational evidence recorder holds this model-version lock") from error
        yield
    finally:
        os.close(descriptor)


def _validate_resource_context(value: object, description: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{description} resource context is missing")
    host = value.get("host")
    containers = value.get("capital_stack_containers")
    if (not isinstance(host, dict) or not all(isinstance(host.get(key), str) and host[key]
                                             for key in ("os", "architecture", "python"))
            or type(host.get("logical_cpu_count")) is not int or host["logical_cpu_count"] <= 0
            or type(host.get("memory_total_bytes")) is not int or host["memory_total_bytes"] <= 0
            or not isinstance(host.get("dependencies"), dict)):
        raise ValueError(f"{description} host resource context is incomplete")
    if (not isinstance(containers, dict) or containers.get("status") != "captured"
            or not isinstance(containers.get("docker_engine_version"), str)
            or not containers["docker_engine_version"] or not containers.get("containers")
            or not containers.get("stats")):
        raise ValueError(f"{description} isolated Docker resource context is incomplete")


def _validate_completed_evidence(plan: dict, report: dict, report_sha256: str,
                                 attempt_id: str, plan_file_sha256: str) -> dict[str, str]:
    identity = plan["identity"]
    model_identity = report.get("model_identity")
    gate = report.get("operational_gate")
    audit = report.get("audit")
    snapshot = report.get("snapshot")
    if (report.get("completed") is not True or report.get("operational_attempt_id") != attempt_id
            or report.get("benchmark_id") != attempt_id or report.get("operational_plan_sha256") != plan["plan_sha256"]
            or report.get("operational_plan_file_sha256") != plan_file_sha256
            or not isinstance(model_identity, dict)
            or any(model_identity.get(key) != value for key, value in {
                "candidate_id": identity["candidate_id"], "model_name": identity["model_name"],
                "model_version": identity["model_version"],
                "artifact_sha256": identity["registered_artifact_sha256"],
                "feature_contract_version": identity["feature_contract_version"],
            }.items())
            or report.get("data_profile_id") != identity["data_profile_id"]
            or report.get("target_contract_id") != identity["target_contract_id"]
            or not isinstance(gate, dict) or gate.get("status") != "not_evaluated"
            or gate.get("input_provenance_status") != "passed"
            or gate.get("measurement_status") not in {"passed", "failed"}
            or not isinstance(report.get("deployment_permission"), dict)
            or report["deployment_permission"].get("status") != "blocked"):
        raise ValueError("Completed report identity or blocked gate state is invalid")
    observed = gate.get("observed")
    if not isinstance(observed, dict):
        raise ValueError("Completed report is missing observed request evidence")
    if (not isinstance(audit, dict) or audit.get("status") != "passed"
            or audit.get("verified_rows") != 1050
            or audit.get("request_id_sha256") != observed.get("request_id_sha256")):
        raise ValueError("Completed report audit evidence is missing or does not match all 1,050 requests")
    if (not isinstance(snapshot, dict) or snapshot.get("status") != "verified"
            or type(snapshot.get("observation_count")) is not int or snapshot["observation_count"] <= 0):
        raise ValueError("Completed report database snapshot evidence is missing")
    _validate_resource_context(plan.get("resource_context_before_requests"), "Frozen-plan")
    _validate_resource_context(report.get("resource_context"), "Measurement")
    observed_count = observed.get("measured_requests")
    if observed_count != 1000 or observed.get("warmup_requests") != 50:
        raise ValueError("Completed report does not contain the fixed 50 + 1,000 workload")
    return {
        "capital_operational_evidence_status": "recorded",
        "capital_operational_attempt_id": attempt_id,
        "capital_operational_report_sha256": report_sha256,
        "capital_operational_plan_sha256": plan["plan_sha256"],
        "capital_operational_plan_file_sha256": plan_file_sha256,
        "capital_operational_measurement_status": gate["measurement_status"],
        "capital_operational_numeric_recheck_status": "matched",
        "capital_operational_input_provenance_status": "passed",
        "capital_operational_audit_status": "passed",
        "capital_operational_resource_context_status": "captured",
        "capital_operational_request_id_sha256": observed["request_id_sha256"],
        "capital_operational_p95_ms": format(float(observed["p95_ms"]), ".17g"),
    }


def _expected_registration_tags(identity: dict) -> dict[str, str]:
    profile = load_data_profile("capital-bikeshare")
    return {
        "data_profile_id": profile["profile_id"],
        "target_contract_id": profile["target_contract_id"],
        "quality_gate": "passed",
        "validation_gate_status": "passed",
        "final_accuracy_gate_status": "passed",
        "operational_gate_status": "not_evaluated",
        "deployment_permission": "blocked",
        "quality_gate_policy_id": BOOTSTRAP_QUALITY_GATE_POLICY["policy_id"],
        "quality_gate_policy_sha256": BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
        "candidate_id": identity["candidate_id"],
        "candidate_model_sha256": identity["candidate_artifact_sha256"],
        "candidate_report_sha256": identity["candidate_report_sha256"],
        "final_report_sha256": identity["final_report_sha256"],
        "evaluation_protocol_sha256": identity["evaluation_protocol_sha256"],
        "data_sha256": identity["data_sha256"],
        "feature_contract_version": identity["feature_contract_version"],
        "artifact_sha256": identity["registered_artifact_sha256"],
    }


def _write_operational_evidence_tags(client, version: str, tags_to_record: dict[str, str]) -> None:
    """Write once per exact version; same-value retries are safe, conflicts are not."""
    registered = client.get_model_version(MODEL_NAME, version)
    if (registered.status != "READY" or registered.name != MODEL_NAME
            or str(registered.version) != version):
        raise ValueError("Exact Capital model version is not READY")
    if (registered.tags.get("operational_gate_status") != "not_evaluated"
            or registered.tags.get("deployment_permission") != "blocked"):
        raise ValueError("Operational evidence can only be attached while deployment remains blocked")
    unexpected = [key for key in registered.tags
                  if key.startswith(_OPERATIONAL_TAG_PREFIX) and key not in tags_to_record]
    if unexpected:
        raise ValueError("Registered version contains unsupported operational evidence tags")
    conflicts = [key for key, value in tags_to_record.items()
                 if key in registered.tags and registered.tags[key] != value]
    if conflicts:
        raise ValueError("Refusing to overwrite different operational evidence tags: " + ", ".join(conflicts))
    for key, value in tags_to_record.items():
        if registered.tags.get(key) != value:
            client.set_model_version_tag(MODEL_NAME, version, key, value)
    verified = client.get_model_version(MODEL_NAME, version)
    conflicts = [key for key, value in tags_to_record.items()
                 if verified.tags.get(key) != value]
    if (conflicts or verified.tags.get("operational_gate_status") != "not_evaluated"
            or verified.tags.get("deployment_permission") != "blocked"):
        raise RuntimeError("Operational evidence tags did not persist without changing blocked gates")


def record_completed_operational_evidence(candidate_id: str, attempt_id: str) -> dict:
    """Attach one linked, independently rechecked report to its exact blocked version."""
    if DATA_PROFILE_ID != "capital-bikeshare" or MODEL_NAME != "bike-demand-capital":
        raise ValueError("Operational evidence recording requires the Capital profile")
    if not re.fullmatch(r"[0-9a-f]{64}", candidate_id):
        raise ValueError("A lowercase candidate ID is required")
    try:
        if str(uuid.UUID(attempt_id)) != attempt_id:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("A canonical measurement attempt ID is required") from error

    ledger_path = ledger.DEFAULT_LEDGER
    lock_dir = ledger.DEFAULT_LOCK_DIR
    with recovery._existing_lock(lock_dir / f"{candidate_id}.lock"):
        with recovery._existing_lock(ledger_path.with_suffix(ledger_path.suffix + ".lock")):
            state = ledger.read_operational_ledger(ledger_path)
            record = next((row for row in state["records"] if row["candidate_id"] == candidate_id), None)
            attempt = next((row for row in record["attempts"] if row["attempt_id"] == attempt_id), None) if record else None
            if attempt is None or attempt.get("status") != "completed":
                raise ValueError("A completed ledger-linked measurement is required")
            plan, plan_status = recovery._plan_check(attempt, candidate_id)
            if plan is None or plan_status != "matched":
                raise ValueError("Frozen plan does not match the completed attempt")
            expected_path = f"data/reports/capital-bikeshare/operational-{attempt_id}.json"
            descriptor = attempt.get("report")
            if not isinstance(descriptor, dict) or descriptor.get("path") != expected_path:
                raise ValueError("Ledger does not link the canonical completed report")
            report_path = PROJECT_ROOT / expected_path
            report, report_sha256, issue = recovery._read_report(report_path)
            if issue or report_sha256 != descriptor.get("sha256"):
                raise ValueError("Ledger-linked report is unreadable or its SHA-256 differs")
            issue = recovery._report_error(report, attempt, candidate_id, plan, linked=True)
            if issue:
                raise ValueError("Ledger-linked report does not match the attempt: " + issue)
            numeric = recheck_measurement_records(plan, report, attempt_id=attempt_id,
                                                  plan_file_sha256=attempt["plan_file_sha256"])
            if numeric.get("numeric_status") != report["operational_gate"].get("measurement_status"):
                raise ValueError("Independent numeric recheck differs from the saved report")
            tags_to_record = _validate_completed_evidence(
                plan, report, report_sha256, attempt_id, attempt["plan_file_sha256"]
            )
            if (report["model_identity"]["model_version"] != plan["identity"]["model_version"]
                    or report["model_identity"]["artifact_sha256"] != plan["identity"]["registered_artifact_sha256"]):
                raise ValueError("Report is not tied to the frozen registered model version and artifact")

            registry_uri = os.getenv("MLFLOW_TRACKING_URI", "")
            parsed = urlsplit(registry_uri)
            if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}
                    or parsed.port != 5001 or parsed.path not in {"", "/"}
                    or parsed.query or parsed.fragment or registry_uri.rstrip("/") != plan["registry_uri"]):
                raise ValueError("MLFLOW_TRACKING_URI must match the frozen local Capital registry")
            if os.getenv("MLFLOW_REGISTRY_URI", registry_uri).rstrip("/") != plan["registry_uri"]:
                raise ValueError("MLFLOW_REGISTRY_URI differs from the frozen Capital registry")
            version = str(plan["identity"]["model_version"])
            with _version_record_lock(MODEL_NAME, version):
                from mlflow.tracking import MlflowClient

                client = MlflowClient(tracking_uri=registry_uri, registry_uri=registry_uri)
                registered = client.get_model_version(MODEL_NAME, version)
                if (registered.status != "READY" or registered.name != MODEL_NAME
                        or str(registered.version) != version):
                    raise ValueError("Exact Capital model version is not READY")
                expected_registration = _expected_registration_tags(plan["identity"])
                conflicts = [key for key, value in expected_registration.items()
                             if registered.tags.get(key) != value]
                if conflicts:
                    raise ValueError("Registered version identity or blocked gate tags differ: " + ", ".join(conflicts))
                _write_operational_evidence_tags(client, version, tags_to_record)
            return {
                "status": "evidence_recorded",
                "model_name": MODEL_NAME,
                "model_version": version,
                "candidate_id": candidate_id,
                "attempt_id": attempt_id,
                "report_sha256": report_sha256,
                "measurement_status": numeric["numeric_status"],
                "operational_gate_status": "not_evaluated",
                "deployment_permission": "blocked",
                "idempotent": True,
            }
