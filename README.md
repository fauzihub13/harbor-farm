# 🏗️ Harbor — TokenHarbor Auto CLI

> **HTTP-only** · **No browser** · **Pure Python**  
> Auto create accounts, generate API keys, and activate free models on TokenHarbor.

<p align="center">
  <img src="https://img.shields.io/badge/python-3.12+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License">
  <img src="https://img.shields.io/badge/mode-HTTP%20only-orange.svg" alt="HTTP Only">
</p>

---

## ✨ Features

- 🔐 **Auto signup** — Next.js Server Action + Turnstile solved by Capsolver **or** keyless Camoufox
- 📧 **BlipMail temp mail** — Claims a disposable inbox via the BlipMail API and polls it for the 6-digit verification code
- 🔑 **API key generation** — Auto create + extract plaintext key
- 🆓 **Free model activation** — One-click enable all free models
- 🚀 **Batch mode** — Create N accounts; parallel across threads or single-threaded
- 🧵 **Configurable threading** — `[threads].enabled=false` forces single-thread, `true` uses the worker pool
- 🌐 **Optional proxy rotation** — Set `[proxy].enabled=false` to run direct, or pin a distinct sticky IP per thread
- 🛡️ **Proxy auto-check** — Verify proxies before use, filter alive only (skipped when proxy is off)
- 🎨 **Rich TUI** — Interactive terminal UI with panels, tables, progress bars
- ⚙️ **Single config file** — Everything (email domains, proxy, solver, threads) in `config.toml`

## 📦 Requirements

```bash
pip install rich requests
# optional, for the keyless Camoufox Turnstile solver:
pip install camoufox && camoufox fetch
```

## 🚀 Quick Start

```bash
git clone https://github.com/fauzihub13/harbor-farm.git
cd harbor-farm
python3 -m venv .venv && source .venv/bin/activate
pip install rich requests
cp tools/tokenharbor/example.config.toml tools/tokenharbor/config.toml
# edit tools/tokenharbor/config.toml (ALLOWED_EMAIL, proxy, capsolver key)

# Interactive menu (recommended)
python3 -m tools.tokenharbor.cli

# Or one-shot commands
python3 -m tools.tokenharbor.cli full-setup
python3 -m tools.tokenharbor.cli batch 5
python3 -m tools.tokenharbor.cli test-key thk_live_xxxxxxxx
```

## 📋 Menu

| Key | Command | Description |
|:---:|---------|-------------|
| `1` | **Full Setup** | Register → Verify → Login → API Key → Free Models → Test |
| `2` | **Batch Create** | Create multiple accounts at once |
| `3` | **Create API Key** | Login with existing account, create new key |
| `4` | **Test API Key** | Test against all free models |
| `5` | **Enable Free** | Enable free models for existing account |
| `6` | **Account Status** | Check free tier, privacy, API keys |
| `7` | **Check Proxies** | Scan all proxies, show alive/dead |
| `8` | **List Accounts** | View saved accounts from `account.json` |

## 🔧 Configuration

**Everything lives in one file:** `tools/tokenharbor/config.toml`. No separate
secret files, no proxy `.txt` files. The file is gitignored — never commit it.

```toml
# OPTIONAL email-domain filter.
# Domains are fetched automatically from BlipMail (GET /api/config -> mailDomains).
# Leave empty to use ALL BlipMail domains, or set a comma-separated subset.
ALLOWED_EMAIL = ""

[tokenharbor]
base_url = "https://tokenharbor.ai"
turnstile_sitekey = "0x4AAAAAADBuC8Knz1EJZx9-"

[tempmail]
# BlipMail — https://blipmail.mpruy.my.id/docs
base_url = "https://blipmail.mpruy.my.id"
api_base = "https://blipmail.mpruy.my.id/api"
timeout = 120
poll_interval = 3

[capsolver]
# enabled = true  -> Capsolver (needs api_key, fast)
# enabled = false -> Camoufox  (keyless browser solver)
enabled = true
api_key = "CAP-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
timeout = 90
poll_interval = 3

[camoufox]
# Keyless Turnstile solver, auto-used when capsolver.enabled = false.
# Requires: pip install camoufox && camoufox fetch
enabled = true
headless = true
humanize = true
timeout = 120
use_proxy = false
geoip = false
max_concurrency = 3

[proxy]
# enabled = false -> run direct (no proxy). Proxy pre-check is skipped.
enabled = true
# DataImpulse direct (rotating gateway)
protocol = "http"
host = "gw.dataimpulse.com"
port = 823
username = "your-dataimpulse-user"
password = "your-dataimpulse-pass"
check_timeout = 8
# Dynamic rotation: give each worker thread its own sticky session (distinct IP).
sticky = true
session_param = "sessid"
# Verify the exit IP differs per thread before using it.
verify_ip = true
ip_check_url = "https://ipinfo.io/json"
max_ip_retries = 5
# Optional extra proxies (full URLs)
# list = ["http://user:pass@host:port"]

[threads]
# Multi-threaded batch creation.
# enabled = false -> single-threaded (one account at a time)
# enabled = true  -> parallel worker pool of max_workers
enabled = true
max_workers = 5
start_delay = 0.5

[files]
account_output = "account.json"
account_txt = "accounts.txt"

[models]
free = [
    "deepseek-v4-flash:free",
    "mimo-v2.5:free",
    "qwen3.8-27b:free",
]
```

### 1. Email domains (auto-fetched)

Usable domains come **automatically from BlipMail** (`GET /api/config` →
`mailDomains`) and are rotated across, so signups spread over every domain
the instance offers. `ALLOWED_EMAIL` is an **optional filter**: set a
comma-separated subset to restrict generation to those domains; leave it
empty for all. If BlipMail is unreachable at start, `ALLOWED_EMAIL` is used
as fallback. Both `"a.com, b.com"` and a TOML array are supported.

### 2. Turnstile solver (Capsolver or Camoufox)

The solver is selected automatically:

- `[capsolver] enabled = true` **and** an `api_key` set → **Capsolver**
  (API key, ~3-5s per solve). Get a key at [capsolver.com](https://capsolver.com).
- `[capsolver] enabled = false` (or no key) → **Camoufox**, a keyless
  anti-detect Firefox that renders the widget and reads the token.

Camoufox needs a one-time setup:

```bash
pip install camoufox
camoufox fetch
```

Options in `[camoufox]`: `enabled`, `headless`, `humanize`, `timeout`,
`use_proxy` (route the browser through the same proxy), `geoip`, and
`max_concurrency` (how many browsers solve at once in batch mode).

### 3. Proxy (optional)

Set `[proxy].enabled = false` to connect **directly** — no proxy needed. The
CLI skips the proxy pre-check and full-setup/batch run without interruption.

When enabled, configure the DataImpulse rotating gateway credentials in
`[proxy]`. The CLI builds `protocol://username:password@host:port`
automatically and scans it before creating accounts — only alive proxies
are used.

**Dynamic rotation (per-thread IP):** with `sticky = true`, every worker
thread appends `__sessid.<unique>` to the username, so DataImpulse exits
from a distinct IP held for that session. Before using it, the CLI checks
the exit IP (`ip_check_url`, default `https://ipinfo.io/json`) and rotates
again if the IP is already claimed by another thread — so each thread is
guaranteed a different IP.

Add more proxies (e.g. extra gateways) under `proxy.list`.

### 4. Threads

`[threads]` controls batch parallelism:

- `enabled = false` — **single-threaded**, accounts created one at a time
  (`max_workers` is ignored)
- `enabled = true` — **multi-threaded** worker pool of `max_workers`
- `max_workers` — number of concurrent account workers (only when enabled)
- `start_delay` — seconds between starting workers (avoids burst rate limits)

Each worker gets its own fresh sticky-session IP (when proxy is enabled).
Results are written to `account.json` (full records) and `accounts.txt`
(`email|apikey` lines) under a lock, so concurrent writes are safe.

### 5. Rate limits ("You're doing that a bit fast")

TokenHarbor throttles signups on multiple axes:

- **Per network/IP** — "Too many sign-ups from this network" after ~3 rapid
  signups from one IP. Avoided by the per-thread sticky IP rotation.
- **Per email domain / global burst** — "You're doing that a bit fast" when
  many signups hit in quick succession (even from different IPs). Triggered
  by submitting all at once and/or using a single email domain.

Mitigations built in:

- **Per-thread sticky IP** — each worker exits from its own IP, so parallel
  signups are safe and **no global pacing is needed** (`signup_interval`
  defaults to 0; raise it only if you still see burst limits).
- `[rate_limit].retry_attempts` / `retry_backoff` / `backoff_multiplier` —
  on a rate-limit error, back off and retry with a **fresh proxy IP** and
  fresh device fingerprint.
- **Email domain rotation** — domains auto-fetched from BlipMail and rotated
  across, so signups spread instead of hammering one domain.
- Per-client User-Agent jitter (Chrome version varies per worker).

> When proxy is disabled, all workers share your host IP — use single-thread
> (`[threads].enabled = false`) to avoid per-IP rate limits.

### 6. Temp Mail (BlipMail)

The CLI uses [BlipMail](https://blipmail.mpruy.my.id/docs). It is an
anonymous-session API (no key): fetch a `sessionId` from `GET /api/session`,
send it as `x-session-id`, claim an inbox, then read its messages.

```
GET  /api/config                        -> {"mailDomains": [...]}
GET  /api/session                       -> {"sessionId"}
POST /api/inboxes                       -> {address, created_at}
GET  /api/inboxes/{address}/messages    -> [{id, from_address, subject, body, received_at}]
```

Each account flow claims the generated address under its own session, then
polls for the TokenHarbor email. Verification is read from the body: a
`verify-email?token=` link is preferred, with a 6-digit code fallback.

## 📁 File Structure

```
harbor-farm/
├── README.md
├── account.json                 # Saved accounts (full JSON)
├── accounts.txt                 # Saved accounts (email|apikey)
├── docs.md
└── tools/tokenharbor/
    ├── config.toml          # ⚙️ All config (gitignored, contains secrets)
    ├── example.config.toml  # Template to copy
    ├── __init__.py
    ├── config.py            # Central config loader
    ├── client.py            # Core HTTP client (signup, login, API key, free models)
    ├── cli.py               # Interactive CLI + Rich TUI
    ├── capsolver.py         # Turnstile solver dispatcher (Capsolver / Camoufox)
    ├── camoufox_solver.py   # Keyless Camoufox Turnstile solver
    └── tempmail.py          # BlipMail client + email parsing
```

## 🔄 How It Works

```
┌──────────┐    ┌──────────┐    ┌───────────┐    ┌──────────┐    ┌───────────┐
│  SIGNUP  │───▶│  EMAIL   │───▶│  VERIFY   │───▶│  LOGIN   │───▶│ API KEY   │
│ Next.js  │    │ BlipMail │    │ link/code │    │ Cookie   │    │ POST      │
│ Action   │    │ inbox    │    │           │    │ auth     │    │ /api/keys │
└──────────┘    └──────────┘    └───────────┘    └──────────┘    └───────────┘
                                                                      │
                                                               ┌──────▼───────┐
                                                               │  FREE MODELS │
                                                               │  POST        │
                                                               │  /api/me/    │
                                                               │  privacy     │
                                                               └──────────────┘
```

1. **Signup** — Multipart form data to the Next.js Server Action, Turnstile token from Capsolver or Camoufox
2. **Email** — Claim the BlipMail inbox and poll for the verification message
3. **Verify** — Extract the link (or 6-digit code) and hit `/verify-email?token=...`
4. **Login** — Server Action signin, extract Supabase chunked cookies → access token
5. **API Key** — `POST /api/keys` with cookie auth, take `plaintext` key
6. **Free Models** — `POST /api/me/privacy` with cookie auth
7. **Test** — `POST /v1/chat/completions` with API key Bearer auth

## 🆓 Free Models

| Model ID | Provider |
|---|---|
| `deepseek-v4-flash:free` | DeepSeek |
| `mimo-v2.5:free` | MiMo |
| `qwen3.8-27b:free` | Qwen |

Model list can be edited in `config.toml`.

## 📝 CLI Reference

```bash
# Interactive menu
python3 -m tools.tokenharbor.cli

# Full flow (random allowed email + password)
python3 -m tools.tokenharbor.cli full-setup

# Custom email
python3 -m tools.tokenharbor.cli full-setup --email me@mydomain.com --password 'Str0ng!Pass'

# Batch create N accounts
python3 -m tools.tokenharbor.cli batch 10

# Create key from existing account
python3 -m tools.tokenharbor.cli create-key --email me@mydomain.com --password 'Str0ng!Pass'

# Test an API key
python3 -m tools.tokenharbor.cli test-key thk_live_xxxxxxxx

# Enable free models
python3 -m tools.tokenharbor.cli enable-free --email me@mydomain.com --password 'Str0ng!Pass'

# Check account status
python3 -m tools.tokenharbor.cli status --email me@mydomain.com --password 'Str0ng!Pass'

# Check all proxies
python3 -m tools.tokenharbor.cli check-proxies
```

## 💡 Tips

- Accounts are appended to `account.json` (full JSON) and `accounts.txt` (plain `email|apikey`)
- Emails use random local parts over BlipMail's domains (auto-fetched, optional `ALLOWED_EMAIL` filter)
- Proxies are scanned before creating — dead proxies are skipped (only when `[proxy].enabled = true`)
- TokenHarbor rate limits are bypassed by rotating the DataImpulse proxy
- Capsolver solves a Turnstile in ~3-5s; Camoufox (keyless) in ~10-20s
- Batch mode runs `max_workers` accounts in parallel (when `[threads].enabled = true`), each on its own sticky IP

---

## 🙏 Credits

Fork of [masanto/harbor](https://github.com/masanto/harbor). Original author **MASANTOID**.

<p align="center">
  <sub>maintained by <b>fauzihub13</b> · <a href="https://tokenharbor.ai">TokenHarbor</a></sub>
</p>