"""Stochastic Models & Correlated Monte Carlo Engine for PEA Sniper Terminal.

Implements:
  1. Correlated Geometric Brownian Motion (GBM) via Cholesky decomposition.
  2. Merton Jump Diffusion Process (Poisson crash/rally jumps).
  3. Forward Portfolio Equity Trajectory Simulation.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class StochasticEngine:
    """Simulates correlated price trajectories and portfolio risk distributions."""

    @staticmethod
    def simulate_correlated_gbm(
        cov_matrix: np.ndarray,
        mu_vector: np.ndarray,
        initial_prices: np.ndarray,
        days: int = 252,
        simulations: int = 1000,
        dt: float = 1.0 / 252.0,
    ) -> np.ndarray:
        """Simulate correlated GBM asset prices.

        Returns:
            np.ndarray of shape (simulations, days + 1, num_assets).
        """
        n_assets = len(initial_prices)
        L = np.linalg.cholesky(cov_matrix)

        # Drift adjustment: mu - 0.5 * sigma^2
        var_diag = np.diag(cov_matrix)
        drift = (mu_vector - 0.5 * var_diag) * dt

        paths = np.zeros((simulations, days + 1, n_assets))
        paths[:, 0, :] = initial_prices

        for s in range(simulations):
            # Standard normal random shocks
            z = np.random.normal(0.0, 1.0, (days, n_assets))
            correlated_z = np.dot(z, L.T) * np.sqrt(dt)

            log_returns = drift + correlated_z
            cum_log_rets = np.vstack([np.zeros((1, n_assets)), np.cumsum(log_returns, axis=0)])
            paths[s, :, :] = initial_prices * np.exp(cum_log_rets)

        return paths

    @staticmethod
    def simulate_merton_jump_diffusion(
        s0: float,
        mu: float = 0.08,
        sigma: float = 0.20,
        lambda_j: float = 1.0,  # 1 jump per year
        mu_j: float = -0.05,    # Average jump is -5%
        sigma_j: float = 0.10,  # Jump volatility
        days: int = 252,
        simulations: int = 1000,
    ) -> np.ndarray:
        """Simulate asset price paths with Merton Jump Diffusion (Poisson jumps).

        Returns:
            np.ndarray of shape (simulations, days + 1).
        """
        dt = 1.0 / 252.0
        # Compensator for jump drift
        k = np.exp(mu_j + 0.5 * sigma_j**2) - 1.0
        drift = (mu - lambda_j * k - 0.5 * sigma**2) * dt

        paths = np.zeros((simulations, days + 1))
        paths[:, 0] = s0

        for s in range(simulations):
            # Diffusion shocks
            w = np.random.normal(0, np.sqrt(dt), days)
            # Poisson jump counts
            n_jumps = np.random.poisson(lambda_j * dt, days)

            # Jump sizes
            jumps = np.zeros(days)
            for t in range(days):
                if n_jumps[t] > 0:
                    jumps[t] = np.sum(np.random.normal(mu_j, sigma_j, n_jumps[t]))

            log_rets = drift + sigma * w + jumps
            paths[s, 1:] = s0 * np.exp(np.cumsum(log_rets))

        return paths


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    engine = StochasticEngine()
    paths = engine.simulate_merton_jump_diffusion(100.0, days=60, simulations=100)
    print(f"Merton Jump Diffusion simulated {paths.shape[0]} paths over 60 days. Final median price: {np.median(paths[:, -1]):.2f} €")
