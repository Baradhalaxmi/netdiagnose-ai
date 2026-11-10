"""Score stored metrics with the detector and write results to `anomalies`."""

from __future__ import annotations

import pandas as pd
from sqlalchemy.orm import Session, sessionmaker

from backend.db import crud
from ml.predict import Detector


def score_rows(detector: Detector, rows: list[tuple]) -> list[dict]:
    """Turn (metric_id, device_id, ts, features) rows into anomalies rows."""
    if not rows:
        return []
    ids, device_ids, ts, features = zip(*rows)
    scored = detector.score_frame(pd.DataFrame(list(features)))
    return [
        {
            "metric_id": mid,
            "device_id": did,
            "detected_at": t,          # when the traffic happened, so per-hour queries line up
            "score": float(s),
            "is_anomaly": bool(flag),
            "model_version": detector.version,
        }
        for mid, did, t, s, flag in zip(
            ids, device_ids, ts, scored["score"], scored["is_anomaly"]
        )
    ]


def score_pending(
    session_factory: sessionmaker, detector: Detector, batch_size: int = 10_000, log=print
) -> dict:
    """Score every metric that has no anomalies row yet. One transaction per batch."""
    with session_factory.begin() as session:
        crud.register_model(session, detector.meta)

    scored = flagged = 0
    while True:
        with session_factory.begin() as session:
            batch = score_batch(session, detector, batch_size)
        if batch is None:
            break
        scored += batch[0]
        flagged += batch[1]
        log(f"  scored {scored:,} rows, {flagged:,} anomalies")
    return {"scored": scored, "anomalies": flagged, "model_version": detector.version}


def score_batch(session: Session, detector: Detector, batch_size: int) -> tuple[int, int] | None:
    rows = crud.unscored_metrics(session, batch_size)
    if not rows:
        return None
    results = score_rows(detector, rows)
    crud.bulk_insert_anomalies(session, results)
    return len(results), sum(r["is_anomaly"] for r in results)
