from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.deps import get_session
from backend.schemas.stats import HourCount, StatsSummary

router = APIRouter(prefix="/stats", tags=["stats"])

SEVERITIES = ("low", "medium", "high", "critical")


@router.get("/summary", response_model=StatsSummary)
def summary(session: Session = Depends(get_session)):
    counts = session.execute(
        text(
            """
            SELECT
              (SELECT count(*) FROM devices) AS devices,
              (SELECT count(*) FROM network_metrics WHERE ts >= now() - interval '24 hours') AS metrics_24h,
              (SELECT count(*) FROM anomalies
                 WHERE is_anomaly AND detected_at >= now() - interval '24 hours') AS anomalies_24h,
              (SELECT count(*) FROM incidents WHERE status IN ('open', 'investigating')) AS open_incidents
            """
        )
    ).one()
    by_sev = dict(
        session.execute(
            text(
                "SELECT severity, count(*) FROM incidents "
                "WHERE status IN ('open', 'investigating') GROUP BY severity"
            )
        ).all()
    )
    per_hour = session.execute(
        text(
            """
            SELECT date_trunc('hour', detected_at) AS hour, count(*) AS count
            FROM anomalies
            WHERE is_anomaly AND detected_at >= now() - interval '24 hours'
            GROUP BY hour ORDER BY hour
            """
        )
    ).all()
    return StatsSummary(
        devices=counts.devices,
        metrics_24h=counts.metrics_24h,
        anomalies_24h=counts.anomalies_24h,
        open_incidents=counts.open_incidents,
        incidents_by_severity={s: int(by_sev.get(s, 0)) for s in SEVERITIES},
        anomalies_per_hour=[HourCount(hour=h, count=c) for h, c in per_hour],
    )
