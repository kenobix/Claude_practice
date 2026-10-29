"""Disposable infrastructure foundation for the future Capital system test.

It can prepare a synthetic candidate, but does not register it, start the
staging API, send predictions or claim that the operational gate passed.
Infrastructure startup is opt-in.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import secrets
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
PORTS = (5001, 8110, 55433, 55434)
LABEL = "bike.mlops.harness"
SERVICES = {"postgres", "mlflow", "postgres-staging"}


class HarnessError(RuntimeError):
    pass


def docker(*args: str, env: dict | None = None, timeout: int = 90,
           progress: str | None = None, progress_interval: float = 20) -> str:
    if progress not in {None, "build_and_start"} or progress_interval <= 0:
        raise ValueError("Invalid progress reporting configuration")
    done = threading.Event()
    began = time.monotonic()
    worker = None
    if progress:
        def heartbeat():
            while not done.wait(progress_interval):
                print(json.dumps({"status": "in_progress", "stage": progress,
                                  "elapsed_seconds": int(time.monotonic() - began)}), flush=True)
        worker = threading.Thread(target=heartbeat, daemon=True)
        worker.start()
    try:
        result = subprocess.run(["docker", *args], env=env, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HarnessError(f"Docker operation {args[0]} could not finish ({type(error).__name__}); "
                           "underlying cause not determined") from error
    finally:
        done.set()
        if worker is not None:
            worker.join()
    if result.returncode:
        # Docker/Compose errors can contain connection strings and env values.
        raise HarnessError(f"Docker operation {args[0]} failed (exit {result.returncode}); "
                           "details suppressed; underlying cause not determined")
    return result.stdout.strip()


def check_local_daemon() -> None:
    context = docker("context", "show")
    host = json.loads(docker("context", "inspect", context, "--format", "{{json .Endpoints.docker.Host}}"))
    explicit = os.environ.get("DOCKER_HOST")
    if not isinstance(host, str) or not host.startswith("unix://") or (explicit and not explicit.startswith("unix://")):
        raise HarnessError("A local Unix-socket Docker daemon is required")


def check_ports_free(ports=PORTS) -> None:
    for port in ports:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", port))
        except OSError as error:
            raise HarnessError(f"Required local port {port} is unavailable; existing services were not stopped") from error


def copy_workspace(source: Path, destination: Path) -> None:
    """Copy only required build/Compose inputs, never user data or .env."""
    files = ("Dockerfile", ".dockerignore", "README.md", "pyproject.toml", "requirements.lock",
             "compose.yaml", "compose.capital.yaml", "compose.capital-staging.yaml")
    directories = ("src", "configs/data-profiles", "docker")
    def manifest(root: Path) -> dict[str, str]:
        selected = [root / name for name in files]
        for name in directories:
            folder = root / name
            if folder.is_symlink() or not folder.is_dir():
                raise HarnessError(f"Required source directory is missing or a symlink: {name}")
            for current, dirs, filenames in os.walk(folder, followlinks=False):
                dirs[:] = [item for item in dirs if item not in {"__pycache__", ".env"}
                           and not any(fnmatch.fnmatch(item, pattern) for pattern in ("*.pyc", "*.sqlite", "*.db"))]
                for item in dirs + filenames:
                    if (Path(current) / item).is_symlink():
                        raise HarnessError(f"Symlinks are not permitted in copied source directory: {name}")
                selected.extend(Path(current) / item for item in filenames
                                if item != ".env" and not any(fnmatch.fnmatch(item, pattern)
                                                             for pattern in ("*.pyc", "*.sqlite", "*.db")))
        result = {}
        for path in selected:
            if path.is_symlink() or not path.is_file():
                raise HarnessError(f"Required source file is missing or a symlink: {path.relative_to(root)}")
            with path.open("rb") as stream:
                result[path.relative_to(root).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
        return result

    before = manifest(source)
    for name in files:
        src = source / name
        if src.is_symlink() or not src.is_file():
            raise HarnessError(f"Required source file is missing or a symlink: {name}")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    for name in directories:
        src = source / name
        if src.is_symlink() or not src.is_dir():
            raise HarnessError(f"Required source directory is missing or a symlink: {name}")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".env", "*.sqlite", "*.db"))
    if manifest(source) != before or manifest(destination) != before:
        raise HarnessError("Source files changed while preparing the isolated workspace")


def compose_environment() -> dict:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("COMPOSE_") or key.startswith("APP_DB_") or key in {"POSTGRES_ADMIN_PASSWORD", "MLFLOW_DB_PASSWORD"}:
            env.pop(key, None)
    return env


def prepare_synthetic_workspace(root: Path) -> dict:
    """Create synthetic candidate evidence inside the already isolated workspace."""
    root = root.resolve()
    if (root.parent != Path(tempfile.gettempdir()).resolve()
            or not root.name.startswith(("capital-synthetic-test-", "capital-system-harness-"))):
        raise HarnessError("Synthetic data requires a disposable workspace")
    (root / ".synthetic-fixture-owned").write_text("synthetic only\n", encoding="utf-8")
    scripts = root / "scripts"
    scripts.mkdir()
    for name in ("test_capital_synthetic_fixture.py", "prepare_capitalbikeshare.py", "evaluate_capitalbikeshare_holdout.py"):
        source = SOURCE_ROOT / "scripts" / name
        if source.is_symlink() or not source.is_file():
            raise HarnessError(f"Synthetic fixture script missing or symlinked: {name}")
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        shutil.copy2(source, scripts / name)
        if (source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest() != before
                or hashlib.sha256((scripts / name).read_bytes()).hexdigest() != before):
            raise HarnessError(f"Synthetic fixture script changed while copying: {name}")
    environment = {"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8", "DATA_PROFILE_ID": "capital-bikeshare",
                   "MLFLOW_DISABLE_AGENT_HINT": "1", "PYTHONPATH": os.pathsep.join((str(root / "src"), str(root)))}
    result = subprocess.run([sys.executable, str(scripts / "test_capital_synthetic_fixture.py"), "--internal-child"],
                            cwd=root, env=environment, capture_output=True, text=True, timeout=240)
    if result.returncode:
        raise HarnessError(f"Synthetic candidate subprocess failed (exit {result.returncode}); no real data was used")
    summary = json.loads((root / "synthetic-fixture-result.json").read_text(encoding="utf-8"))
    if summary.get("status") != "passed" or summary.get("data") != "synthetic":
        raise HarnessError("Synthetic candidate summary is invalid")
    return summary


def make_override(destination: Path, token: str) -> Path:
    labels = {LABEL: token}
    override = {
        "services": {
            "postgres": {"labels": labels},
            "postgres-staging": {"labels": labels},
            "mlflow": {"labels": labels, "build": {"labels": labels}},
        },
        "volumes": {name: {"labels": labels} for name in (
            "postgres-data", "mlflow-artifacts", "capital-staging-postgres-data")},
        "networks": {"default": {"labels": labels}},
    }
    path = destination / "harness-override.json"
    path.write_text(json.dumps(override), encoding="utf-8")
    return path


class Harness:
    def __init__(self, root: Path, project: str, token: str):
        if not re.fullmatch(r"bikemlopstest[a-f0-9]{16}", project) or not re.fullmatch(r"[a-f0-9]{32}", token):
            raise ValueError("Invalid disposable project identity")
        self.root, self.project, self.token = root, project, token
        self.env = compose_environment()
        self.compose = ["compose", "-p", project, "--project-directory", str(root), "--env-file", str(root / "harness.env"),
                        "-f", str(root / "compose.yaml"), "-f", str(root / "compose.capital.yaml"),
                        "-f", str(root / "compose.capital-staging.yaml"), "-f", str(root / "harness-override.json")]
        self.image = f"{project}-mlflow:latest"

    def resources(self):
        project_filter = f"label=com.docker.compose.project={self.project}"
        return {
            "container": docker("ps", "-a", "-q", "--no-trunc", "--filter", project_filter).splitlines(),
            "network": docker("network", "ls", "-q", "--no-trunc", "--filter", project_filter).splitlines(),
            "volume": docker("volume", "ls", "-q", "--filter", project_filter).splitlines(),
        }

    def assert_owned(self, resources: dict) -> None:
        for kind, values in resources.items():
            for value in values:
                command = ("inspect",) if kind == "container" else (kind, "inspect")
                selector = "{{json .Config.Labels}}" if kind == "container" else "{{json .Labels}}"
                labels = json.loads(docker(*command, "--format", selector, value)) or {}
                if labels.get("com.docker.compose.project") != self.project or labels.get(LABEL) != self.token:
                    raise HarnessError(f"Refusing cleanup: {kind} is not owned by this harness")
                if kind == "container" and labels.get("com.docker.compose.service") not in SERVICES:
                    raise HarnessError("Refusing cleanup: unexpected Compose service")

    def assert_empty(self) -> None:
        if any(self.resources().values()) or docker("image", "ls", "-q", self.image):
            raise HarnessError("Disposable project identity already exists; refusing to reuse it")

    def verify_ports(self) -> None:
        for service, internal, expected in (("postgres", "5432/tcp", 55433), ("postgres-staging", "5432/tcp", 55434),
                                             ("mlflow", "5000/tcp", 5001)):
            ids = docker(*self.compose, "ps", "-q", service, env=self.env).splitlines()
            if len(ids) != 1:
                raise HarnessError("Expected exactly one container per infrastructure service")
            self.assert_owned({"container": ids})
            if docker("port", ids[0], internal) != f"127.0.0.1:{expected}":
                raise HarnessError("Published port is not the expected loopback binding")

    def cleanup(self) -> None:
        resources = self.resources()
        self.assert_owned(resources)
        if any(resources.values()):
            docker(*self.compose, "down", "--volumes", env=self.env, timeout=120)
        ids = docker("image", "ls", "-q", self.image).splitlines()
        if ids:
            if len(ids) != 1:
                raise HarnessError("Refusing cleanup: unexpected image identity")
            labels = json.loads(docker("image", "inspect", "--format", "{{json .Config.Labels}}", ids[0])) or {}
            if labels.get(LABEL) != self.token:
                raise HarnessError("Refusing cleanup: image is not owned by this harness")
            docker("image", "rm", self.image)


def write_synthetic_credentials(root: Path) -> None:
    path = root / "harness.env"
    password = secrets.token_hex(24)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        for name in ("POSTGRES_ADMIN_PASSWORD", "MLFLOW_DB_PASSWORD", "APP_DB_PASSWORD"):
            stream.write(f"{name}={password}\n")


def wait_ready() -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    until = time.monotonic() + 45
    while time.monotonic() < until:
        try:
            with opener.open("http://127.0.0.1:5001/health", timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.5)
    raise HarnessError("Disposable MLflow did not become ready")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-infrastructure", action="store_true", help="Build and briefly start disposable DB and MLflow")
    parser.add_argument("--prepare-synthetic", action="store_true", help="Generate a synthetic evaluated candidate in the temporary workspace")
    args = parser.parse_args()
    project = "bikemlopstest" + secrets.token_hex(8)
    token = secrets.token_hex(16)
    root = Path(tempfile.mkdtemp(prefix="capital-system-harness-"))
    cleanup_required = False
    preserve = False
    stage = "prepare_workspace"
    old_handlers = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}

    def interrupt_on_signal(signum, _frame):
        print(json.dumps({"status": "interrupted", "cause": signal.Signals(signum).name}), flush=True)
        raise KeyboardInterrupt(f"Harness received {signal.Signals(signum).name}")

    for signum in old_handlers:
        signal.signal(signum, interrupt_on_signal)
    try:
        copy_workspace(SOURCE_ROOT, root)
        make_override(root, token)
        write_synthetic_credentials(root)
        harness = Harness(root, project, token)
        if args.prepare_synthetic:
            stage = "synthetic_candidate"
            print(json.dumps(prepare_synthetic_workspace(root), ensure_ascii=False), flush=True)
        if not args.run_infrastructure:
            print(json.dumps({"status": "workspace_prepared", "candidate_generation": "prepared" if args.prepare_synthetic else "not_implemented",
                              "prediction_requests": 0, "docker_started": False}))
            return
        stage = "preflight"
        check_local_daemon()
        check_ports_free()
        harness.assert_empty()
        print(json.dumps({"status": "starting_disposable_infrastructure", "project": project,
                          "workspace": str(root)}), flush=True)
        stage = "compose_config"
        docker(*harness.compose, "config", "-q", env=harness.env)
        cleanup_required = True  # A failed or interrupted `up` may still have created resources.
        stage = "build_and_start"
        docker(*harness.compose, "up", "-d", "--build", "postgres", "mlflow", "postgres-staging",
               env=harness.env, timeout=600, progress="build_and_start")
        stage = "verify_ports"
        harness.verify_ports()
        stage = "wait_mlflow"
        wait_ready()
        stage = "verify_ports"
        harness.verify_ports()
        print(json.dumps({"status": "infrastructure_ready", "candidate_generation": "prepared" if args.prepare_synthetic else "not_implemented",
                          "api_started": False, "prediction_requests": 0,
                          "operational_gate": "not_evaluated", "project": project}))
    except HarnessError as error:
        cause = "required_port_unavailable" if str(error).startswith("Required local port ") else "not_determined"
        print(json.dumps({"status": "failed", "stage": stage, "cause": cause,
                          "diagnostic_output": "suppressed"}), flush=True)
        raise
    finally:
        try:
            if cleanup_required:
                try:
                    harness.cleanup()
                    print(json.dumps({"status": "cleanup_complete", "project": project}), flush=True)
                except BaseException as error:
                    preserve = True
                    print(f"Cleanup failed; isolated workspace retained at {root}; project {project}; "
                          f"error type {type(error).__name__}; underlying cause not determined", file=sys.stderr)
                    raise
            if not preserve:
                shutil.rmtree(root)
        finally:
            for signum, handler in old_handlers.items():
                signal.signal(signum, handler)


if __name__ == "__main__":
    main()
