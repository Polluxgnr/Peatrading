import requests
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "01_memory_core"))

from logging_setup import get_logger

logger = get_logger("discord_notifier")

# Colors
COLOR_GREEN = 59006
COLOR_RED = 16726832

def send_high_conviction_alert(signal_dict: dict, webhook_url: str):
    """
    Send a high conviction signal alert to Discord via Webhook.
    
    Args:
        signal_dict: Dictionary containing signal metadata:
            - ticker
            - direction (BUY/SELL)
            - score
            - current_price
            - atr_stop_loss
            - llm_reasoning (optional)
        webhook_url: The Discord Webhook URL.
    """
    if not webhook_url:
        logger.warning("No Discord Webhook URL provided. Skipping alert.")
        return
        
    ticker = signal_dict.get("ticker", "UNKNOWN")
    direction = signal_dict.get("direction", "BUY").upper()
    score = signal_dict.get("score", 0.0)
    current_price = signal_dict.get("current_price", 0.0)
    atr_stop = signal_dict.get("atr_stop_loss", 0.0)
    reasoning = signal_dict.get("llm_reasoning", "No LLM reasoning provided.")
    
    is_buy = direction == "BUY"
    color = COLOR_GREEN if is_buy else COLOR_RED
    title_emoji = "🟢" if is_buy else "🔴"
    
    embed = {
        "title": f"🚨 PEA Sniper Signal Alert: {title_emoji} {direction} {ticker}",
        "description": f"**High Conviction Signal Detected (>75%)**\n\n**LLM Guidance Insight:**\n*{reasoning}*",
        "color": color,
        "fields": [
            {
                "name": "📊 Model Confidence Score",
                "value": f"**{score:.1f}%**",
                "inline": True
            },
            {
                "name": "💰 Current Price",
                "value": f"**{current_price:.2f} €**",
                "inline": True
            },
            {
                "name": "🛡️ ATR Stop-Loss",
                "value": f"**{atr_stop:.2f} €**",
                "inline": True
            }
        ],
        "footer": {
            "text": "PEA Pollux Automated Orchestrator",
            "icon_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c3/Python-logo-notext.svg/1200px-Python-logo-notext.svg.png"
        }
    }
    
    payload = {
        "content": f"<@&EVERYONE> 🚨 {direction} Alert for **{ticker}**",
        "embeds": [embed]
    }
    
    try:
        response = requests.post(webhook_url, json=payload, timeout=10)
        if response.status_code in (200, 204):
            logger.info("Successfully sent Discord high-conviction alert for %s", ticker)
        else:
            logger.warning("Failed to send Discord alert, status code: %s", response.status_code)
    except Exception as e:
        logger.exception("Error sending Discord alert: %s", e)
