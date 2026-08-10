"""Strict data contracts for PEA Sniper Terminal V-Prime.

This module defines the Pydantic V2 models that flow between every layer of the
system (data sensors -> quant engine -> risk portfolio -> orchestrator ->
interfaces). Validating objects at module boundaries prevents malformed data
from ever reaching the risk or execution logic.

No trading logic, API calls, or database code lives here by design.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field


def _utcnow() -> datetime:
    """Return the current timezone-aware UTC timestamp.

    Returns:
        datetime: The current time in UTC.
    """
    return datetime.now(timezone.utc)


class SignalType(str, Enum):
    """Direction of a trading signal."""

    BUY = "BUY"
    SELL = "SELL"


class SignalStatus(str, Enum):
    """Lifecycle state of a signal as it moves through the orchestrator."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    EXECUTED = "EXECUTED"


class MarketRegime(str, Enum):
    """Coarse classification of the prevailing market environment."""

    BULL = "BULL"
    BEAR = "BEAR"
    CHOPPY = "CHOPPY"
    VOLATILE = "VOLATILE"


class Position(BaseModel):
    """A single open holding in the PEA portfolio.

    Attributes:
        ticker: Yahoo Finance ticker symbol (e.g. ``MC.PA``).
        qty_shares: Number of whole shares held. PEA forbids fractional shares.
        avg_entry_price: Volume-weighted average entry price in EUR.
        current_price: Latest known market price in EUR.
        sector: Sector bucket used by the correlation firewall.
    """

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    ticker: str = Field(..., min_length=1, description="Yahoo Finance ticker.")
    qty_shares: int = Field(..., ge=0, description="Whole shares (no fractions).")
    avg_entry_price: float = Field(..., gt=0, description="Avg entry price (EUR).")
    current_price: float = Field(..., gt=0, description="Latest price (EUR).")
    sector: str = Field(..., min_length=1, description="Sector classification.")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def market_value(self) -> float:
        """Current market value of the position in EUR.

        Returns:
            float: ``current_price * qty_shares``.
        """
        return self.current_price * self.qty_shares

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unrealized_pnl_pct(self) -> float:
        """Unrealized profit/loss as a fraction of the entry price.

        Returns:
            float: ``(current_price - avg_entry_price) / avg_entry_price``.
                A value of ``0.10`` represents a +10% unrealized gain.
        """
        return (self.current_price - self.avg_entry_price) / self.avg_entry_price


class PortfolioState(BaseModel):
    """Snapshot of the full portfolio at a point in time.

    Attributes:
        cash_available: Uninvested cash in EUR.
        total_equity: Total account value (cash + positions market value) in EUR.
        positions: List of currently open positions.
        last_updated: Timestamp of this snapshot (UTC).
    """

    model_config = ConfigDict(validate_assignment=True)

    cash_available: float = Field(..., ge=0, description="Uninvested cash (EUR).")
    total_equity: float = Field(..., ge=0, description="Total account value (EUR).")
    positions: List[Position] = Field(default_factory=list)
    last_updated: datetime = Field(default_factory=_utcnow)

    def get_sector_weight(self, sector_name: str) -> float:
        """Compute the fraction of total equity allocated to a sector.

        Args:
            sector_name: Sector to measure (case-insensitive match).

        Returns:
            float: Sector market value divided by ``total_equity``. Returns
                ``0.0`` when total equity is zero to avoid division errors.
        """
        if self.total_equity <= 0:
            return 0.0
        sector_value = sum(
            pos.market_value
            for pos in self.positions
            if pos.sector.casefold() == sector_name.casefold()
        )
        return sector_value / self.total_equity


class Signal(BaseModel):
    """A candidate trade produced by the quant engine.

    LLMs never create these; they are generated purely from mathematical
    conditions and only explained downstream in the interface layer.

    Attributes:
        id: Unique identifier (UUID4 hex string).
        ticker: Yahoo Finance ticker the signal refers to.
        signal_type: BUY or SELL.
        status: Current lifecycle state (defaults to PENDING).
        score: Composite conviction score from 0 to 100.
        target_qty: Whole-share quantity, set later by the position sizer.
        created_at: Emission timestamp (UTC).
        reason: Human-readable explanation surfaced in the UI.
    """

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    id: str = Field(default_factory=lambda: uuid4().hex, description="UUID4 id.")
    ticker: str = Field(..., min_length=1, description="Target ticker.")
    signal_type: SignalType = Field(..., description="BUY or SELL.")
    status: SignalStatus = Field(default=SignalStatus.PENDING)
    score: float = Field(..., ge=0, le=100, description="Conviction score 0-100.")
    target_qty: Optional[int] = Field(
        default=None, ge=0, description="Whole shares set after sizing."
    )
    created_at: datetime = Field(default_factory=_utcnow)
    reason: str = Field(default="", description="Explanation for the UI.")
