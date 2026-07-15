"""Smart DCA core engine for PEA Sniper Terminal V-Prime (Phase 10).

The Core/Satellite model parks the bulk of capital in a broad MSCI World PEA ETF
(``CW8.PA``) and accumulates it with a *Smart* Dollar-Cost-Averaging rule:

  * When ``CW8`` trades **below** its 200-day SMA (market crash / fear), the
    engine raises the target core weight and buys more aggressively.
  * When it trades **above** the SMA (overheated / calm), it keeps the standard
    target weight and drips capital in more slowly.

This module is pure math: it reads price history and config, and returns a
``Signal`` for the Core ETF. It never writes to any database or calls an LLM.
"""

import logging
import math
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

_CORE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "01_memory_core"
)
sys.path.insert(0, _CORE_DIR)

from data_models import Signal, SignalStatus, SignalType  # noqa: E402

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_DIR = _PROJECT_ROOT / "config"
_SMA_LENGTH = 200
_MIN_ROWS = 200


class SmartDcaCore:
    """Recommends Core ETF accumulation via a regime-aware Smart DCA rule."""

    def __init__(self, config_path: str | Path | None = None) -> None:
        """Load core allocation parameters from ``risk_params.yaml``.

        Args:
            config_path: Path to the ``config`` directory (or a risk_params
                YAML file). Defaults to ``<project_root>/config``.
        """
        risk = self._load_risk_params(config_path)
        self.core_ticker: str = str(risk.get("CORE_TICKER", "CW8.PA"))
        self.target_pct: float = float(risk.get("CORE_TARGET_PCT", 0.70))
        self.crash_target_pct: float = float(risk.get("CORE_CRASH_TARGET_PCT", 0.75))
        self.max_tranche_pct: float = float(risk.get("CORE_DCA_MAX_TRANCHE_PCT", 0.05))
        logger.debug(
            "SmartDcaCore loaded: %s target=%.2f crash=%.2f tranche<=%.2f",
            self.core_ticker,
            self.target_pct,
            self.crash_target_pct,
            self.max_tranche_pct,
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

    def _neutral_signal(self, reason: str) -> Signal:
        """Return a do-nothing (score 0, qty 0) core signal with a reason."""
        return Signal(
            ticker=self.core_ticker,
            signal_type=SignalType.BUY,
            status=SignalStatus.PENDING,
            score=0.0,
            target_qty=0,
            reason=reason,
        )

    def evaluate_cw8(
        self, db_manager: Any, current_cash: float, total_equity: float
    ) -> Signal:
        """Produce a Smart-DCA accumulation signal for the Core ETF.

        Args:
            db_manager: Phase 2 ``TimeSeriesDB`` exposing
                ``get_historical_prices(ticker, days)``.
            current_cash: Uninvested cash available in EUR.
            total_equity: Total account value in EUR.

        Returns:
            Signal: A BUY signal for the Core ETF. ``target_qty`` is the whole
            number of shares to accumulate this pass (0 if none warranted or
            data is missing).
        """
        if total_equity <= 0 or current_cash <= 0:
            return self._neutral_signal(
                "Core DCA skipped: no cash/equity available."
            )

        try:
            df = db_manager.get_historical_prices(self.core_ticker, days=400)
        except Exception:  # noqa: BLE001
            logger.exception("Could not read history for %s.", self.core_ticker)
            return self._neutral_signal(
                f"Core DCA skipped: history read failed for {self.core_ticker}."
            )

        if df is None or df.empty or len(df) < _MIN_ROWS:
            return self._neutral_signal(
                f"Core DCA skipped: insufficient history for {self.core_ticker}."
            )

        close = df["Close"].astype(float)
        price = float(close.iloc[-1])
        sma200 = float(close.tail(_SMA_LENGTH).mean())
        if price <= 0 or pd.isna(sma200):
            return self._neutral_signal("Core DCA skipped: invalid price/SMA.")

        # --- Regime decision --------------------------------------------------
        crash_regime = price < sma200
        target_pct = self.crash_target_pct if crash_regime else self.target_pct
        # Bigger, more urgent tranche when the market is fearful.
        tranche_pct = self.max_tranche_pct if crash_regime else self.max_tranche_pct / 2.0
        score = 90.0 if crash_regime else 65.0

        target_value = target_pct * total_equity
        tranche_cash = min(current_cash, tranche_pct * total_equity, target_value)
        qty = int(math.floor(tranche_cash / price)) if tranche_cash > 0 else 0

        regime_txt = (
            "CRASH regime (price < SMA200): accumulate aggressively"
            if crash_regime
            else "CALM regime (price > SMA200): standard drip"
        )
        reason = (
            f"Smart DCA {self.core_ticker}: {regime_txt}. "
            f"Price {price:.2f} vs SMA200 {sma200:.2f}. "
            f"Target core weight {target_pct * 100:.0f}% -> buy {qty} share(s) "
            f"(~{qty * price:.0f} EUR tranche)."
        )

        signal = Signal(
            ticker=self.core_ticker,
            signal_type=SignalType.BUY,
            status=SignalStatus.PENDING,
            score=score,
            target_qty=qty,
            reason=reason,
        )
        logger.info(
            "Core DCA %s: %s (qty=%d, score=%.0f).",
            self.core_ticker,
            "CRASH" if crash_regime else "CALM",
            qty,
            score,
        )
        return signal


if __name__ == "__main__":
    import numpy as np

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    def _make_df(prices: np.ndarray) -> pd.DataFrame:
        n = len(prices)
        return pd.DataFrame(
            {
                "Ticker": "CW8.PA",
                "Date": pd.date_range("2024-01-01", periods=n, freq="B"),
                "Open": prices,
                "High": prices * 1.01,
                "Low": prices * 0.99,
                "Close": prices,
                "Volume": 1_000_000,
            }
        )

    class _MockDB:
        def __init__(self, df: pd.DataFrame) -> None:
            self._df = df

        def get_historical_prices(self, ticker: str, days: int = 400) -> pd.DataFrame:
            return self._df

    core = SmartDcaCore()

    print("--- CALM regime (price above SMA200) ---")
    calm = _make_df(np.linspace(100.0, 200.0, 260))
    s1 = core.evaluate_cw8(_MockDB(calm), current_cash=8000.0, total_equity=20000.0)
    print(f"  score={s1.score:.0f} qty={s1.target_qty}\n  {s1.reason}")

    print("\n--- CRASH regime (price below SMA200) ---")
    crash = _make_df(np.concatenate([np.linspace(200.0, 260.0, 200),
                                     np.linspace(260.0, 170.0, 60)]))
    s2 = core.evaluate_cw8(_MockDB(crash), current_cash=8000.0, total_equity=20000.0)
    print(f"  score={s2.score:.0f} qty={s2.target_qty}\n  {s2.reason}")
