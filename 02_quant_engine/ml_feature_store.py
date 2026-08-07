"""Machine Learning feature store for PEA Pollux (Phase 40).

Builds a supervised training matrix from SQLite audit/news history and DuckDB
OHLCV. Pure offline engineering — no live trading side-effects.

Features
--------
RSI14, Z-Score 50, Volatility 20d, Insider Net Score, Finnhub ROE/PE,
News Sentiment Score (−100…+100).

Target
------
Binary label: 30-day forward return > 2.0% → 1, else 0.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "01_memory_core"))
sys.path.insert(0, str(_ROOT / "02_quant_engine"))

logger = logging.getLogger(__name__)

_DEFAULT_OUT = _ROOT / "database" / "ml_training_dataset.csv"
_FORWARD_DAYS_TACTICAL = 30
_FORWARD_DAYS_STRUCTURAL = 126
_TARGET_RETURN = 0.02


def _safe_float(x: Any, default: float = np.nan) -> float:
    try:
        if x is None or (isinstance(x, float) and np.isnan(x)):
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def _forward_return(close: pd.Series, asof_idx: int, days: int) -> float:
    """Return close[asof+days]/close[asof] - 1 when available."""
    if close is None or asof_idx < 0 or asof_idx + days >= len(close):
        return np.nan
    base = float(close.iloc[asof_idx])
    fut = float(close.iloc[asof_idx + days])
    if base <= 0 or not np.isfinite(base) or not np.isfinite(fut):
        return np.nan
    return fut / base - 1.0


def _insider_net_from_reason(reason: str) -> float:
    """Cheap proxy from audit reason text (−1…+1)."""
    blob = (reason or "").casefold()
    buys = sum(blob.count(w) for w in ("insider buy", "achat dirigeant", "cluster buy", "ins+"))
    sells = sum(blob.count(w) for w in ("insider sell", "vente dirigeant", "ins-"))
    if buys == sells == 0:
        if "insider" in blob and "buy" in blob:
            return 0.5
        return 0.0
    return float(np.clip((buys - sells) / max(1, buys + sells), -1.0, 1.0))


def _news_sentiment_proxy(ticker: str, pdb: Any) -> float:
    """Average heuristic sentiment from archived headlines (−100…+100)."""
    try:
        rows = pdb.get_news_history(ticker, limit=20) if pdb else []
    except Exception:  # noqa: BLE001
        rows = []
    if not rows:
        return 0.0
    pos = ("hausse", "record", "croissance", "beat", "upgrade", "rachat", "dividende")
    neg = ("baisse", "chute", "perte", "downgrade", "licenciement", "fraude", "amende")
    scores = []
    for r in rows:
        title = str(r.get("title") or "").casefold()
        s = 0
        s += 20 * sum(1 for w in pos if w in title)
        s -= 20 * sum(1 for w in neg if w in title)
        # Prefer stored sentiment when present
        raw = r.get("sentiment_score")
        if raw is not None:
            try:
                scores.append(float(raw))
                continue
            except (TypeError, ValueError):
                pass
        scores.append(float(np.clip(s, -100, 100)))
    return float(np.mean(scores)) if scores else 0.0

def get_daily_sentiment(pdb: Any) -> pd.DataFrame:
    """Fetch all scored news from master, group by Ticker and Date, and calculate 3-day rolling sentiment."""
    if not pdb:
        return pd.DataFrame()
        
    try:
        import pandas as pd
        with pdb._connect() as conn:
            df_news = pd.read_sql("SELECT ticker, published_at, sentiment_score FROM news_master WHERE sentiment_score IS NOT NULL AND ticker IS NOT NULL", conn)
        
        if df_news.empty:
            return pd.DataFrame()
            
        df_news['Date'] = pd.to_datetime(df_news['published_at']).dt.tz_localize(None).dt.floor('D')
        
        # Group by Ticker and Date
        daily_sent = df_news.groupby(['ticker', 'Date'])['sentiment_score'].mean().reset_index()
        daily_sent = daily_sent.sort_values(['ticker', 'Date'])
        
        # Calculate 3-day rolling average per ticker
        daily_sent['news_sentiment_3d'] = daily_sent.groupby('ticker')['sentiment_score'].transform(
            lambda x: x.rolling(window=3, min_periods=1).mean()
        )
        return daily_sent[['ticker', 'Date', 'news_sentiment_3d']]
    except Exception as e:
        logger.warning("Failed to calculate rolling sentiment: %s", e)
        return pd.DataFrame()


def _fundamentals(ticker: str, pdb: Any, offline_mode: bool = False) -> tuple[float, float, float]:
    """Return (roe, pe, ev_to_ebitda) from SQLite cache or Finnhub/yfinance sensor."""
    pe = np.nan
    roe = np.nan
    ev_ebitda = np.nan
    try:
        cached = pdb.get_cached_fundamentals(ticker, max_age_days=30) if pdb else None
        if cached:
            pe = _safe_float(cached.get("pe_ratio"))
            roe = _safe_float(cached.get("roe"))
            ev_ebitda = _safe_float(cached.get("ev_to_ebitda"))
            if np.isfinite(pe) or np.isfinite(roe) or np.isfinite(ev_ebitda):
                return roe, pe, ev_ebitda
    except Exception:  # noqa: BLE001
        pass
        
    if offline_mode:
        return np.nan, np.nan, np.nan
        
    try:
        sys.path.insert(0, str(_ROOT / "00_data_sensors"))
        from fundamentals_api import FundamentalsSensor

        data = FundamentalsSensor().get_basic_financials(ticker)
        pe = _safe_float(data.get("pe_ratio"))
        roe = _safe_float(data.get("roe"))
        ev_ebitda = _safe_float(data.get("ev_to_ebitda"))
    except Exception:  # noqa: BLE001
        pass
    return roe, pe, ev_ebitda


def build_ml_feature_row(
    ticker: str,
    *,
    close: pd.Series | None = None,
    cw8_close: pd.Series | None = None,
    exog_closes: dict[str, pd.Series] | None = None,
    reason: str = "",
    pdb: Any = None,
    asof_idx: int | None = None,
    offline_mode: bool = False,
    sector_mean_ret1d: float = 0.0,
) -> dict:
    """Engineer one feature row for ``ticker``."""
    series = close.astype(float).dropna() if close is not None else pd.Series(dtype=float)
    idx = asof_idx if asof_idx is not None else (len(series) - 1 if len(series) else -1)
    hist = series.iloc[: idx + 1] if idx >= 0 else series

    # DRY Feature Engineering via SignalGenerator
    df_hist = pd.DataFrame({"Close": hist, "High": hist, "Low": hist})
    try:
        sys.path.insert(0, str(_ROOT / "02_quant_engine"))
        from technical_scorer import SignalGenerator
        # calculate_indicators expects DataFrame with Close, High, Low
        enriched = SignalGenerator(skip_regime=True, offline_mode=True).calculate_indicators(df_hist)
        last_row = enriched.iloc[-1]
        rsi = float(last_row.get("RSI_14", np.nan))
        z50 = float(last_row.get("Z_SCORE_50", np.nan))
    except Exception:
        rsi = np.nan
        z50 = np.nan

    # Volatility (20-day annualized)
    vol = np.nan
    if len(hist) >= 21:
        rets = hist.pct_change().dropna().tail(20)
        if not rets.empty:
            vol = float(rets.std(ddof=0) * np.sqrt(252.0))
    insider = _insider_net_from_reason(reason)
    news_sent = _news_sentiment_proxy(ticker, pdb)
    roe, pe, ev_ebitda = _fundamentals(ticker, pdb, offline_mode=offline_mode)
    
    # Apex Alpha: FMP Earnings Call Q&A
    qa_score = 0.0
    if not offline_mode:
        try:
            sys.path.insert(0, str(_ROOT / "04_orchestrator_ai"))
            from news_sentiment_llm import NewsSentimentScorer
            import asyncio
            # Create a new event loop for this block if needed, or use asyncio.run
            qa_score = float(asyncio.run(NewsSentimentScorer().analyze_earnings_call_qa(ticker)))
        except Exception:
            pass
            
    # Jalon 1 Macro Alpha Sensors
    import sys, os
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '00_data_sensors')))
    from macro_alpha_api import MacroAlphaSensor
    macro = MacroAlphaSensor()
    short_interest = macro.get_short_interest(ticker) if not offline_mode else 0.0
    ecb_euribor = macro.get_ecb_euribor() if not offline_mode else 0.0
    threshold_cross = macro.get_threshold_crossings(ticker) if not offline_mode else 0
    gex_proxy = macro.get_gamma_exposure(ticker) if not offline_mode else 0.0
    from quantitative_math import frac_diff_ffd
    
    # Fractional Differentiation feature (d=0.4)
    # Computed dynamically if we have enough history.
    frac_val = np.nan
    if len(series) >= 20:
        frac_series = frac_diff_ffd(series, d=0.4)
        if not frac_series.empty and idx >= 0:
            frac_val = float(frac_series.iloc[idx])
            
    # Cross-Asset Spillover features
    spillover = {}
    if exog_closes:
        for sym, exog_s in exog_closes.items():
            if not exog_s.empty and idx >= 0:
                # Return over last 1 day
                # Since exog_s may not be perfectly aligned by index position, we should match by date
                # but since we only have `idx` for the `close` series, this is tricky. 
                # Let's assume idx maps to the same trailing window.
                try:
                    # simplistic approach: take the last available pct_change up to the date of `series.index[idx]`
                    target_date = series.index[idx]
                    exog_sub = exog_s[exog_s.index <= target_date]
                    if len(exog_sub) >= 2:
                        spillover[f"{sym}_ret1d"] = float(exog_sub.iloc[-1] / exog_sub.iloc[-2] - 1.0)
                    else:
                        spillover[f"{sym}_ret1d"] = np.nan
                except Exception:
                    spillover[f"{sym}_ret1d"] = np.nan
                    
    fwd_tactical = _forward_return(series, idx, _FORWARD_DAYS_TACTICAL) if idx >= 0 else np.nan
    fwd_structural = _forward_return(series, idx, _FORWARD_DAYS_STRUCTURAL) if idx >= 0 else np.nan
    
    # Meta-Labeling (Triple Barrier Method for Tactical)
    label_tactical = np.nan
    label_structural = np.nan
    
    if idx >= 0:
        # Tactical Triple Barrier Method (30 days, +8% profit, -4% stop)
        horizon_len = min(_FORWARD_DAYS_TACTICAL, len(series) - 1 - idx)
        if horizon_len > 0:
            horizon = series.iloc[idx+1 : idx+1+horizon_len]
            base_price = float(series.iloc[idx])
            if base_price > 0:
                path_ret = horizon / base_price - 1.0
                
                # Check barriers
                hit_upper = path_ret >= 0.08
                hit_lower = path_ret <= -0.04
                
                upper_idx = hit_upper.idxmax() if hit_upper.any() else None
                lower_idx = hit_lower.idxmax() if hit_lower.any() else None
                
                if upper_idx is not None and lower_idx is not None:
                    # Which barrier was hit first?
                    if path_ret.index.get_loc(upper_idx) < path_ret.index.get_loc(lower_idx):
                        label_tactical = 1
                    else:
                        label_tactical = 0
                elif upper_idx is not None:
                    label_tactical = 1
                else:
                    label_tactical = 0
                    
        # Structural labeling (fallback to fixed horizon > +8% for 126d)
        if np.isfinite(fwd_structural):
            label_structural = int(fwd_structural > _TARGET_RETURN * 4.0)

    ticker_ret1d = 0.0
    if len(series) >= 2 and idx >= 1:
        base = float(series.iloc[idx - 1])
        if base > 0:
            ticker_ret1d = float(series.iloc[idx] / base - 1.0)
    sector_rel_ret = ticker_ret1d - sector_mean_ret1d

    return {
        "asof_date": str(series.index[idx].date()) if hasattr(series.index[idx], 'date') else str(series.index[idx]),
        "ticker": ticker,
        "rsi14": rsi,
        "zscore_50": z50,
        "vol_20d_ann": vol,
        "insider_net_score": insider,
        "finnhub_roe": roe,
        "finnhub_pe": pe,
        "ev_to_ebitda": ev_ebitda,
        "news_sentiment": news_sent,
        "earnings_qa_sentiment": qa_score,
        "amf_short_interest": short_interest,
        "amf_threshold_crossing": threshold_cross,
        "ecb_euribor_3m": ecb_euribor,
        "gex_proxy": gex_proxy,
        "frac_diff_04": frac_val,
        "sp500_ret1d": spillover.get("^GSPC_ret1d", np.nan),
        "ndx_ret1d": spillover.get("^IXIC_ret1d", np.nan),
        "eurusd_ret1d": spillover.get("EURUSD=X_ret1d", np.nan),
        "oat_ret1d": spillover.get("OAT.PA_ret1d", np.nan),
        "sector_relative_ret1d": sector_rel_ret,
        "target_tactical_30d": label_tactical,
        "target_structural_126d": label_structural,
    }


def build_training_dataset(
    portfolio_db: Any | None = None,
    timeseries_db: Any | None = None,
) -> pd.DataFrame:
    """Build a feature matrix from OHLCV historical sampling (Offline Mode) via Vectorization.

    Returns:
        DataFrame ready for XGBoost/NLP training.
    """
    from duckdb_manager import TimeSeriesDB
    from sqlite_portfolio import PortfolioDB

    pdb = portfolio_db or PortfolioDB()
    try:
        pdb.init_db()
    except Exception:  # noqa: BLE001
        pass
    tdb = timeseries_db or TimeSeriesDB(read_only=True)

    # 1. Load Data with Polars directly from DuckDB
    try:
        with tdb._connect() as conn:
            # We explicitly cast to ensure stable Polars types
            df_ohlcv = conn.sql("SELECT Date, Ticker, Close, High, Low FROM ohlcv_data").pl()
    except Exception:
        logger.exception("Could not fetch OHLCV from DuckDB into Polars.")
        return pd.DataFrame()

    # Filter Valid Tickers
    valid_suffixes = (".PA", ".AS", ".NX", ".MI", ".MC", ".LS")
    def is_valid_ticker(t: str) -> bool:
        if "IR3TIB" in t or t.endswith(".EM") or t.endswith(".INDX"):
            return False
        return any(t.endswith(s) for s in valid_suffixes) or t.isalpha()

    unique_tickers = df_ohlcv.get_column("Ticker").unique().to_list()
    valid_tickers = [t for t in unique_tickers if is_valid_ticker(t)]
    macro_symbols = ["^GSPC", "^IXIC", "EURUSD=X", "OAT.PA"]

    import polars as pl
    df_main = df_ohlcv.filter(pl.col("Ticker").is_in(valid_tickers)).sort(["Ticker", "Date"])
    df_macro = df_ohlcv.filter(pl.col("Ticker").is_in(macro_symbols)).sort(["Ticker", "Date"])

    logger.info("Building historical ML dataset for %d valid equity tickers (Polars Vectorized)...", len(valid_tickers))

    # Calculate Macro Returns
    df_macro = df_macro.with_columns([
        (pl.col("Close") / pl.col("Close").shift(1).over("Ticker") - 1.0).alias("macro_ret1d")
    ]).drop_nulls(subset=["macro_ret1d"])

    # Pivot Macro
    try:
        macro_pivoted = df_macro.pivot(
            values="macro_ret1d",
            index="Date",
            on="Ticker",
            aggregate_function="first"
        )
    except Exception:
        # Fallback if pivot fails or no macro
        macro_pivoted = pl.DataFrame({"Date": []})

    # Polars RSI implementation
    def _pl_rsi(price: pl.Expr, n: int = 14) -> pl.Expr:
        delta = price.diff()
        up = pl.when(delta > 0).then(delta).otherwise(0.0)
        down = pl.when(delta < 0).then(delta.abs()).otherwise(0.0)
        roll_up = up.ewm_mean(alpha=1.0/n, adjust=False)
        roll_down = down.ewm_mean(alpha=1.0/n, adjust=False)
        rs = roll_up / roll_down
        return 100.0 - (100.0 / (1.0 + rs))

    # Core Features & Targets Calculation
    df_main = df_main.with_columns([
        pl.col("Close").rolling_mean(window_size=50).over("Ticker").alias("sma50"),
        pl.col("Close").rolling_std(window_size=50, ddof=0).over("Ticker").alias("std50"),
        _pl_rsi(pl.col("Close"), 14).over("Ticker").alias("rsi14"),
        (pl.col("Close").pct_change().over("Ticker") * np.sqrt(252.0)).rolling_std(window_size=20, ddof=0).over("Ticker").alias("vol_20d_ann"),
        (pl.col("Close").shift(-30).over("Ticker") / pl.col("Close") - 1.0).alias("target_tactical_30d"),
        (pl.col("Close").shift(-126).over("Ticker") / pl.col("Close") - 1.0).alias("target_structural_126d"),
        pl.col("Close").pct_change().over("Ticker").alias("ret1d")
    ])

    df_main = df_main.with_columns([
        ((pl.col("Close") - pl.col("sma50")) / pl.col("std50")).alias("zscore_50"),
        pl.int_range(0, pl.len()).over("Ticker").alias("row_nr")
    ])

    # Sample rows (skip first 200, then every 5th)
    df_sampled = df_main.filter(
        (pl.col("row_nr") >= 200) & (pl.col("row_nr") % 5 == 0)
    )

    # Drop null targets
    df_sampled = df_sampled.drop_nulls(subset=["target_tactical_30d", "target_structural_126d"])

    # Join Macro
    if not macro_pivoted.is_empty():
        df_sampled = df_sampled.join(macro_pivoted, on="Date", how="left")
    
    # Fill remaining macro columns if they don't exist
    for m in macro_symbols:
        if m not in df_sampled.columns:
            df_sampled = df_sampled.with_columns(pl.lit(0.0).alias(m))

    # Rename & Fill Nulls
    df_sampled = df_sampled.rename({
        "^GSPC": "sp500_ret1d",
        "^IXIC": "ndx_ret1d",
        "EURUSD=X": "eurusd_ret1d",
        "OAT.PA": "oat_ret1d",
        "Ticker": "ticker"
    }).fill_null(0.0)

    # Add extra constant columns
    df_sampled = df_sampled.with_columns([
        pl.col("Date").dt.strftime("%Y-%m-%d %H:%M:%S").alias("created_at"),
        pl.lit("HISTORICAL").alias("signal_status"),
        pl.lit(0.0).alias("signal_score"),
        pl.lit(0.0).alias("insider_net_score"),
        pl.lit(0.0).alias("earnings_qa_sentiment"),
        pl.lit(0.0).alias("amf_short_interest"),
        pl.lit(0.0).alias("amf_threshold_crossing"),
        pl.lit(0.0).alias("ecb_euribor_3m"),
        pl.lit(0.0).alias("gex_proxy")
    ])
    
    # Drop temp columns
    df_sampled = df_sampled.drop(["sma50", "std50", "row_nr"])
    
    # Convert back to Pandas for complex offline fundamentals / frac diff
    df = df_sampled.to_pandas()
    
    logger.info("Polars processing complete. Enriching with Static Fundamentals and Frac Diff in Pandas. Rows: %d", len(df))
    
    # Fundamentals Enrichment
    df["finnhub_roe"] = np.nan
    df["finnhub_pe"] = np.nan
    df["ev_to_ebitda"] = np.nan
    df["news_sentiment"] = 0.0
    
    # To avoid looping every row, map by ticker
    fund_cache = {}
    for t in df["ticker"].unique():
        roe, pe, ev_ebitda = _fundamentals(t, pdb, offline_mode=True)
        ns = _news_sentiment_proxy(t, pdb)
        fund_cache[t] = (roe, pe, ev_ebitda, ns)
    
    df["finnhub_roe"] = df["ticker"].map(lambda t: fund_cache[t][0])
    df["finnhub_pe"] = df["ticker"].map(lambda t: fund_cache[t][1])
    df["ev_to_ebitda"] = df["ticker"].map(lambda t: fund_cache[t][2])
    df["news_sentiment"] = df["ticker"].map(lambda t: fund_cache[t][3])
    
    # Frac Diff FFD (expensive, applied per ticker)
    sys.path.insert(0, str(_ROOT / "02_quant_engine"))
    from quantitative_math import frac_diff_ffd
    df["frac_diff_04"] = df.groupby("ticker")["Close"].transform(lambda x: frac_diff_ffd(x, d=0.4))

    # StatArb Sector Relative Return
    try:
        import yaml
        with open(_ROOT / "config" / "pea_universe.yaml", "r", encoding="utf-8") as f:
            uni = yaml.safe_load(f).get("universe", {})
        ticker_to_sector = {}
        for sector, items in uni.items():
            for item in items:
                ticker_to_sector[item["ticker"]] = sector
                
        df['sector'] = df['ticker'].map(ticker_to_sector)
        sector_mean = df.groupby(['Date', 'sector'])['ret1d'].mean().reset_index()
        sector_mean = sector_mean.rename(columns={'ret1d': 'sector_mean_ret1d'})
        
        df = pd.merge(df, sector_mean, on=['Date', 'sector'], how='left')
        df['sector_relative_ret1d'] = df['ret1d'] - df['sector_mean_ret1d']
        df['sector_relative_ret1d'] = df['sector_relative_ret1d'].fillna(0.0)
        df = df.drop(columns=['sector', 'ret1d', 'sector_mean_ret1d'])
    except Exception as e:
        logger.warning("StatArb sector relative logic failed: %s", e)
        df['sector_relative_ret1d'] = 0.0

    # Merge Daily Sentiment (3-day rolling average)
    try:
        df_sent = get_daily_sentiment(pdb)
        if not df_sent.empty:
            df['Date'] = pd.to_datetime(df['Date'])
            df = pd.merge(df, df_sent, on=['ticker', 'Date'], how='left')
            df['news_sentiment_3d'] = df['news_sentiment_3d'].fillna(0.0)
        else:
            df['news_sentiment_3d'] = 0.0
    except Exception as e:
        logger.warning("Failed to merge daily sentiment: %s", e)
        df['news_sentiment_3d'] = 0.0

    df = df.sort_values(['ticker', 'Date']).reset_index(drop=True)
    df = df.dropna(subset=['target_tactical_30d', 'target_structural_126d'])
    logger.info("Dataset shape after dropping NaN targets: %s", df.shape)

    return df

def build_ml_dataset(portfolio_db=None, timeseries_db=None, max_signals=500):
    """Wrapper to maintain backwards compatibility while forcing training dataset gen."""
    return build_training_dataset(portfolio_db, timeseries_db)


def export_ml_dataset_csv(
    path: Path | str | None = None,
    portfolio_db: Any | None = None,
    timeseries_db: Any | None = None,
) -> Path:
    """Build the feature matrix and write it to CSV.

    Returns:
        Path to the written CSV file.
    """
    out = Path(path) if path else _DEFAULT_OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    df = build_ml_dataset(portfolio_db=portfolio_db, timeseries_db=timeseries_db)
    df.to_csv(out, index=False, encoding="utf-8")
    logger.info("ML dataset exported → %s (%d rows).", out, len(df))
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    p = export_ml_dataset_csv()
    print(f"Wrote {p}")
