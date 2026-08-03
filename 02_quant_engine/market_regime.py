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
        self._cached_regime = None
        
    def get_regime(self) -> str:
        """Evaluate VIX and CAC40 to return current regime via HMM.
        
        Returns:
            str: 'BULL', 'BEAR', or 'VOLATILE'
        """
        if self._cached_regime:
            return self._cached_regime
            
        try:
            vix = self.macro_sensor.get_european_vix()
        except Exception:
            logger.warning("Could not fetch VIX. Defaulting to VOLATILE for safety.")
            return "VOLATILE"
            
        if vix is not None and vix > 30.0:
            self._cached_regime = "VOLATILE"
            return "VOLATILE"
            
        try:
            import numpy as np
            from hmmlearn.hmm import GaussianHMM
            
            # Fetch ~3 years of data for robust HMM training
            df = self.tsdb.get_historical_prices("^FCHI", days=1000)
            if df is None or df.empty or "Close" not in df.columns or len(df) < 100:
                logger.warning("Not enough history for ^FCHI to compute HMM. Defaulting to VOLATILE for safety.")
                return "VOLATILE"
                
            close = df["Close"].astype(float).dropna()
            returns = close.pct_change().dropna()
            
            # Features: log returns and 10-day rolling volatility
            vol = returns.rolling(10).std().dropna()
            
            # Align indices
            common_idx = returns.index.intersection(vol.index)
            X = np.column_stack([returns[common_idx].values, vol[common_idx].values])
            
            # Fit HMM (3 states: Bull, Bear, Volatile)
            model = GaussianHMM(n_components=3, covariance_type="diag", n_iter=100, random_state=42)
            model.fit(X)
            
            # Predict the latent state for the most recent observation
            hidden_states = model.predict(X)
            current_state = hidden_states[-1]
            
            # Heuristic to label states based on their mean return and volatility
            means = model.means_
            # means[:, 0] = return, means[:, 1] = vol
            
            # Highest vol state = VOLATILE
            volatile_state = np.argmax(means[:, 1])
            
            # Among the other two, the one with higher return is BULL, lower is BEAR
            other_states = [i for i in range(3) if i != volatile_state]
            if means[other_states[0], 0] > means[other_states[1], 0]:
                bull_state, bear_state = other_states[0], other_states[1]
            else:
                bull_state, bear_state = other_states[1], other_states[0]
                
            if current_state == volatile_state:
                regime = "VOLATILE"
            elif current_state == bull_state:
                regime = "BULL"
            else:
                regime = "BEAR"
                
            logger.info("HMM Regime detected: %s (bull=%d, bear=%d, vol=%d, current=%d)",
                        regime, bull_state, bear_state, volatile_state, current_state)
            self._cached_regime = regime
            return regime
            
        except Exception:
            logger.exception("Failed to compute CAC40 HMM regime. Defaulting to VOLATILE for safety.")
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
