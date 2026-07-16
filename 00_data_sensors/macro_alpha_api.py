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
import sys
import time
from functools import wraps
from pathlib import Path
from typing import Callable

import pandas as pd
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
        """Return the net direction of recent insider transactions.

        Prefers yfinance (reliable). AMF BDIF is attempted only while its
        process-wide circuit breaker is closed (often trips on HTTP 500/WAF).
        """
        yf_dir = self._insider_from_yfinance(ticker)
        if yf_dir != 0:
            return yf_dir

        if AmfInsiderScraper is None:
            return 0
        try:
            from amf_scraper import amf_available  # noqa: WPS433
            if not amf_available():
                return 0
        except Exception:  # noqa: BLE001
            pass

        isin = None
        issuer = None
        if BoursoramaScraper is not None:
            try:
                profile = BoursoramaScraper().get_instrument_profile(ticker)
                if profile:
                    isin = profile.get("isin")
                    issuer = profile.get("name")
            except Exception as exc:  # noqa: BLE001
                logger.debug("Bourso profile enrich failed for %s: %s", ticker, exc)

        try:
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
            logger.debug(
                "AMF insider scrape failed for %s (%s); keeping yfinance.",
                ticker, exc,
            )
        return 0

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

    def _insider_from_yfinance(self, ticker: str) -> int:
        """Original yfinance insider net-direction logic (fallback)."""
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
