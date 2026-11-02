"""Content mismatches are rejected independently of Docker happy-path checks."""
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from scripts.capital_paired_restore import require_equal, tree_manifest


class PairedRestoreTests(unittest.TestCase):
    def test_missing_artifacts_or_estimator_is_not_a_model_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "empty|MLmodel"):
                tree_manifest(root)
            model = root / "model"
            model.mkdir()
            (model / "MLmodel").write_text("fixture")
            with self.assertRaisesRegex(ValueError, "skops"):
                tree_manifest(root)

    def test_hash_bytes_names_and_extra_files_change_the_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
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
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "outside").symlink_to("/etc/passwd")
            with self.assertRaisesRegex(ValueError, "regular"):
                tree_manifest(root)

    def test_same_row_count_with_changed_values_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "tables"):
            require_equal({"predictions": {"rows": 10, "sha256": "a"}},
                          {"predictions": {"rows": 10, "sha256": "b"}}, "tables")

    def test_restore_opt_in_and_port_maps_are_checked_before_resources(self):
        from scripts import capital_system_harness as h
        with patch("sys.argv", ["harness", "--verify-paired-restore"]), patch.object(h, "docker") as docker:
            with self.assertRaises(SystemExit) as error:
                h.main()
            self.assertEqual(error.exception.code, 2)
            docker.assert_not_called()
        for ports in ((1, 1, 2, 3), (0, 1, 2, 3), (1, 2, 3, 65536)):
            with self.assertRaisesRegex(ValueError, "port map"):
                h.Harness(Path("/tmp/uncreated"), "bikemlopstest" + "a"*16, "b"*32, ports=ports)

    def test_protected_inventory_includes_only_existing_project_names(self):
        from scripts import capital_system_harness as h
        with patch.object(h, "docker", return_value="bike-mlops-postgres-1 id1\nbikemlopstest123-mlflow-1 id2\nother-api id3"):
            self.assertEqual(h.protected_container_ids(), ["bike-mlops-postgres-1 id1"])


if __name__ == "__main__":
    unittest.main()
