from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from backend.schemas.common import ORMModel

Status = Literal["open", "investigating", "resolved", "closed"]
Severity = Literal["low", "medium", "high", "critical"]


class IncidentSummary(BaseModel):
    id: int
    code: str
    device_id: int
    hostname: str
    status: Status
    severity: Severity
    peak_score: float | None
    anomaly_count: int
    opened_at: datetime
    last_seen_at: datetime
    closed_at: datetime | None
    top_cause: str | None = None


class DeviceRef(BaseModel):
    id: int
    hostname: str


class EvidenceOut(ORMModel):
    id: int
    metric_name: str
    observed_value: float
    baseline_value: float | None
    ratio: float | None
    threshold: float | None
    breached: bool
    note: str | None


class DiagnosisOut(ORMModel):
    id: int
    source: str
    probable_cause: str
    confidence: float | None
    alternatives: list
    recommendations: list
    cited_evidence: list
    report_markdown: str | None
    created_at: datetime


class IncidentDetail(BaseModel):
    id: int
    code: str
    status: Status
    severity: Severity
    peak_score: float | None
    anomaly_count: int
    opened_at: datetime
    last_seen_at: datetime
    closed_at: datetime | None
    summary: str | None
    device: DeviceRef
    evidence: list[EvidenceOut]
    diagnoses: list[DiagnosisOut]


class IncidentPatch(BaseModel):
    status: Literal["investigating", "resolved", "closed"] | None = None
    summary: str | None = None
