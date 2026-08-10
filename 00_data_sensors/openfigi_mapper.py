"""OpenFIGI and European Equities Identifier Mapper for PEA Sniper Terminal.

Translates and resolves identifiers across ISIN, FIGI, and Yahoo/Euronext Tickers
with persistent SQLite caching and high-speed offline resolution tables for French/EU PEA assets.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "database" / "portfolio.db"

# Core European PEA offline identifier table (instant 0ms resolution)
_CORE_OFFLINE_MAP = {
    "FR0000121014": {"ticker": "MC.PA", "figi": "BBG000BDYBG6", "name": "LVMH Moet Hennessy", "exch": "PA"},
    "FR0000120321": {"ticker": "OR.PA", "figi": "BBG000BC96X8", "name": "L'Oreal", "exch": "PA"},
    "FR0000120073": {"ticker": "AI.PA", "figi": "BBG000BC59K9", "name": "Air Liquide", "exch": "PA"},
    "FR0000052292": {"ticker": "RMS.PA", "figi": "BBG000B9W2D4", "name": "Hermes International", "exch": "PA"},
    "FR0000120578": {"ticker": "SAN.PA", "figi": "BBG000BC6P95", "name": "Sanofi", "exch": "PA"},
    "FR0000120271": {"ticker": "TTE.PA", "figi": "BBG000BCJBL9", "name": "TotalEnergies", "exch": "PA"},
    "FR0000131104": {"ticker": "BNP.PA", "figi": "BBG000BDL000", "name": "BNP Paribas", "exch": "PA"},
    "NL0000235190": {"ticker": "AIR.PA", "figi": "BBG000BKSFB7", "name": "Airbus", "exch": "PA"},
    "FR0000121972": {"ticker": "SU.PA", "figi": "BBG000BD37S6", "name": "Schneider Electric", "exch": "PA"},
    "FR0000121667": {"ticker": "EL.PA", "figi": "BBG000BCB9W4", "name": "EssilorLuxottica", "exch": "PA"},
    "NL0010273215": {"ticker": "ASML.AS", "figi": "BBG000D00908", "name": "ASML Holding", "exch": "AS"},
    "LU1681043599": {"ticker": "CW8.PA", "figi": "BBG00F4W0P74", "name": "Amundi MSCI World UCITS ETF", "exch": "PA"},
}


class OpenFigiMapper:
    """Resolves ISIN, FIGI, and Tickers with multi-tiered fallback and local caching."""

    def __init__(self, db_path: str | Path = _DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS figi_ticker_map (
                        isin        TEXT PRIMARY KEY,
                        ticker      TEXT NOT NULL,
                        figi        TEXT,
                        name        TEXT,
                        exchange    TEXT,
                        updated_at  TEXT NOT NULL
                    );
                    """
                )
        except sqlite3.Error as exc:
            logger.debug("figi_ticker_map init error: %s", exc)

    def isin_to_ticker(self, isin: str) -> Optional[str]:
        """Convert ISIN code to Yahoo Ticker."""
        if not isin or len(isin) != 12:
            return None

        # 1. Offline fast lookup
        if isin in _CORE_OFFLINE_MAP:
            return _CORE_OFFLINE_MAP[isin]["ticker"]

        # 2. SQLite cache lookup
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT ticker FROM figi_ticker_map WHERE isin = ?;", (isin,)).fetchone()
                if row:
                    return str(row["ticker"])
        except Exception:
            pass

        # 3. OpenFIGI API lookup
        api_key = os.getenv("OPENFIGI_API_KEY")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["X-OPENFIGI-APIKEY"] = api_key

        url = "https://api.openfigi.com/v3/mapping"
        payload = [{"idType": "ID_ISIN", "idValue": isin}]

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=6)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and data and "data" in data[0]:
                    items = data[0]["data"]
                    for item in items:
                        ticker = item.get("ticker")
                        exch = item.get("exchCode")
                        if ticker and exch in ("FP", "PA", "NA", "AS", "BB", "BR"):
                            suffix = ".PA" if exch in ("FP", "PA") else (".AS" if exch in ("NA", "AS") else ".BR")
                            resolved = f"{ticker}{suffix}"
                            self._cache_mapping(isin, resolved, item.get("figi"), item.get("name"), exch)
                            return resolved
        except Exception as exc:
            logger.debug("OpenFIGI query failed for ISIN %s: %s", isin, exc)

        return None

    def ticker_to_isin(self, ticker: str) -> Optional[str]:
        """Reverse lookup: Ticker to ISIN."""
        for isin, d in _CORE_OFFLINE_MAP.items():
            if d["ticker"] == ticker:
                return isin

        try:
            with self._connect() as conn:
                row = conn.execute("SELECT isin FROM figi_ticker_map WHERE ticker = ?;", (ticker,)).fetchone()
                if row:
                    return str(row["isin"])
        except Exception:
            pass

        return None

    def _cache_mapping(self, isin: str, ticker: str, figi: Optional[str], name: Optional[str], exchange: Optional[str]) -> None:
        try:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO figi_ticker_map (isin, ticker, figi, name, exchange, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(isin) DO UPDATE SET
                        ticker = excluded.ticker,
                        figi = excluded.figi,
                        updated_at = excluded.updated_at;
                    """,
                    (isin, ticker, figi, name, exchange, now),
                )
        except Exception as exc:
            logger.debug("Failed to cache FIGI mapping: %s", exc)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    mapper = OpenFigiMapper()
    print("FR0000121014 ->", mapper.isin_to_ticker("FR0000121014"))
    print("MC.PA ->", mapper.ticker_to_isin("MC.PA"))
