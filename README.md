# PEA Sniper Terminal — V-Prime 3.0

> **Sovereign execution. Kinetic risk management. Absolute quantitative transparency.**

Zero-leverage quantitative **decision support** for the French **PEA**. Market data →
deterministic quant engine → multi-layer risk cascade → **Discord Copilot** for
**manual** execution. A Bloomberg-style **Streamlit** terminal is the command center.

The system **never sends orders to a broker**. Maths decides *what* is worth
considering; AI only *explains*. **Not investment advice.**

---

## Table of contents

1. [Philosophy](#-philosophy)
2. [Feature map](#-feature-map)
3. [Strategy](#-strategy)
4. [Architecture](#-architecture)
5. [Module reference](#-module-reference)
6. [APIs that work](#-apis-that-work)
7. [Installation](#-installation)
8. [Configuration](#-configuration)
9. [Usage](#-usage)
10. [Dashboard](#-dashboard)
11. [Deployment](#-deployment)
12. [Scheduling](#-scheduling)
13. [Roadmap / future improvements](#-roadmap--future-improvements)
14. [Troubleshooting](#-troubleshooting)
15. [Disclaimer](#-disclaimer)

---

## Philosophy

1. **No fractional shares.** PEA sizing always uses `math.floor`.
2. **Math first, AI second.** LLMs never generate or approve trades — they explain,
   score news (−100…+100), and write the weekly digest.
3. **Official sources first.** Insider cascade is **AMF BDIF → FMP → yfinance**.
   Market OHLCV stays on `yfinance` → DuckDB. Scrapers are best-effort with
   circuit-breakers (AMF BDIF is often WAF-blocked).
4. **Split state.** DuckDB = OHLCV; SQLite = portfolio, audit log, **equity curve**.
5. **Zero crash tolerance.** A failed pass logs `CRITICAL`; the daemon keeps running.
6. **Manual execution.** You always have the last word (Discord buttons).

---

## Feature map

| Layer | Capability |
|------|------------|
| **Data** | OHLCV → DuckDB; VIX/VSTOXX; Put/Call; insiders **AMF→FMP→Yahoo**; Polymarket Gamma; Bourso profile/news |
| **Quant** | Mean-reversion exhaustion (RSI&lt;30 + Close&gt;SMA200 + Close&gt;SMA5), EPS&gt;0 |
| **Core/Satellite** | Smart DCA on `CW8.PA`, regime-aware under SMA200 |
| **Risk** | Macro veto, correlation firewall, sector/line caps, vol-parity sizing, 30% satellite budget, VIX panic |
| **Rebalance** | Monthly: +20% profit-shave; **dynamic ATR stop** (`price < entry − 2.5×ATR14`) |
| **Memory** | Daily `portfolio_history` equity curve in SQLite |
| **AI (explain only)** | Trade rationale, news sentiment, weekly CIO digest, geo brief |
| **UI** | Discord Copilot + Streamlit (multi-horizon, equity curve, Exploration, Universe) |
| **Ops** | Paris daemon, seed CLI, wallet editor, RevocationEngine on PENDING |

---

## Strategy

### 1. Core / Satellite
- **Core (≈70–75%)** — `CW8.PA` via Smart DCA: more aggressive below SMA200, drip above.
- **Satellite (≤30%)** — EU stock-picking under `SATELLITE_MAX_BUDGET_PCT`.

### 2. Satellite signal (all must hold)
Trend `Close > SMA200` · Exhaustion `RSI(14) < 30` · Quality `EPS > 0` · Momentum `Close > SMA5`.

### 3. Risk cascade (cheap checks first)
1. Live price exists  
2. VIX panic (`V2TX/VIX > 30`) freezes **new satellite buys** (Core still DCAs)  
3. Macro event blackout  
4. Sector / correlation caps  
5. Vol-parity sizing → whole shares → cash + satellite budget clamp  

### 4. Monthly rebalance
- **Profit-shave:** satellite &gt; +20% → SELL 20%.  
- **ATR stop:** losing satellite with `current < avg_entry − 2.5×ATR(14)` → SELL 100%.  
- Core ETF excluded.

### 5. AI as analyst only
Trade explainer · news → integer score · Friday CIO digest → Discord webhook.

---

## Architecture

```
                       ┌──────────────────────────────────────┐
                       │            main_scheduler.py          │
                       │  (Paris: 09:00 / 13:30 / 17:10)       │
                       └───────────────┬──────────────────────┘
   00_data_sensors        01/02              03_risk_portfolio        04_orchestrator_ai
 ┌───────────────┐   ┌──────────────┐   ┌───────────────────────┐   ┌────────────────────┐
 │ market_prices │──▶│ DuckDB OHLCV │──▶│ correlation_firewall  │──▶│ signal_priority_    │
 │ macro_alpha   │   │ technical_   │   │ pea_position_sizer    │   │ cascade + macro     │
 │ AMF→FMP→YF    │   │ scorer+DCA   │   │ monthly ATR rebalancer│   │ revocation / LLM    │
 └───────────────┘   └──────────────┘   └───────────────────────┘   └─────────┬──────────┘
   SQLite: portfolio · audit · equity curve                                   ▼
                                      Discord Copilot · Streamlit terminal
```

**Per pass:** fetch → VIX → signals → mark-to-market (+ equity snapshot) → risk cascade
→ Smart-DCA → audit → Discord. **1st of month:** ATR/profit rebalance SELLs.

---

## Module reference

| Path | Responsibility |
|------|----------------|
| `00_data_sensors/market_prices_api.py` | Batch OHLCV → DuckDB |
| `00_data_sensors/macro_alpha_api.py` | VIX, Put/Call, insiders (**AMF→FMP→YF**), Polymarket |
| `00_data_sensors/scrapers/amf_scraper.py` | Official AMF BDIF insider scrape + 12h circuit |
| `01_memory_core/sqlite_portfolio.py` | Portfolio, audit log, **`portfolio_history` equity curve** |
| `01_memory_core/duckdb_manager.py` | OHLCV store (feeds ATR) |
| `02_quant_engine/technical_scorer.py` | MRE signals + quality/momentum |
| `02_quant_engine/smart_dca_engine.py` | Regime-aware Core DCA |
| `03_risk_portfolio/monthly_rebalancer.py` | Profit-shave + **2.5×ATR14** stops |
| `03_risk_portfolio/pea_position_sizer.py` | Half-Kelly × vol parity × PEA floor |
| `03_risk_portfolio/correlation_firewall.py` | Sector/Pearson + VIX panic |
| `04_orchestrator_ai/*` | Cascade, macro veto, revocation, sentiment, historian |
| `05_interfaces/terminal_dashboard.py` | Streamlit command center |
| `05_interfaces/discord_copilot.py` | Alerts + approve/revoke |
| `main_scheduler.py` | Daemon: passes, weekly, monthly |
| `tools/build_llm_dump.py` | Regenerate `PROJECT_FULL_DUMP_FOR_LLM.md` |
| `tools/sync_universe_from_bourso.py` | Refresh PEA universe YAML |

---

## APIs that work

| Source | Status | Notes |
|--------|--------|-------|
| **yfinance OHLCV** | Works | Primary market data → DuckDB |
| **`^V2TX` / `^VIX`** | Partial | VSTOXX often delisted on Yahoo → falls back to US VIX |
| **AMF BDIF** | Fragile | Official FR insiders; WAF/HTTP 500 common → circuit + fallbacks |
| **FMP insider API** | Optional | Needs `FMP_API_KEY`; secondary after AMF |
| **yfinance insiders** | Tertiary | Sparse on many `.PA` names |
| **Options Put/Call** | Partial | Sparse for EU → neutral `1.0` |
| **Polymarket Gamma** | Live | Macro context only |
| **OpenRouter** | Optional | Explanations / sentiment / weekly report |
| **TradingView / Yahoo news** | Works | UI embeds + radar |

Graceful degradation: missing sources return **neutral** values; the daemon does not crash.

---

## Installation

> Streamlit needs `pyarrow` → use **Python 3.11 or 3.12 x64** (`venv_x64`).

```bash
git clone https://github.com/Polluxgnr/Peatrading.git pea_sniper_terminal
cd pea_sniper_terminal

python3.11 -m venv venv_x64
# Windows:  venv_x64\Scripts\Activate.ps1
# Unix:     source venv_x64/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

cp config/api_keys.env.example config/api_keys.env
# edit secrets
```

---

## Configuration

### `config/api_keys.env` (git-ignored)

| Variable | Required | Purpose |
|----------|----------|---------|
| `DISCORD_TOKEN` / `DISCORD_CHANNEL_ID` | bot | Copilot with buttons |
| `DISCORD_WEBHOOK_URL` | daemon | Weekly + monthly notifications |
| `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` | optional | LLM explain / sentiment |
| `FMP_API_KEY` | optional | Secondary insider source |
| `EODHD_API_KEY` | optional | Reserved for paid market data |

### `config/risk_params.yaml`

Sizing · circuit breakers · correlation · Core/Satellite · VIX ·  
`REBALANCE_PROFIT_*` · **`REBALANCE_ATR_STOP_MULT`** (default `2.5`).

### `config/pea_universe.yaml`

~600 PEA-eligible Euronext names by sector (synced via Bourso tools). Feeds firewall + dashboard.

---

## Usage

```bash
python seed_account.py --cash 10000          # seed once
python seed_account.py --show
python main_scheduler.py --now               # one pass
python main_scheduler.py --weekly
python main_scheduler.py --rebalance         # ATR / shave now
python main_scheduler.py                     # daemon
python run_discord.py
.\run_dashboard.ps1                          # Streamlit (auto-open)
python tools/build_llm_dump.py               # refresh LLM dump
```

---

## Dashboard

| Tab | Content |
|-----|---------|
| **General & Signaux** | Adaptive multi-horizon suggestion (MICRO→FULL), Core card, geo, ledger, month news |
| **Portefeuille** | **Equity curve**, sunburst, positions, wallet editor → SQLite |
| **Exploration** | Liquid scan, ticker dossier, TA explain, news, insiders (AMF→FMP→YF), Polymarket |
| **Univers** | Full list + sector average performance |
| **Architecture** | Living docs (matches code) |

---

## Deployment

```bash
cp config/api_keys.env.example config/api_keys.env
docker compose up -d --build
# Dashboard :8501 · logs: docker compose logs -f daemon
docker compose exec daemon python seed_account.py --cash 10000
```

Or systemd / cron calling `main_scheduler.py --now` / `--weekly` / `--rebalance`.

---

## Scheduling

| Job | When (Europe/Paris) | Action |
|-----|---------------------|--------|
| Analysis | 09:00, 13:30, 17:10 weekdays | Full pipeline → Discord |
| Weekly report | Friday 18:00 | Historian → webhook |
| Monthly rebalance | Probe 08:30 (acts on the 1st) | ATR stop / profit-shave → webhook |

---

## Roadmap / future improvements

Prioritized ideas that fit the current architecture:

| Priority | Idea | Why |
|----------|------|-----|
| **P0** | **Daily ATR stop check** (not only 1st of month) | Volatility stops should not wait 30 days |
| **P0** | **pytest + CI** on cascade, sizer, AMF/FMP cascade, ATR edge cases | Regressions are silent today |
| **P1** | **Equity metrics** on the curve (max DD, CAGR, Sharpe, cash %) | Curve alone is not enough to judge process |
| **P1** | **Read-only broker import** (CSV / Boursorama / Degiro) | Kill manual wallet drift |
| **P1** | **Walk-forward backtester** on DuckDB OHLCV | Validate MRE + ATR params before live capital |
| **P2** | **Paid VSTOXX** (or Stooq/EODHD) instead of `^VIX` proxy | Panic brake should be European |
| **P2** | **AMF resilience** (ISIN cache, retry jitter, optional proxy) | Keep official source usable more often |
| **P2** | **Earnings / dividend blackout** in macro calendar | Avoid event-driven gaps on satellites |
| **P3** | **Multi-core ETF** rotation (CW8 / EWLD / ESE / PAEEM) | Regime-aware core, still PEA-legal |
| **P3** | **Intraday tape + Discord digest** of veto reasons | Operator learning loop |
| **P3** | **Position-level trailing ATR** after +20% shave | Lock gains without fixed % |
| **P3** | Wire **EODHD** for EU fundamentals when Yahoo is thin | Better EPS / quality filter |

Non-goals (keep out of scope): auto-broker execution, leverage, US penny universe, LLM-as-trader.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Dashboard « En attente… » | `python seed_account.py --cash 10000` |
| Empty equity curve | Appears after `update_portfolio` / wallet save / pipeline pass |
| `pyarrow` / Streamlit fail | Python **3.11/3.12 x64** |
| VIX stuck / `^V2TX` 404 | Falls back to `^VIX` |
| AMF HTTP 500 | Expected; FMP then Yahoo; circuit resets after ~12h |
| No FMP insiders | Set `FMP_API_KEY` in `api_keys.env` |
| ATR stop never fires | Need DuckDB history (`--now` fetch) + losing position |
| LLM / weekly silent | `OPENROUTER_API_KEY` / `DISCORD_WEBHOOK_URL` |
| Cash too small for CW8 | MICRO mode: 1 liquid share + cash runway (by design) |

---

## Disclaimer

Decision-support and educational tool only. **No automated execution. No financial
advice.** You are solely responsible for every trade. Past results do not guarantee
future performance.

© 2026 Pollux Quantitative Research — V-Prime 3.0 (Phase 15).
