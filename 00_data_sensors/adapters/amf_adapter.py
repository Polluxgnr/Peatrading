"""AMF Short Interest Adapter for Layer 1 Ingestion.

Polls the Autorité des Marchés Financiers (AMF) BDIF portal for Net Short Positions
and transforms raw regulatory records into standardized AlternativeSignal contracts.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import List, Optional

_ROOT = Path(__file__).resolve().parent.parent.parent
for sub in ("00_data_sensors", "00_data_sensors/scrapers", "01_memory_core"):
    sys.path.insert(0, str(_ROOT / sub))

from base_adapters import AbstractPollAdapter
from data_contracts import AlternativeSignal

try:
    from amf_short_scraper import AmfShortScraper
except ImportError:
    from scrapers.amf_short_scraper import AmfShortScraper

try:
    from figi_mapper import FigiMapper
except ImportError:
    FigiMapper = None

logger = logging.getLogger("amf_adapter")


class AmfShortAdapter(AbstractPollAdapter):
    """Adapter polling AMF short interest data."""

    interval_seconds: int = 3600  # regulatory publications updated daily/hourly

    def __init__(
        self,
        isins: Optional[List[str]] = None,
        tickers: Optional[List[str]] = None,
        interval_seconds: int = 3600,
    ) -> None:
        self.interval_seconds = interval_seconds
        self.scraper = AmfShortScraper()
        self.figi = FigiMapper() if FigiMapper is not None else None
        self.isins = isins or []
        self.tickers = tickers or []

        # If empty, populate with standard French blue chips
        if not self.isins and not self.tickers:
            self.tickers = ["MC.PA", "OR.PA", "TTE.PA", "SAN.PA", "AIR.PA", "AI.PA", "BNP.PA", "KER.PA"]

    def _resolve_isin(self, ticker_or_isin: str) -> str:
        """Resolve ticker to ISIN if needed."""
        if ticker_or_isin.startswith("FR") and len(ticker_or_isin) == 12:
            return ticker_or_isin
        if self.figi is not None:
            try:
                isin = self.figi.ticker_to_isin(ticker_or_isin)
                if isin:
                    return isin
            except Exception as exc:
                logger.debug("FIGI resolution failed for %s: %s", ticker_or_isin, exc)
        return ticker_or_isin

    async def fetch(self) -> List[AlternativeSignal]:
        """Poll short interest for configured assets and return normalized AlternativeSignals."""
        loop = asyncio.get_event_loop()
        signals: List[AlternativeSignal] = []

        targets = list(self.isins)
        for t in self.tickers:
            isin = self._resolve_isin(t)
            if isin not in targets:
                targets.append((t, isin) if t != isin else isin)

        for item in targets:
            if isinstance(item, tuple):
                ticker, isin = item
            else:
                ticker, isin = item, item

            try:
                short_pct = await loop.run_in_executor(None, self.scraper.get_short_interest, isin)
                signals.append(
                    AlternativeSignal(
                        ticker=ticker,
                        signal_type="SHORT_INTEREST",
                        value=float(short_pct),
                        confidence=1.0,
                        source="AMF_BDIF",
                        metadata={"isin": isin, "threshold_breach": short_pct > 3.0},
                    )
                )
            except Exception as exc:
                logger.warning("Failed to fetch AMF short interest for %s (%s): %s", ticker, isin, exc)

        logger.info("AmfShortAdapter emitted %d AlternativeSignal(s).", len(signals))
        return signals
