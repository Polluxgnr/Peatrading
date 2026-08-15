"""Unit Tests for FMP Piotroski Fundamentals, Discord Copilot Alert Enrichment, and Autonomous ML Retraining."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
for sub in ("00_data_sensors", "00_data_sensors/scrapers", "01_memory_core", "02_quant_engine", "03_risk_portfolio", "04_orchestrator_ai", "05_interfaces", "06_api"):
    sys.path.insert(0, str(ROOT / sub))

from fundamentals_api import FundamentalsSensor
from discord_copilot import DiscordCopilot
from data_models import Signal, SignalStatus, SignalType
from main_scheduler import run_monthly_ml_retraining


class TestFmpCopilotRetrainingSuite(unittest.TestCase):

    def test_01_fmp_piotroski_score_calculation(self):
        """Verify _calculate_piotroski_fmp accurately scores statements from FMP JSON."""
        sensor = FundamentalsSensor()

        mock_income = [
            {"netIncome": 1500000000, "grossProfit": 4000000000, "revenue": 10000000000, "weightedAverageShsOut": 500000000},
            {"netIncome": 1200000000, "grossProfit": 3000000000, "revenue": 9000000000, "weightedAverageShsOut": 500000000},
        ]
        mock_balance = [
            {"totalAssets": 20000000000, "longTermDebt": 3000000000, "totalCurrentAssets": 8000000000, "totalCurrentLiabilities": 4000000000},
            {"totalAssets": 18000000000, "longTermDebt": 3500000000, "totalCurrentAssets": 7000000000, "totalCurrentLiabilities": 4000000000},
        ]
        mock_cashflow = [
            {"operatingCashFlow": 2200000000},
            {"operatingCashFlow": 1800000000},
        ]

        mock_resp_inc = MagicMock(status_code=200, json=lambda: mock_income)
        mock_resp_bs = MagicMock(status_code=200, json=lambda: mock_balance)
        mock_resp_cf = MagicMock(status_code=200, json=lambda: mock_cashflow)

        def mock_get(url, *args, **kwargs):
            if "income-statement" in url:
                return mock_resp_inc
            elif "balance-sheet-statement" in url:
                return mock_resp_bs
            elif "cash-flow-statement" in url:
                return mock_resp_cf
            return MagicMock(status_code=404)

        with patch("requests.get", side_effect=mock_get):
            res = sensor._calculate_piotroski_fmp("MC.PA", "test_key")
            self.assertIsNotNone(res)
            score, breakdown = res
            self.assertGreaterEqual(score, 7)
            self.assertEqual(breakdown["roa_pos"], 1)
            self.assertEqual(breakdown["cfo_pos"], 1)
            self.assertEqual(breakdown["accrual"], 1)
            self.assertEqual(breakdown["leverage_chg"], 1)

    def test_02_discord_copilot_build_embed_enrichment(self):
        """Verify DiscordCopilot embeds include FinBERT, Red Team, ML, and StatArb metadata."""
        copilot = DiscordCopilot()

        sig = Signal(
            ticker="MC.PA",
            signal_type=SignalType.BUY,
            score=88.5,
            target_qty=5,
            status=SignalStatus.PENDING,
            strategy="STAT_ARB_COINTEGRATION",
            lineage={
                "pair_ticker": "OR.PA",
                "z_score": -2.43,
                "coint_pvalue": 0.0001,
                "finbert_sentiment": 45.2,
                "sentiment_label": "Bullish",
                "ml_probability": 0.685,
                "conformal_interval": [65.0, 72.0],
                "red_team_verdict": "Consensus Favorable (Score: 82/100). Croissance confirmée.",
            }
        )

        embed = copilot.build_embed(sig, "Excellente opportunité de mean-reversion.")
        field_names = [f.name for f in embed.fields]

        self.assertIn("Quantité", field_names)
        self.assertIn("Score Technique", field_names)
        self.assertTrue(any("Arbitrage Statistique" in name for name in field_names))
        self.assertTrue(any("Sentiment FinBERT" in name for name in field_names))
        self.assertTrue(any("Probabilité ML" in name for name in field_names))
        self.assertTrue(any("Comité Red Team" in name for name in field_names))

    def test_03_monthly_ml_retraining_execution(self):
        """Verify run_monthly_ml_retraining executes without unhandled errors."""
        mock_metrics = {
            "tactical_BULL": {"accuracy_pct": 74.5},
            "tactical_BEAR": {"accuracy_pct": 68.2},
        }
        with patch("ml_trainer.train_model", return_value=mock_metrics), \
             patch("main_scheduler._post_webhook") as mock_webhook:
            # Force day = 1 for the test
            with patch("main_scheduler.datetime") as mock_dt:
                mock_dt.today.return_value = datetime(2026, 9, 1, 2, 0, 0, tzinfo=timezone.utc)
                mock_dt.now.return_value = datetime(2026, 9, 1, 2, 0, 0, tzinfo=timezone.utc)
                run_monthly_ml_retraining()


if __name__ == "__main__":
    unittest.main()
