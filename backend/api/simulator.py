"""Dev/demo only: inject named faults (disabled when ENV=production)."""

from datetime import datetime, timezone

import numpy as np
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.db.models import Device
from backend.deps import get_attack_features, get_detector, get_session, settings_dep
from backend.errors import AppError
from backend.schemas.incidents import DiagnosisOut
from backend.schemas.metrics import IncidentTouched, MetricIn
from backend.services import diagnosis_service, ingest_service
from simulator.replay import FAULT_ROWS, scenario_payloads
from simulator.scenarios import SCENARIOS

router = APIRouter(prefix="/simulator", tags=["simulator"])


class ScenarioInfo(BaseModel):
    name: str
    description: str
    expected_cause: str
    default_device: str


class ScenarioResult(BaseModel):
    scenario: str
    device: str
    expected_cause: str
    fault_rows: int
    flagged: int
    incident: IncidentTouched | None
    diagnosis: DiagnosisOut | None
    correct: bool | None


def _enabled(settings: Settings = Depends(settings_dep)) -> Settings:
    if settings.env == "production":
        raise AppError(403, "FORBIDDEN", "The simulator is disabled when ENV=production")
    return settings


@router.get("/scenarios", response_model=list[ScenarioInfo])
def list_scenarios(_: Settings = Depends(_enabled)):
    return [ScenarioInfo(name=s.name, description=s.description, expected_cause=s.expected_cause,
                         default_device=s.default_device) for s in SCENARIOS.values()]


@router.post("/scenarios/{name}", response_model=ScenarioResult)
def inject_scenario(
    name: str,
    device: str | None = Query(None, description="hostname; default is the scenario's device"),
    diagnose: bool = True,
    session: Session = Depends(get_session),
    detector=Depends(get_detector),
    settings: Settings = Depends(_enabled),
    attack: list[dict] = Depends(get_attack_features),
):
    """Write an hour of normal telemetry, then a 5-minute fault, then (optionally) diagnose."""
    if name not in SCENARIOS:
        raise AppError(404, "NOT_FOUND", f"Unknown scenario {name!r}; choose from {list(SCENARIOS)}")
    s = SCENARIOS[name]
    hostname = device or s.default_device
    dev = session.scalars(select(Device).where(Device.hostname == hostname)).first()
    if dev is None:
        raise AppError(404, "NOT_FOUND", f"Unknown device {hostname!r}")

    baseline, fault = scenario_payloads(
        name, {"id": dev.id, "link_capacity_mbps": dev.link_capacity_mbps}, attack,
        datetime.now(timezone.utc), np.random.default_rng())

    ingest_service.ingest(session, [MetricIn(**r) for r in baseline], detector, settings, detect=False)
    result = ingest_service.ingest(session, [MetricIn(**r) for r in fault], detector, settings)

    incident = result.incidents_touched[-1] if result.incidents_touched else None
    diag = None
    if diagnose and incident is not None:
        diag, _ = diagnosis_service.diagnose(session, incident.id, settings, force=True)
    return ScenarioResult(
        scenario=name, device=hostname, expected_cause=s.expected_cause,
        fault_rows=FAULT_ROWS, flagged=result.anomalies, incident=incident,
        diagnosis=diag, correct=None if diag is None else diag.probable_cause == s.expected_cause,
    )
