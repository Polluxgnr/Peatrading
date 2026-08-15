"""Extract article titles/links from verbose newsletter HTML."""

from __future__ import annotations

import logging
import re
from typing import Any, List, Set
from urllib.parse import urlparse, urlunparse

from bs4 import BeautifulSoup

from .imap_client import RawMessage

logger = logging.getLogger(__name__)

_TRACKER_HOST_BITS = (
    "doubleclick", "googleadservices", "facebook.com/tr", "mailchi.mp/track",
    "list-manage.com/track", "click.", "/track/", "utm_source=",
)


def _clean_url(url: str) -> str:
    """Strip common tracking query noise while keeping the path."""
    try:
        p = urlparse(url)
        if any(b in url.lower() for b in ("unsubscribe", "mailto:")):
            return ""
        return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))
    except Exception:  # noqa: BLE001
        return url.strip()


def _looks_like_article(title: str, href: str) -> bool:
    t = (title or "").strip()
    if len(t) < 18:
        return False
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
        ``{title, url, source_subject, source_sender, date, content}``.
    """
    html = msg.html or ""
    text = msg.text or ""
    articles: List[dict[str, str]] = []
    seen_href: Set[str] = set()

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
            
            # Extract surrounding paragraph context if available
            parent = a.find_parent(["p", "div", "td", "li"])
            context_text = parent.get_text(" ", strip=True) if parent else title

            articles.append({
                "title": re.sub(r"\s+", " ", title)[:240],
                "url": clean,
                "source_subject": msg.subject,
                "source_sender": msg.sender,
                "date": msg.date,
                "content": re.sub(r"\s+", " ", context_text)[:1500],
            })
    elif text:
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
                "content": text[:1500],
            })

    # If no links qualified as articles, use the subject and lead text
    if not articles and (msg.subject or text):
        clean_subj = re.sub(r"\s+", " ", msg.subject).strip()
        if len(clean_subj) >= 10:
            articles.append({
                "title": clean_subj[:240],
                "url": "",
                "source_subject": msg.subject,
                "source_sender": msg.sender,
                "date": msg.date,
                "content": (text or html)[:1500],
            })

    return {
        "uid": msg.uid,
        "subject": msg.subject,
        "sender": msg.sender,
        "date": msg.date,
        "articles": articles,
    }
