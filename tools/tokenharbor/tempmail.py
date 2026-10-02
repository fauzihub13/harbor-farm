"""
Local temp-mail API client.

Talks to your self-hosted temp-mail server (see temp-api/docs.md):

    GET /health                       -> {"status": "ok", ...}
    GET /inbox/{email}?limit=N        -> {"email", "count", "emails": [...]}
    GET /inbox/{email}/{uid}          -> single email detail

Each email: {"uid", "from", "to", "date", "body", "seen"}

There is no inbox creation step — the server accepts mail for any
address on an allowed domain, so we simply poll the inbox.
"""

from __future__ import annotations

import re
import time
from typing import Optional

import requests

from tools.tokenharbor import config


class TempMailError(Exception):
    """Raised when the temp-mail server returns an error response."""


class TempMailClient:
    """Client for the local temp-mail API."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        poll_interval: Optional[float] = None,
        limit: Optional[int] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.base_url = (base_url or config.TEMPMAIL_BASE_URL).rstrip("/")
        self.timeout = timeout if timeout is not None else config.TEMPMAIL_TIMEOUT
        self.poll_interval = (
            poll_interval if poll_interval is not None else config.TEMPMAIL_POLL_INTERVAL
        )
        self.limit = limit if limit is not None else config.TEMPMAIL_LIMIT
        self.session = session or requests.Session()
        self.session.headers.setdefault("Accept", "application/json")

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, **kwargs) -> requests.Response:
        r = self.session.get(f"{self.base_url}{path}", **kwargs)
        if r.status_code >= 400:
            raise TempMailError(f"HTTP {r.status_code} for {path}: {r.text[:200]}")
        return r

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def health(self) -> dict:
        """Check server health."""
        return self._get("/health", timeout=10).json()

    def get_inbox(self, email: str, limit: Optional[int] = None) -> dict:
        """Fetch the raw inbox payload for an address."""
        r = self._get(
            f"/inbox/{email}",
            params={"limit": limit if limit is not None else self.limit},
            timeout=20,
        )
        return r.json()

    def get_messages(self, email: str, limit: Optional[int] = None) -> list[dict]:
        """Return the list of messages for an address (newest first)."""
        data = self.get_inbox(email, limit=limit)
        if isinstance(data, dict) and data.get("error"):
            raise TempMailError(str(data.get("message", data)))
        return data.get("emails", []) if isinstance(data, dict) else []

    def get_message(self, email: str, uid: str) -> dict:
        """Return a single message by uid."""
        return self._get(f"/inbox/{email}/{uid}", timeout=20).json()

    def wait_for_message(
        self,
        email: str,
        timeout: Optional[float] = None,
        poll_interval: Optional[float] = None,
        sender_contains: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Poll until a message arrives. Returns the newest matching message
        or None on timeout.

        Args:
            sender_contains: if set, only accept messages whose ``from``
                field contains this substring (case-insensitive).
        """
        timeout = self.timeout if timeout is None else timeout
        poll_interval = self.poll_interval if poll_interval is None else poll_interval

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                messages = self.get_messages(email)
            except (TempMailError, requests.RequestException):
                messages = []
            for msg in messages:
                if sender_contains:
                    frm = str(msg.get("from", "")).lower()
                    if sender_contains.lower() not in frm:
                        continue
                return msg
            time.sleep(poll_interval)
        return None


# ------------------------------------------------------------------
# Body parsing helpers
# ------------------------------------------------------------------

def extract_verification_link(body: str, base_url: str = "https://tokenharbor.ai") -> Optional[str]:
    """Extract a TokenHarbor verify-email link from an email body, if present."""
    if not body:
        return None
    host = re.escape(base_url.rstrip("/"))
    m = re.search(rf"https?://[^\s\"'<>]*verify-email\?token=[^\s\"'<>]+", body)
    if m:
        return m.group().replace("&amp;", "&")
    m = re.search(rf"{host}/verify-email\?token=[^\s\"'<>]+", body)
    return m.group().replace("&amp;", "&") if m else None


def extract_verification_code(body: str) -> Optional[str]:
    """
    Extract the 6-digit verification code from a TokenHarbor email body.

    Handles both the raw integer and the letter-spaced / spaced form
    (e.g. "642978", "6 4 2 9 7 8", "6 4 2 9 7 8").
    """
    if not body:
        return None

    # Prefer the code near the explicit instruction line.
    m = re.search(r"6[\s-]?digit[\s\S]{0,200}?(\d(?:[\s-]?\d){5})", body, re.IGNORECASE)
    if m:
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) == 6:
            return digits

    # Fall back to a standalone 6-digit run (most reliable in the HTML block).
    for m in re.finditer(r"(?<![\d])(\d{6})(?![\d])", body):
        return m.group(1)

    # Last resort: spaced digits.
    m = re.search(r"(?<![\d])(\d(?:[\s-]\d){5})(?![\d])", body)
    if m:
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) == 6:
            return digits
    return None