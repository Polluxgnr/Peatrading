import sys
import time
import subprocess
from pathlib import Path
from core.logging_setup import get_logger

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
        
        # Phase 3: NLP Sentiment Scoring Engine
        run_step("02_quant_engine/nlp_sentiment_engine.py")
        
        # Phase 4: Export Feature Store
        run_step("02_quant_engine/ml_feature_store.py")
        
        # Phase 5: Train ML Models & Generate Metrics
        run_step("02_quant_engine/ml_trainer.py")
        
        total_elapsed = time.time() - total_start
        logger.info("🎉 Master Pipeline completed successfully in %.1fs!", total_elapsed)
        logger.info("Dashboard is now ready to serve fresh metrics.")
        
    except Exception as e:
        logger.exception("Pipeline execution aborted due to an error: %s", e)
        sys.exit(1)

if __name__ == "__main__":
    main()
