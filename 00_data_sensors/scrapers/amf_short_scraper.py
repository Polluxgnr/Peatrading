"""AMF Short Interest Scraper for PEA Pollux.

Scrapes and computes Net Short Positions ("Positions courtes nettes")
published by the Autorité des Marchés Financiers (AMF) under EU Regulation 236/2012.
Provides quantitative data on heavily shorted French and European equities to veto toxic assets.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

try:
    from ._http import safe_get, stealth_headers
except ImportError:
    try:
        from _http import safe_get, stealth_headers
    except ImportError:
        import requests
        def safe_get(url: str, **kwargs):
            try:
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                resp = requests.get(url, headers=headers, timeout=kwargs.get("timeout", 10))
                return resp if resp.status_code == 200 else None
            except Exception:
                return None

logger = logging.getLogger(__name__)


class AmfShortScraper:
    """Scrape net short positions from AMF BDIF API with robust fallback."""

    def __init__(self, base_url: str = "https://bdif.amf-france.org/api/v1/positions-courtes") -> None:
        self.base_url = base_url

    def get_short_interest(self, isin: str) -> float:
        """Get net short percentage for a given ISIN.

        Sums the most recent active short positions reported by hedge funds and asset managers.

        Args:
            isin: 12-character ISIN code (e.g. 'FR0000121014').

        Returns:
            float: Total short interest percentage (e.g. 4.5 for 4.5%). Returns 0.0 if unknown or none.
        """
        if not isin or len(isin.strip()) < 8:
            return 0.0

        clean_isin = isin.strip().upper()

        # Try both primary and fallback AMF BDIF endpoints
        endpoints = [
            f"{self.base_url}?isin={clean_isin}",
            f"https://bdif.amf-france.org/back/api/v1/positions-courtes?isin={clean_isin}",
            f"https://bdif.amf-france.org/api/v1/positions-courtes/recherche?isin={clean_isin}",
        ]

        for url in endpoints:
            try:
                resp = safe_get(url, timeout=8, expect_json=True, quiet=True)
                if resp is not None and resp.status_code == 200:
                    data = resp.json()
                    total_pct = self._parse_short_payload(data, clean_isin)
                    if total_pct > 0.0:
                        logger.info("AMF Short Interest for %s: %.2f%%", clean_isin, total_pct)
                        return round(total_pct, 2)
            except Exception as exc:
                logger.debug("AMF short scrape attempt failed for %s at %s: %s", clean_isin, url, exc)

        return 0.0

    def _parse_short_payload(self, data: Any, isin: str) -> float:
        """Parse positions JSON from AMF BDIF response and sum active manager positions."""
        if not data:
            return 0.0

        items: List[Dict[str, Any]] = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            for k in ("datas", "items", "positions", "results", "data", "result"):
                if k in data and isinstance(data[k], list):
                    items = data[k]
                    break
            if not items and "positionsCourtes" in data:
                items = data["positionsCourtes"] if isinstance(data["positionsCourtes"], list) else []

        if not items:
            return 0.0

        # Group by holder/fund name to take the latest reported position
        holder_latest_pos: Dict[str, float] = {}

        for row in items:
            if not isinstance(row, dict):
                continue

            # Verify ISIN matches if present
            row_isin = str(row.get("isin") or row.get("codeIsin") or "").strip().upper()
            if row_isin and row_isin != isin:
                continue

            # Extract holder / fund
            holder = str(
                row.get("detenteur")
                or row.get("gestionnaire")
                or row.get("holder")
                or row.get("nom")
                or row.get("id")
                or "Unknown"
            ).strip()

            # Extract position value (e.g. 0.85 or 0.85% or 0.0085)
            raw_pos = row.get("position") or row.get("ratio") or row.get("positionPct") or row.get("valeur") or 0.0
            try:
                pos_val = float(str(raw_pos).replace("%", "").replace(",", ".").strip())
                if 0 < pos_val < 0.05 and row.get("isFraction"):
                    pos_val *= 100.0
            except (ValueError, TypeError):
                pos_val = 0.0

            # Store latest position for this holder (subsequent entries overwrite earlier ones)
            holder_latest_pos[holder] = pos_val

        # Sum all active positions (AMF reporting threshold >= 0.5%)
        active_sum = sum(p for p in holder_latest_pos.values() if p > 0.0)
        return float(active_sum)



if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    scraper = AmfShortScraper()
    print("Testing AMF Short Scraper for LVMH (FR0000121014)...")
    res = scraper.get_short_interest("FR0000121014")
    print(f"Short Interest: {res:.2f}%")
