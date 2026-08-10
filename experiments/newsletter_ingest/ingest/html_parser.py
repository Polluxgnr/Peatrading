"""Extract article titles/links from verbose newsletter HTML."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse, urlunparse

from bs4 import BeautifulSoup

from ingest.imap_client import RawMessage

logger = logging.getLogger(__name__)

_TRACKER_HOST_BITS = (
    "doubleclick", "googleadservices", "facebook.com/tr", "mailchi.mp/track",
    "list-manage.com/track", "click.", "/track/", "utm_source=",
)


def _clean_url(url: str) -> str:
    """Strip common tracking query noise while keeping the path."""
    try:
        p = urlparse(url)
        # Drop obvious click-wrappers with empty path
        if any(b in url.lower() for b in ("unsubscribe", "mailto:")):
            return ""
        # Keep scheme/netloc/path; drop query/fragment for stable dedupe keys.
        return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))
    except Exception:  # noqa: BLE001
        return url.strip()


def _looks_like_article(title: str, href: str) -> bool:
    t = (title or "").strip()
    if len(t) < 18:
        return False
    # Skip chrome / CTAs
    bad = (
        "unsubscribe", "view in browser", "voir dans le navigateur",
        "privacy", "preferences", "manage subscription", "ouvrir dans",
        "share on", "twitter", "linkedin", "facebook", "instagram",
    )
    low = t.lower()
    if any(b in low for b in bad):
        return False
    if not href.startswith("http"):
        return False
    if any(b in href.lower() for b in _TRACKER_HOST_BITS) and "http" in href:
        # Still allow if path looks real after clean
        cleaned = _clean_url(href)
        if not cleaned or cleaned.count("/") < 3:
            return False
    return True


def parse_newsletter(msg: RawMessage) -> dict[str, Any]:
    """Parse one email into metadata + article candidates.

    Args:
        msg: Raw IMAP message.

    Returns:
        dict: subject/sender/date + ``articles`` list of
        ``{title, url, source_subject, source_sender, date}``.
    """
    html = msg.html or ""
    text = msg.text or ""
    articles: list[dict[str, str]] = []
    seen_href: set[str] = set()

    if html:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            title = a.get_text(" ", strip=True)
            href = a["href"].strip()
            if not _looks_like_article(title, href):
                continue
            clean = _clean_url(href) or href
            if clean in seen_href:
                continue
            seen_href.add(clean)
            articles.append({
                "title": re.sub(r"\s+", " ", title)[:240],
                "url": clean,
                "source_subject": msg.subject,
                "source_sender": msg.sender,
                "date": msg.date,
            })
    elif text:
        # Fallback: plain URLs in text body
        for m in re.finditer(r"https?://\S+", text):
            href = m.group(0).rstrip(").,]")
            title = href
            if not _looks_like_article(title, href):
                continue
            clean = _clean_url(href) or href
            if clean in seen_href:
                continue
            seen_href.add(clean)
            articles.append({
                "title": title[:240],
                "url": clean,
                "source_subject": msg.subject,
                "source_sender": msg.sender,
                "date": msg.date,
            })

    return {
        "uid": msg.uid,
        "subject": msg.subject,
        "sender": msg.sender,
        "date": msg.date,
        "articles": articles,
    }
