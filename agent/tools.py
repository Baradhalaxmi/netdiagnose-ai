"""Read-only tools the agent may call (docs/architecture.md section 10.2).

Every tool is bound to ONE incident when the toolbox is created, so the
model can only read data about that incident and its device; it never
chooses ids for other devices. Nothing here writes to the database.
Raw IP addresses are not exposed (privacy guardrail, section 10.5).
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.models import Device, Diagnosis, Evidence, Incident, NetworkMetric
from diagnosis.evidence import METRICS

TOOL_SPECS: list[dict] = [
    {
        "name": "get_metric_timeseries",
        "description": "Raw samples of one metric for the incident's device, from 15 minutes "
                       "before the incident to its last anomaly (oldest first, at most 60).",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"metric": {"type": "string", "enum": METRICS}},
            "required": ["metric"],
            "additionalProperties": False,
        },
    },
    {
        "name": "compare_baseline",
        "description": "Observed vs baseline for one metric: the stored evidence row plus "
                       "min/mean/max of the metric inside the incident window.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"metric": {"type": "string", "enum": METRICS}},
            "required": ["metric"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_device_status",
        "description": "Type, role, location and link capacity of the incident's device, and "
                       "when it last reported.",
        "strict": True,
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_previous_incidents",
        "description": "Earlier incidents on the same device and how they were diagnosed.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "enum": [3, 5, 10]}},
            "required": ["limit"],
            "additionalProperties": False,
        },
    },
]


class Toolbox:
    def __init__(self, session: Session, incident_id: int):
        self.session = session
        self.incident = session.get(Incident, incident_id)
        if self.incident is None:
            raise ValueError(f"Incident {incident_id} does not exist")
        self._handlers = {
            "get_metric_timeseries": self.get_metric_timeseries,
            "compare_baseline": self.compare_baseline,
            "get_device_status": self.get_device_status,
            "get_previous_incidents": self.get_previous_incidents,
        }

    # ---- dispatch ------------------------------------------------------
    def call(self, name: str, args: dict) -> tuple[str, bool]:
        """Run a tool; returns (JSON text, is_error). Never raises on bad input."""
        handler = self._handlers.get(name)
        if handler is None:
            return json.dumps({"error": f"unknown tool {name!r}"}), True
        try:
            return json.dumps(handler(**args), default=str), False
        except (TypeError, ValueError) as exc:
            return json.dumps({"error": str(exc)}), True

    # ---- context given up front (architecture 10.3 step 2) -------------
    def get_incident(self) -> dict:
        i = self.incident
        return {"id": i.id, "code": i.code, "status": i.status, "severity": i.severity,
                "peak_anomaly_score": i.peak_score, "anomaly_count": i.anomaly_count,
                "opened_at": i.opened_at, "last_anomaly_at": i.last_seen_at}

    def get_rule_findings(self) -> dict:
        evidence = self.session.scalars(
            select(Evidence).where(Evidence.incident_id == self.incident.id).order_by(Evidence.id))
        rules = self.session.scalars(
            select(Diagnosis).where(Diagnosis.incident_id == self.incident.id,
                                    Diagnosis.source == "rules")
            .order_by(Diagnosis.created_at.desc())).first()
        return {
            "evidence": [
                {"evidence_id": e.id, "metric": e.metric_name, "observed": e.observed_value,
                 "baseline": e.baseline_value, "ratio_to_baseline": e.ratio,
                 "abnormal": e.breached}
                for e in evidence],
            "rule_engine": None if rules is None else {
                "top_cause": rules.probable_cause, "confidence": rules.confidence,
                "cited_evidence_ids": rules.cited_evidence,
                "alternatives": rules.alternatives,
                "recommended_investigation": rules.recommendations,
            },
        }

    # ---- tools the model chooses ---------------------------------------
    def get_metric_timeseries(self, metric: str) -> dict:
        self._check_metric(metric)
        col = getattr(NetworkMetric, metric)
        rows = self.session.execute(
            select(NetworkMetric.ts, col).where(
                NetworkMetric.device_id == self.incident.device_id,
                NetworkMetric.ts >= self.incident.opened_at - timedelta(minutes=15),
                NetworkMetric.ts <= self.incident.last_seen_at,
                col.is_not(None))
            .order_by(NetworkMetric.ts.desc()).limit(60)).all()
        return {"metric": metric,
                "samples": [{"ts": t, "value": round(v, 3)} for t, v in reversed(rows)]}

    def compare_baseline(self, metric: str) -> dict:
        self._check_metric(metric)
        col = getattr(NetworkMetric, metric)
        ev = self.session.scalars(select(Evidence).where(
            Evidence.incident_id == self.incident.id, Evidence.metric_name == metric)).first()
        lo, mean, hi, n = self.session.execute(
            select(func.min(col), func.avg(col), func.max(col), func.count(col)).where(
                NetworkMetric.device_id == self.incident.device_id,
                NetworkMetric.ts >= self.incident.opened_at,
                NetworkMetric.ts <= self.incident.last_seen_at)).one()
        return {
            "metric": metric,
            "evidence": None if ev is None else {
                "evidence_id": ev.id, "observed": ev.observed_value, "baseline": ev.baseline_value,
                "ratio_to_baseline": ev.ratio, "abnormal_above": ev.threshold,
                "abnormal": ev.breached},
            "incident_window": {"samples": n, "min": lo, "mean": mean, "max": hi},
        }

    def get_device_status(self) -> dict:
        d = self.session.get(Device, self.incident.device_id)
        last = self.session.scalar(
            select(func.max(NetworkMetric.ts)).where(NetworkMetric.device_id == d.id))
        return {"hostname": d.hostname, "device_type": d.device_type, "location": d.location,
                "link_capacity_mbps": float(d.link_capacity_mbps), "last_reported_at": last}

    def get_previous_incidents(self, limit: int) -> dict:
        if limit not in (3, 5, 10):
            raise ValueError("limit must be 3, 5 or 10")
        rows = self.session.scalars(
            select(Incident).where(Incident.device_id == self.incident.device_id,
                                   Incident.id != self.incident.id,
                                   Incident.opened_at < self.incident.opened_at)
            .order_by(Incident.opened_at.desc()).limit(limit)).all()
        out = []
        for i in rows:
            d = self.session.scalars(select(Diagnosis).where(Diagnosis.incident_id == i.id)
                                     .order_by(Diagnosis.created_at.desc())).first()
            out.append({"code": i.code, "opened_at": i.opened_at, "status": i.status,
                        "severity": i.severity, "diagnosis": d.probable_cause if d else None})
        return {"previous_incidents": out}

    @staticmethod
    def _check_metric(metric: Any) -> None:
        if metric not in METRICS:
            raise ValueError(f"metric must be one of {METRICS}")
