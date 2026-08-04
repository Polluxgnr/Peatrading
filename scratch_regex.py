import re

with open('05_interfaces/terminal_dashboard.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace get_fundamental_metrics
content = re.sub(r'(@st\.cache_data[^\n]*\n)*def get_fundamental_metrics.*?return \{.*?\n    \}\n', '', content, flags=re.DOTALL)

# Replace get_deep_news_synthesis
content = re.sub(r'(@st\.cache_data[^\n]*\n)*def get_deep_news_synthesis.*?return f\"Erreur Synthèse: \{exc\}\"\n', '', content, flags=re.DOTALL)

# Replace _fetch_news_from_apis
content = re.sub(r'(@st\.cache_data[^\n]*\n)*def _fetch_news_from_apis.*?return collected\[:limit\]\n', '', content, flags=re.DOTALL)

# Replace _french_dossier_summary
content = re.sub(r'def _french_dossier_summary.*?return text\[:700\]\n', '', content, flags=re.DOTALL)

# Replace get_ticker_dossier
content = re.sub(r'def get_ticker_dossier.*?return out\n', '', content, flags=re.DOTALL)

with open('05_interfaces/terminal_dashboard.py', 'w', encoding='utf-8') as f:
    f.write(content)
