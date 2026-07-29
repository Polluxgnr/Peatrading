"""Symbol mapper using OpenFIGI API — resolves Yahoo tickers to ISIN/FIGI.

Caches results in SQLite to avoid rate limits (200 req/min free tier).
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = _ROOT / "database" / "portfolio.db"


class SymbolMapper:
    """Maps Yahoo Finance tickers to ISIN/FIGI/Finnhub symbols via OpenFIGI."""

    OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"

    def __init__(self) -> None:
        self.api_key = (os.getenv("OPENFIGI_API_KEY") or "").strip()
        self._session = requests.Session()
        if self.api_key:
            self._session.headers["X-OPENFIGI-APIKEY"] = self.api_key
        self._ensure_table()

    def _ensure_table(self) -> None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(_DB_PATH)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS symbol_map (
                    yahoo_ticker TEXT PRIMARY KEY,
                    isin TEXT,
                    figi TEXT,
                    finnhub_symbol TEXT,
                    name TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

    def _cache_get(self, ticker: str) -> dict | None:
        try:
            with sqlite3.connect(str(_DB_PATH)) as conn:
                row = conn.execute(
                    "SELECT isin, figi, finnhub_symbol, name FROM symbol_map WHERE yahoo_ticker = ?",
                    (ticker,),
                ).fetchone()
            if row:
                return {"isin": row[0], "figi": row[1], "finnhub_symbol": row[2], "name": row[3]}
        except Exception:  # noqa: BLE001
            pass
        return None

    def _cache_put(self, ticker: str, data: dict) -> None:
        try:
            with sqlite3.connect(str(_DB_PATH)) as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO symbol_map
                       (yahoo_ticker, isin, figi, finnhub_symbol, name, updated_at)
                       VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                    (ticker, data.get("isin"), data.get("figi"),
                     data.get("finnhub_symbol"), data.get("name")),
                )
        except Exception:  # noqa: BLE001
            pass

    def _yahoo_to_exchange(self, ticker: str) -> tuple[str, str]:
        """Parse 'MC.PA' -> ('MC', 'PA') exchange code."""
        parts = ticker.rsplit(".", 1)
        if len(parts) == 2:
            return parts[0], parts[1]
        return ticker, ""

    def _exchange_to_mic(self, exch: str) -> str:
        mapping = {
            "PA": "XPAR", "AS": "XAMS", "DE": "XETR", "MI": "XMIL",
            "BR": "XBRU", "LS": "XLIS", "MC": "XMAD", "HE": "XHEL",
        }
        return mapping.get(exch.upper(), "")

    def resolve(self, ticker: str) -> dict:
        """Return {'isin', 'figi', 'finnhub_symbol', 'name'} for a Yahoo ticker."""
        cached = self._cache_get(ticker)
        if cached:
            return cached

        symbol, exch = self._yahoo_to_exchange(ticker)
        mic = self._exchange_to_mic(exch)

        payload = [{"idType": "TICKER", "idValue": symbol}]
        if mic:
            payload[0]["exchCode"] = mic

        try:
            resp = self._session.post(
                self.OPENFIGI_URL,
                json=payload,
                timeout=10,
            )
            if resp.status_code != 200:
                logger.debug("OpenFIGI HTTP %s for %s", resp.status_code, ticker)
                return {"isin": None, "figi": None, "finnhub_symbol": None, "name": None}

            results = resp.json()
            if not results or not isinstance(results, list):
                return {"isin": None, "figi": None, "finnhub_symbol": None, "name": None}

            data_list = results[0].get("data", [])
            if not data_list:
                return {"isin": None, "figi": None, "finnhub_symbol": None, "name": None}

            best = data_list[0]
            out = {
                "isin": best.get("shareClassFIGI") or None,
                "figi": best.get("figi") or None,
                "finnhub_symbol": ticker,  # Finnhub uses Yahoo format for most EU
                "name": best.get("name") or None,
            }
            self._cache_put(ticker, out)
            return out

        except Exception as exc:  # noqa: BLE001
            logger.debug("OpenFIGI failed for %s: %s", ticker, exc)
            return {"isin": None, "figi": None, "finnhub_symbol": None, "name": None}

    def resolve_batch(self, tickers: list[str]) -> dict[str, dict]:
        """Resolve multiple tickers, using cache where possible."""
        results = {}
        to_fetch = []
        for t in tickers:
            cached = self._cache_get(t)
            if cached:
                results[t] = cached
            else:
                to_fetch.append(t)

        # OpenFIGI accepts up to 100 items per request
        for i in range(0, len(to_fetch), 100):
            batch = to_fetch[i:i + 100]
            payload = []
            for t in batch:
                symbol, exch = self._yahoo_to_exchange(t)
                mic = self._exchange_to_mic(exch)
                entry = {"idType": "TICKER", "idValue": symbol}
                if mic:
                    entry["exchCode"] = mic
                payload.append(entry)

            try:
                resp = self._session.post(self.OPENFIGI_URL, json=payload, timeout=15)
                if resp.status_code != 200:
                    continue
                api_results = resp.json()
                for j, t in enumerate(batch):
                    if j >= len(api_results):
                        break
                    data_list = api_results[j].get("data", [])
                    if data_list:
                        best = data_list[0]
                        out = {
                            "isin": best.get("shareClassFIGI"),
                            "figi": best.get("figi"),
                            "finnhub_symbol": t,
                            "name": best.get("name"),
                        }
                    else:
                        out = {"isin": None, "figi": None, "finnhub_symbol": t, "name": None}
                    self._cache_put(t, out)
                    results[t] = out
            except Exception:  # noqa: BLE001
                pass

        return results
