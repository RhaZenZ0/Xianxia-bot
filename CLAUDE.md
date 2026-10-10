# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A persistent Xianxia role-playing Discord bot. Python owns Discord, RAG, dashboard, and presentation
orchestration; Go owns canonical gameplay rules, current game time, simulation mutations, and SQLite
WAL state. Designed for CPU-only QNAP/NAS deployment — narration comes from cloud free-tier models
(OpenRouter, plus an optional direct Google AI Studio route) with a fallback chain ending in
procedural (non-AI) narration.

## Commands

Setup:

```bash
python3 -m venv .venv
. .venv/bin/activate
make install-dev
```

Set `ENGINE_AUTH_TOKEN` in `.env` to the same value for both the Python services and the Go engine
(`python3 -c "import secrets; print(secrets.token_urlsafe(32))"`). The Go engine needs CGO SQLite
bindings (`libsqlite3-dev` on Debian/Ubuntu). `.env.example` is keys, defaults and section
separators only — a contract test holds it to that — and `docs/CONFIGURATION.md` is where every
key is explained; a new key gets its line in both.

**The full suite runs on GitHub, not here.** `.github/workflows/ci.yml` runs all of it on every
pull request and every push to main, so to verify a change, run only the targeted checks for what
it touches (the tests that name the changed file, module or content key; `gofmt`, `go vet` and
`go test` for the touched Go package; `scripts/release_manifest.py --write`), push, and read CI.
Do not run `make check` or a whole `pytest -q` to verify a change: it takes about eight minutes
here and still proves nothing about the Go race detector, the pinned tools or the containers. A
branch with no pull request runs no CI, so a change is checked once its PR is open. The one
exception is the two playtests below, which never run in GitHub and are run here before a release.
The `github-ci` skill (`.claude/skills/github-ci/SKILL.md`) has the details.

`make check` is still the whole suite in one command, for a person on a machine that has time:

```bash
make check          # lint + format-check + test-python + test-go
```

Individual commands:

```bash
make test-python     # python -m pytest -q
make test-go         # cd go_core && CGO_ENABLED=1 go test ./...
make lint            # ruff check app scripts; go vet ./...; staticcheck ./... (fails if staticcheck is missing)
make tools           # installs staticcheck + govulncheck at the versions the Makefile pins; CI runs those
make audit           # govulncheck ./... - the one check that needs the network, so it is not in lint/check
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
python scripts/playtest_all.py                     # the release check: both halves at once, the Discord sweep in 3 parts
python scripts/playtest_engine.py --launch         # the engine half of the playtest: every operation, against a scratch engine
python scripts/playtest_discord.py --launch        # the Discord half: the real bot under a simulated Discord, every leaf pressed (see below)
python -m compileall -q app                        # compile-check production Python
python -m json.tool content/world.json >/dev/null  # validate world content JSON
make lock                                          # regenerate requirements.lock (uv) after editing requirements.txt; the Dockerfile installs it under --require-hashes
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
Rebuild `.env` on a new release's `.env.example`, keeping the values already set (an upgrade
never edits `.env`, so a release that adds a key leaves the two to drift): `./migrate_env.sh`
— `--dry-run` first. The backup it writes, `.env.bak.<timestamp>`, holds the same tokens and is
excluded from git, the release archive, the manifest and the updater's delete loops.

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
- There is intentionally no Go "shadow mode" duplicating Python calculations.

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
simulation/  Python orchestration over engine queries (no SQL since v0.30.0)
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
`synchronous=NORMAL`. Current schema version is 79; historical migrations are kept so old databases
can upgrade in place — see `VERSIONS.md` for the full schema/release history.

## Subsystem notes and findings

The subsystem essays are in `docs/ARCHITECTURE.md` and the release history is in `docs/FINDINGS.md`;
neither is loaded automatically.

## Testing conventions

- `tests/python/unit/`, `integration/`, `contracts/` mirror the Python ownership boundaries above —
  put new tests in the layer they actually test, and don't duplicate Go-owned formulas/state
  transitions in pytest once a mechanic has moved to Go.
- `tests/support.py` holds shared dependency shims and test path helpers.
- **A fixture must carry the constraints production carries.** `npc_consignments` shipped broken
  for thirteen releases with its Go test green, because the test's `auctions` fixture declared
  `seller_user_id INTEGER NOT NULL DEFAULT 0` with no foreign key and no `characters` table at all,
  while production foreign-keys that column and opens every connection with `foreign_keys=ON`. The
  fixture accepted the one value production refused. A fixture that cannot fail the way production
  fails is not testing production — copy the real DDL, foreign keys included, and seed the parent
  rows.
- **Never assert that a random thing happened, however many iterations you give it.** The simulation
  is built out of low-probability rolls and `gamerng` is `crypto/rand` with no seed, so a
  "sixty ticks and surely one landed" test fails for no reason at some rate you cannot drive to
  zero. Use `gamerng.UseRoller(fn)` (v1.0.0-rc.24) — it lends the dice to one test and returns the
  restore, which you must `defer`; `fn` receives the bound so one kind of roll can be answered
  differently from another, and its answer is clamped into the die. It is test-only and a test in
  `gamerng` walks every non-test file in `go_core` to keep it that way. Where the outcome can be
  made certain by the *scenario* instead (overwhelming attributes, a stacked fixture), prefer that.
- **A name is not a reader.** A gate that asks "does production mention this string" cannot tell a
  modifier from a database column, and this tree has both under nearly the same name:
  `sense_precision_bonus` occurs three times in production Go, once as a modifier and twice as the
  `characters` column inside a SQL string, while the modifier vocabulary wants the bare
  `sense_precision`. A substring search called the stat read; so did a scan for the identifier; and
  a Soul Wound had never dulled anybody's sense for it (v1.0.0-rc.58). Ask instead whether the
  string appears **where a value is consumed** - an argument position of a named reader, a key on
  the resolved bundle, a stat-chooser's return - which a column in a SQL string cannot satisfy.
  This is rc.52's "read calls by AST, not by substring" one level down: the AST has to distinguish
  *which* use, not merely that the identifier is present.
- **A gate that can go quiet is decoration too, and only its own drill says so.**
  `modifier_vocabulary_test.go` copied `shippedCatalog`'s defensive `t.Skipf` for an unreadable
  content file, so pointing its path at nothing made the whole test SKIP - green, silent and
  useless. The content file is in the repository and always present, so a read that fails means the
  gate cannot do its job: it is a `t.Fatalf`. Drill the reader, not only the rule.
- **And a gate now says so, because the rule above was prose for four releases and was broken three
  times in them** (v1.0.0-rc.42). `TestOnlyTestsBorrowTheDice` only ever looked one way — production
  must not borrow the dice — and the direction it cannot see is the expensive one.
  `TestATestThatAssertsARollLandedLendsTheDice`, beside it in `gamerng`, walks `internal/simulation`
  and `internal/game` by AST and asks two questions of every test: **can it reach a draw**, which is
  a real call graph (the production functions that name `gamerng`, closed over same-package calls,
  then extended through the package's own test helpers, so a test driving the tick through
  `runHunts(t, path, r, 200)` counts), and **does it assert something happened**, which is a tally
  coming back zero (`x == 0`, `len(x) == 0`, `x < 1`). A test that does both must lend the dice —
  `UseRoller`, a helper that wraps it, or an assignment to one of the `game` package's `*Intn` seam
  vars — or be named in `diceAllowed` with its reason, the shape `DEFERRED_OPERATIONS` and
  `DROPPED_TABLES` already use here. It is a **shape detector, not a proof**: it cannot compute a
  probability, and it deliberately ignores `!flag`, because in this tree a negation is almost always
  a comma-ok `!ok` or a predicate about the fixture and admitting it produced eleven false positives
  against one true one. The thirteen `diceAllowed` entries are all one of two kinds — a count of
  content (`len(location.Roads)`, the authored manuals) or a floor in the production code that makes
  the zero unreachable (`law.comprehend` clamps its gain to 1) — and each says which.

- **A success string is not an exit code, and `make check` has two that look alike.** The first
  thing `make check` runs is `ruff`, which prints **"All checks passed!"** when it is clean - a
  sentence that appears nowhere in the Makefile and says nothing about the eight commands after it.
  Piping the run through `grep` to read that line reports a green suite while `lint` is failing at
  staticcheck four lines later, which is how v1.0.1's own field gate reached CI with an `SA1019`
  on `go/parser.ParseDir` in it. Capture `$?` from `make check` itself; a pipeline's status is the
  last command's, so `make check | grep ...` returns grep's.

## Release delivery

- **Version numbering, on the owner's call (v1.0.14).** A big update bumps the middle number and
  resets the last: `v1.1.0`, `v1.2.0`, and so on. A small fix keeps the big number and adds one to
  the last: `v1.1.0` → `v1.1.1` → `v1.1.2`. Never a fourth part (`1.0.13.1`): `_ENTRY` in
  `release_notes.py` and the release-version gate read at most three, so it would break the
  changelog parse. A small fix is still a full bump - every stamp listed under "The first bump that
  renames a file", its own `VERSIONS.md` entry, the checklist regenerated and the manifest rewritten.
- A release is handed over as the zip — `xianxia_rp_v<version>.zip` — and its `.zip.sha256`
  sidecar. No `..._to_..._code.patch`: a diff nobody applies is noise, not assurance.
  The sidecar is **not** optional and this file used to say it was: `update.sh --fetch`
  downloads it, verifies the archive against it, and refuses to install one that has no
  sidecar ("refusing an unverifiable archive"). The release job attaches both for that
  reason. A hand-unpacked zip still never needs it read by a person — the updater reads it.
- `RELEASE_MANIFEST.sha256` is a different thing and stays: it lives *inside* the tree, is
  regenerated by `scripts/release_manifest.py --write`, and is what proves the shipped tree is
  intact after unpacking. Keep running it before every release.
