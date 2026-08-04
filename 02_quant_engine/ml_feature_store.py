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


def _fundamentals(ticker: str, pdb: Any, offline_mode: bool = False) -> tuple[float, float]:
    """Return (roe, pe) from SQLite cache or Finnhub/yfinance sensor."""
    pe = np.nan
    roe = np.nan
    try:
        cached = pdb.get_cached_fundamentals(ticker, max_age_days=30) if pdb else None
        if cached:
            pe = _safe_float(cached.get("pe_ratio"))
            roe = _safe_float(cached.get("roe"))
            if np.isfinite(pe) or np.isfinite(roe):
                return roe, pe
    except Exception:  # noqa: BLE001
        pass
        
    if offline_mode:
        return np.nan, np.nan
        
    try:
        sys.path.insert(0, str(_ROOT / "00_data_sensors"))
        from fundamentals_api import FundamentalsSensor

        data = FundamentalsSensor().get_basic_financials(ticker)
        pe = _safe_float(data.get("pe_ratio"))
        roe = _safe_float(data.get("roe"))
    except Exception:  # noqa: BLE001
        pass
    return roe, pe


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
    roe, pe = _fundamentals(ticker, pdb, offline_mode=offline_mode)
    # Jalon 1 Macro Alpha Sensors
    from macro_alpha_api import MacroAlphaSensor
    macro = MacroAlphaSensor()
    short_interest = macro.get_short_interest(ticker)
    ecb_euribor = macro.get_ecb_euribor()
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
    
    # Meta-Labeling (Alpha prediction)
    label_tactical = np.nan
    label_structural = np.nan
    
    if cw8_close is not None and not cw8_close.empty and idx >= 0:
        # Align CW8 to the same index
        cw8_fwd_tactical = _forward_return(cw8_close, idx, _FORWARD_DAYS_TACTICAL)
        cw8_fwd_structural = _forward_return(cw8_close, idx, _FORWARD_DAYS_STRUCTURAL)
        
        if np.isfinite(fwd_tactical) and np.isfinite(cw8_fwd_tactical):
            label_tactical = int(fwd_tactical > cw8_fwd_tactical + 0.005)
            
        if np.isfinite(fwd_structural) and np.isfinite(cw8_fwd_structural):
            label_structural = int(fwd_structural > cw8_fwd_structural + 0.01) # higher threshold for 6m
    else:
        if np.isfinite(fwd_tactical):
            label_tactical = int(fwd_tactical > _TARGET_RETURN)
        if np.isfinite(fwd_structural):
            label_structural = int(fwd_structural > _TARGET_RETURN * 4.0)

    return {
        "asof_date": str(series.index[idx].date()) if hasattr(series.index[idx], 'date') else str(series.index[idx]),
        "ticker": ticker,
        "rsi14": rsi,
        "zscore_50": z50,
        "vol_20d_ann": vol,
        "insider_net_score": insider,
        "finnhub_roe": roe,
        "finnhub_pe": pe,
        "news_sentiment": news_sent,
        "amf_short_interest": short_interest,
        "ecb_euribor_3m": ecb_euribor,
        "gex_proxy": gex_proxy,
        "frac_diff_04": frac_val,
        "sp500_ret1d": spillover.get("^GSPC_ret1d", np.nan),
        "ndx_ret1d": spillover.get("^IXIC_ret1d", np.nan),
        "eurusd_ret1d": spillover.get("EURUSD=X_ret1d", np.nan),
        "oat_ret1d": spillover.get("OAT.PA_ret1d", np.nan),
        "target_tactical_30d": label_tactical,
        "target_structural_126d": label_structural,
    }


def build_ml_dataset(
    portfolio_db: Any | None = None,
    timeseries_db: Any | None = None,
    max_signals: int = 500,
) -> pd.DataFrame:
    """Build a feature matrix from audit_logs + OHLCV (+ news/fundamentals).

    Args:
        portfolio_db: ``PortfolioDB`` instance (created if None).
        timeseries_db: ``TimeSeriesDB`` instance (created if None).
        max_signals: Cap on audit rows scanned.

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

    try:
        rows = pdb.fetch_signals_by_status(
            ["APPROVED", "EXECUTED", "REJECTED", "PENDING", "REVOKED", "EXPIRED"],
            limit=max_signals,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not load audit_logs for ML dataset.")
        rows = []

    out: list[dict] = []
    seen: set[str] = set()
    for row in rows or []:
        ticker = str(row.get("ticker") or "").strip()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        try:
            hist = tdb.get_historical_prices(ticker, days=400)
        except Exception:  # noqa: BLE001
            hist = pd.DataFrame()
        close = (
            hist["Close"]
            if hist is not None and not hist.empty and "Close" in hist.columns
            else pd.Series(dtype=float)
        )
        feat = build_ml_feature_row(
            ticker,
            close=close,
            reason=str(row.get("reason") or ""),
            pdb=pdb,
        )
        feat["signal_status"] = str(row.get("status") or "")
        feat["signal_score"] = _safe_float(row.get("score"), 0.0)
        feat["created_at"] = str(row.get("created_at") or "")[:19]
        out.append(feat)

    if not out:
        return pd.DataFrame(
            columns=[
                "ticker", "rsi14", "zscore_50", "vol_20d_ann", "insider_net_score",
                "finnhub_roe", "finnhub_pe", "news_sentiment", "fwd_ret_30d",
                "label_fwd_gt_2pct", "signal_status", "signal_score", "created_at",
            ]
        )
    return pd.DataFrame(out)


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
