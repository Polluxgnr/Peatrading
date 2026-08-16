"""Unit Tests for Advanced Interactive Charts & Glass-Box Explainability."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go

ROOT = Path(__file__).resolve().parent.parent
for sub in ("00_data_sensors", "01_memory_core", "02_quant_engine", "03_risk_portfolio", "04_orchestrator_ai", "05_interfaces", "05_interfaces/components"):
    sys.path.insert(0, str(ROOT / sub))

from charts import (
    render_advanced_price_chart,
    render_rsi_chart,
    render_statarb_zscore_chart,
)


class TestInteractiveChartsSuite(unittest.TestCase):

    def setUp(self):
        dates = pd.date_range("2025-01-01", periods=150, freq="D")
        prices = np.linspace(100.0, 180.0, 150)
        self.ohlcv_df = pd.DataFrame(
            {
                "Open": prices - 1.0,
                "High": prices + 2.0,
                "Low": prices - 2.0,
                "Close": prices,
                "Volume": [10000] * 150,
            },
            index=dates,
        )
        self.sma_50 = pd.Series(prices - 5.0, index=dates)
        self.sma_200 = pd.Series(prices - 15.0, index=dates)
        self.hmm_regimes = pd.Series(["BULL"] * 75 + ["VOLATILE"] * 75, index=dates)

    def test_01_render_advanced_price_chart(self):
        """Verify render_advanced_price_chart builds candlestick, SMAs, and HMM shapes."""
        fig = render_advanced_price_chart(
            ticker="MC.PA",
            ohlcv_df=self.ohlcv_df,
            hmm_regimes=self.hmm_regimes,
            sma_50=self.sma_50,
            sma_200=self.sma_200,
        )

        self.assertIsInstance(fig, go.Figure)
        trace_names = [t.name for t in fig.data]
        self.assertIn("Cours", trace_names)
        self.assertIn("SMA 50", trace_names)
        self.assertIn("SMA 200", trace_names)
        # Should have background highlight shapes for HMM intervals
        self.assertGreaterEqual(len(fig.layout.shapes), 2)

    def test_02_render_rsi_chart(self):
        """Verify render_rsi_chart builds RSI line with adaptive dynamic threshold."""
        dates = pd.date_range("2025-01-01", periods=100, freq="D")
        rsi_vals = np.sin(np.linspace(0, 10, 100)) * 30 + 50
        rsi_series = pd.Series(rsi_vals, index=dates)

        fig = render_rsi_chart(rsi_series, dynamic_threshold=38.0)
        self.assertIsInstance(fig, go.Figure)
        self.assertEqual(len(fig.data), 1)
        self.assertEqual(fig.data[0].name, "RSI 14")
        # Layout should have 0-100 range and shapes
        self.assertEqual(fig.layout.yaxis.range, (0, 100))
        self.assertGreaterEqual(len(fig.layout.shapes), 2)

    def test_03_render_statarb_zscore_chart(self):
        """Verify render_statarb_zscore_chart creates Z-Score chart with +/- 2 sigma boundaries."""
        dates = pd.date_range("2025-01-01", periods=120, freq="D")
        z_vals = np.random.normal(0, 1.2, 120)
        z_series = pd.Series(z_vals, index=dates)

        fig = render_statarb_zscore_chart(
            dates=dates,
            zscores=z_series,
            ticker_a="MC.PA",
            ticker_b="OR.PA",
            threshold=2.0,
        )

        self.assertIsInstance(fig, go.Figure)
        self.assertIn("MC.PA", fig.data[0].name)
        # Should have threshold shapes & lines
        self.assertGreaterEqual(len(fig.layout.shapes), 2)


if __name__ == "__main__":
    unittest.main()
