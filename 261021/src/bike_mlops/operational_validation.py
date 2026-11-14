"""Product components: operational_validation."""
from __future__ import annotations


# ========================================================================
# capital_benchmark_inputs
# ========================================================================

"""Validate operational replay hours against a frozen candidate training split."""



import hashlib
from pathlib import Path

import pandas as pd

from bike_mlops.config import LABEL_DELAY_HOURS


def check_candidate_training_window(selection_path: Path, final_path: Path, candidate_id: str, *, start: str, end: str) -> dict:
    """Verify frozen evidence and the configured dataset before checking replay hours."""
    from bike_mlops.evaluation import sha256_file
    from bike_mlops.staging import verify_candidate_for_staging
    from bike_mlops.cli import _load_profile_hourly
    from bike_mlops.config import DATASET_MANIFEST, DEFAULT_DATASET
    from bike_mlops.evaluation import read_protocol

    evidence = verify_candidate_for_staging(selection_path, final_path)
    selection = evidence["selection"]
    if candidate_id != evidence["candidate_id"]:
        raise ValueError("Requested candidate ID differs from the verified candidate")
    expected_csv = selection["data_sha256"]
    expected_manifest = selection["data_source"]["source_manifest_sha256"]
    if sha256_file(DEFAULT_DATASET) != expected_csv or sha256_file(DATASET_MANIFEST) != expected_manifest:
        raise ValueError("Benchmark CSV or manifest differs from the verified candidate")
    frame = _load_profile_hourly(DEFAULT_DATASET)
    if sha256_file(DEFAULT_DATASET) != expected_csv or sha256_file(DATASET_MANIFEST) != expected_manifest:
        raise ValueError("Benchmark CSV or manifest changed during loading")
    protocol_path = evidence["selection_report_path"].parent / "evaluation-protocol.json"
    expected_protocol_file = selection["evaluation_protocol_artifact"]["sha256"]
    if sha256_file(protocol_path) != expected_protocol_file:
        raise ValueError("Benchmark protocol file differs from the candidate report")
    protocol = read_protocol(protocol_path)
    if (sha256_file(protocol_path) != expected_protocol_file
            or protocol.get("protocol_sha256") != evidence["evaluation_protocol_sha256"]):
        raise ValueError("Benchmark protocol identity changed during loading")
    result = validate_training_window(frame, protocol, selection, start=start, end=end)
    result.update({"candidate_id": candidate_id, "candidate_report_sha256": evidence["selection_report_sha256"],
                   "final_report_sha256": evidence["final_report_sha256"], "data_sha256": expected_csv,
                   "manifest_sha256": expected_manifest, "evaluation_protocol_sha256": evidence["evaluation_protocol_sha256"]})
    return result


def validate_training_window(frame: pd.DataFrame, protocol: dict, selection: dict, *, start: str, end: str) -> dict:
    """Check the frozen training rows and require every replay hour to be among them.

    The caller must independently verify the candidate evidence, source CSV and
    manifest, and hold a stable DB snapshot during measurement.
    """
    if not {"timestamp", "cnt"}.issubset(frame.columns) or frame.empty:
        raise ValueError("Candidate dataset must contain timestamp and cnt rows")
    times = frame["timestamp"]
    if not pd.api.types.is_datetime64_dtype(times.dtype) or times.isna().any():
        raise ValueError("Candidate timestamps must be naive, non-null datetimes")
    if not times.is_monotonic_increasing or times.duplicated().any():
        raise ValueError("Candidate timestamps must be sorted and unique")
    if (times != times.dt.floor("h")).any():
        raise ValueError("Candidate timestamps must be hour-aligned")

    start_time, end_time = pd.Timestamp(start), pd.Timestamp(end)
    cutoff = pd.Timestamp(protocol["training"]["cutoff"])
    validation_start = pd.Timestamp(protocol["validation"]["start_inclusive"])
    for stamp in (start_time, end_time, cutoff, validation_start):
        if pd.isna(stamp) or stamp.tzinfo is not None or stamp != stamp.floor("h"):
            raise ValueError("Window and frozen split boundaries must be naive, hour-aligned timestamps")
    if end_time - start_time != pd.Timedelta(hours=168):
        raise ValueError("Operational replay window must be exactly 168 hours")
    delay = protocol["label_availability"]["delay_hours"]
    if isinstance(delay, bool) or not isinstance(delay, int) or delay != LABEL_DELAY_HOURS:
        raise ValueError("Frozen label delay differs from the active data profile")
    if selection.get("training_cutoff") != protocol["training"]["cutoff"]:
        raise ValueError("Candidate training cutoff differs from the frozen protocol")
    mask = (times < validation_start) & (times + pd.Timedelta(hours=delay) <= cutoff)
    training = frame.loc[mask]
    if len(training) != protocol["training"]["rows"] or len(training) != selection["split_counts"]["train"]:
        raise ValueError("Frozen training row count differs from the dataset or candidate report")
    payload = "".join(
        f"{timestamp.isoformat(sep=' ')}\t{int(target)}\n"
        for timestamp, target in zip(training["timestamp"], training["cnt"])
    )
    row_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if row_hash != selection.get("training_row_keys_sha256"):
        raise ValueError("Frozen training rows differ from the candidate report")

    requested = pd.date_range(start_time, end_time, freq="h", inclusive="left")
    training_times = pd.DatetimeIndex(training["timestamp"])
    excluded = requested.difference(training_times)
    if not excluded.empty:
        raise ValueError(f"Replay window includes a missing or non-training hour: {excluded[0]}")
    return {
        "start_inclusive": start_time.isoformat(sep=" "),
        "end_exclusive": end_time.isoformat(sep=" "),
        "hours": len(requested),
        "training_cutoff": cutoff.isoformat(sep=" "),
        "label_delay_hours": delay,
        "training_rows": len(training),
        "training_row_keys_sha256": row_hash,
        "latest_replay_label_available_at": (requested[-1] + pd.Timedelta(hours=delay)).isoformat(sep=" "),
    }


# ========================================================================
# capital_measurement_guard
# ========================================================================

"""Hold a verified staging observation snapshot and check prediction provenance."""



import hashlib as _measurement_guard_hashlib
import json
import math
import re
from contextlib import contextmanager
from datetime import timedelta

import pandas as _measurement_guard_pd

from bike_mlops.db import canonical_time, stable_hash
from bike_mlops.data import available_observation_frame, build_feature_row


class ObservationSnapshot:
    """A PostgreSQL lock that remains owned by its original transaction."""

    def __init__(self, database, connection, row_count: int):
        self.database = database
        self.connection = connection
        self.row_count = row_count

    def assert_held(self) -> None:
        try:
            row = self.database.execute(
                self.connection,
                "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() "
                "AND relation='observations'::regclass AND mode='ShareRowExclusiveLock' "
                "AND granted) AS held",
            ).fetchone()
        except Exception as error:
            raise RuntimeError("Staging snapshot connection was lost") from error
        if not row or not row["held"]:
            raise RuntimeError("Staging observation lock is no longer held")


@contextmanager
def hold_observation_snapshot(database, frame: _measurement_guard_pd.DataFrame, data_hash: str, *, lock_timeout_ms: int = 5000):
    """Check all rows and hold a PostgreSQL table lock until the caller finishes.

    The caller must first verify that frame and data_hash belong to the pinned
    candidate. Only observations are locked; prediction audit writes on other
    connections remain possible. The caller must check the guard before each
    request and before accepting its final report.
    """
    if database.sqlite:
        raise ValueError("Measurement snapshot requires PostgreSQL; SQLite locks would block audit writes")
    if not isinstance(data_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", data_hash):
        raise ValueError("Candidate data hash must be a lowercase SHA-256")
    if isinstance(lock_timeout_ms, bool) or not isinstance(lock_timeout_ms, int) or not 1 <= lock_timeout_ms <= 60000:
        raise ValueError("lock_timeout_ms must be an integer between 1 and 60000")
    expected = database._expected_staging_observations(available_observation_frame(frame), data_hash)
    connection = database.connect()
    try:
        # Each audit SELECT must see writes committed by the API after the snapshot check.
        database.execute(connection, "SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
        database.execute(connection, "SELECT set_config('lock_timeout', ?, true)", (f"{lock_timeout_ms}ms",))
        database._check_staging_observations(connection, expected, require_complete=True)
        guard = ObservationSnapshot(database, connection, len(expected))
        guard.assert_held()
        yield guard
        guard.assert_held()
        connection.commit()
    except BaseException:
        try:
            connection.rollback()
        except Exception:
            pass  # A lost connection can no longer be rolled back; preserve the original failure.
        raise
    finally:
        connection.close()


class FeatureSourceExpectations:
    """Compute source hashes from rows whose candidate identity was verified by the caller."""

    def __init__(self, frame: pd.DataFrame, data_hash: str, *, label_delay_hours: int):
        if not isinstance(data_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", data_hash):
            raise ValueError("Candidate data hash must be a lowercase SHA-256")
        if isinstance(label_delay_hours, bool) or not isinstance(label_delay_hours, int) or label_delay_hours < 0:
            raise ValueError("Label delay must be a non-negative integer")
        self.data_hash = data_hash
        self.delay = timedelta(hours=label_delay_hours)
        self.counts = {}
        for row in frame.itertuples(index=False):
            stamp = canonical_time(row.timestamp)
            if stamp in self.counts:
                raise ValueError(f"Duplicate candidate source hour: {stamp}")
            count = row.cnt
            if isinstance(count, bool) or not math.isfinite(float(count)) or int(count) != count or count < 0:
                raise ValueError(f"Invalid candidate source count at {stamp}")
            self.counts[stamp] = int(count)
        if not self.counts:
            raise ValueError("Candidate source dataset must not be empty")

    def _sources(self, target_hour) -> tuple[pd.Timestamp, dict]:
        target = _measurement_guard_pd.Timestamp(target_hour)
        if _measurement_guard_pd.isna(target) or target.tzinfo is not None or target != target.floor("h"):
            raise ValueError("Prediction target must be a naive hour")
        sources = {}
        for lag in (24, 168):
            observed = target - timedelta(hours=lag)
            observed_text = canonical_time(observed)
            count = self.counts.get(observed_text)
            sources[f"lag_{lag}"] = (
                {"timestamp": observed_text, "cnt": count, "data_hash": self.data_hash}
                if count is not None and observed + self.delay <= target else None
            )
        return target, sources

    def source_hash(self, target_hour) -> str:
        return stable_hash(self._sources(target_hour)[1])

    def validate_response(self, target_hour, response: object) -> None:
        if not isinstance(response, dict) or response.get("data_version_sha256") != self.source_hash(target_hour):
            raise ValueError("Prediction response data provenance differs from the candidate dataset")

    def expected_features(self, target_hour) -> dict:
        target, sources = self._sources(target_hour)
        observations = _measurement_guard_pd.DataFrame([
            {"timestamp": value["timestamp"], "cnt": value["cnt"],
             "available_at": _measurement_guard_pd.Timestamp(value["timestamp"]) + self.delay}
            for value in sources.values() if value is not None
        ], columns=["timestamp", "cnt", "available_at"])
        features = build_feature_row(target, target, observations)
        return {key: value if math.isfinite(value) else None for key, value in features.items()}


def verify_prediction_audit(payload: dict, response: dict, row: dict, sources: FeatureSourceExpectations, *,
                            candidate_id: str, model_name: str, model_version: str, artifact_sha256: str,
                            data_profile_id: str, target_contract_id: str, feature_contract_version: str) -> None:
    """Compare one request, response and persisted audit row against pinned identity and data."""
    if not isinstance(payload, dict) or not isinstance(response, dict) or not isinstance(row, dict):
        raise ValueError("Prediction request, response and audit row must be objects")
    target = canonical_time(payload["target_hour"])
    as_of = canonical_time(payload["as_of"])
    if target != as_of:
        raise ValueError("Prediction as_of differs from the target hour")
    request_fields = {"feature_contract_version": feature_contract_version, "data_profile_id": data_profile_id,
                      "target_contract_id": target_contract_id}
    if any(payload.get(key) != value for key, value in request_fields.items()):
        raise ValueError("Prediction request identity differs from the pinned candidate")
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise ValueError("Prediction request ID is missing")
    expected = {"request_id": request_id, "target_hour": target, "model_name": model_name,
                "model_version": str(model_version), "candidate_id": candidate_id,
                "artifact_sha256": artifact_sha256, **request_fields}
    if any(response.get(key) != value or row.get(key) != value for key, value in expected.items()):
        raise ValueError("Prediction response or audit identity differs from the pinned candidate")
    if response.get("deployment_permission") != "blocked" or response.get("model_quality_gate") != "passed":
        raise ValueError("Prediction response is not from a blocked, accuracy-passed candidate")
    if row.get("model_quality_gate") != "passed" or row.get("as_of") != as_of:
        raise ValueError("Prediction audit gate or as_of differs from the request")
    canonical_input = {"target_hour": target, "as_of": as_of, **request_fields}
    if row.get("input_hash") != stable_hash(canonical_input):
        raise ValueError("Prediction audit input hash differs from the request")
    sources.validate_response(target, response)
    if row.get("data_version_sha256") != sources.source_hash(target):
        raise ValueError("Prediction audit data provenance differs from the candidate dataset")
    try:
        features = json.loads(row["features_json"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Prediction audit features are unreadable") from error
    if features != sources.expected_features(target):
        raise ValueError("Prediction audit features differ from the candidate dataset")
    prediction = response.get("prediction")
    if (isinstance(prediction, bool) or not isinstance(prediction, (int, float))
            or not math.isfinite(prediction) or prediction < 0 or row.get("predicted") != prediction):
        raise ValueError("Prediction audit value differs from the response")


def verify_prediction_audit_batch(guard: ObservationSnapshot, exchanges: list[tuple[dict, dict]],
                                  sources: FeatureSourceExpectations, *, request_prefix: str, **pinned) -> dict:
    """Reject missing, extra, duplicate or inconsistent rows while the snapshot is held."""
    if not isinstance(request_prefix, str) or not re.fullmatch(r"[A-Za-z0-9.:-]+", request_prefix):
        raise ValueError("Audit request prefix contains unsupported characters")
    if not exchanges:
        raise ValueError("Audit batch must contain at least one request")
    ids = [payload.get("request_id") for payload, _ in exchanges]
    if (any(not isinstance(value, str) or not value.startswith(request_prefix) for value in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError("Audit request IDs must be unique and belong to the benchmark prefix")
    guard.assert_held()
    rows = guard.database.execute(
        guard.connection, "SELECT * FROM predictions WHERE request_id LIKE ?", (request_prefix + "%",)
    ).fetchall()
    by_id = {row["request_id"]: dict(row) for row in rows}
    if len(rows) != len(ids) or set(by_id) != set(ids):
        raise ValueError("Prediction audit rows do not exactly match the benchmark request IDs")
    for payload, response in exchanges:
        verify_prediction_audit(payload, response, by_id[payload["request_id"]], sources, **pinned)
    guard.assert_held()
    return {"verified_rows": len(ids), "request_id_sha256": _measurement_guard_hashlib.sha256("\n".join(ids).encode()).hexdigest()}


# ========================================================================
# capital_benchmark_observer
# ========================================================================

"""Connect a measurement attempt to snapshot and response provenance checks.

This component does not establish an operational gate or authorize deployment.
The caller must validate and bind the candidate and plan before constructing it.
"""


from copy import deepcopy

_benchmark_observer_verify_prediction_audit_batch = verify_prediction_audit_batch
from bike_mlops.operational import MEASURED_REQUESTS, WARMUP_REQUESTS


class CapitalBenchmarkObserver:
    def __init__(self, attempt, snapshot, sources, *, pinned_identity: dict, journal=None):
        self.attempt = attempt
        self.snapshot = snapshot
        self.sources = sources
        self.pinned_identity = dict(pinned_identity)
        self.exchanges: list[tuple[dict, dict]] = []
        self._ids: set[str] = set()
        self._pending = None
        self._marked = False
        self.journal = journal

    def before_request(self, payload: dict) -> None:
        self.snapshot.assert_held()
        request_id = payload.get("request_id")
        if (not isinstance(request_id, str) or not request_id.startswith(self.attempt.request_prefix)
                or request_id in self._ids or self._pending is not None):
            raise ValueError("Request ID is invalid, duplicated, or another request is pending")
        if not self._marked:
            self.attempt.mark_requests_started()
            self._marked = True
        self.snapshot.assert_held()
        if self.journal is not None:
            self.journal.sending(payload)
        self._ids.add(request_id)
        self._pending = deepcopy(payload)

    def after_response(self, payload: dict, response: dict) -> None:
        self.snapshot.assert_held()
        if self._pending is None or payload != self._pending:
            raise ValueError("Response does not match the pending request")
        self.sources.validate_response(payload["target_hour"], response)
        self.exchanges.append((deepcopy(payload), deepcopy(response)))
        self._pending = None

    def request_result(self, payload: dict, *, http_status, latency_ms, error) -> None:
        if self.journal is not None:
            self.journal.outcome(payload, http_status=http_status, latency_ms=latency_ms,
                                 error=None if error is None else "request_not_verified")

    def verify_audits(self) -> dict:
        self.snapshot.assert_held()
        if self._pending is not None or len(self.exchanges) != WARMUP_REQUESTS + MEASURED_REQUESTS:
            raise ValueError("A complete fixed-size request sequence is required for audit verification")
        return _benchmark_observer_verify_prediction_audit_batch(self.snapshot, self.exchanges, self.sources,
                                             request_prefix=self.attempt.request_prefix, **self.pinned_identity)


# ========================================================================
# capital_measurement_recheck
# ========================================================================

"""Independently recalculate the numeric portion of a frozen measurement report.

This is not a provenance, DB audit, resource, approval, or deployment decision.
The caller is responsible for loading the report from a trusted ledger link and
checking its exact file SHA, candidate evidence and current registry separately.
"""


import hashlib as _measurement_recheck_hashlib
import math as _measurement_recheck_math
import re as _measurement_recheck_re
import uuid

import pandas as _measurement_recheck_pd

from bike_mlops.operational import validate_operational_plan


def recheck_measurement_records(plan: dict, report: dict, *, attempt_id: str, plan_file_sha256: str) -> dict:
    """Check exact workload/order and recompute the p95 from saved request rows."""
    validate_operational_plan(plan, allow_legacy=True)
    try:
        if str(uuid.UUID(attempt_id)) != attempt_id:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("A canonical measurement attempt ID is required") from error
    if not isinstance(plan_file_sha256, str) or not _measurement_recheck_re.fullmatch(r"[0-9a-f]{64}", plan_file_sha256):
        raise ValueError("The frozen plan file SHA-256 is required")
    if not isinstance(report, dict):
        raise ValueError("Measurement report must be an object")
    if (report.get("operational_attempt_id") != attempt_id or report.get("benchmark_id") != attempt_id
            or report.get("operational_plan_sha256") != plan["plan_sha256"]
            or report.get("operational_plan_file_sha256") != plan_file_sha256 or report.get("completed") is not True):
        raise ValueError("Report does not match the completed attempt and frozen plan")
    identity = plan["identity"]
    model = report.get("model_identity")
    expected_model = {"candidate_id": identity["candidate_id"], "model_name": identity["model_name"],
                      "model_version": identity["model_version"], "artifact_sha256": identity["registered_artifact_sha256"],
                      "feature_contract_version": identity["feature_contract_version"]}
    if (not isinstance(model, dict) or any(model.get(k) != v for k, v in expected_model.items())
            or report.get("data_profile_id") != identity["data_profile_id"]
            or report.get("target_contract_id") != identity["target_contract_id"]
            or report.get("api_url") != plan["api_url"]
            or not isinstance(report.get("deployment_permission"), dict)
            or report["deployment_permission"].get("status") != "blocked"):
        raise ValueError("Report model, profile, endpoint or deployment status differs from the frozen plan")
    window = report.get("input_window")
    if (not isinstance(window, dict) or window.get("start_inclusive") != plan["input_window"]["start_inclusive"]
            or window.get("end_exclusive") != plan["input_window"]["end_exclusive"] or window.get("hours") != 168):
        raise ValueError("Report window differs from the frozen plan")
    gate = report.get("operational_gate")
    if not isinstance(gate, dict) or gate.get("status") != "not_evaluated" or not isinstance(gate.get("observed"), dict):
        raise ValueError("Report must retain an unevaluated operational gate")
    records = report.get("measurements")
    warmup, measured = plan["policy"]["warmup_requests"], plan["policy"]["measured_requests"]
    if not isinstance(records, list) or len(records) != warmup + measured:
        raise ValueError("Report does not contain all fixed measurement records")
    start = _measurement_recheck_pd.Timestamp(plan["input_window"]["start_inclusive"])
    ids, durations = [], []
    for index, row in enumerate(records):
        phase = "warmup" if index < warmup else "measured"
        phase_index = index if index < warmup else index - warmup
        request_id = f"capital-op:{attempt_id}:{phase}:{phase_index:04d}"
        target = (start + _measurement_recheck_pd.Timedelta(hours=phase_index % 168)).isoformat(sep=" ")
        if (not isinstance(row, dict) or row.get("phase") != phase or type(row.get("index")) is not int
                or row["index"] != phase_index or row.get("request_id") != request_id or row.get("target_hour") != target):
            raise ValueError("Measurement request order or target differs from the frozen plan")
        latency, prediction, data_hash = row.get("latency_ms"), row.get("prediction"), row.get("data_version_sha256")
        if (type(row.get("http_status")) is not int or row["http_status"] != 200 or row.get("error") is not None
                or type(latency) not in {int, float} or not _measurement_recheck_math.isfinite(latency) or latency < 0
                or type(prediction) not in {int, float} or not _measurement_recheck_math.isfinite(prediction) or prediction < 0
                or not isinstance(data_hash, str) or not _measurement_recheck_re.fullmatch(r"[0-9a-f]{64}", data_hash)):
            raise ValueError("Measurement contains a failed or malformed response record")
        ids.append(request_id)
        if phase == "measured":
            durations.append(float(latency))
    digest = _measurement_recheck_hashlib.sha256("\n".join(ids).encode()).hexdigest()
    observed = gate["observed"]
    expected_counts = {"warmup_requests": warmup, "warmup_errors": 0, "measured_requests": measured,
                       "measured_errors": 0, "unique_request_ids": warmup + measured,
                       "retry_count": 0, "latency_count": measured}
    if (any(type(observed.get(k)) is not int or observed[k] != v for k, v in expected_counts.items())
            or observed.get("request_id_sha256") != digest):
        raise ValueError("Reported counts or request ID digest differ from the saved records")
    rank = _measurement_recheck_math.ceil(0.95 * len(durations))
    p95 = sorted(durations)[rank - 1]
    recorded_p95 = observed.get("p95_ms")
    if type(recorded_p95) not in {int, float} or not _measurement_recheck_math.isfinite(recorded_p95) or not _measurement_recheck_math.isclose(p95, recorded_p95, rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError("Reported p95 differs from the saved latency records")
    numeric_status = "passed" if p95 <= plan["policy"]["p95_limit_ms"] else "failed"
    if gate.get("measurement_status") != numeric_status:
        raise ValueError("Reported numerical status differs from the recalculation")
    return {"numeric_status": numeric_status, "measured_requests": measured, "p95_ms": p95,
            "request_id_sha256": digest, "operational_gate": "not_evaluated", "deployment_permission": "blocked",
            "limitations": ["Recalculates saved rows only; does not independently verify DB audit, resource context, retry behavior or file authenticity."]}


def recheck_linked_measurement(candidate_id: str, attempt_id: str, *, ledger_path=None, lock_dir=None) -> dict:
    """Recheck only a ledger-linked report while candidate and ledger locks are held.

    This remains a numeric check, not independent audit, resource or approval proof.
    Missing locks, changed files and incomplete attempts are rejected; nothing is written.
    """
    from bike_mlops import operational as ledger
    from bike_mlops import operational_validation as recovery

    if not isinstance(candidate_id, str) or not _measurement_recheck_re.fullmatch(r"[0-9a-f]{64}", candidate_id):
        raise ValueError("A lowercase candidate ID is required")
    if not recovery._canonical_attempt(attempt_id):
        raise ValueError("A canonical measurement attempt ID is required")
    ledger_path = ledger_path if ledger_path is not None else ledger.DEFAULT_LEDGER
    lock_dir = lock_dir if lock_dir is not None else ledger.DEFAULT_LOCK_DIR
    with recovery._existing_lock(lock_dir / f"{candidate_id}.lock"):
        with recovery._existing_lock(ledger_path.with_suffix(ledger_path.suffix + ".lock")):
            state = ledger.read_operational_ledger(ledger_path)
            record = next((item for item in state["records"] if item["candidate_id"] == candidate_id), None)
            if record is None:
                raise ValueError("Candidate has no operational attempt record")
            attempt = next((item for item in record["attempts"] if item["attempt_id"] == attempt_id), None)
            if attempt is None or attempt.get("status") != "completed":
                raise ValueError("A completed ledger-linked measurement is required")
            plan, plan_status = recovery._plan_check(attempt, candidate_id)
            if plan is None or plan_status != "matched":
                raise ValueError("Frozen operational plan does not match the ledger")
            report_path = f"data/reports/capital-bikeshare/operational-{attempt_id}.json"
            descriptor = attempt.get("report")
            if not isinstance(descriptor, dict) or descriptor.get("path") != report_path:
                raise ValueError("Ledger does not link the canonical measurement report")
            report, digest, error = recovery._read_report(recovery.PROJECT_ROOT / report_path)
            if error or digest != descriptor.get("sha256"):
                raise ValueError("Ledger-linked measurement report is missing, invalid or has a different SHA-256")
            issue = recovery._report_error(report, attempt, candidate_id, plan, linked=True)
            if issue:
                raise ValueError(f"Ledger-linked measurement report identity or outcome is invalid: {issue}")
            recovery.verify_completed_journal(attempt, candidate_id, plan, report)
            result = recheck_measurement_records(plan, report, attempt_id=attempt_id,
                                                  plan_file_sha256=attempt["plan_file_sha256"])
            return {**result, "candidate_id": candidate_id, "attempt_id": attempt_id,
                    "report_path": report_path, "report_sha256": digest, "ledger_binding": "matched"}


# ========================================================================
# capital_operational_recovery
# ========================================================================

"""Read-only inspection of operational attempt evidence; never adopt or repair it."""


import fcntl
import hashlib as _operational_recovery_hashlib
import json as _operational_recovery_json
import os
import re as _operational_recovery_re
import stat
import uuid as _operational_recovery_uuid
from contextlib import contextmanager as _operational_recovery_contextmanager
from pathlib import Path as _operational_recovery_Path

from bike_mlops import operational as ledger
from bike_mlops.config import PROJECT_ROOT
from bike_mlops.operational import inspect_request_journal

_REPORT_PATTERN = _operational_recovery_re.compile(r"^operational-([0-9a-f-]{36})(-failure)?\.json$")


@_operational_recovery_contextmanager
def _existing_lock(path: _operational_recovery_Path):
    """Acquire an existing cooperative lock without creating or modifying it."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise RuntimeError(f"Cannot open existing inspection lock: {path.name}") from error
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RuntimeError("Inspection lock is not a regular file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Inspection refused: candidate or ledger is locked") from error
        yield
    finally:
        os.close(fd)


def _canonical_attempt(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(_operational_recovery_uuid.UUID(value)) == value
    except ValueError:
        return False


def _plan_check(attempt: dict, candidate_id: str) -> tuple[dict | None, str]:
    try:
        plan, _, file_hash = ledger._read_plan(PROJECT_ROOT / attempt["plan_path"], allow_legacy=True)
        if (plan["identity"]["candidate_id"] != candidate_id or plan["plan_sha256"] != attempt["plan_sha256"]
                or file_hash != attempt["plan_file_sha256"]):
            return None, "changed_or_mismatched"
    except (KeyError, OSError, TypeError, ValueError):
        return None, "unreadable_or_invalid"
    return plan, "matched"


def inspect_attempt_journal(attempt: dict, candidate_id: str, plan: dict | None) -> dict:
    if plan is None:
        return {"status": "plan_not_matched"}
    if "request_journal" not in plan["policy"]:
        return {"status": "legacy_not_required", "interpretation": "no_durable_request_evidence"}
    path = PROJECT_ROOT / "data/reports/capital-bikeshare" / f"operational-{attempt['attempt_id']}.requests.jsonl"
    try:
        return inspect_request_journal(path, attempt_id=attempt["attempt_id"], candidate_id=candidate_id,
                                       plan=plan, plan_file_sha256=attempt["plan_file_sha256"])
    except (OSError, ValueError, KeyError, TypeError):
        return {"status": "invalid", "interpretation": "no_request_outcome_can_be_inferred"}


def verify_completed_journal(attempt: dict, candidate_id: str, plan: dict, report: dict) -> None:
    """Bind local report publication/recheck to all durable, verified request times."""
    if "request_journal" not in plan["policy"]:
        return  # Historical numeric inspection is allowed; new execution rejects old plans.
    journal = inspect_attempt_journal(attempt, candidate_id, plan)
    descriptor = report.get("request_journal")
    rows = report.get("measurements")
    if (journal["status"] != "readable" or journal["counts"].get("response_verified") != 1050
            or not isinstance(descriptor, dict)
            or descriptor.get("file_name") != f"operational-{attempt['attempt_id']}.requests.jsonl"
            or descriptor.get("sha256") != journal.get("sha256")
            or not isinstance(rows, list) or len(rows) != 1050
            or any(not isinstance(row, dict)
                   or journal["verified_latencies_ms"].get(row.get("request_id")) != row.get("latency_ms") for row in rows)):
        raise ValueError("Durable request journal is incomplete or differs from the report")


def _read_report(path: _operational_recovery_Path) -> tuple[dict | None, str | None, str | None]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None, None, "missing"
    except OSError:
        return None, None, "unreadable_or_symlink"
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            return None, None, "not_regular_file"
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            raw = stream.read(16 * 1024 * 1024 + 1)
            after = os.fstat(stream.fileno())
        current = os.stat(path, follow_symlinks=False)
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino)):
            return None, None, "file_changed_during_inspection"
    except OSError:
        return None, None, "unreadable_or_changed"
    finally:
        if fd != -1:
            os.close(fd)
    if len(raw) > 16 * 1024 * 1024:
        return None, None, "report_too_large"
    digest = _operational_recovery_hashlib.sha256(raw).hexdigest()
    try:
        body = _operational_recovery_json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None, digest, "invalid_json"
    if not isinstance(body, dict):
        return None, digest, "invalid_json_object"
    return body, digest, None


def _report_error(body: dict, attempt: dict, candidate_id: str, plan: dict | None, *, linked: bool) -> str | None:
    if (body.get("operational_attempt_id") != attempt["attempt_id"]
            or body.get("operational_plan_sha256") != attempt["plan_sha256"]
            or body.get("operational_plan_file_sha256") != attempt["plan_file_sha256"]):
        return "attempt_or_plan_mismatch"
    if "candidate_id" in body and body["candidate_id"] != candidate_id:
        return "candidate_mismatch"
    if plan is not None and any(key in body and body[key] != plan["identity"][key]
                                for key in ("data_profile_id", "target_contract_id")):
        return "profile_or_target_mismatch"
    identity = body.get("model_identity")
    if identity is not None:
        if not isinstance(identity, dict) or identity.get("candidate_id") != candidate_id:
            return "candidate_mismatch"
        if plan is not None:
            expected = plan["identity"]
            if any(identity.get(key) != value for key, value in {
                "model_name": expected["model_name"], "model_version": expected["model_version"],
                "artifact_sha256": expected["registered_artifact_sha256"],
            }.items()):
                return "model_identity_mismatch"
    if linked:
        status = attempt["status"]
        gate = body.get("operational_gate")
        if not isinstance(gate, dict):
            return "invalid_gate"
        if status == "completed" and (body.get("completed") is not True or identity is None
                                      or gate.get("status") not in {"not_evaluated", "passed"}):
            return "outcome_mismatch"
        if status == "failed" and (body.get("completed") is not False or gate.get("status") != "failed"):
            return "outcome_mismatch"
        if status not in {"completed", "failed"}:
            return "nonterminal_attempt_has_report_link"
        recorded = attempt.get("report") or {}
        if gate.get("status") != recorded.get("gate_status"):
            return "gate_status_mismatch"
    return None


def inspect_operational_reports(candidate_id: str, *, ledger_path: _operational_recovery_Path = ledger.DEFAULT_LEDGER,
                                lock_dir: _operational_recovery_Path = ledger.DEFAULT_LOCK_DIR) -> dict:
    """List evidence states while both existing candidate and ledger locks are held.

    Files and ledger remain unchanged. Even a linked or matching orphan is not
    an operational gate decision. An unrelated unassociated filename is listed
    separately because its candidate identity cannot be established safely.
    """
    if not isinstance(candidate_id, str) or not _operational_recovery_re.fullmatch(r"[0-9a-f]{64}", candidate_id):
        raise ValueError("A lowercase candidate ID is required")
    if not ledger_path.is_file():
        raise ValueError("Operational ledger does not exist")
    report_root = PROJECT_ROOT / "data/reports/capital-bikeshare"
    if not report_root.resolve().is_relative_to(PROJECT_ROOT.resolve()):
        raise ValueError("Operational report directory is outside the project")
    with _existing_lock(lock_dir / f"{candidate_id}.lock"):
        with _existing_lock(ledger_path.with_suffix(ledger_path.suffix + ".lock")):
            state = ledger.read_operational_ledger(ledger_path)
            record = next((item for item in state["records"] if item["candidate_id"] == candidate_id), None)
            if record is None:
                raise ValueError("Candidate has no operational ledger record")
            all_ids = {item["attempt_id"] for rec in state["records"] for item in rec["attempts"]}
            attempts, examined = [], set()
            for attempt in record["attempts"]:
                attempt_id = attempt["attempt_id"]
                if not _canonical_attempt(attempt_id):
                    raise ValueError("Ledger contains a noncanonical attempt ID")
                plan, plan_status = _plan_check(attempt, candidate_id)
                journal = inspect_attempt_journal(attempt, candidate_id, plan)
                names = [f"operational-{attempt_id}.json", f"operational-{attempt_id}-failure.json"]
                examined.update(names)
                item = {"attempt_id": attempt_id, "status": attempt["status"], "plan_status": plan_status,
                        "requests_started": bool(attempt.get("requests_started_at_utc")),
                        "partial_evidence_missing": attempt.get("partial_evidence_missing"),
                        "linked_report": {"status": "not_recorded"}, "orphans": []}
                item["request_journal"] = journal
                descriptor = attempt.get("report")
                linked_name = None
                if descriptor is not None:
                    if not isinstance(descriptor, dict) or not isinstance(descriptor.get("path"), str):
                        item["linked_report"] = {"status": "invalid", "reason": "invalid_descriptor"}
                    else:
                        relative = descriptor["path"]
                        expected_relatives = {f"data/reports/capital-bikeshare/{name}": name for name in names}
                        linked_name = expected_relatives.get(relative)
                        if linked_name is None:
                            item["linked_report"] = {"status": "invalid", "reason": "unexpected_report_path"}
                        else:
                            body, digest, error = _read_report(report_root / linked_name)
                            if error:
                                item["linked_report"] = {"status": "missing" if error == "missing" else "invalid", "reason": error}
                            elif digest != descriptor.get("sha256"):
                                item["linked_report"] = {"status": "invalid", "reason": "file_sha256_mismatch", "sha256": digest}
                            else:
                                issue = _report_error(body, attempt, candidate_id, plan, linked=True)
                                if plan is None:
                                    issue = issue or "plan_not_matched"
                                item["linked_report"] = {"status": "invalid" if issue else "matched",
                                                         "reason": issue, "sha256": digest, "path": relative}
                                if (issue is None and plan is not None and "request_journal" in plan["policy"]
                                        and (attempt.get("requests_started_at_utc") or journal["status"] != "missing")):
                                    expected_name = f"operational-{attempt_id}.requests.jsonl"
                                    descriptor = body.get("request_journal")
                                    if (not isinstance(descriptor, dict) or descriptor.get("file_name") != expected_name
                                            or descriptor.get("sha256") != journal.get("sha256")):
                                        item["linked_report"].update(status="invalid", reason="journal_binding_mismatch")
                                item["linked_report"]["candidate_identity_source"] = (
                                    "report_and_plan" if isinstance(body.get("model_identity"), dict)
                                    else "ledger_and_plan") if plan is not None else "unverified"
                for name in names:
                    if name == linked_name:
                        continue
                    body, digest, error = _read_report(report_root / name)
                    if error == "missing":
                        continue
                    issue = error or _report_error(body, attempt, candidate_id, plan, linked=False)
                    if plan is None:
                        issue = issue or "plan_not_matched"
                    item["orphans"].append({"path": f"data/reports/capital-bikeshare/{name}",
                                            "status": "invalid" if issue else "matches_attempt_not_adopted",
                                            "reason": issue, "sha256": digest})
                item["unresolved"] = (attempt["status"] in {"started", "interrupted"}
                                      or (attempt["status"] in {"completed", "failed"}
                                          and item["linked_report"]["status"] == "not_recorded")
                                      or item["linked_report"]["status"] in {"missing", "invalid"}
                                      or bool(item["orphans"]) or plan_status != "matched")
                if (journal["status"] not in {"readable", "legacy_not_required"}
                        and not (journal["status"] == "missing" and not attempt.get("requests_started_at_utc")
                                 and attempt["status"] == "failed")):
                    item["unresolved"] = True
                if (attempt["status"] == "completed" and journal["status"] == "readable"
                        and journal["counts"].get("response_verified") != 1050):
                    item["unresolved"] = True
                item["evidence_state"] = (
                    "unfinished" if attempt["status"] in {"started", "interrupted"}
                    else "incomplete_or_corrupt" if item["linked_report"]["status"] != "matched" or plan_status != "matched"
                    else "linked_with_orphans" if item["orphans"]
                    else "linked")
                attempts.append(item)
            unassociated = []
            if report_root.is_dir():
                for path in sorted(report_root.glob("operational-*.json")):
                    if path.name in examined:
                        continue
                    match = _REPORT_PATTERN.fullmatch(path.name)
                    if not match or not _canonical_attempt(match.group(1)):
                        unassociated.append({"name": path.name, "reason": "unrecognized_filename"})
                    elif match.group(1) not in all_ids:
                        unassociated.append({"name": path.name, "reason": "unknown_attempt_id"})
            return {"candidate_id": candidate_id, "ledger_status": record["status"], "attempts": attempts,
                    "unassociated_report_files": unassociated, "decision": "inspection_only_no_approval"}


# ========================================================================
# capital_operational_registry
# ========================================================================

"""Attach immutable operational evidence references to one blocked MLflow version.

This records evidence only. It deliberately never changes operational_gate_status
or deployment_permission and cannot approve or promote a model.
"""


import fcntl as _operational_registry_fcntl
import hashlib as _operational_registry_hashlib
import os as _operational_registry_os
import re as _operational_registry_re
import stat as _operational_registry_stat
import uuid as _operational_registry_uuid
from contextlib import contextmanager as _operational_registry_contextmanager
from pathlib import Path as _operational_registry_Path
from urllib.parse import urlsplit

from bike_mlops import operational as _operational_registry_ledger
from bike_mlops import operational_validation as recovery
_operational_registry_recheck_measurement_records = recheck_measurement_records
from bike_mlops.config import DATA_PROFILE_ID, MODEL_NAME, PROJECT_ROOT as _operational_registry_PROJECT_ROOT, TARGET_CONTRACT_ID
from bike_mlops.evaluation import BOOTSTRAP_QUALITY_GATE_POLICY, BOOTSTRAP_QUALITY_GATE_POLICY_SHA256
from bike_mlops.config import _profiles_load_data_profile as load_data_profile

_OPERATIONAL_TAG_PREFIX = "capital_operational_"


@_operational_registry_contextmanager
def _version_record_lock(model_name: str, version: str):
    """Serialize cooperating evidence writers for one exact local model version."""
    if not _operational_registry_re.fullmatch(r"bike-demand-capital", model_name) or not version.isdecimal() or int(version) <= 0:
        raise ValueError("Invalid Capital registry version identity")
    directory = _operational_registry_PROJECT_ROOT / "data/evaluations/capital-bikeshare/registry-record-locks"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (directory.is_symlink() or not directory.resolve().is_relative_to(_operational_registry_PROJECT_ROOT.resolve())
            or directory.stat().st_mode & 0o077):
        raise PermissionError("Registry record lock directory permissions must exclude group and other users")
    path = directory / f"{model_name}-v{version}.lock"
    flags = _operational_registry_os.O_CREAT | _operational_registry_os.O_RDWR | _operational_registry_os.O_CLOEXEC | getattr(_operational_registry_os, "O_NOFOLLOW", 0)
    descriptor = _operational_registry_os.open(path, flags, 0o600)
    try:
        info = _operational_registry_os.fstat(descriptor)
        if not _operational_registry_stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise PermissionError("Registry record lock must be a private regular file")
        try:
            _operational_registry_fcntl.flock(descriptor, _operational_registry_fcntl.LOCK_EX | _operational_registry_fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another operational evidence recorder holds this model-version lock") from error
        yield
    finally:
        _operational_registry_os.close(descriptor)


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
    if not _operational_registry_re.fullmatch(r"[0-9a-f]{64}", candidate_id):
        raise ValueError("A lowercase candidate ID is required")
    try:
        if str(_operational_registry_uuid.UUID(attempt_id)) != attempt_id:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("A canonical measurement attempt ID is required") from error

    ledger_path = _operational_registry_ledger.DEFAULT_LEDGER
    lock_dir = _operational_registry_ledger.DEFAULT_LOCK_DIR
    with recovery._existing_lock(lock_dir / f"{candidate_id}.lock"):
        with recovery._existing_lock(ledger_path.with_suffix(ledger_path.suffix + ".lock")):
            state = _operational_registry_ledger.read_operational_ledger(ledger_path)
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
            report_path = _operational_registry_PROJECT_ROOT / expected_path
            report, report_sha256, issue = recovery._read_report(report_path)
            if issue or report_sha256 != descriptor.get("sha256"):
                raise ValueError("Ledger-linked report is unreadable or its SHA-256 differs")
            issue = recovery._report_error(report, attempt, candidate_id, plan, linked=True)
            if issue:
                raise ValueError("Ledger-linked report does not match the attempt: " + issue)
            recovery.verify_completed_journal(attempt, candidate_id, plan, report)
            numeric = _operational_registry_recheck_measurement_records(plan, report, attempt_id=attempt_id,
                                                  plan_file_sha256=attempt["plan_file_sha256"])
            if numeric.get("numeric_status") != report["operational_gate"].get("measurement_status"):
                raise ValueError("Independent numeric recheck differs from the saved report")
            tags_to_record = _validate_completed_evidence(
                plan, report, report_sha256, attempt_id, attempt["plan_file_sha256"]
            )
            if (report["model_identity"]["model_version"] != plan["identity"]["model_version"]
                    or report["model_identity"]["artifact_sha256"] != plan["identity"]["registered_artifact_sha256"]):
                raise ValueError("Report is not tied to the frozen registered model version and artifact")

            registry_uri = _operational_registry_os.getenv("MLFLOW_TRACKING_URI", "")
            parsed = urlsplit(registry_uri)
            if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}
                    or parsed.port != 5001 or parsed.path not in {"", "/"}
                    or parsed.query or parsed.fragment or registry_uri.rstrip("/") != plan["registry_uri"]):
                raise ValueError("MLFLOW_TRACKING_URI must match the frozen local Capital registry")
            if _operational_registry_os.getenv("MLFLOW_REGISTRY_URI", registry_uri).rstrip("/") != plan["registry_uri"]:
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


# ========================================================================
# capital_measurement_evidence
# ========================================================================

"""Archive and independently recalculate synthetic Capital measurement evidence.

The verifier consumes only the ZIP path. It does not use Docker, PostgreSQL,
MLflow, environment credentials, or the harness workspace.
"""


import argparse
import hashlib as _measurement_evidence_hashlib
import io
import json as _measurement_evidence_json
import os as _measurement_evidence_os
import re as _measurement_evidence_re
import shutil
import stat as _measurement_evidence_stat
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path as _measurement_evidence_Path
from urllib.parse import urlsplit as _measurement_evidence_urlsplit

_measurement_evidence_recheck_measurement_records = recheck_measurement_records
from bike_mlops.operational import read_operational_ledger
from bike_mlops.operational import JOURNAL_POLICY, inspect_request_journal as _measurement_evidence_inspect_request_journal

MEMBERS = {
    "payload/plan.json": "plan",
    "payload/report.json": "report",
    "payload/operational-ledger.json": "operational_ledger",
    "payload/request-journal.jsonl": "request_journal",
}
MANIFEST = "manifest.json"
MAX_MEMBER_BYTES = 12 * 1024 * 1024
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
SENSITIVE_KEY = _measurement_evidence_re.compile(r"(?:password|passwd|secret|token|credential|dsn|api[_-]?key|private[_-]?key|authorization|access[_-]?key|raw[_-]?env|command[_-]?line|argv)", _measurement_evidence_re.I)
ABSOLUTE_PATH = _measurement_evidence_re.compile(r"^(?:/|[A-Za-z]:[\\/]|\\\\)")


def _sha(raw: bytes) -> str:
    return _measurement_evidence_hashlib.sha256(raw).hexdigest()


def _json(raw: bytes, label: str) -> dict:
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"{label} contains a duplicate JSON key")
            value[key] = item
        return value
    try:
        value = _measurement_evidence_json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON")))
    except (UnicodeDecodeError, _measurement_evidence_json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"{label} is not strict JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _assert_no_sensitive_or_external(value, *, key="") -> None:
    if SENSITIVE_KEY.search(key):
        raise ValueError(f"Evidence contains a sensitive field name: {key}")
    if key.lower() in {"environment", "env"}:
        raise ValueError("Raw environment data is not eligible for the evidence archive")
    if isinstance(value, dict):
        for child_key, child in value.items():
            _assert_no_sensitive_or_external(child, key=str(child_key))
    elif isinstance(value, list):
        for child in value:
            _assert_no_sensitive_or_external(child, key=key)
    elif isinstance(value, str):
        docker_inventory_row = key == "containers" and _measurement_evidence_re.fullmatch(
            r"/bikemlopstest[0-9a-f]{16}-[a-z0-9-]+-\d+\|[a-zA-Z0-9_.:/-]+\|\d+\|\d+", value)
        # An at sign in a hardware description is not URI userinfo. Continue
        # rejecting scheme authorities (also embedded in text), bare user@host,
        # credential-shaped tokens and email addresses, without copying secrets.
        credential_authority = _measurement_evidence_re.search(
            r"(?:[A-Za-z][A-Za-z0-9+.-]*://[^\s/]*@|(?<!\S)[^/\s@]+@[^\s/]+)", value)
        if (ABSOLUTE_PATH.match(value) and not docker_inventory_row) or credential_authority:
            category = "cpu_model" if key == "cpu_model" else "other_string"
            raise ValueError(f"Evidence contains an absolute path or URI credentials (category={category})")
        if _measurement_evidence_re.match(r"^[a-z][a-z0-9+.-]*://", value, _measurement_evidence_re.I):
            parsed = _measurement_evidence_urlsplit(value)
            if parsed.scheme and (parsed.username is not None or parsed.password is not None):
                raise ValueError("Evidence contains URI credentials")
            if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}:
                raise ValueError("Evidence contains a non-local URI")


def _safe_relative(value) -> bool:
    return (isinstance(value, str) and bool(value) and not ABSOLUTE_PATH.match(value)
            and not _measurement_evidence_re.match(r"^[A-Za-z]:", value) and "\\" not in value
            and ".." not in _measurement_evidence_Path(value).parts and _measurement_evidence_Path(value).as_posix() == value and value != ".")


def _no_symlinks(path: _measurement_evidence_Path) -> None:
    if ".." in path.parts:
        raise ValueError("Evidence path contains traversal")
    if any(part.is_symlink() for part in (path.absolute(), *path.absolute().parents)):
        raise ValueError("Evidence path contains a symlink")


def _read_regular(path: _measurement_evidence_Path, *, max_bytes: int = MAX_MEMBER_BYTES) -> bytes:
    _no_symlinks(path)
    absolute = path.absolute()
    directory = _measurement_evidence_os.open(absolute.anchor, _measurement_evidence_os.O_RDONLY | _measurement_evidence_os.O_DIRECTORY | _measurement_evidence_os.O_CLOEXEC)
    try:
        for part in absolute.parts[1:-1]:
            child = _measurement_evidence_os.open(part, _measurement_evidence_os.O_RDONLY | _measurement_evidence_os.O_DIRECTORY | _measurement_evidence_os.O_CLOEXEC | _measurement_evidence_os.O_NOFOLLOW, dir_fd=directory)
            _measurement_evidence_os.close(directory)
            directory = child
        fd = _measurement_evidence_os.open(absolute.name, _measurement_evidence_os.O_RDONLY | _measurement_evidence_os.O_CLOEXEC | _measurement_evidence_os.O_NOFOLLOW | _measurement_evidence_os.O_NONBLOCK, dir_fd=directory)
    finally:
        _measurement_evidence_os.close(directory)
    try:
        before = _measurement_evidence_os.fstat(fd)
        if not _measurement_evidence_stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
            raise ValueError("Evidence input must be a bounded regular file")
        with _measurement_evidence_os.fdopen(fd, "rb") as stream:
            fd = -1
            raw = stream.read(max_bytes + 1)
            after = _measurement_evidence_os.fstat(stream.fileno())
        sig = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if len(raw) > max_bytes or sig(before) != sig(after) or sig(_measurement_evidence_os.stat(path, follow_symlinks=False)) != sig(after):
            raise ValueError("Evidence input changed while being read or exceeded its limit")
        return raw
    finally:
        if fd >= 0:
            _measurement_evidence_os.close(fd)


def _validate_archive(archive: _measurement_evidence_Path) -> dict:
    archive_raw = _read_regular(archive, max_bytes=MAX_ARCHIVE_BYTES)
    with zipfile.ZipFile(io.BytesIO(archive_raw), "r") as bundle:
        infos = bundle.infolist()
        names = [item.filename for item in infos]
        expected = {MANIFEST, *MEMBERS}
        if len(names) != len(set(names)):
            raise ValueError("Archive contains duplicate members")
        if set(names) != expected or len(names) != len(expected):
            raise ValueError("Archive member allowlist mismatch")
        contents = {}
        for item in infos:
            name = item.filename
            unix_mode = item.external_attr >> 16
            mode_kind = _measurement_evidence_stat.S_IFMT(unix_mode)
            if (name.startswith("/") or "\\" in name or ".." in _measurement_evidence_Path(name).parts
                    or mode_kind not in {0, _measurement_evidence_stat.S_IFREG} or item.file_size > MAX_MEMBER_BYTES
                    or item.compress_type != zipfile.ZIP_STORED):
                raise ValueError("Archive contains an unsafe, compressed or oversized member")
            raw = bundle.read(item)
            if len(raw) != item.file_size:
                raise ValueError("Archive member size changed during read")
            contents[name] = raw
    manifest = _json(contents[MANIFEST], "manifest")
    if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1 or manifest.get("synthetic_only") is not True:
        raise ValueError("Unsupported or non-synthetic archive manifest")
    expected_manifest_fields = {"schema_version", "synthetic_only", "candidate_id", "attempt_id",
                                "source_revision", "member_map", "members", "verification_scope", "limitations"}
    if set(manifest) != expected_manifest_fields:
        raise ValueError("Manifest fields differ from the fixed evidence schema")
    revision = manifest.get("source_revision")
    if (not isinstance(revision, dict) or set(revision) != {"commit", "dirty"}
            or not isinstance(revision.get("commit"), str)
            or not _measurement_evidence_re.fullmatch(r"[0-9a-f]{40,64}", revision["commit"])
            or type(revision.get("dirty")) is not bool):
        raise ValueError("Manifest source revision is invalid")
    if not isinstance(manifest.get("limitations"), list) or not all(isinstance(item, str) for item in manifest["limitations"]):
        raise ValueError("Manifest limitations must be a list of text")
    _assert_no_sensitive_or_external(manifest)
    if manifest.get("member_map") != {name: MEMBERS[name] for name in MEMBERS}:
        raise ValueError("Manifest member map differs from the fixed allowlist")
    descriptors = manifest.get("members")
    if not isinstance(descriptors, dict) or set(descriptors) != set(MEMBERS):
        raise ValueError("Manifest payload descriptors are incomplete")
    for name in MEMBERS:
        descriptor = descriptors[name]
        raw = contents[name]
        if (not isinstance(descriptor, dict) or set(descriptor) != {"source_relative_path", "sha256", "bytes"}
                or descriptor.get("sha256") != _sha(raw)
                or type(descriptor.get("bytes")) is not int or descriptor["bytes"] != len(raw)
                or not _safe_relative(descriptor.get("source_relative_path"))):
            raise ValueError(f"Manifest hash, size or source path mismatch for {name}")

    plan_raw, report_raw = contents["payload/plan.json"], contents["payload/report.json"]
    ledger_raw, journal_raw = contents["payload/operational-ledger.json"], contents["payload/request-journal.jsonl"]
    plan, report = _json(plan_raw, "plan"), _json(report_raw, "report")
    _assert_no_sensitive_or_external(_json(ledger_raw, "operational ledger"))
    _assert_no_sensitive_or_external(_journal_json_lines(journal_raw))
    _assert_no_sensitive_or_external(plan)
    _assert_no_sensitive_or_external(report)
    candidate_id, attempt_id = manifest.get("candidate_id"), manifest.get("attempt_id")
    if (not isinstance(candidate_id, str) or plan.get("identity", {}).get("candidate_id") != candidate_id
            or report.get("model_identity", {}).get("candidate_id") != candidate_id
            or report.get("operational_attempt_id") != attempt_id):
        raise ValueError("Manifest, plan and report candidate/attempt identities differ")
    report_relative = f"data/reports/capital-bikeshare/operational-{attempt_id}.json"
    journal_relative = str(_measurement_evidence_Path(report_relative).with_suffix(".requests.jsonl"))
    if (descriptors["payload/report.json"]["source_relative_path"] != report_relative
            or descriptors["payload/operational-ledger.json"]["source_relative_path"] != "data/evaluations/capital-bikeshare/operational-attempts.json"
            or descriptors["payload/request-journal.jsonl"]["source_relative_path"] != journal_relative):
        raise ValueError("Archived report/ledger/journal source path differs from the fixed attempt")
    plan_sha = _sha(plan_raw)
    record = None
    with tempfile.TemporaryDirectory(prefix="capital-measurement-archive-verify-") as directory:
        root = _measurement_evidence_Path(directory)
        ledger_path = root / "operational-attempts.json"
        ledger_path.write_bytes(ledger_raw)
        ledger = read_operational_ledger(ledger_path)
        record = next((row for row in ledger["records"] if row.get("candidate_id") == candidate_id), None)
        if record is None:
            raise ValueError("Candidate is absent from the archived ledger")
        attempt = next((row for row in record["attempts"] if row.get("attempt_id") == attempt_id), None)
        if (attempt is None or attempt.get("status") != "completed" or attempt.get("plan_sha256") != plan.get("plan_sha256")
                or attempt.get("plan_file_sha256") != plan_sha or attempt.get("plan_path") != manifest["members"]["payload/plan.json"]["source_relative_path"]
                or not isinstance(attempt.get("report"), dict)
                or attempt["report"].get("sha256") != _sha(report_raw)
                or attempt["report"].get("path") != manifest["members"]["payload/report.json"]["source_relative_path"]
                or attempt["report"].get("gate_status") != "not_evaluated"):
            raise ValueError("Archived ledger does not bind the exact completed attempt, plan and report")
        if (plan.get("deployment_permission") != "blocked"
                or report.get("deployment_permission", {}).get("status") != "blocked"
                or report.get("operational_gate", {}).get("status") != "not_evaluated"):
            raise ValueError("Archive changes the blocked/not-evaluated operational state")
        descriptor = report.get("request_journal")
        if not isinstance(descriptor, dict):
            raise ValueError("Report request journal descriptor is missing")
        journal_name = descriptor.get("file_name")
        if (journal_name != _measurement_evidence_Path(journal_relative).name
                or descriptor.get("sha256") != _sha(journal_raw)
                or descriptor.get("events") != 2101
                or descriptor.get("policy_id") != JOURNAL_POLICY["policy_id"]):
            raise ValueError("Report journal descriptor does not bind the archived journal")
        journal_path = root / journal_name
        journal_path.write_bytes(journal_raw)
        journal = _measurement_evidence_inspect_request_journal(journal_path, attempt_id=attempt_id, candidate_id=candidate_id,
                                          plan=plan, plan_file_sha256=plan_sha)
        if journal.get("status") != "readable" or journal.get("sha256") != _sha(journal_raw):
            raise ValueError("Request journal is incomplete, torn or changed")
        numeric = _measurement_evidence_recheck_measurement_records(plan, report, attempt_id=attempt_id, plan_file_sha256=plan_sha)
        latencies = journal.get("verified_latencies_ms", {})
        if len(latencies) != 1050 or len(report.get("measurements", [])) != 1050:
            raise ValueError("The request journal and report must each contain all 1,050 responses")
        for row in report["measurements"]:
            if latencies.get(row.get("request_id")) != row.get("latency_ms"):
                raise ValueError("Request journal latency differs from report row")
        if journal.get("counts", {}).get("response_verified") != 1050:
            raise ValueError("Request journal does not confirm all 1,050 successful responses")
    return {
        "status": "archive_recalculated", "synthetic_only": True,
        "candidate_id": candidate_id, "attempt_id": attempt_id,
        "archive_sha256": _sha(archive_raw),
        "members": {name: {"sha256": _sha(contents[name]), "bytes": len(contents[name])} for name in MEMBERS},
        "warmup_requests_excluded": 50, "measured_requests_recalculated": numeric["measured_requests"],
        "measured_errors": 0, "p95_ms": numeric["p95_ms"], "numeric_status": numeric["numeric_status"],
        "request_id_sha256": numeric["request_id_sha256"],
        "journal_verified_responses": 1050, "operational_gate": "not_evaluated",
        "deployment_permission": "blocked",
        "limitations": ["Synthetic evidence only; does not prove real-data performance, clock qualification, DB audit outside the saved journal, or deployment approval."],
    }


def verify_archive_process(archive: _measurement_evidence_Path) -> dict:
    return _validate_archive(archive)


def _workspace_marker(root: _measurement_evidence_Path, project: str, token: str) -> _measurement_evidence_Path:
    _no_symlinks(root)
    resolved = root.resolve(strict=True)
    tmp = _measurement_evidence_Path(tempfile.gettempdir()).resolve(strict=True)
    if (resolved.parent != tmp or not resolved.name.startswith("capital-system-harness-")
            or not _measurement_evidence_stat.S_ISDIR(resolved.lstat().st_mode) or resolved.is_symlink()
            or resolved.lstat().st_uid != _measurement_evidence_os.geteuid() or resolved.lstat().st_mode & 0o077):
        raise ValueError("Export accepts only the harness-owned temporary workspace")
    marker = resolved / ".t16-workspace-identity.json"
    payload = _measurement_evidence_json.dumps({"project": project, "token": token}, sort_keys=True).encode()
    fd = _measurement_evidence_os.open(marker, _measurement_evidence_os.O_WRONLY | _measurement_evidence_os.O_CREAT | _measurement_evidence_os.O_EXCL | _measurement_evidence_os.O_CLOEXEC | _measurement_evidence_os.O_NOFOLLOW, 0o600)
    try:
        _measurement_evidence_os.write(fd, payload)
        _measurement_evidence_os.fsync(fd)
    finally:
        _measurement_evidence_os.close(fd)
    return marker


def export_synthetic_evidence(*, root: _measurement_evidence_Path, project: str, token: str, candidate_id: str,
                              attempt_id: str, plan_relative: str, report_path: _measurement_evidence_Path,
                              evidence_dir: _measurement_evidence_Path, source_commit: str, source_dirty: bool) -> dict:
    """Copy four exact originals, verify them in another process, then return descriptor."""
    _no_symlinks(root)
    marker = root / ".t16-workspace-identity.json"
    if not _measurement_evidence_re.fullmatch(r"[0-9a-f]{40,64}", source_commit) or type(source_dirty) is not bool:
        raise ValueError("Source revision metadata is invalid")
    marker_stat = marker.lstat()
    if (not _measurement_evidence_stat.S_ISREG(marker_stat.st_mode) or marker_stat.st_mode & 0o077
            or marker_stat.st_uid != _measurement_evidence_os.geteuid()):
        raise ValueError("Harness workspace marker has unsafe type or permissions")
    marker_value = _json(_read_regular(marker, max_bytes=4096), "workspace marker")
    if marker_value != {"project": project, "token": token}:
        raise ValueError("Harness workspace marker identity mismatch")
    workspace = root.resolve(strict=True)
    workspace_stat = workspace.lstat()
    if (workspace.parent != _measurement_evidence_Path(tempfile.gettempdir()).resolve(strict=True)
            or not workspace.name.startswith("capital-system-harness-")
            or workspace_stat.st_uid != _measurement_evidence_os.geteuid() or workspace_stat.st_mode & 0o077):
        raise ValueError("Export source is not a private harness temporary workspace")
    output_base = (_measurement_evidence_Path(__file__).resolve().parents[2] / "data" / "reports").resolve()
    if evidence_dir.is_symlink() or evidence_dir.exists():
        raise FileExistsError("Evidence output directory already exists or is a symlink")
    _no_symlinks(evidence_dir)
    output_dir = evidence_dir.resolve()
    if not output_dir.is_relative_to(output_base) or output_dir == output_base:
        raise ValueError("Evidence output directory must be new and under data/reports")
    output_dir.mkdir(parents=True, mode=0o700)
    try:
        relative_plan = _measurement_evidence_Path(plan_relative)
        if not _safe_relative(plan_relative):
            raise ValueError("Plan source path must be a safe workspace-relative path")
        source_paths = {
            "payload/plan.json": workspace / relative_plan,
            "payload/report.json": report_path,
            "payload/operational-ledger.json": workspace / "data/evaluations/capital-bikeshare/operational-attempts.json",
            "payload/request-journal.jsonl": report_path.with_suffix(".requests.jsonl"),
        }
        originals = {}
        descriptors = {}
        for name, source in source_paths.items():
            _no_symlinks(source)
            if source.is_absolute() and not source.is_relative_to(workspace):
                raise ValueError("Evidence input path must be within the harness workspace")
            resolved = source.resolve(strict=True)
            if not resolved.is_relative_to(workspace) or source.is_symlink():
                raise ValueError("Evidence source is outside the harness workspace or is a symlink")
            raw = _read_regular(source)
            _assert_no_sensitive_or_external(_json(raw, name) if name.endswith(".json") else _journal_json_lines(raw))
            originals[name] = raw
            descriptors[name] = {"source_relative_path": resolved.relative_to(workspace).as_posix(),
                                 "sha256": _sha(raw), "bytes": len(raw)}
        plan = _json(originals["payload/plan.json"], "plan")
        report = _json(originals["payload/report.json"], "report")
        if plan["identity"]["candidate_id"] != candidate_id or report.get("operational_attempt_id") != attempt_id:
            raise ValueError("Harness return identity differs from selected plan/report")
        if report.get("operational_gate", {}).get("status") != "not_evaluated" or plan.get("deployment_permission") != "blocked":
            raise ValueError("Synthetic evidence must retain blocked/not-evaluated status")
        manifest = {"schema_version": 1, "synthetic_only": True, "candidate_id": candidate_id,
                    "attempt_id": attempt_id, "source_revision": {"commit": source_commit, "dirty": source_dirty},
                    "member_map": {name: MEMBERS[name] for name in MEMBERS}, "members": descriptors,
                    "verification_scope": "byte-preserving originals; archive-only process recomputes request counts, journal/report latency joins and nearest-rank p95",
                    "limitations": ["No real-data, database, clock-qualification or deployment approval claim."]}
        archive = output_dir / "synthetic-measurement-evidence.zip"
        with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_STORED) as bundle:
            bundle.writestr(MANIFEST, _measurement_evidence_json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False).encode() + b"\n")
            for name in MEMBERS:
                bundle.writestr(name, originals[name])
        for name, source in source_paths.items():
            if _sha(_read_regular(source)) != descriptors[name]["sha256"]:
                raise ValueError("An original evidence file changed after its initial read")
        archive_hash = _sha(_read_regular(archive, max_bytes=MAX_ARCHIVE_BYTES))
        env = {key: _measurement_evidence_os.environ[key] for key in ("PATH", "VIRTUAL_ENV") if key in _measurement_evidence_os.environ}
        env["MLFLOW_DISABLE_AGENT_HINT"] = "1"
        start_utc = time.time_ns()
        start_mono = time.monotonic_ns()
        checked = subprocess.run([sys.executable, str(_measurement_evidence_Path(__file__).resolve()), "--verify-archive", str(archive)],
                                 cwd=_measurement_evidence_Path(__file__).resolve().parents[2], env=env,
                                 capture_output=True, text=True, timeout=60, check=False)
        if checked.returncode != 0:
            raise RuntimeError(f"Archive-only verifier failed with exit {checked.returncode}")
        result = _json(checked.stdout.encode(), "archive-only verifier result")
        if result.get("archive_sha256") != archive_hash:
            raise ValueError("Separate verifier archive hash differs")
        (output_dir / "archive-sha256.json").write_text(_measurement_evidence_json.dumps({"archive": archive.name,
            "sha256": archive_hash, "bytes": archive.stat().st_size}, indent=2) + "\n", encoding="utf-8")
        (output_dir / "recalculation.json").write_text(_measurement_evidence_json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return {"archive": str(archive.relative_to(output_base.parent)), "archive_sha256": archive_hash,
                "recalculation": result, "verifier_utc_start_ns": start_utc,
                "verifier_monotonic_start_ns": start_mono, "verifier_exit_code": checked.returncode}
    except BaseException as failure:
        try:
            shutil.rmtree(output_dir)
        except BaseException as cleanup_failure:
            raise BaseExceptionGroup("Evidence export and partial-output cleanup both failed", [failure, cleanup_failure])
        raise


def _journal_json_lines(raw: bytes):
    rows = []
    for line in raw.splitlines():
        if line:
            rows.append(_json(line, "journal event"))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-archive", type=_measurement_evidence_Path, required=True,
                        help="Verify and recalculate from this archive only")
    args = parser.parse_args()
    print(_measurement_evidence_json.dumps(verify_archive_process(args.verify_archive), sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()
