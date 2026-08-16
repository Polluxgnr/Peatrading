"""Consolidated Financial News & Sentiment Adapter for Layer 1.

Polls financial RSS feeds (Boursorama, Les Echos, ZoneBourse, Yahoo Finance)
and news APIs, mapping incoming streams into standardized AlternativeSignal contracts.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent.parent
for sub in ("00_data_sensors", "01_memory_core"):
    sys.path.insert(0, str(_ROOT / sub))

from base_adapters import AbstractPollAdapter
from data_contracts import AlternativeSignal

try:
    from news_rss_scraper import _RSS_FEEDS, parse_rss_feed
except ImportError:
    _RSS_FEEDS = []
    def parse_rss_feed(feed_info: dict) -> List[dict]:
        return []

try:
    from news_api_client import fetch_yfinance_news
except ImportError:
    fetch_yfinance_news = None

logger = logging.getLogger("news_adapter")


class ConsolidatedNewsAdapter(AbstractPollAdapter):
    """Adapter aggregating multi-source financial news and raw sentiment signals."""

    interval_seconds: int = 600  # 10 minutes

    def __init__(self, tickers: Optional[List[str]] = None, interval_seconds: int = 600) -> None:
        self.interval_seconds = interval_seconds
        self.tickers = tickers or ["MC.PA", "OR.PA", "TTE.PA", "SAN.PA", "AIR.PA", "AI.PA"]

    async def fetch(self) -> List[AlternativeSignal]:
        """Fetch all RSS feeds and company news items concurrently."""
        loop = asyncio.get_event_loop()
        signals: List[AlternativeSignal] = []

        # 1. Fetch General Financial RSS Feeds
        for feed in _RSS_FEEDS:
            try:
                items = await loop.run_in_executor(None, parse_rss_feed, feed)
                for item in items:
                    signals.append(
                        AlternativeSignal(
                            ticker=item.get("ticker"),
                            signal_type="NEWS_SENTIMENT",
                            value=float(item.get("sentiment_score") or 0.0),
                            confidence=1.0,
                            source=f"RSS_{item.get('source', 'FINANCE')}",
                            metadata={
                                "headline": item.get("title", ""),
                                "url": item.get("url", ""),
                                "published_at": item.get("published_at", ""),
                            },
                        )
                    )
            except Exception as exc:
                logger.warning("ConsolidatedNewsAdapter RSS error for %s: %s", feed.get("source"), exc)

        # 2. Fetch Ticker-Specific News from API Scrapers if available
        if fetch_yfinance_news is not None:
            for ticker in self.tickers[:8]:
                try:
                    t_items = await loop.run_in_executor(None, fetch_yfinance_news, ticker)
                    for item in t_items:
                        signals.append(
                            AlternativeSignal(
                                ticker=ticker,
                                signal_type="NEWS_SENTIMENT",
                                value=float(item.get("sentiment_score") or 0.0),
                                confidence=1.0,
                                source="YFINANCE_NEWS",
                                metadata={
                                    "headline": item.get("title", ""),
                                    "url": item.get("url", ""),
                                    "published_at": item.get("published_at", ""),
                                },
                            )
                        )
                except Exception as exc:
                    logger.debug("ConsolidatedNewsAdapter API error for %s: %s", ticker, exc)

        logger.info("ConsolidatedNewsAdapter emitted %d AlternativeSignal(s).", len(signals))
        return signals
