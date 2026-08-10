"""Model Context Protocol (MCP) Server for PEA Pollux Systematic Engine.

Exposes institutional quantitative tools to Claude Desktop and AI Copilots.
Acts as a decoupled client querying the Internal FastAPI at ``http://localhost:8000``.

Tools:
  - get_portfolio_status(): Account equity, cash buffer, active exposure, and open holdings.
  - get_top_recommendations(): High-conviction trade recommendations awaiting PM execution.
  - analyze_asset(ticker): Technical indicators (RSI, Trend Quality), HMM regime, and news sentiment.

Run:
  python 07_mcp/pollux_mcp.py
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Dict, List, Optional

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("pollux_mcp")

API_BASE_URL = "http://localhost:8000"


def _fetch_api(endpoint: str) -> Dict[str, Any]:
    """Helper to query the Internal FastAPI with robust fallback."""
    url = f"{API_BASE_URL}{endpoint}"
    try:
        resp = requests.get(url, timeout=5.0)
        if resp.status_code == 200:
            return resp.json()
        logger.warning("API call to %s returned %d: %s", endpoint, resp.status_code, resp.text)
        return {"error": f"HTTP {resp.status_code}: {resp.text}"}
    except Exception as exc:
        logger.warning("Failed to connect to internal API at %s: %s", url, exc)
        return {"error": f"Could not connect to PEA Pollux Internal API at {API_BASE_URL}. Ensure 'make api' is running."}


# --- Tool Implementations ---------------------------------------------------

def get_portfolio_status() -> str:
    """Retrieve PEA account balance, total equity, exposure, and open holdings.

    Returns:
        str: Formatted markdown summary of the PEA portfolio state.
    """
    data = _fetch_api("/api/v1/portfolio/summary")
    if "error" in data:
        return f"⚠️ **Error retrieving portfolio**: {data['error']}"

    tot = data.get("total_equity", 0.0)
    cash = data.get("cash_available", 0.0)
    exposure = data.get("exposure_pct", 0.0)
    cash_ratio = data.get("cash_ratio_pct", 0.0)
    positions = data.get("positions", [])

    lines = [
        "### 💼 PEA Portfolio Summary",
        f"- **Total Equity**: `{tot:,.2f} €`",
        f"- **Cash Available**: `{cash:,.2f} €` ({cash_ratio:.1f}% cash buffer)",
        f"- **Active Exposure**: `{exposure:.1f}%`",
        f"- **Open Positions Count**: `{len(positions)}`",
        "",
        "#### 📊 Holdings Breakdown",
    ]

    if not positions:
        lines.append("_No open positions held in the PEA._")
    else:
        lines.append("| Ticker | Qty | Avg Price | Current Price | Market Value | PnL (€) | PnL (%) | Sector |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for p in positions:
            pnl_sign = "+" if p.get("unrealized_pnl_eur", 0.0) >= 0 else ""
            lines.append(
                f"| **{p['ticker']}** | {p['qty_shares']} | {p['avg_entry_price']:.2f} € | "
                f"{p['current_price']:.2f} € | {p['market_value']:,.2f} € | "
                f"{pnl_sign}{p['unrealized_pnl_eur']:.2f} € | {pnl_sign}{p['unrealized_pnl_pct']:.2f}% | "
                f"{p['sector']} |"
            )

    return "\n".join(lines)


def get_top_recommendations() -> str:
    """Retrieve quantitative trade recommendations awaiting human portfolio manager execution.

    Returns:
        str: Formatted markdown list of actionable recommendations.
    """
    data = _fetch_api("/api/v1/recommendations/pending")
    if isinstance(data, dict) and "error" in data:
        return f"⚠️ **Error retrieving recommendations**: {data['error']}"

    if not isinstance(data, list) or not data:
        return "ℹ️ **No pending trade recommendations.** The market currently has no qualifying Mean-Reversion Exhaustion setups, or signals were vetoed by the risk cascade."

    lines = [
        "### 🎯 Active Quantitative Recommendations (Pending Human Execution)",
        "_The system produces data-backed recommendations. Execution authority rests with the human portfolio manager._",
        "",
    ]

    for i, r in enumerate(data, 1):
        lines.append(f"#### {i}. {r.get('action')} **{r.get('ticker')}** (Score: `{r.get('conviction_score', 0):.0f}/100`)")
        lines.append(f"- **Recommended Sizing**: `{r.get('recommended_quantity', 0)} shares` @ `~{r.get('reference_price', 0.0):.2f} €`")
        lines.append(f"- **Quantitative Rationale**: {r.get('rationale', 'N/A')}")
        lines.append(f"- **Generated At**: `{r.get('generated_at', '')}`")
        lines.append("")

    return "\n".join(lines)


def analyze_asset(ticker: str) -> str:
    """Analyze a specific French/European asset with technical indicators, HMM regime, and news sentiment.

    Args:
        ticker: Yahoo Finance symbol, e.g. 'MC.PA', 'OR.PA', 'CW8.PA'.

    Returns:
        str: Deep-dive quantitative context for the asset.
    """
    sym = ticker.upper().strip()
    data = _fetch_api(f"/api/v1/data/ticker/{sym}/context")
    if "error" in data:
        return f"⚠️ **Error analyzing {sym}**: {data['error']}"

    px = data.get("current_price", 0.0)
    r1m = data.get("perf_1m_pct", 0.0)
    r3m = data.get("perf_3m_pct", 0.0)
    r1y = data.get("perf_1y_pct", 0.0)
    rsi = data.get("rsi_14", 50.0)
    sma200 = data.get("sma_200", 0.0)
    trend = data.get("trend_vs_sma200", "UNKNOWN")
    regime = data.get("market_regime", "VOLATILE")
    vix = data.get("vix_level", 0.0)
    sent_avg = data.get("sentiment_score_30d_avg", 0.0)

    lines = [
        f"### 🔍 Quantitative Context for **{sym}**",
        f"- **Current Market Price**: `{px:.2f} €`",
        f"- **Multi-Horizon Performance**: 1M: `{r1m:+.1f}%` | 3M: `{r3m:+.1f}%` | 1Y: `{r1y:+.1f}%`",
        f"- **RSI(14)**: `{rsi:.1f}` ({'⚠️ Oversold Stretch' if rsi < 30 else 'Normal Range'})",
        f"- **SMA 200**: `{sma200:.2f} €` (Trend: **{trend}**)",
        f"- **Market Macro Regime**: `{regime}` (Euro VIX: `{vix:.1f}`)",
        f"- **30-Day News Sentiment**: `{sent_avg:+.1f}` / 100",
        "",
        "#### 📰 Recent Scored News Flow",
    ]

    records = data.get("sentiment_recent_records", [])
    if not records:
        lines.append("_No recent news scored in local SQLite memory._")
    else:
        for it in records:
            lines.append(f"- **[{it.get('source', 'News')}]** {it.get('headline')} (Score: `{it.get('score', 0):+.0f}` on {str(it.get('date_scored', ''))[:10]})")

    return "\n".join(lines)


# --- MCP FastMCP Server Setup -----------------------------------------------

try:
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("pollux_mcp")

    @mcp.tool()
    def mcp_get_portfolio_status() -> str:
        """Retrieve PEA account balance, total equity, exposure, and open holdings."""
        return get_portfolio_status()

    @mcp.tool()
    def mcp_get_top_recommendations() -> str:
        """Retrieve quantitative trade recommendations awaiting human portfolio manager execution."""
        return get_top_recommendations()

    @mcp.tool()
    def mcp_analyze_asset(ticker: str) -> str:
        """Analyze a specific asset with technical indicators, HMM regime, and news sentiment."""
        return analyze_asset(ticker)

except ImportError:
    mcp = None


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        print("--- Testing MCP Tools locally ---")
        print(get_portfolio_status())
        print(get_top_recommendations())
        print(analyze_asset("MC.PA"))
    elif mcp is not None:
        logger.info("Starting PEA Pollux FastMCP Server on stdio...")
        mcp.run()
    else:
        logger.info("Starting standalone MCP Tool CLI...")
        print("MCP Server ready (running in stdio CLI mode).")
