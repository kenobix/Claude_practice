from __future__ import annotations

import json
from datetime import timedelta

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error


def population_stability_index(reference, current, bins: int = 10) -> float | None:
    reference = np.asarray(reference, dtype=float)
    current = np.asarray(current, dtype=float)
    reference = reference[np.isfinite(reference)]
    current = current[np.isfinite(current)]
    if len(reference) < 2 or len(current) < 2:
        return None
    boundaries = np.unique(np.quantile(reference, np.linspace(0.0, 1.0, bins + 1)))
    if len(boundaries) < 2:
        edges = np.array([-np.inf, np.inf])
    elif len(boundaries) == 2:
        edges = np.array([-np.inf, boundaries.mean(), np.inf])
    else:
        edges = np.concatenate(([-np.inf], boundaries[1:-1], [np.inf]))
        edges = np.unique(edges)
    reference_counts = np.histogram(reference, bins=edges)[0].astype(float)
    current_counts = np.histogram(current, bins=edges)[0].astype(float)
    reference_share = (reference_counts + 1e-6) / (reference_counts.sum() + 1e-6 * len(reference_counts))
    current_share = (current_counts + 1e-6) / (current_counts.sum() + 1e-6 * len(current_counts))
    return float(np.sum((current_share - reference_share) * np.log(current_share / reference_share)))


def score_monitor_windows(
    predictions: pd.DataFrame,
    labels: pd.DataFrame,
    reference_features: pd.DataFrame,
    start: pd.Timestamp,
    end_exclusive: pd.Timestamp,
    window_hours: int = 168,
    minimum_labels: int = 24,
    validation_mae: float | None = None,
) -> pd.DataFrame:
    """Replay hourly monitoring clocks; labels count only after their declared availability."""
    predictions = predictions.copy()
    labels = labels.copy()
    predictions["target_hour"] = pd.to_datetime(predictions["target_hour"])
    predictions["features"] = predictions["features_json"].map(json.loads)
    labels["timestamp"] = pd.to_datetime(labels["timestamp"])
    labels["available_at"] = pd.to_datetime(labels["available_at"])
    label_by_time = dict(zip(labels["timestamp"], labels["cnt"].astype(float)))
    available_by_time = dict(zip(labels["timestamp"], labels["available_at"]))
    rows = []
    clock = start
    while clock < end_exclusive:
        lower = clock - pd.Timedelta(hours=window_hours)
        window = predictions.loc[
            (predictions["target_hour"] > lower) & (predictions["target_hour"] <= clock)
        ]
        scored_actual, scored_prediction = [], []
        for row in window.itertuples(index=False):
            target = row.target_hour
            if target in label_by_time and available_by_time[target] <= clock:
                scored_actual.append(label_by_time[target])
                scored_prediction.append(float(row.predicted))
        feature_psi = {}
        for name in ("lag_24", "lag_168", "hour"):
            reference = reference_features[name].to_numpy(dtype=float)
            current = np.asarray([row.get(name, np.nan) for row in window["features"]], dtype=float)
            feature_psi[name] = population_stability_index(reference, current)
        missing_rates = {
            feature_name: (
                float(np.mean([float(item.get(flag_name, 0.0)) for item in window["features"]]))
                if len(window)
                else None
            )
            for feature_name, flag_name in (
                ("lag_24", "lag_24_missing"),
                ("lag_168", "lag_168_missing"),
            )
        }
        n_predictions, n_scored = int(len(window)), len(scored_actual)
        mae = float(mean_absolute_error(scored_actual, scored_prediction)) if n_scored else None
        rmse = (
            float(np.sqrt(mean_squared_error(scored_actual, scored_prediction)))
            if n_scored
            else None
        )
        max_psi = max((value for value in feature_psi.values() if value is not None), default=None)
        rows.append(
            {
                "window_end": clock.isoformat(sep=" "),
                "window_start": lower.isoformat(sep=" "),
                "prediction_count": n_predictions,
                "scored_count": n_scored,
                "label_coverage": n_scored / n_predictions if n_predictions else None,
                "mae": mae if n_scored >= minimum_labels else None,
                "rmse": rmse if n_scored >= minimum_labels else None,
                "quality_status": (
                    "insufficient_labels"
                    if n_scored < minimum_labels
                    else (
                        "investigate_error_increase"
                        if validation_mae is not None and mae > validation_mae * 1.25
                        else "within_review_threshold"
                    )
                ),
                "psi": feature_psi,
                "feature_missing_rates": missing_rates,
                "distribution_status": (
                    "insufficient_data"
                    if max_psi is None
                    else ("investigate_distribution_change" if max_psi >= 0.2 else "no_threshold_crossing")
                ),
            }
        )
        clock += timedelta(hours=1)
    return pd.DataFrame(rows)
