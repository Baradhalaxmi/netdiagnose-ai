# NetDiagnose AI

Evidence-based network fault diagnosis: **ML detects → rules gather evidence → an AI agent explains.**

> Status: in development. See [`docs/roadmap.md`](docs/roadmap.md).

## What it does
1. Detects anomalous network behaviour with an Isolation Forest trained on UNSW-NB15 flows.
2. Groups anomalies into incidents and collects evidence (observed vs baseline).
3. Ranks probable causes with a transparent rule engine (congestion, server overload, link fault, traffic flood).
4. Optionally asks an AI agent to produce a cited, validated diagnosis, with a rule-based fallback.

## Architecture

```text
Streamlit ──▶ FastAPI ──▶ PostgreSQL
                │
                ├──▶ ML model (Isolation Forest)
                ├──▶ Diagnosis engine (rules + evidence)
                └──▶ AI agent (read-only tools, validated output)
```

Details: [`docs/architecture.md`](docs/architecture.md) · API: [`docs/api_spec.md`](docs/api_spec.md) · Schema: [`db/schema.sql`](db/schema.sql)

## Quick start (local)
See [`docs/setup_guide.md`](docs/setup_guide.md).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Quick start (Docker, after Week 8)
```bash
cp .env.example .env
docker compose up --build
# Dashboard: http://localhost:8501   API docs: http://localhost:8000/docs
```

## Project layout
```text
data/  notebooks/  ml/  diagnosis/  agent/  backend/  dashboard/
simulator/  db/  tests/  docker/  docs/
```

## Results
Measured on the official UNSW-NB15 **testing set** (82,332 flows). Isolation Forest is trained on normal flows only; its threshold is chosen for max F1 on a validation split of the training set. Reproduce with `python -m ml.train --baseline` (full numbers in `ml/model_meta.json`).

| Model | Precision | Recall | F1 | ROC-AUC |
|---|---|---|---|---|
| Isolation Forest (unsupervised) | 0.72 | 0.83 | 0.77 | 0.79 |
| Random Forest (supervised baseline) | 0.85 | 0.98 | 0.91 | 0.98 |

Isolation Forest misses the most on Shellcode (50% detected), Fuzzers (54%) and Reconnaissance (59%), and catches Generic (99%), Backdoor (93%) and DoS (91%) attacks well.

| Scenario | Expected cause | Top-ranked cause | Correct |
|---|---|---|---|
| Congestion | Network congestion | _tbd_ | _tbd_ |
| Server overload | Server overload | _tbd_ | _tbd_ |
| Link flap | Link failure | _tbd_ | _tbd_ |
| DDoS | Traffic flood | _tbd_ | _tbd_ |

## Limitations
- UNSW-NB15 is an intrusion dataset; CPU and interface utilisation are **synthetic**.
- Isolation Forest is unsupervised and can miss subtle attacks.
- Rule thresholds are heuristics and should be tuned per environment.

## Roadmap
Week-by-week plan with exit gates: [`docs/roadmap.md`](docs/roadmap.md).

## License
Choose one (e.g. MIT) before publishing.
