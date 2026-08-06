import logging
import math

logger = logging.getLogger(__name__)

def calculate_smart_limit_price(ticker: str, current_price: float, atr_14: float, direction: str = "BUY") -> float:
    """
    Calculates a smart limit price maximizing fill probability while avoiding chasing spikes.
    
    Args:
        ticker: The stock ticker.
        current_price: The latest known closing price or mid price.
        atr_14: The 14-day Average True Range.
        direction: "BUY" or "SELL".
        
    Returns:
        The suggested limit price rounded to 2 decimal places (Euronext tick rules proxy).
    """
    if current_price <= 0:
        logger.warning(f"Invalid current_price {current_price} for {ticker}")
        return current_price
        
    if atr_14 < 0:
        logger.warning(f"Invalid negative ATR {atr_14} for {ticker}, defaulting to 0.")
        atr_14 = 0.0

    direction = str(direction).strip().upper()
    
    if direction == "BUY":
        # Do not pay more than +0.2% or +15% of ATR, whichever is lower
        limit_px = min(current_price * 1.002, current_price + 0.15 * atr_14)
    elif direction == "SELL":
        # Do not sell for less than -0.2% or -15% of ATR, whichever is lower
        limit_px = max(current_price * 0.998, current_price - 0.15 * atr_14)
    else:
        logger.warning(f"Unknown direction '{direction}' for {ticker}, defaulting to current_price.")
        limit_px = current_price
        
    # Euronext typically rounds to 2 or 3 decimals depending on the asset price.
    # We round to 2 decimals for general liquidity on PEA stocks.
    return round(limit_px, 2)
