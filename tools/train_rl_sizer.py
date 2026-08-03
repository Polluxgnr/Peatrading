"""Train PPO Reinforcement Learning model for Position Sizing.

This script creates a mock Gym environment where the agent learns
to output an optimal Kelly Fraction based on (signal_score, volatility)
in order to maximize Sharpe ratio (reward).
"""
import sys
import logging
from pathlib import Path
import numpy as np

try:
    import gymnasium as gym
    from stable_baselines3 import PPO
except ImportError:
    print("Please install stable-baselines3 and gymnasium.")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_MODEL_PATH = _ROOT / "database" / "rl_sizer_model.zip"

class SizingEnv(gym.Env):
    """Custom Environment for Sizing."""
    def __init__(self):
        super(SizingEnv, self).__init__()
        # Action space: [-1, 1] mapped to [0, 1] in inference
        self.action_space = gym.spaces.Box(low=-1.0, high=1.0, shape=(1,), dtype=np.float32)
        # Observation space: [signal_score/100, volatility]
        self.observation_space = gym.spaces.Box(low=0.0, high=2.0, shape=(2,), dtype=np.float32)
        
        self.current_step = 0
        self.max_steps = 1000
        
    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.current_step = 0
        return self._next_obs(), {}
        
    def _next_obs(self):
        score = np.random.uniform(0.65, 1.0)
        vol = np.random.uniform(0.10, 0.40)
        return np.array([score, vol], dtype=np.float32)
        
    def step(self, action):
        self.current_step += 1
        kelly = np.clip((action[0] + 1.0) / 2.0, 0.0, 1.0)
        
        # Reward logic: high kelly on high score + low vol is good.
        # High kelly on high vol is dangerous (drawdown penalty).
        obs = self._next_obs()
        score, vol = obs
        
        expected_return = (score - 0.5) * 2.0  # scaled
        risk_penalty = vol * kelly * 2.0
        reward = expected_return * kelly - risk_penalty
        
        done = self.current_step >= self.max_steps
        truncated = False
        
        return obs, float(reward), done, truncated, {}

def train_agent():
    logger.info("Initializing PPO Sizing Agent...")
    env = SizingEnv()
    
    # In production, we'd train on thousands of historical trades.
    model = PPO("MlpPolicy", env, verbose=1)
    
    logger.info("Training PPO agent...")
    model.learn(total_timesteps=5000)
    
    _MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(_MODEL_PATH))
    logger.info("Model saved to %s", _MODEL_PATH)

if __name__ == "__main__":
    train_agent()
