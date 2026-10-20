import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None


@unittest.skipIf(TestClient is None, "FastAPI/httpx development dependencies are not installed")
class PredictionApiTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_url = f"sqlite:///{Path(self.tempdir.name) / 'api.sqlite'}"

        class FixedModel:
            def predict(self, features):
                return [float(features.iloc[0]["lag_24"] if features.iloc[0]["lag_24_missing"] == 0 else 42)]

        self.environment = patch.dict(
            os.environ,
            {"DATABASE_URL": self.database_url, "MODEL_VERSION": "7"},
        )
        self.environment.start()
        from bike_mlops import api

        self.api = api
        self.model_patch = patch.object(
            api, "_runtime_model", return_value=(FixedModel(), "7", "artifact-test-sha", "passed", None)
        )
        self.model_patch.start()
        self.client_context = TestClient(api.app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.model_patch.stop()
        self.environment.stop()
        self.tempdir.cleanup()

    def test_request_retry_returns_same_persisted_version_and_changed_input_conflicts(self):
        observed_at = "2012-06-30T10:00:00"
        accepted = self.client.post(
            "/observations",
            json={"timestamp": observed_at, "cnt": 123, "available_at": "2012-06-30T13:00:00"},
        )
        self.assertEqual(accepted.status_code, 202)
        payload = {
            "request_id": "request-test-001",
            "target_hour": "2012-07-01T10:00:00",
            "as_of": "2012-07-01T10:00:00",
            "feature_contract_version": "bike-demand-features-v1",
        }
        first = self.client.post("/predict", json=payload)
        # A retry after a restart must return its original model version without reloading that model.
        self.client.app.state.model = None
        retried = self.client.post("/predict", json=payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(retried.status_code, 200)
        self.assertEqual(first.json(), retried.json())
        self.assertEqual(first.json()["prediction"], 123.0)
        changed = {**payload, "target_hour": "2012-07-01T11:00:00", "as_of": "2012-07-01T11:00:00"}
        self.assertEqual(self.client.post("/predict", json=changed).status_code, 409)

    def test_a_prediction_is_not_successful_when_audit_write_fails(self):
        def fail_record(_record):
            raise OSError("simulated database write failure")

        self.client.app.state.database.save_prediction = fail_record
        payload = {
            "request_id": "request-test-002",
            "target_hour": "2012-07-01T10:00:00",
            "as_of": "2012-07-01T10:00:00",
            "feature_contract_version": "bike-demand-features-v1",
        }
        response = self.client.post("/predict", json=payload)
        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
