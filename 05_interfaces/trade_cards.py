"""HTML trade / signal cards for the Streamlit terminal.

Pure presentation helpers: take a portfolio snapshot + signal fields and emit
Bloomberg-ish cards with sizing rationale, ATR risk, conviction tier, and
sector impact. No broker / DB writes.
"""

from __future__ import annotations

from typing import Any, Optional

# Default accents — caller may pass palette overrides.
_TEXT = "#E0E0E0"
_MUTED = "#9BA3AF"
_AMBER = "#FFB000"
_NEON = "#00FF00"
_RED = "#FF3B30"
_CYAN = "#00B4D8"
_PANEL = "#0A0A0A"


def conviction_tier(score: float) -> tuple[str, str]:
    """Map score to a visual tier label.

    Tier A = deep oversold / high conviction (score ≥ 90).
    Tier B = base MRE pass (score ≥ 75).
    Tier C = weaker / informational.

    Returns:
        tuple[str, str]: ``(label, color)``.
    """
    if score >= 90:
        return "Tier A", _NEON
    if score >= 75:
        return "Tier B", _AMBER
    return "Tier C", _MUTED


def sector_impact_line(
    portfolio: Any,
    ticker: str,
    sector: str,
    notional: float,
    equity: float,
    sector_cap_pct: float = 25.0,
) -> str:
    """Human line: sector weight before → after this buy."""
    if equity <= 0:
        return "Impact secteur: n/a (equity nulle)"
    before = 0.0
    for p in getattr(portfolio, "positions", []) or []:
        if getattr(p, "sector", "") == sector:
            before += float(getattr(p, "market_value", 0.0) or 0.0)
    before_pct = before / equity * 100.0
    after_pct = (before + max(0.0, notional)) / equity * 100.0
    return (
        f"Secteur {sector}: {before_pct:.1f}% → {after_pct:.1f}% "
        f"(cap {sector_cap_pct:.0f}%)"
    )


def atr_risk_line(
    qty: int,
    atr: Optional[float],
    atr_mult: float,
    equity: float,
) -> str:
    """Max € / % loss if the 2.5×ATR stop is hit (R-style risk)."""
    if not qty or atr is None or atr <= 0:
        return "Risque stop ATR: n/a (historique insuffisant)"
    risk_eur = float(qty) * atr_mult * float(atr)
    risk_pct = (risk_eur / equity * 100.0) if equity > 0 else 0.0
    return f"Risque stop ATR: −{risk_eur:,.0f} € ({risk_pct:.2f}% equity)"


def market_impact_line(
    adv: Optional[float],
    notional: float,
) -> str:
    """Estimated market impact based on ticket size vs 20d Average Daily Volume (ADV)."""
    if adv is None or adv <= 0:
        return "Impact marché estimé: < 0.05% (liquidité standard)"
    participation_pct = (notional / adv) * 100.0 if adv > 0 else 0.0
    color = "sub-green" if participation_pct < 1.0 else ("sub-amber" if participation_pct < 5.0 else "sub-red")
    return f"Impact marché estimé: {participation_pct:.2f}% de l'ADV 20j (€{adv:,.0f})"


def render_signal_card(
    *,
    ticker: str,
    title: str,
    signal_type: str,
    score: float,
    qty: Optional[int],
    reason: str,
    sizing: Optional[dict] = None,
    lineage: Optional[dict] = None,
    sector_line: str = "",
    risk_line: str = "",
    impact_line: str = "",
    created_at: str = "",
) -> str:
    """Build one approved/pending trade card as HTML.

    Args:
        ticker: Raw symbol.
        title: Display name (``Full Name (TICKER)``).
        signal_type: BUY / SELL.
        score: 0–100.
        qty: Target shares (may be None).
        reason: Pipeline explanation.
        sizing: Optional dict from ``PeaSizer.size_with_explanation``.
        lineage: Optional dict with ML inference feature lineage.
        sector_line: Precomputed sector impact sentence.
        risk_line: Precomputed ATR risk sentence.
        created_at: Timestamp string.

    Returns:
        str: HTML snippet safe for ``st.markdown(..., unsafe_allow_html=True)``.
    """
    tier, tier_color = conviction_tier(float(score or 0))
    is_buy = str(signal_type).upper() == "BUY"
    border = _NEON if is_buy and score >= 75 else (_AMBER if is_buy else _RED)

    sizing_html = ""
    if sizing:
        vol = sizing.get("historical_volatility")
        vol_s = f"{vol * 100:.1f}%" if isinstance(vol, (int, float)) and vol else "n/a"
        sizing_html = (
            f"<div style='margin-top:8px;color:{_MUTED};font-size:12px;line-height:1.45;'>"
            f"<b style='color:{_CYAN};'>Sizing</b> — "
            f"Kelly {sizing.get('kelly_fraction', 0):.2f} × score {sizing.get('score', score):.0f}/100"
            f" · vol {vol_s} (facteur {sizing.get('vol_factor', 1):.2f})"
            f" · ticket {sizing.get('notional', 0):,.0f} €"
            f" · poids {sizing.get('weight_pct', 0):.2f}% equity"
            f"</div>"
        )

    # ML Predictor & Conformal Confidence row
    ml_prob = None
    shap_vals = None
    ml_interval = None

    if lineage and isinstance(lineage, dict):
        ml_prob = lineage.get("ml_probability")
        shap_vals = lineage.get("shap_values")
        ml_interval = lineage.get("ml_interval")
    if ml_prob is None and sizing and isinstance(sizing, dict):
        ml_prob = sizing.get("ml_probability")
        shap_vals = sizing.get("shap_values")
        ml_interval = sizing.get("ml_interval")

    ml_html = ""
    if ml_prob is not None:
        try:
            p_val = float(ml_prob)
            prob_pct = p_val * 100.0 if p_val <= 1.0 else p_val
            int_txt = ""
            if ml_interval and isinstance(ml_interval, (list, tuple)) and len(ml_interval) == 2:
                low_p = float(ml_interval[0]) * 100.0 if float(ml_interval[0]) <= 1.0 else float(ml_interval[0])
                high_p = float(ml_interval[1]) * 100.0 if float(ml_interval[1]) <= 1.0 else float(ml_interval[1])
                int_txt = f" (Confidence Interval: {low_p:.0f}%-{high_p:.0f}%)"

            shap_txt = ""
            if shap_vals and isinstance(shap_vals, dict):
                top_shaps = sorted(
                    [(k, float(v)) for k, v in shap_vals.items()],
                    key=lambda x: abs(x[1]),
                    reverse=True,
                )[:2]
                if top_shaps:
                    shap_items = [f"{k} ({'+' if v > 0 else ''}{v:.2f})" for k, v in top_shaps]
            shap_bars_html = ""
            if shap_vals and isinstance(shap_vals, dict):
                pos_shaps = sorted([(k, float(v)) for k, v in shap_vals.items() if float(v) > 0], key=lambda x: x[1], reverse=True)[:2]
                neg_shaps = sorted([(k, float(v)) for k, v in shap_vals.items() if float(v) < 0], key=lambda x: x[1])[:1]
                bar_items = []
                for k, v in pos_shaps:
                    bar_items.append(f"<span style='background:rgba(34,197,94,0.15);border:1px solid #22C55E;color:#4ADE80;padding:2px 6px;border-radius:3px;font-size:11px;'>▲ {k} (+{v:.2f})</span>")
                for k, v in neg_shaps:
                    bar_items.append(f"<span style='background:rgba(239,68,68,0.15);border:1px solid #EF4444;color:#F87171;padding:2px 6px;border-radius:3px;font-size:11px;'>▼ {k} ({v:.2f})</span>")
                if bar_items:
                    shap_bars_html = f"<div style='margin-top:4px;display:flex;gap:6px;flex-wrap:wrap;'>{' '.join(bar_items)}</div>"

            ml_html = (
                f"<div style='margin-top:6px;color:#38BDF8;font-size:12px;line-height:1.45;'>"
                f"🧠 <b>ML Probability</b>: <b style='color:#7DD3FC;'>{prob_pct:.1f}%</b>{int_txt}{shap_txt}"
                f"{shap_bars_html}"
                f"</div>"
            )
        except Exception:
            ml_html = ""




    adaptive_html = ""
    dynamic_rsi = None
    dyn_regime = None
    rsi_val = None
    if lineage and isinstance(lineage, dict):
        dynamic_rsi = lineage.get("dynamic_rsi_threshold")
        dyn_regime = lineage.get("current_regime")
        rsi_val = lineage.get("rsi_14")
    if dynamic_rsi is None and sizing and isinstance(sizing, dict):
        dynamic_rsi = sizing.get("dynamic_rsi_threshold")
        dyn_regime = sizing.get("current_regime")
        rsi_val = sizing.get("rsi_14")

    if dynamic_rsi is not None:
        rsi_s = f"{rsi_val:.1f}" if rsi_val is not None else "actuel"
        reg_s = f"{dyn_regime}" if dyn_regime else "actif"
        adaptive_html = (
            f"<div style='margin-top:6px;color:#FBBF24;font-size:12px;line-height:1.4;'>"
            f"💡 <b>Rationale</b>: RSI ({rsi_s}) dropped below the adaptive threshold ({dynamic_rsi:.0f}) tailored for the {reg_s} regime."
            f"</div>"
        )

    extras = ""

    if impact_line:
        extras += (
            f"<div style='margin-top:6px;color:{_CYAN};font-size:12px;'>"
            f"⚡ {impact_line}</div>"
        )
    if risk_line:
        extras += (
            f"<div style='margin-top:4px;color:{_AMBER};font-size:12px;'>"
            f"⚠ {risk_line}</div>"
        )
    if sector_line:
        extras += (
            f"<div style='margin-top:4px;color:{_MUTED};font-size:12px;'>"
            f"▣ {sector_line}</div>"
        )

    qty_s = "—" if qty is None else str(qty)
    when = f"<span style='color:{_MUTED};font-size:11px;'>{created_at}</span>" if created_at else ""

    return f"""
<div style="background:{_PANEL};padding:12px 14px;margin-bottom:10px;
 border:1px solid #2A2A2A;border-left:4px solid {border};
 font-family:'Courier New',monospace;">
  <div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;">
    <div>
      <span style="color:{_TEXT};font-weight:700;font-size:15px;">{title}</span>
      <span style="color:{_MUTED};font-size:12px;margin-left:8px;">{signal_type}</span>
    </div>
    <div>
      <span style="color:{tier_color};font-weight:700;border:1px solid {tier_color};
       padding:2px 8px;font-size:11px;letter-spacing:1px;">{tier}</span>
      <span style="color:{_NEON if score >= 75 else _TEXT};margin-left:10px;">
        score {score:.0f}</span>
      <span style="color:{_TEXT};margin-left:10px;">qty {qty_s}</span>
    </div>
  </div>
  <div style="color:{_TEXT};font-size:13px;margin-top:8px;line-height:1.45;">
    {reason}
  </div>
  {adaptive_html}
  {sizing_html}
  {ml_html}
  {extras}
  <div style="margin-top:8px;">{when}</div>
</div>

"""
