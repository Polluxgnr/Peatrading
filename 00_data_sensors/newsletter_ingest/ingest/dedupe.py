"""Simple near-duplicate headline collapse (no ML)."""

from __future__ import annotations

import logging
import re
from typing import List

logger = logging.getLogger(__name__)


def _norm(title: str) -> str:
    t = (title or "").lower()
    t = re.sub(r"[^a-z0-9àâäéèêëïîôùûüç\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _token_set(title: str) -> set[str]:
    return {w for w in _norm(title).split() if len(w) > 2}


def _similar(a: str, b: str, threshold: float = 0.72) -> bool:
    """Jaccard similarity on token sets — cheap and good enough for newsletters."""
    ta, tb = _token_set(a), _token_set(b)
    if not ta or not tb:
        return _norm(a) == _norm(b) and bool(_norm(a))
    inter = len(ta & tb)
    union = len(ta | tb)
    return (inter / union) >= threshold if union else False


def dedupe_articles(articles: List[dict]) -> List[dict]:
    """Drop near-identical titles republished the same day across digests.

    Keeps the first occurrence (stable order). Logs how many were removed.
    """
    kept: List[dict] = []
    for art in articles:
        title = art.get("title") or ""
        if any(_similar(title, k.get("title") or "") for k in kept):
            continue
        # Also collapse exact same cleaned URL
        url = art.get("url") or ""
        if url and any(url == (k.get("url") or "") for k in kept):
            continue
        kept.append(art)
    removed = len(articles) - len(kept)
    if removed:
        logger.info("Removed %d near-duplicate headline(s).", removed)
    return kept
