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
