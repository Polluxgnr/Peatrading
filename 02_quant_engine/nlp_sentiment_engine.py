import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from core.logging_setup import get_logger
from memory_core.sqlite_portfolio import SQLitePortfolioDB

logger = get_logger("nlp_sentiment_engine")

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
except ImportError:
    logger.error("vaderSentiment not installed. Run: pip install vaderSentiment")
    sys.exit(1)

def score_news_batch(db: SQLitePortfolioDB):
    """Fetch unprocessed news, score them using VADER, and update the database."""
    unprocessed = db.get_unprocessed_news()
    if not unprocessed:
        logger.info("No unprocessed news found.")
        return
        
    logger.info("Scoring %d unprocessed news items...", len(unprocessed))
    
    analyzer = SentimentIntensityAnalyzer()
    updates = []
    
    for item in unprocessed:
        # Combine title and content for scoring
        text = f"{item['title']} {item['content'] or ''}"
        
        # VADER returns a dict, we want the 'compound' score [-1.0, 1.0]
        scores = analyzer.polarity_scores(text)
        compound = float(scores["compound"])
        
        if compound >= 0.05:
            label = "Bullish"
        elif compound <= -0.05:
            label = "Bearish"
        else:
            label = "Neutral"
            
        updates.append({
            "id": item["id"],
            "sentiment_score": compound,
            "sentiment_label": label
        })
        
    if updates:
        db.update_news_sentiment(updates)
        logger.info("Sentiment scoring completed for %d items.", len(updates))

if __name__ == "__main__":
    db = SQLitePortfolioDB()
    score_news_batch(db)
