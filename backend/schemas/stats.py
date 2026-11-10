from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class HourCount(BaseModel):
    hour: datetime
    count: int


class StatsSummary(BaseModel):
    devices: int
    metrics_24h: int
    anomalies_24h: int
    open_incidents: int
    incidents_by_severity: dict[str, int]
    anomalies_per_hour: list[HourCount]


class Health(BaseModel):
    status: str
    database: str
    model_loaded: bool
    model_version: str | None
    agent_enabled: bool
