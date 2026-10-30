"""Opt-in T06 storage probe; no project database or existing reports are used.

This checks assumptions for the inspector design, not a completed inspector.
Uses the existing local PostgreSQL image and removes its owned container.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import secrets
import sqlite3
import struct
import tempfile
import time
import uuid
from pathlib import Path

import pandas as pd

from bike_mlops.db import Database
from bike_mlops.monitor_persistence import persist_monitor_run
from test_monitor_persistence_postgres import docker


PROFILE = "capital-bikeshare"
TARGET = "capital-trip-starts-hourly-v1"


def windows():
    return pd.DataFrame([
        {"window_end": "2024-01-18 00:00:00", "prediction_count": 3, "scored_count": 0,
         "label_coverage": 0., "mae": None, "rmse": None, "psi": {"hour": None},
         "feature_missing_rates": {"lag_24": 1.}},
        {"window_end": "2024-01-18 01:00:00", "prediction_count": 3, "scored_count": 1,
         "label_coverage": 1 / 3, "mae": 72.53620455822316, "rmse": 111.50603198624435,
         "psi": {"hour": .123456789}, "feature_missing_rates": {"lag_24": 1 / 3}},
    ])


def normalized(value):
    if isinstance(value, float) and math.isnan(value):
        return "SQL_NaN"
    return value


def logical_rows(database):
    with database.session() as connection:
        return [dict(row) for row in database.execute(connection,
            "SELECT * FROM monitor_windows ORDER BY window_end").fetchall()]


def persist(database, root):
    database.initialize()  # Fixture setup only; the future inspector must never call this.
    persist_monitor_run(database, windows(), root / "monitor.csv",
        {"replay_id": "t06-storage-probe", "model_version": "7", "window_count": 2,
         "data_profile_id": PROFILE, "target_contract_id": TARGET}, run_id=str(uuid.uuid4()))


def sqlite_probe(root):
    path = root / "ops.db"
    database = Database(f"sqlite:///{path}", PROFILE, TARGET)
    persist(database, root)
    before = logical_rows(database)
    files_before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob("monitor.*")}
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        rows = [dict(row) for row in connection.execute("SELECT * FROM monitor_windows ORDER BY window_end")]
        assert rows == before and rows[0]["mae"] is None and rows[1]["label_coverage"] == 1 / 3
        try:
            connection.execute("UPDATE monitor_windows SET prediction_count=99")
        except sqlite3.OperationalError as error:
            assert error.sqlite_errorcode == sqlite3.SQLITE_READONLY
        else:
            raise AssertionError("SQLite read-only connection accepted an update")
        connection.rollback()
    missing = root / "absent" / "ops.db"
    try:
        sqlite3.connect(missing.as_uri() + "?mode=ro", uri=True)
    except sqlite3.OperationalError:
        pass
    else:
        raise AssertionError("Read-only connect unexpectedly created a missing database")
    assert not missing.parent.exists()
    assert logical_rows(database) == before
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob("monitor.*")} == files_before
    return {"numeric_storage": "binary64", "blank_mae_database_value": None,
            "coverage": rows[1]["label_coverage"], "write_rejected": True,
            "missing_database_not_created": True, "logical_rows_and_reports_unchanged": True}


def postgres_probe(root):
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row

    context = docker("context", "show")
    host = json.loads(docker("context", "inspect", context, "--format", "{{json .Endpoints.docker.Host}}"))
    if (not isinstance(host, str) or not host.startswith("unix://")
            or (os.environ.get("DOCKER_HOST") and not os.environ["DOCKER_HOST"].startswith("unix://"))):
        raise RuntimeError("The probe requires a local Unix-socket Docker daemon")
    image = "postgres:17.6-bookworm"
    image_id = docker("image", "inspect", image, "--format", "{{.Id}}")
    token = secrets.token_hex(8)
    label = "bike.monitor.reconciliation.probe"
    password = secrets.token_hex(24)
    env = os.environ.copy()
    env["POSTGRES_PASSWORD"] = password
    container_id = None
    try:
        container_id = docker("run", "-d", "--rm", "--pull=never",
            "--name", f"bike-monitor-reconcile-{token}", "--label", f"{label}={token}",
            "--publish", "127.0.0.1::5432", "-e", "POSTGRES_PASSWORD", image, env=env)
        if len(container_id) != 64 or any(c not in "0123456789abcdef" for c in container_id):
            raise RuntimeError("Docker returned an invalid container ID")
        port_binding = docker("port", container_id, "5432/tcp")
        if not port_binding.startswith("127.0.0.1:") or "\n" in port_binding:
            raise RuntimeError("The probe port must bind only to loopback")
        port = int(port_binding.rsplit(":", 1)[1])
        admin_url = f"postgresql://postgres:{password}@127.0.0.1:{port}/postgres"
        for attempt in range(60):
            try:
                with psycopg.connect(admin_url, connect_timeout=2) as connection:
                    connection.execute("SELECT 1")
                break
            except psycopg.OperationalError:
                if attempt == 59:
                    raise RuntimeError("Disposable PostgreSQL did not become ready") from None
                time.sleep(.25)
        app_password = secrets.token_hex(24)
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE ROLE t06_app LOGIN PASSWORD {}").format(sql.Literal(app_password)))
            connection.execute("CREATE DATABASE t06_probe OWNER t06_app")
        url = f"postgresql://t06_app:{app_password}@127.0.0.1:{port}/t06_probe"
        database = Database(url, PROFILE, TARGET)
        persist(database, root)
        before = logical_rows(database)
        with psycopg.connect(url, autocommit=True, row_factory=dict_row) as connection:
            connection.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            connection.execute("SET LOCAL statement_timeout='5000ms'")
            types = connection.execute("SELECT column_name,data_type FROM information_schema.columns "
                "WHERE table_schema=current_schema() AND table_name='monitor_windows' "
                "AND column_name IN ('mae','rmse','label_coverage') ORDER BY column_name").fetchall()
            rows = connection.execute("SELECT * FROM monitor_windows ORDER BY window_end").fetchall()
            assert all(row["data_type"] == "real" for row in types) and len(types) == 3
            assert math.isnan(rows[0]["mae"]) and math.isnan(rows[0]["rmse"])
            for name in ("label_coverage", "mae", "rmse"):
                source = windows().iloc[1][name]
                assert struct.pack("!f", source) == struct.pack("!f", rows[1][name])
                assert source != rows[1][name]
            assert json.loads(rows[1]["psi_json"]) == {"hour": .123456789}
            try:
                connection.execute("UPDATE monitor_windows SET prediction_count=99")
            except psycopg.errors.ReadOnlySqlTransaction:
                pass
            else:
                raise AssertionError("PostgreSQL read-only transaction accepted an update")
            connection.execute("ROLLBACK")
        after = logical_rows(database)
        # NaN is unequal to itself; normalize only for this fixture's non-mutation comparison.
        clean = lambda data: [{k: normalized(v) for k, v in row.items()} for row in data]
        assert clean(before) == clean(after)
        result = {"image_id": image_id, "column_types": types, "numeric_storage": "binary32",
                  "blank_mae_database_value": normalized(rows[0]["mae"]),
                  "source_coverage": 1 / 3, "database_coverage": rows[1]["label_coverage"],
                  "binary32_quantization_matches": True, "json_numbers_preserve_binary64": True,
                  "write_rejected": True, "logical_rows_unchanged": True}
    finally:
        if container_id:
            labels = json.loads(docker("inspect", container_id, "--format", "{{json .Config.Labels}}"))
            if labels.get(label) != token:
                raise RuntimeError("Refusing to stop a container without the probe ownership label")
            docker("stop", container_id)
            if docker("ps", "-a", "--filter", f"label={label}={token}", "--format", "{{.ID}}"):
                raise RuntimeError("Probe container remains after cleanup")
    result["cleanup_complete"] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-disposable", action="store_true", help="Run synthetic SQLite and owned PostgreSQL probes")
    args = parser.parse_args()
    if not args.run_disposable:
        parser.error("--run-disposable is required; no probe was started")
    with tempfile.TemporaryDirectory(prefix="monitor-reconciliation-storage-") as directory:
        root = Path(directory)
        (root / "sqlite").mkdir()
        (root / "postgres").mkdir()
        sqlite_result = sqlite_probe(root / "sqlite")
        pg_result = postgres_probe(root / "postgres")
        print(json.dumps({"status": "passed", "scope": "T06 storage assumptions only, synthetic data",
                          "sqlite": sqlite_result, "postgres": pg_result}, indent=2, allow_nan=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # DSNs and randomly generated credentials must not appear in diagnostic output.
        raise SystemExit(f"Storage probe failed ({type(error).__name__}); owned-resource cleanup was attempted") from None
