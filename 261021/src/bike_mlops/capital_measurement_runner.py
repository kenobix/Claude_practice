"""Run one frozen staging measurement; deployment approval is deliberately absent."""
from __future__ import annotations

from pathlib import Path
import signal
import threading
from contextlib import contextmanager
from urllib.parse import urlsplit

from bike_mlops.capital_benchmark_observer import CapitalBenchmarkObserver
from bike_mlops.capital_measurement_guard import FeatureSourceExpectations, hold_observation_snapshot
from bike_mlops.capital_measurement_preflight import verify_measurement_inputs
from bike_mlops.capital_operational_ledger import measurement_attempt
from bike_mlops.config import PROJECT_ROOT
from bike_mlops.capital_request_journal import RequestJournal
from bike_mlops.operational_benchmark import run_capital_operational_benchmark, write_benchmark_report


def _validate_staging_database(database) -> None:
    """Restrict the internal runner to the explicitly isolated staging DB."""
    try:
        parsed = urlsplit(database.url)
        valid = (parsed.scheme in {"postgresql", "postgres"} and parsed.hostname in {"localhost", "127.0.0.1"}
                 and parsed.port == 55434 and parsed.username == "bike_app" and parsed.path == "/bikeops"
                 and not parsed.query and not parsed.fragment and not database.sqlite
                 and database.data_profile_id == "capital-bikeshare"
                 and database.target_contract_id == "capital-trip-starts-hourly-v1")
    except (AttributeError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("Measurement requires the isolated local Capital staging PostgreSQL database")


def _identity(plan: dict) -> dict:
    identity = plan["identity"]
    return {"candidate_id": identity["candidate_id"], "model_name": identity["model_name"],
            "model_version": identity["model_version"], "artifact_sha256": identity["registered_artifact_sha256"],
            "data_profile_id": identity["data_profile_id"], "target_contract_id": identity["target_contract_id"],
            "feature_contract_version": identity["feature_contract_version"]}


def _attach_attempt(report: dict, attempt) -> None:
    report.update({"operational_attempt_id": attempt.attempt_id, "operational_plan_sha256": attempt.plan_sha256,
                   "operational_plan_file_sha256": attempt.plan_file_sha256})


def _failure_report(attempt, stage: str, error_kind: str, report: dict | None, observer) -> dict:
    """Preserve partial request evidence without writing exception text or credentials."""
    exchanges = observer.exchanges if observer is not None else []
    pending = getattr(observer, "_pending", None) if observer is not None else None
    verified_ids = [payload["request_id"] for payload, _ in exchanges]
    if report is None:
        report = {"schema_version": 1, "completed": False, "measurements": [], "verified_response_ids": verified_ids,
            "pending_request_id": pending.get("request_id") if isinstance(pending, dict) else None}
    _attach_attempt(report, attempt)
    report["partial_evidence"] = {
        "verified_response_ids": verified_ids,
        "verified_response_count": len(verified_ids),
        "pending_request_id": pending.get("request_id") if isinstance(pending, dict) else None,
        "pending_request_status": "outcome_unknown_may_have_been_sent" if isinstance(pending, dict) else None,
        "limitation": "Memory may be lost; inspect the durable journal. Timeout or DB absence does not rule out a later commit.",
    }
    report["request_sequence_completed"] = report.get("completed") is True
    report["completed"] = False
    gate = report.setdefault("operational_gate", {})
    gate["status"] = "failed"
    gate["input_provenance_status"] = "not_evaluated"
    report["failure"] = {"stage": stage, "error_type": error_kind}
    report["audit"] = {"status": "not_evaluated"}
    report["deployment_permission"] = {"status": "blocked"}
    return report


@contextmanager
def _termination_as_interrupt():
    """Best-effort SIGTERM report on CLI's main thread; SIGKILL requires journal inspection."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGTERM)
    def stop(_signum, _frame):
        raise KeyboardInterrupt("SIGTERM")
    signal.signal(signal.SIGTERM, stop)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def run_staging_measurement(plan_path: Path, selection_path: Path, final_path: Path, database) -> dict:
    """Run one auditable candidate attempt; never make a deployment decision.

    Numeric success plus audit verification still leaves the operational gate
    not_evaluated pending independent evidence/resource review. The caller must
    not treat the ledger's completed state as a passed gate.
    """
    _validate_staging_database(database)
    outcome = None
    interrupted = False
    with _termination_as_interrupt(), measurement_attempt(plan_path) as attempt:
        stage = "preflight"
        observer = None
        report = None
        journal = None
        success_report_path = PROJECT_ROOT / "data/reports/capital-bikeshare" / f"operational-{attempt.attempt_id}.json"
        failure_report_path = PROJECT_ROOT / "data/reports/capital-bikeshare" / f"operational-{attempt.attempt_id}-failure.json"
        try:
            verified = verify_measurement_inputs(attempt, plan_path, selection_path, final_path)
            plan, frame = verified.plan, verified.frame
            identity = _identity(plan)
            stage = "journal"
            journal = RequestJournal(success_report_path.with_suffix(".requests.jsonl"), attempt, plan)
            stage = "snapshot"
            with hold_observation_snapshot(database, frame, plan["identity"]["data_sha256"]) as snapshot:
                sources = FeatureSourceExpectations(frame, plan["identity"]["data_sha256"],
                                                    label_delay_hours=plan["input_window"]["label_delay_hours"])
                observer = CapitalBenchmarkObserver(attempt, snapshot, sources, pinned_identity=identity, journal=journal)
                stage = "measurement"
                report = run_capital_operational_benchmark(
                    api_url=plan["api_url"], model_version=identity["model_version"],
                    candidate_id=identity["candidate_id"], start=plan["input_window"]["start_inclusive"],
                    end=plan["input_window"]["end_exclusive"], attempt_id=attempt.attempt_id,
                    expected_artifact_sha256=identity["artifact_sha256"], request_observer=observer,
                )
                _attach_attempt(report, attempt)
                if not report.get("completed"):
                    if report.get("interruption_requested"):
                        raise KeyboardInterrupt("Measurement termination requested")
                    raise RuntimeError("Incomplete measurement sequence")
                if report.get("operational_gate", {}).get("status") != "not_evaluated":
                    raise ValueError("Operational gate must remain unevaluated")
                if report.get("operational_gate", {}).get("measurement_status") not in {"passed", "failed"}:
                    raise ValueError("Completed measurement must retain its numerical result")
                if (report.get("benchmark_id") != attempt.attempt_id or not isinstance(report.get("model_identity"), dict)
                        or any(report["model_identity"].get(key) != value for key, value in identity.items()
                               if key in {"candidate_id", "model_name", "model_version", "artifact_sha256", "feature_contract_version"})):
                    raise ValueError("Measurement report identity differs from the fixed attempt")
                stage = "audit"
                journal.assert_complete()
                audit = observer.verify_audits()
                measured_ids = report.get("operational_gate", {}).get("observed", {}).get("request_id_sha256")
                if audit.get("verified_rows") != 1050 or audit.get("request_id_sha256") != measured_ids:
                    raise ValueError("Audit count or request ID digest differs from the measurement")
                report["audit"] = {"status": "passed", **audit}
                report["operational_gate"]["input_provenance_status"] = "passed"
                report["snapshot"] = {"status": "verified", "observation_count": snapshot.row_count}
                report["limitations"] = [item for item in report.get("limitations", [])
                                         if item != "DB snapshot and API data provenance are not verified; numerical success is not an operational gate pass."]
                report["limitations"].append("Independent operational evidence and resource review remain incomplete.")
                snapshot.assert_held()
                stage = "report"
                report["request_journal"] = journal.descriptor()
                write_benchmark_report(success_report_path, report)
                snapshot.assert_held()
                stage = "snapshot_release"
            # Do not mark the attempt complete unless the snapshot context
            # successfully checks and releases its lock.
            stage = "ledger"
            attempt.finish(status="completed", report_path=success_report_path)
            outcome = {"status": "completed", "measurement_status": report["operational_gate"]["measurement_status"],
                       "operational_gate": "not_evaluated", "deployment_permission": "blocked",
                       "attempt_id": attempt.attempt_id, "report_path": str(success_report_path)}
        except (Exception, KeyboardInterrupt) as error:
            interrupted = isinstance(error, KeyboardInterrupt)
            failure = _failure_report(attempt, stage, type(error).__name__, report, observer)
            if journal is not None:
                failure["request_journal"] = journal.descriptor()
            write_benchmark_report(failure_report_path, failure)
            failure_code = {"preflight": "preflight_failed", "snapshot": "snapshot_lost", "snapshot_release": "snapshot_lost", "measurement": "request_failed",
                            "audit": "audit_mismatch"}.get(stage, "other")
            attempt.finish(status="failed", report_path=failure_report_path, failure_code=failure_code)
            outcome = {"status": "failed", "operational_gate": "failed", "deployment_permission": "blocked",
                       "attempt_id": attempt.attempt_id, "report_path": str(failure_report_path),
                       "failure_stage": stage, "error_type": type(error).__name__}
        finally:
            if journal is not None:
                journal.close()
    if interrupted:
        raise KeyboardInterrupt("Measurement interrupted; failure report was saved")
    return outcome
