"""Unit Tests for AMF Short Scraper, Autonomous Earnings Calendar, and Cascade Veto."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

ROOT = Path(__file__).resolve().parent.parent
for sub in ("00_data_sensors", "00_data_sensors/scrapers", "01_memory_core", "02_quant_engine", "03_risk_portfolio", "04_orchestrator_ai", "05_interfaces", "06_api"):
    sys.path.insert(0, str(ROOT / sub))

from amf_short_scraper import AmfShortScraper
from earnings_updater import run_earnings_sync, _extract_universe_tickers
from signal_priority_cascade import SignalOrchestrator
from data_models import PortfolioState, Signal, SignalStatus, SignalType


class TestAmfAndEarningsSuite(unittest.TestCase):

    def test_01_amf_short_scraper_parsing(self):
        """Verify AMF BDIF JSON parser calculates active short interest accurately."""
        scraper = AmfShortScraper()

        # Mock JSON with multiple funds reporting positions on same ISIN
        mock_payload = {
            "datas": [
                {"detenteur": "Citadel Advisors", "isin": "FR0000121014", "position": "1.25", "datePosition": "2026-03-01"},
                {"detenteur": "Millennium Capital", "isin": "FR0000121014", "position": 0.85, "datePosition": "2026-03-05"},
                {"detenteur": "Citadel Advisors", "isin": "FR0000121014", "position": "1.40", "datePosition": "2026-03-10"}, # Updated position
                {"detenteur": "Qube Research", "isin": "FR0000120321", "position": "0.60"}, # Different ISIN
            ]
        }

        total = scraper._parse_short_payload(mock_payload, "FR0000121014")
        # Citadel latest = 1.40, Millennium = 0.85 -> Total = 2.25
        self.assertAlmostEqual(total, 2.25, places=2)

    def test_02_amf_short_scraper_empty_fallback(self):
        """Verify scraper gracefully returns 0.0 for unknown or empty responses."""
        scraper = AmfShortScraper()
        self.assertEqual(scraper.get_short_interest(""), 0.0)
        self.assertEqual(scraper.get_short_interest("INVALID"), 0.0)
        self.assertEqual(scraper._parse_short_payload({}, "FR0000121014"), 0.0)
        self.assertEqual(scraper._parse_short_payload([], "FR0000121014"), 0.0)

    def test_03_extract_universe_tickers(self):
        """Verify universe ticker extraction prioritizes liquid assets."""
        tickers = _extract_universe_tickers(ROOT / "config" / "pea_universe.yaml", max_tickers=15)
        self.assertGreaterEqual(len(tickers), 5)
        self.assertIn("AI.PA", tickers)

    def test_04_earnings_sync_execution(self):
        """Verify autonomous earnings updater writes structured YAML calendar."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            # Create mock universe
            univ = {
                "universe": {
                    "Luxury": [{"ticker": "MC.PA", "srd": True}]
                }
            }
            with open(tmp_path / "pea_universe.yaml", "w", encoding="utf-8") as fh:
                yaml.dump(univ, fh)

            # Run earnings sync with mock
            future_date = (date.today() + timedelta(days=20)).strftime("%Y-%m-%d")
            with patch("earnings_updater.fetch_ticker_corporate_events", return_value={future_date: "Q3 Earnings"}):
                res_count = run_earnings_sync(config_dir=tmp_path, max_tickers=5)

            self.assertEqual(res_count, 1)
            cal_file = tmp_path / "earnings_calendar.yaml"
            self.assertTrue(cal_file.exists())

            with open(cal_file, "r", encoding="utf-8") as fh:
                saved = yaml.safe_load(fh)
            self.assertIn("events", saved)
            self.assertIn("MC.PA", saved["events"])
            self.assertEqual(saved["events"]["MC.PA"].get(future_date), "Q3 Earnings")

    def test_05_short_interest_cascade_veto(self):
        """Verify SignalOrchestrator rejects signals on tickers with > 3.0% short interest."""
        orchestrator = SignalOrchestrator()

        # Mock short scraper to return 4.5% for MC.PA
        mock_scraper = MagicMock()
        mock_scraper.get_short_interest.return_value = 4.5
        orchestrator.amf_scraper = mock_scraper

        pf = PortfolioState(
            cash_available=10000.0,
            total_equity=10000.0,
            positions=[],
            last_updated=datetime.now(timezone.utc),
        )

        sig = Signal(
            ticker="MC.PA",
            signal_type=SignalType.BUY,
            score=88.0,
            status=SignalStatus.PENDING,
            lineage={},
        )

        res = orchestrator.process_raw_signals(
            raw_signals=[sig],
            portfolio=pf,
            current_prices={"MC.PA": 600.0},
            vix_level=16.0,
        )

        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].status, SignalStatus.REJECTED)
        self.assertIn("High Short Interest (4.5%)", res[0].reason)
        self.assertEqual(res[0].lineage.get("short_interest"), 4.5)


if __name__ == "__main__":
    unittest.main()
