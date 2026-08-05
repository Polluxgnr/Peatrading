import os
import sys
import logging
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
for _sub in ("00_data_sensors", "01_memory_core", "02_quant_engine", "03_risk_portfolio", "04_orchestrator_ai", "05_interfaces"):
    sys.path.insert(0, str(_ROOT / _sub))

from main_scheduler import _load_universe_tickers
from market_prices_api import MarketDataFetcher
from duckdb_manager import TimeSeriesDB
from logging_setup import setup_app_logging

def main():
    setup_app_logging()
    logger = logging.getLogger("backfill")
    logger.info("Starting ML data backfill...")

    tickers = _load_universe_tickers()
    if not tickers:
        logger.error("No tickers found in universe.")
        return

    logger.info(f"Loaded {len(tickers)} tickers. Filtering and starting download...")
    
    db_manager = TimeSeriesDB()
    fetcher = MarketDataFetcher()
    
    # Fetch 5-year history directly, bypassing the incremental gap-check in update_database
    df = fetcher.fetch_daily_ohlcv(tickers, lookback_days=1825)
    
    if not df.empty:
        rows_inserted = db_manager.upsert_ohlcv(df)
        logger.info(f"Backfill completed successfully: {rows_inserted} rows inserted into DuckDB.")
    else:
        logger.error("Backfill fetched no data.")

if __name__ == "__main__":
    main()
