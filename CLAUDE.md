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
exception is the two playtests under "Other checks", which never run in GitHub and are run here
before a release. The `github-ci` skill (`.claude/skills/github-ci/SKILL.md`) has the details.

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
python scripts/playtest_discord.py --launch        # the Discord half: the real bot under a simulated Discord, every leaf pressed (rc.33 in docs/FINDINGS.md)
python -m compileall -q app                        # compile-check production Python
python -m json.tool content/world.json >/dev/null  # validate world content JSON
make lock                                          # regenerate requirements.lock (uv) after editing requirements.txt; the Dockerfile installs it under --require-hashes
```

The engine half drives every allowlisted operation through the engine's HTTP API; the Discord half
boots `app.bot` unmodified inside SimCord (`simcord`, a dev dependency only: nothing under `app/`
imports it) and presses every leaf the hubs register. **It is a script, not CI**: the bot cannot
boot without the Go engine and the CI `python` job has none, so like the engine half it is run
before a release (`python scripts/playtest_discord.py --launch` builds and starts one).
`tests/python/contracts/test_playtest_coverage.py` holds both to the live surface of operations and
leaves, and `docs/playtest/v<version>.md` is the checklist of what only a live server can show.

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

### Subsystem notes (`docs/ARCHITECTURE.md`)

One line each; read the matching `###` section of `docs/ARCHITECTURE.md` before changing the subsystem.

- **NPCs who go missing** (`npc_missing.go`, schema 47) — `status='missing'` with `missing_since_game_minute`; they cannot free themselves, starve after `missingGraceDays`, and the disappearance (significance 82) is the first event to reach the Quest Forge; `npc.found` and an `/explore` find them.
- **Somebody gets there first** (`npc_grave_robbing.go`) — a last `npc_life` step empties the graves nobody claimed after `graveRobGraceDays`; the deed is `hidden`, the keepsake is fenced at the nearest house, and emptiness is `claimed_game_minute`, never the anonymised user id.
- **One system's error ends the tick** (`npc_consignments`, schema 50) — `runSystems` stops at the first error, so a batch skips a bad row; NULL is the NPC-seller sentinel and `game.PayLotSellerTx` the one payout.
- **What a quest is allowed to ask for** (`app/rules/quests.py`) — `OBJECTIVE_TYPES` is the ceiling and the engine enforces none, so a type needs a reporter that speaks after the engine agreed and after the command answered.
- **People this world makes for itself** (`npc_registry`, schema 49) — catalogue, birth-family and descendant NPCs; the registry is runtime state a content rebuild never touches, and a name the catalogue holds is never taken.
- **The content file as tables** (`content_*`, `internal/contentsync`, schema 51) — nine tables the engine alone fills from `content/world.json`, hash-gated, by three doors (db-init, the bot at `CATALOG_READY`, `admin.content.reload`); tests fill them with `seed_content_tables` in `tests/support.py`.
- **The readiness probe** (`OPERATIONAL_REQUIRED_TABLES`) — the exact set of tables a fresh bootstrap makes; a new table must be listed (`test_startup_health`).
- **RAG / memory** (`app/ai/rag`) — SQLite FTS5, permission-filtered before scoring; retrieval surfaces known canon and never creates it.
- **Structured world history** — `world_history_events` with `public` / `participant` / `faction` / `hidden` visibility; hidden never reaches narrator RAG.
- **World events and their sites** — an event is a row plus a finite site (`world_event_nodes`, `world_event_npcs`) from `event_sites` in content; every spawn path must call the site spawn.
- **Narration routing** — routine and epic chains ending in procedural text; a live call only for dialogue, an epic beat or an explicit ask, behind one per-player action meter.
- **Narration routes in the dashboard** — the panel picks the five slots from OpenRouter's live free catalogue; the engine write (`admin.narration.set_chain`) comes first, then a poke to the bot.
- **Dashboard** (`app/dashboard`, `dashboard/`) — the authenticated GM control plane: reads through Go query sessions, every mutation audited, `/api/capabilities` as the coverage contract, the Player Editor owning every `player.*` lever.

## Standing rules

Each is a lesson a release found the hard way; the release in brackets is the key into
`docs/FINDINGS.md`. The recurring fault here is never a broken mechanic but a finished one with one
wire missing, so before calling a change done, ask which door still does not reach it.

**Doors and bounds**

- **One door per rule.** A rule written in two places drifts apart: the clock (rc.39), the purse (rc.43), a world's money and the way out of it (rc.44), the catalogue lookup (v1.7.0), insight XP (rc.58). One function, every site calls it, an AST gate holds it; a content vocabulary or a tuple is given a name and imported, never copied (v1.0.12).
- **A bound that lives in the client is not a bound (rc.48).** The caller never says what time it is, how long a wait or a retreat lasts, or what happened (rc.56, v1.2.3, v1.3.1); the engine decides and refuses or ignores the caller's version. Presentation may anticipate a refusal, never be the only place the rule lives (v1.0.6).
- **Gating is advertising, never a bound (v1.0.9).** The curriculum, a path's doors and a place's buttons decide only what is drawn; the engine stays the only refusal, and a floor on a leaf nothing but a hub press can reach is a bound (v1.1.0).
- **A surface must not offer what the engine will refuse (rc.46),** and a card must not name what its own picker will not offer (v1.0.8, v1.0.10): pickers, journals and next-step buttons ask the engine's own question (craft v1.0.5, a shop's door v1.7.1).
- **Never hide a status read.** *"never a status read or the door into the system, because a road nobody can see is a road nobody learns exists"* (rc.32); every status read stays open at realm 0 (v1.0.13). Check a rule against "and the rest", not only the examples it cites.
- **A promise a setting can falsify is a promise nobody is holding (rc.56, v1.0.13).** A card, panel or error that states a number, a wait or a window reads it from the engine or the setting; the bot prints the engine's numbers and restates no rule (rc.55, v1.30.0).
- **Money and worlds each have one door (rc.43, rc.44).** Stones are written through `walletDeltaTx`, a price or reward is in the base currency of the world it is paid in, and `moveCharacterTx` is the only way out of a world.
- **A city's gate is that city (v1.0.9).** Compare places through `cityOf` / `city_of_place`, never by string equality; `world_of_location` answers None, not the Mortal World (rc.52).
- **A price is walked against its loop before it ships (v1.0.17, v1.2.3, v1.8.0).** A sell price stays under every price that sells the item, anywhere in the same coin; walk the whole catalogue as the gate does.
- **Hidden is hidden for every consumer.** A reader documented as a superset is filtered by each consumer (v1.0.13), and an unwitnessed deed is `hidden` (rc.24).

**Values and failures**

- **A fallback that looks like a value is not a sentinel (rc.28).** `seller_user_id=0`, `gradeIndex` (rc.55), `0 or 50` (v1.0.13), a missing era key read as 0 (v1.0.7): ask whether a field is absent, never whether it is falsy; an unknown key refuses or answers 1, never the bottom rung; an unreadable reading says "unknown", never a zero (v1.0.8).
- **One system's error ends the tick (`npc_consignments`, rc.28).** `runSystems` returns on the first error and takes every batch ordered after it; a batch skips a bad row and never returns an error over one row's data.
- **Read back on a fresh connection (rc.38).** A test that writes and reads on one open connection cannot see a handler that never committed (`npcFound`); drive the production dispatch with the real world file (v1.0.11).
- **State a player sits behind is settled by the action, not by a flag-gated sweep (rc.56, v1.0.4);** a lock on state only an action can clear self-clears unconditionally, and the way out stays open.
- **Record before the reply, tell after it (v1.0.5, rc.28).** A quest report is written before the command answers and announced after it; a reply that raises after the engine committed tells the player nothing happened (v1.0.3), so key every result a formatter reads.
- **A panel provider never raises (v1.0.10).** A line drawn beside everything else (the Here line, hidden doors, the seat lines) costs the line, not the card; an unreadable flag or roster fails towards play (rc.41, v1.0.9).
- **Anything that deletes a person's rows collects their Discord threads and cards first and deletes them after the engine commits (v1.0.8, v1.7.0);** a table keyed on a person is classified in `erasureAnonymise`.

**Reach**

- **Something built and read by nothing is a fault** (`/learn` rc.43, the peach rc.50, the root grade rc.55, dead modifiers rc.58, fields v1.0.1). A command is on a hub page or the tree, an item has a source, a quest has a giver, a stat is fetched where it is consumed; give the class a gate when you find one.
- **Fix every site of a rule, not the one reported.** Craft and forage (v1.0.3), the picker's three siblings (v1.0.8), `/stall` then `/boss` (v1.7.4, v1.7.10), five channel helpers (rc.59), nine doors (v1.12.3): grep for the siblings before calling it fixed.
- **A change reaches the worlds already running.** Re-parent, migrate or grandfather (rc.51, rc.59, migrations 55 and 62), and a new rule never shortens what a player has already committed to (rc.56).
- **Migrations.** A shipped one is frozen and carries frozen copies of the content it rewrites (migration 46, v1.19.1); a column a migration adds is not also in the base DDL (rc.57); a chain changed in content needs a migration (v1.23.2); a read of a newer table or column is guarded so the migration window degrades instead of failing (v1.1.0).
- **An engine `.env` key needs a compose passthrough (rc.39).** The engine service has an explicit `environment:` allowlist and no `env_file`; a default stated in the Go table, `.env.example` and compose must agree (v1.0.13).
- **The bot allows itself before it denies anybody (rc.52).** Discord overwrites are merged, never replaced (v1.0.11), and reach channels that already exist (rc.59, v1.7.1).

**Gates and drills**

- **Drill every gate.** Revert the fix and watch the test fail with a sentence that names the finding; restore from a copy or the index, never `git checkout` a file holding uncommitted work (v1.3.0). A gate that cannot see what it forbids passes a broken tree (rc.47, rc.49).
- **Read code by AST, not by searching text (rc.52).** A disabled condition leaves its substring in place (rc.49); read call expressions and statements without the docstring; a sweep whose every hit needs hand-checking is not a gate (v1.0.1).
- **Assert the reader works before trusting it (rc.57).** A reader that silently finds nothing makes every assertion after it vacuous.
- **A gate holds the rule, not its spelling or a claim (v1.0.8, v1.0.3).** One pinned to where a rule is written fails exactly when the rule moves; one that encodes a claim makes you change the rule to satisfy it.
- **A Python twin is held to the Go (rc.55, v1.0.9).** Python cannot call Go, so a display twin is one authored number with two readers and a test that reads the Go expression; compute it a third time off the raw content so two wrong halves cannot agree.
- **A helper's own test does not prove anything calls it.** Every-door gates read the call sites by AST (v1.9.1, v1.13.0).
- **Playtests are scripts, not CI (rc.33).** Run them here before a release; "driven" means resolved, not called into a refusal (rc.58), and a step that can succeed at doing nothing is not a step (v1.7.1).

## Findings and subsystem history

`docs/ARCHITECTURE.md` (how each subsystem works) and `docs/FINDINGS.md` (what each release found, in
the order found, with the drills that proved its gate) are not loaded with this file. **When a
subsystem, a gate or an old release label (`rc.48`, `v1.0.8`) comes up, grep them** -
`grep -n 'rc.48' docs/FINDINGS.md` - and read the section; code and tests cite those labels by the
thousand. **A release's findings are appended to `docs/FINDINGS.md`** under a `### Title (v<version>)`
heading, never here; a line is added to the Standing rules only when the lesson is new.
`tests/python/contracts/test_claude_md_stays_small.py` holds this file under 40,000 characters.
Never `@import` either file and add no nested `CLAUDE.md`: both bring the weight back.

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
  the last: `v1.1.0` → `v1.1.1` → `v1.1.2`. Never a fourth part (`1.0.13.1`): `ENTRY` in
  `app/rules/changelog.py` and the release-version gate read at most three, so it would break the
  changelog parse. A small fix is still a full bump - every stamp below, its own `VERSIONS.md`
  entry, the checklist regenerated and the manifest rewritten.
- **The stamps.** The version is stamped in seven places and `tests/python/contracts/test_release_version.py`
  holds them equal: `app/version.py`, `VERSION`, the `Dockerfile` label, `docker-compose.yml`, the
  README title (its first line) and **both** of `VERSIONS.md`'s stamps - the
  `## Release status — v…` heading *and* the `- Current release: v…` line under it, two separate
  assertions - plus the one literal in that test, the only place the number is spelled out in the
  suite. A new schema number is stated in three more places: `Current schema version is N;` in this
  file, `The current schema is **N**` in the README and a `- **Schema N** ` line in `VERSIONS.md`.
- **The generated files.** `docs/playtest/v<version>.md` is regenerated by
  `scripts/playtest_checklist.py` (a bump to a new version carries the previous checklist's ticks
  and dates them; no Python source may spell a checklist filename - build it from `VERSION`), and
  `RELEASE_MANIFEST.sha256` by `scripts/release_manifest.py --write`.
- **A `VERSIONS.md` entry is one header.** A second `**1.0.1**` line inside an entry starts a new
  entry and truncates the notes; continuation paragraphs begin "It also…" or "And…", and the
  first paragraph leads, because `#updates` posts its opening sentence as the headline
  (`tests/python/unit/test_release_headline.py`).
- **A release's findings go to `docs/FINDINGS.md`,** never here: a `### Title (v<version>)` section in
  the voice of the others - what was found, why the gates missed it, what each drill prints.
- A release is handed over as the zip — `xianxia_rp_v<version>.zip` — and its `.zip.sha256`
  sidecar. No `..._to_..._code.patch`: a diff nobody applies is noise, not assurance.
  The sidecar is **not** optional and this file used to say it was: `update.sh --fetch`
  downloads it, verifies the archive against it, and refuses to install one that has no
  sidecar ("refusing an unverifiable archive"). The release job attaches both for that
  reason. A hand-unpacked zip still never needs it read by a person — the updater reads it.
- `RELEASE_MANIFEST.sha256` is a different thing and stays: it lives *inside* the tree, is
  regenerated by `scripts/release_manifest.py --write`, and is what proves the shipped tree is
  intact after unpacking. Keep running it before every release.
