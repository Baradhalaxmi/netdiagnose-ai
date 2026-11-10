# NetDiagnose AI — Architecture

> An evidence-based network diagnosis system: ML detects anomalies, a rule engine collects evidence and proposes causes, and an AI agent turns that evidence into a readable diagnosis report.

**Status:** Design v1.0 (single-project, monolith-first)
**Audience:** You (builder), reviewers, recruiters reading the GitHub repo

---

## 1. Purpose and scope

### 1.1 Problem
Network engineers get alerts such as "latency is high" but still have to work out *why*. NetDiagnose AI automates the first-level triage:

1. Detect that something abnormal is happening.
2. Gather the evidence around it (which metrics moved, by how much, compared to baseline).
3. Propose probable causes using explicit network-engineering rules.
4. Produce a human-readable diagnosis with evidence, alternatives and next investigation steps.

### 1.2 In scope (v1)
- Anomaly detection on network flow/metric data (Isolation Forest).
- PostgreSQL storage for devices, metrics, anomalies, incidents, evidence, diagnoses.
- FastAPI backend exposing the system through a REST API.
- Streamlit dashboard.
- Rule-based diagnosis engine.
- AI agent that reasons over evidence using read-only tools.
- Docker Compose packaging (last step).

### 1.3 Out of scope (v1)
React, Kafka, Redis, Kubernetes, microservices, real device polling (SNMP/NetFlow), authentication/multi-tenancy, real-time streaming. These are listed in the evolution path (section 17).

---

## 2. Analysis of the source plan

The source plan is sound. Its strongest decisions are kept; several gaps are fixed here.

### 2.1 What the plan gets right
| Decision | Why it is good |
|---|---|
| One Python project first, split later | Avoids integration overhead before anything works |
| ML first, AI agent last | You understand each layer before depending on it |
| Rule engine **before** the LLM | Gives deterministic, explainable reasoning the LLM can be checked against |
| Streamlit → FastAPI → PostgreSQL | Demonstrates real backend architecture, not a notebook |
| Docker last | Containers add value only once there are multiple parts |
| Learn-as-needed sequencing | Each technology has an immediate reason to exist |

### 2.2 Gaps found and how this design resolves them

| # | Gap in the source plan | Impact | Resolution in this design |
|---|---|---|---|
| G1 | **Dataset/metric mismatch.** UNSW-NB15 is an *intrusion-detection flow dataset*. It has no `cpu_usage` and no `interface_utilization`, and it does not contain `latency`/`packet_loss`/`jitter` under those names. | The metric table and rules in the plan cannot be fed from the dataset directly | A **feature-mapping layer** (section 7.2) derives metrics from flow fields (`tcprtt`, `sjit/djit`, `sloss/dloss`, `sload/dload`) and a **synthetic telemetry generator** supplies CPU and interface utilization |
| G2 | **Dataset file naming.** The plan uses `UNSW_NB15.csv`. The official release ships as four raw CSVs (`UNSW-NB15_1..4.csv`, no header) plus pre-split `UNSW_NB15_training-set.csv` / `UNSW_NB15_testing-set.csv` (with header and labels). | Reading the wrong file wastes a day | Use the **training/testing set** files with headers (section 7.1) |
| G3 | **Isolation Forest is unsupervised but the dataset is labelled.** The plan does not say how to use labels. | Evaluation will be meaningless if misused | Train on **normal flows only**, evaluate with labels (precision, recall, F1, ROC-AUC) and compare to a supervised baseline (section 8) |
| G4 | **Score semantics.** `predict()` returns `1/-1`; the example shows `score: 0.82`. Raw `decision_function` is not a 0–1 probability. | Misleading UI and thresholds | Define a **normalised anomaly score 0–1** from the training distribution (section 8.4) |
| G5 | **Model persistence with pickle.** `model.pkl` is fragile across versions and unsafe to load from untrusted sources. | Reproducibility and security | Use `joblib` plus a `model_meta.json` recording sklearn version, features, threshold, metrics |
| G6 | **LLM agent has no grounding or failure policy.** | Hallucinated causes, outages when API is down | Structured output validation, evidence-ID citation requirement, rule-engine fallback report (section 10) |
| G7 | **No testing, config or observability plan.** | Hard to call it "portfolio-ready" | Testing pyramid (section 14), `.env` config, structured logging (section 15) |
| G8 | **Database schema is only a list of names.** | Ambiguity when you reach Week 4 | Full schema in `db/schema.sql` |
| G9 | **No incident lifecycle.** Anomaly → incident grouping is not described. | Dashboard would show thousands of raw anomalies | Incident correlation window (section 9.1) |

---

## 3. Architecture principles

1. **Monolith first, services later.** One repository, one Python environment, clear internal module boundaries.
2. **Each layer is independently runnable and testable.** `ml/` works with no database. The rule engine works with no LLM. The backend works with no dashboard.
3. **Evidence over eloquence.** Every statement in a diagnosis must trace to a stored evidence row.
4. **Deterministic core, probabilistic edge.** ML and rules produce the facts; the LLM only explains and prioritises them.
5. **Fail soft.** If the LLM is unavailable, the user still gets a rule-based report.
6. **Configuration, not constants.** Thresholds and rules live in YAML, not buried in code.
7. **Reproducibility.** Pinned dependencies, seeded training, versioned model artifacts.

---

## 4. System context

```text
                      ┌──────────────────┐
   Network engineer ─▶│  Streamlit UI    │
                      └────────┬─────────┘
                               │ HTTP/JSON
                      ┌────────▼─────────┐        ┌────────────────┐
                      │   FastAPI        │───────▶│  LLM provider  │
                      │   Backend        │  (opt) │  (external API)│
                      └─┬──────┬───────┬─┘        └────────────────┘
                        │      │       │
              ┌─────────▼┐  ┌──▼────┐ ┌▼──────────────┐
              │PostgreSQL│  │  ML   │ │ Diagnosis     │
              │          │  │ Model │ │ Engine (rules)│
              └──────────┘  └───────┘ └───────────────┘

   Data sources (v1): UNSW-NB15 dataset  +  synthetic telemetry generator
   Data sources (future): SNMP, NetFlow/IPFIX, syslog, Prometheus
```

---

## 5. Component architecture

### 5.1 Layered view

```text
┌───────────────────────────────────────────────────────────────┐
│ PRESENTATION      dashboard/            (Streamlit)           │
├───────────────────────────────────────────────────────────────┤
│ API               backend/api/          (FastAPI routers)     │
├───────────────────────────────────────────────────────────────┤
│ SERVICES          backend/services/     (orchestration)       │
│    detection_service  incident_service  diagnosis_service     │
├───────────────────────────────────────────────────────────────┤
│ DOMAIN            ml/   diagnosis/   agent/                   │
├───────────────────────────────────────────────────────────────┤
│ PERSISTENCE       backend/db/           (SQLAlchemy models)   │
├───────────────────────────────────────────────────────────────┤
│ INFRASTRUCTURE    PostgreSQL · Docker Compose · .env          │
└───────────────────────────────────────────────────────────────┘
```

Rule: **dependencies point downward only.** `ml/` and `diagnosis/` never import from `backend/` or `dashboard/`.

### 5.2 Component responsibilities

| Component | Responsibility | Inputs | Outputs | Depends on |
|---|---|---|---|---|
| `ml/` | Train, persist and run the anomaly detector | Feature DataFrame / dict | `{is_anomaly, score}` | scikit-learn |
| `diagnosis/` | Build evidence, evaluate rules, rank causes | Metric snapshot + baseline | Evidence list + possible causes | PyYAML |
| `agent/` | Reason over evidence, call read-only tools, write the report | Incident context | Structured diagnosis (JSON + markdown) | LLM provider, `diagnosis/` |
| `backend/db/` | ORM models, sessions, migrations | — | Persistence | SQLAlchemy, PostgreSQL |
| `backend/services/` | Orchestrate detect → incident → diagnose | API requests | Domain results | all domain modules |
| `backend/api/` | HTTP contract, validation, errors | HTTP | JSON | Pydantic, FastAPI |
| `dashboard/` | Visualisation and interaction | API | Browser UI | `requests`, Streamlit |

### 5.3 Why Streamlit talks to FastAPI (not directly to the DB)
Streamlit *can* query PostgreSQL directly, but routing through FastAPI gives you a single business-logic layer, an auto-generated `/docs` page, and a clean demonstration of API design. Read-only chart queries may go direct later if latency matters.

---

## 6. Runtime data flow

### 6.1 Detection → incident → diagnosis

```text
 1  Metrics arrive           POST /metrics            (or batch loader)
 2  Stored                   network_metrics
 3  Detector scores row      ml.predict.detect_anomaly()
 4  Anomaly stored           anomalies
 5  Correlator groups        anomalies on same device within N minutes → one incident
 6  Evidence collected       diagnosis.evidence.collect()  → evidence rows
 7  Rules evaluated          diagnosis.rules.evaluate()    → candidate causes + confidence
 8  Agent invoked (opt)      agent.run(incident_id)        → tool calls → structured diagnosis
 9  Diagnosis stored         diagnoses (source = rules | ai_agent)
10  UI displays              GET /incidents/{id}
```

### 6.2 Sequence diagram

```text
Dashboard      FastAPI        ML         Rules       Agent        DB
   │  POST /detect │           │           │           │          │
   │──────────────▶│  score()  │           │           │          │
   │               │──────────▶│           │           │          │
   │               │◀──────────│           │           │          │
   │               │ save anomaly / open incident ─────────────────▶│
   │               │ collect evidence + evaluate ─────▶│           │
   │               │◀─────────────────────────────────│           │
   │               │ run(incident) ───────────────────────────────▶│
   │               │                         tools: get_metrics…───▶│
   │               │◀──────────── structured diagnosis ────────────│
   │               │ save diagnosis ───────────────────────────────▶│
   │◀──────────────│ incident + diagnosis                          │
```

---

## 7. Data architecture

### 7.1 Primary dataset
**UNSW-NB15** (Australian Centre for Cyber Security). Use:
- `UNSW_NB15_training-set.csv` (~175k rows)
- `UNSW_NB15_testing-set.csv` (~82k rows)

Key columns: `dur, proto, service, state, spkts, dpkts, sbytes, dbytes, rate, sttl, dttl, sload, dload, sloss, dloss, sinpkt, dinpkt, sjit, djit, tcprtt, synack, ackdat, ct_*` plus `attack_cat` (category) and `label` (0 = normal, 1 = attack).

> Verify column names and units against the dataset's feature description file when you load it (Week 1). The mapping below is the intended design; adjust if a unit differs.

### 7.2 Metric mapping (dataset → NetDiagnose metrics)

| NetDiagnose metric | Derived from | Formula (intended) | Notes |
|---|---|---|---|
| `latency_ms` | `tcprtt` | `tcprtt * 1000` | `tcprtt` is 0 for non-TCP flows; fall back to `synack + ackdat` or median-impute |
| `jitter_ms` | `sjit`, `djit` | `(sjit + djit) / 2` | Already in ms in UNSW-NB15 |
| `packet_loss_pct` | `sloss`, `dloss`, `spkts`, `dpkts` | `(sloss + dloss) / (spkts + dpkts) * 100` | Guard divide-by-zero |
| `throughput_mbps` | `sload`, `dload` | `(sload + dload) / 1e6` | Loads are bits/second |
| `cpu_usage_pct` | — | **Synthetic**, correlated with throughput and attack label | Generated, documented as synthetic |
| `interface_utilization_pct` | throughput ÷ assumed link capacity | `min(100, throughput_mbps / capacity_mbps * 100)` | Capacity configurable per device |

### 7.3 Two data modes
| Mode | Purpose | Source |
|---|---|---|
| **Replay** | Evaluate ML and demo the system | Rows from the dataset streamed into `/metrics` at a configurable rate |
| **Scenario** | Show diagnosis logic clearly | Synthetic generator injects named faults (congestion, server overload, link flap, DDoS) with known ground truth |

Scenario mode is what makes the diagnosis engine demonstrable: you know the true cause and can measure whether the system finds it.

### 7.4 Entity-relationship model

```text
 devices 1 ────< network_metrics 1 ──── 0..1 anomalies
    │
    └────< incidents 1 ────< evidence
                   │
                   └────< diagnoses
```

| Table | Purpose |
|---|---|
| `devices` | Monitored hosts, routers, servers |
| `network_metrics` | Time-series measurements per device |
| `anomalies` | Detector output per metric row (score, model version) |
| `incidents` | Grouped anomalies with status and severity |
| `evidence` | Observed vs baseline values attached to an incident |
| `diagnoses` | Rule-based or AI-generated conclusions |
| `model_registry` | Trained model versions and metrics |

Full DDL: `db/schema.sql`.

---

## 8. ML design

### 8.1 Pipeline
```text
raw CSV → clean → map to metrics + keep selected flow features
        → scale (StandardScaler) → IsolationForest → raw score
        → normalise → {is_anomaly, score}
```

### 8.2 Training protocol
1. Split with `train_test_split` (stratified by `label`, fixed `random_state`).
2. **Fit on normal rows only** (`label == 0`) so "anomaly" means "unlike normal".
3. Evaluate on a held-out set containing both classes.
4. Tune `contamination`, `n_estimators`, `max_samples` against validation F1/ROC-AUC.

### 8.3 Evaluation
| Metric | Why |
|---|---|
| Precision / Recall / F1 | Balance false alarms vs missed attacks |
| ROC-AUC (using `decision_function`) | Threshold-independent quality |
| Confusion matrix | Visible in README |
| Baseline comparison | Random Forest on labels — shows how much supervision would buy |

### 8.4 Score normalisation
```text
raw = -model.decision_function(X)           # higher = more anomalous
score = clip((raw - raw_min) / (raw_max - raw_min), 0, 1)
is_anomaly = score >= threshold             # threshold chosen on validation set
```
`raw_min`, `raw_max`, `threshold` are saved in `model_meta.json`.

### 8.5 Artifacts
```text
ml/
├── train.py          # CLI: python -m ml.train
├── predict.py        # detect_anomaly(record) -> dict
├── features.py       # mapping + feature selection (shared by train & predict)
├── model.joblib
└── model_meta.json   # sklearn version, feature list, threshold, metrics, trained_at
```
`features.py` is shared by training and inference to prevent **train/serve skew**.

### 8.6 Public contract
```python
detect_anomaly(record: dict) -> {
    "is_anomaly": bool,
    "score": float,          # 0..1
    "model_version": str
}
```

---

## 9. Diagnosis engine (rules)

### 9.1 Incident correlation
Anomalies on the same device within `INCIDENT_WINDOW_MINUTES` (default 5) join one open incident. An incident closes after `INCIDENT_QUIET_MINUTES` (default 10) with no new anomalies. Severity is derived from peak anomaly score and number of triggered rules.

### 9.2 Evidence collection
For each metric, compare the incident-window mean/peak with the device's **baseline** (median and MAD over the previous N hours of non-anomalous data):

```json
{
  "metric": "packet_loss_pct",
  "observed": 16.0,
  "baseline": 0.4,
  "ratio": 40.0,
  "threshold": 10.0,
  "breached": true
}
```
Each becomes an `evidence` row with an ID the agent must cite.

### 9.3 Rule set (v1)
Thresholds live in `diagnosis/rules.yaml`. Defaults:

| Rule ID | Probable cause | Condition | Base confidence |
|---|---|---|---|
| `R-CONGESTION` | Network congestion | `packet_loss > 10` AND `latency > 200` AND `interface_util > 85` | 0.8 |
| `R-CONGESTION-WEAK` | Possible congestion | `packet_loss > 5` AND `interface_util > 85` | 0.5 |
| `R-SERVER-OVERLOAD` | Server overload | `cpu_usage > 90` AND `latency > 200` | 0.8 |
| `R-LINK-FAULT` | Link failure / flapping | `packet_loss > 30` | 0.7 |
| `R-JITTER-QOS` | Jitter / QoS problem | `jitter > 30` AND `packet_loss < 5` | 0.6 |
| `R-TRAFFIC-FLOOD` | Possible DoS / traffic flood | `throughput > 3 × baseline` AND `interface_util > 90` | 0.7 |

Rule format:
```yaml
- id: R-CONGESTION
  cause: Network congestion
  all:
    - {metric: packet_loss_pct, op: ">", value: 10}
    - {metric: latency_ms, op: ">", value: 200}
    - {metric: interface_utilization_pct, op: ">", value: 85}
  confidence: 0.8
  investigate:
    - Identify top talkers on the affected interface
    - Check QoS policy and queue drops
    - Compare with the same hour on previous days
```

### 9.4 Ranking
Candidates are sorted by `base_confidence × (matched_conditions / total_conditions)`. Overlapping causes (e.g. congestion and overload) are both reported — the second becomes the "alternative possibility".

### 9.5 Output contract
```json
{
  "possible_causes": [
    {"rule_id": "R-CONGESTION", "cause": "Network congestion", "confidence": 0.8,
     "evidence_ids": [101, 102, 104]},
    {"rule_id": "R-SERVER-OVERLOAD", "cause": "Server overload", "confidence": 0.55,
     "evidence_ids": [103, 102]}
  ]
}
```

---

## 10. AI agent design

### 10.1 Role
The agent **explains, reconciles and recommends**. It does not invent facts: it may only state what tools return.

### 10.2 Tools (read-only)
| Tool | Returns |
|---|---|
| `get_incident(incident_id)` | Incident header, status, severity |
| `get_metrics(device_id, from, to)` | Time-series slice |
| `compare_baseline(device_id, metric, window)` | Observed vs baseline stats |
| `get_device_status(device_id)` | Device type, role, last seen |
| `get_previous_incidents(device_id, limit)` | Similar past incidents and their diagnoses |
| `get_rule_findings(incident_id)` | Output of the rule engine (section 9.5) |
| `get_logs(device_id, from, to)` | Optional; stubbed in v1 with generated log lines |

All tools are read-only. The agent cannot change the database or devices.

### 10.3 Control loop
```text
1. Receive incident_id
2. Call get_rule_findings and get_incident
3. Decide which extra tools are needed (max 6 tool calls)
4. Produce structured diagnosis
5. Validate with Pydantic (schema + evidence IDs exist)
6. On validation failure: one retry, then fall back to rules-only report
```

### 10.4 Output schema
```json
{
  "diagnosis": "Probable network congestion",
  "confidence": "medium-high",
  "evidence": [
    {"statement": "Packet loss rose to 16%", "evidence_id": 101},
    {"statement": "Latency rose to 245 ms", "evidence_id": 102}
  ],
  "alternatives": [
    {"cause": "Server overload", "why_less_likely": "CPU at 91% but interface utilisation is also 94%"}
  ],
  "recommended_investigation": [
    "Check traffic sources on the saturated interface"
  ]
}
```

### 10.5 Guardrails
| Risk | Control |
|---|---|
| Hallucinated causes | Every evidence statement needs a valid `evidence_id`; unknown IDs reject the output |
| Overconfidence | Confidence label must not exceed the highest rule confidence by more than one band |
| Tool abuse / loops | Hard cap on tool calls and total tokens |
| Prompt injection from logs/metrics | Tool outputs are passed as data in a delimited block; system prompt states they are untrusted |
| Provider outage | Fallback to rule-based template report; `diagnoses.source = 'rules'` |
| Cost | Cache by `(incident_id, evidence_hash)`; diagnose only on demand or severity ≥ medium |
| Privacy | Send aggregated metrics only — no raw IPs unless explicitly enabled |

### 10.6 Provider abstraction
`agent/llm_client.py` exposes `generate(messages, tools) -> response`. Swapping providers touches one file. Provider name and model come from `.env`.

### 10.7 Report rendering
The structured JSON is rendered to markdown by a template (not by the LLM) so format is always consistent, then stored in `diagnoses.report_markdown`.

---

## 11. API design

Full reference in `docs/api_spec.md`. Summary:

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness |
| GET | `/devices` | List devices |
| GET | `/metrics` | Query metrics (device, time range, limit) |
| POST | `/metrics` | Ingest metric rows |
| POST | `/detect` | Score a metric record |
| GET | `/incidents` | List incidents (filter by status/severity) |
| GET | `/incidents/{id}` | Incident with evidence and diagnoses |
| POST | `/incidents/{id}/diagnose` | Run rules (+ agent if `use_agent=true`) |
| GET | `/stats/summary` | Dashboard KPIs |

Conventions: JSON only, ISO-8601 UTC timestamps, Pydantic request/response models, consistent error body `{ "error": { "code": "...", "message": "..." } }`, `/docs` and `/redoc` enabled.

---

## 12. Repository structure

Start small (Week 0), grow into this:

```text
netdiagnose-ai/
├── data/
│   ├── raw/                      # UNSW-NB15 CSVs (git-ignored)
│   └── processed/                # cleaned parquet/CSV (git-ignored)
├── notebooks/
│   ├── 01_exploration.ipynb
│   └── 02_modeling.ipynb
├── ml/
│   ├── features.py
│   ├── train.py
│   ├── predict.py
│   ├── model.joblib
│   └── model_meta.json
├── diagnosis/
│   ├── evidence.py
│   ├── rules.py
│   └── rules.yaml
├── agent/
│   ├── llm_client.py
│   ├── tools.py
│   ├── prompts.py
│   ├── schemas.py
│   └── runner.py
├── backend/
│   ├── main.py
│   ├── config.py
│   ├── api/                      # routers: metrics, incidents, detect, stats
│   ├── services/
│   ├── db/                       # models.py, session.py, crud.py
│   └── schemas/                  # Pydantic models
├── dashboard/
│   ├── app.py
│   └── pages/
├── simulator/
│   ├── replay.py                 # stream dataset rows into the API
│   └── scenarios.py              # synthetic faults with ground truth
├── db/
│   └── schema.sql
├── tests/
│   ├── test_features.py
│   ├── test_predict.py
│   ├── test_rules.py
│   ├── test_api.py
│   └── test_agent_validation.py
├── docker/
│   ├── Dockerfile.backend
│   └── Dockerfile.dashboard
├── docs/
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

---

## 13. Configuration

All via environment variables (`.env`, loaded by `backend/config.py`):

| Variable | Example | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg2://netdiag:netdiag@localhost:5432/netdiagnose` | DB connection |
| `API_BASE_URL` | `http://localhost:8000` | Used by Streamlit |
| `MODEL_PATH` | `ml/model.joblib` | Model artifact |
| `ANOMALY_THRESHOLD` | `0.6` | Overrides value in meta |
| `INCIDENT_WINDOW_MINUTES` | `5` | Correlation window |
| `LLM_PROVIDER` | `anthropic` | Agent backend |
| `LLM_MODEL` | *(set in .env)* | Model name |
| `LLM_API_KEY` | *(secret)* | Never committed |
| `AGENT_ENABLED` | `true` | Feature flag |
| `AGENT_MAX_TOOL_CALLS` | `6` | Loop cap |

---

## 14. Testing strategy

| Level | Scope | Tooling | Example |
|---|---|---|---|
| Unit | Feature mapping, rule evaluation, schema validation | `pytest` | Packet-loss formula handles zero packets |
| Model | Metrics above a floor on held-out data | `pytest` + saved metrics | F1 ≥ agreed minimum |
| Integration | API ↔ DB | FastAPI `TestClient`, test database | POST metric → anomaly row exists |
| Scenario | End-to-end on injected faults | Scenario generator | "congestion" scenario yields `R-CONGESTION` top-ranked |
| Agent | Output validation, fallback path | Mocked LLM client | Invalid evidence ID → fallback report |

Target: **rule engine and feature mapping ≥ 90% line coverage**; overall ≥ 70%. Run in GitHub Actions on every push.

---

## 15. Deployment, security and observability

### 15.1 Local development
Run PostgreSQL, FastAPI (`uvicorn backend.main:app --reload`) and Streamlit directly on your machine.

### 15.2 Docker Compose (final step)
```text
┌─────────────────────── docker compose ───────────────────────┐
│  dashboard (Streamlit)  :8501  ──▶ backend                   │
│  backend   (FastAPI)    :8000  ──▶ db                        │
│  db        (PostgreSQL) :5432   volume: pgdata               │
└──────────────────────────────────────────────────────────────┘
```
Healthchecks make `backend` wait for `db`. One command: `docker compose up --build`.

### 15.3 Security baseline
- Secrets only in `.env` (git-ignored); `.env.example` committed.
- SQLAlchemy parameterised queries (no string-built SQL).
- Pydantic validation on every input.
- CORS restricted to the dashboard origin.
- No pickle loading of untrusted files; `joblib` artifacts are local and versioned.
- Agent tools are read-only; tool outputs treated as untrusted text.
- *Future:* API key or OAuth, rate limiting, role-based access.

### 15.4 Observability
- Python `logging` with JSON formatter; correlation ID per request.
- Log: model version, anomaly score, rule IDs fired, agent tool calls, token usage, latency.
- `/health` returns DB connectivity and model-loaded status.

---

## 16. Architecture decision records (ADRs)

| ID | Decision | Alternatives | Rationale |
|---|---|---|---|
| ADR-1 | Monolith-first repo | Microservices | Lower complexity; easy to split later along existing module boundaries |
| ADR-2 | Isolation Forest as primary detector | Autoencoder, One-Class SVM | Fast, few hyperparameters, explainable-enough for v1; baseline for future models |
| ADR-3 | PostgreSQL | SQLite, TimescaleDB | Real-world skill; TimescaleDB is a later upgrade for time-series |
| ADR-4 | Rules before LLM | LLM-only | Deterministic evidence and a safe fallback |
| ADR-5 | Streamlit | React | Fastest path to a working UI in Python |
| ADR-6 | Synthetic data for gaps in UNSW-NB15 | Different dataset | Keeps the chosen dataset while providing CPU/utilisation signals; clearly labelled |
| ADR-7 | `joblib` + metadata JSON | `pickle` only | Reproducibility and version safety |
| ADR-8 | Provider-agnostic LLM client | Hard-wired SDK | Avoids lock-in; simplifies mocking in tests |

---

## 17. Evolution path

| Stage | Change | Trigger |
|---|---|---|
| 2 | TimescaleDB hypertable for `network_metrics` | Millions of rows, slow range queries |
| 3 | Real collectors (SNMP, NetFlow/IPFIX, Prometheus) | Move from dataset to live data |
| 4 | Redis queue + worker for diagnosis jobs | Agent calls block requests |
| 5 | Streaming ingest (Kafka/Redpanda) | Sustained high event rate |
| 6 | Split into `detector`, `diagnosis`, `api` services + Kubernetes | Independent scaling needs |
| 7 | Auth, multi-tenant, audit log | Shared deployment |
| 8 | Online learning / drift monitoring, feedback loop (engineer confirms or rejects diagnosis) | Model quality over time |

---

## 18. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Dataset does not fit metrics | High | High | Mapping layer + scenario generator (G1) |
| Isolation Forest performs poorly on flows | Medium | Medium | Feature engineering, supervised baseline, report honestly |
| Scope creep | High | High | Roadmap exit criteria; no new tech until current week's gate passes |
| LLM cost/latency/outage | Medium | Medium | On-demand invocation, caching, rule fallback |
| Hallucinated diagnoses | Medium | High | Evidence-ID validation, structured output |
| Docker/Windows friction | Medium | Low | Docker only in Week 8; local-first workflow |
| Time overrun | Medium | Medium | Roadmap includes buffer and a "minimum viable" cut line |

---

## 19. Definition of done (v1)

- [ ] `docker compose up` starts all services; dashboard reachable on `:8501`.
- [ ] Anomaly detector trained, evaluated, metrics published in README.
- [ ] Replay and at least four fault scenarios work end-to-end.
- [ ] Incidents created automatically with evidence and ranked causes.
- [ ] AI diagnosis produced with valid evidence citations; rule fallback verified.
- [ ] `/docs` documents every endpoint.
- [ ] Tests pass in CI; coverage targets met.
- [ ] README has architecture diagram, screenshots, setup steps, results and limitations.
