"""Discord Copilot for PEA Sniper Terminal V-Prime.

Pushes interactive trade alerts to Discord and waits for the human to approve
or reject. Execution is manual: approving records the trade in SQLite (status
EXECUTED, cash deducted, position added) - it never sends an order to a broker.

STRICT: the LLM only writes the explanation text (Phase 7.1). Buttons and DB
logic here are deterministic.

.env requirements (config/api_keys.env):
    DISCORD_TOKEN        - the bot token.
    DISCORD_CHANNEL_ID   - numeric channel ID for alerts.
    OPENROUTER_API_KEY   - used by NarrativeExplainer (optional; has fallback).
"""

import logging
import os
import sys
from pathlib import Path

import discord

try:
    from dotenv import load_dotenv

    _ENV_PATH = Path(__file__).resolve().parent.parent / "config" / "api_keys.env"
    load_dotenv(_ENV_PATH)
except Exception:  # noqa: BLE001
    pass

_INTERFACES_DIR = os.path.dirname(os.path.abspath(__file__))
_CORE_DIR = os.path.join(os.path.dirname(_INTERFACES_DIR), "01_memory_core")
sys.path.insert(0, _INTERFACES_DIR)
sys.path.insert(0, _CORE_DIR)

from data_models import PortfolioState, Position, Signal, SignalStatus, SignalType  # noqa: E402
from llm_explainer import NarrativeExplainer  # noqa: E402

logger = logging.getLogger(__name__)

_GREEN = discord.Color.from_str("#00E676")
_RED = discord.Color.from_str("#FF3B30")


class TradeActionView(discord.ui.View):
    """Interactive Approve/Reject buttons attached to a trade alert.

    Approving persists the trade to SQLite via the provided ``PortfolioDB``.
    Both callbacks immediately edit the message so Discord never shows a stuck
    "thinking" state.
    """

    def __init__(
        self,
        signal: Signal,
        portfolio_db,
        current_price: float,
        timeout: float | None = 3600,
    ) -> None:
        """Initialize the view.

        Args:
            signal: The approved signal this alert represents.
            portfolio_db: A ``PortfolioDB`` used to persist an execution.
            current_price: Price per share used to compute the cash outlay.
            timeout: Seconds before the buttons auto-disable (default 1h).
        """
        super().__init__(timeout=timeout)
        self.signal = signal
        self.portfolio_db = portfolio_db
        self.current_price = current_price

    def _disable_all(self) -> None:
        """Disable every child button (post-decision)."""
        for child in self.children:
            child.disabled = True

    def _execute_in_db(self) -> float:
        """Persist the executed trade to SQLite and return the cash spent.

        Deducts the notional from cash, adds/merges the position, refreshes
        equity, and logs the signal as EXECUTED.

        Returns:
            float: The cash amount spent on the trade.
        """
        qty = self.signal.target_qty or 0
        cost = qty * self.current_price

        state = self.portfolio_db.get_portfolio_state()
        state.cash_available = max(0.0, state.cash_available - cost)

        # Merge into an existing position (weighted avg) or append a new one.
        existing = next(
            (p for p in state.positions if p.ticker == self.signal.ticker), None
        )
        if existing is not None:
            total_qty = existing.qty_shares + qty
            if total_qty > 0:
                existing.avg_entry_price = (
                    existing.avg_entry_price * existing.qty_shares
                    + self.current_price * qty
                ) / total_qty
            existing.qty_shares = total_qty
            existing.current_price = self.current_price
        else:
            state.positions.append(
                Position(
                    ticker=self.signal.ticker,
                    qty_shares=qty,
                    avg_entry_price=self.current_price,
                    current_price=self.current_price,
                    sector=self._infer_sector(),
                )
            )

        state.total_equity = state.cash_available + sum(
            p.market_value for p in state.positions
        )
        self.portfolio_db.update_portfolio(state)

        self.signal.status = SignalStatus.EXECUTED
        self.portfolio_db.log_signal(self.signal)
        return cost

    def _infer_sector(self) -> str:
        """Best-effort sector lookup from the universe file (falls back)."""
        try:
            import yaml

            universe_path = (
                Path(__file__).resolve().parent.parent / "config" / "pea_universe.yaml"
            )
            with open(universe_path, "r", encoding="utf-8") as fh:
                universe = yaml.safe_load(fh) or {}
            for sector, members in universe.get("universe", {}).items():
                for entry in members:
                    if entry["ticker"] == self.signal.ticker:
                        return sector
        except Exception:  # noqa: BLE001
            pass
        return "UNKNOWN"

    @discord.ui.button(label="Approuver le Trade", style=discord.ButtonStyle.success,
                       emoji="\U0001F7E2")
    async def approve(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        """Persist the execution and update the message."""
        try:
            cost = self._execute_in_db()
            self._disable_all()
            embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
            embed.color = _GREEN
            embed.title = f"\u2705 TRADE EXECUTED : {self.signal.ticker}"
            embed.add_field(
                name="Execution",
                value=(
                    f"{self.signal.target_qty} action(s) @ {self.current_price:.2f} EUR "
                    f"(co\u00fbt {cost:.2f} EUR)"
                ),
                inline=False,
            )
            await interaction.response.edit_message(embed=embed, view=self)
            logger.info("Trade EXECUTED for %s by %s.", self.signal.ticker, interaction.user)
        except Exception:  # noqa: BLE001 - always answer the interaction.
            logger.exception("Approve callback failed for %s.", self.signal.ticker)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "\u26a0\ufe0f Erreur lors de l'ex\u00e9cution en base.", ephemeral=True
                )
        finally:
            self.stop()

    @discord.ui.button(label="Rejeter", style=discord.ButtonStyle.danger,
                       emoji="\U0001F534")
    async def reject(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        """Mark the alert rejected by the user and update the message."""
        try:
            self.signal.status = SignalStatus.REJECTED
            if self.portfolio_db is not None:
                self.portfolio_db.log_signal(self.signal)
            self._disable_all()
            embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
            embed.color = _RED
            embed.title = f"\u274c TRADE REJECTED BY USER : {self.signal.ticker}"
            await interaction.response.edit_message(embed=embed, view=self)
            logger.info("Trade REJECTED for %s by %s.", self.signal.ticker, interaction.user)
        except Exception:  # noqa: BLE001
            logger.exception("Reject callback failed for %s.", self.signal.ticker)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "\u26a0\ufe0f Erreur.", ephemeral=True
                )
        finally:
            self.stop()


class DiscordCopilot(discord.Client):
    """Discord client that posts trade alerts and handles approvals."""

    def __init__(self, portfolio_db=None, explainer: NarrativeExplainer | None = None) -> None:
        """Initialize the client with a portfolio DB and an LLM explainer.

        Args:
            portfolio_db: A ``PortfolioDB`` for persisting executions.
            explainer: A ``NarrativeExplainer`` (created if not provided).
        """
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.portfolio_db = portfolio_db
        self.explainer = explainer or NarrativeExplainer()
        self.channel_id = int(os.getenv("DISCORD_CHANNEL_ID", "0"))

    async def on_ready(self) -> None:
        """Log a confirmation once the bot has connected."""
        logger.info("Discord Copilot connected as %s (channel_id=%s).",
                    self.user, self.channel_id)

    def build_embed(self, signal: Signal, explanation: str) -> discord.Embed:
        """Build the alert embed for a signal.

        Args:
            signal: The approved signal.
            explanation: The LLM-generated rationale.

        Returns:
            discord.Embed: The formatted alert embed.
        """
        is_buy = signal.signal_type == SignalType.BUY
        embed = discord.Embed(
            title=f"\U0001F6A8 PEA OPPORTUNIT\u00c9 : {signal.signal_type.name} {signal.ticker}",
            color=_GREEN if is_buy else _RED,
        )
        embed.add_field(name="Quantit\u00e9", value=f"{signal.target_qty} actions", inline=True)
        embed.add_field(name="Score Technique", value=f"{signal.score:.1f}/100", inline=True)
        embed.add_field(name="Analyse IA", value=explanation, inline=False)
        return embed

    async def send_signal_alert(
        self,
        signal: Signal,
        portfolio: PortfolioState,
        explainer: NarrativeExplainer | None = None,
        current_price: float = 0.0,
    ) -> discord.Message | None:
        """Generate an explanation and post an interactive alert.

        Args:
            signal: The approved, sized signal.
            portfolio: Current portfolio snapshot (for LLM context).
            explainer: Optional explainer override (defaults to ``self.explainer``).
            current_price: Price per share used for execution accounting.

        Returns:
            discord.Message | None: The sent message, or ``None`` if the channel
            could not be resolved.
        """
        explainer = explainer or self.explainer
        explanation = await explainer.explain_trade(signal, portfolio)

        embed = self.build_embed(signal, explanation)
        view = TradeActionView(signal, self.portfolio_db, current_price)

        channel = self.get_channel(self.channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(self.channel_id)
            except Exception:  # noqa: BLE001
                logger.error("Could not resolve channel %s.", self.channel_id)
                return None

        message = await channel.send(embed=embed, view=view)
        logger.info("Alert sent for %s to channel %s.", signal.ticker, self.channel_id)
        return message
