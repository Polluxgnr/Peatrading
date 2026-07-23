"""Walk-forward backtester scaffold (Phase 20 companion).

Rewinds DuckDB OHLCV from ``start`` day-by-day, runs ``SignalGenerator.evaluate``
on the PEA universe slice available at each date, and accumulates a simple
equity curve (equal-weight paper fills when conviction ≥ floor).

This is intentionally a research CLI — it does **not** place broker orders.
Full Orchestrator vetoes can be layered later; here we isolate ensemble
parameter sensitivity (e.g. RSI 25 vs 30 contribution via conviction axes).

Usage
-----
::

    python 02_quant_engine/walk_forward_backtester.py --start 2020-01-01
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

_ROOT = Path(__file__).resolve().parent.parent
for _sub in ("00_data_sensors", "01_memory_core", "02_quant_engine"):
    sys.path.insert(0, str(_ROOT / _sub))

from duckdb_manager import TimeSeriesDB  # noqa: E402
from technical_scorer import SignalGenerator, _CONVICTION_EMIT_FLOOR  # noqa: E402

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


def run_walk_forward(
    start: str = "2020-01-01",
    end: str | None = None,
    conviction_floor: float = _CONVICTION_EMIT_FLOOR,
    notional_per_trade: float = 1000.0,
    max_names: int = 40,
) -> pd.DataFrame:
    """Day-by-day paper equity using ensemble conviction only.

    Returns:
        DataFrame with columns ``date``, ``equity``, ``n_signals``, ``cash``.
    """
    db = TimeSeriesDB()
    gen = SignalGenerator(macro_sensor=None)  # price axes only (offline-friendly)
    tickers = _load_universe()[:max_names]
    end_ts = pd.Timestamp(end or datetime.now(timezone.utc).date())
    start_ts = pd.Timestamp(start)

    cash = 10_000.0
    equity_rows: list[dict] = []
    # Very simple book: ticker -> {qty, cost}
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
    logger.info("Walk-forward %s → %s (%d sessions, %d names).",
                start, end_ts.date(), len(dates), len(tickers))

    for i, day in enumerate(dates):
        day_ts = pd.Timestamp(day)
        n_sig = 0
        # Mark-to-market + optional new entries every ~5 sessions to keep runtime sane.
        if i % 5 == 0:
            for ticker in tickers:
                try:
                    hist = db.get_historical_prices(ticker, days=400)
                    if hist is None or hist.empty:
                        continue
                    if "Date" in hist.columns:
                        hist = hist[pd.to_datetime(hist["Date"]) <= day_ts]
                    else:
                        hist = hist[pd.to_datetime(hist.index) <= day_ts]
                    if len(hist) < 200:
                        continue
                    conv = gen.evaluate(ticker, hist, macro_sensor=None)
                    if float(conv.get("total") or 0) < conviction_floor:
                        continue
                    n_sig += 1
                    px = float(conv.get("close") or 0)
                    if px <= 0 or cash < notional_per_trade:
                        continue
                    if ticker in book:
                        continue
                    qty = int(notional_per_trade // px)
                    if qty < 1:
                        continue
                    cost = qty * px
                    cash -= cost
                    book[ticker] = {"qty": qty, "cost": cost, "px": px}
                except Exception as exc:  # noqa: BLE001
                    logger.debug("WF skip %s @ %s: %s", ticker, day_ts.date(), exc)

        mtm = cash
        for ticker, pos in list(book.items()):
            try:
                hist = db.get_historical_prices(ticker, days=5)
                if hist is None or hist.empty:
                    mtm += pos["qty"] * pos["px"]
                    continue
                if "Date" in hist.columns:
                    sub = hist[pd.to_datetime(hist["Date"]) <= day_ts]
                    last_px = float(sub["Close"].iloc[-1]) if not sub.empty else pos["px"]
                else:
                    last_px = float(hist["Close"].iloc[-1])
                pos["px"] = last_px
                mtm += pos["qty"] * last_px
            except Exception:  # noqa: BLE001
                mtm += pos["qty"] * pos.get("px", 0)

        equity_rows.append({
            "date": day_ts.date().isoformat(),
            "equity": round(mtm, 2),
            "n_signals": n_sig,
            "cash": round(cash, 2),
            "positions": len(book),
        })

    return pd.DataFrame(equity_rows)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Walk-forward ensemble backtester.")
    p.add_argument("--start", default="2020-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--floor", type=float, default=_CONVICTION_EMIT_FLOOR)
    p.add_argument("--notional", type=float, default=1000.0)
    args = p.parse_args()
    curve = run_walk_forward(
        start=args.start,
        end=args.end,
        conviction_floor=args.floor,
        notional_per_trade=args.notional,
    )
    out = _ROOT / "database" / "walk_forward_equity.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    curve.to_csv(out, index=False)
    print(f"Wrote {len(curve)} rows → {out}")
    if not curve.empty:
        print(curve.tail(3).to_string(index=False))


if __name__ == "__main__":
    main()
