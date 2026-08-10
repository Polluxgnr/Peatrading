"""News RSS Scraper for PEA Sniper Terminal.

Fetches European and French financial RSS feeds (Boursorama, Les Echos, Yahoo Finance, AMF),
normalizes them, and persists them into SQLite ``news_master``.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, List

import feedparser

logger = logging.getLogger(__name__)

# Curated financial RSS feeds relevant for French PEA / European equities
_RSS_FEEDS = [
    {"source": "Boursorama", "url": "https://www.boursorama.com/bourse/actualites/flux-rss"},
    {"source": "Les Echos", "url": "https://www.lesechos.fr/rss/marches.xml"},
    {"source": "ZoneBourse", "url": "https://www.zonebourse.com/rss/FeedNews.php"},
    {"source": "YahooFinance CAC", "url": "https://finance.yahoo.com/rss/headline?s=%5EFCHI"},
]


def _hash_id(source: str, title: str, published_at: str) -> str:
    raw = f"{source}_{title}_{published_at}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def parse_rss_feed(feed_info: dict) -> List[dict]:
    """Fetch and parse an individual RSS feed."""
    url = feed_info["url"]
    source = feed_info["source"]
    items: List[dict] = []

    try:
        parsed = feedparser.parse(url)
        for entry in parsed.entries[:15]:
            title = str(getattr(entry, "title", "")).strip()
            if not title:
                continue

            link = str(getattr(entry, "link", ""))
            pub = getattr(entry, "published", None) or getattr(entry, "updated", None)
            if not pub:
                pub = datetime.now(timezone.utc).isoformat()

            items.append({
                "id": _hash_id(source, title, str(pub)),
                "ticker": None,
                "title": title,
                "source": source,
                "url": link,
                "published_at": str(pub),
                "sentiment_score": None,
            })
    except Exception as exc:  # noqa: BLE001
        logger.debug("RSS feed %s (%s) failed: %s", source, url, exc)

    return items


def run_rss_scraper(portfolio_db: Any) -> int:
    """Entry point: pull financial RSS feeds and save to news_master in SQLite.

    Args:
        portfolio_db: PortfolioDB instance.

    Returns:
        int: Number of items saved.
    """
    logger.info("Running News RSS Scraper...")
    all_items: List[dict] = []

    for feed_info in _RSS_FEEDS:
        feed_items = parse_rss_feed(feed_info)
        all_items.extend(feed_items)

    if portfolio_db is not None and hasattr(portfolio_db, "save_news_items"):
        count = portfolio_db.save_news_items(all_items)
        logger.info("News RSS Scraper completed: %d items persisted.", count)
        return count

    return len(all_items)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    n = run_rss_scraper(None)
    print(f"Fetched {n} RSS items.")
