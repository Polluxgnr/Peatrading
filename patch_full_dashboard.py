import codecs

with codecs.open('05_interfaces/terminal_dashboard.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Replace Global News Terminal
import re
news_start = text.find('    # 2. Global News Terminal')
news_end = text.find('    st.markdown("---")\n    st.markdown("### 🚀 Top Opportunities & Momentum Leaders")')

new_news = """    # 2. Global News Terminal
    st.markdown("### 📰 Global News Terminal")
    
    news_filter = st.radio("News Filter", ["All News", "High Impact Only", "Bullish", "Bearish"], horizontal=True)
    
    try:
        db = get_portfolio_db()
        news_items = db.get_news_history(limit=100)
        
        if not news_items:
            st.info("Data lake is empty. Waiting for daemon to ingest news.")
        else:
            filtered_news = []
            for r in news_items:
                score = float(r.get("sentiment_score") or 0)
                if news_filter == "High Impact Only" and abs(score) < 0.5:
                    continue
                if news_filter == "Bullish" and score < 0.2:
                    continue
                if news_filter == "Bearish" and score > -0.2:
                    continue
                filtered_news.append(r)
                
            st.caption(f"Showing {len(filtered_news)} articles matching filter.")
            
            with st.container(height=600):
                for r in filtered_news:
                    score = float(r.get("sentiment_score") or 0)
                    if score > 0.2:
                        badge_col = _NEON
                        badge_txt = "BULLISH"
                    elif score < -0.2:
                        badge_col = _RED
                        badge_txt = "BEARISH"
                    else:
                        badge_col = _MUTED
                        badge_txt = "NEUTRAL"
                        
                    source = r.get("source", "Unknown")
                    title = r.get("title", "No Title")
                    ticker = r.get("ticker", "MACRO")
                    date = str(r.get("published_at"))[:16]
                    url = r.get("url", "#")
                    
                    st.markdown(f'''
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
                    ''', unsafe_allow_html=True)
                    
    except Exception as e:
        st.error(f"Failed to load news: {e}")

"""

if news_start != -1 and news_end != -1:
    text = text[:news_start] + new_news + text[news_end:]

# Replace Deep Dive
dd_start = text.find('@st.cache_data(ttl=900, show_spinner=False)\ndef fetch_ticker_info')
dd_end = text.find('with tab_quant_engine:')

new_dd = """@st.cache_data(ttl=86400, show_spinner=False)
def get_company_info_cached(ticker: str) -> dict:
    try:
        import yfinance as yf
        return yf.Ticker(ticker).info or {}
    except Exception:
        return {}

with tab_ticker_deep_dive:
    st.markdown("## 🔍 Ticker Deep-Dive (Instant Terminal)")
    try:
        tickers = universe_df["Ticker"].unique().tolist() if "universe_df" in globals() else []
    except Exception:
        tickers = []
        
    selected_ticker = st.selectbox("Search PEA Universe", options=tickers, index=0 if tickers else None)
    
    if selected_ticker:
        with st.spinner("⚡ Fetching Quant Data..."):
            try:
                info = get_company_info_cached(selected_ticker)
                name = info.get("longName", selected_ticker)
                sector = info.get("sector", "N/A")
                industry = info.get("industry", "N/A")
                country = info.get("country", "N/A")
                summary = info.get("longBusinessSummary", "")
                
                col_info_left, col_info_right = st.columns([0.4, 0.6])
                with col_info_left:
                    st.markdown(f"### {name}")
                    st.markdown(f"**🌍 Origin:** {country}")
                    st.markdown(f"**🏭 Sector:** {sector}")
                    st.markdown(f"**⚙️ Industry:** {industry}")
                with col_info_right:
                    trunc_summary = summary[:400] + "..." if len(summary) > 400 else summary
                    st.markdown(f"**📖 Business Summary:**<br>_{trunc_summary}_", unsafe_allow_html=True)
                st.markdown("---")
            except Exception as e:
                st.warning("Profile temporarily unavailable.")

            col_fun, col_rad = st.columns(2)
            with col_fun:
                st.markdown("### 📊 Fundamentals")
                try:
                    metrics = get_valuation_metrics(selected_ticker)
                    if metrics:
                        val_pe = metrics.get('pe_ratio')
                        val_pb = metrics.get('pb_ratio')
                        val_ret = metrics.get('return_1y')
                        if isinstance(val_pe, float): val_pe = f"{val_pe:.1f}"
                        if isinstance(val_pb, float): val_pb = f"{val_pb:.2f}"
                        if isinstance(val_ret, float): val_ret = f"{val_ret:.1f}%"
                        st.markdown(metric_box("P/E Ratio", str(val_pe)), unsafe_allow_html=True)
                        st.markdown(metric_box("P/B Ratio", str(val_pb)), unsafe_allow_html=True)
                        st.markdown(metric_box("Return 1Y", str(val_ret)), unsafe_allow_html=True)
                    else:
                        st.info("Metrics unavailable")
                except Exception:
                    st.info("Metrics unavailable")
                    
            with col_rad:
                st.markdown("### 🎯 Strategy Fingerprint")
                try:
                    fp = get_strategy_fingerprint(selected_ticker)
                    if fp:
                        fig = render_strategy_radar(fp, selected_ticker)
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.info("Fingerprint unavailable")
                except Exception:
                    st.info("Fingerprint unavailable")
                    
            st.markdown("---")
            
            st.markdown("### 📈 Price Action & Technicals (1Y)")
            try:
                import plotly.graph_objects as go
                hist = _db_hist(selected_ticker, 252)
                if hist is not None and not hist.empty:
                    fig = go.Figure(data=[go.Candlestick(
                        x=hist.index, open=hist['Open'], high=hist['High'],
                        low=hist['Low'], close=hist['Close'], name='Price'
                    )])
                    fig.update_layout(template="plotly_dark", margin=dict(t=10, b=10, l=10, r=10), height=400, xaxis_rangeslider_visible=False)
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("Chart data unavailable")
            except Exception:
                st.info("Chart data unavailable")
                
            st.markdown("---")
            
            col_ins, col_news = st.columns(2)
            with col_ins:
                st.markdown("### 👔 AMF Insider Activity")
                try:
                    df_insider = get_insider_data(selected_ticker)
                    if not df_insider.empty:
                        summary = summarize_insider_activity(df_insider)
                        sig_msg = summary.get("signal", "N/A")
                        tone = summary.get("tone", "muted")
                        color = _NEON if tone == "bullish" else (_RED if tone == "bearish" else _MUTED)
                        st.markdown(f"**Signal AMF:** <span style='color:{color}; font-weight:bold;'>{sig_msg}</span>", unsafe_allow_html=True)
                        st.dataframe(df_insider, use_container_width=True, hide_index=True)
                    else:
                        st.info("No insider activity recorded")
                except Exception:
                    st.info("Insider data unavailable")
                    
            with col_news:
                st.markdown("### 📰 Ticker-Specific News")
                try:
                    t_news = get_recent_news(selected_ticker, limit=5)
                    if t_news:
                        for n in t_news:
                            render_news_card(selected_ticker, n, n.get('sentiment_score'))
                    else:
                        st.info("No specific news available")
                except Exception:
                    st.info("News unavailable")

"""

if dd_start != -1 and dd_end != -1:
    text = text[:dd_start] + new_dd + "\n" + text[dd_end:]

with codecs.open('05_interfaces/terminal_dashboard.py', 'w', encoding='utf-8') as f:
    f.write(text)

print("success")
