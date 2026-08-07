# PEA Pollux — V-Prime Quant Terminal

> **A professional quantitative research and portfolio management terminal designed for a personal PEA (Plan d'Épargne en Actions) account. Transparent, manual validation, zero automatic execution.**

**PEA Pollux** is a highly advanced data-lake and algorithmic scoring engine. It ingests market data, calculates multi-factor signals (Momentum, Mean Reversion, Quality, Insider Flow), applies strict risk-management cascades, and presents its findings via a high-performance Streamlit dashboard or a Discord Copilot.

**The system never routes orders to a broker.** Quantitative models decide *what* deserves to be studied; AI models *explain* (rationale, sentiment, red-teaming). **This is not investment advice.**

---

## 🌟 V-Prime Features & Recent Overhauls

- **Bulletproof Streamlit Dashboard**: The `05_interfaces/terminal_dashboard.py` has been completely redesigned into a Bloomberg-style "V-Prime" terminal. It features instant-loading, heavy caching, robust SQL error-handling, and gracefully degrades when datasets are missing.
- **Ticker Deep-Dive (Instant Terminal)**: Search the entire PEA universe and instantly view:
  - Corporate Profiles & Fundamentals (P/E, P/B, EV/EBITDA).
  - Native Plotly Price Charts with SMA50/SMA200/RSI(14) overlays.
  - Ticker-specific News Feeds with 30D Sentiment Index.
  - Multi-Scenario Future Theories (Bull / Quant Base / Bear).
  - AMF Insider Flow tracking.
- **SQLite / DuckDB Split**: Ultra-fast time-series analytics (OHLCV) runs on DuckDB, while transactional state (Portfolio, Pending Signals, News Master) runs on robust `sqlite3` reads to prevent database locking in concurrent Streamlit sessions.
- **Interactive Discord Copilot**: A bi-directional assistant allowing you to approve or reject signals directly from Discord using Slash commands (`/approve`, `/reject`).
- **Live Alpha Analytics**: Real-time institutional performance tracking (Jensen's Alpha, Beta, Information Ratio) benchmarked against MSCI World (`CW8.PA`).
- **Smart DCA (Core/Satellite)**: Automated risk-parity scaling. Accumulate the `CW8.PA` core aggressively when the market is below its 200-day SMA, and carefully build Satellite positions with excess budget.
- **Machine Learning & StatArb**: XGBoost meta-labeling trained on point-in-time Technicals, robust Macro indices (`^FCHI`, `^GSPC`, `EURUSD=X`), and dynamic Sector Relative Strength (StatArb).
- **Real SHAP Explainability**: The Streamlit interface displays true SHAP value feature attributions directly from the XGBoost explainer, revealing the exact neural logic behind AI trade approvals.

---

## 🏗️ Architecture

```text
                       ┌──────────────────────────────────────────────┐
                       │            main_scheduler.py                 │
                       │  Paris: 09:00 / 13:30 / 17:10                │
                       │  + ATR 08:35 · shave 1st · Fri 18:00         │
                       └───────────────┬──────────────────────────────┘
                                       │
  00_data_sensors        01/02              03_risk_portfolio        04_orchestrator_ai
 ┌────────────────┐   ┌────────────────┐   ┌──────────────────────┐   ┌──────────────────────┐
 │ market_prices  │──▶│ DuckDB OHLCV   │──▶│ correlation_firewall │──▶│ cascade + earnings   │
 │ macro_alpha    │   │ technical_     │   │ pea_position_sizer   │   │ revocation / LLM     │
 │ AMF→FMP→YF     │   │ scorer+DCA     │   │ ATR rebalancer       │   │ weekly historian     │
 └────────────────┘   │ equity_metrics │   └──────────────────────┘   └──────┬───────────────┘
                      └────────┬───────┘                                     │
                               │                                             ▼
                               │                       Discord + Streamlit (Mission Control)
                               ▼                       (05_interfaces/terminal_dashboard.py)
                SQLite: portfolio · audit · news_master
                logs/ + database/pipeline_status.json
```

---

## 🧬 Empreinte Multi-Stratégies (Scoring)

Every ticker receives a **score from 0 to 100** called the **Empreinte** (fingerprint). A BUY signal is only emitted when **conviction ≥ 65**. 

| Axis | Weight | What it measures |
|------|--------|------------------|
| **MR** | 35 % | Mean Reversion: RSI-14 oversold + price above long-term SMA-200. Z-Score bonus. |
| **Mom** | 25 % | Momentum: Close > SMA-5 > SMA-50 > SMA-200, MACD histogram positive. |
| **Q/V** | 20 % | Quality / Value: Low P/E, low P/B, high ROE, low Debt/Equity. |
| **Ins** | 20 % | Insider Confidence: Directors buying their own stock via AMF/FMP. |

**Modifiers:** News Sentiment (+10 / −15) and Macro Context.

---

## 🛡️ Strict Risk Cascade

Implemented in `signal_priority_cascade.py`. Checked in order before sizing:

1. **VIX panic**: Freeze new satellite buys if V2TX/VIX > 30.
2. **Macro & Earnings Blackout**: No buying before ECB/CPI/NFP or corporate earnings dates.
3. **Liquidity / Max Positions**: Prevents micro-fragmentation of the satellite budget.
4. **Sector & Correlation**: Prevents over-exposure to a single theme.
5. **Sizing**: Hierarchical Risk Parity (HRP) / Half-Kelly × inverse-vol parity.

---

## 🚀 Installation & Setup

> **Note**: Streamlit relies on `pyarrow`, requires Python 3.11 or 3.12 x64.

```bash
# 1. Clone & Environment
git clone https://github.com/Polluxgnr/Peatrading.git pea_pollux
cd pea_pollux
python -m venv venv
venv\Scripts\Activate.ps1  # Windows

# 2. Dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 3. Config
cp config/api_keys.env.example config/api_keys.env
# Fill in required API keys (Discord, OpenRouter, etc.)

# 4. Initialize Database
python seed_account.py --cash 10000
python main_scheduler.py --now

# 5. Launch V-Prime Terminal
.\run_dashboard.ps1
# (or `streamlit run 05_interfaces/terminal_dashboard.py --server.port 8501`)
```

---

## 🛠️ Usage

### CLI Commands
```bash
python main_scheduler.py --now          # Full analysis & ingestion pass
python main_scheduler.py --atr-stops    # Daily ATR evaluation
python main_scheduler.py --rebalance    # Monthly profit-shave
python main_scheduler.py                # Run daemon (Paris schedule)
```

### Dashboard Tabs
The Streamlit V-Prime terminal consists of:
1. **Mission Control**: Live HUD, Portfolio Value, Asset Allocation, and Pending Discord Executions.
2. **Market Pulse**: Market-wide breadth, Top Opportunities, High Momentum Leaders, and Global News Feed.
3. **Ticker Deep-Dive**: The ultimate quant sandbox for researching individual stocks.
4. **Portefeuille (Risque & Valo)**: Monte Carlo simulations, VaR, CVaR, and Black Swan stress tests.
5. **Quant Engine**: Direct access to DuckDB feature stores and ML metric configurations.

---

## 📜 Philosophy

1. **No fractional shares**: PEA sizing uses `math.floor` — one share or nothing.
2. **Math first, AI second**: LLMs never generate or approve trades. They synthesize text and explain scores.
3. **Zero crash tolerance**: Safely wrapped Streamlit components ensure graceful degradation. 
4. **Manual execution**: You always have the last word.

---
*Developed for personal quant research. Do not use for automated trading without extensive code auditing and local backtesting.*
