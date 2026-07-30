"""Discord Copilot Webhook for PEA Pollux.

Pushes trade alerts directly to Discord using a simple Webhook.
Replaces the old discord.py Client which had channel/intent issues.
Execution is manual via the Streamlit Dashboard.

.env requirements (config/api_keys.env):
    DISCORD_WEBHOOK_URL  - webhook for trade alerts.
    OPENROUTER_API_KEY   - used by NarrativeExplainer (optional; has fallback).
"""

import logging
import os
import sys
from pathlib import Path

import aiohttp
import json

try:
    _CORE = Path(__file__).resolve().parent.parent / "01_memory_core"
    sys.path.insert(0, str(_CORE))
    from env_loader import load_api_keys

    load_api_keys(Path(__file__).resolve().parent.parent / "config" / "api_keys.env")
except Exception:  # noqa: BLE001
    _env = Path(__file__).resolve().parent.parent / "config" / "api_keys.env"
    if _env.exists():
        with open(_env, "r", encoding="utf-8") as fh:
            for line in fh:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip(" '\""))

_INTERFACES_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.join(os.path.dirname(_INTERFACES_DIR), "01_memory_core")
sys.path.insert(0, _INTERFACES_DIR)
sys.path.insert(0, _CORE_DIR)

from data_models import PortfolioState, Signal, SignalStatus, SignalType  # noqa: E402
from llm_explainer import NarrativeExplainer  # noqa: E402

logger = logging.getLogger(__name__)

_GREEN = 59006
_RED = 16726832

class DiscordCopilot:
    """Aiohttp-based Discord webhook sender for PEA Pollux alerts."""

    def __init__(self) -> None:
        self.webhook_url = os.getenv("DISCORD_WEBHOOK_URL")

    async def send_signal_alert(
        self,
        signal: Signal,
        portfolio: PortfolioState,
        *,
        explainer: NarrativeExplainer | None = None,
        current_price: float = 0.0,
    ) -> None:
        """Post an embedded trade alert to the Discord webhook.

        Args:
            signal: The signal (BUY or SELL).
            portfolio: Current portfolio state.
            explainer: Optional LLM explainer for the narrative.
            current_price: The live ticker price.
        """
        if not self.webhook_url:
            logger.warning("DISCORD_WEBHOOK_URL not set; skipping alert.")
            return

        is_buy = signal.signal_type == SignalType.BUY
        color = _GREEN if is_buy else _RED
        title_emoji = "🟢" if is_buy else "🔴"
        notional = (signal.target_qty or 0) * current_price

        # Default narrative fallback
        narrative = f"{signal.reason}\n\n*Signal généré par l'algorithme.*"
        
        # LLM generated narrative
        if explainer is not None:
            try:
                narrative = await explainer.explain_trade(signal, portfolio)
            except Exception as exc:  # noqa: BLE001
                logger.error("LLM failed to explain %s: %s", signal.ticker, exc)

        embed = {
            "title": f"{title_emoji} NOUVEAU SIGNAL {signal.signal_type.value} : {signal.ticker}",
            "description": f"{narrative}\n\n*Signal généré par l'algorithme Quantitatif.*",
            "color": color,
            "fields": [
                {
                    "name": "📊 Score Technique",
                    "value": f"**{signal.score:.0f} / 100**",
                    "inline": True,
                },
                {
                    "name": "🎯 Quantité Cible",
                    "value": f"**{signal.target_qty}** actions",
                    "inline": True,
                },
                {
                    "name": "💰 Notional Estimé",
                    "value": f"**{notional:,.0f} €** (@ {current_price:.2f} €)",
                    "inline": True,
                },
                {
                    "name": "⚠️ Attention",
                    "value": "Ceci n'est pas un conseil en investissement.",
                    "inline": False,
                }
            ],
            "footer": {
                "text": "PEA Sniper Terminal • Validation manuelle requise via le Command Center",
                "icon_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c3/Python-logo-notext.svg/1200px-Python-logo-notext.svg.png"
            },
        }

        payload = {
            "content": f"<@&EVERYONE> 🚨 Opportunité PEA détectée sur **{signal.ticker}** !", 
            "embeds": [embed]
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.webhook_url, json=payload) as response:
                    if response.status not in (200, 204):
                        logger.error(f"Failed to post to webhook: {response.status}")
                    else:
                        logger.info("Discord Webhook alert sent for %s.", signal.ticker)
        except Exception as exc:
            logger.exception("Aiohttp webhook post failed for %s.", signal.ticker)
