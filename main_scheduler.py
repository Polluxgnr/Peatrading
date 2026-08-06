"""Root daemon scheduler for PEA Pollux.

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

# Native .env loader (no python-dotenv) — force keys into os.environ.
_ROOT = Path(__file__).resolve().parent
_env_path = _ROOT / "config" / "api_keys.env"
if _env_path.exists():
    with open(_env_path, "r", encoding="utf-8") as f:
        for line in f:
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ[k.strip()] = v.strip().strip(" '\"")

# --- Wire up the digit-prefixed package directories --------------------------
for _sub in (
    "00_data_sensors",
    "01_memory_core",
    "02_quant_engine",
    "03_risk_portfolio",
    "04_orchestrator_ai",
    "05_interfaces",
):
    sys.path.insert(0, str(_ROOT / _sub))

try:
    from env_loader import load_api_keys  # noqa: E402

    load_api_keys(_env_path)
except Exception:  # noqa: BLE001
    pass

import aiohttp  # noqa: E402
import schedule  # noqa: E402

from data_models import Position, PortfolioState, Signal, SignalStatus, SignalType  # noqa: E402
from duckdb_manager import TimeSeriesDB  # noqa: E402
from sqlite_portfolio import PortfolioDB  # noqa: E402
from market_prices_api import MarketDataFetcher  # noqa: E402
from macro_alpha_api import MacroAlphaSensor  # noqa: E402
from newsletter_api import run_morning_briefing_sync  # noqa: E402
from technical_scorer import SignalGenerator  # noqa: E402
from smart_dca_engine import SmartDcaCore  # noqa: E402
from monthly_rebalancer import PortfolioRebalancer  # noqa: E402
from signal_priority_cascade import SignalOrchestrator  # noqa: E402
from revocation_engine import RevocationEngine  # noqa: E402
from llm_explainer import NarrativeExplainer  # noqa: E402
from weekly_historian import WeeklyHistorian  # noqa: E402
from discord_copilot import DiscordCopilot  # noqa: E402
from logging_setup import get_component_logger, setup_app_logging, write_pipeline_status  # noqa: E402

logger = get_component_logger("scheduler")

_CONFIG_DIR = _ROOT / "config"
_UNIVERSE_PATH = _CONFIG_DIR / "pea_universe.yaml"
_RISK_PATH = _CONFIG_DIR / "risk_params.yaml"
_TIMEZONE = "Europe/Paris"
_PASS_TIMES = ("09:00", "13:30", "17:10")
_WEEKLY_REPORT_TIME = "18:00"     # Friday CIO digest.
_MONTHLY_CHECK_TIME = "08:30"     # Daily probe; profit-shave acts only on the 1st.
_MORNING_BRIEFING_TIME = "08:25"  # Newsletter Zeitgeist before market open.
_ATR_STOP_CHECK_TIME = "08:35"    # Daily ATR stop evaluation (weekdays via loop).
_LOOKBACK_DAYS = 3650  # ~10 years -> enough for all ML and long-term SMAs.


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
        raw_tickers = [
            entry["ticker"]
            for members in universe.get("universe", {}).values()
            for entry in members
        ]
        
        # Explicitly filter out macroeconomic symbols like IR3TIB01.EZQ.M.EM
        # We only keep typical equity suffixes for the PEA universe.
        valid_suffixes = (".PA", ".AS", ".NX", ".MI", ".MC", ".LS")
        clean_tickers = [
            t for t in raw_tickers 
            if any(t.endswith(s) for s in valid_suffixes) or t.isalpha()
        ]
        return clean_tickers
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
    generator = SignalGenerator(portfolio_db=pdb)
    orchestrator = SignalOrchestrator(
        config_dir=_CONFIG_DIR, portfolio_db=pdb, timeseries_db=tsdb
    )
    explainer = NarrativeExplainer()
    copilot = DiscordCopilot()

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

    # --- Meta-Labeling (XGBoost) & SHAP Explainability Phase ---
    try:
        from ml_trainer import _MODEL_PATH, FEATURE_COLS
        from ml_feature_store import build_ml_feature_row
        import xgboost as xgb
        
        if _MODEL_PATH.exists() and raw_signals:
            import shap
            logger.info("Meta-Labeling ML model found. Filtering raw signals...")
            bst = xgb.Booster()
            bst.load_model(_MODEL_PATH)
            explainer = shap.TreeExplainer(bst)
            
            # Fetch exogenous data once
            exog_dfs = {}
            for sym in ["^GSPC", "^IXIC", "EURUSD=X", "OAT.PA"]:
                try:
                    df_ex = tsdb.get_historical_prices(sym, days=252)
                    if df_ex is not None and not df_ex.empty:
                        exog_dfs[sym] = df_ex["Close"].astype(float)
                except Exception:
                    pass
            
            try:
                cw8_df = tsdb.get_historical_prices("CW8.PA", days=252)
                cw8_close = cw8_df["Close"].astype(float) if cw8_df is not None and not cw8_df.empty else None
            except Exception:
                cw8_close = None

            filtered_signals = []
            for sig in raw_signals:
                try:
                    df = tsdb.get_historical_prices(sig.ticker, days=252)
                    if df is None or df.empty:
                        continue
                    feat = build_ml_feature_row(
                        sig.ticker,
                        close=df["Close"].astype(float),
                        cw8_close=cw8_close,
                        exog_closes=exog_dfs,
                        reason="live inference",
                        pdb=pdb,
                        asof_idx=-1
                    )
                    from ml_trainer import predict_probability_with_shap
                    
                    proba, shap_vals = predict_probability_with_shap(feat)
                    
                    if proba is not None and shap_vals is not None:
                        # Set shap vals directly on the signal for later consumption by the UI
                        sig.shap_breakdown = shap_vals
                        sig.ml_probability = proba
                        
                        contributions = list(shap_vals.items())
                        contributions.sort(key=lambda x: abs(x[1]), reverse=True)
                        top_3 = contributions[:3]
                        shap_str = ", ".join([f"{k}: {v:+.2f}" for k, v in top_3])
                        
                        if proba >= 0.50:
                            sig.reason += f" | AI Meta-Label: {proba*100:.1f}% ({shap_str})"
                            filtered_signals.append(sig)
                        else:
                            logger.info(f"Signal {sig.ticker} rejected by ML Meta-Labeling (proba {proba*100:.1f}% < 50%)")
                    else:
                        filtered_signals.append(sig)
                except Exception as exc:
                    logger.debug(f"Failed to run ML filter for {sig.ticker}: {exc}")
                    filtered_signals.append(sig)  # Fallback: keep signal if ML fails
            
            raw_signals = filtered_signals
            logger.info(f"After ML Meta-Labeling, {len(raw_signals)} signal(s) passed.")
    except Exception as exc:
        logger.debug(f"ML Meta-Labeling phase skipped: {exc}")

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
    # --- Phase 49: Intelligent Capital Deployment (80% Rule) ---
    from pea_position_sizer import PeaSizer
    inv_rate = PeaSizer.investment_rate(portfolio)
    if inv_rate < 0.80:
        market_reg = getattr(macro_alpha, "_last_regime_result", None)
        is_bad_regime = False
        if market_reg:
            rm = market_reg.get("regime", "").upper()
            if rm in ("BEAR", "VOLATILE"):
                is_bad_regime = True
        
        if not is_bad_regime:
            logger.info("Invested capital (%.1f%%) < 80%%. Activating strategic deployment.", inv_rate * 100)
            # Find signals that were rejected ONLY because of score threshold
            rejected_for_score = [s for s in processed if s.status == SignalStatus.REJECTED and ("Score" in s.reason or "< 65" in s.reason)]
            rejected_for_score.sort(key=lambda x: x.score, reverse=True)
            
            deployed = 0
            for sig in rejected_for_score:
                if deployed >= 3:
                    break
                price = current_prices.get(sig.ticker, 0.0)
                if price > 0:
                    target_qty, sizing = orchestrator.sizer.size_with_explanation(sig, portfolio, price)
                    if target_qty > 0:
                        sig.target_qty = target_qty
                        sig.status = SignalStatus.APPROVED
                        sig.reason = f"DÉPLOIEMENT STRATÉGIQUE (Cash: {100 - inv_rate*100:.1f}%) | {target_qty} actions @ {price:.2f} EUR (Score: {sig.score:.1f})"
                        logger.info("Strategic deployment APPROVED %s (score=%.1f)", sig.ticker, sig.score)
                        deployed += 1


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

    if not os.getenv("DISCORD_WEBHOOK_URL"):
        logger.warning(
            "DISCORD_WEBHOOK_URL not set; %d alert(s) computed but not sent.",
            len(alertable),
        )
        return

    for signal in alertable:
        try:
            # Discord Spam Guard: ensure no other alert sent today for same ticker/type
            if pdb.has_duplicate_signal_today(signal):
                logger.info("Spam guard: %s alert already sent today, skipping Discord.", signal.ticker)
                continue
                
            price = current_prices.get(signal.ticker, 0.0)
            
            # Direct webhook alert for asynchronous paper trading
            from logging_setup import send_discord_alert
            alert_msg = f"🚀 **PAPER TRADE APPROVED**\n**Ticker:** {signal.ticker}\n**Action:** {signal.signal_type.value}\n**Quantity:** {signal.target_qty} shares\n**Price:** {price:.2f} EUR\n**Reason:** {signal.reason}"
            send_discord_alert(alert_msg)
            
            # Also try the rich copilot alert if bot is connected
            try:
                await copilot.send_signal_alert(
                    signal, portfolio, explainer=explainer, current_price=price
                )
            except Exception as e:
                logger.debug("Copilot bot alert skipped (bot might not be connected): %s", e)
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
        # Phase 40: daily concise Discord digest after the evening pass.
        local_hour = datetime.now().hour
        if local_hour >= 17:
            try:
                asyncio.run(run_daily_concise_report_async())
            except Exception:  # noqa: BLE001
                logger.exception("Daily concise report failed after evening pass.")
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


async def run_daily_concise_report_async() -> None:
    """Build and post the Phase 40 end-of-day Discord webhook digest."""
    from discord_copilot import send_daily_concise_report
    from pea_position_sizer import PeaSizer

    pdb = PortfolioDB()
    pdb.init_db()
    state = pdb.get_portfolio_state()
    inv_rate = PeaSizer.investment_rate(state)

    day_chg = None
    try:
        curve = pdb.get_equity_curve()
        if curve is not None and not curve.empty and len(curve) >= 2:
            eqs = curve.sort_values("date")["equity"].astype(float)
            if float(eqs.iloc[-2]) > 0:
                day_chg = (float(eqs.iloc[-1]) / float(eqs.iloc[-2]) - 1.0) * 100.0
    except Exception:  # noqa: BLE001
        day_chg = None

    top_pos = []
    for p in sorted(state.positions, key=lambda x: x.market_value, reverse=True)[:5]:
        top_pos.append({
            "ticker": p.ticker,
            "weight_pct": (
                p.market_value / state.total_equity * 100.0
                if state.total_equity else 0.0
            ),
            "pnl_pct": p.unrealized_pnl_pct * 100.0,
        })

    near_miss = []
    try:
        rows = pdb.fetch_signals_by_status(["PENDING", "REJECTED"], limit=40)
        for row in rows or []:
            try:
                sc = float(row.get("score") or 0)
            except (TypeError, ValueError):
                continue
            if 40 <= sc <= 64:
                near_miss.append({
                    "ticker": str(row.get("ticker") or ""),
                    "score": int(sc),
                    "missing": str(row.get("reason") or "")[:80] or "sous le seuil 65",
                })
        near_miss.sort(key=lambda x: x["score"], reverse=True)
        near_miss = near_miss[:3]
    except Exception:  # noqa: BLE001
        near_miss = []

    vix = None
    try:
        vix = float(MacroAlphaSensor().get_european_vix())
    except Exception:  # noqa: BLE001
        vix = None

    await send_daily_concise_report(
        equity=float(state.total_equity or 0),
        day_change_pct=day_chg,
        investment_rate_pct=inv_rate,
        top_positions=top_pos,
        near_miss=near_miss,
        vix=vix,
    )


def run_backfill_10y() -> None:
    """One-shot ~10-year OHLCV backfill for the PEA universe into DuckDB."""
    logger.info("=== 10-year OHLCV backfill starting (lookback=3650) ===")
    tsdb = TimeSeriesDB()
    tsdb.init_db()
    fetcher = MarketDataFetcher()
    tickers = _load_universe_tickers()
    core = _core_ticker()
    fetch_tickers = tickers + ([core] if core not in tickers else [])
    if not fetch_tickers:
        logger.error("No tickers to backfill.")
        return
    # Batch to avoid Yahoo timeouts on 600+ names × 10y.
    batch_size = 40
    ok_total = 0
    for i in range(0, len(fetch_tickers), batch_size):
        batch = fetch_tickers[i : i + batch_size]
        logger.info(
            "Backfill batch %d–%d / %d …",
            i + 1,
            min(i + batch_size, len(fetch_tickers)),
            len(fetch_tickers),
        )
        if fetcher.update_database(tsdb, batch, lookback_days=3650):
            ok_total += len(batch)
    logger.info("=== 10-year backfill done (%d tickers attempted) ===", ok_total)


async def run_weekly_report_async() -> None:
    """Generate the weekly CIO digest and push it to the Discord webhook."""
    pdb = PortfolioDB()
    pdb.init_db()
    explainer = NarrativeExplainer()
    historian = WeeklyHistorian()

    report = await historian.generate_weekly_report(pdb, explainer=explainer)
    header = (
        "\U0001F4C8 **PEA Pollux - Weekly Risk & Performance Digest**\n"
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


def run_morning_briefing() -> None:
    """08:25 Paris: IMAP newsletter headlines → LLM Zeitgeist → JSON file.

    Strictly read-only IMAP. Failures write an Indisponible briefing so the
    dashboard never crashes.
    """
    started = time.perf_counter()
    logger.info("=== Morning briefing (newsletter Zeitgeist) starting ===")
    try:
        result = run_morning_briefing_sync(folder=os.getenv("NEWSLETTER_IMAP_FOLDER", "Finance"))
        n = len(result.get("headlines") or [])
        logger.info(
            "=== Morning briefing done in %.1fs (%d headlines) ===",
            time.perf_counter() - started,
            n,
        )
    except Exception as exc:  # noqa: BLE001
        logger.critical("Morning briefing FAILED: %s", exc, exc_info=True)
        try:
            from newsletter_api import NewsletterSensor

            NewsletterSensor().write_briefing("Indisponible", [])
        except Exception:  # noqa: BLE001
            pass


def run_nightly_profile_batch() -> None:
    """04:00 Paris: Sequential massive pre-computation of all ticker profiles."""
    import random
    started = time.perf_counter()
    logger.info("=== Night Run (Profile Batch) starting ===")
    
    try:
        if not _UNIVERSE_PATH.exists():
            logger.error("Universe file not found for Night Run.")
            return
            
        with open(_UNIVERSE_PATH, "r", encoding="utf-8") as f:
            univ = yaml.safe_load(f) or {}
            
        tickers = list(univ.keys())
        total = len(tickers)
        logger.info(f"Night Run will process {total} tickers.")
        
        # We need the profile builder
        pb_dir = _ROOT / "01_memory_core"
        if str(pb_dir) not in sys.path:
            sys.path.insert(0, str(pb_dir))
        from profile_builder import build_and_save_ticker_profile
        
        for i, tk in enumerate(tickers, 1):
            write_pipeline_status({"night_run_status": f"Running {i}/{total} ({tk})..."})
            try:
                build_and_save_ticker_profile(tk, include_llm=False)
            except Exception as e:
                logger.error(f"Night Run failed for {tk}: {e}")
                
            time.sleep(random.uniform(1.5, 3.5))
            
        write_pipeline_status({"night_run_status": "Completed"})
        logger.info(
            "=== Night Run done in %.1fs (%d tickers) ===",
            time.perf_counter() - started,
            total,
        )
    except Exception as exc:
        logger.critical("Night Run FAILED: %s", exc, exc_info=True)
        write_pipeline_status({"night_run_status": f"Failed: {exc}"})


def run_weekend_retraining() -> None:
    """Run model retraining on weekends, checked by drift monitor."""
    logger.info("Starting weekend retraining job...")
    
    import sys
    sys.path.insert(0, str(_ROOT / "04_orchestrator_ai"))
    try:
        from model_drift_monitor import check_model_drift
        has_drift = check_model_drift()
        if not has_drift:
            # We can force retrain anyway, but for now we log that we're retraining to stay fresh
            logger.info("No critical drift detected, but retraining to keep models fresh on new data.")
    except Exception as e:
        logger.warning(f"Drift monitor failed: {e}. Retraining anyway.")

    try:
        import subprocess
        cmd = [sys.executable, str(_ROOT / "02_quant_engine" / "ml_trainer.py")]
        result = subprocess.run(cmd, check=True, text=True, capture_output=True)
        for line in result.stdout.splitlines():
            logger.info("[ML_TRAINER] %s", line)
        logger.info("Weekend retraining completed successfully.")
    except subprocess.CalledProcessError as e:
        logger.error("Weekend retraining failed with code %d", e.returncode)
        for line in e.stderr.splitlines():
            logger.error("[ML_TRAINER ERR] %s", line)
    except Exception as e:
        logger.exception("Unexpected error during weekend retraining: %s", e)

def _schedule_passes() -> None:
    """Register all periodic jobs in Europe/Paris time."""
    for pass_time in _PASS_TIMES:
        schedule.every().day.at(pass_time, _TIMEZONE).do(run_analysis_pass)
    # Weekly CIO digest: Friday 18:00 Paris.
    schedule.every().friday.at(_WEEKLY_REPORT_TIME, _TIMEZONE).do(run_weekly_report)
    # Morning newsletter Zeitgeist (before monthly probe / ATR stops).
    schedule.every().day.at(_MORNING_BRIEFING_TIME, _TIMEZONE).do(run_morning_briefing)
    # Monthly profit-shave: probe daily, act only on the 1st (guarded inside).
    schedule.every().day.at(_MONTHLY_CHECK_TIME, _TIMEZONE).do(run_monthly_rebalance)
    # Daily ATR stops (weekdays guarded inside).
    schedule.every().day.at(_ATR_STOP_CHECK_TIME, _TIMEZONE).do(run_daily_atr_stops)
    # Night Run: Mass profile pre-calculation
    schedule.every().day.at("04:00", _TIMEZONE).do(run_nightly_profile_batch)
    # Weekend Auto-Retraining
    schedule.every().saturday.at("02:00", _TIMEZONE).do(run_weekend_retraining)
    logger.info(
        "Scheduled: passes at %s; weekly report Fri %s; morning briefing %s; "
        "monthly probe %s; ATR stops %s; Night Run 04:00 (%s).",
        ", ".join(_PASS_TIMES),
        _WEEKLY_REPORT_TIME,
        _MORNING_BRIEFING_TIME,
        _MONTHLY_CHECK_TIME,
        _ATR_STOP_CHECK_TIME,
        _TIMEZONE,
    )


def main() -> None:
    """Entry point: parse CLI args and either run once or loop forever."""
    setup_app_logging(level=logging.INFO, console=True)

    parser = argparse.ArgumentParser(description="PEA Pollux daemon.")
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
        "--briefing",
        action="store_true",
        help="Run morning newsletter Zeitgeist now, then exit.",
    )
    parser.add_argument(
        "--backfill-10y",
        action="store_true",
        help="Fetch ~10y OHLCV for the PEA universe into DuckDB, then exit.",
    )
    parser.add_argument(
        "--daily-report",
        action="store_true",
        help="Send the Phase 40 daily concise Discord report now, then exit.",
    )
    parser.add_argument(
        "--night-run",
        action="store_true",
        help="Run the massive profile pre-computation (Night Run) now, then exit.",
    )
    args = parser.parse_args()

    if args.backfill_10y:
        logger.info("--backfill-10y: starting long-horizon OHLCV ingest.")
        run_backfill_10y()
        return

    if args.night_run:
        logger.info("--night-run: starting massive profile pre-computation.")
        run_nightly_profile_batch()
        return

    if args.daily_report:
        logger.info("--daily-report: posting concise Discord digest.")
        asyncio.run(run_daily_concise_report_async())
        return

    if args.now:
        logger.info("--now: running a single immediate pass.")
        run_analysis_pass()
        return

    if args.weekly:
        logger.info("--weekly: generating the weekly report now.")
        run_weekly_report()
        return

    if args.briefing:
        logger.info("--briefing: running morning Zeitgeist now.")
        run_morning_briefing()
        return

    if args.atr_stops:
        logger.info("--atr-stops: running ATR stop evaluation now.")
        asyncio.run(run_daily_atr_stops_async())
        return

    if args.rebalance:
        logger.info("--rebalance: running monthly profit-shave now.")
        asyncio.run(run_monthly_rebalance_async())
        return

    _schedule_passes()
    logger.info("\U0001F6E1\uFE0F PEA Pollux Daemon started. "
                "Waiting for scheduled runs...")
    
    last_heartbeat = 0
    start_time = time.time()
    
    while True:
        try:
            schedule.run_pending()
            
            now = time.time()
            if now - last_heartbeat > 900:  # 15 minutes = 900 seconds
                last_heartbeat = now
                hb_path = _LOG_DIR / "health_status.json"
                import json
                hb_path.parent.mkdir(parents=True, exist_ok=True)
                hb_path.write_text(json.dumps({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "status": "running",
                    "uptime_seconds": int(now - start_time)
                }), encoding="utf-8")
                
            time.sleep(60)
        except KeyboardInterrupt:
            logger.info("Shutdown requested; exiting daemon loop.")
            break
        except Exception:  # noqa: BLE001 - never let the loop die.
            logger.critical("Scheduler loop error; continuing.", exc_info=True)
            time.sleep(60)


if __name__ == "__main__":
    main()
