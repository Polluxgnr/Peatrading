"""Unit Tests for Phase 3: Market Adapters, Fundamentals and CPU-Bound Isolation."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
for sub in ("00_data_sensors", "00_data_sensors/adapters", "01_memory_core", "04_orchestrator_ai"):
    sys.path.insert(0, str(ROOT / sub))

from cpu_isolator import CpuTaskIsolator
from fundamentals_adapter import FmpFundamentalsAdapter
from hub import DataIngestionHub
from market_adapter import YFinanceMarketAdapter


def _dummy_heavy_math(n: int) -> int:
    """CPU-bound task helper for process isolation test."""
    total = 0
    for i in range(n):
        total += i * i
    return total


class TestPhase3CpuAndMarketSuite(unittest.TestCase):

    def test_01_cpu_task_isolator_execution(self):
        """Verify CpuTaskIsolator offloads tasks to executor and returns valid results."""
        isolator = CpuTaskIsolator(max_workers=2)

        async def _run():
            return await isolator.run_in_process(_dummy_heavy_math, 1000)

        result = asyncio.run(_run())
        expected = sum(i * i for i in range(1000))
        self.assertEqual(result, expected)

    def test_02_yfinance_market_adapter_structure(self):
        """Verify YFinanceMarketAdapter downloads, cleans and returns valid DuckDB schema."""
        adapter = YFinanceMarketAdapter(chunk_size=10)

        mock_raw = pd.DataFrame(
            {
                ("Close", "MC.PA"): [750.0, 755.0],
                ("Open", "MC.PA"): [745.0, 750.0],
                ("High", "MC.PA"): [755.0, 760.0],
                ("Low", "MC.PA"): [740.0, 748.0],
                ("Volume", "MC.PA"): [100000, 120000],
            },
            index=pd.to_datetime(["2026-08-14", "2026-08-15"]),
        )

        with patch("yfinance.download", return_value=mock_raw):
            df = asyncio.run(adapter.fetch_ohlcv(["MC.PA"], lookback_days=5))
            self.assertFalse(df.empty)
            for col in ("Ticker", "Date", "Open", "High", "Low", "Close", "Volume"):
                self.assertIn(col, df.columns)
            self.assertEqual(df["Ticker"].iloc[0], "MC.PA")
            self.assertEqual(len(df), 2)

    def test_03_fmp_fundamentals_adapter_emission(self):
        """Verify FmpFundamentalsAdapter emits FUNDAMENTAL_PIOTROSKI signals."""
        adapter = FmpFundamentalsAdapter(tickers=["MC.PA"])
        with patch.object(adapter.sensor, "calculate_piotroski_score", return_value=(8, {"roa_positive": 1, "cfo_positive": 1})):
            signals = asyncio.run(adapter.fetch())
            self.assertEqual(len(signals), 1)
            sig = signals[0]
            self.assertEqual(sig.signal_type, "FUNDAMENTAL_PIOTROSKI")
            self.assertEqual(sig.value, 8.0)
            self.assertEqual(sig.source, "FMP/YF")
            self.assertEqual(sig.metadata.get("is_pass"), True)

    def test_04_data_hub_fetch_and_store_market_data(self):
        """Verify DataIngestionHub coordinates market data fetch and DuckDB storage."""
        hub = DataIngestionHub(adapters=[])
        mock_df = pd.DataFrame(
            {
                "Ticker": ["MC.PA"],
                "Date": [pd.to_datetime("2026-08-15")],
                "Open": [750.0],
                "High": [760.0],
                "Low": [748.0],
                "Close": [755.0],
                "Volume": [120000],
            }
        )

        mock_db = MagicMock()
        mock_db.upsert_daily_ohlcv.return_value = 1

        with patch.object(YFinanceMarketAdapter, "_sync_fetch_ohlcv", return_value=mock_df):
            count = asyncio.run(hub.fetch_and_store_market_data(["MC.PA"], mock_db, lookback_days=30))
            self.assertEqual(count, 1)
            mock_db.upsert_daily_ohlcv.assert_called_once()



if __name__ == "__main__":
    unittest.main()
