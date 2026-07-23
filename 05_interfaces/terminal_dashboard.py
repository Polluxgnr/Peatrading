"""Web Terminal (Streamlit dashboard) for PEA Sniper Terminal V-Prime.

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
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as pex
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
from data_models import Position, PortfolioState  # noqa: E402

try:  # Optional sensors — the dashboard still works if a network dep is missing.
    from macro_alpha_api import MacroAlphaSensor  # noqa: E402
except Exception:  # noqa: BLE001
    MacroAlphaSensor = None  # type: ignore[assignment]

try:
    from news_sentiment_llm import NewsSentimentScorer  # noqa: E402
except Exception:  # noqa: BLE001
    NewsSentimentScorer = None  # type: ignore[assignment]

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

# --- Bloomberg palette (pure black + stark neon accents) ---------------------
_BG = "#050505"
_PANEL = "#000000"
_WHITE = "#FFFFFF"
_NEON = "#00FF00"       # neon green (bullish / positive)
_AMBER = "#FFB000"      # amber (warning / median)
_CYAN = "#00FFFF"       # cyan (labels / links / info)
_RED = "#FF3B30"        # red (bearish / negative / breach)
_MUTED = "#9BA3AF"      # readable light gray (never gray-on-gray)
_GRID = "#1A1A1A"       # chart gridlines
_HEADER_FILL = "#0A0A0A"
_BRIGHT_SERIES = ["#00FF00", "#00FFFF", "#FFB000", "#FF3B30", "#FF00FF",
                  "#1E90FF", "#FFFFFF", "#ADFF2F", "#FF7F50", "#7FFFD4"]
# Diverging scale with a DARK neutral (avoids glaring pale-yellow on black).
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


# =============================================================================
# Page config & Bloomberg CSS
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
    section[data-testid="stSidebar"] {{ background-color: {_PANEL};
        border-right: 1px solid #222; }}
    h1, h2, h3, h4 {{ color: {_WHITE} !important;
        font-family: 'Courier New', monospace; letter-spacing: 1px; }}

    /* --- Custom metric boxes (HUD) --- */
    .metric-box {{ background-color: {_PANEL}; padding: 15px 18px;
        border: 1px solid #333333; border-left: 4px solid {_NEON};
        margin-bottom: 10px; font-family: 'Courier New', monospace; }}
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
    .stTabs [aria-selected="true"] {{ color: {_NEON} !important; }}
</style>
""",
    unsafe_allow_html=True,
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
            align="left", line_color=_GRID, height=30,
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
    return PortfolioDB(db_path=_SQLITE_PATH).get_portfolio_state()


@st.cache_data(ttl=60)
def load_equity_curve() -> pd.DataFrame:
    """Load the daily equity curve from SQLite (cached 60s)."""
    if not _SQLITE_PATH.exists():
        return pd.DataFrame(columns=["date", "equity", "cash"])
    return PortfolioDB(db_path=_SQLITE_PATH).get_equity_curve()


@st.cache_data(ttl=60)
def load_signals(statuses: tuple[str, ...], limit: int | None = None) -> pd.DataFrame:
    """Load audit-log rows for the given statuses (cached 60s)."""
    if not _SQLITE_PATH.exists():
        return pd.DataFrame()
    db = PortfolioDB(db_path=_SQLITE_PATH)
    return pd.DataFrame(db.fetch_signals_by_status(list(statuses), limit=limit))


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
    """Compute performance over a preset period or an explicit date range."""
    if not tickers:
        return pd.DataFrame()
    try:
        # Cap batch size — huge universes make yfinance return sparse junk.
        batch = list(tickers)[:120]
        if start:
            raw = yf.download(batch, start=start, end=end, progress=False,
                              auto_adjust=True, threads=True)
        else:
            raw = yf.download(batch, period=period, progress=False,
                              auto_adjust=True, threads=True)
        close = _extract_close_frame(raw, batch)
        if close.empty:
            return pd.DataFrame()

        rows = []
        for t in close.columns:
            series = _valid_price_series(close[t])
            if series is None:
                continue
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
        return (pd.DataFrame(rows)
                .sort_values("Performance (%)", ascending=False)
                .reset_index(drop=True))
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def get_normalized_prices(
    tickers: tuple[str, ...], period: str | None, start: str | None, end: str | None
) -> pd.DataFrame:
    """Return prices rebased to 100 at the interval start (for line charts)."""
    if not tickers:
        return pd.DataFrame()
    try:
        batch = list(tickers)[:40]
        if start:
            raw = yf.download(batch, start=start, end=end, progress=False,
                              auto_adjust=True, threads=True)
        else:
            raw = yf.download(batch, period=period, progress=False,
                              auto_adjust=True, threads=True)
        close = _extract_close_frame(raw, batch)
        if close.empty:
            return pd.DataFrame()
        out = pd.DataFrame(index=close.index)
        for t in close.columns:
            series = _valid_price_series(close[t], min_points=2)
            if series is None:
                continue
            base = float(series.iloc[0])
            out[str(t)] = (series / base) * 100.0
        return out.dropna(how="all")
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


@st.cache_data(ttl=1800, show_spinner=False)
def get_recent_news(symbol: str, limit: int = 6) -> list[dict]:
    """Fetch recent news: Boursorama first (rich), then yfinance fallback."""
    # --- Primary: Boursorama scraper ----------------------------------------
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
            out = []
            for n in items[:limit]:
                out.append({
                    "title": n.get("title", ""),
                    "link": n.get("link") or "#",
                    "date": n.get("date") or "Recent",
                    "provider": (
                        f"Boursorama · {n.get('provider') or 'local'} · "
                        f"sentiment {sentiment} · elig {elig}"
                    ),
                })
            return out
        # Legacy title-only fallback from get_retail_sentiment_and_news
        bourso = BoursoramaScraper().get_retail_sentiment_and_news(symbol)
        headlines = (bourso or {}).get("news") or []
        if headlines:
            sentiment = (bourso or {}).get("sentiment") or "Unknown"
            return [
                {
                    "title": title,
                    "link": "#",
                    "date": "Recent",
                    "provider": f"Boursorama · sentiment {sentiment}",
                }
                for title in headlines[:limit]
            ]
    except Exception:  # noqa: BLE001
        pass

    # --- Fallback: yfinance -------------------------------------------------
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
                              "provider": provider or "Yahoo Finance"})
        return items
    except Exception:  # noqa: BLE001
        return []


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
        PortfolioDB(db_path=_SQLITE_PATH).update_portfolio(state)
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
    """Batch last close prices — per-ticker history to avoid column mixups."""
    out: dict[str, float] = {}
    if not tickers:
        return out
    # Prefer one-shot batch, then validate each ticker individually on miss.
    try:
        raw = yf.download(list(tickers), period="10d", progress=False,
                          auto_adjust=True, threads=True)
        close = _extract_close_frame(raw, tickers)
        for t in close.columns:
            series = pd.to_numeric(close[t], errors="coerce").dropna()
            if len(series):
                px = float(series.iloc[-1])
                if px > 0.05:  # reject absurd penny mis-parses
                    out[str(t)] = px
    except Exception:  # noqa: BLE001
        pass
    missing = [t for t in tickers if t not in out]
    for t in missing:
        try:
            h = yf.Ticker(t).history(period="10d", auto_adjust=True)
            if h is not None and not h.empty and "Close" in h.columns:
                px = float(h["Close"].dropna().iloc[-1])
                if px > 0.05:
                    out[t] = px
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
    """Score an affordable PEA name for MICRO/STARTER suggestions (0-100)."""
    prices = get_last_prices((ticker,))
    px = prices.get(ticker)
    if not px or px <= 0 or px > budget * 0.98:
        return {
            "ticker": ticker, "price": px or 0.0, "score": 0,
            "reco": "INACCESSIBLE", "why": "Prix hors budget ou indisponible.",
            "kind": "?", "rsi": None, "vs_sma200": None,
        }
    ind = get_indicators(ticker) or {}
    dossier = get_ticker_dossier(ticker)
    is_etf = bool(dossier.get("is_etf") or ticker in (
        _CORE_TICKER, "EWLD.PA", "PAEEM.PA", "ESE.PA", "C50.PA", "PE500.PA",
    ))
    score = 40.0
    reasons: list[str] = []
    rsi = ind.get("rsi")
    close = ind.get("close") or px
    sma5, sma200 = ind.get("sma5"), ind.get("sma200")
    vol = ind.get("vol_ann")

    if is_etf:
        score += 18
        reasons.append("ETF = diversification (mieux qu'1 action seule en MICRO)")
    else:
        score += 4
        reasons.append("Action individuelle — risque titre concentre")

    if rsi is not None:
        if rsi < 30:
            score += 22
            reasons.append(f"RSI {rsi:.0f} survendu (setup MRE)")
        elif rsi < 45:
            score += 12
            reasons.append(f"RSI {rsi:.0f} plutot calme")
        elif rsi > 70:
            score -= 18
            reasons.append(f"RSI {rsi:.0f} surachete — eviter d'acheter")
        else:
            score += 4
            reasons.append(f"RSI {rsi:.0f} neutre")

    vs200 = None
    if sma200 and close:
        vs200 = (close / sma200 - 1) * 100
        if close > sma200:
            score += 14
            reasons.append(f"Au-dessus SMA200 ({vs200:+.1f}%)")
        else:
            score -= 8 if not is_etf else 2
            reasons.append(f"Sous SMA200 ({vs200:+.1f}%)")

    if sma5 and close:
        if close > sma5:
            score += 8
            reasons.append("Momentum court terme OK (Close>SMA5)")
        else:
            score -= 6
            reasons.append("Momentum faible (Close<SMA5)")

    if vol is not None:
        if vol > 45 and not is_etf:
            score -= 10
            reasons.append(f"Vol elevee ({vol:.0f}%)")
        elif vol < 25:
            score += 4

    # Prefer leaving cash runway (cost 8–45% of budget).
    weight = px / budget * 100 if budget else 100
    if 8 <= weight <= 45:
        score += 10
        reasons.append(f"1 part = {weight:.0f}% du cash — laisse un runway")
    elif weight > 70:
        score -= 12
        reasons.append(f"1 part = {weight:.0f}% — trop concentre")

    if vix > _VIX_PANIC and not is_etf:
        score -= 20
        reasons.append("VIX panic — privilegier ETF/cash")

    score = int(max(0, min(100, round(score))))
    if score >= 72:
        reco = "ACHETER"
    elif score >= 55:
        reco = "SURVEILLER"
    elif score >= 40:
        reco = "ATTENDRE"
    else:
        reco = "EVITER"

    return {
        "ticker": ticker,
        "price": float(px),
        "score": score,
        "reco": reco,
        "why": " · ".join(reasons[:4]),
        "kind": "ETF" if is_etf else "Action",
        "rsi": rsi,
        "vs_sma200": vs200,
        "weight_pct": weight,
    }


@st.cache_data(ttl=600, show_spinner=False)
def rank_affordable_alternatives(budget: float, vix: float) -> list[dict]:
    """Rank PEA ETFs + liquid stocks affordable with current cash."""
    universe = [
        # Low-fee / PEA ETFs first (CW8 often unaffordable in MICRO)
        "EWLD.PA", "PAEEM.PA", "ESE.PA", "C50.PA", "PE500.PA", _CORE_TICKER,
        # Liquid large/mid caps
        "STLAP.PA", "ORA.PA", "ENGI.PA", "VIE.PA", "GLE.PA", "ACA.PA",
        "SAN.PA", "TTE.PA", "BNP.PA", "RNO.PA", "SGO.PA", "CAP.PA",
        "AIR.PA", "HO.PA", "ML.PA", "BN.PA", "PUB.PA",
    ]
    rows = [score_ticker_opportunity(t, budget, vix) for t in universe]
    rows = [r for r in rows if r["reco"] != "INACCESSIBLE" and r["price"] > 0]
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows


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
        "MICRO": f"Capital {equity:,.0f} \u20ac : trop faible pour diversifier / acheter le Core.",
        "STARTER": f"Capital {equity:,.0f} \u20ac : 1–2 lignes max, plafonds 15%/25% assouplis.",
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
        out["summary"] = summary
    elif out["is_etf"] or ticker == _CORE_TICKER:
        out["summary"] = (
            f"{name} est un ETF eligible PEA. Il replique un indice large "
            "(ex. MSCI World pour CW8) au lieu d'un risque entreprise unique. "
            "C'est l'ancre Core du systeme V-Prime."
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
            "Ancre Core V-Prime (MSCI World PEA). Cible 70–75% de l'equity "
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
        import urllib.request

        url = (
            "https://gamma-api.polymarket.com/events?"
            "active=true&closed=false&order=volume24hr&ascending=false&limit=50"
        )
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "PEA-Sniper-Terminal/1.0", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            events = json.loads(resp.read().decode("utf-8"))
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
    "<h1>\U0001F6E1\uFE0F PEA SNIPER TERMINAL "
    "<span style='color:#00FF00; font-size:20px;'>V-PRIME</span></h1>",
    unsafe_allow_html=True,
)

universe_df = load_universe()
# Populate the name lookup with every universe entry (STEP 1.3 coverage).
TICKER_NAMES.update(dict(zip(universe_df["Ticker"], universe_df["Name"])))

# Live streaming ticker tape across the top.
_tape_symbols = ",".join(
    f'{{"proName":"{_tv_symbol(t)}","title":"{short_name(t)}"}}'
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
# STEP 2 - Top HUD (with plain-language tooltips)
# =============================================================================
positions = portfolio.positions
invested = sum(p.market_value for p in positions)
unrealized = sum((p.current_price - p.avg_entry_price) * p.qty_shares for p in positions)
unrealized_pct = (unrealized / invested * 100) if invested else 0.0
cash_pct = (portfolio.cash_available / portfolio.total_equity * 100
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
    st.markdown(metric_box(
        "Liquidites (Cash)", f"{portfolio.cash_available:,.2f} \u20ac",
        sub=f"{cash_pct:.1f}% de l'equity", accent="muted", sub_cls="sub-muted",
        help_text="Argent disponible non investi, pret a saisir de nouvelles "
                  "opportunites d'achat.",
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

r1, r2, r3, r4 = st.columns(4)
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
    over = sat_used_pct > 100
    ssub = f"{satellite_value:,.0f} / {sat_budget_eur:,.0f} \u20ac (max {_SAT_BUDGET*100:.0f}%)"
    st.markdown(metric_box(
        "Budget Satellite Utilise", f"{sat_used_pct:.0f}%", sub=ssub,
        accent="red" if over else "cyan", sub_cls="sub-red" if over else "sub-muted",
        help_text="Capital alloue aux actions individuelles (max 30% du "
                  "portefeuille total) pour chercher de la surperformance. Le "
                  "reste est investi dans l'ETF Monde (le Coeur du portefeuille).",
    ), unsafe_allow_html=True)
with r4:
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
# Tabs
# =============================================================================
tab_gen, tab_pf, tab_mkt, tab_uni, tab_arch = st.tabs([
    "📊 General & Signaux",
    "🎯 Portefeuille & Allocation",
    "🌍 Exploration",
    "📋 Univers Complet",
    "🧠 Architecture & Documentation",
])

# --- Tab: General + Signals --------------------------------------------------
with tab_gen:
    st.markdown(
        "<div class='info-text'>Briefing + registre des signaux + "
        "<b>suggestion de portefeuille adaptative</b> selon ton capital. "
        "Aucun ordre n'est envoye depuis ici — Discord reste le copilot.</div>",
        unsafe_allow_html=True,
    )

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
        f"<div class='eli5'>{suggestion.get('summary', '')}<br><br>"
        f"<b style='color:{_AMBER};'>Pourquoi ce mode ({suggestion.get('mode')}) :</b> "
        f"{suggestion.get('mode_why', '')}<br><br>"
        f"{suggestion.get('cash_explain', '')}</div>",
        unsafe_allow_html=True,
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
        "liquides. Score = RSI + tendance SMA200 + momentum + fit cash + "
        "bonus diversification ETF. <b>ACHETER / SURVEILLER / ATTENDRE / EVITER</b>. "
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
            "Reco": a.get("reco", ""),
            "RSI": f"{a['rsi']:.0f}" if a.get("rsi") is not None else "—",
            "vs SMA200": (
                f"{a['vs_sma200']:+.1f}%" if a.get("vs_sma200") is not None else "—"
            ),
            "Poids 1 part": f"{a.get('weight_pct', 0):.0f}%",
            "Pourquoi": str(a.get("why", ""))[:110],
        } for i, a in enumerate(alts)])
        reco_colors = []
        for a in alts:
            r = a.get("reco")
            reco_colors.append(
                _NEON if r == "ACHETER" else
                _AMBER if r == "SURVEILLER" else
                _CYAN if r == "ATTENDRE" else _RED
            )
        st.plotly_chart(
            dark_table(adisp, height=min(520, 56 + 32 * len(adisp)),
                       font_color_map={"Reco": reco_colors, "Score": reco_colors},
                       col_widths=[0.5, 2.0, 0.7, 0.8, 0.8, 1.0, 0.6, 0.9, 0.8, 2.4]),
            width="stretch",
            key="gen_alternatives_ranking_table",
        )
    else:
        st.caption("Aucune alternative liquide sous ton cash actuel.")

    horizons = suggestion.get("horizons") or {}
    if horizons:
        with st.expander("Horizons d'allocation (court / moyen / long)", expanded=False):
            h_choice = st.radio(
                "Horizon",
                ["court", "moyen", "long"],
                format_func=lambda k: (horizons.get(k) or {}).get("label", k),
                horizontal=True,
                key="gen_horizon_radio",
            )
            hz = horizons.get(h_choice) or {}
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
                f"<b style='color:{_WHITE};'>{r['title']}</b>"
                f"<div style='color:#D0D0D0;font-size:13px;margin-top:6px;"
                f"line-height:1.4;'><b style='color:{_AMBER};'>Justification :</b> "
                f"{r['why']}</div></div>",
                unsafe_allow_html=True,
            )
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

    st.markdown("---")
    st.markdown("#### ⚡ Signaux & Registre")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("##### En attente (Discord)")
        pending = pending_gen
        if pending.empty:
            st.info(
                "Aucun signal aujourd'hui. Le marche ne presente pas "
                "d'opportunites asymetriques selon nos filtres, ou la "
                "volatilite est trop elevee."
            )
        else:
            disp = pd.DataFrame({
                "Titre": [format_name(t) for t in pending["ticker"]],
                "Type": pending["signal_type"],
                "Score": [f"{s:.1f}" for s in pending["score"]],
                "Raison": pending["reason"].fillna(""),
                "Date": [str(x)[:16] for x in pending["created_at"]],
            })
            st.plotly_chart(
                dark_table(disp, height=320,
                           font_color_map={"Score": [_NEON] * len(disp)},
                           col_widths=[2, 0.8, 0.7, 2.4, 1.2]),
                width="stretch",
                key="gen_pending_signals_table",
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
    st.markdown("#### 📰 Actualites (impact marche)")
    st.markdown(
        "<div class='info-text'>Une seule liste dedupliquee, classee par "
        "impact. Contexte seulement — jamais un trigger d'ordre.</div>",
        unsafe_allow_html=True,
    )
    news_bundle = get_general_news_bundle(watch)
    score_gen = st.checkbox(
        "Scorer les news (IA + mots-cles)",
        value=False,
        key="gen_score_news",
        help="Impact FORT/MOYEN/FAIBLE. Cache 1h. Decoche = heuristique rapide.",
    )
    if news_bundle:
        if score_gen:
            with st.spinner("Notation des actualites…"):
                scored_bundle = [
                    (n, score_news_with_llm(n.get("ticker", ""), n.get("title", "")))
                    for n in news_bundle
                ]
        else:
            scored_bundle = [
                (n, heuristic_news_score(n.get("title", ""))) for n in news_bundle
            ]
        scored_bundle.sort(key=lambda x: abs(x[1]), reverse=True)
        nc1, nc2 = st.columns(2)
        for i, (n, sc) in enumerate(scored_bundle[:12]):
            with (nc1 if i % 2 == 0 else nc2):
                render_news_card(n.get("ticker", ""), n, sc)
    else:
        st.caption("Aucune actualite recente sur la watchlist.")

# --- Tab: Portfolio ----------------------------------------------------------
with tab_pf:
    st.markdown(
        "<div class='info-text'>Decomposition de l'exposition sectorielle. "
        "En capital eleve, le risque V-Prime limite a 25% / secteur et 15% / "
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

    st.markdown("---")
    with st.expander("✏️ Ajuster le wallet (cash & positions)", expanded=False):
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
with tab_mkt:
    st.markdown(
        "<div class='info-text'>Exploration marche (top/flop univers) + "
        "<b>fiche ticker</b> : graphique plein ecran, analyse technique "
        "expliquee, actualites, insiders, Polymarket macro.</div>",
        unsafe_allow_html=True,
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

            st.markdown("#### Classement (top & flop liquides)")
            show = pd.concat([perf.head(12), perf.tail(12)]).drop_duplicates("Ticker")
            show = show.sort_values("Performance (%)", ascending=True)
            show["Label"] = [f"{short_name(t)} ({t})" for t in show["Ticker"]]
            bar = pex.bar(
                show, x="Performance (%)", y="Label", orientation="h",
                color="Performance (%)", color_continuous_scale=_DIVERGE,
                color_continuous_midpoint=0,
                hover_data={"Current Price": ":.2f", "Ticker": True, "Label": False},
            )
            _style_dark_fig(bar, height=max(420, 22 * len(show)))
            bar.update_layout(margin=dict(t=10, l=0, r=0, b=0),
                              coloraxis_showscale=False,
                              yaxis_title="", xaxis_title=f"Perf % · {interval_label}")
            st.plotly_chart(bar, width="stretch")

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

            with st.expander("Table complete du scan liquide", expanded=False):
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
    selected = st.selectbox(
        "Actif a analyser", options, index=default_idx,
        format_func=format_name, key="explore_ticker",
    )
    tv = _tv_symbol(selected)

    dossier = get_ticker_dossier(selected)
    st.markdown(
        f"<div class='eli5'><b style='color:{_CYAN};'>Qui est {dossier.get('name')} ?</b><br>"
        f"{dossier.get('summary', '')}<br>"
        f"<span style='color:{_MUTED};'>"
        f"Secteur: {dossier.get('sector') or 'n/a'} · "
        f"Industrie: {dossier.get('industry') or 'n/a'}"
        f"{' · ETF' if dossier.get('is_etf') else ''}</span></div>",
        unsafe_allow_html=True,
    )
    cat1, cat2 = st.columns(2)
    with cat1:
        st.markdown("**News / catalyseurs qui aideraient**")
        for c in dossier.get("catalysts") or []:
            st.markdown(f"- {c}")
    with cat2:
        st.markdown("**Evenements a surveiller (ne pas vouloir)**")
        for r in dossier.get("risk_events") or []:
            st.markdown(f"- {r}")

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

    # Technical analysis explanation (full width)
    st.markdown(
        f"<div class='eli5'><b style='color:{_AMBER};'>"
        f"Analyse technique expliquee — {format_name(selected)}</b><br>"
        f"{build_ta_explanation(ind, alpha)}</div>",
        unsafe_allow_html=True,
    )

    # Full-width TradingView chart
    chart_html = f"""
    <div class="tradingview-widget-container" style="height:620px;width:100%">
      <div id="tv_chart_explore" style="height:620px;width:100%"></div>
      <script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
      <script type="text/javascript">
        new TradingView.widget({{
          "autosize": true, "symbol": "{tv}", "interval": "D",
          "timezone": "Europe/Paris", "theme": "dark", "style": "1",
          "locale": "fr", "enable_publishing": false,
          "hide_side_toolbar": false, "allow_symbol_change": true,
          "studies": ["RSI@tv-basicstudies", "MASimple@tv-basicstudies"],
          "container_id": "tv_chart_explore"
        }});
      </script>
    </div>
    """
    components.html(chart_html, height=640)

    # TA widget + SMAs under chart
    tw1, tw2 = st.columns([1, 1])
    with tw1:
        ta_html = f"""
        <div class="tradingview-widget-container">
          <div class="tradingview-widget-container__widget"></div>
          <script type="text/javascript"
            src="https://s3.tradingview.com/external-embedding/embed-widget-technical-analysis.js" async>
          {{"interval":"1D","width":"100%","isTransparent":true,"height":380,
            "symbol":"{tv}","showIntervalTabs":true,"locale":"fr","colorTheme":"dark"}}
          </script>
        </div>
        """
        components.html(ta_html, height=400)
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
            f"TradingView: <code>{tv}</code></div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    # News — full width, 2 columns (not a cramped side panel)
    st.markdown(f"#### 📰 Actualites — {short_name(selected)}")
    news = get_recent_news(selected, limit=8)
    if news:
        score_toggle = st.checkbox(
            "Scorer l'impact (IA + mots-cles)",
            value=True,
            key="explore_score_news",
        )
        if score_toggle:
            with st.spinner("Notation…"):
                scores = [score_news_with_llm(selected, n["title"]) for n in news]
        else:
            scores = [heuristic_news_score(n["title"]) for n in news]
        ranked = sorted(zip(news, scores), key=lambda x: abs(x[1] or 0), reverse=True)
        ncol1, ncol2 = st.columns(2)
        for i, (n, sc) in enumerate(ranked):
            with (ncol1 if i % 2 == 0 else ncol2):
                render_news_card(selected, n, sc)
    else:
        st.caption("Aucune actualite majeure recente pour cet actif.")

    # Insiders — AMF first (official), then FMP, then Yahoo
    st.markdown("---")
    st.markdown("#### 🕵️ Activite des dirigeants (insiders)")
    st.markdown(
        "<div class='info-text'><b>Cascade stricte : AMF BDIF → FMP → Yahoo</b>. "
        "L'AMF est la source legale officielle FR. Si BDIF est bloque (WAF / "
        "HTTP 500), le terminal bascule sur Financial Modeling Prep "
        "(<code>FMP_API_KEY</code>), puis yfinance. Un achat net massif = "
        "signal de confiance interne, pas un ordre automatique.</div>",
        unsafe_allow_html=True,
    )
    insider_df = get_insider_data(selected)
    if insider_df.empty:
        st.warning(
            f"Aucune transaction insider pour {format_name(selected)}. "
            "AMF/FMP/Yahoo n'ont rien renvoye (couverture variable sur .PA)."
        )
    else:
        src_note = ""
        if "Source" in insider_df.columns and len(insider_df):
            src_note = f" · Source: {insider_df['Source'].iloc[0]}"
        st.caption(f"{len(insider_df)} declaration(s){src_note}")
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
            dark_table(disp, height=min(420, 44 + 30 * max(len(disp), 1)),
                       font_color_map=font_map),
            width="stretch",
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
with tab_uni:
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
        disp = pd.DataFrame({
            "Titre": view["Name"], "Ticker": view["Ticker"],
            "Secteur": view["Sector"],
        })
        st.plotly_chart(dark_table(disp, height=400,
                                   col_widths=[2, 1, 1.5]), width="stretch")

# --- Tab: Architecture & Documentation --------------------------------------
with tab_arch:
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
- **1er du mois** : Monthly Rebalancer (prise de profit / stop-loss).
- Force manuelle : `python main_scheduler.py --now`

---

### 📡 Les Donnees

| Source | Usage | Statut |
|--------|--------|--------|
| **yfinance** | OHLCV, calendrier, insiders, news fallback | Primaire |
| **VIX / VSTOXX** | Coupe-circuit panic (`VIX_PANIC_THRESHOLD`) | `^V2TX` puis `^VIX` |
| **TradingView** | Graphiques + jauge TA (UI only) | Widgets |
| **Polymarket Gamma** | Probabilites macro (contexte) | Live, no auth |
| **Boursorama** | Profil PEA/SRD, consensus, news (best-effort) | Scraper fragile |
| **AMF BDIF** | Declarations dirigeants (**primaire**) | Officiel FR ; WAF/HTTP 500 possible → FMP → Yahoo |
| **FMP** | Insiders fallback (`FMP_API_KEY`) | Secondaire |
| **OpenRouter** | Sentiment news + briefing geo (explique, ne decide pas) | Optionnel |
| **SQLite + DuckDB** | Portfolio / audit / equity curve / OHLCV | Local |

---

### 🖥️ Dashboard (onglets)

| Onglet | Contenu |
|--------|---------|
| **General & Signaux** | Suggestion adaptative **multi-horizon**, explication cash, fiche ETF Core, reco, geo, registre, news du mois |
| **Portefeuille** | Equity curve + allocation + editeur wallet (SQLite) |
| **Exploration** | Scan liquide top/flop + trajectoires, fiche ticker (dossier entreprise, TA expliquee, news, insiders, Polymarket) |
| **Univers** | Liste PEA + **perf moyenne par secteur** (horizon reglable) |
| **Architecture** | Cette page |

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

---

### 🛡️ Bouclier de risque

| Garde-fou | Regle |
|-----------|-------|
| Zero levier | Pas de marge |
| Budget satellite | Max ~30% equity |
| Secteur / ligne | Max ~25% / ~15% (assoupli en MICRO) |
| VIX panic | Bloque nouveaux satellites |
| Stop / shave | ATR dynamique (2.5×ATR14) exit / +20% trim 20% |
| Execution | Discord only |

---

### 🖥️ Architecture technique

```
AMF → FMP → yfinance / VIX / Bourso best-effort
        → SignalGenerator + SmartDCA
        → CorrelationFirewall + PeaSizer + MacroVeto
        → Monthly ATR rebalancer
        → Discord Copilot
        → SQLite (portfolio + equity curve)  ↔  Streamlit Dashboard
        → DuckDB (OHLCV)
```

Le dashboard lit l'etat en continu. L'editeur de wallet peut ecrire
cash/positions. Les ordres restent Discord + scheduler.
""")

# =============================================================================
# Footer + optional auto-refresh
# =============================================================================
st.write("---")
st.caption(
    "PEA Sniper Terminal V-Prime \u00b7 Zero-leverage \u00b7 Execution manuelle "
    "via Discord \u00b7 Donnees: yfinance / TradingView \u00b7 "
    "Ceci n'est PAS un conseil en investissement."
)

if auto_refresh:
    import time as _time

    _time.sleep(int(refresh_secs))
    st.rerun()
