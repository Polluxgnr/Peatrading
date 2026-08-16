"""Unit Tests for Local Sovereign AI (Ollama) Streaming and Zero-Cost Inference."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
for sub in ("00_data_sensors", "01_memory_core", "02_quant_engine", "03_risk_portfolio", "04_orchestrator_ai", "05_interfaces"):
    sys.path.insert(0, str(ROOT / sub))

from analyst_agent import InstitutionalAnalyst
from llm_explainer import ollama_chat_stream, ollama_chat_stream_sync


class TestLocalOllamaStreamingSuite(unittest.TestCase):

    def test_01_ollama_chat_stream_offline(self):
        """Verify ollama_chat_stream yields clean offline warning when Ollama is unreachable."""
        async def _run():
            chunks = []
            async for chunk in ollama_chat_stream([{"role": "user", "content": "test"}]):
                chunks.append(chunk)
            return "".join(chunks)

        result = asyncio.run(_run())
        self.assertIn("Erreur", result)

    def test_02_ollama_chat_stream_sync_offline(self):
        """Verify ollama_chat_stream_sync yields clean offline warning when Ollama is unreachable."""
        chunks = list(ollama_chat_stream_sync([{"role": "user", "content": "test"}]))
        result = "".join(chunks)
        self.assertIn("Erreur", result)

    def test_03_ollama_streaming_mocked(self):
        """Verify ollama_chat_stream_sync yields tokens sequentially when Ollama responds."""
        mock_lines = [
            b'{"message": {"role": "assistant", "content": "Analyse "}, "done": false}',
            b'{"message": {"role": "assistant", "content": "technique "}, "done": false}',
            b'{"message": {"role": "assistant", "content": "positive."}, "done": true}',
        ]
        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.iter_lines.return_value = mock_lines
            mock_resp.__enter__.return_value = mock_resp
            mock_resp.__exit__.return_value = None
            mock_post.return_value = mock_resp

            tokens = list(ollama_chat_stream_sync([{"role": "user", "content": "hello"}]))
            self.assertEqual(tokens, ["Analyse ", "technique ", "positive."])

    def test_04_institutional_analyst_fallback_on_offline(self):
        """Verify InstitutionalAnalyst produces 3 structured paragraphs even if Ollama is offline."""
        analyst = InstitutionalAnalyst()
        t_state = {
            "mode": "ATTACK",
            "attack_pct": 0.75,
            "defense_pct": 0.25,
            "vix": 14.5,
            "vol_21d": 0.12,
        }
        cand_sig = [
            {"ticker": "MC.PA", "score": 92.0, "reason": "RSI 32.0, Rebond SMA200"},
            {"ticker": "OR.PA", "score": 88.0, "reason": "Decote PER"},
        ]

        brief = analyst.generate_daily_brief_sync(
            portfolio_state=None,
            thermometer_state=t_state,
            top_signals=cand_sig,
        )

        self.assertIn("1. Conjoncture Macroéconomique & Thermomètre de Volatilité", brief)
        self.assertIn("2. Analyse des Opportunités Quantitatives", brief)
        self.assertIn("3. Directives Stratégiques d'Aide à la Décision", brief)
        self.assertIn("MC.PA", brief)
        self.assertIn("Mode ATTACK", brief)


if __name__ == "__main__":
    unittest.main()
