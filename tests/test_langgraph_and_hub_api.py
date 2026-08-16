"""Unit Tests for Layer 5 FastAPI Hub Endpoints and Layer 6 LangGraph Analyst Agent."""

from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
for sub in ("00_data_sensors", "00_data_sensors/adapters", "01_memory_core", "02_quant_engine", "04_orchestrator_ai", "05_interfaces", "06_api"):
    sys.path.insert(0, str(ROOT / sub))

from internal_api import app, _PORTFOLIO_DB
from langgraph_agent import (
    AnalystState,
    fetch_data_node,
    run_analyst_graph,
    synthesize_node,
)
from trade_cards import render_signal_card


class TestLangGraphAndHubApiSuite(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)

    def test_01_hub_signals_endpoint(self):
        """Verify GET /api/v1/hub/signals returns normalized alternative signals."""
        with sqlite3.connect(_PORTFOLIO_DB.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS alternative_signals (
                    id TEXT PRIMARY KEY,
                    ticker TEXT,
                    ts TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    value REAL NOT NULL,
                    confidence REAL NOT NULL,
                    source TEXT NOT NULL,
                    metadata_json TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO alternative_signals (id, ticker, ts, signal_type, value, confidence, source, metadata_json)
                VALUES ('sig_test_01', 'MC.PA', '2026-08-16T12:00:00', 'SHORT_INTEREST', 3.8, 1.0, 'AMF_BDIF', '{"isin": "FR0000121014"}');
                """
            )

        resp = self.client.get("/api/v1/hub/signals?ticker=MC.PA")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsInstance(data, list)
        self.assertTrue(len(data) >= 1)
        self.assertEqual(data[0]["ticker"], "MC.PA")
        self.assertEqual(data[0]["signal_type"], "SHORT_INTEREST")
        self.assertEqual(data[0]["value"], 3.8)

    def test_02_hub_ticks_endpoint(self):
        """Verify GET /api/v1/hub/ticks returns formatted OHLCV market ticks."""
        mock_df = pd.DataFrame(
            {
                "Open": [650.0, 655.0],
                "High": [660.0, 665.0],
                "Low": [645.0, 650.0],
                "Close": [658.0, 662.0],
                "Volume": [150000, 180000],
            },
            index=pd.to_datetime(["2026-08-14", "2026-08-15"]),
        )

        with patch("duckdb_manager.TimeSeriesDB.get_historical_prices", return_value=mock_df):
            resp = self.client.get("/api/v1/hub/ticks?ticker=MC.PA&days=10")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIsInstance(data, list)
            self.assertEqual(len(data), 2)
            self.assertEqual(data[0]["ticker"], "MC.PA")
            self.assertEqual(data[0]["close"], 658.0)

    def test_03_langgraph_nodes_and_run(self):
        """Verify LangGraph analyst state machine execution."""
        initial_state: AnalystState = {
            "ticker": "OR.PA",
            "raw_signals": [],
            "quantitative_data": {},
            "narrative_thesis": "",
        }

        # Test fetch_data_node
        with patch("requests.get") as mock_get:
            mock_signals_resp = MagicMock()
            mock_signals_resp.status_code = 200
            mock_signals_resp.json.return_value = [
                {"signal_type": "SHORT_INTEREST", "value": 0.5, "source": "AMF_BDIF"}
            ]
            mock_ticks_resp = MagicMock()
            mock_ticks_resp.status_code = 200
            mock_ticks_resp.json.return_value = [
                {"ticker": "OR.PA", "date": "2026-08-15", "close": 420.0}
            ]
            mock_get.side_effect = [mock_signals_resp, mock_ticks_resp]

            state_after_fetch = fetch_data_node(initial_state)
            self.assertEqual(len(state_after_fetch["raw_signals"]), 1)
            self.assertEqual(state_after_fetch["quantitative_data"]["latest_close"], 420.0)

            # Test synthesize_node
            state_after_syn = synthesize_node(state_after_fetch)
            self.assertTrue(len(state_after_syn["narrative_thesis"]) > 20)
            self.assertIn("OR.PA", state_after_syn["narrative_thesis"])

        # Test run_analyst_graph
        thesis = run_analyst_graph("AI.PA")
        self.assertIsInstance(thesis, str)
        self.assertTrue(len(thesis) > 20)

    def test_04_trade_cards_shap_visualization(self):
        """Verify render_signal_card renders SHAP positive and negative badges."""
        lineage = {
            "ml_probability": 0.72,
            "ml_interval": [0.68, 0.76],
            "shap_values": {
                "rsi": 0.18,
                "gap_sma200_pct": 0.08,
                "volatility": -0.05,
            },
        }

        card_html = render_signal_card(
            ticker="MC.PA",
            title="LVMH (MC.PA)",
            signal_type="BUY",
            score=88.0,
            qty=5,
            reason="MRE Oversold Rebound",
            lineage=lineage,
        )

        self.assertIn("72.0%", card_html)
        self.assertIn("▲ rsi (+0.18)", card_html)
        self.assertIn("▲ gap_sma200_pct (+0.08)", card_html)
        self.assertIn("▼ volatility (-0.05)", card_html)


if __name__ == "__main__":
    unittest.main()
