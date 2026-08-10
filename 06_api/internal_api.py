"""PEA Pollux — Institutional Recommendation Gateway (Internal API).

Single Source of Truth (SSOT) serving deterministic quantitative state,
algorithmic trade recommendations, multi-horizon data context, and health checks.

The system does NOT execute trades autonomously; it exclusively produces
data-backed Quantitative Recommendations for human portfolio managers.

Endpoints:
  - GET /api/v1/portfolio/summary
  - GET /api/v1/recommendations/pending
  - GET /api/v1/data/ticker/{symbol}/context
  - GET /api/v1/system/health
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Path as FastPath, Query
from fastapi.middleware.cors import CORSMiddleware

# Setup path imports for all engine layers
_ROOT = Path(__file__).resolve().parent.parent
for d in ("00_data_sensors", "01_memory_core", "02_quant_engine", "03_risk_portfolio", "04_orchestrator_ai", "05_interfaces"):
    sys.path.insert(0, str(_ROOT / d))

from data_models import PortfolioState, Signal, SignalStatus, SignalType
from sqlite_portfolio import PortfolioDB
from macro_alpha_api import MacroAlphaSensor
from hmm_regime import HMMRegimeClassifier, MarketRegimeState

logger = logging.getLogger(__name__)

# Singletons
_PORTFOLIO_DB = PortfolioDB()
try:
    _PORTFOLIO_DB.init_db()
except Exception as exc:
    logger.warning("Could not auto-init PortfolioDB: %s", exc)

_MACRO_SENSOR = MacroAlphaSensor()
_HMM_CLASSIFIER = HMMRegimeClassifier("^FCHI")

app = FastAPI(
    title="PEA Pollux — Quantitative Recommendation API",
    description="Deterministic quantitative state and portfolio recommendation engine.",
    version="1.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root() -> Dict[str, str]:
    """Health check and paradigm affirmation."""
    return {
        "engine": "PEA Pollux Systematic Quantitative Engine",
        "paradigm": "Quantitative Recommendations (Human Execution Authority)",
        "status": "OPERATIONAL",
        "docs_url": "/docs",
        "version": "1.1.0",
    }


@app.get("/api/v1/portfolio/summary", response_model=Dict[str, Any])
def get_portfolio_summary() -> Dict[str, Any]:
    """Return cash balance, total portfolio equity, active exposure %, and open holdings."""
    try:
        state: PortfolioState = _PORTFOLIO_DB.get_portfolio_state()
        tot_eq = float(state.total_equity)
        cash = float(state.cash_available)
        invested = tot_eq - cash if tot_eq >= cash else 0.0
        exposure_pct = round((invested / tot_eq * 100.0), 2) if tot_eq > 0 else 0.0
        cash_ratio_pct = round((cash / tot_eq * 100.0), 2) if tot_eq > 0 else 100.0

        positions_list = []
        for p in state.positions:
            mkt_val = round(p.qty_shares * p.current_price, 2)
            pnl_eur = round((p.current_price - p.avg_entry_price) * p.qty_shares, 2)
            pnl_pct = round(((p.current_price / p.avg_entry_price) - 1.0) * 100.0, 2) if p.avg_entry_price > 0 else 0.0
            weight_pct = round((mkt_val / tot_eq * 100.0), 2) if tot_eq > 0 else 0.0

            positions_list.append({
                "ticker": p.ticker,
                "qty_shares": p.qty_shares,
                "avg_entry_price": round(p.avg_entry_price, 2),
                "current_price": round(p.current_price, 2),
                "market_value": mkt_val,
                "weight_pct": weight_pct,
                "unrealized_pnl_eur": pnl_eur,
                "unrealized_pnl_pct": pnl_pct,
                "sector": p.sector,
            })

        return {
            "cash_available": cash,
            "total_equity": tot_eq,
            "invested_capital": round(invested, 2),
            "exposure_pct": exposure_pct,
            "cash_ratio_pct": cash_ratio_pct,
            "positions_count": len(positions_list),
            "positions": positions_list,
            "last_updated": state.last_updated.isoformat(),
        }
    except Exception as exc:
        logger.exception("Failed to retrieve portfolio summary: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/v1/recommendations/pending", response_model=List[Dict[str, Any]])
def get_pending_recommendations(limit: int = Query(default=50, ge=1, le=100)) -> List[Dict[str, Any]]:
    """Return quantitative recommendations that passed the risk cascade awaiting human execution."""
    try:
        # Fetch APPROVED or PENDING signals
        logs = _PORTFOLIO_DB.fetch_signals_by_status(["APPROVED", "PENDING"], limit=limit)
        recommendations = []
        for r in logs:
            recommendations.append({
                "recommendation_id": r.get("id"),
                "ticker": r.get("ticker"),
                "action": r.get("signal_type", "BUY"),
                "status": r.get("status"),
                "conviction_score": float(r.get("score", 0.0)),
                "recommended_quantity": r.get("quantity", 0),
                "reference_price": float(r.get("price", 0.0)),
                "rationale": r.get("reason", ""),
                "generated_at": r.get("created_at"),
            })
        return recommendations
    except Exception as exc:
        logger.exception("Failed to retrieve pending recommendations: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/v1/data/ticker/{symbol}/context", response_model=Dict[str, Any])
def get_ticker_context(symbol: str = FastPath(..., description="Ticker symbol, e.g. MC.PA")) -> Dict[str, Any]:
    """Return unified quantitative context: current price, returns, RSI, sentiment history, and HMM regime."""
    clean_sym = symbol.upper().strip()
    try:
        import yfinance as yf
        raw = yf.download(clean_sym, period="1y", interval="1d", progress=False, auto_adjust=True)
        if isinstance(raw.columns, pd.MultiIndex):
            c = raw["Close"]
            df = pd.DataFrame({"Close": c.iloc[:, 0] if isinstance(c, pd.DataFrame) else c})
        else:
            df = raw

        if df.empty or len(df) < 5:
            raise HTTPException(status_code=404, detail=f"Market data not found for {clean_sym}")

        close = df["Close"].dropna().astype(float)
        cur_px = float(close.iloc[-1])
        r1m = float((cur_px / close.iloc[-min(21, len(close))] - 1.0) * 100.0) if len(close) >= 21 else 0.0
        r3m = float((cur_px / close.iloc[-min(63, len(close))] - 1.0) * 100.0) if len(close) >= 63 else 0.0
        r1y = float((cur_px / close.iloc[0] - 1.0) * 100.0)

        # Technicals
        sma200 = float(close.tail(200).mean()) if len(close) >= 150 else float(close.mean())
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = float(100 - (100 / (1 + rs)).iloc[-1]) if not pd.isna(rs.iloc[-1]) else 50.0

        # Sentiment history
        sent_history = _PORTFOLIO_DB.get_sentiment_history(clean_sym, days=30)
        avg_sent = float(np.mean([s["score"] for s in sent_history])) if sent_history else 0.0

        # Macro & HMM regime
        vix = _MACRO_SENSOR.get_european_vix()
        regime, conf = _HMM_CLASSIFIER.fit_and_predict()

        return {
            "ticker": clean_sym,
            "current_price": round(cur_px, 2),
            "perf_1m_pct": round(r1m, 2),
            "perf_3m_pct": round(r3m, 2),
            "perf_1y_pct": round(r1y, 2),
            "rsi_14": round(rsi, 1),
            "sma_200": round(sma200, 2),
            "trend_vs_sma200": "UPTREND" if cur_px > sma200 else "DOWNTREND",
            "market_regime": regime.value,
            "regime_confidence": round(conf, 2),
            "vix_level": round(vix, 2),
            "sentiment_score_30d_avg": round(avg_sent, 1),
            "sentiment_recent_records": sent_history[-5:],
            "as_of_utc": datetime.now(timezone.utc).isoformat(),
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Context error for %s: %s", clean_sym, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/v1/system/health", response_model=Dict[str, Any])
def get_system_health() -> Dict[str, Any]:
    """Return operational health status and database integrity."""
    db_path = _PORTFOLIO_DB.db_path
    db_exists = db_path.exists()
    db_size_kb = round(db_path.stat().st_size / 1024, 1) if db_exists else 0.0

    return {
        "status": "HEALTHY",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "database": {
            "sqlite_path": str(db_path),
            "sqlite_exists": db_exists,
            "sqlite_size_kb": db_size_kb,
        },
        "engine_mode": "QUANTITATIVE_RECOMMENDATION_SUPPORT",
        "execution_model": "SOVEREIGN_HUMAN_IN_THE_LOOP",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("06_api.internal_api:app", host="0.0.0.0", port=8000, reload=True)
