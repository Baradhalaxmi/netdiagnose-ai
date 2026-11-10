from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.db import crud
from backend.deps import Pagination, get_detector, get_session, settings_dep
from backend.errors import AppError
from backend.schemas.common import Page
from backend.schemas.metrics import (
    DetectIn,
    DetectOut,
    DeviceOut,
    MetricOut,
    MetricsIn,
    MetricsIngestResult,
)
from backend.services import ingest_service

router = APIRouter(tags=["metrics"])


@router.get("/devices", response_model=Page[DeviceOut])
def list_devices(session: Session = Depends(get_session)):
    devices = crud.list_devices(session)
    return Page(items=devices, total=len(devices), limit=len(devices), offset=0)


@router.get("/metrics", response_model=Page[MetricOut])
def get_metrics(
    device_id: int | None = None,
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = None,
    page: Pagination = Depends(),
    session: Session = Depends(get_session),
):
    items, total = crud.get_metrics(session, device_id, from_, to, page.limit, page.offset)
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.post("/metrics", response_model=MetricsIngestResult, status_code=201)
def post_metrics(
    body: MetricsIn,
    session: Session = Depends(get_session),
    detector=Depends(get_detector),
    settings: Settings = Depends(settings_dep),
):
    """Store rows; if `detect` is true, score them and group anomalies into incidents."""
    return ingest_service.ingest(session, body.rows, detector, settings, body.detect)


@router.post("/detect", response_model=DetectOut)
def detect(body: DetectIn, detector=Depends(get_detector)):
    """Score one flow without storing it."""
    if detector is None:
        raise AppError(503, "MODEL_UNAVAILABLE", "Anomaly model is not loaded")
    try:
        return detector.detect(body.features)
    except ValueError as exc:  # missing features
        raise AppError(422, "VALIDATION_ERROR", str(exc)) from exc
