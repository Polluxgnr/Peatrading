"""Broker CSV Reconciliation Engine for PEA Pollux.

Parses exported broker position CSVs (Boursorama, Bourse Direct, Fortuneo, Degiro, Interactive Brokers)
and reconciles the SQLite database with real-world broker balances, quantities, and PRUs.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent
for sub in ("01_memory_core", "05_interfaces"):
    sys.path.insert(0, str(_ROOT / sub))

from data_models import PortfolioState, Position, Signal, SignalStatus, SignalType

logger = logging.getLogger("broker_reconciliation")


def _clean_number(val: Any) -> float:
    """Clean numeric string handling European formatting (e.g. '1 234,56 €' -> 1234.56)."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    s = re.sub(r"[^\d,\.\-]", "", s)
    if not s:
        return 0.0
    # Handle European decimal commas vs thousands separators
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


class BrokerReconciliator:
    """Reconciles internal portfolio memory with official broker account exports."""

    def __init__(self, config_dir: Optional[Path] = None) -> None:
        self.config_dir = config_dir or (_ROOT / "config")

    def resolve_ticker(self, raw_symbol_or_name: str) -> str:
        """Resolve raw broker text (e.g. 'LVMH MOET HENNESSY', 'TTE', 'FR0000120271') to standard Yahoo ticker."""
        cleaned = str(raw_symbol_or_name).strip().upper()
        if not cleaned:
            return "UNKNOWN"

        # Direct ticker match
        if cleaned.endswith((".PA", ".AS", ".DE", ".MI", ".BR", "=X")) or cleaned.startswith("^"):
            return cleaned

        # Common mapping
        from discord_copilot import resolve_ticker_fuzzy
        try:
            return resolve_ticker_fuzzy(cleaned, config_dir=self.config_dir)
        except Exception:
            if re.match(r"^[A-Z0-9]{1,5}$", cleaned):
                return f"{cleaned}.PA"
            return cleaned

    def parse_broker_csv(self, file_content: str) -> List[Dict[str, Any]]:
        """Parse raw CSV string into normalized position records.

        Supports Boursorama, Bourse Direct, Fortuneo, and generic brokers.

        Returns:
            List[Dict]: [{'ticker': 'MC.PA', 'qty_shares': 10, 'avg_entry_price': 650.0, 'current_price': 680.0, 'sector': 'Consumer Cyclical'}, ...]
        """
        if not file_content or not file_content.strip():
            logger.warning("Empty broker CSV content provided.")
            return []

        lines = file_content.strip().splitlines()
        # Find header line (skip initial metadata lines if any)
        delimiter = ";" if ";" in lines[0] or (len(lines) > 1 and ";" in lines[1]) else ","
        if "\t" in lines[0]:
            delimiter = "\t"

        reader = csv.reader(lines, delimiter=delimiter)
        raw_rows = [row for row in reader if row and any(cell.strip() for cell in row)]
        if not raw_rows:
            return []

        # Find header index
        header_idx = 0
        header_candidates = ["ticker", "symbol", "isin", "libellé", "libelle", "titre", "nom", "valeur", "quantité", "quantite", "pru", "cours"]
        for idx, row in enumerate(raw_rows[:10]):
            joined = " ".join(c.lower() for c in row)
            if any(hc in joined for hc in header_candidates):
                header_idx = idx
                break

        headers = [c.strip().lower() for c in raw_rows[header_idx]]
        data_rows = raw_rows[header_idx + 1:]

        # Map header columns
        col_ticker = next((i for i, h in enumerate(headers) if any(k in h for k in ["ticker", "symbol", "isin", "libellé", "libelle", "titre", "nom", "valeur"])), 0)
        col_qty = next((i for i, h in enumerate(headers) if any(k in h for k in ["quantité", "quantite", "qty", "qte", "titres", "nombre", "positions", "shares", "solde"])), None)
        col_pru = next((i for i, h in enumerate(headers) if any(k in h for k in ["pru", "revient", "achat", "entry", "cost", "avg"])), None)
        col_price = next((i for i, h in enumerate(headers) if any(k in h for k in ["cours", "dernier", "actuel", "price", "cotation", "valeur actuelle"])), None)
        col_sector = next((i for i, h in enumerate(headers) if any(k in h for k in ["secteur", "sector", "catégorie", "categorie"])), None)

        parsed_positions = []

        for row in data_rows:
            if not row or len(row) <= col_ticker:
                continue
            raw_tick = row[col_ticker].strip()
            if not raw_tick or raw_tick.lower() in ("total", "somme", "liquidités", "especes", "cash", "disponible"):
                continue

            ticker = self.resolve_ticker(raw_tick)
            qty = int(_clean_number(row[col_qty])) if col_qty is not None and len(row) > col_qty else 0
            if qty <= 0:
                continue

            pru = _clean_number(row[col_pru]) if col_pru is not None and len(row) > col_pru else 0.0
            price = _clean_number(row[col_price]) if col_price is not None and len(row) > col_price else pru
            if price <= 0.0:
                price = pru

            sector = row[col_sector].strip() if col_sector is not None and len(row) > col_sector else "UNKNOWN"

            parsed_positions.append({
                "ticker": ticker,
                "qty_shares": qty,
                "avg_entry_price": round(pru, 2) if pru > 0 else round(price, 2),
                "current_price": round(price, 2),
                "sector": sector,
            })

        logger.info("Successfully parsed %d positions from broker CSV.", len(parsed_positions))
        return parsed_positions

    def reconcile_with_sqlite(
        self,
        parsed_data: List[Dict[str, Any]],
        actual_cash: float,
        portfolio_db: Any,
    ) -> Dict[str, Any]:
        """Overwrite SQLite positions and cash with true broker values and log an audit signal."""
        cash = max(0.0, float(actual_cash))
        new_positions = []

        for item in parsed_data:
            new_positions.append(
                Position(
                    ticker=item["ticker"],
                    qty_shares=int(item["qty_shares"]),
                    avg_entry_price=float(item["avg_entry_price"]),
                    current_price=float(item["current_price"]),
                    sector=str(item.get("sector", "UNKNOWN")),
                )
            )

        total_market_value = sum(p.qty_shares * p.current_price for p in new_positions)
        total_equity = cash + total_market_value

        state = PortfolioState(
            cash_available=cash,
            total_equity=total_equity,
            positions=new_positions,
            last_updated=datetime.now(timezone.utc).isoformat(),
        )

        portfolio_db.update_portfolio(state)

        # Log reconciliation audit record
        rec_signal = Signal(
            ticker="PORTFOLIO",
            signal_type=SignalType.BUY,
            score=100.0,
            target_qty=len(new_positions),
            status=SignalStatus.EXECUTED,
            reason="PORTFOLIO RECONCILIATION: Synced with broker reality.",
            lineage={
                "actual_cash": cash,
                "total_equity": total_equity,
                "positions_count": len(new_positions),
                "strategy": "PORTFOLIO_RECONCILIATION",
                "reason": "PORTFOLIO RECONCILIATION: Synced with broker reality.",
            },
        )
        portfolio_db.log_signal(rec_signal)


        logger.info(
            "Portfolio reconciliation complete: %d positions synced, cash=%.2f EUR, total_equity=%.2f EUR.",
            len(new_positions),
            cash,
            total_equity,
        )

        return {
            "success": True,
            "positions_synced": len(new_positions),
            "cash_available": cash,
            "total_equity": total_equity,
        }
