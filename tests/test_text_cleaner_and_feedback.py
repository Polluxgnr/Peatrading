"""Unit Tests for Text Sanitizer and Autonomous Reinforcement Post-Mortem Loop."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for sub in ("00_data_sensors", "01_memory_core", "02_quant_engine", "03_risk_portfolio", "04_orchestrator_ai"):
    sys.path.insert(0, str(ROOT / sub))

from text_cleaner import clean_financial_text
from post_mortem_engine import TradePostMortemEngine
from contextual_bandit import UCBBandit


class TestTextCleanerAndFeedbackSuite(unittest.TestCase):

    def test_01_text_sanitizer_html_and_urls(self):
        """Verify clean_financial_text strips HTML, URLs, and boilerplate."""
        raw_html = (
            "<html><body>"
            "<h3>TotalEnergies annonce un dividende exceptionnel</h3>"
            "<p>Le groupe pétrolier enregistre une progression solide de 8%.</p>"
            "<a href='https://finance.yahoo.com/news'>Lire la suite</a>"
            "<footer>Disclaimer: Ceci n'est pas un conseil. Unsubscribe here. All rights reserved.</footer>"
            "</body></html>"
        )
        cleaned = clean_financial_text(raw_html)

        self.assertIn("TotalEnergies", cleaned)
        self.assertIn("dividende exceptionnel", cleaned)
        self.assertNotIn("<p>", cleaned)
        self.assertNotIn("https://", cleaned)
        self.assertNotIn("Unsubscribe", cleaned)
        self.assertNotIn("Disclaimer", cleaned)

    def test_02_post_mortem_bandit_reinforcement_update(self):
        """Verify TradePostMortemEngine triggers bandit reward update."""
        import tempfile
        import gc
        
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as b_tf:
            bandit_path = Path(b_tf.name)

        try:
            bandit = UCBBandit(storage_path=bandit_path)
            prev_counts = bandit.state["BULL"]["mean_reversion"]["counts"]

            engine = TradePostMortemEngine(db_path=db_path)
            res = engine.generate_post_mortem(
                trade_id="TR_BANDIT_01",
                ticker="MC.PA",
                entry_date="2026-06-01",
                exit_date="2026-06-15",
                entry_price=100.0,
                exit_price=120.0,
                shares=10,
                exit_reason="PROFIT_SHAVE_20PCT",
            )
            self.assertEqual(res["pnl_eur"], 200.0)
            self.assertEqual(res["pnl_pct"], 20.0)
        finally:
            gc.collect()
            try:
                Path(db_path).unlink(missing_ok=True)
            except Exception:
                pass
            try:
                bandit_path.unlink(missing_ok=True)
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
