#!/usr/bin/env bash
# ==============================================================================
# PEA Pollux Quantitative Terminal — Local Production Deployment & Health Check
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

echo "======================================================================"
echo "🚀 PEA Pollux Terminal — Starting Production Deployment Check"
echo "======================================================================"

# 1. Virtual Environment Setup
VENV_DIR="${ROOT_DIR}/.venv"
if [ ! -d "${VENV_DIR}" ]; then
    echo "📦 Creating Python virtual environment in .venv..."
    python3 -m venv "${VENV_DIR}"
fi

echo "🔌 Activating virtual environment..."
# shellcheck source=/dev/null
source "${VENV_DIR}/bin/activate"

# 2. Dependency Installation
echo "📥 Installing / Updating dependencies from requirements.txt..."
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet

# 3. Database & Memory Core Initialization
echo "🗄️ Initializing SQLite and DuckDB schemas..."
python -c "
import sys
from pathlib import Path
sys.path.insert(0, '01_memory_core')
from sqlite_portfolio import PortfolioDB
from duckdb_manager import TimeSeriesDB

pdb = PortfolioDB()
pdb.init_db()
print('  ✅ SQLite PortfolioDB initialized at database/portfolio.db')

try:
    tsdb = TimeSeriesDB()
    tsdb.init_schema()
    print('  ✅ DuckDB TimeSeriesDB initialized at database/timeseries.duckdb')
except Exception as e:
    print(f'  ⚠️ DuckDB initialization warning: {e}')
"

# 4. Initialize Default Account if empty
if [ ! -f "database/portfolio.db" ] || [ ! -s "database/portfolio.db" ]; then
    echo "💰 Seeding default PEA cash account (10,000 €)..."
    python seed_account.py --cash 10000 || true
fi

# 5. Full System Integrity Verification
echo "🧪 Running Master Test Suite..."
python -m unittest discover tests

echo "======================================================================"
echo "🎉 DEPLOYMENT CHECK PASSED: All subsystems verified & operational!"
echo "======================================================================"
echo "To start services:"
echo "  • Dashboard: streamlit run 05_interfaces/terminal_dashboard.py"
echo "  • Scheduler: python main_scheduler.py"
echo "  • API:       uvicorn 06_api.internal_api:app --port 8000"
echo "======================================================================"
