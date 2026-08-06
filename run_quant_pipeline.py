import sys
import time
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT / "01_memory_core"))
from logging_setup import get_logger

logger = get_logger("quant_pipeline_orchestrator")

_ROOT = Path(__file__).resolve().parent

def run_step(script_name: str, args: list[str] = None):
    """Run a Python script as a subprocess and stream its output."""
    cmd = [sys.executable, str(_ROOT / script_name)]
    if args:
        cmd.extend(args)
        
    logger.info("=" * 60)
    logger.info("🚀 STARTING: %s", script_name)
    logger.info("=" * 60)
    
    start_t = time.time()
    try:
        result = subprocess.run(cmd, check=True, text=True, capture_output=True)
        # Log stdout line by line
        for line in result.stdout.splitlines():
            if line.strip():
                logger.info("  [OUT] %s", line)
        for line in result.stderr.splitlines():
            if line.strip():
                logger.warning("  [ERR] %s", line)
                
        elapsed = time.time() - start_t
        logger.info("✅ SUCCESS: %s completed in %.1fs", script_name, elapsed)
    except subprocess.CalledProcessError as e:
        logger.error("❌ FAILED: %s returned exit code %d", script_name, e.returncode)
        for line in e.stderr.splitlines():
            logger.error("  [ERR] %s", line)
        raise

def main():
    logger.info("🌟 Starting Master Quant Pipeline Orchestrator 🌟")
    total_start = time.time()
    
    try:
        # Phase 1: Fetch Market Data
        run_step("run_backfill.py", ["--days", "3650"])
        
        # Phase 2: Ingest Alternative Data (News / Sentiment)
        run_step("00_data_sensors/news_rss_scraper.py")
        run_step("00_data_sensors/news_api_client.py")
        run_step("00_data_sensors/news_email_scraper.py")
        
        # Phase 3: LLM Sentiment Scoring Engine (Ollama + VADER fallback)
        run_step("02_quant_engine/llm_sentiment_engine.py")
        
        # Phase 4: Export Feature Store
        run_step("02_quant_engine/ml_feature_store.py")
        
        # Phase 5: Train ML Models & Generate Metrics
        run_step("02_quant_engine/ml_trainer.py")
        
        # Phase 6: Signal Generation & Discord Dispatch
        logger.info("=" * 60)
        logger.info("🚀 STARTING: Signal Generation & Discord Dispatch")
        logger.info("=" * 60)
        
        from sqlite_portfolio import SQLitePortfolioDB
        sys.path.insert(0, str(_ROOT / "04_orchestrator_ai"))
        try:
            from discord_notifier import send_high_conviction_alert
        except ImportError:
            logger.warning("discord_notifier not found or could not be loaded. Skipping alerts.")
            send_high_conviction_alert = None
            
        sys.path.insert(0, str(_ROOT / "02_quant_engine"))
        try:
            from risk_engine import RiskEngine
        except ImportError:
            RiskEngine = None

        if send_high_conviction_alert:
            db = SQLitePortfolioDB()
            # Fetch APPROVED signals
            signals = db.fetch_signals_by_status(["APPROVED", "PENDING"])
            import datetime
            today_str = datetime.datetime.now().strftime("%Y-%m-%d")
            
            import os
            from dotenv import load_dotenv
            load_dotenv(_ROOT / ".env")
            webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
            
            # Filter high conviction signals for today
            dispatched = 0
            for sig in signals:
                if not sig["created_at"].startswith(today_str):
                    continue
                if float(sig.get("score", 0)) > 75:
                    # In a full implementation, we'd pull the actual current price and ATR from the timeseries DB
                    # Here we use defaults or parse from lineage_json if available
                    current_price = 100.0
                    atr_14 = 2.0
                    
                    import json
                    try:
                        if "lineage_json" in sig and sig["lineage_json"]:
                            lineage = json.loads(sig["lineage_json"])
                            current_price = float(lineage.get("Close", 100.0))
                            atr_14 = float(lineage.get("atr_14", 2.0))
                    except Exception:
                        pass
                        
                    atr_stop_loss = 0.0
                    if RiskEngine:
                        atr_stop_loss = RiskEngine.calculate_atr_stop(current_price, atr_14)
                        
                    signal_dict = {
                        "ticker": sig["ticker"],
                        "direction": sig["signal_type"],
                        "score": sig["score"],
                        "current_price": current_price,
                        "atr_stop_loss": atr_stop_loss,
                        "llm_reasoning": sig.get("reason", "No reason provided")
                    }
                    send_high_conviction_alert(signal_dict, webhook_url)
                    dispatched += 1
            
            logger.info("  [OUT] Dispatched %d high-conviction alerts to Discord.", dispatched)
            
        total_elapsed = time.time() - total_start
        logger.info("🎉 Master Pipeline completed successfully in %.1fs!", total_elapsed)
        logger.info("Dashboard is now ready to serve fresh metrics.")
        
    except Exception as e:
        logger.exception("Pipeline execution aborted due to an error: %s", e)
        sys.exit(1)

if __name__ == "__main__":
    main()
