"""Root daemon scheduler and Prefect workflow orchestrator for PEA Sniper Terminal V-Prime.

Ties the whole pipeline together and runs it on the multi-pass European market
schedule (09:00, 13:30, 17:10 Paris time, weekdays only):

    Data Ingestion Hub (Market Data + AMF + Macro + News)
    -> Isolated CPU Quant & ML Cascade (ProcessPoolExecutor)
    -> Smart DCA & Anti-Stale Revocation
    -> Sovereign PM Alerts (Discord Copilot)

Design rules honoured here:
  * Prefect Orchestration: Core steps are encapsulated as retry-capable @task and @flow.
  * CPU Isolation: Heavy NLP scoring / ML models offloaded via CpuTaskIsolator.
  * Async/sync bridge: Synchronous daemon runs async Prefect flows via asyncio.run.
  * Zero crash tolerance: Every pass is guarded so outages log CRITICAL and continue.
  * Timezone awareness: Pinned to Europe/Paris; weekends are safely skipped.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# --- Wire up the digit-prefixed package directories --------------------------
_ROOT = Path(__file__).resolve().parent
for _sub in (
    "00_data_sensors",
    "00_data_sensors/adapters",
    "01_memory_core",
    "02_quant_engine",
    "03_risk_portfolio",
    "04_orchestrator_ai",
    "05_interfaces",
):
    sys.path.insert(0, str(_ROOT / _sub))

import aiohttp  # noqa: E402
import schedule  # noqa: E402

# Prefect decorators with robust fallback
try:
    from prefect import flow, task
except ImportError:
    def task(*dargs, **dkwargs):
        def decorator(f):
            return f
        if len(dargs) == 1 and callable(dargs[0]) and not dkwargs:
            return dargs[0]
        return decorator

    def flow(*dargs, **dkwargs):
        def decorator(f):
            return f
        if len(dargs) == 1 and callable(dargs[0]) and not dkwargs:
            return dargs[0]
        return decorator

from cpu_isolator import cpu_isolator  # noqa: E402
from data_models import PortfolioState, Position, Signal, SignalStatus, SignalType  # noqa: E402
from discord_copilot import DiscordCopilot  # noqa: E402
from duckdb_manager import TimeSeriesDB  # noqa: E402
from hub import DataIngestionHub  # noqa: E402
from llm_explainer import NarrativeExplainer  # noqa: E402
from logging_setup import get_component_logger, setup_app_logging, write_pipeline_status  # noqa: E402
from macro_alpha_api import MacroAlphaSensor  # noqa: E402
from market_prices_api import MarketDataFetcher  # noqa: E402
from monthly_rebalancer import PortfolioRebalancer  # noqa: E402
from revocation_engine import RevocationEngine  # noqa: E402
from signal_priority_cascade import SignalOrchestrator  # noqa: E402
from smart_dca_engine import SmartDcaCore  # noqa: E402
from sqlite_portfolio import PortfolioDB  # noqa: E402
from technical_scorer import SignalGenerator  # noqa: E402
from weekly_historian import WeeklyHistorian  # noqa: E402

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
# Run analysis pass every 30 minutes during Euronext market hours (09:00 to 17:30 Paris time)
_PASS_TIMES = tuple(f"{h:02d}:{m:02d}" for h in range(9, 18) for m in (0, 30) if not (h == 17 and m > 30))
_WEEKLY_REPORT_TIME = "18:00"     # Friday CIO digest.

_MONTHLY_CHECK_TIME = "08:30"     # Daily probe; profit-shave acts only on the 1st.
_ATR_STOP_CHECK_TIME = "08:35"    # Daily ATR stop evaluation (weekdays via loop).
_LOOKBACK_DAYS = 400              # ~270 trading days -> enough for SMA-200.


def _post_webhook(url: str, json_payload: dict) -> None:
    """Post JSON payload to Discord webhook synchronously/asynchronously."""
    if not url:
        return
    try:
        async def _post():
            async with aiohttp.ClientSession() as session:
                await session.post(url, json=json_payload)
        asyncio.run(_post())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to post webhook to %s: %s", url, exc)


def _core_ticker() -> str:
    """Read the Core ETF ticker from risk_params.yaml (default CW8.PA)."""
    try:
        with open(_RISK_PATH, "r", encoding="utf-8") as fh:
            risk = yaml.safe_load(fh) or {}
        return str(risk.get("CORE_TICKER", "CW8.PA"))
    except Exception:  # noqa: BLE001
        return "CW8.PA"


def _load_universe_tickers() -> list[str]:
    """Return all active tickers from pea_universe.yaml."""
    try:
        with open(_UNIVERSE_PATH, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        if not raw:
            return []
        u_data = raw if isinstance(raw, dict) else {}
        univ = u_data.get("universe", u_data)
        tickers: list[str] = []
        if isinstance(univ, dict):
            for sector, items in univ.items():
                if isinstance(items, list):
                    for it in items:
                        if isinstance(it, dict) and "ticker" in it:
                            tickers.append(str(it["ticker"]).strip())
                        elif isinstance(it, str):
                            tickers.append(it.strip())
        elif isinstance(univ, list):
            for it in univ:
                if isinstance(it, str):
                    tickers.append(it.strip())
                elif isinstance(it, dict) and "ticker" in it:
                    tickers.append(str(it["ticker"]).strip())
        return [t for t in tickers if t]
    except Exception:  # noqa: BLE001
        logger.exception("Failed to read universe file %s.", _UNIVERSE_PATH)
        return []


def _load_universe_sector_map() -> dict[str, str]:
    """Return mapping of ticker -> sector from pea_universe.yaml."""
    try:
        with open(_UNIVERSE_PATH, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        if not raw:
            return {}
        u_data = raw if isinstance(raw, dict) else {}
        univ = u_data.get("universe", u_data)
        sector_map: dict[str, str] = {}
        if isinstance(univ, dict):
            for sector, items in univ.items():
                if isinstance(items, list):
                    for it in items:
                        if isinstance(it, dict) and "ticker" in it:
                            sector_map[str(it["ticker"]).strip()] = str(sector).strip()
        elif isinstance(univ, list):
            for it in univ:
                if isinstance(it, dict) and "ticker" in it and "sector" in it:
                    sector_map[str(it["ticker"]).strip()] = str(it["sector"]).strip()
        return sector_map
    except Exception:  # noqa: BLE001
        logger.exception("Failed to read sector map from %s.", _UNIVERSE_PATH)
        return {}


def _refresh_portfolio_prices(
    pdb: PortfolioDB,
    portfolio: PortfolioState,
    current_prices: dict[str, float],
) -> PortfolioState:
    """Update held positions with latest prices and persist marked-to-market equity."""
    refreshed: list[Position] = []
    positions_value = 0.0
    for pos in portfolio.positions:
        cur_px = current_prices.get(pos.ticker)
        if cur_px and cur_px > 0:
            up_pos = Position(
                ticker=pos.ticker,
                shares=pos.shares,
                average_buy_price=pos.average_buy_price,
                current_price=cur_px,
                stop_loss=pos.stop_loss,
            )
            refreshed.append(up_pos)
            positions_value += cur_px * pos.shares
        else:
            refreshed.append(pos)
            if pos.current_price:
                positions_value += pos.current_price * pos.shares
            elif pos.average_buy_price:
                positions_value += pos.average_buy_price * pos.shares

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
    except Exception:  # noqa: BLE001
        logger.exception("Failed to persist marked-to-market portfolio.")
        return portfolio
    return new_state


def _latest_prices(tsdb: TimeSeriesDB, tickers: list[str]) -> dict[str, float]:
    """Fetch the most recent close for each ticker from DuckDB."""
    prices: dict[str, float] = {}
    for ticker in tickers:
        try:
            df = tsdb.get_historical_prices(ticker, days=2)
            if df is not None and not df.empty:
                prices[ticker] = float(df["Close"].iloc[-1])
        except Exception:  # noqa: BLE001
            logger.warning("Could not read latest price for %s.", ticker)
    return prices


# =============================================================================
# PREFECT TASKS (Layer 1 Ingestion, Layer 2 Persistence, Layer 3 Workers)
# =============================================================================

@task(name="Ingest_Market_And_Alternative_Data", retries=2, retry_delay_seconds=30)
async def task_ingest_data(
    tsdb: TimeSeriesDB,
    pdb: PortfolioDB,
    fetch_tickers: list[str],
    lookback_days: int = _LOOKBACK_DAYS,
) -> bool:
    """Task: Fetch OHLCV market bars and poll alternative data sensors concurrently."""
    fetcher = MarketDataFetcher()
    ok = fetcher.update_database(tsdb, fetch_tickers, lookback_days=lookback_days)
    if not ok:
        logger.error("Market data ingestion failed.")
        return False

    # Layer 1 Ingestion Hub: Poll AMF short positions and Macro signals concurrently
    try:
        hub = DataIngestionHub()
        hub.register_default_adapters()
        alt_signals = await hub.fetch_all_alternative_signals()
        saved = hub.save_signals_to_sqlite(alt_signals, pdb)
        logger.info("Data Ingestion Hub: %d alternative signal(s) saved to SQLite.", saved)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Alternative Data Ingestion Hub encountered an issue: %s", exc)

    # News Ingestion (RSS, APIs, IMAP)
    try:
        from news_api_client import run_api_scraper
        from news_email_scraper import run_email_scraper
        from news_rss_scraper import run_rss_scraper

        run_api_scraper(pdb)
        run_email_scraper(pdb)
        run_rss_scraper(pdb)
    except Exception as e:  # noqa: BLE001
        logger.warning("News scraping failed: %s", e)

    return True


@task(name="Quant_ML_Signal_Generation", retries=1)
async def task_generate_and_orchestrate(
    tsdb: TimeSeriesDB,
    pdb: PortfolioDB,
    tickers: list[str],
    fetch_tickers: list[str],
    vix_level: float,
    core_ticker: str,
) -> list[Signal]:
    """Task: Generate raw quant signals, execute ML predictive veto, and size positions."""
    generator = SignalGenerator()
    orchestrator = SignalOrchestrator(
        config_dir=_CONFIG_DIR, portfolio_db=pdb, timeseries_db=tsdb
    )
    core_engine = SmartDcaCore(_CONFIG_DIR)

    # Offload CPU-bound Mean-Reversion quant signals to process pool
    mre_signals = await cpu_isolator.run_in_process(generator.generate_raw_signals, tsdb, tickers)
    logger.info("Mean-Reversion engine produced %d raw signal(s).", len(mre_signals))

    # StatArb Cointegration engine
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

    # Orchestration Phase (satellite)
    portfolio: PortfolioState = pdb.get_portfolio_state()
    current_prices = _latest_prices(tsdb, fetch_tickers)
    portfolio = _refresh_portfolio_prices(pdb, portfolio, current_prices)

    # Process through full cascade (VIX floor, Isolation Forest anomaly, XGBoost SHAP, correlation, sizing)
    processed = orchestrator.process_raw_signals(
        raw_signals, portfolio, current_prices, vix_level=vix_level
    )

    # Core Phase: Smart DCA on the MSCI World ETF (immune to VIX veto)
    core_signal = core_engine.evaluate_cw8(
        tsdb, portfolio.cash_available, portfolio.total_equity
    )
    if core_signal and (core_signal.target_qty or 0) > 0:
        core_signal.status = SignalStatus.APPROVED
        processed.append(core_signal)
        logger.info("Core DCA APPROVED: buy %d %s.", core_signal.target_qty, core_ticker)

    # Revocation Phase: anti-stale evaluation on existing PENDING signals
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
            orig_px = cur_px if cur_px > 0 else 1.0
            cur_px = cur_px if cur_px > 0 else 1.0

            updated = revoker.evaluate_signal(sig, cur_px, orig_px)
            if updated.status in (SignalStatus.REVOKED, SignalStatus.EXPIRED):
                processed.append(updated)
                logger.info(
                    "Pending signal %s -> %s (%s).",
                    updated.id[:8], updated.status.value, updated.ticker,
                )
        except Exception:  # noqa: BLE001
            logger.exception("Revocation failed for row %s.", row.get("id"))

    # Audit logging
    for signal in processed:
        try:
            pdb.log_signal(signal)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to audit-log signal %s.", signal.id)

    return processed


@task(name="Dispatch_Discord_Alerts")
async def task_dispatch_alerts(
    processed: list[Signal],
    portfolio: PortfolioState,
    current_prices: dict[str, float],
    copilot: DiscordCopilot,
    explainer: NarrativeExplainer,
) -> None:
    """Task: Deliver enriched, actionable signals to Discord Copilot."""
    alertable = [
        s for s in processed
        if s.status in (SignalStatus.APPROVED, SignalStatus.REVOKED)
    ]
    if not alertable:
        logger.info("No APPROVED/REVOKED signals to push to Discord this pass.")
        return

    if not os.getenv("DISCORD_TOKEN"):
        logger.warning("DISCORD_TOKEN not set; %d alert(s) computed but not sent.", len(alertable))
        return

    for signal in alertable:
        try:
            price = current_prices.get(signal.ticker, 0.0)
            await copilot.send_signal_alert(
                signal, portfolio, explainer=explainer, current_price=price
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to send Discord alert for %s.", signal.ticker)


# =============================================================================
# MAIN PREFECT FLOW
# =============================================================================

@flow(name="PEA_Pollux_Market_Cycle")
async def pea_pollux_market_cycle() -> None:
    """Main Prefect Flow: Coordinates data ingestion, quant signal scoring, and sovereign alerts."""
    tsdb = TimeSeriesDB()
    tsdb.init_db()
    pdb = PortfolioDB()
    pdb.init_db()

    explainer = NarrativeExplainer()
    copilot = DiscordCopilot(portfolio_db=pdb, explainer=explainer)
    macro_alpha = MacroAlphaSensor()
    core_ticker = _core_ticker()

    tickers = _load_universe_tickers()
    if not tickers:
        logger.error("No tickers in universe; aborting cycle.")
        return

    fetch_tickers = list(set(tickers + [core_ticker, "^FCHI", "^GSPC", "^IXIC", "EURUSD=X", "OAT.PA", "CW8.PA"]))
    logger.info("Universe loaded: %d tickers (+core %s, +macro indices).", len(tickers), core_ticker)

    # Step 1: Ingestion Task
    ingestion_ok = await task_ingest_data(tsdb, pdb, fetch_tickers, lookback_days=_LOOKBACK_DAYS)
    if not ingestion_ok:
        logger.error("Data ingestion failed; skipping this cycle (no stale trades).")
        return

    # Step 2: Macro Volatility Gauge
    vix_level = macro_alpha.get_european_vix()

    # Step 3: Quant & ML Signal Generation Task
    processed = await task_generate_and_orchestrate(
        tsdb, pdb, tickers, fetch_tickers, vix_level, core_ticker
    )

    # Step 4: Dispatch Alerts Task
    portfolio: PortfolioState = pdb.get_portfolio_state()
    current_prices = _latest_prices(tsdb, fetch_tickers)
    await task_dispatch_alerts(processed, portfolio, current_prices, copilot, explainer)


# Alias for backward compatibility
run_pipeline_async = pea_pollux_market_cycle


def run_analysis_pass() -> None:
    """Synchronous wrapper: skip weekends, run the Prefect flow safely."""
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
    logger.info("=== Analysis pass starting (Prefect Flow) ===")
    try:
        asyncio.run(pea_pollux_market_cycle())
        elapsed = time.perf_counter() - started
        logger.info("=== Analysis pass completed in %.1fs ===", elapsed)
        write_pipeline_status({
            "job": "analysis",
            "status": "ok",
            "elapsed_s": round(elapsed, 2),
            "health": "green",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:  # noqa: BLE001
        elapsed = time.perf_counter() - started
        logger.critical("Analysis pass crashed after %.1fs: %s", elapsed, exc, exc_info=True)
        write_pipeline_status({
            "job": "analysis",
            "status": "crashed",
            "error": str(exc),
            "elapsed_s": round(elapsed, 2),
            "health": "red",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })


@task(name="Pre_Market_Morning_News", retries=2, retry_delay_seconds=30)
def run_morning_news_routine() -> None:
    """Pre-market morning news ingestion and FinBERT scoring."""
    if datetime.today().weekday() >= 5:
        logger.info("Morning news routine: skipping weekend day.")
        return

    started = time.perf_counter()
    logger.info("=== Morning News & Newsletter Routine starting ===")
    try:
        pdb = PortfolioDB()
        pdb.init_db()

        if run_email_scraper is not None:
            logger.info("Running IMAP newsletter ingestion (Yahoo Mail)...")
            run_email_scraper(pdb)

        try:
            from news_api_client import run_api_scraper
            from news_rss_scraper import run_rss_scraper
            run_api_scraper(pdb)
            run_rss_scraper(pdb)
        except Exception as e:  # noqa: BLE001
            logger.warning("Scrapers encountered an issue: %s", e)

        if score_news_batch is not None:
            logger.info("Scoring unprocessed news via ProsusAI/finbert...")
            scored_count = score_news_batch(pdb, batch_size=50)
            logger.info("FinBERT scored %d news items.", scored_count)

        elapsed = time.perf_counter() - started
        logger.info("=== Morning News Routine completed in %.1fs ===", elapsed)
    except Exception as exc:  # noqa: BLE001
        logger.error("Morning news routine failed: %s", exc, exc_info=True)


@task(name="Autonomous_Monthly_ML_Retraining")
def run_monthly_ml_retraining() -> None:
    """Retrain ML models across market regimes on the 1st day of each month."""
    today = datetime.today()
    if today.day != 1:
        logger.debug("Monthly ML Retraining probe: day=%d (not 1st), skipping.", today.day)
        return

    started = time.perf_counter()
    logger.info("=== Monthly ML Model Retraining job starting (1st of month) ===")
    try:
        from ml_trainer import train_model
        metrics = train_model()
        elapsed = time.perf_counter() - started

        acc_summary = ", ".join(f"{k}: {v.get('accuracy', 0):.1%}" for k, v in metrics.items() if isinstance(v, dict))
        logger.info("Autonomous Monthly ML Retraining Complete in %.1fs. Metrics: %s", elapsed, acc_summary)

        # Discord webhook notification
        webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
        if webhook_url:
            content = (
                "🧠 **Autonomous Monthly ML Retraining Complete**\n"
                f"• Retrained models across regimes in {elapsed:.1f}s\n"
                f"• Accuracies: `{acc_summary}`"
            )
            _post_webhook(webhook_url, {"content": content})
    except Exception as exc:  # noqa: BLE001
        logger.error("Monthly ML Model Retraining failed: %s", exc, exc_info=True)


@task(name="Cloud_Database_Backup")
def run_cloud_backup() -> None:
    """Run Parquet exports and off-instance Cloudflare R2 / S3 backup."""
    started = time.perf_counter()
    logger.info("=== Weekly Database Backup Routine starting (Friday 19:00 Paris) ===")
    try:
        backup_script = _ROOT / "tools" / "backup_databases.py"
        res = subprocess.run([sys.executable, str(backup_script)], capture_output=True, text=True, check=False)
        if res.returncode == 0:
            logger.info("Database backup completed in %.1fs: %s", time.perf_counter() - started, res.stdout.strip())
        else:
            logger.error("Database backup script failed (code %d): %s", res.returncode, res.stderr.strip())
    except Exception as exc:  # noqa: BLE001
        logger.error("Database backup routine failed: %s", exc, exc_info=True)


async def run_monthly_rebalance_async() -> None:
    """Probe daily: profit-shave triggers on 1st of month."""
    started = time.perf_counter()
    try:
        rebalancer = PortfolioRebalancer(_CONFIG_DIR)
        pdb = PortfolioDB()
        pdb.init_db()
        tsdb = TimeSeriesDB()
        tsdb.init_db()
        portfolio = pdb.get_portfolio_state()
        tickers = [p.ticker for p in portfolio.positions]
        if not tickers:
            logger.info("Monthly rebalance: portfolio empty, nothing to evaluate.")
            return

        current_prices = _latest_prices(tsdb, tickers)
        portfolio = _refresh_portfolio_prices(pdb, portfolio, current_prices)

        signals = rebalancer.evaluate_monthly_profit_shave(portfolio, current_prices)
        logger.info("Monthly rebalance produced %d SELL signal(s).", len(signals))

        for sig in signals:
            pdb.log_signal(sig)

        webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
        if webhook_url and signals:
            lines = [f"• **{s.ticker}**: Vendre {s.target_qty} titre(s) - {s.reason}" for s in signals]
            msg = f"🔄 **Ajustement Mensuel PEA (Prise de Bénéfices)**\n" + "\n".join(lines)
            _post_webhook(webhook_url, {"content": msg})
    except Exception as exc:  # noqa: BLE001
        logger.error("Monthly rebalance failed: %s", exc, exc_info=True)


def run_monthly_rebalance() -> None:
    today = datetime.today()
    if today.day != 1:
        return
    asyncio.run(run_monthly_rebalance_async())


async def run_daily_atr_stops_async() -> None:
    """Evaluate daily ATR trailing stops."""
    if datetime.today().weekday() >= 5:
        return
    try:
        rebalancer = PortfolioRebalancer(_CONFIG_DIR)
        pdb = PortfolioDB()
        pdb.init_db()
        tsdb = TimeSeriesDB()
        tsdb.init_db()
        portfolio = pdb.get_portfolio_state()
        tickers = [p.ticker for p in portfolio.positions]
        if not tickers:
            return
        current_prices = _latest_prices(tsdb, tickers)
        portfolio = _refresh_portfolio_prices(pdb, portfolio, current_prices)
        signals = rebalancer.evaluate_atr_stops(portfolio, current_prices, tsdb)
        if signals:
            logger.warning("Daily ATR Stops: %d stop(s) triggered!", len(signals))
            for sig in signals:
                pdb.log_signal(sig)
            webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
            if webhook_url:
                lines = [f"🚨 **STOP LOSS ATR DÉCLENCHÉ**: Vendre tout {s.ticker} ({s.reason})" for s in signals]
                msg = "\n".join(lines)
                _post_webhook(webhook_url, {"content": msg})
    except Exception as exc:  # noqa: BLE001
        logger.error("Daily ATR stops evaluation failed: %s", exc, exc_info=True)


def run_daily_atr_stops() -> None:
    asyncio.run(run_daily_atr_stops_async())


def run_weekly_report() -> None:
    """Generate Friday CIO weekly report."""
    if datetime.today().weekday() != 4:
        return
    try:
        pdb = PortfolioDB()
        pdb.init_db()
        historian = WeeklyHistorian()
        state = pdb.get_portfolio_state()
        report = historian.generate_report(state)
        logger.info("Weekly CIO report generated (%d chars).", len(report))
        webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
        if webhook_url and report:
            chunks = [report[i:i+1900] for i in range(0, len(report), 1900)]
            for c in chunks:
                _post_webhook(webhook_url, {"content": c})
    except Exception as exc:  # noqa: BLE001
        logger.error("Weekly report failed: %s", exc, exc_info=True)


def _schedule_passes() -> None:
    """Register all periodic jobs in Europe/Paris time."""
    schedule.every().day.at("02:00", _TIMEZONE).do(run_monthly_ml_retraining)
    schedule.every().day.at("08:00", _TIMEZONE).do(run_morning_news_routine)
    for pass_time in _PASS_TIMES:
        schedule.every().day.at(pass_time, _TIMEZONE).do(run_analysis_pass)
    schedule.every().friday.at(_WEEKLY_REPORT_TIME, _TIMEZONE).do(run_weekly_report)
    if run_earnings_sync is not None:
        schedule.every().friday.at("18:30", _TIMEZONE).do(run_earnings_sync)
    schedule.every().friday.at("19:00", _TIMEZONE).do(run_cloud_backup)
    schedule.every().day.at(_MONTHLY_CHECK_TIME, _TIMEZONE).do(run_monthly_rebalance)
    schedule.every().day.at(_ATR_STOP_CHECK_TIME, _TIMEZONE).do(run_daily_atr_stops)
    logger.info(
        "Scheduled (Prefect-ready): ML retrain 02:00; morning news 08:00; passes at %s; weekly report Fri %s; "
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
    parser.add_argument("--now", action="store_true", help="Run a single analysis pass immediately, then exit.")
    parser.add_argument("--weekly", action="store_true", help="Generate and send the weekly report now, then exit.")
    parser.add_argument("--rebalance", action="store_true", help="Run monthly profit-shave now.")
    parser.add_argument("--atr-stops", action="store_true", help="Run daily ATR stop-loss evaluation now.")
    parser.add_argument("--sync-earnings", action="store_true", help="Run autonomous earnings calendar sync now.")
    parser.add_argument("--morning-news", action="store_true", help="Run pre-market morning news ingestion now.")
    parser.add_argument("--retrain-ml", action="store_true", help="Run autonomous monthly ML retraining now.")
    parser.add_argument("--backup", action="store_true", help="Run database Parquet export and cloud backup now.")
    args = parser.parse_args()

    if args.now:
        logger.info("--now: running a single immediate pass via Prefect flow.")
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
    logger.info("🛡️ PEA Sniper Terminal Daemon started. Waiting for scheduled runs...")
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
