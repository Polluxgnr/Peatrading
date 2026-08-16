"""Dynamic PEA Universe & Eligibility Manager for PEA Pollux.

Cross-references statically tracked universe configurations with live Boursorama
PEA / PEA-PME eligibility lists, logging audit warnings if an asset loses tax-wrapped status.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

import yaml

_ROOT = Path(__file__).resolve().parent.parent
for d in ("00_data_sensors", "00_data_sensors/adapters", "00_data_sensors/scrapers", "01_memory_core"):
    sys.path.insert(0, str(_ROOT / d))

from adapters.bourso_adapter import BoursoUniverseAdapter
try:
    from bourso_scraper import BoursoramaScraper
except ImportError:
    from scrapers.bourso_scraper import BoursoramaScraper

logger = logging.getLogger("universe_manager")

_DEFAULT_UNIVERSE_PATH = _ROOT / "config" / "pea_universe.yaml"
_DEFAULT_WARNINGS_PATH = _ROOT / "database" / "eligibility_warnings.json"


class UniverseManager:
    """Orchestrates PEA universe synchronization and regulatory eligibility monitoring."""

    def __init__(
        self,
        universe_path: Optional[Path | str] = None,
        warnings_path: Optional[Path | str] = None,
    ) -> None:
        self.universe_path = Path(universe_path) if universe_path else _DEFAULT_UNIVERSE_PATH
        self.warnings_path = Path(warnings_path) if warnings_path else _DEFAULT_WARNINGS_PATH
        self.warnings_path.parent.mkdir(parents=True, exist_ok=True)

    def load_tracked_tickers(self) -> List[str]:
        """Load all tickers tracked in the universe yaml."""
        if not self.universe_path.exists():
            logger.warning("Universe file not found at %s", self.universe_path)
            return []

        try:
            with open(self.universe_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            tickers: Set[str] = set()
            core = data.get("core", {})
            if isinstance(core, dict):
                for t in core.keys():
                    tickers.add(str(t).strip().upper())
            elif isinstance(core, list):
                for t in core:
                    tickers.add(str(t).strip().upper())

            satellites = data.get("satellites", {})
            if isinstance(satellites, dict):
                for sec, sec_tickers in satellites.items():
                    if isinstance(sec_tickers, list):
                        for t in sec_tickers:
                            tickers.add(str(t).strip().upper())
                    elif isinstance(sec_tickers, dict):
                        for t in sec_tickers.keys():
                            tickers.add(str(t).strip().upper())

            return sorted(list(tickers))
        except Exception as exc:
            logger.exception("Failed to parse tracked tickers from YAML: %s", exc)
            return []

    def sync_eligibility(self) -> Dict[str, str]:
        """Scrape latest PEA constituents and verify status for all tracked tickers.

        Returns:
            Dict[str, str]: Map of {ticker: warning_message} for tickers that lost PEA status.
        """
        tracked = self.load_tracked_tickers()
        if not tracked:
            return {}

        logger.info("Syncing PEA eligibility for %d tracked asset(s)...", len(tracked))

        scraped_tickers: Set[str] = set()
        try:
            scraper = BoursoramaScraper()
            items = scraper.get_pea_universe()
            if items:
                for it in items:
                    t = str(it.get("ticker", "")).strip().upper()
                    if t:
                        scraped_tickers.add(t)
                        # Also handle suffix variations e.g. MC vs MC.PA
                        if "." in t:
                            scraped_tickers.add(t.split(".")[0])
        except Exception as exc:
            logger.warning("Live Boursorama scraping failed or blocked: %s", exc)

        warnings: Dict[str, str] = {}
        today_str = date.today().isoformat()

        # If live scraping succeeded, detect any discrepancies
        if scraped_tickers:
            for t in tracked:
                # Exclude synthetic indices / macro benchmarks (starts with ^ or =)
                if t.startswith("^") or "=" in t:
                    continue
                # Normalize base ticker
                base_t = t.split(".")[0] if "." in t else t
                if t not in scraped_tickers and base_t not in scraped_tickers:
                    msg = f"Lost or unconfirmed PEA eligibility on {today_str} (Boursorama registry check)"
                    warnings[t] = msg
                    logger.warning("ELIGIBILITY WARNING: Tracked asset %s %s", t, msg)

        # Persist warnings to disk
        try:
            with open(self.warnings_path, "w", encoding="utf-8") as f:
                json.dump(warnings, f, indent=2, ensure_ascii=False)
            logger.info("Saved eligibility warnings to %s (%d warnings).", self.warnings_path, len(warnings))
        except Exception as exc:
            logger.error("Failed to persist eligibility warnings to %s: %s", self.warnings_path, exc)

        return warnings
