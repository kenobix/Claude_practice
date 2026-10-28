"""Opt-in monitor persistence integration test on disposable PostgreSQL.

Uses an existing local image only, a random loopback port and a unique label.
No project DB, user credentials, prior reports or remote daemon are used.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import tempfile
import time
import uuid
from pathlib import Path


def docker(*args: str, env=None) -> str:
    result = subprocess.run(["docker", *args], env=env, capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError(f"Docker operation failed: {args[0]} (exit {result.returncode})")
    return result.stdout.strip()


def main() -> None:
    context = docker("context", "show")
    daemon_host = json.loads(docker("context", "inspect", context, "--format", "{{json .Endpoints.docker.Host}}"))
    explicit_host = os.environ.get("DOCKER_HOST")
    if (not isinstance(daemon_host, str) or not daemon_host.startswith("unix://")
            or (explicit_host and not explicit_host.startswith("unix://"))):
        raise RuntimeError("Disposable test requires a local Unix-socket Docker daemon")
    image = "postgres:17.6-bookworm"
    docker("image", "inspect", image, "--format", "{{.Id}}")
    suffix = secrets.token_hex(8)
    name = f"bike-monitor-test-{suffix}"
    password = secrets.token_hex(24)
    environment = os.environ.copy()
    environment["POSTGRES_PASSWORD"] = password
    container_id = None
    try:
        container_id = docker("run", "-d", "--rm", "--pull=never", "--name", name,
                              "--label", f"bike.monitor.test={suffix}",
                              "--publish", "127.0.0.1::5432", "-e", "POSTGRES_PASSWORD", image,
                              env=environment)
        if len(container_id) != 64 or any(char not in "0123456789abcdef" for char in container_id):
            raise RuntimeError("Docker did not return a valid container ID")
        published = docker("port", container_id, "5432/tcp")
        if not published.startswith("127.0.0.1:") or "\n" in published:
            raise RuntimeError("Test database was not bound to one loopback port")
        port = int(published.rsplit(":", 1)[1])
        os.environ["DATA_PROFILE_ID"] = "uci-bike-sharing"
        os.environ["DATABASE_URL"] = f"postgresql://postgres:{password}@127.0.0.1:{port}/postgres"
        admin_url = os.environ["DATABASE_URL"]

        import pandas as pd
        import psycopg
        from psycopg import sql
        from bike_mlops.db import Database
        from bike_mlops.monitor_persistence import MonitorPersistenceError, persist_monitor_run

        for attempt in range(60):
            try:
                with psycopg.connect(admin_url) as connection:
                    connection.execute("SELECT 1")
                break
            except psycopg.OperationalError:
                if attempt == 59:
                    raise RuntimeError("Disposable PostgreSQL did not become ready")
                time.sleep(0.5)
        app_password = secrets.token_hex(24)
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE ROLE monitor_test_app LOGIN PASSWORD {} ").format(sql.Literal(app_password)))
            connection.execute("CREATE DATABASE monitor_test OWNER monitor_test_app")
        database = Database(f"postgresql://monitor_test_app:{app_password}@127.0.0.1:{port}/monitor_test")
        database.initialize()
        windows = pd.DataFrame([
            {"window_end": f"2012-10-01 {hour:02d}:00:00", "prediction_count": 2, "scored_count": 1,
             "label_coverage": 0.5, "mae": 5.0, "rmse": 5.0, "psi": {"hour": 0.1},
             "feature_missing_rates": {"lag_24": 0.0}}
            for hour in (0, 1)
        ])
        report = {"replay_id": "synthetic-replay", "model_version": "3", "window_count": 2}
        old_id = "synthetic-replay:v3:2012-10-01 00:00:00"
        first, second, failing = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        collision_id = f"monitor:{failing}:2012-10-01 01:00:00"
        with database.transaction() as connection:
            for old in (old_id, collision_id):
                database.execute(connection, "INSERT INTO monitor_windows VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (old, "synthetic-replay", "2012-10-01 01:00:00", 99, 3, 0.1, 1.0, 2.0, "{}", "{}", "old"))

        def rows():
            with database.session() as connection:
                return {row["window_id"]: dict(row) for row in database.execute(
                    connection, "SELECT * FROM monitor_windows ORDER BY window_id").fetchall()}

        before = rows()
        with tempfile.TemporaryDirectory(prefix="monitor-persistence-pg-") as directory:
            root = Path(directory)
            result_a = persist_monitor_run(database, windows, root / "first.csv", report, run_id=first)
            result_b = persist_monitor_run(database, windows, root / "second.csv", report, run_id=second)
            after = rows()
            if len(after) != 6 or after[old_id] != before[old_id] or after[collision_id] != before[collision_id]:
                raise AssertionError("Prior monitor records were altered")
            for run_id, result in ((first, result_a), (second, result_b)):
                if len([key for key in after if key.startswith(f"monitor:{run_id}:")]) != 2:
                    raise AssertionError("A successful monitor run has an unexpected row count")
                if not result["completion_marker_path"].is_file():
                    raise AssertionError("Successful monitor run is missing the completion marker")
            try:
                persist_monitor_run(database, windows, root / "failed.csv", report, run_id=failing)
            except MonitorPersistenceError as error:
                if "during database" not in str(error):
                    raise
            else:
                raise AssertionError("Duplicate window ID was accepted")
            if rows() != after:
                raise AssertionError("Failed monitor transaction changed prior rows or left a partial insert")
            if not (root / "failed.csv").is_file() or not (root / "failed.json").is_file() or (root / "failed.complete.json").exists():
                raise AssertionError("Failed monitor run did not leave unconfirmed evidence")
            failed_report = json.loads((root / "failed.json").read_text())
            if failed_report.get("monitor_run_id") != failing or "unconfirmed" not in failed_report.get("persistence_status", ""):
                raise AssertionError("Failed report does not identify its unconfirmed run")
        print(json.dumps({"status": "passed", "database": "disposable PostgreSQL, non-superuser app owner",
                          "checks": ["legacy row unchanged", "two independent monitor runs",
                                     "second-row unique conflict rolls back first insert",
                                     "failed report has no completion marker"]}, indent=2))
    finally:
        remaining = docker("ps", "-a", "-q", "--no-trunc", "--filter", f"name=^/{name}$",
                           "--filter", f"label=bike.monitor.test={suffix}").splitlines()
        if len(remaining) > 1 or (container_id and remaining and remaining[0] != container_id):
            raise RuntimeError("Test cleanup identity mismatch; no container was stopped")
        if remaining:
            docker("rm", "-f", remaining[0])
            print("Disposable test container stopped and removed")


if __name__ == "__main__":
    main()
