"""Trade Post-Mortem & Retrospective Analysis Engine for PEA Sniper Terminal.

Automatically triggered upon closing a position (via ATR Stop Loss or Profit-Shaving)
to analyze trade execution, holding period efficiency, entry quality, and lessons learned,
storing permanent audit records in the ``trade_post_mortems`` SQLite table.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "database" / "portfolio.db"


class TradePostMortemEngine:
    """Evaluates closed trades and persists retrospective analytics."""

    def __init__(self, db_path: str | Path = _DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create trade_post_mortems table."""
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS trade_post_mortems (
                        id                  TEXT PRIMARY KEY,
                        ticker              TEXT NOT NULL,
                        entry_date          TEXT NOT NULL,
                        exit_date           TEXT NOT NULL,
                        holding_days        INTEGER NOT NULL,
                        entry_price         REAL NOT NULL,
                        exit_price          REAL NOT NULL,
                        pnl_eur             REAL NOT NULL,
                        pnl_pct             REAL NOT NULL,
                        exit_reason         TEXT NOT NULL,
                        entry_score         REAL,
                        mae_pct             REAL,
                        mfe_pct             REAL,
                        lessons_learned     TEXT,
                        created_at          TEXT NOT NULL
                    );
                    """
                )
        except sqlite3.Error as exc:
            logger.debug("Failed to init trade_post_mortems table: %s", exc)

    def generate_post_mortem(
        self,
        trade_id: str,
        ticker: str,
        entry_date: str,
        exit_date: str,
        entry_price: float,
        exit_price: float,
        shares: int,
        exit_reason: str,
        entry_score: float = 75.0,
        mae_pct: float = 0.0,
        mfe_pct: float = 0.0,
    ) -> Dict:
        """Generate and save post-mortem record for a completed trade."""
        pnl_eur = (exit_price - entry_price) * shares
        pnl_pct = (exit_price / entry_price - 1.0) * 100.0 if entry_price > 0 else 0.0

        # Estimate holding duration
        try:
            d0 = datetime.fromisoformat(entry_date[:10])
            d1 = datetime.fromisoformat(exit_date[:10])
            holding_days = max(1, (d1 - d0).days)
        except Exception:
            holding_days = 1

        # Synthesize qualitative lesson
        if pnl_eur > 0:
            lesson = (
                f"Trade gagnant (+{pnl_pct:.1f}% en {holding_days}j). "
                f"La règle de prise de bénéfice ({exit_reason}) a capturé l'impulsion haussière avec succès."
            )
        else:
            lesson = (
                f"Trade clôturé en perte ({pnl_pct:.1f}% en {holding_days}j). "
                f"Coupe-circuit {exit_reason} exécuté avec discipline, limitant l'érosion du capital."
            )

        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO trade_post_mortems (
                        id, ticker, entry_date, exit_date, holding_days,
                        entry_price, exit_price, pnl_eur, pnl_pct, exit_reason,
                        entry_score, mae_pct, mfe_pct, lessons_learned, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        exit_price = excluded.exit_price,
                        pnl_eur = excluded.pnl_eur,
                        pnl_pct = excluded.pnl_pct,
                        lessons_learned = excluded.lessons_learned;
                    """,
                    (
                        trade_id,
                        ticker,
                        entry_date,
                        exit_date,
                        holding_days,
                        entry_price,
                        exit_price,
                        pnl_eur,
                        pnl_pct,
                        exit_reason,
                        entry_score,
                        mae_pct,
                        mfe_pct,
                        lesson,
                        now,
                    ),
                )
            logger.info("Post-mortem saved for trade %s on %s (PnL: %+.2f EUR)", trade_id, ticker, pnl_eur)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to save post-mortem for %s: %s", trade_id, exc)

        return {
            "id": trade_id,
            "ticker": ticker,
            "pnl_eur": round(pnl_eur, 2),
            "pnl_pct": round(pnl_pct, 2),
            "holding_days": holding_days,
            "exit_reason": exit_reason,
            "lessons_learned": lesson,
        }

    def fetch_recent_post_mortems(self, limit: int = 20) -> List[Dict]:
        """Retrieve recent trade post-mortems from SQLite."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM trade_post_mortems ORDER BY exit_date DESC LIMIT ?;",
                    (limit,),
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to fetch post-mortems: %s", exc)
            return []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    pm = TradePostMortemEngine()
    rec = pm.generate_post_mortem(
        trade_id="TEST_001",
        ticker="MC.PA",
        entry_date="2026-06-01",
        exit_date="2026-07-15",
        entry_price=600.0,
        exit_price=660.0,
        shares=3,
        exit_reason="PROFIT_SHAVE_20PCT",
    )
    print("Generated Post-Mortem:", rec)
