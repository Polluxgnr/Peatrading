import codecs

path = '05_interfaces/terminal_dashboard.py'
with codecs.open(path, 'r', encoding='utf-8') as f:
    text = f.read()

# 1. Fix News Terminal
old_news = '''        # Get latest news from DB
        news_query = "SELECT ticker, published_at, title, url, sentiment_score, source FROM news_sentiment ORDER BY published_at DESC LIMIT 100"
        try:
            news_rows = db.execute(news_query).fetchall()
        except Exception:
            news_rows = []'''

new_news = '''        # Get latest news from DB
        import sqlite3
        import pandas as pd
        conn = sqlite3.connect("data/portfolio.db")
        try:
            news_query = "SELECT ticker, published_at, title, url, sentiment_score, source FROM news_master ORDER BY published_at DESC LIMIT 50"
            news_df = pd.read_sql(news_query, conn)
        except sqlite3.OperationalError:
            try:
                news_query = "SELECT ticker, published_at, title, url, sentiment_score, source FROM news_history ORDER BY published_at DESC LIMIT 50"
                news_df = pd.read_sql(news_query, conn)
            except sqlite3.OperationalError:
                news_df = pd.DataFrame()
        finally:
            conn.close()
            
        news_rows = news_df.to_dict("records") if not news_df.empty else []'''

text = text.replace(old_news, new_news)

old_info = '''        if not news_rows:
            st.info("No recent news found in database.")'''
new_info = '''        if not news_rows:
            st.info("Data lake is empty. Waiting for daemon to ingest news.")'''
text = text.replace(old_info, new_info)

# 2. Add Top Opportunities & Momentum Leaders to the end of tab_market_pulse
old_pulse_end = '''    except Exception as e:
        st.error(f"Failed to load news: {e}")


with tab_ticker_deep_dive:'''

new_pulse_end = '''    except Exception as e:
        st.error(f"Failed to load news: {e}")

    st.markdown("---")
    st.markdown("### 🚀 Top Opportunities & Momentum Leaders")
    try:
        budget = portfolio.cash_available if "portfolio" in globals() and portfolio else 10000.0
        vix_val = float(vix) if "vix" in globals() else 15.0
        
        col_opp, col_mom = st.columns(2)
        with col_opp:
            st.markdown("#### 🎯 Top Scored PEA Candidates")
            opps = rank_affordable_alternatives(budget, vix_val)
            if opps:
                st.dataframe(pd.DataFrame(opps), use_container_width=True, hide_index=True)
            else:
                st.caption("Module unavailable or no data.")
                
        with col_mom:
            st.markdown("#### 🚀 High Momentum Leaders")
            moms = get_momentum_pepites()
            if moms:
                st.dataframe(pd.DataFrame(moms), use_container_width=True, hide_index=True)
            else:
                st.caption("Module unavailable or no data.")
    except Exception as e:
        st.caption(f"Module unavailable or no data. ({e})")


with tab_ticker_deep_dive:'''

text = text.replace(old_pulse_end, new_pulse_end)

# 3. Restore AMF Insiders & Fundamentals in Ticker Deep-Dive
old_deep_dive_end = '''                # Raw Data
                with st.expander("📊 View Raw OHLCV & Feature Data (DuckDB)"):
                    st.dataframe(hist, use_container_width=True)
                    
            else:
                st.warning(f"No historical data found for {selected_ticker}.")
                
        except Exception as e:
            st.error(f"Failed to load ticker data: {e}")
    else:
        st.info("Select a ticker from the dropdown above to view details.")


with tab_quant_engine:'''

new_deep_dive_end = '''                # Raw Data
                with st.expander("📊 View Raw OHLCV & Feature Data (DuckDB)"):
                    st.dataframe(hist, use_container_width=True)
                    
            else:
                st.warning(f"No historical data found for {selected_ticker}.")
                
            st.markdown("---")
            st.markdown("### 🧬 Fundamental & Alternative Data")
            col_alt1, col_alt2 = st.columns(2)
            
            with col_alt1:
                st.markdown("#### 🎯 Quant Strategy Radar")
                try:
                    fingerprint = get_strategy_fingerprint(selected_ticker)
                    if fingerprint:
                        render_strategy_radar(fingerprint, selected_ticker)
                    else:
                        st.caption("Strategy radar data unavailable.")
                except Exception as e:
                    st.caption(f"Module unavailable or no data. ({e})")
                    
                st.markdown("#### 📊 Fundamentals & Valuation")
                try:
                    val_metrics = get_valuation_metrics(selected_ticker)
                    if val_metrics:
                        c1, c2 = st.columns(2)
                        with c1:
                            metric_box("P/E Ratio", val_metrics.get("pe_ratio", "N/A"))
                            metric_box("1M Return", f"{val_metrics.get('ret_1m', 0.0):.1%}")
                        with c2:
                            metric_box("P/B Ratio", val_metrics.get("pb_ratio", "N/A"))
                            metric_box("1Y Return", f"{val_metrics.get('ret_1y', 0.0):.1%}")
                    else:
                        st.caption("Fundamental data unavailable.")
                except Exception as e:
                    st.caption(f"Module unavailable or no data. ({e})")
                    
            with col_alt2:
                st.markdown("#### 🏛️ AMF Insider Flow")
                try:
                    insider_df = get_insider_data(selected_ticker)
                    if insider_df is not None and not insider_df.empty:
                        summary = summarize_insider_activity(insider_df)
                        st.markdown(f"**Activity Summary:** {summary.get('text', 'N/A')} (Score: {summary.get('score', 0)})")
                        st.dataframe(insider_df, use_container_width=True, hide_index=True)
                    else:
                        st.caption("No recent AMF insider activity reported.")
                except Exception as e:
                    st.caption(f"Module unavailable or no data. ({e})")
                
        except Exception as e:
            st.error(f"Failed to load ticker data: {e}")
    else:
        st.info("Select a ticker from the dropdown above to view details.")


with tab_quant_engine:'''

text = text.replace(old_deep_dive_end, new_deep_dive_end)

with codecs.open(path, 'w', encoding='utf-8') as f:
    f.write(text)
