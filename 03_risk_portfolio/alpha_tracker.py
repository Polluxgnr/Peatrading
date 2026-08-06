import logging
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timezone
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "01_memory_core"))

from sqlite_portfolio import PortfolioDB

logger = logging.getLogger(__name__)

# Static Risk-Free Rate (e.g. 3.0% annual)
RISK_FREE_RATE_ANNUAL = 0.03

def calculate_alpha_metrics(portfolio_curve: pd.DataFrame) -> dict:
    """
    Computes Jensen's Alpha, Beta, Information Ratio, and Tracking Error 
    for the portfolio against CW8.PA (MSCI World) and ^FCHI (CAC 40).
    
    Args:
        portfolio_curve: DataFrame with 'date' and 'equity' columns.
        
    Returns:
        A dictionary with the computed metrics.
    """
    if portfolio_curve is None or portfolio_curve.empty or len(portfolio_curve) < 2:
        return {
            "beta_cac": 0.0,
            "beta_msci": 0.0,
            "alpha_cac": 0.0,
            "alpha_msci": 0.0,
            "ir_cac": 0.0,
            "ir_msci": 0.0,
            "te_cac": 0.0,
            "te_msci": 0.0,
        }

    try:
        portfolio_curve = portfolio_curve.copy()
        portfolio_curve['date'] = pd.to_datetime(portfolio_curve['date'])
        portfolio_curve = portfolio_curve.sort_values('date').set_index('date')
        
        # Calculate daily returns of portfolio
        portfolio_curve['returns'] = portfolio_curve['equity'].pct_change().fillna(0.0)
        
        start_date = portfolio_curve.index.min().strftime('%Y-%m-%d')
        end_date = (portfolio_curve.index.max() + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
        
        # Download benchmarks
        benchmarks = yf.download(["CW8.PA", "^FCHI"], start=start_date, end=end_date, progress=False)["Close"]
        
        # Ensure it's a DataFrame
        if isinstance(benchmarks, pd.Series):
            benchmarks = benchmarks.to_frame()
            
        if benchmarks.empty:
            logger.warning("No benchmark data found for the given dates.")
            raise ValueError("No benchmark data.")
            
        bench_returns = benchmarks.pct_change().fillna(0.0)
        bench_returns.index = pd.to_datetime(bench_returns.index)
        
        # Merge portfolio returns and benchmark returns
        merged = portfolio_curve[['returns']].join(bench_returns, how='inner').fillna(0.0)
        
        if len(merged) < 2:
            raise ValueError("Not enough overlapping data points to calculate metrics.")
            
        port_ret = merged['returns']
        rf_daily = RISK_FREE_RATE_ANNUAL / 252.0
        
        metrics = {}
        
        for bm_ticker, bm_name in [("^FCHI", "cac"), ("CW8.PA", "msci")]:
            if bm_ticker not in merged.columns:
                metrics[f"beta_{bm_name}"] = 0.0
                metrics[f"alpha_{bm_name}"] = 0.0
                metrics[f"ir_{bm_name}"] = 0.0
                metrics[f"te_{bm_name}"] = 0.0
                continue
                
            bm_ret = merged[bm_ticker]
            
            # 1. Beta = Cov(Rp, Rb) / Var(Rb)
            cov = np.cov(port_ret, bm_ret)[0, 1]
            var = np.var(bm_ret, ddof=1)
            beta = cov / var if var > 0 else 0.0
            
            # 2. Jensen's Alpha = (Rp - Rf) - Beta * (Rb - Rf)
            ann_port_ret = (1 + port_ret.mean()) ** 252 - 1
            ann_bm_ret = (1 + bm_ret.mean()) ** 252 - 1
            alpha = (ann_port_ret - RISK_FREE_RATE_ANNUAL) - beta * (ann_bm_ret - RISK_FREE_RATE_ANNUAL)
            
            # 3. Tracking Error = StdDev(Rp - Rb)
            active_returns = port_ret - bm_ret
            te_daily = np.std(active_returns, ddof=1)
            te_annual = te_daily * np.sqrt(252)
            
            # 4. Information Ratio = (Rp - Rb) / TE
            ann_active_ret = ann_port_ret - ann_bm_ret
            ir = ann_active_ret / te_annual if te_annual > 0 else 0.0
            
            metrics[f"beta_{bm_name}"] = round(beta, 2)
            metrics[f"alpha_{bm_name}"] = round(alpha * 100, 2) # in %
            metrics[f"ir_{bm_name}"] = round(ir, 2)
            metrics[f"te_{bm_name}"] = round(te_annual * 100, 2) # in %
            
        return metrics

    except Exception as e:
        logger.exception("Error calculating alpha metrics: %s", e)
        return {
            "beta_cac": 0.0,
            "beta_msci": 0.0,
            "alpha_cac": 0.0,
            "alpha_msci": 0.0,
            "ir_cac": 0.0,
            "ir_msci": 0.0,
            "te_cac": 0.0,
            "te_msci": 0.0,
        }

if __name__ == "__main__":
    db = PortfolioDB()
    db.init_db()
    curve = db.get_equity_curve()
    print(calculate_alpha_metrics(curve))
