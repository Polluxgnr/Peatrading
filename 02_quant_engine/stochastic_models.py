"""Stochastic portfolio models (vectorized numpy implementations)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def run_correlated_monte_carlo(
    weights: np.ndarray,
    cov_matrix: pd.DataFrame,
    expected_returns: pd.Series,
    initial_portfolio_value: float,
    days: int = 252,
    simulations: int = 2000,
) -> pd.DataFrame:
    """Run correlated GBM Monte Carlo and return percentile fan paths.

    Args:
        weights: Portfolio weights vector (N,).
        cov_matrix: Daily return covariance matrix (N x N).
        expected_returns: Expected daily returns indexed by ticker (N,).
        initial_portfolio_value: Starting portfolio value.
        days: Trading-day horizon.
        simulations: Number of Monte Carlo simulations.

    Returns:
        DataFrame with columns:
        ``day, p05, p25, p50, p75, p95``.
    """
    w = np.asarray(weights, dtype=float).reshape(-1)
    if w.size == 0:
        return pd.DataFrame(columns=["day", "p05", "p25", "p50", "p75", "p95"])
    if float(w.sum()) <= 0:
        w = np.ones_like(w) / float(w.size)
    else:
        w = w / float(w.sum())

    tickers = list(cov_matrix.columns)
    sigma = cov_matrix.loc[tickers, tickers].to_numpy(dtype=float)
    mu = expected_returns.reindex(tickers).fillna(0.0).to_numpy(dtype=float)

    # Stabilize covariance for Cholesky.
    eps = 1e-10
    sigma = (sigma + sigma.T) / 2.0 + np.eye(sigma.shape[0]) * eps
    try:
        chol = np.linalg.cholesky(sigma)
    except np.linalg.LinAlgError:
        evals, evecs = np.linalg.eigh(sigma)
        evals = np.clip(evals, a_min=eps, a_max=None)
        sigma_psd = (evecs * evals) @ evecs.T
        chol = np.linalg.cholesky(sigma_psd + np.eye(sigma.shape[0]) * eps)

    n_assets = w.size
    T = int(max(1, days))
    M = int(max(100, simulations))
    dt = 1.0

    # Correlated normal shocks: (M, T, N)
    z = np.random.normal(loc=0.0, scale=1.0, size=(M, T, n_assets))
    shocks = np.einsum("mtn,nk->mtk", z, chol)

    drift = (mu - 0.5 * np.diag(sigma)) * dt
    step_returns = np.exp(drift.reshape(1, 1, n_assets) + shocks) - 1.0

    # Portfolio return each step: (M, T)
    port_r = np.einsum("mtn,n->mt", step_returns, w)
    wealth = float(initial_portfolio_value) * np.cumprod(1.0 + port_r, axis=1)

    # Include day 0
    wealth = np.concatenate(
        [np.full((M, 1), float(initial_portfolio_value)), wealth],
        axis=1,
    )
    pct = np.percentile(wealth, q=[5, 25, 50, 75, 95], axis=0)
    days_idx = np.arange(0, T + 1, dtype=int)
    return pd.DataFrame(
        {
            "day": days_idx,
            "p05": pct[0],
            "p25": pct[1],
            "p50": pct[2],
            "p75": pct[3],
            "p95": pct[4],
        }
    )

