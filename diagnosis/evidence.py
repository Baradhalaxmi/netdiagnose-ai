"""Evidence: how far each metric in the incident window is from the device's
normal baseline (docs/architecture.md section 9.2).

Pure functions on DataFrames; no database access here.

Baseline uses the median and MAD (median absolute deviation) of
non-anomalous history, which a few outliers in that history cannot drag.
A metric is "breached" when its window mean is above
    median + K * robust_sigma,   robust_sigma = 1.4826 * MAD
with a small per-metric floor so a perfectly flat baseline (MAD = 0)
does not turn tiny changes into alarms.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

METRICS = [
    "latency_ms",
    "packet_loss_pct",
    "jitter_ms",
    "throughput_mbps",
    "cpu_usage_pct",
    "interface_utilization_pct",
]

K = 3.0
MAD_TO_SIGMA = 1.4826
# Smallest deviation treated as meaningful, in each metric's own unit.
MIN_SIGMA = {
    "latency_ms": 2.0,
    "packet_loss_pct": 0.5,
    "jitter_ms": 1.0,
    "throughput_mbps": 1.0,
    "cpu_usage_pct": 2.0,
    "interface_utilization_pct": 2.0,
}


def baseline_stats(baseline: pd.DataFrame) -> dict[str, dict]:
    """Median, MAD and sample size per metric (NaNs ignored)."""
    stats = {}
    for m in METRICS:
        values = _values(baseline, m)
        if len(values) == 0:
            stats[m] = {"median": None, "mad": None, "n": 0}
            continue
        median = float(np.median(values))
        stats[m] = {
            "median": median,
            "mad": float(np.median(np.abs(values - median))),
            "n": int(len(values)),
        }
    return stats


def collect(window: pd.DataFrame, baseline: pd.DataFrame, k: float = K) -> list[dict]:
    """One evidence item per metric that has data in the incident window."""
    stats = baseline_stats(baseline)
    evidence = []
    for m in METRICS:
        values = _values(window, m)
        if len(values) == 0:
            continue  # e.g. latency for non-TCP traffic
        observed = float(values.mean())
        peak = float(values.max())
        s = stats[m]

        if s["median"] is None:
            baseline_value = ratio = threshold = None
            breached = False
            note = f"peak {peak:.2f}; no baseline data"
        else:
            baseline_value = s["median"]
            sigma = max(MAD_TO_SIGMA * s["mad"], 0.05 * abs(baseline_value), MIN_SIGMA[m])
            threshold = baseline_value + k * sigma
            breached = observed > threshold
            ratio = observed / baseline_value if baseline_value > 0 else None
            note = f"peak {peak:.2f}; baseline n={s['n']}"

        evidence.append(
            {
                "metric": m,
                "observed": round(observed, 4),
                "baseline": _round(baseline_value),
                "ratio": _round(ratio),
                "threshold": _round(threshold),
                "breached": bool(breached),
                "note": note,
            }
        )
    return evidence


def _values(df: pd.DataFrame, metric: str) -> np.ndarray:
    if df is None or metric not in df.columns:
        return np.array([])
    v = pd.to_numeric(df[metric], errors="coerce").to_numpy(dtype=float)
    return v[~np.isnan(v)]


def _round(v: float | None, digits: int = 4) -> float | None:
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(v, digits)
