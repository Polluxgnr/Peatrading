"""Deep News Scraper and RAG analyzer.

Extracts full text from news articles and passes them to a local LLM
(Ollama) to extract key financial metrics, forward guidance, and hidden risks.
"""

import asyncio
import json
import logging
from typing import Dict

import aiohttp
from bs4 import BeautifulSoup
import requests

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "mistral"

async def fetch_article_body(url: str) -> str:
    """Fetch the main text of a news article.

    Gracefully degrades to meta description or title if paywalled or anti-bot blocked.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    
    if not url or url.startswith("title:"):
        # Not a real URL, just a title placeholder
        return url.replace("title:", "")

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=8.0) as response:
                if response.status != 200:
                    logger.warning(f"Failed to fetch {url}: HTTP {response.status}")
                    return f"Failed to fetch full article (HTTP {response.status})."
                
                html = await response.text()
                soup = BeautifulSoup(html, "html.parser")
                
                # Try to find main article body
                article = soup.find("article")
                if article:
                    paragraphs = article.find_all("p")
                else:
                    paragraphs = soup.find_all("p")
                
                text = "\n".join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 40)
                
                if len(text) < 200:
                    # Fallback to meta description if content is too short (e.g. paywall)
                    meta_desc = soup.find("meta", {"name": "description"}) or soup.find("meta", {"property": "og:description"})
                    if meta_desc and meta_desc.get("content"):
                        text = f"Snippet: {meta_desc['content']}"
                    else:
                        text = "Content hidden behind paywall or anti-bot."
                        
                return text[:8000]  # Limit context size for LLM
    except Exception as e:
        logger.exception(f"Error extracting {url}")
        return f"Error extracting article: {str(e)}"

async def analyze_article_deep(url: str, text: str) -> Dict[str, str]:
    """Run full text through local LLM to extract financial insights."""
    
    prompt = f"""You are an expert Quant Analyst. Analyze the following news article.
URL: {url}
Article Text: {text}

Extract the following information and return ONLY a valid JSON object with EXACTLY these keys:
- "key_metrics": A string summarizing key financial figures mentioned (e.g. EPS, Revenue, Margins). If none, say "None mentioned."
- "guidance": A string summarizing the forward outlook, guidance, or strategic shifts.
- "hidden_risks": A string summarizing any risks, regulatory issues, or macro headwinds.

Do not include any markdown formatting around the JSON (like ```json), just output the raw JSON object.
"""
    
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json"
    }
    
    try:
        # Run synchronous requests in an executor to not block the event loop
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(None, lambda: requests.post(OLLAMA_URL, json=payload, timeout=30.0))
        
        if response.status_code == 200:
            data = response.json()
            response_text = data.get("response", "").strip()
            
            try:
                parsed = json.loads(response_text)
                return {
                    "key_metrics": parsed.get("key_metrics", "N/A"),
                    "guidance": parsed.get("guidance", "N/A"),
                    "hidden_risks": parsed.get("hidden_risks", "N/A"),
                }
            except json.JSONDecodeError:
                logger.error(f"Failed to parse LLM JSON: {response_text}")
                return {
                    "key_metrics": "Error parsing LLM response",
                    "guidance": "N/A",
                    "hidden_risks": "N/A",
                }
        else:
            logger.error(f"Ollama error: HTTP {response.status_code}")
            return {
                "key_metrics": f"Ollama unavailable (HTTP {response.status_code})",
                "guidance": "",
                "hidden_risks": "",
            }
    except Exception as e:
        logger.exception("Failed to analyze article with LLM.")
        return {
            "key_metrics": f"LLM Analysis failed: {e}",
            "guidance": "N/A",
            "hidden_risks": "N/A",
        }
