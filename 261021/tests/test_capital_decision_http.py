"""Guard tests for the opt-in HTTP fixture; no MLflow server is started here."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import test_capital_decision_http as fixture


class CapitalHttpFixtureSafetyTests(unittest.TestCase):
    def test_opt_in_required_before_directory_or_server_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "evidence"
            with patch.object(fixture.subprocess, "Popen") as launch:
                with self.assertRaises(SystemExit) as error:
                    fixture.main(["--evidence-dir", str(output)])
                self.assertEqual(error.exception.code, 2)
                self.assertFalse(output.exists())
                launch.assert_not_called()

    def test_no_arbitrary_registry_endpoint_argument(self):
        with patch.object(fixture.subprocess, "Popen") as launch:
            with self.assertRaises(SystemExit):
                fixture.main(["--run-disposable", "--evidence-dir", "/unused",
                              "--registry-uri", "http://127.0.0.1:5000"])
            launch.assert_not_called()

    def test_evidence_cannot_overwrite_previous_trial(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(fixture.subprocess, "Popen") as launch:
                with self.assertRaises(FileExistsError):
                    fixture.main(["--run-disposable", "--evidence-dir", directory])
                launch.assert_not_called()

    def test_dead_or_changed_server_identity_rejects_listener(self):
        expected = {"pid": 1, "ppid": 0, "pgrp": 1, "start_ticks": 1}
        for code, current in ((1, expected), (None, dict(expected, start_ticks=2))):
            with self.subTest(code=code), patch.object(fixture, "process_identity", return_value=current):
                process = SimpleNamespace(pid=1, poll=lambda: code)
                with self.assertRaisesRegex(RuntimeError, "identity"):
                    fixture.listener_owner(process, 5000, expected)

    def test_foreign_socket_is_not_a_private_listener(self):
        # The server identity exists, but no fd in its descendants owns the port.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc = root / "proc"
            (proc / "123" / "fd").mkdir(parents=True)
            tcp = proc / "net" / "tcp"
            tcp.parent.mkdir()
            tcp.write_text("header\n0: 0100007F:1388 00000000:0000 0A 0 0 0 0 0 999\n")
            real_path = Path
            def mapped(value):
                return proc if value == "/proc" else proc / value[6:] if value.startswith("/proc/") else real_path(value)
            identity = dict(pid=123, ppid=1, pgrp=123, start_ticks=1)
            process = SimpleNamespace(pid=123, poll=lambda: None)
            with patch.object(fixture, "Path", side_effect=mapped), patch.object(fixture, "process_identity", return_value=identity):
                with self.assertRaisesRegex(RuntimeError, "not owned"):
                    fixture.listener_owner(process, 5000, identity)

    def test_failure_helper_never_treats_success_as_failure(self):
        with self.assertRaises(AssertionError):
            fixture.expect_failure(lambda: "success")
        result = fixture.expect_failure(lambda: (_ for _ in ()).throw(ValueError("synthetic")))
        self.assertEqual(result, {"error_type": "ValueError", "message": "synthetic"})

    def test_term_unwinds_cleanup_with_nonzero_exit(self):
        with self.assertRaises(SystemExit) as error:
            fixture.request_stop(fixture.signal.SIGTERM, None)
        self.assertEqual(error.exception.code, 143)


if __name__ == "__main__":
    unittest.main()
