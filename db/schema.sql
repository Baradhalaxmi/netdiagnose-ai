-- NetDiagnose AI — PostgreSQL schema (v1)
-- Usage:
--   createdb netdiagnose
--   psql -d netdiagnose -f db/schema.sql

BEGIN;

-- ---------------------------------------------------------------
-- Devices
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS devices (
    id               SERIAL PRIMARY KEY,
    hostname         TEXT        NOT NULL UNIQUE,
    device_type      TEXT        NOT NULL
                     CHECK (device_type IN ('router','switch','server','firewall','host')),
    ip_address       INET,
    location         TEXT,
    link_capacity_mbps NUMERIC(10,2) NOT NULL DEFAULT 1000,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------
-- Network metrics (time series)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS network_metrics (
    id                        BIGSERIAL PRIMARY KEY,
    device_id                 INTEGER     NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    ts                        TIMESTAMPTZ NOT NULL,
    latency_ms                DOUBLE PRECISION,
    packet_loss_pct           DOUBLE PRECISION CHECK (packet_loss_pct BETWEEN 0 AND 100),
    jitter_ms                 DOUBLE PRECISION,
    throughput_mbps           DOUBLE PRECISION,
    cpu_usage_pct             DOUBLE PRECISION CHECK (cpu_usage_pct BETWEEN 0 AND 100),
    interface_utilization_pct DOUBLE PRECISION CHECK (interface_utilization_pct BETWEEN 0 AND 100),
    features                  JSONB,                       -- extra flow features used by the model
    source                    TEXT        NOT NULL DEFAULT 'dataset'
                              CHECK (source IN ('dataset','synthetic','live')),
    true_label                SMALLINT,                    -- 0 normal / 1 attack (replay/scenario only)
    scenario                  TEXT,                        -- injected fault name, if any
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_metrics_device_ts ON network_metrics (device_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_metrics_ts        ON network_metrics (ts DESC);

-- ---------------------------------------------------------------
-- Model registry
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS model_registry (
    id            SERIAL PRIMARY KEY,
    version       TEXT        NOT NULL UNIQUE,
    algorithm     TEXT        NOT NULL,
    threshold     DOUBLE PRECISION NOT NULL,
    metrics       JSONB,                                  -- precision, recall, f1, roc_auc
    trained_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_active     BOOLEAN     NOT NULL DEFAULT FALSE
);

-- ---------------------------------------------------------------
-- Anomalies (detector output)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS anomalies (
    id             BIGSERIAL PRIMARY KEY,
    metric_id      BIGINT      NOT NULL UNIQUE REFERENCES network_metrics(id) ON DELETE CASCADE,
    device_id      INTEGER     NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    detected_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    score          DOUBLE PRECISION NOT NULL CHECK (score BETWEEN 0 AND 1),
    is_anomaly     BOOLEAN     NOT NULL,
    model_version  TEXT        NOT NULL,
    incident_id    INTEGER                                   -- set when grouped (FK added below)
);
CREATE INDEX IF NOT EXISTS idx_anomalies_device_time ON anomalies (device_id, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_anomalies_flag        ON anomalies (is_anomaly) WHERE is_anomaly;

-- ---------------------------------------------------------------
-- Incidents
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS incidents (
    id           SERIAL PRIMARY KEY,
    code         TEXT        NOT NULL UNIQUE,                -- e.g. INC-0001 (generated in app)
    device_id    INTEGER     NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    status       TEXT        NOT NULL DEFAULT 'open'
                 CHECK (status IN ('open','investigating','resolved','closed')),
    severity     TEXT        NOT NULL DEFAULT 'medium'
                 CHECK (severity IN ('low','medium','high','critical')),
    peak_score   DOUBLE PRECISION,
    anomaly_count INTEGER    NOT NULL DEFAULT 0,
    opened_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at    TIMESTAMPTZ,
    summary      TEXT
);
CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents (status, opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_incidents_device ON incidents (device_id, opened_at DESC);

ALTER TABLE anomalies
    ADD CONSTRAINT fk_anomalies_incident
    FOREIGN KEY (incident_id) REFERENCES incidents(id) ON DELETE SET NULL;

-- ---------------------------------------------------------------
-- Evidence (observed vs baseline, cited by diagnoses)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS evidence (
    id              BIGSERIAL PRIMARY KEY,
    incident_id     INTEGER     NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    metric_name     TEXT        NOT NULL,
    observed_value  DOUBLE PRECISION NOT NULL,
    baseline_value  DOUBLE PRECISION,
    ratio           DOUBLE PRECISION,
    threshold       DOUBLE PRECISION,
    breached        BOOLEAN     NOT NULL DEFAULT FALSE,
    note            TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_evidence_incident ON evidence (incident_id);

-- ---------------------------------------------------------------
-- Diagnoses (rule-based or AI)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS diagnoses (
    id               BIGSERIAL PRIMARY KEY,
    incident_id      INTEGER     NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    source           TEXT        NOT NULL CHECK (source IN ('rules','ai_agent')),
    probable_cause   TEXT        NOT NULL,
    confidence       DOUBLE PRECISION CHECK (confidence BETWEEN 0 AND 1),
    alternatives     JSONB       NOT NULL DEFAULT '[]',
    recommendations  JSONB       NOT NULL DEFAULT '[]',
    cited_evidence   JSONB       NOT NULL DEFAULT '[]',      -- list of evidence ids
    report_markdown  TEXT,
    llm_provider     TEXT,
    llm_model        TEXT,
    tokens_used      INTEGER,
    latency_ms       INTEGER,
    evidence_hash    TEXT,                                    -- cache key with incident_id
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_diag_incident ON diagnoses (incident_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_diag_cache
    ON diagnoses (incident_id, source, evidence_hash) WHERE evidence_hash IS NOT NULL;

COMMIT;
