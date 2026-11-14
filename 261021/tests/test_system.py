"""Contract and regression tests: test_system."""
from __future__ import annotations


# ========================================================================
# test_capital_synthetic_fixture
# ========================================================================

import subprocess
import sys
import unittest
import importlib.util
import tempfile
from pathlib import Path
from unittest.mock import patch


class SyntheticFixtureSafetyTests(unittest.TestCase):
    def test_child_refuses_to_run_in_the_real_project(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/system_harness.py'
        result = subprocess.run([sys.executable, str(script), "fixture", "--internal-child"], capture_output=True,
                                text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("only run in its disposable marked workspace", result.stderr)

    def test_interrupted_child_cleans_temporary_workspace(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/system_harness.py'
        spec = importlib.util.spec_from_file_location("synthetic_fixture_for_test", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        create = tempfile.TemporaryDirectory
        paths = []
        def temporary(*args, **kwargs):
            item = create(*args, **kwargs)
            paths.append(Path(item.name))
            return item
        with (patch.object(sys, "path", [str(script.parent), *sys.path]),
              patch.object(sys, "argv", [str(script)]),
              patch.object(module._capital_synthetic_fixture_tempfile, "TemporaryDirectory", side_effect=temporary),
              patch("subprocess.run", side_effect=KeyboardInterrupt)):
            with self.assertRaises(KeyboardInterrupt):
                module._capital_synthetic_fixture_main()
        self.assertEqual(len(paths), 1)
        self.assertFalse(paths[0].exists())


# ========================================================================
# test_capital_system_harness
# ========================================================================

import errno
import importlib.util
import importlib as _capital_system_harness_importlib
import io
import json
import tempfile as _capital_system_harness_tempfile
import time
import types
import unittest as _capital_system_harness_unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path as _capital_system_harness_Path
from unittest.mock import Mock, patch as _capital_system_harness_patch

SCRIPT = _capital_system_harness_Path(__file__).resolve().parents[1] / 'scripts/system_harness.py'
spec = _capital_system_harness_importlib.util.spec_from_file_location("capital_system_harness_for_tests", SCRIPT)
harness = _capital_system_harness_importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


class DisposableHarnessTests(_capital_system_harness_unittest.TestCase):
    def test_shared_compose_image_ids_are_deduplicated_before_cleanup(self):
        self.assertEqual(harness.unique_resource_ids("sha256:abc\nsha256:abc\n\nsha256:def\n"),
                         ["sha256:abc", "sha256:def"])

    def test_docker_image_records_remove_exact_owned_tags_for_shared_id(self):
        project = "bikemlopstest" + "a" * 16
        records = harness.parse_docker_image_records("\n".join([
            json.dumps({"ID": "sha256:abc", "Repository": f"{project}-mlflow", "Tag": "latest"}),
            json.dumps({"ID": "sha256:abc", "Repository": f"{project}-api-staging", "Tag": "test"}),
            json.dumps({"ID": "sha256:abc", "Repository": f"{project}-mlflow", "Tag": "latest"}),
            json.dumps({"ID": "sha256:def", "Repository": "<none>", "Tag": "<none>"}),
        ]))
        self.assertEqual(harness.unique_resource_ids("\n".join(record["ID"] for record in records)),
                         ["sha256:abc", "sha256:def"])
        self.assertEqual(harness.unique_owned_image_references(records, project),
                         [f"{project}-mlflow:latest", f"{project}-api-staging:test"])

    def test_image_cleanup_rejects_repository_reference_outside_project(self):
        project = "bikemlopstest" + "a" * 16
        records = [{"ID": "sha256:abc", "Repository": "unrelated/application", "Tag": "latest"}]
        with self.assertRaisesRegex(harness.HarnessError, "outside this disposable project"):
            harness.unique_owned_image_references(records, project)

    def test_cleanup_removes_owned_tags_without_using_shared_image_ids(self):
        project = "bikemlopstest" + "a" * 16
        instance = harness.Harness(_capital_system_harness_Path("/tmp/test-workspace"), project, "b" * 32)
        image_rows = "\n".join([
            json.dumps({"ID": "sha256:abc", "Repository": f"{project}-mlflow", "Tag": "latest"}),
            json.dumps({"ID": "sha256:abc", "Repository": f"{project}-api-staging", "Tag": "test"}),
        ])
        with _capital_system_harness_patch.object(instance, "resources", return_value={
            "container": [], "network": [], "volume": [], "image": []
        }), _capital_system_harness_patch.object(harness, "docker", side_effect=[
            image_rows, json.dumps({harness.LABEL: instance.token}), ""
        ]) as command:
            instance.cleanup()
        self.assertEqual(command.call_count, 3)
        self.assertEqual(command.call_args_list[-1].args,
                         ("image", "rm", f"{project}-mlflow:latest", f"{project}-api-staging:test"))
        self.assertNotIn("sha256:abc", command.call_args_list[-1].args)

    def test_cleanup_fails_if_owned_resources_remain_after_removal(self):
        project = "bikemlopstest" + "a" * 16
        instance = harness.Harness(_capital_system_harness_Path("/tmp/test-workspace"), project, "b" * 32)
        image_rows = json.dumps({"ID": "sha256:abc", "Repository": f"{project}-mlflow", "Tag": "latest"})
        empty = {"container": [], "network": [], "volume": [], "image": []}
        remaining = {"container": [], "network": [], "volume": [], "image": ["sha256:abc"]}
        with _capital_system_harness_patch.object(instance, "resources", side_effect=[empty, remaining]), \
             _capital_system_harness_patch.object(harness, "docker", side_effect=[
                 image_rows, json.dumps({harness.LABEL: instance.token}), ""
             ]) as command:
            with self.assertRaisesRegex(harness.HarnessError, "remain after cleanup"):
                instance.cleanup()
        self.assertEqual(command.call_args_list[-1].args, ("image", "rm", f"{project}-mlflow:latest"))

    def test_json_result_is_parsed_after_prefix_logs(self):
        value = harness.parse_json_object_with_log_prefix('worker ready\n{"status":"ok","count":2}\n')
        self.assertEqual(value, {"status": "ok", "count": 2})

    def test_child_diagnostics_redact_environment_secrets(self):
        diagnostic = harness.safe_child_diagnostic(
            "connected with top-secret", "password=top-secret", {"APP_DB_PASSWORD": "top-secret"}
        )
        self.assertNotIn("top-secret", diagnostic)
        self.assertEqual(diagnostic.count("[REDACTED]"), 2)

    def test_copy_is_current_build_source_and_excludes_local_data(self):
        with _capital_system_harness_tempfile.TemporaryDirectory() as directory:
            root = _capital_system_harness_Path(directory)
            harness.copy_workspace(harness.SOURCE_ROOT, root)
            self.assertEqual((root / "Dockerfile").read_bytes(), (harness.SOURCE_ROOT / "Dockerfile").read_bytes())
            self.assertTrue((root / "configs/data-profiles").is_dir())
            self.assertFalse((root / "data").exists())
            self.assertFalse((root / ".env").exists())
            self.assertFalse((root / ".git").exists())
            self.assertFalse((root / ".venv").exists())

    def test_copy_rejects_symlink_in_sources(self):
        with _capital_system_harness_tempfile.TemporaryDirectory() as source, _capital_system_harness_tempfile.TemporaryDirectory() as target:
            source_path = _capital_system_harness_Path(source)
            for name in ("Dockerfile", ".dockerignore", "README.md", "pyproject.toml", "requirements.lock", "compose.yaml", "compose.capital.yaml", "compose.capital-staging.yaml"):
                (source_path / name).write_text("test")
            for name in ("src", "configs/data-profiles", "docker"):
                (source_path / name).mkdir(parents=True)
            (source_path / "src/foreign").symlink_to("/etc/passwd")
            with self.assertRaisesRegex(harness.HarnessError, "Symlinks"):
                harness.copy_workspace(source_path, _capital_system_harness_Path(target))

    def test_copy_rejects_source_changed_during_copy(self):
        with _capital_system_harness_tempfile.TemporaryDirectory() as source, _capital_system_harness_tempfile.TemporaryDirectory() as target:
            source_path = _capital_system_harness_Path(source)
            for name in ("Dockerfile", ".dockerignore", "README.md", "pyproject.toml", "requirements.lock", "compose.yaml", "compose.capital.yaml", "compose.capital-staging.yaml"):
                (source_path / name).write_text("original")
            for name in ("src", "configs/data-profiles", "docker"):
                (source_path / name).mkdir(parents=True)
            original_copy = harness.shutil.copy2
            changed = False

            def copy_and_change(src, dst, **kwargs):
                nonlocal changed
                result = original_copy(src, dst, **kwargs)
                if not changed:
                    (source_path / "Dockerfile").write_text("changed")
                    changed = True
                return result

            with _capital_system_harness_patch.object(harness.shutil, "copy2", side_effect=copy_and_change):
                with self.assertRaisesRegex(harness.HarnessError, "changed while preparing"):
                    harness.copy_workspace(source_path, _capital_system_harness_Path(target))

    def test_occupied_port_rejected_without_docker(self):
        port = 54321
        probe = Mock()
        probe.__enter__ = Mock(return_value=probe)
        probe.__exit__ = Mock(return_value=False)
        probe.bind.side_effect = OSError(errno.EADDRINUSE, "simulated occupied port")
        with _capital_system_harness_patch.object(harness.socket, "socket", return_value=probe), _capital_system_harness_patch.object(harness, "docker") as docker_mock:
            with self.assertRaisesRegex(harness.HarnessError, f"port {port}"):
                harness.check_ports_free((port,))
            docker_mock.assert_not_called()

    def test_port_permission_error_is_not_reported_as_in_use(self):
        port = 54321
        probe = Mock()
        probe.__enter__ = Mock(return_value=probe)
        probe.__exit__ = Mock(return_value=False)
        probe.bind.side_effect = PermissionError(errno.EPERM, "sandbox blocked bind")
        with _capital_system_harness_patch.object(harness.socket, "socket", return_value=probe):
            with self.assertRaisesRegex(harness.HarnessError, "Could not check local port") as caught:
                harness.check_ports_free((port,))
        self.assertNotIn("already in use", str(caught.exception))

    def test_compose_override_and_credentials_are_isolated(self):
        with _capital_system_harness_tempfile.TemporaryDirectory() as directory:
            root = _capital_system_harness_Path(directory)
            path = harness.make_override(root, "f" * 32)
            value = json.loads(path.read_text())
            self.assertEqual(value["services"]["mlflow"]["build"]["labels"][harness.LABEL], "f" * 32)
            self.assertEqual(value["volumes"]["postgres-data"]["labels"][harness.LABEL], "f" * 32)
            harness.write_synthetic_credentials(root)
            self.assertEqual((root / "harness.env").stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                harness.write_synthetic_credentials(root)
        with _capital_system_harness_patch.dict(harness.os.environ, {"APP_DB_PASSWORD": "external", "COMPOSE_PROJECT_NAME": "external"}):
            env = harness.compose_environment()
            self.assertNotIn("APP_DB_PASSWORD", env)
            self.assertNotIn("COMPOSE_PROJECT_NAME", env)

    def test_cleanup_rejects_unowned_resources_before_down(self):
        instance = harness.Harness(_capital_system_harness_Path("/tmp/test-workspace"), "bikemlopstest" + "a" * 16, "b" * 32)
        with _capital_system_harness_patch.object(instance, "resources", return_value={"container": ["foreign"], "volume": [], "network": []}), \
             _capital_system_harness_patch.object(harness, "docker", return_value=json.dumps({"com.docker.compose.project": instance.project})) as command:
            with self.assertRaisesRegex(harness.HarnessError, "not owned"):
                instance.cleanup()
            self.assertEqual(command.call_count, 1)
            self.assertNotIn("down", command.call_args.args)

    def test_owned_resources_only_and_unexpected_service_rejected(self):
        instance = harness.Harness(_capital_system_harness_Path("/tmp/test-workspace"), "bikemlopstest" + "a" * 16, "b" * 32)
        labels = {"com.docker.compose.project": instance.project, harness.LABEL: instance.token,
                  "com.docker.compose.service": "api-blue"}
        with _capital_system_harness_patch.object(harness, "docker", return_value=json.dumps(labels)) as command:
            with self.assertRaisesRegex(harness.HarnessError, "unexpected Compose service"):
                instance.assert_owned({"container": ["id"]})
            labels["com.docker.compose.service"] = "mlflow"
            command.return_value = json.dumps(labels)
            instance.assert_owned({"container": ["id"]})

    def test_default_mode_copies_but_never_invokes_docker(self):
        with _capital_system_harness_patch.object(harness, "docker") as command, _capital_system_harness_patch("sys.argv", ["harness"]), redirect_stdout(io.StringIO()) as output:
            harness.main()
        self.assertFalse(json.loads(output.getvalue())["docker_started"])
        command.assert_not_called()

    def test_synthetic_option_runs_in_isolation_before_infrastructure(self):
        events = []
        with _capital_system_harness_patch.object(harness, "prepare_synthetic_workspace", side_effect=lambda _: events.append("synthetic") or {"status": "passed", "data": "synthetic"}), \
             _capital_system_harness_patch("sys.argv", ["harness", "--prepare-synthetic", "--run-infrastructure"]), \
             _capital_system_harness_patch.object(harness, "check_local_daemon"), _capital_system_harness_patch.object(harness, "check_ports_free"), \
             _capital_system_harness_patch.object(harness.Harness, "assert_empty"), _capital_system_harness_patch.object(harness.Harness, "cleanup"), \
             _capital_system_harness_patch.object(harness.Harness, "verify_ports"), _capital_system_harness_patch.object(harness.Harness, "verify_profiles"), _capital_system_harness_patch.object(harness, "wait_ready"), \
             _capital_system_harness_patch.object(harness, "docker", side_effect=lambda *a, **kw: events.append("docker") or ""), \
             redirect_stdout(io.StringIO()) as output:
            harness.main()
        self.assertEqual(events[0], "synthetic")
        completed = [json.loads(line) for line in output.getvalue().splitlines() if '"status": "infrastructure_ready"' in line]
        self.assertEqual(completed[0]["candidate_generation"], "prepared")

    def test_profile_check_uses_only_owned_container_and_rejects_wrong_profile(self):
        instance = harness.Harness(_capital_system_harness_Path("/tmp/test-workspace"), "bikemlopstest" + "a" * 16, "b" * 32)
        answers = ["container-id", json.dumps(["uci-bike-sharing", "uci-bike-sharing-cnt-dataset-local-hour", "bike-demand"]),
                   json.dumps(["capital-bikeshare", "capital-trip-starts-hourly-v1", "bike-demand-capital"])]
        with _capital_system_harness_patch.object(instance, "assert_owned") as owned, _capital_system_harness_patch.object(harness, "docker", side_effect=answers) as command:
            instance.verify_profiles()
            owned.assert_called_once_with({"container": ["container-id"]})
            self.assertEqual(command.call_count, 3)
        with _capital_system_harness_patch.object(instance, "assert_owned"), _capital_system_harness_patch.object(harness, "docker", side_effect=["container-id", "[]"]):
            with self.assertRaisesRegex(harness.HarnessError, "unexpected data profile"):
                instance.verify_profiles()

    def test_failed_compose_up_still_attempts_cleanup(self):
        def operation(*args, **kwargs):
            if "up" in args:
                raise harness.HarnessError("simulated up failure")
            return ""
        with _capital_system_harness_patch("sys.argv", ["harness", "--run-infrastructure"]), \
             _capital_system_harness_patch.object(harness, "check_local_daemon"), _capital_system_harness_patch.object(harness, "check_ports_free"), \
             _capital_system_harness_patch.object(harness.Harness, "assert_empty"), _capital_system_harness_patch.object(harness.Harness, "cleanup") as cleanup, \
             _capital_system_harness_patch.object(harness, "docker", side_effect=operation), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(harness.HarnessError, "simulated up failure"):
                harness.main()
        cleanup.assert_called_once_with()

    def test_cleanup_failure_preserves_owned_workspace(self):
        with _capital_system_harness_tempfile.TemporaryDirectory() as directory:
            root = _capital_system_harness_Path(directory) / "isolated"
            root.mkdir()
            with _capital_system_harness_patch("sys.argv", ["harness", "--run-infrastructure"]), \
                 _capital_system_harness_patch.object(harness.tempfile, "mkdtemp", return_value=str(root)), \
                 _capital_system_harness_patch.object(harness, "check_local_daemon"), _capital_system_harness_patch.object(harness, "check_ports_free"), \
                 _capital_system_harness_patch.object(harness.Harness, "assert_empty"), \
                 _capital_system_harness_patch.object(harness.Harness, "cleanup", side_effect=harness.HarnessError("cleanup failed")), \
                 _capital_system_harness_patch.object(harness, "docker", return_value=""), \
                 _capital_system_harness_patch.object(harness.Harness, "verify_ports", side_effect=harness.HarnessError("verification failed")), \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as stderr:
                with self.assertRaisesRegex(harness.HarnessError, "cleanup failed"):
                    harness.main()
            self.assertTrue(root.is_dir())
            self.assertIn("workspace retained", stderr.getvalue())

    def test_docker_failure_does_not_print_subprocess_output(self):
        result = types.SimpleNamespace(returncode=1, stdout="secret", stderr="secret")
        with _capital_system_harness_patch.object(harness.subprocess, "run", return_value=result):
            with self.assertRaises(harness.HarnessError) as caught:
                harness.docker("compose", "config")
        self.assertNotIn("secret", str(caught.exception))
        self.assertIn("underlying cause not determined", str(caught.exception))

    def test_image_remove_failure_diagnostic_redacts_environment_secrets(self):
        result = types.SimpleNamespace(returncode=1, stdout="", stderr="conflict for local-secret")
        with _capital_system_harness_patch.object(harness.subprocess, "run", return_value=result):
            with self.assertRaises(harness.HarnessError) as caught:
                harness.docker("image", "rm", "sha256:synthetic", env={"APP_DB_PASSWORD": "local-secret"})
        self.assertIn("conflict", str(caught.exception))
        self.assertNotIn("local-secret", str(caught.exception))
        self.assertIn("[REDACTED]", str(caught.exception))

    def test_build_heartbeat_reports_elapsed_without_command_or_environment(self):
        def slow_command(*args, **kwargs):
            time.sleep(0.055)
            return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")
        with _capital_system_harness_patch.object(harness.subprocess, "run", side_effect=slow_command), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(harness.docker("compose", "secret-argument", env={"SECRET": "secret-value"},
                                            progress="build_and_start", progress_interval=0.01), "ok")
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertTrue(events)
        self.assertTrue(all(event["stage"] == "build_and_start" and event["elapsed_seconds"] >= 0 for event in events))
        self.assertNotIn("secret", output.getvalue())


# ========================================================================
# test_capital_paired_restore
# ========================================================================

"""Content mismatches are rejected independently of Docker happy-path checks."""
import tempfile as _capital_paired_restore_tempfile
import unittest as _capital_paired_restore_unittest
from unittest.mock import patch as _capital_paired_restore_patch
from pathlib import Path as _capital_paired_restore_Path

from scripts.system_harness import require_equal, tree_manifest


class PairedRestoreTests(_capital_paired_restore_unittest.TestCase):
    def test_missing_artifacts_or_estimator_is_not_a_model_backup(self):
        with _capital_paired_restore_tempfile.TemporaryDirectory() as directory:
            root = _capital_paired_restore_Path(directory)
            with self.assertRaisesRegex(ValueError, "empty|MLmodel"):
                tree_manifest(root)
            model = root / "model"
            model.mkdir()
            (model / "MLmodel").write_text("fixture")
            with self.assertRaisesRegex(ValueError, "skops"):
                tree_manifest(root)

    def test_hash_bytes_names_and_extra_files_change_the_pair(self):
        with _capital_paired_restore_tempfile.TemporaryDirectory() as directory:
            root = _capital_paired_restore_Path(directory)
            (root / "model").mkdir()
            (root / "model/MLmodel").write_text("fixture")
            path = root / "model/model.skops"
            path.write_bytes(b"original")
            original = tree_manifest(root)
            path.write_bytes(b"modified")
            with self.assertRaisesRegex(ValueError, "artifact"):
                require_equal(original, tree_manifest(root), "artifact")
            path.write_bytes(b"original")
            (root / "extra").write_text("unexpected")
            with self.assertRaises(ValueError):
                require_equal(original, tree_manifest(root), "extra member")

    def test_artifact_symlink_is_rejected(self):
        with _capital_paired_restore_tempfile.TemporaryDirectory() as directory:
            root = _capital_paired_restore_Path(directory)
            (root / "outside").symlink_to("/etc/passwd")
            with self.assertRaisesRegex(ValueError, "regular"):
                tree_manifest(root)

    def test_same_row_count_with_changed_values_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "tables"):
            require_equal({"predictions": {"rows": 10, "sha256": "a"}},
                          {"predictions": {"rows": 10, "sha256": "b"}}, "tables")

    def test_restore_opt_in_and_port_maps_are_checked_before_resources(self):
        from scripts import system_harness as h
        with _capital_paired_restore_patch("sys.argv", ["harness", "--verify-paired-restore"]), _capital_paired_restore_patch.object(h, "docker") as docker:
            with self.assertRaises(SystemExit) as error:
                h.main()
            self.assertEqual(error.exception.code, 2)
            docker.assert_not_called()
        for ports in ((1, 1, 2, 3), (0, 1, 2, 3), (1, 2, 3, 65536)):
            with self.assertRaisesRegex(ValueError, "port map"):
                h.Harness(_capital_paired_restore_Path("/tmp/uncreated"), "bikemlopstest" + "a"*16, "b"*32, ports=ports)

    def test_protected_inventory_includes_only_existing_project_names(self):
        from scripts import system_harness as h
        with _capital_paired_restore_patch.object(h, "docker", return_value="bike-mlops-postgres-1 id1\nbikemlopstest123-mlflow-1 id2\nother-api id3"):
            self.assertEqual(h.protected_container_ids(), ["bike-mlops-postgres-1 id1"])


# ========================================================================
# test_capital_pipeline
# ========================================================================

import json as _capital_pipeline_json
import os
import sqlite3
import tempfile as _capital_pipeline_tempfile
import unittest as _capital_pipeline_unittest
import zipfile
from pathlib import Path as _capital_pipeline_Path

import pandas as pd

from bike_mlops.data import validate_capital_manifest
from scripts.data_tools import aggregate_archives, _has_conflicting_start_hours


class CapitalAggregationContractTests(_capital_pipeline_unittest.TestCase):
    def setUp(self):
        self.temp = _capital_pipeline_tempfile.TemporaryDirectory()
        self.root = _capital_pipeline_Path(self.temp.name)
        self.raw = self.root / "raw"
        self.output = self.root / "output"
        self.raw.mkdir()
        self.output.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def write_archive(self, month: str, rows: list[tuple[str, str]]):
        archive_path = self.raw / f"{month}-capitalbikeshare-tripdata.zip"
        member = f"trips-{month}.csv"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            content = "ride_id,started_at\n" + "".join(f"{ride_id},{started_at}\n" for ride_id, started_at in rows)
            archive.writestr(member, content)
        return archive_path

    def test_conflict_probe_scales_with_current_chunk_and_keeps_exact_hour_rejection(self):
        instructions = []
        for size in (1000, 100000):
            with sqlite3.connect(":memory:") as connection:
                connection.execute("CREATE TABLE seen_trip_ids(ride_id TEXT PRIMARY KEY, started_at TEXT NOT NULL)")
                connection.execute("CREATE TEMP TABLE current_chunk(ride_id TEXT PRIMARY KEY, started_at TEXT NOT NULL)")
                connection.executemany("INSERT INTO seen_trip_ids VALUES(?,?)",
                                       ((f"old-{i:08d}", "2024-01-01 00:00:00") for i in range(size)))
                connection.executemany("INSERT INTO current_chunk VALUES(?,?)",
                                       ((f"new-{i:08d}", "2024-01-01 00:00:00") for i in range(10)))
                count = [0]
                def progress():
                    count[0] += 1
                    return 0
                connection.set_progress_handler(progress, 1)
                self.assertFalse(_has_conflicting_start_hours(connection))
                connection.set_progress_handler(None, 0)
                instructions.append(count[0])
                connection.execute("INSERT INTO current_chunk VALUES('old-00000000','2024-01-01 00:00:00')")
                self.assertFalse(_has_conflicting_start_hours(connection))
                connection.execute("UPDATE current_chunk SET started_at='2024-01-01 01:00:00' WHERE ride_id='old-00000000'")
                self.assertTrue(_has_conflicting_start_hours(connection))
                connection.execute("DELETE FROM current_chunk")
                self.assertFalse(_has_conflicting_start_hours(connection))
        # Bound VM work rather than unqualified wall-clock durations or plan text.
        self.assertLess(instructions[1], instructions[0] * 4 + 1000)
        self.assertLess(instructions[1], 5000)

    def test_row_accounting_dst_and_manifest_hash_are_consistent(self):
        self.write_archive(
            "202403",
            [
                ("a", "2024-03-09 00:10:00"),
                ("a", "2024-03-09 00:45:00"),
                ("spring-gap", "2024-03-10 02:15:00"),
                ("valid", "2024-03-10 03:30:00"),
                ("outside", "2024-03-11 00:00:00"),
            ],
        )
        csv_path = self.output / "hourly.csv"
        manifest_path = self.output / "manifest.json"
        report = aggregate_archives(
            ["202403"],
            pd.Timestamp("2024-03-09 00:00:00"),
            pd.Timestamp("2024-03-11 00:00:00"),
            csv_path,
            manifest_path,
            raw_dir=self.raw,
        )
        self.assertEqual(report["source_rows_read"], 5)
        self.assertEqual(report["duplicate_rows_skipped"], 1)
        self.assertEqual(report["unique_ride_ids"], 4)
        self.assertEqual(report["unique_out_of_window"], 1)
        self.assertEqual(report["unique_dst_excluded"], 1)
        self.assertEqual(report["output_trip_count"], 2)
        self.assertEqual(report["output_rows"], 47)
        self.assertEqual(report["audit"]["data_profile_id"], "capital-bikeshare")
        self.assertEqual(report["audit"]["target_contract_id"], "capital-trip-starts-hourly-v1")
        self.assertIn("monthly publication latency is not measured", report["audit"]["label_available_at_rule"])
        self.assertEqual(report["timezone_database"]["provider"], "python-tzdata-package")
        self.assertEqual(report["timezone_database"]["package_version"], "2026.5")
        self.assertEqual(report["manifest_schema_version"], 3)
        manifest, frame = validate_capital_manifest(_capital_pipeline_json.loads(manifest_path.read_text()), csv_path, self.raw)
        self.assertEqual(manifest["data_sha256"], report["derived_csv_sha256"])
        self.assertEqual(int(frame["cnt"].sum()), 2)

        wrong_timezone_manifest = _capital_pipeline_json.loads(manifest_path.read_text())
        wrong_timezone_manifest["timezone_database"]["zone_file_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "timezone database identity"):
            validate_capital_manifest(wrong_timezone_manifest, csv_path, self.raw)

    def test_archive_months_must_be_contiguous_and_cover_requested_range(self):
        with self.assertRaisesRegex(ValueError, "sorted and contiguous"):
            aggregate_archives(
                ["202401", "202403"],
                pd.Timestamp("2024-01-01"),
                pd.Timestamp("2024-04-01"),
                self.output / "x.csv",
                raw_dir=self.raw,
            )
        with self.assertRaisesRegex(ValueError, "do not cover"):
            aggregate_archives(
                ["202403"],
                pd.Timestamp("2024-02-01"),
                pd.Timestamp("2024-04-01"),
                self.output / "y.csv",
                raw_dir=self.raw,
            )

    def test_multiple_unique_trips_in_one_hour_keep_weights_after_id_dedup(self):
        for month, start, end, valid_hour, dst_hour, next_hour, before, after in (
            ("202403", "2024-03-09", "2024-03-11", "2024-03-09 00", "2024-03-10 02", "2024-03-10 03", "2024-03-08 23", "2024-03-11 00"),
            ("202411", "2024-11-02", "2024-11-04", "2024-11-02 00", "2024-11-03 01", "2024-11-03 03", "2024-11-01 23", "2024-11-04 00"),
        ):
            with self.subTest(month=month):
                rows = []
                for name, hour, count in (("before", before, 4), ("valid", valid_hour, 7),
                                          ("next", next_hour, 3), ("dst", dst_hour, 5), ("after", after, 6)):
                    rows.extend((f"{name}-{i}", hour + ":15:00") for i in range(count))
                rows.append(("valid-0", valid_hour + ":59:00"))
                self.write_archive(month, rows)
                csv_path = self.output / f"{month}.csv"
                manifest_path = self.output / f"{month}.json"
                report = aggregate_archives([month], pd.Timestamp(start), pd.Timestamp(end),
                                            csv_path, manifest_path, raw_dir=self.raw)
                self.assertEqual(report["source_rows_read"], 26)
                self.assertEqual(report["duplicate_rows_skipped"], 1)
                self.assertEqual(report["unique_ride_ids"], 25)
                self.assertEqual(report["unique_out_of_window"], 10)
                self.assertEqual(report["unique_dst_excluded"], 5)
                self.assertEqual(report["output_trip_count"], 10)
                _, frame = validate_capital_manifest(report, csv_path, self.raw)
                actual = frame.set_index("timestamp")["cnt"]
                self.assertEqual(actual[pd.Timestamp(valid_hour + ":00:00")], 7)
                self.assertEqual(actual[pd.Timestamp(next_hour + ":00:00")], 3)
                self.assertNotIn(pd.Timestamp(dst_hour + ":00:00"), actual.index)

    def test_empty_archive_is_rejected_and_does_not_create_output_pair(self):
        self.write_archive("202401", [])
        csv_path = self.output / "hourly.csv"
        manifest_path = self.output / "manifest.json"
        with self.assertRaisesRegex(ValueError, "contains no trip rows"):
            aggregate_archives(
                ["202401"],
                pd.Timestamp("2024-01-01"),
                pd.Timestamp("2024-02-01"),
                csv_path,
                manifest_path,
                raw_dir=self.raw,
            )
        self.assertFalse(csv_path.exists())
        self.assertFalse(manifest_path.exists())

    def test_csv_and_manifest_cannot_share_an_output_path(self):
        with self.assertRaisesRegex(ValueError, "different output paths"):
            aggregate_archives(
                ["202401"],
                pd.Timestamp("2024-01-01"),
                pd.Timestamp("2024-02-01"),
                self.output / "same.json",
                self.output / "same.json",
                raw_dir=self.raw,
            )

    def test_stale_lock_is_recovered_but_live_lock_is_not_stolen(self):
        self.write_archive("202401", [("a", "2024-01-01 00:00:00"), ("b", "2024-01-31 23:00:00")])
        csv_path = self.output / "hourly.csv"
        manifest_path = self.output / "manifest.json"
        lock_path = self.output / ".hourly.csv.build.lock"
        lock_path.write_text("pid=999999999\n")
        aggregate_archives(
            ["202401"], pd.Timestamp("2024-01-01"), pd.Timestamp("2024-02-01"),
            csv_path, manifest_path, raw_dir=self.raw,
        )
        self.assertFalse(lock_path.exists())

        other_csv = self.output / "second.csv"
        other_lock = self.output / ".second.csv.build.lock"
        fd = os.open(other_lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, f"pid={os.getpid()}\n".encode())
            with self.assertRaisesRegex(FileExistsError, "actively holding"):
                aggregate_archives(
                    ["202401"], pd.Timestamp("2024-01-01"), pd.Timestamp("2024-02-01"),
                    other_csv, self.output / "second-manifest.json", raw_dir=self.raw,
                )
        finally:
            os.close(fd)
            other_lock.unlink(missing_ok=True)

    def test_conflicting_ride_id_and_existing_outputs_are_rejected(self):
        self.write_archive(
            "202401",
            [("same", "2024-01-01 01:00:00"), ("same", "2024-01-01 02:00:00")],
        )
        csv_path = self.output / "hourly.csv"
        manifest_path = self.output / "manifest.json"
        with self.assertRaisesRegex(ValueError, "conflicting start hours"):
            aggregate_archives(
                ["202401"],
                pd.Timestamp("2024-01-01"),
                pd.Timestamp("2024-02-01"),
                csv_path,
                manifest_path,
                raw_dir=self.raw,
            )
        csv_path.write_text("preserve me\n")
        with self.assertRaisesRegex(FileExistsError, "Refusing to overwrite"):
            aggregate_archives(
                ["202401"],
                pd.Timestamp("2024-01-01"),
                pd.Timestamp("2024-02-01"),
                csv_path,
                manifest_path,
                raw_dir=self.raw,
            )
        self.assertEqual(csv_path.read_text(), "preserve me\n")

    def test_duplicate_ride_id_across_month_boundaries_is_counted_once(self):
        self.write_archive(
            "202401",
            [("jan-start", "2024-01-31 00:00:00"), ("boundary", "2024-01-31 23:20:00")],
        )
        self.write_archive(
            "202402",
            [
                ("boundary", "2024-01-31 23:55:00"),
                ("feb-start", "2024-02-01 00:00:00"),
                ("feb-end", "2024-02-01 23:00:00"),
            ],
        )
        report = aggregate_archives(
            ["202401", "202402"],
            pd.Timestamp("2024-01-31"),
            pd.Timestamp("2024-02-02"),
            self.output / "hourly.csv",
            raw_dir=self.raw,
        )
        self.assertEqual(report["source_rows_read"], 5)
        self.assertEqual(report["duplicate_rows_skipped"], 1)
        self.assertEqual(report["unique_ride_ids"], 4)
        self.assertEqual(report["output_trip_count"], 4)

    def test_manifest_rejects_non_object_with_actionable_error(self):
        with self.assertRaisesRegex(ValueError, "must be a JSON object"):
            validate_capital_manifest([1], self.root / "missing.csv")


# ========================================================================
# test_deployment_recovery_system
# ========================================================================

"""Safety boundaries run without Docker or a live database."""
import tempfile as _deployment_recovery_system_tempfile
import unittest as _deployment_recovery_system_unittest
from pathlib import Path as _deployment_recovery_system_Path
from unittest.mock import patch as _deployment_recovery_system_patch

from scripts import runtime_checks as fixture


class DeploymentFixtureSafetyTests(_deployment_recovery_system_unittest.TestCase):
    def setUp(self):
        self.temp = _deployment_recovery_system_tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = _deployment_recovery_system_Path(self.temp.name)
        self.token = "a" * 32
        (root / ".fixture-token").write_text(self.token)
        self.spec = dict(workspace=str(root), token=self.token, prefix="bike-deploy-test-" + "b" * 16,
                         database_url="postgresql://postgres:fixture@127.0.0.1:59999/postgres",
                         internal_database_url="postgresql://postgres:fixture@bike-deploy-test-" + "b" * 16 + "-postgres:5432/postgres", ports={})

    def test_real_project_and_mismatched_marker_are_rejected_before_docker(self):
        with _deployment_recovery_system_patch.object(fixture, "docker") as docker:
            with self.assertRaisesRegex(ValueError, "outside the real project"):
                fixture.Fixture(dict(self.spec, workspace=str(fixture._deployment_recovery_system_ROOT)))
            with self.assertRaisesRegex(ValueError, "marker"):
                fixture.Fixture(dict(self.spec, token="c" * 32))
            docker.assert_not_called()

    def test_invalid_resource_identity_is_rejected(self):
        for key, value in (("prefix", "bike-mlops"), ("token", "short")):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "identity"):
                fixture.Fixture(dict(self.spec, **{key: value}))

    def test_api_cannot_connect_to_a_foreign_database(self):
        f = fixture.Fixture(dict(self.spec, internal_database_url="postgresql://postgres:fixture@postgres:5432/postgres"))
        with _deployment_recovery_system_patch.object(fixture, "docker") as docker, self.assertRaisesRegex(ValueError, "outside"):
            f.api("blue", "2")
        docker.assert_not_called()

    def test_host_endpoint_must_equal_the_owned_container_port(self):
        f = fixture.Fixture(self.spec)
        with _deployment_recovery_system_patch('scripts.system_harness.check_local_daemon'), _deployment_recovery_system_patch.object(f, "owned") as owner, _deployment_recovery_system_patch.object(fixture, "docker", return_value="127.0.0.1:58888"):
            with self.assertRaisesRegex(ValueError, "port does not belong"):
                f.patches()
        owner.assert_called_once_with("container", self.spec["prefix"] + "-postgres")

    def test_unowned_resources_are_never_mutated(self):
        f = fixture.Fixture(self.spec)
        with _deployment_recovery_system_patch.object(fixture, "docker", return_value='{"other":"token"}') as docker:
            with self.assertRaisesRegex(RuntimeError, "outside this fixture"):
                f.owned("container", "unknown")
        self.assertEqual(docker.call_args.args[0], "inspect")

    def test_image_requires_exact_owned_id_not_a_cached_shared_tag(self):
        f = fixture.Fixture(self.spec)
        with self.assertRaisesRegex(ValueError, "image identity"):
            f.verified_image()
        image = "sha256:" + "d"*64
        f = fixture.Fixture(dict(self.spec, image_id=image))
        import json
        for labels, expected in (({}, False), ({fixture.LABEL:self.token}, True)):
            with self.subTest(labels=labels), _deployment_recovery_system_patch.object(fixture, "docker", return_value=json.dumps([
                    {"Id":image,"Config":{"Labels":labels}}])):
                if expected: self.assertEqual(f.verified_image(), image)
                else:
                    with self.assertRaisesRegex(RuntimeError, "unowned"):
                        f.verified_image()

    def test_build_timeout_does_not_claim_cleanup_even_when_inventory_is_empty(self):
        import json, subprocess
        evidence = _deployment_recovery_system_Path(self.temp.name) / "evidence"
        def docker(*args, **kwargs):
            if args[0] == "build":
                raise subprocess.TimeoutExpired(["docker","build"], 600)
            return ""
        with _deployment_recovery_system_patch('scripts.system_harness.check_local_daemon'), \
             _deployment_recovery_system_patch('scripts.system_harness.copy_workspace'), _deployment_recovery_system_patch.object(fixture,"docker",side_effect=docker):
            with self.assertRaisesRegex(RuntimeError, "cleanup"):
                fixture._deployment_recovery_system_main(evidence)
        summary=json.loads((evidence/"summary.json").read_text())
        self.assertTrue(summary["daemon_outcome_unconfirmed"])
        self.assertFalse(summary["cleanup_complete"])


if __name__ == "__main__":
    import unittest as _unittest_entry
    _unittest_entry.main()
