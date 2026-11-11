"""Rule-based diagnosis of an incident: window + baseline -> evidence -> ranked causes.

* Window: every metric row for the device between the incident's first and
  last anomaly.
* Baseline: the device's non-anomalous rows in the DIAGNOSIS_BASELINE_MINUTES
  before the incident (falls back to the most recent 200 such rows).
* Evidence is computed once per incident and reused; ``force=True`` rebuilds
  evidence and the rules diagnosis.
* The diagnosis is cached by (incident, source, evidence_hash).
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta

import pandas as pd
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.db.models import Anomaly, Device, Diagnosis, Evidence, Incident, NetworkMetric
from backend.errors import AppError
from diagnosis import evidence as ev
from diagnosis.report import render_rules_report
from diagnosis.rules import evaluate

MIN_BASELINE_ROWS = 10
FALLBACK_BASELINE_ROWS = 200
METRIC_COLUMNS = [getattr(NetworkMetric, m) for m in ev.METRICS]


def diagnose(session: Session, incident_id: int, settings: Settings,
             force: bool = False) -> tuple[Diagnosis, bool]:
    """Return (rules diagnosis, created). ``created`` is False on a cache hit."""
    inc = session.get(Incident, incident_id)
    if inc is None:
        raise AppError(404, "NOT_FOUND", f"Incident {incident_id} does not exist")

    if force:
        session.execute(delete(Diagnosis).where(Diagnosis.incident_id == inc.id,
                                                Diagnosis.source == "rules"))
        session.execute(delete(Evidence).where(Evidence.incident_id == inc.id))
        session.flush()

    evidence_rows = list(session.scalars(
        select(Evidence).where(Evidence.incident_id == inc.id).order_by(Evidence.id)))
    if not evidence_rows:
        evidence_rows = _build_evidence(session, inc, settings)

    items = [_as_item(e) for e in evidence_rows]
    ev_hash = _hash(items)
    cached = session.scalars(select(Diagnosis).where(
        Diagnosis.incident_id == inc.id, Diagnosis.source == "rules",
        Diagnosis.evidence_hash == ev_hash)).first()
    if cached is not None:
        session.commit()
        return cached, False

    causes = evaluate(items)
    hostname = session.scalar(select(Device.hostname).where(Device.id == inc.device_id))
    top = causes[0] if causes else None
    diag = Diagnosis(
        incident_id=inc.id,
        source="rules",
        probable_cause=top["cause"] if top else "Unclassified anomaly",
        confidence=top["confidence"] if top else 0.0,
        alternatives=[{"rule_id": c["rule_id"], "cause": c["cause"], "confidence": c["confidence"]}
                      for c in causes[1:]],
        recommendations=top["investigate"] if top else [],
        cited_evidence=top["evidence_ids"] if top else [],
        report_markdown=render_rules_report(inc.code, hostname, causes, items),
        evidence_hash=ev_hash,
    )
    session.add(diag)
    session.commit()
    return diag, True


def _build_evidence(session: Session, inc: Incident, settings: Settings) -> list[Evidence]:
    window = _frame(session.execute(
        select(*METRIC_COLUMNS).where(
            NetworkMetric.device_id == inc.device_id,
            NetworkMetric.ts >= inc.opened_at,
            NetworkMetric.ts <= inc.last_seen_at,
        )).all())
    baseline = _baseline(session, inc, settings.diagnosis_baseline_minutes)
    rows = [
        Evidence(
            incident_id=inc.id, metric_name=e["metric"], observed_value=e["observed"],
            baseline_value=e["baseline"], ratio=e["ratio"], threshold=e["threshold"],
            breached=e["breached"], note=e["note"],
        )
        for e in ev.collect(window, baseline)
    ]
    session.add_all(rows)
    session.flush()
    return rows


def _baseline(session: Session, inc: Incident, minutes: int) -> pd.DataFrame:
    normal = (
        select(*METRIC_COLUMNS)
        .outerjoin(Anomaly, Anomaly.metric_id == NetworkMetric.id)
        .where(NetworkMetric.device_id == inc.device_id,
               NetworkMetric.ts < inc.opened_at,
               or_(Anomaly.id.is_(None), Anomaly.is_anomaly.is_(False)))
    )
    recent = session.execute(
        normal.where(NetworkMetric.ts >= inc.opened_at - timedelta(minutes=minutes))).all()
    if len(recent) >= MIN_BASELINE_ROWS:
        return _frame(recent)
    return _frame(session.execute(
        normal.order_by(NetworkMetric.ts.desc()).limit(FALLBACK_BASELINE_ROWS)).all())


def _frame(rows) -> pd.DataFrame:
    return pd.DataFrame([tuple(r) for r in rows], columns=ev.METRICS, dtype=float)


def _as_item(e: Evidence) -> dict:
    return {"id": e.id, "metric": e.metric_name, "observed": e.observed_value,
            "baseline": e.baseline_value, "ratio": e.ratio, "threshold": e.threshold,
            "breached": e.breached}


def _hash(items: list[dict]) -> str:
    payload = json.dumps([[i["id"], i["metric"], i["observed"]] for i in items], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
