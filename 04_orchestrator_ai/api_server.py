"""Internal FastAPI Server for PEA Pollux Systematic Trading Engine.

Single Source of Truth (SSOT) API unifying state between Streamlit UI,
Discord Copilot, and quantitative orchestrator daemons.

Endpoints:
  - GET /portfolio: Current PortfolioState (cash, equity, active positions).
  - GET /signals/recent: Latest 50 audit logs and execution signals.
  - GET /ticker/{symbol}/sentiment: Historical sentiment score time-series.
  - GET /system/regime: Current HMM market regime, VIX gauge, and status.

Usage:
  uvicorn api_server:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

# Setup path imports
_ROOT = Path(__file__).resolve().parent.parent
for d in ("00_data_sensors", "01_memory_core", "02_quant_engine", "03_risk_portfolio", "04_orchestrator_ai"):
    sys.path.insert(0, str(_ROOT / d))

from data_models import PortfolioState
from sqlite_portfolio import PortfolioDB
from duckdb_manager import TimeSeriesDB
from hmm_regime import HMMRegimeClassifier
from macro_alpha_api import MacroAlphaSensor

logger = logging.getLogger(__name__)

# Initialize FastAPI application
app = FastAPI(
    title="PEA Pollux Systematic Engine — Internal API",
    description="Deterministic quantitative state and sentiment time-series gateway.",
    version="1.0.0",
)

# Enable CORS for local UI dashboards
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Shared singletons
_PORTFOLIO_DB = PortfolioDB()
_TIMESERIES_DB = None
try:
    _TIMESERIES_DB = TimeSeriesDB()
except Exception as exc:
    logger.debug("TimeSeriesDB initialization error: %s", exc)

_MACRO_SENSOR = MacroAlphaSensor()
_HMM_CLASSIFIER = HMMRegimeClassifier("^FCHI")


@app.get("/")
def read_root() -> Dict[str, str]:
    """Health check and API metadata."""
    return {
        "system": "PEA Pollux Systematic Trading Engine",
        "status": "OPERATIONAL",
        "version": "1.0.0",
    }


@app.get("/portfolio", response_model=Dict[str, Any])
def get_portfolio() -> Dict[str, Any]:
    """Return the current PEA account state, cash, total equity, and open positions."""
    try:
        state: PortfolioState = _PORTFOLIO_DB.get_portfolio_state()
        return {
            "cash_available": state.cash_available,
            "total_equity": state.total_equity,
            "last_updated": state.last_updated.isoformat(),
            "positions_count": len(state.positions),
            "positions": [
                {
                    "ticker": p.ticker,
                    "qty_shares": p.qty_shares,
                    "avg_entry_price": p.avg_entry_price,
                    "current_price": p.current_price,
                    "sector": p.sector,
                    "market_value": round(p.qty_shares * p.current_price, 2),
                    "unrealized_pnl_eur": round((p.current_price - p.avg_entry_price) * p.qty_shares, 2),
                    "unrealized_pnl_pct": round(((p.current_price / p.avg_entry_price) - 1.0) * 100.0, 2)
                    if p.avg_entry_price > 0
                    else 0.0,
                }
                for p in state.positions
            ],
        }
    except Exception as exc:
        logger.exception("Failed to retrieve portfolio state: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/signals/recent", response_model=List[Dict[str, Any]])
def get_recent_signals(limit: int = Query(default=50, ge=1, le=200)) -> List[Dict[str, Any]]:
    """Retrieve recent signal logs and execution audit records."""
    try:
        signals = _PORTFOLIO_DB.fetch_signals_by_status(limit=limit)
        return signals
    except Exception as exc:
        logger.exception("Failed to retrieve recent signals: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/ticker/{symbol}/sentiment", response_model=List[Dict[str, Any]])
def get_ticker_sentiment(
    symbol: str, days: int = Query(default=30, ge=1, le=365)
) -> List[Dict[str, Any]]:
    """Return historical sentiment score time-series for a given ticker."""
    try:
        records = _PORTFOLIO_DB.get_sentiment_history(symbol.upper().strip(), days=days)
        return records
    except Exception as exc:
        logger.exception("Failed to retrieve sentiment history for %s: %s", symbol, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/system/regime", response_model=Dict[str, Any])
def get_system_regime() -> Dict[str, Any]:
    """Return the current market regime from Gaussian HMM and macro VIX indicators."""
    try:
        vix = _MACRO_SENSOR.get_european_vix()
        regime, confidence = _HMM_CLASSIFIER.fit_and_predict()
        oat_bund = _MACRO_SENSOR.get_oat_bund_spread()

        return {
            "vix_gauge": round(vix, 2),
            "vix_panic": bool(vix >= 30.0),
            "market_regime": regime.value,
            "regime_confidence": round(confidence, 2),
            "oat_bund_spread_bps": oat_bund,
            "status": "NORMAL" if vix < 30.0 else "PANIC_DEFENSE",
        }
    except Exception as exc:
        logger.exception("Failed to compute system regime: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api_server:app", host="0.0.0.0", port=8000, reload=True)
