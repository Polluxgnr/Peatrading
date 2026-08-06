import os
import hashlib
import requests
from datetime import datetime
from dotenv import load_dotenv

import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "01_memory_core"))

from logging_setup import get_logger
from sqlite_portfolio import SQLitePortfolioDB

logger = get_logger("news_api_client")

# Load environment variables
load_dotenv(_ROOT / ".env")

ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")

def fetch_alpha_vantage_news() -> list[dict]:
    """Fetch market news from Alpha Vantage Sentiment API."""
    if not ALPHA_VANTAGE_API_KEY:
        logger.warning("ALPHA_VANTAGE_API_KEY not found. Skipping API fetch.")
        return []
        
    url = f"https://www.alphavantage.co/query?function=NEWS_SENTIMENT&limit=50&apikey={ALPHA_VANTAGE_API_KEY}"
    news_items = []
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if "feed" not in data:
            logger.warning("Unexpected response from Alpha Vantage: %s", str(data)[:100])
            return []
            
        for item in data["feed"]:
            link = item.get("url", "")
            title = item.get("title", "")
            summary = item.get("summary", "")
            
            if not link or not title:
                continue
                
            uid = hashlib.sha256(link.encode("utf-8")).hexdigest()
            
            # Alpha vantage format: YYYYMMDDTHHMMSS
            time_str = item.get("time_published", "")
            try:
                dt = datetime.strptime(time_str, "%Y%m%dT%H%M%S")
                published_at = dt.isoformat()
            except Exception:
                published_at = datetime.utcnow().isoformat()
                
            # Attempt to extract the most relevant ticker
            ticker = None
            ticker_sentiments = item.get("ticker_sentiment", [])
            if ticker_sentiments:
                # Get the one with highest relevance
                best_match = max(ticker_sentiments, key=lambda x: float(x.get("relevance_score", 0)))
                ticker = best_match.get("ticker")
                
            news_items.append({
                "id": uid,
                "published_at": published_at,
                "ticker": ticker,
                "source": "API_AlphaVantage",
                "url": link,
                "title": title,
                "content": summary
            })
            
    except requests.exceptions.RequestException as e:
        logger.warning("Network error fetching Alpha Vantage news: %s", e)
    except Exception as e:
        logger.warning("Error processing Alpha Vantage news: %s", e)
        
    return news_items

def run_api_scraper(db: SQLitePortfolioDB):
    news = fetch_alpha_vantage_news()
    if news:
        db.upsert_news_master(news)
        logger.info("API Scraper finished: inserted %d items.", len(news))
    else:
        logger.info("API Scraper finished: no items found or API not configured.")

if __name__ == "__main__":
    db = SQLitePortfolioDB()
    run_api_scraper(db)
