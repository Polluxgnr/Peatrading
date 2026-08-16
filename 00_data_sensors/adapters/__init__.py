"""Layer 1 Ingestion Adapters package for PEA Pollux."""

from .amf_adapter import AmfInsiderAdapter, AmfShortAdapter
from .base_adapters import AbstractMarketDataAdapter, AbstractPollAdapter
from .bourso_adapter import BoursoUniverseAdapter
from .macro_adapter import MacroAlphaAdapter
from .news_adapter import ConsolidatedNewsAdapter

__all__ = [
    "AbstractPollAdapter",
    "AbstractMarketDataAdapter",
    "AmfShortAdapter",
    "AmfInsiderAdapter",
    "ConsolidatedNewsAdapter",
    "BoursoUniverseAdapter",
    "MacroAlphaAdapter",
]
