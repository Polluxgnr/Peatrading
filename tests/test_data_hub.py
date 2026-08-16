"""Unit Tests for Alternative Data Adapters and Central DataIngestionHub."""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
for sub in ("00_data_sensors", "00_data_sensors/adapters", "01_memory_core"):
    sys.path.insert(0, str(ROOT / sub))

from adapters.amf_adapter import AmfShortAdapter
from adapters.base_adapters import AbstractPollAdapter
from adapters.macro_adapter import MacroAlphaAdapter
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

    def test_02_macro_alpha_adapter_fetch(self):
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

    def test_03_data_hub_concurrent_gather(self):
        """Verify DataIngestionHub concurrently queries all registered adapters."""
        hub = DataIngestionHub()
        hub.register_adapter(MockCustomAdapter())

        amf = AmfShortAdapter(isins=["FR0000120271"], tickers=["TTE.PA"])
        with patch.object(amf.scraper, "get_short_interest", return_value=1.1):
            hub.register_adapter(amf)

            signals = asyncio.run(hub.fetch_all_alternative_signals())
            self.assertTrue(len(signals) >= 2)
            types = {s.signal_type for s in signals}
            self.assertIn("INSIDER_TRADE", types)
            self.assertIn("SHORT_INTEREST", types)

    def test_04_save_signals_to_sqlite(self):
        """Verify signals are persisted and upserted into SQLite alternative_signals table."""
        conn = sqlite3.connect(":memory:")
        hub = DataIngestionHub()

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
                value=16.5,
                confidence=1.0,
                source="Yahoo/ECB",
                metadata={"index": "^V2TX"},
            ),
        ]

        count = hub.save_signals_to_sqlite(signals, conn)
        self.assertEqual(count, 2)

        # Query back
        cursor = conn.execute("SELECT ticker, signal_type, value, source FROM alternative_signals ORDER BY ticker")
        rows = cursor.fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], "MARCHE")
        self.assertEqual(rows[0][1], "MACRO_VIX")
        self.assertEqual(rows[0][2], 16.5)
        self.assertEqual(rows[1][0], "SAN.PA")
        self.assertEqual(rows[1][1], "SHORT_INTEREST")

        # Test upsert on same signature
        updated_signals = [
            AlternativeSignal(
                ticker="SAN.PA",
                ts=signals[0].ts,
                signal_type="SHORT_INTEREST",
                value=2.5,
                confidence=0.9,
                source="AMF_BDIF",
                metadata={"isin": "FR0000120578", "updated": True},
            )
        ]
        hub.save_signals_to_sqlite(updated_signals, conn)
        cursor = conn.execute("SELECT value, confidence FROM alternative_signals WHERE ticker='SAN.PA'")
        row = cursor.fetchone()
        self.assertEqual(row[0], 2.5)
        self.assertEqual(row[1], 0.9)


if __name__ == "__main__":
    unittest.main()
