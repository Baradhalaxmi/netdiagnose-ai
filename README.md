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
Measured on the official UNSW-NB15 **testing set** (82,332 flows). Isolation Forest is trained on normal flows only; its threshold is chosen for max F1 on a validation split of the training set. Reproduce with `python -m ml.train --n-estimators 300 --max-samples 8192 --log-numeric --baseline` (full numbers in `ml/model_meta.json`).

| Model | Precision | Recall | F1 | ROC-AUC |
|---|---|---|---|---|
| Isolation Forest, tuned (log-scaled features, 8192 rows/tree, 300 trees) | 0.78 | 0.84 | **0.81** | **0.87** |
| Isolation Forest, untuned (raw features, defaults) | 0.72 | 0.83 | 0.77 | 0.79 |
| Random Forest (supervised baseline) | 0.85 | 0.98 | 0.91 | 0.98 |

Tuning: 24 configs, chosen on validation F1 only ([docs/tuning_results.md](docs/tuning_results.md)). Log-scaling heavy-tailed features gave the biggest gain; removing duplicate training rows did not help.

Isolation Forest misses the most on Fuzzers (49% detected), Shellcode (50%) and Reconnaissance (59%), and catches Generic (99%), Backdoor (93%) and DoS (91%) attacks well.

### Diagnosis on injected faults
Each scenario writes an hour of normal telemetry, then a 5-minute fault carrying real attack-flow features, through the live API (`python -m simulator.replay scenario all`). The detector opens an incident; the rule engine ranks causes against the device baseline.

| Scenario | Expected cause | Top-ranked cause (confidence) | Runner-up | Correct |
|---|---|---|---|---|
| Congestion | Network congestion | Network congestion (0.80) | Possible congestion (0.50) | ✅ |
| Server overload | Server overload | Server overload (0.80) | – | ✅ |
| Link flap | Link failure / flapping | Link failure / flapping (0.70) | Possible congestion (0.25) | ✅ |
| DDoS | Possible DoS / traffic flood | Possible DoS / traffic flood (0.85) | Network congestion (0.80) | ✅ |

The offline scenario test (`tests/test_scenarios.py`) repeats this over 50 random seeds per scenario: 200/200 correct. Fault profiles are synthetic, so this measures the rule logic, not real-world accuracy.

## Agent design & guardrails
The AI agent (`agent/`) explains and cross-checks the rule engine; it never invents facts or changes data.

- **Grounded input:** the incident and the rule engine's evidence rows go in the first message; the agent can then call four read-only tools (`get_metric_timeseries`, `compare_baseline`, `get_device_status`, `get_previous_incidents`), all bound to that one incident's device.
- **Structured output:** it must finish by calling `submit_diagnosis` (strict JSON schema): cause, confidence band, evidence statements each citing an `evidence_id`, alternatives, next steps.
- **Validation:** unknown evidence ids are rejected; confidence may not exceed the strongest rule finding by more than one band. One retry with the problems listed, then fallback.
- **Fail soft:** provider errors, refusals, invalid output twice, or `AGENT_ENABLED=false` all return the rule-based diagnosis with `X-Agent-Fallback-Reason`.
- **Bounded:** at most `AGENT_MAX_TOOL_CALLS` (6) data-tool calls and a hard loop limit; tokens and latency are stored with each diagnosis.
- **Prompt injection:** monitored data is fenced in `<incident_data>` and the system prompt says it is data, not instructions; tools expose no raw IP addresses.
- **Cost:** results are cached per (incident, evidence hash); the system prompt and tool list are identical for every call so they are prompt-cached.
- **Model:** Claude Opus 5.5 (`claude-opus-5-5`, effort `medium`) via `agent/llm_client.py`, the only file that touches the provider SDK. Server-side refusal fallback (`fallbacks: "default"`) is enabled.

## Limitations
- UNSW-NB15 is an intrusion dataset; CPU and interface utilisation are **synthetic**.
- Isolation Forest is unsupervised and can miss subtle attacks.
- Rule thresholds are heuristics and should be tuned per environment.

## Roadmap
Week-by-week plan with exit gates: [`docs/roadmap.md`](docs/roadmap.md).

## License
Choose one (e.g. MIT) before publishing.
