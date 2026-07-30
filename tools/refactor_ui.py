import re
import os

path = "05_interfaces/terminal_dashboard.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

breadth_func_new = """@st.cache_data(ttl=900, show_spinner=False)
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
"""
content = re.sub(r'@st\.cache_data\(ttl=900, show_spinner=False\)\ndef get_market_breadth.*?    except Exception:  # noqa: BLE001\n        return \{"pct_sma50": None, "pct_sma200": None, "valid": 0\}', breadth_func_new, content, flags=re.DOTALL)

old_r1_r5 = """r1, r2, r3, r4, r5 = st.columns(5)
with r1:
    vsub = ("\\U0001F6A8 PANIC - achats satellites geles" if vix_panic
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
        rsub = ("\\U0001F534 SOUS SMA200 - DCA agressif" if crash
                else "\\U0001F7E2 SUR SMA200 - DCA standard")
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
                  "portefeuille). S'il est depasse, le bot refuse de nouveaux "
                  "achats individuels.",
    ), unsafe_allow_html=True)
with r5:
    c_acc = "red" if max_sector_val >= _MAX_SECTOR * 100 else "cyan"
    c_sub = "sub-red" if max_sector_val >= _MAX_SECTOR * 100 else "sub-muted"
    st.markdown(metric_box(
        "Concentration Secteur (Max)", f"{max_sector_val:.1f}%",
        sub=f"{max_sector} (cap {_MAX_SECTOR*100:.0f}%)",
        accent=c_acc, sub_cls=c_sub,
        help_text="Le secteur le plus lourd du portefeuille. S'il depasse le "
                  "plafond, le bot rejettera toute opportunite dans ce meme secteur.",
    ), unsafe_allow_html=True)"""

new_r1_r5 = """r1, r2, r3, r4, r5 = st.columns(5)
with r1:
    vsub = ("\\U0001F6A8 PANIC - achats satellites geles" if vix_panic else f"Calme (seuil {_VIX_PANIC:.0f})")
    with st.popover(f"VIX | {vix:.1f}", use_container_width=True):
        st.markdown(metric_box(
            "Volatilite (VIX)", f"{vix:.1f}", sub=vsub,
            accent="red" if vix_panic else "", sub_cls="sub-red" if vix_panic else "sub-green",
        ), unsafe_allow_html=True)
        vix_hist = _db_hist("^V2TX", 30)
        if not vix_hist.empty:
            fig = pex.line(vix_hist, x="Date", y="Close", title="VIX 30-Day History")
            fig.update_layout(margin=dict(l=0, r=0, t=30, b=0), height=200, plot_bgcolor=_BG, paper_bgcolor=_BG, font=dict(color=_WHITE))
            st.plotly_chart(fig, use_container_width=True)

with r2:
    if regime:
        crash = regime["crash"]
        rsub = ("\\U0001F534 SOUS SMA200" if crash else "\\U0001F7E2 SUR SMA200")
        with st.popover(f"Regime | {regime['gap_pct']:+.1f}%", use_container_width=True):
            st.markdown(metric_box(
                f"Regime Core ({_CORE_TICKER})", f"{regime['gap_pct']:+.1f}%", sub=rsub,
                accent="red" if crash else "", sub_cls="sub-red" if crash else "sub-green",
            ), unsafe_allow_html=True)
    else:
        st.markdown(metric_box(f"Regime Core ({_CORE_TICKER})", "n/a", sub="Donnees indisponibles", accent="muted", sub_cls="sub-muted"), unsafe_allow_html=True)

with r3:
    breadth_val = f"{_pct50_f:.0f}% / {_pct200_f:.0f}%" if _pct200_f is not None else "n/a"
    with st.popover(f"Breadth | {breadth_val}", use_container_width=True):
        st.markdown(metric_box(
            "Market Breadth (SMA50/200)", breadth_val,
            sub=f"{int(_valid)} titres", accent=_breadth_accent, sub_cls=_breadth_sub_cls,
        ), unsafe_allow_html=True)
        st.markdown("### Stocks > SMA200")
        list_200 = _breadth.get("list_200", [])
        if list_200:
            st.dataframe(pd.DataFrame({"Ticker": list_200}), hide_index=True, use_container_width=True)

with r4:
    over = sat_used_pct > 100
    ssub = f"{satellite_value:,.0f} / {sat_budget_eur:,.0f} \\u20ac"
    with st.popover(f"Sat | {sat_used_pct:.0f}%", use_container_width=True):
        st.markdown(metric_box(
            "Budget Satellite", f"{sat_used_pct:.0f}%", sub=ssub,
            accent="red" if over else "cyan", sub_cls="sub-red" if over else "sub-muted",
        ), unsafe_allow_html=True)

with r5:
    c_acc = "red" if max_sector_val >= _MAX_SECTOR * 100 else "cyan"
    c_sub = "sub-red" if max_sector_val >= _MAX_SECTOR * 100 else "sub-muted"
    with st.popover(f"Sector | {max_sector_val:.1f}%", use_container_width=True):
        st.markdown(metric_box(
            "Concentration Secteur (Max)", f"{max_sector_val:.1f}%",
            sub=f"{max_sector}", accent=c_acc, sub_cls=c_sub,
        ), unsafe_allow_html=True)
        if sector_weights:
            pie_df = pd.DataFrame(list(sector_weights.items()), columns=["Sector", "Value"])
            fig = pex.pie(pie_df, names="Sector", values="Value", hole=0.4)
            fig.update_layout(margin=dict(l=0, r=0, t=0, b=0), height=250, plot_bgcolor=_BG, paper_bgcolor=_BG, font=dict(color=_WHITE))
            st.plotly_chart(fig, use_container_width=True)
"""
content = content.replace(old_r1_r5, new_r1_r5)

# Replace single-line expanders with standard markdown/containers
# Using regex to catch any single line expander blocks that are simple
content = re.sub(r'with st\.expander\("Voir les sources \(Newsletters\)", expanded=False\):', 'if True:', content)
content = re.sub(r'with st\.expander\("([^"]+)", expanded=False\):', r'if True:\n        st.markdown("### \1")', content)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
