"""Advanced Interactive Plotly Charts & Glass-Box Visual Explainability.

Provides high-end interactive charts with:
  - Candlestick price action overlaid with SMA 50, SMA 200, and HMM regime background shades.
  - Statistical Arbitrage Cointegration Z-Score tracker with +/- 2.0 sigma entry boundaries.
  - Half-circle Macro Volatility Thermometer Gauge indicator (0-100% Attack).
  - Dynamic RSI oscillator with regime-adaptive oversold shading zones.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go

logger = logging.getLogger("interactive_charts")


def render_hmm_candlestick_chart(
    ticker: str,
    df: Optional[pd.DataFrame] = None,
    sma50: Optional[pd.Series] = None,
    sma200: Optional[pd.Series] = None,
    regime_series: Optional[pd.Series] = None,
    title: Optional[str] = None,
    ohlcv_df: Optional[pd.DataFrame] = None,
    sma_50: Optional[pd.Series] = None,
    sma_200: Optional[pd.Series] = None,
    hmm_regimes: Optional[pd.Series] = None,
) -> go.Figure:
    """Create a dark-themed interactive candlestick chart with SMA overlays and HMM regime bands.

    Args:
        ticker: Ticker symbol (e.g. "MC.PA").
        df: DataFrame with DatetimeIndex and ['Open', 'High', 'Low', 'Close'] (or ohlcv_df).
        sma50: Series of 50-day Simple Moving Average (or sma_50).
        sma200: Series of 200-day Simple Moving Average (or sma_200).
        regime_series: Series aligned with df index containing 'BULL', 'BEAR', 'VOLATILE' (or hmm_regimes).
        title: Optional custom chart title.

    Returns:
        go.Figure: Interactive Plotly figure with range selector.
    """
    fig = go.Figure()

    price_df = df if df is not None else ohlcv_df
    s50 = sma50 if sma50 is not None else sma_50
    s200 = sma200 if sma200 is not None else sma_200
    regimes = regime_series if regime_series is not None else hmm_regimes

    if price_df is None or price_df.empty:
        fig.update_layout(
            template="plotly_dark",
            title=f"{ticker} — Données de prix indisponibles",
            paper_bgcolor="#0A0A0A",
            plot_bgcolor="#0A0A0A",
        )
        return fig

    # Align columns
    cols = {c.lower(): c for c in price_df.columns}
    o_col = cols.get("open", "Open")
    h_col = cols.get("high", "High")
    l_col = cols.get("low", "Low")
    c_col = cols.get("close", "Close")

    # 1. Candlestick Price Trace
    fig.add_trace(
        go.Candlestick(
            x=price_df.index,
            open=price_df[o_col],
            high=price_df[h_col],
            low=price_df[l_col],
            close=price_df[c_col],
            name="Cours",
            increasing_line_color="#00FF66",
            decreasing_line_color="#FF3B30",
            increasing_fillcolor="#00FF66",
            decreasing_fillcolor="#FF3B30",
        )
    )

    # 2. SMA 50 Overlay (Orange)
    if s50 is not None and not s50.dropna().empty:
        fig.add_trace(
            go.Scatter(
                x=s50.index,
                y=s50,
                mode="lines",
                line=dict(color="#FF9500", width=1.5),
                name="SMA 50",
            )
        )

    # 3. SMA 200 Overlay (White / Light Gray)
    if s200 is not None and not s200.dropna().empty:
        fig.add_trace(
            go.Scatter(
                x=s200.index,
                y=s200,
                mode="lines",
                line=dict(color="#FFFFFF", width=1.5, dash="dot"),
                name="SMA 200",
            )
        )

    # 4. HMM Regime Background Highlights
    if regimes is not None and not regimes.dropna().empty:
        regime_colors = {
            "BULL": "rgba(0, 255, 102, 0.08)",
            "BEAR": "rgba(255, 59, 48, 0.08)",
            "VOLATILE": "rgba(156, 163, 175, 0.08)",
            "PANIC": "rgba(239, 68, 68, 0.16)",
        }
        
        reg_series = regimes.dropna()

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
            rangeselector=dict(
                buttons=[
                    dict(count=1, label="1M", step="month", stepmode="backward"),
                    dict(count=3, label="3M", step="month", stepmode="backward"),
                    dict(count=6, label="6M", step="month", stepmode="backward"),
                    dict(count=1, label="1Y", step="year", stepmode="backward"),
                    dict(step="all", label="MAX"),
                ],
                bgcolor="#1A1A1A",
                activecolor="#2563EB",
                font=dict(color="#E0E0E0", size=10),
            ),
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


# Alias for backward compatibility
render_advanced_price_chart = render_hmm_candlestick_chart


def render_statarb_zscore_chart(
    pair_label: Optional[str] = None,
    z_score_series: Optional[pd.Series] = None,
    dates: Optional[pd.DatetimeIndex] = None,
    zscores: Optional[pd.Series] = None,
    ticker_a: Optional[str] = None,
    ticker_b: Optional[str] = None,
    threshold: float = 2.0,
) -> go.Figure:
    """Create an interactive Statistical Arbitrage Z-Score chart with +/- 2.0 sigma boundaries.

    Supports both (pair_label, z_score_series) and legacy (dates, zscores, ticker_a, ticker_b) arguments.
    """
    fig = go.Figure()

    # Normalize arguments
    series = z_score_series if z_score_series is not None else zscores
    if series is None or (isinstance(series, (pd.Series, list)) and len(series) == 0):
        fig.update_layout(template="plotly_dark", title="Z-Score StatArb indisponible")
        return fig

    if not isinstance(series, pd.Series):
        x_axis = dates if dates is not None else list(range(len(series)))
        series = pd.Series(series, index=x_axis)

    x_vals = series.index
    label = pair_label or (f"{ticker_a} vs {ticker_b}" if ticker_a and ticker_b else "Paire Cointégrée")

    # Z-Score Line
    fig.add_trace(
        go.Scatter(
            x=x_vals,
            y=series,
            mode="lines",
            line=dict(color="#38BDF8", width=1.8),
            name=f"Z-Score ({label})",
        )
    )

    # Upper Entry (+2.0 Sigma)
    fig.add_hline(
        y=threshold,
        line_dash="dash",
        line_color="#FF3B30",
        line_width=1.5,
        annotation_text=f"+{threshold:.1f}σ (Surévaluation)",
        annotation_position="top right",
        annotation_font=dict(color="#FF3B30", size=10),
    )

    # Mean Reversion Target (0.0 Sigma)
    fig.add_hline(
        y=0.0,
        line_dash="dot",
        line_color="#E5E5EA",
        line_width=1.0,
        annotation_text="Moyenne (0σ)",
        annotation_position="bottom right",
        annotation_font=dict(color="#E5E5EA", size=10),
    )

    # Lower Entry (-2.0 Sigma)
    fig.add_hline(
        y=-threshold,
        line_dash="dash",
        line_color="#00FF66",
        line_width=1.5,
        annotation_text=f"-{threshold:.1f}σ (Achat Spread)",
        annotation_position="bottom right",
        annotation_font=dict(color="#00FF66", size=10),
    )

    # Anomaly shading regions
    s_clean = series.dropna()
    max_val = max(threshold + 2.0, float(s_clean.max() if not s_clean.empty else threshold + 2.0))
    min_val = min(-threshold - 2.0, float(s_clean.min() if not s_clean.empty else -threshold - 2.0))

    fig.add_hrect(
        y0=threshold,
        y1=max_val,
        fillcolor="rgba(255, 59, 48, 0.12)",
        layer="below",
        line_width=0,
    )
    fig.add_hrect(
        y0=min_val,
        y1=-threshold,
        fillcolor="rgba(0, 255, 102, 0.12)",
        layer="below",
        line_width=0,
    )

    fig.update_layout(
        template="plotly_dark",
        title=dict(
            text=f"<b>StatArb Z-Score</b> · {label}",
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


def render_macro_thermometer_gauge(
    attack_pct: float,
    defense_pct: float,
    mode: str = "ATTACK",
) -> go.Figure:
    """Render an institutional half-circle macro thermometer gauge (0-100% Attack allocation).

    Args:
        attack_pct: Fraction or percentage allocated to Attack engine (e.g. 0.70 or 70.0).
        defense_pct: Fraction or percentage allocated to Defense engine.
        mode: Regime mode ('ATTACK', 'DEFENSE', 'BUNKER').

    Returns:
        go.Figure: Half-circle Plotly gauge indicator.
    """
    attack_val = max(0.0, min(100.0, float(attack_pct) * 100.0 if attack_pct <= 1.0 else float(attack_pct)))
    mode_upper = str(mode).upper()
    mode_color = "#FF3B30" if mode_upper == "BUNKER" else ("#FF9500" if mode_upper == "DEFENSE" else "#00FF66")

    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=attack_val,
            domain=dict(x=[0, 1], y=[0, 1]),
            title=dict(
                text=f"<b>Thermomètre Macro : Mode {mode_upper}</b><br><span style='font-size:12px;color:#9E9E9E;'>Allocation Action Cible (Max 98%)</span>",
                font=dict(size=14, color="#E0E0E0"),
            ),
            number=dict(suffix=" %", font=dict(size=26, color=mode_color)),
            gauge=dict(
                axis=dict(range=[0, 100], tickwidth=1, tickcolor="#666", tickvals=[0, 30, 60, 98, 100]),
                bar=dict(color=mode_color, thickness=0.25),
                bgcolor="#111111",
                borderwidth=1,
                bordercolor="#333333",
                steps=[
                    dict(range=[0, 30], color="rgba(255, 59, 48, 0.25)"),   # Red / Defense / Bunker
                    dict(range=[30, 60], color="rgba(255, 149, 0, 0.25)"), # Orange / Neutral
                    dict(range=[60, 100], color="rgba(0, 255, 102, 0.25)"),# Green / Attack
                ],
                threshold=dict(
                    line=dict(color="#FF3B30", width=3),
                    thickness=0.8,
                    value=98.0,
                ),
            ),

        )
    )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0A0A0A",
        plot_bgcolor="#0A0A0A",
        height=230,
        margin=dict(l=25, r=25, t=50, b=20),
    )
    return fig


def render_rsi_chart(
    rsi_series: pd.Series,
    dynamic_threshold: float = 30.0,
    title: Optional[str] = None,
) -> go.Figure:
    """Create an interactive RSI (14) chart with dynamic regime-adaptive oversold highlighting."""
    fig = go.Figure()

    if rsi_series is None or rsi_series.empty:
        fig.update_layout(template="plotly_dark", title="RSI indisponible")
        return fig

    fig.add_trace(
        go.Scatter(
            x=rsi_series.index,
            y=rsi_series,
            mode="lines",
            line=dict(color="#FBBF24", width=1.5),
            name="RSI 14",
        )
    )

    fig.add_hline(
        y=70,
        line_dash="dash",
        line_color="#EF4444",
        line_width=1,
        annotation_text="Suracheté (70)",
        annotation_position="top right",
        annotation_font=dict(color="#EF4444", size=10),
    )

    fig.add_hline(
        y=dynamic_threshold,
        line_dash="dash",
        line_color="#38BDF8",
        line_width=1.5,
        annotation_text=f"Seuil Adaptatif ({dynamic_threshold:.0f})",
        annotation_position="bottom right",
        annotation_font=dict(color="#38BDF8", size=10),
    )

    fig.add_hrect(
        y0=0,
        y1=dynamic_threshold,
        fillcolor="rgba(56, 189, 248, 0.12)",
        layer="below",
        line_width=0,
    )

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

