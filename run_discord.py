"""Entry point to launch the PEA Sniper Terminal Discord Copilot.

Usage:
    1. Copy config/api_keys.env.example -> config/api_keys.env and fill in:
         DISCORD_TOKEN, DISCORD_CHANNEL_ID, OPENROUTER_API_KEY (optional)
    2. Run:  py run_discord.py

This starts the bot and keeps it connected. Actual signal alerts are pushed by
the scheduler (Phase 9) calling ``copilot.send_signal_alert(...)``. For a quick
manual smoke test, pass --demo to post one fake alert on ``on_ready``.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / "config" / "api_keys.env")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "05_interfaces"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "01_memory_core"))

from discord_copilot import DiscordCopilot  # noqa: E402
from llm_explainer import NarrativeExplainer  # noqa: E402
from sqlite_portfolio import PortfolioDB  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("run_discord")


def main() -> None:
    """Boot the Discord Copilot using credentials from the environment."""
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error(
            "DISCORD_TOKEN is not set. Copy config/api_keys.env.example to "
            "config/api_keys.env and fill it in."
        )
        raise SystemExit(1)

    portfolio_db = PortfolioDB()
    portfolio_db.init_db()

    copilot = DiscordCopilot(
        portfolio_db=portfolio_db,
        explainer=NarrativeExplainer(),
    )

    if "--demo" in sys.argv:
        _attach_demo(copilot)

    copilot.run(token)


def _attach_demo(copilot: "DiscordCopilot") -> None:
    """Post one synthetic alert once the bot is ready (manual smoke test)."""
    from datetime import datetime, timezone

    from data_models import PortfolioState, Signal, SignalStatus, SignalType

    async def _on_ready() -> None:
        logger.info("Demo mode: posting one synthetic alert.")
        signal = Signal(
            ticker="AI.PA", signal_type=SignalType.BUY, score=88.0,
            status=SignalStatus.APPROVED, target_qty=7,
            reason="RSI < 30 while Price > SMA200. Mean-reversion setup.",
        )
        portfolio = PortfolioState(
            cash_available=8000.0, total_equity=20000.0, positions=[],
            last_updated=datetime.now(timezone.utc),
        )
        await copilot.send_signal_alert(signal, portfolio, current_price=180.0)

    # Chain onto on_ready without losing the original logging behaviour.
    original_on_ready = copilot.on_ready

    async def _combined() -> None:
        await original_on_ready()
        await _on_ready()

    copilot.on_ready = _combined  # type: ignore[method-assign]


if __name__ == "__main__":
    main()
