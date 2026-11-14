"""Explicit command tools: storage_checks."""
from __future__ import annotations


# ========================================================================
# test_capital_snapshot_postgres
# ========================================================================

"""Opt-in disposable PostgreSQL integration test; never connects to project databases."""



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
        from bike_mlops.operational_validation import FeatureSourceExpectations, hold_observation_snapshot, verify_prediction_audit, verify_prediction_audit_batch
        from bike_mlops.db import Database
        from bike_mlops.data import available_observation_frame

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


def _entry_test_capital_snapshot_postgres():
    main()


# ========================================================================
# test_capital_request_interruption_postgres
# ========================================================================

"""Opt-in real HTTP timeout/late-commit and connection-loss checks in disposable PostgreSQL.

Only an already-cached image is used. No project service, port, volume or registry
is changed. HTTP fixture records a synthetic prediction; it is not a trained API.
"""


import json as _capital_request_interruption_postgres_json
import os as _capital_request_interruption_postgres_os
import secrets as _capital_request_interruption_postgres_secrets
import sys
import threading
import time as _capital_request_interruption_postgres_time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch as _capital_request_interruption_postgres_patch

_capital_request_interruption_postgres_docker = docker


def check(database) -> dict:
    import httpx
    import pandas as pd
    from bike_mlops import operational_validation as recovery, operational_runner as pending
    from bike_mlops.operational_validation import CapitalBenchmarkObserver
    from bike_mlops.operational_validation import hold_observation_snapshot
    from bike_mlops.operational import RequestJournal
    from bike_mlops.data import available_observation_frame
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    from test_operational import OperationalLedgerTests
    fixture = OperationalLedgerTests(methodName="runTest")
    fixture.setUp()
    received, release, committed = threading.Event(), threading.Event(), threading.Event()
    handler_errors = []
    journal = None
    identity = fixture.plan["identity"]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            try:
                payload = _capital_request_interruption_postgres_json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                # Transaction is open, but not committed until the client has timed out and queried.
                with database.transaction() as connection:
                    database.execute(connection,
                        "INSERT INTO predictions(request_id,input_hash,target_hour,as_of,predicted,model_name,"
                        "model_version,candidate_id,model_quality_gate,artifact_sha256,data_version_sha256,"
                        "data_profile_id,target_contract_id,feature_contract_version,features_json,created_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (payload["request_id"], "a"*64, payload["target_hour"], payload["as_of"], 1.0,
                         identity["model_name"], identity["model_version"], fixture.candidate, "passed",
                         identity["registered_artifact_sha256"], "b"*64, identity["data_profile_id"],
                         identity["target_contract_id"], identity["feature_contract_version"], "{}", "synthetic"))
                    received.set()
                    if not release.wait(15): raise RuntimeError("Commit barrier timed out")
                committed.set()
                self.send_response(200)
                self.end_headers()
                try: self.wfile.write(b'{}')
                except (BrokenPipeError, ConnectionResetError): pass
            except Exception as error:
                handler_errors.append(type(error).__name__)
                received.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with fixture.attempt() as attempt:
            journal = RequestJournal(fixture.report_dir / f"operational-{attempt.attempt_id}.requests.jsonl", attempt, fixture.plan)
            observer = CapitalBenchmarkObserver(attempt, Mock(unsafe=True), Mock(), pinned_identity={}, journal=journal)
            payload = journal.schedule[0]
            observer.before_request(payload)
            started = _capital_request_interruption_postgres_time.perf_counter()
            try:
                with httpx.Client(timeout=0.25, trust_env=False) as client:
                    client.post(f"http://127.0.0.1:{server.server_port}/predict", json=payload)
            except httpx.ReadTimeout:
                observer.request_result(payload, http_status=None, latency_ms=(_capital_request_interruption_postgres_time.perf_counter()-started)*1000,
                                        error="ReadTimeout")
            else:
                raise AssertionError("Real HTTP request did not time out")
            if not received.wait(5) or handler_errors: raise AssertionError("HTTP fixture did not begin transaction")
            # No failure report is fabricated. The lease exits interrupted; inspection uses journal alone.
        args = dict(candidate_id=fixture.candidate, attempt_id=attempt.attempt_id, database=database,
                    ledger_path=fixture.ledger_path, lock_dir=fixture.lock_dir)
        with _capital_request_interruption_postgres_patch.object(recovery, "PROJECT_ROOT", fixture.root), _capital_request_interruption_postgres_patch.object(pending, '_pending_audit_validate_staging_database'):
            before = pending.inspect_pending_audit(**args)
            if before["audit_observation"] != "not_seen_at_query_time" or not before["later_commit_possible"]:
                raise AssertionError("Uncommitted request was not classified as uncertain")
            evidence_before = (journal.path.read_bytes(), fixture.ledger_path.read_bytes())
            release.set()
            if not committed.wait(5) or handler_errors: raise AssertionError("Timed-out request did not commit later")
            after = pending.inspect_pending_audit(**args)
            if after["audit_observation"] != "identity_matches_unverified":
                raise AssertionError("Delayed commit identity was not observed")
            if evidence_before != (journal.path.read_bytes(), fixture.ledger_path.read_bytes()):
                raise AssertionError("Read-only observation modified journal or ledger")
            # Read failure is an error, not an empty result or permission to retry.
            with database.session() as broken:
                pid = database.execute(broken, "SELECT pg_backend_pid() AS pid").fetchone()["pid"]
                with database.transaction() as killer:
                    database.execute(killer, "SELECT pg_terminate_backend(?)", (pid,))
                from contextlib import nullcontext
                with _capital_request_interruption_postgres_patch.object(database, "session", return_value=nullcontext(broken)):
                    try: pending.inspect_pending_audit(**args)
                    except RuntimeError: pass
                    else: raise AssertionError("Lost audit connection was treated as successful observation")
        # Verify the real snapshot guard fails before a subsequent request can acquire send intent.
        journal.close()
        fixture.review(reason="Synthetic interruption investigated; separate connection-loss attempt")
        frame = pd.DataFrame({"timestamp": pd.to_datetime(["2024-01-01 00:00:00"]), "cnt": [1]})
        database.seed_staging_observations(available_observation_frame(frame), "e"*64)
        with fixture.attempt() as second:
            journal = RequestJournal(fixture.report_dir / f"operational-{second.attempt_id}.requests.jsonl", second, fixture.plan)
            try:
                with hold_observation_snapshot(database, frame, "e"*64) as guard:
                    observer = CapitalBenchmarkObserver(second, guard, Mock(), pinned_identity={}, journal=journal)
                    pid = database.execute(guard.connection, "SELECT pg_backend_pid() AS pid").fetchone()["pid"]
                    with database.transaction() as killer:
                        database.execute(killer, "SELECT pg_terminate_backend(?)", (pid,))
                    observer.before_request(journal.schedule[0])
            except RuntimeError:
                pass
            else:
                raise AssertionError("Lost snapshot connection permitted the next request")
            if journal.sequence != 1: raise AssertionError("Connection loss wrote a send intent")
        return {"status": "passed", "checks": ["real HTTP ReadTimeout", "uncommitted row remains uncertain",
            "late PostgreSQL commit observed without resending", "journal and ledger unchanged by inspection",
            "terminated audit connection fails closed", "terminated snapshot connection prevents next send"],
            "prediction_requests": 1, "deployment_permission": "blocked"}
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if journal is not None: journal.close()
        fixture.doCleanups()


def _capital_request_interruption_postgres_main():
    context = _capital_request_interruption_postgres_docker("context", "show")
    host = _capital_request_interruption_postgres_json.loads(_capital_request_interruption_postgres_docker("context", "inspect", context, "--format", "{{json .Endpoints.docker.Host}}"))
    if not host.startswith("unix://") or not _capital_request_interruption_postgres_os.environ.get("DOCKER_HOST", "unix://").startswith("unix://"):
        raise RuntimeError("Test requires a local Unix-socket daemon")
    image = "postgres:17.6-bookworm"
    _capital_request_interruption_postgres_docker("image", "inspect", image, "--format", "{{.Id}}")
    suffix, password = _capital_request_interruption_postgres_secrets.token_hex(8), _capital_request_interruption_postgres_secrets.token_hex(24)
    name = f"bike-request-interruption-{suffix}"
    environment = _capital_request_interruption_postgres_os.environ.copy()
    environment["POSTGRES_PASSWORD"] = password
    container_id = None
    try:
        container_id = _capital_request_interruption_postgres_docker("run", "-d", "--rm", "--name", name, "--label", f"bike.request.test={suffix}",
            "--publish", "127.0.0.1::5432", "-e", "POSTGRES_PASSWORD", image, env=environment)
        if len(container_id) != 64 or any(c not in "0123456789abcdef" for c in container_id):
            raise RuntimeError("Unexpected container identity")
        published = _capital_request_interruption_postgres_docker("port", container_id, "5432/tcp")
        if not published.startswith("127.0.0.1:") or "\n" in published: raise RuntimeError("Nonlocal test port")
        from bike_mlops.db import Database
        import psycopg
        from psycopg import sql
        port = int(published.rsplit(":", 1)[1])
        admin_url = f"postgresql://postgres:{password}@127.0.0.1:{port}/postgres"
        for index in range(60):
            try:
                with psycopg.connect(admin_url): pass
                break
            except psycopg.OperationalError:
                if index == 59: raise RuntimeError("Disposable PostgreSQL did not become ready") from None
                _capital_request_interruption_postgres_time.sleep(0.5)
        app_password = _capital_request_interruption_postgres_secrets.token_hex(24)
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE ROLE request_test LOGIN PASSWORD {}").format(sql.Literal(app_password)))
            connection.execute("CREATE DATABASE request_test OWNER request_test")
        database = Database(f"postgresql://request_test:{app_password}@127.0.0.1:{port}/request_test",
                            "capital-bikeshare", "capital-trip-starts-hourly-v1")
        database.initialize()
        print(_capital_request_interruption_postgres_json.dumps(check(database), indent=2))
    finally:
        remaining = _capital_request_interruption_postgres_docker("ps", "-a", "-q", "--no-trunc", "--filter", f"name=^/{name}$",
                           "--filter", f"label=bike.request.test={suffix}").splitlines()
        if len(remaining) > 1 or (container_id and remaining and remaining[0] != container_id):
            raise RuntimeError("Cleanup identity mismatch; no unrelated container stopped")
        if remaining:
            _capital_request_interruption_postgres_docker("rm", "-f", remaining[0])
        print("cleanup_complete: disposable request interruption container removed")


def _entry_test_capital_request_interruption_postgres():
    _capital_request_interruption_postgres_main()


# ========================================================================
# test_monitor_persistence_postgres
# ========================================================================

"""Opt-in monitor persistence integration test on disposable PostgreSQL.

Uses an existing local image only, a random loopback port and a unique label.
No project DB, user credentials, prior reports or remote daemon are used.
"""


import json as _monitor_persistence_postgres_json
import os as _monitor_persistence_postgres_os
import secrets as _monitor_persistence_postgres_secrets
import subprocess as _monitor_persistence_postgres_subprocess
import tempfile as _monitor_persistence_postgres_tempfile
import time as _monitor_persistence_postgres_time
import uuid as _monitor_persistence_postgres_uuid
import sys as _monitor_persistence_postgres_sys
from pathlib import Path as _monitor_persistence_postgres_Path
from unittest.mock import patch as _monitor_persistence_postgres_patch


def _monitor_persistence_postgres_docker(*args: str, env=None) -> str:
    result = _monitor_persistence_postgres_subprocess.run(["docker", *args], env=env, capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError(f"Docker operation failed: {args[0]} (exit {result.returncode})")
    return result.stdout.strip()


def _monitor_persistence_postgres_main() -> None:
    context = _monitor_persistence_postgres_docker("context", "show")
    daemon_host = _monitor_persistence_postgres_json.loads(_monitor_persistence_postgres_docker("context", "inspect", context, "--format", "{{json .Endpoints.docker.Host}}"))
    explicit_host = _monitor_persistence_postgres_os.environ.get("DOCKER_HOST")
    if (not isinstance(daemon_host, str) or not daemon_host.startswith("unix://")
            or (explicit_host and not explicit_host.startswith("unix://"))):
        raise RuntimeError("Disposable test requires a local Unix-socket Docker daemon")
    image = "postgres:17.6-bookworm"
    _monitor_persistence_postgres_docker("image", "inspect", image, "--format", "{{.Id}}")
    suffix = _monitor_persistence_postgres_secrets.token_hex(8)
    name = f"bike-monitor-test-{suffix}"
    password = _monitor_persistence_postgres_secrets.token_hex(24)
    environment = _monitor_persistence_postgres_os.environ.copy()
    environment["POSTGRES_PASSWORD"] = password
    container_id = None
    try:
        container_id = _monitor_persistence_postgres_docker("run", "-d", "--rm", "--pull=never", "--name", name,
                              "--label", f"bike.monitor.test={suffix}",
                              "--publish", "127.0.0.1::5432", "-e", "POSTGRES_PASSWORD", image,
                              env=environment)
        if len(container_id) != 64 or any(char not in "0123456789abcdef" for char in container_id):
            raise RuntimeError("Docker did not return a valid container ID")
        published = _monitor_persistence_postgres_docker("port", container_id, "5432/tcp")
        if not published.startswith("127.0.0.1:") or "\n" in published:
            raise RuntimeError("Test database was not bound to one loopback port")
        port = int(published.rsplit(":", 1)[1])
        _monitor_persistence_postgres_os.environ["DATA_PROFILE_ID"] = "uci-bike-sharing"
        _monitor_persistence_postgres_os.environ["DATABASE_URL"] = f"postgresql://postgres:{password}@127.0.0.1:{port}/postgres"
        admin_url = _monitor_persistence_postgres_os.environ["DATABASE_URL"]

        import pandas as pd
        import psycopg
        from psycopg import sql
        from bike_mlops.db import Database
        from bike_mlops.monitoring import MonitorPersistenceError, persist_monitor_run

        for attempt in range(60):
            try:
                with psycopg.connect(admin_url) as connection:
                    connection.execute("SELECT 1")
                break
            except psycopg.OperationalError:
                if attempt == 59:
                    raise RuntimeError("Disposable PostgreSQL did not become ready")
                _monitor_persistence_postgres_time.sleep(0.5)
        app_password = _monitor_persistence_postgres_secrets.token_hex(24)
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
        first, second, failing = str(_monitor_persistence_postgres_uuid.uuid4()), str(_monitor_persistence_postgres_uuid.uuid4()), str(_monitor_persistence_postgres_uuid.uuid4())
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
        with _monitor_persistence_postgres_tempfile.TemporaryDirectory(prefix="monitor-persistence-pg-") as directory:
            root = _monitor_persistence_postgres_Path(directory)
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
            failed_report = _monitor_persistence_postgres_json.loads((root / "failed.json").read_text())
            if failed_report.get("monitor_run_id") != failing or "unconfirmed" not in failed_report.get("persistence_status", ""):
                raise AssertionError("Failed report does not identify its unconfirmed run")
        # A second disposable database tests a pending request before and after
        # a delayed commit. Its random port is allowed only in this test.
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute("CREATE DATABASE pending_test OWNER monitor_test_app")
        pending_db = Database(f"postgresql://monitor_test_app:{app_password}@127.0.0.1:{port}/pending_test",
                              "capital-bikeshare", "capital-trip-starts-hourly-v1")
        pending_db.initialize()
        _monitor_persistence_postgres_sys.path.insert(0, str(_monitor_persistence_postgres_Path(__file__).resolve().parents[1] / "tests"))
        import test_operational as ledger_tests
        from bike_mlops import operational_validation as recovery
        from bike_mlops import operational_runner as capital_pending_audit
        fixture = ledger_tests.OperationalLedgerTests(methodName="runTest")
        fixture.setUp()
        try:
            with fixture.attempt() as lease:
                lease.mark_requests_started()
                from bike_mlops.operational import RequestJournal
                journal = RequestJournal(fixture.report_dir / f"operational-{lease.attempt_id}.requests.jsonl", lease, fixture.plan)
                try:
                    journal.sending(journal.schedule[0])
                    journal.outcome(journal.schedule[0], http_status=None, latency_ms=10.0, error="synthetic_timeout")
                finally:
                    journal.close()
                pending_id = f"capital-op:{lease.attempt_id}:warmup:0000"
                report_path = fixture.report_dir / f"operational-{lease.attempt_id}-failure.json"
                failure = {"operational_attempt_id": lease.attempt_id, "operational_plan_sha256": lease.plan_sha256,
                           "operational_plan_file_sha256": lease.plan_file_sha256, "completed": False,
                           "operational_gate": {"status": "failed"},
                           "partial_evidence": {"pending_request_id": pending_id,
                             "pending_request_status": "outcome_unknown_may_have_been_sent"}}
                report_path.write_text(_monitor_persistence_postgres_json.dumps(failure))
                lease.finish(status="failed", report_path=report_path, failure_code="request_failed")
            identity = fixture.plan["identity"]
            with _monitor_persistence_postgres_patch.object(recovery, "PROJECT_ROOT", fixture.root), \
                 _monitor_persistence_postgres_patch.object(capital_pending_audit, '_pending_audit_validate_staging_database'):
                def inspect():
                    return capital_pending_audit.inspect_pending_audit(fixture.candidate, lease.attempt_id, pending_db,
                        ledger_path=fixture.ledger_path, lock_dir=fixture.lock_dir)
                if inspect()["audit_observation"] != "not_seen_at_query_time":
                    raise AssertionError("Pending request was unexpectedly visible before commit")
                with pending_db.transaction() as connection:
                    pending_db.execute(connection,
                        "INSERT INTO predictions(request_id,input_hash,target_hour,as_of,predicted,model_name,"
                        "model_version,candidate_id,model_quality_gate,artifact_sha256,data_version_sha256,"
                        "data_profile_id,target_contract_id,feature_contract_version,features_json,created_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (pending_id, "a" * 64, "2024-01-08 00:00:00", "2024-01-08 00:00:00", 1.0,
                         identity["model_name"], identity["model_version"], fixture.candidate, "passed",
                         identity["registered_artifact_sha256"], "b" * 64, identity["data_profile_id"],
                         identity["target_contract_id"], identity["feature_contract_version"], "{}", "synthetic"))
                observation = inspect()
                if observation["audit_observation"] != "identity_matches_unverified" or observation["later_commit_possible"]:
                    raise AssertionError("Pending request identity was not observed after commit")
        finally:
            fixture.doCleanups()
        print(_monitor_persistence_postgres_json.dumps({"status": "passed", "database": "disposable PostgreSQL, non-superuser app owner",
                          "checks": ["legacy row unchanged", "two independent monitor runs",
                                     "second-row unique conflict rolls back first insert",
                                     "failed report has no completion marker", "pending request before and after delayed commit"]}, indent=2))
    finally:
        remaining = _monitor_persistence_postgres_docker("ps", "-a", "-q", "--no-trunc", "--filter", f"name=^/{name}$",
                           "--filter", f"label=bike.monitor.test={suffix}").splitlines()
        if len(remaining) > 1 or (container_id and remaining and remaining[0] != container_id):
            raise RuntimeError("Test cleanup identity mismatch; no container was stopped")
        if remaining:
            _monitor_persistence_postgres_docker("rm", "-f", remaining[0])
            print("Disposable test container stopped and removed")


def _entry_test_monitor_persistence_postgres():
    _monitor_persistence_postgres_main()


# ========================================================================
# test_monitor_inspection_postgres
# ========================================================================

"""Opt-in T06b acceptance using synthetic reports and one owned local PostgreSQL.

No project database, existing reports, or registry are accessed. Fixture setup
alone uses DDL/writes; every inspector call is checked for logical non-mutation.
"""


import argparse
import hashlib
import json as _monitor_inspection_postgres_json
import math
import os as _monitor_inspection_postgres_os
import secrets as _monitor_inspection_postgres_secrets
import selectors
import signal
import struct
import subprocess as _monitor_inspection_postgres_subprocess
import sys as _monitor_inspection_postgres_sys
import tempfile
import time as _monitor_inspection_postgres_time
import unittest
import uuid
from pathlib import Path as _monitor_inspection_postgres_Path
from unittest.mock import patch as _monitor_inspection_postgres_patch

import pandas as pd
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from bike_mlops import monitor_inspection as inspection
from bike_mlops.db import Database
from bike_mlops.monitoring import persist_monitor_run, MonitorPersistenceError
_monitor_inspection_postgres_docker = _monitor_persistence_postgres_docker

PROFILE, TARGET = 'capital-bikeshare', 'capital-trip-starts-hourly-v1'
CONNECT = psycopg.connect


class ObservedConnection:
    def __init__(self, connection, statements, hook=None):
        self.connection, self.statements, self.hook = connection, statements, hook

    def execute(self, query, params=None):
        text = query if isinstance(query, str) else query.as_string(self.connection)
        self.statements.append((text, params))
        cursor = self.connection.execute(query, params)
        if self.hook:
            self.hook(self.connection, text, params)
        return cursor

    def rollback(self):
        self.statements.append(('ROLLBACK', None))
        self.connection.rollback()

    def close(self):
        self.connection.close()


def normalized(value):
    if isinstance(value, float) and math.isnan(value):
        return 'SQL_NaN'
    if isinstance(value, dict):
        return {key: normalized(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalized(item) for item in value]
    return value


class PostgresInspectionAcceptance(unittest.TestCase):
    url = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='t06b-report-')
        self.addCleanup(self.tmp.cleanup)
        self.root = _monitor_inspection_postgres_Path(self.tmp.name)
        self.csv = self.root / 'monitor.csv'
        self.run_id = str(uuid.uuid4())
        # Only this owned database is reset between cases. Never use project env.
        with CONNECT(self.url, autocommit=True) as connection:
            connection.execute('DROP SCHEMA public CASCADE')
            connection.execute('CREATE SCHEMA public')
        self.database = Database(self.url, PROFILE, TARGET)
        self.database.initialize()
        self.windows = pd.DataFrame([
            {'window_end': '2024-01-18 00:00:00', 'window_start': '2024-01-17 23:00:00',
             'prediction_count': 3, 'scored_count': 0, 'label_coverage': 0.0,
             'mae': None, 'rmse': None, 'quality_status': 'waiting_for_labels',
             'psi': {'hour': 1 / 3}, 'feature_missing_rates': {'lag': 1 / 3},
             'distribution_status': 'no_threshold_crossing'},
            {'window_end': '2024-01-18 01:00:00', 'window_start': '2024-01-18 00:00:00',
             'prediction_count': 3, 'scored_count': 1, 'label_coverage': 1 / 3,
             'mae': 72.53620455822316, 'rmse': 111.50603198624435,
             'quality_status': 'investigate_error_increase', 'psi': {'hour': .123456789},
             'feature_missing_rates': {'lag': 1 / 3}, 'distribution_status': 'investigate_distribution_change'},
        ])
        self.report = {'replay_id': 't06b-synthetic', 'model_name': 'bike-demand', 'model_version': '7',
                       'data_profile_id': PROFILE, 'target_contract_id': TARGET, 'data_sha256': 'a' * 64,
                       'window_count': 2, 'thresholds': {'window_hours': 1},
                       'monitor_period': {'start_inclusive': '2024-01-18 00:00:00',
                                          'end_exclusive': '2024-01-18 02:00:00'},
                       'latest_window': self.windows.iloc[-1].to_dict()}
        persist_monitor_run(self.database, self.windows, self.csv, self.report, run_id=self.run_id)
        self.marker = self.csv.with_suffix('.complete.json')
        self.statements = []

    def query(self, statement, params=None):
        with CONNECT(self.url, autocommit=True, row_factory=dict_row) as connection:
            cursor = connection.execute(statement, params)
            return cursor.fetchall() if cursor.description else None

    def logical_snapshot(self):
        with CONNECT(self.url, row_factory=dict_row) as connection:
            tables = connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()
            return {row['tablename']: normalized(connection.execute(sql.SQL('SELECT * FROM public.{} ORDER BY 1').format(
                sql.Identifier(row['tablename']))).fetchall()) for row in tables}

    def files_snapshot(self):
        return {p.name: (p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns,
                        hashlib.sha256(p.read_bytes()).hexdigest()) for p in self.root.iterdir() if p.is_file()}

    def inspect(self, *, hook=None, preserve=True, url=None):
        self.statements = []
        before, files = self.logical_snapshot(), self.files_snapshot()
        def connect(*args, **kwargs):
            return ObservedConnection(CONNECT(*args, **kwargs), self.statements, hook)
        with _monitor_inspection_postgres_patch('psycopg.connect', side_effect=connect), \
             _monitor_inspection_postgres_patch.object(Database, 'initialize', side_effect=AssertionError('initializer forbidden')), \
             _monitor_inspection_postgres_patch.object(Database, 'connect', side_effect=AssertionError('writer connector forbidden')):
            result = inspection.inspect_monitor_run(self.csv, self.run_id, database_url=url or self.url,
                                                   data_profile_id=PROFILE, target_contract_id=TARGET)
        if preserve:
            self.assertEqual(before, self.logical_snapshot())
            self.assertEqual(files, self.files_snapshot())
        for statement, params in self.statements:
            self.assertTrue(statement.startswith(('SELECT', 'BEGIN TRANSACTION', 'ROLLBACK')))
            if statement.startswith('SELECT window_id'):
                self.assertEqual(params, (f'monitor:{self.run_id}:%', inspection.MAX_ROWS + 1))
        payload = _monitor_inspection_postgres_json.dumps(result, allow_nan=False)
        self.assertNotIn(self.url, payload)
        self.assertNotIn(self.url.split('@')[0].split(':')[-1], payload)
        return result

    def assert_status(self, result, status, reason=None):
        self.assertEqual(result['status'], status)
        self.assertEqual(result['exit_code'], 0 if status == 'matched' else 1 if status in ('mismatch', 'database_profile_mismatch') else 2)
        if reason:
            self.assertEqual(result['reason'], reason)

    def test_s01_s18_s19_real_nan_and_exact_json_match(self):
        result = self.inspect()
        self.assert_status(result, 'matched')
        self.assertEqual(result['database']['numeric_storage'], dict.fromkeys(inspection.METRICS, 'binary32'))
        self.assertEqual(result['database']['missing_representations'], {'sql_null': 0, 'sql_nan_missing': 2})
        self.assertEqual(result['database']['snapshot'], 'postgresql_repeatable_read_only')
        sample = result['database']['binary32_examples']['mae']
        self.assertNotEqual(sample['source_binary64'], sample['expected_binary32'])

    def test_s18_each_real_one_ulp_is_mismatch(self):
        for key in inspection.METRICS:
            with self.subTest(key=key):
                value = float(self.windows.iloc[-1][key])
                bits = struct.unpack('!I', struct.pack('!f', value))[0]
                next_value = struct.unpack('!f', struct.pack('!I', bits + 1))[0]
                self.query(sql.SQL("UPDATE monitor_windows SET {}=%s WHERE window_end='2024-01-18 01:00:00'").format(sql.Identifier(key)), (next_value,))
                self.assert_status(self.inspect(), 'mismatch')
                self.query(sql.SQL("UPDATE monitor_windows SET {}=%s WHERE window_end='2024-01-18 01:00:00'").format(sql.Identifier(key)), (value,))

    def test_s18_json_does_not_use_real_tolerance(self):
        self.query("UPDATE monitor_windows SET psi_json=%s WHERE window_end='2024-01-18 01:00:00'",
                   (_monitor_inspection_postgres_json.dumps({'hour': math.nextafter(.123456789, 1)}),))
        self.assert_status(self.inspect(), 'mismatch')

    def test_database_json_bool_and_malformed_json_are_mismatch(self):
        for value in ('{"hour":true}', '{broken', '{"hour":NaN}', '{"hour":1,"hour":2}'):
            self.query('UPDATE monitor_windows SET psi_json=%s', (value,))
            self.assert_status(self.inspect(), 'mismatch')

    def test_double_precision_and_unknown_numeric_schema(self):
        for key in inspection.METRICS:
            self.query(sql.SQL('ALTER TABLE monitor_windows ALTER COLUMN {} TYPE DOUBLE PRECISION').format(sql.Identifier(key)))
            self.query(sql.SQL("UPDATE monitor_windows SET {}=%s WHERE window_end='2024-01-18 01:00:00'").format(sql.Identifier(key)), (float(self.windows.iloc[-1][key]),))
        result = self.inspect()
        self.assert_status(result, 'matched')
        self.assertEqual(result['database']['numeric_storage'], dict.fromkeys(inspection.METRICS, 'binary64'))
        self.query("UPDATE monitor_windows SET mae=%s WHERE window_end='2024-01-18 01:00:00'", (math.nextafter(72.53620455822316, 100),))
        self.assert_status(self.inspect(), 'mismatch')
        self.query('ALTER TABLE monitor_windows ALTER COLUMN mae TYPE TEXT')
        self.assert_status(self.inspect(), 'unsupported_input', 'monitor_numeric_type_unsupported')

    def test_s17_s19_null_and_nan_only_match_missing(self):
        self.query("UPDATE monitor_windows SET mae=NULL,rmse=NULL WHERE window_end='2024-01-18 00:00:00'")
        self.assert_status(self.inspect(), 'matched')
        for value in (None, float('nan'), float('inf')):
            self.query("UPDATE monitor_windows SET mae=%s WHERE window_end='2024-01-18 01:00:00'", (value,))
            self.assert_status(self.inspect(), 'mismatch')
        self.query("UPDATE monitor_windows SET mae=0 WHERE window_end='2024-01-18 00:00:00'")
        self.assert_status(self.inspect(), 'mismatch')

    def test_s19_latest_null_can_match_sql_nan(self):
        csv = self.root / 'latest-null.csv'
        self.windows.loc[1, ['mae', 'rmse']] = float('nan')
        self.report['latest_window'] = {k: None if isinstance(v, float) and math.isnan(v) else v
                                        for k, v in self.windows.iloc[-1].to_dict().items()}
        self.run_id = str(uuid.uuid4())
        persist_monitor_run(self.database, self.windows, csv, self.report, run_id=self.run_id)
        self.csv = csv
        self.assert_status(self.inspect(), 'matched')

    def test_s02_s03_s05_s06_unconfirmed_observations(self):
        self.marker.unlink()
        self.assert_status(self.inspect(), 'unconfirmed', 'database_without_marker')
        self.query('DELETE FROM monitor_windows')
        result = self.inspect()
        self.assert_status(result, 'unconfirmed', 'files_only')
        self.assertTrue(result['later_commit_possible'])
        for path in self.root.iterdir():
            path.unlink()
        self.assert_status(self.inspect(), 'unconfirmed', 'not_observed')
        self.query("INSERT INTO monitor_windows VALUES (%s,'old','2024-01-18 00:00:00',0,0,0,NULL,NULL,'{}','{}','fixture')",
                   (f'monitor:{self.run_id}:2024-01-18 00:00:00',))
        self.assert_status(self.inspect(), 'unconfirmed', 'database_only')

    def test_s09_partial_extra_and_changed_rows(self):
        self.query("UPDATE monitor_windows SET replay_id='wrong' WHERE window_end='2024-01-18 01:00:00'")
        self.assert_status(self.inspect(), 'mismatch')
        self.query("UPDATE monitor_windows SET replay_id='t06b-synthetic'")
        self.query("INSERT INTO monitor_windows VALUES (%s,'extra','2024-01-18 02:00:00',0,0,0,NULL,NULL,'{}','{}','fixture')",
                   (f'monitor:{self.run_id}:2024-01-18 02:00:00',))
        self.assert_status(self.inspect(), 'mismatch')
        self.query("DELETE FROM monitor_windows WHERE window_end='2024-01-18 02:00:00'")
        self.query("DELETE FROM monitor_windows WHERE window_end='2024-01-18 00:00:00'")
        self.assert_status(self.inspect(), 'mismatch')
        self.marker.unlink()
        self.assert_status(self.inspect(), 'mismatch')

    def test_s10_profile_refused_before_target_select(self):
        self.query("UPDATE data_profile_metadata SET target_contract_id='other'")
        self.assert_status(self.inspect(), 'database_profile_mismatch')
        self.assertFalse(any(q.startswith('SELECT window_id') for q, _ in self.statements))

    def test_s12_missing_metadata_and_unsupported_monitor_schema(self):
        self.query('ALTER TABLE monitor_windows DROP COLUMN psi_json')
        self.assert_status(self.inspect(), 'unsupported_input', 'monitor_schema_unsupported')
        self.query('DROP TABLE data_profile_metadata')
        self.assert_status(self.inspect(), 'legacy_unverifiable')
        self.assertFalse(any(q.startswith('SELECT window_id') for q, _ in self.statements))

    def test_s13_actual_table_permission_denial(self):
        # A SELECT-only, non-owner role is created in the disposable fixture.
        role = 'inspect_' + _monitor_inspection_postgres_secrets.token_hex(4)
        with CONNECT(self.url, autocommit=True) as connection:
            connection.execute(sql.SQL('CREATE ROLE {}').format(sql.Identifier(role)))
            connection.execute(sql.SQL('GRANT {} TO t06_app').format(sql.Identifier(role)))
            connection.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)))
            connection.execute(sql.SQL('GRANT SELECT ON data_profile_metadata TO {}').format(sql.Identifier(role)))
        def as_role(connection, statement, params):
            if statement.startswith('BEGIN'):
                connection.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(role)))
        self.assert_status(self.inspect(hook=as_role), 'database_unavailable')

    def test_s13_actual_lock_timeout(self):
        before, files = self.logical_snapshot(), self.files_snapshot()
        with CONNECT(self.url) as writer:
            def lock_after_snapshot(connection, statement, params):
                if statement.startswith('SELECT current_schema'):
                    writer.execute('LOCK TABLE monitor_windows IN ACCESS EXCLUSIVE MODE')
            started = _monitor_inspection_postgres_time.monotonic()
            result = self.inspect(hook=lock_after_snapshot, preserve=False)
            elapsed = _monitor_inspection_postgres_time.monotonic() - started
            self.assert_status(result, 'database_unavailable')
            self.assertIn('LockNotAvailable', result['reason'])
            self.assertLess(elapsed, 8)
        self.assertEqual(before, self.logical_snapshot())
        self.assertEqual(files, self.files_snapshot())

    def test_s13_actual_statement_timeout(self):
        def slow_query(connection, statement, params):
            if statement.startswith('SELECT current_schema'):
                connection.execute('SELECT pg_sleep(10)')
        started = _monitor_inspection_postgres_time.monotonic()
        result = self.inspect(hook=slow_query)
        self.assert_status(result, 'database_unavailable')
        self.assertIn('QueryCanceled', result['reason'])
        self.assertLess(_monitor_inspection_postgres_time.monotonic() - started, 12)

    def test_s13_actual_connect_failure_and_secret_redaction(self):
        bad_url = self.url.replace('/t06b', '/missing_' + _monitor_inspection_postgres_secrets.token_hex(4))
        self.assert_status(self.inspect(url=bad_url), 'database_unavailable')

    def test_actual_connection_loss_during_snapshot_is_unavailable(self):
        def terminate(connection, statement, params):
            if statement.startswith('SELECT current_schema'):
                self.query('SELECT pg_terminate_backend(%s)', (connection.info.backend_pid,))
        self.assert_status(self.inspect(hook=terminate), 'database_unavailable')

    def test_s16_other_and_legacy_rows_are_preserved(self):
        for key in ('legacy:v1:old', f'monitor:{uuid.uuid4()}:2024-01-18 01:00:00'):
            self.query("INSERT INTO monitor_windows VALUES (%s,'old','2024-01-18 01:00:00',99,3,0.1,1,2,'{}','{}','old')", (key,))
        self.assert_status(self.inspect(), 'matched')

    def test_s20_update_rejected_on_inspector_connection(self):
        rejected = []
        def attempt_update(connection, statement, params):
            if statement.startswith('SELECT current_schema'):
                settings = connection.execute("SELECT current_setting('transaction_isolation') AS i, "
                    "current_setting('transaction_read_only') AS r, current_setting('statement_timeout') AS s, "
                    "current_setting('lock_timeout') AS l").fetchone()
                self.assertEqual(settings, {'i': 'repeatable read', 'r': 'on', 's': '5s', 'l': '1s'})
                try:
                    connection.execute('UPDATE monitor_windows SET prediction_count=99')
                except psycopg.errors.ReadOnlySqlTransaction:
                    rejected.append(True)
                    raise
                self.fail('UPDATE was accepted')
        self.assert_status(self.inspect(hook=attempt_update), 'database_unavailable')
        self.assertEqual(rejected, [True])

    def test_s21_late_commit_not_visible_until_next_inspection(self):
        self.marker.unlink()
        rows = self.query('SELECT * FROM monitor_windows ORDER BY window_id')
        self.query('DELETE FROM monitor_windows')
        writer = CONNECT(self.url)
        self.addCleanup(writer.close)
        for row in rows:
            writer.execute('INSERT INTO monitor_windows VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', tuple(row.values()))
        self.assert_status(self.inspect(), 'unconfirmed', 'files_only')
        committed = []
        def delayed_commit(connection, statement, params):
            # Schema SELECT is the first snapshot query. Commit after it, before
            # metadata/row SELECT, so read committed would incorrectly see rows.
            if statement.startswith('SELECT current_schema'):
                writer.commit()
                committed.append(True)
        first = self.inspect(hook=delayed_commit, preserve=False)
        self.assert_status(first, 'unconfirmed', 'files_only')
        self.assertEqual(committed, [True])
        second = self.inspect()
        self.assert_status(second, 'unconfirmed', 'database_without_marker')
        self.assertTrue(first['later_commit_possible'] and second['later_commit_possible'])
        self.assertEqual(normalized(rows), self.query_normalized())

    def test_profile_and_rows_share_the_same_snapshot(self):
        def change_metadata(connection, statement, params):
            if statement.startswith('SELECT current_schema'):
                self.query("UPDATE data_profile_metadata SET data_profile_id='other'")
        files, rows = self.files_snapshot(), self.query_normalized()
        self.assert_status(self.inspect(hook=change_metadata, preserve=False), 'matched')
        self.assertEqual(files, self.files_snapshot())
        self.assertEqual(rows, self.query_normalized())
        self.assert_status(self.inspect(), 'database_profile_mismatch')

    def query_normalized(self):
        return normalized(self.query('SELECT * FROM monitor_windows ORDER BY window_id'))

    def test_s14_marker_appears_during_database_read(self):
        marker_bytes = self.marker.read_bytes()
        self.marker.unlink()
        before = self.logical_snapshot()
        def appearance(connection, statement, params):
            if statement.startswith('SELECT current_schema'):
                self.marker.write_bytes(marker_bytes)
        self.assert_status(self.inspect(hook=appearance, preserve=False), 'changed_during_inspection')
        self.assertEqual(before, self.logical_snapshot())

    def test_row_limit_is_unsupported(self):
        with _monitor_inspection_postgres_patch.object(inspection, 'MAX_ROWS', 1):
            # File bound must remain >=2 to reach the DB bound.
            from bike_mlops.monitor_inspection import _postgres_rows, _base, InspectionFailure
            out = _base(self.run_id, self.csv, PROFILE, TARGET)
            with self.assertRaises(InspectionFailure) as raised:
                _postgres_rows(self.url, self.run_id, PROFILE, TARGET, out)
            self.assertEqual(raised.exception.reason, 'database_row_limit_exceeded')

    def test_cli_postgres_json_and_exit_codes(self):
        environment = _monitor_inspection_postgres_os.environ.copy()
        environment.update(DATABASE_URL=self.url, DATA_PROFILE_ID=PROFILE, MLFLOW_DISABLE_AGENT_HINT='1')
        for status, exit_code in (('matched', 0), ('mismatch', 1), ('unconfirmed', 2)):
            before, files = self.logical_snapshot(), self.files_snapshot()
            process = _monitor_inspection_postgres_subprocess.run([_monitor_inspection_postgres_sys.executable, '-m', 'bike_mlops.cli', 'monitor-inspect',
                                      '--csv', str(self.csv), '--run-id', self.run_id],
                                     env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(process.returncode, exit_code)
            self.assertEqual(_monitor_inspection_postgres_json.loads(process.stdout)['status'], status)
            self.assertEqual(before, self.logical_snapshot())
            self.assertEqual(files, self.files_snapshot())
            self.assertNotIn(self.url, process.stdout + process.stderr)
            if status == 'matched':
                self.query("UPDATE monitor_windows SET prediction_count=99 WHERE window_end='2024-01-18 01:00:00'")
            elif status == 'mismatch':
                self.query("UPDATE monitor_windows SET prediction_count=3")
                self.marker.unlink()

    def test_writer_failure_before_commit_and_after_commit_without_marker(self):
        for phase in ('rollback', 'marker'):
            run_id, csv = str(uuid.uuid4()), self.root / f'{phase}.csv'
            before = self.logical_snapshot()
            if phase == 'rollback':
                original_execute = self.database.execute
                def fail_second(connection, statement, params=()):
                    if statement.startswith('INSERT INTO monitor_windows') and params[2].endswith('01:00:00'):
                        raise RuntimeError('synthetic interruption before commit')
                    return original_execute(connection, statement, params)
                with _monitor_inspection_postgres_patch.object(self.database, 'execute', side_effect=fail_second), self.assertRaises(MonitorPersistenceError):
                    persist_monitor_run(self.database, self.windows, csv, self.report, run_id=run_id)
                self.assertEqual(before, self.logical_snapshot())
            else:
                from bike_mlops.monitoring import _write_new_file
                def fail_marker(path, body):
                    if path.name.endswith('.complete.json'):
                        raise OSError('synthetic interruption after commit')
                    _write_new_file(path, body)
                with _monitor_inspection_postgres_patch('bike_mlops.monitoring._write_new_file', side_effect=fail_marker), self.assertRaises(MonitorPersistenceError):
                    persist_monitor_run(self.database, self.windows, csv, self.report, run_id=run_id)
            self.csv, self.run_id = csv, run_id
            self.assert_status(self.inspect(), 'unconfirmed', 'files_only' if phase == 'rollback' else 'database_without_marker')

    def test_actual_sigkill_writer_before_and_after_database_commit(self):
        child_code = "\nimport json, os, sys, time\nfrom pathlib import Path\nfrom unittest.mock import patch\nimport pandas as pd\nfrom bike_mlops.db import Database\nfrom bike_mlops.monitoring import persist_monitor_run, _write_new_file\npayload = json.load(sys.stdin)\ndatabase = Database(os.environ['T06B_FIXTURE_URL'], payload['profile'], payload['target'])\ndef pause():\n    print('READY', flush=True)\n    time.sleep(60)\noriginal = database.execute\ndef execute(connection, statement, params=()):\n    result = original(connection, statement, params)\n    if statement.startswith('INSERT INTO monitor_windows'):\n        pause()\n    return result\ndef write(path, body):\n    if path.name.endswith('.complete.json'):\n        pause()\n    _write_new_file(path, body)\nif payload['phase'] == 'before':\n    database.execute = execute\nwith patch('bike_mlops.monitoring._write_new_file', side_effect=write):\n    persist_monitor_run(database, pd.DataFrame(payload['windows']), Path(payload['csv']),\n                        payload['report'], run_id=payload['run_id'])\n"
        for phase in ('before', 'after'):
            csv, run_id = self.root / f'kill-{phase}.csv', str(uuid.uuid4())
            before = self.logical_snapshot()
            payload = {'profile': PROFILE, 'target': TARGET, 'phase': phase, 'csv': str(csv),
                       'run_id': run_id, 'report': self.report,
                       'windows': [{k: None if isinstance(v, float) and math.isnan(v) else v
                                    for k, v in row.items()} for row in self.windows.to_dict('records')]}
            environment = _monitor_inspection_postgres_os.environ.copy()
            environment['T06B_FIXTURE_URL'] = self.url
            process = _monitor_inspection_postgres_subprocess.Popen([_monitor_inspection_postgres_sys.executable, '-c', child_code], env=environment,
                                       stdin=_monitor_inspection_postgres_subprocess.PIPE, stdout=_monitor_inspection_postgres_subprocess.PIPE, stderr=_monitor_inspection_postgres_subprocess.PIPE,
                                       text=True)
            try:
                process.stdin.write(_monitor_inspection_postgres_json.dumps(payload, allow_nan=False))
                process.stdin.close()
                process.stdin = None
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    self.assertTrue(selector.select(timeout=15), 'Writer did not reach interruption point')
                self.assertEqual(process.stdout.readline().strip(), 'READY')
                process.kill()
                process.communicate(timeout=10)
                self.assertEqual(process.returncode, -9)
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=10)
            self.csv, self.run_id = csv, run_id
            self.assertFalse(csv.with_suffix('.complete.json').exists())
            self.assert_status(self.inspect(), 'unconfirmed', 'files_only' if phase == 'before' else 'database_without_marker')
            after = self.logical_snapshot()
            if phase == 'before':
                self.assertEqual(before, after)
            else:
                self.assertEqual(len(after['monitor_windows']), len(before['monitor_windows']) + 2)
                self.assertEqual(before['monitor_windows'], [row for row in after['monitor_windows']
                    if not row['window_id'].startswith(f'monitor:{run_id}:')])


def _monitor_inspection_postgres_main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-disposable', action='store_true', required=True)
    parser.parse_args()
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    context = _monitor_inspection_postgres_docker('context', 'show')
    host = _monitor_inspection_postgres_json.loads(_monitor_inspection_postgres_docker('context', 'inspect', context, '--format', '{{json .Endpoints.docker.Host}}'))
    if not host.startswith('unix://') or (_monitor_inspection_postgres_os.environ.get('DOCKER_HOST') and not _monitor_inspection_postgres_os.environ['DOCKER_HOST'].startswith('unix://')):
        raise RuntimeError('Local Unix-socket Docker required')
    image = 'postgres:17.6-bookworm'
    image_id = _monitor_inspection_postgres_docker('image', 'inspect', image, '--format', '{{.Id}}')
    token, password = _monitor_inspection_postgres_secrets.token_hex(8), _monitor_inspection_postgres_secrets.token_hex(24)
    label = 'bike.monitor.inspection.test'
    container_id = None
    result = None
    try:
        environment = _monitor_inspection_postgres_os.environ.copy()
        environment['POSTGRES_PASSWORD'] = password
        container_id = _monitor_inspection_postgres_docker('run', '-d', '--rm', '--pull=never', '--name', f'bike-t06b-{token}',
                              '--label', f'{label}={token}', '--publish', '127.0.0.1::5432',
                              '-e', 'POSTGRES_PASSWORD', image, env=environment)
        if len(container_id) != 64 or any(c not in '0123456789abcdef' for c in container_id):
            raise RuntimeError('Invalid container ID')
        binding = _monitor_inspection_postgres_docker('port', container_id, '5432/tcp')
        if not binding.startswith('127.0.0.1:') or '\n' in binding:
            raise RuntimeError('Loopback binding required')
        port = int(binding.rsplit(':', 1)[1])
        admin = f'postgresql://postgres:{password}@127.0.0.1:{port}/postgres'
        for attempt in range(60):
            try:
                with CONNECT(admin, connect_timeout=2) as connection:
                    connection.execute('SELECT 1')
                break
            except psycopg.OperationalError:
                if attempt == 59:
                    raise RuntimeError('Owned PostgreSQL startup failed') from None
                _monitor_inspection_postgres_time.sleep(.25)
        app_password = _monitor_inspection_postgres_secrets.token_hex(24)
        with CONNECT(admin, autocommit=True) as connection:
            connection.execute(sql.SQL('CREATE ROLE t06_app LOGIN CREATEROLE PASSWORD {}').format(sql.Literal(app_password)))
            connection.execute('CREATE DATABASE t06b OWNER t06_app')
        PostgresInspectionAcceptance.url = f'postgresql://t06_app:{app_password}@127.0.0.1:{port}/t06b'
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PostgresInspectionAcceptance))
    finally:
        if container_id:
            labels = _monitor_inspection_postgres_json.loads(_monitor_inspection_postgres_docker('inspect', container_id, '--format', '{{json .Config.Labels}}'))
            if labels.get(label) != token:
                raise RuntimeError('Cleanup ownership mismatch')
            _monitor_inspection_postgres_docker('rm', '-f', container_id)
            if _monitor_inspection_postgres_docker('ps', '-a', '--filter', f'label={label}={token}', '--format', '{{.ID}}'):
                raise RuntimeError('Owned container remains')
            print('T06b owned container removed; cleanup_complete=true', flush=True)
    print(_monitor_inspection_postgres_json.dumps({'task': 'T06b', 'scope': 'synthetic_only', 'status': 'passed' if result.wasSuccessful() else 'failed',
                      'tests_run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
                      'image_id': image_id, 'cleanup_complete': True, 'real_capital_evidence': False,
                      'deployment_permission': 'not_evaluated'}, allow_nan=False))
    return 0 if result.wasSuccessful() else 1


def _entry_test_monitor_inspection_postgres():
    try:
        raise SystemExit(_monitor_inspection_postgres_main())
    except KeyboardInterrupt:
        raise SystemExit('T06b acceptance interrupted; owned-resource cleanup attempted') from None
    except Exception as error:
        raise SystemExit(f'T06b acceptance failed ({type(error).__name__}); cleanup attempted') from None


# ========================================================================
# test_prediction_migration_postgres
# ========================================================================

"""T08 opt-in legacy REAL migration and restore, using two owned PostgreSQLs."""

import argparse as _prediction_migration_postgres_argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib as _prediction_migration_postgres_hashlib
import json as _prediction_migration_postgres_json
import os as _prediction_migration_postgres_os
from pathlib import Path as _prediction_migration_postgres_Path
import secrets as _prediction_migration_postgres_secrets
import selectors as _prediction_migration_postgres_selectors
import signal as _prediction_migration_postgres_signal
import struct as _prediction_migration_postgres_struct
import subprocess as _prediction_migration_postgres_subprocess
import sys as _prediction_migration_postgres_sys
import time as _prediction_migration_postgres_time
import unittest as _prediction_migration_postgres_unittest
from unittest.mock import patch as _prediction_migration_postgres_patch
import psycopg as _prediction_migration_postgres_psycopg
from psycopg import sql as _prediction_migration_postgres_sql
# Standalone fixture configuration must not inherit the user's active profile.
_prediction_migration_postgres_os.environ.update(DATA_PROFILE_ID='uci-bike-sharing', FEATURE_CONTRACT_VERSION='bike-demand-features-v1', MLFLOW_MODEL_NAME='bike-demand')
from fastapi import FastAPI
from fastapi.testclient import TestClient
from bike_mlops import api
from bike_mlops.db import Database as _prediction_migration_postgres_Database, SCHEMA, RequestConflict, stable_hash
_prediction_migration_postgres_docker = _monitor_persistence_postgres_docker
_prediction_migration_postgres_PROFILE, _prediction_migration_postgres_TARGET = ('uci-bike-sharing', 'uci-bike-sharing-cnt-dataset-local-hour')
VALUES = (1 / 3, 415.72473, 15.189926280821982, 0.0)

def binary32(value):
    return _prediction_migration_postgres_struct.unpack('!f', _prediction_migration_postgres_struct.pack('!f', value))[0]

def record(index=0):
    stamp = f'2012-07-01 {10 + index:02d}:00:00'
    return dict(request_id=f'legacy-request-{index:03d}', input_hash=stable_hash(dict(target_hour=stamp, as_of=stamp, feature_contract_version='bike-demand-features-v1')), target_hour=stamp, as_of=stamp, predicted=VALUES[index], model_name='bike-demand', model_version='7', candidate_id=None, model_quality_gate='passed', artifact_sha256='a' * 64, data_version_sha256='b' * 64, data_profile_id=_prediction_migration_postgres_PROFILE, target_contract_id=_prediction_migration_postgres_TARGET, feature_contract_version='bike-demand-features-v1', features_json='{}', created_at='2012-07-01T00:00:00+00:00')

def snapshot(database):
    with database.session() as connection:
        tables = connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()
        result = {}
        for table in tables:
            name = table['tablename']
            rows = [dict(row) for row in connection.execute(_prediction_migration_postgres_sql.SQL('SELECT * FROM {} ORDER BY 1').format(_prediction_migration_postgres_sql.Identifier(name)))]
            if name == 'predictions':
                # REAL text output is not an exact binary64 representation: cast in SQL.
                exact = {r['request_id']: r['exact'] for r in connection.execute('SELECT request_id,predicted::double precision AS exact FROM predictions')}
                for row in rows:
                    row['predicted'] = exact[row['request_id']]
            result[name] = rows
        return result

def columns(database):
    with database.session() as connection:
        return connection.execute("SELECT table_name,column_name,data_type,is_nullable,column_default FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position").fetchall()

def predicted_type(database):
    return next((r['data_type'] for r in columns(database) if r['table_name'] == 'predictions' and r['column_name'] == 'predicted'))

def retry_api(database, index):
    # Use the production router without lifespan, which would migrate before the test.
    app = FastAPI()
    app.include_router(api.api_router)
    app.state.database, app.state.model, app.state.model_version = (database, None, '99')
    app.state.model_error = 'Synthetic migration fixture has no model'
    source = record(index)
    payload = {key: source[key] for key in ('request_id', 'target_hour', 'as_of', 'feature_contract_version')}
    with TestClient(app) as client:
        first, second = (client.post('/predict', json=payload), client.post('/predict', json=payload))
        if first.status_code != 200 or second.status_code != 200:
            raise AssertionError('Saved prediction retry did not return HTTP 200')
        if first.json() != second.json() or first.json()['model_version'] != '7':
            raise AssertionError('Saved prediction/version changed within retry')
        if client.post('/predict', json=dict(payload, target_hour='2012-07-02 10:00:00', as_of='2012-07-02 10:00:00')).status_code != 409:
            raise AssertionError('Changed input was not refused with HTTP 409')
        return first.json()

class MigrationAcceptance(_prediction_migration_postgres_unittest.TestCase):
    source_url = restore_url = source_container = restore_container = app_password = evidence = None
    observations = {}

    def setUp(self):
        self.db = _prediction_migration_postgres_Database(self.source_url, _prediction_migration_postgres_PROFILE, _prediction_migration_postgres_TARGET)
        with _prediction_migration_postgres_psycopg.connect(self.source_url, autocommit=True) as c:
            c.execute('DROP SCHEMA public CASCADE')
            c.execute('CREATE SCHEMA public')
        with self.db.transaction() as c:
            for statement in SCHEMA:
                c.execute(statement.replace('predicted DOUBLE PRECISION', 'predicted REAL'))
            self.db.execute(c, 'INSERT INTO data_profile_metadata VALUES (1,?,?)', (_prediction_migration_postgres_PROFILE, _prediction_migration_postgres_TARGET))
        for i in range(len(VALUES)):
            self.db.save_prediction(record(i))
        self.db.add_observation('2012-01-01 00:00:00', 123, '2012-01-01 03:00:00', 'c' * 64)
        with self.db.transaction() as c:
            c.execute("INSERT INTO monitor_windows VALUES ('old-monitor','replay','2012-01-01 01:00:00',1,0,0,NULL,NULL,'{}','{}','fixture')")
            self.db.execute(c, "INSERT INTO deployments(deployment_id,status,phase,data_profile_id,target_contract_id,to_slot,to_version,approver,reason,created_at,updated_at) VALUES ('old-deploy','completed','fixture',?,?,'blue','7','synthetic','fixture','fixture','fixture')", (_prediction_migration_postgres_PROFILE, _prediction_migration_postgres_TARGET))
        self.assertEqual(predicted_type(self.db), 'real')

    def assert_migrated(self, before):
        self.assertEqual(predicted_type(self.db), 'double precision')
        self.assertEqual(before, snapshot(self.db))
        self.assertTrue(all((r['data_type'] == 'real' for r in columns(self.db) if r['table_name'] == 'monitor_windows' and r['column_name'] in ('mae', 'rmse', 'label_coverage'))))

    def test_storage_metadata_and_idempotent_initialize(self):
        before, schema = (snapshot(self.db), columns(self.db))
        self.db.initialize()
        self.assert_migrated(before)
        for old, new in zip(schema, columns(self.db), strict=True):
            self.assertEqual(dict(old, data_type='double precision') if old['table_name'] == 'predictions' and old['column_name'] == 'predicted' else old, new)
        self.db.initialize()
        self.assert_migrated(before)
        for i, value in enumerate(VALUES):
            self.assertEqual(self.db.saved_prediction(record(i)['request_id'])['predicted'], binary32(value))
        self.observations['storage_metadata_repeat_initialize'] = 'passed'

    def test_api_retry_binary_identity_and_decimal_display_difference(self):
        before = [retry_api(self.db, i) for i in range(len(VALUES))]
        rows = snapshot(self.db)
        self.db.initialize()
        after = [retry_api(self.db, i) for i in range(len(VALUES))]
        changes = []
        for i, (old, new) in enumerate(zip(before, after, strict=True)):
            self.assertEqual(_prediction_migration_postgres_struct.pack('!f', old['prediction']), _prediction_migration_postgres_struct.pack('!f', new['prediction']))
            self.assertEqual(new['prediction'], binary32(VALUES[i]))
            self.assertEqual({k: v for k, v in old.items() if k != 'prediction'}, {k: v for k, v in new.items() if k != 'prediction'})
            if old['prediction'] != new['prediction']:
                changes.append(dict(request_id=old['request_id'], before_text_decoded=old['prediction'], after_exact_binary32=new['prediction']))
        self.assertTrue(changes)
        self.assertEqual(rows, snapshot(self.db))
        self.observations['api_retry'] = dict(same_binary32_stored_value=True, stable_after_migration=True, exact_historical_decimal_json_preservation=False, decimal_display_changes=changes, changed_input_status=409, model_reload_required=False)

    def test_new_binary64_and_conflicting_input(self):
        self.db.initialize()
        new = dict(record(2), request_id='new-binary64-request', model_version='99')
        saved = self.db.save_prediction(new)
        self.assertEqual(saved['predicted'], VALUES[2])
        self.assertNotEqual(saved['predicted'], binary32(VALUES[2]))
        self.assertEqual(saved, self.db.save_prediction(dict(new, predicted=999, model_version='100')))
        before = snapshot(self.db)
        with self.assertRaises(RequestConflict):
            self.db.save_prediction(dict(new, input_hash='changed'))
        self.assertEqual(before, snapshot(self.db))
        self.observations['new_binary64_and_conflict'] = 'passed'

    def test_profile_rejection_rolls_back_alter(self):
        before, schema = (snapshot(self.db), columns(self.db))
        with self.assertRaisesRegex(ValueError, 'Database profile mismatch'):
            _prediction_migration_postgres_Database(self.source_url, 'capital-bikeshare', 'capital-trip-starts-hourly-v1').initialize()
        self.assertEqual(before, snapshot(self.db))
        self.assertEqual(schema, columns(self.db))
        self.observations['profile_rejection_rollback'] = 'passed'

    def test_exception_and_connection_loss_rollback_then_resume(self):
        before, schema = (snapshot(self.db), columns(self.db))
        original = self.db.execute
        for failure in ('exception', 'connection_loss'):
            alter_reached = []

            def failed(c, statement, params=()):
                result = original(c, statement, params)
                if statement.startswith('ALTER TABLE predictions ALTER COLUMN predicted TYPE'):
                    alter_reached.append(True)
                    if failure == 'connection_loss':
                        with _prediction_migration_postgres_psycopg.connect(self.source_url, autocommit=True) as killer:
                            killer.execute('SELECT pg_terminate_backend(%s)', (c.info.backend_pid,))
                        c.execute('SELECT 1')
                    raise RuntimeError('Synthetic interruption after ALTER')
                return result
            expected_error = RuntimeError if failure == 'exception' else _prediction_migration_postgres_psycopg.OperationalError
            with _prediction_migration_postgres_patch.object(self.db, 'execute', side_effect=failed), self.assertRaises(expected_error):
                self.db.initialize()
            self.assertEqual(alter_reached, [True])
            self.assertEqual(before, snapshot(self.db))
            self.assertEqual(schema, columns(self.db))
        self.db.initialize()
        self.assert_migrated(before)
        self.observations['exception_and_connection_loss'] = 'passed'

    def test_sigkill_before_and_after_commit(self):
        child = '''
import os,time
from bike_mlops.db import Database
db=Database(os.environ['T08_URL'])
def pause():
    print('READY',flush=True)
    time.sleep(60)
original=db.execute
def execute(c,s,p=()):
    result=original(c,s,p)
    if s.startswith('ALTER TABLE predictions ALTER COLUMN predicted TYPE'):
        pause()
    return result
if os.environ['T08_PHASE']=='before': db.execute=execute
db.initialize()
pause()
'''
        before, schema = (snapshot(self.db), columns(self.db))
        for phase in ('before', 'after'):
            process = _prediction_migration_postgres_subprocess.Popen([_prediction_migration_postgres_sys.executable, '-c', child], env=dict(_prediction_migration_postgres_os.environ, T08_URL=self.source_url, T08_PHASE=phase), stdout=_prediction_migration_postgres_subprocess.PIPE, stderr=_prediction_migration_postgres_subprocess.PIPE, text=True)
            try:
                with _prediction_migration_postgres_selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, _prediction_migration_postgres_selectors.EVENT_READ)
                    self.assertTrue(selector.select(timeout=15), 'Migration barrier not reached')
                self.assertEqual(process.stdout.readline().strip(), 'READY')
                process.kill()
                process.communicate(timeout=10)
                self.assertEqual(process.returncode, -9)
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=10)
            self.assertEqual(before, snapshot(self.db))
            if phase == 'before':
                self.assertEqual(schema, columns(self.db))
            else:
                self.assert_migrated(before)
        self.db.initialize()
        self.assert_migrated(before)
        self.observations['sigkill_before_after_commit'] = 'passed'

    def test_parallel_initialize(self):
        before = snapshot(self.db)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_prediction_migration_postgres_Database(self.source_url, _prediction_migration_postgres_PROFILE, _prediction_migration_postgres_TARGET).initialize) for _ in range(2)]
            for future in futures:
                future.result(timeout=20)
        self.assert_migrated(before)
        self.observations['parallel_initialize'] = 'passed'

    def test_unmarked_old_columns_migration(self):
        before = snapshot(self.db)
        with self.db.transaction() as c:
            c.execute('DROP TABLE data_profile_metadata')
            for name in ('candidate_id', 'data_profile_id', 'target_contract_id'):
                c.execute(_prediction_migration_postgres_sql.SQL('ALTER TABLE predictions DROP COLUMN {}').format(_prediction_migration_postgres_sql.Identifier(name)))
        self.db.initialize()
        self.assert_migrated(before)
        self.observations['old_unmarked_uci_columns'] = 'passed'

    def test_backup_restore_before_after_on_second_volume(self):
        checks = []
        for phase in ('before', 'after'):
            if phase == 'after':
                self.db.initialize()
                self.db.save_prediction(dict(record(2), request_id='new-restore-binary64', model_version='99'))
            before, schema = (snapshot(self.db), columns(self.db))
            env = dict(_prediction_migration_postgres_os.environ, PGPASSWORD=self.app_password)
            dumped = _prediction_migration_postgres_subprocess.run(['docker', 'exec', '-e', 'PGPASSWORD', self.source_container, 'pg_dump', '--host=127.0.0.1', '--username=t08_app', '--dbname=t08_source', '--format=custom', '--no-owner', '--no-acl'], env=env, capture_output=True, timeout=60)
            self.assertEqual(dumped.returncode, 0, 'Owned pg_dump failed')
            self.assertTrue(dumped.stdout.startswith(b'PGDMP'))
            with (self.evidence / f'{phase}.dump').open('xb') as stream:
                stream.write(dumped.stdout)
            restored = _prediction_migration_postgres_subprocess.run(['docker', 'exec', '-i', '-e', 'PGPASSWORD', self.restore_container, 'pg_restore', '--host=127.0.0.1', '--username=t08_app', '--dbname=t08_restore', '--clean', '--if-exists', '--no-owner', '--no-acl', '--exit-on-error', '--single-transaction'], input=dumped.stdout, env=env, capture_output=True, timeout=60)
            self.assertEqual(restored.returncode, 0, 'Owned pg_restore failed')
            db = _prediction_migration_postgres_Database(self.restore_url, _prediction_migration_postgres_PROFILE, _prediction_migration_postgres_TARGET)
            self.assertEqual(schema, columns(db))
            self.assertEqual(before, snapshot(db))
            self.assertEqual(retry_api(db, 1), retry_api(self.db, 1))
            if phase == 'before':
                self.assertEqual(predicted_type(db), 'real')
                db.initialize()
                self.assertEqual(before, snapshot(db))
                self.assertEqual(predicted_type(db), 'double precision')
            else:
                self.assertEqual(db.saved_prediction('new-restore-binary64')['predicted'], VALUES[2])
            checks.append(dict(phase=phase, bytes=len(dumped.stdout), sha256=_prediction_migration_postgres_hashlib.sha256(dumped.stdout).hexdigest(), schema_rows_metadata_and_api_match=True, restore_target='second_owned_instance_and_volume'))
        self.observations['backup_restore'] = checks

def _prediction_migration_postgres_main():
    parser = _prediction_migration_postgres_argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-disposable', action='store_true', required=True)
    parser.add_argument('--evidence-dir', type=_prediction_migration_postgres_Path, required=True)
    args = parser.parse_args()
    args.evidence_dir.mkdir(parents=True, exist_ok=False)

    def interrupted(signum, frame):
        raise KeyboardInterrupt
    _prediction_migration_postgres_signal.signal(_prediction_migration_postgres_signal.SIGTERM, interrupted)
    context = _prediction_migration_postgres_docker('context', 'show')
    host = _prediction_migration_postgres_json.loads(_prediction_migration_postgres_docker('context', 'inspect', context, '--format', '{{json .Endpoints.docker.Host}}'))
    if not host.startswith('unix://') or not _prediction_migration_postgres_os.environ.get('DOCKER_HOST', 'unix://').startswith('unix://'):
        raise RuntimeError('Local Unix-socket Docker required')
    image_id = _prediction_migration_postgres_docker('image', 'inspect', 'postgres:17.6-bookworm', '--format', '{{.Id}}')
    label, owned, result = ('bike.prediction.migration.test', [], None)
    owned_volumes = set()
    password, app_password = (_prediction_migration_postgres_secrets.token_hex(24), _prediction_migration_postgres_secrets.token_hex(24))
    env = dict(_prediction_migration_postgres_os.environ, POSTGRES_PASSWORD=password)
    try:
        for name in ('t08_source', 't08_restore'):
            token = _prediction_migration_postgres_secrets.token_hex(8)
            cid = _prediction_migration_postgres_docker('run', '-d', '--rm', '--pull=never', '--name', f'bike-t08-{token}', '--label', f'{label}={token}', '--publish', '127.0.0.1::5432', '-e', 'POSTGRES_PASSWORD', 'postgres:17.6-bookworm', env=env)
            if len(cid) != 64 or any((c not in '0123456789abcdef' for c in cid)):
                raise RuntimeError('Invalid container ID')
            owned.append((cid, token))
            binding = _prediction_migration_postgres_docker('port', cid, '5432/tcp')
            if not binding.startswith('127.0.0.1:') or '\n' in binding:
                raise RuntimeError('Loopback binding required')
            port = int(binding.rsplit(':', 1)[1])
            admin = f'postgresql://postgres:{password}@127.0.0.1:{port}/postgres'
            for attempt in range(60):
                try:
                    with _prediction_migration_postgres_psycopg.connect(admin, connect_timeout=2) as c:
                        c.execute('SELECT 1')
                    break
                except _prediction_migration_postgres_psycopg.OperationalError:
                    if attempt == 59:
                        raise RuntimeError('Owned PostgreSQL startup failed') from None
                    _prediction_migration_postgres_time.sleep(0.25)
            with _prediction_migration_postgres_psycopg.connect(admin, autocommit=True) as c:
                c.execute(_prediction_migration_postgres_sql.SQL('CREATE ROLE t08_app LOGIN PASSWORD {}').format(_prediction_migration_postgres_sql.Literal(app_password)))
                c.execute(_prediction_migration_postgres_sql.SQL('CREATE DATABASE {} OWNER t08_app').format(_prediction_migration_postgres_sql.Identifier(name)))
            url = f'postgresql://t08_app:{app_password}@127.0.0.1:{port}/{name}'
            if name == 't08_source':
                MigrationAcceptance.source_url, MigrationAcceptance.source_container = (url, cid)
            else:
                MigrationAcceptance.restore_url, MigrationAcceptance.restore_container = (url, cid)
        mounts = [_prediction_migration_postgres_json.loads(_prediction_migration_postgres_docker('inspect', cid, '--format', '{{json .Mounts}}')) for cid, _ in owned]
        volumes = [{m['Name'] for m in group if m.get('Type') == 'volume' and m.get('Name')} for group in mounts]
        if not volumes[0] or not volumes[1] or volumes[0] & volumes[1]:
            raise RuntimeError('Separate data volumes required')
        owned_volumes = set.union(*volumes)
        MigrationAcceptance.app_password, MigrationAcceptance.evidence = (app_password, args.evidence_dir)
        result = _prediction_migration_postgres_unittest.TextTestRunner(verbosity=2).run(_prediction_migration_postgres_unittest.defaultTestLoader.loadTestsFromTestCase(MigrationAcceptance))
    finally:
        for cid, token in reversed(owned):
            labels = _prediction_migration_postgres_json.loads(_prediction_migration_postgres_docker('inspect', cid, '--format', '{{json .Config.Labels}}'))
            if labels.get(label) != token:
                raise RuntimeError('Cleanup ownership mismatch')
            _prediction_migration_postgres_docker('rm', '-f', '-v', cid)
        if _prediction_migration_postgres_docker('ps', '-a', '--filter', f'label={label}', '--format', '{{.ID}}'):
            raise RuntimeError('Owned migration container remains')
        if owned_volumes & set(_prediction_migration_postgres_docker('volume', 'ls', '-q').splitlines()):
            raise RuntimeError('Owned migration volume remains')
        if owned:
            print('T08 source/restore containers and anonymous volumes removed; cleanup_complete=true', flush=True)
    output = dict(task='T08', scope='synthetic_only', status='passed' if result.wasSuccessful() else 'failed', tests_run=result.testsRun, failures=len(result.failures), errors=len(result.errors), observations=MigrationAcceptance.observations, image_id=image_id, separate_source_and_restore_volumes=True, cleanup_complete=True, real_database_migration_performed=False, deployment_permission='not_evaluated')
    with (args.evidence_dir / 'summary.json').open('x') as stream:
        _prediction_migration_postgres_json.dump(output, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(_prediction_migration_postgres_json.dumps(output, allow_nan=False))
    return 0 if result.wasSuccessful() else 1
def _entry_test_prediction_migration_postgres():
    try:
        raise SystemExit(_prediction_migration_postgres_main())
    except KeyboardInterrupt:
        raise SystemExit('T08 interrupted; owned-resource cleanup attempted') from None
    except Exception as error:
        raise SystemExit(f'T08 failed ({type(_prediction_migration_postgres_error).__name__}); owned-resource cleanup attempted') from None


# ========================================================================
# test_monitor_inspection_sqlite_os
# ========================================================================

"""Disposable S13 OS permission and real SQLite lock wait, without mocks."""
import hashlib as _monitor_inspection_sqlite_os_hashlib, json as _monitor_inspection_sqlite_os_json, os as _monitor_inspection_sqlite_os_os, pathlib, sqlite3, sys as _monitor_inspection_sqlite_os_sys, time as _monitor_inspection_sqlite_os_time


def _monitor_inspection_sqlite_os_main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-disposable', action='store_true', required=True)
    parser.parse_args()
    _monitor_inspection_sqlite_os_os.environ['DATA_PROFILE_ID'] = 'uci-bike-sharing'
    root=pathlib.Path(__file__).resolve().parents[1]
    _monitor_inspection_sqlite_os_sys.path.insert(0,str(root/'tests'))
    from test_monitor_inspection import MonitorInspectionTests
    from bike_mlops.monitor_inspection import inspect_monitor_run
    from bike_mlops.config import DATA_PROFILE_ID, TARGET_CONTRACT_ID
    if _monitor_inspection_sqlite_os_os.geteuid()==0:
        raise SystemExit('Must use an unprivileged account for chmod denial')
    x=MonitorInspectionTests();x.setUp()
    def inspect():
        return inspect_monitor_run(x.csv,x.run_id,database_url=x.db_url,data_profile_id=DATA_PROFILE_ID,target_contract_id=TARGET_CONTRACT_ID)
    def digest(path): return _monitor_inspection_sqlite_os_hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        with sqlite3.connect(x.db_path) as c:
            assert c.execute('PRAGMA journal_mode=DELETE').fetchone()[0]=='delete'
        before=digest(x.db_path); files=x._file_snapshot(x.paths); logical=x._logical_snapshot(x.db_path)
        mode=x.db_path.stat().st_mode & 0o777
        try:
            x.db_path.chmod(0)
            permission=inspect()
            assert permission['status']=='database_unavailable' and permission['exit_code']==2
        finally:
            x.db_path.chmod(mode)
        assert digest(x.db_path)==before and x._file_snapshot(x.paths)==files
        assert inspect()['status']=='matched'
        locker=sqlite3.connect(x.db_path,isolation_level=None)
        try:
            locker.execute('BEGIN EXCLUSIVE')
            began=_monitor_inspection_sqlite_os_time.monotonic_ns(); lock=inspect(); ended=_monitor_inspection_sqlite_os_time.monotonic_ns()
            assert lock['status']=='database_unavailable' and lock['exit_code']==2
            assert 4 <= (ended-began)/1e9 < 10
        finally:
            locker.rollback();locker.close()
        assert digest(x.db_path)==before and x._file_snapshot(x.paths)==files
        assert x._logical_snapshot(x.db_path)==logical
        assert inspect()['status']=='matched'
        print(_monitor_inspection_sqlite_os_json.dumps(dict(status='passed',synthetic_only=True,real_Capital_evidence=False,
            unprivileged_euid=_monitor_inspection_sqlite_os_os.geteuid(),permission_status=permission['status'],permission_exit=permission['exit_code'],
            lock_status=lock['status'],lock_exit=lock['exit_code'],lock_elapsed_monotonic_seconds=(ended-began)/1e9,
            busy_timeout_seconds=5,journal_mode='delete',database_bytes_unchanged=True,logical_rows_unchanged=True,
            CSV_JSON_marker_unchanged=True,permissions_restored=True,post_release_matched=True)))
    finally:
        x.tearDown()


def _entry_test_monitor_inspection_sqlite_os():
    _monitor_inspection_sqlite_os_main()


# ========================================================================
# test_monitor_reconciliation_storage
# ========================================================================

"""Opt-in T06 storage probe; no project database or existing reports are used.

This checks assumptions for the inspector design, not a completed inspector.
Uses the existing local PostgreSQL image and removes its owned container.
"""


import argparse as _monitor_reconciliation_storage_argparse
import hashlib as _monitor_reconciliation_storage_hashlib
import json as _monitor_reconciliation_storage_json
import math as _monitor_reconciliation_storage_math
import os as _monitor_reconciliation_storage_os
import secrets as _monitor_reconciliation_storage_secrets
import sqlite3 as _monitor_reconciliation_storage_sqlite3
import struct as _monitor_reconciliation_storage_struct
import tempfile as _monitor_reconciliation_storage_tempfile
import time as _monitor_reconciliation_storage_time
import uuid as _monitor_reconciliation_storage_uuid
from pathlib import Path as _monitor_reconciliation_storage_Path

import pandas as _monitor_reconciliation_storage_pd

from bike_mlops.db import Database as _monitor_reconciliation_storage_Database
from bike_mlops.monitoring import persist_monitor_run as _monitor_reconciliation_storage_persist_monitor_run
_monitor_reconciliation_storage_docker = _monitor_persistence_postgres_docker


_monitor_reconciliation_storage_PROFILE = "capital-bikeshare"
_monitor_reconciliation_storage_TARGET = "capital-trip-starts-hourly-v1"


def windows():
    return _monitor_reconciliation_storage_pd.DataFrame([
        {"window_end": "2024-01-18 00:00:00", "prediction_count": 3, "scored_count": 0,
         "label_coverage": 0., "mae": None, "rmse": None, "psi": {"hour": None},
         "feature_missing_rates": {"lag_24": 1.}},
        {"window_end": "2024-01-18 01:00:00", "prediction_count": 3, "scored_count": 1,
         "label_coverage": 1 / 3, "mae": 72.53620455822316, "rmse": 111.50603198624435,
         "psi": {"hour": .123456789}, "feature_missing_rates": {"lag_24": 1 / 3}},
    ])


def _monitor_reconciliation_storage_normalized(value):
    if isinstance(value, float) and _monitor_reconciliation_storage_math.isnan(value):
        return "SQL_NaN"
    return value


def logical_rows(database):
    with database.session() as connection:
        return [dict(row) for row in database.execute(connection,
            "SELECT * FROM monitor_windows ORDER BY window_end").fetchall()]


def persist(database, root):
    database.initialize()  # Fixture setup only; the future inspector must never call this.
    _monitor_reconciliation_storage_persist_monitor_run(database, windows(), root / "monitor.csv",
        {"replay_id": "t06-storage-probe", "model_version": "7", "window_count": 2,
         "data_profile_id": _monitor_reconciliation_storage_PROFILE, "target_contract_id": _monitor_reconciliation_storage_TARGET}, run_id=str(_monitor_reconciliation_storage_uuid.uuid4()))


def sqlite_probe(root):
    path = root / "ops.db"
    database = _monitor_reconciliation_storage_Database(f"sqlite:///{path}", _monitor_reconciliation_storage_PROFILE, _monitor_reconciliation_storage_TARGET)
    persist(database, root)
    before = logical_rows(database)
    files_before = {p.name: _monitor_reconciliation_storage_hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob("monitor.*")}
    with _monitor_reconciliation_storage_sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = _monitor_reconciliation_storage_sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        rows = [dict(row) for row in connection.execute("SELECT * FROM monitor_windows ORDER BY window_end")]
        assert rows == before and rows[0]["mae"] is None and rows[1]["label_coverage"] == 1 / 3
        try:
            connection.execute("UPDATE monitor_windows SET prediction_count=99")
        except _monitor_reconciliation_storage_sqlite3.OperationalError as error:
            assert error.sqlite_errorcode == _monitor_reconciliation_storage_sqlite3.SQLITE_READONLY
        else:
            raise AssertionError("SQLite read-only connection accepted an update")
        connection.rollback()
    missing = root / "absent" / "ops.db"
    try:
        _monitor_reconciliation_storage_sqlite3.connect(missing.as_uri() + "?mode=ro", uri=True)
    except _monitor_reconciliation_storage_sqlite3.OperationalError:
        pass
    else:
        raise AssertionError("Read-only connect unexpectedly created a missing database")
    assert not missing.parent.exists()
    assert logical_rows(database) == before
    assert {p.name: _monitor_reconciliation_storage_hashlib.sha256(p.read_bytes()).hexdigest() for p in root.glob("monitor.*")} == files_before
    return {"numeric_storage": "binary64", "blank_mae_database_value": None,
            "coverage": rows[1]["label_coverage"], "write_rejected": True,
            "missing_database_not_created": True, "logical_rows_and_reports_unchanged": True}


def postgres_probe(root):
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row

    context = _monitor_reconciliation_storage_docker("context", "show")
    host = _monitor_reconciliation_storage_json.loads(_monitor_reconciliation_storage_docker("context", "inspect", context, "--format", "{{json .Endpoints.docker.Host}}"))
    if (not isinstance(host, str) or not host.startswith("unix://")
            or (_monitor_reconciliation_storage_os.environ.get("DOCKER_HOST") and not _monitor_reconciliation_storage_os.environ["DOCKER_HOST"].startswith("unix://"))):
        raise RuntimeError("The probe requires a local Unix-socket Docker daemon")
    image = "postgres:17.6-bookworm"
    image_id = _monitor_reconciliation_storage_docker("image", "inspect", image, "--format", "{{.Id}}")
    token = _monitor_reconciliation_storage_secrets.token_hex(8)
    label = "bike.monitor.reconciliation.probe"
    password = _monitor_reconciliation_storage_secrets.token_hex(24)
    env = _monitor_reconciliation_storage_os.environ.copy()
    env["POSTGRES_PASSWORD"] = password
    container_id = None
    try:
        container_id = _monitor_reconciliation_storage_docker("run", "-d", "--rm", "--pull=never",
            "--name", f"bike-monitor-reconcile-{token}", "--label", f"{label}={token}",
            "--publish", "127.0.0.1::5432", "-e", "POSTGRES_PASSWORD", image, env=env)
        if len(container_id) != 64 or any(c not in "0123456789abcdef" for c in container_id):
            raise RuntimeError("Docker returned an invalid container ID")
        port_binding = _monitor_reconciliation_storage_docker("port", container_id, "5432/tcp")
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
                _monitor_reconciliation_storage_time.sleep(.25)
        app_password = _monitor_reconciliation_storage_secrets.token_hex(24)
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE ROLE t06_app LOGIN PASSWORD {}").format(sql.Literal(app_password)))
            connection.execute("CREATE DATABASE t06_probe OWNER t06_app")
        url = f"postgresql://t06_app:{app_password}@127.0.0.1:{port}/t06_probe"
        database = _monitor_reconciliation_storage_Database(url, _monitor_reconciliation_storage_PROFILE, _monitor_reconciliation_storage_TARGET)
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
            assert _monitor_reconciliation_storage_math.isnan(rows[0]["mae"]) and _monitor_reconciliation_storage_math.isnan(rows[0]["rmse"])
            for name in ("label_coverage", "mae", "rmse"):
                source = windows().iloc[1][name]
                assert _monitor_reconciliation_storage_struct.pack("!f", source) == _monitor_reconciliation_storage_struct.pack("!f", rows[1][name])
                assert source != rows[1][name]
            assert _monitor_reconciliation_storage_json.loads(rows[1]["psi_json"]) == {"hour": .123456789}
            try:
                connection.execute("UPDATE monitor_windows SET prediction_count=99")
            except psycopg.errors.ReadOnlySqlTransaction:
                pass
            else:
                raise AssertionError("PostgreSQL read-only transaction accepted an update")
            connection.execute("ROLLBACK")
        after = logical_rows(database)
        # NaN is unequal to itself; normalize only for this fixture's non-mutation comparison.
        clean = lambda data: [{k: _monitor_reconciliation_storage_normalized(v) for k, v in row.items()} for row in data]
        assert clean(before) == clean(after)
        result = {"image_id": image_id, "column_types": types, "numeric_storage": "binary32",
                  "blank_mae_database_value": _monitor_reconciliation_storage_normalized(rows[0]["mae"]),
                  "source_coverage": 1 / 3, "database_coverage": rows[1]["label_coverage"],
                  "binary32_quantization_matches": True, "json_numbers_preserve_binary64": True,
                  "write_rejected": True, "logical_rows_unchanged": True}
    finally:
        if container_id:
            labels = _monitor_reconciliation_storage_json.loads(_monitor_reconciliation_storage_docker("inspect", container_id, "--format", "{{json .Config.Labels}}"))
            if labels.get(label) != token:
                raise RuntimeError("Refusing to stop a container without the probe ownership label")
            _monitor_reconciliation_storage_docker("stop", container_id)
            if _monitor_reconciliation_storage_docker("ps", "-a", "--filter", f"label={label}={token}", "--format", "{{.ID}}"):
                raise RuntimeError("Probe container remains after cleanup")
    result["cleanup_complete"] = True
    return result


def _monitor_reconciliation_storage_main():
    parser = _monitor_reconciliation_storage_argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-disposable", action="store_true", help="Run synthetic SQLite and owned PostgreSQL probes")
    args = parser.parse_args()
    if not args.run_disposable:
        parser.error("--run-disposable is required; no probe was started")
    with _monitor_reconciliation_storage_tempfile.TemporaryDirectory(prefix="monitor-reconciliation-storage-") as directory:
        root = _monitor_reconciliation_storage_Path(directory)
        (root / "sqlite").mkdir()
        (root / "postgres").mkdir()
        sqlite_result = sqlite_probe(root / "sqlite")
        pg_result = postgres_probe(root / "postgres")
        print(_monitor_reconciliation_storage_json.dumps({"status": "passed", "scope": "T06 storage assumptions only, synthetic data",
                          "sqlite": sqlite_result, "postgres": pg_result}, indent=2, allow_nan=False))


def _entry_test_monitor_reconciliation_storage():
    try:
        _monitor_reconciliation_storage_main()
    except Exception as error:
        # DSNs and randomly generated credentials must not appear in diagnostic output.
        raise SystemExit(f"Storage probe failed ({type(_monitor_reconciliation_storage_error).__name__}); owned-resource cleanup was attempted") from None


def _dispatch_cli():
    import sys as _dispatch_sys
    commands = {'snapshot': _entry_test_capital_snapshot_postgres, 'interruption': _entry_test_capital_request_interruption_postgres, 'monitor-inspection': _entry_test_monitor_inspection_postgres, 'monitor-persistence': _entry_test_monitor_persistence_postgres, 'migration': _entry_test_prediction_migration_postgres, 'sqlite-os': _entry_test_monitor_inspection_sqlite_os, 'storage-probe': _entry_test_monitor_reconciliation_storage}
    if len(_dispatch_sys.argv) < 2 or _dispatch_sys.argv[1] in {"-h", "--help"}:
        print("Usage: storage_checks.py <command> [options]\nCommands: snapshot, interruption, monitor-inspection, monitor-persistence, migration, sqlite-os, storage-probe")
        return 0
    command = _dispatch_sys.argv.pop(1)
    if command not in commands:
        raise SystemExit("Unknown command: " + command)
    return commands[command]()


if __name__ == "__main__":
    raise SystemExit(_dispatch_cli())
