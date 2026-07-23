"""Strict sender whitelist for newsletter IMAP ingest.

Only these From addresses are parsed; receipts / security alerts are skipped.
"""

from __future__ import annotations

import re
from typing import FrozenSet

# Exact email addresses allowed (case-insensitive match on extracted address).
ALLOWED_SENDERS: FrozenSet[str] = frozenset({
    # Phase 18 core digests
    "dan@tldrnewsletter.com",
    "luc@the-nbs.fr",
    "thevccorner@substack.com",
    "theaicorner1@substack.com",
    "hello@brief.me",
    "daily@timetosignoff.fr",
    "hello@brief.eco",
    "newsletter@thedeepview.co",
    "laura@lbkconsulting.fr",
    "hello@brief.science",
    # FR / PEA-oriented additions
    "contact@cafedelabourse.com",
    "charlessterlings@substack.com",
    "plancash@substack.com",
})

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+", re.IGNORECASE)


def extract_sender_email(from_header: str) -> str:
    """Pull the bare email from a From header (``Name <a@b.c>`` or bare)."""
    if not from_header:
        return ""
    match = _EMAIL_RE.search(from_header)
    return match.group(0).lower() if match else ""


def is_allowed_sender(from_header: str) -> bool:
    """Return True iff the From address is on the newsletter whitelist."""
    email = extract_sender_email(from_header)
    return bool(email) and email in ALLOWED_SENDERS
