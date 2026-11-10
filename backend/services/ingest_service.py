"""POST /metrics orchestration: store -> detect -> correlate into incidents."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.db.models import Anomaly, Device, NetworkMetric
from backend.errors import AppError
from backend.schemas.metrics import IncidentTouched, MetricIn, MetricsIngestResult
from backend.services import incident_service
from ml.features import add_metrics
from ml.predict import Detector

DERIVED = ("latency_ms", "jitter_ms", "packet_loss_pct", "throughput_mbps")
DERIVE_INPUTS = {"tcprtt", "sjit", "djit", "sloss", "dloss", "spkts", "dpkts", "sload", "dload"}


def ingest(
    session: Session,
    rows: list[MetricIn],
    detector: Detector | None,
    settings: Settings,
    detect: bool = True,
) -> MetricsIngestResult:
    _check_devices(session, {r.device_id for r in rows})
    if detect:
        if detector is None:
            raise AppError(503, "MODEL_UNAVAILABLE", "Anomaly model is not loaded")
        _check_features(rows, detector.features)

    now = datetime.now(timezone.utc)
    metrics = [NetworkMetric(**_metric_values(r, now)) for r in rows]
    session.add_all(metrics)
    session.flush()  # assigns ids

    anomalies: list[Anomaly] = []
    if detect:
        scored = detector.score_frame(pd.DataFrame([r.features for r in rows]))
        for m, score, flag in zip(metrics, scored["score"], scored["is_anomaly"]):
            anomalies.append(
                Anomaly(
                    metric_id=m.id, device_id=m.device_id, detected_at=m.ts,
                    score=float(score), is_anomaly=bool(flag), model_version=detector.version,
                )
            )
        session.add_all(anomalies)
        session.flush()

    incident_service.close_stale(session, settings.incident_quiet_minutes, now)
    touched = incident_service.correlate(session, anomalies, settings.incident_window_minutes)
    session.commit()

    return MetricsIngestResult(
        inserted=len(metrics),
        anomalies=sum(a.is_anomaly for a in anomalies),
        incidents_touched=[IncidentTouched(id=t.id, code=t.code, created=t.created) for t in touched],
    )


def _check_devices(session: Session, ids: set[int]) -> None:
    found = set(session.scalars(select(Device.id).where(Device.id.in_(ids))))
    missing = sorted(ids - found)
    if missing:
        raise AppError(404, "NOT_FOUND", f"Unknown device_id(s): {missing}")


def _check_features(rows: list[MetricIn], required: list[str]) -> None:
    for i, r in enumerate(rows):
        missing = [f for f in required if f not in (r.features or {})]
        if missing:
            shown = ", ".join(missing[:5]) + (" ..." if len(missing) > 5 else "")
            raise AppError(
                422, "VALIDATION_ERROR",
                f"rows[{i}].features is missing {len(missing)} model feature(s): {shown}",
            )


def _metric_values(r: MetricIn, now: datetime) -> dict:
    values = r.model_dump(exclude={"features"})
    values["ts"] = values["ts"] or now
    values["features"] = r.features
    # Fill any network metric the client left out from the raw flow fields.
    if r.features and DERIVE_INPUTS <= r.features.keys():
        derived = add_metrics(pd.DataFrame([r.features])).iloc[0]
        for k in DERIVED:
            if values[k] is None and pd.notna(derived[k]):
                values[k] = float(derived[k])
    return values
