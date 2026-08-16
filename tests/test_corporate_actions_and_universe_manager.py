"""Unit Tests for Corporate Actions Self-Healing and Dynamic PEA Universe Manager."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
for sub in ("00_data_sensors", "00_data_sensors/adapters", "01_memory_core", "02_quant_engine", "03_risk_portfolio", "04_orchestrator_ai", "05_interfaces"):
    sys.path.insert(0, str(ROOT / sub))

from corporate_actions import DataHealer
from duckdb_manager import TimeSeriesDB
from universe_manager import UniverseManager
import main_scheduler


class TestCorporateActionsAndUniverseManagerSuite(unittest.TestCase):

    def test_01_detect_and_heal_split(self):
        """Verify DataHealer detects stock split and triggers historical data wipe & reload."""
        healer = DataHealer()
        mock_tsdb = MagicMock(spec=TimeSeriesDB)
        mock_conn = MagicMock()
        mock_tsdb._connect.return_value.__enter__.return_value = mock_conn

        with patch("yfinance.Ticker") as mock_ticker_cls, \
             patch("yfinance.download") as mock_download:
            
            mock_ticker = MagicMock()
            # Split series with 2:1 split 2 days ago
            dates = [pd.Timestamp.now() - pd.Timedelta(days=2)]
            mock_ticker.splits = pd.Series([2.0], index=dates)
            mock_ticker_cls.return_value = mock_ticker

            # Mock 252-day auto-adjusted history
            hist_df = pd.DataFrame(
                {
                    "Open": [50.0] * 10,
                    "High": [52.0] * 10,
                    "Low": [49.0] * 10,
                    "Close": [51.0] * 10,
                    "Volume": [10000] * 10,
                },
                index=pd.date_range("2025-01-01", periods=10),
            )
            mock_download.return_value = hist_df
            mock_tsdb.upsert_ohlcv.return_value = 10

            healed = healer.detect_and_heal_splits("MC.PA", mock_tsdb)
            self.assertTrue(healed)
            # Verify DELETE query was executed
            self.assertTrue(mock_conn.execute.called)
            del_query = mock_conn.execute.call_args[0][0]
            self.assertIn("DELETE FROM ohlcv_data", del_query)
            # Verify upsert was called with adjusted data
            self.assertTrue(mock_tsdb.upsert_ohlcv.called)

    def test_02_no_split_no_healing(self):
        """Verify DataHealer returns False when no split occurred."""
        healer = DataHealer()
        mock_tsdb = MagicMock(spec=TimeSeriesDB)

        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker.splits = pd.Series(dtype=float)
            mock_ticker.actions = pd.DataFrame()
            mock_ticker_cls.return_value = mock_ticker

            healed = healer.detect_and_heal_splits("AI.PA", mock_tsdb)
            self.assertFalse(healed)

    def test_03_universe_manager_eligibility_sync(self):
        """Verify UniverseManager identifies non-eligible tickers and saves warnings."""
        tmp_dir = Path(tempfile.gettempdir())
        tmp_warnings = tmp_dir / f"test_warnings_{datetime.now().timestamp()}.json"

        mgr = UniverseManager(warnings_path=tmp_warnings)

        with patch.object(mgr, "load_tracked_tickers", return_value=["MC.PA", "OR.PA", "INVALID.PA"]), \
             patch("universe_manager.BoursoramaScraper") as mock_scraper_cls:
            
            mock_scraper = MagicMock()
            # Boursorama only returns MC and OR
            mock_scraper.get_pea_universe.return_value = [
                {"ticker": "MC.PA", "name": "LVMH"},
                {"ticker": "OR.PA", "name": "L'Oreal"},
            ]
            mock_scraper_cls.return_value = mock_scraper

            warnings = mgr.sync_eligibility()
            self.assertIn("INVALID.PA", warnings)
            self.assertNotIn("MC.PA", warnings)
            self.assertNotIn("OR.PA", warnings)

            self.assertTrue(tmp_warnings.exists())
            with open(tmp_warnings, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertIn("INVALID.PA", saved)

            if tmp_warnings.exists():
                try:
                    tmp_warnings.unlink()
                except Exception:
                    pass

    def test_04_market_hours_30min_schedule(self):
        """Verify main_scheduler._PASS_TIMES has 30-minute intervals covering market hours."""
        pass_times = main_scheduler._PASS_TIMES
        self.assertIn("09:00", pass_times)
        self.assertIn("09:30", pass_times)
        self.assertIn("12:00", pass_times)
        self.assertIn("17:30", pass_times)
        self.assertEqual(len(pass_times), 18)  # 18 intervals of 30min between 09:00 and 17:30


if __name__ == "__main__":
    unittest.main()
