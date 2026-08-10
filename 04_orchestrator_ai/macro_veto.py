"""Macro Veto Engine for PEA Sniper Terminal V-Prime.

Blocks new offensive signals when a high-impact macro event (ECB/FED decision,
CPI, NFP) falls within a configurable window. Running this cheap check before
the heavy correlation math keeps the cascade CPU-efficient.

Pure logical routing: no LLMs, no APIs. All paths use ``pathlib`` for
cross-platform compatibility (Windows x64/ARM and Linux).
"""

import datetime as dt
import logging
from pathlib import Path
from typing import Dict, Tuple

import yaml

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_DIR = _PROJECT_ROOT / "config"


class MacroVetoEngine:
    """Vetoes new trades near scheduled high-impact macro events.

    Attributes:
        veto_days_before: Number of days before an event during which new
            trades are blocked.
        calendar: Mapping of event date -> event name.
    """

    def __init__(self, config_dir: str | Path | None = None) -> None:
        """Load the veto window and the macro calendar.

        Args:
            config_dir: Path to the ``config`` directory. Defaults to
                ``<project_root>/config``.
        """
        config_path = Path(config_dir) if config_dir else _DEFAULT_CONFIG_DIR

        risk = self._load_yaml(config_path / "risk_params.yaml")
        calendar_raw = self._load_yaml(config_path / "macro_calendar.yaml")

        self.veto_days_before: int = int(risk["MACRO_VETO_DAYS_BEFORE"])
        self.calendar: Dict[dt.date, str] = self._parse_calendar(calendar_raw)

        logger.debug(
            "MacroVetoEngine loaded: window=%d day(s), %d event(s).",
            self.veto_days_before,
            len(self.calendar),
        )

    @staticmethod
    def _load_yaml(path: Path) -> dict:
        """Load a YAML file into a dict, raising a clear error if missing."""
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    @staticmethod
    def _parse_calendar(raw: dict) -> Dict[dt.date, str]:
        """Normalize raw YAML into a ``date -> name`` mapping.

        Accepts either a top-level ``events:`` mapping or a bare ``date: name``
        mapping. Date keys may be ``datetime.date`` (parsed by PyYAML) or ISO
        strings.
        """
        events = raw.get("events", raw) if isinstance(raw, dict) else {}
        parsed: Dict[dt.date, str] = {}
        for key, name in events.items():
            if isinstance(key, dt.datetime):
                event_date = key.date()
            elif isinstance(key, dt.date):
                event_date = key
            else:
                event_date = dt.date.fromisoformat(str(key))
            parsed[event_date] = str(name)
        return parsed

    def check_veto(self, target_date: dt.date) -> Tuple[bool, str]:
        """Check whether a trade on ``target_date`` must be vetoed.

        A veto applies when an event is scheduled on ``target_date`` or within
        the next ``veto_days_before`` days.

        Args:
            target_date: The date the trade would be placed.

        Returns:
            tuple[bool, str]: ``(True, reason)`` if vetoed, else
            ``(False, "Clear")``.
        """
        if isinstance(target_date, dt.datetime):
            target_date = target_date.date()

        for event_date, name in sorted(self.calendar.items()):
            delta = (event_date - target_date).days
            if 0 <= delta <= self.veto_days_before:
                if delta == 0:
                    reason = f"VETO: {name} today"
                elif delta == 1:
                    reason = f"VETO: {name} in 1 day"
                else:
                    reason = f"VETO: {name} in {delta} days"
                logger.info("Macro veto for %s -> %s", target_date, reason)
                return True, reason

        return False, "Clear"


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    engine = MacroVetoEngine()
    print("Window (days before):", engine.veto_days_before)
    print("Events loaded:", len(engine.calendar))

    # ECB Rate Decision seeded on 2026-07-16.
    for d in ("2026-07-14", "2026-07-15", "2026-07-16", "2026-07-25"):
        vetoed, msg = engine.check_veto(dt.date.fromisoformat(d))
        print(f"{d}: vetoed={vetoed} -> {msg}")
