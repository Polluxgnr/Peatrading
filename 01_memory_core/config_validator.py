"""Strict Pydantic validation for ``risk_params.yaml``.

Every key in the YAML must be declared here. Unknown keys raise on load so
config/code drift cannot hide silently.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_RISK_PATH = _PROJECT_ROOT / "config" / "risk_params.yaml"


class RiskParamsConfig(BaseModel):
    """Institutional risk parameters — single source of truth."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Position sizing
    KELLY_FRACTION: float = Field(0.5, gt=0, le=1)
    MAX_SINGLE_POSITION_PCT: float = Field(0.15, gt=0, le=1)
    MAX_SECTOR_WEIGHT_PCT: float = Field(0.25, gt=0, le=1)
    MAX_ALLOCATION_PER_DAY_PCT: float = Field(0.03, gt=0, le=1)

    # Circuit breakers
    DAILY_MAX_LOSS_PCT: float = Field(-0.005, lt=0)
    WEEKLY_MAX_LOSS_PCT: float = Field(-0.02, lt=0)
    MONTHLY_MAX_LOSS_PCT: float = Field(-0.05, lt=0)

    # Correlation
    MAX_CORRELATION_TO_PORTFOLIO: float = Field(0.70, gt=0, le=1)
    MAX_CORRELATION_SAME_SECTOR: float = Field(0.80, gt=0, le=1)
    CORRELATION_LOOKBACK_DAYS: int = Field(60, ge=10, le=500)

    # Signals
    CONVICTION_EMIT_FLOOR: float = Field(65.0, ge=0, le=100)
    SIGNAL_SELL_THRESHOLD: float = Field(35.0, ge=0, le=100)
    SIGNAL_VALIDITY_HOURS: int = Field(12, ge=1, le=168)
    MACRO_VETO_DAYS_BEFORE: int = Field(3, ge=0, le=30)
    EARNINGS_BLACKOUT_DAYS: int = Field(2, ge=0, le=30)
    RSI_OVERSOLD_THRESHOLD: float = Field(30.0, gt=0, lt=100)
    MIN_LIQUIDITY_ADV: float = Field(50_000, ge=0)
    MAX_POSITIONS_TOTAL: int = Field(12, ge=1, le=100)

    # Core / satellite
    CORE_TICKER: str = Field("CW8.PA", min_length=1)
    MAX_IDLE_CASH_PCT: float = Field(0.02, ge=0, le=1)
    CORE_TARGET_PCT: float = Field(0.70, ge=0, le=1)
    CORE_CRASH_TARGET_PCT: float = Field(0.75, ge=0, le=1)
    CORE_DCA_MAX_TRANCHE_PCT: float = Field(0.05, gt=0, le=1)
    SATELLITE_MAX_BUDGET_PCT: float = Field(0.30, ge=0, le=1)

    # Volatility / VIX
    VOLATILITY_REFERENCE: float = Field(0.20, gt=0)
    VOLATILITY_MAX_FACTOR: float = Field(1.5, gt=0)
    VIX_PANIC_THRESHOLD: float = Field(30.0, gt=0)

    # Rebalancing / exits
    REBALANCE_PROFIT_SHAVE_PCT: float = Field(0.20, gt=0, le=1)
    REBALANCE_PROFIT_TRIGGER_PCT: float = Field(20.0, gt=0)
    REBALANCE_ATR_STOP_MULT: float = Field(2.5, gt=0)


def _resolve_risk_path(config_path: str | Path | None) -> Path:
    if config_path is None:
        return _DEFAULT_RISK_PATH
    p = Path(config_path)
    if p.is_file():
        return p
    return p / "risk_params.yaml"


def load_risk_config(config_path: str | Path | None = None) -> RiskParamsConfig:
    """Load and validate ``risk_params.yaml``. Crash on malformed config."""
    path = _resolve_risk_path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"risk_params.yaml not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    try:
        return RiskParamsConfig.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(
            f"Invalid risk_params.yaml at {path}:\n{exc}"
        ) from exc


# Module-level singleton — validated once at import for fast access.
try:
    RISK: RiskParamsConfig = load_risk_config()
except (FileNotFoundError, ValueError):
    RISK = None  # type: ignore[assignment]
