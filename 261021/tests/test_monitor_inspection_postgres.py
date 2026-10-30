"""PostgreSQL adapter guards without a running server; integration is opt-in."""
from __future__ import annotations

import json
import math
import struct
import unittest
from unittest.mock import MagicMock, patch

from bike_mlops import monitor_inspection as inspection


class PostgresInspectionUnitTests(unittest.TestCase):
    def test_lost_connection_cleanup_does_not_mask_query_failure(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        import psycopg
        connection = MagicMock()
        connection.execute.side_effect = psycopg.OperationalError('SECRET')
        connection.rollback.side_effect = psycopg.OperationalError('SECRET')
        connection.close.side_effect = OSError('SECRET')
        with TemporaryDirectory() as directory, patch('psycopg.connect', return_value=connection):
            result = inspection.inspect_monitor_run(
                Path(directory) / 'missing.csv', '00000000-0000-0000-0000-000000000001',
                database_url='postgresql://private:SECRET@host/db', data_profile_id='p', target_contract_id='t')
        self.assertEqual(result['status'], 'database_unavailable')
        self.assertNotIn('SECRET', json.dumps(result))
        connection.close.assert_called_once()

    def test_connect_failure_redacts_uri_and_overrides_unsafe_uri_options(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        for scheme in ("postgresql", "postgres"):
            with self.subTest(scheme=scheme), TemporaryDirectory() as directory:
                with patch("psycopg.connect", side_effect=OSError("private:SECRET@host")) as connect:
                    result = inspection.inspect_monitor_run(
                        Path(directory) / "none.csv", "00000000-0000-0000-0000-000000000001",
                        database_url=f"{scheme}://private:SECRET@host/db?connect_timeout=90",
                        data_profile_id="p", target_contract_id="t")
                self.assertEqual((result["status"], result["exit_code"]), ("database_unavailable", 2))
                self.assertNotIn("SECRET", json.dumps(result, allow_nan=False))
                self.assertIsNone(result["database"]["row_count"])
                self.assertEqual(result["database"]["backend"], "postgresql")
                self.assertEqual(connect.call_args.kwargs["connect_timeout"], 5)
                self.assertTrue(connect.call_args.kwargs["autocommit"])
                self.assertIn("default_transaction_read_only=on", connect.call_args.kwargs["options"])
                self.assertIn("statement_timeout=5000", connect.call_args.kwargs["options"])
                self.assertIn("lock_timeout=1000", connect.call_args.kwargs["options"])

    def compare_metric(self, want, got, storage="binary32"):
        out = {"database": {"backend": "postgresql", "numeric_storage": {
            key: storage for key in inspection.METRICS}, "missing_representations": {"sql_nan_missing": 0}},
            "differences": [], "difference_count": 0, "differences_truncated": False, "checks": {}}
        left = {"window_end": "2012-01-01 01:00:00", "prediction_count": 3, "scored_count": 1,
                "label_coverage": want, "mae": None, "rmse": None, "psi": {"hour": 1 / 3},
                "feature_missing_rates": {"lag": 0.0}}
        right = dict(left, window_id="monitor:run:2012-01-01 01:00:00", replay_id="replay",
                     label_coverage=got, psi_json=json.dumps(left["psi"]), missing_json='{"lag": 0.0}')
        inspection._compare([left], [right], {"monitor_run_id": "run", "replay_id": "replay"}, out)
        json.dumps(out, allow_nan=False)
        return out

    def test_real_quantization_and_one_ulp_mismatch(self):
        rounded = struct.unpack("!f", struct.pack("!f", 1 / 3))[0]
        self.assertEqual(self.compare_metric(1 / 3, rounded)["difference_count"], 0)
        bits = struct.unpack("!I", struct.pack("!f", rounded))[0]
        next_value = struct.unpack("!f", struct.pack("!I", bits + 1))[0]
        mismatch = self.compare_metric(1 / 3, next_value)
        self.assertEqual(mismatch["difference_count"], 1)
        self.assertEqual(mismatch["differences"][0]["expected"], repr(rounded))

    def test_double_precision_does_not_quantize(self):
        self.assertEqual(self.compare_metric(1 / 3, 1 / 3, "binary64")["difference_count"], 0)
        self.assertEqual(self.compare_metric(1 / 3, math.nextafter(1 / 3, 1), "binary64")["difference_count"], 1)

    def test_nan_only_normalized_for_expected_missing(self):
        missing = self.compare_metric(None, float("nan"))
        self.assertEqual(missing["difference_count"], 0)
        self.assertEqual(missing["database"]["missing_representations"]["sql_nan_missing"], 1)
        mismatch = self.compare_metric(0.0, float("nan"))
        self.assertEqual(mismatch["difference_count"], 1)
        self.assertEqual(mismatch["database"]["missing_representations"]["sql_nan_missing"], 0)
        self.assertEqual(mismatch["differences"][0]["observed"], "SQL_NaN")

    def test_infinity_strings_bool_and_overflow_do_not_match(self):
        for want, got in ((None, float("inf")), (0.0, None), (None, 0.0),
                          (1.0, "1.0"), (1.0, True), (1e100, 1e100)):
            with self.subTest(want=want, got=got):
                self.assertEqual(self.compare_metric(want, got)["difference_count"], 1)
