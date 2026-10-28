"""Resolve monitoring clocks from a replay report or explicit boundaries."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd


def _boundaries(start: object, end: object) -> tuple[pd.Timestamp, pd.Timestamp]:
    if not isinstance(start, str) or not start.strip() or not isinstance(end, str) or not end.strip():
        raise ValueError("Monitoring boundaries must be valid naive hourly timestamps")
    try:
        first, last = pd.Timestamp(start), pd.Timestamp(end)
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError("Monitoring boundaries must be valid naive hourly timestamps") from error
    if (pd.isna(first) or pd.isna(last) or first.tzinfo is not None or last.tzinfo is not None
            or first != first.floor("h") or last != last.floor("h") or first >= last):
        raise ValueError("Monitoring boundaries must be valid naive hourly timestamps with start < end")
    return first, last


def resolve_monitor_window(*, replay_id: str, model_version: str, data_sha256: str, profile_id: str,
                           target_contract_id: str, report_dir: Path, start: str | None,
                           end: str | None) -> tuple[pd.Timestamp, pd.Timestamp, dict]:
    """Inherit the replay's requested period, or constrain an explicitly selected subperiod.

    Older replay reports may not exist; an explicit pair of bounds is then required.
    The caller must still verify the actual prediction rows and data provenance.
    """
    if not isinstance(replay_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", replay_id):
        raise ValueError("Replay ID contains unsupported characters")
    if not isinstance(model_version, str) or not model_version.isdecimal() or int(model_version) <= 0:
        raise ValueError("Monitor requires a positive explicit model version")
    if (start is None) != (end is None):
        raise ValueError("Specify both --start and --end, or omit both to use the replay report")
    root = report_dir.resolve()
    path = root / f"replay-{replay_id}-v{model_version}.json"
    report = None
    if path.exists() or path.is_symlink():
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_file() or not resolved.is_relative_to(root):
                raise ValueError("Replay report path is outside the report directory")
            report = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("Could not read the replay report") from error
        expected = {"replay_id": replay_id, "model_version": model_version, "data_sha256": data_sha256}
        if not isinstance(report, dict) or any(str(report.get(k)) != v for k, v in expected.items()):
            raise ValueError("Replay report identity does not match the selected model and dataset")
        legacy_uci = ("data_profile_id" not in report and "target_contract_id" not in report
                      and profile_id == "uci-bike-sharing"
                      and target_contract_id == "uci-bike-sharing-cnt-dataset-local-hour"
                      and data_sha256 == "e03de4ee4ef4dc376ac6e04bf829673c6269e8eba5c60fa121640fa2f829504f")
        if not legacy_uci and (report.get("data_profile_id") != profile_id
                               or report.get("target_contract_id") != target_contract_id):
            raise ValueError("Replay report profile and target do not match the active profile")
        identity_source = "legacy_uci_dataset_hash_inference" if legacy_uci else "report"
        report_start, report_end = _boundaries(report.get("start"), report.get("end_exclusive"))
    if start is None:
        if report is None:
            raise ValueError("Replay report is missing; specify both --start and --end explicitly")
        return report_start, report_end, {"source": "replay_report", "replay_report": path.name,
                                          "profile_identity_source": identity_source}
    first, last = _boundaries(start, end)
    if report is not None and (first < report_start or last > report_end):
        raise ValueError("Explicit monitoring period exceeds the replay report period")
    return first, last, {"source": "explicit", "replay_report": path.name if report is not None else None,
                         "profile_identity_source": identity_source if report is not None else "not_in_report"}
