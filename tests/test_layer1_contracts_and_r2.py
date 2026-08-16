"""Unit Tests for Layer 1 Data Contracts, Base Adapters, and Cloudflare R2 Backup."""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch

import pandas as pd
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parent.parent
for sub in ("00_data_sensors", "00_data_sensors/adapters", "01_memory_core", "tools"):
    sys.path.insert(0, str(ROOT / sub))

from data_contracts import AlternativeSignal, MarketTick
from base_adapters import AbstractMarketDataAdapter, AbstractPollAdapter
import backup_databases


class DummyPollAdapter(AbstractPollAdapter):
    interval_seconds: int = 300

    async def fetch(self) -> List[AlternativeSignal]:
        return [
            AlternativeSignal(
                ticker="MC.PA",
                signal_type="insider_buy",
                value=50000.0,
                confidence=0.95,
                source="amf_test",
                metadata={"declarant": "Arnault"},
            )
        ]


class DummyMarketDataAdapter(AbstractMarketDataAdapter):
    async def fetch_ohlcv(self, tickers: List[str], lookback_days: int = 10) -> pd.DataFrame:
        data = {
            "Open": [100.0, 101.0],
            "High": [105.0, 106.0],
            "Low": [99.0, 100.0],
            "Close": [104.0, 105.0],
            "Volume": [1000, 1200],
        }
        return pd.DataFrame(data)


class TestLayer1ContractsAndR2Suite(unittest.TestCase):

    def test_01_market_tick_contract(self):
        """Verify MarketTick Pydantic validation and serialization."""
        tick = MarketTick(
            ticker="MC.PA",
            price=680.50,
            volume=45000.0,
            source="yfinance",
        )
        self.assertEqual(tick.ticker, "MC.PA")
        self.assertEqual(tick.price, 680.50)
        self.assertEqual(tick.volume, 45000.0)
        self.assertEqual(tick.source, "yfinance")
        self.assertIsInstance(tick.ts, datetime)

        # Invalid price <= 0
        with self.assertRaises(ValidationError):
            MarketTick(ticker="MC.PA", price=-10.0, source="bad")

    def test_02_alternative_signal_contract(self):
        """Verify AlternativeSignal Pydantic validation with default metadata."""
        sig = AlternativeSignal(
            ticker="AI.PA",
            signal_type="sentiment",
            value=85.0,
            confidence=0.9,
            source="finbert",
            metadata={"headline": "Air Liquide signs green hydrogen contract"},
        )
        self.assertEqual(sig.ticker, "AI.PA")
        self.assertEqual(sig.signal_type, "sentiment")
        self.assertEqual(sig.value, 85.0)
        self.assertEqual(sig.confidence, 0.9)
        self.assertEqual(sig.metadata["headline"], "Air Liquide signs green hydrogen contract")

        # Invalid confidence > 1.0
        with self.assertRaises(ValidationError):
            AlternativeSignal(ticker="AI.PA", signal_type="sentiment", value=10.0, confidence=1.5, source="test")

    def test_03_abstract_adapters_implementation(self):
        """Verify subclassing AbstractPollAdapter and AbstractMarketDataAdapter."""
        poller = DummyPollAdapter()
        self.assertEqual(poller.interval_seconds, 300)

        signals = asyncio.run(poller.fetch())
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].ticker, "MC.PA")
        self.assertEqual(signals[0].signal_type, "insider_buy")

        mkt_adapter = DummyMarketDataAdapter()
        df = asyncio.run(mkt_adapter.fetch_ohlcv(["MC.PA"], lookback_days=5))
        self.assertIsInstance(df, pd.DataFrame)
        self.assertEqual(len(df), 2)
        self.assertIn("Close", df.columns)

    def test_04_cloudflare_r2_backup_initialization(self):
        """Verify backup_to_r2_or_s3 configures endpoint_url and region_name='auto' for Cloudflare R2."""
        mock_file = ROOT / "config" / "pea_universe.yaml"
        mock_boto = MagicMock()
        mock_s3 = MagicMock()
        mock_boto.client.return_value = mock_s3

        with patch.dict(sys.modules, {"boto3": mock_boto}):
            success = backup_databases.backup_to_r2_or_s3(
                [mock_file],
                stamp="20260816_160000",
                bucket_name="pea-backups-r2",
                endpoint_url="https://abc123456.r2.cloudflarestorage.com",
                access_key_id="r2_access_key",
                secret_access_key="r2_secret_key",
            )
            self.assertTrue(success)
            mock_boto.client.assert_called_once_with(
                "s3",
                endpoint_url="https://abc123456.r2.cloudflarestorage.com",
                aws_access_key_id="r2_access_key",
                aws_secret_access_key="r2_secret_key",
                region_name="auto",
            )
            self.assertTrue(mock_s3.upload_file.called)


if __name__ == "__main__":
    unittest.main()
