"""Unit Tests for LLM 24h Persistent SQLite Cache and Zero-Cost Guardrails."""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
for sub in ("01_memory_core", "04_orchestrator_ai", "05_interfaces"):
    sys.path.insert(0, str(ROOT / sub))

from llm_explainer import openrouter_chat
from sqlite_portfolio import PortfolioDB


class TestLlmCacheAndGuardrailsSuite(unittest.TestCase):

    def setUp(self):
        self.temp_db_path = ROOT / "database" / "test_llm_cache.db"
        if self.temp_db_path.exists():
            self.temp_db_path.unlink()
        self.db = PortfolioDB(db_path=self.temp_db_path)
        self.db.init_db()

    def tearDown(self):
        if self.temp_db_path.exists():
            self.temp_db_path.unlink()

    def test_01_save_and_retrieve_synthesis_fresh(self):
        """Verify synthesis saved to SQLite is retrieved when age < 24h."""
        self.db.save_synthesis("MC.PA", "### Note LVMH\n- Signal haussier RSI.")
        cached = self.db.get_cached_synthesis("MC.PA", max_age_hours=24)
        self.assertIsNotNone(cached)
        self.assertIn("Note LVMH", cached)

    def test_02_synthesis_cache_expiration(self):
        """Verify cached synthesis expires and returns None when age > 24h."""
        # Insert expired row (25 hours ago)
        old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        with self.db._connect() as conn:
            conn.execute(
                """
                INSERT INTO llm_synthesis_cache (ticker, synthesis, generated_at)
                VALUES (?, ?, ?);
                """,
                ("AIR.PA", "Old expired analysis", old_time),
            )

        cached = self.db.get_cached_synthesis("AIR.PA", max_age_hours=24)
        self.assertIsNone(cached)

    def test_03_openrouter_payload_guardrails(self):
        """Verify openrouter_chat caps max_tokens at 350 and injects system constraints."""
        captured_payload = {}

        class DummyResponse:
            status = 200

            async def text(self):
                return ""

            async def json(self):
                return {"choices": [{"message": {"content": "1. Macro OK\n2. Technique OK\n3. Risque modere"}}]}

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        class DummySession:
            def __init__(self, *args, **kwargs):
                pass

            def post(self, url, json=None, headers=None):
                nonlocal captured_payload
                captured_payload = json
                return DummyResponse()

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        with patch("aiohttp.ClientSession", side_effect=DummySession):
            res = asyncio.run(
                openrouter_chat(
                    messages=[{"role": "user", "content": "Analyse MC.PA"}],
                    api_key="fake_key",
                    max_tokens=900,  # Exceeds cap
                    temperature=0.8,  # Exceeds cap
                )
            )

            self.assertIsNotNone(res)
            self.assertEqual(captured_payload["max_tokens"], 350)
            self.assertEqual(captured_payload["temperature"], 0.5)
            self.assertTrue(any(m["role"] == "system" for m in captured_payload["messages"]))


if __name__ == "__main__":
    unittest.main()
