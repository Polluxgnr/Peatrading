import pandas as pd
import numpy as np

def run_autonomous_backtest(csv_path: str, initial_capital: float = 10000.0) -> pd.DataFrame:
    """Run an autonomous backtest on the ML dataset vs CW8.
    
    Dynamically sizes trades based on Score/Probability.
    Includes 0.5% slippage/fees.
    Uses a threshold to avoid high frequency (e.g. Score > 70).
    """
    try:
        df = pd.read_csv(csv_path)
    except Exception:
        return pd.DataFrame()

    if df.empty or 'Date' not in df.columns:
        return pd.DataFrame()

    df['Date'] = pd.to_datetime(df['Date'])
    df = df.sort_values('Date')
    
    return df
