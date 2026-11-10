import math

import numpy as np
import pandas as pd
import pytest

from ml import features as f


def _row(**overrides):
    base = {
        "id": 1, "proto": "tcp", "service": "http", "state": "FIN",
        "dur": 0.5, "spkts": 10, "dpkts": 10, "sbytes": 1000, "dbytes": 2000,
        "sload": 4_000_000.0, "dload": 1_000_000.0, "sloss": 1, "dloss": 1,
        "sjit": 10.0, "djit": 30.0, "tcprtt": 0.25, "synack": 0.1, "ackdat": 0.15,
        "attack_cat": "Normal", "label": 0,
    }
    base.update(overrides)
    return base


# ---- latency ---------------------------------------------------------
def test_latency_converts_seconds_to_ms():
    assert f.latency_ms(0.245) == pytest.approx(245.0)


def test_latency_is_nan_when_not_measured():
    # non-TCP flows have tcprtt == 0
    assert math.isnan(f.latency_ms(0.0))


# ---- jitter ----------------------------------------------------------
def test_jitter_is_mean_of_both_directions():
    assert f.jitter_ms(10.0, 30.0) == pytest.approx(20.0)


# ---- packet loss -----------------------------------------------------
def test_packet_loss_basic():
    assert f.packet_loss_pct(1, 1, 10, 10) == pytest.approx(10.0)


def test_packet_loss_zero_packets_is_zero_not_error():
    assert f.packet_loss_pct(0, 0, 0, 0) == 0.0


def test_packet_loss_clipped_to_100():
    # 12 rows in the training set report more lost than sent packets
    assert f.packet_loss_pct(50, 0, 10, 0) == 100.0


# ---- throughput ------------------------------------------------------
def test_throughput_bits_to_megabits():
    assert f.throughput_mbps(4_000_000, 1_000_000) == pytest.approx(5.0)


# ---- vectorised use --------------------------------------------------
def test_functions_accept_series_and_keep_index():
    s = pd.Series([0.0, 0.1], index=[7, 9])
    out = f.latency_ms(s)
    assert list(out.index) == [7, 9]
    assert math.isnan(out[7]) and out[9] == pytest.approx(100.0)


def test_add_metrics_adds_columns_without_mutating_input():
    df = pd.DataFrame([_row(), _row(proto="udp", tcprtt=0.0, spkts=0, dpkts=0, sloss=0, dloss=0)])
    before = df.copy()
    out = f.add_metrics(df)

    pd.testing.assert_frame_equal(df, before)
    assert set(f.METRIC_COLS) <= set(out.columns)
    assert out.loc[0, "latency_ms"] == pytest.approx(250.0)
    assert out.loc[0, "packet_loss_pct"] == pytest.approx(10.0)
    assert out.loc[0, "throughput_mbps"] == pytest.approx(5.0)
    assert np.isnan(out.loc[1, "latency_ms"])
    assert out.loc[1, "packet_loss_pct"] == 0.0


# ---- model input -----------------------------------------------------
def test_split_xy_drops_non_features():
    X, y = f.split_xy(pd.DataFrame([_row()]))
    assert not set(f.NON_FEATURE_COLS) & set(X.columns)
    assert y.tolist() == [0]


def test_split_xy_without_label():
    X, y = f.split_xy(pd.DataFrame([_row()]).drop(columns=["label"]))
    assert y is None and "attack_cat" not in X.columns


def test_preprocessor_ignores_unknown_categories():
    train = pd.DataFrame([_row(), _row(proto="udp", state="CON")])
    X, _ = f.split_xy(train)
    pre = f.build_preprocessor(X).fit(X)

    unseen, _ = f.split_xy(pd.DataFrame([_row(proto="sctp", service="ftp")]))
    out = pre.transform(unseen)
    assert out.shape == (1, pre.transform(X).shape[1])
