from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator

from backend.schemas.common import ORMModel

# A real DoS flow from the UNSW-NB15 testing set; the detector flags it.
# Used as the /docs example so the endpoints can be tried in one click.
EXAMPLE_FEATURES: dict[str, Any] = {
    "dur": 0.921987, "proto": "ospf", "service": "-", "state": "INT", "spkts": 20,
    "dpkts": 0, "sbytes": 1280, "dbytes": 0, "rate": 20.607666, "sttl": 254, "dttl": 0,
    "sload": 10551.125, "dload": 0.0, "sloss": 0, "dloss": 0, "sinpkt": 48.525633,
    "dinpkt": 0.0, "sjit": 52.253805, "djit": 0.0, "swin": 0, "stcpb": 0, "dtcpb": 0,
    "dwin": 0, "tcprtt": 0.0, "synack": 0.0, "ackdat": 0.0, "smean": 64, "dmean": 0,
    "trans_depth": 0, "response_body_len": 0, "ct_srv_src": 1, "ct_state_ttl": 2,
    "ct_dst_ltm": 1, "ct_src_dport_ltm": 1, "ct_dst_sport_ltm": 1, "ct_dst_src_ltm": 2,
    "is_ftp_login": 0, "ct_ftp_cmd": 0, "ct_flw_http_mthd": 0, "ct_src_ltm": 1,
    "ct_srv_dst": 1, "is_sm_ips_ports": 0,
}


class DeviceOut(ORMModel):
    id: int
    hostname: str
    device_type: str
    ip_address: str | None
    location: str | None
    link_capacity_mbps: Decimal


class MetricIn(BaseModel):
    """One measurement. Network metrics left out are derived from ``features``
    (latency from tcprtt, loss from sloss/dloss, ...)."""

    device_id: int
    ts: datetime | None = Field(None, description="UTC; defaults to now")
    latency_ms: float | None = Field(None, ge=0)
    packet_loss_pct: float | None = Field(None, ge=0, le=100)
    jitter_ms: float | None = Field(None, ge=0)
    throughput_mbps: float | None = Field(None, ge=0)
    cpu_usage_pct: float | None = Field(None, ge=0, le=100)
    interface_utilization_pct: float | None = Field(None, ge=0, le=100)
    features: dict[str, Any] | None = Field(
        None, description="UNSW-NB15 flow fields; required for anomaly detection"
    )
    source: str = Field("live", pattern="^(dataset|synthetic|live)$")
    scenario: str | None = Field(None, max_length=50, description="injected fault name (ground truth)")

    @field_validator("ts")
    @classmethod
    def _assume_utc(cls, v: datetime | None) -> datetime | None:
        """Timestamps without a timezone are taken as UTC."""
        return v.replace(tzinfo=timezone.utc) if v is not None and v.tzinfo is None else v


class MetricsIn(BaseModel):
    detect: bool = True
    rows: list[MetricIn] = Field(min_length=1, max_length=5000)

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "detect": True,
                    "rows": [
                        {"device_id": 1, "cpu_usage_pct": 91,
                         "interface_utilization_pct": 94, "features": EXAMPLE_FEATURES}
                    ],
                }
            ]
        }
    }


class MetricOut(ORMModel):
    id: int
    device_id: int
    ts: datetime
    latency_ms: float | None
    packet_loss_pct: float | None
    jitter_ms: float | None
    throughput_mbps: float | None
    cpu_usage_pct: float | None
    interface_utilization_pct: float | None
    source: str


class IncidentTouched(BaseModel):
    id: int
    code: str
    created: bool


class MetricsIngestResult(BaseModel):
    inserted: int
    anomalies: int
    incidents_touched: list[IncidentTouched]


class DetectIn(BaseModel):
    features: dict[str, Any]

    model_config = {"json_schema_extra": {"examples": [{"features": EXAMPLE_FEATURES}]}}


class DetectOut(BaseModel):
    is_anomaly: bool
    score: float
    model_version: str
