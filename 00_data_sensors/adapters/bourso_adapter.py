"""Boursorama PEA Universe & Instrument Metadata Adapter for Layer 1.

Polls French PEA constituent lists and instrument profiles from Boursorama with
anti-bot resilience, emitting standardized AlternativeSignal updates.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent.parent
for sub in ("00_data_sensors", "00_data_sensors/scrapers", "01_memory_core"):
    sys.path.insert(0, str(_ROOT / sub))

from base_adapters import AbstractPollAdapter
from data_contracts import AlternativeSignal

try:
    from bourso_scraper import BoursoramaScraper
except ImportError:
    from scrapers.bourso_scraper import BoursoramaScraper

logger = logging.getLogger("bourso_adapter")


class BoursoUniverseAdapter(AbstractPollAdapter):
    """Adapter polling Boursorama for PEA eligibility and universe updates."""

    interval_seconds: int = 86400  # daily check

    def __init__(self, interval_seconds: int = 86400) -> None:
        self.interval_seconds = interval_seconds
        self.scraper = BoursoramaScraper() if BoursoramaScraper is not None else None

    async def fetch(self) -> List[AlternativeSignal]:
        """Harvest active PEA universe and emit an UNIVERSE_UPDATE signal."""
        if self.scraper is None:
            return []

        loop = asyncio.get_event_loop()
        try:
            items = await loop.run_in_executor(None, self.scraper.get_pea_universe)
            if not items:
                logger.info("BoursoUniverseAdapter: No items returned (or blocked).")
                return []

            tickers = [str(it.get("ticker")) for it in items if it.get("ticker")]
            sectors = list({str(it.get("sector")) for it in items if it.get("sector")})

            sig = AlternativeSignal(
                ticker="PARIS",
                signal_type="UNIVERSE_UPDATE",
                value=float(len(tickers)),
                confidence=1.0,
                source="BOURSORAMA",
                metadata={
                    "total_constituents": len(tickers),
                    "sample_tickers": tickers[:20],
                    "sectors": sectors,
                },
            )
            logger.info("BoursoUniverseAdapter emitted UNIVERSE_UPDATE for %d constituents.", len(tickers))
            return [sig]
        except Exception as exc:
            logger.warning("BoursoUniverseAdapter encountered an issue: %s", exc)
            return []
