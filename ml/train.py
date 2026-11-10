"""Train the Isolation Forest anomaly detector.

    python -m ml.train                      # train + evaluate, write ml/model.joblib + model_meta.json
    python -m ml.train --baseline           # also report a Random Forest baseline (not saved)

Protocol (docs/architecture.md section 8.2):
  1. Split the training CSV into fit / validation (stratified, seeded).
  2. Fit the Isolation Forest on NORMAL rows of the fit split only.
  3. Normalise scores to 0..1 using the raw-score range on the fit split.
  4. Choose the alert threshold that maximises F1 on the validation split.
  5. Report final metrics once, on the untouched testing CSV.
"""

from __future__ import annotations

import argparse
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.metrics import (
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

from ml.features import ATTACK_CAT_COL, build_preprocessor, split_xy
from ml.predict import normalise, raw_scores

RANDOM_STATE = 42


# --------------------------------------------------------------------
# Building blocks (unit-tested in tests/test_predict.py)
# --------------------------------------------------------------------
def fit_detector(
    X_normal: pd.DataFrame, n_estimators: int = 200, max_samples="auto"
) -> Pipeline:
    pipe = Pipeline(
        [
            ("pre", build_preprocessor(X_normal)),
            (
                "iforest",
                IsolationForest(
                    n_estimators=n_estimators,
                    max_samples=max_samples,
                    contamination="auto",
                    random_state=RANDOM_STATE,
                    n_jobs=-1,
                ),
            ),
        ]
    )
    return pipe.fit(X_normal)


def choose_threshold(y_true, scores) -> float:
    """Threshold on normalised scores that maximises F1."""
    precision, recall, thresholds = precision_recall_curve(y_true, scores)
    f1 = 2 * precision * recall / np.maximum(precision + recall, 1e-12)
    # precision/recall have one more element than thresholds
    best = int(np.argmax(f1[:-1]))
    return float(thresholds[best])


def evaluate(y_true, scores, threshold: float) -> dict:
    y_pred = (np.asarray(scores) >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    return {
        "precision": round(float(precision_score(y_true, y_pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y_true, y_pred, zero_division=0)), 4),
        "f1": round(float(f1_score(y_true, y_pred, zero_division=0)), 4),
        "roc_auc": round(float(roc_auc_score(y_true, scores)), 4),
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
    }


def detection_by_category(df: pd.DataFrame, y_pred) -> dict:
    """Share of attack flows flagged, per attack category."""
    attacks = df.assign(flagged=np.asarray(y_pred)).loc[df["label"] == 1]
    rates = attacks.groupby(ATTACK_CAT_COL)["flagged"].agg(["count", "mean"])
    return {
        cat: {"flows": int(r["count"]), "detection_rate": round(float(r["mean"]), 4)}
        for cat, r in rates.sort_values("mean").iterrows()
    }


def train(
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    n_estimators: int = 200,
    max_samples="auto",
    val_size: float = 0.2,
) -> tuple[Pipeline, dict]:
    """Run the full protocol. Returns the fitted pipeline and its metadata."""
    fit_df, val_df = train_test_split(
        train_df, test_size=val_size, stratify=train_df["label"], random_state=RANDOM_STATE
    )
    X_fit, y_fit = split_xy(fit_df)
    X_val, y_val = split_xy(val_df)
    X_test, y_test = split_xy(test_df)
    features = list(X_fit.columns)

    model = fit_detector(X_fit[y_fit == 0], n_estimators, max_samples)

    raw_fit = raw_scores(model, X_fit)
    raw_min, raw_max = float(raw_fit.min()), float(raw_fit.max())

    val_scores = normalise(raw_scores(model, X_val[features]), raw_min, raw_max)
    threshold = choose_threshold(y_val, val_scores)

    test_scores = normalise(raw_scores(model, X_test[features]), raw_min, raw_max)
    trained_at = datetime.now(timezone.utc)

    meta = {
        "model_version": f"iforest-{trained_at:%Y%m%d-%H%M%S}",
        "trained_at": trained_at.isoformat(timespec="seconds"),
        "sklearn_version": sklearn.__version__,
        "python_version": platform.python_version(),
        "params": {
            "n_estimators": n_estimators,
            "max_samples": max_samples,
            "random_state": RANDOM_STATE,
            "fit_rows_normal": int((y_fit == 0).sum()),
            "val_size": val_size,
        },
        "features": features,
        "score": {"raw_min": raw_min, "raw_max": raw_max, "threshold": round(threshold, 6)},
        "metrics": {
            "validation": evaluate(y_val, val_scores, threshold),
            "test": evaluate(y_test, test_scores, threshold),
        },
        "test_detection_by_attack_cat": detection_by_category(
            test_df, test_scores >= threshold
        ),
    }
    return model, meta


def random_forest_baseline(train_df: pd.DataFrame, test_df: pd.DataFrame) -> dict:
    """Supervised reference point: how much would labels buy us?"""
    X, y = split_xy(train_df)
    X_test, y_test = split_xy(test_df)
    rf = Pipeline(
        [
            ("pre", build_preprocessor(X)),
            (
                "rf",
                RandomForestClassifier(
                    n_estimators=200,
                    class_weight="balanced",
                    random_state=RANDOM_STATE,
                    n_jobs=-1,
                ),
            ),
        ]
    ).fit(X, y)
    proba = rf.predict_proba(X_test[X.columns])[:, 1]
    return evaluate(y_test, proba, 0.5)


def save(model: Pipeline, meta: dict, out_dir: str | Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / "model.joblib"
    joblib.dump(model, model_path)
    (out_dir / "model_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return model_path


# --------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------
def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Train the NetDiagnose anomaly detector.")
    p.add_argument("--train", default="data/raw/UNSW_NB15_training-set.csv")
    p.add_argument("--test", default="data/raw/UNSW_NB15_testing-set.csv")
    p.add_argument("--out", default="ml", help="directory for model.joblib + model_meta.json")
    p.add_argument("--n-estimators", type=int, default=200)
    p.add_argument("--max-samples", default="auto", help="'auto', an int, or a float fraction")
    p.add_argument("--baseline", action="store_true", help="also train a Random Forest baseline")
    args = p.parse_args(argv)

    max_samples = args.max_samples
    if max_samples != "auto":
        max_samples = float(max_samples) if "." in max_samples else int(max_samples)

    train_df = pd.read_csv(args.train)
    test_df = pd.read_csv(args.test)

    model, meta = train(train_df, test_df, args.n_estimators, max_samples)
    if args.baseline:
        meta["baseline_random_forest_test"] = random_forest_baseline(train_df, test_df)

    path = save(model, meta, args.out)
    print(f"Saved {path} ({meta['model_version']})")
    print(f"Threshold (max F1 on validation): {meta['score']['threshold']:.4f}")
    for split, m in meta["metrics"].items():
        print(f"{split:>10}: P={m['precision']} R={m['recall']} F1={m['f1']} AUC={m['roc_auc']}")
    if "baseline_random_forest_test" in meta:
        m = meta["baseline_random_forest_test"]
        print(f"  RF test: P={m['precision']} R={m['recall']} F1={m['f1']} AUC={m['roc_auc']}")
    print("Detection rate by attack category (test):")
    for cat, r in meta["test_detection_by_attack_cat"].items():
        print(f"  {cat:<15} {r['detection_rate']:.1%}  ({r['flows']} flows)")


if __name__ == "__main__":
    main()
