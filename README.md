# 🏗️ Harbor

> **TokenHarbor Auto CLI** — HTTP-only, no browser, pure Python.

Auto create accounts, generate API keys, and activate free models on [TokenHarbor](https://tokenharbor.ai).

## Quick Start

```bash
git clone https://github.com/masanto/harbor.git
cd harbor
python3 -m venv .venv && source .venv/bin/activate
pip install rich requests
cp tools/tokenharbor/example.config.toml tools/tokenharbor/config.toml
# edit tools/tokenharbor/config.toml (ALLOWED_EMAIL, proxy, capsolver key)
python3 -m tools.tokenharbor.cli
```

## Docs

See [tools/tokenharbor/README.md](tools/tokenharbor/README.md) for full documentation.

## License

MIT