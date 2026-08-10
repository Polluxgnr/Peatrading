"""InsiderScreener.com Official API Client for PEA Sniper Terminal.

Queries insider buying/selling transactions via the official InsiderScreener API
(Starter/internal personal use plan) to provide standardized, cross-European insider signals.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class InsiderScreenerClient:
    """Official API client for InsiderScreener.com."""

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = api_key or os.getenv("INSIDERSCREENER_API_KEY")
        self.base_url = "https://www.insiderscreener.com/api/v1"

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def get_insider_transactions(self, isin: str, limit: int = 15) -> List[Dict]:
        """Fetch insider transactions for a specific instrument by ISIN."""
        if not self.is_configured:
            logger.debug("INSIDERSCREENER_API_KEY unset; skipping InsiderScreener API.")
            return []

        url = f"{self.base_url}/transactions"
        params = {"isin": isin, "limit": limit}
        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}

        try:
            resp = requests.get(url, params=params, headers=headers, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                results = []
                for item in data.get("transactions", []):
                    results.append({
                        "source": "insiderscreener",
                        "isin": isin,
                        "date": item.get("date"),
                        "insider_name": item.get("insider"),
                        "role": item.get("role"),
                        "transaction_type": "BUY" if str(item.get("type", "")).upper() in ("BUY", "PURCHASE", "ACHAT") else "SELL",
                        "shares": item.get("shares", 0),
                        "price": item.get("price", 0.0),
                        "amount_eur": item.get("total_eur", 0.0),
                    })
                return results
            else:
                logger.debug("InsiderScreener HTTP %d for ISIN %s", resp.status_code, isin)
        except Exception as exc:  # noqa: BLE001
            logger.debug("InsiderScreener API request failed for %s: %s", isin, exc)

        return []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    client = InsiderScreenerClient()
    print("InsiderScreener Configured:", client.is_configured)
