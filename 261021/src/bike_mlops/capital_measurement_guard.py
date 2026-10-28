"""Hold a verified staging observation snapshot and check prediction provenance."""

from __future__ import annotations

import hashlib
import json
import math
import re
from contextlib import contextmanager
from datetime import timedelta

import pandas as pd

from bike_mlops.db import canonical_time, stable_hash
from bike_mlops.features import available_observation_frame, build_feature_row


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
def hold_observation_snapshot(database, frame: pd.DataFrame, data_hash: str, *, lock_timeout_ms: int = 5000):
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
        target = pd.Timestamp(target_hour)
        if pd.isna(target) or target.tzinfo is not None or target != target.floor("h"):
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
        observations = pd.DataFrame([
            {"timestamp": value["timestamp"], "cnt": value["cnt"],
             "available_at": pd.Timestamp(value["timestamp"]) + self.delay}
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
    return {"verified_rows": len(ids), "request_id_sha256": hashlib.sha256("\n".join(ids).encode()).hexdigest()}
