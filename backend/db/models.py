"""SQLAlchemy ORM models. The source of truth for the tables is db/schema.sql;
these classes mirror it so Python code can query with types.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, ForeignKey, Numeric, SmallInteger, func
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    hostname: Mapped[str] = mapped_column(unique=True)
    device_type: Mapped[str]
    ip_address: Mapped[str | None] = mapped_column(INET)
    location: Mapped[str | None]
    link_capacity_mbps: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=1000)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    metrics: Mapped[list[NetworkMetric]] = relationship(back_populates="device")
    incidents: Mapped[list[Incident]] = relationship(back_populates="device")


class NetworkMetric(Base):
    __tablename__ = "network_metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    ts: Mapped[datetime]
    latency_ms: Mapped[float | None]
    packet_loss_pct: Mapped[float | None]
    jitter_ms: Mapped[float | None]
    throughput_mbps: Mapped[float | None]
    cpu_usage_pct: Mapped[float | None]
    interface_utilization_pct: Mapped[float | None]
    features: Mapped[dict | None] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(default="dataset")
    true_label: Mapped[int | None] = mapped_column(SmallInteger)
    scenario: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    device: Mapped[Device] = relationship(back_populates="metrics")
    anomaly: Mapped[Anomaly | None] = relationship(back_populates="metric")


class ModelRegistry(Base):
    __tablename__ = "model_registry"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(unique=True)
    algorithm: Mapped[str]
    threshold: Mapped[float]
    metrics: Mapped[dict | None] = mapped_column(JSONB)
    trained_at: Mapped[datetime] = mapped_column(server_default=func.now())
    is_active: Mapped[bool] = mapped_column(default=False)


class Anomaly(Base):
    """Detector output for one metric row (stored for every scored row;
    ``is_anomaly`` says whether it crossed the threshold)."""

    __tablename__ = "anomalies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    metric_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("network_metrics.id", ondelete="CASCADE"), unique=True
    )
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    detected_at: Mapped[datetime] = mapped_column(server_default=func.now())
    score: Mapped[float]
    is_anomaly: Mapped[bool]
    model_version: Mapped[str]
    incident_id: Mapped[int | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="SET NULL")
    )

    metric: Mapped[NetworkMetric] = relationship(back_populates="anomaly")
    incident: Mapped[Incident | None] = relationship(back_populates="anomalies")


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(unique=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(default="open")
    severity: Mapped[str] = mapped_column(default="medium")
    peak_score: Mapped[float | None]
    anomaly_count: Mapped[int] = mapped_column(default=0)
    opened_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(server_default=func.now())
    closed_at: Mapped[datetime | None]
    summary: Mapped[str | None]

    device: Mapped[Device] = relationship(back_populates="incidents")
    anomalies: Mapped[list[Anomaly]] = relationship(back_populates="incident")
    evidence: Mapped[list[Evidence]] = relationship(back_populates="incident")
    diagnoses: Mapped[list[Diagnosis]] = relationship(back_populates="incident")


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"))
    metric_name: Mapped[str]
    observed_value: Mapped[float]
    baseline_value: Mapped[float | None]
    ratio: Mapped[float | None]
    threshold: Mapped[float | None]
    breached: Mapped[bool] = mapped_column(default=False)
    note: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    incident: Mapped[Incident] = relationship(back_populates="evidence")


class Diagnosis(Base):
    __tablename__ = "diagnoses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"))
    source: Mapped[str]
    probable_cause: Mapped[str]
    confidence: Mapped[float | None]
    alternatives: Mapped[list] = mapped_column(JSONB, default=list)
    recommendations: Mapped[list] = mapped_column(JSONB, default=list)
    cited_evidence: Mapped[list] = mapped_column(JSONB, default=list)
    report_markdown: Mapped[str | None]
    llm_provider: Mapped[str | None]
    llm_model: Mapped[str | None]
    tokens_used: Mapped[int | None]
    latency_ms: Mapped[int | None]
    evidence_hash: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    incident: Mapped[Incident] = relationship(back_populates="diagnoses")
