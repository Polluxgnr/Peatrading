"""Production IMAP Newsletter Ingestion Package for PEA Pollux."""

from .imap_client import RawMessage, YahooImapClient
from .html_parser import parse_newsletter
from .whitelist import ALLOWED_SENDERS, is_allowed_sender, extract_sender_email
from .dedupe import dedupe_articles

__all__ = [
    "RawMessage",
    "YahooImapClient",
    "parse_newsletter",
    "ALLOWED_SENDERS",
    "is_allowed_sender",
    "extract_sender_email",
    "dedupe_articles",
]
