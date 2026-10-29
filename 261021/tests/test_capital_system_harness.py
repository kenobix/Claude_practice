from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import time
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/capital_system_harness.py"
spec = importlib.util.spec_from_file_location("capital_system_harness_for_tests", SCRIPT)
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


class DisposableHarnessTests(unittest.TestCase):
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
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            harness.copy_workspace(harness.SOURCE_ROOT, root)
            self.assertEqual((root / "Dockerfile").read_bytes(), (harness.SOURCE_ROOT / "Dockerfile").read_bytes())
            self.assertTrue((root / "configs/data-profiles").is_dir())
            self.assertFalse((root / "data").exists())
            self.assertFalse((root / ".env").exists())
            self.assertFalse((root / ".git").exists())
            self.assertFalse((root / ".venv").exists())

    def test_copy_rejects_symlink_in_sources(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as target:
            source_path = Path(source)
            for name in ("Dockerfile", ".dockerignore", "README.md", "pyproject.toml", "requirements.lock", "compose.yaml", "compose.capital.yaml", "compose.capital-staging.yaml"):
                (source_path / name).write_text("test")
            for name in ("src", "configs/data-profiles", "docker"):
                (source_path / name).mkdir(parents=True)
            (source_path / "src/foreign").symlink_to("/etc/passwd")
            with self.assertRaisesRegex(harness.HarnessError, "Symlinks"):
                harness.copy_workspace(source_path, Path(target))

    def test_copy_rejects_source_changed_during_copy(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as target:
            source_path = Path(source)
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

            with patch.object(harness.shutil, "copy2", side_effect=copy_and_change):
                with self.assertRaisesRegex(harness.HarnessError, "changed while preparing"):
                    harness.copy_workspace(source_path, Path(target))

    def test_occupied_port_rejected_without_docker(self):
        port = 54321
        probe = Mock()
        probe.__enter__ = Mock(return_value=probe)
        probe.__exit__ = Mock(return_value=False)
        probe.bind.side_effect = OSError("simulated occupied port")
        with patch.object(harness.socket, "socket", return_value=probe), patch.object(harness, "docker") as docker_mock:
            with self.assertRaisesRegex(harness.HarnessError, f"port {port}"):
                harness.check_ports_free((port,))
            docker_mock.assert_not_called()

    def test_compose_override_and_credentials_are_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = harness.make_override(root, "f" * 32)
            value = json.loads(path.read_text())
            self.assertEqual(value["services"]["mlflow"]["build"]["labels"][harness.LABEL], "f" * 32)
            self.assertEqual(value["volumes"]["postgres-data"]["labels"][harness.LABEL], "f" * 32)
            harness.write_synthetic_credentials(root)
            self.assertEqual((root / "harness.env").stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                harness.write_synthetic_credentials(root)
        with patch.dict(harness.os.environ, {"APP_DB_PASSWORD": "external", "COMPOSE_PROJECT_NAME": "external"}):
            env = harness.compose_environment()
            self.assertNotIn("APP_DB_PASSWORD", env)
            self.assertNotIn("COMPOSE_PROJECT_NAME", env)

    def test_cleanup_rejects_unowned_resources_before_down(self):
        instance = harness.Harness(Path("/tmp/test-workspace"), "bikemlopstest" + "a" * 16, "b" * 32)
        with patch.object(instance, "resources", return_value={"container": ["foreign"], "volume": [], "network": []}), \
             patch.object(harness, "docker", return_value=json.dumps({"com.docker.compose.project": instance.project})) as command:
            with self.assertRaisesRegex(harness.HarnessError, "not owned"):
                instance.cleanup()
            self.assertEqual(command.call_count, 1)
            self.assertNotIn("down", command.call_args.args)

    def test_owned_resources_only_and_unexpected_service_rejected(self):
        instance = harness.Harness(Path("/tmp/test-workspace"), "bikemlopstest" + "a" * 16, "b" * 32)
        labels = {"com.docker.compose.project": instance.project, harness.LABEL: instance.token,
                  "com.docker.compose.service": "api-blue"}
        with patch.object(harness, "docker", return_value=json.dumps(labels)) as command:
            with self.assertRaisesRegex(harness.HarnessError, "unexpected Compose service"):
                instance.assert_owned({"container": ["id"]})
            labels["com.docker.compose.service"] = "mlflow"
            command.return_value = json.dumps(labels)
            instance.assert_owned({"container": ["id"]})

    def test_default_mode_copies_but_never_invokes_docker(self):
        with patch.object(harness, "docker") as command, patch("sys.argv", ["harness"]), redirect_stdout(io.StringIO()) as output:
            harness.main()
        self.assertFalse(json.loads(output.getvalue())["docker_started"])
        command.assert_not_called()

    def test_synthetic_option_runs_in_isolation_before_infrastructure(self):
        events = []
        with patch.object(harness, "prepare_synthetic_workspace", side_effect=lambda _: events.append("synthetic") or {"status": "passed", "data": "synthetic"}), \
             patch("sys.argv", ["harness", "--prepare-synthetic", "--run-infrastructure"]), \
             patch.object(harness, "check_local_daemon"), patch.object(harness, "check_ports_free"), \
             patch.object(harness.Harness, "assert_empty"), patch.object(harness.Harness, "cleanup"), \
             patch.object(harness.Harness, "verify_ports"), patch.object(harness.Harness, "verify_profiles"), patch.object(harness, "wait_ready"), \
             patch.object(harness, "docker", side_effect=lambda *a, **kw: events.append("docker") or ""), \
             redirect_stdout(io.StringIO()) as output:
            harness.main()
        self.assertEqual(events[0], "synthetic")
        completed = [json.loads(line) for line in output.getvalue().splitlines() if '"status": "infrastructure_ready"' in line]
        self.assertEqual(completed[0]["candidate_generation"], "prepared")

    def test_profile_check_uses_only_owned_container_and_rejects_wrong_profile(self):
        instance = harness.Harness(Path("/tmp/test-workspace"), "bikemlopstest" + "a" * 16, "b" * 32)
        answers = ["container-id", json.dumps(["uci-bike-sharing", "uci-bike-sharing-cnt-dataset-local-hour", "bike-demand"]),
                   json.dumps(["capital-bikeshare", "capital-trip-starts-hourly-v1", "bike-demand-capital"])]
        with patch.object(instance, "assert_owned") as owned, patch.object(harness, "docker", side_effect=answers) as command:
            instance.verify_profiles()
            owned.assert_called_once_with({"container": ["container-id"]})
            self.assertEqual(command.call_count, 3)
        with patch.object(instance, "assert_owned"), patch.object(harness, "docker", side_effect=["container-id", "[]"]):
            with self.assertRaisesRegex(harness.HarnessError, "unexpected data profile"):
                instance.verify_profiles()

    def test_failed_compose_up_still_attempts_cleanup(self):
        def operation(*args, **kwargs):
            if "up" in args:
                raise harness.HarnessError("simulated up failure")
            return ""
        with patch("sys.argv", ["harness", "--run-infrastructure"]), \
             patch.object(harness, "check_local_daemon"), patch.object(harness, "check_ports_free"), \
             patch.object(harness.Harness, "assert_empty"), patch.object(harness.Harness, "cleanup") as cleanup, \
             patch.object(harness, "docker", side_effect=operation), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(harness.HarnessError, "simulated up failure"):
                harness.main()
        cleanup.assert_called_once_with()

    def test_cleanup_failure_preserves_owned_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "isolated"
            root.mkdir()
            with patch("sys.argv", ["harness", "--run-infrastructure"]), \
                 patch.object(harness.tempfile, "mkdtemp", return_value=str(root)), \
                 patch.object(harness, "check_local_daemon"), patch.object(harness, "check_ports_free"), \
                 patch.object(harness.Harness, "assert_empty"), \
                 patch.object(harness.Harness, "cleanup", side_effect=harness.HarnessError("cleanup failed")), \
                 patch.object(harness, "docker", return_value=""), \
                 patch.object(harness.Harness, "verify_ports", side_effect=harness.HarnessError("verification failed")), \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as stderr:
                with self.assertRaisesRegex(harness.HarnessError, "cleanup failed"):
                    harness.main()
            self.assertTrue(root.is_dir())
            self.assertIn("workspace retained", stderr.getvalue())

    def test_docker_failure_does_not_print_subprocess_output(self):
        result = types.SimpleNamespace(returncode=1, stdout="secret", stderr="secret")
        with patch.object(harness.subprocess, "run", return_value=result):
            with self.assertRaises(harness.HarnessError) as caught:
                harness.docker("compose", "config")
        self.assertNotIn("secret", str(caught.exception))
        self.assertIn("underlying cause not determined", str(caught.exception))

    def test_build_heartbeat_reports_elapsed_without_command_or_environment(self):
        def slow_command(*args, **kwargs):
            time.sleep(0.055)
            return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")
        with patch.object(harness.subprocess, "run", side_effect=slow_command), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(harness.docker("compose", "secret-argument", env={"SECRET": "secret-value"},
                                            progress="build_and_start", progress_interval=0.01), "ok")
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertTrue(events)
        self.assertTrue(all(event["stage"] == "build_and_start" and event["elapsed_seconds"] >= 0 for event in events))
        self.assertNotIn("secret", output.getvalue())


if __name__ == "__main__":
    unittest.main()
