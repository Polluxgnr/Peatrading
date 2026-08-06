import codecs

with codecs.open('05_interfaces/terminal_dashboard.py', 'r', encoding='utf-8') as f:
    text = f.read()

idx_start = text.find('with tab_ticker_deep_dive:')
idx_end = text.find('with tab_quant_engine:')

new_tab = """
@st.cache_data(ttl=900, show_spinner=False)
def fetch_ticker_info(ticker: str) -> dict:
    import yfinance as yf
    try:
        info = yf.Ticker(ticker).info
        return info or {}
    except Exception:
        return {}

with tab_ticker_deep_dive:
    st.markdown("## 🔍 Ticker Deep-Dive (Instant Terminal)")
    
    # Universal Search & Quick Buttons
    try:
        tickers = universe_df["Ticker"].unique().tolist() if "universe_df" in globals() else []
    except Exception:
        tickers = []
        
    st.markdown("### ⚡ Quick Select")
    quick_tickers = ["AIR.PA", "MC.PA", "TTE.PA", "SAN.PA", "BNP.PA"]
    cols_qb = st.columns(len(quick_tickers))
    for i, qt in enumerate(quick_tickers):
        with cols_qb[i]:
            if st.button(qt, use_container_width=True):
                st.session_state["deep_dive_ticker"] = qt
                
    default_index = 0
    if st.session_state.get("deep_dive_ticker") in tickers:
        default_index = tickers.index(st.session_state["deep_dive_ticker"])
        
    selected_ticker = st.selectbox("Search PEA Universe", options=tickers, index=default_index if tickers else None)
    if selected_ticker:
        st.session_state["deep_dive_ticker"] = selected_ticker
        
        with st.spinner("⚡ Fetching Quant Data..."):
            # 1. Header & Corporate Profile
            try:
                info = fetch_ticker_info(selected_ticker)
                name = info.get("longName", selected_ticker)
                sector = info.get("sector", "Unknown Sector")
                industry = info.get("industry", "Unknown Industry")
                country = info.get("country", "")
                mcap = info.get("marketCap", 0)
                summary = info.get("longBusinessSummary", "No business summary available.")
                
                st.markdown(f"## {name} ({selected_ticker})")
                st.markdown(f"**{sector} | {industry} | {country} | MCap: {mcap:,.0f}**")
                st.caption(summary[:300] + "..." if len(summary) > 300 else summary)
                
                c1, c2, c3, c4, c5 = st.columns(5)
                with c1:
                    metric_box("P/E", f"{info.get('forwardPE', info.get('trailingPE', 'N/A'))}")
                with c2:
                    metric_box("P/B", f"{info.get('priceToBook', 'N/A')}")
                with c3:
                    yld = info.get("dividendYield")
                    metric_box("Div Yield", f"{yld*100:.2f}%" if yld else "N/A")
                with c4:
                    metric_box("EV/EBITDA", f"{info.get('enterpriseToEbitda', 'N/A')}")
                with c5:
                    h52 = info.get("fiftyTwoWeekHigh", 0)
                    l52 = info.get("fiftyTwoWeekLow", 0)
                    metric_box("52W H/L", f"{h52:.1f} / {l52:.1f}")
                    
            except Exception as e:
                st.caption(f"Profile unavailable: {e}")
                
            st.markdown("---")
            
            # 2. Interactive Price History & Technical Radar
            col_chart, col_radar = st.columns([0.7, 0.3])
            with col_chart:
                st.markdown("#### 📈 Price Action & Technicals (1Y)")
                try:
                    import plotly.graph_objects as go
                    import pandas as pd
                    import numpy as np
                    
                    hist = _db_hist(selected_ticker, 252)
                    if hist is not None and not hist.empty:
                        hist["SMA50"] = hist["Close"].rolling(50).mean()
                        hist["SMA200"] = hist["Close"].rolling(200).mean()
                        delta = hist["Close"].diff()
                        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
                        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
                        rs = gain / loss
                        hist["RSI"] = 100 - (100 / (1 + rs))
                        
                        fig = go.Figure(data=[go.Candlestick(
                            x=hist.index,
                            open=hist['Open'],
                            high=hist['High'],
                            low=hist['Low'],
                            close=hist['Close'],
                            name='Price'
                        )])
                        fig.add_trace(go.Scatter(x=hist.index, y=hist['SMA50'], mode='lines', name='SMA50', line=dict(color='cyan', width=1)))
                        fig.add_trace(go.Scatter(x=hist.index, y=hist['SMA200'], mode='lines', name='SMA200', line=dict(color='orange', width=1)))
                        
                        fig.update_layout(template="plotly_dark", margin=dict(t=10, b=10, l=10, r=10), height=350, xaxis_rangeslider_visible=False)
                        st.plotly_chart(fig, use_container_width=True)
                        
                        rsi_last = hist["RSI"].iloc[-1]
                        st.caption(f"RSI(14): {rsi_last:.1f} | SMA50: {hist['SMA50'].iloc[-1]:.1f} | SMA200: {hist['SMA200'].iloc[-1]:.1f}")
                    else:
                        st.warning("Historical data unavailable.")
                except Exception as e:
                    st.caption(f"Chart unavailable: {e}")
                    
            with col_radar:
                st.markdown("#### 🎯 Quant Radar")
                try:
                    fingerprint = get_strategy_fingerprint(selected_ticker)
                    if fingerprint:
                        render_strategy_radar(fingerprint, selected_ticker)
                    else:
                        st.caption("Radar data unavailable.")
                except Exception as e:
                    st.caption(f"Radar unavailable: {e}")
                    
            st.markdown("---")
            
            # 3. AI Synthesis & Multi-Scenario Future Theories
            st.markdown("#### 🧠 AI Synthesis & Future Scenarios")
            try:
                from technical_scorer import SignalGenerator
                from ml_feature_store import build_ml_feature_row
                from ml_trainer import predict_probability_with_shap
                from market_regime import MarketRegimeClassifier
                import numpy as np
                
                regime_obj = MarketRegimeClassifier().get_regime()
                feat_row = build_ml_feature_row(selected_ticker, close=float(hist["Close"].iloc[-1]) if 'hist' in locals() and not hist.empty else 0, reason="", pdb=None, offline_mode=False)
                prob, shap_vals, interval = predict_probability_with_shap(feat_row, horizon="tactical", regime=regime_obj)
                
                st.info("Algorithm dynamically assessing RSI, Momentum, Volatility, and XGBoost regime probabilities...")
                
                c_bull, c_base, c_bear = st.columns(3)
                
                with c_bull:
                    st.markdown(f"<div style='padding:15px; background:#0A1F0A; border-top:4px solid {_NEON}; border-radius:5px; height: 180px;'>"
                                f"<h4 style='color:{_NEON};'>🐂 Bull Thesis</h4>"
                                "<p style='font-size:13px; color:#CCC;'>Upside scenario driven by positive momentum, fundamental undervaluation, or strong institutional buying.</p>"
                                "</div>", unsafe_allow_html=True)
                                
                with c_base:
                    vol20 = hist["Close"].pct_change().rolling(20).std().iloc[-1] * np.sqrt(252) if 'hist' in locals() and not hist.empty else 0
                    st.markdown(f"<div style='padding:15px; background:#111; border-top:4px solid {_CYAN}; border-radius:5px; height: 180px;'>"
                                f"<h4 style='color:{_CYAN};'>⚖️ Quant Base</h4>"
                                f"<p style='font-size:13px; color:#CCC;'>ML Tactical Confidence: <b>{(prob or 0)*100:.1f}%</b><br>Historical Vol (20D ann): <b>{vol20*100:.1f}%</b><br>Expected 30D Range: ±{vol20/np.sqrt(12)*100:.1f}%</p>"
                                "</div>", unsafe_allow_html=True)
                                
                with c_bear:
                    st.markdown(f"<div style='padding:15px; background:#2A0A0A; border-top:4px solid {_RED}; border-radius:5px; height: 180px;'>"
                                f"<h4 style='color:{_RED};'>🐻 Bear Thesis</h4>"
                                "<p style='font-size:13px; color:#CCC;'>Downside scenario highlighting macro pressures, technical resistance, or deteriorating sentiment.</p>"
                                "</div>", unsafe_allow_html=True)
            except Exception as e:
                st.caption(f"AI Synthesis unavailable: {e}")
                
            st.markdown("---")
            
            # 4. News & Insider Flow
            col_news, col_insider = st.columns(2)
            
            with col_news:
                st.markdown("#### 📰 Ticker News & Sentiment")
                try:
                    import sqlite3
                    import pandas as pd
                    conn = sqlite3.connect("data/portfolio.db")
                    try:
                        n_query = "SELECT published_at, title, url, sentiment_score, source FROM news_master WHERE ticker = ? ORDER BY published_at DESC LIMIT 10"
                        n_df = pd.read_sql(n_query, conn, params=(selected_ticker,))
                    except sqlite3.OperationalError:
                        try:
                            n_query = "SELECT published_at, title, url, sentiment_score, source FROM news_history WHERE ticker = ? ORDER BY published_at DESC LIMIT 10"
                            n_df = pd.read_sql(n_query, conn, params=(selected_ticker,))
                        except sqlite3.OperationalError:
                            n_df = pd.DataFrame()
                    conn.close()
                    
                    if not n_df.empty:
                        agg_score = n_df['sentiment_score'].astype(float).mean()
                        agg_color = _NEON if agg_score > 0 else (_RED if agg_score < 0 else _MUTED)
                        st.markdown(f"**Aggregate Sentiment (30D):** <span style='color:{agg_color}; font-weight:bold;'>{agg_score:+.2f}</span>", unsafe_allow_html=True)
                        
                        with st.container(height=300):
                            for _, r in n_df.iterrows():
                                score = float(r["sentiment_score"] or 0)
                                if score > 0.2:
                                    bc, bt = _NEON, "BULL"
                                elif score < -0.2:
                                    bc, bt = _RED, "BEAR"
                                else:
                                    bc, bt = _MUTED, "NEUT"
                                    
                                date = str(r["published_at"])[:10]
                                st.markdown(f"""
                                <div style="margin-bottom:8px; padding-bottom:8px; border-bottom:1px solid #333;">
                                    <span style="color:{bc}; font-size:10px; border:1px solid {bc}; padding:1px 4px; border-radius:3px;">{bt}</span>
                                    <span style="color:#888; font-size:12px;"> {date} | {r['source']}</span><br>
                                    <a href="{r['url']}" target="_blank" style="color:#DDD; text-decoration:none; font-size:13px;">{r['title']}</a>
                                </div>
                                """, unsafe_allow_html=True)
                    else:
                        st.info("No recent news found for this ticker.")
                except Exception as e:
                    st.caption(f"News unavailable: {e}")
                    
            with col_insider:
                st.markdown("#### 🏛️ AMF Insider Flow")
                try:
                    insider_df = get_insider_data(selected_ticker)
                    if insider_df is not None and not insider_df.empty:
                        summary = summarize_insider_activity(insider_df)
                        st.markdown(f"**Activity Summary:** {summary.get('text', 'N/A')}")
                        st.dataframe(insider_df, use_container_width=True, hide_index=True)
                    else:
                        st.info("No recent AMF filings for this ticker.")
                except Exception as e:
                    st.caption(f"Insider flow unavailable: {e}")
                    
    else:
        st.info("Select a ticker from the dropdown above or use Quick Select to view details.")

with tab_quant_engine:
"""

text = text[:idx_start] + new_tab.lstrip() + text[idx_end + len("with tab_quant_engine:"):]

with codecs.open('05_interfaces/terminal_dashboard.py', 'w', encoding='utf-8') as f:
    f.write(text)
