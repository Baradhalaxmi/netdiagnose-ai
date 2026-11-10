from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from backend.db.models import Device, Diagnosis, Incident
from backend.deps import Pagination, get_session
from backend.errors import AppError
from backend.schemas.common import Page
from backend.schemas.incidents import (
    DeviceRef,
    IncidentDetail,
    IncidentPatch,
    IncidentSummary,
    Severity,
    Status,
)

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.get("", response_model=Page[IncidentSummary])
def list_incidents(
    status: Status | None = None,
    severity: Severity | None = None,
    device_id: int | None = None,
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = None,
    page: Pagination = Depends(),
    session: Session = Depends(get_session),
):
    # Latest diagnosis per incident gives the "top cause" column.
    ranked = select(
        Diagnosis.incident_id,
        Diagnosis.probable_cause,
        func.row_number()
        .over(partition_by=Diagnosis.incident_id, order_by=Diagnosis.created_at.desc())
        .label("rn"),
    ).subquery()
    latest = (
        select(ranked.c.incident_id, ranked.c.probable_cause).where(ranked.c.rn == 1).subquery()
    )
    q = (
        select(Incident, Device.hostname, latest.c.probable_cause)
        .join(Device, Device.id == Incident.device_id)
        .outerjoin(latest, latest.c.incident_id == Incident.id)
    )
    if status:
        q = q.where(Incident.status == status)
    if severity:
        q = q.where(Incident.severity == severity)
    if device_id is not None:
        q = q.where(Incident.device_id == device_id)
    if from_:
        q = q.where(Incident.last_seen_at >= from_)
    if to:
        q = q.where(Incident.opened_at < to)

    total = session.scalar(select(func.count()).select_from(q.subquery()))
    rows = session.execute(
        q.order_by(Incident.last_seen_at.desc()).limit(page.limit).offset(page.offset)
    ).all()
    items = [
        IncidentSummary(
            id=i.id, code=i.code, device_id=i.device_id, hostname=host, status=i.status,
            severity=i.severity, peak_score=i.peak_score, anomaly_count=i.anomaly_count,
            opened_at=i.opened_at, last_seen_at=i.last_seen_at, closed_at=i.closed_at,
            top_cause=cause,
        )
        for i, host, cause in rows
    ]
    return Page(items=items, total=int(total or 0), limit=page.limit, offset=page.offset)


@router.get("/{incident_id}", response_model=IncidentDetail)
def get_incident(incident_id: int, session: Session = Depends(get_session)):
    return _detail(_load(session, incident_id))


@router.patch("/{incident_id}", response_model=IncidentDetail)
def patch_incident(incident_id: int, body: IncidentPatch, session: Session = Depends(get_session)):
    inc = _load(session, incident_id)
    if body.status is not None:
        inc.status = body.status
        if body.status in ("resolved", "closed") and inc.closed_at is None:
            inc.closed_at = func.now()
    if body.summary is not None:
        inc.summary = body.summary
    session.commit()
    return _detail(_load(session, incident_id))


def _load(session: Session, incident_id: int) -> Incident:
    inc = session.scalars(
        select(Incident)
        .where(Incident.id == incident_id)
        .options(
            selectinload(Incident.device),
            selectinload(Incident.evidence),
            selectinload(Incident.diagnoses),
        )
        .execution_options(populate_existing=True)
    ).first()
    if inc is None:
        raise AppError(404, "NOT_FOUND", f"Incident {incident_id} does not exist")
    return inc


def _detail(inc: Incident) -> IncidentDetail:
    return IncidentDetail(
        id=inc.id, code=inc.code, status=inc.status, severity=inc.severity,
        peak_score=inc.peak_score, anomaly_count=inc.anomaly_count, opened_at=inc.opened_at,
        last_seen_at=inc.last_seen_at, closed_at=inc.closed_at, summary=inc.summary,
        device=DeviceRef(id=inc.device.id, hostname=inc.device.hostname),
        evidence=sorted(inc.evidence, key=lambda e: e.id),
        diagnoses=sorted(inc.diagnoses, key=lambda d: d.created_at, reverse=True),
    )
