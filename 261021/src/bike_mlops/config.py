from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
REPORT_DIR = DATA_DIR / "reports"
RUNTIME_DIR = DATA_DIR / "runtime"
DEFAULT_DATASET = RAW_DIR / "hour.csv"
DATASET_URL = "https://archive.ics.uci.edu/static/public/275/bike+sharing+dataset.zip"
DATASET_ARCHIVE = RAW_DIR / "bike-sharing-dataset.zip"
DATASET_MANIFEST = RAW_DIR / "source-manifest.json"
MODEL_NAME = os.getenv("MLFLOW_MODEL_NAME", "bike-demand")
FEATURE_CONTRACT_VERSION = os.getenv(
    "FEATURE_CONTRACT_VERSION", "bike-demand-features-v1"
)
LABEL_DELAY_HOURS = 3
DEFAULT_DATABASE_URL = os.getenv(
    "DATABASE_URL", f"sqlite:///{DATA_DIR / 'bikeops.db'}"
)
DEFAULT_MLFLOW_URI = os.getenv(
    "MLFLOW_TRACKING_URI", f"sqlite:///{DATA_DIR / 'mlflow.db'}"
)
