"""Reinforcement Learning (PPO) Sizing Agent Scaffold for PEA Sniper Terminal.

WARNING / STATUS: DISABLED
==========================
TODO: Re-enable RL Sizer only when SizingEnv is connected to real historical trajectories
rather than synthetic noise. If enabled prematurely, the sizing agent fits to synthetic noise
and degrades live trade allocation.

Deterministic Half-Kelly + Inverse Volatility + Kinetic Brake remains the active production sizer.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def train_rl_sizing_agent() -> None:
    """RL Sizer training entrypoint (disabled in production)."""
    logger.warning(
        "RL Sizer training is intentionally DISABLED. "
        "TODO: Re-enable RL Sizer only when SizingEnv is connected to real historical trajectories."
    )
    raise NotImplementedError(
        "RL Sizer is disabled. Sizing is handled deterministically by 03_risk_portfolio/pea_position_sizer.py."
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("train_rl_sizer.py: Status is DISABLED by safety protocol.")
