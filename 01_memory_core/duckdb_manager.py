"""DuckDB time-series engine for PEA Sniper Terminal V-Prime.

DuckDB stores heavy OHLCV history and serves fast columnar reads to the quant
engine (pandas-ta). This is a pure I/O layer: no indicator math, no trading
logic, no API fetching lives here.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

try:
    import duckdb
except ImportError:
    duckdb = None  # type: ignore[assignment]

import pandas as pd

logger = logging.getLogger(__name__)

# database/ lives at the project root (one level up from 01_memory_core/).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DB_PATH = _PROJECT_ROOT / "database" / "timeseries.duckdb"

# Canonical OHLCV column order used for inserts/reads.
_OHLCV_COLUMNS = ["Ticker", "Date", "Open", "High", "Low", "Close", "Volume"]


class TimeSeriesDB:
    """Persistence gateway for OHLCV time-series stored in DuckDB.

    Attributes:
        db_path: Absolute path to the DuckDB database file.
    """

    def __init__(self, db_path: Optional[Path | str] = None) -> None:
        """Initialize the manager and ensure the database directory exists.

        Args:
            db_path: Optional custom path to the DuckDB file. Defaults to
                ``<project_root>/database/timeseries.duckdb``.
        """
        self.db_path: Path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        logger.debug("TimeSeriesDB using database at %s", self.db_path)

    @contextmanager
    def _connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        """Yield a DuckDB connection that always closes.

        Yields:
            duckdb.DuckDBPyConnection: An open connection.

        Raises:
            duckdb.Error: Propagated if any DB error occurs.
        """
        if duckdb is None:
            raise RuntimeError("DuckDB package is not installed.")
        conn = duckdb.connect(str(self.db_path))
        try:
            yield conn
        except Exception:
            logger.exception("DuckDB operation failed.")
            raise
        finally:
            conn.close()

    def init_db(self) -> None:
        """Create the ``ohlcv_data`` table if it does not already exist.

        A composite primary key on ``(ticker, date)`` enforces one row per
        ticker per day and enables efficient upserts.
        """
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ohlcv_data (
                        ticker     VARCHAR NOT NULL,
                        date       DATE     NOT NULL,
                        open       DOUBLE,
                        high       DOUBLE,
                        low        DOUBLE,
                        close      DOUBLE,
                        volume     BIGINT,
                        is_outlier BOOLEAN DEFAULT FALSE,
                        PRIMARY KEY (ticker, date)
                    );
                    """
                )
                try:
                    conn.execute("ALTER TABLE ohlcv_data ADD COLUMN IF NOT EXISTS is_outlier BOOLEAN DEFAULT FALSE;")
                except Exception:
                    pass
            logger.info("DuckDB schema initialized at %s", self.db_path)
        except Exception:
            logger.exception("Failed to initialize DuckDB schema.")
            raise


    def upsert_ohlcv(self, df: pd.DataFrame) -> int:
        """Insert or replace OHLCV rows from a DataFrame through DataQualityGateway.

        Args:
            df: DataFrame with columns ``Ticker``, ``Date``, ``Open``, ``High``,
                ``Low``, ``Close`` and ``Volume`` (typically from yfinance).

        Returns:
            int: The number of rows submitted for upsert.

        Raises:
            ValueError: If required columns are missing.
            duckdb.Error: If the database operation fails.
        """
        if df is None or df.empty:
            logger.warning("upsert_ohlcv received an empty DataFrame; skipping.")
            return 0

        # Pass through DataQualityGateway for institutional hygiene & outlier tagging
        try:
            from data_quality import DataQualityGateway
            gateway = DataQualityGateway()
            payload = gateway.validate_ohlcv_batch(df)
        except Exception as exc:
            logger.warning("DataQualityGateway validation failed (%s); falling back to direct schema.", exc)
            missing = [c for c in _OHLCV_COLUMNS if c not in df.columns]
            if missing:
                raise ValueError(f"DataFrame missing required columns: {missing}")
            payload = df[_OHLCV_COLUMNS].copy()
            payload["Date"] = pd.to_datetime(payload["Date"]).dt.date
            payload["is_outlier"] = False

        if payload is None or payload.empty:
            logger.warning("DataQualityGateway rejected entire batch; 0 rows upserted.")
            return 0

        try:
            with self._connect() as conn:
                # Register the DataFrame so DuckDB can read it directly.
                conn.register("incoming_ohlcv", payload)
                conn.execute(
                    """
                    INSERT INTO ohlcv_data
                        (ticker, date, open, high, low, close, volume, is_outlier)
                    SELECT Ticker, Date, Open, High, Low, Close, Volume, is_outlier
                    FROM incoming_ohlcv
                    ON CONFLICT (ticker, date) DO UPDATE SET
                        open       = excluded.open,
                        high       = excluded.high,
                        low        = excluded.low,
                        close      = excluded.close,
                        volume     = excluded.volume,
                        is_outlier = excluded.is_outlier;
                    """
                )
                conn.unregister("incoming_ohlcv")
            logger.info("Upserted %d OHLCV rows into DuckDB.", len(payload))
            return len(payload)
        except duckdb.Error:
            logger.exception("Failed to upsert OHLCV data.")
            raise


    def get_historical_prices(self, ticker: str, days: int = 252) -> pd.DataFrame:
        """Fetch the most recent ``days`` of OHLCV for a ticker, chronologically.

        Args:
            ticker: The ticker symbol to query.
            days: Number of most-recent trading days to return (default 252).

        Returns:
            pd.DataFrame: Columns ``Ticker``, ``Date``, ``Open``, ``High``,
            ``Low``, ``Close``, ``Volume`` sorted ascending by date and ready
            for pandas-ta. Empty DataFrame (with correct columns) if none found.
        """
        try:
            with self._connect() as conn:
                # Take the last N rows by date, then re-sort ascending so the
                # output is chronological for indicator calculations.
                result = conn.execute(
                    """
                    SELECT ticker AS Ticker,
                           date   AS Date,
                           open   AS Open,
                           high   AS High,
                           low    AS Low,
                           close  AS Close,
                           volume AS Volume
                    FROM (
                        SELECT *
                        FROM ohlcv_data
                        WHERE ticker = ?
                        ORDER BY date DESC
                        LIMIT ?
                    )
                    ORDER BY date ASC;
                    """,
                    [ticker, days],
                ).fetch_df()
            logger.debug(
                "Fetched %d rows of history for %s.", len(result), ticker
            )
            if result.empty:
                return pd.DataFrame(columns=_OHLCV_COLUMNS)
            return result
        except duckdb.Error:
            logger.exception("Failed to fetch historical prices for %s.", ticker)
            raise
