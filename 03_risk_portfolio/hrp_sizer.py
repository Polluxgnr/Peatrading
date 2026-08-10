"""Hierarchical Risk Parity (HRP) Portfolio Allocation for PEA Sniper Terminal.

Implements Marcos López de Prado's HRP algorithm:
  1. Tree Clustering: Correlation -> Distance Matrix -> Single Linkage.
  2. Quasi-Diagonalization: Reorders the covariance matrix according to the dendrogram.
  3. Recursive Bisection: Inverse-variance allocation across clustered subsets.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage
from scipy.spatial.distance import squareform

logger = logging.getLogger(__name__)


class HRPSizer:
    """Calculates Hierarchical Risk Parity portfolio weights from historical returns."""

    @staticmethod
    def get_quasi_diag(link: np.ndarray) -> List[int]:
        """Sort clustered items by hierarchical tree order (Quasi-Diagonalization)."""
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
            sort_ix = pd.concat([sort_ix, df0]).sort_index()
            sort_ix.index = range(sort_ix.shape[0])

        return sort_ix.tolist()

    @staticmethod
    def get_cluster_variance(cov: np.ndarray, c_items: List[int]) -> float:
        """Compute the variance of a sub-cluster using inverse-variance weights."""
        sub_cov = cov[np.ix_(c_items, c_items)]
        ivp = 1.0 / np.diag(sub_cov)
        ivp /= ivp.sum()
        w = ivp.reshape(-1, 1)
        c_var = np.dot(np.dot(w.T, sub_cov), w)[0, 0]
        return float(c_var)

    def get_rec_bisection(self, cov: np.ndarray, sort_ix: List[int]) -> pd.Series:
        """Recursive bisection allocation down the quasi-diagonalized tree."""
        w = pd.Series(1.0, index=sort_ix)
        c_items = [sort_ix]

        while len(c_items) > 0:
            c_items = [
                i[j:k]
                for i in c_items
                for j, k in ((0, int(len(i) / 2)), (int(len(i) / 2), len(i)))
                if len(i) > 1
            ]
            for i in range(0, len(c_items), 2):
                c_items0 = c_items[i]
                c_items1 = c_items[i + 1]
                c_var0 = self.get_cluster_variance(cov, c_items0)
                c_var1 = self.get_cluster_variance(cov, c_items1)
                alpha = 1.0 - c_var0 / (c_var0 + c_var1)
                w[c_items0] *= alpha
                w[c_items1] *= 1.0 - alpha

        return w

    def calculate_hrp_weights(self, returns_df: pd.DataFrame) -> Dict[str, float]:
        """Compute HRP weights given a DataFrame of asset returns.

        Args:
            returns_df: DataFrame where each column is a ticker's daily return series.

        Returns:
            dict: {ticker: weight_float} summing to 1.0.
        """
        if returns_df is None or returns_df.empty or returns_df.shape[1] < 2:
            cols = list(returns_df.columns) if returns_df is not None else []
            if len(cols) == 1:
                return {cols[0]: 1.0}
            return {}

        clean_rets = returns_df.dropna().copy()
        if len(clean_rets) < 10:
            # Fallback to equal weight if history is too short
            n = returns_df.shape[1]
            return {c: 1.0 / n for c in returns_df.columns}

        cov = clean_rets.cov().values
        corr = clean_rets.corr().values
        tickers = list(clean_rets.columns)

        # 1. Distance matrix: D = sqrt(0.5 * (1 - rho))
        dist = np.sqrt(np.clip(0.5 * (1.0 - corr), 0, 1.0))
        np.fill_diagonal(dist, 0.0)

        # Convert to condensed form for scipy linkage
        condensed_dist = squareform(dist, checks=False)
        link = linkage(condensed_dist, method="single")

        # 2. Quasi-Diagonalization
        sort_ix = self.get_quasi_diag(link)

        # 3. Recursive Bisection
        weights = self.get_rec_bisection(cov, sort_ix)
        weights = weights.sort_index()

        hrp_dict = {tickers[i]: float(weights.iloc[i]) for i in range(len(tickers))}
        return hrp_dict


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    hrp = HRPSizer()
    np.random.seed(42)
    fake_rets = pd.DataFrame(
        np.random.normal(0.0005, 0.015, (252, 4)),
        columns=["MC.PA", "OR.PA", "AI.PA", "CW8.PA"],
    )
    res = hrp.calculate_hrp_weights(fake_rets)
    print("HRP Allocation Weights:", {k: f"{v*100:.2f}%" for k, v in res.items()})
