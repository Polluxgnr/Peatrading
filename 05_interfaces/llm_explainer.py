"""LLM narrative explainer for PEA Sniper Terminal V-Prime.

Wraps OpenRouter (async, via ``aiohttp``) to turn an already-approved,
already-sized ``Signal`` into a short, human-readable rationale for Discord.

STRICT: the LLM has ZERO decision power. It only produces the ``explanation``
string. It never reads or writes ``status``, ``target_qty`` or any math.

.env requirements (config/api_keys.env):
    OPENROUTER_API_KEY   - required; without it the fallback string is used.
    OPENROUTER_MODEL     - optional; defaults to mistralai/mistral-7b-instruct.
"""

import logging
import os
import sys
from pathlib import Path

import aiohttp

try:  # Load config/api_keys.env if python-dotenv is available.
    from dotenv import load_dotenv

    _ENV_PATH = Path(__file__).resolve().parent.parent / "config" / "api_keys.env"
    load_dotenv(_ENV_PATH)
except Exception:  # noqa: BLE001 - dotenv is a convenience, not a requirement.
    pass

_CORE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "01_memory_core"
)
sys.path.insert(0, _CORE_DIR)

from data_models import PortfolioState, Signal  # noqa: E402

logger = logging.getLogger(__name__)

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_DEFAULT_MODEL = "mistralai/mistral-7b-instruct"
_FALLBACK = "Technical signal approved. (AI explanation unavailable)"
_REQUEST_TIMEOUT_S = 20


async def openrouter_chat(
    messages: list[dict],
    api_key: str | None,
    model: str = _DEFAULT_MODEL,
    max_tokens: int = 180,
    temperature: float = 0.4,
    timeout_s: int = _REQUEST_TIMEOUT_S,
) -> str | None:
    """Send a chat-completion request to OpenRouter and return the text.

    Shared by every LLM consumer (trade explainer, news sentiment scorer, weekly
    historian) so the HTTP/auth/error handling lives in exactly one place.

    Args:
        messages: OpenAI-style ``[{"role", "content"}, ...]`` message list.
        api_key: OpenRouter API key; ``None`` short-circuits to ``None``.
        model: Model slug to query.
        max_tokens: Upper bound on the completion length.
        temperature: Sampling temperature.
        timeout_s: Total request timeout in seconds.

    Returns:
        str | None: The assistant message content, or ``None`` on any failure.
    """
    if not api_key:
        return None

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Title": "PEA Sniper Terminal V-Prime",
    }
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                _OPENROUTER_URL, json=payload, headers=headers
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error("OpenRouter HTTP %s: %s", resp.status, body[:200])
                    return None
                data = await resp.json()
                content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                ).strip()
                return content or None
    except Exception:  # noqa: BLE001 - never let LLM I/O crash a caller.
        logger.exception("OpenRouter request failed.")
        return None


class NarrativeExplainer:
    """Generates concise trade rationales via OpenRouter."""

    def __init__(self) -> None:
        """Read the OpenRouter API key and model slug from the environment."""
        self.api_key: str | None = os.getenv("OPENROUTER_API_KEY")
        self.model: str = os.getenv("OPENROUTER_MODEL", _DEFAULT_MODEL)
        if not self.api_key:
            logger.warning(
                "OPENROUTER_API_KEY not set; explanations will use the fallback."
            )

    @staticmethod
    def _sector_breakdown(portfolio: PortfolioState) -> str:
        """Return a compact 'Sector X%' string from the portfolio positions."""
        sectors = sorted({p.sector for p in portfolio.positions})
        if not sectors:
            return "no open positions"
        parts = [
            f"{sector} {portfolio.get_sector_weight(sector) * 100:.0f}%"
            for sector in sectors
        ]
        return ", ".join(parts)

    def _build_prompt(self, signal: Signal, portfolio: PortfolioState) -> str:
        """Compose the user prompt describing the trade and portfolio context."""
        qty = signal.target_qty if signal.target_qty is not None else 0
        breakdown = self._sector_breakdown(portfolio)
        cash_pct = (
            portfolio.cash_available / portfolio.total_equity * 100
            if portfolio.total_equity > 0
            else 0.0
        )
        return (
            f"Explain why buying {qty} shares of {signal.ticker} makes sense. "
            f"Signal type: {signal.signal_type.value}. "
            f"Technical score: {signal.score:.1f}/100. "
            f"Underlying setup: {signal.reason}. "
            f"Portfolio context: {cash_pct:.0f}% cash, sector exposure -> "
            f"{breakdown}. "
            "Be concise and professional."
        )

    async def explain_trade(
        self, signal: Signal, portfolio: PortfolioState
    ) -> str:
        """Generate a 2-3 sentence rationale for an approved trade.

        Args:
            signal: The APPROVED, already-sized signal.
            portfolio: Current portfolio snapshot for context.

        Returns:
            str: The LLM explanation, or a safe fallback string on any error.
        """
        if not self.api_key:
            return _FALLBACK

        system_prompt = (
            "You are a quantitative analyst at a systematic PEA fund. A "
            "mathematical model has ALREADY decided this trade; you do not make "
            "decisions. Explain the rationale in strictly 2 to 3 short "
            "sentences. No greetings, no disclaimers, no financial advice - "
            "just crisp, professional analysis."
        )
        content = await openrouter_chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": self._build_prompt(signal, portfolio)},
            ],
            api_key=self.api_key,
            model=self.model,
            max_tokens=180,
            temperature=0.4,
        )
        return content or _FALLBACK


if __name__ == "__main__":
    import asyncio
    from datetime import datetime, timezone

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    sys.path.insert(0, _CORE_DIR)
    from data_models import Position, SignalType  # noqa: E402

    demo_signal = Signal(
        ticker="AI.PA",
        signal_type=SignalType.BUY,
        score=88.0,
        target_qty=7,
        reason="RSI < 30 while Price > SMA200. Mean-reversion setup.",
    )
    demo_portfolio = PortfolioState(
        cash_available=8000.0,
        total_equity=20000.0,
        positions=[
            Position(ticker="MC.PA", qty_shares=2, avg_entry_price=600,
                     current_price=600, sector="Luxury"),
        ],
        last_updated=datetime.now(timezone.utc),
    )

    async def _demo() -> None:
        explainer = NarrativeExplainer()
        print("Prompt preview:\n", explainer._build_prompt(demo_signal, demo_portfolio))
        text = await explainer.explain_trade(demo_signal, demo_portfolio)
        print("\nExplanation:\n", text)

    asyncio.run(_demo())
