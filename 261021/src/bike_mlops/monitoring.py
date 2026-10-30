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
    *,
    psi_version: str | None = None,
    frozen_reference: dict | None = None,
) -> pd.DataFrame:
    """Replay hourly monitoring clocks; labels count only after their declared availability."""
    from bike_mlops.monitoring_psi_v2 import PSI_ALGORITHM_V2, evaluate_psi_reference
    if psi_version not in {None, PSI_ALGORITHM_V2}:
        raise ValueError("Unsupported monitoring PSI algorithm")
    version2 = psi_version == PSI_ALGORITHM_V2
    if version2:
        from bike_mlops.capital_deployment_evidence import canonical_bytes, digest
        from bike_mlops.training_monitor_reference import POLICY
        if (not isinstance(frozen_reference, dict)
                or canonical_bytes(frozen_reference.get("policy")) != canonical_bytes(POLICY)
                or frozen_reference.get("reference_sha256") != digest({k: v for k, v in frozen_reference.items() if k != "reference_sha256"})):
            raise ValueError("PSI v2 requires a valid frozen training reference")
        if validation_mae is not None and (type(validation_mae) not in {int, float}
                                          or not np.isfinite(validation_mae) or validation_mae < 0):
            raise ValueError("Performance reference MAE must be finite and non-negative")
    elif frozen_reference is not None:
        raise ValueError("Frozen references require an explicit PSI algorithm version")
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
        psi_details = {}
        for name in ("lag_24", "lag_168", "hour"):
            current = np.asarray([row.get(name, np.nan) for row in window["features"]], dtype=float)
            if version2:
                psi_details[name] = evaluate_psi_reference(
                    frozen_reference["features"][name], current, version=psi_version,
                    minimum_count=frozen_reference["policy"]["minimum_current_count"],
                )
                feature_psi[name] = psi_details[name]["psi"]
            else:
                reference = reference_features[name].to_numpy(dtype=float)
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
        if version2:
            missing_rates = {name: (psi_details[name]["current_input_counts"]["missing"] / len(window)
                                   if len(window) else None) for name in ("lag_24", "lag_168")}
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
                        else ("performance_reference_unset" if version2 and validation_mae is None
                              else "within_review_threshold")
                    )
                ),
                "psi": feature_psi,
                "feature_missing_rates": missing_rates,
                "distribution_status": (
                    "insufficient_data"
                    if max_psi is None
                    else ("investigate_distribution_change" if max_psi >= 0.2 else "no_threshold_crossing")
                ),
                **({"psi_algorithm": psi_version, "psi_details": psi_details,
                    "training_reference_sha256": frozen_reference["reference_sha256"]} if version2 else {}),
            }
        )
        if version2:
            states = {item["status"] for item in psi_details.values()}
            if states & {"invalid_reference_values", "invalid_current_values"}:
                rows[-1]["distribution_status"] = "invalid_values"
            elif states & {"no_reference", "insufficient_reference"}:
                rows[-1]["distribution_status"] = "reference_unavailable"
            elif max_psi is None or (states != {"ready"} and max_psi < 0.2):
                rows[-1]["distribution_status"] = "insufficient_data"
        clock += timedelta(hours=1)
    return pd.DataFrame(rows)
