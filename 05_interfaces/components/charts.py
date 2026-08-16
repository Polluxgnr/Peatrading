"""Advanced Interactive Plotly Charts & Glass-Box Visual Explainability.

Provides high-end interactive charts with:
  - Candlestick price action overlaid with SMA 50, SMA 200, and HMM regime background shades.
  - Dynamic RSI oscillator with regime-adaptive oversold shading zones.
  - Statistical Arbitrage Cointegration Z-Score tracker with +/- 2.0 sigma entry boundaries.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go

logger = logging.getLogger("interactive_charts")


def render_advanced_price_chart(
    ticker: str,
    ohlcv_df: pd.DataFrame,
    hmm_regimes: Optional[pd.Series] = None,
    sma_50: Optional[pd.Series] = None,
    sma_200: Optional[pd.Series] = None,
    title: Optional[str] = None,
) -> go.Figure:
    """Create a dark-themed interactive candlestick chart with SMA overlays and HMM regime bands.

    Args:
        ticker: Ticker symbol (e.g. "MC.PA").
        ohlcv_df: DataFrame with DatetimeIndex and ['Open', 'High', 'Low', 'Close'].
        hmm_regimes: Series aligned with df index containing 'BULL', 'BEAR', 'VOLATILE'.
        sma_50: Series of 50-day Simple Moving Average.
        sma_200: Series of 200-day Simple Moving Average.
        title: Optional custom chart title.

    Returns:
        go.Figure: Interactive Plotly figure.
    """
    fig = go.Figure()

    if ohlcv_df is None or ohlcv_df.empty:
        fig.update_layout(
            template="plotly_dark",
            title=f"{ticker} — Données de prix indisponibles",
            paper_bgcolor="#0A0A0A",
            plot_bgcolor="#0A0A0A",
        )
        return fig

    # Align columns
    cols = {c.lower(): c for c in ohlcv_df.columns}
    o_col = cols.get("open", "Open")
    h_col = cols.get("high", "High")
    l_col = cols.get("low", "Low")
    c_col = cols.get("close", "Close")

    # 1. Candlestick Price Trace
    fig.add_trace(
        go.Candlestick(
            x=ohlcv_df.index,
            open=ohlcv_df[o_col],
            high=ohlcv_df[h_col],
            low=ohlcv_df[l_col],
            close=ohlcv_df[c_col],
            name="Cours",
            increasing_line_color="#00FF00",
            decreasing_line_color="#FF3B30",
            increasing_fillcolor="#00FF00",
            decreasing_fillcolor="#FF3B30",
        )
    )

    # 2. SMA 50 Overlay (Orange)
    if sma_50 is not None and not sma_50.dropna().empty:
        fig.add_trace(
            go.Scatter(
                x=sma_50.index,
                y=sma_50,
                mode="lines",
                line=dict(color="#FFB000", width=1.5),
                name="SMA 50",
            )
        )

    # 3. SMA 200 Overlay (White / Light Gray)
    if sma_200 is not None and not sma_200.dropna().empty:
        fig.add_trace(
            go.Scatter(
                x=sma_200.index,
                y=sma_200,
                mode="lines",
                line=dict(color="#E0E0E0", width=1.5, dash="dot"),
                name="SMA 200",
            )
        )

    # 4. HMM Regime Background Highlights
    if hmm_regimes is not None and not hmm_regimes.dropna().empty:
        regime_colors = {
            "BULL": "rgba(0, 255, 0, 0.08)",
            "BEAR": "rgba(255, 59, 48, 0.08)",
            "VOLATILE": "rgba(156, 163, 175, 0.08)",
            "PANIC": "rgba(239, 68, 68, 0.15)",
        }
        
        # Group contiguous regime intervals
        reg_series = hmm_regimes.dropna()
        if not reg_series.empty:
            start_dt = reg_series.index[0]
            cur_reg = str(reg_series.iloc[0]).upper()

            for dt, val in reg_series.iloc[1:].items():
                val_upper = str(val).upper()
                if val_upper != cur_reg:
                    color = regime_colors.get(cur_reg, "rgba(156, 163, 175, 0.05)")
                    fig.add_vrect(
                        x0=start_dt,
                        x1=dt,
                        fillcolor=color,
                        opacity=1.0,
                        layer="below",
                        line_width=0,
                    )
                    start_dt = dt
                    cur_reg = val_upper

            # Add final interval
            color = regime_colors.get(cur_reg, "rgba(156, 163, 175, 0.05)")
            fig.add_vrect(
                x0=start_dt,
                x1=reg_series.index[-1],
                fillcolor=color,
                opacity=1.0,
                layer="below",
                line_width=0,
            )

    chart_title = title or f"<b>{ticker}</b> · Analyse Technique & Régimes HMM"
    fig.update_layout(
        template="plotly_dark",
        title=dict(text=chart_title, font=dict(color="#E0E0E0", size=15)),
        paper_bgcolor="#0A0A0A",
        plot_bgcolor="#0A0A0A",
        xaxis=dict(
            rangeslider=dict(visible=False),
            gridcolor="#1F2937",
            showgrid=True,
        ),
        yaxis=dict(
            gridcolor="#1F2937",
            showgrid=True,
            title="Cours (€)",
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(size=11),
        ),
        margin=dict(l=40, r=40, t=50, b=40),
        height=460,
    )

    return fig


def render_rsi_chart(
    rsi_series: pd.Series,
    dynamic_threshold: float = 30.0,
    title: Optional[str] = None,
) -> go.Figure:
    """Create an interactive RSI (14) chart with dynamic regime-adaptive oversold highlighting.

    Args:
        rsi_series: Series containing RSI values (0 to 100).
        dynamic_threshold: Adaptive threshold (e.g. 38.0 for Bull, 30.0 for Volatile, 25.0 for Bear).
        title: Optional title string.

    Returns:
        go.Figure: Interactive RSI figure.
    """
    fig = go.Figure()

    if rsi_series is None or rsi_series.empty:
        fig.update_layout(template="plotly_dark", title="RSI indisponible")
        return fig

    # 1. RSI Line
    fig.add_trace(
        go.Scatter(
            x=rsi_series.index,
            y=rsi_series,
            mode="lines",
            line=dict(color="#FBBF24", width=1.5),
            name="RSI 14",
        )
    )

    # 2. Overbought Level (70)
    fig.add_hline(
        y=70,
        line_dash="dash",
        line_color="#EF4444",
        line_width=1,
        annotation_text="Suracheté (70)",
        annotation_position="top right",
        annotation_font=dict(color="#EF4444", size=10),
    )

    # 3. Dynamic Regime-Adaptive Oversold Level
    fig.add_hline(
        y=dynamic_threshold,
        line_dash="dash",
        line_color="#38BDF8",
        line_width=1.5,
        annotation_text=f"Seuil Adaptatif ({dynamic_threshold:.0f})",
        annotation_position="bottom right",
        annotation_font=dict(color="#38BDF8", size=10),
    )

    # 4. Oversold Shaded Zone [0, dynamic_threshold]
    fig.add_hrect(
        y0=0,
        y1=dynamic_threshold,
        fillcolor="rgba(56, 189, 248, 0.12)",
        layer="below",
        line_width=0,
    )

    # 5. Overbought Shaded Zone [70, 100]
    fig.add_hrect(
        y0=70,
        y1=100,
        fillcolor="rgba(239, 68, 68, 0.10)",
        layer="below",
        line_width=0,
    )

    chart_title = title or f"<b>RSI (14)</b> · Seuil de Survente Dynamique : <b>{dynamic_threshold:.0f}</b>"
    fig.update_layout(
        template="plotly_dark",
        title=dict(text=chart_title, font=dict(color="#E0E0E0", size=13)),
        paper_bgcolor="#0A0A0A",
        plot_bgcolor="#0A0A0A",
        yaxis=dict(
            range=[0, 100],
            gridcolor="#1F2937",
            showgrid=True,
            tickvals=[0, 20, dynamic_threshold, 50, 70, 100],
        ),
        xaxis=dict(
            gridcolor="#1F2937",
            showgrid=True,
        ),
        margin=dict(l=40, r=40, t=40, b=30),
        height=220,
        showlegend=False,
    )

    return fig


def render_statarb_zscore_chart(
    dates: pd.DatetimeIndex,
    zscores: pd.Series,
    ticker_a: str,
    ticker_b: str,
    threshold: float = 2.0,
) -> go.Figure:
    """Create an interactive Statistical Arbitrage Z-Score chart with +/- 2 sigma boundaries.

    Args:
        dates: DatetimeIndex or date list.
        zscores: Series of rolling cointegration spread Z-Scores.
        ticker_a: Primary leg ticker (e.g. "MC.PA").
        ticker_b: Secondary leg ticker (e.g. "OR.PA").
        threshold: Entry z-score threshold (default 2.0).

    Returns:
        go.Figure: Interactive StatArb Z-Score figure.
    """
    fig = go.Figure()

    # Z-Score Line
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=zscores,
            mode="lines",
            line=dict(color="#38BDF8", width=1.8),
            name=f"Spread Z-Score ({ticker_a} / {ticker_b})",
        )
    )

    # Upper Entry (+2.0 Sigma)
    fig.add_hline(
        y=threshold,
        line_dash="dash",
        line_color="#EF4444",
        line_width=1.5,
        annotation_text=f"+{threshold:.1f}σ (Surévaluation)",
        annotation_position="top right",
        annotation_font=dict(color="#EF4444", size=10),
    )

    # Mean Reversion Target (0.0 Sigma)
    fig.add_hline(
        y=0.0,
        line_dash="dot",
        line_color="#9BA3AF",
        line_width=1.0,
        annotation_text="Moyenne (0σ)",
        annotation_position="bottom right",
        annotation_font=dict(color="#9BA3AF", size=10),
    )

    # Lower Entry (-2.0 Sigma)
    fig.add_hline(
        y=-threshold,
        line_dash="dash",
        line_color="#10B981",
        line_width=1.5,
        annotation_text=f"-{threshold:.1f}σ (Sous-évaluation / Achat)",
        annotation_position="bottom right",
        annotation_font=dict(color="#10B981", size=10),
    )

    # Anomaly shading regions
    fig.add_hrect(
        y0=threshold,
        y1=max(threshold + 2.0, float(zscores.max() if not zscores.empty else threshold + 2.0)),
        fillcolor="rgba(239, 68, 68, 0.12)",
        layer="below",
        line_width=0,
    )
    fig.add_hrect(
        y0=min(-threshold - 2.0, float(zscores.min() if not zscores.empty else -threshold - 2.0)),
        y1=-threshold,
        fillcolor="rgba(16, 185, 129, 0.12)",
        layer="below",
        line_width=0,
    )

    fig.update_layout(
        template="plotly_dark",
        title=dict(
            text=f"<b>StatArb Z-Score</b> · Paire Cointégrée <b>{ticker_a}</b> vs <b>{ticker_b}</b>",
            font=dict(color="#E0E0E0", size=14),
        ),
        paper_bgcolor="#0A0A0A",
        plot_bgcolor="#0A0A0A",
        yaxis=dict(
            title="Écart en Écarts-Types (σ)",
            gridcolor="#1F2937",
            showgrid=True,
        ),
        xaxis=dict(
            gridcolor="#1F2937",
            showgrid=True,
        ),
        margin=dict(l=40, r=40, t=45, b=30),
        height=280,
        showlegend=False,
    )

    return fig
