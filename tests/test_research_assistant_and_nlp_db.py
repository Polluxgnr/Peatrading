"""Unit Tests for Phase 7: Research Assistant Paradigm, Double Price Verification & NLP News DB."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
for sub in (
    "00_data_sensors",
    "00_data_sensors/adapters",
    "01_memory_core",
    "02_quant_engine",
    "03_risk_portfolio",
    "04_orchestrator_ai",
    "05_interfaces",
):
    sys.path.insert(0, str(ROOT / sub))

from correlation_firewall import CorrelationFirewall
from data_contracts import MarketTick
from data_models import Position, PortfolioState, Signal, SignalType
from data_quality import DataQualityGateway
from market_data_adapter import YFinanceMarketDataAdapter
from signal_priority_cascade import SignalOrchestrator
from sqlite_portfolio import PortfolioDB


class TestResearchAssistantSuite(unittest.TestCase):

    def setUp(self):
        self.temp_db = ROOT / "database" / "test_phase7_portfolio.db"
        if self.temp_db.exists():
            self.temp_db.unlink()
        self.db = PortfolioDB(db_path=self.temp_db)
        self.db.init_db()

    def tearDown(self):
        if self.temp_db.exists():
            self.temp_db.unlink()

    def test_01_double_price_verification_divergence_warning(self):
        """Verify fetch_latest_tick flags price divergence > 1.5% in metadata without dropping data."""
        adapter = YFinanceMarketDataAdapter()

        with patch("yfinance.Ticker") as mock_yf, patch("scrapers.bourso_scraper.BoursoramaScraper") as mock_bourso:
            # Mock yfinance price = 700.00 EUR
            mock_t = MagicMock()
            mock_df = pd.DataFrame({"Close": [700.00], "Volume": [10000.0]})
            mock_t.history.return_value = mock_df
            mock_yf.return_value = mock_t

            # Mock Boursorama price = 720.00 EUR (delta: 2.85% > 1.5%)
            mock_b = MagicMock()
            mock_b.get_instrument_profile.return_value = {"price": 720.00, "name": "LVMH"}
            mock_bourso.return_value = mock_b

            tick = adapter.fetch_latest_tick("MC.PA")

            self.assertIsNotNone(tick)
            self.assertEqual(tick.ticker, "MC.PA")
            self.assertEqual(tick.price, 700.00)
            self.assertIn("price_warning", tick.metadata)
            self.assertIn("diverge by", tick.metadata["price_warning"])

    def test_02_mad_based_outlier_detection_preserves_all_rows(self):
        """Verify DataQualityGateway flags returns > 5 MADs without deleting rows."""
        gateway = DataQualityGateway(mad_threshold=5.0)

        dates = pd.date_range("2026-01-01", periods=20, freq="D")
        # Steady prices around 100 with small daily moves +/- 0.5%
        prices = [100.0 + (i % 2) * 0.5 for i in range(19)]
        # Add single spike at index 19 (+50%)
        prices.append(150.0)

        df = pd.DataFrame(
            {
                "Ticker": ["OR.PA"] * 20,
                "Date": dates,
                "Open": prices,
                "High": prices,
                "Low": prices,
                "Close": prices,
                "Volume": [5000] * 20,
            }
        )

        cleaned = gateway.validate_ohlcv_batch(df)

        # All 20 rows must be preserved (zero deletion of outliers)
        self.assertEqual(len(cleaned), 20)
        self.assertIn("is_outlier", cleaned.columns)
        # Outlier row is flagged True
        self.assertTrue(cleaned.iloc[-1]["is_outlier"])

    def test_03_indicators_over_blockers_correlation_warnings(self):
        """Verify CorrelationFirewall returns warning list and SignalOrchestrator attaches them without vetoing."""
        firewall = CorrelationFirewall()

        portfolio = PortfolioState(
            cash_available=5000.0,
            total_equity=10000.0,
            positions=[
                Position(
                    ticker="MC.PA",
                    qty_shares=10,
                    avg_entry_price=600.0,
                    current_price=600.0,
                    sector="Consumer Cyclical",
                    last_updated=datetime.now(timezone.utc),
                )
            ],
            last_updated=datetime.now(timezone.utc),
        )

        # 1. Sector check returns warning list instead of bool False
        sector_warns = firewall.check_sector_limit("RMS.PA", portfolio)
        self.assertIsInstance(sector_warns, list)

        # 2. Mock high correlation
        mock_tsdb = MagicMock()
        mock_tsdb.get_historical_prices.return_value = pd.DataFrame(
            {"Close": [100.0 + i for i in range(30)], "Date": pd.date_range("2026-01-01", periods=30, freq="D")}
        )
        corr_warns = firewall.check_correlation("RMS.PA", portfolio, mock_tsdb)
        self.assertIsInstance(corr_warns, list)

        # 3. Test Orchestrator attaches warnings without dropping
        orchestrator = SignalOrchestrator(timeseries_db=mock_tsdb)
        signal = Signal(
            ticker="RMS.PA",
            signal_type=SignalType.BUY,
            score=88.0,
            reason="Mean Reversion Dip",
            price=2000.0,
            target_qty=1,
        )

        processed = orchestrator.process_raw_signals(
            raw_signals=[signal],
            portfolio=portfolio,
            current_prices={"RMS.PA": 2000.0, "MC.PA": 600.0},
            vix_level=15.0,
        )

        self.assertEqual(len(processed), 1)
        self.assertEqual(processed[0].ticker, "RMS.PA")
        # Ensure warning attached to reason/lineage
        self.assertTrue("risk_warnings" in processed[0].lineage or "⚠️" in processed[0].reason)

    def test_04_nlp_news_impact_db_schema_and_update(self):
        """Verify news_master table stores price_impact_1d, price_impact_5d, and nlp_summary."""
        # Insert test news article
        news_id = "test_news_001"
        self.db.save_news_items(
            [
                {
                    "id": news_id,
                    "ticker": "MC.PA",
                    "title": "LVMH publie d'excellents resultats T3",
                    "source": "Reuters",
                    "url": "https://reuters.com/news/123",
                    "published_at": "2026-08-16T12:00:00Z",
                    "sentiment_score": 0.85,
                    "sentiment_label": "Positive",
                }
            ]
        )

        # Update NLP impact forward returns
        self.db.update_news_nlp_impact(
            news_id=news_id,
            price_impact_1d=0.024,  # +2.4% at T+1
            price_impact_5d=0.051,  # +5.1% at T+5
            nlp_summary="Forte surperformance de la division Mode & Maroquinerie.",
        )

        with self.db._connect() as conn:
            row = conn.execute(
                "SELECT price_impact_1d, price_impact_5d, nlp_summary FROM news_master WHERE id = ?;",
                (news_id,),
            ).fetchone()

            self.assertIsNotNone(row)
            self.assertAlmostEqual(row["price_impact_1d"], 0.024)
            self.assertAlmostEqual(row["price_impact_5d"], 0.051)
            self.assertIn("Mode & Maroquinerie", row["nlp_summary"])


if __name__ == "__main__":
    unittest.main()
