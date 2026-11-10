import numpy as np
import pandas as pd
import pytest

from ml import predict, train

rng = np.random.default_rng(0)


def _flows(n, label):
    """Synthetic UNSW-NB15-shaped rows; attacks have much larger load and loss."""
    scale = 1 if label == 0 else 50
    return pd.DataFrame(
        {
            "id": np.arange(n),
            "proto": rng.choice(["tcp", "udp"], n),
            "service": rng.choice(["http", "dns", "-"], n),
            "state": rng.choice(["FIN", "CON", "INT"], n),
            "dur": rng.exponential(0.5, n),
            "spkts": rng.integers(1, 20, n) * scale,
            "dpkts": rng.integers(0, 20, n) * scale,
            "sload": rng.exponential(1e5, n) * scale,
            "dload": rng.exponential(1e4, n) * scale,
            "sloss": rng.integers(0, 2, n) * scale,
            "dloss": rng.integers(0, 2, n) * scale,
            "attack_cat": "Normal" if label == 0 else "DoS",
            "label": label,
        }
    )


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    train_df = pd.concat([_flows(400, 0), _flows(100, 1)], ignore_index=True)
    test_df = pd.concat([_flows(200, 0), _flows(50, 1)], ignore_index=True)
    model, meta = train.train(train_df, test_df, n_estimators=50)
    out = tmp_path_factory.mktemp("model")
    path = train.save(model, meta, out)
    return predict.Detector.load(path), meta, test_df


# ---- scoring helpers -------------------------------------------------
def test_normalise_clips_to_unit_range():
    out = predict.normalise(np.array([-1.0, 0.0, 0.5, 1.0, 2.0]), 0.0, 1.0)
    assert out.tolist() == [0.0, 0.0, 0.5, 1.0, 1.0]


def test_normalise_degenerate_range():
    assert predict.normalise(np.array([3.0]), 1.0, 1.0).tolist() == [0.0]


def test_choose_threshold_separates_perfectly_separable_scores():
    t = train.choose_threshold([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9])
    assert 0.2 < t <= 0.8


# ---- training output -------------------------------------------------
def test_meta_has_required_fields(trained):
    _, meta, _ = trained
    for key in ("model_version", "sklearn_version", "features", "score", "metrics"):
        assert key in meta
    assert 0.0 <= meta["score"]["threshold"] <= 1.0
    assert "label" not in meta["features"] and "attack_cat" not in meta["features"]


def test_detector_learns_obvious_attacks(trained):
    _, meta, _ = trained
    assert meta["metrics"]["test"]["roc_auc"] > 0.9


# ---- inference contract ----------------------------------------------
def test_detect_returns_contract(trained):
    det, meta, test_df = trained
    record = test_df.iloc[0].to_dict()
    out = det.detect(record)
    assert set(out) == {"is_anomaly", "score", "model_version"}
    assert isinstance(out["is_anomaly"], bool)
    assert 0.0 <= out["score"] <= 1.0
    assert out["model_version"] == meta["model_version"]


def test_detect_works_without_label_columns(trained):
    det, _, test_df = trained
    record = test_df.iloc[0].drop(["id", "label", "attack_cat"]).to_dict()
    assert "score" in det.detect(record)


def test_missing_feature_raises(trained):
    det, _, test_df = trained
    record = test_df.iloc[0].drop(["sload"]).to_dict()
    with pytest.raises(ValueError, match="sload"):
        det.detect(record)


def test_attack_scores_higher_than_normal_on_average(trained):
    det, _, test_df = trained
    scored = det.score_frame(test_df)
    by_label = scored["score"].groupby(test_df["label"]).mean()
    assert by_label[1] > by_label[0]


def test_env_threshold_overrides_meta(trained, monkeypatch):
    det, meta, _ = trained
    monkeypatch.setenv("ANOMALY_THRESHOLD", "0.99")
    assert predict.Detector(det.model, meta).threshold == 0.99


def test_missing_model_file_has_helpful_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="python -m ml.train"):
        predict.Detector.load(tmp_path / "model.joblib")
