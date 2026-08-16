# PEA Pollux — AI Orchestration, Priority Cascade, Red Team Debate & Post-Mortem
Generated: `2026-08-16 13:03 UTC` | File Count: `11`
Institutional Systematic Decision Support Architecture for French PEA.
---
## Included Files Index
- [04_orchestrator_ai/__init__.py](#file-04_orchestrator_ai-__init__-py)
- [04_orchestrator_ai/discord_notifier.py](#file-04_orchestrator_ai-discord_notifier-py)
- [04_orchestrator_ai/earnings_blackout.py](#file-04_orchestrator_ai-earnings_blackout-py)
- [04_orchestrator_ai/macro_veto.py](#file-04_orchestrator_ai-macro_veto-py)
- [04_orchestrator_ai/model_drift_monitor.py](#file-04_orchestrator_ai-model_drift_monitor-py)
- [04_orchestrator_ai/news_sentiment_llm.py](#file-04_orchestrator_ai-news_sentiment_llm-py)
- [04_orchestrator_ai/post_mortem_engine.py](#file-04_orchestrator_ai-post_mortem_engine-py)
- [04_orchestrator_ai/red_team_agent.py](#file-04_orchestrator_ai-red_team_agent-py)
- [04_orchestrator_ai/revocation_engine.py](#file-04_orchestrator_ai-revocation_engine-py)
- [04_orchestrator_ai/signal_priority_cascade.py](#file-04_orchestrator_ai-signal_priority_cascade-py)
- [04_orchestrator_ai/weekly_historian.py](#file-04_orchestrator_ai-weekly_historian-py)

---
## FILE: 04_orchestrator_ai/__init__.py
```python
"""AI Orchestration, Priority Cascade & Red Team Agents package for PEA Pollux."""

from .earnings_blackout import EarningsBlackoutEngine
from .macro_veto import MacroCalendarVeto
from .news_sentiment_llm import NewsSentimentScorer
from .post_mortem_engine import TradePostMortemEngine
from .red_team_agent import RedTeamDebateAgent
from .revocation_engine import RevocationEngine
from .signal_priority_cascade import SignalOrchestrator
from .weekly_historian import WeeklyHistorian

__all__ = [
    "EarningsBlackoutEngine",
    "MacroCalendarVeto",
    "NewsSentimentScorer",
    "RedTeamDebateAgent",
    "RevocationEngine",
    "SignalOrchestrator",
    "TradePostMortemEngine",
    "WeeklyHistorian",
]
```

## FILE: 04_orchestrator_ai/discord_notifier.py
```python
import requests
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "01_memory_core"))

from logging_setup import get_logger

logger = get_logger("discord_notifier")

# Colors
COLOR_GREEN = 59006
COLOR_RED = 16726832

def send_high_conviction_alert(signal_dict: dict, webhook_url: str):
    """
    Send a high conviction signal alert to Discord via Webhook.
    
    Args:
        signal_dict: Dictionary containing signal metadata:
            - ticker
            - direction (BUY/SELL)
            - score
            - current_price
            - atr_stop_loss
            - llm_reasoning (optional)
        webhook_url: The Discord Webhook URL.
    """
    if not webhook_url:
        logger.warning("No Discord Webhook URL provided. Skipping alert.")
        return
        
    ticker = signal_dict.get("ticker", "UNKNOWN")
    direction = signal_dict.get("direction", "BUY").upper()
    score = signal_dict.get("score", 0.0)
    current_price = signal_dict.get("current_price", 0.0)
    atr_stop = signal_dict.get("atr_stop_loss", 0.0)
    reasoning = signal_dict.get("llm_reasoning", "No LLM reasoning provided.")
    
    is_buy = direction == "BUY"
    color = COLOR_GREEN if is_buy else COLOR_RED
    title_emoji = "🟢" if is_buy else "🔴"
    
    embed = {
        "title": f"🚨 PEA Sniper Signal Alert: {title_emoji} {direction} {ticker}",
        "description": f"**High Conviction Signal Detected (>75%)**\n\n**LLM Guidance Insight:**\n*{reasoning}*",
        "color": color,
        "fields": [
            {
                "name": "📊 Model Confidence Score",
                "value": f"**{score:.1f}%**",
                "inline": True
            },
            {
                "name": "💰 Current Price",
                "value": f"**{current_price:.2f} €**",
                "inline": True
            },
            {
                "name": "🛡️ ATR Stop-Loss",
                "value": f"**{atr_stop:.2f} €**",
                "inline": True
            }
        ],
        "footer": {
            "text": "PEA Pollux Automated Orchestrator",
            "icon_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c3/Python-logo-notext.svg/1200px-Python-logo-notext.svg.png"
        }
    }
    
    payload = {
        "content": f"<@&EVERYONE> 🚨 {direction} Alert for **{ticker}**",
        "embeds": [embed]
    }
    
    try:
        response = requests.post(webhook_url, json=payload, timeout=10)
        if response.status_code in (200, 204):
            logger.info("Successfully sent Discord high-conviction alert for %s", ticker)
        else:
            logger.warning("Failed to send Discord alert, status code: %s", response.status_code)
    except Exception as e:
        logger.exception("Error sending Discord alert: %s", e)
```

## FILE: 04_orchestrator_ai/earnings_blackout.py
```python
"""Per-ticker earnings / dividend blackout (same pattern as MacroVetoEngine).

Blocks new satellite buys when a corporate event for that ticker falls within
``EARNINGS_BLACKOUT_DAYS``. Calendar is maintained in
``config/earnings_calendar.yaml`` (manual seed; later auto-synced from an API).
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Dict, Tuple

import yaml

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_DIR = _PROJECT_ROOT / "config"


class EarningsBlackoutEngine:
    """Vetoes buys near ticker-specific earnings/dividend dates."""

    def __init__(self, config_dir: str | Path | None = None) -> None:
        config_path = Path(config_dir) if config_dir else _DEFAULT_CONFIG_DIR
        risk = self._load_yaml(config_path / "risk_params.yaml")
        cal_raw = self._load_yaml(config_path / "earnings_calendar.yaml")
        self.blackout_days: int = int(risk.get("EARNINGS_BLACKOUT_DAYS", 2))
        # ticker -> {date -> event_name}
        self.calendar: Dict[str, Dict[dt.date, str]] = self._parse_calendar(cal_raw)
        logger.debug(
            "EarningsBlackoutEngine: window=%d day(s), %d ticker(s).",
            self.blackout_days,
            len(self.calendar),
        )

    @staticmethod
    def _load_yaml(path: Path) -> dict:
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    @staticmethod
    def _parse_calendar(raw: dict) -> Dict[str, Dict[dt.date, str]]:
        """Accept ``events: { TICKER: { YYYY-MM-DD: name } }``."""
        events = raw.get("events", raw) if isinstance(raw, dict) else {}
        parsed: Dict[str, Dict[dt.date, str]] = {}
        if not isinstance(events, dict):
            return parsed
        for ticker, dates in events.items():
            if not isinstance(dates, dict):
                continue
            bucket: Dict[dt.date, str] = {}
            for key, name in dates.items():
                if isinstance(key, dt.datetime):
                    event_date = key.date()
                elif isinstance(key, dt.date):
                    event_date = key
                else:
                    try:
                        event_date = dt.date.fromisoformat(str(key))
                    except ValueError:
                        continue
                bucket[event_date] = str(name)
            if bucket:
                parsed[str(ticker)] = bucket
        return parsed

    def check_veto(
        self, ticker: str, target_date: dt.date
    ) -> Tuple[bool, str]:
        """Return ``(True, reason)`` if ``ticker`` is in an earnings blackout."""
        if isinstance(target_date, dt.datetime):
            target_date = target_date.date()
        events = self.calendar.get(ticker) or {}
        for event_date, name in sorted(events.items()):
            delta = (event_date - target_date).days
            if 0 <= delta <= self.blackout_days:
                if delta == 0:
                    reason = f"EARNINGS BLACKOUT: {name} today ({ticker})"
                elif delta == 1:
                    reason = f"EARNINGS BLACKOUT: {name} in 1 day ({ticker})"
                else:
                    reason = (
                        f"EARNINGS BLACKOUT: {name} in {delta} days ({ticker})"
                    )
                logger.info("%s", reason)
                return True, reason
        return False, "Clear"
```

## FILE: 04_orchestrator_ai/macro_veto.py
```python
"""Macro Veto Engine for PEA Sniper Terminal V-Prime.

Blocks new offensive signals when a high-impact macro event (ECB/FED decision,
CPI, NFP) falls within a configurable window. Running this cheap check before
the heavy correlation math keeps the cascade CPU-efficient.

Pure logical routing: no LLMs, no APIs. All paths use ``pathlib`` for
cross-platform compatibility (Windows x64/ARM and Linux).
"""

import datetime as dt
import logging
from pathlib import Path
from typing import Dict, Tuple

import yaml

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_DIR = _PROJECT_ROOT / "config"


class MacroVetoEngine:
    """Vetoes new trades near scheduled high-impact macro events.

    Attributes:
        veto_days_before: Number of days before an event during which new
            trades are blocked.
        calendar: Mapping of event date -> event name.
    """

    def __init__(self, config_dir: str | Path | None = None) -> None:
        """Load the veto window and the macro calendar.

        Args:
            config_dir: Path to the ``config`` directory. Defaults to
                ``<project_root>/config``.
        """
        config_path = Path(config_dir) if config_dir else _DEFAULT_CONFIG_DIR

        risk = self._load_yaml(config_path / "risk_params.yaml")
        calendar_raw = self._load_yaml(config_path / "macro_calendar.yaml")

        self.veto_days_before: int = int(risk["MACRO_VETO_DAYS_BEFORE"])
        self.calendar: Dict[dt.date, str] = self._parse_calendar(calendar_raw)

        logger.debug(
            "MacroVetoEngine loaded: window=%d day(s), %d event(s).",
            self.veto_days_before,
            len(self.calendar),
        )

    @staticmethod
    def _load_yaml(path: Path) -> dict:
        """Load a YAML file into a dict, raising a clear error if missing."""
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    @staticmethod
    def _parse_calendar(raw: dict) -> Dict[dt.date, str]:
        """Normalize raw YAML into a ``date -> name`` mapping.

        Accepts either a top-level ``events:`` mapping or a bare ``date: name``
        mapping. Date keys may be ``datetime.date`` (parsed by PyYAML) or ISO
        strings.
        """
        events = raw.get("events", raw) if isinstance(raw, dict) else {}
        parsed: Dict[dt.date, str] = {}
        for key, name in events.items():
            if isinstance(key, dt.datetime):
                event_date = key.date()
            elif isinstance(key, dt.date):
                event_date = key
            else:
                event_date = dt.date.fromisoformat(str(key))
            parsed[event_date] = str(name)
        return parsed

    def check_veto(self, target_date: dt.date) -> Tuple[bool, str]:
        """Check whether a trade on ``target_date`` must be vetoed.

        A veto applies when an event is scheduled on ``target_date`` or within
        the next ``veto_days_before`` days.

        Args:
            target_date: The date the trade would be placed.

        Returns:
            tuple[bool, str]: ``(True, reason)`` if vetoed, else
            ``(False, "Clear")``.
        """
        if isinstance(target_date, dt.datetime):
            target_date = target_date.date()

        for event_date, name in sorted(self.calendar.items()):
            delta = (event_date - target_date).days
            if 0 <= delta <= self.veto_days_before:
                if delta == 0:
                    reason = f"VETO: {name} today"
                elif delta == 1:
                    reason = f"VETO: {name} in 1 day"
                else:
                    reason = f"VETO: {name} in {delta} days"
                logger.info("Macro veto for %s -> %s", target_date, reason)
                return True, reason

        return False, "Clear"


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    engine = MacroVetoEngine()
    print("Window (days before):", engine.veto_days_before)
    print("Events loaded:", len(engine.calendar))

    # ECB Rate Decision seeded on 2026-07-16.
    for d in ("2026-07-14", "2026-07-15", "2026-07-16", "2026-07-25"):
        vetoed, msg = engine.check_veto(dt.date.fromisoformat(d))
        print(f"{d}: vetoed={vetoed} -> {msg}")
```

## FILE: 04_orchestrator_ai/model_drift_monitor.py
```python
import json
import logging
from pathlib import Path

logger = logging.getLogger("model_drift_monitor")

_ROOT = Path(__file__).resolve().parent.parent

def check_model_drift(db_path: Path | None = None) -> bool:
    """
    Evaluates if the current ML models are losing predictive power.
    Returns True if drift is detected (Accuracy < 0.55 on either model).
    """
    db_path = db_path or (_ROOT / "database")
    
    tactical_path = db_path / "xgboost_model_tactical.json"
    structural_path = db_path / "xgboost_model_structural.json"
    
    drift_detected = False
    
    for path, name in [(tactical_path, "Tactical"), (structural_path, "Structural")]:
        if not path.exists():
            logger.warning(f"{name} ML model artifact not found. Needs training.")
            drift_detected = True
            continue
            
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                acc = float(data.get("metrics", {}).get("accuracy", 0.0))
                
                if acc < 0.55:
                    logger.warning(f"🚨 DRIFT DETECTED: {name} model accuracy dropped to {acc:.2%}")
                    drift_detected = True
                else:
                    logger.info(f"✅ {name} model healthy. Accuracy: {acc:.2%}")
        except Exception as e:
            logger.error(f"Failed to read metrics for {name}: {e}")
            drift_detected = True
            
    return drift_detected

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(_ROOT / "01_memory_core"))
    from logging_setup import get_logger
    logger = get_logger("model_drift_monitor")
    
    is_drifting = check_model_drift()
    if is_drifting:
        logger.warning("Pipeline requires retraining due to model drift.")
        sys.exit(1)
    else:
        logger.info("All models are performing optimally.")
        sys.exit(0)
```

## FILE: 04_orchestrator_ai/news_sentiment_llm.py
```python
"""Financial News Sentiment Scorer for PEA Pollux using ProsusAI/finbert.

Provides 100% deterministic, offline, institutional-grade sentiment classification
mapping strictly to the [-100, +100] quantitative conviction scale.

Model: ProsusAI/finbert (BERT-based financial domain language model)
Output mapping:
  - positive (prob p): +p * 100.0
  - negative (prob p): -p * 100.0
  - neutral: 0.0
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent.parent
for _d in ("00_data_sensors", "01_memory_core", "02_quant_engine", "03_risk_portfolio"):
    sys.path.insert(0, str(_ROOT / _d))

logger = logging.getLogger(__name__)

_FINBERT_PIPELINE = None


def get_finbert_pipeline():
    """Lazily load and cache the ProsusAI/finbert sentiment analysis pipeline."""
    global _FINBERT_PIPELINE
    if _FINBERT_PIPELINE is None:
        try:
            from transformers import pipeline
            logger.info("Initializing ProsusAI/finbert transformer pipeline...")
            _FINBERT_PIPELINE = pipeline(
                "sentiment-analysis",
                model="ProsusAI/finbert",
                tokenizer="ProsusAI/finbert",
                top_k=None,
            )
            logger.info("ProsusAI/finbert pipeline successfully loaded.")
        except Exception as exc:
            logger.warning("FinBERT pipeline could not be loaded: %s (using heuristic fallback)", exc)
            _FINBERT_PIPELINE = False
    return _FINBERT_PIPELINE if _FINBERT_PIPELINE is not False else None


class NewsSentimentScorer:
    """Quantitative sentiment scorer powered by ProsusAI/finbert transformer."""

    def __init__(self, portfolio_db=None) -> None:
        if portfolio_db is None:
            try:
                from sqlite_portfolio import PortfolioDB
                self.portfolio_db = PortfolioDB()
            except Exception:
                self.portfolio_db = None
        else:
            self.portfolio_db = portfolio_db

    def score_single_headline(self, headline: str) -> Tuple[float, str]:
        """Score a single headline string using FinBERT or deterministic keywords.

        Args:
            headline: Text of the financial headline.

        Returns:
            Tuple[float, str]: (score in [-100, 100], label string).
        """
        try:
            from text_cleaner import clean_financial_text
            cleaned = clean_financial_text(headline, max_chars=1500)
        except Exception:
            cleaned = str(headline)[:1500].strip()

        if not cleaned:
            return 0.0, "neutral"

        pipe = get_finbert_pipeline()
        if pipe is not None:
            try:
                # pipe returns e.g. [[{'label': 'positive', 'score': 0.89}, {'label': 'negative', ...}, ...]]
                outputs = pipe(cleaned)
                if outputs and isinstance(outputs[0], list):
                    # Sort by score descending to get top label
                    sorted_preds = sorted(outputs[0], key=lambda x: x.get("score", 0.0), reverse=True)
                    top_pred = sorted_preds[0]
                elif outputs and isinstance(outputs[0], dict):
                    top_pred = outputs[0]
                else:
                    top_pred = {"label": "neutral", "score": 1.0}

                label = str(top_pred.get("label", "neutral")).lower()
                prob = float(top_pred.get("score", 0.0))

                if label == "positive":
                    return round(prob * 100.0, 1), "positive"
                elif label == "negative":
                    return round(prob * -100.0, 1), "negative"
                else:
                    return 0.0, "neutral"
            except Exception as exc:
                logger.debug("FinBERT inference error on '%s': %s", cleaned[:50], exc)

        # Heuristic financial keywords fallback
        lower = cleaned.lower()
        bull_words = ["hausse", "croissance", "record", "dividende", "bénéfice", "achat", "surperformance", "rehausse", "upgrade", "beat", "surge", "gain", "profit", "bullish"]
        bear_words = ["baisse", "chute", "perte", "déficit", "avertissement", "dégradation", "downgrade", "miss", "plunge", "fraud", "investigation", "litigation", "bearish"]
        
        pos_hits = sum(1 for w in bull_words if w in lower)
        neg_hits = sum(1 for w in bear_words if w in lower)

        if pos_hits > neg_hits:
            return 50.0 + min(40.0, pos_hits * 15.0), "positive"
        elif neg_hits > pos_hits:
            return -50.0 - min(40.0, neg_hits * 15.0), "negative"
        return 0.0, "neutral"

    async def analyze_news(
        self, ticker: str, news_headlines: List[str], source: str = "finbert"
    ) -> float:
        """Score the aggregate sentiment of headlines for one ticker using FinBERT.

        Args:
            ticker: Asset ticker (e.g. 'MC.PA').
            news_headlines: List of headline strings.
            source: Ingestion source tag.

        Returns:
            float: Normalized aggregate sentiment score in [-100.0, +100.0].
        """
        headlines = [h.strip() for h in (news_headlines or []) if h and h.strip()]
        if not headlines:
            logger.debug("No headlines for %s; neutral sentiment (0).", ticker)
            return 0.0

        scores: List[float] = []
        for h in headlines[:15]:
            h_score, _ = self.score_single_headline(h)
            scores.append(h_score)

            if self.portfolio_db is not None:
                try:
                    self.portfolio_db.upsert_sentiment_history(
                        ticker=ticker,
                        score=h_score,
                        source=source,
                        headline=h[:120],
                    )
                except Exception as exc:
                    logger.debug("Failed to upsert sentiment history for %s: %s", ticker, exc)

        final_score = float(sum(scores) / len(scores)) if scores else 0.0
        final_score = max(-100.0, min(100.0, round(final_score, 1)))
        logger.info("FinBERT sentiment for %s: %.1f (from %d headlines).", ticker, final_score, len(headlines))
        return final_score


def score_news_batch(db, limit: int = 50) -> int:
    """Batch score unprocessed news in SQLite using FinBERT.

    Args:
        db: PortfolioDB instance.
        limit: Max news items to process.

    Returns:
        int: Number of news items successfully scored.
    """
    if db is None:
        return 0
    unproc = db.get_unprocessed_news(limit=limit) if hasattr(db, "get_unprocessed_news") else []
    if not unproc:
        return 0
    scorer = NewsSentimentScorer(portfolio_db=db)
    updates = []
    for item in unproc:
        title = item.get("title", "")
        content = item.get("content", "")
        text = f"{title}. {content}".strip()
        score, label = scorer.score_single_headline(text)
        news_id = item.get("id")
        ticker = item.get("ticker", "MARCHE")
        if news_id:
            updates.append({
                "id": news_id,
                "sentiment_score": score,
                "sentiment_label": label,
            })
            if hasattr(db, "upsert_sentiment_history"):
                try:
                    db.upsert_sentiment_history(
                        ticker=ticker,
                        score=score,
                        source=item.get("source", "batch"),
                        headline=title[:120],
                    )
                except Exception:
                    pass

    if updates and hasattr(db, "update_news_sentiment"):
        db.update_news_sentiment(updates)
    elif updates and hasattr(db, "mark_news_processed"):
        for u in updates:
            db.mark_news_processed(u["id"], sentiment_score=u["sentiment_score"], sentiment_label=u["sentiment_label"])

    return len(updates)





class OpenRouterClient:
    """Optional client for OpenRouter generative queries (Red Team debate, Friday CIO digest, AI ticker summaries)."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None) -> None:
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        self.model = model or os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
        self.is_configured = bool(self.api_key and self.api_key.strip())

    def query_sync(self, prompt: str, max_tokens: int = 300) -> Optional[str]:
        """Synchronously query the OpenRouter API."""
        if not self.is_configured:
            return None
        try:
            import requests
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/Polluxgnr/Peatrading",
                "X-Title": "PEA Pollux Terminal",
            }
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.2,
            }
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=12,
            )
            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices") or []
                if choices:
                    return choices[0].get("message", {}).get("content", "").strip()
        except Exception as exc:
            logger.debug("OpenRouter query failed: %s", exc)
        return None


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    scorer = NewsSentimentScorer()
    demo_headlines = [
        "LVMH records strong Q1 sales growth beating all analyst expectations",
        "European luxury sector downgraded on slowing Chinese demand",
    ]
    res = asyncio.run(scorer.analyze_news("MC.PA", demo_headlines))
    print("Aggregate FinBERT sentiment score:", res)
```

## FILE: 04_orchestrator_ai/post_mortem_engine.py
```python
"""Trade Post-Mortem & Retrospective Analysis Engine for PEA Sniper Terminal.

Automatically triggered upon closing a position (via ATR Stop Loss or Profit-Shaving)
to analyze trade execution, holding period efficiency, entry quality, and lessons learned,
storing permanent audit records in the ``trade_post_mortems`` SQLite table.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "database" / "portfolio.db"


class TradePostMortemEngine:
    """Evaluates closed trades and persists retrospective analytics."""

    def __init__(self, db_path: str | Path = _DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create trade_post_mortems table."""
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS trade_post_mortems (
                        id                  TEXT PRIMARY KEY,
                        ticker              TEXT NOT NULL,
                        entry_date          TEXT NOT NULL,
                        exit_date           TEXT NOT NULL,
                        holding_days        INTEGER NOT NULL,
                        entry_price         REAL NOT NULL,
                        exit_price          REAL NOT NULL,
                        pnl_eur             REAL NOT NULL,
                        pnl_pct             REAL NOT NULL,
                        exit_reason         TEXT NOT NULL,
                        entry_score         REAL,
                        mae_pct             REAL,
                        mfe_pct             REAL,
                        lessons_learned     TEXT,
                        created_at          TEXT NOT NULL
                    );
                    """
                )
        except sqlite3.Error as exc:
            logger.debug("Failed to init trade_post_mortems table: %s", exc)

    def generate_post_mortem(
        self,
        trade_id: str,
        ticker: str,
        entry_date: str,
        exit_date: str,
        entry_price: float,
        exit_price: float,
        shares: int,
        exit_reason: str,
        entry_score: float = 75.0,
        mae_pct: float = 0.0,
        mfe_pct: float = 0.0,
    ) -> Dict:
        """Generate and save post-mortem record for a completed trade."""
        pnl_eur = (exit_price - entry_price) * shares
        pnl_pct = (exit_price / entry_price - 1.0) * 100.0 if entry_price > 0 else 0.0

        # Estimate holding duration
        try:
            d0 = datetime.fromisoformat(entry_date[:10])
            d1 = datetime.fromisoformat(exit_date[:10])
            holding_days = max(1, (d1 - d0).days)
        except Exception:
            holding_days = 1

        # Synthesize qualitative lesson
        if pnl_eur > 0:
            lesson = (
                f"Trade gagnant (+{pnl_pct:.1f}% en {holding_days}j). "
                f"La règle de prise de bénéfice ({exit_reason}) a capturé l'impulsion haussière avec succès."
            )
        else:
            lesson = (
                f"Trade clôturé en perte ({pnl_pct:.1f}% en {holding_days}j). "
                f"Coupe-circuit {exit_reason} exécuté avec discipline, limitant l'érosion du capital."
            )
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO trade_post_mortems (
                        id, ticker, entry_date, exit_date, holding_days,
                        entry_price, exit_price, pnl_eur, pnl_pct, exit_reason,
                        entry_score, mae_pct, mfe_pct, lessons_learned, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        exit_price = excluded.exit_price,
                        pnl_eur = excluded.pnl_eur,
                        pnl_pct = excluded.pnl_pct,
                        lessons_learned = excluded.lessons_learned;
                    """,
                    (
                        trade_id,
                        ticker,
                        entry_date,
                        exit_date,
                        holding_days,
                        entry_price,
                        exit_price,
                        pnl_eur,
                        pnl_pct,
                        exit_reason,
                        entry_score,
                        mae_pct,
                        mfe_pct,
                        lesson,
                        now,
                    ),
                )
            logger.info("Post-mortem saved for trade %s on %s (PnL: %+.2f EUR)", trade_id, ticker, pnl_eur)

            # --- Autonomous Reinforcement Feedback Loop (Contextual Bandit) ---
            try:
                from contextual_bandit import UCBBandit
                bandit = UCBBandit()

                # Infer strategy arm from exit reason or metadata
                er_upper = str(exit_reason).upper()
                if "STAT_ARB" in er_upper or "COINT" in er_upper or "PAIR" in er_upper:
                    strategy_arm = "context"
                elif "BREAKOUT" in er_upper:
                    strategy_arm = "breakout"
                elif "TREND" in er_upper:
                    strategy_arm = "trend"
                else:
                    strategy_arm = "mean_reversion"

                # Infer regime: BULL if profit shave / gain, VOLATILE if stop loss
                if pnl_eur > 0:
                    current_regime = "BULL"
                elif "ATR_STOP" in er_upper or pnl_pct < -5.0:
                    current_regime = "VOLATILE"
                else:
                    current_regime = "BEAR"

                # Reward is proportional to realized PnL %
                reward = float(pnl_pct)
                bandit.update_reward(regime=current_regime, arm=strategy_arm, reward=reward)
                logger.info("Bandit feedback loop updated: regime=%s, arm=%s, reward=%+.2f%%", current_regime, strategy_arm, reward)
            except Exception as b_exc:
                logger.debug("Bandit update skipped: %s", b_exc)

        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to save post-mortem for %s: %s", trade_id, exc)

        return {
            "id": trade_id,
            "ticker": ticker,
            "pnl_eur": round(pnl_eur, 2),
            "pnl_pct": round(pnl_pct, 2),
            "holding_days": holding_days,
            "exit_reason": exit_reason,
            "lessons_learned": lesson,
        }

    def fetch_recent_post_mortems(self, limit: int = 20) -> List[Dict]:
        """Retrieve recent trade post-mortems from SQLite."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM trade_post_mortems ORDER BY exit_date DESC LIMIT ?;",
                    (limit,),
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to fetch post-mortems: %s", exc)
            return []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    pm = TradePostMortemEngine()
    rec = pm.generate_post_mortem(
        trade_id="TEST_001",
        ticker="MC.PA",
        entry_date="2026-06-01",
        exit_date="2026-07-15",
        entry_price=600.0,
        exit_price=660.0,
        shares=3,
        exit_reason="PROFIT_SHAVE_20PCT",
    )
    print("Generated Post-Mortem:", rec)
```

## FILE: 04_orchestrator_ai/red_team_agent.py
```python
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
```

## FILE: 04_orchestrator_ai/revocation_engine.py
```python
"""Revocation Engine for PEA Sniper Terminal V-Prime.

Implements the Anti-Stale logic re-run at each daily pass (09:00, 13:30, 17:10):
a signal is REVOKED if the price drifts too far from the emission price, or
EXPIRED once it outlives its validity window.

Pure logical routing: no LLMs, no APIs. All paths use ``pathlib``.
"""

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

_CORE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "01_memory_core"
)
sys.path.insert(0, _CORE_DIR)

from data_models import Signal, SignalStatus  # noqa: E402

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_DIR = _PROJECT_ROOT / "config"
_PRICE_DRIFT_LIMIT = 0.03  # 3% intraday drift revokes a signal.


class RevocationEngine:
    """Revokes or expires signals that are no longer actionable.

    Attributes:
        validity_hours: Number of hours a signal remains valid after emission.
    """

    def __init__(self, config_dir: str | Path | None = None) -> None:
        """Load the signal validity window from ``risk_params.yaml``.

        Args:
            config_dir: Path to the ``config`` directory. Defaults to
                ``<project_root>/config``.
        """
        config_path = Path(config_dir) if config_dir else _DEFAULT_CONFIG_DIR
        risk = self._load_yaml(config_path / "risk_params.yaml")
        self.validity_hours: float = float(risk["SIGNAL_VALIDITY_HOURS"])
        logger.debug("RevocationEngine loaded: validity=%.1fh", self.validity_hours)

    @staticmethod
    def _load_yaml(path: Path) -> dict:
        """Load a YAML file into a dict, raising a clear error if missing."""
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    def evaluate_signal(
        self, signal: Signal, current_price: float, original_price: float
    ) -> Signal:
        """Re-evaluate a signal for price drift and time decay.

        Args:
            signal: The signal to evaluate (mutated in place and returned).
            current_price: Latest market price for the ticker.
            original_price: Price at the moment the signal was emitted.

        Returns:
            Signal: The same signal object, with updated ``status``/``reason``.
        """
        # Rule 1 - Price drift (revocation takes precedence over expiry).
        if original_price and original_price > 0:
            drift = abs(current_price - original_price) / original_price
            if drift > _PRICE_DRIFT_LIMIT:
                signal.status = SignalStatus.REVOKED
                signal.reason = f"{signal.reason} | REVOKED: Price drifted > 3%".strip(" |")
                logger.info(
                    "Signal %s REVOKED: %s drifted %.2f%% (%.2f -> %.2f).",
                    signal.id[:8],
                    signal.ticker,
                    drift * 100,
                    original_price,
                    current_price,
                )
                return signal

        # Rule 2 - Time decay.
        now = datetime.now(timezone.utc)
        created = signal.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_hours = (now - created).total_seconds() / 3600.0
        if age_hours > self.validity_hours:
            signal.status = SignalStatus.EXPIRED
            signal.reason = f"{signal.reason} | EXPIRED: Older than validity window".strip(" |")
            logger.info(
                "Signal %s EXPIRED: age %.1fh > %.1fh.",
                signal.id[:8],
                age_hours,
                self.validity_hours,
            )
            return signal

        logger.debug("Signal %s still valid (age %.1fh).", signal.id[:8], age_hours)
        return signal


if __name__ == "__main__":
    from datetime import timedelta

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    engine = RevocationEngine()

    print("--- Rule 1: price drift ---")
    s1 = Signal(ticker="MC.PA", signal_type="BUY", score=80.0,
                reason="Mean-reversion setup")
    s1 = engine.evaluate_signal(s1, current_price=94.0, original_price=100.0)
    print(f"status={s1.status.value} | reason='{s1.reason}'")

    print("\n--- Rule 2: time decay ---")
    s2 = Signal(ticker="AI.PA", signal_type="BUY", score=90.0,
                reason="Mean-reversion setup")
    s2.created_at = datetime.now(timezone.utc) - timedelta(hours=13)
    s2 = engine.evaluate_signal(s2, current_price=100.5, original_price=100.0)
    print(f"status={s2.status.value} | reason='{s2.reason}'")

    print("\n--- Still valid ---")
    s3 = Signal(ticker="OR.PA", signal_type="BUY", score=75.0,
                reason="Mean-reversion setup")
    s3 = engine.evaluate_signal(s3, current_price=100.5, original_price=100.0)
    print(f"status={s3.status.value} | reason='{s3.reason}'")
```

## FILE: 04_orchestrator_ai/signal_priority_cascade.py
```python
"""Signal Priority Cascade for PEA Sniper Terminal V-Prime.

The strict conductor. Raw signals flow through an ordered, CPU-optimal cascade:

    0. Price sanity      (reject non-positive / missing marks)
    1. VIX panic         (market-wide emergency brake — CorrelationFirewall)
    2. Macro Veto        (cheap date lookup)
    2b. Earnings blackout (per-ticker corporate calendar)
    2c. Max positions    (satellite line count cap)
    2d. Min liquidity    (ADV € floor)
    3. Sector limit      (cheap arithmetic)
    4. Correlation       (heavy Pearson math — only if still alive)
    5. PEA sizing        (integer shares vs available cash)

This is the ONLY module that finalizes a signal's ``status``, ``target_qty``
and ``reason``. Pure logical routing: no LLMs, no APIs. All paths use
``pathlib``/``os.path`` for cross-platform compatibility.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Union

import pandas as pd
import yaml


# --- Cross-package imports (directories start with digits) --------------------
_ROOT = Path(__file__).resolve().parent.parent
for _sub in ("00_data_sensors", "01_memory_core", "02_quant_engine", "03_risk_portfolio", "04_orchestrator_ai"):
    sys.path.insert(0, os.path.join(str(_ROOT), _sub))

from data_models import PortfolioState, Signal, SignalStatus  # noqa: E402
from correlation_firewall import CorrelationFirewall  # noqa: E402
from pea_position_sizer import PeaSizer  # noqa: E402
from macro_veto import MacroVetoEngine  # noqa: E402
from earnings_blackout import EarningsBlackoutEngine  # noqa: E402
from drawdown_breaker import DrawdownBreaker  # noqa: E402
from fundamentals_api import FundamentalsSensor  # noqa: E402
from risk_config import load_and_validate_risk_params  # noqa: E402
from market_regime import VolatilityRegimeSentinel  # noqa: E402

try:
    from amf_short_scraper import AmfShortScraper
except ImportError:
    try:
        from scrapers.amf_short_scraper import AmfShortScraper
    except ImportError:
        AmfShortScraper = None

try:
    from openfigi_mapper import OpenFigiMapper
except ImportError:
    OpenFigiMapper = None

try:
    from ml_trainer import predict_probability_with_shap, predict_anomaly
except ImportError:
    predict_probability_with_shap = None
    predict_anomaly = None

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_DIR = _ROOT / "config"


class SignalOrchestrator:
    """Routes raw signals through veto, correlation and sizing checks."""

    def __init__(
        self,
        config_dir: str | Path | None = None,
        portfolio_db=None,
        timeseries_db=None,
    ) -> None:
        """Initialize the sub-engines that make up the cascade."""
        config_path = Path(config_dir) if config_dir else _DEFAULT_CONFIG_DIR
        self.config_dir = config_path
        self.portfolio_db = portfolio_db
        self.timeseries_db = timeseries_db

        risk_cfg = load_and_validate_risk_params(config_path / "risk_params.yaml")
        self.risk_cfg = risk_cfg
        self.core_ticker: str = str(risk_cfg.CORE_TICKER)
        self.max_positions_total: int = int(risk_cfg.MAX_POSITIONS_TOTAL)
        self.min_liquidity_adv: float = float(risk_cfg.MIN_LIQUIDITY_ADV)

        self.macro_veto = MacroVetoEngine(config_path)
        self.earnings_blackout = EarningsBlackoutEngine(config_path)
        self.firewall = CorrelationFirewall(config_path)
        self.sizer = PeaSizer(config_path)
        self.drawdown_breaker = DrawdownBreaker(
            daily_max_loss=risk_cfg.DAILY_MAX_LOSS_PCT,
            weekly_max_loss=risk_cfg.WEEKLY_MAX_LOSS_PCT,
            monthly_max_loss=risk_cfg.MONTHLY_MAX_LOSS_PCT,
        )
        self.fundamentals_sensor = FundamentalsSensor()
        self.vol_sentinel = VolatilityRegimeSentinel(window=252)

        if AmfShortScraper is not None:
            self.amf_scraper = AmfShortScraper()
        else:
            self.amf_scraper = None

        if OpenFigiMapper is not None:
            self.figi_mapper = OpenFigiMapper(config_path.parent / "database" / "portfolio.db")
        else:
            self.figi_mapper = None


        logger.debug("SignalOrchestrator initialized with validated config at %s", config_path)

    @staticmethod
    def _reject(signal: Signal, reason: str) -> Signal:
        signal.status = SignalStatus.REJECTED
        signal.reason = (f"{signal.reason} | {reason}").strip(" |")
        signal.target_qty = 0
        if hasattr(signal, "lineage") and isinstance(signal.lineage, dict):
            signal.lineage["rejection_reason"] = reason
            signal.lineage["status"] = SignalStatus.REJECTED.value
        logger.info("%s %s: %s", signal.ticker, signal.id[:8], reason)
        return signal

    def _historical_volatility(self, ticker: str, days: int = 60) -> float | None:
        """Annualized stdev of daily returns for a ticker (or ``None``).

        Args:
            ticker: Ticker to measure.
            days: Lookback window in trading days.

        Returns:
            float | None: Annualized volatility (e.g. 0.28), or ``None`` when
            history is unavailable.
        """
        if self.timeseries_db is None:
            return None
        try:
            df = self.timeseries_db.get_historical_prices(ticker, days=days)
            if df is None or df.empty or "Close" not in df or len(df) < 10:
                return None
            returns = df["Close"].astype(float).pct_change().dropna()
            if returns.empty:
                return None
            return float(returns.std() * (252 ** 0.5))
        except Exception:  # noqa: BLE001
            logger.debug("Volatility unavailable for %s.", ticker)
            return None

    def _avg_daily_euro_volume(self, ticker: str, days: int = 20) -> float | None:
        """Approximate ADV in EUR = mean(Close * Volume) over ``days``."""
        if self.timeseries_db is None:
            return None
        try:
            df = self.timeseries_db.get_historical_prices(ticker, days=days)
            if df is None or df.empty:
                return None
            if "Close" not in df.columns or "Volume" not in df.columns:
                return None
            close = df["Close"].astype(float)
            vol = df["Volume"].astype(float)
            adv = (close * vol).dropna()
            if adv.empty:
                return None
            return float(adv.mean())
        except Exception:  # noqa: BLE001
            return None

    def _get_vix_history(self, days: int = 252) -> pd.Series | None:
        """Fetch historical VIX/V2TX series for rolling volatility percentile ranking."""
        if self.timeseries_db is not None:
            try:
                for sym in ("^V2TX", "^VIX"):
                    df = self.timeseries_db.get_historical_prices(sym, days=days)
                    if df is not None and not df.empty and "Close" in df.columns:
                        s = df["Close"].dropna().astype(float)
                        if len(s) >= 10:
                            return s
            except Exception as exc:
                logger.debug("Failed to load VIX history from TimeSeriesDB: %s", exc)
        return None

    def _satellite_line_count(self, portfolio: PortfolioState) -> int:
        return sum(
            1
            for p in portfolio.positions
            if p.qty_shares > 0 and p.ticker != self.core_ticker
        )

    def process_raw_signals(
        self,
        raw_signals: List[Signal],
        portfolio: PortfolioState,
        current_prices: Dict[str, float],
        vix_level: float | None = None,
        data_degraded_mode: bool = False,
    ) -> List[Signal]:
        """Run each raw signal through the full decision cascade."""
        today = datetime.now(timezone.utc).date()
        processed: List[Signal] = []
        satellite_lines = self._satellite_line_count(portfolio)

        # =====================================================================
        # STEP 0: Multi-Horizon Loss Limits & Kinetic Drawdown Breaker (FIRST)
        # =====================================================================
        # Evaluated before any single-name logic or VIX.
        kinetic_mult, dd_reason = self.drawdown_breaker.check(portfolio.total_equity)
        if kinetic_mult <= 0.0:
            logger.warning("HALT: Kinetic Drawdown Breaker triggered (%s). All new buys frozen.", dd_reason)
            for signal in raw_signals:
                processed.append(self._reject(signal, f"REJECTED: {dd_reason}"))
            return processed

        # =====================================================================
        # Continuous Volatility Regime & Dynamic Conviction Floor (Brain Sentinel)
        # =====================================================================
        vix_hist = self._get_vix_history(days=252)
        cur_vix = float(vix_level) if vix_level is not None else 16.0
        base_threshold = int(self.risk_cfg.SIGNAL_BUY_THRESHOLD)

        vol_eval = self.vol_sentinel.evaluate_vix_regime(
            vix_history=vix_hist,
            current_vix=cur_vix,
            base_floor=base_threshold,
        )

        regime_name = vol_eval.get("regime", "NORMAL")
        pct_rank = vol_eval.get("percentile", 50.0)
        eff_floor = float(vol_eval.get("effective_floor", base_threshold))
        is_panic = vol_eval.get("is_panic", False)

        # Conviction floor enforcement: raised in elevated vol or degraded mode
        conviction_floor = max(85.0, eff_floor) if data_degraded_mode else eff_floor

        logger.info(
            "Continuous Volatility Regime: %s (VIX=%.1f, Percentile=%.1f%%) -> Floor set to %.0f",
            regime_name,
            cur_vix,
            pct_rank,
            conviction_floor,
        )

        vix_ok = not is_panic

        for signal in raw_signals:
            ticker = signal.ticker

            # --- Check 0a: Conviction Floor (Enforced in Degraded Mode) ---
            if signal.score < conviction_floor:
                processed.append(
                    self._reject(
                        signal,
                        f"REJECTED: Score {signal.score:.1f} below conviction floor "
                        f"({conviction_floor:.0f}{' [DEGRADED MODE]' if data_degraded_mode else ''})",
                    )
                )
                continue

            # --- Check 0b: Price sanity ---
            price = current_prices.get(ticker)
            if price is None or price <= 0:
                processed.append(self._reject(signal, "REJECTED: No current price"))
                continue

            # --- Check 0c: VIX panic veto (market-wide emergency brake) ---
            if not vix_ok:
                processed.append(
                    self._reject(
                        signal,
                        f"REJECTED: VIX panic (V2TX={vix_level:.1f}) - "
                        "satellite buys frozen",
                    )
                )
                continue

            # --- Check 1: Macro veto (economic calendar) ---
            vetoed, veto_reason = self.macro_veto.check_veto(today)
            if vetoed:
                processed.append(self._reject(signal, f"REJECTED: {veto_reason}"))
                continue

            # --- Check 1b: Earnings / dividend blackout (per ticker) ---
            earn_veto, earn_reason = self.earnings_blackout.check_veto(ticker, today)
            if earn_veto:
                processed.append(self._reject(signal, f"REJECTED: {earn_reason}"))
                continue

            # --- Check 1c: Strict Piotroski F-Score Veto (< 4) ---
            if self.fundamentals_sensor is not None and ticker != self.core_ticker:
                piot_score, _ = self.fundamentals_sensor.calculate_piotroski_score(ticker)
                if piot_score < 4:
                    processed.append(
                        self._reject(
                            signal,
                            f"REJECTED: Low Piotroski quality ({piot_score}/9 < 4)",
                        )
                    )
                    continue

            # --- Check 1d: Max simultaneous satellite lines ---
            already_held = any(p.ticker == ticker for p in portfolio.positions)
            if not already_held and satellite_lines >= self.max_positions_total:
                processed.append(
                    self._reject(
                        signal,
                        f"REJECTED: Max satellite positions "
                        f"({self.max_positions_total}) reached",
                    )
                )
                continue

            # --- Check 1e: Minimum liquidity (ADV €) ---
            adv = self._avg_daily_euro_volume(ticker)
            if adv is not None and adv < self.min_liquidity_adv:
                processed.append(
                    self._reject(
                        signal,
                        f"REJECTED: Illiquid (ADV €{adv:,.0f} < "
                        f"{self.min_liquidity_adv:,.0f})",
                    )
                )
                continue

            # --- Check 1f: Short Interest Veto (AMF BDIF) ---
            short_interest = 0.0
            if self.amf_scraper is not None and self.figi_mapper is not None and ticker != self.core_ticker:
                try:
                    isin = self.figi_mapper.ticker_to_isin(ticker)
                    if isin:
                        short_interest = float(self.amf_scraper.get_short_interest(isin))
                except Exception as exc:
                    logger.debug("Failed to check AMF short interest for %s: %s", ticker, exc)

            if isinstance(signal.lineage, dict):
                signal.lineage["short_interest"] = short_interest

            if short_interest > 3.0:
                processed.append(
                    self._reject(
                        signal,
                        f"REJECTED: High Short Interest ({short_interest:.1f}%) - Toxic asset risk",
                    )
                )
                continue

            # --- Check 2a: Sector concentration limit (cheap arithmetic) ---

            if not self.firewall.check_sector_limit(ticker, portfolio):
                processed.append(
                    self._reject(signal, "REJECTED: Sector weight limit reached")
                )
                continue

            # --- Check 2b: Correlation firewall (heavy Pearson) ---
            ok, corr_reason = self.firewall.check_correlation(
                ticker, portfolio, self.timeseries_db
            )
            if not ok:
                processed.append(self._reject(signal, f"REJECTED: {corr_reason}"))
                continue

            # --- Check 2c: ML Predictive Veto (XGBoost + Isolation Forest) ---
            if predict_anomaly is not None and predict_probability_with_shap is not None:
                # Determine current market regime from vix_level (default to VOLATILE)
                if vix_level is not None:
                    if vix_level < 17.5:
                        current_regime = "BULL"
                    elif vix_level > 23.0:
                        current_regime = "VOLATILE"
                    else:
                        current_regime = "BEAR"
                else:
                    current_regime = "VOLATILE"

                feat_snapshot = (
                    signal.lineage
                    if hasattr(signal, "lineage") and isinstance(signal.lineage, dict)
                    else {}
                )

                # Anomaly Detection via Isolation Forest
                is_anomaly = predict_anomaly(feat_snapshot)
                if is_anomaly is True:
                    processed.append(
                        self._reject(
                            signal,
                            "REJECTED: Structural Anomaly detected by Isolation Forest",
                        )
                    )
                    continue

                # Win Probability & SHAP Scoring via XGBoost
                proba, shap_dict, interval = predict_probability_with_shap(
                    feat_snapshot, horizon="tactical", regime=current_regime
                )
                if proba is not None:
                    if proba < 0.50:
                        processed.append(
                            self._reject(
                                signal,
                                f"REJECTED: ML Win Probability too low ({proba * 100:.1f}%)",
                            )
                        )
                        continue

                    # Inject ML inference features into signal lineage
                    if not hasattr(signal, "lineage") or not isinstance(signal.lineage, dict):
                        signal.lineage = {}
                    signal.lineage["ml_probability"] = proba
                    signal.lineage["shap_values"] = shap_dict
                    signal.lineage["ml_interval"] = interval

            # --- Check 3: PEA position sizing (volatility & kinetic adjusted) ---
            # TODO: Re-enable RL Sizer only when SizingEnv is connected to real historical trajectories
            # rather than synthetic noise. Current sizing strictly relies on deterministic Half-Kelly,
            # Inverse Volatility, and the Kinetic Brake multiplier.
            hist_vol = self._historical_volatility(ticker)
            target_qty, sizing = self.sizer.size_with_explanation(
                signal, portfolio, price, historical_volatility=hist_vol
            )
            if target_qty <= 0:
                processed.append(
                    self._reject(signal, "REJECTED: Insufficient cash for 1 share")
                )
                continue

            signal.target_qty = target_qty
            signal.status = SignalStatus.APPROVED
            vol = sizing.get("historical_volatility")
            vol_txt = f"{vol * 100:.1f}%" if isinstance(vol, (int, float)) and vol else "n/a"
            signal.reason = (
                f"{signal.reason} | APPROVED: {target_qty} share(s) @ {price:.2f} EUR "
                f"| sizing: Kelly {sizing.get('kelly_fraction', 0):.2f} × "
                f"score {signal.score:.0f}/100 · vol {vol_txt} "
                f"(×{sizing.get('vol_factor', 1):.2f}) · "
                f"poids {sizing.get('weight_pct', 0):.2f}% equity "
                f"({sizing.get('notional', 0):,.0f} €)"
            ).strip(" |")

            if hasattr(signal, "lineage") and isinstance(signal.lineage, dict):
                signal.lineage.update({
                    "status": SignalStatus.APPROVED.value,
                    "target_qty": target_qty,
                    "execution_price": price,
                    "sizing": sizing,
                    "kinetic_multiplier": kinetic_mult,
                    "vix": vix_level,
                })

            logger.info(
                "APPROVED %s: %d share(s) @ %.2f EUR (score=%.1f, weight=%.2f%%).",
                ticker,
                target_qty,
                price,
                signal.score,
                sizing.get("weight_pct", 0),
            )
            if not already_held:
                satellite_lines += 1
            processed.append(signal)

        return processed


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    from data_models import Position, SignalType

    class _MockTSDB:
        """Returns uncorrelated price history so the firewall passes."""

        def get_historical_prices(self, ticker: str, days: int = 60):
            import numpy as np
            import pandas as pd

            dates = pd.date_range("2026-01-01", periods=days, freq="B")
            seed = sum(ord(c) for c in ticker)
            rng = np.random.default_rng(seed)
            close = np.cumsum(rng.normal(0, 1, days)) + 100
            return pd.DataFrame({"Ticker": ticker, "Date": dates, "Close": close})

    orch = SignalOrchestrator(timeseries_db=_MockTSDB())

    portfolio = PortfolioState(
        cash_available=10_000.0,
        total_equity=20_000.0,
        positions=[
            Position(ticker="MC.PA", qty_shares=2, avg_entry_price=600,
                     current_price=600, sector="Luxury"),
        ],
        last_updated=datetime.now(timezone.utc),
    )

    raw = [
        Signal(ticker="AI.PA", signal_type=SignalType.BUY, score=90.0,
               reason="Mean-reversion setup"),   # Industrials-adjacent -> APPROVE
        Signal(ticker="KER.PA", signal_type=SignalType.BUY, score=85.0,
               reason="Mean-reversion setup"),   # Luxury, but firewall/sizing decide
        Signal(ticker="OR.PA", signal_type=SignalType.BUY, score=70.0,
               reason="Mean-reversion setup"),   # Luxury
    ]
    prices = {"AI.PA": 180.0, "KER.PA": 250.0, "OR.PA": 380.0}

    def _show(title, signals):
        print(f"\n--- {title} ---")
        for s in signals:
            qty = s.target_qty if s.target_qty is not None else "-"
            print(f"{s.ticker:8} {s.status.value:9} qty={qty}")
            print(f"         reason: {s.reason}")

    # Run 1: real calendar. Today (2026-07-15) is 1 day before an ECB decision,
    # so the macro veto correctly short-circuits every signal.
    print("Macro veto today?", orch.macro_veto.check_veto(datetime.now(timezone.utc).date()))
    _show("Cascade WITH macro veto active (real calendar)",
          orch.process_raw_signals([s.model_copy() for s in raw], portfolio, prices))

    # Run 2: simulate a macro-clear day by emptying the in-memory calendar, so
    # the downstream sector / correlation / sizing logic (and APPROVED path) show.
    orch.macro_veto.calendar = {}
    _show("Cascade on a macro-CLEAR day",
          orch.process_raw_signals([s.model_copy() for s in raw], portfolio, prices))
```

## FILE: 04_orchestrator_ai/weekly_historian.py
```python
"""Weekly Historian for PEA Sniper Terminal V-Prime (Phase 12).

Every Friday the system "steps back" and writes a hedge-fund-style weekly digest
for the CIO. It aggregates the last 7 days of audit logs into hard counts
(vetoes, executions, current equity/cash) and asks the LLM to translate those
numbers into a concise, professional risk-and-performance narrative.

The LLM is a *post-hoc analyst only*: it summarizes decisions the deterministic
engine already made. It never generates or approves trades.
"""

import logging
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:  # Load config/api_keys.env if python-dotenv is available.
    from dotenv import load_dotenv

    _ENV_PATH = Path(__file__).resolve().parent.parent / "config" / "api_keys.env"
    load_dotenv(_ENV_PATH)
except Exception:  # noqa: BLE001
    pass

_INTERFACES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "05_interfaces"
)
sys.path.insert(0, _INTERFACES_DIR)

from llm_explainer import openrouter_chat  # noqa: E402

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "mistralai/mistral-7b-instruct"
_LOOKBACK_DAYS = 7
_FALLBACK_PREFIX = "[AI narrative unavailable] "


class WeeklyHistorian:
    """Builds and narrates the weekly risk/performance digest."""

    def __init__(self) -> None:
        """Read the OpenRouter API key and model slug from the environment."""
        self.api_key: str | None = os.getenv("OPENROUTER_API_KEY")
        self.model: str = os.getenv("OPENROUTER_MODEL", _DEFAULT_MODEL)
        if not self.api_key:
            logger.warning(
                "OPENROUTER_API_KEY not set; weekly report will use a data-only "
                "fallback (no AI narrative)."
            )

    @staticmethod
    def _classify(row: dict) -> str:
        """Bucket an audit row into a coarse decision category."""
        status = (row.get("status") or "").upper()
        reason = (row.get("reason") or "").lower()
        if status in ("EXECUTED", "APPROVED"):
            return "executed"
        if status == "REVOKED":
            return "revoked"
        if status == "REJECTED":
            if "vix" in reason or "panic" in reason:
                return "vetoed_vix"
            if "earnings" in reason or "blackout" in reason:
                return "vetoed_earnings"
            if "illiquid" in reason or "adv" in reason:
                return "vetoed_liquidity"
            if "max satellite" in reason or "max positions" in reason:
                return "vetoed_max_positions"
            if "macro" in reason or ("veto" in reason and "earnings" not in reason):
                return "vetoed_macro"
            if "sector" in reason:
                return "vetoed_sector"
            if "correlation" in reason or "correlated" in reason:
                return "vetoed_correlation"
            return "rejected_other"
        return "other"

    def _build_context(self, rows: list[dict], portfolio: Any) -> tuple[str, dict]:
        """Summarize audit rows + portfolio into an LLM context string.

        Returns:
            tuple[str, dict]: The context block and the raw counts dict (so the
            fallback path can render numbers without the LLM).
        """
        buckets = Counter(self._classify(r) for r in rows)
        executed = [r for r in rows if self._classify(r) == "executed"]

        positions_txt = ", ".join(
            f"{p.ticker} {p.qty_shares}@{p.current_price:.2f} "
            f"({p.unrealized_pnl_pct * 100:+.1f}%)"
            for p in portfolio.positions
        ) or "none"

        top_trades = "; ".join(
            f"{r['ticker']} ({r['status']})" for r in executed[:8]
        ) or "none"

        counts = dict(buckets)
        context = (
            f"REPORTING WINDOW: last {_LOOKBACK_DAYS} days.\n"
            f"Total signals evaluated: {len(rows)}.\n"
            f"Executed/Approved: {buckets.get('executed', 0)}.\n"
            f"Revoked (macro window): {buckets.get('revoked', 0)}.\n"
            f"Vetoed by MACRO event: {buckets.get('vetoed_macro', 0)}.\n"
            f"Vetoed by EARNINGS blackout: {buckets.get('vetoed_earnings', 0)}.\n"
            f"Vetoed by VIX panic: {buckets.get('vetoed_vix', 0)}.\n"
            f"Vetoed by LIQUIDITY: {buckets.get('vetoed_liquidity', 0)}.\n"
            f"Vetoed by MAX POSITIONS: {buckets.get('vetoed_max_positions', 0)}.\n"
            f"Vetoed by SECTOR limit: {buckets.get('vetoed_sector', 0)}.\n"
            f"Vetoed by CORRELATION: {buckets.get('vetoed_correlation', 0)}.\n"
            f"Other rejections: {buckets.get('rejected_other', 0)}.\n"
            f"Executed names: {top_trades}.\n"
            f"CURRENT EQUITY: {portfolio.total_equity:,.2f} EUR.\n"
            f"CASH AVAILABLE: {portfolio.cash_available:,.2f} EUR "
            f"({(portfolio.cash_available / portfolio.total_equity * 100) if portfolio.total_equity else 0:.1f}%).\n"
            f"OPEN POSITIONS: {positions_txt}.\n"
        )
        return context, counts

    @staticmethod
    def _fallback_report(context: str) -> str:
        """Return a numbers-only report when the LLM is unavailable."""
        return (
            f"{_FALLBACK_PREFIX}Weekly Risk & Performance Digest\n\n{context}"
        )

    async def generate_weekly_report(
        self, portfolio_db: Any, explainer: Any = None
    ) -> str:
        """Generate the weekly CIO digest.

        Args:
            portfolio_db: A ``PortfolioDB`` exposing ``fetch_signals_since`` and
                ``get_portfolio_state``.
            explainer: Optional ``NarrativeExplainer`` (unused directly; kept for
                interface compatibility — the shared OpenRouter client is used).

        Returns:
            str: The generated report, or a data-only fallback on any failure.
        """
        since = (datetime.now(timezone.utc) - timedelta(days=_LOOKBACK_DAYS)).isoformat()
        try:
            rows = portfolio_db.fetch_signals_since(since)
        except Exception:  # noqa: BLE001
            logger.exception("Could not read audit logs for weekly report.")
            rows = []

        portfolio = portfolio_db.get_portfolio_state()
        context, _counts = self._build_context(rows, portfolio)

        if not self.api_key:
            return self._fallback_report(context)

        system_prompt = (
            "Act as a Hedge Fund Risk Manager. Write a weekly digest for the "
            "CIO. Explain how risk was managed (vetoes), summarize performance, "
            "and give a 2-sentence macro outlook. Tone: professional, empirical, "
            "numbers-driven. Keep it under 220 words. No disclaimers."
        )
        narrative = await openrouter_chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context},
            ],
            api_key=self.api_key,
            model=self.model,
            max_tokens=420,
            temperature=0.5,
        )
        if not narrative:
            return self._fallback_report(context)

        logger.info("Weekly report generated (%d chars).", len(narrative))
        return narrative


if __name__ == "__main__":
    import asyncio
    from datetime import datetime, timezone

    _CORE_DIR = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "01_memory_core"
    )
    sys.path.insert(0, _CORE_DIR)
    from data_models import PortfolioState, Position  # noqa: E402

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    class _MockDB:
        def fetch_signals_since(self, since_iso: str) -> list[dict]:
            now = datetime.now(timezone.utc).isoformat()
            return [
                {"ticker": "MC.PA", "status": "EXECUTED", "reason": "approved", "created_at": now},
                {"ticker": "OR.PA", "status": "REJECTED", "reason": "Macro veto: ECB", "created_at": now},
                {"ticker": "AI.PA", "status": "REJECTED", "reason": "VIX panic", "created_at": now},
                {"ticker": "SU.PA", "status": "REJECTED", "reason": "Sector weight limit", "created_at": now},
            ]

        def get_portfolio_state(self) -> PortfolioState:
            return PortfolioState(
                cash_available=6000.0,
                total_equity=20000.0,
                positions=[
                    Position(ticker="MC.PA", qty_shares=5, avg_entry_price=600.0,
                             current_price=660.0, sector="Luxury"),
                ],
                last_updated=datetime.now(timezone.utc),
            )

    hist = WeeklyHistorian()
    report = asyncio.run(hist.generate_weekly_report(_MockDB()))
    print("\n===== WEEKLY REPORT =====\n")
    print(report)
```
