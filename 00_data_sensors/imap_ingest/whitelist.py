"""Strict sender whitelist for newsletter IMAP ingest.

Only these From addresses are parsed; receipts / security alerts are skipped.
"""

from __future__ import annotations

import re
from typing import FrozenSet

ALLOWED_SENDERS: FrozenSet[str] = frozenset({
    # FR / PEA-oriented additions
    "hello@brief.me",
    "hello@brief.eco",
    "contact@cafedelabourse.com",
    "plancash@substack.com",
    "europeansmallcapideas@substack.com",
    "frenchhiddenchampions@substack.com",
    "newsletter@zonebourse.com",
    "contact@zonebourse.com",
    "investir@lesechos.fr",
    "newsletter@boursorama.fr",
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
    if not from_header:
        return False
    email = extract_sender_email(from_header)
    if not email:
        return False
    if email in ALLOWED_SENDERS:
        return True
    # If domain is substack or finance provider, allow
    if "@substack.com" in email or "@brief." in email or "@lesechos." in email:
        return True
    return False
