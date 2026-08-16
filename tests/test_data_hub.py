"""Unit Tests for Alternative Data Adapters and Central DataIngestionHub."""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
for sub in ("00_data_sensors", "00_data_sensors/adapters", "01_memory_core"):
    sys.path.insert(0, str(ROOT / sub))

from adapters.amf_adapter import AmfInsiderAdapter, AmfShortAdapter
from adapters.base_adapters import AbstractPollAdapter
from adapters.bourso_adapter import BoursoUniverseAdapter
from adapters.macro_adapter import MacroAlphaAdapter
from adapters.news_adapter import ConsolidatedNewsAdapter
from data_contracts import AlternativeSignal
from hub import DataIngestionHub


class MockCustomAdapter(AbstractPollAdapter):
    interval_seconds: int = 600

    async def fetch(self) -> list[AlternativeSignal]:
        return [
            AlternativeSignal(
                ticker="RMS.PA",
                signal_type="INSIDER_TRADE",
                value=1250000.0,
                confidence=1.0,
                source="AMF_INSIDERS",
                metadata={"declarant": "Hermes Family"},
            )
        ]


class TestDataHubSuite(unittest.TestCase):

    def test_01_amf_short_adapter_fetch(self):
        """Verify AmfShortAdapter emits valid AlternativeSignal objects."""
        adapter = AmfShortAdapter(isins=["FR0000121014"], tickers=["MC.PA"])
        with patch.object(adapter.scraper, "get_short_interest", return_value=4.25):
            signals = asyncio.run(adapter.fetch())
            self.assertTrue(len(signals) >= 1)
            sig = signals[0]
            self.assertIsInstance(sig, AlternativeSignal)
            self.assertEqual(sig.signal_type, "SHORT_INTEREST")
            self.assertEqual(sig.value, 4.25)
            self.assertEqual(sig.source, "AMF_BDIF")
            self.assertEqual(sig.metadata.get("threshold_breach"), True)

    def test_02_amf_insider_adapter_fetch(self):
        """Verify AmfInsiderAdapter parses transactions and emits direction signals."""
        adapter = AmfInsiderAdapter(tickers=["MC.PA"])
        mock_df = pd.DataFrame(
            {
                "Date": ["2026-08-14", "2026-08-15"],
                "Transaction": ["Acquisition d'actions", "Achat"],
                "Volume": [1000, 500],
            }
        )
        if adapter.scraper is not None:
            with patch.object(adapter.scraper, "get_recent_declarations", return_value=mock_df):
                signals = asyncio.run(adapter.fetch())
                self.assertEqual(len(signals), 1)
                self.assertEqual(signals[0].signal_type, "INSIDER_TX")
                self.assertEqual(signals[0].value, 1.0)
                self.assertEqual(signals[0].metadata.get("buys_count"), 2)

    def test_03_consolidated_news_adapter_fetch(self):
        """Verify ConsolidatedNewsAdapter gathers RSS news items."""
        adapter = ConsolidatedNewsAdapter(tickers=["MC.PA"])
        mock_feed_items = [
            {
                "id": "rss_1",
                "ticker": "MC.PA",
                "title": "LVMH annonce des resultats solides",
                "source": "Boursorama",
                "url": "https://boursorama.com/art1",
                "published_at": "2026-08-16T10:00:00Z",
                "sentiment_score": 0.65,
            }
        ]
        with patch("adapters.news_adapter.parse_rss_feed", return_value=mock_feed_items):
            signals = asyncio.run(adapter.fetch())
            self.assertTrue(len(signals) >= 1)
            self.assertEqual(signals[0].signal_type, "NEWS_SENTIMENT")
            self.assertIn("LVMH", signals[0].metadata.get("headline", ""))

    def test_04_bourso_universe_adapter_fetch(self):
        """Verify BoursoUniverseAdapter harvests PEA constituents."""
        adapter = BoursoUniverseAdapter()
        mock_universe = [
            {"ticker": "MC.PA", "sector": "Consumer Cyclical"},
            {"ticker": "OR.PA", "sector": "Consumer Defensive"},
        ]
        if adapter.scraper is not None:
            with patch.object(adapter.scraper, "get_pea_universe", return_value=mock_universe):
                signals = asyncio.run(adapter.fetch())
                self.assertEqual(len(signals), 1)
                self.assertEqual(signals[0].signal_type, "UNIVERSE_UPDATE")
                self.assertEqual(signals[0].value, 2.0)
                self.assertEqual(signals[0].metadata.get("total_constituents"), 2)

    def test_05_macro_alpha_adapter_fetch(self):
        """Verify MacroAlphaAdapter emits MACRO_VIX and MACRO_SPREAD signals."""
        adapter = MacroAlphaAdapter()
        with patch.object(adapter.sensor, "get_european_vix", return_value=17.8), \
             patch.object(adapter.sensor, "get_oat_bund_spread", return_value=74.2):
            signals = asyncio.run(adapter.fetch())
            self.assertEqual(len(signals), 2)
            types = {s.signal_type for s in signals}
            self.assertIn("MACRO_VIX", types)
            self.assertIn("MACRO_SPREAD", types)
            vix_sig = next(s for s in signals if s.signal_type == "MACRO_VIX")
            self.assertEqual(vix_sig.value, 17.8)
            self.assertEqual(vix_sig.ticker, "MARCHE")

    def test_06_data_hub_default_registration_and_concurrent_gather(self):
        """Verify DataIngestionHub registers all default adapters and runs gather."""
        hub = DataIngestionHub(adapters=[MockCustomAdapter()])
        signals = asyncio.run(hub.fetch_all_alternative_signals())
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].signal_type, "INSIDER_TRADE")

        # Test with default adapters
        hub_defaults = DataIngestionHub()
        self.assertTrue(len(hub_defaults.adapters) >= 5)

    def test_07_save_signals_to_sqlite(self):
        """Verify signals are persisted and upserted into SQLite alternative_signals table."""
        conn = sqlite3.connect(":memory:")
        hub = DataIngestionHub(adapters=[])

        signals = [
            AlternativeSignal(
                ticker="SAN.PA",
                signal_type="SHORT_INTEREST",
                value=0.8,
                confidence=1.0,
                source="AMF_BDIF",
                metadata={"isin": "FR0000120578"},
            ),
            AlternativeSignal(
                ticker="MARCHE",
                signal_type="MACRO_VIX",
                value=15.4,
                confidence=1.0,
                source="MACRO_ALPHA_SENSOR",
                metadata={"regime": "NORMAL"},
            ),
        ]

        saved = hub.save_signals_to_sqlite(signals, conn)
        self.assertEqual(saved, 2)

        cur = conn.cursor()
        rows = cur.execute("SELECT ticker, signal_type, value FROM alternative_signals ORDER BY ticker ASC;").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], "MARCHE")
        self.assertEqual(rows[0][1], "MACRO_VIX")
        self.assertEqual(rows[1][0], "SAN.PA")
        self.assertEqual(rows[1][1], "SHORT_INTEREST")

        # Test Upsert update
        updated_signals = [
            AlternativeSignal(
                ticker="SAN.PA",
                ts=signals[0].ts,
                signal_type="SHORT_INTEREST",
                value=1.5,
                confidence=1.0,
                source="AMF_BDIF",
                metadata={"isin": "FR0000120578", "updated": True},
            )
        ]
        saved2 = hub.save_signals_to_sqlite(updated_signals, conn)
        self.assertEqual(saved2, 1)

        val = cur.execute("SELECT value FROM alternative_signals WHERE ticker='SAN.PA';").fetchone()[0]
        self.assertEqual(val, 1.5)


if __name__ == "__main__":
    unittest.main()
