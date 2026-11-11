"""Read/write helpers. All SQL goes through SQLAlchemy (parameterised)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, selectinload

from backend.db.models import Anomaly, Device, ModelRegistry, NetworkMetric


# --------------------------------------------------------------------
# Devices
# --------------------------------------------------------------------
def upsert_devices(session: Session, devices: Sequence[dict]) -> int:
    """Insert devices that do not exist yet (matched by hostname)."""
    if not devices:
        return 0
    stmt = pg_insert(Device).values(list(devices)).on_conflict_do_nothing(
        index_elements=["hostname"]
    )
    return session.execute(stmt).rowcount


def list_devices(session: Session) -> list[Device]:
    return list(session.scalars(select(Device).order_by(Device.id)))


# --------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------
def bulk_insert_metrics(session: Session, rows: Sequence[dict]) -> int:
    """Insert many network_metrics rows in one round-trip batch."""
    if not rows:
        return 0
    session.execute(pg_insert(NetworkMetric), list(rows))
    return len(rows)


def get_metrics(
    session: Session,
    device_id: int | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[NetworkMetric], int]:
    """Metrics newest first, plus the total count for pagination."""
    q = select(NetworkMetric)
    if device_id is not None:
        q = q.where(NetworkMetric.device_id == device_id)
    if start is not None:
        q = q.where(NetworkMetric.ts >= start)
    if end is not None:
        q = q.where(NetworkMetric.ts < end)
    total = session.scalar(select(func.count()).select_from(q.subquery()))
    items = session.scalars(
        q.options(selectinload(NetworkMetric.anomaly))
        .order_by(NetworkMetric.ts.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return list(items), int(total or 0)


def unscored_metrics(session: Session, limit: int) -> list[tuple[int, int, datetime, dict]]:
    """Up to ``limit`` metric rows that have no anomalies row yet, oldest id first."""
    q = (
        select(NetworkMetric.id, NetworkMetric.device_id, NetworkMetric.ts, NetworkMetric.features)
        .outerjoin(Anomaly, Anomaly.metric_id == NetworkMetric.id)
        .where(Anomaly.id.is_(None), NetworkMetric.features.is_not(None))
        .order_by(NetworkMetric.id)
        .limit(limit)
    )
    return [tuple(r) for r in session.execute(q)]


# --------------------------------------------------------------------
# Anomalies
# --------------------------------------------------------------------
def bulk_insert_anomalies(session: Session, rows: Sequence[dict]) -> int:
    """Insert detector results; rows already scored (same metric_id) are skipped."""
    if not rows:
        return 0
    stmt = pg_insert(Anomaly).on_conflict_do_nothing(index_elements=["metric_id"])
    session.execute(stmt, list(rows))
    return len(rows)


def anomalies_per_device_per_hour(session: Session, since: datetime | None = None) -> list[dict]:
    """The Week 4 exit-gate query (also in db/queries.sql)."""
    sql = text(
        """
        SELECT d.hostname,
               date_trunc('hour', a.detected_at) AS hour,
               count(*) FILTER (WHERE a.is_anomaly) AS anomalies,
               count(*)                             AS scored
        FROM anomalies a
        JOIN devices d ON d.id = a.device_id
        WHERE (CAST(:since AS timestamptz) IS NULL OR a.detected_at >= :since)
        GROUP BY d.hostname, hour
        ORDER BY hour, d.hostname
        """
    )
    return [dict(r._mapping) for r in session.execute(sql, {"since": since})]


# --------------------------------------------------------------------
# Model registry
# --------------------------------------------------------------------
def register_model(session: Session, meta: dict, activate: bool = True) -> None:
    """Record a trained model version (from model_meta.json); optionally make it active."""
    stmt = (
        pg_insert(ModelRegistry)
        .values(
            version=meta["model_version"],
            algorithm="isolation_forest",
            threshold=meta["score"]["threshold"],
            metrics=meta["metrics"],
            trained_at=datetime.fromisoformat(meta["trained_at"]),
        )
        .on_conflict_do_nothing(index_elements=["version"])
    )
    session.execute(stmt)
    if activate:
        session.execute(
            update(ModelRegistry).values(
                is_active=(ModelRegistry.version == meta["model_version"])
            )
        )
