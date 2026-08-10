"""Raw Data Storage (Bronze Layer) for PEA Sniper Terminal.

Persists raw, unmodified API responses (Finnhub, FMP, AMF, Boursorama, ECB, etc.)
into structured Bronze-layer JSON artifacts in ``database/raw_bronze/`` for auditability,
backtesting replay, and offline data pipeline ingestion.
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


def save_raw_response(
    source: str,
    ticker: str,
    payload: Union[dict, list, str, bytes],
    base_dir: Union[Path, str] = _DEFAULT_BRONZE_DIR,
) -> Path:
    """Save raw API response into the Bronze storage directory.

    Args:
        source: Upstream provider (e.g. 'finnhub', 'fmp', 'amf', 'bourso', 'ecb').
        ticker: Symbol or identifier (e.g. 'MC.PA', 'GLOBAL').
        payload: Response dictionary, list, JSON string, or text.
        base_dir: Root directory for raw bronze storage.

    Returns:
        Path: The absolute path of the persisted JSON file.
    """
    clean_source = source.lower().strip().replace(" ", "_")
    clean_ticker = ticker.upper().strip().replace(".", "_")
    now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")

    target_dir = Path(base_dir) / clean_source
    target_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{clean_ticker}_{now_str}.json"
    file_path = target_dir / filename

    data_to_write = {
        "_bronze_meta": {
            "source": clean_source,
            "ticker": ticker,
            "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        },
        "payload": payload,
    }

    try:
        with open(file_path, "w", encoding="utf-8") as fh:
            if isinstance(payload, (dict, list)):
                json.dump(data_to_write, fh, ensure_ascii=False, indent=2)
            elif isinstance(payload, str):
                try:
                    # Attempt to parse as JSON if valid JSON string
                    parsed = json.loads(payload)
                    data_to_write["payload"] = parsed
                    json.dump(data_to_write, fh, ensure_ascii=False, indent=2)
                except Exception:
                    fh.write(json.dumps(data_to_write, ensure_ascii=False, indent=2))
            else:
                fh.write(json.dumps(data_to_write, ensure_ascii=False, indent=2))
        logger.debug("Raw Bronze response saved: %s", file_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to save raw bronze response for %s (%s): %s", ticker, source, exc)

    return file_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    p = save_raw_response("finnhub", "MC.PA", {"test": "data", "status": "ok"})
    print("Saved Bronze Raw Artifact:", p)
