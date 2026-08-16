"""Attack / Shield Allocation & Volatility Thermometer for PEA Pollux.

Implements a Global Macro allocation mechanism:
  - Dynamically splits portfolio capital between the Attack Engine (directional equities/ETFs)
    and Shield Engine (uncapped cash or PEA Money Market funds like CSH.PA).
  - Calculates 21-day rolling annualized volatility of the market benchmark.
  - Enforces "Bunker Mode": 100% Defense (0% Attack) whenever benchmark close < SMA_200.
  - Dynamically scales Attack allocation inversely to VIX and 21-day realized volatility when above SMA_200.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Union

import numpy as np
import pandas as pd

logger = logging.getLogger("allocation_thermometer")


class VolatilityThermometer:
    """Computes dynamic Attack vs Shield allocation splits and manages Bunker Mode."""

    def __init__(self, permanent_cash_buffer: float = 0.02) -> None:
        self.permanent_cash_buffer = permanent_cash_buffer
        self.max_exposure = 1.0 - permanent_cash_buffer  # 0.98

    def calculate_attack_defense_split(
        self,
        index_history: Optional[Union[pd.DataFrame, pd.Series]] = None,
        current_vix: float = 16.0,
    ) -> Dict[str, Any]:
        """Calculate the Attack vs Defense allocation split based on 200 SMA and volatility.

        Args:
            index_history: Benchmark OHLCV or Close price series (^FCHI, CW8.PA, SPY).
            current_vix: Current spot VIX / V2TX level.

        Returns:
            Dict[str, Any]: {
                "attack_pct": float (0.0 to 0.98),
                "defense_pct": float (0.02 to 1.0),
                "mode": "BUNKER" | "ATTACK" | "DEFENSE_LEANING",
                "vol_21d": float,
                "vix": float,
                "sma_200": float,
                "close": float,
                "is_bunker": bool,
            }
        """
        # Default baseline
        cur_close = 100.0
        sma_200 = 90.0
        vol_21d = 0.15

        if index_history is not None and not index_history.empty:
            if isinstance(index_history, pd.DataFrame):
                col = "Close" if "Close" in index_history.columns else index_history.columns[0]
                close = index_history[col].dropna().astype(float)
            else:
                close = index_history.dropna().astype(float)

            if len(close) >= 21:
                rets = close.pct_change().dropna()
                vol_21d = float(rets.tail(21).std() * np.sqrt(252))

            if len(close) >= 200:
                sma_200 = float(close.tail(200).mean())
            elif len(close) > 0:
                sma_200 = float(close.mean())

            if len(close) > 0:
                cur_close = float(close.iloc[-1])

        # Check Bunker Mode Trigger: Close < SMA_200
        if cur_close < sma_200:
            logger.warning(
                "BUNKER MODE TRIGGERED: Index Close (%.2f) < SMA_200 (%.2f). 100%% Defense split.",
                cur_close, sma_200,
            )
            return {
                "attack_pct": 0.0,
                "defense_pct": 1.0,
                "mode": "BUNKER",
                "vol_21d": round(vol_21d, 4),
                "vix": round(float(current_vix), 2),
                "sma_200": round(sma_200, 2),
                "close": round(cur_close, 2),
                "is_bunker": True,
            }

        # Above SMA_200: Scale Attack ratio inversely to VIX and 21d volatility
        # VIX < 15 & Vol < 0.14 -> 90-98% Attack
        # VIX > 25 or Vol > 0.25 -> 20-35% Attack
        vix_penalty = max(0.0, (current_vix - 12.0) / 18.0) * 0.65  # up to -0.65
        vol_penalty = max(0.0, (vol_21d - 0.10) / 0.20) * 0.35      # up to -0.35

        raw_attack = 0.98 - vix_penalty - vol_penalty
        attack_pct = float(np.clip(raw_attack, 0.15, self.max_exposure))
        defense_pct = round(1.0 - attack_pct, 4)
        attack_pct = round(attack_pct, 4)

        mode = "ATTACK" if attack_pct >= 0.50 else "DEFENSE_LEANING"

        logger.info(
            "Volatility Thermometer: Attack=%.1f%%, Defense=%.1f%% (Mode=%s, VIX=%.1f, Vol21d=%.1f%%)",
            attack_pct * 100.0, defense_pct * 100.0, mode, current_vix, vol_21d * 100.0,
        )

        return {
            "attack_pct": attack_pct,
            "defense_pct": defense_pct,
            "mode": mode,
            "vol_21d": round(vol_21d, 4),
            "vix": round(float(current_vix), 2),
            "sma_200": round(sma_200, 2),
            "close": round(cur_close, 2),
            "is_bunker": False,
        }
