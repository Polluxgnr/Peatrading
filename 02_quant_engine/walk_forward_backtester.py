"""Walk-forward backtester scaffold (Phase 20 companion).

Rewinds DuckDB OHLCV from ``start`` day-by-day, runs ``SignalGenerator.evaluate``
on the PEA universe slice available at each date, and accumulates a simple
equity curve (equal-weight paper fills when conviction ≥ floor).

This is a research CLI integrating the Full SignalOrchestrator to ensure
historical simulations match live risk conditions (VIX panics, sizing, etc.).

Usage
-----
::

    python 02_quant_engine/walk_forward_backtester.py --start 2020-01-01 --fast
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
import uuid

import numpy as np
import pandas as pd
import yaml

_ROOT = Path(__file__).resolve().parent.parent
for _sub in ("00_data_sensors", "01_memory_core", "02_quant_engine", "03_risk_portfolio", "04_orchestrator_ai"):
    sys.path.insert(0, str(_ROOT / _sub))

from duckdb_manager import TimeSeriesDB  # noqa: E402
from technical_scorer import SignalGenerator, _CONVICTION_EMIT_FLOOR  # noqa: E402
from config_validator import load_risk_config  # noqa: E402
from data_models import PortfolioState, Position, Signal, SignalType, SignalStatus # noqa: E402
from signal_priority_cascade import SignalOrchestrator # noqa: E402
from equity_metrics import generate_tear_sheet # noqa: E402
from macro_alpha_api import MacroAlphaSensor # noqa: E402

logger = logging.getLogger(__name__)


def _load_universe() -> list[str]:
    path = _ROOT / "config" / "pea_universe.yaml"
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    tickers: list[str] = []
    uni = data.get("universe") or data.get("tickers") or {}
    if isinstance(uni, list):
        for item in uni:
            if isinstance(item, dict) and item.get("ticker"):
                tickers.append(str(item["ticker"]))
            elif isinstance(item, str):
                tickers.append(item)
    elif isinstance(uni, dict):
        for _sector, names in uni.items():
            if not isinstance(names, list):
                continue
            for item in names:
                if isinstance(item, dict) and item.get("ticker"):
                    tickers.append(str(item["ticker"]))
                elif isinstance(item, str):
                    tickers.append(item)
    # Prefer blue-chips first for a fast smoke run
    preferred = [
        "CW8.PA", "MC.PA", "OR.PA", "AI.PA", "ASML.AS", "SAP.DE",
        "SAN.PA", "TTE.PA", "BNP.PA", "AIR.PA", "RMS.PA",
    ]
    ordered = [t for t in preferred if t in tickers]
    ordered += [t for t in tickers if t not in ordered]
    return ordered


def _hist_asof(hist: pd.DataFrame, day_ts: pd.Timestamp) -> pd.DataFrame:
    if hist is None or hist.empty:
        return pd.DataFrame()
    if "Date" in hist.columns:
        return hist[pd.to_datetime(hist["Date"]) <= day_ts]
    return hist[pd.to_datetime(hist.index) <= day_ts]


def _latest_atr14(gen: SignalGenerator, hist: pd.DataFrame) -> float | None:
    if hist is None or len(hist) < 20:
        return None
    try:
        enriched = gen.calculate_indicators(hist)
        atr_col = next((c for c in enriched.columns if "ATR" in str(c).upper()), None)
        if not atr_col:
            return None
        val = float(enriched[atr_col].iloc[-1])
        return val if val > 0 else None
    except Exception:  # noqa: BLE001
        return None


def run_walk_forward(
    start: str = "2020-01-01",
    end: str | None = None,
    conviction_floor: float = _CONVICTION_EMIT_FLOOR,
    max_names: int = 40,
    fast_mode: bool = False
) -> pd.DataFrame:
    """Day-by-day paper equity using ensemble conviction and full orchestrator.

    Returns:
        DataFrame with columns ``date``, ``equity``, ``n_signals``, ``cash``.
    """
    db = TimeSeriesDB()
    macro = MacroAlphaSensor()
    gen = SignalGenerator(macro_sensor=macro)  # price axes + macro
    orchestrator = SignalOrchestrator(timeseries_db=db)
    
    risk = load_risk_config()
    atr_mult = float(risk.REBALANCE_ATR_STOP_MULT)
    profit_trigger = float(risk.REBALANCE_PROFIT_TRIGGER_PCT)
    profit_shave = float(risk.REBALANCE_PROFIT_SHAVE_PCT)
    
    tickers = _load_universe()
    if fast_mode:
        tickers = tickers[:10]  # Only top 10 for rapid testing
    else:
        tickers = tickers[:max_names]
        
    end_ts = pd.Timestamp(end or datetime.now(timezone.utc).date())
    start_ts = pd.Timestamp(start)

    cash = 10_000.0
    equity_rows: list[dict] = []
    # Very simple book: ticker -> {qty, cost, entry_px, highest_px}
    book: dict[str, dict] = {}

    # Build a common calendar from the Core ETF if available.
    calendar_ticker = "CW8.PA" if "CW8.PA" in tickers else (tickers[0] if tickers else None)
    if not calendar_ticker:
        logger.error("Empty universe.")
        return pd.DataFrame(columns=["date", "equity", "n_signals", "cash"])

    cal = db.get_historical_prices(calendar_ticker, days=4000)
    if cal is None or cal.empty:
        logger.error("No calendar history for %s.", calendar_ticker)
        return pd.DataFrame(columns=["date", "equity", "n_signals", "cash"])

    date_col = "Date" if "Date" in cal.columns else cal.index.name
    if date_col and date_col in cal.columns:
        dates = pd.to_datetime(cal[date_col]).sort_values().unique()
    else:
        dates = pd.to_datetime(cal.index).sort_values().unique()

    dates = [d for d in dates if start_ts <= pd.Timestamp(d) <= end_ts]
    
    # If fast mode, skip the first X% to only do the last 3 months, for example
    if fast_mode and len(dates) > 60:
        dates = dates[-60:]
        
    logger.info("Walk-forward %s → %s (%d sessions, %d names). Fast mode=%s",
                dates[0].date(), dates[-1].date(), len(dates), len(tickers), fast_mode)

    pending_signals: list[Signal] = []

    for i, day in enumerate(dates):
        day_ts = pd.Timestamp(day)
        n_sig = 0
        
        # 1. Execute APPROVED signals at today's Open (signals from T-1).
        for signal in pending_signals:
            if signal.status != SignalStatus.APPROVED:
                continue
            ticker = signal.ticker
            if ticker in book:
                continue
            
            try:
                hist = db.get_historical_prices(ticker, days=30)
                if hist is None or hist.empty:
                    continue
                sub = _hist_asof(hist, day_ts)
                if sub.empty:
                    continue
                
                open_px = float(sub["Open"].iloc[-1]) if "Open" in sub.columns else float(sub["Close"].iloc[-1])
                
                # --- Square-Root Slippage Model ---
                adv = 1e6
                vol = 0.02
                if "Volume" in sub.columns and "Close" in sub.columns:
                    adv = float((sub["Close"] * sub["Volume"]).mean())
                    vol = float(sub["Close"].pct_change().std())
                if np.isnan(vol) or vol == 0:
                    vol = 0.02
                if np.isnan(adv) or adv == 0:
                    adv = 1e6
                    
                alloc_amt = signal.allocated_amount or 1000.0
                slippage_pct = 0.1 * vol * np.sqrt(alloc_amt / max(1.0, adv))
                open_px_slipped = open_px * (1.0 + slippage_pct)
                
                if open_px_slipped <= 0 or cash < alloc_amt:
                    continue
                qty = int(alloc_amt // open_px_slipped)
                if qty < 1:
                    continue
                cost = qty * open_px_slipped
                cash -= cost
                book[ticker] = {"qty": qty, "cost": cost, "px": open_px_slipped, "entry_px": open_px_slipped, "highest_px": open_px_slipped}
            except Exception:  # noqa: BLE001
                pass
                
        pending_signals = []

        # 2. Simulate exits: ATR stop-loss and profit shave
        for ticker in list(book.keys()):
            pos = book[ticker]
            try:
                hist = db.get_historical_prices(ticker, days=80)
                sub = _hist_asof(hist, day_ts)
                if sub.empty:
                    continue
                last_px = float(sub["Close"].iloc[-1])
                entry_px = float(pos.get("entry_px") or pos.get("px") or 0)
                if entry_px <= 0:
                    continue
                pnl_pct = (last_px / entry_px - 1.0) * 100.0
                # Chandelier Exit (Trailing Stop from Highest High)
                pos["highest_px"] = max(pos.get("highest_px", entry_px), last_px)
                atr14 = _latest_atr14(gen, sub)
                
                if atr14 is not None and last_px < pos["highest_px"] - atr_mult * atr14:
                    cash += pos["qty"] * last_px
                    del book[ticker]
                    continue

                if pnl_pct >= profit_trigger:
                    sell_qty = max(1, int(pos["qty"] * profit_shave))
                    sell_qty = min(sell_qty, pos["qty"])
                    cash += sell_qty * last_px
                    pos["qty"] -= sell_qty
                    if pos["qty"] <= 0:
                        del book[ticker]
            except Exception:  # noqa: BLE001
                pass

        # Calculate current equity for portfolio state
        mtm = cash
        current_prices = {}
        positions = []
        for ticker, pos in list(book.items()):
            try:
                hist = db.get_historical_prices(ticker, days=5)
                sub = _hist_asof(hist, day_ts)
                last_px = float(sub["Close"].iloc[-1]) if not sub.empty else pos["px"]
                pos["px"] = last_px
                mtm += pos["qty"] * last_px
                current_prices[ticker] = last_px
                
                positions.append(Position(
                    ticker=ticker,
                    qty_shares=pos["qty"],
                    avg_entry_price=pos["entry_px"],
                    current_price=last_px,
                    sector="Unknown"
                ))
            except Exception:  # noqa: BLE001
                mtm += pos["qty"] * pos.get("px", 0)

        portfolio_state = PortfolioState(
            cash_available=cash,
            total_equity=mtm,
            positions=positions,
            last_updated=datetime.now(timezone.utc)
        )

        equity_rows.append({
            "date": day_ts.date().isoformat(),
            "equity": round(mtm, 2),
            "n_signals": 0,
            "cash": round(cash, 2),
            "positions": len(book),
        })

        # 3. Generate raw signals on day T (evaluated on Close)
        if i % 5 == 0:
            raw_candidates = []
            for ticker in tickers:
                if ticker in book:
                    continue # Already in portfolio
                    
                try:
                    hist = db.get_historical_prices(ticker, days=400)
                    if hist is None or hist.empty:
                        continue
                    sub = _hist_asof(hist, day_ts)
                    if len(sub) < 200:
                        continue
                        
                    conv = gen.evaluate(ticker, sub, macro_sensor=macro)
                    total_score = float(conv.get("total") or 0)
                    if total_score < conviction_floor:
                        continue
                        
                    last_px = float(sub["Close"].iloc[-1])
                    current_prices[ticker] = last_px
                    
                    sig = Signal(
                        id=str(uuid.uuid4()),
                        ticker=ticker,
                        signal_type=SignalType.BUY,
                        status=SignalStatus.PENDING,
                        score=total_score,
                        reason=f"Backtest conviction {total_score:.1f}",
                        created_at=datetime.now(timezone.utc)
                    )
                    raw_candidates.append(sig)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("WF skip %s @ %s: %s", ticker, day_ts.date(), exc)

            if raw_candidates:
                # Approximate VIX (using VIX history if available, else static)
                vix_val = 15.0
                try:
                    vix_hist = db.get_historical_prices("^VIX", days=30)
                    if vix_hist is not None and not vix_hist.empty:
                        vsub = _hist_asof(vix_hist, day_ts)
                        if not vsub.empty:
                            vix_val = float(vsub["Close"].iloc[-1])
                except Exception:
                    pass
                
                # Pass through the full orchestrator
                processed = orchestrator.process_raw_signals(
                    raw_signals=raw_candidates,
                    portfolio=portfolio_state,
                    current_prices=current_prices,
                    vix_level=vix_val
                )
                
                n_sig = len([s for s in processed if s.status == SignalStatus.APPROVED])
                equity_rows[-1]["n_signals"] = n_sig
                pending_signals = processed

    df = pd.DataFrame(equity_rows)
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    return df


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Walk-forward ensemble backtester.")
    p.add_argument("--start", default="2020-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--floor", type=float, default=_CONVICTION_EMIT_FLOOR)
    p.add_argument("--fast", action="store_true", help="Sample only top 10 tickers and last few months.")
    args = p.parse_args()
    
    curve = run_walk_forward(
        start=args.start,
        end=args.end,
        conviction_floor=args.floor,
        fast_mode=args.fast
    )
    out = _ROOT / "database" / "walk_forward_equity.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    curve.to_csv(out)
    print(f"Wrote {len(curve)} rows → {out}")
    if not curve.empty:
        print(curve.tail(3))
        
        # Generate tear sheet
        print("\n--- Walk-Forward Tear Sheet ---")
        metrics = generate_tear_sheet(curve)
        for k, v in metrics.items():
            print(f"{k}: {v}")


if __name__ == "__main__":
    main()
