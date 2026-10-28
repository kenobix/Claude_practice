from __future__ import annotations

import hashlib
import unittest
from importlib.resources import files
from io import BytesIO
from zoneinfo import ZoneInfo

import pandas as pd
import tzdata

from bike_mlops.timezones import load_pinned_timezone, timezone_data_identity


class PinnedTimezoneTests(unittest.TestCase):
    def test_dst_grid_uses_the_locked_package_and_preserves_transition_rules(self):
        zone_name = "America/New_York"
        zone = load_pinned_timezone(zone_name)

        spring = pd.date_range(
            "2024-03-10 00:00", "2024-03-10 05:00", freq="h", inclusive="left", tz=zone
        ).tz_localize(None)
        self.assertEqual(
            spring.tolist(),
            [pd.Timestamp("2024-03-10 00:00"), pd.Timestamp("2024-03-10 01:00"),
             pd.Timestamp("2024-03-10 03:00"), pd.Timestamp("2024-03-10 04:00")],
        )

        fall_aware = pd.date_range(
            "2024-11-03 00:00", "2024-11-03 04:00", freq="h", inclusive="left", tz=zone
        )
        fall_local = fall_aware.tz_localize(None)
        self.assertEqual(fall_local.duplicated(keep=False).sum(), 2)
        self.assertEqual(fall_aware[1].utcoffset(), pd.Timedelta(hours=-4))
        self.assertEqual(fall_aware[2].utcoffset(), pd.Timedelta(hours=-5))

    def test_timezone_identity_hashes_the_package_resource(self):
        name = "America/New_York"
        resource = files("tzdata.zoneinfo").joinpath(*name.split("/"))
        with resource.open("rb") as stream:
            raw = stream.read()
        identity = timezone_data_identity(name)

        self.assertEqual(identity["provider"], "python-tzdata-package")
        self.assertEqual(identity["package"], "tzdata")
        self.assertEqual(identity["package_version"], tzdata.__version__)
        self.assertEqual(identity["zone_file_bytes"], len(raw))
        self.assertEqual(
            identity["zone_file_sha256"],
            hashlib.sha256(raw).hexdigest(),
        )
        identity["zone_file_sha256"] = "0" * 64
        self.assertEqual(timezone_data_identity(name)["zone_file_sha256"], hashlib.sha256(raw).hexdigest())
        with resource.open("rb") as stream:
            from_file_zone = ZoneInfo.from_file(BytesIO(stream.read()), key=name)
        self.assertEqual(load_pinned_timezone(name).utcoffset(pd.Timestamp("2024-01-01").to_pydatetime()),
                         from_file_zone.utcoffset(pd.Timestamp("2024-01-01").to_pydatetime()))

    def test_timezone_path_traversal_is_rejected(self):
        for key in ("../UTC", "/etc/localtime", "America\\New_York"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                load_pinned_timezone(key)


if __name__ == "__main__":
    unittest.main()
