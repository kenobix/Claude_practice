from __future__ import annotations

import json
import math
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pandas as pd
from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from bike_mlops import __version__
from bike_mlops.config import (
    DEFAULT_DATABASE_URL,
    FEATURE_CONTRACT_VERSION,
    LABEL_DELAY_HOURS,
    MODEL_NAME,
)
from bike_mlops.db import Database, RequestConflict, canonical_time, stable_hash
from bike_mlops.features import FEATURE_COLUMNS, build_feature_row
from bike_mlops.model import load_registered_model, predict_non_negative


class PredictionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    target_hour: datetime
    as_of: datetime
    feature_contract_version: str = Field(min_length=1, max_length=64)


class ObservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    cnt: int = Field(ge=0)
    available_at: datetime


def _runtime_model() -> tuple[object | None, str | None, str | None, str | None, str | None]:
    version = os.getenv("MODEL_VERSION")
    if not version:
        return None, None, None, None, "MODEL_VERSION is not set; an explicit registered version is required"
    until = time.monotonic() + 45
    last_error = None
    while time.monotonic() < until:
        try:
            model, artifact_hash, quality_gate = load_registered_model(
                os.getenv("MLFLOW_MODEL_NAME", MODEL_NAME), version
            )
            return model, str(int(version)), artifact_hash, quality_gate, None
        except Exception as error:
            last_error = error
            time.sleep(2)
    return None, str(version), None, None, f"Could not load registered model version {version}: {last_error}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    database = Database(os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL))
    database.initialize()
    model, version, artifact_hash, quality_gate, model_error = _runtime_model()
    app.state.database = database
    app.state.model = model
    app.state.model_version = version
    app.state.artifact_hash = artifact_hash
    app.state.model_quality_gate = quality_gate
    app.state.model_error = model_error
    app.state.started_at = datetime.now(timezone.utc).isoformat()
    yield


app = FastAPI(
    title="Bike Demand Prediction API",
    version=__version__,
    description=(
        "Retrospective hourly demand prediction. Each process loads and pins one explicit MLflow model version."
    ),
    lifespan=lifespan,
)


@app.get("/health")
def health():
    return {"status": "alive"}


@app.get("/ready")
def ready(request: Request):
    if not hasattr(request.app.state, "database"):
        raise HTTPException(status_code=503, detail="Application has not completed startup")
    if request.app.state.model is None:
        raise HTTPException(status_code=503, detail=request.app.state.model_error)
    try:
        with request.app.state.database.session() as connection:
            request.app.state.database.execute(connection, "SELECT 1").fetchone()
    except Exception as error:
        raise HTTPException(status_code=503, detail=f"Database is unavailable: {error}") from error
    return {
        "status": "ready",
        "model_name": MODEL_NAME,
        "model_version": request.app.state.model_version,
        "quality_gate_status": request.app.state.model_quality_gate,
    }


@app.get("/version")
def version(request: Request):
    return {
        "application_version": __version__,
        "model_name": MODEL_NAME,
        "model_version": getattr(request.app.state, "model_version", None),
        "artifact_sha256": getattr(request.app.state, "artifact_hash", None),
        "quality_gate_status": getattr(request.app.state, "model_quality_gate", None),
        "feature_contract_version": FEATURE_CONTRACT_VERSION,
    }


@app.post("/observations", status_code=status.HTTP_202_ACCEPTED)
def ingest_observation(payload: ObservationRequest, request: Request):
    if payload.timestamp.tzinfo is not None or payload.available_at.tzinfo is not None:
        raise HTTPException(status_code=422, detail="Timestamps must be naive dataset-local time")
    if payload.timestamp.minute or payload.timestamp.second or payload.timestamp.microsecond:
        raise HTTPException(status_code=422, detail="Observation timestamp must be aligned to an hour")
    expected = payload.timestamp + timedelta(hours=LABEL_DELAY_HOURS)
    if payload.available_at != expected:
        raise HTTPException(
            status_code=422,
            detail=f"This replay experiment requires available_at = timestamp + {LABEL_DELAY_HOURS} hours",
        )
    source_hash = stable_hash(
        {"timestamp": canonical_time(payload.timestamp), "cnt": payload.cnt, "available_at": canonical_time(payload.available_at)}
    )
    try:
        request.app.state.database.add_observation(
            payload.timestamp, payload.cnt, payload.available_at, source_hash
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"status": "accepted", "timestamp": canonical_time(payload.timestamp), "available_at": canonical_time(payload.available_at)}


@app.post("/predict")
def predict(payload: PredictionRequest, request: Request):
    if payload.target_hour.tzinfo is not None or payload.as_of.tzinfo is not None:
        raise HTTPException(status_code=422, detail="Timestamps must be naive dataset-local time")
    if payload.feature_contract_version != FEATURE_CONTRACT_VERSION:
        raise HTTPException(
            status_code=409,
            detail=f"Unsupported feature contract; this process uses {FEATURE_CONTRACT_VERSION}",
        )
    try:
        target, as_of = canonical_time(payload.target_hour), canonical_time(payload.as_of)
        request_hash = stable_hash(
            {
                "target_hour": target,
                "as_of": as_of,
                "feature_contract_version": payload.feature_contract_version,
            }
        )
        saved = request.app.state.database.saved_prediction(payload.request_id)
        if saved:
            if saved["input_hash"] != request_hash:
                raise HTTPException(status_code=409, detail="request_id was already used with different input")
            return _prediction_response(saved)
        if request.app.state.model is None:
            raise HTTPException(status_code=503, detail=request.app.state.model_error)
        observations = request.app.state.database.observations_as_of(payload.target_hour, payload.as_of)
        features = build_feature_row(payload.target_hour, payload.as_of, observations)
        feature_frame = pd.DataFrame([features], columns=FEATURE_COLUMNS)
        prediction = predict_non_negative(request.app.state.model, feature_frame)
        feature_sources = {}
        for lag_hours in (24, 168):
            source_timestamp = canonical_time(payload.target_hour - timedelta(hours=lag_hours))
            matching = observations.loc[observations["timestamp"] == source_timestamp]
            feature_sources[f"lag_{lag_hours}"] = (
                {
                    "timestamp": source_timestamp,
                    "cnt": int(matching.iloc[0]["cnt"]),
                    "data_hash": str(matching.iloc[0]["data_hash"]),
                }
                if not matching.empty
                else None
            )
        data_version_hash = stable_hash(feature_sources)
        record = {
            "request_id": payload.request_id,
            "input_hash": request_hash,
            "target_hour": target,
            "as_of": as_of,
            "predicted": prediction,
            "model_name": MODEL_NAME,
            "model_version": request.app.state.model_version,
            "model_quality_gate": request.app.state.model_quality_gate,
            "artifact_sha256": request.app.state.artifact_hash,
            "data_version_sha256": data_version_hash,
            "feature_contract_version": FEATURE_CONTRACT_VERSION,
            "features_json": json.dumps(
                {
                    name: value if math.isfinite(value) else None
                    for name, value in features.items()
                },
                sort_keys=True,
                allow_nan=False,
            ),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        saved = request.app.state.database.save_prediction(record)
        return _prediction_response(saved)
    except HTTPException:
        raise
    except RequestConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        # A successful prediction must never be returned when its audit row failed to persist.
        raise HTTPException(status_code=503, detail=f"Prediction could not be recorded: {error}") from error


def _prediction_response(record: dict) -> dict:
    return {
        "request_id": record["request_id"],
        "target_hour": record["target_hour"],
        "prediction": float(record["predicted"]),
        "model_name": record["model_name"],
        "model_version": str(record["model_version"]),
        "model_quality_gate": record["model_quality_gate"],
        "artifact_sha256": record["artifact_sha256"],
        "data_version_sha256": record["data_version_sha256"],
        "feature_contract_version": record["feature_contract_version"],
    }
