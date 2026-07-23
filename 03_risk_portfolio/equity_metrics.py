"""Shared equity-curve analytics for live dashboard and future backtests.

Pure functions over a daily equity series — no I/O, no Streamlit, no broker.
Reuse the same metrics on ``portfolio_history`` (live) and on a simulated curve
(walk-forward backtester) so numbers stay comparable.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _prepare_equity_series(curve: pd.DataFrame | pd.Series) -> pd.Series:
    """Normalize a curve into a sorted float Series indexed by date."""
    if isinstance(curve, pd.Series):
        s = curve.astype(float).copy()
        s.index = pd.to_datetime(s.index, errors="coerce")
        return s.dropna().sort_index()

    if curve is None or getattr(curve, "empty", True):
        return pd.Series(dtype=float)

    df = curve.copy()
    if "equity" not in df.columns:
        return pd.Series(dtype=float)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date", "equity"]).sort_values("date")
        return df.set_index("date")["equity"].astype(float)

    s = df["equity"].astype(float)
    s.index = pd.to_datetime(s.index, errors="coerce")
    return s.dropna().sort_index()


def max_drawdown(equity: pd.Series) -> float:
    """Peak-to-trough drawdown as a negative fraction (e.g. -0.12 = -12%)."""
    if equity is None or len(equity) < 2:
        return 0.0
    peak = equity.cummax()
    dd = (equity / peak) - 1.0
    val = float(dd.min())
    return val if np.isfinite(val) else 0.0


def cagr(equity: pd.Series, periods_per_year: float = 252.0) -> float | None:
    """Compound annual growth rate from first to last equity point.

    Uses calendar days between endpoints when the index is datetime-like;
    otherwise falls back to ``len(equity) / periods_per_year`` years.
    """
    if equity is None or len(equity) < 2:
        return None
    start = float(equity.iloc[0])
    end = float(equity.iloc[-1])
    if start <= 0 or end <= 0 or not np.isfinite(start) or not np.isfinite(end):
        return None
    try:
        delta_days = (equity.index[-1] - equity.index[0]).days
        years = max(delta_days / 365.25, 1e-9)
    except Exception:  # noqa: BLE001
        years = max(len(equity) / periods_per_year, 1e-9)
    return float((end / start) ** (1.0 / years) - 1.0)


def sharpe_ratio(
    equity: pd.Series,
    risk_free: float = 0.0,
    periods_per_year: float = 252.0,
) -> float | None:
    """Annualized Sharpe from daily equity returns (sample stdev)."""
    if equity is None or len(equity) < 3:
        return None
    rets = equity.pct_change().dropna()
    if rets.empty or float(rets.std()) == 0.0:
        return None
    excess = rets - (risk_free / periods_per_year)
    val = float(excess.mean() / excess.std() * np.sqrt(periods_per_year))
    return val if np.isfinite(val) else None


def sortino_ratio(
    equity: pd.Series,
    risk_free: float = 0.0,
    periods_per_year: float = 252.0,
) -> float | None:
    """Annualized Sortino (downside deviation only)."""
    if equity is None or len(equity) < 3:
        return None
    rets = equity.pct_change().dropna()
    if rets.empty:
        return None
    excess = rets - (risk_free / periods_per_year)
    downside = excess[excess < 0]
    if downside.empty or float(downside.std()) == 0.0:
        return None
    val = float(excess.mean() / downside.std() * np.sqrt(periods_per_year))
    return val if np.isfinite(val) else None


def compute_equity_metrics(
    curve: pd.DataFrame | pd.Series,
    risk_free: float = 0.0,
) -> dict[str, Any]:
    """Return a metrics dict ready for dashboard / backtest reports.

    Keys: ``n_points``, ``start_equity``, ``end_equity``, ``total_return``,
    ``cagr``, ``max_drawdown``, ``sharpe``, ``sortino``, ``cash_last`` (if col).
    """
    equity = _prepare_equity_series(curve)
    out: dict[str, Any] = {
        "n_points": int(len(equity)),
        "start_equity": None,
        "end_equity": None,
        "total_return": None,
        "cagr": None,
        "max_drawdown": 0.0,
        "sharpe": None,
        "sortino": None,
        "cash_last": None,
    }
    if equity.empty:
        return out

    start = float(equity.iloc[0])
    end = float(equity.iloc[-1])
    out["start_equity"] = start
    out["end_equity"] = end
    out["total_return"] = (end / start - 1.0) if start > 0 else None
    out["cagr"] = cagr(equity)
    out["max_drawdown"] = max_drawdown(equity)
    out["sharpe"] = sharpe_ratio(equity, risk_free=risk_free)
    out["sortino"] = sortino_ratio(equity, risk_free=risk_free)

    if isinstance(curve, pd.DataFrame) and "cash" in curve.columns and not curve.empty:
        try:
            out["cash_last"] = float(curve.sort_values("date").iloc[-1]["cash"])
        except Exception:  # noqa: BLE001
            out["cash_last"] = None
    return out
