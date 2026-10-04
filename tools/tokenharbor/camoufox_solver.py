"""
Keyless Cloudflare Turnstile solver using Camoufox.

Camoufox is an anti-detect Firefox build driven by Playwright. It needs no
captcha-solver API key: it loads the Turnstile widget in a real browser
fingerprint and reads the token the widget produces.

Used automatically when Capsolver is disabled in config.toml.

Design note: Playwright's ``page.evaluate`` runs in a separate JS realm from
scripts injected into the page, so ``window.turnstile`` is not observable from
Python. The DOM, however, IS shared — so the onload callback writes the token
into a hidden ``<input>`` and we read it back through a locator.
"""

from __future__ import annotations

import threading
from typing import Optional
from urllib.parse import unquote, urlsplit

from tools.tokenharbor import config

# Camoufox launches a full browser per solve — cap how many run at once.
_SOLVE_SEMAPHORE = threading.BoundedSemaphore(max(1, config.CAMOUFOX_MAX_CONCURRENCY))


def is_available() -> bool:
    """Return True when camoufox and its browser binary are usable."""
    try:
        from camoufox.sync_api import Camoufox  # noqa: F401
    except Exception:
        return False
    return True


def _proxy_options(proxy: Optional[str]) -> Optional[dict]:
    """Convert ``scheme://user:pass@host:port`` into Camoufox/Playwright form."""
    if not proxy:
        return None
    try:
        u = urlsplit(proxy)
        server = f"{u.scheme}://{u.hostname}:{u.port}" if u.scheme else proxy
        opts: dict = {"server": server}
        if u.username:
            opts["username"] = unquote(u.username)
        if u.password:
            opts["password"] = unquote(u.password)
        return opts
    except Exception:
        return {"server": proxy}


def solve_turnstile(
    page_url: Optional[str] = None,
    sitekey: Optional[str] = None,
    timeout: Optional[float] = None,
    proxy: Optional[str] = None,
    headless: Optional[bool] = None,
    humanize: Optional[bool] = None,
) -> Optional[str]:
    """
    Solve a Turnstile challenge with Camoufox. Returns the token or None.

    No API key required. Returns None on any failure so the caller can surface
    a clean error instead of crashing.
    """
    if not config.CAMOUFOX_ENABLED:
        print("  Camoufox: disabled in config")
        return None

    try:
        from camoufox.sync_api import Camoufox
    except Exception as e:  # pragma: no cover - environment dependent
        print(f"  Camoufox: not installed ({e}). Run: pip install camoufox && camoufox fetch")
        return None

    page_url = page_url or config.SIGNUP_URL
    sitekey = sitekey or config.TURNSTILE_SITEKEY
    timeout = config.CAMOUFOX_TIMEOUT if timeout is None else timeout
    headless = config.CAMOUFOX_HEADLESS if headless is None else headless
    humanize = config.CAMOUFOX_HUMANIZE if humanize is None else humanize

    launch: dict = {"headless": headless, "humanize": humanize}
    if config.CAMOUFOX_GEOIP:
        launch["geoip"] = True
    if config.CAMOUFOX_USE_PROXY:
        popts = _proxy_options(proxy)
        if popts:
            launch["proxy"] = popts

    _SOLVE_SEMAPHORE.acquire()
    try:
        return _solve_with_browser(Camoufox, launch, page_url, sitekey, timeout)
    finally:
        _SOLVE_SEMAPHORE.release()


def _solve_with_browser(Camoufox, launch: dict, page_url: str, sitekey: str, timeout: float) -> Optional[str]:
    import time

    try:
        with Camoufox(**launch) as browser:
            page = browser.new_page()
            page.goto(page_url, timeout=min(60, timeout) * 1000, wait_until="domcontentloaded")

            # Define, in the page's main world, an onload callback that renders
            # the widget and copies the token into a hidden input (DOM is shared
            # with Python; window.* is not).
            page.add_script_tag(content=f"""
                (function() {{
                    window.__thSolveErr = "";
                    window.__thSolveRender = function() {{
                        try {{
                            var host = document.createElement('div');
                            host.id = '__th_ts_host';
                            document.body.appendChild(host);
                            var out = document.createElement('input');
                            out.id = '__th_ts_token';
                            out.type = 'hidden';
                            document.body.appendChild(out);
                            window.turnstile.render('#__th_ts_host', {{
                                sitekey: '{sitekey}',
                                callback: function(t) {{ document.getElementById('__th_ts_token').value = t; }},
                                'error-callback': function(e) {{ window.__thSolveErr = String(e); }},
                                'timeout-callback': function() {{ window.__thSolveErr = 'timeout'; }}
                            }});
                        }} catch (e) {{ window.__thSolveErr = 'render:' + e; }}
                    }};
                }})();
            """)
            page.add_script_tag(
                url="https://challenges.cloudflare.com/turnstile/v0/api.js?onload=__thSolveRender&render=explicit"
            )

            deadline = time.time() + timeout
            while time.time() < deadline:
                token = ""
                try:
                    token = page.locator("#__th_ts_token").input_value(timeout=500)
                except Exception:
                    token = ""
                if token:
                    return token
                # also accept a token the page itself may have produced
                try:
                    native = page.locator('input[name="cf-turnstile-response"]').input_value(timeout=300)
                except Exception:
                    native = ""
                if native:
                    return native
                time.sleep(1)

            print("  Camoufox: timeout waiting for Turnstile token")
            return None
    except Exception as e:
        print(f"  Camoufox: solve failed ({type(e).__name__}: {e})")
        return None