"""Historical stress testing utilities (black swan replay)."""

from __future__ import annotations

from typing import Iterable

import pandas as pd

_SHOCK_WINDOWS = [
    ("Subprime 2008", "2008-09-01", "2008-10-31"),
    ("COVID Crash 2020", "2020-02-20", "2020-03-23"),
    ("Inflation Shock 2022", "2022-01-03", "2022-10-12"),
]

# CAC 40 index has history back to 2000 — CW8/EWLD did not exist in 2008.
_PRIMARY_PROXY = "^FCHI"
_FALLBACK_PROXIES: Iterable[str] = ("^FCHI", "EWLD.PA", "CW8.PA", "PE500.PA")
_NO_DATA_MSG = "Pas de données historiques"


def _max_drawdown_from_returns(returns: pd.Series) -> float:
    if returns is None or returns.empty:
        return 0.0
    wealth = (1.0 + returns.astype(float)).cumprod()
    peak = wealth.cummax()
    drawdown = wealth / peak - 1.0
    return float(drawdown.min()) if not drawdown.empty else 0.0


def _load_close_series(db_manager, ticker: str, days: int) -> pd.Series | None:
    try:
        hist = db_manager.get_historical_prices(ticker, days=days)
        if hist is None or hist.empty:
            return None
        frame = hist[["Date", "Close"]].copy()
        frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
        frame["Close"] = pd.to_numeric(frame["Close"], errors="coerce")
        frame = frame.dropna(subset=["Date", "Close"]).sort_values("Date")
        if frame.empty:
            return None
        return frame.set_index("Date")["Close"]
    except Exception:
        return None


def simulate_historical_shocks(
    portfolio_tickers: list,
    weights: dict,
    db_manager,
) -> pd.DataFrame:
    """Replay historical windows and estimate weighted portfolio drawdowns.

    Uses ``^FCHI`` (CAC 40) as primary proxy for pre-2010 shocks.
  """
    if not portfolio_tickers:
        return pd.DataFrame(columns=["Shock", "Start", "End", "Worst PnL %", "Proxy Used"])

    tickers = [str(t) for t in portfolio_tickers if str(t)]
    w = {str(k): float(v) for k, v in (weights or {}).items()}
    if not w:
        ew = 1.0 / float(len(tickers))
        w = {t: ew for t in tickers}

    start_min = min(pd.Timestamp(s) for _, s, _ in _SHOCK_WINDOWS)
    end_max = max(pd.Timestamp(e) for _, _, e in _SHOCK_WINDOWS)
    days = int((end_max - start_min).days) + 60

    series_map: dict[str, pd.Series] = {}
    for t in tickers:
        s = _load_close_series(db_manager, t, days)
        if s is not None:
            series_map[t] = s

    # Pre-load proxy series (CAC 40 first for 2008 coverage).
    proxy_map: dict[str, pd.Series] = {}
    for px in _FALLBACK_PROXIES:
        s = _load_close_series(db_manager, px, days)
        if s is not None and not s.empty:
            proxy_map[px] = s

    out_rows = []
    for shock_name, start_s, end_s in _SHOCK_WINDOWS:
        start = pd.Timestamp(start_s)
        end = pd.Timestamp(end_s)
        active_returns = []
        active_weights = []
        proxy_used = False

        for t in tickers:
            s = series_map.get(t)
            if s is None or s[(s.index >= start) & (s.index <= end)].empty:
                # Prefer CAC 40 for 2008; fall back to other proxies.
                for px in _FALLBACK_PROXIES:
                    sp = proxy_map.get(px)
                    if sp is not None:
                        wdw_test = sp[(sp.index >= start) & (sp.index <= end)]
                        if wdw_test is not None and len(wdw_test) >= 4:
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
                    "Worst PnL %": _NO_DATA_MSG,
                    "Proxy Used": _PRIMARY_PROXY if shock_name.startswith("Subprime") else "n/a",
                }
            )
            continue

        mat = pd.concat(active_returns, axis=1, join="inner").dropna()
        if mat.empty:
            worst = _NO_DATA_MSG
        else:
            ww = pd.Series(active_weights, dtype=float)
            ww = ww / ww.sum() if ww.sum() > 0 else pd.Series([1.0 / len(active_weights)] * len(active_weights))
            pr = mat.to_numpy(dtype=float) @ ww.to_numpy(dtype=float)
            dd = _max_drawdown_from_returns(pd.Series(pr, index=mat.index))
            worst = round(dd * 100.0, 2)

        out_rows.append(
            {
                "Shock": shock_name,
                "Start": start_s,
                "End": end_s,
                "Worst PnL %": worst,
                "Proxy Used": _PRIMARY_PROXY if proxy_used else "no",
            }
        )

    return pd.DataFrame(out_rows)
