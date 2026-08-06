import codecs

with codecs.open('05_interfaces/terminal_dashboard.py', 'r', encoding='utf-8') as f:
    text = f.read()

old_opp = \"\"\"    st.markdown("### ?? Top Opportunities & Momentum Leaders")
    try:
        budget = portfolio.cash_available if "portfolio" in globals() and portfolio else 10000.0
        vix_val = float(vix) if "vix" in globals() else 15.0
        
        col_opp, col_mom = st.columns(2)
        with col_opp:
            st.markdown("#### 🎯 Top Scored PEA Candidates")
            try:
                opps = rank_affordable_alternatives(budget, vix_val)
                if opps:
                    st.dataframe(pd.DataFrame(opps), use_container_width=True, hide_index=True)
                else:
                    st.caption("Module unavailable or no data.")
            except Exception as e:
                st.caption(f"Module unavailable or no data. ({e})")
                
        with col_mom:
            st.markdown("#### 🚀 High Momentum Leaders")
            try:
                moms = get_momentum_pepites()
                if moms:
                    st.dataframe(pd.DataFrame(moms), use_container_width=True, hide_index=True)
                else:
                    st.caption("Module unavailable or no data.")
            except Exception as e:
                st.caption(f"Module unavailable or no data. ({e})")
    except Exception as e:
        st.caption(f"Module unavailable or no data. ({e})")\"\"\"
        
new_opp = \"\"\"    st.markdown("### 🚀 Top Opportunities & Momentum Leaders")
    try:
        budget = portfolio.cash_available if "portfolio" in globals() and portfolio else 10000.0
        vix_val = float(vix) if "vix" in globals() else 15.0
        
        col_opp, col_mom = st.columns(2)
        with col_opp:
            st.markdown("#### 🎯 Top Scored PEA Candidates")
            try:
                opps = rank_affordable_alternatives(budget, vix_val)
                if opps:
                    df_opps = pd.DataFrame(opps).head(5)
                    st.dataframe(df_opps, use_container_width=True, hide_index=True)
                else:
                    st.info("Data unavailable")
            except Exception as e:
                st.info("Data unavailable")
                
        with col_mom:
            st.markdown("#### 🚀 High Momentum Leaders")
            try:
                moms = get_momentum_pepites(limit=5)
                if moms:
                    df_moms = pd.DataFrame(moms)
                    st.dataframe(df_moms, use_container_width=True, hide_index=True)
                else:
                    st.info("Data unavailable")
            except Exception as e:
                st.info("Data unavailable")
    except Exception as e:
        st.info("Data unavailable")\"\"\"
        
if old_opp in text:
    text = text.replace(old_opp, new_opp)
    with codecs.open('05_interfaces/terminal_dashboard.py', 'w', encoding='utf-8') as f:
        f.write(text)
    print("Fixed Top Opportunities")
else:
    print("Could not find the old block to replace.")
