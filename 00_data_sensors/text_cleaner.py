"""Financial Text Sanitizer & Data Janitor for PEA Pollux NLP pipeline.

Strips HTML, URLs, emails, boilerplate disclaimers, and normalizes financial text
to under 1500 characters so Transformer/FinBERT models receive clean, high-signal input.
"""

from __future__ import annotations

import re
import html
import logging
from typing import List

logger = logging.getLogger(__name__)

# Compile regex patterns for fast sanitization
_HTML_TAG_RE = re.compile(r"<[^>]+>", re.IGNORECASE)
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
_MULTISPACE_RE = re.compile(r"[ \t]+")
_MULTILINE_RE = re.compile(r"\n{2,}")

_BOILERPLATE_PATTERNS: List[re.Pattern] = [
    re.compile(r"unsubscribe\b.*", re.IGNORECASE | re.DOTALL),
    re.compile(r"désabonner\b.*", re.IGNORECASE | re.DOTALL),
    re.compile(r"se désinscrire\b.*", re.IGNORECASE | re.DOTALL),
    re.compile(r"view in (your )?browser\b.*", re.IGNORECASE),
    re.compile(r"consulter (ce message )?dans votre navigateur\b.*", re.IGNORECASE),
    re.compile(r"disclaimer\s*:?.*", re.IGNORECASE),
    re.compile(r"avertissement\s*:?.*", re.IGNORECASE),
    re.compile(r"all rights reserved\b.*", re.IGNORECASE),
    re.compile(r"tous droits réservés\b.*", re.IGNORECASE),
    re.compile(r"ce message a été envoyé à\b.*", re.IGNORECASE),
    re.compile(r"this email was sent to\b.*", re.IGNORECASE),
    re.compile(r"click here to (opt out|manage).*?", re.IGNORECASE),
]


def clean_financial_text(raw_text: str, max_chars: int = 1500) -> str:
    """Sanitize and normalize financial news and newsletter text.

    Args:
        raw_text: Raw incoming text (HTML, email body, RSS snippet, or headline).
        max_chars: Maximum character limit (default 1500 for FinBERT context).

    Returns:
        str: Sanitized, plain text string. Empty if no signal remains.
    """
    if not raw_text or not isinstance(raw_text, str):
        return ""

    text = raw_text

    # 1. Unescape HTML entities
    text = html.unescape(text)

    # 2. Strip HTML tags (try BeautifulSoup if available, else regex)
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(text, "html.parser")
        # Remove script and style elements
        for script in soup(["script", "style", "noscript", "header", "footer"]):
            script.decompose()
        text = soup.get_text(separator=" ")
    except Exception:
        text = _HTML_TAG_RE.sub(" ", text)

    # 3. Strip URLs and Emails
    text = _URL_RE.sub("", text)
    text = _EMAIL_RE.sub("", text)

    # 4. Remove common boilerplate lines/sections
    for pattern in _BOILERPLATE_PATTERNS:
        text = pattern.sub("", text)

    # 5. Clean up whitespace and newlines
    text = _MULTISPACE_RE.sub(" ", text)
    text = _MULTILINE_RE.sub("\n", text)
    text = text.strip()

    # 6. Truncate to maximum context window safely focusing on lead content
    if len(text) > max_chars:
        # Split on sentence boundaries if possible
        truncated = text[:max_chars]
        last_period = max(truncated.rfind(". "), truncated.rfind(".\n"), truncated.rfind("! "), truncated.rfind("? "))
        if last_period > max_chars // 2:
            text = truncated[: last_period + 1].strip()
        else:
            text = truncated.strip()

    return text


if __name__ == "__main__":
    sample_html = """
    <html>
        <body>
            <h1>LVMH : Chiffre d'affaires record au Q1 2026 !</h1>
            <p>Le groupe de luxe annonce une hausse de 12% de ses ventes, tirée par la maroquinerie.</p>
            <p>Retrouvez tous les détails sur <a href="https://example.com/lvmh">https://example.com/lvmh</a>.</p>
            <footer>
                Disclaimer: Ceci n'est pas un conseil financier. 
                <a href="https://example.com/unsub">Unsubscribe from newsletter</a>.
                All rights reserved 2026.
            </footer>
        </body>
    </html>
    """
    cleaned = clean_financial_text(sample_html)
    print("Sanitized text output:")
    print("---")
    print(cleaned)
    print("---")
