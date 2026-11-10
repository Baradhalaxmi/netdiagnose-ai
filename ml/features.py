"""Feature mapping and selection shared by training and inference.

Everything that turns a UNSW-NB15 row into model input or NetDiagnose
metrics lives here, so train.py and predict.py can never drift apart
(train/serve skew). See docs/architecture.md section 7.2.

Units verified against UNSW_NB15_training-set.csv:
    tcprtt, synack, ackdat  seconds
    sjit, djit              milliseconds
    sload, dload            bits per second
    sloss, dloss            packet counts (can exceed spkts/dpkts in a few rows)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder

# --------------------------------------------------------------------
# Column definitions
# --------------------------------------------------------------------
ID_COL = "id"
LABEL_COL = "label"            # 0 = normal, 1 = attack
ATTACK_CAT_COL = "attack_cat"
NON_FEATURE_COLS = [ID_COL, ATTACK_CAT_COL, LABEL_COL]

CATEGORICAL_COLS = ["proto", "service", "state"]

METRIC_COLS = ["latency_ms", "jitter_ms", "packet_loss_pct", "throughput_mbps"]


# --------------------------------------------------------------------
# Metric derivation (dataset -> NetDiagnose metrics)
# Each function works on scalars or pandas Series.
# --------------------------------------------------------------------
def latency_ms(tcprtt):
    """Round-trip time in ms from ``tcprtt`` (seconds).

    ``tcprtt`` is only measured for TCP; it is 0 for every non-TCP flow.
    A 0 means "not measured", not "zero latency", so it becomes NaN
    (stored as NULL in network_metrics).
    """
    rtt = np.asarray(tcprtt, dtype=float)
    out = np.where(rtt > 0, rtt * 1000.0, np.nan)
    return _like(tcprtt, out)


def jitter_ms(sjit, djit):
    """Mean of source and destination jitter (both already in ms)."""
    out = (np.asarray(sjit, dtype=float) + np.asarray(djit, dtype=float)) / 2.0
    return _like(sjit, out)


def packet_loss_pct(sloss, dloss, spkts, dpkts):
    """Lost packets as a percentage of all packets, clipped to 0..100.

    Returns 0 when the flow has no packets (avoids divide-by-zero).
    """
    lost = np.asarray(sloss, dtype=float) + np.asarray(dloss, dtype=float)
    total = np.asarray(spkts, dtype=float) + np.asarray(dpkts, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        pct = np.where(total > 0, lost / total * 100.0, 0.0)
    return _like(sloss, np.clip(pct, 0.0, 100.0))


def throughput_mbps(sload, dload):
    """Combined load in megabits per second (inputs are bits/s)."""
    out = (np.asarray(sload, dtype=float) + np.asarray(dload, dtype=float)) / 1e6
    return _like(sload, out)


def add_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of ``df`` with the four derived metric columns added."""
    out = df.copy()
    out["latency_ms"] = latency_ms(df["tcprtt"])
    out["jitter_ms"] = jitter_ms(df["sjit"], df["djit"])
    out["packet_loss_pct"] = packet_loss_pct(
        df["sloss"], df["dloss"], df["spkts"], df["dpkts"]
    )
    out["throughput_mbps"] = throughput_mbps(df["sload"], df["dload"])
    return out


# --------------------------------------------------------------------
# Model input
# --------------------------------------------------------------------
def split_xy(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series | None]:
    """Split a raw dataset frame into features X and label y.

    ``y`` is None when the frame has no label (e.g. live inference).
    """
    X = df.drop(columns=[c for c in NON_FEATURE_COLS if c in df.columns])
    y = df[LABEL_COL] if LABEL_COL in df.columns else None
    return X, y


def numerical_cols(X: pd.DataFrame) -> list[str]:
    """All feature columns that are not categorical, in frame order."""
    return [c for c in X.columns if c not in CATEGORICAL_COLS]


def build_preprocessor(X: pd.DataFrame) -> ColumnTransformer:
    """One-hot encode categoricals, pass numerics through.

    Same transform as notebooks/01_exploration.ipynb. Unknown categories
    at inference time are ignored instead of raising.
    """
    return ColumnTransformer(
        transformers=[
            (
                "categorical",
                OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                CATEGORICAL_COLS,
            ),
            ("numerical", "passthrough", numerical_cols(X)),
        ]
    )


# --------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------
def _like(template, values: np.ndarray):
    """Return ``values`` shaped like ``template`` (Series, scalar or array)."""
    if isinstance(template, pd.Series):
        return pd.Series(values, index=template.index)
    if np.ndim(values) == 0:
        return float(values)
    return values
