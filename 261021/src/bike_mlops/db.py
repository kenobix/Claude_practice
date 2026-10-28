from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from bike_mlops.config import DATA_PROFILE_ID, DEFAULT_DATABASE_URL, TARGET_CONTRACT_ID

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS observations (
        timestamp TEXT PRIMARY KEY,
        cnt INTEGER NOT NULL CHECK (cnt >= 0),
        available_at TEXT NOT NULL,
        data_hash TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS predictions (
        request_id TEXT PRIMARY KEY,
        input_hash TEXT NOT NULL,
        target_hour TEXT NOT NULL,
        as_of TEXT NOT NULL,
        predicted REAL NOT NULL CHECK (predicted >= 0),
        model_name TEXT NOT NULL,
        model_version TEXT NOT NULL,
        model_quality_gate TEXT NOT NULL,
        artifact_sha256 TEXT NOT NULL,
        data_version_sha256 TEXT NOT NULL,
        data_profile_id TEXT NOT NULL DEFAULT 'uci-bike-sharing',
        target_contract_id TEXT NOT NULL DEFAULT 'uci-bike-sharing-cnt-dataset-local-hour',
        feature_contract_version TEXT NOT NULL,
        features_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS deployments (
        deployment_id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        phase TEXT NOT NULL,
        data_profile_id TEXT NOT NULL,
        target_contract_id TEXT NOT NULL,
        from_slot TEXT,
        to_slot TEXT NOT NULL,
        from_version TEXT,
        to_version TEXT NOT NULL,
        approver TEXT NOT NULL,
        reason TEXT NOT NULL,
        error TEXT,
        probe_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS monitor_windows (
        window_id TEXT PRIMARY KEY,
        replay_id TEXT NOT NULL,
        window_end TEXT NOT NULL,
        prediction_count INTEGER NOT NULL,
        scored_count INTEGER NOT NULL,
        label_coverage REAL,
        mae REAL,
        rmse REAL,
        psi_json TEXT NOT NULL,
        missing_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS data_profile_metadata (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        data_profile_id TEXT NOT NULL,
        target_contract_id TEXT NOT NULL
    )""",
)


def canonical_time(value: datetime | str) -> str:
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if stamp.tzinfo is not None:
        raise ValueError("Database timestamps must use naive dataset-local time")
    return stamp.isoformat(sep=" ")


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Database:
    def __init__(
        self,
        url: str = DEFAULT_DATABASE_URL,
        data_profile_id: str = DATA_PROFILE_ID,
        target_contract_id: str = TARGET_CONTRACT_ID,
    ):
        self.url = url
        self.data_profile_id = data_profile_id
        self.target_contract_id = target_contract_id
        self.sqlite = url.startswith("sqlite:")
        if not self.sqlite and not url.startswith(("postgresql://", "postgres://")):
            raise ValueError("DATABASE_URL must use sqlite:/// or postgresql://")

    def connect(self):
        if self.sqlite:
            path = self.url.removeprefix("sqlite:///")
            file_path = Path(path)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(str(file_path), timeout=20, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            return connection
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self.url, row_factory=dict_row)

    def execute(self, connection, sql: str, params=()):
        if not self.sqlite:
            sql = sql.replace("?", "%s")
        return connection.execute(sql, params)

    def initialize(self) -> None:
        if self.sqlite:
            connection = self.connect()
            try:
                connection.execute("PRAGMA journal_mode=WAL")
            finally:
                connection.close()
        with self.transaction(immediate=self.sqlite) as connection:
            if not self.sqlite:
                self.execute(connection, "SELECT pg_advisory_xact_lock(726314092)")
            for statement in SCHEMA:
                self.execute(connection, statement)
            self._ensure_prediction_profile_columns(connection)
            self._ensure_deployment_profile_columns(connection)
            metadata = self.execute(
                connection,
                "SELECT data_profile_id,target_contract_id FROM data_profile_metadata WHERE singleton=1",
            ).fetchone()
            if metadata is None:
                populated = any(
                    int(self.execute(connection, f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])
                    for table in ("observations", "predictions", "deployments", "monitor_windows")
                )
                if populated and self.data_profile_id != "uci-bike-sharing":
                    raise ValueError(
                        "Refusing to assign a non-UCI profile to an unmarked database containing legacy rows; "
                        "use a new isolated database"
                    )
                self.execute(
                    connection,
                    "INSERT INTO data_profile_metadata(singleton,data_profile_id,target_contract_id) VALUES (1,?,?)",
                    (self.data_profile_id, self.target_contract_id),
                )
            elif (
                metadata["data_profile_id"] != self.data_profile_id
                or metadata["target_contract_id"] != self.target_contract_id
            ):
                raise ValueError(
                    "Database profile mismatch: database is bound to "
                    f"{metadata['data_profile_id']}/{metadata['target_contract_id']}, but this process uses "
                    f"{self.data_profile_id}/{self.target_contract_id}; use a separate database"
                )

    def _ensure_prediction_profile_columns(self, connection) -> None:
        if self.sqlite:
            columns = {
                row["name"] for row in self.execute(connection, "PRAGMA table_info(predictions)").fetchall()
            }
            additions = (
                ("data_profile_id", "TEXT NOT NULL DEFAULT 'uci-bike-sharing'"),
                ("target_contract_id", "TEXT NOT NULL DEFAULT 'uci-bike-sharing-cnt-dataset-local-hour'"),
            )
            for name, declaration in additions:
                if name not in columns:
                    self.execute(connection, f"ALTER TABLE predictions ADD COLUMN {name} {declaration}")
        else:
            self.execute(
                connection,
                "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS data_profile_id TEXT NOT NULL "
                "DEFAULT 'uci-bike-sharing'",
            )
            self.execute(
                connection,
                "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS target_contract_id TEXT NOT NULL "
                "DEFAULT 'uci-bike-sharing-cnt-dataset-local-hour'",
            )

    def _ensure_deployment_profile_columns(self, connection) -> None:
        if self.sqlite:
            columns = {
                row["name"] for row in self.execute(connection, "PRAGMA table_info(deployments)").fetchall()
            }
            additions = (
                ("data_profile_id", "TEXT NOT NULL DEFAULT 'uci-bike-sharing'"),
                ("target_contract_id", "TEXT NOT NULL DEFAULT 'uci-bike-sharing-cnt-dataset-local-hour'"),
            )
            for name, declaration in additions:
                if name not in columns:
                    self.execute(connection, f"ALTER TABLE deployments ADD COLUMN {name} {declaration}")
        else:
            self.execute(
                connection,
                "ALTER TABLE deployments ADD COLUMN IF NOT EXISTS data_profile_id TEXT NOT NULL "
                "DEFAULT 'uci-bike-sharing'",
            )
            self.execute(
                connection,
                "ALTER TABLE deployments ADD COLUMN IF NOT EXISTS target_contract_id TEXT NOT NULL "
                "DEFAULT 'uci-bike-sharing-cnt-dataset-local-hour'",
            )

    @contextmanager
    def session(self) -> Iterator[Any]:
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self, immediate: bool = False) -> Iterator[Any]:
        connection = self.connect()
        try:
            if self.sqlite:
                connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def add_observation(
        self, timestamp: datetime | str, cnt: int, available_at: datetime | str, data_hash: str
    ) -> bool:
        observed, available = canonical_time(timestamp), canonical_time(available_at)
        with self.transaction(immediate=self.sqlite) as connection:
            self.execute(
                connection,
                "INSERT INTO observations(timestamp,cnt,available_at,data_hash) VALUES (?,?,?,?) "
                "ON CONFLICT(timestamp) DO NOTHING",
                (observed, int(cnt), available, data_hash),
            )
            row = self.execute(
                connection,
                "SELECT cnt,available_at,data_hash FROM observations WHERE timestamp=?",
                (observed,),
            ).fetchone()
            if int(row["cnt"]) != int(cnt) or row["available_at"] != available:
                raise ValueError(f"Conflicting observation already stored for {observed}")
            if row["data_hash"] != data_hash:
                raise ValueError(f"Observation source version conflicts for {observed}; use a clean database")
            return row["data_hash"] == data_hash

    def add_observations(self, observations, data_hash: str) -> int:
        inserted = 0
        with self.transaction(immediate=self.sqlite) as connection:
            for row in observations.itertuples(index=False):
                observed = canonical_time(row.timestamp)
                available = canonical_time(row.available_at)
                result = self.execute(
                    connection,
                    "INSERT INTO observations(timestamp,cnt,available_at,data_hash) VALUES (?,?,?,?) "
                    "ON CONFLICT(timestamp) DO NOTHING",
                    (observed, int(row.cnt), available, data_hash),
                )
                inserted += max(result.rowcount, 0)
                saved = self.execute(
                    connection,
                    "SELECT cnt,available_at,data_hash FROM observations WHERE timestamp=?",
                    (observed,),
                ).fetchone()
                if int(saved["cnt"]) != int(row.cnt) or saved["available_at"] != available:
                    raise ValueError(f"Conflicting observation already stored for {observed}")
                if saved["data_hash"] != data_hash:
                    raise ValueError(f"Observation source version conflicts for {observed}; use a clean database")
        return inserted

    def observations_as_of(self, target_hour: datetime | str, as_of: datetime | str):
        import pandas as pd

        target = datetime.fromisoformat(canonical_time(target_hour))
        lag_24 = canonical_time(target - timedelta(hours=24))
        lag_168 = canonical_time(target - timedelta(hours=168))
        with self.session() as connection:
            rows = self.execute(
                connection,
                "SELECT timestamp,cnt,available_at,data_hash FROM observations "
                "WHERE timestamp IN (?,?) AND timestamp < ? AND available_at <= ? ORDER BY timestamp",
                (lag_24, lag_168, canonical_time(target_hour), canonical_time(as_of)),
            ).fetchall()
        return pd.DataFrame(
            [dict(row) for row in rows], columns=["timestamp", "cnt", "available_at", "data_hash"]
        )

    def saved_prediction(self, request_id: str):
        with self.session() as connection:
            row = self.execute(
                connection, "SELECT * FROM predictions WHERE request_id=?", (request_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_predictions(
        self,
        start: datetime | str,
        end_exclusive: datetime | str,
        model_version: str,
        request_prefix: str,
    ):
        with self.session() as connection:
            rows = self.execute(
                connection,
                "SELECT * FROM predictions WHERE target_hour >= ? AND target_hour < ? "
                "AND model_version=? AND request_id LIKE ? ORDER BY target_hour",
                (
                    canonical_time(start), canonical_time(end_exclusive), str(model_version),
                    f"{request_prefix}%",
                ),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_prediction(self, record: dict[str, Any]) -> dict:
        if record.get("data_profile_id", self.data_profile_id) != self.data_profile_id:
            raise ValueError("Prediction data profile differs from the database profile")
        if record.get("target_contract_id", self.target_contract_id) != self.target_contract_id:
            raise ValueError("Prediction target contract differs from the database target contract")
        with self.transaction(immediate=self.sqlite) as connection:
            self.execute(
                connection,
                "INSERT INTO predictions(request_id,input_hash,target_hour,as_of,predicted,model_name,"
                "model_version,model_quality_gate,artifact_sha256,data_version_sha256,data_profile_id,target_contract_id,"
                "feature_contract_version,features_json,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(request_id) DO NOTHING",
                (
                    record["request_id"], record["input_hash"], record["target_hour"],
                    record["as_of"], record["predicted"], record["model_name"],
                    str(record["model_version"]), record["model_quality_gate"], record["artifact_sha256"], record["data_version_sha256"],
                    record.get("data_profile_id", self.data_profile_id),
                    record.get("target_contract_id", self.target_contract_id),
                    record["feature_contract_version"], record["features_json"],
                    record["created_at"],
                ),
            )
            saved = self.execute(
                connection, "SELECT * FROM predictions WHERE request_id=?", (record["request_id"],)
            ).fetchone()
            saved = dict(saved)
            if saved["input_hash"] != record["input_hash"]:
                raise RequestConflict("request_id was already used with different input")
            return saved


class RequestConflict(ValueError):
    """An idempotency key was reused with a different canonical request."""
