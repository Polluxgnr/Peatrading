"""Native ``config/api_keys.env`` loader (no python-dotenv dependency)."""

from __future__ import annotations

import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_ENV = _PROJECT_ROOT / "config" / "api_keys.env"


def load_api_keys(env_path: Path | str | None = None) -> Path | None:
    """Parse KEY=VALUE lines into ``os.environ`` (does not override existing).

    Returns:
        Path loaded, or ``None`` if the file is missing.
    """
    path = Path(env_path) if env_path else _DEFAULT_ENV
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            value = value.strip().strip("'").strip('"')
            # Prefer already-exported shell env over file (CI / Docker).
            if key not in os.environ or not str(os.environ.get(key) or "").strip():
                os.environ[key] = value
    return path
