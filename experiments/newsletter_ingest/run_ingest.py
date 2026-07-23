"""Yahoo Mail newsletter ingest — isolated sandbox (no production DB writes).

Connects read-only via IMAP SSL, parses HTML newsletters, dedupes headlines,
and writes a timestamped JSON under ``output/``.

Secrets live in a local ``.env`` next to this folder (never commit them).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Keep this sandbox hermetic: only local imports under experiments/.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from ingest.env_loader import load_sandbox_env  # noqa: E402
from ingest.imap_client import YahooImapClient  # noqa: E402
from ingest.html_parser import parse_newsletter  # noqa: E402
from ingest.dedupe import dedupe_articles  # noqa: E402
from ingest.writer import write_output  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("newsletter_ingest")


def main() -> int:
    """CLI entry — same spirit as ``seed_account.py``."""
    parser = argparse.ArgumentParser(
        description="Ingest financial newsletters from Yahoo Mail (read-only)."
    )
    parser.add_argument(
        "--limit", type=int, default=20, help="Max emails to fetch (default 20)."
    )
    parser.add_argument(
        "--folder",
        default="Finance",
        help="IMAP folder/label name (default: Finance).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and print summary without writing JSON.",
    )
    parser.add_argument(
        "--env",
        default=str(_HERE / ".env"),
        help="Path to sandbox .env (YAHOO_MAIL_USER / YAHOO_MAIL_APP_PASSWORD).",
    )
    args = parser.parse_args()

    creds = load_sandbox_env(Path(args.env))
    if not creds.get("YAHOO_MAIL_USER") or not creds.get("YAHOO_MAIL_APP_PASSWORD"):
        logger.error(
            "Missing YAHOO_MAIL_USER / YAHOO_MAIL_APP_PASSWORD in %s "
            "(copy .env.example → .env and use a Yahoo *app password*).",
            args.env,
        )
        return 2

    client = YahooImapClient(
        user=creds["YAHOO_MAIL_USER"],
        app_password=creds["YAHOO_MAIL_APP_PASSWORD"],
    )
    articles = []
    try:
        messages = client.fetch_recent(folder=args.folder, limit=args.limit)
        logger.info("Fetched %d raw message(s) from folder '%s'.", len(messages), args.folder)
        for msg in messages:
            try:
                parsed = parse_newsletter(msg)
                articles.extend(parsed.get("articles") or [])
                logger.info(
                    "Parsed '%s' → %d article link(s).",
                    parsed.get("subject", "?")[:80],
                    len(parsed.get("articles") or []),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Parse failed for one message: %s", exc)
    except Exception as exc:  # noqa: BLE001
        logger.error("IMAP ingest failed: %s", exc, exc_info=True)
        return 1
    finally:
        client.close()

    before = len(articles)
    articles = dedupe_articles(articles)
    logger.info("Dedupe: %d → %d article(s).", before, len(articles))

    payload = {
        "folder": args.folder,
        "limit": args.limit,
        "articles_raw": before,
        "articles_deduped": len(articles),
        "articles": articles,
    }
    if args.dry_run:
        logger.info("Dry-run — not writing JSON. Sample titles:")
        for a in articles[:10]:
            logger.info("  • %s", (a.get("title") or "")[:100])
        return 0

    out = write_output(payload, _HERE / "output")
    logger.info("Wrote %s", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
