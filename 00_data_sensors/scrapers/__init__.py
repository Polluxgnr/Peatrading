"""French-market scrapers (AMF BDIF + Boursorama).

Isolated from the clean yfinance API layer. Every public method is antifragile.
"""

from amf_scraper import AmfInsiderScraper
from bourso_scraper import (
    BoursoramaScraper,
    bourso_slug_to_yahoo,
    yahoo_to_bourso_slug,
)

__all__ = [
    "AmfInsiderScraper",
    "BoursoramaScraper",
    "bourso_slug_to_yahoo",
    "yahoo_to_bourso_slug",
]
