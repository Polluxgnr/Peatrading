# PEA Pollux

[![CI](https://github.com/Polluxgnr/Peatrading/actions/workflows/ci.yml/badge.svg)](https://github.com/Polluxgnr/Peatrading/actions/workflows/ci.yml)

A decision-support system for my French PEA (tax-advantaged equity account). Every trading day it scans about 170 Euronext stocks, scores them, runs each candidate through a list of risk checks, and sends me the few that survive with a position size and limit prices. It never places orders: I read the recommendation and decide.

I built it to learn how a real quant pipeline is put together end to end (ingestion, storage, signals, risk, reporting) rather than to beat the market.

## How it works

1. **Data** (`00_data_sensors/`): daily prices from yfinance, fundamentals from Financial Modeling Prep, insider trades and short positions scraped from the AMF register, ECB rates, RSS and newsletter headlines. Raw payloads are kept as JSON so a run can be replayed.
2. **Storage** (`01_memory_core/`): prices in DuckDB, portfolio, signals and audit log in SQLite. Data passes through Pydantic models and a quality gate that catches outliers and stock splits.
3. **Signals** (`02_quant_engine/`):
   - mean reversion: price above its 200-day average, RSI(14) under 30, short-term bounce, positive earnings;
   - pairs trading inside a sector (Engle-Granger cointegration, z-score of the spread);
   - market regime of the CAC 40 with a 3-state Gaussian HMM;
   - FinBERT sentiment on headlines;
   - an XGBoost model that estimates the probability a trade ends positive, with SHAP to explain it and an Isolation Forest to flag unusual market states.
   A UCB bandit re-weights the strategies by regime using the P&L of closed trades.
4. **Risk** (`03_risk_portfolio/`, `04_orchestrator_ai/signal_priority_cascade.py`): a candidate is rejected if any check fails, and the reason is logged: daily/weekly/monthly loss limits, drawdown brake, volatility regime, macro and earnings blackout days, Piotroski F-score below 4, liquidity, AMF short interest above 3%, 25% sector cap, correlation above 0.70 with current holdings. Survivors are sized with half-Kelly scaled by volatility, rounded down to whole shares (the PEA does not allow fractions).
5. **Output** (`05_interfaces/`, `06_api/`, `07_mcp/`): a Streamlit dashboard, a FastAPI service that the dashboard reads from, Discord alerts with three limit-price levels based on ATR, and an MCP server so I can ask Claude about the portfolio.

`main_scheduler.py` runs all of this on Paris market hours (morning scan, midday check, pre-close pass, weekly report, monthly model retraining).

## Stack

Python 3.11, pandas, DuckDB, SQLite, scikit-learn, XGBoost, hmmlearn, statsmodels, Hugging Face Transformers (FinBERT), FastAPI, Streamlit, Plotly, discord.py, Docker. Tests with pytest, linting with Ruff, both run in GitHub Actions.

## Run it

```bash
git clone https://github.com/Polluxgnr/Peatrading.git
cd Peatrading
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp config/api_keys.env.example config/api_keys.env  # keys are optional, missing sources are skipped
python seed_account.py --cash 10000                 # create the paper portfolio
python main_scheduler.py --now                      # one full pass
streamlit run 05_interfaces/terminal_dashboard.py   # http://localhost:8501
```

Other entry points are in the `Makefile` (`make api`, `make mcp`, `make scheduler`, `make test`). `docker compose up` starts the scheduler and the dashboard together.

Risk limits and the universe are plain YAML files in `config/`.

## Tests

```bash
python -m pytest -q
```

About 140 tests cover the signal logic, the risk cascade, sizing, storage, data cleaning and the API.

## Layout

```
00_data_sensors/     scrapers and API clients
01_memory_core/      DuckDB/SQLite access, data contracts, quality checks
02_quant_engine/     signals, regime model, ML training, backtester
03_risk_portfolio/   sizing, circuit breakers, correlation and sector limits
04_orchestrator_ai/  decision cascade, post-trade review, weekly report
05_interfaces/       Streamlit dashboard, Discord bot, trade cards
06_api/ 07_mcp/      FastAPI service and MCP server
config/              universe, risk parameters, calendars
tests/
```

More detail in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Limits

The walk-forward backtester fills at the next day's open but does not model fees or slippage yet. The ML model learns from my own closed trades, so it has little data and its probabilities are rough. Most data comes from free sources that break from time to time. This is a personal research project, not investment advice.
