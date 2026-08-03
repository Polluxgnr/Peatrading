"""XGBoost trainer for forward-return prediction (Phase 44).

Reads ``database/ml_training_dataset.csv``, trains a classifier for
``label_fwd_gt_2pct``, and saves the model to ``database/xgboost_model.json``.

Usage::

    python 02_quant_engine/ml_trainer.py
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "02_quant_engine"))

logger = logging.getLogger(__name__)

_DATASET = _ROOT / "database" / "ml_training_dataset.csv"
_MODEL_PATH = _ROOT / "database" / "xgboost_model.json"
_METRICS_PATH = _ROOT / "database" / "ml_model_metrics.json"

FEATURE_COLS = [
    "rsi14",
    "zscore_50",
    "vol_20d_ann",
    "insider_net_score",
    "finnhub_roe",
    "finnhub_pe",
    "news_sentiment",
    "amf_short_interest",
    "ecb_euribor_3m",
    "gex_proxy",
    "frac_diff_04",
    "sp500_ret1d",
    "ndx_ret1d",
    "eurusd_ret1d",
    "oat_ret1d",
]
TARGET_COL = "label_fwd_gt_2pct"


def _load_dataset(path: Path | None = None) -> pd.DataFrame:
    p = Path(path) if path else _DATASET
    if not p.exists():
        raise FileNotFoundError(f"Training dataset not found: {p}")
    df = pd.read_csv(p)
    if df.empty:
        raise ValueError("Training dataset is empty.")
    return df


def train_model(
    dataset_path: Path | None = None,
    model_path: Path | None = None,
) -> dict:
    """Train XGBoost classifier and persist model + metrics."""
    try:
        import xgboost as xgb
    except ImportError as exc:
        raise ImportError(
            "xgboost is required for ML training. pip install xgboost"
        ) from exc

    df = _load_dataset(dataset_path)
    for col in FEATURE_COLS + [TARGET_COL]:
        if col not in df.columns:
            raise ValueError(f"Missing column in dataset: {col}")

    work = df.dropna(subset=[TARGET_COL]).copy()
    if "created_at" in work.columns:
        work = work.sort_values("created_at")
    elif "Date" in work.columns:
        work = work.sort_values("Date")
    for col in FEATURE_COLS:
        work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.dropna(subset=FEATURE_COLS)
    if len(work) < 30:
        raise ValueError(f"Insufficient labeled rows ({len(work)} < 30).")

    y = work[TARGET_COL].astype(int).values
    X = work[FEATURE_COLS].values.astype(float)

    split = int(len(work) * 0.8)
    embargo = 30
    train_end = max(1, split - embargo)
    
    X_train, X_test = X[:train_end], X[split:]
    y_train, y_test = y[:train_end], y[split:]

    model = xgb.XGBClassifier(
        n_estimators=80,
        max_depth=4,
        learning_rate=0.08,
        subsample=0.8,
        colsample_bytree=0.8,
        eval_metric="logloss",
        random_state=42,
    )
    model.fit(X_train, y_train)

    out_model = model_path or _MODEL_PATH
    out_model.parent.mkdir(parents=True, exist_ok=True)
    model.save_model(str(out_model))

    metrics = evaluate_model(model, X_test, y_test, work.iloc[split:])
    metrics["n_train"] = int(len(X_train))
    metrics["n_test"] = int(len(X_test))
    
    # Auto Feature Selection: Track importance
    importances = model.feature_importances_
    feat_imp = {col: float(imp) for col, imp in zip(FEATURE_COLS, importances)}
    metrics["feature_importances"] = feat_imp
    metrics["feature_cols"] = FEATURE_COLS

    # Optional: Log warning if a feature's importance is near zero
    for f_name, f_weight in feat_imp.items():
        if f_weight < 0.01:
            logger.warning("Feature %s has very low importance (%.3f). Consider excluding it.", f_name, f_weight)

    _METRICS_PATH.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    logger.info("Model saved to %s (accuracy=%.1f%%)", out_model, metrics.get("accuracy_pct", 0))
    return metrics


def evaluate_model(
    model,
    X_test: np.ndarray,
    y_test: np.ndarray,
    test_df: pd.DataFrame | None = None,
) -> dict:
    """Return accuracy, Brier score, and high-conviction accuracy."""
    preds = model.predict(X_test)
    probs = model.predict_proba(X_test)[:, 1]
    accuracy = float((preds == y_test).mean()) if len(y_test) else 0.0
    brier = float(np.mean((probs - y_test) ** 2)) if len(y_test) else 1.0

    # Proxy "signals > 75": model probability >= 0.75
    high_mask = probs >= 0.75
    if high_mask.any():
        acc_high = float((preds[high_mask] == y_test[high_mask]).mean())
        n_high = int(high_mask.sum())
    else:
        acc_high = None
        n_high = 0

    return {
        "accuracy_pct": round(accuracy * 100, 1),
        "brier_score": round(brier, 4),
        "accuracy_signals_above_75_pct": round(acc_high * 100, 1) if acc_high is not None else None,
        "n_signals_above_75": n_high,
    }


def load_metrics() -> dict:
    """Load persisted metrics JSON (empty dict if missing)."""
    if not _METRICS_PATH.exists():
        return {}
    try:
        return json.loads(_METRICS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def predict_probability(features: dict) -> float | None:
    """Return ML probability for a single feature dict, or None if no model."""
    if not _MODEL_PATH.exists():
        return None
    try:
        import xgboost as xgb

        model = xgb.XGBClassifier()
        model.load_model(str(_MODEL_PATH))
        row = [float(features.get(c, 0.0) or 0.0) for c in FEATURE_COLS]
        prob = float(model.predict_proba(np.array([row]))[0, 1])
        return prob if np.isfinite(prob) else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("ML predict failed: %s", exc)
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    m = train_model()
    print(json.dumps(m, indent=2))
