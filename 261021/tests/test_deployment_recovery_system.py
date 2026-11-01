"""Safety boundaries run without Docker or a live database."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import test_deployment_recovery_system as fixture


class DeploymentFixtureSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.token = "a" * 32
        (root / ".fixture-token").write_text(self.token)
        self.spec = dict(workspace=str(root), token=self.token, prefix="bike-deploy-test-" + "b" * 16,
                         database_url="postgresql://postgres:fixture@127.0.0.1:59999/postgres",
                         internal_database_url="postgresql://postgres:fixture@bike-deploy-test-" + "b" * 16 + "-postgres:5432/postgres", ports={})

    def test_real_project_and_mismatched_marker_are_rejected_before_docker(self):
        with patch.object(fixture, "docker") as docker:
            with self.assertRaisesRegex(ValueError, "outside the real project"):
                fixture.Fixture(dict(self.spec, workspace=str(fixture.ROOT)))
            with self.assertRaisesRegex(ValueError, "marker"):
                fixture.Fixture(dict(self.spec, token="c" * 32))
            docker.assert_not_called()

    def test_invalid_resource_identity_is_rejected(self):
        for key, value in (("prefix", "bike-mlops"), ("token", "short")):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "identity"):
                fixture.Fixture(dict(self.spec, **{key: value}))

    def test_api_cannot_connect_to_a_foreign_database(self):
        f = fixture.Fixture(dict(self.spec, internal_database_url="postgresql://postgres:fixture@postgres:5432/postgres"))
        with patch.object(fixture, "docker") as docker, self.assertRaisesRegex(ValueError, "outside"):
            f.api("blue", "2")
        docker.assert_not_called()

    def test_host_endpoint_must_equal_the_owned_container_port(self):
        f = fixture.Fixture(self.spec)
        with patch("scripts.capital_system_harness.check_local_daemon"), patch.object(f, "owned") as owner, patch.object(fixture, "docker", return_value="127.0.0.1:58888"):
            with self.assertRaisesRegex(ValueError, "port does not belong"):
                f.patches()
        owner.assert_called_once_with("container", self.spec["prefix"] + "-postgres")

    def test_unowned_resources_are_never_mutated(self):
        f = fixture.Fixture(self.spec)
        with patch.object(fixture, "docker", return_value='{"other":"token"}') as docker:
            with self.assertRaisesRegex(RuntimeError, "outside this fixture"):
                f.owned("container", "unknown")
        self.assertEqual(docker.call_args.args[0], "inspect")


if __name__ == "__main__":
    unittest.main()
