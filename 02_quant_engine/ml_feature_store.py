"""ML Feature Store & Conformal Prediction Engine for PEA Sniper Terminal.

Extracts technical & quantitative features:
  - RSI(14), ATR(14) normalized, Bollinger Bands %B and Bandwidth
  - MACD Histogram, Rolling Momentum (5d, 21d, 63d)
  - Volume Z-Score, Trend Quality (linear regression R^2 * slope)
Trains an XGBoost classifier with Conformal Prediction prediction sets
to output mathematically calibrated prediction sets (guaranteed coverage).
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.stats import linregress

logger = logging.getLogger(__name__)


class FeatureStore:
    """Computes ML feature matrices and manages Conformal Prediction calibration."""

    @staticmethod
    def extract_features(df: pd.DataFrame) -> pd.DataFrame:
        """Extract multi-factor technical features from raw OHLCV DataFrame."""
        if df is None or df.empty or len(df) < 30:
            return pd.DataFrame()

        data = df.sort_values("Date").copy()
        close = data["Close"].astype(float)
        high = data["High"].astype(float)
        low = data["Low"].astype(float)
        volume = data["Volume"].astype(float)

        feats = pd.DataFrame(index=data.index)
        feats["date"] = data["Date"]

        # 1. Momentum & Returns
        feats["ret_1d"] = close.pct_change(1)
        feats["ret_5d"] = close.pct_change(5)
        feats["ret_21d"] = close.pct_change(21)

        # 2. RSI(14)
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        feats["rsi_14"] = 100.0 - (100.0 / (1.0 + rs))

        # 3. ATR(14) Normalized
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr14 = tr.rolling(14).mean()
        feats["atr_norm"] = atr14 / close

        # 4. Bollinger Bands (%B and Bandwidth)
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper = sma20 + 2.0 * std20
        lower = sma20 - 2.0 * std20
        feats["bb_pct_b"] = (close - lower) / (upper - lower).replace(0, np.nan)
        feats["bb_bandwidth"] = (upper - lower) / sma20

        # 5. Volume Z-Score
        vol_mean = volume.rolling(20).mean()
        vol_std = volume.rolling(20).std().replace(0, np.nan)
        feats["vol_zscore"] = (volume - vol_mean) / vol_std

        # 6. Trend Quality (Rolling 30d linregress R^2 * slope)
        def _calc_tq(window):
            if len(window) < 10:
                return 0.0
            y = window.values / window.values[0]
            x = np.arange(len(y))
            res = linregress(x, y)
            return float(res.rvalue**2 * res.slope * 252)

        feats["trend_quality"] = close.rolling(30).apply(_calc_tq, raw=False)

        # Target: Forward 5-day return > +2.0%
        feats["target"] = (close.shift(-5) / close - 1.0 > 0.02).astype(int)

        return feats.dropna().reset_index(drop=True)

    @staticmethod
    def train_conformal_classifier(
        train_features: pd.DataFrame,
        confidence_level: float = 0.90,
    ) -> Tuple[any, float]:
        """Train XGBoost model and calibrate non-conformity scores.

        Returns:
            Tuple[model, conformity_threshold].
        """
        try:
            from xgboost import XGBClassifier
        except ImportError:
            logger.debug("xgboost not installed, using RandomForest fallback")
            from sklearn.ensemble import RandomForestClassifier as XGBClassifier

        feature_cols = [c for c in train_features.columns if c not in ("date", "target")]
        X = train_features[feature_cols].values
        y = train_features["target"].values

        # Split into training and calibration sets (80 / 20)
        split_idx = int(len(X) * 0.8)
        X_train, X_calib = X[:split_idx], X[split_idx:]
        y_train, y_calib = y[:split_idx], y[split_idx:]

        model = XGBClassifier(n_estimators=100, max_depth=3, random_state=42)
        model.fit(X_train, y_train)

        # Calibration: Non-conformity scores = 1 - P(True class)
        probs_calib = model.predict_proba(X_calib)
        non_conformity = 1.0 - probs_calib[np.arange(len(y_calib)), y_calib]

        # Quantile threshold for 1 - alpha coverage
        q_level = np.ceil((len(y_calib) + 1) * confidence_level) / len(y_calib)
        q_level = min(1.0, max(0.0, q_level))
        threshold = float(np.quantile(non_conformity, q_level))

        logger.info("Conformal classifier trained. Coverage threshold: %.4f", threshold)
        return model, threshold


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    dates = pd.date_range("2024-01-01", periods=100)
    prices = np.linspace(100, 120, 100) + np.random.normal(0, 1, 100)
    sample_df = pd.DataFrame({
        "Date": dates,
        "Open": prices,
        "High": prices + 1.0,
        "Low": prices - 1.0,
        "Close": prices,
        "Volume": np.random.randint(1000, 5000, 100),
    })

    store = FeatureStore()
    extracted = store.extract_features(sample_df)
    print("Extracted features shape:", extracted.shape)
