"""Monthly portfolio rebalancer for PEA Sniper Terminal V-Prime (Phase 12/15).

Adds mechanical, emotionless housekeeping trades so the operator does not have to
babysit winners and losers:

  * Profit shaving: trim a fixed slice of any satellite winner above +20% PnL.
  * Dynamic ATR stop-loss: fully exit any satellite whose price trades below
    ``avg_entry - 2.5 * ATR_14`` (replaces the static -10% rule).

The Core ETF (Smart-DCA accumulation vehicle) is deliberately excluded — it is
meant to be held and averaged into, not shaved or stopped out.

Pure logic: reads a ``PortfolioState`` and config, returns ``SELL`` signals. It
never writes to a database or touches a broker.
"""

import logging
import math
import os
import sys
from pathlib import Path
from typing import Any, List, Optional

import pandas as pd
import yaml

# pandas-ta registers the ``.ta`` DataFrame accessor on import.
try:
    import pandas_ta as ta  # noqa: F401
except ImportError:  # pragma: no cover - classic fork on some envs
    import pandas_ta_classic as ta  # noqa: F401

_CORE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "01_memory_core"
)
sys.path.insert(0, _CORE_DIR)

from data_models import PortfolioState, Signal, SignalStatus, SignalType  # noqa: E402

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_DIR = _PROJECT_ROOT / "config"

# Multiplier applied to ATR_14 for the dynamic stop distance.
_ATR_STOP_MULT = 2.5
_ATR_LENGTH = 14
_OHLCV_LOOKBACK = 60


class PortfolioRebalancer:
    """Generates mechanical SELL signals for profit-taking and ATR stop-losses."""

    def __init__(
        self,
        config_path: str | Path | None = None,
        timeseries_db: Any | None = None,
    ) -> None:
        """Load rebalancing thresholds from ``risk_params.yaml``.

        Args:
            config_path: Path to the ``config`` directory (or a risk_params
                YAML file). Defaults to ``<project_root>/config``.
            timeseries_db: Optional ``TimeSeriesDB`` used to fetch OHLCV for
                ATR-based stop-loss calculation.
        """
        risk = self._load_risk_params(config_path)
        self.timeseries_db = timeseries_db
        self.core_ticker: str = str(risk.get("CORE_TICKER", "CW8.PA"))
        self.profit_trigger: float = float(
            risk.get("REBALANCE_PROFIT_TRIGGER_PCT", 20.0)
        )
        self.profit_shave: float = float(
            risk.get("REBALANCE_PROFIT_SHAVE_PCT", 0.20)
        )
        self.atr_stop_mult: float = float(
            risk.get("REBALANCE_ATR_STOP_MULT", _ATR_STOP_MULT)
        )
        logger.debug(
            "Rebalancer loaded: profit>+%.0f%% shave %.0f%%, ATR stop %.1fx "
            "(core=%s).",
            self.profit_trigger,
            self.profit_shave * 100,
            self.atr_stop_mult,
            self.core_ticker,
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

    def _latest_atr14(self, ticker: str) -> Optional[float]:
        """Return the latest ATR_14 from DuckDB OHLCV, or None if unavailable."""
        if self.timeseries_db is None:
            logger.debug("No timeseries_db; cannot compute ATR for %s.", ticker)
            return None
        try:
            hist = self.timeseries_db.get_historical_prices(
                ticker, days=_OHLCV_LOOKBACK
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to fetch OHLCV for ATR on %s.", ticker)
            return None
        if hist is None or hist.empty or len(hist) < _ATR_LENGTH + 1:
            logger.debug(
                "Insufficient OHLCV for ATR on %s (%s rows).",
                ticker,
                0 if hist is None else len(hist),
            )
            return None
        try:
            work = hist.copy()
            for col in ("Open", "High", "Low", "Close"):
                if col not in work.columns:
                    return None
                work[col] = pd.to_numeric(work[col], errors="coerce")
            atr = work.ta.atr(
                high=work["High"],
                low=work["Low"],
                close=work["Close"],
                length=_ATR_LENGTH,
            )
            if atr is None:
                return None
            if isinstance(atr, pd.DataFrame):
                atr = atr.iloc[:, 0]
            val = float(atr.dropna().iloc[-1])
            if not math.isfinite(val) or val <= 0:
                return None
            return val
        except Exception:  # noqa: BLE001
            logger.exception("ATR_14 calculation failed for %s.", ticker)
            return None

    def generate_rebalance_signals(
        self, portfolio: PortfolioState
    ) -> List[Signal]:
        """Produce mechanical SELL signals from the current portfolio.

        Rules (satellite positions only):
            * ``unrealized_pnl_pct`` > +20% -> SELL 20% of the shares (shave).
            * Losing position (``unrealized_pnl_pct`` < 0) whose
              ``current_price < avg_entry - 2.5 * ATR_14`` -> SELL 100%.

        Args:
            portfolio: Current portfolio snapshot.

        Returns:
            List[Signal]: PENDING SELL signals (empty if nothing triggers).
        """
        signals: List[Signal] = []

        for pos in portfolio.positions:
            if pos.ticker == self.core_ticker:
                continue  # Core ETF is accumulated, never rebalanced out.
            if pos.qty_shares <= 0:
                continue

            pnl_pct = pos.unrealized_pnl_pct * 100.0

            # --- Dynamic ATR stop-loss: full exit on volatility breach ------
            if pnl_pct < 0:
                atr14 = self._latest_atr14(pos.ticker)
                if atr14 is not None:
                    stop_level = pos.avg_entry_price - (
                        self.atr_stop_mult * atr14
                    )
                    if pos.current_price < stop_level:
                        signals.append(
                            Signal(
                                ticker=pos.ticker,
                                signal_type=SignalType.SELL,
                                status=SignalStatus.PENDING,
                                score=100.0,
                                target_qty=pos.qty_shares,
                                reason=(
                                    f"ATR STOP-LOSS: {pos.ticker} at "
                                    f"{pos.current_price:.2f} < "
                                    f"entry {pos.avg_entry_price:.2f} - "
                                    f"{self.atr_stop_mult:.1f}*ATR14 "
                                    f"({atr14:.2f}) = {stop_level:.2f} "
                                    f"(PnL {pnl_pct:+.1f}%). Full exit of "
                                    f"{pos.qty_shares} share(s)."
                                ),
                            )
                        )
                        logger.info(
                            "Rebalance ATR-STOP %s: price=%.2f stop=%.2f "
                            "ATR14=%.2f — sell all %d.",
                            pos.ticker,
                            pos.current_price,
                            stop_level,
                            atr14,
                            pos.qty_shares,
                        )
                        continue

            # --- Profit shaving: trim a slice of the winner ------------------
            if pnl_pct > self.profit_trigger:
                shave_qty = int(math.floor(pos.qty_shares * self.profit_shave))
                if shave_qty < 1:
                    logger.debug(
                        "%s up %.1f%% but too few shares (%d) to shave.",
                        pos.ticker,
                        pnl_pct,
                        pos.qty_shares,
                    )
                    continue
                signals.append(
                    Signal(
                        ticker=pos.ticker,
                        signal_type=SignalType.SELL,
                        status=SignalStatus.PENDING,
                        score=100.0,
                        target_qty=shave_qty,
                        reason=(
                            f"PROFIT-SHAVE: {pos.ticker} at {pnl_pct:+.1f}% "
                            f"(> {self.profit_trigger:.0f}%). Trim "
                            f"{self.profit_shave * 100:.0f}% -> sell {shave_qty} "
                            f"of {pos.qty_shares} share(s)."
                        ),
                    )
                )
                logger.info(
                    "Rebalance PROFIT-SHAVE %s (%.1f%%): sell %d of %d.",
                    pos.ticker,
                    pnl_pct,
                    shave_qty,
                    pos.qty_shares,
                )

        logger.info("Rebalancer produced %d SELL signal(s).", len(signals))
        return signals


if __name__ == "__main__":
    from datetime import datetime, timezone

    sys.path.insert(0, _CORE_DIR)
    from data_models import Position  # noqa: E402

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    portfolio = PortfolioState(
        cash_available=5000.0,
        total_equity=20000.0,
        positions=[
            # Winner (+25%) -> profit shave.
            Position(ticker="MC.PA", qty_shares=10, avg_entry_price=100.0,
                     current_price=125.0, sector="Luxury"),
            # Loser without DuckDB -> ATR stop skipped (no timeseries_db).
            Position(ticker="STLAP.PA", qty_shares=8, avg_entry_price=20.0,
                     current_price=17.6, sector="Auto"),
            # Small mover (+3%) -> untouched.
            Position(ticker="AI.PA", qty_shares=4, avg_entry_price=150.0,
                     current_price=154.5, sector="Industrials"),
            # Core ETF up +30% -> still excluded.
            Position(ticker="CW8.PA", qty_shares=50, avg_entry_price=400.0,
                     current_price=520.0, sector="ETF"),
        ],
        last_updated=datetime.now(timezone.utc),
    )

    rebal = PortfolioRebalancer()
    out = rebal.generate_rebalance_signals(portfolio)
    print(f"\nGenerated {len(out)} rebalance signal(s):")
    for s in out:
        print(f"  {s.ticker} {s.signal_type.value} qty={s.target_qty}\n    {s.reason}")
