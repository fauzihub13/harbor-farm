"""
BlipMail temp-mail API client.

Docs: https://blipmail.mpruy.my.id/docs

Endpoints (all under ``{base_url}/api``):

    GET  /config                          -> {"appName", "mailDomain", "mailDomains", ...}
    GET  /session                         -> {"sessionId"}
    GET  /inboxes                         -> [{address, created_at}]
    POST /inboxes                         -> {address, created_at}   (claim/create)
    DELETE /inboxes/{address}             -> {"ok": true}
    GET  /inboxes/{address}/messages      -> [{id, inbox_address, from_address,
                                              subject, body, received_at}]

Auth is an anonymous session: fetch a ``sessionId`` once and send it as the
``x-session-id`` header on every request. Inboxes/messages are scoped to it.
"""

from __future__ import annotations

import re
import time
from typing import Optional
from urllib.parse import quote

import requests

from tools.tokenharbor import config


class TempMailError(Exception):
    """Raised when BlipMail returns an error response."""


class TempMailClient:
    """Client for the BlipMail temp-mail API."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_base: Optional[str] = None,
        timeout: Optional[float] = None,
        poll_interval: Optional[float] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.base_url = (base_url or config.TEMPMAIL_BASE_URL).rstrip("/")
        self.api_base = (
            api_base or config.TEMPMAIL_API_BASE or f"{self.base_url}/api"
        ).rstrip("/")
        self.timeout = timeout if timeout is not None else config.TEMPMAIL_TIMEOUT
        self.poll_interval = (
            poll_interval if poll_interval is not None else config.TEMPMAIL_POLL_INTERVAL
        )
        self.session = session or requests.Session()
        self.session.headers.setdefault("Accept", "application/json")
        self._session_id: Optional[str] = None

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _ensure_session(self) -> str:
        if not self._session_id:
            r = self.session.get(f"{self.api_base}/session", timeout=15)
            if r.status_code >= 400:
                raise TempMailError(f"HTTP {r.status_code} for /session: {r.text[:200]}")
            self._session_id = r.json().get("sessionId")
            if not self._session_id:
                raise TempMailError("BlipMail /session returned no sessionId")
        return self._session_id

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        self._ensure_session()
        headers = kwargs.pop("headers", {})
        headers["x-session-id"] = self._session_id
        kwargs.setdefault("timeout", 20)
        r = self.session.request(method, f"{self.api_base}{path}", headers=headers, **kwargs)
        if r.status_code >= 400:
            raise TempMailError(f"HTTP {r.status_code} for {path}: {r.text[:200]}")
        return r

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_config(self) -> dict:
        """Public app config, including available mail domains."""
        return self._request("GET", "/config", timeout=15).json()

    def get_domains(self) -> list[str]:
        """Available mail domains (from /config)."""
        data = self.get_config()
        domains = data.get("mailDomains")
        if not domains:
            single = data.get("mailDomain")
            return [single] if single else []
        return list(domains)

    def list_inboxes(self) -> list[dict]:
        """Inboxes linked to this session."""
        data = self._request("GET", "/inboxes").json()
        return data if isinstance(data, list) else []

    def create_inbox(self, local_part: str = "", domain: str = "") -> dict:
        """Create or claim an inbox. Random address when local_part is empty."""
        payload: dict = {}
        if local_part:
            payload["localPart"] = local_part
        if domain:
            payload["domain"] = domain
        return self._request("POST", "/inboxes", json=payload).json()

    def delete_inbox(self, address: str) -> dict:
        """Unlink an inbox from this session (messages are kept server-side)."""
        return self._request("DELETE", f"/inboxes/{quote(address, safe='')}").json()

    def get_messages(self, address: str) -> list[dict]:
        """All messages for an address (must belong to this session)."""
        data = self._request(
            "GET", f"/inboxes/{quote(address, safe='')}/messages"
        ).json()
        return data if isinstance(data, list) else []

    def wait_for_message(
        self,
        address: str,
        timeout: Optional[float] = None,
        poll_interval: Optional[float] = None,
        sender_contains: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Poll until a message arrives. Returns the newest matching message
        or None on timeout.

        Args:
            sender_contains: if set, only accept messages whose ``from_address``
                field contains this substring (case-insensitive).
        """
        timeout = self.timeout if timeout is None else timeout
        poll_interval = self.poll_interval if poll_interval is None else poll_interval

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                messages = self.get_messages(address)
            except (TempMailError, requests.RequestException):
                messages = []
            for msg in messages:
                if sender_contains:
                    frm = _sender(msg).lower()
                    if sender_contains.lower() not in frm:
                        continue
                return msg
            time.sleep(poll_interval)
        return None


def _sender(msg: dict) -> str:
    """Sender address across possible field names."""
    return str(msg.get("from_address") or msg.get("from") or "")


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