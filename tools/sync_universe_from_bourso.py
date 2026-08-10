"""Sync ``config/pea_universe.yaml`` from Boursorama's PEA eligibility filter.

Harvests ``quotation_az_filter[peaEligibility]=1`` across SRD / compartments /
PEA-PME, maps Bourso slugs to Yahoo tickers, validates live prices, and merges
into the existing universe (keeps known sectors/names when possible).

Run:
    python tools/sync_universe_from_bourso.py
    python tools/sync_universe_from_bourso.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from pathlib import Path

import yaml
import yfinance as yf

_ROOT = Path(__file__).resolve().parent.parent
_SCRAPERS = _ROOT / "00_data_sensors" / "scrapers"
_UNIVERSE = _ROOT / "config" / "pea_universe.yaml"
sys.path.insert(0, str(_SCRAPERS))

from bourso_scraper import BoursoramaScraper  # noqa: E402

logger = logging.getLogger("sync_universe")

# Map Bourso French activity labels → our sector buckets.
_SECTOR_MAP = {
    "technologie": "Technology",
    "logiciel": "Technology",
    "semiconduct": "Technology",
    "santé": "Healthcare",
    "sante": "Healthcare",
    "pharma": "Healthcare",
    "biotechn": "Healthcare",
    "banque": "Financial Services",
    "assurance": "Financial Services",
    "finance": "Financial Services",
    "investissement": "Financial Services",
    "pétrol": "Energy",
    "petrol": "Energy",
    "gaz": "Energy",
    "énergie": "Utilities",
    "energie": "Utilities",
    "utilit": "Utilities",
    "immobilier": "Real Estate",
    "fonci": "Real Estate",
    "télécom": "Communication Services",
    "telecom": "Communication Services",
    "média": "Communication Services",
    "media": "Communication Services",
    "publicité": "Communication Services",
    "luxe": "Consumer Cyclical",
    "automobile": "Consumer Cyclical",
    "voyage": "Consumer Cyclical",
    "loisir": "Consumer Cyclical",
    "distribution": "Consumer Defensive",
    "alimentaire": "Consumer Defensive",
    "boisson": "Consumer Defensive",
    "chimie": "Basic Materials",
    "matériaux": "Basic Materials",
    "materiaux": "Basic Materials",
    "mines": "Basic Materials",
    "industrie": "Industrials",
    "construction": "Industrials",
    "aéro": "Industrials",
    "aero": "Industrials",
    "transport": "Industrials",
}


def _guess_sector(label: str | None) -> str:
    if not label:
        return "Divers"
    low = label.lower()
    for needle, sector in _SECTOR_MAP.items():
        if needle in low:
            return sector
    return "Divers"


def _yf_sector(ticker: str) -> str | None:
    try:
        info = yf.Ticker(ticker).info or {}
        return info.get("sector")
    except Exception:  # noqa: BLE001
        return None


def _validate(symbols: list[str]) -> set[str]:
    good: set[str] = set()
    if not symbols:
        return good
    # Batch in chunks to avoid huge downloads.
    chunk_size = 80
    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i: i + chunk_size]
        try:
            data = yf.download(
                chunk, period="5d", progress=False,
                auto_adjust=False, group_by="ticker", threads=True,
            )
        except Exception:  # noqa: BLE001
            data = None
        for sym in chunk:
            ok = False
            try:
                if data is not None and sym in data.columns.get_level_values(0):
                    if not data[sym]["Close"].dropna().empty:
                        ok = True
            except Exception:  # noqa: BLE001
                ok = False
            if not ok:
                try:
                    hist = yf.Ticker(sym).history(period="5d")
                    ok = hist is not None and not hist.empty
                except Exception:  # noqa: BLE001
                    ok = False
            if ok:
                good.add(sym)
    return good


def _load_existing() -> dict[str, dict]:
    """Return ticker -> {name, sector} from current YAML."""
    if not _UNIVERSE.exists():
        return {}
    data = yaml.safe_load(_UNIVERSE.read_text(encoding="utf-8")) or {}
    out: dict[str, dict] = {}
    for sector, members in (data.get("universe") or {}).items():
        for e in members or []:
            t = e.get("ticker")
            if t:
                out[t] = {"name": e.get("name", t), "sector": sector,
                          "pea_pme": e.get("pea_pme"), "srd": e.get("srd")}
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-validate", action="store_true",
                        help="Skip Yahoo live-price validation (faster).")
    args = parser.parse_args()

    logger.info("Harvesting Boursorama PEA eligibility listings…")
    rows = BoursoramaScraper().get_pea_universe(include_pea_pme=True)
    logger.info("Raw Bourso PEA rows: %d", len(rows))

    existing = _load_existing()
    # Preserve ETF sleeve from current universe.
    etf_keep = {
        t: meta for t, meta in existing.items()
        if meta.get("sector") == "ETF"
    }

    by_ticker: dict[str, dict] = {}
    for row in rows:
        yahoo = row["yahoo"]
        by_ticker[yahoo] = {
            "name": row["name"],
            "sector": existing.get(yahoo, {}).get("sector") or "Divers",
            "pea_pme": row.get("pea_pme") == "true",
            "srd": row.get("market") == "SRD",
            "bourso_sector": None,
        }

    tickers = sorted(by_ticker)
    if args.skip_validate:
        good = set(tickers)
    else:
        logger.info("Validating %d tickers on Yahoo Finance…", len(tickers))
        good = _validate(tickers)
        dropped = set(tickers) - good
        if dropped:
            logger.warning("Dropped %d invalid: %s",
                           len(dropped), ", ".join(sorted(list(dropped)[:20])))

    # Sector enrichment for unknowns.
    for t in sorted(good):
        meta = by_ticker[t]
        if meta["sector"] in ("Divers", None) or t not in existing:
            yf_sec = _yf_sector(t)
            if yf_sec:
                meta["sector"] = yf_sec
            # light rate-limit courtesy
        if t in existing and existing[t]["sector"] not in ("Divers", "Unknown"):
            meta["sector"] = existing[t]["sector"]
            meta["name"] = existing[t]["name"] or meta["name"]

    # Re-attach ETFs.
    for t, meta in etf_keep.items():
        by_ticker[t] = {
            "name": meta["name"], "sector": "ETF",
            "pea_pme": False, "srd": False,
        }
        good.add(t)

    buckets: dict[str, list[dict]] = defaultdict(list)
    for t in sorted(good):
        meta = by_ticker[t]
        entry = {"ticker": t, "name": meta["name"]}
        if meta.get("pea_pme"):
            entry["pea_pme"] = True
        if meta.get("srd"):
            entry["srd"] = True
        buckets[meta["sector"] or "Divers"].append(entry)

    payload = {"universe": {k: buckets[k] for k in sorted(buckets)}}
    total = sum(len(v) for v in buckets.values())
    logger.info("Universe ready: %d tickers across %d sectors", total, len(buckets))

    if args.dry_run:
        for sec, members in list(payload["universe"].items())[:5]:
            logger.info("  %s: %d (e.g. %s)", sec, len(members),
                        ", ".join(m["ticker"] for m in members[:3]))
        return

    with open(_UNIVERSE, "w", encoding="utf-8") as fh:
        fh.write("# PEA Sniper Terminal V-Prime - investable universe\n")
        fh.write("# Synced from Boursorama Eligibilité PEA filter "
                 "(tools/sync_universe_from_bourso.py).\n")
        fh.write("# Extra flags: srd=true (liquid SRD), pea_pme=true.\n\n")
        yaml.safe_dump(payload, fh, allow_unicode=True, sort_keys=False,
                       default_flow_style=False)
    logger.info("Wrote %s", _UNIVERSE)


if __name__ == "__main__":
    main()
