import codecs
import re

path = 'README.md'
with codecs.open(path, 'r', encoding='utf-8', errors='ignore') as f:
    text = f.read()

idx = text.find('## Recent Updates (August 2026)')
if idx != -1:
    text = text[:idx]

new_text = '''
## Recent Updates (August 2026)
- **UI/UX Bloomberg Overhaul**: Streamlit interface restructured into 4 clean Workspaces (Market Pulse, Ticker Deep-Dive, Quant Engine, Portfolio & Ledger). Replaced deprecated width="stretch" with use_container_width=True on buttons.
- **Dependency & AWS Docker Fixes**: 
  - Pinned starlette<0.36.0 to resolve GZipResponder Streamlit crash on boot.
  - Purged pandas-ta library entirely and replaced it with native Pure Pandas indicators (SMA, RSI, MACD, BBands, ATR) to permanently resolve 
umpy 2.0 / scipy dependency conflicts.
  - Fixed syntax error in sqlite_portfolio.py caused by invalid docstring formatting.
'''

text += new_text.strip() + '\n'

with codecs.open(path, 'w', encoding='utf-8') as f:
    f.write(text)
