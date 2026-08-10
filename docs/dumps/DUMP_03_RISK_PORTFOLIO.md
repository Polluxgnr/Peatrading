# PEA Pollux — Risk Sentinel, Pydantic Config, Drawdown Breakers & HRP Sizer
Generated: `2026-08-10 17:10 UTC` | File Count: `11`
Institutional Systematic Decision Support Architecture for French PEA.
---
## Included Files Index
- [03_risk_portfolio/__init__.py](#file-03_risk_portfolio-__init__-py)
- [03_risk_portfolio/alpha_tracker.py](#file-03_risk_portfolio-alpha_tracker-py)
- [03_risk_portfolio/correlation_firewall.py](#file-03_risk_portfolio-correlation_firewall-py)
- [03_risk_portfolio/drawdown_breaker.py](#file-03_risk_portfolio-drawdown_breaker-py)
- [03_risk_portfolio/equity_metrics.py](#file-03_risk_portfolio-equity_metrics-py)
- [03_risk_portfolio/hrp_sizer.py](#file-03_risk_portfolio-hrp_sizer-py)
- [03_risk_portfolio/limit_price_optimizer.py](#file-03_risk_portfolio-limit_price_optimizer-py)
- [03_risk_portfolio/monthly_rebalancer.py](#file-03_risk_portfolio-monthly_rebalancer-py)
- [03_risk_portfolio/pea_position_sizer.py](#file-03_risk_portfolio-pea_position_sizer-py)
- [03_risk_portfolio/risk_config.py](#file-03_risk_portfolio-risk_config-py)
- [03_risk_portfolio/stress_tester.py](#file-03_risk_portfolio-stress_tester-py)

---
## FILE: 03_risk_portfolio/__init__.py
```python
"""Risk Sentinel & Portfolio Construction package for PEA Pollux."""

from .correlation_firewall import CorrelationFirewall
from .drawdown_breaker import DrawdownBreaker
from .equity_metrics import compute_equity_metrics, max_drawdown, sharpe_ratio
from .hrp_sizer import HRPSizer
from .monthly_rebalancer import PortfolioRebalancer
from .pea_position_sizer import PeaSizer
from .risk_config import RiskParamsConfig, load_and_validate_risk_params
from .stress_tester import CrisisStressTester

__all__ = [
    "CorrelationFirewall",
    "CrisisStressTester",
    "DrawdownBreaker",
    "HRPSizer",
    "PeaSizer",
    "PortfolioRebalancer",
    "RiskParamsConfig",
    "compute_equity_metrics",
    "load_and_validate_risk_params",
    "max_drawdown",
    "sharpe_ratio",
]
```

## FILE: 03_risk_portfolio/alpha_tracker.py
```python
import logging
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timezone
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "01_memory_core"))

from sqlite_portfolio import PortfolioDB

logger = logging.getLogger(__name__)

# Static Risk-Free Rate (e.g. 3.0% annual)
RISK_FREE_RATE_ANNUAL = 0.03

def calculate_alpha_metrics(portfolio_curve: pd.DataFrame) -> dict:
    """
    Computes Jensen's Alpha, Beta, Information Ratio, and Tracking Error 
    for the portfolio against CW8.PA (MSCI World) and ^FCHI (CAC 40).
    
    Args:
        portfolio_curve: DataFrame with 'date' and 'equity' columns.
        
    Returns:
        A dictionary with the computed metrics.
    """
    if portfolio_curve is None or portfolio_curve.empty or len(portfolio_curve) < 2:
        return {
            "beta_cac": 0.0,
            "beta_msci": 0.0,
            "alpha_cac": 0.0,
            "alpha_msci": 0.0,
            "ir_cac": 0.0,
            "ir_msci": 0.0,
            "te_cac": 0.0,
            "te_msci": 0.0,
        }

    try:
        portfolio_curve = portfolio_curve.copy()
        portfolio_curve['date'] = pd.to_datetime(portfolio_curve['date'])
        portfolio_curve = portfolio_curve.sort_values('date').set_index('date')
        
        # Calculate daily returns of portfolio
        portfolio_curve['returns'] = portfolio_curve['equity'].pct_change().fillna(0.0)
        
        start_date = portfolio_curve.index.min().strftime('%Y-%m-%d')
        end_date = (portfolio_curve.index.max() + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
        
        # Download benchmarks
        benchmarks = yf.download(["CW8.PA", "^FCHI"], start=start_date, end=end_date, progress=False)["Close"]
        
        # Ensure it's a DataFrame
        if isinstance(benchmarks, pd.Series):
            benchmarks = benchmarks.to_frame()
            
        if benchmarks.empty:
            logger.warning("No benchmark data found for the given dates.")
            raise ValueError("No benchmark data.")
            
        bench_returns = benchmarks.pct_change().fillna(0.0)
        bench_returns.index = pd.to_datetime(bench_returns.index)
        
        # Merge portfolio returns and benchmark returns
        merged = portfolio_curve[['returns']].join(bench_returns, how='inner').fillna(0.0)
        
        if len(merged) < 2:
            raise ValueError("Not enough overlapping data points to calculate metrics.")
            
        port_ret = merged['returns']
        rf_daily = RISK_FREE_RATE_ANNUAL / 252.0
        
        metrics = {}
        
        for bm_ticker, bm_name in [("^FCHI", "cac"), ("CW8.PA", "msci")]:
            if bm_ticker not in merged.columns:
                metrics[f"beta_{bm_name}"] = 0.0
                metrics[f"alpha_{bm_name}"] = 0.0
                metrics[f"ir_{bm_name}"] = 0.0
                metrics[f"te_{bm_name}"] = 0.0
                continue
                
            bm_ret = merged[bm_ticker]
            
            # 1. Beta = Cov(Rp, Rb) / Var(Rb)
            cov = np.cov(port_ret, bm_ret)[0, 1]
            var = np.var(bm_ret, ddof=1)
            beta = cov / var if var > 0 else 0.0
            
            # 2. Jensen's Alpha = (Rp - Rf) - Beta * (Rb - Rf)
            ann_port_ret = (1 + port_ret.mean()) ** 252 - 1
            ann_bm_ret = (1 + bm_ret.mean()) ** 252 - 1
            alpha = (ann_port_ret - RISK_FREE_RATE_ANNUAL) - beta * (ann_bm_ret - RISK_FREE_RATE_ANNUAL)
            
            # 3. Tracking Error = StdDev(Rp - Rb)
            active_returns = port_ret - bm_ret
            te_daily = np.std(active_returns, ddof=1)
            te_annual = te_daily * np.sqrt(252)
            
            # 4. Information Ratio = (Rp - Rb) / TE
            ann_active_ret = ann_port_ret - ann_bm_ret
            ir = ann_active_ret / te_annual if te_annual > 0 else 0.0
            
            metrics[f"beta_{bm_name}"] = round(beta, 2)
            metrics[f"alpha_{bm_name}"] = round(alpha * 100, 2) # in %
            metrics[f"ir_{bm_name}"] = round(ir, 2)
            metrics[f"te_{bm_name}"] = round(te_annual * 100, 2) # in %
            
        return metrics

    except Exception as e:
        logger.exception("Error calculating alpha metrics: %s", e)
        return {
            "beta_cac": 0.0,
            "beta_msci": 0.0,
            "alpha_cac": 0.0,
            "alpha_msci": 0.0,
            "ir_cac": 0.0,
            "ir_msci": 0.0,
            "te_cac": 0.0,
            "te_msci": 0.0,
        }

if __name__ == "__main__":
    db = PortfolioDB()
    db.init_db()
    curve = db.get_equity_curve()
    print(calculate_alpha_metrics(curve))
```

## FILE: 03_risk_portfolio/correlation_firewall.py
```python
"""Correlation Firewall for PEA Sniper Terminal V-Prime.

Intercepts candidate signals and vetoes them when they would over-concentrate
the portfolio, either by sector weight or by price correlation with existing
holdings (Pearson, 60-day window).

Read-only layer: it reads ``PortfolioState`` and YAML config, and never writes
to any database. It does not mutate signals here (sizing does that in Phase 5.2).
"""

import logging
import os
import sys
from pathlib import Path
from typing import Dict, Tuple

import pandas as pd
import yaml

_CORE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "01_memory_core"
)
sys.path.insert(0, _CORE_DIR)

from data_models import PortfolioState  # noqa: E402

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_DIR = _PROJECT_ROOT / "config"
_CORR_WINDOW_DEFAULT = 60


class CorrelationFirewall:
    """Vetoes trades that breach sector-weight or correlation limits.

    Attributes:
        max_correlation: Max allowed Pearson correlation to any holding.
        max_sector_weight: Max fraction of equity allowed in one sector.
        max_single_position: Max fraction of equity for a single new position.
        corr_lookback_days: Trading-day window for Pearson correlation.
        ticker_sectors: Mapping of ticker -> sector from the universe file.
    """

    def __init__(self, config_path: str | Path | None = None) -> None:
        """Load risk limits and the ticker->sector map.

        Args:
            config_path: Path to the ``config`` directory (or a risk_params
                YAML file). Defaults to ``<project_root>/config``.
        """
        config_dir = self._resolve_config_dir(config_path)
        risk = self._load_yaml(config_dir / "risk_params.yaml")
        universe = self._load_yaml(config_dir / "pea_universe.yaml")

        self.max_correlation: float = float(risk["MAX_CORRELATION_TO_PORTFOLIO"])
        self.max_sector_weight: float = float(risk["MAX_SECTOR_WEIGHT_PCT"])
        self.max_single_position: float = float(risk["MAX_SINGLE_POSITION_PCT"])
        self.vix_panic_threshold: float = float(risk.get("VIX_PANIC_THRESHOLD", 30.0))
        self.corr_lookback_days: int = int(
            risk.get("CORRELATION_LOOKBACK_DAYS", _CORR_WINDOW_DEFAULT)
        )
        self.ticker_sectors: Dict[str, str] = self._build_sector_map(universe)

        logger.debug(
            "Firewall loaded: max_corr=%.2f max_sector=%.2f max_single=%.2f "
            "lookback=%d (%d tickers mapped).",
            self.max_correlation,
            self.max_sector_weight,
            self.max_single_position,
            self.corr_lookback_days,
            len(self.ticker_sectors),
        )

    @staticmethod
    def _resolve_config_dir(config_path: str | Path | None) -> Path:
        """Return the config directory from a dir path, file path, or default."""
        if config_path is None:
            return _DEFAULT_CONFIG_DIR
        path = Path(config_path)
        return path.parent if path.is_file() or path.suffix else path

    @staticmethod
    def _load_yaml(path: Path) -> dict:
        """Load a YAML file into a dict, raising a clear error if missing."""
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    @staticmethod
    def _build_sector_map(universe: dict) -> Dict[str, str]:
        """Flatten the universe YAML into a ticker -> sector dict."""
        mapping: Dict[str, str] = {}
        for sector, members in universe.get("universe", {}).items():
            for entry in members:
                mapping[entry["ticker"]] = sector
        return mapping

    def get_sector(self, ticker: str) -> str:
        """Return the sector for a ticker, or ``"UNKNOWN"`` if unmapped."""
        return self.ticker_sectors.get(ticker, "UNKNOWN")

    def check_sector_limit(self, ticker: str, portfolio: PortfolioState) -> bool:
        """Check whether buying ``ticker`` keeps its sector within limits.

        Args:
            ticker: Candidate ticker.
            portfolio: Current portfolio snapshot.

        Returns:
            bool: ``True`` if the projected sector weight is within
            ``MAX_SECTOR_WEIGHT_PCT``; ``False`` (veto) otherwise.
        """
        if portfolio.total_equity <= 0:
            logger.warning("Total equity is zero; vetoing %s on sector check.", ticker)
            return False

        sector = self.get_sector(ticker)
        current_sector_value = sum(
            p.market_value
            for p in portfolio.positions
            if p.sector.casefold() == sector.casefold()
        )
        proposed_add = portfolio.total_equity * self.max_single_position
        projected_weight = (current_sector_value + proposed_add) / portfolio.total_equity

        if projected_weight > self.max_sector_weight:
            logger.info(
                "VETO %s: sector '%s' would reach %.1f%% (limit %.1f%%).",
                ticker,
                sector,
                projected_weight * 100,
                self.max_sector_weight * 100,
            )
            return False

        logger.debug(
            "%s sector '%s' projected weight %.1f%% within limit.",
            ticker,
            sector,
            projected_weight * 100,
        )
        return True

    def check_vix_panic(self, vix_level: float) -> bool:
        """Emergency market-wide brake based on European volatility (VSTOXX).

        When ``vix_level`` exceeds ``VIX_PANIC_THRESHOLD`` the market is in panic
        mode and all *new satellite* stock-picking buys must be blocked. Core
        Smart-DCA accumulation is handled separately and is intentionally NOT
        gated by this check (buy the fear on the broad ETF).

        Args:
            vix_level: Current ``^V2TX`` level (e.g. 34.0).

        Returns:
            bool: ``True`` if satellite buying is allowed, ``False`` (VETO) if
            the market is in panic.
        """
        if vix_level is None:
            return True
        if vix_level > self.vix_panic_threshold:
            logger.warning(
                "VIX PANIC VETO: V2TX %.1f > %.1f -> blocking new satellite buys.",
                vix_level,
                self.vix_panic_threshold,
            )
            return False
        logger.debug(
            "VIX %.1f within calm threshold %.1f; satellite buys allowed.",
            vix_level,
            self.vix_panic_threshold,
        )
        return True

    def check_correlation(
        self, ticker: str, portfolio: PortfolioState, db_manager
    ) -> Tuple[bool, str]:
        """Check Pearson correlation of the candidate vs existing holdings.

        Args:
            ticker: Candidate ticker.
            portfolio: Current portfolio snapshot.
            db_manager: A ``TimeSeriesDB`` exposing ``get_historical_prices``.

        Returns:
            tuple[bool, str]: ``(True, msg)`` if safe or the portfolio is empty;
            ``(False, msg)`` naming the first holding that breaches the limit.
        """
        holdings = [p.ticker for p in portfolio.positions if p.ticker != ticker]
        if not holdings:
            return True, "Correlation check passed (empty portfolio)"

        close_series: Dict[str, pd.Series] = {}
        for tkr in [ticker, *holdings]:
            series = self._close_series(tkr, db_manager)
            if series is not None and not series.empty:
                close_series[tkr] = series

        if ticker not in close_series:
            logger.warning("No price history for candidate %s; cannot correlate.", ticker)
            return True, "Correlation check skipped (no candidate history)"

        prices = pd.concat(close_series, axis=1)
        prices = prices.ffill().dropna(how="all")
        if len(prices) < 2 or prices.shape[1] < 2:
            return True, "Correlation check passed (insufficient overlap)"

        corr_matrix = prices.corr(method="pearson")
        candidate_corr = corr_matrix[ticker].drop(labels=[ticker], errors="ignore")

        for existing_ticker, corr in candidate_corr.items():
            if pd.isna(corr):
                continue
            if corr > self.max_correlation:
                msg = f"Highly correlated with {existing_ticker} (r={corr:.2f})"
                logger.info("VETO %s: %s (limit %.2f).", ticker, msg, self.max_correlation)
                return False, msg

        logger.debug("%s passed correlation check.", ticker)
        return True, "Correlation check passed"

    def _close_series(self, ticker: str, db_manager) -> pd.Series | None:
        """Return a Date-indexed Close series for the configured lookback."""
        df = db_manager.get_historical_prices(
            ticker, days=self.corr_lookback_days
        )
        if df is None or df.empty or "Close" not in df.columns:
            return None
        series = df.set_index("Date")["Close"].astype(float)
        series.name = ticker
        return series


if __name__ == "__main__":
    from datetime import datetime, timezone

    import numpy as np

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    sys.path.insert(0, _CORE_DIR)
    from data_models import Position, PortfolioState as _PS  # noqa: E402

    n = _CORR_WINDOW_DEFAULT
    dates = pd.date_range("2026-01-01", periods=n, freq="B")
    rng = np.random.default_rng(42)
    base = np.cumsum(rng.normal(0, 1, n)) + 100

    class _MockDB:
        """Returns synthetic close series to demonstrate correlation logic."""

        def get_historical_prices(self, ticker: str, days: int = 60) -> pd.DataFrame:
            if ticker == "SAF.PA":
                close = base + rng.normal(0, 0.05, n)
            elif ticker == "OR.PA":
                close = np.cumsum(rng.normal(0, 1, n)) + 200
            else:
                close = base + rng.normal(0, 0.05, n)
            use = min(days, n)
            return pd.DataFrame({
                "Ticker": ticker,
                "Date": dates[:use],
                "Close": close[:use],
            })

    fw = CorrelationFirewall()

    lvmh = Position(ticker="MC.PA", qty_shares=2, avg_entry_price=600,
                    current_price=600, sector="Luxury")
    kering = Position(ticker="KER.PA", qty_shares=5, avg_entry_price=250,
                      current_price=250, sector="Luxury")
    portfolio = _PS(cash_available=5000, total_equity=10000,
                    positions=[lvmh, kering], last_updated=datetime.now(timezone.utc))

    print("--- Sector limit demo ---")
    print("Buy another Luxury (RMS.PA) allowed?", fw.check_sector_limit("RMS.PA", portfolio))
    print("Buy Industrials (AIR.PA) allowed?", fw.check_sector_limit("AIR.PA", portfolio))

    print("\n--- Correlation demo ---")
    saf = Position(ticker="SAF.PA", qty_shares=1, avg_entry_price=100,
                   current_price=100, sector="Industrials")
    orp = Position(ticker="OR.PA", qty_shares=1, avg_entry_price=200,
                   current_price=200, sector="Luxury")
    portfolio2 = _PS(cash_available=5000, total_equity=10000,
                     positions=[saf, orp], last_updated=datetime.now(timezone.utc))
    ok, msg = fw.check_correlation("AIR.PA", portfolio2, _MockDB())
    print(f"AIR.PA correlation check -> {ok}: {msg}")
```

## FILE: 03_risk_portfolio/drawdown_breaker.py
```python
"""Kinetic Brake & Dynamic Drawdown Sizing for PEA Sniper Terminal.

Upgrades binary drawdown stops to a continuous, tiered Kinetic Brake:
  * Drawdown > -5%: 1.00 (Full exposure)
  * Drawdown <= -5% and > -10%: 0.50 (Exposure reduced by 50%)
  * Drawdown <= -10% and > -15%: 0.20 (Exposure reduced by 80%)
  * Drawdown <= -15%: 0.00 (Circuit breaker / total halt)
"""

from __future__ import annotations

import logging
from typing import Sequence, Tuple, Union

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class DrawdownBreaker:
    """Computes dynamic kinetic multipliers and enforces daily/weekly/monthly circuit breakers."""

    def __init__(
        self,
        daily_max_loss: float = -0.005,
        weekly_max_loss: float = -0.02,
        monthly_max_loss: float = -0.05,
        halt_threshold: float = -0.15,
    ) -> None:
        """Initialize breaker with institutional thresholds."""
        self.daily_max_loss = daily_max_loss
        self.weekly_max_loss = weekly_max_loss
        self.monthly_max_loss = monthly_max_loss
        self.halt_threshold = halt_threshold

    @staticmethod
    def compute_drawdown(
        equity_series: Union[pd.Series, Sequence[float], pd.DataFrame]
    ) -> float:
        """Calculate the current drawdown from peak."""
        if equity_series is None:
            return 0.0

        if isinstance(equity_series, pd.DataFrame):
            if "equity" not in equity_series.columns or equity_series.empty:
                return 0.0
            series = equity_series["equity"].dropna().astype(float)
        elif isinstance(equity_series, pd.Series):
            series = equity_series.dropna().astype(float)
        elif isinstance(equity_series, (list, tuple)):
            series = pd.Series(equity_series, dtype=float).dropna()
        else:
            return 0.0

        if len(series) < 1:
            return 0.0

        peak = float(series.cummax().iloc[-1])
        current = float(series.iloc[-1])

        if peak <= 0:
            return 0.0

        dd = (current - peak) / peak
        return float(dd)

    def calculate_kinetic_multiplier(self, drawdown: float) -> float:
        """Map drawdown to kinetic sizing multiplier [0.0, 1.0]."""
        if drawdown > -0.05:
            return 1.0
        elif drawdown > -0.10:
            return 0.50
        elif drawdown > -0.15:
            return 0.20
        else:
            return 0.0

    def check_loss_limits(
        self,
        equity_history: pd.DataFrame | pd.Series,
    ) -> Tuple[bool, str]:
        """Check multi-horizon loss circuit breakers (daily, weekly, monthly).

        Returns:
            Tuple[bool, str]: (passed, reason_if_vetoed).
        """
        if equity_history is None:
            return True, "OK"

        if isinstance(equity_history, pd.DataFrame):
            if "equity" not in equity_history.columns or len(equity_history) < 2:
                return True, "OK"
            series = equity_history["equity"].astype(float)
        else:
            series = pd.Series(equity_history, dtype=float).dropna()

        if len(series) < 2:
            return True, "OK"

        cur = float(series.iloc[-1])
        prev_1d = float(series.iloc[-2])
        if prev_1d > 0:
            chg_1d = (cur - prev_1d) / prev_1d
            if chg_1d < self.daily_max_loss:
                return False, f"DAILY_MAX_LOSS breached: {chg_1d*100:+.2f}% < {self.daily_max_loss*100:.2f}%"

        if len(series) >= 5:
            prev_5d = float(series.iloc[-5])
            if prev_5d > 0:
                chg_5d = (cur - prev_5d) / prev_5d
                if chg_5d < self.weekly_max_loss:
                    return False, f"WEEKLY_MAX_LOSS breached: {chg_5d*100:+.2f}% < {self.weekly_max_loss*100:.2f}%"

        if len(series) >= 21:
            prev_21d = float(series.iloc[-21])
            if prev_21d > 0:
                chg_21d = (cur - prev_21d) / prev_21d
                if chg_21d < self.monthly_max_loss:
                    return False, f"MONTHLY_MAX_LOSS breached: {chg_21d*100:+.2f}% < {self.monthly_max_loss*100:.2f}%"

        return True, "OK"

    def check(self, drawdown_or_equity: Union[float, pd.Series, Sequence[float], pd.DataFrame]) -> Tuple[float, str]:
        """Evaluate drawdown and return (kinetic_multiplier, reason)."""
        if isinstance(drawdown_or_equity, (int, float)):
            dd = float(drawdown_or_equity)
        else:
            dd = self.compute_drawdown(drawdown_or_equity)

        mult = self.calculate_kinetic_multiplier(dd)

        if mult == 1.0:
            reason = f"Normal regime: Drawdown {dd*100:+.1f}% > -5.0%. Full exposure (1.0x)."
        elif mult == 0.50:
            reason = f"Kinetic Brake Tier 1: Drawdown {dd*100:+.1f}% in [-10%, -5%]. Tranches scaled to 50%."
        elif mult == 0.20:
            reason = f"Kinetic Brake Tier 2: Drawdown {dd*100:+.1f}% in [-15%, -10%]. Tranches scaled to 20%."
        else:
            reason = f"Kinetic Brake HALT: Drawdown {dd*100:+.1f}% <= -15.0%. New allocations suspended (0.0x)."

        logger.info("Kinetic Brake check: dd=%.2f%% -> mult=%.2f (%s)", dd * 100, mult, reason)
        return mult, reason


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    breaker = DrawdownBreaker()
    test_eq = [10000, 10500, 11000, 10300, 9800, 9200]
    dd = breaker.compute_drawdown(test_eq)
    mult, why = breaker.check(dd)
    print(f"Current DD: {dd*100:+.2f}% | Multiplier: {mult} | Reason: {why}")
```

## FILE: 03_risk_portfolio/equity_metrics.py
```python
"""Shared equity-curve analytics for live dashboard and future backtests.

Pure functions over a daily equity series — no I/O, no Streamlit, no broker.
Reuse the same metrics on ``portfolio_history`` (live) and on a simulated curve
(walk-forward backtester) so numbers stay comparable.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _prepare_equity_series(curve: pd.DataFrame | pd.Series) -> pd.Series:
    """Normalize a curve into a sorted float Series indexed by date."""
    if isinstance(curve, pd.Series):
        s = curve.astype(float).copy()
        s.index = pd.to_datetime(s.index, errors="coerce")
        return s.dropna().sort_index()

    if curve is None or getattr(curve, "empty", True):
        return pd.Series(dtype=float)

    df = curve.copy()
    if "equity" not in df.columns:
        return pd.Series(dtype=float)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date", "equity"]).sort_values("date")
        return df.set_index("date")["equity"].astype(float)

    s = df["equity"].astype(float)
    s.index = pd.to_datetime(s.index, errors="coerce")
    return s.dropna().sort_index()


def max_drawdown(equity: pd.Series) -> float:
    """Peak-to-trough drawdown as a negative fraction (e.g. -0.12 = -12%)."""
    if equity is None or len(equity) < 2:
        return 0.0
    peak = equity.cummax()
    dd = (equity / peak) - 1.0
    val = float(dd.min())
    return val if np.isfinite(val) else 0.0


def cagr(equity: pd.Series, periods_per_year: float = 252.0) -> float | None:
    """Compound annual growth rate from first to last equity point.

    Uses calendar days between endpoints when the index is datetime-like;
    otherwise falls back to ``len(equity) / periods_per_year`` years.
    """
    if equity is None or len(equity) < 2:
        return None
    start = float(equity.iloc[0])
    end = float(equity.iloc[-1])
    if start <= 0 or end <= 0 or not np.isfinite(start) or not np.isfinite(end):
        return None
    try:
        delta_days = (equity.index[-1] - equity.index[0]).days
        years = max(delta_days / 365.25, 1e-9)
    except Exception:  # noqa: BLE001
        years = max(len(equity) / periods_per_year, 1e-9)
    return float((end / start) ** (1.0 / years) - 1.0)


def sharpe_ratio(
    equity: pd.Series,
    risk_free: float = 0.0,
    periods_per_year: float = 252.0,
) -> float | None:
    """Annualized Sharpe from daily equity returns (sample stdev)."""
    if equity is None or len(equity) < 3:
        return None
    rets = equity.pct_change().dropna()
    if rets.empty or float(rets.std()) == 0.0:
        return None
    excess = rets - (risk_free / periods_per_year)
    val = float(excess.mean() / excess.std() * np.sqrt(periods_per_year))
    return val if np.isfinite(val) else None


def sortino_ratio(
    equity: pd.Series,
    risk_free: float = 0.0,
    periods_per_year: float = 252.0,
) -> float | None:
    """Annualized Sortino (downside deviation only)."""
    if equity is None or len(equity) < 3:
        return None
    rets = equity.pct_change().dropna()
    if rets.empty:
        return None
    excess = rets - (risk_free / periods_per_year)
    downside = excess[excess < 0]
    if downside.empty or float(downside.std()) == 0.0:
        return None
    val = float(excess.mean() / downside.std() * np.sqrt(periods_per_year))
    return val if np.isfinite(val) else None


def compute_equity_metrics(
    curve: pd.DataFrame | pd.Series,
    risk_free: float = 0.0,
) -> dict[str, Any]:
    """Return a metrics dict ready for dashboard / backtest reports.

    Keys: ``n_points``, ``start_equity``, ``end_equity``, ``total_return``,
    ``cagr``, ``max_drawdown``, ``sharpe``, ``sortino``, ``cash_last`` (if col).
    """
    equity = _prepare_equity_series(curve)
    out: dict[str, Any] = {
        "n_points": int(len(equity)),
        "start_equity": None,
        "end_equity": None,
        "total_return": None,
        "cagr": None,
        "max_drawdown": 0.0,
        "sharpe": None,
        "sortino": None,
        "cash_last": None,
    }
    if equity.empty:
        return out

    start = float(equity.iloc[0])
    end = float(equity.iloc[-1])
    out["start_equity"] = start
    out["end_equity"] = end
    out["total_return"] = (end / start - 1.0) if start > 0 else None
    out["cagr"] = cagr(equity)
    out["max_drawdown"] = max_drawdown(equity)
    out["sharpe"] = sharpe_ratio(equity, risk_free=risk_free)
    out["sortino"] = sortino_ratio(equity, risk_free=risk_free)

    if isinstance(curve, pd.DataFrame) and "cash" in curve.columns and not curve.empty:
        try:
            out["cash_last"] = float(curve.sort_values("date").iloc[-1]["cash"])
        except Exception:  # noqa: BLE001
            out["cash_last"] = None
    return out
```

## FILE: 03_risk_portfolio/hrp_sizer.py
```python
"""Hierarchical Risk Parity (HRP) Portfolio Allocation for PEA Sniper Terminal.

Implements Marcos López de Prado's HRP algorithm:
  1. Tree Clustering: Correlation -> Distance Matrix -> Single Linkage.
  2. Quasi-Diagonalization: Reorders the covariance matrix according to the dendrogram.
  3. Recursive Bisection: Inverse-variance allocation across clustered subsets.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage
from scipy.spatial.distance import squareform

logger = logging.getLogger(__name__)


class HRPSizer:
    """Calculates Hierarchical Risk Parity portfolio weights from historical returns."""

    @staticmethod
    def get_quasi_diag(link: np.ndarray) -> List[int]:
        """Sort clustered items by hierarchical tree order (Quasi-Diagonalization)."""
        link = link.astype(int)
        sort_ix = pd.Series([link[-1, 0], link[-1, 1]])
        num_items = link[-1, 3]

        while sort_ix.max() >= num_items:
            sort_ix.index = range(0, sort_ix.shape[0] * 2, 2)
            df0 = sort_ix[sort_ix >= num_items]
            i = df0.index
            j = df0.values - num_items
            sort_ix[i] = link[j, 0]
            df0 = pd.Series(link[j, 1], index=i + 1)
            sort_ix = pd.concat([sort_ix, df0]).sort_index()
            sort_ix.index = range(sort_ix.shape[0])

        return sort_ix.tolist()

    @staticmethod
    def get_cluster_variance(cov: np.ndarray, c_items: List[int]) -> float:
        """Compute the variance of a sub-cluster using inverse-variance weights."""
        sub_cov = cov[np.ix_(c_items, c_items)]
        ivp = 1.0 / np.diag(sub_cov)
        ivp /= ivp.sum()
        w = ivp.reshape(-1, 1)
        c_var = np.dot(np.dot(w.T, sub_cov), w)[0, 0]
        return float(c_var)

    def get_rec_bisection(self, cov: np.ndarray, sort_ix: List[int]) -> pd.Series:
        """Recursive bisection allocation down the quasi-diagonalized tree."""
        w = pd.Series(1.0, index=sort_ix)
        c_items = [sort_ix]

        while len(c_items) > 0:
            c_items = [
                i[j:k]
                for i in c_items
                for j, k in ((0, int(len(i) / 2)), (int(len(i) / 2), len(i)))
                if len(i) > 1
            ]
            for i in range(0, len(c_items), 2):
                c_items0 = c_items[i]
                c_items1 = c_items[i + 1]
                c_var0 = self.get_cluster_variance(cov, c_items0)
                c_var1 = self.get_cluster_variance(cov, c_items1)
                alpha = 1.0 - c_var0 / (c_var0 + c_var1)
                w[c_items0] *= alpha
                w[c_items1] *= 1.0 - alpha

        return w

    def calculate_hrp_weights(self, returns_df: pd.DataFrame) -> Dict[str, float]:
        """Compute HRP weights given a DataFrame of asset returns.

        Args:
            returns_df: DataFrame where each column is a ticker's daily return series.

        Returns:
            dict: {ticker: weight_float} summing to 1.0.
        """
        if returns_df is None or returns_df.empty or returns_df.shape[1] < 2:
            cols = list(returns_df.columns) if returns_df is not None else []
            if len(cols) == 1:
                return {cols[0]: 1.0}
            return {}

        clean_rets = returns_df.dropna().copy()
        if len(clean_rets) < 10:
            # Fallback to equal weight if history is too short
            n = returns_df.shape[1]
            return {c: 1.0 / n for c in returns_df.columns}

        cov = clean_rets.cov().values
        corr = clean_rets.corr().values
        tickers = list(clean_rets.columns)

        # 1. Distance matrix: D = sqrt(0.5 * (1 - rho))
        dist = np.sqrt(np.clip(0.5 * (1.0 - corr), 0, 1.0))
        np.fill_diagonal(dist, 0.0)

        # Convert to condensed form for scipy linkage
        condensed_dist = squareform(dist, checks=False)
        link = linkage(condensed_dist, method="single")

        # 2. Quasi-Diagonalization
        sort_ix = self.get_quasi_diag(link)

        # 3. Recursive Bisection
        weights = self.get_rec_bisection(cov, sort_ix)
        weights = weights.sort_index()

        hrp_dict = {tickers[i]: float(weights.iloc[i]) for i in range(len(tickers))}
        return hrp_dict


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    hrp = HRPSizer()
    np.random.seed(42)
    fake_rets = pd.DataFrame(
        np.random.normal(0.0005, 0.015, (252, 4)),
        columns=["MC.PA", "OR.PA", "AI.PA", "CW8.PA"],
    )
    res = hrp.calculate_hrp_weights(fake_rets)
    print("HRP Allocation Weights:", {k: f"{v*100:.2f}%" for k, v in res.items()})
```

## FILE: 03_risk_portfolio/limit_price_optimizer.py
```python
import logging
import math

logger = logging.getLogger(__name__)

def calculate_smart_limit_price(ticker: str, current_price: float, atr_14: float, direction: str = "BUY") -> float:
    """
    Calculates a smart limit price maximizing fill probability while avoiding chasing spikes.
    
    Args:
        ticker: The stock ticker.
        current_price: The latest known closing price or mid price.
        atr_14: The 14-day Average True Range.
        direction: "BUY" or "SELL".
        
    Returns:
        The suggested limit price rounded to 2 decimal places (Euronext tick rules proxy).
    """
    if current_price <= 0:
        logger.warning(f"Invalid current_price {current_price} for {ticker}")
        return current_price
        
    if atr_14 < 0:
        logger.warning(f"Invalid negative ATR {atr_14} for {ticker}, defaulting to 0.")
        atr_14 = 0.0

    direction = str(direction).strip().upper()
    
    if direction == "BUY":
        # Do not pay more than +0.2% or +15% of ATR, whichever is lower
        limit_px = min(current_price * 1.002, current_price + 0.15 * atr_14)
    elif direction == "SELL":
        # Do not sell for less than -0.2% or -15% of ATR, whichever is lower
        limit_px = max(current_price * 0.998, current_price - 0.15 * atr_14)
    else:
        logger.warning(f"Unknown direction '{direction}' for {ticker}, defaulting to current_price.")
        limit_px = current_price
        
    # Euronext typically rounds to 2 or 3 decimals depending on the asset price.
    # We round to 2 decimals for general liquidity on PEA stocks.
    return round(limit_px, 2)
```

## FILE: 03_risk_portfolio/monthly_rebalancer.py
```python
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
```

## FILE: 03_risk_portfolio/pea_position_sizer.py
```python
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
        logger.debug(
            "Sizer loaded: kelly=%.2f max_single=%.2f sat_budget=%.2f vol_ref=%.2f max_sector=%.2f",
            self.kelly_fraction,
            self.max_single_position,
            self.satellite_max_budget,
            self.vol_reference,
            self.max_sector_weight,
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
    ) -> tuple[int, dict]:
        """Return ``(qty, meta)`` so UIs can show the sizing reasoning.

        Meta keys: kelly_fraction, score, historical_volatility, vol_factor,
        max_alloc, target_cash_pre_cap, target_cash, notional, weight_pct,
        satellite_room, cash_capped, sector_scale, kinetic_multiplier.
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
```

## FILE: 03_risk_portfolio/risk_config.py
```python
"""Pydantic validation schema for risk_params.yaml.

Strictly enforces types, value ranges, and forbids unexpected extra keys (extra='forbid', frozen=True)
to prevent silent configuration bugs at boot time.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)


class RiskParamsConfig(BaseModel):
    """Institutional risk configuration with strict compile-time validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Position Sizing
    KELLY_FRACTION: float = Field(default=0.5, ge=0.05, le=1.0)
    MAX_SINGLE_POSITION_PCT: float = Field(default=0.15, ge=0.01, le=0.50)
    MAX_SECTOR_WEIGHT_PCT: float = Field(default=0.25, ge=0.05, le=0.60)
    MAX_ALLOCATION_PER_DAY_PCT: float = Field(default=0.03, ge=0.005, le=0.20)

    # Circuit Breakers & Loss Limits
    DAILY_MAX_LOSS_PCT: float = Field(default=-0.005, le=0.0, ge=-0.10)
    WEEKLY_MAX_LOSS_PCT: float = Field(default=-0.02, le=0.0, ge=-0.25)
    MONTHLY_MAX_LOSS_PCT: float = Field(default=-0.05, le=0.0, ge=-0.40)

    # Correlation Limits
    MAX_CORRELATION_TO_PORTFOLIO: float = Field(default=0.70, ge=0.1, le=1.0)
    MAX_CORRELATION_SAME_SECTOR: float = Field(default=0.80, ge=0.1, le=1.0)
    CORRELATION_LOOKBACK_DAYS: int = Field(default=60, ge=10, le=252)

    # Signals & Constraints
    SIGNAL_BUY_THRESHOLD: float = Field(default=75.0, ge=40.0, le=100.0)
    SIGNAL_SELL_THRESHOLD: float = Field(default=35.0, ge=0.0, le=60.0)
    SIGNAL_VALIDITY_HOURS: int = Field(default=12, ge=1, le=72)
    MACRO_VETO_DAYS_BEFORE: int = Field(default=3, ge=0, le=14)
    EARNINGS_BLACKOUT_DAYS: int = Field(default=2, ge=0, le=14)
    RSI_OVERSOLD_THRESHOLD: float = Field(default=30.0, ge=10.0, le=45.0)
    MIN_LIQUIDITY_ADV: float = Field(default=50000.0, ge=0.0)
    MAX_POSITIONS_TOTAL: int = Field(default=12, ge=1, le=50)

    # Exits
    PROFIT_TARGET_PCT: float = Field(default=0.10, ge=0.01)
    STOP_LOSS_PCT: float = Field(default=-0.05, le=0.0)

    # Core / Satellite Model
    CORE_TICKER: str = Field(default="CW8.PA")
    CORE_TARGET_PCT: float = Field(default=0.70, ge=0.30, le=0.95)
    CORE_CRASH_TARGET_PCT: float = Field(default=0.75, ge=0.30, le=0.95)
    CORE_DCA_MAX_TRANCHE_PCT: float = Field(default=0.05, ge=0.01, le=0.20)
    SATELLITE_MAX_BUDGET_PCT: float = Field(default=0.30, ge=0.05, le=0.70)

    # Volatility & VIX Defense
    VOLATILITY_REFERENCE: float = Field(default=0.20, ge=0.05, le=0.50)
    VOLATILITY_MAX_FACTOR: float = Field(default=1.5, ge=1.0, le=3.0)
    VIX_PANIC_THRESHOLD: float = Field(default=30.0, ge=15.0, le=60.0)

    # Rebalancing
    REBALANCE_PROFIT_SHAVE_PCT: float = Field(default=0.20, ge=0.05, le=0.50)
    REBALANCE_PROFIT_TRIGGER_PCT: float = Field(default=20.0, ge=5.0, le=100.0)
    REBALANCE_ATR_STOP_MULT: float = Field(default=2.5, ge=1.0, le=5.0)


def load_and_validate_risk_params(path: str | Path) -> RiskParamsConfig:
    """Load YAML risk configuration and strictly validate against RiskParamsConfig.

    Raises:
        ValidationError: If any key is misspelled, extra, or value is out of bounds.
        FileNotFoundError: If the YAML file does not exist.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Risk params file not found: {p}")

    with open(p, "r", encoding="utf-8") as fh:
        raw_data = yaml.safe_load(fh) or {}

    try:
        config = RiskParamsConfig(**raw_data)
        logger.info("Risk parameters validated successfully from %s", p)
        return config
    except ValidationError as exc:
        logger.critical("FATAL: risk_params.yaml failed Pydantic validation: %s", exc)
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    root = Path(__file__).resolve().parent.parent
    cfg = load_and_validate_risk_params(root / "config" / "risk_params.yaml")
    print("Risk config loaded and validated:", cfg.model_dump())
```

## FILE: 03_risk_portfolio/stress_tester.py
```python
"""Ratio Backfill & Historical Crisis Stress Tester for PEA Sniper Terminal.

Solves the truncated history problem for French PEA ETFs (e.g. ``CW8.PA``)
by mathematically stitching their price action to long-history proxies
(``URTH``, ``^GSPC``, ``SPY``) using the invariant ratio at the first overlap date:
    ratio = Asset[first_date] / Proxy[first_date]
    Synthetic_History = Proxy[:first_date] * ratio
"""

from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# Key historical crisis regimes to stress-test
CRISIS_PERIODS = {
    "2008_GFC_Lehman": ("2007-10-01", "2009-03-09"),
    "2011_Euro_Debt": ("2011-05-01", "2011-10-04"),
    "2020_Covid_Crash": ("2020-02-19", "2020-03-23"),
    "2022_Inflation_Bear": ("2022-01-03", "2022-10-12"),
}


class RatioBackfillStressTester:
    """Stitches asset history with a proxy index (^FCHI / ^GSPC) and executes crisis stress tests."""

    def __init__(self, target_ticker: str = "CW8.PA", proxy_ticker: str = "^FCHI") -> None:
        self.target_ticker = target_ticker
        self.proxy_ticker = proxy_ticker

    def synthesize_ratio_backfill(
        self,
        target_df: Optional[pd.DataFrame] = None,
        proxy_df: Optional[pd.DataFrame] = None,
        start_year: str = "2000-01-01",
    ) -> pd.DataFrame:
        """Create a continuous synthetic OHLCV history by ratio-backfilling target with proxy.

        Args:
            target_df: DataFrame with Date index and 'Close' column for target (e.g. CW8.PA).
            proxy_df: DataFrame with Date index and 'Close' column for proxy (e.g. ^GSPC).
            start_year: Start date for proxy download if fetching live.

        Returns:
            pd.DataFrame: Stitched DataFrame with columns ['Close', 'Synthetic'].
        """
        if target_df is None or target_df.empty:
            try:
                target_df = yf.download(self.target_ticker, start="2005-01-01", progress=False, auto_adjust=True)
                if isinstance(target_df.columns, pd.MultiIndex):
                    c = target_df["Close"]
                    target_df = pd.DataFrame({"Close": c.iloc[:, 0] if isinstance(c, pd.DataFrame) else c})
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not download %s: %s", self.target_ticker, exc)
                target_df = pd.DataFrame()

        if proxy_df is None or proxy_df.empty:
            try:
                proxy_df = yf.download(self.proxy_ticker, start=start_year, progress=False, auto_adjust=True)
                if isinstance(proxy_df.columns, pd.MultiIndex):
                    c = proxy_df["Close"]
                    proxy_df = pd.DataFrame({"Close": c.iloc[:, 0] if isinstance(c, pd.DataFrame) else c})
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not download proxy %s: %s", self.proxy_ticker, exc)
                proxy_df = pd.DataFrame()

        if target_df.empty and proxy_df.empty:
            return pd.DataFrame(columns=["Close", "Synthetic"])

        if target_df.empty:
            # Entirely proxy
            res = pd.DataFrame({"Close": proxy_df["Close"].dropna(), "Synthetic": True})
            return res

        if proxy_df.empty:
            # Entirely target
            res = pd.DataFrame({"Close": target_df["Close"].dropna(), "Synthetic": False})
            return res

        t_close = target_df["Close"].dropna().sort_index()
        p_close = proxy_df["Close"].dropna().sort_index()

        # Find first overlapping valid date
        overlap_dates = t_close.index.intersection(p_close.index)
        if len(overlap_dates) == 0:
            logger.warning("No overlap dates found between %s and %s.", self.target_ticker, self.proxy_ticker)
            return pd.DataFrame({"Close": t_close, "Synthetic": False})

        first_overlap = overlap_dates[0]
        ratio = float(t_close.loc[first_overlap]) / float(p_close.loc[first_overlap])
        logger.info(
            "Ratio Backfill: first overlap at %s | %s=%.2f, %s=%.2f | ratio=%.6f",
            str(first_overlap)[:10],
            self.target_ticker,
            float(t_close.loc[first_overlap]),
            self.proxy_ticker,
            float(p_close.loc[first_overlap]),
            ratio,
        )

        # Synthetic history prior to first_overlap
        p_pre = p_close[p_close.index < first_overlap] * ratio
        synth_pre = pd.DataFrame({"Close": p_pre, "Synthetic": True})
        actual_post = pd.DataFrame({"Close": t_close, "Synthetic": False})

        stitched = pd.concat([synth_pre, actual_post]).sort_index()
        stitched = stitched[~stitched.index.duplicated(keep="last")]
        return stitched

    def stress_test_crisis(self, stitched_df: pd.DataFrame, start_date: str, end_date: str) -> Dict[str, float]:
        """Calculate maximum drawdown and performance over a specified crisis window."""
        if stitched_df.empty:
            return {"max_drawdown": 0.0, "total_return": 0.0, "trough_date": "n/a"}

        sub = stitched_df.loc[start_date:end_date]
        if sub.empty or len(sub) < 2:
            return {"max_drawdown": 0.0, "total_return": 0.0, "trough_date": "n/a"}

        series = sub["Close"].astype(float)
        peak = series.cummax()
        drawdowns = (series - peak) / peak

        max_dd = float(drawdowns.min())
        trough_idx = drawdowns.idxmin()
        tot_return = float((series.iloc[-1] / series.iloc[0]) - 1.0)

        return {
            "max_drawdown": max_dd,
            "total_return": tot_return,
            "trough_date": str(trough_idx)[:10],
            "start_price": float(series.iloc[0]),
            "trough_price": float(series.loc[trough_idx]),
            "end_price": float(series.iloc[-1]),
        }

    def run_all_stress_tests(self, stitched_df: Optional[pd.DataFrame] = None) -> Dict[str, dict]:
        """Execute full battery of crisis stress tests."""
        if stitched_df is None or stitched_df.empty:
            stitched_df = self.synthesize_ratio_backfill()

        results: Dict[str, dict] = {}
        for name, (start_d, end_d) in CRISIS_PERIODS.items():
            results[name] = self.stress_test_crisis(stitched_df, start_d, end_d)
            logger.info(
                "Stress Test [%s]: Max DD = %.2f%%, Total Return = %.2f%% (Trough: %s)",
                name,
                results[name]["max_drawdown"] * 100,
                results[name]["total_return"] * 100,
                results[name]["trough_date"],
            )

        return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    tester = RatioBackfillStressTester()
    res = tester.run_all_stress_tests()
    print("\n--- Crisis Stress Testing Results ---")
    for crisis, stats in res.items():
        print(f"[{crisis}] Max DD: {stats['max_drawdown']*100:+.2f}% | Return: {stats['total_return']*100:+.2f}% | Trough: {stats['trough_date']}")
```
