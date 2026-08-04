"""News sentiment scorer for PEA Pollux (Phase 11).

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

try:
    _CORE = Path(__file__).resolve().parent.parent / "01_memory_core"
    sys.path.insert(0, str(_CORE))
    from env_loader import load_api_keys

    load_api_keys(Path(__file__).resolve().parent.parent / "config" / "api_keys.env")
except Exception:  # noqa: BLE001
    _env = Path(__file__).resolve().parent.parent / "config" / "api_keys.env"
    if _env.exists():
        with open(_env, "r", encoding="utf-8") as fh:
            for line in fh:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip(" '\""))

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

        joined = "\n".join(f"- {h}" for h in headlines[:10])
        system_prompt = (
            "You are a quantitative NLP model. Output NOTHING EXCEPT a single "
            "integer between -100 and 100. Do not wrap the integer in markdown "
            "or backticks."
        )
        user_prompt = (
            f"Ticker: {ticker}\nHeadlines:\n{joined}\n\n"
            "Return ONLY one integer between -100 and 100."
        )

        try:
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
        except Exception as exc:
            logger.exception("Failed to compute news sentiment for %s.", ticker)
            try:
                import sys
                from pathlib import Path
                _ROOT = Path(__file__).resolve().parent.parent
                if str(_ROOT / "01_memory_core") not in sys.path:
                    sys.path.insert(0, str(_ROOT / "01_memory_core"))
                from logging_setup import update_pipeline_status
                update_pipeline_status({"data_degraded_mode": True, "degraded_reason": f"news_sentiment_llm.py: {exc}"})
            except Exception:
                pass
            return _NEUTRAL_SCORE

    async def analyze_earnings_call_qa(self, ticker: str) -> float:
        """Fetch the latest earnings call transcript and score the Q&A section.
        
        Extracts the Q&A portion (or the latter half if not explicitly marked)
        and scores management confidence on a [-100, 100] scale.
        """
        import requests
        
        fmp_key = (os.getenv("FMP_API_KEY") or "").strip()
        if not fmp_key or not self.api_key:
            return _NEUTRAL_SCORE
            
        symbol = ticker.replace(".PA", "").replace(".AS", "").upper()
        try:
            url = f"https://financialmodelingprep.com/api/v3/earning_call_transcript/{symbol}?limit=1&apikey={fmp_key}"
            # Using synchronous requests here since it's a lightweight fetch, but we could use aiohttp
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                return _NEUTRAL_SCORE
            data = resp.json()
            if not isinstance(data, list) or not data:
                return _NEUTRAL_SCORE
                
            content = data[0].get("content") or ""
            if not content:
                return _NEUTRAL_SCORE
                
            # Try to find the Q&A section, or take the last 30% of the transcript
            qa_text = ""
            qa_idx = content.lower().find("question-and-answer")
            if qa_idx == -1:
                qa_idx = content.lower().find("questions and answers")
                
            if qa_idx != -1:
                qa_text = content[qa_idx:]
            else:
                # Fallback: take the last 4000 chars
                qa_text = content[-4000:] if len(content) > 4000 else content
                
            # Truncate to avoid blowing up the context window
            qa_text = qa_text[:6000]
            
            system_prompt = (
                "You are a quantitative NLP model evaluating management confidence "
                "from Earnings Call Q&A sessions. Output NOTHING EXCEPT a single "
                "integer between -100 and 100. Do not wrap the integer in markdown "
                "or backticks."
            )
            user_prompt = (
                f"Ticker: {ticker}\nQ&A Transcript Snippet:\n{qa_text}\n\n"
                "Return ONLY one integer between -100 (evasive, negative, weak guidance) "
                "and 100 (highly confident, raises guidance, strong answers)."
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
            logger.info("Earnings Q&A sentiment for %s: %.0f", ticker, score)
            return score
        except Exception as exc:
            logger.debug("Failed to compute earnings Q&A sentiment for %s: %s", ticker, exc)
            return _NEUTRAL_SCORE


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
