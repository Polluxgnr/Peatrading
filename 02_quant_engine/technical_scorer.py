"""Quantitative signal engine for PEA Sniper Terminal V-Prime.

Reads OHLCV history from DuckDB, computes technical indicators via the
pandas-ta accessor, and emits raw ``Signal`` objects from an **ensemble
conviction score** (Phase 20) — not a single boolean mean-reversion flag.

Hard vetoes (VIX panic, EPS < 0) live at the Orchestrator. This module only
scores survivors' technical / alt-data axes (0–100) and emits when ≥ 65.
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, List, Optional

import pandas as pd
import yaml

try:  # yfinance is only needed for the optional Quality (EPS) filter.
    import yfinance as yf
except Exception:  # noqa: BLE001 - keep the pure-math engine importable offline.
    yf = None  # type: ignore[assignment]

try:  # pragma: no cover - environment-dependent import.
    import pandas_ta as ta  # noqa: F401
except ImportError:  # pragma: no cover
    import pandas_ta_classic as ta  # noqa: F401

_CORE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "01_memory_core"
)
sys.path.insert(0, _CORE_DIR)

from data_models import Signal, SignalStatus, SignalType  # noqa: E402

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_DIR = _PROJECT_ROOT / "config"
_SENSORS_DIR = _PROJECT_ROOT / "00_data_sensors"

# Minimum history required to compute a valid SMA-200.
_MIN_ROWS = 200
_DEFAULT_RSI_OVERSOLD = 30.0
_CONVICTION_EMIT_FLOOR = 65.0

# Proxy for institutional quality (Fundsmith / Amundi-style large holdings).
# Also mirrored on MacroAlphaSensor.get_institutional_consensus.
TOP_INSTITUTIONAL_HOLDINGS: set[str] = {
    "MC.PA", "OR.PA", "RMS.PA", "AI.PA", "SAN.PA", "TTE.PA", "BNP.PA",
    "AIR.PA", "SU.PA", "EL.PA", "KER.PA", "CS.PA", "DG.PA", "DSY.PA",
    "SAF.PA", "STLAP.PA", "HO.PA", "ENGI.PA", "CAP.PA", "BN.PA",
    "ASML.AS", "SAP.DE", "SIE.DE", "ALV.DE", "DTE.DE", "ADS.DE",
    "NESN.SW", "NOVN.SW", "ROG.SW", "AZN.L",
}


class SignalGenerator:
    """Generates raw BUY signals from ensemble conviction scoring."""

    def __init__(
        self,
        config_path: str | Path | None = None,
        macro_sensor: Any | None = None,
    ) -> None:
        """Load optional thresholds from ``risk_params.yaml``.

        Args:
            config_path: Config dir or risk_params.yaml path.
            macro_sensor: Optional ``MacroAlphaSensor`` for insider /
                institutional axes (lazy-created on first need if None).
        """
        path = Path(config_path) if config_path else _DEFAULT_CONFIG_DIR
        risk_file = path if path.is_file() else path / "risk_params.yaml"
        risk: dict = {}
        if risk_file.exists():
            with open(risk_file, "r", encoding="utf-8") as fh:
                risk = yaml.safe_load(fh) or {}
        self.rsi_oversold: float = float(
            risk.get("RSI_OVERSOLD_THRESHOLD", _DEFAULT_RSI_OVERSOLD)
        )
        self._macro = macro_sensor

    def _macro_sensor(self) -> Any | None:
        if self._macro is not None:
            return self._macro
        try:
            if str(_SENSORS_DIR) not in sys.path:
                sys.path.insert(0, str(_SENSORS_DIR))
            from macro_alpha_api import MacroAlphaSensor  # noqa: WPS433

            self._macro = MacroAlphaSensor()
            return self._macro
        except Exception as exc:  # noqa: BLE001
            logger.debug("MacroAlphaSensor unavailable for conviction: %s", exc)
            return None

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Attach SMA-5/50/200 and RSI-14 columns for a single ticker."""
        out = df.copy()
        close = out["Close"]
        out["SMA_5"] = out.ta.sma(close=close, length=5)
        out["SMA_50"] = out.ta.sma(close=close, length=50)
        out["SMA_200"] = out.ta.sma(close=close, length=200)
        out["RSI_14"] = out.ta.rsi(close=close, length=14)
        return out

    def score_rsi(self, rsi_value: float) -> float:
        """Legacy RSI→score helper (kept for UI / back-compat)."""
        thr = self.rsi_oversold
        if rsi_value is None or pd.isna(rsi_value):
            return 0.0
        if rsi_value >= thr:
            return 0.0
        score = 60.0 + (thr - rsi_value) * 2.0
        return float(max(60.0, min(100.0, score)))

    @staticmethod
    @lru_cache(maxsize=512)
    def _trailing_eps(ticker: str) -> float | None:
        """Return trailing EPS via yfinance (cached). ``None`` = unknown."""
        if yf is None:
            return None
        try:
            info = yf.Ticker(ticker).info or {}
            for key in ("trailingEps", "epsTrailingTwelveMonths"):
                val = info.get(key)
                if val is not None:
                    return float(val)
        except Exception:  # noqa: BLE001
            logger.debug("EPS lookup failed for %s; treating as unknown.", ticker)
        return None

    def is_profitable(self, ticker: str) -> bool:
        """Quality filter helper for Orchestrator: False only if EPS known < 0."""
        eps = self._trailing_eps(ticker)
        if eps is None:
            return True
        return eps > 0

    def evaluate(
        self,
        ticker: str,
        history: pd.DataFrame,
        *,
        macro_sensor: Any | None = None,
    ) -> dict[str, Any]:
        """Score a ticker on the four ensemble axes (max 100).

        Weights
        -------
        * Mean Reversion — max 35
        * Volume Breakout — max 25
        * Insider Clustering — max 20
        * Institutional Quality — max 20

        Returns:
            dict: Keys ``mean_reversion``, ``volume_breakout``, ``insider``,
            ``institutional``, ``total``, ``factors`` (list[str]), plus
            diagnostic RSI / close fields when available.
        """
        empty = {
            "mean_reversion": 0,
            "volume_breakout": 0,
            "insider": 0,
            "institutional": 0,
            "total": 0.0,
            "factors": [],
            "rsi": None,
            "close": None,
            "sma200": None,
        }
        if history is None or history.empty or len(history) < _MIN_ROWS:
            return empty
        if "Close" not in history.columns:
            return empty

        enriched = self.calculate_indicators(history)
        last = enriched.iloc[-1]
        close = float(last["Close"])
        sma_200 = last["SMA_200"]
        rsi_14 = last["RSI_14"]
        factors: list[str] = []
        mr = vol_pts = ins_pts = inst_pts = 0

        if not pd.isna(sma_200) and not pd.isna(rsi_14):
            above_sma = close > float(sma_200)
            if above_sma and float(rsi_14) < 30.0:
                mr = 35
                factors.append(f"MR+35 RSI={float(rsi_14):.1f}<30 & Close>SMA200")
            elif above_sma and float(rsi_14) < 40.0:
                mr = 15
                factors.append(f"MR+15 RSI={float(rsi_14):.1f}<40 & Close>SMA200")

        # Volume breakout: 50d high close + volume > 2× 20d ADV
        if (
            "Volume" in enriched.columns
            and len(enriched) >= 50
            and not pd.isna(last.get("Volume"))
        ):
            window50 = enriched.tail(50)
            high_50 = float(window50["Close"].max())
            avg_vol_20 = float(enriched["Volume"].tail(20).mean())
            today_vol = float(last["Volume"])
            if (
                avg_vol_20 > 0
                and close >= high_50 * 0.999
                and today_vol > 2.0 * avg_vol_20
            ):
                vol_pts = 25
                factors.append(
                    f"VOL+25 50d-high + vol {today_vol / avg_vol_20:.1f}× ADV20"
                )

        sensor = macro_sensor if macro_sensor is not None else self._macro_sensor()
        cluster = 0
        if sensor is not None:
            try:
                cluster = int(sensor.get_insider_buy_cluster(ticker))
            except Exception as exc:  # noqa: BLE001
                logger.debug("Insider cluster failed for %s: %s", ticker, exc)
                cluster = 0
        if cluster >= 2:
            ins_pts = 20
            factors.append(f"INS+20 cluster buys={cluster}")
        elif cluster == 1:
            ins_pts = 10
            factors.append("INS+10 single buy cluster")

        is_inst = ticker in TOP_INSTITUTIONAL_HOLDINGS
        if sensor is not None:
            try:
                is_inst = bool(sensor.get_institutional_consensus(ticker)) or is_inst
            except Exception:  # noqa: BLE001
                pass
        if is_inst:
            inst_pts = 20
            factors.append("INST+20 institutional consensus proxy")

        total = float(mr + vol_pts + ins_pts + inst_pts)
        return {
            "mean_reversion": mr,
            "volume_breakout": vol_pts,
            "insider": ins_pts,
            "institutional": inst_pts,
            "total": total,
            "factors": factors,
            "rsi": None if pd.isna(rsi_14) else float(rsi_14),
            "close": close,
            "sma200": None if pd.isna(sma_200) else float(sma_200),
        }

    def generate_raw_signals(
        self,
        db_manager: Any,
        tickers: List[str],
        apply_quality_filter: bool = False,
        apply_momentum_filter: bool = False,
        conviction_floor: float = _CONVICTION_EMIT_FLOOR,
    ) -> List[Signal]:
        """Evaluate each ticker; emit BUY when ensemble conviction ≥ floor.

        Args:
            db_manager: ``TimeSeriesDB`` with ``get_historical_prices``.
            tickers: Universe symbols.
            apply_quality_filter: Legacy EPS gate (prefer Orchestrator).
            apply_momentum_filter: Unused in ensemble mode (kept for API compat).
            conviction_floor: Minimum total points to emit (default 65).

        Returns:
            List[Signal]: PENDING BUYs with score = conviction total.
        """
        _ = apply_momentum_filter  # ensemble replaces SMA5 knife filter
        signals: List[Signal] = []
        sensor = self._macro_sensor()

        for ticker in tickers:
            df = db_manager.get_historical_prices(ticker, days=252)
            if df is None or df.empty or len(df) < _MIN_ROWS:
                logger.debug(
                    "Skipping %s: insufficient history (%d rows).",
                    ticker,
                    0 if df is None else len(df),
                )
                continue

            if apply_quality_filter and not self.is_profitable(ticker):
                logger.info("Quality filter blocked %s (EPS < 0).", ticker)
                continue

            conv = self.evaluate(ticker, df, macro_sensor=sensor)
            total = float(conv.get("total") or 0.0)
            if total < float(conviction_floor):
                logger.debug(
                    "Skip %s: conviction %.0f < %.0f (%s).",
                    ticker,
                    total,
                    conviction_floor,
                    ", ".join(conv.get("factors") or []) or "no factors",
                )
                continue

            reason = (
                f"Conviction {total:.0f}/100 ≥ {conviction_floor:.0f} | "
                + " · ".join(conv.get("factors") or ["ensemble"])
            )
            signal = Signal(
                id=str(uuid.uuid4()),
                ticker=ticker,
                signal_type=SignalType.BUY,
                status=SignalStatus.PENDING,
                score=total,
                target_qty=None,
                created_at=datetime.now(timezone.utc),
                reason=reason,
            )
            signals.append(signal)
            logger.info(
                "BUY signal %s for %s (conviction=%.0f).",
                signal.id[:8],
                ticker,
                total,
            )

        return signals


if __name__ == "__main__":
    import numpy as np

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    n = 260
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    base = np.linspace(100.0, 200.0, n)
    close = base.copy()
    close[-8:] = close[-9] * np.array(
        [0.955, 0.925, 0.898, 0.875, 0.858, 0.848, 0.858, 0.866]
    )
    volume = np.full(n, 1_000_000.0)
    volume[-1] = 3_500_000.0  # volume breakout candidate
    mock = pd.DataFrame(
        {
            "Ticker": "MC.PA",
            "Date": dates,
            "Open": close,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": volume,
        }
    )

    class _MockMacro:
        def get_insider_buy_cluster(self, ticker: str) -> int:
            return 2

        def get_institutional_consensus(self, ticker: str) -> bool:
            return ticker in TOP_INSTITUTIONAL_HOLDINGS

    class _MockDB:
        def get_historical_prices(self, ticker: str, days: int = 252) -> pd.DataFrame:
            return mock

    gen = SignalGenerator(macro_sensor=_MockMacro())
    conv = gen.evaluate("MC.PA", mock)
    print("Conviction breakdown:", conv)
    results = gen.generate_raw_signals(_MockDB(), ["MC.PA"])
    print(f"\nGenerated {len(results)} signal(s):")
    for s in results:
        print(f"  {s.id[:8]} {s.ticker} score={s.score:.1f}")
        print(f"  reason: {s.reason}")
