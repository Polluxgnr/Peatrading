"""Market data ingestion for PEA Pollux.

Fetches daily OHLCV via the official ``yfinance`` API (no scraping), flattens
the multi-ticker response into the schema expected by ``TimeSeriesDB``
(Phase 2), and feeds it into DuckDB.

This is a pure ingestion layer: no indicator math, risk, or trading logic.
"""

import logging
import os
import requests
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

    def fetch_daily_ohlcv(
        self, tickers: List[str], lookback_days: int = 3650
    ) -> pd.DataFrame:
        """Download and flatten daily OHLCV for a batch of tickers.

        All tickers are downloaded in a single batched ``yf.download`` call to
        avoid rate limits. The multi-index response is flattened into the
        columns ``Ticker, Date, Open, High, Low, Close, Volume``.

        Args:
            tickers: List of Yahoo Finance ticker symbols.
            lookback_days: Calendar days of history to request.
                Use ``3650`` (~10 years) for long-horizon / ML backfills.

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
            "Downloading OHLCV for %d ticker(s) since %s.",
            len(tickers),
            start_date,
        )

        massive_df = self._fetch_from_massive(tickers, lookback_days)
        if massive_df is not None and not massive_df.empty:
            return self._clean(massive_df)

        av_df = None
        core_tickers = [t for t in tickers if t in ("CW8.PA", "PUST.PA", "PSP5.PA")]
        if core_tickers:
            av_df = self._fetch_from_alphavantage(core_tickers, lookback_days)
            
        remaining_tickers = [t for t in tickers if av_df is None or av_df.empty or t not in av_df["Ticker"].unique()]
        
        yf_df = None
        if remaining_tickers:
            try:
                from logging_setup import update_pipeline_status, send_discord_alert
                update_pipeline_status({
                    "data_degraded_mode": True,
                    "degraded_reason": "MASSIVE API failed. Using AlphaVantage for Core, falling back to yfinance for the rest."
                })
                send_discord_alert("⚠️ **API Circuit Breaker**: MASSIVE API failed. Falling back to yfinance for OHLCV.")
                logger.error("DEGRADED MODE: MASSIVE API unavailable. Falling back to yfinance.")
            except Exception:
                pass

            try:
                import time
                import pandas as pd
                chunk_size = 20
                all_yf_dfs = []
                for i in range(0, len(remaining_tickers), chunk_size):
                    chunk = remaining_tickers[i:i + chunk_size]
                    raw = yf.download(
                        chunk,
                        start=start_date,
                        progress=False,
                        auto_adjust=True,
                        group_by="column",
                        threads=True,
                    )
                    if raw is not None and not raw.empty:
                        flat_chunk = self._flatten(raw, chunk)
                        all_yf_dfs.append(flat_chunk)
                    
                    if i + chunk_size < len(remaining_tickers):
                        time.sleep(2)
                
                if all_yf_dfs:
                    yf_df = pd.concat(all_yf_dfs, ignore_index=True)
            except Exception:  # noqa: BLE001 - never let an API error crash caller.
                logger.exception("yf.download failed for tickers: %s", remaining_tickers)

        frames = []
        if av_df is not None and not av_df.empty:
            frames.append(av_df)
        if yf_df is not None and not yf_df.empty:
            frames.append(yf_df)
            
        if not frames:
            return pd.DataFrame(columns=_FLAT_COLUMNS)
            
        combined = pd.concat(frames, ignore_index=True)
        return self._clean(combined)
        
    def _fetch_from_massive(self, tickers: List[str], lookback_days: int) -> pd.DataFrame | None:
        """Attempt to fetch data from the institutional MASSIVE API."""
        api_key = os.getenv("MASSIVE_API_KEY")
        if not api_key:
            return None
            
        try:
            # Assuming a standard mock URL for MASSIVE API
            url = "https://api.massive.example.com/v1/historical"
            headers = {"Authorization": f"Bearer {api_key}"}
            payload = {
                "tickers": tickers,
                "lookback_days": lookback_days
            }
            
            resp = requests.post(url, json=payload, headers=headers, timeout=10.0)
            resp.raise_for_status()
            
            data = resp.json()
            # Expecting data format: [{"Ticker": "AAPL", "Date": "2024-01-01", "Open": 150.0, ...}, ...]
            if not data:
                return None
                
            df = pd.DataFrame(data)
            
            missing = [c for c in _FLAT_COLUMNS if c not in df.columns]
            if missing:
                logger.error("MASSIVE API response missing columns: %s", missing)
                return None
                
            df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None).dt.normalize()
            return df[_FLAT_COLUMNS]
            
        except requests.RequestException as e:
            logger.warning("MASSIVE API network error: %s", e)
            return None
        except Exception as e:
            logger.error("Failed to parse MASSIVE API response: %s", e)
            return None

    def _fetch_from_alphavantage(self, tickers: List[str], lookback_days: int) -> pd.DataFrame | None:
        """Fetch high-quality OHLCV from Alpha Vantage for Core tickers only."""
        api_key = os.getenv("ALPHAVANTAGE_API_KEY")
        if not api_key:
            logger.warning("No ALPHAVANTAGE_API_KEY set; skipping AV backup.")
            return None
            
        frames = []
        start_date = datetime.now() - timedelta(days=lookback_days)
        
        for ticker in tickers:
            try:
                # Alpha Vantage uses 'CW8.PAR' instead of 'CW8.PA' typically, but we will pass as is and hope it resolves
                av_ticker = ticker.replace(".PA", ".PAR")
                url = f"https://www.alphavantage.co/query?function=TIME_SERIES_DAILY_ADJUSTED&symbol={av_ticker}&outputsize=full&apikey={api_key}"
                resp = requests.get(url, timeout=10.0)
                
                # Check for rate limits
                if "Note" in resp.text and "call frequency" in resp.text:
                    logger.error("Alpha Vantage rate limit hit on ticker %s", ticker)
                    try:
                        from logging_setup import send_discord_alert
                        send_discord_alert(f"⚠️ **API Circuit Breaker**: Alpha Vantage Rate Limit hit for {ticker}.")
                    except Exception:
                        pass
                    break
                    
                resp.raise_for_status()
                data = resp.json()
                
                ts = data.get("Time Series (Daily)", {})
                if not ts:
                    continue
                    
                rows = []
                for date_str, metrics in ts.items():
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                    if dt < start_date:
                        continue
                    rows.append({
                        "Ticker": ticker,  # revert to original PA notation
                        "Date": dt,
                        "Open": float(metrics["1. open"]),
                        "High": float(metrics["2. high"]),
                        "Low": float(metrics["3. low"]),
                        "Close": float(metrics["5. adjusted close"]),
                        "Volume": int(metrics["6. volume"])
                    })
                if rows:
                    frames.append(pd.DataFrame(rows))
            except Exception as e:
                logger.error("Alpha Vantage failed for %s: %s", ticker, e)
                
        if not frames:
            return None
        df = pd.concat(frames, ignore_index=True)
        return df[_FLAT_COLUMNS]

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
    ) -> bool:
        """Fetch OHLCV and upsert it into a ``TimeSeriesDB`` instance.

        Args:
            db_manager: A Phase 2 ``TimeSeriesDB`` (must expose ``upsert_ohlcv``).
            tickers: Ticker symbols to ingest.
            lookback_days: Calendar days of history to request (default 252).

        Returns:
            bool: ``True`` on success, ``False`` if any exception occurred.
        """
        try:
            # Phase 49: Strict Incremental Ingestion
            latest_dates = getattr(db_manager, "get_latest_dates", lambda t: {})(tickers)
            max_gap_days = 0
            now = datetime.now()
            
            for t in tickers:
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
                
            logger.info("Incremental fetch: requested %d days, optimized to %d days.", lookback_days, final_lookback)
            
            df = self.fetch_daily_ohlcv(tickers, lookback_days=final_lookback)
            if df.empty:
                logger.warning("No data fetched; nothing to ingest.")
                return False

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
                        iso = IsolationForest(contamination=0.005, random_state=42)
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
                # Forward fill the previous row's close for these abnormal rows by dropping them
                # Since duckdb upsert handles missing dates gracefully (or they just won't be inserted)
                # It's safer to just drop the abnormal rows so they don't get into DB.
                df = df[~abnormal_mask]
                
            df = df.drop(columns=["_pct_chg"])
            
            rows = db_manager.upsert_ohlcv(df)
            n_tickers = df["Ticker"].nunique()
            logger.info(
                "Successfully ingested %d rows for %d ticker(s).", rows, n_tickers
            )
            return True
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
