"""Autonomous Earnings & Dividend Calendar Synchronizer for PEA Pollux.

Automatically scans the investable PEA universe via yfinance calendar hooks,
resolves upcoming corporate earnings announcements and ex-dividend dates,
and updates ``config/earnings_calendar.yaml`` to protect against volatility gap-downs.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import yaml

try:
    import yfinance as yf
except ImportError:
    yf = None

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_DIR = _PROJECT_ROOT / "config"


def _extract_universe_tickers(universe_path: Path, max_tickers: int = 100) -> List[str]:
    """Extract prioritized tickers from pea_universe.yaml (srd=true, pea_pme=true first)."""
    if not universe_path.exists():
        return ["MC.PA", "OR.PA", "AI.PA", "SAN.PA", "TTE.PA", "BNP.PA", "AIR.PA", "SU.PA", "EL.PA", "CW8.PA"]

    try:
        with open(universe_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception as exc:
        logger.warning("Could not parse pea_universe.yaml: %s", exc)
        return ["MC.PA", "OR.PA", "AI.PA", "SAN.PA", "TTE.PA"]

    universe = data.get("universe", {})
    priority_tickers: List[str] = []
    other_tickers: List[str] = []

    for sector, items in universe.items():
        if isinstance(items, list):
            for entry in items:
                if isinstance(entry, dict) and "ticker" in entry:
                    t = str(entry["ticker"]).strip().upper()
                    if entry.get("srd") or entry.get("held"):
                        priority_tickers.append(t)
                    else:
                        other_tickers.append(t)
                elif isinstance(entry, str):
                    other_tickers.append(entry.strip().upper())

    combined = list(dict.fromkeys(priority_tickers + other_tickers))
    return combined[:max_tickers]


def fetch_ticker_corporate_events(ticker: str) -> Dict[str, str]:
    """Fetch upcoming earnings dates and ex-dividend dates for a single ticker via yfinance.

    Args:
        ticker: Yahoo Finance ticker (e.g. 'MC.PA').

    Returns:
        Dict[str, str]: Mapping of "YYYY-MM-DD" -> "Event Name".
    """
    events: Dict[str, str] = {}
    if yf is None:
        return events

    try:
        t = yf.Ticker(ticker)
        # 1. Check calendar
        cal = getattr(t, "calendar", None)
        if cal is not None:
            if isinstance(cal, dict):
                # Standard dict format
                for key in ("Earnings Date", "Earnings High", "Earnings Low", "Earnings Average"):
                    val = cal.get(key)
                    if isinstance(val, list):
                        for item in val:
                            if isinstance(item, (datetime, date)):
                                d_str = item.strftime("%Y-%m-%d")
                                events[d_str] = "Earnings Report"
                            elif isinstance(item, str) and len(item) >= 10:
                                events[item[:10]] = "Earnings Report"
                    elif isinstance(val, (datetime, date)):
                        events[val.strftime("%Y-%m-%d")] = "Earnings Report"

                # Ex-Dividend Date
                ex_div = cal.get("Ex-Dividend Date") or cal.get("Dividend Date")
                if isinstance(ex_div, (datetime, date)):
                    events[ex_div.strftime("%Y-%m-%d")] = "Ex-Dividend"
                elif isinstance(ex_div, list) and ex_div:
                    first = ex_div[0]
                    if isinstance(first, (datetime, date)):
                        events[first.strftime("%Y-%m-%d")] = "Ex-Dividend"

            elif hasattr(cal, "T") or hasattr(cal, "iterrows"):
                # DataFrame format
                for col in cal.columns if hasattr(cal, "columns") else []:
                    c_str = str(col).lower()
                    if "earnings" in c_str or "date" in c_str:
                        for item in cal[col].dropna():
                            if isinstance(item, (datetime, date)):
                                events[item.strftime("%Y-%m-%d")] = "Earnings Report"

        # 2. Check info for upcoming ex-dividend or earnings timestamp
        info = getattr(t, "info", None)
        if isinstance(info, dict):
            ex_ts = info.get("exDividendDate")
            if ex_ts and isinstance(ex_ts, (int, float)) and ex_ts > 0:
                try:
                    d_obj = datetime.fromtimestamp(ex_ts, tz=timezone.utc).date()
                    if d_obj >= date.today() - timedelta(days=5):
                        events[d_obj.strftime("%Y-%m-%d")] = "Ex-Dividend"
                except Exception:
                    pass

    except Exception as exc:
        logger.debug("Failed to fetch corporate calendar for %s: %s", ticker, exc)

    return events


def run_earnings_sync(config_dir: str | Path | None = None, max_tickers: int = 100) -> int:
    """Synchronize corporate earnings and dividend dates for the PEA universe into earnings_calendar.yaml.

    Args:
        config_dir: Directory containing config YAML files.
        max_tickers: Maximum number of universe tickers to inspect.

    Returns:
        int: Number of tickers with upcoming corporate events synchronized.
    """
    conf_path = Path(config_dir) if config_dir else _DEFAULT_CONFIG_DIR
    universe_path = conf_path / "pea_universe.yaml"
    earnings_path = conf_path / "earnings_calendar.yaml"

    tickers = _extract_universe_tickers(universe_path, max_tickers=max_tickers)
    logger.info("Starting autonomous earnings calendar sync for %d PEA tickers...", len(tickers))

    existing_calendar: Dict[str, Dict[str, str]] = {}
    if earnings_path.exists():
        try:
            with open(earnings_path, "r", encoding="utf-8") as fh:
                raw = yaml.safe_load(fh) or {}
                events = raw.get("events", raw) if isinstance(raw, dict) else {}
                if isinstance(events, dict):
                    existing_calendar = {
                        str(t).upper(): {str(k): str(v) for k, v in d.items()}
                        for t, d in events.items()
                        if isinstance(d, dict)
                    }
        except Exception as exc:
            logger.warning("Could not parse existing earnings_calendar.yaml: %s", exc)

    updated_count = 0
    today_str = date.today().strftime("%Y-%m-%d")

    for ticker in tickers:
        new_events = fetch_ticker_corporate_events(ticker)
        if new_events:
            ticker_events = existing_calendar.get(ticker, {})
            # Merge while keeping future dates
            for d_str, ev_name in new_events.items():
                if d_str >= today_str:
                    ticker_events[d_str] = ev_name
            if ticker_events:
                existing_calendar[ticker] = ticker_events
                updated_count += 1

    # Clean old past dates (older than 14 days)
    cutoff_str = (date.today() - timedelta(days=14)).strftime("%Y-%m-%d")
    cleaned_calendar: Dict[str, Dict[str, str]] = {}
    for t, dates in existing_calendar.items():
        future_dates = {d: name for d, name in dates.items() if d >= cutoff_str}
        if future_dates:
            cleaned_calendar[t] = future_dates

    # Write back to earnings_calendar.yaml
    output_payload = {
        "events": cleaned_calendar
    }

    try:
        with open(earnings_path, "w", encoding="utf-8") as fh:
            yaml.dump(output_payload, fh, default_flow_style=False, sort_keys=True, allow_unicode=True)
        logger.info(
            "Earnings calendar synchronized: %d active tickers saved to %s.",
            len(cleaned_calendar),
            earnings_path,
        )
    except Exception as exc:
        logger.error("Failed to write earnings_calendar.yaml: %s", exc)

    return len(cleaned_calendar)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Running autonomous earnings calendar sync...")
    count = run_earnings_sync(max_tickers=20)
    print(f"Sync complete. {count} tickers with upcoming events.")
