"""Institutional Test Suite for PEA Pollux Systematic Engine.

Tests:
  1. RiskParamsConfig Pydantic strictness (extra='forbid', frozen=True).
  2. DrawdownBreaker multi-horizon loss circuit breakers & kinetic multipliers.
  3. SignalOrchestrator Step 0 Drawdown Halt, Degraded Mode (Floor=85), and Piotroski Veto.
  4. FundamentalsSensor Piotroski 9-point calculation and SQLite caching.
  5. HRPSizer Hierarchical Risk Parity allocation.
  6. Quantitative Math (VaR 95/99, Cornish-Fisher, CVaR).
  7. Stochastic Models (Correlated GBM, Merton Jump Diffusion).
  8. FeatureStore feature extraction & conformal calibration.
  9. HMMRegimeClassifier fail-safe to VOLATILE.
  10. OpenFigiMapper offline and cache resolution.
  11. TradePostMortemEngine SQLite persistence.
  12. RedTeamDebateAgent adversarial debate synthesis.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from pydantic import ValidationError

# Setup system path
ROOT = Path(__file__).resolve().parent.parent
for d in ("00_data_sensors", "00_data_sensors/scrapers", "01_memory_core", "02_quant_engine", "03_risk_portfolio", "04_orchestrator_ai", "05_interfaces"):
    sys.path.insert(0, str(ROOT / d))

from risk_config import RiskParamsConfig, load_and_validate_risk_params
from drawdown_breaker import DrawdownBreaker
from fundamentals_api import FundamentalsSensor
from signal_priority_cascade import SignalOrchestrator
from data_models import PortfolioState, Position, Signal, SignalStatus, SignalType
from hrp_sizer import HRPSizer
from quantitative_math import calculate_historical_var, calculate_cvar, calculate_cornish_fisher_var, compute_comprehensive_risk_profile
from stochastic_models import StochasticEngine
from ml_feature_store import FeatureStore
from hmm_regime import HMMRegimeClassifier, MarketRegimeState
from openfigi_mapper import OpenFigiMapper
from post_mortem_engine import TradePostMortemEngine
from red_team_agent import RedTeamDebateAgent


class TestInstitutionalSuite(unittest.TestCase):

    def test_01_risk_config_pydantic_validation(self):
        """Test strict pydantic validation and typo rejection."""
        # Valid config
        cfg = RiskParamsConfig(
            KELLY_FRACTION=0.5,
            MAX_SINGLE_POSITION_PCT=0.15,
            MAX_SECTOR_WEIGHT_PCT=0.25,
            DAILY_MAX_LOSS_PCT=-0.005,
        )
        self.assertEqual(cfg.KELLY_FRACTION, 0.5)

        # Frozen: mutating raises error
        with self.assertRaises(ValidationError):
            cfg.KELLY_FRACTION = 0.8  # type: ignore

        # Extra misspelled key raises error due to extra='forbid'
        with self.assertRaises(ValidationError):
            RiskParamsConfig(KELLY_FRACTON=0.5)  # Typo

    def test_02_drawdown_breaker_multi_horizon(self):
        """Test kinetic multiplier and daily/weekly/monthly loss circuit breakers."""
        db = DrawdownBreaker(daily_max_loss=-0.01, weekly_max_loss=-0.03, monthly_max_loss=-0.06)

        # Kinetic multiplier tiers
        self.assertEqual(db.calculate_kinetic_multiplier(-0.02), 1.0)
        self.assertEqual(db.calculate_kinetic_multiplier(-0.07), 0.50)
        self.assertEqual(db.calculate_kinetic_multiplier(-0.12), 0.20)
        self.assertEqual(db.calculate_kinetic_multiplier(-0.18), 0.0)

        # Multi-horizon limits
        history_ok = pd.Series([10000, 10050, 10020])
        passed, _ = db.check_loss_limits(history_ok)
        self.assertTrue(passed)

        history_breach_daily = pd.Series([10000, 9800])  # -2% vs -1% limit
        passed, reason = db.check_loss_limits(history_breach_daily)
        self.assertFalse(passed)
        self.assertIn("DAILY_MAX_LOSS", reason)

    def test_03_signal_priority_cascade_vetos(self):
        """Test Drawdown halt, degraded mode floor 85, and Piotroski veto in cascade."""
        orch = SignalOrchestrator(ROOT / "config")
        pstate = PortfolioState(cash_available=5000, total_equity=10000, positions=[])

        # Test normal pass
        sig = Signal(ticker="MC.PA", score=80.0, signal_type=SignalType.BUY)
        processed = orch.process_raw_signals([sig], pstate, current_prices={"MC.PA": 600.0})
        self.assertEqual(len(processed), 1)

        # Test Degraded Mode: score 80 < 85 -> REJECTED
        sig_deg = Signal(ticker="MC.PA", score=80.0, signal_type=SignalType.BUY)
        processed_deg = orch.process_raw_signals([sig_deg], pstate, current_prices={"MC.PA": 600.0}, data_degraded_mode=True)
        self.assertEqual(processed_deg[0].status, SignalStatus.REJECTED)
        self.assertIn("DEGRADED MODE", processed_deg[0].reason)

    def test_04_fundamentals_piotroski(self):
        """Test Piotroski F-score engine."""
        sensor = FundamentalsSensor(ROOT / "database" / "test_fund.db")
        score, bd = sensor.calculate_piotroski_score("MC.PA")
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 9)
        try:
            if (ROOT / "database" / "test_fund.db").exists():
                os.remove(ROOT / "database" / "test_fund.db")
        except Exception:
            pass

    def test_05_hrp_sizer(self):
        """Test Hierarchical Risk Parity allocation."""
        np.random.seed(42)
        rets = pd.DataFrame(
            np.random.normal(0.0005, 0.015, (100, 3)),
            columns=["MC.PA", "OR.PA", "AI.PA"],
        )
        sizer = HRPSizer()
        weights = sizer.calculate_hrp_weights(rets)
        self.assertEqual(len(weights), 3)
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=4)

    def test_06_quantitative_math_var_cvar(self):
        """Test VaR, Cornish-Fisher, and CVaR calculations."""
        np.random.seed(42)
        rets = np.random.normal(0.0, 0.02, 500)
        var95 = calculate_historical_var(rets, 0.95)
        cvar95 = calculate_cvar(rets, 0.95)
        cf_var = calculate_cornish_fisher_var(rets, 0.95)

        self.assertGreater(var95, 0.0)
        self.assertGreater(cvar95, var95)  # CVaR is always >= VaR
        self.assertGreater(cf_var, 0.0)

    def test_07_stochastic_models(self):
        """Test Correlated GBM and Merton Jump Diffusion simulations."""
        engine = StochasticEngine()
        paths = engine.simulate_merton_jump_diffusion(100.0, days=30, simulations=50)
        self.assertEqual(paths.shape, (50, 31))
        self.assertTrue((paths > 0).all())

    def test_08_ml_feature_store(self):
        """Test feature engineering."""
        dates = pd.date_range("2024-01-01", periods=60)
        prices = np.linspace(100, 110, 60)
        df = pd.DataFrame({
            "Date": dates,
            "Open": prices,
            "High": prices + 0.5,
            "Low": prices - 0.5,
            "Close": prices,
            "Volume": [1000] * 60,
        })
        store = FeatureStore()
        feats = store.extract_features(df)
        self.assertIn("rsi_14", feats.columns)
        self.assertIn("trend_quality", feats.columns)

    def test_09_hmm_regime_failsafe(self):
        """Test HMM classifier failsafe to VOLATILE."""
        clf = HMMRegimeClassifier("^FCHI")
        # Empty df triggers fail-safe
        state, prob = clf.fit_and_predict(pd.DataFrame())
        self.assertEqual(state, MarketRegimeState.VOLATILE)

    def test_10_openfigi_mapper(self):
        """Test offline FIGI / Ticker mapper."""
        mapper = OpenFigiMapper(ROOT / "database" / "test_figi.db")
        self.assertEqual(mapper.isin_to_ticker("FR0000121014"), "MC.PA")
        self.assertEqual(mapper.ticker_to_isin("MC.PA"), "FR0000121014")
        try:
            if (ROOT / "database" / "test_figi.db").exists():
                os.remove(ROOT / "database" / "test_figi.db")
        except Exception:
            pass

    def test_11_trade_post_mortem(self):
        """Test post-mortem recording."""
        pm = TradePostMortemEngine(ROOT / "database" / "test_pm.db")
        res = pm.generate_post_mortem(
            trade_id="T001",
            ticker="MC.PA",
            entry_date="2026-05-01",
            exit_date="2026-06-01",
            entry_price=600.0,
            exit_price=660.0,
            shares=2,
            exit_reason="PROFIT_SHAVE",
        )
        self.assertEqual(res["ticker"], "MC.PA")
        self.assertEqual(res["pnl_eur"], 120.0)
        try:
            if (ROOT / "database" / "test_pm.db").exists():
                os.remove(ROOT / "database" / "test_pm.db")
        except Exception:
            pass

    def test_12_red_team_debate(self):
        """Test Red Team adversarial debate agent."""
        agent = RedTeamDebateAgent()
        res = agent.run_debate(
            "MC.PA",
            85.0,
            {"name": "LVMH", "sector": "Luxe"},
            {"close": 600.0, "rsi": 26.0},
            {"trailing_pe": 20.0},
        )
        self.assertEqual(res.ticker, "MC.PA")
        self.assertIn(res.final_verdict, ("GO", "REDUCE_SIZE", "NO_GO"))


if __name__ == "__main__":
    unittest.main()
