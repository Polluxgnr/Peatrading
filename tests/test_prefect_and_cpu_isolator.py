"""Unit Tests for Prefect Workflow Orchestration and CPU Task Isolator."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
for sub in ("00_data_sensors", "00_data_sensors/adapters", "01_memory_core", "02_quant_engine", "03_risk_portfolio", "04_orchestrator_ai", "05_interfaces"):
    sys.path.insert(0, str(ROOT / sub))

from cpu_isolator import CpuTaskIsolator, cpu_isolator
import main_scheduler


def _heavy_compute_job(x: int, y: int) -> int:
    """Mock heavy CPU compute job."""
    return (x ** 2) + (y ** 2)


class TestPrefectAndCpuIsolatorSuite(unittest.TestCase):

    def test_01_cpu_isolator_singleton_and_execution(self):
        """Verify CpuTaskIsolator singleton instance and process pool execution."""
        iso1 = CpuTaskIsolator()
        iso2 = CpuTaskIsolator()
        self.assertIs(iso1, iso2)

        res = asyncio.run(cpu_isolator.run_in_process(_heavy_compute_job, 3, 4))
        self.assertEqual(res, 25)

    def test_02_prefect_flow_and_tasks_decoration(self):
        """Verify main_scheduler exposes Prefect flow and tasks."""
        self.assertTrue(hasattr(main_scheduler, "pea_pollux_market_cycle"))
        self.assertTrue(hasattr(main_scheduler, "task_ingest_data"))
        self.assertTrue(hasattr(main_scheduler, "task_generate_and_orchestrate"))
        self.assertTrue(hasattr(main_scheduler, "task_dispatch_alerts"))

    @patch("main_scheduler._load_universe_tickers", return_value=["MC.PA", "CW8.PA"])
    @patch("main_scheduler.TimeSeriesDB.init_db", return_value=None)
    @patch("main_scheduler.PortfolioDB.init_db", return_value=None)
    @patch("main_scheduler.task_ingest_data", new_callable=AsyncMock)
    @patch("main_scheduler.task_generate_and_orchestrate", new_callable=AsyncMock)
    @patch("main_scheduler.task_dispatch_alerts", new_callable=AsyncMock)
    def test_03_pea_pollux_market_cycle_orchestration(
        self, mock_dispatch, mock_gen, mock_ingest, mock_pdb_init, mock_tsdb_init, mock_universe
    ):
        """Verify the execution sequence of the main market cycle flow."""
        mock_ingest.return_value = True
        mock_gen.return_value = []
        mock_dispatch.return_value = None

        with patch("main_scheduler.MacroAlphaSensor.get_european_vix", return_value=16.0):
            asyncio.run(main_scheduler.pea_pollux_market_cycle())

            self.assertTrue(mock_ingest.called)
            self.assertTrue(mock_gen.called)
            self.assertTrue(mock_dispatch.called)


if __name__ == "__main__":
    unittest.main()
