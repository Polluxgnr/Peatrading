"""Market Prices Ingestion Adapter for PEA Pollux.

Implements AbstractMarketDataAdapter using yfinance with anti-ban chunking,
NaN handling, and DataQualityGateway validation before DuckDB persistence.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

import pandas as pd
import yfinance as yf

_ROOT = Path(__file__).resolve().parent.parent.parent
for d in ("00_data_sensors", "00_data_sensors/adapters", "01_memory_core"):
    sys.path.insert(0, str(_ROOT / d))

try:
    from adapters.base_adapters import AbstractMarketDataAdapter
except ImportError:
    try:
        from .base_adapters import AbstractMarketDataAdapter
    except ImportError:
        from base_adapters import AbstractMarketDataAdapter

try:
    from data_quality import DataQualityGateway
except ImportError:
    DataQualityGateway = None

logger = logging.getLogger("market_adapter")

_FLAT_COLUMNS = ["Ticker", "Date", "Open", "High", "Low", "Close", "Volume"]


class YFinanceMarketAdapter(AbstractMarketDataAdapter):
    """Standardized Market Data Adapter backed by Yahoo Finance with chunking and quality control."""

    def __init__(
        self,
        chunk_size: int = 20,
        pause_sec: float = 0.3,
        quality_gateway: Optional[Any] = None,
    ) -> None:
        self.chunk_size = max(1, int(chunk_size))
        self.pause_sec = max(0.0, float(pause_sec))
        self.quality_gateway = quality_gateway or (DataQualityGateway() if DataQualityGateway is not None else None)

    async def fetch_ohlcv(self, tickers: List[str], lookback_days: int = 252) -> pd.DataFrame:
        """Fetch daily OHLCV bars in chunks, clean NaNs, and validate schema for DuckDB storage.

        Args:
            tickers: List of standardized ticker symbols.
            lookback_days: Number of calendar days to retrieve.

        Returns:
            pd.DataFrame: Cleaned DataFrame matching DuckDB schema ['Ticker', 'Date', 'Open', 'High', 'Low', 'Close', 'Volume'].
        """
        if not tickers:
            return pd.DataFrame(columns=_FLAT_COLUMNS)

        clean_tickers = [t.strip().upper() for t in tickers if t.strip()]
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_fetch_ohlcv, clean_tickers, lookback_days)

    def _sync_fetch_ohlcv(self, tickers: List[str], lookback_days: int = 252) -> pd.DataFrame:
        start_date = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        all_frames: List[pd.DataFrame] = []

        for i in range(0, len(tickers), self.chunk_size):
            chunk = tickers[i : i + self.chunk_size]
            if i > 0 and self.pause_sec > 0:
                time.sleep(self.pause_sec)

            try:
                raw = yf.download(
                    chunk,
                    start=start_date,
                    progress=False,
                    auto_adjust=True,
                    group_by="ticker",
                    threads=False,
                )
            except Exception as exc:
                logger.warning("yf.download failed for chunk %s: %s", chunk, exc)
                continue

            if raw is None or raw.empty:
                continue

            rows = []
            if isinstance(raw.columns, pd.MultiIndex):
                if chunk[0] in raw.columns.get_level_values(0):
                    by_ticker = {t: raw[t] for t in chunk if t in raw}
                elif chunk[0] in raw.columns.get_level_values(1):
                    by_ticker = {t: raw.xs(t, axis=1, level=1) for t in chunk if t in raw.columns.get_level_values(1)}
                else:
                    by_ticker = {chunk[0]: raw}
            else:
                by_ticker = {chunk[0]: raw}

            for t, df_t in by_ticker.items():
                if df_t is None or df_t.empty:
                    continue
                if hasattr(df_t.columns, "get_level_values"):
                    df_t.columns = df_t.columns.get_level_values(0)
                if "Close" in df_t.columns:
                    df_t = df_t.dropna(subset=["Close"])
                for dt, r in df_t.iterrows():
                    rows.append({
                        "Ticker": t,
                        "Date": pd.to_datetime(dt),
                        "Open": float(r.get("Open", 0.0)),
                        "High": float(r.get("High", 0.0)),
                        "Low": float(r.get("Low", 0.0)),
                        "Close": float(r.get("Close", 0.0)),
                        "Volume": float(r.get("Volume", 0.0)),
                    })

            if rows:
                chunk_df = pd.DataFrame(rows)
                all_frames.append(chunk_df)


        if not all_frames:
            return pd.DataFrame(columns=_FLAT_COLUMNS)

        combined = pd.concat(all_frames, ignore_index=True)
        # Clean nulls / invalid values
        combined = combined.dropna(subset=["Ticker", "Date", "Close"])
        combined["Date"] = pd.to_datetime(combined["Date"])
        combined = combined.sort_values(by=["Ticker", "Date"]).reset_index(drop=True)

        if self.quality_gateway is not None:
            try:
                combined = self.quality_gateway.validate_ohlcv_batch(combined)
            except Exception as exc:
                logger.warning("DataQualityGateway validation failed: %s", exc)

        return combined


# Alias for backward compatibility
YFinanceMarketDataAdapter = YFinanceMarketAdapter
