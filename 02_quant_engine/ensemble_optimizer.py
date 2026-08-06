import json
import logging
from pathlib import Path

logger = logging.getLogger("ensemble_optimizer")

_ROOT = Path(__file__).resolve().parent.parent

class DynamicEnsemble:
    """Dynamic Ensemble Optimizer for weighting ML vs Heuristic models."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or (_ROOT / "database")

    def _read_ml_metrics(self, filename: str) -> dict:
        """Read metrics from the XGBoost JSON artifact safely."""
        filepath = self.db_path / filename
        if not filepath.exists():
            return {}
            
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("metrics", {})
        except Exception as exc:
            logger.warning(f"Could not parse ML metrics from {filename}: {exc}")
            return {}

    def get_optimized_weights(self) -> dict[str, float]:
        """
        Calculates dynamic weights for the ensemble.
        Uses XGBoost accuracy to balance ML vs Heuristics globally.
        If ML is highly accurate, it gets more weight. If it fails, heuristics take over.
        """
        tactical_metrics = self._read_ml_metrics("xgboost_model_tactical.json")
        structural_metrics = self._read_ml_metrics("xgboost_model_structural.json")

        # Get accuracy (default to 0.50 if not found)
        acc_tactical = float(tactical_metrics.get("accuracy", 0.50))
        acc_structural = float(structural_metrics.get("accuracy", 0.50))
        
        avg_acc = (acc_tactical + acc_structural) / 2.0

        # Base weights for heuristics
        # Standard: 0.30 Trend, 0.25 MR, 0.20 Breakout, 0.25 Context
        base_heuristic = {
            "trend": 0.30,
            "mean_reversion": 0.25,
            "breakout": 0.20,
            "context": 0.25
        }
        
        # Calculate ML multiplier based on accuracy vs 50% baseline
        # E.g., if accuracy is 60%, ml_weight is 0.60
        # If accuracy is 40%, ml_weight is 0.40
        # We cap it between 0.20 (min ML influence) and 0.80 (max ML influence)
        ml_weight = max(0.20, min(0.80, avg_acc))
        
        # The remaining weight goes to the heuristics
        heuristic_weight = 1.0 - ml_weight
        
        # Scale heuristic weights
        heuristic_scaled = {k: v * heuristic_weight for k, v in base_heuristic.items()}
        
        return {
            "ml_tactical_weight": ml_weight * 0.5,
            "ml_structural_weight": ml_weight * 0.5,
            "ml_total_weight": ml_weight,
            "heuristic_trend_weight": heuristic_scaled["trend"],
            "heuristic_mr_weight": heuristic_scaled["mean_reversion"],
            "heuristic_breakout_weight": heuristic_scaled["breakout"],
            "heuristic_context_weight": heuristic_scaled["context"],
            "avg_accuracy": avg_acc
        }
