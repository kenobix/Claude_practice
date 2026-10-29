from __future__ import annotations

import subprocess
import sys
import unittest
import importlib.util
import tempfile
from pathlib import Path
from unittest.mock import patch


class SyntheticFixtureSafetyTests(unittest.TestCase):
    def test_child_refuses_to_run_in_the_real_project(self):
        script = Path(__file__).resolve().parents[1] / "scripts/test_capital_synthetic_fixture.py"
        result = subprocess.run([sys.executable, str(script), "--internal-child"], capture_output=True,
                                text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("only run in its disposable marked workspace", result.stderr)

    def test_interrupted_child_cleans_temporary_workspace(self):
        script = Path(__file__).resolve().parents[1] / "scripts/test_capital_synthetic_fixture.py"
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
              patch.object(module.tempfile, "TemporaryDirectory", side_effect=temporary),
              patch("subprocess.run", side_effect=KeyboardInterrupt)):
            with self.assertRaises(KeyboardInterrupt):
                module.main()
        self.assertEqual(len(paths), 1)
        self.assertFalse(paths[0].exists())


if __name__ == "__main__":
    unittest.main()
