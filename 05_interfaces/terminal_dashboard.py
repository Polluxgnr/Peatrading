"""Web Terminal (Streamlit dashboard) for PEA Sniper Terminal V-Prime.

A read-only command center replicating the "Aegis Prime" dark UI, adapted for
the PEA SQLite/DuckDB architecture. It ONLY reads state; it never mutates any
database.

Run with:
    streamlit run 05_interfaces/terminal_dashboard.py
"""

import os
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go  # noqa: F401 - available for custom charts.
import streamlit as st
import streamlit.components.v1 as components

# --- Cross-package imports (dirs start with digits) --------------------------
_ROOT = Path(__file__).resolve().parent.parent
_CORE_DIR = _ROOT / "01_memory_core"
sys.path.insert(0, str(_CORE_DIR))

from sqlite_portfolio import PortfolioDB  # noqa: E402
from duckdb_manager import TimeSeriesDB  # noqa: E402

_DB_DIR = _ROOT / "database"
_SQLITE_PATH = _DB_DIR / "portfolio.db"
_DUCKDB_PATH = _DB_DIR / "timeseries.duckdb"
_UNIVERSE_PATH = _ROOT / "config" / "pea_universe.yaml"

# Aegis Prime palette.
_BG = "#0E1117"
_GREEN = "#00E676"
_RED = "#FF3B30"


# =============================================================================
# STEP 8.1: Page config & CSS
# =============================================================================
st.set_page_config(
    page_title="PEA Sniper Terminal",
    layout="wide",
    initial_sidebar_state="collapsed",
)

_CSS = f"""
<style>
    .stApp {{ background-color: {_BG}; }}
    .metric-box {{
        background-color: #161A25;
        border-radius: 10px;
        padding: 18px 20px;
        border-left: 5px solid {_GREEN};
        box-shadow: 0 2px 8px rgba(0,0,0,0.4);
    }}
    .metric-box.negative {{ border-left: 5px solid {_RED}; }}
    .metric-box.neutral  {{ border-left: 5px solid #3D4657; }}
    .metric-title {{
        color: #8B93A7; font-size: 0.80rem; text-transform: uppercase;
        letter-spacing: 0.08em; margin-bottom: 6px;
    }}
    .metric-value {{ color: #FFFFFF; font-size: 1.9rem; font-weight: 700; }}
    .metric-value.green {{ color: {_GREEN}; }}
    .metric-value.red   {{ color: {_RED}; }}
    .metric-sub {{ color: #8B93A7; font-size: 0.85rem; margin-top: 4px; }}
    h1, h2, h3, h4 {{ color: #E6E9F0 !important; }}
</style>
"""
st.markdown(_CSS, unsafe_allow_html=True)


def _metric_box(title: str, value: str, sub: str = "", accent: str = "green") -> str:
    """Return HTML for a styled metric box.

    Args:
        title: Small uppercase label.
        value: Main value string.
        sub: Optional sub-caption.
        accent: One of ``"green"``, ``"red"`` or ``"neutral"``.

    Returns:
        str: The HTML snippet.
    """
    box_cls = "metric-box"
    if accent == "red":
        box_cls += " negative"
    elif accent == "neutral":
        box_cls += " neutral"
    value_cls = "metric-value"
    if accent in ("green", "red"):
        value_cls += f" {accent}"
    return (
        f'<div class="{box_cls}">'
        f'<div class="metric-title">{title}</div>'
        f'<div class="{value_cls}">{value}</div>'
        f'<div class="metric-sub">{sub}</div>'
        f"</div>"
    )


# =============================================================================
# STEP 8.2: Cached data loaders (read-only)
# =============================================================================
@st.cache_data(ttl=60)
def load_portfolio_state():
    """Load the current portfolio snapshot (cached 60s).

    Returns:
        PortfolioState | None: The portfolio, or ``None`` if the DB is missing.
    """
    if not _SQLITE_PATH.exists():
        return None
    db = PortfolioDB(db_path=_SQLITE_PATH)
    return db.get_portfolio_state()


@st.cache_data(ttl=60)
def load_active_signals() -> pd.DataFrame:
    """Load PENDING signals from the audit log (cached 60s).

    Returns:
        pd.DataFrame: Pending signals (possibly empty).
    """
    if not _SQLITE_PATH.exists():
        return pd.DataFrame()
    db = PortfolioDB(db_path=_SQLITE_PATH)
    return pd.DataFrame(db.fetch_signals_by_status(["PENDING"]))


@st.cache_data(ttl=60)
def load_execution_history(limit: int = 20) -> pd.DataFrame:
    """Load recent EXECUTED/REVOKED signals from the audit log (cached 60s).

    Args:
        limit: Maximum number of rows to return.

    Returns:
        pd.DataFrame: Execution/revocation ledger (possibly empty).
    """
    if not _SQLITE_PATH.exists():
        return pd.DataFrame()
    db = PortfolioDB(db_path=_SQLITE_PATH)
    return pd.DataFrame(db.fetch_signals_by_status(["EXECUTED", "REVOKED"], limit=limit))


@st.cache_data(ttl=300)
def load_universe_tickers() -> list[str]:
    """Load tickers from the universe YAML (cached 5 min).

    Returns:
        list[str]: Sorted ticker symbols, or a small default list on failure.
    """
    try:
        import yaml

        with open(_UNIVERSE_PATH, "r", encoding="utf-8") as fh:
            universe = yaml.safe_load(fh) or {}
        tickers = [
            entry["ticker"]
            for members in universe.get("universe", {}).values()
            for entry in members
        ]
        return sorted(set(tickers))
    except Exception:  # noqa: BLE001
        return ["MC.PA", "OR.PA", "AI.PA", "ASML.AS", "SAP.DE"]


def _tradingview_symbol(ticker: str) -> str:
    """Map a Yahoo ticker to a TradingView exchange:symbol string."""
    exchange_map = {
        ".PA": "EURONEXT", ".AS": "EURONEXT", ".BR": "EURONEXT",
        ".LS": "EURONEXT", ".DE": "XETR", ".MC": "BME", ".MI": "MIL",
    }
    for suffix, exch in exchange_map.items():
        if ticker.endswith(suffix):
            return f"{exch}:{ticker[: -len(suffix)]}"
    return ticker


# =============================================================================
# Header
# =============================================================================
st.markdown("# PEA SNIPER TERMINAL  ·  V-PRIME")
st.caption("Zero-leverage · EU equities · Manual copilot execution")

portfolio = load_portfolio_state()
if portfolio is None:
    st.warning(
        "Awaiting database initialization... Run the scheduler or initialize "
        "the databases to populate the terminal."
    )
    st.stop()


# =============================================================================
# STEP 8.3: Top HUD
# =============================================================================
positions = portfolio.positions
invested = sum(p.market_value for p in positions)
cash_pct = (portfolio.cash_available / portfolio.total_equity * 100
            if portfolio.total_equity else 0.0)

c1, c2, c3 = st.columns(3)
with c1:
    st.markdown(
        _metric_box(
            "Total Equity",
            f"{portfolio.total_equity:,.2f} \u20ac",
            f"Invested: {invested:,.2f} \u20ac",
            accent="green",
        ),
        unsafe_allow_html=True,
    )
with c2:
    st.markdown(
        _metric_box(
            "Cash Available",
            f"{portfolio.cash_available:,.2f} \u20ac",
            f"{cash_pct:.1f}% of equity",
            accent="neutral",
        ),
        unsafe_allow_html=True,
    )
with c3:
    st.markdown(
        _metric_box(
            "Active Positions",
            f"{len(positions)}",
            f"Last update: {portfolio.last_updated:%Y-%m-%d %H:%M}",
            accent="green" if positions else "neutral",
        ),
        unsafe_allow_html=True,
    )

st.markdown("<br>", unsafe_allow_html=True)


# =============================================================================
# STEP 8.4: Tabs
# =============================================================================
tab_alloc, tab_signals, tab_radar = st.tabs(
    ["\U0001F3AF Portfolio Allocation", "\u26A1 Live Signals & History", "\U0001F4CA Charting Radar"]
)

# --- Tab 1: Portfolio Allocation --------------------------------------------
with tab_alloc:
    if not positions:
        st.info("No open positions yet. Allocation appears once trades execute.")
    else:
        rows = [
            {
                "Sector": p.sector,
                "Ticker": p.ticker,
                "Qty": p.qty_shares,
                "Entry Price": p.avg_entry_price,
                "Current Price": p.current_price,
                "Market Value": p.market_value,
                "Unrealized PnL %": p.unrealized_pnl_pct * 100,
            }
            for p in positions
        ]
        df = pd.DataFrame(rows)

        alloc = df[["Sector", "Ticker", "Market Value"]].copy()
        if portfolio.cash_available > 0:
            alloc = pd.concat(
                [
                    alloc,
                    pd.DataFrame(
                        [{"Sector": "Cash", "Ticker": "CASH",
                          "Market Value": portfolio.cash_available}]
                    ),
                ],
                ignore_index=True,
            )

        fig = px.sunburst(
            alloc,
            path=["Sector", "Ticker"],
            values="Market Value",
            color="Sector",
            color_discrete_sequence=px.colors.qualitative.Set2,
        )
        fig.update_layout(
            paper_bgcolor=_BG,
            plot_bgcolor=_BG,
            font_color="#E6E9F0",
            margin=dict(t=20, l=0, r=0, b=0),
            height=460,
        )
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("Holdings")
        styled = (
            df.style.format(
                {
                    "Entry Price": "{:.2f} \u20ac",
                    "Current Price": "{:.2f} \u20ac",
                    "Market Value": "{:,.2f} \u20ac",
                    "Unrealized PnL %": "{:+.2f}%",
                }
            ).map(
                lambda v: f"color: {_GREEN}" if v >= 0 else f"color: {_RED}",
                subset=["Unrealized PnL %"],
            )
        )
        st.dataframe(styled, use_container_width=True, hide_index=True)

# --- Tab 2: Live Signals & History ------------------------------------------
with tab_signals:
    st.subheader("\u26A1 Pending Signals")
    pending = load_active_signals()
    if pending.empty:
        st.info("No pending signals right now.")
    else:
        view = pending[["ticker", "signal_type", "score", "reason", "created_at"]].copy()
        view.columns = ["Ticker", "Type", "Score", "Reason", "Created At"]
        styled_pending = view.style.format({"Score": "{:.1f}"}).background_gradient(
            subset=["Score"], cmap="Greens", vmin=0, vmax=100
        )
        st.dataframe(styled_pending, use_container_width=True, hide_index=True)

    st.subheader("\U0001F4D2 Execution Ledger (last 20)")
    history = load_execution_history(limit=20)
    if history.empty:
        st.info("No executed or revoked signals yet.")
    else:
        hview = history[["ticker", "signal_type", "status", "score", "created_at"]].copy()
        hview.columns = ["Ticker", "Type", "Status", "Score", "Created At"]
        st.dataframe(hview, use_container_width=True, hide_index=True)

# --- Tab 3: Charting Radar (TradingView) ------------------------------------
with tab_radar:
    held = [p.ticker for p in positions]
    options = sorted(set(held) | set(load_universe_tickers()))
    default_idx = options.index(held[0]) if held and held[0] in options else 0
    selected = st.selectbox("Select ticker", options, index=default_idx)

    tv_symbol = _tradingview_symbol(selected)
    tv_html = f"""
    <div class="tradingview-widget-container" style="height:600px;width:100%">
      <div id="tv_chart" style="height:600px;width:100%"></div>
      <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
      <script type="text/javascript">
        new TradingView.widget({{
          "autosize": true,
          "symbol": "{tv_symbol}",
          "interval": "D",
          "timezone": "Europe/Paris",
          "theme": "dark",
          "style": "1",
          "locale": "fr",
          "toolbar_bg": "{_BG}",
          "enable_publishing": false,
          "hide_top_toolbar": false,
          "hide_legend": false,
          "container_id": "tv_chart"
        }});
      </script>
    </div>
    """
    st.caption(f"TradingView symbol: `{tv_symbol}` (mapped from `{selected}`)")
    components.html(tv_html, height=620)
