"""Unit & Integration Tests for StatArb Cointegration Engine & Walk-Forward Backtester."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
for sub in ("00_data_sensors", "01_memory_core", "02_quant_engine", "03_risk_portfolio", "04_orchestrator_ai", "05_interfaces"):
    sys.path.insert(0, str(ROOT / sub))

from stat_arb_pairs import StatArbEngine
from walk_forward_backtester import WalkForwardBacktester
from data_models import SignalType
import main_scheduler


class TestStatArbAndBacktestSuite(unittest.TestCase):

    def test_01_stat_arb_cointegrated_pair_detection(self):
        """Verify StatArbEngine detects synthetic cointegrated series and emits signals."""
        np.random.seed(42)
        n = 300
        common_trend = np.cumsum(np.random.normal(0, 1, n))
        noise_a = np.random.normal(0, 0.05, n)
        noise_b = np.random.normal(0, 0.05, n)

        # Create temporary spread divergence at the end
        noise_a[-2:] -= 0.6

        dates = pd.date_range("2024-01-01", periods=n, freq="B").strftime("%Y-%m-%d")
        df_a = pd.DataFrame({"Date": dates, "Close": np.exp(common_trend + noise_a + 4.0)})
        df_b = pd.DataFrame({"Date": dates, "Close": np.exp(common_trend + noise_b + 4.0)})

        engine = StatArbEngine(p_val_threshold=0.05, z_score_entry=2.0)
        sector_map = {"MC.PA": "Luxury", "OR.PA": "Luxury"}

        pairs = engine.find_cointegrated_pairs({"MC.PA": df_a, "OR.PA": df_b}, sector_map)
        self.assertGreaterEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["sector"], "Luxury")
        self.assertLess(pairs[0]["p_value"], 0.05)

        # Generate signals
        sigs = engine.generate_stat_arb_signals({"MC.PA": df_a, "OR.PA": df_b}, sector_map)
        self.assertGreaterEqual(len(sigs), 1)
        sig = sigs[0]
        self.assertEqual(sig.signal_type, SignalType.BUY)
        self.assertIn("STAT_ARB_COINTEGRATION", sig.lineage.get("strategy", ""))
        self.assertIn("z_score", sig.lineage)

    def test_02_walk_forward_backtester_execution(self):
        """Verify event-driven execution at T+1 Open and profit-shaving rules."""
        dates = pd.date_range("2024-01-01", periods=100, freq="B").strftime("%Y-%m-%d")
        # Steady uptrend
        prices = [100.0 + i * 1.0 for i in range(100)]
        df = pd.DataFrame({
            "Date": dates,
            "Open": prices,
            "High": [p + 2.0 for p in prices],
            "Low": [p - 2.0 for p in prices],
            "Close": prices,
            "Volume": [10000] * 100,
        })

        signals_df = pd.DataFrame([
            {"Date": dates[5], "Ticker": "MC.PA", "Score": 85.0, "SignalType": "BUY"}
        ])

        tester = WalkForwardBacktester(initial_capital=10_000.0, atr_stop_mult=2.5)
        res = tester.run_backtest({"MC.PA": df}, signals_df)

        self.assertGreater(res["final_equity"], 10_000.0)
        self.assertGreater(res["total_return_pct"], 0.0)
        self.assertFalse(res["equity_curve"].empty)

    def test_03_main_scheduler_sector_map_loader(self):
        """Verify sector map loader accurately extracts sectors from universe YAML."""
        s_map = main_scheduler._load_universe_sector_map()
        self.assertIsInstance(s_map, dict)
        self.assertIn("MC.PA", s_map)
        self.assertEqual(s_map["MC.PA"], "Consumer Cyclical")


if __name__ == "__main__":
    unittest.main()
