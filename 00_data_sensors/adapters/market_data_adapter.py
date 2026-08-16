"""Market Data Ingestion Adapter for PEA Pollux.

Implements AbstractMarketDataAdapter using yfinance with strict Pydantic contract validation
(MarketTick) and DataQualityGateway validation before storage.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import pandas as pd
import yfinance as yf

_ROOT = Path(__file__).resolve().parent.parent.parent
for d in ("00_data_sensors", "00_data_sensors/adapters", "01_memory_core"):
    sys.path.insert(0, str(_ROOT / d))

from base_adapters import AbstractMarketDataAdapter
from data_contracts import MarketTick
from data_quality import DataQualityGateway

logger = logging.getLogger("market_data_adapter")


class YFinanceMarketDataAdapter(AbstractMarketDataAdapter):
    """Standardized Market Data Adapter backed by Yahoo Finance."""

    def __init__(self, quality_gateway: Optional[DataQualityGateway] = None) -> None:
        self.quality_gateway = quality_gateway or DataQualityGateway()

    async def fetch_ohlcv(self, tickers: List[str], lookback_days: int = 252) -> pd.DataFrame:
        """Fetch daily OHLCV bars for candidate tickers and validate through DataQualityGateway.

        Args:
            tickers: List of standardized ticker symbols.
            lookback_days: Calendar days to fetch.

        Returns:
            pd.DataFrame: Cleaned, quality-checked DataFrame with ['Ticker', 'Date', 'Open', 'High', 'Low', 'Close', 'Volume', 'is_outlier'].
        """
        if not tickers:
            return pd.DataFrame()

        clean_tickers = [t.strip().upper() for t in tickers if t.strip()]
        logger.info("YFinanceMarketDataAdapter: Fetching %d days for %d ticker(s)...", lookback_days, len(clean_tickers))

        try:
            raw = yf.download(
                clean_tickers,
                period=f"{lookback_days}d",
                interval="1d",
                progress=False,
                auto_adjust=True,
                group_by="ticker",
            )
            if raw is None or raw.empty:
                return pd.DataFrame()

            rows = []
            if len(clean_tickers) == 1:
                t = clean_tickers[0]
                df_t = raw.copy()
                if hasattr(df_t.columns, "get_level_values"):
                    df_t.columns = df_t.columns.get_level_values(0)
                for dt, r in df_t.iterrows():
                    rows.append({
                        "Ticker": t,
                        "Date": dt,
                        "Open": float(r.get("Open", 0.0)),
                        "High": float(r.get("High", 0.0)),
                        "Low": float(r.get("Low", 0.0)),
                        "Close": float(r.get("Close", 0.0)),
                        "Volume": float(r.get("Volume", 0.0)),
                    })
            else:
                for t in clean_tickers:
                    if t in raw:
                        df_t = raw[t].dropna(subset=["Close"])
                        for dt, r in df_t.iterrows():
                            rows.append({
                                "Ticker": t,
                                "Date": dt,
                                "Open": float(r.get("Open", 0.0)),
                                "High": float(r.get("High", 0.0)),
                                "Low": float(r.get("Low", 0.0)),
                                "Close": float(r.get("Close", 0.0)),
                                "Volume": float(r.get("Volume", 0.0)),
                            })

            df_all = pd.DataFrame(rows)
            # Run through DataQualityGateway
            return self.quality_gateway.validate_ohlcv_batch(df_all)

        except Exception as exc:
            logger.exception("Failed to fetch OHLCV batch via YFinanceMarketDataAdapter: %s", exc)
            return pd.DataFrame()

    def fetch_latest_tick(self, ticker: str) -> Optional[MarketTick]:
        """Fetch the latest spot quote and return validated MarketTick contract."""
        clean_t = ticker.strip().upper()
        try:
            t_obj = yf.Ticker(clean_t)
            hist = t_obj.history(period="1d", interval="1m")
            if hist is not None and not hist.empty:
                last_row = hist.iloc[-1]
                return MarketTick(
                    ticker=clean_t,
                    ts=datetime.now(timezone.utc),
                    price=float(last_row["Close"]),
                    volume=float(last_row.get("Volume", 0.0)),
                    source="yfinance_intraday",
                )
        except Exception as exc:
            logger.debug("Failed to fetch latest tick for %s: %s", clean_t, exc)
        return None
