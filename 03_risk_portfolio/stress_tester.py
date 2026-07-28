"""Historical stress testing utilities (black swan replay)."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

import pandas as pd


_SHOCK_WINDOWS = [
    ("Subprime 2008", "2008-09-01", "2008-10-31"),
    ("COVID Crash 2020", "2020-02-20", "2020-03-23"),
    ("Inflation Shock 2022", "2022-01-03", "2022-10-12"),
]


def _max_drawdown_from_returns(returns: pd.Series) -> float:
    if returns is None or returns.empty:
        return 0.0
    wealth = (1.0 + returns.astype(float)).cumprod()
    peak = wealth.cummax()
    drawdown = wealth / peak - 1.0
    return float(drawdown.min()) if not drawdown.empty else 0.0


def simulate_historical_shocks(
    portfolio_tickers: list,
    weights: dict,
    db_manager,
) -> pd.DataFrame:
    """Replay historical windows and estimate weighted portfolio drawdowns.

    If a ticker has no data for a window, a Core ETF proxy is attempted.
    """
    if not portfolio_tickers:
        return pd.DataFrame(columns=["Shock", "Start", "End", "Worst PnL %", "Proxy Used"])

    tickers = [str(t) for t in portfolio_tickers if str(t)]
    w = {str(k): float(v) for k, v in (weights or {}).items()}
    if not w:
        ew = 1.0 / float(len(tickers))
        w = {t: ew for t in tickers}

    # pull broad history once (covers earliest window)
    start_min = min(pd.Timestamp(s) for _, s, _ in _SHOCK_WINDOWS)
    end_max = max(pd.Timestamp(e) for _, _, e in _SHOCK_WINDOWS)
    days = int((end_max - start_min).days) + 30

    series_map: dict[str, pd.Series] = {}
    for t in tickers:
        try:
            hist = db_manager.get_historical_prices(t, days=days)
            if hist is None or hist.empty:
                continue
            frame = hist[["Date", "Close"]].copy()
            frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
            frame["Close"] = pd.to_numeric(frame["Close"], errors="coerce")
            frame = frame.dropna(subset=["Date", "Close"]).sort_values("Date")
            if frame.empty:
                continue
            series_map[t] = frame.set_index("Date")["Close"]
        except Exception:
            continue

    proxy_pool: Iterable[str] = ("CW8.PA", "EWLD.PA", "PE500.PA")
    out_rows = []
    for shock_name, start_s, end_s in _SHOCK_WINDOWS:
        start = pd.Timestamp(start_s)
        end = pd.Timestamp(end_s)
        active_returns = []
        active_weights = []
        proxy_used = False

        for t in tickers:
            s = series_map.get(t)
            if s is None:
                # Try proxy
                for px in proxy_pool:
                    sp = series_map.get(px)
                    if sp is not None:
                        s = sp
                        proxy_used = True
                        break
            if s is None:
                continue

            wdw = s[(s.index >= start) & (s.index <= end)]
            if wdw is None or wdw.empty or len(wdw) < 4:
                continue
            r = wdw.pct_change().dropna()
            if r.empty:
                continue
            active_returns.append(r.rename(t))
            active_weights.append(float(w.get(t, 0.0)))

        if not active_returns:
            out_rows.append(
                {
                    "Shock": shock_name,
                    "Start": start_s,
                    "End": end_s,
                    "Worst PnL %": None,
                    "Proxy Used": "n/a",
                }
            )
            continue

        mat = pd.concat(active_returns, axis=1, join="inner").dropna()
        if mat.empty:
            worst = None
        else:
            ww = pd.Series(active_weights, dtype=float)
            ww = ww / ww.sum() if ww.sum() > 0 else pd.Series([1.0 / len(active_weights)] * len(active_weights))
            pr = mat.to_numpy(dtype=float) @ ww.to_numpy(dtype=float)
            dd = _max_drawdown_from_returns(pd.Series(pr, index=mat.index))
            worst = dd * 100.0

        out_rows.append(
            {
                "Shock": shock_name,
                "Start": start_s,
                "End": end_s,
                "Worst PnL %": worst,
                "Proxy Used": "yes" if proxy_used else "no",
            }
        )

    return pd.DataFrame(out_rows)

