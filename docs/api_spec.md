# NetDiagnose AI — API Specification (v1)

Base URL (local): `http://localhost:8000`
Interactive docs: `/docs` (Swagger UI) and `/redoc`
Format: JSON, UTF-8. Timestamps are ISO-8601 in UTC (`2026-01-15T10:30:00Z`).

---

## Conventions

### Error body
```json
{ "error": { "code": "NOT_FOUND", "message": "Incident 42 does not exist" } }
```

| HTTP | `code` | Meaning |
|---|---|---|
| 400 | `BAD_REQUEST` | Malformed request |
| 404 | `NOT_FOUND` | Resource missing |
| 422 | `VALIDATION_ERROR` | Pydantic validation failure |
| 503 | `MODEL_UNAVAILABLE` | Model not loaded |
| 503 | `AGENT_UNAVAILABLE` | LLM provider failed (rules fallback returned where possible) |
| 500 | `INTERNAL_ERROR` | Unexpected failure |

### Pagination
List endpoints accept `limit` (default 50, max 500) and `offset` (default 0) and return:
```json
{ "items": [ ... ], "total": 1234, "limit": 50, "offset": 0 }
```

---

## Endpoints

### `GET /health`
Liveness and readiness.
```json
{ "status": "ok", "database": "up", "model_loaded": true, "model_version": "iforest-2026-01-10", "agent_enabled": true }
```

### `GET /devices`
```json
{ "items": [ { "id": 1, "hostname": "core-rtr-01", "device_type": "router", "ip_address": "10.0.0.1", "link_capacity_mbps": 1000 } ] }
```

### `GET /metrics`
Query parameters: `device_id`, `from`, `to`, `limit`, `offset`.
```json
{
  "items": [
    { "id": 981, "device_id": 1, "ts": "2026-01-15T10:30:00Z",
      "latency_ms": 245.0, "packet_loss_pct": 16.0, "jitter_ms": 42.0,
      "throughput_mbps": 940.0, "cpu_usage_pct": 91.0, "interface_utilization_pct": 94.0 } ],
  "total": 1, "limit": 50, "offset": 0
}
```

### `POST /metrics`
Ingest one or many rows. Each row is stored; if `detect=true` (default) it is also scored.

Request:
```json
{
  "detect": true,
  "rows": [
    { "device_id": 1, "ts": "2026-01-15T10:30:00Z",
      "latency_ms": 245, "packet_loss_pct": 16, "jitter_ms": 42,
      "throughput_mbps": 940, "cpu_usage_pct": 91, "interface_utilization_pct": 94,
      "features": { "sttl": 62, "proto": "tcp" } }
  ]
}
```
Response `201`:
```json
{ "inserted": 1, "anomalies": 1, "incidents_touched": [ { "id": 7, "code": "INC-0007", "created": true } ] }
```

### `POST /detect`
Score a record **without** storing it (stateless).

Request: a single metric object (same fields as above).
Response:
```json
{ "is_anomaly": true, "score": 0.82, "model_version": "iforest-2026-01-10" }
```

### `GET /incidents`
Filters: `status`, `severity`, `device_id`, `from`, `to`, `limit`, `offset`.
```json
{
  "items": [
    { "id": 7, "code": "INC-0007", "device_id": 1, "hostname": "core-rtr-01",
      "status": "open", "severity": "high", "peak_score": 0.91,
      "anomaly_count": 14, "opened_at": "2026-01-15T10:30:00Z",
      "last_seen_at": "2026-01-15T10:36:00Z", "top_cause": "Network congestion" } ],
  "total": 1, "limit": 50, "offset": 0
}
```

### `GET /incidents/{id}`
Returns the incident with evidence and all diagnoses.
```json
{
  "id": 7, "code": "INC-0007", "status": "open", "severity": "high",
  "device": { "id": 1, "hostname": "core-rtr-01" },
  "evidence": [
    { "id": 101, "metric_name": "packet_loss_pct", "observed_value": 16.0, "baseline_value": 0.4,
      "ratio": 40.0, "threshold": 10.0, "breached": true }
  ],
  "diagnoses": [
    { "id": 12, "source": "rules", "probable_cause": "Network congestion", "confidence": 0.8,
      "alternatives": [ { "cause": "Server overload", "confidence": 0.55 } ],
      "recommendations": [ "Identify top talkers on the affected interface" ],
      "cited_evidence": [101, 102, 104], "created_at": "2026-01-15T10:37:00Z" },
    { "id": 13, "source": "ai_agent", "probable_cause": "Probable network congestion",
      "confidence": 0.75, "report_markdown": "### Diagnosis ...", "cited_evidence": [101, 102, 104] }
  ]
}
```

### `POST /incidents/{id}/diagnose`
Runs evidence collection and the rule engine; optionally the AI agent.

Query/body: `use_agent` (bool, default `false`), `force` (bool, bypass cache).
Response `200`: the new diagnosis objects (same shape as above). If the agent fails validation or the provider errors, the response includes only the rules diagnosis and a header `X-Agent-Fallback: true`.

### `PATCH /incidents/{id}`
Update `status` (`investigating`, `resolved`, `closed`) or `summary`.

### `GET /stats/summary`
```json
{
  "devices": 8,
  "metrics_24h": 120344,
  "anomalies_24h": 931,
  "open_incidents": 3,
  "incidents_by_severity": { "low": 1, "medium": 1, "high": 1, "critical": 0 },
  "anomalies_per_hour": [ { "hour": "2026-01-15T09:00:00Z", "count": 42 } ]
}
```

### Simulator endpoints (dev/demo only)
| Method | Path | Purpose |
|---|---|---|
| POST | `/simulator/scenarios/{name}` | Inject `congestion`, `server_overload`, `link_flap`, `ddos` on a device |
| POST | `/simulator/replay` | Start/stop dataset replay (`rate_per_sec`) |

Disabled when `ENV=production`.

---

## Versioning
v1 has no URL prefix while local. Before any public release, move to `/api/v1/...` and keep `/health` unversioned.
