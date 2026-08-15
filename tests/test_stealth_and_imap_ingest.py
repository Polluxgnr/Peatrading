"""Unit Tests for Stealth Anti-Bot Scraping (cloudscraper) and Production IMAP Newsletter Ingest."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
for sub in ("00_data_sensors", "00_data_sensors/scrapers", "00_data_sensors/imap_ingest", "01_memory_core", "02_quant_engine", "03_risk_portfolio", "04_orchestrator_ai", "05_interfaces", "06_api"):
    sys.path.insert(0, str(ROOT / sub))

from _http import safe_get, stealth_headers
from bourso_scraper import BoursoramaScraper
from imap_ingest import RawMessage, parse_newsletter, is_allowed_sender, dedupe_articles
from news_email_scraper import run_email_scraper
from main_scheduler import run_morning_news_routine


class TestStealthAndImapIngestSuite(unittest.TestCase):

    def test_01_stealth_headers(self):
        """Verify rotating stealth headers include appropriate browser signatures."""
        hdrs = stealth_headers()
        self.assertIn("User-Agent", hdrs)
        self.assertIn("Accept-Language", hdrs)
        self.assertIn("Connection", hdrs)

    def test_02_bourso_scraper_init_and_resilience(self):
        """Verify Boursorama scraper initializes session and gracefully handles anti-bot challenges."""
        scraper = BoursoramaScraper()
        self.assertIsNotNone(scraper._session)

        # Mock safe_get to return captcha response
        mock_resp = MagicMock()
        mock_resp.text = "<html><body>Please solve this DataDome captcha</body></html>"
        with patch("bourso_scraper.safe_get", return_value=mock_resp):
            profile = scraper.get_instrument_profile("MC.PA")
            self.assertEqual(profile, {})

    def test_03_imap_whitelist(self):
        """Verify strict sender whitelist accurately identifies trusted financial sources."""
        self.assertTrue(is_allowed_sender("Brief Eco <hello@brief.eco>"))
        self.assertTrue(is_allowed_sender("Substack <plancash@substack.com>"))
        self.assertTrue(is_allowed_sender("newsletter@boursorama.fr"))
        self.assertFalse(is_allowed_sender("Spam Guy <spam@phishing.net>"))
        self.assertFalse(is_allowed_sender(""))

    def test_04_html_parser_article_extraction(self):
        """Verify HTML parser extracts clean article links and contextual paragraphs."""
        html_body = """
        <html>
            <body>
                <p>Bienvenue dans la lettre financière.</p>
                <div>
                    <a href="https://www.brief.eco/article/l-oreal-resultats-record-2026?utm_source=email">
                        L'Oréal affiche des résultats record au premier semestre 2026
                    </a>
                    <p>La marge opérationnelle du groupe de cosmétiques progresse de 12% grâce au marché asiatique.</p>
                </div>
                <a href="https://www.brief.eco/unsubscribe">Unsubscribe</a>
            </body>
        </html>
        """
        msg = RawMessage(
            uid="123",
            subject="Brief Éco du jour",
            sender="Brief Eco <hello@brief.eco>",
            date="2026-08-15 08:00:00",
            html=html_body,
            text="",
        )
        parsed = parse_newsletter(msg)
        self.assertEqual(parsed["subject"], "Brief Éco du jour")
        articles = parsed["articles"]
        self.assertEqual(len(articles), 1)
        self.assertIn("L'Oréal", articles[0]["title"])
        self.assertNotIn("utm_source", articles[0]["url"])

    def test_05_dedupe_articles(self):
        """Verify token Jaccard deduplication collapses near-duplicate headlines."""
        articles = [
            {"title": "L'Oréal affiche des résultats record au premier semestre", "url": "https://a.com/1"},
            {"title": "L'Oréal affiche des résultats record au premier semestre !", "url": "https://a.com/2"},
            {"title": "Air Liquide signe un contrat majeur pour l'hydrogène vert", "url": "https://b.com/1"},
        ]
        deduped = dedupe_articles(articles)
        self.assertEqual(len(deduped), 2)

    def test_06_production_news_email_scraper_flow(self):
        """Verify run_email_scraper parses messages, cleans text, and saves to PortfolioDB."""
        mock_db = MagicMock()
        mock_db.save_news_items.return_value = 1

        mock_msg = RawMessage(
            uid="456",
            subject="L'Oréal : Nouvelle dynamique de croissance en Europe",
            sender="Substack <plancash@substack.com>",
            date=datetime.now(timezone.utc).isoformat(),
            html="<p>Analyse détaillée des résultats de L'Oréal et Air Liquide pour le PEA.</p>",
            text="",
        )

        with patch.dict(os.environ, {"YAHOO_MAIL_USER": "test@yahoo.com", "YAHOO_MAIL_APP_PASSWORD": "secretpassword"}):
            with patch("news_email_scraper.YahooImapClient") as MockClient:
                instance = MockClient.return_value
                instance.fetch_recent.return_value = [mock_msg]

                saved = run_email_scraper(mock_db)
                self.assertEqual(saved, 1)
                self.assertTrue(mock_db.save_news_items.called)
                args = mock_db.save_news_items.call_args[0][0]
                self.assertEqual(len(args), 1)
                self.assertIn("L'Oréal", args[0]["title"])

    def test_07_morning_news_routine_execution(self):
        """Verify run_morning_news_routine runs without crashing."""
        with patch("main_scheduler.run_email_scraper", return_value=2), \
             patch("main_scheduler.score_news_batch", return_value=2), \
             patch("main_scheduler.PortfolioDB"):
            run_morning_news_routine()


if __name__ == "__main__":
    unittest.main()
