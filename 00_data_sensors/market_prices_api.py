"""Market data ingestion for PEA Sniper Terminal V-Prime.

Fetches daily OHLCV via the official ``yfinance`` API (no scraping), flattens
the multi-ticker response into the schema expected by ``TimeSeriesDB``
(Phase 2), and feeds it into DuckDB.

This is a pure ingestion layer: no indicator math, risk, or trading logic.
"""

import logging
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
        self, tickers: List[str], lookback_days: int = 252
    ) -> pd.DataFrame:
        """Download and flatten daily OHLCV for a batch of tickers.

        All tickers are downloaded in a single batched ``yf.download`` call to
        avoid rate limits. The multi-index response is flattened into the
        columns ``Ticker, Date, Open, High, Low, Close, Volume``.

        Args:
            tickers: List of Yahoo Finance ticker symbols.
            lookback_days: Calendar days of history to request (default 252).

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

        try:
            raw = yf.download(
                tickers,
                start=start_date,
                progress=False,
                auto_adjust=False,
                group_by="column",
                threads=True,
            )
        except Exception:  # noqa: BLE001 - never let an API error crash caller.
            logger.exception("yf.download failed for tickers: %s", tickers)
            return pd.DataFrame(columns=_FLAT_COLUMNS)

        if raw is None or raw.empty:
            logger.warning("yf.download returned no data for: %s", tickers)
            return pd.DataFrame(columns=_FLAT_COLUMNS)

        flat = self._flatten(raw, tickers)
        if flat.empty:
            return flat

        return self._clean(flat)

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
        self, db_manager: Any, tickers: List[str], lookback_days: int = 252
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
            df = self.fetch_daily_ohlcv(tickers, lookback_days=lookback_days)
            if df.empty:
                logger.warning("No data fetched; nothing to ingest.")
                return False

            rows = db_manager.upsert_ohlcv(df)
            n_tickers = df["Ticker"].nunique()
            logger.info(
                "Successfully ingested %d rows for %d ticker(s).", rows, n_tickers
            )
            return True
        except Exception:  # noqa: BLE001 - ingestion must never crash the daemon.
            logger.exception("Database update failed for tickers: %s", tickers)
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
