"""LLM multi-agent red teaming: bull vs bear vs devil's advocate vs judge."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "05_interfaces"))

from llm_explainer import openrouter_chat  # noqa: E402


async def run_bull_bear_debate(ticker: str, context_data: str) -> dict:
    """Run a 4-agent debate: Bull, Bear, Devil's Advocate PEA, then Judge."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    model = os.getenv("OPENROUTER_MODEL", "mistralai/mistral-7b-instruct")
    if not api_key:
        return {
            "bull": "OpenRouter indisponible (clé manquante).",
            "bear": "OpenRouter indisponible (clé manquante).",
            "devil_advocate": "OpenRouter indisponible (clé manquante).",
            "judge": "Impossible d'arbitrer sans LLM.",
        }

    bull_sys = (
        "You are a ruthless BULL analyst. Defend the stock aggressively, "
        "focus on catalysts, upside asymmetry, and dismiss noisy objections."
    )
    bear_sys = (
        "You are a ruthless BEAR analyst. Attack the stock aggressively, "
        "focus on debt, fragility, momentum breakdowns, and systemic risks."
    )
    devil_sys = (
        "You are the Devil's Advocate PEA — a cynical French retail investor "
        "specialist. Focus exclusively on: Euronext Paris liquidity (ADV, "
        "bid-ask spread), risk of delisting or suspension, PEA-eligibility "
        "removal (titres non éligibles), Bodacc filings, bankruptcy / "
        "sauvegarde judiciaire risk, and the reality of executing integer "
        "share orders on illiquid small/mid caps. Be brutal and specific."
    )
    user_prompt = (
        f"Ticker: {ticker}\n\nContext:\n{context_data}\n\n"
        "Give exactly 5 concise bullet points."
    )

    bull_task = openrouter_chat(
        [{"role": "system", "content": bull_sys}, {"role": "user", "content": user_prompt}],
        api_key=api_key,
        model=model,
        max_tokens=260,
        temperature=0.4,
    )
    bear_task = openrouter_chat(
        [{"role": "system", "content": bear_sys}, {"role": "user", "content": user_prompt}],
        api_key=api_key,
        model=model,
        max_tokens=260,
        temperature=0.4,
    )
    devil_task = openrouter_chat(
        [{"role": "system", "content": devil_sys}, {"role": "user", "content": user_prompt}],
        api_key=api_key,
        model=model,
        max_tokens=280,
        temperature=0.35,
    )
    bull, bear, devil = await asyncio.gather(bull_task, bear_task, devil_task)
    bull = (bull or "Bull argument indisponible.").strip()
    bear = (bear or "Bear argument indisponible.").strip()
    devil = (devil or "Devil's Advocate indisponible.").strip()

    judge_sys = (
        "You are a cynical Senior Portfolio Manager on a French PEA desk. "
        "Read Bull, Bear, and Devil's Advocate PEA arguments and issue a "
        "ruthless final decision in exactly 4 sentences, in French."
    )
    judge_user = (
        f"Ticker: {ticker}\n\nBULL:\n{bull}\n\nBEAR:\n{bear}\n\n"
        f"DEVIL'S ADVOCATE PEA:\n{devil}\n\n"
        "Return: 1) conviction side, 2) key PEA-specific risk, "
        "3) liquidity verdict, 4) action bias."
    )
    judge = await openrouter_chat(
        [{"role": "system", "content": judge_sys}, {"role": "user", "content": judge_user}],
        api_key=api_key,
        model=model,
        max_tokens=280,
        temperature=0.2,
    )
    return {
        "bull": bull,
        "bear": bear,
        "devil_advocate": devil,
        "judge": (judge or "Verdict indisponible.").strip(),
    }
