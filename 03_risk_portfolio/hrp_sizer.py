"""Hierarchical Risk Parity (HRP) module.

Implements Marco Lopez de Prado's HRP algorithm to cluster correlated
assets and allocate weights (risk ceilings) across the portfolio,
ensuring risk parity without requiring matrix inversion.
"""
import logging
from typing import Dict, List
import pandas as pd
import numpy as np
try:
    import scipy.cluster.hierarchy as sch
    from scipy.spatial.distance import squareform
except ImportError:
    sch = None

try:
    from sklearn.covariance import LedoitWolf
except ImportError:
    LedoitWolf = None

logger = logging.getLogger(__name__)

class HRPSizer:
    def __init__(self, tsdb):
        self.tsdb = tsdb
        
    def _get_quasi_diag(self, link):
        """Sort clustered items by distance."""
        link = link.astype(int)
        sort_ix = pd.Series([link[-1, 0], link[-1, 1]])
        num_items = link[-1, 3]
        while sort_ix.max() >= num_items:
            sort_ix.index = range(0, sort_ix.shape[0] * 2, 2)
            df0 = sort_ix[sort_ix >= num_items]
            i = df0.index
            j = df0.values - num_items
            sort_ix[i] = link[j, 0]
            df0 = pd.Series(link[j, 1], index=i + 1)
            sort_ix = pd.concat([sort_ix, df0])
            sort_ix = sort_ix.sort_index()
            sort_ix.index = range(sort_ix.shape[0])
        return sort_ix.tolist()
        
    def _get_rec_bipart(self, cov, sort_ix):
        """Compute HRP allocations via recursive bisection."""
        w = pd.Series(1.0, index=sort_ix)
        c_items = [sort_ix]
        while len(c_items) > 0:
            c_items = [i[j:k] for i in c_items for j, k in ((0, len(i) // 2), (len(i) // 2, len(i))) if len(i) > 1]
            for i in range(0, len(c_items), 2):
                c_items0 = c_items[i] # left cluster
                c_items1 = c_items[i + 1] # right cluster
                c_var0 = self._get_cluster_var(cov, c_items0)
                c_var1 = self._get_cluster_var(cov, c_items1)
                alpha = 1 - c_var0 / (c_var0 + c_var1)
                w[c_items0] *= alpha
                w[c_items1] *= 1 - alpha
        return w
        
    def _get_cluster_var(self, cov, c_items):
        """Calculate variance of a cluster."""
        cov_ = cov.iloc[c_items, c_items]
        ivp = 1.0 / np.diag(cov_)
        ivp /= ivp.sum()
        w_ = ivp.reshape(-1, 1)
        c_var = np.dot(np.dot(w_.T, cov_), w_)[0, 0]
        return c_var
        
    def get_hrp_weights(self, returns_df: pd.DataFrame) -> Dict[str, float]:
        """Calculate HRP weights from a DataFrame of returns.
        
        Args:
            returns_df: DataFrame where index is date, columns are tickers.
            
        Returns:
            Dict mapping ticker to weight [0.0, 1.0].
        """
        if sch is None:
            logger.warning("scipy not installed. Falling back to equal weights.")
            return {c: 1.0/len(returns_df.columns) for c in returns_df.columns}
            
        if returns_df.empty or len(returns_df.columns) < 2:
            return {c: 1.0/len(returns_df.columns) for c in returns_df.columns}
            
        from quantitative_math import calculate_shrunk_covariance
        cov = calculate_shrunk_covariance(returns_df)
        corr = returns_df.corr(method="pearson").fillna(0.0)
        
        # Distance matrix
        # d[i, j] = sqrt(0.5 * (1 - corr[i,j]))
        dist = np.sqrt(np.clip((1.0 - corr) / 2.0, 0, 1))
        # Ensure exact symmetry and zeros on diagonal
        np.fill_diagonal(dist.values, 0)
        dist = (dist + dist.T) / 2.0
        
        # Condensed distance matrix
        condensed_dist = squareform(dist.values, checks=False)
        
        # Clustering
        link = sch.linkage(condensed_dist, 'single')
        sort_ix = self._get_quasi_diag(link)
        sort_ix = corr.index[sort_ix].tolist()
        
        hrp_weights = self._get_rec_bipart(cov, sort_ix)
        return hrp_weights.to_dict()
        
    def compute_max_allocations(self, tickers: List[str], max_budget: float) -> Dict[str, float]:
        """Compute the max budget allocation per ticker using HRP.
        
        Args:
            tickers: List of tickers currently being evaluated.
            max_budget: The total available budget to allocate.
            
        Returns:
            Dictionary of ticker -> maximum allowed EUR allocation.
        """
        # Fetch returns for the universe over last 252 days
        series_dict = {}
        for ticker in tickers:
            try:
                df = self.tsdb.get_historical_prices(ticker, days=252)
                if df is not None and not df.empty and "Close" in df.columns:
                    ret = df["Close"].pct_change().dropna()
                    series_dict[ticker] = ret
            except Exception:
                pass
                
        if not series_dict:
            return {}
            
        returns_df = pd.DataFrame(series_dict).fillna(0)
        
        weights = self.get_hrp_weights(returns_df)
        
        # Scale to max_budget
        allocations = {t: w * max_budget for t, w in weights.items()}
        return allocations
