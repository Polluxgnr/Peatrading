import codecs

with codecs.open('05_interfaces/terminal_dashboard.py', 'r', encoding='utf-8') as f:
    text = f.read()

news_start = text.find('    # 2. Global News Terminal')
news_end = text.find('    st.markdown("---")\r\n    st.markdown("### 🚀 Top Opportunities')
print('News Start:', news_start, 'News End:', news_end)

dd_start = text.find('@st.cache_data(ttl=900, show_spinner=False)\r\ndef fetch_ticker_info')
dd_end = text.find('with tab_quant_engine:')
print('DD Start:', dd_start, 'DD End:', dd_end)
