"""Market data ingestion for PEA Pollux.

Fetches daily OHLCV via the official ``yfinance`` API (no scraping), flattens
the multi-ticker response into the schema expected by ``TimeSeriesDB``
(Phase 2), and feeds it into DuckDB.

This is a pure ingestion layer: no indicator math, risk, or trading logic.
"""

import logging
import os
import random
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import datetime, timedelta
from typing import Any, List

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# Flat schema shared with TimeSeriesDB (Phase 2).
_FLAT_COLUMNS = ["Ticker", "Date", "Open", "High", "Low", "Close", "Volume"]
_OHLCV_ATTRS = ["Open", "High", "Low", "Close", "Volume"]


class MarketDataFetcher:
    """Downloads and normalizes daily OHLCV data from Yahoo Finance."""
    
    def _get_stealth_session(self) -> requests.Session:
        """Create a stealthy requests session to bypass rate limits."""
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1"
        })
        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def fetch_daily_ohlcv(
        self, tickers: List[str], lookback_days: int = 3650
    ) -> pd.DataFrame:
        """Download and flatten daily OHLCV for a batch of tickers.

        All tickers are downloaded in a single batched `yf.download` call to
        avoid rate limits. The multi-index response is flattened into the
        columns `Ticker, Date, Open, High, Low, Close, Volume`.

        Args:
            tickers: List of Yahoo Finance ticker symbols.
            lookback_days: Calendar days of history to request.
                Use `3650` (~10 years) for long-horizon / ML backfills.

        Returns:
            pd.DataFrame: Cleaned, flat OHLCV data. Empty DataFrame (with the
            correct columns) if nothing could be retrieved.
        """
        if not tickers:
            logger.warning("fetch_daily_ohlcv called with no tickers.")
            return pd.DataFrame(columns=_FLAT_COLUMNS)

        start_date = (datetime.now() - timedelta(days=lookback_days)).strftime(
            "%Y-%m-%d"
        )
        logger.info(
            "Downloading OHLCV for %d ticker(s) since %s (SNIPER MODE).",
            len(tickers),
            start_date,
        )

        frames = []
        stealth_session = self._get_stealth_session()
        
        for ticker in tickers:
            try:
                t_obj = yf.Ticker(ticker, session=stealth_session)
                df_ticker = t_obj.history(start=start_date, auto_adjust=True)
                
                if df_ticker is not None and not df_ticker.empty:
                    df_ticker = df_ticker.reset_index()
                    if "Date" in df_ticker.columns and pd.api.types.is_datetime64tz_dtype(df_ticker["Date"]):
                        df_ticker["Date"] = df_ticker["Date"].dt.tz_localize(None)
                    
                    df_ticker["Ticker"] = ticker
                    
                    missing = [c for c in _FLAT_COLUMNS if c not in df_ticker.columns]
                    for m in missing:
                        df_ticker[m] = 0.0
                        
                    frames.append(df_ticker[_FLAT_COLUMNS])
                else:
                    logger.debug("No data for %s", ticker)
            except Exception as e:
                logger.warning("Failed fetching %s: %s", ticker, e)
                
            time.sleep(random.uniform(1.5, 3.5))

        if frames:
            yf_df = pd.concat(frames, ignore_index=True)
            return self._clean(yf_df)
            
        return pd.DataFrame(columns=_FLAT_COLUMNS)

    def _flatten(self, raw: pd.DataFrame, tickers: List[str]) -> pd.DataFrame:
        """Restructure a yfinance response into the flat schema.

        Handles both the multi-ticker (MultiIndex columns) and single-ticker
        (flat columns) response shapes.

        Args:
            raw: Raw DataFrame returned by ``yf.download``.
            tickers: The originally requested tickers (used for the single case).

        Returns:
            pd.DataFrame: Flat OHLCV with the canonical column order.
        """
        if isinstance(raw.columns, pd.MultiIndex):
            # Columns are (Attribute, Ticker). Stack the ticker level into rows.
            stacked = raw.stack(level=1, future_stack=True)
            stacked = stacked.reset_index()
            # After reset_index: 'Date', the ticker level, then attributes.
            rename_map = {stacked.columns[0]: "Date", stacked.columns[1]: "Ticker"}
            stacked = stacked.rename(columns=rename_map)
            flat = stacked
        else:
            # Single ticker: attach the ticker name explicitly.
            flat = raw.reset_index().rename(columns={"index": "Date"})
            if "Date" not in flat.columns and "Datetime" in flat.columns:
                flat = flat.rename(columns={"Datetime": "Date"})
            flat["Ticker"] = tickers[0]

        missing = [c for c in _OHLCV_ATTRS if c not in flat.columns]
        if missing:
            logger.warning("Response missing attributes %s; got %s", missing,
                           list(flat.columns))
            return pd.DataFrame(columns=_FLAT_COLUMNS)

        flat = flat[_FLAT_COLUMNS].copy()
        flat["Date"] = pd.to_datetime(flat["Date"]).dt.tz_localize(None).dt.normalize()
        return flat

    def _clean(self, flat: pd.DataFrame) -> pd.DataFrame:
        """Handle NaNs per ticker and drop empty/delisted tickers.

        Forward- then backward-fills OHLCV within each ticker group. Tickers
        with no usable data at all are logged and dropped.

        Args:
            flat: Flat OHLCV DataFrame.

        Returns:
            pd.DataFrame: Cleaned data sorted by ``Ticker`` then ``Date``.
        """
        cleaned_frames: List[pd.DataFrame] = []
        for ticker, group in flat.groupby("Ticker", sort=False):
            price_slice = group[_OHLCV_ATTRS]
            if price_slice.dropna(how="all").empty:
                logger.warning("Ticker %s has no data; dropping.", ticker)
                continue
            group = group.sort_values("Date").copy()
            group[_OHLCV_ATTRS] = group[_OHLCV_ATTRS].ffill().bfill()
            group = group.dropna(subset=_OHLCV_ATTRS)
            if group.empty:
                logger.warning("Ticker %s empty after cleaning; dropping.", ticker)
                continue
            group["Volume"] = group["Volume"].fillna(0).astype("int64")
            cleaned_frames.append(group)

        if not cleaned_frames:
            logger.warning("No tickers survived cleaning.")
            return pd.DataFrame(columns=_FLAT_COLUMNS)

        result = pd.concat(cleaned_frames, ignore_index=True)
        result = result.sort_values(["Ticker", "Date"]).reset_index(drop=True)
        return result[_FLAT_COLUMNS]

    def update_database(
        self, db_manager: Any, tickers: List[str], lookback_days: int = 3650
    ) -> Any:
        """Fetch OHLCV and upsert it into a `TimeSeriesDB` instance.

        Args:
            db_manager: A Phase 2 `TimeSeriesDB` (must expose `upsert_ohlcv`).
            tickers: Ticker symbols to ingest.
            lookback_days: Calendar days of history to request (default 252).

        Returns:
            int: Number of rows upserted on success, `False` if any exception occurred.
        """
        try:
            import time
            total_rows_upserted = 0
            chunk_size = 10
            now = datetime.now()
            
            for i in range(0, len(tickers), chunk_size):
                chunk = tickers[i:i + chunk_size]
                latest_dates = getattr(db_manager, "get_latest_dates", lambda t: {})(chunk)
                max_gap_days = 0
                
                for t in chunk:
                    last_dt_str = latest_dates.get(t)
                    if not last_dt_str:
                        max_gap_days = max(max_gap_days, lookback_days)
                        continue
                    try:
                        last_dt = datetime.strptime(last_dt_str, "%Y-%m-%d")
                        gap = (now - last_dt).days + 1
                        max_gap_days = max(max_gap_days, gap)
                    except ValueError:
                        max_gap_days = max(max_gap_days, lookback_days)
                
                final_lookback = min(max_gap_days, lookback_days)
                if final_lookback <= 0:
                    final_lookback = 3  # Always fetch a few days to ensure no missed updates
                    
                logger.info("Incremental fetch for chunk %d/%d: requested %d days, optimized to %d days.", 
                            (i // chunk_size) + 1, (len(tickers) + chunk_size - 1) // chunk_size, lookback_days, final_lookback)
                
                df = self.fetch_daily_ohlcv(chunk, lookback_days=final_lookback)
                if df.empty:
                    logger.warning("No data fetched for chunk; skipping.")
                    continue

                # --- Sanity Outlier Filter (Phase 53+) ---
                # Drop rows with > +50% or < -40% daily return to prevent yfinance bugs
                # from corrupting the ML models.
                df = df.sort_values(["Ticker", "Date"])
                # Calculate pct_change per ticker
                df["_pct_chg"] = df.groupby("Ticker")["Close"].pct_change()
                
                # Phase 60: Dynamic Tick Anomaly Detection using IsolationForest
                abnormal_mask = pd.Series(False, index=df.index)
                if not df["_pct_chg"].isna().all():
                    try:
                        from sklearn.ensemble import IsolationForest
                        valid_idx = df["_pct_chg"].dropna().index
                        if len(valid_idx) > 50:
                            iso = IsolationForest(contamination=0.01, random_state=42)
                            preds = iso.fit_predict(df.loc[valid_idx, ["_pct_chg"]])
                            abnormal_mask.loc[valid_idx] = (preds == -1)
                        else:
                            # Fallback if too few rows for IsolationForest
                            abnormal_mask = (df["_pct_chg"] > 0.50) | (df["_pct_chg"] < -0.40)
                    except Exception as exc:
                        logger.debug("IsolationForest failed, falling back to static anomaly threshold: %s", exc)
                        abnormal_mask = (df["_pct_chg"] > 0.50) | (df["_pct_chg"] < -0.40)
                
                if abnormal_mask.any():
                    abnormal_tickers = df[abnormal_mask]["Ticker"].unique()
                    logger.warning("Sanity Outlier Filter triggered for: %s. Dropping abnormal rows.", abnormal_tickers.tolist())
                    df = df[~abnormal_mask]
                    
                df = df.drop(columns=["_pct_chg"])
                
                rows = db_manager.upsert_ohlcv(df)
                total_rows_upserted += rows
                n_tickers = df["Ticker"].nunique()
                logger.info(
                    "Successfully ingested %d rows for %d ticker(s) in chunk.", rows, n_tickers
                )
                
                if i + chunk_size < len(tickers):
                    time.sleep(random.uniform(3.5, 7.5))
            
            logger.info("Finished incremental update. Total rows upserted: %d", total_rows_upserted)
            return total_rows_upserted
        except Exception as exc:  # noqa: BLE001 - ingestion must never crash the daemon.
            logger.exception("Database update failed for tickers: %s", tickers)
            try:
                import sys
                from pathlib import Path
                _ROOT = Path(__file__).resolve().parent.parent
                if str(_ROOT / "01_memory_core") not in sys.path:
                    sys.path.insert(0, str(_ROOT / "01_memory_core"))
                from logging_setup import update_pipeline_status
                update_pipeline_status({"data_degraded_mode": True, "degraded_reason": f"market_prices_api.py: {exc}"})
            except Exception:
                pass
            return False

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    fetcher = MarketDataFetcher()
    sample = ["MC.PA", "OR.PA", "AI.PA"]
    frame = fetcher.fetch_daily_ohlcv(sample, lookback_days=30)

    print("\n--- Fetched shape:", frame.shape)
    print("--- Columns:", list(frame.columns))
    print("--- Tickers:", sorted(frame["Ticker"].unique()) if not frame.empty else [])
    print(frame.tail(10).to_string(index=False))
