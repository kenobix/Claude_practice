"""Explicit command tools: runtime_checks."""
from __future__ import annotations


# ========================================================================
# test_capital_decision_http
# ========================================================================

"""Opt-in synthetic decisions against a private MLflow HTTP server and fault proxy.

No external endpoint argument is accepted. Registry/artifacts/locks live in a new
temporary directory; the server must own its loopback listener before any write.
The evidence fixture and preseeded historical decision timestamps are synthetic.
This tests transport recovery, never real Capital eligibility or clock fitness.
"""


import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import http as _capital_decision_http_http
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
            conn = _capital_decision_http_http.client.HTTPConnection("127.0.0.1", self.server.backend_port, timeout=15)
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
    from support import fixture
    from bike_mlops import deployment as evidence
    from bike_mlops import operational_validation as registry
    from bike_mlops.deployment import record_deployment_approval, record_operational_review
    from bike_mlops.deployment import require_deployment_eligible

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
    with patch.object(registry, '_operational_registry_PROJECT_ROOT', root):
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
                    conn = _capital_decision_http_http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                    try:
                        conn.request("GET", "/health")
                        response = conn.getresponse()
                        response.read()
                        ready = response.status == 200
                    finally:
                        conn.close()
                    if ready:
                        break
                except (OSError, RuntimeError, _capital_decision_http_http.client.HTTPException):
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


def _entry_test_capital_decision_http():
    raise SystemExit(main())


# ========================================================================
# test_deployment_recovery_system
# ========================================================================

"""Opt-in synthetic UCI deployment recovery with real API, PostgreSQL and Caddy.

Model and registry are explicit doubles. Only UUID-labelled disposable resources,
random loopback ports and an allowlisted workspace copy are used. No real registry.
Builds a uniquely tagged current Dockerfile image; PostgreSQL/Caddy are disposable.
"""


import argparse as _deployment_recovery_system_argparse
from concurrent.futures import ThreadPoolExecutor as _deployment_recovery_system_ThreadPoolExecutor
from contextlib import ExitStack
import hashlib as _deployment_recovery_system_hashlib
import json as _deployment_recovery_system_json
import os as _deployment_recovery_system_os
from pathlib import Path as _deployment_recovery_system_Path
import re
import secrets
import shutil
import signal as _deployment_recovery_system_signal
import subprocess as _deployment_recovery_system_subprocess
import sys as _deployment_recovery_system_sys
import tempfile as _deployment_recovery_system_tempfile
import threading as _deployment_recovery_system_threading
import time as _deployment_recovery_system_time
from types import SimpleNamespace
import urllib.error
import urllib.request
from urllib.parse import urlsplit as _deployment_recovery_system_urlsplit
from unittest.mock import patch as _deployment_recovery_system_patch

_deployment_recovery_system_ROOT = _deployment_recovery_system_Path(__file__).resolve().parents[1]
LABEL = "bike.deployment.test"
PROFILE, TARGET = "uci-bike-sharing", "uci-bike-sharing-cnt-dataset-local-hour"
MODEL = "bike-demand-synthetic-recovery"
IMAGES = ("postgres:17.6-bookworm", "caddy:2.10.2-alpine")
HTTP_LOG = None
HTTP_LOCK = _deployment_recovery_system_threading.Lock()


def record_http(path, event):
    if path is not None:
        with HTTP_LOCK, _deployment_recovery_system_Path(path).open("a", encoding="utf-8") as output:
            output.write(_deployment_recovery_system_json.dumps(event, sort_keys=True) + "\n")


def docker(*args, env=None, timeout=60):
    result = _deployment_recovery_system_subprocess.run(["docker", *args], env=env, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"Disposable Docker {args[0]} failed (exit {result.returncode}); native details suppressed")
    return result.stdout.strip()


def _http(url, payload=None):
    data = None if payload is None else _deployment_recovery_system_json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, _deployment_recovery_system_json.loads(response.read())
    except urllib.error.HTTPError as error:
        body = error.read()
        try:
            body = _deployment_recovery_system_json.loads(body)
        except ValueError:
            body = body.decode(errors="replace")
        return error.code, body


def http(url, payload=None):
    try:
        code, body = _http(url, payload)
    except Exception as error:
        record_http(HTTP_LOG, dict(url=url, payload=payload, error_type=type(error).__name__))
        raise
    record_http(HTTP_LOG, dict(url=url, payload=payload, status=code, body=body))
    return code, body


def wait(url):
    until = _deployment_recovery_system_time.monotonic() + 30
    while _deployment_recovery_system_time.monotonic() < until:
        try:
            code, body = http(url)
            if code == 200:
                return body
        except (OSError, ValueError):
            pass
        _deployment_recovery_system_time.sleep(.2)
    raise RuntimeError("Disposable endpoint readiness timeout")


def digest(path):
    return _deployment_recovery_system_hashlib.sha256(path.read_bytes()).hexdigest()


class ConstantModel:
    def __init__(self, value):
        self.value = value

    def predict(self, frame):
        return [self.value] * len(frame)


def read_model(root, version):
    spec = _deployment_recovery_system_json.loads((root / "model-specs.json").read_text())[version]
    path = root / "artifacts" / ("v" + version + ".json")
    if digest(path) != spec["sha256"]:
        raise ValueError("Synthetic artifact digest mismatch")
    return ConstantModel(_deployment_recovery_system_json.loads(path.read_text())["constant"]), spec["sha256"], "passed"


class Fixture:
    def __init__(self, spec):
        self.spec = spec
        self.root = _deployment_recovery_system_Path(spec["workspace"]).resolve()
        if (not re.fullmatch(r"[a-f0-9]{32}", spec.get("token", ""))
                or not re.fullmatch(r"bike-deploy-test-[a-f0-9]{16}", spec.get("prefix", ""))):
            raise ValueError("Invalid disposable fixture identity")
        if self.root == _deployment_recovery_system_ROOT or _deployment_recovery_system_ROOT in self.root.parents:
            raise ValueError("Disposable workspace must be outside the real project")
        if (self.root / ".fixture-token").read_text() != spec["token"]:
            raise ValueError("Disposable workspace marker does not match")
        self.runtime = self.root / "data/runtime"

    def owned(self, kind, name):
        args = ("inspect",) if kind == "container" else (kind, "inspect")
        selector = "{{json .Config.Labels}}" if kind == "container" else "{{json .Labels}}"
        labels = _deployment_recovery_system_json.loads(docker(*args, "--format", selector, name)) or {}
        if labels.get(LABEL) != self.spec["token"]:
            raise RuntimeError("Refusing operation on a resource outside this fixture")

    def verified_image(self):
        image = self.spec.get("image_id", "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
            raise ValueError("Missing disposable image identity")
        data = _deployment_recovery_system_json.loads(docker("image", "inspect", image))[0]
        if data["Id"] != image or data["Config"].get("Labels", {}).get(LABEL) != self.spec["token"]:
            raise RuntimeError("Refusing unowned fixture image")
        return image

    def api(self, slot, version):
        endpoint = _deployment_recovery_system_urlsplit(self.spec["internal_database_url"])
        if (endpoint.scheme != "postgresql" or endpoint.hostname not in {
                self.spec["prefix"] + "-postgres", self.spec["prefix"] + "-restore"}
                or endpoint.port != 5432 or endpoint.path != "/postgres"
                or endpoint.username != "postgres" or not endpoint.password or endpoint.query or endpoint.fragment):
            raise ValueError("API database endpoint is outside the disposable fixture")
        self.owned("container", endpoint.hostname)
        if slot not in {"blue", "green", "restored"} or version not in {"1", "2", "3"}:
            raise ValueError("Unexpected fixture API identity")
        name = self.spec["prefix"] + "-api-" + slot
        if docker("ps", "-a", "-q", "--filter", "name=^/" + name + "$"):
            self.owned("container", name)
            if self.spec.get("evidence_dir"):
                previous = _deployment_recovery_system_subprocess.run(["docker", "logs", name], capture_output=True, timeout=15)
                _deployment_recovery_system_Path(self.spec["evidence_dir"], slot + "-replaced-" + str(_deployment_recovery_system_time.monotonic_ns()) + ".log").write_bytes(previous.stdout + previous.stderr)
            docker("rm", "-f", "-v", name)
        env = _deployment_recovery_system_os.environ.copy()
        env["DATABASE_URL"] = self.spec["internal_database_url"]
        port = self.spec["ports"].get("api-" + slot, "")
        docker("run", "-d", "--pull=never", "--name", name, "--label", f"{LABEL}={self.spec['token']}",
               "--network", self.spec["prefix"], "--network-alias", "api-" + slot,
               "--publish", f"127.0.0.1:{port}:8000", "--mount", f"type=bind,src={self.root},dst=/workspace,readonly",
               "-e", "DATABASE_URL", "-e", "PYTHONPATH=/workspace/src:/workspace", "-e", "DATA_PROFILE_ID=" + PROFILE,
               "-e", "MLFLOW_MODEL_NAME=" + MODEL, "-e", "MODEL_VERSION=" + version,
               "-e", "DEPLOYMENT_ID=" + self.spec.get("operation_ids", {}).get(slot, ""),
               "-e", "DEPLOYMENT_SLOT=" + slot,
               self.verified_image(), "uvicorn", "fixture_api:app", "--host", "0.0.0.0", "--port", "8000", env=env)
        bound = docker("port", name, "8000/tcp")
        if not bound.startswith("127.0.0.1:") or "\n" in bound:
            raise RuntimeError("API did not bind only to loopback")
        return int(bound.rsplit(":", 1)[1])

    def compose(self, *args, **kwargs):
        if len(args) == 5 and args[:4] == ("up", "-d", "--build", "--no-deps") and args[-1] in {"api-blue", "api-green"}:
            slot = args[-1].split("-", 1)[1]
            values = dict(line.split("=", 1) for line in (self.runtime / (slot + ".env")).read_text().splitlines())
            self.spec.setdefault("operation_ids", {})[slot] = values.get("DEPLOYMENT_ID", "")
            self.api(slot, values["MODEL_VERSION"])
        elif args[:4] == ("exec", "-T", "caddy", "caddy"):
            name = self.spec["prefix"] + "-caddy"
            self.owned("container", name)
            docker("exec", name, *args[3:])
        else:
            raise RuntimeError("Unexpected Compose command in disposable fixture")
        return SimpleNamespace(returncode=0)

    def patches(self):
        if __package__:
            from .system_harness import check_local_daemon
        else:
            from system_harness import check_local_daemon
        check_local_daemon()
        endpoint = _deployment_recovery_system_urlsplit(self.spec["database_url"])
        if (endpoint.scheme != "postgresql" or endpoint.hostname != "127.0.0.1" or endpoint.port is None
                or endpoint.path != "/postgres" or endpoint.username != "postgres" or not endpoint.password
                or endpoint.query or endpoint.fragment):
            raise ValueError("Host database endpoint is outside the disposable fixture")
        pg = self.spec["prefix"] + "-postgres"
        self.owned("container", pg)
        if docker("port", pg, "5432/tcp") != f"127.0.0.1:{endpoint.port}":
            raise ValueError("Host database port does not belong to this fixture")
        _deployment_recovery_system_os.environ["DATA_PROFILE_ID"] = PROFILE
        from bike_mlops import cli
        from bike_mlops.db import Database
        stack = ExitStack()
        db = Database(self.spec["database_url"], PROFILE, TARGET)
        specs = _deployment_recovery_system_json.loads((self.root / "model-specs.json").read_text())
        registry = SimpleNamespace(get_model_version=lambda name, version: SimpleNamespace(
            name=name, version=str(version), status="READY", tags={"quality_gate": "passed",
                "artifact_sha256": specs[str(version)]["sha256"], "data_profile_id": PROFILE, "target_contract_id": TARGET}))
        ports = dict(cli.DATA_PROFILE["runtime_ports"], api_blue=self.spec["ports"]["api-blue"],
                     api_green=self.spec["ports"]["api-green"], proxy=self.spec["ports"]["caddy"])
        for key, value in dict(RUNTIME_DIR=self.runtime, PROJECT_ROOT=self.root,
                DEFAULT_DATASET=self.root / "data/raw/hour.csv", DATA_PROFILE_ID=PROFILE,
                TARGET_CONTRACT_ID=TARGET, MODEL_NAME=MODEL, DATA_PROFILE=dict(cli.DATA_PROFILE, runtime_ports=ports)).items():
            stack.enter_context(_deployment_recovery_system_patch.object(cli, key, value))
        stack.enter_context(_deployment_recovery_system_patch.object(cli, "_database", return_value=db))
        stack.enter_context(_deployment_recovery_system_patch.object(cli, "_model_registry_client", return_value=registry))
        stack.enter_context(_deployment_recovery_system_patch.object(cli, "_compose", side_effect=self.compose))
        original_url = cli._url_json
        def observed_url(url, payload=None, timeout=5.0):
            try:
                result = original_url(url, payload, timeout)
            except Exception as error:
                record_http(self.spec.get("http_log"), dict(url=url, payload=payload,
                    error_type=type(error).__name__, status=getattr(error, "code", None)))
                raise
            record_http(self.spec.get("http_log"), dict(url=url, payload=payload, returned_json=result))
            return result
        stack.enter_context(_deployment_recovery_system_patch.object(cli, "_url_json", side_effect=observed_url))
        stack.enter_context(_deployment_recovery_system_patch("bike_mlops.model.load_registered_model", side_effect=lambda name, version: read_model(self.root, str(version))))
        return stack, cli, db


def worker(path, stage):
    spec = _deployment_recovery_system_json.loads(path.read_text())
    spec["http_log"] = str(_deployment_recovery_system_Path(spec["evidence_dir"]) / (stage + "-cli-http.jsonl"))
    fixture = Fixture(spec)
    stack, cli, _db = fixture.patches()
    original = cli._deployment_row
    def checkpoint(database, operation, phase, *args, **kwargs):
        def stop():
            path.with_name("checkpoint.txt").write_text(stage)
            _deployment_recovery_system_os.kill(_deployment_recovery_system_os.getpid(), _deployment_recovery_system_signal.SIGKILL)
        if phase == "completed" and stage == "before_completed":
            stop()
        result = original(database, operation, phase, *args, **kwargs)
        if phase == stage:
            stop()
        return result
    with stack, _deployment_recovery_system_patch.object(cli, "_deployment_row", side_effect=checkpoint):
        cli._promote("3", "synthetic-worker", "interruption at " + stage, False, False)
    raise AssertionError("Worker did not reach the intended SIGKILL checkpoint")


def _deployment_recovery_system_main(evidence, term_after_startup=False):
    global HTTP_LOG
    if __package__:
        from .system_harness import check_local_daemon, copy_workspace
    else:
        from system_harness import check_local_daemon, copy_workspace
    import pandas as pd
    import psycopg
    from bike_mlops.db import Database
    check_local_daemon()
    for image in IMAGES:
        docker("image", "inspect", image, "--format", "{{.Id}}")
    evidence.mkdir(parents=True, exist_ok=False)
    HTTP_LOG = evidence / "http-observations.jsonl"
    token, prefix, password = secrets.token_hex(16), "bike-deploy-test-" + secrets.token_hex(8), secrets.token_hex(24)
    protected = lambda: sorted(x for x in docker("ps", "-a", "--format", "{{.Names}} {{.ID}}").splitlines() if x.startswith("bike-mlops-"))
    before = protected()
    summary = dict(synthetic_only=True, model_registry_boundary="constant JSON model/registry doubles",
                   real_API_PostgreSQL_Caddy=True, real_Capital_evidence=False, cleanup_complete=False)
    containers, volumes, volume, network = [], [], prefix + "-pg-data", False
    image_tag = prefix + ":current"
    image_id = None
    build_started = build_finished = False
    old_handlers = {sig: _deployment_recovery_system_signal.getsignal(sig) for sig in (_deployment_recovery_system_signal.SIGTERM, _deployment_recovery_system_signal.SIGINT)}
    def interrupted(sig, frame):
        summary["interrupted_signal"] = _deployment_recovery_system_signal.Signals(sig).name
        raise KeyboardInterrupt("Disposable deployment harness interrupted")
    for sig in old_handlers:
        _deployment_recovery_system_signal.signal(sig, interrupted)
    fixture = None
    try:
        with _deployment_recovery_system_tempfile.TemporaryDirectory(prefix=prefix) as directory:
            root = _deployment_recovery_system_Path(directory)
            (root / ".fixture-token").write_text(token)
            fixture = Fixture(dict(workspace=str(root), prefix=prefix, token=token, ports={}))
            copy_workspace(_deployment_recovery_system_ROOT, root)
            build_started = True
            docker("build", "--label", f"{LABEL}={token}", "-t", image_tag, str(root), timeout=600)
            image_id = docker("image", "inspect", image_tag, "--format", "{{.Id}}")
            fixture.spec["image_id"] = image_id
            fixture.verified_image()
            build_finished = True
            summary.update(current_Dockerfile_build=True, image_id=image_id,
                           Dockerfile_sha256=digest(root / "Dockerfile"))
            shutil.copyfile(_deployment_recovery_system_Path(__file__), root / "fixture_models.py")
            (root / "fixture_api.py").write_text("import os\nfrom pathlib import Path\nfrom bike_mlops import api\nfrom fixture_models import read_model\ndef runtime():\n v=os.environ['MODEL_VERSION']\n try:\n  model,h,gate=read_model(Path('/workspace'),v)\n  return model,v,h,gate,None\n except Exception as e:return None,v,None,None,str(e)\napi._runtime_model=runtime\napp=api.app\n")
            runtime, raw, artifacts = root / "data/runtime", root / "data/raw", root / "artifacts"
            for folder in (runtime, raw, artifacts):
                folder.mkdir(parents=True)
            stamps = pd.date_range("2012-01-01", periods=200, freq="h")
            pd.DataFrame(dict(dteday=stamps.strftime("%Y-%m-%d"), hr=stamps.hour, cnt=50)).to_csv(raw / "hour.csv", index=False)
            specs = {}
            for v in ("1", "2", "3"):
                p = artifacts / ("v" + v + ".json")
                p.write_text(_deployment_recovery_system_json.dumps(dict(constant=int(v) * 100)))
                specs[v] = dict(sha256=digest(p))
            (root / "model-specs.json").write_text(_deployment_recovery_system_json.dumps(specs))
            docker("network", "create", "--label", f"{LABEL}={token}", prefix)
            network = True
            if docker("volume", "ls", "-q", "--filter", "name=^" + volume + "$"):
                raise RuntimeError("Disposable volume identity already exists; refusing reuse")
            docker("volume", "create", "--label", f"{LABEL}={token}", volume)
            volumes.append(volume)
            fixture.owned("volume", volume)
            pg = prefix + "-postgres"
            containers.append(pg)
            env = _deployment_recovery_system_os.environ.copy()
            env["POSTGRES_PASSWORD"] = password
            docker("run", "-d", "--pull=never", "--name", pg, "--label", f"{LABEL}={token}", "--network", prefix,
                   "--publish", "127.0.0.1::5432", "--mount", f"type=volume,src={volume},dst=/var/lib/postgresql/data",
                   "-e", "POSTGRES_PASSWORD", IMAGES[0], env=env)
            bound = docker("port", pg, "5432/tcp")
            if not bound.startswith("127.0.0.1:"):
                raise RuntimeError("PostgreSQL did not bind to loopback")
            url = f"postgresql://postgres:{password}@127.0.0.1:{bound.rsplit(':', 1)[1]}/postgres"
            until = _deployment_recovery_system_time.monotonic() + 30
            while True:
                try:
                    with psycopg.connect(url) as c:
                        c.execute("SELECT 1")
                    break
                except psycopg.OperationalError:
                    if _deployment_recovery_system_time.monotonic() > until:
                        raise RuntimeError("Disposable PostgreSQL startup timeout") from None
                    _deployment_recovery_system_time.sleep(.2)
            Database(url, PROFILE, TARGET).initialize()
            state = dict(active_slot="blue", active_version="2", data_profile_id=PROFILE, target_contract_id=TARGET,
                         deployment_mode="approved", previous_versions=["1"], last_deployment_id="fixture-bootstrap",
                         approver="synthetic-bootstrap", reason="synthetic bootstrap")
            (runtime / "deployment-state.json").write_text(_deployment_recovery_system_json.dumps(state))
            for slot in ("blue", "green"):
                (runtime / (slot + ".env")).write_text("MODEL_VERSION=2\n")
            (runtime / "Caddyfile").write_text(":8080 {\n reverse_proxy api-blue:8000\n}\n")
            spec = dict(workspace=str(root), prefix=prefix, token=token, image_id=image_id, database_url=url,
                        internal_database_url=f"postgresql://postgres:{password}@{pg}:5432/postgres", ports={},
                        evidence_dir=str(evidence), http_log=str(evidence / "cli-http.jsonl"))
            fixture = Fixture(spec)
            for slot in ("blue", "green"):
                containers.append(prefix + "-api-" + slot)
                spec["ports"]["api-" + slot] = fixture.api(slot, "2")
                wait(f"http://127.0.0.1:{spec['ports']['api-' + slot]}/ready")
            caddy = prefix + "-caddy"
            containers.append(caddy)
            docker("run", "-d", "--pull=never", "--name", caddy, "--label", f"{LABEL}={token}", "--network", prefix,
                   "--publish", "127.0.0.1::8080", "--mount", f"type=bind,src={runtime},dst=/runtime,readonly",
                   IMAGES[1], "caddy", "run", "--config", "/runtime/Caddyfile", "--adapter", "caddyfile")
            bound = docker("port", caddy, "8080/tcp")
            if not bound.startswith("127.0.0.1:"):
                raise RuntimeError("Caddy did not bind to loopback")
            spec["ports"]["caddy"] = int(bound.rsplit(":", 1)[1])
            proxy = f"http://127.0.0.1:{spec['ports']['caddy']}"
            wait(proxy + "/ready")
            if term_after_startup:
                _deployment_recovery_system_os.kill(_deployment_recovery_system_os.getpid(), _deployment_recovery_system_signal.SIGTERM)
                raise AssertionError("TERM was ignored")
            spec_path = root / "worker-spec.json"
            spec_path.write_text(_deployment_recovery_system_json.dumps(spec))
            stack, cli, db = fixture.patches()
            def state_now():
                return _deployment_recovery_system_json.loads((runtime / "deployment-state.json").read_text())
            def rows(table):
                assert table in {"predictions", "deployments"}
                with db.session() as c:
                    return [dict(x) for x in c.execute("SELECT * FROM " + table + " ORDER BY 1")]
            def confirm(v):
                assert wait(proxy + "/ready")["model_version"] == v
                assert http(proxy + "/version")[1]["artifact_sha256"] == specs[v]["sha256"]
                state = state_now()
                assert state["active_version"] == v
                if "deployment_operation" in state:
                    identity = http(proxy + "/version")[1]["deployment_operation"]
                    row = next(x for x in rows("deployments") if x["deployment_id"] == state["last_deployment_id"])
                    from bike_mlops.deployment import operation_identity
                    assert identity["status"] == "matched_completed"
                    assert identity["operation"] == state["deployment_operation"] == operation_identity(row, specs[v]["sha256"])
                    assert not identity["approval_evidence"]
            with stack:
                cli._seed_observations(db, raw / "hour.csv")
                body = dict(request_id="synthetic-concurrent-0001", target_hour="2012-01-09T00:00:00", as_of="2012-01-09T00:00:00", feature_contract_version="bike-demand-features-v1")
                with _deployment_recovery_system_ThreadPoolExecutor(max_workers=4) as pool:
                    replies = list(pool.map(lambda _: http(proxy + "/predict", body), range(4)))
                assert all(x == replies[0] and x[0] == 200 for x in replies) and len(rows("predictions")) == 1
                first = replies[0][1]
                assert first["model_version"] == "2" and first["prediction"] == 200
                changed = dict(body, target_hour="2012-01-09T01:00:00", as_of="2012-01-09T01:00:00")
                assert http(proxy + "/predict", changed)[0] == 409
                summary["parallel_same_id_and_changed_409"] = "passed"
                promoted = cli._promote("3", "synthetic-operator-3", "synthetic promotion", False, False)
                (evidence / "promotion.json").write_text(_deployment_recovery_system_json.dumps(promoted, indent=2) + "\n")
                confirm("3")
                assert state_now()["approver"] == "synthetic-operator-3"
                assert http(proxy + "/predict", body) == (200, first)
                summary["real_proxy_switch_original_retry_metadata"] = "passed"
                with db.transaction() as c:
                    c.execute("ALTER TABLE predictions ADD CONSTRAINT synthetic_no_audits CHECK (FALSE) NOT VALID")
                count = len(rows("predictions"))
                assert http(proxy + "/predict", dict(body, request_id="synthetic-write-fail-0001"))[0] == 503
                assert len(rows("predictions")) == count
                with db.transaction() as c:
                    c.execute("ALTER TABLE predictions DROP CONSTRAINT synthetic_no_audits")
                summary["postgres_write_failure_503_no_saved_row"] = "passed"
                cli._promote("2", "synthetic-rollback", "synthetic rollback", True, False)
                confirm("2")
                artifact = artifacts / "v3.json"
                original = artifact.read_bytes()
                artifact.write_text('{"constant":999}')
                try:
                    try:
                        original_wait = cli._wait_ready
                        # This case deliberately never becomes ready; use a short
                        # refusal fixture only here, not for normal API startup.
                        with _deployment_recovery_system_patch.object(cli, "_wait_ready", side_effect=lambda url, version, gate: original_wait(url, version, gate, timeout_seconds=5)):
                            cli._promote("3", "synthetic-operator", "corrupt artifact", False, False)
                    except TimeoutError:
                        pass
                    else:
                        raise AssertionError("Corrupt fixture artifact was promoted")
                finally:
                    artifact.write_bytes(original)
                confirm("2")
                summary["corrupt_artifact_ready_refusal_old_proxy_retained"] = "passed"
                cases = []
                for stage in ("candidate_ready", "proxy_switched", "before_completed"):
                    if state_now()["active_version"] == "3":
                        cli._promote("2", "synthetic-reset", "reset between interruption cases", True, False)
                    child = _deployment_recovery_system_subprocess.run([_deployment_recovery_system_sys.executable, str(_deployment_recovery_system_Path(__file__).resolve()), "deployment-recovery", "--worker", str(spec_path), "--stop-at", stage], capture_output=True, timeout=60)
                    (evidence / (stage + "-worker.log")).write_bytes(child.stdout + child.stderr)
                    assert child.returncode == -_deployment_recovery_system_signal.SIGKILL, (stage, child.returncode)
                    assert spec_path.with_name("checkpoint.txt").read_text() == stage
                    shutil.copyfile(spec_path.with_name("checkpoint.txt"), evidence / (stage + "-checkpoint.txt"))
                    pending = [x for x in rows("deployments") if x["status"] == "in_progress"]
                    assert len(pending) == 1
                    operation = pending[0]["deployment_id"]
                    cli._recover_deployment()
                    expected = "2" if stage == "candidate_ready" else "3"
                    confirm(expected)
                    resolved = next(x for x in rows("deployments") if x["deployment_id"] == operation)
                    assert resolved["status"] == ("failed" if expected == "2" else "completed")
                    if expected == "3":
                        assert state_now()["last_deployment_id"] == operation and state_now()["approver"] == "synthetic-worker"
                        assert state_now()["reason"] == "interruption at " + stage
                    assert http(proxy + "/predict", body) == (200, first)
                    cases.append(dict(stage=stage, child_exit=child.returncode, active_version=expected, operation=operation, journal_status=resolved["status"]))
                summary["real_sigkill_cases"] = cases
                # Detection/recovery of this fixture's active API; never a user service.
                active_name = prefix + "-api-" + state_now()["active_slot"]
                fixture.owned("container", active_name)
                docker("stop", "--time", "1", active_name)
                assert http(proxy + "/ready")[0] in {502, 503}
                docker("start", active_name)
                confirm("3")
                summary["owned_api_stop_detected_and_restarted"] = "passed"
                registry = cli._model_registry_client()
                registered = registry.get_model_version(MODEL, 2)
                registered.tags["quality_gate"] = "failed"
                bad_registry = SimpleNamespace(get_model_version=lambda *_: registered)
                before_count = len(rows("deployments"))
                with _deployment_recovery_system_patch.object(cli, "_model_registry_client", return_value=bad_registry):
                    try:
                        cli._promote("2", "synthetic-operator", "failed gate", False, False)
                    except ValueError:
                        pass
                    else:
                        raise AssertionError("Failed fixture gate was promoted")
                assert len(rows("deployments")) == before_count
                confirm("3")
                summary["failed_gate_rejected_without_journal_write"] = "passed"

                # Quiescent paired snapshot: all tables plus exact fixture artifacts.
                tables = ("predictions", "observations", "deployments", "monitor_windows", "data_profile_metadata")
                def snapshot(database):
                    with database.session() as c:
                        return {table: [dict(x) for x in c.execute("SELECT * FROM " + table + " ORDER BY 1")] for table in tables}
                saved = snapshot(db)
                (evidence / "original-table-snapshot.json").write_text(_deployment_recovery_system_json.dumps(saved, indent=2) + "\n")
                (evidence / "final-state.json").write_text(_deployment_recovery_system_json.dumps(state_now(), indent=2) + "\n")
                dumped = _deployment_recovery_system_subprocess.run(["docker", "exec", pg, "pg_dump", "-U", "postgres", "-d", "postgres", "-Fc"],
                                        capture_output=True, timeout=30)
                assert dumped.returncode == 0
                (evidence / "synthetic.dump").write_bytes(dumped.stdout)
                shutil.copyfile(root / "model-specs.json", evidence / "model-specs.json")
                for v in specs:
                    shutil.copyfile(artifacts / ("v" + v + ".json"), evidence / ("v" + v + ".json"))
                restore_volume, restore_name = prefix + "-restore-data", prefix + "-restore"
                if docker("volume", "ls", "-q", "--filter", "name=^" + restore_volume + "$"):
                    raise RuntimeError("Restore volume already exists; refusing reuse")
                docker("volume", "create", "--label", f"{LABEL}={token}", restore_volume)
                volumes.append(restore_volume)
                fixture.owned("volume", restore_volume)
                containers.append(restore_name)
                docker("run", "-d", "--pull=never", "--name", restore_name, "--label", f"{LABEL}={token}",
                       "--network", prefix, "--publish", "127.0.0.1::5432",
                       "--mount", f"type=volume,src={restore_volume},dst=/var/lib/postgresql/data",
                       "-e", "POSTGRES_PASSWORD", IMAGES[0], env=env)
                bound = docker("port", restore_name, "5432/tcp")
                if not bound.startswith("127.0.0.1:"):
                    raise RuntimeError("Restore PostgreSQL did not bind to loopback")
                restore_url = f"postgresql://postgres:{password}@127.0.0.1:{bound.rsplit(':', 1)[1]}/postgres"
                until = _deployment_recovery_system_time.monotonic() + 30
                while True:
                    try:
                        with psycopg.connect(restore_url) as c:
                            c.execute("SELECT 1")
                        break
                    except psycopg.OperationalError:
                        if _deployment_recovery_system_time.monotonic() > until:
                            raise RuntimeError("Restore PostgreSQL startup timeout") from None
                        _deployment_recovery_system_time.sleep(.2)
                restored = _deployment_recovery_system_subprocess.run(["docker", "exec", "-i", restore_name, "pg_restore", "-U", "postgres", "-d", "postgres", "--exit-on-error"],
                                          input=dumped.stdout, capture_output=True, timeout=30)
                assert restored.returncode == 0
                restore_db = Database(restore_url, PROFILE, TARGET)
                assert snapshot(restore_db) == saved
                (evidence / "restored-table-snapshot.json").write_text(_deployment_recovery_system_json.dumps(snapshot(restore_db), indent=2) + "\n")
                restore_root = root / "restored-workspace"
                restore_root.mkdir()
                (restore_root / ".fixture-token").write_text(token)
                for folder in ("src", "configs"):
                    shutil.copytree(root / folder, restore_root / folder)
                for filename in ("fixture_api.py", "fixture_models.py"):
                    shutil.copyfile(root / filename, restore_root / filename)
                shutil.copyfile(evidence / "model-specs.json", restore_root / "model-specs.json")
                (restore_root / "artifacts").mkdir()
                for v in specs:
                    filename = "v" + v + ".json"
                    shutil.copyfile(evidence / filename, restore_root / "artifacts" / filename)
                    assert digest(restore_root / "artifacts" / filename) == specs[v]["sha256"]
                restore_spec = dict(spec, workspace=str(restore_root),
                                    internal_database_url=f"postgresql://postgres:{password}@{restore_name}:5432/postgres")
                containers.append(prefix + "-api-restored")
                restore_spec["operation_ids"] = {}
                restore_port = Fixture(restore_spec).api("restored", "3")
                restore_api = f"http://127.0.0.1:{restore_port}"
                wait(restore_api + "/ready")
                assert http(restore_api + "/predict", body) == (200, first)
                assert snapshot(restore_db) == saved
                summary["paired_snapshot_restore"] = dict(status="passed", separate_volume=True,
                    tables=list(tables), prediction_count=len(saved["predictions"]),
                    dump_sha256=digest(evidence / "synthetic.dump"), restored_original_version="2")
                summary["status"] = "passed"
    except BaseException as error:
        summary.update(status="failed", error_type=type(error).__name__)
        if (build_started and not build_finished) or isinstance(error, _deployment_recovery_system_subprocess.TimeoutExpired):
            summary["daemon_outcome_unconfirmed"] = True
        raise
    finally:
        for sig in old_handlers:
            _deployment_recovery_system_signal.signal(sig, _deployment_recovery_system_signal.SIG_IGN)
        errors = []
        for name in reversed(containers):
            try:
                if docker("ps", "-a", "-q", "--filter", "name=^/" + name + "$"):
                    fixture.owned("container", name)
                    log = _deployment_recovery_system_subprocess.run(["docker", "logs", name], capture_output=True, timeout=15)
                    (evidence / (name[len(prefix) + 1:] + ".log")).write_bytes(log.stdout + log.stderr)
                    docker("rm", "-f", "-v", name)
            except BaseException as error:
                errors.append(type(error).__name__ + ":container")
        for owned_volume in reversed(volumes):
            try:
                fixture.owned("volume", owned_volume)
                docker("volume", "rm", owned_volume)
            except BaseException as error:
                errors.append(type(error).__name__ + ":volume")
        if network:
            try:
                fixture.owned("network", prefix)
                docker("network", "rm", prefix)
            except BaseException as error:
                errors.append(type(error).__name__ + ":network")
        if image_id is None:
            image_id = docker("images", "-q", "--no-trunc", "--filter", f"label={LABEL}={token}") or None
        if image_id:
            try:
                fixture.spec["image_id"] = image_id
                fixture.verified_image()
                docker("image", "rm", image_tag)
            except BaseException as error:
                errors.append(type(error).__name__ + ":image")
        remaining = {kind: docker(*args, "--filter", f"label={LABEL}={token}") for kind, args in (
            ("containers", ("ps", "-a", "-q")), ("volumes", ("volume", "ls", "-q")), ("networks", ("network", "ls", "-q")))}
        remaining["images"] = docker("images", "-q", "--filter", f"label={LABEL}={token}")
        summary.update(cleanup_errors=errors, resources_remaining=remaining, protected_container_ids_unchanged=before == protected(),
                       cleanup_complete=not errors and not any(remaining.values()) and not summary.get("daemon_outcome_unconfirmed", False))
        (evidence / "summary.json").write_text(_deployment_recovery_system_json.dumps(summary, indent=2) + "\n")
        print(_deployment_recovery_system_json.dumps(summary), flush=True)
        for sig, handler in old_handlers.items():
            _deployment_recovery_system_signal.signal(sig, handler)
        if not summary["cleanup_complete"] or not summary["protected_container_ids_unchanged"]:
            raise RuntimeError("Disposable cleanup or protected-container comparison failed")


def _entry_test_deployment_recovery_system():
    parser = _deployment_recovery_system_argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run-disposable", action="store_true")
    mode.add_argument("--worker", type=_deployment_recovery_system_Path)
    parser.add_argument("--stop-at", choices=("candidate_ready", "proxy_switched", "before_completed"))
    parser.add_argument("--evidence-dir", type=_deployment_recovery_system_Path)
    parser.add_argument("--term-after-startup", action="store_true")
    args = parser.parse_args()
    if args.worker:
        worker(args.worker, args.stop_at)
    elif args.evidence_dir:
        _deployment_recovery_system_main(args.evidence_dir.resolve(), args.term_after_startup)
    else:
        parser.error("--evidence-dir is required")


def _dispatch_cli():
    import sys as _dispatch_sys
    commands = {'decision-http': _entry_test_capital_decision_http, 'deployment-recovery': _entry_test_deployment_recovery_system}
    if len(_dispatch_sys.argv) < 2 or _dispatch_sys.argv[1] in {"-h", "--help"}:
        print("Usage: runtime_checks.py <command> [options]\nCommands: decision-http, deployment-recovery")
        return 0
    command = _dispatch_sys.argv.pop(1)
    if command not in commands:
        raise SystemExit("Unknown command: " + command)
    return commands[command]()


if __name__ == "__main__":
    raise SystemExit(_dispatch_cli())
