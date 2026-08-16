"""LLM narrative explainer for PEA Sniper Terminal V-Prime.

Wraps OpenRouter (async, via ``aiohttp``) to turn an already-approved,
already-sized quantitative ``Signal`` into a short, human-readable recommendation rationale for Discord & UI.

STRICT: The system solely produces Quantitative Recommendations.
The LLM has ZERO decision power; execution authority rests strictly with the human portfolio manager.

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

import json
from typing import AsyncIterator, Iterator
import requests

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
_DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "mistral")
_FALLBACK = "Technical signal approved. (AI explanation unavailable)"
_REQUEST_TIMEOUT_S = 20


async def ollama_chat_stream(
    messages: list[dict],
    model: str = _DEFAULT_MODEL,
    timeout_s: int = 60,
) -> AsyncIterator[str]:
    """Stream chat completion tokens from a local Ollama instance asynchronously.

    Args:
        messages: List of [{"role": "user"|"system"|"assistant", "content": "..."}].
        model: Local model tag (default: 'mistral').
        timeout_s: Request timeout in seconds.

    Yields:
        str: Content delta chunk as received.
    """
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(_OLLAMA_URL, json=payload) as resp:
                if resp.status != 200:
                    err_text = await resp.text()
                    logger.error("Ollama HTTP %s: %s", resp.status, err_text[:200])
                    yield f"🔴 Erreur HTTP {resp.status} depuis l'IA locale (Ollama)."
                    return

                async for line in resp.content:
                    if not line:
                        continue
                    try:
                        line_str = line.decode("utf-8").strip()
                        if not line_str:
                            continue
                        chunk_obj = json.loads(line_str)
                        msg_chunk = chunk_obj.get("message", {}).get("content", "")
                        if msg_chunk:
                            yield msg_chunk
                        if chunk_obj.get("done", False):
                            break
                    except Exception:
                        continue

    except (aiohttp.ClientConnectorError, aiohttp.ServerDisconnectedError):
        logger.warning("Ollama connection refused at %s. Service offline.", _OLLAMA_URL)
        yield "🔴 Erreur : Le moteur d'IA local (Ollama) est hors ligne ou injoignable."
    except Exception as exc:
        logger.exception("Ollama chat stream failed: %s", exc)
        yield f"🔴 Erreur IA locale : {exc}"


def ollama_chat_stream_sync(
    messages: list[dict],
    model: str = _DEFAULT_MODEL,
    timeout_s: int = 60,
) -> Iterator[str]:
    """Synchronous generator yielding text chunks from local Ollama instance (for Streamlit)."""
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    try:
        with requests.post(_OLLAMA_URL, json=payload, stream=True, timeout=timeout_s) as resp:
            if resp.status_code != 200:
                yield f"🔴 Erreur HTTP {resp.status_code} depuis l'IA locale (Ollama)."
                return

            for line in resp.iter_lines():
                if line:
                    try:
                        line_str = line.decode("utf-8").strip()
                        if not line_str:
                            continue
                        chunk_obj = json.loads(line_str)
                        content = chunk_obj.get("message", {}).get("content", "")
                        if content:
                            yield content
                        if chunk_obj.get("done", False):
                            break
                    except Exception:
                        continue
    except (requests.ConnectionError, requests.Timeout):
        logger.warning("Ollama connection refused at %s.", _OLLAMA_URL)
        yield "🔴 Erreur : Le moteur d'IA local (Ollama) est hors ligne ou injoignable."
    except Exception as exc:
        logger.exception("Ollama sync stream failed: %s", exc)
        yield f"🔴 Erreur IA locale : {exc}"



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
