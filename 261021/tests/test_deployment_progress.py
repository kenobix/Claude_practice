import io
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from bike_mlops import cli
from bike_mlops.deployment_progress import DeploymentTimeout, deployment_stage


class DeploymentProgressTests(unittest.TestCase):
    def test_heartbeat_is_process_monotonic_with_safe_stage_and_journal(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stderr(io.StringIO()) as output:
            root = Path(directory)
            with deployment_stage(root, "candidate_ready", interval=.005, operation_id="operation-1"):
                time.sleep(.025)
            events = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(events, [json.loads(line) for line in (root / "deployment-progress.jsonl").read_text().splitlines()])
            self.assertEqual(events[0]["status"], "started")
            self.assertEqual(events[-1]["status"], "stage_completed")
            self.assertIn("in_progress", [item["status"] for item in events])
            self.assertEqual([e["monotonic_ns"] for e in events], sorted(e["monotonic_ns"] for e in events))
            self.assertTrue(all(e["deployment_id"] == "operation-1" for e in events))

    def test_docker_timeout_is_bounded_unconfirmed_and_does_not_leak_native_output(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(cli, "RUNTIME_DIR", Path(directory)), redirect_stderr(io.StringIO()) as output:
            with patch.object(cli.subprocess, "run", side_effect=subprocess.TimeoutExpired(["secret-command"], 600, output="secret-value")) as run:
                with self.assertRaisesRegex(DeploymentTimeout, "unconfirmed") as error:
                    cli._compose("up", "-d", "--build", "api-green")
            self.assertEqual(run.call_args.kwargs["timeout"], 600)
            self.assertNotIn("secret", output.getvalue() + str(error.exception))
            self.assertEqual(json.loads(output.getvalue().splitlines()[-1])["outcome"], "unconfirmed")

    def test_nonbuild_docker_timeout_and_nonzero_are_not_swallowed(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(cli, "RUNTIME_DIR", Path(directory)), redirect_stderr(io.StringIO()):
            with patch.object(cli.subprocess, "run", side_effect=subprocess.CalledProcessError(23, ["native-secret"], output="secret-value")) as run:
                with self.assertRaisesRegex(RuntimeError, "exit 23") as error:
                    cli._compose("exec", "caddy")
            self.assertEqual(run.call_args.kwargs["timeout"], 30)
            self.assertNotIn("secret", str(error.exception))

    def test_interrupt_and_unsafe_log_are_not_reported_as_complete(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stderr(io.StringIO()) as output:
            root = Path(directory)
            with self.assertRaises(KeyboardInterrupt):
                with deployment_stage(root, "recover"):
                    raise KeyboardInterrupt()
            self.assertEqual(json.loads(output.getvalue().splitlines()[-1])["status"], "interrupted_or_failed")
            (root / "deployment-progress.jsonl").unlink()
            (root / "deployment-progress.jsonl").symlink_to(root / "outside")
            with self.assertRaises(OSError):
                with deployment_stage(root, "recover"):
                    self.fail("Symlink journal was accepted")


if __name__ == "__main__":
    unittest.main()
