"""Unit Tests for Dynamic Mean-Reversion RSI and VIX ROC Black Swan Detection."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
for sub in ("00_data_sensors", "01_memory_core", "02_quant_engine", "03_risk_portfolio", "04_orchestrator_ai", "05_interfaces"):
    sys.path.insert(0, str(ROOT / sub))

from hmm_regime import HMMRegimeClassifier, MarketRegimeState
from market_regime import VolatilityRegimeSentinel
from technical_scorer import SignalGenerator
from trade_cards import render_signal_card


class TestDynamicRegimeAndVixRocSuite(unittest.TestCase):

    def test_01_vix_roc_5d_normal(self):
        """Verify 5-day VIX ROC is calculated accurately in normal volatility."""
        sentinel = VolatilityRegimeSentinel()
        # Series with 15.0 at iloc[-5], and higher values so percentile is ~60%
        history = [10.0, 12.0, 25.0, 28.0, 30.0, 15.0, 15.2, 15.5, 15.8, 16.0]
        res = sentinel.evaluate_vix_regime(history, current_vix=16.5)

        self.assertIn("vix_roc_5d", res)
        # iloc[-5] is 15.0 -> ROC = (16.5 - 15.0) / 15.0 = 0.10
        self.assertAlmostEqual(res["vix_roc_5d"], 0.10, places=2)
        self.assertFalse(res["is_panic"])
        self.assertEqual(res["regime"], "NORMAL")



    def test_02_vix_roc_5d_black_swan_panic(self):
        """Verify VIX ROC > 25% forces PANIC regime immediately."""
        sentinel = VolatilityRegimeSentinel()
        # VIX jumps from 16.0 to 22.0 in 5 days (+37.5% spike)
        history = [16.0, 16.0, 16.0, 16.0, 16.0, 22.0]
        res = sentinel.evaluate_vix_regime(history, current_vix=22.0)

        self.assertIn("vix_roc_5d", res)
        self.assertGreater(res["vix_roc_5d"], 0.25)
        self.assertEqual(res["regime"], "PANIC")
        self.assertTrue(res["is_panic"])
        self.assertEqual(res["floor_modifier"], 15)

    def test_03_hmm_regime_dict_probabilities(self):
        """Verify HMMRegimeClassifier returns structured dict with all state probabilities."""
        clf = HMMRegimeClassifier("^FCHI")
        res = clf.fit_and_predict(pd.DataFrame())

        self.assertIsInstance(res, dict)
        self.assertIn("regime", res)
        self.assertIn("confidence", res)
        self.assertIn("bull_prob", res)
        self.assertIn("bear_prob", res)
        self.assertIn("volatile_prob", res)
        self.assertEqual(res["regime"], MarketRegimeState.VOLATILE.value)

    def test_04_dynamic_rsi_thresholds_by_regime(self):
        """Verify SignalGenerator adjusts RSI thresholds dynamically based on market regime."""
        gen = SignalGenerator()

        dates = pd.date_range("2025-01-01", periods=260, freq="D")
        prices = np.linspace(100.0, 200.0, 260)
        df = pd.DataFrame(
            {
                "Open": prices,
                "High": prices + 2,
                "Low": prices - 2,
                "Close": prices,
                "Volume": [50000] * 260,
            },
            index=dates,
        )

        mock_db = MagicMock()
        mock_db.get_historical_prices.return_value = df

        with patch.object(gen, "calculate_indicators") as mock_ind, \
             patch.object(gen, "is_profitable", return_value=True):
            ind_df = df.copy()
            ind_df["SMA_5"] = 195.0
            ind_df["SMA_50"] = 180.0
            ind_df["SMA_200"] = 150.0
            ind_df["RSI_14"] = 35.0  # oversold in Bull (<38), but NOT in Volatile (<30) or Bear (<25)
            mock_ind.return_value = ind_df

            # BULL regime: should emit signal (35 < 38)
            signals_bull = gen.generate_raw_signals(mock_db, ["MC.PA"], current_regime="BULL")
            self.assertEqual(len(signals_bull), 1)
            self.assertEqual(signals_bull[0].lineage.get("dynamic_rsi_threshold"), 38.0)
            self.assertIn("adaptive 38 in BULL", signals_bull[0].reason)

            # BEAR regime: RSI 35 is NOT oversold (needs < 25)
            signals_bear = gen.generate_raw_signals(mock_db, ["MC.PA"], current_regime="BEAR")
            self.assertEqual(len(signals_bear), 0)

    def test_05_trade_card_adaptive_rationale_rendering(self):
        """Verify render_signal_card renders the adaptive rationale explanation."""
        lineage = {
            "dynamic_rsi_threshold": 38.0,
            "current_regime": "BULL",
            "rsi_14": 32.5,
            "ml_probability": 0.78,
        }

        card = render_signal_card(
            ticker="MC.PA",
            title="LVMH (MC.PA)",
            signal_type="BUY",
            score=86.0,
            qty=4,
            reason="Adaptive dip",
            lineage=lineage,
        )

        self.assertIn("adaptive threshold (38)", card)
        self.assertIn("BULL regime", card)
        self.assertIn("RSI (32.5)", card)


if __name__ == "__main__":
    unittest.main()
