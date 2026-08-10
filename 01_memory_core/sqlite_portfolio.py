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
