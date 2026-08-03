"""Cross-Sectional Momentum Engine for PEA Pollux.

Ranks the stock universe to enforce relative sector rotation.
"""

import pandas as pd
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)

class CrossSectionalScorer:
    """Computes cross-sectional momentum percentiles across a universe."""
    
    def __init__(self, timeseries_db):
        self.tsdb = timeseries_db
        
    def rank_universe(self, tickers: List[str], days: int = 126) -> Dict[str, float]:
        """Rank tickers by their return over the last `days` (default 126 ~ 6 months).
        
        Args:
            tickers: List of ticker symbols to rank.
            days: Lookback period in trading days (default 126 ~ 6 months).
            
        Returns:
            Dict[str, float]: A mapping of ticker to its percentile rank (0.0 to 100.0).
        """
        returns = {}
        for ticker in tickers:
            try:
                df = self.tsdb.get_historical_prices(ticker, days=days + 10)
                if df is not None and not df.empty and len(df) > 20:
                    close = df["Close"].dropna()
                    if len(close) > 20:
                        ret = (close.iloc[-1] / close.iloc[0]) - 1.0
                        returns[ticker] = float(ret)
            except Exception as exc:
                logger.debug("Failed to fetch history for %s in cross-sectional: %s", ticker, exc)
                
        if not returns:
            return {}
            
        # Convert to series for rank computation
        s = pd.Series(returns)
        # Compute percentile rank (0.0 to 1.0)
        ranks = s.rank(pct=True) * 100.0
        
        logger.info("Computed cross-sectional momentum for %d tickers.", len(ranks))
        return ranks.to_dict()
