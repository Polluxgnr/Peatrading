# 🛡️ PEA Sniper Terminal — V-Prime 3.0

> **Sovereign execution. Kinetic risk management. Absolute quantitative transparency.**

An institutional-grade, **zero-leverage** quantitative decision-support system built
strictly for the French **PEA** (Plan d'Épargne en Actions). It ingests market data,
runs a deterministic quant engine, filters signals through a multi-layer risk cascade,
and pushes highly-curated trade proposals to a **Discord Copilot** for **manual**
execution. A **Streamlit terminal** (Bloomberg-style) gives you a full command center.

The system **never sends orders to a broker**. Every trade is executed by you.
The maths decides *what* is worth considering; the AI only *explains* it in plain
language. **This is not investment advice.**

---

## Table of contents

1. [Philosophy](#-philosophy)
2. [What it does (feature map)](#-what-it-does-feature-map)
3. [The strategy in depth](#-the-strategy-in-depth)
4. [Architecture](#-architecture)
5. [Module reference](#-module-reference)
6. [Which APIs actually work](#-which-apis-actually-work)
7. [Installation](#-installation)
8. [Configuration](#-configuration)
9. [Usage](#-usage)
10. [The dashboard](#-the-dashboard)
11. [Deployment](#-deployment)
12. [Scheduling reference](#-scheduling-reference)
13. [Troubleshooting](#-troubleshooting)
14. [Disclaimer](#-disclaimer)

---

## 🧭 Philosophy

1. **No fractional shares.** PEA-legal sizing always uses `math.floor`.
2. **Math first, AI second.** LLMs have **zero** decision power. They only (a) explain
   an already-decided trade, (b) compress news into a **number** (‑100…+100), and
   (c) write a weekly narrative. They never generate or approve a trade.
3. **API-first, scrapers best-effort.** Primary market data is `yfinance` +
   TradingView widgets + Polymarket Gamma. Optional French enrichments
   (Boursorama profile/news, AMF BDIF insiders) are **best-effort scrapers** with
   circuit-breakers and Yahoo fallbacks — AMF BDIF is often WAF-blocked (HTTP 500).
4. **Separation of state.** DuckDB for heavy time-series (OHLCV); SQLite for
   application state (portfolio, positions, immutable audit log).
5. **Zero crash tolerance.** Every scheduled pass is wrapped so a data outage or a
   locked DB logs `CRITICAL` and the daemon survives for the next pass.
6. **Manual execution.** You always have the last word — via a Discord button.

---

## 🚀 What it does (feature map)

| Layer | Capability |
|------|------------|
| **Data** | OHLCV (`yfinance` → DuckDB), VIX/VSTOXX, Put/Call, insiders (Yahoo; AMF best-effort), Polymarket Gamma, Bourso profile/news |
| **Quant** | Mean-Reversion Exhaustion (RSI<30 + Close>SMA200 + Close>SMA5), quality EPS>0 |
| **Core/Satellite** | Smart DCA on `CW8.PA`, regime-aware under SMA200 |
| **Risk** | Macro veto, correlation firewall, sector/line caps, vol-parity sizing, 30% satellite budget, VIX panic brake |
| **Rebalance** | Monthly profit-shave (+20%→trim 20%) and stop (−10%→full exit) |
| **AI (explain only)** | Trade rationale, news sentiment, weekly CIO digest, geo briefing |
| **Interfaces** | Discord Copilot, Streamlit terminal (General multi-horizon, Exploration, Universe sector perfs) |
| **Ops** | Daemon scheduler (Paris), seed CLI, wallet editor, **RevocationEngine** on PENDING |

---

## 📐 The strategy in depth

### 1. Core / Satellite allocation
Capital is split in two:

- **Core (up to 70–75%)** — accumulated on a broad **MSCI World PEA ETF** (`CW8.PA`)
  via **Smart DCA**: when `CW8` trades **below** its 200-day SMA (fear/crash), the
  engine raises the target weight to 75% and buys a larger tranche; **above** the SMA
  it drips a smaller tranche at a 70% target. *(`02_quant_engine/smart_dca_engine.py`)*
- **Satellite (≤30%)** — individual EU stock-picking, hard-capped so the total
  non-core exposure never exceeds `SATELLITE_MAX_BUDGET_PCT` of equity.

### 2. Satellite signal generation
A signal fires only when **all** conditions hold *(`02_quant_engine/technical_scorer.py`)*:

- **Trend:** `Close > SMA200` (long-term uptrend).
- **Exhaustion:** `RSI(14) < 30` (oversold pullback → mean-reversion setup).
- **Quality:** trailing `EPS > 0` (no loss-making hype stocks).
- **Momentum:** `Close > SMA5` (pullback already stabilising — no falling knives).

### 3. Risk cascade (order matters — cheapest checks first)
*(`04_orchestrator_ai/signal_priority_cascade.py`)*

1. **Price sanity** — a live price must exist.
2. **VIX panic brake** — if `V2TX/VIX > 30`, **all new satellite buys are frozen**
   (Core DCA still accumulates — you buy the fear on the broad ETF).
3. **Macro veto** — blocks trades within N days of a known macro event (ECB, CPI…).
4. **Sector cap** — projected sector weight must stay under `MAX_SECTOR_WEIGHT_PCT`.
5. **Correlation firewall** — Pearson vs each holding must stay under the limit.
6. **Volatility-parity sizing** — `target_cash` is scaled **inversely to volatility**
   (a 40%-vol name gets half the allocation of a 20%-vol name), then floored to whole
   shares and clamped by cash and the 30% satellite budget.

### 4. Monthly rebalance *(`03_risk_portfolio/monthly_rebalancer.py`)*
On the 1st of each month:
- **Profit-shave:** any satellite winner above **+20%** → SELL 20% of the shares.
- **Stop-loss:** any satellite position below **‑10%** → SELL 100%.
- The Core ETF is deliberately excluded (it is meant to be held & averaged).

### 5. AI as a post-hoc analyst
- **Trade explainer** — 2–3 sentence rationale for an approved trade.
- **News sentiment** — the LLM is forced to output a single integer **‑100…+100**;
  garbage/prose is neutralised to 0. *(`04_orchestrator_ai/news_sentiment_llm.py`)*
- **Weekly Historian** — every Friday 18:00 Paris, aggregates 7 days of audit logs
  (vetoes, executions, equity/cash) into a hedge-fund-style CIO digest and posts it to
  a Discord webhook. *(`04_orchestrator_ai/weekly_historian.py`)*

---

## 🏗️ Architecture

```
                       ┌──────────────────────────────────────┐
                       │            main_scheduler.py          │
                       │  (Paris time: 09:00 / 13:30 / 17:10)  │
                       └───────────────┬──────────────────────┘
                                       │ orchestrates each pass
   00_data_sensors        01/02              03_risk_portfolio        04_orchestrator_ai
 ┌───────────────┐   ┌──────────────┐   ┌───────────────────────┐   ┌────────────────────┐
 │ market_prices │──▶│ DuckDB (OHLCV)│──▶│ correlation_firewall  │──▶│ signal_priority_    │
 │ macro_alpha   │   │ technical_    │   │ pea_position_sizer    │   │ cascade (conductor)│
 │ (VIX,P/C,ins) │   │ scorer (RSI)  │   │ monthly_rebalancer    │   │ macro_veto         │
 └───────────────┘   │ smart_dca     │   │ (VIX brake, budgets)  │   │ news_sentiment_llm │
                     └──────────────┘   └───────────────────────┘   │ weekly_historian   │
                                                                     └─────────┬──────────┘
   01_memory_core (SQLite state + audit log)                                   │ approved / revoked
 ┌───────────────────────────────────────┐                                    ▼
 │ sqlite_portfolio  ·  data_models       │◀───────────── 05_interfaces ───────────────┐
 └───────────────────────────────────────┘        Discord Copilot · Streamlit terminal │
                                                    · llm_explainer · webhooks           │
                                                   └─────────────────────────────────────┘
```

**Data flow per pass:** `fetch (yfinance → DuckDB)` → `VIX read` → `quant signals`
→ `mark-to-market portfolio` → `risk cascade (+vol sizing, VIX/macro/sector/corr)`
→ `Smart-DCA core` → `audit log (SQLite)` → `Discord alerts`.

---

## 📚 Module reference

| Path | Responsibility |
|------|----------------|
| `00_data_sensors/market_prices_api.py` | Batch OHLCV download → DuckDB |
| `00_data_sensors/macro_alpha_api.py` | `MacroAlphaSensor`: VIX, Put/Call, insider (Yahoo→AMF), Polymarket Gamma |
| `01_memory_core/data_models.py` | Pydantic v2 contracts (`Signal`, `Position`, `PortfolioState`) |
| `01_memory_core/duckdb_manager.py` | `TimeSeriesDB` — OHLCV storage & queries |
| `01_memory_core/sqlite_portfolio.py` | `PortfolioDB` — account/positions/audit log |
| `02_quant_engine/technical_scorer.py` | `SignalGenerator` — indicators, MRE rule + quality/momentum filters |
| `02_quant_engine/smart_dca_engine.py` | `SmartDcaCore` — regime-aware Core ETF DCA |
| `03_risk_portfolio/correlation_firewall.py` | `CorrelationFirewall` — sector cap, Pearson, `check_vix_panic` |
| `03_risk_portfolio/pea_position_sizer.py` | `PeaSizer` — Half-Kelly + volatility parity + satellite budget |
| `03_risk_portfolio/monthly_rebalancer.py` | `PortfolioRebalancer` — profit-shave & stop-loss SELLs |
| `04_orchestrator_ai/macro_veto.py` | `MacroVetoEngine` — macro-event blackout |
| `04_orchestrator_ai/signal_priority_cascade.py` | `SignalOrchestrator` — the conductor |
| `04_orchestrator_ai/revocation_engine.py` | Cancels stale/invalidated signals |
| `04_orchestrator_ai/news_sentiment_llm.py` | `NewsSentimentScorer` — news → integer score |
| `04_orchestrator_ai/weekly_historian.py` | `WeeklyHistorian` — weekly CIO digest |
| `05_interfaces/llm_explainer.py` | `NarrativeExplainer` + shared `openrouter_chat` client |
| `05_interfaces/discord_copilot.py` | Discord bot: alerts + approve/revoke buttons |
| `05_interfaces/terminal_dashboard.py` | Streamlit command center |
| `main_scheduler.py` | Daemon: daily passes, weekly report, monthly rebalance |
| `seed_account.py` | CLI to seed/reset the PEA account |
| `run_discord.py` | Entrypoint for the Discord bot |

---

## 🔌 Which APIs actually work

| Source | Status | Notes |
|--------|--------|-------|
| **yfinance OHLCV** | ✅ Works | Primary market data (batch download → DuckDB). |
| **European VIX `^V2TX`** | ⚠️ **Delisted on Yahoo** | The sensor tries `^V2TX` first, then automatically falls back to **`^VIX`** (US VIX, strongly correlated) as a panic proxy. Swap in a paid VSTOXX feed if you have one. |
| **`^VIX` (fallback)** | ✅ Works | Reliable on Yahoo (~16 at time of writing). |
| **Options Put/Call** | ⚠️ Partial | `yfinance` option chains exist for many US names but are **sparse for EU tickers**; the sensor returns `1.0` (neutral) when unavailable. |
| **Insider transactions** | ⚠️ Partial | Available for some names; returns `0` (neutral) when missing. |
| **Polymarket** | 🔧 Stub | Deterministic placeholder in `[0,1]`, ready to wire to the free CLOB API. |
| **OpenRouter (LLM)** | ✅ Works | Needs `OPENROUTER_API_KEY`. Used for explanations, sentiment, weekly report. Falls back gracefully when absent. |
| **TradingView widgets** | ✅ Works | Ticker tape, advanced chart, technical-analysis gauge (client-side embeds). |
| **Yahoo news** | ✅ Works | Powers the Radar news feed and the sentiment input. |

> **Key takeaway:** the system is designed to **degrade gracefully**. Any dead/missing
> source returns a neutral value and logs the reason — the daemon never crashes.

---

## ⚙️ Installation

> **Important:** Streamlit depends on `pyarrow`, which has **no prebuilt wheel for
> Python 3.13 / arm64**. Use **Python 3.11 or 3.12 (x64)** for the dashboard. The
> backend/daemon runs fine on 3.11–3.13.

```bash
# 1. Clone
git clone <your-repo-url> pea_sniper_terminal
cd pea_sniper_terminal

# 2. Create an x64 Python 3.11 virtual environment
python3.11 -m venv venv_x64
# Windows PowerShell:
venv_x64\Scripts\Activate.ps1
# Linux/macOS:
source venv_x64/bin/activate

# 3. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 4. Configure secrets
cp config/api_keys.env.example config/api_keys.env
#   → edit config/api_keys.env with your real keys
```

---

## 🔧 Configuration

### `config/api_keys.env` (secrets — git-ignored)

| Variable | Required | Purpose |
|----------|----------|---------|
| `DISCORD_TOKEN` | for bot | Discord bot token (Copilot with buttons). |
| `DISCORD_CHANNEL_ID` | for bot | Channel where alerts are posted. |
| `DISCORD_WEBHOOK_URL` | for daemon | Weekly report + monthly rebalance notifications (no bot process needed). |
| `OPENROUTER_API_KEY` | optional | Enables LLM explanations/sentiment/report. |
| `OPENROUTER_MODEL` | optional | Defaults to `mistralai/mistral-7b-instruct`. |

### `config/risk_params.yaml` (the rulebook)

| Group | Keys |
|-------|------|
| **Sizing** | `KELLY_FRACTION`, `MAX_SINGLE_POSITION_PCT`, `MAX_SECTOR_WEIGHT_PCT`, `MAX_ALLOCATION_PER_DAY_PCT` |
| **Circuit breakers** | `DAILY_MAX_LOSS_PCT`, `WEEKLY_MAX_LOSS_PCT`, `MONTHLY_MAX_LOSS_PCT` |
| **Correlation** | `MAX_CORRELATION_TO_PORTFOLIO`, `MAX_CORRELATION_SAME_SECTOR` |
| **Signals** | `SIGNAL_BUY_THRESHOLD`, `SIGNAL_SELL_THRESHOLD`, `MACRO_VETO_DAYS_BEFORE` |
| **Core/Satellite** | `CORE_TICKER`, `CORE_TARGET_PCT`, `CORE_CRASH_TARGET_PCT`, `CORE_DCA_MAX_TRANCHE_PCT`, `SATELLITE_MAX_BUDGET_PCT` |
| **Volatility/VIX** | `VOLATILITY_REFERENCE`, `VOLATILITY_MAX_FACTOR`, `VIX_PANIC_THRESHOLD` |
| **Rebalance** | `REBALANCE_PROFIT_TRIGGER_PCT`, `REBALANCE_PROFIT_SHAVE_PCT`, `REBALANCE_STOPLOSS_TRIGGER_PCT` |

### `config/pea_universe.yaml`
~80 PEA-eligible EU/EEA tickers grouped by sector. Add/remove entries freely; the
sector map feeds the correlation firewall and the dashboard.

---

## 🕹️ Usage

```bash
# 0. Seed your capital (once) — e.g. a 10,000 EUR PEA, 100% cash:
python seed_account.py --cash 10000

#    Seed with existing holdings (TICKER:QTY:AVG[:SECTOR]):
python seed_account.py --cash 8000 --position MC.PA:3:620:Luxury
#    Inspect current state:
python seed_account.py --show
#    Reset everything:
python seed_account.py --cash 25000 --reset

# 1. Run ONE analysis pass immediately (great for testing):
python main_scheduler.py --now

# 2. Run the weekly report now (posts to DISCORD_WEBHOOK_URL):
python main_scheduler.py --weekly

# 3. Run the monthly rebalancer now (ignores the 1st-of-month guard):
python main_scheduler.py --rebalance

# 4. Start the daemon (loops forever, Paris schedule):
python main_scheduler.py

# 5. Launch the Discord Copilot bot:
python run_discord.py

# 6. Launch the Streamlit terminal:
venv_x64\Scripts\streamlit run 05_interfaces/terminal_dashboard.py
#    → open http://localhost:8501
```

---

## 🖥️ The dashboard

Launch (auto-opens browser):

```powershell
.\run_dashboard.ps1
```

Bloomberg-style black UI. Tabs:

| Tab | What you get |
|-----|----------------|
| **General & Signaux** | Adaptive **multi-horizon** portfolio suggestion (MICRO→FULL), explicit *why 1 share + cash*, Core ETF card, geo brief, Discord ledger, **biggest news of the month** |
| **Portefeuille** | Sunburst + positions + **wallet editor** (writes SQLite) |
| **Exploration** | Liquid scan top/flop + trajectories (no more fake 0% rows), full ticker dossier (business, catalysts, risk events), TA explained, news, insiders, Polymarket with clickable links |
| **Univers Complet** | Universe table + **average sector performance** with selectable timeframe |
| **Architecture** | Living docs (matches the code) |

**Sidebar:** auto-refresh, cache clear, system status.

---

## 🐳 Deployment

Two containers: the **daemon** (backend, always-on) and the **dashboard**
(Streamlit UI). Both share the `database/` volume and `config/`.

```bash
# 1. Prepare secrets
cp config/api_keys.env.example config/api_keys.env   # then edit it

# 2. Build & launch the fleet
docker compose up -d --build

# 3. Access
#    Dashboard: http://<server-ip>:8501
#    Logs:      docker compose logs -f daemon
#               docker compose logs -f dashboard

# 4. Seed capital inside the container (once)
docker compose exec daemon python seed_account.py --cash 10000

# 5. Stop
docker compose down
```

### Alternatives

- **systemd** (bare-metal daemon): create a unit that runs
  `.../venv_x64/bin/python main_scheduler.py` with `Restart=always`.
- **cron** (if you prefer not to keep a process alive): call
  `python main_scheduler.py --now` at 09:00/13:30/17:10 and
  `--weekly` / `--rebalance` on their schedules.

---

## ⏱️ Scheduling reference

| Job | When (Europe/Paris) | Action |
|-----|---------------------|--------|
| Analysis pass | 09:00, 13:30, 17:10 (weekdays) | Full pipeline → Discord alerts |
| Weekly report | Friday 18:00 | Historian digest → webhook |
| Monthly rebalance | Daily probe 08:30 (acts only on the 1st) | Profit-shave / stop-loss SELLs → webhook |

Weekends are skipped automatically for analysis passes.

---

## 🩺 Troubleshooting

| Symptom | Fix |
|---------|-----|
| Dashboard shows "En attente de l'initialisation…" | Seed: `python seed_account.py --cash 10000` (or 100). |
| `run_dashboard.ps1` parse error on `--server.*` | Use the single-line launcher in repo (no broken backticks). |
| `pyarrow`/`streamlit` install fails | Python **3.11/3.12 x64** (`venv_x64`). |
| VIX always 15.0 / `^V2TX` 404 | Falls back to `^VIX` — threshold is still `VIX_PANIC_THRESHOLD`. |
| AMF BDIF HTTP 500 | Expected; circuit opens **12h** then retries; Yahoo insiders used. |
| Worst perf shows `0.00%` / pennies | Liquid blue-chip scan only; filter price ≥ 2 €. Clear cache. |
| `AttributeError: float has no attribute bar` | Fixed (`plotly.express` aliased `pex` — was shadowed by price var `px`). |
| Browser opens twice | Fixed: `run_dashboard.ps1` no longer double-opens URL. |
| Duplicate news blocks | Merged into one Actualites section. |
| No LLM text / sentiment = 0 | Set `OPENROUTER_API_KEY` in `config/api_keys.env`. |
| Weekly report not sent | Set `DISCORD_WEBHOOK_URL`. |
| All BUYs "insufficient cash" | Seed capital, or cash < 1 share price. |

---

## ⚠️ Disclaimer

This software is a **decision-support and educational tool**. It performs **no
automated execution** and constitutes **no financial advice**. Markets carry risk;
you are solely responsible for every trade you place. Past/backtested performance
does not guarantee future results.

© 2026 Pollux Quantitative Research — V-Prime 3.0.
