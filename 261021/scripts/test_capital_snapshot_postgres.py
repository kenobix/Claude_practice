"""Opt-in disposable PostgreSQL integration test; never connects to project databases."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import time
from unittest.mock import patch


def docker(*args, env=None) -> str:
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
    docker("image", "inspect", image, "--format", "{{.Id}}")  # Never pull automatically.
    suffix = secrets.token_hex(8)
    name = f"bike-snapshot-test-{suffix}"
    password = secrets.token_hex(24)
    environment = os.environ.copy()
    environment["POSTGRES_PASSWORD"] = password
    container_id = None
    try:
        container_id = docker(
            "run", "-d", "--rm", "--name", name,
            "--label", f"bike.snapshot.test={suffix}",
            "--publish", "127.0.0.1::5432", "-e", "POSTGRES_PASSWORD", image,
            env=environment,
        )
        if len(container_id) != 64 or any(char not in "0123456789abcdef" for char in container_id):
            raise RuntimeError("Docker did not return a valid container ID")
        published = docker("port", container_id, "5432/tcp")
        if not published.startswith("127.0.0.1:") or "\n" in published:
            raise RuntimeError("Test database was not bound to one loopback port")
        port = int(published.rsplit(":", 1)[1])
        os.environ["DATA_PROFILE_ID"] = "capital-bikeshare"
        os.environ["MLFLOW_MODEL_NAME"] = "bike-demand-capital"
        os.environ["FEATURE_CONTRACT_VERSION"] = "bike-demand-features-v1"
        os.environ["DATABASE_URL"] = f"postgresql://postgres:{password}@127.0.0.1:{port}/postgres"

        import pandas as pd
        import psycopg
        from fastapi.testclient import TestClient
        from bike_mlops import api
        from bike_mlops.capital_measurement_guard import (
            FeatureSourceExpectations, hold_observation_snapshot, verify_prediction_audit,
            verify_prediction_audit_batch,
        )
        from bike_mlops.db import Database
        from bike_mlops.features import available_observation_frame

        admin_url = os.environ["DATABASE_URL"]
        db = Database(admin_url)
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
        from psycopg import sql
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE ROLE snapshot_app LOGIN PASSWORD {}").format(sql.Literal(app_password)))
            connection.execute("CREATE DATABASE snapshot_test OWNER snapshot_app")
        os.environ["DATABASE_URL"] = f"postgresql://snapshot_app:{app_password}@127.0.0.1:{port}/snapshot_test"
        db = Database(os.environ["DATABASE_URL"])
        db.initialize()
        data_hash = "a" * 64
        frame = pd.DataFrame({
            "timestamp": pd.to_datetime(["2024-01-03 10:00", "2024-01-09 10:00", "2024-01-10 10:00"]),
            "cnt": [5, 12, 20],
        })
        db.seed_staging_observations(available_observation_frame(frame), data_hash)
        sources = FeatureSourceExpectations(frame, data_hash, label_delay_hours=3)

        class FixedModel:
            def predict(self, features):
                return [float(features.iloc[0]["lag_24"])]

        with patch.object(api, "_runtime_model", return_value=(FixedModel(), "7", "b" * 64, "passed", None)):
            with TestClient(api.app) as client:
                client.app.state.candidate_id = "c" * 64
                with hold_observation_snapshot(db, frame, data_hash, lock_timeout_ms=1000) as guard:
                    guard.assert_held()
                    try:
                        with db.transaction() as connection:
                            db.execute(connection, "SET LOCAL lock_timeout = '250ms'")
                            db.execute(connection, "UPDATE observations SET cnt=cnt+1 WHERE timestamp=?", ("2024-01-09 10:00:00",))
                    except psycopg.errors.LockNotAvailable:
                        pass
                    else:
                        raise AssertionError("Concurrent observation update was not blocked")
                    payload = {
                        "request_id": "snapshot-integration-001", "target_hour": "2024-01-10 10:00:00",
                        "as_of": "2024-01-10 10:00:00", "feature_contract_version": "bike-demand-features-v1",
                        "data_profile_id": "capital-bikeshare", "target_contract_id": "capital-trip-starts-hourly-v1",
                    }
                    response = client.post("/predict", json=payload)
                    if response.status_code != 200:
                        raise AssertionError(f"Prediction during observation lock returned HTTP {response.status_code}")
                    body = response.json()
                    sources.validate_response(payload["target_hour"], body)
                    if body["prediction"] != 12.0:
                        raise AssertionError("Prediction did not use the expected 24-hour source")
                    saved = db.saved_prediction(payload["request_id"])
                    if not saved or saved["data_version_sha256"] != body["data_version_sha256"]:
                        raise AssertionError("Prediction audit row was not saved while the lock was held")
                    pinned = {
                        "candidate_id": "c" * 64, "model_name": "bike-demand-capital", "model_version": "7",
                        "artifact_sha256": "b" * 64, "data_profile_id": "capital-bikeshare",
                        "target_contract_id": "capital-trip-starts-hourly-v1",
                        "feature_contract_version": "bike-demand-features-v1",
                    }
                    verify_prediction_audit(payload, body, saved, sources, **pinned)
                    batch = verify_prediction_audit_batch(
                        guard, [(payload, body)], sources, request_prefix="snapshot-integration-", **pinned
                    )
                    if batch["verified_rows"] != 1:
                        raise AssertionError("Unexpected verified audit row count")
                    missing_payload = {**payload, "request_id": "snapshot-integration-missing"}
                    try:
                        verify_prediction_audit_batch(
                            guard, [(payload, body), (missing_payload, {**body, "request_id": missing_payload["request_id"]})],
                            sources, request_prefix="snapshot-integration-", **pinned,
                        )
                    except ValueError as error:
                        if "exactly match" not in str(error):
                            raise
                    else:
                        raise AssertionError("Missing audit row was accepted")
                    try:
                        verify_prediction_audit_batch(
                            guard, [(payload, body), (payload, body)], sources,
                            request_prefix="snapshot-integration-", **pinned,
                        )
                    except ValueError:
                        pass
                    else:
                        raise AssertionError("Duplicate benchmark request IDs were accepted")
                    extra_payload = {**payload, "request_id": "snapshot-integration-002"}
                    extra_response = client.post("/predict", json=extra_payload)
                    if extra_response.status_code != 200:
                        raise AssertionError("Second synthetic prediction failed")
                    try:
                        verify_prediction_audit_batch(
                            guard, [(payload, body)], sources, request_prefix="snapshot-integration-", **pinned
                        )
                    except ValueError as error:
                        if "exactly match" not in str(error):
                            raise
                    else:
                        raise AssertionError("Unexpected audit row was accepted")
                    verify_prediction_audit_batch(
                        guard, [(payload, body), (extra_payload, extra_response.json())], sources,
                        request_prefix="snapshot-integration-", **pinned,
                    )
                    for changed in ({"predicted": 999}, {"input_hash": "0" * 64}, {"features_json": "{}"},
                                    {"candidate_id": "d" * 64}):
                        try:
                            verify_prediction_audit(payload, body, {**saved, **changed}, sources, **pinned)
                        except ValueError:
                            pass
                        else:
                            raise AssertionError("Changed prediction audit row was accepted")
                    guard.assert_held()
                with db.transaction() as connection:
                    db.execute(connection, "UPDATE observations SET cnt=cnt+1 WHERE timestamp=?", ("2024-01-09 10:00:00",))
                try:
                    with hold_observation_snapshot(db, frame, data_hash):
                        raise AssertionError("Changed observation was accepted")
                except ValueError as error:
                    if "differs" not in str(error):
                        raise
                with db.transaction() as connection:
                    db.execute(connection, "UPDATE observations SET cnt=cnt-1 WHERE timestamp=?", ("2024-01-09 10:00:00",))
                with db.transaction() as writer:
                    db.execute(writer, "UPDATE observations SET cnt=cnt WHERE timestamp=?", ("2024-01-09 10:00:00",))
                    try:
                        with hold_observation_snapshot(db, frame, data_hash, lock_timeout_ms=250):
                            raise AssertionError("Snapshot was acquired while another writer held the table")
                    except psycopg.errors.LockNotAvailable:
                        pass
                try:
                    sources.validate_response(payload["target_hour"], {"data_version_sha256": "f" * 64})
                except ValueError:
                    pass
                else:
                    raise AssertionError("A mismatched response source hash was accepted")
                try:
                    with hold_observation_snapshot(db, frame, data_hash) as guard:
                        pid = db.execute(guard.connection, "SELECT pg_backend_pid() AS pid").fetchone()["pid"]
                        with db.transaction() as connection:
                            db.execute(connection, "SELECT pg_terminate_backend(?)", (pid,))
                        guard.assert_held()
                except RuntimeError as error:
                    if "connection was lost" not in str(error):
                        raise
                else:
                    raise AssertionError("Lost snapshot connection was accepted")
        print(json.dumps({
            "status": "passed", "database": "disposable PostgreSQL, non-superuser app owner", "model": "synthetic mocked estimator",
            "checks": ["observation update blocked", "API prediction and audit write succeed under lock",
                       "source hash and audit match candidate", "missing/extra rows, duplicate IDs and changed audit rejected",
                       "changed data rejected", "wrong hash rejected", "lock acquisition times out",
                       "lost lock connection rejected", "lock released after context"],
        }, indent=2))
    finally:
        # Locate only the uniquely named and labeled container, even if docker
        # run was interrupted after creation but before returning its ID.
        remaining = docker("ps", "-a", "-q", "--no-trunc", "--filter", f"name=^/{name}$",
                           "--filter", f"label=bike.snapshot.test={suffix}").splitlines()
        if len(remaining) > 1 or (container_id and remaining and remaining[0] != container_id):
            raise RuntimeError("Test cleanup identity mismatch; no container was stopped")
        if remaining:
            docker("rm", "-f", remaining[0])
            print("Disposable test container stopped and removed")


if __name__ == "__main__":
    main()
