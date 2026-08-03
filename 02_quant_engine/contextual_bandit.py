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

class UCBBandit:
    def __init__(self, storage_path: Path | None = None, c: float = 2.0):
        self.storage_path = storage_path or Path(__file__).resolve().parent.parent / "database" / "bandit_state.json"
        self.arms = ["trend", "mean_reversion", "breakout", "context"]
        self.c = c
        self.state = self._load_state()

    def _load_state(self) -> dict:
        if self.storage_path.exists():
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                logger.warning("Failed to load bandit state, using default.")
        
        # Default tracking parameters
        return {
            "trend": {"rewards": 30.0, "counts": 100},
            "mean_reversion": {"rewards": 25.0, "counts": 100},
            "breakout": {"rewards": 20.0, "counts": 100},
            "context": {"rewards": 25.0, "counts": 100},
        }

    def save_state(self):
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save bandit state: {e}")

    def get_weights(self) -> dict[str, float]:
        """Calculate UCB weights and normalize to sum to 1.0"""
        total_counts = sum(arm_data["counts"] for arm_data in self.state.values())
        if total_counts == 0:
            return {arm: 1.0 / len(self.arms) for arm in self.arms}
            
        ucb_values = {}
        for arm in self.arms:
            counts = self.state[arm]["counts"]
            if counts == 0:
                ucb_values[arm] = 1000.0 # High value to ensure exploration
            else:
                mean_reward = self.state[arm]["rewards"] / counts
                exploration = self.c * np.sqrt(np.log(total_counts) / counts)
                ucb_values[arm] = max(0, mean_reward + exploration)
                
        total_ucb = sum(ucb_values.values())
        if total_ucb > 0:
            return {arm: val / total_ucb for arm, val in ucb_values.items()}
        return {arm: 1.0 / len(self.arms) for arm in self.arms}

    def update_reward(self, arm: str, reward: float):
        """Update counts and rewards for the chosen arm."""
        if arm not in self.state:
            return
            
        self.state[arm]["counts"] += 1
        self.state[arm]["rewards"] += reward
        self.save_state()
