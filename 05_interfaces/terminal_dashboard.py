"""Web Terminal (Streamlit dashboard) for PEA Sniper Terminal V-Prime.

A read-only command center (Aegis Prime dark UI) for the PEA SQLite/DuckDB
architecture. It ONLY reads state; it never mutates any database.

Features:
  * Live TradingView ticker tape (streaming quotes).
  * Top HUD (equity, cash, PnL, active lines) + Risk/Macro HUD (VIX panic
    gauge, Core regime vs SMA200, satellite-budget usage, sector concentration).
  * Portfolio allocation sunburst coloured by unrealized PnL.
  * Market Explorer: best/worst performers over an adjustable interval
    (presets or a custom date range), full universe ranking + charts.
  * Signals & audit ledger.
  * Radar: local indicators (RSI/SMA/vol), alpha sensors (Put/Call, insiders,
    Polymarket), on-demand LLM news sentiment, TradingView chart + TA gauge.
  * Monte Carlo forecast (GBM equity projection).
  * Sidebar: auto-refresh, cache reset, system status.

Run (use the x64 Python 3.11 venv, Streamlit needs pyarrow):
    venv_x64\\Scripts\\streamlit run 05_interfaces/terminal_dashboard.py
"""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
import yaml
import yfinance as yf

# --- Cross-package imports (dirs start with digits) --------------------------
_ROOT = Path(__file__).resolve().parent.parent
for _sub in ("00_data_sensors", "01_memory_core", "02_quant_engine",
             "04_orchestrator_ai", "05_interfaces"):
    sys.path.insert(0, str(_ROOT / _sub))

from sqlite_portfolio import PortfolioDB  # noqa: E402

try:  # Optional sensors — the dashboard still works if a network dep is missing.
    from macro_alpha_api import MacroAlphaSensor  # noqa: E402
except Exception:  # noqa: BLE001
    MacroAlphaSensor = None  # type: ignore[assignment]

_DB_DIR = _ROOT / "database"
_SQLITE_PATH = _DB_DIR / "portfolio.db"
_UNIVERSE_PATH = _ROOT / "config" / "pea_universe.yaml"
_RISK_PATH = _ROOT / "config" / "risk_params.yaml"


def _load_risk() -> dict:
    """Load risk parameters (thresholds shown in the risk HUD)."""
    try:
        with open(_RISK_PATH, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001
        return {}


_RISK = _load_risk()
_VIX_PANIC = float(_RISK.get("VIX_PANIC_THRESHOLD", 30.0))
_SAT_BUDGET = float(_RISK.get("SATELLITE_MAX_BUDGET_PCT", 0.30))
_MAX_SECTOR = float(_RISK.get("MAX_SECTOR_WEIGHT_PCT", 0.25))
_CORE_TICKER = str(_RISK.get("CORE_TICKER", "CW8.PA"))

# Aegis Prime palette.
_BG = "#0E1117"
_CARD = "#151A22"
_GREEN = "#00E676"
_RED = "#FF3B30"
_BLUE = "#007AFF"
_MUTED = "#8B949E"


# =============================================================================
# Page config & CSS
# =============================================================================
st.set_page_config(
    page_title="PEA Sniper Terminal | V-Prime",
    layout="wide",
    page_icon="\U0001F6E1\uFE0F",
    initial_sidebar_state="collapsed",
)

st.markdown(
    f"""
<style>
    .stApp {{ background-color: {_BG}; }}
    .metric-box {{
        background-color: {_CARD}; padding: 18px 20px; border-radius: 10px;
        border-left: 5px solid {_GREEN}; box-shadow: 0 4px 6px rgba(0,0,0,0.3);
        margin-bottom: 12px;
    }}
    .metric-box.blue    {{ border-left-color: {_BLUE}; }}
    .metric-box.red     {{ border-left-color: {_RED}; }}
    .metric-box.muted   {{ border-left-color: #30363D; }}
    .metric-title {{ color: {_MUTED}; font-size: 13px; text-transform: uppercase;
        letter-spacing: 1px; }}
    .metric-value {{ color: #FFFFFF; font-size: 27px; font-weight: 700; margin-top: 5px; }}
    .metric-sub {{ font-size: 13px; margin-top: 5px; font-weight: 500; }}
    .sub-green {{ color: {_GREEN}; }}
    .sub-red   {{ color: {_RED}; }}
    .sub-muted {{ color: {_MUTED}; }}
    .info-text {{ color: #A3B3BC; font-size: 14px; font-style: italic;
        margin-bottom: 15px; padding-left: 10px; border-left: 3px solid #30363D; }}
    h1, h2, h3, h4 {{ color: #E6E9F0 !important; }}
</style>
""",
    unsafe_allow_html=True,
)


# =============================================================================
# Cached data loaders (read-only)
# =============================================================================
@st.cache_data(ttl=300)
def load_universe() -> pd.DataFrame:
    """Load the full tradable universe as a DataFrame.

    Returns:
        pd.DataFrame: Columns ``Ticker``, ``Name``, ``Sector`` (empty on error).
    """
    try:
        with open(_UNIVERSE_PATH, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        rows = [
            {"Ticker": e["ticker"], "Name": e.get("name", e["ticker"]), "Sector": sector}
            for sector, members in data.get("universe", {}).items()
            for e in members
        ]
        return pd.DataFrame(rows)
    except Exception:  # noqa: BLE001
        return pd.DataFrame(
            [{"Ticker": t, "Name": t, "Sector": "Unknown"}
             for t in ("MC.PA", "OR.PA", "AI.PA", "ASML.AS", "SAP.DE")]
        )


@st.cache_data(ttl=60)
def load_portfolio_state():
    """Load the current portfolio snapshot (cached 60s).

    Returns:
        PortfolioState | None: The portfolio, or ``None`` if the DB is missing.
    """
    if not _SQLITE_PATH.exists():
        return None
    return PortfolioDB(db_path=_SQLITE_PATH).get_portfolio_state()


@st.cache_data(ttl=60)
def load_signals(statuses: tuple[str, ...], limit: int | None = None) -> pd.DataFrame:
    """Load audit-log rows for the given statuses (cached 60s)."""
    if not _SQLITE_PATH.exists():
        return pd.DataFrame()
    db = PortfolioDB(db_path=_SQLITE_PATH)
    return pd.DataFrame(db.fetch_signals_by_status(list(statuses), limit=limit))


@st.cache_data(ttl=300, show_spinner=False)
def get_market_performance(
    tickers: tuple[str, ...],
    period: str | None = "1mo",
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """Compute performance over a preset period or an explicit date range.

    Args:
        tickers: Tickers to evaluate.
        period: yfinance period string (used when ``start`` is None).
        start: Optional ISO start date (overrides ``period``).
        end: Optional ISO end date.

    Returns:
        pd.DataFrame: Columns ``Ticker, Start Price, Current Price,
        Performance (%)`` sorted descending (empty on failure).
    """
    try:
        if start:
            raw = yf.download(list(tickers), start=start, end=end, progress=False,
                              auto_adjust=False)
        else:
            raw = yf.download(list(tickers), period=period, progress=False,
                              auto_adjust=False)
        if raw is None or raw.empty:
            return pd.DataFrame()
        close = raw["Close"] if "Close" in raw.columns.get_level_values(0) else raw
        close = close.ffill().bfill()
        if isinstance(close, pd.Series):
            close = close.to_frame(name=tickers[0])

        rows = []
        for t in close.columns:
            series = close[t].dropna()
            if len(series) < 2:
                continue
            start_price, end_price = series.iloc[0], series.iloc[-1]
            if start_price <= 0:
                continue
            rows.append({
                "Ticker": t,
                "Start Price": float(start_price),
                "Current Price": float(end_price),
                "Performance (%)": float((end_price / start_price - 1) * 100),
            })
        return pd.DataFrame(rows).sort_values("Performance (%)", ascending=False)
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def get_normalized_prices(
    tickers: tuple[str, ...], period: str | None, start: str | None, end: str | None
) -> pd.DataFrame:
    """Return prices rebased to 100 at the interval start (for line charts)."""
    try:
        if start:
            raw = yf.download(list(tickers), start=start, end=end, progress=False,
                              auto_adjust=False)
        else:
            raw = yf.download(list(tickers), period=period, progress=False,
                              auto_adjust=False)
        if raw is None or raw.empty:
            return pd.DataFrame()
        close = raw["Close"] if "Close" in raw.columns.get_level_values(0) else raw
        close = close.ffill().bfill()
        if isinstance(close, pd.Series):
            close = close.to_frame(name=tickers[0])
        return close / close.iloc[0] * 100
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


@st.cache_data(ttl=1800, show_spinner=False)
def get_recent_news(symbol: str, limit: int = 6) -> list[dict]:
    """Fetch recent news items for a symbol via yfinance (robust to schema)."""
    try:
        raw = yf.Ticker(symbol).news or []
        items = []
        for n in raw[:limit]:
            content = n.get("content", n)
            title = content.get("title") or n.get("title") or ""
            link = (
                content.get("clickThroughUrl", {}).get("url")
                or content.get("canonicalUrl", {}).get("url")
                or n.get("link")
                or "#"
            )
            date_str = content.get("pubDate") or content.get("displayTime") or ""
            provider = (content.get("provider") or {}).get("displayName", "")
            if title:
                items.append({"title": title, "link": link,
                              "date": (date_str or "")[:10] or "Recent",
                              "provider": provider})
        return items
    except Exception:  # noqa: BLE001
        return []


def _tv_symbol(ticker: str) -> str:
    """Map a Yahoo ticker to a TradingView exchange:symbol string."""
    mapping = {".PA": "EURONEXT", ".AS": "EURONEXT", ".BR": "EURONEXT",
               ".LS": "EURONEXT", ".DE": "XETR", ".MC": "BME", ".MI": "MIL",
               ".HE": "OMXHEX", ".IR": "EURONEXTDUBLIN"}
    for suffix, exch in mapping.items():
        if ticker.endswith(suffix):
            return f"{exch}:{ticker[: -len(suffix)]}"
    return ticker


@st.cache_data(ttl=600, show_spinner=False)
def get_vix() -> float:
    """Current market volatility gauge (VSTOXX, VIX proxy fallback)."""
    if MacroAlphaSensor is None:
        return 15.0
    try:
        return float(MacroAlphaSensor().get_european_vix())
    except Exception:  # noqa: BLE001
        return 15.0


@st.cache_data(ttl=900, show_spinner=False)
def get_core_regime() -> dict:
    """Return the Core ETF regime (price vs 200-day SMA)."""
    try:
        df = yf.download(_CORE_TICKER, period="1y", progress=False,
                         auto_adjust=False)
        if df is None or df.empty:
            return {}
        close = df["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close = close.dropna()
        price = float(close.iloc[-1])
        sma200 = float(close.tail(200).mean())
        return {
            "ticker": _CORE_TICKER,
            "price": price,
            "sma200": sma200,
            "crash": price < sma200,
            "gap_pct": (price / sma200 - 1) * 100 if sma200 else 0.0,
        }
    except Exception:  # noqa: BLE001
        return {}


@st.cache_data(ttl=600, show_spinner=False)
def get_indicators(ticker: str) -> dict:
    """Compute RSI(14) + SMA 5/50/200 + trend flags for one ticker."""
    try:
        import pandas_ta_classic as ta  # noqa: F401  (registers .ta accessor)
    except Exception:  # noqa: BLE001
        try:
            import pandas_ta as ta  # noqa: F401
        except Exception:  # noqa: BLE001
            return {}
    try:
        df = yf.download(ticker, period="1y", progress=False, auto_adjust=False)
        if df is None or df.empty:
            return {}
        close = df["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close = close.dropna()
        if len(close) < 30:
            return {}
        frame = close.to_frame("Close")
        rsi = frame.ta.rsi(close=frame["Close"], length=14)
        out = {
            "close": float(close.iloc[-1]),
            "rsi": float(rsi.iloc[-1]) if rsi is not None and not rsi.empty else None,
            "sma5": float(close.tail(5).mean()),
            "sma50": float(close.tail(50).mean()) if len(close) >= 50 else None,
            "sma200": float(close.tail(200).mean()) if len(close) >= 200 else None,
            "chg_1d": float((close.iloc[-1] / close.iloc[-2] - 1) * 100)
            if len(close) >= 2 else 0.0,
            "chg_5d": float((close.iloc[-1] / close.iloc[-6] - 1) * 100)
            if len(close) >= 6 else 0.0,
            "vol_ann": float(close.pct_change().dropna().tail(60).std() * (252 ** 0.5)
                             * 100),
        }
        return out
    except Exception:  # noqa: BLE001
        return {}


@st.cache_data(ttl=1800, show_spinner=False)
def get_alpha_signals(ticker: str) -> dict:
    """Fetch alternative-data signals (put/call, insider, polymarket)."""
    if MacroAlphaSensor is None:
        return {}
    try:
        s = MacroAlphaSensor()
        return {
            "put_call": s.get_put_call_ratio(ticker),
            "insider": s.get_insider_activity(ticker),
            "polymarket": s.get_polymarket_sentiment(f"{ticker} outlook"),
        }
    except Exception:  # noqa: BLE001
        return {}


def run_sentiment(ticker: str, headlines: list[str]) -> float | None:
    """Synchronously score news sentiment via the LLM (returns -100..100)."""
    if not headlines:
        return None
    try:
        import asyncio

        from news_sentiment_llm import NewsSentimentScorer
        return asyncio.run(NewsSentimentScorer().analyze_news(ticker, headlines))
    except Exception:  # noqa: BLE001
        return None


# =============================================================================
# Header + live ticker tape (streaming)
# =============================================================================
st.markdown(
    "<h1>\U0001F6E1\uFE0F PEA Sniper Terminal "
    "<span style='color:#00E676; font-size:20px;'>V-PRIME</span></h1>",
    unsafe_allow_html=True,
)

universe_df = load_universe()

# Live streaming ticker tape across the top.
_tape_symbols = ",".join(
    f'{{"proName":"{_tv_symbol(t)}","title":"{t}"}}'
    for t in universe_df["Ticker"].head(16)
)
_tape_html = f"""
<div class="tradingview-widget-container">
  <div class="tradingview-widget-container__widget"></div>
  <script type="text/javascript"
    src="https://s3.tradingview.com/external-embedding/embed-widget-ticker-tape.js" async>
  {{"symbols":[{_tape_symbols}],"showSymbolLogo":true,"colorTheme":"dark",
   "isTransparent":true,"displayMode":"adaptive","locale":"fr"}}
  </script>
</div>
"""
components.html(_tape_html, height=80)

portfolio = load_portfolio_state()
if portfolio is None:
    st.warning(
        "\u26A0\uFE0F En attente de l'initialisation des bases de donn\u00e9es "
        "par le Main Scheduler... (lancez `py main_scheduler.py --now`)"
    )
    st.stop()


# =============================================================================
# Top HUD
# =============================================================================
positions = portfolio.positions
invested = sum(p.market_value for p in positions)
unrealized = sum((p.current_price - p.avg_entry_price) * p.qty_shares for p in positions)
unrealized_pct = (unrealized / invested * 100) if invested else 0.0
cash_pct = (portfolio.cash_available / portfolio.total_equity * 100
            if portfolio.total_equity else 0.0)

c1, c2, c3, c4 = st.columns(4)
with c1:
    st.markdown(
        f'<div class="metric-box"><div class="metric-title">Valeur du Portefeuille</div>'
        f'<div class="metric-value">{portfolio.total_equity:,.2f} \u20ac</div>'
        f'<div class="metric-sub sub-muted">Investi: {invested:,.2f} \u20ac</div></div>',
        unsafe_allow_html=True,
    )
with c2:
    st.markdown(
        f'<div class="metric-box muted"><div class="metric-title">Liquidit\u00e9s (Cash)</div>'
        f'<div class="metric-value">{portfolio.cash_available:,.2f} \u20ac</div>'
        f'<div class="metric-sub sub-muted">{cash_pct:.1f}% de l\'\u00e9quity</div></div>',
        unsafe_allow_html=True,
    )
with c3:
    pnl_cls = "sub-green" if unrealized >= 0 else "sub-red"
    st.markdown(
        f'<div class="metric-box {"" if unrealized >= 0 else "red"}">'
        f'<div class="metric-title">PnL Latent</div>'
        f'<div class="metric-value">{unrealized:,.2f} \u20ac</div>'
        f'<div class="metric-sub {pnl_cls}">{unrealized_pct:+.2f}%</div></div>',
        unsafe_allow_html=True,
    )
with c4:
    st.markdown(
        f'<div class="metric-box blue"><div class="metric-title">Lignes Actives</div>'
        f'<div class="metric-value">{len(positions)}</div>'
        f'<div class="metric-sub sub-muted">Z\u00e9ro Levier Garanti</div></div>',
        unsafe_allow_html=True,
    )


# =============================================================================
# Risk / Macro HUD (VIX, regime, satellite budget, sector concentration)
# =============================================================================
vix = get_vix()
vix_panic = vix > _VIX_PANIC
regime = get_core_regime()

satellite_value = sum(p.market_value for p in positions if p.ticker != _CORE_TICKER)
sat_budget_eur = _SAT_BUDGET * portfolio.total_equity if portfolio.total_equity else 0.0
sat_used_pct = (satellite_value / sat_budget_eur * 100) if sat_budget_eur else 0.0

sector_weights = {}
for p in positions:
    sector_weights[p.sector] = sector_weights.get(p.sector, 0.0) + p.market_value
max_sector, max_sector_val = ("-", 0.0)
if sector_weights and portfolio.total_equity:
    max_sector = max(sector_weights, key=sector_weights.get)
    max_sector_val = sector_weights[max_sector] / portfolio.total_equity * 100

r1, r2, r3, r4 = st.columns(4)
with r1:
    vcls = "red" if vix_panic else ""
    vsub = ("\U0001F6A8 PANIC - achats satellites gel\u00e9s" if vix_panic
            else f"Calme (seuil {_VIX_PANIC:.0f})")
    vsubcls = "sub-red" if vix_panic else "sub-green"
    st.markdown(
        f'<div class="metric-box {vcls}"><div class="metric-title">Volatilit\u00e9 '
        f'(VIX/VSTOXX)</div><div class="metric-value">{vix:.1f}</div>'
        f'<div class="metric-sub {vsubcls}">{vsub}</div></div>',
        unsafe_allow_html=True,
    )
with r2:
    if regime:
        crash = regime["crash"]
        rsub = ("\U0001F534 SOUS SMA200 - DCA agressif" if crash
                else "\U0001F7E2 SUR SMA200 - DCA standard")
        rsubcls = "sub-red" if crash else "sub-green"
        st.markdown(
            f'<div class="metric-box {"red" if crash else ""}">'
            f'<div class="metric-title">R\u00e9gime Core ({_CORE_TICKER})</div>'
            f'<div class="metric-value">{regime["gap_pct"]:+.1f}%</div>'
            f'<div class="metric-sub {rsubcls}">{rsub}</div></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div class="metric-box muted"><div class="metric-title">R\u00e9gime Core '
            f'({_CORE_TICKER})</div><div class="metric-value">n/a</div>'
            f'<div class="metric-sub sub-muted">Donn\u00e9es indisponibles</div></div>',
            unsafe_allow_html=True,
        )
with r3:
    over = sat_used_pct > 100
    scls = "red" if over else "blue"
    ssub = f"{satellite_value:,.0f} / {sat_budget_eur:,.0f} \u20ac (max {_SAT_BUDGET*100:.0f}%)"
    st.markdown(
        f'<div class="metric-box {scls}"><div class="metric-title">Budget Satellite '
        f'Utilis\u00e9</div><div class="metric-value">{sat_used_pct:.0f}%</div>'
        f'<div class="metric-sub sub-muted">{ssub}</div></div>',
        unsafe_allow_html=True,
    )
with r4:
    breach = max_sector_val > _MAX_SECTOR * 100
    mcls = "red" if breach else ""
    msubcls = "sub-red" if breach else "sub-muted"
    st.markdown(
        f'<div class="metric-box {mcls}"><div class="metric-title">Concentration '
        f'Sectorielle Max</div><div class="metric-value">{max_sector_val:.0f}%</div>'
        f'<div class="metric-sub {msubcls}">{max_sector} (limite '
        f'{_MAX_SECTOR*100:.0f}%)</div></div>',
        unsafe_allow_html=True,
    )

# --- Sidebar: settings & controls -------------------------------------------
with st.sidebar:
    st.markdown("### \u2699\uFE0F Param\u00e8tres")
    auto_refresh = st.checkbox("Rafra\u00eechissement auto", value=False)
    refresh_secs = st.slider("Intervalle (s)", 30, 600, 120, 30,
                             disabled=not auto_refresh)
    if st.button("\U0001F504 Vider le cache & recharger", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    st.markdown("---")
    st.markdown("### \U0001F4CA \u00c9tat Syst\u00e8me")
    st.metric("Univers", f"{len(universe_df)} titres")
    st.metric("Derni\u00e8re MAJ", portfolio.last_updated.strftime("%d/%m %H:%M"))
    st.caption(
        "Amorcer le capital :\n\n`python seed_account.py --cash 10000`\n\n"
        "Lancer une passe :\n\n`python main_scheduler.py --now`"
    )
    if auto_refresh:
        st.caption(f"\u23F1\uFE0F Auto-refresh dans {refresh_secs}s")

st.write("---")


# =============================================================================
# Tabs
# =============================================================================
tab_pf, tab_mkt, tab_uni, tab_sig, tab_radar, tab_mc = st.tabs([
    "\U0001F3AF Portefeuille & Allocation",
    "\U0001F30D Explorateur de March\u00e9",
    "\U0001F4CB Univers Complet",
    "\u26A1 Signaux & Registre",
    "\U0001F4E1 Radar & Actualit\u00e9s",
    "\U0001F52E Projection Monte Carlo",
])

# --- Tab: Portfolio ----------------------------------------------------------
with tab_pf:
    st.markdown(
        "<div class='info-text'>D\u00e9composition de l'exposition sectorielle. "
        "La gestion du risque V-Prime interdit de d\u00e9passer 25% sur un seul "
        "secteur et 15% sur une seule ligne pour \u00e9viter la fragilit\u00e9 "
        "syst\u00e9mique.</div>",
        unsafe_allow_html=True,
    )
    if not positions:
        st.info("Le portefeuille est actuellement 100% en liquidit\u00e9s.")
    else:
        rows = [{
            "Ticker": p.ticker, "Secteur": p.sector, "Qté": p.qty_shares,
            "PRU (€)": p.avg_entry_price, "Cours (€)": p.current_price,
            "Valeur (€)": p.market_value, "Poids (%)": 0.0,
            "PnL Latent (%)": p.unrealized_pnl_pct * 100,
        } for p in positions]
        dfp = pd.DataFrame(rows)
        dfp["Poids (%)"] = dfp["Valeur (€)"] / portfolio.total_equity * 100

        sun = dfp[["Secteur", "Ticker", "Valeur (€)", "PnL Latent (%)"]].copy()
        if portfolio.cash_available > 0:
            sun = pd.concat([sun, pd.DataFrame([{
                "Secteur": "Liquidités", "Ticker": "CASH",
                "Valeur (€)": portfolio.cash_available, "PnL Latent (%)": 0.0}])],
                ignore_index=True)

        fig = px.sunburst(sun, path=["Secteur", "Ticker"], values="Valeur (€)",
                          color="PnL Latent (%)", color_continuous_scale="RdYlGn",
                          color_continuous_midpoint=0)
        fig.update_layout(template="plotly_dark", paper_bgcolor=_BG,
                          margin=dict(t=10, l=0, r=0, b=0), height=420)

        col_chart, col_table = st.columns([1, 1.4])
        with col_chart:
            st.plotly_chart(fig, width="stretch")
        with col_table:
            st.dataframe(
                dfp.style.format({
                    "PRU (€)": "{:.2f} €", "Cours (€)": "{:.2f} €",
                    "Valeur (€)": "{:,.2f} €", "Poids (%)": "{:.1f}%",
                    "PnL Latent (%)": "{:+.2f}%"})
                .background_gradient(subset=["PnL Latent (%)"], cmap="RdYlGn"),
                width="stretch", height=420, hide_index=True)

# --- Tab: Market Explorer ----------------------------------------------------
with tab_mkt:
    st.markdown(
        "<div class='info-text'>Dynamique des prix sur l'univers. Identifie les "
        "actifs en surchauffe (\u00e0 \u00e9viter) et ceux en forte correction, "
        "candidats potentiels \u00e0 la r\u00e9version \u00e0 la moyenne (le c\u0153ur "
        "de la strat\u00e9gie MRE).</div>",
        unsafe_allow_html=True,
    )

    all_tickers = tuple(universe_df["Ticker"].tolist())
    mode = st.radio("Mode d'intervalle", ["Pr\u00e9r\u00e9glage", "Plage personnalis\u00e9e"],
                    horizontal=True)

    if mode == "Pr\u00e9r\u00e9glage":
        period_map = {"1 Semaine": "5d", "1 Mois": "1mo", "3 Mois": "3mo",
                      "6 Mois": "6mo", "1 An": "1y", "2 Ans": "2y", "5 Ans": "5y"}
        label = st.select_slider("Intervalle d'analyse", list(period_map.keys()),
                                 value="1 Mois")
        perf = get_market_performance(all_tickers, period=period_map[label])
        norm = get_normalized_prices(all_tickers, period_map[label], None, None)
        interval_label = label
    else:
        cA, cB = st.columns(2)
        with cA:
            d_start = st.date_input("D\u00e9but", value=date.today() - timedelta(days=90),
                                    max_value=date.today())
        with cB:
            d_end = st.date_input("Fin", value=date.today(), max_value=date.today())
        perf = get_market_performance(all_tickers, period=None,
                                      start=d_start.isoformat(), end=d_end.isoformat())
        norm = get_normalized_prices(all_tickers, None, d_start.isoformat(),
                                     d_end.isoformat())
        interval_label = f"{d_start.isoformat()} \u2192 {d_end.isoformat()}"

    if perf.empty:
        st.error("Impossible de r\u00e9cup\u00e9rer les donn\u00e9es de march\u00e9 pour cet intervalle.")
    else:
        names = dict(zip(universe_df["Ticker"], universe_df["Name"]))
        best, worst = perf.iloc[0], perf.iloc[-1]
        c1, c2 = st.columns(2)
        with c1:
            st.success(f"\U0001F7E2 **MEILLEURE PERFORMANCE** \u00b7 {interval_label}")
            st.metric(f"{best['Ticker']} \u2014 {names.get(best['Ticker'], '')}",
                      f"{best['Current Price']:.2f} \u20ac",
                      f"{best['Performance (%)']:.2f}%")
        with c2:
            st.error("\U0001F534 **PIRE PERFORMANCE** (candidat Mean-Reversion)")
            st.metric(f"{worst['Ticker']} \u2014 {names.get(worst['Ticker'], '')}",
                      f"{worst['Current Price']:.2f} \u20ac",
                      f"{worst['Performance (%)']:.2f}%")

        bar = px.bar(perf, x="Performance (%)", y="Ticker", orientation="h",
                     color="Performance (%)", color_continuous_scale="RdYlGn",
                     color_continuous_midpoint=0)
        bar.update_layout(template="plotly_dark", paper_bgcolor=_BG,
                          height=max(400, 18 * len(perf)),
                          margin=dict(t=10, l=0, r=0, b=0),
                          yaxis={"categoryorder": "total ascending"})
        st.plotly_chart(bar, width="stretch")

        if not norm.empty:
            movers = list(perf["Ticker"].head(3)) + list(perf["Ticker"].tail(3))
            cols = [c for c in movers if c in norm.columns]
            if cols:
                line = go.Figure()
                for c in cols:
                    line.add_trace(go.Scatter(x=norm.index, y=norm[c], name=c,
                                              mode="lines"))
                line.update_layout(template="plotly_dark", paper_bgcolor=_BG,
                                   title="Top & Flop rebas\u00e9s \u00e0 100",
                                   height=380, margin=dict(t=40, l=0, r=0, b=0))
                st.plotly_chart(line, width="stretch")

        st.markdown("#### Classement complet de l'univers")
        st.dataframe(
            perf.style.format({"Start Price": "{:.2f} €", "Current Price": "{:.2f} €",
                               "Performance (%)": "{:.2f}%"})
            .background_gradient(subset=["Performance (%)"], cmap="RdYlGn"),
            width="stretch", height=460, hide_index=True)

# --- Tab: Full Universe ------------------------------------------------------
with tab_uni:
    st.markdown(
        "<div class='info-text'>L'univers investissable complet, \u00e9ligible PEA "
        "(actions UE/EEE). Chaque signal ne peut porter que sur l'un de ces "
        "titres.</div>",
        unsafe_allow_html=True,
    )
    st.caption(f"{len(universe_df)} titres \u00b7 {universe_df['Sector'].nunique()} secteurs")
    csum = universe_df.groupby("Sector").size().reset_index(name="Nb titres")
    cc1, cc2 = st.columns([1, 2])
    with cc1:
        pie = px.pie(csum, names="Sector", values="Nb titres", hole=0.45,
                     color_discrete_sequence=px.colors.qualitative.Set3)
        pie.update_layout(template="plotly_dark", paper_bgcolor=_BG,
                          height=380, margin=dict(t=10, l=0, r=0, b=0),
                          showlegend=False)
        pie.update_traces(textinfo="label+value")
        st.plotly_chart(pie, width="stretch")
    with cc2:
        sector_filter = st.multiselect("Filtrer par secteur",
                                       sorted(universe_df["Sector"].unique()))
        view = universe_df if not sector_filter else \
            universe_df[universe_df["Sector"].isin(sector_filter)]
        st.dataframe(view.sort_values(["Sector", "Ticker"]),
                     width="stretch", height=380, hide_index=True)

# --- Tab: Signals & Ledger ---------------------------------------------------
with tab_sig:
    st.markdown(
        "<div class='info-text'>Le registre d'audit. Les signaux en attente sont "
        "approuv\u00e9s via Discord. Les signaux ex\u00e9cut\u00e9s ou r\u00e9voqu\u00e9s "
        "sont archiv\u00e9s ici de mani\u00e8re immuable.</div>",
        unsafe_allow_html=True,
    )
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("### \U0001F514 Signaux en Attente")
        pending = load_signals(("PENDING",))
        if pending.empty:
            st.info("Aucun signal en attente d'approbation.")
        else:
            v = pending[["ticker", "signal_type", "score", "reason", "created_at"]]
            st.dataframe(v.style.format({"score": "{:.1f}"})
                         .background_gradient(subset=["score"], cmap="Greens"),
                         width="stretch", hide_index=True)
    with col2:
        st.markdown("### \U0001F4DC Registre Historique (20 derniers)")
        hist = load_signals(("EXECUTED", "REVOKED", "REJECTED", "EXPIRED"), limit=20)
        if hist.empty:
            st.info("Aucun historique disponible.")
        else:
            def _color(v):
                return {"EXECUTED": f"color: {_GREEN}",
                        "REVOKED": f"color: {_RED}",
                        "REJECTED": f"color: {_MUTED}",
                        "EXPIRED": "color: #E3B341"}.get(v, "")
            v = hist[["ticker", "status", "signal_type", "score", "created_at"]]
            st.dataframe(v.style.map(_color, subset=["status"]),
                         width="stretch", hide_index=True)

# --- Tab: Radar & News -------------------------------------------------------
with tab_radar:
    st.markdown(
        "<div class='info-text'>Action des prix en direct (TradingView) + sentiment "
        "macro (actualit\u00e9s). \u00c0 consulter avant d'approuver un trade sur "
        "Discord.</div>",
        unsafe_allow_html=True,
    )
    held = [p.ticker for p in positions]
    options = sorted(set(held) | set(universe_df["Ticker"]))
    default_idx = options.index(held[0]) if held and held[0] in options else 0
    selected = st.selectbox("Actif \u00e0 analyser", options, index=default_idx)
    tv = _tv_symbol(selected)
    st.caption(f"TradingView: `{tv}`")

    # --- Quant readout: local indicators + alpha sensors --------------------
    ind = get_indicators(selected)
    alpha = get_alpha_signals(selected)
    if ind:
        rsi = ind.get("rsi")
        rsi_txt = f"{rsi:.1f}" if rsi is not None else "n/a"
        rsi_state = ("Survendu" if rsi is not None and rsi < 30 else
                     "Surachet\u00e9" if rsi is not None and rsi > 70 else "Neutre")
        trend = "\U0001F7E2 Haussier" if ind.get("sma200") and ind["close"] > ind["sma200"] \
            else "\U0001F534 Baissier"
        k1, k2, k3, k4, k5, k6 = st.columns(6)
        k1.metric("Cours", f"{ind['close']:.2f}", f"{ind['chg_1d']:+.2f}% (1j)")
        k2.metric("RSI(14)", rsi_txt, rsi_state)
        k3.metric("Perf 5j", f"{ind['chg_5d']:+.2f}%")
        k4.metric("Vol. annualis\u00e9e", f"{ind['vol_ann']:.0f}%")
        k5.metric("Tendance LT", trend)
        pc = alpha.get("put_call")
        k6.metric("Put/Call", f"{pc:.2f}" if pc is not None else "n/a",
                  "Peur (contrarian +)" if pc and pc > 1.2 else "Normal")
        sma_parts = []
        if ind.get("sma5"):
            sma_parts.append(f"SMA5 {ind['sma5']:.2f}")
        if ind.get("sma50"):
            sma_parts.append(f"SMA50 {ind['sma50']:.2f}")
        if ind.get("sma200"):
            sma_parts.append(f"SMA200 {ind['sma200']:.2f}")
        detail = " \u00b7 ".join(sma_parts)
        if alpha:
            ins = alpha.get("insider", 0)
            ins_txt = {1: "\U0001F7E2 Achats nets",
                       -1: "\U0001F534 Ventes nettes"}.get(ins, "\u26AA Neutre")
            poly = alpha.get("polymarket")
            detail += f" \u2014 Insiders: {ins_txt}"
            if poly is not None:
                detail += f" \u00b7 Polymarket: {poly:.2f}"
        if detail:
            st.caption(detail)
    else:
        st.caption("Indicateurs indisponibles (donn\u00e9es de march\u00e9 manquantes).")

    col_chart, col_side = st.columns([1.6, 1])
    with col_chart:
        chart_html = f"""
        <div class="tradingview-widget-container" style="height:520px;width:100%">
          <div id="tv_chart" style="height:520px;width:100%"></div>
          <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
          <script type="text/javascript">
            new TradingView.widget({{
              "autosize": true, "symbol": "{tv}", "interval": "D",
              "timezone": "Europe/Paris", "theme": "dark", "style": "1",
              "locale": "fr", "enable_publishing": false,
              "hide_side_toolbar": false, "allow_symbol_change": false,
              "container_id": "tv_chart"
            }});
          </script>
        </div>
        """
        components.html(chart_html, height=540)

    with col_side:
        ta_html = f"""
        <div class="tradingview-widget-container">
          <div class="tradingview-widget-container__widget"></div>
          <script type="text/javascript"
            src="https://s3.tradingview.com/external-embedding/embed-widget-technical-analysis.js" async>
          {{"interval":"1D","width":"100%","isTransparent":true,"height":300,
            "symbol":"{tv}","showIntervalTabs":true,"locale":"fr","colorTheme":"dark"}}
          </script>
        </div>
        """
        components.html(ta_html, height=320)

        st.markdown(f"#### \U0001F4F0 Actualit\u00e9s : {selected}")
        news = get_recent_news(selected)
        if news:
            if st.button("\U0001F9E0 Analyser le sentiment (LLM)", width="stretch"):
                with st.spinner("Analyse LLM en cours..."):
                    score = run_sentiment(selected, [n["title"] for n in news])
                if score is None:
                    st.warning("Sentiment indisponible (cl\u00e9 OPENROUTER_API_KEY ?).")
                else:
                    tone = ("\U0001F7E2 Haussier" if score > 20 else
                            "\U0001F534 Baissier" if score < -20 else "\u26AA Neutre")
                    st.metric("Score de sentiment", f"{score:+.0f}", tone)
                    st.progress(int((score + 100) / 2))
            for n in news:
                meta = " \u00b7 ".join(x for x in (n["provider"], n["date"]) if x)
                st.markdown(
                    f"<div style='background-color:{_CARD}; padding:10px; "
                    f"border-radius:5px; margin-bottom:8px; border-left:3px solid {_BLUE};'>"
                    f"<a href='{n['link']}' target='_blank' style='color:#FFF; "
                    f"text-decoration:none; font-weight:bold;'>{n['title']}</a><br>"
                    f"<span style='color:{_MUTED}; font-size:12px;'>{meta}</span></div>",
                    unsafe_allow_html=True,
                )
        else:
            st.caption("Aucune actualit\u00e9 majeure r\u00e9cente pour cet actif.")

# --- Tab: Monte Carlo Forecast ----------------------------------------------
with tab_mc:
    st.markdown(
        "<div class='info-text'>Moteur stochastique (Geometric Brownian Motion) "
        "projetant 300 trajectoires possibles de l'\u00e9quity sur 1 an, calibr\u00e9 "
        "sur des hypoth\u00e8ses de rendement/volatilit\u00e9. Outil de gestion du "
        "risque \u2014 PAS une pr\u00e9diction.</div>",
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        mc_cagr = st.slider("Rendement annuel espéré (%)", -10.0, 40.0, 15.0, 0.5)
    with c2:
        mc_vol = st.slider("Volatilité annualisée (%)", 5.0, 60.0, 24.0, 0.5)
    with c3:
        mc_days = st.slider("Horizon (jours de bourse)", 63, 504, 252, 21)
    with c4:
        mc_paths = st.slider("Trajectoires", 100, 1000, 300, 50)

    start_equity = float(portfolio.total_equity) if portfolio.total_equity > 0 else 10000.0

    rng = np.random.default_rng(42)
    mu_daily = (mc_cagr / 100.0) / 252.0
    sigma_daily = (mc_vol / 100.0) / np.sqrt(252.0)
    shocks = rng.normal(
        mu_daily - 0.5 * sigma_daily**2, sigma_daily, size=(mc_days, mc_paths)
    )
    paths = start_equity * np.exp(np.cumsum(shocks, axis=0))
    paths = np.vstack([np.full((1, mc_paths), start_equity), paths])

    finals = paths[-1]
    p5, p50, p95 = np.percentile(finals, [5, 50, 95])

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Capital initial", f"{start_equity:,.0f} €")
    m2.metric("Médiane (P50)", f"{p50:,.0f} €",
              f"{(p50 / start_equity - 1) * 100:+.1f}%")
    m3.metric("Pessimiste (P5)", f"{p5:,.0f} €",
              f"{(p5 / start_equity - 1) * 100:+.1f}%")
    m4.metric("Optimiste (P95)", f"{p95:,.0f} €",
              f"{(p95 / start_equity - 1) * 100:+.1f}%")

    x = list(range(mc_days + 1))
    fig_mc = go.Figure()
    for i in range(min(mc_paths, 120)):
        fig_mc.add_trace(go.Scatter(
            x=x, y=paths[:, i], mode="lines",
            line=dict(width=0.5, color="rgba(88,166,255,0.15)"),
            hoverinfo="skip", showlegend=False))
    pct_bands = np.percentile(paths, [5, 50, 95], axis=1)
    for band, name, color in zip(
        pct_bands, ("P5", "Médiane", "P95"), (_RED, "#E3B341", _GREEN)
    ):
        fig_mc.add_trace(go.Scatter(
            x=x, y=band, mode="lines", name=name,
            line=dict(width=2.5, color=color)))
    fig_mc.add_hline(y=start_equity, line_dash="dot", line_color=_MUTED)
    fig_mc.update_layout(
        template="plotly_dark", paper_bgcolor=_BG, plot_bgcolor=_BG,
        height=460, margin=dict(t=20, l=0, r=0, b=0),
        xaxis_title="Jours de bourse", yaxis_title="Équity projetée (€)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    st.plotly_chart(fig_mc, width="stretch")

    prob_loss = float((finals < start_equity).mean() * 100)
    st.caption(
        f"Probabilité de terminer en perte : **{prob_loss:.1f}%** sur "
        f"{mc_paths} trajectoires · hypothèses μ={mc_cagr:.1f}% / σ={mc_vol:.1f}%."
    )


# =============================================================================
# Footer + optional auto-refresh
# =============================================================================
st.write("---")
st.caption(
    "PEA Sniper Terminal V-Prime \u00b7 Zero-leverage \u00b7 Ex\u00e9cution manuelle "
    "via Discord \u00b7 Donn\u00e9es: yfinance / TradingView \u00b7 "
    "Ceci n'est PAS un conseil en investissement."
)

if auto_refresh:
    import time as _time

    _time.sleep(int(refresh_secs))
    st.rerun()
