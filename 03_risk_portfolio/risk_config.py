"""Pydantic validation schema for risk_params.yaml.

Strictly enforces types, value ranges, and forbids unexpected extra keys (extra='forbid', frozen=True)
to prevent silent configuration bugs at boot time.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)


class RiskParamsConfig(BaseModel):
    """Institutional risk configuration with strict compile-time validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Position Sizing
    KELLY_FRACTION: float = Field(default=0.5, ge=0.05, le=1.0)
    MAX_SINGLE_POSITION_PCT: float = Field(default=0.15, ge=0.01, le=0.50)
    MAX_SECTOR_WEIGHT_PCT: float = Field(default=0.25, ge=0.05, le=0.60)
    MAX_ALLOCATION_PER_DAY_PCT: float = Field(default=0.03, ge=0.005, le=0.20)

    # Circuit Breakers & Loss Limits
    DAILY_MAX_LOSS_PCT: float = Field(default=-0.005, le=0.0, ge=-0.10)
    WEEKLY_MAX_LOSS_PCT: float = Field(default=-0.02, le=0.0, ge=-0.25)
    MONTHLY_MAX_LOSS_PCT: float = Field(default=-0.05, le=0.0, ge=-0.40)

    # Correlation Limits
    MAX_CORRELATION_TO_PORTFOLIO: float = Field(default=0.70, ge=0.1, le=1.0)
    MAX_CORRELATION_SAME_SECTOR: float = Field(default=0.80, ge=0.1, le=1.0)
    CORRELATION_LOOKBACK_DAYS: int = Field(default=60, ge=10, le=252)

    # Signals & Constraints
    SIGNAL_BUY_THRESHOLD: float = Field(default=75.0, ge=40.0, le=100.0)
    SIGNAL_SELL_THRESHOLD: float = Field(default=35.0, ge=0.0, le=60.0)
    SIGNAL_VALIDITY_HOURS: int = Field(default=12, ge=1, le=72)
    MACRO_VETO_DAYS_BEFORE: int = Field(default=3, ge=0, le=14)
    EARNINGS_BLACKOUT_DAYS: int = Field(default=2, ge=0, le=14)
    RSI_OVERSOLD_THRESHOLD: float = Field(default=30.0, ge=10.0, le=45.0)
    MIN_LIQUIDITY_ADV: float = Field(default=50000.0, ge=0.0)
    MAX_POSITIONS_TOTAL: int = Field(default=12, ge=1, le=50)

    # Exits
    PROFIT_TARGET_PCT: float = Field(default=0.10, ge=0.01)
    STOP_LOSS_PCT: float = Field(default=-0.05, le=0.0)

    # Core / Satellite Model
    CORE_TICKER: str = Field(default="CW8.PA")
    CORE_TARGET_PCT: float = Field(default=0.70, ge=0.30, le=0.95)
    CORE_CRASH_TARGET_PCT: float = Field(default=0.75, ge=0.30, le=0.95)
    CORE_DCA_MAX_TRANCHE_PCT: float = Field(default=0.05, ge=0.01, le=0.20)
    SATELLITE_MAX_BUDGET_PCT: float = Field(default=0.30, ge=0.05, le=0.70)

    # Volatility & VIX Defense
    VOLATILITY_REFERENCE: float = Field(default=0.20, ge=0.05, le=0.50)
    VOLATILITY_MAX_FACTOR: float = Field(default=1.5, ge=1.0, le=3.0)
    VIX_PANIC_THRESHOLD: float = Field(default=30.0, ge=15.0, le=60.0)

    # Rebalancing
    REBALANCE_PROFIT_SHAVE_PCT: float = Field(default=0.20, ge=0.05, le=0.50)
    REBALANCE_PROFIT_TRIGGER_PCT: float = Field(default=20.0, ge=5.0, le=100.0)
    REBALANCE_ATR_STOP_MULT: float = Field(default=2.5, ge=1.0, le=5.0)


def load_and_validate_risk_params(path: str | Path) -> RiskParamsConfig:
    """Load YAML risk configuration and strictly validate against RiskParamsConfig.

    Raises:
        ValidationError: If any key is misspelled, extra, or value is out of bounds.
        FileNotFoundError: If the YAML file does not exist.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Risk params file not found: {p}")

    with open(p, "r", encoding="utf-8") as fh:
        raw_data = yaml.safe_load(fh) or {}

    try:
        config = RiskParamsConfig(**raw_data)
        logger.info("Risk parameters validated successfully from %s", p)
        return config
    except ValidationError as exc:
        logger.critical("FATAL: risk_params.yaml failed Pydantic validation: %s", exc)
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    root = Path(__file__).resolve().parent.parent
    cfg = load_and_validate_risk_params(root / "config" / "risk_params.yaml")
    print("Risk config loaded and validated:", cfg.model_dump())
