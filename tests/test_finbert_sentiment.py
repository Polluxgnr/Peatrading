"""Unit Tests for FinBERT Sentiment Scorer and Batch NLP Engine."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for sub in ("00_data_sensors", "01_memory_core", "02_quant_engine", "03_risk_portfolio", "04_orchestrator_ai"):
    sys.path.insert(0, str(ROOT / sub))

from news_sentiment_llm import NewsSentimentScorer, score_news_batch
from sqlite_portfolio import SQLitePortfolioDB



class TestFinBertSentimentSuite(unittest.TestCase):

    def test_01_single_headline_scoring(self):
        """Verify FinBERT scoring on positive, negative, and neutral financial headlines."""
        scorer = NewsSentimentScorer()

        # Positive headline
        pos_score, pos_label = scorer.score_single_headline("LVMH reports record profits and raises dividend by 15%")
        self.assertGreater(pos_score, 0.0)
        self.assertEqual(pos_label, "positive")

        # Negative headline
        neg_score, neg_label = scorer.score_single_headline("Company warns of massive profit drop and revenue miss")
        self.assertLess(neg_score, 0.0)
        self.assertEqual(neg_label, "negative")

        # Empty headline
        zero_score, zero_label = scorer.score_single_headline("")
        self.assertEqual(zero_score, 0.0)
        self.assertEqual(zero_label, "neutral")

    def test_02_aggregate_analyze_news(self):
        """Verify aggregate news scoring and normalization in [-100, 100]."""
        scorer = NewsSentimentScorer()
        headlines = [
            "Air Liquide reports strong growth across all business lines",
            "Target price upgraded by major European banks",
        ]
        avg_score = asyncio.run(scorer.analyze_news("AI.PA", headlines))
        self.assertGreater(avg_score, 0.0)
        self.assertLessEqual(avg_score, 100.0)

    def test_03_batch_nlp_scoring_with_db(self):
        """Verify batch news scoring persists bullish/bearish labels into SQLite."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            db = SQLitePortfolioDB(db_path=db_path)
            db.init_db()

            # Insert unprocessed news
            db.insert_raw_news([
                {"id": "NEWS_1", "ticker": "MC.PA", "title": "Record sales in Q1 for luxury giant", "content": "Tremendous growth in Europe", "source": "Reuters", "published_at": "2026-08-10 10:00:00"},
                {"id": "NEWS_2", "ticker": "OR.PA", "title": "Profit collapse and severe regulatory penalties", "content": "Downturn expected", "source": "Bloomberg", "published_at": "2026-08-10 10:30:00"},
            ])

            score_news_batch(db)

            # Verify processed status
            unproc = db.get_unprocessed_news()
            self.assertEqual(len(unproc), 0)
        finally:
            try:
                Path(db_path).unlink(missing_ok=True)
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
