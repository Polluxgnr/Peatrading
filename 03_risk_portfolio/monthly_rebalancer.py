"""Portfolio rebalancer for PEA Sniper Terminal V-Prime (Phase 12/15/16).

Mechanical housekeeping trades:

  * **ATR stop-loss (daily):** fully exit a satellite when
    ``current_price < avg_entry - mult * ATR_14``.
  * **Profit shave (monthly):** trim a fixed slice of winners above +20% PnL.

The Core ETF is excluded — held and averaged into, never shaved or stopped out.

Absolute ATR is correct for *per-name* stop distance (ATR scales with price).
``atr_pct = ATR / price`` is exposed for cross-name comparisons / vol dashboards.
"""

from __future__ import annotations

import logging
import math
import os
import sys
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence

import pandas as pd
import yaml

try:
    import pandas_ta as ta  # noqa: F401
except ImportError:  # pragma: no cover
    import pandas_ta_classic as ta  # noqa: F401

_CORE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "01_memory_core"
)
sys.path.insert(0, _CORE_DIR)

from data_models import PortfolioState, Signal, SignalStatus, SignalType  # noqa: E402

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_DIR = _PROJECT_ROOT / "config"

_ATR_STOP_MULT = 2.5
_ATR_LENGTH = 14
_OHLCV_LOOKBACK = 60


class PortfolioRebalancer:
    """Generates mechanical SELL signals for ATR stops and/or profit shaves."""

    def __init__(
        self,
        config_path: str | Path | None = None,
        timeseries_db: Any | None = None,
    ) -> None:
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
            "Rebalancer: profit>+%.0f%% shave %.0f%%, ATR stop %.1fx (core=%s).",
            self.profit_trigger,
            self.profit_shave * 100,
            self.atr_stop_mult,
            self.core_ticker,
        )

    @staticmethod
    def _load_risk_params(config_path: str | Path | None) -> dict:
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
        """Latest ATR_14 in price units, or None."""
        if self.timeseries_db is None:
            return None
        try:
            hist = self.timeseries_db.get_historical_prices(
                ticker, days=_OHLCV_LOOKBACK
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to fetch OHLCV for ATR on %s.", ticker)
            return None
        if hist is None or hist.empty or len(hist) < _ATR_LENGTH + 1:
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

    @staticmethod
    def atr_pct(atr: float, price: float) -> float | None:
        """Normalized ATR (ATR / price) for cross-name comparisons."""
        if price is None or price <= 0 or atr is None or atr <= 0:
            return None
        return float(atr / price)

    def generate_atr_stop_signals(
        self, portfolio: PortfolioState
    ) -> List[Signal]:
        """Daily job: ATR stop-loss SELLs only."""
        return self.generate_rebalance_signals(portfolio, modes=("atr",))

    def generate_profit_shave_signals(
        self, portfolio: PortfolioState
    ) -> List[Signal]:
        """Monthly job: profit-shave SELLs only."""
        return self.generate_rebalance_signals(portfolio, modes=("shave",))

    def generate_rebalance_signals(
        self,
        portfolio: PortfolioState,
        modes: Sequence[str] | None = None,
    ) -> List[Signal]:
        """Produce SELL signals for the requested modes.

        Args:
            portfolio: Current snapshot.
            modes: Subset of ``(\"atr\", \"shave\")``. Default = both
                (backward compatible with Phase 15 callers).
        """
        wanted: Iterable[str] = modes if modes is not None else ("atr", "shave")
        want_atr = "atr" in wanted
        want_shave = "shave" in wanted
        signals: List[Signal] = []

        for pos in portfolio.positions:
            if pos.ticker == self.core_ticker or pos.qty_shares <= 0:
                continue

            pnl_pct = pos.unrealized_pnl_pct * 100.0

            if want_atr and pnl_pct < 0:
                atr14 = self._latest_atr14(pos.ticker)
                if atr14 is not None:
                    stop_level = pos.avg_entry_price - (
                        self.atr_stop_mult * atr14
                    )
                    if pos.current_price < stop_level:
                        pct = self.atr_pct(atr14, pos.current_price)
                        pct_s = f", ATR%={pct * 100:.2f}%" if pct else ""
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
                                    f"(PnL {pnl_pct:+.1f}%{pct_s}). "
                                    f"Full exit of {pos.qty_shares} share(s)."
                                ),
                            )
                        )
                        logger.info(
                            "ATR-STOP %s: price=%.2f stop=%.2f ATR14=%.2f.",
                            pos.ticker,
                            pos.current_price,
                            stop_level,
                            atr14,
                        )
                        continue  # already exiting; skip shave

            if want_shave and pnl_pct > self.profit_trigger:
                shave_qty = int(math.floor(pos.qty_shares * self.profit_shave))
                if shave_qty < 1:
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
                    "PROFIT-SHAVE %s (%.1f%%): sell %d of %d.",
                    pos.ticker,
                    pnl_pct,
                    shave_qty,
                    pos.qty_shares,
                )

        logger.info("Rebalancer produced %d SELL signal(s).", len(signals))
        return signals
