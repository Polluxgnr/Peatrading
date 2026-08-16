"""Hidden Markov Model (HMM) Market Regime Classifier for PEA Sniper Terminal.

Fits a 3-state Gaussian HMM on CAC 40 (^FCHI) daily returns & realized volatility:
  - State 0: BULL (Positive drift, low volatility)
  - State 1: BEAR (Negative drift, elevated volatility)
  - State 2: VOLATILE / TRANSITION (Zero/mixed drift, high volatility)

Fail-safe: defaults strictly to VOLATILE (never BULL) if data retrieval fails or history is insufficient.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Dict, Optional, Tuple, Union

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


class MarketRegimeState(str, Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    VOLATILE = "VOLATILE"


class HMMRegimeClassifier:
    """Classifies market regimes using Gaussian Hidden Markov Models."""

    def __init__(self, index_ticker: str = "^FCHI", n_states: int = 3) -> None:
        self.index_ticker = index_ticker
        self.n_states = n_states
        self.model = None

    def fit_and_predict(self, ohlcv_df: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
        """Fit HMM on index returns and return the current regime state and posterior probabilities.

        Returns:
            Dict[str, Any]: {
                "regime": "BULL" | "BEAR" | "VOLATILE",
                "confidence": float,
                "bull_prob": float,
                "bear_prob": float,
                "volatile_prob": float,
            }
        """
        # Fail-safe default
        default_res = {
            "regime": MarketRegimeState.VOLATILE.value,
            "confidence": 0.50,
            "bull_prob": 0.25,
            "bear_prob": 0.25,
            "volatile_prob": 0.50,
        }

        if ohlcv_df is None or ohlcv_df.empty:
            try:
                ohlcv_df = yf.download(self.index_ticker, period="2y", interval="1d", progress=False, auto_adjust=True)
                if isinstance(ohlcv_df.columns, pd.MultiIndex):
                    c = ohlcv_df["Close"]
                    ohlcv_df = pd.DataFrame({"Close": c.iloc[:, 0] if isinstance(c, pd.DataFrame) else c})
            except Exception as exc:  # noqa: BLE001
                logger.warning("HMM failed to fetch %s: %s; using fail-safe", self.index_ticker, exc)
                return default_res

        if ohlcv_df is None or ohlcv_df.empty or len(ohlcv_df) < 100:
            logger.warning("Insufficient history for HMM; using fail-safe")
            return default_res

        try:
            from hmmlearn.hmm import GaussianHMM
        except ImportError:
            logger.debug("hmmlearn not installed; using rule-based regime heuristic")
            return self._rule_based_fallback(ohlcv_df)

        try:
            close = ohlcv_df["Close"].dropna().astype(float)
            rets = close.pct_change().dropna()
            vol = rets.rolling(20).std().dropna()

            idx_common = rets.index.intersection(vol.index)
            X = np.column_stack([rets.loc[idx_common].values, vol.loc[idx_common].values])

            self.model = GaussianHMM(n_components=self.n_states, covariance_type="full", n_iter=100, random_state=42)
            self.model.fit(X)

            # Identify states by mean return
            means = self.model.means_[:, 0]
            bull_state_idx = int(np.argmax(means))
            bear_state_idx = int(np.argmin(means))
            all_indices = set(range(self.n_states))
            vol_state_idx = list(all_indices - {bull_state_idx, bear_state_idx})[0]

            # Predict current state probabilities
            posteriors = self.model.predict_proba(X[-1:])[0]
            cur_state_idx = int(np.argmax(posteriors))
            confidence = float(posteriors[cur_state_idx])

            bull_p = float(posteriors[bull_state_idx])
            bear_p = float(posteriors[bear_state_idx])
            vol_p = float(posteriors[vol_state_idx])

            if cur_state_idx == bull_state_idx:
                regime = MarketRegimeState.BULL
            elif cur_state_idx == bear_state_idx:
                regime = MarketRegimeState.BEAR
            else:
                regime = MarketRegimeState.VOLATILE

            logger.info(
                "HMM Regime on %s: %s (Prob: %.2f | Bull: %.2f, Bear: %.2f, Vol: %.2f)",
                self.index_ticker, regime.value, confidence, bull_p, bear_p, vol_p,
            )
            return {
                "regime": regime.value,
                "confidence": confidence,
                "bull_prob": bull_p,
                "bear_prob": bear_p,
                "volatile_prob": vol_p,
            }

        except Exception as exc:  # noqa: BLE001
            logger.warning("HMM fitting failed: %s; using fail-safe", exc)
            return default_res

    def _rule_based_fallback(self, ohlcv_df: pd.DataFrame) -> Dict[str, Any]:
        """Fallback regime detector when hmmlearn is offline."""
        close = ohlcv_df["Close"].dropna().astype(float)
        cur = float(close.iloc[-1])
        sma50 = float(close.tail(50).mean())
        sma200 = float(close.tail(200).mean())

        if cur > sma50 > sma200:
            return {
                "regime": MarketRegimeState.BULL.value,
                "confidence": 0.80,
                "bull_prob": 0.80,
                "bear_prob": 0.10,
                "volatile_prob": 0.10,
            }
        elif cur < sma50 < sma200:
            return {
                "regime": MarketRegimeState.BEAR.value,
                "confidence": 0.80,
                "bull_prob": 0.10,
                "bear_prob": 0.80,
                "volatile_prob": 0.10,
            }
        else:
            return {
                "regime": MarketRegimeState.VOLATILE.value,
                "confidence": 0.65,
                "bull_prob": 0.20,
                "bear_prob": 0.20,
                "volatile_prob": 0.60,
            }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    clf = HMMRegimeClassifier()
    res = clf.fit_and_predict()
    print(f"Market Regime: {res['regime']} (Confidence: {res['confidence']:.2f})", res)
