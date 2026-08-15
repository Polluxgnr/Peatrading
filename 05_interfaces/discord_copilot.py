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
_RISK_DIR = os.path.join(os.path.dirname(_INTERFACES_DIR), "03_risk_portfolio")
sys.path.insert(0, _INTERFACES_DIR)
sys.path.insert(0, _CORE_DIR)
sys.path.insert(0, _RISK_DIR)

from data_models import PortfolioState, Position, Signal, SignalStatus, SignalType  # noqa: E402
from llm_explainer import NarrativeExplainer  # noqa: E402

try:
    from limit_price_optimizer import calculate_smart_limit_price  # noqa: E402
except ImportError:
    calculate_smart_limit_price = None

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
        """Persist the execution and update the message with tiered limit prices."""
        try:
            cost = self._execute_in_db()
            self._disable_all()
            embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
            embed.color = _GREEN
            embed.title = f"\u2705 ORDRE EX\u00c9CUT\u00c9 EN BASE : {self.signal.ticker}"

            atr = 0.0
            if isinstance(self.signal.lineage, dict):
                atr = float(self.signal.lineage.get("atr_14") or self.signal.lineage.get("atr") or 0.0)

            tiers = {}
            if calculate_smart_limit_price is not None:
                tiers = calculate_smart_limit_price(
                    self.signal.ticker,
                    self.current_price,
                    atr_14=atr,
                    direction=self.signal.signal_type.name,
                )

            tiers_md = (
                f"\U0001F7E2 **Aggressif (Fill rapide)** : `{tiers.get('aggressive', self.current_price):.2f} \u20ac`\n"
                f"\U0001F3AF **Optimal (Recommand\u00e9)** : `{tiers.get('optimal', self.current_price):.2f} \u20ac`\n"
                f"\U0001F422 **Patient (Bon R:R)** : `{tiers.get('patient', self.current_price):.2f} \u20ac`"
            )

            embed.add_field(
                name="\U0001F4CB Ticket d'Ex\u00e9cution PEA",
                value=(
                    f"**Volume** : {self.signal.target_qty} action(s)\n"
                    f"**Notional Estim\u00e9** : {cost:.2f} EUR\n"
                    f"**Prix de March\u00e9** : {self.current_price:.2f} EUR\n\n"
                    f"**Paliers de Prix Limite Sugg\u00e9r\u00e9s** :\n{tiers_md}"
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


import io
from difflib import get_close_matches
import mplfinance as mpf
import pandas as pd
import yfinance as yf
import yaml

_COMMON_NAME_MAP = {
    "LVMH": "MC.PA", "AIR LIQUIDE": "AI.PA", "TOTAL": "TTE.PA", "TOTALENERGIES": "TTE.PA",
    "HERMES": "RMS.PA", "SANOFI": "SAN.PA", "BNP": "BNP.PA", "BNP PARIBAS": "BNP.PA",
    "AIRBUS": "AIR.PA", "SCHNEIDER": "SU.PA", "L'OREAL": "OR.PA", "LOREAL": "OR.PA",
    "KERING": "KER.PA", "DANONE": "BN.PA", "VINCI": "DG.PA", "SAFRAN": "SAF.PA",
    "STELLANTIS": "STLAP.PA", "RENAULT": "RNO.PA", "ORANGE": "ORA.PA", "ENGIE": "ENGI.PA",
    "CAPGEMINI": "CAP.PA", "DASSAULT": "DSY.PA", "THALES": "HO.PA", "MICHELIN": "ML.PA",
    "SAINT-GOBAIN": "SGO.PA", "SAINT GOBAIN": "SGO.PA", "SOCIETE GENERALE": "GLE.PA",
    "SOCGEN": "GLE.PA", "CREDIT AGRICOLE": "ACA.PA", "VEOLIA": "VIE.PA", "PUBLICIS": "PUB.PA",
    "PERNOD": "RI.PA", "PERNOD RICARD": "RI.PA", "WORLD": "CW8.PA", "MSCI WORLD": "CW8.PA",
    "CW8": "CW8.PA", "CAC40": "^FCHI", "CAC": "^FCHI", "SP500": "^GSPC", "NASDAQ": "^IXIC",
}


def resolve_ticker_fuzzy(query: str, config_dir: Path | None = None) -> str:
    """Resolve a raw user string (e.g. 'LVMH', 'air liquide', 'MC') to a valid Yahoo ticker."""
    cleaned = query.strip().upper().replace("<GO>", "").replace("GO", "").strip()
    if not cleaned:
        return "MC.PA"

    if cleaned in _COMMON_NAME_MAP:
        return _COMMON_NAME_MAP[cleaned]

    # Direct ticker with .PA, .AS, .DE, .MI
    if cleaned.endswith((".PA", ".AS", ".DE", ".MI", ".BR", "=X")) or cleaned.startswith("^"):
        return cleaned

    # Try mapping from pea_universe.yaml
    cfg_dir = config_dir or (Path(__file__).resolve().parent.parent / "config")
    uni_path = cfg_dir / "pea_universe.yaml"
    if uni_path.exists():
        try:
            with open(uni_path, "r", encoding="utf-8") as fh:
                uni = yaml.safe_load(fh).get("universe", {})
            name_to_tick = {}
            for sector, members in uni.items():
                for m in members:
                    name_to_tick[m["name"].upper()] = m["ticker"]
                    name_to_tick[m["ticker"].upper()] = m["ticker"]

            # Exact match
            if cleaned in name_to_tick:
                return name_to_tick[cleaned]

            # Substring match
            for name, tick in name_to_tick.items():
                if cleaned in name or name in cleaned:
                    return tick

            # Close match
            matches = get_close_matches(cleaned, list(name_to_tick.keys()), n=1, cutoff=0.6)
            if matches:
                return name_to_tick[matches[0]]
        except Exception:  # noqa: BLE001
            pass

    # Default to .PA for French equities
    return f"{cleaned}.PA"


def generate_candlestick_chart(ticker: str, days: int = 120, timeseries_db=None) -> io.BytesIO | None:
    """Generate dark-themed candlestick chart with Volume and SMA50 using mplfinance."""
    df = None
    if timeseries_db is not None and hasattr(timeseries_db, "get_historical_prices"):
        try:
            df = timeseries_db.get_historical_prices(ticker, days=days)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed fetching %s from DuckDB: %s", ticker, exc)

    if df is None or df.empty or len(df) < 10:
        try:
            raw = yf.download(ticker, period=f"{max(days, 60)}d", interval="1d", progress=False, auto_adjust=True)
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)
            df = raw
        except Exception as exc:  # noqa: BLE001
            logger.warning("yfinance fallback failed for %s: %s", ticker, exc)
            return None

    if df is None or df.empty or len(df) < 5:
        return None

    # Ensure column casing for mplfinance: Date index, Open, High, Low, Close, Volume
    plot_df = df.copy()
    if "Date" in plot_df.columns:
        plot_df["Date"] = pd.to_datetime(plot_df["Date"])
        plot_df.set_index("Date", inplace=True)
    elif not isinstance(plot_df.index, pd.DatetimeIndex):
        plot_df.index = pd.to_datetime(plot_df.index)

    required = ["Open", "High", "Low", "Close"]
    for col in required:
        if col not in plot_df.columns and col.lower() in plot_df.columns:
            plot_df[col] = plot_df[col.lower()]

    if "Volume" not in plot_df.columns and "volume" in plot_df.columns:
        plot_df["Volume"] = plot_df["volume"]
    elif "Volume" not in plot_df.columns:
        plot_df["Volume"] = 1000

    plot_df = plot_df.tail(days).dropna(subset=["Open", "High", "Low", "Close"])
    if plot_df.empty:
        return None

    # Custom Bloomberg dark market colors
    mc = mpf.make_marketcolors(
        up="#00FF00", down="#FF3B30",
        edge={"up": "#00FF00", "down": "#FF3B30"},
        wick={"up": "#00FF00", "down": "#FF3B30"},
        volume={"up": "#00B4D8", "down": "#FF3B30"},
    )
    style = mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        marketcolors=mc,
        facecolor="#050505",
        edgecolor="#222222",
        figcolor="#050505",
        gridcolor="#1A1A1A",
        gridstyle="--",
    )

    buf = io.BytesIO()
    try:
        mpf.plot(
            plot_df,
            type="candle",
            mav=(50,),
            volume=True,
            style=style,
            title=f"\n{ticker} - 120D Candlestick + SMA50",
            savefig=dict(fname=buf, dpi=120, bbox_inches="tight", facecolor="#050505"),
        )
        buf.seek(0)
        return buf
    except Exception as exc:  # noqa: BLE001
        logger.exception("mplfinance rendering failed for %s: %s", ticker, exc)
        return None


class DiscordCopilot(discord.Client):
    """Discord client that posts trade alerts, handles approvals, and charts on-the-fly."""

    def __init__(self, portfolio_db=None, explainer: NarrativeExplainer | None = None, timeseries_db=None) -> None:
        """Initialize the client with a portfolio DB, timeseries DB, and an LLM explainer."""
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.portfolio_db = portfolio_db
        self.timeseries_db = timeseries_db
        self.explainer = explainer or NarrativeExplainer()
        self.channel_id = int(os.getenv("DISCORD_CHANNEL_ID", "0"))

    async def on_ready(self) -> None:
        """Log a confirmation once the bot has connected."""
        logger.info("Discord Copilot connected as %s (channel_id=%s).",
                    self.user, self.channel_id)

    async def on_message(self, message: discord.Message) -> None:
        """Handle conversational commands like !chart [TICKER]."""
        if message.author.bot:
            return

        content = message.content.strip()
        if content.startswith(("!chart", "!c ", "/chart")):
            parts = content.split(maxsplit=1)
            query = parts[1] if len(parts) > 1 else "CW8.PA"
            ticker = resolve_ticker_fuzzy(query)

            async with message.channel.typing():
                buf = generate_candlestick_chart(ticker, days=120, timeseries_db=self.timeseries_db)
                if buf is None:
                    await message.reply(f"⚠️ Impossible de générer le graphique pour `{ticker}` (symbole introuvable ou données manquantes).")
                    return

                discord_file = discord.File(fp=buf, filename=f"{ticker}_chart.png")
                embed = discord.Embed(
                    title=f"📊 Graphique Technique 120J : {ticker}",
                    color=discord.Color.from_str("#00B4D8"),
                )
                embed.set_image(url=f"attachment://{ticker}_chart.png")
                embed.set_footer(text="PEA Pollux Sniper Terminal • Chandeliers + Volume + SMA50")
                await message.reply(embed=embed, file=discord_file)

    def build_embed(self, signal: Signal, explanation: str) -> discord.Embed:
        """Build the alert embed for a signal with enriched AI, NLP, and StatArb metadata."""
        is_buy = signal.signal_type == SignalType.BUY
        embed = discord.Embed(
            title=f"\U0001F6A8 PEA OPPORTUNIT\u00c9 : {signal.signal_type.name} {signal.ticker}",
            color=_GREEN if is_buy else _RED,
        )
        embed.add_field(name="Quantit\u00e9", value=f"{signal.target_qty} actions", inline=True)
        embed.add_field(name="Score Technique", value=f"{signal.score:.1f}/100", inline=True)

        lineage = signal.lineage if isinstance(signal.lineage, dict) else {}

        # 1. StatArb Context
        strategy_str = str(getattr(signal, "strategy", "") or lineage.get("strategy", ""))
        pair_ticker = lineage.get("pair_ticker") or lineage.get("pair")
        z_score = lineage.get("z_score") or lineage.get("spread_zscore")
        p_val = lineage.get("coint_pvalue") or lineage.get("p_value")
        if "STAT_ARB" in strategy_str.upper() or pair_ticker is not None:
            z_str = f"{float(z_score):.2f}" if z_score is not None else "N/A"
            p_str = f"{float(p_val):.4f}" if p_val is not None else "N/A"
            embed.add_field(
                name="\u2696\ufe0f Arbitrage Statistique (Paire)",
                value=f"Paire: **{signal.ticker}** vs **{pair_ticker}** | Z-Score: `{z_str}` (p={p_str})",
                inline=False,
            )

        # 2. FinBERT Sentiment
        sentiment_score = lineage.get("finbert_sentiment") or lineage.get("sentiment_score") or lineage.get("nlp_score")
        sentiment_label = lineage.get("sentiment_label") or lineage.get("nlp_label")
        if sentiment_score is not None:
            try:
                s_val = float(sentiment_score)
                label_txt = f" ({sentiment_label})" if sentiment_label else (" (Bullish)" if s_val > 15 else (" (Bearish)" if s_val < -15 else " (Neutre)"))
                embed.add_field(
                    name="\U0001F4F0 Sentiment FinBERT (30J)",
                    value=f"`{s_val:+.1f}/100`{label_txt}",
                    inline=True,
                )
            except Exception:
                pass

        # 3. ML Win Probability
        ml_prob = getattr(signal, "ml_probability", None) or lineage.get("ml_probability") or lineage.get("win_probability")
        if ml_prob is not None:
            try:
                prob_pct = float(ml_prob) * 100.0 if float(ml_prob) <= 1.0 else float(ml_prob)
                ci = lineage.get("conformal_interval")
                ci_str = f" [IC: {ci[0]:.0f}% - {ci[1]:.0f}%]" if isinstance(ci, (list, tuple)) and len(ci) == 2 else ""
                embed.add_field(
                    name="\U0001F916 Probabilit\u00e9 ML (XGBoost)",
                    value=f"`{prob_pct:.1f}%`{ci_str}",
                    inline=True,
                )
            except Exception:
                pass

        # 4. Red Team Verdict
        red_team = lineage.get("red_team_verdict") or lineage.get("judge_synthesis") or lineage.get("red_team_debate")
        if red_team:
            embed.add_field(
                name="\u2696\ufe0f Verdict Comit\u00e9 Red Team",
                value=str(red_team)[:500],
                inline=False,
            )

        embed.add_field(name="Analyse IA", value=explanation[:1000] if explanation else "Aucune synth\u00e8se narrative.", inline=False)
        return embed


    async def send_signal_alert(
        self,
        signal: Signal,
        portfolio: PortfolioState,
        explainer: NarrativeExplainer | None = None,
        current_price: float = 0.0,
    ) -> discord.Message | None:
        """Generate an explanation and post an interactive alert."""
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
