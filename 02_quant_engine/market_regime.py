"""Market Regime Classifier for PEA Pollux.

Detects current market regime (BULL, BEAR, VOLATILE) using VIX and CAC40.
Modulates quant engine parameters like CONVICTION_EMIT_FLOOR and RSI_OVERSOLD.
"""

import logging
from typing import Tuple

import pandas as pd

from duckdb_manager import TimeSeriesDB
from macro_alpha_api import MacroAlphaSensor

logger = logging.getLogger(__name__)

class MarketRegimeClassifier:
    """Classifies the market regime to modulate quant engine thresholds."""
    
    def __init__(self) -> None:
        self.tsdb = TimeSeriesDB(read_only=True)
        self.macro_sensor = MacroAlphaSensor()
        
    def get_regime(self) -> str:
        """Evaluate VIX and CAC40 to return current regime.
        
        Returns:
            str: 'BULL', 'BEAR', or 'VOLATILE'
        """
        try:
            vix = self.macro_sensor.get_european_vix()
        except Exception:
            logger.warning("Could not fetch VIX. Defaulting to VOLATILE for safety.")
            return "VOLATILE"
            
        if vix is not None and vix > 30.0:
            return "VOLATILE"
            
        try:
            # Need enough history to compute SMA200 (200 trading days requires ~300 calendar days)
            df = self.tsdb.get_historical_prices("^FCHI", days=400)
            if df is None or df.empty or "Close" not in df.columns or len(df) < 200:
                logger.warning("Not enough history for ^FCHI to compute SMA200. Defaulting to VOLATILE for safety.")
                return "VOLATILE"
                
            close = df["Close"].astype(float).dropna()
            if close.empty or len(close) < 200:
                logger.warning("Close data missing. Defaulting to VOLATILE for safety.")
                return "VOLATILE"
                
            current_price = float(close.iloc[-1])
            sma200 = float(close.rolling(window=200).mean().iloc[-1])
            
            if current_price > sma200:
                return "BULL"
            else:
                return "BEAR"
        except Exception:
            logger.exception("Failed to compute CAC40 regime. Defaulting to VOLATILE for safety.")
            return "VOLATILE"

    def get_modulated_thresholds(
        self, regime: str, base_conviction: float = 65.0, base_rsi: float = 30.0
    ) -> Tuple[float, float]:
        """Modulate conviction and RSI based on regime.
        
        Args:
            regime: Output of get_regime().
            base_conviction: Default floor.
            base_rsi: Default RSI oversold threshold.
            
        Returns:
            Tuple[float, float]: (conviction_floor, rsi_oversold)
        """
        if regime == "VOLATILE":
            return 75.0, base_rsi
        elif regime == "BEAR":
            return 70.0, 25.0
        else:
            return base_conviction, base_rsi
