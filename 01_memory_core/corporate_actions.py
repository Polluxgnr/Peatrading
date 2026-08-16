"""Corporate Actions & Self-Healing Data Engine for PEA Pollux.

Detects corporate actions (stock splits, consolidations, special distributions)
that distort historical price continuity, and automatically triggers retroactive
self-healing of DuckDB time-series records.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

_ROOT = Path(__file__).resolve().parent.parent
for d in ("00_data_sensors", "00_data_sensors/adapters", "01_memory_core"):
    sys.path.insert(0, str(_ROOT / d))

from duckdb_manager import TimeSeriesDB
from market_data_adapter import YFinanceMarketDataAdapter

logger = logging.getLogger("corporate_actions")


class DataHealer:
    """Automated self-healing engine for corporate actions and split adjustments."""

    def __init__(self, market_adapter: Optional[YFinanceMarketDataAdapter] = None) -> None:
        self.market_adapter = market_adapter or YFinanceMarketDataAdapter()

    def detect_and_heal_splits(self, ticker: str, ts_db: TimeSeriesDB) -> bool:
        """Check for recent stock splits in the last 5 days and heal DuckDB history.

        Args:
            ticker: Standardized Yahoo/Euronext ticker symbol (e.g., 'MC.PA').
            ts_db: TimeSeriesDB persistence gateway instance.

        Returns:
            bool: True if a split was detected and history was healed, False otherwise.
        """
        clean_ticker = ticker.strip().upper()
        logger.debug("Checking corporate action splits for %s...", clean_ticker)

        try:
            t_obj = yf.Ticker(clean_ticker)
            splits_series = t_obj.splits

            has_recent_split = False
            if splits_series is not None and not splits_series.empty:
                # Filter splits in the last 5 days
                cutoff_dt = pd.Timestamp.now(tz=splits_series.index.tz if hasattr(splits_series.index, "tz") else None) - pd.Timedelta(days=5)
                # Convert timezone if needed
                if hasattr(splits_series.index, "tz") and splits_series.index.tz is not None:
                    recent_splits = splits_series[splits_series.index >= cutoff_dt]
                else:
                    cutoff_naive = pd.Timestamp.now() - pd.Timedelta(days=5)
                    recent_splits = splits_series[splits_series.index >= cutoff_naive]

                if not recent_splits.empty and (recent_splits > 0).any():
                    has_recent_split = True

            # Also check actions table if available
            if not has_recent_split and hasattr(t_obj, "actions") and t_obj.actions is not None and not t_obj.actions.empty:
                actions_df = t_obj.actions
                if "Stock Splits" in actions_df.columns:
                    cutoff_naive = pd.Timestamp.now() - pd.Timedelta(days=5)
                    if hasattr(actions_df.index, "tz") and actions_df.index.tz is not None:
                        actions_df_recent = actions_df[actions_df.index >= pd.Timestamp.now(tz=actions_df.index.tz) - pd.Timedelta(days=5)]
                    else:
                        actions_df_recent = actions_df[actions_df.index >= cutoff_naive]

                    if not actions_df_recent.empty and (actions_df_recent["Stock Splits"] > 0).any():
                        has_recent_split = True

            if not has_recent_split:
                return False

            logger.critical("CORPORATE ACTION: Split detected for %s. Initiating self-healing.", clean_ticker)

            # 1. Wipe existing history for this ticker in DuckDB
            try:
                with ts_db._connect() as conn:
                    conn.execute("DELETE FROM ohlcv_data WHERE ticker = ?;", [clean_ticker])
                logger.info("Wiped unadjusted historical OHLCV data for %s from DuckDB.", clean_ticker)
            except Exception as exc:
                logger.warning("Could not execute DELETE on DuckDB for %s: %s", clean_ticker, exc)

            # 2. Re-download full 252-day auto-adjusted history
            raw_hist = yf.download(clean_ticker, period="252d", interval="1d", progress=False, auto_adjust=True)
            if raw_hist is not None and not raw_hist.empty:
                if hasattr(raw_hist.columns, "get_level_values"):
                    raw_hist.columns = raw_hist.columns.get_level_values(0)

                rows = []
                for dt, r in raw_hist.iterrows():
                    rows.append({
                        "Ticker": clean_ticker,
                        "Date": dt,
                        "Open": float(r.get("Open", 0.0)),
                        "High": float(r.get("High", 0.0)),
                        "Low": float(r.get("Low", 0.0)),
                        "Close": float(r.get("Close", 0.0)),
                        "Volume": float(r.get("Volume", 0.0)),
                    })
                df_healed = pd.DataFrame(rows)
                upserted = ts_db.upsert_ohlcv(df_healed)
                logger.info("Self-healing complete for %s: %d auto-adjusted rows inserted.", clean_ticker, upserted)
                return True

        except Exception as exc:
            logger.exception("Failed during detect_and_heal_splits for %s: %s", clean_ticker, exc)

        return False
