"""Opt-in synthetic UCI deployment recovery with real API, PostgreSQL and Caddy.

Model and registry are explicit doubles. Only UUID-labelled disposable resources,
random loopback ports and an allowlisted workspace copy are used. No real registry.
Requires cached postgres:17.6-bookworm, caddy:2.10.2-alpine and bike-mlops-api-blue.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
LABEL = "bike.deployment.test"
PROFILE, TARGET = "uci-bike-sharing", "uci-bike-sharing-cnt-dataset-local-hour"
MODEL = "bike-demand-synthetic-recovery"
IMAGES = ("postgres:17.6-bookworm", "caddy:2.10.2-alpine", "bike-mlops-api-blue")
HTTP_LOG = None
HTTP_LOCK = threading.Lock()


def record_http(path, event):
    if path is not None:
        with HTTP_LOCK, Path(path).open("a", encoding="utf-8") as output:
            output.write(json.dumps(event, sort_keys=True) + "\n")


def docker(*args, env=None, timeout=60):
    result = subprocess.run(["docker", *args], env=env, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"Disposable Docker {args[0]} failed (exit {result.returncode}); native details suppressed")
    return result.stdout.strip()


def _http(url, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        body = error.read()
        try:
            body = json.loads(body)
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
    until = time.monotonic() + 30
    while time.monotonic() < until:
        try:
            code, body = http(url)
            if code == 200:
                return body
        except (OSError, ValueError):
            pass
        time.sleep(.2)
    raise RuntimeError("Disposable endpoint readiness timeout")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ConstantModel:
    def __init__(self, value):
        self.value = value

    def predict(self, frame):
        return [self.value] * len(frame)


def read_model(root, version):
    spec = json.loads((root / "model-specs.json").read_text())[version]
    path = root / "artifacts" / ("v" + version + ".json")
    if digest(path) != spec["sha256"]:
        raise ValueError("Synthetic artifact digest mismatch")
    return ConstantModel(json.loads(path.read_text())["constant"]), spec["sha256"], "passed"


class Fixture:
    def __init__(self, spec):
        self.spec = spec
        self.root = Path(spec["workspace"]).resolve()
        if (not re.fullmatch(r"[a-f0-9]{32}", spec.get("token", ""))
                or not re.fullmatch(r"bike-deploy-test-[a-f0-9]{16}", spec.get("prefix", ""))):
            raise ValueError("Invalid disposable fixture identity")
        if self.root == ROOT or ROOT in self.root.parents:
            raise ValueError("Disposable workspace must be outside the real project")
        if (self.root / ".fixture-token").read_text() != spec["token"]:
            raise ValueError("Disposable workspace marker does not match")
        self.runtime = self.root / "data/runtime"

    def owned(self, kind, name):
        args = ("inspect",) if kind == "container" else (kind, "inspect")
        selector = "{{json .Config.Labels}}" if kind == "container" else "{{json .Labels}}"
        labels = json.loads(docker(*args, "--format", selector, name)) or {}
        if labels.get(LABEL) != self.spec["token"]:
            raise RuntimeError("Refusing operation on a resource outside this fixture")

    def api(self, slot, version):
        endpoint = urlsplit(self.spec["internal_database_url"])
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
            docker("rm", "-f", "-v", name)
        env = os.environ.copy()
        env["DATABASE_URL"] = self.spec["internal_database_url"]
        port = self.spec["ports"].get("api-" + slot, "")
        docker("run", "-d", "--pull=never", "--name", name, "--label", f"{LABEL}={self.spec['token']}",
               "--network", self.spec["prefix"], "--network-alias", "api-" + slot,
               "--publish", f"127.0.0.1:{port}:8000", "--mount", f"type=bind,src={self.root},dst=/workspace,readonly",
               "-e", "DATABASE_URL", "-e", "PYTHONPATH=/workspace/src:/workspace", "-e", "DATA_PROFILE_ID=" + PROFILE,
               "-e", "MLFLOW_MODEL_NAME=" + MODEL, "-e", "MODEL_VERSION=" + version,
               IMAGES[2], "uvicorn", "fixture_api:app", "--host", "0.0.0.0", "--port", "8000", env=env)
        bound = docker("port", name, "8000/tcp")
        if not bound.startswith("127.0.0.1:") or "\n" in bound:
            raise RuntimeError("API did not bind only to loopback")
        return int(bound.rsplit(":", 1)[1])

    def compose(self, *args, **kwargs):
        if len(args) == 5 and args[:4] == ("up", "-d", "--build", "--no-deps") and args[-1] in {"api-blue", "api-green"}:
            slot = args[-1].split("-", 1)[1]
            self.api(slot, (self.runtime / (slot + ".env")).read_text().strip().split("=", 1)[1])
        elif args[:4] == ("exec", "-T", "caddy", "caddy"):
            name = self.spec["prefix"] + "-caddy"
            self.owned("container", name)
            docker("exec", name, *args[3:])
        else:
            raise RuntimeError("Unexpected Compose command in disposable fixture")
        return SimpleNamespace(returncode=0)

    def patches(self):
        if __package__:
            from .capital_system_harness import check_local_daemon
        else:
            from capital_system_harness import check_local_daemon
        check_local_daemon()
        endpoint = urlsplit(self.spec["database_url"])
        if (endpoint.scheme != "postgresql" or endpoint.hostname != "127.0.0.1" or endpoint.port is None
                or endpoint.path != "/postgres" or endpoint.username != "postgres" or not endpoint.password
                or endpoint.query or endpoint.fragment):
            raise ValueError("Host database endpoint is outside the disposable fixture")
        pg = self.spec["prefix"] + "-postgres"
        self.owned("container", pg)
        if docker("port", pg, "5432/tcp") != f"127.0.0.1:{endpoint.port}":
            raise ValueError("Host database port does not belong to this fixture")
        os.environ["DATA_PROFILE_ID"] = PROFILE
        from bike_mlops import cli
        from bike_mlops.db import Database
        stack = ExitStack()
        db = Database(self.spec["database_url"], PROFILE, TARGET)
        specs = json.loads((self.root / "model-specs.json").read_text())
        registry = SimpleNamespace(get_model_version=lambda name, version: SimpleNamespace(
            name=name, version=str(version), status="READY", tags={"quality_gate": "passed",
                "artifact_sha256": specs[str(version)]["sha256"], "data_profile_id": PROFILE, "target_contract_id": TARGET}))
        ports = dict(cli.DATA_PROFILE["runtime_ports"], api_blue=self.spec["ports"]["api-blue"],
                     api_green=self.spec["ports"]["api-green"], proxy=self.spec["ports"]["caddy"])
        for key, value in dict(RUNTIME_DIR=self.runtime, PROJECT_ROOT=self.root,
                DEFAULT_DATASET=self.root / "data/raw/hour.csv", DATA_PROFILE_ID=PROFILE,
                TARGET_CONTRACT_ID=TARGET, MODEL_NAME=MODEL, DATA_PROFILE=dict(cli.DATA_PROFILE, runtime_ports=ports)).items():
            stack.enter_context(patch.object(cli, key, value))
        stack.enter_context(patch.object(cli, "_database", return_value=db))
        stack.enter_context(patch.object(cli, "_model_registry_client", return_value=registry))
        stack.enter_context(patch.object(cli, "_compose", side_effect=self.compose))
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
        stack.enter_context(patch.object(cli, "_url_json", side_effect=observed_url))
        stack.enter_context(patch("bike_mlops.model.load_registered_model", side_effect=lambda name, version: read_model(self.root, str(version))))
        original_wait = cli._wait_ready
        stack.enter_context(patch.object(cli, "_wait_ready", side_effect=lambda url, version, gate: original_wait(url, version, gate, timeout_seconds=5)))
        return stack, cli, db


def worker(path, stage):
    spec = json.loads(path.read_text())
    spec["http_log"] = str(Path(spec["evidence_dir"]) / (stage + "-cli-http.jsonl"))
    fixture = Fixture(spec)
    stack, cli, _db = fixture.patches()
    original = cli._deployment_row
    def checkpoint(database, operation, phase, *args, **kwargs):
        def stop():
            path.with_name("checkpoint.txt").write_text(stage)
            os.kill(os.getpid(), signal.SIGKILL)
        if phase == "completed" and stage == "before_completed":
            stop()
        result = original(database, operation, phase, *args, **kwargs)
        if phase == stage:
            stop()
        return result
    with stack, patch.object(cli, "_deployment_row", side_effect=checkpoint):
        cli._promote("3", "synthetic-worker", "interruption at " + stage, False, False)
    raise AssertionError("Worker did not reach the intended SIGKILL checkpoint")


def main(evidence):
    global HTTP_LOG
    from capital_system_harness import check_local_daemon, copy_workspace
    import pandas as pd
    import psycopg
    import tzdata
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
    fixture = None
    try:
        with tempfile.TemporaryDirectory(prefix=prefix) as directory:
            root = Path(directory)
            (root / ".fixture-token").write_text(token)
            fixture = Fixture(dict(workspace=str(root), prefix=prefix, token=token, ports={}))
            copy_workspace(ROOT, root)
            # The installed UCI image predates the project's tzdata addition.
            # Supply this pure data/Python package from the locked host env only
            # in the disposable copy; no image/venv/network install is performed.
            if f"tzdata=={tzdata.__version__}" not in (ROOT / "requirements.lock").read_text().splitlines():
                raise RuntimeError("Host tzdata does not match the project lock")
            shutil.copytree(Path(tzdata.__file__).resolve().parent, root / "tzdata",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            summary["fixture_tzdata_version"] = tzdata.__version__
            summary["cached_image_is_not_a_current_Dockerfile_build_test"] = True
            shutil.copyfile(Path(__file__), root / "fixture_models.py")
            (root / "fixture_api.py").write_text("import os\nfrom pathlib import Path\nfrom bike_mlops import api\nfrom fixture_models import read_model\ndef runtime():\n v=os.environ['MODEL_VERSION']\n try:\n  model,h,gate=read_model(Path('/workspace'),v)\n  return model,v,h,gate,None\n except Exception as e:return None,v,None,None,str(e)\napi._runtime_model=runtime\napp=api.app\n")
            runtime, raw, artifacts = root / "data/runtime", root / "data/raw", root / "artifacts"
            for folder in (runtime, raw, artifacts):
                folder.mkdir(parents=True)
            stamps = pd.date_range("2012-01-01", periods=200, freq="h")
            pd.DataFrame(dict(dteday=stamps.strftime("%Y-%m-%d"), hr=stamps.hour, cnt=50)).to_csv(raw / "hour.csv", index=False)
            specs = {}
            for v in ("1", "2", "3"):
                p = artifacts / ("v" + v + ".json")
                p.write_text(json.dumps(dict(constant=int(v) * 100)))
                specs[v] = dict(sha256=digest(p))
            (root / "model-specs.json").write_text(json.dumps(specs))
            docker("network", "create", "--label", f"{LABEL}={token}", prefix)
            network = True
            if docker("volume", "ls", "-q", "--filter", "name=^" + volume + "$"):
                raise RuntimeError("Disposable volume identity already exists; refusing reuse")
            docker("volume", "create", "--label", f"{LABEL}={token}", volume)
            volumes.append(volume)
            fixture.owned("volume", volume)
            pg = prefix + "-postgres"
            containers.append(pg)
            env = os.environ.copy()
            env["POSTGRES_PASSWORD"] = password
            docker("run", "-d", "--pull=never", "--name", pg, "--label", f"{LABEL}={token}", "--network", prefix,
                   "--publish", "127.0.0.1::5432", "--mount", f"type=volume,src={volume},dst=/var/lib/postgresql/data",
                   "-e", "POSTGRES_PASSWORD", IMAGES[0], env=env)
            bound = docker("port", pg, "5432/tcp")
            if not bound.startswith("127.0.0.1:"):
                raise RuntimeError("PostgreSQL did not bind to loopback")
            url = f"postgresql://postgres:{password}@127.0.0.1:{bound.rsplit(':', 1)[1]}/postgres"
            until = time.monotonic() + 30
            while True:
                try:
                    with psycopg.connect(url) as c:
                        c.execute("SELECT 1")
                    break
                except psycopg.OperationalError:
                    if time.monotonic() > until:
                        raise RuntimeError("Disposable PostgreSQL startup timeout") from None
                    time.sleep(.2)
            Database(url, PROFILE, TARGET).initialize()
            state = dict(active_slot="blue", active_version="2", data_profile_id=PROFILE, target_contract_id=TARGET,
                         deployment_mode="approved", previous_versions=["1"], last_deployment_id="fixture-bootstrap",
                         approver="synthetic-bootstrap", reason="synthetic bootstrap")
            (runtime / "deployment-state.json").write_text(json.dumps(state))
            for slot in ("blue", "green"):
                (runtime / (slot + ".env")).write_text("MODEL_VERSION=2\n")
            (runtime / "Caddyfile").write_text(":8080 {\n reverse_proxy api-blue:8000\n}\n")
            spec = dict(workspace=str(root), prefix=prefix, token=token, database_url=url,
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
            spec_path = root / "worker-spec.json"
            spec_path.write_text(json.dumps(spec))
            stack, cli, db = fixture.patches()
            def state_now():
                return json.loads((runtime / "deployment-state.json").read_text())
            def rows(table):
                assert table in {"predictions", "deployments"}
                with db.session() as c:
                    return [dict(x) for x in c.execute("SELECT * FROM " + table + " ORDER BY 1")]
            def confirm(v):
                assert wait(proxy + "/ready")["model_version"] == v
                assert http(proxy + "/version")[1]["artifact_sha256"] == specs[v]["sha256"]
                assert state_now()["active_version"] == v
            with stack:
                cli._seed_observations(db, raw / "hour.csv")
                body = dict(request_id="synthetic-concurrent-0001", target_hour="2012-01-09T00:00:00", as_of="2012-01-09T00:00:00", feature_contract_version="bike-demand-features-v1")
                with ThreadPoolExecutor(max_workers=4) as pool:
                    replies = list(pool.map(lambda _: http(proxy + "/predict", body), range(4)))
                assert all(x == replies[0] and x[0] == 200 for x in replies) and len(rows("predictions")) == 1
                first = replies[0][1]
                assert first["model_version"] == "2" and first["prediction"] == 200
                changed = dict(body, target_hour="2012-01-09T01:00:00", as_of="2012-01-09T01:00:00")
                assert http(proxy + "/predict", changed)[0] == 409
                summary["parallel_same_id_and_changed_409"] = "passed"
                promoted = cli._promote("3", "synthetic-operator-3", "synthetic promotion", False, False)
                (evidence / "promotion.json").write_text(json.dumps(promoted, indent=2) + "\n")
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
                    child = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", str(spec_path), "--stop-at", stage], capture_output=True, timeout=60)
                    (evidence / (stage + "-worker.log")).write_bytes(child.stdout + child.stderr)
                    assert child.returncode == -signal.SIGKILL, (stage, child.returncode)
                    assert spec_path.with_name("checkpoint.txt").read_text() == stage
                    shutil.copyfile(spec_path.with_name("checkpoint.txt"), evidence / (stage + "-checkpoint.txt"))
                    pending = [x for x in rows("deployments") if x["status"] == "in_progress"]
                    assert len(pending) == 1
                    operation = pending[0]["deployment_id"]
                    cli.command_deploy_recover(SimpleNamespace())
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
                with patch.object(cli, "_model_registry_client", return_value=bad_registry):
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
                (evidence / "original-table-snapshot.json").write_text(json.dumps(saved, indent=2) + "\n")
                (evidence / "final-state.json").write_text(json.dumps(state_now(), indent=2) + "\n")
                dumped = subprocess.run(["docker", "exec", pg, "pg_dump", "-U", "postgres", "-d", "postgres", "-Fc"],
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
                until = time.monotonic() + 30
                while True:
                    try:
                        with psycopg.connect(restore_url) as c:
                            c.execute("SELECT 1")
                        break
                    except psycopg.OperationalError:
                        if time.monotonic() > until:
                            raise RuntimeError("Restore PostgreSQL startup timeout") from None
                        time.sleep(.2)
                restored = subprocess.run(["docker", "exec", "-i", restore_name, "pg_restore", "-U", "postgres", "-d", "postgres", "--exit-on-error"],
                                          input=dumped.stdout, capture_output=True, timeout=30)
                assert restored.returncode == 0
                restore_db = Database(restore_url, PROFILE, TARGET)
                assert snapshot(restore_db) == saved
                (evidence / "restored-table-snapshot.json").write_text(json.dumps(snapshot(restore_db), indent=2) + "\n")
                restore_root = root / "restored-workspace"
                restore_root.mkdir()
                (restore_root / ".fixture-token").write_text(token)
                for folder in ("src", "configs", "tzdata"):
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
        raise
    finally:
        errors = []
        for name in reversed(containers):
            try:
                if docker("ps", "-a", "-q", "--filter", "name=^/" + name + "$"):
                    fixture.owned("container", name)
                    log = subprocess.run(["docker", "logs", name], capture_output=True, timeout=15)
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
        remaining = {kind: docker(*args, "--filter", f"label={LABEL}={token}") for kind, args in (
            ("containers", ("ps", "-a", "-q")), ("volumes", ("volume", "ls", "-q")), ("networks", ("network", "ls", "-q")))}
        summary.update(cleanup_errors=errors, resources_remaining=remaining, protected_container_ids_unchanged=before == protected(),
                       cleanup_complete=not errors and not any(remaining.values()))
        (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary), flush=True)
        if not summary["cleanup_complete"] or not summary["protected_container_ids_unchanged"]:
            raise RuntimeError("Disposable cleanup or protected-container comparison failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run-disposable", action="store_true")
    mode.add_argument("--worker", type=Path)
    parser.add_argument("--stop-at", choices=("candidate_ready", "proxy_switched", "before_completed"))
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker, args.stop_at)
    elif args.evidence_dir:
        main(args.evidence_dir.resolve())
    else:
        parser.error("--evidence-dir is required")
