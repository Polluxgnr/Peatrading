"""Smart Limit Price Optimizer for PEA Pollux.

Calculates multi-tiered limit order execution prices to maximize fill probability
or optimize risk/reward while avoiding chasing price spikes on Euronext Paris.
"""

from __future__ import annotations

import logging
from typing import Dict

logger = logging.getLogger(__name__)


def calculate_smart_limit_price(
    ticker: str,
    current_price: float,
    atr_14: float = 0.0,
    direction: str = "BUY",
) -> Dict[str, float]:
    """Calculates 3 tiers of smart limit prices based on current price and ATR volatility.

    Args:
        ticker: Asset ticker (e.g. 'MC.PA').
        current_price: Current market price.
        atr_14: 14-day Average True Range (defaults to ~2% of price if 0).
        direction: 'BUY' or 'SELL'.

    Returns:
        Dict[str, float]: Dictionary with 'aggressive', 'optimal', 'patient' limit prices.
    """
    if current_price <= 0:
        logger.warning("Invalid current_price %s for %s", current_price, ticker)
        return {"aggressive": 0.0, "optimal": 0.0, "patient": 0.0}

    # Default ATR to 2% if missing or invalid
    if atr_14 <= 0:
        atr_14 = current_price * 0.02

    direction_clean = str(direction).strip().upper()

    if direction_clean == "BUY":
        # Aggressive: high fill probability (+0.05 ATR)
        p_agg = current_price + 0.05 * atr_14
        # Optimal: balanced (-0.10 ATR)
        p_opt = current_price - 0.10 * atr_14
        # Patient: better entry/RR, lower fill (-0.25 ATR)
        p_pat = current_price - 0.25 * atr_14
    elif direction_clean == "SELL":
        # Aggressive: high fill probability (-0.05 ATR)
        p_agg = current_price - 0.05 * atr_14
        # Optimal: balanced (+0.10 ATR)
        p_opt = current_price + 0.10 * atr_14
        # Patient: better exit/RR (+0.25 ATR)
        p_pat = current_price + 0.25 * atr_14
    else:
        logger.warning("Unknown direction '%s' for %s, defaulting to current price.", direction, ticker)
        p_agg = p_opt = p_pat = current_price

    return {
        "aggressive": round(float(p_agg), 2),
        "optimal": round(float(p_opt), 2),
        "patient": round(float(p_pat), 2),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    res = calculate_smart_limit_price("MC.PA", 600.0, 15.0, "BUY")
    print("Buy Limit Tiers for LVMH (600€, ATR 15€):", res)
