"""ASGI entry point for the Capital candidate-only staging service."""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from bike_mlops import api as runtime_api
from bike_mlops.capital_staging import load_staging_registered_model
from bike_mlops.config import DATA_PROFILE_ID, TARGET_CONTRACT_ID
from bike_mlops.db import Database
from fastapi import FastAPI


def _staging_runtime_model():
    model_version = os.getenv("CAPITAL_STAGING_MODEL_VERSION")
    if not model_version:
        return None, None, None, None, "CAPITAL_STAGING_MODEL_VERSION is not set"
    deadline = time.monotonic() + 45
    last_error = None
    while time.monotonic() < deadline:
        try:
            model, version, artifact_hash, quality_gate, error = load_staging_registered_model()
            return model, version, artifact_hash, quality_gate, error
        except Exception as error:  # keep API startup diagnosable while MLflow finishes starting
            last_error = error
            time.sleep(2)
    return None, model_version, None, None, f"Could not load pinned staging candidate: {last_error}"


@asynccontextmanager
async def _staging_lifespan(app):
    database = Database(
        os.getenv("DATABASE_URL", ""), DATA_PROFILE_ID, TARGET_CONTRACT_ID
    )
    database.initialize()
    model, version, artifact_hash, quality_gate, model_error = _staging_runtime_model()
    app.state.database = database
    app.state.model = model
    app.state.model_version = version
    app.state.artifact_hash = artifact_hash
    app.state.model_quality_gate = quality_gate
    app.state.model_error = model_error
    app.state.candidate_id = os.getenv("CAPITAL_STAGING_CANDIDATE_ID")
    app.state.started_at = datetime.now(timezone.utc).isoformat()
    yield


app = FastAPI(
    title="Bike Demand Candidate Staging API",
    version=runtime_api.__version__,
    description="Isolated Capital candidate validation endpoint; deployment permission remains blocked.",
    lifespan=_staging_lifespan,
)
app.include_router(runtime_api.api_router)
