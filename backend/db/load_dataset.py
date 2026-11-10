"""Load UNSW-NB15 flows into PostgreSQL and score them.

    python -m backend.db.load_dataset                    # testing set, all rows, then score
    python -m backend.db.load_dataset --limit 60000 --days 3
    python -m backend.db.load_dataset --no-score          # load only
    python -m backend.db.load_dataset --score-only        # score rows already loaded

Pipeline: CSV -> add_metrics() -> synthetic CPU / utilisation -> assign each
flow to a device and a timestamp -> network_metrics -> detector -> anomalies.

The *testing* CSV is the default because the model never saw it in
training, so the stored anomaly flags reflect real detector behaviour.
"""

from __future__ import annotations

import argparse
import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from backend.config import get_settings
from backend.db import crud
from backend.db.init_db import seed_devices
from backend.db.session import SessionLocal
from backend.services.detection_service import score_pending
from ml.features import add_metrics, split_xy
from ml.predict import Detector
from simulator.scenarios import generate_cpu, generate_interface_util

METRIC_FIELDS = ["latency_ms", "packet_loss_pct", "jitter_ms", "throughput_mbps"]


def build_metric_rows(
    df: pd.DataFrame,
    devices: list[dict],
    end: datetime,
    days: float,
    rng: np.random.Generator,
) -> list[dict]:
    """Turn raw dataset rows into network_metrics rows.

    Rows are shuffled, then spread evenly over ``days`` ending at ``end``,
    and each is assigned to a random device. ``devices`` items need ``id``
    and ``link_capacity_mbps``.
    """
    df = df.sample(frac=1.0, random_state=int(rng.integers(1 << 31))).reset_index(drop=True)
    n = len(df)

    metrics = add_metrics(df)
    X, y = split_xy(df)

    dev_idx = rng.integers(0, len(devices), n)
    device_ids = np.array([d["id"] for d in devices])[dev_idx]
    capacity = np.array([float(d["link_capacity_mbps"]) for d in devices])[dev_idx]

    start = end - timedelta(days=days)
    offsets = np.sort(rng.uniform(0, days * 86_400, n))
    ts = [start + timedelta(seconds=float(s)) for s in offsets]

    cpu = generate_cpu(metrics["throughput_mbps"], y, rng)
    util = generate_interface_util(metrics["throughput_mbps"], capacity)

    feature_records = X.to_dict(orient="records")
    labels = y.tolist() if y is not None else [None] * n
    metric_values = metrics[METRIC_FIELDS].to_dict(orient="records")

    rows = []
    for i in range(n):
        row = {k: _nullable(v) for k, v in metric_values[i].items()}
        row.update(
            device_id=int(device_ids[i]),
            ts=ts[i],
            cpu_usage_pct=float(cpu[i]),
            interface_utilization_pct=float(util[i]),
            features=feature_records[i],
            source="dataset",
            true_label=None if labels[i] is None else int(labels[i]),
        )
        rows.append(row)
    return rows


def _nullable(v):
    """NaN -> None so it is stored as SQL NULL."""
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


def load(csv_path: str, limit: int | None, days: float, seed: int, chunk: int) -> int:
    seed_devices()
    with SessionLocal() as session:
        devices = [
            {"id": d.id, "link_capacity_mbps": d.link_capacity_mbps}
            for d in crud.list_devices(session)
        ]

    df = pd.read_csv(csv_path)
    if limit:
        df = df.sample(n=min(limit, len(df)), random_state=seed)
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    rows = build_metric_rows(df, devices, end, days, np.random.default_rng(seed))

    for i in range(0, len(rows), chunk):
        with SessionLocal.begin() as session:
            crud.bulk_insert_metrics(session, rows[i : i + chunk])
        print(f"  inserted {min(i + chunk, len(rows)):,}/{len(rows):,} metric rows")
    return len(rows)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Load UNSW-NB15 into PostgreSQL and score it.")
    p.add_argument("--csv", default="data/raw/UNSW_NB15_testing-set.csv")
    p.add_argument("--limit", type=int, default=None, help="load a random sample of N rows")
    p.add_argument("--days", type=float, default=7, help="spread rows over this many days")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--chunk", type=int, default=5000)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--no-score", action="store_true")
    g.add_argument("--score-only", action="store_true")
    args = p.parse_args(argv)

    if not args.score_only:
        n = load(args.csv, args.limit, args.days, args.seed, args.chunk)
        print(f"Loaded {n:,} rows into network_metrics")

    if not args.no_score:
        settings = get_settings()
        detector = Detector.load(settings.model_path, settings.anomaly_threshold)
        print(f"Scoring with {detector.version} (threshold {detector.threshold:.4f})")
        result = score_pending(SessionLocal, detector)
        print(f"Scored {result['scored']:,} rows, {result['anomalies']:,} flagged as anomalies")


if __name__ == "__main__":
    main()
