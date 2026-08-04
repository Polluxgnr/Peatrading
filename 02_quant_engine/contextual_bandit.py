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
        
        # Default tracking parameters per regime
        return {
            "BULL": {
                "trend": {"rewards": 30.0, "counts": 100},
                "mean_reversion": {"rewards": 25.0, "counts": 100},
                "breakout": {"rewards": 20.0, "counts": 100},
                "context": {"rewards": 25.0, "counts": 100},
            },
            "BEAR": {
                "trend": {"rewards": 10.0, "counts": 100},
                "mean_reversion": {"rewards": 30.0, "counts": 100},
                "breakout": {"rewards": 10.0, "counts": 100},
                "context": {"rewards": 30.0, "counts": 100},
            },
            "VOLATILE": {
                "trend": {"rewards": 15.0, "counts": 100},
                "mean_reversion": {"rewards": 35.0, "counts": 100},
                "breakout": {"rewards": 25.0, "counts": 100},
                "context": {"rewards": 25.0, "counts": 100},
            }
        }

    def save_state(self):
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save bandit state: {e}")

    def get_weights(self, regime: str = "BULL") -> dict[str, float]:
        """Calculate UCB weights and normalize to sum to 1.0"""
        regime_state = self.state.get(regime, self.state.get("BULL", {}))
        if not regime_state:
            return {arm: 1.0 / len(self.arms) for arm in self.arms}

        total_counts = sum(arm_data["counts"] for arm_data in regime_state.values())
        if total_counts == 0:
            return {arm: 1.0 / len(self.arms) for arm in self.arms}
            
        ucb_values = {}
        for arm in self.arms:
            arm_data = regime_state.get(arm, {"rewards": 0.0, "counts": 0})
            counts = arm_data["counts"]
            if counts == 0:
                ucb_values[arm] = 1000.0 # High value to ensure exploration
            else:
                mean_reward = arm_data["rewards"] / counts
                exploration = self.c * np.sqrt(np.log(total_counts) / counts)
                ucb_values[arm] = max(0, mean_reward + exploration)
                
        total_ucb = sum(ucb_values.values())
        if total_ucb > 0:
            return {arm: val / total_ucb for arm, val in ucb_values.items()}
        return {arm: 1.0 / len(self.arms) for arm in self.arms}

    def update_reward(self, regime: str, arm: str, reward: float):
        """Update counts and rewards for the chosen arm."""
        if regime not in self.state:
            self.state[regime] = {a: {"rewards": 0.0, "counts": 0} for a in self.arms}
            
        if arm not in self.state[regime]:
            self.state[regime][arm] = {"rewards": 0.0, "counts": 0}
            
        self.state[regime][arm]["counts"] += 1
        self.state[regime][arm]["rewards"] += reward
        self.save_state()
