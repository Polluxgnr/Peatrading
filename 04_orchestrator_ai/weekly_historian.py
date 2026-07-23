"""Weekly Historian for PEA Sniper Terminal V-Prime (Phase 12).

Every Friday the system "steps back" and writes a hedge-fund-style weekly digest
for the CIO. It aggregates the last 7 days of audit logs into hard counts
(vetoes, executions, current equity/cash) and asks the LLM to translate those
numbers into a concise, professional risk-and-performance narrative.

The LLM is a *post-hoc analyst only*: it summarizes decisions the deterministic
engine already made. It never generates or approves trades.
"""

import logging
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:  # Load config/api_keys.env if python-dotenv is available.
    from dotenv import load_dotenv

    _ENV_PATH = Path(__file__).resolve().parent.parent / "config" / "api_keys.env"
    load_dotenv(_ENV_PATH)
except Exception:  # noqa: BLE001
    pass

_INTERFACES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "05_interfaces"
)
sys.path.insert(0, _INTERFACES_DIR)

from llm_explainer import openrouter_chat  # noqa: E402

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "mistralai/mistral-7b-instruct"
_LOOKBACK_DAYS = 7
_FALLBACK_PREFIX = "[AI narrative unavailable] "


class WeeklyHistorian:
    """Builds and narrates the weekly risk/performance digest."""

    def __init__(self) -> None:
        """Read the OpenRouter API key and model slug from the environment."""
        self.api_key: str | None = os.getenv("OPENROUTER_API_KEY")
        self.model: str = os.getenv("OPENROUTER_MODEL", _DEFAULT_MODEL)
        if not self.api_key:
            logger.warning(
                "OPENROUTER_API_KEY not set; weekly report will use a data-only "
                "fallback (no AI narrative)."
            )

    @staticmethod
    def _classify(row: dict) -> str:
        """Bucket an audit row into a coarse decision category."""
        status = (row.get("status") or "").upper()
        reason = (row.get("reason") or "").lower()
        if status in ("EXECUTED", "APPROVED"):
            return "executed"
        if status == "REVOKED":
            return "revoked"
        if status == "REJECTED":
            if "vix" in reason or "panic" in reason:
                return "vetoed_vix"
            if "earnings" in reason or "blackout" in reason:
                return "vetoed_earnings"
            if "illiquid" in reason or "adv" in reason:
                return "vetoed_liquidity"
            if "max satellite" in reason or "max positions" in reason:
                return "vetoed_max_positions"
            if "macro" in reason or ("veto" in reason and "earnings" not in reason):
                return "vetoed_macro"
            if "sector" in reason:
                return "vetoed_sector"
            if "correlation" in reason:
                return "vetoed_correlation"
            return "rejected_other"
        return "other"

    def _build_context(self, rows: list[dict], portfolio: Any) -> tuple[str, dict]:
        """Summarize audit rows + portfolio into an LLM context string.

        Returns:
            tuple[str, dict]: The context block and the raw counts dict (so the
            fallback path can render numbers without the LLM).
        """
        buckets = Counter(self._classify(r) for r in rows)
        executed = [r for r in rows if self._classify(r) == "executed"]

        positions_txt = ", ".join(
            f"{p.ticker} {p.qty_shares}@{p.current_price:.2f} "
            f"({p.unrealized_pnl_pct * 100:+.1f}%)"
            for p in portfolio.positions
        ) or "none"

        top_trades = "; ".join(
            f"{r['ticker']} ({r['status']})" for r in executed[:8]
        ) or "none"

        counts = dict(buckets)
        context = (
            f"REPORTING WINDOW: last {_LOOKBACK_DAYS} days.\n"
            f"Total signals evaluated: {len(rows)}.\n"
            f"Executed/Approved: {buckets.get('executed', 0)}.\n"
            f"Revoked (macro window): {buckets.get('revoked', 0)}.\n"
            f"Vetoed by MACRO event: {buckets.get('vetoed_macro', 0)}.\n"
            f"Vetoed by EARNINGS blackout: {buckets.get('vetoed_earnings', 0)}.\n"
            f"Vetoed by VIX panic: {buckets.get('vetoed_vix', 0)}.\n"
            f"Vetoed by LIQUIDITY: {buckets.get('vetoed_liquidity', 0)}.\n"
            f"Vetoed by MAX POSITIONS: {buckets.get('vetoed_max_positions', 0)}.\n"
            f"Vetoed by SECTOR limit: {buckets.get('vetoed_sector', 0)}.\n"
            f"Vetoed by CORRELATION: {buckets.get('vetoed_correlation', 0)}.\n"
            f"Other rejections: {buckets.get('rejected_other', 0)}.\n"
            f"Executed names: {top_trades}.\n"
            f"CURRENT EQUITY: {portfolio.total_equity:,.2f} EUR.\n"
            f"CASH AVAILABLE: {portfolio.cash_available:,.2f} EUR "
            f"({(portfolio.cash_available / portfolio.total_equity * 100) if portfolio.total_equity else 0:.1f}%).\n"
            f"OPEN POSITIONS: {positions_txt}.\n"
        )
        return context, counts

    @staticmethod
    def _fallback_report(context: str) -> str:
        """Return a numbers-only report when the LLM is unavailable."""
        return (
            f"{_FALLBACK_PREFIX}Weekly Risk & Performance Digest\n\n{context}"
        )

    async def generate_weekly_report(
        self, portfolio_db: Any, explainer: Any = None
    ) -> str:
        """Generate the weekly CIO digest.

        Args:
            portfolio_db: A ``PortfolioDB`` exposing ``fetch_signals_since`` and
                ``get_portfolio_state``.
            explainer: Optional ``NarrativeExplainer`` (unused directly; kept for
                interface compatibility — the shared OpenRouter client is used).

        Returns:
            str: The generated report, or a data-only fallback on any failure.
        """
        since = (datetime.now(timezone.utc) - timedelta(days=_LOOKBACK_DAYS)).isoformat()
        try:
            rows = portfolio_db.fetch_signals_since(since)
        except Exception:  # noqa: BLE001
            logger.exception("Could not read audit logs for weekly report.")
            rows = []

        portfolio = portfolio_db.get_portfolio_state()
        context, _counts = self._build_context(rows, portfolio)

        if not self.api_key:
            return self._fallback_report(context)

        system_prompt = (
            "Act as a Hedge Fund Risk Manager. Write a weekly digest for the "
            "CIO. Explain how risk was managed (vetoes), summarize performance, "
            "and give a 2-sentence macro outlook. Tone: professional, empirical, "
            "numbers-driven. Keep it under 220 words. No disclaimers."
        )
        narrative = await openrouter_chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context},
            ],
            api_key=self.api_key,
            model=self.model,
            max_tokens=420,
            temperature=0.5,
        )
        if not narrative:
            return self._fallback_report(context)

        logger.info("Weekly report generated (%d chars).", len(narrative))
        return narrative


if __name__ == "__main__":
    import asyncio
    from datetime import datetime, timezone

    _CORE_DIR = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "01_memory_core"
    )
    sys.path.insert(0, _CORE_DIR)
    from data_models import PortfolioState, Position  # noqa: E402

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    class _MockDB:
        def fetch_signals_since(self, since_iso: str) -> list[dict]:
            now = datetime.now(timezone.utc).isoformat()
            return [
                {"ticker": "MC.PA", "status": "EXECUTED", "reason": "approved", "created_at": now},
                {"ticker": "OR.PA", "status": "REJECTED", "reason": "Macro veto: ECB", "created_at": now},
                {"ticker": "AI.PA", "status": "REJECTED", "reason": "VIX panic", "created_at": now},
                {"ticker": "SU.PA", "status": "REJECTED", "reason": "Sector weight limit", "created_at": now},
            ]

        def get_portfolio_state(self) -> PortfolioState:
            return PortfolioState(
                cash_available=6000.0,
                total_equity=20000.0,
                positions=[
                    Position(ticker="MC.PA", qty_shares=5, avg_entry_price=600.0,
                             current_price=660.0, sector="Luxury"),
                ],
                last_updated=datetime.now(timezone.utc),
            )

    hist = WeeklyHistorian()
    report = asyncio.run(hist.generate_weekly_report(_MockDB()))
    print("\n===== WEEKLY REPORT =====\n")
    print(report)
