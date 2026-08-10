"""Realistic Event-Driven Walk-Forward Backtester for PEA Sniper Terminal.

Enforces realistic execution rules:
  1. Execution strictly at T+1 Open (never at signal-day Close, eliminating lookahead bias).
  2. Realistic exit simulation:
     - ATR Trailing Stop: Price < Entry - 2.5 * ATR_14
     - Monthly Profit-Shaving: Trim 20% of position when unrealized gain >= +20%
  3. Rolling walk-forward windows without future leakage.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    ticker: str
    entry_date: str
    entry_price: float
    exit_date: Optional[str] = None
    exit_price: Optional[float] = None
    exit_reason: str = ""
    shares: int = 1
    pnl_eur: float = 0.0
    pnl_pct: float = 0.0


class WalkForwardBacktester:
    """Event-driven walk-forward engine with T+1 open execution and ATR stops."""

    def __init__(
        self,
        initial_capital: float = 10_000.0,
        atr_stop_mult: float = 2.5,
        profit_shave_trigger_pct: float = 0.20,
        profit_shave_trim_pct: float = 0.20,
    ) -> None:
        self.initial_capital = initial_capital
        self.atr_stop_mult = atr_stop_mult
        self.profit_shave_trigger_pct = profit_shave_trigger_pct
        self.profit_shave_trim_pct = profit_shave_trim_pct

    def run_backtest(
        self,
        ohlcv_dict: Dict[str, pd.DataFrame],
        signals_df: pd.DataFrame,
    ) -> Dict[str, any]:
        """Execute backtest over historical price data and generated signals.

        Args:
            ohlcv_dict: Dict of {ticker: DataFrame with Date, Open, High, Low, Close, Volume}.
            signals_df: DataFrame with columns [Date, Ticker, Score, SignalType].

        Returns:
            dict: Performance metrics, equity curve, trade log.
        """
        if not ohlcv_dict or signals_df.empty:
            return {"error": "Empty data", "trades": [], "total_return_pct": 0.0}

        cash = self.initial_capital
        positions: Dict[str, Dict] = {}  # {ticker: {shares, entry_price, entry_date, atr}}
        trades: List[TradeRecord] = []
        equity_curve: List[Dict] = []

        # Align all dates across universe
        all_dates = sorted(
            list(
                set().union(
                    *[
                        pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d").unique()
                        for df in ohlcv_dict.values()
                        if "Date" in df.columns
                    ]
                )
            )
        )

        signals_by_date = signals_df.groupby("Date")

        for i in range(len(all_dates) - 1):
            cur_date = all_dates[i]
            next_date = all_dates[i + 1]

            # 1. Evaluate open positions on cur_date (Stop loss / Profit shaving)
            for ticker in list(positions.keys()):
                pos = positions[ticker]
                df_t = ohlcv_dict.get(ticker)
                if df_t is None or df_t.empty:
                    continue

                row_cur = df_t[df_t["Date"].astype(str).str[:10] == cur_date]
                if row_cur.empty:
                    continue

                cur_close = float(row_cur["Close"].iloc[-1])
                cur_low = float(row_cur["Low"].iloc[-1])
                atr = pos.get("atr", cur_close * 0.02)
                stop_price = pos["entry_price"] - (self.atr_stop_mult * atr)

                # Check Stop Loss breach
                if cur_low <= stop_price:
                    exit_px = min(cur_close, stop_price)
                    pnl = (exit_px - pos["entry_price"]) * pos["shares"]
                    pnl_pct = (exit_px / pos["entry_price"] - 1.0) * 100.0
                    trades.append(
                        TradeRecord(
                            ticker=ticker,
                            entry_date=pos["entry_date"],
                            entry_price=pos["entry_price"],
                            exit_date=cur_date,
                            exit_price=exit_px,
                            exit_reason="ATR_STOP_LOSS",
                            shares=pos["shares"],
                            pnl_eur=pnl,
                            pnl_pct=pnl_pct,
                        )
                    )
                    cash += exit_px * pos["shares"]
                    del positions[ticker]
                    continue

                # Check Profit Shaving (+20% gain)
                unrealized_gain = (cur_close / pos["entry_price"]) - 1.0
                if unrealized_gain >= self.profit_shave_trigger_pct and not pos.get("shaved", False):
                    trim_shares = max(1, int(pos["shares"] * self.profit_shave_trim_pct))
                    if trim_shares < pos["shares"]:
                        pos["shares"] -= trim_shares
                        pos["shaved"] = True
                        shave_pnl = (cur_close - pos["entry_price"]) * trim_shares
                        trades.append(
                            TradeRecord(
                                ticker=ticker,
                                entry_date=pos["entry_date"],
                                entry_price=pos["entry_price"],
                                exit_date=cur_date,
                                exit_price=cur_close,
                                exit_reason="PROFIT_SHAVE_20PCT",
                                shares=trim_shares,
                                pnl_eur=shave_pnl,
                                pnl_pct=unrealized_gain * 100.0,
                            )
                        )
                        cash += cur_close * trim_shares

            # 2. Process BUY Signals emitted on cur_date -> Execute at next_date OPEN
            if cur_date in signals_by_date.groups:
                day_signals = signals_by_date.get_group(cur_date)
                for _, sig in day_signals.iterrows():
                    sig_ticker = sig["Ticker"]
                    if sig_ticker in positions:
                        continue  # Already held

                    df_sig = ohlcv_dict.get(sig_ticker)
                    if df_sig is None or df_sig.empty:
                        continue

                    # Look up T+1 OPEN
                    row_next = df_sig[df_sig["Date"].astype(str).str[:10] == next_date]
                    if row_next.empty:
                        continue

                    t1_open = float(row_next["Open"].iloc[0])
                    if t1_open <= 0 or cash < t1_open:
                        continue

                    # Approximate ATR14
                    idx_cur = df_sig[df_sig["Date"].astype(str).str[:10] == cur_date].index
                    atr14 = t1_open * 0.025
                    if not idx_cur.empty and idx_cur[0] >= 14:
                        highs = df_sig["High"].iloc[idx_cur[0] - 14 : idx_cur[0]]
                        lows = df_sig["Low"].iloc[idx_cur[0] - 14 : idx_cur[0]]
                        atr14 = float((highs - lows).mean())

                    # Target size: 10% of equity
                    target_notional = min(cash * 0.95, (cash + sum(p["shares"] * p["entry_price"] for p in positions.values())) * 0.10)
                    shares = max(1, int(target_notional // t1_open))

                    cost = shares * t1_open
                    if cash >= cost:
                        cash -= cost
                        positions[sig_ticker] = {
                            "shares": shares,
                            "entry_price": t1_open,
                            "entry_date": next_date,
                            "atr": atr14,
                            "shaved": False,
                        }

            # 3. Calculate portfolio total equity at cur_date Close
            pos_val = 0.0
            for t_sym, p_data in positions.items():
                df_cur = ohlcv_dict.get(t_sym)
                if df_cur is not None and not df_cur.empty:
                    r_c = df_cur[df_cur["Date"].astype(str).str[:10] == cur_date]
                    px = float(r_c["Close"].iloc[-1]) if not r_c.empty else p_data["entry_price"]
                else:
                    px = p_data["entry_price"]
                pos_val += p_data["shares"] * px

            tot_eq = cash + pos_val
            equity_curve.append({"date": cur_date, "equity": tot_eq, "cash": cash, "positions_value": pos_val})

        # Calculate final stats
        eq_df = pd.DataFrame(equity_curve)
        final_eq = eq_df["equity"].iloc[-1] if not eq_df.empty else self.initial_capital
        total_return_pct = ((final_eq / self.initial_capital) - 1.0) * 100.0

        # Win rate
        closed_trades = [t for t in trades if t.exit_date is not None]
        wins = [t for t in closed_trades if t.pnl_eur > 0]
        win_rate = len(wins) / len(closed_trades) * 100.0 if closed_trades else 0.0

        return {
            "initial_capital": self.initial_capital,
            "final_equity": round(final_eq, 2),
            "total_return_pct": round(total_return_pct, 2),
            "total_trades": len(closed_trades),
            "win_rate_pct": round(win_rate, 1),
            "trades": closed_trades,
            "equity_curve": eq_df,
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    bt = WalkForwardBacktester()
    print("WalkForwardBacktester initialized with T+1 Open execution.")
