"""Dashboard pages rendered headless with streamlit.testing.AppTest.

The API client is replaced with fakes, so these tests need neither the
API nor the database.
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from dashboard import client

VIEWS = Path(__file__).resolve().parents[1] / "dashboard" / "views"
T0, T1 = "2026-01-15T10:30:00+00:00", "2026-01-15T10:34:30+00:00"

DEVICES = [{"id": 1, "hostname": "access-sw-01", "device_type": "switch", "ip_address": "10.0.1.2",
            "location": "Office", "link_capacity_mbps": "1000.00"},
           {"id": 2, "hostname": "web-srv-01", "device_type": "server", "ip_address": None,
            "location": None, "link_capacity_mbps": "1000.00"}]
INCIDENT_ROW = {"id": 7, "code": "INC-0007", "device_id": 1, "hostname": "access-sw-01",
                "status": "open", "severity": "high", "peak_score": 0.91, "anomaly_count": 10,
                "opened_at": T0, "last_seen_at": T1, "closed_at": None,
                "top_cause": "Network congestion"}
EVIDENCE = [{"id": 101, "metric_name": "packet_loss_pct", "observed_value": 15.0,
             "baseline_value": 0.2, "ratio": 75.0, "threshold": 0.8, "breached": True, "note": ""},
            {"id": 102, "metric_name": "cpu_usage_pct", "observed_value": 40.0,
             "baseline_value": 30.0, "ratio": 1.3, "threshold": 45.0, "breached": False, "note": ""}]
DIAGNOSIS = {"id": 12, "source": "rules", "probable_cause": "Network congestion", "confidence": 0.8,
             "alternatives": [{"rule_id": "R-CONGESTION-WEAK", "cause": "Possible congestion",
                               "confidence": 0.5}],
             "recommendations": ["Identify top talkers on the affected interface"],
             "cited_evidence": [101], "report_markdown": "### Diagnosis: Network congestion",
             "created_at": T1}


def incident_detail(diagnosed=True):
    return {**{k: INCIDENT_ROW[k] for k in ("id", "code", "status", "severity", "peak_score",
                                            "anomaly_count", "opened_at", "last_seen_at",
                                            "closed_at")},
            "summary": None, "device": {"id": 1, "hostname": "access-sw-01"},
            "evidence": EVIDENCE if diagnosed else [],
            "diagnoses": [DIAGNOSIS] if diagnosed else []}


@pytest.fixture
def fake_api(monkeypatch):
    calls = []
    fakes = {
        "health": lambda: {"status": "ok", "database": "up", "model_loaded": True,
                           "model_version": "iforest-test", "agent_enabled": False},
        "devices": lambda: DEVICES,
        "stats": lambda: {"devices": 2, "metrics_24h": 1200, "anomalies_24h": 40,
                          "open_incidents": 1,
                          "incidents_by_severity": {"low": 0, "medium": 0, "high": 1, "critical": 0},
                          "anomalies_per_hour": [{"hour": T0, "count": 40}]},
        "incidents": lambda **kw: {"items": [INCIDENT_ROW], "total": 1, "limit": 100, "offset": 0},
        "incident": lambda i: incident_detail(),
        "metrics": lambda device_id, limit=500: [
            {"id": n, "device_id": device_id, "ts": f"2026-01-15T10:{n:02d}:00+00:00",
             "latency_ms": 20.0 + n, "packet_loss_pct": 0.2, "jitter_ms": 3.0,
             "throughput_mbps": 600.0, "cpu_usage_pct": 30.0, "interface_utilization_pct": 60.0,
             "source": "synthetic", "scenario": None, "is_anomaly": n == 5,
             "anomaly_score": 0.8 if n == 5 else 0.1} for n in range(10)],
        "scenarios": lambda: [{"name": "congestion", "description": "Link saturates.",
                               "expected_cause": "Network congestion",
                               "default_device": "access-sw-01"}],
        "inject": lambda name, device=None: calls.append(("inject", name, device)) or {
            "scenario": name, "device": device, "expected_cause": "Network congestion",
            "fault_rows": 10, "flagged": 10, "correct": True,
            "incident": {"id": 7, "code": "INC-0007", "created": True}, "diagnosis": DIAGNOSIS},
        "diagnose": lambda i, force=False: calls.append(("diagnose", i, force)) or [DIAGNOSIS],
        "update_incident": lambda i, **f: calls.append(("patch", i, f)) or incident_detail(),
    }
    for name, fn in fakes.items():
        monkeypatch.setattr(client, name, fn)
    return calls


def run(page: str, **state) -> AppTest:
    at = AppTest.from_file(str(VIEWS / f"{page}.py"), default_timeout=30)
    for k, v in state.items():
        at.session_state[k] = v
    return at.run()


@pytest.mark.parametrize("page", ["overview", "devices", "incidents", "incident_detail", "simulator"])
def test_every_page_renders_without_errors(fake_api, page):
    at = run(page)
    assert not at.exception, at.exception
    assert not at.error


def test_overview_kpis(fake_api):
    at = run("overview")
    assert {m.label: m.value for m in at.metric} == {
        "Devices": "2", "Metrics (24h)": "1,200", "Anomalies (24h)": "40", "Open incidents": "1"}


def test_simulator_inject_shows_result(fake_api):
    at = run("simulator")
    at.button(key="inject_congestion").click().run()
    assert ("inject", "congestion", "access-sw-01") in fake_api
    assert "matches the injected fault" in at.success[0].value
    assert at.metric[0].value == "INC-0007"


def test_incident_detail_shows_cause_evidence_and_steps(fake_api):
    at = run("incident_detail", incident_id=7)
    assert any(m.value == "### Probable cause: Network congestion" for m in at.markdown)
    evidence = at.dataframe[0].value
    assert list(evidence["id"]) == [101, 102]
    assert list(evidence["cited"]) == ["✔", ""]
    assert any("Identify top talkers" in m.value for m in at.markdown)


def test_incident_detail_run_diagnosis_button(fake_api, monkeypatch):
    monkeypatch.setattr(client, "incident", lambda i: incident_detail(diagnosed=False))
    at = run("incident_detail", incident_id=7)
    assert "Not diagnosed yet" in at.info[0].value
    at.button[0].click().run()
    assert ("diagnose", 7, False) in fake_api


def test_api_down_shows_error_not_traceback(monkeypatch):
    def down(*a, **k):
        raise client.ApiError("Cannot reach the API at http://localhost:8000.", "UNREACHABLE")

    for name in ("stats", "incidents", "devices"):
        monkeypatch.setattr(client, name, down)
    at = run("overview")
    assert not at.exception and "Cannot reach the API" in at.error[0].value
