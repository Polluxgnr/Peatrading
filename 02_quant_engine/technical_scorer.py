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
from functools import lru_cache
from pathlib import Path
from typing import Any, List

import pandas as pd
import yaml

try:  # yfinance is only needed for the optional Quality (EPS) filter.
    import yfinance as yf
except Exception:  # noqa: BLE001 - keep the pure-math engine importable offline.
    yf = None  # type: ignore[assignment]

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

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_DIR = _PROJECT_ROOT / "config"

# Minimum history required to compute a valid SMA-200.
_MIN_ROWS = 200
_DEFAULT_RSI_OVERSOLD = 30.0


class SignalGenerator:
    """Generates raw BUY signals from mathematical price-action rules."""

    def __init__(self, config_path: str | Path | None = None) -> None:
        """Load optional thresholds from ``risk_params.yaml``."""
        path = Path(config_path) if config_path else _DEFAULT_CONFIG_DIR
        risk_file = path if path.is_file() else path / "risk_params.yaml"
        risk: dict = {}
        if risk_file.exists():
            with open(risk_file, "r", encoding="utf-8") as fh:
                risk = yaml.safe_load(fh) or {}
        self.rsi_oversold: float = float(
            risk.get("RSI_OVERSOLD_THRESHOLD", _DEFAULT_RSI_OVERSOLD)
        )

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
        out["SMA_5"] = out.ta.sma(close=close, length=5)
        out["SMA_50"] = out.ta.sma(close=close, length=50)
        out["SMA_200"] = out.ta.sma(close=close, length=200)
        out["RSI_14"] = out.ta.rsi(close=close, length=14)
        return out

    def score_rsi(self, rsi_value: float) -> float:
        """Map an RSI value to a BUY conviction score.

        Linear mapping in the oversold zone relative to ``rsi_oversold``.
        """
        thr = self.rsi_oversold
        if rsi_value is None or pd.isna(rsi_value):
            return 0.0
        if rsi_value >= thr:
            return 0.0
        score = 60.0 + (thr - rsi_value) * 2.0
        return float(max(60.0, min(100.0, score)))

    @staticmethod
    @lru_cache(maxsize=512)
    def _trailing_eps(ticker: str) -> float | None:
        """Return trailing EPS for a ticker via yfinance (cached, tolerant).

        Args:
            ticker: Yahoo Finance ticker symbol.

        Returns:
            float | None: Trailing EPS, or ``None`` if it cannot be determined
            (network error, missing field). ``None`` means "unknown -> allow".
        """
        if yf is None:
            return None
        try:
            info = yf.Ticker(ticker).info or {}
            for key in ("trailingEps", "epsTrailingTwelveMonths"):
                val = info.get(key)
                if val is not None:
                    return float(val)
        except Exception:  # noqa: BLE001 - never block sizing on a data outage.
            logger.debug("EPS lookup failed for %s; treating as unknown.", ticker)
        return None

    def is_profitable(self, ticker: str) -> bool:
        """Quality filter: reject loss-making names (EPS < 0).

        Unknown EPS (data unavailable) is treated as pass, so a data outage
        never silently blocks the whole universe.

        Args:
            ticker: Ticker to check.

        Returns:
            bool: ``False`` only when EPS is known and negative.
        """
        eps = self._trailing_eps(ticker)
        if eps is None:
            return True
        return eps > 0

    def generate_raw_signals(
        self,
        db_manager: Any,
        tickers: List[str],
        apply_quality_filter: bool = True,
        apply_momentum_filter: bool = True,
    ) -> List[Signal]:
        """Evaluate each ticker and emit raw Mean-Reversion Exhaustion signals.

        Rule (BUY): the most recent bar has ``Close > SMA_200`` (long-term
        uptrend) AND ``RSI_14 < RSI_OVERSOLD_THRESHOLD`` (default 30), refined by:

          * Quality filter (Phase 11): the company must be profitable (EPS > 0).
          * Momentum filter (Phase 11): do not catch falling knives — require
            ``Close > SMA_5`` so the pullback is already stabilizing.

        Args:
            db_manager: A Phase 2 ``TimeSeriesDB`` exposing
                ``get_historical_prices(ticker, days)``.
            tickers: Ticker symbols to evaluate.
            apply_quality_filter: Skip loss-making companies when ``True``.
            apply_momentum_filter: Require ``Close > SMA_5`` when ``True``.

        Returns:
            List[Signal]: PENDING BUY signals for tickers meeting all rules.
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
            sma_5 = last["SMA_5"]
            sma_200 = last["SMA_200"]
            rsi_14 = last["RSI_14"]

            if pd.isna(sma_200) or pd.isna(rsi_14):
                logger.debug("Skipping %s: indicators not yet warmed up.", ticker)
                continue

            uptrend = close > sma_200
            oversold = rsi_14 < self.rsi_oversold

            # --- Momentum filter: reject falling knives (Close <= SMA_5) ------
            if apply_momentum_filter and (pd.isna(sma_5) or close <= sma_5):
                if uptrend and oversold:
                    logger.info(
                        "Momentum filter blocked %s (Close %.2f <= SMA5 %.2f).",
                        ticker,
                        close,
                        sma_5,
                    )
                continue

            # --- Quality filter: reject loss-making hype stocks (EPS < 0) -----
            if uptrend and oversold and apply_quality_filter and not self.is_profitable(
                ticker
            ):
                logger.info("Quality filter blocked %s (EPS < 0).", ticker)
                continue

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
                        f"RSI < {self.rsi_oversold:.0f} (Value: {rsi_14:.1f}) while Price > SMA200 "
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

    # Build a synthetic uptrend (Close > SMA200) that dips into an oversold
    # pullback (RSI_14 < 30) and then STABILISES (Close > SMA_5) so both the
    # mean-reversion rule and the new momentum filter fire together.
    n = 260
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    base = np.linspace(100.0, 200.0, n)          # long-term uptrend
    close = base.copy()
    close[-8:] = close[-9] * np.array(           # deep dip, then a 2-bar bounce
        [0.955, 0.925, 0.898, 0.875, 0.858, 0.848, 0.858, 0.866]
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
        f"Last bar -> Close={last['Close']:.2f} SMA5={last['SMA_5']:.2f} "
        f"SMA200={last['SMA_200']:.2f} RSI14={last['RSI_14']:.2f}"
    )
    print("score_rsi checks:",
          gen.score_rsi(30), gen.score_rsi(20), gen.score_rsi(10),
          gen.score_rsi(35), gen.score_rsi(float("nan")))

    # Quality filter needs network EPS; disable it for this offline demo.
    results = gen.generate_raw_signals(
        _MockDB(), ["TEST.PA"], apply_quality_filter=False
    )
    print(f"\nGenerated {len(results)} signal(s):")
    for s in results:
        print(f"  {s.id[:8]} {s.ticker} {s.signal_type.value} "
              f"score={s.score:.1f} status={s.status.value}")
        print(f"  reason: {s.reason}")
