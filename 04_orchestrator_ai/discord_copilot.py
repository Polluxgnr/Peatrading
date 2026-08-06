import asyncio
import logging
import os
import sys
from pathlib import Path

import discord
from discord import app_commands

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "01_memory_core"))
sys.path.insert(0, str(_ROOT / "03_risk_portfolio"))

from sqlite_portfolio import PortfolioDB, get_portfolio_db
from limit_price_optimizer import calculate_smart_limit_price

# Configure basic logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger("discord_copilot")

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not DISCORD_BOT_TOKEN:
    logger.warning("DISCORD_BOT_TOKEN not found in env. Discord Copilot will not start.")

class PEAPolluxClient(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.db = get_portfolio_db()
        self.db.init_db()

    async def setup_hook(self):
        # Sync the application command with Discord
        await self.tree.sync()
        logger.info("Discord commands synced successfully.")

client = PEAPolluxClient()

def get_signal_by_id(signal_id: str):
    """Fetch a single signal from the database by ID."""
    try:
        row = client.db._conn.execute(
            "SELECT id, ticker, signal_type, score, reason FROM audit_logs WHERE id = ?;",
            (signal_id,)
        ).fetchone()
        return row
    except Exception as e:
        logger.error(f"Error fetching signal {signal_id}: {e}")
        return None

def get_latest_price_and_atr(ticker: str):
    """Fetch the latest price and ATR for a given ticker."""
    try:
        # Fallback to yfinance if local DB doesn't have it easily accessible for a fast query
        import yfinance as yf
        df = yf.download(ticker, period="1mo", progress=False)
        if df.empty:
            return 0.0, 0.0
            
        close = float(df['Close'].iloc[-1])
        # Simple ATR calculation
        high_low = df['High'] - df['Low']
        high_close = (df['High'] - df['Close'].shift()).abs()
        low_close = (df['Low'] - df['Close'].shift()).abs()
        ranges = float(max(high_low.iloc[-1], high_close.iloc[-1], low_close.iloc[-1]))
        atr = ranges # Approximation for demo purposes
        
        return close, atr
    except Exception as e:
        logger.error(f"Error fetching price for {ticker}: {e}")
        return 0.0, 0.0

@client.tree.command(name="approve", description="Approve a trading signal and generate an Order Ticket")
async def approve(interaction: discord.Interaction, signal_id: str):
    await interaction.response.defer()
    
    # Run DB calls in a thread executor if they were heavy, but sqlite is fast enough here
    signal = get_signal_by_id(signal_id)
    if not signal:
        await interaction.followup.send(f"❌ Signal `{signal_id}` not found.")
        return

    ticker = signal["ticker"]
    signal_type = signal["signal_type"]
    
    client.db.update_signal_status(signal_id, "APPROVED", " | Approved via Discord Copilot")
    
    # Calculate smart limit price
    current_price, atr = get_latest_price_and_atr(ticker)
    limit_px = calculate_smart_limit_price(ticker, current_price, atr, direction=signal_type)
    
    # Mock Quantity logic for the ticket
    alloc_amt = 1000.0
    qty = int(alloc_amt // limit_px) if limit_px > 0 else 0
    estimated_fees = round(qty * limit_px * 0.005, 2) # PEA 0.5% cap
    
    ticket_md = f"""
📋 **BROKER ORDER TICKET** 📋
**Signal ID:** `{signal_id}`

**ISIN / Ticker:** `{ticker}`
**Action:** `{signal_type}`
**Quantity:** `{qty}` shares
**Suggested Limit Price:** `€{limit_px:.2f}`
**Estimated Fees (0.5% max PEA cap):** `€{estimated_fees:.2f}`

✅ *Signal has been marked as APPROVED in the orchestrator.*
"""
    await interaction.followup.send(ticket_md)

@client.tree.command(name="reject", description="Reject a trading signal")
async def reject(interaction: discord.Interaction, signal_id: str):
    await interaction.response.defer()
    
    signal = get_signal_by_id(signal_id)
    if not signal:
        await interaction.followup.send(f"❌ Signal `{signal_id}` not found.")
        return

    client.db.update_signal_status(signal_id, "REJECTED", " | Rejected via Discord Copilot")
    await interaction.followup.send(f"🚫 Signal `{signal_id}` for **{signal['ticker']}** has been rejected.")

@client.tree.command(name="status", description="Get live portfolio status")
async def status(interaction: discord.Interaction):
    await interaction.response.defer()
    
    portfolio = client.db.get_portfolio_state()
    
    # VIX approximation via yf
    vix = 15.0
    try:
        import yfinance as yf
        vix_df = yf.download("^VIX", period="5d", progress=False)
        if not vix_df.empty:
            vix = float(vix_df['Close'].iloc[-1])
    except:
        pass
        
    msg = f"""
📊 **PEA Pollux Status**
**Total Equity:** `€{portfolio.total_equity:,.2f}`
**Cash Runway:** `€{portfolio.cash_available:,.2f}`
**Positions:** `{len(portfolio.positions)}` active lines
**VIX Level:** `{vix:.2f}`
"""
    await interaction.followup.send(msg)

@client.tree.command(name="portfolio", description="List active positions and ATR stops")
async def portfolio(interaction: discord.Interaction):
    await interaction.response.defer()
    
    port = client.db.get_portfolio_state()
    if not port.positions:
        await interaction.followup.send("💼 Your portfolio is currently empty.")
        return
        
    lines = ["💼 **Active Positions**"]
    for p in port.positions:
        pnl = 0.0
        if p.avg_entry_price > 0:
            pnl = ((p.current_price / p.avg_entry_price) - 1.0) * 100
            
        lines.append(f"- **{p.ticker}**: {p.qty_shares} shares @ €{p.current_price:.2f} (PnL: {pnl:+.2f}%)")
        
    await interaction.followup.send("\n".join(lines))

if __name__ == "__main__":
    if DISCORD_BOT_TOKEN:
        logger.info("Starting Discord Copilot Daemon...")
        client.run(DISCORD_BOT_TOKEN)
    else:
        logger.error("No token found, exiting.")
