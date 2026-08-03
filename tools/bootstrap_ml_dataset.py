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
from tqdm import tqdm

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "01_memory_core"))
sys.path.insert(0, str(_ROOT / "02_quant_engine"))
sys.path.insert(0, str(_ROOT / "00_data_sensors"))

from duckdb_manager import TimeSeriesDB
from technical_scorer import SignalGenerator
from sqlite_portfolio import PortfolioDB
from ml_feature_store import build_ml_feature_row
import yaml

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Constants
START_DATE = datetime.datetime.now() - datetime.timedelta(days=365 * 10)
END_DATE = datetime.datetime.now() - datetime.timedelta(days=35)
STEP_DAYS = 5
MIN_ROWS = 252

# Global Worker State
PDB = None
GEN = None
TSDB = None
CW8_DF = None
EXOG_DF = {}

def init_worker():
    global PDB, GEN, TSDB, CW8_DF, EXOG_DF
    PDB = PortfolioDB()
    PDB.init_db()
    GEN = SignalGenerator(portfolio_db=PDB, macro_sensor=None, skip_regime=True, offline_mode=True)
    TSDB = TimeSeriesDB(read_only=True)
    try:
        CW8_DF = TSDB.get_historical_prices("CW8.PA", days=4000)
    except Exception:
        logger.warning("Could not load CW8.PA. Meta-labeling might fallback to absolute return.")
        CW8_DF = None
        
    for sym in ["^GSPC", "^IXIC", "EURUSD=X", "OAT.PA"]:
        try:
            EXOG_DF[sym] = TSDB.get_historical_prices(sym, days=4000)
        except Exception:
            EXOG_DF[sym] = None

def _process_ticker_dates(ticker: str, last_dt: datetime.datetime | None = None) -> List[Dict]:
    """Evaluate historical dates for a single ticker."""
    global GEN, PDB, TSDB
    
    try:
        df = TSDB.get_historical_prices(ticker, days=4000)
    except Exception:
        return []
        
    if df is None or df.empty or "Close" not in df.columns or len(df) < MIN_ROWS:
        return []
        
    df = df.sort_values("Date")
    close_series = df["Close"].astype(float)
    
    results = []
    
    current_date = pd.to_datetime(START_DATE).tz_localize(None)
    if last_dt is not None:
        current_date = max(current_date, last_dt + datetime.timedelta(days=1))
        
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
            conv = GEN.evaluate(ticker, valid_hist, macro_sensor=None, is_historical=True)
            total = float(conv.get("total") or 0.0)
            
            if total >= 65.0:
                cw8_close = CW8_DF["Close"].astype(float) if CW8_DF is not None and not CW8_DF.empty else None
                exog_closes = {sym: df["Close"].astype(float) for sym, df in EXOG_DF.items() if df is not None and not df.empty}
                feat = build_ml_feature_row(
                    ticker,
                    close=close_series,
                    cw8_close=cw8_close,
                    exog_closes=exog_closes,
                    reason="historical bootstrap",
                    pdb=PDB,
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
    
    out_path = _ROOT / "database" / "ml_training_dataset.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    
    init_worker()
    
    existing_df = None
    if out_path.exists():
        try:
            existing_df = pd.read_parquet(out_path)
            logger.info(f"Loaded existing dataset with {len(existing_df)} rows.")
        except Exception as e:
            logger.warning(f"Could not read existing parquet file: {e}")
            existing_df = None
    
    total_rows = 0
    new_rows_list = []
    
    for ticker in tqdm(tickers, desc="Evaluating Tickers"):
        try:
            last_dt = None
            if existing_df is not None and not existing_df.empty:
                t_df = existing_df[existing_df["ticker"] == ticker]
                if not t_df.empty and "asof_date" in t_df.columns:
                    last_dt = pd.to_datetime(t_df["asof_date"]).max()
                    
            res = _process_ticker_dates(ticker, last_dt=last_dt)
            if res:
                df = pd.DataFrame(res)
                # Drop NaN properly across features before saving
                df = df.dropna()
                if not df.empty:
                    new_rows_list.append(df)
                    total_rows += len(df)
        except Exception as exc:
            logger.warning(f"Ticker {ticker} generated an exception: {exc}")
            continue
            
    if new_rows_list:
        new_df = pd.concat(new_rows_list, ignore_index=True)
        if existing_df is not None and not existing_df.empty:
            final_df = pd.concat([existing_df, new_df], ignore_index=True)
        else:
            final_df = new_df
            
        import os
        tmp_path = out_path.with_suffix(".tmp.parquet")
        final_df.to_parquet(tmp_path, index=False)
        os.replace(tmp_path, out_path)
        logger.info(f"Appended {total_rows} new rows. Total dataset rows: {len(final_df)}.")
    else:
        logger.info("No new features generated. Dataset is up to date.")
        
    try:
        from ml_trainer import train_model
        logger.info("Training XGBoost model...")
        train_model(dataset_path=str(out_path))
        logger.info("Training complete.")
    except Exception as e:
        logger.exception("Failed to train model.")

if __name__ == "__main__":
    main()
