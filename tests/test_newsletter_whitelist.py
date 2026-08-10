"""Whitelist sender filter test for newsletter ingest."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "00_data_sensors" / "newsletter_ingest"))

from ingest.whitelist import (  # noqa: E402
    extract_sender_email,
    is_allowed_sender,
)


class TestNewsletterWhitelist(unittest.TestCase):

    def test_extract_and_allow_known_senders(self):
        self.assertEqual(
            extract_sender_email("Brief <hello@brief.me>"),
            "hello@brief.me",
        )
        self.assertTrue(is_allowed_sender("hello@brief.me"))
        self.assertTrue(is_allowed_sender("Brief <hello@brief.me>"))
        self.assertTrue(is_allowed_sender("contact@cafedelabourse.com"))
        self.assertFalse(is_allowed_sender("Yahoo <noreply@yahoo.com>"))
        self.assertFalse(is_allowed_sender("Security Alert <account-protection@yahoo.com>"))


if __name__ == "__main__":
    unittest.main()
