import codecs
import re

with codecs.open('05_interfaces/terminal_dashboard.py', 'r', encoding='utf-8') as f:
    text = f.read().replace('\r\n', '\n')

# Find HARDCODED_PROFILES block and get_company_info
start_idx = text.find('HARDCODED_PROFILES = {')
end_idx = text.find('with tab_ticker_deep_dive:')

new_block = '''def get_company_info(ticker: str) -> dict:
    try:
        import sqlite3
        db = get_portfolio_db()
        with db._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name, sector, industry, country, summary FROM ticker_profiles WHERE ticker = ?", (ticker,))
            row = cursor.fetchone()
            if row:
                return {
                    "longName": row["name"] if "name" in row.keys() else row[0],
                    "sector": row["sector"] if "sector" in row.keys() else row[1],
                    "industry": row["industry"] if "industry" in row.keys() else row[2],
                    "country": row["country"] if "country" in row.keys() else row[3],
                    "longBusinessSummary": row["summary"] if "summary" in row.keys() else row[4]
                }
    except Exception as e:
        pass
        
    return {
        "longName": ticker,
        "sector": "Inconnu",
        "industry": "Inconnu",
        "country": "Europe",
        "longBusinessSummary": "Description non disponible en base."
    }

'''
if start_idx != -1 and end_idx != -1:
    text = text[:start_idx] + new_block + text[end_idx:]

with codecs.open('05_interfaces/terminal_dashboard.py', 'w', encoding='utf-8') as f:
    f.write(text)

