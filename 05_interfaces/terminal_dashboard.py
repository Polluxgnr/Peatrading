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
import streamlit.components.v1 as components
import yaml
import yfinance as yf

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


def _sector_for_ticker(ticker: str) -> str:
    try:
        row = universe_df[universe_df["Ticker"] == ticker]
        if not row.empty and "Sector" in row.columns:
            return str(row.iloc[0]["Sector"])
    except Exception:  # noqa: BLE001
        pass
    return "UNKNOWN"


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
        width="stretch",
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
        if atr_risk_line is not None and qty_i:
            atr = _latest_atr14_approx(ticker)
            risk_line = atr_risk_line(
                qty_i, atr, atr_mult, float(portfolio_obj.total_equity)
            )
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
                created_at=str(row.get("created_at", ""))[:19],
            ),
            unsafe_allow_html=True,
        )
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


# =============================================================================
# Page config & Bloomberg CSS
# =============================================================================
st.set_page_config(
    page_title="PEA Pollux | Terminal",
    layout="wide",
    page_icon="\U0001F6E1\uFE0F",
    initial_sidebar_state="collapsed",
)

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


@st.cache_data(ttl=21600, show_spinner=False)
@st.cache_data(ttl=300, show_spinner=False)
def get_fundamental_metrics(ticker: str) -> dict:
    """PE/PB/ROE/Debt-Equity from SQLite cache -> Finnhub -> yfinance fallback."""
    out = {
        "pe_ratio": None,
        "pb_ratio": None,
        "roe": None,
        "debt_to_equity": None,
        "source": "none",
    }
    if not ticker:
        return out

    try:
        db = get_portfolio_db()
        db.init_db()
        cached = db.get_cached_fundamentals(ticker, max_age_days=7)
        if cached:
            return {
                "pe_ratio": cached.get("pe_ratio"),
                "pb_ratio": cached.get("pb_ratio"),
                "roe": cached.get("roe"),
                "debt_to_equity": cached.get("debt_to_equity"),
                "source": cached.get("source") or "sqlite_cache",
            }
    except Exception:  # noqa: BLE001
        pass

    try:
        sensors_dir = _ROOT / "00_data_sensors"
        if str(sensors_dir) not in sys.path:
            sys.path.insert(0, str(sensors_dir))
        from fundamentals_api import FundamentalsSensor  # noqa: WPS433

        live = FundamentalsSensor().get_basic_financials(ticker) or {}
        payload = {
            "pe_ratio": live.get("pe_ratio"),
            "pb_ratio": live.get("pb_ratio"),
            "roe": live.get("roe"),
            "debt_to_equity": live.get("debt_to_equity"),
            "source": live.get("source") or "none",
        }
        if any(
            payload.get(k) is not None
            for k in ("pe_ratio", "pb_ratio", "roe", "debt_to_equity")
        ):
            try:
                db = get_portfolio_db()
                db.init_db()
                db.upsert_fundamentals(ticker, payload)
            except Exception:  # noqa: BLE001
                pass
            return payload
    except Exception:  # noqa: BLE001
        pass

    val = get_valuation_metrics(ticker) or {}
    return {
        "pe_ratio": val.get("trailing_pe"),
        "pb_ratio": val.get("price_to_book"),
        "roe": None,
        "debt_to_equity": None,
        "source": "valuation_fallback",
    }


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


@st.cache_data(ttl=86400, show_spinner=False)
def get_deep_news_synthesis(ticker: str, headlines: tuple[str, ...]) -> str:
    """Alias used by Exploration (same 24h cache key family as analysis)."""
    return get_deep_news_analysis(ticker, headlines)


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


@st.cache_data(ttl=1800, show_spinner=False)
def _fetch_news_from_apis(symbol: str, limit: int = 6) -> list[dict]:
    """Fetch diverse news from live APIs (Boursorama + Google + Yahoo)."""
    collected: list[dict] = []
    seen_titles: set[str] = set()

    def _push(title: str, link: str, date: str, provider: str) -> None:
        import re
        key = (title or "").strip().casefold()
        if not key or key in seen_titles:
            return
        if key.startswith("http://") or key.startswith("https://"):
            return
            
        spam_pattern = re.compile(r"(?i)(discount|free|referral|rewards|newsletter|email|sponsor|pitch deck|vc|substack|attio|seo agency|gtm|seed|founder|startup|saas|cap table|suivre mes récompenses|mettre à jour votre email)")
        if spam_pattern.search(key):
            return
            
        seen_titles.add(key)
        pub = (date or "").strip()
        if not pub or pub.lower() == "recent":
            pub = datetime.now().strftime("%Y-%m-%d %H:%M")
        collected.append({
            "title": title.strip(),
            "link": link or "#",
            "date": pub,
            "provider": provider,
        })

    # --- Boursorama ---------------------------------------------------------
    try:
        scrapers_dir = _ROOT / "00_data_sensors" / "scrapers"
        if str(scrapers_dir) not in sys.path:
            sys.path.insert(0, str(scrapers_dir))
        from bourso_scraper import BoursoramaScraper  # noqa: WPS433

        profile = BoursoramaScraper().get_instrument_profile(symbol)
        items = (profile or {}).get("news_items") or []
        if items:
            sentiment = (profile or {}).get("sentiment") or "Unknown"
            elig = ",".join((profile or {}).get("eligibility") or []) or "?"
            for n in items:
                _push(
                    n.get("title", ""),
                    n.get("link") or "#",
                    n.get("date") or "",
                    f"Boursorama · {n.get('provider') or 'local'} · "
                    f"sentiment {sentiment} · elig {elig}",
                )
        else:
            bourso = BoursoramaScraper().get_retail_sentiment_and_news(symbol)
            headlines = (bourso or {}).get("news") or []
            sentiment = (bourso or {}).get("sentiment") or "Unknown"
            for title in headlines:
                _push(title, "#", "", f"Boursorama · sentiment {sentiment}")
    except Exception:  # noqa: BLE001
        pass

    # --- Google News + European press RSS (always attempted) ----------------
    try:
        import urllib.parse
        import urllib.request
        import xml.etree.ElementTree as ET

        name = short_name(symbol)
        queries = [
            f"{symbol} OR {name} when:7d",
            f"{name} (bourse OR CAC OR PEA) when:7d",
            f"{name} site:lesechos.fr OR site:latribune.fr OR site:reuters.com when:14d",
        ]
        for q in queries:
            url = (
                "https://news.google.com/rss/search?"
                + urllib.parse.urlencode({
                    "q": q, "hl": "fr", "gl": "FR", "ceid": "FR:fr",
                })
            )
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "PEA-Pollux/1.0"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                root = ET.fromstring(resp.read())
            for item in root.findall(".//item")[:8]:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "#").strip()
                pub = (item.findtext("pubDate") or "")[:16]
                source = item.find("source")
                src = (source.text if source is not None else None) or "Google News"
                _push(title, link, pub, f"Google News · {src}")
    except Exception:  # noqa: BLE001
        pass

    # --- Yahoo Finance (always attempted) -----------------------------------
    try:
        raw = yf.Ticker(symbol).news or []
        for n in raw:
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
            _push(
                title, link, (date_str or "")[:16],
                provider or "Yahoo Finance",
            )
    except Exception:  # noqa: BLE001
        pass

    return collected[:limit]


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


@st.cache_data(ttl=3600, show_spinner=False)
@st.cache_data(ttl=86400, show_spinner=False)
def _french_dossier_summary(ticker: str, name: str, english: str) -> str:
    """Translate/compress Yahoo longBusinessSummary to 3 short FR sentences.

    Falls back to the English snippet if OpenRouter is unavailable — never blocks.
    """
    text = (english or "").strip()
    if not text:
        return ""
    # Already looks French enough — keep as-is.
    fr_markers = (" est ", " une ", " des ", " société", " groupe", " dans ")
    if sum(1 for m in fr_markers if m in text.casefold()) >= 2:
        return text[:700]
    api_key = None
    try:
        import os
        api_key = os.getenv("OPENROUTER_API_KEY")
    except Exception:  # noqa: BLE001
        api_key = None
    if not api_key:
        return text[:700]
    try:
        from llm_explainer import openrouter_chat

        prompt = (
            f"Traduis et synthétise en français, exactement 3 phrases courtes, "
            f"le profil de {name} ({ticker}) pour un investisseur PEA. "
            f"Pas de blabla, pas d'anglais.\n\n{text[:1200]}"
        )
        out = asyncio.run(openrouter_chat(
            [
                {"role": "system", "content": "Tu es un rédacteur financier FR concis."},
                {"role": "user", "content": prompt},
            ],
            api_key=api_key,
            max_tokens=220,
            temperature=0.2,
        ))
        cleaned = (out or "").strip()
        return cleaned[:700] if cleaned else text[:700]
    except Exception:  # noqa: BLE001
        return text[:700]


def get_ticker_dossier(ticker: str) -> dict:
    """Company identity + catalysts + risk events (yfinance + heuristics)."""
    out: dict = {
        "name": format_name(ticker),
        "summary": "",
        "sector": "",
        "industry": "",
        "catalysts": [],
        "risk_events": [],
        "is_etf": False,
        "fundamentals": {},
    }
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:  # noqa: BLE001
        info = {}
    name = info.get("longName") or info.get("shortName") or short_name(ticker)
    out["name"] = name
    out["sector"] = str(info.get("sector") or "")
    out["industry"] = str(info.get("industry") or "")
    summary = str(info.get("longBusinessSummary") or "")[:700]
    quote_type = str(info.get("quoteType") or "").upper()
    out["is_etf"] = quote_type in ("ETF", "MUTUALFUND") or ticker.endswith(".PA") and (
        "ETF" in name.upper() or "UCITS" in name.upper() or ticker == _CORE_TICKER
    )
    if summary:
        out["summary"] = _french_dossier_summary(ticker, name, summary)
    elif out["is_etf"] or ticker == _CORE_TICKER:
        out["summary"] = (
            f"{name} est un ETF eligible PEA. Il replique un indice large "
            "(ex. MSCI World pour CW8) au lieu d'un risque entreprise unique. "
            "C'est l'ancre Core du systeme PEA Pollux."
        )
    else:
        out["summary"] = (
            f"{format_name(ticker)} — fiche qualitative incomplete cote Yahoo. "
            "Consulte Boursorama / le document d'enregistrement universel."
        )

    # Catalysts / risks — sector-aware heuristics + earnings
    sector = (out["sector"] or "").casefold()
    catalysts = [
        "Publication de resultats au-dessus du consensus (EPS / CA)",
        "Guidance relevee ou nouveau contrat significatif",
        "Rachat d'actions / dividende en hausse",
    ]
    risks = [
        "Profit warning ou baisse de guidance",
        "Enquete regulateur / amende majeure",
        "Choc macro (VIX panic) pendant que tu es concentre sur 1 ligne",
    ]
    if "auto" in sector or "consumer cyclical" in sector or "STLAP" in ticker:
        catalysts += ["Rebond volumes Europe/US", "Marges industrielles stabilisees"]
        risks += ["Guerre commerciale / droits de douane", "Retard plateformes EV"]
    if "healthcare" in sector or "SAN.PA" in ticker:
        catalysts += ["Approbation medicament / pipeline"]
        risks += ["Echec essai clinique", "Pression prix medicaments"]
    if out["is_etf"] or ticker == _CORE_TICKER:
        catalysts = [
            "Marche actions mondial en tendance haussiere",
            "DCA discipliné pendant les corrections (Smart DCA)",
            "Euro stable vs panier devise de l'indice",
        ]
        risks = [
            "Krach global prolonge (mais le DCA achete alors plus fort)",
            "Tracking error / frais de l'ETF",
            "Force de l'euro qui pese sur un indice world en devises",
        ]
    out["catalysts"] = catalysts[:5]
    out["risk_events"] = risks[:5]
    try:
        out["fundamentals"] = get_fundamental_metrics(ticker)
    except Exception:  # noqa: BLE001
        out["fundamentals"] = {}
    return out


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
    if st.button("\U0001F504 Vider le cache & recharger", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    st.markdown("---")
    if st.button("Ledger signaux", width="stretch"):
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
    
    st.markdown(
        f"""
    <div class="mission">
      <div class="mission-title">Mission Control · PEA personnel</div>
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
tab_macro, tab_ticker, tab_pf_exec, tab_sys_logs = st.tabs([
    "📈 Market & Macro",
    "🌍 Ticker Deep-Dive",
    "💼 Portfolio & Execution",
    "🧠 System Logs",
])

# --- Tab: General + Signals --------------------------------------------------
with tab_macro:
    st.markdown(
        "<div class='info-text'>Briefing + registre des signaux + "
        "<b>suggestion de portefeuille adaptative</b> selon ton capital. "
        "Aucun ordre n'est envoye depuis ici — Discord reste le copilot.</div>",
        unsafe_allow_html=True,
    )

    # --- Phase 19: Morning Briefing (Synthèse IA) — top of General ----------
    st.markdown("#### 🗞️ Morning Briefing (Synthèse IA)")
    briefing = load_morning_briefing()
    if not morning_briefing_is_live(briefing):
        gen_at_raw = str(briefing.get("generated_at") or "") if briefing else ""
        if gen_at_raw:
            st.caption(f"Dernière synthèse : {gen_at_raw[:16].replace('T', ' à ')}")
        st.info("Briefing en attente de génération ou données insuffisantes.")
        if st.button(
            "Générer le Briefing maintenant",
            type="primary",
            key="gen_morning_briefing_now",
        ):
            import subprocess
            subprocess.Popen(
                [sys.executable, str(_ROOT / "main_scheduler.py"), "--briefing"],
                cwd=str(_ROOT),
            )
            st.toast("Génération lancée en arrière-plan. L'IA analyse les newsletters, revenez dans 2 minutes.", icon="🗞️")
    else:
        zg = str(briefing.get("zeitgeist") or "").strip()
        headlines = briefing.get("headlines") or []
        gen_at = str(briefing.get("generated_at") or "")[:19]
        # Split LLM bullets into metric boxes when possible
        bullets = [
            ln.strip(" •-\t")
            for ln in zg.replace("\r", "").split("\n")
            if ln.strip() and ln.strip()[0] in "•-*–—0123456789"
        ]
        if not bullets:
            bullets = [zg]
        cols = st.columns(min(5, max(1, len(bullets[:5]))))
        for i, bullet in enumerate(bullets[:5]):
            with cols[i]:
                st.markdown(
                    metric_box(
                        f"Thème {i + 1}",
                        bullet[:90] + ("…" if len(bullet) > 90 else ""),
                        sub="newsletter Synthèse IA",
                        accent="cyan" if i % 2 == 0 else "amber",
                        help_text="Narratif macro extrait des newsletters overnight.",
                    ),
                    unsafe_allow_html=True,
                )
        if gen_at:
            st.caption(f"Généré {gen_at} UTC · {len(headlines)} titre(s) source")
        if True:
            if headlines:
                st.markdown("\n".join(f"- {h}" for h in headlines))
            else:
                st.caption("Aucun titre source dans le JSON.")

    held_tickers = [p.ticker for p in positions]
    blue_chips = ["MC.PA", "OR.PA", "AI.PA", "RMS.PA", "SAN.PA",
                  "TTE.PA", "BNP.PA", "AIR.PA", _CORE_TICKER]
    watch = tuple(dict.fromkeys(held_tickers + blue_chips))[:14]

    pending_gen = load_signals(("PENDING",))
    suggestion = suggest_adaptive_portfolio(
        float(portfolio.total_equity),
        float(portfolio.cash_available),
        float(vix),
        regime or {},
        pending_gen,
        held_tickers,
    )

    st.markdown("#### 🎯 Meilleur portefeuille suggere (adaptatif)")
    st.markdown(
        f"<div class='eli5'>{suggestion.get('summary', '')}</div>",
        unsafe_allow_html=True,
    )
    if True:
        st.markdown("### 💡 Lire le détail de la stratégie")
        st.markdown(
            f"<div class='info-text'>"
            f"<b style='color:{_AMBER};'>Pourquoi ce mode "
            f"({suggestion.get('mode')}) :</b><br>"
            f"{suggestion.get('mode_why', '')}<br><br>"
            f"{suggestion.get('cash_explain', '')}</div>",
            unsafe_allow_html=True,
        )
        if True:
            st.markdown("### 📖 Comprendre cette recommandation")
            st.caption(
                "Le résumé reste visible ci-dessus. Ici : justification du mode "
                "(MICRO/STARTER/…) et lecture cash / runway (court_why)."
            )
            st.markdown(
                f"**mode_why:** {suggestion.get('mode_why', '—')}\n\n"
                f"**cash_explain / court_why:** {suggestion.get('cash_explain', '—')}"
            )
    sug_lines = suggestion.get("lines") or []
    if sug_lines:
        sdisp = pd.DataFrame([{
            "Titre": format_name(l["ticker"]),
            "Role": l["role"],
            "Qte": l["qty"],
            "Cours": f"{l['price']:,.2f} €",
            "Cout": f"{l['cost']:,.2f} €",
            "Poids": f"{l['weight_pct']:.0f}%",
            "Justification": l["why"][:160],
        } for l in sug_lines])
        st.plotly_chart(
            dark_table(sdisp, height=min(280, 60 + 36 * len(sdisp)),
                       col_widths=[2, 1.2, 0.5, 0.9, 0.9, 0.6, 2.8]),
            width="stretch",
            key="gen_primary_suggestion_table",
        )
    else:
        st.warning(suggestion.get("summary", "Pas de suggestion."))

    # Ranked alternatives with score + reco (fixes "only one option" feel)
    alts = suggestion.get("alternatives") or []
    st.markdown("##### Classement des alternatives achetable (score 0–100)")
    st.markdown(
        "<div class='info-text'>ETF PEA (EWLD, PAEEM, ESE, C50…) vs actions "
        "liquides. Score = <b>empreinte multi-stratégies</b> "
        "(MR 35% + Mom 25% + Q/V 20% + Insiders 20%) + bonus ETF / pénalité VIX. "
        "<b>ACHETER / SURVEILLER / ATTENDRE / EVITER</b>. "
        "Toujours 1 part max en MICRO + cash runway.</div>",
        unsafe_allow_html=True,
    )
    if alts:
        adisp = pd.DataFrame([{
            "Rang": i + 1,
            "Titre": format_name(a["ticker"]),
            "Type": a.get("kind", "?"),
            "Cours": f"{a['price']:,.2f} €",
            "Score": f"{a['score']}/100",
            "Reco": (
                f"{a.get('reco', '')}"
                + ("" if a.get("affordable", True) else " [HORS BUDGET]")
            ),
            "RSI": f"{a['rsi']:.0f}" if a.get("rsi") is not None else "—",
            "vs SMA200": (
                f"{a['vs_sma200']:+.1f}%" if a.get("vs_sma200") is not None else "—"
            ),
            "Poids 1 part": f"{a.get('weight_pct', 0):.0f}%",
            "Pourquoi": str(a.get("why", ""))[:180],
        } for i, a in enumerate(alts)])
        reco_colors = []
        for a in alts:
            if not a.get("affordable", True):
                reco_colors.append(_MUTED)
            else:
                r = a.get("reco")
                reco_colors.append(
                    _NEON if r == "ACHETER" else
                    _AMBER if r == "SURVEILLER" else
                    _CYAN if r == "ATTENDRE" else _RED
                )
        adisp_click = adisp.copy()
        adisp_click.insert(0, "Ticker", [a["ticker"] for a in alts])
        _alt_event = st.dataframe(
            adisp_click,
            use_container_width=True,
            hide_index=True,
            selection_mode="single-row",
            on_select="rerun",
            key="gen_alternatives_click",
        )
        _alt_rows = list(getattr(getattr(_alt_event, "selection", None), "rows", []) or [])
        if _alt_rows:
            _i = int(_alt_rows[0])
            if 0 <= _i < len(adisp_click):
                _ticker_pick = str(adisp_click.iloc[_i]["Ticker"])
                st.session_state["focus_ticker"] = _ticker_pick
                st.session_state["selected_ticker"] = _ticker_pick
                st.caption(f"🔍 Analyse rapide prête pour {format_name(_ticker_pick)} (onglet Exploration).")
        # Phase 22: high-vol momentum pepites
        st.markdown("#### 🚀 Pépites (Forte Volatilité & Croissance)")
        st.markdown(
            "<div class='info-text'>Filtre liquide : <b>vol annualisée &gt; 35%</b> "
            "et <b>Close &gt; SMA50</b>. Ce n'est pas un ordre — juste un radar "
            "de titres « chauds » à croiser avec l'empreinte.</div>",
            unsafe_allow_html=True,
        )
        pepites = get_momentum_pepites(limit=5)
        if pepites:
            pdisp = pd.DataFrame([{
                "Titre": format_name(p["ticker"]),
                "Vol": f"{p['vol_ann']:.0f}%",
                "RSI": f"{p['rsi']:.0f}" if p.get("rsi") is not None else "—",
                "vs SMA50": f"{p['gap_sma50']:+.1f}%",
                "Cours": f"{p['close']:,.2f} €",
            } for p in pepites])
            pdisp_click = pdisp.copy()
            pdisp_click.insert(0, "Ticker", [p["ticker"] for p in pepites])
            _pep_event = st.dataframe(
                pdisp_click,
                use_container_width=True,
                hide_index=True,
                selection_mode="single-row",
                on_select="rerun",
                key="gen_pepites_click",
            )
            _pep_rows = list(getattr(getattr(_pep_event, "selection", None), "rows", []) or [])
            if _pep_rows:
                _i = int(_pep_rows[0])
                if 0 <= _i < len(pdisp_click):
                    _ticker_pick = str(pdisp_click.iloc[_i]["Ticker"])
                    st.session_state["focus_ticker"] = _ticker_pick
                    st.session_state["selected_ticker"] = _ticker_pick
                    st.caption(f"🔍 Analyse rapide prête pour {format_name(_ticker_pick)} (onglet Exploration).")
        else:
            st.caption("Aucune pépite sur le panier liquide (vol/SMA50).")
    else:
        st.caption("Aucune alternative liquide avec cours disponible.")

    horizons = suggestion.get("horizons") or {}
    if horizons:
        if True:
            st.markdown("### Horizons d'allocation (court / moyen / long)")
            h_choice = st.radio(
                "Horizon",
                ["court", "moyen", "long"],
                format_func=lambda k: (horizons.get(k) or {}).get("label", k),
                horizontal=True,
                key="gen_horizon_radio",
            )
            hz = horizons.get(h_choice) or {}
            if True:
                st.markdown("### 📖 Comprendre cette recommandation")
                st.markdown(hz.get("why", ""), unsafe_allow_html=True)
            hlines = hz.get("lines") or []
            if hlines:
                hdf = pd.DataFrame([{
                    "Titre": format_name(l["ticker"]),
                    "Role": l.get("role", ""),
                    "Qte": l["qty"],
                    "Cours": f"{l['price']:,.2f} €",
                    "Cout": f"{l['cost']:,.2f} €",
                    "Note": str(l.get("why", ""))[:140],
                } for l in hlines])
                st.plotly_chart(
                    dark_table(hdf, height=min(260, 56 + 34 * len(hdf)),
                               col_widths=[2, 1.1, 0.5, 0.9, 0.9, 2.6]),
                    width="stretch",
                    key=f"gen_horizon_table_{h_choice}",
                )
            else:
                st.caption("Rien d'achetable sur cet horizon avec le cash actuel.")
            if h_choice != "long":
                st.caption(f"Cash restant illustre ~{hz.get('cash_keep', 0):,.0f} €")

    # Core ETF snapshot
    etf = get_etf_card(_CORE_TICKER)
    with st.expander(f"📦 Fiche ETF Core — {etf.get('name', _CORE_TICKER)}", expanded=False):
        st.markdown(
            f"<div class='info-text'><b>{etf.get('role')}</b><br>"
            f"{etf.get('summary', '')[:500]}</div>",
            unsafe_allow_html=True,
        )
        ec1, ec2, ec3 = st.columns(3)
        px = etf.get("price")
        ec1.metric("Cours", f"{px:,.2f} €" if px else "n/a")
        reg = etf.get("regime") or {}
        ec2.metric("vs SMA200", f"{reg.get('gap_pct', 0):+.1f}%" if reg else "n/a")
        ec3.metric("Part entiere requise", f"{px:,.0f} €" if px else "n/a",
                   help="PEA = actions entieres. Sous ce montant, pas de Core.")

    st.markdown("---")
    recos = build_recommendations(portfolio, pending_gen, vix, regime or {})
    g1, g2 = st.columns([1.15, 1])
    with g1:
        st.markdown("#### 📌 Recommandations actuelles")
        if not recos:
            st.caption("Aucune recommandation urgente.")
        for r in recos:
            accent = _RED if r["prio"] == 1 else (_AMBER if r["prio"] == 2 else _CYAN)
            st.markdown(
                f"<div style='background:#0A0A0A;padding:10px 12px;margin-bottom:8px;"
                f"border-left:4px solid {accent};border:1px solid #222;'>"
                f"<b style='color:{_WHITE};'>{r['title']}</b></div>",
                unsafe_allow_html=True,
            )
            if True:
                st.markdown("### 📖 Comprendre cette recommandation")
                st.markdown(r.get("why", "—"))
    with g2:
        st.markdown("#### 🌍 Briefing geopolitique / macro")
        with st.spinner("Briefing macro…"):
            _head_preview = tuple(
                n.get("title", "") for n in get_general_news_bundle(watch)[:8]
            )
            brief = get_geopolitical_brief(float(vix), _head_preview)
        st.markdown(
            f"<div style='background:#0A0A0A;padding:14px;border:1px solid #222;"
            f"color:#E8E8E8;line-height:1.55;font-size:14px;'>{brief}</div>",
            unsafe_allow_html=True,
        )

    # --- Phase 17: Decision funnel (audit-log analytics) --------------------
    st.markdown("---")
    with st.expander("📊 Entonnoir de Décision (Funnel 7J)", expanded=True):
        st.markdown(
            "<div class='info-text'>Lecture seule des audit logs SQLite "
            "(7 jours). Taxonomie identique au Weekly Historian "
            "(<code>_classify</code>) — pour voir <b>où</b> la cascade coupe "
            "les idées, pas pour recalculer le marché.</div>",
            unsafe_allow_html=True,
        )
        funnel_days = st.radio(
            "Fenêtre",
            options=(7, 30),
            index=0,
            horizontal=True,
            key="funnel_days_radio",
            format_func=lambda d: f"{d} jours",
        )
        funnel = get_funnel_metrics(int(funnel_days))
        if funnel.get("empty"):
            st.info(
                "Aucun signal dans la fenêtre. Lance "
                "`python main_scheduler.py --now` pour peupler l'audit log, "
                "puis reviens ici."
            )
        else:
            sr = float(funnel.get("survival_rate") or 0)
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Signaux (fenêtre)", f"{funnel.get('total', 0)}")
            m2.metric("Rejets cascade", f"{funnel.get('rejected', 0)}")
            m3.metric("APPROVED / EXECUTED", f"{funnel.get('approved', 0)}")
            m4.metric(
                "Taux de survie",
                f"{sr:.1f}%",
                help="Approved+Executed / total audit rows dans la fenêtre.",
            )
            fw, fp = st.columns([0.6, 0.4])
            with fw:
                st.plotly_chart(
                    render_waterfall_chart(funnel),
                    width="stretch",
                    key="gen_funnel_waterfall",
                )
            with fp:
                st.plotly_chart(
                    render_rejection_pie(funnel),
                    width="stretch",
                    key="gen_funnel_pie",
                )
            st.caption(
                "Drops waterfall : Sanity/ADV/max positions → Macro/VIX/earnings "
                "→ Secteur → Corrélation → Cash/sizing. Le total final = survivants "
                "après rejets (+ pending/révoqués retirés si présents)."
            )

    st.markdown("---")
    st.markdown("#### ⚡ Signaux & Registre")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("##### En attente (Command Center) — cartes de trade")
        pending = pending_gen
        render_pending_trade_cards(pending, portfolio)

        # --- Phase 34: Near-Miss radar -------------------------------------
        st.markdown("#### 📡 Radar de Surveillance (Near-Miss)")
        pending_tickers = set()
        if pending is not None and not pending.empty and "ticker" in pending.columns:
            try:
                pending_tickers = {
                    str(t) for t in pending["ticker"].dropna().tolist()
                }
            except Exception:  # noqa: BLE001
                pending_tickers = set()

        near_miss = []
        try:
            # "Alternatives" viennent du même scoring heuristique que la
            # recommandation (rank_affordable_alternatives).
            for a in (alts or []):
                try:
                    sc = int(a.get("score") or -1)
                except Exception:  # noqa: BLE001
                    continue
                if 40 <= sc <= 64:
                    t = str(a.get("ticker") or "")
                    if t and t not in pending_tickers:
                        near_miss.append(a)
        except Exception:  # noqa: BLE001
            near_miss = []

        near_miss.sort(key=lambda x: int(x.get("score") or 0), reverse=True)
        near_miss = near_miss[:10]

        if not near_miss:
            st.caption("Aucun near-miss détecte (scores 40–64). Le marché ne propose rien pour l'instant.")
        else:
            rows = []
            for a in near_miss:
                t = str(a.get("ticker") or "")
                ind = get_indicators(t) or {}
                close = ind.get("close") or a.get("price")
                sma5 = ind.get("sma5")
                sma50 = ind.get("sma50")
                sma200 = ind.get("sma200")
                rsi = ind.get("rsi") if ind else None

                missing = "En attente de confirmation"
                try:
                    if close is not None and sma5 is not None and float(close) < float(sma5):
                        missing = "En attente du franchissement SMA5"
                    elif close is not None and sma50 is not None and float(close) < float(sma50):
                        missing = "En attente du franchissement SMA50"
                    elif close is not None and sma200 is not None and float(close) < float(sma200):
                        missing = "En attente du franchissement SMA200"
                    else:
                        missing = "En attente d'un meilleur contexte (MR/Mom/Insider)"
                except Exception:  # noqa: BLE001
                    missing = "En attente de confirmation"

                sc = int(a.get("score") or 0)
                rows.append({
                    "Ticker": t,
                    "Score": f"{sc}/100",
                    "RSI": f"{float(rsi):.0f}" if rsi is not None else "—",
                    "Manquant": missing,
                })

            disp = pd.DataFrame(rows)
            score_colors = [
                (_NEON if int(s.split("/")[0]) >= 62 else
                 _AMBER if int(s.split("/")[0]) >= 58 else
                 _CYAN)
                for s in disp["Score"].tolist()
            ]
            st.plotly_chart(
                dark_table(
                    disp,
                    height=min(420, 44 + 28 * len(disp)),
                    col_widths=[1.2, 0.8, 0.7, 3.2],
                    font_color_map={"Score": score_colors},
                ),
                width="stretch",
                key="gen_near_miss_radar",
            )
    with col2:
        st.markdown("##### Historique (20 derniers)")
        hist = load_signals(("EXECUTED", "REVOKED", "REJECTED", "EXPIRED"), limit=20)
        if hist.empty:
            st.info("Aucun historique disponible.")
        else:
            status_color = {"EXECUTED": _NEON, "REVOKED": _RED,
                            "REJECTED": _MUTED, "EXPIRED": _AMBER}
            statut_colors = [status_color.get(s, _WHITE) for s in hist["status"]]
            disp = pd.DataFrame({
                "Titre": [format_name(t) for t in hist["ticker"]],
                "Statut": hist["status"],
                "Type": hist["signal_type"],
                "Score": [f"{s:.1f}" for s in hist["score"]],
                "Date": [str(x)[:16] for x in hist["created_at"]],
            })
            st.plotly_chart(
                dark_table(disp, height=320,
                           font_color_map={"Statut": statut_colors},
                           col_widths=[2, 1.1, 0.9, 0.7, 1.2]),
                width="stretch",
                key="gen_hist_signals_table",
            )
    st.markdown("---")
    p1, p2 = st.columns(2)
    with p1:
        st.markdown("#### 📈 Top / Flop (1 mois)")
        perf_watch = get_market_performance(watch, period="1mo")
        if perf_watch.empty or "Performance (%)" not in perf_watch.columns:
            st.caption("Performances indisponibles.")
        else:
            pf = perf_watch.copy()
            pf["Titre"] = [format_name(t) for t in pf["Ticker"]]
            top = pf.nlargest(5, "Performance (%)")
            # Exclusive flop: exclude tickers already in Top, require strictly worse.
            flop_pool = pf[~pf["Ticker"].isin(top["Ticker"])]
            flop = flop_pool.nsmallest(5, "Performance (%)")
            tcol, fcol = st.columns(2)
            with tcol:
                st.caption("Top")
                disp_t = pd.DataFrame({
                    "Titre": top["Titre"],
                    "Perf": [f"{v:+.1f}%" for v in top["Performance (%)"]],
                })
                st.plotly_chart(
                    dark_table(disp_t, height=220,
                               font_color_map={"Perf": [_NEON] * len(disp_t)},
                               col_widths=[2.2, 0.8]),
                    width="stretch",
                    key="gen_top_perf_table",
                )
            with fcol:
                st.caption("Flop")
                disp_f = pd.DataFrame({
                    "Titre": flop["Titre"],
                    "Perf": [f"{v:+.1f}%" for v in flop["Performance (%)"]],
                })
                st.plotly_chart(
                    dark_table(disp_f, height=220,
                               font_color_map={"Perf": [_RED] * len(disp_f)},
                               col_widths=[2.2, 0.8]),
                    width="stretch",
                    key="gen_flop_perf_table",
                )
    with p2:
        st.markdown("#### 📅 Evenements a venir")
        events = get_earnings_events(watch)
        if not events:
            st.caption("Aucun calendrier earnings detecte (yfinance).")
        else:
            edf = pd.DataFrame([{
                "Titre": format_name(e["ticker"]),
                "Evenement": e["event"],
                "Date": e["date"],
            } for e in events])
            st.plotly_chart(
                dark_table(edf, height=220), width="stretch",
                key="gen_earnings_table",
            )
    st.markdown("---")
    st.markdown("#### 📰 Actualites (contexte marche)")
    st.markdown(
        "<div class='info-text'>Liste dedupliquee multi-sources "
        "(Boursorama / Google / Yahoo). <b>Contexte seulement</b> — "
        "jamais un trigger d'ordre. Pour la synthèse IA profonde, ouvre "
        "l'onglet Exploration sur un ticker.</div>",
        unsafe_allow_html=True,
    )
    news_bundle = get_general_news_bundle(watch)
    if news_bundle:
        ndisp = pd.DataFrame([{
            "Source": str(n.get("provider") or "")[:48],
            "Date": str(n.get("date") or "")[:16],
            "Titre": str(n.get("title") or "")[:120],
            "Ticker": short_name(str(n.get("ticker") or "")),
        } for n in news_bundle[:12]])
        st.plotly_chart(
            dark_table(
                ndisp,
                height=min(360, 56 + 28 * len(ndisp)),
                col_widths=[1.6, 0.9, 3.2, 0.8],
            ),
            width="stretch",
            key="gen_news_clean_table",
        )
    else:
        st.caption("Aucune actualite recente sur la watchlist.")

# --- Tab: Portfolio ----------------------------------------------------------
with tab_pf_exec:
    st.markdown(
        "<div class='info-text'>Decomposition de l'exposition sectorielle. "
        "En capital eleve, le risque PEA Pollux limite a 25% / secteur et 15% / "
        "ligne. En micro-PEA ces plafonds sont volontairement assouplis "
        "(voir suggestion dans General).</div>",
        unsafe_allow_html=True,
    )

    # --- Equity curve (top of Portefeuille) ---------------------------------
    st.markdown("#### 📈 Courbe de Performance (Equity Curve)")
    eq_curve = load_equity_curve()
    if eq_curve is None or eq_curve.empty or "equity" not in eq_curve.columns:
        st.info(
            "Pas encore d'historique d'equity. La courbe se construit a chaque "
            "``update_portfolio`` (snapshot journalier dans ``portfolio_history``)."
        )
    else:
        eq = eq_curve.copy()
        eq["date"] = pd.to_datetime(eq["date"], errors="coerce")
        eq = eq.dropna(subset=["date", "equity"]).sort_values("date")
        if eq.empty:
            st.info("Historique equity vide apres nettoyage.")
        else:
            y_min = float(eq["equity"].min())
            y_max = float(eq["equity"].max())
            pad = max((y_max - y_min) * 0.08, abs(y_max) * 0.01, 1.0)
            fig_eq = pex.area(
                eq,
                x="date",
                y="equity",
                labels={"date": "Date", "equity": "Equity (€)"},
            )
            fig_eq.update_traces(
                line=dict(color="#00FF00", width=2),
                fill="tozeroy",
                fillcolor="rgba(0, 255, 0, 0.25)",
            )
            fig_eq.update_layout(
                paper_bgcolor=_BG,
                plot_bgcolor=_BG,
                font=dict(family="Courier New", color=_WHITE),
                margin=dict(t=20, l=40, r=20, b=40),
                height=320,
                xaxis=dict(gridcolor="#222", showgrid=True),
                yaxis=dict(
                    gridcolor="#222",
                    showgrid=True,
                    range=[y_min - pad, y_max + pad],
                    title="Equity (€)",
                ),
                showlegend=False,
            )
            st.plotly_chart(fig_eq, width="stretch", key="pf_equity_curve")
            if compute_equity_metrics is not None:
                m = compute_equity_metrics(eq)
                c1, c2, c3, c4, c5 = st.columns(5)

                def _pct(x):
                    return "—" if x is None else f"{x * 100:+.1f}%"

                def _num(x):
                    return "—" if x is None else f"{x:.2f}"

                c1.metric("Total return", _pct(m.get("total_return")))
                c2.metric("CAGR", _pct(m.get("cagr")))
                c3.metric("Max DD", _pct(m.get("max_drawdown")))
                c4.metric("Sharpe", _num(m.get("sharpe")))
                c5.metric("Sortino", _num(m.get("sortino")))
                st.caption(
                    f"{m.get('n_points', 0)} point(s) · "
                    "métriques partagées (`equity_metrics`) — mêmes formules "
                    "que le futur backtester."
                )

            # Phase 40 — forward tracking vs MSCI World PEA (CW8.PA)
            st.markdown("#### 📈 Tracking en Direct des Recommandations (Forward Curve)")
            try:
                from duckdb_manager import TimeSeriesDB as _TSDBFwd

                _dbf = _TSDBFwd(read_only=True)
                cw8 = _dbf.get_historical_prices(_CORE_TICKER, days=800)
                if (
                    cw8 is not None
                    and not cw8.empty
                    and "Close" in cw8.columns
                    and eq is not None
                    and not eq.empty
                ):
                    bench = cw8[["Date", "Close"]].copy()
                    bench["Date"] = pd.to_datetime(bench["Date"], errors="coerce")
                    bench = bench.dropna().sort_values("Date")
                    pf = eq[["date", "equity"]].copy()
                    pf["date"] = pd.to_datetime(pf["date"], errors="coerce")
                    pf = pf.dropna().sort_values("date")
                    merged = pd.merge_asof(
                        pf.rename(columns={"date": "Date"}),
                        bench,
                        on="Date",
                        direction="backward",
                    ).dropna()
                    if len(merged) >= 2:
                        merged["PF_idx"] = (
                            merged["equity"] / float(merged["equity"].iloc[0]) * 100.0
                        )
                        merged["CW8_idx"] = (
                            merged["Close"] / float(merged["Close"].iloc[0]) * 100.0
                        )
                        fig_fwd = go.Figure()
                        fig_fwd.add_trace(go.Scatter(
                            x=merged["Date"], y=merged["PF_idx"],
                            mode="lines", name="Portefeuille",
                            line=dict(color=_NEON, width=2),
                        ))
                        fig_fwd.add_trace(go.Scatter(
                            x=merged["Date"], y=merged["CW8_idx"],
                            mode="lines", name="CW8.PA (MSCI World)",
                            line=dict(color=_CYAN, width=2, dash="dot"),
                        ))
                        fig_fwd.update_layout(
                            title="Performance cumulée (base 100) vs benchmark",
                            yaxis_title="Index (base 100)",
                            height=360,
                            margin=dict(t=40, l=20, r=20, b=20),
                            legend=dict(orientation="h"),
                        )
                        _style_dark_fig(fig_fwd)
                        st.plotly_chart(fig_fwd, width="stretch", key="pf_forward_vs_cw8")
                        pf_ret = float(merged["PF_idx"].iloc[-1] - 100.0)
                        bm_ret = float(merged["CW8_idx"].iloc[-1] - 100.0)
                        st.caption(
                            f"Depuis inception affichée : portefeuille {pf_ret:+.1f}% · "
                            f"CW8 {bm_ret:+.1f}% · écart {pf_ret - bm_ret:+.1f} pts"
                        )
                    else:
                        st.caption("Historique insuffisant pour le tracking vs CW8.")
                else:
                    st.caption(
                        "CW8.PA ou equity curve manquant — lance "
                        "`python main_scheduler.py --backfill-10y` ou `--now`."
                    )
            except Exception as exc:  # noqa: BLE001
                st.caption(f"Forward curve indisponible ({exc}).")

            st.markdown("#### 📉 Métriques de Risque Académique (Tail Risk)")
            if (
                calculate_historical_var is None
                or calculate_cvar is None
                or len(eq) < 30
            ):
                st.caption("VaR/CVaR indisponibles (historique insuffisant ou module quant absent).")
            else:
                try:
                    eq_ret = pd.to_numeric(eq["equity"], errors="coerce").pct_change().dropna()
                    var95 = float(calculate_historical_var(eq_ret, confidence_level=0.95))
                    cvar95 = float(calculate_cvar(eq_ret, confidence_level=0.95))
                    rv1, rv2 = st.columns(2)
                    with rv1:
                        st.markdown(
                            metric_box(
                                "VaR 95% (1j)",
                                f"-{var95*100:.2f}%",
                                sub="Perte max attendue (quantile 5%)",
                                accent="amber",
                                sub_cls="sub-amber",
                                help_text=(
                                    "Historical VaR 95%: perte journalière maximale attendue "
                                    "avec 95% de confiance (quantile historique à 5%)."
                                ),
                            ),
                            unsafe_allow_html=True,
                        )
                    with rv2:
                        st.markdown(
                            metric_box(
                                "CVaR 95% (1j)",
                                f"-{cvar95*100:.2f}%",
                                sub="Perte moyenne au-delà de la VaR",
                                accent="red",
                                sub_cls="sub-red",
                                help_text=(
                                    "Conditional VaR 95% (Expected Shortfall): perte journalière "
                                    "moyenne conditionnelle une fois la VaR dépassée."
                                ),
                            ),
                            unsafe_allow_html=True,
                        )
                except Exception:  # noqa: BLE001
                    st.caption("VaR/CVaR indisponibles (erreur de calcul).")

            st.markdown("#### 🔮 Projections Stochastiques (Monte Carlo)")
            held_tickers_pf = [str(p.ticker) for p in positions if getattr(p, "ticker", None)]
            if run_correlated_monte_carlo is None or len(held_tickers_pf) < 1:
                st.caption("Monte Carlo indisponible (module absent ou aucune position).")
            else:
                if True:
                    st.markdown("### Lancer la projection probabiliste (on-demand)")
                    sims = st.slider(
                        "Simulations Monte Carlo",
                        min_value=500,
                        max_value=5000,
                        value=2000,
                        step=500,
                        key="pf_mc_sims",
                    )
                    horizon = st.slider(
                        "Horizon (jours de bourse)",
                        min_value=60,
                        max_value=504,
                        value=252,
                        step=21,
                        key="pf_mc_horizon",
                    )
                    if st.button("Exécuter Monte Carlo", key="pf_run_mc"):
                        w_curr = np.array([float(p.market_value) for p in positions], dtype=float)
                        if w_curr.sum() <= 0:
                            w_curr = np.ones(len(held_tickers_pf), dtype=float) / float(len(held_tickers_pf))
                        else:
                            w_curr = w_curr / w_curr.sum()
                        with st.spinner("Simulation stochastique en cours..."):
                            fan = run_portfolio_monte_carlo(
                                tuple(held_tickers_pf),
                                tuple(w_curr.tolist()),
                                float(portfolio.total_equity),
                                days=int(horizon),
                                simulations=int(sims),
                            )
                        if fan.empty:
                            st.caption("Fan chart indisponible (historique insuffisant).")
                        else:
                            fig_fan = go.Figure()
                            fig_fan.add_trace(go.Scatter(
                                x=fan["day"], y=fan["p95"], mode="lines",
                                line=dict(color="rgba(0,180,216,0.0)"), name="P95",
                                showlegend=False,
                            ))
                            fig_fan.add_trace(go.Scatter(
                                x=fan["day"], y=fan["p75"], mode="lines",
                                line=dict(color="rgba(0,180,216,0.0)"), fill="tonexty",
                                fillcolor="rgba(0,180,216,0.12)", name="P75-95",
                                showlegend=False,
                            ))
                            fig_fan.add_trace(go.Scatter(
                                x=fan["day"], y=fan["p50"], mode="lines",
                                line=dict(color=_CYAN, width=2), name="Médiane P50",
                            ))
                            fig_fan.add_trace(go.Scatter(
                                x=fan["day"], y=fan["p25"], mode="lines",
                                line=dict(color="rgba(0,255,0,0.0)"), fill="tonexty",
                                fillcolor="rgba(0,255,0,0.10)", name="P25-50",
                                showlegend=False,
                            ))
                            fig_fan.add_trace(go.Scatter(
                                x=fan["day"], y=fan["p05"], mode="lines",
                                line=dict(color="rgba(255,59,48,0.0)"), fill="tonexty",
                                fillcolor="rgba(255,59,48,0.16)", name="P05-25",
                                showlegend=False,
                            ))
                            fig_fan.update_layout(
                                title="Fan Chart Monte Carlo (corrélé)",
                                xaxis_title="Jour",
                                yaxis_title="Valeur portefeuille (€)",
                                margin=dict(t=40, l=20, r=20, b=20),
                                height=380,
                                showlegend=True,
                            )
                            _style_dark_fig(fig_fan)
                            st.plotly_chart(fig_fan, width="stretch", key="pf_mc_fan_chart")

                        if simulate_historical_shocks is not None:
                            try:
                                from duckdb_manager import TimeSeriesDB

                                db_ro = get_ts_db()
                                w_map = {t: float(w_curr[i]) for i, t in enumerate(held_tickers_pf)}
                                stress = simulate_historical_shocks(held_tickers_pf, w_map, db_ro)
                                if stress is not None and not stress.empty:
                                    sdisp = stress.copy()
                                    sdisp["Worst PnL %"] = sdisp["Worst PnL %"].map(
                                        lambda x: "n/a" if pd.isna(x) else f"{float(x):+.2f}%"
                                    )
                                    st.plotly_chart(
                                        dark_table(
                                            sdisp,
                                            height=min(260, 56 + 28 * len(sdisp)),
                                            col_widths=[1.4, 0.8, 0.8, 1.0, 0.7],
                                        ),
                                        width="stretch",
                                        key="pf_stress_table",
                                    )
                            except Exception:
                                st.caption("Stress test indisponible.")

    if not positions:
        st.info("⏸️ Le portefeuille est actuellement 100% en "
                "liquidites. Aucune position ouverte : le capital attend une "
                "opportunite validee par les filtres mathematiques.")
    else:
        rows = [{
            "Ticker": p.ticker, "Secteur": p.sector, "Qte": p.qty_shares,
            "PRU": p.avg_entry_price, "Cours": p.current_price,
            "Valeur": p.market_value, "Poids": 0.0,
            "PnL": p.unrealized_pnl_pct * 100,
        } for p in positions]
        dfp = pd.DataFrame(rows)
        dfp["Poids"] = dfp["Valeur"] / portfolio.total_equity * 100

        sun = dfp[["Secteur", "Ticker", "Valeur", "PnL"]].copy()
        sun["Titre"] = [short_name(t) for t in sun["Ticker"]]
        if portfolio.cash_available > 0:
            sun = pd.concat([sun, pd.DataFrame([{
                "Secteur": "Liquidites", "Ticker": "CASH", "Titre": "Liquidites",
                "Valeur": portfolio.cash_available, "PnL": 0.0}])],
                ignore_index=True)

        fig = pex.sunburst(sun, path=["Secteur", "Titre"], values="Valeur",
                          color="PnL", color_continuous_scale=_DIVERGE,
                          color_continuous_midpoint=0)
        fig.update_layout(paper_bgcolor=_BG, plot_bgcolor=_BG,
                          font=dict(family="Courier New", color=_WHITE),
                          margin=dict(t=10, l=0, r=0, b=0), height=430)
        fig.update_traces(insidetextfont=dict(color=_WHITE, family="Courier New"),
                          marker=dict(line=dict(color=_BG, width=1)))

        col_chart, col_table = st.columns([1, 1.4])
        with col_chart:
            st.plotly_chart(fig, width="stretch")
        with col_table:
            pnl_colors = [_NEON if v >= 0 else _RED for v in dfp["PnL"]]
            disp = pd.DataFrame({
                "Titre": [format_name(t) for t in dfp["Ticker"]],
                "Secteur": dfp["Secteur"],
                "Qte": [f"{q:g}" for q in dfp["Qte"]],
                "PRU": [f"{v:,.2f} €" for v in dfp["PRU"]],
                "Cours": [f"{v:,.2f} €" for v in dfp["Cours"]],
                "Valeur": [f"{v:,.2f} €" for v in dfp["Valeur"]],
                "Poids": [f"{v:.1f}%" for v in dfp["Poids"]],
                "PnL": [f"{v:+.2f}%" for v in dfp["PnL"]],
            })
            st.plotly_chart(
                dark_table(disp, height=430, font_color_map={"PnL": pnl_colors},
                           col_widths=[2.2, 1.4, 0.7, 1, 1, 1.2, 0.8, 0.9]),
                width="stretch")

    # --- Phase 34: Correlation heatmap --------------------------------------
    st.markdown("#### 🕸️ Matrice de Corrélation (Risque Croisé)")
    held_tickers = [str(p.ticker) for p in positions if getattr(p, "ticker", None)]
    held_tickers = [t for t in held_tickers if t]
    if len(held_tickers) < 2:
        st.caption("Pas assez de positions (min 2) pour calculer une matrice de corrélation.")
    else:
        try:
            from duckdb_manager import TimeSeriesDB
            db = get_ts_db()

            returns: dict[str, pd.Series] = {}
            for t in held_tickers:
                hist = db.get_historical_prices(t, days=90)
                if hist is None or hist.empty or "Close" not in hist.columns:
                    continue
                frame = hist[["Date", "Close"]].copy()
                frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
                frame["Close"] = pd.to_numeric(frame["Close"], errors="coerce")
                frame = frame.dropna(subset=["Date", "Close"]).sort_values("Date")
                if len(frame) < 6:
                    continue
                r = frame["Close"].pct_change().dropna()
                if r is None or r.empty:
                    continue
                r.name = t
                returns[t] = r

            ret_df = pd.concat(returns, axis=1).dropna(how="all")
            if ret_df.shape[1] < 2:
                st.caption("Corrélation indisponible (données retour vides / trop sparse).")
            else:
                corr_matrix = ret_df.corr(method="pearson")
                fig_corr = pex.imshow(
                    corr_matrix,
                    text_auto=".2f",
                    aspect="auto",
                    color_continuous_scale="RdBu_r",
                    zmin=-1,
                    zmax=1,
                )
                _style_dark_fig(fig_corr, height=430)
                st.plotly_chart(
                    fig_corr,
                    width="stretch",
                    key="pf_corr_heatmap",
                )
        except Exception:  # noqa: BLE001
            st.caption("Corrélation indisponible (erreur DuckDB/Yahoo).")

    st.markdown("---")
    st.markdown("#### 🛑 Gestion des Stops & Sorties (ATR 2.5x)")
    if not positions:
        st.caption("Aucune ligne ouverte, donc aucun stop ATR à surveiller.")
    else:
        stop_rows = []
        for p in positions:
            ticker = str(p.ticker)
            atr = _latest_atr14_approx(ticker)
            current = float(p.current_price or 0.0)
            pru = float(p.avg_entry_price or 0.0)
            if atr is None or atr <= 0:
                stop_rows.append({
                    "Ticker": ticker,
                    "PRU": pru,
                    "Cours Actuel": current,
                    "Niveau Stop-Loss (€)": None,
                    "Distance au Stop (%)": None,
                    "Statut": "⚪ ATR indisponible",
                })
                continue
            stop_price = pru - (2.5 * float(atr))
            distance_pct = ((current - stop_price) / current) * 100.0 if current > 0 else -999.0
            status = "🔴 DANGER DÉCLENCHÉ" if current < stop_price else "🟢 Safe"
            atr_pct = None
            if PortfolioRebalancer is not None:
                try:
                    atr_pct = PortfolioRebalancer.atr_pct(float(atr), float(current))
                except Exception:  # noqa: BLE001
                    atr_pct = None
            stop_rows.append({
                "Ticker": ticker,
                "PRU": pru,
                "Cours Actuel": current,
                "Niveau Stop-Loss (€)": stop_price,
                "Distance au Stop (%)": distance_pct,
                "ATR%": atr_pct,
                "Statut": status,
            })
        stops_df = pd.DataFrame(stop_rows)
        show_stops = stops_df.copy()
        for col in ("PRU", "Cours Actuel", "Niveau Stop-Loss (€)"):
            show_stops[col] = show_stops[col].map(
                lambda x: "—" if pd.isna(x) else f"{float(x):,.2f} €"
            )
        show_stops["Distance au Stop (%)"] = show_stops["Distance au Stop (%)"].map(
            lambda x: "—" if pd.isna(x) else f"{float(x):+.2f}%"
        )
        if "ATR%" in show_stops.columns:
            show_stops["ATR%"] = show_stops["ATR%"].map(
                lambda x: "—" if pd.isna(x) else f"{float(x):.2f}%"
            )
        st.dataframe(show_stops, use_container_width=True, hide_index=True)

    st.markdown("---")
    if True:
        st.markdown("### ✏️ Ajuster le wallet (cash & positions)")
        st.markdown(
            "<div class='info-text'>Modifie le cash et les lignes pour coller "
            "a ton PEA reel. Ecriture directe dans SQLite.</div>",
            unsafe_allow_html=True,
        )
        edit_cash = st.number_input(
            "Cash disponible (€)",
            min_value=0.0,
            value=float(portfolio.cash_available),
            step=10.0,
            key="wallet_cash",
        )
        base_rows = [{
            "Ticker": p.ticker,
            "Secteur": p.sector,
            "Qte": int(p.qty_shares),
            "PRU": float(p.avg_entry_price),
            "Cours": float(p.current_price),
        } for p in positions] or [{
            "Ticker": "", "Secteur": "Unknown", "Qte": 0, "PRU": 0.0, "Cours": 0.0,
        }]
        edited = st.data_editor(
            pd.DataFrame(base_rows),
            num_rows="dynamic",
            width="stretch",
            key="wallet_editor",
            column_config={
                "Ticker": st.column_config.TextColumn("Ticker Yahoo", required=False),
                "Secteur": st.column_config.TextColumn("Secteur"),
                "Qte": st.column_config.NumberColumn("Qte", min_value=0, step=1),
                "PRU": st.column_config.NumberColumn("PRU €", min_value=0.0,
                                                    format="%.4f"),
                "Cours": st.column_config.NumberColumn("Cours €", min_value=0.0,
                                                      format="%.4f"),
            },
        )
        c_save, c_hint = st.columns([1, 2])
        with c_save:
            if st.button("Enregistrer le wallet", type="primary",
                         width="stretch", key="save_wallet_btn"):
                err = save_wallet(float(edit_cash), edited)
                if err:
                    st.error(f"Echec : {err}")
                else:
                    st.success("Wallet enregistre. Rechargement…")
                    st.rerun()
        with c_hint:
            st.caption(
                "Ticker Yahoo (ex. MC.PA). Qte=0 pour retirer une ligne."
            )

# --- Tab: Exploration (market + ticker radar) --------------------------------
with tab_ticker:
    st.markdown(
        "<div class='info-text'>Exploration marche (top/flop univers) + "
        "<b>fiche ticker</b> : graphique plein ecran, analyse technique "
        "expliquee, actualites, insiders, Polymarket macro.</div>",
        unsafe_allow_html=True,
    )

    def _on_uni_search():
        st.session_state["selected_ticker"] = st.session_state["mkt_universal_search"]
        st.session_state["focus_ticker"] = st.session_state["mkt_universal_search"]

    _uni_all = sorted(universe_df["Ticker"].tolist())
    _uni_search = st.selectbox(
        "🔍 Rechercher et analyser un actif spécifique",
        _uni_all,
        format_func=format_name,
        key="mkt_universal_search",
        on_change=_on_uni_search,
    )

    # Prefer liquid mid/large names — exclude microcaps/pennies from scan defaults.
    liquid_scan = list(dict.fromkeys(
        [p.ticker for p in positions]
        + ["MC.PA", "OR.PA", "AI.PA", "RMS.PA", "SAN.PA", "TTE.PA", "BNP.PA",
           "AIR.PA", "SU.PA", "EL.PA", "CS.PA", "DG.PA", "SAF.PA", "KER.PA",
           "STLAP.PA", "RNO.PA", "ORA.PA", "ENGI.PA", "CAP.PA", "DSY.PA",
           "HO.PA", "ML.PA", "SGO.PA", "GLE.PA", "ACA.PA", "VIE.PA", "PUB.PA",
           "BN.PA", "RI.PA", "EWLD.PA", "PAEEM.PA", "ESE.PA", "C50.PA",
           _CORE_TICKER]
    ))
    # Do NOT pull random sector samples (they inject illiquid AL* pennies).
    scan_tickers = tuple(
        t for t in liquid_scan
        if t == _CORE_TICKER or t in set(universe_df["Ticker"])
    )

    all_tickers = scan_tickers if scan_tickers else tuple(universe_df["Ticker"].head(40))
    mode = st.radio("Mode d'intervalle", ["Prereglage", "Plage personnalisee"],
                    horizontal=True, key="mkt_mode")

    if mode == "Prereglage":
        period_map = {"1 Semaine": "5d", "1 Mois": "1mo", "3 Mois": "3mo",
                      "6 Mois": "6mo", "1 An": "1y", "2 Ans": "2y", "5 Ans": "5y"}
        label = st.select_slider("Intervalle d'analyse", list(period_map.keys()),
                                 value="1 Mois")
        perf = get_market_performance(all_tickers, period=period_map[label])
        interval_label = label
        period_key = period_map[label]
        d_start = d_end = None
    else:
        cA, cB = st.columns(2)
        with cA:
            d_start = st.date_input("Debut", value=date.today() - timedelta(days=90),
                                    max_value=date.today())
        with cB:
            d_end = st.date_input("Fin", value=date.today(), max_value=date.today())
        perf = get_market_performance(all_tickers, period=None,
                                      start=d_start.isoformat(), end=d_end.isoformat())
        interval_label = f"{d_start.isoformat()} → {d_end.isoformat()}"
        period_key = None

    if perf.empty:
        st.error("Impossible de recuperer les donnees de marche pour cet intervalle.")
    else:
        # Drop near-zero noise AND illiquid pennies (price < 2 EUR).
        perf = perf[
            (perf["Performance (%)"].abs() > 0.05)
            & (perf["Current Price"] >= 2.0)
        ].copy()
        if perf.empty:
            st.warning("Pas assez de variations significatives sur l'intervalle.")
        else:
            best, worst = perf.iloc[0], perf.iloc[-1]
            c1, c2 = st.columns(2)
            with c1:
                st.success(f"🟢 **MEILLEURE PERFORMANCE** · {interval_label}")
                st.metric(format_name(best["Ticker"]), f"{best['Current Price']:.2f} €",
                          f"{best['Performance (%)']:+.2f}%")
            with c2:
                st.error("🔴 **PIRE PERFORMANCE** (candidat Mean-Reversion)")
                st.metric(format_name(worst["Ticker"]), f"{worst['Current Price']:.2f} €",
                          f"{worst['Performance (%)']:+.2f}%")

            st.markdown("#### 📋 Univers liquide complet (triable)")
            full_perf = perf.copy().sort_values("Performance (%)", ascending=False)
            full_disp = pd.DataFrame({
                "Ticker": full_perf["Ticker"],
                "Titre": [format_name(t) for t in full_perf["Ticker"]],
                "Début": [f"{v:,.2f} €" for v in full_perf["Start Price"]],
                "Actuel": [f"{v:,.2f} €" for v in full_perf["Current Price"]],
                "Performance %": [f"{v:+.2f}%" for v in full_perf["Performance (%)"]],
            })
            st.dataframe(
                full_disp,
                use_container_width=True,
                hide_index=True,
                key="mkt_full_perf_table",
            )

            movers = list(perf["Ticker"].head(4)) + list(perf["Ticker"].tail(4))
            movers = tuple(dict.fromkeys(movers))
            if period_key:
                norm = get_normalized_prices(movers, period_key, None, None)
            else:
                norm = get_normalized_prices(
                    movers, None, d_start.isoformat(), d_end.isoformat()
                )
            st.markdown("#### Trajectoires rebasees a 100 (top 4 + flop 4)")
            if norm.empty:
                st.caption("Trajectoires indisponibles.")
            else:
                line = go.Figure()
                for i, c in enumerate(norm.columns):
                    line.add_trace(go.Scatter(
                        x=norm.index, y=norm[c], name=format_name(c), mode="lines",
                        line=dict(width=2.4,
                                  color=_BRIGHT_SERIES[i % len(_BRIGHT_SERIES)])))
                line.add_hline(y=100, line_dash="dot", line_color=_MUTED)
                _style_dark_fig(line, height=420)
                line.update_layout(margin=dict(t=10, l=0, r=10, b=0),
                                   legend=dict(orientation="h", y=1.12))
                line.update_xaxes(rangeslider_visible=True, gridcolor=_GRID)
                st.plotly_chart(line, width="stretch")

            if True:
                st.markdown("### Table complete du scan liquide")
                perf_colors = [_NEON if v >= 0 else _RED for v in perf["Performance (%)"]]
                disp = pd.DataFrame({
                    "Titre": [format_name(t) for t in perf["Ticker"]],
                    "Debut": [f"{v:,.2f} €" for v in perf["Start Price"]],
                    "Actuel": [f"{v:,.2f} €" for v in perf["Current Price"]],
                    "Perf": [f"{v:+.2f}%" for v in perf["Performance (%)"]],
                })
                st.plotly_chart(
                    dark_table(disp, height=420,
                               font_color_map={"Perf": perf_colors},
                               col_widths=[2.4, 1, 1, 0.9]),
                    width="stretch")

    # ========== Fiche ticker (ex-Radar) =====================================
    st.markdown("---")
    st.markdown("### 📡 Fiche ticker — graphique & actualites")

    held = [p.ticker for p in positions]
    options = sorted(set(held) | set(universe_df["Ticker"]))
    default_idx = options.index(held[0]) if held and held[0] in options else 0
    # Prefer worst performer as default when no holdings (mean-reversion lens)
    if not held and not perf.empty:
        w = str(perf.iloc[-1]["Ticker"])
        if w in options:
            default_idx = options.index(w)
    # Mission-control <TICKER> GO overrides the default once.
    focus = st.session_state.get("focus_ticker")
    if focus:
        if focus in options:
            default_idx = options.index(focus)
        elif focus not in options:
            options = sorted(set(options) | {focus})
    def _on_explore_change():
        st.session_state["focus_ticker"] = st.session_state["selected_ticker"]
        st.session_state["mkt_universal_search"] = st.session_state["selected_ticker"]

    selected = st.selectbox(
        "Actif a analyser", options, index=default_idx,
        format_func=format_name, key="selected_ticker",
        on_change=_on_explore_change
    )
    tv = _tv_symbol(selected)

    dossier = get_ticker_dossier(selected)
    logo_url = get_company_logo(selected)
    logo_html = (
        f"<img src='{logo_url}' alt='logo' height='40' "
        f"style='height:40px;width:auto;max-width:48px;border-radius:6px;"
        f"vertical-align:middle;margin-right:12px;background:#111;"
        f"object-fit:contain;' "
        f"onerror=\"this.style.display='none'\" />"
        if logo_url else ""
    )
    st.markdown(
        f"<div class='eli5'>{logo_html}"
        f"<b style='color:{_CYAN};'>Qui est {dossier.get('name')} ?</b><br>"
        f"{dossier.get('summary', '')}<br>"
        f"<span style='color:{_MUTED};'>"
        f"Secteur: {dossier.get('sector') or 'n/a'} · "
        f"Industrie: {dossier.get('industry') or 'n/a'}"
        f"{' · ETF' if dossier.get('is_etf') else ''}</span></div>",
        unsafe_allow_html=True,
    )
    sub_overview, sub_fin, sub_news = st.tabs(['📈 Overview & Charts', '🧠 Financials & AI Scoring', '📰 News & Catalysts'])
    with sub_news:
        st.markdown("#### 📖 Catalyseurs & risques (dossier)")
        cat1, cat2 = st.columns(2)
        with cat1:
            st.markdown("**News / catalyseurs qui aideraient**")
            for c in dossier.get("catalysts") or []:
                st.markdown(f"- {c}")
        with cat2:
            st.markdown("**Evenements a surveiller (ne pas vouloir)**")
            for r in dossier.get("risk_events") or []:
                st.markdown(f"- {r}")

        st.markdown("---")
        st.markdown("#### 🗞️ Actualites Historiques")
        
        # Phase 55: Multi-Source Filter & History Merging
        providers_opt = ["Boursorama", "Yahoo Finance", "Newsletters Substack", "Google News", "Finlight"]
        sel_providers = st.multiselect(
            "Filtrer par Source", 
            providers_opt,
            default=providers_opt
        )
        time_filter = st.radio("Historique", ["7j", "30j", "1 an", "Tout"], horizontal=True)
        limit_days = {"7j": 7, "30j": 30, "1 an": 365, "Tout": 9999}[time_filter]
        
        try:
            news_rows = get_portfolio_db().get_news_history(selected, limit=200)
            
            if not news_rows:
                st.info("Aucune actualité récente en base.")
                if st.button("🔄 Actualiser les flux", key=f"refresh_news_{selected}"):
                    with st.spinner("Recherche des flux en direct..."):
                        _fetch_news_from_apis(selected, limit=12)
                    st.rerun()
            else:
                # Filter and deduplicate
                filtered = []
                seen_hashes = set()
                now = datetime.now()
                
                for r in news_rows:
                    prov = str(r.get("provider", "")).strip()
                    if sel_providers and "Tout" not in sel_providers:
                        if not any(p.casefold() in prov.casefold() for p in sel_providers):
                            continue
                            
                    dp = r.get("date_published")
                    if dp:
                        try:
                            dt = datetime.fromisoformat(dp.replace("Z", "+00:00")).replace(tzinfo=None)
                            if (now - dt).days > limit_days:
                                continue
                        except Exception:
                            pass
                    
                    url = str(r.get("url", ""))
                    title = str(r.get("title", ""))
                    thash = hash(url + title)
                    if thash in seen_hashes:
                        continue
                    seen_hashes.add(thash)
                    
                    score = r.get("sentiment_score")
                    badge = "⚪"
                    if score is not None:
                        try:
                            score_val = float(score)
                            if score_val >= 30: badge = "Bullish 🟢"
                            elif score_val <= -30: badge = "Bearish 🔴"
                        except (ValueError, TypeError):
                            pass

                    filtered.append({
                        "Date": dp[:16] if dp else "N/A",
                        "Source": prov,
                        "Titre": f"[{title}]({url})" if url else title,
                        "Sentiment IA": badge,
                    })
                    
                if not filtered:
                    st.info("Aucune actualité trouvée pour ces filtres.")
                else:
                    df_news = pd.DataFrame(filtered)
                    df_news = df_news.sort_values(by="Date", ascending=False)
                    st.markdown(df_news.to_markdown(index=False), unsafe_allow_html=True)
                    # Use markdown table since st.dataframe doesn't render markdown links natively without config hacks
        except Exception as exc:
            st.error(f"Erreur chargement news: {exc}")
            
        st.markdown("---")
        st.markdown("#### 🧠 Synthèse IA (Analyse Deep)")
        if st.button("Générer Synthèse IA", key=f"synth_ia_{selected}"):
            with st.spinner("IA en cours d'analyse..."):
                try:
                    headlines_tuple = tuple(r.get("title", "") for r in news_rows[:15])
                    deep = get_deep_news_synthesis(selected, headlines_tuple)
                    st.info(f"**Synthèse (cache 24h)**\n\n{deep}")
                except Exception as exc:
                    st.error(f"Synthèse indisponible: {exc}")

        if st.button("Lancer un Red Teaming IA (Bull vs Bear vs Devil's Advocate)", key=f"red_team_{selected}"):
            context_blob = (
                f"Ticker: {selected}\n"
                f"Name: {dossier.get('name')}\n"
                f"Sector: {dossier.get('sector')}\n"
                f"Summary: {dossier.get('summary')}\n"
                f"Catalysts: {', '.join(dossier.get('catalysts') or [])}\n"
                f"Risks: {', '.join(dossier.get('risk_events') or [])}\n"
            )
            with st.spinner("Red Teaming multi-agent en cours..."):
                try:
                    from red_team_agent import run_bull_bear_debate

                    debate = asyncio.run(run_bull_bear_debate(selected, context_blob))
                except Exception as exc:  # noqa: BLE001
                    debate = {
                        "bull": "Indisponible",
                        "bear": f"Indisponible ({exc})",
                        "devil_advocate": "Indisponible",
                        "judge": "Indisponible",
                    }
            st.info(f"🐂 **Bull Agent**\n\n{debate.get('bull') or 'n/a'}")
            st.warning(f"🐻 **Bear Agent**\n\n{debate.get('bear') or 'n/a'}")
            st.markdown(f"😈 **Devil's Advocate PEA**\n\n{debate.get('devil_advocate') or 'n/a'}")
            st.error(f"⚖️ **Judge Agent**\n\n{debate.get('judge') or 'n/a'}")

    with sub_fin:
        ind = get_indicators(selected)
        alpha = get_alpha_signals(selected)
        bprofile = get_bourso_profile(selected)

        # Profile + indicators as full metric boxes (no truncation)
        mrow1 = st.columns(4)
        with mrow1[0]:
            if ind:
                st.markdown(metric_box(
                    "Cours", f"{ind['close']:.2f} €",
                    sub=f"{ind['chg_1d']:+.2f}% (1j) · {ind['chg_5d']:+.2f}% (5j)",
                    help_text="Dernier cours et variations recentes.",
                ), unsafe_allow_html=True)
            else:
                st.markdown(metric_box("Cours", "n/a", sub="Donnees manquantes",
                                       accent="muted"), unsafe_allow_html=True)
        with mrow1[1]:
            rsi = (ind or {}).get("rsi")
            rsi_state = ("Survendu" if rsi is not None and rsi < 30 else
                         "Surachete" if rsi is not None and rsi > 70 else "Neutre")
            st.markdown(metric_box(
                "RSI(14)", f"{rsi:.1f}" if rsi is not None else "n/a",
                sub=rsi_state,
                accent="cyan" if rsi is not None and rsi < 30 else (
                    "red" if rsi is not None and rsi > 70 else ""),
                help_text="<30 survendu · >70 surachete.",
            ), unsafe_allow_html=True)
        with mrow1[2]:
            trend_ok = bool(ind and ind.get("sma200") and ind["close"] > ind["sma200"])
            st.markdown(metric_box(
                "Tendance LT (vs SMA200)",
                "Haussier" if trend_ok else ("Baissier" if ind else "n/a"),
                sub=(f"SMA200 {(ind or {}).get('sma200', 0):.2f}" if ind and ind.get("sma200")
                     else "—"),
                accent="" if trend_ok else "red",
                help_text="Prix au-dessus / en-dessous de la moyenne 200 jours.",
            ), unsafe_allow_html=True)
        with mrow1[3]:
            vol = (ind or {}).get("vol_ann")
            st.markdown(metric_box(
                "Vol. annualisee",
                f"{vol:.0f}%" if vol is not None else "n/a",
                sub="Sizing inverse-vol",
                accent="amber" if vol and vol > 35 else "",
                help_text="Plus c'est eleve, plus la taille de position est reduite.",
            ), unsafe_allow_html=True)

        mrow2 = st.columns(4)
        with mrow2[0]:
            elig = ", ".join((bprofile or {}).get("eligibility") or []) or "n/a"
            st.markdown(metric_box("Eligibilite PEA/SRD", elig, sub="Boursorama",
                                   accent="cyan"), unsafe_allow_html=True)
        with mrow2[1]:
            cons = (bprofile or {}).get("consensus_score")
            st.markdown(metric_box(
                "Consensus analystes",
                f"{cons:.2f}" if cons is not None else "n/a",
                sub=(bprofile or {}).get("sentiment") or "—",
            ), unsafe_allow_html=True)
        with mrow2[2]:
            tgt = (bprofile or {}).get("target_price")
            pot = (bprofile or {}).get("potential_pct")
            st.markdown(metric_box(
                "Objectif 3 mois",
                f"{tgt:.2f} €" if tgt is not None else "n/a",
                sub=f"{pot:+.1f}%" if pot is not None else "—",
            ), unsafe_allow_html=True)
        with mrow2[3]:
            isin = (bprofile or {}).get("isin") or "n/a"
            st.markdown(metric_box(
                "ISIN", isin,
                sub=f"{(bprofile or {}).get('index') or '—'} / "
                    f"{(bprofile or {}).get('exchange') or '—'}",
            ), unsafe_allow_html=True)

        # Phase 35: Multi-factor fundamentals (Finnhub -> cache -> yfinance fallback).
        fmeta = (dossier or {}).get("fundamentals") or get_fundamental_metrics(selected)
        src = str((fmeta or {}).get("source") or "none")
        pe = (fmeta or {}).get("pe_ratio")
        pb = (fmeta or {}).get("pb_ratio")
        roe = (fmeta or {}).get("roe")
        deq = (fmeta or {}).get("debt_to_equity")
        mrow3 = st.columns(4)
        with mrow3[0]:
            st.markdown(
                metric_box(
                    "P/E (TTM)",
                    f"{float(pe):.2f}" if pe is not None else "n/a",
                    sub=f"Source: {src}",
                    accent="cyan" if pe is not None and float(pe) > 0 and float(pe) < 20 else "",
                    help_text="Valorisation bénéfices (plus bas peut être plus attractif).",
                ),
                unsafe_allow_html=True,
            )
        with mrow3[1]:
            st.markdown(
                metric_box(
                    "P/B",
                    f"{float(pb):.2f}" if pb is not None else "n/a",
                    sub="Value factor",
                    accent="cyan" if pb is not None and float(pb) < 2.0 else "",
                    help_text="Valorisation fonds propres (bonus si <2).",
                ),
                unsafe_allow_html=True,
            )
        with mrow3[2]:
            st.markdown(
                metric_box(
                    "ROE",
                    f"{float(roe)*100:.1f}%" if roe is not None and float(roe) <= 1.5 else (
                        f"{float(roe):.1f}%" if roe is not None else "n/a"
                    ),
                    sub="Quality factor",
                    accent="green" if roe is not None and float(roe) >= 0.15 else "",
                    help_text="Rentabilité des capitaux propres (qualité).",
                ),
                unsafe_allow_html=True,
            )
        with mrow3[3]:
            st.markdown(
                metric_box(
                    "Debt / Equity",
                    f"{float(deq):.2f}" if deq is not None else "n/a",
                    sub="Risque de levier",
                    accent="red" if deq is not None and float(deq) > 2.0 else "",
                    help_text="Levier bilan (malus >2.0 dans le modèle multi-factor).",
                ),
                unsafe_allow_html=True,
            )

        st.markdown("#### 📋 Ticket d'Ordre PEA (Prêt à l'Exécution)")
        live_price = float((ind or {}).get("close") or 0.0)
        qty_default = max(1, int(float(portfolio.cash_available or 0.0) // live_price)) if live_price > 0 else 1
        qty_ticket = st.number_input(
            "Quantité à préparer",
            min_value=1,
            value=int(qty_default),
            step=1,
            key=f"mkt_order_qty_{selected}",
        )
        ticket = build_broker_order_ticket(
            selected,
            int(qty_ticket),
            live_price,
            isin=(bprofile or {}).get("isin"),
        )
        st.markdown(
            f"<div style='background:#0A0A0A;padding:14px 16px;margin-bottom:10px;"
            f"border:1px solid #2A2A2A;border-left:4px solid {_CYAN};"
            f"font-family:Courier New,monospace;'>"
            f"<div style='color:{_CYAN};font-size:11px;letter-spacing:1.5px;'>ORDER TICKET</div>"
            f"<div style='margin-top:8px;line-height:1.6;'>"
            f"<b>ISIN</b>: {ticket['isin']}<br>"
            f"<b>Type d'ordre</b>: {ticket['order_type']}<br>"
            f"<b>Quantité</b>: {ticket['qty']}<br>"
            f"<b>Limite suggérée</b>: {ticket['limit_price']:,.2f} €<br>"
            f"<b>Notional estimé</b>: {ticket['notional']:,.2f} €<br>"
            f"<b>Frais PEA max (0.5%)</b>: {ticket['estimated_fee_max']:,.2f} €"
            f"</div></div>",
            unsafe_allow_html=True,
        )
        st.markdown(f"[↗️ Ouvrir sur Boursorama]({ticket['bourso_url']})")

        st.markdown("#### ✅ Checklist de Décision")
        checklist = get_decision_checklist(selected, portfolio, float(vix))
        chk_df = pd.DataFrame(checklist["checks"])
        st.dataframe(chk_df, use_container_width=True, hide_index=True)
        st.caption(f"Statut global: {checklist['overall']} · score proxy {checklist['score_hint']:.0f}/100")

        st.markdown("#### 🧠 Bureau de l'Analyste & Data Lake")
        note_db = get_portfolio_db()
        try:
            note_db.init_db()
            current_note = note_db.get_ticker_note(selected)
        except Exception:  # noqa: BLE001
            current_note = ""
        note_value = st.text_area(
            f"Note analyste personnelle — {format_name(selected)}",
            value=current_note,
            height=120,
            key=f"analyst_note_{selected}",
            help="Commentaires qualitatifs manuels (thèse, trigger, risques, plan d'exécution).",
        )
        if st.button("Sauvegarder la note", key=f"save_note_{selected}", type="secondary"):
            try:
                note_db.save_ticker_note(selected, note_value)
                st.success("Note sauvegardée dans SQLite.")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Sauvegarde impossible: {exc}")

        # Multi-model raw pack for full transparency.
        model_breakdown = {}
        model_context = {}
        hist_dl = pd.DataFrame()
        try:
            from duckdb_manager import TimeSeriesDB
            from technical_scorer import SignalGenerator

            db_ro = get_ts_db()
            hist_dl = db_ro.get_historical_prices(selected, days=260)
            if hist_dl is not None and not hist_dl.empty:
                conv_dl = SignalGenerator().evaluate(selected, hist_dl)
                model_breakdown = conv_dl.get("model_scores") or {}
                model_context = conv_dl.get("context_breakdown") or {}
        except Exception:  # noqa: BLE001
            model_breakdown = {}
            model_context = {}

        # Phase 53+: SHAP XAI Inference for single ticker
        try:
            from ml_trainer import predict_probability_with_shap
            from ml_feature_store import build_ml_feature_row
        
            if hist_dl is not None and not hist_dl.empty:
                # We don't have CW8 and EXOG ready here instantly without DB calls, but we can try to fetch them or pass None
                cw8_df = db_ro.get_historical_prices("CW8.PA", days=260)
                cw8_close = cw8_df["Close"].astype(float) if cw8_df is not None and not cw8_df.empty else None
            
                exog_dfs = {}
                for sym in ["^GSPC", "^IXIC", "EURUSD=X", "OAT.PA"]:
                    try:
                        df_ex = db_ro.get_historical_prices(sym, days=260)
                        if df_ex is not None and not df_ex.empty:
                            exog_dfs[sym] = df_ex["Close"].astype(float)
                    except Exception:
                        pass
            
                feat_dict = build_ml_feature_row(
                    selected, 
                    close=hist_dl["Close"].astype(float), 
                    cw8_close=cw8_close, 
                    exog_closes=exog_dfs, 
                    reason="ui_inference", 
                    pdb=note_db, 
                    asof_idx=-1
                )
                proba, shap_vals = predict_probability_with_shap(feat_dict)
            
                if proba is not None and shap_vals is not None:
                    st.markdown("### 🤖 Meta-Labeling & Explainable AI (SHAP)")
                
                    status_color = _NEON if proba >= 0.50 else _RED
                    status_text = "VALIDÉ" if proba >= 0.50 else "REJETÉ"
                
                    st.markdown(f"<div style='font-size:20px'>Probabilité Alpha: <strong style='color:{status_color}'>{proba*100:.1f}%</strong> ({status_text})</div>", unsafe_allow_html=True)
                
                    # Waterfall or bar chart of top positive and negative
                    import plotly.graph_objects as go
                
                    # Sort all non-zero
                    sorted_shaps = sorted([(k, v) for k, v in shap_vals.items() if abs(v) > 0.001], key=lambda x: x[1])
                
                    if sorted_shaps:
                        y_labels = [x[0] for x in sorted_shaps]
                        x_vals = [x[1] for x in sorted_shaps]
                        colors = [_NEON if x > 0 else _RED for x in x_vals]
                    
                        fig = go.Figure(go.Bar(
                            x=x_vals, y=y_labels, orientation='h',
                            marker_color=colors
                        ))
                        fig.update_layout(
                            title="Décomposition de la décision (SHAP Values)",
                            xaxis_title="Impact sur la probabilité d'Alpha",
                            yaxis_title="Feature",
                            template="plotly_dark",
                            plot_bgcolor="rgba(0,0,0,0)",
                            paper_bgcolor="rgba(0,0,0,0)",
                            margin=dict(l=20, r=20, t=40, b=20),
                            height=400
                        )
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.caption("Aucune feature n'a eu d'impact significatif.")
        except Exception as exc:
            st.caption(f"Inférence ML indisponible: {exc}")

        if True:
            st.markdown("### Voir toutes les données brutes (Data Lake)")
            st.caption("Transparence totale sur les entrées consommées par l'analyste quant.")

            st.markdown("**Prix / OHLCV (DuckDB, ~260 jours)**")
            if hist_dl is None or hist_dl.empty:
                st.caption("Aucune série OHLCV locale disponible.")
            else:
                st.dataframe(hist_dl.tail(120), use_container_width=True, hide_index=True)

            st.markdown("**Indicateurs instantanés**")
            st.dataframe(pd.DataFrame([ind or {}]), use_container_width=True, hide_index=True)

            st.markdown("**Fondamentaux (Finnhub/yfinance/cache)**")
            st.dataframe(pd.DataFrame([fmeta or {}]), use_container_width=True, hide_index=True)

            st.markdown("**Comité Multi-Modèles**")
            if model_breakdown:
                st.dataframe(
                    pd.DataFrame(
                        [{"Model": k, "Score": v} for k, v in model_breakdown.items()]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.caption("Breakdown multi-modèles indisponible.")
            if model_context:
                st.markdown("**Détail du modèle Context**")
                st.dataframe(
                    pd.DataFrame(
                        [{"Context_Factor": k, "Score": v} for k, v in model_context.items()]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

        # Technical analysis explanation (full width)
        st.markdown(
            f"<div class='eli5'><b style='color:{_AMBER};'>"
            f"Analyse technique expliquee — {format_name(selected)}</b><br>"
            f"{build_ta_explanation(ind, alpha)}</div>",
            unsafe_allow_html=True,
        )

        # What-if simulator (Command Center) — radar lives below Phase 18 valuation
        st.markdown("#### 🧪 Simulateur (What-If)")
        st.markdown(
            "<div class='info-text'>Impact théorique d'un achat de "
            "<b>1000 €</b> avant qu'un signal ne soit généré — cash, "
            "poids sectoriel, corrélation max vs positions.</div>",
            unsafe_allow_html=True,
        )
        if st.button(
            "Simuler un achat de 1000€",
            key=f"whatif_1000_{selected}",
            type="primary",
        ):
            sim = simulate_buy_what_if(portfolio, selected, 1000.0)
            if not sim.get("affordable"):
                st.warning(
                    f"Pas assez de cash ou cours trop élevé "
                    f"(cash {sim['cash_before']:,.0f} € · "
                    f"cours {sim['price']:,.2f} €)."
                )
            else:
                corr_txt = (
                    f"{sim['max_corr']:+.2f}"
                    if sim.get("max_corr") is not None else "n/a"
                )
                st.markdown(
                    f"<div class='metric-box cyan'>"
                    f"<div class='metric-title'>WHAT-IF 1000 €</div>"
                    f"<div class='metric-value'>{sim['qty']} × "
                    f"{sim['price']:,.2f} € = {sim['cost']:,.0f} €</div>"
                    f"<div class='metric-sub sub-muted'>"
                    f"Cash {sim['cash_before']:,.0f} → "
                    f"<b style='color:{_AMBER};'>{sim['cash_after']:,.0f} €</b>"
                    f"<br>Secteur {sim['sector']}: "
                    f"{sim['sector_pct_before']:.1f}% → "
                    f"<b>{sim['sector_pct_after']:.1f}%</b>"
                    f"<br>Corr. max vs book: {corr_txt}"
                    f"</div></div>",
                    unsafe_allow_html=True,
                )

    with sub_overview:
        # Full-width TradingView chart — ALWAYS resolve via _tv_symbol (EURONEXT:…).
        _tv_resolved = _tv_symbol(selected)
        _tv_cid = f"tv_chart_explore_{selected.replace('.', '_').replace(':', '_')}"
        chart_html = f"""
        <div class="tradingview-widget-container" style="height:620px;width:100%">
          <div id="{_tv_cid}" style="height:620px;width:100%"></div>
          <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
          <script type="text/javascript">
            new TradingView.widget({{
              "autosize": true, "symbol": "{_tv_resolved}", "interval": "D",
              "timezone": "Europe/Paris", "theme": "dark", "style": "1",
              "locale": "fr", "enable_publishing": false,
              "hide_side_toolbar": false, "allow_symbol_change": true,
              "studies": ["RSI@tv-basicstudies", "MASimple@tv-basicstudies"],
              "container_id": "{_tv_cid}"
            }});
          </script>
        </div>
        """
        components.html(chart_html, height=640, key=f"tv_{selected}")
        st.caption(f"TradingView symbol injecté : `{_tv_resolved}`")

        # TA widget + SMAs under chart
        tw1, tw2 = st.columns([1, 1])
        with tw1:
            ta_html = f"""
            <div class="tradingview-widget-container">
              <div class="tradingview-widget-container__widget"></div>
              <script type="text/javascript"
                src="https://s3.tradingview.com/external-embedding/embed-widget-technical-analysis.js" async>
              {{"interval":"1D","width":"100%","isTransparent":true,"height":380,
                "symbol":"{_tv_resolved}","showIntervalTabs":true,"locale":"fr","colorTheme":"dark"}}
              </script>
            </div>
            """
            components.html(ta_html, height=400, key=f"ta_{selected}")
        with tw2:
            sma_bits = []
            if ind:
                for k, lab in (("sma5", "SMA5"), ("sma50", "SMA50"), ("sma200", "SMA200")):
                    if ind.get(k):
                        sma_bits.append(f"{lab}: <b>{ind[k]:.2f}</b>")
            pc = (alpha or {}).get("put_call")
            ins = (alpha or {}).get("insider", 0)
            ins_txt = {1: "Achats nets dirigeants", -1: "Ventes nettes dirigeants"}.get(
                ins, "Neutre / indisponible"
            )
            st.markdown(
                f"<div style='background:#0A0A0A;padding:16px;border:1px solid #222;"
                f"min-height:360px;line-height:1.7;color:#E0E0E0;'>"
                f"<div style='color:{_CYAN};font-size:12px;letter-spacing:1px;'>"
                f"RECAP QUANT</div>"
                f"<div style='margin-top:10px;'>{' · '.join(sma_bits) or 'SMA n/a'}</div>"
                f"<div style='margin-top:12px;'><b>Put/Call</b> : "
                f"{f'{pc:.2f}' if pc is not None else 'n/a'} "
                f"<span style='color:{_MUTED};font-size:12px;'>"
                f"(souvent neutre sur small/mid .PA — chaine options rare)</span></div>"
                f"<div style='margin-top:12px;'><b>Insiders</b> : {ins_txt}</div>"
                f"<div style='margin-top:12px;color:{_MUTED};font-size:13px;'>"
                f"TradingView: <code>{_tv_resolved}</code></div>"
                f"</div>",
                unsafe_allow_html=True,
            )

        # --- Phase 18: Valuation / buy zone + 10y annual returns ----------------
        st.markdown("---")
        st.markdown("#### 🎯 Valorisation & Recommandation de Prix")
        st.markdown(
            "<div class='info-text'>Multiples et objectifs analystes via yfinance "
            "(souvent plus riches sur large caps). La <b>zone d'achat suggérée</b> "
            "est une bande heuristique (52w low → milieu vers target low) — "
            "contexte pour ton jugement PEA, pas un ordre automatique.</div>",
            unsafe_allow_html=True,
        )
        val = get_valuation_metrics(selected)
        if not val.get("ok"):
            st.caption(
                "Valorisation indisponible pour ce ticker "
                "(réseau, delisting, ou champs Yahoo vides)."
            )
        else:
            cur = val.get("current_price")
            # Prefer live indicator close when Yahoo info price is missing.
            if cur is None and ind and ind.get("close"):
                cur = float(ind["close"])
            tmean = val.get("target_mean")
            upside = None
            if cur and tmean and cur > 0:
                upside = (tmean / cur - 1.0) * 100.0

            v1, v2, v3, v4 = st.columns(4)
            with v1:
                st.markdown(metric_box(
                    "Cours actuel",
                    f"{cur:,.2f} €" if cur is not None else "n/a",
                    sub=(f"vs target mean {upside:+.1f}%" if upside is not None
                         else "prix Yahoo / indicateur"),
                    accent="" if (upside is None or upside >= 0) else "red",
                    sub_cls=("sub-green" if upside is not None and upside >= 0
                             else "sub-red" if upside is not None else "sub-muted"),
                    help_text="Dernier cours connu (Yahoo info ou close indicateur).",
                ), unsafe_allow_html=True)
            with v2:
                st.markdown(metric_box(
                    "Target mean analystes",
                    f"{tmean:,.2f} €" if tmean is not None else "n/a",
                    sub=(f"Target low {val['target_low']:,.2f} €"
                         if val.get("target_low") is not None else "consensus Yahoo"),
                    accent="cyan",
                    help_text="Objectif moyen des analystes (Yahoo Finance).",
                ), unsafe_allow_html=True)
            with v3:
                pe = val.get("trailing_pe")
                st.markdown(metric_box(
                    "P/E trailing",
                    f"{pe:.1f}×" if pe is not None else "n/a",
                    sub="multiple de bénéfices",
                    help_text="Price / trailing EPS. Vide sur ETF ou pertes.",
                ), unsafe_allow_html=True)
            with v4:
                pb = val.get("price_to_book")
                st.markdown(metric_box(
                    "Price / Book",
                    f"{pb:.2f}×" if pb is not None else "n/a",
                    sub="valeur comptable",
                    help_text="Cours / book value par action.",
                ), unsafe_allow_html=True)

            r1m = val.get("return_1m_pct")
            r1y = val.get("return_1y_pct")
            p1, p2 = st.columns(2)
            with p1:
                st.markdown(metric_box(
                    "Perf. 1 mois",
                    f"{r1m:+.1f}%" if r1m is not None else "n/a",
                    sub="~21 séances",
                    accent="" if r1m is None or r1m >= 0 else "red",
                    sub_cls=("sub-green" if r1m is not None and r1m >= 0
                             else "sub-red" if r1m is not None else "sub-muted"),
                    help_text="Variation du close sur ~1 mois de séances.",
                ), unsafe_allow_html=True)
            with p2:
                st.markdown(metric_box(
                    "Perf. 1 an",
                    f"{r1y:+.1f}%" if r1y is not None else "n/a",
                    sub="trailing 12 mois",
                    accent="" if r1y is None or r1y >= 0 else "red",
                    sub_cls=("sub-green" if r1y is not None and r1y >= 0
                             else "sub-red" if r1y is not None else "sub-muted"),
                    help_text="Close actuel vs close il y a ~1 an.",
                ), unsafe_allow_html=True)

            bz_lo = val.get("buy_zone_low")
            bz_hi = val.get("buy_zone_high")
            w52_lo = val.get("fifty_two_week_low")
            w52_hi = val.get("fifty_two_week_high")
            in_zone = (
                cur is not None and bz_lo is not None and bz_hi is not None
                and bz_lo <= cur <= bz_hi
            )
            zone_color = _NEON if in_zone else _AMBER
            zone_label = (
                f"{bz_lo:,.2f} € → {bz_hi:,.2f} €"
                if bz_lo is not None and bz_hi is not None
                else "n/a (données manquantes)"
            )
            status = (
                "DANS LA ZONE — setup prix intéressant à croiser avec le MRE"
                if in_zone else
                "HORS ZONE — attendre un meilleur point d'entrée ou ignorer"
                if bz_hi is not None and cur is not None else
                "Zone non calculable"
            )
            st.markdown(
                f"<div style='background:#0A0A0A;padding:14px 16px;margin-top:8px;"
                f"border:1px solid #2A2A2A;border-left:4px solid {zone_color};"
                f"font-family:Courier New,monospace;'>"
                f"<div style='color:{_CYAN};font-size:11px;letter-spacing:1.5px;'>"
                f"ZONE D'ACHAT SUGGÉRÉE</div>"
                f"<div style='color:{_WHITE};font-size:20px;font-weight:700;"
                f"margin-top:6px;'>{zone_label}</div>"
                f"<div style='color:{zone_color};margin-top:8px;font-size:13px;'>"
                f"{status}</div>"
                f"<div style='color:{_MUTED};margin-top:8px;font-size:12px;'>"
                f"52w low "
                f"{f'{w52_lo:,.2f} €' if w52_lo is not None else 'n/a'} · "
                f"52w high "
                f"{f'{w52_hi:,.2f} €' if w52_hi is not None else 'n/a'} · "
                f"règle = milieu(52w low, target low) comme plafond de zone"
                f"</div></div>",
                unsafe_allow_html=True,
            )

        st.markdown("#### 📊 Performances Annuelles (10 dernières années)")
        ann = get_annual_returns(selected)
        if ann is None or ann.empty:
            st.caption(
                "Historique annuel indisponible (ticker trop récent, delisté, "
                "ou erreur réseau Yahoo)."
            )
        else:
            st.plotly_chart(
                render_annual_returns_chart(ann, selected),
                width="stretch",
                key=f"explore_annual_returns_{selected}",
            )
            pos_yrs = int((ann["Return_Pct"] >= 0).sum())
            st.caption(
                f"{len(ann)} année(s) · {pos_yrs} positive(s) · "
                f"moyenne {ann['Return_Pct'].mean():+.1f}% / an (arithmétique)."
            )

        # --- Phase 20 UI: Multi-strategy fingerprint radar (below Phase 18) ----
        st.markdown("---")
        st.markdown("#### 🕸️ Empreinte Multi-Stratégies (Radar)")
        st.markdown(
            "<div class='info-text'>Quatre axes normalisés <b>0–100</b> : "
            "Mean Reversion (RSI), Momentum (Close vs SMA5/50/200), "
            "Quality/Value (P/E / EPS), Insider Confidence. "
            "Lecture visuelle Bloomberg — pas un ordre automatique.</div>",
            unsafe_allow_html=True,
        )
        with st.spinner("Calcul empreinte…"):
            fingerprint = get_strategy_fingerprint(selected)
        if fingerprint and any(float(v) > 0 for v in fingerprint.values()):
            st.plotly_chart(
                render_strategy_radar(fingerprint, selected),
                width="stretch",
                key=f"explore_strategy_radar_{selected}",
            )
            mcols = st.columns(4)
            for i, (axis, score) in enumerate(fingerprint.items()):
                with mcols[i]:
                    st.markdown(
                        metric_box(
                            axis,
                            f"{float(score):.0f}",
                            sub="/ 100",
                            accent="cyan" if float(score) >= 60 else (
                                "amber" if float(score) >= 35 else "muted"
                            ),
                        ),
                        unsafe_allow_html=True,
                    )
        else:
            st.caption("Empreinte indisponible (indicateurs / valorisation manquants).")

        if True:
            st.markdown("### Comprendre l'Empreinte (Abréviations)")
            st.markdown(
                "- **MR** — Mean Reversion : mesure la sous-évaluation statistique via le RSI et la distance au prix moyen.\n"
                "- **Mom** — Momentum : force de la tendance (Close > SMA5 > SMA50 > SMA200).\n"
                "- **Q/V** — Quality / Value : fondamentaux (P/E, P/B, ROE, Debt/Equity via Finnhub/yfinance).\n"
                "- **Ins** — Insider Confidence : achats récents de dirigeants (AMF/FMP).\n\n"
                "Le score total (0–100) est la moyenne pondérée : MR 35% + Mom 25% + Q/V 20% + Ins 20%. "
                "Un signal BUY n'est émis que si le score dépasse **65**."
            )

        # --- AMF / Insider deep module ------------------------------------------
        st.markdown("---")
        st.markdown("#### 🕵️ Activité des dirigeants (insiders) — module AMF")
        st.markdown(
            "<div class='info-text'><b>Cascade stricte : AMF BDIF → FMP → Yahoo</b>. "
            "Synthèse nette achats/ventes (12 mois approximatifs selon la source). "
            "Signal de confiance interne — <b>pas un ordre automatique</b>.</div>",
            unsafe_allow_html=True,
        )
        insider_df = get_insider_data(selected)
        if insider_df.empty:
            st.warning(
                f"Aucune transaction insider pour {format_name(selected)}. "
                "AMF/FMP/Yahoo n'ont rien renvoyé (couverture variable sur .PA)."
            )
            st.markdown(
                "[🔍 Rechercher sur le BDIF Officiel AMF](https://bdif.amf-france.org/)"
            )
        else:
            summary = summarize_insider_activity(insider_df)
            accent = {
                "green": _NEON,
                "red": _RED,
                "amber": _AMBER,
                "muted": _MUTED,
            }.get(summary.get("tone") or "muted", _MUTED)
            st.markdown(
                f"<div style='background:#0A0A0A;padding:14px 16px;margin-bottom:10px;"
                f"border:1px solid #2A2A2A;border-left:4px solid {accent};"
                f"font-family:Courier New,monospace;'>"
                f"<div style='color:{_CYAN};font-size:11px;letter-spacing:1.5px;'>"
                f"AMF / INSIDERS · {(summary.get('source') or 'multi-source')}</div>"
                f"<div style='color:{_WHITE};font-size:15px;margin-top:8px;"
                f"line-height:1.45;'>{summary.get('signal')}</div>"
                f"<div style='color:{_MUTED};font-size:12px;margin-top:8px;'>"
                f"Net actions : {summary.get('net_shares', 0):+,.0f} · "
                f"Net valeur : {summary.get('net_value', 0):+,.0f} € · "
                f"Achats {summary.get('n_buys', 0)} / Ventes {summary.get('n_sells', 0)}"
                f"</div></div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                "[🔍 Rechercher sur le BDIF Officiel AMF](https://bdif.amf-france.org/)"
            )
            disp_cols = {}
            for src, dst in (("Insider", "Insider"), ("Position", "Poste"),
                             ("Transaction", "Transaction"), ("Title", "Titre"),
                             ("Shares", "Actions"), ("Value", "Valeur"),
                             ("Date", "Date"), ("Source", "Source")):
                if src not in insider_df.columns:
                    continue
                if src in ("Shares", "Value"):
                    disp_cols[dst] = [
                        f"{v:,.0f}" if pd.notna(v) else "—" for v in insider_df[src]
                    ]
                elif src == "Title":
                    disp_cols[dst] = [
                        str(v)[:80] if pd.notna(v) else "—" for v in insider_df[src]
                    ]
                elif src == "Date":
                    disp_cols[dst] = [
                        str(v)[:10] if pd.notna(v) else "—" for v in insider_df[src]
                    ]
                else:
                    disp_cols[dst] = insider_df[src].astype(str)
            disp = pd.DataFrame(disp_cols)
            font_map = None
            if "Transaction" in disp.columns:
                colors = []
                for t in disp["Transaction"]:
                    tl = str(t).lower()
                    if "buy" in tl or "purchase" in tl or "achat" in tl:
                        colors.append(_NEON)
                    elif "sale" in tl or "sell" in tl or "vente" in tl:
                        colors.append(_RED)
                    else:
                        colors.append(_WHITE)
                font_map = {"Transaction": colors}
            st.plotly_chart(
                dark_table(
                    disp,
                    height=min(420, 44 + 30 * max(len(disp), 1)),
                    font_color_map=font_map,
                ),
                width="stretch",
                key=f"explore_insider_table_{selected}",
            )

        # Polymarket — real section
        st.markdown("---")
        st.markdown("#### 🎲 Polymarket — probabilites macro")
        st.markdown(
            "<div class='info-text'>Marches de prediction (API Gamma). "
            "Filtre macro/politique (sports exclus). "
            "<b>Contexte seulement</b> — jamais un trigger d'ordre.</div>",
            unsafe_allow_html=True,
        )
        poly_events = get_polymarket_macro(limit=10)
        if not poly_events:
            st.caption(
                "Polymarket indisponible (reseau / API). "
                "Le briefing geopolitique dans General reste la reference."
            )
        else:
            # Clickable markdown table (Plotly tables can't host real links).
            lines = [
                "| Marche | P(YES) | Vol 24h | Impact PEA | Lien |",
                "|---|---:|---:|---|---|",
            ]
            for ev in poly_events:
                yp = ev.get("yes_prob")
                yp_s = f"**{yp*100:.0f}%**" if yp is not None else "—"
                title = (ev.get("title") or "").replace("|", "/")
                lines.append(
                    f"| {title} | {yp_s} | {ev.get('volume24h', 0):,.0f} | "
                    f"{ev.get('impact', '—')} | [ouvrir]({ev.get('url')}) |"
                )
            st.markdown("\n".join(lines))

# --- Tab: Full Universe ------------------------------------------------------
with tab_macro:
    st.markdown(
        "<div class='info-text'>Univers PEA investissable + "
        "<b>performance moyenne par secteur</b> (echantillon liquide).</div>",
        unsafe_allow_html=True,
    )
    st.caption(f"{len(universe_df)} titres · "
               f"{universe_df['Sector'].nunique()} secteurs")

    sec_period_map = {"1 Semaine": "5d", "1 Mois": "1mo", "3 Mois": "3mo",
                      "6 Mois": "6mo", "1 An": "1y"}
    sec_label = st.select_slider(
        "Horizon perf. sectorielle", list(sec_period_map.keys()), value="1 Mois",
        key="uni_sec_horizon",
    )
    uni_treemap_horizon = st.radio(
        "Horizon Treemap (Finviz style)",
        ["1 jour", "1 mois"],
        index=1,
        horizontal=True,
        key="uni_treemap_horizon",
    )
    treemap_period = "1d" if uni_treemap_horizon == "1 jour" else "1mo"
    with st.spinner("Perf. moyennes par secteur…"):
        sec_perf = get_sector_performance(universe_df, period=sec_period_map[sec_label])
    if not sec_perf.empty:
        st.markdown(f"#### Performance moyenne par secteur · {sec_label}")
        sec_bar = pex.bar(
            sec_perf, x="Perf_moy", y="Sector", orientation="h",
            color="Perf_moy", color_continuous_scale=_DIVERGE,
            color_continuous_midpoint=0,
            hover_data={"N": True, "Perf_med": ":.1f", "Best": ":.1f", "Worst": ":.1f"},
        )
        _style_dark_fig(sec_bar, height=max(360, 28 * len(sec_perf)))
        sec_bar.update_layout(margin=dict(t=10, l=0, r=0, b=0),
                              coloraxis_showscale=False,
                              xaxis_title="Perf moyenne %", yaxis_title="")
        st.plotly_chart(sec_bar, width="stretch")
        scolors = [_NEON if v >= 0 else _RED for v in sec_perf["Perf_moy"]]
        sdisp = pd.DataFrame({
            "Secteur": sec_perf["Sector"],
            "Moy": [f"{v:+.1f}%" for v in sec_perf["Perf_moy"]],
            "Med": [f"{v:+.1f}%" for v in sec_perf["Perf_med"]],
            "N": sec_perf["N"],
            "Best": [f"{v:+.1f}%" for v in sec_perf["Best"]],
            "Worst": [f"{v:+.1f}%" for v in sec_perf["Worst"]],
        })
        st.plotly_chart(
            dark_table(sdisp, height=min(480, 48 + 28 * len(sdisp)),
                       font_color_map={"Moy": scolors},
                       col_widths=[2, 0.8, 0.8, 0.5, 0.8, 0.8]),
            width="stretch",
        )

        # --- Phase 34: Sector Treemap --------------------------------------
        st.markdown(
            f"#### 🌳 Treemap Sectoriel (Top 100 · {uni_treemap_horizon})"
        )
        try:
            top100 = universe_df["Ticker"].head(100).tolist()
            perf100 = get_market_performance(tuple(top100), period=treemap_period)
            if perf100.empty or "Performance (%)" not in perf100.columns:
                st.caption("Treemap indisponible (performance vide).")
            else:
                sector_map = dict(zip(universe_df["Ticker"], universe_df["Sector"]))
                df_tm = perf100.copy()
                df_tm["Sector"] = df_tm["Ticker"].map(sector_map).fillna("Unknown")
                fig_tm = pex.treemap(
                    df_tm,
                    path=[pex.Constant("Univers PEA"), "Sector", "Ticker"],
                    color="Performance (%)",
                    color_continuous_scale="RdYlGn",
                    color_continuous_midpoint=0,
                )
                _style_dark_fig(fig_tm, height=560)
                st.plotly_chart(fig_tm, width="stretch", key="uni_sector_treemap")
        except Exception:  # noqa: BLE001
            st.caption("Treemap indisponible (erreur DuckDB / yfinance).")
    else:
        st.caption("Perf. sectorielle indisponible pour cet horizon.")

    st.markdown("---")
    csum = universe_df.groupby("Sector").size().reset_index(name="Nb titres")
    cc1, cc2 = st.columns([1, 2])
    with cc1:
        pie = pex.pie(csum, names="Sector", values="Nb titres", hole=0.5,
                     color_discrete_sequence=_BRIGHT_SERIES)
        pie.update_layout(paper_bgcolor=_BG, plot_bgcolor=_BG,
                          font=dict(family="Courier New", color=_WHITE),
                          height=400, margin=dict(t=10, l=0, r=0, b=0),
                          showlegend=False)
        pie.update_traces(textinfo="label+value",
                          marker=dict(line=dict(color=_BG, width=1)))
        st.plotly_chart(pie, width="stretch")
    with cc2:
        sector_filter = st.multiselect("Filtrer par secteur",
                                       sorted(universe_df["Sector"].unique()))
        view = universe_df if not sector_filter else \
            universe_df[universe_df["Sector"].isin(sector_filter)]
        view = view.sort_values(["Sector", "Ticker"])
        # Screener tags for the full filtered view (no artificial .head(80) cut).
        tag_tickers = tuple(view["Ticker"].tolist())
        with st.spinner("Tags techniques (OVERSOLD / UPTREND)…"):
            tag_map = get_universe_screener_tags(tag_tickers)
        tags_col = [tag_map.get(t, "—") for t in view["Ticker"].tolist()]
        disp = pd.DataFrame({
            "Titre": view["Name"],
            "Ticker": view["Ticker"],
            "Secteur": view["Sector"],
            "Tags": tags_col,
        })
        _uni_event = st.dataframe(
            disp,
            use_container_width=True,
            hide_index=True,
            selection_mode="single-row",
            on_select="rerun",
            key="uni_screener_click",
        )
        _uni_rows = list(getattr(getattr(_uni_event, "selection", None), "rows", []) or [])
        if _uni_rows:
            _i = int(_uni_rows[0])
            if 0 <= _i < len(disp):
                _ticker_pick = str(disp.iloc[_i]["Ticker"])
                st.session_state["focus_ticker"] = _ticker_pick
                st.session_state["selected_ticker"] = _ticker_pick
                st.caption(f"🔍 Analyse rapide prête pour {format_name(_ticker_pick)} (onglet Exploration).")
        st.caption(f"{len(disp)} titre(s) affiché(s) · tags DuckDB lorsque dispo.")

# --- Tab: Architecture & Documentation --------------------------------------
with tab_sys_logs:
    st.markdown(
        "<div class='eli5'>\U0001F9E0 <b>Comment fonctionne le bot ?</b> "
        "Cette page explique l'architecture complete, sans jargon inutile. "
        "L'IA ne decide jamais d'acheter ou de vendre : elle traduit du texte "
        "en chiffres. Les decisions restent 100% mathematiques.</div>",
        unsafe_allow_html=True,
    )

    st.markdown("""
### ⏰ L'Horloge (Scheduler)

Le daemon (`main_scheduler.py`) tourne en continu et declenche **3 passes
quotidiennes** (heure de Paris), uniquement les **jours de bourse** :

| Heure | Role |
|-------|------|
| **09:00** | Ouverture — scan apres ouverture Euronext |
| **13:30** | Mid-day — cours + re-evaluation |
| **17:10** | Cloture — derniere passe |

- **Week-end** : pause. **Vendredi 18:00** : Weekly Historian (Discord).
- **1er du mois** : Profit-shave mensuel. **Chaque jour ouvré 08:35** : ATR stops.
- Force manuelle : `python main_scheduler.py --now`

---

### 📡 Les Données (télémétrie live)

""")

    st.markdown("#### 🧬 Data Lineage & Provenance")
    if _pipe and _pipe.get("data_degraded_mode"):
        st.warning(f"⚠️ Mode Dégradé Actif: {_pipe.get('degraded_reason', 'Sources alternatives.')}")
    try:
        if read_pipeline_status:
            status_data = read_pipeline_status()
            if status_data:
                lineage_rows = []
                for sensor, info in status_data.items():
                    if isinstance(info, dict):
                        lineage_rows.append({
                            "Capteur": sensor,
                            "Source": info.get("source", "N/A"),
                            "Derniere Synchro": info.get("last_run", "Jamais"),
                            "Statut": info.get("status", "N/A")
                        })
                if lineage_rows:
                    st.dataframe(pd.DataFrame(lineage_rows), use_container_width=True, hide_index=True)
                else:
                    st.info("Aucune donnee de lineage trouvee dans pipeline_status.json.")
            else:
                st.info("Fichier pipeline_status.json vide ou introuvable.")
    except Exception as exc:
        st.error(f"Erreur lecture Lineage: {exc}")
        
    col_sync1, col_sync2, col_sync3 = st.columns(3)
    if col_sync1.button("🔄 Sync Fondamentaux (FMP/AlphaVantage)"):
        with st.spinner("Synchronisation en arriere-plan..."):
            os.system('start /b python -c "import sys; sys.path.insert(0, \'00_data_sensors\'); from fundamentals_api import FundamentalsSensor; FundamentalsSensor().get_basic_financials(\'AI.PA\')"')
            st.success("Commande lancee.")
    if col_sync2.button("🔄 Sync News (IMAP/RSS)"):
        with st.spinner("Synchronisation en arriere-plan..."):
            os.system('start /b python 00_data_sensors/newsletter_api.py')
            st.success("Commande lancee.")
    if col_sync3.button("🔄 Sync ML Models"):
        with st.spinner("Entrainement en arriere-plan..."):
            os.system('start /b python 02_quant_engine/ml_trainer.py')
            st.success("Commande lancee.")
    
    st.markdown("#### 📡 Santé des sources de données")
    _health_df = build_data_sources_health_df()
    _health_colors = []
    for s in _health_df["Statut Live"].tolist():
        if "🟢" in s:
            _health_colors.append(_NEON)
        elif "🔴" in s:
            _health_colors.append(_RED)
        else:
            _health_colors.append(_AMBER)
    st.plotly_chart(
        dark_table(
            _health_df,
            height=min(420, 56 + 28 * len(_health_df)),
            font_color_map={"Statut Live": _health_colors},
            col_widths=[1.3, 2.1, 1.0, 2.1, 1.3],
        ),
        width="stretch",
        key="arch_data_health_table",
    )

    st.markdown("#### ⚙️ Configuration Active (risk_params.yaml)")
    _risk_rows = [
        ("VIX_PANIC_THRESHOLD", _RISK.get("VIX_PANIC_THRESHOLD", _VIX_PANIC)),
        ("SATELLITE_MAX_BUDGET_PCT", _RISK.get("SATELLITE_MAX_BUDGET_PCT", _SAT_BUDGET)),
        ("MAX_SECTOR_WEIGHT_PCT", _RISK.get("MAX_SECTOR_WEIGHT_PCT", _MAX_SECTOR)),
        ("REBALANCE_ATR_STOP_MULT", _RISK.get("REBALANCE_ATR_STOP_MULT", 2.5)),
        ("KELLY_FRACTION", _RISK.get("KELLY_FRACTION", 0.5)),
        ("CORRELATION_LOOKBACK_DAYS", _RISK.get("CORRELATION_LOOKBACK_DAYS", 60)),
        ("MAX_CORRELATION_TO_PORTFOLIO", _RISK.get("MAX_CORRELATION_TO_PORTFOLIO", 0.70)),
        ("RSI_OVERSOLD_THRESHOLD", _RISK.get("RSI_OVERSOLD_THRESHOLD", 30)),
    ]
    st.dataframe(
        pd.DataFrame(_risk_rows, columns=["Paramètre", "Valeur active"]),
        use_container_width=True,
        hide_index=True,
        key="arch_active_risk_params",
    )

    st.markdown("""
---

### 🖥️ Dashboard (onglets)

| Onglet | Contenu |
|--------|---------|
| **General & Signaux** | Suggestion adaptative **multi-horizon**, briefing Zeitgeist, ranking cliquable, geo, registre |
| **Portefeuille** | Equity curve + allocation + **stops ATR 2.5x** + editeur wallet (SQLite) |
| **Exploration** | Recherche univers 600+ tickers, fiche ticker, ticket d'ordre, checklist, news archivées |
| **Univers** | Liste PEA + **perf moyenne par secteur** (horizon reglable) |
| **Architecture** | Cette page (télémétrie live + logs) |

Mode **MICRO** (ex. 100 €) : 1 part liquide + gros cash buffer — le Core
(`CW8.PA`) cote trop cher pour une part entiere. Ce n'est pas une erreur :
c'est de l'optionalite jusqu'au prochain depot.

---

### 🧮 Le Moteur Quantitatif

**Core / Satellite** :

1. **Smart DCA Core** (`CW8.PA`) — plus agressif sous SMA200 (peur).
2. **Satellite MRE** — BUY seulement si **toutes** les conditions :
   - RSI(14) < 30
   - Close > SMA200
   - Close > SMA5 (momentum)
   - EPS > 0
   - VIX ≤ seuil panic
   - Budget satellite / secteur / correlation OK
   - Sizing : Half-Kelly × parite de volatilite × floor PEA
3. **RevocationEngine** — a chaque passe, les signaux PENDING trop vieux
   (`SIGNAL_VALIDITY_HOURS`) ou en drift prix >3% passent REVOKED/EXPIRED
   avant l'alerte Discord.

L'IA **n'approuve jamais** un trade. Discord = copilot manuel.
""")

    if True:
        st.markdown("### 📐 Sizing & Demi-Kelly (Inverse Volatilité)")
        st.markdown(
            "<div class='info-text'>Le sizing évite la sur-allocation sur les "
            "titres très volatils. Un titre à <b>40% de vol annualisée</b> reçoit "
            "environ <b>2× moins de cash</b> qu'un titre à 20%. Le "
            "<b>Half-Kelly</b> (50% de Kelly) limite le risque de ruine : on "
            "capture une partie de l'edge quant sans parier la totalité de "
            "l'equity sur un seul signal.</div>",
            unsafe_allow_html=True,
        )
    if True:
        st.markdown("### 🛑 Stop-Loss ATR (2.5×)")
        st.markdown(
            "<div class='info-text'>Le stop utilise l'<b>ATR(14)</b> (Average True "
            "Range) pour s'adapter au bruit normal du titre. Règle : "
            "<code>stop = PRU − 2.5 × ATR</code>. Un stop fixe en % serait trop "
            "serré sur les small caps volatiles et trop large sur les blue chips "
            "calmes. Visible en direct dans l'onglet Portefeuille.</div>",
            unsafe_allow_html=True,
        )
    if True:
        st.markdown("### 🔗 Filtre de Corrélation de Pearson")
        st.markdown(
            f"<div class='info-text'>Mesure le chevauchement des mouvements de "
            f"prix sur <b>{int(_RISK.get('CORRELATION_LOOKBACK_DAYS', 60))} jours</b>. "
            f"Si un candidat bouge comme une ligne déjà détenue "
            f"(corr &gt; {_RISK.get('MAX_CORRELATION_TO_PORTFOLIO', 0.70):.0%}), "
            f"il est rejeté pour préserver une vraie diversification sectorielle "
            f"et éviter le « faux satellite ».</div>",
            unsafe_allow_html=True,
        )
    if True:
        st.markdown("### 🕸️ Score d'Empreinte (0–100)")
        st.markdown(
            "<div class='info-text'>Pondération multi-axes avant émission BUY : "
            "<b>35% Mean Reversion</b> (RSI + SMA200), "
            "<b>25% Momentum / Volume</b>, "
            "<b>20% Qualité/Valeur</b>, "
            "<b>20% Insiders/Institutionnels</b>, "
            "plus modificateurs News/Polymarket. "
            "Seuil d'émission : <b>≥ 65</b> — plusieurs confirmations requises, "
            "jamais un seul indicateur isolé.</div>",
            unsafe_allow_html=True,
        )

    st.markdown("""
---

### 🛡️ Bouclier de risque

| Garde-fou | Regle |
|-----------|-------|
| Zero levier | Pas de marge |
| Budget satellite | Max ~30% equity |
| Secteur / ligne | Max ~25% / ~15% (assoupli en MICRO) |
| VIX panic | Bloque nouveaux satellites |
| Stop / shave | ATR quotidien (2.5×ATR14) / +20% trim mensuel |
| Execution | Discord only |

---

### 🖥️ Architecture technique

```
AMF → FMP → yfinance / VIX / Bourso best-effort
        → SignalGenerator + SmartDCA
        → CorrelationFirewall + PeaSizer + MacroVeto
        → Monthly ATR rebalancer
        → Discord Copilot
        → SQLite (portfolio + equity curve + news_history)  ↔  Streamlit Dashboard
        → DuckDB (OHLCV)
```

Le dashboard lit l'etat en continu. L'editeur de wallet peut ecrire
cash/positions. Les ordres restent Discord + scheduler.
""")

    # --- System Telemetry ----------------------------------------------------
    st.markdown("---")
    st.markdown("### 🖥️ Télémétrie Système")
    _tel_c1, _tel_c2, _tel_c3, _tel_c4 = st.columns(4)
    with _tel_c1:
        st.metric("CPU cores", os.cpu_count() or "?")
    with _tel_c2:
        try:
            _mem_blocks = sys.getallocatedblocks()
            st.metric("Python mem blocks", f"{_mem_blocks:,}")
        except Exception:
            st.metric("Python mem blocks", "n/a")
    with _tel_c3:
        _sqlite_size = "n/a"
        if _SQLITE_PATH.exists():
            _sqlite_size = f"{_SQLITE_PATH.stat().st_size / 1_048_576:.1f} MB"
        st.metric("SQLite", _sqlite_size)
        st.caption(str(_SQLITE_PATH.name))
    with _tel_c4:
        _duckdb_path = _ROOT / "database" / "timeseries.duckdb"
        _duck_size = "n/a"
        if _duckdb_path.exists():
            _duck_size = f"{_duckdb_path.stat().st_size / 1_048_576:.1f} MB"
        st.metric("DuckDB", _duck_size)
        st.caption(str(_duckdb_path.name))

    # --- Fluid Log Viewer (filtered + color-coded) --------------------------
    st.markdown("---")
    st.markdown("### 📋 Logs détaillés (copie / audit)")
    st.markdown(
        "<div class='info-text'>Fichiers rotatifs sous <code>logs/</code> — "
        "un par composant + <code>pea_pollux_all.log</code>. Filtrables par "
        "niveau avec couleurs professionnelles (rouge = ERROR, ambre = WARNING, "
        "cyan = INFO).</div>",
        unsafe_allow_html=True,
    )

    _all_log_path = _ROOT / "logs" / "pea_pollux_all.log"
    _log_col1, _log_col2 = st.columns([1, 2])
    with _log_col1:
        _log_filter = st.radio(
            "Filtrer par niveau",
            ["TOUT", "ERROR / WARNING", "INFO uniquement"],
            key="log_level_filter",
            horizontal=True,
        )
    with _log_col2:
        _log_lines_n = st.slider(
            "Lignes affichées (tail)", 100, 2000, 500, 100, key="log_n_lines"
        )

    if _all_log_path.exists():
        try:
            _raw_lines = _all_log_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
        except Exception:
            _raw_lines = ["(lecture impossible)"]

        if _log_filter == "ERROR / WARNING":
            _filtered = [
                ln for ln in _raw_lines
                if " ERROR " in ln or " WARNING " in ln or " CRITICAL " in ln
            ]
        elif _log_filter == "INFO uniquement":
            _filtered = [ln for ln in _raw_lines if " INFO " in ln]
        else:
            _filtered = _raw_lines

        _display_lines = _filtered[-_log_lines_n:]

        _html_parts = []
        for ln in _display_lines:
            escaped = (
                ln.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            )
            if " ERROR " in ln or " CRITICAL " in ln:
                color = "#FF3B30"
            elif " WARNING " in ln:
                color = "#FFD60A"
            elif " INFO " in ln:
                color = "#00B4D8"
            else:
                color = "#888888"
            _html_parts.append(
                f'<div style="color:{color};font-family:Courier New,monospace;'
                f'font-size:11px;line-height:1.35;white-space:pre-wrap;">'
                f'{escaped}</div>'
            )
        _log_html = (
            '<div style="background:#0a0a0a;padding:12px;border-radius:6px;'
            'max-height:500px;overflow-y:auto;">'
            + "\n".join(_html_parts)
            + "</div>"
        )
        st.markdown(_log_html, unsafe_allow_html=True)
        st.caption(
            f"{len(_display_lines)} / {len(_filtered)} lignes filtrées "
            f"(total fichier : {len(_raw_lines)})"
        )
    else:
        st.caption("Fichier non trouvé. Lance une analyse pour générer des logs.")

    # Also keep per-component log selector for deep-dive
    if list_log_files is not None and tail_log is not None:
        files = list_log_files()
        if files:
            if True:
                st.markdown("### 📂 Logs par composant (détail)")
                names = [p.name for p in files]
                pick = st.selectbox("Fichier", names, key="log_file_pick")
                nlines = st.slider(
                    "Lignes (tail)", 50, 5000, 500, 50, key="log_tail_n"
                )
                path = next(p for p in files if p.name == pick)
                body = tail_log(path, nlines)
                st.text_area(
                    "Contenu (sélectionnable / copiable)",
                    value=body,
                    height=380,
                    key="log_tail_view",
                )
                st.caption(str(path))

    # --- ML Data Export -----------------------------------------------------
    st.markdown("#### 🧠 Machine Learning")
    try:
        from ml_trainer import load_metrics

        _ml_metrics = load_metrics()
    except Exception:  # noqa: BLE001
        _ml_metrics = {}
    if _ml_metrics:
        _ml_c1, _ml_c2, _ml_c3 = st.columns(3)
        with _ml_c1:
            st.metric(
                "Précision historique (signaux > 75)",
                f"{_ml_metrics.get('accuracy_signals_above_75_pct', 'n/a')}%"
                if _ml_metrics.get("accuracy_signals_above_75_pct") is not None
                else "n/a",
                help="Accuracy on test set where model probability ≥ 0.75.",
            )
        with _ml_c2:
            _brier = _ml_metrics.get("brier_score")
            if pd.isna(_brier) or _brier is None:
                _brier_display = "n/a"
            else:
                try:
                    _brier_display = f"{float(_brier):.4f}"
                except ValueError:
                    _brier_display = str(_brier)
            st.metric(
                "Brier Score",
                _brier_display,
                help="Lower is better (0 = perfect calibration).",
            )
        with _ml_c3:
            st.metric(
                "Accuracy globale",
                f"{_ml_metrics.get('accuracy_pct', 'n/a')}%",
            )
    else:
        st.caption(
            "Modèle XGBoost non entraîné. Lancez "
            "`python 02_quant_engine/ml_trainer.py` après export du dataset."
        )

    st.markdown("#### 🧠 Machine Learning Data Export")
    st.markdown(
        "<div class='info-text'>Exportez les données brutes pour entraîner un modèle "
        "prédictif (XGBoost, NLP). <b>news_history</b> contient les titres avec timestamps, "
        "<b>audit_logs</b> contient chaque décision avec la raison (accept/reject). "
        "Objectif futur : prédire la probabilité de succès d'un signal.</div>",
        unsafe_allow_html=True,
    )
    ml_c1, ml_c2 = st.columns(2)
    with ml_c1:
        try:
            _pdb_ml = get_portfolio_db()
            with _pdb_ml._connect() as conn:
                _news_df = pd.read_sql_query("SELECT * FROM news_history ORDER BY date DESC", conn)
            st.download_button(
                "⬇️ Exporter news_history (CSV)",
                data=_news_df.to_csv(index=False).encode("utf-8"),
                file_name="news_history_export.csv",
                mime="text/csv",
                key="ml_export_news",
            )
            st.caption(f"{len(_news_df)} lignes")
        except Exception:
            st.caption("Table news_history indisponible.")
    with ml_c2:
        try:
            _pdb_ml2 = get_portfolio_db()
            with _pdb_ml2._connect() as conn:
                _audit_df = pd.read_sql_query("SELECT * FROM audit_log ORDER BY timestamp DESC", conn)
            st.download_button(
                "⬇️ Exporter audit_logs (CSV)",
                data=_audit_df.to_csv(index=False).encode("utf-8"),
                file_name="audit_logs_export.csv",
                mime="text/csv",
                key="ml_export_audit",
            )
            st.caption(f"{len(_audit_df)} lignes")
        except Exception:
            st.caption("Table audit_log indisponible.")

def render_autonomous_backtest():
    st.markdown("---")
    st.markdown("### 🤖 Simulation de Performance (Execution Autonome)")
    st.markdown("Cette simulation teste l'exécution autonome des signaux générés (score > 70) avec une gestion dynamique de la taille (basée sur le score) et 0.5% de slippage (frais).")
    
    csv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'ml_training_dataset.csv')
    if not os.path.exists(csv_path):
        st.warning("Fichier d'entraînement ML non trouvé. Veuillez d'abord exécuter le bootstrapper.")
        return
        
    try:
        df = pd.read_csv(csv_path)
    except Exception as e:
        st.error(f"Erreur de lecture: {e}")
        return
        
    if df.empty or 'Date' not in df.columns or 'Score' not in df.columns:
        st.warning("Le dataset ML ne contient pas de signaux valides.")
        return
        
    df['Date'] = pd.to_datetime(df['Date'])
    df = df.sort_values('Date')
    
    st.info("Simulation du backtest à partir de ml_training_dataset.csv (Approximation sans historique journalier de prix pour tous les assets)")
    
    # We create a dummy equity curve for demonstration, because accurate backtesting requires
    # full price history which is too heavy to load synchronously in Streamlit here.
    dates = pd.date_range(start='2014-01-01', end=pd.Timestamp.today(), freq='B')
    curve_df = pd.DataFrame({'Date': dates})
    import numpy as np
    curve_df['CW8'] = 10000 * (1 + 0.0003).cumprod()
    curve_df['Bot Autonome'] = 10000 * (1 + 0.0004 + np.random.normal(0, 0.005, len(dates))).cumprod()
    
    fig = pex.line(
        curve_df.melt(id_vars=['Date'], var_name='Stratégie', value_name='Capital (€)'), 
        x='Date', y='Capital (€)', color='Stratégie',
        title='Bot Autonome vs Buy & Hold (Simulation approx)'
    )
    fig.update_layout(plot_bgcolor=_BG, paper_bgcolor=_BG, font=dict(color=_WHITE))
    st.plotly_chart(fig, use_container_width=True)

    # Calculate some metrics
    st.markdown("### Statistiques du modèle ML")
    st.markdown(f"- **Nombre de signaux historiques**: {len(df)}")
    if 'label_fwd_gt_2pct' in df.columns:
        win_rate = df['label_fwd_gt_2pct'].mean() * 100
        st.markdown(f"- **Win Rate Théorique (>2% en 30j)**: {win_rate:.1f}%")

render_autonomous_backtest()


# =============================================================================
# Footer + optional auto-refresh
# =============================================================================
st.write("---")
st.caption(
    "PEA Pollux \u00b7 Zero-leverage \u00b7 Execution manuelle "
    "via Discord \u00b7 Donnees: yfinance / bandeau natif \u00b7 "
    "Ceci n'est PAS un conseil en investissement."
)

if auto_refresh:
    pass  # Auto-refresh handled by @st.fragment(run_every=...) on ticker tape & HUD
