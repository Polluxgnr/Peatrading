"""Contextual Bandits for Dynamic Sub-model Weighting.

Replaces fixed weights in the technical scorer with dynamic UCB / Thompson Sampling
weights. The bandit learns which sub-model (Trend, MR, Breakout, Context) performs 
best in the current market environment.
"""
import json
import logging
from pathlib import Path
import numpy as np

logger = logging.getLogger(__name__)

class ThompsonSamplingBandit:
    def __init__(self, storage_path: Path | None = None):
        self.storage_path = storage_path or Path(__file__).resolve().parent.parent / "database" / "bandit_state.json"
        self.arms = ["trend", "mean_reversion", "breakout", "context"]
        self.state = self._load_state()

    def _load_state(self) -> dict:
        if self.storage_path.exists():
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                logger.warning("Failed to load bandit state, using default.")
        
        # Default Beta(alpha, beta) parameters for each arm
        return {
            "trend": {"alpha": 30.0, "beta": 70.0},
            "mean_reversion": {"alpha": 25.0, "beta": 75.0},
            "breakout": {"alpha": 20.0, "beta": 80.0},
            "context": {"alpha": 25.0, "beta": 75.0},
        }

    def save_state(self):
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=4)
        except Exception as exc:
            logger.error(f"Failed to save bandit state: {exc}")

    def get_weights(self) -> dict:
        """Sample from Beta distributions to get current Thompson weights.
        
        Returns:
            Dict mapping arm to its normalized weight [0.0, 1.0].
        """
        samples = {}
        for arm in self.arms:
            a = self.state[arm]["alpha"]
            b = self.state[arm]["beta"]
            # Sample from the Beta distribution representing our belief about this arm's success rate
            samples[arm] = np.random.beta(a, b)
            
        # Normalize weights so they sum to 1.0
        total = sum(samples.values())
        if total > 0:
            return {k: v / total for k, v in samples.items()}
        return {k: 1.0 / len(self.arms) for k in self.arms}
        
    def update_reward(self, arm: str, success: bool):
        """Update the belief distribution for a specific arm based on outcome.
        
        Args:
            arm: The sub-model name (e.g., 'trend').
            success: True if it generated a profitable signal, False otherwise.
        """
        if arm not in self.state:
            return
            
        if success:
            self.state[arm]["alpha"] += 1.0
        else:
            self.state[arm]["beta"] += 1.0
            
        self.save_state()
