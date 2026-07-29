"""Export key SQLite tables to Parquet for backup and portability.

Usage:
    python tools/backup_databases.py
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = _ROOT / "database" / "portfolio.db"
_BACKUP_DIR = _ROOT / "database" / "backups"

TABLES_TO_EXPORT = ["portfolio_history", "audit_log", "news_history"]


def main() -> None:
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if not _DB_PATH.exists():
        print(f"Database not found: {_DB_PATH}")
        return

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    conn = sqlite3.connect(str(_DB_PATH))

    existing = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    for table in TABLES_TO_EXPORT:
        if table not in existing:
            print(f"  [skip] {table} (not found)")
            continue
        df = pd.read_sql_query(f"SELECT * FROM {table}", conn)  # noqa: S608
        out_path = _BACKUP_DIR / f"{table}_{stamp}.parquet"
        df.to_parquet(out_path, index=False)
        print(f"  [ok] {table} -> {out_path.name} ({len(df)} rows)")

    conn.close()
    print("Backup complete.")


if __name__ == "__main__":
    main()
