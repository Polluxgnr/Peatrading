"""Profile builder logic extracted from dashboard for Night Run."""
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

def get_fundamental_metrics(ticker: str) ->dict:
    """PE/PB/ROE/Debt-Equity from SQLite cache -> Finnhub -> yfinance fallback."""
    out = {'pe_ratio': None, 'pb_ratio': None, 'roe': None,
        'debt_to_equity': None, 'source': 'none'}
    if not ticker:
        return out
    try:
        db = get_portfolio_db()
        db.init_db()
        cached = db.get_cached_fundamentals(ticker, max_age_days=7)
        if cached:
            return {'pe_ratio': cached.get('pe_ratio'), 'pb_ratio': cached.
                get('pb_ratio'), 'roe': cached.get('roe'), 'debt_to_equity':
                cached.get('debt_to_equity'), 'source': cached.get('source'
                ) or 'sqlite_cache'}
    except Exception:
        pass
    try:
        sensors_dir = _ROOT / '00_data_sensors'
        if str(sensors_dir) not in sys.path:
            sys.path.insert(0, str(sensors_dir))
        from fundamentals_api import FundamentalsSensor
        live = FundamentalsSensor().get_basic_financials(ticker) or {}
        payload = {'pe_ratio': live.get('pe_ratio'), 'pb_ratio': live.get(
            'pb_ratio'), 'roe': live.get('roe'), 'debt_to_equity': live.get
            ('debt_to_equity'), 'source': live.get('source') or 'none'}
        if any(payload.get(k) is not None for k in ('pe_ratio', 'pb_ratio',
            'roe', 'debt_to_equity')):
            try:
                db = get_portfolio_db()
                db.init_db()
                db.upsert_fundamentals(ticker, payload)
            except Exception:
                pass
            return payload
    except Exception:
        pass
    val = get_valuation_metrics(ticker) or {}
    return {'pe_ratio': val.get('trailing_pe'), 'pb_ratio': val.get(
        'price_to_book'), 'roe': None, 'debt_to_equity': None, 'source':
        'valuation_fallback'}

def get_deep_news_synthesis(ticker: str, headlines: tuple[str, ...]) ->str:
    """Alias used by Exploration (same 24h cache key family as analysis)."""
    return get_deep_news_analysis(ticker, headlines)

def _fetch_news_from_apis(symbol: str, limit: int=6) ->list[dict]:
    """Fetch diverse news from live APIs (Boursorama + Google + Yahoo)."""
    collected: list[dict] = []
    seen_titles: set[str] = set()

    def _push(title: str, link: str, date: str, provider: str) ->None:
        import re
        key = (title or '').strip().casefold()
        if not key or key in seen_titles:
            return
        if key.startswith('http://') or key.startswith('https://'
            ) or key.startswith('http'):
            return
        spam_pattern = re.compile(
            r"(?i)(discount|free|referral|rewards|newsletter|email|sponsor|pitch deck|vc|substack|attio|seo agency|gtm|seed|founder|startup|saas|cap table|récompense|mettre [aà] jour|update your|unsubscribe|cliquez ici|abonnez-vous|subscribe|webinar|masterclass|lifestyle|promo|offre|gift|cadeau|bonus|vip|exclusive|limited time|last chance)"
            )
        if spam_pattern.search(key):
            return
        seen_titles.add(key)
        pub = (date or '').strip()
        if not pub or pub.lower() == 'recent':
            pub = datetime.now().strftime('%Y-%m-%d %H:%M')
        collected.append({'title': title.strip(), 'link': link or '#',
            'date': pub, 'provider': provider})
    try:
        scrapers_dir = _ROOT / '00_data_sensors' / 'scrapers'
        if str(scrapers_dir) not in sys.path:
            sys.path.insert(0, str(scrapers_dir))
        from bourso_scraper import BoursoramaScraper
        profile = BoursoramaScraper().get_instrument_profile(symbol)
        items = (profile or {}).get('news_items') or []
        if items:
            sentiment = (profile or {}).get('sentiment') or 'Unknown'
            elig = ','.join((profile or {}).get('eligibility') or []) or '?'
            for n in items:
                _push(n.get('title', ''), n.get('link') or '#', n.get(
                    'date') or '',
                    f"Boursorama · {n.get('provider') or 'local'} · sentiment {sentiment} · elig {elig}"
                    )
        else:
            bourso = BoursoramaScraper().get_retail_sentiment_and_news(symbol)
            headlines = (bourso or {}).get('news') or []
            sentiment = (bourso or {}).get('sentiment') or 'Unknown'
            for title in headlines:
                _push(title, '#', '', f'Boursorama · sentiment {sentiment}')
    except Exception:
        pass
    try:
        import urllib.parse
        import urllib.request
        import xml.etree.ElementTree as ET
        name = short_name(symbol)
        queries = [f'{symbol} OR {name} when:7d',
            f'{name} (bourse OR CAC OR PEA) when:7d',
            f'{name} site:lesechos.fr OR site:latribune.fr OR site:reuters.com when:14d'
            ]
        for q in queries:
            url = ('https://news.google.com/rss/search?' + urllib.parse.
                urlencode({'q': q, 'hl': 'fr', 'gl': 'FR', 'ceid': 'FR:fr'}))
            req = urllib.request.Request(url, headers={'User-Agent':
                'PEA-Pollux/1.0'})
            with urllib.request.urlopen(req, timeout=8) as resp:
                root = ET.fromstring(resp.read())
            for item in root.findall('.//item')[:8]:
                title = (item.findtext('title') or '').strip()
                link = (item.findtext('link') or '#').strip()
                pub = (item.findtext('pubDate') or '')[:16]
                source = item.find('source')
                src = (source.text if source is not None else None
                    ) or 'Google News'
                _push(title, link, pub, f'Google News · {src}')
    except Exception:
        pass
    try:
        raw = yf.Ticker(symbol).news or []
        for n in raw:
            content = n.get('content', n)
            title = content.get('title') or n.get('title') or ''
            link = content.get('clickThroughUrl', {}).get('url'
                ) or content.get('canonicalUrl', {}).get('url') or n.get('link'
                ) or '#'
            date_str = content.get('pubDate') or content.get('displayTime'
                ) or ''
            provider = (content.get('provider') or {}).get('displayName', '')
            _push(title, link, (date_str or '')[:16], provider or
                'Yahoo Finance')
    except Exception:
        pass
    return collected[:limit]

def _french_dossier_summary(ticker: str, name: str, english: str) ->str:
    """Translate/compress Yahoo longBusinessSummary to 3 short FR sentences.

    Falls back to the English snippet if OpenRouter is unavailable — never blocks.
    """
    text = (english or '').strip()
    if not text:
        return ''
    fr_markers = ' est ', ' une ', ' des ', ' société', ' groupe', ' dans '
    if sum(1 for m in fr_markers if m in text.casefold()) >= 2:
        return text[:700]
    api_key = None
    try:
        import os
        api_key = os.getenv('OPENROUTER_API_KEY')
    except Exception:
        api_key = None
    if not api_key:
        return text[:700]
    try:
        from llm_explainer import openrouter_chat
        prompt = f"""Traduis et synthétise en français, exactement 3 phrases courtes, le profil de {name} ({ticker}) pour un investisseur PEA. Pas de blabla, pas d'anglais.

{text[:1200]}"""
        out = asyncio.run(openrouter_chat([{'role': 'system', 'content':
            'Tu es un rédacteur financier FR concis.'}, {'role': 'user',
            'content': prompt}], api_key=api_key, max_tokens=220,
            temperature=0.2))
        cleaned = (out or '').strip()
        return cleaned[:700] if cleaned else text[:700]
    except Exception:
        return text[:700]

def get_ticker_dossier(ticker: str) ->dict:
    """Company identity + catalysts + risk events (yfinance + heuristics)."""
    out: dict = {'name': format_name(ticker), 'summary': '', 'sector': '',
        'industry': '', 'catalysts': [], 'risk_events': [], 'is_etf': False,
        'fundamentals': {}}
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        info = {}
    name = info.get('longName') or info.get('shortName') or short_name(ticker)
    out['name'] = name
    out['sector'] = str(info.get('sector') or '')
    out['industry'] = str(info.get('industry') or '')
    summary = str(info.get('longBusinessSummary') or '')[:700]
    quote_type = str(info.get('quoteType') or '').upper()
    out['is_etf'] = quote_type in ('ETF', 'MUTUALFUND') or ticker.endswith(
        '.PA') and ('ETF' in name.upper() or 'UCITS' in name.upper() or 
        ticker == _CORE_TICKER)
    if summary:
        out['summary'] = _french_dossier_summary(ticker, name, summary)
    elif out['is_etf'] or ticker == _CORE_TICKER:
        out['summary'] = (
            f"{name} est un ETF eligible PEA. Il replique un indice large (ex. MSCI World pour CW8) au lieu d'un risque entreprise unique. C'est l'ancre Core du systeme PEA Pollux."
            )
    else:
        out['summary'] = (
            f"{format_name(ticker)} — fiche qualitative incomplete cote Yahoo. Consulte Boursorama / le document d'enregistrement universel."
            )
    sector = (out['sector'] or '').casefold()
    catalysts = ['Publication de resultats au-dessus du consensus (EPS / CA)',
        'Guidance relevee ou nouveau contrat significatif',
        "Rachat d'actions / dividende en hausse"]
    risks = ['Profit warning ou baisse de guidance',
        'Enquete regulateur / amende majeure',
        'Choc macro (VIX panic) pendant que tu es concentre sur 1 ligne']
    if 'auto' in sector or 'consumer cyclical' in sector or 'STLAP' in ticker:
        catalysts += ['Rebond volumes Europe/US',
            'Marges industrielles stabilisees']
        risks += ['Guerre commerciale / droits de douane',
            'Retard plateformes EV']
    if 'healthcare' in sector or 'SAN.PA' in ticker:
        catalysts += ['Approbation medicament / pipeline']
        risks += ['Echec essai clinique', 'Pression prix medicaments']
    if out['is_etf'] or ticker == _CORE_TICKER:
        catalysts = ['Marche actions mondial en tendance haussiere',
            'DCA discipliné pendant les corrections (Smart DCA)',
            "Euro stable vs panier devise de l'indice"]
        risks = ['Krach global prolonge (mais le DCA achete alors plus fort)',
            "Tracking error / frais de l'ETF",
            "Force de l'euro qui pese sur un indice world en devises"]
    out['catalysts'] = catalysts[:5]
    out['risk_events'] = risks[:5]
    try:
        out['fundamentals'] = get_fundamental_metrics(ticker)
    except Exception:
        out['fundamentals'] = {}
    return out
