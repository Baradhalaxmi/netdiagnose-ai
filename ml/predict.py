"""Inference for the anomaly detector.

Public contract (docs/architecture.md section 8.6):

    detect_anomaly(record: dict) -> {"is_anomaly": bool, "score": float, "model_version": str}

The score normalisation used here is also imported by ml/train.py, so
training and inference always compute scores the same way.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline

from ml.features import split_xy

DEFAULT_MODEL_PATH = Path(os.getenv("MODEL_PATH", "ml/model.joblib"))


# --------------------------------------------------------------------
# Scoring (shared with train.py)
# --------------------------------------------------------------------
def raw_scores(model: Pipeline, X: pd.DataFrame) -> np.ndarray:
    """Higher = more anomalous. Negates IsolationForest.decision_function."""
    return -model.decision_function(X)


def normalise(raw: np.ndarray, raw_min: float, raw_max: float) -> np.ndarray:
    """Map raw scores to 0..1 using the range seen in training (section 8.4)."""
    span = raw_max - raw_min
    if span <= 0:
        return np.zeros_like(raw, dtype=float)
    return np.clip((raw - raw_min) / span, 0.0, 1.0)


# --------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------
class Detector:
    """A trained pipeline plus the metadata needed to interpret its scores."""

    def __init__(self, model: Pipeline, meta: dict):
        self.model = model
        self.meta = meta
        self.version: str = meta["model_version"]
        self.features: list[str] = meta["features"]
        self.raw_min: float = meta["score"]["raw_min"]
        self.raw_max: float = meta["score"]["raw_max"]
        self.threshold: float = float(
            os.getenv("ANOMALY_THRESHOLD") or meta["score"]["threshold"]
        )

    @classmethod
    def load(cls, model_path: str | Path = DEFAULT_MODEL_PATH) -> "Detector":
        model_path = Path(model_path)
        meta_path = model_path.with_name("model_meta.json")
        if not model_path.exists():
            raise FileNotFoundError(
                f"{model_path} not found. Train it first: python -m ml.train"
            )
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return cls(joblib.load(model_path), meta)

    def score_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        """Score many rows. Returns columns ``score`` and ``is_anomaly``."""
        X, _ = split_xy(df)
        missing = [c for c in self.features if c not in X.columns]
        if missing:
            raise ValueError(f"record is missing features: {missing}")
        X = X[self.features]
        score = normalise(raw_scores(self.model, X), self.raw_min, self.raw_max)
        return pd.DataFrame(
            {"score": score, "is_anomaly": score >= self.threshold}, index=df.index
        )

    def detect(self, record: dict) -> dict:
        row = self.score_frame(pd.DataFrame([record])).iloc[0]
        return {
            "is_anomaly": bool(row["is_anomaly"]),
            "score": round(float(row["score"]), 4),
            "model_version": self.version,
        }


@lru_cache(maxsize=1)
def get_detector() -> Detector:
    """Load the default model once per process (FastAPI calls this at startup)."""
    return Detector.load()


def detect_anomaly(record: dict) -> dict:
    """Score one UNSW-NB15-style flow record with the default model."""
    return get_detector().detect(record)
