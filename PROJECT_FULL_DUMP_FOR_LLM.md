# PEA Sniper Terminal — Full Project Dump for LLM
Root: `C:\Users\PolluxGronier\Downloads\pea_sniper_terminal`
Generated: 2026-07-23 13:20 UTC
One-shot context dump of source, configs, and docs (no venv, no DBs, no secrets).
---
## File index (51 files)
- .github/workflows/ci.yml
- .gitignore
- .streamlit/config.toml
- 00_data_sensors/__init__.py
- 00_data_sensors/macro_alpha_api.py
- 00_data_sensors/market_prices_api.py
- 00_data_sensors/scrapers/__init__.py
- 00_data_sensors/scrapers/_http.py
- 00_data_sensors/scrapers/amf_scraper.py
- 00_data_sensors/scrapers/bourso_scraper.py
- 01_memory_core/__init__.py
- 01_memory_core/data_models.py
- 01_memory_core/duckdb_manager.py
- 01_memory_core/sqlite_portfolio.py
- 02_quant_engine/__init__.py
- 02_quant_engine/smart_dca_engine.py
- 02_quant_engine/technical_scorer.py
- 03_risk_portfolio/__init__.py
- 03_risk_portfolio/correlation_firewall.py
- 03_risk_portfolio/equity_metrics.py
- 03_risk_portfolio/monthly_rebalancer.py
- 03_risk_portfolio/pea_position_sizer.py
- 04_orchestrator_ai/__init__.py
- 04_orchestrator_ai/earnings_blackout.py
- 04_orchestrator_ai/macro_veto.py
- 04_orchestrator_ai/news_sentiment_llm.py
- 04_orchestrator_ai/revocation_engine.py
- 04_orchestrator_ai/signal_priority_cascade.py
- 04_orchestrator_ai/weekly_historian.py
- 05_interfaces/__init__.py
- 05_interfaces/discord_copilot.py
- 05_interfaces/llm_explainer.py
- 05_interfaces/terminal_dashboard.py
- config/api_keys.env.example
- config/earnings_calendar.yaml
- config/macro_calendar.yaml
- config/pea_universe.yaml
- config/risk_params.yaml
- docker-compose.yml
- Dockerfile
- main_scheduler.py
- README.md
- requirements.txt
- run_dashboard.ps1
- run_discord.py
- seed_account.py
- tests/__init__.py
- tests/test_phase16_foundations.py
- tools/build_llm_dump.py
- tools/build_universe.py
- tools/sync_universe_from_bourso.py

---
## FILE: .github/workflows/ci.yml
```yaml
# PEA Sniper Terminal — CI
name: ci

on:
  push:
    branches: [main, master]
  pull_request:
    branches: [main, master]

jobs:
  pytest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install deps
        run: |
          python -m pip install --upgrade pip
          pip install pytest pandas numpy pyyaml pydantic pandas-ta-classic
      - name: Run tests
        run: python -m pytest -q
```

## FILE: .gitignore
```text
# --- Secrets & config ---
config/api_keys.env
*.env
!*.env.example

# --- Databases (state & time-series) ---
database/
*.db
*.duckdb
*.sqlite
*.sqlite3

# --- Python ---
__pycache__/
*.py[cod]
*$py.class
.venv/
venv/
venv_x64/
venv*/
env/
.python-version
*.egg-info/
.pytest_cache/
.mypy_cache/
.ruff_cache/

# --- Notebooks ---
.ipynb_checkpoints/

# --- OS / Editor ---
.DS_Store
Thumbs.db
.vscode/
.idea/

# --- Logs & data dumps ---
*.log
data/
```

## FILE: .streamlit/config.toml
```toml
# Force a pure-black "Bloomberg terminal" dark theme so native widgets
# (st.dataframe grid, st.metric, inputs) never render on a white background.
[theme]
base = "dark"
backgroundColor = "#050505"
secondaryBackgroundColor = "#0A0A0A"
textColor = "#FFFFFF"
primaryColor = "#00FF00"
font = "monospace"

[client]
# false for any non-local deploy (Docker / public IP) — avoids leaking paths.
# Local debug: set STREAMLIT_CLIENT_SHOW_ERROR_DETAILS=true or flip to true.
showErrorDetails = false
toolbarMode = "minimal"

[browser]
gatherUsageStats = false
serverAddress = "localhost"

[server]
# Local default: open a browser. Docker overrides with --server.headless=true
# (see docker-compose.yml dashboard service) — containers have no display.
headless = false
port = 8501
```

## FILE: 00_data_sensors/__init__.py
```python

```

## FILE: 00_data_sensors/macro_alpha_api.py
```python
"""Alternative-data / macro alpha sensors for PEA Sniper Terminal V-Prime.

This module turns qualitative market signals into hard numbers the deterministic
engine can act on:

  * European volatility (VSTOXX / ``^V2TX``) as an emergency "panic" gauge.
  * Options Put/Call volume ratio (contrarian fear gauge).
  * Insider net buying/selling direction.
  * A Polymarket geopolitical-probability placeholder.

Everything is read-only and network-tolerant: any upstream failure degrades to a
neutral value and logs the reason, so the daemon never crashes on a data outage.
"""

import logging
import os
import sys
import time
from functools import wraps
from pathlib import Path
from typing import Callable

import pandas as pd
import requests
import yfinance as yf

# Optional French scrapers (isolated; failures must never crash the daemon).
_SCRAPERS_DIR = Path(__file__).resolve().parent / "scrapers"
if str(_SCRAPERS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRAPERS_DIR))
try:
    from amf_scraper import AmfInsiderScraper  # noqa: E402
except Exception:  # noqa: BLE001
    AmfInsiderScraper = None  # type: ignore[assignment,misc]
try:
    from bourso_scraper import BoursoramaScraper  # noqa: E402
except Exception:  # noqa: BLE001
    BoursoramaScraper = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

# Volatility gauges, tried in order. VSTOXX (^V2TX / Euro Stoxx 50 Volatility)
# is the primary European gauge, but Yahoo intermittently delists it, so the US
# VIX (^VIX) is kept as a highly-correlated fallback proxy for panic detection.
_VIX_TICKERS = ("^V2TX", "^VIX")
# Neutral fallbacks used whenever upstream data is missing.
_NEUTRAL_VIX = 15.0
_NEUTRAL_PUT_CALL = 1.0


def _retry(attempts: int = 3, base_delay: float = 1.0) -> Callable:
    """Decorator: retry a network call with exponential backoff.

    Args:
        attempts: Total number of tries before giving up.
        base_delay: Initial delay in seconds; doubles each retry.

    Returns:
        Callable: The wrapped function that swallows transient errors.
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = base_delay
            for attempt in range(1, attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001 - transient network I/O.
                    if attempt == attempts:
                        logger.warning(
                            "%s failed after %d attempts: %s",
                            func.__name__,
                            attempts,
                            exc,
                        )
                        raise
                    logger.debug(
                        "%s attempt %d/%d failed (%s); retrying in %.1fs.",
                        func.__name__,
                        attempt,
                        attempts,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
                    delay *= 2
            return None  # pragma: no cover - unreachable.

        return wrapper

    return decorator


class MacroAlphaSensor:
    """Fetches macro and alternative-data signals as plain floats/ints."""

    def __init__(self, neutral_vix: float = _NEUTRAL_VIX) -> None:
        """Store fallbacks used when live data is unavailable.

        Args:
            neutral_vix: VIX value returned when ``^V2TX`` cannot be fetched.
        """
        self.neutral_vix = float(neutral_vix)

    # ---------------------------------------------------------------- VIX ----
    @_retry(attempts=2, base_delay=1.0)
    def _download_vix(self, ticker: str) -> float:
        """Return the latest close for a volatility ticker (raises to retry)."""
        data = yf.Ticker(ticker).history(period="5d", interval="1d")
        if data is None or data.empty or "Close" not in data:
            raise ValueError(f"empty VIX frame for {ticker}")
        value = float(data["Close"].dropna().iloc[-1])
        if value <= 0:
            raise ValueError(f"non-positive VIX for {ticker}: {value}")
        return value

    def get_european_vix(self) -> float:
        """Fetch the current market volatility (VSTOXX, VIX proxy fallback).

        Tries ``^V2TX`` (Euro Stoxx 50 Volatility) first, then ``^VIX`` as a
        correlated proxy if VSTOXX is unavailable on Yahoo.

        Returns:
            float: Latest volatility index close, or a neutral fallback.
        """
        for ticker in _VIX_TICKERS:
            try:
                value = self._download_vix(ticker)
                logger.info("Volatility gauge %s = %.2f", ticker, value)
                return value
            except Exception:  # noqa: BLE001 - try the next source.
                logger.debug("Volatility source %s unavailable.", ticker)
        logger.warning(
            "No volatility source available; using neutral %.1f.", self.neutral_vix
        )
        return self.neutral_vix

    # ------------------------------------------------------- Put/Call ratio --
    def get_put_call_ratio(self, ticker: str) -> float:
        """Compute the Put/Call *volume* ratio from the nearest options expiry.

        A ratio > 1.2 means heavy put buying (fear) — a contrarian bullish tell.

        Args:
            ticker: Yahoo Finance ticker symbol.

        Returns:
            float: Put/Call volume ratio, or 1.0 (neutral) if unavailable.
        """
        try:
            tk = yf.Ticker(ticker)
            expiries = tk.options
            if not expiries:
                logger.debug("No options chain for %s; neutral P/C.", ticker)
                return _NEUTRAL_PUT_CALL
            chain = tk.option_chain(expiries[0])
            put_vol = float(chain.puts["volume"].fillna(0).sum())
            call_vol = float(chain.calls["volume"].fillna(0).sum())
            if call_vol <= 0:
                logger.debug("Zero call volume for %s; neutral P/C.", ticker)
                return _NEUTRAL_PUT_CALL
            ratio = put_vol / call_vol
            logger.info(
                "%s Put/Call volume ratio = %.2f (P=%.0f, C=%.0f).",
                ticker,
                ratio,
                put_vol,
                call_vol,
            )
            return ratio
        except Exception:  # noqa: BLE001 - many EU tickers have no options.
            logger.debug("Put/Call unavailable for %s; neutral.", ticker)
            return _NEUTRAL_PUT_CALL

    # ------------------------------------------------------ Insider signal --
    def get_insider_activity(self, ticker: str) -> int:
        """Return net insider direction: AMF first, then FMP, then yfinance.

        Cascade (strict):
            1. ``AmfInsiderScraper`` (official French BDIF)
            2. Financial Modeling Prep ``/api/v4/insider-trading``
            3. ``yfinance.insider_transactions``
        """
        # --- 1) AMF BDIF (primary) ------------------------------------------
        if AmfInsiderScraper is not None:
            try:
                isin = None
                issuer = None
                if BoursoramaScraper is not None:
                    try:
                        profile = BoursoramaScraper().get_instrument_profile(ticker)
                        if profile:
                            isin = profile.get("isin")
                            issuer = profile.get("name")
                    except Exception as exc:  # noqa: BLE001
                        logger.debug(
                            "Bourso profile enrich failed for %s: %s", ticker, exc
                        )
                amf_df = AmfInsiderScraper().get_recent_declarations(
                    ticker, isin=isin, issuer=issuer
                )
                if amf_df is not None and not amf_df.empty:
                    direction = self._score_amf_declarations(amf_df)
                    logger.info(
                        "%s insider activity (AMF): %+d from %d row(s).",
                        ticker, direction, len(amf_df),
                    )
                    return direction
            except Exception as exc:  # noqa: BLE001
                logger.debug("AMF insider scrape failed for %s: %s", ticker, exc)

        # --- 2) FMP (secondary) ---------------------------------------------
        fmp_dir = self._insider_from_fmp(ticker)
        if fmp_dir is not None:
            return fmp_dir

        # --- 3) yfinance (tertiary) -----------------------------------------
        return self._insider_from_yfinance(ticker)

    @staticmethod
    def _score_amf_declarations(df: pd.DataFrame) -> int:
        """Map AMF Achat/Vente rows to +1 / -1 / 0."""
        if "Transaction" not in df.columns:
            return 0
        text = df["Transaction"].astype(str).str.lower()
        buys = int(text.str.contains("achat|acquisition|buy|purchase").sum())
        sells = int(text.str.contains("vente|cession|sale|sell").sum())
        net = buys - sells
        return 1 if net > 0 else (-1 if net < 0 else 0)

    def _insider_from_fmp(self, ticker: str) -> int | None:
        """FMP insider-trading net direction (+1 / -1 / 0), or None on failure.

        Returns:
            int: Scored direction when FMP returns a usable payload.
            None: Missing key, HTTP error, or empty/invalid response — caller
                should fall through to yfinance.
        """
        api_key = os.getenv("FMP_API_KEY")
        if not api_key:
            logger.debug("FMP_API_KEY unset; skipping FMP insider for %s.", ticker)
            return None
        # FMP expects US-style symbols; strip .PA/.AS suffix as best-effort.
        symbol = ticker.split(".")[0]
        url = (
            "https://financialmodelingprep.com/api/v4/insider-trading"
            f"?symbol={symbol}&apikey={api_key}"
        )
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                logger.debug(
                    "FMP insider HTTP %s for %s.", resp.status_code, ticker
                )
                return None
            payload = resp.json()
            if not isinstance(payload, list) or not payload:
                return None
            buys = 0
            sells = 0
            for row in payload[:40]:
                if not isinstance(row, dict):
                    continue
                ttype = str(
                    row.get("transactionType")
                    or row.get("acquistionOrDisposition")
                    or row.get("type")
                    or ""
                ).casefold()
                # FMP uses A/D codes or free text.
                if ttype in ("a", "acquisition", "purchase", "buy", "p-purchase"):
                    buys += 1
                elif ttype in ("d", "disposition", "sale", "sell", "s-sale"):
                    sells += 1
                elif "acqui" in ttype or "buy" in ttype or "purchase" in ttype:
                    buys += 1
                elif "dispos" in ttype or "sale" in ttype or "sell" in ttype:
                    sells += 1
            if buys == 0 and sells == 0:
                return None
            net = buys - sells
            direction = 1 if net > 0 else (-1 if net < 0 else 0)
            logger.info(
                "%s insider activity (FMP): buys=%d sells=%d -> %+d.",
                ticker, buys, sells, direction,
            )
            return direction
        except Exception:  # noqa: BLE001
            logger.debug("FMP insider unavailable for %s; falling through.", ticker)
            return None

    def _insider_from_yfinance(self, ticker: str) -> int:
        """yfinance insider net-direction logic (tertiary fallback)."""
        try:
            tx = yf.Ticker(ticker).insider_transactions
            if tx is None or not isinstance(tx, pd.DataFrame) or tx.empty:
                return 0

            text_col = next(
                (c for c in ("Text", "Transaction") if c in tx.columns), None
            )
            if text_col is None:
                return 0

            recent = tx.head(20)[text_col].astype(str).str.lower()
            buys = int(recent.str.contains("buy|purchase").sum())
            sells = int(recent.str.contains("sale|sell").sum())
            net = buys - sells
            direction = 1 if net > 0 else (-1 if net < 0 else 0)
            logger.info(
                "%s insider activity (yfinance): buys=%d sells=%d -> %+d.",
                ticker,
                buys,
                sells,
                direction,
            )
            return direction
        except Exception:  # noqa: BLE001
            logger.debug("Insider data unavailable for %s; neutral.", ticker)
            return 0

    # -------------------------------------------------- Polymarket ----------
    def get_polymarket_sentiment(self, query: str) -> float:
        """Best-effort Polymarket YES probability for a macro query.

        Tries the public Gamma API search; falls back to a deterministic stub
        so callers always get a float in ``[0, 1]``.
        """
        try:
            import json
            import urllib.parse
            import urllib.request

            q = urllib.parse.quote(query[:80])
            url = f"https://gamma-api.polymarket.com/public-search?q={q}&limit_per_type=3"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "PEA-Sniper-Terminal/1.0",
                         "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            events = (data or {}).get("events") or []
            for ev in events:
                markets = ev.get("markets") or []
                if not markets:
                    continue
                prices = markets[0].get("outcomePrices")
                if isinstance(prices, str):
                    prices = json.loads(prices)
                if isinstance(prices, (list, tuple)) and prices:
                    return round(float(prices[0]), 4)
        except Exception:  # noqa: BLE001
            logger.debug("Polymarket live fetch failed for %r", query, exc_info=True)

        seed = sum(ord(c) for c in query) % 31
        return round(0.35 + (seed / 30.0) * 0.30, 4)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    sensor = MacroAlphaSensor()
    print("European VIX (V2TX):", sensor.get_european_vix())
    print("Put/Call ASML.AS   :", sensor.get_put_call_ratio("ASML.AS"))
    print("Insider MC.PA      :", sensor.get_insider_activity("MC.PA"))
    print("Polymarket stub    :", sensor.get_polymarket_sentiment("recession 2026"))
```

## FILE: 00_data_sensors/market_prices_api.py
```python
"""Market data ingestion for PEA Sniper Terminal V-Prime.

Fetches daily OHLCV via the official ``yfinance`` API (no scraping), flattens
the multi-ticker response into the schema expected by ``TimeSeriesDB``
(Phase 2), and feeds it into DuckDB.

This is a pure ingestion layer: no indicator math, risk, or trading logic.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, List

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# Flat schema shared with TimeSeriesDB (Phase 2).
_FLAT_COLUMNS = ["Ticker", "Date", "Open", "High", "Low", "Close", "Volume"]
_OHLCV_ATTRS = ["Open", "High", "Low", "Close", "Volume"]


class MarketDataFetcher:
    """Downloads and normalizes daily OHLCV data from Yahoo Finance."""

    def fetch_daily_ohlcv(
        self, tickers: List[str], lookback_days: int = 252
    ) -> pd.DataFrame:
        """Download and flatten daily OHLCV for a batch of tickers.

        All tickers are downloaded in a single batched ``yf.download`` call to
        avoid rate limits. The multi-index response is flattened into the
        columns ``Ticker, Date, Open, High, Low, Close, Volume``.

        Args:
            tickers: List of Yahoo Finance ticker symbols.
            lookback_days: Calendar days of history to request (default 252).

        Returns:
            pd.DataFrame: Cleaned, flat OHLCV data. Empty DataFrame (with the
            correct columns) if nothing could be retrieved.
        """
        if not tickers:
            logger.warning("fetch_daily_ohlcv called with no tickers.")
            return pd.DataFrame(columns=_FLAT_COLUMNS)

        start_date = (datetime.now() - timedelta(days=lookback_days)).strftime(
            "%Y-%m-%d"
        )
        logger.info(
            "Downloading OHLCV for %d ticker(s) since %s.",
            len(tickers),
            start_date,
        )

        try:
            raw = yf.download(
                tickers,
                start=start_date,
                progress=False,
                auto_adjust=False,
                group_by="column",
                threads=True,
            )
        except Exception:  # noqa: BLE001 - never let an API error crash caller.
            logger.exception("yf.download failed for tickers: %s", tickers)
            return pd.DataFrame(columns=_FLAT_COLUMNS)

        if raw is None or raw.empty:
            logger.warning("yf.download returned no data for: %s", tickers)
            return pd.DataFrame(columns=_FLAT_COLUMNS)

        flat = self._flatten(raw, tickers)
        if flat.empty:
            return flat

        return self._clean(flat)

    def _flatten(self, raw: pd.DataFrame, tickers: List[str]) -> pd.DataFrame:
        """Restructure a yfinance response into the flat schema.

        Handles both the multi-ticker (MultiIndex columns) and single-ticker
        (flat columns) response shapes.

        Args:
            raw: Raw DataFrame returned by ``yf.download``.
            tickers: The originally requested tickers (used for the single case).

        Returns:
            pd.DataFrame: Flat OHLCV with the canonical column order.
        """
        if isinstance(raw.columns, pd.MultiIndex):
            # Columns are (Attribute, Ticker). Stack the ticker level into rows.
            stacked = raw.stack(level=1, future_stack=True)
            stacked = stacked.reset_index()
            # After reset_index: 'Date', the ticker level, then attributes.
            rename_map = {stacked.columns[0]: "Date", stacked.columns[1]: "Ticker"}
            stacked = stacked.rename(columns=rename_map)
            flat = stacked
        else:
            # Single ticker: attach the ticker name explicitly.
            flat = raw.reset_index().rename(columns={"index": "Date"})
            if "Date" not in flat.columns and "Datetime" in flat.columns:
                flat = flat.rename(columns={"Datetime": "Date"})
            flat["Ticker"] = tickers[0]

        missing = [c for c in _OHLCV_ATTRS if c not in flat.columns]
        if missing:
            logger.warning("Response missing attributes %s; got %s", missing,
                           list(flat.columns))
            return pd.DataFrame(columns=_FLAT_COLUMNS)

        flat = flat[_FLAT_COLUMNS].copy()
        flat["Date"] = pd.to_datetime(flat["Date"]).dt.tz_localize(None).dt.normalize()
        return flat

    def _clean(self, flat: pd.DataFrame) -> pd.DataFrame:
        """Handle NaNs per ticker and drop empty/delisted tickers.

        Forward- then backward-fills OHLCV within each ticker group. Tickers
        with no usable data at all are logged and dropped.

        Args:
            flat: Flat OHLCV DataFrame.

        Returns:
            pd.DataFrame: Cleaned data sorted by ``Ticker`` then ``Date``.
        """
        cleaned_frames: List[pd.DataFrame] = []
        for ticker, group in flat.groupby("Ticker", sort=False):
            price_slice = group[_OHLCV_ATTRS]
            if price_slice.dropna(how="all").empty:
                logger.warning("Ticker %s has no data; dropping.", ticker)
                continue
            group = group.sort_values("Date").copy()
            group[_OHLCV_ATTRS] = group[_OHLCV_ATTRS].ffill().bfill()
            group = group.dropna(subset=_OHLCV_ATTRS)
            if group.empty:
                logger.warning("Ticker %s empty after cleaning; dropping.", ticker)
                continue
            group["Volume"] = group["Volume"].fillna(0).astype("int64")
            cleaned_frames.append(group)

        if not cleaned_frames:
            logger.warning("No tickers survived cleaning.")
            return pd.DataFrame(columns=_FLAT_COLUMNS)

        result = pd.concat(cleaned_frames, ignore_index=True)
        result = result.sort_values(["Ticker", "Date"]).reset_index(drop=True)
        return result[_FLAT_COLUMNS]

    def update_database(
        self, db_manager: Any, tickers: List[str], lookback_days: int = 252
    ) -> bool:
        """Fetch OHLCV and upsert it into a ``TimeSeriesDB`` instance.

        Args:
            db_manager: A Phase 2 ``TimeSeriesDB`` (must expose ``upsert_ohlcv``).
            tickers: Ticker symbols to ingest.
            lookback_days: Calendar days of history to request (default 252).

        Returns:
            bool: ``True`` on success, ``False`` if any exception occurred.
        """
        try:
            df = self.fetch_daily_ohlcv(tickers, lookback_days=lookback_days)
            if df.empty:
                logger.warning("No data fetched; nothing to ingest.")
                return False

            rows = db_manager.upsert_ohlcv(df)
            n_tickers = df["Ticker"].nunique()
            logger.info(
                "Successfully ingested %d rows for %d ticker(s).", rows, n_tickers
            )
            return True
        except Exception:  # noqa: BLE001 - ingestion must never crash the daemon.
            logger.exception("Database update failed for tickers: %s", tickers)
            return False


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    fetcher = MarketDataFetcher()
    sample = ["MC.PA", "OR.PA", "AI.PA"]
    frame = fetcher.fetch_daily_ohlcv(sample, lookback_days=30)

    print("\n--- Fetched shape:", frame.shape)
    print("--- Columns:", list(frame.columns))
    print("--- Tickers:", sorted(frame["Ticker"].unique()) if not frame.empty else [])
    print(frame.tail(10).to_string(index=False))
```

## FILE: 00_data_sensors/scrapers/__init__.py
```python
"""French-market scrapers (AMF BDIF + Boursorama).

Isolated from the clean yfinance API layer. Every public method is antifragile.
"""

from amf_scraper import AmfInsiderScraper
from bourso_scraper import (
    BoursoramaScraper,
    bourso_slug_to_yahoo,
    yahoo_to_bourso_slug,
)

__all__ = [
    "AmfInsiderScraper",
    "BoursoramaScraper",
    "bourso_slug_to_yahoo",
    "yahoo_to_bourso_slug",
]
```

## FILE: 00_data_sensors/scrapers/_http.py
```python
"""Shared HTTP helpers for fragile French-market scrapers."""

from __future__ import annotations

import logging
import random
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

_USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15",
)

DEFAULT_TIMEOUT = 25


def stealth_headers() -> dict[str, str]:
    """Return a rotating browser-like header set."""
    return {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
    }


def rate_limit(min_s: float = 0.6, max_s: float = 1.8) -> None:
    """Sleep a random delay to reduce ban risk."""
    time.sleep(random.uniform(min_s, max_s))


def safe_get(
    url: str,
    *,
    session: requests.Session | None = None,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    expect_json: bool = False,
    quiet: bool = False,
) -> requests.Response | None:
    """GET with stealth headers. Returns ``None`` on any failure (never raises)."""
    log = logger.debug if quiet else logger.warning
    try:
        rate_limit()
        hdrs = {**stealth_headers(), **(headers or {})}
        client = session or requests
        resp = client.get(url, headers=hdrs, params=params, timeout=timeout)
        if resp.status_code in (403, 429):
            log("Scraper blocked (%s) for %s", resp.status_code, url)
            return None
        if resp.status_code >= 400:
            log("Scraper HTTP %s for %s", resp.status_code, url)
            return None
        if expect_json:
            ct = (resp.headers.get("content-type") or "").lower()
            if "json" not in ct and not resp.text.lstrip().startswith(("{", "[")):
                log("Scraper expected JSON, got non-JSON from %s", url)
                return None
        return resp
    except Exception as exc:  # noqa: BLE001
        log("Scraper GET failed for %s: %s", url, exc)
        return None
```

## FILE: 00_data_sensors/scrapers/amf_scraper.py
```python
"""AMF BDIF insider-declaration scraper (antifragile, multi-source).

Primary: AMF BDIF public search API (``/api/v1/informations``).
Secondary: enrich with ISIN from Boursorama profile when available.
Any failure returns an empty DataFrame so callers fall back to yfinance.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import requests

try:
    from _http import rate_limit, safe_get, stealth_headers
except ImportError:  # pragma: no cover
    from scrapers._http import rate_limit, safe_get, stealth_headers  # type: ignore

logger = logging.getLogger(__name__)

_BDIF_BASE = "https://bdif.amf-france.org"

# Process-wide circuit breaker: AMF BDIF is often WAF-blocked (HTTP 500).
# After a hard failure, skip further calls until the TTL elapses (antifragile
# retry — a temporary WAF blip must not kill AMF for weeks on a long-lived daemon).
_AMF_CIRCUIT_OPEN = False
_AMF_CIRCUIT_REASON = ""
_AMF_CIRCUIT_OPENED_AT: datetime | None = None
_AMF_CIRCUIT_TTL = timedelta(hours=12)


def amf_available() -> bool:
    """Return False when the BDIF circuit breaker is open (within TTL)."""
    global _AMF_CIRCUIT_OPEN, _AMF_CIRCUIT_OPENED_AT, _AMF_CIRCUIT_REASON
    if not _AMF_CIRCUIT_OPEN:
        return True
    if _AMF_CIRCUIT_OPENED_AT is None:
        return False
    if datetime.now(timezone.utc) - _AMF_CIRCUIT_OPENED_AT >= _AMF_CIRCUIT_TTL:
        logger.info(
            "AMF BDIF circuit RESET after %s — will retry.", _AMF_CIRCUIT_TTL
        )
        _AMF_CIRCUIT_OPEN = False
        _AMF_CIRCUIT_OPENED_AT = None
        _AMF_CIRCUIT_REASON = ""
        return True
    return False


def _trip_amf_circuit(reason: str) -> None:
    global _AMF_CIRCUIT_OPEN, _AMF_CIRCUIT_REASON, _AMF_CIRCUIT_OPENED_AT
    if not _AMF_CIRCUIT_OPEN:
        logger.info(
            "AMF BDIF circuit OPEN (%s) — skip AMF for %s then retry; "
            "using yfinance fallback.",
            reason, _AMF_CIRCUIT_TTL,
        )
    _AMF_CIRCUIT_OPEN = True
    _AMF_CIRCUIT_REASON = reason
    _AMF_CIRCUIT_OPENED_AT = datetime.now(timezone.utc)

_TICKER_TO_ISSUER: dict[str, str] = {
    "MC.PA": "LVMH", "OR.PA": "L'OREAL", "AI.PA": "AIR LIQUIDE",
    "RMS.PA": "HERMES", "TTE.PA": "TOTALENERGIES", "SAN.PA": "SANOFI",
    "SU.PA": "SCHNEIDER ELECTRIC", "AIR.PA": "AIRBUS", "BNP.PA": "BNP PARIBAS",
    "CS.PA": "AXA", "DG.PA": "VINCI", "SAF.PA": "SAFRAN",
    "EL.PA": "ESSILORLUXOTTICA", "KER.PA": "KERING", "RI.PA": "PERNOD RICARD",
    "ORA.PA": "ORANGE", "ENGI.PA": "ENGIE", "CAP.PA": "CAPGEMINI",
    "DSY.PA": "DASSAULT SYSTEMES", "STLAP.PA": "STELLANTIS",
    "STMPA.PA": "STMICROELECTRONICS", "HO.PA": "THALES", "ML.PA": "MICHELIN",
    "SGO.PA": "SAINT-GOBAIN", "GLE.PA": "SOCIETE GENERALE",
    "ACA.PA": "CREDIT AGRICOLE", "VIE.PA": "VEOLIA", "PUB.PA": "PUBLICIS",
    "BN.PA": "DANONE", "RNO.PA": "RENAULT", "FR.PA": "VALEO", "CW8.PA": "AMUNDI",
}


def _issuer_name(ticker: str) -> str:
    if ticker in _TICKER_TO_ISSUER:
        return _TICKER_TO_ISSUER[ticker]
    return ticker.split(".")[0].replace("-", " ").strip().upper()


class AmfInsiderScraper:
    """Fetches recent AMF dirigeant declarations for a Yahoo ticker."""

    def __init__(self) -> None:
        self._session = requests.Session()
        self.last_error: str | None = None

    def get_recent_declarations(
        self,
        ticker: str,
        *,
        isin: str | None = None,
        issuer: str | None = None,
    ) -> pd.DataFrame:
        """Return recent insider declarations as a DataFrame.

        Columns when available:
        ``Date, Insider, Transaction, Value, Volume, Price, Title, ISIN, Source``.

        Args:
            ticker: Yahoo symbol (e.g. ``MC.PA``).
            isin: Optional ISIN (from Boursorama profile) to refine search.
            issuer: Optional company name override.
        """
        self.last_error = None
        if not amf_available():
            self.last_error = _AMF_CIRCUIT_REASON or "circuit open"
            return pd.DataFrame()
        try:
            rate_limit(0.4, 1.0)
            # Skip homepage probe — API 500 is enough to trip the breaker.
            name = issuer or _issuer_name(ticker)
            rows = self._search_bdif(name, isin=isin)
            if not rows and isin and amf_available():
                rows = self._search_bdif(isin.split("_")[0], isin=isin)

            if not amf_available():
                self.last_error = _AMF_CIRCUIT_REASON
                return pd.DataFrame()

            if not rows:
                self.last_error = self.last_error or "no BDIF rows"
                logger.debug(
                    "AMF BDIF empty for %s (%s / %s).", ticker, name, isin
                )
                return pd.DataFrame()

            df = pd.DataFrame(rows)
            keep = [c for c in (
                "Date", "Insider", "Transaction", "Value", "Volume", "Price",
                "Title", "ISIN", "Source",
            ) if c in df.columns]
            return df[keep].reset_index(drop=True) if keep else pd.DataFrame()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            _trip_amf_circuit(str(exc))
            logger.debug("AmfInsiderScraper failed for %s: %s", ticker, exc)
            return pd.DataFrame()

    def get_declarations_for_profile(self, profile: dict) -> pd.DataFrame:
        """Convenience: use a Boursorama profile dict (isin + name + ticker)."""
        return self.get_recent_declarations(
            profile.get("ticker") or "",
            isin=profile.get("isin"),
            issuer=profile.get("name"),
        )

    def _search_bdif(
        self, query: str, *, isin: str | None = None
    ) -> list[dict[str, Any]]:
        """Query BDIF search with fail-fast on WAF blocks."""
        if not amf_available():
            return []
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=548)  # ~18 months
        attempts = [
            {
                "RechercheTexte": query,
                "TypesDocument": "DD",
                "DateDebut": start.strftime("%Y-%m-%dT00:00:00.000Z"),
                "DateFin": end.strftime("%Y-%m-%dT23:59:59.999Z"),
                "From": 0,
                "Size": 40,
            },
            {
                "RechercheTexte": query,
                "DateDebut": start.strftime("%Y-%m-%dT00:00:00.000Z"),
                "DateFin": end.strftime("%Y-%m-%dT23:59:59.999Z"),
                "From": 0,
                "Size": 40,
            },
        ]
        for params in attempts:
            if not amf_available():
                return []
            rate_limit(0.4, 1.0)
            resp = safe_get(
                _BDIF_BASE + "/api/v1/informations",
                session=self._session,
                headers={
                    **stealth_headers(),
                    "Accept": "application/json, text/plain, */*",
                    "Origin": _BDIF_BASE,
                    "Referer": _BDIF_BASE + "/",
                },
                params=params,
                expect_json=True,
                quiet=True,
            )
            if resp is None:
                self.last_error = "BDIF API blocked/HTTP error"
                _trip_amf_circuit("HTTP error / WAF on /api/v1/informations")
                return []
            try:
                payload = resp.json()
            except Exception:  # noqa: BLE001
                self.last_error = "BDIF JSON parse failed"
                _trip_amf_circuit("BDIF JSON parse failed")
                return []
            rows = self._parse_payload(payload, query, isin=isin)
            if rows:
                return rows
        return []

    @staticmethod
    def _parse_payload(
        payload: Any, query: str, *, isin: str | None = None
    ) -> list[dict[str, Any]]:
        """Normalize BDIF JSON into flat declaration rows."""
        items: list[Any] = []
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            for key in ("items", "results", "informations", "data", "content"):
                if isinstance(payload.get(key), list):
                    items = payload[key]
                    break
            if not items and payload:
                items = [payload]

        rows: list[dict[str, Any]] = []
        q = (query or "").lower()
        isin_clean = (isin or "").split("_")[0].upper()

        for item in items:
            if not isinstance(item, dict):
                continue
            title = str(
                item.get("titre") or item.get("title") or item.get("intitule")
                or item.get("objet") or ""
            )
            blob = " ".join(
                str(item.get(k, ""))
                for k in (
                    "titre", "title", "type", "typeDocument", "typeInformation",
                    "resume", "description", "emetteur", "societe", "isin",
                )
            ).lower()

            is_dd = any(
                tok in blob
                for tok in ("dirigeant", " dd", "dd ", "declaration", "déclar")
            )
            matches_issuer = q and q in blob or q in title.lower()
            matches_isin = bool(isin_clean) and isin_clean.lower() in blob
            if not (is_dd or matches_issuer or matches_isin):
                continue

            tx_type = "Achat" if any(
                w in blob for w in ("achat", "acquisition", "souscription")
            ) else ("Vente" if any(
                w in blob for w in ("vente", "cession", "disposal")
            ) else "Declaration")

            date_raw = (
                item.get("datePublication") or item.get("date")
                or item.get("dateDocument") or item.get("publishedAt") or ""
            )
            insider = str(
                item.get("declarant") or item.get("auteur")
                or item.get("emetteur") or item.get("societe") or "Dirigeant"
            )
            value = item.get("montant") or item.get("valeur") or item.get("value")
            volume = item.get("volume") or item.get("quantite") or item.get("shares")
            price = item.get("prix") or item.get("price") or item.get("prixUnitaire")
            doc_isin = item.get("isin") or isin_clean or ""

            rows.append({
                "Date": str(date_raw)[:10],
                "Insider": insider,
                "Transaction": tx_type,
                "Value": value,
                "Volume": volume,
                "Price": price,
                "Title": title[:240] or f"Declaration AMF — {query}",
                "ISIN": str(doc_isin).split("_")[0],
                "Source": "AMF BDIF",
            })
        return rows
```

## FILE: 00_data_sensors/scrapers/bourso_scraper.py
```python
"""Boursorama scraper — news, consensus, PEA flags, and PEA universe harvest.

Antifragile: any HTTP block / DOM change returns empty structures so callers
can fall back to yfinance. Never raises into the trading pipeline.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

try:
    from _http import rate_limit, safe_get, stealth_headers
except ImportError:  # pragma: no cover
    from scrapers._http import rate_limit, safe_get, stealth_headers  # type: ignore

logger = logging.getLogger(__name__)

_BOURSO_BASE = "https://www.boursorama.com"
_INDEX_SLUGS = {
    "1rPCAC", "1rPPX4", "1rPCESGP", "1rPPX5", "1rPPX8", "1rPCAPME", "1rPENPME",
    "2zPCN20", "2zPCM100", "2zPCS90", "2zPMS190",
}

# Explicit map for top holdings (Yahoo -> Boursorama slug).
_BOURSO_SLUGS: dict[str, str] = {
    "MC.PA": "1rPMC", "OR.PA": "1rPOR", "AI.PA": "1rPAI", "RMS.PA": "1rPRMS",
    "TTE.PA": "1rPTTE", "SAN.PA": "1rPSAN", "SU.PA": "1rPSU", "AIR.PA": "1rPAIR",
    "BNP.PA": "1rPBNP", "CS.PA": "1rPCS", "DG.PA": "1rPDG", "SAF.PA": "1rPSAF",
    "EL.PA": "1rPEL", "KER.PA": "1rPKER", "RI.PA": "1rPRI", "ORA.PA": "1rPORA",
    "ENGI.PA": "1rPENGI", "CAP.PA": "1rPCAP", "DSY.PA": "1rPDSY",
    "STLAP.PA": "1rPSTLAP", "STMPA.PA": "1rPSTMPA", "HO.PA": "1rPHO",
    "ML.PA": "1rPML", "SGO.PA": "1rPSGO", "GLE.PA": "1rPGLE", "ACA.PA": "1rPACA",
    "VIE.PA": "1rPVIE", "PUB.PA": "1rPPUB", "BN.PA": "1rPBN", "RNO.PA": "1rPRNO",
    "FR.PA": "1rPFR", "CW8.PA": "1rPCW8", "ASML.AS": "1rAASML", "SAP.DE": "1zSAP",
}

_EMPTY: dict[str, Any] = {
    "news": [],
    "sentiment": "Unknown",
    "consensus_score": None,
    "target_price": None,
    "potential_pct": None,
    "eligibility": [],
    "isin": None,
    "sector": None,
    "index": None,
    "exchange": None,
    "source": "Boursorama",
}

# Markets to crawl when building the PEA universe (label, market code, title hint).
_PEA_MARKETS: list[tuple[str, str, str]] = [
    ("SRD", "SRD", "SRD"),
    ("SBF120", "1rPPX4", "SBF 120"),
    ("CAC All-Tradable", "1rPPX5", "All-Tradable"),
    ("Compartment A", "2201", ""),
    ("Compartment B", "2202", ""),
    ("Compartment C", "2203", ""),
    ("Euronext Growth", "2240", ""),
    ("PEA-PME", "PEAPME", "PEA-PME"),
]


def yahoo_to_bourso_slug(ticker: str) -> str | None:
    """Map a Yahoo ticker to a Boursorama instrument slug."""
    if ticker in _BOURSO_SLUGS:
        return _BOURSO_SLUGS[ticker]
    if "." not in ticker:
        return f"1rP{ticker}"
    symbol, exch = ticker.rsplit(".", 1)
    prefix = {"PA": "1rP", "AS": "1rA", "BR": "1rB", "LS": "1rL",
              "DE": "1z", "MI": "1g", "MC": "1rE"}.get(exch.upper())
    return f"{prefix}{symbol}" if prefix else None


def bourso_slug_to_yahoo(slug: str) -> str | None:
    """Map a Boursorama slug (``1rPMC``) to a Yahoo ticker (``MC.PA``)."""
    slug = (slug or "").strip()
    for prefix, suffix in (
        ("1rP", ".PA"), ("1rA", ".AS"), ("1rB", ".BR"), ("1rL", ".LS"),
        ("1z", ".DE"), ("1g", ".MI"), ("1rE", ".MC"),
    ):
        if slug.startswith(prefix) and len(slug) > len(prefix):
            return slug[len(prefix):] + suffix
    return None


class BoursoramaScraper:
    """Rich Boursorama client: profile, news, consensus, PEA universe."""

    def __init__(self) -> None:
        self._session = requests.Session()

    # ------------------------------------------------------------------ API
    def get_retail_sentiment_and_news(self, ticker: str) -> dict:
        """Fetch news + soft sentiment (backward-compatible wrapper).

        Returns a dict with at least ``news`` (list[str]) and ``sentiment``.
        Extra keys (consensus, eligibility, ISIN…) are included when available.
        """
        profile = self.get_instrument_profile(ticker)
        if not profile:
            return dict(_EMPTY)
        # Keep legacy shape: news as list of title strings.
        titles = [n["title"] for n in profile.get("news_items") or [] if n.get("title")]
        out = dict(_EMPTY)
        out.update({
            "news": titles[:6],
            "news_items": profile.get("news_items") or [],
            "sentiment": profile.get("sentiment") or "Unknown",
            "consensus_score": profile.get("consensus_score"),
            "target_price": profile.get("target_price"),
            "potential_pct": profile.get("potential_pct"),
            "eligibility": profile.get("eligibility") or [],
            "isin": profile.get("isin"),
            "sector": profile.get("sector"),
            "index": profile.get("index"),
            "exchange": profile.get("exchange"),
            "source": "Boursorama",
        })
        return out

    def get_instrument_profile(self, ticker: str) -> dict[str, Any]:
        """Parse the full instrument page (eligibility, ISIN, news, consensus)."""
        try:
            slug = yahoo_to_bourso_slug(ticker)
            if not slug:
                logger.warning("No Boursorama slug for %s.", ticker)
                return {}
            url = f"{_BOURSO_BASE}/cours/{slug}/"
            resp = safe_get(
                url,
                session=self._session,
                headers={**stealth_headers(), "Referer": f"{_BOURSO_BASE}/"},
            )
            if resp is None:
                return {}
            if "captcha" in resp.text.lower() or "datadome" in resp.text.lower():
                logger.warning("Bourso blocked (captcha) for %s.", ticker)
                return {}

            soup = BeautifulSoup(resp.text, "html.parser")
            meta = self._parse_tracking_json(resp.text)
            news_items = self._extract_news_items(soup, limit=8)
            consensus = self._extract_consensus(soup.get_text(" ", strip=True))
            sentiment = self._sentiment_from_consensus(consensus.get("score"))
            if sentiment == "Unknown":
                sentiment = self._sentiment_from_wording(resp.text)

            isin_raw = meta.get("isin") or ""
            isin = isin_raw.split("_")[0] if isin_raw else None

            return {
                "ticker": ticker,
                "slug": slug,
                "name": meta.get("name"),
                "isin": isin,
                "sector": self._unescape(meta.get("sector")),
                "eligibility": meta.get("eligibility") or [],
                "index": meta.get("index"),
                "exchange": meta.get("exchange"),
                "pea_eligible": "PEA" in (meta.get("eligibility") or []),
                "srd_eligible": "SRD" in (meta.get("eligibility") or []),
                "consensus_score": consensus.get("score"),
                "target_price": consensus.get("target"),
                "potential_pct": consensus.get("potential"),
                "sentiment": sentiment,
                "news_items": news_items,
                "url": url,
                "source": "Boursorama",
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("Boursorama profile failed for %s: %s", ticker, exc)
            return {}

    def get_pea_universe(
        self,
        *,
        include_pea_pme: bool = True,
        max_pages_per_market: int = 25,
    ) -> list[dict[str, str]]:
        """Scrape Bourso's *Eligibilité PEA* filtered listings across markets.

        Uses ``quotation_az_filter[peaEligibility]=1`` (the real PEA checkbox
        on the cotations page), plus the dedicated PEA-PME market list.

        Returns:
            list[dict]: ``{slug, name, yahoo, market, pea_pme}`` rows (deduped).
        """
        found: dict[str, dict[str, str]] = {}
        markets = list(_PEA_MARKETS)
        if not include_pea_pme:
            markets = [m for m in markets if m[1] != "PEAPME"]

        for label, code, title_hint in markets:
            try:
                rows = self._harvest_market(
                    market=code,
                    pea_eligibility=True,
                    title_hint=title_hint,
                    max_pages=max_pages_per_market,
                    label=label,
                )
                # PEA-PME page also without checkbox (all PME are PEA-eligible).
                if code == "PEAPME":
                    rows += self._harvest_market(
                        market="PEAPME",
                        pea_eligibility=False,
                        title_hint="PEA-PME",
                        max_pages=max_pages_per_market,
                        label="PEA-PME",
                    )
                for row in rows:
                    slug = row["slug"]
                    prev = found.get(slug)
                    if prev is None:
                        found[slug] = row
                    else:
                        # Prefer richer market tags.
                        if row.get("pea_pme") == "true":
                            prev["pea_pme"] = "true"
                        if row.get("market") == "SRD":
                            prev["market"] = "SRD"
                logger.info(
                    "Bourso PEA harvest %s: +%d (running total %d).",
                    label, len(rows), len(found),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Bourso PEA harvest failed for %s: %s", label, exc)

        return sorted(found.values(), key=lambda r: r.get("name", ""))

    # ------------------------------------------------------------- internals
    def _harvest_market(
        self,
        *,
        market: str,
        pea_eligibility: bool,
        title_hint: str,
        max_pages: int,
        label: str,
    ) -> list[dict[str, str]]:
        """Paginate one cotations filter; stop on empty page or title bleed."""
        out: list[dict[str, str]] = []
        seen: set[str] = set()
        for page in range(1, max_pages + 1):
            params = []
            if market:
                params.append(f"quotation_az_filter%5Bmarket%5D={market}")
            if pea_eligibility:
                params.append("quotation_az_filter%5BpeaEligibility%5D=1")
            qs = "&".join(params)
            if page == 1:
                url = f"{_BOURSO_BASE}/bourse/actions/cotations/?{qs}"
            else:
                url = f"{_BOURSO_BASE}/bourse/actions/cotations/page-{page}?{qs}"

            resp = safe_get(
                url,
                session=self._session,
                headers={**stealth_headers(), "Referer": f"{_BOURSO_BASE}/"},
            )
            if resp is None:
                break
            soup = BeautifulSoup(resp.text, "html.parser")
            title = (soup.title.get_text(strip=True) if soup.title else "")

            # Stop if pagination bled into another market (common Bourso quirk).
            if page > 1 and title_hint and title_hint not in title:
                if market == "PEAPME" and "PEA-PME" not in title:
                    logger.debug("PEA-PME bleed at page %d (%s).", page, title[:40])
                    break
                if market == "SRD" and "SRD" not in title:
                    break

            added = 0
            for a in soup.select("a[href*='/cours/']"):
                href = a.get("href") or ""
                name = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
                name = re.sub(r"\s*[+\-]\d+,\d+%.*$", "", name).strip()
                m = re.search(r"/cours/(1rP[A-Z0-9]+)/?", href)
                if not m:
                    continue
                slug = m.group(1)
                if slug in _INDEX_SLUGS or slug in seen or len(name) < 2:
                    continue
                if name.lower().startswith("cours "):
                    continue
                yahoo = bourso_slug_to_yahoo(slug)
                if not yahoo:
                    continue
                seen.add(slug)
                out.append({
                    "slug": slug,
                    "name": name,
                    "yahoo": yahoo,
                    "market": label,
                    "pea_pme": "true" if market == "PEAPME" else "false",
                })
                added += 1
            if added == 0 and page > 1:
                break
        return out

    @staticmethod
    def _parse_tracking_json(html: str) -> dict[str, Any]:
        """Extract fv_* analytics fields embedded in the instrument page."""
        meta: dict[str, Any] = {}
        m = re.search(
            r'"fv_secteur_activite":"([^"]*)".*?"fv_code_isin":"([^"]*)".*?'
            r'"fv_symb_societe":"([^"]*)".*?"fv_eligibilite":(\[[^\]]*\]).*?'
            r'"fv_indice_principal":"([^"]*)".*?"fv_bourse_label":"([^"]*)"',
            html,
            flags=re.S,
        )
        if m:
            sector, isin, slug, elig_raw, index, exchange = m.groups()
            try:
                eligibility = re.findall(r'"([^"]+)"', elig_raw)
            except Exception:  # noqa: BLE001
                eligibility = []
            meta.update({
                "sector": sector,
                "isin": isin,
                "slug": slug,
                "eligibility": eligibility,
                "index": index,
                "exchange": exchange,
            })
        # Name from <title>
        tm = re.search(r"<title>([^|<]+)", html, re.I)
        if tm:
            meta["name"] = tm.group(1).strip()
        return meta

    @staticmethod
    def _extract_news_items(soup: BeautifulSoup, limit: int = 8) -> list[dict]:
        """Pull latest news with title + absolute link."""
        items: list[dict] = []
        seen: set[str] = set()
        for a in soup.select("a[href*='/bourse/actualites/']"):
            title = re.sub(r"\s+", " ", (a.get_text() or "").strip())
            href = a.get("href") or ""
            if len(title) < 25:
                continue
            if "calendrier" in href.lower() or title.lower().startswith("toutes"):
                continue
            key = title.casefold()
            if key in seen:
                continue
            seen.add(key)
            # Best-effort date from nearby text.
            parent = a.find_parent(["li", "div", "article", "tr"])
            date = ""
            if parent is not None:
                blob = parent.get_text(" ", strip=True)
                dm = re.search(
                    r"(\d{1,2}\s+(?:janv|févr|mars|avr|mai|juin|juil|août|"
                    r"sept|oct|nov|déc)\.?\s+\d{4}"
                    r"|\d{2}/\d{2}/\d{4}"
                    r"|(?:hier|aujourd'?hui))",
                    blob,
                    re.I,
                )
                if dm:
                    date = dm.group(0)
            provider = ""
            if parent is not None:
                pm = re.search(
                    r"information fournie par\s+([A-Za-z0-9 .&\-]+)",
                    parent.get_text(" ", strip=True),
                    re.I,
                )
                if pm:
                    provider = pm.group(1).strip()
            items.append({
                "title": title,
                "link": urljoin(_BOURSO_BASE, href),
                "date": date or "Recent",
                "provider": provider or "Boursorama",
            })
            if len(items) >= limit:
                break
        return items

    @staticmethod
    def _extract_consensus(text: str) -> dict[str, float | None]:
        """Parse analyst consensus score, target price, and upside %."""
        out: dict[str, float | None] = {
            "score": None, "target": None, "potential": None,
        }
        m = re.search(
            r"Objectif de cours.*?(\d+[,\.]\d+)\s*EUR"
            r".{0,40}Potentiel:\s*([+\-]?\d+[,\.]\d+)\s*%",
            text,
            re.I | re.S,
        )
        if m:
            try:
                out["target"] = float(m.group(1).replace(",", "."))
                out["potential"] = float(m.group(2).replace(",", "."))
            except ValueError:
                pass
        # Bourso scale ~1 (Buy) to 5 (Sell), often shown near consensus.
        m2 = re.search(
            r"Consensus des analystes[^0-9]{0,100}?(\d[,\.]\d{2})",
            text,
            re.I,
        )
        if m2:
            try:
                out["score"] = float(m2.group(1).replace(",", "."))
            except ValueError:
                pass
        # Fallback: standalone "1,92" after potential block.
        if out["score"] is None:
            m3 = re.search(
                r"Potentiel:\s*[+\-]?\d+[,\.]\d+\s*%\s*(\d[,\.]\d{2})",
                text,
                re.I,
            )
            if m3:
                try:
                    out["score"] = float(m3.group(1).replace(",", "."))
                except ValueError:
                    pass
        return out

    @staticmethod
    def _sentiment_from_consensus(score: float | None) -> str:
        if score is None:
            return "Unknown"
        if score <= 2.2:
            return "Bullish"
        if score >= 3.5:
            return "Bearish"
        return "Neutral"

    @staticmethod
    def _sentiment_from_wording(html: str) -> str:
        low = html.lower()
        bull = sum(low.count(w) for w in ("acheter", "renforcer", "haussier"))
        bear = sum(low.count(w) for w in ("vendre", "alléger", "alleger", "baissier"))
        if bull > bear + 2:
            return "Bullish"
        if bear > bull + 2:
            return "Bearish"
        return "Unknown"

    @staticmethod
    def _unescape(value: str | None) -> str | None:
        if not value:
            return value
        try:
            import codecs
            # Bourso embeds literal \\u00xx sequences in the tracking JSON.
            if "\\u" in value:
                return codecs.decode(value, "unicode_escape")
            return value
        except Exception:  # noqa: BLE001
            return value
```

## FILE: 01_memory_core/__init__.py
```python

```

## FILE: 01_memory_core/data_models.py
```python
"""Strict data contracts for PEA Sniper Terminal V-Prime.

This module defines the Pydantic V2 models that flow between every layer of the
system (data sensors -> quant engine -> risk portfolio -> orchestrator ->
interfaces). Validating objects at module boundaries prevents malformed data
from ever reaching the risk or execution logic.

No trading logic, API calls, or database code lives here by design.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field


def _utcnow() -> datetime:
    """Return the current timezone-aware UTC timestamp.

    Returns:
        datetime: The current time in UTC.
    """
    return datetime.now(timezone.utc)


class SignalType(str, Enum):
    """Direction of a trading signal."""

    BUY = "BUY"
    SELL = "SELL"


class SignalStatus(str, Enum):
    """Lifecycle state of a signal as it moves through the orchestrator."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    EXECUTED = "EXECUTED"


class MarketRegime(str, Enum):
    """Coarse classification of the prevailing market environment."""

    BULL = "BULL"
    BEAR = "BEAR"
    CHOPPY = "CHOPPY"
    VOLATILE = "VOLATILE"


class Position(BaseModel):
    """A single open holding in the PEA portfolio.

    Attributes:
        ticker: Yahoo Finance ticker symbol (e.g. ``MC.PA``).
        qty_shares: Number of whole shares held. PEA forbids fractional shares.
        avg_entry_price: Volume-weighted average entry price in EUR.
        current_price: Latest known market price in EUR.
        sector: Sector bucket used by the correlation firewall.
    """

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    ticker: str = Field(..., min_length=1, description="Yahoo Finance ticker.")
    qty_shares: int = Field(..., ge=0, description="Whole shares (no fractions).")
    avg_entry_price: float = Field(..., gt=0, description="Avg entry price (EUR).")
    current_price: float = Field(..., gt=0, description="Latest price (EUR).")
    sector: str = Field(..., min_length=1, description="Sector classification.")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def market_value(self) -> float:
        """Current market value of the position in EUR.

        Returns:
            float: ``current_price * qty_shares``.
        """
        return self.current_price * self.qty_shares

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unrealized_pnl_pct(self) -> float:
        """Unrealized profit/loss as a fraction of the entry price.

        Returns:
            float: ``(current_price - avg_entry_price) / avg_entry_price``.
                A value of ``0.10`` represents a +10% unrealized gain.
        """
        return (self.current_price - self.avg_entry_price) / self.avg_entry_price


class PortfolioState(BaseModel):
    """Snapshot of the full portfolio at a point in time.

    Attributes:
        cash_available: Uninvested cash in EUR.
        total_equity: Total account value (cash + positions market value) in EUR.
        positions: List of currently open positions.
        last_updated: Timestamp of this snapshot (UTC).
    """

    model_config = ConfigDict(validate_assignment=True)

    cash_available: float = Field(..., ge=0, description="Uninvested cash (EUR).")
    total_equity: float = Field(..., ge=0, description="Total account value (EUR).")
    positions: List[Position] = Field(default_factory=list)
    last_updated: datetime = Field(default_factory=_utcnow)

    def get_sector_weight(self, sector_name: str) -> float:
        """Compute the fraction of total equity allocated to a sector.

        Args:
            sector_name: Sector to measure (case-insensitive match).

        Returns:
            float: Sector market value divided by ``total_equity``. Returns
                ``0.0`` when total equity is zero to avoid division errors.
        """
        if self.total_equity <= 0:
            return 0.0
        sector_value = sum(
            pos.market_value
            for pos in self.positions
            if pos.sector.casefold() == sector_name.casefold()
        )
        return sector_value / self.total_equity


class Signal(BaseModel):
    """A candidate trade produced by the quant engine.

    LLMs never create these; they are generated purely from mathematical
    conditions and only explained downstream in the interface layer.

    Attributes:
        id: Unique identifier (UUID4 hex string).
        ticker: Yahoo Finance ticker the signal refers to.
        signal_type: BUY or SELL.
        status: Current lifecycle state (defaults to PENDING).
        score: Composite conviction score from 0 to 100.
        target_qty: Whole-share quantity, set later by the position sizer.
        created_at: Emission timestamp (UTC).
        reason: Human-readable explanation surfaced in the UI.
    """

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    id: str = Field(default_factory=lambda: uuid4().hex, description="UUID4 id.")
    ticker: str = Field(..., min_length=1, description="Target ticker.")
    signal_type: SignalType = Field(..., description="BUY or SELL.")
    status: SignalStatus = Field(default=SignalStatus.PENDING)
    score: float = Field(..., ge=0, le=100, description="Conviction score 0-100.")
    target_qty: Optional[int] = Field(
        default=None, ge=0, description="Whole shares set after sizing."
    )
    created_at: datetime = Field(default_factory=_utcnow)
    reason: str = Field(default="", description="Explanation for the UI.")
```

## FILE: 01_memory_core/duckdb_manager.py
```python
"""DuckDB time-series engine for PEA Sniper Terminal V-Prime.

DuckDB stores heavy OHLCV history and serves fast columnar reads to the quant
engine (pandas-ta). This is a pure I/O layer: no indicator math, no trading
logic, no API fetching lives here.
"""

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

import duckdb
import pandas as pd

logger = logging.getLogger(__name__)

# database/ lives at the project root (one level up from 01_memory_core/).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DB_PATH = _PROJECT_ROOT / "database" / "timeseries.duckdb"

# Canonical OHLCV column order used for inserts/reads.
_OHLCV_COLUMNS = ["Ticker", "Date", "Open", "High", "Low", "Close", "Volume"]


class TimeSeriesDB:
    """Persistence gateway for OHLCV time-series stored in DuckDB.

    Attributes:
        db_path: Absolute path to the DuckDB database file.
    """

    def __init__(self, db_path: Optional[Path | str] = None) -> None:
        """Initialize the manager and ensure the database directory exists.

        Args:
            db_path: Optional custom path to the DuckDB file. Defaults to
                ``<project_root>/database/timeseries.duckdb``.
        """
        self.db_path: Path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        logger.debug("TimeSeriesDB using database at %s", self.db_path)

    @contextmanager
    def _connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        """Yield a DuckDB connection that always closes.

        Yields:
            duckdb.DuckDBPyConnection: An open connection.

        Raises:
            duckdb.Error: Propagated if any DB error occurs.
        """
        conn = duckdb.connect(str(self.db_path))
        try:
            yield conn
        except duckdb.Error:
            logger.exception("DuckDB operation failed.")
            raise
        finally:
            conn.close()

    def init_db(self) -> None:
        """Create the ``ohlcv_data`` table if it does not already exist.

        A composite primary key on ``(ticker, date)`` enforces one row per
        ticker per day and enables efficient upserts.
        """
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ohlcv_data (
                        ticker  VARCHAR NOT NULL,
                        date    DATE     NOT NULL,
                        open    DOUBLE,
                        high    DOUBLE,
                        low     DOUBLE,
                        close   DOUBLE,
                        volume  BIGINT,
                        PRIMARY KEY (ticker, date)
                    );
                    """
                )
            logger.info("DuckDB schema initialized at %s", self.db_path)
        except duckdb.Error:
            logger.exception("Failed to initialize DuckDB schema.")
            raise

    def upsert_ohlcv(self, df: pd.DataFrame) -> int:
        """Insert or replace OHLCV rows from a DataFrame.

        Args:
            df: DataFrame with columns ``Ticker``, ``Date``, ``Open``, ``High``,
                ``Low``, ``Close`` and ``Volume`` (typically from yfinance).

        Returns:
            int: The number of rows submitted for upsert.

        Raises:
            ValueError: If required columns are missing.
            duckdb.Error: If the database operation fails.
        """
        if df is None or df.empty:
            logger.warning("upsert_ohlcv received an empty DataFrame; skipping.")
            return 0

        missing = [c for c in _OHLCV_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"DataFrame missing required columns: {missing}")

        # Work on a normalized copy in the canonical column order.
        payload = df[_OHLCV_COLUMNS].copy()
        payload["Date"] = pd.to_datetime(payload["Date"]).dt.date

        try:
            with self._connect() as conn:
                # Register the DataFrame so DuckDB can read it directly.
                conn.register("incoming_ohlcv", payload)
                conn.execute(
                    """
                    INSERT INTO ohlcv_data
                        (ticker, date, open, high, low, close, volume)
                    SELECT Ticker, Date, Open, High, Low, Close, Volume
                    FROM incoming_ohlcv
                    ON CONFLICT (ticker, date) DO UPDATE SET
                        open   = excluded.open,
                        high   = excluded.high,
                        low    = excluded.low,
                        close  = excluded.close,
                        volume = excluded.volume;
                    """
                )
                conn.unregister("incoming_ohlcv")
            logger.info("Upserted %d OHLCV rows into DuckDB.", len(payload))
            return len(payload)
        except duckdb.Error:
            logger.exception("Failed to upsert OHLCV data.")
            raise

    def get_historical_prices(self, ticker: str, days: int = 252) -> pd.DataFrame:
        """Fetch the most recent ``days`` of OHLCV for a ticker, chronologically.

        Args:
            ticker: The ticker symbol to query.
            days: Number of most-recent trading days to return (default 252).

        Returns:
            pd.DataFrame: Columns ``Ticker``, ``Date``, ``Open``, ``High``,
            ``Low``, ``Close``, ``Volume`` sorted ascending by date and ready
            for pandas-ta. Empty DataFrame (with correct columns) if none found.
        """
        try:
            with self._connect() as conn:
                # Take the last N rows by date, then re-sort ascending so the
                # output is chronological for indicator calculations.
                result = conn.execute(
                    """
                    SELECT ticker AS Ticker,
                           date   AS Date,
                           open   AS Open,
                           high   AS High,
                           low    AS Low,
                           close  AS Close,
                           volume AS Volume
                    FROM (
                        SELECT *
                        FROM ohlcv_data
                        WHERE ticker = ?
                        ORDER BY date DESC
                        LIMIT ?
                    )
                    ORDER BY date ASC;
                    """,
                    [ticker, days],
                ).fetch_df()
            logger.debug(
                "Fetched %d rows of history for %s.", len(result), ticker
            )
            if result.empty:
                return pd.DataFrame(columns=_OHLCV_COLUMNS)
            return result
        except duckdb.Error:
            logger.exception("Failed to fetch historical prices for %s.", ticker)
            raise
```

## FILE: 01_memory_core/sqlite_portfolio.py
```python
"""SQLite state manager for PEA Sniper Terminal V-Prime.

This module owns application state persistence: the current PEA account
snapshot, open positions, and the audit log of every signal and its lifecycle.

It is a pure I/O layer. No trading, risk, or API logic lives here. All queries
are parameterized and every connection is context-managed so it closes cleanly
even on error.
"""

import logging
import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

import pandas as pd

# The module directory name starts with a digit, so it is not importable as a
# normal package. Adding this file's directory to sys.path lets us import the
# Phase 1 data contracts regardless of how the process is launched.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_models import Position, PortfolioState, Signal  # noqa: E402

logger = logging.getLogger(__name__)

# database/ lives at the project root (one level up from 01_memory_core/).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DB_PATH = _PROJECT_ROOT / "database" / "portfolio.db"


class PortfolioDB:
    """Persistence gateway for PEA account state, positions, and audit logs.

    Attributes:
        db_path: Absolute path to the SQLite database file.
    """

    def __init__(self, db_path: Optional[Path | str] = None) -> None:
        """Initialize the manager and ensure the database directory exists.

        Args:
            db_path: Optional custom path to the SQLite file. Defaults to
                ``<project_root>/database/portfolio.db``.
        """
        self.db_path: Path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        logger.debug("PortfolioDB using database at %s", self.db_path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a SQLite connection, committing on success and always closing.

        Yields:
            sqlite3.Connection: A connection with ``Row`` factory and foreign
            keys enabled.

        Raises:
            sqlite3.Error: Propagated after a rollback if any DB error occurs.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            logger.exception("SQLite operation failed; rolled back.")
            raise
        finally:
            conn.close()

    def init_db(self) -> None:
        """Create the ``account_state``, ``positions`` and ``audit_logs`` tables.

        The operation is idempotent (``IF NOT EXISTS``).
        """
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS account_state (
                        id              INTEGER PRIMARY KEY CHECK (id = 1),
                        cash_available  REAL    NOT NULL,
                        total_equity    REAL    NOT NULL,
                        last_updated    TEXT    NOT NULL
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS positions (
                        ticker           TEXT PRIMARY KEY,
                        qty_shares       INTEGER NOT NULL,
                        avg_entry_price  REAL    NOT NULL,
                        current_price    REAL    NOT NULL,
                        sector           TEXT    NOT NULL,
                        last_updated     TEXT    NOT NULL
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS audit_logs (
                        id           TEXT PRIMARY KEY,
                        ticker       TEXT NOT NULL,
                        signal_type  TEXT NOT NULL,
                        status       TEXT NOT NULL,
                        score        REAL NOT NULL,
                        reason       TEXT,
                        created_at   TEXT NOT NULL
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS portfolio_history (
                        date    TEXT PRIMARY KEY,
                        equity  REAL NOT NULL,
                        cash    REAL NOT NULL
                    );
                    """
                )
            logger.info("SQLite schema initialized at %s", self.db_path)
        except sqlite3.Error:
            logger.exception("Failed to initialize SQLite schema.")
            raise

    def get_portfolio_state(self) -> PortfolioState:
        """Read the account state and open positions into a Pydantic model.

        Returns:
            PortfolioState: The current portfolio. If no account row exists yet,
            an empty portfolio (zero cash/equity, no positions) is returned.
        """
        try:
            with self._connect() as conn:
                account = conn.execute(
                    "SELECT cash_available, total_equity, last_updated "
                    "FROM account_state WHERE id = 1;"
                ).fetchone()

                rows = conn.execute(
                    "SELECT ticker, qty_shares, avg_entry_price, current_price, "
                    "sector FROM positions ORDER BY ticker;"
                ).fetchall()

            positions = [
                Position(
                    ticker=row["ticker"],
                    qty_shares=row["qty_shares"],
                    avg_entry_price=row["avg_entry_price"],
                    current_price=row["current_price"],
                    sector=row["sector"],
                )
                for row in rows
            ]

            if account is None:
                logger.warning("No account_state row found; returning empty state.")
                return PortfolioState(
                    cash_available=0.0, total_equity=0.0, positions=positions
                )

            return PortfolioState(
                cash_available=account["cash_available"],
                total_equity=account["total_equity"],
                positions=positions,
                last_updated=datetime.fromisoformat(account["last_updated"]),
            )
        except sqlite3.Error:
            logger.exception("Failed to read portfolio state.")
            raise

    def update_portfolio(self, state: PortfolioState) -> None:
        """Persist a full portfolio snapshot.

        Upserts the single ``account_state`` row (id=1) and fully refreshes the
        ``positions`` table to match ``state.positions``.

        Args:
            state: The portfolio snapshot to persist.
        """
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO account_state
                        (id, cash_available, total_equity, last_updated)
                    VALUES (1, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        cash_available = excluded.cash_available,
                        total_equity   = excluded.total_equity,
                        last_updated   = excluded.last_updated;
                    """,
                    (
                        state.cash_available,
                        state.total_equity,
                        state.last_updated.isoformat(),
                    ),
                )

                conn.execute("DELETE FROM positions;")
                now = datetime.now(timezone.utc).isoformat()
                conn.executemany(
                    """
                    INSERT INTO positions
                        (ticker, qty_shares, avg_entry_price, current_price,
                         sector, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    [
                        (
                            p.ticker,
                            p.qty_shares,
                            p.avg_entry_price,
                            p.current_price,
                            p.sector,
                            now,
                        )
                        for p in state.positions
                    ],
                )

                # Daily equity curve snapshot (one row per calendar day).
                day_key = (
                    state.last_updated.date().isoformat()
                    if hasattr(state.last_updated, "date")
                    else str(state.last_updated)[:10]
                )
                conn.execute(
                    """
                    INSERT INTO portfolio_history (date, equity, cash)
                    VALUES (?, ?, ?)
                    ON CONFLICT(date) DO UPDATE SET
                        equity = excluded.equity,
                        cash   = excluded.cash;
                    """,
                    (day_key, float(state.total_equity), float(state.cash_available)),
                )
            logger.info(
                "Portfolio updated: equity=%.2f cash=%.2f positions=%d",
                state.total_equity,
                state.cash_available,
                len(state.positions),
            )
        except sqlite3.Error:
            logger.exception("Failed to update portfolio.")
            raise

    def get_equity_curve(self) -> pd.DataFrame:
        """Return the daily equity curve sorted by date ascending.

        Returns:
            pd.DataFrame: Columns ``date``, ``equity``, ``cash``. Empty if none.
        """
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT date, equity, cash FROM portfolio_history "
                    "ORDER BY date ASC;"
                ).fetchall()
            if not rows:
                return pd.DataFrame(columns=["date", "equity", "cash"])
            return pd.DataFrame(
                [{"date": r["date"], "equity": r["equity"], "cash": r["cash"]}
                 for r in rows]
            )
        except sqlite3.Error:
            logger.exception("Failed to read portfolio_history.")
            return pd.DataFrame(columns=["date", "equity", "cash"])

    def log_signal(self, signal: Signal) -> None:
        """Insert a signal or update its lifecycle state in ``audit_logs``.

        Args:
            signal: The signal to record. Upsert key is ``signal.id``.
        """
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO audit_logs
                        (id, ticker, signal_type, status, score, reason,
                         created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        status = excluded.status,
                        score  = excluded.score,
                        reason = excluded.reason;
                    """,
                    (
                        signal.id,
                        signal.ticker,
                        signal.signal_type.value,
                        signal.status.value,
                        signal.score,
                        signal.reason,
                        signal.created_at.isoformat(),
                    ),
                )
            logger.info(
                "Signal logged: %s %s %s status=%s",
                signal.id[:8],
                signal.ticker,
                signal.signal_type.value,
                signal.status.value,
            )
        except sqlite3.Error:
            logger.exception("Failed to log signal %s.", signal.id)
            raise

    def fetch_signals_by_status(
        self, statuses: list[str], limit: int | None = None
    ) -> list[dict]:
        """Read audit-log rows matching one or more statuses (read-only).

        Args:
            statuses: Status values to include (e.g. ``["PENDING"]`` or
                ``["EXECUTED", "REVOKED"]``).
            limit: Optional maximum number of rows (most recent first).

        Returns:
            list[dict]: Rows with keys ``id, ticker, signal_type, status,
            score, reason, created_at``, ordered by ``created_at`` descending.
        """
        if not statuses:
            return []

        placeholders = ",".join("?" for _ in statuses)
        query = (
            "SELECT id, ticker, signal_type, status, score, reason, created_at "
            "FROM audit_logs "
            f"WHERE status IN ({placeholders}) "
            "ORDER BY created_at DESC"
        )
        params: list = list(statuses)
        if limit is not None:
            query += " LIMIT ?"
            params.append(int(limit))

        try:
            with self._connect() as conn:
                rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error:
            logger.exception("Failed to fetch signals by status %s.", statuses)
            raise

    def fetch_signals_since(self, since_iso: str) -> list[dict]:
        """Read audit-log rows created at or after an ISO timestamp (read-only).

        Args:
            since_iso: Lower bound as an ISO-8601 string (e.g.
                ``"2026-07-08T00:00:00+00:00"``). Comparison is lexical, which
                is correct for zero-padded ISO timestamps.

        Returns:
            list[dict]: Rows with keys ``id, ticker, signal_type, status,
            score, reason, created_at``, ordered by ``created_at`` descending.
        """
        query = (
            "SELECT id, ticker, signal_type, status, score, reason, created_at "
            "FROM audit_logs "
            "WHERE created_at >= ? "
            "ORDER BY created_at DESC"
        )
        try:
            with self._connect() as conn:
                rows = conn.execute(query, (since_iso,)).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error:
            logger.exception("Failed to fetch signals since %s.", since_iso)
            raise
```

## FILE: 02_quant_engine/__init__.py
```python

```

## FILE: 02_quant_engine/smart_dca_engine.py
```python
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
```

## FILE: 02_quant_engine/technical_scorer.py
```python
"""Quantitative signal engine for PEA Sniper Terminal V-Prime.

Reads OHLCV history from DuckDB, computes technical indicators via the
pandas-ta accessor, and emits raw ``Signal`` objects from purely mathematical
rules (Mean-Reversion Exhaustion).

This module is 100% math: no LLMs, no APIs, no risk/portfolio/broker logic.
"""

import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, List

import pandas as pd
import yaml

try:  # yfinance is only needed for the optional Quality (EPS) filter.
    import yfinance as yf
except Exception:  # noqa: BLE001 - keep the pure-math engine importable offline.
    yf = None  # type: ignore[assignment]

# pandas-ta registers the ``.ta`` DataFrame accessor on import. The classic
# fork is used because upstream ``pandas_ta`` 0.4.x pulls in numba (no wheel
# for Python 3.13 / arm64) and 0.3.x breaks on numpy 2.x.
try:  # pragma: no cover - environment-dependent import.
    import pandas_ta as ta  # noqa: F401
except ImportError:  # pragma: no cover
    import pandas_ta_classic as ta  # noqa: F401

# 01_memory_core starts with a digit, so it is not a normal package. Add it to
# sys.path so the Phase 1 data contracts import regardless of launch context.
_CORE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "01_memory_core"
)
sys.path.insert(0, _CORE_DIR)

from data_models import Signal, SignalStatus, SignalType  # noqa: E402

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_DIR = _PROJECT_ROOT / "config"

# Minimum history required to compute a valid SMA-200.
_MIN_ROWS = 200
_DEFAULT_RSI_OVERSOLD = 30.0


class SignalGenerator:
    """Generates raw BUY signals from mathematical price-action rules."""

    def __init__(self, config_path: str | Path | None = None) -> None:
        """Load optional thresholds from ``risk_params.yaml``."""
        path = Path(config_path) if config_path else _DEFAULT_CONFIG_DIR
        risk_file = path if path.is_file() else path / "risk_params.yaml"
        risk: dict = {}
        if risk_file.exists():
            with open(risk_file, "r", encoding="utf-8") as fh:
                risk = yaml.safe_load(fh) or {}
        self.rsi_oversold: float = float(
            risk.get("RSI_OVERSOLD_THRESHOLD", _DEFAULT_RSI_OVERSOLD)
        )

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Attach SMA-50, SMA-200 and RSI-14 columns for a single ticker.

        Args:
            df: Chronologically-sorted OHLCV for ONE ticker. Must contain a
                ``Close`` column.

        Returns:
            pd.DataFrame: A copy of ``df`` with ``SMA_50``, ``SMA_200`` and
            ``RSI_14`` columns appended.
        """
        out = df.copy()
        close = out["Close"]
        out["SMA_5"] = out.ta.sma(close=close, length=5)
        out["SMA_50"] = out.ta.sma(close=close, length=50)
        out["SMA_200"] = out.ta.sma(close=close, length=200)
        out["RSI_14"] = out.ta.rsi(close=close, length=14)
        return out

    def score_rsi(self, rsi_value: float) -> float:
        """Map an RSI value to a BUY conviction score.

        Linear mapping in the oversold zone relative to ``rsi_oversold``.
        """
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
        """Return trailing EPS for a ticker via yfinance (cached, tolerant).

        Args:
            ticker: Yahoo Finance ticker symbol.

        Returns:
            float | None: Trailing EPS, or ``None`` if it cannot be determined
            (network error, missing field). ``None`` means "unknown -> allow".
        """
        if yf is None:
            return None
        try:
            info = yf.Ticker(ticker).info or {}
            for key in ("trailingEps", "epsTrailingTwelveMonths"):
                val = info.get(key)
                if val is not None:
                    return float(val)
        except Exception:  # noqa: BLE001 - never block sizing on a data outage.
            logger.debug("EPS lookup failed for %s; treating as unknown.", ticker)
        return None

    def is_profitable(self, ticker: str) -> bool:
        """Quality filter: reject loss-making names (EPS < 0).

        Unknown EPS (data unavailable) is treated as pass, so a data outage
        never silently blocks the whole universe.

        Args:
            ticker: Ticker to check.

        Returns:
            bool: ``False`` only when EPS is known and negative.
        """
        eps = self._trailing_eps(ticker)
        if eps is None:
            return True
        return eps > 0

    def generate_raw_signals(
        self,
        db_manager: Any,
        tickers: List[str],
        apply_quality_filter: bool = True,
        apply_momentum_filter: bool = True,
    ) -> List[Signal]:
        """Evaluate each ticker and emit raw Mean-Reversion Exhaustion signals.

        Rule (BUY): the most recent bar has ``Close > SMA_200`` (long-term
        uptrend) AND ``RSI_14 < RSI_OVERSOLD_THRESHOLD`` (default 30), refined by:

          * Quality filter (Phase 11): the company must be profitable (EPS > 0).
          * Momentum filter (Phase 11): do not catch falling knives — require
            ``Close > SMA_5`` so the pullback is already stabilizing.

        Args:
            db_manager: A Phase 2 ``TimeSeriesDB`` exposing
                ``get_historical_prices(ticker, days)``.
            tickers: Ticker symbols to evaluate.
            apply_quality_filter: Skip loss-making companies when ``True``.
            apply_momentum_filter: Require ``Close > SMA_5`` when ``True``.

        Returns:
            List[Signal]: PENDING BUY signals for tickers meeting all rules.
        """
        signals: List[Signal] = []

        for ticker in tickers:
            df = db_manager.get_historical_prices(ticker, days=252)
            if df is None or df.empty or len(df) < _MIN_ROWS:
                logger.debug(
                    "Skipping %s: insufficient history (%d rows).",
                    ticker,
                    0 if df is None else len(df),
                )
                continue

            enriched = self.calculate_indicators(df)
            last = enriched.iloc[-1]

            close = last["Close"]
            sma_5 = last["SMA_5"]
            sma_200 = last["SMA_200"]
            rsi_14 = last["RSI_14"]

            if pd.isna(sma_200) or pd.isna(rsi_14):
                logger.debug("Skipping %s: indicators not yet warmed up.", ticker)
                continue

            uptrend = close > sma_200
            oversold = rsi_14 < self.rsi_oversold

            # --- Momentum filter: reject falling knives (Close <= SMA_5) ------
            if apply_momentum_filter and (pd.isna(sma_5) or close <= sma_5):
                if uptrend and oversold:
                    logger.info(
                        "Momentum filter blocked %s (Close %.2f <= SMA5 %.2f).",
                        ticker,
                        close,
                        sma_5,
                    )
                continue

            # --- Quality filter: reject loss-making hype stocks (EPS < 0) -----
            if uptrend and oversold and apply_quality_filter and not self.is_profitable(
                ticker
            ):
                logger.info("Quality filter blocked %s (EPS < 0).", ticker)
                continue

            if uptrend and oversold:
                score = self.score_rsi(rsi_14)
                signal = Signal(
                    id=str(uuid.uuid4()),
                    ticker=ticker,
                    signal_type=SignalType.BUY,
                    status=SignalStatus.PENDING,
                    score=score,
                    target_qty=None,
                    created_at=datetime.now(timezone.utc),
                    reason=(
                        f"RSI < {self.rsi_oversold:.0f} (Value: {rsi_14:.1f}) while Price > SMA200 "
                        f"({close:.2f} > {sma_200:.2f}). Mean-reversion setup."
                    ),
                )
                signals.append(signal)
                logger.info(
                    "BUY signal %s for %s (RSI=%.1f, score=%.1f).",
                    signal.id[:8],
                    ticker,
                    rsi_14,
                    score,
                )

        return signals


if __name__ == "__main__":
    import numpy as np

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Build a synthetic uptrend (Close > SMA200) that dips into an oversold
    # pullback (RSI_14 < 30) and then STABILISES (Close > SMA_5) so both the
    # mean-reversion rule and the new momentum filter fire together.
    n = 260
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    base = np.linspace(100.0, 200.0, n)          # long-term uptrend
    close = base.copy()
    close[-8:] = close[-9] * np.array(           # deep dip, then a 2-bar bounce
        [0.955, 0.925, 0.898, 0.875, 0.858, 0.848, 0.858, 0.866]
    )
    mock = pd.DataFrame(
        {
            "Ticker": "TEST.PA",
            "Date": dates,
            "Open": close,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": 1_000_000,
        }
    )

    class _MockDB:
        """Minimal stand-in for TimeSeriesDB returning the mock frame."""

        def get_historical_prices(self, ticker: str, days: int = 252) -> pd.DataFrame:
            return mock

    gen = SignalGenerator()

    enriched = gen.calculate_indicators(mock)
    last = enriched.iloc[-1]
    print(
        f"Last bar -> Close={last['Close']:.2f} SMA5={last['SMA_5']:.2f} "
        f"SMA200={last['SMA_200']:.2f} RSI14={last['RSI_14']:.2f}"
    )
    print("score_rsi checks:",
          gen.score_rsi(30), gen.score_rsi(20), gen.score_rsi(10),
          gen.score_rsi(35), gen.score_rsi(float("nan")))

    # Quality filter needs network EPS; disable it for this offline demo.
    results = gen.generate_raw_signals(
        _MockDB(), ["TEST.PA"], apply_quality_filter=False
    )
    print(f"\nGenerated {len(results)} signal(s):")
    for s in results:
        print(f"  {s.id[:8]} {s.ticker} {s.signal_type.value} "
              f"score={s.score:.1f} status={s.status.value}")
        print(f"  reason: {s.reason}")
```

## FILE: 03_risk_portfolio/__init__.py
```python

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
        self.vol_reference: float = float(risk.get("VOLATILITY_REFERENCE", 0.20))
        self.vol_max_factor: float = float(risk.get("VOLATILITY_MAX_FACTOR", 1.5))
        logger.debug(
            "Sizer loaded: kelly=%.2f max_single=%.2f sat_budget=%.2f vol_ref=%.2f",
            self.kelly_fraction,
            self.max_single_position,
            self.satellite_max_budget,
            self.vol_reference,
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

    def calculate_target_qty(
        self,
        signal: Signal,
        portfolio: PortfolioState,
        current_price: float,
        historical_volatility: float | None = None,
    ) -> int:
        """Compute the integer share quantity for a satellite signal.

        Steps:
            1. ``max_alloc = total_equity * MAX_SINGLE_POSITION_PCT``
            2. ``target_cash = max_alloc * (score / 100) * KELLY_FRACTION``
            3. Scale ``target_cash`` by the inverse-volatility parity factor.
            4. Cap ``target_cash`` so total satellite exposure stays within
               ``SATELLITE_MAX_BUDGET_PCT`` of equity.
            5. ``qty = floor(target_cash / current_price)`` (no fractions).
            6. Clamp to available cash if the notional would exceed it.

        Args:
            signal: The signal being sized (score drives the allocation).
            portfolio: Current portfolio snapshot (equity + cash).
            current_price: Latest price per share in EUR.
            historical_volatility: Annualized stdev of the asset's returns used
                for volatility parity. ``None`` disables vol scaling.

        Returns:
            int: Whole number of shares to buy (0 if nothing is affordable).
        """
        if current_price <= 0:
            logger.warning("Non-positive price for %s; sizing to 0.", signal.ticker)
            return 0
        if portfolio.total_equity <= 0:
            logger.warning("Zero equity; sizing %s to 0.", signal.ticker)
            return 0

        max_alloc = portfolio.total_equity * self.max_single_position
        target_cash = max_alloc * (signal.score / 100.0) * self.kelly_fraction

        # --- Volatility parity: calmer names get more, wild names get less ----
        vol_factor = self._volatility_factor(historical_volatility)
        target_cash *= vol_factor

        # --- Enforce the 30% satellite budget across ALL non-core holdings ----
        satellite_room = max(
            0.0,
            self.satellite_max_budget * portfolio.total_equity
            - self._satellite_value(portfolio),
        )
        if target_cash > satellite_room:
            logger.info(
                "%s sizing capped by satellite budget: %.2f -> %.2f EUR room.",
                signal.ticker,
                target_cash,
                satellite_room,
            )
            target_cash = satellite_room

        qty_shares = math.floor(target_cash / current_price)

        notional = qty_shares * current_price
        if notional > portfolio.cash_available:
            qty_shares = math.floor(portfolio.cash_available / current_price)
            logger.info(
                "%s sizing capped by cash: target %.2f EUR > cash %.2f EUR -> %d shares.",
                signal.ticker,
                notional,
                portfolio.cash_available,
                qty_shares,
            )
        else:
            logger.info(
                "%s sized to %d shares (target_cash=%.2f EUR @ %.2f, score=%.1f, "
                "vol_factor=%.2f).",
                signal.ticker,
                qty_shares,
                target_cash,
                current_price,
                signal.score,
                vol_factor,
            )

        return max(0, qty_shares)


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

## FILE: 04_orchestrator_ai/__init__.py
```python

```

## FILE: 04_orchestrator_ai/earnings_blackout.py
```python
"""Per-ticker earnings / dividend blackout (same pattern as MacroVetoEngine).

Blocks new satellite buys when a corporate event for that ticker falls within
``EARNINGS_BLACKOUT_DAYS``. Calendar is maintained in
``config/earnings_calendar.yaml`` (manual seed; later auto-synced from an API).
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Dict, Tuple

import yaml

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_DIR = _PROJECT_ROOT / "config"


class EarningsBlackoutEngine:
    """Vetoes buys near ticker-specific earnings/dividend dates."""

    def __init__(self, config_dir: str | Path | None = None) -> None:
        config_path = Path(config_dir) if config_dir else _DEFAULT_CONFIG_DIR
        risk = self._load_yaml(config_path / "risk_params.yaml")
        cal_raw = self._load_yaml(config_path / "earnings_calendar.yaml")
        self.blackout_days: int = int(risk.get("EARNINGS_BLACKOUT_DAYS", 2))
        # ticker -> {date -> event_name}
        self.calendar: Dict[str, Dict[dt.date, str]] = self._parse_calendar(cal_raw)
        logger.debug(
            "EarningsBlackoutEngine: window=%d day(s), %d ticker(s).",
            self.blackout_days,
            len(self.calendar),
        )

    @staticmethod
    def _load_yaml(path: Path) -> dict:
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    @staticmethod
    def _parse_calendar(raw: dict) -> Dict[str, Dict[dt.date, str]]:
        """Accept ``events: { TICKER: { YYYY-MM-DD: name } }``."""
        events = raw.get("events", raw) if isinstance(raw, dict) else {}
        parsed: Dict[str, Dict[dt.date, str]] = {}
        if not isinstance(events, dict):
            return parsed
        for ticker, dates in events.items():
            if not isinstance(dates, dict):
                continue
            bucket: Dict[dt.date, str] = {}
            for key, name in dates.items():
                if isinstance(key, dt.datetime):
                    event_date = key.date()
                elif isinstance(key, dt.date):
                    event_date = key
                else:
                    try:
                        event_date = dt.date.fromisoformat(str(key))
                    except ValueError:
                        continue
                bucket[event_date] = str(name)
            if bucket:
                parsed[str(ticker)] = bucket
        return parsed

    def check_veto(
        self, ticker: str, target_date: dt.date
    ) -> Tuple[bool, str]:
        """Return ``(True, reason)`` if ``ticker`` is in an earnings blackout."""
        if isinstance(target_date, dt.datetime):
            target_date = target_date.date()
        events = self.calendar.get(ticker) or {}
        for event_date, name in sorted(events.items()):
            delta = (event_date - target_date).days
            if 0 <= delta <= self.blackout_days:
                if delta == 0:
                    reason = f"EARNINGS BLACKOUT: {name} today ({ticker})"
                elif delta == 1:
                    reason = f"EARNINGS BLACKOUT: {name} in 1 day ({ticker})"
                else:
                    reason = (
                        f"EARNINGS BLACKOUT: {name} in {delta} days ({ticker})"
                    )
                logger.info("%s", reason)
                return True, reason
        return False, "Clear"
```

## FILE: 04_orchestrator_ai/macro_veto.py
```python
"""Macro Veto Engine for PEA Sniper Terminal V-Prime.

Blocks new offensive signals when a high-impact macro event (ECB/FED decision,
CPI, NFP) falls within a configurable window. Running this cheap check before
the heavy correlation math keeps the cascade CPU-efficient.

Pure logical routing: no LLMs, no APIs. All paths use ``pathlib`` for
cross-platform compatibility (Windows x64/ARM and Linux).
"""

import datetime as dt
import logging
from pathlib import Path
from typing import Dict, Tuple

import yaml

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_DIR = _PROJECT_ROOT / "config"


class MacroVetoEngine:
    """Vetoes new trades near scheduled high-impact macro events.

    Attributes:
        veto_days_before: Number of days before an event during which new
            trades are blocked.
        calendar: Mapping of event date -> event name.
    """

    def __init__(self, config_dir: str | Path | None = None) -> None:
        """Load the veto window and the macro calendar.

        Args:
            config_dir: Path to the ``config`` directory. Defaults to
                ``<project_root>/config``.
        """
        config_path = Path(config_dir) if config_dir else _DEFAULT_CONFIG_DIR

        risk = self._load_yaml(config_path / "risk_params.yaml")
        calendar_raw = self._load_yaml(config_path / "macro_calendar.yaml")

        self.veto_days_before: int = int(risk["MACRO_VETO_DAYS_BEFORE"])
        self.calendar: Dict[dt.date, str] = self._parse_calendar(calendar_raw)

        logger.debug(
            "MacroVetoEngine loaded: window=%d day(s), %d event(s).",
            self.veto_days_before,
            len(self.calendar),
        )

    @staticmethod
    def _load_yaml(path: Path) -> dict:
        """Load a YAML file into a dict, raising a clear error if missing."""
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    @staticmethod
    def _parse_calendar(raw: dict) -> Dict[dt.date, str]:
        """Normalize raw YAML into a ``date -> name`` mapping.

        Accepts either a top-level ``events:`` mapping or a bare ``date: name``
        mapping. Date keys may be ``datetime.date`` (parsed by PyYAML) or ISO
        strings.
        """
        events = raw.get("events", raw) if isinstance(raw, dict) else {}
        parsed: Dict[dt.date, str] = {}
        for key, name in events.items():
            if isinstance(key, dt.datetime):
                event_date = key.date()
            elif isinstance(key, dt.date):
                event_date = key
            else:
                event_date = dt.date.fromisoformat(str(key))
            parsed[event_date] = str(name)
        return parsed

    def check_veto(self, target_date: dt.date) -> Tuple[bool, str]:
        """Check whether a trade on ``target_date`` must be vetoed.

        A veto applies when an event is scheduled on ``target_date`` or within
        the next ``veto_days_before`` days.

        Args:
            target_date: The date the trade would be placed.

        Returns:
            tuple[bool, str]: ``(True, reason)`` if vetoed, else
            ``(False, "Clear")``.
        """
        if isinstance(target_date, dt.datetime):
            target_date = target_date.date()

        for event_date, name in sorted(self.calendar.items()):
            delta = (event_date - target_date).days
            if 0 <= delta <= self.veto_days_before:
                if delta == 0:
                    reason = f"VETO: {name} today"
                elif delta == 1:
                    reason = f"VETO: {name} in 1 day"
                else:
                    reason = f"VETO: {name} in {delta} days"
                logger.info("Macro veto for %s -> %s", target_date, reason)
                return True, reason

        return False, "Clear"


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    engine = MacroVetoEngine()
    print("Window (days before):", engine.veto_days_before)
    print("Events loaded:", len(engine.calendar))

    # ECB Rate Decision seeded on 2026-07-16.
    for d in ("2026-07-14", "2026-07-15", "2026-07-16", "2026-07-25"):
        vetoed, msg = engine.check_veto(dt.date.fromisoformat(d))
        print(f"{d}: vetoed={vetoed} -> {msg}")
```

## FILE: 04_orchestrator_ai/news_sentiment_llm.py
```python
"""News sentiment scorer for PEA Sniper Terminal V-Prime (Phase 11).

Turns unstructured news headlines into a single hard number the deterministic
engine can use. The LLM is constrained to act as a quantitative NLP model and
MUST return only an integer in ``[-100, +100]`` — no prose, no explanation.

This keeps the pipeline emotionless: the model never decides trades, it only
compresses text into a scalar sentiment feature.
"""

import logging
import os
import re
import sys
from pathlib import Path
from typing import List

try:  # Load config/api_keys.env if python-dotenv is available.
    from dotenv import load_dotenv

    _ENV_PATH = Path(__file__).resolve().parent.parent / "config" / "api_keys.env"
    load_dotenv(_ENV_PATH)
except Exception:  # noqa: BLE001 - dotenv is a convenience, not a requirement.
    pass

# Reuse the shared OpenRouter client from the interfaces layer.
_INTERFACES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "05_interfaces"
)
sys.path.insert(0, _INTERFACES_DIR)

from llm_explainer import openrouter_chat  # noqa: E402

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "mistralai/mistral-7b-instruct"
_NEUTRAL_SCORE = 0.0
# Extract the first signed integer from the model reply.
_INT_RE = re.compile(r"-?\d+")


class NewsSentimentScorer:
    """Compresses news headlines into a numeric sentiment score."""

    def __init__(self) -> None:
        """Read the OpenRouter API key and model slug from the environment."""
        self.api_key: str | None = os.getenv("OPENROUTER_API_KEY")
        self.model: str = os.getenv("OPENROUTER_MODEL", _DEFAULT_MODEL)
        if not self.api_key:
            logger.warning(
                "OPENROUTER_API_KEY not set; news sentiment will be neutral (0)."
            )

    @staticmethod
    def _parse_score(raw: str | None) -> float:
        """Parse the LLM reply into a float clamped to [-100, 100]."""
        if not raw:
            return _NEUTRAL_SCORE
        match = _INT_RE.search(raw)
        if not match:
            logger.warning("No integer in sentiment reply %r; neutral.", raw[:80])
            return _NEUTRAL_SCORE
        value = float(int(match.group()))
        return max(-100.0, min(100.0, value))

    async def analyze_news(
        self, ticker: str, news_headlines: List[str]
    ) -> float:
        """Score the aggregate sentiment of headlines for one ticker.

        Args:
            ticker: The ticker the headlines relate to (for prompt context).
            news_headlines: Recent headline strings.

        Returns:
            float: Sentiment in ``[-100.0, +100.0]`` (negative = bearish,
            positive = bullish). Returns ``0.0`` (neutral) if there is no data
            or the API is unavailable.
        """
        headlines = [h.strip() for h in (news_headlines or []) if h and h.strip()]
        if not headlines:
            logger.debug("No headlines for %s; neutral sentiment.", ticker)
            return _NEUTRAL_SCORE
        if not self.api_key:
            return _NEUTRAL_SCORE

        joined = "\n".join(f"- {h}" for h in headlines[:15])
        system_prompt = (
            "You are a deterministic quantitative NLP sentiment model. You read "
            "financial news headlines and output market sentiment as a single "
            "integer between -100 (extremely bearish) and +100 (extremely "
            "bullish), where 0 is neutral. Output ONLY the integer. No words, no "
            "symbols, no explanation, no punctuation."
        )
        user_prompt = (
            f"Ticker: {ticker}\nHeadlines:\n{joined}\n\n"
            "Return ONLY one integer between -100 and 100."
        )

        raw = await openrouter_chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            api_key=self.api_key,
            model=self.model,
            max_tokens=8,
            temperature=0.0,
        )
        score = self._parse_score(raw)
        logger.info("News sentiment for %s: %.0f (from %d headlines).",
                    ticker, score, len(headlines))
        return score


if __name__ == "__main__":
    import asyncio

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    scorer = NewsSentimentScorer()

    # Offline unit check of the parser (no network needed).
    assert scorer._parse_score("42") == 42.0
    assert scorer._parse_score("Score: -73 (bearish)") == -73.0
    assert scorer._parse_score("999") == 100.0
    assert scorer._parse_score("nonsense") == 0.0
    print("Parser checks passed.")

    demo = [
        "Company X beats earnings, raises full-year guidance",
        "Analysts upgrade Company X to Buy on strong order book",
    ]
    result = asyncio.run(scorer.analyze_news("TEST.PA", demo))
    print("Live sentiment (0 if no API key):", result)
```

## FILE: 04_orchestrator_ai/revocation_engine.py
```python
"""Revocation Engine for PEA Sniper Terminal V-Prime.

Implements the Anti-Stale logic re-run at each daily pass (09:00, 13:30, 17:10):
a signal is REVOKED if the price drifts too far from the emission price, or
EXPIRED once it outlives its validity window.

Pure logical routing: no LLMs, no APIs. All paths use ``pathlib``.
"""

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

_CORE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "01_memory_core"
)
sys.path.insert(0, _CORE_DIR)

from data_models import Signal, SignalStatus  # noqa: E402

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_DIR = _PROJECT_ROOT / "config"
_PRICE_DRIFT_LIMIT = 0.03  # 3% intraday drift revokes a signal.


class RevocationEngine:
    """Revokes or expires signals that are no longer actionable.

    Attributes:
        validity_hours: Number of hours a signal remains valid after emission.
    """

    def __init__(self, config_dir: str | Path | None = None) -> None:
        """Load the signal validity window from ``risk_params.yaml``.

        Args:
            config_dir: Path to the ``config`` directory. Defaults to
                ``<project_root>/config``.
        """
        config_path = Path(config_dir) if config_dir else _DEFAULT_CONFIG_DIR
        risk = self._load_yaml(config_path / "risk_params.yaml")
        self.validity_hours: float = float(risk["SIGNAL_VALIDITY_HOURS"])
        logger.debug("RevocationEngine loaded: validity=%.1fh", self.validity_hours)

    @staticmethod
    def _load_yaml(path: Path) -> dict:
        """Load a YAML file into a dict, raising a clear error if missing."""
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    def evaluate_signal(
        self, signal: Signal, current_price: float, original_price: float
    ) -> Signal:
        """Re-evaluate a signal for price drift and time decay.

        Args:
            signal: The signal to evaluate (mutated in place and returned).
            current_price: Latest market price for the ticker.
            original_price: Price at the moment the signal was emitted.

        Returns:
            Signal: The same signal object, with updated ``status``/``reason``.
        """
        # Rule 1 - Price drift (revocation takes precedence over expiry).
        if original_price and original_price > 0:
            drift = abs(current_price - original_price) / original_price
            if drift > _PRICE_DRIFT_LIMIT:
                signal.status = SignalStatus.REVOKED
                signal.reason = f"{signal.reason} | REVOKED: Price drifted > 3%".strip(" |")
                logger.info(
                    "Signal %s REVOKED: %s drifted %.2f%% (%.2f -> %.2f).",
                    signal.id[:8],
                    signal.ticker,
                    drift * 100,
                    original_price,
                    current_price,
                )
                return signal

        # Rule 2 - Time decay.
        now = datetime.now(timezone.utc)
        created = signal.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_hours = (now - created).total_seconds() / 3600.0
        if age_hours > self.validity_hours:
            signal.status = SignalStatus.EXPIRED
            signal.reason = f"{signal.reason} | EXPIRED: Older than validity window".strip(" |")
            logger.info(
                "Signal %s EXPIRED: age %.1fh > %.1fh.",
                signal.id[:8],
                age_hours,
                self.validity_hours,
            )
            return signal

        logger.debug("Signal %s still valid (age %.1fh).", signal.id[:8], age_hours)
        return signal


if __name__ == "__main__":
    from datetime import timedelta

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    engine = RevocationEngine()

    print("--- Rule 1: price drift ---")
    s1 = Signal(ticker="MC.PA", signal_type="BUY", score=80.0,
                reason="Mean-reversion setup")
    s1 = engine.evaluate_signal(s1, current_price=94.0, original_price=100.0)
    print(f"status={s1.status.value} | reason='{s1.reason}'")

    print("\n--- Rule 2: time decay ---")
    s2 = Signal(ticker="AI.PA", signal_type="BUY", score=90.0,
                reason="Mean-reversion setup")
    s2.created_at = datetime.now(timezone.utc) - timedelta(hours=13)
    s2 = engine.evaluate_signal(s2, current_price=100.5, original_price=100.0)
    print(f"status={s2.status.value} | reason='{s2.reason}'")

    print("\n--- Still valid ---")
    s3 = Signal(ticker="OR.PA", signal_type="BUY", score=75.0,
                reason="Mean-reversion setup")
    s3 = engine.evaluate_signal(s3, current_price=100.5, original_price=100.0)
    print(f"status={s3.status.value} | reason='{s3.reason}'")
```

## FILE: 04_orchestrator_ai/signal_priority_cascade.py
```python
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
            target_qty = self.sizer.calculate_target_qty(
                signal, portfolio, price, historical_volatility=hist_vol
            )
            if target_qty <= 0:
                processed.append(
                    self._reject(signal, "REJECTED: Insufficient cash for 1 share")
                )
                continue

            signal.target_qty = target_qty
            signal.status = SignalStatus.APPROVED
            signal.reason = (
                f"{signal.reason} | APPROVED: {target_qty} share(s) "
                f"@ {price:.2f} EUR"
            ).strip(" |")
            logger.info(
                "APPROVED %s: %d share(s) @ %.2f EUR (score=%.1f).",
                ticker,
                target_qty,
                price,
                signal.score,
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
```

## FILE: 04_orchestrator_ai/weekly_historian.py
```python
"""Weekly Historian for PEA Sniper Terminal V-Prime (Phase 12).

Every Friday the system "steps back" and writes a hedge-fund-style weekly digest
for the CIO. It aggregates the last 7 days of audit logs into hard counts
(vetoes, executions, current equity/cash) and asks the LLM to translate those
numbers into a concise, professional risk-and-performance narrative.

The LLM is a *post-hoc analyst only*: it summarizes decisions the deterministic
engine already made. It never generates or approves trades.
"""

import logging
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:  # Load config/api_keys.env if python-dotenv is available.
    from dotenv import load_dotenv

    _ENV_PATH = Path(__file__).resolve().parent.parent / "config" / "api_keys.env"
    load_dotenv(_ENV_PATH)
except Exception:  # noqa: BLE001
    pass

_INTERFACES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "05_interfaces"
)
sys.path.insert(0, _INTERFACES_DIR)

from llm_explainer import openrouter_chat  # noqa: E402

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "mistralai/mistral-7b-instruct"
_LOOKBACK_DAYS = 7
_FALLBACK_PREFIX = "[AI narrative unavailable] "


class WeeklyHistorian:
    """Builds and narrates the weekly risk/performance digest."""

    def __init__(self) -> None:
        """Read the OpenRouter API key and model slug from the environment."""
        self.api_key: str | None = os.getenv("OPENROUTER_API_KEY")
        self.model: str = os.getenv("OPENROUTER_MODEL", _DEFAULT_MODEL)
        if not self.api_key:
            logger.warning(
                "OPENROUTER_API_KEY not set; weekly report will use a data-only "
                "fallback (no AI narrative)."
            )

    @staticmethod
    def _classify(row: dict) -> str:
        """Bucket an audit row into a coarse decision category."""
        status = (row.get("status") or "").upper()
        reason = (row.get("reason") or "").lower()
        if status in ("EXECUTED", "APPROVED"):
            return "executed"
        if status == "REVOKED":
            return "revoked"
        if status == "REJECTED":
            if "vix" in reason or "panic" in reason:
                return "vetoed_vix"
            if "earnings" in reason or "blackout" in reason:
                return "vetoed_earnings"
            if "illiquid" in reason or "adv" in reason:
                return "vetoed_liquidity"
            if "max satellite" in reason or "max positions" in reason:
                return "vetoed_max_positions"
            if "macro" in reason or ("veto" in reason and "earnings" not in reason):
                return "vetoed_macro"
            if "sector" in reason:
                return "vetoed_sector"
            if "correlation" in reason:
                return "vetoed_correlation"
            return "rejected_other"
        return "other"

    def _build_context(self, rows: list[dict], portfolio: Any) -> tuple[str, dict]:
        """Summarize audit rows + portfolio into an LLM context string.

        Returns:
            tuple[str, dict]: The context block and the raw counts dict (so the
            fallback path can render numbers without the LLM).
        """
        buckets = Counter(self._classify(r) for r in rows)
        executed = [r for r in rows if self._classify(r) == "executed"]

        positions_txt = ", ".join(
            f"{p.ticker} {p.qty_shares}@{p.current_price:.2f} "
            f"({p.unrealized_pnl_pct * 100:+.1f}%)"
            for p in portfolio.positions
        ) or "none"

        top_trades = "; ".join(
            f"{r['ticker']} ({r['status']})" for r in executed[:8]
        ) or "none"

        counts = dict(buckets)
        context = (
            f"REPORTING WINDOW: last {_LOOKBACK_DAYS} days.\n"
            f"Total signals evaluated: {len(rows)}.\n"
            f"Executed/Approved: {buckets.get('executed', 0)}.\n"
            f"Revoked (macro window): {buckets.get('revoked', 0)}.\n"
            f"Vetoed by MACRO event: {buckets.get('vetoed_macro', 0)}.\n"
            f"Vetoed by EARNINGS blackout: {buckets.get('vetoed_earnings', 0)}.\n"
            f"Vetoed by VIX panic: {buckets.get('vetoed_vix', 0)}.\n"
            f"Vetoed by LIQUIDITY: {buckets.get('vetoed_liquidity', 0)}.\n"
            f"Vetoed by MAX POSITIONS: {buckets.get('vetoed_max_positions', 0)}.\n"
            f"Vetoed by SECTOR limit: {buckets.get('vetoed_sector', 0)}.\n"
            f"Vetoed by CORRELATION: {buckets.get('vetoed_correlation', 0)}.\n"
            f"Other rejections: {buckets.get('rejected_other', 0)}.\n"
            f"Executed names: {top_trades}.\n"
            f"CURRENT EQUITY: {portfolio.total_equity:,.2f} EUR.\n"
            f"CASH AVAILABLE: {portfolio.cash_available:,.2f} EUR "
            f"({(portfolio.cash_available / portfolio.total_equity * 100) if portfolio.total_equity else 0:.1f}%).\n"
            f"OPEN POSITIONS: {positions_txt}.\n"
        )
        return context, counts

    @staticmethod
    def _fallback_report(context: str) -> str:
        """Return a numbers-only report when the LLM is unavailable."""
        return (
            f"{_FALLBACK_PREFIX}Weekly Risk & Performance Digest\n\n{context}"
        )

    async def generate_weekly_report(
        self, portfolio_db: Any, explainer: Any = None
    ) -> str:
        """Generate the weekly CIO digest.

        Args:
            portfolio_db: A ``PortfolioDB`` exposing ``fetch_signals_since`` and
                ``get_portfolio_state``.
            explainer: Optional ``NarrativeExplainer`` (unused directly; kept for
                interface compatibility — the shared OpenRouter client is used).

        Returns:
            str: The generated report, or a data-only fallback on any failure.
        """
        since = (datetime.now(timezone.utc) - timedelta(days=_LOOKBACK_DAYS)).isoformat()
        try:
            rows = portfolio_db.fetch_signals_since(since)
        except Exception:  # noqa: BLE001
            logger.exception("Could not read audit logs for weekly report.")
            rows = []

        portfolio = portfolio_db.get_portfolio_state()
        context, _counts = self._build_context(rows, portfolio)

        if not self.api_key:
            return self._fallback_report(context)

        system_prompt = (
            "Act as a Hedge Fund Risk Manager. Write a weekly digest for the "
            "CIO. Explain how risk was managed (vetoes), summarize performance, "
            "and give a 2-sentence macro outlook. Tone: professional, empirical, "
            "numbers-driven. Keep it under 220 words. No disclaimers."
        )
        narrative = await openrouter_chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context},
            ],
            api_key=self.api_key,
            model=self.model,
            max_tokens=420,
            temperature=0.5,
        )
        if not narrative:
            return self._fallback_report(context)

        logger.info("Weekly report generated (%d chars).", len(narrative))
        return narrative


if __name__ == "__main__":
    import asyncio
    from datetime import datetime, timezone

    _CORE_DIR = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "01_memory_core"
    )
    sys.path.insert(0, _CORE_DIR)
    from data_models import PortfolioState, Position  # noqa: E402

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    class _MockDB:
        def fetch_signals_since(self, since_iso: str) -> list[dict]:
            now = datetime.now(timezone.utc).isoformat()
            return [
                {"ticker": "MC.PA", "status": "EXECUTED", "reason": "approved", "created_at": now},
                {"ticker": "OR.PA", "status": "REJECTED", "reason": "Macro veto: ECB", "created_at": now},
                {"ticker": "AI.PA", "status": "REJECTED", "reason": "VIX panic", "created_at": now},
                {"ticker": "SU.PA", "status": "REJECTED", "reason": "Sector weight limit", "created_at": now},
            ]

        def get_portfolio_state(self) -> PortfolioState:
            return PortfolioState(
                cash_available=6000.0,
                total_equity=20000.0,
                positions=[
                    Position(ticker="MC.PA", qty_shares=5, avg_entry_price=600.0,
                             current_price=660.0, sector="Luxury"),
                ],
                last_updated=datetime.now(timezone.utc),
            )

    hist = WeeklyHistorian()
    report = asyncio.run(hist.generate_weekly_report(_MockDB()))
    print("\n===== WEEKLY REPORT =====\n")
    print(report)
```

## FILE: 05_interfaces/__init__.py
```python

```

## FILE: 05_interfaces/discord_copilot.py
```python
"""Discord Copilot for PEA Sniper Terminal V-Prime.

Pushes interactive trade alerts to Discord and waits for the human to approve
or reject. Execution is manual: approving records the trade in SQLite (status
EXECUTED, cash deducted, position added) - it never sends an order to a broker.

STRICT: the LLM only writes the explanation text (Phase 7.1). Buttons and DB
logic here are deterministic.

.env requirements (config/api_keys.env):
    DISCORD_TOKEN        - the bot token.
    DISCORD_CHANNEL_ID   - numeric channel ID for alerts.
    OPENROUTER_API_KEY   - used by NarrativeExplainer (optional; has fallback).
"""

import logging
import os
import sys
from pathlib import Path

import discord

try:
    from dotenv import load_dotenv

    _ENV_PATH = Path(__file__).resolve().parent.parent / "config" / "api_keys.env"
    load_dotenv(_ENV_PATH)
except Exception:  # noqa: BLE001
    pass

_INTERFACES_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.join(os.path.dirname(_INTERFACES_DIR), "01_memory_core")
sys.path.insert(0, _INTERFACES_DIR)
sys.path.insert(0, _CORE_DIR)

from data_models import PortfolioState, Position, Signal, SignalStatus, SignalType  # noqa: E402
from llm_explainer import NarrativeExplainer  # noqa: E402

logger = logging.getLogger(__name__)

_GREEN = discord.Color.from_str("#00E676")
_RED = discord.Color.from_str("#FF3B30")


class TradeActionView(discord.ui.View):
    """Interactive Approve/Reject buttons attached to a trade alert.

    Approving persists the trade to SQLite via the provided ``PortfolioDB``.
    Both callbacks immediately edit the message so Discord never shows a stuck
    "thinking" state.
    """

    def __init__(
        self,
        signal: Signal,
        portfolio_db,
        current_price: float,
        timeout: float | None = 3600,
    ) -> None:
        """Initialize the view.

        Args:
            signal: The approved signal this alert represents.
            portfolio_db: A ``PortfolioDB`` used to persist an execution.
            current_price: Price per share used to compute the cash outlay.
            timeout: Seconds before the buttons auto-disable (default 1h).
        """
        super().__init__(timeout=timeout)
        self.signal = signal
        self.portfolio_db = portfolio_db
        self.current_price = current_price

    def _disable_all(self) -> None:
        """Disable every child button (post-decision)."""
        for child in self.children:
            child.disabled = True

    def _execute_in_db(self) -> float:
        """Persist the executed trade to SQLite and return the cash spent.

        Deducts the notional from cash, adds/merges the position, refreshes
        equity, and logs the signal as EXECUTED.

        Returns:
            float: The cash amount spent on the trade.
        """
        qty = self.signal.target_qty or 0
        cost = qty * self.current_price

        state = self.portfolio_db.get_portfolio_state()
        state.cash_available = max(0.0, state.cash_available - cost)

        # Merge into an existing position (weighted avg) or append a new one.
        existing = next(
            (p for p in state.positions if p.ticker == self.signal.ticker), None
        )
        if existing is not None:
            total_qty = existing.qty_shares + qty
            if total_qty > 0:
                existing.avg_entry_price = (
                    existing.avg_entry_price * existing.qty_shares
                    + self.current_price * qty
                ) / total_qty
            existing.qty_shares = total_qty
            existing.current_price = self.current_price
        else:
            state.positions.append(
                Position(
                    ticker=self.signal.ticker,
                    qty_shares=qty,
                    avg_entry_price=self.current_price,
                    current_price=self.current_price,
                    sector=self._infer_sector(),
                )
            )

        state.total_equity = state.cash_available + sum(
            p.market_value for p in state.positions
        )
        self.portfolio_db.update_portfolio(state)

        self.signal.status = SignalStatus.EXECUTED
        self.portfolio_db.log_signal(self.signal)
        return cost

    def _infer_sector(self) -> str:
        """Best-effort sector lookup from the universe file (falls back)."""
        try:
            import yaml

            universe_path = (
                Path(__file__).resolve().parent.parent / "config" / "pea_universe.yaml"
            )
            with open(universe_path, "r", encoding="utf-8") as fh:
                universe = yaml.safe_load(fh) or {}
            for sector, members in universe.get("universe", {}).items():
                for entry in members:
                    if entry["ticker"] == self.signal.ticker:
                        return sector
        except Exception:  # noqa: BLE001
            pass
        return "UNKNOWN"

    @discord.ui.button(label="Approuver le Trade", style=discord.ButtonStyle.success,
                       emoji="\U0001F7E2")
    async def approve(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        """Persist the execution and update the message."""
        try:
            cost = self._execute_in_db()
            self._disable_all()
            embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
            embed.color = _GREEN
            embed.title = f"\u2705 TRADE EXECUTED : {self.signal.ticker}"
            embed.add_field(
                name="Execution",
                value=(
                    f"{self.signal.target_qty} action(s) @ {self.current_price:.2f} EUR "
                    f"(co\u00fbt {cost:.2f} EUR)"
                ),
                inline=False,
            )
            await interaction.response.edit_message(embed=embed, view=self)
            logger.info("Trade EXECUTED for %s by %s.", self.signal.ticker, interaction.user)
        except Exception:  # noqa: BLE001 - always answer the interaction.
            logger.exception("Approve callback failed for %s.", self.signal.ticker)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "\u26a0\ufe0f Erreur lors de l'ex\u00e9cution en base.", ephemeral=True
                )
        finally:
            self.stop()

    @discord.ui.button(label="Rejeter", style=discord.ButtonStyle.danger,
                       emoji="\U0001F534")
    async def reject(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        """Mark the alert rejected by the user and update the message."""
        try:
            self.signal.status = SignalStatus.REJECTED
            if self.portfolio_db is not None:
                self.portfolio_db.log_signal(self.signal)
            self._disable_all()
            embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
            embed.color = _RED
            embed.title = f"\u274c TRADE REJECTED BY USER : {self.signal.ticker}"
            await interaction.response.edit_message(embed=embed, view=self)
            logger.info("Trade REJECTED for %s by %s.", self.signal.ticker, interaction.user)
        except Exception:  # noqa: BLE001
            logger.exception("Reject callback failed for %s.", self.signal.ticker)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "\u26a0\ufe0f Erreur.", ephemeral=True
                )
        finally:
            self.stop()


class DiscordCopilot(discord.Client):
    """Discord client that posts trade alerts and handles approvals."""

    def __init__(self, portfolio_db=None, explainer: NarrativeExplainer | None = None) -> None:
        """Initialize the client with a portfolio DB and an LLM explainer.

        Args:
            portfolio_db: A ``PortfolioDB`` for persisting executions.
            explainer: A ``NarrativeExplainer`` (created if not provided).
        """
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.portfolio_db = portfolio_db
        self.explainer = explainer or NarrativeExplainer()
        self.channel_id = int(os.getenv("DISCORD_CHANNEL_ID", "0"))

    async def on_ready(self) -> None:
        """Log a confirmation once the bot has connected."""
        logger.info("Discord Copilot connected as %s (channel_id=%s).",
                    self.user, self.channel_id)

    def build_embed(self, signal: Signal, explanation: str) -> discord.Embed:
        """Build the alert embed for a signal.

        Args:
            signal: The approved signal.
            explanation: The LLM-generated rationale.

        Returns:
            discord.Embed: The formatted alert embed.
        """
        is_buy = signal.signal_type == SignalType.BUY
        embed = discord.Embed(
            title=f"\U0001F6A8 PEA OPPORTUNIT\u00c9 : {signal.signal_type.name} {signal.ticker}",
            color=_GREEN if is_buy else _RED,
        )
        embed.add_field(name="Quantit\u00e9", value=f"{signal.target_qty} actions", inline=True)
        embed.add_field(name="Score Technique", value=f"{signal.score:.1f}/100", inline=True)
        embed.add_field(name="Analyse IA", value=explanation, inline=False)
        return embed

    async def send_signal_alert(
        self,
        signal: Signal,
        portfolio: PortfolioState,
        explainer: NarrativeExplainer | None = None,
        current_price: float = 0.0,
    ) -> discord.Message | None:
        """Generate an explanation and post an interactive alert.

        Args:
            signal: The approved, sized signal.
            portfolio: Current portfolio snapshot (for LLM context).
            explainer: Optional explainer override (defaults to ``self.explainer``).
            current_price: Price per share used for execution accounting.

        Returns:
            discord.Message | None: The sent message, or ``None`` if the channel
            could not be resolved.
        """
        explainer = explainer or self.explainer
        explanation = await explainer.explain_trade(signal, portfolio)

        embed = self.build_embed(signal, explanation)
        view = TradeActionView(signal, self.portfolio_db, current_price)

        channel = self.get_channel(self.channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(self.channel_id)
            except Exception:  # noqa: BLE001
                logger.error("Could not resolve channel %s.", self.channel_id)
                return None

        message = await channel.send(embed=embed, view=view)
        logger.info("Alert sent for %s to channel %s.", signal.ticker, self.channel_id)
        return message
```

## FILE: 05_interfaces/llm_explainer.py
```python
"""LLM narrative explainer for PEA Sniper Terminal V-Prime.

Wraps OpenRouter (async, via ``aiohttp``) to turn an already-approved,
already-sized ``Signal`` into a short, human-readable rationale for Discord.

STRICT: the LLM has ZERO decision power. It only produces the ``explanation``
string. It never reads or writes ``status``, ``target_qty`` or any math.

.env requirements (config/api_keys.env):
    OPENROUTER_API_KEY   - required; without it the fallback string is used.
    OPENROUTER_MODEL     - optional; defaults to mistralai/mistral-7b-instruct.
"""

import logging
import os
import sys
from pathlib import Path

import aiohttp

try:  # Load config/api_keys.env if python-dotenv is available.
    from dotenv import load_dotenv

    _ENV_PATH = Path(__file__).resolve().parent.parent / "config" / "api_keys.env"
    load_dotenv(_ENV_PATH)
except Exception:  # noqa: BLE001 - dotenv is a convenience, not a requirement.
    pass

_CORE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "01_memory_core"
)
sys.path.insert(0, _CORE_DIR)

from data_models import PortfolioState, Signal  # noqa: E402

logger = logging.getLogger(__name__)

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_DEFAULT_MODEL = "mistralai/mistral-7b-instruct"
_FALLBACK = "Technical signal approved. (AI explanation unavailable)"
_REQUEST_TIMEOUT_S = 20


async def openrouter_chat(
    messages: list[dict],
    api_key: str | None,
    model: str = _DEFAULT_MODEL,
    max_tokens: int = 180,
    temperature: float = 0.4,
    timeout_s: int = _REQUEST_TIMEOUT_S,
) -> str | None:
    """Send a chat-completion request to OpenRouter and return the text.

    Shared by every LLM consumer (trade explainer, news sentiment scorer, weekly
    historian) so the HTTP/auth/error handling lives in exactly one place.

    Args:
        messages: OpenAI-style ``[{"role", "content"}, ...]`` message list.
        api_key: OpenRouter API key; ``None`` short-circuits to ``None``.
        model: Model slug to query.
        max_tokens: Upper bound on the completion length.
        temperature: Sampling temperature.
        timeout_s: Total request timeout in seconds.

    Returns:
        str | None: The assistant message content, or ``None`` on any failure.
    """
    if not api_key:
        return None

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Title": "PEA Sniper Terminal V-Prime",
    }
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                _OPENROUTER_URL, json=payload, headers=headers
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error("OpenRouter HTTP %s: %s", resp.status, body[:200])
                    return None
                data = await resp.json()
                content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                ).strip()
                return content or None
    except Exception:  # noqa: BLE001 - never let LLM I/O crash a caller.
        logger.exception("OpenRouter request failed.")
        return None


class NarrativeExplainer:
    """Generates concise trade rationales via OpenRouter."""

    def __init__(self) -> None:
        """Read the OpenRouter API key and model slug from the environment."""
        self.api_key: str | None = os.getenv("OPENROUTER_API_KEY")
        self.model: str = os.getenv("OPENROUTER_MODEL", _DEFAULT_MODEL)
        if not self.api_key:
            logger.warning(
                "OPENROUTER_API_KEY not set; explanations will use the fallback."
            )

    @staticmethod
    def _sector_breakdown(portfolio: PortfolioState) -> str:
        """Return a compact 'Sector X%' string from the portfolio positions."""
        sectors = sorted({p.sector for p in portfolio.positions})
        if not sectors:
            return "no open positions"
        parts = [
            f"{sector} {portfolio.get_sector_weight(sector) * 100:.0f}%"
            for sector in sectors
        ]
        return ", ".join(parts)

    def _build_prompt(self, signal: Signal, portfolio: PortfolioState) -> str:
        """Compose the user prompt describing the trade and portfolio context."""
        qty = signal.target_qty if signal.target_qty is not None else 0
        breakdown = self._sector_breakdown(portfolio)
        cash_pct = (
            portfolio.cash_available / portfolio.total_equity * 100
            if portfolio.total_equity > 0
            else 0.0
        )
        return (
            f"Explain why buying {qty} shares of {signal.ticker} makes sense. "
            f"Signal type: {signal.signal_type.value}. "
            f"Technical score: {signal.score:.1f}/100. "
            f"Underlying setup: {signal.reason}. "
            f"Portfolio context: {cash_pct:.0f}% cash, sector exposure -> "
            f"{breakdown}. "
            "Be concise and professional."
        )

    async def explain_trade(
        self, signal: Signal, portfolio: PortfolioState
    ) -> str:
        """Generate a 2-3 sentence rationale for an approved trade.

        Args:
            signal: The APPROVED, already-sized signal.
            portfolio: Current portfolio snapshot for context.

        Returns:
            str: The LLM explanation, or a safe fallback string on any error.
        """
        if not self.api_key:
            return _FALLBACK

        system_prompt = (
            "You are a quantitative analyst at a systematic PEA fund. A "
            "mathematical model has ALREADY decided this trade; you do not make "
            "decisions. Explain the rationale in strictly 2 to 3 short "
            "sentences. No greetings, no disclaimers, no financial advice - "
            "just crisp, professional analysis."
        )
        content = await openrouter_chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": self._build_prompt(signal, portfolio)},
            ],
            api_key=self.api_key,
            model=self.model,
            max_tokens=180,
            temperature=0.4,
        )
        return content or _FALLBACK


if __name__ == "__main__":
    import asyncio
    from datetime import datetime, timezone

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    sys.path.insert(0, _CORE_DIR)
    from data_models import Position, SignalType  # noqa: E402

    demo_signal = Signal(
        ticker="AI.PA",
        signal_type=SignalType.BUY,
        score=88.0,
        target_qty=7,
        reason="RSI < 30 while Price > SMA200. Mean-reversion setup.",
    )
    demo_portfolio = PortfolioState(
        cash_available=8000.0,
        total_equity=20000.0,
        positions=[
            Position(ticker="MC.PA", qty_shares=2, avg_entry_price=600,
                     current_price=600, sector="Luxury"),
        ],
        last_updated=datetime.now(timezone.utc),
    )

    async def _demo() -> None:
        explainer = NarrativeExplainer()
        print("Prompt preview:\n", explainer._build_prompt(demo_signal, demo_portfolio))
        text = await explainer.explain_trade(demo_signal, demo_portfolio)
        print("\nExplanation:\n", text)

    asyncio.run(_demo())
```

## FILE: 05_interfaces/terminal_dashboard.py
```python
"""Web Terminal (Streamlit dashboard) for PEA Sniper Terminal V-Prime.

BLOOMBERG TERMINAL EDITION - command center on a pure-black, high-contrast UI.

Design rules enforced here:
  * Pure black background (#050505); text in white / neon-green / amber / cyan.
  * No white dataframes: every table is a colour-coded
    ``plotly.graph_objects.Table`` (black cells, neon/red text), backed by a
    forced dark theme via ``.streamlit/config.toml``.
  * Every metric carries a plain-language explanation (``help=`` / HTML title).
  * Raw tickers are always shown as "Full Name (TICKER)" via ``format_name``.

Features: TradingView ticker tape, top HUD, Risk/Macro HUD, General & Signaux
(adaptive portfolio suggestion, news, geo brief, signal ledger), portfolio +
wallet editor, Exploration (market scan + full ticker chart/TA/news/insiders/
Polymarket), universe, architecture docs.

Run (auto-opens browser):
    .\\run_dashboard.ps1
    # or: venv_x64\\Scripts\\streamlit run 05_interfaces/terminal_dashboard.py
"""

import asyncio
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as pex
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
import yaml
import yfinance as yf

# --- Cross-package imports (dirs start with digits) --------------------------
_ROOT = Path(__file__).resolve().parent.parent
for _sub in ("00_data_sensors", "01_memory_core", "02_quant_engine",
             "03_risk_portfolio", "04_orchestrator_ai", "05_interfaces"):
    sys.path.insert(0, str(_ROOT / _sub))

from sqlite_portfolio import PortfolioDB  # noqa: E402
from data_models import Position, PortfolioState  # noqa: E402

try:
    from equity_metrics import compute_equity_metrics  # noqa: E402
except Exception:  # noqa: BLE001
    compute_equity_metrics = None  # type: ignore[assignment]

try:  # Optional sensors — the dashboard still works if a network dep is missing.
    from macro_alpha_api import MacroAlphaSensor  # noqa: E402
except Exception:  # noqa: BLE001
    MacroAlphaSensor = None  # type: ignore[assignment]

try:
    from news_sentiment_llm import NewsSentimentScorer  # noqa: E402
except Exception:  # noqa: BLE001
    NewsSentimentScorer = None  # type: ignore[assignment]

_DB_DIR = _ROOT / "database"
_SQLITE_PATH = _DB_DIR / "portfolio.db"
_UNIVERSE_PATH = _ROOT / "config" / "pea_universe.yaml"
_RISK_PATH = _ROOT / "config" / "risk_params.yaml"


def _load_risk() -> dict:
    """Load risk parameters (thresholds shown in the risk HUD)."""
    try:
        with open(_RISK_PATH, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001
        return {}


_RISK = _load_risk()
_VIX_PANIC = float(_RISK.get("VIX_PANIC_THRESHOLD", 30.0))
_SAT_BUDGET = float(_RISK.get("SATELLITE_MAX_BUDGET_PCT", 0.30))
_MAX_SECTOR = float(_RISK.get("MAX_SECTOR_WEIGHT_PCT", 0.25))
_CORE_TICKER = str(_RISK.get("CORE_TICKER", "CW8.PA"))

# --- Bloomberg palette (pure black + stark neon accents) ---------------------
_BG = "#050505"
_PANEL = "#000000"
_WHITE = "#FFFFFF"
_NEON = "#00FF00"       # neon green (bullish / positive)
_AMBER = "#FFB000"      # amber (warning / median)
_CYAN = "#00FFFF"       # cyan (labels / links / info)
_RED = "#FF3B30"        # red (bearish / negative / breach)
_MUTED = "#9BA3AF"      # readable light gray (never gray-on-gray)
_GRID = "#1A1A1A"       # chart gridlines
_HEADER_FILL = "#0A0A0A"
_BRIGHT_SERIES = ["#00FF00", "#00FFFF", "#FFB000", "#FF3B30", "#FF00FF",
                  "#1E90FF", "#FFFFFF", "#ADFF2F", "#FF7F50", "#7FFFD4"]
# Diverging scale with a DARK neutral (avoids glaring pale-yellow on black).
_DIVERGE = [[0.0, _RED], [0.5, "#2A2A2A"], [1.0, _NEON]]

# =============================================================================
# STEP 1.2 - Ticker -> full company name mapping
# =============================================================================
TICKER_NAMES: dict[str, str] = {
    "MC.PA": "LVMH", "OR.PA": "L'Oreal", "AI.PA": "Air Liquide",
    "RMS.PA": "Hermes", "CDI.PA": "Christian Dior", "RACE.MI": "Ferrari",
    "EL.PA": "EssilorLuxottica", "ASML.AS": "ASML", "SAP.DE": "SAP",
    "CW8.PA": "Amundi MSCI World PEA", "^VIX": "S&P 500 Volatility",
    "^V2TX": "Euro Stoxx 50 Volatility", "^STOXX50E": "Euro Stoxx 50",
    "CASH": "Liquidites",
}


def format_name(ticker: str) -> str:
    """Return ``"Full Name (TICKER)"`` when known, else the raw ticker."""
    name = TICKER_NAMES.get(ticker)
    return f"{name} ({ticker})" if name else ticker


def short_name(ticker: str) -> str:
    """Return just the company name when known, else the raw ticker."""
    return TICKER_NAMES.get(ticker, ticker)


# =============================================================================
# Page config & Bloomberg CSS
# =============================================================================
st.set_page_config(
    page_title="PEA Sniper Terminal | V-Prime",
    layout="wide",
    page_icon="\U0001F6E1\uFE0F",
    initial_sidebar_state="collapsed",
)

st.markdown(
    f"""
<style>
    .stApp {{ background-color: {_BG}; }}
    section[data-testid="stSidebar"] {{ background-color: {_PANEL};
        border-right: 1px solid #222; }}
    h1, h2, h3, h4 {{ color: {_WHITE} !important;
        font-family: 'Courier New', monospace; letter-spacing: 1px; }}

    /* --- Custom metric boxes (HUD) --- */
    .metric-box {{ background-color: {_PANEL}; padding: 15px 18px;
        border: 1px solid #333333; border-left: 4px solid {_NEON};
        margin-bottom: 10px; font-family: 'Courier New', monospace; }}
    .metric-box.amber {{ border-left-color: {_AMBER}; }}
    .metric-box.cyan  {{ border-left-color: {_CYAN}; }}
    .metric-box.red   {{ border-left-color: {_RED}; }}
    .metric-box.muted {{ border-left-color: #555555; }}
    .metric-box:hover {{ border-color: #555555; cursor: help; }}
    .metric-title {{ color: {_CYAN}; font-size: 12px; text-transform: uppercase;
        letter-spacing: 1.5px; }}
    .metric-value {{ color: {_WHITE}; font-size: 22px; font-weight: 700;
        margin-top: 4px; word-break: break-word; line-height: 1.25; }}
    .metric-sub {{ font-size: 12px; margin-top: 4px; font-weight: 600;
        word-break: break-word; }}
    .sub-green {{ color: {_NEON}; }}
    .sub-red   {{ color: {_RED}; }}
    .sub-amber {{ color: {_AMBER}; }}
    .sub-muted {{ color: {_MUTED}; }}

    /* --- Native metric widgets --- */
    [data-testid="stMetricValue"] {{ color: {_WHITE} !important;
        font-family: 'Courier New', monospace; }}
    [data-testid="stMetricLabel"] p {{ color: {_CYAN} !important;
        text-transform: uppercase; letter-spacing: 1px; font-weight: 600; }}

    /* --- Info / explanation banners --- */
    .info-text {{ color: #C8D0D8; font-size: 14px; margin-bottom: 14px;
        padding: 8px 12px; border-left: 3px solid {_CYAN};
        background-color: #0A0A0A; }}
    .eli5 {{ color: {_WHITE}; font-size: 14px; line-height: 1.6;
        margin-bottom: 14px; padding: 12px 16px; border: 1px solid #333333;
        border-left: 4px solid {_AMBER}; background-color: #0A0A0A; }}

    /* --- Tabs --- */
    .stTabs [data-baseweb="tab-list"] {{ gap: 2px; border-bottom: 1px solid #222; }}
    .stTabs [data-baseweb="tab"] {{ background-color: {_PANEL};
        color: {_MUTED}; font-family: 'Courier New', monospace; }}
    .stTabs [aria-selected="true"] {{ color: {_NEON} !important; }}
</style>
""",
    unsafe_allow_html=True,
)


def metric_box(title: str, value: str, sub: str = "", accent: str = "",
               sub_cls: str = "sub-muted", help_text: str = "") -> str:
    """Build a Bloomberg-style metric box with a hover tooltip (title attr)."""
    cls = ("metric-box " + accent).strip()
    tip = f' title="{help_text}"' if help_text else ""
    sub_html = f'<div class="metric-sub {sub_cls}">{sub}</div>' if sub else ""
    return (f'<div class="{cls}"{tip}><div class="metric-title">{title}</div>'
            f'<div class="metric-value">{value}</div>{sub_html}</div>')


def dark_table(display_df: pd.DataFrame, height: int | None = None,
               font_color_map: dict[str, list[str]] | None = None,
               col_widths: list[float] | None = None) -> go.Figure:
    """Render a strictly dark, colour-coded table via plotly go.Table.

    Args:
        display_df: Pre-formatted (string) columns to display.
        height: Fixed pixel height (Plotly tables scroll when rows overflow).
        font_color_map: Optional ``{column: [per-row colors]}`` overrides.
        col_widths: Optional relative column widths.

    Returns:
        go.Figure: A dark table figure ready for ``st.plotly_chart``.
    """
    headers = list(display_df.columns)
    n = len(display_df)
    col_colors = [
        (font_color_map[c] if font_color_map and c in font_color_map
         else [_WHITE] * n)
        for c in headers
    ]
    fig = go.Figure(data=[go.Table(
        columnwidth=col_widths,
        header=dict(
            values=[f"<b>{h}</b>" for h in headers],
            fill_color=_HEADER_FILL,
            font=dict(color=_CYAN, size=13, family="Courier New"),
            align="left", line_color="#333333", height=34,
        ),
        cells=dict(
            values=[display_df[c].tolist() for c in headers],
            fill_color=_BG,
            font=dict(color=col_colors, size=12, family="Courier New"),
            align="left", line_color=_GRID, height=30,
        ),
    )])
    fig.update_layout(
        paper_bgcolor=_BG, plot_bgcolor=_BG,
        margin=dict(t=0, l=0, r=0, b=0),
        height=height or min(700, 44 + 30 * max(n, 1)),
    )
    return fig


def _style_dark_fig(fig: go.Figure, height: int | None = None) -> go.Figure:
    """Apply the shared black/neon chart theme to a plotly figure."""
    fig.update_layout(template="plotly_dark", paper_bgcolor=_BG,
                      plot_bgcolor=_BG,
                      font=dict(family="Courier New", color=_WHITE),
                      legend=dict(font=dict(color=_WHITE)))
    fig.update_xaxes(gridcolor=_GRID, zerolinecolor=_GRID)
    fig.update_yaxes(gridcolor=_GRID, zerolinecolor=_GRID)
    if height:
        fig.update_layout(height=height)
    return fig


# =============================================================================
# Cached data loaders (read-only)
# =============================================================================
@st.cache_data(ttl=300)
def load_universe() -> pd.DataFrame:
    """Load the full tradable universe as a DataFrame.

    Returns:
        pd.DataFrame: Columns ``Ticker``, ``Name``, ``Sector`` (empty on error).
    """
    try:
        with open(_UNIVERSE_PATH, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        rows = [
            {"Ticker": e["ticker"], "Name": e.get("name", e["ticker"]),
             "Sector": sector}
            for sector, members in data.get("universe", {}).items()
            for e in members
        ]
        return pd.DataFrame(rows)
    except Exception:  # noqa: BLE001
        return pd.DataFrame(
            [{"Ticker": t, "Name": t, "Sector": "Unknown"}
             for t in ("MC.PA", "OR.PA", "AI.PA", "ASML.AS", "SAP.DE")]
        )


@st.cache_data(ttl=60)
def load_portfolio_state():
    """Load the current portfolio snapshot (cached 60s)."""
    if not _SQLITE_PATH.exists():
        return None
    return PortfolioDB(db_path=_SQLITE_PATH).get_portfolio_state()


@st.cache_data(ttl=60)
def load_equity_curve() -> pd.DataFrame:
    """Load the daily equity curve from SQLite (cached 60s)."""
    if not _SQLITE_PATH.exists():
        return pd.DataFrame(columns=["date", "equity", "cash"])
    return PortfolioDB(db_path=_SQLITE_PATH).get_equity_curve()


@st.cache_data(ttl=60)
def load_signals(statuses: tuple[str, ...], limit: int | None = None) -> pd.DataFrame:
    """Load audit-log rows for the given statuses (cached 60s)."""
    if not _SQLITE_PATH.exists():
        return pd.DataFrame()
    db = PortfolioDB(db_path=_SQLITE_PATH)
    return pd.DataFrame(db.fetch_signals_by_status(list(statuses), limit=limit))


@st.cache_data(ttl=300, show_spinner=False)
def _extract_close_frame(raw: pd.DataFrame, tickers: tuple[str, ...] | list[str]) -> pd.DataFrame:
    """Extract a clean Close matrix from yfinance download (no cross-ticker fill)."""
    if raw is None or raw.empty:
        return pd.DataFrame()
    close = raw
    if isinstance(raw.columns, pd.MultiIndex):
        lvl0 = raw.columns.get_level_values(0)
        if "Close" in lvl0:
            close = raw["Close"]
        elif "Adj Close" in lvl0:
            close = raw["Adj Close"]
    if isinstance(close, pd.Series):
        name = tickers[0] if tickers else "TICKER"
        close = close.to_frame(name=name)
    # Per-column forward fill only — NEVER bfill across columns (that created
    # flat 0% performances and swapped prices between tickers).
    close = close.apply(lambda s: s.ffill())
    return close


def _valid_price_series(series: pd.Series, min_points: int = 3) -> pd.Series | None:
    """Drop flat/NaN series that would produce fake 0% performances."""
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) < min_points:
        return None
    if float(s.nunique()) < 2:
        return None  # constant after fill = bad data
    if float(s.iloc[0]) <= 0 or float(s.iloc[-1]) <= 0:
        return None
    return s


@st.cache_data(ttl=600, show_spinner=False)
def get_market_performance(
    tickers: tuple[str, ...],
    period: str | None = "1mo",
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """Compute performance over a preset period or an explicit date range."""
    if not tickers:
        return pd.DataFrame()
    try:
        # Cap batch size — huge universes make yfinance return sparse junk.
        batch = list(tickers)[:120]
        if start:
            raw = yf.download(batch, start=start, end=end, progress=False,
                              auto_adjust=True, threads=True)
        else:
            raw = yf.download(batch, period=period, progress=False,
                              auto_adjust=True, threads=True)
        close = _extract_close_frame(raw, batch)
        if close.empty:
            return pd.DataFrame()

        rows = []
        for t in close.columns:
            series = _valid_price_series(close[t])
            if series is None:
                continue
            start_price, end_price = float(series.iloc[0]), float(series.iloc[-1])
            perf = (end_price / start_price - 1.0) * 100.0
            rows.append({
                "Ticker": str(t),
                "Start Price": start_price,
                "Current Price": end_price,
                "Performance (%)": perf,
            })
        if not rows:
            return pd.DataFrame()
        return (pd.DataFrame(rows)
                .sort_values("Performance (%)", ascending=False)
                .reset_index(drop=True))
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def get_normalized_prices(
    tickers: tuple[str, ...], period: str | None, start: str | None, end: str | None
) -> pd.DataFrame:
    """Return prices rebased to 100 at the interval start (for line charts)."""
    if not tickers:
        return pd.DataFrame()
    try:
        batch = list(tickers)[:40]
        if start:
            raw = yf.download(batch, start=start, end=end, progress=False,
                              auto_adjust=True, threads=True)
        else:
            raw = yf.download(batch, period=period, progress=False,
                              auto_adjust=True, threads=True)
        close = _extract_close_frame(raw, batch)
        if close.empty:
            return pd.DataFrame()
        out = pd.DataFrame(index=close.index)
        for t in close.columns:
            series = _valid_price_series(close[t], min_points=2)
            if series is None:
                continue
            base = float(series.iloc[0])
            out[str(t)] = (series / base) * 100.0
        return out.dropna(how="all")
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


@st.cache_data(ttl=1800, show_spinner=False)
def get_recent_news(symbol: str, limit: int = 6) -> list[dict]:
    """Fetch recent news: Boursorama first (rich), then yfinance fallback."""
    # --- Primary: Boursorama scraper ----------------------------------------
    try:
        scrapers_dir = _ROOT / "00_data_sensors" / "scrapers"
        if str(scrapers_dir) not in sys.path:
            sys.path.insert(0, str(scrapers_dir))
        from bourso_scraper import BoursoramaScraper  # noqa: WPS433

        profile = BoursoramaScraper().get_instrument_profile(symbol)
        items = (profile or {}).get("news_items") or []
        if items:
            sentiment = (profile or {}).get("sentiment") or "Unknown"
            elig = ",".join((profile or {}).get("eligibility") or []) or "?"
            out = []
            for n in items[:limit]:
                out.append({
                    "title": n.get("title", ""),
                    "link": n.get("link") or "#",
                    "date": n.get("date") or "Recent",
                    "provider": (
                        f"Boursorama · {n.get('provider') or 'local'} · "
                        f"sentiment {sentiment} · elig {elig}"
                    ),
                })
            return out
        # Legacy title-only fallback from get_retail_sentiment_and_news
        bourso = BoursoramaScraper().get_retail_sentiment_and_news(symbol)
        headlines = (bourso or {}).get("news") or []
        if headlines:
            sentiment = (bourso or {}).get("sentiment") or "Unknown"
            return [
                {
                    "title": title,
                    "link": "#",
                    "date": "Recent",
                    "provider": f"Boursorama · sentiment {sentiment}",
                }
                for title in headlines[:limit]
            ]
    except Exception:  # noqa: BLE001
        pass

    # --- Fallback: yfinance -------------------------------------------------
    try:
        raw = yf.Ticker(symbol).news or []
        items = []
        for n in raw[:limit]:
            content = n.get("content", n)
            title = content.get("title") or n.get("title") or ""
            link = (
                content.get("clickThroughUrl", {}).get("url")
                or content.get("canonicalUrl", {}).get("url")
                or n.get("link")
                or "#"
            )
            date_str = content.get("pubDate") or content.get("displayTime") or ""
            provider = (content.get("provider") or {}).get("displayName", "")
            if title:
                items.append({"title": title, "link": link,
                              "date": (date_str or "")[:10] or "Recent",
                              "provider": provider or "Yahoo Finance"})
        return items
    except Exception:  # noqa: BLE001
        return []


@st.cache_data(ttl=1800, show_spinner=False)
def get_bourso_profile(ticker: str) -> dict:
    """Cached Boursorama instrument profile (eligibility, consensus, ISIN)."""
    try:
        scrapers_dir = _ROOT / "00_data_sensors" / "scrapers"
        if str(scrapers_dir) not in sys.path:
            sys.path.insert(0, str(scrapers_dir))
        from bourso_scraper import BoursoramaScraper  # noqa: WPS433
        return BoursoramaScraper().get_instrument_profile(ticker) or {}
    except Exception:  # noqa: BLE001
        return {}


def _tv_symbol(ticker: str) -> str:
    """Map a Yahoo ticker to a TradingView exchange:symbol string."""
    mapping = {".PA": "EURONEXT", ".AS": "EURONEXT", ".BR": "EURONEXT",
               ".LS": "EURONEXT", ".DE": "XETR", ".MC": "BME", ".MI": "MIL",
               ".HE": "OMXHEX", ".IR": "EURONEXTDUBLIN"}
    for suffix, exch in mapping.items():
        if ticker.endswith(suffix):
            return f"{exch}:{ticker[: -len(suffix)]}"
    return ticker


@st.cache_data(ttl=600, show_spinner=False)
def get_vix() -> float:
    """Current market volatility gauge (VSTOXX, VIX proxy fallback)."""
    if MacroAlphaSensor is None:
        return 15.0
    try:
        return float(MacroAlphaSensor().get_european_vix())
    except Exception:  # noqa: BLE001
        return 15.0


@st.cache_data(ttl=900, show_spinner=False)
def get_core_regime() -> dict:
    """Return the Core ETF regime (price vs 200-day SMA)."""
    try:
        df = yf.download(_CORE_TICKER, period="1y", progress=False,
                         auto_adjust=False)
        if df is None or df.empty:
            return {}
        close = df["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close = close.dropna()
        price = float(close.iloc[-1])
        sma200 = float(close.tail(200).mean())
        return {
            "ticker": _CORE_TICKER,
            "price": price,
            "sma200": sma200,
            "crash": price < sma200,
            "gap_pct": (price / sma200 - 1) * 100 if sma200 else 0.0,
        }
    except Exception:  # noqa: BLE001
        return {}


@st.cache_data(ttl=600, show_spinner=False)
def get_indicators(ticker: str) -> dict:
    """Compute RSI(14) + SMA 5/50/200 + trend flags for one ticker."""
    try:
        import pandas_ta_classic as ta  # noqa: F401  (registers .ta accessor)
    except Exception:  # noqa: BLE001
        try:
            import pandas_ta as ta  # noqa: F401
        except Exception:  # noqa: BLE001
            return {}
    try:
        df = yf.download(ticker, period="1y", progress=False, auto_adjust=False)
        if df is None or df.empty:
            return {}
        close = df["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close = close.dropna()
        if len(close) < 30:
            return {}
        frame = close.to_frame("Close")
        rsi = frame.ta.rsi(close=frame["Close"], length=14)
        out = {
            "close": float(close.iloc[-1]),
            "rsi": float(rsi.iloc[-1]) if rsi is not None and not rsi.empty else None,
            "sma5": float(close.tail(5).mean()),
            "sma50": float(close.tail(50).mean()) if len(close) >= 50 else None,
            "sma200": float(close.tail(200).mean()) if len(close) >= 200 else None,
            "chg_1d": float((close.iloc[-1] / close.iloc[-2] - 1) * 100)
            if len(close) >= 2 else 0.0,
            "chg_5d": float((close.iloc[-1] / close.iloc[-6] - 1) * 100)
            if len(close) >= 6 else 0.0,
            "vol_ann": float(close.pct_change().dropna().tail(60).std() * (252 ** 0.5)
                             * 100),
        }
        return out
    except Exception:  # noqa: BLE001
        return {}


@st.cache_data(ttl=1800, show_spinner=False)
def get_alpha_signals(ticker: str) -> dict:
    """Fetch alternative-data signals (put/call, insider, polymarket)."""
    if MacroAlphaSensor is None:
        return {}
    try:
        s = MacroAlphaSensor()
        return {
            "put_call": s.get_put_call_ratio(ticker),
            "insider": s.get_insider_activity(ticker),
            "polymarket": s.get_polymarket_sentiment(f"{ticker} outlook"),
        }
    except Exception:  # noqa: BLE001
        return {}


@st.cache_data(ttl=1800, show_spinner=False)
def get_insider_data(ticker: str) -> pd.DataFrame:
    """Fetch insider transactions: AMF BDIF -> FMP -> yfinance."""
    # --- 1) AMF BDIF (official French legal source) --------------------------
    try:
        scrapers_dir = _ROOT / "00_data_sensors" / "scrapers"
        if str(scrapers_dir) not in sys.path:
            sys.path.insert(0, str(scrapers_dir))
        from amf_scraper import AmfInsiderScraper  # noqa: WPS433

        profile: dict = {}
        try:
            profile = get_bourso_profile(ticker)
        except Exception:  # noqa: BLE001
            profile = {}
        amf = AmfInsiderScraper().get_recent_declarations(
            ticker,
            isin=profile.get("isin"),
            issuer=profile.get("name"),
        )
        if amf is not None and not amf.empty:
            out = amf.head(25).copy()
            if "Source" not in out.columns:
                out["Source"] = "AMF BDIF"
            return out.reset_index(drop=True)
    except Exception:  # noqa: BLE001
        pass

    # --- 2) FMP (secondary) --------------------------------------------------
    try:
        import os
        import requests

        api_key = os.getenv("FMP_API_KEY")
        if api_key:
            symbol = ticker.split(".")[0]
            url = (
                "https://financialmodelingprep.com/api/v4/insider-trading"
                f"?symbol={symbol}&apikey={api_key}"
            )
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                payload = resp.json()
                if isinstance(payload, list) and payload:
                    rows = []
                    for row in payload[:25]:
                        if not isinstance(row, dict):
                            continue
                        rows.append({
                            "Insider": row.get("reportingName")
                            or row.get("ownerName")
                            or "",
                            "Transaction": row.get("transactionType")
                            or row.get("acquistionOrDisposition")
                            or "",
                            "Shares": row.get("securitiesTransacted")
                            or row.get("shares"),
                            "Value": row.get("value") or row.get("price"),
                            "Date": row.get("transactionDate")
                            or row.get("filingDate"),
                            "Source": "FMP",
                        })
                    if rows:
                        return pd.DataFrame(rows)
    except Exception:  # noqa: BLE001
        pass

    # --- 3) yfinance (tertiary) ----------------------------------------------
    try:
        raw = yf.Ticker(ticker).insider_transactions
        if isinstance(raw, pd.DataFrame) and not raw.empty:
            df = raw.copy()
            df = df.rename(columns={"Start Date": "Date"})
            keep = [c for c in ("Insider", "Position", "Transaction", "Shares",
                                "Value", "Date") if c in df.columns]
            if keep:
                out = df[keep].copy()
                out["Source"] = "Yahoo Finance"
                if "Date" in out.columns:
                    out = out.sort_values("Date", ascending=False)
                if "Value" in out.columns:
                    out["Value"] = pd.to_numeric(out["Value"], errors="coerce")
                if "Shares" in out.columns:
                    out["Shares"] = pd.to_numeric(out["Shares"], errors="coerce")
                return out.head(25).reset_index(drop=True)
    except Exception:  # noqa: BLE001
        pass
    return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def heuristic_news_score(title: str) -> int:
    """Keyword impact score when LLM is unavailable or returns ~0."""
    t = (title or "").casefold()
    if not t:
        return 0
    bull = (
        "rachat", "acquisition", "fusion", "record", "hausse", "rebond",
        "dividende", "bénéfice", "benefice", "profit", "croissance", "contrat",
        "upgrade", "buyback", "guidance relev", "surperform", "positif",
        "approval", "autorisation", "victoire", "accord",
    )
    bear = (
        "amende", "fraude", "scandale", "baisse", "perte", "licenciement",
        "faillite", "recession", "guerre", "sanction", "downgrade", "alerte",
        "profit warning", "déception", "deception", "enquête", "enquete",
        "rachat d'actions annul", "coupures", "gel", "crise", "krach",
        "miss", "retard", "rappel",
    )
    score = 0
    for w in bull:
        if w in t:
            score += 28
    for w in bear:
        if w in t:
            score -= 32
    # Cap so heuristic never pretends to be a full LLM conviction.
    return int(max(-75, min(75, score)))


@st.cache_data(ttl=3600, show_spinner=False)
def score_news_with_llm(ticker: str, title: str) -> int:
    """Score a single headline (-100..+100), LLM first then keyword fallback.

    Cache key is ``(ticker, title)`` — reloading does not re-bill OpenRouter.
    """
    if not title or not title.strip():
        return 0
    llm_score = 0
    if NewsSentimentScorer is not None:
        try:
            score = asyncio.run(
                NewsSentimentScorer().analyze_news(ticker, [title.strip()])
            )
            llm_score = int(round(float(score)))
        except Exception:  # noqa: BLE001
            llm_score = 0
    if abs(llm_score) >= 10:
        return llm_score
    # Blend: if LLM is flat, surface keyword impact so cards are not all grey.
    heur = heuristic_news_score(title)
    if abs(heur) > abs(llm_score):
        return heur
    return llm_score


def run_sentiment(ticker: str, headlines: list[str]) -> float | None:
    """Synchronously score an aggregate news bundle (legacy aggregate button)."""
    if not headlines or NewsSentimentScorer is None:
        return None
    try:
        return asyncio.run(NewsSentimentScorer().analyze_news(ticker, headlines))
    except Exception:  # noqa: BLE001
        return None


def _sentiment_pill(score: int) -> str:
    """HTML badge for a -100..+100 news sentiment score."""
    if score > 20:
        color, bg, emoji = _NEON, "#0A2A0A", "\U0001F7E2"
    elif score < -20:
        color, bg, emoji = _RED, "#2A0A0A", "\U0001F534"
    else:
        color, bg, emoji = _MUTED, "#1A1A1A", "\u26AA"
    return (
        f"<span style='display:inline-block; padding:2px 8px; border-radius:10px; "
        f"background:{bg}; color:{color}; font-weight:700; font-size:12px; "
        f"font-family:Courier New,monospace; border:1px solid {color}; "
        f"margin-right:8px;'>{emoji} {score:+d}</span>"
    )


def news_impact_meta(score: int) -> dict:
    """Map a sentiment score to impact level + plain-French justification."""
    abs_s = abs(int(score))
    if abs_s >= 55:
        level, color = "FORT", _RED if score < 0 else _NEON
    elif abs_s >= 25:
        level, color = "MOYEN", _AMBER
    elif abs_s >= 10:
        level, color = "FAIBLE", _CYAN
    else:
        level, color = "NEGLIGEABLE", _MUTED

    if score >= 55:
        why = ("Signal haussier fort : la new pousse clairement a l'optimisme. "
               "Surveiller un eventuel renforcement / hold si deja en portefeuille.")
    elif score >= 25:
        why = ("Biais positif modere. Utile en confirmation d'un signal quant "
               "(RSI survendu + rebond), pas comme ordre d'achat seul.")
    elif score <= -55:
        why = ("Signal baissier fort : risque de pression vendeuse. Si la ligne "
               "est detenue, verifier stop / taille ; pas de nouvel achat satellite.")
    elif score <= -25:
        why = ("Biais negatif. Eviter d'acheter 'a la baisse' sans filtre "
               "momentum (Close > SMA5) et sans EPS positif.")
    elif abs_s >= 10:
        why = ("Bruit d'information faible. Ne change pas la decision du bot : "
               "les filtres mathematiques restent prioritaires.")
    else:
        why = ("Impact negligeable sur le pricing. Ignorer pour le sizing — "
               "garder le focus VIX / regime Core / RSI.")
    return {"level": level, "color": color, "why": why, "abs": abs_s}


def render_news_card(ticker: str, item: dict, score: int | None) -> None:
    """Render one news card with impact badge + justified explanation."""
    sc = 0 if score is None else int(score)
    meta = news_impact_meta(sc)
    pill = _sentiment_pill(sc) if score is not None else ""
    prov = " \u00b7 ".join(
        x for x in (item.get("provider"), item.get("date"), format_name(ticker)) if x
    )
    st.markdown(
        f"<div style='background:#0A0A0A;padding:12px 14px;margin-bottom:10px;"
        f"border-left:4px solid {meta['color']};border:1px solid #222;'>"
        f"<div style='margin-bottom:6px;'>{pill}"
        f"<span style='color:{meta['color']};font-weight:700;font-size:12px;"
        f"letter-spacing:1px;'>IMPACT {meta['level']}</span></div>"
        f"<a href='{item.get('link') or '#'}' target='_blank' "
        f"style='color:{_CYAN};text-decoration:none;font-weight:700;font-size:15px;'>"
        f"{item.get('title', '')}</a>"
        f"<div style='color:{_MUTED};font-size:12px;margin-top:4px;'>{prov}</div>"
        f"<div style='color:#D0D0D0;font-size:13px;margin-top:8px;line-height:1.45;'>"
        f"<b style='color:{_AMBER};'>Pourquoi ca compte :</b> {meta['why']}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


def save_wallet(cash: float, positions_df: pd.DataFrame) -> str:
    """Persist an edited wallet to SQLite. Returns an error string or ''."""
    try:
        positions: list[Position] = []
        for _, row in positions_df.iterrows():
            ticker = str(row.get("Ticker", "")).strip()
            if not ticker:
                continue
            qty = int(float(row.get("Qte", 0) or 0))
            if qty <= 0:
                continue
            pru = float(row.get("PRU", 0) or 0)
            cours = float(row.get("Cours", pru) or pru)
            sector = str(row.get("Secteur", "Unknown") or "Unknown")
            if pru <= 0 or cours <= 0:
                return f"PRU/Cours invalide pour {ticker}."
            positions.append(Position(
                ticker=ticker, qty_shares=qty, avg_entry_price=pru,
                current_price=cours, sector=sector,
            ))
        invested = sum(p.market_value for p in positions)
        equity = float(cash) + invested
        state = PortfolioState(
            cash_available=float(cash),
            total_equity=equity,
            positions=positions,
            last_updated=datetime.now(),
        )
        PortfolioDB(db_path=_SQLITE_PATH).update_portfolio(state)
        st.cache_data.clear()
        return ""
    except Exception as exc:  # noqa: BLE001
        return str(exc)


@st.cache_data(ttl=900, show_spinner=False)
def get_earnings_events(tickers: tuple[str, ...]) -> list[dict]:
    """Best-effort upcoming earnings / events via yfinance calendar."""
    events: list[dict] = []
    for t in tickers[:12]:
        try:
            cal = yf.Ticker(t).calendar
            if cal is None:
                continue
            # yfinance may return dict or DataFrame depending on version.
            raw = None
            if isinstance(cal, dict):
                raw = cal.get("Earnings Date") or cal.get("earningsDate")
            elif isinstance(cal, pd.DataFrame) and not cal.empty:
                if "Earnings Date" in cal.index:
                    raw = cal.loc["Earnings Date"].tolist()
            if not raw:
                continue
            if not isinstance(raw, (list, tuple)):
                raw = [raw]
            for d in raw[:2]:
                events.append({
                    "ticker": t,
                    "event": "Resultats / Earnings",
                    "date": str(d)[:10],
                })
        except Exception:  # noqa: BLE001
            continue
    return events


@st.cache_data(ttl=1800, show_spinner=False)
def get_general_news_bundle(tickers: tuple[str, ...]) -> list[dict]:
    """Aggregate headlines across a watchlist (held + blue chips)."""
    bundle: list[dict] = []
    for t in tickers:
        try:
            for n in get_recent_news(t, limit=3):
                bundle.append({**n, "ticker": t})
        except Exception:  # noqa: BLE001
            continue
    # Deduplicate by title.
    seen: set[str] = set()
    out: list[dict] = []
    for n in bundle:
        key = (n.get("title") or "").casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(n)
    return out[:24]


@st.cache_data(ttl=3600, show_spinner=False)
def get_geopolitical_brief(vix: float, headlines: tuple[str, ...]) -> str:
    """Generate a short justified geopolitical/macro brief (LLM + fallback)."""
    context = (
        f"VIX/VSTOXX actuel: {vix:.1f} (seuil panique bot: {_VIX_PANIC:.0f}). "
        f"Core ETF: {_CORE_TICKER}. "
        f"Headlines: " + " | ".join(headlines[:8])
    )
    try:
        import os
        from llm_explainer import openrouter_chat

        key = os.getenv("OPENROUTER_API_KEY")
        if key:
            text = asyncio.run(openrouter_chat(
                messages=[
                    {"role": "system",
                     "content": "Analyste macro institutionnel. Factuel, chiffre, prudent."},
                    {"role": "user",
                     "content": (
                         "Tu es un risk manager macro pour un PEA francais (zero levier). "
                         "En 5-7 phrases max, donne un briefing geopolitique/macro "
                         "ACTIONNABLE et JUSTIFIE (chiffres, risques, implications "
                         "Core CW8 vs satellites). Pas de conseil personnalise. "
                         "Francais. Contexte:\n" + context
                     )},
                ],
                api_key=key,
                max_tokens=450,
            ))
            if text and len(text.strip()) > 40:
                return text.strip()
    except Exception:  # noqa: BLE001
        pass

    if vix > _VIX_PANIC:
        regime = (
            f"Panique mesuree (VIX {vix:.1f} > {_VIX_PANIC:.0f}) : le bot bloque "
            "les nouveaux achats satellites. Priorite : cash buffer + DCA Core."
        )
    elif vix > 22:
        regime = (
            f"Stress modere (VIX {vix:.1f}) : reduire l'agressivite satellite, "
            "garder le Core comme ancre."
        )
    else:
        regime = (
            f"Volatilite calme (VIX {vix:.1f}) : environnement favorable aux "
            "signaux mean-reversion satellites SI RSI<30 et Close>SMA5."
        )
    return (
        f"{regime} Justification : le VIX est le circuit-breaker officiel du "
        f"systeme. Les titres d'actualite fournis ({len(headlines)} headlines) "
        "servent de contexte qualitatif uniquement — ils ne declenchent jamais "
        "un ordre. Pour un PEA zero-levier, la discipline reste : budget "
        f"satellite max {_SAT_BUDGET*100:.0f}%, secteur max {_MAX_SECTOR*100:.0f}%, "
        "et Smart DCA sur le Core en cas de prix sous SMA200."
    )


def build_recommendations(
    portfolio_obj,
    pending_df: pd.DataFrame,
    vix: float,
    regime: dict,
) -> list[dict]:
    """Build justified actionable recommendations for the General tab."""
    recos: list[dict] = []

    if vix > _VIX_PANIC:
        recos.append({
            "prio": 1,
            "title": "GEL des achats satellites",
            "why": (f"VIX={vix:.1f} au-dessus du seuil {_VIX_PANIC:.0f}. "
                    "Le correlation firewall veto les nouveaux BUY stock-picking. "
                    "Le Smart DCA Core reste autorise."),
        })
    else:
        recos.append({
            "prio": 2,
            "title": "Fenetre satellite ouverte",
            "why": (f"VIX={vix:.1f} sous le seuil de panique. Les signaux "
                    "mean-reversion (RSI<30 + Close>SMA5 + EPS>0) peuvent passer."),
        })

    if regime:
        if regime.get("crash"):
            recos.append({
                "prio": 1,
                "title": f"DCA agressif sur {_CORE_TICKER}",
                "why": (f"Prix Core {_CORE_TICKER} sous SMA200 "
                        f"({regime.get('gap_pct', 0):+.1f}%). "
                        "Regle Smart DCA : viser ~75% d'allocation Core."),
            })
        else:
            recos.append({
                "prio": 3,
                "title": f"DCA standard {_CORE_TICKER}",
                "why": (f"Core au-dessus de SMA200 ({regime.get('gap_pct', 0):+.1f}%). "
                        "Allocation cible ~70% — pas de sur-accumulation."),
            })

    if pending_df is not None and not pending_df.empty:
        for _, row in pending_df.head(5).iterrows():
            recos.append({
                "prio": 1,
                "title": f"Signal {row.get('signal_type')} {format_name(row.get('ticker',''))}",
                "why": (f"Score {row.get('score', 0):.0f}/100 — "
                        f"{str(row.get('reason', ''))[:180]} "
                        "Approuver/refuser via Discord."),
            })

    for p in (portfolio_obj.positions if portfolio_obj else []):
        try:
            ind = get_indicators(p.ticker)
        except Exception:  # noqa: BLE001
            ind = {}
        if not ind:
            continue
        rsi = ind.get("rsi")
        pnl = p.unrealized_pnl_pct * 100
        if rsi is not None and rsi < 30 and ind.get("close", 0) > (ind.get("sma5") or 0):
            recos.append({
                "prio": 2,
                "title": f"Surveillance rebond {format_name(p.ticker)}",
                "why": (f"RSI={rsi:.0f} survendu + Close>SMA5. Ligne deja detenue "
                        f"(PnL {pnl:+.1f}%). Pas d'ajout auto — verifier budget secteur."),
            })
        if pnl <= -10:
            recos.append({
                "prio": 1,
                "title": f"Stop-loss candidat {format_name(p.ticker)}",
                "why": (f"PnL latent {pnl:+.1f}% (perte). "
                        "Le rebalancer mensuel sort a 100% si le cours casse "
                        "avg_entry - 2.5×ATR(14)."),
            })
        if pnl >= 20:
            recos.append({
                "prio": 2,
                "title": f"Prise de profit {format_name(p.ticker)}",
                "why": (f"PnL latent {pnl:+.1f}% au-dessus de +20%. "
                        "Regle : shave 20% des titres au prochain rebalance."),
            })

    recos.sort(key=lambda r: r["prio"])
    return recos[:10]


@st.cache_data(ttl=600, show_spinner=False)
def get_last_prices(tickers: tuple[str, ...]) -> dict[str, float]:
    """Batch last close prices — per-ticker history to avoid column mixups."""
    out: dict[str, float] = {}
    if not tickers:
        return out
    # Prefer one-shot batch, then validate each ticker individually on miss.
    try:
        raw = yf.download(list(tickers), period="10d", progress=False,
                          auto_adjust=True, threads=True)
        close = _extract_close_frame(raw, tickers)
        for t in close.columns:
            series = pd.to_numeric(close[t], errors="coerce").dropna()
            if len(series):
                px = float(series.iloc[-1])
                if px > 0.05:  # reject absurd penny mis-parses
                    out[str(t)] = px
    except Exception:  # noqa: BLE001
        pass
    missing = [t for t in tickers if t not in out]
    for t in missing:
        try:
            h = yf.Ticker(t).history(period="10d", auto_adjust=True)
            if h is not None and not h.empty and "Close" in h.columns:
                px = float(h["Close"].dropna().iloc[-1])
                if px > 0.05:
                    out[t] = px
        except Exception:  # noqa: BLE001
            continue
    return out


def build_ta_explanation(ind: dict, alpha: dict | None = None) -> str:
    """Plain-French technical analysis narrative for the selected ticker."""
    if not ind:
        return ("Pas assez de donnees de marche pour expliquer la configuration "
                "technique. Reessaie apres une mise a jour des cours.")
    parts: list[str] = []
    close = ind.get("close")
    rsi = ind.get("rsi")
    sma5, sma50, sma200 = ind.get("sma5"), ind.get("sma50"), ind.get("sma200")
    chg5 = ind.get("chg_5d")
    vol = ind.get("vol_ann")

    if rsi is not None:
        if rsi < 30:
            parts.append(
                f"RSI(14)={rsi:.0f} : zone <b>survendue</b>. Historiquement, "
                "cela favorise un rebond court terme — mais seulement si le "
                "filtre momentum (Close &gt; SMA5) confirme."
            )
        elif rsi > 70:
            parts.append(
                f"RSI(14)={rsi:.0f} : zone <b>surachetee</b>. Risque de "
                "repli / pause. Le bot n'ouvre pas de nouveaux satellites ici."
            )
        else:
            parts.append(
                f"RSI(14)={rsi:.0f} : zone neutre. Pas de signal mean-reversion "
                "fort ; les filtres quant restent prioritaires."
            )

    if close and sma200:
        if close > sma200:
            parts.append(
                f"Cours ({close:.2f}) <b>au-dessus</b> de la SMA200 "
                f"({sma200:.2f}) : tendance de fond haussiere."
            )
        else:
            parts.append(
                f"Cours ({close:.2f}) <b>sous</b> la SMA200 ({sma200:.2f}) : "
                "tendance de fond baissiere — prudence sur le sizing satellite."
            )

    if close and sma5:
        mom = "confirme" if close > sma5 else "ABSENT (Close &lt; SMA5)"
        parts.append(
            f"Momentum court terme (SMA5={sma5:.2f}) : {mom}. "
            "Sans Close&gt;SMA5, un RSI bas ne suffit pas a un BUY MRE."
        )

    if sma50 and close:
        parts.append(
            f"SMA50={sma50:.2f} — intermediaire. "
            + ("Prix au-dessus = biais moyen terme positif."
               if close > sma50 else
               "Prix en dessous = biais moyen terme negatif.")
        )

    if chg5 is not None:
        parts.append(f"Perf 5 seances : <b>{chg5:+.1f}%</b>.")
    if vol is not None:
        parts.append(
            f"Volatilite annualisee ~{vol:.0f}% : "
            + ("sizing reduit (parite de vol)." if vol > 35 else
               "volatilite raisonnable pour un satellite.")
        )

    alpha = alpha or {}
    pc = alpha.get("put_call")
    if pc is not None and pc != 1.0:
        parts.append(
            f"Put/Call={pc:.2f} "
            + ("(peur options — biais contrarian haussier)." if pc > 1.2 else
               "(options calmes).")
        )
    elif pc == 1.0:
        parts.append(
            "Put/Call neutre (1.0) : souvent <b>pas de chaine d'options</b> "
            "Yahoo sur les mid-caps .PA — signal peu fiable titre par titre."
        )

    return " ".join(parts)


@st.cache_data(ttl=600, show_spinner=False)
def score_ticker_opportunity(ticker: str, budget: float, vix: float) -> dict:
    """Score an affordable PEA name for MICRO/STARTER suggestions (0-100)."""
    prices = get_last_prices((ticker,))
    px = prices.get(ticker)
    if not px or px <= 0 or px > budget * 0.98:
        return {
            "ticker": ticker, "price": px or 0.0, "score": 0,
            "reco": "INACCESSIBLE", "why": "Prix hors budget ou indisponible.",
            "kind": "?", "rsi": None, "vs_sma200": None,
        }
    ind = get_indicators(ticker) or {}
    dossier = get_ticker_dossier(ticker)
    is_etf = bool(dossier.get("is_etf") or ticker in (
        _CORE_TICKER, "EWLD.PA", "PAEEM.PA", "ESE.PA", "C50.PA", "PE500.PA",
    ))
    score = 40.0
    reasons: list[str] = []
    rsi = ind.get("rsi")
    close = ind.get("close") or px
    sma5, sma200 = ind.get("sma5"), ind.get("sma200")
    vol = ind.get("vol_ann")

    if is_etf:
        score += 18
        reasons.append("ETF = diversification (mieux qu'1 action seule en MICRO)")
    else:
        score += 4
        reasons.append("Action individuelle — risque titre concentre")

    if rsi is not None:
        if rsi < 30:
            score += 22
            reasons.append(f"RSI {rsi:.0f} survendu (setup MRE)")
        elif rsi < 45:
            score += 12
            reasons.append(f"RSI {rsi:.0f} plutot calme")
        elif rsi > 70:
            score -= 18
            reasons.append(f"RSI {rsi:.0f} surachete — eviter d'acheter")
        else:
            score += 4
            reasons.append(f"RSI {rsi:.0f} neutre")

    vs200 = None
    if sma200 and close:
        vs200 = (close / sma200 - 1) * 100
        if close > sma200:
            score += 14
            reasons.append(f"Au-dessus SMA200 ({vs200:+.1f}%)")
        else:
            score -= 8 if not is_etf else 2
            reasons.append(f"Sous SMA200 ({vs200:+.1f}%)")

    if sma5 and close:
        if close > sma5:
            score += 8
            reasons.append("Momentum court terme OK (Close>SMA5)")
        else:
            score -= 6
            reasons.append("Momentum faible (Close<SMA5)")

    if vol is not None:
        if vol > 45 and not is_etf:
            score -= 10
            reasons.append(f"Vol elevee ({vol:.0f}%)")
        elif vol < 25:
            score += 4

    # Prefer leaving cash runway (cost 8–45% of budget).
    weight = px / budget * 100 if budget else 100
    if 8 <= weight <= 45:
        score += 10
        reasons.append(f"1 part = {weight:.0f}% du cash — laisse un runway")
    elif weight > 70:
        score -= 12
        reasons.append(f"1 part = {weight:.0f}% — trop concentre")

    if vix > _VIX_PANIC and not is_etf:
        score -= 20
        reasons.append("VIX panic — privilegier ETF/cash")

    score = int(max(0, min(100, round(score))))
    if score >= 72:
        reco = "ACHETER"
    elif score >= 55:
        reco = "SURVEILLER"
    elif score >= 40:
        reco = "ATTENDRE"
    else:
        reco = "EVITER"

    return {
        "ticker": ticker,
        "price": float(px),
        "score": score,
        "reco": reco,
        "why": " · ".join(reasons[:4]),
        "kind": "ETF" if is_etf else "Action",
        "rsi": rsi,
        "vs_sma200": vs200,
        "weight_pct": weight,
    }


@st.cache_data(ttl=600, show_spinner=False)
def rank_affordable_alternatives(budget: float, vix: float) -> list[dict]:
    """Rank PEA ETFs + liquid stocks affordable with current cash."""
    universe = [
        # Low-fee / PEA ETFs first (CW8 often unaffordable in MICRO)
        "EWLD.PA", "PAEEM.PA", "ESE.PA", "C50.PA", "PE500.PA", _CORE_TICKER,
        # Liquid large/mid caps
        "STLAP.PA", "ORA.PA", "ENGI.PA", "VIE.PA", "GLE.PA", "ACA.PA",
        "SAN.PA", "TTE.PA", "BNP.PA", "RNO.PA", "SGO.PA", "CAP.PA",
        "AIR.PA", "HO.PA", "ML.PA", "BN.PA", "PUB.PA",
    ]
    rows = [score_ticker_opportunity(t, budget, vix) for t in universe]
    rows = [r for r in rows if r["reco"] != "INACCESSIBLE" and r["price"] > 0]
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows


def suggest_adaptive_portfolio(
    equity: float,
    cash: float,
    vix: float,
    regime: dict,
    pending_df: pd.DataFrame,
    held_tickers: list[str],
) -> dict:
    """Capital-aware suggestions for court / moyen / long horizons."""
    equity = max(float(equity or 0), float(cash or 0), 0.0)
    cash = max(float(cash or 0), 0.0)
    budget = cash if cash > 0 else equity

    candidates = [
        _CORE_TICKER, "EWLD.PA", "PAEEM.PA", "ESE.PA", "C50.PA",
        "SAN.PA", "TTE.PA", "BNP.PA", "GLE.PA", "ACA.PA", "ENGI.PA",
        "ORA.PA", "VIE.PA", "SGO.PA", "CAP.PA", "AIR.PA", "STLAP.PA",
        "RNO.PA", "ML.PA", "HO.PA",
    ]
    pending_tickers: list[str] = []
    if pending_df is not None and not pending_df.empty:
        pending_tickers = [str(t) for t in pending_df["ticker"].tolist() if str(t)]
    prices = get_last_prices(tuple(dict.fromkeys(pending_tickers + candidates)))
    core_px = prices.get(_CORE_TICKER)

    if equity < 200:
        mode = "MICRO"
    elif equity < 800:
        mode = "STARTER"
    elif equity < 3000:
        mode = "BUILD"
    else:
        mode = "FULL"

    ranked = rank_affordable_alternatives(budget, float(vix))

    def _pick_micro_line() -> tuple[str, float, dict] | None:
        if not ranked:
            return None
        best = ranked[0]
        return best["ticker"], float(best["price"]), best

    def _horizon_pack(label: str, lines: list[dict], cash_keep: float, why: str) -> dict:
        for l in lines:
            l["weight_pct"] = (l["cost"] / equity * 100) if equity else 100.0
        return {"label": label, "lines": lines, "cash_keep": cash_keep, "why": why}

    # --- COURT TERME (0–3 mois): best scored affordable + cash runway --------
    court_lines: list[dict] = []
    pick = _pick_micro_line()
    if pick and mode in ("MICRO", "STARTER"):
        t, px, meta = pick
        qty = 1
        cost = qty * px
        court_lines.append({
            "ticker": t, "qty": qty, "price": px, "cost": cost,
            "role": f"Top score {meta.get('score', 0)}/100 · {meta.get('kind')}",
            "why": (
                f"Reco {meta.get('reco')} — {meta.get('why', '')} "
                f"Core {_CORE_TICKER} "
                f"({f'{core_px:.0f} €' if core_px else 'n/a'}) hors budget."
            ),
        })
    court_cash = budget - sum(l["cost"] for l in court_lines)
    court_why = (
        f"<b>Court terme — playbook different du long terme.</b> "
        f"Objectif 0–3 mois : rester liquide et opportuniste. "
        f"1 part max du meilleur score sous budget ({budget:,.0f} €), "
        f"cash ~{court_cash:,.0f} € pour rebondir vite. "
        f"Pas une strategie 'economiser pour CW8' : c'est un ticket tradeable "
        f"maintenant (ETF PEA cheap ou action scoree). VIX={vix:.1f}."
    )

    # --- MOYEN TERME (3–18 mois): Core-first des que possible -----------------
    mid_lines: list[dict] = []
    mid_why = ""
    if core_px and core_px <= budget * 0.98:
        qty = max(int((budget * 0.70) // core_px), 1)
        cost = qty * core_px
        if cost <= budget:
            mid_lines.append({
                "ticker": _CORE_TICKER, "qty": qty, "price": core_px, "cost": cost,
                "role": "Core ETF",
                "why": "Ancre MSCI World PEA — objectif ~70% des que le capital le permet.",
            })
        mid_why = (
            "<b>Moyen terme (3–18 mois)</b> : bascule Core-first des que "
            f"1 part {_CORE_TICKER} est achetable. Les satellites ne viennent "
            "qu'apres, sous budget 30% et VIX OK. Différent du court terme "
            "(qui reste un ticket liquide flexible)."
        )
    else:
        # Medium-term: accumulate via ranked ETFs (not "wait forever for CW8")
        mid_lines = []
        for alt in ranked[:2]:
            if alt["price"] > budget * 0.5:
                continue
            mid_lines.append({
                "ticker": alt["ticker"],
                "qty": 1,
                "price": alt["price"],
                "cost": alt["price"],
                "role": f"Pont moyen terme · score {alt['score']}",
                "why": (
                    f"{alt['reco']} — {alt['why']}. "
                    f"Pont vers Core {_CORE_TICKER} "
                    f"({f'{core_px:.0f} €' if core_px else 'n/a'}) "
                    "sans rester 100% cash."
                ),
            })
            if len(mid_lines) >= 1:
                break
        if not mid_lines:
            mid_lines = list(court_lines)
        mid_why = (
            "<b>Moyen terme</b> : Core encore trop cher — on ne reste pas "
            "inactif : ETF PEA abordable (EWLD/PAEEM/ESE…) comme pont, "
            f"tout en visant {_CORE_TICKER} au prochain depot. "
            "Ce n'est PAS la meme reco que le court terme (plus diversifie, "
            "moins 'ticket trading')."
        )

    # --- LONG TERME (3–10 ans): allocation institutionnelle cible ------------
    long_lines: list[dict] = []
    if core_px:
        # Target allocation in EUR if user had enough capital (illustrative).
        target_eq = max(equity, core_px / 0.70, 5000.0)
        core_budget = target_eq * (0.75 if regime.get("crash") else 0.70)
        qty = max(int(core_budget // core_px), 1)
        long_lines.append({
            "ticker": _CORE_TICKER, "qty": qty, "price": core_px,
            "cost": qty * core_px,
            "role": "Core cible",
            "why": (
                f"Allocation cible long terme sur equity illustre "
                f"~{target_eq:,.0f} \u20ac (pas ton cash actuel)."
            ),
        })
    long_why = (
        f"<b>Long terme (cible institutionnelle)</b> — autre logique : "
        f"~70–75% {_CORE_TICKER}, ≤30% satellites MRE, secteur ≤{_MAX_SECTOR*100:.0f}%, "
        "ligne ≤15%, Smart DCA sous SMA200. "
        "Les tickets court terme (1 action / 1 petit ETF) ne sont PAS la cible "
        "finale : ils sont des etapes. Ce tableau illustre l'allocation une fois "
        "le capital suffisant — pas un ordre a passer aujourd'hui avec 100 €."
    )

    primary = court_lines if mode in ("MICRO", "STARTER") else (
        mid_lines if mid_lines else court_lines
    )
    cash_keep = budget - sum(l["cost"] for l in primary)
    for l in primary:
        l["weight_pct"] = (l["cost"] / equity * 100) if equity else 100.0

    if primary:
        top = primary[0]
        summary = (
            f"Mode <b>{mode}</b> — maintenant : {top['qty']}\u00d7 "
            f"{format_name(top['ticker'])} a {top['price']:.2f} \u20ac "
            f"(~{top['weight_pct']:.0f}% du capital). "
            f"Cash a garder ~{cash_keep:,.0f} \u20ac."
        )
    else:
        summary = (
            f"Mode <b>{mode}</b> — aucun titre liquide fiable sous "
            f"{budget:,.0f} \u20ac. Garde le cash, vise {_CORE_TICKER}."
        )

    mode_why = {
        "MICRO": f"Capital {equity:,.0f} \u20ac : trop faible pour diversifier / acheter le Core.",
        "STARTER": f"Capital {equity:,.0f} \u20ac : 1–2 lignes max, plafonds 15%/25% assouplis.",
        "BUILD": f"Capital {equity:,.0f} \u20ac : construction Core-first.",
        "FULL": f"Capital {equity:,.0f} \u20ac : regles institutionnelles completes.",
    }[mode]
    if vix > _VIX_PANIC:
        mode_why += f" VIX={vix:.1f} > {_VIX_PANIC:.0f} : frein satellite actif."

    return {
        "mode": mode,
        "mode_why": mode_why,
        "lines": primary,
        "cash_keep": cash_keep,
        "summary": summary,
        "have_core": any(l["ticker"] == _CORE_TICKER for l in primary),
        "cash_explain": court_why,
        "alternatives": ranked[:12],
        "horizons": {
            "court": _horizon_pack("Court terme (0–3 mois)", court_lines, court_cash, court_why),
            "moyen": _horizon_pack(
                "Moyen terme (3–18 mois)", mid_lines,
                budget - sum(l["cost"] for l in mid_lines), mid_why,
            ),
            "long": _horizon_pack(
                "Long terme (cible)", long_lines,
                0.0, long_why,
            ),
        },
    }


@st.cache_data(ttl=3600, show_spinner=False)
def get_ticker_dossier(ticker: str) -> dict:
    """Company identity + catalysts + risk events (yfinance + heuristics)."""
    out: dict = {
        "name": format_name(ticker),
        "summary": "",
        "sector": "",
        "industry": "",
        "catalysts": [],
        "risk_events": [],
        "is_etf": False,
    }
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:  # noqa: BLE001
        info = {}
    name = info.get("longName") or info.get("shortName") or short_name(ticker)
    out["name"] = name
    out["sector"] = str(info.get("sector") or "")
    out["industry"] = str(info.get("industry") or "")
    summary = str(info.get("longBusinessSummary") or "")[:700]
    quote_type = str(info.get("quoteType") or "").upper()
    out["is_etf"] = quote_type in ("ETF", "MUTUALFUND") or ticker.endswith(".PA") and (
        "ETF" in name.upper() or "UCITS" in name.upper() or ticker == _CORE_TICKER
    )
    if summary:
        out["summary"] = summary
    elif out["is_etf"] or ticker == _CORE_TICKER:
        out["summary"] = (
            f"{name} est un ETF eligible PEA. Il replique un indice large "
            "(ex. MSCI World pour CW8) au lieu d'un risque entreprise unique. "
            "C'est l'ancre Core du systeme V-Prime."
        )
    else:
        out["summary"] = (
            f"{format_name(ticker)} — fiche qualitative incomplete cote Yahoo. "
            "Consulte Boursorama / le document d'enregistrement universel."
        )

    # Catalysts / risks — sector-aware heuristics + earnings
    sector = (out["sector"] or "").casefold()
    catalysts = [
        "Publication de resultats au-dessus du consensus (EPS / CA)",
        "Guidance relevee ou nouveau contrat significatif",
        "Rachat d'actions / dividende en hausse",
    ]
    risks = [
        "Profit warning ou baisse de guidance",
        "Enquete regulateur / amende majeure",
        "Choc macro (VIX panic) pendant que tu es concentre sur 1 ligne",
    ]
    if "auto" in sector or "consumer cyclical" in sector or "STLAP" in ticker:
        catalysts += ["Rebond volumes Europe/US", "Marges industrielles stabilisees"]
        risks += ["Guerre commerciale / droits de douane", "Retard plateformes EV"]
    if "healthcare" in sector or "SAN.PA" in ticker:
        catalysts += ["Approbation medicament / pipeline"]
        risks += ["Echec essai clinique", "Pression prix medicaments"]
    if out["is_etf"] or ticker == _CORE_TICKER:
        catalysts = [
            "Marche actions mondial en tendance haussiere",
            "DCA discipliné pendant les corrections (Smart DCA)",
            "Euro stable vs panier devise de l'indice",
        ]
        risks = [
            "Krach global prolonge (mais le DCA achete alors plus fort)",
            "Tracking error / frais de l'ETF",
            "Force de l'euro qui pese sur un indice world en devises",
        ]
    out["catalysts"] = catalysts[:5]
    out["risk_events"] = risks[:5]
    return out


@st.cache_data(ttl=1800, show_spinner=False)
def get_etf_card(ticker: str = _CORE_TICKER) -> dict:
    """Key facts for the Core (or any) PEA ETF."""
    dossier = get_ticker_dossier(ticker)
    ind = get_indicators(ticker)
    prices = get_last_prices((ticker,))
    px = prices.get(ticker) or (ind or {}).get("close")
    return {
        "ticker": ticker,
        "name": dossier.get("name") or ticker,
        "summary": dossier.get("summary") or "",
        "price": px,
        "regime": get_core_regime() if ticker == _CORE_TICKER else {},
        "indicators": ind or {},
        "role": (
            "Ancre Core V-Prime (MSCI World PEA). Cible 70–75% de l'equity "
            "des que ton capital permet d'acheter des parts entieres."
            if ticker == _CORE_TICKER else
            "ETF eligible PEA — diversification indicielle."
        ),
    }


@st.cache_data(ttl=1800, show_spinner=False)
def get_monthly_market_news(tickers: tuple[str, ...]) -> list[dict]:
    """Biggest headlines of the month across a watchlist, impact-ranked."""
    bundle = get_general_news_bundle(tickers)
    scored = []
    for n in bundle:
        sc = heuristic_news_score(n.get("title", ""))
        # Light LLM only for top candidates would be slow; heuristic for month pack.
        scored.append({**n, "score": sc, "abs": abs(sc)})
    scored.sort(key=lambda x: x["abs"], reverse=True)
    return scored[:12]


@st.cache_data(ttl=900, show_spinner=False)
def get_sector_performance(
    universe_df: pd.DataFrame, period: str = "1mo"
) -> pd.DataFrame:
    """Average performance by sector over a timeframe."""
    if universe_df is None or universe_df.empty:
        return pd.DataFrame()
    # Sample up to 4 tickers per sector to keep Yahoo calls sane.
    samples: list[str] = []
    for _sector, grp in universe_df.groupby("Sector"):
        samples.extend(grp["Ticker"].head(4).tolist())
    samples = list(dict.fromkeys(samples))[:80]
    perf = get_market_performance(tuple(samples), period=period)
    if perf.empty:
        return pd.DataFrame()
    meta = universe_df.set_index("Ticker")["Sector"].to_dict()
    perf = perf.copy()
    perf["Sector"] = perf["Ticker"].map(meta).fillna("Unknown")
    agg = (perf.groupby("Sector", as_index=False)
           .agg(Perf_moy=("Performance (%)", "mean"),
                Perf_med=("Performance (%)", "median"),
                N=("Ticker", "count"),
                Best=("Performance (%)", "max"),
                Worst=("Performance (%)", "min"))
           .sort_values("Perf_moy", ascending=False))
    return agg


@st.cache_data(ttl=1800, show_spinner=False)
def get_polymarket_macro(limit: int = 8) -> list[dict]:
    """Fetch live macro-relevant Polymarket events (Gamma API, no auth)."""
    try:
        import json
        import urllib.request

        url = (
            "https://gamma-api.polymarket.com/events?"
            "active=true&closed=false&order=volume24hr&ascending=false&limit=50"
        )
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "PEA-Sniper-Terminal/1.0", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            events = json.loads(resp.read().decode("utf-8"))
        if not isinstance(events, list):
            return []

        keys = (
            "recession", "fed", "ecb", "inflation", "tariff", "war", "ukraine",
            "china", "oil", "rate", "gdp", "election", "trump", "europe",
            "france", "germany", "nasdaq", "spx", "crash", "btc", "dollar",
            "le pen", "macron", "yield",
        )
        # Exclude pure sports noise.
        ban = ("euro 2024", "world cup", "mlb", "nba", "nfl", "champions league",
               "olympic", "grand slam", "premier league")
        out: list[dict] = []
        for ev in events:
            title = str(ev.get("title") or ev.get("slug") or "")
            tl = title.casefold()
            if any(b in tl for b in ban):
                continue
            if not any(k in tl for k in keys):
                continue
            markets = ev.get("markets") or []
            yes_p = None
            question = title
            if markets:
                m0 = markets[0]
                question = str(m0.get("question") or title)
                prices = m0.get("outcomePrices")
                if isinstance(prices, str):
                    try:
                        prices = json.loads(prices)
                    except Exception:  # noqa: BLE001
                        prices = None
                if isinstance(prices, (list, tuple)) and prices:
                    try:
                        yes_p = float(prices[0])
                    except Exception:  # noqa: BLE001
                        yes_p = None
            vol = ev.get("volume24hr") or ev.get("volume") or 0
            try:
                vol_f = float(vol)
            except Exception:  # noqa: BLE001
                vol_f = 0.0
            slug = ev.get("slug") or ""
            # Impact hint for PEA
            if yes_p is None:
                impact = "Contexte"
            elif "recession" in tl or "crash" in tl:
                impact = "Risque risk-off" if yes_p > 0.35 else "Tail risk faible"
            elif "fed" in tl or "ecb" in tl or "rate" in tl:
                impact = "Sensibilite taux / valorisations"
            elif "france" in tl or "le pen" in tl or "europe" in tl:
                impact = "Premium politique EU"
            else:
                impact = "Macro general"
            out.append({
                "title": question[:120],
                "yes_prob": yes_p,
                "volume24h": vol_f,
                "impact": impact,
                "url": f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com",
            })
            if len(out) >= limit:
                break
        return out
    except Exception:  # noqa: BLE001
        return []


# =============================================================================
# Header + live ticker tape (streaming)
# =============================================================================
st.markdown(
    "<h1>\U0001F6E1\uFE0F PEA SNIPER TERMINAL "
    "<span style='color:#00FF00; font-size:20px;'>V-PRIME</span></h1>",
    unsafe_allow_html=True,
)

universe_df = load_universe()
# Populate the name lookup with every universe entry (STEP 1.3 coverage).
TICKER_NAMES.update(dict(zip(universe_df["Ticker"], universe_df["Name"])))

# Live streaming ticker tape across the top.
_tape_symbols = ",".join(
    f'{{"proName":"{_tv_symbol(t)}","title":"{short_name(t)}"}}'
    for t in universe_df["Ticker"].head(16)
)
_tape_html = f"""
<div class="tradingview-widget-container">
  <div class="tradingview-widget-container__widget"></div>
  <script type="text/javascript"
    src="https://s3.tradingview.com/external-embedding/embed-widget-ticker-tape.js" async>
  {{"symbols":[{_tape_symbols}],"showSymbolLogo":true,"colorTheme":"dark",
   "isTransparent":true,"displayMode":"adaptive","locale":"fr"}}
  </script>
</div>
"""
components.html(_tape_html, height=80)

portfolio = load_portfolio_state()
if portfolio is None:
    st.warning(
        "\u26A0\uFE0F En attente de l'initialisation des bases de donn\u00e9es "
        "par le Main Scheduler... (lancez `py main_scheduler.py --now`)"
    )
    st.stop()


# =============================================================================
# STEP 2 - Top HUD (with plain-language tooltips)
# =============================================================================
positions = portfolio.positions
invested = sum(p.market_value for p in positions)
unrealized = sum((p.current_price - p.avg_entry_price) * p.qty_shares for p in positions)
unrealized_pct = (unrealized / invested * 100) if invested else 0.0
cash_pct = (portfolio.cash_available / portfolio.total_equity * 100
            if portfolio.total_equity else 0.0)

c1, c2, c3, c4 = st.columns(4)
with c1:
    st.markdown(metric_box(
        "Valeur du Portefeuille", f"{portfolio.total_equity:,.2f} \u20ac",
        sub=f"Investi: {invested:,.2f} \u20ac", accent="", sub_cls="sub-muted",
        help_text="Valeur totale de votre PEA : la somme de vos liquidites et de "
                  "la valeur de marche de toutes vos actions detenues.",
    ), unsafe_allow_html=True)
with c2:
    st.markdown(metric_box(
        "Liquidites (Cash)", f"{portfolio.cash_available:,.2f} \u20ac",
        sub=f"{cash_pct:.1f}% de l'equity", accent="muted", sub_cls="sub-muted",
        help_text="Argent disponible non investi, pret a saisir de nouvelles "
                  "opportunites d'achat.",
    ), unsafe_allow_html=True)
with c3:
    pnl_cls = "sub-green" if unrealized >= 0 else "sub-red"
    st.markdown(metric_box(
        "PnL Latent", f"{unrealized:,.2f} \u20ac", sub=f"{unrealized_pct:+.2f}%",
        accent="" if unrealized >= 0 else "red", sub_cls=pnl_cls,
        help_text="Gains ou pertes virtuels sur les positions actuellement "
                  "detenues, avant de les vendre (non realises).",
    ), unsafe_allow_html=True)
with c4:
    st.markdown(metric_box(
        "Lignes Actives", f"{len(positions)}", sub="Zero Levier Garanti",
        accent="cyan", sub_cls="sub-muted",
        help_text="Nombre de positions distinctes en portefeuille. Le systeme "
                  "n'utilise jamais d'effet de levier (pas de marge).",
    ), unsafe_allow_html=True)


# =============================================================================
# Risk / Macro HUD (VIX, regime, satellite budget, sector concentration)
# =============================================================================
vix = get_vix()
vix_panic = vix > _VIX_PANIC
regime = get_core_regime()

satellite_value = sum(p.market_value for p in positions if p.ticker != _CORE_TICKER)
sat_budget_eur = _SAT_BUDGET * portfolio.total_equity if portfolio.total_equity else 0.0
sat_used_pct = (satellite_value / sat_budget_eur * 100) if sat_budget_eur else 0.0

sector_weights: dict[str, float] = {}
for p in positions:
    sector_weights[p.sector] = sector_weights.get(p.sector, 0.0) + p.market_value
max_sector, max_sector_val = ("-", 0.0)
if sector_weights and portfolio.total_equity:
    max_sector = max(sector_weights, key=sector_weights.get)
    max_sector_val = sector_weights[max_sector] / portfolio.total_equity * 100

r1, r2, r3, r4 = st.columns(4)
with r1:
    vsub = ("\U0001F6A8 PANIC - achats satellites geles" if vix_panic
            else f"Calme (seuil {_VIX_PANIC:.0f})")
    st.markdown(metric_box(
        "Volatilite (VIX)", f"{vix:.1f}", sub=vsub,
        accent="red" if vix_panic else "", sub_cls="sub-red" if vix_panic else "sub-green",
        help_text="L'indice de la peur. Au-dessus de 30, le marche panique et le "
                  "bot bloque les nouveaux achats risques pour proteger le capital.",
    ), unsafe_allow_html=True)
with r2:
    if regime:
        crash = regime["crash"]
        rsub = ("\U0001F534 SOUS SMA200 - DCA agressif" if crash
                else "\U0001F7E2 SUR SMA200 - DCA standard")
        st.markdown(metric_box(
            f"Regime Core ({_CORE_TICKER})", f"{regime['gap_pct']:+.1f}%", sub=rsub,
            accent="red" if crash else "", sub_cls="sub-red" if crash else "sub-green",
            help_text="Indique si le marche global est en tendance haussiere "
                      "(au-dessus de sa moyenne 200 jours) ou en crise (en dessous). "
                      "En crise, le bot accumule l'ETF Monde plus agressivement.",
        ), unsafe_allow_html=True)
    else:
        st.markdown(metric_box(
            f"Regime Core ({_CORE_TICKER})", "n/a", sub="Donnees indisponibles",
            accent="muted", sub_cls="sub-muted",
            help_text="Regime du marche global (prix vs moyenne 200 jours). "
                      "Donnees temporairement indisponibles.",
        ), unsafe_allow_html=True)
with r3:
    over = sat_used_pct > 100
    ssub = f"{satellite_value:,.0f} / {sat_budget_eur:,.0f} \u20ac (max {_SAT_BUDGET*100:.0f}%)"
    st.markdown(metric_box(
        "Budget Satellite Utilise", f"{sat_used_pct:.0f}%", sub=ssub,
        accent="red" if over else "cyan", sub_cls="sub-red" if over else "sub-muted",
        help_text="Capital alloue aux actions individuelles (max 30% du "
                  "portefeuille total) pour chercher de la surperformance. Le "
                  "reste est investi dans l'ETF Monde (le Coeur du portefeuille).",
    ), unsafe_allow_html=True)
with r4:
    breach = max_sector_val > _MAX_SECTOR * 100
    st.markdown(metric_box(
        "Concentration Sectorielle Max", f"{max_sector_val:.0f}%",
        sub=f"{max_sector} (limite {_MAX_SECTOR*100:.0f}%)",
        accent="red" if breach else "", sub_cls="sub-red" if breach else "sub-muted",
        help_text="Poids du secteur le plus represente. Le systeme interdit de "
                  "depasser cette limite pour eviter d'etre trop expose a un "
                  "seul theme (diversification imposee).",
    ), unsafe_allow_html=True)

# --- Sidebar: settings & controls -------------------------------------------
with st.sidebar:
    st.markdown("### \u2699\uFE0F Parametres")
    auto_refresh = st.checkbox("Rafraichissement auto", value=False)
    refresh_secs = st.slider("Intervalle (s)", 30, 600, 120, 30,
                             disabled=not auto_refresh)
    if st.button("\U0001F504 Vider le cache & recharger", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    st.markdown("---")
    st.markdown("### \U0001F4CA Etat Systeme")
    st.metric("Univers", f"{len(universe_df)} titres",
              help="Nombre total d'actions/ETF eligibles PEA suivis par le bot.")
    st.metric("Derniere MAJ", portfolio.last_updated.strftime("%d/%m %H:%M"),
              help="Horodatage de la derniere passe du Main Scheduler ayant "
                   "actualise les cours et l'equity.")
    st.caption(
        "Amorcer le capital :\n\n`python seed_account.py --cash 10000`\n\n"
        "Lancer une passe :\n\n`python main_scheduler.py --now`"
    )
    if auto_refresh:
        st.caption(f"\u23F1\uFE0F Auto-refresh dans {refresh_secs}s")

st.write("---")


# =============================================================================
# Tabs
# =============================================================================
tab_gen, tab_pf, tab_mkt, tab_uni, tab_arch = st.tabs([
    "📊 General & Signaux",
    "🎯 Portefeuille & Allocation",
    "🌍 Exploration",
    "📋 Univers Complet",
    "🧠 Architecture & Documentation",
])

# --- Tab: General + Signals --------------------------------------------------
with tab_gen:
    st.markdown(
        "<div class='info-text'>Briefing + registre des signaux + "
        "<b>suggestion de portefeuille adaptative</b> selon ton capital. "
        "Aucun ordre n'est envoye depuis ici — Discord reste le copilot.</div>",
        unsafe_allow_html=True,
    )

    held_tickers = [p.ticker for p in positions]
    blue_chips = ["MC.PA", "OR.PA", "AI.PA", "RMS.PA", "SAN.PA",
                  "TTE.PA", "BNP.PA", "AIR.PA", _CORE_TICKER]
    watch = tuple(dict.fromkeys(held_tickers + blue_chips))[:14]

    pending_gen = load_signals(("PENDING",))
    suggestion = suggest_adaptive_portfolio(
        float(portfolio.total_equity),
        float(portfolio.cash_available),
        float(vix),
        regime or {},
        pending_gen,
        held_tickers,
    )

    st.markdown("#### 🎯 Meilleur portefeuille suggere (adaptatif)")
    st.markdown(
        f"<div class='eli5'>{suggestion.get('summary', '')}<br><br>"
        f"<b style='color:{_AMBER};'>Pourquoi ce mode ({suggestion.get('mode')}) :</b> "
        f"{suggestion.get('mode_why', '')}<br><br>"
        f"{suggestion.get('cash_explain', '')}</div>",
        unsafe_allow_html=True,
    )
    sug_lines = suggestion.get("lines") or []
    if sug_lines:
        sdisp = pd.DataFrame([{
            "Titre": format_name(l["ticker"]),
            "Role": l["role"],
            "Qte": l["qty"],
            "Cours": f"{l['price']:,.2f} €",
            "Cout": f"{l['cost']:,.2f} €",
            "Poids": f"{l['weight_pct']:.0f}%",
            "Justification": l["why"][:160],
        } for l in sug_lines])
        st.plotly_chart(
            dark_table(sdisp, height=min(280, 60 + 36 * len(sdisp)),
                       col_widths=[2, 1.2, 0.5, 0.9, 0.9, 0.6, 2.8]),
            width="stretch",
            key="gen_primary_suggestion_table",
        )
    else:
        st.warning(suggestion.get("summary", "Pas de suggestion."))

    # Ranked alternatives with score + reco (fixes "only one option" feel)
    alts = suggestion.get("alternatives") or []
    st.markdown("##### Classement des alternatives achetable (score 0–100)")
    st.markdown(
        "<div class='info-text'>ETF PEA (EWLD, PAEEM, ESE, C50…) vs actions "
        "liquides. Score = RSI + tendance SMA200 + momentum + fit cash + "
        "bonus diversification ETF. <b>ACHETER / SURVEILLER / ATTENDRE / EVITER</b>. "
        "Toujours 1 part max en MICRO + cash runway.</div>",
        unsafe_allow_html=True,
    )
    if alts:
        adisp = pd.DataFrame([{
            "Rang": i + 1,
            "Titre": format_name(a["ticker"]),
            "Type": a.get("kind", "?"),
            "Cours": f"{a['price']:,.2f} €",
            "Score": f"{a['score']}/100",
            "Reco": a.get("reco", ""),
            "RSI": f"{a['rsi']:.0f}" if a.get("rsi") is not None else "—",
            "vs SMA200": (
                f"{a['vs_sma200']:+.1f}%" if a.get("vs_sma200") is not None else "—"
            ),
            "Poids 1 part": f"{a.get('weight_pct', 0):.0f}%",
            "Pourquoi": str(a.get("why", ""))[:110],
        } for i, a in enumerate(alts)])
        reco_colors = []
        for a in alts:
            r = a.get("reco")
            reco_colors.append(
                _NEON if r == "ACHETER" else
                _AMBER if r == "SURVEILLER" else
                _CYAN if r == "ATTENDRE" else _RED
            )
        st.plotly_chart(
            dark_table(adisp, height=min(520, 56 + 32 * len(adisp)),
                       font_color_map={"Reco": reco_colors, "Score": reco_colors},
                       col_widths=[0.5, 2.0, 0.7, 0.8, 0.8, 1.0, 0.6, 0.9, 0.8, 2.4]),
            width="stretch",
            key="gen_alternatives_ranking_table",
        )
    else:
        st.caption("Aucune alternative liquide sous ton cash actuel.")

    horizons = suggestion.get("horizons") or {}
    if horizons:
        with st.expander("Horizons d'allocation (court / moyen / long)", expanded=False):
            h_choice = st.radio(
                "Horizon",
                ["court", "moyen", "long"],
                format_func=lambda k: (horizons.get(k) or {}).get("label", k),
                horizontal=True,
                key="gen_horizon_radio",
            )
            hz = horizons.get(h_choice) or {}
            st.markdown(hz.get("why", ""), unsafe_allow_html=True)
            hlines = hz.get("lines") or []
            if hlines:
                hdf = pd.DataFrame([{
                    "Titre": format_name(l["ticker"]),
                    "Role": l.get("role", ""),
                    "Qte": l["qty"],
                    "Cours": f"{l['price']:,.2f} €",
                    "Cout": f"{l['cost']:,.2f} €",
                    "Note": str(l.get("why", ""))[:140],
                } for l in hlines])
                st.plotly_chart(
                    dark_table(hdf, height=min(260, 56 + 34 * len(hdf)),
                               col_widths=[2, 1.1, 0.5, 0.9, 0.9, 2.6]),
                    width="stretch",
                    key=f"gen_horizon_table_{h_choice}",
                )
            else:
                st.caption("Rien d'achetable sur cet horizon avec le cash actuel.")
            if h_choice != "long":
                st.caption(f"Cash restant illustre ~{hz.get('cash_keep', 0):,.0f} €")

    # Core ETF snapshot
    etf = get_etf_card(_CORE_TICKER)
    with st.expander(f"📦 Fiche ETF Core — {etf.get('name', _CORE_TICKER)}", expanded=False):
        st.markdown(
            f"<div class='info-text'><b>{etf.get('role')}</b><br>"
            f"{etf.get('summary', '')[:500]}</div>",
            unsafe_allow_html=True,
        )
        ec1, ec2, ec3 = st.columns(3)
        px = etf.get("price")
        ec1.metric("Cours", f"{px:,.2f} €" if px else "n/a")
        reg = etf.get("regime") or {}
        ec2.metric("vs SMA200", f"{reg.get('gap_pct', 0):+.1f}%" if reg else "n/a")
        ec3.metric("Part entiere requise", f"{px:,.0f} €" if px else "n/a",
                   help="PEA = actions entieres. Sous ce montant, pas de Core.")

    st.markdown("---")
    recos = build_recommendations(portfolio, pending_gen, vix, regime or {})
    g1, g2 = st.columns([1.15, 1])
    with g1:
        st.markdown("#### 📌 Recommandations actuelles")
        if not recos:
            st.caption("Aucune recommandation urgente.")
        for r in recos:
            accent = _RED if r["prio"] == 1 else (_AMBER if r["prio"] == 2 else _CYAN)
            st.markdown(
                f"<div style='background:#0A0A0A;padding:10px 12px;margin-bottom:8px;"
                f"border-left:4px solid {accent};border:1px solid #222;'>"
                f"<b style='color:{_WHITE};'>{r['title']}</b>"
                f"<div style='color:#D0D0D0;font-size:13px;margin-top:6px;"
                f"line-height:1.4;'><b style='color:{_AMBER};'>Justification :</b> "
                f"{r['why']}</div></div>",
                unsafe_allow_html=True,
            )
    with g2:
        st.markdown("#### 🌍 Briefing geopolitique / macro")
        with st.spinner("Briefing macro…"):
            _head_preview = tuple(
                n.get("title", "") for n in get_general_news_bundle(watch)[:8]
            )
            brief = get_geopolitical_brief(float(vix), _head_preview)
        st.markdown(
            f"<div style='background:#0A0A0A;padding:14px;border:1px solid #222;"
            f"color:#E8E8E8;line-height:1.55;font-size:14px;'>{brief}</div>",
            unsafe_allow_html=True,
        )

    st.markdown("---")
    st.markdown("#### ⚡ Signaux & Registre")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("##### En attente (Discord)")
        pending = pending_gen
        if pending.empty:
            st.info(
                "Aucun signal aujourd'hui. Le marche ne presente pas "
                "d'opportunites asymetriques selon nos filtres, ou la "
                "volatilite est trop elevee."
            )
        else:
            disp = pd.DataFrame({
                "Titre": [format_name(t) for t in pending["ticker"]],
                "Type": pending["signal_type"],
                "Score": [f"{s:.1f}" for s in pending["score"]],
                "Raison": pending["reason"].fillna(""),
                "Date": [str(x)[:16] for x in pending["created_at"]],
            })
            st.plotly_chart(
                dark_table(disp, height=320,
                           font_color_map={"Score": [_NEON] * len(disp)},
                           col_widths=[2, 0.8, 0.7, 2.4, 1.2]),
                width="stretch",
                key="gen_pending_signals_table",
            )
    with col2:
        st.markdown("##### Historique (20 derniers)")
        hist = load_signals(("EXECUTED", "REVOKED", "REJECTED", "EXPIRED"), limit=20)
        if hist.empty:
            st.info("Aucun historique disponible.")
        else:
            status_color = {"EXECUTED": _NEON, "REVOKED": _RED,
                            "REJECTED": _MUTED, "EXPIRED": _AMBER}
            statut_colors = [status_color.get(s, _WHITE) for s in hist["status"]]
            disp = pd.DataFrame({
                "Titre": [format_name(t) for t in hist["ticker"]],
                "Statut": hist["status"],
                "Type": hist["signal_type"],
                "Score": [f"{s:.1f}" for s in hist["score"]],
                "Date": [str(x)[:16] for x in hist["created_at"]],
            })
            st.plotly_chart(
                dark_table(disp, height=320,
                           font_color_map={"Statut": statut_colors},
                           col_widths=[2, 1.1, 0.9, 0.7, 1.2]),
                width="stretch",
                key="gen_hist_signals_table",
            )
    st.markdown("---")
    p1, p2 = st.columns(2)
    with p1:
        st.markdown("#### 📈 Top / Flop (1 mois)")
        perf_watch = get_market_performance(watch, period="1mo")
        if perf_watch.empty or "Performance (%)" not in perf_watch.columns:
            st.caption("Performances indisponibles.")
        else:
            pf = perf_watch.copy()
            pf["Titre"] = [format_name(t) for t in pf["Ticker"]]
            top = pf.nlargest(5, "Performance (%)")
            # Exclusive flop: exclude tickers already in Top, require strictly worse.
            flop_pool = pf[~pf["Ticker"].isin(top["Ticker"])]
            flop = flop_pool.nsmallest(5, "Performance (%)")
            tcol, fcol = st.columns(2)
            with tcol:
                st.caption("Top")
                disp_t = pd.DataFrame({
                    "Titre": top["Titre"],
                    "Perf": [f"{v:+.1f}%" for v in top["Performance (%)"]],
                })
                st.plotly_chart(
                    dark_table(disp_t, height=220,
                               font_color_map={"Perf": [_NEON] * len(disp_t)},
                               col_widths=[2.2, 0.8]),
                    width="stretch",
                    key="gen_top_perf_table",
                )
            with fcol:
                st.caption("Flop")
                disp_f = pd.DataFrame({
                    "Titre": flop["Titre"],
                    "Perf": [f"{v:+.1f}%" for v in flop["Performance (%)"]],
                })
                st.plotly_chart(
                    dark_table(disp_f, height=220,
                               font_color_map={"Perf": [_RED] * len(disp_f)},
                               col_widths=[2.2, 0.8]),
                    width="stretch",
                    key="gen_flop_perf_table",
                )
    with p2:
        st.markdown("#### 📅 Evenements a venir")
        events = get_earnings_events(watch)
        if not events:
            st.caption("Aucun calendrier earnings detecte (yfinance).")
        else:
            edf = pd.DataFrame([{
                "Titre": format_name(e["ticker"]),
                "Evenement": e["event"],
                "Date": e["date"],
            } for e in events])
            st.plotly_chart(
                dark_table(edf, height=220), width="stretch",
                key="gen_earnings_table",
            )
    st.markdown("---")
    st.markdown("#### 📰 Actualites (impact marche)")
    st.markdown(
        "<div class='info-text'>Une seule liste dedupliquee, classee par "
        "impact. Contexte seulement — jamais un trigger d'ordre.</div>",
        unsafe_allow_html=True,
    )
    news_bundle = get_general_news_bundle(watch)
    score_gen = st.checkbox(
        "Scorer les news (IA + mots-cles)",
        value=False,
        key="gen_score_news",
        help="Impact FORT/MOYEN/FAIBLE. Cache 1h. Decoche = heuristique rapide.",
    )
    if news_bundle:
        if score_gen:
            with st.spinner("Notation des actualites…"):
                scored_bundle = [
                    (n, score_news_with_llm(n.get("ticker", ""), n.get("title", "")))
                    for n in news_bundle
                ]
        else:
            scored_bundle = [
                (n, heuristic_news_score(n.get("title", ""))) for n in news_bundle
            ]
        scored_bundle.sort(key=lambda x: abs(x[1]), reverse=True)
        nc1, nc2 = st.columns(2)
        for i, (n, sc) in enumerate(scored_bundle[:12]):
            with (nc1 if i % 2 == 0 else nc2):
                render_news_card(n.get("ticker", ""), n, sc)
    else:
        st.caption("Aucune actualite recente sur la watchlist.")

# --- Tab: Portfolio ----------------------------------------------------------
with tab_pf:
    st.markdown(
        "<div class='info-text'>Decomposition de l'exposition sectorielle. "
        "En capital eleve, le risque V-Prime limite a 25% / secteur et 15% / "
        "ligne. En micro-PEA ces plafonds sont volontairement assouplis "
        "(voir suggestion dans General).</div>",
        unsafe_allow_html=True,
    )

    # --- Equity curve (top of Portefeuille) ---------------------------------
    st.markdown("#### 📈 Courbe de Performance (Equity Curve)")
    eq_curve = load_equity_curve()
    if eq_curve is None or eq_curve.empty or "equity" not in eq_curve.columns:
        st.info(
            "Pas encore d'historique d'equity. La courbe se construit a chaque "
            "``update_portfolio`` (snapshot journalier dans ``portfolio_history``)."
        )
    else:
        eq = eq_curve.copy()
        eq["date"] = pd.to_datetime(eq["date"], errors="coerce")
        eq = eq.dropna(subset=["date", "equity"]).sort_values("date")
        if eq.empty:
            st.info("Historique equity vide apres nettoyage.")
        else:
            y_min = float(eq["equity"].min())
            y_max = float(eq["equity"].max())
            pad = max((y_max - y_min) * 0.08, abs(y_max) * 0.01, 1.0)
            fig_eq = pex.area(
                eq,
                x="date",
                y="equity",
                labels={"date": "Date", "equity": "Equity (€)"},
            )
            fig_eq.update_traces(
                line=dict(color="#00FF00", width=2),
                fill="tozeroy",
                fillcolor="rgba(0, 255, 0, 0.25)",
            )
            fig_eq.update_layout(
                paper_bgcolor=_BG,
                plot_bgcolor=_BG,
                font=dict(family="Courier New", color=_WHITE),
                margin=dict(t=20, l=40, r=20, b=40),
                height=320,
                xaxis=dict(gridcolor="#222", showgrid=True),
                yaxis=dict(
                    gridcolor="#222",
                    showgrid=True,
                    range=[y_min - pad, y_max + pad],
                    title="Equity (€)",
                ),
                showlegend=False,
            )
            st.plotly_chart(fig_eq, width="stretch", key="pf_equity_curve")
            if compute_equity_metrics is not None:
                m = compute_equity_metrics(eq)
                c1, c2, c3, c4, c5 = st.columns(5)

                def _pct(x):
                    return "—" if x is None else f"{x * 100:+.1f}%"

                def _num(x):
                    return "—" if x is None else f"{x:.2f}"

                c1.metric("Total return", _pct(m.get("total_return")))
                c2.metric("CAGR", _pct(m.get("cagr")))
                c3.metric("Max DD", _pct(m.get("max_drawdown")))
                c4.metric("Sharpe", _num(m.get("sharpe")))
                c5.metric("Sortino", _num(m.get("sortino")))
                st.caption(
                    f"{m.get('n_points', 0)} point(s) · "
                    "métriques partagées (`equity_metrics`) — mêmes formules "
                    "que le futur backtester."
                )

    if not positions:
        st.info("⏸️ Le portefeuille est actuellement 100% en "
                "liquidites. Aucune position ouverte : le capital attend une "
                "opportunite validee par les filtres mathematiques.")
    else:
        rows = [{
            "Ticker": p.ticker, "Secteur": p.sector, "Qte": p.qty_shares,
            "PRU": p.avg_entry_price, "Cours": p.current_price,
            "Valeur": p.market_value, "Poids": 0.0,
            "PnL": p.unrealized_pnl_pct * 100,
        } for p in positions]
        dfp = pd.DataFrame(rows)
        dfp["Poids"] = dfp["Valeur"] / portfolio.total_equity * 100

        sun = dfp[["Secteur", "Ticker", "Valeur", "PnL"]].copy()
        sun["Titre"] = [short_name(t) for t in sun["Ticker"]]
        if portfolio.cash_available > 0:
            sun = pd.concat([sun, pd.DataFrame([{
                "Secteur": "Liquidites", "Ticker": "CASH", "Titre": "Liquidites",
                "Valeur": portfolio.cash_available, "PnL": 0.0}])],
                ignore_index=True)

        fig = pex.sunburst(sun, path=["Secteur", "Titre"], values="Valeur",
                          color="PnL", color_continuous_scale=_DIVERGE,
                          color_continuous_midpoint=0)
        fig.update_layout(paper_bgcolor=_BG, plot_bgcolor=_BG,
                          font=dict(family="Courier New", color=_WHITE),
                          margin=dict(t=10, l=0, r=0, b=0), height=430)
        fig.update_traces(insidetextfont=dict(color=_WHITE, family="Courier New"),
                          marker=dict(line=dict(color=_BG, width=1)))

        col_chart, col_table = st.columns([1, 1.4])
        with col_chart:
            st.plotly_chart(fig, width="stretch")
        with col_table:
            pnl_colors = [_NEON if v >= 0 else _RED for v in dfp["PnL"]]
            disp = pd.DataFrame({
                "Titre": [format_name(t) for t in dfp["Ticker"]],
                "Secteur": dfp["Secteur"],
                "Qte": [f"{q:g}" for q in dfp["Qte"]],
                "PRU": [f"{v:,.2f} €" for v in dfp["PRU"]],
                "Cours": [f"{v:,.2f} €" for v in dfp["Cours"]],
                "Valeur": [f"{v:,.2f} €" for v in dfp["Valeur"]],
                "Poids": [f"{v:.1f}%" for v in dfp["Poids"]],
                "PnL": [f"{v:+.2f}%" for v in dfp["PnL"]],
            })
            st.plotly_chart(
                dark_table(disp, height=430, font_color_map={"PnL": pnl_colors},
                           col_widths=[2.2, 1.4, 0.7, 1, 1, 1.2, 0.8, 0.9]),
                width="stretch")

    st.markdown("---")
    with st.expander("✏️ Ajuster le wallet (cash & positions)", expanded=False):
        st.markdown(
            "<div class='info-text'>Modifie le cash et les lignes pour coller "
            "a ton PEA reel. Ecriture directe dans SQLite.</div>",
            unsafe_allow_html=True,
        )
        edit_cash = st.number_input(
            "Cash disponible (€)",
            min_value=0.0,
            value=float(portfolio.cash_available),
            step=10.0,
            key="wallet_cash",
        )
        base_rows = [{
            "Ticker": p.ticker,
            "Secteur": p.sector,
            "Qte": int(p.qty_shares),
            "PRU": float(p.avg_entry_price),
            "Cours": float(p.current_price),
        } for p in positions] or [{
            "Ticker": "", "Secteur": "Unknown", "Qte": 0, "PRU": 0.0, "Cours": 0.0,
        }]
        edited = st.data_editor(
            pd.DataFrame(base_rows),
            num_rows="dynamic",
            width="stretch",
            key="wallet_editor",
            column_config={
                "Ticker": st.column_config.TextColumn("Ticker Yahoo", required=False),
                "Secteur": st.column_config.TextColumn("Secteur"),
                "Qte": st.column_config.NumberColumn("Qte", min_value=0, step=1),
                "PRU": st.column_config.NumberColumn("PRU €", min_value=0.0,
                                                    format="%.4f"),
                "Cours": st.column_config.NumberColumn("Cours €", min_value=0.0,
                                                      format="%.4f"),
            },
        )
        c_save, c_hint = st.columns([1, 2])
        with c_save:
            if st.button("Enregistrer le wallet", type="primary",
                         width="stretch", key="save_wallet_btn"):
                err = save_wallet(float(edit_cash), edited)
                if err:
                    st.error(f"Echec : {err}")
                else:
                    st.success("Wallet enregistre. Rechargement…")
                    st.rerun()
        with c_hint:
            st.caption(
                "Ticker Yahoo (ex. MC.PA). Qte=0 pour retirer une ligne."
            )

# --- Tab: Exploration (market + ticker radar) --------------------------------
with tab_mkt:
    st.markdown(
        "<div class='info-text'>Exploration marche (top/flop univers) + "
        "<b>fiche ticker</b> : graphique plein ecran, analyse technique "
        "expliquee, actualites, insiders, Polymarket macro.</div>",
        unsafe_allow_html=True,
    )

    # Prefer liquid mid/large names — exclude microcaps/pennies from scan defaults.
    liquid_scan = list(dict.fromkeys(
        [p.ticker for p in positions]
        + ["MC.PA", "OR.PA", "AI.PA", "RMS.PA", "SAN.PA", "TTE.PA", "BNP.PA",
           "AIR.PA", "SU.PA", "EL.PA", "CS.PA", "DG.PA", "SAF.PA", "KER.PA",
           "STLAP.PA", "RNO.PA", "ORA.PA", "ENGI.PA", "CAP.PA", "DSY.PA",
           "HO.PA", "ML.PA", "SGO.PA", "GLE.PA", "ACA.PA", "VIE.PA", "PUB.PA",
           "BN.PA", "RI.PA", "EWLD.PA", "PAEEM.PA", "ESE.PA", "C50.PA",
           _CORE_TICKER]
    ))
    # Do NOT pull random sector samples (they inject illiquid AL* pennies).
    scan_tickers = tuple(
        t for t in liquid_scan
        if t == _CORE_TICKER or t in set(universe_df["Ticker"])
    )

    all_tickers = scan_tickers if scan_tickers else tuple(universe_df["Ticker"].head(40))
    mode = st.radio("Mode d'intervalle", ["Prereglage", "Plage personnalisee"],
                    horizontal=True, key="mkt_mode")

    if mode == "Prereglage":
        period_map = {"1 Semaine": "5d", "1 Mois": "1mo", "3 Mois": "3mo",
                      "6 Mois": "6mo", "1 An": "1y", "2 Ans": "2y", "5 Ans": "5y"}
        label = st.select_slider("Intervalle d'analyse", list(period_map.keys()),
                                 value="1 Mois")
        perf = get_market_performance(all_tickers, period=period_map[label])
        interval_label = label
        period_key = period_map[label]
        d_start = d_end = None
    else:
        cA, cB = st.columns(2)
        with cA:
            d_start = st.date_input("Debut", value=date.today() - timedelta(days=90),
                                    max_value=date.today())
        with cB:
            d_end = st.date_input("Fin", value=date.today(), max_value=date.today())
        perf = get_market_performance(all_tickers, period=None,
                                      start=d_start.isoformat(), end=d_end.isoformat())
        interval_label = f"{d_start.isoformat()} → {d_end.isoformat()}"
        period_key = None

    if perf.empty:
        st.error("Impossible de recuperer les donnees de marche pour cet intervalle.")
    else:
        # Drop near-zero noise AND illiquid pennies (price < 2 EUR).
        perf = perf[
            (perf["Performance (%)"].abs() > 0.05)
            & (perf["Current Price"] >= 2.0)
        ].copy()
        if perf.empty:
            st.warning("Pas assez de variations significatives sur l'intervalle.")
        else:
            best, worst = perf.iloc[0], perf.iloc[-1]
            c1, c2 = st.columns(2)
            with c1:
                st.success(f"🟢 **MEILLEURE PERFORMANCE** · {interval_label}")
                st.metric(format_name(best["Ticker"]), f"{best['Current Price']:.2f} €",
                          f"{best['Performance (%)']:+.2f}%")
            with c2:
                st.error("🔴 **PIRE PERFORMANCE** (candidat Mean-Reversion)")
                st.metric(format_name(worst["Ticker"]), f"{worst['Current Price']:.2f} €",
                          f"{worst['Performance (%)']:+.2f}%")

            st.markdown("#### Classement (top & flop liquides)")
            show = pd.concat([perf.head(12), perf.tail(12)]).drop_duplicates("Ticker")
            show = show.sort_values("Performance (%)", ascending=True)
            show["Label"] = [f"{short_name(t)} ({t})" for t in show["Ticker"]]
            bar = pex.bar(
                show, x="Performance (%)", y="Label", orientation="h",
                color="Performance (%)", color_continuous_scale=_DIVERGE,
                color_continuous_midpoint=0,
                hover_data={"Current Price": ":.2f", "Ticker": True, "Label": False},
            )
            _style_dark_fig(bar, height=max(420, 22 * len(show)))
            bar.update_layout(margin=dict(t=10, l=0, r=0, b=0),
                              coloraxis_showscale=False,
                              yaxis_title="", xaxis_title=f"Perf % · {interval_label}")
            st.plotly_chart(bar, width="stretch")

            movers = list(perf["Ticker"].head(4)) + list(perf["Ticker"].tail(4))
            movers = tuple(dict.fromkeys(movers))
            if period_key:
                norm = get_normalized_prices(movers, period_key, None, None)
            else:
                norm = get_normalized_prices(
                    movers, None, d_start.isoformat(), d_end.isoformat()
                )
            st.markdown("#### Trajectoires rebasees a 100 (top 4 + flop 4)")
            if norm.empty:
                st.caption("Trajectoires indisponibles.")
            else:
                line = go.Figure()
                for i, c in enumerate(norm.columns):
                    line.add_trace(go.Scatter(
                        x=norm.index, y=norm[c], name=format_name(c), mode="lines",
                        line=dict(width=2.4,
                                  color=_BRIGHT_SERIES[i % len(_BRIGHT_SERIES)])))
                line.add_hline(y=100, line_dash="dot", line_color=_MUTED)
                _style_dark_fig(line, height=420)
                line.update_layout(margin=dict(t=10, l=0, r=10, b=0),
                                   legend=dict(orientation="h", y=1.12))
                line.update_xaxes(rangeslider_visible=True, gridcolor=_GRID)
                st.plotly_chart(line, width="stretch")

            with st.expander("Table complete du scan liquide", expanded=False):
                perf_colors = [_NEON if v >= 0 else _RED for v in perf["Performance (%)"]]
                disp = pd.DataFrame({
                    "Titre": [format_name(t) for t in perf["Ticker"]],
                    "Debut": [f"{v:,.2f} €" for v in perf["Start Price"]],
                    "Actuel": [f"{v:,.2f} €" for v in perf["Current Price"]],
                    "Perf": [f"{v:+.2f}%" for v in perf["Performance (%)"]],
                })
                st.plotly_chart(
                    dark_table(disp, height=420,
                               font_color_map={"Perf": perf_colors},
                               col_widths=[2.4, 1, 1, 0.9]),
                    width="stretch")

    # ========== Fiche ticker (ex-Radar) =====================================
    st.markdown("---")
    st.markdown("### 📡 Fiche ticker — graphique & actualites")

    held = [p.ticker for p in positions]
    options = sorted(set(held) | set(universe_df["Ticker"]))
    default_idx = options.index(held[0]) if held and held[0] in options else 0
    # Prefer worst performer as default when no holdings (mean-reversion lens)
    if not held and not perf.empty:
        w = str(perf.iloc[-1]["Ticker"])
        if w in options:
            default_idx = options.index(w)
    selected = st.selectbox(
        "Actif a analyser", options, index=default_idx,
        format_func=format_name, key="explore_ticker",
    )
    tv = _tv_symbol(selected)

    dossier = get_ticker_dossier(selected)
    st.markdown(
        f"<div class='eli5'><b style='color:{_CYAN};'>Qui est {dossier.get('name')} ?</b><br>"
        f"{dossier.get('summary', '')}<br>"
        f"<span style='color:{_MUTED};'>"
        f"Secteur: {dossier.get('sector') or 'n/a'} · "
        f"Industrie: {dossier.get('industry') or 'n/a'}"
        f"{' · ETF' if dossier.get('is_etf') else ''}</span></div>",
        unsafe_allow_html=True,
    )
    cat1, cat2 = st.columns(2)
    with cat1:
        st.markdown("**News / catalyseurs qui aideraient**")
        for c in dossier.get("catalysts") or []:
            st.markdown(f"- {c}")
    with cat2:
        st.markdown("**Evenements a surveiller (ne pas vouloir)**")
        for r in dossier.get("risk_events") or []:
            st.markdown(f"- {r}")

    ind = get_indicators(selected)
    alpha = get_alpha_signals(selected)
    bprofile = get_bourso_profile(selected)

    # Profile + indicators as full metric boxes (no truncation)
    mrow1 = st.columns(4)
    with mrow1[0]:
        if ind:
            st.markdown(metric_box(
                "Cours", f"{ind['close']:.2f} €",
                sub=f"{ind['chg_1d']:+.2f}% (1j) · {ind['chg_5d']:+.2f}% (5j)",
                help_text="Dernier cours et variations recentes.",
            ), unsafe_allow_html=True)
        else:
            st.markdown(metric_box("Cours", "n/a", sub="Donnees manquantes",
                                   accent="muted"), unsafe_allow_html=True)
    with mrow1[1]:
        rsi = (ind or {}).get("rsi")
        rsi_state = ("Survendu" if rsi is not None and rsi < 30 else
                     "Surachete" if rsi is not None and rsi > 70 else "Neutre")
        st.markdown(metric_box(
            "RSI(14)", f"{rsi:.1f}" if rsi is not None else "n/a",
            sub=rsi_state,
            accent="cyan" if rsi is not None and rsi < 30 else (
                "red" if rsi is not None and rsi > 70 else ""),
            help_text="<30 survendu · >70 surachete.",
        ), unsafe_allow_html=True)
    with mrow1[2]:
        trend_ok = bool(ind and ind.get("sma200") and ind["close"] > ind["sma200"])
        st.markdown(metric_box(
            "Tendance LT (vs SMA200)",
            "Haussier" if trend_ok else ("Baissier" if ind else "n/a"),
            sub=(f"SMA200 {(ind or {}).get('sma200', 0):.2f}" if ind and ind.get("sma200")
                 else "—"),
            accent="" if trend_ok else "red",
            help_text="Prix au-dessus / en-dessous de la moyenne 200 jours.",
        ), unsafe_allow_html=True)
    with mrow1[3]:
        vol = (ind or {}).get("vol_ann")
        st.markdown(metric_box(
            "Vol. annualisee",
            f"{vol:.0f}%" if vol is not None else "n/a",
            sub="Sizing inverse-vol",
            accent="amber" if vol and vol > 35 else "",
            help_text="Plus c'est eleve, plus la taille de position est reduite.",
        ), unsafe_allow_html=True)

    mrow2 = st.columns(4)
    with mrow2[0]:
        elig = ", ".join((bprofile or {}).get("eligibility") or []) or "n/a"
        st.markdown(metric_box("Eligibilite PEA/SRD", elig, sub="Boursorama",
                               accent="cyan"), unsafe_allow_html=True)
    with mrow2[1]:
        cons = (bprofile or {}).get("consensus_score")
        st.markdown(metric_box(
            "Consensus analystes",
            f"{cons:.2f}" if cons is not None else "n/a",
            sub=(bprofile or {}).get("sentiment") or "—",
        ), unsafe_allow_html=True)
    with mrow2[2]:
        tgt = (bprofile or {}).get("target_price")
        pot = (bprofile or {}).get("potential_pct")
        st.markdown(metric_box(
            "Objectif 3 mois",
            f"{tgt:.2f} €" if tgt is not None else "n/a",
            sub=f"{pot:+.1f}%" if pot is not None else "—",
        ), unsafe_allow_html=True)
    with mrow2[3]:
        isin = (bprofile or {}).get("isin") or "n/a"
        st.markdown(metric_box(
            "ISIN", isin,
            sub=f"{(bprofile or {}).get('index') or '—'} / "
                f"{(bprofile or {}).get('exchange') or '—'}",
        ), unsafe_allow_html=True)

    # Technical analysis explanation (full width)
    st.markdown(
        f"<div class='eli5'><b style='color:{_AMBER};'>"
        f"Analyse technique expliquee — {format_name(selected)}</b><br>"
        f"{build_ta_explanation(ind, alpha)}</div>",
        unsafe_allow_html=True,
    )

    # Full-width TradingView chart
    chart_html = f"""
    <div class="tradingview-widget-container" style="height:620px;width:100%">
      <div id="tv_chart_explore" style="height:620px;width:100%"></div>
      <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
      <script type="text/javascript">
        new TradingView.widget({{
          "autosize": true, "symbol": "{tv}", "interval": "D",
          "timezone": "Europe/Paris", "theme": "dark", "style": "1",
          "locale": "fr", "enable_publishing": false,
          "hide_side_toolbar": false, "allow_symbol_change": true,
          "studies": ["RSI@tv-basicstudies", "MASimple@tv-basicstudies"],
          "container_id": "tv_chart_explore"
        }});
      </script>
    </div>
    """
    components.html(chart_html, height=640)

    # TA widget + SMAs under chart
    tw1, tw2 = st.columns([1, 1])
    with tw1:
        ta_html = f"""
        <div class="tradingview-widget-container">
          <div class="tradingview-widget-container__widget"></div>
          <script type="text/javascript"
            src="https://s3.tradingview.com/external-embedding/embed-widget-technical-analysis.js" async>
          {{"interval":"1D","width":"100%","isTransparent":true,"height":380,
            "symbol":"{tv}","showIntervalTabs":true,"locale":"fr","colorTheme":"dark"}}
          </script>
        </div>
        """
        components.html(ta_html, height=400)
    with tw2:
        sma_bits = []
        if ind:
            for k, lab in (("sma5", "SMA5"), ("sma50", "SMA50"), ("sma200", "SMA200")):
                if ind.get(k):
                    sma_bits.append(f"{lab}: <b>{ind[k]:.2f}</b>")
        pc = (alpha or {}).get("put_call")
        ins = (alpha or {}).get("insider", 0)
        ins_txt = {1: "Achats nets dirigeants", -1: "Ventes nettes dirigeants"}.get(
            ins, "Neutre / indisponible"
        )
        st.markdown(
            f"<div style='background:#0A0A0A;padding:16px;border:1px solid #222;"
            f"min-height:360px;line-height:1.7;color:#E0E0E0;'>"
            f"<div style='color:{_CYAN};font-size:12px;letter-spacing:1px;'>"
            f"RECAP QUANT</div>"
            f"<div style='margin-top:10px;'>{' · '.join(sma_bits) or 'SMA n/a'}</div>"
            f"<div style='margin-top:12px;'><b>Put/Call</b> : "
            f"{f'{pc:.2f}' if pc is not None else 'n/a'} "
            f"<span style='color:{_MUTED};font-size:12px;'>"
            f"(souvent neutre sur small/mid .PA — chaine options rare)</span></div>"
            f"<div style='margin-top:12px;'><b>Insiders</b> : {ins_txt}</div>"
            f"<div style='margin-top:12px;color:{_MUTED};font-size:13px;'>"
            f"TradingView: <code>{tv}</code></div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    # News — full width, 2 columns (not a cramped side panel)
    st.markdown(f"#### 📰 Actualites — {short_name(selected)}")
    news = get_recent_news(selected, limit=8)
    if news:
        score_toggle = st.checkbox(
            "Scorer l'impact (IA + mots-cles)",
            value=True,
            key="explore_score_news",
        )
        if score_toggle:
            with st.spinner("Notation…"):
                scores = [score_news_with_llm(selected, n["title"]) for n in news]
        else:
            scores = [heuristic_news_score(n["title"]) for n in news]
        ranked = sorted(zip(news, scores), key=lambda x: abs(x[1] or 0), reverse=True)
        ncol1, ncol2 = st.columns(2)
        for i, (n, sc) in enumerate(ranked):
            with (ncol1 if i % 2 == 0 else ncol2):
                render_news_card(selected, n, sc)
    else:
        st.caption("Aucune actualite majeure recente pour cet actif.")

    # Insiders — AMF first (official), then FMP, then Yahoo
    st.markdown("---")
    st.markdown("#### 🕵️ Activite des dirigeants (insiders)")
    st.markdown(
        "<div class='info-text'><b>Cascade stricte : AMF BDIF → FMP → Yahoo</b>. "
        "L'AMF est la source legale officielle FR. Si BDIF est bloque (WAF / "
        "HTTP 500), le terminal bascule sur Financial Modeling Prep "
        "(<code>FMP_API_KEY</code>), puis yfinance. Un achat net massif = "
        "signal de confiance interne, pas un ordre automatique.</div>",
        unsafe_allow_html=True,
    )
    insider_df = get_insider_data(selected)
    if insider_df.empty:
        st.warning(
            f"Aucune transaction insider pour {format_name(selected)}. "
            "AMF/FMP/Yahoo n'ont rien renvoye (couverture variable sur .PA)."
        )
    else:
        src_note = ""
        if "Source" in insider_df.columns and len(insider_df):
            src_note = f" · Source: {insider_df['Source'].iloc[0]}"
        st.caption(f"{len(insider_df)} declaration(s){src_note}")
        disp_cols = {}
        for src, dst in (("Insider", "Insider"), ("Position", "Poste"),
                         ("Transaction", "Transaction"), ("Title", "Titre"),
                         ("Shares", "Actions"), ("Value", "Valeur"),
                         ("Date", "Date"), ("Source", "Source")):
            if src not in insider_df.columns:
                continue
            if src in ("Shares", "Value"):
                disp_cols[dst] = [
                    f"{v:,.0f}" if pd.notna(v) else "—" for v in insider_df[src]
                ]
            elif src == "Title":
                disp_cols[dst] = [
                    str(v)[:80] if pd.notna(v) else "—" for v in insider_df[src]
                ]
            elif src == "Date":
                disp_cols[dst] = [
                    str(v)[:10] if pd.notna(v) else "—" for v in insider_df[src]
                ]
            else:
                disp_cols[dst] = insider_df[src].astype(str)
        disp = pd.DataFrame(disp_cols)
        font_map = None
        if "Transaction" in disp.columns:
            colors = []
            for t in disp["Transaction"]:
                tl = str(t).lower()
                if "buy" in tl or "purchase" in tl or "achat" in tl:
                    colors.append(_NEON)
                elif "sale" in tl or "sell" in tl or "vente" in tl:
                    colors.append(_RED)
                else:
                    colors.append(_WHITE)
            font_map = {"Transaction": colors}
        st.plotly_chart(
            dark_table(disp, height=min(420, 44 + 30 * max(len(disp), 1)),
                       font_color_map=font_map),
            width="stretch",
        )

    # Polymarket — real section
    st.markdown("---")
    st.markdown("#### 🎲 Polymarket — probabilites macro")
    st.markdown(
        "<div class='info-text'>Marches de prediction (API Gamma). "
        "Filtre macro/politique (sports exclus). "
        "<b>Contexte seulement</b> — jamais un trigger d'ordre.</div>",
        unsafe_allow_html=True,
    )
    poly_events = get_polymarket_macro(limit=10)
    if not poly_events:
        st.caption(
            "Polymarket indisponible (reseau / API). "
            "Le briefing geopolitique dans General reste la reference."
        )
    else:
        # Clickable markdown table (Plotly tables can't host real links).
        lines = [
            "| Marche | P(YES) | Vol 24h | Impact PEA | Lien |",
            "|---|---:|---:|---|---|",
        ]
        for ev in poly_events:
            yp = ev.get("yes_prob")
            yp_s = f"**{yp*100:.0f}%**" if yp is not None else "—"
            title = (ev.get("title") or "").replace("|", "/")
            lines.append(
                f"| {title} | {yp_s} | {ev.get('volume24h', 0):,.0f} | "
                f"{ev.get('impact', '—')} | [ouvrir]({ev.get('url')}) |"
            )
        st.markdown("\n".join(lines))

# --- Tab: Full Universe ------------------------------------------------------
with tab_uni:
    st.markdown(
        "<div class='info-text'>Univers PEA investissable + "
        "<b>performance moyenne par secteur</b> (echantillon liquide).</div>",
        unsafe_allow_html=True,
    )
    st.caption(f"{len(universe_df)} titres · "
               f"{universe_df['Sector'].nunique()} secteurs")

    sec_period_map = {"1 Semaine": "5d", "1 Mois": "1mo", "3 Mois": "3mo",
                      "6 Mois": "6mo", "1 An": "1y"}
    sec_label = st.select_slider(
        "Horizon perf. sectorielle", list(sec_period_map.keys()), value="1 Mois",
        key="uni_sec_horizon",
    )
    with st.spinner("Perf. moyennes par secteur…"):
        sec_perf = get_sector_performance(universe_df, period=sec_period_map[sec_label])
    if not sec_perf.empty:
        st.markdown(f"#### Performance moyenne par secteur · {sec_label}")
        sec_bar = pex.bar(
            sec_perf, x="Perf_moy", y="Sector", orientation="h",
            color="Perf_moy", color_continuous_scale=_DIVERGE,
            color_continuous_midpoint=0,
            hover_data={"N": True, "Perf_med": ":.1f", "Best": ":.1f", "Worst": ":.1f"},
        )
        _style_dark_fig(sec_bar, height=max(360, 28 * len(sec_perf)))
        sec_bar.update_layout(margin=dict(t=10, l=0, r=0, b=0),
                              coloraxis_showscale=False,
                              xaxis_title="Perf moyenne %", yaxis_title="")
        st.plotly_chart(sec_bar, width="stretch")
        scolors = [_NEON if v >= 0 else _RED for v in sec_perf["Perf_moy"]]
        sdisp = pd.DataFrame({
            "Secteur": sec_perf["Sector"],
            "Moy": [f"{v:+.1f}%" for v in sec_perf["Perf_moy"]],
            "Med": [f"{v:+.1f}%" for v in sec_perf["Perf_med"]],
            "N": sec_perf["N"],
            "Best": [f"{v:+.1f}%" for v in sec_perf["Best"]],
            "Worst": [f"{v:+.1f}%" for v in sec_perf["Worst"]],
        })
        st.plotly_chart(
            dark_table(sdisp, height=min(480, 48 + 28 * len(sdisp)),
                       font_color_map={"Moy": scolors},
                       col_widths=[2, 0.8, 0.8, 0.5, 0.8, 0.8]),
            width="stretch",
        )
    else:
        st.caption("Perf. sectorielle indisponible pour cet horizon.")

    st.markdown("---")
    csum = universe_df.groupby("Sector").size().reset_index(name="Nb titres")
    cc1, cc2 = st.columns([1, 2])
    with cc1:
        pie = pex.pie(csum, names="Sector", values="Nb titres", hole=0.5,
                     color_discrete_sequence=_BRIGHT_SERIES)
        pie.update_layout(paper_bgcolor=_BG, plot_bgcolor=_BG,
                          font=dict(family="Courier New", color=_WHITE),
                          height=400, margin=dict(t=10, l=0, r=0, b=0),
                          showlegend=False)
        pie.update_traces(textinfo="label+value",
                          marker=dict(line=dict(color=_BG, width=1)))
        st.plotly_chart(pie, width="stretch")
    with cc2:
        sector_filter = st.multiselect("Filtrer par secteur",
                                       sorted(universe_df["Sector"].unique()))
        view = universe_df if not sector_filter else \
            universe_df[universe_df["Sector"].isin(sector_filter)]
        view = view.sort_values(["Sector", "Ticker"])
        disp = pd.DataFrame({
            "Titre": view["Name"], "Ticker": view["Ticker"],
            "Secteur": view["Sector"],
        })
        st.plotly_chart(dark_table(disp, height=400,
                                   col_widths=[2, 1, 1.5]), width="stretch")

# --- Tab: Architecture & Documentation --------------------------------------
with tab_arch:
    st.markdown(
        "<div class='eli5'>\U0001F9E0 <b>Comment fonctionne le bot ?</b> "
        "Cette page explique l'architecture complete, sans jargon inutile. "
        "L'IA ne decide jamais d'acheter ou de vendre : elle traduit du texte "
        "en chiffres. Les decisions restent 100% mathematiques.</div>",
        unsafe_allow_html=True,
    )

    st.markdown("""
### ⏰ L'Horloge (Scheduler)

Le daemon (`main_scheduler.py`) tourne en continu et declenche **3 passes
quotidiennes** (heure de Paris), uniquement les **jours de bourse** :

| Heure | Role |
|-------|------|
| **09:00** | Ouverture — scan apres ouverture Euronext |
| **13:30** | Mid-day — cours + re-evaluation |
| **17:10** | Cloture — derniere passe |

- **Week-end** : pause. **Vendredi 18:00** : Weekly Historian (Discord).
- **1er du mois** : Profit-shave mensuel. **Chaque jour ouvré 08:35** : ATR stops.
- Force manuelle : `python main_scheduler.py --now`

---

### 📡 Les Donnees

| Source | Usage | Statut |
|--------|--------|--------|
| **yfinance** | OHLCV, calendrier, insiders, news fallback | Primaire |
| **VIX / VSTOXX** | Coupe-circuit panic (`VIX_PANIC_THRESHOLD`) | `^V2TX` puis `^VIX` |
| **TradingView** | Graphiques + jauge TA (UI only) | Widgets |
| **Polymarket Gamma** | Probabilites macro (contexte) | Live, no auth |
| **Boursorama** | Profil PEA/SRD, consensus, news (best-effort) | Scraper fragile |
| **AMF BDIF** | Declarations dirigeants (**primaire**) | Officiel FR ; WAF/HTTP 500 possible → FMP → Yahoo |
| **FMP** | Insiders fallback (`FMP_API_KEY`) | Secondaire |
| **OpenRouter** | Sentiment news + briefing geo (explique, ne decide pas) | Optionnel |
| **SQLite + DuckDB** | Portfolio / audit / equity curve / OHLCV | Local |

---

### 🖥️ Dashboard (onglets)

| Onglet | Contenu |
|--------|---------|
| **General & Signaux** | Suggestion adaptative **multi-horizon**, explication cash, fiche ETF Core, reco, geo, registre, news du mois |
| **Portefeuille** | Equity curve + allocation + editeur wallet (SQLite) |
| **Exploration** | Scan liquide top/flop + trajectoires, fiche ticker (dossier entreprise, TA expliquee, news, insiders, Polymarket) |
| **Univers** | Liste PEA + **perf moyenne par secteur** (horizon reglable) |
| **Architecture** | Cette page |

Mode **MICRO** (ex. 100 €) : 1 part liquide + gros cash buffer — le Core
(`CW8.PA`) cote trop cher pour une part entiere. Ce n'est pas une erreur :
c'est de l'optionalite jusqu'au prochain depot.

---

### 🧮 Le Moteur Quantitatif

**Core / Satellite** :

1. **Smart DCA Core** (`CW8.PA`) — plus agressif sous SMA200 (peur).
2. **Satellite MRE** — BUY seulement si **toutes** les conditions :
   - RSI(14) < 30
   - Close > SMA200
   - Close > SMA5 (momentum)
   - EPS > 0
   - VIX ≤ seuil panic
   - Budget satellite / secteur / correlation OK
   - Sizing : Half-Kelly × parite de volatilite × floor PEA
3. **RevocationEngine** — a chaque passe, les signaux PENDING trop vieux
   (`SIGNAL_VALIDITY_HOURS`) ou en drift prix >3% passent REVOKED/EXPIRED
   avant l'alerte Discord.

L'IA **n'approuve jamais** un trade. Discord = copilot manuel.

---

### 🛡️ Bouclier de risque

| Garde-fou | Regle |
|-----------|-------|
| Zero levier | Pas de marge |
| Budget satellite | Max ~30% equity |
| Secteur / ligne | Max ~25% / ~15% (assoupli en MICRO) |
| VIX panic | Bloque nouveaux satellites |
| Stop / shave | ATR quotidien (2.5×ATR14) / +20% trim mensuel |
| Execution | Discord only |

---

### 🖥️ Architecture technique

``​`
AMF → FMP → yfinance / VIX / Bourso best-effort
        → SignalGenerator + SmartDCA
        → CorrelationFirewall + PeaSizer + MacroVeto
        → Monthly ATR rebalancer
        → Discord Copilot
        → SQLite (portfolio + equity curve)  ↔  Streamlit Dashboard
        → DuckDB (OHLCV)
``​`

Le dashboard lit l'etat en continu. L'editeur de wallet peut ecrire
cash/positions. Les ordres restent Discord + scheduler.
""")

# =============================================================================
# Footer + optional auto-refresh
# =============================================================================
st.write("---")
st.caption(
    "PEA Sniper Terminal V-Prime \u00b7 Zero-leverage \u00b7 Execution manuelle "
    "via Discord \u00b7 Donnees: yfinance / TradingView \u00b7 "
    "Ceci n'est PAS un conseil en investissement."
)

if auto_refresh:
    import time as _time

    _time.sleep(int(refresh_secs))
    st.rerun()
```

## FILE: config/api_keys.env.example
```text
# =============================================================================
# PEA Sniper Terminal V-Prime - Secrets template
# -----------------------------------------------------------------------------
# Copy this file to `config/api_keys.env` and fill in real values.
# `config/api_keys.env` is git-ignored and must NEVER be committed.
# =============================================================================

# Discord bot token (Discord Developer Portal -> Bot -> Reset Token).
DISCORD_TOKEN=your_discord_bot_token_here

# Numeric ID of the channel where alerts are posted (enable Developer Mode,
# right-click the channel -> Copy ID).
DISCORD_CHANNEL_ID=123456789012345678

# Discord webhook URL used by the daemon for the weekly report and monthly
# rebalance notifications (Channel -> Edit -> Integrations -> Webhooks -> New).
# This works without a running bot process, so the scheduler can post directly.
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/xxxx/yyyy

# OpenRouter API key (https://openrouter.ai/keys).
OPENROUTER_API_KEY=sk-or-your_openrouter_key_here

# Optional: OpenRouter model slug used for explanations (defaults below).
OPENROUTER_MODEL=mistralai/mistral-7b-instruct

# Financial Modeling Prep (https://site.financialmodelingprep.com/developer/docs).
# Secondary insider-trading fallback after AMF BDIF.
FMP_API_KEY=your_fmp_api_key_here

# EOD Historical Data (https://eodhistoricaldata.com/) — optional market data.
EODHD_API_KEY=your_eodhd_api_key_here
```

## FILE: config/earnings_calendar.yaml
```yaml
# =============================================================================
# PEA Sniper Terminal — Earnings / dividend blackout calendar
# -----------------------------------------------------------------------------
# Per-ticker corporate events. The cascade vetoes NEW satellite buys for a
# ticker when an event falls within EARNINGS_BLACKOUT_DAYS (risk_params.yaml).
#
# Format:
#   events:
#     MC.PA:
#       2026-07-24: "Q2 earnings"
#     OR.PA:
#       2026-08-01: "Ex-dividend"
#
# Prefer official / API calendars later (Euronext, Trading Economics). Keep
# HTML scraping of broker sites as a last resort.
# =============================================================================

events: {}
```

## FILE: config/macro_calendar.yaml
```yaml
# =============================================================================
# PEA Sniper Terminal V-Prime - Macro Event Calendar (dummy / seed data)
# -----------------------------------------------------------------------------
# High-impact macro events that trigger a hard veto on new offensive signals
# within MACRO_VETO_DAYS_BEFORE (see risk_params.yaml).
#
# In production this file is refreshed from Trading Economics / Finnhub. For now
# it is seeded manually. Keys are ISO dates (YYYY-MM-DD), values are event names.
# =============================================================================

events:
  2026-06-17: "ECB Rate Decision"
  2026-07-16: "ECB Rate Decision"
  2026-07-17: "Euro Area CPI (Flash)"
  2026-07-31: "US Non-Farm Payrolls (NFP)"
  2026-08-13: "US CPI"
  2026-09-17: "FED Rate Decision"
```

## FILE: config/pea_universe.yaml
```yaml
# PEA Sniper Terminal V-Prime - investable universe
# Synced from Boursorama Eligibilité PEA filter (tools/sync_universe_from_bourso.py).
# Extra flags: srd=true (liquid SRD), pea_pme=true.

universe:
  Basic Materials:
  - ticker: AI.PA
    name: Air Liquide
    srd: true
  - ticker: AKE.PA
    name: Arkema
    srd: true
  - ticker: ALAFY.PA
    name: AFYREN
    pea_pme: true
  - ticker: ALBKK.PA
    name: BAIKOWSKI
    pea_pme: true
  - ticker: ALCOG.PA
    name: COGRA
    pea_pme: true
  - ticker: ALCRB.PA
    name: CARBIOS
    pea_pme: true
  - ticker: ALDUB.PA
    name: ENCRES DUBUIT
    pea_pme: true
  - ticker: ALFLO.PA
    name: FLORENTAISE
    pea_pme: true
  - ticker: ALGLD.PA
    name: GOLD BY GOLD
    pea_pme: true
  - ticker: ALHGR.PA
    name: HOFFMANN GREEN CEMENT TEC.
    pea_pme: true
  - ticker: ALHRG.PA
    name: HERIGE
    pea_pme: true
  - ticker: ALKOM.PA
    name: PLASTICOS COMP
    pea_pme: true
  - ticker: ALLUX.PA
    name: INSTALLUX
    pea_pme: true
  - ticker: ALMIB.PA
    name: AMOEBA
    pea_pme: true
  - ticker: ALMOU.PA
    name: MOULINVEST
    pea_pme: true
  - ticker: ALRGR.PA
    name: ROUGIER S.A.
    pea_pme: true
  - ticker: ALVIN.PA
    name: VINPAI
  - ticker: CBE.PA
    name: ROBERTET CI E87
    pea_pme: true
  - ticker: ERA.PA
    name: Eramet
    pea_pme: true
    srd: true
  - ticker: EXPL.PA
    name: EPC GROUPE
    pea_pme: true
  - ticker: GRVO.PA
    name: VOLTZ (GRAINES)
  - ticker: JCQ.PA
    name: Jacquet Metals
    pea_pme: true
    srd: true
  - ticker: LHYFE.PA
    name: LHYFE
    pea_pme: true
  - ticker: MLDYN.PA
    name: DYNAFOND
    pea_pme: true
  - ticker: MLPRX.PA
    name: PARX MATERIALS
    pea_pme: true
  - ticker: NK.PA
    name: Imerys
    srd: true
  - ticker: RBT.PA
    name: ROBERTET
    pea_pme: true
  - ticker: VCT.PA
    name: Vicat
    srd: true
  - ticker: VK.PA
    name: Vallourec
    srd: true
  Communication Services:
  - ticker: ALALO.PA
    name: ACHETER-LOUER.FR
    pea_pme: true
  - ticker: ALATA.PA
    name: ATARI
    pea_pme: true
  - ticker: ALBIZ.PA
    name: OBIZ
    pea_pme: true
  - ticker: ALBLD.PA
    name: BILENDI
    pea_pme: true
  - ticker: ALDNE.PA
    name: DONTNOD
    pea_pme: true
  - ticker: ALDNX.PA
    name: DNXCORP
    pea_pme: true
  - ticker: ALDUX.PA
    name: ADUX
    pea_pme: true
  - ticker: ALECP.PA
    name: EUROPACORP
    pea_pme: true
    srd: true
  - ticker: ALENT.PA
    name: ETHERO
    pea_pme: true
  - ticker: ALFUM.PA
    name: FILL UP MEDIA
    pea_pme: true
  - ticker: ALHOP.PA
    name: HOPSCOTCH GRP
    pea_pme: true
  - ticker: ALINV.PA
    name: INVIBES ADV
    pea_pme: true
  - ticker: ALISP.PA
    name: ISPD NETWORK
    pea_pme: true
  - ticker: ALKLA.PA
    name: KLARSEN
    pea_pme: true
  - ticker: ALLLN.PA
    name: LLEID SERV TELEM
    pea_pme: true
  - ticker: ALMEX.PA
    name: MEXEDIA
    pea_pme: true
  - ticker: ALMKS.PA
    name: MAKING SCI GRP
    pea_pme: true
  - ticker: ALNMG.PA
    name: NETMEDIA GROUP
    pea_pme: true
  - ticker: ALPRI.PA
    name: PRISMAFLEX INTL
    pea_pme: true
  - ticker: ALPUL.PA
    name: PULLUP ENTERTAINMENT
    pea_pme: true
  - ticker: ALSRS.PA
    name: SIRIUS MEDIA
    pea_pme: true
  - ticker: ALUNI.PA
    name: UNIFY GROUP
  - ticker: ALWIN.PA
    name: WINAMP GROUP
  - ticker: ALWIT.PA
    name: WITBE
  - ticker: ALXIL.PA
    name: XILAM ANIMATION
  - ticker: BOL.PA
    name: Bollore
    srd: true
  - ticker: DEC.PA
    name: JCDecaux
    pea_pme: true
    srd: true
  - ticker: DEEZR.PA
    name: DEEZER
    pea_pme: true
  - ticker: DKUPL.PA
    name: DEKUPLE
    pea_pme: true
  - ticker: EFG.PA
    name: EAGLE FOOTBALL GR
    pea_pme: true
  - ticker: ETL.PA
    name: Eutelsat
    pea_pme: true
    srd: true
  - ticker: GAM.PA
    name: GAUMONT
    pea_pme: true
  - ticker: HCO.PA
    name: HIGH CO
    pea_pme: true
  - ticker: LOCAL.PA
    name: Solocal
    pea_pme: true
    srd: true
  - ticker: MLHPE.PA
    name: HOPENING
    pea_pme: true
  - ticker: MLIML.PA
    name: IMALLIANCE
    pea_pme: true
  - ticker: MLIMP.PA
    name: IMPRIMERIE CHIRAT
    pea_pme: true
  - ticker: MMT.PA
    name: M6 Metropole Television
    pea_pme: true
    srd: true
  - ticker: NACON.PA
    name: NACON
    pea_pme: true
  - ticker: NRG.PA
    name: NRJ GRP
    pea_pme: true
  - ticker: ODET.PA
    name: Compagnie de l'Odet
    srd: true
  - ticker: ORA.PA
    name: Orange
    pea_pme: true
    srd: true
  - ticker: PRC.PA
    name: Artmarket.com
    pea_pme: true
    srd: true
  - ticker: PUB.PA
    name: Publicis Groupe
    pea_pme: true
    srd: true
  - ticker: TFI.PA
    name: TF1
    srd: true
  - ticker: UBI.PA
    name: Ubisoft
    srd: true
  - ticker: VANTI.PA
    name: VANTIVA
  - ticker: VIV.PA
    name: VIVENDI
  Consumer Cyclical:
  - ticker: ABEO.PA
    name: ABEO
    pea_pme: true
  - ticker: AC.PA
    name: Accor
    srd: true
  - ticker: AKW.PA
    name: Akwel
    pea_pme: true
    srd: true
  - ticker: ALAIR.PA
    name: AIRWELL
    pea_pme: true
  - ticker: ALATI.PA
    name: ACTIA GROUP
    pea_pme: true
  - ticker: ALBI.PA
    name: GASCOGNE
    pea_pme: true
  - ticker: ALBOU.PA
    name: BOURRELIER GRP
    pea_pme: true
  - ticker: ALCAF.PA
    name: CAFOM
    pea_pme: true
  - ticker: ALCAT.PA
    name: Catana Group
    pea_pme: true
    srd: true
  - ticker: ALDAR.PA
    name: DAMARTEX
    pea_pme: true
  - ticker: ALDBL.PA
    name: BERNARD LOISEAU
    pea_pme: true
  - ticker: ALDEL.PA
    name: DELFINGEN
    pea_pme: true
  - ticker: ALDEV.PA
    name: DEVERNOIS
    pea_pme: true
  - ticker: ALDLT.PA
    name: DELTA PLUS GRP
    pea_pme: true
  - ticker: ALEMV.PA
    name: EMOVA GRP
    pea_pme: true
  - ticker: ALFOR.PA
    name: FORSEE POWER
    pea_pme: true
  - ticker: ALFPC.PA
    name: FOUNTAINE PAJOT
    pea_pme: true
  - ticker: ALGIL.PA
    name: GROUPE GUILLIN
    pea_pme: true
  - ticker: ALHEX.PA
    name: Hexaom
    pea_pme: true
    srd: true
  - ticker: ALHPI.PA
    name: HOPIUM
    pea_pme: true
  - ticker: ALHRS.PA
    name: HRS (HYDROGEN REFUELING SOL.)
    pea_pme: true
  - ticker: ALHUN.PA
    name: HUNYVERS
    pea_pme: true
  - ticker: ALKLN.PA
    name: KALEON
    pea_pme: true
  - ticker: ALLEX.PA
    name: LEXIBOOK LINGUIST
    pea_pme: true
  - ticker: ALLPL.PA
    name: LEPERMISLIBRE
    pea_pme: true
  - ticker: ALLSF.PA
    name: LE SLIP FRANCAIS
    pea_pme: true
  - ticker: ALMLB.PA
    name: MILIBOO
    pea_pme: true
  - ticker: ALMRB.PA
    name: MR BRICOLAGE
    pea_pme: true
  - ticker: ALNLF.PA
    name: NEOLIFE
    pea_pme: true
  - ticker: ALPAS.PA
    name: PASSAT
    pea_pme: true
  - ticker: ALPDX.PA
    name: PISCINES DESJOYAUX
    pea_pme: true
  - ticker: ALPET.PA
    name: PET SVC HLDG
    pea_pme: true
  - ticker: ALPG.PA
    name: PREATONI GRP
    pea_pme: true
  - ticker: ALPVL.PA
    name: PLASTiVALOIRE
    pea_pme: true
    srd: true
  - ticker: ALRFG.PA
    name: RACING FORCE
    pea_pme: true
  - ticker: ALSPT.PA
    name: SPARTOO
    pea_pme: true
  - ticker: ALU10.PA
    name: U10 CORP
  - ticker: ALUPG.PA
    name: UPERGY
  - ticker: ALVAP.PA
    name: KUMULUS VAPE
    pea_pme: true
  - ticker: ALVIA.PA
    name: VIALIFE
  - ticker: ALVU.PA
    name: VENTE UNIQUE.COM
  - ticker: ARAMI.PA
    name: ARAMIS GROUP
    pea_pme: true
  - ticker: BAIN.PA
    name: BAINS DE MER MONACO
    srd: true
  - ticker: BB.PA
    name: Bic
    srd: true
  - ticker: BEN.PA
    name: Beneteau
    pea_pme: true
    srd: true
  - ticker: BUI.PA
    name: BARBARA BUI
    pea_pme: true
  - ticker: BUR.PA
    name: BURELLE
    pea_pme: true
  - ticker: CDA.PA
    name: Compagnie des Alpes
    pea_pme: true
    srd: true
  - ticker: CDI.PA
    name: Christian Dior
    srd: true
  - ticker: CHSR.PA
    name: LA CHAUSSERIA
    pea_pme: true
  - ticker: DPT.PA
    name: ST DUPONT
    pea_pme: true
  - ticker: ELIOR.PA
    name: ELIOR GROUP
    pea_pme: true
    srd: true
  - ticker: FCMC.PA
    name: CASINO CANNES
    pea_pme: true
  - ticker: FDJU.PA
    name: FDJ United
    pea_pme: true
    srd: true
  - ticker: FNAC.PA
    name: Fnac Darty
    pea_pme: true
    srd: true
  - ticker: FR.PA
    name: Valeo
    srd: true
  - ticker: FRVIA.PA
    name: Forvia
    pea_pme: true
    srd: true
  - ticker: GJAJ.PA
    name: GROUPE JAJ (EX JAJ DISTRIBUTION)
    pea_pme: true
  - ticker: HDP.PA
    name: HOTELS DE PARIS
    pea_pme: true
  - ticker: ITXT.PA
    name: INTL TEXT.ASSOCIES
    pea_pme: true
  - ticker: KER.PA
    name: Kering
    srd: true
  - ticker: KOF.PA
    name: KAUFMAN ET BROAD
    pea_pme: true
  - ticker: LEBL.PA
    name: FONCIERE 7 INV
    pea_pme: true
  - ticker: MC.PA
    name: LVMH
    srd: true
  - ticker: MDM.PA
    name: MAISONS DU MONDE
    pea_pme: true
  - ticker: MHM.PA
    name: MYHOTELMATCH
    pea_pme: true
  - ticker: MLAA.PA
    name: L'AGENCE AUTOMOBILIERE
    pea_pme: true
  - ticker: MLARD.PA
    name: ARDOIN AMAND N-A
    pea_pme: true
  - ticker: MLCLI.PA
    name: MAISON CLIO
    pea_pme: true
  - ticker: MLCLP.PA
    name: Colipays
    pea_pme: true
  - ticker: MLCMB.PA
    name: COMPAGNIE MONT BLANC
    pea_pme: true
  - ticker: MLHBP.PA
    name: HOTELES BESTPR
    pea_pme: true
  - ticker: MLHCF.PA
    name: HOME CONCEPT
    pea_pme: true
  - ticker: MLHIN.PA
    name: HOTELIERE IMMOBILIERE DE NICE
    pea_pme: true
  - ticker: MLHOT.PA
    name: HOTELIM
    pea_pme: true
  - ticker: MLIFS.PA
    name: IMPULSE FITNESS
    pea_pme: true
  - ticker: MLODT.PA
    name: ODIOT
    pea_pme: true
  - ticker: MLONE.PA
    name: BODY ONE
    pea_pme: true
  - ticker: MLSML.PA
    name: SMALTO
    pea_pme: true
  - ticker: MLSTR.PA
    name: STREIT MECANIQ.
    pea_pme: true
  - ticker: MMB.PA
    name: Lagardere
    srd: true
  - ticker: NR21.PA
    name: NR21
    pea_pme: true
  - ticker: OPM.PA
    name: OPmobility
    pea_pme: true
    srd: true
  - ticker: PARP.PA
    name: PARTOUCHE
    pea_pme: true
  - ticker: RBO.PA
    name: ROCHE BOBOIS
    pea_pme: true
  - ticker: RMS.PA
    name: Hermes International
    srd: true
  - ticker: RNO.PA
    name: Renault
    pea_pme: true
    srd: true
  - ticker: SFCA.PA
    name: SOC FRANC CASINOS
    pea_pme: true
  - ticker: SK.PA
    name: SEB
    srd: true
  - ticker: SMCP.PA
    name: SMCP
    pea_pme: true
  - ticker: SRP.PA
    name: SHOWROOMPRIVE
    pea_pme: true
  - ticker: STLAP.PA
    name: Stellantis
    pea_pme: true
    srd: true
  - ticker: TFF.PA
    name: TFF Group
    srd: true
  - ticker: TRI.PA
    name: Trigano
    srd: true
  - ticker: VAC.PA
    name: Pierre et Vacances
    pea_pme: true
    srd: true
  - ticker: VRLA.PA
    name: VERALLIA
  Consumer Defensive:
  - ticker: ALAVI.PA
    name: ADVINI
    pea_pme: true
  - ticker: ALECO.PA
    name: ECOMIAM
    pea_pme: true
  - ticker: ALFLE.PA
    name: FLEURY MICHON
    pea_pme: true
  - ticker: ALIEV.PA
    name: IEVA GROUP
    pea_pme: true
  - ticker: ALKKO.PA
    name: KKO INTL
    pea_pme: true
  - ticker: ALLAN.PA
    name: LANSON-BCC
    pea_pme: true
  - ticker: ALMER.PA
    name: SAPMER
    pea_pme: true
  - ticker: ALODC.PA
    name: OMER-DECUGIS & CIE
    pea_pme: true
  - ticker: ALPAU.PA
    name: PAULIC MEUNERIE
    pea_pme: true
  - ticker: ALPOU.PA
    name: POULAILLON
    pea_pme: true
  - ticker: ALVAL.PA
    name: VALBIOTIS
  - ticker: BN.PA
    name: Danone
    srd: true
  - ticker: BOI.PA
    name: Boiron
    pea_pme: true
    srd: true
  - ticker: BON.PA
    name: Bonduelle
    pea_pme: true
    srd: true
  - ticker: CA.PA
    name: Carrefour
    srd: true
  - ticker: CO.PA
    name: Casino Guichard
    pea_pme: true
    srd: true
  - ticker: ITP.PA
    name: Interparfums
    srd: true
  - ticker: JBOG.PA
    name: BOGART
    pea_pme: true
  - ticker: LOUP.PA
    name: LDC
    pea_pme: true
    srd: true
  - ticker: LPE.PA
    name: LAURENT PERRIER
    pea_pme: true
  - ticker: MALT.PA
    name: MALTER.FRANCO-BEL
    pea_pme: true
  - ticker: MBWS.PA
    name: Marie Brizard
    pea_pme: true
    srd: true
  - ticker: MLAAH.PA
    name: AMATHEON AGRI
    pea_pme: true
  - ticker: MLCAC.PA
    name: LOMBARD ET MEDOT
    pea_pme: true
  - ticker: MLFDV.PA
    name: FD
    pea_pme: true
  - ticker: MLGAL.PA
    name: GALEO
    pea_pme: true
  - ticker: MLGRC.PA
    name: GROUPE CARNIVOR
    pea_pme: true
  - ticker: MLONL.PA
    name: ONLINEFORMAPRO N
    pea_pme: true
  - ticker: MLSCI.PA
    name: SCIENTIA SCHOOL
    pea_pme: true
  - ticker: MLSDN.PA
    name: SAVONNERIE NYONS
    pea_pme: true
  - ticker: MLSRP.PA
    name: SPEED RABBIT PIZZA
    pea_pme: true
  - ticker: OR.PA
    name: L'Oreal
    srd: true
  - ticker: POMRY.PA
    name: MAISON POMMERY & ASS.
    pea_pme: true
    srd: true
  - ticker: RCO.PA
    name: Remy Cointreau
    pea_pme: true
    srd: true
  - ticker: RI.PA
    name: Pernod Ricard
    srd: true
  - ticker: SABE.PA
    name: SAINT JEAN GRP
    pea_pme: true
  - ticker: SAVE.PA
    name: Savencia
    pea_pme: true
    srd: true
  - ticker: SBT.PA
    name: Oeneo
    pea_pme: true
    srd: true
  Divers:
  - ticker: ALSEI.PA
    name: SOC EDIT IL FAT
    pea_pme: true
  - ticker: AUGR.PA
    name: AUGROS COSM PACK
    pea_pme: true
  - ticker: FGRMC.PA
    name: EIFFAGE
    srd: true
  - ticker: ML.PA
    name: MICHELIN
    srd: true
  - ticker: MLBIO.PA
    name: NORTEM BIOGROUP
    pea_pme: true
  - ticker: MLCOR.PA
    name: COREP LIGHTING
    pea_pme: true
  - ticker: MLEAV.PA
    name: E.A.V.S. GROUPE
    pea_pme: true
  - ticker: MLMFI.PA
    name: CONDOR TECH
    pea_pme: true
  - ticker: MLVIE.PA
    name: INTEGRIT VIAGER
    pea_pme: true
  - ticker: YOUNI.PA
    name: YOUNITED FINL
  ETF:
  - ticker: C50.PA
    name: Amundi Euro Stoxx 50 UCITS ETF
  - ticker: CAC.PA
    name: Amundi CAC 40 UCITS ETF
  - ticker: CW8.PA
    name: Amundi MSCI World UCITS ETF (Core)
  - ticker: ESE.PA
    name: BNP Paribas Easy S&P 500 UCITS ETF
  - ticker: LYPS.DE
    name: Amundi S&P 500 UCITS ETF
  - ticker: PAASI.PA
    name: Amundi PEA Asie Emergente UCITS ETF
  - ticker: PABZ.PA
    name: Amundi PEA MSCI USA UCITS ETF
  - ticker: PAEEM.PA
    name: Amundi PEA Emerging Markets UCITS ETF
  - ticker: PANX.PA
    name: Amundi Nasdaq-100 UCITS ETF
  - ticker: PCEU.PA
    name: Amundi PEA MSCI Europe UCITS ETF
  - ticker: PE500.PA
    name: Amundi PEA S&P 500 UCITS ETF
  - ticker: PUST.PA
    name: Amundi PEA Nasdaq-100 UCITS ETF
  - ticker: WPEA.PA
    name: iShares MSCI World Swap PEA UCITS ETF
  Energy:
  - ticker: ALDOL.PA
    name: DOLFINES
    pea_pme: true
  - ticker: ALESA.PA
    name: ECOSLOPS
    pea_pme: true
  - ticker: DPAM.PA
    name: DOCKS PETR.D'AMBE
    pea_pme: true
  - ticker: FDE.PA
    name: FRANCAISE DE L'ENERGIE
    pea_pme: true
  - ticker: GTT.PA
    name: GTT
    srd: true
  - ticker: MAU.PA
    name: Maurel et Prom
    pea_pme: true
    srd: true
  - ticker: MLSEQ.PA
    name: SEQUA PETROLEUM
    pea_pme: true
  - ticker: NAE.PA
    name: NORTH ATLANTIC ENERGIES
    pea_pme: true
  - ticker: RUI.PA
    name: Rubis
    srd: true
  - ticker: TE.PA
    name: Technip Energies
    srd: true
  - ticker: TTE.PA
    name: TotalEnergies
    pea_pme: true
    srd: true
  - ticker: VIRI.PA
    name: VIRIDIEN
  Financial Services:
  - ticker: ABCA.PA
    name: ABC Arbitrage
    pea_pme: true
    srd: true
  - ticker: ACA.PA
    name: Credit Agricole
    pea_pme: true
    srd: true
  - ticker: ALAUD.PA
    name: AUDACIA
    pea_pme: true
  - ticker: ALBON.PA
    name: LEBON
    pea_pme: true
  - ticker: ALCBI.PA
    name: CRYPTO BLOCKCHAIN INDUSTRIES
    pea_pme: true
  - ticker: ALEFA.PA
    name: EDUFORM'ACTION
    pea_pme: true
  - ticker: ALERO.PA
    name: EUROLAND CORP
    pea_pme: true
  - ticker: ALEXP.PA
    name: ONE EXPERIENCE
    pea_pme: true
  - ticker: ALVAZ.PA
    name: VAZIVA
  - ticker: AMUN.PA
    name: Amundi
    srd: true
  - ticker: ANTIN.PA
    name: ANTIN INFRA. PARTNERS
    pea_pme: true
  - ticker: BNP.PA
    name: BNP Paribas
    srd: true
  - ticker: BSD.PA
    name: BOURSE DIRECT
    pea_pme: true
  - ticker: CAF.PA
    name: CRCAM PARIS ET IDF
    pea_pme: true
  - ticker: CAT31.PA
    name: CA TOULOUSE 31 CCI
    pea_pme: true
  - ticker: CBDG.PA
    name: CAMBODGE DIV.24
  - ticker: CCN.PA
    name: CRCAM NOR.SE.CCI
    pea_pme: true
  - ticker: CIV.PA
    name: CRCAM ILLE CCI
    pea_pme: true
  - ticker: CMO.PA
    name: CRCAM MORBIHAN CCI
    pea_pme: true
  - ticker: CNDF.PA
    name: CRCAM NORD FRANCE
    pea_pme: true
  - ticker: COFA.PA
    name: Coface
    pea_pme: true
    srd: true
  - ticker: CRAP.PA
    name: CRCAM ALPES PROVENCE.CCI
    pea_pme: true
  - ticker: CRAV.PA
    name: LOIRE ATL.VEND.CCI
    pea_pme: true
  - ticker: CRBP2.PA
    name: CRCAM BRIE PIC2CCI
    pea_pme: true
  - ticker: CRLA.PA
    name: CRCAM LANGUEDOC
    pea_pme: true
  - ticker: CRLO.PA
    name: CRCAM LOIRE HAUTE LOIRE
    pea_pme: true
  - ticker: CRSU.PA
    name: CRCAM SRA CI
    pea_pme: true
  - ticker: CRTO.PA
    name: CRCAM TOURAINE CCI
    pea_pme: true
  - ticker: CS.PA
    name: AXA
    srd: true
  - ticker: EDEN.PA
    name: Edenred
    srd: true
  - ticker: EEM.PA
    name: EEM
    pea_pme: true
  - ticker: EGR.PA
    name: TRANSITION EVERGREEN
  - ticker: ENX.PA
    name: Euronext
    srd: true
  - ticker: FMONC.PA
    name: FINANCIERE MONCEY
    pea_pme: true
    srd: true
  - ticker: GLE.PA
    name: Societe Generale
    srd: true
  - ticker: IDIP.PA
    name: IDI
    pea_pme: true
  - ticker: LTA.PA
    name: Altamir
    pea_pme: true
    srd: true
  - ticker: MF.PA
    name: Wendel
    srd: true
  - ticker: MLAEM.PA
    name: ASHLER MANSON
    pea_pme: true
  - ticker: MLGEQ.PA
    name: GENTLEMEN'S
    pea_pme: true
  - ticker: MLHBB.PA
    name: HOCHE BAINS LES BAINS
    pea_pme: true
  - ticker: MLIRF.PA
    name: INNOVATIVE-RFK
    pea_pme: true
  - ticker: MLMUT.PA
    name: MUTTER VENTURE-WI23
    pea_pme: true
  - ticker: MLNMA.PA
    name: NICOLAS MIGUET N
    pea_pme: true
  - ticker: MLPHO.PA
    name: PHOTONIKE
    pea_pme: true
  - ticker: MLPTZ.PA
    name: PYRATZ CORP.
    pea_pme: true
  - ticker: PEUG.PA
    name: Peugeot Invest
    pea_pme: true
    srd: true
  - ticker: RF.PA
    name: Eurazeo
    srd: true
  - ticker: SCR.PA
    name: SCOR
    srd: true
  - ticker: TBSO.PA
    name: TBSO
  - ticker: TKO.PA
    name: Tikehau Capital
    srd: true
  - ticker: VIL.PA
    name: VIEL
  Healthcare:
  - ticker: AB.PA
    name: AB Science
    pea_pme: true
    srd: true
  - ticker: ABLD.PA
    name: ABL DIAGNOSTICS
    pea_pme: true
  - ticker: ABNX.PA
    name: ABIONYX PHARMA
    pea_pme: true
  - ticker: ABVX.PA
    name: ABIVAX
    pea_pme: true
  - ticker: ADOC.PA
    name: Adocia
    pea_pme: true
    srd: true
  - ticker: AELIS.PA
    name: AELIS FARMA
    pea_pme: true
  - ticker: ALBIO.PA
    name: BIOSYNEX
    pea_pme: true
  - ticker: ALBLU.PA
    name: BLUELINEA
    pea_pme: true
  - ticker: ALBPS.PA
    name: BIOPHYTIS
    pea_pme: true
  - ticker: ALCGM.PA
    name: Cegedim
    pea_pme: true
    srd: true
  - ticker: ALCJ.PA
    name: CROSSJECT
    pea_pme: true
  - ticker: ALCOX.PA
    name: NICOX
    pea_pme: true
  - ticker: ALDMS.PA
    name: DMS
    pea_pme: true
  - ticker: ALDVI.PA
    name: ADVICENNE
    pea_pme: true
  - ticker: ALECR.PA
    name: EUROFINS-CEREP
    pea_pme: true
  - ticker: ALEMG.PA
    name: EUROMEDIS GROUP
    pea_pme: true
  - ticker: ALERS.PA
    name: EUROBIO SCIENTIFIC
    pea_pme: true
  - ticker: ALGAE.PA
    name: FERMENTALG
    pea_pme: true
  - ticker: ALIKO.PA
    name: IKONISYS
    pea_pme: true
  - ticker: ALIMP.PA
    name: IMPLANET
    pea_pme: true
  - ticker: ALINT.PA
    name: INTEGRAGEN
    pea_pme: true
  - ticker: ALKLH.PA
    name: KLEA HOLDING (ex VISIOMED)
    pea_pme: true
  - ticker: ALMDT.PA
    name: MEDIAN TECHNOLOGIES
    pea_pme: true
  - ticker: ALMKT.PA
    name: MAUNA KEA
    pea_pme: true
  - ticker: ALNEV.PA
    name: NEOVACS
    pea_pme: true
  - ticker: ALNFL.PA
    name: NFL BIOSCIENCES
    pea_pme: true
  - ticker: ALNOV.PA
    name: NOVACYT
    pea_pme: true
  - ticker: ALOPM.PA
    name: ONCODESIGN PM
    pea_pme: true
  - ticker: ALPAT.PA
    name: PLANT ADVANCED
    pea_pme: true
  - ticker: ALPRE.PA
    name: PREDILIFE
    pea_pme: true
  - ticker: ALQGC.PA
    name: QUANTUM GENOMICS
    pea_pme: true
  - ticker: ALSAF.PA
    name: SAFE
    pea_pme: true
  - ticker: ALSEN.PA
    name: SENSORION
    pea_pme: true
  - ticker: ALSGD.PA
    name: SPINEGUARD
    pea_pme: true
  - ticker: ALSMA.PA
    name: SMAIO
    pea_pme: true
  - ticker: ALSPW.PA
    name: SPINEWAY
    pea_pme: true
  - ticker: ALTAO.PA
    name: ATON
    pea_pme: true
  - ticker: ALTHE.PA
    name: THERACLION
  - ticker: ALTHX.PA
    name: THX PHARMA (EX THERANEXUS)
  - ticker: ALTME.PA
    name: TME PHARMA
  - ticker: ALVIO.PA
    name: VALERIO THER. (EX...
    pea_pme: true
    srd: true
  - ticker: BIM.PA
    name: bioMerieux
    srd: true
  - ticker: BLC.PA
    name: BASTIDE LE CONFORT MED.
    pea_pme: true
  - ticker: CLARI.PA
    name: Clariane
    pea_pme: true
    srd: true
  - ticker: CVX.PA
    name: CARVOLIX
    pea_pme: true
  - ticker: DBV.PA
    name: DBV Technologies
    pea_pme: true
    srd: true
  - ticker: DIM.PA
    name: Sartorius Stedim Biotech
    srd: true
  - ticker: EAPI.PA
    name: EuroAPI
    pea_pme: true
    srd: true
  - ticker: EL.PA
    name: EssilorLuxottica
    srd: true
  - ticker: EMEIS.PA
    name: Emeis
    pea_pme: true
    srd: true
  - ticker: EQS.PA
    name: EQUASENS
    pea_pme: true
    srd: true
  - ticker: ERF.PA
    name: Eurofins Scientific
    srd: true
  - ticker: GBT.PA
    name: GUERBET
    pea_pme: true
  - ticker: GDS.PA
    name: Ramsay Generale de Sante
    pea_pme: true
    srd: true
  - ticker: GNFT.PA
    name: Genfit
    pea_pme: true
    srd: true
  - ticker: IPH.PA
    name: Innate Pharma
    pea_pme: true
    srd: true
  - ticker: IPN.PA
    name: Ipsen
    srd: true
  - ticker: IVA.PA
    name: INVENTIVA
    pea_pme: true
  - ticker: LBIRD.PA
    name: Lumibird
    pea_pme: true
    srd: true
  - ticker: LNA.PA
    name: LNA Sante
    pea_pme: true
    srd: true
  - ticker: MAAT.PA
    name: MAAT PHARMA
    pea_pme: true
  - ticker: MEDCL.PA
    name: MEDINCELL
    pea_pme: true
  - ticker: MLBON.PA
    name: BONYF
    pea_pme: true
  - ticker: MLINA.PA
    name: INMOLECULE NANO
    pea_pme: true
  - ticker: MLLAB.PA
    name: MEDIA LAB
    pea_pme: true
  - ticker: MLMIB.PA
    name: METRICS IN BAL
    pea_pme: true
  - ticker: NANO.PA
    name: Nanobiotix
    pea_pme: true
    srd: true
  - ticker: OSE.PA
    name: OSE Immunotherapeutics
    pea_pme: true
    srd: true
  - ticker: POXEL.PA
    name: POXEL
    pea_pme: true
  - ticker: SAN.PA
    name: Sanofi
    srd: true
  - ticker: SIGHT.PA
    name: GENSIGHT BIOLOGICS
    pea_pme: true
  - ticker: TNG.PA
    name: TRANSGENE
  - ticker: VETO.PA
    name: Vetoquinol
    srd: true
  - ticker: VIRP.PA
    name: Virbac
    srd: true
  - ticker: VLA.PA
    name: Valneva
    srd: true
  Industrials:
  - ticker: AAA.PA
    name: ALAN ALLMAN ASSOCIATES
    pea_pme: true
  - ticker: ADP.PA
    name: Aeroports de Paris
    srd: true
  - ticker: AF.PA
    name: Air France-KLM
    srd: true
  - ticker: AIR.PA
    name: Airbus
    srd: true
  - ticker: ALBOA.PA
    name: BOA CONCEPT
    pea_pme: true
  - ticker: ALCIS.PA
    name: Catering International Services
    pea_pme: true
    srd: true
  - ticker: ALCUR.PA
    name: ARCURE
    pea_pme: true
  - ticker: ALDBT.PA
    name: DBT
    pea_pme: true
  - ticker: ALEAC.PA
    name: EDILIZIACROB
    pea_pme: true
  - ticker: ALENO.PA
    name: ENOGIA
    pea_pme: true
  - ticker: ALEUP.PA
    name: EUROPLASMA
    pea_pme: true
  - ticker: ALEXA.PA
    name: Exail Technologies
    pea_pme: true
  - ticker: ALFER.PA
    name: SERGE FERRARI
    pea_pme: true
  - ticker: ALGEV.PA
    name: GEVELOT
    pea_pme: true
  - ticker: ALGIR.PA
    name: SIGNAUX GIROD
    pea_pme: true
  - ticker: ALGRO.PA
    name: GROLLEAU
    pea_pme: true
  - ticker: ALHG.PA
    name: LOUIS HACHETTE GROUP
    pea_pme: true
  - ticker: ALIBR.PA
    name: CALIBRE
    pea_pme: true
  - ticker: ALMAR.PA
    name: MARE NOSTRUM
    pea_pme: true
  - ticker: ALMCE.PA
    name: MON COURTIER ENERGIE
    pea_pme: true
  - ticker: ALMGI.PA
    name: MG INTERNATIONAL
    pea_pme: true
  - ticker: ALNSC.PA
    name: NSC GROUPE
    pea_pme: true
  - ticker: ALO.PA
    name: Alstom
    srd: true
  - ticker: ALODY.PA
    name: ODYSSEE TECHNOLOGIES
    pea_pme: true
  - ticker: ALORA.PA
    name: ALTHEORA
    pea_pme: true
  - ticker: ALPJT.PA
    name: POUJOULAT
    pea_pme: true
  - ticker: ALPM.PA
    name: PRECIA
    pea_pme: true
  - ticker: ALSEC.PA
    name: SODITECH
    pea_pme: true
  - ticker: ALSOG.PA
    name: SOGECLAIR
    pea_pme: true
  - ticker: ALSTI.PA
    name: STIF
    pea_pme: true
  - ticker: ALTD.PA
    name: TONNER DRONES
  - ticker: ALTOO.PA
    name: TOOSLA
  - ticker: ALTOU.PA
    name: TOUAX
  - ticker: ALTPC.PA
    name: SMTPC
    pea_pme: true
  - ticker: ALTUV.PA
    name: BIO-UV GRP
    pea_pme: true
  - ticker: ALUCI.PA
    name: LUCIBEL
    pea_pme: true
  - ticker: ALUVI.PA
    name: UV GERMI
  - ticker: ALWF.PA
    name: WINFARM
  - ticker: ALWTR.PA
    name: WATERA
  - ticker: AM.PA
    name: Dassault Aviation
    srd: true
  - ticker: ASY.PA
    name: Assystem
    pea_pme: true
    srd: true
  - ticker: AURE.PA
    name: AUREA
    pea_pme: true
  - ticker: AYV.PA
    name: Ayvens
    srd: true
  - ticker: BVI.PA
    name: Bureau Veritas
    srd: true
  - ticker: CEN.PA
    name: Groupe CRIT
    pea_pme: true
    srd: true
  - ticker: CRI.PA
    name: Chargeurs
    pea_pme: true
    srd: true
  - ticker: DBG.PA
    name: DERICHEBOURG
    pea_pme: true
  - ticker: DG.PA
    name: Vinci
    pea_pme: true
    srd: true
  - ticker: ELIS.PA
    name: Elis
    srd: true
  - ticker: EN.PA
    name: Bouygues
    pea_pme: true
    srd: true
  - ticker: EXA.PA
    name: EXAIL TECHNOLOGIES
    pea_pme: true
    srd: true
  - ticker: EXE.PA
    name: Exel Industries
    pea_pme: true
    srd: true
  - ticker: EXENS.PA
    name: EXOSENS
    pea_pme: true
  - ticker: FGA.PA
    name: FIGEAC AERO
    pea_pme: true
  - ticker: FII.PA
    name: LISI
    pea_pme: true
  - ticker: FINM.PA
    name: FIN MARJOS
    pea_pme: true
  - ticker: GEA.PA
    name: GEA
    pea_pme: true
  - ticker: GET.PA
    name: GETLINK
    srd: true
  - ticker: GLO.PA
    name: GL Events
    pea_pme: true
    srd: true
  - ticker: GPE.PA
    name: GPE PIZZORNO ENVI
    pea_pme: true
  - ticker: HO.PA
    name: Thales
    srd: true
  - ticker: IDL.PA
    name: ID Logistics
    pea_pme: true
    srd: true
  - ticker: IPS.PA
    name: Ipsos
    pea_pme: true
    srd: true
  - ticker: LAT.PA
    name: LATECOERE
    pea_pme: true
  - ticker: LR.PA
    name: Legrand
    srd: true
  - ticker: MLAAT.PA
    name: AZOREAN
    pea_pme: true
  - ticker: MLAGI.PA
    name: GROUPE AG3I
    pea_pme: true
  - ticker: MLAIG.PA
    name: ANDINO GLB
    pea_pme: true
  - ticker: MLCFD.PA
    name: CHEMIN FER DEPARTEMENTAUX
    pea_pme: true
  - ticker: MLCMI.PA
    name: SCEMI
    pea_pme: true
  - ticker: MLFXO.PA
    name: FINAXO
    pea_pme: true
  - ticker: MLHK.PA
    name: H&K
    pea_pme: true
  - ticker: MLHYD.PA
    name: HYDRAULIQUE HLD
    pea_pme: true
  - ticker: MLHYE.PA
    name: HYDRO-EXPLOITATIONS
    pea_pme: true
  - ticker: MLITN.PA
    name: ITALY INNOV
    pea_pme: true
  - ticker: MLPHW.PA
    name: PHONE WEB
    pea_pme: true
  - ticker: MLPLC.PA
    name: PLACOPLATRE
    pea_pme: true
  - ticker: MLROT.PA
    name: ROTH MIONS
    pea_pme: true
  - ticker: MRN.PA
    name: Mersen
    pea_pme: true
    srd: true
  - ticker: MTU.PA
    name: Manitou
    pea_pme: true
    srd: true
  - ticker: NEX.PA
    name: NEXANS
    srd: true
  - ticker: OREGE.PA
    name: OREGE
    pea_pme: true
  - ticker: PERR.PA
    name: PERRIER INDUSTRIE
    pea_pme: true
  - ticker: PIG.PA
    name: Haulotte Group
    pea_pme: true
    srd: true
  - ticker: PLX.PA
    name: PLUXEE
    pea_pme: true
  - ticker: RXL.PA
    name: Rexel
    srd: true
  - ticker: SACI.PA
    name: FIDUCIAL OFF.SOLU
    pea_pme: true
  - ticker: SAF.PA
    name: Safran
    pea_pme: true
    srd: true
  - ticker: SAMS.PA
    name: SAMSE
    pea_pme: true
  - ticker: SCHP.PA
    name: Seche Environnement
    pea_pme: true
    srd: true
  - ticker: SDG.PA
    name: SYNERGIE
    srd: true
  - ticker: SFPI.PA
    name: GROUPE SFPI
    pea_pme: true
  - ticker: SGO.PA
    name: Saint-Gobain
    pea_pme: true
    srd: true
  - ticker: SPIE.PA
    name: Spie
    srd: true
  - ticker: STF.PA
    name: STEF
    pea_pme: true
    srd: true
  - ticker: SU.PA
    name: Schneider Electric
    srd: true
  - ticker: SW.PA
    name: Sodexo
    srd: true
  - ticker: TEP.PA
    name: Teleperformance
    srd: true
  - ticker: THEP.PA
    name: THERMADOR
  - ticker: VIE.PA
    name: Veolia
    srd: true
  - ticker: WAGA.PA
    name: WAGA ENERGY
  Real Estate:
  - ticker: ALADO.PA
    name: ADOMOS
    pea_pme: true
  - ticker: ALEUA.PA
    name: EURASIA GROUPE
    pea_pme: true
  - ticker: ALIMO.PA
    name: GROUPIMO
    pea_pme: true
  - ticker: ALREA.PA
    name: REALITES
    pea_pme: true
  - ticker: ALREB.PA
    name: REBIRTH
    pea_pme: true
  - ticker: ALRIS.PA
    name: RISING STONE
    pea_pme: true
  - ticker: ALTA.PA
    name: ALTAREA
    srd: true
  - ticker: AREIT.PA
    name: ALTAREIT
    pea_pme: true
  - ticker: ARG.PA
    name: ARGAN
    srd: true
  - ticker: ARTE.PA
    name: ARTEA
    pea_pme: true
  - ticker: ATLD.PA
    name: ATLAND
  - ticker: BASS.PA
    name: BASSAC
    pea_pme: true
  - ticker: CFI.PA
    name: CFI
    pea_pme: true
  - ticker: COUR.PA
    name: COURTOIS N
    pea_pme: true
  - ticker: CROS.PA
    name: CROSSWOOD
    pea_pme: true
  - ticker: EFI.PA
    name: EFI
  - ticker: EIFF.PA
    name: Societe de la Tour Eiffel
    srd: true
  - ticker: FSDV.PA
    name: FSDV
    pea_pme: true
  - ticker: MLALV.PA
    name: ALVEEN
    pea_pme: true
  - ticker: MLCOU.PA
    name: COURBET HERITAGE
    pea_pme: true
  - ticker: MLFTI.PA
    name: FRANCE TOURISME
    pea_pme: true
  - ticker: MLIPP.PA
    name: IMM.PARIS.PERLE
    pea_pme: true
  - ticker: MLLCB.PA
    name: LES CONSTRUCTEURS DU BOIS
    pea_pme: true
  - ticker: MLPRE.PA
    name: PRELUDE
    pea_pme: true
  - ticker: MLPRI.PA
    name: SOC NAT PR IMM
    pea_pme: true
  - ticker: MLVIN.PA
    name: FONCIERE VINDI
    pea_pme: true
  - ticker: NXI.PA
    name: Nexity
    pea_pme: true
    srd: true
  - ticker: ORIA.PA
    name: FIDUCIAL REAL ESTATE
    pea_pme: true
  - ticker: SPEL.PA
    name: FONCIERE VOLTA
    pea_pme: true
  Technology:
  - ticker: 74SW.PA
    name: 74Software
    pea_pme: true
    srd: true
  - ticker: AL2SI.PA
    name: 2CRSI
    pea_pme: true
    srd: true
  - ticker: ALARF.PA
    name: ADEUNIS
    pea_pme: true
  - ticker: ALBFR.PA
    name: SIDETRADE
    pea_pme: true
  - ticker: ALBOO.PA
    name: BOOSTHEAT
    pea_pme: true
  - ticker: ALBPK.PA
    name: BROADPEAK
    pea_pme: true
  - ticker: ALCBX.PA
    name: CIBOX INTER ACTIVE
    pea_pme: true
  - ticker: ALCLA.PA
    name: CLARANOVA
    pea_pme: true
  - ticker: ALCOF.PA
    name: COFIDUR
    pea_pme: true
  - ticker: ALCPA.PA
    name: MACOMPTA.FR
    pea_pme: true
  - ticker: ALCPB.PA
    name: CAPITAL B
    pea_pme: true
  - ticker: ALDRV.PA
    name: DRONE VOLT
    pea_pme: true
  - ticker: ALGEC.PA
    name: GECI INTL
    pea_pme: true
  - ticker: ALGID.PA
    name: EGIDE
    pea_pme: true
  - ticker: ALGTR.PA
    name: GROUPE TERA
    pea_pme: true
  - ticker: ALHF.PA
    name: HF COMPANY
    pea_pme: true
  - ticker: ALHIT.PA
    name: HITECHPROS
    pea_pme: true
  - ticker: ALHYP.PA
    name: HIPAY GROUP
    pea_pme: true
  - ticker: ALICA.PA
    name: ICAPE HOLDING
    pea_pme: true
  - ticker: ALIMR.PA
    name: IMMERSION
    pea_pme: true
  - ticker: ALINN.PA
    name: INNELEC MULTIMEDIA
    pea_pme: true
  - ticker: ALITL.PA
    name: IT LINK
    pea_pme: true
  - ticker: ALJXR.PA
    name: ARCHOS
    pea_pme: true
  - ticker: ALKAL.PA
    name: KALRAY
    pea_pme: true
  - ticker: ALKEY.PA
    name: KEYRUS
    pea_pme: true
  - ticker: ALKLK.PA
    name: KERLINK
    pea_pme: true
  - ticker: ALLDL.PA
    name: GROUPE LDLC
    pea_pme: true
  - ticker: ALLGO.PA
    name: LARGO
    pea_pme: true
  - ticker: ALLIX.PA
    name: WALLIX GROUP
  - ticker: ALLOG.PA
    name: LOGIC INSTRUMENT
    pea_pme: true
  - ticker: ALMDG.PA
    name: MGI DIGIT TECH
    pea_pme: true
  - ticker: ALMUN.PA
    name: MUNIC
    pea_pme: true
  - ticker: ALNMR.PA
    name: NAM.R
    pea_pme: true
  - ticker: ALNN6.PA
    name: ENENSYS TECHNO
    pea_pme: true
  - ticker: ALNRG.PA
    name: ENERGISME
    pea_pme: true
  - ticker: ALNSE.PA
    name: NSE
    pea_pme: true
  - ticker: ALNTG.PA
    name: NETGEM
    pea_pme: true
  - ticker: ALORD.PA
    name: ORDISSIMO
    pea_pme: true
  - ticker: ALPHI.PA
    name: FACEPHI BIOMETR
    pea_pme: true
  - ticker: ALPRG.PA
    name: Prologue
    pea_pme: true
    srd: true
  - ticker: ALPWG.PA
    name: PRODWAYS
    pea_pme: true
  - ticker: ALRIB.PA
    name: RIBER
    pea_pme: true
  - ticker: ALROC.PA
    name: ROCTOOL
    pea_pme: true
  - ticker: ALSEM.PA
    name: SEMCO TECHNOLOGIES
    pea_pme: true
  - ticker: ALTAI.PA
    name: LIGHTON
    pea_pme: true
  - ticker: ALTHO.PA
    name: METAVISIO (THOMSON COMP.)
    pea_pme: true
  - ticker: ALTRA.PA
    name: TRACTIAL
  - ticker: ALUAV.PA
    name: EMB SIST INTEL
    pea_pme: true
  - ticker: ALVGO.PA
    name: VOGO
  - ticker: ALWEC.PA
    name: WE.CONNECT
  - ticker: ARTO.PA
    name: ARTOIS
    pea_pme: true
  - ticker: ATE.PA
    name: Alten
    srd: true
  - ticker: ATEME.PA
    name: ATEME
    pea_pme: true
  - ticker: ATO.PA
    name: ATOS GROUP
    pea_pme: true
  - ticker: AUB.PA
    name: Aubay
    pea_pme: true
    srd: true
  - ticker: AVT.PA
    name: Avenir Telecom
    pea_pme: true
    srd: true
  - ticker: BIG.PA
    name: Bigben Interactive
    pea_pme: true
    srd: true
  - ticker: CAP.PA
    name: Capgemini
    srd: true
  - ticker: COH.PA
    name: COHERIS
    pea_pme: true
  - ticker: DSY.PA
    name: Dassault Systemes
    srd: true
  - ticker: EKI.PA
    name: Ekinops
    pea_pme: true
    srd: true
  - ticker: EOS.PA
    name: ACTEOS (EX DATATRONIC)
    pea_pme: true
  - ticker: FPG.PA
    name: UTI GROUP
  - ticker: GUI.PA
    name: GUILLEMOT CORP.
    pea_pme: true
  - ticker: INF.PA
    name: INFOTEL
    pea_pme: true
  - ticker: LACR.PA
    name: LACROIX
    pea_pme: true
  - ticker: LIN.PA
    name: LINEDATA SERVICES
    pea_pme: true
  - ticker: LSS.PA
    name: Lectra
    pea_pme: true
    srd: true
  - ticker: MEMS.PA
    name: MEMSCAP REGPT
    pea_pme: true
  - ticker: MLACT.PA
    name: ACTIVIUM GROUP
    pea_pme: true
  - ticker: MLCHE.PA
    name: CHEOPS TECH FCE
    pea_pme: true
  - ticker: MLCNT.PA
    name: CONSORT NT
    pea_pme: true
  - ticker: MLDAM.PA
    name: DAMARIS
    pea_pme: true
  - ticker: MLFNP.PA
    name: FNP TECH
    pea_pme: true
  - ticker: MLIDS.PA
    name: IDS
    pea_pme: true
  - ticker: MLIFC.PA
    name: INFOCLIP
    pea_pme: true
  - ticker: MLLOI.PA
    name: LOCASYSTEM INTERNATIONAL
    pea_pme: true
  - ticker: MLMGL.PA
    name: MD SERVICES
    pea_pme: true
  - ticker: MLNOV.PA
    name: NOVATECH INDUSTRIES
    pea_pme: true
  - ticker: MLOCT.PA
    name: OCTOPUS BIOSAF
    pea_pme: true
  - ticker: MLPAC.PA
    name: PACTE NOVATION
    pea_pme: true
  - ticker: NRO.PA
    name: Neurones
    pea_pme: true
    srd: true
  - ticker: OVH.PA
    name: OVHCLOUD
    pea_pme: true
  - ticker: PARRO.PA
    name: PARROT
    pea_pme: true
  - ticker: PLNW.PA
    name: PLANISWARE
    pea_pme: true
  - ticker: PROAC.PA
    name: PROACTIS
    pea_pme: true
  - ticker: QDT.PA
    name: Quadient
    pea_pme: true
    srd: true
  - ticker: S30.PA
    name: Solutions 30
    pea_pme: true
    srd: true
  - ticker: SOI.PA
    name: Soitec
    pea_pme: true
    srd: true
  - ticker: SOP.PA
    name: Sopra Steria
    srd: true
  - ticker: STMPA.PA
    name: STMicroelectronics
    pea_pme: true
    srd: true
  - ticker: SWP.PA
    name: Sword Group
    srd: true
  - ticker: VMX.PA
    name: Verimatrix
    srd: true
  - ticker: VU.PA
    name: VusionGroup
    srd: true
  - ticker: WAVE.PA
    name: Wavestone
    srd: true
  - ticker: WLN.PA
    name: Worldline
    srd: true
  - ticker: XFAB.PA
    name: X-FAB SILICON
  Utilities:
  - ticker: ALAGO.PA
    name: E-PANGO
    pea_pme: true
  - ticker: ALAGP.PA
    name: AGRIPOWER
    pea_pme: true
  - ticker: ALCWE.PA
    name: CHARWOOD ENERGY
    pea_pme: true
  - ticker: ALESE.PA
    name: ENTECH
    pea_pme: true
  - ticker: ALETC.PA
    name: ENERGY SOL TECH
    pea_pme: true
  - ticker: ALHAF.PA
    name: HAFFNER ENERGY
    pea_pme: true
  - ticker: ALMIN.PA
    name: MINT
    pea_pme: true
  - ticker: ALOKW.PA
    name: GROUPE OKWIND
    pea_pme: true
  - ticker: ARVEN.PA
    name: ARVERNE
    pea_pme: true
  - ticker: ELEC.PA
    name: ELECTRICITE DE STRASBOURG
    pea_pme: true
  - ticker: ENGI.PA
    name: Engie
    srd: true
  - ticker: HDF.PA
    name: HYDROGENE DE FRANCE
    pea_pme: true
  - ticker: MLBSP.PA
    name: BLUE SHARK PS
    pea_pme: true
  - ticker: MLCMG.PA
    name: CMG CLEANTECH
    pea_pme: true
  - ticker: MLEDR.PA
    name: EAUX DE ROYAN
    pea_pme: true
  - ticker: VLTSA.PA
    name: Voltalia
    srd: true
```

## FILE: config/risk_params.yaml
```yaml
# =============================================================================
# PEA Sniper Terminal V-Prime - Institutional Risk Parameters
# -----------------------------------------------------------------------------
# These limits are NON-NEGOTIABLE. They are enforced by the Correlation Firewall
# (03_risk_portfolio), the Position Sizer, and the Macro Veto Engine.
# All percentages are expressed as fractions (0.15 == 15%).
# =============================================================================

# --- Position Sizing ---------------------------------------------------------
KELLY_FRACTION: 0.5              # Half-Kelly. Never use full Kelly.
MAX_SINGLE_POSITION_PCT: 0.15    # Max 15% of total equity in a single name.
MAX_SECTOR_WEIGHT_PCT: 0.25      # Max 25% of total equity in a single sector.
MAX_ALLOCATION_PER_DAY_PCT: 0.03 # Max 3% of capital deployed per calendar day.

# --- Risk Limits (circuit breakers) -----------------------------------------
DAILY_MAX_LOSS_PCT: -0.005       # Halt execution if daily P&L < -0.5%.
WEEKLY_MAX_LOSS_PCT: -0.02       # Max weekly drawdown before pause.
MONTHLY_MAX_LOSS_PCT: -0.05      # Max monthly drawdown -> liquidate + manual review.

# --- Correlation Limits ------------------------------------------------------
MAX_CORRELATION_TO_PORTFOLIO: 0.70  # Pearson vs any holding.
MAX_CORRELATION_SAME_SECTOR: 0.80   # Stricter allowance within same sector.
CORRELATION_LOOKBACK_DAYS: 60       # Trading days for Pearson window.

# --- Signals -----------------------------------------------------------------
SIGNAL_BUY_THRESHOLD: 75         # Minimum score (0-100) to emit a BUY.
SIGNAL_SELL_THRESHOLD: 35        # Score below which a SELL is considered.
SIGNAL_VALIDITY_HOURS: 12        # Signal expires after 12h.
MACRO_VETO_DAYS_BEFORE: 3        # Veto new trades within N days of macro event.
EARNINGS_BLACKOUT_DAYS: 2        # Per-ticker earnings/div blackout window.
RSI_OVERSOLD_THRESHOLD: 30.0     # MRE trigger; later walk-forward calibrable.
MIN_LIQUIDITY_ADV: 50000         # Min average daily € volume (20d) for new buys.
MAX_POSITIONS_TOTAL: 12          # Cap on simultaneous satellite lines.

# --- Exits -------------------------------------------------------------------
PROFIT_TARGET_PCT: 0.10          # Limit sell at +10% from entry.
STOP_LOSS_PCT: -0.05             # Legacy hard stop (ATR stop is primary).

# --- Core / Satellite model (Phase 10) --------------------------------------
CORE_TICKER: "CW8.PA"            # Amundi MSCI World UCITS ETF (PEA eligible).
CORE_TARGET_PCT: 0.70            # Standard core weight when market overheated.
CORE_CRASH_TARGET_PCT: 0.75      # Larger core weight when CW8 < SMA200 (crash).
CORE_DCA_MAX_TRANCHE_PCT: 0.05   # Max % of equity deployed to core per pass.
SATELLITE_MAX_BUDGET_PCT: 0.30   # Max total equity in satellite stock-picking.

# --- Volatility & VIX defense (Phase 10) ------------------------------------
VOLATILITY_REFERENCE: 0.20       # Baseline annualized vol for parity scaling.
VOLATILITY_MAX_FACTOR: 1.5       # Cap on inverse-volatility up-scaling.
VIX_PANIC_THRESHOLD: 30.0        # V2TX above this vetoes new satellite buys.

# --- Rebalancing (Phase 12 / 15) --------------------------------------------
REBALANCE_PROFIT_SHAVE_PCT: 0.20   # Trim 20% of a winner above +20% PnL.
REBALANCE_PROFIT_TRIGGER_PCT: 20.0 # Profit-shave trigger (unrealized %).
# Dynamic ATR stop: exit if price < avg_entry - REBALANCE_ATR_STOP_MULT * ATR_14.
# (Static -10% stop removed in Phase 15.)
REBALANCE_ATR_STOP_MULT: 2.5
```

## FILE: docker-compose.yml
```yaml
# PEA Sniper Terminal V-Prime - fleet.
#   daemon    : always-on backend (scheduled analysis, weekly report, rebalance)
#   dashboard : Streamlit command center on :8501
# Both share the same image, the database volume, and the config directory.

services:
  daemon:
    build: .
    image: pea_sniper_terminal:latest
    container_name: pea_daemon
    restart: unless-stopped
    env_file:
      - config/api_keys.env
    environment:
      - TZ=Europe/Paris
    volumes:
      - ./database:/app/database
      - ./config:/app/config
    command: ["python", "main_scheduler.py"]

  dashboard:
    build: .
    image: pea_sniper_terminal:latest
    container_name: pea_dashboard
    restart: unless-stopped
    depends_on:
      - daemon
    env_file:
      - config/api_keys.env
    environment:
      - TZ=Europe/Paris
    ports:
      - "8501:8501"
    volumes:
      - ./database:/app/database
      - ./config:/app/config
    command:
      - streamlit
      - run
      - 05_interfaces/terminal_dashboard.py
      - --server.port=8501
      - --server.address=0.0.0.0
      - --server.headless=true

  # Optional: enable the interactive Discord bot (approve/revoke buttons).
  # discord:
  #   build: .
  #   image: pea_sniper_terminal:latest
  #   container_name: pea_discord
  #   restart: unless-stopped
  #   env_file:
  #     - config/api_keys.env
  #   volumes:
  #     - ./database:/app/database
  #     - ./config:/app/config
  #   command: ["python", "run_discord.py"]
```

## FILE: Dockerfile
```text
# PEA Sniper Terminal V-Prime - single image, two roles (daemon + dashboard).
# Python 3.11 (x64) is required: streamlit's pyarrow has no 3.13/arm64 wheel.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Europe/Paris

WORKDIR /app

# System deps: tzdata for Paris scheduling, build tools for wheels that need them.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (better layer caching).
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy the application code.
COPY . .

# Persisted state + Streamlit UI port.
VOLUME ["/app/database"]
EXPOSE 8501

# Default role is the daemon; docker-compose overrides the command for the UI.
CMD ["python", "main_scheduler.py"]
```

## FILE: main_scheduler.py
```python
"""Root daemon scheduler for PEA Sniper Terminal V-Prime.

Ties the whole pipeline together and runs it on the multi-pass European market
schedule (09:00, 13:30, 17:10 Paris time, weekdays only):

    fetch (yfinance -> DuckDB) -> quant signals -> orchestrator (macro veto,
    VIX, correlation, sizing) -> revoke/expire PENDING -> Discord alerts.

Design rules honoured here:
  * Async/sync bridge: the synchronous ``schedule`` job runs the async pipeline
    via ``asyncio.run``.
  * Zero crash tolerance: every pass is wrapped so a data outage or locked DB
    logs CRITICAL and the daemon keeps running for the next pass.
  * Timezone awareness: schedule times are pinned to Europe/Paris; weekends are
    skipped.

This module only stitches existing phases together; it does not modify them.
"""

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

# --- Wire up the digit-prefixed package directories --------------------------
_ROOT = Path(__file__).resolve().parent
for _sub in (
    "00_data_sensors",
    "01_memory_core",
    "02_quant_engine",
    "03_risk_portfolio",
    "04_orchestrator_ai",
    "05_interfaces",
):
    sys.path.insert(0, str(_ROOT / _sub))

import aiohttp  # noqa: E402
import schedule  # noqa: E402

from data_models import Position, PortfolioState, Signal, SignalStatus, SignalType  # noqa: E402
from duckdb_manager import TimeSeriesDB  # noqa: E402
from sqlite_portfolio import PortfolioDB  # noqa: E402
from market_prices_api import MarketDataFetcher  # noqa: E402
from macro_alpha_api import MacroAlphaSensor  # noqa: E402
from technical_scorer import SignalGenerator  # noqa: E402
from smart_dca_engine import SmartDcaCore  # noqa: E402
from monthly_rebalancer import PortfolioRebalancer  # noqa: E402
from signal_priority_cascade import SignalOrchestrator  # noqa: E402
from revocation_engine import RevocationEngine  # noqa: E402
from llm_explainer import NarrativeExplainer  # noqa: E402
from weekly_historian import WeeklyHistorian  # noqa: E402
from discord_copilot import DiscordCopilot  # noqa: E402

logger = logging.getLogger("main_scheduler")

_CONFIG_DIR = _ROOT / "config"
_UNIVERSE_PATH = _CONFIG_DIR / "pea_universe.yaml"
_RISK_PATH = _CONFIG_DIR / "risk_params.yaml"
_TIMEZONE = "Europe/Paris"
_PASS_TIMES = ("09:00", "13:30", "17:10")
_WEEKLY_REPORT_TIME = "18:00"     # Friday CIO digest.
_MONTHLY_CHECK_TIME = "08:30"     # Daily probe; profit-shave acts only on the 1st.
_ATR_STOP_CHECK_TIME = "08:35"    # Daily ATR stop evaluation (weekdays via loop).
_LOOKBACK_DAYS = 400  # ~270 trading days -> enough for SMA-200.


def _core_ticker() -> str:
    """Read the Core ETF ticker from ``risk_params.yaml`` (default CW8.PA)."""
    try:
        with open(_RISK_PATH, "r", encoding="utf-8") as fh:
            risk = yaml.safe_load(fh) or {}
        return str(risk.get("CORE_TICKER", "CW8.PA"))
    except Exception:  # noqa: BLE001
        return "CW8.PA"


async def _post_webhook(content: str) -> bool:
    """Post a plain-text message to the Discord webhook, chunked to 2000 chars.

    Args:
        content: The message body.

    Returns:
        bool: ``True`` if every chunk posted with a 2xx status.
    """
    url = os.getenv("DISCORD_WEBHOOK_URL")
    if not url:
        logger.warning("DISCORD_WEBHOOK_URL not set; message not sent.")
        return False

    chunks = [content[i : i + 1900] for i in range(0, len(content), 1900)] or [""]
    ok = True
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for chunk in chunks:
                async with session.post(url, json={"content": chunk}) as resp:
                    if resp.status not in (200, 204):
                        body = await resp.text()
                        logger.error("Webhook HTTP %s: %s", resp.status, body[:200])
                        ok = False
    except Exception:  # noqa: BLE001 - a failed webhook must not crash the daemon.
        logger.exception("Discord webhook post failed.")
        return False
    return ok


def _load_universe_tickers() -> list[str]:
    """Read the tradable tickers from ``config/pea_universe.yaml``.

    Returns:
        list[str]: All tickers across every sector (empty on failure).
    """
    try:
        with open(_UNIVERSE_PATH, "r", encoding="utf-8") as fh:
            universe = yaml.safe_load(fh) or {}
        return [
            entry["ticker"]
            for members in universe.get("universe", {}).values()
            for entry in members
        ]
    except Exception:  # noqa: BLE001
        logger.exception("Could not read universe file %s", _UNIVERSE_PATH)
        return []


def _refresh_portfolio_prices(
    pdb: PortfolioDB, portfolio: PortfolioState, prices: dict[str, float]
) -> PortfolioState:
    """Mark held positions to market and recompute equity, then persist.

    Keeps the dashboard PnL and the sizer's equity honest between manual
    executions. If nothing changed (no held tickers priced) the input is
    returned unmodified.

    Args:
        pdb: Portfolio database.
        portfolio: Current snapshot.
        prices: ticker -> latest close.

    Returns:
        PortfolioState: The refreshed (and persisted) snapshot.
    """
    if not portfolio.positions:
        return portfolio

    refreshed = []
    for p in portfolio.positions:
        new_price = prices.get(p.ticker, p.current_price)
        refreshed.append(
            Position(
                ticker=p.ticker,
                qty_shares=p.qty_shares,
                avg_entry_price=p.avg_entry_price,
                current_price=new_price if new_price > 0 else p.current_price,
                sector=p.sector,
            )
        )
    positions_value = sum(p.market_value for p in refreshed)
    new_state = PortfolioState(
        cash_available=portfolio.cash_available,
        total_equity=portfolio.cash_available + positions_value,
        positions=refreshed,
        last_updated=datetime.now(timezone.utc),
    )
    try:
        pdb.update_portfolio(new_state)
        logger.info(
            "Portfolio marked to market: equity=%.2f (%d positions).",
            new_state.total_equity,
            len(refreshed),
        )
    except Exception:  # noqa: BLE001 - a failed refresh must not abort the pass.
        logger.exception("Failed to persist marked-to-market portfolio.")
        return portfolio
    return new_state


def _latest_prices(tsdb: TimeSeriesDB, tickers: list[str]) -> dict[str, float]:
    """Fetch the most recent close for each ticker from DuckDB.

    Args:
        tsdb: The time-series database.
        tickers: Tickers to look up.

    Returns:
        dict[str, float]: ticker -> latest close (absent if no data).
    """
    prices: dict[str, float] = {}
    for ticker in tickers:
        try:
            df = tsdb.get_historical_prices(ticker, days=2)
            if df is not None and not df.empty:
                prices[ticker] = float(df["Close"].iloc[-1])
        except Exception:  # noqa: BLE001
            logger.warning("Could not read latest price for %s.", ticker)
    return prices


async def run_pipeline_async() -> None:
    """Execute one full analysis pass end-to-end.

    Raises:
        Exception: Propagated to the sync wrapper, which logs CRITICAL. This
            keeps the daemon alive for the next scheduled pass.
    """
    # --- Init Phase ---
    tsdb = TimeSeriesDB()
    tsdb.init_db()
    pdb = PortfolioDB()
    pdb.init_db()
    fetcher = MarketDataFetcher()
    generator = SignalGenerator()
    orchestrator = SignalOrchestrator(
        config_dir=_CONFIG_DIR, portfolio_db=pdb, timeseries_db=tsdb
    )
    explainer = NarrativeExplainer()
    copilot = DiscordCopilot(portfolio_db=pdb, explainer=explainer)

    core_engine = SmartDcaCore(_CONFIG_DIR)
    macro_alpha = MacroAlphaSensor()
    core_ticker = _core_ticker()

    tickers = _load_universe_tickers()
    if not tickers:
        logger.error("No tickers in universe; aborting pass.")
        return
    # The Core ETF must be fetched too so Smart DCA can read its history.
    fetch_tickers = tickers + ([core_ticker] if core_ticker not in tickers else [])
    logger.info("Universe loaded: %d tickers (+core %s).", len(tickers), core_ticker)

    # --- Data Phase ---
    ok = fetcher.update_database(tsdb, fetch_tickers, lookback_days=_LOOKBACK_DAYS)
    if not ok:
        logger.error("Data ingestion failed; skipping this pass (no stale trades).")
        return

    # --- Macro Phase: European VIX emergency brake ---
    vix_level = macro_alpha.get_european_vix()

    # --- Quant Phase ---
    raw_signals = generator.generate_raw_signals(tsdb, tickers)
    logger.info("Quant engine produced %d raw signal(s).", len(raw_signals))

    # --- Orchestration Phase (satellite) ---
    portfolio: PortfolioState = pdb.get_portfolio_state()
    current_prices = _latest_prices(tsdb, fetch_tickers)
    # Mark held positions to market so PnL/equity are fresh for sizing + UI.
    portfolio = _refresh_portfolio_prices(pdb, portfolio, current_prices)
    processed = orchestrator.process_raw_signals(
        raw_signals, portfolio, current_prices, vix_level=vix_level
    )

    approved = [s for s in processed if s.status == SignalStatus.APPROVED]
    logger.info(
        "Orchestrator finalized %d signal(s): %d APPROVED (VIX=%.1f).",
        len(processed),
        len(approved),
        vix_level,
    )

    # --- Core Phase: Smart DCA on the MSCI World ETF (immune to VIX veto) ---
    core_signal = core_engine.evaluate_cw8(
        tsdb, portfolio.cash_available, portfolio.total_equity
    )
    if core_signal and (core_signal.target_qty or 0) > 0:
        core_signal.status = SignalStatus.APPROVED
        processed.append(core_signal)
        logger.info(
            "Core DCA APPROVED: buy %d %s.", core_signal.target_qty, core_ticker
        )

    # --- Revocation Phase: anti-stale on existing PENDING signals ------------
    revoker = RevocationEngine(_CONFIG_DIR)
    try:
        pending_rows = pdb.fetch_signals_by_status(["PENDING"])
    except Exception:  # noqa: BLE001
        logger.exception("Could not load PENDING signals for revocation.")
        pending_rows = []
    for row in pending_rows:
        try:
            created_raw = row.get("created_at")
            if isinstance(created_raw, str):
                created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            else:
                created_at = datetime.now(timezone.utc)
            sig = Signal(
                id=str(row["id"]),
                ticker=str(row["ticker"]),
                signal_type=SignalType(str(row["signal_type"])),
                status=SignalStatus.PENDING,
                score=float(row.get("score") or 0),
                reason=str(row.get("reason") or ""),
                created_at=created_at,
            )
            cur_px = float(current_prices.get(sig.ticker) or 0.0)
            if cur_px <= 0:
                # Still allow time-expiry with a dummy equal price (no false drift).
                cur_px = 1.0
                orig_px = 1.0
            else:
                # Approximate emission price from DuckDB history near created_at.
                orig_px = cur_px
                try:
                    hist = tsdb.get_historical_prices(sig.ticker, days=30)
                    if hist is not None and not hist.empty and "Close" in hist.columns:
                        # Use oldest close in window as conservative proxy if
                        # we cannot align exact timestamp.
                        series = hist["Close"].dropna()
                        if len(series):
                            orig_px = float(series.iloc[0])
                except Exception:  # noqa: BLE001
                    orig_px = cur_px
            updated = revoker.evaluate_signal(sig, cur_px, orig_px)
            if updated.status in (SignalStatus.REVOKED, SignalStatus.EXPIRED):
                processed.append(updated)
                logger.info(
                    "Pending signal %s -> %s (%s).",
                    updated.id[:8], updated.status.value, updated.ticker,
                )
        except Exception:  # noqa: BLE001
            logger.exception("Revocation failed for row %s.", row.get("id"))

    # Persist every decision to the audit log for the dashboard/ledger.
    for signal in processed:
        try:
            pdb.log_signal(signal)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to audit-log signal %s.", signal.id)

    # --- Alert Phase ---
    alertable = [
        s for s in processed
        if s.status in (SignalStatus.APPROVED, SignalStatus.REVOKED)
    ]
    if not alertable:
        logger.info("No APPROVED/REVOKED signals to push to Discord this pass.")
        return

    if not os.getenv("DISCORD_TOKEN"):
        logger.warning(
            "DISCORD_TOKEN not set; %d alert(s) computed but not sent.",
            len(alertable),
        )
        return

    for signal in alertable:
        try:
            price = current_prices.get(signal.ticker, 0.0)
            await copilot.send_signal_alert(
                signal, portfolio, explainer=explainer, current_price=price
            )
        except Exception:  # noqa: BLE001 - a failed alert must not abort the pass.
            logger.exception("Failed to send Discord alert for %s.", signal.ticker)


def run_analysis_pass() -> None:
    """Synchronous wrapper: skip weekends, run the async pipeline safely."""
    if datetime.today().weekday() >= 5:
        logger.info("Weekend: Market closed, skipping pass.")
        return

    started = time.perf_counter()
    logger.info("=== Analysis pass starting ===")
    try:
        asyncio.run(run_pipeline_async())
        elapsed = time.perf_counter() - started
        logger.info("=== Analysis pass completed in %.1fs ===", elapsed)
    except Exception as exc:  # noqa: BLE001 - daemon must survive any failure.
        elapsed = time.perf_counter() - started
        logger.critical(
            "Analysis pass FAILED after %.1fs: %s", elapsed, exc, exc_info=True
        )


async def run_weekly_report_async() -> None:
    """Generate the weekly CIO digest and push it to the Discord webhook."""
    pdb = PortfolioDB()
    pdb.init_db()
    explainer = NarrativeExplainer()
    historian = WeeklyHistorian()

    report = await historian.generate_weekly_report(pdb, explainer=explainer)
    header = (
        "\U0001F4C8 **PEA Sniper Terminal - Weekly Risk & Performance Digest**\n"
        f"_(generated {datetime.now().strftime('%Y-%m-%d %H:%M')} Paris)_\n\n"
    )
    sent = await _post_webhook(header + report)
    logger.info("Weekly report %s.", "sent" if sent else "computed but NOT sent")


def run_weekly_report() -> None:
    """Sync wrapper for the Friday weekly report job."""
    started = time.perf_counter()
    logger.info("=== Weekly report job starting ===")
    try:
        asyncio.run(run_weekly_report_async())
        logger.info(
            "=== Weekly report done in %.1fs ===", time.perf_counter() - started
        )
    except Exception as exc:  # noqa: BLE001
        logger.critical("Weekly report FAILED: %s", exc, exc_info=True)


async def _push_rebalance_sells(
    sells: list, pdb: PortfolioDB, title: str
) -> None:
    """Audit-log and webhook a batch of rebalance SELL signals."""
    if not sells:
        return
    for signal in sells:
        try:
            pdb.log_signal(signal)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to audit-log rebalance signal %s.", signal.id)
    lines = [f"\U0001F501 **{title}**\n"]
    for s in sells:
        lines.append(f"- **{s.ticker}** SELL {s.target_qty} - {s.reason}")
    await _post_webhook("\n".join(lines))
    logger.info("%s pushed %d SELL signal(s).", title, len(sells))


async def run_daily_atr_stops_async() -> None:
    """Evaluate ATR stop-losses every day (independent of profit-shave)."""
    pdb = PortfolioDB()
    pdb.init_db()
    tsdb = TimeSeriesDB()
    tsdb.init_db()
    rebalancer = PortfolioRebalancer(_CONFIG_DIR, timeseries_db=tsdb)
    portfolio = pdb.get_portfolio_state()
    sells = rebalancer.generate_atr_stop_signals(portfolio)
    if not sells:
        logger.info("Daily ATR stops: nothing triggered.")
        return
    await _push_rebalance_sells(sells, pdb, "Daily ATR Stop-Loss — SELLs for approval")


def run_daily_atr_stops() -> None:
    """Sync wrapper for the daily ATR stop job."""
    # Skip weekends (Euronext closed) — same spirit as analysis passes.
    if datetime.today().weekday() >= 5:
        return
    started = time.perf_counter()
    logger.info("=== Daily ATR stop job starting ===")
    try:
        asyncio.run(run_daily_atr_stops_async())
        logger.info(
            "=== Daily ATR stops done in %.1fs ===",
            time.perf_counter() - started,
        )
    except Exception as exc:  # noqa: BLE001
        logger.critical("Daily ATR stops FAILED: %s", exc, exc_info=True)


async def run_monthly_rebalance_async() -> None:
    """Monthly profit-shave SELLs only (ATR stops run daily separately)."""
    pdb = PortfolioDB()
    pdb.init_db()
    tsdb = TimeSeriesDB()
    tsdb.init_db()
    rebalancer = PortfolioRebalancer(_CONFIG_DIR, timeseries_db=tsdb)

    portfolio = pdb.get_portfolio_state()
    sells = rebalancer.generate_profit_shave_signals(portfolio)
    if not sells:
        logger.info("Monthly rebalance: no profit-shave triggers.")
        await _post_webhook(
            "\U0001F501 **Monthly Rebalance** - no profit-shave triggers this month."
        )
        return

    await _push_rebalance_sells(
        sells, pdb, "Monthly Rebalance — profit-shave SELLs for approval"
    )


def run_monthly_rebalance() -> None:
    """Sync wrapper: only acts on the 1st calendar day of the month."""
    if datetime.today().day != 1:
        return
    started = time.perf_counter()
    logger.info("=== Monthly profit-shave job starting (1st of month) ===")
    try:
        asyncio.run(run_monthly_rebalance_async())
        logger.info(
            "=== Monthly profit-shave done in %.1fs ===",
            time.perf_counter() - started,
        )
    except Exception as exc:  # noqa: BLE001
        logger.critical("Monthly rebalance FAILED: %s", exc, exc_info=True)


def _schedule_passes() -> None:
    """Register all periodic jobs in Europe/Paris time."""
    for pass_time in _PASS_TIMES:
        schedule.every().day.at(pass_time, _TIMEZONE).do(run_analysis_pass)
    # Weekly CIO digest: Friday 18:00 Paris.
    schedule.every().friday.at(_WEEKLY_REPORT_TIME, _TIMEZONE).do(run_weekly_report)
    # Monthly profit-shave: probe daily, act only on the 1st (guarded inside).
    schedule.every().day.at(_MONTHLY_CHECK_TIME, _TIMEZONE).do(run_monthly_rebalance)
    # Daily ATR stops (weekdays guarded inside).
    schedule.every().day.at(_ATR_STOP_CHECK_TIME, _TIMEZONE).do(run_daily_atr_stops)
    logger.info(
        "Scheduled: passes at %s; weekly report Fri %s; monthly probe %s; "
        "ATR stops %s (%s).",
        ", ".join(_PASS_TIMES),
        _WEEKLY_REPORT_TIME,
        _MONTHLY_CHECK_TIME,
        _ATR_STOP_CHECK_TIME,
        _TIMEZONE,
    )


def main() -> None:
    """Entry point: parse CLI args and either run once or loop forever."""
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="PEA Sniper Terminal daemon.")
    parser.add_argument(
        "--now",
        action="store_true",
        help="Run a single analysis pass immediately, then exit.",
    )
    parser.add_argument(
        "--weekly",
        action="store_true",
        help="Generate and send the weekly report now, then exit.",
    )
    parser.add_argument(
        "--rebalance",
        action="store_true",
        help="Run monthly profit-shave now (ignores the 1st-of-month guard).",
    )
    parser.add_argument(
        "--atr-stops",
        action="store_true",
        help="Run daily ATR stop-loss evaluation now.",
    )
    args = parser.parse_args()

    if args.now:
        logger.info("--now: running a single immediate pass.")
        run_analysis_pass()
        return

    if args.weekly:
        logger.info("--weekly: generating the weekly report now.")
        run_weekly_report()
        return

    if args.atr_stops:
        logger.info("--atr-stops: running ATR stop evaluation now.")
        asyncio.run(run_daily_atr_stops_async())
        return

    if args.rebalance:
        logger.info("--rebalance: running monthly profit-shave now.")
        asyncio.run(run_monthly_rebalance_async())
        return

    _schedule_passes()
    logger.info("\U0001F6E1\uFE0F PEA Sniper Terminal Daemon started. "
                "Waiting for scheduled runs...")
    while True:
        try:
            schedule.run_pending()
            time.sleep(60)
        except KeyboardInterrupt:
            logger.info("Shutdown requested; exiting daemon loop.")
            break
        except Exception:  # noqa: BLE001 - never let the loop die.
            logger.critical("Scheduler loop error; continuing.", exc_info=True)
            time.sleep(60)


if __name__ == "__main__":
    main()
```

## FILE: README.md
```markdown
# PEA Sniper Terminal — V-Prime 3.0

> **Sovereign execution. Kinetic risk management. Absolute quantitative transparency.**

Zero-leverage quantitative **decision support** for the French **PEA**. Market data →
deterministic quant engine → multi-layer risk cascade → **Discord Copilot** for
**manual** execution. A Bloomberg-style **Streamlit** terminal is the command center.

The system **never sends orders to a broker**. Maths decides *what* is worth
considering; AI only *explains*. **Not investment advice.**

---

## Table of contents

1. [Philosophy](#-philosophy)
2. [Feature map](#-feature-map)
3. [Strategy](#-strategy)
4. [Architecture](#-architecture)
5. [Module reference](#-module-reference)
6. [APIs that work](#-apis-that-work)
7. [Installation](#-installation)
8. [Configuration](#-configuration)
9. [Usage](#-usage)
10. [Dashboard](#-dashboard)
11. [Deployment](#-deployment)
12. [Scheduling](#-scheduling)
13. [Roadmap / future improvements](#-roadmap--future-improvements)
14. [Troubleshooting](#-troubleshooting)
15. [Disclaimer](#-disclaimer)

---

## Philosophy

1. **No fractional shares.** PEA sizing always uses `math.floor`.
2. **Math first, AI second.** LLMs never generate or approve trades — they explain,
   score news (−100…+100), and write the weekly digest.
3. **Official sources first.** Insider cascade is **AMF BDIF → FMP → yfinance**.
   Market OHLCV stays on `yfinance` → DuckDB. Scrapers are best-effort with
   circuit-breakers (AMF BDIF is often WAF-blocked).
4. **Split state.** DuckDB = OHLCV; SQLite = portfolio, audit log, **equity curve**.
5. **Zero crash tolerance.** A failed pass logs `CRITICAL`; the daemon keeps running.
6. **Manual execution.** You always have the last word (Discord buttons).

---

## Feature map

| Layer | Capability |
|------|------------|
| **Data** | OHLCV → DuckDB; VIX/VSTOXX; Put/Call; insiders **AMF→FMP→Yahoo**; Polymarket Gamma; Bourso profile/news |
| **Quant** | Mean-reversion exhaustion (RSI&lt;30 + Close&gt;SMA200 + Close&gt;SMA5), EPS&gt;0 |
| **Core/Satellite** | Smart DCA on `CW8.PA`, regime-aware under SMA200 |
| **Risk** | Macro veto, correlation firewall, sector/line caps, vol-parity sizing, 30% satellite budget, VIX panic |
| **Rebalance** | Daily ATR stop (`--atr-stops`); monthly +20% profit-shave |
| **Memory** | Daily equity curve + shared `equity_metrics` (DD/CAGR/Sharpe/Sortino) |
| **AI (explain only)** | Trade rationale, news sentiment, weekly CIO digest, geo brief |
| **UI** | Discord Copilot + Streamlit (multi-horizon, equity curve, Exploration, Universe) |
| **Ops** | Paris daemon, seed CLI, wallet editor, RevocationEngine on PENDING |

---

## Strategy

### 1. Core / Satellite
- **Core (≈70–75%)** — `CW8.PA` via Smart DCA: more aggressive below SMA200, drip above.
- **Satellite (≤30%)** — EU stock-picking under `SATELLITE_MAX_BUDGET_PCT`.

### 2. Satellite signal (all must hold)
Trend `Close > SMA200` · Exhaustion `RSI(14) < 30` · Quality `EPS > 0` · Momentum `Close > SMA5`.

### 3. Risk cascade (cheap checks first)
1. Live price exists  
2. VIX panic (`V2TX/VIX > 30`) freezes **new satellite buys** (Core still DCAs)  
3. Macro event blackout  
4. Sector / correlation caps  
5. Vol-parity sizing → whole shares → cash + satellite budget clamp  

### 4. Exits
- **Daily ATR stop:** losing satellite with `current < avg_entry − 2.5×ATR(14)` → SELL 100%.  
- **Monthly profit-shave:** satellite &gt; +20% → SELL 20%.  
- Core ETF excluded.

### 5. AI as analyst only
Trade explainer · news → integer score · Friday CIO digest → Discord webhook.

---

## Architecture

``​`
                       ┌──────────────────────────────────────┐
                       │            main_scheduler.py          │
                       │  (Paris: 09:00 / 13:30 / 17:10)       │
                       └───────────────┬──────────────────────┘
   00_data_sensors        01/02              03_risk_portfolio        04_orchestrator_ai
 ┌───────────────┐   ┌──────────────┐   ┌───────────────────────┐   ┌────────────────────┐
 │ market_prices │──▶│ DuckDB OHLCV │──▶│ correlation_firewall  │──▶│ signal_priority_    │
 │ macro_alpha   │   │ technical_   │   │ pea_position_sizer    │   │ cascade + macro     │
 │ AMF→FMP→YF    │   │ scorer+DCA   │   │ monthly ATR rebalancer│   │ revocation / LLM    │
 └───────────────┘   └──────────────┘   └───────────────────────┘   └─────────┬──────────┘
   SQLite: portfolio · audit · equity curve                                   ▼
                                      Discord Copilot · Streamlit terminal
``​`

**Per pass:** fetch → VIX → signals → mark-to-market (+ equity snapshot) → risk cascade
→ Smart-DCA → audit → Discord. **1st of month:** ATR/profit rebalance SELLs.

---

## Module reference

| Path | Responsibility |
|------|----------------|
| `00_data_sensors/market_prices_api.py` | Batch OHLCV → DuckDB |
| `00_data_sensors/macro_alpha_api.py` | VIX, Put/Call, insiders (**AMF→FMP→YF**), Polymarket |
| `00_data_sensors/scrapers/amf_scraper.py` | Official AMF BDIF insider scrape + 12h circuit |
| `01_memory_core/sqlite_portfolio.py` | Portfolio, audit log, **`portfolio_history` equity curve** |
| `01_memory_core/duckdb_manager.py` | OHLCV store (feeds ATR) |
| `02_quant_engine/technical_scorer.py` | MRE signals + quality/momentum |
| `02_quant_engine/smart_dca_engine.py` | Regime-aware Core DCA |
| `03_risk_portfolio/monthly_rebalancer.py` | Profit-shave + **2.5×ATR14** stops |
| `03_risk_portfolio/pea_position_sizer.py` | Half-Kelly × vol parity × PEA floor |
| `03_risk_portfolio/correlation_firewall.py` | Sector/Pearson + VIX panic |
| `04_orchestrator_ai/*` | Cascade, macro veto, revocation, sentiment, historian |
| `05_interfaces/terminal_dashboard.py` | Streamlit command center |
| `05_interfaces/discord_copilot.py` | Alerts + approve/revoke |
| `main_scheduler.py` | Daemon: passes, weekly, monthly |
| `tools/build_llm_dump.py` | Regenerate `PROJECT_FULL_DUMP_FOR_LLM.md` |
| `tools/sync_universe_from_bourso.py` | Refresh PEA universe YAML |

---

## APIs that work

| Source | Status | Notes |
|--------|--------|-------|
| **yfinance OHLCV** | Works | Primary market data → DuckDB |
| **`^V2TX` / `^VIX`** | Partial | VSTOXX often delisted on Yahoo → falls back to US VIX |
| **AMF BDIF** | Fragile | Official FR insiders; WAF/HTTP 500 common → circuit + fallbacks |
| **FMP insider API** | Optional | Needs `FMP_API_KEY`; secondary after AMF |
| **yfinance insiders** | Tertiary | Sparse on many `.PA` names |
| **Options Put/Call** | Partial | Sparse for EU → neutral `1.0` |
| **Polymarket Gamma** | Live | Macro context only |
| **OpenRouter** | Optional | Explanations / sentiment / weekly report |
| **TradingView / Yahoo news** | Works | UI embeds + radar |

Graceful degradation: missing sources return **neutral** values; the daemon does not crash.

---

## Installation

> Streamlit needs `pyarrow` → use **Python 3.11 or 3.12 x64** (`venv_x64`).

``​`bash
git clone https://github.com/Polluxgnr/Peatrading.git pea_sniper_terminal
cd pea_sniper_terminal

python3.11 -m venv venv_x64
# Windows:  venv_x64\Scripts\Activate.ps1
# Unix:     source venv_x64/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

cp config/api_keys.env.example config/api_keys.env
# edit secrets
``​`

---

## Configuration

### `config/api_keys.env` (git-ignored)

| Variable | Required | Purpose |
|----------|----------|---------|
| `DISCORD_TOKEN` / `DISCORD_CHANNEL_ID` | bot | Copilot with buttons |
| `DISCORD_WEBHOOK_URL` | daemon | Weekly + monthly notifications |
| `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` | optional | LLM explain / sentiment |
| `FMP_API_KEY` | optional | Secondary insider source |
| `EODHD_API_KEY` | optional | Reserved for paid market data |

### `config/risk_params.yaml`

Sizing · circuit breakers · correlation (`CORRELATION_LOOKBACK_DAYS`) · Core/Satellite · VIX ·  
`REBALANCE_PROFIT_*` · `REBALANCE_ATR_STOP_MULT` · `EARNINGS_BLACKOUT_DAYS` ·  
`MIN_LIQUIDITY_ADV` · `MAX_POSITIONS_TOTAL` · `RSI_OVERSOLD_THRESHOLD`.

### `config/pea_universe.yaml`

~600 PEA-eligible Euronext names by sector (synced via Bourso tools). Feeds firewall + dashboard.

---

## Usage

``​`bash
python seed_account.py --cash 10000          # seed once
python seed_account.py --show
python main_scheduler.py --now               # one pass
python main_scheduler.py --weekly
python main_scheduler.py --rebalance         # profit-shave now
python main_scheduler.py --atr-stops         # ATR stops now
python main_scheduler.py                     # daemon
python run_discord.py
.\run_dashboard.ps1                          # Streamlit (auto-open)
python tools/build_llm_dump.py               # refresh LLM dump
``​`

---

## Dashboard

| Tab | Content |
|-----|---------|
| **General & Signaux** | Adaptive multi-horizon suggestion (MICRO→FULL), Core card, geo, ledger, month news |
| **Portefeuille** | **Equity curve**, sunburst, positions, wallet editor → SQLite |
| **Exploration** | Liquid scan, ticker dossier, TA explain, news, insiders (AMF→FMP→YF), Polymarket |
| **Univers** | Full list + sector average performance |
| **Architecture** | Living docs (matches code) |

---

## Deployment

``​`bash
cp config/api_keys.env.example config/api_keys.env
docker compose up -d --build
# Dashboard :8501 · logs: docker compose logs -f daemon
docker compose exec daemon python seed_account.py --cash 10000
``​`

Or systemd / cron calling `main_scheduler.py --now` / `--weekly` / `--rebalance`.

---

## Scheduling

| Job | When (Europe/Paris) | Action |
|-----|---------------------|--------|
| Analysis | 09:00, 13:30, 17:10 weekdays | Full pipeline → Discord |
| Weekly report | Friday 18:00 | Historian → webhook |
| ATR stops | 08:35 weekdays | Dynamic ATR SELLs → webhook |
| Profit-shave | Probe 08:30 (acts on the 1st) | +20% trim → webhook |

---

## Roadmap / future improvements

Prioritized after Phase 15/16 wiring feedback. **Diff-only broker import** (never
blind overwrite). Prefer official/API sources over furtive HTML scraping.

### P0 — ship next / in progress

| Item | Notes |
|------|-------|
| **Daily ATR stops** | ✅ Split from monthly profit-shave; probe weekdays 08:35 (`--atr-stops`) |
| **pytest + CI** | ✅ Minimal suite + GitHub Actions; expand coverage continuously |
| **Equity metrics (shared)** | ✅ `equity_metrics.py` (max DD / CAGR / Sharpe / Sortino) on live curve — **same functions for the future backtester** |

### P0 / P1 — next up

| Item | Notes |
|------|-------|
| **Earnings / dividend blackout** | ✅ Engine + empty `earnings_calendar.yaml` + cascade hook; fill calendar (API later) |
| **Walk-forward backtester** | Biggest ROI: turns “system that runs” into “strategy validated empirically”; reuse `equity_metrics` |
| **Broker CSV import (diff)** | Diff Boursorama CSV vs SQLite; show missing/qty mismatches — **never blind overwrite** |

### New cascade / risk params (config ready)

| Key | Role |
|-----|------|
| `EARNINGS_BLACKOUT_DAYS` | Per-ticker corporate blackout window |
| `MIN_LIQUIDITY_ADV` | Floor on average daily € volume |
| `MAX_POSITIONS_TOTAL` | Cap on simultaneous satellite lines |
| `CORRELATION_LOOKBACK_DAYS` | Explicit Pearson window (was hardcoded 60) |
| `RSI_OVERSOLD_THRESHOLD` | Calibrable later via walk-forward (vol-regime adaptive) |

**ATR note:** stop distance uses **absolute** ATR (correct per name; ATR scales with
price). `atr_pct = ATR/price` is logged for cross-name comparison / dashboards —
use % for vol-parity style comparisons, absolute for the stop rule.

### Additional signals (post-backtester calibration)

| Signal | Role |
|--------|------|
| Relative strength vs sector / CAC40 (3–6m) | Filter structurally broken RSI&lt;30 names |
| Distance to 52w high/low | Cheap DuckDB confirmation beside SMA200/RSI |
| Analyst revision drift (`yfinance` upgrades) | Soft consensus signal, not a hard filter |
| EUR/USD context for `CW8.PA` | Info in weekly CIO digest (USD FX exposure), not a veto |

### Data sources (legal preference)

1. **Official / regulator** — AMF (done), Euronext corporate actions, ECB SDW / INSEE  
2. **Macro APIs with free tier** — Trading Economics → auto-sync `macro_calendar.yaml`  
3. **Structured commercial APIs** — FMP (already secondary for insiders), EODHD  
4. **HTML scrapers last** — Boursorama / Zonebourse / Investing: fragile + ToS grey zone; keep minimal  

### Dashboard visualizations (queued)

| Viz | Purpose |
|-----|---------|
| Signal funnel waterfall | raw → VIX → macro → earnings → liquidity → sector → corr → sizing → approved |
| Rejection motif pie (30/90d) | Expose `weekly_historian._classify` in UI |
| Per-ticker RSI/SMA200 sparkline | Audit false negatives with approve/reject markers |
| Rolling Sharpe / Sortino / DD | Built on `equity_metrics` |
| Richer ticker dossier | More `yfinance.info` fields, insider table (done path), per-article LLM scores, portfolio correlation heatmap |

### P2 / P3

Paid VSTOXX · AMF resilience · multi-core ETF rotation · trailing ATR after shave ·
intraday Discord veto digest.

**Non-goals:** auto-broker execution, leverage, LLM-as-trader, US pennies.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Dashboard « En attente… » | `python seed_account.py --cash 10000` |
| Empty equity curve | Appears after `update_portfolio` / wallet save / pipeline pass |
| `pyarrow` / Streamlit fail | Python **3.11/3.12 x64** |
| VIX stuck / `^V2TX` 404 | Falls back to `^VIX` |
| AMF HTTP 500 | Expected; FMP then Yahoo; circuit resets after ~12h |
| No FMP insiders | Set `FMP_API_KEY` in `api_keys.env` |
| ATR stop never fires | Need DuckDB history (`--now` fetch) + losing position |
| LLM / weekly silent | `OPENROUTER_API_KEY` / `DISCORD_WEBHOOK_URL` |
| Cash too small for CW8 | MICRO mode: 1 liquid share + cash runway (by design) |

---

## Disclaimer

Decision-support and educational tool only. **No automated execution. No financial
advice.** You are solely responsible for every trade. Past results do not guarantee
future performance.

© 2026 Pollux Quantitative Research — V-Prime 3.0 (Phase 15).
```

## FILE: requirements.txt
```text
# PEA Sniper Terminal V-Prime - Python 3.11+
# Phase 1 only needs pydantic + pyyaml; the rest is pinned for the roadmap.

# --- Core / data contracts (Phase 1) ---
pydantic>=2.6,<3.0
pyyaml>=6.0

# --- Memory core (Phase 2) ---
duckdb>=0.10
# sqlite3 is part of the Python standard library.

# --- Data sensors (Phase 3) ---
yfinance>=0.2.40
requests>=2.31
beautifulsoup4>=4.12
feedparser>=6.0

# --- Quant engine (Phase 4) ---
pandas>=2.1
numpy>=2.0
# pandas-ta-classic is the numpy-2.x / numba-free provider of the `.ta`
# accessor. Upstream `pandas-ta` 0.4.x requires numba (no py3.13/arm64 wheel).
pandas-ta-classic>=0.6.0

# --- Interfaces (Phases 7-8) ---
discord.py>=2.3
plotly>=5.20
matplotlib>=3.8   # required by pandas Styler.background_gradient in the dashboard
# streamlit needs pyarrow, which has NO prebuilt wheel for Python 3.13 / arm64.
# Use a Python 3.11/3.12 (x64) environment to install and run the dashboard.
streamlit>=1.33

# --- Scheduler (Phase 9) ---
schedule>=1.2

# --- Dev / tests ---
pytest>=8.0
```

## FILE: run_dashboard.ps1
```powershell
# Launch PEA Sniper Terminal dashboard.
# Streamlit opens the browser itself when headless=false — do NOT also Start-Process
# (that caused a double browser tab).
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$py = Join-Path $Root "venv_x64\Scripts\streamlit.exe"
if (-not (Test-Path $py)) {
    Write-Host "venv_x64 missing. Create it first (Python 3.11 x64)." -ForegroundColor Red
    exit 1
}

Write-Host "Starting PEA Sniper Terminal on http://localhost:8501 ..." -ForegroundColor Green
& $py run "05_interfaces/terminal_dashboard.py" --server.headless false --browser.gatherUsageStats false --server.port 8501
```

## FILE: run_discord.py
```python
"""Entry point to launch the PEA Sniper Terminal Discord Copilot.

Usage:
    1. Copy config/api_keys.env.example -> config/api_keys.env and fill in:
         DISCORD_TOKEN, DISCORD_CHANNEL_ID, OPENROUTER_API_KEY (optional)
    2. Run:  py run_discord.py

This starts the bot and keeps it connected. Actual signal alerts are pushed by
the scheduler (Phase 9) calling ``copilot.send_signal_alert(...)``. For a quick
manual smoke test, pass --demo to post one fake alert on ``on_ready``.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / "config" / "api_keys.env")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "05_interfaces"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "01_memory_core"))

from discord_copilot import DiscordCopilot  # noqa: E402
from llm_explainer import NarrativeExplainer  # noqa: E402
from sqlite_portfolio import PortfolioDB  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("run_discord")


def main() -> None:
    """Boot the Discord Copilot using credentials from the environment."""
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error(
            "DISCORD_TOKEN is not set. Copy config/api_keys.env.example to "
            "config/api_keys.env and fill it in."
        )
        raise SystemExit(1)

    portfolio_db = PortfolioDB()
    portfolio_db.init_db()

    copilot = DiscordCopilot(
        portfolio_db=portfolio_db,
        explainer=NarrativeExplainer(),
    )

    if "--demo" in sys.argv:
        _attach_demo(copilot)

    copilot.run(token)


def _attach_demo(copilot: "DiscordCopilot") -> None:
    """Post one synthetic alert once the bot is ready (manual smoke test)."""
    from datetime import datetime, timezone

    from data_models import PortfolioState, Signal, SignalStatus, SignalType

    async def _on_ready() -> None:
        logger.info("Demo mode: posting one synthetic alert.")
        signal = Signal(
            ticker="AI.PA", signal_type=SignalType.BUY, score=88.0,
            status=SignalStatus.APPROVED, target_qty=7,
            reason="RSI < 30 while Price > SMA200. Mean-reversion setup.",
        )
        portfolio = PortfolioState(
            cash_available=8000.0, total_equity=20000.0, positions=[],
            last_updated=datetime.now(timezone.utc),
        )
        await copilot.send_signal_alert(signal, portfolio, current_price=180.0)

    # Chain onto on_ready without losing the original logging behaviour.
    original_on_ready = copilot.on_ready

    async def _combined() -> None:
        await original_on_ready()
        await _on_ready()

    copilot.on_ready = _combined  # type: ignore[method-assign]


if __name__ == "__main__":
    main()
```

## FILE: seed_account.py
```python
"""Account seeding CLI for PEA Sniper Terminal V-Prime.

Bootstraps (or resets) the SQLite portfolio so the daemon, sizer and dashboard
have a real starting capital to work from. Without this, the account is empty
(0 EUR) and every BUY is rejected for "insufficient cash".

Examples:
    # Seed a fresh 10,000 EUR PEA, 100% cash:
    python seed_account.py --cash 10000

    # Reset everything and start over at 25,000 EUR:
    python seed_account.py --cash 25000 --reset

    # Seed cash AND an existing position (ticker:qty:avg_price:sector):
    python seed_account.py --cash 8000 --position MC.PA:3:620:Luxury

    # Show the current account state and exit:
    python seed_account.py --show
"""

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT / "01_memory_core"))

from data_models import Position, PortfolioState  # noqa: E402
from sqlite_portfolio import PortfolioDB  # noqa: E402

logger = logging.getLogger("seed_account")


def _parse_position(spec: str) -> Position:
    """Parse a ``TICKER:QTY:AVG_PRICE[:SECTOR]`` string into a Position."""
    parts = spec.split(":")
    if len(parts) < 3:
        raise argparse.ArgumentTypeError(
            f"Invalid position '{spec}'. Use TICKER:QTY:AVG_PRICE[:SECTOR]."
        )
    ticker, qty, avg = parts[0], int(parts[1]), float(parts[2])
    sector = parts[3] if len(parts) > 3 else "Unknown"
    return Position(
        ticker=ticker,
        qty_shares=qty,
        avg_entry_price=avg,
        current_price=avg,  # refreshed by the daemon on the next pass.
        sector=sector,
    )


def _print_state(state: PortfolioState) -> None:
    """Pretty-print a portfolio snapshot to stdout."""
    print("\n===== ACCOUNT STATE =====")
    print(f"  Total equity : {state.total_equity:,.2f} EUR")
    print(f"  Cash         : {state.cash_available:,.2f} EUR")
    print(f"  Positions    : {len(state.positions)}")
    for p in state.positions:
        print(
            f"    - {p.ticker:<10} {p.qty_shares:>4} @ {p.avg_entry_price:.2f} "
            f"({p.sector})"
        )
    print(f"  Last updated : {state.last_updated.isoformat()}\n")


def main() -> None:
    """Parse CLI args and seed / reset / display the account."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="Seed the PEA account state.")
    parser.add_argument("--cash", type=float, help="Cash to seed (EUR).")
    parser.add_argument(
        "--equity",
        type=float,
        default=None,
        help="Total equity (defaults to cash + positions value).",
    )
    parser.add_argument(
        "--position",
        action="append",
        default=[],
        metavar="TICKER:QTY:AVG[:SECTOR]",
        help="Seed an existing holding (repeatable).",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Wipe existing positions before seeding.",
    )
    parser.add_argument(
        "--show", action="store_true", help="Print current state and exit."
    )
    args = parser.parse_args()

    db = PortfolioDB()
    db.init_db()

    if args.show:
        _print_state(db.get_portfolio_state())
        return

    if args.cash is None:
        parser.error("Provide --cash to seed, or use --show to inspect.")

    existing = db.get_portfolio_state()
    positions = [] if args.reset else list(existing.positions)
    for spec in args.position:
        positions.append(_parse_position(spec))

    positions_value = sum(p.market_value for p in positions)
    total_equity = (
        args.equity if args.equity is not None else args.cash + positions_value
    )

    state = PortfolioState(
        cash_available=args.cash,
        total_equity=total_equity,
        positions=positions,
        last_updated=datetime.now(timezone.utc),
    )
    db.update_portfolio(state)
    logger.info("Account seeded successfully.")
    _print_state(db.get_portfolio_state())


if __name__ == "__main__":
    main()
```

## FILE: tests/__init__.py
```python
# Empty package marker for pytest discovery.
```

## FILE: tests/test_phase16_foundations.py
```python
"""Unit tests for equity metrics and rebalancer mode split."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
for sub in ("01_memory_core", "03_risk_portfolio", "04_orchestrator_ai"):
    sys.path.insert(0, str(ROOT / sub))

from equity_metrics import (  # noqa: E402
    compute_equity_metrics,
    max_drawdown,
    sharpe_ratio,
)
from monthly_rebalancer import PortfolioRebalancer  # noqa: E402
from earnings_blackout import EarningsBlackoutEngine  # noqa: E402
from data_models import Position, PortfolioState  # noqa: E402


def test_max_drawdown_and_sharpe_on_synthetic_curve():
    dates = pd.date_range("2025-01-01", periods=60, freq="B")
    # Rise then 20% drawdown then recover partially.
    eq = pd.Series(
        [100.0] * 10
        + list(range(100, 120))
        + [120 * 0.8] * 10
        + [100.0] * 20,
        index=dates[:60],
    )
    # Pad/trim to 60
    eq = eq.iloc[:60]
    dd = max_drawdown(eq)
    assert dd <= -0.15
    m = compute_equity_metrics(pd.DataFrame({"date": eq.index, "equity": eq.values}))
    assert m["n_points"] == 60
    assert m["max_drawdown"] <= -0.15
    assert m["sharpe"] is None or isinstance(m["sharpe"], float)


def test_rebalancer_modes_split_without_tsdb():
    cfg = ROOT / "config"
    rb = PortfolioRebalancer(cfg, timeseries_db=None)
    portfolio = PortfolioState(
        cash_available=1000,
        total_equity=5000,
        positions=[
            Position(
                ticker="MC.PA",
                qty_shares=10,
                avg_entry_price=100.0,
                current_price=125.0,
                sector="Luxury",
            ),
            Position(
                ticker="STLAP.PA",
                qty_shares=8,
                avg_entry_price=20.0,
                current_price=17.0,
                sector="Auto",
            ),
        ],
        last_updated=datetime.now(timezone.utc),
    )
    shaves = rb.generate_profit_shave_signals(portfolio)
    atrs = rb.generate_atr_stop_signals(portfolio)
    assert len(shaves) == 1 and shaves[0].ticker == "MC.PA"
    # No DuckDB -> ATR stops cannot fire.
    assert atrs == []


def test_earnings_blackout_window(tmp_path):
    risk = tmp_path / "risk_params.yaml"
    risk.write_text("EARNINGS_BLACKOUT_DAYS: 2\n", encoding="utf-8")
    cal = tmp_path / "earnings_calendar.yaml"
    cal.write_text(
        "events:\n  MC.PA:\n    2026-07-25: \"Q2 earnings\"\n",
        encoding="utf-8",
    )
    eng = EarningsBlackoutEngine(tmp_path)
    from datetime import date

    veto, reason = eng.check_veto("MC.PA", date(2026, 7, 24))
    assert veto and "Q2" in reason
    clear, _ = eng.check_veto("OR.PA", date(2026, 7, 24))
    assert not clear
```

## FILE: tools/build_llm_dump.py
```python
#!/usr/bin/env python3
"""Regenerate PROJECT_FULL_DUMP_FOR_LLM.md for one-shot LLM context.

Usage (from repo root):
    python tools/build_llm_dump.py
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "PROJECT_FULL_DUMP_FOR_LLM.md"

SKIP_DIRS = {
    ".git",
    "venv_x64",
    "venv",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    ".cursor",
    "database",
    "mcps",
    "agent-transcripts",
    "terminals",
}

EXTS = {
    ".py",
    ".yaml",
    ".yml",
    ".toml",
    ".md",
    ".txt",
    ".ps1",
    ".json",
    ".ini",
    ".cfg",
}

NAME_ALLOW = {
    "Dockerfile",
    "docker-compose.yml",
    "requirements.txt",
    "api_keys.env.example",
    ".gitignore",
}

# Never embed the dump inside itself, or huge generated noise.
SKIP_FILES = {
    "PROJECT_FULL_DUMP_FOR_LLM.md",
}


def _lang(path: Path) -> str:
    return {
        ".py": "python",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".md": "markdown",
        ".txt": "text",
        ".ps1": "powershell",
        ".json": "json",
        ".ini": "ini",
        ".cfg": "ini",
    }.get(path.suffix.lower(), "text")


def _should_include(path: Path) -> bool:
    if path.name in SKIP_FILES:
        return False
    if any(part in SKIP_DIRS for part in path.parts):
        return False
    if path.name in NAME_ALLOW:
        return True
    if path.suffix.lower() in EXTS:
        # Prefer the example secrets file only (never real .env).
        if path.suffix.lower() == ".env" or path.name.endswith(".env"):
            return path.name.endswith(".env.example")
        return True
    return False


def collect_files() -> list[Path]:
    files: list[Path] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if _should_include(rel):
            files.append(rel)
    return files


def main() -> None:
    files = collect_files()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        "# PEA Sniper Terminal — Full Project Dump for LLM",
        f"Root: `{ROOT}`",
        f"Generated: {stamp}",
        "One-shot context dump of source, configs, and docs (no venv, no DBs, no secrets).",
        "---",
        f"## File index ({len(files)} files)",
    ]
    for rel in files:
        lines.append(f"- {rel.as_posix()}")
    lines.append("")
    lines.append("---")

    for rel in files:
        abs_path = ROOT / rel
        try:
            text = abs_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        # Fence safety: close any accidental triple-backticks in source.
        safe = text.replace("``​`", "``\u200b`")
        lines.append(f"## FILE: {rel.as_posix()}")
        lines.append(f"``​`{_lang(rel)}")
        lines.append(safe.rstrip() + "\n``​`")
        lines.append("")

    OUT.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    size_kb = OUT.stat().st_size / 1024
    print(f"Wrote {OUT.name}: {len(files)} files, {size_kb:.0f} KB")


if __name__ == "__main__":
    main()
```

## FILE: tools/build_universe.py
```python
"""Universe builder for PEA Sniper Terminal V-Prime.

Writes ``config/pea_universe.yaml`` from a CURATED, authoritative map of
Euronext Paris tickers (correctness > automation: yfinance search often returns
low-liquidity foreign listings for French blue chips). Every ticker is validated
against Yahoo Finance before being written, and any symbol that no longer returns
price data is dropped and reported.

Run:
    python tools/build_universe.py
"""

import logging
from collections import defaultdict
from pathlib import Path

import yaml
import yfinance as yf

logger = logging.getLogger("build_universe")

_ROOT = Path(__file__).resolve().parent.parent
_UNIVERSE_PATH = _ROOT / "config" / "pea_universe.yaml"

# (ticker, display name, sector) - curated Euronext Paris universe.
_CURATED: list[tuple[str, str, str]] = [
    # --- Consumer Cyclical ---
    ("AC.PA", "Accor", "Consumer Cyclical"),
    ("AKW.PA", "Akwel", "Consumer Cyclical"),
    ("ALCAT.PA", "Catana Group", "Consumer Cyclical"),
    ("ALHEX.PA", "Hexaom", "Consumer Cyclical"),
    ("BB.PA", "Bic", "Consumer Cyclical"),
    ("BEN.PA", "Beneteau", "Consumer Cyclical"),
    ("CDA.PA", "Compagnie des Alpes", "Consumer Cyclical"),
    ("CDI.PA", "Christian Dior", "Consumer Cyclical"),
    ("FDJU.PA", "FDJ United", "Consumer Cyclical"),
    ("FNAC.PA", "Fnac Darty", "Consumer Cyclical"),
    ("FR.PA", "Valeo", "Consumer Cyclical"),
    ("FRVIA.PA", "Forvia", "Consumer Cyclical"),
    ("KER.PA", "Kering", "Consumer Cyclical"),
    ("MC.PA", "LVMH", "Consumer Cyclical"),
    ("MMB.PA", "Lagardere", "Consumer Cyclical"),
    ("OPM.PA", "OPmobility", "Consumer Cyclical"),
    ("RMS.PA", "Hermes International", "Consumer Cyclical"),
    ("RNO.PA", "Renault", "Consumer Cyclical"),
    ("STLAP.PA", "Stellantis", "Consumer Cyclical"),
    ("TFF.PA", "TFF Group", "Consumer Cyclical"),
    ("TRI.PA", "Trigano", "Consumer Cyclical"),
    ("VAC.PA", "Pierre et Vacances", "Consumer Cyclical"),
    # --- Consumer Defensive ---
    ("BN.PA", "Danone", "Consumer Defensive"),
    ("BOI.PA", "Boiron", "Consumer Defensive"),
    ("BON.PA", "Bonduelle", "Consumer Defensive"),
    ("CA.PA", "Carrefour", "Consumer Defensive"),
    ("CO.PA", "Casino Guichard", "Consumer Defensive"),
    ("ITP.PA", "Interparfums", "Consumer Defensive"),
    ("LOUP.PA", "LDC", "Consumer Defensive"),
    ("MBWS.PA", "Marie Brizard", "Consumer Defensive"),
    ("OR.PA", "L'Oreal", "Consumer Defensive"),
    ("RCO.PA", "Remy Cointreau", "Consumer Defensive"),
    ("RI.PA", "Pernod Ricard", "Consumer Defensive"),
    ("SAVE.PA", "Savencia", "Consumer Defensive"),
    ("SBT.PA", "Oeneo", "Consumer Defensive"),
    # --- Financial Services ---
    ("ABCA.PA", "ABC Arbitrage", "Financial Services"),
    ("ACA.PA", "Credit Agricole", "Financial Services"),
    ("AMUN.PA", "Amundi", "Financial Services"),
    ("BNP.PA", "BNP Paribas", "Financial Services"),
    ("COFA.PA", "Coface", "Financial Services"),
    ("CS.PA", "AXA", "Financial Services"),
    ("EDEN.PA", "Edenred", "Financial Services"),
    ("ENX.PA", "Euronext", "Financial Services"),
    ("GLE.PA", "Societe Generale", "Financial Services"),
    ("LTA.PA", "Altamir", "Financial Services"),
    ("MF.PA", "Wendel", "Financial Services"),
    ("PEUG.PA", "Peugeot Invest", "Financial Services"),
    ("RF.PA", "Eurazeo", "Financial Services"),
    ("TKO.PA", "Tikehau Capital", "Financial Services"),
    # --- Healthcare ---
    ("AB.PA", "AB Science", "Healthcare"),
    ("ADOC.PA", "Adocia", "Healthcare"),
    ("BIM.PA", "bioMerieux", "Healthcare"),
    ("BLIRD.PA", "Lumibird", "Healthcare"),  # validated below; corrected to LBIRD
    ("CGM.PA", "Cegedim", "Healthcare"),
    ("CLARI.PA", "Clariane", "Healthcare"),
    ("DBV.PA", "DBV Technologies", "Healthcare"),
    ("DIM.PA", "Sartorius Stedim Biotech", "Healthcare"),
    ("EAPI.PA", "EuroAPI", "Healthcare"),
    ("EL.PA", "EssilorLuxottica", "Healthcare"),
    ("EMEIS.PA", "Emeis", "Healthcare"),
    ("ERF.PA", "Eurofins Scientific", "Healthcare"),
    ("GDS.PA", "Ramsay Generale de Sante", "Healthcare"),
    ("GNFT.PA", "Genfit", "Healthcare"),
    ("IPH.PA", "Innate Pharma", "Healthcare"),
    ("IPN.PA", "Ipsen", "Healthcare"),
    ("LNA.PA", "LNA Sante", "Healthcare"),
    ("NANO.PA", "Nanobiotix", "Healthcare"),
    ("OSE.PA", "OSE Immunotherapeutics", "Healthcare"),
    ("SAN.PA", "Sanofi", "Healthcare"),
    ("VETO.PA", "Vetoquinol", "Healthcare"),
    ("VIRP.PA", "Virbac", "Healthcare"),
    ("VLA.PA", "Valneva", "Healthcare"),
    # --- Industrials ---
    ("ADP.PA", "Aeroports de Paris", "Industrials"),
    ("AF.PA", "Air France-KLM", "Industrials"),
    ("AIR.PA", "Airbus", "Industrials"),
    ("ALCIS.PA", "Catering International Services", "Industrials"),
    ("ALEXA.PA", "Exail Technologies", "Industrials"),
    ("ALO.PA", "Alstom", "Industrials"),
    ("AM.PA", "Dassault Aviation", "Industrials"),
    ("ASY.PA", "Assystem", "Industrials"),
    ("AYV.PA", "Ayvens", "Industrials"),
    ("BVI.PA", "Bureau Veritas", "Industrials"),
    ("CEN.PA", "Groupe CRIT", "Industrials"),
    ("CRI.PA", "Chargeurs", "Industrials"),
    ("DG.PA", "Vinci", "Industrials"),
    ("ELIS.PA", "Elis", "Industrials"),
    ("EN.PA", "Bouygues", "Industrials"),
    ("EXE.PA", "Exel Industries", "Industrials"),
    ("FGR.PA", "Eiffage", "Industrials"),
    ("GLO.PA", "GL Events", "Industrials"),
    ("HO.PA", "Thales", "Industrials"),
    ("IDL.PA", "ID Logistics", "Industrials"),
    ("IPS.PA", "Ipsos", "Industrials"),
    ("LR.PA", "Legrand", "Industrials"),
    ("MRN.PA", "Mersen", "Industrials"),
    ("MTU.PA", "Manitou", "Industrials"),
    ("PIG.PA", "Haulotte Group", "Industrials"),
    ("RXL.PA", "Rexel", "Industrials"),
    ("SAF.PA", "Safran", "Industrials"),
    ("SCHP.PA", "Seche Environnement", "Industrials"),
    ("SGO.PA", "Saint-Gobain", "Industrials"),
    ("SPIE.PA", "Spie", "Industrials"),
    ("STF.PA", "STEF", "Industrials"),
    ("SU.PA", "Schneider Electric", "Industrials"),
    ("SW.PA", "Sodexo", "Industrials"),
    ("TEP.PA", "Teleperformance", "Industrials"),
    ("VIE.PA", "Veolia", "Industrials"),
    # --- Technology ---
    ("74SW.PA", "74Software", "Technology"),
    ("ALPRG.PA", "Prologue", "Technology"),
    ("ATE.PA", "Alten", "Technology"),
    ("AUB.PA", "Aubay", "Technology"),
    ("AVT.PA", "Avenir Telecom", "Technology"),
    ("BIG.PA", "Bigben Interactive", "Technology"),
    ("CAP.PA", "Capgemini", "Technology"),
    ("DSY.PA", "Dassault Systemes", "Technology"),
    ("EKI.PA", "Ekinops", "Technology"),
    ("LSS.PA", "Lectra", "Technology"),
    ("NRO.PA", "Neurones", "Technology"),
    ("QDT.PA", "Quadient", "Technology"),
    ("S30.PA", "Solutions 30", "Technology"),
    ("SOI.PA", "Soitec", "Technology"),
    ("SOP.PA", "Sopra Steria", "Technology"),
    ("STMPA.PA", "STMicroelectronics", "Technology"),
    ("SWP.PA", "Sword Group", "Technology"),
    ("VMX.PA", "Verimatrix", "Technology"),
    ("VU.PA", "VusionGroup", "Technology"),
    ("WAVE.PA", "Wavestone", "Technology"),
    ("WLN.PA", "Worldline", "Technology"),
    # --- Communication Services ---
    ("BOL.PA", "Bollore", "Communication Services"),
    ("DEC.PA", "JCDecaux", "Communication Services"),
    ("ETL.PA", "Eutelsat", "Communication Services"),
    ("LOCAL.PA", "Solocal", "Communication Services"),
    ("MMT.PA", "M6 Metropole Television", "Communication Services"),
    ("ODET.PA", "Compagnie de l'Odet", "Communication Services"),
    ("ORA.PA", "Orange", "Communication Services"),
    ("PRC.PA", "Artmarket.com", "Communication Services"),
    ("PUB.PA", "Publicis Groupe", "Communication Services"),
    ("TFI.PA", "TF1", "Communication Services"),
    ("UBI.PA", "Ubisoft", "Communication Services"),
    # --- Basic Materials ---
    ("AI.PA", "Air Liquide", "Basic Materials"),
    ("AKE.PA", "Arkema", "Basic Materials"),
    ("ERA.PA", "Eramet", "Basic Materials"),
    ("JCQ.PA", "Jacquet Metals", "Basic Materials"),
    ("NK.PA", "Imerys", "Basic Materials"),
    ("VCT.PA", "Vicat", "Basic Materials"),
    ("VK.PA", "Vallourec", "Basic Materials"),
    # --- Energy ---
    ("GTT.PA", "GTT", "Energy"),
    ("MAU.PA", "Maurel et Prom", "Energy"),
    ("RUI.PA", "Rubis", "Energy"),
    ("TE.PA", "Technip Energies", "Energy"),
    ("TTE.PA", "TotalEnergies", "Energy"),
    # --- Utilities ---
    ("ENGI.PA", "Engie", "Utilities"),
    ("VLTSA.PA", "Voltalia", "Utilities"),
    # --- Real Estate ---
    ("EIFF.PA", "Societe de la Tour Eiffel", "Real Estate"),
    ("NXI.PA", "Nexity", "Real Estate"),
    # --- ETF sleeve (PEA-eligible; core + broad indices) ---
    ("CW8.PA", "Amundi MSCI World UCITS ETF (Core)", "ETF"),
    ("WPEA.PA", "iShares MSCI World Swap PEA UCITS ETF", "ETF"),
    ("PE500.PA", "Amundi PEA S&P 500 UCITS ETF", "ETF"),
    ("ESE.PA", "BNP Paribas Easy S&P 500 UCITS ETF", "ETF"),
    ("PUST.PA", "Amundi PEA Nasdaq-100 UCITS ETF", "ETF"),
    ("PANX.PA", "Amundi Nasdaq-100 UCITS ETF", "ETF"),
    ("CAC.PA", "Amundi CAC 40 UCITS ETF", "ETF"),
    ("C50.PA", "Amundi Euro Stoxx 50 UCITS ETF", "ETF"),
    ("PCEU.PA", "Amundi PEA MSCI Europe UCITS ETF", "ETF"),
    ("PAEEM.PA", "Amundi PEA Emerging Markets UCITS ETF", "ETF"),
    ("PAASI.PA", "Amundi PEA Asie Emergente UCITS ETF", "ETF"),
    ("PABZ.PA", "Amundi PEA MSCI USA UCITS ETF", "ETF"),
    ("LYPS.DE", "Amundi S&P 500 UCITS ETF", "ETF"),
]

# Corrections applied after a first validation pass (typo -> real symbol).
_FIXUPS = {"BLIRD.PA": "LBIRD.PA", "CGM.PA": "ALCGM.PA"}


def validate(symbols: list[str]) -> set[str]:
    """Return the subset of symbols that return recent price data."""
    good: set[str] = set()
    try:
        data = yf.download(symbols, period="5d", progress=False,
                           auto_adjust=False, group_by="ticker", threads=True)
    except Exception:  # noqa: BLE001
        data = None
    for sym in symbols:
        ok = False
        try:
            lvl0 = data.columns.get_level_values(0) if data is not None else []
            if sym in lvl0 and not data[sym]["Close"].dropna().empty:
                ok = True
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            try:
                hist = yf.Ticker(sym).history(period="5d")
                ok = hist is not None and not hist.empty
            except Exception:  # noqa: BLE001
                ok = False
        if ok:
            good.add(sym)
    return good


def main() -> None:
    """Validate the curated list and write the universe YAML."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    rows = [(_FIXUPS.get(t, t), n, s) for t, n, s in _CURATED]
    symbols = [t for t, _, _ in rows]
    logger.info("Validating %d curated tickers...", len(symbols))
    good = validate(symbols)
    dropped = [t for t in symbols if t not in good]
    if dropped:
        logger.warning("Dropped %d invalid tickers (verify manually): %s",
                       len(dropped), ", ".join(dropped))

    buckets: dict[str, list[dict]] = defaultdict(list)
    for ticker, name, sector in rows:
        if ticker in good:
            buckets[sector].append({"ticker": ticker, "name": name})

    payload = {"universe": {k: buckets[k] for k in sorted(buckets)}}
    with open(_UNIVERSE_PATH, "w", encoding="utf-8") as fh:
        fh.write("# PEA Sniper Terminal V-Prime - investable universe\n")
        fh.write("# Curated Euronext Paris tickers, validated against Yahoo "
                 "Finance.\n")
        fh.write("# Regenerate with: python tools/build_universe.py\n\n")
        yaml.safe_dump(payload, fh, allow_unicode=True, sort_keys=False,
                       default_flow_style=False)

    total = sum(len(v) for v in buckets.values())
    logger.info("Wrote %d tickers across %d sectors to %s",
                total, len(buckets), _UNIVERSE_PATH)


if __name__ == "__main__":
    main()
```

## FILE: tools/sync_universe_from_bourso.py
```python
"""Sync ``config/pea_universe.yaml`` from Boursorama's PEA eligibility filter.

Harvests ``quotation_az_filter[peaEligibility]=1`` across SRD / compartments /
PEA-PME, maps Bourso slugs to Yahoo tickers, validates live prices, and merges
into the existing universe (keeps known sectors/names when possible).

Run:
    python tools/sync_universe_from_bourso.py
    python tools/sync_universe_from_bourso.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from pathlib import Path

import yaml
import yfinance as yf

_ROOT = Path(__file__).resolve().parent.parent
_SCRAPERS = _ROOT / "00_data_sensors" / "scrapers"
_UNIVERSE = _ROOT / "config" / "pea_universe.yaml"
sys.path.insert(0, str(_SCRAPERS))

from bourso_scraper import BoursoramaScraper  # noqa: E402

logger = logging.getLogger("sync_universe")

# Map Bourso French activity labels → our sector buckets.
_SECTOR_MAP = {
    "technologie": "Technology",
    "logiciel": "Technology",
    "semiconduct": "Technology",
    "santé": "Healthcare",
    "sante": "Healthcare",
    "pharma": "Healthcare",
    "biotechn": "Healthcare",
    "banque": "Financial Services",
    "assurance": "Financial Services",
    "finance": "Financial Services",
    "investissement": "Financial Services",
    "pétrol": "Energy",
    "petrol": "Energy",
    "gaz": "Energy",
    "énergie": "Utilities",
    "energie": "Utilities",
    "utilit": "Utilities",
    "immobilier": "Real Estate",
    "fonci": "Real Estate",
    "télécom": "Communication Services",
    "telecom": "Communication Services",
    "média": "Communication Services",
    "media": "Communication Services",
    "publicité": "Communication Services",
    "luxe": "Consumer Cyclical",
    "automobile": "Consumer Cyclical",
    "voyage": "Consumer Cyclical",
    "loisir": "Consumer Cyclical",
    "distribution": "Consumer Defensive",
    "alimentaire": "Consumer Defensive",
    "boisson": "Consumer Defensive",
    "chimie": "Basic Materials",
    "matériaux": "Basic Materials",
    "materiaux": "Basic Materials",
    "mines": "Basic Materials",
    "industrie": "Industrials",
    "construction": "Industrials",
    "aéro": "Industrials",
    "aero": "Industrials",
    "transport": "Industrials",
}


def _guess_sector(label: str | None) -> str:
    if not label:
        return "Divers"
    low = label.lower()
    for needle, sector in _SECTOR_MAP.items():
        if needle in low:
            return sector
    return "Divers"


def _yf_sector(ticker: str) -> str | None:
    try:
        info = yf.Ticker(ticker).info or {}
        return info.get("sector")
    except Exception:  # noqa: BLE001
        return None


def _validate(symbols: list[str]) -> set[str]:
    good: set[str] = set()
    if not symbols:
        return good
    # Batch in chunks to avoid huge downloads.
    chunk_size = 80
    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i: i + chunk_size]
        try:
            data = yf.download(
                chunk, period="5d", progress=False,
                auto_adjust=False, group_by="ticker", threads=True,
            )
        except Exception:  # noqa: BLE001
            data = None
        for sym in chunk:
            ok = False
            try:
                if data is not None and sym in data.columns.get_level_values(0):
                    if not data[sym]["Close"].dropna().empty:
                        ok = True
            except Exception:  # noqa: BLE001
                ok = False
            if not ok:
                try:
                    hist = yf.Ticker(sym).history(period="5d")
                    ok = hist is not None and not hist.empty
                except Exception:  # noqa: BLE001
                    ok = False
            if ok:
                good.add(sym)
    return good


def _load_existing() -> dict[str, dict]:
    """Return ticker -> {name, sector} from current YAML."""
    if not _UNIVERSE.exists():
        return {}
    data = yaml.safe_load(_UNIVERSE.read_text(encoding="utf-8")) or {}
    out: dict[str, dict] = {}
    for sector, members in (data.get("universe") or {}).items():
        for e in members or []:
            t = e.get("ticker")
            if t:
                out[t] = {"name": e.get("name", t), "sector": sector,
                          "pea_pme": e.get("pea_pme"), "srd": e.get("srd")}
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-validate", action="store_true",
                        help="Skip Yahoo live-price validation (faster).")
    args = parser.parse_args()

    logger.info("Harvesting Boursorama PEA eligibility listings…")
    rows = BoursoramaScraper().get_pea_universe(include_pea_pme=True)
    logger.info("Raw Bourso PEA rows: %d", len(rows))

    existing = _load_existing()
    # Preserve ETF sleeve from current universe.
    etf_keep = {
        t: meta for t, meta in existing.items()
        if meta.get("sector") == "ETF"
    }

    by_ticker: dict[str, dict] = {}
    for row in rows:
        yahoo = row["yahoo"]
        by_ticker[yahoo] = {
            "name": row["name"],
            "sector": existing.get(yahoo, {}).get("sector") or "Divers",
            "pea_pme": row.get("pea_pme") == "true",
            "srd": row.get("market") == "SRD",
            "bourso_sector": None,
        }

    tickers = sorted(by_ticker)
    if args.skip_validate:
        good = set(tickers)
    else:
        logger.info("Validating %d tickers on Yahoo Finance…", len(tickers))
        good = _validate(tickers)
        dropped = set(tickers) - good
        if dropped:
            logger.warning("Dropped %d invalid: %s",
                           len(dropped), ", ".join(sorted(list(dropped)[:20])))

    # Sector enrichment for unknowns.
    for t in sorted(good):
        meta = by_ticker[t]
        if meta["sector"] in ("Divers", None) or t not in existing:
            yf_sec = _yf_sector(t)
            if yf_sec:
                meta["sector"] = yf_sec
            # light rate-limit courtesy
        if t in existing and existing[t]["sector"] not in ("Divers", "Unknown"):
            meta["sector"] = existing[t]["sector"]
            meta["name"] = existing[t]["name"] or meta["name"]

    # Re-attach ETFs.
    for t, meta in etf_keep.items():
        by_ticker[t] = {
            "name": meta["name"], "sector": "ETF",
            "pea_pme": False, "srd": False,
        }
        good.add(t)

    buckets: dict[str, list[dict]] = defaultdict(list)
    for t in sorted(good):
        meta = by_ticker[t]
        entry = {"ticker": t, "name": meta["name"]}
        if meta.get("pea_pme"):
            entry["pea_pme"] = True
        if meta.get("srd"):
            entry["srd"] = True
        buckets[meta["sector"] or "Divers"].append(entry)

    payload = {"universe": {k: buckets[k] for k in sorted(buckets)}}
    total = sum(len(v) for v in buckets.values())
    logger.info("Universe ready: %d tickers across %d sectors", total, len(buckets))

    if args.dry_run:
        for sec, members in list(payload["universe"].items())[:5]:
            logger.info("  %s: %d (e.g. %s)", sec, len(members),
                        ", ".join(m["ticker"] for m in members[:3]))
        return

    with open(_UNIVERSE, "w", encoding="utf-8") as fh:
        fh.write("# PEA Sniper Terminal V-Prime - investable universe\n")
        fh.write("# Synced from Boursorama Eligibilité PEA filter "
                 "(tools/sync_universe_from_bourso.py).\n")
        fh.write("# Extra flags: srd=true (liquid SRD), pea_pme=true.\n\n")
        yaml.safe_dump(payload, fh, allow_unicode=True, sort_keys=False,
                       default_flow_style=False)
    logger.info("Wrote %s", _UNIVERSE)


if __name__ == "__main__":
    main()
```
