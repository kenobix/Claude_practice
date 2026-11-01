"""Opt-in synthetic decisions against a private MLflow HTTP server and fault proxy.

No external endpoint argument is accepted. Registry/artifacts/locks live in a new
temporary directory; the server must own its loopback listener before any write.
The evidence fixture and preseeded historical decision timestamps are synthetic.
This tests transport recovery, never real Capital eligibility or clock fitness.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
FIXED_TIME = "2026-11-01T00:00:00+00:00"


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def process_identity(pid):
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return {"pid": pid, "ppid": int(fields[1]), "pgrp": int(fields[2]),
            "start_ticks": int(fields[19])}


def group_members(group):
    members = []
    for directory in Path("/proc").iterdir():
        if directory.name.isdecimal():
            try:
                row = process_identity(int(directory.name))
                if row["pgrp"] == group:
                    members.append(row)
            except (OSError, ValueError, IndexError):
                pass
    return members


def listener_owner(process, port, expected):
    """Read only our descendant fd links, refusing a stolen/reused listener."""
    if process.poll() is not None or process_identity(process.pid) != expected:
        raise RuntimeError("Private server exited or changed identity")
    identities = {}
    for directory in Path("/proc").iterdir():
        if directory.name.isdecimal():
            try:
                identities[int(directory.name)] = process_identity(int(directory.name))
            except (OSError, ValueError, IndexError):
                pass
    children = {process.pid}
    while True:
        more = {pid for pid, row in identities.items() if row["ppid"] in children}
        if more <= children:
            break
        children |= more
    inodes = set()
    for line in Path("/proc/net/tcp").read_text().splitlines()[1:]:
        fields = line.split()
        if fields[1] == f"0100007F:{port:04X}" and fields[3] == "0A":
            inodes.add(fields[9])
    owners = []
    for pid in children:
        row = identities.get(pid)
        if not row or row["pgrp"] != expected["pgrp"] or row["start_ticks"] < expected["start_ticks"]:
            continue
        try:
            for fd in Path(f"/proc/{pid}/fd").iterdir():
                try:
                    target = os.readlink(fd)
                except OSError:
                    continue
                if target in {f"socket:[{inode}]" for inode in inodes}:
                    owners.append(dict(row, fd=fd.name))
        except OSError:
            pass
    if not owners:
        raise RuntimeError("Loopback listener is not owned by the private MLflow server")
    return owners


class FaultProxy(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, backend_port, log):
        self.backend_port, self.log = backend_port, log
        self.guard = threading.Lock()
        self.fault = None
        self.hit = 0
        super().__init__(("127.0.0.1", 0), ProxyHandler)

    def arm(self, key, mode):
        with self.guard:
            self.fault, self.hit = (key, mode), 0

    def event(self, value):
        with self.guard, self.log.open("a") as stream:
            stream.write(json.dumps(value, sort_keys=True) + "\n")


class ProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def forward(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        payload = json.loads(body) if body else {}
        key = payload.get("key") if self.path.endswith("/set-tag") else None
        with self.server.guard:
            fault = self.server.fault
            mode = fault[1] if fault and key == fault[0] else None
            if mode:
                self.server.hit += 1
        event = {"method": self.command, "path": self.path, "tag": key,
                 "value": payload.get("value") if key else None, "fault": mode,
                 "monotonic_ns": time.monotonic_ns()}
        if mode == "before_write":
            data = b'{"error_code":"TEMPORARILY_UNAVAILABLE","message":"synthetic pre-write failure"}'
            event.update(status=502, forwarded=False)
        else:
            conn = http.client.HTTPConnection("127.0.0.1", self.server.backend_port, timeout=15)
            try:
                conn.request(self.command, self.path, body=body or None,
                             headers={"Content-Type": "application/json"})
                response = conn.getresponse()
                status, data = response.status, response.read()
            finally:
                conn.close()
            event.update(status=status, forwarded=True)
            if mode == "lost_response":
                self.server.event(event)
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
        self.server.event(event)
        self.send_response(event["status"])
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def expect_failure(call):
    try:
        call()
    except Exception as error:
        return {"error_type": type(error).__name__, "message": str(error)[:1500]}
    raise AssertionError("Expected rejection or transport failure")


def request_stop(signum, frame):
    """Unwind our own server/proxy cleanup when GNU timeout sends TERM."""
    raise SystemExit(128 + signum)


def exercise(root, output, client, proxy):
    sys.path.insert(0, str(ROOT / "tests"))
    from capital_deployment_fixture import fixture
    from bike_mlops import capital_deployment_evidence as evidence
    from bike_mlops import capital_operational_registry as registry
    from bike_mlops.capital_deployment_decisions import record_deployment_approval, record_operational_review
    from bike_mlops.deployment_gates import require_deployment_eligible

    experiment = client.create_experiment("synthetic-http-only", artifact_location=(root / "artifacts").as_uri())
    run = client.create_run(experiment)
    run_id = run.info.run_id
    client.create_registered_model(evidence.MODEL)
    artifact_uri = urlsplit(run.info.artifact_uri)
    if artifact_uri.scheme != "file" or artifact_uri.netloc:
        raise ValueError("Synthetic artifacts must be private local files")
    artifact_root = Path(unquote(artifact_uri.path)).resolve()
    if not artifact_root.is_relative_to(root.resolve()):
        raise ValueError("Synthetic artifact path escaped the temporary workspace")
    source = artifact_root / "not-a-trained-model"
    source.mkdir(parents=True)
    for _ in range(2):
        client.create_model_version(evidence.MODEL, str(source), run_id=run_id)
    tags, bundle, _ = fixture()
    bundle["identity"]["run_id"] = run_id
    body = evidence.canonical_bytes(bundle)
    (output / "synthetic-evidence-bundle.json").write_bytes(body)
    refs = evidence.reference_tags(body, run_id)
    tags.update(refs)
    # Explicit, immutable synthetic inputs, not a wall-clock clamp or validator patch.
    tags.update(capital_operational_review_at_utc=FIXED_TIME, capital_deployment_approval_at_utc=FIXED_TIME)
    artifact = root / "bundle.json"
    artifact.write_bytes(body)
    client.log_artifact(run_id, str(artifact), artifact_path=refs["capital_deployment_evidence_path"].rsplit("/", 1)[0])
    for key, value in tags.items():
        client.set_model_version_tag(evidence.MODEL, "2", key, value)

    def snapshot(label):
        current = dict(client.get_model_version(evidence.MODEL, "2").tags)
        save(output / (label + "-tags.json"), current)
        return current

    def eligible(current):
        require_deployment_eligible("capital-bikeshare", current, model_name=evidence.MODEL,
                                    model_version="2", client=client)

    def review(reason="synthetic transport test only"):
        return record_operational_review(client, "2", "synthetic-reviewer", "passed", reason)

    def approve(reason="synthetic transport test only"):
        return record_deployment_approval(client, "2", "synthetic-approver", reason)

    result = {"synthetic_only": True, "decision_time_input": FIXED_TIME,
              "artifact_transport": "temporary local file, not HTTP", "cases": {}}
    with patch.object(registry, "PROJECT_ROOT", root):
        proxy.arm("capital_operational_review_reason", "before_write")
        failure = expect_failure(review)
        assert proxy.hit == 1, "Library retries must be disabled"
        proxy.arm(None, None)
        partial = snapshot("partial-review")
        assert partial["operational_gate_status"] == "not_evaluated"
        assert partial["deployment_permission"] == "blocked"
        assert partial["capital_operational_review_reviewer"] == "synthetic-reviewer"
        assert "capital_operational_review_reason" not in partial
        denied = expect_failure(lambda: eligible(partial))
        proxy.arm("operational_gate_status", "lost_response")
        gate_failure = expect_failure(review)
        assert proxy.hit == 1
        proxy.arm(None, None)
        gate_committed = snapshot("response-lost-review")
        assert gate_committed["operational_gate_status"] == "passed"
        assert gate_committed["deployment_permission"] == "blocked"
        expect_failure(lambda: eligible(gate_committed))
        recovered = review()
        current = snapshot("review-recovered")
        assert current == gate_committed
        assert current["capital_operational_review_at_utc"] == FIXED_TIME
        assert current["operational_gate_status"] == "passed" and current["deployment_permission"] == "blocked"
        assert review() == recovered
        result["cases"]["partial_review"] = dict(failure=failure, loader_rejection=denied,
                                                  recovery=recovered, injected_requests=1)
        result["cases"]["lost_review_response"] = dict(failure=gate_failure, recovery=recovered,
                                                       readback="passed_but_blocked", injected_requests=1)

        proxy.arm("capital_deployment_approval_reason", "before_write")
        partial_failure = expect_failure(approve)
        assert proxy.hit == 1
        proxy.arm(None, None)
        partial_approval = snapshot("partial-approval")
        assert partial_approval["deployment_permission"] == "blocked"
        assert partial_approval["capital_deployment_approver"] == "synthetic-approver"
        assert "capital_deployment_approval_reason" not in partial_approval
        result["cases"]["partial_approval"] = dict(failure=partial_failure,
            loader_rejection=expect_failure(lambda: eligible(partial_approval)), injected_requests=1)

        proxy.arm("deployment_permission", "lost_response")
        failure = expect_failure(approve)
        assert proxy.hit == 1
        proxy.arm(None, None)
        committed = snapshot("response-lost-approval")
        assert committed["deployment_permission"] == "eligible"
        eligible(committed)
        recovered = approve()
        assert snapshot("approval-recovered") == committed
        assert recovered["approval_sha256"] == committed["capital_deployment_approval_sha256"]
        result["cases"]["lost_approval_response"] = dict(failure=failure, client_outcome="unknown_until_readback",
                                                          readback="eligible_and_coherent", recovery=recovered,
                                                          injected_requests=1)

        conflict = expect_failure(lambda: approve("conflicting synthetic reason"))
        assert "overwrite" in conflict["message"]
        assert snapshot("conflict-rejected") == committed
        # The lock belongs to this temporary workspace only. Force a simultaneous
        # competing write at a known point, not probabilistic thread scheduling.
        entered, release = threading.Event(), threading.Event()
        original = client.set_model_version_tag
        def block_write(*args, **kwargs):
            if args[2] == "deployment_permission":
                entered.set()
                if not release.wait(15):
                    raise TimeoutError("Synthetic lock coordination failed")
            return original(*args, **kwargs)
        with patch.object(client, "set_model_version_tag", side_effect=block_write), ThreadPoolExecutor(1) as pool:
            first = pool.submit(approve)
            try:
                assert entered.wait(15)
                competing = expect_failure(lambda: approve("parallel conflicting reason"))
                assert competing["error_type"] == "RuntimeError" and "lock" in competing["message"]
            finally:
                release.set()
            assert first.result(timeout=20) == recovered
        assert snapshot("concurrent-rejected") == committed
        result["cases"]["conflicting_decisions"] = dict(sequential=conflict, concurrent=competing,
                                                          metadata_unchanged=True)
    result["status"] = "passed"
    save(output / "cases.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.run_disposable:
        parser.error("Explicit --run-disposable is required")
    output = args.evidence_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("MLFLOW_"):
            env.pop(key)
    env.update(MLFLOW_DISABLE_AGENT_HINT="1", MLFLOW_HTTP_REQUEST_MAX_RETRIES="0",
               MLFLOW_HTTP_REQUEST_TIMEOUT="15", NO_PROXY="127.0.0.1,localhost")
    process, proxy, thread = None, None, None
    cleanup = {"server_stopped": False, "proxy_stopped": False, "temporary_workspace_removed": False}
    temporary = tempfile.TemporaryDirectory(prefix="bike-decision-http-")
    root = Path(temporary.name)
    previous_term = signal.signal(signal.SIGTERM, request_stop)
    try:
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        uri = f"http://127.0.0.1:{port}"
        cmd = [sys.executable, "-m", "mlflow", "server", "--backend-store-uri", f"sqlite:///{root / 'mlflow.db'}",
               "--registry-store-uri", f"sqlite:///{root / 'mlflow.db'}", "--no-serve-artifacts",
               "--default-artifact-root", (root / "artifacts").as_uri(), "--host", "127.0.0.1",
               "--port", str(port), "--workers", "1", "--allowed-hosts", f"127.0.0.1:{port}"]
        save(output / "command.json", cmd)
        try:
            with (output / "server.log").open("x") as log:
                process = subprocess.Popen(cmd, cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            identity = process_identity(process.pid)
            deadline = time.monotonic() + 60
            while True:
                if process.poll() is not None:
                    raise RuntimeError("Private MLflow startup failed; see server.log")
                try:
                    owners = listener_owner(process, port, identity)
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                    try:
                        conn.request("GET", "/health")
                        response = conn.getresponse()
                        response.read()
                        ready = response.status == 200
                    finally:
                        conn.close()
                    if ready:
                        break
                except (OSError, RuntimeError, http.client.HTTPException):
                    pass
                if time.monotonic() >= deadline:
                    raise TimeoutError("Private server readiness/ownership deadline")
                time.sleep(.1)
            save(output / "listener-ownership.json", {"server": identity, "owners": owners, "port": port})
            proxy = FaultProxy(port, output / "http.jsonl")
            thread = threading.Thread(target=proxy.serve_forever)
            thread.start()
            proxy_uri = f"http://127.0.0.1:{proxy.server_port}"
            # Every client uses the same private endpoint; don't inherit a live registry.
            env.update(MLFLOW_TRACKING_URI=proxy_uri, MLFLOW_REGISTRY_URI=proxy_uri)
            with patch.dict(os.environ, env, clear=True):
                from mlflow.tracking import MlflowClient
                client = MlflowClient(tracking_uri=proxy_uri, registry_uri=proxy_uri)
                listener_owner(process, port, identity)
                result = exercise(root, output, client, proxy)
                print(json.dumps(result), flush=True)
        finally:
            if proxy is not None:
                proxy.shutdown()
                proxy.server_close()
                thread.join(timeout=10)
                cleanup["proxy_stopped"] = not thread.is_alive()
            if process is not None:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=3)
                cleanup["server_stopped"] = process.poll() is not None
                cleanup["server_returncode"] = process.returncode
                deadline = time.monotonic() + 3
                remaining = group_members(process.pid)
                while remaining and time.monotonic() < deadline:
                    time.sleep(.05)
                    remaining = group_members(process.pid)
                cleanup["server_group_remaining"] = remaining
            save(output / "cleanup.json", cleanup)
    except BaseException as error:
        save(output / "failure.json", {"error_type": type(error).__name__, "message": str(error)[:1500]})
        raise
    finally:
        temporary.cleanup()
        signal.signal(signal.SIGTERM, previous_term)
        cleanup["temporary_workspace_removed"] = not root.exists()
        save(output / "cleanup.json", cleanup)
        files = {p.name: {"bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                 for p in output.iterdir() if p.is_file() and p.name != "hashes.json"}
        save(output / "hashes.json", files)
        print(json.dumps({"status": "cleanup_observed", **cleanup}), flush=True)
    assert all(cleanup[key] for key in ("server_stopped", "proxy_stopped", "temporary_workspace_removed"))
    assert not cleanup["server_group_remaining"]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
