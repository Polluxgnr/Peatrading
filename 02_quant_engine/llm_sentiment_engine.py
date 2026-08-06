import os
import sys
import json
import requests
from pathlib import Path
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "01_memory_core"))

from logging_setup import get_logger
from sqlite_portfolio import SQLitePortfolioDB

logger = get_logger("llm_sentiment_engine")

load_dotenv(_ROOT / ".env")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral")

# Load VADER as a fallback
try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    vader_analyzer = SentimentIntensityAnalyzer()
except ImportError:
    logger.warning("vaderSentiment not installed. Fallback sentiment will be 0.0.")
    vader_analyzer = None


def fallback_vader(text: str) -> tuple[float, str, str]:
    """Fallback sentiment calculation using VADER."""
    if not vader_analyzer:
        return 0.0, "Neutral", "Fallback to neutral due to missing VADER."
    
    scores = vader_analyzer.polarity_scores(text)
    compound = float(scores["compound"])
    
    if compound >= 0.05:
        label = "Bullish"
    elif compound <= -0.05:
        label = "Bearish"
    else:
        label = "Neutral"
        
    return compound, label, "Calculated using VADER heuristic fallback."


def call_ollama(text: str) -> tuple[float, str, str] | None:
    """Send text to Ollama and ask for structured JSON."""
    prompt = f"""You are a professional quantitative analyst. 
Analyze the following financial news article and return a strict JSON object with EXACTLY these three keys:
- "guidance_score": A float between -1.0 (extremely bearish) and 1.0 (extremely bullish).
- "sentiment_label": Must be exactly one of "Bullish", "Bearish", or "Neutral".
- "reasoning": A brief one-sentence financial justification for the score.

News text:
{text}

Return ONLY the JSON object. Do not include markdown formatting or conversational text."""

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json"
    }

    try:
        url = f"{OLLAMA_URL.rstrip('/')}/api/generate"
        response = requests.post(url, json=payload, timeout=20)
        response.raise_for_status()
        
        result = response.json()
        output_text = result.get("response", "").strip()
        
        # Ollama might wrap JSON in markdown block even with format="json" in some models
        if output_text.startswith("```json"):
            output_text = output_text[7:]
        if output_text.endswith("```"):
            output_text = output_text[:-3]
            
        data = json.loads(output_text.strip())
        
        g_score = float(data.get("guidance_score", 0.0))
        label = str(data.get("sentiment_label", "Neutral"))
        reasoning = str(data.get("reasoning", "No reasoning provided."))
        
        # Ensure label validity
        if label not in ("Bullish", "Bearish", "Neutral"):
            label = "Neutral"
            
        # Ensure score bounds
        g_score = max(-1.0, min(1.0, g_score))
        
        return g_score, label, reasoning
        
    except Exception as e:
        logger.warning(f"Ollama inference failed: {e}")
        return None


def score_news_batch(db: SQLitePortfolioDB):
    """Fetch unprocessed news, score them using Ollama (or VADER), and update the DB."""
    unprocessed = db.get_unprocessed_news()
    if not unprocessed:
        logger.info("No unprocessed news found.")
        return
        
    logger.info("Scoring %d unprocessed news items with Ollama (%s)...", len(unprocessed), OLLAMA_MODEL)
    
    updates = []
    
    for item in unprocessed:
        text = f"{item['title']} {item['content'] or ''}"
        # Truncate text if it's too long for typical small LLM context
        text = text[:4000]
        
        res = call_ollama(text)
        if res:
            compound, label, reasoning = res
            logger.debug("Ollama success for news ID %s: %s", item["id"], label)
        else:
            compound, label, reasoning = fallback_vader(text)
            logger.debug("VADER fallback for news ID %s: %s", item["id"], label)
            
        # We also might want to store reasoning, but our news_master schema might not have it yet.
        # We will just log it for now and update sentiment.
        # The prompt requested we use the database, the schema has:
        # id, published_at, ticker, source, url, title, content, sentiment_score, sentiment_label
        
        updates.append({
            "id": item["id"],
            "sentiment_score": compound,
            "sentiment_label": label
        })
        
    if updates:
        db.update_news_sentiment(updates)
        logger.info("LLM Sentiment scoring completed for %d items.", len(updates))


if __name__ == "__main__":
    db = SQLitePortfolioDB()
    score_news_batch(db)
