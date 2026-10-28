from __future__ import annotations

import os
from pathlib import Path

from bike_mlops.profiles import load_data_profile

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
REPORT_DIR = DATA_DIR / "reports"
DATA_PROFILE_ID = os.getenv("DATA_PROFILE_ID", "uci-bike-sharing")
DATA_PROFILE = load_data_profile(DATA_PROFILE_ID)
TARGET_CONTRACT_ID = DATA_PROFILE["target_contract_id"]
profile_suffix = "" if DATA_PROFILE_ID == "uci-bike-sharing" else f"-{DATA_PROFILE_ID}"
if profile_suffix:
    REPORT_DIR = REPORT_DIR / DATA_PROFILE_ID
RUNTIME_DIR = DATA_DIR / (
    "runtime" if DATA_PROFILE_ID == "uci-bike-sharing" else f"runtime-{DATA_PROFILE_ID}"
)
DATASET_URL = "https://archive.ics.uci.edu/static/public/275/bike+sharing+dataset.zip"
DATASET_ARCHIVE = RAW_DIR / "bike-sharing-dataset.zip"
DATASET_MANIFEST = PROJECT_ROOT / DATA_PROFILE["default_manifest"]
DEFAULT_DATASET = PROJECT_ROOT / DATA_PROFILE["default_csv"]
MODEL_NAME = os.getenv("MLFLOW_MODEL_NAME", DATA_PROFILE["model_name"])
FEATURE_CONTRACT_VERSION = os.getenv(
    "FEATURE_CONTRACT_VERSION", DATA_PROFILE["feature_contract_version"]
)
LABEL_DELAY_HOURS = DATA_PROFILE["label_availability"]["delay_hours"]
DEFAULT_DATABASE_URL = os.getenv(
    "DATABASE_URL", f"sqlite:///{DATA_DIR / f'bikeops{profile_suffix}.db'}"
)
DEFAULT_MLFLOW_URI = os.getenv(
    "MLFLOW_TRACKING_URI", f"sqlite:///{DATA_DIR / f'mlflow{profile_suffix}.db'}"
)
