"""Quiescent, synthetic-only registry/artifact/audit backup and separate-volume restore.

Called only by the UUID-owned Capital system harness. There is no endpoint or
resource selector CLI; source and restored resources are checked through Harness.
Raw dumps and artifact bytes stay in the temporary workspace, never CI uploads.
"""
from __future__ import annotations

import hashlib
from contextlib import ExitStack
import json
from pathlib import Path
import secrets
import socket
import shutil
import subprocess
import tempfile


def tree_manifest(root: Path) -> dict:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Artifact snapshot root must be a regular directory")
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or (not path.is_dir() and not path.is_file()):
            raise ValueError("Artifact snapshot must contain only regular files/directories")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = {
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
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
            canonical = sorted(json.dumps(json.loads(row[0]), sort_keys=True, separators=(",", ":")) for row in rows)
            tables[name] = dict(rows=len(rows), sha256=hashlib.sha256("\n".join(canonical).encode()).hexdigest())
        columns = connection.execute("SELECT table_name,column_name,data_type,is_nullable,column_default "
            "FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position").fetchall()
        sequences = {}
        for (name,) in connection.execute("SELECT sequencename FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename"):
            sequences[name] = connection.execute(sql.SQL("SELECT last_value,is_called FROM {}").format(
                sql.Identifier("public", name))).fetchone()
    if not tables:
        raise ValueError("Database snapshot has no public tables")
    return dict(tables=tables, sequences=sequences,
                columns_sha256=hashlib.sha256(json.dumps(columns).encode()).hexdigest())


def require_equal(expected, actual, description):
    if expected != actual:
        raise ValueError("Paired restore mismatch: " + description)


def run_paired_restore(root, harness, result, synthetic, *, harness_module=None):
    # Relative import also works when the parent script is launched directly.
    if harness_module is not None:
        h = harness_module
    elif __package__:
        from . import capital_system_harness as h
    else:
        import capital_system_harness as h

    h.check_local_daemon()
    harness.verify_ports(require_staging_api=True)
    harness.assert_owned(harness.resources())
    password = h.read_synthetic_credentials(root)
    version, candidate = result["model_version"], result["candidate_id"]
    identity = h.request_json("http://127.0.0.1:8110/version")[1]
    if identity.get("candidate_id") != candidate or identity.get("model_version") != version:
        raise ValueError("Snapshot API identity differs from the synthetic candidate")
    target = synthetic["measurement_start"]
    payload = dict(request_id="paired-restore-" + secrets.token_hex(8), target_hour=target, as_of=target,
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
    restore_project, token = "bikemlopstest" + secrets.token_hex(8), secrets.token_hex(16)
    with ExitStack() as stack:
        sockets = [stack.enter_context(socket.socket()) for _ in range(4)]
        for item in sockets:
            item.bind(("127.0.0.1", 0))
        ports = tuple(item.getsockname()[1] for item in sockets)
    restore_root = Path(tempfile.mkdtemp(prefix="capital-system-harness-"))
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
        shutil.copy2(root / "harness.env", restore_root / "harness.env")
        runtime = restore_root / "data/runtime-capital-staging"
        runtime.mkdir(parents=True)
        shutil.copy2(root / "data/runtime-capital-staging/staging.env", runtime / "staging.env")
        backup = restore_root / "paired-backup"
        backup.mkdir(mode=0o700)
        hashes = {}
        for service, dbname, role, name in (("postgres", "mlflow", "mlflow_app", "registry"),
                                           ("postgres-staging", "bikeops", "bike_app", "audit")):
            destination = backup / (name + ".dump")
            with destination.open("xb") as stream:
                completed = subprocess.run(["docker", "exec", ids[service], "pg_dump", "-U", role,
                    "-d", dbname, "-Fc", "--no-owner", "--no-acl"], stdout=stream, stderr=subprocess.PIPE, timeout=60)
            if completed.returncode:
                raise RuntimeError("Owned database dump failed; native details suppressed")
            hashes[name] = hashlib.sha256(destination.read_bytes()).hexdigest()
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
        override = json.loads((restore_root / "harness-override.json").read_text())
        image_ids = {}
        for service in ("mlflow", "api-staging"):
            source_id = h.docker(*harness.compose, "ps", "-a", "-q", service, env=harness.env)
            image = h.docker("inspect", "--format", "{{.Image}}", source_id)
            image_ids[service] = image
            override["services"][service]["image"] = image
        command = json.loads(h.docker(*harness.compose, "config", "--format", "json", env=harness.env))["services"]["mlflow"]["command"]
        command[command.index("--allowed-hosts") + 1] = f"mlflow:5000,127.0.0.1:{ports[0]}"
        override["services"]["mlflow"]["command"] = command
        (restore_root / "harness-override.json").write_text(json.dumps(override))
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
                completed = subprocess.run(["docker", "exec", "-i", container, "pg_restore", "-U", "postgres",
                    "--role", role, "-d", dbname, "--exit-on-error", "--no-owner", "--no-acl"],
                    stdin=stream, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
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
        rejection = h.run_isolated_python(restore_root, env, "restored corrupt skops rejection", """
import json,os
from pathlib import Path
for line in Path('data/runtime-capital-staging/staging.env').read_text().splitlines():
    key,value=line.split('=',1);os.environ[key]=value
from bike_mlops.capital_staging import load_staging_registered_model
try:
    load_staging_registered_model()
except ValueError as error:
    assert 'SHA-256 does not match' in str(error), type(error).__name__
else:
    raise AssertionError('Corrupt restored skops was accepted')
print(json.dumps({'corrupt_restored_skops_rejected':True}))
""", timeout=60)
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
        inspection = h.run_isolated_python(restore_root, env, "read-only restored state inspection", """
import json,os
from pathlib import Path
from bike_mlops.deployment_inspection import inspect_state
path=Path('data/legacy-state-inspection.json')
path.write_text(json.dumps(dict(active_version='1',active_slot='blue',last_deployment_id='synthetic-bootstrap',
    data_profile_id='capital-bikeshare',target_contract_id='capital-trip-starts-hourly-v1')))
legacy=inspect_state(path,os.environ['DATABASE_URL'],'capital-bikeshare','capital-trip-starts-hourly-v1')
foreign=inspect_state(path,os.environ['DATABASE_URL'],'uci-bike-sharing','uci-bike-sharing-cnt-dataset-local-hour')
assert legacy['status']=='legacy_unverifiable' and foreign['status']=='database_profile_mismatch'
assert legacy['automatic_repair'] is False and foreign['automatic_repair'] is False
print(json.dumps({'legacy':legacy['status'],'foreign_profile':foreign['status'],'automatic_repair':False}))
""", timeout=45)
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
                    shutil.rmtree(restore_root)
            except BaseException:
                summary["status"] = "workspace_cleanup_failed"
                summary["cleanup_complete"] = False
                raise
            finally:
                summary["workspace_removed"] = not restore_root.exists()
                print(json.dumps({"paired_restore": summary}), flush=True)
    return summary
