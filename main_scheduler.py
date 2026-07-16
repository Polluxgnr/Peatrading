"""Root daemon scheduler for PEA Sniper Terminal V-Prime.

Ties the whole pipeline together and runs it on the multi-pass European market
schedule (09:00, 13:30, 17:10 Paris time, weekdays only):

    fetch (yfinance -> DuckDB) -> quant signals -> orchestrator (macro veto,
    VIX, correlation, sizing) -> revoke/expire PENDING -> Discord alerts.

Design rules honoured here:
  * Async/sync bridge: the synchronous ``schedule`` job runs the async pipeline
    via ``asyncio.run``.
  * Zero crash tolerance: every pass is wrapped so a data outage or locked DB
    logs CRITICAL and the daemon keeps running for the next pass.
  * Timezone awareness: schedule times are pinned to Europe/Paris; weekends are
    skipped.

This module only stitches existing phases together; it does not modify them.
"""

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

# --- Wire up the digit-prefixed package directories --------------------------
_ROOT = Path(__file__).resolve().parent
for _sub in (
    "00_data_sensors",
    "01_memory_core",
    "02_quant_engine",
    "03_risk_portfolio",
    "04_orchestrator_ai",
    "05_interfaces",
):
    sys.path.insert(0, str(_ROOT / _sub))

import aiohttp  # noqa: E402
import schedule  # noqa: E402

from data_models import Position, PortfolioState, Signal, SignalStatus, SignalType  # noqa: E402
from duckdb_manager import TimeSeriesDB  # noqa: E402
from sqlite_portfolio import PortfolioDB  # noqa: E402
from market_prices_api import MarketDataFetcher  # noqa: E402
from macro_alpha_api import MacroAlphaSensor  # noqa: E402
from technical_scorer import SignalGenerator  # noqa: E402
from smart_dca_engine import SmartDcaCore  # noqa: E402
from monthly_rebalancer import PortfolioRebalancer  # noqa: E402
from signal_priority_cascade import SignalOrchestrator  # noqa: E402
from revocation_engine import RevocationEngine  # noqa: E402
from llm_explainer import NarrativeExplainer  # noqa: E402
from weekly_historian import WeeklyHistorian  # noqa: E402
from discord_copilot import DiscordCopilot  # noqa: E402

logger = logging.getLogger("main_scheduler")

_CONFIG_DIR = _ROOT / "config"
_UNIVERSE_PATH = _CONFIG_DIR / "pea_universe.yaml"
_RISK_PATH = _CONFIG_DIR / "risk_params.yaml"
_TIMEZONE = "Europe/Paris"
_PASS_TIMES = ("09:00", "13:30", "17:10")
_WEEKLY_REPORT_TIME = "18:00"     # Friday CIO digest.
_MONTHLY_CHECK_TIME = "08:30"     # Daily probe; acts only on the 1st.
_LOOKBACK_DAYS = 400  # ~270 trading days -> enough for SMA-200.


def _core_ticker() -> str:
    """Read the Core ETF ticker from ``risk_params.yaml`` (default CW8.PA)."""
    try:
        with open(_RISK_PATH, "r", encoding="utf-8") as fh:
            risk = yaml.safe_load(fh) or {}
        return str(risk.get("CORE_TICKER", "CW8.PA"))
    except Exception:  # noqa: BLE001
        return "CW8.PA"


async def _post_webhook(content: str) -> bool:
    """Post a plain-text message to the Discord webhook, chunked to 2000 chars.

    Args:
        content: The message body.

    Returns:
        bool: ``True`` if every chunk posted with a 2xx status.
    """
    url = os.getenv("DISCORD_WEBHOOK_URL")
    if not url:
        logger.warning("DISCORD_WEBHOOK_URL not set; message not sent.")
        return False

    chunks = [content[i : i + 1900] for i in range(0, len(content), 1900)] or [""]
    ok = True
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for chunk in chunks:
                async with session.post(url, json={"content": chunk}) as resp:
                    if resp.status not in (200, 204):
                        body = await resp.text()
                        logger.error("Webhook HTTP %s: %s", resp.status, body[:200])
                        ok = False
    except Exception:  # noqa: BLE001 - a failed webhook must not crash the daemon.
        logger.exception("Discord webhook post failed.")
        return False
    return ok


def _load_universe_tickers() -> list[str]:
    """Read the tradable tickers from ``config/pea_universe.yaml``.

    Returns:
        list[str]: All tickers across every sector (empty on failure).
    """
    try:
        with open(_UNIVERSE_PATH, "r", encoding="utf-8") as fh:
            universe = yaml.safe_load(fh) or {}
        return [
            entry["ticker"]
            for members in universe.get("universe", {}).values()
            for entry in members
        ]
    except Exception:  # noqa: BLE001
        logger.exception("Could not read universe file %s", _UNIVERSE_PATH)
        return []


def _refresh_portfolio_prices(
    pdb: PortfolioDB, portfolio: PortfolioState, prices: dict[str, float]
) -> PortfolioState:
    """Mark held positions to market and recompute equity, then persist.

    Keeps the dashboard PnL and the sizer's equity honest between manual
    executions. If nothing changed (no held tickers priced) the input is
    returned unmodified.

    Args:
        pdb: Portfolio database.
        portfolio: Current snapshot.
        prices: ticker -> latest close.

    Returns:
        PortfolioState: The refreshed (and persisted) snapshot.
    """
    if not portfolio.positions:
        return portfolio

    refreshed = []
    for p in portfolio.positions:
        new_price = prices.get(p.ticker, p.current_price)
        refreshed.append(
            Position(
                ticker=p.ticker,
                qty_shares=p.qty_shares,
                avg_entry_price=p.avg_entry_price,
                current_price=new_price if new_price > 0 else p.current_price,
                sector=p.sector,
            )
        )
    positions_value = sum(p.market_value for p in refreshed)
    new_state = PortfolioState(
        cash_available=portfolio.cash_available,
        total_equity=portfolio.cash_available + positions_value,
        positions=refreshed,
        last_updated=datetime.now(timezone.utc),
    )
    try:
        pdb.update_portfolio(new_state)
        logger.info(
            "Portfolio marked to market: equity=%.2f (%d positions).",
            new_state.total_equity,
            len(refreshed),
        )
    except Exception:  # noqa: BLE001 - a failed refresh must not abort the pass.
        logger.exception("Failed to persist marked-to-market portfolio.")
        return portfolio
    return new_state


def _latest_prices(tsdb: TimeSeriesDB, tickers: list[str]) -> dict[str, float]:
    """Fetch the most recent close for each ticker from DuckDB.

    Args:
        tsdb: The time-series database.
        tickers: Tickers to look up.

    Returns:
        dict[str, float]: ticker -> latest close (absent if no data).
    """
    prices: dict[str, float] = {}
    for ticker in tickers:
        try:
            df = tsdb.get_historical_prices(ticker, days=2)
            if df is not None and not df.empty:
                prices[ticker] = float(df["Close"].iloc[-1])
        except Exception:  # noqa: BLE001
            logger.warning("Could not read latest price for %s.", ticker)
    return prices


async def run_pipeline_async() -> None:
    """Execute one full analysis pass end-to-end.

    Raises:
        Exception: Propagated to the sync wrapper, which logs CRITICAL. This
            keeps the daemon alive for the next scheduled pass.
    """
    # --- Init Phase ---
    tsdb = TimeSeriesDB()
    tsdb.init_db()
    pdb = PortfolioDB()
    pdb.init_db()
    fetcher = MarketDataFetcher()
    generator = SignalGenerator()
    orchestrator = SignalOrchestrator(
        config_dir=_CONFIG_DIR, portfolio_db=pdb, timeseries_db=tsdb
    )
    explainer = NarrativeExplainer()
    copilot = DiscordCopilot(portfolio_db=pdb, explainer=explainer)

    core_engine = SmartDcaCore(_CONFIG_DIR)
    macro_alpha = MacroAlphaSensor()
    core_ticker = _core_ticker()

    tickers = _load_universe_tickers()
    if not tickers:
        logger.error("No tickers in universe; aborting pass.")
        return
    # The Core ETF must be fetched too so Smart DCA can read its history.
    fetch_tickers = tickers + ([core_ticker] if core_ticker not in tickers else [])
    logger.info("Universe loaded: %d tickers (+core %s).", len(tickers), core_ticker)

    # --- Data Phase ---
    ok = fetcher.update_database(tsdb, fetch_tickers, lookback_days=_LOOKBACK_DAYS)
    if not ok:
        logger.error("Data ingestion failed; skipping this pass (no stale trades).")
        return

    # --- Macro Phase: European VIX emergency brake ---
    vix_level = macro_alpha.get_european_vix()

    # --- Quant Phase ---
    raw_signals = generator.generate_raw_signals(tsdb, tickers)
    logger.info("Quant engine produced %d raw signal(s).", len(raw_signals))

    # --- Orchestration Phase (satellite) ---
    portfolio: PortfolioState = pdb.get_portfolio_state()
    current_prices = _latest_prices(tsdb, fetch_tickers)
    # Mark held positions to market so PnL/equity are fresh for sizing + UI.
    portfolio = _refresh_portfolio_prices(pdb, portfolio, current_prices)
    processed = orchestrator.process_raw_signals(
        raw_signals, portfolio, current_prices, vix_level=vix_level
    )

    approved = [s for s in processed if s.status == SignalStatus.APPROVED]
    logger.info(
        "Orchestrator finalized %d signal(s): %d APPROVED (VIX=%.1f).",
        len(processed),
        len(approved),
        vix_level,
    )

    # --- Core Phase: Smart DCA on the MSCI World ETF (immune to VIX veto) ---
    core_signal = core_engine.evaluate_cw8(
        tsdb, portfolio.cash_available, portfolio.total_equity
    )
    if core_signal and (core_signal.target_qty or 0) > 0:
        core_signal.status = SignalStatus.APPROVED
        processed.append(core_signal)
        logger.info(
            "Core DCA APPROVED: buy %d %s.", core_signal.target_qty, core_ticker
        )

    # --- Revocation Phase: anti-stale on existing PENDING signals ------------
    revoker = RevocationEngine(_CONFIG_DIR)
    try:
        pending_rows = pdb.fetch_signals_by_status(["PENDING"])
    except Exception:  # noqa: BLE001
        logger.exception("Could not load PENDING signals for revocation.")
        pending_rows = []
    for row in pending_rows:
        try:
            created_raw = row.get("created_at")
            if isinstance(created_raw, str):
                created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            else:
                created_at = datetime.now(timezone.utc)
            sig = Signal(
                id=str(row["id"]),
                ticker=str(row["ticker"]),
                signal_type=SignalType(str(row["signal_type"])),
                status=SignalStatus.PENDING,
                score=float(row.get("score") or 0),
                reason=str(row.get("reason") or ""),
                created_at=created_at,
            )
            cur_px = float(current_prices.get(sig.ticker) or 0.0)
            if cur_px <= 0:
                # Still allow time-expiry with a dummy equal price (no false drift).
                cur_px = 1.0
                orig_px = 1.0
            else:
                # Approximate emission price from DuckDB history near created_at.
                orig_px = cur_px
                try:
                    hist = tsdb.get_historical_prices(sig.ticker, days=30)
                    if hist is not None and not hist.empty and "Close" in hist.columns:
                        # Use oldest close in window as conservative proxy if
                        # we cannot align exact timestamp.
                        series = hist["Close"].dropna()
                        if len(series):
                            orig_px = float(series.iloc[0])
                except Exception:  # noqa: BLE001
                    orig_px = cur_px
            updated = revoker.evaluate_signal(sig, cur_px, orig_px)
            if updated.status in (SignalStatus.REVOKED, SignalStatus.EXPIRED):
                processed.append(updated)
                logger.info(
                    "Pending signal %s -> %s (%s).",
                    updated.id[:8], updated.status.value, updated.ticker,
                )
        except Exception:  # noqa: BLE001
            logger.exception("Revocation failed for row %s.", row.get("id"))

    # Persist every decision to the audit log for the dashboard/ledger.
    for signal in processed:
        try:
            pdb.log_signal(signal)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to audit-log signal %s.", signal.id)

    # --- Alert Phase ---
    alertable = [
        s for s in processed
        if s.status in (SignalStatus.APPROVED, SignalStatus.REVOKED)
    ]
    if not alertable:
        logger.info("No APPROVED/REVOKED signals to push to Discord this pass.")
        return

    if not os.getenv("DISCORD_TOKEN"):
        logger.warning(
            "DISCORD_TOKEN not set; %d alert(s) computed but not sent.",
            len(alertable),
        )
        return

    for signal in alertable:
        try:
            price = current_prices.get(signal.ticker, 0.0)
            await copilot.send_signal_alert(
                signal, portfolio, explainer=explainer, current_price=price
            )
        except Exception:  # noqa: BLE001 - a failed alert must not abort the pass.
            logger.exception("Failed to send Discord alert for %s.", signal.ticker)


def run_analysis_pass() -> None:
    """Synchronous wrapper: skip weekends, run the async pipeline safely."""
    if datetime.today().weekday() >= 5:
        logger.info("Weekend: Market closed, skipping pass.")
        return

    started = time.perf_counter()
    logger.info("=== Analysis pass starting ===")
    try:
        asyncio.run(run_pipeline_async())
        elapsed = time.perf_counter() - started
        logger.info("=== Analysis pass completed in %.1fs ===", elapsed)
    except Exception as exc:  # noqa: BLE001 - daemon must survive any failure.
        elapsed = time.perf_counter() - started
        logger.critical(
            "Analysis pass FAILED after %.1fs: %s", elapsed, exc, exc_info=True
        )


async def run_weekly_report_async() -> None:
    """Generate the weekly CIO digest and push it to the Discord webhook."""
    pdb = PortfolioDB()
    pdb.init_db()
    explainer = NarrativeExplainer()
    historian = WeeklyHistorian()

    report = await historian.generate_weekly_report(pdb, explainer=explainer)
    header = (
        "\U0001F4C8 **PEA Sniper Terminal - Weekly Risk & Performance Digest**\n"
        f"_(generated {datetime.now().strftime('%Y-%m-%d %H:%M')} Paris)_\n\n"
    )
    sent = await _post_webhook(header + report)
    logger.info("Weekly report %s.", "sent" if sent else "computed but NOT sent")


def run_weekly_report() -> None:
    """Sync wrapper for the Friday weekly report job."""
    started = time.perf_counter()
    logger.info("=== Weekly report job starting ===")
    try:
        asyncio.run(run_weekly_report_async())
        logger.info(
            "=== Weekly report done in %.1fs ===", time.perf_counter() - started
        )
    except Exception as exc:  # noqa: BLE001
        logger.critical("Weekly report FAILED: %s", exc, exc_info=True)


async def run_monthly_rebalance_async() -> None:
    """Generate mechanical rebalance SELLs and push them for manual approval."""
    pdb = PortfolioDB()
    pdb.init_db()
    tsdb = TimeSeriesDB()
    tsdb.init_db()
    rebalancer = PortfolioRebalancer(_CONFIG_DIR, timeseries_db=tsdb)

    portfolio = pdb.get_portfolio_state()
    sells = rebalancer.generate_rebalance_signals(portfolio)
    if not sells:
        logger.info("Monthly rebalance: no positions triggered.")
        await _post_webhook(
            "\U0001F501 **Monthly Rebalance** - no profit-taking or stop-loss "
            "triggers this month."
        )
        return

    # Persist to the audit log and surface for manual approval.
    for signal in sells:
        try:
            pdb.log_signal(signal)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to audit-log rebalance signal %s.", signal.id)

    lines = ["\U0001F501 **Monthly Rebalance - SELL signals for approval**\n"]
    for s in sells:
        lines.append(f"- **{s.ticker}** SELL {s.target_qty} - {s.reason}")
    await _post_webhook("\n".join(lines))
    logger.info("Monthly rebalance pushed %d SELL signal(s).", len(sells))


def run_monthly_rebalance() -> None:
    """Sync wrapper: only acts on the 1st calendar day of the month."""
    if datetime.today().day != 1:
        return
    started = time.perf_counter()
    logger.info("=== Monthly rebalance job starting (1st of month) ===")
    try:
        asyncio.run(run_monthly_rebalance_async())
        logger.info(
            "=== Monthly rebalance done in %.1fs ===",
            time.perf_counter() - started,
        )
    except Exception as exc:  # noqa: BLE001
        logger.critical("Monthly rebalance FAILED: %s", exc, exc_info=True)


def _schedule_passes() -> None:
    """Register all periodic jobs in Europe/Paris time."""
    for pass_time in _PASS_TIMES:
        schedule.every().day.at(pass_time, _TIMEZONE).do(run_analysis_pass)
    # Weekly CIO digest: Friday 18:00 Paris.
    schedule.every().friday.at(_WEEKLY_REPORT_TIME, _TIMEZONE).do(run_weekly_report)
    # Monthly rebalance: probe daily, act only on the 1st (guarded inside).
    schedule.every().day.at(_MONTHLY_CHECK_TIME, _TIMEZONE).do(run_monthly_rebalance)
    logger.info(
        "Scheduled: passes at %s; weekly report Fri %s; monthly probe %s (%s).",
        ", ".join(_PASS_TIMES),
        _WEEKLY_REPORT_TIME,
        _MONTHLY_CHECK_TIME,
        _TIMEZONE,
    )


def main() -> None:
    """Entry point: parse CLI args and either run once or loop forever."""
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="PEA Sniper Terminal daemon.")
    parser.add_argument(
        "--now",
        action="store_true",
        help="Run a single analysis pass immediately, then exit.",
    )
    parser.add_argument(
        "--weekly",
        action="store_true",
        help="Generate and send the weekly report now, then exit.",
    )
    parser.add_argument(
        "--rebalance",
        action="store_true",
        help="Run the monthly rebalancer now (ignores the 1st-of-month guard).",
    )
    args = parser.parse_args()

    if args.now:
        logger.info("--now: running a single immediate pass.")
        run_analysis_pass()
        return

    if args.weekly:
        logger.info("--weekly: generating the weekly report now.")
        run_weekly_report()
        return

    if args.rebalance:
        logger.info("--rebalance: running monthly rebalancer now.")
        asyncio.run(run_monthly_rebalance_async())
        return

    _schedule_passes()
    logger.info("\U0001F6E1\uFE0F PEA Sniper Terminal Daemon started. "
                "Waiting for scheduled runs...")
    while True:
        try:
            schedule.run_pending()
            time.sleep(60)
        except KeyboardInterrupt:
            logger.info("Shutdown requested; exiting daemon loop.")
            break
        except Exception:  # noqa: BLE001 - never let the loop die.
            logger.critical("Scheduler loop error; continuing.", exc_info=True)
            time.sleep(60)


if __name__ == "__main__":
    main()
