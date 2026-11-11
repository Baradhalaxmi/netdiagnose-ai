import numpy as np
import pandas as pd
import pytest

from diagnosis.evidence import baseline_stats, collect
from diagnosis.report import render_rules_report
from diagnosis.rules import evaluate, load_rules, parse_rules

HEALTHY = {"latency_ms": 20, "packet_loss_pct": 0.2, "jitter_ms": 3,
           "throughput_mbps": 200, "cpu_usage_pct": 30, "interface_utilization_pct": 20}


def ev(**observed):
    """Evidence list: healthy values unless overridden; baseline = healthy."""
    return [{"id": i, "metric": m, "observed": observed.get(m, v), "baseline": v}
            for i, (m, v) in enumerate(HEALTHY.items(), start=100)]


def top(evidence):
    causes = evaluate(evidence)
    return causes[0]["rule_id"] if causes else None


# ---- rules file ------------------------------------------------------
def test_rules_file_loads_all_six_rules():
    ids = [r.id for r in load_rules()]
    assert ids == ["R-CONGESTION", "R-CONGESTION-WEAK", "R-SERVER-OVERLOAD",
                   "R-LINK-FAULT", "R-JITTER-QOS", "R-TRAFFIC-FLOOD"]


@pytest.mark.parametrize("bad,msg", [
    ({"id": "X", "cause": "c", "confidence": 0.5, "all": [{"metric": "nope", "op": ">", "value": 1}]},
     "unknown metric"),
    ({"id": "X", "cause": "c", "confidence": 0.5, "all": [{"metric": "jitter_ms", "op": "~", "value": 1}]},
     "unknown op"),
    ({"id": "X", "cause": "c", "confidence": 1.5, "all": [{"metric": "jitter_ms", "op": ">", "value": 1}]},
     "confidence"),
    ({"id": "X", "cause": "c", "confidence": 0.5, "all": [{"metric": "jitter_ms", "op": ">"}]},
     "exactly one"),
    ({"id": "X", "cause": "c", "confidence": 0.5, "all": []}, "at least one"),
])
def test_invalid_rules_are_rejected(bad, msg):
    with pytest.raises(ValueError, match=msg):
        parse_rules([bad])


# ---- one test per rule -----------------------------------------------
def test_healthy_evidence_matches_nothing():
    assert evaluate(ev()) == []


def test_congestion():
    assert top(ev(packet_loss_pct=15, latency_ms=250, interface_utilization_pct=92)) == "R-CONGESTION"


def test_congestion_weak():
    assert top(ev(packet_loss_pct=7, interface_utilization_pct=88)) == "R-CONGESTION-WEAK"


def test_server_overload():
    assert top(ev(cpu_usage_pct=96, latency_ms=320)) == "R-SERVER-OVERLOAD"


def test_link_fault():
    assert top(ev(packet_loss_pct=45)) == "R-LINK-FAULT"


def test_jitter_qos():
    assert top(ev(jitter_ms=45)) == "R-JITTER-QOS"


def test_traffic_flood_uses_baseline_ratio():
    assert top(ev(throughput_mbps=900, interface_utilization_pct=95)) == "R-TRAFFIC-FLOOD"
    # same utilisation, but throughput only 1.5x baseline -> not a flood
    causes = evaluate(ev(throughput_mbps=300, interface_utilization_pct=95))
    assert "R-TRAFFIC-FLOOD" not in [c["rule_id"] for c in causes if c["matched"] == c["total"]]


def test_baseline_ratio_needs_a_baseline():
    e = ev(throughput_mbps=900, interface_utilization_pct=95)
    for item in e:
        item["baseline"] = None
    flood = next(c for c in evaluate(e) if c["rule_id"] == "R-TRAFFIC-FLOOD")
    assert flood["matched"] == 1          # only the absolute utilisation condition


# ---- overlap and ranking ---------------------------------------------
def test_overlap_congestion_and_overload_both_reported():
    causes = evaluate(ev(packet_loss_pct=15, latency_ms=250, interface_utilization_pct=92,
                         cpu_usage_pct=95))
    ids = [c["rule_id"] for c in causes]
    assert ids[0] in ("R-CONGESTION", "R-SERVER-OVERLOAD")
    assert {"R-CONGESTION", "R-SERVER-OVERLOAD"} <= set(ids)
    # tie at 0.8: both fully matched, both with confidence 0.8
    assert causes[0]["confidence"] == causes[1]["confidence"] == 0.8


def test_partial_match_is_scaled_and_cites_only_matched_evidence():
    causes = evaluate(ev(latency_ms=250, interface_utilization_pct=92))   # 2/3 congestion
    c = next(c for c in causes if c["rule_id"] == "R-CONGESTION")
    assert c["confidence"] == pytest.approx(0.8 * 2 / 3, abs=1e-3)
    assert c["evidence_metrics"] == ["latency_ms", "interface_utilization_pct"]
    assert c["evidence_ids"] == [100, 105]


def test_below_half_matched_is_dropped():
    causes = evaluate(ev(latency_ms=250))   # 1/3 congestion, 1/2 overload
    assert "R-CONGESTION" not in [c["rule_id"] for c in causes]
    assert "R-SERVER-OVERLOAD" in [c["rule_id"] for c in causes]


def test_flood_beats_congestion_when_both_match():
    causes = evaluate(ev(packet_loss_pct=12, latency_ms=260, interface_utilization_pct=97,
                         throughput_mbps=970))
    assert [c["rule_id"] for c in causes[:2]] == ["R-TRAFFIC-FLOOD", "R-CONGESTION"]


# ---- evidence --------------------------------------------------------
def test_baseline_stats_median_and_mad():
    s = baseline_stats(pd.DataFrame({"latency_ms": [10, 12, 14, 100]}))["latency_ms"]
    assert s["median"] == 13 and s["mad"] == 2 and s["n"] == 4


def test_collect_flags_breach_and_computes_ratio():
    rng = np.random.default_rng(0)
    base = pd.DataFrame({"latency_ms": rng.normal(20, 2, 60)})
    window = pd.DataFrame({"latency_ms": [240, 260]})
    [e] = collect(window, base)
    assert e["metric"] == "latency_ms" and e["observed"] == 250 and e["breached"]
    assert e["ratio"] == pytest.approx(250 / e["baseline"])


def test_collect_small_change_on_flat_baseline_not_breached():
    base = pd.DataFrame({"cpu_usage_pct": [30.0] * 50})          # MAD = 0
    [e] = collect(pd.DataFrame({"cpu_usage_pct": [32.0]}), base)
    assert not e["breached"]                                     # floor sigma prevents a false alarm


def test_collect_skips_metric_without_window_data_and_handles_no_baseline():
    window = pd.DataFrame({"latency_ms": [np.nan], "cpu_usage_pct": [95.0]})
    [e] = collect(window, pd.DataFrame())
    assert e["metric"] == "cpu_usage_pct" and e["baseline"] is None and not e["breached"]


# ---- report ----------------------------------------------------------
def test_report_contains_cause_evidence_and_steps():
    e = ev(cpu_usage_pct=96, latency_ms=320)
    md = render_rules_report("INC-0001", "app-srv-01", evaluate(e), e)
    assert "Server overload" in md and "INC-0001" in md and "| 104 | cpu_usage_pct" in md
    assert "#### Recommended investigation" in md


def test_report_when_nothing_matches():
    md = render_rules_report("INC-0002", "fw-01", [], ev())
    assert "Unclassified anomaly" in md


def test_exclusion_condition_alone_does_not_suggest_a_cause():
    # "packet_loss < 5" holds on healthy data; without real jitter it must not suggest QoS
    assert "R-JITTER-QOS" not in [c["rule_id"] for c in evaluate(ev(packet_loss_pct=1))]
