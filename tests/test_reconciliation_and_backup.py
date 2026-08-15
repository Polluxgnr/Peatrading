"""Unit Tests for Broker CSV Reconciliation, Dashboard Auth, and S3 Cloud Backups."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
for sub in ("00_data_sensors", "01_memory_core", "02_quant_engine", "03_risk_portfolio", "04_orchestrator_ai", "05_interfaces", "tools"):
    sys.path.insert(0, str(ROOT / sub))

from broker_reconciliation import BrokerReconciliator
import backup_databases
from main_scheduler import run_cloud_backup


class TestReconciliationAndBackupSuite(unittest.TestCase):

    def test_01_parse_broker_csv_french_format(self):
        """Verify parsing French Boursorama/Bourse Direct broker CSV exports."""
        reconciliator = BrokerReconciliator()

        csv_sample = """Libellé;Code / ISIN;Quantité;PRU;Dernier Cours;Valeur
LVMH MOET HENNESSY;FR0000121014;10;620,50 €;650,00 €;6 500,00 €
AIR LIQUIDE;FR0000120073;15;170,25 €;178,50 €;2 677,50 €
TOTALENERGIES;FR0000120271;30;58,00 €;61,20 €;1 836,00 €
Liquidités;;;;;1 500,00 €
Total Portefeuille;;;;;12 513,50 €
"""
        parsed = reconciliator.parse_broker_csv(csv_sample)
        self.assertEqual(len(parsed), 3)

        lvmh = next((p for p in parsed if "MC.PA" in p["ticker"] or "LVMH" in p["ticker"] or "FR0000121014" in p["ticker"]), None)
        self.assertIsNotNone(lvmh)
        self.assertEqual(lvmh["qty_shares"], 10)
        self.assertEqual(lvmh["avg_entry_price"], 620.50)
        self.assertEqual(lvmh["current_price"], 650.00)

    def test_02_reconcile_with_sqlite(self):
        """Verify reconcile_with_sqlite correctly overwrites database state and logs audit record."""
        reconciliator = BrokerReconciliator()
        mock_db = MagicMock()

        parsed_data = [
            {"ticker": "MC.PA", "qty_shares": 8, "avg_entry_price": 600.0, "current_price": 620.0, "sector": "Consumer Cyclical"},
            {"ticker": "CW8.PA", "qty_shares": 15, "avg_entry_price": 480.0, "current_price": 500.0, "sector": "Core ETF"},
        ]
        actual_cash = 2500.0

        res = reconciliator.reconcile_with_sqlite(parsed_data, actual_cash, mock_db)

        self.assertTrue(res["success"])
        self.assertEqual(res["positions_synced"], 2)
        self.assertEqual(res["cash_available"], 2500.0)
        # Expected Equity = 2500 + (8*620 + 15*500) = 2500 + (4960 + 7500) = 14960.0
        self.assertEqual(res["total_equity"], 14960.0)

        self.assertTrue(mock_db.update_portfolio.called)
        self.assertTrue(mock_db.log_signal.called)
        logged_signal = mock_db.log_signal.call_args[0][0]
        self.assertEqual(logged_signal.reason, "PORTFOLIO RECONCILIATION: Synced with broker reality.")
        self.assertEqual(logged_signal.lineage.get("strategy"), "PORTFOLIO_RECONCILIATION")

    def test_03_backup_databases_s3_upload(self):
        """Verify backup_to_s3 calls boto3 upload_file properly."""
        mock_file = ROOT / "config" / "pea_universe.yaml"
        mock_boto = MagicMock()
        mock_s3 = MagicMock()
        mock_boto.client.return_value = mock_s3

        with patch.dict(sys.modules, {"boto3": mock_boto}):
            success = backup_databases.backup_to_s3([mock_file], "20260816_120000", "my-test-bucket")
            self.assertTrue(success)
            self.assertTrue(mock_s3.upload_file.called)

    def test_04_run_cloud_backup_scheduler(self):
        """Verify run_cloud_backup routine executes without crashing."""
        with patch("subprocess.run") as mock_sub:
            mock_sub.return_value = MagicMock(returncode=0, stdout="OK", stderr="")
            run_cloud_backup()
            self.assertTrue(mock_sub.called)


if __name__ == "__main__":
    unittest.main()
