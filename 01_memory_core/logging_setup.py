"""Central logging setup for PEA Sniper Terminal.

One place to configure human-readable, copy-friendly logs:

* Console: compact INFO for day-to-day ops.
* Rotating files under ``logs/``: one file per logical component, DEBUG detail
  (module, function, line) so you can audit a full pass without drowning the UI.

Usage::

    from logging_setup import setup_app_logging, get_component_logger
    setup_app_logging()                    # once at process entry
    log = get_component_logger("cascade")  # -> logs/cascade.log + console

Keep it light: this is a personal PEA terminal, not a Kubernetes fleet.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
_LOG_DIR = _ROOT / "logs"
_CONFIGURED = False

# Concise for humans watching the terminal.
_CONSOLE_FMT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
# Hyper-detailed for post-mortems / copy-paste into tickets.
_FILE_FMT = (
    "%(asctime)s | %(levelname)-7s | %(name)s | %(filename)s:%(lineno)d "
    "%(funcName)s | %(message)s"
)
_DATE_FMT = "%Y-%m-%d %H:%M:%S"


def log_dir() -> Path:
    """Return (and create) the project logs directory."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    return _LOG_DIR


def setup_app_logging(
    level: int | str = logging.INFO,
    console: bool = True,
) -> None:
    """Idempotent root logging bootstrap for CLI entrypoints.

    Args:
        level: Root level (INFO recommended; DEBUG for deep dives).
        console: Attach a StreamHandler when True.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # handlers filter; keep DEBUG available to files

    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    # Quiet noisy third parties so our own trails stay readable.
    for noisy in ("urllib3", "yfinance", "peewee", "asyncio", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if console and not any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
        for h in root.handlers
    ):
        sh = logging.StreamHandler()
        sh.setLevel(level)
        sh.setFormatter(logging.Formatter(_CONSOLE_FMT, datefmt=_DATE_FMT))
        root.addHandler(sh)

    # Shared "all" trail — every component fans into this too.
    all_path = log_dir() / "pea_sniper_all.log"
    if not any(
        isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", "") == str(all_path)
        for h in root.handlers
    ):
        fh = RotatingFileHandler(
            all_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(_FILE_FMT, datefmt=_DATE_FMT))
        root.addHandler(fh)

    _CONFIGURED = True
    logging.getLogger("logging_setup").info(
        "Logging ready — console=%s, files under %s", console, log_dir()
    )


def get_component_logger(
    component: str,
    level: int = logging.DEBUG,
    max_bytes: int = 1_500_000,
    backup_count: int = 4,
) -> logging.Logger:
    """Return a named logger that also writes ``logs/<component>.log``.

    Args:
        component: Short slug (``scheduler``, ``cascade``, ``dashboard``…).
        level: Minimum level for the component file handler.
        max_bytes: Rotate when the file exceeds this size.
        backup_count: How many rotated files to keep.

    Returns:
        logging.Logger: Ready-to-use logger (propagate to root for the all-trail).
    """
    setup_app_logging()
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in component)
    logger = logging.getLogger(safe)
    logger.setLevel(level)

    path = log_dir() / f"{safe}.log"
    already = any(
        isinstance(h, RotatingFileHandler)
        and Path(getattr(h, "baseFilename", "")).resolve() == path.resolve()
        for h in logger.handlers
    )
    if not already:
        fh = RotatingFileHandler(
            path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        fh.setLevel(level)
        fh.setFormatter(logging.Formatter(_FILE_FMT, datefmt=_DATE_FMT))
        logger.addHandler(fh)

    return logger


def list_log_files() -> list[Path]:
    """Sorted list of ``*.log`` files under ``logs/`` (newest first by mtime)."""
    d = log_dir()
    files = list(d.glob("*.log"))
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def tail_log(path: Path | str, n_lines: int = 200) -> str:
    """Return the last ``n_lines`` of a log file (UTF-8, tolerant).

    Args:
        path: Log file path.
        n_lines: How many trailing lines to return.

    Returns:
        str: Tail text, or an error message if unreadable.
    """
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"[unavailable: {exc}]"
    lines = text.splitlines()
    return "\n".join(lines[-max(1, n_lines) :])


def write_pipeline_status(payload: dict) -> Path:
    """Persist a tiny JSON heartbeat the dashboard can read (mission control).

    Args:
        payload: Must be JSON-serializable (status, timestamps, counts…).

    Returns:
        Path: Written file under ``database/pipeline_status.json``.
    """
    import json
    from datetime import datetime, timezone

    out_dir = _ROOT / "database"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "pipeline_status.json"
    body = {
        **payload,
        "written_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(body, indent=2, default=str), encoding="utf-8")
    return path


def read_pipeline_status() -> Optional[dict]:
    """Load the last pipeline heartbeat, or ``None`` if missing/corrupt."""
    import json

    path = _ROOT / "database" / "pipeline_status.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
