"""Unit Tests for Brain Wiring (Bandit, Ensemble, Continuous VIX), UI Decoupling, and OpenInsider."""

from __future__ import annotations

import gc
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
for sub in ("00_data_sensors", "00_data_sensors/scrapers", "01_memory_core", "02_quant_engine", "03_risk_portfolio", "04_orchestrator_ai", "05_interfaces", "06_api"):
    sys.path.insert(0, str(ROOT / sub))

from openinsider_eu_scraper import OpenInsiderEuScraper, clean_numeric_value
from technical_scorer import SignalGenerator
from signal_priority_cascade import SignalOrchestrator
from data_models import PortfolioState, Position, Signal, SignalStatus, SignalType
from internal_api import app


class TestBrainAndDecouplingSuite(unittest.TestCase):

    def test_01_openinsider_numeric_cleaner(self):
        """Verify currency and numeric string parsing in OpenInsider scraper."""
        self.assertEqual(clean_numeric_value("€ 1,200,000"), 1200000.0)
        self.assertEqual(clean_numeric_value("$ 500k"), 500000.0)
        self.assertEqual(clean_numeric_value("12.50 €"), 12.50)
        self.assertEqual(clean_numeric_value("1,250.75"), 1250.75)
        self.assertEqual(clean_numeric_value(""), 0.0)
        self.assertEqual(clean_numeric_value(None), 0.0)
        self.assertEqual(clean_numeric_value(1500), 1500.0)

    def test_02_technical_scorer_bandit_ensemble_lineage(self):
        """Verify SignalGenerator dynamically applies bandit and ensemble weights and records in lineage."""
        dates = pd.date_range("2024-01-01", periods=260, freq="B")
        base = [100.0 + i * 0.5 for i in range(260)]
        close = list(base)
        # Create oversold pullback with 2-bar bounce
        for idx, mult in enumerate([0.95, 0.92, 0.89, 0.86, 0.84, 0.83, 0.85, 0.86]):
            close[-8 + idx] = close[-9] * mult

        mock_df = pd.DataFrame({
            "Ticker": "TEST.PA",
            "Date": dates,
            "Open": close,
            "High": [c * 1.01 for c in close],
            "Low": [c * 0.99 for c in close],
            "Close": close,
            "Volume": [1_000_000] * len(close),
        })

        class _MockDB:
            def get_historical_prices(self, ticker: str, days: int = 252) -> pd.DataFrame:
                return mock_df

        gen = SignalGenerator()
        signals = gen.generate_raw_signals(
            _MockDB(),
            ["TEST.PA"],
            apply_quality_filter=False,
            current_regime="BULL",
        )

        self.assertGreaterEqual(len(signals), 1)
        sig = signals[0]
        self.assertEqual(sig.signal_type, SignalType.BUY)
        self.assertIn("bandit_weights", sig.lineage)
        self.assertIn("ensemble_weights", sig.lineage)
        self.assertIn("scaled_mr_score", sig.lineage)
        self.assertIn("scaled_trend_score", sig.lineage)

    def test_03_continuous_vix_regime_cascade(self):
        """Verify SignalOrchestrator evaluates continuous VIX regime and sets dynamic floor."""
        orchestrator = SignalOrchestrator()
        self.assertIsNotNone(orchestrator.vol_sentinel)

        pf = PortfolioState(
            cash_available=10000.0,
            total_equity=10000.0,
            positions=[],
            last_updated=datetime.now(timezone.utc),
        )

        sig = Signal(
            ticker="MC.PA",
            signal_type=SignalType.BUY,
            score=72.0,  # Below 75 floor
            status=SignalStatus.PENDING,
        )

        # In elevated volatility (VIX=28.0), conviction floor is raised (+5 pts -> 75 -> 80)
        res = orchestrator.process_raw_signals(
            raw_signals=[sig],
            portfolio=pf,
            current_prices={"MC.PA": 600.0},
            vix_level=28.0,
        )

        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].status, SignalStatus.REJECTED)
        self.assertIn("conviction floor", res[0].reason.lower())

    def test_04_fastapi_decoupled_endpoints(self):
        """Verify newly decoupled FastAPI endpoints (/equity_curve, /analytics/funnel, /ledger/closed, /signals)."""
        client = TestClient(app)

        # 1. Equity Curve
        resp_eq = client.get("/api/v1/portfolio/equity_curve")
        self.assertEqual(resp_eq.status_code, 200)
        self.assertIsInstance(resp_eq.json(), list)

        # 2. Funnel Analytics
        resp_funnel = client.get("/api/v1/analytics/funnel?days=7")
        self.assertEqual(resp_funnel.status_code, 200)
        data_funnel = resp_funnel.json()
        self.assertIn("drops", data_funnel)
        self.assertIn("survival_rate", data_funnel)

        # 3. Closed Ledger
        resp_ledger = client.get("/api/v1/ledger/closed?limit=10")
        self.assertEqual(resp_ledger.status_code, 200)
        self.assertIsInstance(resp_ledger.json(), list)

        # 4. Signals by Status
        resp_sig = client.get("/api/v1/signals?status=PENDING&limit=10")
        self.assertEqual(resp_sig.status_code, 200)
        self.assertIsInstance(resp_sig.json(), list)


if __name__ == "__main__":
    unittest.main()
