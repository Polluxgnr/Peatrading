import os
import imaplib
import email
from email.header import decode_header
import hashlib
from datetime import datetime
from dotenv import load_dotenv

import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "01_memory_core"))

from logging_setup import get_logger
from sqlite_portfolio import SQLitePortfolioDB

logger = get_logger("news_email_scraper")

load_dotenv(_ROOT / ".env")

IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")
IMAP_USER = os.getenv("IMAP_USER")
IMAP_PASS = os.getenv("IMAP_PASS")
IMAP_FOLDER = os.getenv("IMAP_FOLDER", "INBOX")

def get_text_from_email(msg):
    """Extract plain text from an email message."""
    text_content = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            if content_type == "text/plain" and "attachment" not in content_disposition:
                try:
                    text_content += part.get_payload(decode=True).decode()
                except Exception:
                    pass
    else:
        if msg.get_content_type() == "text/plain":
            try:
                text_content = msg.get_payload(decode=True).decode()
            except Exception:
                pass
    return text_content.strip()

def fetch_email_newsletters() -> list[dict]:
    """Fetch unread newsletters via IMAP."""
    if not IMAP_USER or not IMAP_PASS:
        logger.warning("IMAP_USER or IMAP_PASS not configured. Skipping email scraper.")
        return []
        
    news_items = []
    
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(IMAP_USER, IMAP_PASS)
        mail.select(IMAP_FOLDER)
        
        # Search for unread emails
        status, messages = mail.search(None, "UNSEEN")
        if status != "OK":
            logger.error("Failed to search emails: %s", status)
            return []
            
        email_ids = messages[0].split()
        for eid in email_ids:
            res, msg_data = mail.fetch(eid, "(RFC822)")
            if res != "OK":
                continue
                
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    
                    subject, encoding = decode_header(msg["Subject"])[0]
                    if isinstance(subject, bytes):
                        subject = subject.decode(encoding if encoding else "utf-8")
                        
                    content = get_text_from_email(msg)
                    if not content:
                        continue
                        
                    uid = hashlib.sha256((subject + content[:100]).encode("utf-8")).hexdigest()
                    
                    news_items.append({
                        "id": uid,
                        "published_at": datetime.utcnow().isoformat(),
                        "ticker": None,
                        "source": "EMAIL_Newsletter",
                        "url": "email://internal",
                        "title": subject,
                        "content": content[:2000]  # Store up to 2000 chars of email
                    })
                    
            # Mark as read (implicitly done by fetching RFC822 usually, but just in case)
            mail.store(eid, '+FLAGS', '\Seen')
            
        mail.close()
        mail.logout()
        
    except imaplib.IMAP4.error as e:
        logger.warning("IMAP authentication failed: %s", e)
    except Exception as e:
        logger.warning("Error during email scraping: %s", e)
        
    return news_items

def run_email_scraper(db: SQLitePortfolioDB):
    news = fetch_email_newsletters()
    if news:
        db.upsert_news_master(news)
        logger.info("Email Scraper finished: inserted %d items.", len(news))
    else:
        logger.info("Email Scraper finished: no items found or IMAP not configured.")

if __name__ == "__main__":
    db = SQLitePortfolioDB()
    run_email_scraper(db)
