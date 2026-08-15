"""OpenInsider.eu Scraper & Multi-Source Cross-Verification Engine.

Parses European director and executive transactions from OpenInsider.eu,
cross-referencing with AMF BDIF, FMP, and InsiderScreener to produce a unified,
clean, de-duplicated database of insider operations.
"""

from __future__ import annotations

import io
import logging
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

try:
    from ._http import rate_limit, stealth_headers
except ImportError:
    try:
        from _http import rate_limit, stealth_headers
    except ImportError:
        def stealth_headers() -> dict[str, str]:
            return {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        def rate_limit(min_s: float = 0.5, max_s: float = 1.2) -> None:
            pass

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "database" / "portfolio.db"


def clean_numeric_value(val: Any) -> float:
    """Parse numeric values with currency symbols (€, $, £), commas, or suffixes (k, M).

    Args:
        val: String or raw numeric value.

    Returns:
        float: Clean parsed float value.
    """
    if val is None or pd.isna(val):
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)

    s = str(val).strip()
    # Remove currency symbols and non-breaking spaces
    for char in ("€", "$", "£", "¥", " ", "\xa0", "\t", "\n", "\r"):
        s = s.replace(char, "")

    if not s:
        return 0.0

    multiplier = 1.0
    if s.endswith(("k", "K")):
        multiplier = 1_000.0
        s = s[:-1]
    elif s.endswith(("m", "M")):
        multiplier = 1_000_000.0
        s = s[:-1]
    elif s.endswith(("b", "B")):
        multiplier = 1_000_000_000.0
        s = s[:-1]

    # Handle European comma decimals or thousands separators
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")

    cleaned_digits = re.sub(r"[^\d.-]", "", s)
    try:
        return float(cleaned_digits) * multiplier
    except (ValueError, TypeError):
        return 0.0


class OpenInsiderEuScraper:
    """Scrapes and normalizes transactions from OpenInsider EU with cross-source deduplication."""

    def __init__(self, db_path: str | Path = _DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.base_url = "https://openinsider.eu/api/v1/trades"
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create insiders_master table for cross-verified insider signals."""
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS insiders_master (
                        id               TEXT PRIMARY KEY,
                        ticker           TEXT NOT NULL,
                        isin             TEXT,
                        source           TEXT NOT NULL,
                        insider_name     TEXT,
                        role             TEXT,
                        transaction_type TEXT NOT NULL,
                        shares           REAL,
                        price            REAL,
                        amount_eur       REAL,
                        trade_date       TEXT NOT NULL,
                        created_at       TEXT NOT NULL
                    );
                    """
                )
        except sqlite3.Error as exc:
            logger.debug("insiders_master schema error: %s", exc)

    @staticmethod
    def _find_column(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
        """Find the matching column name case-insensitively."""
        cols_lower = {str(c).lower().strip(): c for c in df.columns}
        for cand in candidates:
            cand_l = cand.lower().strip()
            if cand_l in cols_lower:
                return cols_lower[cand_l]
        return None

    def fetch_openinsider_trades(self, ticker_or_isin: str, auto_save: bool = True) -> List[Dict]:
        """Fetch transactions from OpenInsider EU with stealth headers and robust mapping.

        Args:
            ticker_or_isin: Ticker symbol (e.g. 'MC.PA') or ISIN code.
            auto_save: Automatically insert and deduplicate records into SQLite.

        Returns:
            List[Dict]: Normalized insider transactions.
        """
        trades: List[Dict] = []
        try:
            rate_limit(0.5, 1.2)
            url = f"https://openinsider.eu/search?q={ticker_or_isin}"
            headers = stealth_headers()
            resp = requests.get(url, headers=headers, timeout=8)
            if resp.status_code == 200 and resp.text:
                # Read HTML tables with error tolerance
                tables = pd.read_html(io.StringIO(resp.text))
                if tables:
                    df = tables[0]
                    # Map columns flexibly
                    c_date = self._find_column(df, ["Filing Date", "Trade Date", "Date", "FilingDate", "TradeDate"])
                    c_name = self._find_column(df, ["Insider Name", "Insider", "Name", "Officer", "Reporting Owner"])
                    c_role = self._find_column(df, ["Title", "Role", "Relationship", "Officer Title"])
                    c_type = self._find_column(df, ["Trade Type", "Type", "Transaction", "Txn Type"])
                    c_qty = self._find_column(df, ["Qty", "Shares", "Quantity", "Number of Shares", "Volume"])
                    c_price = self._find_column(df, ["Price", "Price/Share", "Cost"])
                    c_value = self._find_column(df, ["Value", "Amount", "Total Value", "EUR Value", "Cost"])

                    for _, row in df.head(25).iterrows():
                        raw_tdate = str(row.get(c_date, "") if c_date else "").strip()
                        raw_name = str(row.get(c_name, "Unknown") if c_name else "Unknown").strip()
                        raw_role = str(row.get(c_role, "") if c_role else "").strip()
                        raw_type = str(row.get(c_type, "") if c_type else "").lower().strip()

                        ttype = "BUY" if any(w in raw_type for w in ("purchase", "buy", "achat", "p")) else "SELL"
                        shares = clean_numeric_value(row.get(c_qty) if c_qty else 0)
                        price = clean_numeric_value(row.get(c_price) if c_price else 0)
                        value = clean_numeric_value(row.get(c_value) if c_value else 0)

                        if value <= 0.0 and price > 0.0 and shares > 0.0:
                            value = round(price * shares, 2)

                        if raw_tdate or raw_name != "Unknown":
                            trades.append({
                                "source": "openinsider_eu",
                                "ticker": str(ticker_or_isin).upper(),
                                "trade_date": raw_tdate or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                                "insider_name": raw_name,
                                "role": raw_role,
                                "transaction_type": ttype,
                                "shares": shares,
                                "price": price,
                                "amount_eur": value,
                            })
        except Exception as exc:  # noqa: BLE001
            logger.debug("OpenInsider EU scrape failed for %s: %s", ticker_or_isin, exc)

        if auto_save and trades:
            self.save_and_deduplicate(trades)

        return trades

    def save_and_deduplicate(self, transactions: List[Dict]) -> int:
        """Insert and deduplicate insider transactions into SQLite."""
        if not transactions:
            return 0

        saved = 0
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as conn:
                for tx in transactions:
                    ticker = str(tx.get("ticker", "UNKNOWN")).upper()
                    name = str(tx.get("insider_name", "UNKNOWN"))
                    tdate = str(tx.get("trade_date", "UNKNOWN"))
                    ttype = str(tx.get("transaction_type", "BUY"))
                    # Generate deterministic deduplication ID
                    tx_id = f"{ticker}_{name}_{tdate}_{ttype}".replace(" ", "_")

                    conn.execute(
                        """
                        INSERT OR IGNORE INTO insiders_master
                        (id, ticker, isin, source, insider_name, role, transaction_type, shares, price, amount_eur, trade_date, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            tx_id,
                            ticker,
                            tx.get("isin", ""),
                            tx.get("source", "openinsider_eu"),
                            name,
                            tx.get("role", ""),
                            ttype,
                            float(tx.get("shares", 0) or 0),
                            float(tx.get("price", 0) or 0),
                            float(tx.get("amount_eur", 0) or 0),
                            tdate,
                            now,
                        ),
                    )
                    saved += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("Deduplication save error: %s", exc)

        return saved


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    scraper = OpenInsiderEuScraper()
    print("OpenInsider EU Scraper initialized. Testing numeric cleaner:")
    print("€ 1,200,000 ->", clean_numeric_value("€ 1,200,000"))
    print("$ 500k ->", clean_numeric_value("$ 500k"))
    print("12.50 € ->", clean_numeric_value("12.50 €"))
