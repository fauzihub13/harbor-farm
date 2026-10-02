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

- 🔐 **Auto signup** — Next.js Server Action + Capsolver Turnstile bypass
- 📧 **Local temp mail** — Polls your self-hosted temp-mail API for the 6-digit verification code
- 🔑 **API key generation** — Auto create + extract plaintext key
- 🆓 **Free model activation** — One-click enable all free models
- 🚀 **Batch mode** — Create N accounts in parallel across threads
- 🧵 **Multi-threaded** — Configurable worker pool (`[threads].max_workers`)
- 🌐 **Dynamic proxy rotation** — Each thread pins its own sticky session (distinct IP), verified via IP check
- 🛡️ **Proxy auto-check** — Verify proxies before use, filter alive only
- 🎨 **Rich TUI** — Interactive terminal UI with panels, tables, progress bars
- ⚙️ **Single config file** — Everything (email domains, proxy, Capsolver, threads) in `config.toml`

## 📦 Requirements

```bash
pip install rich requests
```

## 🚀 Quick Start

```bash
cd /path/to/harbor
source .venv/bin/activate
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
# Domains allowed to receive mail on your local temp-mail server.
# Comma-separated (multi value).
ALLOWED_EMAIL = "mpruy.my.id,example.com"

[tokenharbor]
base_url = "https://tokenharbor.ai"
turnstile_sitekey = "0x4AAAAAADBuC8Knz1EJZx9-"

[tempmail]
base_url = "http://localhost:8000"   # your local temp-mail API
timeout = 120
poll_interval = 3
limit = 50

[capsolver]
enabled = true
api_key = "CAP-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
timeout = 90
poll_interval = 3

[proxy]
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

### 1. ALLOWED_EMAIL

A comma-separated list of domains that your local temp-mail server accepts.
The CLI generates random local parts and rotates across these domains.
Both `"a.com, b.com"` and a TOML array `["a.com", "b.com"]` are supported.

### 2. Capsolver

Put your Capsolver API key directly in `[capsolver].api_key`.
Get one at [capsolver.com](https://capsolver.com) — a few dollars covers
hundreds of solves. Set `enabled = false` to skip the Turnstile step.

### 3. Proxy (DataImpulse)

Configure the DataImpulse rotating gateway credentials in `[proxy]`.
The CLI builds `protocol://username:password@host:port` automatically and
scans it before creating accounts — only alive proxies are used.

**Dynamic rotation (per-thread IP):** with `sticky = true`, every worker
thread appends `__sessid.<unique>` to the username, so DataImpulse exits
from a distinct IP held for that session. Before using it, the CLI checks
the exit IP (`ip_check_url`, default `https://ipinfo.io/json`) and rotates
again if the IP is already claimed by another thread — so each thread is
guaranteed a different IP.

Add more proxies (e.g. extra gateways) under `proxy.list`.

### 4. Threads

`[threads]` controls batch parallelism:

- `enabled` — turn multi-threading on/off
- `max_workers` — number of concurrent account workers
- `start_delay` — seconds between starting workers (avoids burst rate limits)

Each worker gets its own fresh sticky-session IP. Results are written to
`account.json` (full records) and `accounts.txt` (`email|apikey` lines)
under a lock, so concurrent writes are safe.

### 5. Temp Mail (local API)

The CLI talks to your local temp-mail server (see `temp-api/docs.md`):

```
GET /health                     -> {"status": "ok", ...}
GET /inbox/{email}?limit=50     -> {"email", "count", "emails": [...]}
GET /inbox/{email}/{uid}        -> single email detail
```

Each email has `uid`, `from`, `to`, `date`, `body`, `seen`. No inbox creation
is needed — just poll the inbox. Verification is read from the email body:
a `verify-email?token=` link is preferred, with a 6-digit code fallback.

## 📁 File Structure

```
tools/tokenharbor/
├── config.toml          # ⚙️ All config (gitignored, contains secrets)
├── example.config.toml  # Template to copy
├── __init__.py
├── config.py            # Central config loader
├── client.py            # Core HTTP client (signup, login, API key, free models)
├── cli.py               # Interactive CLI + Rich TUI
├── capsolver.py         # Capsolver Turnstile solver
└── tempmail.py          # Local temp-mail client + email parsing
```

## 🔄 How It Works

```
┌──────────┐    ┌──────────┐    ┌───────────┐    ┌──────────┐    ┌───────────┐
│  SIGNUP  │───▶│  EMAIL   │───▶│  VERIFY   │───▶│  LOGIN   │───▶│ API KEY   │
│ Next.js  │    │ local    │    │ link/code │    │ Cookie   │    │ POST      │
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

1. **Signup** — Multipart form data to the Next.js Server Action, Turnstile token from Capsolver
2. **Email** — Poll the local temp-mail inbox for the verification message
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
- Emails use random local parts over the domains in `ALLOWED_EMAIL`
- Proxies are scanned before creating — dead proxies are skipped
- TokenHarbor rate limits are bypassed by rotating the DataImpulse proxy
- Capsolver solves a Turnstile in ~3-5 seconds
- Batch mode runs `max_workers` accounts in parallel, each on its own sticky IP

---

<p align="center">
  <sub>by <b>MASANTOID</b> · <a href="https://tokenharbor.ai">TokenHarbor</a></sub>
</p>