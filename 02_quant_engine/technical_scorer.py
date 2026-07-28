"""Quantitative signal engine for PEA Sniper Terminal V-Prime.

Reads OHLCV history from DuckDB, computes technical indicators via the
pandas-ta accessor, and emits raw ``Signal`` objects from an **ensemble
conviction score** (Phase 20) — not a single boolean mean-reversion flag.

Hard vetoes (VIX panic, EPS < 0) live at the Orchestrator. This module only
scores survivors' technical / alt-data axes (0–100) and emits when ≥ 65.
"""

from __future__ import annotations

import asyncio
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
from quantitative_math import calculate_z_score

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
from sqlite_portfolio import PortfolioDB  # noqa: E402

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

try:  # Optional: richer news signal if OpenRouter/news module is available.
    from news_sentiment_llm import NewsSentimentScorer  # noqa: E402
except Exception:  # noqa: BLE001
    NewsSentimentScorer = None  # type: ignore[assignment]


def _heuristic_news_score(title: str) -> int:
    """Fallback keyword score (-75..+75) when LLM news is unavailable."""
    t = (title or "").casefold()
    if not t:
        return 0
    bull = (
        "rachat", "acquisition", "fusion", "record", "hausse", "rebond",
        "dividende", "benefice", "bénéfice", "profit", "croissance", "contrat",
        "upgrade", "buyback", "surperform", "positif", "approval", "accord",
    )
    bear = (
        "amende", "fraude", "scandale", "baisse", "perte", "licenciement",
        "faillite", "recession", "récession", "guerre", "sanction", "downgrade",
        "profit warning", "deception", "déception", "enquete", "enquête", "crise",
    )
    score = 0
    for w in bull:
        if w in t:
            score += 28
    for w in bear:
        if w in t:
            score -= 32
    return int(max(-75, min(75, score)))


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

    @staticmethod
    def _load_fundamentals_from_sources(ticker: str) -> dict:
        """Fetch fundamentals via SQLite cache -> Finnhub/yfinance sensor."""
        try:
            pdb = PortfolioDB()
            pdb.init_db()
            cache = pdb.get_cached_fundamentals(ticker, max_age_days=7)
            if cache:
                return cache
        except Exception as exc:  # noqa: BLE001
            logger.debug("Fundamentals cache read failed for %s: %s", ticker, exc)

        data: dict = {}
        try:
            if str(_SENSORS_DIR) not in sys.path:
                sys.path.insert(0, str(_SENSORS_DIR))
            from fundamentals_api import FundamentalsSensor  # noqa: WPS433

            data = FundamentalsSensor().get_basic_financials(ticker) or {}
        except Exception as exc:  # noqa: BLE001
            logger.debug("Fundamentals sensor unavailable for %s: %s", ticker, exc)
            data = {}

        if any(
            data.get(k) is not None
            for k in ("pe_ratio", "pb_ratio", "roe", "debt_to_equity")
        ):
            try:
                pdb = PortfolioDB()
                pdb.init_db()
                pdb.upsert_fundamentals(ticker, data)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Fundamentals cache upsert failed for %s: %s", ticker, exc)
        return data

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
        """Attach trend/MR/breakout indicators for the ensemble committee."""
        out = df.copy()
        close = out["Close"]
        out["SMA_5"] = out.ta.sma(close=close, length=5)
        out["SMA_50"] = out.ta.sma(close=close, length=50)
        out["SMA_200"] = out.ta.sma(close=close, length=200)
        out["RSI_14"] = out.ta.rsi(close=close, length=14)
        out.ta.macd(close=close, append=True)
        out.ta.bbands(close=close, append=True)
        out["Z_SCORE_50"] = calculate_z_score(close)
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
        """Committee-style multi-model score (0..100 total)."""
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
            "model_scores": {
                "trend_model": 0.0,
                "mean_reversion_model": 0.0,
                "breakout_model": 0.0,
                "context_model": 0.0,
            },
            "context_breakdown": {
                "fundamentals": 0.0,
                "insiders": 0.0,
                "news": 0.0,
                "polymarket": 0.0,
            },
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
        z50 = last.get("Z_SCORE_50")
        factors: list[str] = []
        news_mod = 0.0
        poly_mod = 0.0
        fundamentals_score = 0.0
        insider_score = 0.0

        # --- Trend model: MACD histogram + close>SMA50 ----------------------
        trend_score = 0.0
        macd_hist_col = next((c for c in enriched.columns if c.startswith("MACDh_")), "")
        sma_50 = last.get("SMA_50")
        if macd_hist_col:
            mh = pd.to_numeric(enriched[macd_hist_col], errors="coerce").dropna()
            if len(mh) >= 2:
                last_h = float(mh.iloc[-1])
                prev_h = float(mh.iloc[-2])
                if last_h > 0:
                    trend_score += 35.0
                if last_h > prev_h:
                    trend_score += 25.0
                if sma_50 is not None and not pd.isna(sma_50) and close > float(sma_50):
                    trend_score += 40.0
        trend_score = max(0.0, min(100.0, trend_score))
        if trend_score > 0:
            factors.append(f"TREND {trend_score:.0f}/100")

        # --- Mean-reversion model: RSI + lower Bollinger proximity ----------
        mr_score = 0.0
        bbl_col = next((c for c in enriched.columns if c.startswith("BBL_")), "")
        if rsi_14 is not None and not pd.isna(rsi_14):
            rv = float(rsi_14)
            if rv < 30:
                mr_score += 60.0
            elif rv < 35:
                mr_score += 35.0
            elif rv < 40:
                mr_score += 15.0
        if z50 is not None and not pd.isna(z50):
            z = float(z50)
            if z < -2.0:
                mr_score += 30.0
                factors.append(f"STAT+30 Z={z:.2f}< -2")
            elif z < -1.5:
                mr_score += 15.0
                factors.append(f"STAT+15 Z={z:.2f}< -1.5")
        if bbl_col:
            bbl = last.get(bbl_col)
            if bbl is not None and not pd.isna(bbl) and float(bbl) > 0:
                dist = abs(close - float(bbl)) / float(bbl)
                if close <= float(bbl) * 1.02:
                    mr_score += 40.0
                elif dist <= 0.05:
                    mr_score += 20.0
        mr_score = max(0.0, min(100.0, mr_score))
        if mr_score > 0:
            factors.append(f"MR {mr_score:.0f}/100")

        # --- Breakout model: close 20d high + volume burst -------------------
        breakout_score = 0.0
        if "Volume" in enriched.columns and len(enriched) >= 25 and not pd.isna(last.get("Volume")):
            w20 = enriched.tail(20)
            high_20 = float(pd.to_numeric(w20["Close"], errors="coerce").max())
            avg_vol_20 = float(pd.to_numeric(enriched["Volume"], errors="coerce").tail(20).mean())
            today_vol = float(last["Volume"])
            if high_20 > 0 and close >= high_20 * 0.999:
                breakout_score += 60.0
            if avg_vol_20 > 0 and today_vol > 1.8 * avg_vol_20:
                breakout_score += 40.0
        breakout_score = max(0.0, min(100.0, breakout_score))
        if breakout_score > 0:
            factors.append(f"BREAKOUT {breakout_score:.0f}/100")

        sensor = macro_sensor if macro_sensor is not None else self._macro_sensor()
        cluster = 0
        if sensor is not None:
            try:
                cluster = int(sensor.get_insider_buy_cluster(ticker))
            except Exception as exc:  # noqa: BLE001
                logger.debug("Insider cluster failed for %s: %s", ticker, exc)
                cluster = 0
        if cluster >= 2:
            insider_score = 100.0
            factors.append(f"INS 100/100 cluster buys={cluster}")
        elif cluster == 1:
            insider_score = 65.0
            factors.append("INS 65/100 single buy cluster")
        elif cluster <= -1:
            insider_score = 10.0
            factors.append("INS 10/100 net selling")
        else:
            insider_score = 35.0

        # Value/Quality axis (fundamentals) with graceful fallback.
        fundamentals = self._load_fundamentals_from_sources(ticker)
        pe = fundamentals.get("pe_ratio")
        pb = fundamentals.get("pb_ratio")
        roe = fundamentals.get("roe")
        debt_eq = fundamentals.get("debt_to_equity")
        source = fundamentals.get("source") or "fallback"

        if any(v is not None for v in (pe, pb, roe, debt_eq)):
            f_raw = 50.0
            # Value
            if pe is not None and pe > 0:
                if pe < 15:
                    f_raw += 18.0
                    factors.append(f"VAL+8 PE={pe:.1f}<15 ({source})")
                elif pe < 25:
                    f_raw += 10.0
                    factors.append(f"VAL+5 PE={pe:.1f}<25 ({source})")
                elif pe > 30:
                    f_raw -= 12.0
                    factors.append(f"VAL-3 PE={pe:.1f}>30 ({source})")
            if pb is not None and pb > 0:
                if pb < 2.0:
                    f_raw += 12.0
                    factors.append(f"VAL+4 PB={pb:.2f}<2 ({source})")
                elif pb > 5.0:
                    f_raw -= 8.0
                    factors.append(f"VAL-2 PB={pb:.2f}>5 ({source})")

            # Quality
            if roe is not None:
                if roe >= 0.15:
                    f_raw += 16.0
                    factors.append(f"QLT+6 ROE={roe:.2f}>=15% ({source})")
                elif roe <= 0:
                    f_raw -= 8.0
                    factors.append(f"QLT-2 ROE={roe:.2f}<=0 ({source})")
            if debt_eq is not None:
                if debt_eq > 2.0:
                    f_raw -= 24.0
                    factors.append(f"QLT-7 D/E={debt_eq:.2f}>2 ({source})")
                elif debt_eq < 1.0:
                    f_raw += 8.0
                    factors.append(f"QLT+2 D/E={debt_eq:.2f}<1 ({source})")
            fundamentals_score = max(0.0, min(100.0, f_raw))
        else:
            # Fallback: legacy EPS profitability proxy when fundamentals unavailable.
            if self.is_profitable(ticker):
                fundamentals_score = 55.0
                factors.append("Q/V+10 EPS>0 proxy (fallback)")
            else:
                fundamentals_score = 25.0
                factors.append("Q/V-5 EPS<0 proxy (fallback)")

        # Holistic news integration: LLM sentiment first, heuristic fallback.
        news_score = 0.0
        headlines: list[str] = []
        try:
            if yf is not None:
                raw_news = yf.Ticker(ticker).news or []
                for n in raw_news[:6]:
                    content = n.get("content", n)
                    title = (content.get("title") or n.get("title") or "").strip()
                    if title:
                        headlines.append(title)
        except Exception as exc:  # noqa: BLE001
            logger.debug("News fetch failed for %s: %s", ticker, exc)
        if headlines:
            if NewsSentimentScorer is not None:
                try:
                    news_score = float(
                        asyncio.run(NewsSentimentScorer().analyze_news(ticker, headlines))
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("LLM sentiment failed for %s: %s", ticker, exc)
            if abs(news_score) < 1:
                heuristic_vals = [_heuristic_news_score(h) for h in headlines]
                if heuristic_vals:
                    news_score = float(sum(heuristic_vals) / len(heuristic_vals))
        if news_score > 30:
            news_mod = 15.0
            factors.append(f"NEWS+10 Bullish sentiment ({news_score:.0f})")
        elif news_score < -30:
            news_mod = -20.0
            factors.append(f"NEWS-15 Bearish sentiment ({news_score:.0f})")
        news_component = max(0.0, min(100.0, 50.0 + news_mod))

        # Polymarket integration via existing macro sensor.
        if sensor is not None:
            try:
                poly_prob = float(sensor.get_polymarket_sentiment(f"{ticker} outlook"))
                if poly_prob >= 0.62:
                    poly_mod = 12.0
                    factors.append(f"POLY+10 YES={poly_prob:.2f}")
                elif poly_prob <= 0.38:
                    poly_mod = -12.0
                    factors.append(f"POLY-10 YES={poly_prob:.2f}")
            except Exception as exc:  # noqa: BLE001
                logger.debug("Polymarket sentiment failed for %s: %s", ticker, exc)
        poly_component = max(0.0, min(100.0, 50.0 + poly_mod))

        # Context model combines fundamentals + sentiment + insiders.
        context_score = (
            0.45 * fundamentals_score
            + 0.25 * insider_score
            + 0.20 * news_component
            + 0.10 * poly_component
        )
        context_score = max(0.0, min(100.0, context_score))

        # Final ensemble as weighted average of model committee.
        total = (
            0.30 * trend_score
            + 0.25 * mr_score
            + 0.20 * breakout_score
            + 0.25 * context_score
        )
        total = float(max(0.0, min(100.0, total)))

        return {
            # Backward-compatible keys consumed by dashboard/orchestrator.
            "mean_reversion": int(round(mr_score * 0.35)),
            "volume_breakout": int(round(breakout_score * 0.25)),
            "insider": int(round(insider_score * 0.20)),
            "institutional": int(round(fundamentals_score * 0.20)),
            "news_modifier": int(round(news_mod)),
            "polymarket_modifier": int(round(poly_mod)),
            "total": total,
            "factors": factors,
            "rsi": None if pd.isna(rsi_14) else float(rsi_14),
            "close": close,
            "sma200": None if pd.isna(sma_200) else float(sma_200),
            "zscore_50": None if (z50 is None or pd.isna(z50)) else float(z50),
            "model_scores": {
                "trend_model": float(trend_score),
                "mean_reversion_model": float(mr_score),
                "breakout_model": float(breakout_score),
                "context_model": float(context_score),
            },
            "context_breakdown": {
                "fundamentals": float(fundamentals_score),
                "insiders": float(insider_score),
                "news": float(news_component),
                "polymarket": float(poly_component),
            },
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
