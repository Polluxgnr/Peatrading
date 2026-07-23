"""Unit tests for equity metrics and rebalancer mode split."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
for sub in ("01_memory_core", "03_risk_portfolio", "04_orchestrator_ai"):
    sys.path.insert(0, str(ROOT / sub))

from equity_metrics import (  # noqa: E402
    compute_equity_metrics,
    max_drawdown,
    sharpe_ratio,
)
from monthly_rebalancer import PortfolioRebalancer  # noqa: E402
from earnings_blackout import EarningsBlackoutEngine  # noqa: E402
from data_models import Position, PortfolioState  # noqa: E402


def test_max_drawdown_and_sharpe_on_synthetic_curve():
    dates = pd.date_range("2025-01-01", periods=60, freq="B")
    # Rise then 20% drawdown then recover partially.
    eq = pd.Series(
        [100.0] * 10
        + list(range(100, 120))
        + [120 * 0.8] * 10
        + [100.0] * 20,
        index=dates[:60],
    )
    # Pad/trim to 60
    eq = eq.iloc[:60]
    dd = max_drawdown(eq)
    assert dd <= -0.15
    m = compute_equity_metrics(pd.DataFrame({"date": eq.index, "equity": eq.values}))
    assert m["n_points"] == 60
    assert m["max_drawdown"] <= -0.15
    assert m["sharpe"] is None or isinstance(m["sharpe"], float)


def test_rebalancer_modes_split_without_tsdb():
    cfg = ROOT / "config"
    rb = PortfolioRebalancer(cfg, timeseries_db=None)
    portfolio = PortfolioState(
        cash_available=1000,
        total_equity=5000,
        positions=[
            Position(
                ticker="MC.PA",
                qty_shares=10,
                avg_entry_price=100.0,
                current_price=125.0,
                sector="Luxury",
            ),
            Position(
                ticker="STLAP.PA",
                qty_shares=8,
                avg_entry_price=20.0,
                current_price=17.0,
                sector="Auto",
            ),
        ],
        last_updated=datetime.now(timezone.utc),
    )
    shaves = rb.generate_profit_shave_signals(portfolio)
    atrs = rb.generate_atr_stop_signals(portfolio)
    assert len(shaves) == 1 and shaves[0].ticker == "MC.PA"
    # No DuckDB -> ATR stops cannot fire.
    assert atrs == []


def test_earnings_blackout_window(tmp_path):
    risk = tmp_path / "risk_params.yaml"
    risk.write_text("EARNINGS_BLACKOUT_DAYS: 2\n", encoding="utf-8")
    cal = tmp_path / "earnings_calendar.yaml"
    cal.write_text(
        "events:\n  MC.PA:\n    2026-07-25: \"Q2 earnings\"\n",
        encoding="utf-8",
    )
    eng = EarningsBlackoutEngine(tmp_path)
    from datetime import date

    veto, reason = eng.check_veto("MC.PA", date(2026, 7, 24))
    assert veto and "Q2" in reason
    clear, _ = eng.check_veto("OR.PA", date(2026, 7, 24))
    assert not clear
