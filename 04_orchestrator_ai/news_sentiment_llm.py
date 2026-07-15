"""News sentiment scorer for PEA Sniper Terminal V-Prime (Phase 11).

Turns unstructured news headlines into a single hard number the deterministic
engine can use. The LLM is constrained to act as a quantitative NLP model and
MUST return only an integer in ``[-100, +100]`` — no prose, no explanation.

This keeps the pipeline emotionless: the model never decides trades, it only
compresses text into a scalar sentiment feature.
"""

import logging
import os
import re
import sys
from pathlib import Path
from typing import List

try:  # Load config/api_keys.env if python-dotenv is available.
    from dotenv import load_dotenv

    _ENV_PATH = Path(__file__).resolve().parent.parent / "config" / "api_keys.env"
    load_dotenv(_ENV_PATH)
except Exception:  # noqa: BLE001 - dotenv is a convenience, not a requirement.
    pass

# Reuse the shared OpenRouter client from the interfaces layer.
_INTERFACES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "05_interfaces"
)
sys.path.insert(0, _INTERFACES_DIR)

from llm_explainer import openrouter_chat  # noqa: E402

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "mistralai/mistral-7b-instruct"
_NEUTRAL_SCORE = 0.0
# Extract the first signed integer from the model reply.
_INT_RE = re.compile(r"-?\d+")


class NewsSentimentScorer:
    """Compresses news headlines into a numeric sentiment score."""

    def __init__(self) -> None:
        """Read the OpenRouter API key and model slug from the environment."""
        self.api_key: str | None = os.getenv("OPENROUTER_API_KEY")
        self.model: str = os.getenv("OPENROUTER_MODEL", _DEFAULT_MODEL)
        if not self.api_key:
            logger.warning(
                "OPENROUTER_API_KEY not set; news sentiment will be neutral (0)."
            )

    @staticmethod
    def _parse_score(raw: str | None) -> float:
        """Parse the LLM reply into a float clamped to [-100, 100]."""
        if not raw:
            return _NEUTRAL_SCORE
        match = _INT_RE.search(raw)
        if not match:
            logger.warning("No integer in sentiment reply %r; neutral.", raw[:80])
            return _NEUTRAL_SCORE
        value = float(int(match.group()))
        return max(-100.0, min(100.0, value))

    async def analyze_news(
        self, ticker: str, news_headlines: List[str]
    ) -> float:
        """Score the aggregate sentiment of headlines for one ticker.

        Args:
            ticker: The ticker the headlines relate to (for prompt context).
            news_headlines: Recent headline strings.

        Returns:
            float: Sentiment in ``[-100.0, +100.0]`` (negative = bearish,
            positive = bullish). Returns ``0.0`` (neutral) if there is no data
            or the API is unavailable.
        """
        headlines = [h.strip() for h in (news_headlines or []) if h and h.strip()]
        if not headlines:
            logger.debug("No headlines for %s; neutral sentiment.", ticker)
            return _NEUTRAL_SCORE
        if not self.api_key:
            return _NEUTRAL_SCORE

        joined = "\n".join(f"- {h}" for h in headlines[:15])
        system_prompt = (
            "You are a deterministic quantitative NLP sentiment model. You read "
            "financial news headlines and output market sentiment as a single "
            "integer between -100 (extremely bearish) and +100 (extremely "
            "bullish), where 0 is neutral. Output ONLY the integer. No words, no "
            "symbols, no explanation, no punctuation."
        )
        user_prompt = (
            f"Ticker: {ticker}\nHeadlines:\n{joined}\n\n"
            "Return ONLY one integer between -100 and 100."
        )

        raw = await openrouter_chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            api_key=self.api_key,
            model=self.model,
            max_tokens=8,
            temperature=0.0,
        )
        score = self._parse_score(raw)
        logger.info("News sentiment for %s: %.0f (from %d headlines).",
                    ticker, score, len(headlines))
        return score


if __name__ == "__main__":
    import asyncio

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    scorer = NewsSentimentScorer()

    # Offline unit check of the parser (no network needed).
    assert scorer._parse_score("42") == 42.0
    assert scorer._parse_score("Score: -73 (bearish)") == -73.0
    assert scorer._parse_score("999") == 100.0
    assert scorer._parse_score("nonsense") == 0.0
    print("Parser checks passed.")

    demo = [
        "Company X beats earnings, raises full-year guidance",
        "Analysts upgrade Company X to Buy on strong order book",
    ]
    result = asyncio.run(scorer.analyze_news("TEST.PA", demo))
    print("Live sentiment (0 if no API key):", result)
