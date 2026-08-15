"""Production News Email / Newsletter Scraper for PEA Pollux.

Ingests financial newsletters via Yahoo IMAP, filters via strict sender whitelist,
sanitizes HTML/content via text_cleaner, and persists articles into SQLite news_master.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "00_data_sensors"))

try:
    from text_cleaner import clean_financial_text
except ImportError:
    def clean_financial_text(t: str) -> str:
        return t[:1500] if t else ""

try:
    from imap_ingest import YahooImapClient, parse_newsletter, is_allowed_sender, dedupe_articles
except ImportError:
    try:
        from .imap_ingest import YahooImapClient, parse_newsletter, is_allowed_sender, dedupe_articles
    except ImportError:
        YahooImapClient = None
        parse_newsletter = None
        is_allowed_sender = None
        dedupe_articles = None

logger = logging.getLogger(__name__)


def _hash_id(source: str, title: str, published_at: str) -> str:
    raw = f"{source}_{title}_{published_at}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def run_email_scraper(portfolio_db: Any = None, folder: str = "Finance", limit: int = 20) -> int:
    """Production entry point: pull email newsletter content from Yahoo Mail IMAP,
    filter, clean, deduplicate, and persist to news_master in SQLite.

    Args:
        portfolio_db: PortfolioDB instance.
        folder: IMAP folder to poll (defaults to "Finance").
        limit: Max messages to fetch.

    Returns:
        int: Number of new news items saved to SQLite.
    """
    user = os.getenv("YAHOO_MAIL_USER")
    app_pwd = os.getenv("YAHOO_MAIL_APP_PASSWORD")

    if not user or not app_pwd:
        logger.info("YAHOO_MAIL_USER or YAHOO_MAIL_APP_PASSWORD not set. Skipping live IMAP email scrape.")
        return 0

    if YahooImapClient is None or parse_newsletter is None:
        logger.error("IMAP ingestion modules not available.")
        return 0

    client = YahooImapClient(user=user, app_password=app_pwd)
    raw_articles: List[dict] = []

    try:
        messages = client.fetch_recent(folder=folder, limit=limit)
        logger.info("Fetched %d messages from IMAP folder '%s'.", len(messages), folder)

        for msg in messages:
            if is_allowed_sender and not is_allowed_sender(msg.sender):
                logger.debug("Skipping message from non-whitelisted sender: %s", msg.sender)
                continue

            parsed = parse_newsletter(msg)
            for art in parsed.get("articles", []):
                raw_articles.append(art)

    except Exception as exc:
        logger.error("IMAP email scraping failed: %s", exc, exc_info=True)
        return 0
    finally:
        client.close()

    if not raw_articles:
        logger.info("No new newsletter articles found.")
        return 0

    if dedupe_articles:
        unique_articles = dedupe_articles(raw_articles)
    else:
        unique_articles = raw_articles

    items_to_save: List[dict] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for art in unique_articles:
        raw_title = art.get("title", "")
        raw_content = art.get("content", "") or raw_title
        clean_title = clean_financial_text(raw_title)[:240]
        clean_content = clean_financial_text(raw_content)[:1500]

        if not clean_title or len(clean_title) < 10:
            continue

        pub = art.get("date") or now_iso
        source = art.get("source_sender") or "Newsletter"
        item_id = _hash_id("newsletter", clean_title, str(pub))

        items_to_save.append({
            "id": item_id,
            "ticker": "MARCHE",
            "title": clean_title,
            "source": source,
            "url": art.get("url", ""),
            "published_at": pub,
            "sentiment_score": None,
            "sentiment_label": None,
            "content": clean_content,
        })

    if not items_to_save:
        return 0

    saved_count = 0
    if portfolio_db is not None:
        try:
            if hasattr(portfolio_db, "save_news_items"):
                saved_count = portfolio_db.save_news_items(items_to_save)
            elif hasattr(portfolio_db, "insert_raw_news"):
                saved_count = portfolio_db.insert_raw_news(items_to_save)
            logger.info("Successfully persisted %d newsletter articles into SQLite news_master.", saved_count)
        except Exception as exc:
            logger.error("Failed to save newsletter items to DB: %s", exc)

    return saved_count


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Testing News Email Scraper...")
    res = run_email_scraper()
    print(f"Scraped and saved: {res} articles.")
