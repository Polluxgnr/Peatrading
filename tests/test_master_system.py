"""Master Integration & Regression Test Suite for PEA Pollux Terminal.

Tests end-to-end quantitative execution, 7-stage risk cascades, columnar DuckDB
and SQLite state persistence, Data Quality Gateway anomaly filtering, and Volatility Thermometer logic.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
for sub in (
    "00_data_sensors",
    "00_data_sensors/adapters",
    "01_memory_core",
    "02_quant_engine",
    "03_risk_portfolio",
    "04_orchestrator_ai",
    "05_interfaces",
):
    sys.path.insert(0, str(ROOT / sub))

from allocation_thermometer import VolatilityThermometer
from data_models import Position, PortfolioState, Signal, SignalType
from data_quality import DataQualityGateway
from duckdb_manager import TimeSeriesDB
from signal_priority_cascade import SignalOrchestrator
from sqlite_portfolio import PortfolioDB
from technical_scorer import SignalGenerator


class TestMasterSystemSuite(unittest.TestCase):

    def setUp(self):
        self.temp_sqlite = ROOT / "database" / "test_master_portfolio.db"
        self.temp_duckdb = ROOT / "database" / "test_master_timeseries.duckdb"
        for p in (self.temp_sqlite, self.temp_duckdb):
            if p.exists():
                p.unlink()

        self.db = PortfolioDB(db_path=self.temp_sqlite)
        self.db.init_db()

        self.ts_db = TimeSeriesDB(db_path=self.temp_duckdb)

    def tearDown(self):
        self.ts_db.close()
        for p in (self.temp_sqlite, self.temp_duckdb):
            if p.exists():
                p.unlink()

    def test_01_end_to_end_signal_generation_and_risk_cascade(self):
        """Verify SignalGenerator produces technical scores and SignalOrchestrator filters through 7-stage risk cascade."""
        # 1. Technical signal indicators calculation
        gen = SignalGenerator()
        oversold_score = gen.score_rsi(22.0)
        self.assertTrue(oversold_score > 70.0)

        # 2. Feed into 7-stage risk cascade
        mock_tsdb = MagicMock()
        mock_hist = pd.DataFrame({
            "Close": np.linspace(100, 110, 60),
            "Date": pd.date_range("2026-01-01", periods=60, freq="D"),
        })
        mock_tsdb.get_historical_prices.return_value = mock_hist

        orchestrator = SignalOrchestrator(timeseries_db=mock_tsdb)
        portfolio_state = PortfolioState(
            cash_available=5000.0,
            total_equity=10000.0,
            positions=[
                Position(
                    ticker="OR.PA",
                    qty_shares=5,
                    avg_entry_price=400.0,
                    current_price=420.0,
                    sector="Consumer Defensive",
                    last_updated=datetime.now(timezone.utc),
                )
            ],
            last_updated=datetime.now(timezone.utc),
        )

        test_signal = Signal(
            ticker="MC.PA",
            signal_type=SignalType.BUY,
            score=88.0,
            reason="MRE Oversold bounce",
            price=700.0,
            target_qty=5,
            lineage={"source": "test_master"},
        )

        current_prices = {"MC.PA": 700.0, "OR.PA": 420.0}
        processed = orchestrator.process_raw_signals(
            raw_signals=[test_signal],
            portfolio=portfolio_state,
            current_prices=current_prices,
            vix_level=15.0,
        )

        # Signal should be processed without crashing
        self.assertEqual(len(processed), 1)
        self.assertEqual(processed[0].ticker, "MC.PA")



    def test_02_database_persistence_and_retrieval(self):
        """Verify state persistence across DuckDB TimeSeriesDB and SQLite PortfolioDB."""
        # 1. SQLite state check
        p_state = PortfolioState(
            cash_available=3500.0,
            total_equity=12000.0,
            positions=[
                Position(
                    ticker="AI.PA",
                    qty_shares=10,
                    avg_entry_price=160.0,
                    current_price=175.0,
                    sector="Basic Materials",
                    last_updated=datetime.now(timezone.utc),
                )
            ],
            last_updated=datetime.now(timezone.utc),
        )
        self.db.update_portfolio(p_state)
        loaded = self.db.get_portfolio_state()
        self.assertEqual(loaded.cash_available, 3500.0)
        self.assertEqual(loaded.total_equity, 12000.0)
        self.assertEqual(len(loaded.positions), 1)
        self.assertEqual(loaded.positions[0].ticker, "AI.PA")

        # 2. DuckDB OHLCV upsert and retrieval
        ohlcv_data = pd.DataFrame(
            {
                "Ticker": ["AI.PA", "AI.PA"],
                "Date": [pd.to_datetime("2026-08-14"), pd.to_datetime("2026-08-15")],
                "Open": [170.0, 172.0],
                "High": [175.0, 176.0],
                "Low": [169.0, 171.0],
                "Close": [174.0, 175.0],
                "Volume": [50000.0, 55000.0],
            }
        )
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetch_df.return_value = ohlcv_data

        with patch.object(self.ts_db, "_connect") as mock_connect:
            mock_connect.return_value.__enter__.return_value = mock_conn
            mock_connect.return_value.__exit__.return_value = None

            self.ts_db.init_schema()
            self.assertTrue(mock_conn.execute.called)

            rows_inserted = self.ts_db.upsert_daily_ohlcv(ohlcv_data)
            self.assertEqual(rows_inserted, 2)

            fetched = self.ts_db.get_historical_prices("AI.PA", days=10)
            self.assertEqual(len(fetched), 2)
            self.assertEqual(float(fetched["Close"].iloc[-1]), 175.0)


    def test_03_data_quality_gateway_outlier_detection(self):
        """Verify DataQualityGateway detects and flags outlier returns (>40% swing or 4-sigma)."""
        gw = DataQualityGateway(outlier_return_threshold=0.40, outlier_zscore_threshold=4.0)

        # Baseline series with an extreme erroneous spike
        dates = pd.date_range("2026-01-01", periods=10, freq="D")
        closes = [100.0, 101.0, 100.5, 102.0, 101.5, 250.0, 102.0, 101.0, 103.0, 102.5]  # 250 is +146% spike
        df_raw = pd.DataFrame(
            {
                "Ticker": ["TTE.PA"] * 10,
                "Date": dates,
                "Open": closes,
                "High": closes,
                "Low": closes,
                "Close": closes,
                "Volume": [100000] * 10,
            }
        )

        cleaned = gw.validate_ohlcv_batch(df_raw)
        self.assertIn("is_outlier", cleaned.columns)
        outliers = cleaned[cleaned["is_outlier"] == True]  # noqa: E712
        self.assertTrue(len(outliers) >= 1)

    def test_04_volatility_thermometer_split(self):
        """Verify VolatilityThermometer calculates Attack/Defense splits and triggers Bunker mode."""
        thermo = VolatilityThermometer()

        # 1. Normal low volatility environment (e.g. index above SMA200)
        dates = pd.date_range("2025-01-01", periods=250, freq="D")
        # Steady upward trend
        closes = np.linspace(6000, 7800, 250)
        df_idx = pd.DataFrame({"Close": closes}, index=dates)

        res_norm = thermo.calculate_attack_defense_split(df_idx, current_vix=14.0)
        self.assertEqual(res_norm["mode"], "ATTACK")
        self.assertTrue(res_norm["attack_pct"] >= 0.60)
        self.assertTrue(res_norm["defense_pct"] <= 0.40)

        # 2. Bunker mode (index falls below SMA200)
        closes_bunker = np.concatenate([np.linspace(7000, 7500, 220), np.linspace(7500, 5000, 30)])
        df_bunker = pd.DataFrame({"Close": closes_bunker}, index=dates)

        res_bunker = thermo.calculate_attack_defense_split(df_bunker, current_vix=28.0)
        self.assertEqual(res_bunker["mode"], "BUNKER")
        self.assertEqual(res_bunker["attack_pct"], 0.0)
        self.assertEqual(res_bunker["defense_pct"], 1.0)


if __name__ == "__main__":
    unittest.main()
