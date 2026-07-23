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

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import yaml

# --- Cross-package imports (directories start with digits) --------------------
_ROOT = Path(__file__).resolve().parent.parent
for _sub in ("01_memory_core", "03_risk_portfolio", "04_orchestrator_ai"):
    sys.path.insert(0, os.path.join(str(_ROOT), _sub))

from data_models import PortfolioState, Signal, SignalStatus  # noqa: E402
from correlation_firewall import CorrelationFirewall  # noqa: E402
from pea_position_sizer import PeaSizer  # noqa: E402
from macro_veto import MacroVetoEngine  # noqa: E402
from earnings_blackout import EarningsBlackoutEngine  # noqa: E402

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
        """Initialize the sub-engines that make up the cascade.

        Args:
            config_dir: Path to the ``config`` directory. Defaults to
                ``<project_root>/config``.
            portfolio_db: Optional ``PortfolioDB`` (state is passed explicitly to
                ``process_raw_signals``; kept for symmetry/future use).
            timeseries_db: A ``TimeSeriesDB`` used by the correlation firewall.
        """
        config_path = Path(config_dir) if config_dir else _DEFAULT_CONFIG_DIR
        self.config_dir = config_path
        self.portfolio_db = portfolio_db
        self.timeseries_db = timeseries_db

        risk_path = config_path / "risk_params.yaml"
        risk: dict = {}
        if risk_path.exists():
            with open(risk_path, "r", encoding="utf-8") as fh:
                risk = yaml.safe_load(fh) or {}
        self.core_ticker: str = str(risk.get("CORE_TICKER", "CW8.PA"))
        self.max_positions_total: int = int(risk.get("MAX_POSITIONS_TOTAL", 12))
        self.min_liquidity_adv: float = float(risk.get("MIN_LIQUIDITY_ADV", 50_000))

        self.macro_veto = MacroVetoEngine(config_path)
        self.earnings_blackout = EarningsBlackoutEngine(config_path)
        self.firewall = CorrelationFirewall(config_path)
        self.sizer = PeaSizer(config_path)

        logger.debug("SignalOrchestrator initialized with config at %s", config_path)

    @staticmethod
    def _reject(signal: Signal, reason: str) -> Signal:
        """Mark a signal REJECTED and append the reason."""
        signal.status = SignalStatus.REJECTED
        signal.reason = f"{signal.reason} | {reason}".strip(" |")
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
    ) -> List[Signal]:
        """Run each raw signal through the full decision cascade."""
        today = datetime.now(timezone.utc).date()
        processed: List[Signal] = []
        satellite_lines = self._satellite_line_count(portfolio)

        # Market-wide panic brake: evaluated once for the whole batch.
        vix_ok = self.firewall.check_vix_panic(vix_level) if vix_level is not None else True

        for signal in raw_signals:
            ticker = signal.ticker

            # --- Check 0: we need a live price to size anything ---
            price = current_prices.get(ticker)
            if price is None or price <= 0:
                processed.append(self._reject(signal, "REJECTED: No current price"))
                continue

            # --- Check 0b: VIX panic veto (market-wide emergency brake) ---
            if not vix_ok:
                processed.append(
                    self._reject(
                        signal,
                        f"REJECTED: VIX panic (V2TX={vix_level:.1f}) - "
                        "satellite buys frozen",
                    )
                )
                continue

            # --- Check 0c: Quality / EPS < 0 (Orchestrator-level veto) ---
            try:
                from technical_scorer import SignalGenerator  # noqa: WPS433

                if not SignalGenerator().is_profitable(ticker):
                    processed.append(
                        self._reject(signal, "REJECTED: EPS < 0 (quality veto)")
                    )
                    continue
            except Exception:  # noqa: BLE001 - never block the cascade on EPS outage
                pass

            # --- Check 1: Macro veto (cheapest - runs first) ---
            vetoed, veto_reason = self.macro_veto.check_veto(today)
            if vetoed:
                processed.append(self._reject(signal, f"REJECTED: {veto_reason}"))
                continue

            # --- Check 1b: Earnings / dividend blackout (per ticker) ---
            earn_veto, earn_reason = self.earnings_blackout.check_veto(ticker, today)
            if earn_veto:
                processed.append(self._reject(signal, f"REJECTED: {earn_reason}"))
                continue

            # --- Check 1c: Max simultaneous satellite lines ---
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

            # --- Check 1d: Minimum liquidity (ADV €) ---
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

            # --- Check 2a: Sector concentration limit (cheap arithmetic) ---
            if not self.firewall.check_sector_limit(ticker, portfolio):
                processed.append(
                    self._reject(signal, "REJECTED: Sector weight limit reached")
                )
                continue

            # --- Check 2b: Correlation firewall (heavy Pearson) ---
            ok, corr_reason = self.firewall.check_correlation(
                ticker, portfolio, self.timeseries_db
            )
            if not ok:
                processed.append(self._reject(signal, f"REJECTED: {corr_reason}"))
                continue

            # --- Check 3: PEA position sizing (volatility-adjusted) ---
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
