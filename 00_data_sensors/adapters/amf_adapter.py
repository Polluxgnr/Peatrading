"""AMF Regulatory Ingestion Adapters for Layer 1.

Polls the Autorité des Marchés Financiers (AMF) BDIF portal for Net Short Positions
and Dirigeants / Insider Transactions, converting raw records into strict Pydantic AlternativeSignals.
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

try:
    from adapters.base_adapters import AbstractPollAdapter
except ImportError:
    try:
        from .base_adapters import AbstractPollAdapter
    except ImportError:
        from base_adapters import AbstractPollAdapter

from data_contracts import AlternativeSignal


try:
    from amf_short_scraper import AmfShortScraper
except ImportError:
    from scrapers.amf_short_scraper import AmfShortScraper

try:
    from amf_scraper import AmfInsiderScraper
except ImportError:
    from scrapers.amf_scraper import AmfInsiderScraper

try:
    from figi_mapper import FigiMapper
except ImportError:
    FigiMapper = None

logger = logging.getLogger("amf_adapter")

_DEFAULT_PEA_TICKERS = ["MC.PA", "OR.PA", "TTE.PA", "SAN.PA", "AIR.PA", "AI.PA", "BNP.PA", "KER.PA"]


class AmfShortAdapter(AbstractPollAdapter):
    """Adapter polling AMF net short positions."""

    interval_seconds: int = 3600

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

        if not self.isins and not self.tickers:
            self.tickers = list(_DEFAULT_PEA_TICKERS)

    def _resolve_isin(self, ticker_or_isin: str) -> str:
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


class AmfInsiderAdapter(AbstractPollAdapter):
    """Adapter polling AMF official declarations of directors and executives."""

    interval_seconds: int = 7200

    def __init__(
        self,
        tickers: Optional[List[str]] = None,
        interval_seconds: int = 7200,
    ) -> None:
        self.interval_seconds = interval_seconds
        self.scraper = AmfInsiderScraper() if AmfInsiderScraper is not None else None
        self.tickers = tickers or list(_DEFAULT_PEA_TICKERS)

    async def fetch(self) -> List[AlternativeSignal]:
        """Poll insider filings and compute direction scores."""
        if self.scraper is None:
            return []

        loop = asyncio.get_event_loop()
        signals: List[AlternativeSignal] = []

        for ticker in self.tickers:
            try:
                df = await loop.run_in_executor(None, self.scraper.get_recent_declarations, ticker)
                if df is not None and not df.empty:
                    tx_col = next((c for c in ("Transaction", "Title", "type") if c in df.columns), None)
                    buys, sells = 0, 0
                    if tx_col:
                        tx_series = df[tx_col].astype(str).str.lower()
                        buys = int(tx_series.str.contains("acqui|achat|buy|souscription").sum())
                        sells = int(tx_series.str.contains("cess|vente|sell|dispos").sum())

                    direction = 1.0 if buys > sells else (-1.0 if sells > buys else 0.0)
                    signals.append(
                        AlternativeSignal(
                            ticker=ticker,
                            signal_type="INSIDER_TX",
                            value=direction,
                            confidence=1.0,
                            source="AMF_BDIF",
                            metadata={
                                "declarations_count": len(df),
                                "buys_count": buys,
                                "sells_count": sells,
                                "latest_date": str(df["Date"].iloc[0]) if "Date" in df.columns else "",
                            },
                        )
                    )
            except Exception as exc:
                logger.warning("AmfInsiderAdapter failed for %s: %s", ticker, exc)

        logger.info("AmfInsiderAdapter emitted %d AlternativeSignal(s).", len(signals))
        return signals


class AmfAdapter(AbstractPollAdapter):
    """Unified AMF regulatory data adapter polling both short interest and insider filings."""

    interval_seconds: int = 3600

    def __init__(
        self,
        isins: Optional[List[str]] = None,
        tickers: Optional[List[str]] = None,
        interval_seconds: int = 3600,
    ) -> None:
        self.interval_seconds = interval_seconds
        self.short_adapter = AmfShortAdapter(isins=isins, tickers=tickers, interval_seconds=interval_seconds)
        self.insider_adapter = AmfInsiderAdapter(tickers=tickers, interval_seconds=interval_seconds)

    async def fetch(self) -> List[AlternativeSignal]:
        """Fetch both short interest and insider filings from AMF BDIF."""
        short_sigs = await self.short_adapter.fetch()
        insider_sigs = await self.insider_adapter.fetch()
        return short_sigs + insider_sigs

