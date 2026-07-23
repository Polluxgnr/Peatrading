# PEA Sniper Terminal — V-Prime 3.0 (Phase 17)

> **Sovereign execution. Kinetic risk management. Absolute quantitative transparency.**

Zero-leverage quantitative **decision support** for a personal French **PEA**
(Plan d'Épargne en Actions). The stack ingests market data, runs a deterministic
quant engine, filters every idea through a multi-layer risk cascade, then surfaces
highly curated proposals to a **Discord Copilot** for **manual** execution. A
Bloomberg-inspired **Streamlit** terminal is the day-to-day command center
(Mission Control, equity curve, rich trade cards, log viewer).

**The system never sends orders to a broker.** Maths decides *what* is worth
considering; AI only *explains* (rationale, news score, weekly CIO digest).
**This is not investment advice.**

Repo: [github.com/Polluxgnr/Peatrading](https://github.com/Polluxgnr/Peatrading)

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
12. [Experiments / sandboxes](#-experiments--sandboxes)
13. [LLM full dump](#-llm-full-dump)
14. [Deployment](#-deployment)
15. [Scheduling](#-scheduling)
16. [Roadmap](#-roadmap--future-improvements)
17. [Troubleshooting](#-troubleshooting)
18. [Disclaimer](#-disclaimer)

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
   audit log, **daily equity curve** (`portfolio_history`).
5. **Zero crash tolerance.** A failed pass logs `CRITICAL` and writes a red
   pipeline heartbeat; the daemon keeps running for the next slot.
6. **Manual execution.** You always have the last word (Discord approve / revoke).
7. **Personal portfolio demo, not a SaaS fleet.** Observability is detailed and
   copy-friendly, but deliberately human-scale (rotating local logs, Mission Control).

---

## Feature map

| Layer | What it does (why it exists) |
|------|------------------------------|
| **Data** | OHLCV → DuckDB; VIX/VSTOXX; Put/Call; insiders **AMF→FMP→Yahoo**; Polymarket Gamma; Bourso profile/news (best-effort) |
| **Quant** | Mean-reversion exhaustion: RSI below threshold + Close&gt;SMA200 + Close&gt;SMA5 + EPS&gt;0 |
| **Core/Satellite** | Smart DCA on `CW8.PA` (more aggressive under SMA200); satellites capped ~30% equity |
| **Risk cascade** | VIX panic, macro veto, **earnings blackout**, max satellite lines, **ADV € floor**, sector, correlation, vol-parity sizing |
| **Exits** | **Daily** ATR stop (`price < entry − 2.5×ATR14`); **monthly** +20% profit-shave |
| **Memory** | SQLite equity curve + shared `equity_metrics` (max DD, CAGR, Sharpe, Sortino) — same maths for a future backtester |
| **AI (explain only)** | Trade rationale, news sentiment, weekly digest, geo brief |
| **UI** | Mission Control + Discord + Streamlit (**decision funnel waterfall**, trade cards, equity curve, Logs) |
| **Ops** | Paris daemon, seed CLI, wallet editor, RevocationEngine, rotating logs, CI pytest |

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

### 2. Satellite signal (Mean-Reversion Exhaustion)

A raw BUY fires only when **all** of these hold (`technical_scorer.py`):

| Filter | Rule | Intent |
|--------|------|--------|
| Trend | `Close > SMA200` | Only pullbacks inside an uptrend |
| Exhaustion | `RSI(14) < RSI_OVERSOLD_THRESHOLD` (default 30) | Oversold stretch |
| Momentum | `Close > SMA5` | Avoid catching falling knives |
| Quality | trailing `EPS > 0` | Skip loss-making hype |

The continuous score (0–100) maps how deep the RSI is; the dashboard shows a
**Tier A / B / C** label so you can rank conviction without treating the score
as a black box (Tier A ≥ 90, Tier B ≥ 75).

### 3. Risk cascade (order matters — cheap checks first)

Implemented in `signal_priority_cascade.py`:

0. Live price exists  
1. **VIX panic** — if V2TX/VIX &gt; `VIX_PANIC_THRESHOLD`, freeze **new satellite buys** (Core DCA still runs)  
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

---

## Logging & observability

Designed for a **personal** PEA terminal: enough detail to copy into notes or
debug a silent day, without enterprise noise.

| Piece | Role |
|-------|------|
| `01_memory_core/logging_setup.py` | Console (compact INFO) + rotating **DEBUG** files |
| `logs/<component>.log` | Per-component trails (`scheduler`, `dashboard`, `cascade`, …) |
| `logs/pea_sniper_all.log` | Fan-in of everything |
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
| `00_data_sensors/scrapers/amf_scraper.py` | Official AMF BDIF + 12h circuit breaker |
| `01_memory_core/data_models.py` | Pydantic contracts (`Signal`, `Position`, `PortfolioState`) |
| `01_memory_core/sqlite_portfolio.py` | Account, positions, audit, **`portfolio_history`** |
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
| `experiments/newsletter_ingest/` | Yahoo Mail IMAP sandbox → local JSON only |
| `tests/` | pytest foundations (sizing, equity metrics, cards, dedupe) |
| `.github/workflows/ci.yml` | CI on push/PR |

---

## APIs that work

| Source | Status | Notes |
|--------|--------|-------|
| **yfinance OHLCV** | Works | Primary market data → DuckDB |
| **`^V2TX` / `^VIX`** | Partial | VSTOXX often missing on Yahoo → falls back to US VIX as panic proxy |
| **AMF BDIF** | Fragile | Official FR insiders; HTTP 500/WAF common → 12h circuit → FMP → Yahoo |
| **FMP insider API** | Optional | Needs `FMP_API_KEY` |
| **yfinance insiders** | Tertiary | Sparse on many `.PA` mid-caps |
| **Options Put/Call** | Partial | Sparse for EU → neutral `1.0` |
| **Polymarket Gamma** | Live | Macro context only (never a trade trigger) |
| **OpenRouter** | Optional | Explanations / sentiment / weekly report |
| **TradingView / Yahoo news** | Works | UI embeds + radar |
| **Yahoo Mail IMAP** | Sandbox | App password; read-only newsletter ingest (experiments only) |

Graceful degradation: missing sources return **neutral** values; the daemon does not crash.

---

## Installation

> Streamlit depends on `pyarrow` → use **Python 3.11 or 3.12 x64** (`venv_x64`).

```bash
git clone https://github.com/Polluxgnr/Peatrading.git pea_sniper_terminal
cd pea_sniper_terminal

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
| `EODHD_API_KEY` | optional | Reserved for paid EU market data |

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
python main_scheduler.py --atr-stops    # daily ATR evaluation now
python main_scheduler.py --rebalance    # monthly profit-shave now
python main_scheduler.py                # daemon (Paris schedule)

python run_discord.py
.\run_dashboard.ps1

python -m pytest -q
python tools/build_llm_dump.py          # refresh LLM one-shot dump
```

---

## Dashboard

Launch: `.\run_dashboard.ps1` → http://localhost:8501

### Mission Control (above tabs)

Designed so you read **market state in ~3 seconds** before diving into tabs:

- Euronext Paris open/closed + local time  
- Last pipeline pass status (from `pipeline_status.json`)  
- Equity + day variation (from `portfolio_history`)  
- VIX gauge, count of PENDING Discord signals  
- Quick actions: **`TICKER` + GO** (jumps Exploration dossier), ledger hint, manual pass reminder  

**Palette:** off-white `#E0E0E0` for body text; neon `#00FF00` reserved for
**positive PnL / APPROVED**; amber for alerts/vetoes; red for losses. Closer to
real Bloomberg conventions and easier on long sessions than green-everywhere.

### Tabs

| Tab | Content |
|-----|---------|
| **General & Signaux** | Adaptive multi-horizon suggestion (MICRO→FULL), Core card, geo brief, **Entonnoir de décision (waterfall 7J/30J)**, **rich PENDING trade cards**, news, ledger |
| **Portefeuille** | Equity curve + **Sharpe/DD/CAGR/Sortino**, sunburst, positions, wallet editor → SQLite |
| **Exploration** | Liquid scan, full ticker dossier (business, TA explained, news, AMF→FMP→YF insiders, Polymarket) |
| **Univers** | Full list + average sector performance |
| **Architecture & Logs** | Living docs + **log file picker / tail / copy** |

### Rich trade cards (what you see before approving on Discord)

For each PENDING BUY the card shows:

1. **Tier A/B/C** + score  
2. **Sizing rationale** — Kelly fraction, measured vol + vol factor, ticket €, weight % of equity  
3. **R-style risk** — max € / % equity loss if the **2.5×ATR** stop is hit  
4. **Sector impact** — e.g. Luxury 18% → 23% (cap 25%), not just pass/fail  

---

## Experiments / sandboxes

### `experiments/newsletter_ingest/` (Yahoo Mail → local JSON)

**Isolated** from `00_`–`05_` (no cross-imports, no SQLite/DuckDB writes).

1. Yahoo 2FA → generate an **app password** (not your main password)  
2. `cp experiments/newsletter_ingest/.env.example experiments/newsletter_ingest/.env`  
3. Create a Yahoo folder/label (e.g. `Finance`) and filter newsletters into it  
4. Run:

```bash
python experiments/newsletter_ingest/run_ingest.py --folder Finance --limit 20
python experiments/newsletter_ingest/run_ingest.py --dry-run --limit 5
```

Output: `experiments/newsletter_ingest/output/ingest_*.json`. IMAP is
**read-only** (no delete/move). After manual validation on real digests, headlines
can later feed `news_sentiment_llm.py` — that wiring is **out of scope** until you decide.

---

## LLM full dump

For one-shot context in another LLM / agent:

```bash
python tools/build_llm_dump.py
```

Writes **`PROJECT_FULL_DUMP_FOR_LLM.md`**: indexed concatenation of source,
configs, and docs (excludes venv, DBs, secrets, nested dump). Regenerate after
meaningful code or README changes so external agents stay in sync.

---

## Deployment

```bash
cp config/api_keys.env.example config/api_keys.env
docker compose up -d --build
# Dashboard :8501
docker compose logs -f daemon
docker compose exec daemon python seed_account.py --cash 10000
```

Alternatives: systemd (`Restart=always` on `main_scheduler.py`) or cron for
`--now` / `--weekly` / `--atr-stops` / `--rebalance`.

---

## Scheduling

| Job | When (Europe/Paris) | Action |
|-----|---------------------|--------|
| Analysis | 09:00, 13:30, 17:10 weekdays | Full pipeline → Discord + heartbeat |
| ATR stops | 08:35 weekdays | Dynamic ATR SELLs → webhook |
| Profit-shave | Probe 08:30 (acts on the **1st**) | +20% trim → webhook |
| Weekly report | Friday 18:00 | Historian → webhook |

Weekends: analysis / ATR skipped automatically.

---

## Roadmap / future improvements

Prioritized for a **validated personal PEA process**, not feature theatre.
Broker import must **diff** vs SQLite (never blind overwrite). Prefer official/API
sources over furtive HTML scraping.

### Done (Phase 15–16)

| Item | Notes |
|------|-------|
| AMF→FMP→Yahoo insider cascade | Official FR source first |
| Equity curve + shared metrics | Live dashboard; ready for backtest reuse |
| Daily ATR vs monthly shave | Split jobs / CLI flags |
| Earnings blackout engine | Calendar empty — fill via API later |
| ADV / max positions / RSI / corr lookback | Wired in `risk_params.yaml` + cascade |
| Mission Control + trade cards + logs | Operator UX |
| **Decision funnel waterfall + rejection pie** | ✅ Phase 17 — 7J/30J audit-log analytics in General |
| pytest + GitHub Actions CI | Expand coverage over time |
| Newsletter IMAP sandbox | Manual validation before any prod hook |

### Next (highest leverage)

| Item | Why |
|------|-----|
| **Walk-forward backtester** | Turns “system that runs” into “strategy with evidence”; reuse `equity_metrics` |
| **Broker CSV diff import** | Kill wallet drift without erasing manual fixes |
| Fill **earnings_calendar** (Euronext / API) | Blackout already coded |
| Signal **funnel waterfall** + rejection pie | ✅ Phase 17 — General tab (`get_funnel_metrics`, audit logs + `_classify`) |
| Relative strength / 52w / analyst drift | Post-backtester calibration knobs |

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
| CI / pytest | `python -m pytest -q` |

---

## Disclaimer

Decision-support and educational tool only. **No automated execution. No financial
advice.** You are solely responsible for every trade. Past or backtested results
do not guarantee future performance.

© 2026 Pollux Quantitative Research — V-Prime 3.0 (Phase 17).
