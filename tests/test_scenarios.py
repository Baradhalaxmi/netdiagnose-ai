"""Scenario accuracy: each injected fault's true cause must rank first.

Pure test (no API, no DB): generate a normal baseline and a fault window,
build evidence, run the rules. 50 random seeds per scenario.
"""

from datetime import datetime, timezone

import numpy as np
import pytest

from diagnosis.evidence import collect
from diagnosis.rules import evaluate
from simulator.replay import BASELINE_MINUTES, FAULT_ROWS, scenario_payloads
from simulator.scenarios import SCENARIOS, fault_rows, normal_rows

SEEDS = range(50)


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_true_cause_ranks_first(name):
    expected = SCENARIOS[name].expected_rule
    misses = []
    for seed in SEEDS:
        rng = np.random.default_rng(seed)
        causes = evaluate(collect(fault_rows(name, FAULT_ROWS, 1000, rng),
                                  normal_rows(name, BASELINE_MINUTES, 1000, rng)))
        if not causes or causes[0]["rule_id"] != expected:
            misses.append((seed, causes[0]["rule_id"] if causes else None))
    assert not misses, f"{name}: wrong top cause for seeds {misses}"


def test_every_scenario_targets_a_real_rule():
    from diagnosis.rules import load_rules

    rules = {r.id: r.cause for r in load_rules()}
    for s in SCENARIOS.values():
        assert rules[s.expected_rule] == s.expected_cause


def test_generated_values_respect_physical_limits():
    rng = np.random.default_rng(0)
    for name in SCENARIOS:
        for df in (normal_rows(name, 500, 1000, rng), fault_rows(name, 500, 1000, rng)):
            assert df["packet_loss_pct"].between(0, 100).all()
            assert df["cpu_usage_pct"].between(0, 100).all()
            assert df["interface_utilization_pct"].between(0, 100).all()
            assert (df["throughput_mbps"] <= 1000).all()


def test_scenario_payloads_timeline():
    now = datetime(2026, 1, 15, 12, tzinfo=timezone.utc)
    device = {"id": 3, "link_capacity_mbps": 1000}
    feats = [{"dur": 1.0}]
    base, fault = scenario_payloads("congestion", device, feats, now, np.random.default_rng(0))
    assert len(base) == BASELINE_MINUTES and len(fault) == FAULT_ROWS
    assert base[-1]["ts"] < fault[0]["ts"] and fault[-1]["ts"] == now.isoformat()
    assert all("features" not in r for r in base) and all(r["features"] for r in fault)
    assert {r["scenario"] for r in base + fault} == {"congestion"}
    assert {r["device_id"] for r in base + fault} == {3}
