"""Macro Alpha Adapter for Layer 1 Ingestion.

Polls European volatility (VSTOXX / V2TX / VIX) and ECB 10Y OAT-Bund sovereign spreads,
emitting normalized AlternativeSignal objects.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import List

_ROOT = Path(__file__).resolve().parent.parent.parent
for sub in ("00_data_sensors", "01_memory_core"):
    sys.path.insert(0, str(_ROOT / sub))

from base_adapters import AbstractPollAdapter
from data_contracts import AlternativeSignal
from macro_alpha_api import MacroAlphaSensor

logger = logging.getLogger("macro_adapter")


class MacroAlphaAdapter(AbstractPollAdapter):
    """Adapter polling European VIX and ECB sovereign spreads."""

    interval_seconds: int = 900  # 15-minute polling

    def __init__(self, interval_seconds: int = 900) -> None:
        self.interval_seconds = interval_seconds
        self.sensor = MacroAlphaSensor()

    async def fetch(self) -> List[AlternativeSignal]:
        """Fetch live European VIX and 10Y OAT-Bund yield spread concurrently."""
        loop = asyncio.get_event_loop()
        signals: List[AlternativeSignal] = []

        try:
            vix_val = await loop.run_in_executor(None, self.sensor.get_european_vix)
            signals.append(
                AlternativeSignal(
                    ticker="MARCHE",
                    signal_type="MACRO_VIX",
                    value=float(vix_val),
                    confidence=1.0,
                    source="Yahoo/ECB",
                    metadata={"index": "^V2TX", "description": "European Volatility Index"},
                )
            )
        except Exception as exc:
            logger.warning("MacroAlphaAdapter failed to fetch European VIX: %s", exc)

        try:
            spread_val = await loop.run_in_executor(None, self.sensor.get_oat_bund_spread)
            signals.append(
                AlternativeSignal(
                    ticker="MARCHE",
                    signal_type="MACRO_SPREAD",
                    value=float(spread_val),
                    confidence=1.0,
                    source="Yahoo/ECB",
                    metadata={"unit": "bps", "benchmark": "10Y_OAT_BUND", "description": "10Y OAT vs Bund Spread"},
                )
            )
        except Exception as exc:
            logger.warning("MacroAlphaAdapter failed to fetch OAT-Bund spread: %s", exc)

        logger.info("MacroAlphaAdapter emitted %d AlternativeSignal(s).", len(signals))
        return signals
