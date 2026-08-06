"""Async web scraper for Institutional Holdings.

Fetches the top holdings of major European indices (CAC 40, Euro Stoxx 50)
from public sources (like Wikipedia) to establish the dynamic institutional consensus.
"""

import asyncio
import logging
from datetime import datetime, timezone
import sys
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT / "01_memory_core"))

try:
    from sqlite_portfolio import PortfolioDB
except ImportError:
    PortfolioDB = None

logger = logging.getLogger(__name__)

async def fetch_cac40_components(session: aiohttp.ClientSession) -> list[dict]:
    """Scrape CAC 40 components from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/CAC_40"
    holdings = []
    now = datetime.now(timezone.utc).isoformat()
    try:
        async with session.get(url, timeout=10.0) as response:
            if response.status != 200:
                logger.error(f"Failed to fetch CAC 40: HTTP {response.status}")
                return holdings
            html = await response.text()
            soup = BeautifulSoup(html, "html.parser")
            table = soup.find("table", {"id": "constituents"})
            if not table:
                return holdings
            
            for row in table.find_all("tr")[1:]:  # Skip header
                cols = row.find_all(["th", "td"])
                if len(cols) >= 3:
                    company = cols[0].get_text(strip=True)
                    ticker_raw = cols[2].get_text(strip=True)
                    # Convert EPA:XXX to XXX.PA
                    if ticker_raw.startswith("EPA:"):
                        ticker = ticker_raw.split(":")[1].strip() + ".PA"
                    else:
                        ticker = ticker_raw + ".PA"
                    
                    holdings.append({
                        "ticker": ticker,
                        "company_name": company,
                        "fund_source": "CAC 40",
                        "weight_pct": 1.0,  # Unweighted for now
                        "updated_at": now
                    })
    except Exception as e:
        logger.exception(f"Error scraping CAC 40: {e}")
    return holdings


async def fetch_eurostoxx50_components(session: aiohttp.ClientSession) -> list[dict]:
    """Scrape Euro Stoxx 50 components from Wikipedia."""
    url = "https://en.wikipedia.org/wiki/EURO_STOXX_50"
    holdings = []
    now = datetime.now(timezone.utc).isoformat()
    try:
        async with session.get(url, timeout=10.0) as response:
            if response.status != 200:
                logger.error(f"Failed to fetch Euro Stoxx 50: HTTP {response.status}")
                return holdings
            html = await response.text()
            soup = BeautifulSoup(html, "html.parser")
            table = soup.find("table", {"id": "constituents"})
            if not table:
                return holdings
            
            for row in table.find_all("tr")[1:]:
                cols = row.find_all(["td"])
                if len(cols) >= 3:
                    ticker_raw = cols[0].get_text(strip=True)
                    company = cols[1].get_text(strip=True)
                    # Simple heuristic: add .PA if missing (Eurostoxx has many markets, 
                    # but usually we can map them. For simplicity, we keep original if it has dot,
                    # or append .PA/.AS/.DE manually based on some heuristic, or just save as is).
                    # Actually Wikipedia Eurostoxx50 tickers are often just symbols without suffix.
                    ticker = ticker_raw
                    # Attempt a naive mapping
                    if "France" in html: # Not reliable per row without reading the country column
                        pass
                    
                    # For now, just save the raw ticker if we don't have a robust mapping.
                    # In a real setup, we'd map via ISIN or an explicit dictionary.
                    # Or we just assume the user configures the universe and we intersect.
                    holdings.append({
                        "ticker": ticker,
                        "company_name": company,
                        "fund_source": "Euro Stoxx 50",
                        "weight_pct": 1.0,
                        "updated_at": now
                    })
    except Exception as e:
        logger.exception(f"Error scraping Euro Stoxx 50: {e}")
    return holdings

async def run_institutional_sync() -> None:
    """Run all scrapers and persist to DB."""
    logger.info("Starting institutional holdings sync...")
    async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"}) as session:
        tasks = [
            fetch_cac40_components(session),
            fetch_eurostoxx50_components(session)
        ]
        results = await asyncio.gather(*tasks)
    
    all_holdings = []
    for r in results:
        all_holdings.extend(r)
        
    if all_holdings and PortfolioDB is not None:
        db = PortfolioDB()
        db.init_db()
        db.save_institutional_holdings(all_holdings)
        logger.info(f"Successfully synced {len(all_holdings)} institutional holdings.")
    else:
        logger.warning("No holdings found or PortfolioDB not available.")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_institutional_sync())
