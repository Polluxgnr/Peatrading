"""Kinetic Brake & Dynamic Drawdown Sizing for PEA Sniper Terminal.

Upgrades binary drawdown stops to a continuous, tiered Kinetic Brake:
  * Drawdown > -5%: 1.00 (Full exposure)
  * Drawdown <= -5% and > -10%: 0.50 (Exposure reduced by 50%)
  * Drawdown <= -10% and > -15%: 0.20 (Exposure reduced by 80%)
  * Drawdown <= -15%: 0.00 (Circuit breaker / total halt)
"""

from __future__ import annotations

import logging
from typing import Sequence, Tuple, Union

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class DrawdownBreaker:
    """Computes dynamic kinetic multipliers and enforces daily/weekly/monthly circuit breakers."""

    def __init__(
        self,
        daily_max_loss: float = -0.005,
        weekly_max_loss: float = -0.02,
        monthly_max_loss: float = -0.05,
        halt_threshold: float = -0.15,
    ) -> None:
        """Initialize breaker with institutional thresholds."""
        self.daily_max_loss = daily_max_loss
        self.weekly_max_loss = weekly_max_loss
        self.monthly_max_loss = monthly_max_loss
        self.halt_threshold = halt_threshold

    @staticmethod
    def compute_drawdown(
        equity_series: Union[pd.Series, Sequence[float], pd.DataFrame]
    ) -> float:
        """Calculate the current drawdown from peak."""
        if equity_series is None:
            return 0.0

        if isinstance(equity_series, pd.DataFrame):
            if "equity" not in equity_series.columns or equity_series.empty:
                return 0.0
            series = equity_series["equity"].dropna().astype(float)
        elif isinstance(equity_series, pd.Series):
            series = equity_series.dropna().astype(float)
        elif isinstance(equity_series, (list, tuple)):
            series = pd.Series(equity_series, dtype=float).dropna()
        else:
            return 0.0

        if len(series) < 1:
            return 0.0

        peak = float(series.cummax().iloc[-1])
        current = float(series.iloc[-1])

        if peak <= 0:
            return 0.0

        dd = (current - peak) / peak
        return float(dd)

    def calculate_kinetic_multiplier(self, drawdown: float) -> float:
        """Map drawdown to kinetic sizing multiplier [0.0, 1.0]."""
        if drawdown > -0.05:
            return 1.0
        elif drawdown > -0.10:
            return 0.50
        elif drawdown > -0.15:
            return 0.20
        else:
            return 0.0

    def check_loss_limits(
        self,
        equity_history: pd.DataFrame | pd.Series,
    ) -> Tuple[bool, str]:
        """Check multi-horizon loss circuit breakers (daily, weekly, monthly).

        Returns:
            Tuple[bool, str]: (passed, reason_if_vetoed).
        """
        if equity_history is None:
            return True, "OK"

        if isinstance(equity_history, pd.DataFrame):
            if "equity" not in equity_history.columns or len(equity_history) < 2:
                return True, "OK"
            series = equity_history["equity"].astype(float)
        else:
            series = pd.Series(equity_history, dtype=float).dropna()

        if len(series) < 2:
            return True, "OK"

        cur = float(series.iloc[-1])
        prev_1d = float(series.iloc[-2])
        if prev_1d > 0:
            chg_1d = (cur - prev_1d) / prev_1d
            if chg_1d < self.daily_max_loss:
                return False, f"DAILY_MAX_LOSS breached: {chg_1d*100:+.2f}% < {self.daily_max_loss*100:.2f}%"

        if len(series) >= 5:
            prev_5d = float(series.iloc[-5])
            if prev_5d > 0:
                chg_5d = (cur - prev_5d) / prev_5d
                if chg_5d < self.weekly_max_loss:
                    return False, f"WEEKLY_MAX_LOSS breached: {chg_5d*100:+.2f}% < {self.weekly_max_loss*100:.2f}%"

        if len(series) >= 21:
            prev_21d = float(series.iloc[-21])
            if prev_21d > 0:
                chg_21d = (cur - prev_21d) / prev_21d
                if chg_21d < self.monthly_max_loss:
                    return False, f"MONTHLY_MAX_LOSS breached: {chg_21d*100:+.2f}% < {self.monthly_max_loss*100:.2f}%"

        return True, "OK"

    def check(self, drawdown_or_equity: Union[float, pd.Series, Sequence[float], pd.DataFrame]) -> Tuple[float, str]:
        """Evaluate drawdown and return (kinetic_multiplier, reason)."""
        if isinstance(drawdown_or_equity, (int, float)):
            dd = float(drawdown_or_equity)
        else:
            dd = self.compute_drawdown(drawdown_or_equity)

        mult = self.calculate_kinetic_multiplier(dd)

        if mult == 1.0:
            reason = f"Normal regime: Drawdown {dd*100:+.1f}% > -5.0%. Full exposure (1.0x)."
        elif mult == 0.50:
            reason = f"Kinetic Brake Tier 1: Drawdown {dd*100:+.1f}% in [-10%, -5%]. Tranches scaled to 50%."
        elif mult == 0.20:
            reason = f"Kinetic Brake Tier 2: Drawdown {dd*100:+.1f}% in [-15%, -10%]. Tranches scaled to 20%."
        else:
            reason = f"Kinetic Brake HALT: Drawdown {dd*100:+.1f}% <= -15.0%. New allocations suspended (0.0x)."

        logger.info("Kinetic Brake check: dd=%.2f%% -> mult=%.2f (%s)", dd * 100, mult, reason)
        return mult, reason


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    breaker = DrawdownBreaker()
    test_eq = [10000, 10500, 11000, 10300, 9800, 9200]
    dd = breaker.compute_drawdown(test_eq)
    mult, why = breaker.check(dd)
    print(f"Current DD: {dd*100:+.2f}% | Multiplier: {mult} | Reason: {why}")
