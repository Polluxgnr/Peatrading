"""News Email / Newsletter Scraper for PEA Sniper Terminal.

Ingests financial newsletters via IMAP or from local JSON output exports,
normalizes them, and persists them into SQLite ``news_master``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_OUTPUT_DIR = _ROOT / "experiments" / "newsletter_ingest" / "output"


def _hash_id(source: str, title: str, published_at: str) -> str:
    raw = f"{source}_{title}_{published_at}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def ingest_local_newsletter_files() -> List[dict]:
    """Read parsed newsletter JSONs produced by the sandbox ingestor."""
    items: List[dict] = []
    if not _OUTPUT_DIR.exists():
        return items

    for json_file in _OUTPUT_DIR.glob("*.json"):
        try:
            content = json.loads(json_file.read_text(encoding="utf-8"))
            if isinstance(content, list):
                raw_items = content
            elif isinstance(content, dict):
                raw_items = content.get("articles") or content.get("items") or [content]
            else:
                continue

            for row in raw_items:
                title = str(row.get("subject") or row.get("title") or "").strip()
                if not title:
                    continue
                sender = str(row.get("sender") or row.get("source") or "Newsletter")
                pub = str(row.get("date") or row.get("published_at") or datetime.now(timezone.utc).isoformat())
                items.append({
                    "id": _hash_id("newsletter", title, pub),
                    "ticker": row.get("ticker"),
                    "title": title,
                    "source": sender,
                    "url": str(row.get("url") or ""),
                    "published_at": pub,
                    "sentiment_score": None,
                })
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed reading newsletter json %s: %s", json_file.name, exc)

    return items


def run_email_scraper(portfolio_db: Any) -> int:
    """Entry point: pull email newsletter content and save to news_master.

    Args:
        portfolio_db: PortfolioDB instance.

    Returns:
        int: Number of items saved.
    """
    logger.info("Running News Email / Newsletter Scraper...")
    items: List[dict] = []

    # 1. Try running live IMAP ingest if configured
    try:
        from experiments.newsletter_ingest.run_ingest import run_ingest_pipeline
        # If env variables are set, this will download and write output JSONs
        res = run_ingest_pipeline()
        if isinstance(res, list):
            for r in res:
                title = str(r.get("subject") or r.get("title") or "").strip()
                if title:
                    pub = str(r.get("date") or datetime.now(timezone.utc).isoformat())
                    items.append({
                        "id": _hash_id("newsletter_live", title, pub),
                        "ticker": r.get("ticker"),
                        "title": title,
                        "source": str(r.get("sender") or "Newsletter"),
                        "url": str(r.get("url") or ""),
                        "published_at": pub,
                        "sentiment_score": None,
                    })
    except Exception as exc:  # noqa: BLE001
        logger.debug("Live email IMAP ingest skipped/failed: %s", exc)

    # 2. Ingest existing output files
    file_items = ingest_local_newsletter_files()
    items.extend(file_items)

    if portfolio_db is not None and hasattr(portfolio_db, "save_news_items"):
        count = portfolio_db.save_news_items(items)
        logger.info("News Email Scraper completed: %d items persisted.", count)
        return count

    return len(items)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    n = run_email_scraper(None)
    print(f"Discovered {n} newsletter items.")
