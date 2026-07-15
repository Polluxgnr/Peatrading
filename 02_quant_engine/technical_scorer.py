"""Quantitative signal engine for PEA Sniper Terminal V-Prime.

Reads OHLCV history from DuckDB, computes technical indicators via the
pandas-ta accessor, and emits raw ``Signal`` objects from purely mathematical
rules (Mean-Reversion Exhaustion).

This module is 100% math: no LLMs, no APIs, no risk/portfolio/broker logic.
"""

import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, List

import pandas as pd

# pandas-ta registers the ``.ta`` DataFrame accessor on import. The classic
# fork is used because upstream ``pandas_ta`` 0.4.x pulls in numba (no wheel
# for Python 3.13 / arm64) and 0.3.x breaks on numpy 2.x.
try:  # pragma: no cover - environment-dependent import.
    import pandas_ta as ta  # noqa: F401
except ImportError:  # pragma: no cover
    import pandas_ta_classic as ta  # noqa: F401

# 01_memory_core starts with a digit, so it is not a normal package. Add it to
# sys.path so the Phase 1 data contracts import regardless of launch context.
_CORE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "01_memory_core"
)
sys.path.insert(0, _CORE_DIR)

from data_models import Signal, SignalStatus, SignalType  # noqa: E402

logger = logging.getLogger(__name__)

# Minimum history required to compute a valid SMA-200.
_MIN_ROWS = 200


class SignalGenerator:
    """Generates raw BUY signals from mathematical price-action rules."""

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Attach SMA-50, SMA-200 and RSI-14 columns for a single ticker.

        Args:
            df: Chronologically-sorted OHLCV for ONE ticker. Must contain a
                ``Close`` column.

        Returns:
            pd.DataFrame: A copy of ``df`` with ``SMA_50``, ``SMA_200`` and
            ``RSI_14`` columns appended.
        """
        out = df.copy()
        close = out["Close"]
        out["SMA_50"] = out.ta.sma(close=close, length=50)
        out["SMA_200"] = out.ta.sma(close=close, length=200)
        out["RSI_14"] = out.ta.rsi(close=close, length=14)
        return out

    def score_rsi(self, rsi_value: float) -> float:
        """Map an RSI value to a BUY conviction score.

        Linear mapping in the oversold zone: RSI 30 -> 60, RSI 20 -> 80,
        RSI 10 -> 100 (clamped to [60, 100]).

        Args:
            rsi_value: The RSI(14) reading.

        Returns:
            float: Score in [60, 100] when ``rsi_value < 30``; otherwise 0.0.
            Returns 0.0 for NaN input.
        """
        if rsi_value is None or pd.isna(rsi_value):
            return 0.0
        if rsi_value >= 30:
            return 0.0
        score = 60.0 + (30.0 - rsi_value) * 2.0
        return float(max(60.0, min(100.0, score)))

    def generate_raw_signals(
        self, db_manager: Any, tickers: List[str]
    ) -> List[Signal]:
        """Evaluate each ticker and emit raw Mean-Reversion Exhaustion signals.

        Rule (BUY): the most recent bar has ``Close > SMA_200`` (long-term
        uptrend) AND ``RSI_14 < 30`` (short-term oversold pullback).

        Args:
            db_manager: A Phase 2 ``TimeSeriesDB`` exposing
                ``get_historical_prices(ticker, days)``.
            tickers: Ticker symbols to evaluate.

        Returns:
            List[Signal]: PENDING BUY signals for tickers meeting the rule.
        """
        signals: List[Signal] = []

        for ticker in tickers:
            df = db_manager.get_historical_prices(ticker, days=252)
            if df is None or df.empty or len(df) < _MIN_ROWS:
                logger.debug(
                    "Skipping %s: insufficient history (%d rows).",
                    ticker,
                    0 if df is None else len(df),
                )
                continue

            enriched = self.calculate_indicators(df)
            last = enriched.iloc[-1]

            close = last["Close"]
            sma_200 = last["SMA_200"]
            rsi_14 = last["RSI_14"]

            if pd.isna(sma_200) or pd.isna(rsi_14):
                logger.debug("Skipping %s: indicators not yet warmed up.", ticker)
                continue

            uptrend = close > sma_200
            oversold = rsi_14 < 30

            if uptrend and oversold:
                score = self.score_rsi(rsi_14)
                signal = Signal(
                    id=str(uuid.uuid4()),
                    ticker=ticker,
                    signal_type=SignalType.BUY,
                    status=SignalStatus.PENDING,
                    score=score,
                    target_qty=None,
                    created_at=datetime.now(timezone.utc),
                    reason=(
                        f"RSI < 30 (Value: {rsi_14:.1f}) while Price > SMA200 "
                        f"({close:.2f} > {sma_200:.2f}). Mean-reversion setup."
                    ),
                )
                signals.append(signal)
                logger.info(
                    "BUY signal %s for %s (RSI=%.1f, score=%.1f).",
                    signal.id[:8],
                    ticker,
                    rsi_14,
                    score,
                )

        return signals


if __name__ == "__main__":
    import numpy as np

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Build a synthetic uptrend (Close > SMA200) that ends in a sharp, oversold
    # pullback (RSI_14 < 30) to prove the BUY rule fires.
    n = 260
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    base = np.linspace(100.0, 200.0, n)          # long-term uptrend
    close = base.copy()
    close[-8:] = close[-9] * np.array(           # abrupt final pullback
        [0.965, 0.945, 0.930, 0.918, 0.910, 0.905, 0.902, 0.900]
    )
    mock = pd.DataFrame(
        {
            "Ticker": "TEST.PA",
            "Date": dates,
            "Open": close,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": 1_000_000,
        }
    )

    class _MockDB:
        """Minimal stand-in for TimeSeriesDB returning the mock frame."""

        def get_historical_prices(self, ticker: str, days: int = 252) -> pd.DataFrame:
            return mock

    gen = SignalGenerator()

    enriched = gen.calculate_indicators(mock)
    last = enriched.iloc[-1]
    print(
        f"Last bar -> Close={last['Close']:.2f} "
        f"SMA200={last['SMA_200']:.2f} RSI14={last['RSI_14']:.2f}"
    )
    print("score_rsi checks:",
          gen.score_rsi(30), gen.score_rsi(20), gen.score_rsi(10),
          gen.score_rsi(35), gen.score_rsi(float("nan")))

    results = gen.generate_raw_signals(_MockDB(), ["TEST.PA"])
    print(f"\nGenerated {len(results)} signal(s):")
    for s in results:
        print(f"  {s.id[:8]} {s.ticker} {s.signal_type.value} "
              f"score={s.score:.1f} status={s.status.value}")
        print(f"  reason: {s.reason}")
