"""Explicit command tools: system_harness."""
from __future__ import annotations


# ========================================================================
# capital_system_harness
# ========================================================================

"""Run isolated synthetic Capital checks without touching the user's project data."""


import argparse
import errno
import fnmatch
import hashlib
import json
import math
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
SERVICES = {"postgres", "mlflow", "postgres-staging", "api-staging"}


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
        if args[:2] == ("image", "rm"):
            details = safe_child_diagnostic(result.stdout, result.stderr, env or {})
            raise HarnessError(f"Docker operation image removal failed (exit {result.returncode}); {details}")
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
            if error.errno == errno.EADDRINUSE:
                raise HarnessError(f"Required local port {port} is already in use; existing services were not stopped") from error
            raise HarnessError(f"Could not check local port {port} (OS error {error.errno}); existing services were not stopped") from error


def unique_resource_ids(output: str) -> list[str]:
    """Deduplicate Docker list output before ownership inspection."""
    return list(dict.fromkeys(line.strip() for line in output.splitlines() if line.strip()))


def protected_container_ids() -> list[str]:
    return sorted(line for line in docker("ps", "-a", "--no-trunc", "--format", "{{.Names}} {{.ID}}").splitlines()
                  if line.startswith("bike-mlops-"))


def parse_docker_image_records(output: str) -> list[dict]:
    records = []
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise HarnessError("Docker image list returned invalid JSON") from error
        if not isinstance(record, dict) or not isinstance(record.get("ID"), str) or not record["ID"]:
            raise HarnessError("Docker image list returned an invalid image record")
        records.append(record)
    return records


def unique_owned_image_references(records: list[dict], project: str) -> list[str]:
    """Return project-scoped repository:tag refs, avoiding shared image IDs."""
    references = []
    for record in records:
        repository, tag = record.get("Repository"), record.get("Tag")
        if (repository is None or repository == "<none>") and (tag is None or tag == "<none>"):
            continue
        if not isinstance(repository, str) or not repository or not isinstance(tag, str) or not tag:
            raise HarnessError("Docker image list returned an incomplete image reference")
        if not repository.startswith(f"{project}-"):
            raise HarnessError("Docker image reference is outside this disposable project; refusing cleanup")
        reference = f"{repository}:{tag}"
        if any(character.isspace() for character in reference):
            raise HarnessError("Docker image list returned an invalid image reference")
        references.append(reference)
    return list(dict.fromkeys(references))


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
    for name in ('system_harness.py', 'data_tools.py'):
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
    result = subprocess.run([sys.executable, str(scripts / 'system_harness.py'), 'fixture', "--internal-child"],
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
            "api-staging": {"labels": labels, "build": {"labels": labels}},
        },
        "volumes": {name: {"labels": labels} for name in (
            "postgres-data", "mlflow-artifacts", "capital-staging-postgres-data")},
        "networks": {"default": {"labels": labels}},
    }
    path = destination / "harness-override.json"
    path.write_text(json.dumps(override), encoding="utf-8")
    return path


class Harness:
    def __init__(self, root: Path, project: str, token: str, ports=PORTS):
        if not re.fullmatch(r"bikemlopstest[a-f0-9]{16}", project) or not re.fullmatch(r"[a-f0-9]{32}", token):
            raise ValueError("Invalid disposable project identity")
        self.root, self.project, self.token = root, project, token
        if len(ports) != 4 or len(set(ports)) != 4 or any(type(p) is not int or not 0 < p < 65536 for p in ports):
            raise ValueError("Invalid disposable port map")
        self.ports = ports
        self.env = compose_environment()
        self.compose = ["compose", "-p", project, "--project-directory", str(root), "--env-file", str(root / "harness.env"),
                        "-f", str(root / "compose.yaml"), "-f", str(root / "compose.capital.yaml"),
                        "-f", str(root / "compose.capital-staging.yaml"), "-f", str(root / "harness-override.json")]

    def resources(self):
        project_filter = f"label=com.docker.compose.project={self.project}"
        return {
            "container": docker("ps", "-a", "-q", "--no-trunc", "--filter", project_filter).splitlines(),
            "network": docker("network", "ls", "-q", "--no-trunc", "--filter", project_filter).splitlines(),
            "volume": docker("volume", "ls", "-q", "--filter", project_filter).splitlines(),
            # Multiple Compose service tags can point to one identical image ID.
            # Passing duplicate IDs to `docker image rm` can make the repeated
            # removal fail after the first reference has already been removed.
            "image": unique_resource_ids(docker(
                "image", "ls", "-q", "--no-trunc", "--filter", f"label={LABEL}={self.token}"
            )),
        }

    def assert_owned(self, resources: dict) -> None:
        for kind, values in resources.items():
            for value in values:
                command = ("inspect",) if kind in {"container", "image"} else (kind, "inspect")
                selector = "{{json .Config.Labels}}" if kind in {"container", "image"} else "{{json .Labels}}"
                labels = json.loads(docker(*command, "--format", selector, value)) or {}
                if labels.get(LABEL) != self.token or (kind != "image" and labels.get("com.docker.compose.project") != self.project):
                    raise HarnessError(f"Refusing cleanup: {kind} is not owned by this harness")
                if kind == "container" and labels.get("com.docker.compose.service") not in SERVICES:
                    raise HarnessError("Refusing cleanup: unexpected Compose service")

    def assert_empty(self) -> None:
        if any(self.resources().values()):
            raise HarnessError("Disposable project identity already exists; refusing to reuse it")

    def verify_ports(self, *, require_staging_api: bool = False) -> None:
        mlflow, api, postgres, staging = self.ports
        expected_ports = [("postgres", "5432/tcp", postgres), ("postgres-staging", "5432/tcp", staging),
                          ("mlflow", "5000/tcp", mlflow)]
        if require_staging_api:
            expected_ports.append(("api-staging", "8000/tcp", api))
        for service, internal, expected in expected_ports:
            ids = docker(*self.compose, "ps", "-q", service, env=self.env).splitlines()
            if len(ids) != 1:
                raise HarnessError("Expected exactly one container per infrastructure service")
            self.assert_owned({"container": ids})
            if docker("port", ids[0], internal) != f"127.0.0.1:{expected}":
                raise HarnessError("Published port is not the expected loopback binding")

    def verify_profiles(self) -> None:
        ids = docker(*self.compose, "ps", "-q", "mlflow", env=self.env).splitlines()
        if len(ids) != 1:
            raise HarnessError("Expected exactly one isolated MLflow container")
        self.assert_owned({"container": ids})
        program = ('import json; from bike_mlops.config import DATA_PROFILE_ID, TARGET_CONTRACT_ID, MODEL_NAME; print(json.dumps([DATA_PROFILE_ID,TARGET_CONTRACT_ID,MODEL_NAME]))')
        for profile, target, model in (
            ("uci-bike-sharing", "uci-bike-sharing-cnt-dataset-local-hour", "bike-demand"),
            ("capital-bikeshare", "capital-trip-starts-hourly-v1", "bike-demand-capital"),
        ):
            result = docker("exec", "--env", f"DATA_PROFILE_ID={profile}", ids[0], "python", "-c", program)
            if json.loads(result) != [profile, target, model]:
                raise HarnessError("The current Docker image resolved an unexpected data profile")

    def cleanup(self) -> None:
        resources = self.resources()
        self.assert_owned(resources)
        if any(resources[kind] for kind in ("container", "network", "volume")):
            docker(*self.compose, "down", "--volumes", env=self.env, timeout=120)
        image_output = docker("image", "ls", "-a", "--no-trunc", "--format", "{{json .}}",
                              "--filter", f"label={LABEL}={self.token}")
        records = parse_docker_image_records(image_output)
        image_ids = unique_resource_ids("\n".join(record["ID"] for record in records))
        if image_ids:
            self.assert_owned({"image": image_ids})
            references = unique_owned_image_references(records, self.project)
            if references:
                docker("image", "rm", *references)
            tagged_ids = {record["ID"] for record in records
                          if (record.get("Repository") is not None and record.get("Repository") != "<none>")
                          or (record.get("Tag") is not None and record.get("Tag") != "<none>")}
            untagged_ids = unique_resource_ids("\n".join(
                record["ID"] for record in records if record["ID"] not in tagged_ids
            ))
            if untagged_ids:
                docker("image", "rm", *untagged_ids)
        if any(self.resources().values()):
            raise HarnessError("Disposable project resources remain after cleanup")


def write_synthetic_credentials(root: Path) -> None:
    path = root / "harness.env"
    password = secrets.token_hex(24)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        for name in ("POSTGRES_ADMIN_PASSWORD", "MLFLOW_DB_PASSWORD", "APP_DB_PASSWORD"):
            stream.write(f"{name}={password}\n")


def wait_ready(base_url="http://127.0.0.1:5001") -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    until = time.monotonic() + 45
    while time.monotonic() < until:
        try:
            with opener.open(base_url + "/health", timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.5)
    raise HarnessError("Disposable MLflow did not become ready")


def read_synthetic_credentials(root: Path) -> str:
    path = root / "harness.env"
    if path.stat().st_mode & 0o777 != 0o600:
        raise HarnessError("Synthetic credential file permissions are not restricted")
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if not separator or not key or not value or key in values:
            raise HarnessError("Synthetic credentials file has an invalid format")
        values[key] = value
    if set(values) != {"POSTGRES_ADMIN_PASSWORD", "MLFLOW_DB_PASSWORD", "APP_DB_PASSWORD"}:
        raise HarnessError("Synthetic credentials file has unexpected entries")
    if len(set(values.values())) != 1:
        raise HarnessError("Synthetic isolated credentials are inconsistent")
    return values["APP_DB_PASSWORD"]


def isolated_python_environment(root: Path, project: str, password: str, ports=PORTS) -> dict:
    return {
        "PATH": os.environ.get("PATH", ""),
        "LANG": "C.UTF-8",
        "PYTHONPATH": os.pathsep.join((str(root / "src"), str(root))),
        "PYTHONNOUSERSITE": "1",
        "DATA_PROFILE_ID": "capital-bikeshare",
        "MLFLOW_TRACKING_URI": f"http://127.0.0.1:{ports[0]}",
        "MLFLOW_REGISTRY_URI": f"http://127.0.0.1:{ports[0]}",
        "MLFLOW_DISABLE_AGENT_HINT": "1",
        "APP_DB_PASSWORD": password,
        "DATABASE_URL": f"postgresql://bike_app:{password}@127.0.0.1:{ports[3]}/bikeops",
        "CAPITAL_COMPOSE_PROJECT_NAME": project,
        "CAPITAL_COMPOSE_ENV_FILE": str(root / "harness.env"),
    }


def safe_child_diagnostic(stdout: str, stderr: str, env: dict) -> str:
    sensitive = [value for key, value in env.items()
                 if value and any(marker in key.upper() for marker in ("PASSWORD", "TOKEN", "SECRET"))]
    combined = f"stdout={stdout[-1200:]!r}; stderr={stderr[-1200:]!r}"
    for value in sensitive:
        combined = combined.replace(value, "[REDACTED]")
    return combined


def parse_json_object_with_log_prefix(output: str) -> dict:
    decoder = json.JSONDecoder()
    candidates = []
    for index, character in enumerate(output):
        if character != "{":
            continue
        try:
            value, consumed = decoder.raw_decode(output[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append((index + consumed, value))
    if not candidates:
        raise ValueError("No JSON object found")
    return max(candidates, key=lambda item: item[0])[1]


def run_isolated_python(root: Path, env: dict, stage: str, code: str, arguments: list[str] | None = None,
                        timeout: int = 90) -> dict:
    began = time.monotonic()
    print(json.dumps({"status": "stage_started", "stage": stage}), flush=True)
    try:
        result = subprocess.run([sys.executable, "-c", code, *(arguments or [])], cwd=root, env=env,
                                capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HarnessError(f"Synthetic system stage {stage} could not finish ({type(error).__name__})") from error
    if result.returncode:
        raise HarnessError(f"Synthetic system stage {stage} failed (exit {result.returncode}); "
                           f"{safe_child_diagnostic(result.stdout, result.stderr, env)}")
    try:
        value = parse_json_object_with_log_prefix(result.stdout)
    except ValueError as error:
        raise HarnessError(f"Synthetic system stage {stage} returned invalid JSON; "
                           f"{safe_child_diagnostic(result.stdout, result.stderr, env)}") from error
    if not isinstance(value, dict):
        raise HarnessError(f"Synthetic system stage {stage} returned an unexpected result")
    print(json.dumps({"status": "stage_completed", "stage": stage,
                      "elapsed_seconds": round(time.monotonic() - began, 3)}), flush=True)
    return value


def run_isolated_script(root: Path, env: dict, stage: str, script: str, arguments: list[str], timeout: int = 300) -> dict:
    path = SOURCE_ROOT / "scripts" / script
    began = time.monotonic()
    print(json.dumps({"status": "stage_started", "stage": stage}), flush=True)
    try:
        result = subprocess.run([sys.executable, str(path), *arguments], cwd=root, env=env,
                                capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HarnessError(f"Synthetic system stage {stage} could not finish ({type(error).__name__})") from error
    if result.returncode:
        raise HarnessError(f"Synthetic system stage {stage} failed (exit {result.returncode}); "
                           f"{safe_child_diagnostic(result.stdout, result.stderr, env)}")
    try:
        value = parse_json_object_with_log_prefix(result.stdout)
    except ValueError as error:
        raise HarnessError(f"Synthetic system stage {stage} returned invalid JSON; "
                           f"{safe_child_diagnostic(result.stdout, result.stderr, env)}") from error
    if not isinstance(value, dict):
        raise HarnessError(f"Synthetic system stage {stage} returned an unexpected result")
    print(json.dumps({"status": "stage_completed", "stage": stage,
                      "elapsed_seconds": round(time.monotonic() - began, 3)}), flush=True)
    return value


def run_expected_script_rejection(root: Path, env: dict, stage: str, script: str,
                                 arguments: list[str], expected_error: str) -> None:
    """Require a specific safe refusal without printing child diagnostics."""
    path = SOURCE_ROOT / "scripts" / script
    began = time.monotonic()
    print(json.dumps({"status": "stage_started", "stage": stage}), flush=True)
    try:
        result = subprocess.run([sys.executable, str(path), *arguments], cwd=root, env=env,
                                capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HarnessError(f"Synthetic system stage {stage} could not finish ({type(error).__name__})") from error
    if result.returncode == 0 or expected_error not in result.stderr:
        raise HarnessError(f"Synthetic system stage {stage} did not reject the intended mismatch")
    print(json.dumps({"status": "stage_completed", "stage": stage, "expected": "rejected",
                      "elapsed_seconds": round(time.monotonic() - began, 3)}), flush=True)


def request_json(url: str, payload: dict | None = None) -> tuple[int, dict]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"},
                                     method="GET" if payload is None else "POST")
    try:
        with opener.open(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read())
        except (ValueError, OSError):
            return error.code, {}
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        raise HarnessError(f"Synthetic staging API request failed ({type(error).__name__})") from error


def wait_staging_api(candidate_id: str, version: str, artifact_sha256: str,
                     base_url="http://127.0.0.1:8110") -> tuple[dict, dict]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    until = time.monotonic() + 90
    while time.monotonic() < until:
        try:
            with opener.open(base_url + "/ready", timeout=3) as response:
                status, ready = response.status, json.loads(response.read())
            if (status == 200 and ready.get("status") == "ready"
                    and ready.get("candidate_id") == candidate_id and str(ready.get("model_version")) == version
                    and ready.get("deployment_permission") == "blocked"):
                version_status, version_info = request_json(base_url + "/version")
                if (version_status == 200 and version_info.get("candidate_id") == candidate_id
                        and str(version_info.get("model_version")) == version
                        and version_info.get("artifact_sha256") == artifact_sha256
                        and version_info.get("deployment_permission") == "blocked"):
                    return ready, version_info
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            pass
        time.sleep(0.5)
    raise HarnessError("Disposable staging API did not become ready with the pinned candidate identity")


def run_synthetic_system(root: Path, harness: "Harness", synthetic: dict, project: str) -> dict:
    """Register, seed, serve and audit a candidate generated only from synthetic data."""
    password = read_synthetic_credentials(root)
    env = isolated_python_environment(root, project, password)
    candidate_id = synthetic["candidate_id"]
    selection = f"data/candidates/{candidate_id}/candidate-report.json"
    final = "data/reports/capital-bikeshare/synthetic-final.json"

    registered = run_isolated_script(root, env, "blocked registration", 'runtime_tools.py',
                                     ['register', "--selection-report", selection, "--final-report", final])
    registered_again = run_isolated_script(root, env, "idempotent registration", 'runtime_tools.py',
                                           ['register', "--selection-report", selection, "--final-report", final])
    version = str(registered.get("model_version", ""))
    if (not version.isdecimal() or version != str(registered_again.get("model_version"))
            or registered.get("candidate_id") != candidate_id
            or registered.get("deployment_permission") != "blocked"
            or registered.get("operational_gate_status") != "not_evaluated"
            or registered.get("registered_artifact_sha256") != registered_again.get("registered_artifact_sha256")):
        raise HarnessError("Blocked registration or idempotent rerun did not preserve the exact synthetic identity")

    env_path = root / "data/runtime-capital-staging/staging.env"
    values = dict(line.split("=", 1) for line in env_path.read_text(encoding="utf-8").splitlines())
    if (values.get("CAPITAL_STAGING_CANDIDATE_ID") != candidate_id
            or values.get("CAPITAL_STAGING_MODEL_VERSION") != version
            or values.get("CAPITAL_STAGING_REGISTERED_ARTIFACT_SHA256") != registered["registered_artifact_sha256"]):
        raise HarnessError("Generated staging environment does not pin the registered synthetic candidate")
    if any("PASSWORD" in key or "TOKEN" in key or "SECRET" in key for key in values):
        raise HarnessError("Generated staging identity unexpectedly contains credential fields")

    seed_args = ["--selection-report", selection, "--final-report", final, "--model-version", version]
    seeded = run_isolated_script(root, env, "staging database seed", 'runtime_tools.py', ['seed', *seed_args])
    verified = run_isolated_script(root, env, "staging database verification", 'runtime_tools.py',
                                   ['seed', *seed_args, "--verify-only"])
    if (seeded.get("new_observations_inserted") != synthetic["hourly_rows"]
            or verified.get("verified_rows") != synthetic["hourly_rows"]
            or seeded.get("candidate_id") != candidate_id or verified.get("candidate_id") != candidate_id):
        raise HarnessError("Staging database did not contain the complete candidate-bound synthetic snapshot")

    legacy_schema = run_isolated_python(root, env, "legacy precision migration setup", (
        "import json,os,psycopg; "
        "conn=psycopg.connect(os.environ['DATABASE_URL']); "
        "conn.execute('ALTER TABLE predictions ALTER COLUMN predicted TYPE REAL USING predicted::REAL'); "
        "conn.commit(); "
        "row=conn.execute(\"SELECT data_type FROM information_schema.columns WHERE table_schema=current_schema() "
        "AND table_name='predictions' AND column_name='predicted'\").fetchone(); conn.close(); "
        "print(json.dumps({'data_type':row[0]}))"
    ), timeout=60)
    if legacy_schema.get("data_type") != "real":
        raise HarnessError("Synthetic legacy database precision setup did not produce a REAL prediction column")

    docker(*harness.compose, "up", "-d", "--build", "api-staging", env=harness.env, timeout=600,
           progress="build_and_start")
    harness.verify_ports(require_staging_api=True)
    ready, _version_info = wait_staging_api(candidate_id, version, registered["registered_artifact_sha256"])
    migrated_schema = run_isolated_python(root, env, "prediction precision migration verification", (
        "import json,os,psycopg; "
        "conn=psycopg.connect(os.environ['DATABASE_URL']); "
        "row=conn.execute(\"SELECT data_type FROM information_schema.columns WHERE table_schema=current_schema() "
        "AND table_name='predictions' AND column_name='predicted'\").fetchone(); conn.close(); "
        "print(json.dumps({'data_type':row[0]}))"
    ), timeout=60)
    if migrated_schema.get("data_type") != "double precision":
        raise HarnessError("Staging API did not migrate a legacy prediction column to double precision")

    target = synthetic["measurement_start"]
    offline_code = (
        "import json,sys; from pathlib import Path; import pandas as pd; from bike_mlops.evaluation import load_candidate_model; from bike_mlops.cli import _load_profile_hourly; from bike_mlops.config import DEFAULT_DATASET; from bike_mlops.data import feature_frame_for_dataset; from bike_mlops.model import predict_non_negative; root=Path.cwd(); selection=json.loads((root/sys.argv[1]).read_text()); frame=_load_profile_hourly(DEFAULT_DATASET); stamp=pd.Timestamp(sys.argv[2]); indices=frame.index[frame.timestamp.eq(stamp)]; assert len(indices)==1; idx=indices[0]; model,sha=load_candidate_model(root/selection['candidate_bundle']/'model.skops',selection['candidate_artifact']['sha256']); value=float(predict_non_negative(model,feature_frame_for_dataset(frame).loc[[idx]])); print(json.dumps({'prediction':value,'candidate_id':selection['candidate_id'],'artifact_sha256':sha}))"
    )
    offline = run_isolated_python(root, env, "offline prediction", offline_code, [selection, target], timeout=90)
    prefix = "synthetic-system-" + secrets.token_hex(8)
    request_id = prefix + "-parity"
    payload = {"request_id": request_id, "target_hour": target, "as_of": target,
               "feature_contract_version": "bike-demand-features-v1",
               "data_profile_id": "capital-bikeshare", "target_contract_id": "capital-trip-starts-hourly-v1"}
    status, prediction = request_json("http://127.0.0.1:8110/predict", payload)
    try:
        prediction_value = float(prediction.get("prediction", float("nan")))
        offline_value = float(offline["prediction"])
        parity_delta = abs(prediction_value - offline_value)
    except (TypeError, ValueError, KeyError):
        prediction_value, offline_value, parity_delta = float("nan"), float("nan"), float("nan")
    parity_checks = {
        "http_200": status == 200,
        "api_candidate": prediction.get("candidate_id") == candidate_id,
        "api_version": prediction.get("model_version") == version,
        "api_blocked": prediction.get("deployment_permission") == "blocked",
        "api_artifact": prediction.get("artifact_sha256") == registered["registered_artifact_sha256"],
        "offline_candidate": offline.get("candidate_id") == candidate_id,
        "offline_artifact": offline.get("artifact_sha256") == registered["candidate_artifact_sha256"],
        "prediction_numeric": math.isfinite(prediction_value) and math.isfinite(offline_value),
        "prediction_parity": math.isfinite(parity_delta) and math.isclose(
            prediction_value, offline_value, rel_tol=1e-12, abs_tol=1e-12
        ),
    }
    if not all(parity_checks.values()):
        raise HarnessError("Synthetic parity checks failed: " + json.dumps({
            "checks": parity_checks, "api_prediction": prediction_value,
            "offline_prediction": offline_value, "absolute_delta": parity_delta,
        }, sort_keys=True))
    retry_status, retry = request_json("http://127.0.0.1:8110/predict", payload)
    changed = {**payload, "target_hour": "2024-01-07 23:00:00", "as_of": "2024-01-07 23:00:00"}
    conflict_status, _ = request_json("http://127.0.0.1:8110/predict", changed)
    if retry_status != 200 or retry != prediction or conflict_status != 409:
        raise HarnessError("Staging API did not preserve idempotent retry and changed-input conflict behavior")

    audit_code = (
        "import json,os; from datetime import datetime; "
        "from bike_mlops.config import DATA_PROFILE_ID,TARGET_CONTRACT_ID; from bike_mlops.db import Database; "
        "db=Database(os.environ['DATABASE_URL'],DATA_PROFILE_ID,TARGET_CONTRACT_ID); "
        f"rows=db.list_predictions(datetime.fromisoformat('2024-01-07 22:00:00'),datetime.fromisoformat('2024-01-07 23:00:00'),'{version}','{prefix}'); "
        f"assert len(rows)==1 and rows[0]['candidate_id']=='{candidate_id}' and rows[0]['artifact_sha256']=='{registered['registered_artifact_sha256']}'; "
        "print(json.dumps({'audit_rows':len(rows),'candidate_id':rows[0]['candidate_id']}))"
    )
    audit_result = run_isolated_python(root, env, "staging prediction audit", audit_code, timeout=60)
    if audit_result.get("audit_rows") != 1 or audit_result.get("candidate_id") != candidate_id:
        raise HarnessError("Staging database audit row count or candidate identity is wrong")

    runtime_rejections = []
    for diagnostic in (False, True):
        argv = ["mlops-bikectl", "runtime", "init", "--version", version,
                "--approver", "synthetic-system-test", "--reason", "verify blocked candidate rejection"]
        if diagnostic:
            argv.append("--diagnostic")
        code = "import sys; from bike_mlops.cli import main; sys.argv=" + repr(argv) + "; main()"
        rejected = subprocess.run([sys.executable, "-c", code], cwd=root, env=env,
                                  capture_output=True, text=True, timeout=60)
        if rejected.returncode == 0 or "not eligible for deployment" not in rejected.stderr:
            raise HarnessError("Normal runtime initialization accepted or misclassified a blocked synthetic candidate")
        runtime_rejections.append("diagnostic" if diagnostic else "normal")
    runtime_dir = root / "data/runtime-capital-bikeshare"
    if any((runtime_dir / name).exists() for name in ("deployment-state.json", "blue.env", "green.env", "Caddyfile")):
        raise HarnessError("Rejected runtime initialization left a deployable state behind")

    plan_path = "data/evaluations/capital-bikeshare/operational-plans/synthetic-system-plan.json"
    plan = run_isolated_script(root, env, "operational plan freeze", 'runtime_tools.py', ['plan',
        "--candidate-id", candidate_id, "--model-version", version,
        "--selection-report", selection, "--final-report", final,
        "--start", synthetic["measurement_start"], "--end", synthetic["measurement_end"],
        "--output", plan_path,
    ])
    if plan.get("status") != "plan_frozen_no_predictions_sent" or plan.get("candidate_id") != candidate_id:
        raise HarnessError("Synthetic operational plan was not frozen before prediction traffic")
    plan_resources = run_isolated_python(root, env, "plan resource verification", (
        "import json,sys; from pathlib import Path; context=json.loads(Path(sys.argv[1]).read_text())"
        "['resource_context_before_requests']['capital_stack_containers']; "
        "print(json.dumps({'resource_status':context['status'],'detail':context.get('detail'),"
        "'container_count':len(context.get('containers',[])),'stats_count':len(context.get('stats',[]))}))"
    ), [plan_path], timeout=30)
    if plan_resources.get("resource_status") != "captured":
        raise HarnessError("Frozen operational plan did not capture isolated stack resources: "
                           + json.dumps(plan_resources, sort_keys=True))

    measurement = run_isolated_script(root, env, "audited synthetic measurement", 'runtime_tools.py', ['benchmark',
        "--run-measurement", "--plan", plan_path, "--candidate-id", candidate_id,
        "--selection-report", selection, "--final-report", final,
    ], timeout=600)
    if (measurement.get("status") != "completed" or measurement.get("operational_gate") != "not_evaluated"
            or measurement.get("deployment_permission") != "blocked"):
        raise HarnessError("Synthetic measurement did not finish with deployment blocked")
    report = json.loads(Path(measurement["report_path"]).read_text(encoding="utf-8"))
    if (report.get("completed") is not True or report.get("audit", {}).get("status") != "passed"
            or report.get("operational_gate", {}).get("status") != "not_evaluated"
            or report.get("operational_gate", {}).get("input_provenance_status") != "passed"
            or len(report.get("measurements", [])) != 1050
            or report.get("resource_context", {}).get("capital_stack_containers", {}).get("status") != "captured"):
        raise HarnessError("Synthetic measurement report lacks completed requests, audit or resource evidence")
    recheck = run_isolated_script(root, env, "ledger-linked numeric recheck", 'runtime_tools.py', ['recheck',
        "--candidate-id", candidate_id, "--attempt-id", measurement["attempt_id"],
    ])
    if (recheck.get("ledger_binding") != "matched" or recheck.get("operational_gate") != "not_evaluated"
            or recheck.get("deployment_permission") != "blocked"
            or recheck.get("numeric_status") != report["operational_gate"]["measurement_status"]):
        raise HarnessError("Linked recheck differs from the report or changes a non-evaluated gate")
    evidence_record = run_isolated_script(root, env, "exact-version operational evidence recording",
                                          'runtime_tools.py', ['record-operational',
        "--candidate-id", candidate_id, "--attempt-id", measurement["attempt_id"],
    ])
    conflicting_report_sha = "0" * 64 if recheck["report_sha256"] != "0" * 64 else "1" * 64
    run_isolated_python(root, env, "synthetic conflicting registry tag setup", (
        "import os,sys; from mlflow.tracking import MlflowClient; "
        "c=MlflowClient(tracking_uri=os.environ['MLFLOW_TRACKING_URI'],registry_uri=os.environ['MLFLOW_REGISTRY_URI']); "
        f"c.set_model_version_tag('bike-demand-capital','{version}','capital_operational_report_sha256',sys.argv[1]); "
        "print('{}')"
    ), [conflicting_report_sha])
    run_expected_script_rejection(
        root, env, "conflicting exact-version evidence rejection", 'runtime_tools.py',
        ['record-operational', "--candidate-id", candidate_id, "--attempt-id", measurement["attempt_id"]],
        "Refusing to overwrite different operational evidence tags",
    )
    conflict_check = run_isolated_python(root, env, "blocked gate after conflict rejection", (
        "import json,os; from mlflow.tracking import MlflowClient; "
        "c=MlflowClient(tracking_uri=os.environ['MLFLOW_TRACKING_URI'],registry_uri=os.environ['MLFLOW_REGISTRY_URI']); "
        f"v=c.get_model_version('bike-demand-capital','{version}'); "
        "print(json.dumps({'report_sha':v.tags.get('capital_operational_report_sha256'),"
        "'gate':v.tags.get('operational_gate_status'),'permission':v.tags.get('deployment_permission')}))"
    ))
    if (conflict_check.get("report_sha") != conflicting_report_sha
            or conflict_check.get("gate") != "not_evaluated"
            or conflict_check.get("permission") != "blocked"):
        raise HarnessError("Conflicting evidence refusal changed registry tags or unblocked the model")
    run_isolated_python(root, env, "restore disposable synthetic tag after conflict test", (
        "import os,sys; from mlflow.tracking import MlflowClient; "
        "c=MlflowClient(tracking_uri=os.environ['MLFLOW_TRACKING_URI'],registry_uri=os.environ['MLFLOW_REGISTRY_URI']); "
        f"c.set_model_version_tag('bike-demand-capital','{version}','capital_operational_report_sha256',sys.argv[1]); "
        "print('{}')"
    ), [recheck["report_sha256"]])
    evidence_record_again = run_isolated_script(root, env, "idempotent exact-version evidence recording",
                                                'runtime_tools.py', ['record-operational',
        "--candidate-id", candidate_id, "--attempt-id", measurement["attempt_id"],
    ])
    registry_tags = run_isolated_python(root, env, "exact-version evidence tag verification", (
        "import json,os; from mlflow.tracking import MlflowClient; "
        "c=MlflowClient(tracking_uri=os.environ['MLFLOW_TRACKING_URI'],registry_uri=os.environ['MLFLOW_REGISTRY_URI']); "
        f"v=c.get_model_version('bike-demand-capital','{version}'); "
        "keys=[k for k in v.tags if k.startswith('capital_operational_')]; "
        "print(json.dumps({'tags':{k:v.tags[k] for k in keys},'gate':v.tags.get('operational_gate_status'),"
        "'permission':v.tags.get('deployment_permission')}))"
    ), timeout=60)
    if (evidence_record.get("status") != "evidence_recorded"
            or evidence_record.get("report_sha256") != evidence_record_again.get("report_sha256")
            or evidence_record_again.get("idempotent") is not True
            or registry_tags.get("gate") != "not_evaluated"
            or registry_tags.get("permission") != "blocked"
            or registry_tags.get("tags", {}).get("capital_operational_attempt_id") != measurement["attempt_id"]
            or registry_tags.get("tags", {}).get("capital_operational_report_sha256") != recheck["report_sha256"]):
        raise HarnessError("Operational evidence tags did not persist idempotently on the blocked exact version")
    return {
        "status": "synthetic_system_passed",
        "data": "synthetic_only",
        "candidate_id": candidate_id,
        "model_version": version,
        "candidate_registration": "blocked_and_idempotent",
        "staging_rows_seeded_and_verified": seeded["new_observations_inserted"],
        "api_ready": ready.get("status"),
        "offline_api_parity_absolute_delta": abs(float(prediction["prediction"]) - float(offline["prediction"])),
        "api_idempotent_retry": "passed",
        "api_changed_input_conflict": conflict_status,
        "api_audit_rows": audit_result["audit_rows"],
        "runtime_init_rejections": runtime_rejections,
        "measurement_attempt": measurement["attempt_id"],
        "plan_relative_path": plan_path,
        "report_relative_path": Path(measurement["report_path"]).resolve().relative_to(root.resolve()).as_posix(),
        "requests": len(report["measurements"]),
        "audited_rows": report["audit"]["verified_rows"],
        "measurement_status": recheck["numeric_status"],
        "p95_ms": recheck["p95_ms"],
        "numeric_recheck": "matched",
        "exact_version_evidence_tags": "recorded_and_idempotent",
        "conflicting_evidence_rejected_without_unblocking": True,
        "operational_gate": "not_evaluated",
        "deployment_permission": "blocked",
        "resource_context": "captured_before_and_after",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-infrastructure", action="store_true", help="Build and briefly start disposable DB and MLflow")
    parser.add_argument("--prepare-synthetic", action="store_true", help="Generate a synthetic evaluated candidate in the temporary workspace")
    parser.add_argument("--run-synthetic-system", action="store_true",
                        help="Run blocked registration, staging seed/API, audited 1,050-request measurement and cleanup using synthetic data")
    parser.add_argument("--evidence-dir", type=Path,
                        help="Opt in to archive and independently recalculate this synthetic run under data/reports")
    parser.add_argument("--verify-paired-restore", action="store_true",
                        help="After synthetic measurement, restore registry/skops/audit to three separate disposable volumes")
    args = parser.parse_args()
    run_synthetic = args.run_synthetic_system
    if args.evidence_dir is not None and not run_synthetic:
        parser.error("--evidence-dir requires --run-synthetic-system")
    if args.verify_paired_restore and not run_synthetic:
        parser.error("--verify-paired-restore requires --run-synthetic-system")
    prepare_synthetic = args.prepare_synthetic or run_synthetic
    run_infrastructure = args.run_infrastructure or run_synthetic
    project = "bikemlopstest" + secrets.token_hex(8)
    token = secrets.token_hex(16)
    root = Path(tempfile.mkdtemp(prefix="capital-system-harness-"))
    cleanup_required = False
    protected_before = None
    preserve = False
    evidence_exported = False
    stage = "prepare_workspace"
    old_handlers = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}

    def interrupt_on_signal(signum, _frame):
        print(json.dumps({"status": "interrupted", "cause": signal.Signals(signum).name}), flush=True)
        raise KeyboardInterrupt(f"Harness received {signal.Signals(signum).name}")

    for signum in old_handlers:
        signal.signal(signum, interrupt_on_signal)
    try:
        copy_workspace(SOURCE_ROOT, root)
        if run_synthetic:
            from bike_mlops.operational_validation import _workspace_marker
            _workspace_marker(root, project, token)
        make_override(root, token)
        write_synthetic_credentials(root)
        harness = Harness(root, project, token)
        synthetic = None
        if prepare_synthetic:
            stage = "synthetic_candidate"
            synthetic = prepare_synthetic_workspace(root)
            print(json.dumps(synthetic, ensure_ascii=False), flush=True)
        if not run_infrastructure:
            print(json.dumps({"status": "workspace_prepared", "candidate_generation": "prepared" if prepare_synthetic else "not_implemented",
                              "prediction_requests": 0, "docker_started": False}))
            return
        stage = "preflight"
        check_local_daemon()
        check_ports_free()
        harness.assert_empty()
        protected_before = protected_container_ids()
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
        stage = "verify_profiles"
        harness.verify_profiles()
        stage = "wait_mlflow"
        wait_ready()
        stage = "verify_ports"
        harness.verify_ports()
        print(json.dumps({"status": "infrastructure_ready", "candidate_generation": "prepared" if prepare_synthetic else "not_implemented",
                          "api_started": False, "prediction_requests": 0,
                          "operational_gate": "not_evaluated", "project": project}))
        if run_synthetic:
            stage = "synthetic_system"
            result = run_synthetic_system(root, harness, synthetic, project)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            if args.evidence_dir is not None:
                stage = "archive_synthetic_evidence"
                from bike_mlops.operational_validation import export_synthetic_evidence
                commit = subprocess.run(["git", "-C", str(SOURCE_ROOT), "rev-parse", "HEAD"],
                                        capture_output=True, text=True, timeout=10, check=True).stdout.strip()
                dirty = bool(subprocess.run(["git", "-C", str(SOURCE_ROOT), "status", "--porcelain"],
                                            capture_output=True, text=True, timeout=10, check=True).stdout.strip())
                evidence_dir = args.evidence_dir if args.evidence_dir.is_absolute() else SOURCE_ROOT / args.evidence_dir
                evidence = export_synthetic_evidence(
                    root=root, project=project, token=token, candidate_id=result["candidate_id"],
                    attempt_id=result["measurement_attempt"], plan_relative=result["plan_relative_path"],
                    report_path=root / result["report_relative_path"], evidence_dir=evidence_dir,
                    source_commit=commit, source_dirty=dirty)
                evidence_exported = True
                print(json.dumps({"status": "synthetic_evidence_archived_and_recalculated", **evidence},
                                 ensure_ascii=False), flush=True)
            if args.verify_paired_restore:
                stage = "paired_registry_skops_audit_restore"
                from system_harness import run_paired_restore
                run_paired_restore(root, harness, result, synthetic, harness_module=sys.modules[__name__])
    except HarnessError as error:
        cause = "required_port_unavailable" if str(error).startswith("Required local port ") else "not_determined"
        print(json.dumps({"status": "failed", "stage": stage, "cause": cause,
                          "diagnostic_output": "suppressed"}), flush=True)
        raise
    except KeyboardInterrupt:
        print(json.dumps({"status": "interrupted", "stage": stage,
                          "resources_cleanup": "attempted"}), flush=True)
        raise SystemExit(130)
    finally:
        try:
            if cleanup_required:
                try:
                    harness.cleanup()
                    if protected_before != protected_container_ids():
                        raise HarnessError("Protected project container identities changed during the fixture")
                    print(json.dumps({"status": "protected_container_ids_unchanged"}), flush=True)
                    print(json.dumps({"status": "cleanup_complete", "project": project}), flush=True)
                except BaseException as error:
                    preserve = True
                    if evidence_exported:
                        evidence_dir = args.evidence_dir if args.evidence_dir.is_absolute() else SOURCE_ROOT / args.evidence_dir
                        try:
                            shutil.rmtree(evidence_dir)
                        except BaseException as removal_error:
                            print(f"Evidence removal failed with {type(removal_error).__name__}; "
                                  "resource cleanup also failed; workspace retained", file=sys.stderr)
                            raise BaseExceptionGroup("Resource cleanup and evidence removal both failed", [error, removal_error])
                        print("Synthetic evidence removed because disposable-resource cleanup failed", file=sys.stderr)
                    print(f"Cleanup failed; isolated workspace retained at {root}; project {project}; "
                          f"error type {type(error).__name__}; underlying cause not determined", file=sys.stderr)
                    raise
            if not preserve:
                shutil.rmtree(root)
        finally:
            for signum, handler in old_handlers.items():
                signal.signal(signum, handler)


def _entry_capital_system_harness():
    main()


# ========================================================================
# test_capital_synthetic_fixture
# ========================================================================

"""Generate and validate a Capital candidate solely inside a disposable workspace.

No Docker, external dataset, real evaluation ledger, or registry is used.
The temporary workspace is removed after this standalone verification.
"""


import argparse as _capital_synthetic_fixture_argparse
import json as _capital_synthetic_fixture_json
import signal as _capital_synthetic_fixture_signal
import sys as _capital_synthetic_fixture_sys
import tempfile as _capital_synthetic_fixture_tempfile
from pathlib import Path as _capital_synthetic_fixture_Path


def _run_child() -> None:
    root = _capital_synthetic_fixture_Path(__file__).resolve().parents[1]
    temporary_root = _capital_synthetic_fixture_Path(_capital_synthetic_fixture_tempfile.gettempdir()).resolve()
    if (root.parent != temporary_root or not root.name.startswith(("capital-synthetic-test-", "capital-system-harness-"))
            or not (root / ".synthetic-fixture-owned").is_file()):
        raise RuntimeError("Synthetic fixture may only run in its disposable marked workspace")
    import contextlib
    import io
    import zipfile
    import pandas as pd
    from bike_mlops.config import PROJECT_ROOT, DEFAULT_DATASET, DATASET_MANIFEST, DATA_PROFILE_ID
    from bike_mlops.operational_validation import check_candidate_training_window
    from bike_mlops.evaluation import build_capital_protocol, save_protocol
    from bike_mlops.training import HYPERPARAMETERS, train_candidate
    from scripts.data_tools import aggregate_archives
    from scripts import data_tools as evaluate_capitalbikeshare_holdout

    if PROJECT_ROOT.resolve() != root or DATA_PROFILE_ID != "capital-bikeshare":
        raise RuntimeError("Synthetic fixture resolved an unexpected project root or profile")
    raw = root / "data/raw/capitalbikeshare/monthly"
    raw.mkdir(parents=True, exist_ok=False)
    lines = ["ride_id,started_at"]
    ride_id = 0
    for timestamp in pd.date_range("2024-01-01", "2024-02-01", freq="h", inclusive="left"):
        count = 5 + timestamp.hour % 12 + (0 if timestamp.dayofweek >= 5 else 25)
        for _ in range(count):
            lines.append(f"ride-{ride_id},{timestamp:%Y-%m-%d %H:%M:%S}")
            ride_id += 1
    with zipfile.ZipFile(raw / "202401-capitalbikeshare-tripdata.zip", "x", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("trips.csv", "\n".join(lines) + "\n")
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        aggregate_archives(["202401"], pd.Timestamp("2024-01-01"), pd.Timestamp("2024-02-01"),
                           DEFAULT_DATASET, DATASET_MANIFEST, raw_dir=raw)
        protocol = build_capital_protocol(DEFAULT_DATASET, DATASET_MANIFEST,
            training_cutoff="2024-01-15 00:00:00", validation_start="2024-01-15 00:00:00",
            validation_end="2024-01-22 00:00:00", evaluation_start="2024-01-22 03:00:00",
            evaluation_end="2024-01-29 00:00:00", incumbent_version=None, incumbent_artifact_sha256=None,
            incumbent_quality_gate=None, candidate_recipe={"model_family": "scikit-learn HistGradientBoostingRegressor in a Pipeline",
                                                          "hyperparameters": HYPERPARAMETERS}, raw_dir=raw)
        protocol_path = root / "data/evaluations/synthetic-protocol.json"
        save_protocol(protocol, protocol_path)
        report = train_candidate(data_path=DEFAULT_DATASET, incumbent_version=None, source_manifest_path=DATASET_MANIFEST,
                                 register=False, evaluation_protocol_path=protocol_path)
        if not report["quality_gate_passed"]:
            raise RuntimeError("Synthetic candidate did not pass the predefined validation gate")
        selection = root / report["candidate_artifacts"]["candidate_report"]
        output = root / "data/reports/capital-bikeshare/synthetic-final.json"
        _capital_synthetic_fixture_sys.argv = ['data_tools.py', "--csv", str(DEFAULT_DATASET),
                    "--source-manifest", str(DATASET_MANIFEST), "--selection-report", str(selection), "--output", str(output)]
        evaluate_capitalbikeshare_holdout._evaluate_capitalbikeshare_holdout_main()
        final = _capital_synthetic_fixture_json.loads(output.read_text())
        if not final["final_accuracy_gate"]["passed"]:
            raise RuntimeError("Synthetic candidate did not pass the predefined final gate")
        window = check_candidate_training_window(selection, output, report["candidate_artifacts"]["candidate_id"],
            start="2024-01-07 22:00:00", end="2024-01-14 22:00:00")
        if window["hours"] != 168 or window["latest_replay_label_available_at"] != "2024-01-15 00:00:00":
            raise RuntimeError("Synthetic measurement window violates the training cutoff")
    summary = {"status": "passed", "data": "synthetic", "rides": ride_id, "hourly_rows": len(pd.read_csv(DEFAULT_DATASET)),
               "candidate_id": report["candidate_artifacts"]["candidate_id"],
               "validation_gate": report["validation_gate_status"],
               "final_gate": final["final_accuracy_gate"]["status"],
               "measurement_start": "2024-01-07 22:00:00",
               "measurement_end": "2024-01-14 22:00:00",
               "measurement_window_hours": window["hours"],
               "latest_label_available_at": window["latest_replay_label_available_at"],
               "registry_writes": 0, "prediction_requests": 0,
               "deployment_permission": "blocked"}
    (root / "synthetic-fixture-result.json").write_text(_capital_synthetic_fixture_json.dumps(summary), encoding="utf-8")


def _run_parent() -> None:
    from system_harness import copy_workspace, prepare_synthetic_workspace, SOURCE_ROOT
    with _capital_synthetic_fixture_tempfile.TemporaryDirectory(prefix="capital-synthetic-test-") as directory:
        root = _capital_synthetic_fixture_Path(directory).resolve()
        copy_workspace(SOURCE_ROOT, root)
        summary = prepare_synthetic_workspace(root)
        print(_capital_synthetic_fixture_json.dumps(summary, indent=2, ensure_ascii=False))
    if root.exists():
        raise RuntimeError("Synthetic workspace was not removed")
    print("Disposable synthetic workspace removed")


def _capital_synthetic_fixture_main() -> None:
    parser = _capital_synthetic_fixture_argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--internal-child", action="store_true", help=_capital_synthetic_fixture_argparse.SUPPRESS)
    args = parser.parse_args()
    if args.internal_child:
        _run_child()
        return
    previous = _capital_synthetic_fixture_signal.getsignal(_capital_synthetic_fixture_signal.SIGTERM)
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt("Synthetic fixture received SIGTERM")
    _capital_synthetic_fixture_signal.signal(_capital_synthetic_fixture_signal.SIGTERM, interrupted)
    try:
        _run_parent()
    finally:
        _capital_synthetic_fixture_signal.signal(_capital_synthetic_fixture_signal.SIGTERM, previous)


def _entry_test_capital_synthetic_fixture():
    _capital_synthetic_fixture_main()


# ========================================================================
# capital_paired_restore
# ========================================================================

"""Quiescent, synthetic-only registry/artifact/audit backup and separate-volume restore.

Called only by the UUID-owned Capital system harness. There is no endpoint or
resource selector CLI; source and restored resources are checked through Harness.
Raw dumps and artifact bytes stay in the temporary workspace, never CI uploads.
"""


import hashlib as _paired_restore_hashlib
from contextlib import ExitStack
import json as _paired_restore_json
from pathlib import Path as _paired_restore_Path
import secrets as _paired_restore_secrets
import socket as _paired_restore_socket
import shutil as _paired_restore_shutil
import subprocess as _paired_restore_subprocess
import tempfile as _paired_restore_tempfile


def tree_manifest(root: _paired_restore_Path) -> dict:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Artifact snapshot root must be a regular directory")
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or (not path.is_dir() and not path.is_file()):
            raise ValueError("Artifact snapshot must contain only regular files/directories")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = {
                "bytes": path.stat().st_size,
                "sha256": _paired_restore_hashlib.sha256(path.read_bytes()).hexdigest(),
            }
    if not result or not any(name.endswith("/MLmodel") for name in result):
        raise ValueError("Artifact snapshot is empty or lacks MLmodel")
    if not any(name.endswith(".skops") for name in result):
        raise ValueError("Artifact snapshot lacks the skops estimator")
    return result


def database_manifest(url: str) -> dict:
    import psycopg
    from psycopg import sql

    tables = {}
    with psycopg.connect(url) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        names = connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
        ).fetchall()
        for (name,) in names:
            rows = connection.execute(sql.SQL("SELECT to_jsonb(t)::text FROM {} AS t").format(
                sql.Identifier("public", name))).fetchall()
            canonical = sorted(_paired_restore_json.dumps(_paired_restore_json.loads(row[0]), sort_keys=True, separators=(",", ":")) for row in rows)
            tables[name] = dict(rows=len(rows), sha256=_paired_restore_hashlib.sha256("\n".join(canonical).encode()).hexdigest())
        columns = connection.execute("SELECT table_name,column_name,data_type,is_nullable,column_default "
            "FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position").fetchall()
        sequences = {}
        for (name,) in connection.execute("SELECT sequencename FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename"):
            sequences[name] = connection.execute(sql.SQL("SELECT last_value,is_called FROM {}").format(
                sql.Identifier("public", name))).fetchone()
    if not tables:
        raise ValueError("Database snapshot has no public tables")
    return dict(tables=tables, sequences=sequences,
                columns_sha256=_paired_restore_hashlib.sha256(_paired_restore_json.dumps(columns).encode()).hexdigest())


def require_equal(expected, actual, description):
    if expected != actual:
        raise ValueError("Paired restore mismatch: " + description)


def run_paired_restore(root, harness, result, synthetic, *, harness_module=None):
    # Relative import also works when the parent script is launched directly.
    if harness_module is not None:
        h = harness_module
    elif __package__:
        from . import system_harness as h
    else:
        import system_harness as h

    h.check_local_daemon()
    harness.verify_ports(require_staging_api=True)
    harness.assert_owned(harness.resources())
    password = h.read_synthetic_credentials(root)
    version, candidate = result["model_version"], result["candidate_id"]
    identity = h.request_json("http://127.0.0.1:8110/version")[1]
    if identity.get("candidate_id") != candidate or identity.get("model_version") != version:
        raise ValueError("Snapshot API identity differs from the synthetic candidate")
    target = synthetic["measurement_start"]
    payload = dict(request_id="paired-restore-" + _paired_restore_secrets.token_hex(8), target_hour=target, as_of=target,
                   feature_contract_version="bike-demand-features-v1", data_profile_id="capital-bikeshare",
                   target_contract_id="capital-trip-starts-hourly-v1")
    code, original_prediction = h.request_json("http://127.0.0.1:8110/predict", payload)
    if code != 200:
        raise ValueError("Cannot snapshot an unrecorded prediction")
    ids = {}
    for service in ("mlflow", "postgres", "postgres-staging"):
        entries = h.docker(*harness.compose, "ps", "-q", service, env=harness.env).splitlines()
        if len(entries) != 1:
            raise ValueError("Snapshot requires exactly one owned service")
        ids[service] = entries[0]
    # No other clients exist in this private fixture. Stop both writers before
    # either DB dump or file copy; this is a quiescent pair, not a live backup.
    h.docker(*harness.compose, "stop", "api-staging", "mlflow", env=harness.env)
    before = {
        "registry": database_manifest(f"postgresql://mlflow_app:{password}@127.0.0.1:55433/mlflow"),
        "audit": database_manifest(f"postgresql://bike_app:{password}@127.0.0.1:55434/bikeops"),
    }
    restore_project, token = "bikemlopstest" + _paired_restore_secrets.token_hex(8), _paired_restore_secrets.token_hex(16)
    with ExitStack() as stack:
        sockets = [stack.enter_context(_paired_restore_socket.socket()) for _ in range(4)]
        for item in sockets:
            item.bind(("127.0.0.1", 0))
        ports = tuple(item.getsockname()[1] for item in sockets)
    restore_root = _paired_restore_Path(_paired_restore_tempfile.mkdtemp(prefix="capital-system-harness-"))
    restore = h.Harness(restore_root, restore_project, token, ports=ports)
    restore_api = f"http://127.0.0.1:{ports[1]}"
    restore_mlflow = f"http://127.0.0.1:{ports[0]}"
    cleanup_needed, preserve = False, False
    summary = dict(status="started", synthetic_only=True, real_Capital_evidence=False,
                   source_project=harness.project, restore_project=restore_project,
                   cleanup_complete=False)
    try:
        h.copy_workspace(root, restore_root)
        h.make_override(restore_root, token)
        _paired_restore_shutil.copy2(root / "harness.env", restore_root / "harness.env")
        runtime = restore_root / "data/runtime-capital-staging"
        runtime.mkdir(parents=True)
        _paired_restore_shutil.copy2(root / "data/runtime-capital-staging/staging.env", runtime / "staging.env")
        backup = restore_root / "paired-backup"
        backup.mkdir(mode=0o700)
        hashes = {}
        for service, dbname, role, name in (("postgres", "mlflow", "mlflow_app", "registry"),
                                           ("postgres-staging", "bikeops", "bike_app", "audit")):
            destination = backup / (name + ".dump")
            with destination.open("xb") as stream:
                completed = _paired_restore_subprocess.run(["docker", "exec", ids[service], "pg_dump", "-U", role,
                    "-d", dbname, "-Fc", "--no-owner", "--no-acl"], stdout=stream, stderr=_paired_restore_subprocess.PIPE, timeout=60)
            if completed.returncode:
                raise RuntimeError("Owned database dump failed; native details suppressed")
            hashes[name] = _paired_restore_hashlib.sha256(destination.read_bytes()).hexdigest()
        artifacts = backup / "artifacts"
        artifacts.mkdir()
        h.docker("cp", ids["mlflow"] + ":/mlartifacts/.", str(artifacts))
        files = tree_manifest(artifacts)
        # A version/DB/file pair must remain unchanged throughout collection.
        require_equal(before["registry"], database_manifest(
            f"postgresql://mlflow_app:{password}@127.0.0.1:55433/mlflow"), "source registry changed")
        require_equal(before["audit"], database_manifest(
            f"postgresql://bike_app:{password}@127.0.0.1:55434/bikeops"), "source audit changed")
        h.docker(*harness.compose, "stop", "postgres", "postgres-staging", env=harness.env)
        h.check_ports_free(ports)
        restore.assert_empty()
        override = _paired_restore_json.loads((restore_root / "harness-override.json").read_text())
        image_ids = {}
        for service in ("mlflow", "api-staging"):
            source_id = h.docker(*harness.compose, "ps", "-a", "-q", service, env=harness.env)
            image = h.docker("inspect", "--format", "{{.Image}}", source_id)
            image_ids[service] = image
            override["services"][service]["image"] = image
        command = _paired_restore_json.loads(h.docker(*harness.compose, "config", "--format", "json", env=harness.env))["services"]["mlflow"]["command"]
        command[command.index("--allowed-hosts") + 1] = f"mlflow:5000,127.0.0.1:{ports[0]}"
        override["services"]["mlflow"]["command"] = command
        (restore_root / "harness-override.json").write_text(_paired_restore_json.dumps(override))
        port_file = restore_root / "restore-ports.yaml"
        port_file.write_text("services:\n" + "".join(
            f"  {service}:\n    ports: !override\n      - '127.0.0.1:{port}:{internal}'\n"
            for service, port, internal in (("mlflow", ports[0], 5000), ("api-staging", ports[1], 8000),
                                             ("postgres", ports[2], 5432), ("postgres-staging", ports[3], 5432))))
        restore.compose.extend(["-f", str(port_file)])
        cleanup_needed = True
        h.docker(*restore.compose, "up", "-d", "--no-build", "postgres", "postgres-staging", env=restore.env)
        # Compose health waits are used before restore, not elapsed wall UTC.
        h.docker(*restore.compose, "up", "-d", "--no-build", "--wait", "postgres", "postgres-staging", env=restore.env)
        for service, dbname, role, name in (("postgres", "mlflow", "mlflow_app", "registry"),
                                           ("postgres-staging", "bikeops", "bike_app", "audit")):
            container = h.docker(*restore.compose, "ps", "-q", service, env=restore.env)
            restore.assert_owned({"container": [container]})
            with (backup / (name + ".dump")).open("rb") as stream:
                completed = _paired_restore_subprocess.run(["docker", "exec", "-i", container, "pg_restore", "-U", "postgres",
                    "--role", role, "-d", dbname, "--exit-on-error", "--no-owner", "--no-acl"],
                    stdin=stream, stdout=_paired_restore_subprocess.PIPE, stderr=_paired_restore_subprocess.PIPE, timeout=60)
            if completed.returncode:
                raise RuntimeError("Owned database restore failed; native details suppressed")
        h.docker(*restore.compose, "create", "--no-build", "mlflow", env=restore.env)
        mlflow_id = h.docker(*restore.compose, "ps", "-a", "-q", "mlflow", env=restore.env)
        restore.assert_owned({"container": [mlflow_id]})
        h.docker("cp", str(artifacts) + "/.", mlflow_id + ":/mlartifacts")
        check = backup / "restored-artifacts"
        check.mkdir()
        h.docker("cp", mlflow_id + ":/mlartifacts/.", str(check))
        require_equal(files, tree_manifest(check), "artifact files")
        for key, url in (("registry", f"postgresql://mlflow_app:{password}@127.0.0.1:{ports[2]}/mlflow"),
                         ("audit", f"postgresql://bike_app:{password}@127.0.0.1:{ports[3]}/bikeops")):
            require_equal(before[key], database_manifest(url), key + " tables")
        source_volumes = set(harness.resources()["volume"])
        restored_volumes = set(restore.resources()["volume"])
        if len(source_volumes) != 3 or len(restored_volumes) != 3 or source_volumes & restored_volumes:
            raise ValueError("Restore did not use three separate volumes")
        h.docker(*restore.compose, "up", "-d", "--no-build", "mlflow", env=restore.env)
        h.wait_ready(restore_mlflow)
        # Exercise the actual pinned staging loader against a corrupt restored
        # estimator, then repair only this disposable copy from the saved bytes.
        estimator = next(name for name in files if name.endswith(".skops"))
        h.docker("exec", mlflow_id, "python", "-c",
                 "from pathlib import Path; import sys; (Path('/mlartifacts')/sys.argv[1]).write_bytes(b'corrupt fixture')",
                 estimator)
        env = h.isolated_python_environment(restore_root, restore_project, password, ports=ports)
        rejection = h.run_isolated_python(restore_root, env, "restored corrupt skops rejection", "\nimport json,os\nfrom pathlib import Path\nfor line in Path('data/runtime-capital-staging/staging.env').read_text().splitlines():\n    key,value=line.split('=',1);os.environ[key]=value\nfrom bike_mlops.staging import load_staging_registered_model\ntry:\n    load_staging_registered_model()\nexcept ValueError as error:\n    assert 'SHA-256 does not match' in str(error), type(error).__name__\nelse:\n    raise AssertionError('Corrupt restored skops was accepted')\nprint(json.dumps({'corrupt_restored_skops_rejected':True}))\n", timeout=60)
        require_equal({"corrupt_restored_skops_rejected": True}, rejection, "corrupt model rejection")
        h.docker("cp", str(artifacts / estimator), mlflow_id + ":/mlartifacts/" + estimator)
        h.docker(*restore.compose, "up", "-d", "--no-build", "api-staging", env=restore.env)
        restore.verify_ports(require_staging_api=True)
        h.wait_staging_api(candidate, version, identity["artifact_sha256"], base_url=restore_api)
        actual_identity = h.request_json(restore_api + "/version")[1]
        require_equal(identity, actual_identity, "API model identity")
        code, retried = h.request_json(restore_api + "/predict", payload)
        require_equal((200, original_prediction), (code, retried), "persisted prediction retry")
        # New ID proves deserializing the restored model and actually predicting,
        # rather than only returning the restored audit row for an old request.
        new_payload = dict(payload, request_id=payload["request_id"] + "-new")
        code, fresh = h.request_json(restore_api + "/predict", new_payload)
        require_equal((200, original_prediction["prediction"], identity["artifact_sha256"]),
                      (code, fresh.get("prediction"), fresh.get("artifact_sha256")), "fresh skops prediction")
        if fresh.get("deployment_permission") != "blocked":
            raise ValueError("Synthetic restore must remain blocked")
        inspection = h.run_isolated_python(restore_root, env, "read-only restored state inspection", "\nimport json,os\nfrom pathlib import Path\nfrom bike_mlops.deployment import inspect_state\npath=Path('data/legacy-state-inspection.json')\npath.write_text(json.dumps(dict(active_version='1',active_slot='blue',last_deployment_id='synthetic-bootstrap',\n    data_profile_id='capital-bikeshare',target_contract_id='capital-trip-starts-hourly-v1')))\nlegacy=inspect_state(path,os.environ['DATABASE_URL'],'capital-bikeshare','capital-trip-starts-hourly-v1')\nforeign=inspect_state(path,os.environ['DATABASE_URL'],'uci-bike-sharing','uci-bike-sharing-cnt-dataset-local-hour')\nassert legacy['status']=='legacy_unverifiable' and foreign['status']=='database_profile_mismatch'\nassert legacy['automatic_repair'] is False and foreign['automatic_repair'] is False\nprint(json.dumps({'legacy':legacy['status'],'foreign_profile':foreign['status'],'automatic_repair':False}))\n", timeout=45)
        summary.update(status="passed", separate_volumes=True, dump_sha256=hashes, tables=before,
                       artifact_files=files, model_identity=identity, current_Dockerfile_image_ids=image_ids,
                       retry_identical=True, fresh_skops_prediction=True, corrupt_restored_skops_rejected=True,
                       deployment_permission="blocked",
                       operational_gate="not_evaluated", read_only_state_inspection=inspection)
    except BaseException as error:
        summary.update(status="failed", error_type=type(error).__name__)
        raise
    finally:
        try:
            if cleanup_needed:
                restore.cleanup()
            summary["resources_remaining"] = restore.resources()
            if any(summary["resources_remaining"].values()):
                raise RuntimeError("Restore resources remain after cleanup")
            summary["cleanup_complete"] = True
        except BaseException:
            preserve = True
            summary["status"] = "cleanup_failed"
            raise
        finally:
            try:
                if not preserve:
                    _paired_restore_shutil.rmtree(restore_root)
            except BaseException:
                summary["status"] = "workspace_cleanup_failed"
                summary["cleanup_complete"] = False
                raise
            finally:
                summary["workspace_removed"] = not restore_root.exists()
                print(_paired_restore_json.dumps({"paired_restore": summary}), flush=True)
    return summary


def _dispatch_cli():
    import sys as _dispatch_sys
    commands = {'run': _entry_capital_system_harness, 'fixture': _entry_test_capital_synthetic_fixture}
    if len(_dispatch_sys.argv) < 2 or _dispatch_sys.argv[1] in {"-h", "--help"}:
        print("Usage: system_harness.py <command> [options]\nCommands: run, fixture")
        return 0
    command = _dispatch_sys.argv.pop(1)
    if command not in commands:
        raise SystemExit("Unknown command: " + command)
    return commands[command]()


if __name__ == "__main__":
    raise SystemExit(_dispatch_cli())
