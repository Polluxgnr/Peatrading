"""Read-only Yahoo Mail IMAP client (SSL, app password)."""

from __future__ import annotations

import email
import imaplib
import logging
from dataclasses import dataclass
from email.header import decode_header
from typing import List, Optional

logger = logging.getLogger(__name__)

_HOST = "imap.mail.yahoo.com"
_PORT = 993


@dataclass
class RawMessage:
    """Minimal email payload for the HTML parser."""

    uid: str
    subject: str
    sender: str
    date: str
    html: str
    text: str


def _decode_mime(value: Optional[str]) -> str:
    if not value:
        return ""
    parts = []
    for chunk, enc in decode_header(value):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(enc or "utf-8", errors="replace"))
        else:
            parts.append(str(chunk))
    return " ".join(parts).strip()


class YahooImapClient:
    """Connect, fetch recent messages, always close cleanly.

    Never deletes, moves, or flags messages as deleted.
    """

    def __init__(self, user: str, app_password: str) -> None:
        self.user = user
        self.app_password = app_password
        self._conn: Optional[imaplib.IMAP4_SSL] = None

    def connect(self) -> None:
        """Open an SSL IMAP session."""
        logger.info("Connecting to %s:%s as %s …", _HOST, _PORT, self.user)
        self._conn = imaplib.IMAP4_SSL(_HOST, _PORT)
        self._conn.login(self.user, self.app_password)
        logger.info("IMAP login OK.")

    def close(self) -> None:
        """Logout and close; swallow errors (never crash the CLI)."""
        if self._conn is None:
            return
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._conn.logout()
        except Exception:  # noqa: BLE001
            pass
        self._conn = None
        logger.info("IMAP session closed.")

    def fetch_recent(self, folder: str = "Finance", limit: int = 20) -> List[RawMessage]:
        """Fetch the ``limit`` most recent messages from ``folder`` (read-only).

        Args:
            folder: IMAP mailbox / Yahoo label name.
            limit: Max messages to return (newest first).

        Returns:
            list[RawMessage]: Parsed envelopes + body parts.
        """
        if self._conn is None:
            self.connect()
        assert self._conn is not None

        candidates = [folder, f'"{folder}"', "INBOX"]
        selected = None
        for name in candidates:
            try:
                typ, _ = self._conn.select(name, readonly=True)
                if typ == "OK":
                    selected = name
                    break
            except Exception:
                continue

        if selected is None:
            logger.warning("Could not SELECT folder '%s' (tried %s).", folder, candidates)
            return []

        typ, data = self._conn.search(None, "ALL")
        if typ != "OK" or not data or not data[0]:
            logger.info("No messages found in folder '%s'.", selected)
            return []

        uids = data[0].split()
        uids_to_fetch = uids[-limit:]
        uids_to_fetch.reverse()

        messages: List[RawMessage] = []
        for uid_bytes in uids_to_fetch:
            uid = uid_bytes.decode()
            typ, msg_data = self._conn.fetch(uid_bytes, "(RFC822)")
            if typ != "OK" or not msg_data:
                continue

            raw_bytes = None
            for part in msg_data:
                if isinstance(part, tuple) and len(part) >= 2:
                    raw_bytes = part[1]
                    break
            if not raw_bytes:
                continue

            msg = email.message_from_bytes(raw_bytes)
            subject = _decode_mime(msg.get("Subject", ""))
            sender = _decode_mime(msg.get("From", ""))
            date_hdr = msg.get("Date", "")

            html_body = ""
            text_body = ""
            if msg.is_multipart():
                for subpart in msg.walk():
                    ct = subpart.get_content_type()
                    payload = subpart.get_payload(decode=True) or b""
                    charset = subpart.get_content_charset() or "utf-8"
                    try:
                        decoded = payload.decode(charset, errors="replace")
                    except Exception:
                        decoded = payload.decode("utf-8", errors="replace")
                    if ct == "text/html" and not html_body:
                        html_body = decoded
                    elif ct == "text/plain" and not text_body:
                        text_body = decoded
            else:
                ct = msg.get_content_type()
                payload = msg.get_payload(decode=True) or b""
                charset = msg.get_content_charset() or "utf-8"
                try:
                    decoded = payload.decode(charset, errors="replace")
                except Exception:
                    decoded = payload.decode("utf-8", errors="replace")
                if ct == "text/html":
                    html_body = decoded
                else:
                    text_body = decoded

            messages.append(
                RawMessage(
                    uid=uid,
                    subject=subject,
                    sender=sender,
                    date=date_hdr,
                    html=html_body,
                    text=text_body,
                )
            )

        return messages
