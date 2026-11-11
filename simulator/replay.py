"""Drive the running API with synthetic faults or dataset replay.

    python -m simulator.replay scenario congestion            # inject + diagnose
    python -m simulator.replay scenario ddos --device web-srv-01
    python -m simulator.replay scenario all                   # all four, prints an accuracy table
    python -m simulator.replay dataset --rate 5 --limit 100   # stream real flows at 5 rows/s

A scenario posts, for one device:
  1. BASELINE_MINUTES of normal telemetry (detect=false), so the diagnosis
     has a clean baseline right before the fault;
  2. FAULT_ROWS rows of fault telemetry over the last few minutes
     (detect=true). Each carries features of a real attack flow so the ML
     detector fires; the telemetry is what the rules use to explain it.
Then it calls POST /incidents/{id}/diagnose and compares the top cause
with the scenario's ground truth.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx
import numpy as np
import pandas as pd

from ml.features import split_xy
from simulator.scenarios import SCENARIOS, fault_rows, normal_rows

BASELINE_MINUTES = 60
FAULT_ROWS = 10
FAULT_SPACING = timedelta(seconds=30)
DATASET = "data/raw/UNSW_NB15_testing-set.csv"


# --------------------------------------------------------------------
# Payload building (pure; used by tests too)
# --------------------------------------------------------------------
def load_attack_features(csv_path: str = DATASET, n: int = 200, seed: int = 0) -> list[dict]:
    """Feature dicts of 'Generic' attack flows (the detector catches ~99% of them)."""
    df = pd.read_csv(csv_path)
    attacks = df[df["attack_cat"] == "Generic"].sample(n=n, random_state=seed)
    return split_xy(attacks)[0].to_dict(orient="records")


def scenario_payloads(
    name: str,
    device: dict,
    attack_features: list[dict],
    now: datetime,
    rng: np.random.Generator,
) -> tuple[list[dict], list[dict]]:
    """(baseline rows, fault rows) ready for POST /metrics."""
    cap = float(device["link_capacity_mbps"])
    fault_start = now - FAULT_SPACING * (FAULT_ROWS - 1)
    base_start = fault_start - timedelta(minutes=BASELINE_MINUTES)

    def rows(df: pd.DataFrame, start: datetime, step: timedelta, features: bool) -> list[dict]:
        out = []
        for i, values in enumerate(df.to_dict(orient="records")):
            row = {"device_id": device["id"], "ts": (start + step * i).isoformat(),
                   "source": "synthetic", "scenario": name,
                   **{k: round(float(v), 3) for k, v in values.items()}}
            if features:
                row["features"] = attack_features[int(rng.integers(len(attack_features)))]
            out.append(row)
        return out

    baseline = rows(normal_rows(name, BASELINE_MINUTES, cap, rng), base_start,
                    timedelta(minutes=1), features=False)
    fault = rows(fault_rows(name, FAULT_ROWS, cap, rng), fault_start, FAULT_SPACING, features=True)
    return baseline, fault


# --------------------------------------------------------------------
# Talking to the API
# --------------------------------------------------------------------
def _device(client: httpx.Client, hostname: str) -> dict:
    for d in client.get("/devices").raise_for_status().json()["items"]:
        if d["hostname"] == hostname:
            return d
    sys.exit(f"Unknown device {hostname!r}; see GET /devices")


def run_scenario(client: httpx.Client, name: str, hostname: str | None,
                 attack_features: list[dict], rng: np.random.Generator,
                 agent: bool = False) -> dict:
    s = SCENARIOS[name]
    device = _device(client, hostname or s.default_device)
    baseline, fault = scenario_payloads(name, device, attack_features,
                                        datetime.now(timezone.utc), rng)

    client.post("/metrics", json={"detect": False, "rows": baseline}).raise_for_status()
    result = client.post("/metrics", json={"detect": True, "rows": fault}).raise_for_status().json()
    if not result["incidents_touched"]:
        return {"scenario": name, "device": device["hostname"], "incident": None,
                "expected": s.expected_cause, "top_cause": None, "correct": False,
                "flagged": result["anomalies"]}

    inc = result["incidents_touched"][-1]
    [diag] = client.post(f"/incidents/{inc['id']}/diagnose",
                         params={"force": True}).raise_for_status().json()
    out = {
        "scenario": name, "device": device["hostname"], "incident": inc["code"],
        "flagged": result["anomalies"], "expected": s.expected_cause,
        "top_cause": diag["probable_cause"], "confidence": diag["confidence"],
        "alternatives": diag["alternatives"],
        "correct": diag["probable_cause"] == s.expected_cause,
    }
    if agent:
        r = client.post(f"/incidents/{inc['id']}/diagnose", params={"use_agent": True},
                        timeout=300).raise_for_status()
        diags = r.json()
        ai = next((d for d in diags if d["source"] == "ai_agent"), None)
        evidence = {e["id"] for e in client.get(f"/incidents/{inc['id']}").json()["evidence"]}
        out["ai"] = None if ai is None else {
            "cause": ai["probable_cause"], "cited": ai["cited_evidence"],
            "citations_valid": set(ai["cited_evidence"]) <= evidence,
            "alternatives": [a["cause"] for a in ai["alternatives"]],
        }
        out["ai_fallback"] = r.headers.get("X-Agent-Fallback-Reason")
    return out


def replay_dataset(client: httpx.Client, csv_path: str, rate: float, limit: int, seed: int) -> None:
    df = pd.read_csv(csv_path).sample(n=limit, random_state=seed)
    devices = client.get("/devices").raise_for_status().json()["items"]
    rng = np.random.default_rng(seed)
    X, y = split_xy(df)
    sent = flagged = 0
    for features in X.to_dict(orient="records"):
        row = {"device_id": devices[int(rng.integers(len(devices)))]["id"],
               "source": "dataset", "features": features}
        r = client.post("/metrics", json={"rows": [row]}).raise_for_status().json()
        sent += 1
        flagged += r["anomalies"]
        if sent % 20 == 0 or sent == limit:
            print(f"  sent {sent}/{limit}, flagged {flagged}")
        time.sleep(1 / rate)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Inject faults or replay the dataset into the API.")
    p.add_argument("--api", default="http://localhost:8000")
    p.add_argument("--seed", type=int, default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    sc = sub.add_parser("scenario", help="inject a fault scenario and diagnose it")
    sc.add_argument("name", choices=[*SCENARIOS, "all"])
    sc.add_argument("--device", help="hostname (default: the scenario's typical device)")
    sc.add_argument("--csv", default=DATASET, help="source of attack-flow features")
    sc.add_argument("--agent", action="store_true",
                    help="also run the AI agent (needs AGENT_ENABLED=true and an API key)")

    ds = sub.add_parser("dataset", help="stream dataset rows into POST /metrics")
    ds.add_argument("--csv", default=DATASET)
    ds.add_argument("--rate", type=float, default=5, help="rows per second")
    ds.add_argument("--limit", type=int, default=100)
    args = p.parse_args(argv)

    with httpx.Client(base_url=args.api, timeout=30) as client:
        if args.cmd == "dataset":
            replay_dataset(client, args.csv, args.rate, args.limit, args.seed or 0)
            return

        rng = np.random.default_rng(args.seed)
        features = load_attack_features(args.csv, seed=args.seed or 0)
        names = list(SCENARIOS) if args.name == "all" else [args.name]
        results = [run_scenario(client, n, args.device, features, rng,
                                    agent=args.cmd == "scenario" and args.agent)
                       for n in names]

    for r in results:
        mark = "OK " if r["correct"] else "MISS"
        print(f"[{mark}] {r['scenario']:<16} {r['device']:<13} {r['incident'] or '-':<9} "
              f"flagged {r['flagged']}/{FAULT_ROWS}  expected: {r['expected']:<28} "
              f"got: {r['top_cause']} ({r.get('confidence', 0):.2f})")
        for alt in r.get("alternatives", []):
            print(f"{'':>8}alternative: {alt['cause']} ({alt['confidence']:.2f})")
        if "ai" in r:
            if r["ai"] is None:
                print(f"{'':>8}AI agent: fell back to rules ({r['ai_fallback']})")
            else:
                a = r["ai"]
                print(f"{'':>8}AI agent: {a['cause']} | cites {a['cited']} "
                      f"({'valid' if a['citations_valid'] else 'INVALID'}) | "
                      f"alternatives: {', '.join(a['alternatives']) or '-'}")
    if len(results) > 1:
        print(f"\nScenario accuracy: {sum(r['correct'] for r in results)}/{len(results)}")


if __name__ == "__main__":
    main()
