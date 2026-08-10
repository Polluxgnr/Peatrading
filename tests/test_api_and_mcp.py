"""Test Suite for PEA Pollux Internal API & MCP Server Tools.

Verifies:
  1. Internal API endpoints (/portfolio/summary, /recommendations/pending, /system/health, /data/ticker/MC.PA/context).
  2. Recommendation paradigm adherence.
  3. MCP tools formatting and decoupling.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
for d in ("00_data_sensors", "01_memory_core", "02_quant_engine", "03_risk_portfolio", "04_orchestrator_ai", "05_interfaces", "06_api", "07_mcp"):
    sys.path.insert(0, str(ROOT / d))

from internal_api import app
from fastapi.testclient import TestClient
import pollux_mcp


class TestApiAndMcpSuite(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)

    def test_01_root_paradigm(self):
        """Verify API root specifies quantitative recommendations."""
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("paradigm", data)
        self.assertIn("Recommendations", data["paradigm"])

    def test_02_portfolio_summary(self):
        """Verify /api/v1/portfolio/summary structure."""
        res = self.client.get("/api/v1/portfolio/summary")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("cash_available", data)
        self.assertIn("total_equity", data)
        self.assertIn("exposure_pct", data)
        self.assertIn("positions", data)

    def test_03_pending_recommendations(self):
        """Verify /api/v1/recommendations/pending returns list."""
        res = self.client.get("/api/v1/recommendations/pending")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIsInstance(data, list)

    def test_04_system_health(self):
        """Verify /api/v1/system/health returns healthy status."""
        res = self.client.get("/api/v1/system/health")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data.get("status"), "HEALTHY")
        self.assertEqual(data.get("execution_model"), "SOVEREIGN_HUMAN_IN_THE_LOOP")

    def test_05_mcp_tools_with_mocked_api(self):
        """Verify MCP tools format cleanly when querying the API."""
        mock_summary = {
            "cash_available": 5000.0,
            "total_equity": 15000.0,
            "exposure_pct": 66.7,
            "cash_ratio_pct": 33.3,
            "positions": [
                {
                    "ticker": "MC.PA",
                    "qty_shares": 10,
                    "avg_entry_price": 600.0,
                    "current_price": 650.0,
                    "market_value": 6500.0,
                    "unrealized_pnl_eur": 500.0,
                    "unrealized_pnl_pct": 8.33,
                    "sector": "Luxe",
                }
            ],
        }
        with patch("pollux_mcp._fetch_api", return_value=mock_summary):
            text = pollux_mcp.get_portfolio_status()
            self.assertIn("PEA Portfolio Summary", text)
            self.assertIn("15,000.00 €", text)
            self.assertIn("MC.PA", text)

        mock_recs = [
            {
                "action": "BUY",
                "ticker": "OR.PA",
                "conviction_score": 85.0,
                "recommended_quantity": 4,
                "reference_price": 420.0,
                "rationale": "RSI < 30 oversold pull-back",
                "generated_at": "2026-08-10T14:00:00Z",
            }
        ]
        with patch("pollux_mcp._fetch_api", return_value=mock_recs):
            text_recs = pollux_mcp.get_top_recommendations()
            self.assertIn("Active Quantitative Recommendations", text_recs)
            self.assertIn("OR.PA", text_recs)


if __name__ == "__main__":
    unittest.main()
