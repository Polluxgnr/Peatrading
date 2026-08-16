"""Fundamentals Ingestion Adapter for PEA Pollux.

Polls financial statements via FMP and yfinance to calculate 9-point Piotroski F-Scores,
emitting strictly typed AlternativeSignals into the Data Ingestion Hub.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import List, Optional

_ROOT = Path(__file__).resolve().parent.parent.parent
for d in ("00_data_sensors", "00_data_sensors/adapters", "01_memory_core"):
    sys.path.insert(0, str(_ROOT / d))

try:
    from adapters.base_adapters import AbstractPollAdapter
except ImportError:
    try:
        from .base_adapters import AbstractPollAdapter
    except ImportError:
        from base_adapters import AbstractPollAdapter

from data_contracts import AlternativeSignal
from fundamentals_api import FundamentalsSensor

logger = logging.getLogger("fundamentals_adapter")

_DEFAULT_PEA_UNIVERSE = ["MC.PA", "OR.PA", "TTE.PA", "SAN.PA", "AIR.PA", "AI.PA", "BNP.PA", "KER.PA"]


class FmpFundamentalsAdapter(AbstractPollAdapter):
    """Adapter polling financial statements to compute Piotroski F-Scores."""

    interval_seconds: int = 86400  # Daily / fundamental refresh

    def __init__(
        self,
        tickers: Optional[List[str]] = None,
        interval_seconds: int = 86400,
    ) -> None:
        self.interval_seconds = interval_seconds
        self.tickers = tickers or list(_DEFAULT_PEA_UNIVERSE)
        self.sensor = FundamentalsSensor()

    async def fetch(self) -> List[AlternativeSignal]:
        """Fetch fundamental data and compute Piotroski F-Score signals for configured tickers."""
        loop = asyncio.get_event_loop()
        signals: List[AlternativeSignal] = []

        for ticker in self.tickers:
            try:
                score, breakdown = await loop.run_in_executor(
                    None, self.sensor.calculate_piotroski_score, ticker
                )
                signals.append(
                    AlternativeSignal(
                        ticker=ticker,
                        signal_type="FUNDAMENTAL_PIOTROSKI",
                        value=float(score),
                        confidence=1.0,
                        source="FMP/YF",
                        metadata={
                            "piotroski_score": int(score),
                            "is_pass": score >= 4,
                            "breakdown": breakdown,
                        },
                    )
                )
            except Exception as exc:
                logger.warning("FmpFundamentalsAdapter failed for %s: %s", ticker, exc)

        logger.info("FmpFundamentalsAdapter emitted %d AlternativeSignal(s).", len(signals))
        return signals
