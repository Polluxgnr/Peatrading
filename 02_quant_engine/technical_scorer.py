"""Quantitative signal engine for PEA Pollux.

Reads OHLCV history from DuckDB, computes technical indicators via the
pandas-ta accessor, and emits raw ``Signal`` objects from an **ensemble
conviction score** (Phase 20) — not a single boolean mean-reversion flag.

Hard vetoes (VIX panic, EPS < 0) live at the Orchestrator. This module only
scores survivors' technical / alt-data axes (0–100) and emits when ≥ 65.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
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

_CORE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "01_memory_core"
)
sys.path.insert(0, _CORE_DIR)

from data_models import Signal, SignalStatus, SignalType  # noqa: E402
from sqlite_portfolio import PortfolioDB  # noqa: E402
from config_validator import load_risk_config  # noqa: E402

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_DIR = _PROJECT_ROOT / "config"
_SENSORS_DIR = _PROJECT_ROOT / "00_data_sensors"

# Minimum history required to compute a valid SMA-200.
_MIN_ROWS = 200
_DEFAULT_RSI_OVERSOLD = 30.0


def _load_conviction_floor() -> float:
    """Read CONVICTION_EMIT_FLOOR from validated risk_params.yaml."""
    try:
        return float(load_risk_config().CONVICTION_EMIT_FLOOR)
    except Exception:  # noqa: BLE001
        return 65.0


_CONVICTION_EMIT_FLOOR = _load_conviction_floor()

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
        portfolio_db: Any | None = None,
        skip_regime: bool = False,
        offline_mode: bool = False,
    ) -> None:
        """Load optional thresholds from ``risk_params.yaml``.

        Args:
            config_path: Config dir or risk_params.yaml path.
            macro_sensor: Optional ``MacroAlphaSensor`` for insider /
                institutional axes (lazy-created on first need if None).
        """
        path = Path(config_path) if config_path else _DEFAULT_CONFIG_DIR
        risk = load_risk_config(path)
        self._macro = macro_sensor
        self.portfolio_db = portfolio_db
        self.offline_mode = offline_mode
        
        if skip_regime:
            self.regime = "BULL"
            self.conviction_floor = 65.0
            self.rsi_oversold = 30.0
        else:
            try:
                from market_regime import MarketRegimeClassifier
                classifier = MarketRegimeClassifier()
                self.regime = classifier.get_regime()
                self.conviction_floor, self.rsi_oversold = classifier.get_modulated_thresholds(
                    self.regime, 
                    base_conviction=float(risk.CONVICTION_EMIT_FLOOR),
                    base_rsi=float(risk.RSI_OVERSOLD_THRESHOLD)
                )
                logger.info(f"SignalGenerator loaded: regime={self.regime}, floor={self.conviction_floor}, rsi={self.rsi_oversold}")
            except Exception as exc:
                logger.warning("Could not determine market regime (%s), using base thresholds.", exc)
                self.regime = "BULL"
                self.rsi_oversold = float(risk.RSI_OVERSOLD_THRESHOLD)
                self.conviction_floor = float(risk.CONVICTION_EMIT_FLOOR)

    def _load_fundamentals_from_sources(self, ticker: str, pdb: Any = None) -> dict:
        """Fetch fundamentals via SQLite cache -> Finnhub/yfinance sensor."""
        try:
            if pdb is None:
                from sqlite_portfolio import PortfolioDB
                pdb = PortfolioDB()
                pdb.init_db()
            cache = pdb.get_cached_fundamentals(ticker, max_age_days=7)
            if cache:
                return cache
        except Exception as exc:  # noqa: BLE001
            logger.debug("Fundamentals cache read failed for %s: %s", ticker, exc)

        if self.offline_mode:
            return {}

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
                if pdb is None:
                    from sqlite_portfolio import PortfolioDB
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
        out["SMA_5"] = _calc_sma(close, 5)
        out["SMA_50"] = _calc_sma(close, 50)
        out["SMA_200"] = _calc_sma(close, 200)
        out["RSI_14"] = _calc_rsi(close, 14)
        
        macd_line, macd_hist, macd_sig = _calc_macd(close)
        out["MACD_12_26_9"] = macd_line
        out["MACDh_12_26_9"] = macd_hist
        out["MACDs_12_26_9"] = macd_sig
        
        bbl, bbm, bbu = _calc_bbands(close)
        out["BBL_5_2.0"] = bbl
        out["BBM_5_2.0"] = bbm
        out["BBU_5_2.0"] = bbu
        
        out["ATRr_14"] = _calc_atr(out["High"], out["Low"], close, 14)
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
        is_historical: bool = False,
        cs_rank: float = 50.0,
        daily_sector_means: dict[str, float] | None = None,
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
                "ml": 50.0,
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
        
        # Cross-sectional momentum modifier
        if cs_rank > 80.0:
            factors.append(f"MOM+5 Leader (Top {100 - cs_rank:.0f}%)")
        elif cs_rank < 20.0:
            factors.append(f"MOM-5 Laggard (Bot {cs_rank:.0f}%)")
        news_mod = 0.0
        poly_mod = 0.0
        fundamentals_score = 0.0
        insider_score = 0.0

        # --- Trend model: MACD histogram + close>SMA50 ----------------------
        trend_score = 0.0
        macd_hist_col = next((c for c in enriched.columns if c.startswith("MACDh_")), "")
        sma_5 = last.get("SMA_5")
        sma_50 = last.get("SMA_50")
        sma_200 = last.get("SMA_200")
        
        # --- Trend Model ---
        trend_score = 50.0
        if sma_5 is not None and sma_50 is not None:
            if sma_5 > sma_50:
                trend_score += 15
            else:
                trend_score -= 15
        if sma_50 is not None and sma_200 is not None:
            if sma_50 > sma_200:
                trend_score += 20
            else:
                trend_score -= 10
        if sma_50 is not None and close > sma_50:
            trend_score += 15
            
        if cs_rank > 80.0:
            trend_score += 10
        elif cs_rank < 20.0:
            trend_score -= 10
            
        trend_score = max(0.0, min(100.0, trend_score))
        if trend_score >= 80:
            factors.append("TREND 80/100 (Strong)")
        elif trend_score <= 30:
            factors.append("TREND 30/100 (Bearish)")
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
        if not is_historical and sensor is not None:
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
        fundamentals = self._load_fundamentals_from_sources(ticker, pdb=self.portfolio_db)
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
        if not self.offline_mode and not is_historical:
            import requests
            
            # 1. Try Institutional Finlight API First
            finlight_key = os.getenv("FINLIGHT_API_KEY")
            raw_news_data = []
            fetched_via_finlight = False
            
            if finlight_key:
                try:
                    resp = requests.get(
                        f"https://api.finlight.me/v1/news?ticker={ticker}",
                        headers={"Authorization": f"Bearer {finlight_key}"},
                        timeout=5.0
                    )
                    resp.raise_for_status()
                    finlight_news = resp.json()
                    
                    if isinstance(finlight_news, list):
                        for n in finlight_news[:6]:
                            title = (n.get("title") or n.get("headline") or "").strip()
                            if title:
                                headlines.append(title)
                                raw_news_data.append({
                                    "url": str(n.get("id", n.get("url", ""))),
                                    "title": title,
                                    "ticker": ticker,
                                    "date_published": n.get("date", n.get("published_at", "")),
                                    "provider": "Finlight"
                                })
                        fetched_via_finlight = True
                except Exception as e:
                    logger.error(f"Finlight API failed for {ticker}: {e}")
            
            # 2. Fallback to yfinance if Finlight failed or not configured
            if not fetched_via_finlight:
                if finlight_key:
                    try:
                        from logging_setup import update_pipeline_status
                        update_pipeline_status({
                            "data_degraded_mode": True,
                            "degraded_reason": "Finlight API failed. Falling back to yfinance for news."
                        })
                        logger.error("DEGRADED MODE: Finlight API unavailable. Falling back to yfinance.")
                    except Exception:
                        pass
                
                try:
                    if yf is not None:
                        raw_news = yf.Ticker(ticker).news or []
                        for n in raw_news[:6]:
                            content = n.get("content", n)
                            title = (content.get("title") or n.get("title") or "").strip()
                            if title:
                                headlines.append(title)
                                raw_news_data.append({
                                    "url": str(content.get("providerPublishTime", "")), 
                                    "title": title,
                                    "ticker": ticker,
                                    "date_published": "",
                                    "provider": "Yahoo Finance"
                                })
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
                
                # Update news score in the raw data for SQLite
                for n_dict in raw_news_data:
                    n_dict["sentiment_score"] = news_score
                
                try:
                    from sqlite_portfolio import PortfolioDB
                    db = PortfolioDB()
                    db.save_news(raw_news_data)
                except Exception as exc:
                    logger.debug("Failed to save news to SQLite: %s", exc)
        if news_score > 30:
            news_mod = 15.0
            factors.append(f"NEWS+10 Bullish sentiment ({news_score:.0f})")
        elif news_score < -30:
            news_mod = -20.0
            factors.append(f"NEWS-15 Bearish sentiment ({news_score:.0f})")
        news_component = max(0.0, min(100.0, 50.0 + news_mod))

        # ML modifier (Phase 60): XGBoost probability as 5th context factor (Regime-conditional + Conformal).
        ml_component = 50.0
        ml_prob: float | None = None
        ml_interval_str = ""
        try:
            from ml_feature_store import build_ml_feature_row  # noqa: WPS433
            from ml_trainer import predict_probability_with_shap  # noqa: WPS433
            
            sector = self.portfolio_db.get_sector(ticker) if hasattr(self.portfolio_db, "get_sector") else "Unknown"
            sec_mean = daily_sector_means.get(sector, 0.0) if daily_sector_means else 0.0

            feat_row = build_ml_feature_row(
                ticker,
                close=enriched["Close"],
                reason="",
                pdb=None,
                offline_mode=is_historical,
                sector_mean_ret1d=sec_mean,
            )
            ml_prob, _, ml_interval = predict_probability_with_shap(feat_row, horizon="tactical", regime=self.regime)
            if ml_prob is not None:
                ml_component = float(ml_prob) * 100.0
                if ml_interval:
                    ml_interval_str = f" ±{abs((ml_interval[1] - ml_prob)*100):.1f}%"
                    
                if ml_prob >= 0.65:
                    factors.append(f"ML+5 prob={ml_prob:.2f}{ml_interval_str}")
                elif ml_prob <= 0.35:
                    factors.append(f"ML-5 prob={ml_prob:.2f}{ml_interval_str}")
        except Exception as exc:  # noqa: BLE001
            logger.debug("ML modifier skipped for %s: %s", ticker, exc)

        # Polymarket removed from per-ticker scoring (Phase 42): macro-only.
        poly_component = 50.0

        # Context model: fundamentals + insiders + news + ML ensemble.
        context_score = (
            0.40 * fundamentals_score
            + 0.20 * insider_score
            + 0.20 * news_component
            + 0.20 * ml_component
        )
        context_score = max(0.0, min(100.0, context_score))

        try:
            from ensemble_optimizer import DynamicEnsemble
            dyn = DynamicEnsemble()
            weights = dyn.get_optimized_weights()
            w_trend = weights["heuristic_trend_weight"]
            w_mr = weights["heuristic_mr_weight"]
            w_brk = weights["heuristic_breakout_weight"]
            w_ctx = weights["heuristic_context_weight"]
            w_ml_total = weights["ml_total_weight"]
        except Exception as e:
            w_trend, w_mr, w_brk, w_ctx = 0.30, 0.25, 0.20, 0.25
            w_ml_total = 0.0

        # Final ensemble as weighted average of model committee.
        # If w_ml_total > 0, we blend the heuristic total and the ML tactical/structural scores.
        heuristic_total = (
            w_trend * trend_score
            + w_mr * mr_score
            + w_brk * breakout_score
            + w_ctx * context_score
        )
        
        if w_ml_total > 0.0:
            # We already computed ml_component which is (ml_tactical + ml_structural)/2
            total = heuristic_total + (ml_component * w_ml_total)
        else:
            total = heuristic_total
            
        total = float(max(0.0, min(100.0, total)))

        # Phase 55: Boost Achats d'Insidés & PEA-PME
        pb = fundamentals.get("pb_ratio")
        if cluster >= 3 and (rsi_14 is not None and not pd.isna(rsi_14) and float(rsi_14) < 40) and (pb is not None and pb < 1.5):
            total = float(max(0.0, min(100.0, total * 1.35)))
            factors.append("BOOST x1.35 (Insider+RSI+PB)")
            
        # CUSUM Structural Breakdown Detection
        from quantitative_math import detect_cusum_downward_break
        if not history.empty and "Close" in history.columns:
            recent_returns = history["Close"].tail(60).pct_change().dropna()
            if detect_cusum_downward_break(recent_returns):
                total -= 25.0
                factors.append("⚠️ CUSUM VETO (Structural Breakdown)")

        return {
            # Backward-compatible keys consumed by dashboard/orchestrator.
            "mean_reversion": int(round(mr_score * w_mr)),
            "volume_breakout": int(round(breakout_score * w_brk)),
            "insider": int(round(insider_score * 0.20)),
            "institutional": int(round(fundamentals_score * 0.40)),
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
                "ml": float(ml_component),
            },
        }

    def generate_raw_signals(
        self,
        timeseries_db: Any,
        tickers: list[str],
        conviction_floor: float | None = None,
        daily_sector_means: dict[str, float] | None = None,
    ) -> list[Signal]:
        """Evaluate each ticker; emit BUY when ensemble conviction ≥ floor.

        Args:
            timeseries_db: ``TimeSeriesDB`` with ``get_historical_prices``.
            tickers: Universe symbols.
            conviction_floor: Minimum total points to emit.

        Returns:
            List[Signal]: PENDING BUYs with score = conviction total.
        """
        signals: list[Signal] = []
        macro = self._macro_sensor()
        
        # --- Active Degraded Mode Risk Enforcement ---
        try:
            import json
            status_path = _PROJECT_ROOT / "database" / "pipeline_status.json"
            if status_path.exists():
                with open(status_path, "r", encoding="utf-8") as f:
                    pipe_status = json.load(f)
                if pipe_status.get("data_degraded_mode", False):
                    old_floor = conviction_floor
                    conviction_floor = max(conviction_floor, 85.0)
                    logger.warning(
                        "Data Degraded Mode active! Raising minimum conviction threshold from %.1f to %.1f.", 
                        old_floor, conviction_floor
                    )
        except Exception as exc:
            logger.debug("Failed to read pipeline_status.json for degraded mode check: %s", exc)

        
        # Precompute cross-sectional momentum ranks for relative rotation
        try:
            from cross_sectional import CrossSectionalScorer
            cs_scorer = CrossSectionalScorer(timeseries_db)
            cs_ranks = cs_scorer.rank_universe(tickers, days=126)
        except Exception as exc:
            logger.debug("Cross-sectional scoring failed: %s", exc)
            cs_ranks = {}
            
        from market_regime import MarketRegimeClassifier
        mr_classifier = MarketRegimeClassifier()

        def _eval_ticker(ticker: str) -> Signal | None:
            df = timeseries_db.get_historical_prices(ticker, days=252)
            if df is None or df.empty or len(df) < _MIN_ROWS:
                return None
            
            cs_rank = float(cs_ranks.get(ticker, 50.0))
            conv = self.evaluate(ticker, df, macro_sensor=macro, cs_rank=cs_rank, daily_sector_means=daily_sector_means)
            total = float(conv.get("total") or 0.0)
            actual_floor = conviction_floor if conviction_floor is not None else self.conviction_floor
            
            if total < float(actual_floor):
                return None
                
            # Meta-Labeling Arbitrator
            meta_prob = None
            try:
                from ml_feature_store import build_ml_feature_row
                from ml_trainer import predict_meta_probability
                
                sector = self.portfolio_db.get_sector(ticker) if hasattr(self.portfolio_db, "get_sector") else "Unknown"
                sec_mean = daily_sector_means.get(sector, 0.0) if daily_sector_means else 0.0
                
                features = build_ml_feature_row(ticker, df, pdb=self.portfolio_db, offline_mode=self.offline_mode, sector_mean_ret1d=sec_mean)
                meta_prob = predict_meta_probability(features)
                
                if meta_prob is not None and meta_prob < 0.65:
                    logger.info("Signal %s vetoed by Meta-Labeler (prob=%.2f < 0.65)", ticker, meta_prob)
                    return None
            except Exception as exc:
                logger.warning("Meta-Labeling failed for %s: %s", ticker, exc)

            mr = conv["model_scores"]["mean_reversion_model"]
            mom = conv["model_scores"]["trend_model"]
            qv = conv["context_breakdown"]["fundamentals"]
            ins = conv["context_breakdown"]["insiders"]
            news = conv["news_modifier"]
            polymarket = conv["polymarket_modifier"]

            reason = (
                f"Conviction {total:.0f}/100 ≥ {actual_floor:.0f} | "
                f"MR {mr:.0f} | Mom {mom:.0f} | Q/V {qv:.0f} | Ins {ins:.0f}"
            )
            if news != 0:
                reason += f" | News {news:+.0f}"
            if polymarket != 0:
                reason += f" | Poly {polymarket:+.0f}"
            if meta_prob is not None:
                reason += f" | Meta-Label {meta_prob*100:.0f}%"
                
            return Signal(
                id=str(uuid.uuid4()),
                ticker=ticker,
                signal_type=SignalType.BUY,
                status=SignalStatus.PENDING,
                score=total,
                target_qty=None,
                created_at=datetime.now(timezone.utc),
                reason=reason,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_eval_ticker, t): t for t in tickers}
            for fut in concurrent.futures.as_completed(futures):
                try:
                    sig = fut.result()
                    if sig is not None:
                        signals.append(sig)
                        logger.info(
                            "BUY signal %s for %s (conviction=%.0f).",
                            sig.id[:8], sig.ticker, sig.score,
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Eval failed for %s: %s", futures[fut], exc)

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
