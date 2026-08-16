"""Unit Tests for Data Quality Gateway, Pipeline Hardening, and Outlier Handling."""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
for sub in ("00_data_sensors", "00_data_sensors/adapters", "01_memory_core", "02_quant_engine", "03_risk_portfolio", "04_orchestrator_ai", "05_interfaces", "06_api"):
    sys.path.insert(0, str(ROOT / sub))

from data_contracts import MarketTick
from data_quality import DataQualityGateway
from duckdb_manager import TimeSeriesDB
from market_data_adapter import YFinanceMarketDataAdapter


class TestDataQualityAndHardeningSuite(unittest.TestCase):

    def test_01_gateway_forward_fill_and_stale_drop(self):
        """Verify DataQualityGateway forward-fills up to 3 days and drops longer missing spans."""
        gateway = DataQualityGateway(max_ffill_limit=3)

        dates = pd.date_range("2025-01-01", periods=8, freq="D")
        # Row 0: 100, Row 1: NaN, Row 2: NaN, Row 3: NaN, Row 4: NaN (4th consecutive NaN -> drop), Row 5: 105, Row 6: NaN, Row 7: 110
        prices = [100.0, np.nan, np.nan, np.nan, np.nan, 105.0, np.nan, 110.0]
        df = pd.DataFrame(
            {
                "Ticker": "MC.PA",
                "Date": dates,
                "Open": prices,
                "High": prices,
                "Low": prices,
                "Close": prices,
                "Volume": [1000] * 8,
            }
        )

        res = gateway.validate_ohlcv_batch(df)

        self.assertFalse(res.empty)
        # Row 4 should have been dropped because 4th consecutive missing > limit 3
        self.assertIn("is_outlier", res.columns)
        self.assertEqual(len(res), 7)  # 8 - 1 dropped

    def test_02_gateway_outlier_detection(self):
        """Verify DataQualityGateway tags return spikes > 40% as is_outlier=True."""
        gateway = DataQualityGateway(outlier_return_threshold=0.40)

        dates = pd.date_range("2025-01-01", periods=10, freq="D")
        # Normal prices around 100, then day 5 jumps to 160 (+60% spike)
        prices = [100.0, 101.0, 99.5, 100.5, 102.0, 165.0, 102.0, 101.5, 103.0, 102.5]
        df = pd.DataFrame(
            {
                "Ticker": "MC.PA",
                "Date": dates,
                "Open": prices,
                "High": [p + 1 for p in prices],
                "Low": [p - 1 for p in prices],
                "Close": prices,
                "Volume": [5000] * 10,
            }
        )

        res = gateway.validate_ohlcv_batch(df)

        self.assertEqual(len(res), 10)
        outliers = res[res["is_outlier"] == True]
        self.assertGreaterEqual(len(outliers), 1)
        # Index 5 (165.0) and Index 6 (-38% / drop back)
        self.assertTrue(res.iloc[5]["is_outlier"])

    def test_03_duckdb_upsert_with_outliers(self):
        """Verify TimeSeriesDB registers and persists is_outlier column."""
        tsdb = TimeSeriesDB()
        mock_conn = MagicMock()

        with patch.object(tsdb, "_connect") as mock_connect:
            mock_connect.return_value.__enter__.return_value = mock_conn
            mock_connect.return_value.__exit__.return_value = None

            # Test init_db
            tsdb.init_db()
            self.assertTrue(mock_conn.execute.called)

            dates = pd.date_range("2025-01-01", periods=5, freq="D")
            prices = [100.0, 102.0, 180.0, 103.0, 104.0]
            df = pd.DataFrame(
                {
                    "Ticker": "AI.PA",
                    "Date": dates,
                    "Open": prices,
                    "High": prices,
                    "Low": prices,
                    "Close": prices,
                    "Volume": [10000] * 5,
                }
            )

            inserted = tsdb.upsert_ohlcv(df)
            self.assertEqual(inserted, 5)
            self.assertTrue(mock_conn.register.called)
            # Verify registered dataframe has is_outlier column
            reg_args = mock_conn.register.call_args[0]
            self.assertEqual(reg_args[0], "incoming_ohlcv")
            self.assertIn("is_outlier", reg_args[1].columns)


    def test_04_market_data_adapter_tick(self):
        """Verify YFinanceMarketDataAdapter produces valid MarketTick contract."""
        adapter = YFinanceMarketDataAdapter()
        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_t = MagicMock()
            mock_df = pd.DataFrame(
                {"Close": [820.50], "Volume": [25000]},
                index=[pd.Timestamp.now()],
            )
            mock_t.history.return_value = mock_df
            mock_ticker_cls.return_value = mock_t

            tick = adapter.fetch_latest_tick("MC.PA")
            self.assertIsInstance(tick, MarketTick)
            self.assertEqual(tick.ticker, "MC.PA")
            self.assertEqual(tick.price, 820.50)
            self.assertEqual(tick.volume, 25000)


if __name__ == "__main__":
    unittest.main()
