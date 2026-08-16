"""Layer 1 Standard Data Ingestion Contracts for PEA Pollux.

Provides strict Pydantic V2 data contracts that all data sensors, pollers,
scrapers, and market adapters must emit before persistence or downstream scoring.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc)


class MarketTick(BaseModel):
    """Normalized tick / price update contract across all data sources.

    Attributes:
        ticker: Standardized Euronext / Yahoo symbol (e.g. 'MC.PA').
        ts: UTC timestamp of the quote.
        price: Last traded or closing price (EUR).
        volume: Volume traded on the period / tick.
        source: Data provider identifier (e.g. 'yfinance', 'boursorama').
    """

    model_config = ConfigDict(validate_assignment=True, str_strip_whitespace=True)

    ticker: str = Field(..., min_length=1, description="Standardized Yahoo / Euronext ticker symbol.")
    ts: datetime = Field(default_factory=_utcnow, description="Timestamp of the market tick (UTC).")
    price: float = Field(..., gt=0, description="Last traded or closing price (EUR).")
    volume: float = Field(default=0.0, ge=0, description="Volume traded.")
    source: str = Field(..., min_length=1, description="Data source identifier (e.g., 'yfinance', 'boursorama').")


class AlternativeSignal(BaseModel):
    """Normalized alternative data event contract (sentiment, insiders, short interest, macro).

    Attributes:
        ticker: Associated ticker symbol if company-specific (or None for market-wide).
        ts: UTC timestamp of signal emission or capture.
        signal_type: Category of the signal (e.g. 'sentiment', 'insider_buy', 'short_interest', 'macro').
        value: Numeric value of the metric or indicator score.
        confidence: Confidence score from 0.0 to 1.0.
        source: Adapter or source name (e.g. 'finbert', 'amf_bdif', 'openinsider', 'ecb_sdw').
        metadata: Unstructured payload or auxiliary dictionary.
    """

    model_config = ConfigDict(validate_assignment=True, str_strip_whitespace=True)

    ticker: Optional[str] = Field(default=None, description="Associated ticker symbol if company-specific.")
    ts: datetime = Field(default_factory=_utcnow, description="Timestamp of the signal emission or capture (UTC).")
    signal_type: str = Field(..., min_length=1, description="Category of the signal (e.g., 'sentiment', 'insider_buy', 'short_interest').")
    value: float = Field(..., description="Numeric value of the signal or metric.")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence score from 0.0 to 1.0.")
    source: str = Field(..., min_length=1, description="Adapter or source name (e.g., 'finbert', 'amf_bdif', 'openinsider').")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional unstructured payload or metadata dictionary.")
