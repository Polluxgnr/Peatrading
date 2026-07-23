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

        # Yahoo labels often appear as folder names; try a few variants.
        candidates = [folder, f'"{folder}"', "INBOX"]
        selected = None
        for name in candidates:
            typ, _ = self._conn.select(name, readonly=True)
            if typ == "OK":
                selected = name
                break
        if selected is None:
            raise RuntimeError(
                f"Could not SELECT folder '{folder}' (tried {candidates}). "
                "Create the Yahoo label/folder and feed it with filters."
            )
        logger.info("Selected folder %s (readonly).", selected)

        typ, data = self._conn.search(None, "ALL")
        if typ != "OK" or not data or not data[0]:
            logger.warning("No messages in folder %s.", selected)
            return []

        ids = data[0].split()
        ids = ids[-max(1, limit) :]  # newest are usually last
        ids = list(reversed(ids))  # newest first in output
        out: List[RawMessage] = []
        for mid in ids:
            try:
                typ, msg_data = self._conn.fetch(mid, "(RFC822)")
                if typ != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                if not isinstance(raw, (bytes, bytearray)):
                    continue
                msg = email.message_from_bytes(raw)
                html, text = self._extract_bodies(msg)
                out.append(
                    RawMessage(
                        uid=mid.decode() if isinstance(mid, bytes) else str(mid),
                        subject=_decode_mime(msg.get("Subject")),
                        sender=_decode_mime(msg.get("From")),
                        date=_decode_mime(msg.get("Date")),
                        html=html,
                        text=text,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skip message %s: %s", mid, exc)
        return out

    @staticmethod
    def _extract_bodies(msg: email.message.Message) -> tuple[str, str]:
        html, text = "", ""
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                disp = str(part.get("Content-Disposition") or "")
                if "attachment" in disp.lower():
                    continue
                try:
                    payload = part.get_payload(decode=True) or b""
                    charset = part.get_content_charset() or "utf-8"
                    body = payload.decode(charset, errors="replace")
                except Exception:  # noqa: BLE001
                    continue
                if ctype == "text/html" and not html:
                    html = body
                elif ctype == "text/plain" and not text:
                    text = body
        else:
            try:
                payload = msg.get_payload(decode=True) or b""
                charset = msg.get_content_charset() or "utf-8"
                body = payload.decode(charset, errors="replace")
            except Exception:  # noqa: BLE001
                body = ""
            if msg.get_content_type() == "text/html":
                html = body
            else:
                text = body
        return html, text
