"""Unit Tests for Attack/Shield Volatility Thermometer, Bunker Mode, and 98% Max Exposure Rule."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
for sub in ("00_data_sensors", "01_memory_core", "02_quant_engine", "03_risk_portfolio", "04_orchestrator_ai", "05_interfaces"):
    sys.path.insert(0, str(ROOT / sub))

from allocation_thermometer import VolatilityThermometer
from data_models import PortfolioState, Position, Signal, SignalStatus, SignalType
from pea_position_sizer import PeaSizer


class TestAllocationThermometerSuite(unittest.TestCase):

    def test_01_bunker_mode_when_below_sma200(self):
        """Verify VolatilityThermometer triggers BUNKER mode when Close < SMA200."""
        thermo = VolatilityThermometer()

        # 220 days of data: mean is 150, but last close drops to 120 (< SMA200)
        prices = [150.0] * 219 + [120.0]
        df = pd.DataFrame({"Close": prices})

        res = thermo.calculate_attack_defense_split(df, current_vix=18.0)
        self.assertEqual(res["mode"], "BUNKER")
        self.assertEqual(res["attack_pct"], 0.0)
        self.assertEqual(res["defense_pct"], 1.0)
        self.assertTrue(res["is_bunker"])

    def test_02_attack_mode_when_low_vol_above_sma200(self):
        """Verify VolatilityThermometer allocates ~90%+ Attack in calm structural uptrend."""
        thermo = VolatilityThermometer()

        # Steady uptrend from 100 to 180 (Close 180 > SMA200 ~140), calm vol
        prices = list(np.linspace(100.0, 180.0, 250))
        df = pd.DataFrame({"Close": prices})

        res = thermo.calculate_attack_defense_split(df, current_vix=13.5)
        self.assertEqual(res["mode"], "ATTACK")
        self.assertGreaterEqual(res["attack_pct"], 0.70)
        self.assertLessEqual(res["attack_pct"], 0.98)
        self.assertFalse(res["is_bunker"])

    def test_03_defense_leaning_when_high_vol_above_sma200(self):
        """Verify VolatilityThermometer scales down Attack allocation when VIX is high."""
        thermo = VolatilityThermometer()

        prices = list(np.linspace(100.0, 180.0, 250))
        df = pd.DataFrame({"Close": prices})

        res = thermo.calculate_attack_defense_split(df, current_vix=28.0)
        self.assertEqual(res["mode"], "DEFENSE_LEANING")
        self.assertLessEqual(res["attack_pct"], 0.50)

    def test_04_pea_sizer_98pct_max_exposure_rule(self):
        """Verify PeaSizer enforces 2% permanent cash buffer (98% max exposure limit)."""
        sizer = PeaSizer()
        self.assertEqual(sizer.permanent_cash_buffer, 0.02)

        # Portfolio has 10,000 EUR total equity, 9,700 EUR already invested in equities, 300 EUR cash available.
        # Max exposure cap (98%) is 9,800 EUR -> remaining room is only 100 EUR (even if cash is 300 EUR).
        portfolio = PortfolioState(
            cash_available=300.0,
            total_equity=10000.0,
            positions=[
                Position(
                    ticker="CW8.PA",
                    sector="Financial Services",
                    qty_shares=19,
                    avg_entry_price=510.0,
                    current_price=510.52,
                    market_value=9700.0,
                )

            ],
            last_updated=datetime.now(timezone.utc),
        )

        signal = Signal(
            id="sig_test_98",
            ticker="MC.PA",
            signal_type=SignalType.BUY,
            status=SignalStatus.PENDING,
            score=95.0,
            created_at=datetime.now(timezone.utc),
            reason="Oversold test",
        )

        # Share price 60 EUR. 100 EUR room allows at most 1 share (60 EUR notional).
        qty, meta = sizer.size_with_explanation(
            signal=signal,
            portfolio=portfolio,
            current_price=60.0,
            historical_volatility=0.20,
        )

        self.assertEqual(qty, 1)
        self.assertEqual(meta["notional"], 60.0)
        self.assertLessEqual(9700.0 + meta["notional"], 10000.0 * 0.98)

    def test_05_pea_sizer_attack_budget_constraint(self):
        """Verify PeaSizer caps stock picking allocation to attack_budget_pct."""
        sizer = PeaSizer()

        # Portfolio with 10,000 EUR equity, 0 holdings, 10,000 EUR cash.
        portfolio = PortfolioState(
            cash_available=10000.0,
            total_equity=10000.0,
            positions=[],
            last_updated=datetime.now(timezone.utc),
        )

        signal = Signal(
            id="sig_test_atk",
            ticker="MC.PA",
            signal_type=SignalType.BUY,
            status=SignalStatus.PENDING,
            score=90.0,
            created_at=datetime.now(timezone.utc),
            reason="Attack test",
        )

        # If attack_budget_pct is 0.10 (10% max attack equity = 1,000 EUR max total),
        # single position cap 15% would normally allow 1,500 EUR, but attack budget constrains it to 1,000 EUR.
        qty, meta = sizer.size_with_explanation(
            signal=signal,
            portfolio=portfolio,
            current_price=500.0,
            historical_volatility=0.20,
            attack_budget_pct=0.10,
        )

        self.assertLessEqual(meta["notional"], 1000.0)
        self.assertEqual(qty, 1)  # 500 EUR <= 1000 EUR


if __name__ == "__main__":
    unittest.main()
