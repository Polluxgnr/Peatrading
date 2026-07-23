"""Per-ticker earnings / dividend blackout (same pattern as MacroVetoEngine).

Blocks new satellite buys when a corporate event for that ticker falls within
``EARNINGS_BLACKOUT_DAYS``. Calendar is maintained in
``config/earnings_calendar.yaml`` (manual seed; later auto-synced from an API).
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Dict, Tuple

import yaml

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_DIR = _PROJECT_ROOT / "config"


class EarningsBlackoutEngine:
    """Vetoes buys near ticker-specific earnings/dividend dates."""

    def __init__(self, config_dir: str | Path | None = None) -> None:
        config_path = Path(config_dir) if config_dir else _DEFAULT_CONFIG_DIR
        risk = self._load_yaml(config_path / "risk_params.yaml")
        cal_raw = self._load_yaml(config_path / "earnings_calendar.yaml")
        self.blackout_days: int = int(risk.get("EARNINGS_BLACKOUT_DAYS", 2))
        # ticker -> {date -> event_name}
        self.calendar: Dict[str, Dict[dt.date, str]] = self._parse_calendar(cal_raw)
        logger.debug(
            "EarningsBlackoutEngine: window=%d day(s), %d ticker(s).",
            self.blackout_days,
            len(self.calendar),
        )

    @staticmethod
    def _load_yaml(path: Path) -> dict:
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    @staticmethod
    def _parse_calendar(raw: dict) -> Dict[str, Dict[dt.date, str]]:
        """Accept ``events: { TICKER: { YYYY-MM-DD: name } }``."""
        events = raw.get("events", raw) if isinstance(raw, dict) else {}
        parsed: Dict[str, Dict[dt.date, str]] = {}
        if not isinstance(events, dict):
            return parsed
        for ticker, dates in events.items():
            if not isinstance(dates, dict):
                continue
            bucket: Dict[dt.date, str] = {}
            for key, name in dates.items():
                if isinstance(key, dt.datetime):
                    event_date = key.date()
                elif isinstance(key, dt.date):
                    event_date = key
                else:
                    try:
                        event_date = dt.date.fromisoformat(str(key))
                    except ValueError:
                        continue
                bucket[event_date] = str(name)
            if bucket:
                parsed[str(ticker)] = bucket
        return parsed

    def check_veto(
        self, ticker: str, target_date: dt.date
    ) -> Tuple[bool, str]:
        """Return ``(True, reason)`` if ``ticker`` is in an earnings blackout."""
        if isinstance(target_date, dt.datetime):
            target_date = target_date.date()
        events = self.calendar.get(ticker) or {}
        for event_date, name in sorted(events.items()):
            delta = (event_date - target_date).days
            if 0 <= delta <= self.blackout_days:
                if delta == 0:
                    reason = f"EARNINGS BLACKOUT: {name} today ({ticker})"
                elif delta == 1:
                    reason = f"EARNINGS BLACKOUT: {name} in 1 day ({ticker})"
                else:
                    reason = (
                        f"EARNINGS BLACKOUT: {name} in {delta} days ({ticker})"
                    )
                logger.info("%s", reason)
                return True, reason
        return False, "Clear"
