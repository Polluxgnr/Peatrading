"""Ratio Backfill & Historical Crisis Stress Tester for PEA Sniper Terminal.

Solves the truncated history problem for French PEA ETFs (e.g. ``CW8.PA``)
by mathematically stitching their price action to long-history proxies
(``URTH``, ``^GSPC``, ``SPY``) using the invariant ratio at the first overlap date:
    ratio = Asset[first_date] / Proxy[first_date]
    Synthetic_History = Proxy[:first_date] * ratio
"""

from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# Key historical crisis regimes to stress-test
CRISIS_PERIODS = {
    "2008_GFC_Lehman": ("2007-10-01", "2009-03-09"),
    "2011_Euro_Debt": ("2011-05-01", "2011-10-04"),
    "2020_Covid_Crash": ("2020-02-19", "2020-03-23"),
    "2022_Inflation_Bear": ("2022-01-03", "2022-10-12"),
}


class RatioBackfillStressTester:
    """Stitches asset history with a proxy index (^FCHI / ^GSPC) and executes crisis stress tests."""

    def __init__(self, target_ticker: str = "CW8.PA", proxy_ticker: str = "^FCHI") -> None:
        self.target_ticker = target_ticker
        self.proxy_ticker = proxy_ticker

    def synthesize_ratio_backfill(
        self,
        target_df: Optional[pd.DataFrame] = None,
        proxy_df: Optional[pd.DataFrame] = None,
        start_year: str = "2000-01-01",
    ) -> pd.DataFrame:
        """Create a continuous synthetic OHLCV history by ratio-backfilling target with proxy.

        Args:
            target_df: DataFrame with Date index and 'Close' column for target (e.g. CW8.PA).
            proxy_df: DataFrame with Date index and 'Close' column for proxy (e.g. ^GSPC).
            start_year: Start date for proxy download if fetching live.

        Returns:
            pd.DataFrame: Stitched DataFrame with columns ['Close', 'Synthetic'].
        """
        if target_df is None or target_df.empty:
            try:
                target_df = yf.download(self.target_ticker, start="2005-01-01", progress=False, auto_adjust=True)
                if isinstance(target_df.columns, pd.MultiIndex):
                    c = target_df["Close"]
                    target_df = pd.DataFrame({"Close": c.iloc[:, 0] if isinstance(c, pd.DataFrame) else c})
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not download %s: %s", self.target_ticker, exc)
                target_df = pd.DataFrame()

        if proxy_df is None or proxy_df.empty:
            try:
                proxy_df = yf.download(self.proxy_ticker, start=start_year, progress=False, auto_adjust=True)
                if isinstance(proxy_df.columns, pd.MultiIndex):
                    c = proxy_df["Close"]
                    proxy_df = pd.DataFrame({"Close": c.iloc[:, 0] if isinstance(c, pd.DataFrame) else c})
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not download proxy %s: %s", self.proxy_ticker, exc)
                proxy_df = pd.DataFrame()

        if target_df.empty and proxy_df.empty:
            return pd.DataFrame(columns=["Close", "Synthetic"])

        if target_df.empty:
            # Entirely proxy
            res = pd.DataFrame({"Close": proxy_df["Close"].dropna(), "Synthetic": True})
            return res

        if proxy_df.empty:
            # Entirely target
            res = pd.DataFrame({"Close": target_df["Close"].dropna(), "Synthetic": False})
            return res

        t_close = target_df["Close"].dropna().sort_index()
        p_close = proxy_df["Close"].dropna().sort_index()

        # Find first overlapping valid date
        overlap_dates = t_close.index.intersection(p_close.index)
        if len(overlap_dates) == 0:
            logger.warning("No overlap dates found between %s and %s.", self.target_ticker, self.proxy_ticker)
            return pd.DataFrame({"Close": t_close, "Synthetic": False})

        first_overlap = overlap_dates[0]
        ratio = float(t_close.loc[first_overlap]) / float(p_close.loc[first_overlap])
        logger.info(
            "Ratio Backfill: first overlap at %s | %s=%.2f, %s=%.2f | ratio=%.6f",
            str(first_overlap)[:10],
            self.target_ticker,
            float(t_close.loc[first_overlap]),
            self.proxy_ticker,
            float(p_close.loc[first_overlap]),
            ratio,
        )

        # Synthetic history prior to first_overlap
        p_pre = p_close[p_close.index < first_overlap] * ratio
        synth_pre = pd.DataFrame({"Close": p_pre, "Synthetic": True})
        actual_post = pd.DataFrame({"Close": t_close, "Synthetic": False})

        stitched = pd.concat([synth_pre, actual_post]).sort_index()
        stitched = stitched[~stitched.index.duplicated(keep="last")]
        return stitched

    def stress_test_crisis(self, stitched_df: pd.DataFrame, start_date: str, end_date: str) -> Dict[str, float]:
        """Calculate maximum drawdown and performance over a specified crisis window."""
        if stitched_df.empty:
            return {"max_drawdown": 0.0, "total_return": 0.0, "trough_date": "n/a"}

        sub = stitched_df.loc[start_date:end_date]
        if sub.empty or len(sub) < 2:
            return {"max_drawdown": 0.0, "total_return": 0.0, "trough_date": "n/a"}

        series = sub["Close"].astype(float)
        peak = series.cummax()
        drawdowns = (series - peak) / peak

        max_dd = float(drawdowns.min())
        trough_idx = drawdowns.idxmin()
        tot_return = float((series.iloc[-1] / series.iloc[0]) - 1.0)

        return {
            "max_drawdown": max_dd,
            "total_return": tot_return,
            "trough_date": str(trough_idx)[:10],
            "start_price": float(series.iloc[0]),
            "trough_price": float(series.loc[trough_idx]),
            "end_price": float(series.iloc[-1]),
        }

    def run_all_stress_tests(self, stitched_df: Optional[pd.DataFrame] = None) -> Dict[str, dict]:
        """Execute full battery of crisis stress tests."""
        if stitched_df is None or stitched_df.empty:
            stitched_df = self.synthesize_ratio_backfill()

        results: Dict[str, dict] = {}
        for name, (start_d, end_d) in CRISIS_PERIODS.items():
            results[name] = self.stress_test_crisis(stitched_df, start_d, end_d)
            logger.info(
                "Stress Test [%s]: Max DD = %.2f%%, Total Return = %.2f%% (Trough: %s)",
                name,
                results[name]["max_drawdown"] * 100,
                results[name]["total_return"] * 100,
                results[name]["trough_date"],
            )

        return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    tester = RatioBackfillStressTester()
    res = tester.run_all_stress_tests()
    print("\n--- Crisis Stress Testing Results ---")
    for crisis, stats in res.items():
        print(f"[{crisis}] Max DD: {stats['max_drawdown']*100:+.2f}% | Return: {stats['total_return']*100:+.2f}% | Trough: {stats['trough_date']}")
