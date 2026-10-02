"""
Central configuration loader for the TokenHarbor Auto CLI.

Every setting lives in ``config.toml`` next to this file — no separate
secret files. This module exposes parsed, ready-to-use values so the
rest of the code never touches the raw TOML.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Optional

CONFIG_PATH = Path(__file__).resolve().parent / "config.toml"


def load_raw() -> dict:
    """Load the raw config.toml mapping."""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Config not found: {CONFIG_PATH}\n"
            "Copy example.config.toml to config.toml and fill in your values."
        )
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


_RAW = load_raw()


# ── tokenharbor ────────────────────────────────────────────────────────────
TH = _RAW.get("tokenharbor", {})
BASE_URL: str = TH.get("base_url", "https://tokenharbor.ai")
TURNSTILE_SITEKEY: str = TH.get("turnstile_sitekey", "0x4AAAAAADBuC8Knz1EJZx9-")
SIGNUP_URL: str = TH.get("signup_url", f"{BASE_URL}/login?mode=signup")
SIGNIN_URL: str = TH.get("signin_url", f"{BASE_URL}/login")
USER_AGENT: str = TH.get(
    "user_agent",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
)
TIMEZONE: str = TH.get("timezone", "Asia/Jakarta")


# ── ALLOWED_EMAIL ──────────────────────────────────────────────────────────
def _parse_allowed(value) -> list[str]:
    """
    ALLOWED_EMAIL accepts either a comma-separated string
    ("a.com, b.com") or a TOML array (["a.com", "b.com"]).
    Only domains are expected; an "user@domain" entry is reduced to domain.
    """
    if value is None:
        return []
    items = value if isinstance(value, list) else str(value).split(",")
    out: list[str] = []
    for item in items:
        dom = str(item).strip().lower()
        if not dom:
            continue
        if "@" in dom:
            dom = dom.rsplit("@", 1)[1].strip()
        if dom and dom not in out:
            out.append(dom)
    return out


ALLOWED_EMAIL: list[str] = _parse_allowed(_RAW.get("ALLOWED_EMAIL"))


# ── tempmail ───────────────────────────────────────────────────────────────
TM = _RAW.get("tempmail", {})
TEMPMAIL_BASE_URL: str = TM.get("base_url", "http://localhost:8000")
TEMPMAIL_TIMEOUT: float = float(TM.get("timeout", 120))
TEMPMAIL_POLL_INTERVAL: float = float(TM.get("poll_interval", 3))
TEMPMAIL_LIMIT: int = int(TM.get("limit", 50))


# ── capsolver ──────────────────────────────────────────────────────────────
CS = _RAW.get("capsolver", {})
CAPSOLVER_ENABLED: bool = bool(CS.get("enabled", True))
CAPSOLVER_API_KEY: Optional[str] = CS.get("api_key") or None
CAPSOLVER_TIMEOUT: float = float(CS.get("timeout", 90))
CAPSOLVER_POLL_INTERVAL: float = float(CS.get("poll_interval", 3))


# ── proxy ──────────────────────────────────────────────────────────────────
PX = _RAW.get("proxy", {})
PROXY_ENABLED: bool = bool(PX.get("enabled", True))
PROXY_PROTOCOL: str = PX.get("protocol", "http")
PROXY_HOST: Optional[str] = PX.get("host") or None
PROXY_PORT: Optional[int] = PX.get("port")
PROXY_USERNAME: Optional[str] = PX.get("username")
PROXY_PASSWORD: Optional[str] = PX.get("password")
PROXY_CHECK_TIMEOUT: float = float(PX.get("check_timeout", 8))
PROXY_LIST: list[str] = [str(p) for p in PX.get("list", []) if str(p).strip()]
# Dynamic rotation: pin a distinct sticky session (IP) per worker thread.
PROXY_STICKY: bool = bool(PX.get("sticky", True))
PROXY_SESSION_PARAM: str = PX.get("session_param", "sessid")
PROXY_VERIFY_IP: bool = bool(PX.get("verify_ip", True))
PROXY_IP_CHECK_URL: str = PX.get("ip_check_url", "https://ipinfo.io/json")
PROXY_MAX_IP_RETRIES: int = int(PX.get("max_ip_retries", 5))


# ── threads ────────────────────────────────────────────────────────────────
THREADS = _RAW.get("threads", {})
THREADS_ENABLED: bool = bool(THREADS.get("enabled", True))
THREADS_MAX_WORKERS: int = int(THREADS.get("max_workers", 5))
THREADS_START_DELAY: float = float(THREADS.get("start_delay", 0.5))


# ── files ──────────────────────────────────────────────────────────────────
FILES = _RAW.get("files", {})
ACCOUNT_OUTPUT: str = FILES.get("account_output", "account.json")


# ── models ─────────────────────────────────────────────────────────────────
FREE_MODELS: list[str] = list(_RAW.get("models", {}).get("free", []))


# ── helpers ────────────────────────────────────────────────────────────────
def build_proxy_url(protocol: str, host: str, port, username: str, password: str) -> str:
    """Assemble a proxy URL, injecting credentials only when both are present."""
    auth = ""
    if username and password:
        auth = f"{username}:{password}@"
    return f"{protocol}://{auth}{host}:{port}"


def load_proxies() -> list[str]:
    """All usable proxy URLs: explicit list first, then the DataImpulse gateway."""
    if not PROXY_ENABLED:
        return []
    proxies = list(PROXY_LIST)
    if PROXY_HOST and PROXY_PORT:
        proxies.append(
            build_proxy_url(
                PROXY_PROTOCOL,
                PROXY_HOST,
                PROXY_PORT,
                PROXY_USERNAME or "",
                PROXY_PASSWORD or "",
            )
        )
    return proxies


def session_proxy_url(session_id: str) -> Optional[str]:
    """
    Build a sticky-session proxy URL that pins one IP per session id.

    DataImpulse expects parameters appended to the username with ``__``,
    e.g. ``user__sessid.<id>``. Returns None when no gateway is configured.
    """
    if not (PROXY_HOST and PROXY_PORT):
        return None
    if not PROXY_ENABLED:
        return None
    if not PROXY_STICKY:
        return build_proxy_url(
            PROXY_PROTOCOL, PROXY_HOST, PROXY_PORT, PROXY_USERNAME or "", PROXY_PASSWORD or ""
        )
    user = PROXY_USERNAME or ""
    if PROXY_SESSION_PARAM:
        user = f"{user}__{PROXY_SESSION_PARAM}.{session_id}"
    return build_proxy_url(
        PROXY_PROTOCOL, PROXY_HOST, PROXY_PORT, user, PROXY_PASSWORD or ""
    )


def new_session_id() -> str:
    """A unique sticky-session id (letters/digits only, safe in the username)."""
    import uuid

    return "th" + uuid.uuid4().hex[:16]