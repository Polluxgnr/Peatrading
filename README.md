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
| **Rebalance** | Daily ATR stop (`--atr-stops`); monthly +20% profit-shave |
| **Memory** | Daily equity curve + shared `equity_metrics` (DD/CAGR/Sharpe/Sortino) |
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

### 4. Exits
- **Daily ATR stop:** losing satellite with `current < avg_entry − 2.5×ATR(14)` → SELL 100%.  
- **Monthly profit-shave:** satellite &gt; +20% → SELL 20%.  
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

Sizing · circuit breakers · correlation (`CORRELATION_LOOKBACK_DAYS`) · Core/Satellite · VIX ·  
`REBALANCE_PROFIT_*` · `REBALANCE_ATR_STOP_MULT` · `EARNINGS_BLACKOUT_DAYS` ·  
`MIN_LIQUIDITY_ADV` · `MAX_POSITIONS_TOTAL` · `RSI_OVERSOLD_THRESHOLD`.

### `config/pea_universe.yaml`

~600 PEA-eligible Euronext names by sector (synced via Bourso tools). Feeds firewall + dashboard.

---

## Usage

```bash
python seed_account.py --cash 10000          # seed once
python seed_account.py --show
python main_scheduler.py --now               # one pass
python main_scheduler.py --weekly
python main_scheduler.py --rebalance         # profit-shave now
python main_scheduler.py --atr-stops         # ATR stops now
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
| ATR stops | 08:35 weekdays | Dynamic ATR SELLs → webhook |
| Profit-shave | Probe 08:30 (acts on the 1st) | +20% trim → webhook |

---

## Roadmap / future improvements

Prioritized after Phase 15/16 wiring feedback. **Diff-only broker import** (never
blind overwrite). Prefer official/API sources over furtive HTML scraping.

### P0 — ship next / in progress

| Item | Notes |
|------|-------|
| **Daily ATR stops** | ✅ Split from monthly profit-shave; probe weekdays 08:35 (`--atr-stops`) |
| **pytest + CI** | ✅ Minimal suite + GitHub Actions; expand coverage continuously |
| **Equity metrics (shared)** | ✅ `equity_metrics.py` (max DD / CAGR / Sharpe / Sortino) on live curve — **same functions for the future backtester** |

### P0 / P1 — next up

| Item | Notes |
|------|-------|
| **Earnings / dividend blackout** | ✅ Engine + empty `earnings_calendar.yaml` + cascade hook; fill calendar (API later) |
| **Walk-forward backtester** | Biggest ROI: turns “system that runs” into “strategy validated empirically”; reuse `equity_metrics` |
| **Broker CSV import (diff)** | Diff Boursorama CSV vs SQLite; show missing/qty mismatches — **never blind overwrite** |

### New cascade / risk params (config ready)

| Key | Role |
|-----|------|
| `EARNINGS_BLACKOUT_DAYS` | Per-ticker corporate blackout window |
| `MIN_LIQUIDITY_ADV` | Floor on average daily € volume |
| `MAX_POSITIONS_TOTAL` | Cap on simultaneous satellite lines |
| `CORRELATION_LOOKBACK_DAYS` | Explicit Pearson window (was hardcoded 60) |
| `RSI_OVERSOLD_THRESHOLD` | Calibrable later via walk-forward (vol-regime adaptive) |

**ATR note:** stop distance uses **absolute** ATR (correct per name; ATR scales with
price). `atr_pct = ATR/price` is logged for cross-name comparison / dashboards —
use % for vol-parity style comparisons, absolute for the stop rule.

### Additional signals (post-backtester calibration)

| Signal | Role |
|--------|------|
| Relative strength vs sector / CAC40 (3–6m) | Filter structurally broken RSI&lt;30 names |
| Distance to 52w high/low | Cheap DuckDB confirmation beside SMA200/RSI |
| Analyst revision drift (`yfinance` upgrades) | Soft consensus signal, not a hard filter |
| EUR/USD context for `CW8.PA` | Info in weekly CIO digest (USD FX exposure), not a veto |

### Data sources (legal preference)

1. **Official / regulator** — AMF (done), Euronext corporate actions, ECB SDW / INSEE  
2. **Macro APIs with free tier** — Trading Economics → auto-sync `macro_calendar.yaml`  
3. **Structured commercial APIs** — FMP (already secondary for insiders), EODHD  
4. **HTML scrapers last** — Boursorama / Zonebourse / Investing: fragile + ToS grey zone; keep minimal  

### Dashboard visualizations (queued)

| Viz | Purpose |
|-----|---------|
| Signal funnel waterfall | raw → VIX → macro → earnings → liquidity → sector → corr → sizing → approved |
| Rejection motif pie (30/90d) | Expose `weekly_historian._classify` in UI |
| Per-ticker RSI/SMA200 sparkline | Audit false negatives with approve/reject markers |
| Rolling Sharpe / Sortino / DD | Built on `equity_metrics` |
| Richer ticker dossier | More `yfinance.info` fields, insider table (done path), per-article LLM scores, portfolio correlation heatmap |

### P2 / P3

Paid VSTOXX · AMF resilience · multi-core ETF rotation · trailing ATR after shave ·
intraday Discord veto digest.

**Non-goals:** auto-broker execution, leverage, LLM-as-trader, US pennies.

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
