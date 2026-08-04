"""Newsletter IMAP sensor + LLM morning Zeitgeist (Phase 19).

Read-only Yahoo Mail ingest for whitelisted financial newsletters.
Never deletes or moves mailbox messages.

Secrets: ``YAHOO_MAIL_USER`` / ``YAHOO_MAIL_APP_PASSWORD`` in
``config/api_keys.env``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_INGEST = _ROOT / "00_data_sensors" / "newsletter_ingest"
_DEFAULT_BRIEFING = _ROOT / "database" / "morning_briefing.json"

if str(_INGEST) not in sys.path:
    sys.path.insert(0, str(_INGEST))

try:
    sys.path.insert(0, str(_ROOT / "01_memory_core"))
    from env_loader import load_api_keys

    load_api_keys(_ROOT / "config" / "api_keys.env")
except Exception:  # noqa: BLE001
    _env = _ROOT / "config" / "api_keys.env"
    if _env.exists():
        with open(_env, "r", encoding="utf-8") as fh:
            for line in fh:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip(" '\""))


class NewsletterSensor:
    """Fetch whitelisted newsletter headlines and summarise macro Zeitgeist."""

    def __init__(
        self,
        folder: str = "Finance",
        user: str | None = None,
        app_password: str | None = None,
    ) -> None:
        self.folder = folder
        self.user = user or os.getenv("YAHOO_MAIL_USER") or ""
        self.app_password = app_password or os.getenv("YAHOO_MAIL_APP_PASSWORD") or ""

    def fetch_morning_headlines(self, limit: int = 50) -> List[str]:
        """IMAP extract → parse → dedupe → list of headline strings.

        Args:
            limit: Soft target for article headlines after dedupe.

        Returns:
            list[str]: Deduped titles (may be empty on IMAP/auth failure).
        """
        if not self.user or not self.app_password:
            logger.warning(
                "YAHOO_MAIL_USER / YAHOO_MAIL_APP_PASSWORD unset; "
                "newsletter headlines unavailable."
            )
            return []

        try:
            from ingest.imap_client import YahooImapClient
            from ingest.html_parser import parse_newsletter
            from ingest.dedupe import dedupe_articles
            from ingest.whitelist import (
                extract_sender_email,
                is_allowed_sender,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Newsletter ingest imports failed: %s", exc)
            return []

        client = YahooImapClient(user=self.user, app_password=self.app_password)
        articles: list[dict] = []
        try:
            scan = max(limit * 3, 40)
            messages = client.fetch_recent(folder=self.folder, limit=scan)
            for msg in messages:
                try:
                    if not is_allowed_sender(msg.sender):
                        logger.debug(
                            "Ignored email from %s",
                            extract_sender_email(msg.sender) or msg.sender,
                        )
                        continue
                    parsed = parse_newsletter(msg)
                    articles.extend(parsed.get("articles") or [])
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Skip message parse: %s", exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("IMAP newsletter fetch failed: %s", exc)
            return []
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass

        deduped = dedupe_articles(articles)
        import re
        spam_pattern = re.compile(r"(?i)(discount|free|referral|rewards|newsletter|email|sponsor|pitch deck|vc|substack|attio|seo agency|gtm|seed|founder|startup|saas|cap table|suivre mes récompenses|mettre à jour votre email)")
        
        titles = []
        for a in deduped:
            t = str(a.get("title") or "").strip()
            if t.startswith("http://") or t.startswith("https://"):
                continue
            if t and not spam_pattern.search(t):
                titles.append(t)
                
        logger.info("NewsletterSensor: %d headline(s) after dedupe and spam filter.", len(titles))
        return titles[: max(1, limit)]

    async def get_daily_zeitgeist(self, headlines: list[str]) -> str:
        """Ask OpenRouter for 5 short FR macro themes from overnight headlines.

        Returns:
            str: LLM bullet list, or a graceful French fallback string.
        """
        if not headlines:
            return "Indisponible (aucune une newsletter)."

        try:
            _iface = str(_ROOT / "05_interfaces")
            if _iface not in sys.path:
                sys.path.insert(0, _iface)
            from llm_explainer import openrouter_chat  # noqa: WPS433
        except Exception as exc:  # noqa: BLE001
            logger.warning("openrouter_chat import failed: %s", exc)
            return "Indisponible (module LLM)."

        api_key = os.getenv("OPENROUTER_API_KEY")
        model = os.getenv("OPENROUTER_MODEL", "mistralai/mistral-7b-instruct")
        blob = "\n".join(f"- {h}" for h in headlines[:40])
        messages = [
            {
                "role": "system",
                "content": (
                    "Tu es un analyste macro. Analyse ces titres de newsletters "
                    "financières reçues cette nuit. Identifie les 5 thèmes ou "
                    "narratifs dominants qui vont dicter la journée. Fais 5 "
                    "bullet points très courts et percutants en français. "
                    "Pas de blabla."
                ),
            },
            {"role": "user", "content": blob},
        ]
        try:
            text = await openrouter_chat(
                messages, api_key=api_key, model=model, max_tokens=320, temperature=0.3
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Zeitgeist LLM call failed: %s", exc)
            return "Indisponible (LLM)."
        if not text or not str(text).strip():
            return "Indisponible (LLM)."
        return str(text).strip()

    def write_briefing(
        self,
        zeitgeist: str,
        headlines: list[str],
        path: Path | None = None,
    ) -> Path:
        """Persist morning briefing JSON for the dashboard."""
        out = Path(path) if path else _DEFAULT_BRIEFING
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "zeitgeist": zeitgeist or "Indisponible",
            "headlines": headlines or [],
            "n_headlines": len(headlines or []),
        }
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Wrote morning briefing → %s", out)
        return out

    @staticmethod
    def read_briefing(path: Path | None = None) -> Optional[dict[str, Any]]:
        """Load ``morning_briefing.json`` or ``None`` if missing/corrupt."""
        p = Path(path) if path else _DEFAULT_BRIEFING
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None


def run_morning_briefing_sync(folder: str = "Finance") -> dict[str, Any]:
    """Sync entry used by the scheduler (wraps async Zeitgeist)."""
    sensor = NewsletterSensor(folder=folder)
    headlines = sensor.fetch_morning_headlines(limit=50)
    try:
        zeitgeist = asyncio.run(sensor.get_daily_zeitgeist(headlines))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Zeitgeist async failed: %s", exc)
        zeitgeist = "Indisponible"
    sensor.write_briefing(zeitgeist, headlines)
    return {"zeitgeist": zeitgeist, "headlines": headlines}
