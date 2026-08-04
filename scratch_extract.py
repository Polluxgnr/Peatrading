import ast
import astor

with open('c:/Users/PolluxGronier/Downloads/pea_sniper_terminal/05_interfaces/terminal_dashboard.py', 'r', encoding='utf-8') as f:
    source = f.read()

tree = ast.parse(source)
funcs = ['get_fundamental_metrics', 'get_deep_news_synthesis', '_fetch_news_from_apis', '_french_dossier_summary', 'get_ticker_dossier']

extracted = []
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in funcs:
        node.decorator_list = []
        extracted.append(astor.to_source(node))

header = """\"\"\"Profile builder logic extracted from dashboard for Night Run.\"\"\"
import sys
import logging
import json
from pathlib import Path
from datetime import datetime, timezone

_ROOT = Path(__file__).resolve().parent.parent

if str(_ROOT / "01_memory_core") not in sys.path:
    sys.path.insert(0, str(_ROOT / "01_memory_core"))
from sqlite_portfolio import PortfolioDB, get_portfolio_db
from duckdb_manager import get_ts_db

if str(_ROOT / "04_orchestrator_ai") not in sys.path:
    sys.path.insert(0, str(_ROOT / "04_orchestrator_ai"))
from llm_explainer import NarrativeExplainer

import yfinance as yf
_CORE_TICKER = "CW8.PA"

def short_name(ticker: str) -> str:
    return ticker.split(".")[0]

def format_name(ticker: str) -> str:
    return ticker

def get_valuation_metrics(ticker: str) -> dict:
    return {}

def build_and_save_ticker_profile(ticker: str, include_llm: bool = False) -> dict:
    db = get_portfolio_db()
    dossier_data = get_ticker_dossier(ticker)
    fmeta = get_fundamental_metrics(ticker)
    ts_db = get_ts_db()
    ohlcv_df = ts_db.get_historical_prices(ticker, days=30)
    if ohlcv_df is not None and not ohlcv_df.empty:
        ohlcv = json.loads(ohlcv_df.to_json(orient='records', date_format='iso'))
    else:
        ohlcv = []
        
    news_items = _fetch_news_from_apis(ticker, limit=12)
    headlines = tuple(str(n.get("title") or "").strip() for n in news_items if str(n.get("title") or "").strip())
    
    if include_llm:
        try:
            synth = get_deep_news_synthesis(ticker, headlines[:15])
        except Exception as e:
            synth = f"Erreur Synthèse: {e}"
    else:
        synth = "Synthèse non générée. Cliquez sur 'Générer Synthèse IA' pour l'analyser."
        
    new_prof = {
        "ticker": ticker,
        "dossier": dossier_data,
        "fundamentals": fmeta,
        "ohlcv": ohlcv,
        "synthesis": synth,
        "news_count": len(headlines)
    }
    db.upsert_ticker_profile(ticker, new_prof)
    return new_prof

"""

with open('c:/Users/PolluxGronier/Downloads/pea_sniper_terminal/01_memory_core/profile_builder.py', 'w', encoding='utf-8') as f:
    f.write(header + '\n'.join(extracted))
