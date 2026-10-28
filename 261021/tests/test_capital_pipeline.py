from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

import pandas as pd

from bike_mlops.capital_data import validate_capital_manifest
from scripts.prepare_capitalbikeshare import aggregate_archives


class CapitalAggregationContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
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
        manifest, frame = validate_capital_manifest(json.loads(manifest_path.read_text()), csv_path, self.raw)
        self.assertEqual(manifest["data_sha256"], report["derived_csv_sha256"])
        self.assertEqual(int(frame["cnt"].sum()), 2)

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


if __name__ == "__main__":
    unittest.main()
