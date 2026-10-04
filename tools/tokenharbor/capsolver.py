"""
Turnstile solver dispatcher for TokenHarbor.

Solver selection (from config.toml):

  [capsolver] enabled = true   -> Capsolver (API key, fast)
  [capsolver] enabled = false  -> Camoufox  (keyless browser solver)

The public ``solve_turnstile`` name is unchanged, so callers never change.
All values (API key, sitekey, page URLs, timeouts) come from config.toml.
"""

from __future__ import annotations

import time
from typing import Optional

import requests

from tools.tokenharbor import config

CREATE_TASK_URL = "https://api.capsolver.com/createTask"
GET_RESULT_URL = "https://api.capsolver.com/getTaskResult"


def _resolve_api_key(api_key: Optional[str] = None) -> Optional[str]:
    return api_key or config.CAPSOLVER_API_KEY


def solve_turnstile(
    page_url: Optional[str] = None,
    sitekey: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: Optional[float] = None,
    poll_interval: Optional[float] = None,
    proxy: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> Optional[str]:
    """
    Solve a Turnstile challenge with the configured solver.

    Uses Capsolver when ``[capsolver] enabled = true`` (and a key exists),
    otherwise falls back to the keyless Camoufox solver.

    Returns the token string, or None on timeout / failure.
    """
    if config.CAPSOLVER_ENABLED and _resolve_api_key(api_key):
        return _solve_capsolver(
            page_url=page_url,
            sitekey=sitekey,
            api_key=api_key,
            timeout=timeout,
            poll_interval=poll_interval,
            proxy=proxy,
            session=session,
        )

    # Capsolver disabled (or enabled without a key) -> keyless Camoufox.
    from tools.tokenharbor import camoufox_solver

    return camoufox_solver.solve_turnstile(
        page_url=page_url,
        sitekey=sitekey,
        timeout=timeout,
        proxy=proxy,
    )


def _solve_capsolver(
    page_url: Optional[str] = None,
    sitekey: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: Optional[float] = None,
    poll_interval: Optional[float] = None,
    proxy: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> Optional[str]:
    """Solve a Turnstile captcha via Capsolver."""
    client_key = _resolve_api_key(api_key)
    if not client_key:
        print("  Capsolver: no API key configured")
        return None

    page_url = page_url or config.SIGNUP_URL
    sitekey = sitekey or config.TURNSTILE_SITEKEY
    timeout = config.CAPSOLVER_TIMEOUT if timeout is None else timeout
    poll_interval = (
        config.CAPSOLVER_POLL_INTERVAL if poll_interval is None else poll_interval
    )
    http = session or requests

    task: dict = {
        "type": "AntiTurnstileTaskProxyLess",
        "websiteURL": page_url,
        "websiteKey": sitekey,
    }
    if proxy:
        # Use a proxy-aware task type when a proxy is supplied.
        task = {
            "type": "AntiTurnstileTask",
            "websiteURL": page_url,
            "websiteKey": sitekey,
            "proxy": proxy,
        }

    try:
        r = http.post(
            CREATE_TASK_URL,
            json={"clientKey": client_key, "task": task},
            timeout=15,
        )
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        print(f"  Capsolver createTask failed: {e}")
        return None

    if data.get("errorId") != 0:
        print(f"  Capsolver createTask error: {data.get('errorDescription', data)}")
        return None

    task_id = data.get("taskId")
    if not task_id:
        print("  Capsolver: no taskId returned")
        return None

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(poll_interval)
        try:
            r = http.post(
                GET_RESULT_URL,
                json={"clientKey": client_key, "taskId": task_id},
                timeout=10,
            )
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            print(f"  Capsolver getTaskResult failed: {e}")
            return None

        if data.get("status") == "ready":
            return data.get("solution", {}).get("token")
        if data.get("errorId") != 0:
            print(f"  Capsolver getTaskResult error: {data.get('errorDescription', data)}")
            return None

    print("  Capsolver: timeout waiting for Turnstile solution")
    return None