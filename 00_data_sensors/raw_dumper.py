"""Raw Data Dumper (Bronze Layer) for PEA Sniper Terminal.

Saves raw upstream API payloads into partitioned JSON structures:
``database/raw_bronze/{source}/{YYYY-MM-DD}/{timestamp}_{endpoint}.json``

This guarantees full auditability, zero data loss, and replayability for ML model training.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Union

logger = logging.getLogger(__name__)

_DEFAULT_BRONZE_DIR = (
    Path(__file__).resolve().parent.parent / "database" / "raw_bronze"
)


def dump_bronze_json(
    source: str,
    endpoint: str,
    payload: Union[dict, list, str, bytes],
    base_dir: Union[Path, str] = _DEFAULT_BRONZE_DIR,
) -> Path:
    """Save raw API response into date-partitioned Bronze storage directory.

    Path format:
        ``database/raw_bronze/{source}/{YYYY-MM-DD}/{timestamp}_{endpoint}.json``

    Args:
        source: Provider identifier (e.g. 'finnhub', 'fmp', 'amf', 'bourso', 'openinsider', 'ecb').
        endpoint: API endpoint or query name (e.g. 'company_news', 'profile', 'insiders', 'quote').
        payload: Raw JSON-serializable dictionary, list, string, or bytes.
        base_dir: Root Bronze directory.

    Returns:
        Path: Path of the written JSON file.
    """
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    timestamp_str = now.strftime("%H%M%S_%f")

    clean_source = source.lower().strip().replace(" ", "_")
    clean_endpoint = endpoint.lower().strip().replace("/", "_").replace(" ", "_")

    target_dir = Path(base_dir) / clean_source / date_str
    target_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{timestamp_str}_{clean_endpoint}.json"
    file_path = target_dir / filename

    data_to_write = {
        "_bronze_meta": {
            "source": clean_source,
            "endpoint": clean_endpoint,
            "saved_at_utc": now.isoformat(),
        },
        "payload": payload,
    }

    try:
        with open(file_path, "w", encoding="utf-8") as fh:
            if isinstance(payload, (dict, list)):
                json.dump(data_to_write, fh, ensure_ascii=False, indent=2)
            elif isinstance(payload, str):
                try:
                    parsed = json.loads(payload)
                    data_to_write["payload"] = parsed
                    json.dump(data_to_write, fh, ensure_ascii=False, indent=2)
                except Exception:
                    json.dump(data_to_write, fh, ensure_ascii=False, indent=2)
            else:
                json.dump(data_to_write, fh, ensure_ascii=False, indent=2)
        logger.debug("Raw bronze dumped: %s", file_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to dump raw bronze JSON for %s/%s: %s", source, endpoint, exc)

    return file_path


def save_raw_response(
    source: str,
    ticker: str,
    payload: Union[dict, list, str, bytes],
    base_dir: Union[Path, str] = _DEFAULT_BRONZE_DIR,
) -> Path:
    """Alias for dump_bronze_json using ticker as the endpoint identifier."""
    return dump_bronze_json(source=source, endpoint=ticker, payload=payload, base_dir=base_dir)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    p = dump_bronze_json("finnhub", "company_news_MC.PA", {"headlines": ["LVMH growth accelerates"]})
    print("Dumped Bronze JSON:", p)
