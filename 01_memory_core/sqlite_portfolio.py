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
                        created_at   TEXT NOT NULL
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS portfolio_history (
                        date    TEXT PRIMARY KEY,
                        equity  REAL NOT NULL,
                        cash    REAL NOT NULL
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

    def log_signal(self, signal: Signal) -> None:
        """Insert a signal or update its lifecycle state in ``audit_logs``.

        Args:
            signal: The signal to record. Upsert key is ``signal.id``.
        """
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO audit_logs
                        (id, ticker, signal_type, status, score, reason,
                         created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        status = excluded.status,
                        score  = excluded.score,
                        reason = excluded.reason;
                    """,
                    (
                        signal.id,
                        signal.ticker,
                        signal.signal_type.value,
                        signal.status.value,
                        signal.score,
                        signal.reason,
                        signal.created_at.isoformat(),
                    ),
                )
            logger.info(
                "Signal logged: %s %s %s status=%s",
                signal.id[:8],
                signal.ticker,
                signal.signal_type.value,
                signal.status.value,
            )
        except sqlite3.Error:
            logger.exception("Failed to log signal %s.", signal.id)
            raise

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
            "SELECT id, ticker, signal_type, status, score, reason, created_at "
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
