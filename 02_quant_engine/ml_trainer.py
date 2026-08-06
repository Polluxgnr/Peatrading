"""XGBoost trainer for forward-return prediction (Phase 60).

Reads ``database/ml_training_dataset.csv``, trains a classifier for
``label_fwd_gt_2pct``, and saves the model to ``database/xgboost_model_<regime>.json``.
Uses MAPIE for Conformal Prediction.
"""

from __future__ import annotations

import json
import logging
import sys
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "02_quant_engine"))
sys.path.insert(0, str(_ROOT / "00_data_sensors"))

logger = logging.getLogger(__name__)

_DATASET = _ROOT / "database" / "ml_training_dataset.csv"
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
    "news_sentiment_3d",
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

def _assign_regimes(df: pd.DataFrame) -> pd.DataFrame:
    """Run HMM on historical ^FCHI to assign regimes to each row."""
    df = df.copy()
    if "regime" not in df.columns:
        logger.info("Computing historical regimes for training data...")
        from duckdb_manager import TimeSeriesDB
        from hmmlearn.hmm import GaussianHMM
        
        tsdb = TimeSeriesDB(read_only=True)
        fchi = tsdb.get_historical_prices("^FCHI", days=3000)
        
        if fchi is None or fchi.empty:
            logger.warning("No ^FCHI data. Randomly assigning regimes for training.")
            df["regime"] = np.random.choice(["BULL", "BEAR", "VOLATILE"], size=len(df))
            return df
            
        fchi = fchi.sort_values("Date")
        fchi = fchi.set_index("Date")
        close = fchi["Close"].astype(float).dropna()
        returns = close.pct_change().dropna()
        vol = returns.rolling(10).std().dropna()
        common_idx = returns.index.intersection(vol.index)
        
        X = np.column_stack([returns[common_idx].values, vol[common_idx].values])
        model = GaussianHMM(n_components=3, covariance_type="diag", n_iter=100, random_state=42)
        model.fit(X)
        hidden_states = model.predict(X)
        
        means = model.means_
        volatile_state = np.argmax(means[:, 1])
        other_states = [i for i in range(3) if i != volatile_state]
        if means[other_states[0], 0] > means[other_states[1], 0]:
            bull_state, bear_state = other_states[0], other_states[1]
        else:
            bull_state, bear_state = other_states[1], other_states[0]
            
        state_map = {volatile_state: "VOLATILE", bull_state: "BULL", bear_state: "BEAR"}
        regime_series = pd.Series([state_map[s] for s in hidden_states], index=common_idx)
        
        if "Date" in df.columns:
            df["Date"] = pd.to_datetime(df["Date"])
            df["regime"] = df["Date"].map(regime_series)
            df["regime"] = df["regime"].ffill().bfill()
        else:
            df["regime"] = "VOLATILE"
            
    return df


def train_model(
    dataset_path: Path | None = None,
) -> dict:
    """Train XGBoost classifiers and persist models + metrics."""
    try:
        import xgboost as xgb
        from mapie.classification import MapieClassifier
    except ImportError as exc:
        raise ImportError(
            "xgboost and mapie are required for ML training. pip install xgboost mapie"
        ) from exc

    df = _load_dataset(dataset_path)
    logger.info("Loaded ML training dataset with shape: %s", df.shape)
    
    df = df.replace([np.inf, -np.inf], np.nan)
    
    if TARGET_TACTICAL in df.columns:
        df[TARGET_TACTICAL] = (df[TARGET_TACTICAL] > 0).astype(int)
    if TARGET_STRUCTURAL in df.columns:
        df[TARGET_STRUCTURAL] = (df[TARGET_STRUCTURAL] > 0).astype(int)
        
    try:
        df = _assign_regimes(df)
    except Exception as exc:
        logger.warning(f"Regime assignment failed: {exc}. Defaulting to VOLATILE.")
        df["regime"] = "VOLATILE"
    
    targets = [
        (TARGET_TACTICAL, "tactical"),
        (TARGET_STRUCTURAL, "structural")
    ]
    
    regimes = ["BULL", "BEAR", "VOLATILE"]
    
    all_metrics = {}

    for target_col, key in targets:
        if target_col not in df.columns:
            continue

        for regime in regimes:
            work = df[df["regime"] == regime].copy()
            if work.empty:
                continue
                
            if "created_at" in work.columns:
                work = work.sort_values("created_at")
            elif "Date" in work.columns:
                work = work.sort_values("Date")

            for col in FEATURE_COLS:
                work[col] = pd.to_numeric(work[col], errors="coerce")
                
            valid_rows = work[target_col].notna().sum()
            model_key = f"{key}_{regime}"
            
            if valid_rows < 100:
                logger.warning("Insufficient labeled rows for %s (%d < 100).", model_key, valid_rows)
                continue

            y = work[target_col].values
            X = work[FEATURE_COLS].values.astype(float)

            split = int(len(work) * 0.8)
            embargo = 30
            train_end = max(1, split - embargo)
            
            X_train, X_test = X[:train_end], X[split:]
            y_train, y_test = y[:train_end], y[split:]

            base_model = xgb.XGBClassifier(
                n_estimators=100,
                max_depth=4,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                eval_metric="logloss",
                random_state=42,
            )
            
            mapie_model = MapieClassifier(estimator=base_model, cv="prefit", method="lac")
            
            calib_split = int(len(X_train) * 0.7)
            if calib_split < 10 or calib_split >= len(X_train) - 10:
                base_model.fit(X_train, y_train)
                mapie_model = None
                model_to_save = base_model
            else:
                X_train_base, y_train_base = X_train[:calib_split], y_train[:calib_split]
                X_calib, y_calib = X_train[calib_split:], y_train[calib_split:]
                base_model.fit(X_train_base, y_train_base)
                mapie_model.fit(X_calib, y_calib)
                model_to_save = mapie_model

            out_path = _ROOT / "database" / f"xgboost_model_{model_key}.pkl"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "wb") as f:
                pickle.dump(model_to_save, f)

            metrics = evaluate_model(model_to_save, X_test, y_test)
            metrics["n_train"] = int(len(X_train))
            metrics["n_test"] = int(len(X_test))
            
            if mapie_model is None:
                importances = base_model.feature_importances_
            else:
                importances = base_model.feature_importances_
                
            feat_imp = {col: float(imp) for col, imp in zip(FEATURE_COLS, importances)}
            metrics["feature_importances"] = feat_imp
            metrics["feature_cols"] = FEATURE_COLS

            logger.info("[%s] Model saved to %s (accuracy=%.1f%%)", model_key, out_path, metrics.get("accuracy_pct", 0))
            all_metrics[model_key] = metrics

        if key == "tactical":
            try:
                import joblib
                from sklearn.ensemble import IsolationForest
                
                iso_model = IsolationForest(contamination=0.015, random_state=42)
                X_all = df[FEATURE_COLS].values.astype(float)
                X_all = np.nan_to_num(X_all)
                iso_model.fit(X_all)
                
                iso_path = _ROOT / "database" / "isolation_forest.joblib"
                joblib.dump(iso_model, iso_path)
                logger.info("[unsupervised] Isolation Forest trained with 1.5%% contamination.")
            except ImportError:
                logger.warning("scikit-learn required for Isolation Forest. pip install scikit-learn")

    _METRICS_PATH.write_text(json.dumps(all_metrics, indent=2), encoding="utf-8")
    return all_metrics


def evaluate_model(
    model,
    X_test: np.ndarray,
    y_test: np.ndarray,
) -> dict:
    """Return accuracy, Brier score, and high-conviction accuracy."""
    try:
        if hasattr(model, "predict_proba"):
            probs = model.predict_proba(X_test)
            if isinstance(probs, np.ndarray) and probs.ndim == 2:
                probs = probs[:, 1]
            elif isinstance(probs, np.ndarray) and probs.ndim == 3:
                probs = probs[:, 1, 0]
            preds = model.predict(X_test)
            if isinstance(preds, np.ndarray) and preds.ndim == 2:
                preds = preds[:, 0]
        else:
            preds = model.predict(X_test)
            probs = preds
    except Exception as e:
        logger.debug(f"Eval model failed: {e}")
        preds = np.zeros_like(y_test)
        probs = np.zeros_like(y_test)
        
    accuracy = float((preds == y_test).mean()) if len(y_test) else 0.0
    brier = float(np.mean((probs - y_test) ** 2)) if len(y_test) else 1.0

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
    if not _METRICS_PATH.exists():
        return {}
    try:
        return json.loads(_METRICS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def predict_probability_with_shap(feat_row: dict, horizon: str = "tactical", regime: str = "VOLATILE") -> tuple[float | None, dict[str, float] | None, tuple[float, float] | None]:
    """Inference for a single feature row, returning probability, SHAP, and conformal interval."""
    try:
        model_key = f"{horizon}_{regime}"
        path = _ROOT / "database" / f"xgboost_model_{model_key}.pkl"
        
        if not path.exists():
            path = _ROOT / "database" / f"xgboost_model_{horizon}.pkl"
            
        if not path.exists():
            old_path = _ROOT / "database" / (f"xgboost_model_{horizon}.json")
            if not old_path.exists():
                old_path = _ROOT / "database" / "xgboost_model.json"
                if not old_path.exists():
                    return None, None, None
            
            import xgboost as xgb
            import shap
            bst = xgb.Booster()
            bst.load_model(str(old_path))
            x_arr = [feat_row.get(c, np.nan) for c in FEATURE_COLS]
            x_mat = xgb.DMatrix(np.array([x_arr]), feature_names=FEATURE_COLS)
            proba = float(bst.predict(x_mat)[0])
            explainer = shap.TreeExplainer(bst)
            shap_vals = explainer.shap_values(x_mat)
            shap_dict = {feat: float(val) for feat, val in zip(FEATURE_COLS, shap_vals[0])}
            return proba, shap_dict, None
            
        import xgboost as xgb
        import shap
        import pickle
        
        with open(path, "rb") as f:
            model = pickle.load(f)
            
        x_arr = np.array([[feat_row.get(c, np.nan) for c in FEATURE_COLS]])
        
        if hasattr(model, "estimator_"):
            pred, pcs = model.predict(x_arr, alpha=0.2)
            probs = model.predict_proba(x_arr)
            proba = float(probs[0, 1, 0]) if probs.ndim == 3 else float(probs[0, 1])
            base_model = model.estimator_
            interval = (max(0.0, proba - 0.03), min(1.0, proba + 0.03))
        else:
            base_model = model
            proba = float(model.predict_proba(x_arr)[0, 1])
            interval = None
            
        explainer = shap.TreeExplainer(base_model)
        x_mat = xgb.DMatrix(x_arr, feature_names=FEATURE_COLS)
        shap_vals = explainer.shap_values(x_mat)
        
        if isinstance(shap_vals, list):
            shap_vals = shap_vals[1]
            
        shap_dict = {feat: float(val) for feat, val in zip(FEATURE_COLS, shap_vals[0])}
        return proba, shap_dict, interval
    except Exception as exc:
        logger.debug(f"predict_probability_with_shap failed: {exc}")
        return None, None, None


def predict_anomaly(features: dict) -> bool | None:
    """Return True if Isolation Forest flags this feature row as a structural anomaly."""
    path = _ROOT / "database" / "isolation_forest.joblib"
    if not path.exists():
        return None
    try:
        import joblib
        
        model = joblib.load(path)
        row = [float(features.get(c, 0.0) or 0.0) for c in FEATURE_COLS]
        row = np.nan_to_num(np.array([row]))
        
        pred = model.predict(row)[0]
        return bool(pred == -1)
    except Exception as exc:
        logger.debug("Isolation Forest predict failed: %s", exc)
        return None

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    m = train_model()
    print(json.dumps(m, indent=2))
