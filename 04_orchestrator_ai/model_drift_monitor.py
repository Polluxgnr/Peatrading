import json
import logging
from pathlib import Path

logger = logging.getLogger("model_drift_monitor")

_ROOT = Path(__file__).resolve().parent.parent

def check_model_drift(db_path: Path | None = None) -> bool:
    """
    Evaluates if the current ML models are losing predictive power.
    Returns True if drift is detected (Accuracy < 0.55 on either model).
    """
    db_path = db_path or (_ROOT / "database")
    
    tactical_path = db_path / "xgboost_model_tactical.json"
    structural_path = db_path / "xgboost_model_structural.json"
    
    drift_detected = False
    
    for path, name in [(tactical_path, "Tactical"), (structural_path, "Structural")]:
        if not path.exists():
            logger.warning(f"{name} ML model artifact not found. Needs training.")
            drift_detected = True
            continue
            
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                acc = float(data.get("metrics", {}).get("accuracy", 0.0))
                
                if acc < 0.55:
                    logger.warning(f"🚨 DRIFT DETECTED: {name} model accuracy dropped to {acc:.2%}")
                    drift_detected = True
                else:
                    logger.info(f"✅ {name} model healthy. Accuracy: {acc:.2%}")
        except Exception as e:
            logger.error(f"Failed to read metrics for {name}: {e}")
            drift_detected = True
            
    return drift_detected

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(_ROOT / "01_memory_core"))
    from logging_setup import get_logger
    logger = get_logger("model_drift_monitor")
    
    is_drifting = check_model_drift()
    if is_drifting:
        logger.warning("Pipeline requires retraining due to model drift.")
        sys.exit(1)
    else:
        logger.info("All models are performing optimally.")
        sys.exit(0)
