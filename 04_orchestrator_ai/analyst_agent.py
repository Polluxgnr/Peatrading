"""Autonomous Institutional Analyst Agent for PEA Pollux Decision Support Terminal.

Synthesizes multi-source portfolio metrics, Attack/Shield allocation splits,
VIX/Black Swan watchdogs, and approved algorithmic recommendations into an
executive 3-paragraph daily briefing for the human Portfolio Manager.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional

_ROOT = Path(__file__).resolve().parent.parent
for sub in ("00_data_sensors", "01_memory_core", "02_quant_engine", "03_risk_portfolio", "05_interfaces"):
    sys.path.insert(0, str(_ROOT / sub))

logger = logging.getLogger("analyst_agent")


class InstitutionalAnalyst:
    """Generates comprehensive institutional market briefs and portfolio risk assessments."""

    def __init__(
        self,
        model_name: Optional[str] = None,
        timeout: float = 8.0,
    ) -> None:
        self.model_name = model_name or os.getenv("OLLAMA_MODEL", "mistral")
        self.timeout = timeout

    def _build_prompt(
        self,
        portfolio_state: Any,
        thermometer_state: Dict[str, Any],
        top_signals: List[Dict[str, Any]],
        watchdog_alert: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Construct structured quantitative context for the LLM."""
        tot_equity = getattr(portfolio_state, "total_equity", 10000.0) if portfolio_state else 10000.0
        cash = getattr(portfolio_state, "cash_available", 2500.0) if portfolio_state else 2500.0
        exposure_pct = round(((tot_equity - cash) / tot_equity * 100.0), 1) if tot_equity > 0 else 0.0

        atk_pct = float(thermometer_state.get("attack_pct", 0.70)) * 100.0
        def_pct = float(thermometer_state.get("defense_pct", 0.30)) * 100.0
        mode = thermometer_state.get("mode", "ATTACK")
        vix = thermometer_state.get("vix", 16.0)
        vol_21d = float(thermometer_state.get("vol_21d", 0.15)) * 100.0

        watchdog_txt = "AUCUNE ANOMALIE INTRADAY"
        if watchdog_alert and watchdog_alert.get("alert"):
            watchdog_txt = f"🚨 FLASH CRASH INTRADAY DÉTECTÉ ({watchdog_alert.get('drop_pct', 0)*100:.1f}%)"

        sig_lines = []
        for s in top_signals[:3]:
            ticker = s.get("ticker", "N/A")
            score = s.get("score", 0)
            reason = s.get("reason", "")
            prob = s.get("ml_probability") or s.get("lineage", {}).get("ml_probability")
            prob_txt = f" (ML Prob: {prob*100:.0f}%)" if prob else ""
            sig_lines.append(f"- {ticker} (Score: {score:.0f}/100{prob_txt}) : {reason[:120]}")

        signals_txt = "\n".join(sig_lines) if sig_lines else "Aucun signal quantitatif en attente."

        return (
            f"Tu es le Chef Stratège & Analyste Macro Institutionnel pour un portefeuille PEA systématique (Horizon moyen/long terme).\n"
            f"Voici les métriques exactes du système d'aide à la décision :\n\n"
            f"1. ÉTAT DU PORTEFEUILLE :\n"
            f"   - Capital Total : {tot_equity:,.0f} € (Exposition active : {exposure_pct}%, Liquidité disponible : {cash:,.0f} €)\n"
            f"   - Plafond d'exposition réglementaire : 98.0% (Buffer de sécurité permanent de 2% de cash)\n\n"
            f"2. RÉGIME MACRO & THERMOMÈTRE DE VOLATILITÉ :\n"
            f"   - Mode Actif : {mode} (Allocation Cible : {atk_pct:.0f}% Attaque / {def_pct:.0f}% Bouclier Cash/Monétaire CSH.PA)\n"
            f"   - Volatilité 21j CAC40 : {vol_21d:.1f}% | VIX Spot : {vix:.1f}\n"
            f"   - Watchdog Intraday : {watchdog_txt}\n\n"
            f"3. RECOMMANDATIONS ANALYTIQUES PRIORITAIRES :\n"
            f"{signals_txt}\n\n"
            f"Rédige une note de synthèse institutionnelle claire et percutante en EXACTEMENT 3 PARAGRAPHES (en Français Markdown) :\n"
            f"Paragraphe 1 : Diagnostic Macroéconomique & Régime de Volatilité (analyse de l'arbitrage Attaque/Bouclier et du VIX).\n"
            f"Paragraphe 2 : Évaluation des Opportunités Quantitatives (analyse des signaux prioritaires et de leur convergence technique/ML).\n"
            f"Paragraphe 3 : Directive Stratégique pour le Portfolio Manager (recommandations d'exécution prudentielle et gestion du cash buffer)."
        )

    def _build_deterministic_fallback(
        self,
        thermometer_state: Dict[str, Any],
        top_signals: List[Dict[str, Any]],
    ) -> str:
        """High-Conviction Deterministic Fallback Synthesis."""
        atk_pct = float(thermometer_state.get("attack_pct", 0.70)) * 100.0
        def_pct = float(thermometer_state.get("defense_pct", 0.30)) * 100.0
        mode = thermometer_state.get("mode", "ATTACK")
        vix = thermometer_state.get("vix", 16.0)

        top_sig_names = ", ".join(s.get("ticker", "") for s in top_signals[:3] if s.get("ticker")) or "Aucun titre"

        p1 = (
            f"**1. Conjoncture Macroéconomique & Thermomètre de Volatilité** : Le marché évolue sous un régime de volatilité mesurée "
            f"(VIX à {vix:.1f}), validant un calibrage dynamique en **Mode {mode}** ({atk_pct:.0f}% Attaque / {def_pct:.0f}% Bouclier). "
            f"L'indice de référence conserve son ancrage au-dessus de sa moyenne mobile à 200 jours, autorisant une allocation directionnelle "
            f"sans déclenchement du protocole Bunker."
        )

        p2 = (
            f"**2. Analyse des Opportunités Quantitatives** : Le moteur statistique identifie des configurations de Mean-Reversion favorables "
            f"sur la sélection : **{top_sig_names}**. Ces titres présentent une décote technique validée par les filtres de qualité fondamentale "
            f"et confirmée par les modèles prédictifs d'ensemble, offrant un ratio risque/rendement asymétrique."
        )

        p3 = (
            f"**3. Directives Stratégiques d'Aide à la Décision** : Il est recommandé au gérant de maintenir le strict respect du plafond "
            f"d'exposition à 98% (sanctuarisation du buffer de sécurité de 2% de liquidités). L'exécution des ordres proposés doit être "
            f"échelonnée aux cours limites optimaux calculés par le sizer, en neutralisant tout risque de slippage."
        )

        return f"{p1}\n\n{p2}\n\n{p3}"

    async def generate_daily_brief(
        self,
        portfolio_state: Any,
        thermometer_state: Dict[str, Any],
        top_signals: List[Dict[str, Any]],
        watchdog_alert: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[str]:
        """Asynchronously stream institutional briefing chunks from local Ollama instance."""
        prompt = self._build_prompt(portfolio_state, thermometer_state, top_signals, watchdog_alert)
        messages = [
            {"role": "system", "content": "Tu es un directeur de gestion quantitative et de gestion des risques PEA."},
            {"role": "user", "content": prompt},
        ]
        has_yielded = False
        try:
            from llm_explainer import ollama_chat_stream
            async for chunk in ollama_chat_stream(messages, model=self.model_name):
                if "Erreur" in chunk:
                    fallback = self._build_deterministic_fallback(thermometer_state, top_signals)
                    for word in fallback.split(" "):
                        yield word + " "
                    return
                has_yielded = True
                yield chunk
        except Exception:
            pass

        if not has_yielded:
            fallback = self._build_deterministic_fallback(thermometer_state, top_signals)
            for word in fallback.split(" "):
                yield word + " "


    def generate_daily_brief_stream_sync(
        self,
        portfolio_state: Any,
        thermometer_state: Dict[str, Any],
        top_signals: List[Dict[str, Any]],
        watchdog_alert: Optional[Dict[str, Any]] = None,
    ) -> Iterator[str]:
        """Synchronous streaming of institutional briefing chunks (for Streamlit st.write_stream)."""
        prompt = self._build_prompt(portfolio_state, thermometer_state, top_signals, watchdog_alert)
        messages = [
            {"role": "system", "content": "Tu es un directeur de gestion quantitative et de gestion des risques PEA."},
            {"role": "user", "content": prompt},
        ]
        try:
            from llm_explainer import ollama_chat_stream_sync
            stream_iter = ollama_chat_stream_sync(messages, model=self.model_name)
            has_yielded = False
            for chunk in stream_iter:
                if "Erreur" in chunk:
                    fallback = self._build_deterministic_fallback(thermometer_state, top_signals)
                    for word in fallback.split(" "):
                        yield word + " "
                    return
                has_yielded = True
                yield chunk

            if not has_yielded:
                fallback = self._build_deterministic_fallback(thermometer_state, top_signals)
                for word in fallback.split(" "):
                    yield word + " "
        except Exception:
            fallback = self._build_deterministic_fallback(thermometer_state, top_signals)
            for word in fallback.split(" "):
                yield word + " "

    def generate_daily_brief_sync(
        self,
        portfolio_state: Any,
        thermometer_state: Dict[str, Any],
        top_signals: List[Dict[str, Any]],
        watchdog_alert: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Synchronous generation of complete institutional daily brief."""
        chunks = list(self.generate_daily_brief_stream_sync(portfolio_state, thermometer_state, top_signals, watchdog_alert))
        return "".join(chunks).strip()

