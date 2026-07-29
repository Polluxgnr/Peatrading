"""ML Historical Bootstrapper for PEA Pollux.

Simulates the last 10 years to generate XGBoost training features.
Uses multiprocessing to scan tickers x 10 years efficiently.
"""

import concurrent.futures
import datetime
import logging
import os
import sys
from pathlib import Path
from typing import List, Dict

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "01_memory_core"))
sys.path.insert(0, str(_ROOT / "02_quant_engine"))
sys.path.insert(0, str(_ROOT / "00_data_sensors"))

from duckdb_manager import TimeSeriesDB
from technical_scorer import SignalGenerator
from ml_feature_store import build_ml_feature_row
import yaml

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Constants
START_DATE = datetime.datetime.now() - datetime.timedelta(days=365 * 10)
END_DATE = datetime.datetime.now() - datetime.timedelta(days=35)
STEP_DAYS = 5
MIN_ROWS = 252

def _process_ticker_dates(ticker: str) -> List[Dict]:
    """Evaluate historical dates for a single ticker."""
    tsdb = TimeSeriesDB(read_only=True)
    generator = SignalGenerator() 
    
    try:
        df = tsdb.get_historical_prices(ticker, days=4000)
    except Exception:
        return []
        
    if df is None or df.empty or "Close" not in df.columns or len(df) < MIN_ROWS:
        return []
        
    df = df.sort_values("Date")
    close_series = df["Close"].astype(float)
    
    results = []
    
    current_date = pd.to_datetime(START_DATE).tz_localize(None)
    end_date = pd.to_datetime(END_DATE).tz_localize(None)
    
    dates_to_check = []
    while current_date <= end_date:
        dates_to_check.append(current_date)
        current_date += datetime.timedelta(days=STEP_DAYS)
        
    df["Date_dt"] = pd.to_datetime(df["Date"]).dt.tz_localize(None)
    
    for d in dates_to_check:
        mask = df["Date_dt"] <= d
        valid_hist = df[mask]
        
        if len(valid_hist) < MIN_ROWS:
            continue
            
        asof_idx = len(valid_hist) - 1
        
        try:
            conv = generator.evaluate(ticker, valid_hist, macro_sensor=None)
            total = float(conv.get("total") or 0.0)
            
            if total >= 65.0:
                feat = build_ml_feature_row(
                    ticker,
                    close=close_series,
                    reason="historical bootstrap",
                    pdb=None,
                    asof_idx=asof_idx
                )
                if feat.get("label_fwd_gt_2pct") is not None and not pd.isna(feat["label_fwd_gt_2pct"]):
                    feat["conviction_score"] = total
                    results.append(feat)
        except Exception:
            continue
            
    return results

def load_universe_tickers() -> List[str]:
    """Parse config/pea_universe.yaml and return a flat list of tickers."""
    universe_path = _ROOT / "config" / "pea_universe.yaml"
    with open(universe_path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    
    tickers = []
    for sector, items in data.get("universe", {}).items():
        for item in items:
            tickers.append(item["ticker"])
    return tickers

def main() -> None:
    tickers = load_universe_tickers()
    logger.info(f"Loaded {len(tickers)} tickers for ML bootstrap.")
    
    all_features = []
    
    with concurrent.futures.ProcessPoolExecutor() as executor:
        futures = {executor.submit(_process_ticker_dates, ticker): ticker for ticker in tickers}
        completed = 0
        
        for future in concurrent.futures.as_completed(futures):
            ticker = futures[future]
            try:
                res = future.result()
                all_features.extend(res)
            except Exception as exc:
                logger.error(f"Ticker {ticker} generated an exception: {exc}")
            
            completed += 1
            if completed % 10 == 0:
                logger.info(f"Progress: {completed}/{len(tickers)} tickers processed. Collected {len(all_features)} signals.")
                
    if not all_features:
        logger.error("No features generated. Exiting.")
        return
        
    out_df = pd.DataFrame(all_features)
    out_path = _ROOT / "database" / "ml_training_dataset.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out_path, index=False)
    logger.info(f"Saved {len(out_df)} rows to {out_path}.")
    
    try:
        from ml_trainer import train_model
        logger.info("Training XGBoost model...")
        train_model(dataset_path=str(out_path))
        logger.info("Training complete.")
    except Exception as e:
        logger.exception("Failed to train model.")

if __name__ == "__main__":
    main()
