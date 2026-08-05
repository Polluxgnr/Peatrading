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
_MODEL_PATH_TACTICAL = _ROOT / "database" / "xgboost_model_tactical.json"
_MODEL_PATH_STRUCTURAL = _ROOT / "database" / "xgboost_model_structural.json"
_METRICS_PATH = _ROOT / "database" / "ml_model_metrics.json"

FEATURE_COLS = [
    "rsi14",
    "zscore_50",
    "vol_20d_ann",
    "insider_net_score",
    "finnhub_roe",
    "finnhub_pe",
    "ev_to_ebitda",
    "news_sentiment",
    "earnings_qa_sentiment",
    "amf_short_interest",
    "amf_threshold_crossing",
    "ecb_euribor_3m",
    "gex_proxy",
    "frac_diff_04",
    "sp500_ret1d",
    "ndx_ret1d",
    "eurusd_ret1d",
    "oat_ret1d",
]
TARGET_TACTICAL = "target_tactical_30d"
TARGET_STRUCTURAL = "target_structural_126d"


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
    """Train XGBoost classifiers and persist models + metrics."""
    try:
        import xgboost as xgb
    except ImportError as exc:
        raise ImportError(
            "xgboost is required for ML training. pip install xgboost"
        ) from exc

    df = _load_dataset(dataset_path)
    logger.info("Loaded ML training dataset with shape: %s", df.shape)
    
    targets = [
        (TARGET_TACTICAL, model_path or _MODEL_PATH_TACTICAL, "tactical"),
        (TARGET_STRUCTURAL, _MODEL_PATH_STRUCTURAL, "structural")
    ]
    
    all_metrics = {}

    for target_col, out_path, key in targets:
        if target_col not in df.columns:
            logger.warning("Missing column in dataset: %s", target_col)
            continue

        work = df.dropna(subset=[target_col]).copy()
        if "created_at" in work.columns:
            work = work.sort_values("created_at")
        elif "Date" in work.columns:
            work = work.sort_values("Date")
        for col in FEATURE_COLS:
            work[col] = pd.to_numeric(work[col], errors="coerce")
        work = work.dropna(subset=FEATURE_COLS)
        if len(work) < 1000:
            logger.warning("Insufficient labeled rows for %s (%d < 1000). Need at least 1000 for robust training.", target_col, len(work))
            continue

        if target_col == TARGET_TACTICAL:
            y = (work[target_col] > 0.02).astype(int).values
        else:
            y = (work[target_col] > 0.08).astype(int).values
        X = work[FEATURE_COLS].values.astype(float)

        # Time-Series Split Cross-Validation to prevent lookahead bias
        from sklearn.model_selection import TimeSeriesSplit
        tscv = TimeSeriesSplit(n_splits=5)
        cv_metrics = []
        for train_index, test_index in tscv.split(X):
            embargo = 30
            tr_end = max(1, len(train_index) - embargo)
            X_tr, y_tr = X[train_index[:tr_end]], y[train_index[:tr_end]]
            X_te, y_te = X[test_index], y[test_index]
            
            if len(np.unique(y_tr)) < 2:
                continue
                
            cv_model = xgb.XGBClassifier(
                n_estimators=100, max_depth=4, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8, eval_metric="logloss",
                random_state=42
            )
            cv_model.fit(X_tr, y_tr)
            cv_metrics.append(cv_model.score(X_te, y_te))
            
        if cv_metrics:
            logger.info("[%s] TimeSeriesSplit CV Mean Accuracy: %.1f%%", key, float(np.mean(cv_metrics)) * 100)

        # Final production model on the traditional 80/20 chronological split
        split = int(len(work) * 0.8)
        embargo = 30
        train_end = max(1, split - embargo)
        
        X_train, X_test = X[:train_end], X[split:]
        y_train, y_test = y[:train_end], y[split:]

        model = xgb.XGBClassifier(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            eval_metric="logloss",
            random_state=42,
        )
        model.fit(X_train, y_train)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        model.save_model(str(out_path))

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
                logger.warning("[%s] Feature %s has very low importance (%.3f).", key, f_name, f_weight)

        logger.info("[%s] Model saved to %s (accuracy=%.1f%%)", key, out_path, metrics.get("accuracy_pct", 0))
        all_metrics[key] = metrics

        # ---------------------------------------------------------------------
        # Meta-Labeling & Unsupervised ML Pipeline
        # ---------------------------------------------------------------------
        if key == "tactical":
            # 1. Isolation Forest for Structural Anomalies (Black Swans)
            try:
                import joblib
                from sklearn.ensemble import IsolationForest
                
                iso_model = IsolationForest(contamination=0.015, random_state=42)
                iso_model.fit(X_train)
                
                iso_path = _ROOT / "database" / "isolation_forest.joblib"
                joblib.dump(iso_model, iso_path)
                logger.info("[unsupervised] Isolation Forest trained with 1.5%% contamination.")
            except ImportError:
                logger.warning("scikit-learn required for Isolation Forest. pip install scikit-learn")
                
            # 2. XGBoost Meta-Labeling
            preds_train = model.predict(X_train)
            meta_y_train = (preds_train == y_train).astype(int)
            
            meta_model = xgb.XGBClassifier(
                n_estimators=50, max_depth=3, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8, eval_metric="logloss", random_state=42
            )
            meta_model.fit(X_train, meta_y_train)
            meta_path = _ROOT / "database" / "xgboost_meta_tactical.json"
            meta_model.save_model(str(meta_path))
            
            preds_test = model.predict(X_test)
            meta_y_test = (preds_test == y_test).astype(int)
            meta_metrics = evaluate_model(meta_model, X_test, meta_y_test)
            all_metrics["meta_tactical"] = meta_metrics
            logger.info("[meta_tactical] Meta-Labeling model saved to %s", meta_path)

    _METRICS_PATH.write_text(json.dumps(all_metrics, indent=2), encoding="utf-8")
    return all_metrics


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


def predict_probability(features: dict, horizon: str = "tactical") -> float | None:
    """Return ML probability for a single feature dict, or None if no model."""
    path = _MODEL_PATH_STRUCTURAL if horizon == "structural" else _MODEL_PATH_TACTICAL
    # Fallback to old path if tactical doesn't exist yet but old model does
    if not path.exists():
        old_path = _ROOT / "database" / "xgboost_model.json"
        if horizon == "tactical" and old_path.exists():
            path = old_path
        else:
            return None
    try:
        import xgboost as xgb

        model = xgb.XGBClassifier()
        model.load_model(str(path))
        row = [float(features.get(c, 0.0) or 0.0) for c in FEATURE_COLS]
        prob = float(model.predict_proba(np.array([row]))[0, 1])
        return prob if np.isfinite(prob) else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("ML predict failed: %s", exc)
        return None


def predict_probability_with_shap(feat_row: dict, horizon: str = "tactical") -> tuple[float | None, dict[str, float] | None]:
    """Inference for a single feature row, returning probability and SHAP breakdown."""
    try:
        path = _MODEL_PATH_STRUCTURAL if horizon == "structural" else _MODEL_PATH_TACTICAL
        if not path.exists():
            old_path = _ROOT / "database" / "xgboost_model.json"
            if horizon == "tactical" and old_path.exists():
                path = old_path
            else:
                return None, None
            
        import xgboost as xgb
        import shap
        
        bst = xgb.Booster()
        bst.load_model(str(path))
        
        # Prepare X
        x_arr = []
        for c in FEATURE_COLS:
            x_arr.append(feat_row.get(c, np.nan))
        x_mat = xgb.DMatrix(np.array([x_arr]), feature_names=FEATURE_COLS)
        
        proba = float(bst.predict(x_mat)[0])
        
        explainer = shap.TreeExplainer(bst)
        shap_vals = explainer.shap_values(x_mat)
        
        shap_dict = {feat: float(val) for feat, val in zip(FEATURE_COLS, shap_vals[0])}
        return proba, shap_dict
    except Exception as exc:
        logger.debug(f"predict_probability_with_shap failed: {exc}")
        return None, None


def predict_anomaly(features: dict) -> bool | None:
    """Return True if Isolation Forest flags this feature row as a structural anomaly."""
    path = _ROOT / "database" / "isolation_forest.joblib"
    if not path.exists():
        return None
    try:
        import joblib
        
        model = joblib.load(path)
        row = [float(features.get(c, 0.0) or 0.0) for c in FEATURE_COLS]
        
        # predict returns -1 for outliers, 1 for inliers
        pred = model.predict(np.array([row]))[0]
        return bool(pred == -1)
    except Exception as exc:
        logger.debug("Isolation Forest predict failed: %s", exc)
        return None


def predict_meta_probability(features: dict) -> float | None:
    """Return the meta-confidence probability (XGBoost predicting if primary model is right)."""
    path = _ROOT / "database" / "xgboost_meta_tactical.json"
    if not path.exists():
        return None
    try:
        import xgboost as xgb

        model = xgb.XGBClassifier()
        model.load_model(str(path))
        row = [float(features.get(c, 0.0) or 0.0) for c in FEATURE_COLS]
        prob = float(model.predict_proba(np.array([row]))[0, 1])
        return prob if np.isfinite(prob) else None
    except Exception as exc:
        logger.debug("Meta ML predict failed: %s", exc)
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    m = train_model()
    print(json.dumps(m, indent=2))
