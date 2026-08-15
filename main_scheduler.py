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
from logging_setup import get_component_logger, setup_app_logging, write_pipeline_status  # noqa: E402

try:
    from earnings_updater import run_earnings_sync  # noqa: E402
except ImportError:
    run_earnings_sync = None

try:
    from news_email_scraper import run_email_scraper  # noqa: E402
except ImportError:
    run_email_scraper = None

try:
    from news_sentiment_llm import score_news_batch  # noqa: E402
except ImportError:
    score_news_batch = None

logger = get_component_logger("scheduler")



_CONFIG_DIR = _ROOT / "config"
_UNIVERSE_PATH = _CONFIG_DIR / "pea_universe.yaml"
_RISK_PATH = _CONFIG_DIR / "risk_params.yaml"
_TIMEZONE = "Europe/Paris"
_PASS_TIMES = ("09:00", "13:30", "17:10")
_WEEKLY_REPORT_TIME = "18:00"     # Friday CIO digest.
_MONTHLY_CHECK_TIME = "08:30"     # Daily probe; profit-shave acts only on the 1st.
_ATR_STOP_CHECK_TIME = "08:35"    # Daily ATR stop evaluation (weekdays via loop).
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


def _load_universe_sector_map() -> dict[str, str]:
    """Read mapping from ticker -> sector from ``config/pea_universe.yaml``."""
    try:
        with open(_UNIVERSE_PATH, "r", encoding="utf-8") as fh:
            universe = yaml.safe_load(fh) or {}
        sector_map: dict[str, str] = {}
        for sector, members in universe.get("universe", {}).items():
            for entry in members:
                if isinstance(entry, dict) and "ticker" in entry:
                    sector_map[str(entry["ticker"])] = str(sector)
        return sector_map
    except Exception:  # noqa: BLE001
        logger.exception("Could not read universe sector map %s", _UNIVERSE_PATH)
        return {}


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
    fetch_tickers = list(set(fetch_tickers + ["^FCHI", "^GSPC", "^IXIC", "EURUSD=X", "OAT.PA", "CW8.PA"]))
    logger.info("Universe loaded: %d tickers (+core %s, +macro indices).", len(tickers), core_ticker)

    # --- Data Phase ---
    ok = fetcher.update_database(tsdb, fetch_tickers, lookback_days=_LOOKBACK_DAYS)
    if not ok:
        logger.error("Data ingestion failed; skipping this pass (no stale trades).")
        return

    # --- News Ingestion Phase ---
    try:
        from news_api_client import run_api_scraper
        from news_email_scraper import run_email_scraper
        from news_rss_scraper import run_rss_scraper

        run_api_scraper(pdb)
        run_email_scraper(pdb)
        run_rss_scraper(pdb)
    except Exception as e:
        logger.warning(f"News scraping failed: {e}")

    # --- Macro Phase: European VIX emergency brake ---
    vix_level = macro_alpha.get_european_vix()

    # --- Quant Phase (Mean-Reversion Exhaustion + StatArb Cointegration) ---
    mre_signals = generator.generate_raw_signals(tsdb, tickers)
    logger.info("Mean-Reversion engine produced %d raw signal(s).", len(mre_signals))

    stat_arb_signals = []
    try:
        from stat_arb_pairs import StatArbEngine
        stat_arb_engine = StatArbEngine()
        sector_map = _load_universe_sector_map()
        
        prices_by_ticker = {}
        for t in tickers:
            df_t = tsdb.get_historical_prices(t, days=500)
            if df_t is not None and not df_t.empty and "Close" in df_t.columns:
                prices_by_ticker[t] = df_t
                
        stat_arb_signals = stat_arb_engine.generate_stat_arb_signals(prices_by_ticker, sector_map)
        logger.info("StatArb Cointegration engine produced %d raw signal(s).", len(stat_arb_signals))
    except Exception as exc:  # noqa: BLE001
        logger.warning("StatArb pass failed: %s", exc)

    raw_signals = mre_signals + stat_arb_signals
    logger.info("Total unified raw candidate signal(s): %d.", len(raw_signals))

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
        write_pipeline_status({
            "job": "analysis",
            "status": "skipped",
            "reason": "weekend",
            "health": "green",
        })
        return

    started = time.perf_counter()
    logger.info("=== Analysis pass starting ===")
    try:
        asyncio.run(run_pipeline_async())
        elapsed = time.perf_counter() - started
        logger.info("=== Analysis pass completed in %.1fs ===", elapsed)
        write_pipeline_status({
            "job": "analysis",
            "status": "ok",
            "health": "green",
            "elapsed_sec": round(elapsed, 2),
            "finished_at_local": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
    except Exception as exc:  # noqa: BLE001 - daemon must survive any failure.
        elapsed = time.perf_counter() - started
        logger.critical(
            "Analysis pass FAILED after %.1fs: %s", elapsed, exc, exc_info=True
        )
        write_pipeline_status({
            "job": "analysis",
            "status": "failed",
            "health": "red",
            "error": str(exc),
            "elapsed_sec": round(elapsed, 2),
            "finished_at_local": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })


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


async def _push_rebalance_sells(
    sells: list, pdb: PortfolioDB, title: str
) -> None:
    """Audit-log and webhook a batch of rebalance SELL signals."""
    if not sells:
        return
    for signal in sells:
        try:
            pdb.log_signal(signal)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to audit-log rebalance signal %s.", signal.id)
    lines = [f"\U0001F501 **{title}**\n"]
    for s in sells:
        lines.append(f"- **{s.ticker}** SELL {s.target_qty} - {s.reason}")
    await _post_webhook("\n".join(lines))
    logger.info("%s pushed %d SELL signal(s).", title, len(sells))


async def run_daily_atr_stops_async() -> None:
    """Evaluate ATR stop-losses every day (independent of profit-shave)."""
    pdb = PortfolioDB()
    pdb.init_db()
    tsdb = TimeSeriesDB()
    tsdb.init_db()
    rebalancer = PortfolioRebalancer(_CONFIG_DIR, timeseries_db=tsdb)
    portfolio = pdb.get_portfolio_state()
    sells = rebalancer.generate_atr_stop_signals(portfolio)
    if not sells:
        logger.info("Daily ATR stops: nothing triggered.")
        return
    await _push_rebalance_sells(sells, pdb, "Daily ATR Stop-Loss — SELLs for approval")


def run_daily_atr_stops() -> None:
    """Sync wrapper for the daily ATR stop job."""
    # Skip weekends (Euronext closed) — same spirit as analysis passes.
    if datetime.today().weekday() >= 5:
        return
    started = time.perf_counter()
    logger.info("=== Daily ATR stop job starting ===")
    try:
        asyncio.run(run_daily_atr_stops_async())
        logger.info(
            "=== Daily ATR stops done in %.1fs ===",
            time.perf_counter() - started,
        )
    except Exception as exc:  # noqa: BLE001
        logger.critical("Daily ATR stops FAILED: %s", exc, exc_info=True)


async def run_monthly_rebalance_async() -> None:
    """Monthly profit-shave SELLs only (ATR stops run daily separately)."""
    pdb = PortfolioDB()
    pdb.init_db()
    tsdb = TimeSeriesDB()
    tsdb.init_db()
    rebalancer = PortfolioRebalancer(_CONFIG_DIR, timeseries_db=tsdb)

    portfolio = pdb.get_portfolio_state()
    sells = rebalancer.generate_profit_shave_signals(portfolio)
    if not sells:
        logger.info("Monthly rebalance: no profit-shave triggers.")
        await _post_webhook(
            "\U0001F501 **Monthly Rebalance** - no profit-shave triggers this month."
        )
        return

    await _push_rebalance_sells(
        sells, pdb, "Monthly Rebalance — profit-shave SELLs for approval"
    )


def run_monthly_rebalance() -> None:
    """Sync wrapper: only acts on the 1st calendar day of the month."""
    if datetime.today().day != 1:
        return
    started = time.perf_counter()
    logger.info("=== Monthly profit-shave job starting (1st of month) ===")
    try:
        asyncio.run(run_monthly_rebalance_async())
        logger.info(
            "=== Monthly profit-shave done in %.1fs ===",
            time.perf_counter() - started,
        )
    except Exception as exc:  # noqa: BLE001
        logger.critical("Monthly rebalance FAILED: %s", exc, exc_info=True)


def run_morning_news_routine() -> None:
    """Pre-market morning routine (08:00 Paris weekdays):
    1. Ingest overnight email newsletters via IMAP into SQLite news_master.
    2. Batch score unprocessed news articles with local FinBERT sentiment.
    """
    if datetime.today().weekday() >= 5:
        logger.info("Morning news routine: skipping weekend day.")
        return

    started = time.perf_counter()
    logger.info("=== Pre-market Morning News & IMAP Routine starting (08:00 Paris) ===")
    try:
        pdb = PortfolioDB(_ROOT / "database" / "portfolio.db")
        scraped = 0
        if run_email_scraper is not None:
            scraped = run_email_scraper(pdb)
            logger.info("Morning IMAP ingestion completed: %d articles fetched.", scraped)

        scored = 0
        if score_news_batch is not None:
            scored = score_news_batch(pdb, limit=50)
            logger.info("Morning FinBERT sentiment scoring completed: %d articles scored.", scored)

        logger.info(
            "=== Morning news routine finished in %.1fs (scraped=%d, scored=%d) ===",
            time.perf_counter() - started,
            scraped,
            scored,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Morning news routine encountered error: %s", exc, exc_info=True)


def run_monthly_ml_retraining() -> None:
    """Monthly autonomous XGBoost & Isolation Forest model retraining routine (02:00 Paris, 1st of month)."""
    if datetime.today().day != 1:
        return

    started = time.perf_counter()
    logger.info("=== Monthly ML Model Retraining job starting (1st of month) ===")
    try:
        from ml_trainer import train_model
        metrics = train_model()
        acc_summary = ", ".join([f"{k}: {v.get('accuracy_pct', 0):.1f}%" for k, v in metrics.items() if isinstance(v, dict)])
        msg = (
            f"\U0001F9E0 **Autonomous Monthly ML Retraining Complete**\n"
            f"• Retrained models across regimes in {time.perf_counter() - started:.1f}s\n"
            f"• Accuracies: `{acc_summary}`"
        )
        logger.info(msg)
        asyncio.run(_post_webhook(msg))
    except Exception as exc:  # noqa: BLE001
        logger.error("Monthly ML model retraining failed: %s", exc, exc_info=True)
        asyncio.run(_post_webhook(f"\u26a0\ufe0f **Monthly ML Retraining Failed**: `{exc}`"))


def run_cloud_backup() -> None:
    """Run local Parquet database exports and upload to AWS S3 (Friday 19:00 Paris)."""
    started = time.perf_counter()
    logger.info("=== Weekly Database Backup Routine starting (Friday 19:00 Paris) ===")
    try:
        import subprocess
        res = subprocess.run([sys.executable, str(_ROOT / "tools" / "backup_databases.py")], capture_output=True, text=True, check=False)
        if res.returncode == 0:
            logger.info("Database backup completed in %.1fs: %s", time.perf_counter() - started, res.stdout.strip())
        else:
            logger.error("Database backup script failed (code %d): %s", res.returncode, res.stderr.strip())
    except Exception as exc:  # noqa: BLE001
        logger.error("Database backup routine failed: %s", exc, exc_info=True)


def _schedule_passes() -> None:
    """Register all periodic jobs in Europe/Paris time."""
    # Monthly ML retraining: probe daily at 02:00, acts only on the 1st of the month.
    schedule.every().day.at("02:00", _TIMEZONE).do(run_monthly_ml_retraining)
    # Morning pre-market news & newsletter ingestion: 08:00 Paris.
    schedule.every().day.at("08:00", _TIMEZONE).do(run_morning_news_routine)
    for pass_time in _PASS_TIMES:
        schedule.every().day.at(pass_time, _TIMEZONE).do(run_analysis_pass)
    # Weekly CIO digest: Friday 18:00 Paris.
    schedule.every().friday.at(_WEEKLY_REPORT_TIME, _TIMEZONE).do(run_weekly_report)
    # Weekly Earnings Calendar sync: Friday 18:30 Paris.
    if run_earnings_sync is not None:
        schedule.every().friday.at("18:30", _TIMEZONE).do(run_earnings_sync)
    # Weekly Cloud Backup: Friday 19:00 Paris.
    schedule.every().friday.at("19:00", _TIMEZONE).do(run_cloud_backup)
    # Monthly profit-shave: probe daily, act only on the 1st (guarded inside).
    schedule.every().day.at(_MONTHLY_CHECK_TIME, _TIMEZONE).do(run_monthly_rebalance)
    # Daily ATR stops (weekdays guarded inside).
    schedule.every().day.at(_ATR_STOP_CHECK_TIME, _TIMEZONE).do(run_daily_atr_stops)
    logger.info(
        "Scheduled: ML retrain 02:00; morning news 08:00; passes at %s; weekly report Fri %s; "
        "earnings sync Fri 18:30; backup Fri 19:00; monthly probe %s; ATR stops %s (%s).",
        ", ".join(_PASS_TIMES),
        _WEEKLY_REPORT_TIME,
        _MONTHLY_CHECK_TIME,
        _ATR_STOP_CHECK_TIME,
        _TIMEZONE,
    )


def main() -> None:
    """Entry point: parse CLI args and either run once or loop forever."""
    setup_app_logging(level=logging.INFO, console=True)

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
        help="Run monthly profit-shave now (ignores the 1st-of-month guard).",
    )
    parser.add_argument(
        "--atr-stops",
        action="store_true",
        help="Run daily ATR stop-loss evaluation now.",
    )
    parser.add_argument(
        "--sync-earnings",
        action="store_true",
        help="Run autonomous earnings calendar sync now.",
    )
    parser.add_argument(
        "--morning-news",
        action="store_true",
        help="Run pre-market morning news ingestion and FinBERT scoring now.",
    )
    parser.add_argument(
        "--retrain-ml",
        action="store_true",
        help="Run autonomous monthly ML retraining now (ignores 1st-of-month guard).",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="Run database Parquet export and cloud backup now.",
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

    if args.atr_stops:
        logger.info("--atr-stops: running ATR stop evaluation now.")
        asyncio.run(run_daily_atr_stops_async())
        return

    if args.rebalance:
        logger.info("--rebalance: running monthly profit-shave now.")
        asyncio.run(run_monthly_rebalance_async())
        return

    if args.sync_earnings:
        logger.info("--sync-earnings: syncing corporate earnings calendar now.")
        if run_earnings_sync is not None:
            run_earnings_sync()
        return

    if args.morning_news:
        logger.info("--morning-news: running morning news & IMAP ingestion now.")
        run_morning_news_routine()
        return

    if args.retrain_ml:
        logger.info("--retrain-ml: executing ML model retraining now.")
        try:
            from ml_trainer import train_model
            metrics = train_model()
            logger.info("Retraining complete. Metrics: %s", metrics)
        except Exception as exc:  # noqa: BLE001
            logger.error("Retraining failed: %s", exc)
        return

    if args.backup:
        logger.info("--backup: executing database backup now.")
        run_cloud_backup()
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

