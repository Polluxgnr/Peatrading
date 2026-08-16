"""Signal Priority Cascade for PEA Sniper Terminal V-Prime.

The strict conductor. Raw signals flow through an ordered, CPU-optimal cascade:

    0. Price sanity      (reject non-positive / missing marks)
    1. VIX panic         (market-wide emergency brake — CorrelationFirewall)
    2. Macro Veto        (cheap date lookup)
    2b. Earnings blackout (per-ticker corporate calendar)
    2c. Max positions    (satellite line count cap)
    2d. Min liquidity    (ADV € floor)
    3. Sector limit      (cheap arithmetic)
    4. Correlation       (heavy Pearson math — only if still alive)
    5. PEA sizing        (integer shares vs available cash)

This is the ONLY module that finalizes a signal's ``status``, ``target_qty``
and ``reason``. Pure logical routing: no LLMs, no APIs. All paths use
``pathlib``/``os.path`` for cross-platform compatibility.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Union

import pandas as pd
import yaml


# --- Cross-package imports (directories start with digits) --------------------
_ROOT = Path(__file__).resolve().parent.parent
for _sub in ("00_data_sensors", "01_memory_core", "02_quant_engine", "03_risk_portfolio", "04_orchestrator_ai"):
    sys.path.insert(0, os.path.join(str(_ROOT), _sub))

from data_models import PortfolioState, Signal, SignalStatus  # noqa: E402
from correlation_firewall import CorrelationFirewall  # noqa: E402
from pea_position_sizer import PeaSizer  # noqa: E402
from macro_veto import MacroVetoEngine  # noqa: E402
from earnings_blackout import EarningsBlackoutEngine  # noqa: E402
from drawdown_breaker import DrawdownBreaker  # noqa: E402
from fundamentals_api import FundamentalsSensor  # noqa: E402
from risk_config import load_and_validate_risk_params  # noqa: E402
from market_regime import VolatilityRegimeSentinel  # noqa: E402

try:
    from amf_short_scraper import AmfShortScraper
except ImportError:
    try:
        from scrapers.amf_short_scraper import AmfShortScraper
    except ImportError:
        AmfShortScraper = None

try:
    from openfigi_mapper import OpenFigiMapper
except ImportError:
    OpenFigiMapper = None

try:
    from ml_trainer import predict_probability_with_shap, predict_anomaly
except ImportError:
    predict_probability_with_shap = None
    predict_anomaly = None

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_DIR = _ROOT / "config"


class SignalOrchestrator:
    """Routes raw signals through veto, correlation and sizing checks."""

    def __init__(
        self,
        config_dir: str | Path | None = None,
        portfolio_db=None,
        timeseries_db=None,
    ) -> None:
        """Initialize the sub-engines that make up the cascade."""
        config_path = Path(config_dir) if config_dir else _DEFAULT_CONFIG_DIR
        self.config_dir = config_path
        self.portfolio_db = portfolio_db
        self.timeseries_db = timeseries_db

        risk_cfg = load_and_validate_risk_params(config_path / "risk_params.yaml")
        self.risk_cfg = risk_cfg
        self.core_ticker: str = str(risk_cfg.CORE_TICKER)
        self.max_positions_total: int = int(risk_cfg.MAX_POSITIONS_TOTAL)
        self.min_liquidity_adv: float = float(risk_cfg.MIN_LIQUIDITY_ADV)

        self.macro_veto = MacroVetoEngine(config_path)
        self.earnings_blackout = EarningsBlackoutEngine(config_path)
        self.firewall = CorrelationFirewall(config_path)
        self.sizer = PeaSizer(config_path)
        self.drawdown_breaker = DrawdownBreaker(
            daily_max_loss=risk_cfg.DAILY_MAX_LOSS_PCT,
            weekly_max_loss=risk_cfg.WEEKLY_MAX_LOSS_PCT,
            monthly_max_loss=risk_cfg.MONTHLY_MAX_LOSS_PCT,
        )
        self.fundamentals_sensor = FundamentalsSensor()
        self.vol_sentinel = VolatilityRegimeSentinel(window=252)

        if AmfShortScraper is not None:
            self.amf_scraper = AmfShortScraper()
        else:
            self.amf_scraper = None

        if OpenFigiMapper is not None:
            self.figi_mapper = OpenFigiMapper(config_path.parent / "database" / "portfolio.db")
        else:
            self.figi_mapper = None


        logger.debug("SignalOrchestrator initialized with validated config at %s", config_path)

    @staticmethod
    def _reject(signal: Signal, reason: str) -> Signal:
        signal.status = SignalStatus.REJECTED
        signal.reason = (f"{signal.reason} | {reason}").strip(" |")
        signal.target_qty = 0
        if hasattr(signal, "lineage") and isinstance(signal.lineage, dict):
            signal.lineage["rejection_reason"] = reason
            signal.lineage["status"] = SignalStatus.REJECTED.value
        logger.info("%s %s: %s", signal.ticker, signal.id[:8], reason)
        return signal

    def _historical_volatility(self, ticker: str, days: int = 60) -> float | None:
        """Annualized stdev of daily returns for a ticker (or ``None``).

        Args:
            ticker: Ticker to measure.
            days: Lookback window in trading days.

        Returns:
            float | None: Annualized volatility (e.g. 0.28), or ``None`` when
            history is unavailable.
        """
        if self.timeseries_db is None:
            return None
        try:
            df = self.timeseries_db.get_historical_prices(ticker, days=days)
            if df is None or df.empty or "Close" not in df or len(df) < 10:
                return None
            returns = df["Close"].astype(float).pct_change().dropna()
            if returns.empty:
                return None
            return float(returns.std() * (252 ** 0.5))
        except Exception:  # noqa: BLE001
            logger.debug("Volatility unavailable for %s.", ticker)
            return None

    def _avg_daily_euro_volume(self, ticker: str, days: int = 20) -> float | None:
        """Approximate ADV in EUR = mean(Close * Volume) over ``days``."""
        if self.timeseries_db is None:
            return None
        try:
            df = self.timeseries_db.get_historical_prices(ticker, days=days)
            if df is None or df.empty:
                return None
            if "Close" not in df.columns or "Volume" not in df.columns:
                return None
            close = df["Close"].astype(float)
            vol = df["Volume"].astype(float)
            adv = (close * vol).dropna()
            if adv.empty:
                return None
            return float(adv.mean())
        except Exception:  # noqa: BLE001
            return None

    def _get_vix_history(self, days: int = 252) -> pd.Series | None:
        """Fetch historical VIX/V2TX series for rolling volatility percentile ranking."""
        if self.timeseries_db is not None:
            try:
                for sym in ("^V2TX", "^VIX"):
                    df = self.timeseries_db.get_historical_prices(sym, days=days)
                    if df is not None and not df.empty and "Close" in df.columns:
                        s = df["Close"].dropna().astype(float)
                        if len(s) >= 10:
                            return s
            except Exception as exc:
                logger.debug("Failed to load VIX history from TimeSeriesDB: %s", exc)
        return None

    def _satellite_line_count(self, portfolio: PortfolioState) -> int:
        return sum(
            1
            for p in portfolio.positions
            if p.qty_shares > 0 and p.ticker != self.core_ticker
        )

    def process_raw_signals(
        self,
        raw_signals: List[Signal],
        portfolio: PortfolioState,
        current_prices: Dict[str, float],
        vix_level: float | None = None,
        data_degraded_mode: bool = False,
    ) -> List[Signal]:
        """Run each raw signal through the full decision cascade."""
        today = datetime.now(timezone.utc).date()
        processed: List[Signal] = []
        satellite_lines = self._satellite_line_count(portfolio)

        # =====================================================================
        # STEP 0: Multi-Horizon Loss Limits & Kinetic Drawdown Breaker (FIRST)
        # =====================================================================
        # Evaluated before any single-name logic or VIX.
        kinetic_mult, dd_reason = self.drawdown_breaker.check(portfolio.total_equity)
        if kinetic_mult <= 0.0:
            logger.warning("HALT: Kinetic Drawdown Breaker triggered (%s). All new buys frozen.", dd_reason)
            for signal in raw_signals:
                processed.append(self._reject(signal, f"REJECTED: {dd_reason}"))
            return processed

        # =====================================================================
        # Continuous Volatility Regime & Dynamic Conviction Floor (Brain Sentinel)
        # =====================================================================
        vix_hist = self._get_vix_history(days=252)
        cur_vix = float(vix_level) if vix_level is not None else 16.0
        base_threshold = int(self.risk_cfg.SIGNAL_BUY_THRESHOLD)

        vol_eval = self.vol_sentinel.evaluate_vix_regime(
            vix_history=vix_hist,
            current_vix=cur_vix,
            base_floor=base_threshold,
        )

        regime_name = vol_eval.get("regime", "NORMAL")
        pct_rank = vol_eval.get("percentile", 50.0)
        eff_floor = float(vol_eval.get("effective_floor", base_threshold))
        is_panic = vol_eval.get("is_panic", False)

        # Conviction floor enforcement: raised in elevated vol or degraded mode
        conviction_floor = max(85.0, eff_floor) if data_degraded_mode else eff_floor

        logger.info(
            "Continuous Volatility Regime: %s (VIX=%.1f, Percentile=%.1f%%) -> Floor set to %.0f",
            regime_name,
            cur_vix,
            pct_rank,
            conviction_floor,
        )

        vix_ok = not is_panic

        for signal in raw_signals:
            ticker = signal.ticker

            # --- Check 0a: Conviction Floor (Enforced in Degraded Mode) ---
            if signal.score < conviction_floor:
                processed.append(
                    self._reject(
                        signal,
                        f"REJECTED: Score {signal.score:.1f} below conviction floor "
                        f"({conviction_floor:.0f}{' [DEGRADED MODE]' if data_degraded_mode else ''})",
                    )
                )
                continue

            # --- Check 0b: Price sanity ---
            price = current_prices.get(ticker)
            if price is None or price <= 0:
                processed.append(self._reject(signal, "REJECTED: No current price"))
                continue

            # --- Check 0c: VIX panic veto (market-wide emergency brake) ---
            if not vix_ok:
                processed.append(
                    self._reject(
                        signal,
                        f"REJECTED: VIX panic (V2TX={vix_level:.1f}) - "
                        "satellite buys frozen",
                    )
                )
                continue

            # --- Check 1: Macro veto (economic calendar) ---
            vetoed, veto_reason = self.macro_veto.check_veto(today)
            if vetoed:
                processed.append(self._reject(signal, f"REJECTED: {veto_reason}"))
                continue

            # --- Check 1b: Earnings / dividend blackout (per ticker) ---
            earn_veto, earn_reason = self.earnings_blackout.check_veto(ticker, today)
            if earn_veto:
                processed.append(self._reject(signal, f"REJECTED: {earn_reason}"))
                continue

            # --- Check 1c: Strict Piotroski F-Score Veto (< 4) ---
            if self.fundamentals_sensor is not None and ticker != self.core_ticker:
                piot_res = self.fundamentals_sensor.calculate_piotroski_score(ticker)
                if piot_res is not None and isinstance(piot_res, tuple) and len(piot_res) == 2:
                    piot_score, _ = piot_res
                    if piot_score is not None and piot_score < 4:
                        processed.append(
                            self._reject(
                                signal,
                                f"REJECTED: Low Piotroski quality ({piot_score}/9 < 4)",
                            )
                        )
                        continue


            # --- Check 1d: Max simultaneous satellite lines ---
            already_held = any(p.ticker == ticker for p in portfolio.positions)
            if not already_held and satellite_lines >= self.max_positions_total:
                processed.append(
                    self._reject(
                        signal,
                        f"REJECTED: Max satellite positions "
                        f"({self.max_positions_total}) reached",
                    )
                )
                continue

            # --- Check 1e: Minimum liquidity (ADV €) ---
            adv = self._avg_daily_euro_volume(ticker)
            if adv is not None and adv < self.min_liquidity_adv:
                processed.append(
                    self._reject(
                        signal,
                        f"REJECTED: Illiquid (ADV €{adv:,.0f} < "
                        f"{self.min_liquidity_adv:,.0f})",
                    )
                )
                continue

            # --- Check 1f: Short Interest Veto (AMF BDIF) ---
            short_interest = 0.0
            if self.amf_scraper is not None and self.figi_mapper is not None and ticker != self.core_ticker:
                try:
                    isin = self.figi_mapper.ticker_to_isin(ticker)
                    if isin:
                        short_interest = float(self.amf_scraper.get_short_interest(isin))
                except Exception as exc:
                    logger.debug("Failed to check AMF short interest for %s: %s", ticker, exc)

            if isinstance(signal.lineage, dict):
                signal.lineage["short_interest"] = short_interest

            if short_interest > 3.0:
                processed.append(
                    self._reject(
                        signal,
                        f"REJECTED: High Short Interest ({short_interest:.1f}%) - Toxic asset risk",
                    )
                )
                continue

            # --- Check 2a & 2b: Sector concentration & Correlation (Indicator Warnings, No Hard Veto) ---
            sector_res = self.firewall.check_sector_limit(ticker, portfolio)
            if isinstance(sector_res, list):
                sector_warnings = sector_res
            elif isinstance(sector_res, bool):
                sector_warnings = [f"⚠️ Sector exposure limit exceeded for {ticker}"] if not sector_res else []
            else:
                sector_warnings = []

            corr_res = self.firewall.check_correlation(ticker, portfolio, self.timeseries_db)
            if isinstance(corr_res, list):
                corr_warnings = corr_res
            elif isinstance(corr_res, tuple) and len(corr_res) == 2:
                ok, msg = corr_res
                corr_warnings = [f"⚠️ {msg}"] if not ok else []
            elif isinstance(corr_res, bool):
                corr_warnings = [f"⚠️ Correlation limit exceeded for {ticker}"] if not corr_res else []
            else:
                corr_warnings = []

            all_risk_warnings = sector_warnings + corr_warnings

            if all_risk_warnings:
                if signal.lineage is None:
                    signal.lineage = {}
                signal.lineage["risk_warnings"] = all_risk_warnings
                warn_suffix = " · " + " · ".join(all_risk_warnings)
                if warn_suffix not in signal.reason:
                    signal.reason += warn_suffix



            # --- Check 2c: ML Predictive Veto (XGBoost + Isolation Forest) ---
            if predict_anomaly is not None and predict_probability_with_shap is not None:
                # Determine current market regime from vix_level (default to VOLATILE)
                if vix_level is not None:
                    if vix_level < 17.5:
                        current_regime = "BULL"
                    elif vix_level > 23.0:
                        current_regime = "VOLATILE"
                    else:
                        current_regime = "BEAR"
                else:
                    current_regime = "VOLATILE"

                feat_snapshot = (
                    signal.lineage
                    if hasattr(signal, "lineage") and isinstance(signal.lineage, dict)
                    else {}
                )

                # Anomaly Detection via Isolation Forest
                is_anomaly = predict_anomaly(feat_snapshot)
                if is_anomaly is True:
                    processed.append(
                        self._reject(
                            signal,
                            "REJECTED: Structural Anomaly detected by Isolation Forest",
                        )
                    )
                    continue

                # Win Probability & SHAP Scoring via XGBoost
                proba, shap_dict, interval = predict_probability_with_shap(
                    feat_snapshot, horizon="tactical", regime=current_regime
                )
                if proba is not None:
                    if proba < 0.50:
                        processed.append(
                            self._reject(
                                signal,
                                f"REJECTED: ML Win Probability too low ({proba * 100:.1f}%)",
                            )
                        )
                        continue

                    # Inject ML inference features into signal lineage
                    if not hasattr(signal, "lineage") or not isinstance(signal.lineage, dict):
                        signal.lineage = {}
                    signal.lineage["ml_probability"] = proba
                    signal.lineage["shap_values"] = shap_dict
                    signal.lineage["ml_interval"] = interval

            # --- Check 3: PEA position sizing (volatility & kinetic adjusted) ---
            # TODO: Re-enable RL Sizer only when SizingEnv is connected to real historical trajectories
            # rather than synthetic noise. Current sizing strictly relies on deterministic Half-Kelly,
            # Inverse Volatility, and the Kinetic Brake multiplier.
            hist_vol = self._historical_volatility(ticker)
            target_qty, sizing = self.sizer.size_with_explanation(
                signal, portfolio, price, historical_volatility=hist_vol
            )
            if target_qty <= 0:
                processed.append(
                    self._reject(signal, "REJECTED: Insufficient cash for 1 share")
                )
                continue

            signal.target_qty = target_qty
            signal.status = SignalStatus.APPROVED
            vol = sizing.get("historical_volatility")
            vol_txt = f"{vol * 100:.1f}%" if isinstance(vol, (int, float)) and vol else "n/a"
            signal.reason = (
                f"{signal.reason} | APPROVED: {target_qty} share(s) @ {price:.2f} EUR "
                f"| sizing: Kelly {sizing.get('kelly_fraction', 0):.2f} × "
                f"score {signal.score:.0f}/100 · vol {vol_txt} "
                f"(×{sizing.get('vol_factor', 1):.2f}) · "
                f"poids {sizing.get('weight_pct', 0):.2f}% equity "
                f"({sizing.get('notional', 0):,.0f} €)"
            ).strip(" |")

            if hasattr(signal, "lineage") and isinstance(signal.lineage, dict):
                signal.lineage.update({
                    "status": SignalStatus.APPROVED.value,
                    "target_qty": target_qty,
                    "execution_price": price,
                    "sizing": sizing,
                    "kinetic_multiplier": kinetic_mult,
                    "vix": vix_level,
                })

            logger.info(
                "APPROVED %s: %d share(s) @ %.2f EUR (score=%.1f, weight=%.2f%%).",
                ticker,
                target_qty,
                price,
                signal.score,
                sizing.get("weight_pct", 0),
            )
            if not already_held:
                satellite_lines += 1
            processed.append(signal)

        return processed


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    from data_models import Position, SignalType

    class _MockTSDB:
        """Returns uncorrelated price history so the firewall passes."""

        def get_historical_prices(self, ticker: str, days: int = 60):
            import numpy as np
            import pandas as pd

            dates = pd.date_range("2026-01-01", periods=days, freq="B")
            seed = sum(ord(c) for c in ticker)
            rng = np.random.default_rng(seed)
            close = np.cumsum(rng.normal(0, 1, days)) + 100
            return pd.DataFrame({"Ticker": ticker, "Date": dates, "Close": close})

    orch = SignalOrchestrator(timeseries_db=_MockTSDB())

    portfolio = PortfolioState(
        cash_available=10_000.0,
        total_equity=20_000.0,
        positions=[
            Position(ticker="MC.PA", qty_shares=2, avg_entry_price=600,
                     current_price=600, sector="Luxury"),
        ],
        last_updated=datetime.now(timezone.utc),
    )

    raw = [
        Signal(ticker="AI.PA", signal_type=SignalType.BUY, score=90.0,
               reason="Mean-reversion setup"),   # Industrials-adjacent -> APPROVE
        Signal(ticker="KER.PA", signal_type=SignalType.BUY, score=85.0,
               reason="Mean-reversion setup"),   # Luxury, but firewall/sizing decide
        Signal(ticker="OR.PA", signal_type=SignalType.BUY, score=70.0,
               reason="Mean-reversion setup"),   # Luxury
    ]
    prices = {"AI.PA": 180.0, "KER.PA": 250.0, "OR.PA": 380.0}

    def _show(title, signals):
        print(f"\n--- {title} ---")
        for s in signals:
            qty = s.target_qty if s.target_qty is not None else "-"
            print(f"{s.ticker:8} {s.status.value:9} qty={qty}")
            print(f"         reason: {s.reason}")

    # Run 1: real calendar. Today (2026-07-15) is 1 day before an ECB decision,
    # so the macro veto correctly short-circuits every signal.
    print("Macro veto today?", orch.macro_veto.check_veto(datetime.now(timezone.utc).date()))
    _show("Cascade WITH macro veto active (real calendar)",
          orch.process_raw_signals([s.model_copy() for s in raw], portfolio, prices))

    # Run 2: simulate a macro-clear day by emptying the in-memory calendar, so
    # the downstream sector / correlation / sizing logic (and APPROVED path) show.
    orch.macro_veto.calendar = {}
    _show("Cascade on a macro-CLEAR day",
          orch.process_raw_signals([s.model_copy() for s in raw], portfolio, prices))
