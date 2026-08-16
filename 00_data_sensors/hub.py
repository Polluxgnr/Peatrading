"""Central Data Ingestion Hub for Layer 1.

Orchestrates all asynchronous polling and streaming data adapters, aggregates
strongly-typed AlternativeSignals concurrently, and persists them into SQLite.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any, List, Optional

_ROOT = Path(__file__).resolve().parent.parent
for sub in ("00_data_sensors", "00_data_sensors/adapters", "01_memory_core"):
    sys.path.insert(0, str(_ROOT / sub))

from adapters.amf_adapter import AmfShortAdapter
from adapters.base_adapters import AbstractPollAdapter
from adapters.macro_adapter import MacroAlphaAdapter
from data_contracts import AlternativeSignal

logger = logging.getLogger("data_hub")


class DataIngestionHub:
    """Central orchestrator for all Layer 1 ingestion adapters."""

    def __init__(self, adapters: Optional[List[AbstractPollAdapter]] = None) -> None:
        self.adapters: List[AbstractPollAdapter] = adapters or []

    def register_adapter(self, adapter: AbstractPollAdapter) -> None:
        """Register a new poll adapter into the hub."""
        if adapter not in self.adapters:
            self.adapters.append(adapter)
            logger.info("Registered adapter: %s (interval=%ds)", type(adapter).__name__, adapter.interval_seconds)

    def register_default_adapters(self) -> None:
        """Register the standard production adapters (AMF Short Interest, Macro VIX/Spread)."""
        self.register_adapter(AmfShortAdapter())
        self.register_adapter(MacroAlphaAdapter())

    async def fetch_all_alternative_signals(self) -> List[AlternativeSignal]:
        """Fetch all alternative signals concurrently across registered adapters."""
        if not self.adapters:
            logger.warning("No adapters registered in DataIngestionHub.")
            return []

        tasks = [adapter.fetch() for adapter in self.adapters]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_signals: List[AlternativeSignal] = []
        for i, res in enumerate(results):
            adapter_name = type(self.adapters[i]).__name__
            if isinstance(res, Exception):
                logger.error("Adapter %s raised an unhandled exception: %s", adapter_name, res, exc_info=True)
            elif isinstance(res, list):
                all_signals.extend(res)
                logger.debug("Adapter %s returned %d signal(s).", adapter_name, len(res))

        logger.info("DataIngestionHub aggregated a total of %d AlternativeSignal(s).", len(all_signals))
        return all_signals

    def save_signals_to_sqlite(self, signals: List[AlternativeSignal], portfolio_db: Any) -> int:
        """Persist or upsert AlternativeSignal records into SQLite alternative_signals table."""
        if not signals:
            return 0

        # Resolve SQLite connection
        conn = None
        should_close = False
        if hasattr(portfolio_db, "_connect"):
            conn = portfolio_db._connect()
        elif hasattr(portfolio_db, "db_path"):
            conn = sqlite3.connect(str(portfolio_db.db_path))
            should_close = True
        elif isinstance(portfolio_db, (str, Path)):
            conn = sqlite3.connect(str(portfolio_db))
            should_close = True
        elif isinstance(portfolio_db, sqlite3.Connection):
            conn = portfolio_db

        if conn is None:
            raise ValueError("Unable to obtain SQLite connection from provided portfolio_db parameter.")

        try:
            with conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS alternative_signals (
                        id TEXT PRIMARY KEY,
                        ticker TEXT,
                        ts TEXT NOT NULL,
                        signal_type TEXT NOT NULL,
                        value REAL NOT NULL,
                        confidence REAL NOT NULL,
                        source TEXT NOT NULL,
                        metadata_json TEXT
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_alt_sig_ticker ON alternative_signals(ticker)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_alt_sig_type ON alternative_signals(signal_type)")

                saved_count = 0
                for s in signals:
                    ts_str = s.ts.isoformat()
                    tick_str = s.ticker or "ALL"
                    # Deterministic ID hash
                    sig_id = hashlib.sha256(f"{tick_str}_{ts_str[:13]}_{s.signal_type}_{s.source}".encode()).hexdigest()[:24]
                    meta_str = json.dumps(s.metadata)

                    conn.execute(
                        """
                        INSERT INTO alternative_signals (id, ticker, ts, signal_type, value, confidence, source, metadata_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            value=excluded.value,
                            confidence=excluded.confidence,
                            metadata_json=excluded.metadata_json
                        """,
                        (sig_id, s.ticker, ts_str, s.signal_type, float(s.value), float(s.confidence), s.source, meta_str),
                    )
                    saved_count += 1

            logger.info("Saved %d AlternativeSignal(s) to SQLite alternative_signals table.", saved_count)
            return saved_count
        finally:
            if should_close and conn is not None:
                conn.close()
