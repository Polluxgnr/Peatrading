"""Export key SQLite tables to Parquet and back up databases off-instance to Cloudflare R2 (or AWS S3).

Cloudflare R2 is 100% S3-compatible with zero egress fees.

Usage:
    python tools/backup_databases.py
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

try:
    from dotenv import load_dotenv

    _ENV_PATH = Path(__file__).resolve().parent.parent / "config" / "api_keys.env"
    load_dotenv(_ENV_PATH)
except Exception:  # noqa: BLE001
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
logger = logging.getLogger("backup_databases")

_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = _ROOT / "database" / "portfolio.db"
_BACKUP_DIR = _ROOT / "database" / "backups"

TABLES_TO_EXPORT = [
    "portfolio_history",
    "audit_logs",
    "news_master",
    "positions",
    "account_state",
    "fundamentals_cache",
    "universe_snapshots",
]


def backup_to_r2_or_s3(
    local_files: list[Path],
    stamp: str,
    bucket_name: str,
    endpoint_url: Optional[str] = None,
    access_key_id: Optional[str] = None,
    secret_access_key: Optional[str] = None,
) -> bool:
    """Upload backup artifacts to Cloudflare R2 or Amazon S3 bucket."""
    try:
        import boto3

        client_kwargs = {}
        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url
            client_kwargs["region_name"] = "auto"
        if access_key_id and secret_access_key:
            client_kwargs["aws_access_key_id"] = access_key_id
            client_kwargs["aws_secret_access_key"] = secret_access_key

        s3 = boto3.client("s3", **client_kwargs)
        dest_name = "Cloudflare R2" if endpoint_url else "Amazon S3"
        prefix = f"pea_pollux_backups/{stamp}"
        logger.info("Uploading %d backup files to %s (bucket: %s, prefix: %s) ...", len(local_files), dest_name, bucket_name, prefix)

        for fpath in local_files:
            if not fpath.exists():
                continue
            key = f"{prefix}/{fpath.name}"
            s3.upload_file(str(fpath), bucket_name, key)
            logger.info("  [R2/S3 OK] %s -> s3://%s/%s", fpath.name, bucket_name, key)

        logger.info("%s remote cloud backup completed successfully.", dest_name)
        return True
    except Exception as exc:
        logger.error("Cloud backup upload failed: %s", exc)
        return False


# Alias for backward compatibility
backup_to_s3 = backup_to_r2_or_s3


def main() -> None:
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if not _DB_PATH.exists():
        logger.warning("Database not found: %s", _DB_PATH)
        return

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    conn = sqlite3.connect(str(_DB_PATH))

    existing = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    generated_files: list[Path] = []

    for table in TABLES_TO_EXPORT:
        if table not in existing:
            continue
        try:
            df = pd.read_sql_query(f"SELECT * FROM {table}", conn)  # noqa: S608
            out_path = _BACKUP_DIR / f"{table}_{stamp}.parquet"
            df.to_parquet(out_path, index=False)
            logger.info("  [Parquet OK] %s -> %s (%d rows)", table, out_path.name, len(df))
            generated_files.append(out_path)
        except Exception as exc:
            logger.warning("Failed to export table %s: %s", table, exc)

    conn.close()

    # Also snapshot the raw SQLite database file
    raw_db_snapshot = _BACKUP_DIR / f"portfolio_{stamp}.db"
    try:
        shutil.copy2(_DB_PATH, raw_db_snapshot)
        logger.info("  [Raw DB OK] portfolio.db -> %s", raw_db_snapshot.name)
        generated_files.append(raw_db_snapshot)
    except Exception as exc:
        logger.warning("Failed to copy raw database: %s", exc)

    # Cloudflare R2 / AWS S3 Off-Instance Remote Backup
    r2_endpoint = os.getenv("R2_ENDPOINT_URL")
    r2_access_key = os.getenv("R2_ACCESS_KEY_ID")
    r2_secret_key = os.getenv("R2_SECRET_ACCESS_KEY")
    bucket = os.getenv("R2_BUCKET_NAME") or os.getenv("AWS_S3_BACKUP_BUCKET")

    if bucket and bucket.strip():
        backup_to_r2_or_s3(
            generated_files,
            stamp,
            bucket.strip(),
            endpoint_url=r2_endpoint.strip() if r2_endpoint else None,
            access_key_id=r2_access_key.strip() if r2_access_key else os.getenv("AWS_ACCESS_KEY_ID"),
            secret_access_key=r2_secret_key.strip() if r2_secret_key else os.getenv("AWS_SECRET_ACCESS_KEY"),
        )
    else:
        logger.info("R2_BUCKET_NAME / AWS_S3_BACKUP_BUCKET not set; stored backups locally in database/backups/.")

    logger.info("=== Backup Routine Complete (%d artifacts created) ===", len(generated_files))


if __name__ == "__main__":
    main()
