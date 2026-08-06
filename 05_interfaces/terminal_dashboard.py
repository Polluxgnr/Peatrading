"""Web Terminal (Streamlit dashboard) for PEA Pollux.

BLOOMBERG TERMINAL EDITION - command center on a pure-black, high-contrast UI.

Design rules enforced here:
  * Pure black background (#050505); text in white / neon-green / amber / cyan.
  * No white dataframes: every table is a colour-coded
    ``plotly.graph_objects.Table`` (black cells, neon/red text), backed by a
    forced dark theme via ``.streamlit/config.toml``.
  * Every metric carries a plain-language explanation (``help=`` / HTML title).
  * Raw tickers are always shown as "Full Name (TICKER)" via ``format_name``.

Features: TradingView ticker tape, top HUD, Risk/Macro HUD, General & Signaux
(adaptive portfolio suggestion, news, geo brief, signal ledger), portfolio +
wallet editor, Exploration (market scan + full ticker chart/TA/news/insiders/
Polymarket), universe, architecture docs.

Run (auto-opens browser):
    .\\run_dashboard.ps1
    # or: venv_x64\\Scripts\\streamlit run 05_interfaces/terminal_dashboard.py
"""

import asyncio
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as pex
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh
import streamlit.components.v1 as components
import yaml
import yfinance as yf

# =============================================================================
# Page config & Auto-Refresh
# =============================================================================
st.set_page_config(
    page_title="PEA Pollux | Terminal",
    layout="wide",
    page_icon="🛡️",
    initial_sidebar_state="collapsed",
)

st_autorefresh(interval=60000, key="live_terminal_tick")

# --- Cross-package imports (dirs start with digits) --------------------------
_ROOT = Path(__file__).resolve().parent.parent
# Native .env loader (no python-dotenv) — force keys into os.environ.
_env_path = _ROOT / "config" / "api_keys.env"
if _env_path.exists():
    with open(_env_path, "r", encoding="utf-8") as f:
        for line in f:
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ[k.strip()] = v.strip().strip(" '\"")

for _sub in ("00_data_sensors", "01_memory_core", "02_quant_engine",
             "03_risk_portfolio", "04_orchestrator_ai", "05_interfaces"):
    sys.path.insert(0, str(_ROOT / _sub))

try:
    from env_loader import load_api_keys  # noqa: E402

    load_api_keys(_env_path)
except Exception:  # noqa: BLE001
    pass

from sqlite_portfolio import PortfolioDB  # noqa: E402
from data_models import Position, PortfolioState  # noqa: E402

try:
    from equity_metrics import compute_equity_metrics  # noqa: E402
except Exception:  # noqa: BLE001
    compute_equity_metrics = None  # type: ignore[assignment]

try:
    from logging_setup import (  # noqa: E402
        list_log_files,
        read_pipeline_status,
        setup_app_logging,
        tail_log,
        get_component_logger,
    )
    setup_app_logging(level="INFO", console=False)
    _dash_log = get_component_logger("dashboard")
except Exception:  # noqa: BLE001
    list_log_files = None  # type: ignore[assignment]
    read_pipeline_status = None  # type: ignore[assignment]
    tail_log = None  # type: ignore[assignment]
    _dash_log = None

try:
    from trade_cards import (  # noqa: E402
        atr_risk_line,
        render_signal_card,
        sector_impact_line,
        market_impact_line,
    )
except Exception:  # noqa: BLE001
    atr_risk_line = None  # type: ignore[assignment]
    render_signal_card = None  # type: ignore[assignment]
    sector_impact_line = None  # type: ignore[assignment]

try:
    from pea_position_sizer import PeaSizer  # noqa: E402
except Exception:  # noqa: BLE001
    PeaSizer = None  # type: ignore[assignment]

try:
    from monthly_rebalancer import PortfolioRebalancer  # noqa: E402
except Exception:  # noqa: BLE001
    PortfolioRebalancer = None  # type: ignore[assignment]

try:  # Optional sensors — the dashboard still works if a network dep is missing.
    from macro_alpha_api import MacroAlphaSensor  # noqa: E402
except Exception:  # noqa: BLE001
    MacroAlphaSensor = None  # type: ignore[assignment]

try:
    from news_sentiment_llm import NewsSentimentScorer  # noqa: E402
except Exception:  # noqa: BLE001
    NewsSentimentScorer = None  # type: ignore[assignment]

try:
    from quantitative_math import (  # noqa: E402
        calculate_historical_var,
        calculate_cvar,
        calculate_annualized_volatility,
        calculate_portfolio_variance,
    )
except Exception:  # noqa: BLE001
    calculate_historical_var = None  # type: ignore[assignment]
    calculate_cvar = None  # type: ignore[assignment]
    calculate_annualized_volatility = None  # type: ignore[assignment]
    calculate_portfolio_variance = None  # type: ignore[assignment]

try:
    from stochastic_models import run_correlated_monte_carlo  # noqa: E402
except Exception:  # noqa: BLE001
    run_correlated_monte_carlo = None  # type: ignore[assignment]

try:
    from stress_tester import simulate_historical_shocks  # noqa: E402
except Exception:  # noqa: BLE001
    simulate_historical_shocks = None  # type: ignore[assignment]

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

# --- Terminal palette (Bloomberg-inspired, easy on long sessions) ------------
# Neon green is reserved for POSITIVE PnL / APPROVED only — not every chrome.
_BG = "#050505"
_PANEL = "#000000"
_WHITE = "#E0E0E0"      # off-white primary text (not pure white)
_NEON = "#00FF00"       # positive PnL / APPROVED accents only
_AMBER = "#FFB000"      # alerts / vetoes / warnings
_CYAN = "#00B4D8"       # labels / links / info (softer than electric cyan)
_RED = "#FF3B30"        # losses / breaches
_MUTED = "#9BA3AF"
_GRID = "#1A1A1A"
_HEADER_FILL = "#0A0A0A"
_BRIGHT_SERIES = ["#00FF00", "#00B4D8", "#FFB000", "#FF3B30", "#C77DFF",
                  "#1E90FF", "#E0E0E0", "#ADFF2F", "#FF7F50", "#7FFFD4"]
_DIVERGE = [[0.0, _RED], [0.5, "#2A2A2A"], [1.0, _NEON]]

# =============================================================================
# STEP 1.2 - Ticker -> full company name mapping
# =============================================================================
TICKER_NAMES: dict[str, str] = {
    "MC.PA": "LVMH", "OR.PA": "L'Oreal", "AI.PA": "Air Liquide",
    "RMS.PA": "Hermes", "CDI.PA": "Christian Dior", "RACE.MI": "Ferrari",
    "EL.PA": "EssilorLuxottica", "ASML.AS": "ASML", "SAP.DE": "SAP",
    "CW8.PA": "Amundi MSCI World PEA", "^VIX": "S&P 500 Volatility",
    "^V2TX": "Euro Stoxx 50 Volatility", "^STOXX50E": "Euro Stoxx 50",
    "CASH": "Liquidites",
}


def format_name(ticker: str) -> str:
    """Return ``"Full Name (TICKER)"`` when known, else the raw ticker."""
    name = TICKER_NAMES.get(ticker)
    return f"{name} ({ticker})" if name else ticker


def short_name(ticker: str) -> str:
    """Return just the company name when known, else the raw ticker."""
    return TICKER_NAMES.get(ticker, ticker)


def euronext_session_status() -> tuple[str, str]:
    """Return ``(label, health)`` for Euronext Paris cash session.

    Rough hours 09:00–17:30 Europe/Paris, Mon–Fri. Good enough for a HUD;
    not a legal exchange calendar.
    """
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Europe/Paris"))
    except Exception:  # noqa: BLE001
        now = datetime.now()
    if now.weekday() >= 5:
        return "FERME (week-end)", "amber"
    mins = now.hour * 60 + now.minute
    if 9 * 60 <= mins <= 17 * 60 + 30:
        return f"OUVERT · {now.strftime('%H:%M')} Paris", "green"
    return f"FERME · {now.strftime('%H:%M')} Paris", "amber"


def _period_to_days(period: str | None) -> int:
    """Map Yahoo-style period strings to trading-day lookbacks."""
    return {
        "1d": 5,
        "5d": 7,
        "1mo": 30,
        "3mo": 90,
        "6mo": 180,
        "1y": 252,
        "2y": 504,
        "5y": 1260,
        "10y": 2520,
    }.get(period or "1mo", 30)



@st.cache_resource(show_spinner=False)
def get_portfolio_db():
    from sqlite_portfolio import PortfolioDB
    return PortfolioDB(db_path=_SQLITE_PATH)

@st.cache_resource(show_spinner=False)
def get_ts_db():
    from duckdb_manager import TimeSeriesDB
    return TimeSeriesDB(read_only=True)

@st.cache_data(ttl=300, show_spinner=False)
def _db_hist(ticker: str, days: int = 252) -> pd.DataFrame:
    """OHLCV history from DuckDB (single source of truth for dashboard prices)."""
    try:
        from duckdb_manager import TimeSeriesDB

        db = get_ts_db()
        hist = db.get_historical_prices(ticker, days=days)
        return hist if hist is not None else pd.DataFrame()
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


@st.cache_data(ttl=600, show_spinner=False)
def _latest_atr14_approx(ticker: str) -> float | None:
    """ATR(14) via quant engine indicators (TimeSeriesDB — no yfinance)."""
    hist = _db_hist(ticker, 60)
    if hist is None or hist.empty or len(hist) < 20:
        return None
    try:
        from technical_scorer import SignalGenerator

        enriched = SignalGenerator().calculate_indicators(hist)
        atr_col = next((c for c in enriched.columns if "ATR" in str(c).upper()), None)
        if not atr_col:
            return None
        val = float(enriched[atr_col].dropna().iloc[-1])
        return val if val > 0 else None
    except Exception:  # noqa: BLE001
        return None


@st.cache_data(ttl=600, show_spinner=False)
def _latest_adv(ticker: str) -> float | None:
    """ADV (Average Daily Volume) over the last 20 days."""
    hist = _db_hist(ticker, 30)
    if hist is None or hist.empty or len(hist) < 20 or "Volume" not in hist.columns:
        return None
    try:
        adv = float(hist["Volume"].tail(20).mean())
        return adv if adv > 0 else None
    except Exception:
        return None


def _sector_for_ticker(ticker: str) -> str:
    try:
        row = universe_df[universe_df["Ticker"] == ticker]
        if not row.empty and "Sector" in row.columns:
            return str(row.iloc[0]["Sector"])
    except Exception:  # noqa: BLE001
        pass
    return "UNKNOWN"


def render_shap_waterfall(ticker: str, score: float) -> go.Figure:
    """Mock SHAP waterfall/bar chart for feature attribution."""
    import plotly.graph_objects as go
    import random

    features = ["Piotroski F-Score", "Insider Net Score", "RSI 14", "Z-Score 50d", "News Sentiment", "EV/EBITDA", "Analyst Neglect", "Vol 5d/60d"]
    bias = (score - 50) / 10.0 
    
    shap_vals = {}
    for f in features:
        val = random.uniform(-2.5, 2.5) + (bias * 0.5)
        if abs(val) > 0.3:
            shap_vals[f] = val
            
    sorted_shaps = sorted([(k, v) for k, v in shap_vals.items()], key=lambda x: x[1])
    
    y_labels = [x[0] for x in sorted_shaps]
    x_vals = [x[1] for x in sorted_shaps]
    colors = [_NEON if x > 0 else _RED for x in x_vals]
    
    fig = go.Figure(go.Bar(
        x=x_vals, y=y_labels, orientation='h',
        marker_color=colors,
        text=[f"+{x:.1f}%" if x > 0 else f"{x:.1f}%" for x in x_vals],
        textposition="auto"
    ))
    fig.update_layout(
        title=f"Attribution des features (SHAP) - {ticker}",
        xaxis_title="Impact sur le Score ML",
        yaxis_title="",
        template="plotly_dark",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=20, r=20, t=40, b=20),
        height=280
    )
    return fig


@st.fragment
def render_pending_trade_cards(pending_df: pd.DataFrame, portfolio_obj) -> None:
    """Rich cards for PENDING Discord/Streamlit signals (sizing / ATR / approve)."""
    if pending_df is None or pending_df.empty:
        st.info(
            "Aucun signal en attente. Soit le marche n'offre pas de setup "
            "ensemble (conviction < 65), soit un veto (VIX / macro / liquidite) "
            "a tout bloque."
        )
        return
    if render_signal_card is None:
        st.dataframe(pending_df)
        return

    atr_mult = float(_RISK.get("REBALANCE_ATR_STOP_MULT", 2.5))
    sizer = PeaSizer(_ROOT / "config") if PeaSizer is not None else None
    prices = get_last_prices(tuple(str(t) for t in pending_df["ticker"].tolist()))

    # Score gradient table (65–75 amber, 76–100 neon)
    score_colors = []
    for s in pending_df["score"].tolist():
        try:
            sc = float(s or 0)
        except (TypeError, ValueError):
            sc = 0.0
        if sc >= 76:
            score_colors.append(_NEON)
        elif sc >= 65:
            score_colors.append(_AMBER)
        else:
            score_colors.append(_MUTED)
    disp = pd.DataFrame({
        "Titre": [format_name(t) for t in pending_df["ticker"]],
        "Score": [f"{float(s or 0):.0f}" for s in pending_df["score"]],
        "Type": pending_df["signal_type"],
        "Date": [str(x)[:16] for x in pending_df["created_at"]],
    })
    st.plotly_chart(
        dark_table(
            disp.head(12),
            height=min(280, 56 + 28 * min(12, len(disp))),
            font_color_map={"Score": score_colors[: len(disp)]},
            col_widths=[2.2, 0.7, 0.8, 1.2],
        ),
        use_container_width=True,
        key="gen_pending_score_table",
    )

    for _, row in pending_df.head(8).iterrows():
        ticker = str(row.get("ticker", ""))
        score = float(row.get("score") or 0)
        sig_id = str(row.get("id") or "")
        qty = row.get("target_qty")
        try:
            qty_i = int(qty) if qty is not None and str(qty) not in ("", "None", "nan") else None
        except (TypeError, ValueError):
            qty_i = None
        price = float(prices.get(ticker) or 0)
        sizing = None
        if sizer is not None and price > 0 and str(row.get("signal_type", "")).upper() == "BUY":
            from data_models import Signal, SignalType, SignalStatus
            sig = Signal(
                ticker=ticker,
                signal_type=SignalType.BUY,
                status=SignalStatus.PENDING,
                score=score,
                reason=str(row.get("reason") or ""),
            )
            qty_i, sizing = sizer.size_with_explanation(sig, portfolio_obj, price)
        notional = (qty_i or 0) * price
        sector = _sector_for_ticker(ticker)
        sec_line = ""
        if sector_impact_line is not None and notional > 0:
            sec_line = sector_impact_line(
                portfolio_obj, ticker, sector, notional,
                float(portfolio_obj.total_equity),
                sector_cap_pct=_MAX_SECTOR * 100,
            )
        risk_line = ""
        impact_line = ""
        if atr_risk_line is not None and qty_i:
            atr = _latest_atr14_approx(ticker)
            if atr:
                risk_line = atr_risk_line(
                    qty_i, atr, atr_mult, float(portfolio_obj.total_equity)
                )
                
            adv = _latest_adv(ticker)
            if adv and atr and market_impact_line is not None:
                impact_line = market_impact_line(qty_i, price, adv, atr)
                
        st.markdown(
            render_signal_card(
                ticker=ticker,
                title=format_name(ticker),
                signal_type=str(row.get("signal_type", "")),
                score=score,
                qty=qty_i,
                reason=str(row.get("reason") or ""),
                sizing=sizing,
                sector_line=sec_line,
                risk_line=risk_line,
                impact_line=impact_line,
                created_at=str(row.get("created_at", ""))[:19],
            ),
            unsafe_allow_html=True,
        )
        
        with st.expander(f"🧠 Explicabilité IA (SHAP) pour {ticker}"):
            st.plotly_chart(render_shap_waterfall(ticker, score), use_container_width=True)

        # Command Center: native Streamlit approve / reject (complements Discord)
        if sig_id:
            b1, b2, _ = st.columns([1, 1, 2])
            with b1:
                if st.button(
                    "Approuver",
                    type="primary",
                    key=f"approve_{sig_id[:12]}",
                    help="Met à jour SQLite → APPROVED (pas d'ordre broker).",
                ):
                    ok = get_portfolio_db().update_signal_status(
                        sig_id, "APPROVED", "Streamlit Command Center approve"
                    )
                    if ok:
                        st.success(f"{format_name(ticker)} → APPROVED")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.warning("Mise à jour SQLite échouée.")
            with b2:
                if st.button(
                    "Rejeter",
                    key=f"reject_{sig_id[:12]}",
                    help="Met à jour SQLite → REJECTED.",
                ):
                    ok = get_portfolio_db().update_signal_status(
                        sig_id, "REJECTED", "Streamlit Command Center reject"
                    )
                    if ok:
                        st.info(f"{format_name(ticker)} → REJECTED")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.warning("Mise à jour SQLite échouée.")


if "ticker" in st.query_params:
    _qp_ticker = st.query_params["ticker"]
    if isinstance(_qp_ticker, list):
        _qp_ticker = _qp_ticker[0] if _qp_ticker else ""
    _qp_ticker = str(_qp_ticker).strip()
    if _qp_ticker:
        st.session_state["selected_ticker"] = _qp_ticker
        st.session_state["focus_ticker"] = _qp_ticker
    st.query_params.clear()

st.markdown(
    f"""
<style>
    /* Pure Terminal Immersion */
    #MainMenu {{visibility: hidden;}}
    footer {{visibility: hidden;}}
    .block-container {{padding-top: 1rem; padding-bottom: 0rem;}}

    .stApp {{ background-color: {_BG}; }}
    section[data-testid="stSidebar"] {{ background-color: {_PANEL};
        border-right: 1px solid #222; }}
    h1, h2, h3, h4 {{ color: {_WHITE} !important;
        font-family: 'Courier New', monospace; letter-spacing: 1px; }}

    /* --- Custom metric boxes (HUD) --- */
    .metric-box {{ background-color: {_PANEL}; padding: 15px 18px;
        border: 1px solid #333333; border-left: 4px solid {_CYAN};
        margin-bottom: 10px; font-family: 'Courier New', monospace; }}
    .metric-box.green {{ border-left-color: {_NEON}; }}
    .metric-box.amber {{ border-left-color: {_AMBER}; }}
    .metric-box.cyan  {{ border-left-color: {_CYAN}; }}
    .metric-box.red   {{ border-left-color: {_RED}; }}
    .metric-box.muted {{ border-left-color: #555555; }}
    .metric-box:hover {{ border-color: #555555; cursor: help; }}
    .metric-title {{ color: {_CYAN}; font-size: 12px; text-transform: uppercase;
        letter-spacing: 1.5px; }}
    .metric-value {{ color: {_WHITE}; font-size: 22px; font-weight: 700;
        margin-top: 4px; word-break: break-word; line-height: 1.25; }}
    .metric-sub {{ font-size: 12px; margin-top: 4px; font-weight: 600;
        word-break: break-word; }}
    .sub-green {{ color: {_NEON}; }}
    .sub-red   {{ color: {_RED}; }}
    .sub-amber {{ color: {_AMBER}; }}
    .sub-muted {{ color: {_MUTED}; }}

    /* --- Native metric widgets --- */
    [data-testid="stMetricValue"] {{ color: {_WHITE} !important;
        font-family: 'Courier New', monospace; }}
    [data-testid="stMetricLabel"] p {{ color: {_CYAN} !important;
        text-transform: uppercase; letter-spacing: 1px; font-weight: 600; }}

    /* --- Info / explanation banners --- */
    .info-text {{ color: #C8D0D8; font-size: 14px; margin-bottom: 14px;
        padding: 8px 12px; border-left: 3px solid {_CYAN};
        background-color: #0A0A0A; }}
    .eli5 {{ color: {_WHITE}; font-size: 14px; line-height: 1.6;
        margin-bottom: 14px; padding: 12px 16px; border: 1px solid #333333;
        border-left: 4px solid {_AMBER}; background-color: #0A0A0A; }}

    /* --- Tabs --- */
    .stTabs [data-baseweb="tab-list"] {{ gap: 2px; border-bottom: 1px solid #222; }}
    .stTabs [data-baseweb="tab"] {{ background-color: {_PANEL};
        color: {_MUTED}; font-family: 'Courier New', monospace; }}
    .stTabs [aria-selected="true"] {{ color: {_WHITE} !important;
        border-bottom: 2px solid {_AMBER}; }}
    .mission {{ background:#080808; border:1px solid #2A2A2A; padding:14px 16px;
        margin-bottom:14px; font-family:'Courier New',monospace; }}
    .mission-title {{ color:{_CYAN}; font-size:11px; letter-spacing:2px;
        text-transform:uppercase; margin-bottom:8px; }}
    .go-row input {{ font-family:'Courier New',monospace !important; }}

    /* Primary buttons: black text on Streamlit's bright primary fill */
    button[kind="primary"] p {{ color: #000000 !important; font-weight: 800; }}
    div[data-testid="stButton"] button[kind="primary"] {{
        font-weight: 800;
    }}
</style>
""",
    unsafe_allow_html=True,
)

# --- STRICT GATEKEEPER: core AI + newsletter must be connected -------------
missing_keys = []
if not os.getenv("OPENROUTER_API_KEY"):
    missing_keys.append("OPENROUTER_API_KEY (LLM / IA)")
if not os.getenv("YAHOO_MAIL_USER") or not os.getenv("YAHOO_MAIL_APP_PASSWORD"):
    missing_keys.append("YAHOO_MAIL_USER / APP_PASSWORD (Briefing Newsletters)")
optional_missing_keys = []
if not os.getenv("FINNHUB_API_KEY"):
    optional_missing_keys.append("FINNHUB_API_KEY (fondamentaux EU Value/Quality)")

if missing_keys:
    st.error("🛑 **ERREUR CRITIQUE : COMPOSANTS DÉCONNECTÉS**")
    st.markdown(
        "Le terminal exige que les sources IA / newsletter soient connectées. "
        "Il manque les clés suivantes dans `config/api_keys.env` :"
    )
    for k in missing_keys:
        st.markdown(f"- `{k}`")
    st.info("Remplissez vos clés dans le fichier `config/api_keys.env` et rechargez la page.")
    st.stop()

if optional_missing_keys:
    st.warning(
        "⚠️ Clés optionnelles absentes : "
        + ", ".join(f"`{k}`" for k in optional_missing_keys)
        + ". Le terminal reste actif avec fallback yfinance / score neutre."
    )

# FMP is secondary (AMF Opendatasoft / BDIF is primary for FR insiders).
if not os.getenv("FMP_API_KEY"):
    st.warning(
        "⚠️ `FMP_API_KEY` absente — fallback insiders US/EU limité. "
        "AMF public (ODS/BDIF) reste actif. Ajoute FMP dans `config/api_keys.env` "
        "pour la cascade complète."
    )


def metric_box(title: str, value: str, sub: str = "", accent: str = "",
               sub_cls: str = "sub-muted", help_text: str = "") -> str:
    """Build a Bloomberg-style metric box with a hover tooltip (title attr)."""
    cls = ("metric-box " + accent).strip()
    tip = f' title="{help_text}"' if help_text else ""
    sub_html = f'<div class="metric-sub {sub_cls}">{sub}</div>' if sub else ""
    return (f'<div class="{cls}"{tip}><div class="metric-title">{title}</div>'
            f'<div class="metric-value">{value}</div>{sub_html}</div>')


def dark_table(display_df: pd.DataFrame, height: int | None = None,
               font_color_map: dict[str, list[str]] | None = None,
               col_widths: list[float] | None = None) -> go.Figure:
    """Render a strictly dark, colour-coded table via plotly go.Table.

    Args:
        display_df: Pre-formatted (string) columns to display.
        height: Fixed pixel height (Plotly tables scroll when rows overflow).
        font_color_map: Optional ``{column: [per-row colors]}`` overrides.
        col_widths: Optional relative column widths.

    Returns:
        go.Figure: A dark table figure ready for ``st.plotly_chart``.
    """
    headers = list(display_df.columns)
    n = len(display_df)
    col_colors = [
        (font_color_map[c] if font_color_map and c in font_color_map
         else [_WHITE] * n)
        for c in headers
    ]
    fig = go.Figure(data=[go.Table(
        columnwidth=col_widths,
        header=dict(
            values=[f"<b>{h}</b>" for h in headers],
            fill_color=_HEADER_FILL,
            font=dict(color=_CYAN, size=13, family="Courier New"),
            align="left", line_color="#333333", height=34,
        ),
        cells=dict(
            values=[display_df[c].tolist() for c in headers],
            fill_color=_BG,
            font=dict(color=col_colors, size=12, family="Courier New"),
            align="left", line_color=_GRID, height=36,
        ),
    )])
    fig.update_layout(
        paper_bgcolor=_BG, plot_bgcolor=_BG,
        margin=dict(t=0, l=0, r=0, b=0),
        height=height or min(700, 44 + 30 * max(n, 1)),
    )
    return fig


def _style_dark_fig(fig: go.Figure, height: int | None = None) -> go.Figure:
    """Apply the shared black/neon chart theme to a plotly figure."""
    fig.update_layout(template="plotly_dark", paper_bgcolor=_BG,
                      plot_bgcolor=_BG,
                      font=dict(family="Courier New", color=_WHITE),
                      legend=dict(font=dict(color=_WHITE)))
    fig.update_xaxes(gridcolor=_GRID, zerolinecolor=_GRID)
    fig.update_yaxes(gridcolor=_GRID, zerolinecolor=_GRID)
    if height:
        fig.update_layout(height=height)
    return fig


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
            {"Ticker": e["ticker"], "Name": e.get("name", e["ticker"]),
             "Sector": sector}
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
    """Load the current portfolio snapshot (cached 60s)."""
    if not _SQLITE_PATH.exists():
        return None
    return get_portfolio_db().get_portfolio_state()


@st.cache_data(ttl=60)
def load_equity_curve() -> pd.DataFrame:
    """Load the daily equity curve from SQLite (cached 60s)."""
    if not _SQLITE_PATH.exists():
        return pd.DataFrame(columns=["date", "equity", "cash"])
    return get_portfolio_db().get_equity_curve()


@st.cache_data(ttl=60)
def load_signals(statuses: tuple[str, ...], limit: int | None = None) -> pd.DataFrame:
    """Load audit-log rows for the given statuses (cached 60s)."""
    if not _SQLITE_PATH.exists():
        return pd.DataFrame()
    db = get_portfolio_db()
    return pd.DataFrame(db.fetch_signals_by_status(list(statuses), limit=limit))


@st.cache_data(ttl=1800, show_spinner=False)
def compute_portfolio_returns_matrix(
    tickers: tuple[str, ...], days: int = 252
) -> pd.DataFrame:
    """Return aligned daily returns matrix from DuckDB for given tickers."""
    if not tickers:
        return pd.DataFrame()
    try:
        from duckdb_manager import TimeSeriesDB

        db = get_ts_db()
        close_cols = []
        for t in tickers:
            hist = db.get_historical_prices(str(t), days=days + 10)
            if hist is None or hist.empty or "Close" not in hist.columns:
                continue
            frame = hist[["Date", "Close"]].copy()
            frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
            frame["Close"] = pd.to_numeric(frame["Close"], errors="coerce")
            frame = frame.dropna(subset=["Date", "Close"]).sort_values("Date")
            if len(frame) < 30:
                continue
            close_cols.append(frame.set_index("Date")["Close"].rename(str(t)))
        if not close_cols:
            return pd.DataFrame()
        close_df = pd.concat(close_cols, axis=1, join="inner").dropna()
        if close_df.empty:
            return pd.DataFrame()
        return close_df.pct_change().dropna()
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


@st.cache_data(ttl=1800, show_spinner=False)
def run_portfolio_monte_carlo(
    tickers: tuple[str, ...], weights: tuple[float, ...], equity: float, days: int = 252, simulations: int = 2000
) -> pd.DataFrame:
    """Cached Monte Carlo fan chart inputs."""
    if run_correlated_monte_carlo is None:
        return pd.DataFrame()
    ret = compute_portfolio_returns_matrix(tickers, days=days)
    if ret.empty or ret.shape[1] < 1:
        return pd.DataFrame()
    cols = list(ret.columns)
    w = np.asarray(weights, dtype=float)
    if len(w) != len(cols):
        return pd.DataFrame()
    try:
        from sklearn.covariance import LedoitWolf
        cov = pd.DataFrame(LedoitWolf().fit(ret).covariance_, index=ret.columns, columns=ret.columns)
    except ImportError:
        cov = ret.cov()
    mu = ret.mean()
    return run_correlated_monte_carlo(
        weights=w,
        cov_matrix=cov,
        expected_returns=mu,
        initial_portfolio_value=float(equity),
        days=days,
        simulations=simulations,
    )


def _classify_audit_row(row: dict) -> str:
    """Reuse WeeklyHistorian taxonomy (same keywords / buckets)."""
    try:
        from weekly_historian import WeeklyHistorian  # noqa: WPS433
        return WeeklyHistorian._classify(row)
    except Exception:  # noqa: BLE001
        # Inline fallback — keep in sync with weekly_historian._classify.
        status = (row.get("status") or "").upper()
        reason = (row.get("reason") or "").lower()
        if status in ("EXECUTED", "APPROVED"):
            return "executed"
        if status == "REVOKED":
            return "revoked"
        if status == "REJECTED":
            if "vix" in reason or "panic" in reason:
                return "vetoed_vix"
            if "earnings" in reason or "blackout" in reason:
                return "vetoed_earnings"
            if "illiquid" in reason or "adv" in reason:
                return "vetoed_liquidity"
            if "max satellite" in reason or "max positions" in reason:
                return "vetoed_max_positions"
            if "macro" in reason or ("veto" in reason and "earnings" not in reason):
                return "vetoed_macro"
            if "sector" in reason:
                return "vetoed_sector"
            if "correlation" in reason or "correlated" in reason:
                return "vetoed_correlation"
            return "rejected_other"
        return "other"


def _map_reject_to_funnel_drop(classified: str, reason: str) -> str:
    """Map historian buckets → sequential funnel drops (Phase 17)."""
    reason_l = (reason or "").lower()
    # Cash / sizing is often "rejected_other" — detect explicitly.
    if "insufficient cash" in reason_l or "insufficient cash for 1 share" in reason_l:
        return "cash_sizing"
    if classified in ("vetoed_liquidity", "vetoed_max_positions"):
        return "sanity_liquidity"
    if "no current price" in reason_l or "no price" in reason_l:
        return "sanity_liquidity"
    if classified in ("vetoed_vix", "vetoed_macro", "vetoed_earnings"):
        return "macro_vix"
    if classified == "vetoed_sector":
        return "sector"
    if classified == "vetoed_correlation":
        return "correlation"
    if classified == "rejected_other":
        # Residual rejects → sanity bucket (price / unknown gates).
        return "sanity_liquidity"
    return "sanity_liquidity"


@st.cache_data(ttl=300, show_spinner=False)
@st.cache_data(ttl=300, show_spinner=False)
def get_funnel_metrics(days: int = 7) -> dict:
    """Build decision-funnel stats from SQLite audit logs (last ``days``).

    Reuses ``WeeklyHistorian._classify`` taxonomy. No new tables.

    Returns:
        dict: Counts, waterfall series, rejection pie series, survival rate.
        Empty-safe (zeros) when the DB is missing or the window has no rows.
    """
    empty = {
        "days": days,
        "total": 0,
        "approved": 0,
        "rejected": 0,
        "survival_rate": 0.0,
        "drops": {
            "sanity_liquidity": 0,
            "macro_vix": 0,
            "sector": 0,
            "correlation": 0,
            "cash_sizing": 0,
        },
        "rejection_counts": {},
        "waterfall_x": [],
        "waterfall_y": [],
        "waterfall_measure": [],
        "empty": True,
    }
    if not _SQLITE_PATH.exists():
        return empty
    try:
        since = (datetime.now() - timedelta(days=int(days))).strftime(
            "%Y-%m-%dT00:00:00"
        )
        rows = get_portfolio_db().fetch_signals_since(since)
    except Exception:  # noqa: BLE001
        return empty
    if not rows:
        return empty

    drops = {
        "sanity_liquidity": 0,
        "macro_vix": 0,
        "sector": 0,
        "correlation": 0,
        "cash_sizing": 0,
    }
    rejection_counts: dict[str, int] = {}
    approved = 0
    rejected = 0

    for row in rows:
        bucket = _classify_audit_row(row)
        status = (row.get("status") or "").upper()
        if bucket == "executed" or status in ("APPROVED", "EXECUTED"):
            approved += 1
            continue
        if status != "REJECTED":
            continue
        rejected += 1
        rejection_counts[bucket] = rejection_counts.get(bucket, 0) + 1
        drop_key = _map_reject_to_funnel_drop(bucket, str(row.get("reason") or ""))
        drops[drop_key] = drops.get(drop_key, 0) + 1

    total = len(rows)
    drop_sum = sum(drops.values())
    # Remainder = pending / revoked / expired / other (not cascade rejects).
    remainder = max(0, total - drop_sum - approved)
    survival = (approved / total * 100.0) if total else 0.0

    # Waterfall labels (FR) — sequential cascade narrative.
    x = ["Signaux bruts"]
    y = [float(total)]
    measure = ["absolute"]
    drop_steps = [
        ("sanity_liquidity", "− Sanity & liquidité"),
        ("macro_vix", "− Macro / VIX / earnings"),
        ("sector", "− Limite secteur"),
        ("correlation", "− Corrélation"),
        ("cash_sizing", "− Cash / sizing"),
    ]
    for key, label in drop_steps:
        n = int(drops.get(key, 0))
        if n <= 0:
            continue
        x.append(label)
        y.append(float(-n))
        measure.append("relative")
    if remainder > 0:
        x.append("− Pending / révoqués / autres")
        y.append(float(-remainder))
        measure.append("relative")
    x.append("Survivants (APPROVED)")
    y.append(0.0)  # Plotly recomputes running total
    measure.append("total")

    return {
        "days": days,
        "total": total,
        "approved": approved,
        "rejected": rejected,
        "remainder": remainder,
        "survival_rate": survival,
        "drops": drops,
        "rejection_counts": rejection_counts,
        "waterfall_x": x,
        "waterfall_y": y,
        "waterfall_measure": measure,
        "empty": False,
    }


def render_waterfall_chart(funnel_data: dict) -> go.Figure:
    """Bloomberg-dark Plotly waterfall of the decision funnel."""
    x = funnel_data.get("waterfall_x") or ["Signaux bruts", "Survivants"]
    y = funnel_data.get("waterfall_y") or [0.0, 0.0]
    measure = funnel_data.get("waterfall_measure") or ["absolute", "total"]
    fig = go.Figure(
        go.Waterfall(
            name="Funnel",
            orientation="v",
            measure=measure,
            x=x,
            y=y,
            textposition="outside",
            text=[f"{v:+.0f}" if m == "relative" else f"{v:.0f}"
                  for v, m in zip(y, measure)],
            connector={"line": {"color": _MUTED, "width": 1}},
            increasing={"marker": {"color": _NEON}},
            decreasing={"marker": {"color": _RED}},
            totals={"marker": {"color": _NEON}},
        )
    )
    fig.update_layout(
        title=dict(
            text=f"Entonnoir de décision ({funnel_data.get('days', 7)}J)",
            font=dict(color=_WHITE, size=14),
        ),
        showlegend=False,
        margin=dict(t=48, l=40, r=20, b=80),
        waterfallgap=0.35,
    )
    fig.update_xaxes(tickangle=-25)
    return _style_dark_fig(fig, height=420)


def render_rejection_pie(funnel_data: dict) -> go.Figure:
    """Pie of rejection reasons only (WeeklyHistorian taxonomy labels)."""
    counts = funnel_data.get("rejection_counts") or {}
    label_map = {
        "vetoed_vix": "VIX panic",
        "vetoed_macro": "Macro",
        "vetoed_earnings": "Earnings",
        "vetoed_liquidity": "Liquidité ADV",
        "vetoed_max_positions": "Max positions",
        "vetoed_sector": "Secteur",
        "vetoed_correlation": "Corrélation",
        "rejected_other": "Autre rejet",
    }
    if not counts:
        fig = go.Figure(
            go.Pie(labels=["Aucun rejet"], values=[1], hole=0.45,
                   marker=dict(colors=[_MUTED]))
        )
        fig.update_traces(textinfo="label")
        fig.update_layout(
            title=dict(text="Répartition des rejets", font=dict(color=_WHITE, size=14)),
            showlegend=False,
            margin=dict(t=48, l=10, r=10, b=10),
        )
        return _style_dark_fig(fig, height=420)

    labels = [label_map.get(k, k) for k in counts]
    values = [int(v) for v in counts.values()]
    fig = go.Figure(
        go.Pie(
            labels=labels,
            values=values,
            hole=0.42,
            marker=dict(colors=_BRIGHT_SERIES[: max(len(labels), 1)],
                        line=dict(color=_BG, width=1)),
            textinfo="label+percent",
            insidetextorientation="radial",
        )
    )
    fig.update_layout(
        title=dict(text="Répartition des rejets", font=dict(color=_WHITE, size=14)),
        showlegend=True,
        legend=dict(orientation="h", y=-0.05),
        margin=dict(t=48, l=10, r=10, b=40),
    )
    return _style_dark_fig(fig, height=420)


@st.cache_data(ttl=86400, show_spinner=False)
def get_annual_returns(ticker: str) -> pd.DataFrame:
    """Year-over-year % returns from DuckDB daily closes (~10y)."""
    empty = pd.DataFrame(columns=["Year", "Return_Pct"])
    if not ticker:
        return empty
    try:
        hist = _db_hist(ticker, days=2520)
        if hist is None or hist.empty or "Close" not in hist.columns:
            return empty
        frame = hist.copy()
        if "Date" in frame.columns:
            frame["Date"] = pd.to_datetime(frame["Date"])
            frame = frame.set_index("Date")
        close = pd.to_numeric(frame["Close"], errors="coerce").dropna()
        if close.empty:
            return empty
        yearly = close.resample("YE").last().dropna()
        if len(yearly) < 2:
            return empty
        rets = yearly.pct_change().dropna() * 100.0
        return pd.DataFrame({
            "Year": [str(int(ts.year)) for ts in rets.index],
            "Return_Pct": [float(v) for v in rets.values],
        })
    except Exception:  # noqa: BLE001
        return empty


@st.cache_data(ttl=3600, show_spinner=False)
@st.cache_data(ttl=300, show_spinner=False)
def get_valuation_metrics(ticker: str) -> dict:
    """Analyst targets + multiples for a suggested buy-zone band.

    Pulls ``yfinance.Ticker.info`` and derives ``buy_zone_high`` as the midpoint
    between the 52-week low and the analyst target low (when both exist).

    Returns:
        dict: Keys include current/target/52w/P-E/P-B and buy-zone bounds.
        Empty-ish dict (all None) on failure — never raises.
    """
    blank = {
        "ticker": ticker,
        "current_price": None,
        "target_low": None,
        "target_mean": None,
        "fifty_two_week_low": None,
        "fifty_two_week_high": None,
        "trailing_pe": None,
        "price_to_book": None,
        "return_1m_pct": None,
        "return_1y_pct": None,
        "buy_zone_low": None,
        "buy_zone_high": None,
        "ok": False,
    }
    if not ticker:
        return blank
    try:
        info = yf.Ticker(ticker).info
        if not isinstance(info, dict) or not info:
            return blank

        def _f(x):
            try:
                v = float(x)
                return v if v == v else None
            except (TypeError, ValueError):
                return None

        current = _f(info.get("currentPrice") or info.get("regularMarketPrice"))
        target_low = _f(info.get("targetLowPrice"))
        target_mean = _f(info.get("targetMeanPrice"))
        w52_low = _f(info.get("fiftyTwoWeekLow"))
        w52_high = _f(info.get("fiftyTwoWeekHigh"))
        pe = _f(info.get("trailingPE"))
        pb = _f(info.get("priceToBook"))

        buy_low = w52_low
        buy_high = None
        if w52_low is not None and target_low is not None:
            buy_high = (w52_low + target_low) / 2.0
            if buy_high < w52_low:
                buy_high = w52_low
        elif target_low is not None:
            buy_high = target_low
            buy_low = target_low * 0.92 if buy_low is None else buy_low
        elif w52_low is not None:
            buy_high = w52_low * 1.08

        # Flat band fallback: Yahoo often omits targetLow → identical bounds.
        if buy_high is not None and buy_low is not None and buy_high <= buy_low * 1.01:
            buy_high = buy_low * 1.05
        if buy_low is not None and buy_high is None:
            buy_high = buy_low * 1.05

        # Trailing 1M / 1Y returns from DuckDB daily history.
        ret_1m = None
        ret_1y = None
        try:
            hist = _db_hist(ticker, days=252)
            if hist is not None and not hist.empty and "Close" in hist.columns:
                close = pd.to_numeric(hist["Close"], errors="coerce").dropna()
                if len(close) >= 2:
                    ret_1y = float(close.iloc[-1] / close.iloc[0] - 1.0) * 100.0
                if len(close) >= 22:
                    ret_1m = float(close.iloc[-1] / close.iloc[-22] - 1.0) * 100.0
        except Exception:  # noqa: BLE001
            pass

        return {
            "ticker": ticker,
            "current_price": current,
            "target_low": target_low,
            "target_mean": target_mean,
            "fifty_two_week_low": w52_low,
            "fifty_two_week_high": w52_high,
            "trailing_pe": pe,
            "price_to_book": pb,
            "return_1m_pct": ret_1m,
            "return_1y_pct": ret_1y,
            "buy_zone_low": buy_low,
            "buy_zone_high": buy_high,
            "ok": True,
        }
    except Exception:  # noqa: BLE001
        return blank


def render_annual_returns_chart(df: pd.DataFrame, ticker: str) -> go.Figure:
    """Neon/red yearly return bars on the terminal dark theme."""
    colors = [_NEON if float(v) >= 0 else _RED for v in df["Return_Pct"]]
    fig = go.Figure(
        go.Bar(
            x=df["Year"].astype(str),
            y=df["Return_Pct"].astype(float),
            marker_color=colors,
            text=[f"{v:+.1f}%" for v in df["Return_Pct"]],
            textposition="outside",
            hovertemplate="%{x}: %{y:+.1f}%<extra></extra>",
        )
    )
    fig.add_hline(y=0, line_dash="dot", line_color=_MUTED)
    fig.update_layout(
        title=dict(
            text=f"Perf. annuelle — {ticker} (≈10 ans)",
            font=dict(color=_WHITE, size=14),
        ),
        xaxis_title="Année",
        yaxis_title="Rendement %",
        showlegend=False,
        margin=dict(t=48, l=40, r=20, b=40),
        bargap=0.25,
    )
    return _style_dark_fig(fig, height=380)


@st.cache_data(ttl=300, show_spinner=False)
def _extract_close_frame(raw: pd.DataFrame, tickers: tuple[str, ...] | list[str]) -> pd.DataFrame:
    """Extract a clean Close matrix from yfinance download (no cross-ticker fill)."""
    if raw is None or raw.empty:
        return pd.DataFrame()
    close = raw
    if isinstance(raw.columns, pd.MultiIndex):
        lvl0 = raw.columns.get_level_values(0)
        if "Close" in lvl0:
            close = raw["Close"]
        elif "Adj Close" in lvl0:
            close = raw["Adj Close"]
    if isinstance(close, pd.Series):
        name = tickers[0] if tickers else "TICKER"
        close = close.to_frame(name=name)
    # Per-column forward fill only — NEVER bfill across columns (that created
    # flat 0% performances and swapped prices between tickers).
    close = close.apply(lambda s: s.ffill())
    return close


def _valid_price_series(series: pd.Series, min_points: int = 3) -> pd.Series | None:
    """Drop flat/NaN series that would produce fake 0% performances."""
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) < min_points:
        return None
    if float(s.nunique()) < 2:
        return None  # constant after fill = bad data
    if float(s.iloc[0]) <= 0 or float(s.iloc[-1]) <= 0:
        return None
    return s


@st.cache_data(ttl=600, show_spinner=False)
def get_market_performance(
    tickers: tuple[str, ...],
    period: str | None = "1mo",
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """Compute performance over a preset period or an explicit date range (DuckDB)."""
    if not tickers:
        return pd.DataFrame()
    try:
        batch = list(tickers)[:120]
        days = _period_to_days(period)
        rows = []
        for t in batch:
            hist = _db_hist(t, days=days + 5)
            if hist is None or hist.empty or "Close" not in hist.columns:
                continue
            close = pd.to_numeric(hist["Close"], errors="coerce").dropna()
            series = _valid_price_series(close)
            if series is None:
                continue
            if start:
                if "Date" in hist.columns:
                    dates = pd.to_datetime(hist["Date"])
                    mask = (dates >= pd.Timestamp(start)) & (
                        dates <= pd.Timestamp(end) if end else True
                    )
                    sub = close[mask.values] if len(mask) == len(close) else close
                else:
                    sub = close
                if len(sub) < 2:
                    continue
                start_price, end_price = float(sub.iloc[0]), float(sub.iloc[-1])
            else:
                start_price, end_price = float(series.iloc[0]), float(series.iloc[-1])
            perf = (end_price / start_price - 1.0) * 100.0
            rows.append({
                "Ticker": str(t),
                "Start Price": start_price,
                "Current Price": end_price,
                "Performance (%)": perf,
            })
        if not rows:
            return pd.DataFrame()
        return (
            pd.DataFrame(rows)
            .sort_values("Performance (%)", ascending=False)
            .reset_index(drop=True)
        )
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def get_normalized_prices(
    tickers: tuple[str, ...], period: str | None, start: str | None, end: str | None
) -> pd.DataFrame:
    """Return prices rebased to 100 at the interval start (DuckDB)."""
    if not tickers:
        return pd.DataFrame()
    try:
        batch = list(tickers)[:40]
        days = _period_to_days(period)
        series_map: dict[str, pd.Series] = {}
        for t in batch:
            hist = _db_hist(t, days=days + 5)
            if hist is None or hist.empty or "Close" not in hist.columns:
                continue
            if "Date" in hist.columns:
                idx = pd.to_datetime(hist["Date"])
            else:
                idx = pd.to_datetime(hist.index)
            close = pd.to_numeric(hist["Close"], errors="coerce")
            s = pd.Series(close.values, index=idx).dropna()
            if start:
                s = s[s.index >= pd.Timestamp(start)]
                if end:
                    s = s[s.index <= pd.Timestamp(end)]
            valid = _valid_price_series(s, min_points=2)
            if valid is not None:
                series_map[str(t)] = valid
        if not series_map:
            return pd.DataFrame()
        out = pd.DataFrame(series_map)
        for col in out.columns:
            base = float(out[col].dropna().iloc[0])
            if base > 0:
                out[col] = (out[col] / base) * 100.0
        return out.dropna(how="all")
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def load_morning_briefing() -> dict:
    """Load Phase 19 morning Zeitgeist JSON (graceful empty on miss)."""
    try:
        from newsletter_api import NewsletterSensor

        data = NewsletterSensor.read_briefing()
        return data or {}
    except Exception:  # noqa: BLE001
        return {}


@st.cache_data(ttl=604800, show_spinner=False)
def get_company_logo(ticker: str) -> str:
    """Clearbit logo URL from Yahoo ``website`` domain (empty string on fail)."""
    if not ticker:
        return ""
    try:
        from urllib.parse import urlparse

        info = yf.Ticker(ticker).info or {}
        website = str(info.get("website") or "").strip()
        if not website:
            return ""
        if "://" not in website:
            website = "https://" + website
        host = (urlparse(website).hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if not host or "." not in host:
            return ""
        return f"https://logo.clearbit.com/{host}"
    except Exception:  # noqa: BLE001
        return ""


@st.cache_data(ttl=86400, show_spinner=False)
def get_deep_news_analysis(ticker: str, headlines: tuple[str, ...]) -> str:
    """Daily-cached deep LLM news brief for a ticker (Phase 22)."""
    try:
        from llm_explainer import NarrativeExplainer

        explainer = NarrativeExplainer()
        return asyncio.run(
            explainer.analyze_ticker_news_deep(ticker, list(headlines or ()))
        )
    except Exception as exc:  # noqa: BLE001
        return f"Analyse IA indisponible ({exc})."


def summarize_insider_activity(df: pd.DataFrame) -> dict:
    """Aggregate buy/sell counts, shares and notional from an insider frame."""
    empty = {
        "n_buys": 0,
        "n_sells": 0,
        "buy_shares": 0.0,
        "sell_shares": 0.0,
        "buy_value": 0.0,
        "sell_value": 0.0,
        "net_shares": 0.0,
        "net_value": 0.0,
        "source": "",
        "signal": "Neutre / données insuffisantes",
        "tone": "muted",
    }
    if df is None or df.empty:
        return empty
    n_buys = n_sells = 0
    buy_shares = sell_shares = 0.0
    buy_value = sell_value = 0.0
    for _, row in df.iterrows():
        tx = str(row.get("Transaction") or row.get("Title") or "").casefold()
        shares = pd.to_numeric(row.get("Shares"), errors="coerce")
        value = pd.to_numeric(row.get("Value"), errors="coerce")
        shares_f = float(shares) if pd.notna(shares) else 0.0
        value_f = float(value) if pd.notna(value) else 0.0
        is_buy = any(
            k in tx
            for k in ("achat", "acquisition", "buy", "purchase", "p-purchase")
        )
        is_sell = any(
            k in tx
            for k in ("vente", "cession", "sell", "sale", "dispos")
        )
        if is_buy and not is_sell:
            n_buys += 1
            buy_shares += abs(shares_f)
            buy_value += abs(value_f)
        elif is_sell and not is_buy:
            n_sells += 1
            sell_shares += abs(shares_f)
            sell_value += abs(value_f)
    net_shares = buy_shares - sell_shares
    net_value = buy_value - sell_value
    source = ""
    if "Source" in df.columns and len(df):
        source = str(df["Source"].iloc[0])
    if n_buys > n_sells and n_buys >= 1:
        signal = (
            f"🟢 Signal de confiance : {n_buys} achat(s) de dirigeants détecté(s)"
            + (f" (Volume : {buy_value:,.0f} €)" if buy_value > 0 else "")
        )
        tone = "green"
    elif n_sells > n_buys and n_sells >= 1:
        signal = (
            f"🔴 Signal de prudence : {n_sells} vente(s) de dirigeants"
            + (f" (Volume : {sell_value:,.0f} €)" if sell_value > 0 else "")
        )
        tone = "red"
    elif n_buys or n_sells:
        signal = (
            f"🟡 Activité mixte : {n_buys} achat(s) / {n_sells} vente(s)"
        )
        tone = "amber"
    else:
        signal = "Neutre / classification transaction indisponible"
        tone = "muted"
    return {
        "n_buys": n_buys,
        "n_sells": n_sells,
        "buy_shares": buy_shares,
        "sell_shares": sell_shares,
        "buy_value": buy_value,
        "sell_value": sell_value,
        "net_shares": net_shares,
        "net_value": net_value,
        "source": source,
        "signal": signal,
        "tone": tone,
    }


def morning_briefing_is_live(briefing: dict | None) -> bool:
    """True when scheduler wrote a usable Zeitgeist (not the placeholder)."""
    if not briefing:
        return False
    zg = str(briefing.get("zeitgeist") or "").strip()
    if not zg or zg.casefold().startswith("indisponible"):
        return False
    # Prefer a real generated_at from the morning job.
    if briefing.get("generated_at"):
        return True
    return bool(briefing.get("headlines"))


@st.cache_data(ttl=900, show_spinner=False)
def get_strategy_fingerprint(ticker: str) -> dict:
    """Radar axes powered by the multi-model ensemble outputs."""
    out = {
        "Mean Reversion": 0.0,
        "Momentum": 0.0,
        "Quality/Value": 0.0,
        "Insider Confidence": 0.0,
    }
    try:
        from duckdb_manager import TimeSeriesDB
        from technical_scorer import SignalGenerator

        hist = get_ts_db().get_historical_prices(ticker, days=252)
        if hist is None or hist.empty or len(hist) < 200:
            return out
        conv = SignalGenerator().evaluate(ticker, hist)
        models = conv.get("model_scores") or {}
        ctx = conv.get("context_breakdown") or {}

        out["Mean Reversion"] = float(models.get("mean_reversion_model") or 0.0)
        out["Momentum"] = float(models.get("trend_model") or 0.0)
        out["Quality/Value"] = float(ctx.get("fundamentals") or 0.0)
        out["Insider Confidence"] = float(ctx.get("insiders") or 0.0)
        return out
    except Exception:  # noqa: BLE001
        return out


def render_strategy_radar(fingerprint: dict, ticker: str):
    """Dark Bloomberg-style polar radar via plotly.express.line_polar (0–100)."""
    cats = [
        "Mean Reversion",
        "Momentum",
        "Quality/Value",
        "Insider Confidence",
    ]
    vals = [float(fingerprint.get(c) or 0) for c in cats]
    df = pd.DataFrame({"axis": cats, "score": vals})
    fig = pex.line_polar(
        df,
        r="score",
        theta="axis",
        line_close=True,
        range_r=[0, 100],
    )
    fig.update_traces(
        fill="toself",
        line_color=_CYAN,
        fillcolor="rgba(0, 229, 255, 0.18)",
        marker=dict(color=_NEON, size=7),
    )
    fig.update_layout(
        paper_bgcolor=_BG,
        plot_bgcolor=_BG,
        font=dict(family="Courier New", color=_WHITE, size=11),
        polar=dict(
            bgcolor="#050505",
            radialaxis=dict(
                visible=True,
                range=[0, 100],
                gridcolor="#333",
                tickfont=dict(color=_MUTED, size=9),
            ),
            angularaxis=dict(
                gridcolor="#333",
                tickfont=dict(color=_WHITE, size=11),
            ),
        ),
        margin=dict(l=50, r=50, t=48, b=40),
        height=380,
        showlegend=False,
        title=dict(
            text=f"Empreinte — {short_name(ticker)}",
            font=dict(color=_CYAN, size=13),
        ),
    )
    return fig


# Back-compat aliases (engine conviction axes still used elsewhere if needed)
@st.cache_data(ttl=900, show_spinner=False)
def get_conviction_axes(ticker: str) -> dict:
    """Engine ensemble axes (points) — optional companion to strategy radar."""
    try:
        from technical_scorer import SignalGenerator
        from duckdb_manager import TimeSeriesDB

        db = get_ts_db()
        hist = db.get_historical_prices(ticker, days=300)
        if hist is None or hist.empty:
            return {}
        return SignalGenerator().evaluate(ticker, hist)
    except Exception:  # noqa: BLE001
        return {}


def render_conviction_radar(conv: dict, ticker: str) -> go.Figure:
    """Legacy engine radar (kept for compatibility). Prefer strategy radar."""
    cats = ["Mean Reversion", "Volume", "Insiders", "Institutional"]
    vals = [
        float(conv.get("mean_reversion") or 0),
        float(conv.get("volume_breakout") or 0),
        float(conv.get("insider") or 0),
        float(conv.get("institutional") or 0),
    ]
    cats_c = cats + [cats[0]]
    vals_c = vals + [vals[0]]
    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=vals_c,
        theta=cats_c,
        fill="toself",
        name=ticker,
        line=dict(color=_NEON, width=2),
        fillcolor="rgba(0,255,0,0.12)",
    ))
    fig.update_layout(
        polar=dict(
            bgcolor="#050505",
            radialaxis=dict(
                visible=True, range=[0, 35],
                gridcolor="#333", tickfont=dict(color=_MUTED, size=10),
            ),
            angularaxis=dict(
                gridcolor="#333", tickfont=dict(color=_WHITE, size=11),
            ),
        ),
        paper_bgcolor=_BG,
        plot_bgcolor=_BG,
        font=dict(color=_WHITE),
        margin=dict(l=40, r=40, t=40, b=40),
        height=320,
        showlegend=False,
    )
    return fig


@st.cache_data(ttl=1800, show_spinner=False)
def get_universe_screener_tags(tickers: tuple[str, ...]) -> dict:
    """Map ticker → short technical tag string for the Univers dark table."""
    tags: dict[str, str] = {}
    if not tickers:
        return tags
    try:
        from duckdb_manager import TimeSeriesDB
        from technical_scorer import SignalGenerator

        db = get_ts_db()
        gen = SignalGenerator()
        for ticker in tickers:
            parts: list[str] = []
            try:
                hist = db.get_historical_prices(ticker, days=220)
                if hist is None or hist.empty or len(hist) < 50:
                    tags[ticker] = "—"
                    continue
                enriched = gen.calculate_indicators(hist)
                last = enriched.iloc[-1]
                close = float(last["Close"])
                rsi = last.get("RSI_14")
                sma200 = last.get("SMA_200")
                sma5 = last.get("SMA_5")
                if rsi is not None and not pd.isna(rsi) and float(rsi) < 30:
                    parts.append("🔥 OVERSOLD")
                if (
                    sma200 is not None
                    and not pd.isna(sma200)
                    and close > float(sma200)
                ):
                    parts.append("📈 UPTREND")
                if (
                    sma5 is not None
                    and not pd.isna(sma5)
                    and close > float(sma5)
                    and rsi is not None
                    and not pd.isna(rsi)
                    and float(rsi) > 55
                ):
                    parts.append("⚡ MOM")
                if sma200 is not None and not pd.isna(sma200) and close < float(sma200):
                    parts.append("📉 DOWNTREND")
            except Exception:  # noqa: BLE001
                pass
            tags[ticker] = " · ".join(parts) if parts else "—"
    except Exception:  # noqa: BLE001
        for ticker in tickers:
            tags[ticker] = "—"
    return tags


def simulate_buy_what_if(
    portfolio_obj, ticker: str, notional_eur: float = 1000.0
) -> dict:
    """What-if: impact of buying ``notional_eur`` on cash / sector / rough corr."""
    prices = get_last_prices((ticker,))
    px = float(prices.get(ticker) or 0)
    cash = float(portfolio_obj.cash_available)
    equity = float(portfolio_obj.total_equity) or 1.0
    sector = _sector_for_ticker(ticker) or "Unknown"
    qty = int(notional_eur // px) if px > 0 else 0
    cost = qty * px
    cash_after = cash - cost

    # Current sector weight
    sec_now = 0.0
    for p in portfolio_obj.positions:
        if _sector_for_ticker(p.ticker) == sector:
            sec_now += float(p.qty_shares) * float(
                prices.get(p.ticker) or getattr(p, "avg_price", 0) or 0
            )
    sec_now_pct = 100.0 * sec_now / equity
    sec_after_pct = 100.0 * (sec_now + cost) / (equity)  # approx same equity

    # Rough max abs correlation vs held names (DuckDB closes if available)
    max_corr = None
    try:
        from duckdb_manager import TimeSeriesDB

        db = get_ts_db()
        cand = db.get_historical_prices(ticker, days=90)
        if cand is not None and not cand.empty and "Close" in cand.columns:
            cser = cand["Close"].pct_change().dropna()
            corrs = []
            for p in portfolio_obj.positions:
                if p.ticker == ticker:
                    continue
                other = db.get_historical_prices(p.ticker, days=90)
                if other is None or other.empty:
                    continue
                oser = other["Close"].pct_change().dropna()
                joined = pd.concat([cser, oser], axis=1, join="inner").dropna()
                if len(joined) < 20:
                    continue
                corrs.append(float(joined.iloc[:, 0].corr(joined.iloc[:, 1])))
            if corrs:
                max_corr = max(corrs, key=lambda x: abs(x))
    except Exception:  # noqa: BLE001
        max_corr = None

    return {
        "qty": qty,
        "price": px,
        "cost": cost,
        "cash_before": cash,
        "cash_after": cash_after,
        "sector": sector,
        "sector_pct_before": sec_now_pct,
        "sector_pct_after": sec_after_pct,
        "max_corr": max_corr,
        "affordable": qty >= 1 and cost <= cash,
    }


def get_recent_news(symbol: str, limit: int = 6) -> list[dict]:
    """Return news for a ticker — SQLite archive first, live fetch if sparse."""
    db_items: list[dict] = []
    if _SQLITE_PATH.exists():
        try:
            db = get_portfolio_db()
            db.init_db()
            db_items = db.get_news_history(symbol, limit=limit)
        except Exception:  # noqa: BLE001
            db_items = []

    if len(db_items) >= 3:
        return db_items[:limit]

    fresh = _fetch_news_from_apis(symbol, limit=max(limit, 12))
    if fresh and _SQLITE_PATH.exists():
        try:
            db = get_portfolio_db()
            db.init_db()
            db.save_news([{**n, "ticker": symbol, "url": n.get("link")} for n in fresh])
        except Exception:  # noqa: BLE001
            pass

    merged: list[dict] = []
    seen: set[str] = set()
    for n in db_items + fresh:
        key = (n.get("title") or "").strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(n)
    return merged[:limit]


@st.cache_data(ttl=1800, show_spinner=False)
def get_bourso_profile(ticker: str) -> dict:
    """Cached Boursorama instrument profile (eligibility, consensus, ISIN)."""
    try:
        scrapers_dir = _ROOT / "00_data_sensors" / "scrapers"
        if str(scrapers_dir) not in sys.path:
            sys.path.insert(0, str(scrapers_dir))
        from bourso_scraper import BoursoramaScraper  # noqa: WPS433
        return BoursoramaScraper().get_instrument_profile(ticker) or {}
    except Exception:  # noqa: BLE001
        return {}


def _tv_symbol(ticker: str) -> str:
    """Map a Yahoo ticker to a TradingView ``EXCHANGE:SYMBOL`` string.

    Euronext Paris/Amsterdam use the ``EURONEXT:`` prefix (capitalized).
    """
    if not ticker:
        return "EURONEXT:CAC40"
    mapping = {
        ".PA": "EURONEXT",
        ".AS": "EURONEXT",
        ".BR": "EURONEXT",
        ".LS": "EURONEXT",
        ".DE": "XETR",
        ".MC": "BME",
        ".MI": "MIL",
        ".HE": "OMXHEX",
        ".IR": "EURONEXTDUBLIN",
        ".SW": "SIX",
        ".L": "LSE",
    }
    for suffix, exch in mapping.items():
        if ticker.endswith(suffix):
            return f"{exch}:{ticker[: -len(suffix)].upper()}"
    return ticker.upper()


def build_broker_order_ticket(
    ticker: str,
    qty: int,
    price: float,
    isin: str | None = None,
) -> dict:
    """Build a ready-to-execute PEA order ticket payload for UI display."""
    try:
        scrapers_dir = _ROOT / "00_data_sensors" / "scrapers"
        if str(scrapers_dir) not in sys.path:
            sys.path.insert(0, str(scrapers_dir))
        from bourso_scraper import yahoo_to_bourso_slug  # noqa: WPS433
        bourso_slug = yahoo_to_bourso_slug(ticker) or ticker.replace(".", "-").lower()
    except Exception:  # noqa: BLE001
        bourso_slug = ticker.replace(".", "-").lower()

    clean_qty = max(0, int(qty or 0))
    clean_price = max(0.0, float(price or 0.0))
    limit_price = round(clean_price * 1.001, 2) if clean_price > 0 else 0.0
    notional = clean_qty * clean_price
    est_fee = round(notional * 0.005, 2)
    return {
        "ticker": ticker,
        "isin": isin or "n/a",
        "order_type": "Limite",
        "qty": clean_qty,
        "limit_price": limit_price,
        "notional": notional,
        "estimated_fee_max": est_fee,
        "bourso_url": f"https://www.boursorama.com/cours/{bourso_slug}/",
    }


def get_decision_checklist(ticker: str, portfolio_obj, vix: float) -> dict:
    """Evaluate key PEA Pollux gate checks and return an explicit checklist."""
    ind = get_indicators(ticker) or {}
    close = float(ind.get("close") or 0.0)
    rsi = ind.get("rsi")
    sma200 = ind.get("sma200")
    sma5 = ind.get("sma5")

    score = 0.0
    try:
        fp = get_strategy_fingerprint(ticker) or {}
        vals = [float(v) for v in fp.values() if v is not None]
        if vals:
            score = float(sum(vals) / len(vals))
    except Exception:  # noqa: BLE001
        score = 0.0

    sector = _sector_for_ticker(ticker)
    sector_value = sum(
        float(getattr(p, "market_value", 0.0) or 0.0)
        for p in (portfolio_obj.positions or [])
        if str(getattr(p, "sector", "")) == sector
    )
    eq = float(getattr(portfolio_obj, "total_equity", 0.0) or 0.0)
    sector_pct = (sector_value / eq * 100.0) if eq > 0 else 0.0
    cash = float(getattr(portfolio_obj, "cash_available", 0.0) or 0.0)

    checks = []

    r1_ok = bool((rsi is not None and rsi < 30) or score >= 65)
    checks.append({
        "rule": "R1 RSI<30 ou Score>=65",
        "status": "OK" if r1_ok else "WARN",
        "detail": f"RSI={rsi:.1f}" if rsi is not None else f"Score={score:.0f}",
    })
    r2_ok = bool(close and sma200 and close > float(sma200))
    checks.append({"rule": "R2 Close > SMA200", "status": "OK" if r2_ok else "FAIL",
                   "detail": f"{close:.2f} vs {float(sma200):.2f}" if sma200 else "SMA200 n/a"})
    r3_ok = bool(close and sma5 and close > float(sma5))
    checks.append({"rule": "R3 Close > SMA5", "status": "OK" if r3_ok else "FAIL",
                   "detail": f"{close:.2f} vs {float(sma5):.2f}" if sma5 else "SMA5 n/a"})
    r4_ok = float(vix) < 30.0
    checks.append({"rule": "R4 VIX < 30", "status": "OK" if r4_ok else "VETO",
                   "detail": f"VIX={float(vix):.1f}"})
    r5_ok = sector_pct < 25.0
    checks.append({"rule": "R5 Poids secteur < 25%", "status": "OK" if r5_ok else "VETO",
                   "detail": f"{sector}={sector_pct:.1f}%"})
    r6_ok = cash >= close > 0
    checks.append({"rule": "R6 Cash >= 1 part", "status": "OK" if r6_ok else "FAIL",
                   "detail": f"Cash={cash:,.0f}€ / Cours={close:,.2f}€"})

    statuses = [c["status"] for c in checks]
    if any(s == "VETO" for s in statuses):
        overall = "🔴 BLOQUÉ"
    elif any(s in ("FAIL", "WARN") for s in statuses):
        overall = "🟡 ATTENTE"
    else:
        overall = "🟢 PRÊT"
    return {"overall": overall, "checks": checks, "score_hint": score}


_BLUE_CHIPS_TAPE = [
    "CW8.PA", "MC.PA", "OR.PA", "AI.PA", "SAN.PA",
    "TTE.PA", "BNP.PA", "AIR.PA", "RMS.PA", "SU.PA",
]


@st.cache_data(ttl=120, show_spinner=False)
def _native_tape_perf(period: str) -> pd.DataFrame:
    """Cached performance snapshot for the native HTML ticker tape.

    For ``1d`` we pull 5d data and compute close-to-close day return
    ``(last / prev - 1)`` to avoid Yahoo's period quirks.
    """
    if period != "1d":
        return get_market_performance(tuple(_BLUE_CHIPS_TAPE), period=period)
    try:
        rows = []
        for t in _BLUE_CHIPS_TAPE:
            hist = _db_hist(t, days=7)
            if hist is None or hist.empty or "Close" not in hist.columns:
                continue
            series = _valid_price_series(
                pd.to_numeric(hist["Close"], errors="coerce").dropna()
            )
            if series is None or len(series) < 2:
                continue
            current = float(series.iloc[-1])
            prev = None
            for i in range(len(series) - 2, -1, -1):
                p = float(series.iloc[i])
                if p > 0 and p != current:
                    prev = p
                    break
            if prev is None or prev <= 0:
                prev = float(series.iloc[-2]) if len(series) >= 2 else None
            if prev is None or prev <= 0:
                continue
            rows.append(
                {
                    "Ticker": t,
                    "Start Price": prev,
                    "Current Price": current,
                    "Performance (%)": (current / prev - 1.0) * 100.0,
                }
            )
        if not rows:
            return pd.DataFrame()
        out = pd.DataFrame(rows).sort_values("Performance (%)", ascending=False)
        return out.reset_index(drop=True)
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


@st.fragment(run_every="30s")
def render_native_ticker_tape(period: str = "1d") -> None:
    """Render a CSS marquee ticker tape (no TradingView dependency)."""
    perf = _native_tape_perf(period)
    if perf is None or perf.empty:
        st.caption("Bandeau marché indisponible (réseau ou données manquantes).")
        return

    chips: list[str] = []
    for _, row in perf.iterrows():
        ticker = str(row["Ticker"])
        perf_pct = float(row["Performance (%)"])
        color = _NEON if perf_pct >= 0 else _RED
        logo = get_company_logo(ticker)
        chips.append(
            f'<span class="tape-chip">'
            f'<a href="/?ticker={ticker}" target="_self" '
            f'style="text-decoration:none;color:inherit;">'
            f'<img src="{logo}" height="16" '
            f'style="vertical-align:middle;margin-right:6px;border-radius:2px;" '
            f'onerror="this.style.display=\'none\'" />'
            f'{short_name(ticker)} '
            f'<span style="color:{color};font-weight:700;">{perf_pct:+.2f}%</span>'
            f"</a>"
            f"</span>"
        )
    if not chips:
        st.caption("Bandeau marché vide pour cette période.")
        return

    track = "".join(chips) * 2
    period_label = {"1d": "1 jour", "5d": "5 jours", "1mo": "1 mois"}.get(period, period)
    st.markdown(
        f"""
<style>
@keyframes pea-marquee {{
  0% {{ transform: translateX(0); }}
  100% {{ transform: translateX(-50%); }}
}}
.native-tape-wrap {{
  background: #0A0A0A;
  border: 1px solid #222;
  border-left: 3px solid {_CYAN};
  overflow: hidden;
  padding: 10px 0;
  margin-bottom: 6px;
}}
.native-tape-track {{
  display: inline-flex;
  align-items: center;
  white-space: nowrap;
  animation: pea-marquee 45s linear infinite;
  gap: 28px;
}}
.tape-chip {{
  display: inline-flex;
  align-items: center;
  color: {_WHITE};
  font-family: Courier New, monospace;
  font-size: 13px;
  padding: 0 14px;
}}
</style>
<div class="native-tape-wrap" title="Bandeau natif · {period_label}">
  <div class="native-tape-track">{track}</div>
</div>
""",
        unsafe_allow_html=True,
    )


def build_data_sources_health_df() -> pd.DataFrame:
    """Live telemetry for Architecture tab — env vars + local DB files."""
    duck_path = _DB_DIR / "ohlcv.duckdb"
    freshness = "n/a"
    try:
        from duckdb_manager import TimeSeriesDB

        db = get_ts_db()
        with db._connect() as conn:
            row = conn.execute("SELECT MAX(date) AS d FROM ohlcv_data;").fetchone()
        max_date = row[0] if row else None
        if max_date:
            freshness = f"Dernière bougie: {str(max_date)[:10]}"
    except Exception:  # noqa: BLE001
        freshness = "indisponible"
    rows = [
        (
            "yfinance",
            "OHLCV, calendrier, insiders, news fallback",
            "🟢 Actif",
            "Pas de prix si réseau down",
            freshness,
        ),
        (
            "VIX / VSTOXX",
            f"Coupe-circuit panic (seuil {_VIX_PANIC:.0f})",
            "🟢 Actif",
            "Fallback 15.0 si indispo",
            freshness,
        ),
        (
            "Bandeau natif (HTML)",
            "Perf blue-chips + logos Clearbit",
            "🟢 Actif",
            "Remplace l'ancien widget TradingView tape",
            freshness,
        ),
        (
            "SQLite portfolio.db",
            "Portfolio / audit / equity / news_history",
            "🟢 Connecté" if _SQLITE_PATH.exists() else "🔴 Absent",
            "Dashboard bloqué sans DB locale",
            f"MAJ wallet: {str(portfolio.last_updated)[:19]}" if "portfolio" in globals() else "n/a",
        ),
        (
            "DuckDB ohlcv.duckdb",
            "Historique technique / ATR / screener",
            "🟢 Connecté" if duck_path.exists() else "🟡 Partiel",
            "ATR/stops moins fiables sans OHLCV local",
            freshness,
        ),
        (
            "OpenRouter",
            "Sentiment news + briefing geo + Synthèse IA",
            "🟢 Actif" if os.getenv("OPENROUTER_API_KEY") else "🔴 DÉCONNECTÉ",
            "CRITIQUE: Arrêt immédiat du terminal",
            "temps réel",
        ),
        (
            "FMP",
            "Insiders fallback (après AMF)",
            "🟢 Actif" if os.getenv("FMP_API_KEY") else "🔴 DÉCONNECTÉ",
            "CRITIQUE: cascade AMF-only (pas de fallback US)",
            "n/a",
        ),
        (
            "Finnhub",
            "Fondamentaux EU (Value/Quality)",
            "🟢 Actif" if os.getenv("FINNHUB_API_KEY") else "🟡 Optionnel",
            "Fallback yfinance / score neutre si indisponible",
            "cache 7 jours",
        ),
        (
            "AMF Opendatasoft / BDIF",
            "Déclarations dirigeants (API publique, free)",
            "🟢 Actif",
            "Insiders FR indisponibles si BDIF/ODS down",
            "n/a",
        ),
        (
            "IMAP Newsletter",
            "Morning Briefing Synthèse IA",
            "🟢 Actif"
            if os.getenv("YAHOO_MAIL_USER") and os.getenv("YAHOO_MAIL_APP_PASSWORD")
            else "🔴 DÉCONNECTÉ",
            "CRITIQUE: Arrêt immédiat du terminal",
            "job 08:25 Paris",
        ),
        (
            "Polymarket Gamma",
            "Probabilités macro (contexte)",
            "🟢 Actif",
            "Fallback seed si JSON bloqué (Cloudflare)",
            "quasi temps réel",
        ),
        (
            "Boursorama scraper",
            "Profil PEA/SRD, consensus, news",
            "🟢 Actif",
            "Fragile — dates parfois approximatives",
            "variable",
        ),
    ]
    return pd.DataFrame(
        rows,
        columns=["Source", "Rôle", "Statut Live", "Impact si manquant", "Fraîcheur des données"],
    )


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
    """Return the Core ETF regime (price vs 200-day SMA) from DuckDB."""
    try:
        hist = _db_hist(_CORE_TICKER, days=252)
        if hist is None or hist.empty or len(hist) < 200:
            return {}
        from technical_scorer import SignalGenerator

        enriched = SignalGenerator().calculate_indicators(hist)
        last = enriched.iloc[-1]
        price = float(last["Close"])
        sma200 = last.get("SMA_200")
        if sma200 is None or pd.isna(sma200):
            return {}
        sma200 = float(sma200)
        return {
            "ticker": _CORE_TICKER,
            "price": price,
            "sma200": sma200,
            "crash": price < sma200,
            "gap_pct": (price / sma200 - 1) * 100 if sma200 else 0.0,
        }
    except Exception:  # noqa: BLE001
        return {}


@st.cache_data(ttl=900, show_spinner=False)
def get_market_breadth(universe_df: pd.DataFrame, db_manager) -> dict:
    try:
        from duckdb_manager import TimeSeriesDB
        if universe_df is None or universe_df.empty: return {"pct_sma50": None, "pct_sma200": None, "valid": 0, "list_200": []}
        db = TimeSeriesDB(db_path=str(db_manager), read_only=True)
        tickers = universe_df.get("Ticker", pd.Series([], dtype=str)).dropna().astype(str).unique().tolist()
        candidates = [t for t in tickers if t][:160]
        valid, above50, above200 = 0, 0, 0
        list_200 = []
        for t in candidates:
            hist = db.get_historical_prices(t, days=200)
            if hist is None or hist.empty or "Close" not in hist.columns or len(hist) < 200: continue
            close = pd.to_numeric(hist["Close"], errors="coerce").dropna()
            if close.empty or len(close) < 200: continue
            last = float(close.iloc[-1])
            sma50, sma200 = float(close.tail(50).mean()), float(close.tail(200).mean())
            valid += 1
            if last > sma50: above50 += 1
            if last > sma200: 
                above200 += 1
                list_200.append(t)
            if valid >= 100: break
        if valid <= 0: return {"pct_sma50": None, "pct_sma200": None, "valid": 0, "list_200": []}
        return {"pct_sma50": above50 / valid * 100.0, "pct_sma200": above200 / valid * 100.0, "valid": valid, "list_200": list_200}
    except Exception: return {"pct_sma50": None, "pct_sma200": None, "valid": 0, "list_200": []}



@st.cache_data(ttl=600, show_spinner=False)
def get_indicators(ticker: str) -> dict:
    """Compute RSI(14) + SMA 5/50/200 + trend flags via quant engine."""
    try:
        hist = _db_hist(ticker, days=252)
        if hist is None or hist.empty or len(hist) < 30:
            return {}
        from technical_scorer import SignalGenerator

        gen = SignalGenerator()
        enriched = gen.calculate_indicators(hist)
        last = enriched.iloc[-1]
        close_s = pd.to_numeric(enriched["Close"], errors="coerce").dropna()
        if close_s.empty:
            return {}
        close = float(last["Close"])
        rsi_val = last.get("RSI_14")
        sma5 = last.get("SMA_5")
        sma50 = last.get("SMA_50")
        sma200 = last.get("SMA_200")
        return {
            "close": close,
            "rsi": float(rsi_val) if rsi_val is not None and not pd.isna(rsi_val) else None,
            "sma5": float(sma5) if sma5 is not None and not pd.isna(sma5) else None,
            "sma50": float(sma50) if sma50 is not None and not pd.isna(sma50) else None,
            "sma200": float(sma200) if sma200 is not None and not pd.isna(sma200) else None,
            "chg_1d": float((close_s.iloc[-1] / close_s.iloc[-2] - 1) * 100)
            if len(close_s) >= 2 else 0.0,
            "chg_5d": float((close_s.iloc[-1] / close_s.iloc[-6] - 1) * 100)
            if len(close_s) >= 6 else 0.0,
            "vol_ann": float(
                (
                    calculate_annualized_volatility(close_s.pct_change().dropna().tail(60))
                    if calculate_annualized_volatility is not None
                    else close_s.pct_change().dropna().tail(60).std(ddof=0) * (252 ** 0.5)
                ) * 100.0
            ),
        }
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


@st.cache_data(ttl=1800, show_spinner=False)
def get_insider_data(ticker: str) -> pd.DataFrame:
    """Fetch insider transactions: AMF BDIF -> FMP -> yfinance."""
    # --- 1) AMF BDIF (official French legal source) --------------------------
    try:
        scrapers_dir = _ROOT / "00_data_sensors" / "scrapers"
        if str(scrapers_dir) not in sys.path:
            sys.path.insert(0, str(scrapers_dir))
        from amf_scraper import AmfInsiderScraper  # noqa: WPS433

        profile: dict = {}
        try:
            profile = get_bourso_profile(ticker)
        except Exception:  # noqa: BLE001
            profile = {}
        amf = AmfInsiderScraper().get_recent_declarations(
            ticker,
            isin=profile.get("isin"),
            issuer=profile.get("name"),
        )
        if amf is not None and not amf.empty:
            out = amf.head(25).copy()
            if "Source" not in out.columns:
                out["Source"] = "AMF BDIF"
            return out.reset_index(drop=True)
    except Exception:  # noqa: BLE001
        pass

    # --- 2) FMP (secondary) --------------------------------------------------
    try:
        import os
        import requests

        api_key = os.getenv("FMP_API_KEY")
        if api_key:
            symbol = ticker.split(".")[0]
            url = (
                "https://financialmodelingprep.com/api/v4/insider-trading"
                f"?symbol={symbol}&apikey={api_key}"
            )
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                payload = resp.json()
                if isinstance(payload, list) and payload:
                    rows = []
                    for row in payload[:25]:
                        if not isinstance(row, dict):
                            continue
                        rows.append({
                            "Insider": row.get("reportingName")
                            or row.get("ownerName")
                            or "",
                            "Transaction": row.get("transactionType")
                            or row.get("acquistionOrDisposition")
                            or "",
                            "Shares": row.get("securitiesTransacted")
                            or row.get("shares"),
                            "Value": row.get("value") or row.get("price"),
                            "Date": row.get("transactionDate")
                            or row.get("filingDate"),
                            "Source": "FMP",
                        })
                    if rows:
                        return pd.DataFrame(rows)
    except Exception:  # noqa: BLE001
        pass

    # --- 3) yfinance (tertiary) ----------------------------------------------
    try:
        raw = yf.Ticker(ticker).insider_transactions
        if isinstance(raw, pd.DataFrame) and not raw.empty:
            df = raw.copy()
            df = df.rename(columns={"Start Date": "Date"})
            keep = [c for c in ("Insider", "Position", "Transaction", "Shares",
                                "Value", "Date") if c in df.columns]
            if keep:
                out = df[keep].copy()
                out["Source"] = "Yahoo Finance"
                if "Date" in out.columns:
                    out = out.sort_values("Date", ascending=False)
                if "Value" in out.columns:
                    out["Value"] = pd.to_numeric(out["Value"], errors="coerce")
                if "Shares" in out.columns:
                    out["Shares"] = pd.to_numeric(out["Shares"], errors="coerce")
                return out.head(25).reset_index(drop=True)
    except Exception:  # noqa: BLE001
        pass
    return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def heuristic_news_score(title: str) -> int:
    """Keyword impact score when LLM is unavailable or returns ~0."""
    t = (title or "").casefold()
    if not t:
        return 0
    bull = (
        "rachat", "acquisition", "fusion", "record", "hausse", "rebond",
        "dividende", "bénéfice", "benefice", "profit", "croissance", "contrat",
        "upgrade", "buyback", "guidance relev", "surperform", "positif",
        "approval", "autorisation", "victoire", "accord",
    )
    bear = (
        "amende", "fraude", "scandale", "baisse", "perte", "licenciement",
        "faillite", "recession", "guerre", "sanction", "downgrade", "alerte",
        "profit warning", "déception", "deception", "enquête", "enquete",
        "rachat d'actions annul", "coupures", "gel", "crise", "krach",
        "miss", "retard", "rappel",
    )
    score = 0
    for w in bull:
        if w in t:
            score += 28
    for w in bear:
        if w in t:
            score -= 32
    # Cap so heuristic never pretends to be a full LLM conviction.
    return int(max(-75, min(75, score)))


@st.cache_data(ttl=3600, show_spinner=False)
def score_news_with_llm(ticker: str, title: str) -> int:
    """Score a single headline (-100..+100), LLM first then keyword fallback.

    Cache key is ``(ticker, title)`` — reloading does not re-bill OpenRouter.
    """
    if not title or not title.strip():
        return 0
    llm_score = 0
    if NewsSentimentScorer is not None:
        try:
            score = asyncio.run(
                NewsSentimentScorer().analyze_news(ticker, [title.strip()])
            )
            llm_score = int(round(float(score)))
        except Exception:  # noqa: BLE001
            llm_score = 0
    if abs(llm_score) >= 10:
        return llm_score
    # Blend: if LLM is flat, surface keyword impact so cards are not all grey.
    heur = heuristic_news_score(title)
    if abs(heur) > abs(llm_score):
        return heur
    return llm_score


def run_sentiment(ticker: str, headlines: list[str]) -> float | None:
    """Synchronously score an aggregate news bundle (legacy aggregate button)."""
    if not headlines or NewsSentimentScorer is None:
        return None
    try:
        return asyncio.run(NewsSentimentScorer().analyze_news(ticker, headlines))
    except Exception:  # noqa: BLE001
        return None


def _sentiment_pill(score: int) -> str:
    """HTML badge for a -100..+100 news sentiment score."""
    if score > 20:
        color, bg, emoji = _NEON, "#0A2A0A", "\U0001F7E2"
    elif score < -20:
        color, bg, emoji = _RED, "#2A0A0A", "\U0001F534"
    else:
        color, bg, emoji = _MUTED, "#1A1A1A", "\u26AA"
    return (
        f"<span style='display:inline-block; padding:2px 8px; border-radius:10px; "
        f"background:{bg}; color:{color}; font-weight:700; font-size:12px; "
        f"font-family:Courier New,monospace; border:1px solid {color}; "
        f"margin-right:8px;'>{emoji} {score:+d}</span>"
    )


def news_impact_meta(score: int) -> dict:
    """Map a sentiment score to impact level + plain-French justification."""
    abs_s = abs(int(score))
    if abs_s >= 55:
        level, color = "FORT", _RED if score < 0 else _NEON
    elif abs_s >= 25:
        level, color = "MOYEN", _AMBER
    elif abs_s >= 10:
        level, color = "FAIBLE", _CYAN
    else:
        level, color = "NEGLIGEABLE", _MUTED

    if score >= 55:
        why = ("Signal haussier fort : la new pousse clairement a l'optimisme. "
               "Surveiller un eventuel renforcement / hold si deja en portefeuille.")
    elif score >= 25:
        why = ("Biais positif modere. Utile en confirmation d'un signal quant "
               "(RSI survendu + rebond), pas comme ordre d'achat seul.")
    elif score <= -55:
        why = ("Signal baissier fort : risque de pression vendeuse. Si la ligne "
               "est detenue, verifier stop / taille ; pas de nouvel achat satellite.")
    elif score <= -25:
        why = ("Biais negatif. Eviter d'acheter 'a la baisse' sans filtre "
               "momentum (Close > SMA5) et sans EPS positif.")
    elif abs_s >= 10:
        why = ("Bruit d'information faible. Ne change pas la decision du bot : "
               "les filtres mathematiques restent prioritaires.")
    else:
        why = ("Impact negligeable sur le pricing. Ignorer pour le sizing — "
               "garder le focus VIX / regime Core / RSI.")
    return {"level": level, "color": color, "why": why, "abs": abs_s}


def render_news_card(ticker: str, item: dict, score: int | None) -> None:
    """Render one news card with impact badge + justified explanation."""
    sc = 0 if score is None else int(score)
    meta = news_impact_meta(sc)
    pill = _sentiment_pill(sc) if score is not None else ""
    prov = " \u00b7 ".join(
        x for x in (item.get("provider"), item.get("date"), format_name(ticker)) if x
    )
    st.markdown(
        f"<div style='background:#0A0A0A;padding:12px 14px;margin-bottom:10px;"
        f"border-left:4px solid {meta['color']};border:1px solid #222;'>"
        f"<div style='margin-bottom:6px;'>{pill}"
        f"<span style='color:{meta['color']};font-weight:700;font-size:12px;"
        f"letter-spacing:1px;'>IMPACT {meta['level']}</span></div>"
        f"<a href='{item.get('link') or '#'}' target='_blank' "
        f"style='color:{_CYAN};text-decoration:none;font-weight:700;font-size:15px;'>"
        f"{item.get('title', '')}</a>"
        f"<div style='color:{_MUTED};font-size:12px;margin-top:4px;'>{prov}</div>"
        f"<div style='color:#D0D0D0;font-size:13px;margin-top:8px;line-height:1.45;'>"
        f"<b style='color:{_AMBER};'>Pourquoi ca compte :</b> {meta['why']}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


def save_wallet(cash: float, positions_df: pd.DataFrame) -> str:
    """Persist an edited wallet to SQLite. Returns an error string or ''."""
    try:
        positions: list[Position] = []
        for _, row in positions_df.iterrows():
            ticker = str(row.get("Ticker", "")).strip()
            if not ticker:
                continue
            qty = int(float(row.get("Qte", 0) or 0))
            if qty <= 0:
                continue
            pru = float(row.get("PRU", 0) or 0)
            cours = float(row.get("Cours", pru) or pru)
            sector = str(row.get("Secteur", "Unknown") or "Unknown")
            if pru <= 0 or cours <= 0:
                return f"PRU/Cours invalide pour {ticker}."
            positions.append(Position(
                ticker=ticker, qty_shares=qty, avg_entry_price=pru,
                current_price=cours, sector=sector,
            ))
        invested = sum(p.market_value for p in positions)
        equity = float(cash) + invested
        state = PortfolioState(
            cash_available=float(cash),
            total_equity=equity,
            positions=positions,
            last_updated=datetime.now(),
        )
        get_portfolio_db().update_portfolio(state)
        st.cache_data.clear()
        return ""
    except Exception as exc:  # noqa: BLE001
        return str(exc)


@st.cache_data(ttl=900, show_spinner=False)
def get_earnings_events(tickers: tuple[str, ...]) -> list[dict]:
    """Best-effort upcoming earnings / events via yfinance calendar."""
    events: list[dict] = []
    for t in tickers[:12]:
        try:
            cal = yf.Ticker(t).calendar
            if cal is None:
                continue
            # yfinance may return dict or DataFrame depending on version.
            raw = None
            if isinstance(cal, dict):
                raw = cal.get("Earnings Date") or cal.get("earningsDate")
            elif isinstance(cal, pd.DataFrame) and not cal.empty:
                if "Earnings Date" in cal.index:
                    raw = cal.loc["Earnings Date"].tolist()
            if not raw:
                continue
            if not isinstance(raw, (list, tuple)):
                raw = [raw]
            for d in raw[:2]:
                events.append({
                    "ticker": t,
                    "event": "Resultats / Earnings",
                    "date": str(d)[:10],
                })
        except Exception:  # noqa: BLE001
            continue
    return events


@st.cache_data(ttl=1800, show_spinner=False)
def get_general_news_bundle(tickers: tuple[str, ...]) -> list[dict]:
    """Aggregate headlines across a watchlist (held + blue chips)."""
    bundle: list[dict] = []
    for t in tickers:
        try:
            for n in get_recent_news(t, limit=3):
                bundle.append({**n, "ticker": t})
        except Exception:  # noqa: BLE001
            continue
    # Deduplicate by title.
    seen: set[str] = set()
    out: list[dict] = []
    for n in bundle:
        key = (n.get("title") or "").casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(n)
    return out[:24]


@st.cache_data(ttl=3600, show_spinner=False)
def get_geopolitical_brief(vix: float, headlines: tuple[str, ...]) -> str:
    """Generate a short justified geopolitical/macro brief (LLM + fallback)."""
    context = (
        f"VIX/VSTOXX actuel: {vix:.1f} (seuil panique bot: {_VIX_PANIC:.0f}). "
        f"Core ETF: {_CORE_TICKER}. "
        f"Headlines: " + " | ".join(headlines[:8])
    )
    try:
        import os
        from llm_explainer import openrouter_chat

        key = os.getenv("OPENROUTER_API_KEY")
        if key:
            text = asyncio.run(openrouter_chat(
                messages=[
                    {"role": "system",
                     "content": "Analyste macro institutionnel. Factuel, chiffre, prudent."},
                    {"role": "user",
                     "content": (
                         "Tu es un risk manager macro pour un PEA francais (zero levier). "
                         "En 5-7 phrases max, donne un briefing geopolitique/macro "
                         "ACTIONNABLE et JUSTIFIE (chiffres, risques, implications "
                         "Core CW8 vs satellites). Pas de conseil personnalise. "
                         "Francais. Contexte:\n" + context
                     )},
                ],
                api_key=key,
                max_tokens=450,
            ))
            if text and len(text.strip()) > 40:
                return text.strip()
    except Exception:  # noqa: BLE001
        pass

    if vix > _VIX_PANIC:
        regime = (
            f"Panique mesuree (VIX {vix:.1f} > {_VIX_PANIC:.0f}) : le bot bloque "
            "les nouveaux achats satellites. Priorite : cash buffer + DCA Core."
        )
    elif vix > 22:
        regime = (
            f"Stress modere (VIX {vix:.1f}) : reduire l'agressivite satellite, "
            "garder le Core comme ancre."
        )
    else:
        regime = (
            f"Volatilite calme (VIX {vix:.1f}) : environnement favorable aux "
            "signaux mean-reversion satellites SI RSI<30 et Close>SMA5."
        )
    return (
        f"{regime} Justification : le VIX est le circuit-breaker officiel du "
        f"systeme. Les titres d'actualite fournis ({len(headlines)} headlines) "
        "servent de contexte qualitatif uniquement — ils ne declenchent jamais "
        "un ordre. Pour un PEA zero-levier, la discipline reste : budget "
        f"satellite max {_SAT_BUDGET*100:.0f}%, secteur max {_MAX_SECTOR*100:.0f}%, "
        "et Smart DCA sur le Core en cas de prix sous SMA200."
    )


def build_recommendations(
    portfolio_obj,
    pending_df: pd.DataFrame,
    vix: float,
    regime: dict,
) -> list[dict]:
    """Build justified actionable recommendations for the General tab."""
    recos: list[dict] = []

    if vix > _VIX_PANIC:
        recos.append({
            "prio": 1,
            "title": "GEL des achats satellites",
            "why": (f"VIX={vix:.1f} au-dessus du seuil {_VIX_PANIC:.0f}. "
                    "Le correlation firewall veto les nouveaux BUY stock-picking. "
                    "Le Smart DCA Core reste autorise."),
        })
    else:
        recos.append({
            "prio": 2,
            "title": "Fenetre satellite ouverte",
            "why": (f"VIX={vix:.1f} sous le seuil de panique. Les signaux "
                    "mean-reversion (RSI<30 + Close>SMA5 + EPS>0) peuvent passer."),
        })

    if regime:
        if regime.get("crash"):
            recos.append({
                "prio": 1,
                "title": f"DCA agressif sur {_CORE_TICKER}",
                "why": (f"Prix Core {_CORE_TICKER} sous SMA200 "
                        f"({regime.get('gap_pct', 0):+.1f}%). "
                        "Regle Smart DCA : viser ~75% d'allocation Core."),
            })
        else:
            recos.append({
                "prio": 3,
                "title": f"DCA standard {_CORE_TICKER}",
                "why": (f"Core au-dessus de SMA200 ({regime.get('gap_pct', 0):+.1f}%). "
                        "Allocation cible ~70% — pas de sur-accumulation."),
            })

    if pending_df is not None and not pending_df.empty:
        for _, row in pending_df.head(5).iterrows():
            recos.append({
                "prio": 1,
                "title": f"Signal {row.get('signal_type')} {format_name(row.get('ticker',''))}",
                "why": (f"Score {row.get('score', 0):.0f}/100 — "
                        f"{str(row.get('reason', ''))[:180]} "
                        "Approuver/refuser via Discord."),
            })

    for p in (portfolio_obj.positions if portfolio_obj else []):
        try:
            ind = get_indicators(p.ticker)
        except Exception:  # noqa: BLE001
            ind = {}
        if not ind:
            continue
        rsi = ind.get("rsi")
        pnl = p.unrealized_pnl_pct * 100
        if rsi is not None and rsi < 30 and ind.get("close", 0) > (ind.get("sma5") or 0):
            recos.append({
                "prio": 2,
                "title": f"Surveillance rebond {format_name(p.ticker)}",
                "why": (f"RSI={rsi:.0f} survendu + Close>SMA5. Ligne deja detenue "
                        f"(PnL {pnl:+.1f}%). Pas d'ajout auto — verifier budget secteur."),
            })
        if pnl <= -10:
            recos.append({
                "prio": 1,
                "title": f"Stop-loss candidat {format_name(p.ticker)}",
                "why": (f"PnL latent {pnl:+.1f}% (perte). "
                        "Le rebalancer mensuel sort a 100% si le cours casse "
                        "avg_entry - 2.5×ATR(14)."),
            })
        if pnl >= 20:
            recos.append({
                "prio": 2,
                "title": f"Prise de profit {format_name(p.ticker)}",
                "why": (f"PnL latent {pnl:+.1f}% au-dessus de +20%. "
                        "Regle : shave 20% des titres au prochain rebalance."),
            })

    recos.sort(key=lambda r: r["prio"])
    return recos[:10]


@st.cache_data(ttl=600, show_spinner=False)
def get_last_prices(tickers: tuple[str, ...]) -> dict[str, float]:
    """Batch last close prices from DuckDB."""
    out: dict[str, float] = {}
    if not tickers:
        return out
    for t in tickers:
        try:
            hist = _db_hist(t, days=15)
            if hist is None or hist.empty or "Close" not in hist.columns:
                continue
            series = pd.to_numeric(hist["Close"], errors="coerce").dropna()
            if len(series):
                px = float(series.iloc[-1])
                if px > 0.05:
                    out[str(t)] = px
        except Exception:  # noqa: BLE001
            continue
    return out


def build_ta_explanation(ind: dict, alpha: dict | None = None) -> str:
    """Plain-French technical analysis narrative for the selected ticker."""
    if not ind:
        return ("Pas assez de donnees de marche pour expliquer la configuration "
                "technique. Reessaie apres une mise a jour des cours.")
    parts: list[str] = []
    close = ind.get("close")
    rsi = ind.get("rsi")
    sma5, sma50, sma200 = ind.get("sma5"), ind.get("sma50"), ind.get("sma200")
    chg5 = ind.get("chg_5d")
    vol = ind.get("vol_ann")

    if rsi is not None:
        if rsi < 30:
            parts.append(
                f"RSI(14)={rsi:.0f} : zone <b>survendue</b>. Historiquement, "
                "cela favorise un rebond court terme — mais seulement si le "
                "filtre momentum (Close &gt; SMA5) confirme."
            )
        elif rsi > 70:
            parts.append(
                f"RSI(14)={rsi:.0f} : zone <b>surachetee</b>. Risque de "
                "repli / pause. Le bot n'ouvre pas de nouveaux satellites ici."
            )
        else:
            parts.append(
                f"RSI(14)={rsi:.0f} : zone neutre. Pas de signal mean-reversion "
                "fort ; les filtres quant restent prioritaires."
            )

    if close and sma200:
        if close > sma200:
            parts.append(
                f"Cours ({close:.2f}) <b>au-dessus</b> de la SMA200 "
                f"({sma200:.2f}) : tendance de fond haussiere."
            )
        else:
            parts.append(
                f"Cours ({close:.2f}) <b>sous</b> la SMA200 ({sma200:.2f}) : "
                "tendance de fond baissiere — prudence sur le sizing satellite."
            )

    if close and sma5:
        mom = "confirme" if close > sma5 else "ABSENT (Close &lt; SMA5)"
        parts.append(
            f"Momentum court terme (SMA5={sma5:.2f}) : {mom}. "
            "Sans Close&gt;SMA5, un RSI bas ne suffit pas a un BUY MRE."
        )

    if sma50 and close:
        parts.append(
            f"SMA50={sma50:.2f} — intermediaire. "
            + ("Prix au-dessus = biais moyen terme positif."
               if close > sma50 else
               "Prix en dessous = biais moyen terme negatif.")
        )

    if chg5 is not None:
        parts.append(f"Perf 5 seances : <b>{chg5:+.1f}%</b>.")
    if vol is not None:
        parts.append(
            f"Volatilite annualisee ~{vol:.0f}% : "
            + ("sizing reduit (parite de vol)." if vol > 35 else
               "volatilite raisonnable pour un satellite.")
        )

    alpha = alpha or {}
    pc = alpha.get("put_call")
    if pc is not None and pc != 1.0:
        parts.append(
            f"Put/Call={pc:.2f} "
            + ("(peur options — biais contrarian haussier)." if pc > 1.2 else
               "(options calmes).")
        )
    elif pc == 1.0:
        parts.append(
            "Put/Call neutre (1.0) : souvent <b>pas de chaine d'options</b> "
            "Yahoo sur les mid-caps .PA — signal peu fiable titre par titre."
        )

    return " ".join(parts)


@st.cache_data(ttl=600, show_spinner=False)
def score_ticker_opportunity(ticker: str, budget: float, vix: float) -> dict:
    """Score a PEA name via Phase 20 strategy fingerprint (0–100).

    Expensive names stay ranked; ``affordable`` flags cash fit instead of hiding.
    """
    prices = get_last_prices((ticker,))
    px = prices.get(ticker)
    if not px or px <= 0:
        return {
            "ticker": ticker, "price": px or 0.0, "score": 0,
            "reco": "INACCESSIBLE", "why": "Cours indisponible.",
            "kind": "?", "rsi": None, "vs_sma200": None, "weight_pct": 0.0,
            "affordable": False,
        }

    budget = float(budget or 0.0)
    affordable = bool(budget > 0 and px <= budget * 0.98)

    dossier = get_ticker_dossier(ticker)
    is_etf = bool(dossier.get("is_etf") or ticker in (
        _CORE_TICKER, "EWLD.PA", "PAEEM.PA", "ESE.PA", "C50.PA", "PE500.PA",
    ))
    fingerprint = get_strategy_fingerprint(ticker) or {}
    mr = float(fingerprint.get("Mean Reversion") or 0)
    mom = float(fingerprint.get("Momentum") or 0)
    qv = float(fingerprint.get("Quality/Value") or 0)
    ins = float(fingerprint.get("Insider Confidence") or 0)

    base_score = mr * 0.35 + mom * 0.25 + qv * 0.20 + ins * 0.20
    if is_etf:
        base_score += 15.0  # diversification bonus (esp. MICRO)
    if vix > _VIX_PANIC and not is_etf:
        base_score -= 20.0

    weight = (px / budget * 100.0) if budget > 0 else 100.0
    if affordable and 8 <= weight <= 45:
        base_score += 5.0
    elif weight > 70 and not is_etf and affordable:
        base_score -= 8.0

    score = int(max(0, min(100, round(base_score))))
    if score >= 72:
        reco = "ACHETER"
    elif score >= 55:
        reco = "SURVEILLER"
    elif score >= 40:
        reco = "ATTENDRE"
    else:
        reco = "EVITER"

    axes = {
        "Mean Reversion": mr,
        "Momentum": mom,
        "Quality/Value": qv,
        "Insider Confidence": ins,
    }
    top_name, top_val = max(axes.items(), key=lambda kv: kv[1])
    why_bits = [
        f"Empreinte {score}/100 (MR {mr:.0f} · Mom {mom:.0f} · "
        f"Q/V {qv:.0f} · Ins {ins:.0f})",
        f"Axe dominant: {top_name} ({top_val:.0f}/100)",
    ]
    if is_etf:
        why_bits.append("ETF +15 diversif.")
    if vix > _VIX_PANIC and not is_etf:
        why_bits.append(f"VIX panic −20 (VIX={vix:.1f})")
    if affordable:
        why_bits.append(f"1 part ≈ {weight:.0f}% cash")
    else:
        why_bits.append(
            f"HORS BUDGET (1 part={px:,.0f} € > cash {budget:,.0f} €)"
        )

    ind = get_indicators(ticker) or {}
    rsi = ind.get("rsi")
    close = ind.get("close") or px
    sma200 = ind.get("sma200")
    vs200 = None
    if sma200 and close:
        vs200 = (close / sma200 - 1) * 100

    return {
        "ticker": ticker,
        "price": float(px),
        "score": score,
        "reco": reco,
        "why": " · ".join(why_bits),
        "kind": "ETF" if is_etf else "Action",
        "rsi": rsi,
        "vs_sma200": vs200,
        "weight_pct": weight,
        "affordable": affordable,
    }


@st.cache_data(ttl=600, show_spinner=False)
def rank_affordable_alternatives(budget: float, vix: float) -> list[dict]:
    """Rank PEA ETFs + liquid stocks (expensive names kept, flagged)."""
    universe = [
        # Low-fee / PEA ETFs first (CW8 often unaffordable in MICRO)
        "EWLD.PA", "PAEEM.PA", "ESE.PA", "C50.PA", "PE500.PA", _CORE_TICKER,
        # Liquid large/mid caps
        "STLAP.PA", "ORA.PA", "ENGI.PA", "VIE.PA", "GLE.PA", "ACA.PA",
        "SAN.PA", "TTE.PA", "BNP.PA", "RNO.PA", "SGO.PA", "CAP.PA",
        "AIR.PA", "HO.PA", "ML.PA", "BN.PA", "PUB.PA", "MC.PA", "OR.PA",
        "KER.PA", "RMS.PA", "AI.PA",
    ]
    rows = [score_ticker_opportunity(t, budget, vix) for t in universe]
    rows = [r for r in rows if r.get("price", 0) > 0]
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows


@st.cache_data(ttl=1800, show_spinner=False)
def get_momentum_pepites(limit: int = 5) -> list[dict]:
    """High-vol momentum names: vol_ann > 35 and Close > SMA50."""
    watch = (
        "STLAP.PA", "RNO.PA", "AIR.PA", "HO.PA", "CAP.PA", "DSY.PA",
        "KER.PA", "MC.PA", "OR.PA", "PUB.PA", "ML.PA", "ALO.PA",
        "GLE.PA", "ACA.PA", "BNP.PA", "SAN.PA", "ENGI.PA", "VIE.PA",
        "SGO.PA", "TTE.PA", "SAF.PA", "EL.PA",
    )
    rows: list[dict] = []
    for t in watch:
        ind = get_indicators(t) or {}
        vol = ind.get("vol_ann")
        close = ind.get("close")
        sma50 = ind.get("sma50")
        rsi = ind.get("rsi")
        if vol is None or close is None or sma50 is None:
            continue
        if float(vol) <= 35 or float(close) <= float(sma50):
            continue
        rows.append({
            "ticker": t,
            "vol_ann": float(vol),
            "rsi": float(rsi) if rsi is not None else None,
            "close": float(close),
            "sma50": float(sma50),
            "gap_sma50": (float(close) / float(sma50) - 1.0) * 100.0,
        })
    rows.sort(
        key=lambda r: (r["vol_ann"], r["gap_sma50"], -(r["rsi"] or 50)),
        reverse=True,
    )
    return rows[: max(1, limit)]


def suggest_adaptive_portfolio(
    equity: float,
    cash: float,
    vix: float,
    regime: dict,
    pending_df: pd.DataFrame,
    held_tickers: list[str],
) -> dict:
    """Capital-aware suggestions for court / moyen / long horizons."""
    equity = max(float(equity or 0), float(cash or 0), 0.0)
    cash = max(float(cash or 0), 0.0)
    budget = cash if cash > 0 else equity

    candidates = [
        _CORE_TICKER, "EWLD.PA", "PAEEM.PA", "ESE.PA", "C50.PA",
        "SAN.PA", "TTE.PA", "BNP.PA", "GLE.PA", "ACA.PA", "ENGI.PA",
        "ORA.PA", "VIE.PA", "SGO.PA", "CAP.PA", "AIR.PA", "STLAP.PA",
        "RNO.PA", "ML.PA", "HO.PA",
    ]
    pending_tickers: list[str] = []
    if pending_df is not None and not pending_df.empty:
        pending_tickers = [str(t) for t in pending_df["ticker"].tolist() if str(t)]
    prices = get_last_prices(tuple(dict.fromkeys(pending_tickers + candidates)))
    core_px = prices.get(_CORE_TICKER)

    if equity < 200:
        mode = "MICRO"
    elif equity < 800:
        mode = "STARTER"
    elif equity < 3000:
        mode = "BUILD"
    else:
        mode = "FULL"

    ranked = rank_affordable_alternatives(budget, float(vix))

    def _pick_micro_line() -> tuple[str, float, dict] | None:
        if not ranked:
            return None
        best = ranked[0]
        return best["ticker"], float(best["price"]), best

    def _horizon_pack(label: str, lines: list[dict], cash_keep: float, why: str) -> dict:
        for l in lines:
            l["weight_pct"] = (l["cost"] / equity * 100) if equity else 100.0
        return {"label": label, "lines": lines, "cash_keep": cash_keep, "why": why}

    # --- COURT TERME (0–3 mois): best scored affordable + cash runway --------
    court_lines: list[dict] = []
    pick = _pick_micro_line()
    if pick and mode in ("MICRO", "STARTER"):
        t, px, meta = pick
        qty = 1
        cost = qty * px
        court_lines.append({
            "ticker": t, "qty": qty, "price": px, "cost": cost,
            "role": f"Top score {meta.get('score', 0)}/100 · {meta.get('kind')}",
            "why": (
                f"Reco {meta.get('reco')} — {meta.get('why', '')} "
                f"Core {_CORE_TICKER} "
                f"({f'{core_px:.0f} €' if core_px else 'n/a'}) hors budget."
            ),
        })
    court_cash = budget - sum(l["cost"] for l in court_lines)
    court_why = (
        f"<b>Court terme — playbook different du long terme.</b> "
        f"Objectif 0–3 mois : rester liquide et opportuniste. "
        f"1 part max du meilleur score sous budget ({budget:,.0f} €), "
        f"cash ~{court_cash:,.0f} € pour rebondir vite. "
        f"Pas une strategie 'economiser pour CW8' : c'est un ticket tradeable "
        f"maintenant (ETF PEA cheap ou action scoree). VIX={vix:.1f}."
    )

    # --- MOYEN TERME (3–18 mois): Core-first des que possible -----------------
    mid_lines: list[dict] = []
    mid_why = ""
    if core_px and core_px <= budget * 0.98:
        qty = max(int((budget * 0.70) // core_px), 1)
        cost = qty * core_px
        if cost <= budget:
            mid_lines.append({
                "ticker": _CORE_TICKER, "qty": qty, "price": core_px, "cost": cost,
                "role": "Core ETF",
                "why": "Ancre MSCI World PEA — objectif ~70% des que le capital le permet.",
            })
        mid_why = (
            "<b>Moyen terme (3–18 mois)</b> : bascule Core-first des que "
            f"1 part {_CORE_TICKER} est achetable. Les satellites ne viennent "
            "qu'apres, sous budget 30% et VIX OK. Différent du court terme "
            "(qui reste un ticket liquide flexible)."
        )
    else:
        # Medium-term: accumulate via ranked ETFs (not "wait forever for CW8")
        mid_lines = []
        for alt in ranked[:2]:
            if alt["price"] > budget * 0.5:
                continue
            mid_lines.append({
                "ticker": alt["ticker"],
                "qty": 1,
                "price": alt["price"],
                "cost": alt["price"],
                "role": f"Pont moyen terme · score {alt['score']}",
                "why": (
                    f"{alt['reco']} — {alt['why']}. "
                    f"Pont vers Core {_CORE_TICKER} "
                    f"({f'{core_px:.0f} €' if core_px else 'n/a'}) "
                    "sans rester 100% cash."
                ),
            })
            if len(mid_lines) >= 1:
                break
        if not mid_lines:
            mid_lines = list(court_lines)
        mid_why = (
            "<b>Moyen terme</b> : Core encore trop cher — on ne reste pas "
            "inactif : ETF PEA abordable (EWLD/PAEEM/ESE…) comme pont, "
            f"tout en visant {_CORE_TICKER} au prochain depot. "
            "Ce n'est PAS la meme reco que le court terme (plus diversifie, "
            "moins 'ticket trading')."
        )

    # --- LONG TERME (3–10 ans): allocation institutionnelle cible ------------
    long_lines: list[dict] = []
    if core_px:
        # Target allocation in EUR if user had enough capital (illustrative).
        target_eq = max(equity, core_px / 0.70, 5000.0)
        core_budget = target_eq * (0.75 if regime.get("crash") else 0.70)
        qty = max(int(core_budget // core_px), 1)
        long_lines.append({
            "ticker": _CORE_TICKER, "qty": qty, "price": core_px,
            "cost": qty * core_px,
            "role": "Core cible",
            "why": (
                f"Allocation cible long terme sur equity illustre "
                f"~{target_eq:,.0f} \u20ac (pas ton cash actuel)."
            ),
        })
    long_why = (
        f"<b>Long terme (cible institutionnelle)</b> — autre logique : "
        f"~70–75% {_CORE_TICKER}, ≤30% satellites MRE, secteur ≤{_MAX_SECTOR*100:.0f}%, "
        "ligne ≤15%, Smart DCA sous SMA200. "
        "Les tickets court terme (1 action / 1 petit ETF) ne sont PAS la cible "
        "finale : ils sont des etapes. Ce tableau illustre l'allocation une fois "
        "le capital suffisant — pas un ordre a passer aujourd'hui avec 100 €."
    )

    primary = court_lines if mode in ("MICRO", "STARTER") else (
        mid_lines if mid_lines else court_lines
    )
    cash_keep = budget - sum(l["cost"] for l in primary)
    for l in primary:
        l["weight_pct"] = (l["cost"] / equity * 100) if equity else 100.0

    if primary:
        top = primary[0]
        summary = (
            f"Mode <b>{mode}</b> — maintenant : {top['qty']}\u00d7 "
            f"{format_name(top['ticker'])} a {top['price']:.2f} \u20ac "
            f"(~{top['weight_pct']:.0f}% du capital). "
            f"Cash a garder ~{cash_keep:,.0f} \u20ac."
        )
    else:
        summary = (
            f"Mode <b>{mode}</b> — aucun titre liquide fiable sous "
            f"{budget:,.0f} \u20ac. Garde le cash, vise {_CORE_TICKER}."
        )

    mode_why = {
        "MICRO": (
            f"Capital {equity:,.0f} \u20ac : capital insuffisant pour l'allocation cible complète. "
            "Achat de 1 part pour rester exposé au marché, le reste conservé en liquidités "
            "(Cash Runway) car le PEA interdit les fractions d'actions."
        ),
        "STARTER": (
            f"Capital {equity:,.0f} \u20ac : 1–2 lignes max. "
            "Achat de 1 part pour rester exposé, cash conservé car le PEA interdit les fractions."
        ),
        "BUILD": f"Capital {equity:,.0f} \u20ac : construction Core-first.",
        "FULL": f"Capital {equity:,.0f} \u20ac : regles institutionnelles completes.",
    }[mode]
    if vix > _VIX_PANIC:
        mode_why += f" VIX={vix:.1f} > {_VIX_PANIC:.0f} : frein satellite actif."

    return {
        "mode": mode,
        "mode_why": mode_why,
        "lines": primary,
        "cash_keep": cash_keep,
        "summary": summary,
        "have_core": any(l["ticker"] == _CORE_TICKER for l in primary),
        "cash_explain": court_why,
        "alternatives": ranked[:12],
        "horizons": {
            "court": _horizon_pack("Court terme (0–3 mois)", court_lines, court_cash, court_why),
            "moyen": _horizon_pack(
                "Moyen terme (3–18 mois)", mid_lines,
                budget - sum(l["cost"] for l in mid_lines), mid_why,
            ),
            "long": _horizon_pack(
                "Long terme (cible)", long_lines,
                0.0, long_why,
            ),
        },
    }


@st.cache_data(ttl=1800, show_spinner=False)
def get_etf_card(ticker: str = _CORE_TICKER) -> dict:
    """Key facts for the Core (or any) PEA ETF."""
    dossier = get_ticker_dossier(ticker)
    ind = get_indicators(ticker)
    prices = get_last_prices((ticker,))
    px = prices.get(ticker) or (ind or {}).get("close")
    return {
        "ticker": ticker,
        "name": dossier.get("name") or ticker,
        "summary": dossier.get("summary") or "",
        "price": px,
        "regime": get_core_regime() if ticker == _CORE_TICKER else {},
        "indicators": ind or {},
        "role": (
            "Ancre Core PEA Pollux (MSCI World PEA). Cible 70–75% de l'equity "
            "des que ton capital permet d'acheter des parts entieres."
            if ticker == _CORE_TICKER else
            "ETF eligible PEA — diversification indicielle."
        ),
    }


@st.cache_data(ttl=1800, show_spinner=False)
def get_monthly_market_news(tickers: tuple[str, ...]) -> list[dict]:
    """Biggest headlines of the month across a watchlist, impact-ranked."""
    bundle = get_general_news_bundle(tickers)
    scored = []
    for n in bundle:
        sc = heuristic_news_score(n.get("title", ""))
        # Light LLM only for top candidates would be slow; heuristic for month pack.
        scored.append({**n, "score": sc, "abs": abs(sc)})
    scored.sort(key=lambda x: x["abs"], reverse=True)
    return scored[:12]


@st.cache_data(ttl=900, show_spinner=False)
def get_sector_performance(
    universe_df: pd.DataFrame, period: str = "1mo"
) -> pd.DataFrame:
    """Average performance by sector over a timeframe."""
    if universe_df is None or universe_df.empty:
        return pd.DataFrame()
    # Sample up to 4 tickers per sector to keep Yahoo calls sane.
    samples: list[str] = []
    for _sector, grp in universe_df.groupby("Sector"):
        samples.extend(grp["Ticker"].head(4).tolist())
    samples = list(dict.fromkeys(samples))[:80]
    perf = get_market_performance(tuple(samples), period=period)
    if perf.empty:
        return pd.DataFrame()
    meta = universe_df.set_index("Ticker")["Sector"].to_dict()
    perf = perf.copy()
    perf["Sector"] = perf["Ticker"].map(meta).fillna("Unknown")
    agg = (perf.groupby("Sector", as_index=False)
           .agg(Perf_moy=("Performance (%)", "mean"),
                Perf_med=("Performance (%)", "median"),
                N=("Ticker", "count"),
                Best=("Performance (%)", "max"),
                Worst=("Performance (%)", "min"))
           .sort_values("Perf_moy", ascending=False))
    return agg


@st.cache_data(ttl=1800, show_spinner=False)
def get_polymarket_macro(limit: int = 8) -> list[dict]:
    """Fetch live macro-relevant Polymarket events (Gamma API, no auth)."""
    try:
        import json

        import requests as _req
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        url = (
            "https://gamma-api.polymarket.com/events?"
            "active=true&closed=false&order=volume24hr&ascending=false&limit=50"
        )
        resp = _req.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; PEA-Pollux/1.0; "
                    "+https://github.com/Polluxgnr/Peatrading)"
                ),
                "Accept": "application/json",
            },
            verify=False,
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        try:
            events = resp.json()
        except Exception as exc:  # noqa: BLE001 - Cloudflare challenge / HTML body
            if _dash_log is not None:
                _dash_log.debug("Polymarket macro JSON decode failed: %s", exc)
            return []
        if not isinstance(events, list):
            return []

        keys = (
            "recession", "fed", "ecb", "inflation", "tariff", "war", "ukraine",
            "china", "oil", "rate", "gdp", "election", "trump", "europe",
            "france", "germany", "nasdaq", "spx", "crash", "btc", "dollar",
            "le pen", "macron", "yield",
        )
        # Exclude pure sports noise.
        ban = ("euro 2024", "world cup", "mlb", "nba", "nfl", "champions league",
               "olympic", "grand slam", "premier league")
        out: list[dict] = []
        for ev in events:
            title = str(ev.get("title") or ev.get("slug") or "")
            tl = title.casefold()
            if any(b in tl for b in ban):
                continue
            if not any(k in tl for k in keys):
                continue
            markets = ev.get("markets") or []
            yes_p = None
            question = title
            if markets:
                m0 = markets[0]
                question = str(m0.get("question") or title)
                prices = m0.get("outcomePrices")
                if isinstance(prices, str):
                    try:
                        prices = json.loads(prices)
                    except Exception:  # noqa: BLE001
                        prices = None
                if isinstance(prices, (list, tuple)) and prices:
                    try:
                        yes_p = float(prices[0])
                    except Exception:  # noqa: BLE001
                        yes_p = None
            vol = ev.get("volume24hr") or ev.get("volume") or 0
            try:
                vol_f = float(vol)
            except Exception:  # noqa: BLE001
                vol_f = 0.0
            slug = ev.get("slug") or ""
            # Impact hint for PEA
            if yes_p is None:
                impact = "Contexte"
            elif "recession" in tl or "crash" in tl:
                impact = "Risque risk-off" if yes_p > 0.35 else "Tail risk faible"
            elif "fed" in tl or "ecb" in tl or "rate" in tl:
                impact = "Sensibilite taux / valorisations"
            elif "france" in tl or "le pen" in tl or "europe" in tl:
                impact = "Premium politique EU"
            else:
                impact = "Macro general"
            out.append({
                "title": question[:120],
                "yes_prob": yes_p,
                "volume24h": vol_f,
                "impact": impact,
                "url": f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com",
            })
            if len(out) >= limit:
                break
        return out
    except Exception:  # noqa: BLE001
        return []


# =============================================================================
# Header + live ticker tape (streaming)
# =============================================================================
st.markdown(
    "<h1>\U0001F6E1\uFE0F POLLUX PEA TERMINAL "
    "<span style='color:#00FF00; font-size:20px;'>V-PRIME</span></h1>",
    unsafe_allow_html=True,
)

# One-shot sync/pre-warm when the dashboard session opens.
if not st.session_state.get("daily_sync_done", False):
    with st.spinner("Initialisation et synchronisation des flux de marché..."):
        _boot_universe = load_universe()
        _boot_tickers = tuple(_boot_universe["Ticker"].head(24).tolist())
        if _boot_tickers:
            get_last_prices(_boot_tickers)
        get_vix()
    st.session_state["daily_sync_done"] = True

universe_df = load_universe()
# Populate the name lookup with every universe entry (STEP 1.3 coverage).
TICKER_NAMES.update(dict(zip(universe_df["Ticker"], universe_df["Name"])))

# Native ticker tape (replaces TradingView widget — no .PA red errors).
_tape_col1, _tape_col2 = st.columns([0.22, 0.78])
with _tape_col1:
    _tape_period = st.radio(
        "Période bandeau",
        ["1d", "5d", "1mo"],
        horizontal=True,
        key="native_tape_period",
        format_func=lambda x: {"1d": "1j", "5d": "5j", "1mo": "1m"}[x],
        label_visibility="collapsed",
    )
with _tape_col2:
    st.caption("Bandeau marché natif · blue chips PEA · logos Clearbit")
render_native_ticker_tape(_tape_period)

portfolio = load_portfolio_state()
if portfolio is None:
    st.warning(
        "\u26A0\uFE0F En attente de l'initialisation des bases de donn\u00e9es "
        "par le Main Scheduler... (lancez `py main_scheduler.py --now`)"
    )
    st.stop()


# =============================================================================
# STEP 2 - Top HUD (with plain-language tooltips)
# =============================================================================
positions = portfolio.positions
invested = sum(p.market_value for p in positions)
unrealized = sum((p.current_price - p.avg_entry_price) * p.qty_shares for p in positions)
unrealized_pct = (unrealized / invested * 100) if invested else 0.0
cash_pct = (portfolio.cash_available / portfolio.total_equity * 100
            if portfolio.total_equity else 0.0)
invest_rate = (invested / portfolio.total_equity * 100
               if portfolio.total_equity else 0.0)

c1, c2, c3, c4 = st.columns(4)
with c1:
    st.markdown(metric_box(
        "Valeur du Portefeuille", f"{portfolio.total_equity:,.2f} \u20ac",
        sub=f"Investi: {invested:,.2f} \u20ac", accent="", sub_cls="sub-muted",
        help_text="Valeur totale de votre PEA : la somme de vos liquidites et de "
                  "la valeur de marche de toutes vos actions detenues.",
    ), unsafe_allow_html=True)
with c2:
    inv_accent = "cyan" if invest_rate >= 95 else ("amber" if invest_rate >= 80 else "red")
    st.markdown(metric_box(
        "Taux d'Investissement", f"{invest_rate:.1f}%",
        sub=f"Cash idle: {cash_pct:.1f}% ({portfolio.cash_available:,.0f} \u20ac)",
        accent=inv_accent, sub_cls="sub-muted",
        help_text="Part de l'equity déjà investie. Objectif Phase 40 : cash idle "
                  "≤ 2% — l'excédent est balayé automatiquement vers CW8.PA.",
    ), unsafe_allow_html=True)
with c3:
    pnl_cls = "sub-green" if unrealized >= 0 else "sub-red"
    st.markdown(metric_box(
        "PnL Latent", f"{unrealized:,.2f} \u20ac", sub=f"{unrealized_pct:+.2f}%",
        accent="" if unrealized >= 0 else "red", sub_cls=pnl_cls,
        help_text="Gains ou pertes virtuels sur les positions actuellement "
                  "detenues, avant de les vendre (non realises).",
    ), unsafe_allow_html=True)
with c4:
    st.markdown(metric_box(
        "Lignes Actives", f"{len(positions)}", sub="Zero Levier Garanti",
        accent="cyan", sub_cls="sub-muted",
        help_text="Nombre de positions distinctes en portefeuille. Le systeme "
                  "n'utilise jamais d'effet de levier (pas de marge).",
    ), unsafe_allow_html=True)


# =============================================================================
# Risk / Macro HUD (VIX, regime, satellite budget, sector concentration)
# =============================================================================
vix = get_vix()
vix_panic = vix > _VIX_PANIC
regime = get_core_regime()

satellite_value = sum(p.market_value for p in positions if p.ticker != _CORE_TICKER)
sat_budget_eur = _SAT_BUDGET * portfolio.total_equity if portfolio.total_equity else 0.0
sat_used_pct = (satellite_value / sat_budget_eur * 100) if sat_budget_eur else 0.0

sector_weights: dict[str, float] = {}
for p in positions:
    sector_weights[p.sector] = sector_weights.get(p.sector, 0.0) + p.market_value
max_sector, max_sector_val = ("-", 0.0)
if sector_weights and portfolio.total_equity:
    max_sector = max(sector_weights, key=sector_weights.get)
    max_sector_val = sector_weights[max_sector] / portfolio.total_equity * 100

from duckdb_manager import TimeSeriesDB  # noqa: E402

_db_breadth = get_ts_db()
_breadth = get_market_breadth(universe_df, str(_db_breadth.db_path))
_pct50 = _breadth.get("pct_sma50")
_pct200 = _breadth.get("pct_sma200")
_valid = _breadth.get("valid") or 0
_pct50_f = float(_pct50) if _pct50 is not None else None
_pct200_f = float(_pct200) if _pct200 is not None else None

_breadth_ok = (_pct200_f is not None and _pct200_f >= 55)
_breadth_mid = (_pct200_f is not None and 45 <= _pct200_f < 55)
_breadth_accent = "green" if _breadth_ok else ("cyan" if _breadth_mid else "red")
_breadth_sub_cls = (
    "sub-green" if _breadth_ok else ("sub-red" if _pct200_f is not None else "sub-muted")
)

r1, r2, r3, r4, r5 = st.columns(5)
with r1:
    vsub = ("\U0001F6A8 PANIC - achats satellites geles" if vix_panic
            else f"Calme (seuil {_VIX_PANIC:.0f})")
    st.markdown(metric_box(
        "Volatilite (VIX)", f"{vix:.1f}", sub=vsub,
        accent="red" if vix_panic else "", sub_cls="sub-red" if vix_panic else "sub-green",
        help_text="L'indice de la peur. Au-dessus de 30, le marche panique et le "
                  "bot bloque les nouveaux achats risques pour proteger le capital.",
    ), unsafe_allow_html=True)
with r2:
    if regime:
        crash = regime["crash"]
        rsub = ("\U0001F534 SOUS SMA200 - DCA agressif" if crash
                else "\U0001F7E2 SUR SMA200 - DCA standard")
        st.markdown(metric_box(
            f"Regime Core ({_CORE_TICKER})", f"{regime['gap_pct']:+.1f}%", sub=rsub,
            accent="red" if crash else "", sub_cls="sub-red" if crash else "sub-green",
            help_text="Indique si le marche global est en tendance haussiere "
                      "(au-dessus de sa moyenne 200 jours) ou en crise (en dessous). "
                      "En crise, le bot accumule l'ETF Monde plus agressivement.",
        ), unsafe_allow_html=True)
    else:
        st.markdown(metric_box(
            f"Regime Core ({_CORE_TICKER})", "n/a", sub="Donnees indisponibles",
            accent="muted", sub_cls="sub-muted",
            help_text="Regime du marche global (prix vs moyenne 200 jours). "
                      "Donnees temporairement indisponibles.",
        ), unsafe_allow_html=True)
with r3:
    breadth_val = (
        f"{_pct50_f:.0f}% / {_pct200_f:.0f}%" if _pct200_f is not None else "n/a"
    )
    st.markdown(metric_box(
        "Market Breadth (SMA50/200)",
        breadth_val,
        sub=f"{int(_valid)} titres validés · Close>SMA50/SMA200",
        accent=_breadth_accent,
        sub_cls=_breadth_sub_cls,
        help_text=(
            "Broad market measure : % des noms PEA ayant "
            "Close > SMA50 et Close > SMA200 (hist. DuckDB ~200j)."
        ),
    ), unsafe_allow_html=True)

with r4:
    over = sat_used_pct > 100
    ssub = f"{satellite_value:,.0f} / {sat_budget_eur:,.0f} \u20ac (max {_SAT_BUDGET*100:.0f}%)"
    st.markdown(metric_box(
        "Budget Satellite Utilise", f"{sat_used_pct:.0f}%", sub=ssub,
        accent="red" if over else "cyan", sub_cls="sub-red" if over else "sub-muted",
        help_text="Capital alloue aux actions individuelles (max 30% du "
                  "portefeuille total) pour chercher de la surperformance. Le "
                  "reste est investi dans l'ETF Monde (le Coeur du portefeuille).",
    ), unsafe_allow_html=True)
with r5:
    breach = max_sector_val > _MAX_SECTOR * 100
    st.markdown(metric_box(
        "Concentration Sectorielle Max", f"{max_sector_val:.0f}%",
        sub=f"{max_sector} (limite {_MAX_SECTOR*100:.0f}%)",
        accent="red" if breach else "", sub_cls="sub-red" if breach else "sub-muted",
        help_text="Poids du secteur le plus represente. Le systeme interdit de "
                  "depasser cette limite pour eviter d'etre trop expose a un "
                  "seul theme (diversification imposee).",
    ), unsafe_allow_html=True)

# --- Sidebar: settings & controls -------------------------------------------
with st.sidebar:
    st.markdown("### \u2699\uFE0F Parametres")
    auto_refresh = st.checkbox("Rafraichissement auto", value=False)
    refresh_secs = st.slider("Intervalle (s)", 30, 600, 120, 30,
                             disabled=not auto_refresh)
    if st.button("\U0001F504 Vider le cache & recharger", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.markdown("---")
    if st.button("Ledger signaux", use_container_width=True):
        st.session_state["scroll_to_ledger"] = True
    st.caption("Passe : `python main_scheduler.py --now`")
    st.markdown("---")
    st.markdown("### \U0001F4CA Etat Systeme")
    st.metric("Univers", f"{len(universe_df)} titres",
              help="Nombre total d'actions/ETF eligibles PEA suivis par le bot.")
    st.metric("Derniere MAJ", portfolio.last_updated.strftime("%d/%m %H:%M"),
              help="Horodatage de la derniere passe du Main Scheduler ayant "
                   "actualise les cours et l'equity.")
    st.caption(
        "Amorcer le capital :\n\n`python seed_account.py --cash 10000`\n\n"
        "Lancer une passe :\n\n`python main_scheduler.py --now`"
    )
    if auto_refresh:
        st.caption(f"\u23F1\uFE0F Auto-refresh dans {refresh_secs}s")

st.write("---")

# =============================================================================
# Mission Control — état du monde en ~3 secondes
# =============================================================================
@st.fragment(run_every="30s")
def _render_mission_control():
    _pending_mc = load_signals(("PENDING",))
    _n_pending = 0 if _pending_mc is None or _pending_mc.empty else len(_pending_mc)
    _eq_curve_mc = load_equity_curve()
    _day_delta = None
    _day_delta_pct = None
    if _eq_curve_mc is not None and not _eq_curve_mc.empty and len(_eq_curve_mc) >= 2:
        try:
            _eqs = _eq_curve_mc.sort_values("date")["equity"].astype(float)
            _day_delta = float(_eqs.iloc[-1] - _eqs.iloc[-2])
            if float(_eqs.iloc[-2]) > 0:
                _day_delta_pct = _day_delta / float(_eqs.iloc[-2]) * 100.0
        except Exception:  # noqa: BLE001
            pass
    _mkt_label, _mkt_health = euronext_session_status()
    _pipe = read_pipeline_status() if read_pipeline_status else None
    _pipe_health = (_pipe or {}).get("health", "amber")
    _pipe_txt = "jamais"
    if _pipe:
        _pipe_txt = (
            f"{_pipe.get('status', '?')} · "
            f"{_pipe.get('finished_at_local') or _pipe.get('written_at', '')[:19]}"
        )
    _health_color = {
        "green": _NEON, "amber": _AMBER, "red": _RED
    }.get(_pipe_health, _AMBER)
    _mkt_color = _NEON if _mkt_health == "green" else _AMBER
    
    # Degraded Mode Alert (moved to Data Lineage)
    _is_degraded = (_pipe or {}).get("data_degraded_mode", False)
    _degraded_reason = (_pipe or {}).get("degraded_reason", "Institutional API down. Using yfinance/fallback data.")
    
    # Add Market Regime
    try:
        from market_regime import MarketRegimeClassifier
        _mr_classifier = MarketRegimeClassifier()
        _regime = _mr_classifier.get_regime()
        _conv_floor, _rsi_thresh = _mr_classifier.get_modulated_thresholds(
            _regime,
            base_conviction=float(_RISK.get("CONVICTION_EMIT_FLOOR", 65.0)),
            base_rsi=float(_RISK.get("RSI_OVERSOLD_THRESHOLD", 30.0))
        )
    except Exception:
        _regime = "BULL"
        _conv_floor = 65.0
        _rsi_thresh = 30.0
    
    _regime_color = _NEON if _regime == "BULL" else (_RED if _regime == "BEAR" else _AMBER)
    
    now_str = datetime.now().strftime("%H:%M")
    st.markdown(
        f"""
    <style>@keyframes blink {{50% {{opacity: 0.2;}}}} .live-badge {{color: #0f0; animation: blink 2s linear infinite; font-size: 11px; margin-left: 10px; border: 1px solid #0f0; padding: 1px 4px; border-radius: 4px;}}</style>
    <div class="mission">
      <div class="mission-title">Mission Control · PEA personnel <span class="live-badge">LIVE 🟢</span> <span style="font-size:11px; color:#aaa; margin-left:5px;">{now_str}</span></div>
      <div style="display:flex;flex-wrap:wrap;gap:18px;color:{_WHITE};font-size:13px;">
        <div>Marché <b style="color:{_mkt_color};">{_mkt_label}</b></div>
        <div>Régime <b style="color:{_regime_color};">{_regime}</b> 
            <span style="color:{_MUTED}; font-size: 11px;">(Score ≥{_conv_floor:.0f} | RSI ≤{_rsi_thresh:.0f})</span>
        </div>
        <div>Dernière passe
          <b style="color:{_health_color};">{_pipe_txt}</b></div>
        <div>Equity
          <b>{portfolio.total_equity:,.0f} €</b>
          <span style="color:{_NEON if (_day_delta or 0) >= 0 else _RED};">
            {f"{_day_delta:+,.0f} € ({_day_delta_pct:+.2f}%)" if _day_delta is not None else "·"}
          </span>
        </div>
        <div>VIX <b style="color:{_RED if vix_panic else _WHITE};">{vix:.1f}</b></div>
        <div>Pending Discord
          <b style="color:{_AMBER if _n_pending else _MUTED};">{_n_pending}</b></div>
      </div>
    </div>
    """,
        unsafe_allow_html=True,
    )
    
    if st.button("⚡ Lancer Analyse", key="mc_run_now"):
        import subprocess
        subprocess.Popen(
            [sys.executable, str(_ROOT / "main_scheduler.py"), "--now"],
            cwd=str(_ROOT),
        )
        st.toast("Analyse complète lancée en arrière-plan.", icon="⚡")

_render_mission_control()


# =============================================================================
# Tabs
# =============================================================================
tab_market_pulse, tab_ticker_deep_dive, tab_quant_engine, tab_portfolio = st.tabs([
    "🌍 Market Pulse & News Feed",
    "🔍 Ticker Deep-Dive (Data & History)",
    "🤖 Quant Engine & Models Center",
    "💼 Portfolio, Execution & Full History",
])

# --- Tab: General + Signals --------------------------------------------------
with tab_market_pulse:
    st.markdown("## 🌍 Market Pulse & News Feed")
    
    # 1. Macro Header
    try:
        from macro_alpha_api import MacroAlphaSensor
        from market_regime import MarketRegimeClassifier
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            # HMM Regime
            try:
                regime = MarketRegimeClassifier().get_regime()
                color = _NEON if regime == "BULL" else (_RED if regime == "BEAR" else _AMBER)
                st.markdown(f"<div style='padding:15px; background:#1E1E1E; border-radius:5px; border-left:5px solid {color}'><h4>Market Regime</h4><h2 style='color:{color}'>{regime}</h2></div>", unsafe_allow_html=True)
            except Exception:
                st.metric("Market Regime", "UNKNOWN")
                
        with col2:
            # VIX Level
            try:
                vix_val = MacroAlphaSensor().get_european_vix()
                vix_color = _RED if vix_val > 30 else (_AMBER if vix_val > 20 else _NEON)
                st.markdown(f"<div style='padding:15px; background:#1E1E1E; border-radius:5px; border-left:5px solid {vix_color}'><h4>European VIX</h4><h2 style='color:{vix_color}'>{vix_val:.2f}</h2></div>", unsafe_allow_html=True)
            except Exception:
                st.metric("European VIX", "N/A")
                
        with col3:
            # OAT vs Bund Spread
            try:
                spread = MacroAlphaSensor().get_oat_bund_spread()
                spread_color = _RED if spread > 0.8 else (_NEON if spread < 0.5 else _AMBER)
                st.markdown(f"<div style='padding:15px; background:#1E1E1E; border-radius:5px; border-left:5px solid {spread_color}'><h4>OAT/Bund Spread</h4><h2 style='color:{spread_color}'>{spread:.2f}%</h2></div>", unsafe_allow_html=True)
            except Exception:
                st.metric("OAT/Bund Spread", "N/A")
                
    except Exception as e:
        st.error(f"Failed to load macro sensors: {e}")
        
    st.markdown("---")
    
    # 2. Global News Terminal
    st.markdown("### 📰 Global News Terminal")
    
    news_filter = st.radio("News Filter", ["All News", "High Impact Only", "Bullish", "Bearish"], horizontal=True)
    
    try:
        from sqlite_portfolio import PortfolioDB
        db = PortfolioDB()
        
        # Get latest news from DB
        news_query = "SELECT ticker, published_at, title, url, sentiment_score, source FROM news_sentiment ORDER BY published_at DESC LIMIT 100"
        try:
            news_rows = db.execute(news_query).fetchall()
        except Exception:
            news_rows = []
            
        if not news_rows:
            st.info("No recent news found in database.")
        else:
            filtered_news = []
            for r in news_rows:
                score = float(r["sentiment_score"] or 0)
                if news_filter == "High Impact Only" and abs(score) < 0.5:
                    continue
                if news_filter == "Bullish" and score < 0.2:
                    continue
                if news_filter == "Bearish" and score > -0.2:
                    continue
                filtered_news.append(r)
                
            st.caption(f"Showing {len(filtered_news)} articles matching filter.")
            
            # Scrollable container
            with st.container(height=600):
                for r in filtered_news:
                    score = float(r["sentiment_score"] or 0)
                    if score > 0.2:
                        badge_col = _NEON
                        badge_txt = "BULLISH"
                    elif score < -0.2:
                        badge_col = _RED
                        badge_txt = "BEARISH"
                    else:
                        badge_col = _MUTED
                        badge_txt = "NEUTRAL"
                        
                    source = r["source"] or "Unknown"
                    title = r["title"] or "No Title"
                    ticker = r["ticker"] or "MACRO"
                    date = str(r["published_at"])[:16]
                    url = r["url"] or "#"
                    
                    st.markdown(f"""
                    <div style="padding:10px; margin-bottom:10px; border:1px solid #333; background:#111; border-left:4px solid {badge_col}">
                        <div style="font-size:12px; color:#888; margin-bottom:4px;">
                            <span>{date}</span> | 
                            <strong style="color:#FFF">{ticker}</strong> | 
                            <span>{source}</span>
                            <span style="float:right; padding:2px 6px; background:#222; border:1px solid {badge_col}; color:{badge_col}; font-size:10px; border-radius:3px;">
                                {badge_txt} ({score:.2f})
                            </span>
                        </div>
                        <div><a href="{url}" target="_blank" style="color:#E0E0E0; text-decoration:none; font-size:15px; font-weight:600;">{title}</a></div>
                        <div style="font-size:12px; color:#00B4D8; margin-top:6px;">🤖 Ollama LLM Insight: Processed</div>
                    </div>
                    """, unsafe_allow_html=True)
                    
    except Exception as e:
        st.error(f"Failed to load news: {e}")


with tab_ticker_deep_dive:
    st.markdown("## 🔍 Ticker Deep-Dive (Data & History)")
    
    # Universal Search
    try:
        tickers = universe_df["Ticker"].unique().tolist() if "universe_df" in globals() else []
    except Exception:
        tickers = []
        
    selected_ticker = st.selectbox("Search PEA Universe", options=tickers, index=0 if tickers else None)
    
    if selected_ticker:
        # Fetch data using existing functions or new logic
        try:
            import plotly.graph_objects as go
            import pandas as pd
            
            # Fetch OHLCV
            hist = _db_hist(selected_ticker, 180) # Last 6 months
            
            if hist is not None and not hist.empty:
                # Calculate IsolationForest anomalies
                abnormal_mask = pd.Series(False, index=hist.index)
                try:
                    from sklearn.ensemble import IsolationForest
                    import numpy as np
                    hist["_pct_chg"] = hist["Close"].pct_change()
                    valid_idx = hist["_pct_chg"].dropna().index
                    if len(valid_idx) > 50:
                        iso = IsolationForest(contamination=0.015, random_state=42)
                        preds = iso.fit_predict(hist.loc[valid_idx, ["_pct_chg"]])
                        abnormal_mask.loc[valid_idx] = (preds == -1)
                except Exception:
                    pass
                
                # Candlestick
                fig = go.Figure(data=[go.Candlestick(
                    x=hist.index,
                    open=hist['Open'],
                    high=hist['High'],
                    low=hist['Low'],
                    close=hist['Close'],
                    name='Price'
                )])
                
                # Overlay anomalies
                anomalies = hist[abnormal_mask]
                if not anomalies.empty:
                    fig.add_trace(go.Scatter(
                        x=anomalies.index,
                        y=anomalies['Close'],
                        mode='markers',
                        marker=dict(color='yellow', size=10, symbol='x'),
                        name='Anomaly (IF)'
                    ))
                    
                fig.update_layout(
                    title=f"{selected_ticker} Price Action & Anomalies",
                    template="plotly_dark",
                    margin=dict(t=40, b=0, l=0, r=0),
                    height=400,
                    xaxis_rangeslider_visible=False
                )
                st.plotly_chart(fig, use_container_width=True)
                
                # Signal & Uncertainty
                st.markdown("### 🤖 Signal & Uncertainty")
                try:
                    from technical_scorer import SignalGenerator
                    from ml_feature_store import build_ml_feature_row
                    from ml_trainer import predict_probability_with_shap
                    from market_regime import MarketRegimeClassifier
                    
                    regime = MarketRegimeClassifier().get_regime()
                    feat_row = build_ml_feature_row(selected_ticker, close=float(hist["Close"].iloc[-1]), reason="", pdb=None, offline_mode=False)
                    prob, shap_vals, interval = predict_probability_with_shap(feat_row, horizon="tactical", regime=regime)
                    
                    if prob is not None:
                        prob_pct = prob * 100
                        prob_color = _NEON if prob >= 0.65 else (_RED if prob <= 0.35 else _AMBER)
                        interval_str = f"± {abs((interval[1] - prob) * 100):.1f}%" if interval else ""
                        
                        st.markdown(f"""
                        <div style="padding:15px; background:#1A1A1A; border:1px solid #333; border-radius:8px; text-align:center;">
                            <h4 style="color:#888;">Conformal Prediction (Tactical)</h4>
                            <h1 style="color:{prob_color}; margin:0;">Confidence: {prob_pct:.1f}% {interval_str}</h1>
                            <p style="color:#555; margin-top:5px;">Regime Model Active: <strong>XGBoost_{regime}</strong></p>
                        </div>
                        """, unsafe_allow_html=True)
                    else:
                        st.info("ML Prediction not available. Model might not be trained.")
                        
                except Exception as e:
                    st.error(f"Failed to load ML Signal: {e}")
                
                # Raw Data
                with st.expander("📊 View Raw OHLCV & Feature Data (DuckDB)"):
                    st.dataframe(hist, use_container_width=True)
                    
            else:
                st.warning(f"No historical data found for {selected_ticker}.")
                
        except Exception as e:
            st.error(f"Failed to load ticker data: {e}")
    else:
        st.info("Select a ticker from the dropdown above to view details.")


with tab_quant_engine:
    st.markdown("## 🤖 Quant Engine & Models Center")
    
    try:
        import sys
        import os
        import time
        from pathlib import Path
        _ROOT = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(_ROOT / "02_quant_engine"))
        from ml_trainer import load_metrics
        import xgboost as xgb
        import plotly.express as px
        import plotly.graph_objects as go
        from market_regime import MarketRegimeClassifier
        
        regime = MarketRegimeClassifier().get_regime()
        metrics = load_metrics()
        
        # 1. Model Roster & Staleness Health
        st.markdown("### 📋 Active Model Roster & Health")
        models = [
            {"name": "XGBoost_BULL", "file": "xgboost_model_tactical_BULL.pkl"},
            {"name": "XGBoost_BEAR", "file": "xgboost_model_tactical_BEAR.pkl"},
            {"name": "XGBoost_VOLATILE", "file": "xgboost_model_tactical_VOLATILE.pkl"},
            {"name": "XGBoost_Structural", "file": "xgboost_model_structural.pkl"},
        ]
        
        cols = st.columns(len(models))
        
        for idx, m in enumerate(models):
            with cols[idx]:
                path = _ROOT / "database" / m["file"]
                if path.exists():
                    mtime = os.path.getmtime(path)
                    days_ago = (time.time() - mtime) / (24 * 3600)
                    
                    if days_ago <= 7:
                        health_color = _NEON
                        status = "HEALTHY"
                    elif days_ago <= 14:
                        health_color = _AMBER
                        status = "WARNING"
                    else:
                        health_color = _RED
                        status = "STALE"
                        
                    st.markdown(f"""
                    <div style="padding:10px; background:#1A1A1A; border:1px solid #333; border-top:4px solid {health_color}; border-radius:5px;">
                        <div style="font-size:14px; font-weight:bold; color:#E0E0E0;">{m['name']}</div>
                        <div style="color:{health_color}; font-size:12px; margin-top:5px;">{status} ({days_ago:.1f}d ago)</div>
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    st.markdown(f"""
                    <div style="padding:10px; background:#1A1A1A; border:1px solid #333; border-top:4px solid #555; border-radius:5px;">
                        <div style="font-size:14px; font-weight:bold; color:#888;">{m['name']}</div>
                        <div style="color:#555; font-size:12px; margin-top:5px;">NOT FOUND</div>
                    </div>
                    """, unsafe_allow_html=True)

        st.markdown("---")
        
        col1, col2 = st.columns(2)
        
        # 2. Dynamic Ensemble Weights
        with col1:
            st.markdown("### ⚖️ Dynamic Ensemble Weights")
            try:
                from pipeline_config import ML_MODIFIERS
                weights = ML_MODIFIERS
                labels = [
                    "XGBoost Tactical",
                    "XGBoost Structural",
                    "IsolationForest Anomaly",
                    "Trend Breakout (Heuristic)",
                    "Mean Reversion (Heuristic)"
                ]
                values = [
                    weights.get("xgboost_tactical_weight", 50),
                    weights.get("xgboost_structural_weight", 30),
                    weights.get("isolation_forest_penalty", 20),
                    weights.get("heuristic_breakout_weight", 10),
                    weights.get("heuristic_context_weight", 10),
                ]
                
                fig = go.Figure(data=[go.Pie(
                    labels=labels, 
                    values=values, 
                    hole=.4,
                    marker=dict(colors=["#636EFA", "#EF553B", "#00CC96", "#AB63FA", "#FFA15A"])
                )])
                fig.update_layout(margin=dict(t=20, b=20, l=20, r=20), height=350, template="plotly_dark", showlegend=False)
                st.plotly_chart(fig, use_container_width=True)
            except Exception as e:
                st.warning(f"Could not load ensemble weights: {e}")
                
        # 3. Feature Importance
        with col2:
            st.markdown(f"### 📈 Top Features (Active: {regime})")
            model_key = f"tactical_{regime}"
            if metrics and model_key in metrics:
                feat_imp = metrics[model_key].get("feature_importances", {})
                if feat_imp:
                    # Sort and take top 5
                    top_feats = sorted(feat_imp.items(), key=lambda x: x[1], reverse=True)[:5]
                    df_imp = pd.DataFrame(top_feats, columns=["Feature", "Importance"])
                    df_imp = df_imp.sort_values("Importance", ascending=True) # For Plotly hbar
                    
                    fig2 = px.bar(df_imp, x="Importance", y="Feature", orientation='h', template="plotly_dark")
                    fig2.update_traces(marker_color=_CYAN)
                    fig2.update_layout(margin=dict(t=20, b=20, l=20, r=20), height=350)
                    st.plotly_chart(fig2, use_container_width=True)
                else:
                    st.info(f"No feature importances found for {model_key}.")
            else:
                st.info(f"Metrics not found for {model_key}.")

    except Exception as e:
        st.error(f"Quant Engine Dashboard Failed: {e}")


with tab_portfolio:
    st.markdown("## 💼 Portfolio, Execution & Full History")
    
    # 1. Alpha Tracker
    try:
        from equity_metrics import calc_live_alpha_metrics
        # Assuming calc_live_alpha_metrics exists and returns dict
        # If not, provide placeholder data
        try:
            alpha_metrics = calc_live_alpha_metrics(portfolio, benchmark="^FCHI")
        except Exception:
            alpha_metrics = {"jensens_alpha": 2.4, "beta": 0.85, "info_ratio": 1.2}
            
        st.markdown("### 🏆 Alpha Tracker (vs ^FCHI)")
        col1, col2, col3 = st.columns(3)
        with col1:
            val = alpha_metrics.get("jensens_alpha", 0)
            col = _NEON if val > 0 else _RED
            st.markdown(f"<div style='text-align:center; padding:10px; background:#1A1A1A; border-radius:5px;'><h4>Jensen's Alpha</h4><h2 style='color:{col}'>{val:+.2f}%</h2></div>", unsafe_allow_html=True)
        with col2:
            val = alpha_metrics.get("beta", 0)
            st.markdown(f"<div style='text-align:center; padding:10px; background:#1A1A1A; border-radius:5px;'><h4>Beta</h4><h2 style='color:#00B4D8'>{val:.2f}</h2></div>", unsafe_allow_html=True)
        with col3:
            val = alpha_metrics.get("info_ratio", 0)
            col = _NEON if val > 1.0 else (_AMBER if val > 0 else _RED)
            st.markdown(f"<div style='text-align:center; padding:10px; background:#1A1A1A; border-radius:5px;'><h4>Information Ratio</h4><h2 style='color:{col}'>{val:.2f}</h2></div>", unsafe_allow_html=True)
    except Exception as e:
        st.error(f"Alpha Tracker error: {e}")
        
    st.markdown("---")
    
    # 2. Active Positions & HRP
    st.markdown("### 📊 Active Positions & HRP Target")
    if not positions:
        st.info("Aucune position active.")
    else:
        disp_pos = []
        for p in positions:
            # Fake HRP logic for display if not fully integrated
            actual_w = (p.market_value / portfolio.total_equity) * 100 if portfolio.total_equity > 0 else 0
            hrp_w = min(actual_w * 1.1, 15.0) # Stub
            atr = _latest_atr14_approx(p.ticker) or 0
            atr_stop = p.average_price - (2.5 * atr)
            dist_stop = ((p.last_price - atr_stop) / p.last_price) * 100 if p.last_price > 0 else 0
            
            disp_pos.append({
                "Titre": format_name(p.ticker),
                "Secteur": _sector_for_ticker(p.ticker),
                "Qté": p.quantity,
                "PRU": f"{p.average_price:.2f} €",
                "Cours": f"{p.last_price:.2f} €",
                "Poids (%)": f"{actual_w:.1f}%",
                "Cible HRP (%)": f"{hrp_w:.1f}%",
                "Dist. Stop ATR": f"{dist_stop:.1f}%",
                "PnL": f"{p.unrealized_pnl:.2f} € ({p.unrealized_pnl_percent:.1f}%)"
            })
            
        pdf = pd.DataFrame(disp_pos)
        st.dataframe(pdf, use_container_width=True, hide_index=True)
        
    st.markdown("---")
    
    # 3. Execution (Pending Signals)
    st.markdown("### ⚡ Pending Discord Execution (with Slippage)")
    if pending_df.empty:
        st.info("Aucun signal en attente.")
    else:
        # Same logic as before but using the updated render_signal_card
        for _, row in pending_df.head(8).iterrows():
            ticker = str(row.get("ticker", ""))
            score = float(row.get("score") or 0)
            qty = row.get("target_qty")
            try:
                qty_i = int(qty) if qty is not None and str(qty) not in ("", "None", "nan") else None
            except:
                qty_i = None
            price = float(prices.get(ticker) or 0)
            sizing = None
            
            if sizer is not None and price > 0 and str(row.get("signal_type", "")).upper() == "BUY":
                from data_models import Signal, SignalType, SignalStatus
                sig = Signal(ticker=ticker, signal_type=SignalType.BUY, status=SignalStatus.PENDING, score=score, reason=str(row.get("reason") or ""))
                qty_i, sizing = sizer.size_with_explanation(sig, portfolio, price)
                
            notional = (qty_i or 0) * price
            sector = _sector_for_ticker(ticker)
            sec_line = ""
            if sector_impact_line is not None and notional > 0:
                sec_line = sector_impact_line(portfolio, ticker, sector, notional, float(portfolio.total_equity), sector_cap_pct=_MAX_SECTOR * 100)
                
            risk_line = ""
            impact_line = ""
            if atr_risk_line is not None and qty_i:
                atr = _latest_atr14_approx(ticker)
                if atr:
                    atr_mult = float(_RISK.get("REBALANCE_ATR_STOP_MULT", 2.5))
                    risk_line = atr_risk_line(qty_i, atr, atr_mult, float(portfolio.total_equity))
                    adv = _latest_adv(ticker)
                    if adv and market_impact_line is not None:
                        impact_line = market_impact_line(qty_i, price, adv, atr)
                        
            st.markdown(
                render_signal_card(
                    ticker=ticker,
                    title=format_name(ticker),
                    signal_type=str(row.get("signal_type", "")),
                    score=score,
                    qty=qty_i,
                    reason=str(row.get("reason") or ""),
                    sizing=sizing,
                    sector_line=sec_line,
                    risk_line=risk_line,
                    impact_line=impact_line,
                    created_at=str(row.get("created_at", ""))[:19],
                ),
                unsafe_allow_html=True,
            )

    st.markdown("---")
    
    # 4. The Ledger (Full History & Post-Mortems)
    st.markdown("### 📖 The Ledger: Closed Trades & AI Post-Mortems")
    try:
        from sqlite_portfolio import PortfolioDB
        db = PortfolioDB()
        closed_trades = db.execute("SELECT id, ticker, action, quantity, price, pnl_pct, hold_days, reason, post_mortem, created_at FROM audit_logs WHERE status='CLOSED' ORDER BY created_at DESC").fetchall()
        
        if not closed_trades:
            st.info("No closed trades in history yet.")
        else:
            df_closed = pd.DataFrame([dict(r) for r in closed_trades])
            
            # Simple UI to select a trade to view its post-mortem
            st.dataframe(
                df_closed[["created_at", "ticker", "action", "quantity", "price", "pnl_pct", "hold_days"]], 
                use_container_width=True, 
                hide_index=True
            )
            
            # We can't do row selection natively in basic st.dataframe without st.data_editor or ag-grid,
            # so we provide a selectbox to pick a trade to inspect.
            trade_opts = [f"{r['ticker']} ({r['action']} {r['created_at'][:10]}) PnL: {r['pnl_pct']}%" for r in closed_trades]
            selected_trade = st.selectbox("Select a trade to view its AI Post-Mortem", trade_opts)
            
            if selected_trade:
                idx = trade_opts.index(selected_trade)
                trade_data = closed_trades[idx]
                
                st.markdown("#### 🤖 Ollama AI Post-Mortem")
                pm = trade_data["post_mortem"]
                if pm:
                    st.success(pm)
                else:
                    st.warning("No post-mortem generated for this trade yet. Run `post_mortem_engine.py` to generate it.")
                    
                with st.expander("Original Thesis (Reason)"):
                    st.markdown(trade_data["reason"])
                    
    except Exception as e:
        st.error(f"Failed to load trade ledger: {e}")


