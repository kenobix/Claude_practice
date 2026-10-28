import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bike_mlops.profiles import load_data_profile


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DataProfileTests(unittest.TestCase):
    def test_uci_and_capital_profiles_declare_distinct_targets_and_runtime(self):
        uci = load_data_profile("uci-bike-sharing")
        capital = load_data_profile("capital-bikeshare")
        self.assertNotEqual(uci["target_contract_id"], capital["target_contract_id"])
        self.assertNotEqual(uci["model_name"], capital["model_name"])
        self.assertNotEqual(uci["runtime_ports"], capital["runtime_ports"])
        self.assertIsNone(capital["monitor_reference"])
        self.assertEqual(capital["status"], "migration-only")
        self.assertTrue(capital["default_csv"].endswith("capitalbikeshare-next/hourly.csv"))

    def test_capital_process_uses_separate_local_paths(self):
        environment = os.environ.copy()
        environment.update(
            {
                "DATA_PROFILE_ID": "capital-bikeshare",
                "PYTHONPATH": str(PROJECT_ROOT / "src"),
            }
        )
        environment.pop("DATABASE_URL", None)
        environment.pop("MLFLOW_TRACKING_URI", None)
        code = (
            "import json; from bike_mlops.config import DATA_PROFILE_ID, DEFAULT_DATASET, "
            "DEFAULT_DATABASE_URL, DEFAULT_MLFLOW_URI, REPORT_DIR, RUNTIME_DIR; "
            "print(json.dumps([DATA_PROFILE_ID, str(DEFAULT_DATASET), DEFAULT_DATABASE_URL, "
            "DEFAULT_MLFLOW_URI, str(REPORT_DIR), str(RUNTIME_DIR)]))"
        )
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=PROJECT_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        values = json.loads(completed.stdout)
        self.assertEqual(values[0], "capital-bikeshare")
        self.assertIn("capitalbikeshare-next/hourly.csv", values[1])
        self.assertIn("bikeops-capital-bikeshare.db", values[2])
        self.assertIn("mlflow-capital-bikeshare.db", values[3])
        self.assertTrue(values[4].endswith("reports/capital-bikeshare"))
        self.assertTrue(values[5].endswith("runtime-capital-bikeshare"))

    def test_unknown_profile_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown data profile"):
            load_data_profile("not-a-real-profile")

    def test_malformed_label_availability_is_rejected_as_a_profile_error(self):
        profile = load_data_profile("uci-bike-sharing")
        profile["profile_id"] = "test-profile"
        profile["label_availability"] = []
        with tempfile.TemporaryDirectory() as temporary_directory:
            profile_dir = Path(temporary_directory)
            (profile_dir / "test-profile.json").write_text(json.dumps(profile), encoding="utf-8")
            with patch("bike_mlops.profiles.PROFILE_DIR", profile_dir):
                with self.assertRaisesRegex(ValueError, "label_availability as an object"):
                    load_data_profile("test-profile")


if __name__ == "__main__":
    unittest.main()
