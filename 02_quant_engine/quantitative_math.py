"""Academic quantitative-math utilities for portfolio analytics.

Pure numpy/pandas implementations (vectorized) with no DB side-effects.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import scipy.stats as stats


def _clean_returns(returns: pd.Series) -> pd.Series:
    """Return finite float returns only."""
    if returns is None:
        return pd.Series(dtype=float)
    ser = pd.to_numeric(returns, errors="coerce").replace([np.inf, -np.inf], np.nan)
    return ser.dropna()


def calculate_historical_var(
    returns: pd.Series, confidence_level: float = 0.95
) -> float:
    """Cornish-Fisher Value at Risk (VaR) as a positive loss number.

    Args:
        returns: Series of arithmetic returns (e.g. daily pct returns in decimal).
        confidence_level: Tail confidence (default 95%).

    Returns:
        Positive loss estimate (e.g. 0.018 means -1.8% one-period VaR), or 0.0
        when data is insufficient.
    """
    r = _clean_returns(returns)
    if r.empty:
        return 0.0
    alpha = float(1.0 - confidence_level)
    alpha = min(max(alpha, 1e-6), 1.0 - 1e-6)
    
    mu = float(np.mean(r))
    sigma = float(np.std(r, ddof=1))
    
    if sigma == 0:
        q_hist = float(np.quantile(r.to_numpy(dtype=float), alpha))
        return float(max(0.0, -q_hist))
        
    z = float(stats.norm.ppf(alpha))
    
    s = float(stats.skew(r, nan_policy='omit'))
    k = float(stats.kurtosis(r, nan_policy='omit'))
    if np.isnan(s): s = 0.0
    if np.isnan(k): k = 0.0
    s = np.clip(s, -5.0, 5.0)
    k = np.clip(k, -10.0, 10.0)
    
    z_cf = z + (z**2 - 1) * s / 6.0 + (z**3 - 3*z) * k / 24.0 - (2 * z**3 - 5 * z) * (s**2) / 36.0
    z_cf = np.clip(z_cf, -10.0, 10.0)
    
    q_cf = mu + z_cf * sigma
    return float(max(0.0, -q_cf))


def calculate_cvar(returns: pd.Series, confidence_level: float = 0.95) -> float:
    """Conditional VaR (Expected Shortfall) as positive tail loss using Cornish-Fisher VaR threshold.

    Args:
        returns: Series of arithmetic returns.
        confidence_level: Tail confidence (default 95%).

    Returns:
        Positive expected loss in the alpha tail, or 0.0 when unavailable.
    """
    r = _clean_returns(returns)
    if r.empty:
        return 0.0
    alpha = float(1.0 - confidence_level)
    alpha = min(max(alpha, 1e-6), 1.0 - 1e-6)
    
    mu = float(np.mean(r))
    sigma = float(np.std(r, ddof=1))
    
    if sigma == 0:
        q = float(np.quantile(r.to_numpy(dtype=float), alpha))
        tail = r[r <= q]
        if tail.empty:
            return float(max(0.0, -q))
        return float(max(0.0, -float(tail.mean())))
        
    z = float(stats.norm.ppf(alpha))
    s = float(stats.skew(r, nan_policy='omit'))
    k = float(stats.kurtosis(r, nan_policy='omit'))
    if np.isnan(s): s = 0.0
    if np.isnan(k): k = 0.0
    s = np.clip(s, -5.0, 5.0)
    k = np.clip(k, -10.0, 10.0)
    
    z_cf = z + (z**2 - 1) * s / 6.0 + (z**3 - 3*z) * k / 24.0 - (2 * z**3 - 5 * z) * (s**2) / 36.0
    z_cf = np.clip(z_cf, -10.0, 10.0)
    
    q_cf = mu + z_cf * sigma
    tail = r[r <= q_cf]
    
    if tail.empty:
        return float(max(0.0, -q_cf))
    return float(max(0.0, -float(tail.mean())))


def calculate_z_score(series: pd.Series) -> pd.Series:
    """50-period rolling Z-score: ``(x - mean) / std``.

    Args:
        series: Input numeric series.

    Returns:
        Series aligned to input index. Non-computable points are NaN.
    """
    ser = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)
    roll_mean = ser.rolling(window=50, min_periods=50).mean()
    roll_std = ser.rolling(window=50, min_periods=50).std(ddof=0)
    z = (ser - roll_mean) / roll_std.replace(0.0, np.nan)
    return z.replace([np.inf, -np.inf], np.nan)


def calculate_portfolio_variance(weights: np.ndarray, cov_matrix: pd.DataFrame) -> float:
    """Portfolio variance ``w.T @ Sigma @ w``.

    Args:
        weights: 1D numpy vector of portfolio weights.
        cov_matrix: Covariance matrix as pandas DataFrame.

    Returns:
        Non-negative scalar portfolio variance.
    """
    w = np.asarray(weights, dtype=float).reshape(-1)
    sigma = np.asarray(cov_matrix, dtype=float)
    if sigma.ndim != 2 or sigma.shape[0] != sigma.shape[1] or sigma.shape[0] != w.shape[0]:
        raise ValueError("weights and covariance matrix dimensions are inconsistent")
    var = float(w.T @ sigma @ w)
    return float(max(0.0, var))


def get_weights_ffd(d: float, thres: float = 1e-5) -> np.ndarray:
    """Calculate the weights for Fast Fractional Differencing (FFD)."""
    w, k = [1.], 1
    while True:
        w_ = -w[-1] / k * (d - k + 1)
        if abs(w_) < thres:
            break
        w.append(w_)
        k += 1
    return np.array(w[::-1]).reshape(-1, 1)


def frac_diff_ffd(series: pd.Series, d: float = 0.4, thres: float = 1e-5) -> pd.Series:
    """Apply Fractional Differentiation to a time series to achieve stationarity while retaining memory.
    
    Args:
        series: Pandas Series of prices or data.
        d: Fractional differentiation parameter (0 < d < 1).
        thres: Threshold to drop insignificant weights.
        
    Returns:
        Pandas Series of fractionally differentiated data.
    """
    w = get_weights_ffd(d, thres)
    width = len(w) - 1
    df = pd.Series(np.nan, index=series.index, dtype=float)
    if len(series) <= width:
        return df
    
    # Vectorized sliding window dot product
    for i in range(width, len(series)):
        val = np.dot(w.T, series.iloc[i - width:i + 1].values)
        df.iloc[i] = val[0]
        
    return df


def calculate_annualized_volatility(returns: pd.Series, periods_per_year: int = 252) -> float:
    """Annualized volatility from return series (helper for refactoring)."""
    r = _clean_returns(returns)
    if r.empty:
        return 0.0
    std_ewm = r.ewm(span=20).std().dropna()
    if std_ewm.empty:
        return 0.0
    return float(std_ewm.iloc[-1] * np.sqrt(float(periods_per_year)))


def detect_cusum_downward_break(returns: pd.Series, threshold: float = 3.0, drift: float = 0.5) -> bool:
    """
    Detects a structural downward break in returns using the CUSUM algorithm.
    
    Calculates the standardized cumulative sum of negative deviations from the mean.
    If the CUSUM drops below -threshold, it indicates a bearish breakdown.
    
    Args:
        returns: Pandas Series of daily returns.
        threshold: The negative threshold to trigger a structural break alert (e.g. 3.0).
        drift: Tolerance parameter to ignore minor deviations (in standard deviations).
        
    Returns:
        bool: True if a structural downward break is detected, False otherwise.
    """
    r = _clean_returns(returns)
    if r.empty or len(r) < 5:
        return False
        
    # Standardize returns
    mean_ret = r.mean()
    std_ret = r.std(ddof=1)
    
    if std_ret == 0 or pd.isna(std_ret):
        return False
        
    z_scores = (r - mean_ret) / std_ret
    
    # Calculate negative CUSUM (S_low)
    s_low = 0.0
    for z in z_scores:
        s_low = min(0.0, s_low + z + drift)
        if s_low <= -threshold:
            return True
            
    return False

def calculate_shrunk_covariance(returns_df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculates a Ledoit-Wolf shrunk covariance matrix.
    Uses sklearn.covariance.LedoitWolf for robust correlation estimation 
    even with a small sample of highly collinear assets.
    """
    if returns_df.empty:
        return pd.DataFrame()
        
    df = returns_df.dropna(how='all', axis=1).fillna(0.0)
    if df.shape[1] < 2:
        return df.cov()
        
    from sklearn.covariance import LedoitWolf
    try:
        lw = LedoitWolf().fit(df.values)
        return pd.DataFrame(lw.covariance_, index=df.columns, columns=df.columns)
    except Exception:
        return df.cov()

