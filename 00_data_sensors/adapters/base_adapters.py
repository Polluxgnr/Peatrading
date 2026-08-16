"""Base Abstract Ingestion Adapters for Layer 1.

All future scrapers, polling connectors, and data feeds implement these
interfaces to enforce decoupling between external sources and core quant engines.
"""

from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT / "01_memory_core"))

from data_contracts import AlternativeSignal


class AbstractPollAdapter(ABC):
    """Abstract polling adapter for recurring ingestion of alternative data streams.

    Attributes:
        interval_seconds (int): Minimum interval between polling runs in seconds (default: 900s / 15m).
    """

    interval_seconds: int = 900

    @abstractmethod
    async def fetch(self) -> List[AlternativeSignal]:
        """Poll the remote data provider and return normalized AlternativeSignal objects.

        Returns:
            List[AlternativeSignal]: Standardized signals emitted by this sensor.
        """
        raise NotImplementedError("Subclasses must implement fetch().")


class AbstractMarketDataAdapter(ABC):
    """Abstract market data adapter for price quotes, historical bars, and order book states."""

    @abstractmethod
    async def fetch_ohlcv(self, tickers: List[str], lookback_days: int = 252) -> pd.DataFrame:
        """Fetch daily or intraday OHLCV bars for candidate tickers.

        Args:
            tickers: List of standardized ticker symbols (e.g. ['MC.PA', 'CW8.PA']).
            lookback_days: Number of historical calendar days to request.

        Returns:
            pd.DataFrame: Cleaned dataframe with Open, High, Low, Close, Volume columns.
        """
        raise NotImplementedError("Subclasses must implement fetch_ohlcv().")
