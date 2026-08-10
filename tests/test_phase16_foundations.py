"""Unit tests for equity metrics and rebalancer mode split."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

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


class TestPhase16Foundations(unittest.TestCase):

    def test_max_drawdown_and_sharpe_on_synthetic_curve(self):
        dates = pd.date_range("2025-01-01", periods=60, freq="B")
        # Rise then 20% drawdown then recover partially.
        eq = pd.Series(
            [100.0] * 10
            + list(range(100, 120))
            + [120 * 0.8] * 10
            + [100.0] * 20,
            index=dates[:60],
        )
        eq = eq.iloc[:60]
        dd = max_drawdown(eq)
        self.assertLessEqual(dd, -0.15)
        m = compute_equity_metrics(pd.DataFrame({"date": eq.index, "equity": eq.values}))
        self.assertEqual(m["n_points"], 60)
        self.assertLessEqual(m["max_drawdown"], -0.15)
        self.assertTrue(m["sharpe"] is None or isinstance(m["sharpe"], float))

    def test_rebalancer_modes_split_without_tsdb(self):
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
        self.assertEqual(len(shaves), 1)
        self.assertEqual(shaves[0].ticker, "MC.PA")
        self.assertEqual(atrs, [])

    def test_earnings_blackout_window(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            risk = tmp_path / "risk_params.yaml"
            risk.write_text("EARNINGS_BLACKOUT_DAYS: 2\n", encoding="utf-8")
            cal = tmp_path / "earnings_calendar.yaml"
            cal.write_text(
                "events:\n  MC.PA:\n    2026-07-25: \"Q2 earnings\"\n",
                encoding="utf-8",
            )
            eng = EarningsBlackoutEngine(tmp_path)

            veto, reason = eng.check_veto("MC.PA", date(2026, 7, 24))
            self.assertTrue(veto)
            self.assertIn("Q2", reason)
            clear, _ = eng.check_veto("OR.PA", date(2026, 7, 24))
            self.assertFalse(clear)


if __name__ == "__main__":
    unittest.main()
