# PEA Sniper Terminal - Architectural Blueprint

This repository contains a localized, strictly long-only algorithmic trading system designed for the European PEA (Plan d'Epargne en Actions) tax wrapper. 

The architecture is divided into clear logical modules, isolating data ingestion, quantitative scoring, risk assessment, and mission control interfaces.

## 1. System Architecture

The system operates across five primary layers:

### Layer 00: Data Sensors (`00_data_sensors/`)
Stateless APIs responsible for extracting external market data. Network failures are strictly bounded to this layer.
- `market_prices_api.py`: Connects to `yfinance` to fetch heavily cached, chunked OHLCV data. 
- `macro_alpha_api.py`: Fetches broad macroeconomic indicators (e.g. VIX, EUR/USD, ^FCHI).
- `fundamentals_api.py` / `scrapers/`: Pulls static fundamental data (P/E, ROE) and regulatory insider flow filings (AMF).

### Layer 01: Memory Core (`01_memory_core/`)
Manages data persistence and state.
- **SQLite Database (`sqlite_portfolio.py`)**: Stores transactional, mutable state. Tracks the `portfolio_state`, `signals` ledger, `audit_logs`, and cached `fundamentals`. Using SQLite ensures safe concurrent read/writes for the Streamlit UI.
- **DuckDB Time-Series (`duckdb_manager.py`)**: Stores immutable, high-density OHLCV history. DuckDB provides columnar vectorization capabilities for quantitative feature engineering and backtesting without memory bottlenecks.

### Layer 02: Quant Engine (`02_quant_engine/`)
Performs feature engineering and generates trading signals.
- `ml_feature_store.py`: Extracts DuckDB data and engineers target variables (30d, 126d forward returns), rolling statistics, and StatArb Sector Relative Strength metrics. 
- `ml_trainer.py`: Uses XGBoost for Meta-Labeling signals based on quantitative features. Employs `IsolationForest` for anomaly detection (fitted exclusively on training sets to prevent leakage).
  - `technical_scorer.py`: Generates the baseline conviction score (0-100) using a multi-model ensemble (Mean Reversion, Trend/Momentum, Breakout, Context). Includes CUSUM detection (`detect_cusum_downward_break`) and IsolationForest anomaly detection to veto structural breakdowns.
- `quantitative_math.py`: Implements advanced analytics including Cornish-Fisher VaR (Extreme Value Theory) and Ledoit-Wolf Shrinkage.

### Layer 03: Risk & Portfolio (`03_risk_portfolio/`)
Enforces capital constraints and exposure limits.
- `correlation_firewall.py`: Prevents overexposure to specific sectors and blocks purchases when the VIX/V2TX exceeds panic thresholds.
- `pea_position_sizer.py`: Allocates capital safely, heavily favoring low-volatility/blue-chip assets and scaling positions inversely to risk.

### Layer 04: Orchestrator & AI (`04_orchestrator_ai/`)
Filters and prioritizes incoming signals.
- `signal_priority_cascade.py`: The ultimate arbiter. Evaluates signals sequentially against Macro constraints, Earnings Blackouts, Liquidity thresholds, Piotroski Quality rules, and Drawdown Breakers. Signals that fail are marked `REJECTED`. 

### Layer 05: Interfaces (`05_interfaces/`)
- `terminal_dashboard.py`: A Streamlit application providing live Mission Control. It allows manual wallet tracking, signal approvals via SQLite, real-time SHAP explainability charts, and deep-dive ticker analysis.

## 2. Core vs Satellite Framework (Smart DCA)

The engine enforces a strict bifurcation in capital allocation:

- **Core Budget**: Allocated exclusively to the MSCI World ETF (`CW8.PA`). Managed by `smart_dca_engine.py`, which accumulates shares based on the ETF's proximity to its 200-day SMA, accumulating aggressively during dips and passively dripping capital during overheated rallies. This budget is immune to standard risk cascade vetoes (like VIX panic).
- **Satellite Budget**: Allocated to high-conviction European equities. Extremely risk-averse, utilizing strict stop-losses, Piotroski F-Score checks, and liquidity minimums (`MIN_LIQUIDITY_ADV`).

## 3. Execution Schedule & Orchestration

The system runs via `main_scheduler.py` executing a synchronized pipeline:

1. **Market Sync**: Fetches missing OHLCV and fundamental data.
2. **Signal Generation**: Evaluates the universe and assigns conviction scores.
3. **Risk Cascade**: Filters signals through `signal_priority_cascade.py`.
4. **Smart DCA**: Evaluates `CW8.PA` against the cash buffer.
5. **Discord Alerting**: Routes alerts appropriately (urgent tagging for approved paper trades, VIX panics, and drawdown triggers).
6. **Garbage Collection**: Revokes stale pending signals to maintain ledger hygiene.

**Scheduled Execution (Paris Time):**
- **09:00**: Morning Analysis Pass
- **13:30**: Midday Re-evaluation
- **17:10**: Pre-Close Analysis Pass
- **08:35 / 18:00 (Friday)**: ATR Stop-Loss evaluations and profit shaving.

## 4. Setup and Operations

1. Clone and initialize a Python 3.11+ virtual environment.
2. Install dependencies: `pip install -r requirements.txt`.
3. Copy `config/api_keys.env.example` to `config/api_keys.env` and populate webhooks.
4. Initialize the ledger: `python seed_account.py --cash 10000`.
5. Run the orchestrator: `python main_scheduler.py --now`.
6. Launch the dashboard: `streamlit run 05_interfaces/terminal_dashboard.py`.
