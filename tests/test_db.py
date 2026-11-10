"""Database layer tests.

Pure-Python tests always run. Tests marked ``db`` need a reachable
PostgreSQL with db/schema.sql applied (python -m backend.db.init_db);
they are skipped otherwise. Each DB test runs inside a transaction that
is rolled back, so it never leaves data behind.
"""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.db import crud
from backend.db.load_dataset import build_metric_rows
from simulator.scenarios import generate_cpu, generate_interface_util

END = datetime(2026, 1, 15, 12, tzinfo=timezone.utc)
DEVICES = [{"id": 1, "link_capacity_mbps": 1000}, {"id": 2, "link_capacity_mbps": 100}]


def _raw(n=20):
    rng = np.random.default_rng(1)
    return pd.DataFrame(
        {
            "id": np.arange(n), "proto": ["tcp", "udp"] * (n // 2), "service": "-",
            "state": "FIN", "tcprtt": [0.1, 0.0] * (n // 2), "sjit": 1.0, "djit": 3.0,
            "sloss": 1, "dloss": 0, "spkts": 10, "dpkts": 0,
            "sload": rng.uniform(0, 5e8, n), "dload": 0.0,
            "attack_cat": "Normal", "label": [0, 1] * (n // 2),
        }
    )


# ---- config ----------------------------------------------------------
def test_empty_threshold_env_means_use_model_meta(monkeypatch):
    monkeypatch.setenv("ANOMALY_THRESHOLD", "")
    assert Settings(_env_file=None).anomaly_threshold is None


# ---- synthetic telemetry ---------------------------------------------
def test_interface_util_clipped_and_zero_capacity_safe():
    out = generate_interface_util([50, 5000, 10], [100, 100, 0])
    assert out.tolist() == [50.0, 100.0, 0.0]


def test_cpu_in_range_and_higher_for_attacks():
    rng = np.random.default_rng(0)
    tp = np.full(2000, 10.0)
    normal = generate_cpu(tp, np.zeros(2000), rng)
    attack = generate_cpu(tp, np.ones(2000), rng)
    assert normal.min() >= 0 and attack.max() <= 100
    assert attack.mean() > normal.mean() + 15


# ---- loader row building ---------------------------------------------
def test_build_metric_rows_shapes_rows_for_the_table():
    rows = build_metric_rows(_raw(), DEVICES, END, days=1, rng=np.random.default_rng(0))
    assert len(rows) == 20
    r = rows[0]
    assert {"device_id", "ts", "latency_ms", "cpu_usage_pct", "features", "true_label"} <= set(r)
    assert "label" not in r["features"] and "attack_cat" not in r["features"]
    assert {r["device_id"] for r in rows} <= {1, 2}


def test_build_metric_rows_time_window_and_order():
    rows = build_metric_rows(_raw(), DEVICES, END, days=1, rng=np.random.default_rng(0))
    ts = [r["ts"] for r in rows]
    assert ts == sorted(ts)
    assert END - timedelta(days=1) <= ts[0] and ts[-1] <= END


def test_build_metric_rows_nan_latency_becomes_none():
    rows = build_metric_rows(_raw(), DEVICES, END, days=1, rng=np.random.default_rng(0))
    udp = [r for r in rows if r["features"]["proto"] == "udp"]
    assert udp and all(r["latency_ms"] is None for r in udp)


def test_interface_util_uses_each_devices_capacity():
    rows = build_metric_rows(_raw(), DEVICES, END, days=1, rng=np.random.default_rng(0))
    cap = {1: 1000, 2: 100}
    for r in rows:
        expected = min(100.0, r["throughput_mbps"] / cap[r["device_id"]] * 100)
        assert r["interface_utilization_pct"] == pytest.approx(expected)


# ---- database (integration) ------------------------------------------
@pytest.fixture
def session():
    from backend.db.session import engine

    try:
        conn = engine.connect()
    except Exception as exc:  # no server or bad credentials
        pytest.skip(f"PostgreSQL not available: {exc.__class__.__name__}")
    # Open the outer transaction first: any query (even the probe below)
    # would otherwise autobegin one and make conn.begin() fail.
    trans = conn.begin()
    try:
        conn.execute(text("SELECT 1 FROM devices LIMIT 1"))
    except Exception as exc:  # schema not applied
        trans.rollback()
        conn.close()
        pytest.skip(f"Schema not applied: {exc.__class__.__name__}")
    s = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield s
    finally:
        s.close()
        trans.rollback()
        conn.close()


@pytest.mark.db
def test_insert_metrics_score_and_query(session):
    crud.upsert_devices(
        session,
        [{"hostname": "test-dev-a", "device_type": "router", "link_capacity_mbps": 1000}],
    )
    dev = next(d for d in crud.list_devices(session) if d.hostname == "test-dev-a")
    devices = [{"id": dev.id, "link_capacity_mbps": dev.link_capacity_mbps}]
    rows = build_metric_rows(_raw(10), devices, END, days=1, rng=np.random.default_rng(0))
    crud.bulk_insert_metrics(session, rows)

    items, total = crud.get_metrics(session, device_id=dev.id, limit=5)
    assert total == 10 and len(items) == 5
    assert items[0].ts >= items[-1].ts                    # newest first

    pending = [r for r in crud.unscored_metrics(session, 100_000) if r[1] == dev.id]
    assert len(pending) == 10

    crud.bulk_insert_anomalies(
        session,
        [
            {"metric_id": mid, "device_id": did, "detected_at": ts, "score": 0.9,
             "is_anomaly": True, "model_version": "test"}
            for mid, did, ts, _ in pending
        ],
    )
    assert not [r for r in crud.unscored_metrics(session, 100_000) if r[1] == dev.id]

    per_hour = [r for r in crud.anomalies_per_device_per_hour(session) if r["hostname"] == "test-dev-a"]
    assert sum(r["anomalies"] for r in per_hour) == 10


@pytest.mark.db
def test_upsert_devices_is_idempotent(session):
    dev = {"hostname": "test-dev-b", "device_type": "switch", "link_capacity_mbps": 1000}
    assert crud.upsert_devices(session, [dev]) == 1
    assert crud.upsert_devices(session, [dev]) == 0
