# PEA Pollux — Terminal quantitatif personnel

> **Un bureau d'analyse quantitatif pour votre PEA — transparent, manuel, sans exécution automatique.**

**PEA Pollux** est un terminal de recherche et de suivi de portefeuille conçu pour un
**PEA personnel** (Plan d'Épargne en Actions). Il ingère les données de marché,
calcule des signaux multi-facteurs, applique une cascade de risque stricte, puis
présente des propositions **à valider manuellement** dans le dashboard Streamlit
ou via Discord.

**Le système n'envoie jamais d'ordres à un courtier.** Les modèles quantitatifs
décident *ce qui mérite d'être étudié* ; l'IA *explique* (rationale, sentiment,
briefing hebdo, red teaming Bull/Bear). **Ce n'est pas un conseil en investissement.**

Repo: [github.com/Polluxgnr/Peatrading](https://github.com/Polluxgnr/Peatrading)

---

## Pourquoi PEA Pollux ?

| Besoin | Réponse |
|--------|---------|
| Comprendre *pourquoi* un signal apparaît | Score multi-modèle + Data Lake transparent |
| Gérer le risque avant d'acheter | Cascade VIX, corrélation, liquidité, earnings blackout |
| Suivre la performance | Courbe d'equity, VaR/CVaR, Monte Carlo corrélé |
| Challenger une idée | Red teaming IA (Bull vs Bear + Judge) |
| Déployer proprement | Docker, healthchecks, logs rotatifs, CI pytest |

---

## Table of contents

1. [Philosophy](#-philosophy)
2. [Feature map](#-feature-map)
3. [Strategy in depth](#-strategy-in-depth)
4. [Architecture](#-architecture)
5. [Logging & observability](#-logging--observability)
6. [Module reference](#-module-reference)
7. [APIs that work](#-apis-that-work)
8. [Installation](#-installation)
9. [Configuration](#-configuration)
10. [Usage](#-usage)
11. [Dashboard](#-dashboard)
12. [LLM full dump](#-llm-full-dump)
13. [Deployment](#-deployment)
14. [Scheduling](#-scheduling)
15. [Roadmap](#-roadmap--future-improvements)
16. [Troubleshooting](#-troubleshooting)
17. [Disclaimer](#-disclaimer)
18. [English guide](#english-guide)

---

## Philosophy

1. **No fractional shares.** PEA sizing always uses `math.floor` — one share or nothing.
2. **Math first, AI second.** LLMs never generate or approve trades. They only:
   explain an already-decided signal, compress news into an integer (−100…+100),
   and write the Friday CIO digest.
3. **Official sources first.** Insider cascade is strict:
   **AMF BDIF → FMP → yfinance**. OHLCV stays on `yfinance` → DuckDB. HTML
   scrapers are best-effort with circuit-breakers (AMF BDIF is often WAF-blocked).
4. **Split state.** DuckDB = heavy OHLCV; SQLite = portfolio, positions, immutable
   audit log, **daily equity curve** (`portfolio_history`), and **news archive**
   (`news_history` — cross-session headlines with real timestamps).
5. **Zero crash tolerance.** A failed pass logs `CRITICAL` and writes a red
   pipeline heartbeat; the daemon keeps running for the next slot.
6. **Manual execution.** You always have the last word (Discord **or** Streamlit
   Approuver / Rejeter → SQLite).
7. **Personal portfolio demo, not a SaaS fleet.** Observability is detailed and
   copy-friendly, but deliberately human-scale (rotating local logs, Mission Control).

---

## Feature map

| Layer | What it does (why it exists) |
|------|------------------------------|
| **Data** | OHLCV → DuckDB; VIX/VSTOXX; Put/Call; insiders **AMF→FMP→Yahoo**; Polymarket Gamma; Bourso + **Google News / Yahoo** news (archived in **`news_history`**); **newsletter IMAP** (whitelist) |
| **Quant** | **Ensemble conviction (0–100)**: MR ≤35 + Vol ≤25 + Insider ≤20 + Inst ≤20 + **News/Polymarket modifiers** — emit if ≥65 |
| **Core/Satellite** | Smart DCA on `CW8.PA` (more aggressive under SMA200); satellites capped ~30% equity |
| **Risk cascade** | VIX panic, **EPS &lt; 0**, macro veto, **earnings blackout**, max satellite lines, **ADV € floor**, sector, correlation, vol-parity sizing |
| **Exits** | **Daily** ATR stop (`price < entry − 2.5×ATR14`); **monthly** +20% profit-shave |
| **Memory** | SQLite equity curve + **`news_history`** + shared `equity_metrics` + `morning_briefing.json` Zeitgeist |
| **AI (explain only)** | Trade rationale, news sentiment, weekly digest, geo brief, **morning newsletter Zeitgeist**, deep news synthesis (24h cache) |
| **UI** | Mission Control + **native HTML ticker tape** + Discord + Streamlit (**Command Center**, funnel, radar, what-if, **order ticket**, **decision checklist**, **live telemetry**) |
| **Ops** | Paris daemon (incl. **08:25 briefing**), session auto-sync on dashboard open, walk-forward scaffold, seed CLI, CI pytest |

---

## Strategy in depth

### 1. Core / Satellite allocation

Capital is split so the PEA stays diversified even when stock-picking is quiet:

- **Core (~70–75%)** — Amundi MSCI World PEA ETF (`CW8.PA`) via **Smart DCA**.
  When CW8 trades **below** its 200-day SMA (fear), the engine raises the target
  weight and buys a larger tranche; **above** the SMA it drips smaller amounts.
- **Satellite (≤30%)** — individual EU names under `SATELLITE_MAX_BUDGET_PCT`.
  Also capped by `MAX_POSITIONS_TOTAL` so the 30% budget is not fragmented into
  too many tiny lines.

### 2. Empreinte Multi-Stratégies (how signals are scored)

Every ticker receives a **score from 0 to 100** called the **Empreinte**
(fingerprint). It combines four weighted axes into a single conviction number.
A BUY signal is only emitted when **conviction ≥ 65**. Hard vetoes (VIX,
EPS < 0) are enforced later in the risk cascade — scoring runs first.

| Abbreviation | Axis | Weight | What it measures |
|:------------:|------|:------:|------------------|
| **MR** | Mean Reversion | 35 % | Statistical under-valuation: RSI-14 oversold + price above long-term SMA-200. A Z-Score < −2 on a 50-day window adds a bonus. |
| **Mom** | Momentum | 25 % | Trend strength: Close > SMA-5 > SMA-50 > SMA-200, MACD histogram positive and growing, close near upper Bollinger Band. |
| **Q/V** | Quality / Value | 20 % | Fundamentals from Finnhub or yfinance: low P/E (< 15 = high score), low P/B (< 2 = bonus), high ROE (> 15 %), low Debt/Equity. |
| **Ins** | Insider Confidence | 20 % | Directors buying their own stock: AMF/FMP/EODHD buy-cluster ≥ 2 = max, single buy = half. |

**Modifiers** applied on top of the base score:

| Modifier | Range | Source |
|----------|-------|--------|
| News sentiment | +10 / −15 | LLM integer score (−100…+100) or heuristic keyword fallback |
| Polymarket macro | +10 / −10 | YES probability ≥ 0.62 → bullish, ≤ 0.38 → bearish (context only) |

The final score is clamped to 0–100 and displayed in the dashboard as a
**polar radar chart** (Exploration tab). Colour coding: **amber 65–75** /
**neon 76–100**.

### 3. Risk cascade (order matters — cheap checks first)

Implemented in `signal_priority_cascade.py`:

0. Live price exists  
1. **VIX panic** — if V2TX/VIX &gt; `VIX_PANIC_THRESHOLD`, freeze **new satellite buys** (Core DCA still runs)  
1b. **EPS &lt; 0** — quality veto (Orchestrator)  
2. **Macro veto** — blackout window before ECB/CPI/NFP (`macro_calendar.yaml`)  
2b. **Earnings / dividend blackout** — per ticker (`earnings_calendar.yaml` + `EARNINGS_BLACKOUT_DAYS`)  
2c. **Max satellite positions** — `MAX_POSITIONS_TOTAL`  
2d. **Min liquidity** — average daily € volume ≥ `MIN_LIQUIDITY_ADV`  
3. Sector weight cap  
4. Pearson correlation vs holdings (`CORRELATION_LOOKBACK_DAYS`)  
5. **Sizing** — Half-Kelly × score × inverse-vol parity → whole shares, clamped by cash + satellite room  

Approved reasons now embed the sizing breakdown (Kelly, vol, weight % equity)
so Discord and the dashboard stay auditable.

### 4. Exits (split on purpose)

| Job | Cadence | Rule |
|-----|---------|------|
| **ATR stop** | Weekdays 08:35 (`--atr-stops`) | Losing satellite & `price < avg_entry − REBALANCE_ATR_STOP_MULT × ATR14` → SELL 100% |
| **Profit-shave** | 1st of month (`--rebalance`) | Unrealized &gt; +20% → SELL 20% of shares |

Core ETF is never shaved or stopped by these jobs (accumulation vehicle).

**ATR absolute vs %:** the stop uses **absolute** ATR (correct per name — ATR
already scales with price). `ATR% = ATR/price` is logged for cross-name
comparisons; use % for vol-style dashboards, absolute for the stop distance.

### 5. AI as post-hoc analyst only

- Trade explainer (2–3 sentences)  
- News → forced integer −100…+100  
- Friday Historian → Discord webhook  

---

## Architecture

```
                       ┌──────────────────────────────────────┐
                       │            main_scheduler.py          │
                       │  Paris: 09:00 / 13:30 / 17:10         │
                       │  + ATR 08:35 · shave 1st · Fri 18:00  │
                       └───────────────┬──────────────────────┘
   00_data_sensors        01/02              03_risk_portfolio        04_orchestrator_ai
 ┌───────────────┐   ┌──────────────┐   ┌───────────────────────┐   ┌────────────────────┐
 │ market_prices │──▶│ DuckDB OHLCV │──▶│ correlation_firewall  │──▶│ cascade + earnings  │
 │ macro_alpha   │   │ technical_   │   │ pea_position_sizer    │   │ revocation / LLM    │
 │ AMF→FMP→YF    │   │ scorer+DCA   │   │ ATR rebalancer        │   │ weekly historian    │
 └───────────────┘   │ equity_metrics│   └───────────────────────┘   └─────────┬──────────┘
                     └──────────────┘                                         ▼
   SQLite: portfolio · audit · equity curve              Discord + Streamlit (Mission Control)
   logs/ + database/pipeline_status.json
```

**One analysis pass:** fetch → VIX → raw signals → mark-to-market (+ equity
snapshot) → cascade → Smart-DCA → audit log → Discord alerts → pipeline heartbeat.

### Phase 38 add-on architecture (Monte Carlo + Stress + Red Team)

```
Portfolio Weights + DuckDB Returns
              │
              ▼
  02_quant_engine/stochastic_models.py
  - Cholesky(cov)
  - Correlated GBM paths
  - Fan chart percentiles (P05..P95)
              │
              ▼
  Streamlit Portefeuille Tab
  - On-demand Monte Carlo fan chart
  - Tail risk (VaR/CVaR)
  - Black swan stress table
```

```
Exploration Tab (selected ticker)
              │
              ▼
04_orchestrator_ai/red_team_agent.py
  Bull Agent  ─┐
               ├─ asyncio.gather ─► Judge Agent ─► 3-sentence verdict
  Bear Agent  ─┘
              │
              ▼
UI Boxes: st.info (Bull), st.warning (Bear), st.error (Judge)
```

---

## Logging & observability

Designed for a **personal** PEA terminal: enough detail to copy into notes or
debug a silent day, without enterprise noise.

| Piece | Role |
|-------|------|
| `01_memory_core/logging_setup.py` | Console (compact INFO) + rotating **DEBUG** files |
| `logs/<component>.log` | Per-component trails (`scheduler`, `dashboard`, `cascade`, …) |
| `logs/pea_pollux_all.log` | Fan-in of everything |
| `database/pipeline_status.json` | Last pass health for Mission Control (green / amber / red) |
| Dashboard → **Architecture & Logs** | Pick a file, tail N lines, select/copy |

Format in files: `timestamp | LEVEL | logger | file:line function | message`.

Entry points call `setup_app_logging()` once (scheduler already does). `logs/`
is git-ignored.

---

## Module reference

| Path | Responsibility |
|------|----------------|
| `00_data_sensors/market_prices_api.py` | Batch OHLCV download → DuckDB |
| `00_data_sensors/macro_alpha_api.py` | VIX, Put/Call, insiders (**AMF→FMP→YF**), Polymarket |
| `00_data_sensors/scrapers/amf_scraper.py` | AMF Opendatasoft v2.1 + BDIF `/back` (`RechercheTexte`) → legacy BDIF + 12h circuit |
| `01_memory_core/env_loader.py` | Native `api_keys.env` parser (no python-dotenv) |
| `01_memory_core/data_models.py` | Pydantic contracts (`Signal`, `Position`, `PortfolioState`) |
| `01_memory_core/sqlite_portfolio.py` | Account, positions, audit, **`portfolio_history`**, **`news_history`** |
| `01_memory_core/duckdb_manager.py` | OHLCV store (ATR / correlation / indicators) |
| `01_memory_core/logging_setup.py` | Rotating logs + pipeline heartbeat |
| `02_quant_engine/technical_scorer.py` | MRE signals; `RSI_OVERSOLD_THRESHOLD` from YAML |
| `02_quant_engine/smart_dca_engine.py` | Regime-aware Core DCA |
| `03_risk_portfolio/pea_position_sizer.py` | Half-Kelly × vol parity; **`size_with_explanation`** for UI |
| `03_risk_portfolio/correlation_firewall.py` | Sector / Pearson / VIX panic |
| `03_risk_portfolio/monthly_rebalancer.py` | Modes `atr` (daily) vs `shave` (monthly) |
| `03_risk_portfolio/equity_metrics.py` | Shared DD / CAGR / Sharpe / Sortino |
| `04_orchestrator_ai/signal_priority_cascade.py` | Conductor (all vetoes + sizing) |
| `04_orchestrator_ai/earnings_blackout.py` | Per-ticker corporate blackout |
| `04_orchestrator_ai/macro_veto.py` | Macro calendar blackout |
| `04_orchestrator_ai/revocation_engine.py` | Expire / revoke stale PENDING |
| `04_orchestrator_ai/weekly_historian.py` | Friday CIO digest + rejection taxonomy |
| `05_interfaces/terminal_dashboard.py` | Mission Control + tabs |
| `05_interfaces/trade_cards.py` | HTML cards: Tier, Kelly, ATR risk €, sector impact |
| `05_interfaces/discord_copilot.py` | Alerts + approve/revoke buttons |
| `main_scheduler.py` | Daemon + CLI (`--now`, `--weekly`, `--atr-stops`, `--rebalance`) |
| `seed_account.py` | Seed / reset PEA cash & positions |
| `tools/build_llm_dump.py` | Regenerate `PROJECT_FULL_DUMP_FOR_LLM.md` |
| `tools/sync_universe_from_bourso.py` | Refresh PEA universe YAML |
| `00_data_sensors/newsletter_api.py` | IMAP headlines + LLM morning Zeitgeist |
| `00_data_sensors/newsletter_ingest/` | Modules IMAP (whitelist, dedupe, parse) |
| `00_data_sensors/fundamentals_api.py` | Finnhub + yfinance (Value/Quality) |
| `02_quant_engine/quantitative_math.py` | VaR, CVaR, Z-Score, variance (NumPy pur) |
| `02_quant_engine/stochastic_models.py` | Monte Carlo corrélé (Cholesky + GBM) |
| `03_risk_portfolio/stress_tester.py` | Stress tests historiques (2008/2020/2022) |
| `04_orchestrator_ai/red_team_agent.py` | Débat Bull/Bear/Judge (OpenRouter) |
| `02_quant_engine/walk_forward_backtester.py` | Walk-forward equity scaffold on DuckDB |
| `tests/` | pytest foundations (sizing, equity metrics, cards, dedupe) |
| `.github/workflows/ci.yml` | CI on push/PR |

---

## APIs that work

| Source | Status | Notes |
|--------|--------|-------|
| **yfinance OHLCV** | Works | Primary market data → DuckDB |
| **`^V2TX` / `^VIX`** | Partial | VSTOXX often missing on Yahoo → falls back to US VIX as panic proxy |
| **AMF ODS / BDIF back** | Primary | Public, no paid key; ODS explore v2.1 + `/back/api/v1` with `RechercheTexte` |
| **AMF BDIF legacy** | Fragile | `/api/v1` often WAF/500 → 12h circuit → FMP → Yahoo |
| **FMP insider API** | Optional | Needs `FMP_API_KEY` |
| **yfinance insiders** | Tertiary | Sparse on many `.PA` mid-caps |
| **Options Put/Call** | Partial | Sparse for EU → neutral `1.0` |
| **Polymarket Gamma** | Live | Macro context + conviction modifier (never a trade trigger) |
| **OpenRouter** | Optional | Explanations / sentiment / weekly report / deep news |
| **Boursorama scraper** | Fragile | PEA profile, consensus, news (dates normalized to ISO) |
| **Native ticker tape** | Works | HTML/CSS marquee — blue chips + Clearbit logos (no TradingView tape) |
| **Yahoo Mail IMAP** | Optional | Morning Briefing Zeitgeist (`YAHOO_MAIL_USER`) |

Graceful degradation: missing sources return **neutral** values; the daemon does not crash.

---

## Installation

> Streamlit depends on `pyarrow` → use **Python 3.11 or 3.12 x64** (`venv_x64`).

```bash
git clone https://github.com/Polluxgnr/Peatrading.git pea_pollux
cd pea_pollux

python3.11 -m venv venv_x64
# Windows:  venv_x64\Scripts\Activate.ps1
# Unix:     source venv_x64/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

cp config/api_keys.env.example config/api_keys.env
# fill Discord / OpenRouter / FMP as needed

python seed_account.py --cash 10000
python main_scheduler.py --now    # first fetch + equity snapshot
.\run_dashboard.ps1
```

---

## Configuration

### `config/api_keys.env` (git-ignored)

| Variable | Required | Purpose |
|----------|----------|---------|
| `DISCORD_TOKEN` / `DISCORD_CHANNEL_ID` | bot | Copilot with buttons |
| `DISCORD_WEBHOOK_URL` | daemon | Weekly + monthly / ATR notifications |
| `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` | optional | LLM explain / sentiment |
| `FMP_API_KEY` | optional | Secondary insider source after AMF |
| `AMF_API_KEY` | placeholder | Public ODS/BDIF — use `free_public_ods_api` |
| `YAHOO_MAIL_USER` / `YAHOO_MAIL_APP_PASSWORD` | briefing | Morning Briefing IMAP |
| `EODHD_API_KEY` | optional | Reserved for paid EU market data |
| `ALPHAVANTAGE_API_KEY` | optional | Fundamentals fallback (Finnhub → Alpha Vantage → yfinance) |
| `OPENFIGI_API_KEY` | optional | Bloomberg OpenFIGI symbol/ISIN mapper (cached in SQLite) |

Keys are loaded by a **native** parser (`01_memory_core/env_loader.py`) —
**no `python-dotenv` required**. `main_scheduler.py` and the Streamlit dashboard
force-load `config/api_keys.env` at boot.

### `config/risk_params.yaml` (the rulebook)

| Group | Keys (intent) |
|-------|----------------|
| **Sizing** | `KELLY_FRACTION`, `MAX_SINGLE_POSITION_PCT`, `MAX_SECTOR_WEIGHT_PCT`, `MAX_ALLOCATION_PER_DAY_PCT` |
| **Circuit breakers** | `DAILY/WEEKLY/MONTHLY_MAX_LOSS_PCT` |
| **Correlation** | `MAX_CORRELATION_*`, **`CORRELATION_LOOKBACK_DAYS`** |
| **Signals** | `SIGNAL_*`, `MACRO_VETO_DAYS_BEFORE`, **`RSI_OVERSOLD_THRESHOLD`** |
| **Cascade guards** | **`EARNINGS_BLACKOUT_DAYS`**, **`MIN_LIQUIDITY_ADV`**, **`MAX_POSITIONS_TOTAL`** |
| **Core/Satellite** | `CORE_TICKER`, `CORE_*_PCT`, `SATELLITE_MAX_BUDGET_PCT` |
| **VIX** | `VIX_PANIC_THRESHOLD`, vol parity refs |
| **Rebalance** | `REBALANCE_PROFIT_*`, **`REBALANCE_ATR_STOP_MULT`** (default 2.5) |

### Calendars

- `config/macro_calendar.yaml` — ECB / CPI / NFP style events (manual; later API sync)  
- `config/earnings_calendar.yaml` — per-ticker earnings/div dates (starts empty)  
- `config/pea_universe.yaml` — ~600 PEA-eligible names by sector  

---

## Usage

```bash
python seed_account.py --cash 10000
python seed_account.py --position MC.PA:3:620:Luxury
python seed_account.py --show

python main_scheduler.py --now          # full analysis pass
python main_scheduler.py --weekly       # CIO digest now
python main_scheduler.py --briefing     # newsletter Zeitgeist now
python main_scheduler.py --atr-stops    # daily ATR evaluation now
python main_scheduler.py --rebalance    # monthly profit-shave now
python main_scheduler.py                # daemon (Paris schedule)

python 02_quant_engine/walk_forward_backtester.py --start 2020-01-01
python run_discord.py
.\run_dashboard.ps1

python -m pytest -q
python tools/build_llm_dump.py          # refresh LLM one-shot dump
```

---

## Dashboard

Launch: `.\run_dashboard.ps1` → http://localhost:8501

On first open each session, the dashboard **auto-syncs** market data
(`load_universe`, `get_last_prices`, `get_vix`) behind a global spinner.

### Native ticker tape (top of page)

Replaces the old TradingView widget (which showed red errors on `.PA` small caps).
A **CSS marquee** scrolls blue-chip performances with **Clearbit logos** and a
period selector: **1j / 5j / 1m**. Data from `get_market_performance` — no
external widget dependency.

### Mission Control (above tabs)

Designed so you read **market state in ~3 seconds** before diving into tabs:

- Euronext Paris open/closed + local time  
- Last pipeline pass status (from `pipeline_status.json`)  
- Equity + day variation (from `portfolio_history`)  
- VIX gauge, count of PENDING Discord signals  
- Quick actions: clickable ranking/universe rows (jumps Exploration dossier), ledger hint, manual pass reminder  

**Palette:** off-white `#E0E0E0` for body text; neon `#00FF00` reserved for
**positive PnL / APPROVED**; amber for alerts/vetoes; red for losses. Closer to
real Bloomberg conventions and easier on long sessions than green-everywhere.

### Tabs

| Tab | Content |
|-----|---------|
| **General & Signaux** | Morning Briefing (chargement patient), suggestion + ranking/pépites **cliquables**, geo brief, funnel |
| **Portefeuille** | Equity curve, sunburst, **stops ATR 2.5x**, wallet editor → SQLite |
| **Exploration** | **Recherche univers 600+** (selectbox haut de page), dossier ticker, **ticket d'ordre PEA**, **checklist décision**, news archivées SQLite, synthèse IA 24h |
| **Univers** | Liste PEA + tags techniques **cliquables** (full filtered view) |
| **Architecture & Logs** | **Télémétrie live** (health check env + DB), **`risk_params.yaml` actifs**, expanders logique quant, logs (5000 lignes) |

### News memory (`news_history`)

Headlines are **upserted into SQLite** on each fetch (`PortfolioDB.save_news`).
The UI reads `get_news_history(ticker)` first; live APIs run only if fewer than
3 cached articles. Boursorama relative dates (`il y a 2h`, empty, `Recent`) are
normalized to `YYYY-MM-DD HH:MM` at scrape time.

### Rich trade cards (what you see before approving)

For each PENDING BUY the card shows:

1. **Conviction score** (colour: amber 65–75 / neon 76–100) + Tier label  
2. **Sizing rationale** — Kelly fraction, measured vol + vol factor, ticket €, weight % of equity  
3. **R-style risk** — max € / % equity loss if the **2.5×ATR** stop is hit  
4. **Sector impact** — e.g. Luxury 18% → 23% (cap 25%), not just pass/fail  
5. **Streamlit Approuver / Rejeter** — updates SQLite instantly (complements Discord)  

---

## LLM full dump

For one-shot context in another LLM / agent:

```bash
python tools/build_llm_dump.py
# optional: skip architecture preamble
python tools/build_llm_dump.py --no-summary
```

Writes **`PROJECT_FULL_DUMP_FOR_LLM.md`** with:

- **Architecture snapshot** — layer map, Phase 26–28 highlights, hard rules
- **Priority file list** — README, risk YAML, scorer, dashboard, scheduler
- **Grouped file index** — by directory, with line counts and ⭐ on key files
- **Full source bodies** — fenced code blocks for every included file

Excludes: `venv*`, `database/*.db`, secrets, nested dump, agent transcripts.
Regenerate after meaningful code or README changes so external agents stay in sync.

---

## Deployment

### Docker (recommended)

`docker-compose.yml` is production-oriented for a single personal instance:

- **persistent volumes**:
  - `./database:/app/database` (SQLite + DuckDB + heartbeat JSON)
  - `./logs:/app/logs` (component logs + `pea_pollux_all.log`)
  - `./config:/app/config` (risk params, calendars, universe, env template)
- **timezone pinned**:
  - `TZ=Europe/Paris` in both `daemon` and `dashboard`
  - scheduler itself also uses explicit `schedule.every().day.at(..., "Europe/Paris")`
    in `main_scheduler.py`

```bash
cp config/api_keys.env.example config/api_keys.env
# Fill secrets locally (never commit config/api_keys.env)

docker compose config            # final compose validation
docker compose up -d --build
docker compose ps
docker compose logs -f daemon
docker compose logs -f dashboard
```

First-time bootstrap (inside daemon container):

```bash
docker compose exec daemon python seed_account.py --cash 10000
docker compose exec daemon python main_scheduler.py --now
```

Dashboard is exposed on `:8501`.

### Pre-deploy final checks

Run these before each push/deploy:

```bash
python -m pytest -q
python tools/build_llm_dump.py
git status --short
```

Expected outcomes:

- pytest green (current baseline: `10 passed`)
- `PROJECT_FULL_DUMP_FOR_LLM.md` regenerated and in sync with README/code
- no secret files staged (`config/api_keys.env` must stay untracked/ignored)

### Test coverage snapshot

Current automated tests are focused and fast:

- `tests/test_phase16_foundations.py`
  - equity metrics (`max_drawdown`, `sharpe`, summary metrics)
  - rebalancer mode split (`shave` vs `atr`) without network dependencies
  - earnings blackout logic from YAML windows
- `tests/test_ui_and_sandbox.py`
  - sizing explanation metadata contract (`size_with_explanation`)
  - trade-card helper rendering logic (tier/risk/sector-impact text)
  - newsletter dedupe for near-duplicate titles
- `tests/test_newsletter_whitelist.py`
  - sender extraction + whitelist allow/deny behavior
- `tests/test_funnel_analytics.py`
  - rejection taxonomy mapping for funnel analytics consistency

Alternatives: systemd (`Restart=always` on `main_scheduler.py`) or cron for
`--now` / `--weekly` / `--atr-stops` / `--rebalance`.

---

## Scheduling

| Job | When (Europe/Paris) | Action |
|-----|---------------------|--------|
| **Morning briefing** | **08:25** | Newsletter IMAP → LLM Zeitgeist → `morning_briefing.json` |
| ATR stops | 08:35 weekdays | Dynamic ATR SELLs → webhook |
| Profit-shave | Probe 08:30 (acts on the **1st**) | +20% trim → webhook |
| Analysis | 09:00, 13:30, 17:10 weekdays | Full pipeline → Discord + heartbeat |
| Weekly report | Friday 18:00 | Historian → webhook |

Weekends: analysis / ATR skipped automatically.

---

## Roadmap / future improvements

Prioritized for a **validated personal PEA process**, not feature theatre.
Broker import must **diff** vs SQLite (never blind overwrite). Prefer official/API
sources over furtive HTML scraping.

### Done (Phase 15–20)

| Item | Notes |
|------|-------|
| AMF→FMP→Yahoo insider cascade | Official FR source first |
| Equity curve + shared metrics | Live dashboard; ready for backtest reuse |
| Daily ATR vs monthly shave | Split jobs / CLI flags |
| Earnings blackout engine | Calendar empty — fill via API later |
| ADV / max positions / RSI / corr lookback | Wired in `risk_params.yaml` + cascade |
| Mission Control + trade cards + logs | Operator UX |
| **Decision funnel waterfall + rejection pie** | ✅ Phase 17 — 7J/30J audit-log analytics in General |
| **Valuation + 10y annual returns** | ✅ Phase 18 — Exploration (buy zone, P/E, P/B, **1M/1Y**, annual bars) |
| **Newsletter whitelist + Zeitgeist** | ✅ Phase 19 — `NewsletterSensor` + 08:25 job + dashboard |
| **Ensemble conviction scoring** | ✅ Phase 20 — 4 axes, emit ≥65; radar + Command Center approve |
| **What-if 1000€ + walk-forward scaffold** | ✅ Exploration simulator + `walk_forward_backtester.py` |
| **Terminal polish (TV / zone / ranking / Polymarket)** | ✅ Phase 21 — EPA: ticker map, flat buy-zone fix, fingerprint ranking, SSL-tolerant Gamma |
| **Smart UX + deep news + logos + pépites** | ✅ Phase 22 |
| **UX overhaul (tape / GO / news diversity)** | ✅ Phase 23 — no GO, logos off, multi-source news, deep IA narrative |
| **Polymarket harden + news clean + tape + logs** | ✅ Phase 24 — JSONDecode guard, no heuristic pills, blue-chip tape, briefing button, log tail 5k |
| **Auto-sync + score holistique + UI exécution** | ✅ Phase 26 — warmup au démarrage, News/Polymarket dans le score, stops ATR visibles, tickets/checklist, tables cliquables |
| **Bandeau natif + news SQLite + exploration universelle** | ✅ Phase 27 — marquee HTML/CSS, `news_history`, dates exactes, selectbox 600+ tickers |
| **Télémétrie live Architecture & Logs** | ✅ Phase 28 — health check sources, risk_params actifs, expanders logique quant |
| **UX rename + clickable tape + news history** | ✅ Phase 29 — Pollux branding, query-param tape, Synthèse IA, full news DB |
| **Native env + AMF ODS + TV EURONEXT + uncapped lists** | ✅ Phase 32 |
| **Multi-factor + Finnhub + Data Lake analyste** | ✅ Phase 35–36 |
| **VaR/CVaR + Z-Score académique** | ✅ Phase 37 |
| **Monte Carlo + stress tests + red teaming IA** | ✅ Phase 38 |
| **UX (tape fix, briefing async, near-miss radar, ML export)** | ✅ Phase 39 |
| **Cash sweep, Discord daily digest, 10y backfill, forward curve, ML store** | ✅ Phase 40 |
| **AMF semantic parsing (legal FR regex), fluid log viewer, system telemetry** | ✅ Phase 41 |
| **Institutional Overhaul: data quality (auto_adjust), parallel I/O, drawdown breaker, OpenFIGI, Alpha Vantage, backtester look-ahead fix, CI ruff** | ✅ Phase 42 |
| **Pydantic config validation, backtester exits, dashboard DuckDB dedup, XGBoost ML, Devil's Advocate PEA** | ✅ Phase 44 |
| **Dynamic Market Regime, EWMA Risk Math, Pipeline Idempotency** | ✅ Phase 45 |
| **ML Historical Bootstrapper, Gemini 2.5 Optimization** | ✅ Phase 46 |
| **Ultimate Performance (SQLite I/O fix), Pure Webhooks for Discord** | ✅ Phase 47 |
| pytest + GitHub Actions CI | Expand coverage over time |

### Next (highest leverage)

| Item | Why |
|------|-----|
| Richer walk-forward (full Orchestrator + costs) | Validate RSI / conviction weights on PEA universe |
| Fundsmith/Amundi holdings scraper | Replace institutional proxy set |
| Broker CSV diff import | Keep SQLite honest vs reality |
| Fill **earnings_calendar** (Euronext / API) | Blackout already coded |
| Relative strength / 52w / analyst drift | Post-backtester calibration knobs |

### Phase 40: Predictive Machine Learning (XGBoost / NLP)

Goal: train a classifier on the `news_history` and `audit_log` SQLite tables to
predict the probability that a given signal will result in a profitable trade.

- **Features:** headline sentiment embeddings (NLP), RSI/MACD/Bollinger at signal
  time, VIX level, sector, day-of-week, insider cluster flag.
- **Labels:** from `audit_log` — did the signal reach +5% within 20 trading days
  after APPROVED/EXECUTED?
- **Model:** XGBoost gradient-boosted trees (tabular) + optional sentence-transformer
  embeddings for headline text.
- **Integration:** predicted probability displayed alongside the existing conviction
  score in the dashboard (e.g., "ML confidence: 72%").
- **Data export:** available today in the Architecture tab (CSV download of both tables).

### Later

Paid VSTOXX · AMF resilience · multi-core ETF rotation · trailing ATR after shave ·
EUR/USD note in CIO digest · rolling Sharpe chart.

**Non-goals:** auto-broker execution, leverage, LLM-as-trader, US pennies.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Dashboard « En attente… » | `python seed_account.py --cash 10000` then `--now` |
| Empty equity curve | Needs at least one `update_portfolio` (pass or wallet save) |
| Mission Control pass = « jamais » | Run `python main_scheduler.py --now` once |
| Empty `logs/` | Same — scheduler/dashboard create files on first run |
| `pyarrow` / Streamlit fail | Python **3.11/3.12 x64** |
| VIX stuck / `^V2TX` 404 | Falls back to `^VIX` |
| AMF HTTP 500 | Expected; FMP then Yahoo; circuit ~12h |
| No FMP insiders | Set `FMP_API_KEY` |
| ATR stop never fires | Need DuckDB history + losing position; try `--atr-stops` |
| Cards show ATR risk n/a | Fetch history with `--now` first |
| LLM / weekly silent | `OPENROUTER_API_KEY` / `DISCORD_WEBHOOK_URL` |
| Cash too small for CW8 | MICRO mode: 1 liquid share + cash runway (by design) |
| Newsletter IMAP auth fail | Use Yahoo **app password**, folder name exact, SSL 993 |
| News dates show `Recent` | Re-open ticker in Exploration — scraper now stamps ISO; archive in `news_history` |
| Red TradingView tape errors | Fixed in Phase 27 — native HTML marquee replaces TV widget |
| Briefing flashes error on boot | Phase 27 — patient `st.info` + manual generate button |
| CI / pytest | `python -m pytest -q` |

---

## Disclaimer

Decision-support and educational tool only. **No automated execution. No financial
advice.** You are solely responsible for every trade. Past or backtested results
do not guarantee future performance.

© 2026 Pollux Gronier — PEA Pollux.

---

## English guide

**PEA Pollux** is a personal quantitative research terminal for a French **PEA**
( tax-advantaged equity savings plan ). It helps you *research*, *size*, and *risk-manage*
ideas — but **never places broker orders**.

### What it does

1. **Ingests data** — OHLCV (yfinance → DuckDB), insiders (AMF → FMP → Yahoo),
   news, newsletters, macro proxies (VIX, Polymarket).
2. **Scores opportunities** — multi-model ensemble (Trend, Mean-Reversion, Breakout,
   Context) with fundamentals (Finnhub) and sentiment modifiers.
3. **Filters through risk** — VIX panic, sector caps, correlation firewall, earnings
   blackout, liquidity floor, vol-parity sizing (whole shares only).
4. **Surfaces decisions** — Streamlit dashboard + optional Discord copilot for
   manual approve/reject.
5. **Explains & challenges** — LLM rationales, weekly digest, Bull/Bear red teaming.

### Phase 38–41 highlights

| Feature | Module | Phase |
|---------|--------|:-----:|
| Monte Carlo fan chart | `stochastic_models.py` | 38 |
| Black swan replay | `stress_tester.py` | 38 |
| Tail risk VaR & CVaR | `quantitative_math.py` | 37 |
| AI red team (Bull/Bear/Judge) | `red_team_agent.py` | 38 |
| Ticker tape 1d fix + near-miss radar | `terminal_dashboard.py` | 39 |
| ML feature store + CSV export | `ml_feature_store.py` | 40 |
| Cash sweep (zero idle cash) | `smart_dca_engine.py` | 40 |
| Discord daily concise report | `discord_copilot.py` | 40 |
| 10-year OHLCV backfill (`--backfill-10y`) | `main_scheduler.py` | 40 |
| Forward equity curve vs CW8 benchmark | `terminal_dashboard.py` | 40 |
| AMF semantic parsing (FR legal regex) | `amf_scraper.py` | 41 |
| Filtered + color-coded log viewer | `terminal_dashboard.py` | 41 |
| System telemetry (DB sizes, CPU, mem) | `terminal_dashboard.py` | 41 |

### Architecture (high level)

```
Data sensors → DuckDB/SQLite → Quant engine → Risk cascade → UI (Streamlit/Discord)
                                      ↓
                         Stochastic models + stress tests + LLM explainers
```

### Quick start

```bash
git clone https://github.com/Polluxgnr/Peatrading.git pea_pollux
cd pea_pollux
python3.11 -m venv venv_x64 && source venv_x64/bin/activate  # or Windows Activate.ps1
pip install -r requirements.txt
cp config/api_keys.env.example config/api_keys.env
python seed_account.py --cash 10000
python main_scheduler.py --now
streamlit run 05_interfaces/terminal_dashboard.py
```

### Design principles

- **Math-first, AI-second** — models decide eligibility; LLMs only explain or debate.
- **Manual execution** — you always confirm trades.
- **Graceful degradation** — missing API keys → neutral fallbacks, no crashes.
- **Vectorized math** — NumPy/Pandas for VaR, Monte Carlo, Z-Scores.
- **On-demand heavy compute** — Monte Carlo runs behind a button + cache, not on every page load.

---
## Phase 49 : The Apex Optimization (Current)

*   **⚡ Blazing Fast UI :** Caching systématique (@st.cache_resource, @st.cache_data) et lazy loading pour un Dashboard sub-seconde.
*   **📊 Interactive Metrics :** Drill-down des métriques via st.popover (Market Breadth dynamique, Mini-charts VIX).
*   **🤖 Déploiement Stratégique (80% Rule) :** Force le déploiement de capital sur les meilleurs signaux techniques rejetés si l'exposition Cash est > 20% en régime Bull.
*   **📈 Simulateur ML Autonome :** Backtester visuel intégré dans le Dashboard permettant d'évaluer la stratégie Machine Learning vis-à-vis d'un Buy & Hold (CW8) avec prise en compte du slippage.
*   **📡 Ingestion Incrémentale :** Optimisation drastique des appels réseaux (DuckDB get_latest_dates) pour ne télécharger que le strict nécessaire depuis yfinance.
*   **🚨 Copilot Discord V2 :** Refonte des webhooks avec intégration du Notional estimé, formatage premium et ping @everyone.
