"""Quantitative Risk Math for PEA Sniper Terminal.

Computes:
  - Historical Value-at-Risk (VaR 95% & 99%)
  - Parametric Gaussian VaR
  - Cornish-Fisher expansion VaR (accounting for skewness & excess kurtosis)
  - Conditional Value-at-Risk (CVaR / Expected Shortfall)
  - Tail Risk & Maximum Loss Estimators
"""

from __future__ import annotations

import logging
from typing import Dict, Sequence, Union

import numpy as np
import pandas as pd
from scipy.stats import kurtosis, norm, skew

logger = logging.getLogger(__name__)


def calculate_historical_var(
    returns: Union[pd.Series, np.ndarray, Sequence[float]],
    confidence_level: float = 0.95,
) -> float:
    """Calculate Historical Value-at-Risk at specified confidence level (positive float)."""
    rets = np.asarray(returns, dtype=float)
    rets = rets[~np.isnan(rets)]
    if len(rets) < 5:
        return 0.0
    alpha = (1.0 - confidence_level) * 100.0
    var = -float(np.percentile(rets, alpha))
    return max(0.0, var)


def calculate_cvar(
    returns: Union[pd.Series, np.ndarray, Sequence[float]],
    confidence_level: float = 0.95,
) -> float:
    """Calculate Conditional Value-at-Risk (CVaR / Expected Shortfall)."""
    rets = np.asarray(returns, dtype=float)
    rets = rets[~np.isnan(rets)]
    if len(rets) < 5:
        return 0.0
    alpha = (1.0 - confidence_level) * 100.0
    cutoff = np.percentile(rets, alpha)
    tail = rets[rets <= cutoff]
    if len(tail) == 0:
        return max(0.0, -cutoff)
    return max(0.0, -float(np.mean(tail)))


def calculate_cornish_fisher_var(
    returns: Union[pd.Series, np.ndarray, Sequence[float]],
    confidence_level: float = 0.95,
) -> float:
    """Calculate Cornish-Fisher modified VaR adjusting for non-normal skew & kurtosis."""
    rets = np.asarray(returns, dtype=float)
    rets = rets[~np.isnan(rets)]
    if len(rets) < 10:
        return calculate_historical_var(returns, confidence_level)

    mu = float(np.mean(rets))
    sigma = float(np.std(rets, ddof=1))
    if sigma <= 1e-8:
        return 0.0

    s = float(skew(rets))
    k = float(kurtosis(rets))  # excess kurtosis

    z = norm.ppf(1.0 - confidence_level)
    # Cornish-Fisher expansion quantile:
    # z_cf = z + (z^2 - 1)*S/6 + (z^3 - 3z)*K/24 - (2z^3 - 5z)*S^2/36
    z_cf = (
        z
        + (z**2 - 1.0) * s / 6.0
        + (z**3 - 3.0 * z) * k / 24.0
        - (2.0 * z**3 - 5.0 * z) * (s**2) / 36.0
    )

    var_cf = -(mu + z_cf * sigma)
    return max(0.0, float(var_cf))


def compute_comprehensive_risk_profile(
    returns: Union[pd.Series, np.ndarray, Sequence[float]]
) -> Dict[str, float]:
    """Compute all quantitative risk metrics for a return series."""
    rets = np.asarray(returns, dtype=float)
    rets = rets[~np.isnan(rets)]
    if len(rets) < 5:
        return {
            "var_95_hist": 0.0,
            "var_99_hist": 0.0,
            "cvar_95": 0.0,
            "cvar_99": 0.0,
            "var_95_cf": 0.0,
            "skewness": 0.0,
            "excess_kurtosis": 0.0,
        }

    return {
        "var_95_hist": round(calculate_historical_var(rets, 0.95), 4),
        "var_99_hist": round(calculate_historical_var(rets, 0.99), 4),
        "cvar_95": round(calculate_cvar(rets, 0.95), 4),
        "cvar_99": round(calculate_cvar(rets, 0.99), 4),
        "var_95_cf": round(calculate_cornish_fisher_var(rets, 0.95), 4),
        "skewness": round(float(skew(rets)), 3),
        "excess_kurtosis": round(float(kurtosis(rets)), 3),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    np.random.seed(42)
    sample_rets = np.random.standard_t(df=5, size=500) * 0.015
    profile = compute_comprehensive_risk_profile(sample_rets)
    print("Quantitative Risk Profile:", profile)
