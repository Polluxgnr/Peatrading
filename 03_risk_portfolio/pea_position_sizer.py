"""PEA position sizer for PEA Pollux.

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
from config_validator import load_risk_config  # noqa: E402

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
        risk = load_risk_config(config_path)
        self.kelly_fraction: float = float(risk.KELLY_FRACTION)
        self.max_single_position: float = float(risk.MAX_SINGLE_POSITION_PCT)
        # Core/Satellite + volatility-parity parameters (Phase 10).
        self.core_ticker: str = str(risk.CORE_TICKER)
        self.satellite_max_budget: float = float(risk.SATELLITE_MAX_BUDGET_PCT)
        self.vol_reference: float = float(risk.VOLATILITY_REFERENCE)
        self.vol_max_factor: float = float(risk.VOLATILITY_MAX_FACTOR)
        
        try:
            from stable_baselines3 import PPO
            model_path = _PROJECT_ROOT / "database" / "rl_sizer_model.zip"
            if model_path.exists():
                self.rl_model = PPO.load(str(model_path))
                logger.info("Loaded PPO RL model for dynamic sizing.")
            else:
                self.rl_model = None
        except Exception:
            self.rl_model = None
            
        logger.debug(
            "Sizer loaded: kelly=%.2f max_single=%.2f sat_budget=%.2f vol_ref=%.2f",
            self.kelly_fraction,
            self.max_single_position,
            self.satellite_max_budget,
            self.vol_reference,
        )

    @staticmethod
    def _load_risk_params(config_path: str | Path | None):
        """Resolve and load validated risk config."""
        return load_risk_config(config_path)

    @staticmethod
    def investment_rate(portfolio: PortfolioState) -> float:
        """Calculate the ratio of invested capital to total equity."""
        if portfolio.total_equity <= 0:
            return 0.0
        invested = sum(p.market_value for p in portfolio.positions)
        return invested / portfolio.total_equity

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

    def size_with_explanation(
        self,
        signal: Signal,
        portfolio: PortfolioState,
        current_price: float,
        historical_volatility: float | None = None,
    ) -> tuple[int, dict]:
        """Return ``(qty, meta)`` so UIs can show the sizing reasoning.

        Meta keys: kelly_fraction, score, historical_volatility, vol_factor,
        max_alloc, target_cash_pre_cap, target_cash, notional, weight_pct,
        satellite_room, cash_capped.
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
        }
        if current_price <= 0 or portfolio.total_equity <= 0:
            logger.warning(
                "Sizing %s to 0 (price=%.4f equity=%.2f).",
                signal.ticker, current_price, portfolio.total_equity,
            )
            return 0, meta

        max_alloc = portfolio.total_equity * self.max_single_position
        
        # RL Sizing Path
        if getattr(self, "rl_model", None) is not None and historical_volatility is not None:
            import numpy as np
            obs = np.array([signal.score / 100.0, historical_volatility], dtype=np.float32)
            action, _ = self.rl_model.predict(obs, deterministic=True)
            # Action space [-1, 1] mapped to [0, 1]
            dynamic_kelly = float(np.clip((action[0] + 1.0) / 2.0, 0.0, 1.0))
            meta["kelly_fraction"] = dynamic_kelly
            target_cash = max_alloc * dynamic_kelly
            vol_factor = 1.0  # RL agent learns vol natively
            meta["vol_factor"] = vol_factor
            meta["target_cash_pre_cap"] = target_cash
            meta["max_alloc"] = max_alloc
        else:
            # Traditional Deterministic Path
            target_cash = max_alloc * (signal.score / 100.0) * self.kelly_fraction
            vol_factor = self._volatility_factor(historical_volatility)
            target_cash *= vol_factor
            meta.update({
                "vol_factor": vol_factor,
                "max_alloc": max_alloc,
                "target_cash_pre_cap": target_cash,
            })

        satellite_room = max(
            0.0,
            self.satellite_budget_room(portfolio),
        )
        meta["satellite_room"] = satellite_room
        if target_cash > satellite_room:
            logger.info(
                "%s sizing capped by satellite budget: %.2f -> %.2f EUR.",
                signal.ticker, target_cash, satellite_room,
            )
            target_cash = satellite_room

        qty_shares = math.floor(target_cash / current_price)
        notional = qty_shares * current_price
        if notional > portfolio.cash_available:
            qty_shares = math.floor(portfolio.cash_available / current_price)
            notional = qty_shares * current_price
            meta["cash_capped"] = True
            logger.info(
                "%s sizing capped by cash -> %d shares.",
                signal.ticker, qty_shares,
            )
        else:
            logger.info(
                "%s sized to %d shares (target=%.2f @ %.2f, score=%.1f, vol_f=%.2f).",
                signal.ticker, qty_shares, target_cash, current_price,
                signal.score, vol_factor,
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

    @staticmethod
    def investment_rate(portfolio: PortfolioState) -> float:
        """Percentage of equity currently invested (100 − cash drag)."""
        if portfolio is None or portfolio.total_equity <= 0:
            return 0.0
        invested = sum(float(p.market_value) for p in portfolio.positions)
        return float(max(0.0, min(100.0, invested / portfolio.total_equity * 100.0)))

    @staticmethod
    def idle_cash_pct(portfolio: PortfolioState) -> float:
        """Cash as a percentage of total equity."""
        if portfolio is None or portfolio.total_equity <= 0:
            return 0.0
        return float(
            max(0.0, min(100.0, portfolio.cash_available / portfolio.total_equity * 100.0))
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
