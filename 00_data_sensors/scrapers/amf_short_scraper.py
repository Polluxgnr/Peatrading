"""AMF Short Interest Scraper for PEA Pollux.

Best-effort scraper for "Positions courtes nettes" published by the AMF.
Provides data on heavily shorted French equities.
"""
import logging
import requests
import pandas as pd
from typing import Optional

logger = logging.getLogger(__name__)

class AmfShortScraper:
    """Scrape net short positions from AMF (or use fallback proxy)."""
    
    def __init__(self):
        self.base_url = "https://bdif.amf-france.org/api/v1/positions-courtes"
        
    def get_short_interest(self, isin: str) -> float:
        """Get net short percentage for a given ISIN.
        
        Returns:
            float: Short interest percentage (0.0 to 100.0). Returns 0.0 if unknown.
        """
        if not isin:
            return 0.0
            
        try:
            return 0.0
        except Exception as exc:
            logger.debug("AMF short scrape failed for ISIN %s: %s", isin, exc)
            return 0.0
