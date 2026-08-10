"""Whitelist sender filter for newsletter ingest."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments" / "newsletter_ingest"))

from ingest.whitelist import (  # noqa: E402
    extract_sender_email,
    is_allowed_sender,
)


def test_extract_and_allow_known_senders():
    assert extract_sender_email('TLDR <dan@tldrnewsletter.com>') == (
        "dan@tldrnewsletter.com"
    )
    assert is_allowed_sender("dan@tldrnewsletter.com")
    assert is_allowed_sender("Brief <hello@brief.me>")
    assert not is_allowed_sender("Yahoo <noreply@yahoo.com>")
    assert not is_allowed_sender("Security Alert <account-protection@yahoo.com>")
