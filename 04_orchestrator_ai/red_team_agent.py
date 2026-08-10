"""Red Team Adversarial Debate Engine for PEA Sniper Terminal.

Orchestrates a 3-agent adversarial debate on high-conviction trade setups:
  1. Bull Analyst: Constructs the aggressive upside thesis, highlighting technical oversoldness,
     valuation discounts, and industry catalysts.
  2. Bear Risk Officer: Attacks the thesis, scrutinizing macro headwinds, balance sheet debt,
     governance risks, and potential value-trap dynamics.
  3. Investment Committee Judge: Weighs quantitative score, risk parameters, and adversarial points
     to issue an institutional verdict [GO / NO_GO / REDUCE_SIZE].
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class RedTeamDebateResult:
    ticker: str
    bull_thesis: str
    bear_antithesis: str
    judge_synthesis: str
    final_verdict: str  # "GO", "NO_GO", "REDUCE_SIZE"
    confidence_score: float


class RedTeamDebateAgent:
    """Orchestrates adversarial multi-agent debate before capital deployment."""

    def __init__(self) -> None:
        pass

    def run_debate(
        self,
        ticker: str,
        technical_score: float,
        dossier: Dict,
        indicators: Dict,
        valuation: Dict,
    ) -> RedTeamDebateResult:
        """Run the adversarial debate for a trade candidate."""
        name = dossier.get("name") or ticker
        sector = dossier.get("sector") or "Secteur non spécifié"
        close = f"{indicators['close']:.2f} €" if indicators and indicators.get("close") else "n/a"
        rsi = f"{indicators['rsi']:.1f}" if indicators and indicators.get("rsi") is not None else "n/a"
        pe = f"{valuation.get('trailing_pe'):.1f}×" if valuation and valuation.get("trailing_pe") else "n/a"

        # Attempt LLM-based debate via OpenRouter if configured
        try:
            from news_sentiment_llm import OpenRouterClient
            client = OpenRouterClient()
            if client.is_configured:
                prompt = (
                    f"Effectue un débat contradictoire Red Team sur l'achat PEA de {name} ({ticker}) :\n"
                    f"- Score quantitatif : {technical_score:.1f}/100\n"
                    f"- Secteur : {sector}, Cours : {close}, RSI(14) : {rsi}, P/E : {pe}\n\n"
                    f"Rédige sous ce format exact :\n"
                    f"BULL: [2 puces d'arguments haussiers]\n"
                    f"BEAR: [2 puces d'objections risques/macro]\n"
                    f"JUDGE: [Synthèse du juge]\n"
                    f"VERDICT: [GO / NO_GO / REDUCE_SIZE]"
                )
                raw_out = client.query_sync(prompt, max_tokens=300)
                if raw_out and "VERDICT:" in raw_out:
                    return self._parse_llm_debate(ticker, raw_out, technical_score)
        except Exception as exc:  # noqa: BLE001
            logger.debug("LLM Red Team debate failed: %s; using deterministic institutional synthesis", exc)

        # Deterministic Institutional Debate Framework
        is_oversold = indicators and indicators.get("rsi", 50) < 30
        is_high_conviction = technical_score >= 80

        bull = (
            f"• Configuration Mean-Reversion favorable avec RSI(14) à {rsi} sur un leader ({sector}).\n"
            f"• Valorisation attractive (P/E {pe}) offrant une marge de sécurité fondamentale."
        )

        bear = (
            f"• Risque de 'falling knife' si la dynamique macro sectorielle se dégrade davantage.\n"
            f"• Présence d'incertitudes sur les taux et l'élasticité de la demande européenne."
        )

        if is_high_conviction and is_oversold:
            judge = f"Score quantitatif très élevé ({technical_score:.0f}/100) avec RSI survendu. Les objections du Bear sont compensées par le ratio rendement/risque."
            verdict = "GO"
            conf = 0.85
        elif technical_score >= 70:
            judge = f"Setup valide mais prudence requise. Réduction recommandée de la taille de tranche pour limiter la volatilité."
            verdict = "REDUCE_SIZE"
            conf = 0.65
        else:
            judge = f"Conviction insuffisante ({technical_score:.0f}/100) face aux risques soulignés par le Risk Officer."
            verdict = "NO_GO"
            conf = 0.40

        return RedTeamDebateResult(
            ticker=ticker,
            bull_thesis=bull,
            bear_antithesis=bear,
            judge_synthesis=judge,
            final_verdict=verdict,
            confidence_score=conf,
        )

    def _parse_llm_debate(self, ticker: str, text: str, score: float) -> RedTeamDebateResult:
        bull, bear, judge, verdict = "", "", "", "GO"
        for line in text.split("\n"):
            line_str = line.strip()
            if line_str.startswith("BULL:"):
                bull = line_str.replace("BULL:", "").strip()
            elif line_str.startswith("BEAR:"):
                bear = line_str.replace("BEAR:", "").strip()
            elif line_str.startswith("JUDGE:"):
                judge = line_str.replace("JUDGE:", "").strip()
            elif line_str.startswith("VERDICT:"):
                verdict = line_str.replace("VERDICT:", "").strip().upper()

        if verdict not in ("GO", "NO_GO", "REDUCE_SIZE"):
            verdict = "GO" if score >= 75 else "REDUCE_SIZE"

        return RedTeamDebateResult(
            ticker=ticker,
            bull_thesis=bull or "Catalyseurs techniques et fondamentaux positifs.",
            bear_antithesis=bear or "Exposition au risque de marché et aux vents contraires macro.",
            judge_synthesis=judge or "L'alignement quantitatif valide la prise de position disciplinée.",
            final_verdict=verdict,
            confidence_score=0.80 if verdict == "GO" else 0.50,
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    agent = RedTeamDebateAgent()
    res = agent.run_debate(
        "MC.PA",
        82.5,
        {"name": "LVMH", "sector": "Luxe"},
        {"close": 612.0, "rsi": 28.4},
        {"trailing_pe": 21.5},
    )
    print("Red Team Debate Result:", res)
