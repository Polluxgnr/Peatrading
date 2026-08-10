"""Quantitative Engine & Alpha Strategy package for PEA Pollux."""

from .hmm_regime import HMMRegimeClassifier, MarketRegimeState
from .ml_feature_store import FeatureStore
from .quantitative_math import calculate_cvar, calculate_historical_var, calculate_cornish_fisher_var
from .smart_dca_engine import SmartDCAEngine
from .stat_arb_pairs import StatArbEngine
from .stochastic_models import StochasticEngine
from .technical_scorer import SignalGenerator
from .walk_forward_backtester import WalkForwardBacktester

__all__ = [
    "FeatureStore",
    "HMMRegimeClassifier",
    "MarketRegimeState",
    "SignalGenerator",
    "SmartDCAEngine",
    "StatArbEngine",
    "StochasticEngine",
    "WalkForwardBacktester",
    "calculate_cvar",
    "calculate_historical_var",
    "calculate_cornish_fisher_var",
]
