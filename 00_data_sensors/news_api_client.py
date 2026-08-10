"""News API Client for PEA Sniper Terminal.

Fetches financial news articles from yfinance and optional REST APIs (Finnhub / NewsAPI),
normalizes them, and persists them into the SQLite ``news_master`` table via PortfolioDB.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Any, List, Optional

import requests
import yfinance as yf

logger = logging.getLogger(__name__)

# Sample of core liquid PEA / macro tickers to poll regularly
_DEFAULT_NEWS_TICKERS = [
    "CW8.PA", "MC.PA", "OR.PA", "AI.PA", "RMS.PA", "TTE.PA", "SAN.PA",
    "BNP.PA", "AIR.PA", "SU.PA", "EL.PA", "KER.PA", "DG.PA", "SAF.PA",
    "^FCHI", "^GSPC",
]


def _hash_id(source: str, title: str, published_at: str) -> str:
    raw = f"{source}_{title}_{published_at}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def fetch_yfinance_news(tickers: Optional[List[str]] = None, max_per_ticker: int = 4) -> List[dict]:
    """Fetch recent news articles via yfinance."""
    tickers = tickers or _DEFAULT_NEWS_TICKERS
    items: List[dict] = []
    seen_titles = set()

    for ticker in tickers[:15]:
        try:
            tk = yf.Ticker(ticker)
            news = getattr(tk, "news", []) or []
            for n in news[:max_per_ticker]:
                title = str(n.get("title") or "").strip()
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)

                pub_ts = n.get("providerPublishTime")
                if pub_ts:
                    pub_str = datetime.fromtimestamp(pub_ts, tz=timezone.utc).isoformat()
                else:
                    pub_str = datetime.now(timezone.utc).isoformat()

                link = str(n.get("link") or "")
                publisher = str(n.get("publisher") or "YahooFinance")
                article_id = _hash_id("yfinance", title, pub_str)

                items.append({
                    "id": article_id,
                    "ticker": ticker,
                    "title": title,
                    "source": publisher,
                    "url": link,
                    "published_at": pub_str,
                    "sentiment_score": None,
                })
        except Exception as exc:  # noqa: BLE001
            logger.debug("yfinance news failed for %s: %s", ticker, exc)
            continue

    return items


def fetch_finnhub_news(api_key: Optional[str] = None, category: str = "general") -> List[dict]:
    """Fetch market news from Finnhub API if key is available."""
    key = api_key or os.getenv("FINNHUB_API_KEY")
    if not key:
        return []

    url = f"https://finnhub.io/api/v1/news?category={category}&token={key}"
    items: List[dict] = []
    try:
        resp = requests.get(url, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                for row in data[:20]:
                    title = str(row.get("headline") or "").strip()
                    if not title:
                        continue
                    dt_ts = row.get("datetime")
                    if dt_ts:
                        pub_str = datetime.fromtimestamp(dt_ts, tz=timezone.utc).isoformat()
                    else:
                        pub_str = datetime.now(timezone.utc).isoformat()

                    items.append({
                        "id": _hash_id("finnhub", title, pub_str),
                        "ticker": row.get("related") or None,
                        "title": title,
                        "source": str(row.get("source") or "Finnhub"),
                        "url": str(row.get("url") or ""),
                        "published_at": pub_str,
                        "sentiment_score": None,
                    })
    except Exception as exc:  # noqa: BLE001
        logger.debug("Finnhub news API failed: %s", exc)

    return items


def run_api_scraper(portfolio_db: Any, tickers: Optional[List[str]] = None) -> int:
    """Entry point: pull API news and persist to news_master in SQLite.

    Args:
        portfolio_db: PortfolioDB instance.
        tickers: Optional list of tickers to target.

    Returns:
        int: Number of news items saved.
    """
    logger.info("Running News API Scraper...")
    all_items: List[dict] = []

    # 1) yfinance
    yf_items = fetch_yfinance_news(tickers)
    all_items.extend(yf_items)

    # 2) Finnhub
    fh_items = fetch_finnhub_news()
    all_items.extend(fh_items)

    if portfolio_db is not None and hasattr(portfolio_db, "save_news_items"):
        count = portfolio_db.save_news_items(all_items)
        logger.info("News API Scraper completed: %d items persisted.", count)
        return count

    return len(all_items)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    items = fetch_yfinance_news(["MC.PA", "CW8.PA"], max_per_ticker=2)
    print(f"Fetched {len(items)} items from yfinance:")
    for it in items:
        print(f" - [{it['ticker']}] {it['title']} ({it['source']})")
