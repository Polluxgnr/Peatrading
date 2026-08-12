# PEA Pollux — Memory Core, State Persistence & Data Contracts
Generated: `2026-08-12 10:00 UTC` | File Count: `8`
Institutional Systematic Decision Support Architecture for French PEA.
---
## Included Files Index
- [01_memory_core/__init__.py](#file-01_memory_core-__init__-py)
- [01_memory_core/config_validator.py](#file-01_memory_core-config_validator-py)
- [01_memory_core/data_models.py](#file-01_memory_core-data_models-py)
- [01_memory_core/duckdb_manager.py](#file-01_memory_core-duckdb_manager-py)
- [01_memory_core/env_loader.py](#file-01_memory_core-env_loader-py)
- [01_memory_core/logging_setup.py](#file-01_memory_core-logging_setup-py)
- [01_memory_core/profile_builder.py](#file-01_memory_core-profile_builder-py)
- [01_memory_core/sqlite_portfolio.py](#file-01_memory_core-sqlite_portfolio-py)

---
## FILE: 01_memory_core/__init__.py
```python
"""Memory Core & State Persistence package for PEA Pollux."""

from .data_models import PortfolioState, Position, Signal, SignalStatus, SignalType
from .duckdb_manager import TimeSeriesDB
from .sqlite_portfolio import PortfolioDB

__all__ = [
    "PortfolioDB",
    "TimeSeriesDB",
    "PortfolioState",
    "Position",
    "Signal",
    "SignalStatus",
    "SignalType",
]
```

## FILE: 01_memory_core/config_validator.py
```python
"""Strict Pydantic validation for ``risk_params.yaml``.

Every key in the YAML must be declared here. Unknown keys raise on load so
config/code drift cannot hide silently.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_RISK_PATH = _PROJECT_ROOT / "config" / "risk_params.yaml"


class RiskParamsConfig(BaseModel):
    """Institutional risk parameters — single source of truth."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Position sizing
    KELLY_FRACTION: float = Field(0.5, gt=0, le=1)
    MAX_SINGLE_POSITION_PCT: float = Field(0.15, gt=0, le=1)
    MAX_SECTOR_WEIGHT_PCT: float = Field(0.25, gt=0, le=1)
    MAX_ALLOCATION_PER_DAY_PCT: float = Field(0.03, gt=0, le=1)

    # Circuit breakers
    DAILY_MAX_LOSS_PCT: float = Field(-0.005, lt=0)
    WEEKLY_MAX_LOSS_PCT: float = Field(-0.02, lt=0)
    MONTHLY_MAX_LOSS_PCT: float = Field(-0.05, lt=0)

    # Correlation
    MAX_CORRELATION_TO_PORTFOLIO: float = Field(0.70, gt=0, le=1)
    MAX_CORRELATION_SAME_SECTOR: float = Field(0.80, gt=0, le=1)
    CORRELATION_LOOKBACK_DAYS: int = Field(60, ge=10, le=500)

    # Signals
    CONVICTION_EMIT_FLOOR: float = Field(65.0, ge=0, le=100)
    SIGNAL_SELL_THRESHOLD: float = Field(35.0, ge=0, le=100)
    SIGNAL_VALIDITY_HOURS: int = Field(12, ge=1, le=168)
    MACRO_VETO_DAYS_BEFORE: int = Field(3, ge=0, le=30)
    EARNINGS_BLACKOUT_DAYS: int = Field(2, ge=0, le=30)
    RSI_OVERSOLD_THRESHOLD: float = Field(30.0, gt=0, lt=100)
    MIN_LIQUIDITY_ADV: float = Field(50_000, ge=0)
    MAX_POSITIONS_TOTAL: int = Field(12, ge=1, le=100)

    # Core / satellite
    CORE_TICKER: str = Field("CW8.PA", min_length=1)
    MAX_IDLE_CASH_PCT: float = Field(0.02, ge=0, le=1)
    CORE_TARGET_PCT: float = Field(0.70, ge=0, le=1)
    CORE_CRASH_TARGET_PCT: float = Field(0.75, ge=0, le=1)
    CORE_DCA_MAX_TRANCHE_PCT: float = Field(0.05, gt=0, le=1)
    SATELLITE_MAX_BUDGET_PCT: float = Field(0.30, ge=0, le=1)

    # Volatility / VIX
    VOLATILITY_REFERENCE: float = Field(0.20, gt=0)
    VOLATILITY_MAX_FACTOR: float = Field(1.5, gt=0)
    VIX_PANIC_THRESHOLD: float = Field(30.0, gt=0)

    # Rebalancing / exits
    REBALANCE_PROFIT_SHAVE_PCT: float = Field(0.20, gt=0, le=1)
    REBALANCE_PROFIT_TRIGGER_PCT: float = Field(20.0, gt=0)
    REBALANCE_ATR_STOP_MULT: float = Field(2.5, gt=0)


def _resolve_risk_path(config_path: str | Path | None) -> Path:
    if config_path is None:
        return _DEFAULT_RISK_PATH
    p = Path(config_path)
    if p.is_file():
        return p
    return p / "risk_params.yaml"


def load_risk_config(config_path: str | Path | None = None) -> RiskParamsConfig:
    """Load and validate ``risk_params.yaml``. Crash on malformed config."""
    path = _resolve_risk_path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"risk_params.yaml not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    try:
        return RiskParamsConfig.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(
            f"Invalid risk_params.yaml at {path}:\n{exc}"
        ) from exc


# Module-level singleton — validated once at import for fast access.
try:
    RISK: RiskParamsConfig = load_risk_config()
except (FileNotFoundError, ValueError):
    RISK = None  # type: ignore[assignment]
```

## FILE: 01_memory_core/data_models.py
```python
"""Strict data contracts for PEA Sniper Terminal V-Prime.

This module defines the Pydantic V2 models that flow between every layer of the
system (data sensors -> quant engine -> risk portfolio -> orchestrator ->
interfaces). Validating objects at module boundaries prevents malformed data
from ever reaching the risk or execution logic.

No trading logic, API calls, or database code lives here by design.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field


def _utcnow() -> datetime:
    """Return the current timezone-aware UTC timestamp.

    Returns:
        datetime: The current time in UTC.
    """
    return datetime.now(timezone.utc)


class SignalType(str, Enum):
    """Direction of a trading signal."""

    BUY = "BUY"
    SELL = "SELL"


class SignalStatus(str, Enum):
    """Lifecycle state of a signal as it moves through the orchestrator."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    EXECUTED = "EXECUTED"


class MarketRegime(str, Enum):
    """Coarse classification of the prevailing market environment."""

    BULL = "BULL"
    BEAR = "BEAR"
    CHOPPY = "CHOPPY"
    VOLATILE = "VOLATILE"


class Position(BaseModel):
    """A single open holding in the PEA portfolio.

    Attributes:
        ticker: Yahoo Finance ticker symbol (e.g. ``MC.PA``).
        qty_shares: Number of whole shares held. PEA forbids fractional shares.
        avg_entry_price: Volume-weighted average entry price in EUR.
        current_price: Latest known market price in EUR.
        sector: Sector bucket used by the correlation firewall.
    """

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    ticker: str = Field(..., min_length=1, description="Yahoo Finance ticker.")
    qty_shares: int = Field(..., ge=0, description="Whole shares (no fractions).")
    avg_entry_price: float = Field(..., gt=0, description="Avg entry price (EUR).")
    current_price: float = Field(..., gt=0, description="Latest price (EUR).")
    sector: str = Field(..., min_length=1, description="Sector classification.")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def market_value(self) -> float:
        """Current market value of the position in EUR.

        Returns:
            float: ``current_price * qty_shares``.
        """
        return self.current_price * self.qty_shares

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unrealized_pnl_pct(self) -> float:
        """Unrealized profit/loss as a fraction of the entry price.

        Returns:
            float: ``(current_price - avg_entry_price) / avg_entry_price``.
                A value of ``0.10`` represents a +10% unrealized gain.
        """
        return (self.current_price - self.avg_entry_price) / self.avg_entry_price


class PortfolioState(BaseModel):
    """Snapshot of the full portfolio at a point in time.

    Attributes:
        cash_available: Uninvested cash in EUR.
        total_equity: Total account value (cash + positions market value) in EUR.
        positions: List of currently open positions.
        last_updated: Timestamp of this snapshot (UTC).
    """

    model_config = ConfigDict(validate_assignment=True)

    cash_available: float = Field(..., ge=0, description="Uninvested cash (EUR).")
    total_equity: float = Field(..., ge=0, description="Total account value (EUR).")
    positions: List[Position] = Field(default_factory=list)
    last_updated: datetime = Field(default_factory=_utcnow)

    def get_sector_weight(self, sector_name: str) -> float:
        """Compute the fraction of total equity allocated to a sector.

        Args:
            sector_name: Sector to measure (case-insensitive match).

        Returns:
            float: Sector market value divided by ``total_equity``. Returns
                ``0.0`` when total equity is zero to avoid division errors.
        """
        if self.total_equity <= 0:
            return 0.0
        sector_value = sum(
            pos.market_value
            for pos in self.positions
            if pos.sector.casefold() == sector_name.casefold()
        )
        return sector_value / self.total_equity


class Signal(BaseModel):
    """A candidate trade produced by the quant engine.

    LLMs never create these; they are generated purely from mathematical
    conditions and only explained downstream in the interface layer.

    Attributes:
        id: Unique identifier (UUID4 hex string).
        ticker: Yahoo Finance ticker the signal refers to.
        signal_type: BUY or SELL.
        status: Current lifecycle state (defaults to PENDING).
        score: Composite conviction score from 0 to 100.
        target_qty: Whole-share quantity, set later by the position sizer.
        created_at: Emission timestamp (UTC).
        reason: Human-readable explanation surfaced in the UI.
    """

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    id: str = Field(default_factory=lambda: uuid4().hex, description="UUID4 id.")
    ticker: str = Field(..., min_length=1, description="Target ticker.")
    signal_type: SignalType = Field(..., description="BUY or SELL.")
    status: SignalStatus = Field(default=SignalStatus.PENDING)
    score: float = Field(..., ge=0, le=100, description="Conviction score 0-100.")
    target_qty: Optional[int] = Field(
        default=None, ge=0, description="Whole shares set after sizing."
    )
    created_at: datetime = Field(default_factory=_utcnow)
    reason: str = Field(default="", description="Explanation for the UI.")
    lineage: dict = Field(default_factory=dict, description="Feature snapshot dump for ML training replay.")
```

## FILE: 01_memory_core/duckdb_manager.py
```python
"""DuckDB time-series engine for PEA Sniper Terminal V-Prime.

DuckDB stores heavy OHLCV history and serves fast columnar reads to the quant
engine (pandas-ta). This is a pure I/O layer: no indicator math, no trading
logic, no API fetching lives here.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

try:
    import duckdb
except ImportError:
    duckdb = None  # type: ignore[assignment]

import pandas as pd

logger = logging.getLogger(__name__)

# database/ lives at the project root (one level up from 01_memory_core/).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DB_PATH = _PROJECT_ROOT / "database" / "timeseries.duckdb"

# Canonical OHLCV column order used for inserts/reads.
_OHLCV_COLUMNS = ["Ticker", "Date", "Open", "High", "Low", "Close", "Volume"]


class TimeSeriesDB:
    """Persistence gateway for OHLCV time-series stored in DuckDB.

    Attributes:
        db_path: Absolute path to the DuckDB database file.
    """

    def __init__(self, db_path: Optional[Path | str] = None) -> None:
        """Initialize the manager and ensure the database directory exists.

        Args:
            db_path: Optional custom path to the DuckDB file. Defaults to
                ``<project_root>/database/timeseries.duckdb``.
        """
        self.db_path: Path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        logger.debug("TimeSeriesDB using database at %s", self.db_path)

    @contextmanager
    def _connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        """Yield a DuckDB connection that always closes.

        Yields:
            duckdb.DuckDBPyConnection: An open connection.

        Raises:
            duckdb.Error: Propagated if any DB error occurs.
        """
        if duckdb is None:
            raise RuntimeError("DuckDB package is not installed.")
        conn = duckdb.connect(str(self.db_path))
        try:
            yield conn
        except Exception:
            logger.exception("DuckDB operation failed.")
            raise
        finally:
            conn.close()

    def init_db(self) -> None:
        """Create the ``ohlcv_data`` table if it does not already exist.

        A composite primary key on ``(ticker, date)`` enforces one row per
        ticker per day and enables efficient upserts.
        """
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ohlcv_data (
                        ticker  VARCHAR NOT NULL,
                        date    DATE     NOT NULL,
                        open    DOUBLE,
                        high    DOUBLE,
                        low     DOUBLE,
                        close   DOUBLE,
                        volume  BIGINT,
                        PRIMARY KEY (ticker, date)
                    );
                    """
                )
            logger.info("DuckDB schema initialized at %s", self.db_path)
        except duckdb.Error:
            logger.exception("Failed to initialize DuckDB schema.")
            raise

    def upsert_ohlcv(self, df: pd.DataFrame) -> int:
        """Insert or replace OHLCV rows from a DataFrame.

        Args:
            df: DataFrame with columns ``Ticker``, ``Date``, ``Open``, ``High``,
                ``Low``, ``Close`` and ``Volume`` (typically from yfinance).

        Returns:
            int: The number of rows submitted for upsert.

        Raises:
            ValueError: If required columns are missing.
            duckdb.Error: If the database operation fails.
        """
        if df is None or df.empty:
            logger.warning("upsert_ohlcv received an empty DataFrame; skipping.")
            return 0

        missing = [c for c in _OHLCV_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"DataFrame missing required columns: {missing}")

        # Work on a normalized copy in the canonical column order.
        payload = df[_OHLCV_COLUMNS].copy()
        payload["Date"] = pd.to_datetime(payload["Date"]).dt.date

        try:
            with self._connect() as conn:
                # Register the DataFrame so DuckDB can read it directly.
                conn.register("incoming_ohlcv", payload)
                conn.execute(
                    """
                    INSERT INTO ohlcv_data
                        (ticker, date, open, high, low, close, volume)
                    SELECT Ticker, Date, Open, High, Low, Close, Volume
                    FROM incoming_ohlcv
                    ON CONFLICT (ticker, date) DO UPDATE SET
                        open   = excluded.open,
                        high   = excluded.high,
                        low    = excluded.low,
                        close  = excluded.close,
                        volume = excluded.volume;
                    """
                )
                conn.unregister("incoming_ohlcv")
            logger.info("Upserted %d OHLCV rows into DuckDB.", len(payload))
            return len(payload)
        except duckdb.Error:
            logger.exception("Failed to upsert OHLCV data.")
            raise

    def get_historical_prices(self, ticker: str, days: int = 252) -> pd.DataFrame:
        """Fetch the most recent ``days`` of OHLCV for a ticker, chronologically.

        Args:
            ticker: The ticker symbol to query.
            days: Number of most-recent trading days to return (default 252).

        Returns:
            pd.DataFrame: Columns ``Ticker``, ``Date``, ``Open``, ``High``,
            ``Low``, ``Close``, ``Volume`` sorted ascending by date and ready
            for pandas-ta. Empty DataFrame (with correct columns) if none found.
        """
        try:
            with self._connect() as conn:
                # Take the last N rows by date, then re-sort ascending so the
                # output is chronological for indicator calculations.
                result = conn.execute(
                    """
                    SELECT ticker AS Ticker,
                           date   AS Date,
                           open   AS Open,
                           high   AS High,
                           low    AS Low,
                           close  AS Close,
                           volume AS Volume
                    FROM (
                        SELECT *
                        FROM ohlcv_data
                        WHERE ticker = ?
                        ORDER BY date DESC
                        LIMIT ?
                    )
                    ORDER BY date ASC;
                    """,
                    [ticker, days],
                ).fetch_df()
            logger.debug(
                "Fetched %d rows of history for %s.", len(result), ticker
            )
            if result.empty:
                return pd.DataFrame(columns=_OHLCV_COLUMNS)
            return result
        except duckdb.Error:
            logger.exception("Failed to fetch historical prices for %s.", ticker)
            raise
```

## FILE: 01_memory_core/env_loader.py
```python
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
```

## FILE: 01_memory_core/logging_setup.py
```python
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
    import warnings
    warnings.filterwarnings("ignore", category=FutureWarning, module="yfinance")
    warnings.filterwarnings("ignore", category=FutureWarning, module="pandas")

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
```

## FILE: 01_memory_core/profile_builder.py
```python
"""Profile builder logic extracted from dashboard for Night Run."""
import sys
import logging
import json
from pathlib import Path
from datetime import datetime, timezone

_ROOT = Path(__file__).resolve().parent.parent

if str(_ROOT / "01_memory_core") not in sys.path:
    sys.path.insert(0, str(_ROOT / "01_memory_core"))
from sqlite_portfolio import PortfolioDB, get_portfolio_db
from duckdb_manager import get_ts_db

if str(_ROOT / "04_orchestrator_ai") not in sys.path:
    sys.path.insert(0, str(_ROOT / "04_orchestrator_ai"))
from llm_explainer import NarrativeExplainer

import yfinance as yf
_CORE_TICKER = "CW8.PA"

def short_name(ticker: str) -> str:
    return ticker.split(".")[0]

def format_name(ticker: str) -> str:
    return ticker

def get_valuation_metrics(ticker: str) -> dict:
    return {}

def build_and_save_ticker_profile(ticker: str, include_llm: bool = False) -> dict:
    db = get_portfolio_db()
    dossier_data = get_ticker_dossier(ticker)
    fmeta = get_fundamental_metrics(ticker)
    ts_db = get_ts_db()
    ohlcv_df = ts_db.get_historical_prices(ticker, days=30)
    if ohlcv_df is not None and not ohlcv_df.empty:
        ohlcv = json.loads(ohlcv_df.to_json(orient='records', date_format='iso'))
    else:
        ohlcv = []
        
    news_items = _fetch_news_from_apis(ticker, limit=12)
    headlines = tuple(str(n.get("title") or "").strip() for n in news_items if str(n.get("title") or "").strip())
    
    if include_llm:
        try:
            synth = get_deep_news_synthesis(ticker, headlines[:15])
        except Exception as e:
            synth = f"Erreur Synthèse: {e}"
    else:
        synth = "Synthèse non générée. Cliquez sur 'Générer Synthèse IA' pour l'analyser."
        
    new_prof = {
        "ticker": ticker,
        "dossier": dossier_data,
        "fundamentals": fmeta,
        "ohlcv": ohlcv,
        "synthesis": synth,
        "news_count": len(headlines)
    }
    db.upsert_ticker_profile(ticker, new_prof)
    return new_prof

def get_fundamental_metrics(ticker: str) ->dict:
    """PE/PB/ROE/Debt-Equity from SQLite cache -> Finnhub -> yfinance fallback."""
    out = {'pe_ratio': None, 'pb_ratio': None, 'roe': None,
        'debt_to_equity': None, 'source': 'none'}
    if not ticker:
        return out
    try:
        db = get_portfolio_db()
        db.init_db()
        cached = db.get_cached_fundamentals(ticker, max_age_days=7)
        if cached:
            return {'pe_ratio': cached.get('pe_ratio'), 'pb_ratio': cached.
                get('pb_ratio'), 'roe': cached.get('roe'), 'debt_to_equity':
                cached.get('debt_to_equity'), 'source': cached.get('source'
                ) or 'sqlite_cache'}
    except Exception:
        pass
    try:
        sensors_dir = _ROOT / '00_data_sensors'
        if str(sensors_dir) not in sys.path:
            sys.path.insert(0, str(sensors_dir))
        from fundamentals_api import FundamentalsSensor
        live = FundamentalsSensor().get_basic_financials(ticker) or {}
        payload = {'pe_ratio': live.get('pe_ratio'), 'pb_ratio': live.get(
            'pb_ratio'), 'roe': live.get('roe'), 'debt_to_equity': live.get
            ('debt_to_equity'), 'source': live.get('source') or 'none'}
        if any(payload.get(k) is not None for k in ('pe_ratio', 'pb_ratio',
            'roe', 'debt_to_equity')):
            try:
                db = get_portfolio_db()
                db.init_db()
                db.upsert_fundamentals(ticker, payload)
            except Exception:
                pass
            return payload
    except Exception:
        pass
    val = get_valuation_metrics(ticker) or {}
    return {'pe_ratio': val.get('trailing_pe'), 'pb_ratio': val.get(
        'price_to_book'), 'roe': None, 'debt_to_equity': None, 'source':
        'valuation_fallback'}

def get_deep_news_synthesis(ticker: str, headlines: tuple[str, ...]) ->str:
    """Alias used by Exploration (same 24h cache key family as analysis)."""
    return get_deep_news_analysis(ticker, headlines)

def _fetch_news_from_apis(symbol: str, limit: int=6) ->list[dict]:
    """Fetch diverse news from live APIs (Boursorama + Google + Yahoo)."""
    collected: list[dict] = []
    seen_titles: set[str] = set()

    def _push(title: str, link: str, date: str, provider: str) ->None:
        import re
        key = (title or '').strip().casefold()
        if not key or key in seen_titles:
            return
        if key.startswith('http://') or key.startswith('https://'
            ) or key.startswith('http'):
            return
        spam_pattern = re.compile(
            r"(?i)(discount|free|referral|rewards|newsletter|email|sponsor|pitch deck|vc|substack|attio|seo agency|gtm|seed|founder|startup|saas|cap table|récompense|mettre [aà] jour|update your|unsubscribe|cliquez ici|abonnez-vous|subscribe|webinar|masterclass|lifestyle|promo|offre|gift|cadeau|bonus|vip|exclusive|limited time|last chance)"
            )
        if spam_pattern.search(key):
            return
        seen_titles.add(key)
        pub = (date or '').strip()
        if not pub or pub.lower() == 'recent':
            pub = datetime.now().strftime('%Y-%m-%d %H:%M')
        collected.append({'title': title.strip(), 'link': link or '#',
            'date': pub, 'provider': provider})
    try:
        scrapers_dir = _ROOT / '00_data_sensors' / 'scrapers'
        if str(scrapers_dir) not in sys.path:
            sys.path.insert(0, str(scrapers_dir))
        from bourso_scraper import BoursoramaScraper
        profile = BoursoramaScraper().get_instrument_profile(symbol)
        items = (profile or {}).get('news_items') or []
        if items:
            sentiment = (profile or {}).get('sentiment') or 'Unknown'
            elig = ','.join((profile or {}).get('eligibility') or []) or '?'
            for n in items:
                _push(n.get('title', ''), n.get('link') or '#', n.get(
                    'date') or '',
                    f"Boursorama · {n.get('provider') or 'local'} · sentiment {sentiment} · elig {elig}"
                    )
        else:
            bourso = BoursoramaScraper().get_retail_sentiment_and_news(symbol)
            headlines = (bourso or {}).get('news') or []
            sentiment = (bourso or {}).get('sentiment') or 'Unknown'
            for title in headlines:
                _push(title, '#', '', f'Boursorama · sentiment {sentiment}')
    except Exception:
        pass
    try:
        import urllib.parse
        import urllib.request
        import xml.etree.ElementTree as ET
        name = short_name(symbol)
        queries = [f'{symbol} OR {name} when:7d',
            f'{name} (bourse OR CAC OR PEA) when:7d',
            f'{name} site:lesechos.fr OR site:latribune.fr OR site:reuters.com when:14d'
            ]
        for q in queries:
            url = ('https://news.google.com/rss/search?' + urllib.parse.
                urlencode({'q': q, 'hl': 'fr', 'gl': 'FR', 'ceid': 'FR:fr'}))
            req = urllib.request.Request(url, headers={'User-Agent':
                'PEA-Pollux/1.0'})
            with urllib.request.urlopen(req, timeout=8) as resp:
                root = ET.fromstring(resp.read())
            for item in root.findall('.//item')[:8]:
                title = (item.findtext('title') or '').strip()
                link = (item.findtext('link') or '#').strip()
                pub = (item.findtext('pubDate') or '')[:16]
                source = item.find('source')
                src = (source.text if source is not None else None
                    ) or 'Google News'
                _push(title, link, pub, f'Google News · {src}')
    except Exception:
        pass
    try:
        raw = yf.Ticker(symbol).news or []
        for n in raw:
            content = n.get('content', n)
            title = content.get('title') or n.get('title') or ''
            link = content.get('clickThroughUrl', {}).get('url'
                ) or content.get('canonicalUrl', {}).get('url') or n.get('link'
                ) or '#'
            date_str = content.get('pubDate') or content.get('displayTime'
                ) or ''
            provider = (content.get('provider') or {}).get('displayName', '')
            _push(title, link, (date_str or '')[:16], provider or
                'Yahoo Finance')
    except Exception:
        pass
    return collected[:limit]

def _french_dossier_summary(ticker: str, name: str, english: str) ->str:
    """Translate/compress Yahoo longBusinessSummary to 3 short FR sentences.

    Falls back to the English snippet if OpenRouter is unavailable — never blocks.
    """
    text = (english or '').strip()
    if not text:
        return ''
    fr_markers = ' est ', ' une ', ' des ', ' société', ' groupe', ' dans '
    if sum(1 for m in fr_markers if m in text.casefold()) >= 2:
        return text[:700]
    api_key = None
    try:
        import os
        api_key = os.getenv('OPENROUTER_API_KEY')
    except Exception:
        api_key = None
    if not api_key:
        return text[:700]
    try:
        from llm_explainer import openrouter_chat
        prompt = f"""Traduis et synthétise en français, exactement 3 phrases courtes, le profil de {name} ({ticker}) pour un investisseur PEA. Pas de blabla, pas d'anglais.

{text[:1200]}"""
        out = asyncio.run(openrouter_chat([{'role': 'system', 'content':
            'Tu es un rédacteur financier FR concis.'}, {'role': 'user',
            'content': prompt}], api_key=api_key, max_tokens=220,
            temperature=0.2))
        cleaned = (out or '').strip()
        return cleaned[:700] if cleaned else text[:700]
    except Exception:
        return text[:700]

def get_ticker_dossier(ticker: str) ->dict:
    """Company identity + catalysts + risk events (yfinance + heuristics)."""
    out: dict = {'name': format_name(ticker), 'summary': '', 'sector': '',
        'industry': '', 'catalysts': [], 'risk_events': [], 'is_etf': False,
        'fundamentals': {}}
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        info = {}
    name = info.get('longName') or info.get('shortName') or short_name(ticker)
    out['name'] = name
    out['sector'] = str(info.get('sector') or '')
    out['industry'] = str(info.get('industry') or '')
    summary = str(info.get('longBusinessSummary') or '')[:700]
    quote_type = str(info.get('quoteType') or '').upper()
    out['is_etf'] = quote_type in ('ETF', 'MUTUALFUND') or ticker.endswith(
        '.PA') and ('ETF' in name.upper() or 'UCITS' in name.upper() or 
        ticker == _CORE_TICKER)
    if summary:
        out['summary'] = _french_dossier_summary(ticker, name, summary)
    elif out['is_etf'] or ticker == _CORE_TICKER:
        out['summary'] = (
            f"{name} est un ETF eligible PEA. Il replique un indice large (ex. MSCI World pour CW8) au lieu d'un risque entreprise unique. C'est l'ancre Core du systeme PEA Pollux."
            )
    else:
        out['summary'] = (
            f"{format_name(ticker)} — fiche qualitative incomplete cote Yahoo. Consulte Boursorama / le document d'enregistrement universel."
            )
    sector = (out['sector'] or '').casefold()
    catalysts = ['Publication de resultats au-dessus du consensus (EPS / CA)',
        'Guidance relevee ou nouveau contrat significatif',
        "Rachat d'actions / dividende en hausse"]
    risks = ['Profit warning ou baisse de guidance',
        'Enquete regulateur / amende majeure',
        'Choc macro (VIX panic) pendant que tu es concentre sur 1 ligne']
    if 'auto' in sector or 'consumer cyclical' in sector or 'STLAP' in ticker:
        catalysts += ['Rebond volumes Europe/US',
            'Marges industrielles stabilisees']
        risks += ['Guerre commerciale / droits de douane',
            'Retard plateformes EV']
    if 'healthcare' in sector or 'SAN.PA' in ticker:
        catalysts += ['Approbation medicament / pipeline']
        risks += ['Echec essai clinique', 'Pression prix medicaments']
    if out['is_etf'] or ticker == _CORE_TICKER:
        catalysts = ['Marche actions mondial en tendance haussiere',
            'DCA discipliné pendant les corrections (Smart DCA)',
            "Euro stable vs panier devise de l'indice"]
        risks = ['Krach global prolonge (mais le DCA achete alors plus fort)',
            "Tracking error / frais de l'ETF",
            "Force de l'euro qui pese sur un indice world en devises"]
    out['catalysts'] = catalysts[:5]
    out['risk_events'] = risks[:5]
    try:
        out['fundamentals'] = get_fundamental_metrics(ticker)
    except Exception:
        out['fundamentals'] = {}
    return out
```

## FILE: 01_memory_core/sqlite_portfolio.py
```python
"""SQLite state manager for PEA Sniper Terminal V-Prime.

This module owns application state persistence: the current PEA account
snapshot, open positions, and the audit log of every signal and its lifecycle.

It is a pure I/O layer. No trading, risk, or API logic lives here. All queries
are parameterized and every connection is context-managed so it closes cleanly
even on error.
"""

import logging
import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

import pandas as pd

# The module directory name starts with a digit, so it is not importable as a
# normal package. Adding this file's directory to sys.path lets us import the
# Phase 1 data contracts regardless of how the process is launched.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_models import Position, PortfolioState, Signal  # noqa: E402

logger = logging.getLogger(__name__)

# database/ lives at the project root (one level up from 01_memory_core/).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DB_PATH = _PROJECT_ROOT / "database" / "portfolio.db"


class PortfolioDB:
    """Persistence gateway for PEA account state, positions, and audit logs.

    Attributes:
        db_path: Absolute path to the SQLite database file.
    """

    def __init__(self, db_path: Optional[Path | str] = None) -> None:
        """Initialize the manager and ensure the database directory exists.

        Args:
            db_path: Optional custom path to the SQLite file. Defaults to
                ``<project_root>/database/portfolio.db``.
        """
        self.db_path: Path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        logger.debug("PortfolioDB using database at %s", self.db_path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a SQLite connection, committing on success and always closing.

        Yields:
            sqlite3.Connection: A connection with ``Row`` factory and foreign
            keys enabled.

        Raises:
            sqlite3.Error: Propagated after a rollback if any DB error occurs.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            logger.exception("SQLite operation failed; rolled back.")
            raise
        finally:
            conn.close()

    def init_db(self) -> None:
        """Create the ``account_state``, ``positions`` and ``audit_logs`` tables.

        The operation is idempotent (``IF NOT EXISTS``).
        """
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS account_state (
                        id              INTEGER PRIMARY KEY CHECK (id = 1),
                        cash_available  REAL    NOT NULL,
                        total_equity    REAL    NOT NULL,
                        last_updated    TEXT    NOT NULL
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS positions (
                        ticker           TEXT PRIMARY KEY,
                        qty_shares       INTEGER NOT NULL,
                        avg_entry_price  REAL    NOT NULL,
                        current_price    REAL    NOT NULL,
                        sector           TEXT    NOT NULL,
                        last_updated     TEXT    NOT NULL
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS audit_logs (
                        id           TEXT PRIMARY KEY,
                        ticker       TEXT NOT NULL,
                        signal_type  TEXT NOT NULL,
                        status       TEXT NOT NULL,
                        score        REAL NOT NULL,
                        reason       TEXT,
                        created_at   TEXT NOT NULL,
                        quantity     INTEGER DEFAULT 0,
                        price        REAL DEFAULT 0.0,
                        lineage_json TEXT
                    );
                    """
                )
                # Automatic column migration for older schemas
                cols = [r["name"] for r in conn.execute("PRAGMA table_info(audit_logs);").fetchall()]
                if "quantity" not in cols:
                    conn.execute("ALTER TABLE audit_logs ADD COLUMN quantity INTEGER DEFAULT 0;")
                if "price" not in cols:
                    conn.execute("ALTER TABLE audit_logs ADD COLUMN price REAL DEFAULT 0.0;")
                if "lineage_json" not in cols:
                    conn.execute("ALTER TABLE audit_logs ADD COLUMN lineage_json TEXT;")

                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS portfolio_history (
                        date    TEXT PRIMARY KEY,
                        equity  REAL NOT NULL,
                        cash    REAL NOT NULL
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS news_master (
                        id              TEXT PRIMARY KEY,
                        ticker          TEXT,
                        title           TEXT NOT NULL,
                        source          TEXT,
                        url             TEXT,
                        published_at    TEXT,
                        sentiment_score REAL,
                        sentiment_label TEXT,
                        created_at      TEXT NOT NULL
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS news_sentiment_history (
                        id          TEXT PRIMARY KEY,
                        ticker      TEXT,
                        date_scored TEXT,
                        score       REAL,
                        source      TEXT,
                        headline    TEXT
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS universe_snapshots (
                        date    TEXT NOT NULL,
                        ticker  TEXT NOT NULL,
                        sector  TEXT,
                        PRIMARY KEY (date, ticker)
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS model_training_runs (
                        id                      TEXT PRIMARY KEY,
                        trained_at              TEXT NOT NULL,
                        model_type              TEXT NOT NULL,
                        accuracy                REAL,
                        brier_score             REAL,
                        feature_importance_json TEXT
                    );
                    """
                )
            logger.info("SQLite schema initialized at %s", self.db_path)
        except sqlite3.Error:
            logger.exception("Failed to initialize SQLite schema.")
            raise

    def get_portfolio_state(self) -> PortfolioState:
        """Read the account state and open positions into a Pydantic model.

        Returns:
            PortfolioState: The current portfolio. If no account row exists yet,
            an empty portfolio (zero cash/equity, no positions) is returned.
        """
        try:
            with self._connect() as conn:
                account = conn.execute(
                    "SELECT cash_available, total_equity, last_updated "
                    "FROM account_state WHERE id = 1;"
                ).fetchone()

                rows = conn.execute(
                    "SELECT ticker, qty_shares, avg_entry_price, current_price, "
                    "sector FROM positions ORDER BY ticker;"
                ).fetchall()

            positions = [
                Position(
                    ticker=row["ticker"],
                    qty_shares=row["qty_shares"],
                    avg_entry_price=row["avg_entry_price"],
                    current_price=row["current_price"],
                    sector=row["sector"],
                )
                for row in rows
            ]

            if account is None:
                logger.warning("No account_state row found; returning empty state.")
                return PortfolioState(
                    cash_available=0.0, total_equity=0.0, positions=positions
                )

            return PortfolioState(
                cash_available=account["cash_available"],
                total_equity=account["total_equity"],
                positions=positions,
                last_updated=datetime.fromisoformat(account["last_updated"]),
            )
        except sqlite3.Error:
            logger.exception("Failed to read portfolio state.")
            raise

    def update_portfolio(self, state: PortfolioState) -> None:
        """Persist a full portfolio snapshot.

        Upserts the single ``account_state`` row (id=1) and fully refreshes the
        ``positions`` table to match ``state.positions``.

        Args:
            state: The portfolio snapshot to persist.
        """
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO account_state
                        (id, cash_available, total_equity, last_updated)
                    VALUES (1, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        cash_available = excluded.cash_available,
                        total_equity   = excluded.total_equity,
                        last_updated   = excluded.last_updated;
                    """,
                    (
                        state.cash_available,
                        state.total_equity,
                        state.last_updated.isoformat(),
                    ),
                )

                conn.execute("DELETE FROM positions;")
                now = datetime.now(timezone.utc).isoformat()
                conn.executemany(
                    """
                    INSERT INTO positions
                        (ticker, qty_shares, avg_entry_price, current_price,
                         sector, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    [
                        (
                            p.ticker,
                            p.qty_shares,
                            p.avg_entry_price,
                            p.current_price,
                            p.sector,
                            now,
                        )
                        for p in state.positions
                    ],
                )

                # Daily equity curve snapshot (one row per calendar day).
                day_key = (
                    state.last_updated.date().isoformat()
                    if hasattr(state.last_updated, "date")
                    else str(state.last_updated)[:10]
                )
                conn.execute(
                    """
                    INSERT INTO portfolio_history (date, equity, cash)
                    VALUES (?, ?, ?)
                    ON CONFLICT(date) DO UPDATE SET
                        equity = excluded.equity,
                        cash   = excluded.cash;
                    """,
                    (day_key, float(state.total_equity), float(state.cash_available)),
                )
            logger.info(
                "Portfolio updated: equity=%.2f cash=%.2f positions=%d",
                state.total_equity,
                state.cash_available,
                len(state.positions),
            )
        except sqlite3.Error:
            logger.exception("Failed to update portfolio.")
            raise

    def get_equity_curve(self) -> pd.DataFrame:
        """Return the daily equity curve sorted by date ascending.

        Returns:
            pd.DataFrame: Columns ``date``, ``equity``, ``cash``. Empty if none.
        """
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT date, equity, cash FROM portfolio_history "
                    "ORDER BY date ASC;"
                ).fetchall()
            if not rows:
                return pd.DataFrame(columns=["date", "equity", "cash"])
            return pd.DataFrame(
                [{"date": r["date"], "equity": r["equity"], "cash": r["cash"]}
                 for r in rows]
            )
        except sqlite3.Error:
            logger.exception("Failed to read portfolio_history.")
            return pd.DataFrame(columns=["date", "equity", "cash"])

    def log_signal(self, signal: Signal, price: float = 0.0) -> None:
        """Insert a signal or update its lifecycle state in ``audit_logs``.

        Args:
            signal: The signal to record. Upsert key is ``signal.id``.
            price: Optional execution or trigger price.
        """
        import json
        try:
            qty = signal.target_qty if signal.target_qty is not None else 0
            lineage_str = json.dumps(signal.lineage or {}) if hasattr(signal, "lineage") else "{}"
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO audit_logs
                        (id, ticker, signal_type, status, score, reason,
                         created_at, quantity, price, lineage_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        status       = excluded.status,
                        score        = excluded.score,
                        reason       = excluded.reason,
                        quantity     = excluded.quantity,
                        price        = excluded.price,
                        lineage_json = excluded.lineage_json;
                    """,
                    (
                        signal.id,
                        signal.ticker,
                        signal.signal_type.value,
                        signal.status.value,
                        signal.score,
                        signal.reason,
                        signal.created_at.isoformat(),
                        qty,
                        price,
                        lineage_str,
                    ),
                )
            logger.info(
                "Signal logged: %s %s %s status=%s qty=%s",
                signal.id[:8],
                signal.ticker,
                signal.signal_type.value,
                signal.status.value,
                qty,
            )
        except sqlite3.Error:
            logger.exception("Failed to log signal %s.", signal.id)
            raise

    def save_news_item(self, item: dict) -> None:
        """Insert a single news article into ``news_master`` (idempotent)."""
        self.save_news_items([item])

    def save_news_items(self, items: list[dict]) -> int:
        """Insert a batch of news articles into ``news_master`` (idempotent).

        Args:
            items: List of dicts with keys ``id, ticker, title, source, url, published_at, sentiment_score``.

        Returns:
            int: Number of items processed.
        """
        if not items:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as conn:
                conn.executemany(
                    """
                    INSERT INTO news_master
                        (id, ticker, title, source, url, published_at, sentiment_score, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        ticker          = COALESCE(excluded.ticker, news_master.ticker),
                        sentiment_score = COALESCE(excluded.sentiment_score, news_master.sentiment_score);
                    """,
                    [
                        (
                            str(it["id"]),
                            it.get("ticker"),
                            str(it["title"]),
                            it.get("source", "Unknown"),
                            it.get("url"),
                            it.get("published_at", now),
                            it.get("sentiment_score"),
                            now,
                        )
                        for it in items
                        if it.get("id") and it.get("title")
                    ],
                )
            logger.info("Saved %d news items to news_master.", len(items))
            return len(items)
        except sqlite3.Error:
            logger.exception("Failed to save news items.")
            return 0

    def fetch_news_master(self, ticker: str | None = None, limit: int = 50) -> list[dict]:
        """Fetch latest news articles from ``news_master``."""
        try:
            with self._connect() as conn:
                if ticker:
                    rows = conn.execute(
                        """
                        SELECT id, ticker, title, source, url, published_at, sentiment_score, created_at
                        FROM news_master
                        WHERE ticker = ? OR ticker IS NULL
                        ORDER BY published_at DESC, created_at DESC
                        LIMIT ?;
                        """,
                        (ticker, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT id, ticker, title, source, url, published_at, sentiment_score, created_at
                        FROM news_master
                        ORDER BY published_at DESC, created_at DESC
                        LIMIT ?;
                        """,
                        (limit,),
                    ).fetchall()
                return [dict(r) for r in rows]
        except sqlite3.Error:
            logger.exception("Failed to fetch news from news_master.")
            return []

    def get_unprocessed_news(self, limit: int = 100) -> list[dict]:
        """Fetch news articles that do not have a sentiment label yet."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT id, ticker, title, source, url, published_at, sentiment_score, sentiment_label
                    FROM news_master
                    WHERE sentiment_label IS NULL OR sentiment_label = ''
                    ORDER BY published_at DESC
                    LIMIT ?;
                    """,
                    (limit,),
                ).fetchall()
                return [dict(r) for r in rows]
        except sqlite3.Error:
            logger.exception("Failed to fetch unprocessed news.")
            return []

    def update_news_sentiment(self, updates: list[dict]) -> int:
        """Batch update sentiment scores and labels on news_master."""
        if not updates:
            return 0
        try:
            with self._connect() as conn:
                conn.executemany(
                    """
                    UPDATE news_master
                    SET sentiment_score = ?,
                        sentiment_label = ?
                    WHERE id = ?;
                    """,
                    [
                        (
                            float(u.get("sentiment_score", 0.0)),
                            str(u.get("sentiment_label", "Neutral")),
                            str(u["id"]),
                        )
                        for u in updates
                        if u.get("id")
                    ],
                )
            logger.info("Updated sentiment for %d news items.", len(updates))
            return len(updates)
        except sqlite3.Error:
            logger.exception("Failed to update news sentiment in batch.")
            return 0

    def insert_raw_news(self, items: list[dict]) -> int:
        """Alias for save_news_items to insert batch news."""
        return self.save_news_items(items)

    def fetch_recent_post_mortems(self, limit: int = 50) -> list[dict]:
        """Fetch historical post-mortems from trade_post_mortems table."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT id, ticker, entry_date, exit_date, holding_days,
                           entry_price, exit_price, pnl_eur, pnl_pct, exit_reason,
                           entry_score, mae_pct, mfe_pct, lessons_learned, created_at
                    FROM trade_post_mortems
                    ORDER BY exit_date DESC, created_at DESC
                    LIMIT ?;
                    """,
                    (limit,),
                ).fetchall()
                return [dict(r) for r in rows]
        except sqlite3.Error:
            logger.debug("Failed to fetch trade_post_mortems from SQLite.")
            return []

    def fetch_closed_signals(self, limit: int = 50) -> list[dict]:
        """Query closed/executed audit log entries for the Portfolio Ledger."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT id, ticker, signal_type, quantity, price, score, reason, created_at
                    FROM audit_logs
                    WHERE status IN ('CLOSED', 'EXECUTED')
                    ORDER BY created_at DESC
                    LIMIT ?;
                    """,
                    (limit,),
                ).fetchall()
                return [dict(r) for r in rows]
        except sqlite3.Error:
            logger.exception("Failed to fetch closed signals.")
            return []

    def fetch_signals_by_status(
        self, statuses: list[str], limit: int | None = None
    ) -> list[dict]:
        """Read audit-log rows matching one or more statuses (read-only).

        Args:
            statuses: Status values to include (e.g. ``["PENDING"]`` or
                ``["EXECUTED", "REVOKED"]``).
            limit: Optional maximum number of rows (most recent first).

        Returns:
            list[dict]: Rows with keys ``id, ticker, signal_type, status,
            score, reason, created_at``, ordered by ``created_at`` descending.
        """
        if not statuses:
            return []

        placeholders = ",".join("?" for _ in statuses)
        query = (
            "SELECT id, ticker, signal_type, status, score, reason, created_at, quantity, price, lineage_json "
            "FROM audit_logs "
            f"WHERE status IN ({placeholders}) "
            "ORDER BY created_at DESC"
        )
        params: list = list(statuses)
        if limit is not None:
            query += " LIMIT ?"
            params.append(int(limit))

        try:
            with self._connect() as conn:
                rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error:
            logger.exception("Failed to fetch signals by status %s.", statuses)
            raise

    def fetch_signals_since(self, since_iso: str) -> list[dict]:
        """Read audit-log rows created at or after an ISO timestamp (read-only).

        Args:
            since_iso: Lower bound as an ISO-8601 string (e.g.
                ``"2026-07-08T00:00:00+00:00"``). Comparison is lexical, which
                is correct for zero-padded ISO timestamps.

        Returns:
            list[dict]: Rows with keys ``id, ticker, signal_type, status,
            score, reason, created_at``, ordered by ``created_at`` descending.
        """
        query = (
            "SELECT id, ticker, signal_type, status, score, reason, created_at "
            "FROM audit_logs "
            "WHERE created_at >= ? "
            "ORDER BY created_at DESC"
        )
        try:
            with self._connect() as conn:
                rows = conn.execute(query, (since_iso,)).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error:
            logger.exception("Failed to fetch signals since %s.", since_iso)
            raise

    def upsert_sentiment_history(
        self, ticker: str, score: float, source: str, headline: str
    ) -> None:
        """Save every scored news item with a timestamp for time-series analysis."""
        import uuid
        now = datetime.now(timezone.utc).isoformat()
        item_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{ticker}_{headline[:60]}_{source}_{now[:13]}"))
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO news_sentiment_history (id, ticker, date_scored, score, source, headline)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        score = excluded.score,
                        date_scored = excluded.date_scored;
                    """,
                    (item_id, ticker, float(score), source, headline, now),
                )
            logger.debug("Sentiment history recorded for %s: %+.1f (%s)", ticker, score, source)
        except sqlite3.Error:
            logger.exception("Failed to upsert sentiment history for %s", ticker)

    def get_sentiment_history(self, ticker: str, days: int = 30) -> list[dict]:
        """Return a time-series of sentiment scores for the UI and API."""
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT id, ticker, date_scored, score, source, headline
                    FROM news_sentiment_history
                    WHERE ticker = ? AND date_scored >= ?
                    ORDER BY date_scored ASC;
                    """,
                    (ticker, cutoff),
                ).fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error:
            logger.exception("Failed to fetch sentiment history for %s", ticker)
            return []

    def snapshot_universe(
        self,
        universe_yaml_path: Optional[str | Path] = None,
        date_str: Optional[str] = None,
    ) -> int:
        """Snapshot current universe definition to prevent survivorship bias in historical replays.

        Args:
            universe_yaml_path: Optional path to pea_universe.yaml.
            date_str: Date string YYYY-MM-DD (defaults to UTC today).

        Returns:
            int: Number of ticker rows snapshotted.
        """
        import yaml
        target_path = Path(universe_yaml_path) if universe_yaml_path else (_PROJECT_ROOT / "config" / "pea_universe.yaml")
        if not target_path.exists():
            logger.warning("Universe file not found at %s; skipping snapshot.", target_path)
            return 0

        today = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            with open(target_path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            tickers = data.get("tickers", [])
            if not tickers:
                return 0

            records = []
            for t in tickers:
                if isinstance(t, dict):
                    records.append((today, str(t.get("ticker", "")), str(t.get("sector", "Unknown"))))
                elif isinstance(t, str):
                    records.append((today, t, "Unknown"))

            with self._connect() as conn:
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO universe_snapshots (date, ticker, sector)
                    VALUES (?, ?, ?);
                    """,
                    records,
                )
            logger.info("Snapshotted %d universe tickers for %s.", len(records), today)
            return len(records)
        except Exception as exc:
            logger.exception("Failed to snapshot universe for %s: %s", today, exc)
            return 0

    def log_model_training_run(
        self,
        model_type: str,
        accuracy: float,
        brier_score: float,
        feature_importance: dict,
    ) -> str:
        """Log ML training metrics and feature importances for audit and provenance."""
        import json
        import uuid
        run_id = f"RUN_{uuid.uuid4().hex[:10]}"
        now = datetime.now(timezone.utc).isoformat()
        feat_json = json.dumps(feature_importance or {})

        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO model_training_runs
                    (id, trained_at, model_type, accuracy, brier_score, feature_importance_json)
                    VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    (run_id, now, model_type, float(accuracy), float(brier_score), feat_json),
                )
            logger.info("Logged ML training run %s (%s, acc=%.3f, brier=%.4f).", run_id, model_type, accuracy, brier_score)
            return run_id
        except sqlite3.Error:
            logger.exception("Failed to log model training run.")
            return ""


# Backward-compatible alias
SQLitePortfolioDB = PortfolioDB
```
