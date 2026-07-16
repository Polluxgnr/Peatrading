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
