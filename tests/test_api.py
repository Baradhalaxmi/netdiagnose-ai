"""API tests: FastAPI TestClient against a real PostgreSQL, rolled back after
each test. The detector is a small model trained on synthetic flows, so the
tests do not need ml/model.joblib or the dataset.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.deps import get_detector, get_session
from backend.main import app
from backend.services.incident_service import severity_for
from ml import train
from ml.predict import Detector
from tests.conftest import synthetic_flows

pytestmark = pytest.mark.db


# ---- fixtures ----------------------------------------------------------
@pytest.fixture(scope="module")
def detector():
    train_df = pd.concat([synthetic_flows(400, 0), synthetic_flows(100, 1)], ignore_index=True)
    test_df = pd.concat([synthetic_flows(100, 0, seed=7), synthetic_flows(30, 1, seed=7)],
                        ignore_index=True)
    model, meta = train.train(train_df, test_df, n_estimators=50)
    return Detector(model, meta)


@pytest.fixture
def client(db_session, detector):
    app.dependency_overrides[get_session] = lambda: db_session
    app.dependency_overrides[get_detector] = lambda: detector
    try:
        yield TestClient(app)        # no "with": skip lifespan, the override supplies the model
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def device_id(client, db_session):
    from backend.db import crud

    crud.upsert_devices(db_session, [{"hostname": "test-api-dev", "device_type": "server",
                                      "link_capacity_mbps": 1000}])
    db_session.commit()
    return next(d.id for d in crud.list_devices(db_session) if d.hostname == "test-api-dev")


def features(label: int, seed: int = 3) -> dict:
    row = synthetic_flows(1, label, seed=seed).iloc[0]
    return row.drop(["id", "label", "attack_cat"]).to_dict()


def post_attack(client, device_id, ts=None, **extra):
    row = {"device_id": device_id, "features": features(1), **extra}
    if ts:
        row["ts"] = ts.isoformat()
    return client.post("/metrics", json={"rows": [row]})


# ---- pure ------------------------------------------------------------
@pytest.mark.parametrize(
    "peak,count,expected",
    [(0.3, 1, "low"), (0.65, 1, "medium"), (0.3, 5, "medium"),
     (0.85, 1, "high"), (0.3, 20, "high"), (0.95, 50, "critical")],
)
def test_severity_bands(peak, count, expected):
    assert severity_for(peak, count) == expected


# ---- health / devices ------------------------------------------------
def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["database"] == "up" and body["model_loaded"]


def test_health_degraded_without_model(client):
    app.dependency_overrides[get_detector] = lambda: None
    body = client.get("/health").json()
    assert body["status"] == "degraded" and body["model_loaded"] is False


def test_devices_lists_seeded_device(client, device_id):
    hosts = [d["hostname"] for d in client.get("/devices").json()["items"]]
    assert "test-api-dev" in hosts


# ---- exit gate: POST metric -> flagged -> incident -------------------
def test_post_metric_flags_and_opens_incident(client, device_id):
    r = post_attack(client, device_id)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body == {"inserted": 1, "anomalies": 1, "incidents_touched": body["incidents_touched"]}
    [touched] = body["incidents_touched"]
    assert touched["created"] and touched["code"] == f"INC-{touched['id']:04d}"

    listed = client.get("/incidents", params={"device_id": device_id}).json()
    assert listed["total"] == 1 and listed["items"][0]["status"] == "open"

    detail = client.get(f"/incidents/{touched['id']}").json()
    assert detail["device"]["hostname"] == "test-api-dev"
    assert detail["anomaly_count"] == 1 and detail["evidence"] == [] and detail["diagnoses"] == []


def test_anomalies_within_window_join_same_incident(client, device_id):
    t0 = datetime.now(timezone.utc)
    first = post_attack(client, device_id, ts=t0).json()["incidents_touched"][0]
    second = post_attack(client, device_id, ts=t0 + timedelta(minutes=3)).json()["incidents_touched"][0]
    assert second["id"] == first["id"] and second["created"] is False
    assert client.get(f"/incidents/{first['id']}").json()["anomaly_count"] == 2


def test_anomaly_outside_window_opens_new_incident(client, device_id):
    t0 = datetime.now(timezone.utc)
    first = post_attack(client, device_id, ts=t0 - timedelta(minutes=8)).json()["incidents_touched"][0]
    later = post_attack(client, device_id, ts=t0).json()["incidents_touched"][0]
    assert later["id"] != first["id"] and later["created"]


def test_normal_traffic_does_not_open_incident(client, device_id):
    rows = [{"device_id": device_id, "features": features(0, seed=s)} for s in range(5)]
    body = client.post("/metrics", json={"rows": rows}).json()
    flagged = body["anomalies"]
    assert flagged <= 1                       # an unsupervised model may flag the odd one
    assert len(body["incidents_touched"]) == (1 if flagged else 0)


def test_detect_false_stores_without_scoring(client, device_id):
    body = client.post("/metrics", json={"detect": False, "rows": [
        {"device_id": device_id, "latency_ms": 12.5}]}).json()
    assert body == {"inserted": 1, "anomalies": 0, "incidents_touched": []}


def test_missing_metrics_are_derived_from_features(client, device_id):
    f = features(1)
    post_attack(client, device_id)
    m = client.get("/metrics", params={"device_id": device_id}).json()["items"][0]
    expected = (f["sload"] + f["dload"]) / 1e6
    assert m["throughput_mbps"] == pytest.approx(expected)
    assert m["latency_ms"] == pytest.approx(f["tcprtt"] * 1000)


# ---- metrics read / pagination ---------------------------------------
def test_get_metrics_pagination(client, device_id):
    rows = [{"device_id": device_id, "latency_ms": float(i)} for i in range(7)]
    client.post("/metrics", json={"detect": False, "rows": rows})
    page = client.get("/metrics", params={"device_id": device_id, "limit": 5, "offset": 5}).json()
    assert page["total"] == 7 and len(page["items"]) == 2 and page["offset"] == 5


def test_limit_above_max_is_rejected(client):
    r = client.get("/metrics", params={"limit": 501})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


# ---- /detect ---------------------------------------------------------
def test_detect_is_stateless(client, device_id):
    before = client.get("/metrics", params={"device_id": device_id}).json()["total"]
    out = client.post("/detect", json={"features": features(1)}).json()
    assert set(out) == {"is_anomaly", "score", "model_version"} and out["is_anomaly"]
    assert client.get("/metrics", params={"device_id": device_id}).json()["total"] == before


def test_detect_without_model_is_503(client):
    app.dependency_overrides[get_detector] = lambda: None
    r = client.post("/detect", json={"features": features(1)})
    assert r.status_code == 503 and r.json()["error"]["code"] == "MODEL_UNAVAILABLE"


# ---- errors ----------------------------------------------------------
def test_unknown_device_is_404(client):
    r = client.post("/metrics", json={"rows": [{"device_id": 999999, "features": features(1)}]})
    assert r.status_code == 404
    assert r.json()["error"] == {"code": "NOT_FOUND", "message": "Unknown device_id(s): [999999]"}


def test_missing_model_features_is_422(client, device_id):
    r = client.post("/metrics", json={"rows": [{"device_id": device_id, "features": {"dur": 1}}]})
    assert r.status_code == 422 and "missing" in r.json()["error"]["message"]


def test_unknown_incident_is_404(client):
    r = client.get("/incidents/999999")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_unknown_route_uses_error_body(client):
    assert client.get("/nope").json()["error"]["code"] == "NOT_FOUND"


def test_request_id_header(client):
    assert client.get("/health", headers={"X-Request-ID": "abc"}).headers["X-Request-ID"] == "abc"


# ---- incident updates / stats ----------------------------------------
def test_patch_incident_status(client, device_id):
    inc_id = post_attack(client, device_id).json()["incidents_touched"][0]["id"]
    body = client.patch(f"/incidents/{inc_id}", json={"status": "resolved", "summary": "fixed"}).json()
    assert body["status"] == "resolved" and body["summary"] == "fixed" and body["closed_at"]
    assert client.patch(f"/incidents/{inc_id}", json={"status": "bogus"}).status_code == 422


def test_quiet_incidents_are_closed(client, device_id):
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    inc_id = post_attack(client, device_id, ts=old).json()["incidents_touched"][0]["id"]
    post_attack(client, device_id)           # next ingest sweeps stale incidents
    assert client.get(f"/incidents/{inc_id}").json()["status"] == "closed"


def test_stats_summary_shape(client, device_id):
    post_attack(client, device_id)
    s = client.get("/stats/summary").json()
    assert s["devices"] >= 1 and s["open_incidents"] >= 1 and s["anomalies_24h"] >= 1
    assert set(s["incidents_by_severity"]) == {"low", "medium", "high", "critical"}
