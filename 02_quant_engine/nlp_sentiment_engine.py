"""Batch NLP News Sentiment Engine using ProsusAI/finbert transformer model.

Scores unprocessed news items in SQLite database and persists sentiment labels
('Bullish', 'Bearish', 'Neutral') and numeric compound scores in [-1.0, 1.0].
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

_ROOT = Path(__file__).resolve().parent.parent
for _d in ("00_data_sensors", "01_memory_core", "02_quant_engine", "03_risk_portfolio", "04_orchestrator_ai"):
    sys.path.insert(0, str(_ROOT / _d))

from sqlite_portfolio import SQLitePortfolioDB
from news_sentiment_llm import get_finbert_pipeline

logger = logging.getLogger("nlp_sentiment_engine")


def score_news_batch(db: SQLitePortfolioDB) -> None:
    """Fetch unprocessed news, score them using ProsusAI/finbert, and update the database."""
    unprocessed = db.get_unprocessed_news()
    if not unprocessed:
        logger.info("No unprocessed news found.")
        return

    logger.info("Scoring %d unprocessed news items with FinBERT...", len(unprocessed))

    nlp = get_finbert_pipeline()
    updates: List[Dict[str, Any]] = []

    for item in unprocessed:
        # Sanitize and truncate content to avoid token limit and boilerplate
        raw_combined = f"{item['title']} {item.get('content') or ''}"
        try:
            from text_cleaner import clean_financial_text
            text = clean_financial_text(raw_combined, max_chars=1500)
        except Exception:
            text = raw_combined[:1500].strip()

        if not text:
            continue

        label = "Neutral"
        compound = 0.0

        if nlp is not None:
            try:
                outputs = nlp(text)
                if outputs and isinstance(outputs[0], list):
                    sorted_preds = sorted(outputs[0], key=lambda x: x.get("score", 0.0), reverse=True)
                    top = sorted_preds[0]
                elif outputs and isinstance(outputs[0], dict):
                    top = outputs[0]
                else:
                    top = {"label": "neutral", "score": 1.0}

                pred_label = str(top.get("label", "neutral")).lower()
                prob = float(top.get("score", 0.0))

                if pred_label == "positive" and prob > 0.50:
                    label = "Bullish"
                    compound = prob
                elif pred_label == "negative" and prob > 0.50:
                    label = "Bearish"
                    compound = -prob
                else:
                    label = "Neutral"
                    compound = 0.0
            except Exception as exc:
                logger.debug("FinBERT inference error on news item %s: %s", item.get("id"), exc)
        else:
            # Fallback keyword scoring
            t_lower = text.lower()
            if any(w in t_lower for w in ("record", "croissance", "hausse", "bénéfice", "upgrade", "beat")):
                label = "Bullish"
                compound = 0.70
            elif any(w in t_lower for w in ("chute", "baisse", "perte", "déficit", "downgrade", "miss")):
                label = "Bearish"
                compound = -0.70

        updates.append({
            "id": item["id"],
            "sentiment_score": round(compound, 4),
            "sentiment_label": label,
        })

    if updates:
        db.update_news_sentiment(updates)
        logger.info("FinBERT sentiment scoring completed for %d items.", len(updates))


if __name__ == "__main__":
    db = SQLitePortfolioDB()
    score_news_batch(db)

