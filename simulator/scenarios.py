"""Synthetic telemetry.

1. CPU and interface utilisation for dataset rows (UNSW-NB15 has neither,
   architecture gap G1). Stored as clearly synthetic values and NOT used as
   model features, so they cannot leak the label into the detector.

2. Named fault scenarios with known ground truth (congestion,
   server_overload, link_flap, ddos). Each has a normal profile for its
   typical device and a fault profile; the diagnosis engine should rank
   ``expected_rule`` first. This is what makes diagnosis measurable.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


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


# --------------------------------------------------------------------
# Fault scenarios
# --------------------------------------------------------------------
# Profile: metric -> (mean, standard deviation). Interface utilisation is
# not listed; it is derived from throughput and the device's link capacity.
Profile = dict[str, tuple[float, float]]


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    expected_rule: str
    expected_cause: str
    default_device: str
    normal: Profile
    fault: Profile


SCENARIOS: dict[str, Scenario] = {
    s.name: s
    for s in [
        Scenario(
            "congestion",
            "A link that normally runs hot saturates: queues fill, loss and latency climb.",
            "R-CONGESTION", "Network congestion", "access-sw-01",
            normal={"latency_ms": (20, 4), "packet_loss_pct": (0.2, 0.1), "jitter_ms": (3, 1),
                    "throughput_mbps": (600, 40), "cpu_usage_pct": (30, 4)},
            fault={"latency_ms": (250, 30), "packet_loss_pct": (15, 3), "jitter_ms": (20, 4),
                   "throughput_mbps": (930, 20), "cpu_usage_pct": (40, 4)},
        ),
        Scenario(
            "server_overload",
            "The server's CPU pins near 100%: responses slow down, the network is fine.",
            "R-SERVER-OVERLOAD", "Server overload", "app-srv-01",
            normal={"latency_ms": (30, 5), "packet_loss_pct": (0.2, 0.1), "jitter_ms": (4, 1),
                    "throughput_mbps": (150, 20), "cpu_usage_pct": (40, 5)},
            fault={"latency_ms": (320, 40), "packet_loss_pct": (0.5, 0.2), "jitter_ms": (8, 2),
                   "throughput_mbps": (160, 20), "cpu_usage_pct": (96, 2)},
        ),
        Scenario(
            "link_flap",
            "A physical link keeps going up and down: heavy loss, throughput collapses.",
            "R-LINK-FAULT", "Link failure / flapping", "edge-rtr-01",
            normal={"latency_ms": (15, 3), "packet_loss_pct": (0.1, 0.05), "jitter_ms": (2, 0.5),
                    "throughput_mbps": (300, 30), "cpu_usage_pct": (25, 4)},
            fault={"latency_ms": (90, 30), "packet_loss_pct": (45, 10), "jitter_ms": (22, 6),
                   "throughput_mbps": (120, 40), "cpu_usage_pct": (30, 4)},
        ),
        Scenario(
            "ddos",
            "A traffic flood hits a normally quiet server: throughput jumps 10x and fills the link.",
            "R-TRAFFIC-FLOOD", "Possible DoS / traffic flood", "web-srv-01",
            normal={"latency_ms": (25, 5), "packet_loss_pct": (0.2, 0.1), "jitter_ms": (3, 1),
                    "throughput_mbps": (80, 15), "cpu_usage_pct": (35, 5)},
            fault={"latency_ms": (260, 40), "packet_loss_pct": (12, 3), "jitter_ms": (15, 4),
                   "throughput_mbps": (970, 15), "cpu_usage_pct": (85, 3)},
        ),
    ]
}

LIMITS = {
    "latency_ms": (0.1, None), "packet_loss_pct": (0.0, 100.0), "jitter_ms": (0.0, None),
    "throughput_mbps": (0.0, None), "cpu_usage_pct": (0.0, 100.0),
}


def _sample(profile: Profile, n: int, capacity_mbps: float, rng: np.random.Generator) -> pd.DataFrame:
    df = pd.DataFrame(
        {m: np.clip(rng.normal(mu, sd, n), *LIMITS[m]) for m, (mu, sd) in profile.items()}
    )
    df["throughput_mbps"] = np.minimum(df["throughput_mbps"], capacity_mbps)
    df["interface_utilization_pct"] = generate_interface_util(df["throughput_mbps"], capacity_mbps)
    return df


def normal_rows(name: str, n: int, capacity_mbps: float = 1000,
                rng: np.random.Generator | None = None) -> pd.DataFrame:
    """Healthy telemetry for the scenario's device type (the baseline)."""
    return _sample(SCENARIOS[name].normal, n, capacity_mbps, rng or np.random.default_rng())


def fault_rows(name: str, n: int, capacity_mbps: float = 1000,
               rng: np.random.Generator | None = None) -> pd.DataFrame:
    """Telemetry during the fault."""
    return _sample(SCENARIOS[name].fault, n, capacity_mbps, rng or np.random.default_rng())
