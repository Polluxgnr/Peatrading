# PEA Sniper Terminal V-Prime

Institutional-grade, **zero-leverage** algorithmic trading system designed
strictly for the French **PEA** (Plan d'Épargne en Actions). The universe is
restricted to EU large/mid-cap equities and execution is **manual** via a
Discord Copilot — the system never sends orders to a broker.

## Core principles

1. **No fractional shares.** Position sizing always uses `math.floor`.
2. **Math first, AI second.** LLMs have zero decision power; they only explain
   the mathematical decision in the interface layer.
3. **API-first, no scraping.** Data comes from `yfinance`, Google News RSS and
   Trading Economics APIs.
4. **State management.** DuckDB for heavy time-series (OHLCV/indicators),
   SQLite for application state (portfolio, audit logs, signal states).
5. **Copilot execution.** Highly filtered signals are pushed to Discord; the
   user executes manually and confirms via a button.

## Directory structure

```
00_data_sensors/     # yfinance, RSS, Macro APIs
01_memory_core/      # DuckDB (time-series) & SQLite (state) + data contracts
02_quant_engine/     # RSI, SMA, mean-reversion stats (pandas-ta)
03_risk_portfolio/   # Correlation firewall, Kelly sizing, vetoes
04_orchestrator_ai/  # Signal validation, revocation, LLM explanation
05_interfaces/       # Discord bot (discord.py), Web dashboard (Streamlit)
config/              # YAML constraints, universes, env variables
```

## Status

**Phase 1 complete** — project scaffolding, configuration files, and strict
Pydantic V2 data contracts (`01_memory_core/data_models.py`). No trading logic,
API calls, or database code has been written yet.

## Getting started

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Unix:     source .venv/bin/activate
pip install -r requirements.txt
```

## Roadmap (subsequent phases)

- **Phase 2** — Memory core: SQLite portfolio + DuckDB time-series managers.
- **Phase 3** — Data sensors: `yfinance` batch ingestion.
- **Phase 4** — Quant engine: RSI/SMA scoring, raw signal generation.
- **Phase 5** — Risk portfolio: correlation firewall + PEA position sizing.
- **Phase 6** — Orchestrator: macro veto + revocation engine.
- **Phase 7** — Discord copilot.
- **Phase 8** — Streamlit dashboard.
- **Phase 9** — Main scheduler (09:00 / 13:30 / 17:10 passes).
