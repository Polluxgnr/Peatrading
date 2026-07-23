"""Write timestamped JSON under the sandbox ``output/`` folder only."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def write_output(payload: dict[str, Any], out_dir: Path) -> Path:
    """Serialize ``payload`` to ``output/ingest_YYYYMMDD_HHMMSS.json``.

    Args:
        payload: JSON-serializable ingest result.
        out_dir: Destination directory (created if needed).

    Returns:
        Path: Written file path.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"ingest_{stamp}.json"
    body = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    path.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Sandbox output written (%d bytes).", path.stat().st_size)
    return path
