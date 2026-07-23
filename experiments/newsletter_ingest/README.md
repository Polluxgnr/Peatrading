# Newsletter ingest sandbox (Yahoo Mail IMAP → local JSON)

Isolated from `00_`–`05_` production code. **No SQLite/DuckDB writes.**

## Setup

1. Enable Yahoo 2FA and create an **app password**.
2. `cp .env.example .env` and fill `YAHOO_MAIL_USER` / `YAHOO_MAIL_APP_PASSWORD`.
3. Create a Yahoo Mail folder/label (e.g. `Finance`) and route newsletters into it.
4. Run:

```bash
python experiments/newsletter_ingest/run_ingest.py --limit 20 --folder Finance
python experiments/newsletter_ingest/run_ingest.py --dry-run --limit 5
```

Output JSON lands in `experiments/newsletter_ingest/output/`.

## Acceptance

- IMAP connects and always closes.
- Titles/links extracted from varied HTML digests.
- Deduper collapses obvious same-day reprints.
- Zero mailbox mutations; zero production DB I/O.
