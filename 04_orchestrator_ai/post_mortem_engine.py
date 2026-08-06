"""AI-based Post-Mortem Engine for Closed Trades (Phase 60).

Evaluates closed trades by sending entry/exit data, hold time, and PnL to the LLM.
Records the generated lessons into the database to improve future decision-making.
"""

import logging
import os
import json
from pathlib import Path

logger = logging.getLogger(__name__)

class PostMortemEngine:
    def __init__(self):
        try:
            import google.generativeai as genai
            api_key = os.getenv("GEMINI_API_KEY")
            if api_key:
                genai.configure(api_key=api_key)
                self.model = genai.GenerativeModel("gemini-1.5-flash")
            else:
                self.model = None
                logger.warning("GEMINI_API_KEY not set. Post-Mortem Engine disabled.")
        except ImportError:
            self.model = None
            logger.warning("google.generativeai not installed. Post-Mortem Engine disabled.")

    def run_post_mortems(self):
        """Find recently closed trades and generate post-mortems for them."""
        if self.model is None:
            return

        try:
            import sqlite3
            _ROOT = Path(__file__).resolve().parent.parent
            db_path = _ROOT / "database" / "portfolio.db"
            if not db_path.exists():
                return
                
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            
            # Make sure post_mortem column exists
            try:
                conn.execute("ALTER TABLE audit_logs ADD COLUMN post_mortem TEXT")
            except sqlite3.OperationalError:
                pass
                
            closed_trades = conn.execute("SELECT id, ticker, action, quantity, price, created_at, reason FROM audit_logs WHERE status='CLOSED' AND post_mortem IS NULL").fetchall()
            
            for trade in closed_trades:
                trade_id = trade["id"]
                ticker = trade["ticker"]
                
                prompt = (
                    f"Analyze this closed trade for {ticker}:\n"
                    f"Action: {trade['action']}, Price: {trade['price']}\n"
                    f"Original thesis: {trade['reason']}\n\n"
                    "Provide a brief, 3-sentence post-mortem: Was the thesis correct? Was the exit premature or late? What is the core lesson learned?"
                )
                
                try:
                    response = self.model.generate_content(prompt)
                    lesson = response.text.strip()
                    logger.info("Post-Mortem for %s generated: %s", ticker, lesson)
                    
                    conn.execute("UPDATE audit_logs SET post_mortem = ? WHERE id = ?", (lesson, trade_id))
                    conn.commit()
                except Exception as exc:
                    logger.debug("LLM call failed for post-mortem %s: %s", trade_id, exc)
                    
            conn.close()
        except Exception as exc:
            logger.error("Post-mortem engine failed: %s", exc)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    engine = PostMortemEngine()
    engine.run_post_mortems()
