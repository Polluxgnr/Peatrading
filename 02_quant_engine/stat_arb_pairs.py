"""Statistical Arbitrage & Cointegration Pairs Trading Engine for PEA Pollux.

Identifies stationary, cointegrated asset pairs within the same economic sector
(Engle-Granger two-step test, p-value < 0.05) and calculates rolling Z-scores of the
hedged spread to generate mean-reverting statistical arbitrage signals.

Strict Sector Isolation:
  Pairs are strictly formed within the same sector (from pea_universe.yaml)
  to prevent spurious mathematical correlation across fundamentally disconnected businesses.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    from statsmodels.tsa.stattools import coint
except ImportError:
    coint = None  # Fallback handled in code

_ROOT = Path(__file__).resolve().parent.parent
for _d in ("00_data_sensors", "01_memory_core", "02_quant_engine", "03_risk_portfolio"):
    sys.path.insert(0, str(_ROOT / _d))

from data_models import Signal, SignalStatus, SignalType

logger = logging.getLogger(__name__)


class StatArbEngine:
    """Quantitative cointegration pairs trading and spread Z-score engine."""

    def __init__(
        self,
        p_val_threshold: float = 0.05,
        z_score_entry: float = 2.0,
        z_score_exit: float = 0.5,
        rolling_window: int = 20,
        min_history_days: int = 120,
    ) -> None:
        self.p_val_threshold = float(p_val_threshold)
        self.z_score_entry = float(z_score_entry)
        self.z_score_exit = float(z_score_exit)
        self.rolling_window = int(rolling_window)
        self.min_history_days = int(min_history_days)

    def compute_pair_spread(
        self, series_a: pd.Series, series_b: pd.Series
    ) -> Tuple[float, float, pd.Series, float]:
        """Fit OLS hedge ratio on log prices and calculate the rolling Z-score.

        Args:
            series_a: Close prices for Asset A.
            series_b: Close prices for Asset B.

        Returns:
            Tuple: (p_value, hedge_ratio_beta, z_score_series, current_z_score).
        """
        # Align series on common dates
        df = pd.DataFrame({"A": series_a, "B": series_b}).dropna()
        if len(df) < self.min_history_days:
            return 1.0, 1.0, pd.Series(dtype=float), 0.0

        log_a = np.log(df["A"])
        log_b = np.log(df["B"])

        # 1. Cointegration test (Engle-Granger)
        if coint is not None:
            try:
                score, p_value, _ = coint(log_a, log_b)
            except Exception as exc:
                logger.debug("Cointegration calculation error: %s", exc)
                p_value = 1.0
        else:
            p_value = 1.0

        # 2. Estimate hedge ratio beta via OLS slope
        cov_matrix = np.cov(log_a, log_b)
        var_b = cov_matrix[1, 1]
        beta = float(cov_matrix[0, 1] / var_b) if var_b > 1e-12 else 1.0

        # 3. Calculate spread: ln(A) - beta * ln(B)
        spread = log_a - (beta * log_b)

        # 4. Rolling 20-day Z-score
        rolling_mean = spread.rolling(window=self.rolling_window).mean()
        rolling_std = spread.rolling(window=self.rolling_window).std()

        z_scores = (spread - rolling_mean) / rolling_std.replace(0, np.nan)
        z_scores = z_scores.dropna()

        cur_z = float(z_scores.iloc[-1]) if not z_scores.empty else 0.0
        return float(p_value), float(beta), z_scores, cur_z

    def find_cointegrated_pairs(
        self,
        prices_by_ticker: Dict[str, pd.DataFrame],
        sector_map: Dict[str, str],
    ) -> List[Dict]:
        """Scan tickers grouped strictly by sector to identify cointegrated pairs.

        Args:
            prices_by_ticker: Dict mapping ticker to DataFrame with 'Close' column.
            sector_map: Dict mapping ticker to its economic sector.

        Returns:
            List[Dict]: List of discovered cointegrated pair descriptors.
        """
        # Group tickers by sector
        sectors: Dict[str, List[str]] = {}
        for ticker, sector in sector_map.items():
            if ticker in prices_by_ticker:
                sectors.setdefault(sector, []).append(ticker)

        found_pairs: List[Dict] = []

        for sector, tickers in sectors.items():
            if len(tickers) < 2:
                continue

            for i in range(len(tickers)):
                for j in range(i + 1, len(tickers)):
                    t_a = tickers[i]
                    t_b = tickers[j]

                    df_a = prices_by_ticker[t_a]
                    df_b = prices_by_ticker[t_b]

                    s_a = df_a["Close"] if "Close" in df_a.columns else None
                    s_b = df_b["Close"] if "Close" in df_b.columns else None

                    if s_a is None or s_b is None or len(s_a) < self.min_history_days or len(s_b) < self.min_history_days:
                        continue

                    p_val, beta, z_series, cur_z = self.compute_pair_spread(s_a, s_b)

                    if p_val < self.p_val_threshold:
                        found_pairs.append({
                            "ticker_a": t_a,
                            "ticker_b": t_b,
                            "sector": sector,
                            "p_value": round(p_val, 4),
                            "beta": round(beta, 4),
                            "current_z": round(cur_z, 2),
                            "n_obs": len(z_series),
                        })

        found_pairs.sort(key=lambda p: abs(p["current_z"]), reverse=True)
        logger.info("StatArb: Found %d cointegrated pairs across %d sectors.", len(found_pairs), len(sectors))
        return found_pairs

    def generate_stat_arb_signals(
        self,
        prices_by_ticker: Dict[str, pd.DataFrame],
        sector_map: Dict[str, str],
    ) -> List[Signal]:
        """Generate actionable BUY signals when a cointegrated pair's spread deviates > 2.0 sigma.

        Args:
            prices_by_ticker: Dict mapping ticker to historical DataFrame with Close prices.
            sector_map: Dict mapping ticker to sector.

        Returns:
            List[Signal]: List of statistical arbitrage candidate signals.
        """
        pairs = self.find_cointegrated_pairs(prices_by_ticker, sector_map)
        signals: List[Signal] = []

        for pair in pairs:
            t_a = pair["ticker_a"]
            t_b = pair["ticker_b"]
            z = pair["current_z"]
            p_val = pair["p_value"]
            beta = pair["beta"]
            sector = pair["sector"]

            # Scenario 1: Z-score <= -2.0 -> Asset A is significantly undervalued relative to Asset B
            if z <= -self.z_score_entry:
                score = min(95.0, 75.0 + abs(z) * 5.0)
                reason = (
                    f"StatArb Cointegration: {t_a} is undervalued vs {t_b} in sector '{sector}' "
                    f"(Z-score: {z:+.2f}, p-value: {p_val:.4f}, Beta: {beta:.2f})"
                )
                sig = Signal(
                    ticker=t_a,
                    signal_type=SignalType.BUY,
                    score=round(score, 1),
                    reason=reason,
                    lineage={
                        "strategy": "STAT_ARB_COINTEGRATION",
                        "pair_partner": t_b,
                        "sector": sector,
                        "z_score": z,
                        "p_value": p_val,
                        "beta": beta,
                    },
                )
                signals.append(sig)
                logger.info("StatArb Signal Generated: BUY %s (vs %s, Z=%.2f, p=%.4f)", t_a, t_b, z, p_val)

            # Scenario 2: Z-score >= +2.0 -> Asset B is significantly undervalued relative to Asset A
            elif z >= self.z_score_entry:
                score = min(95.0, 75.0 + abs(z) * 5.0)
                reason = (
                    f"StatArb Cointegration: {t_b} is undervalued vs {t_a} in sector '{sector}' "
                    f"(Z-score: {-z:+.2f}, p-value: {p_val:.4f}, Beta: {beta:.2f})"
                )
                sig = Signal(
                    ticker=t_b,
                    signal_type=SignalType.BUY,
                    score=round(score, 1),
                    reason=reason,
                    lineage={
                        "strategy": "STAT_ARB_COINTEGRATION",
                        "pair_partner": t_a,
                        "sector": sector,
                        "z_score": -z,
                        "p_value": p_val,
                        "beta": beta,
                    },
                )
                signals.append(sig)
                logger.info("StatArb Signal Generated: BUY %s (vs %s, Z=%.2f, p=%.4f)", t_b, t_a, -z, p_val)

        return signals


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Synthetic test of cointegrated random walk
    np.random.seed(42)
    t_len = 300
    walk = np.cumsum(np.random.normal(0, 1, t_len))
    noise1 = np.random.normal(0, 0.5, t_len)
    noise2 = np.random.normal(0, 0.5, t_len)
    # create artificial spread divergence at the end
    noise1[-5:] -= 3.0

    p_a = pd.DataFrame({"Close": np.exp(walk + noise1 + 4.0)})
    p_b = pd.DataFrame({"Close": np.exp(walk + noise2 + 4.0)})

    engine = StatArbEngine()
    sigs = engine.generate_stat_arb_signals(
        {"MC.PA": p_a, "OR.PA": p_b},
        {"MC.PA": "Luxury", "OR.PA": "Luxury"},
    )
    print("Signals emitted:", sigs)
