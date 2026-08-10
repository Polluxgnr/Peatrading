"""Shared HTTP helpers for fragile French-market scrapers."""

from __future__ import annotations

import logging
import random
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

_USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15",
)

DEFAULT_TIMEOUT = 25


def stealth_headers() -> dict[str, str]:
    """Return a rotating browser-like header set."""
    return {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
    }


def rate_limit(min_s: float = 0.6, max_s: float = 1.8) -> None:
    """Sleep a random delay to reduce ban risk."""
    time.sleep(random.uniform(min_s, max_s))


def safe_get(
    url: str,
    *,
    session: requests.Session | None = None,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    expect_json: bool = False,
    quiet: bool = False,
) -> requests.Response | None:
    """GET with stealth headers. Returns ``None`` on any failure (never raises)."""
    log = logger.debug if quiet else logger.warning
    try:
        rate_limit()
        hdrs = {**stealth_headers(), **(headers or {})}
        client = session or requests
        resp = client.get(url, headers=hdrs, params=params, timeout=timeout)
        if resp.status_code in (403, 429):
            log("Scraper blocked (%s) for %s", resp.status_code, url)
            return None
        if resp.status_code >= 400:
            log("Scraper HTTP %s for %s", resp.status_code, url)
            return None
        if expect_json:
            ct = (resp.headers.get("content-type") or "").lower()
            if "json" not in ct and not resp.text.lstrip().startswith(("{", "[")):
                log("Scraper expected JSON, got non-JSON from %s", url)
                return None
        return resp
    except Exception as exc:  # noqa: BLE001
        log("Scraper GET failed for %s: %s", url, exc)
        return None
