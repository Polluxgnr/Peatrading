import codecs

with codecs.open('05_interfaces/terminal_dashboard.py', 'r', encoding='utf-8') as f:
    text = f.read()

idx_start = text.find('with tab_ticker_deep_dive:')
idx_end = text.find('with tab_quant_engine:')

with codecs.open('new_deep_dive_v3.txt', 'r', encoding='utf-8') as f:
    new_tab = f.read()

text = text[:idx_start] + new_tab.lstrip() + text[idx_end + len('with tab_quant_engine:'):]

with codecs.open('05_interfaces/terminal_dashboard.py', 'w', encoding='utf-8') as f:
    f.write(text)
