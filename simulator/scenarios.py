"""Synthetic telemetry for metrics UNSW-NB15 does not contain (generator v0).

UNSW-NB15 has no CPU or interface-utilisation columns (architecture gap G1),
so these are generated and stored as clearly synthetic values. They are NOT
used as model features, so they cannot leak the label into the detector.

The named fault scenarios (congestion, server_overload, link_flap, ddos)
are added here in Week 6a.
"""

from __future__ import annotations

import numpy as np


def generate_interface_util(throughput_mbps, capacity_mbps) -> np.ndarray:
    """Interface utilisation % = throughput / link capacity, clipped to 0..100."""
    tp = np.asarray(throughput_mbps, dtype=float)
    cap = np.asarray(capacity_mbps, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        util = np.where(cap > 0, tp / cap * 100.0, 0.0)
    return np.clip(util, 0.0, 100.0)


def generate_cpu(throughput_mbps, label=None, rng: np.random.Generator | None = None) -> np.ndarray:
    """CPU usage % that rises with traffic, plus a bump during attacks.

    base ~20%  +  8% per decade of throughput  +  ~25% if attack  +  noise.
    """
    rng = rng or np.random.default_rng()
    tp = np.asarray(throughput_mbps, dtype=float)
    cpu = 20.0 + 8.0 * np.log10(1.0 + tp) + rng.normal(0.0, 5.0, tp.shape)
    if label is not None:
        cpu = cpu + 25.0 * np.asarray(label, dtype=float)
    return np.clip(cpu, 0.0, 100.0)
