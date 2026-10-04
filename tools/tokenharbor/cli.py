#!/usr/bin/env python3
"""
TokenHarbor Auto CLI — Interactive terminal tool with Rich UI.
HTTP mode, no browser needed.

Usage:
  python3 -m tools.tokenharbor.cli              # interactive menu
  python3 -m tools.tokenharbor.cli full-setup    # one-shot full flow
  python3 -m tools.tokenharbor.cli batch 5       # batch create 5 accounts
  python3 -m tools.tokenharbor.cli test-key THK  # test single key
"""

from __future__ import annotations

import json
import random
import secrets
import string
import sys
import threading
import time
from pathlib import Path
from typing import Optional

# ── path setup ──────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
TOOLS_DIR = PROJECT_ROOT / "tools"

# ── config ──────────────────────────────────────────────────────────────────
from tools.tokenharbor import config

_TH_BASE_URL = config.BASE_URL
_ACCOUNT_FILE = PROJECT_ROOT / config.ACCOUNT_OUTPUT
_ACCOUNT_TXT_FILE = PROJECT_ROOT / config.ACCOUNT_TXT
_FREE_MODELS = config.FREE_MODELS

# ── rich ────────────────────────────────────────────────────────────────────
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.prompt import Prompt, Confirm, IntPrompt
from rich import box

console = Console()
# serializes console output and shared writes across worker threads
_console_lock = threading.Lock()
# exit IPs already claimed by a worker thread (dynamic rotation dedupe)
_USED_IPS: set[str] = set()
_USED_IPS_LOCK = threading.Lock()
# paces signup submissions across threads (anti rate-limit)
_SIGNUP_GATE_LOCK = threading.Lock()
_LAST_SIGNUP_AT: list[float] = [0.0]


def _log(*args, **kwargs) -> None:
    """Thread-safe console.print."""
    with _console_lock:
        console.print(*args, **kwargs)


# Ordered pipeline steps: (key, human label)
_STEP_DEFS: list[tuple[str, str]] = [
    ("signup", "Sign up"),
    ("email", "Verification email"),
    ("verify", "Verify email"),
    ("login", "Log in"),
    ("key", "Create API key"),
    ("free", "Enable free models"),
]
_STEP_LABEL = dict(_STEP_DEFS)
_STEP_NO = {key: i + 1 for i, (key, _) in enumerate(_STEP_DEFS)}
_STEP_TOTAL = len(_STEP_DEFS)


def _step_reporter(tag: str = "", numbered: bool = True):
    """
    Build a thread-safe step reporter.

    ``tag`` prefixes each line (e.g. ``#2``) so parallel batch workers stay
    readable. ``numbered`` shows the ``n/6`` position in the full pipeline.
    Returns ``report(key, status, detail="")`` where status is
    ``start`` | ``ok`` | ``fail`` | ``warn``.
    """
    prefix = f"[dim]{tag}[/dim] " if tag else ""
    started: dict[str, float] = {}

    def report(key: str, status: str, detail: str = "") -> None:
        label = _STEP_LABEL.get(key, key)
        pos = f"[dim]{_STEP_NO.get(key, 0)}/{_STEP_TOTAL}[/dim] " if numbered else ""
        suffix = f" [dim]· {detail}[/dim]" if detail else ""
        if status == "start":
            started[key] = time.time()
            extra = f" [dim]({detail})[/dim]" if detail else ""
            _log(f"  {prefix}{pos}[cyan]» {label}[/cyan]{extra}")
        elif status == "ok":
            dur = time.time() - started.get(key, time.time())
            _log(f"  {prefix}{pos}[green]✓ {label}[/green] [dim]{dur:4.1f}s[/dim]{suffix}")
        elif status == "warn":
            dur = time.time() - started.get(key, time.time())
            _log(f"  {prefix}{pos}[yellow]⚠ {label}[/yellow] [dim]{dur:4.1f}s[/dim]{suffix}")
        else:  # fail
            dur = time.time() - started.get(key, time.time())
            _log(f"  {prefix}{pos}[red]✗ {label}[/red] [dim]{dur:4.1f}s[/dim]")
            if detail:
                _log(f"       [red]{detail}[/red]")

    return report


_RATE_LIMIT_MARKERS = (
    "a bit fast",
    "too many",
    "rate limit",
    "too fast",
    "try again in",
    "take a breath",
    "take a moment",
)


def _is_rate_limited(error: Optional[str]) -> bool:
    """Detect TokenHarbor rate-limit errors ("a bit fast", "too many", ...)."""
    if not error:
        return False
    lowered = error.lower()
    return any(marker in lowered for marker in _RATE_LIMIT_MARKERS)


def _pace_signup() -> None:
    """
    Block until ``signup_interval`` has elapsed since the last signup
    submission (global, across all threads) to avoid burst rate limits.
    """
    interval = max(0.0, config.RATE_SIGNUP_INTERVAL)
    if interval <= 0:
        return
    with _SIGNUP_GATE_LOCK:
        now = time.time()
        wait = (_LAST_SIGNUP_AT[0] + interval) - now
        if wait > 0:
            time.sleep(wait)
        _LAST_SIGNUP_AT[0] = time.time()


def _signup_with_retry(email, password, capsolver_key, proxy, report=None):
    """
    Submit signup with global pacing and retry-on-rate-limit.

    On a rate-limit error: back off (growing delay), rotate to a fresh
    proxy IP and a fresh device fingerprint, then resubmit.
    """
    attempts = max(1, 1 + config.RATE_RETRY_ATTEMPTS)
    backoff = config.RATE_RETRY_BACKOFF
    last_error = "Signup failed"
    for attempt in range(1, attempts + 1):
        if report and attempt > 1:
            report("signup", "start", detail=f"retry {attempt}/{attempts}")
        _pace_signup()
        client = TokenHarborClient(capsolver_key=capsolver_key, proxy=proxy)
        result = client.signup(email, password)
        if result.get("ok"):
            return client, result
        last_error = str(result.get("error") or "Signup failed")
        if not _is_rate_limited(last_error):
            return client, result
        if attempt < attempts:
            wait = backoff * (config.RATE_BACKOFF_MULTIPLIER ** (attempt - 1))
            if report:
                report("signup", "warn", detail=f"rate limited — retry in {wait:.0f}s")
            time.sleep(wait)
            proxy = _get_fresh_proxy()[0] or proxy
    return TokenHarborClient(capsolver_key=capsolver_key, proxy=proxy), {"ok": False, "error": last_error}

# ── local imports ───────────────────────────────────────────────────────────
from tools.tokenharbor.client import TokenHarborClient
from tools.tokenharbor.tempmail import (
    TempMailClient,
    TempMailError,
    extract_verification_link,
    extract_verification_code,
)

# ═══════════════════════════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _load_capsolver_key() -> Optional[str]:
    return config.CAPSOLVER_API_KEY


def _solver_name() -> str:
    """Label for the active Turnstile solver."""
    if config.CAPSOLVER_ENABLED and config.CAPSOLVER_API_KEY:
        return "Capsolver"
    return "Camoufox"


def _solver_ready() -> bool:
    """True when a Turnstile solver is usable (Capsolver key, or Camoufox)."""
    if config.CAPSOLVER_ENABLED and config.CAPSOLVER_API_KEY:
        return True
    if not config.CAMOUFOX_ENABLED:
        return False
    from tools.tokenharbor import camoufox_solver
    return camoufox_solver.is_available()


def _load_all_proxies() -> list[str]:
    """Load all proxies from config.toml (DataImpulse gateway + optional list)."""
    return config.load_proxies()


def _load_random_proxy() -> Optional[str]:
    proxies = _load_all_proxies()
    return random.choice(proxies) if proxies else None


def _proxy_exit_ip(proxy: str, timeout: Optional[float] = None) -> Optional[str]:
    """Return the exit IP for a proxy, or None if unreachable."""
    import requests as _r

    timeout = config.PROXY_CHECK_TIMEOUT if timeout is None else timeout
    try:
        resp = _r.get(
            config.PROXY_IP_CHECK_URL,
            proxies={"http": proxy, "https": proxy},
            timeout=timeout,
        )
        if not resp.ok:
            return None
        data = resp.json()
        return data.get("ip") if isinstance(data, dict) else None
    except Exception:
        return None


def _get_fresh_proxy() -> tuple[Optional[str], Optional[str]]:
    """
    Build a rotating proxy with a NEW sticky session so it exits from a
    fresh IP, verifying that the IP is not already used by another thread.

    Returns ``(proxy_url, exit_ip)``. Falls back to the shared gateway if
    sticky sessions are unavailable.
    """
    gateway = config.session_proxy_url(config.new_session_id())
    if gateway is None:
        return None, None

    attempts = max(1, config.PROXY_MAX_IP_RETRIES)
    last_ip: Optional[str] = None
    for _ in range(attempts):
        ip = _proxy_exit_ip(gateway)
        if not ip:
            time.sleep(1)
            continue
        last_ip = ip
        with _USED_IPS_LOCK:
            if ip not in _USED_IPS:
                _USED_IPS.add(ip)
                return gateway, ip
        # IP already claimed by another thread — rotate to a new session.
        gateway = config.session_proxy_url(config.new_session_id())
    return gateway, last_ip


def _check_proxy(proxy: str, timeout: Optional[float] = None) -> bool:
    """Test if a proxy is alive by hitting tokenharbor.ai."""
    import requests as _r

    timeout = config.PROXY_CHECK_TIMEOUT if timeout is None else timeout
    try:
        resp = _r.get(
            f"{_TH_BASE_URL}/login",
            proxies={"http": proxy, "https": proxy},
            timeout=timeout,
        )
        return resp.status_code < 500
    except Exception:
        return False


def _check_all_proxies(proxies: Optional[list[str]] = None, timeout: Optional[float] = None) -> tuple[list[str], list[str]]:
    """Check all proxies. Returns (alive, dead)."""
    if proxies is None:
        proxies = _load_all_proxies()
    alive, dead = [], []
    for p in proxies:
        if _check_proxy(p, timeout=timeout):
            alive.append(p)
        else:
            dead.append(p)
    return alive, dead


def _get_working_proxy(checked_alive: Optional[list[str]] = None) -> Optional[str]:
    """Get a random working proxy. If checked_alive provided, use it; otherwise load one."""
    if checked_alive:
        return random.choice(checked_alive)
    return _load_random_proxy()


def _pick_working_proxy_interactive() -> Optional[str]:
    """Pick a usable proxy: a fresh sticky session when available, else scan list."""
    gateway = config.session_proxy_url(config.new_session_id())
    if gateway:
        console.print("[bold cyan]Checking rotating proxy...[/bold cyan]")
        if _check_proxy(gateway):
            console.print("  [green]✓ rotating proxy alive[/green]")
            return gateway
        console.print("  [red]✗ rotating proxy unreachable[/red]")
        return None

    all_proxies = _load_all_proxies()
    if not all_proxies:
        console.print("[yellow]No proxies configured[/yellow]")
        return None

    console.print(f"[bold cyan]Checking {len(all_proxies)} proxies...[/bold cyan]")
    alive, dead = _check_all_proxies(all_proxies)
    if not alive:
        console.print(f"[red]✗ All {len(dead)} proxies are dead![/red]")
        return None

    console.print(f"  [green]✓ {len(alive)} alive[/green]  [red]✗ {len(dead)} dead[/red]")
    return random.choice(alive)


def _gen_password(length: int = 20) -> str:
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(chars) for _ in range(length))


_DOMAINS_CACHE: list[str] = []
_DOMAINS_CACHE_LOCK = threading.Lock()


def _get_domains(refresh: bool = False) -> list[str]:
    """
    Usable email domains.

    Source of truth is BlipMail's ``GET /api/config`` -> ``mailDomains``.
    ``ALLOWED_EMAIL`` in config.toml is an OPTIONAL filter: when set, only
    those domains (intersected with BlipMail's) are used; when empty, all
    BlipMail domains are used. Cached for the process lifetime.
    """
    with _DOMAINS_CACHE_LOCK:
        if _DOMAINS_CACHE and not refresh:
            return list(_DOMAINS_CACHE)

        try:
            domains = TempMailClient().get_domains()
        except Exception:
            domains = []

        if not domains:
            # BlipMail unreachable — fall back to ALLOWED_EMAIL if present.
            domains = [d for d in config.ALLOWED_EMAIL if d]

        if config.ALLOWED_EMAIL:
            filtered = [d for d in domains if d in config.ALLOWED_EMAIL]
            if filtered:
                domains = filtered

        _DOMAINS_CACHE[:] = domains
        return list(_DOMAINS_CACHE)


def _gen_email(domain: Optional[str] = None) -> str:
    """Generate a random email on one of the usable (BlipMail) domains."""
    local_part = "th_" + "".join(
        secrets.choice(string.ascii_lowercase + string.digits) for _ in range(10)
    )
    if domain is None:
        domains = _get_domains()
        if not domains:
            raise RuntimeError(
                "No usable email domains: BlipMail /api/config unreachable "
                "and ALLOWED_EMAIL is empty"
            )
        domain = random.choice(domains)
    return f"{local_part}@{domain}"


def _save_account(account: dict, path: Optional[str] = None) -> None:
    with _console_lock:
        _save_account_unlocked(account, path)


def _save_account_unlocked(account: dict, path: Optional[str] = None) -> None:
    filepath = Path(path or _ACCOUNT_FILE)
    if filepath.exists():
        try:
            existing = json.loads(filepath.read_text())
        except json.JSONDecodeError:
            existing = []
        if isinstance(existing, list):
            existing.append(account)
        else:
            existing = [existing, account]
        filepath.write_text(json.dumps(existing, indent=2))
    else:
        filepath.write_text(json.dumps(account, indent=2))

    _save_account_txt(account)


def _save_account_txt(account: dict) -> None:
    """Append an ``email|apikey`` line to the plain-text account file."""
    email = account.get("email")
    api_key = account.get("api_key")
    if not email or not api_key:
        return
    txt_path = Path(_ACCOUNT_TXT_FILE)
    with txt_path.open("a") as f:
        f.write(f"{email}|{api_key}\n")


# ═══════════════════════════════════════════════════════════════════════════════
# banner
# ═══════════════════════════════════════════════════════════════════════════════

BANNER = r"""
[bold cyan]
               ██╗  ██╗ █████╗ ██████╗ ██████╗  ██████╗ ██████╗
               ██║  ██║██╔══██╗██╔══██╗██╔══██╗██╔═══██╗██╔══██╗
               ███████║███████║██████╔╝██████╔╝██║   ██║██████╔╝
               ██╔══██║██╔══██║██╔══██╗██╔══██╗██║   ██║██╔══██╗
               ██║  ██║██║  ██║██║  ██║██████╔╝╚██████╔╝██║  ██║
               ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝  ╚═════╝ ╚═╝  ╚═╝
[/bold cyan]
[bold bright_cyan]                 TokenHarbor Auto CLI — HTTP Mode • No Browser[/bold bright_cyan]
[dim italic]                                 by MASANTOID[/dim italic]
"""


def _print_banner(clear: bool = True):
    if clear:
        console.clear()
    console.print(BANNER)
    console.print()


# ═══════════════════════════════════════════════════════════════════════════════
# single account setup
# ═══════════════════════════════════════════════════════════════════════════════

def _wait_for_verification(email: str, timeout: Optional[float] = None) -> Optional[str]:
    """
    Claim the BlipMail inbox for ``email`` (so it belongs to this session),
    then poll until a TokenHarbor verification link or code arrives.

    Returns the link (preferred), the 6-digit code, or None on timeout.
    """
    timeout = config.TEMPMAIL_TIMEOUT if timeout is None else timeout
    mail = TempMailClient()

    local_part, _, domain = email.partition("@")
    try:
        mail.create_inbox(local_part=local_part, domain=domain)
    except TempMailError as e:
        _log(f"  [yellow]⚠ BlipMail inbox claim failed for {email}: {e}[/yellow]")

    msg = mail.wait_for_message(
        email,
        timeout=timeout,
        sender_contains="tokenharbor",
    )
    if not msg:
        return None
    body = msg.get("body", "") or ""
    return extract_verification_link(body, _TH_BASE_URL) or extract_verification_code(body)


def _run_single_setup(
    email: str,
    password: str,
    capsolver_key: str,
    proxy: Optional[str] = None,
    report=None,
) -> dict:
    """
    Run full setup for ONE account, printing each step as it happens.

    Returns the account dict on success, or ``{"error": <message>}`` on failure.
    ``report`` is an optional ``_step_reporter`` callable; when omitted a
    standalone reporter is created. It must be thread-safe (the batch runner
    passes a tagged reporter).
    """
    if report is None:
        report = _step_reporter()

    proxy = proxy or _load_random_proxy()

    # ── step 1: signup (paced globally + retried on rate limit) ─────────
    report("signup", "start", detail=f"via {_solver_name()}")
    client, result = _signup_with_retry(email, password, capsolver_key, proxy, report)
    if not result.get("ok"):
        report("signup", "fail", detail=str(result.get("error")))
        return {"error": f"Signup failed: {result.get('error')}"}
    report("signup", "ok")

    # ── step 2: wait for verification email ─────────────────────────────
    report("email", "start")
    verification = _wait_for_verification(email)
    if not verification:
        report("email", "fail", detail=f"No verification email for {email}")
        return {"error": f"No verification email for {email}"}
    report("email", "ok")

    # ── step 3: verify email ────────────────────────────────────────────
    report("verify", "start")
    if client.verify_email(verification):
        report("verify", "ok")
    else:
        report("verify", "warn", detail="not confirmed — continuing")

    # ── step 4: login ───────────────────────────────────────────────────
    report("login", "start")
    login_result = client.login(email, password)
    if not login_result.get("ok"):
        report("login", "fail", detail=str(login_result.get("error")))
        return {"error": f"Login failed for {email}: {login_result.get('error')}"}
    report("login", "ok")

    # ── step 5: create API key ──────────────────────────────────────────
    report("key", "start")
    key_result = client.create_api_key("auto-cli")
    api_key = key_result.get("plaintext") or key_result.get("key", {}).get("plaintext")
    if not api_key:
        report("key", "fail", detail="no plaintext key returned")
        return {"error": f"API key failed for {email}"}
    report("key", "ok", detail=api_key[:18] + "…")

    # ── step 6: enable free models ──────────────────────────────────────
    report("free", "start")
    free_result = client.enable_free_models()
    free_enabled = free_result.get("ok") or free_result.get("free_models_enabled")
    report("free", "ok" if free_enabled else "warn", detail="" if free_enabled else "not confirmed")

    return {
        "email": email,
        "password": password,
        "api_key": api_key,
        "free_tier_enabled": bool(free_enabled),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _run_full_setup(email: Optional[str] = None, password: Optional[str] = None) -> int:
    """Complete flow: register → verify → login → create key → enable free → test."""
    try:
        email = email or _gen_email()
    except RuntimeError as e:
        _print_banner()
        console.print(f"[red]✗ {e}[/red]")
        return 1
    password = password or _gen_password()

    _print_banner()

    domains = _get_domains()
    info = Table.grid(padding=(0, 2))
    info.add_column(style="bold cyan", justify="right")
    info.add_column(style="white")
    info.add_row("Email    ", email)
    info.add_row("Password ", password)
    info.add_row("Domains  ", ", ".join(domains) or "[red]none[/red]")
    console.print(Panel(info, title="[bold]Account Info[/bold]", border_style="cyan"))

    capsolver_key = _load_capsolver_key()
    if not _solver_ready():
        console.print("[red]✗ No Turnstile solver available (enable Capsolver or Camoufox)[/red]")
        return 1

    # check & pick working proxy
    proxy = _pick_working_proxy_interactive()
    if proxy is None:
        console.print("[red]✗ No working proxy available[/red]")
        return 1

    console.print()
    console.print(
        Panel.fit(
            f"[bold]{email}[/bold]\n[dim]solver: {_solver_name()} · flow: "
            f"{_STEP_TOTAL} steps · {_STEP_LABEL['signup']} → {_STEP_LABEL['free']}[/dim]",
            title="[bold cyan]Running setup[/bold cyan]",
            border_style="cyan",
        )
    )
    console.print()

    account = _run_single_setup(email, password, capsolver_key, proxy=proxy)
    if account.get("error"):
        console.print()
        console.print(Panel(f"[red]✗ {account['error']}[/red]", title="[red]Setup failed[/red]", border_style="red"))
        return 1

    # ── test ────────────────────────────────────────────────────────────
    api_key = account["api_key"]
    free_enabled = account["free_tier_enabled"]
    console.print()
    with Progress(SpinnerColumn(), TextColumn("[dim]Testing API key…[/dim]"), console=console) as progress:
        task = progress.add_task("", total=None)
        client = TokenHarborClient(capsolver_key=capsolver_key, proxy=_load_random_proxy())
        test = client.chat(api_key, _FREE_MODELS[0], "Say 'TokenHarbor CLI works!' in 5 words")
        progress.remove_task(task)
    if "choices" in test:
        content = test["choices"][0]["message"]["content"]
        console.print(f"  [green]✓[/green] [dim]API key works:[/dim] {content}")
    else:
        console.print(f"  [yellow]⚠[/yellow] {test.get('error', test)}")

    # ── save ────────────────────────────────────────────────────────────
    _save_account(account)

    # ── result ──────────────────────────────────────────────────────────
    rt = Table.grid(padding=(0, 2))
    rt.add_column(style="bold cyan", justify="right")
    rt.add_column(style="white")
    rt.add_row("Email              ", email)
    rt.add_row("Password           ", password)
    rt.add_row("API Key            ", f"[bold green]{api_key}[/bold green]")
    rt.add_row("Free Tier Enabled  ", "✓" if free_enabled else "✗")
    console.print()
    console.print(Panel(rt, title="[bold green]✓ Setup Complete[/bold green]", border_style="green"))
    return 0


# ═══════════════════════════════════════════════════════════════════════════════
# batch mode
# ═══════════════════════════════════════════════════════════════════════════════

def _run_batch(count: int) -> int:
    """Create N accounts in batch mode (multi-threaded when enabled)."""
    _print_banner()

    capsolver_key = _load_capsolver_key()
    if not _solver_ready():
        console.print("[red]✗ No Turnstile solver available (enable Capsolver or Camoufox)[/red]")
        return 1

    domains = _get_domains()
    if not domains:
        console.print("[red]✗ No ALLOWED_EMAIL domains configured in config.toml[/red]")
        return 1
    console.print(f"  [dim]Domains:[/dim] {', '.join(domains)}")
    console.print(f"  [dim]Accounts to create:[/dim] {count}")

    # pre-check configured proxies (rotating gateway reachability)
    console.print("[bold cyan]Checking proxies...[/bold cyan]")
    check_proxy = config.session_proxy_url(config.new_session_id())
    if check_proxy:
        alive = [check_proxy] if _check_proxy(check_proxy) else []
        dead = [] if alive else [check_proxy]
    else:
        all_proxies = _load_all_proxies()
        alive, dead = _check_all_proxies(all_proxies)
    if not alive:
        console.print(f"[red]✗ All {len(dead)} proxies are dead![/red]")
        return 1
    console.print(f"  [green]✓ {len(alive)} alive[/green]  [red]✗ {len(dead)} dead[/red]")

    workers = config.THREADS_MAX_WORKERS if config.THREADS_ENABLED else 1
    workers = max(1, min(workers, count))
    mode = "multi-threaded" if workers > 1 else "single-threaded"
    console.print(f"  [dim]Workers:[/dim] {workers} ({mode})")
    console.print()

    # reset per-run IP claim state
    global _USED_IPS
    _USED_IPS = set()

    accounts: list[dict] = []
    failed = 0
    results_lock = threading.Lock()

    def _create_one(index: int) -> None:
        nonlocal failed
        domain = random.choice(domains)
        email = _gen_email(domain=domain)
        password = _gen_password()

        # each thread builds its own rotating proxy with a fresh sticky session
        proxy, ip = _get_fresh_proxy()
        if proxy is None:
            proxy = random.choice(alive)
            ip = _proxy_exit_ip(proxy)

        _log("")
        _log(
            f"[bold cyan]┌── [{index}/{count}][/bold cyan] [bold]{email}[/bold] "
            f"[dim]({domain} · ip={ip or '?'} · solver={_solver_name()})[/dim]"
        )
        report = _step_reporter(tag=f"#{index}")

        try:
            result = _run_single_setup(email, password, capsolver_key, proxy=proxy, report=report)
        except Exception as exc:  # never let a worker die silently
            result = {"error": f"Unexpected error: {type(exc).__name__}: {exc}"}

        if result.get("error"):
            _log(f"[bold cyan]└──[/bold cyan] [red]✗ [{index}/{count}] {email} failed[/red]")
        else:
            _save_account(result)
            _log(
                f"[bold cyan]└──[/bold cyan] [green]✓ [{index}/{count}] {email}[/green] "
                f"[dim]→ {result['api_key'][:24]}…[/dim]"
            )

        with results_lock:
            if result.get("error"):
                failed += 1
            else:
                accounts.append(result)

    if workers > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = []
            for i in range(1, count + 1):
                futures.append(pool.submit(_create_one, i))
                time.sleep(config.THREADS_START_DELAY)
            for _ in as_completed(futures):
                pass
    else:
        for i in range(1, count + 1):
            _create_one(i)
            if i < count:
                time.sleep(2)

    # ── summary ─────────────────────────────────────────────────────────
    console.print()
    summary = Table(title="[bold]Batch Summary[/bold]", box=box.ROUNDED, border_style="cyan")
    summary.add_column("#", style="dim")
    summary.add_column("Email", style="cyan")
    summary.add_column("API Key", style="green")
    summary.add_column("Free", style="bold")
    for j, a in enumerate(accounts, 1):
        key = a["api_key"]
        summary.add_row(
            str(j),
            a["email"],
            key[:30] + "..." if len(key) > 30 else key,
            "✓" if a.get("free_tier_enabled") else "✗",
        )

    if failed > 0:
        summary.add_row("", f"[red]{failed} failed[/red]", "", "")

    console.print(summary)
    console.print(f"[bold green]✓ {len(accounts)}/{count} accounts created[/bold green]")
    return 0


# ═══════════════════════════════════════════════════════════════════════════════
# other commands
# ═══════════════════════════════════════════════════════════════════════════════

def _run_create_key(email: str, password: str, label: str = "auto-cli") -> int:
    _print_banner()
    capsolver_key = _load_capsolver_key()
    if not _solver_ready():
        console.print("[red]✗ No Turnstile solver available[/red]")
        return 1

    proxy = _load_random_proxy()
    console.print(f"  [dim]Proxy:[/dim] {proxy or '[yellow]none[/yellow]'}")
    console.print(f"  [dim]Solver:[/dim] {_solver_name()}")
    console.print()
    client = TokenHarborClient(capsolver_key=capsolver_key, proxy=proxy)
    report = _step_reporter(numbered=False)

    report("login", "start")
    login_result = client.login(email, password)
    if not login_result.get("ok"):
        report("login", "fail", detail=str(login_result.get("error")))
        return 1
    report("login", "ok")

    report("key", "start")
    key_result = client.create_api_key(label)
    api_key = key_result.get("plaintext") or key_result.get("key", {}).get("plaintext")
    if not api_key:
        report("key", "fail", detail=json.dumps(key_result)[:160])
        return 1
    report("key", "ok", detail=api_key[:18] + "…")

    if api_key:
        _save_account({"email": email, "api_key": api_key, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        console.print()
        console.print(Panel(f"[bold green]{api_key}[/bold green]", title="[bold]API Key Created[/bold]", border_style="green"))
        return 0

    console.print(f"[red]✗ Failed:[/red] {json.dumps(key_result)[:200]}")
    return 1


def _run_check_proxies() -> int:
    """Check all proxies and display results."""
    _print_banner()
    all_proxies = _load_all_proxies()
    if not all_proxies:
        console.print("[yellow]No proxies configured[/yellow]")
        return 1

    console.print(f"[bold cyan]Checking {len(all_proxies)} proxies...[/bold cyan]")
    console.print()

    alive, dead = _check_all_proxies(all_proxies)

    table = Table(title="[bold]Proxy Check Results[/bold]", box=box.ROUNDED, border_style="cyan")
    table.add_column("Proxy", style="dim")
    table.add_column("Status", style="bold")

    for p in alive:
        table.add_row(p, "[green]✓ Alive[/green]")
    for p in dead:
        table.add_row(p, "[red]✗ Dead[/red]")

    console.print(table)
    console.print()
    console.print(f"  [green]✓ {len(alive)} alive[/green]  │  [red]✗ {len(dead)} dead[/red]  │  Total: {len(all_proxies)}")
    return 0


def _run_test_key(key: str) -> int:
    _print_banner()
    import requests

    table = Table(title="[bold]API Key Test[/bold]", box=box.ROUNDED, border_style="cyan")
    table.add_column("Model", style="cyan")
    table.add_column("Status", style="bold")
    table.add_column("Response", style="dim")

    for model_id in _FREE_MODELS:
        with Progress(SpinnerColumn(), TextColumn(f"[dim]Testing {model_id}...[/dim]"), console=console) as progress:
            t = progress.add_task("", total=None)
            try:
                r = requests.post(
                    f"{_TH_BASE_URL}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={"model": model_id, "messages": [{"role": "user", "content": "Say hi in 3 words"}], "max_tokens": 20},
                    timeout=30,
                )
                if r.ok:
                    content = r.json()["choices"][0]["message"]["content"]
                    table.add_row(model_id, "[green]✓ OK[/green]", content)
                else:
                    table.add_row(model_id, f"[red]✗ {r.status_code}[/red]", r.text[:100])
            except Exception as e:
                table.add_row(model_id, "[red]✗ Error[/red]", str(e)[:80])
            progress.remove_task(t)

    console.print(table)
    return 0


def _run_enable_free(email: str, password: str) -> int:
    _print_banner()
    capsolver_key = _load_capsolver_key()
    proxy = _load_random_proxy()
    console.print(f"  [dim]Proxy:[/dim] {proxy or '[yellow]none[/yellow]'}")
    console.print(f"  [dim]Solver:[/dim] {_solver_name()}")
    console.print()
    client = TokenHarborClient(capsolver_key=capsolver_key, proxy=proxy)
    report = _step_reporter(numbered=False)

    report("login", "start")
    login_result = client.login(email, password)
    if not login_result.get("ok"):
        report("login", "fail", detail=str(login_result.get("error")))
        return 1
    report("login", "ok")

    report("free", "start")
    result = client.enable_free_models()
    if result.get("ok") or result.get("free_models_enabled"):
        report("free", "ok")
        console.print("[green]✓ Free models enabled![/green]")
        return 0
    report("free", "fail", detail=json.dumps(result)[:160])
    return 1


def _run_status(email: str, password: str) -> int:
    _print_banner()
    capsolver_key = _load_capsolver_key()
    proxy = _load_random_proxy()
    console.print(f"  [dim]Proxy:[/dim] {proxy or '[yellow]none[/yellow]'}")
    client = TokenHarborClient(capsolver_key=capsolver_key, proxy=proxy)

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("[cyan]Logging in...", total=None)
        login_result = client.login(email, password)
        if not login_result.get("ok"):
            progress.stop()
            console.print(f"[red]✗ Login failed:[/red] {login_result.get('error')}")
            return 1

        progress.update(task, description="[cyan]Fetching status...")
        free_tier = client.get_free_tier_status()
        privacy = client.get_privacy_status()
        keys = client.list_api_keys()

    ft = Table(title="[bold]Free Tier[/bold]", box=box.SIMPLE, border_style="cyan")
    ft.add_column("Key", style="cyan"); ft.add_column("Value")
    for k, v in free_tier.items():
        ft.add_row(str(k), str(v))
    console.print(ft)

    console.print()
    pv = Table(title="[bold]Privacy[/bold]", box=box.SIMPLE, border_style="cyan")
    pv.add_column("Key", style="cyan"); pv.add_column("Value")
    for k, v in privacy.items():
        pv.add_row(str(k), str(v))
    console.print(pv)

    console.print()
    kt = Table(title="[bold]API Keys[/bold]", box=box.SIMPLE, border_style="cyan")
    kt.add_column("Label", style="cyan"); kt.add_column("Prefix", style="dim"); kt.add_column("Created", style="dim")
    for k in keys:
        prefix = k.get("prefix", "?")
        kt.add_row(k.get("label", "?"), prefix[:20] + "..." if len(prefix) > 20 else prefix, k.get("created_at", "?"))
    console.print(kt)
    return 0


# ═══════════════════════════════════════════════════════════════════════════════
# interactive menu
# ═══════════════════════════════════════════════════════════════════════════════

def _interactive_menu() -> int:
    while True:
        console.clear()
        _print_banner()

        capsolver_key = _load_capsolver_key()
        proxy = _load_random_proxy()
        domains = _get_domains()

        # status box
        status_grid = Table.grid(padding=(0, 3))
        status_grid.add_column(justify="center")
        status_grid.add_column(justify="center")
        status_grid.add_column(justify="center")
        if _solver_ready():
            caps_status = f"[green]✓ {_solver_name()} ready[/green]"
        else:
            caps_status = "[red]✗ No solver (Capsolver off, Camoufox missing)[/red]"
        proxy_status = "[green]✓ Proxy ready[/green]" if proxy else "[yellow]⚠ Proxy none[/yellow]"
        domain_status = f"[dim]📧 {', '.join(domains)}[/dim]" if domains else "[red]✗ No domains[/red]"
        status_grid.add_row(caps_status, proxy_status, domain_status)
        console.print(Panel(status_grid, border_style="dim cyan", padding=(0, 2)))
        console.print()

        menu = Table(show_header=False, box=box.SIMPLE, padding=(0, 3))
        menu.add_column("Key", style="bold cyan", width=4)
        menu.add_column("Command", style="white")
        menu.add_column("Description", style="dim")
        menu.add_row("[1]", "Full Setup",       "Register → Verify → Login → API Key → Free Models → Test")
        menu.add_row("[2]", "Batch Create",     "Create multiple accounts at once")
        menu.add_row("[3]", "Create API Key",   "Login with existing account and create a new API key")
        menu.add_row("[4]", "Test API Key",     "Test an API key against all free models")
        menu.add_row("[5]", "Enable Free",      "Enable free models for an existing account")
        menu.add_row("[6]", "Account Status",   "Check free tier, privacy, and API keys")
        menu.add_row("[7]", "Check Proxies",    "Test all proxies and show alive/dead")
        menu.add_row("[8]", "List Accounts",    "Show saved accounts from account.json")
        menu.add_row("[q]", "Quit",             "Exit the CLI")
        console.print(Panel(menu, title="[bold]Menu[/bold]", border_style="cyan"))

        console.print()
        choice = Prompt.ask("[bold cyan]Select[/bold cyan]", choices=["1", "2", "3", "4", "5", "6", "7", "8", "q"], default="1")

        if choice == "q":
            console.print("[dim]Bye![/dim]")
            return 0

        elif choice == "1":
            console.clear()
            use_custom = Confirm.ask("Use custom email/password?", default=False)
            email, password = None, None
            if use_custom:
                email = Prompt.ask("Email", default=_gen_email())
                password = Prompt.ask("Password", default=_gen_password())
            _run_full_setup(email=email, password=password)
            console.print(); input("Press Enter to continue...")

        elif choice == "2":
            console.clear()
            count = IntPrompt.ask("How many accounts?", default=3)
            _run_batch(count)
            console.print(); input("Press Enter to continue...")

        elif choice == "3":
            console.clear()
            email = Prompt.ask("Email")
            password = Prompt.ask("Password", password=True)
            label = Prompt.ask("Key label", default="auto-cli")
            _run_create_key(email, password, label)
            console.print(); input("Press Enter to continue...")

        elif choice == "4":
            console.clear()
            key = Prompt.ask("API Key")
            _run_test_key(key)
            console.print(); input("Press Enter to continue...")

        elif choice == "5":
            console.clear()
            email = Prompt.ask("Email")
            password = Prompt.ask("Password", password=True)
            _run_enable_free(email, password)
            console.print(); input("Press Enter to continue...")

        elif choice == "6":
            console.clear()
            email = Prompt.ask("Email")
            password = Prompt.ask("Password", password=True)
            _run_status(email, password)
            console.print(); input("Press Enter to continue...")

        elif choice == "7":
            console.clear()
            _run_check_proxies()
            console.print(); input("Press Enter to continue...")

        elif choice == "8":
            console.clear()
            _print_banner()
            acct_file = Path(_ACCOUNT_FILE)
            if acct_file.exists():
                try:
                    accounts = json.loads(acct_file.read_text())
                    if not isinstance(accounts, list):
                        accounts = [accounts]
                    tbl = Table(title="[bold]Saved Accounts[/bold]", box=box.ROUNDED, border_style="cyan")
                    tbl.add_column("#", style="dim")
                    tbl.add_column("Email", style="cyan")
                    tbl.add_column("API Key", style="green")
                    tbl.add_column("Free", style="bold")
                    tbl.add_column("Created", style="dim")
                    for i, a in enumerate(accounts, 1):
                        key = a.get("api_key", "?")
                        if len(key) > 30:
                            key = key[:30] + "..."
                        tbl.add_row(str(i), a.get("email", "?"), key,
                                    "✓" if a.get("free_tier_enabled") else "✗", a.get("created_at", "?"))
                    console.print(tbl)
                except json.JSONDecodeError:
                    console.print("[yellow]account.json is corrupted[/yellow]")
            else:
                console.print("[dim]No accounts saved yet.[/dim]")
            console.print(); input("Press Enter to continue...")


# ═══════════════════════════════════════════════════════════════════════════════
# argparse
# ═══════════════════════════════════════════════════════════════════════════════

def _parse_args():
    import argparse
    parser = argparse.ArgumentParser(description="TokenHarbor Auto CLI")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("full-setup")
    p.add_argument("--email"); p.add_argument("--password")

    p = sub.add_parser("batch")
    p.add_argument("count", type=int, help="Number of accounts to create")

    p = sub.add_parser("create-key")
    p.add_argument("--email", required=True); p.add_argument("--password", required=True)
    p.add_argument("--label", default="auto-cli")

    p = sub.add_parser("test-key")
    p.add_argument("key")

    p = sub.add_parser("enable-free")
    p.add_argument("--email", required=True); p.add_argument("--password", required=True)

    p = sub.add_parser("check-proxies", help="Check all proxies")
    p = sub.add_parser("status")
    p.add_argument("--email", required=True); p.add_argument("--password", required=True)

    return parser.parse_args()


# ═══════════════════════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    args = _parse_args()

    if args.command == "full-setup":
        return _run_full_setup(email=args.email, password=args.password)
    elif args.command == "batch":
        return _run_batch(args.count)
    elif args.command == "create-key":
        return _run_create_key(args.email, args.password, args.label)
    elif args.command == "test-key":
        return _run_test_key(args.key)
    elif args.command == "enable-free":
        return _run_enable_free(args.email, args.password)
    elif args.command == "check-proxies":
        return _run_check_proxies()
    elif args.command == "status":
        return _run_status(args.email, args.password)
    else:
        return _interactive_menu()


if __name__ == "__main__":
    sys.exit(main())