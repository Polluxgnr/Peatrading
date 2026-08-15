"""Financial News Sentiment Scorer for PEA Pollux using ProsusAI/finbert.

Provides 100% deterministic, offline, institutional-grade sentiment classification
mapping strictly to the [-100, +100] quantitative conviction scale.

Model: ProsusAI/finbert (BERT-based financial domain language model)
Output mapping:
  - positive (prob p): +p * 100.0
  - negative (prob p): -p * 100.0
  - neutral: 0.0
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent.parent
for _d in ("00_data_sensors", "01_memory_core", "02_quant_engine", "03_risk_portfolio"):
    sys.path.insert(0, str(_ROOT / _d))

logger = logging.getLogger(__name__)

_FINBERT_PIPELINE = None


def get_finbert_pipeline():
    """Lazily load and cache the ProsusAI/finbert sentiment analysis pipeline."""
    global _FINBERT_PIPELINE
    if _FINBERT_PIPELINE is None:
        try:
            from transformers import pipeline
            logger.info("Initializing ProsusAI/finbert transformer pipeline...")
            _FINBERT_PIPELINE = pipeline(
                "sentiment-analysis",
                model="ProsusAI/finbert",
                tokenizer="ProsusAI/finbert",
                top_k=None,
            )
            logger.info("ProsusAI/finbert pipeline successfully loaded.")
        except Exception as exc:
            logger.warning("FinBERT pipeline could not be loaded: %s (using heuristic fallback)", exc)
            _FINBERT_PIPELINE = False
    return _FINBERT_PIPELINE if _FINBERT_PIPELINE is not False else None


class NewsSentimentScorer:
    """Quantitative sentiment scorer powered by ProsusAI/finbert transformer."""

    def __init__(self, portfolio_db=None) -> None:
        if portfolio_db is None:
            try:
                from sqlite_portfolio import PortfolioDB
                self.portfolio_db = PortfolioDB()
            except Exception:
                self.portfolio_db = None
        else:
            self.portfolio_db = portfolio_db

    def score_single_headline(self, headline: str) -> Tuple[float, str]:
        """Score a single headline string using FinBERT or deterministic keywords.

        Args:
            headline: Text of the financial headline.

        Returns:
            Tuple[float, str]: (score in [-100, 100], label string).
        """
        try:
            from text_cleaner import clean_financial_text
            cleaned = clean_financial_text(headline, max_chars=1500)
        except Exception:
            cleaned = str(headline)[:1500].strip()

        if not cleaned:
            return 0.0, "neutral"

        pipe = get_finbert_pipeline()
        if pipe is not None:
            try:
                # pipe returns e.g. [[{'label': 'positive', 'score': 0.89}, {'label': 'negative', ...}, ...]]
                outputs = pipe(cleaned)
                if outputs and isinstance(outputs[0], list):
                    # Sort by score descending to get top label
                    sorted_preds = sorted(outputs[0], key=lambda x: x.get("score", 0.0), reverse=True)
                    top_pred = sorted_preds[0]
                elif outputs and isinstance(outputs[0], dict):
                    top_pred = outputs[0]
                else:
                    top_pred = {"label": "neutral", "score": 1.0}

                label = str(top_pred.get("label", "neutral")).lower()
                prob = float(top_pred.get("score", 0.0))

                if label == "positive":
                    return round(prob * 100.0, 1), "positive"
                elif label == "negative":
                    return round(prob * -100.0, 1), "negative"
                else:
                    return 0.0, "neutral"
            except Exception as exc:
                logger.debug("FinBERT inference error on '%s': %s", cleaned[:50], exc)

        # Heuristic financial keywords fallback
        lower = cleaned.lower()
        bull_words = ["hausse", "croissance", "record", "dividende", "bénéfice", "achat", "surperformance", "rehausse", "upgrade", "beat", "surge", "gain", "profit", "bullish"]
        bear_words = ["baisse", "chute", "perte", "déficit", "avertissement", "dégradation", "downgrade", "miss", "plunge", "fraud", "investigation", "litigation", "bearish"]
        
        pos_hits = sum(1 for w in bull_words if w in lower)
        neg_hits = sum(1 for w in bear_words if w in lower)

        if pos_hits > neg_hits:
            return 50.0 + min(40.0, pos_hits * 15.0), "positive"
        elif neg_hits > pos_hits:
            return -50.0 - min(40.0, neg_hits * 15.0), "negative"
        return 0.0, "neutral"

    async def analyze_news(
        self, ticker: str, news_headlines: List[str], source: str = "finbert"
    ) -> float:
        """Score the aggregate sentiment of headlines for one ticker using FinBERT.

        Args:
            ticker: Asset ticker (e.g. 'MC.PA').
            news_headlines: List of headline strings.
            source: Ingestion source tag.

        Returns:
            float: Normalized aggregate sentiment score in [-100.0, +100.0].
        """
        headlines = [h.strip() for h in (news_headlines or []) if h and h.strip()]
        if not headlines:
            logger.debug("No headlines for %s; neutral sentiment (0).", ticker)
            return 0.0

        scores: List[float] = []
        for h in headlines[:15]:
            h_score, _ = self.score_single_headline(h)
            scores.append(h_score)

            if self.portfolio_db is not None:
                try:
                    self.portfolio_db.upsert_sentiment_history(
                        ticker=ticker,
                        score=h_score,
                        source=source,
                        headline=h[:120],
                    )
                except Exception as exc:
                    logger.debug("Failed to upsert sentiment history for %s: %s", ticker, exc)

        final_score = float(sum(scores) / len(scores)) if scores else 0.0
        final_score = max(-100.0, min(100.0, round(final_score, 1)))
        logger.info("FinBERT sentiment for %s: %.1f (from %d headlines).", ticker, final_score, len(headlines))
        return final_score



class OpenRouterClient:
    """Optional client for OpenRouter generative queries (Red Team debate, Friday CIO digest, AI ticker summaries)."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None) -> None:
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        self.model = model or os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
        self.is_configured = bool(self.api_key and self.api_key.strip())

    def query_sync(self, prompt: str, max_tokens: int = 300) -> Optional[str]:
        """Synchronously query the OpenRouter API."""
        if not self.is_configured:
            return None
        try:
            import requests
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/Polluxgnr/Peatrading",
                "X-Title": "PEA Pollux Terminal",
            }
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.2,
            }
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=12,
            )
            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices") or []
                if choices:
                    return choices[0].get("message", {}).get("content", "").strip()
        except Exception as exc:
            logger.debug("OpenRouter query failed: %s", exc)
        return None


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    scorer = NewsSentimentScorer()
    demo_headlines = [
        "LVMH records strong Q1 sales growth beating all analyst expectations",
        "European luxury sector downgraded on slowing Chinese demand",
    ]
    res = asyncio.run(scorer.analyze_news("MC.PA", demo_headlines))
    print("Aggregate FinBERT sentiment score:", res)


