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
from typing import Optional, Tuple

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

    def fit_and_predict(self, ohlcv_df: Optional[pd.DataFrame] = None) -> Tuple[MarketRegimeState, float]:
        """Fit HMM on index returns and return the current regime state and posterior probability.

        Returns:
            Tuple[MarketRegimeState, float]: (Current regime, Confidence probability).
        """
        # Fail-safe default
        default_state = MarketRegimeState.VOLATILE
        default_prob = 0.50

        if ohlcv_df is None or ohlcv_df.empty:
            try:
                ohlcv_df = yf.download(self.index_ticker, period="2y", interval="1d", progress=False, auto_adjust=True)
                if isinstance(ohlcv_df.columns, pd.MultiIndex):
                    c = ohlcv_df["Close"]
                    ohlcv_df = pd.DataFrame({"Close": c.iloc[:, 0] if isinstance(c, pd.DataFrame) else c})
            except Exception as exc:  # noqa: BLE001
                logger.warning("HMM failed to fetch %s: %s; using fail-safe %s", self.index_ticker, exc, default_state)
                return default_state, default_prob

        if ohlcv_df is None or ohlcv_df.empty or len(ohlcv_df) < 100:
            logger.warning("Insufficient history for HMM; using fail-safe %s", default_state)
            return default_state, default_prob

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
            # The remaining is volatile
            all_indices = set(range(self.n_states))
            vol_state_idx = list(all_indices - {bull_state_idx, bear_state_idx})[0]

            # Predict current state
            posteriors = self.model.predict_proba(X[-1:])
            cur_state_idx = int(np.argmax(posteriors[0]))
            confidence = float(posteriors[0][cur_state_idx])

            if cur_state_idx == bull_state_idx:
                regime = MarketRegimeState.BULL
            elif cur_state_idx == bear_state_idx:
                regime = MarketRegimeState.BEAR
            else:
                regime = MarketRegimeState.VOLATILE

            logger.info("HMM Regime on %s: %s (Prob: %.2f)", self.index_ticker, regime.value, confidence)
            return regime, confidence

        except Exception as exc:  # noqa: BLE001
            logger.warning("HMM fitting failed: %s; using fail-safe %s", exc, default_state)
            return default_state, default_prob

    def _rule_based_fallback(self, ohlcv_df: pd.DataFrame) -> Tuple[MarketRegimeState, float]:
        """Fallback regime detector when hmmlearn is offline."""
        close = ohlcv_df["Close"].dropna().astype(float)
        cur = float(close.iloc[-1])
        sma50 = float(close.tail(50).mean())
        sma200 = float(close.tail(200).mean())

        if cur > sma50 > sma200:
            return MarketRegimeState.BULL, 0.80
        elif cur < sma50 < sma200:
            return MarketRegimeState.BEAR, 0.80
        else:
            return MarketRegimeState.VOLATILE, 0.65


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    clf = HMMRegimeClassifier()
    reg, conf = clf.fit_and_predict()
    print(f"Market Regime: {reg.value} (Confidence: {conf:.2f})")
