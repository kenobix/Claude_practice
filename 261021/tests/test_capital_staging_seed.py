from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from bike_mlops import capital_staging_seed
from bike_mlops.candidate_artifacts import sha256_file
from bike_mlops.capital_staging import BOOTSTRAP_QUALITY_GATE_POLICY, BOOTSTRAP_QUALITY_GATE_POLICY_SHA256
from bike_mlops.config import TARGET_CONTRACT_ID
from bike_mlops.db import Database


class StagingSeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.csv = root / "source.csv"
        self.manifest = root / "manifest.json"
        self.csv.write_text("fixture csv", encoding="utf-8")
        self.manifest.write_text("fixture manifest", encoding="utf-8")
        self.data_hash = sha256_file(self.csv)
        self.frame = pd.DataFrame({"timestamp": pd.to_datetime(["2024-01-01 00:00", "2024-01-01 01:00"]),
                                   "cnt": [3, 4]})
        self.evidence = {"candidate_id": "a" * 64, "selection_report_sha256": "b" * 64,
                         "selection": {"data_sha256": self.data_hash,
                                       "data_source": {"source_manifest_sha256": sha256_file(self.manifest)}}}
        self.tags = {"data_profile_id": "capital-bikeshare", "target_contract_id": TARGET_CONTRACT_ID,
                     "quality_gate": "passed", "validation_gate_status": "passed",
                     "final_accuracy_gate_status": "passed", "operational_gate_status": "not_evaluated",
                     "deployment_permission": "blocked", "quality_gate_policy_id": BOOTSTRAP_QUALITY_GATE_POLICY["policy_id"],
                     "quality_gate_policy_sha256": BOOTSTRAP_QUALITY_GATE_POLICY_SHA256,
                     "candidate_id": self.evidence["candidate_id"], "data_sha256": self.data_hash}
        self.registered = SimpleNamespace(status="READY", tags=self.tags, version="7")
        self.db = Database(f"sqlite:///{root / 'staging.sqlite'}")
        self.db.initialize()

    def seed(self):
        expected = {"candidate_id": self.evidence["candidate_id"], "data_sha256": self.data_hash}
        with patch.object(capital_staging_seed, "_registration_tags", return_value=expected):
            return capital_staging_seed.seed_verified_candidate(
                self.db, self.frame, self.csv, self.manifest, self.evidence, self.registered)

    def rows(self):
        with self.db.session() as connection:
            return [dict(row) for row in self.db.execute(connection, "SELECT * FROM observations ORDER BY timestamp").fetchall()]

    def test_seed_is_idempotent_and_identity_is_reported(self):
        result = self.seed()
        self.assertEqual(result["new_observations_inserted"], 2)
        self.assertEqual(result["model_version"], "7")
        self.assertEqual(self.seed()["new_observations_inserted"], 0)
        self.assertEqual(len(self.rows()), 2)

    def test_registry_and_source_mismatch_do_not_write(self):
        for change in ("registry", "csv", "manifest", "status"):
            with self.subTest(change=change):
                original_tags, original_status = self.tags.copy(), self.registered.status
                if change == "registry": self.tags["data_sha256"] = "c" * 64
                if change == "csv": self.csv.write_text("changed", encoding="utf-8")
                if change == "manifest": self.manifest.write_text("changed", encoding="utf-8")
                if change == "status": self.registered.status = "FAILED_REGISTRATION"
                with self.assertRaises(ValueError): self.seed()
                self.assertEqual(self.rows(), [])
                self.tags.clear(); self.tags.update(original_tags)
                self.registered.status = original_status
                self.csv.write_text("fixture csv", encoding="utf-8")
                self.manifest.write_text("fixture manifest", encoding="utf-8")

    def test_existing_extra_or_changed_rows_are_rejected_without_partial_writes(self):
        for timestamp, count, source_hash in [("2023-01-01 00:00:00", 9, self.data_hash),
                                               ("2024-01-01 00:00:00", 9, self.data_hash),
                                               ("2024-01-01 00:00:00", 3, "c" * 64)]:
            with self.subTest(timestamp=timestamp, count=count, source_hash=source_hash):
                with self.db.transaction() as connection:
                    self.db.execute(connection, "DELETE FROM observations")
                self.db.add_observation(timestamp, count, pd.Timestamp(timestamp) + pd.Timedelta(hours=3), source_hash)
                before = self.rows()
                with self.assertRaisesRegex(ValueError, "differs from candidate dataset"): self.seed()
                self.assertEqual(self.rows(), before)


if __name__ == "__main__":
    unittest.main()
