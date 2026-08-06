# PEA Pollux â€” Terminal quantitatif personnel



> **Un bureau d'analyse quantitatif pour votre PEA â€” transparent, manuel, sans exÃ©cution automatique.**



**PEA Pollux** est un terminal de recherche et de suivi de portefeuille conÃ§u pour un

**PEA personnel** (Plan d'Ã‰pargne en Actions). Il ingÃ¨re les donnÃ©es de marchÃ©,

calcule des signaux multi-facteurs, applique une cascade de risque stricte, puis

prÃ©sente des propositions **Ã  valider manuellement** dans le dashboard Streamlit

ou via Discord.



**Le systÃ¨me n'envoie jamais d'ordres Ã  un courtier.** Les modÃ¨les quantitatifs

dÃ©cident *ce qui mÃ©rite d'Ãªtre Ã©tudiÃ©* ; l'IA *explique* (rationale, sentiment,

briefing hebdo, red teaming Bull/Bear). **Ce n'est pas un conseil en investissement.**



Repo: [github.com/Polluxgnr/Peatrading](https://github.com/Polluxgnr/Peatrading)



---




## Nouveautés Récentes
- 🤖 **Interactive Discord Copilot**: Un véritable assistant bidirectionnel (`discord_copilot.py`) permettant d'approuver ou rejeter les signaux via des commandes Slash (`/approve`, `/reject`, `/status`, `/portfolio`), avec génération automatique d'un Ticket d'Ordre et calcul du Smart Limit Price.
- 🏆 **Live Alpha Analytics**: Suivi de performance institutionnelle en temps réel (Alpha de Jensen, Beta, Information Ratio) intégré nativement dans le tableau de bord pour se mesurer au MSCI World (`CW8.PA`) et au CAC 40 (`^FCHI`).
- 🧠 **Local LLM Sentiment & Deep News**: RAG local (Ollama) lisant l'intégralité des articles pour en extraire des KPIs et risques cachés, avec dégradation gracieuse sur les paywalls.
- 🛡️ **Risk & Stop-Loss Engine**: Intégration de l'Average True Range (ATR) pour calculer dynamiquement les stop-loss et un modèle d'optimisation de l'exécution (Smart Order Routing).
- 🔄 **Full-Orchestrator Walk-Forward**: Backtester complet appliquant les règles réelles (VIX, sizing, firewall) à l'historique complet pour des simulations "live-like".

## Pourquoi PEA Pollux ?



| Besoin | RÃ©ponse |

|--------|---------|

| Comprendre *pourquoi* un signal apparaÃ®t | Score multi-modÃ¨le + Data Lake transparent |

| GÃ©rer le risque avant d'acheter | Cascade VIX, corrÃ©lation, liquiditÃ©, earnings blackout |

| Suivre la performance | Courbe d'equity, VaR/CVaR, Monte Carlo corrÃ©lÃ© |

| Challenger une idÃ©e | Red teaming IA (Bull vs Bear + Judge) |

| DÃ©ployer proprement | Docker, healthchecks, logs rotatifs, CI pytest |



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



1. **No fractional shares.** PEA sizing always uses `math.floor` â€” one share or nothing.

2. **Math first, AI second.** LLMs never generate or approve trades. They only:

   explain an already-decided signal, compress news into an integer (âˆ’100â€¦+100),

   and write the Friday CIO digest.

3. **Official sources first.** Insider cascade is strict:

   **AMF BDIF â†’ FMP â†’ yfinance**. OHLCV stays on `yfinance` â†’ DuckDB. HTML

   scrapers are best-effort with circuit-breakers (AMF BDIF is often WAF-blocked).

4. **Split state.** DuckDB = heavy OHLCV; SQLite = portfolio, positions, immutable

   audit log, **daily equity curve** (`portfolio_history`), and **news archive**

   (`news_history` â€” cross-session headlines with real timestamps).

5. **Zero crash tolerance.** A failed pass logs `CRITICAL` and writes a red

   pipeline heartbeat; the daemon keeps running for the next slot.

6. **Manual execution.** You always have the last word (Discord **or** Streamlit

   Approuver / Rejeter â†’ SQLite).

7. **Personal portfolio demo, not a SaaS fleet.** Observability is detailed and

   copy-friendly, but deliberately human-scale (rotating local logs, Mission Control).



---



## Feature map



| Layer | What it does (why it exists) |

|------|------------------------------|

| **Data** | OHLCV â†’ DuckDB; VIX/VSTOXX; Put/Call; insiders **AMFâ†’FMPâ†’Yahoo**; Polymarket Gamma; Bourso + **Google News / Yahoo** news (archived in **`news_history`**); **newsletter IMAP** (whitelist) |

| **Quant** | **Ensemble conviction (0â€“100)**: MR â‰¤35 + Vol â‰¤25 + Insider â‰¤20 + Inst â‰¤20 + **News/Polymarket modifiers** â€” emit if â‰¥65 |

| **Core/Satellite** | Smart DCA on `CW8.PA` (more aggressive under SMA200); satellites capped ~30% equity |

| **Risk cascade** | VIX panic, **EPS &lt; 0**, macro veto, **earnings blackout**, max satellite lines, **ADV â‚¬ floor**, sector, correlation, vol-parity sizing |

| **Exits** | **Daily** ATR stop (`price < entry âˆ’ 2.5Ã—ATR14`); **monthly** +20% profit-shave |

| **Memory** | SQLite equity curve + **`news_history`** + shared `equity_metrics` + `morning_briefing.json` Zeitgeist |

| **AI (explain only)** | Trade rationale, news sentiment, weekly digest, geo brief, **morning newsletter Zeitgeist**, deep news synthesis (24h cache) |

| **UI** | Mission Control + **native HTML ticker tape** + Discord + Streamlit (**Command Center**, funnel, radar, what-if, **order ticket**, **decision checklist**, **live telemetry**) |

| **Ops** | Paris daemon (incl. **08:25 briefing**), session auto-sync on dashboard open, walk-forward scaffold, seed CLI, CI pytest |



---



## Strategy in depth



### 1. Core / Satellite allocation



Capital is split so the PEA stays diversified even when stock-picking is quiet:



- **Core (~70â€“75%)** â€” Amundi MSCI World PEA ETF (`CW8.PA`) via **Smart DCA**.

  When CW8 trades **below** its 200-day SMA (fear), the engine raises the target

  weight and buys a larger tranche; **above** the SMA it drips smaller amounts.

- **Satellite (â‰¤30%)** â€” individual EU names under `SATELLITE_MAX_BUDGET_PCT`.

  Also capped by `MAX_POSITIONS_TOTAL` so the 30% budget is not fragmented into

  too many tiny lines.



### 2. Empreinte Multi-StratÃ©gies (how signals are scored)



Every ticker receives a **score from 0 to 100** called the **Empreinte**

(fingerprint). It combines four weighted axes into a single conviction number.

A BUY signal is only emitted when **conviction â‰¥ 65**. Hard vetoes (VIX,

EPS < 0) are enforced later in the risk cascade â€” scoring runs first.



| Abbreviation | Axis | Weight | What it measures |

|:------------:|------|:------:|------------------|

| **MR** | Mean Reversion | 35 % | Statistical under-valuation: RSI-14 oversold + price above long-term SMA-200. A Z-Score < âˆ’2 on a 50-day window adds a bonus. |

| **Mom** | Momentum | 25 % | Trend strength: Close > SMA-5 > SMA-50 > SMA-200, MACD histogram positive and growing, close near upper Bollinger Band. |

| **Q/V** | Quality / Value | 20 % | Fundamentals from Finnhub or yfinance: low P/E (< 15 = high score), low P/B (< 2 = bonus), high ROE (> 15 %), low Debt/Equity. |

| **Ins** | Insider Confidence | 20 % | Directors buying their own stock: AMF/FMP/EODHD buy-cluster â‰¥ 2 = max, single buy = half. |



**Modifiers** applied on top of the base score:



| Modifier | Range | Source |

|----------|-------|--------|

| News sentiment | +10 / âˆ’15 | LLM integer score (âˆ’100â€¦+100) or heuristic keyword fallback |

| Polymarket macro | +10 / âˆ’10 | YES probability â‰¥ 0.62 â†’ bullish, â‰¤ 0.38 â†’ bearish (context only) |



The final score is clamped to 0â€“100 and displayed in the dashboard as a

**polar radar chart** (Exploration tab). Colour coding: **amber 65â€“75** /

**neon 76â€“100**.



### 3. Risk cascade (order matters â€” cheap checks first)



Implemented in `signal_priority_cascade.py`:



0. Live price exists  

1. **VIX panic** â€” if V2TX/VIX &gt; `VIX_PANIC_THRESHOLD`, freeze **new satellite buys** (Core DCA still runs)  

1b. **EPS &lt; 0** â€” quality veto (Orchestrator)  

2. **Macro veto** â€” blackout window before ECB/CPI/NFP (`macro_calendar.yaml`)  

2b. **Earnings / dividend blackout** â€” per ticker (`earnings_calendar.yaml` + `EARNINGS_BLACKOUT_DAYS`)  

2c. **Max satellite positions** â€” `MAX_POSITIONS_TOTAL`  

2d. **Min liquidity** â€” average daily â‚¬ volume â‰¥ `MIN_LIQUIDITY_ADV`  

3. Sector weight cap  

4. Pearson correlation vs holdings (`CORRELATION_LOOKBACK_DAYS`)  

5. **Sizing** â€” Half-Kelly Ã— score Ã— inverse-vol parity â†’ whole shares, clamped by cash + satellite room  



Approved reasons now embed the sizing breakdown (Kelly, vol, weight % equity)

so Discord and the dashboard stay auditable.



### 4. Exits (split on purpose)



| Job | Cadence | Rule |

|-----|---------|------|

| **ATR stop** | Weekdays 08:35 (`--atr-stops`) | Losing satellite & `price < avg_entry âˆ’ REBALANCE_ATR_STOP_MULT Ã— ATR14` â†’ SELL 100% |

| **Profit-shave** | 1st of month (`--rebalance`) | Unrealized &gt; +20% â†’ SELL 20% of shares |



Core ETF is never shaved or stopped by these jobs (accumulation vehicle).



**ATR absolute vs %:** the stop uses **absolute** ATR (correct per name â€” ATR

already scales with price). `ATR% = ATR/price` is logged for cross-name

comparisons; use % for vol-style dashboards, absolute for the stop distance.



### 5. AI as post-hoc analyst only



- Trade explainer (2â€“3 sentences)  

- News â†’ forced integer âˆ’100â€¦+100  

- Friday Historian â†’ Discord webhook  



---



## Architecture



```

                       â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”�

                       â”‚            main_scheduler.py          â”‚

                       â”‚  Paris: 09:00 / 13:30 / 17:10         â”‚

                       â”‚  + ATR 08:35 Â· shave 1st Â· Fri 18:00  â”‚

                       â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜

   00_data_sensors        01/02              03_risk_portfolio        04_orchestrator_ai

 â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”�   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”�   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”�   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”�

 â”‚ market_prices â”‚â”€â”€â–¶â”‚ DuckDB OHLCV â”‚â”€â”€â–¶â”‚ correlation_firewall  â”‚â”€â”€â–¶â”‚ cascade + earnings  â”‚

 â”‚ macro_alpha   â”‚   â”‚ technical_   â”‚   â”‚ pea_position_sizer    â”‚   â”‚ revocation / LLM    â”‚

 â”‚ AMFâ†’FMPâ†’YF    â”‚   â”‚ scorer+DCA   â”‚   â”‚ ATR rebalancer        â”‚   â”‚ weekly historian    â”‚

 â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜   â”‚ equity_metricsâ”‚   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜

                     â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜                                         â–¼

   SQLite: portfolio Â· audit Â· equity curve              Discord + Streamlit (Mission Control)

   logs/ + database/pipeline_status.json

```



**One analysis pass:** fetch â†’ VIX â†’ raw signals â†’ mark-to-market (+ equity

snapshot) â†’ cascade â†’ Smart-DCA â†’ audit log â†’ Discord alerts â†’ pipeline heartbeat.



### Phase 38 add-on architecture (Monte Carlo + Stress + Red Team)



```

Portfolio Weights + DuckDB Returns

              â”‚

              â–¼

  02_quant_engine/stochastic_models.py

  - Cholesky(cov)

  - Correlated GBM paths

  - Fan chart percentiles (P05..P95)

              â”‚

              â–¼

  Streamlit Portefeuille Tab

  - On-demand Monte Carlo fan chart

  - Tail risk (VaR/CVaR)

  - Black swan stress table

```



```

Exploration Tab (selected ticker)

              â”‚

              â–¼

04_orchestrator_ai/red_team_agent.py

  Bull Agent  â”€â”�

               â”œâ”€ asyncio.gather â”€â–º Judge Agent â”€â–º 3-sentence verdict

  Bear Agent  â”€â”˜

              â”‚

              â–¼

UI Boxes: st.info (Bull), st.warning (Bear), st.error (Judge)

```



---



## Logging & observability



Designed for a **personal** PEA terminal: enough detail to copy into notes or

debug a silent day, without enterprise noise.



| Piece | Role |

|-------|------|

| `01_memory_core/logging_setup.py` | Console (compact INFO) + rotating **DEBUG** files |

| `logs/<component>.log` | Per-component trails (`scheduler`, `dashboard`, `cascade`, â€¦) |

| `logs/pea_pollux_all.log` | Fan-in of everything |

| `database/pipeline_status.json` | Last pass health for Mission Control (green / amber / red) |

| Dashboard â†’ **Architecture & Logs** | Pick a file, tail N lines, select/copy |



Format in files: `timestamp | LEVEL | logger | file:line function | message`.



Entry points call `setup_app_logging()` once (scheduler already does). `logs/`

is git-ignored.



---



## Module reference



| Path | Responsibility |

|------|----------------|

| `00_data_sensors/market_prices_api.py` | Batch OHLCV download â†’ DuckDB |

| `00_data_sensors/macro_alpha_api.py` | VIX, Put/Call, insiders (**AMFâ†’FMPâ†’YF**), Polymarket |

| `00_data_sensors/scrapers/amf_scraper.py` | AMF Opendatasoft v2.1 + BDIF `/back` (`RechercheTexte`) â†’ legacy BDIF + 12h circuit |

| `01_memory_core/env_loader.py` | Native `api_keys.env` parser (no python-dotenv) |

| `01_memory_core/data_models.py` | Pydantic contracts (`Signal`, `Position`, `PortfolioState`) |

| `01_memory_core/sqlite_portfolio.py` | Account, positions, audit, **`portfolio_history`**, **`news_history`** |

| `01_memory_core/duckdb_manager.py` | OHLCV store (ATR / correlation / indicators) |

| `01_memory_core/logging_setup.py` | Rotating logs + pipeline heartbeat |

| `02_quant_engine/technical_scorer.py` | MRE signals; `RSI_OVERSOLD_THRESHOLD` from YAML |

| `02_quant_engine/smart_dca_engine.py` | Regime-aware Core DCA |

| `03_risk_portfolio/pea_position_sizer.py` | Half-Kelly Ã— vol parity; **`size_with_explanation`** for UI |

| `03_risk_portfolio/correlation_firewall.py` | Sector / Pearson / VIX panic |

| `03_risk_portfolio/monthly_rebalancer.py` | Modes `atr` (daily) vs `shave` (monthly) |

| `03_risk_portfolio/equity_metrics.py` | Shared DD / CAGR / Sharpe / Sortino |

| `04_orchestrator_ai/signal_priority_cascade.py` | Conductor (all vetoes + sizing) |

| `04_orchestrator_ai/earnings_blackout.py` | Per-ticker corporate blackout |

| `04_orchestrator_ai/macro_veto.py` | Macro calendar blackout |

| `04_orchestrator_ai/revocation_engine.py` | Expire / revoke stale PENDING |

| `04_orchestrator_ai/weekly_historian.py` | Friday CIO digest + rejection taxonomy |

| `05_interfaces/terminal_dashboard.py` | Mission Control + tabs |

| `05_interfaces/trade_cards.py` | HTML cards: Tier, Kelly, ATR risk â‚¬, sector impact |

| `05_interfaces/discord_copilot.py` | Alerts + approve/revoke buttons |

| `main_scheduler.py` | Daemon + CLI (`--now`, `--weekly`, `--atr-stops`, `--rebalance`) |

| `seed_account.py` | Seed / reset PEA cash & positions |

| `tools/build_llm_dump.py` | Regenerate `PROJECT_FULL_DUMP_FOR_LLM.md` |

| `tools/sync_universe_from_bourso.py` | Refresh PEA universe YAML |

| `00_data_sensors/newsletter_api.py` | IMAP headlines + LLM morning Zeitgeist |

| `00_data_sensors/newsletter_ingest/` | Modules IMAP (whitelist, dedupe, parse) |

| `00_data_sensors/fundamentals_api.py` | Finnhub + yfinance (Value/Quality) |

| `02_quant_engine/quantitative_math.py` | VaR, CVaR, Z-Score, variance (NumPy pur) |

| `02_quant_engine/stochastic_models.py` | Monte Carlo corrÃ©lÃ© (Cholesky + GBM) |

| `03_risk_portfolio/stress_tester.py` | Stress tests historiques (2008/2020/2022) |

| `04_orchestrator_ai/red_team_agent.py` | DÃ©bat Bull/Bear/Judge (OpenRouter) |

| `02_quant_engine/walk_forward_backtester.py` | Walk-forward equity scaffold on DuckDB |

| `tests/` | pytest foundations (sizing, equity metrics, cards, dedupe) |

| `.github/workflows/ci.yml` | CI on push/PR |



---



## APIs that work



| Source | Status | Notes |

|--------|--------|-------|

| **yfinance OHLCV** | Works | Primary market data â†’ DuckDB |

| **`^V2TX` / `^VIX`** | Partial | VSTOXX often missing on Yahoo â†’ falls back to US VIX as panic proxy |

| **AMF ODS / BDIF back** | Primary | Public, no paid key; ODS explore v2.1 + `/back/api/v1` with `RechercheTexte` |

| **AMF BDIF legacy** | Fragile | `/api/v1` often WAF/500 â†’ 12h circuit â†’ FMP â†’ Yahoo |

| **FMP insider API** | Optional | Needs `FMP_API_KEY` |

| **yfinance insiders** | Tertiary | Sparse on many `.PA` mid-caps |

| **Options Put/Call** | Partial | Sparse for EU â†’ neutral `1.0` |

| **Polymarket Gamma** | Live | Macro context + conviction modifier (never a trade trigger) |

| **OpenRouter** | Optional | Explanations / sentiment / weekly report / deep news |

| **Boursorama scraper** | Fragile | PEA profile, consensus, news (dates normalized to ISO) |

| **Native ticker tape** | Works | HTML/CSS marquee â€” blue chips + Clearbit logos (no TradingView tape) |

| **Yahoo Mail IMAP** | Optional | Morning Briefing Zeitgeist (`YAHOO_MAIL_USER`) |



Graceful degradation: missing sources return **neutral** values; the daemon does not crash.



---



## Installation



> Streamlit depends on `pyarrow` â†’ use **Python 3.11 or 3.12 x64** (`venv_x64`).



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

| `AMF_API_KEY` | placeholder | Public ODS/BDIF â€” use `free_public_ods_api` |

| `YAHOO_MAIL_USER` / `YAHOO_MAIL_APP_PASSWORD` | briefing | Morning Briefing IMAP |

| `EODHD_API_KEY` | optional | Reserved for paid EU market data |

| `ALPHAVANTAGE_API_KEY` | optional | Fundamentals fallback (Finnhub â†’ Alpha Vantage â†’ yfinance) |

| `OPENFIGI_API_KEY` | optional | Bloomberg OpenFIGI symbol/ISIN mapper (cached in SQLite) |



Keys are loaded by a **native** parser (`01_memory_core/env_loader.py`) â€”

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



- `config/macro_calendar.yaml` â€” ECB / CPI / NFP style events (manual; later API sync)  

- `config/earnings_calendar.yaml` â€” per-ticker earnings/div dates (starts empty)  

- `config/pea_universe.yaml` â€” ~600 PEA-eligible names by sector  



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



Launch: `.\run_dashboard.ps1` â†’ http://localhost:8501



On first open each session, the dashboard **auto-syncs** market data

(`load_universe`, `get_last_prices`, `get_vix`) behind a global spinner.



### Native ticker tape (top of page)



Replaces the old TradingView widget (which showed red errors on `.PA` small caps).

A **CSS marquee** scrolls blue-chip performances with **Clearbit logos** and a

period selector: **1j / 5j / 1m**. Data from `get_market_performance` â€” no

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

| **General & Signaux** | Morning Briefing (chargement patient), suggestion + ranking/pÃ©pites **cliquables**, geo brief, funnel |

| **Portefeuille** | Equity curve, sunburst, **stops ATR 2.5x**, wallet editor â†’ SQLite |

| **Exploration** | **Recherche univers 600+** (selectbox haut de page), dossier ticker, **ticket d'ordre PEA**, **checklist dÃ©cision**, news archivÃ©es SQLite, synthÃ¨se IA 24h |

| **Univers** | Liste PEA + tags techniques **cliquables** (full filtered view) |

| **Architecture & Logs** | **TÃ©lÃ©mÃ©trie live** (health check env + DB), **`risk_params.yaml` actifs**, expanders logique quant, logs (5000 lignes) |



### News memory (`news_history`)



Headlines are **upserted into SQLite** on each fetch (`PortfolioDB.save_news`).

The UI reads `get_news_history(ticker)` first; live APIs run only if fewer than

3 cached articles. Boursorama relative dates (`il y a 2h`, empty, `Recent`) are

normalized to `YYYY-MM-DD HH:MM` at scrape time.



### Rich trade cards (what you see before approving)



For each PENDING BUY the card shows:



1. **Conviction score** (colour: amber 65â€“75 / neon 76â€“100) + Tier label  

2. **Sizing rationale** â€” Kelly fraction, measured vol + vol factor, ticket â‚¬, weight % of equity  

3. **R-style risk** â€” max â‚¬ / % equity loss if the **2.5Ã—ATR** stop is hit  

4. **Sector impact** â€” e.g. Luxury 18% â†’ 23% (cap 25%), not just pass/fail  

5. **Streamlit Approuver / Rejeter** â€” updates SQLite instantly (complements Discord)  



---



## LLM full dump



For one-shot context in another LLM / agent:



```bash

python tools/build_llm_dump.py

# optional: skip architecture preamble

python tools/build_llm_dump.py --no-summary

```



Writes **`PROJECT_FULL_DUMP_FOR_LLM.md`** with:



- **Architecture snapshot** â€” layer map, Phase 26â€“28 highlights, hard rules

- **Priority file list** â€” README, risk YAML, scorer, dashboard, scheduler

- **Grouped file index** â€” by directory, with line counts and â­� on key files

- **Full source bodies** â€” fenced code blocks for every included file



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

| **Morning briefing** | **08:25** | Newsletter IMAP â†’ LLM Zeitgeist â†’ `morning_briefing.json` |

| ATR stops | 08:35 weekdays | Dynamic ATR SELLs â†’ webhook |

| Profit-shave | Probe 08:30 (acts on the **1st**) | +20% trim â†’ webhook |

| Analysis | 09:00, 13:30, 17:10 weekdays | Full pipeline â†’ Discord + heartbeat |

| Weekly report | Friday 18:00 | Historian â†’ webhook |



Weekends: analysis / ATR skipped automatically.



---



## Roadmap / future improvements



Prioritized for a **validated personal PEA process**, not feature theatre.

Broker import must **diff** vs SQLite (never blind overwrite). Prefer official/API

sources over furtive HTML scraping.



### Done (Phase 15â€“20)



| Item | Notes |

|------|-------|

| AMFâ†’FMPâ†’Yahoo insider cascade | Official FR source first |

| Equity curve + shared metrics | Live dashboard; ready for backtest reuse |

| Daily ATR vs monthly shave | Split jobs / CLI flags |

| Earnings blackout engine | Calendar empty â€” fill via API later |

| ADV / max positions / RSI / corr lookback | Wired in `risk_params.yaml` + cascade |

| Mission Control + trade cards + logs | Operator UX |

| **Decision funnel waterfall + rejection pie** | âœ… Phase 17 â€” 7J/30J audit-log analytics in General |

| **Valuation + 10y annual returns** | âœ… Phase 18 â€” Exploration (buy zone, P/E, P/B, **1M/1Y**, annual bars) |

| **Newsletter whitelist + Zeitgeist** | âœ… Phase 19 â€” `NewsletterSensor` + 08:25 job + dashboard |

| **Ensemble conviction scoring** | âœ… Phase 20 â€” 4 axes, emit â‰¥65; radar + Command Center approve |

| **What-if 1000â‚¬ + walk-forward scaffold** | âœ… Exploration simulator + `walk_forward_backtester.py` |

| **Terminal polish (TV / zone / ranking / Polymarket)** | âœ… Phase 21 â€” EPA: ticker map, flat buy-zone fix, fingerprint ranking, SSL-tolerant Gamma |

| **Smart UX + deep news + logos + pÃ©pites** | âœ… Phase 22 |

| **UX overhaul (tape / GO / news diversity)** | âœ… Phase 23 â€” no GO, logos off, multi-source news, deep IA narrative |

| **Polymarket harden + news clean + tape + logs** | âœ… Phase 24 â€” JSONDecode guard, no heuristic pills, blue-chip tape, briefing button, log tail 5k |

| **Auto-sync + score holistique + UI exÃ©cution** | âœ… Phase 26 â€” warmup au dÃ©marrage, News/Polymarket dans le score, stops ATR visibles, tickets/checklist, tables cliquables |

| **Bandeau natif + news SQLite + exploration universelle** | âœ… Phase 27 â€” marquee HTML/CSS, `news_history`, dates exactes, selectbox 600+ tickers |

| **TÃ©lÃ©mÃ©trie live Architecture & Logs** | âœ… Phase 28 â€” health check sources, risk_params actifs, expanders logique quant |

| **UX rename + clickable tape + news history** | âœ… Phase 29 â€” Pollux branding, query-param tape, SynthÃ¨se IA, full news DB |

| **Native env + AMF ODS + TV EURONEXT + uncapped lists** | âœ… Phase 32 |

| **Multi-factor + Finnhub + Data Lake analyste** | âœ… Phase 35â€“36 |

| **VaR/CVaR + Z-Score acadÃ©mique** | âœ… Phase 37 |

| **Monte Carlo + stress tests + red teaming IA** | âœ… Phase 38 |

| **UX (tape fix, briefing async, near-miss radar, ML export)** | âœ… Phase 39 |

| **Cash sweep, Discord daily digest, 10y backfill, forward curve, ML store** | âœ… Phase 40 |

| **AMF semantic parsing (legal FR regex), fluid log viewer, system telemetry** | âœ… Phase 41 |

| **Institutional Overhaul: data quality (auto_adjust), parallel I/O, drawdown breaker, OpenFIGI, Alpha Vantage, backtester look-ahead fix, CI ruff** | âœ… Phase 42 |

| **Pydantic config validation, backtester exits, dashboard DuckDB dedup, XGBoost ML, Devil's Advocate PEA** | âœ… Phase 44 |

| **Dynamic Market Regime, EWMA Risk Math, Pipeline Idempotency** | âœ… Phase 45 |

| **ML Historical Bootstrapper, Gemini 2.5 Optimization** | âœ… Phase 46 |

| **Ultimate Performance (SQLite I/O fix), Pure Webhooks for Discord** | âœ… Phase 47 |

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

- **Labels:** from `audit_log` â€” did the signal reach +5% within 20 trading days

  after APPROVED/EXECUTED?

- **Model:** XGBoost gradient-boosted trees (tabular) + optional sentence-transformer

  embeddings for headline text.

- **Integration:** predicted probability displayed alongside the existing conviction

  score in the dashboard (e.g., "ML confidence: 72%").

- **Data export:** available today in the Architecture tab (CSV download of both tables).



### Later



Paid VSTOXX Â· AMF resilience Â· multi-core ETF rotation Â· trailing ATR after shave Â·

EUR/USD note in CIO digest Â· rolling Sharpe chart.



**Non-goals:** auto-broker execution, leverage, LLM-as-trader, US pennies.



---



## Troubleshooting



| Symptom | Fix |

|---------|-----|

| Dashboard Â« En attenteâ€¦ Â» | `python seed_account.py --cash 10000` then `--now` |

| Empty equity curve | Needs at least one `update_portfolio` (pass or wallet save) |

| Mission Control pass = Â« jamais Â» | Run `python main_scheduler.py --now` once |

| Empty `logs/` | Same â€” scheduler/dashboard create files on first run |

| `pyarrow` / Streamlit fail | Python **3.11/3.12 x64** |

| VIX stuck / `^V2TX` 404 | Falls back to `^VIX` |

| AMF HTTP 500 | Expected; FMP then Yahoo; circuit ~12h |

| No FMP insiders | Set `FMP_API_KEY` |

| ATR stop never fires | Need DuckDB history + losing position; try `--atr-stops` |

| Cards show ATR risk n/a | Fetch history with `--now` first |

| LLM / weekly silent | `OPENROUTER_API_KEY` / `DISCORD_WEBHOOK_URL` |

| Cash too small for CW8 | MICRO mode: 1 liquid share + cash runway (by design) |

| Newsletter IMAP auth fail | Use Yahoo **app password**, folder name exact, SSL 993 |

| News dates show `Recent` | Re-open ticker in Exploration â€” scraper now stamps ISO; archive in `news_history` |

| Red TradingView tape errors | Fixed in Phase 27 â€” native HTML marquee replaces TV widget |

| Briefing flashes error on boot | Phase 27 â€” patient `st.info` + manual generate button |

| CI / pytest | `python -m pytest -q` |



---



## Disclaimer



Decision-support and educational tool only. **No automated execution. No financial

advice.** You are solely responsible for every trade. Past or backtested results

do not guarantee future performance.



Â© 2026 Pollux Gronier â€” PEA Pollux.



---



## English guide



**PEA Pollux** is a personal quantitative research terminal for a French **PEA**

( tax-advantaged equity savings plan ). It helps you *research*, *size*, and *risk-manage*

ideas â€” but **never places broker orders**.



### What it does



1. **Ingests data** â€” OHLCV (yfinance â†’ DuckDB), insiders (AMF â†’ FMP â†’ Yahoo),

   news, newsletters, macro proxies (VIX, Polymarket).

2. **Scores opportunities** â€” multi-model ensemble (Trend, Mean-Reversion, Breakout,

   Context) with fundamentals (Finnhub) and sentiment modifiers.

3. **Filters through risk** â€” VIX panic, sector caps, correlation firewall, earnings

   blackout, liquidity floor, vol-parity sizing (whole shares only).

4. **Surfaces decisions** â€” Streamlit dashboard + optional Discord copilot for

   manual approve/reject.

5. **Explains & challenges** â€” LLM rationales, weekly digest, Bull/Bear red teaming.



### Phase 38â€“41 highlights



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

Data sensors â†’ DuckDB/SQLite â†’ Quant engine â†’ Risk cascade â†’ UI (Streamlit/Discord)

                                      â†“

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



- **Math-first, AI-second** â€” models decide eligibility; LLMs only explain or debate.

- **Manual execution** â€” you always confirm trades.

- **Graceful degradation** â€” missing API keys â†’ neutral fallbacks, no crashes.

- **Vectorized math** â€” NumPy/Pandas for VaR, Monte Carlo, Z-Scores.

- **On-demand heavy compute** â€” Monte Carlo runs behind a button + cache, not on every page load.



---

## Phase 49 : The Apex Optimization (Current)



*   **âš¡ Blazing Fast UI :** Caching systÃ©matique (@st.cache_resource, @st.cache_data) et lazy loading pour un Dashboard sub-seconde.

*   **ðŸ“Š Interactive Metrics :** Drill-down des mÃ©triques via st.popover (Market Breadth dynamique, Mini-charts VIX).

*   **ðŸ¤– DÃ©ploiement StratÃ©gique (80% Rule) :** Force le dÃ©ploiement de capital sur les meilleurs signaux techniques rejetÃ©s si l'exposition Cash est > 20% en rÃ©gime Bull.

*   **ðŸ“ˆ Simulateur ML Autonome :** Backtester visuel intÃ©grÃ© dans le Dashboard permettant d'Ã©valuer la stratÃ©gie Machine Learning vis-Ã -vis d'un Buy & Hold (CW8) avec prise en compte du slippage.

*   **ðŸ“¡ Ingestion IncrÃ©mentale :** Optimisation drastique des appels rÃ©seaux (DuckDB get_latest_dates) pour ne tÃ©lÃ©charger que le strict nÃ©cessaire depuis yfinance.

*   **ðŸš¨ Copilot Discord V2 :** Refonte des webhooks avec intÃ©gration du Notional estimÃ©, formatage premium et ping @everyone.



---

## Phase 51 : Robust ML Pipeline Refactoring (Current)



*   **Sequential Bootstrapping:** Remplacement du multiprocessing instable sous Docker par une boucle sÃ©quentielle stricte avec barre de progression (\	qdm\).

*   **Memory Optimization:** Sauvegarde incrÃ©mentale directe dans le CSV (\ml_training_dataset.csv\) pour Ã©viter les crash OOM lors du balayage de 10 ans d'historique sur plus de 600 tickers.

*   **No Look-Ahead Bias / No Ban IP:** DÃ©sactivation intelligente des webhooks de scraping live (Sentiment Boursorama / YFinance) lorsque le bot tourne en simulation historique. L'infÃ©rence live conserve 100% de ses capacitÃ©s.

*   **Error Resilience:** Poursuite automatique du bootstrap mÃªme si des API financiÃ¨res flanchent ou que l'historique d'un ticker est corrompu.



---

## Phase 54.5 : Loud Fallback, UI Fluidity & Production Prep (Current)



*   **10-Year Data Lake Uncapping:** L'historique stockÃ© dans DuckDB par `market_prices_api.py` monte jusqu'Ã  10 ans, offrant un set de donnÃ©es massif pour les modÃ¨les ML tout en limitant les appels d'API.

*   **Loud Fallback Mechanism:** En cas de panne d'une API tierce (Boursorama, OpenRouter, etc.), le systÃ¨me maintient une fluiditÃ© totale en tombant sur des valeurs neutres (`0.0`), tout en dÃ©clarant un Ã©tat `data_degraded_mode=True` dans `pipeline_status.json` pour avertir l'utilisateur.

*   **FluiditÃ© & Ticker Sync:** RÃ©solution d'un bug majeur de dÃ©synchronisation de l'UI grÃ¢ce Ã  l'utilisation d'Ã©vÃ©nements `on_change` asynchrones dans Streamlit, couplÃ© Ã  une limitation des graphiques Plotly aux 500 derniÃ¨res bougies pour maintenir 60 FPS.

*   **UI Reorganization & Sub-Tabs:** Restructuration totale du Dashboard pour Ã©liminer le bruit visuel avec 4 onglets principaux (`Market & Macro`, `Ticker Deep-Dive`, `Portfolio & Execution`, `System Logs`) et 3 sous-onglets encapsulÃ©s pour les fiches actions.

*   **Strict News Filtering:** ImplÃ©mentation d'un filtre regex anti-spam universel (`discount|free|referral|newsletter|sponsor...`) qui intercepte et purifie les flux RSS avant leur traitement IA (Morning Briefing).


---
## Phase 55 : Multi-Source News Engine, Data Lineage & Quant ML Evolution (Current)

*   **Multi-Source News Engine:** Filtre multi-sources (Substack, Google News, Boursorama) avec historique des news archivées dans DuckDB/SQLite et badges IA (Bullish/Bearish).
*   **Data Lineage Tab:** Section architecture du tableau de bord affichant la provenance des données, l'heure de synchronisation, et le statut via pipeline_status.json.
*   **XGBoost Multi-Horizon:** Support intégré de cibles ML dual-horizon (30 jours tactique, 126 jours structurel) avec inférence SHAP XAI.
*   **Piotroski Score & Value Trap Veto:** Intégration du score Piotroski (via FMP) dans le cache SQLite avec un veto strict (<4) au niveau du cascade.
*   **Performances & Sécurité:** Optimisation de Plotly, fix du Thread lock des connexions DB avec @st.cache_resource, et validation étendue de la whitelist newsletter.


## Recent Updates (August 2026)
- **UI/UX Bloomberg Overhaul**: Upgraded tables to native \st.dataframe\ with link formats and strict color-coding. Migrated Top/Flop to Market & Macro.
- **Extreme Anti-Spam**: Drastically expanded the spam regex to block lifestyle, promo, and exclusive offer emails from polluting the stream.
- **Robust IFrames**: Hardened the TradingView iframe rendering with try/except fallbacks to Plotly native charts.
