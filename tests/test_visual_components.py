"""Unit Tests for Modular Visual Analytics & Plotly Components."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go

ROOT = Path(__file__).resolve().parent.parent
for sub in ("05_interfaces", "05_interfaces/components", "01_memory_core"):
    sys.path.insert(0, str(ROOT / sub))

from charts import (
    render_hmm_candlestick_chart,
    render_macro_thermometer_gauge,
    render_rsi_chart,
    render_statarb_zscore_chart,
)
from trade_cards import render_signal_card


class TestVisualComponentsSuite(unittest.TestCase):

    def setUp(self):
        dates = pd.date_range("2026-01-01", periods=100, freq="D")
        self.sample_df = pd.DataFrame(
            {
                "Open": np.linspace(700, 750, 100),
                "High": np.linspace(710, 760, 100),
                "Low": np.linspace(690, 740, 100),
                "Close": np.linspace(705, 745, 100),
                "Volume": [100000] * 100,
            },
            index=dates,
        )
        self.sma50 = self.sample_df["Close"].rolling(50).mean()
        self.sma200 = self.sample_df["Close"].rolling(100).mean()
        self.regimes = pd.Series(["BULL"] * 50 + ["VOLATILE"] * 50, index=dates)
        self.rsi_series = pd.Series(np.linspace(25, 75, 100), index=dates)
        self.zscores = pd.Series(np.random.normal(0, 1, 100), index=dates)

    def test_01_render_hmm_candlestick_chart(self):
        """Verify render_hmm_candlestick_chart returns a valid plotly Figure."""
        fig = render_hmm_candlestick_chart(
            ticker="MC.PA",
            df=self.sample_df,
            sma50=self.sma50,
            sma200=self.sma200,
            regime_series=self.regimes,
        )
        self.assertIsInstance(fig, go.Figure)
        self.assertIn("data", fig.to_dict())
        self.assertTrue(len(fig.data) >= 1)

    def test_02_render_statarb_zscore_chart(self):
        """Verify render_statarb_zscore_chart returns a valid plotly Figure with reference thresholds."""
        fig = render_statarb_zscore_chart(
            pair_label="MC.PA vs OR.PA",
            z_score_series=self.zscores,
            threshold=2.0,
        )
        self.assertIsInstance(fig, go.Figure)
        self.assertTrue(len(fig.data) >= 1)

    def test_03_render_macro_thermometer_gauge(self):
        """Verify render_macro_thermometer_gauge returns a valid half-circle gauge."""
        fig = render_macro_thermometer_gauge(
            attack_pct=0.75,
            defense_pct=0.25,
            mode="ATTACK",
        )
        self.assertIsInstance(fig, go.Figure)
        self.assertEqual(len(fig.data), 1)
        self.assertEqual(fig.data[0].type, "indicator")

    def test_04_render_rsi_chart(self):
        """Verify render_rsi_chart returns a valid RSI oscillator figure."""
        fig = render_rsi_chart(self.rsi_series, dynamic_threshold=35.0)
        self.assertIsInstance(fig, go.Figure)

    def test_05_trade_card_shap_attribution(self):
        """Verify render_signal_card properly formats positive and negative SHAP driver badges."""
        lineage = {
            "ml_probability": 0.84,
            "shap_values": {
                "vol_zscore": 0.084,
                "rsi_14": -0.032,
                "trend_sma200": 0.045,
            },
        }

        html = render_signal_card(
            ticker="MC.PA",
            title="LVMH (MC.PA)",
            signal_type="BUY",
            score=92.0,
            qty=5,
            reason="Oversold bounce",
            lineage=lineage,
        )

        self.assertIn("vol_zscore", html)
        self.assertIn("rsi_14", html)
        self.assertIn("🟢", html)
        self.assertIn("🔴", html)


if __name__ == "__main__":
    unittest.main()
