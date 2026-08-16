"""Market Regime & Volatility Percentile Tiers for PEA Sniper Terminal.

Upgrades hard binary VIX cutoffs to continuous 252-day percentile-ranked volatility tiers:
  * Percentile >= 95th: Panic / Extreme Volatility -> Conviction Floor +15 pts
  * Percentile >= 80th: Elevated Volatility -> Conviction Floor +5 pts
  * Percentile >= 50th: Normal / Moderate -> Conviction Floor +0 pts
  * Percentile < 50th: Low Volatility / Complacency -> Conviction Floor +0 pts
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple, Union

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class VolatilityRegimeSentinel:
    """Computes rolling 252-day percentile rank of European / Global volatility."""

    def __init__(self, window: int = 252) -> None:
        self.window = window

    @staticmethod
    def calculate_percentile_rank(
        history: Union[pd.Series, Sequence[float], pd.DataFrame],
        current_value: Optional[float] = None,
    ) -> float:
        """Calculate the percentile rank (0.0 to 100.0) of current volatility.

        Args:
            history: Historical VIX/V2TX series (at least 20-252 points).
            current_value: Current VIX level. If None, uses the last element of history.

        Returns:
            float: Percentile rank between 0.0 and 100.0.
        """
        if history is None:
            return 50.0

        if isinstance(history, pd.DataFrame):
            col = "Close" if "Close" in history.columns else history.columns[0]
            series = history[col].dropna().astype(float)
        elif isinstance(history, pd.Series):
            series = history.dropna().astype(float)
        elif isinstance(history, (list, tuple)):
            series = pd.Series(history, dtype=float).dropna()
        else:
            return 50.0

        if len(series) < 5:
            return 50.0

        val = float(current_value if current_value is not None else series.iloc[-1])
        # Percentile rank: % of historical observations <= val
        rank = (series <= val).mean() * 100.0
        return float(np.clip(rank, 0.0, 100.0))

    def get_conviction_floor_modifier(self, percentile: float) -> int:
        """Map volatility percentile rank to a conviction floor offset.

        Args:
            percentile: Percentile rank [0.0..100.0].

        Returns:
            int: Modifier (+15, +5, 0).
        """
        if percentile >= 95.0:
            return 15
        elif percentile >= 80.0:
            return 5
        elif percentile >= 50.0:
            return 0
        else:
            return 0

    def evaluate_vix_regime(
        self,
        vix_history: Union[pd.Series, Sequence[float], pd.DataFrame],
        current_vix: float,
        base_floor: int = 70,
    ) -> dict:
        """Evaluate volatility regime and calculate dynamic conviction threshold.

        Args:
            vix_history: Historical VIX data.
            current_vix: Current spot VIX / V2TX.
            base_floor: Standard emit floor (e.g. 70).

        Returns:
            dict: {
                "current_vix": float,
                "percentile": float,
                "floor_modifier": int,
                "effective_floor": int,
                "regime": str,
                "is_panic": bool
            }
        """
        series = None
        if isinstance(vix_history, pd.DataFrame):
            col = "Close" if "Close" in vix_history.columns else vix_history.columns[0]
            series = vix_history[col].dropna().astype(float)
        elif isinstance(vix_history, pd.Series):
            series = vix_history.dropna().astype(float)
        elif isinstance(vix_history, (list, tuple)):
            series = pd.Series(vix_history, dtype=float).dropna()

        vix_roc_5d = 0.0
        if series is not None and len(series) >= 5:
            past_val = float(series.iloc[-5])
            if past_val > 0:
                vix_roc_5d = float((current_vix - past_val) / past_val)

        pct = self.calculate_percentile_rank(vix_history, current_vix)
        mod = self.get_conviction_floor_modifier(pct)
        eff_floor = base_floor + mod

        is_flash_spike = vix_roc_5d > 0.25

        if pct >= 95.0 or current_vix >= 32.0 or is_flash_spike:
            regime = "PANIC"
            is_panic = True
            if is_flash_spike and mod < 15:
                mod = 15
                eff_floor = base_floor + mod
        elif pct >= 80.0:
            regime = "ELEVATED_VOL"
            is_panic = False
        elif pct >= 50.0:
            regime = "NORMAL"
            is_panic = False
        else:
            regime = "LOW_VOL"
            is_panic = False

        logger.info(
            "VIX Regime: level=%.2f (pct=%.1f%%, roc_5d=%.1f%%) -> regime=%s floor=%d (+%d)",
            current_vix,
            pct,
            vix_roc_5d * 100.0,
            regime,
            eff_floor,
            mod,
        )

        return {
            "current_vix": float(current_vix),
            "vix_roc_5d": float(vix_roc_5d),
            "percentile": float(pct),
            "floor_modifier": int(mod),
            "effective_floor": int(eff_floor),
            "regime": regime,
            "is_panic": is_panic,
        }



if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sentinel = VolatilityRegimeSentinel()
    np.random.seed(42)
    fake_vix = np.random.normal(18.0, 4.0, 252)
    res = sentinel.evaluate_vix_regime(fake_vix, current_vix=28.5)
    print("Regime Assessment:", res)
