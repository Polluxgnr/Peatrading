import hashlib
import re
from datetime import datetime
import feedparser
import bs4

import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from core.logging_setup import get_logger
from memory_core.sqlite_portfolio import SQLitePortfolioDB

logger = get_logger("news_rss_scraper")

RSS_FEEDS = [
    # Fallback to general financial news
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664"
]

def clean_html(raw_html: str) -> str:
    """Remove HTML tags from a string."""
    if not raw_html:
        return ""
    try:
        soup = bs4.BeautifulSoup(raw_html, "html.parser")
        return soup.get_text(separator=" ", strip=True)
    except Exception:
        # Fallback regex
        cleanr = re.compile('<.*?>')
        return re.sub(cleanr, '', str(raw_html))

def fetch_rss_news() -> list[dict]:
    """Fetch and parse RSS feeds into the news_master schema."""
    news_items = []
    
    for url in RSS_FEEDS:
        try:
            logger.info("Fetching RSS feed: %s", url)
            feed = feedparser.parse(url)
            
            for entry in feed.entries:
                title = getattr(entry, "title", "").strip()
                link = getattr(entry, "link", "")
                summary = clean_html(getattr(entry, "summary", ""))
                
                if not title or not link:
                    continue
                    
                # Create a stable ID hash
                uid = hashlib.sha256(link.encode("utf-8")).hexdigest()
                
                # Parse date if available, else use current time
                pub_date = getattr(entry, "published", None)
                if pub_date:
                    try:
                        # Feedparser parses standard dates into a time.struct_time
                        dt = datetime(*entry.published_parsed[:6])
                        published_at = dt.isoformat()
                    except Exception:
                        published_at = datetime.utcnow().isoformat()
                else:
                    published_at = datetime.utcnow().isoformat()
                
                news_items.append({
                    "id": uid,
                    "published_at": published_at,
                    "ticker": None,  # General market news
                    "source": "RSS_Feed",
                    "url": link,
                    "title": title,
                    "content": summary
                })
        except Exception as exc:
            logger.warning("Failed to fetch RSS %s: %s", url, exc)
            
    return news_items

def run_rss_scraper(db: SQLitePortfolioDB):
    news = fetch_rss_news()
    if news:
        db.upsert_news_master(news)
        logger.info("RSS Scraper finished: inserted %d items.", len(news))
    else:
        logger.info("RSS Scraper finished: no items found.")

if __name__ == "__main__":
    db = SQLitePortfolioDB()
    run_rss_scraper(db)
