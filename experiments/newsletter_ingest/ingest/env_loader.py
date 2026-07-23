"""Load sandbox ``.env`` without touching production ``config/api_keys.env``."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def load_sandbox_env(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE env file into a dict.

    Args:
        path: Path to the sandbox ``.env``.

    Returns:
        dict[str, str]: Uppercase keys; empty dict if file missing.
    """
    out: dict[str, str] = {}
    if not path.exists():
        logger.warning("Sandbox env file not found: %s", path)
        return out
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key:
                out[key] = val
    except OSError as exc:
        logger.error("Could not read %s: %s", path, exc)
    return out
