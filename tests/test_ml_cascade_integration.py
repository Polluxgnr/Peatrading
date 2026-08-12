"""Test Suite for ML Predictor Worker Live Signal Cascade Integration.

Verifies:
  1. Step 2c Isolation Forest Anomaly Detection veto.
  2. Step 2c XGBoost Probability + SHAP threshold (< 0.50) veto.
  3. Step 2c ML Probability enrichment into Signal lineage when proba >= 0.50.
  4. UI Trade Cards rendering of ML probability and SHAP drivers.
  5. Internal API recommendation endpoint returning ML metadata.
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
for d in ("00_data_sensors", "01_memory_core", "02_quant_engine", "03_risk_portfolio", "04_orchestrator_ai", "05_interfaces", "06_api", "07_mcp"):
    sys.path.insert(0, str(ROOT / d))

from data_models import PortfolioState, Position, Signal, SignalStatus, SignalType
from signal_priority_cascade import SignalOrchestrator
import trade_cards


class TestMLCascadeIntegration(unittest.TestCase):

    def setUp(self):
        self.portfolio = PortfolioState(
            cash_available=10000.0,
            total_equity=20000.0,
            positions=[
                Position(ticker="CW8.PA", qty_shares=20, avg_entry_price=450.0, current_price=500.0, sector="ETF"),
            ],
            last_updated=datetime.now(),
        )
        self.config_dir = ROOT / "config"
        self.orchestrator = SignalOrchestrator(config_dir=self.config_dir)

    def test_01_ml_anomaly_veto(self):
        """Verify Isolation Forest anomaly triggers rejection."""
        sig = Signal(
            ticker="MC.PA",
            signal_type=SignalType.BUY,
            status=SignalStatus.PENDING,
            score=82.0,
            reason="Mean reversion RSI < 30",
            lineage={"rsi": 25.0, "gap_sma200_pct": 5.0, "atr_pct": 2.0},
        )
        with patch.object(self.orchestrator.macro_veto, "check_veto", return_value=(False, "")), \
             patch.object(self.orchestrator.earnings_blackout, "check_veto", return_value=(False, "")), \
             patch.object(self.orchestrator.fundamentals_sensor, "calculate_piotroski_score", return_value=(7, {})), \
             patch.object(self.orchestrator.firewall, "check_correlation", return_value=(True, "")), \
             patch("signal_priority_cascade.predict_anomaly", return_value=True), \
             patch("signal_priority_cascade.predict_probability_with_shap", return_value=(0.75, {"rsi": 0.1}, (0.7, 0.8))):
            processed = self.orchestrator.process_raw_signals(
                raw_signals=[sig],
                portfolio=self.portfolio,
                current_prices={"MC.PA": 600.0},
                vix_level=16.0,
            )
            self.assertEqual(len(processed), 1)
            self.assertEqual(processed[0].status, SignalStatus.REJECTED)
            self.assertIn("Structural Anomaly detected by Isolation Forest", processed[0].reason)

    def test_02_ml_low_probability_veto(self):
        """Verify low ML probability (< 0.50) triggers rejection."""
        sig = Signal(
            ticker="MC.PA",
            signal_type=SignalType.BUY,
            status=SignalStatus.PENDING,
            score=82.0,
            reason="Mean reversion RSI < 30",
            lineage={"rsi": 25.0, "gap_sma200_pct": 5.0, "atr_pct": 2.0},
        )
        with patch.object(self.orchestrator.macro_veto, "check_veto", return_value=(False, "")), \
             patch.object(self.orchestrator.earnings_blackout, "check_veto", return_value=(False, "")), \
             patch.object(self.orchestrator.fundamentals_sensor, "calculate_piotroski_score", return_value=(7, {})), \
             patch.object(self.orchestrator.firewall, "check_correlation", return_value=(True, "")), \
             patch("signal_priority_cascade.predict_anomaly", return_value=False), \
             patch("signal_priority_cascade.predict_probability_with_shap", return_value=(0.42, {"rsi": -0.05}, (0.38, 0.46))):
            processed = self.orchestrator.process_raw_signals(
                raw_signals=[sig],
                portfolio=self.portfolio,
                current_prices={"MC.PA": 600.0},
                vix_level=16.0,
            )
            self.assertEqual(len(processed), 1)
            self.assertEqual(processed[0].status, SignalStatus.REJECTED)
            self.assertIn("ML Win Probability too low (42.0%)", processed[0].reason)

    def test_03_ml_pass_enriches_lineage(self):
        """Verify passing ML check enriches signal lineage with ml_probability and shap_values."""
        sig = Signal(
            ticker="MC.PA",
            signal_type=SignalType.BUY,
            status=SignalStatus.PENDING,
            score=82.0,
            reason="Mean reversion RSI < 30",
            lineage={"rsi": 25.0, "gap_sma200_pct": 5.0, "atr_pct": 2.0},
        )
        mock_shap = {"rsi": 0.12, "gap_sma200_pct": 0.08}
        with patch.object(self.orchestrator.macro_veto, "check_veto", return_value=(False, "")), \
             patch.object(self.orchestrator.earnings_blackout, "check_veto", return_value=(False, "")), \
             patch.object(self.orchestrator.fundamentals_sensor, "calculate_piotroski_score", return_value=(7, {})), \
             patch.object(self.orchestrator.firewall, "check_correlation", return_value=(True, "")), \
             patch("signal_priority_cascade.predict_anomaly", return_value=False), \
             patch("signal_priority_cascade.predict_probability_with_shap", return_value=(0.685, mock_shap, (0.65, 0.72))), \
             patch.object(self.orchestrator.sizer, "size_with_explanation", return_value=(5, {"raw_shares": 5, "price": 600.0, "weight_pct": 15.0})):
            processed = self.orchestrator.process_raw_signals(
                raw_signals=[sig],
                portfolio=self.portfolio,
                current_prices={"MC.PA": 600.0},
                vix_level=16.0,
            )
            self.assertEqual(len(processed), 1)
            self.assertEqual(processed[0].status, SignalStatus.APPROVED)
            self.assertEqual(processed[0].lineage.get("ml_probability"), 0.685)
            self.assertEqual(processed[0].lineage.get("shap_values"), mock_shap)
            self.assertEqual(tuple(processed[0].lineage.get("ml_interval")), (0.65, 0.72))

    def test_04_trade_card_renders_ml_probability(self):
        """Verify UI trade card displays ML probability, interval, and top SHAP factors."""
        card_html = trade_cards.render_signal_card(
            ticker="MC.PA",
            title="LVMH",
            signal_type="BUY",
            score=85.0,
            qty=4,
            reason="RSI survendu",
            lineage={
                "ml_probability": 0.685,
                "ml_interval": [0.65, 0.72],
                "shap_values": {"rsi": 0.15, "gap_sma200_pct": 0.05, "vol_ann": -0.02},
            },
        )
        self.assertIn("ML Probability", card_html)
        self.assertIn("68.5%", card_html)
        self.assertIn("Confidence Interval: 65%-72%", card_html)
        self.assertIn("rsi (+0.15)", card_html)
        self.assertIn("gap_sma200_pct (+0.05)", card_html)

    def test_05_internal_api_includes_ml_fields(self):
        """Verify internal API pending recommendations include ml_probability."""
        from fastapi.testclient import TestClient
        from internal_api import app

        mock_rows = [
            {
                "id": "sig-123",
                "ticker": "OR.PA",
                "signal_type": "BUY",
                "score": 88.0,
                "quantity": 3,
                "price": 400.0,
                "reason": "Mean reversion",
                "created_at": "2026-08-10T14:00:00Z",
                "lineage_json": json.dumps({
                    "ml_probability": 0.72,
                    "ml_interval": [0.68, 0.76],
                    "shap_values": {"rsi": 0.14},
                }),
            }
        ]
        client = TestClient(app)
        with patch("internal_api._PORTFOLIO_DB.fetch_signals_by_status", return_value=mock_rows):
            res = client.get("/api/v1/recommendations/pending")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["ticker"], "OR.PA")
            self.assertEqual(data[0]["ml_probability"], 0.72)
            self.assertEqual(data[0]["ml_interval"], [0.68, 0.76])
            self.assertEqual(data[0]["shap_values"], {"rsi": 0.14})


if __name__ == "__main__":
    unittest.main()
