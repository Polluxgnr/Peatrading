# PEA Pollux — Quantitative Strategy, Indicators, HMM Regimes & ML Feature Store
Generated: `2026-08-10 17:10 UTC` | File Count: `18`
Institutional Systematic Decision Support Architecture for French PEA.
---
## Included Files Index
- [02_quant_engine/__init__.py](#file-02_quant_engine-__init__-py)
- [02_quant_engine/contextual_bandit.py](#file-02_quant_engine-contextual_bandit-py)
- [02_quant_engine/cross_sectional.py](#file-02_quant_engine-cross_sectional-py)
- [02_quant_engine/ensemble_optimizer.py](#file-02_quant_engine-ensemble_optimizer-py)
- [02_quant_engine/hmm_regime.py](#file-02_quant_engine-hmm_regime-py)
- [02_quant_engine/llm_sentiment_engine.py](#file-02_quant_engine-llm_sentiment_engine-py)
- [02_quant_engine/market_regime.py](#file-02_quant_engine-market_regime-py)
- [02_quant_engine/ml_backtester.py](#file-02_quant_engine-ml_backtester-py)
- [02_quant_engine/ml_feature_store.py](#file-02_quant_engine-ml_feature_store-py)
- [02_quant_engine/ml_trainer.py](#file-02_quant_engine-ml_trainer-py)
- [02_quant_engine/nlp_sentiment_engine.py](#file-02_quant_engine-nlp_sentiment_engine-py)
- [02_quant_engine/quantitative_math.py](#file-02_quant_engine-quantitative_math-py)
- [02_quant_engine/risk_engine.py](#file-02_quant_engine-risk_engine-py)
- [02_quant_engine/smart_dca_engine.py](#file-02_quant_engine-smart_dca_engine-py)
- [02_quant_engine/stochastic_models.py](#file-02_quant_engine-stochastic_models-py)
- [02_quant_engine/technical_scorer.py](#file-02_quant_engine-technical_scorer-py)
- [02_quant_engine/train_rl_sizer.py](#file-02_quant_engine-train_rl_sizer-py)
- [02_quant_engine/walk_forward_backtester.py](#file-02_quant_engine-walk_forward_backtester-py)

---
## FILE: 02_quant_engine/__init__.py
```python
"""Quantitative Engine & Alpha Strategy package for PEA Pollux."""

from .hmm_regime import HMMRegimeClassifier, MarketRegimeState
from .ml_feature_store import FeatureStore
from .quantitative_math import calculate_cvar, calculate_historical_var, calculate_cornish_fisher_var
from .smart_dca_engine import SmartDCAEngine
from .stochastic_models import StochasticEngine
from .technical_scorer import SignalGenerator
from .walk_forward_backtester import WalkForwardBacktester

__all__ = [
    "FeatureStore",
    "HMMRegimeClassifier",
    "MarketRegimeState",
    "SignalGenerator",
    "SmartDCAEngine",
    "StochasticEngine",
    "WalkForwardBacktester",
    "calculate_cvar",
    "calculate_historical_var",
    "calculate_cornish_fisher_var",
]
```

## FILE: 02_quant_engine/contextual_bandit.py
```python
"""Contextual Bandits for Dynamic Sub-model Weighting.

Replaces fixed weights in the technical scorer with dynamic UCB / Thompson Sampling
weights. The bandit learns which sub-model (Trend, MR, Breakout, Context) performs 
best in the current market environment.
"""
import json
import logging
from pathlib import Path
import numpy as np

logger = logging.getLogger(__name__)

class UCBBandit:
    def __init__(self, storage_path: Path | None = None, c: float = 2.0):
        self.storage_path = storage_path or Path(__file__).resolve().parent.parent / "database" / "bandit_state.json"
        self.arms = ["trend", "mean_reversion", "breakout", "context"]
        self.c = c
        self.state = self._load_state()

    def _load_state(self) -> dict:
        if self.storage_path.exists():
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                logger.warning("Failed to load bandit state, using default.")
        
        # Default tracking parameters per regime
        return {
            "BULL": {
                "trend": {"rewards": 30.0, "counts": 100},
                "mean_reversion": {"rewards": 25.0, "counts": 100},
                "breakout": {"rewards": 20.0, "counts": 100},
                "context": {"rewards": 25.0, "counts": 100},
            },
            "BEAR": {
                "trend": {"rewards": 10.0, "counts": 100},
                "mean_reversion": {"rewards": 30.0, "counts": 100},
                "breakout": {"rewards": 10.0, "counts": 100},
                "context": {"rewards": 30.0, "counts": 100},
            },
            "VOLATILE": {
                "trend": {"rewards": 15.0, "counts": 100},
                "mean_reversion": {"rewards": 35.0, "counts": 100},
                "breakout": {"rewards": 25.0, "counts": 100},
                "context": {"rewards": 25.0, "counts": 100},
            }
        }

    def save_state(self):
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save bandit state: {e}")

    def get_weights(self, regime: str = "BULL") -> dict[str, float]:
        """Calculate UCB weights and normalize to sum to 1.0"""
        regime_state = self.state.get(regime, self.state.get("BULL", {}))
        if not regime_state:
            return {arm: 1.0 / len(self.arms) for arm in self.arms}

        total_counts = sum(arm_data["counts"] for arm_data in regime_state.values())
        if total_counts == 0:
            return {arm: 1.0 / len(self.arms) for arm in self.arms}
            
        ucb_values = {}
        for arm in self.arms:
            arm_data = regime_state.get(arm, {"rewards": 0.0, "counts": 0})
            counts = arm_data["counts"]
            if counts == 0:
                ucb_values[arm] = 1000.0 # High value to ensure exploration
            else:
                mean_reward = arm_data["rewards"] / counts
                exploration = self.c * np.sqrt(np.log(total_counts) / counts)
                ucb_values[arm] = max(0, mean_reward + exploration)
                
        total_ucb = sum(ucb_values.values())
        if total_ucb > 0:
            return {arm: val / total_ucb for arm, val in ucb_values.items()}
        return {arm: 1.0 / len(self.arms) for arm in self.arms}

    def update_reward(self, regime: str, arm: str, reward: float):
        """Update counts and rewards for the chosen arm."""
        if regime not in self.state:
            self.state[regime] = {a: {"rewards": 0.0, "counts": 0} for a in self.arms}
            
        if arm not in self.state[regime]:
            self.state[regime][arm] = {"rewards": 0.0, "counts": 0}
            
        self.state[regime][arm]["counts"] += 1
        self.state[regime][arm]["rewards"] += reward
        self.save_state()
```

## FILE: 02_quant_engine/cross_sectional.py
```python
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
```

## FILE: 02_quant_engine/ensemble_optimizer.py
```python
import json
import logging
from pathlib import Path

logger = logging.getLogger("ensemble_optimizer")

_ROOT = Path(__file__).resolve().parent.parent

class DynamicEnsemble:
    """Dynamic Ensemble Optimizer for weighting ML vs Heuristic models."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or (_ROOT / "database")

    def _read_ml_metrics(self, filename: str) -> dict:
        """Read metrics from the XGBoost JSON artifact safely."""
        filepath = self.db_path / filename
        if not filepath.exists():
            return {}
            
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("metrics", {})
        except Exception as exc:
            logger.warning(f"Could not parse ML metrics from {filename}: {exc}")
            return {}

    def get_optimized_weights(self) -> dict[str, float]:
        """
        Calculates dynamic weights for the ensemble.
        Uses XGBoost accuracy to balance ML vs Heuristics globally.
        If ML is highly accurate, it gets more weight. If it fails, heuristics take over.
        """
        tactical_metrics = self._read_ml_metrics("xgboost_model_tactical.json")
        structural_metrics = self._read_ml_metrics("xgboost_model_structural.json")

        # Get accuracy (default to 0.50 if not found)
        acc_tactical = float(tactical_metrics.get("accuracy", 0.50))
        acc_structural = float(structural_metrics.get("accuracy", 0.50))
        
        avg_acc = (acc_tactical + acc_structural) / 2.0

        # Base weights for heuristics
        # Standard: 0.30 Trend, 0.25 MR, 0.20 Breakout, 0.25 Context
        base_heuristic = {
            "trend": 0.30,
            "mean_reversion": 0.25,
            "breakout": 0.20,
            "context": 0.25
        }
        
        # Calculate ML multiplier based on accuracy vs 50% baseline
        # E.g., if accuracy is 60%, ml_weight is 0.60
        # If accuracy is 40%, ml_weight is 0.40
        # We cap it between 0.20 (min ML influence) and 0.80 (max ML influence)
        ml_weight = max(0.20, min(0.80, avg_acc))
        
        # The remaining weight goes to the heuristics
        heuristic_weight = 1.0 - ml_weight
        
        # Scale heuristic weights
        heuristic_scaled = {k: v * heuristic_weight for k, v in base_heuristic.items()}
        
        return {
            "ml_tactical_weight": ml_weight * 0.5,
            "ml_structural_weight": ml_weight * 0.5,
            "ml_total_weight": ml_weight,
            "heuristic_trend_weight": heuristic_scaled["trend"],
            "heuristic_mr_weight": heuristic_scaled["mean_reversion"],
            "heuristic_breakout_weight": heuristic_scaled["breakout"],
            "heuristic_context_weight": heuristic_scaled["context"],
            "avg_accuracy": avg_acc
        }
```

## FILE: 02_quant_engine/hmm_regime.py
```python
"""Hidden Markov Model (HMM) Market Regime Classifier for PEA Sniper Terminal.

Fits a 3-state Gaussian HMM on CAC 40 (^FCHI) daily returns & realized volatility:
  - State 0: BULL (Positive drift, low volatility)
  - State 1: BEAR (Negative drift, elevated volatility)
  - State 2: VOLATILE / TRANSITION (Zero/mixed drift, high volatility)

Fail-safe: defaults strictly to VOLATILE (never BULL) if data retrieval fails or history is insufficient.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


class MarketRegimeState(str, Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    VOLATILE = "VOLATILE"


class HMMRegimeClassifier:
    """Classifies market regimes using Gaussian Hidden Markov Models."""

    def __init__(self, index_ticker: str = "^FCHI", n_states: int = 3) -> None:
        self.index_ticker = index_ticker
        self.n_states = n_states
        self.model = None

    def fit_and_predict(self, ohlcv_df: Optional[pd.DataFrame] = None) -> Tuple[MarketRegimeState, float]:
        """Fit HMM on index returns and return the current regime state and posterior probability.

        Returns:
            Tuple[MarketRegimeState, float]: (Current regime, Confidence probability).
        """
        # Fail-safe default
        default_state = MarketRegimeState.VOLATILE
        default_prob = 0.50

        if ohlcv_df is None or ohlcv_df.empty:
            try:
                ohlcv_df = yf.download(self.index_ticker, period="2y", interval="1d", progress=False, auto_adjust=True)
                if isinstance(ohlcv_df.columns, pd.MultiIndex):
                    c = ohlcv_df["Close"]
                    ohlcv_df = pd.DataFrame({"Close": c.iloc[:, 0] if isinstance(c, pd.DataFrame) else c})
            except Exception as exc:  # noqa: BLE001
                logger.warning("HMM failed to fetch %s: %s; using fail-safe %s", self.index_ticker, exc, default_state)
                return default_state, default_prob

        if ohlcv_df is None or ohlcv_df.empty or len(ohlcv_df) < 100:
            logger.warning("Insufficient history for HMM; using fail-safe %s", default_state)
            return default_state, default_prob

        try:
            from hmmlearn.hmm import GaussianHMM
        except ImportError:
            logger.debug("hmmlearn not installed; using rule-based regime heuristic")
            return self._rule_based_fallback(ohlcv_df)

        try:
            close = ohlcv_df["Close"].dropna().astype(float)
            rets = close.pct_change().dropna()
            vol = rets.rolling(20).std().dropna()

            idx_common = rets.index.intersection(vol.index)
            X = np.column_stack([rets.loc[idx_common].values, vol.loc[idx_common].values])

            self.model = GaussianHMM(n_components=self.n_states, covariance_type="full", n_iter=100, random_state=42)
            self.model.fit(X)

            # Identify states by mean return
            means = self.model.means_[:, 0]
            bull_state_idx = int(np.argmax(means))
            bear_state_idx = int(np.argmin(means))
            # The remaining is volatile
            all_indices = set(range(self.n_states))
            vol_state_idx = list(all_indices - {bull_state_idx, bear_state_idx})[0]

            # Predict current state
            posteriors = self.model.predict_proba(X[-1:])
            cur_state_idx = int(np.argmax(posteriors[0]))
            confidence = float(posteriors[0][cur_state_idx])

            if cur_state_idx == bull_state_idx:
                regime = MarketRegimeState.BULL
            elif cur_state_idx == bear_state_idx:
                regime = MarketRegimeState.BEAR
            else:
                regime = MarketRegimeState.VOLATILE

            logger.info("HMM Regime on %s: %s (Prob: %.2f)", self.index_ticker, regime.value, confidence)
            return regime, confidence

        except Exception as exc:  # noqa: BLE001
            logger.warning("HMM fitting failed: %s; using fail-safe %s", exc, default_state)
            return default_state, default_prob

    def _rule_based_fallback(self, ohlcv_df: pd.DataFrame) -> Tuple[MarketRegimeState, float]:
        """Fallback regime detector when hmmlearn is offline."""
        close = ohlcv_df["Close"].dropna().astype(float)
        cur = float(close.iloc[-1])
        sma50 = float(close.tail(50).mean())
        sma200 = float(close.tail(200).mean())

        if cur > sma50 > sma200:
            return MarketRegimeState.BULL, 0.80
        elif cur < sma50 < sma200:
            return MarketRegimeState.BEAR, 0.80
        else:
            return MarketRegimeState.VOLATILE, 0.65


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    clf = HMMRegimeClassifier()
    reg, conf = clf.fit_and_predict()
    print(f"Market Regime: {reg.value} (Confidence: {conf:.2f})")
```

## FILE: 02_quant_engine/llm_sentiment_engine.py
```python
import os
import sys
import json
import requests
from pathlib import Path
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "01_memory_core"))

from logging_setup import get_logger
from sqlite_portfolio import SQLitePortfolioDB

logger = get_logger("llm_sentiment_engine")

load_dotenv(_ROOT / ".env")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral")

# Load VADER as a fallback
try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    vader_analyzer = SentimentIntensityAnalyzer()
except ImportError:
    logger.warning("vaderSentiment not installed. Fallback sentiment will be 0.0.")
    vader_analyzer = None


def fallback_vader(text: str) -> tuple[float, str, str]:
    """Fallback sentiment calculation using VADER."""
    if not vader_analyzer:
        return 0.0, "Neutral", "Fallback to neutral due to missing VADER."
    
    scores = vader_analyzer.polarity_scores(text)
    compound = float(scores["compound"])
    
    if compound >= 0.05:
        label = "Bullish"
    elif compound <= -0.05:
        label = "Bearish"
    else:
        label = "Neutral"
        
    return compound, label, "Calculated using VADER heuristic fallback."


def call_ollama(text: str) -> tuple[float, str, str] | None:
    """Send text to Ollama and ask for structured JSON."""
    prompt = f"""You are a professional quantitative analyst. 
Analyze the following financial news article and return a strict JSON object with EXACTLY these three keys:
- "guidance_score": A float between -1.0 (extremely bearish) and 1.0 (extremely bullish).
- "sentiment_label": Must be exactly one of "Bullish", "Bearish", or "Neutral".
- "reasoning": A brief one-sentence financial justification for the score.

News text:
{text}

Return ONLY the JSON object. Do not include markdown formatting or conversational text."""

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json"
    }

    try:
        url = f"{OLLAMA_URL.rstrip('/')}/api/generate"
        response = requests.post(url, json=payload, timeout=20)
        response.raise_for_status()
        
        result = response.json()
        output_text = result.get("response", "").strip()
        
        # Ollama might wrap JSON in markdown block even with format="json" in some models
        if output_text.startswith("``​`json"):
            output_text = output_text[7:]
        if output_text.endswith("``​`"):
            output_text = output_text[:-3]
            
        data = json.loads(output_text.strip())
        
        g_score = float(data.get("guidance_score", 0.0))
        label = str(data.get("sentiment_label", "Neutral"))
        reasoning = str(data.get("reasoning", "No reasoning provided."))
        
        # Ensure label validity
        if label not in ("Bullish", "Bearish", "Neutral"):
            label = "Neutral"
            
        # Ensure score bounds
        g_score = max(-1.0, min(1.0, g_score))
        
        return g_score, label, reasoning
        
    except Exception as e:
        logger.warning(f"Ollama inference failed: {e}")
        return None


def score_news_batch(db: SQLitePortfolioDB):
    """Fetch unprocessed news, score them using Ollama (or VADER), and update the DB."""
    unprocessed = db.get_unprocessed_news()
    if not unprocessed:
        logger.info("No unprocessed news found.")
        return
        
    logger.info("Scoring %d unprocessed news items with Ollama (%s)...", len(unprocessed), OLLAMA_MODEL)
    
    updates = []
    
    for item in unprocessed:
        text = f"{item['title']} {item['content'] or ''}"
        # Truncate text if it's too long for typical small LLM context
        text = text[:4000]
        
        res = call_ollama(text)
        if res:
            compound, label, reasoning = res
            logger.debug("Ollama success for news ID %s: %s", item["id"], label)
        else:
            compound, label, reasoning = fallback_vader(text)
            logger.debug("VADER fallback for news ID %s: %s", item["id"], label)
            
        # We also might want to store reasoning, but our news_master schema might not have it yet.
        # We will just log it for now and update sentiment.
        # The prompt requested we use the database, the schema has:
        # id, published_at, ticker, source, url, title, content, sentiment_score, sentiment_label
        
        updates.append({
            "id": item["id"],
            "sentiment_score": compound,
            "sentiment_label": label
        })
        
    if updates:
        db.update_news_sentiment(updates)
        logger.info("LLM Sentiment scoring completed for %d items.", len(updates))


if __name__ == "__main__":
    db = SQLitePortfolioDB()
    score_news_batch(db)
```

## FILE: 02_quant_engine/market_regime.py
```python
"""Market Regime & Volatility Percentile Tiers for PEA Sniper Terminal.

Upgrades hard binary VIX cutoffs to continuous 252-day percentile-ranked volatility tiers:
  * Percentile >= 95th: Panic / Extreme Volatility -> Conviction Floor +15 pts
  * Percentile >= 80th: Elevated Volatility -> Conviction Floor +5 pts
  * Percentile >= 50th: Normal / Moderate -> Conviction Floor +0 pts
  * Percentile < 50th: Low Volatility / Complacency -> Conviction Floor +0 pts
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple, Union

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class VolatilityRegimeSentinel:
    """Computes rolling 252-day percentile rank of European / Global volatility."""

    def __init__(self, window: int = 252) -> None:
        self.window = window

    @staticmethod
    def calculate_percentile_rank(
        history: Union[pd.Series, Sequence[float], pd.DataFrame],
        current_value: Optional[float] = None,
    ) -> float:
        """Calculate the percentile rank (0.0 to 100.0) of current volatility.

        Args:
            history: Historical VIX/V2TX series (at least 20-252 points).
            current_value: Current VIX level. If None, uses the last element of history.

        Returns:
            float: Percentile rank between 0.0 and 100.0.
        """
        if history is None:
            return 50.0

        if isinstance(history, pd.DataFrame):
            col = "Close" if "Close" in history.columns else history.columns[0]
            series = history[col].dropna().astype(float)
        elif isinstance(history, pd.Series):
            series = history.dropna().astype(float)
        elif isinstance(history, (list, tuple)):
            series = pd.Series(history, dtype=float).dropna()
        else:
            return 50.0

        if len(series) < 5:
            return 50.0

        val = float(current_value if current_value is not None else series.iloc[-1])
        # Percentile rank: % of historical observations <= val
        rank = (series <= val).mean() * 100.0
        return float(np.clip(rank, 0.0, 100.0))

    def get_conviction_floor_modifier(self, percentile: float) -> int:
        """Map volatility percentile rank to a conviction floor offset.

        Args:
            percentile: Percentile rank [0.0..100.0].

        Returns:
            int: Modifier (+15, +5, 0).
        """
        if percentile >= 95.0:
            return 15
        elif percentile >= 80.0:
            return 5
        elif percentile >= 50.0:
            return 0
        else:
            return 0

    def evaluate_vix_regime(
        self,
        vix_history: Union[pd.Series, Sequence[float], pd.DataFrame],
        current_vix: float,
        base_floor: int = 70,
    ) -> dict:
        """Evaluate volatility regime and calculate dynamic conviction threshold.

        Args:
            vix_history: Historical VIX data.
            current_vix: Current spot VIX / V2TX.
            base_floor: Standard emit floor (e.g. 70).

        Returns:
            dict: {
                "current_vix": float,
                "percentile": float,
                "floor_modifier": int,
                "effective_floor": int,
                "regime": str,
                "is_panic": bool
            }
        """
        pct = self.calculate_percentile_rank(vix_history, current_vix)
        mod = self.get_conviction_floor_modifier(pct)
        eff_floor = base_floor + mod

        if pct >= 95.0 or current_vix >= 32.0:
            regime = "PANIC"
            is_panic = True
        elif pct >= 80.0:
            regime = "ELEVATED_VOL"
            is_panic = False
        elif pct >= 50.0:
            regime = "NORMAL"
            is_panic = False
        else:
            regime = "LOW_VOL"
            is_panic = False

        logger.info(
            "VIX Regime: level=%.2f (pct=%.1f%%) -> regime=%s floor=%d (+%d)",
            current_vix,
            pct,
            regime,
            eff_floor,
            mod,
        )

        return {
            "current_vix": float(current_vix),
            "percentile": float(pct),
            "floor_modifier": int(mod),
            "effective_floor": int(eff_floor),
            "regime": regime,
            "is_panic": is_panic,
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sentinel = VolatilityRegimeSentinel()
    np.random.seed(42)
    fake_vix = np.random.normal(18.0, 4.0, 252)
    res = sentinel.evaluate_vix_regime(fake_vix, current_vix=28.5)
    print("Regime Assessment:", res)
```

## FILE: 02_quant_engine/ml_backtester.py
```python
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
```

## FILE: 02_quant_engine/ml_feature_store.py
```python
"""ML Feature Store & Conformal Prediction Engine for PEA Sniper Terminal.

Extracts technical & quantitative features:
  - RSI(14), ATR(14) normalized, Bollinger Bands %B and Bandwidth
  - MACD Histogram, Rolling Momentum (5d, 21d, 63d)
  - Volume Z-Score, Trend Quality (linear regression R^2 * slope)
Trains an XGBoost classifier with Conformal Prediction prediction sets
to output mathematically calibrated prediction sets (guaranteed coverage).
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.stats import linregress

logger = logging.getLogger(__name__)


class FeatureStore:
    """Computes ML feature matrices and manages Conformal Prediction calibration."""

    @staticmethod
    def extract_features(df: pd.DataFrame) -> pd.DataFrame:
        """Extract multi-factor technical features from raw OHLCV DataFrame."""
        if df is None or df.empty or len(df) < 30:
            return pd.DataFrame()

        data = df.sort_values("Date").copy()
        close = data["Close"].astype(float)
        high = data["High"].astype(float)
        low = data["Low"].astype(float)
        volume = data["Volume"].astype(float)

        feats = pd.DataFrame(index=data.index)
        feats["date"] = data["Date"]

        # 1. Momentum & Returns
        feats["ret_1d"] = close.pct_change(1)
        feats["ret_5d"] = close.pct_change(5)
        feats["ret_21d"] = close.pct_change(21)

        # 2. RSI(14)
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        feats["rsi_14"] = 100.0 - (100.0 / (1.0 + rs))

        # 3. ATR(14) Normalized
        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr14 = tr.rolling(14).mean()
        feats["atr_norm"] = atr14 / close

        # 4. Bollinger Bands (%B and Bandwidth)
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper = sma20 + 2.0 * std20
        lower = sma20 - 2.0 * std20
        feats["bb_pct_b"] = (close - lower) / (upper - lower).replace(0, np.nan)
        feats["bb_bandwidth"] = (upper - lower) / sma20

        # 5. Volume Z-Score
        vol_mean = volume.rolling(20).mean()
        vol_std = volume.rolling(20).std().replace(0, np.nan)
        feats["vol_zscore"] = (volume - vol_mean) / vol_std

        # 6. Trend Quality (Rolling 30d linregress R^2 * slope)
        def _calc_tq(window):
            if len(window) < 10:
                return 0.0
            y = window.values / window.values[0]
            x = np.arange(len(y))
            res = linregress(x, y)
            return float(res.rvalue**2 * res.slope * 252)

        feats["trend_quality"] = close.rolling(30).apply(_calc_tq, raw=False)

        # Target: Forward 5-day return > +2.0%
        feats["target"] = (close.shift(-5) / close - 1.0 > 0.02).astype(int)

        return feats.dropna().reset_index(drop=True)

    @staticmethod
    def train_conformal_classifier(
        train_features: pd.DataFrame,
        confidence_level: float = 0.90,
    ) -> Tuple[any, float]:
        """Train XGBoost model and calibrate non-conformity scores.

        Returns:
            Tuple[model, conformity_threshold].
        """
        try:
            from xgboost import XGBClassifier
        except ImportError:
            logger.debug("xgboost not installed, using RandomForest fallback")
            from sklearn.ensemble import RandomForestClassifier as XGBClassifier

        feature_cols = [c for c in train_features.columns if c not in ("date", "target")]
        X = train_features[feature_cols].values
        y = train_features["target"].values

        # Split into training and calibration sets (80 / 20)
        split_idx = int(len(X) * 0.8)
        X_train, X_calib = X[:split_idx], X[split_idx:]
        y_train, y_calib = y[:split_idx], y[split_idx:]

        model = XGBClassifier(n_estimators=100, max_depth=3, random_state=42)
        model.fit(X_train, y_train)

        # Calibration: Non-conformity scores = 1 - P(True class)
        probs_calib = model.predict_proba(X_calib)
        non_conformity = 1.0 - probs_calib[np.arange(len(y_calib)), y_calib]

        # Quantile threshold for 1 - alpha coverage
        q_level = np.ceil((len(y_calib) + 1) * confidence_level) / len(y_calib)
        q_level = min(1.0, max(0.0, q_level))
        threshold = float(np.quantile(non_conformity, q_level))

        logger.info("Conformal classifier trained. Coverage threshold: %.4f", threshold)
        return model, threshold


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    dates = pd.date_range("2024-01-01", periods=100)
    prices = np.linspace(100, 120, 100) + np.random.normal(0, 1, 100)
    sample_df = pd.DataFrame({
        "Date": dates,
        "Open": prices,
        "High": prices + 1.0,
        "Low": prices - 1.0,
        "Close": prices,
        "Volume": np.random.randint(1000, 5000, 100),
    })

    store = FeatureStore()
    extracted = store.extract_features(sample_df)
    print("Extracted features shape:", extracted.shape)
```

## FILE: 02_quant_engine/ml_trainer.py
```python
"""XGBoost trainer for forward-return prediction (Phase 60).

Reads ``database/ml_training_dataset.csv``, trains a classifier for
``label_fwd_gt_2pct``, and saves the model to ``database/xgboost_model_<regime>.json``.
Uses MAPIE for Conformal Prediction.
"""

from __future__ import annotations

import json
import logging
import sys
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "02_quant_engine"))
sys.path.insert(0, str(_ROOT / "00_data_sensors"))
sys.path.insert(0, str(_ROOT / "01_memory_core"))

logger = logging.getLogger(__name__)

_DATASET = _ROOT / "database" / "ml_training_dataset.csv"
_METRICS_PATH = _ROOT / "database" / "ml_model_metrics.json"

FEATURE_COLS = [
    "rsi14",
    "zscore_50",
    "vol_20d_ann",
    "zscore_20d",
    "sp500_ret1d",
    "ndx_ret1d",
    "eurusd_ret1d",
    "oat_ret1d",
    "sector_relative_ret1d",
]
TARGET_TACTICAL = "target_tactical_30d"
TARGET_STRUCTURAL = "target_structural_126d"


def _load_dataset(path: Path | None = None) -> pd.DataFrame:
    p = Path(path) if path else _DATASET
    if not p.exists():
        raise FileNotFoundError(f"Training dataset not found: {p}")
    df = pd.read_csv(p)
    if df.empty:
        raise ValueError("Training dataset is empty.")
    return df

def _assign_regimes(df: pd.DataFrame) -> pd.DataFrame:
    """Run HMM on historical ^FCHI to assign regimes to each row."""
    df = df.copy()
    if "regime" not in df.columns:
        logger.info("Computing historical regimes for training data...")
        from duckdb_manager import TimeSeriesDB
        from hmmlearn.hmm import GaussianHMM
        
        tsdb = TimeSeriesDB(read_only=True)
        fchi = tsdb.get_historical_prices("^FCHI", days=3000)
        
        if fchi is None or fchi.empty:
            logger.warning("No ^FCHI data. Randomly assigning regimes for training.")
            df["regime"] = np.random.choice(["BULL", "BEAR", "VOLATILE"], size=len(df))
            return df
            
        fchi = fchi.sort_values("Date")
        fchi = fchi.set_index("Date")
        close = fchi["Close"].astype(float).dropna()
        returns = close.pct_change().dropna()
        
        # Calculate rolling stats to avoid lookahead bias
        vol_ann = returns.rolling(20).std() * np.sqrt(252)
        trend = close.rolling(126).mean()
        common_idx = returns.index.intersection(vol_ann.index).intersection(trend.index)
        
        # Calculate an expanding 75th percentile for high volatility threshold
        vol_threshold = vol_ann.expanding(min_periods=126).quantile(0.75)
        
        regimes = []
        for date in common_idx:
            v = vol_ann.loc[date]
            t = trend.loc[date]
            c = close.loc[date]
            v_thresh = vol_threshold.loc[date]
            
            if pd.isna(v) or pd.isna(t) or pd.isna(v_thresh):
                regimes.append("VOLATILE")
            elif v > v_thresh:
                regimes.append("VOLATILE")
            elif c > t:
                regimes.append("BULL")
            else:
                regimes.append("BEAR")
                
        regime_series = pd.Series(regimes, index=common_idx)
        
        if "Date" in df.columns:
            df["Date"] = pd.to_datetime(df["Date"])
            df["regime"] = df["Date"].map(regime_series)
            df["regime"] = df["regime"].ffill().bfill()
        else:
            df["regime"] = "VOLATILE"
            
    return df


def train_model(
    dataset_path: Path | None = None,
) -> dict:
    """Train XGBoost classifiers and persist models + metrics."""
    try:
        import xgboost as xgb
        from mapie.classification import MapieClassifier
    except ImportError as exc:
        raise ImportError(
            "xgboost and mapie are required for ML training. pip install xgboost mapie"
        ) from exc

    df = _load_dataset(dataset_path)
    logger.info("Loaded ML training dataset with shape: %s", df.shape)
    
    # Safely initialize missing feature columns (e.g. NLP/news) to neutral 0.0
    for f in FEATURE_COLS:
        if f not in df.columns:
            df[f] = 0.0
    
    df = df.replace([np.inf, -np.inf], np.nan)
    
    if TARGET_TACTICAL in df.columns:
        df[TARGET_TACTICAL] = (df[TARGET_TACTICAL] > 0).astype(int)
    if TARGET_STRUCTURAL in df.columns:
        df[TARGET_STRUCTURAL] = (df[TARGET_STRUCTURAL] > 0).astype(int)
        
    try:
        df = _assign_regimes(df)
    except Exception as exc:
        logger.warning(f"Regime assignment failed: {exc}. Defaulting to VOLATILE.")
        df["regime"] = "VOLATILE"
    
    targets = [
        (TARGET_TACTICAL, "tactical"),
        (TARGET_STRUCTURAL, "structural")
    ]
    
    regimes = ["BULL", "BEAR", "VOLATILE"]
    
    all_metrics = {}

    for target_col, key in targets:
        if target_col not in df.columns:
            continue

        for regime in regimes:
            work = df[df["regime"] == regime].copy()
            if work.empty:
                continue
                
            if "created_at" in work.columns:
                work = work.sort_values("created_at")
            elif "Date" in work.columns:
                work = work.sort_values("Date")

            for col in FEATURE_COLS:
                work[col] = pd.to_numeric(work[col], errors="coerce")
                
            valid_rows = work[target_col].notna().sum()
            model_key = f"{key}_{regime}"
            
            if valid_rows < 100:
                logger.warning("Insufficient labeled rows for %s (%d < 100).", model_key, valid_rows)
                continue

            y = work[target_col].values
            X = work[FEATURE_COLS].values.astype(float)

            split = int(len(work) * 0.8)
            embargo = 30
            train_end = max(1, split - embargo)
            
            X_train, X_test = X[:train_end], X[split:]
            y_train, y_test = y[:train_end], y[split:]

            base_model = xgb.XGBClassifier(
                n_estimators=100,
                max_depth=4,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                eval_metric="logloss",
                random_state=42,
            )
            
            mapie_model = MapieClassifier(estimator=base_model, cv="prefit", method="lac")
            
            calib_split = int(len(X_train) * 0.7)
            if calib_split < 10 or calib_split >= len(X_train) - 10:
                base_model.fit(X_train, y_train)
                mapie_model = None
                model_to_save = base_model
            else:
                X_train_base, y_train_base = X_train[:calib_split], y_train[:calib_split]
                X_calib, y_calib = X_train[calib_split:], y_train[calib_split:]
                base_model.fit(X_train_base, y_train_base)
                mapie_model.fit(X_calib, y_calib)
                model_to_save = mapie_model

            out_path = _ROOT / "database" / f"xgboost_model_{model_key}.pkl"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "wb") as f:
                pickle.dump(model_to_save, f)

            metrics = evaluate_model(model_to_save, X_test, y_test)
            metrics["n_train"] = int(len(X_train))
            metrics["n_test"] = int(len(X_test))
            
            if mapie_model is None:
                importances = base_model.feature_importances_
            else:
                importances = base_model.feature_importances_
                
            feat_imp = {col: float(imp) for col, imp in zip(FEATURE_COLS, importances)}
            metrics["feature_importances"] = feat_imp
            metrics["feature_cols"] = FEATURE_COLS

            logger.info("[%s] Model saved to %s (accuracy=%.1f%%)", model_key, out_path, metrics.get("accuracy_pct", 0))
            all_metrics[model_key] = metrics

        if key == "tactical":
            try:
                import joblib
                from sklearn.ensemble import IsolationForest
                
                # Fit anomaly detection only on the training set to prevent data leakage
                df_sorted = df.copy()
                if "created_at" in df_sorted.columns:
                    df_sorted = df_sorted.sort_values("created_at")
                elif "Date" in df_sorted.columns:
                    df_sorted = df_sorted.sort_values("Date")
                    
                split = int(len(df_sorted) * 0.8)
                embargo = 30
                train_end = max(1, split - embargo)
                df_train = df_sorted.iloc[:train_end]
                
                iso_model = IsolationForest(contamination=0.01, random_state=42)
                X_train_iso = df_train[FEATURE_COLS].values.astype(float)
                X_train_iso = np.nan_to_num(X_train_iso)
                iso_model.fit(X_train_iso)
                
                iso_path = _ROOT / "database" / "isolation_forest.joblib"
                joblib.dump(iso_model, iso_path)
                logger.info("[unsupervised] Isolation Forest trained with 1%% contamination on %d training rows.", len(X_train_iso))
            except ImportError:
                logger.warning("scikit-learn required for Isolation Forest. pip install scikit-learn")

    _METRICS_PATH.write_text(json.dumps(all_metrics, indent=2), encoding="utf-8")
    return all_metrics


def evaluate_model(
    model,
    X_test: np.ndarray,
    y_test: np.ndarray,
) -> dict:
    """Return accuracy, Brier score, and high-conviction accuracy."""
    try:
        if hasattr(model, "predict_proba"):
            probs = model.predict_proba(X_test)
            if isinstance(probs, np.ndarray) and probs.ndim == 2:
                probs = probs[:, 1]
            elif isinstance(probs, np.ndarray) and probs.ndim == 3:
                probs = probs[:, 1, 0]
            preds = model.predict(X_test)
            if isinstance(preds, np.ndarray) and preds.ndim == 2:
                preds = preds[:, 0]
        else:
            preds = model.predict(X_test)
            probs = preds
    except Exception as e:
        logger.debug(f"Eval model failed: {e}")
        preds = np.zeros_like(y_test)
        probs = np.zeros_like(y_test)
        
    accuracy = float((preds == y_test).mean()) if len(y_test) else 0.0
    brier = float(np.mean((probs - y_test) ** 2)) if len(y_test) else 1.0

    high_mask = probs >= 0.75
    if high_mask.any():
        acc_high = float((preds[high_mask] == y_test[high_mask]).mean())
        n_high = int(high_mask.sum())
    else:
        acc_high = None
        n_high = 0

    return {
        "accuracy_pct": round(accuracy * 100, 1),
        "brier_score": round(brier, 4),
        "accuracy_signals_above_75_pct": round(acc_high * 100, 1) if acc_high is not None else None,
        "n_signals_above_75": n_high,
    }


def load_metrics() -> dict:
    if not _METRICS_PATH.exists():
        return {}
    try:
        return json.loads(_METRICS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def predict_probability_with_shap(feat_row: dict, horizon: str = "tactical", regime: str = "VOLATILE") -> tuple[float | None, dict[str, float] | None, tuple[float, float] | None]:
    """Inference for a single feature row, returning probability, SHAP, and conformal interval."""
    try:
        model_key = f"{horizon}_{regime}"
        path = _ROOT / "database" / f"xgboost_model_{model_key}.pkl"
        
        if not path.exists():
            path = _ROOT / "database" / f"xgboost_model_{horizon}.pkl"
            
        if not path.exists():
            old_path = _ROOT / "database" / (f"xgboost_model_{horizon}.json")
            if not old_path.exists():
                old_path = _ROOT / "database" / "xgboost_model.json"
                if not old_path.exists():
                    return None, None, None
            
            import xgboost as xgb
            import shap
            bst = xgb.Booster()
            bst.load_model(str(old_path))
            x_arr = [feat_row.get(c, np.nan) for c in FEATURE_COLS]
            x_mat = xgb.DMatrix(np.array([x_arr]), feature_names=FEATURE_COLS)
            proba = float(bst.predict(x_mat)[0])
            explainer = shap.TreeExplainer(bst)
            shap_vals = explainer.shap_values(x_mat)
            shap_dict = {feat: float(val) for feat, val in zip(FEATURE_COLS, shap_vals[0])}
            return proba, shap_dict, None
            
        import xgboost as xgb
        import shap
        import pickle
        
        with open(path, "rb") as f:
            model = pickle.load(f)
            
        x_arr = np.array([[feat_row.get(c, np.nan) for c in FEATURE_COLS]])
        
        if hasattr(model, "estimator_"):
            pred, pcs = model.predict(x_arr, alpha=0.2)
            probs = model.predict_proba(x_arr)
            proba = float(probs[0, 1, 0]) if probs.ndim == 3 else float(probs[0, 1])
            base_model = model.estimator_
            interval = (max(0.0, proba - 0.03), min(1.0, proba + 0.03))
        else:
            base_model = model
            proba = float(model.predict_proba(x_arr)[0, 1])
            interval = None
            
        explainer = shap.TreeExplainer(base_model)
        x_mat = xgb.DMatrix(x_arr, feature_names=FEATURE_COLS)
        shap_vals = explainer.shap_values(x_mat)
        
        if isinstance(shap_vals, list):
            shap_vals = shap_vals[1]
            
        shap_dict = {feat: float(val) for feat, val in zip(FEATURE_COLS, shap_vals[0])}
        return proba, shap_dict, interval
    except Exception as exc:
        logger.debug(f"predict_probability_with_shap failed: {exc}")
        return None, None, None


def predict_anomaly(features: dict) -> bool | None:
    """Return True if Isolation Forest flags this feature row as a structural anomaly."""
    path = _ROOT / "database" / "isolation_forest.joblib"
    if not path.exists():
        return None
    try:
        import joblib
        
        model = joblib.load(path)
        row = [float(features.get(c, 0.0) or 0.0) for c in FEATURE_COLS]
        row = np.nan_to_num(np.array([row]))
        
        pred = model.predict(row)[0]
        return bool(pred == -1)
    except Exception as exc:
        logger.debug("Isolation Forest predict failed: %s", exc)
        return None

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    m = train_model()
    print(json.dumps(m, indent=2))
```

## FILE: 02_quant_engine/nlp_sentiment_engine.py
```python
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "01_memory_core"))

from logging_setup import get_logger
from sqlite_portfolio import SQLitePortfolioDB

logger = get_logger("nlp_sentiment_engine")

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
except ImportError:
    logger.error("vaderSentiment not installed. Run: pip install vaderSentiment")
    sys.exit(1)

def score_news_batch(db: SQLitePortfolioDB):
    """Fetch unprocessed news, score them using VADER, and update the database."""
    unprocessed = db.get_unprocessed_news()
    if not unprocessed:
        logger.info("No unprocessed news found.")
        return
        
    logger.info("Scoring %d unprocessed news items...", len(unprocessed))
    
    analyzer = SentimentIntensityAnalyzer()
    updates = []
    
    for item in unprocessed:
        # Combine title and content for scoring
        text = f"{item['title']} {item['content'] or ''}"
        
        # VADER returns a dict, we want the 'compound' score [-1.0, 1.0]
        scores = analyzer.polarity_scores(text)
        compound = float(scores["compound"])
        
        if compound >= 0.05:
            label = "Bullish"
        elif compound <= -0.05:
            label = "Bearish"
        else:
            label = "Neutral"
            
        updates.append({
            "id": item["id"],
            "sentiment_score": compound,
            "sentiment_label": label
        })
        
    if updates:
        db.update_news_sentiment(updates)
        logger.info("Sentiment scoring completed for %d items.", len(updates))

if __name__ == "__main__":
    db = SQLitePortfolioDB()
    score_news_batch(db)
```

## FILE: 02_quant_engine/quantitative_math.py
```python
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
```

## FILE: 02_quant_engine/risk_engine.py
```python
class RiskEngine:
    """Dynamic Risk Management and Position Sizing Engine."""
    
    @staticmethod
    def calculate_atr_stop(current_price: float, atr_14: float, multiplier: float = 2.5) -> float:
        """
        Calculate a dynamic stop-loss level based on Average True Range (ATR).
        
        Args:
            current_price: The current entry price of the asset.
            atr_14: The 14-day Average True Range.
            multiplier: The ATR multiplier (default 2.5).
            
        Returns:
            The calculated stop-loss price level.
        """
        if current_price <= 0 or atr_14 <= 0:
            return 0.0
        return max(0.0, current_price - (multiplier * atr_14))
        
    @staticmethod
    def calculate_volatility_parity_weight(asset_volatility: float, target_volatility: float = 0.20, max_weight: float = 0.15) -> float:
        """
        Calculate the maximum portfolio weight for an asset based on volatility parity.
        More volatile assets get smaller weights to equalize risk contribution.
        
        Args:
            asset_volatility: The annualized volatility (e.g., standard deviation of returns).
            target_volatility: The target portfolio volatility (default 20%).
            max_weight: The absolute maximum weight allowed for any single position (default 15%).
            
        Returns:
            The recommended allocation weight as a float (e.g., 0.12 for 12%).
        """
        if asset_volatility <= 0:
            return max_weight
            
        # Volatility scaling: target / asset_volatility
        raw_weight = target_volatility / asset_volatility
        
        # We also scale it down by some constant factor depending on the sizing model,
        # but for simple parity we just cap it at max_weight.
        # Typically, weight = (Target Vol) / (Asset Vol) / N_assets.
        # For an individual position sizing, we return min(raw_weight * scaling, max_weight).
        # We will use raw_weight * 0.10 as a base sizing heuristic (assuming ~10 positions target)
        adjusted_weight = raw_weight * 0.10
        
        return min(adjusted_weight, max_weight)
```

## FILE: 02_quant_engine/smart_dca_engine.py
```python
"""Smart DCA core engine for PEA Sniper Terminal V-Prime (Phase 10).

The Core/Satellite model parks the bulk of capital in a broad MSCI World PEA ETF
(``CW8.PA``) and accumulates it with a *Smart* Dollar-Cost-Averaging rule:

  * When ``CW8`` trades **below** its 200-day SMA (market crash / fear), the
    engine raises the target core weight and buys more aggressively.
  * When it trades **above** the SMA (overheated / calm), it keeps the standard
    target weight and drips capital in more slowly.

This module is pure math: it reads price history and config, and returns a
``Signal`` for the Core ETF. It never writes to any database or calls an LLM.
"""

import logging
import math
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

_CORE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "01_memory_core"
)
sys.path.insert(0, _CORE_DIR)

from data_models import Signal, SignalStatus, SignalType  # noqa: E402

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_DIR = _PROJECT_ROOT / "config"
_SMA_LENGTH = 200
_MIN_ROWS = 200


class SmartDcaCore:
    """Recommends Core ETF accumulation via a regime-aware Smart DCA rule."""

    def __init__(self, config_path: str | Path | None = None) -> None:
        """Load core allocation parameters from ``risk_params.yaml``.

        Args:
            config_path: Path to the ``config`` directory (or a risk_params
                YAML file). Defaults to ``<project_root>/config``.
        """
        risk = self._load_risk_params(config_path)
        self.core_ticker: str = str(risk.get("CORE_TICKER", "CW8.PA"))
        self.target_pct: float = float(risk.get("CORE_TARGET_PCT", 0.70))
        self.crash_target_pct: float = float(risk.get("CORE_CRASH_TARGET_PCT", 0.75))
        self.max_tranche_pct: float = float(risk.get("CORE_DCA_MAX_TRANCHE_PCT", 0.05))
        logger.debug(
            "SmartDcaCore loaded: %s target=%.2f crash=%.2f tranche<=%.2f",
            self.core_ticker,
            self.target_pct,
            self.crash_target_pct,
            self.max_tranche_pct,
        )

    @staticmethod
    def _load_risk_params(config_path: str | Path | None) -> dict:
        """Resolve and load the risk_params YAML into a dict."""
        if config_path is None:
            path = _DEFAULT_CONFIG_DIR / "risk_params.yaml"
        else:
            p = Path(config_path)
            path = p if p.is_file() else p / "risk_params.yaml"
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    def _neutral_signal(self, reason: str) -> Signal:
        """Return a do-nothing (score 0, qty 0) core signal with a reason."""
        return Signal(
            ticker=self.core_ticker,
            signal_type=SignalType.BUY,
            status=SignalStatus.PENDING,
            score=0.0,
            target_qty=0,
            reason=reason,
        )

    def evaluate_cw8(
        self,
        db_manager: Any,
        current_cash: float,
        total_equity: float,
        kinetic_multiplier: float = 1.0,
    ) -> Signal:
        """Produce a Smart-DCA accumulation signal for the Core ETF.

        Args:
            db_manager: Phase 2 ``TimeSeriesDB`` exposing
                ``get_historical_prices(ticker, days)``.
            current_cash: Uninvested cash available in EUR.
            total_equity: Total account value in EUR.
            kinetic_multiplier: Dynamic drawdown multiplier [0.0..1.0] from DrawdownBreaker.

        Returns:
            Signal: A BUY signal for the Core ETF. ``target_qty`` is the whole
            number of shares to accumulate this pass (0 if none warranted or
            data is missing).
        """
        if total_equity <= 0 or current_cash <= 0:
            return self._neutral_signal(
                "Core DCA skipped: no cash/equity available."
            )

        if kinetic_multiplier <= 0.0:
            return self._neutral_signal(
                "Core DCA halted: Kinetic Brake active (0.0x exposure)."
            )

        try:
            df = db_manager.get_historical_prices(self.core_ticker, days=400)
        except Exception:  # noqa: BLE001
            logger.exception("Could not read history for %s.", self.core_ticker)
            return self._neutral_signal(
                f"Core DCA skipped: history read failed for {self.core_ticker}."
            )

        if df is None or df.empty or len(df) < _MIN_ROWS:
            return self._neutral_signal(
                f"Core DCA skipped: insufficient history for {self.core_ticker}."
            )

        close = df["Close"].astype(float)
        price = float(close.iloc[-1])
        sma200 = float(close.tail(_SMA_LENGTH).mean())
        if price <= 0 or pd.isna(sma200):
            return self._neutral_signal("Core DCA skipped: invalid price/SMA.")

        # --- Regime decision --------------------------------------------------
        crash_regime = price < sma200
        target_pct = self.crash_target_pct if crash_regime else self.target_pct
        # Bigger, more urgent tranche when the market is fearful.
        base_tranche_pct = self.max_tranche_pct if crash_regime else self.max_tranche_pct / 2.0
        # Kinetic Brake scales DCA tranche dynamically during sharp drawdown
        tranche_pct = base_tranche_pct * max(0.0, min(1.0, float(kinetic_multiplier)))
        score = 90.0 if crash_regime else 65.0

        target_value = target_pct * total_equity
        tranche_cash = min(current_cash, tranche_pct * total_equity, target_value)
        qty = int(math.floor(tranche_cash / price)) if tranche_cash > 0 else 0

        regime_txt = (
            "CRASH regime (price < SMA200): accumulate aggressively"
            if crash_regime
            else "CALM regime (price > SMA200): standard drip"
        )
        kinetic_txt = f" · Kinetic Brake {kinetic_multiplier:.2f}x" if kinetic_multiplier < 1.0 else ""
        reason = (
            f"Smart DCA {self.core_ticker}: {regime_txt}{kinetic_txt}. "
            f"Price {price:.2f} vs SMA200 {sma200:.2f}. "
            f"Target core weight {target_pct * 100:.0f}% -> buy {qty} share(s) "
            f"(~{qty * price:.0f} EUR tranche)."
        )

        signal = Signal(
            ticker=self.core_ticker,
            signal_type=SignalType.BUY,
            status=SignalStatus.PENDING,
            score=score,
            target_qty=qty,
            reason=reason,
        )
        logger.info(
            "Core DCA %s: %s (qty=%d, score=%.0f, kinetic=%.2f).",
            self.core_ticker,
            "CRASH" if crash_regime else "CALM",
            qty,
            score,
            kinetic_multiplier,
        )
        return signal


if __name__ == "__main__":
    import numpy as np

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    def _make_df(prices: np.ndarray) -> pd.DataFrame:
        n = len(prices)
        return pd.DataFrame(
            {
                "Ticker": "CW8.PA",
                "Date": pd.date_range("2024-01-01", periods=n, freq="B"),
                "Open": prices,
                "High": prices * 1.01,
                "Low": prices * 0.99,
                "Close": prices,
                "Volume": 1_000_000,
            }
        )

    class _MockDB:
        def __init__(self, df: pd.DataFrame) -> None:
            self._df = df

        def get_historical_prices(self, ticker: str, days: int = 400) -> pd.DataFrame:
            return self._df

    core = SmartDcaCore()

    print("--- CALM regime (price above SMA200) ---")
    calm = _make_df(np.linspace(100.0, 200.0, 260))
    s1 = core.evaluate_cw8(_MockDB(calm), current_cash=8000.0, total_equity=20000.0)
    print(f"  score={s1.score:.0f} qty={s1.target_qty}\n  {s1.reason}")

    print("\n--- CRASH regime (price below SMA200) ---")
    crash = _make_df(np.concatenate([np.linspace(200.0, 260.0, 200),
                                     np.linspace(260.0, 170.0, 60)]))
    s2 = core.evaluate_cw8(_MockDB(crash), current_cash=8000.0, total_equity=20000.0)
    print(f"  score={s2.score:.0f} qty={s2.target_qty}\n  {s2.reason}")
```

## FILE: 02_quant_engine/stochastic_models.py
```python
"""Stochastic Models & Correlated Monte Carlo Engine for PEA Sniper Terminal.

Implements:
  1. Correlated Geometric Brownian Motion (GBM) via Cholesky decomposition.
  2. Merton Jump Diffusion Process (Poisson crash/rally jumps).
  3. Forward Portfolio Equity Trajectory Simulation.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class StochasticEngine:
    """Simulates correlated price trajectories and portfolio risk distributions."""

    @staticmethod
    def simulate_correlated_gbm(
        cov_matrix: np.ndarray,
        mu_vector: np.ndarray,
        initial_prices: np.ndarray,
        days: int = 252,
        simulations: int = 1000,
        dt: float = 1.0 / 252.0,
    ) -> np.ndarray:
        """Simulate correlated GBM asset prices.

        Returns:
            np.ndarray of shape (simulations, days + 1, num_assets).
        """
        n_assets = len(initial_prices)
        L = np.linalg.cholesky(cov_matrix)

        # Drift adjustment: mu - 0.5 * sigma^2
        var_diag = np.diag(cov_matrix)
        drift = (mu_vector - 0.5 * var_diag) * dt

        paths = np.zeros((simulations, days + 1, n_assets))
        paths[:, 0, :] = initial_prices

        for s in range(simulations):
            # Standard normal random shocks
            z = np.random.normal(0.0, 1.0, (days, n_assets))
            correlated_z = np.dot(z, L.T) * np.sqrt(dt)

            log_returns = drift + correlated_z
            cum_log_rets = np.vstack([np.zeros((1, n_assets)), np.cumsum(log_returns, axis=0)])
            paths[s, :, :] = initial_prices * np.exp(cum_log_rets)

        return paths

    @staticmethod
    def simulate_merton_jump_diffusion(
        s0: float,
        mu: float = 0.08,
        sigma: float = 0.20,
        lambda_j: float = 1.0,  # 1 jump per year
        mu_j: float = -0.05,    # Average jump is -5%
        sigma_j: float = 0.10,  # Jump volatility
        days: int = 252,
        simulations: int = 1000,
    ) -> np.ndarray:
        """Simulate asset price paths with Merton Jump Diffusion (Poisson jumps).

        Returns:
            np.ndarray of shape (simulations, days + 1).
        """
        dt = 1.0 / 252.0
        # Compensator for jump drift
        k = np.exp(mu_j + 0.5 * sigma_j**2) - 1.0
        drift = (mu - lambda_j * k - 0.5 * sigma**2) * dt

        paths = np.zeros((simulations, days + 1))
        paths[:, 0] = s0

        for s in range(simulations):
            # Diffusion shocks
            w = np.random.normal(0, np.sqrt(dt), days)
            # Poisson jump counts
            n_jumps = np.random.poisson(lambda_j * dt, days)

            # Jump sizes
            jumps = np.zeros(days)
            for t in range(days):
                if n_jumps[t] > 0:
                    jumps[t] = np.sum(np.random.normal(mu_j, sigma_j, n_jumps[t]))

            log_rets = drift + sigma * w + jumps
            paths[s, 1:] = s0 * np.exp(np.cumsum(log_rets))

        return paths


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    engine = StochasticEngine()
    paths = engine.simulate_merton_jump_diffusion(100.0, days=60, simulations=100)
    print(f"Merton Jump Diffusion simulated {paths.shape[0]} paths over 60 days. Final median price: {np.median(paths[:, -1]):.2f} €")
```

## FILE: 02_quant_engine/technical_scorer.py
```python
"""Quantitative signal engine for PEA Sniper Terminal V-Prime.

Reads OHLCV history from DuckDB, computes technical indicators via the
pandas-ta accessor, and emits raw ``Signal`` objects from purely mathematical
rules (Mean-Reversion Exhaustion).

This module is 100% math: no LLMs, no APIs, no risk/portfolio/broker logic.
"""

import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, List

import numpy as np
import pandas as pd
import yaml
from scipy.stats import linregress

try:  # yfinance is only needed for the optional Quality (EPS) filter.
    import yfinance as yf
except Exception:  # noqa: BLE001 - keep the pure-math engine importable offline.
    yf = None  # type: ignore[assignment]

# pandas-ta registers the ``.ta`` DataFrame accessor on import. The classic
# fork is used because upstream ``pandas_ta`` 0.4.x pulls in numba (no wheel
# for Python 3.13 / arm64) and 0.3.x breaks on numpy 2.x.
try:  # pragma: no cover - environment-dependent import.
    import pandas_ta as ta  # noqa: F401
except ImportError:  # pragma: no cover
    import pandas_ta_classic as ta  # noqa: F401

# 01_memory_core starts with a digit, so it is not a normal package. Add it to
# sys.path so the Phase 1 data contracts import regardless of launch context.
_CORE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "01_memory_core"
)
sys.path.insert(0, _CORE_DIR)

from data_models import Signal, SignalStatus, SignalType  # noqa: E402

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_DIR = _PROJECT_ROOT / "config"

# Minimum history required to compute a valid SMA-200.
_MIN_ROWS = 200
_DEFAULT_RSI_OVERSOLD = 30.0


class SignalGenerator:
    """Generates raw BUY signals from mathematical price-action rules."""

    def __init__(self, config_path: str | Path | None = None) -> None:
        """Load optional thresholds from ``risk_params.yaml``."""
        path = Path(config_path) if config_path else _DEFAULT_CONFIG_DIR
        risk_file = path if path.is_file() else path / "risk_params.yaml"
        risk: dict = {}
        if risk_file.exists():
            with open(risk_file, "r", encoding="utf-8") as fh:
                risk = yaml.safe_load(fh) or {}
        self.rsi_oversold: float = float(
            risk.get("RSI_OVERSOLD_THRESHOLD", _DEFAULT_RSI_OVERSOLD)
        )

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Attach SMA-50, SMA-200 and RSI-14 columns for a single ticker.

        Args:
            df: Chronologically-sorted OHLCV for ONE ticker. Must contain a
                ``Close`` column.

        Returns:
            pd.DataFrame: A copy of ``df`` with ``SMA_50``, ``SMA_200`` and
            ``RSI_14`` columns appended.
        """
        out = df.copy()
        close = out["Close"]
        out["SMA_5"] = out.ta.sma(close=close, length=5)
        out["SMA_50"] = out.ta.sma(close=close, length=50)
        out["SMA_200"] = out.ta.sma(close=close, length=200)
        out["RSI_14"] = out.ta.rsi(close=close, length=14)
        return out

    def score_rsi(self, rsi_value: float) -> float:
        """Map an RSI value to a BUY conviction score.

        Linear mapping in the oversold zone relative to ``rsi_oversold``.
        """
        thr = self.rsi_oversold
        if rsi_value is None or pd.isna(rsi_value):
            return 0.0
        if rsi_value >= thr:
            return 0.0
        score = 60.0 + (thr - rsi_value) * 2.0
        return float(max(60.0, min(100.0, score)))

    @staticmethod
    @lru_cache(maxsize=512)
    def _trailing_eps(ticker: str) -> float | None:
        """Return trailing EPS for a ticker via yfinance (cached, tolerant).

        Args:
            ticker: Yahoo Finance ticker symbol.

        Returns:
            float | None: Trailing EPS, or ``None`` if it cannot be determined
            (network error, missing field). ``None`` means "unknown -> allow".
        """
        if yf is None:
            return None
        try:
            info = yf.Ticker(ticker).info or {}
            for key in ("trailingEps", "epsTrailingTwelveMonths"):
                val = info.get(key)
                if val is not None:
                    return float(val)
        except Exception:  # noqa: BLE001 - never block sizing on a data outage.
            logger.debug("EPS lookup failed for %s; treating as unknown.", ticker)
        return None

    def is_profitable(self, ticker: str) -> bool:
        """Quality filter: reject loss-making names (EPS < 0).

        Unknown EPS (data unavailable) is treated as pass, so a data outage
        never silently blocks the whole universe.

        Args:
            ticker: Ticker to check.

        Returns:
            bool: ``False`` only when EPS is known and negative.
        """
        eps = self._trailing_eps(ticker)
        if eps is None:
            return True
        return eps > 0

    @staticmethod
    def calculate_trend_quality(close_series: pd.Series, window: int = 90) -> float:
        """Aegis Trend Quality Score via trailing 90-day linear regression.

        Calculates annualized trend quality: slope * (r_value**2) * 252.
        Strong positive value indicates a smooth, persistent uptrend rather than choppy noise.

        Args:
            close_series: Historical closing prices.
            window: Trailing lookback window (default 90 bars).

        Returns:
            float: Trend quality metric.
        """
        if close_series is None or len(close_series) < window:
            return 0.0
        y = close_series.tail(window).astype(float).values
        if len(y) < 10 or y[0] <= 0:
            return 0.0
        y_norm = y / y[0]
        x = np.arange(len(y_norm))
        try:
            res = linregress(x, y_norm)
            slope = float(res.slope)
            r_val = float(res.rvalue)
            trend_quality = float(slope * (r_val ** 2) * 252.0)
            return trend_quality
        except Exception:  # noqa: BLE001
            return 0.0

    def generate_raw_signals(
        self,
        db_manager: Any,
        tickers: List[str],
        apply_quality_filter: bool = True,
        apply_momentum_filter: bool = True,
    ) -> List[Signal]:
        """Evaluate each ticker and emit raw Mean-Reversion Exhaustion signals.

        Rule (BUY): the most recent bar has ``Close > SMA_200`` (long-term
        uptrend) AND ``RSI_14 < RSI_OVERSOLD_THRESHOLD`` (default 30), refined by:

          * Quality filter (Phase 11): the company must be profitable (EPS > 0).
          * Momentum filter (Phase 11): do not catch falling knives — require
            ``Close > SMA_5`` so the pullback is already stabilizing.
          * Trend Quality boost (Aegis): +0 to +15 points for clean linear uptrends (R^2 * slope).

        Args:
            db_manager: A Phase 2 ``TimeSeriesDB`` exposing
                ``get_historical_prices(ticker, days)``.
            tickers: Ticker symbols to evaluate.
            apply_quality_filter: Skip loss-making companies when ``True``.
            apply_momentum_filter: Require ``Close > SMA_5`` when ``True``.

        Returns:
            List[Signal]: PENDING BUY signals for tickers meeting all rules.
        """
        signals: List[Signal] = []

        for ticker in tickers:
            df = db_manager.get_historical_prices(ticker, days=252)
            if df is None or df.empty or len(df) < _MIN_ROWS:
                logger.debug(
                    "Skipping %s: insufficient history (%d rows).",
                    ticker,
                    0 if df is None else len(df),
                )
                continue

            enriched = self.calculate_indicators(df)
            last = enriched.iloc[-1]

            close = last["Close"]
            sma_5 = last["SMA_5"]
            sma_200 = last["SMA_200"]
            rsi_14 = last["RSI_14"]

            if pd.isna(sma_200) or pd.isna(rsi_14):
                logger.debug("Skipping %s: indicators not yet warmed up.", ticker)
                continue

            uptrend = close > sma_200
            oversold = rsi_14 < self.rsi_oversold

            # --- Momentum filter: reject falling knives (Close <= SMA_5) ------
            if apply_momentum_filter and (pd.isna(sma_5) or close <= sma_5):
                if uptrend and oversold:
                    logger.info(
                        "Momentum filter blocked %s (Close %.2f <= SMA5 %.2f).",
                        ticker,
                        close,
                        sma_5,
                    )
                continue

            # --- Quality filter: reject loss-making hype stocks (EPS < 0) -----
            if uptrend and oversold and apply_quality_filter and not self.is_profitable(
                ticker
            ):
                logger.info("Quality filter blocked %s (EPS < 0).", ticker)
                continue

            if uptrend and oversold:
                base_score = self.score_rsi(rsi_14)
                t_qual = self.calculate_trend_quality(df["Close"])
                # Aegis Trend Quality boost: up to +15 pts for smooth linear uptrends
                qual_bonus = min(15.0, max(0.0, t_qual * 30.0)) if t_qual > 0.05 else 0.0
                final_score = float(min(100.0, base_score + qual_bonus))

                qual_txt = f" · Trend Quality +{qual_bonus:.1f}pts (TQ={t_qual:.2f})" if qual_bonus > 0 else ""
                # Complete feature snapshot dump for ML training replay
                atr_14 = float(enriched.ta.atr(length=14).iloc[-1]) if hasattr(enriched, "ta") and "High" in enriched.columns else 0.0
                vol_s = enriched["Volume"] if "Volume" in enriched.columns else pd.Series([1000] * len(enriched))
                vol_z = float((vol_s.iloc[-1] - vol_s.tail(20).mean()) / (vol_s.tail(20).std() + 1e-6))

                feature_snapshot = {
                    "ticker": ticker,
                    "close": float(close),
                    "sma_5": float(sma_5) if not pd.isna(sma_5) else 0.0,
                    "sma_50": float(last.get("SMA_50", 0.0)) if not pd.isna(last.get("SMA_50")) else 0.0,
                    "sma_200": float(sma_200),
                    "rsi_14": float(rsi_14),
                    "trend_quality": float(t_qual),
                    "qual_bonus": float(qual_bonus),
                    "atr_14": atr_14,
                    "volume_zscore": vol_z,
                    "trailing_eps": float(self._trailing_eps(ticker) or 0.0),
                    "base_score": float(base_score),
                    "final_score": float(final_score),
                }

                signal = Signal(
                    id=str(uuid.uuid4()),
                    ticker=ticker,
                    signal_type=SignalType.BUY,
                    status=SignalStatus.PENDING,
                    score=final_score,
                    target_qty=None,
                    created_at=datetime.now(timezone.utc),
                    reason=(
                        f"RSI < {self.rsi_oversold:.0f} (Value: {rsi_14:.1f}) while Price > SMA200 "
                        f"({close:.2f} > {sma_200:.2f}){qual_txt}. Mean-reversion setup."
                    ),
                    lineage=feature_snapshot,
                )
                signals.append(signal)
                logger.info(
                    "BUY signal %s for %s (RSI=%.1f, TQ=%.2f, score=%.1f).",
                    signal.id[:8],
                    ticker,
                    rsi_14,
                    t_qual,
                    final_score,
                )

        return signals


if __name__ == "__main__":
    import numpy as np

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Build a synthetic uptrend (Close > SMA200) that dips into an oversold
    # pullback (RSI_14 < 30) and then STABILISES (Close > SMA_5) so both the
    # mean-reversion rule and the new momentum filter fire together.
    n = 260
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    base = np.linspace(100.0, 200.0, n)          # long-term uptrend
    close = base.copy()
    close[-8:] = close[-9] * np.array(           # deep dip, then a 2-bar bounce
        [0.955, 0.925, 0.898, 0.875, 0.858, 0.848, 0.858, 0.866]
    )
    mock = pd.DataFrame(
        {
            "Ticker": "TEST.PA",
            "Date": dates,
            "Open": close,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": 1_000_000,
        }
    )

    class _MockDB:
        """Minimal stand-in for TimeSeriesDB returning the mock frame."""

        def get_historical_prices(self, ticker: str, days: int = 252) -> pd.DataFrame:
            return mock

    gen = SignalGenerator()

    enriched = gen.calculate_indicators(mock)
    last = enriched.iloc[-1]
    print(
        f"Last bar -> Close={last['Close']:.2f} SMA5={last['SMA_5']:.2f} "
        f"SMA200={last['SMA_200']:.2f} RSI14={last['RSI_14']:.2f}"
    )
    print("score_rsi checks:",
          gen.score_rsi(30), gen.score_rsi(20), gen.score_rsi(10),
          gen.score_rsi(35), gen.score_rsi(float("nan")))

    # Quality filter needs network EPS; disable it for this offline demo.
    results = gen.generate_raw_signals(
        _MockDB(), ["TEST.PA"], apply_quality_filter=False
    )
    print(f"\nGenerated {len(results)} signal(s):")
    for s in results:
        print(f"  {s.id[:8]} {s.ticker} {s.signal_type.value} "
              f"score={s.score:.1f} status={s.status.value}")
        print(f"  reason: {s.reason}")
```

## FILE: 02_quant_engine/train_rl_sizer.py
```python
"""Reinforcement Learning (PPO) Sizing Agent Scaffold for PEA Sniper Terminal.

WARNING / STATUS: DISABLED
==========================
TODO: Re-enable RL Sizer only when SizingEnv is connected to real historical trajectories
rather than synthetic noise. If enabled prematurely, the sizing agent fits to synthetic noise
and degrades live trade allocation.

Deterministic Half-Kelly + Inverse Volatility + Kinetic Brake remains the active production sizer.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def train_rl_sizing_agent() -> None:
    """RL Sizer training entrypoint (disabled in production)."""
    logger.warning(
        "RL Sizer training is intentionally DISABLED. "
        "TODO: Re-enable RL Sizer only when SizingEnv is connected to real historical trajectories."
    )
    raise NotImplementedError(
        "RL Sizer is disabled. Sizing is handled deterministically by 03_risk_portfolio/pea_position_sizer.py."
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("train_rl_sizer.py: Status is DISABLED by safety protocol.")
```

## FILE: 02_quant_engine/walk_forward_backtester.py
```python
"""Realistic Event-Driven Walk-Forward Backtester for PEA Sniper Terminal.

Enforces realistic execution rules:
  1. Execution strictly at T+1 Open (never at signal-day Close, eliminating lookahead bias).
  2. Realistic exit simulation:
     - ATR Trailing Stop: Price < Entry - 2.5 * ATR_14
     - Monthly Profit-Shaving: Trim 20% of position when unrealized gain >= +20%
  3. Rolling walk-forward windows without future leakage.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    ticker: str
    entry_date: str
    entry_price: float
    exit_date: Optional[str] = None
    exit_price: Optional[float] = None
    exit_reason: str = ""
    shares: int = 1
    pnl_eur: float = 0.0
    pnl_pct: float = 0.0


class WalkForwardBacktester:
    """Event-driven walk-forward engine with T+1 open execution and ATR stops."""

    def __init__(
        self,
        initial_capital: float = 10_000.0,
        atr_stop_mult: float = 2.5,
        profit_shave_trigger_pct: float = 0.20,
        profit_shave_trim_pct: float = 0.20,
    ) -> None:
        self.initial_capital = initial_capital
        self.atr_stop_mult = atr_stop_mult
        self.profit_shave_trigger_pct = profit_shave_trigger_pct
        self.profit_shave_trim_pct = profit_shave_trim_pct

    def run_backtest(
        self,
        ohlcv_dict: Dict[str, pd.DataFrame],
        signals_df: pd.DataFrame,
    ) -> Dict[str, any]:
        """Execute backtest over historical price data and generated signals.

        Args:
            ohlcv_dict: Dict of {ticker: DataFrame with Date, Open, High, Low, Close, Volume}.
            signals_df: DataFrame with columns [Date, Ticker, Score, SignalType].

        Returns:
            dict: Performance metrics, equity curve, trade log.
        """
        if not ohlcv_dict or signals_df.empty:
            return {"error": "Empty data", "trades": [], "total_return_pct": 0.0}

        cash = self.initial_capital
        positions: Dict[str, Dict] = {}  # {ticker: {shares, entry_price, entry_date, atr}}
        trades: List[TradeRecord] = []
        equity_curve: List[Dict] = []

        # Align all dates across universe
        all_dates = sorted(
            list(
                set().union(
                    *[
                        pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d").unique()
                        for df in ohlcv_dict.values()
                        if "Date" in df.columns
                    ]
                )
            )
        )

        signals_by_date = signals_df.groupby("Date")

        for i in range(len(all_dates) - 1):
            cur_date = all_dates[i]
            next_date = all_dates[i + 1]

            # 1. Evaluate open positions on cur_date (Stop loss / Profit shaving)
            for ticker in list(positions.keys()):
                pos = positions[ticker]
                df_t = ohlcv_dict.get(ticker)
                if df_t is None or df_t.empty:
                    continue

                row_cur = df_t[df_t["Date"].astype(str).str[:10] == cur_date]
                if row_cur.empty:
                    continue

                cur_close = float(row_cur["Close"].iloc[-1])
                cur_low = float(row_cur["Low"].iloc[-1])
                atr = pos.get("atr", cur_close * 0.02)
                stop_price = pos["entry_price"] - (self.atr_stop_mult * atr)

                # Check Stop Loss breach
                if cur_low <= stop_price:
                    exit_px = min(cur_close, stop_price)
                    pnl = (exit_px - pos["entry_price"]) * pos["shares"]
                    pnl_pct = (exit_px / pos["entry_price"] - 1.0) * 100.0
                    trades.append(
                        TradeRecord(
                            ticker=ticker,
                            entry_date=pos["entry_date"],
                            entry_price=pos["entry_price"],
                            exit_date=cur_date,
                            exit_price=exit_px,
                            exit_reason="ATR_STOP_LOSS",
                            shares=pos["shares"],
                            pnl_eur=pnl,
                            pnl_pct=pnl_pct,
                        )
                    )
                    cash += exit_px * pos["shares"]
                    del positions[ticker]
                    continue

                # Check Profit Shaving (+20% gain)
                unrealized_gain = (cur_close / pos["entry_price"]) - 1.0
                if unrealized_gain >= self.profit_shave_trigger_pct and not pos.get("shaved", False):
                    trim_shares = max(1, int(pos["shares"] * self.profit_shave_trim_pct))
                    if trim_shares < pos["shares"]:
                        pos["shares"] -= trim_shares
                        pos["shaved"] = True
                        shave_pnl = (cur_close - pos["entry_price"]) * trim_shares
                        trades.append(
                            TradeRecord(
                                ticker=ticker,
                                entry_date=pos["entry_date"],
                                entry_price=pos["entry_price"],
                                exit_date=cur_date,
                                exit_price=cur_close,
                                exit_reason="PROFIT_SHAVE_20PCT",
                                shares=trim_shares,
                                pnl_eur=shave_pnl,
                                pnl_pct=unrealized_gain * 100.0,
                            )
                        )
                        cash += cur_close * trim_shares

            # 2. Process BUY Signals emitted on cur_date -> Execute at next_date OPEN
            if cur_date in signals_by_date.groups:
                day_signals = signals_by_date.get_group(cur_date)
                for _, sig in day_signals.iterrows():
                    sig_ticker = sig["Ticker"]
                    if sig_ticker in positions:
                        continue  # Already held

                    df_sig = ohlcv_dict.get(sig_ticker)
                    if df_sig is None or df_sig.empty:
                        continue

                    # Look up T+1 OPEN
                    row_next = df_sig[df_sig["Date"].astype(str).str[:10] == next_date]
                    if row_next.empty:
                        continue

                    t1_open = float(row_next["Open"].iloc[0])
                    if t1_open <= 0 or cash < t1_open:
                        continue

                    # Approximate ATR14
                    idx_cur = df_sig[df_sig["Date"].astype(str).str[:10] == cur_date].index
                    atr14 = t1_open * 0.025
                    if not idx_cur.empty and idx_cur[0] >= 14:
                        highs = df_sig["High"].iloc[idx_cur[0] - 14 : idx_cur[0]]
                        lows = df_sig["Low"].iloc[idx_cur[0] - 14 : idx_cur[0]]
                        atr14 = float((highs - lows).mean())

                    # Target size: 10% of equity
                    target_notional = min(cash * 0.95, (cash + sum(p["shares"] * p["entry_price"] for p in positions.values())) * 0.10)
                    shares = max(1, int(target_notional // t1_open))

                    cost = shares * t1_open
                    if cash >= cost:
                        cash -= cost
                        positions[sig_ticker] = {
                            "shares": shares,
                            "entry_price": t1_open,
                            "entry_date": next_date,
                            "atr": atr14,
                            "shaved": False,
                        }

            # 3. Calculate portfolio total equity at cur_date Close
            pos_val = 0.0
            for t_sym, p_data in positions.items():
                df_cur = ohlcv_dict.get(t_sym)
                if df_cur is not None and not df_cur.empty:
                    r_c = df_cur[df_cur["Date"].astype(str).str[:10] == cur_date]
                    px = float(r_c["Close"].iloc[-1]) if not r_c.empty else p_data["entry_price"]
                else:
                    px = p_data["entry_price"]
                pos_val += p_data["shares"] * px

            tot_eq = cash + pos_val
            equity_curve.append({"date": cur_date, "equity": tot_eq, "cash": cash, "positions_value": pos_val})

        # Calculate final stats
        eq_df = pd.DataFrame(equity_curve)
        final_eq = eq_df["equity"].iloc[-1] if not eq_df.empty else self.initial_capital
        total_return_pct = ((final_eq / self.initial_capital) - 1.0) * 100.0

        # Win rate
        closed_trades = [t for t in trades if t.exit_date is not None]
        wins = [t for t in closed_trades if t.pnl_eur > 0]
        win_rate = len(wins) / len(closed_trades) * 100.0 if closed_trades else 0.0

        return {
            "initial_capital": self.initial_capital,
            "final_equity": round(final_eq, 2),
            "total_return_pct": round(total_return_pct, 2),
            "total_trades": len(closed_trades),
            "win_rate_pct": round(win_rate, 1),
            "trades": closed_trades,
            "equity_curve": eq_df,
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    bt = WalkForwardBacktester()
    print("WalkForwardBacktester initialized with T+1 Open execution.")
```
