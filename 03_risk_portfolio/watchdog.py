"""Intraday Market Watchdog for PEA Pollux Decision Support Terminal.

Monitors real-time intraday price action of European and global indices (^FCHI, ^STOXX50E, CW8.PA)
to detect flash crashes or intraday drawdowns exceeding risk thresholds.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

_ROOT = Path(__file__).resolve().parent.parent
for sub in ("00_data_sensors", "01_memory_core", "02_quant_engine"):
    sys.path.insert(0, str(_ROOT / sub))

try:
    import yfinance as yf
except ImportError:
    yf = None

logger = logging.getLogger("market_watchdog")


class MarketWatchdog:
    """Monitors real-time intraday market movements for anomaly and crash detection."""

    def __init__(self, default_threshold: float = -0.10) -> None:
        """Initialize watchdog with an intraday crash threshold (default -10%)."""
        self.default_threshold = default_threshold

    def check_intraday_crash(
        self,
        index_ticker: str = "^FCHI",
        threshold: Optional[float] = None,
        mock_data: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """Evaluate intraday high vs current price for flash crash conditions.

        Args:
            index_ticker: Benchmark index symbol (^FCHI, ^GSPC, CW8.PA).
            threshold: Custom percentage drop threshold (e.g. -0.10 for -10%).
            mock_data: Optional dict with 'high' and 'current' for unit testing.

        Returns:
            Dict[str, Any]: {
                "alert": bool,
                "drop_pct": float,
                "day_high": float,
                "current_price": float,
                "ticker": str,
                "message": str,
            }
        """
        thresh = threshold if threshold is not None else self.default_threshold
        clean_ticker = index_ticker.strip().upper()

        day_high: float = 0.0
        cur_price: float = 0.0

        if mock_data is not None:
            day_high = float(mock_data.get("high", 0.0))
            cur_price = float(mock_data.get("current", 0.0))
        elif yf is not None:
            try:
                # Fetch 1-day intraday or daily quote
                data = yf.download(clean_ticker, period="1d", interval="1m", progress=False, auto_adjust=True)
                if data is not None and not data.empty:
                    if hasattr(data.columns, "get_level_values"):
                        data.columns = data.columns.get_level_values(0)
                    day_high = float(data["High"].max())
                    cur_price = float(data["Close"].iloc[-1])
                else:
                    # Fallback to daily bars
                    df_daily = yf.download(clean_ticker, period="5d", interval="1d", progress=False, auto_adjust=True)
                    if df_daily is not None and not df_daily.empty:
                        if hasattr(df_daily.columns, "get_level_values"):
                            df_daily.columns = df_daily.columns.get_level_values(0)
                        day_high = float(df_daily["High"].iloc[-1])
                        cur_price = float(df_daily["Close"].iloc[-1])
            except Exception as exc:
                logger.debug("Failed to fetch intraday data for %s via yfinance: %s", clean_ticker, exc)

        # Fallback if price could not be retrieved
        if day_high <= 0 or cur_price <= 0:
            return {
                "alert": False,
                "drop_pct": 0.0,
                "day_high": 0.0,
                "current_price": 0.0,
                "ticker": clean_ticker,
                "message": "Data unavailable / Market closed",
            }

        drop_pct = (cur_price - day_high) / day_high
        is_crash = drop_pct <= thresh

        if is_crash:
            logger.critical(
                "WATCHDOG FLASH CRASH ALERT on %s: Intraday Drop %.2f%% <= Threshold %.2f%% (High: %.2f, Current: %.2f)",
                clean_ticker, drop_pct * 100.0, thresh * 100.0, day_high, cur_price,
            )
            message = f"CRITICAL: Intraday Flash Crash Detected ({drop_pct*100:.1f}%)"
        else:
            message = "Normal market conditions"

        return {
            "alert": is_crash,
            "drop_pct": round(float(drop_pct), 4),
            "day_high": round(float(day_high), 2),
            "current_price": round(float(cur_price), 2),
            "ticker": clean_ticker,
            "message": message,
        }
