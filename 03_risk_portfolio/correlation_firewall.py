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

    def check_sector_limit(self, ticker: str, portfolio: PortfolioState) -> list[str]:
        """Check sector exposure and return warning indicators (never vetoes).

        Args:
            ticker: Candidate ticker.
            portfolio: Current portfolio snapshot.

        Returns:
            list[str]: List of warning strings if sector limit would be exceeded, else empty list.
        """
        warnings: list[str] = []
        if portfolio.total_equity <= 0:
            return warnings

        sector = self.get_sector(ticker)
        current_sector_value = sum(
            p.market_value
            for p in portfolio.positions
            if p.sector.casefold() == sector.casefold()
        )
        proposed_add = portfolio.total_equity * self.max_single_position
        projected_weight = (current_sector_value + proposed_add) / portfolio.total_equity

        if projected_weight > self.max_sector_weight:
            warn = (
                f"⚠️ Sector exposure would reach {projected_weight * 100:.1f}% "
                f"for '{sector}' (cap {self.max_sector_weight * 100:.0f}%)"
            )
            warnings.append(warn)
            logger.info("SECTOR CONCENTRATION WARNING for %s: %s", ticker, warn)
        else:
            logger.debug(
                "%s sector '%s' projected weight %.1f%% within limit.",
                ticker,
                sector,
                projected_weight * 100,
            )

        return warnings

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
    ) -> list[str]:
        """Check Pearson correlation of the candidate vs existing holdings and return warnings.

        Args:
            ticker: Candidate ticker.
            portfolio: Current portfolio snapshot.
            db_manager: A ``TimeSeriesDB`` exposing ``get_historical_prices``.

        Returns:
            list[str]: List of warning strings for each holding where correlation exceeds max_correlation.
        """
        warnings: list[str] = []
        holdings = [p.ticker for p in portfolio.positions if p.ticker != ticker]
        if not holdings:
            return warnings

        close_series: Dict[str, pd.Series] = {}
        for tkr in [ticker, *holdings]:
            series = self._close_series(tkr, db_manager)
            if series is not None and not series.empty:
                close_series[tkr] = series

        if ticker not in close_series:
            logger.warning("No price history for candidate %s; cannot correlate.", ticker)
            return warnings

        prices = pd.concat(close_series, axis=1)
        prices = prices.ffill().dropna(how="all")
        if len(prices) < 2 or prices.shape[1] < 2:
            return warnings

        corr_matrix = prices.corr(method="pearson")
        candidate_corr = corr_matrix[ticker].drop(labels=[ticker], errors="ignore")

        for existing_ticker, corr in candidate_corr.items():
            if pd.isna(corr):
                continue
            if corr > self.max_correlation:
                warn = (
                    f"⚠️ {corr * 100:.0f}% correlation with existing holding {existing_ticker} "
                    f"(cap {self.max_correlation * 100:.0f}%)"
                )
                warnings.append(warn)
        return warnings

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
