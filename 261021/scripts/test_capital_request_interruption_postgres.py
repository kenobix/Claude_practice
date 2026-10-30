"""Opt-in real HTTP timeout/late-commit and connection-loss checks in disposable PostgreSQL.

Only an already-cached image is used. No project service, port, volume or registry
is changed. HTTP fixture records a synthetic prediction; it is not a trained API.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from test_capital_snapshot_postgres import docker


def check(database) -> dict:
    import httpx
    import pandas as pd
    from bike_mlops import capital_operational_recovery as recovery, capital_pending_audit as pending
    from bike_mlops.capital_benchmark_observer import CapitalBenchmarkObserver
    from bike_mlops.capital_measurement_guard import hold_observation_snapshot
    from bike_mlops.capital_request_journal import RequestJournal
    from bike_mlops.features import available_observation_frame
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    from test_capital_operational_ledger import OperationalLedgerTests
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
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
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
            started = time.perf_counter()
            try:
                with httpx.Client(timeout=0.25, trust_env=False) as client:
                    client.post(f"http://127.0.0.1:{server.server_port}/predict", json=payload)
            except httpx.ReadTimeout:
                observer.request_result(payload, http_status=None, latency_ms=(time.perf_counter()-started)*1000,
                                        error="ReadTimeout")
            else:
                raise AssertionError("Real HTTP request did not time out")
            if not received.wait(5) or handler_errors: raise AssertionError("HTTP fixture did not begin transaction")
            # No failure report is fabricated. The lease exits interrupted; inspection uses journal alone.
        args = dict(candidate_id=fixture.candidate, attempt_id=attempt.attempt_id, database=database,
                    ledger_path=fixture.ledger_path, lock_dir=fixture.lock_dir)
        with patch.object(recovery, "PROJECT_ROOT", fixture.root), patch.object(pending, "_validate_staging_database"):
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
                with patch.object(database, "session", return_value=nullcontext(broken)):
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


def main():
    context = docker("context", "show")
    host = json.loads(docker("context", "inspect", context, "--format", "{{json .Endpoints.docker.Host}}"))
    if not host.startswith("unix://") or not os.environ.get("DOCKER_HOST", "unix://").startswith("unix://"):
        raise RuntimeError("Test requires a local Unix-socket daemon")
    image = "postgres:17.6-bookworm"
    docker("image", "inspect", image, "--format", "{{.Id}}")
    suffix, password = secrets.token_hex(8), secrets.token_hex(24)
    name = f"bike-request-interruption-{suffix}"
    environment = os.environ.copy()
    environment["POSTGRES_PASSWORD"] = password
    container_id = None
    try:
        container_id = docker("run", "-d", "--rm", "--name", name, "--label", f"bike.request.test={suffix}",
            "--publish", "127.0.0.1::5432", "-e", "POSTGRES_PASSWORD", image, env=environment)
        if len(container_id) != 64 or any(c not in "0123456789abcdef" for c in container_id):
            raise RuntimeError("Unexpected container identity")
        published = docker("port", container_id, "5432/tcp")
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
                time.sleep(0.5)
        app_password = secrets.token_hex(24)
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE ROLE request_test LOGIN PASSWORD {}").format(sql.Literal(app_password)))
            connection.execute("CREATE DATABASE request_test OWNER request_test")
        database = Database(f"postgresql://request_test:{app_password}@127.0.0.1:{port}/request_test",
                            "capital-bikeshare", "capital-trip-starts-hourly-v1")
        database.initialize()
        print(json.dumps(check(database), indent=2))
    finally:
        remaining = docker("ps", "-a", "-q", "--no-trunc", "--filter", f"name=^/{name}$",
                           "--filter", f"label=bike.request.test={suffix}").splitlines()
        if len(remaining) > 1 or (container_id and remaining and remaining[0] != container_id):
            raise RuntimeError("Cleanup identity mismatch; no unrelated container stopped")
        if remaining:
            docker("rm", "-f", remaining[0])
        print("cleanup_complete: disposable request interruption container removed")


if __name__ == "__main__":
    main()
