from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from bike_mlops.config import LABEL_DELAY_HOURS

FEATURE_COLUMNS = (
    "hour",
    "weekday",
    "month",
    "day_of_year",
    "is_weekend",
    "lag_24",
    "lag_24_missing",
    "lag_168",
    "lag_168_missing",
)


def _timestamp(value: datetime | pd.Timestamp | str) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is not None:
        raise ValueError("Feature contract requires a naive dataset-local timestamp")
    return stamp


def build_feature_row(
    target_hour: datetime | pd.Timestamp | str,
    as_of: datetime | pd.Timestamp | str,
    observations: pd.DataFrame,
) -> dict[str, float]:
    target = _timestamp(target_hour)
    decision_time = _timestamp(as_of)
    if target != target.floor("h"):
        raise ValueError("target_hour must be aligned to the start of an hour")
    if decision_time != target:
        raise ValueError("MVP contract requires as_of == target_hour")

    required = {"timestamp", "cnt", "available_at"}
    if not required.issubset(observations.columns):
        raise ValueError(f"Observations must include {sorted(required)}")
    available_values: dict[pd.Timestamp, float] = {}
    for row in observations.itertuples(index=False):
        observed_at = _timestamp(row.timestamp)
        available_at = _timestamp(row.available_at)
        if available_at <= decision_time and observed_at < target:
            available_values[observed_at] = float(row.cnt)

    feature_values: dict[str, float] = {
        "hour": float(target.hour),
        "weekday": float(target.dayofweek),
        "month": float(target.month),
        "day_of_year": float(target.dayofyear),
        "is_weekend": float(target.dayofweek >= 5),
    }
    for lag_hours in (24, 168):
        source_time = target - pd.Timedelta(hours=lag_hours)
        count = available_values.get(source_time)
        feature_values[f"lag_{lag_hours}"] = float(count) if count is not None else float("nan")
        feature_values[f"lag_{lag_hours}_missing"] = float(count is None)
    return feature_values


def feature_frame_for_dataset(frame: pd.DataFrame) -> pd.DataFrame:
    """Build each row using only exact-time observations available at that row's start."""
    counts = dict(zip(frame["timestamp"], frame["cnt"]))
    rows = []
    for row in frame.itertuples(index=False):
        target = _timestamp(row.timestamp)
        feature_values = {
            "hour": float(target.hour),
            "weekday": float(target.dayofweek),
            "month": float(target.month),
            "day_of_year": float(target.dayofyear),
            "is_weekend": float(target.dayofweek >= 5),
        }
        for lag_hours in (24, 168):
            source_time = target - pd.Timedelta(hours=lag_hours)
            observed = counts.get(source_time)
            is_available = source_time + pd.Timedelta(hours=LABEL_DELAY_HOURS) <= target
            count = float(observed) if observed is not None and is_available else float("nan")
            feature_values[f"lag_{lag_hours}"] = count
            feature_values[f"lag_{lag_hours}_missing"] = float(np.isnan(count))
        rows.append(feature_values)
    return pd.DataFrame(rows, columns=FEATURE_COLUMNS, index=frame.index)


def available_observation_frame(frame: pd.DataFrame) -> pd.DataFrame:
    history = frame[["timestamp", "cnt"]].copy()
    history["available_at"] = history["timestamp"] + pd.Timedelta(hours=LABEL_DELAY_HOURS)
    return history
