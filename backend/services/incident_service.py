"""Group anomalies into incidents (docs/architecture.md section 9.1).

Rules:
* A flagged anomaly joins the device's open incident if it falls within
  INCIDENT_WINDOW_MINUTES of that incident's time span; otherwise it opens
  a new incident.
* An open incident with no new anomaly for INCIDENT_QUIET_MINUTES (wall
  clock) is closed automatically.
* Severity comes from the peak anomaly score and the number of anomalies.
  Week 6 adds triggered rules to this.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from backend.db.models import Anomaly, Incident

ACTIVE_STATUSES = ("open", "investigating")


@dataclass
class Touched:
    id: int
    code: str
    created: bool


def severity_for(peak_score: float, anomaly_count: int) -> str:
    """Map peak score + volume to a severity band."""
    if peak_score >= 0.9 and anomaly_count >= 50:
        return "critical"
    if peak_score >= 0.8 or anomaly_count >= 20:
        return "high"
    if peak_score >= 0.6 or anomaly_count >= 5:
        return "medium"
    return "low"


def close_stale(session: Session, quiet_minutes: int, now: datetime | None = None) -> int:
    """Close open incidents that have been quiet for ``quiet_minutes``."""
    now = now or datetime.now(timezone.utc)
    quiet = timedelta(minutes=quiet_minutes)
    result = session.execute(
        update(Incident)
        .where(Incident.status == "open", Incident.last_seen_at < now - quiet)
        .values(status="closed", closed_at=Incident.last_seen_at + quiet)
    )
    return result.rowcount


def correlate(
    session: Session,
    anomalies: list[Anomaly],
    window_minutes: int,
) -> list[Touched]:
    """Attach flagged anomalies to incidents. Returns each incident touched once,
    with ``created=True`` if this call opened it."""
    window = timedelta(minutes=window_minutes)
    touched: dict[int, Touched] = {}

    for a in sorted((a for a in anomalies if a.is_anomaly), key=lambda a: a.detected_at):
        inc = _active_incident(session, a.device_id)
        if inc is not None and inc.opened_at - window <= a.detected_at <= inc.last_seen_at + window:
            inc.anomaly_count += 1
            inc.peak_score = max(inc.peak_score or 0.0, a.score)
            inc.opened_at = min(inc.opened_at, a.detected_at)
            inc.last_seen_at = max(inc.last_seen_at, a.detected_at)
            created = False
        else:
            inc = _open_incident(session, a)
            created = True
        inc.severity = severity_for(inc.peak_score or 0.0, inc.anomaly_count)
        a.incident_id = inc.id
        session.flush()

        prev = touched.get(inc.id)
        touched[inc.id] = Touched(inc.id, inc.code, created or (prev.created if prev else False))
    return list(touched.values())


def _active_incident(session: Session, device_id: int) -> Incident | None:
    return session.scalars(
        select(Incident)
        .where(Incident.device_id == device_id, Incident.status.in_(ACTIVE_STATUSES))
        .order_by(Incident.last_seen_at.desc())
        .limit(1)
    ).first()


def _open_incident(session: Session, a: Anomaly) -> Incident:
    # Take the id first so the human-readable code (INC-0007) can be set on insert.
    new_id = session.execute(
        text("SELECT nextval(pg_get_serial_sequence('incidents', 'id'))")
    ).scalar_one()
    inc = Incident(
        id=new_id,
        code=f"INC-{new_id:04d}",
        device_id=a.device_id,
        status="open",
        peak_score=a.score,
        anomaly_count=1,
        opened_at=a.detected_at,
        last_seen_at=a.detected_at,
    )
    session.add(inc)
    session.flush()
    return inc
