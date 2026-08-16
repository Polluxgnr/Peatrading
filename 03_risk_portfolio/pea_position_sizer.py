"""PEA position sizer for PEA Sniper Terminal V-Prime.

Converts an approved signal into an integer number of shares, respecting the
PEA's no-fractional-shares rule, the per-position cap, Half-Kelly scaling by
conviction score, and available cash.

Read-only layer: reads ``PortfolioState`` and YAML config. It never writes to
any database; it only computes an integer quantity for the caller to apply.
"""

import logging
import math
import os
import sys
from pathlib import Path

import yaml

_CORE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "01_memory_core"
)
sys.path.insert(0, _CORE_DIR)

from data_models import PortfolioState, Signal  # noqa: E402

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_DIR = _PROJECT_ROOT / "config"


class PeaSizer:
    """Computes integer share quantities under PEA constraints.

    Attributes:
        kelly_fraction: Fraction of full Kelly to apply (e.g. 0.5 = Half-Kelly).
        max_single_position: Max fraction of equity for a single position.
    """

    def __init__(self, config_path: str | Path | None = None) -> None:
        """Load sizing parameters from ``risk_params.yaml``.

        Args:
            config_path: Path to the ``config`` directory (or a risk_params
                YAML file). Defaults to ``<project_root>/config``.
        """
        risk = self._load_risk_params(config_path)
        self.kelly_fraction: float = float(risk["KELLY_FRACTION"])
        self.max_single_position: float = float(risk["MAX_SINGLE_POSITION_PCT"])
        # Core/Satellite + volatility-parity parameters (Phase 10).
        self.core_ticker: str = str(risk.get("CORE_TICKER", "CW8.PA"))
        self.satellite_max_budget: float = float(
            risk.get("SATELLITE_MAX_BUDGET_PCT", 0.30)
        )
        self.max_sector_weight: float = float(risk.get("MAX_SECTOR_WEIGHT_PCT", 0.25))
        self.vol_reference: float = float(risk.get("VOLATILITY_REFERENCE", 0.20))
        self.vol_max_factor: float = float(risk.get("VOLATILITY_MAX_FACTOR", 1.5))
        self.permanent_cash_buffer: float = float(risk.get("PERMANENT_CASH_BUFFER_PCT", 0.02))
        logger.debug(
            "Sizer loaded: kelly=%.2f max_single=%.2f sat_budget=%.2f vol_ref=%.2f max_sector=%.2f cash_buffer=%.2f",
            self.kelly_fraction,
            self.max_single_position,
            self.satellite_max_budget,
            self.vol_reference,
            self.max_sector_weight,
            self.permanent_cash_buffer,
        )


    @staticmethod
    def _load_risk_params(config_path: str | Path | None) -> dict:
        """Resolve and load the risk_params YAML into a dict."""
        if config_path is None:
            path = _DEFAULT_CONFIG_DIR / "risk_params.yaml"
        else:
            p = Path(config_path)
            path = p if p.is_file() else p / "risk_params.yaml"
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    def _satellite_value(self, portfolio: PortfolioState) -> float:
        """Sum the market value of all non-core (satellite) holdings."""
        return sum(
            pos.market_value
            for pos in portfolio.positions
            if pos.ticker != self.core_ticker
        )

    def _volatility_factor(self, historical_volatility: float | None) -> float:
        """Return an inverse-volatility scaling factor.

        Uses volatility parity relative to ``VOLATILITY_REFERENCE``: an asset at
        the reference vol scales by 1.0, one at twice the reference by 0.5, and
        a very calm asset is capped at ``VOLATILITY_MAX_FACTOR``.

        Args:
            historical_volatility: Annualized stdev of returns (e.g. 0.25), or
                ``None``/non-positive for neutral (no scaling).

        Returns:
            float: Multiplier applied to the base target cash.
        """
        if historical_volatility is None or historical_volatility <= 0:
            return 1.0
        factor = self.vol_reference / historical_volatility
        return float(max(0.1, min(self.vol_max_factor, factor)))

    def calculate_sector_scale(
        self,
        ticker_sector: str,
        portfolio: PortfolioState,
        proposed_notional: float,
    ) -> float:
        """Proportional sector scaler to keep sector strictly under MAX_SECTOR_WEIGHT_PCT.

        If buying an asset would push the sector to 32% while max is 25%,
        returns scale = 25.0 / 32.0 (0.781) instead of binary trade veto.

        Args:
            ticker_sector: Sector name of candidate ticker.
            portfolio: Current portfolio state.
            proposed_notional: Intended cash allocation.

        Returns:
            float: Scale multiplier [0.0..1.0].
        """
        if portfolio.total_equity <= 0 or not ticker_sector or ticker_sector == "UNKNOWN":
            return 1.0

        current_sector_val = sum(
            p.market_value
            for p in portfolio.positions
            if (p.sector or "").casefold() == ticker_sector.casefold()
        )
        projected_sector_val = current_sector_val + proposed_notional
        projected_weight = projected_sector_val / portfolio.total_equity

        if projected_weight > self.max_sector_weight and projected_weight > 0:
            scale = self.max_sector_weight / projected_weight
            logger.info(
                "Proportional Sector Rescaling for sector '%s': projected %.1f%% > max %.1f%% -> scale=%.3f",
                ticker_sector,
                projected_weight * 100,
                self.max_sector_weight * 100,
                scale,
            )
            return float(max(0.0, min(1.0, scale)))

        return 1.0

    def size_with_explanation(
        self,
        signal: Signal,
        portfolio: PortfolioState,
        current_price: float,
        historical_volatility: float | None = None,
        ticker_sector: str = "UNKNOWN",
        kinetic_multiplier: float = 1.0,
        attack_budget_pct: float | None = None,
    ) -> tuple[int, dict]:
        """Return ``(qty, meta)`` so UIs can show the sizing reasoning.

        Meta keys: kelly_fraction, score, historical_volatility, vol_factor,
        max_alloc, target_cash_pre_cap, target_cash, notional, weight_pct,
        satellite_room, cash_capped, sector_scale, kinetic_multiplier, max_exposure_room.
        """
        meta: dict = {
            "kelly_fraction": self.kelly_fraction,
            "score": float(signal.score),
            "historical_volatility": historical_volatility,
            "vol_factor": 1.0,
            "max_alloc": 0.0,
            "target_cash_pre_cap": 0.0,
            "target_cash": 0.0,
            "notional": 0.0,
            "weight_pct": 0.0,
            "satellite_room": 0.0,
            "cash_capped": False,
            "sector_scale": 1.0,
            "kinetic_multiplier": kinetic_multiplier,
            "max_exposure_room": 0.0,
        }
        if current_price <= 0 or portfolio.total_equity <= 0 or kinetic_multiplier <= 0.0:
            logger.warning(
                "Sizing %s to 0 (price=%.4f equity=%.2f kinetic=%.2f).",
                signal.ticker, current_price, portfolio.total_equity, kinetic_multiplier,
            )
            return 0, meta

        max_alloc = portfolio.total_equity * self.max_single_position
        target_cash = max_alloc * (signal.score / 100.0) * self.kelly_fraction
        vol_factor = self._volatility_factor(historical_volatility)
        target_cash *= vol_factor

        # Apply Kinetic Brake multiplier
        target_cash *= max(0.0, min(1.0, float(kinetic_multiplier)))

        # Apply Proportional Sector Rescaling
        sec_scale = self.calculate_sector_scale(ticker_sector, portfolio, target_cash)
        target_cash *= sec_scale

        meta.update({
            "vol_factor": vol_factor,
            "max_alloc": max_alloc,
            "target_cash_pre_cap": target_cash,
            "sector_scale": sec_scale,
        })

        # Strict 98% Max Exposure Limit (2% Permanent Cash Buffer)
        invested_equity = sum(p.market_value for p in portfolio.positions)
        max_exposure_cap = portfolio.total_equity * (1.0 - self.permanent_cash_buffer)
        max_exposure_room = max(0.0, max_exposure_cap - invested_equity)

        satellite_room = max(
            0.0,
            self.satellite_budget_room(portfolio),
        )

        # Dynamic Attack Budget cap from Volatility Thermometer
        if attack_budget_pct is not None:
            max_attack_equity = portfolio.total_equity * min(1.0 - self.permanent_cash_buffer, max(0.0, float(attack_budget_pct)))
            current_sat = self._satellite_value(portfolio)
            attack_room = max(0.0, max_attack_equity - current_sat)
            satellite_room = min(satellite_room, attack_room)

        satellite_room = min(satellite_room, max_exposure_room)
        meta["satellite_room"] = satellite_room
        meta["max_exposure_room"] = max_exposure_room

        if target_cash > satellite_room:
            logger.info(
                "%s sizing capped by satellite/exposure budget: %.2f -> %.2f EUR.",
                signal.ticker, target_cash, satellite_room,
            )
            target_cash = satellite_room

        max_usable_cash = max(0.0, min(portfolio.cash_available, max_exposure_room))
        qty_shares = math.floor(target_cash / current_price)
        notional = qty_shares * current_price
        if notional > max_usable_cash:
            qty_shares = math.floor(max_usable_cash / current_price)
            notional = qty_shares * current_price
            meta["cash_capped"] = True
            logger.info(
                "%s sizing capped by usable cash (under 98%% exposure limit) -> %d shares.",
                signal.ticker, qty_shares,
            )
        else:
            logger.info(
                "%s sized to %d shares (target=%.2f @ %.2f, score=%.1f, vol_f=%.2f, sec_scale=%.2f).",
                signal.ticker, qty_shares, target_cash, current_price,
                signal.score, vol_factor, sec_scale,
            )


        qty_shares = max(0, qty_shares)
        notional = qty_shares * current_price
        meta["target_cash"] = target_cash
        meta["notional"] = notional
        meta["weight_pct"] = (
            (notional / portfolio.total_equity * 100.0)
            if portfolio.total_equity else 0.0
        )
        return qty_shares, meta

    def satellite_budget_room(self, portfolio: PortfolioState) -> float:
        """EUR room left under the satellite budget cap."""
        return (
            self.satellite_max_budget * portfolio.total_equity
            - self._satellite_value(portfolio)
        )

    def calculate_target_qty(
        self,
        signal: Signal,
        portfolio: PortfolioState,
        current_price: float,
        historical_volatility: float | None = None,
    ) -> int:
        """Compute the integer share quantity for a satellite signal.

        See ``size_with_explanation`` for the full breakdown (dashboard cards).
        """
        qty, _meta = self.size_with_explanation(
            signal, portfolio, current_price, historical_volatility
        )
        return qty


if __name__ == "__main__":
    from datetime import datetime, timezone

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    sizer = PeaSizer()

    portfolio = PortfolioState(
        cash_available=8000.0,
        total_equity=15000.0,
        positions=[],
        last_updated=datetime.now(timezone.utc),
    )

    print("--- Normal sizing (score 80) ---")
    sig = Signal(ticker="MC.PA", signal_type="BUY", score=80.0)
    # max_alloc = 15000 * 0.15 = 2250 ; target = 2250 * 0.80 * 0.5 = 900 EUR
    qty = sizer.calculate_target_qty(sig, portfolio, current_price=600.0)
    print(f"MC.PA @600 EUR -> {qty} shares (expected floor(900/600)=1)")

    print("\n--- Score 100 sizing ---")
    sig2 = Signal(ticker="AI.PA", signal_type="BUY", score=100.0)
    # target = 2250 * 1.0 * 0.5 = 1125 EUR ; floor(1125/180)=6
    qty2 = sizer.calculate_target_qty(sig2, portfolio, current_price=180.0)
    print(f"AI.PA @180 EUR -> {qty2} shares (expected floor(1125/180)=6)")

    print("\n--- Cash-constrained sizing ---")
    poor = PortfolioState(cash_available=300.0, total_equity=15000.0,
                          positions=[], last_updated=datetime.now(timezone.utc))
    sig3 = Signal(ticker="ASML.AS", signal_type="BUY", score=100.0)
    # target ~1125 EUR but only 300 cash ; floor(300/180)=1
    qty3 = sizer.calculate_target_qty(sig3, poor, current_price=180.0)
    print(f"ASML.AS @180 EUR, cash 300 -> {qty3} shares (expected 1)")
