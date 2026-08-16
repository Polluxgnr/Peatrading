"""Unit Tests for Intraday Market Watchdog and Institutional LLM Analyst Agent."""

from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
for sub in ("00_data_sensors", "01_memory_core", "02_quant_engine", "03_risk_portfolio", "04_orchestrator_ai", "05_interfaces"):
    sys.path.insert(0, str(ROOT / sub))

from analyst_agent import InstitutionalAnalyst
from data_models import PortfolioState
from watchdog import MarketWatchdog


class TestWatchdogAndAnalystSuite(unittest.TestCase):

    def test_01_watchdog_normal_action(self):
        """Verify MarketWatchdog reports normal conditions when intraday drop is small."""
        dog = MarketWatchdog(default_threshold=-0.10)
        res = dog.check_intraday_crash(
            index_ticker="^FCHI",
            mock_data={"high": 7500.0, "current": 7425.0},  # -1.0% drop
        )

        self.assertFalse(res["alert"])
        self.assertAlmostEqual(res["drop_pct"], -0.01, places=3)
        self.assertEqual(res["ticker"], "^FCHI")
        self.assertIn("Normal", res["message"])

    def test_02_watchdog_flash_crash_alert(self):
        """Verify MarketWatchdog triggers critical alert when intraday drop exceeds threshold."""
        dog = MarketWatchdog(default_threshold=-0.10)
        # High: 8000.0, Current: 7000.0 -> -12.5% flash crash
        res = dog.check_intraday_crash(
            index_ticker="^FCHI",
            mock_data={"high": 8000.0, "current": 7000.0},
        )

        self.assertTrue(res["alert"])
        self.assertAlmostEqual(res["drop_pct"], -0.125, places=3)
        self.assertIn("CRITICAL: Intraday Flash Crash Detected", res["message"])

    def test_03_institutional_analyst_fallback_brief(self):
        """Verify InstitutionalAnalyst produces 3-paragraph executive synthesis."""
        analyst = InstitutionalAnalyst()
        # Force deterministic fallback by ensuring empty api_key
        analyst.api_key = None

        portfolio_state = PortfolioState(
            cash_available=3000.0,
            total_equity=12000.0,
            positions=[],
            last_updated=datetime.now(timezone.utc),
        )

        thermometer_state = {
            "attack_pct": 0.80,
            "defense_pct": 0.20,
            "mode": "ATTACK",
            "vix": 14.5,
            "vol_21d": 0.12,
        }

        top_signals = [
            {
                "ticker": "MC.PA",
                "score": 88.0,
                "reason": "RSI oversold rebound",
                "ml_probability": 0.82,
            },
            {
                "ticker": "AI.PA",
                "score": 85.0,
                "reason": "Trend continuation",
                "ml_probability": 0.76,
            },
        ]

        brief = analyst.generate_daily_brief_sync(
            portfolio_state=portfolio_state,
            thermometer_state=thermometer_state,
            top_signals=top_signals,
        )

        self.assertIsInstance(brief, str)
        self.assertIn("1. Conjoncture Macroéconomique", brief)
        self.assertIn("2. Analyse des Opportunités Quantitatives", brief)
        self.assertIn("3. Directives Stratégiques", brief)
        self.assertIn("MC.PA", brief)
        self.assertIn("AI.PA", brief)
        self.assertIn("Mode ATTACK", brief)

    def test_04_institutional_analyst_async_execution(self):
        """Verify InstitutionalAnalyst async method returns report properly."""
        analyst = InstitutionalAnalyst()
        analyst.api_key = None

        portfolio_state = PortfolioState(
            cash_available=2000.0,
            total_equity=10000.0,
            positions=[],
            last_updated=datetime.now(timezone.utc),
        )

        thermometer_state = {
            "attack_pct": 0.0,
            "defense_pct": 1.0,
            "mode": "BUNKER",
            "vix": 32.0,
            "vol_21d": 0.35,
        }

        async def run_test():
            gen = analyst.generate_daily_brief(
                portfolio_state=portfolio_state,
                thermometer_state=thermometer_state,
                top_signals=[],
                watchdog_alert={"alert": True, "drop_pct": -0.11},
            )
            chunks = []
            async for c in gen:
                chunks.append(c)
            return "".join(chunks)

        loop = asyncio.new_event_loop()
        res = loop.run_until_complete(run_test())
        loop.close()


        self.assertIsInstance(res, str)
        self.assertIn("BUNKER", res)


if __name__ == "__main__":
    unittest.main()
