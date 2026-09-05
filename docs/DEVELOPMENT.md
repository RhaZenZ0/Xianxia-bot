# Development

## Setup

Use Python 3.12 or newer and Go 1.23 or newer. Install the development tools with:

```bash
python3 -m venv .venv
. .venv/bin/activate
make install-dev
```

Set `ENGINE_AUTH_TOKEN` in `.env` to the same random value for the Python services and Go engine. Generate one with:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

The Go engine uses CGO SQLite bindings. On Debian or Ubuntu, install the SQLite development package first:

```bash
sudo apt-get install libsqlite3-dev
```

## Checks

Run the complete local check suite with:

```bash
make check
```

Individual commands are available when iterating:

```bash
make test-python
make test-go
make lint
make format-check
```

Python tests are grouped as `unit`, `integration`, and `contract` tests. For example:

```bash
python -m pytest -m unit
python -m pytest -m contract
```

The same Python and Go checks run in GitHub Actions. Container builds run after both language checks pass.