"""Unit Tests for Smart Limit Price Tiers and AI Radar Chart Telemetry."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
for sub in ("00_data_sensors", "01_memory_core", "02_quant_engine", "03_risk_portfolio", "04_orchestrator_ai", "05_interfaces", "06_api"):
    sys.path.insert(0, str(ROOT / sub))

from limit_price_optimizer import calculate_smart_limit_price
from contextual_bandit import UCBBandit
from ensemble_optimizer import DynamicEnsemble


class TestLimitTiersAndRadarSuite(unittest.TestCase):

    def test_01_buy_smart_limit_price_tiers(self):
        """Verify BUY limit price tiers (aggressive, optimal, patient)."""
        current_price = 100.0
        atr_14 = 4.0

        tiers = calculate_smart_limit_price("MC.PA", current_price, atr_14, direction="BUY")
        self.assertIn("aggressive", tiers)
        self.assertIn("optimal", tiers)
        self.assertIn("patient", tiers)

        # Aggressive = current + 0.05 * ATR = 100 + 0.20 = 100.20
        self.assertEqual(tiers["aggressive"], 100.20)
        # Optimal = current - 0.10 * ATR = 100 - 0.40 = 99.60
        self.assertEqual(tiers["optimal"], 99.60)
        # Patient = current - 0.25 * ATR = 100 - 1.00 = 99.00
        self.assertEqual(tiers["patient"], 99.00)

        # Ensure aggressive >= optimal >= patient for BUY
        self.assertGreater(tiers["aggressive"], tiers["optimal"])
        self.assertGreater(tiers["optimal"], tiers["patient"])

    def test_02_sell_smart_limit_price_tiers(self):
        """Verify SELL limit price tiers (aggressive, optimal, patient)."""
        current_price = 200.0
        atr_14 = 8.0

        tiers = calculate_smart_limit_price("AI.PA", current_price, atr_14, direction="SELL")

        # Aggressive = current - 0.05 * ATR = 200 - 0.40 = 199.60
        self.assertEqual(tiers["aggressive"], 199.60)
        # Optimal = current + 0.10 * ATR = 200 + 0.80 = 200.80
        self.assertEqual(tiers["optimal"], 200.80)
        # Patient = current + 0.25 * ATR = 200 + 2.00 = 202.00
        self.assertEqual(tiers["patient"], 202.00)

        # Ensure patient >= optimal >= aggressive for SELL
        self.assertGreater(tiers["patient"], tiers["optimal"])
        self.assertGreater(tiers["optimal"], tiers["aggressive"])

    def test_03_zero_or_negative_inputs(self):
        """Verify graceful fallback for invalid prices or zero ATR."""
        tiers_zero = calculate_smart_limit_price("TTE.PA", 0.0, 5.0, direction="BUY")
        self.assertEqual(tiers_zero["aggressive"], 0.0)

        tiers_no_atr = calculate_smart_limit_price("TTE.PA", 60.0, 0.0, direction="BUY")
        self.assertGreater(tiers_no_atr["aggressive"], 0.0)
        self.assertGreater(tiers_no_atr["optimal"], 0.0)

    def test_04_bandit_and_ensemble_weights(self):
        """Verify UCBBandit and DynamicEnsemble provide valid normalized weights."""
        bandit = UCBBandit()
        weights_bull = bandit.get_weights("BULL")
        self.assertIn("trend", weights_bull)
        self.assertIn("mean_reversion", weights_bull)
        self.assertAlmostEqual(sum(weights_bull.values()), 1.0, places=2)

        ensemble = DynamicEnsemble()
        ens_weights = ensemble.get_optimized_weights()
        self.assertIn("heuristic_mr_weight", ens_weights)
        self.assertIn("heuristic_trend_weight", ens_weights)
        self.assertIn("ml_total_weight", ens_weights)


if __name__ == "__main__":
    unittest.main()
