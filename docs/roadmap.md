# NetDiagnose AI — Roadmap

An 8-week build plan (plus a Week 0 setup) following the order: **Networking → Dataset → ML → Database → FastAPI → Dashboard → Diagnosis Engine → AI Agent → Docker.**

**Pace assumed:** ~10–15 hours/week. If you have more time, finish early and use the stretch goals. If you have less, stretch each week to two (see section 11).

**Rule of the roadmap:** do not start the next week until the current week's **exit gate** passes. Each gate is a concrete, testable outcome.

---

## 1. Overview

| Week | Theme | Main deliverable | Exit gate |
|---|---|---|---|
| 0 | Setup | Repo, environment, tools | `python --version`, `psql`, `git` all work; repo on GitHub |
| 1 | Networking + dataset | Understanding of network metrics and UNSW-NB15 | Written metric-mapping table and data dictionary |
| 2 | pandas + EDA | Cleaned dataset, feature set | `data/processed/` file + EDA notebook with findings |
| 3 | scikit-learn | Working anomaly detector | `detect_anomaly()` returns result; F1/ROC-AUC reported |
| 4 | PostgreSQL + SQLAlchemy | Persistent storage | Metrics and anomalies stored and queryable |
| 5 | FastAPI | Working backend | All core endpoints work in `/docs` |
| 6 | Streamlit + diagnosis rules | Dashboard + rule engine | Fault scenario → incident → ranked causes shown in UI |
| 7 | AI agent | Evidence-based AI diagnosis | Valid structured diagnosis with citations; fallback works |
| 8 | Docker + testing + GitHub | Portfolio-ready project | `docker compose up` works from a clean clone; CI green |

```text
Week:   0     1      2      3      4       5       6        7       8
       Setup  Net    EDA    ML     SQL     API     UI+Rules Agent   Docker
        ▓▓    ▓▓▓▓   ▓▓▓▓   ▓▓▓▓   ▓▓▓▓    ▓▓▓▓    ▓▓▓▓▓    ▓▓▓▓    ▓▓▓▓
```

---

## Week 0 — Setup (1–2 days)

**Goal:** a working environment and an empty but organised repo.

### Tasks
- [ ] Install Python 3.11+ (3.12 is fine), VS Code, Git, PostgreSQL 15+.
- [ ] Skip Docker Desktop for now.
- [ ] VS Code extensions: Python, Pylance, Jupyter, SQLTools (optional).
- [ ] Create the folder `netdiagnose-ai/` and open it in VS Code.
- [ ] Create a virtual environment:
  ```bash
  python -m venv .venv
  # Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
  pip install -r requirements.txt
  ```
- [ ] `git init`, add `.gitignore`, first commit, push to a new GitHub repo.
- [ ] Create the starter folders: `data/raw`, `ml`, `backend`, `dashboard`, `notebooks`, `tests`, `docs`.
- [ ] Copy `docs/architecture.md` and `docs/roadmap.md` into the repo.

### Exit gate
- `python -c "import pandas, sklearn, fastapi, streamlit"` runs without error.
- `psql --version` works.
- Repo visible on GitHub with the starter structure.

---

## Week 1 — Networking fundamentals + the dataset

**Goal:** understand what the metrics *mean* before modelling them.

### Learn (≈ 40% of time)
| Topic | Must understand |
|---|---|
| TCP/IP basics | Packets, flows, ports, protocols (TCP/UDP/ICMP) |
| Latency, RTT | What `tcprtt` measures; why TCP-only |
| Packet loss and retransmission | Causes: congestion, bad link, buffer overflow |
| Jitter | Variation in delay; impact on voice/video |
| Throughput vs bandwidth vs utilisation | Why 94% utilisation causes queueing |
| Typical failure patterns | Congestion, server overload, link flap, DoS |

### Build (≈ 60%)
- [ ] Download UNSW-NB15 (**training-set** and **testing-set** CSVs, plus the feature description file).
- [ ] Place in `data/raw/`.
- [ ] Create `notebooks/01_exploration.ipynb` and run:
  ```python
  import pandas as pd
  df = pd.read_csv("data/raw/UNSW_NB15_training-set.csv")
  print(df.shape); print(df.head()); print(df.columns.tolist())
  print(df.isnull().sum()); print(df["label"].value_counts()); print(df["attack_cat"].value_counts())
  ```
- [ ] Write `docs/data_dictionary.md`: each column, its meaning, unit, whether you will keep it.
- [ ] Fill in the **metric mapping table** (architecture §7.2): confirm each formula against actual column units.

### Deliverables
- `docs/data_dictionary.md`
- Mapping table with verified formulas
- A one-paragraph explanation, in your own words, of how congestion differs from server overload in metric terms

### Exit gate
You can explain every column you plan to use, and you have identified which metrics the dataset cannot supply (CPU, interface utilisation).

---

## Week 2 — pandas + exploratory data analysis

**Goal:** a clean, feature-ready dataset.

### Learn
`DataFrame` selection, `groupby`, `merge`, missing values, `describe()`, histograms/boxplots with matplotlib, categorical encoding basics.

### Build
- [ ] Missing values, duplicates, infinities: decide and document handling.
- [ ] Derive `latency_ms`, `jitter_ms`, `packet_loss_pct`, `throughput_mbps` (put code in `ml/features.py`, **not** only in the notebook).
- [ ] Distribution plots per metric, split by `label`.
- [ ] Correlation heatmap; remove redundant features.
- [ ] Check class balance and outliers; note `tcprtt = 0` for non-TCP.
- [ ] Select a feature list (start with ~15–25 features, numeric + a few encoded categoricals such as `proto`, `state`).
- [ ] Save `data/processed/train.parquet` and `test.parquet` (or CSV).
- [ ] **Synthetic generator v0:** `simulator/scenarios.py` with `generate_cpu()` and `generate_interface_util()` correlated to throughput.

### Deliverables
- `notebooks/01_exploration.ipynb` with commentary
- `ml/features.py` with unit-tested functions
- 5–8 findings written at the end of the notebook

### Exit gate
`pytest tests/test_features.py` passes (including zero-packet edge case), and processed files exist.

---

## Week 3 — scikit-learn: the anomaly detector

**Goal:** working, evaluated, saved model.

### Learn
Train/test split, scaling, Isolation Forest parameters (`n_estimators`, `max_samples`, `contamination`), precision/recall/F1, ROC-AUC, confusion matrix, `Pipeline`, `joblib`.

### Build
- [ ] `notebooks/02_modeling.ipynb`: baseline Isolation Forest trained **on normal rows only**.
- [ ] Evaluate on the held-out test set with both classes.
- [ ] Tune a small grid (contamination, n_estimators, max_samples); record results in a table.
- [ ] Train a **Random Forest** supervised baseline for comparison.
- [ ] Choose the alert threshold on a validation split; document the precision/recall trade-off.
- [ ] Implement normalised score (architecture §8.4).
- [ ] Write `ml/train.py` (CLI) producing `model.joblib` and `model_meta.json`.
- [ ] Write `ml/predict.py` with `detect_anomaly(record) -> dict`.
- [ ] Add a few attack-category breakdowns (which attack types does it miss?).

### Deliverables
- Trained model + metadata
- Results table (Isolation Forest vs baseline)
- Confusion matrix image in `docs/images/`

### Exit gate
```python
from ml.predict import detect_anomaly
print(detect_anomaly(sample_record))   # {'is_anomaly': True, 'score': 0.78, 'model_version': '...'}
```
and metrics are written in the README. Be honest about weak spots — that reads as maturity, not failure.

> **Time-box:** if F1 is poor, spend at most 2 extra days on feature engineering, then move on. The system's value is the pipeline, not a leaderboard score.

---

## Week 4 — PostgreSQL + SQLAlchemy

**Goal:** persistent storage of everything the detector produces.

### Learn
Tables, primary/foreign keys, indexes, `INSERT/SELECT/JOIN/GROUP BY`, time-range queries, SQLAlchemy ORM sessions, environment-based config.

### Build
- [ ] Create database `netdiagnose` and a dedicated user.
- [ ] Apply `db/schema.sql`.
- [ ] `backend/config.py` loading `.env`.
- [ ] `backend/db/models.py`, `session.py`, `crud.py`.
- [ ] Seed `devices` (e.g. 5–8 named devices: router, switch, web server, DB server).
- [ ] Loader script: read processed data → assign to devices/timestamps → bulk insert into `network_metrics`.
- [ ] Batch-score stored metrics and write to `anomalies`.
- [ ] Write 6–8 SQL queries you will need (anomalies per device per hour, top devices by anomaly count, etc.) and save them in `db/queries.sql`.
- [ ] Add useful indexes (`device_id, ts`).

### Deliverables
- Database populated with ≥ 50k metric rows
- `db/queries.sql`
- ER diagram (export from pgAdmin or draw.io) in `docs/images/`

### Exit gate
A script does `Python → PostgreSQL → ML → anomalies table`, and a SQL query returns "anomalies per device per hour".

---

## Week 5 — FastAPI backend

**Goal:** the system usable through an API.

### Learn
Path/query parameters, Pydantic models, dependency injection (`Depends`), routers, status codes, error handling, `TestClient`.

### Build
- [ ] `backend/main.py` with routers: `metrics`, `detect`, `incidents`, `stats`, `health`.
- [ ] Implement endpoints from `docs/api_spec.md`:
  `GET /health`, `GET /devices`, `GET /metrics`, `POST /metrics`, `POST /detect`, `GET /incidents`, `GET /incidents/{id}`, `GET /stats/summary`.
- [ ] Load the model **once** at startup (lifespan event), not per request.
- [ ] **Incident correlation service** (architecture §9.1): group anomalies into incidents.
- [ ] Standard error body and request logging.
- [ ] Pagination on list endpoints.
- [ ] Integration tests with a test database.

### Deliverables
- Running API at `http://localhost:8000/docs`
- `tests/test_api.py`

### Exit gate
From `/docs` you can POST a metric, see it flagged, and then find an incident through `GET /incidents`.

---

## Week 6 — Streamlit dashboard + diagnosis rules

**Goal:** see the system working and reason about causes. This is the heaviest week; split into 6a and 6b.

### 6a — Diagnosis engine (first half)
- [ ] `diagnosis/rules.yaml` (architecture §9.3).
- [ ] `diagnosis/evidence.py`: baseline computation (median/MAD) and evidence rows.
- [ ] `diagnosis/rules.py`: evaluate rules, rank causes, output contract (§9.5).
- [ ] Unit tests: one test per rule, plus an overlap case (congestion + overload).
- [ ] `POST /incidents/{id}/diagnose` (rules only for now).
- [ ] **Scenario generator**: four faults with ground truth — `congestion`, `server_overload`, `link_flap`, `ddos`. Write `simulator/scenarios.py` and `simulator/replay.py`.
- [ ] Scenario accuracy test: each scenario's true cause ranks first.

### 6b — Dashboard (second half)
- [ ] Page 1 **Overview**: KPIs (devices, anomalies 24h, open incidents), anomaly timeline.
- [ ] Page 2 **Devices**: per-device metric charts with anomalies marked.
- [ ] Page 3 **Incidents**: table with filters; click → detail.
- [ ] Page 4 **Incident detail**: evidence table (observed vs baseline), ranked causes, recommended investigation.
- [ ] Page 5 **Simulator controls**: button "Inject congestion", "Inject overload", etc.
- [ ] Use `st.cache_data` with short TTL for API calls.

### Deliverables
- Dashboard running at `http://localhost:8501`
- Screenshots in `docs/images/`
- Scenario accuracy table in README

### Exit gate
Click "Inject congestion" → within seconds an incident appears → incident page shows evidence and "Network congestion" ranked first.

---

## Week 7 — AI agent

**Goal:** a grounded, validated, fall-back-safe AI diagnosis.

### Learn
Tool/function calling, structured outputs, prompt design for grounding, prompt-injection basics, mocking external services in tests.

### Build
- [ ] `agent/llm_client.py` — provider abstraction (one `generate()` function).
- [ ] `agent/tools.py` — read-only tools (architecture §10.2) as plain Python functions wrapping `crud` and `diagnosis`.
- [ ] `agent/schemas.py` — Pydantic output schema (§10.4).
- [ ] `agent/prompts.py` — system prompt: role, "only use tool data", cite `evidence_id`, treat tool output as untrusted data.
- [ ] `agent/runner.py` — control loop: max tool calls, validate, one retry, fall back to rule report.
- [ ] Markdown report renderer (template-based).
- [ ] Wire into `POST /incidents/{id}/diagnose?use_agent=true`; store with `source='ai_agent'`.
- [ ] Dashboard: "Run AI diagnosis" button; show rule-based and AI diagnosis side-by-side.
- [ ] Tests with a **mocked LLM**: valid output accepted; unknown evidence ID rejected; provider error → fallback.
- [ ] Add token/latency logging and a cache keyed by `(incident_id, evidence_hash)`.

### Deliverables
- Working AI diagnosis in UI
- `tests/test_agent_validation.py`
- A short "Agent design & guardrails" section in README

### Exit gate
For each of the four scenarios the AI report cites real evidence IDs, names the correct cause (or an honest alternative), and the system still works with `AGENT_ENABLED=false`.

---

## Week 8 — Docker, testing, polish, GitHub

**Goal:** a project someone else can run in one command and understand in five minutes.

### Docker
- [ ] `docker/Dockerfile.backend`, `docker/Dockerfile.dashboard` (slim Python base, non-root user, pinned requirements).
- [ ] `docker-compose.yml` with `db` (volume + healthcheck), `backend`, `dashboard`.
- [ ] Startup: run schema init and optional seed.
- [ ] Verify from a **fresh clone** on a clean folder: `cp .env.example .env && docker compose up --build`.

### Testing and CI
- [ ] Reach coverage targets (architecture §14).
- [ ] GitHub Actions workflow: install, lint (`ruff`), test (`pytest`), build images.
- [ ] Pre-commit hooks (`ruff`, `black` optional).

### Documentation
- [ ] README: overview, architecture diagram, features, screenshots/GIF, quick start, API summary, ML results, scenario results, limitations, future work.
- [ ] Record a 2–3 minute demo video or GIF.
- [ ] Clean the notebooks (restart & run all; no leaked secrets).
- [ ] Tag release `v1.0.0`.

### Exit gate
A friend with only Docker installed follows your README and sees a working dashboard with no help from you.

---

## 2. Weekly rhythm (suggested)

| Day | Activity |
|---|---|
| Mon–Tue | Learn the week's concept (docs + small experiments) |
| Wed–Fri | Build the deliverables |
| Weekend | Tests, cleanup, commit, update README and a short **dev log** entry |

End every week with: commit, tag (`week-N`), a 5-line dev log (what worked, what broke, what you learned).

---

## 3. Git milestones

| Tag | When | Content |
|---|---|---|
| `v0.1` | End Week 2 | Cleaned data pipeline + EDA |
| `v0.2` | End Week 3 | Trained detector |
| `v0.3` | End Week 4 | Database layer |
| `v0.4` | End Week 5 | REST API |
| `v0.5` | End Week 6 | Dashboard + rule engine |
| `v0.9` | End Week 7 | AI agent |
| `v1.0.0` | End Week 8 | Dockerised release |

Use short feature branches (`feat/rule-engine`) and meaningful commit messages (`feat: add congestion rule with evidence IDs`).

---

## 4. Minimum viable cut line

If time runs short, protect these in order:

1. ML detector with honest evaluation (Week 3)
2. PostgreSQL + FastAPI (Weeks 4–5)
3. Rule-based diagnosis with scenarios (Week 6a)
4. Dashboard essentials: Overview + Incident detail (Week 6b)
5. AI agent (Week 7)
6. Docker + CI (Week 8)

You can drop, without losing the story: extra dashboard pages, `get_logs` tool, response caching, pre-commit hooks. A project with items 1–4 plus Docker is still a strong portfolio piece.

---

## 5. Stretch goals (after v1.0.0)

- [ ] TimescaleDB hypertable and continuous aggregates
- [ ] Second detector (Autoencoder or LOF) and model comparison page
- [ ] SHAP-style feature contribution for each anomaly
- [ ] Engineer feedback buttons ("diagnosis correct / incorrect") feeding an evaluation table
- [ ] Prometheus `/metrics` endpoint + Grafana dashboard
- [ ] API-key authentication and rate limiting
- [ ] Real collector: ingest from `ping`/`iperf`/SNMP on a home lab
- [ ] Redis + background worker for diagnosis jobs
- [ ] Deploy on a cloud VM or a free container host

---

## 6. Skills map (what you will be able to claim)

| Area | Evidence in the project |
|---|---|
| Networking | Metric derivation, fault-pattern rules, scenario design |
| Data analysis | EDA notebook, cleaning decisions, feature selection |
| Machine learning | Isolation Forest, baselines, threshold selection, honest evaluation |
| SQL / databases | Schema design, indexes, analytic queries |
| Backend | FastAPI, Pydantic, service layer, tests |
| Frontend | Streamlit multi-page dashboard |
| AI engineering | Tool-using agent, structured output, guardrails, fallback |
| DevOps | Docker Compose, CI, config management |
| Engineering practice | ADRs, testing pyramid, documentation |

### Resume bullet templates
- Built an end-to-end network fault diagnosis platform (FastAPI, PostgreSQL, Streamlit, scikit-learn) detecting anomalies on UNSW-NB15 flows with **F1 = _x_ / ROC-AUC = _y_**.
- Designed a rule engine and evidence model that ranks probable causes (congestion, server overload, link fault, traffic flood), correctly identifying **_n_/4** injected fault scenarios.
- Implemented a tool-calling AI agent with schema validation, evidence-ID citation checks and deterministic fallback, preventing unsupported diagnoses.
- Containerised a three-service stack with Docker Compose and GitHub Actions CI (coverage **_z_%**).

(Fill in real numbers only after you measure them.)

---

## 7. Common pitfalls and how to avoid them

| Pitfall | Avoidance |
|---|---|
| Jumping to the LLM early | Keep the "AI agent" folder empty until Week 7 |
| Logic only in notebooks | Move working code into `ml/`, `diagnosis/` the same day |
| Training on all data with Isolation Forest | Fit on normal rows only; evaluate on both |
| Different preprocessing in train vs predict | Single shared `ml/features.py` |
| Loading the model on every request | Load once at startup |
| Hard-coded thresholds | `rules.yaml` and `.env` |
| Committing secrets or the dataset | `.gitignore` + `.env.example` |
| Perfectionism on model score | Time-box tuning to 2 days |
| Starting Docker too early | Only in Week 8 |

---

## 8. Learning resources (search terms)

- *FastAPI tutorial* (official docs) — routers, dependencies, testing
- *Streamlit docs: multipage apps, st.cache_data*
- *scikit-learn user guide: Outlier detection, IsolationForest*
- *SQLAlchemy 2.0 ORM quickstart*
- *PostgreSQL tutorial: indexes, window functions*
- *UNSW-NB15 feature description* (dataset page)
- *Docker Compose getting started*
- *Anthropic or other LLM provider docs: tool use / function calling*

---

## 9. Progress tracker

Copy into your README or a GitHub Project board.

```text
[ ] W0  Setup complete
[ ] W1  Data dictionary + mapping table
[ ] W2  Cleaned data + features.py tested
[ ] W3  Detector trained + evaluated + saved
[ ] W4  DB populated + queries written
[ ] W5  API endpoints working + tests
[ ] W6a Rule engine + scenarios passing
[ ] W6b Dashboard pages working
[ ] W7  Agent with guardrails + fallback
[ ] W8  Docker + CI + README + demo
```

---

## 10. Weekly review template

```markdown
## Week N review
- Goal:
- Done:
- Not done / carried over:
- Biggest problem and how I solved it:
- What I learned:
- Plan for next week:
```

---

## 11. If you have less time

| Hours/week | Duration | Adjustment |
|---|---|---|
| 5–7 | ~16 weeks | Double each week; split Weeks 3, 6, 7 into two blocks |
| 8–10 | ~11 weeks | Keep order; stretch Weeks 3 and 6 by one week each |
| 20+ | ~4–5 weeks | Run Week 1–2 together; start stretch goals early |
