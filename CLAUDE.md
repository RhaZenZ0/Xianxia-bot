# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A persistent Xianxia role-playing Discord bot. Python owns Discord, RAG, dashboard, and presentation
orchestration; Go owns canonical gameplay rules, current game time, simulation mutations, and SQLite
WAL state. Designed for CPU-only QNAP/NAS deployment — no local LLM, narration comes from cloud
free-tier models (OpenRouter, plus an optional direct Google AI Studio route) with a fallback chain
ending in procedural (non-AI) narration.

## Commands

Setup:

```bash
python3 -m venv .venv
. .venv/bin/activate
make install-dev
```

Set `ENGINE_AUTH_TOKEN` in `.env` to the same value for both the Python services and the Go engine
(`python3 -c "import secrets; print(secrets.token_urlsafe(32))"`). The Go engine needs CGO SQLite
bindings (`libsqlite3-dev` on Debian/Ubuntu).

Full local check suite (mirrors CI):

```bash
make check          # lint + format-check + test-python + test-go
```

Individual commands:

```bash
make test-python     # python -m pytest -q
make test-go         # cd go_core && CGO_ENABLED=1 go test ./...
make lint            # ruff check app scripts; go vet ./...
make format-check    # gofmt -l go_core
```

Run a single test / a layer of the Python suite:

```bash
pytest -q path/to/test_file.py::test_name
pytest -q -m unit           # fast Python-only tests
pytest -q -m integration    # repository/orchestration tests still owned by Python
pytest -q -m contract       # Python<->Go HTTP/RPC, startup, deployment, release boundary tests
```

Single Go test:

```bash
cd go_core && go test ./internal/game/... -run TestName
```

Other checks:

```bash
python scripts/check_dashboard_implementation.py   # dashboard frontend/backend drift + coverage gate, part of the release gate
python -m compileall -q app                        # compile-check production Python
python -m json.tool content/world.json >/dev/null  # validate world content JSON
```

Run without Docker:

```bash
cd go_core && go run ./cmd/xianxia-core
# in another shell:
export GAME_ENGINE_URL=http://127.0.0.1:8081
python -m app.database.bootstrap
python -m app.bot
python -m app.dashboard   # optional
```

Docker/QNAP: `./startup.sh` / `./stop.sh`. Reset the world (takes a safety backup first): `./reset_database.sh`.

## Architecture

### Authority split (the single most important rule in this repo)

- **Go is authoritative** for canonical mechanics and is the only thing that opens the production
  SQLite database. It owns game/admin actions (scene transitions, NPC relationship updates, quest
  progression, combat damage, cultivation rewards, GM mutations), native batched world simulation
  (`npc_civilization`, `npc_life`, `dynamic_economy`, `black_markets`, `sect_politics`,
  `clan_dynamics`), and the canonical game clock.
- **Python** owns Discord commands/views, RAG/context assembly, permissions, and presentation. It
  talks to Go over HTTP/RPC (`app/database` Go remote DB transport, `app/ops/game_engine` client) —
  it never opens the production SQLite file directly.
- **AI is narration-only.** `/action` selects intent through a guided UI; deterministic mechanics
  resolve the result first, and the LLM only describes the already-decided outcome. AI cannot write
  rewards, deaths, relationships, travel, or history. Narration falls back to procedural (template)
  text if every OpenRouter route fails or the daily free-tier quota is exhausted — gameplay must
  survive AI outages.
- There is intentionally no Go "shadow mode" duplicating Python calculations, and no local LLM/Ollama.

### Design rules for future work (from README, enforced by intent)

1. Do not reintroduce a Go shadow mode — migrated mechanics execute once, in Go.
2. Do not open production SQLite from Python — add a Go action/batch or Go-hosted repository session.
3. Batch world work — use a native Go batch instead of one HTTP/DB op per NPC.
4. Keep AI non-authoritative (see above).
5. Keep viewpoint permissions deterministic — RAG must never leak hidden/participant/faction-only info.
6. Any new Admin Console action must write to `admin_audit_log`.
7. Prefer native Go tests for Go-owned rules; pytest should assert the Python-owned boundary, not
   re-implement engine formulas.

### Python layout and layering (`app/`)

```text
bot/         Discord frontend: runtime, services, commands/, admin/, ui/, surface wiring
rules/       gameplay rules and content helpers (pure: alchemy, aptitudes, birthfamily, samsara,
             sect*, worldtime, game/World...) - imports nothing above it
ai/          ai_router (OpenRouter routing), narrator + narrator_context, rag, chat_monitor
ops/         config, health/http_limits, game_engine (Go client), core_services, healthcheck entrypoint
dashboard/   authenticated GM web control plane (server.py) + front-end contract
database/    Python repository API, Go remote DB transport, bootstrap entrypoint
simulation/  Python orchestration/compatibility during migration
version.py   the release stamp
```

Enforced layering (see `tests/python/unit/test_app_layout.py`):
`{rules, ops} <- ai <- database <- simulation <- dashboard <- bot`, and `rules`/`ops` do not import
each other.

### Go layout (`go_core/`)

```text
cmd/xianxia-core/       service entry point
internal/game/          authoritative game/admin actions
internal/simulation/    native batched world simulation
internal/storage/       SQLite WAL ownership, sessions, backup support
internal/server/        HTTP control/data plane
```

Every Go SQLite connection uses `journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=10000`,
`synchronous=NORMAL`. Current schema version is 32; historical migrations are kept so old databases
can upgrade in place — see `VERSIONS.md` for the full schema/release history.

### RAG / memory (`app/ai/rag`)

Deterministic and SQLite-first (FTS5), not embedding/vector-based. Retrieval never creates game
truth — it surfaces known canonical information only, permission-filtered before scoring:

```text
live structured SQL -> permission-filtered FTS5 candidates -> deterministic scoring
  -> small scene-specific context packet -> narrator
```

Raw player text is tokenized/sanitized before building FTS5 queries (never passed straight to
`MATCH`). The corpus deliberately excludes NPC secrets, unrevealed schedules, undiscovered
locations/manuals, raw DB dumps, and GM-only state.

### Structured world history

`world_history_events` records what mechanically happened (deaths, battles, succession, discoveries,
etc.), separate from current structured state (what's true now — always wins over history). Rows
carry visibility levels `public` / `participant` / `faction` / `hidden`; hidden rows never reach
narrator RAG, and a focused NPC does not inherit the player's participant-only knowledge.

### Narration routing

Two chains, "routine" (ordinary scenes) and "epic" (breakthroughs, sect trials, major events), each
walking primary model -> fallback model -> `openrouter/free` -> procedural narration on failure.

Reasoning is disabled per-request (`OPENROUTER_DISABLE_REASONING=true`) and `OPENROUTER_REQUIRE_FREE`
rejects paid model IDs. Rate limiting is fail-fast (no queuing) and shared with the admin
`chat_digest` monitor, so an unbounded transcript can starve narration — see `MONITOR_*` env knobs.

Optionally (v0.26.0) a direct Google AI Studio route (`aistudio/<model>`, `app/ai/google_route.py`)
leads both chains when `GOOGLE_AI_STUDIO_API_KEY` is set. It is the one route that does not go
through OpenRouter, so it deliberately does not spend `AITaskRouter.limiter` - OpenRouter's daily
free budget - and `OPENROUTER_REQUIRE_FREE` does not apply to it. It is still narration-only and
still passes through `_validate_generated_text`, so it is not trusted more than any other route.
`google-genai` is an optional, lazily imported dependency: absent or incompatible, the route is left
out of the chain and `ai_status` reports why.

Every `ROUTE_AUDIT_HOURS` (v0.27.0, default 24, `0` off) `audit_routes()` pings each configured route
with the cheapest call the API takes — one character in, `max_tokens=1`, reply discarded unread — and
retires the ones that answer `401`/`403`/`404`, plus a `400` confirmed at an ordinary token size (the
reasoning-mandatory case). `429`s and timeouts never retire anything; that is what the per-route
cooldown is for. The audit spends the shared budget it uses, stands down below half the daily
allowance, and retires nothing when *every* route fails at once (a local fault, not an empty
catalogue). It proves reachability only — a scratchpadding model passes it, so
`_validate_generated_text` remains the sole judge of whether a reply is usable prose.

### Dashboard (`app/dashboard`, `dashboard/`)

Authenticated GM control plane; production reads go through Go-owned query sessions (dashboard never
opens SQLite directly), and every state-changing GM action is written to `admin_audit_log`.
`/api/capabilities` is the frontend/backend coverage contract checked by
`scripts/check_dashboard_implementation.py` and CI.

## Testing conventions

- `tests/python/unit/`, `integration/`, `contracts/` mirror the Python ownership boundaries above —
  put new tests in the layer they actually test, and don't duplicate Go-owned formulas/state
  transitions in pytest once a mechanic has moved to Go.
- `tests/support.py` holds shared dependency shims and test path helpers.

## Release delivery

- A release is handed over as **the zip alone** — `xianxia_rp_v<version>.zip`, nothing beside it.
  No `..._to_..._code.patch` and no `.zip.sha256`: Mitchell unpacks the zip over the previous
  tree, so a diff he never applies and a checksum he never runs are noise, not assurance.
- `RELEASE_MANIFEST.sha256` is a different thing and stays: it lives *inside* the tree, is
  regenerated by `scripts/release_manifest.py --write`, and is what proves the shipped tree is
  intact after unpacking. Keep running it before every release.
