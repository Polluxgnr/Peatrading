"""AMF insider-declaration scraper (antifragile, multi-source).

Primary: Opendatasoft explore v2.1 + BDIF ``/back/api/v1`` (``RechercheTexte``).
Fallback: legacy BDIF ``/api/v1`` (WAF-prone, 12h circuit) then callers use FMP/YF.
Any failure returns an empty DataFrame so callers fall back gracefully.
"""

from __future__ import annotations

import logging
import os
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
        try:
            rate_limit(0.2, 0.6)
            name = issuer or _issuer_name(ticker)

            # 1) Opendatasoft / public structured API first (no API key).
            rows = self._search_ods_api(name)
            if not rows and isin:
                rows = self._search_ods_api(isin.split("_")[0])

            # 2) BDIF fallback (legacy + /back API).
            if not rows and amf_available():
                rows = self._search_bdif(name, isin=isin)
            if not rows and isin and amf_available():
                rows = self._search_bdif(isin.split("_")[0], isin=isin)

            if not rows:
                # 3) Paid API fallback when AMF returns empty / ambiguous.
                rows = self._search_fmp_insiders(ticker)
            if not rows:
                rows = self._search_eodhd_insiders(ticker)

            if not rows:
                self.last_error = self.last_error or "no AMF/ODS/FMP/EODHD rows"
                logger.debug(
                    "AMF empty for %s (%s / %s).", ticker, name, isin
                )
                return pd.DataFrame()

    async def get_recent_declarations_async(self, tickers: list[str]) -> dict[str, pd.DataFrame]:
        """Fetch declarations for multiple tickers concurrently."""
        from scrapers._http import async_safe_get
        import asyncio
        import aiohttp
        
        results = {}
        sem = asyncio.Semaphore(3)
        
        async def fetch_one(session, ticker: str):
            # Wrapper logic to async fetch from ODS API or BDIF
            # To avoid complete rewrite, we'll wrap the sync fallback logic with run_in_executor
            # but for true async, we'd hit the ODS API asynchronously here.
            loop = asyncio.get_event_loop()
            try:
                # Limit concurrency with semaphore even for threads
                async with sem:
                    df = await loop.run_in_executor(None, self.get_recent_declarations, ticker)
                return ticker, df
            except Exception:
                return ticker, pd.DataFrame()
                
        async with aiohttp.ClientSession() as session:
            tasks = [fetch_one(session, t) for t in tickers]
            for coro in asyncio.as_completed(tasks):
                t, df = await coro
                results[t] = df
                
        return results

            # Reclassify generic "Declaration" using title keywords.
            for r in rows:
                tx = str(r.get("Transaction") or "")
                if tx.casefold() in ("declaration", "déclar", ""):
                    blob = f"{r.get('Title') or ''} {r.get('Transaction') or ''}"
                    r["Transaction"] = self._classify_transaction(blob)

            # If still all ambiguous Declarations, prefer FMP/EODHD detail.
            ambiguous = all(
                str(r.get("Transaction") or "").casefold() == "declaration"
                for r in rows
            )
            if ambiguous:
                paid = self._search_fmp_insiders(ticker) or self._search_eodhd_insiders(ticker)
                if paid:
                    rows = paid

            df = pd.DataFrame(rows)
            keep = [c for c in (
                "Date", "Insider", "Transaction", "Value", "Volume", "Shares",
                "Price", "Title", "ISIN", "Source",
            ) if c in df.columns]
            return df[keep].reset_index(drop=True) if keep else pd.DataFrame()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            logger.debug("AmfInsiderScraper failed for %s: %s", ticker, exc)
            return pd.DataFrame()

    def get_declarations_for_profile(self, profile: dict) -> pd.DataFrame:
        """Convenience: use a Boursorama profile dict (isin + name + ticker)."""
        return self.get_recent_declarations(
            profile.get("ticker") or "",
            isin=profile.get("isin"),
            issuer=profile.get("name"),
        )

    # --- Strict French legal-vocabulary regex for transaction classification ----
    _RE_ACHAT = re.compile(
        r"\b(achat|acquisition|souscription|exercice|attribution|"
        r"conversion|apport|purchase|buy)\b",
        re.IGNORECASE,
    )
    _RE_VENTE = re.compile(
        r"\b(vente|cession|ali[eé]nation|disposal|sale|sell|rachat|"
        r"transfert|remise)\b",
        re.IGNORECASE,
    )
    _RE_EUR_VALUE = re.compile(
        r"(\d[\d\s]*[.,]?\d*)\s*(?:€|EUR|eur)", re.IGNORECASE
    )
    _RE_SHARES = re.compile(
        r"(\d[\d\s]*)\s*(?:actions?|titres?|parts?|shares?)\b", re.IGNORECASE
    )

    @classmethod
    def _classify_transaction(cls, blob: str) -> str:
        """Classify transaction type using strict French legal vocabulary.

        Uses word-boundary regex to avoid false positives on substrings
        (e.g. 'cession' inside 'accession').
        """
        text = (blob or "")
        if cls._RE_ACHAT.search(text):
            return "Achat"
        if cls._RE_VENTE.search(text):
            return "Vente"
        return "Declaration"

    @classmethod
    def _extract_value_from_text(cls, text: str) -> float | None:
        """Try to extract a EUR value from free-text description."""
        m = cls._RE_EUR_VALUE.search(text or "")
        if not m:
            return None
        try:
            raw = m.group(1).replace(" ", "").replace(",", ".")
            return float(raw)
        except (ValueError, TypeError):
            return None

    @classmethod
    def _extract_shares_from_text(cls, text: str) -> int | None:
        """Try to extract a share count from free-text description."""
        m = cls._RE_SHARES.search(text or "")
        if not m:
            return None
        try:
            raw = m.group(1).replace(" ", "")
            return int(raw)
        except (ValueError, TypeError):
            return None

    def _search_fmp_insiders(self, ticker: str) -> list[dict]:
        """Fallback: FMP ``/api/v4/insider-trading`` with share counts."""
        api_key = (os.getenv("FMP_API_KEY") or "").strip()
        if not api_key:
            return []
        symbol = ticker.replace(".PA", "").replace(".AS", "").upper()
        try:
            resp = self._session.get(
                "https://financialmodelingprep.com/api/v4/insider-trading",
                params={"symbol": symbol, "limit": 25, "apikey": api_key},
                timeout=12,
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            if not isinstance(data, list):
                return []
            rows = []
            for item in data[:25]:
                if not isinstance(item, dict):
                    continue
                tx_raw = str(
                    item.get("transactionType")
                    or item.get("acquistionOrDisposition")
                    or ""
                )
                tx = self._classify_transaction(tx_raw)
                if tx == "Declaration":
                    # FMP uses A/D codes sometimes
                    code = str(item.get("acquistionOrDisposition") or "").upper()
                    if code == "A":
                        tx = "Achat"
                    elif code == "D":
                        tx = "Vente"
                shares = item.get("securitiesTransacted") or item.get("securitiesOwned")
                price = item.get("price")
                value = None
                try:
                    if shares is not None and price is not None:
                        value = float(shares) * float(price)
                except (TypeError, ValueError):
                    value = None
                rows.append({
                    "Date": str(item.get("transactionDate") or item.get("filingDate") or "")[:10],
                    "Insider": item.get("reportingName") or item.get("reporter") or "Insider",
                    "Transaction": tx,
                    "Value": value,
                    "Shares": shares,
                    "Volume": shares,
                    "Price": price,
                    "Title": f"FMP: {tx_raw}"[:240],
                    "ISIN": "",
                    "Source": "FMP",
                })
            return rows
        except Exception as exc:  # noqa: BLE001
            logger.debug("FMP insider fallback failed for %s: %s", ticker, exc)
            return []

    def _search_eodhd_insiders(self, ticker: str) -> list[dict]:
        """Fallback: EODHD insider transactions (when ``EODHD_API_KEY`` set)."""
        api_key = (os.getenv("EODHD_API_KEY") or "").strip()
        if not api_key:
            return []
        # EODHD expects exchange suffix like KER.PA
        symbol = ticker if "." in ticker else f"{ticker}.PA"
        try:
            resp = self._session.get(
                f"https://eodhd.com/api/insider-transactions",
                params={"code": symbol, "api_token": api_key, "fmt": "json"},
                timeout=12,
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            if not isinstance(data, list):
                return []
            rows = []
            for item in data[:25]:
                if not isinstance(item, dict):
                    continue
                tx_raw = str(item.get("transactionType") or item.get("ownerType") or "")
                tx = self._classify_transaction(tx_raw)
                shares = item.get("transactionAmount") or item.get("shares")
                price = item.get("transactionPrice") or item.get("price")
                rows.append({
                    "Date": str(item.get("date") or item.get("reportDate") or "")[:10],
                    "Insider": item.get("ownerName") or item.get("name") or "Insider",
                    "Transaction": tx,
                    "Value": item.get("transactionValue"),
                    "Shares": shares,
                    "Volume": shares,
                    "Price": price,
                    "Title": f"EODHD: {tx_raw}"[:240],
                    "ISIN": "",
                    "Source": "EODHD",
                })
            return rows
        except Exception as exc:  # noqa: BLE001
            logger.debug("EODHD insider fallback failed for %s: %s", ticker, exc)
            return []

    def _search_ods_api(self, query: str) -> list[dict]:
        """Fetch AMF data via Opendatasoft v2.1 API using ODSQL (public)."""
        if not query or not str(query).strip():
            return []
        q = str(query).strip().replace('"', "")
        # Candidate portals/datasets (AMF / Info-Financière / Economy ODS).
        endpoints = [
            (
                "https://data.amf-france.org/api/explore/v2.1/catalog/datasets/"
                "declarations-dirigeants/records"
            ),
            (
                "https://www.info-financiere.fr/api/explore/v2.1/catalog/datasets/"
                "flux-amf-new-prod/records"
            ),
        ]
        # Also hit the live BDIF back API (structured public feed).
        back_rows = self._search_bdif_back(q)
        if back_rows:
            return back_rows

        for url in endpoints:
            try:
                rate_limit(0.2, 0.5)
                resp = self._session.get(
                    url,
                    params={
                        "where": f'search("{q}")',
                        "limit": 25,
                        "order_by": "date_publication DESC",
                    },
                    headers={
                        **stealth_headers(),
                        "Accept": "application/json",
                    },
                    timeout=10,
                )
                if resp.status_code != 200:
                    continue
                try:
                    data = resp.json()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("ODS JSON decode failed for %s: %s", q, exc)
                    continue
                results: list[dict] = []
                for item in (data.get("results") or []):
                    if not isinstance(item, dict):
                        continue
                    full_blob = " ".join(
                        str(item.get(k) or "")
                        for k in (
                            "type_transaction", "nature_transaction",
                            "typesDocument", "titre", "resume",
                            "description", "objet", "declarant", "nom",
                        )
                    )
                    tx = self._classify_transaction(full_blob)
                    raw_value = item.get("montant") or item.get("valeur")
                    raw_shares = item.get("volume") or item.get("quantite")
                    if raw_value is None:
                        raw_value = self._extract_value_from_text(full_blob)
                    if raw_shares is None:
                        raw_shares = self._extract_shares_from_text(full_blob)
                    results.append({
                        "Date": str(
                            item.get("date_publication")
                            or item.get("datePublication")
                            or item.get("date")
                            or ""
                        )[:10],
                        "Insider": (
                            item.get("declarant")
                            or item.get("nom")
                            or item.get("raison_sociale")
                            or "Dirigeant"
                        ),
                        "Transaction": tx,
                        "Value": raw_value,
                        "Shares": raw_shares,
                        "Volume": raw_shares,
                        "Title": f"ODS API: {q}",
                        "ISIN": item.get("isin") or "",
                        "Source": "AMF Opendatasoft",
                    })
                if results:
                    return results
            except Exception as exc:  # noqa: BLE001
                logger.debug("ODS API failed for %r via %s: %s", q, url, exc)
        return []

    def _search_bdif_back(self, query: str) -> list[dict[str, Any]]:
        """Public BDIF ``/back/api/v1/informations`` feed (typesInformation=DD)."""
        q = (query or "").strip()
        if not q:
            return []
        try:
            rate_limit(0.2, 0.5)
            resp = self._session.get(
                _BDIF_BASE + "/back/api/v1/informations",
                params={
                    "from": 0,
                    "size": 40,
                    "typesInformation": "DD",
                    "RechercheTexte": q,
                },
                headers={
                    **stealth_headers(),
                    "Accept": "application/json",
                },
                timeout=12,
            )
            if resp.status_code != 200:
                return []
            payload = resp.json()
            items = payload.get("result") or payload.get("hits") or []
            if isinstance(items, dict):
                items = items.get("hits") or []
            rows: list[dict[str, Any]] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                src = item.get("_source") if "_source" in item else item
                if not isinstance(src, dict):
                    continue
                societes = src.get("societes") or []
                names = " ".join(
                    str(s.get("raisonSociale") or "")
                    for s in societes if isinstance(s, dict)
                )
                title = (
                    src.get("titre")
                    or f"Declaration dirigeants — {names or q}"
                )
                full_blob = " ".join(
                    str(src.get(k) or "")
                    for k in (
                        "titre", "resume", "description", "objet",
                        "typeDocument", "typeInformation",
                    )
                ) + " " + names
                tx = self._classify_transaction(full_blob)
                extracted_val = self._extract_value_from_text(full_blob)
                extracted_shares = self._extract_shares_from_text(full_blob)
                rows.append({
                    "Date": str(
                        src.get("datePublication")
                        or src.get("dateInformation")
                        or src.get("dateMiseEnLigne")
                        or ""
                    )[:10],
                    "Insider": names or "Dirigeant",
                    "Transaction": tx,
                    "Value": extracted_val,
                    "Shares": extracted_shares,
                    "Volume": extracted_shares,
                    "Title": str(title)[:240],
                    "ISIN": "",
                    "Source": "AMF BDIF Back API",
                })
            return rows[:25]
        except Exception as exc:  # noqa: BLE001
            logger.debug("BDIF back API failed for %r: %s", q, exc)
            return []

    def _search_bdif(
        self, query: str, *, isin: str | None = None
    ) -> list[dict[str, Any]]:
        """Query BDIF search with fail-fast on WAF blocks."""
        if not amf_available():
            return []
        # Prefer the working /back endpoint before the fragile /api/v1.
        back = self._search_bdif_back(query)
        if back:
            return back
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
            for key in ("items", "results", "result", "informations", "data", "content"):
                if isinstance(payload.get(key), list):
                    items = payload[key]
                    break
            if not items and isinstance(payload.get("hits"), dict):
                items = payload["hits"].get("hits") or []
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

            tx_type = AmfInsiderScraper._classify_transaction(blob)

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
            if value is None:
                value = AmfInsiderScraper._extract_value_from_text(blob)
            if volume is None:
                volume = AmfInsiderScraper._extract_shares_from_text(blob)
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

    def get_threshold_crossings(
        self, ticker: str, *, issuer: str | None = None
    ) -> list[dict[str, Any]]:
        """Query BDIF for 'Franchissement de seuil' (FS) for a ticker.
        
        A quiet accumulation crossing the 5% threshold is a structural anomaly signal.
        """
        q = (issuer or _issuer_name(ticker) or "").strip()
        if not q or not amf_available():
            return []
            
        try:
            rate_limit(0.2, 0.5)
            resp = self._session.get(
                _BDIF_BASE + "/back/api/v1/informations",
                params={
                    "from": 0,
                    "size": 20,
                    "typesInformation": "FS",
                    "RechercheTexte": q,
                },
                headers={
                    **stealth_headers(),
                    "Accept": "application/json",
                },
                timeout=12,
            )
            if resp.status_code != 200:
                return []
            payload = resp.json()
            items = payload.get("result") or payload.get("hits") or []
            if isinstance(items, dict):
                items = items.get("hits") or []
            rows: list[dict[str, Any]] = []
            
            for item in items:
                if not isinstance(item, dict):
                    continue
                src = item.get("_source") if "_source" in item else item
                if not isinstance(src, dict):
                    continue
                    
                title = str(src.get("titre") or src.get("resume") or "").lower()
                blob = title + " " + str(src.get("description") or "").lower()
                
                # We specifically look for "hausse" (accumulation) crossing thresholds like 5%
                direction = "accumulation" if "hausse" in blob or "franchissement en hausse" in blob else ("distribution" if "baisse" in blob else "unknown")
                
                date_raw = (
                    src.get("datePublication")
                    or src.get("dateInformation")
                    or src.get("dateMiseEnLigne")
                    or ""
                )[:10]
                
                rows.append({
                    "Date": str(date_raw),
                    "Ticker": ticker,
                    "Title": src.get("titre") or f"Franchissement Seuil — {q}",
                    "Direction": direction,
                    "Blob": blob[:500],
                })
            return rows
        except Exception as exc:
            logger.debug("BDIF FS (threshold crossing) API failed for %r: %s", q, exc)
            return []

