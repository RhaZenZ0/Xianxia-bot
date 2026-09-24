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

Full local check suite (mirrors CI):

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
`synchronous=NORMAL`. Current schema version is 62; historical migrations are kept so old databases
can upgrade in place — see `VERSIONS.md` for the full schema/release history.

### NPCs who go missing (`npc_missing.go`, schema 47)

Somebody away from home can vanish. `status` carries `'missing'` beside `'alive'` and `'dead'`, so
every batch that reads `WHERE status='alive'` stops offering them by construction, and
`missing_since_game_minute` is the one thing about a journey worth storing — `npcTravel` deliberately
stores none, which is right for an errand and wrong for a disappearance.

**They cannot free themselves.** If they wandered home the quest would be decoration. What they can
do is last: the surroundings feed them for `missingGraceDays`, then health drains and they die of it.
That deadline is what makes the search mean something.

The significance is the point. `forge_quests_from_history` drafts a quest per public history row at
or above `QUEST_FORGE_MIN_SIGNIFICANCE` (default **80**) — and the whole simulation package tops out
at 74, so **no autonomous event has ever been able to reach the Quest Forge**. A disappearance is
written at 82 and is the first one that can. Resolution is mechanical: the `npc.found` action clears
it only when the caller is standing where the NPC actually is, and the engine checks that itself
rather than taking `/talk`'s word for it.

### Somebody gets there first (`npc_grave_robbing.go`)

The Tomb-Watch Clan has listed "Grave-robbers" among its troubles since the birth families were
written, and `npcFindChance` has always given the best find rate in the game to a grave/tomb/relic/
scaveng/prospect/digger/miner/salvage trade — but their finds were abstract, drawn from a catalogue
pool, because until schema 48 there was nothing in the world to dig up. A step of `npc_life`, last,
turns over the graves nobody came for, so arriving late is no longer the same as arriving.

Two things hold it in shape. **The grace** (`graveRobGraceDays`, 21 — three ticks) is the window in
which the grave is the searcher's alone: long enough for the Forge to draft the quest, a GM to pass
it and somebody to walk there. And **the deed is `hidden` while the goods are not**: nobody stood in
the wilderness and watched, so the history row never reaches narrator RAG and the world genuinely
does not know — but the keepsake goes under the hammer at the nearest house, and `auctions` is the
one fence of the two that carries `seller_npc_name`. A player who reaches an emptied grave and later
finds the dead herbalist's satchel listed under a known digger's name has worked it out from the
world rather than been told. The GM dashboard's Graves table reads the hidden row directly, because
a GM is not a player.

Emptiness is `claimed_game_minute`, never `claimed_by_user_id` — the latter anonymises on erasure
(see `erasureAnonymise`), so keying off it would let an erasure refill a grave.

### One system's error ends the tick (`npc_consignments`, schema 50)

`runSystems` walks `orderedSystems` and `return`s on the first error, so a batch that throws does
not merely fail — it takes every batch ordered after it, and the whole `advancedMaintenance` bundle,
with it. `npc_consignments` is fifth of eight.

It threw on every run from v1.0.0-rc.15 to rc.28. `consignToNearestHouse` wrote `seller_user_id=0`
for a find by one of the world's own people, and that column is foreign-keyed to `characters` while
every Go connection sets `foreign_keys=ON` — so SQLite refused it, every time, because **0 is not a
sentinel, it is just an id nobody holds**. The migration comment that introduced it had the
reasoning exactly backwards: it said 0 was used *because* the column is foreign-keyed, when being
foreign-keyed is precisely why 0 cannot be stored. The consequence was that `sect_politics`,
`clan_dynamics`, `autonomous_world_events`, commission expiry, auction settlement, merchant bidding
and secret-realm rotation had not run since rc.15. The batch is daily, so this was every day.

Schema 50 drops the `NOT NULL` and makes NULL the sentinel. **Nothing downstream changed**, because
every reader was already correct: `storage.ParseInt(nil)` is `0`, so each `if seller := i64(...);
seller > 0` guard reads a NULL exactly as it was always meant to read the sentinel, and
`payAuctionSeller` still pays a finder into their own `wealth`. Only the value it hinged on had to
become one the table can hold.

**Except one reader, one step downstream — and the review found it, not the playtest**, because a
consignment carries hours of real time before settlement and the harness never waits that long.
`merchantTakesLotTx` paid `walletDeltaTx(conn, i64(seller_user_id), …)` unconditionally, and
`MerchantsBid` bids on every open lot, so the first NPC consignment a merchant won or bought would
have written `currency_wallets(user_id=0)` — foreign-keyed to `characters`, refused — and ended the
maintenance pass before its commit, then again on every tick after, since the lot stays active with
its `ends_at` in the past. The same failure the schema fixed, moved one step. `game.PayLotSellerTx`
is the one payout now: both settlement paths call it, `test_npc_consignments.py` holds that neither
carries its own copy, and `TestAMerchantWinningAnNPCLotPaysTheFinderNotUserZero` fails with the
production error when the old call is put back.

The rebuild has one trap worth knowing before writing another: `auction_bids` is `ON DELETE CASCADE`
on `auctions`, and under `foreign_keys=ON` a `DROP TABLE` performs an implicit `DELETE` that fires
that cascade — so a plain rebuild silently destroys every bid on every live lot, and
`PRAGMA defer_foreign_keys` does not prevent it (both measured). The migration parks the bids in a
table carrying no foreign key of its own and puts them back once the new parent exists.

### What a quest is allowed to ask for (`app/rules/quests.py`)

`OBJECTIVE_TYPES` is the ceiling on every quest in the game — the static ones, commissions, and
anything the Quest Forge drafts — because `quest.progress` only advances an objective whose type
matches an event somebody reported. The engine enforces nothing here: `progressQuest`
(`go_core/internal/core/contracts.go`) matches `objective.Type` against no whitelist at all, so a
vocabulary entry with no reporter behind it is not an error anywhere — it is a quest nobody can
finish, draftable in the Forge without warning.

Which is how it sat at five (`explore`, `talk`, `scene_action`, `sect_discovery`, `sect_trial`) for
several releases: there were exactly five `QUESTS.progress(...)` calls in the bot and they were
those. **Cultivation, combat, crafting, travel, the shops and the hills reported nothing**, so no
quest could ask a player to meditate, win a fight, make something, walk somewhere, buy something or
pick a herb. v1.0.0-rc.25 adds `cultivate`, `travel`, `combat_win`, `craft`, `trade` and `gather`.

Three rules for adding another. The report is written **after** the authoritative action has already
succeeded — the engine decides that something happened and the reporter only says so, never the
other way round. It is also written after the command has **answered**: `announce_quest_progress`
falls back to `interaction.response.send_message` when the interaction has not been answered yet (it
has to — a command that defers has no other way to be heard), so a reporter placed ahead of a
command's only reply spends it, and the player is told their quest advanced and never sees the craft
roll or the harvest while the engine has already granted the items. `/craft` and `/alchemy forage`
shipped that way in rc.25 and are fixed in rc.28. And a new type costs Go nothing: a vocabulary
entry, one line at the command that already does the work, and a target kind in
`validate_quest_definition` if it names something.
`tests/python/unit/test_quest_objective_reporters.py` fails if a type ever loses its reporter, if a
reporter names a type the vocabulary does not have, or if one speaks before its command answers.

`combat_win` is deliberately untargeted: an opponent may be a catalogue NPC, an event manifestation
or a beast off the hunt roster, and only the first is in `world.npcs`, so there is no roster a draft
could be validated against. The reports carry the name anyway, for the day there is one.

### People this world makes for itself (`npc_registry`, schema 49)

Three populations, and until v1.0.0-rc.27 only one of them could be spoken to. `catalog_npcs` is a
mirror of `content/world.json`, rewritten from the file at every boot. `birth_family_npcs` is a
starter household's relatives. `npc_descendants` is children born to two NPCs — and
`generated_as_npc` on it had existed since the life cycle was written, read by **nothing**, written
twice as a hardcoded `0`, because there was nowhere to promote a child *into*.

`npc_registry` is that somewhere: authored state, written at runtime, carried in backups, and never
touched by a rebuild from the content file. **It is deliberately a second table rather than an
`origin='catalogue'` row in the mirror** — a rebuild is an unconditional `DELETE` over the derived
table, and the registry is never named in that statement, so no wrong predicate can wipe the world's
own people on every boot. A name the content file already carries is never taken; the catalogue
wins, because two people answering to one name is worse than a birth refused.

`origin` is `descendant` / `birth_family` / `event` / `gm`, each with a different lifetime. It is
GM-facing and `get_registered_npc` strips it before the row can reach a narrator prompt.

- **Coming of age** (`npc_maturation.go`) — at `maturityYears` (18, the same age a played character
  starts at) a descendant gets prose from `npc_generated_traits`, a registry row, and rows in both
  simulation tables, so every batch reading `WHERE status='alive'` starts offering them. From then
  they are an ordinary NPC: courtable, sendable, eventually buried. An orphan is left for a later
  tick rather than given an invented town.
- **Relatives** — registered by `registerHouseholdRelativesTx`, called from
  `grantBirthFamilySendoffTx`, which is the one helper all three doors into a household use, ahead
  of its early returns because a household with no heirloom still has a family in it.
- **The prose is content** (`npc_generated_traits` in `world.json`), picked by `hash64` of the name
  *per field* — one index across all five pools would weld fear to personality and make the world's
  own people read as a handful of archetypes.

Three readers had drifted from the gate they sit behind, and all three are fixed here.
`DB.get_npc_definition` now resolves catalogue → registry → running event's cast.
`narrator.py` was a bare `self.world.npcs[npc_name]` plus six bare field subscripts while the gate
upstream already fell back to the event cast, so a militia captain passed the gate and `KeyError`'d
— `/talk`'s `except Exception` turned that into *"the narrator service failed to answer."* It takes
a duck-typed `npc_resolver` now, injected because `test_app_layout.py` puts `ai` below `database`,
exactly as `NarratorContextBuilder` already did. And `/sense` refused with *"Unknown NPC."* anybody
outside the content file while its own picker offered them.

`current_npc_location` answers the registry when there is no simulation row. That matters because
`None` means "nothing knows where they are", which every caller reads as *do not filter by
location* — so without it somebody else's uncle would be talkable from across the world.

### The content file as tables (`content_*`, `internal/contentsync`, schema 51)

Nine derived tables — `content_npcs`, `content_locations`, `content_items`, `content_recipes`,
`content_sects`, `content_shops`, `content_merchants`, `content_manuals`, `content_techniques` —
mirror `content/world.json` with real, indexed columns. **The engine alone writes them**, from the
file itself, hash-gated (`world_state['content_version']`), in one transaction, with deletes: the
Python-written `catalog_*` blobs only ever upserted, so a renamed NPC lived in `catalog_npcs`
forever.

**Every row is the entry's raw bytes plus a projection.** `data_json` is the exact JSON of that entry
as it sits in the file, and the typed columns beside it are read off it by `contentsync.Sections` —
`Text`, `Integer`, or a presence `Flag` for the fields whose value is a structure (`circuit`,
`hidden_master`). An absent key is `NULL`, never `''`. This is deliberately not the struct-widening
the plan first called for, which it named "silent when wrong": a field missed in a Go struct is an
empty column and nothing errors. Keeping the file's own bytes makes the blob complete by
construction, and `TestProjectionMatchesTheRawEntries` holds every projected column against the real
2.5 MB — the count of non-NULL cells must equal the count of entries carrying the key.

**Three doors, one apply, and the order is the point.** The tables are filled by the engine but
created by Python's migration, which in the compose stack runs *after* the engine is healthy — so
the engine's guarded apply at `server.New` finds no tables on a first boot and does nothing. db-init
(`app.database.bootstrap`) calls `POST /v1/content/sync` the moment `init()` has run, the bot calls it
again at `CATALOG_READY` before it counts, and the GM's `admin.content.reload` runs the same apply
on demand. Together those guarantee the tables are full before any reader in every boot order;
`test_content_tables.py` asserts the ordering rather than hoping.

**One table, one path (schema 52, v1.0.0-rc.40).** Schema 51 shipped a mode switch —
`content_table_for(catalog_table, engine_backed)` — because pytest has no engine to fill `content_*`,
so a local read stayed on the `catalog_*` blob it had always used. That was a deliberate one-release
loan, and migration 52 calls it in: the five mirrors are dropped, the switch is deleted, and
`_catalog_get`, `search_catalog`, `catalog_counts` and the dashboard's two catalogue reads name their
`content_*` table outright. Go's own rules keep reading the memoised in-memory catalogue — a table of
what the engine already holds parsed would be a slower copy, not a source.

**The no-engine path is a fixture, not a second source.** The obvious way to keep pytest working
would have been to let Python write `content_*` when no engine is attached — and that is exactly the
rule those tables exist to enforce, so it is refused. The tables are *created* by Python's migration
and *filled* only by the engine; a test that needs catalogue rows calls
`tests/support.seed_content_tables`, which writes the three columns every reader touches (`name`,
`data_json`, `updated_at`) and leaves the typed projection NULL, where the DDL already expects it.
The projection has one definition, in Go, and `TestProjectionMatchesTheRawEntries` still owns it.
`test_content_tables.py` holds the rest: no file under `app/` or `scripts/` may name a retired mirror
outside the migration list, and each of the five has a `DROPPED_TABLES` entry so the migration drill
proves the drop rather than shrugging at it.

**What was left of the writer.** `sync_world_catalog` was ~1,800 catalogue upserts plus a territory
node per location plus the baseline era. The upserts are gone with their tables, and the rest is
`seed_world_territories` — the part that was never a mirror. The name matters: a method called
`sync_world_catalog` that syncs no catalogue is the same class of lie as the GM maintenance action
below, which used to report a resync it had not done.

**The GM sync tells the truth now.** `/admin server maintenance → Sync world catalog` used to write
`WORLD.data` — this process's copy, parsed at import — and report that it had resynced from
`world.json`, which it had not. It re-reads the file on both sides: the engine applies into
`content_*` with an audit row in the same commit — since rc.40 that is the whole catalogue — and
Python's half reseeds the territory map from a fresh parse (the running `WORLD` is left alone — a hot
swap of a dict 347 call sites read is not a maintenance action). The message reports the content
hash, whether anything changed, and that this bot's in-process presentation applies the edit at its
next restart. `worlddata.Load` is memoised on the file's stat,
so the Go rules had already picked the edit up on their own.

### The readiness probe (`OPERATIONAL_REQUIRED_TABLES`)

`operational_health` exists to tell a healthy versioned database from the empty file SQLite will
create if the real one is removed or replaced while the bot is running. It does that by checking
that a set of tables is present — and since v1.0.0-rc.28 that set is **exact**: every table a fresh
bootstrap makes, all 169 of them.

It used to be a sample of twenty-seven written for v0.20.7 and never revisited. By schema 49 it
still named two catalogue mirrors nothing reads for their content and omitted
`npc_civilization_state`, `inventory`, `character_quests`, `battles` and everything added in
twenty-nine releases. **A sample cannot be kept honest, because nothing says which tables belong in
it.** An exact set can: `test_startup_health` holds it against a real bootstrap rather than against
another list, so adding a table without listing it fails there. That test is the whole mechanism —
the literal is only reviewable because the test makes it true.

FTS5 virtual tables and their shadow tables are deliberately excluded: they are made by
`CREATE VIRTUAL TABLE` and rebuilt from their base tables, so their absence is a different fault.

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

### World events and their sites

A world event is a row in `world_events` (category, severity, location, expiry) plus a **site**:
the concrete, finite things inside it, in `world_event_nodes` (schema 42). Before the site existed an
event was an empty room - the action menu rolled 2d10 and moved four integers, the only reward in a
whole scene was one first-participation claim, "Gather Resources" granted no item, and the Battle
button fought an anonymous "hostile manifestation".

Nodes come in five kinds - `beast`, `herb`, `ore`, `relic`, `task` - and each carries a `total` and a
`remaining` that depletes as players work it, so a scene can be cleared out and a late arrival can
see that it was. The roster is content, not code: `event_sites` in `content/world.json` holds one
template per event category (plus a `default` for categories nobody wrote), each node's count a
`[min,max]` pair scaled by event severity. Material rewards are written `@herb`/`@ore`/`@core` and
resolved against the world tier the event landed in, so one template stays correct from the Mortal
World to the Celestial.

`forage_materials` (v1.0.0-rc.21) is the sibling roster, and the reason the two are separate is that
these are tier-flat: `talisman_paper`, `spirit_ink` and `array_disk_blank` serve a Mortal scribe and
a Celestial one alike, so they carry a find chance and a `min_resources` floor rather than a per-world
material. `forageResolveAction` rolls them beside the tiered herb. Before it existed, shops were their
only source, so Alchemy and Forging could be gathered into and Inscription and Formation could only
be bought into - `EveryCraftCanBeGatheredIntoTests` is what holds that shut.

Go owns all of it. `SpawnWorldEventNodes` is called from every world-event spawn path - the
player-triggered exploration event and the native autonomous simulation batch - so no event can reach
a player empty; it is idempotent per event key. `world_event.engage` resolves one attempt against one
node (attribute check vs the node's TN, and on success a guarded `remaining>0` decrement plus the real
item, cultivation and spirit stones), and an event battle names a real beast from the roster, with the
node key riding the combat `source` as `event:<key>|node:<node>` so the kill depletes it. Python only
reads the site (`DB.list_world_event_nodes`, `DB.world_event_site_progress`) and draws it.

An event also brings a **cast** (`world_event_npcs`, schema 43) - the militia captain to report to,
the visiting elder to impress, the auctioneer whose floor it is - written per category beside the
nodes and named at spawn from a shared pool, walked forward until the name is free so two live
events never field the same officer. They are deliberately *not* added to the permanent NPC
catalogue: an eight-hour captain must not be aged, married and buried by `npc_life`. Instead
`DB.get_npc_definition` falls back to the cast of a *running* event, which is all `/talk` needs, and
`NarratorContext._public_npc` does the same so a cast member reaches the narrator with their role,
manner and stated want rather than as an anonymous local cultivator. Because both lookups filter on
the event still being active, the rows need no cleanup - they simply stop answering when it closes.

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
retires the ones that answer `401`/`403`/`404`. A `400` is not a verdict but a family of causes, so
`_classify_bad_request` isolates one variable per confirmation: first an ordinary token budget with
`REASONING_OFF` still attached (success means the 400 was the one-token probe hitting a provider
minimum), then the same call with the `reasoning` object removed (success means the parameter was
the cause, and only that retires). A 400 that survives both is not parameter-caused and is recorded,
not acted on; the verdict is stored per route as `probe_400_class` for the panel. `429`s and
timeouts never retire anything; that is what the per-route cooldown is for. The audit spends the shared budget it uses, stands down below half the daily
allowance, and retires nothing when *every* route fails at once (a local fault, not an empty
catalogue). The AI Studio route is never retired whatever it answers — it is the operator's own key
on its own quota, outside the shared budget. It proves reachability only — a scratchpadding model passes it, so
`_validate_generated_text` remains the sole judge of whether a reply is usable prose.

Since v0.31.0 a live call is made for three reasons only: an NPC answering a player (`dialogue`),
an epic beat (`epic`), or an explicit ask (`narrate_it` - the typed-play picker's *Narrate it*, the
button under an exploration or hunt result, an @mention, or the GM's `ai_routine_narration`
automation flag). `narrate_exploration` and `narrate_hunt_result` are procedural by default and
take `upgrade=True` for the explicit path; every `_generate` call names its purpose and the router
counts purposes for the AI Routing page. The procedural floor is content: `narration_pool` in
`content/world.json` (eleven scene kinds by four world tiers - seven scenes and the four road-site
explorations since v1.0.0-rc.2), chosen deterministically by
`app/rules/narration_pool.py`. One per-player bucket (`TYPED_PLAY_BURST` / `TYPED_PLAY_PER_MINUTE`)
meters every door - typed lines, shorthand commands (`x explore`, v1.0.0, the one door heard in
every channel of the guild), `serialized_user_action` (slash and hub), Narrate-it - and reports
per door. `tests/python/contracts/test_narrator_budget.py` holds all of it.

### Narration routes in the dashboard

The GM dashboard's **Narration Routes** panel also carries the ten-dollar switch (v0.31.0):
OpenRouter's free allowance is 50 requests a day under ten dollars of credit and 1000 above, so
`credits_topped_up` is stored beside the slots by the same engine write and applied through
`set_slots`; `OPENROUTER_CREDITS_TOPPED_UP` is the `.env` baseline. The panel picks the five chain slots
(`routine_model`, `routine_fallback_model`, `epic_model`, `epic_fallback_model`,
`dynamic_free_model`) from OpenRouter's live free catalogue rather than from a list kept in this
repo — a list kept here is how `z-ai/glm-5.2:free` and `minimax/minimax-m3:free` both shipped as
defaults that no longer existed. The AI Studio lead is not settable: it exists only when the
operator has put their own Google key in the environment.

Three processes, and the order is the point. The browser posts to the dashboard; the dashboard
writes through the engine (`admin.narration.set_chain` → `world_state['narration_chain']` +
`admin_audit_log`, no schema change — it follows `admin.automation.set`); then it pokes the bot
over the existing `/control/discord` channel (`narration.apply`) to re-read and apply it live.
The engine write is what makes a choice durable and audited, so it happens first and independently
— an unreachable bot means "stored, applies at next restart", not a failure. The bot also applies
the stored chain at startup, so `.env` is the baseline rather than the last word.

`AITaskRouter` keeps the slots as slots (not only as assembled chains) so `set_slots()` can rebuild
in place; `OPENROUTER_REQUIRE_FREE` still applies, every slot is validated before any is assigned,
and a newly chosen route has its probe verdict cleared so it does not inherit the previous
occupant's retirement.

### Dashboard (`app/dashboard`, `dashboard/`)

Authenticated GM control plane; production reads go through Go-owned query sessions (dashboard never
opens SQLite directly), and every state-changing GM action is written to `admin_audit_log`.
`/api/capabilities` is the frontend/backend coverage contract checked by
`scripts/check_dashboard_implementation.py` and CI. One view is not backed by SQLite: `ai_routing`
reads the narration router's in-process chains, counters and audit verdicts through the bot control
plane, and is read-only (no `admin_audit_log` row, and it sits under Systems, not Admin).

**The Player Editor (v1.0.0-rc.37)** is where one character's levers live. The Admin Console had
sixteen cards that each began with a Player select and knew nothing about the character chosen -
a GM setting a realm typed 0/1 over whatever was there, and the bloodline card wanted an id the
GM had to look up on another page. `player_editor` picks a player once (the picker sits in the
page header, and the drawer on Player Activity opens it), reads `/api/player`, and draws every
`player.*` action the controller maps, pre-filled from the row that action writes: `player_detail`
returns the wallets, root, bloodlines, physique, tribulation gates, perfection rows, beasts,
equipment, abode and its guests, pill toxicity and fate beside the sheet, so the ids a lever needs
(`bloodline_id`, `beast_id`, `equipment_id`, `guest_user_id`) are picked, not typed. The console
keeps what acts on the world or the server. Nothing about the write path changed: the same
`/api/admin/action`, the same `ACTION_MAP`, the same audit row - the editor decides nothing, it only
fills the form. `test_the_player_editor_owns_every_per_player_lever` holds that every mapped
`player.*` action is driven from the editor and none from the console, and that a snowflake is
never put through `Number()` on the way (`EDIT_UID` is the string the server returned).

The Discord `/admin` panel has the same shape since rc.37: rc.13 had split `/admin player` into
Players, Grants and Moderation so no page needed a Next button, and they are one **Player Edit**
head again by request, the one page `test_hub_pages.py` allows past the eight-row layout (the
panel pages it with "More actions"; `LONG_PAGES` names it and nothing else). It also gained
`/admin player setrealm`, the only realm lever on the Discord side, with the same optional body
pair; the engine writes its audit row, so the handler logs nothing of its own.

The Admin Console's NPC card (rc.38) carries **Lose** and **Bring back** beside Relocate:
`admin.npc.set_missing`, the disappearance a GM can stage, audited and undoable like relocate. See
"What only the world makes" above for why it writes the tick's own row.


## What each release found

Each section below is one release's finding, in the order they were found. They are kept because the
recurring fault in this codebase was never a broken mechanic - it was a finished mechanic with one
wire missing, and the only reliable defence has been writing down what the last one looked like.
Read them as the reasons behind the rules above, not as a changelog: `VERSIONS.md` is the changelog.

### The NPC life cycle (v1.0.0-rc.24)

`npc_romance.go` replaced a pairing that walked one globally sorted list of singles two at a time and
kept a pair only if both landed on the same `current_location`. Against the shipped catalogue that
is fifteen usable pairs out of two hundred and forty, at six percent — four weddings a month in a
world of 574 people, and so almost no couples for `npcChildbirth` to work with. The cause is
geography: 477 places hold those people and **392 are the only person standing where they stand**.
Courting now reaches one step, through `game.WhereAnNPCCanWalk` (district↔city), which drops that
392 to 56. A courtship gains affinity while the pair stay in reach and cools when the roads separate
them; realm and age must agree *proportionally* (sixty years is a lifetime to a mortal and nothing to
a Nascent Soul elder); kin and standing grudges are excluded. **There is deliberately no gender
rule — not one of the 574 catalogue NPCs carries a gender field, so a rule would be inventing
content rather than reading it.** Two sects on speaking terms also marry their weightiest unattached
members to each other, which is the first thing that has ever *made* a `marriage_pact` rather than
describing one at bootstrap.

`bootstrap_households.go` gives a new world a past: ~88 households, ~176 married, ~104 children and
~29 people near the end of the span their realm allows, all keyed off `hash64` so the same content
makes the same world twice.

`ReleaseNPCBondsTx` (in `game`, so all three death paths can reach it) widows the survivor. Nothing
had ever set `relationship_status` back from `'married'`, so a widow stayed married to a corpse,
could never be courted again, and went on bearing his children.

### What NPCs do on their own (`npc_deeds.go`, v1.0.0-rc.24)

A step of the `npc_life` batch, after the feuds: a criminal trade (or anyone ambitious enough and
poor enough) robs, beats or smuggles; a hunting trade goes out after a beast from the same roster
`/hunt` uses (`game.RollHuntQuarry`). Everything writes a column that already existed — wealth and
`activity` on `npc_civilization_state`, health and injury on `npc_life_state`, grudges in
`npc_social_relations`, contraband in `black_market_stock`, lots in `auctions`, the record in
`world_history_events`.

**NPCs never get a `crime_records` row.** That table is FK'd to `characters` and is the player's:
an NPC row there would mean a bounty nobody can collect and a capture nothing can perform. The
visibility ladder above carries NPC crime instead — a crime with a witness is `public` and leaves a
named grudge that `npcFeuds` later settles; one without is `hidden`, so it never reaches narrator
RAG and the world really does not know who did it. A killing is always `public` (a body is found);
the summary is what says whether the culprit is named. Making NPC crime prosecutable would be a
schema change and is a separate decision — do not add it casually.

### The path a new cultivator is put on (`beginner_path`, v1.0.0-rc.26)

`first_steps` — "First Steps Beneath Heaven" — has been in `app/rules/quests.py` since before the
Forge, with exactly the right three objectives, and **no player has ever held it**. It is seeded
into `quest_definitions` on every boot and it is listed in `/quests`; what never existed is a path
that hands it to anybody. The only two statements in the engine that write a `character_quests` row
are both in `commission_actions.go` and both want a `giver_npc`, which the static quests
deliberately do not have. A new player is not short of a quest — they are short of being *given*
one, and `/quests` is one of sixteen equally-weighted hubs with nothing saying it is theirs.

`content/world.json` → `beginner_path` is an ordered list of stages; `grantBeginnerPathTx` hands the
first over in the transaction that makes the character, beside the birth-family send-off, and each
stage hands over the next as it completes. **There is no second quest mechanism**: a stage is an
ordinary `quest_definitions` row seeded the way the authored commission pool is, and once handed
over it is pinned, progressed, completed and paid by the code every other quest uses.

Four rules hold it:

- **The first stage must be completable indoors.** A character is created at `birth_family:<id>`, a
  private residence, and `explorationExploreAction`, `explorationTravelAction` and
  `explorationHuntAction` all refuse there. `cultivationTrain` and `scene.action` do not, which is
  why stage one is those two. `test_beginner_path.py` reads those refusals out of the Go source
  rather than trusting a list, so a new gate in the engine fails the test rather than the player.
- **A missing definition costs the quest, never the character.** `grantOrdinaryQuestTx` treats an
  absent definition, an absent table and an already-held quest as three kinds of "no", not errors.
- **A commission is never handed over.** A `giver_npc` means the one-at-a-time slot and a deadline,
  offered in person; the grant refuses one whatever a chain says.
- **The chain is read off `quest_definitions.seed_json`, not off the content file**, so a GM who
  re-points it in the dashboard workbench is obeyed and the shipped file is only the starting shape.

"Fires once" is the `(user_id, quest_key)` primary key — the row is the memory, which is what makes
the grant safe to call from creation, a dao-family rebirth and a samsara return alike.

### What it costs to draw a scene (v1.0.0-rc.28)

Three things on the content path cost far more than they look, and all three are on the hot path.

**"Who is standing here" was 574 engine round trips.** Every surface that draws it — `/action`'s
target picker, `/scene status`, `/world`, `/world → City → Look` — walked the whole NPC catalogue
calling `npc.status` per name, inside an `await`, so serially. `npcs_present(location, period)` in
`app/bot/locations.py` is the one resolver now.

**What bounds it is asking content first, and that is easy to get wrong in a way no source check can
see.** One `npc.at_location` query gets everybody the engine has standing here; the obvious next
step — fall through to `current_npc_location` for everybody the query did not return — is the old
cost with a new shape, because the people it did not return are the five hundred and fifty-eight who
are demonstrably somewhere else. So the in-process catalogue rules them out before anybody is
resolved: only an NPC content places here this period can still be in doubt, and only those cost a
round trip. Measured against the shipped catalogue, one open is `npc.at_location` ×1 and
`npc.status` ×1. Content can be wrong in exactly one direction — it does not know about autonomous
travel — and that direction is covered by the first query, because somebody the simulation walked
here has a row saying so.

It keeps the same order of precedence `current_npc_location` does, and it has to: circuit first, then
the simulation, then the schedule. A picker that offers somebody `/talk` then refuses them is worse
than either being wrong alone — and the schedule is the half the engine cannot know, because it is
content: a row at its NPC's *home* is a routine, not a whereabouts, so the fourteen catalogue NPCs
whose day takes them out of their home town were offered in the wrong room at twenty (place, period)
pairs until the override was applied here too. A missing person is exempt: they keep no routine
(schema 47) and their row is the whole truth. `npc.at_location` is deliberately narrow —
`npc.status` carries relationships, disciple bonds and the life row, and loading all of that for
everybody in a city to decide whether to list them is what made the old shape slow twice over.

`test_who_is_here.py` measures both of those with a counting fake rather than reading the source,
because the source reads correctly in both the fast and the slow version — the round trips are one
level down, inside `current_npc_location`.

**`worlddata.Load` re-parsed 2.5 MB per action.** Fifteen of its seventeen call sites are in
`authoritative.go`, inside the request path. It is memoised on `(path, mtime, size)` — not on the
path alone, so an operator editing content on a live NAS still does not need a restart. Size is in
the key beside the timestamp because some filesystems keep mtime at one-second resolution, and two
edits inside the same second that change the length would otherwise serve the older parse.

**Boot spent ~2,000 HTTP round trips rewriting unchanged content.** On the Go-backed path each
`db.execute` is one POST, and `sync_world_catalog` made about 1,800 catalogue upserts plus one
territory node per location, one at a time. They go in a single `/v1/db/batch` request now — an
endpoint that had existed on the transport since the Go engine landed with nothing on this path
using it. The statements stay inside `sync_world_catalog` rather than in a helper because
`test_authority_boundary` reads the write allowlists off the method that contains the SQL, and
moving them out would mean widening an authority gate for a refactor that changes no authority.

### What the household teaches, and how well (Tradition + Tutoring, v1.0.0-rc.31)

Every one of the thirteen birth families names a trade in its send-off (`birth_family_sendoff` in
`world.json` — Forging ×5, Inscription ×3, Formation ×3, Alchemy ×2), and `teachHouseholdMethodsTx`
has handed every child the entry methods of that trade since rc.20. What varied was nothing: the
only bonus in the game was +2 on Alchemy rolls keyed on the archetype string `"alchemy_family"` — so
`body_tempering_family`, which teaches Alchemy, got nothing, and the five Forging, three Inscription
and three Formation houses got nothing for the trade they teach. Two things vary now, and both read
data the household already carries (`household_tutoring.go`).

**Tradition is flat and keyed on the trade.** `householdTradeBonusTx` gives +2 on a roll in the trade
the send-off names, whichever family, whichever trade; forage is Alchemy's gathering half, so it is
the two Alchemy houses' bonus and nobody else's. The Python `family_profession_bonus` is the same rule
for display — it takes the roster, not an archetype string — and the engine's result carries
`family_trade` so the craft and forage labels name the trade instead of assuming Alchemy.

**Tutoring is what the household could afford.** `tutorHouseholdTradeTx` writes a
`profession_progress` row for the trade at the send-off, banded on `birth_families.wealth` at that
moment: below 40 (fallen martial clan 26, tomb-watch 31, body-tempering 36) shows you the basics —
level 0, 0 XP; 40–59 (the eight middling houses) a journeyman in the family, 30 XP, halfway to
Apprentice; 60–79 (alchemy family 61) a hired tutor, 55 XP; 80 and above (noble martial clan 82) a
master retained — you leave an **Apprentice**. Wealth rather than tier because tier cannot tell the
fallen clan (26) from the martial household (42) — both tier 2 — and the fallen clan should teach
worse. Capped at level 1: a head start, not mastery.

**The row is the memory.** The grant checks for an existing `profession_progress` row and writes only
when there is none. The door that needs that guard is `family.support`'s backfill
(`family_dao_actions.go`): a character created before rc.15 who has been forging for months comes home,
asks, and keeps every point. **There is no dao-family rebirth** — `rebirth_mode` is only ever
`'samsara'` — and samsara does not reach the guard at all: `reincarnateAction` deletes
`profession_progress` and `character_recipes` before it calls the send-off, so a new life is tutored
fresh by its new household. (rc.31's version of this paragraph said both rebirths "keep every point";
that was wrong on both counts.) The grant runs ahead of the heirloom guard for the same reason the
schooling does: a household with no heirloom still teaches. The send-off result carries a `tutoring`
block and `family_tutoring_line` says who taught you; nothing is decided in presentation.

### What the hands remember (the craft echo, v1.0.0-rc.32)

Everything samsara carries is an *echo* — realm resets to 0 but `law_echo/25` rides comprehension
rolls (`soulLawBonus`), talent and insight carry as percentages, and `memory_seed` is a ceiling that
`awakened_memory` climbs toward through breakthroughs and law insight. Crafting was the one thing a
soul had done that it could not remember. `craft_echo.go` is two halves. `pastLifeProfessionsTx` reads
the dying life's trades (every `profession_progress` row past level 0) into the past-life entry in
`soul_legacy.past_lives_json`, **before** the wipe at `lifecycle_actions.go` runs — the record is
written first, so the rows are still there, and no schema changed. `craftEchoTx` reads them back on
every craft and forage roll: the best level that profession reached in any recorded life (the most
recent life wins a tie and is the one named), scaled by `awakened_memory/memory_seed` and capped at
`craftEchoCap` (+3). A fresh rebirth remembers nothing — `awakened_memory` is reset to 0 — and as
memory wakes, the hands remember. It rides `contextBonus` beside the household tradition, so a reborn
smith born into a Forging house is deliberately the best smith in town. The result carries
`craft_echo`, `craft_echo_life` and `craft_echo_level`; `/soul` names the trades a visible life
carried; nothing is decided in presentation. `craft_echo_test.go` holds the cap, the gate and
most-recent-wins; `test_craft_echo.py` holds that the record is written before the wipe.

### A house worth coming back to (v1.0.0-rc.32)

Until now the birth household gave a child an heirloom, a trade and a +2 on the way out of the door,
and after that one handout of stones every three in-world months — which **could be asked for from
anywhere in the world**. `/family enter` was a free teleport. `treasury_balance` was written once at
bootstrap and read by nothing. The household was ordinary ground (`placeCultivationMultiplier` →
1.0) and refused seclusion. The family simulation ran only when somebody looked at it and never
involved the player. Nothing in the game ever said "go home". `household_return.go` is the reasons to,
and none of them needed schema:

- **Presence.** `family.support`, `family.contribute`, `family.tutor` and `family.errand` refuse
  unless the character stands in `birth_family:<id>` (`requireAtHomeTx`), and the door itself opens
  only from the family's own town: `familyHouseholdEnterAction` refuses from anywhere else with
  "travel there first". The panel hides each door where it would refuse (`register_hidden_actions`
  in `hubs.py`, `_household_hidden_actions` in `surface.py`): the hub asks one async provider for
  the paths to leave off whenever it refreshes its status, and a failed lookup hides nothing. The
  same door does the rest of the game's late doors (`PROGRESSION_GATES`, `_progression_hidden_actions`):
  a law before the realm that can hold one, tribulations off a world-crossing gate, Perfection off
  stage 9, a sect's rooms to somebody in no sect, a home's keys with no home, an inner world with none,
  a beast's training with no beast, a house's seats with no house, the Samsara legacy in a first life.
  Only what the engine would refuse outright is hidden - never a status read or the door into the
  system, because a road nobody can see is a road nobody learns exists - and a hidden door is not
  silent: every provider answers `path -> reason`, and the page prints each as a locked line
  ("🔒 Comprehend — a Law needs Foundation Establishment; you stand at Qi Condensation"). `test_hidden_actions.py`
  holds every hidden name to a row on the playtest checklist, so a renamed command cannot leave a
  stale hide behind.
- **The hearth.** `birthFamilyCultivationMultiplier` is `1.04 + 0.02 × tier`, held under the
  shrine's 1.15 — a good place to sit, never the best — read by `placeCultivationMultiplier` and by
  `seclusionEnvironmentGo` (the household is a seclusion site now) from one helper so the two cannot
  drift.
- **The purse.** `family.contribute {amount}` spends low spirit stones into `treasury_balance`, raises
  `wealth` a fifth as fast and `influence` a twenty-fifth (both capped at 100), writes a line into
  `history_json`, and raises the player's standing with the house — `faction_reputation` under the key
  `family:<id>`, a table that imposes no vocabulary. Support pays a bounded standing term
  (`standing/10`, at most 10 stones).
- **Taught again.** `family.tutor` re-reads `householdTutoring(current wealth)` and raises the trade's
  row to the band — level up to 1, XP up to the band's — and never lowers either; a house that can
  teach nothing new says so and names the wealth at which it could. This is what makes the purse
  worth filling for a crafter.
- **Needed.** `return_home` is an objective type, reported by `/family enter` and by the talisman
  after the reply. `household_errands` in `world.json` is a pool per trade (three each), ordinary
  giver-less quests whose last objective is always the door; `family.errand` hands the next unheld one
  over through `grantOrdinaryQuestTx`, one at a time. **Only a key with the `errand_` prefix may pay
  `household_standing`** — the engine ignores it on any other key and the validator refuses it on any
  other draft — so the Forge cannot inflate a house's opinion of a player. A finished errand goes into
  the chronicle. The beginner path gained a fourth stage, `beginner_home`, so the first hour ends
  where it began.
- **The round trip.** Two talismans, Apprentice Inscription methods whose slips the talisman hall sells
  (not entry methods: the grandfathering migration deliberately sweeps in nothing authored after it). The
  **Hearth-Return Talisman** (`use.homeward`, one folded into every send-off) carries you home from
  anywhere and marks where it found you on the scene's metadata; the **Waymark Talisman**
  (`use.waymark`) is read inside the household and takes you back to that mark. Walking in from the
  town leaves no mark, so a waymark after walking in is refused unspent, and leaving on foot is always
  the street. Both are refused before they are consumed — mid-battle, in seclusion, or with no mark.

Errands are quests, not commissions, on purpose: a commission's `giver_npc` is content fixed at
authoring time while relatives are generated names, and `grantOrdinaryQuestTx` refuses a giver by
design. Two things are deferred, deliberately: relatives never age or die (that needs a simulation
pass over `birth_family_npcs`, which no batch reads today), and the family simulation still writes
only `history_json` rather than `world_history_events`, because starter households are shared and
the visibility of a shared family's news is a decision, not a default.

### The Discord half of the playtest (`scripts/playtest_discord.py`, v1.0.0-rc.33)

`scripts/playtest_engine.py` drives the roadmap's loops through the engine's HTTP API; everything a
player actually touches - slash commands, the hub panels, their pickers and modals, private threads,
typed lines - was a hand-ticked checklist that nothing ran. `playtest_discord.py` boots `app.bot`
unmodified inside **SimCord** (`simcord==2.0.1`, MIT, only dependency `discord.py>=2.7.1`): an
in-memory Discord that runs discord.py's real machinery, replacing exactly two seams - `bot.http` is a
fake REST client over an in-memory model, and gateway events are fed straight into discord.py's own
parsers, so `setup_hook` runs, `tree.sync(guild=GUILD)` registers into the fake, `on_ready` fires
through normal dispatch, and a test actor fires `/family`, presses a panel's buttons, chooses from its
selects, submits its modals and types `$ I explore`, then reads what came back. **Every loop goes
through those surfaces**, never through a handler or a `DB` method: the point is the wiring the
engine playtest cannot see. Nothing asserts on dice.

Three rules hold it. **The environment is set before the bot is imported**: `app/bot/runtime.py`
builds `SETTINGS`, `ENGINE` and `DB` at import and `bot.py` makes the singleton, so `_configure` puts
the scratch engine, a numeric `GUILD_ID` (which `env.create_guild(id=…)` must repeat - `GUILD` is
`discord.Object(id=SETTINGS.guild_id)`), a non-default `HEALTH_PORT`, `NARRATOR_PROVIDER=procedural`
and the workers' off switches into `os.environ` first; `test_playtest_gate` holds that no module-level
`app` import exists. **SimCord is a dev dependency only** (`requirements-dev.txt`, beside pytest and
ruff): nothing under `app/` imports it, so `requirements.lock` and the Dockerfile's `--require-hashes`
install never carry it, and the gate test holds all three. **It is a script, not CI**: the bot cannot
boot without the Go engine and the CI `python` job has none, so like the engine half it is run
before a release (`python scripts/playtest_discord.py --launch` builds and starts one).

Two settings the harness raises are findings in their own right: typed play in a private thread
listens only with `AUTO_NARRATE=true` and the message-content intent, and the per-player action meter
(`TYPED_PLAY_BURST`/`TYPED_PLAY_PER_MINUTE`, about six a minute) refuses anything that presses sixty
buttons in one - right for a person, and the harness says so. And its first green run found a bug no
source read had: a modal opened from a panel (Contribute, a GM's category name) submits with the
panel as its message, but is acknowledged with a "thinking" placeholder, and `_show_result_in_panel`
edited the *original response* - so the placeholder became a second panel and the real one kept
buttons `rebuild()` had already orphaned, dead until reopened. A modal now takes the direct
`panel.edit` and the placeholder is deleted, the way a picker's step message always was;
`test_gui_ii.py` holds it with a `modal_submit` source.

### The last lesson (`household_lesson.go`, v1.0.0-rc.34)

The beginner path walked a new cultivator out of the household, through the town and the road, and
home again — and then stopped. Nobody in the house had ever spoken to them as a teacher: the send-off
hands over an heirloom, one trade's entry methods and a tutoring band, and the head of the family
(`birth_families.head_title` and `head_name`) was a line in `/family → View` and nothing else. And a
fresh cultivator could craft only in the household's own trade, because `craft.resolve` refuses any
method they do not know and the other three trades were bought into from slips.

`family.lesson` is the head of the house speaking to their child, at home, once per life:
`/family → Hearth → Lesson`, the fifth and last stage of the path (`beginner_lesson`, reached through
`beginner_home`'s `follow_on`). **The conversation is the action.** Every line the head speaks is
content (`birth_family_lesson` in `world.json`, one entry per archetype: `lesson`, `test`, `pass`,
`fail`, `story`, plus the house's `manual` and `keepsake`); Python prints and nothing is decided in
presentation. The head is deliberately not a talkable NPC: `combatTargetsGo` already stands the head in
the home city as a `family_head` target and `combat_aftermath.go` retires a killed one to "Vacant
Ancestral Seat", and a registry row would have to follow both.

- **The test** is one demonstration check on the attribute the family's trade lives on (Forging →
  body, Inscription → will, Formation → spirit, Alchemy → insight; `householdLessonAttribute`), through
  `canonicalAttribute` + `rollCheck` like a Scene Action: modifier = attribute + the trade's
  `profession_progress.level` + `min(2, standing/10)`, against TN 10 (`householdLessonTN`), which a
  fresh character clears about three times in four. **A failure costs one world day and nothing
  else** (`householdLessonRetryGameMinutes` = 1440, the sect trial's own wait): the wait is read off
  the attempt, the cooldown card lists it as `family_lesson_retry`, and `_explain_engine_error` says
  it in hours.
- **Passing qualifies the cultivator at level 0 in all four trades**: a `profession_progress` row in
  each where there was none (never lowered — the tutoring rule) and every trade's entry methods
  (`teachTradeMethodsTx`, the one helper the send-off now shares, source `family_lesson`), so `/craft`
  works in any trade. Then **the technique of the house**: the family's manual (realm 0, never
  Demonic — `manualForbidden` would cost a child karma on first study, and the engine refuses such
  content outright; `test_household_lesson.py` holds it) goes into the inventory and its first-study
  row is written, so its mastery-0 technique is usable at once. Then **the story and a keepsake**: the
  house's own history into `history_json`, a per-trade keepsake item (`market_excluded`), and +5
  standing.
- **The record is the event log, per life.** `family.lesson` rows in `event_log` carry the attempt,
  its game minute and `soul_legacy.incarnation_count` (1 in a first life); a pass is refused again only
  in the life that earned it, so samsara — which wipes the trades and the methods — lets a new life
  take the lesson again without deleting any log. No schema.
- **Grandfathering happens at the door.** A boot migration cannot hand a *quest* over — the stage is
  seeded by the bot after the engine starts, and `grantOrdinaryQuestTx` treats a missing definition as
  "no" — so `catchUpBeginnerPathTx` runs when the lesson is passed: any stage whose predecessor is
  completed and which was never given is handed over in the same transaction, and the reporter
  completes and pays it. Somebody who finished "The Road Home" before rc.34 gets "The Last Lesson"
  the moment they ask for it.

`family_lesson` is an objective type reported only on a pass, after the reply.
`household_lesson_test.go` lends the dice (`gamerng.UseRoller`) and holds every rule above; the two
playtest harnesses assert only what is certain either way — the check is printed, and the second ask is
refused, as "already taught" after a pass or as the wait after a fail.

### The playtest touches everything (v1.0.0-rc.35)

Two harnesses run before a release, and until now nothing said what they had to drive. The engine
half drove 51 of the 193 allowlisted operations and the Discord half pressed 9 of 245 leaves; the
rest were proven by Go unit tests for their rules and by nothing for their wiring, and a new
operation or leaf was uncovered until somebody noticed. `tests/python/contracts/test_playtest_coverage.py`
is the gate that makes "everything" a fact rather than a claim. It enumerates the surface **from the
code** - the two allowlist maps in `authoritative.go` plus the dispatch switch in `actions.go` (246
operations), and `hubs.REGISTERED_HUBS` walked through `_leaf_actions` (298 leaves, admin included) -
and holds each harness to it with one explicit deferred set per harness, `DEFERRED_OPERATIONS` and
`DEFERRED_LEAVES`, read off the scripts by AST so neither harness is imported. Three rules:

- **Driven means called.** The engine set is the first string argument of every `act`, `gm`, `query`
  or `audited` call in `playtest_engine.py` - not a substring scan, so an operation named in a comment,
  a step title or an `expect_error` is not driven. A deferral that is also driven, or that names an
  operation the engine no longer has, fails the gate: the set can only shrink honestly.
- **The Discord sweep is generic, so a new leaf is covered the day it is registered.** Section 8 of
  `playtest_discord.py` walks the live definitions, opens each hub once per page, pages with the
  panel's own "More actions" until the offset wraps (the visible row limit is recomputed on every
  rebuild and drops while a result is shown), presses every leaf and answers each input step the way
  a player with no plan would: a confirm confirmed, a modal filled with canned values chosen the way
  `_resolve_input` will read them, a picker's first option, a second guild member with no character
  for every member picker, so nothing mutes, bans or erases the character the rest of the run walks.
  It holds one thing per leaf: **the reply is a result or a designed refusal** - never one of the three
  fixed strings the bot prints when a handler raised (`WIRING_FAILURE_TEXTS`, held equal to the source
  by the gate), never the action meter, never an exception in `env.errors`. A leaf the panel hides
  must print its `🔒` lock line instead. The run's last step holds `pressed ∪ locked ∪ deferred == live`.
- **The engine legs build state with GM levers and never assert on dice.** Sections 20b-22 of
  `playtest_engine.py` drive every family a fresh pair of characters can reach - storage, equipment,
  artifacts, arrays, a slip, the hills, a purge, a Law, a fight at realm 7/9 that cannot be lost, a
  party and a raid, a duel ended by surrender, a Dao partnership, a house, the stalls, an underworld
  post at karma -60, a caravan and a seclusion waited out on the world clock, a secret realm the GM
  spawns and a key that opens another, a surprise made certain with
  `unexpected_event_chance_percent: 100` and driven by kind as it comes (a personal event worked and
  left, a world event acted in and its site engaged, a rift closed; which kinds came is reported),
  every remaining GM lever with its audit row checked, the dynasty a new life inherits, and last the
  erasure of the ghost. A roll is reported; a refusal that is
  designed either way (a claim the wheel may not have opened, a raid that may not be won in thirty
  rounds) passes on the refusal text that names why, and says which.

**What the first sweep found**, none of it visible to a source read, all of it fixed here with a
test (`test_leaf_sweep_findings.py`): no trade had ever left `/trade offer`, `accept` or `decline`
from Discord - the payloads carried `game_minute`, which the client refuses to send because the
engine stamps the canonical minute on every authoritative action and refuses a caller's; the forage
reply raised on every forage from the hub, because the engine's result flattened `d1`/`d2` and
dropped the degree while `roll_line` reads `die1`/`die2`/`degree` (the result now carries the roll
map whole); `/talk` at a grave or to a missing NPC called `npc.found` - a switch operation, not an
allowlisted one - through the authoritative client, which raised before sending; the event scene
called `WORLD.unexpected_events()`, a property; and a GM's currency grant to a member with no
character printed the failure text instead of the refusal its siblings give. Two lessons about the
harness itself: SimCord's settle timeout must stay at its default, because a bot-owned worker whose
next wake falls inside the deadline counts as runnable and a longer deadline never settles (a slow
leaf is waited out in short settles instead); and the typed explore's surprise chance is pinned to
zero, because an open surprise blocks the road and failed the capital step on the dice one run in
four.

**The sect and the homestead (v1.0.0-rc.36)** emptied the first deferred block. The staging is worth
knowing because three of its facts are not where a source read would look. A `sect_abodes` row is
never written by the engine: `/sect abode` stages it through the repository's `ensure_sect_abode`,
so the harness uses the same door, at the sect's `recruitment.location`. Contribution points come
only from `sect.contribute` (`quantity × sect_value`, the items into `sect_treasury`), and the manor's
establish (`spirit_iron` 30, `spirit_herb` 20, `beast_core` 10) and its upgrades eat that treasury;
the residence's upgrade costs `40·L²` points under a rank cap and a realm floor. The hidden sect's
initiation wants karma at or below `karma_initiation` (−200 in the content) and does not care about
a public membership. A recommendation refuses anyone already in a public sect, so it is the second
character's. A war starts the moment a second sect claims a territory the first holds (territory keys
are location names), and `war.act` is one act per side on a wall-clock cooldown, so nothing waits.
The homestead is the only buildable property type, founded at rank 40 in a normal town without an
auction house; `abode.focus` grants an effect for four rooms and none for the rest, and the harness
holds both. Every outcome that is a roll (the recommendation, the war's siege and morale) is reported.

**Progression (v1.0.0-rc.37)** emptied the second. Four facts decide its shape, and none of them is
where a source read would look. **The qi stage is filled by a lever and the body stage is not**:
`cultivation.reward` adds to `characters.cultivation`, but nothing except `cultivation.body_train`
writes `body_cultivation`, so the body path trains its stage full (about ten sessions at body 0/9,
`cooldown_seconds: 1` because the floor is 300 only at or below zero) while the qi path is handed 400.
The body ladder itself had no lever at all - `admin.player.set_realm` set the qi pair only - so it
gained an optional `body_realm_index`/`body_phase` pair, refused one without the other, carried in the
audit row and restored by the undo; the dashboard's realm card sends them only when both are filled.
**A perfection quest is a roll no lever can fix**: `canonicalAttribute` carries effects, arrays, a
root mutation and a bloodline, never the realm, so a quest is attr+2 (+1 when both ladders stand on
the same stage) against TN 13-18 on 2d10. Each is prepared to its requirement, then attempted on a
bounded loop; the preparation survives a failed roll. **The trial's gate is `completed_quests == 7
and progress == 100`**, and `admin.player.set_realm_perfection` writes `progress` alone, so it cannot
open it: the harness drives the trial when the dice allowed every quest and holds it locked when they
did not, and says which. The last twenty of the hundred come only from training at stage 9 while the
path is active (`perfectionTraining`, cap 20, one to three a session), on a stage that is already
full - training refuses nothing there, it gains zero essence and still credits the path. `abandon`
deletes only a row with `completed=0`, so a perfected realm is kept. **A tribulation attempt burns
nothing but the preparation**: five points of the departure world's stone (`low_spirit_stone` at the
Mortal gate), a sixth refused, three waves at TN 17-19 needing two, each failed wave a condition
(`meridian_damage`, `heart_demon`, `soul_wound`) that `condition.treat` clears with the herb or pill
`conditionDefinitionGo` names - the harness grants both first so a failed wave is treated rather than
refused. The aptitude rows differ by birth: every character has a root and a physique row, a
bloodline row only if creation rolled one, and `admin.player.set_bloodline` edits a row that exists,
so the harness reads which world it is in and drives the real path or the four designed refusals.
Space Law is supreme (floor Nirvana, realm 11; a fixed two-hour cooldown), and every `law.comprehend`
gains at least a point, so 100% is a bounded climb with `reset_cooldowns` between; the world itself
wants Dao Saint (realm 30), is keyed `personal_world:<user_id>`, and `leave` lands at Greenriver Town.

**What only the world makes (v1.0.0-rc.38)** emptied the last, and `DEFERRED_OPERATIONS` is an
empty dict on purpose. **A beast begins with the hunt roll and nothing else**: `wild_beast_encounters`
is written only by `explorationHuntAction`, on margin 4 of 2d10 against a TN of 12–15 at realm 0,
and no lever or payload flag forces one - but the hunt's cooldown is the one thing on the path that
*is* payload-adjustable, so an encounter is a bounded loop of free hunts, the tame a second roll
(spirit+presence+will/2 against the encounter's own `taming_tn`, on a fixed cooldown the GM clears),
and the family is driven on whichever the dice allow, every roll reported; `feed`, `train`, `active`
and `evolve` are certain once a beast exists (`evolve` is no roll: loyalty ≥ 60+10·stage, then
−20), and `set_beast_stats` makes the first evolution certain. **A bounty is deterministic**: the only
writer of `bounties` is `recordCrimeTx` (severity ≥ 3, evidence ≥ 50), black-market busts are severity
2 and never reach it, and a forbidden technique used in a fight while `concealment_active` is 0 is
witnessed with certainty - `blood_sea_palm` is severity 6 at evidence 77, a 300-stone bounty. The
hunters' spawner (`spawnHunters`) is not a roll and not a forceable system: it runs on every due tick
and fields a hunter for every open bounty, so the harness calls `run_due_simulation`, not `force`.
`bounty_hunter.act` is a formula plus a stable hash, never `gamerng`. **A disappearance is the one
thing that gained a lever.** Nothing but the `npc_life` tick lost an NPC (three in a hundred of the
people away from home, at most one a tick), so `admin.npc.set_missing {npc_name, missing}` stages
one: the tick's exact UPDATE and the tick's exact history row, through `game.RecordNPCMissingTx`,
which the batch now calls too, so a staged disappearance reaches the Quest Forge at 82 the way a
rolled one does and the two cannot drift. `missing:false` brings somebody home off-screen with a
quieter `npc_returned` row at 40; the undo restores status and minute and leaves the row, a record
that something was staged. It refuses the dead, the unknown and the already-missing; the dashboard's
NPC card carries Lose and Bring back, and `/admin npc setmissing` is the Discord side. The harness
tries the world's own way first (six forced ticks, reported either way), then the lever, and drives
`npc.found` from the wrong place (`elsewhere`) and the right one.

**What the leg found, the day it could drive `npc.found` at all**: the find answered `found: true`
and persisted nothing. `storage.Conn` begins a transaction implicitly on a handler's first write
(`maybeBeginImplicitLocked`), `npcFound` never committed, and the switch path in `ApplyWithWorld`
closes the connection when the handler returns - so `/talk` told the player they had found somebody,
the row stayed `missing`, and the search left no history; the grave claim on the same path handed
over a keepsake it never wrote. `npc_found_test.go` had driven the function on one open connection
and read back inside the same implicit transaction, which is exactly the shape that cannot see it.
`TestAFindThroughTheSwitchPathPersists` goes through `Apply` and reads back on a fresh connection,
fails with the production symptom when the handler's commit is removed, and the switch path now
commits a result returned over an open transaction and rolls back an error, so the answer and the
database cannot disagree again.

### The last Python-side clock (`world.clock`, v1.0.0-rc.39)

Go has owned the canonical game clock since v0.30.0 - and Python kept its own copy of the
arithmetic anyway. `Database.get_world_clock` read the anchor out of `world_state`, computed the
minute here, seeded a default row when there was none, and **re-anchored the row whenever the stored
scale disagreed with the `scale` argument** - which every caller filled with
`SETTINGS.world_time_scale`. So the dashboard's "New time scale" wrote a rate through the audited
lever and the next `/time`, `/cultivate` or narration quietly wrote it back. `/admin world
advancetime` did the same by construction: it sent `scale: SETTINGS.world_time_scale` on every call,
including the ones that only wanted the clock moved.

`world.clock` is the one door now: a world-status query returning `{game_minute, scale,
anchor_game_minute, anchor_real_ts, real_ts, seeded}`. **It deliberately seeds nothing** - it reads
through `loadCanonicalWorldClock`, the read-only loader, so a clock somebody merely looked at is not
a clock that started, and `TestWorldClockIsReadOnlyAndSeedsNothing` counts the rows to hold it.
`current_world_time` (~142 call sites, none of which changed), the bot's startup, the GM dashboard,
`/time` and `scripts/playtest_engine.py`'s `clock()` all read through it, and
`NarratorContextBuilder` takes the engine the way `WorldSimulator` does rather than a scale.

The arithmetic itself was **four copies in Go and two in Python**; it is now
`loadCanonicalWorldClock` (the SELECT, the decode, the two clamps) plus `worldClockGameMinute`
(`anchor + elapsed_real_minutes × scale`, floored at 0), and nothing else. `adminAdvanceTime` is the
one reader that still decodes the row leniently, because a row whose JSON is damaged must stay
repairable by the only lever that can repair it; it shares the seed and the arithmetic.

**`WORLD_TIME_SCALE` changed owners, and that is the fix nobody would have found by reading Python.**
The engine's compose service takes an explicit `environment:` allowlist and no `env_file`, so the
engine had never been able to see the key at all - Python's re-anchoring was the only thing that ever
applied it, which is to say the bug was also the feature. Compose passes it now, and it is the
`.env` baseline rather than the last word, exactly as the narration chain is: it seeds a **new**
world's clock row and nothing else, and on a world that already has one the stored scale wins until
the audited lever changes it. `/admin world advancetime` gained an optional `scale` sent only when
given. `Settings.world_time_scale` is gone, and
`tests/python/contracts/test_world_clock_read_through.py` holds that no file under `app/` or
`scripts/` so much as names `anchor_real_ts`.

### The world closed for maintenance (`maintenance_mode`, v1.0.0-rc.41)

An operator updating the server had no way to stop play while they did it. `/admin server
maintenance` is cleanup, VACUUM and the content resync - it stops nobody - and the engine's
`maintenanceBarrier` is a `sync.RWMutex` that makes a restore wait for in-flight writes and makes
them wait for it. That barrier never *refuses* anybody: a request held there queues and then runs
normally, which is right for a thirty-second restore and wrong for somebody swapping binaries under
a live world. `/admin server lockdown` and the dashboard's Maintenance card are the refusal.

**Where the gate sits is the design.** `applyAuthoritative` handles the ~150 player operations and
every `admin.*` lever falls through to the switch in `ApplyWithWorld` instead, so a check there
refuses players and *cannot* refuse a GM. That asymmetry is what makes the mode safe to have at all:
closing the world can never lock the operator out of reopening it, and
`TestAClosedWorldIsStillTheGMsToOpen` holds it.

**The engine gate alone is not enough, and the reason is the same one moderation states about
itself.** A read never reaches the authoritative path: `/sheet`, `/quests` and every other card
answer out of the presentation layer's own SQL, so the engine would let them through mid-migration.
The bot holds the matching gate at the four doors a player has - the command tree's
`interaction_check` (every slash command, including the reads `serialized_user_action` never
wrapped), `_invoke_action` (every hub button, select and modal, checked on the press because a panel
outlives the world closing), `on_message` (the typed line and the shorthand heard in every channel)
and `typed_play.dispatch` (a picker click, which is a button on a message and so never meets the
command tree). `app/bot/maintenance.py` is the one rule all four call — and since v1.0.0-rc.56
each of those four doors calls a second, `app/bot/seclusion.py`, which is why the tree class and
the panel gate are named for gating rather than for maintenance.

Three things are deliberate. **The flag fails open** - an absent row, unreadable JSON or an
unreachable database all mean the world is open, because a flag that gates all play must fail
towards play; a world nobody can enter is also a world nobody can reach to unlock. **The bot caches
it for three seconds**, so a panel's rapid clicks cost one read, and the Discord lever seeds that
cache from the engine's own answer so the very next command obeys without waiting for the TTL; the
dashboard needs no poke down the control channel for the same reason. **The scheduled tick stands
down**: `RunDue` returns no runs while the world is closed, so an update is not racing a batch, and
nothing is lost because every system schedules off `last_game_minute` rather than wall-clock - the
work is deferred, not skipped. `Force` is deliberately not gated, because a GM working on a closed
world is the point of closing it.

The reason an operator types is shown to players verbatim and is bounded at 300 characters in the
engine, not trusted from the payload. The Discord playtest drives this leaf explicitly rather than
through the generic sweep, and `DEFERRED_LEAVES` says why: the sweep answers a boolean by enabling
it, and this is the one leaf that would refuse every leaf pressed after it.

### The slip nobody could read, and the road nobody could pay for (v1.0.0-rc.43)

Two dead ends at a shop counter, and the same fault underneath: the thing is authored, priced and
implemented, and one wire is missing.

**`/learn` reached no player for twenty-three releases.** `recipe.learn` is allowlisted, writes the
method and spends the slip in one transaction, and carries an event-ledger row; the Discord command
exists with its own autocomplete over the slips in the player's bags; `content/world.json` authors
**33 method slips, one per recipe**, sold in **68 of 120 shops**. But `learn` was in neither
`_MIGRATED_ROOTS` (which lands a root on a hub page) nor the tuple in `register_command_surface`
(which adds one to the command tree), so it sat on no page and was never a slash command. There was
no other door: `/use` refuses any item without `use`/`storage_upgrade`/`array_deploy`, and the item
picker filters slips out of the list. `character_recipes` has two writers, and the other one is the
household lesson, which teaches one trade's `min_level<=0` methods — **7 of 33 recipes are at level
0**, so a character created today reached about three of thirty-three, and `/craft` was the thinnest
hub in the game.

It is on `/craft → Profession` now, beside `profession status` — where a player looks for what they
know, and one fewer of the eight single-action pages `test_hub_pages.py` caps. `/use` names the door
instead of saying a slip has "no implemented active use yet".

**The gate is the point.** `TheLearningStepTests` in `test_world_content_gate.py` is eight tests that
prove the slip content exhaustively, and its own docstring names *"two new ways to ship something
dead: a recipe no slip teaches, and a slip no shop sells"*. It was three. Every test there asks
whether the content is right and none asks whether anything reaches it, which is why the third way
was invisible from inside that file. `tests/python/unit/test_commands_reach_a_player.py` is the
missing half: every root in the registry must be on a hub page or in the tree tuple, or named in
`ALLOWED_UNREACHABLE` with a reason. **The allowlist is empty and was empty the day it was written** —
`/learn` was the only orphan of forty-five — so an entry there is a new decision, never a backlog
inherited from this one. The tree tuple is read out of `surface.py` by AST rather than copied, and a
test holds that the read still finds it, because a silently-empty read would make every root look
unreachable and turn the gate into noise.

**The caravan charged money that does not exist.** `caravan.dispatch` took `low_spirit_stone` — a
*Mortal World* currency — in all four worlds, and escalated to `mid_spirit_stone` at realm 4 and
`high_spirit_stone` at realm 7. Those two ids appeared at exactly the two lines that spent them:
nothing in the game has ever credited a tier above the base, and `walletDeltaTx` refuses a debit
beyond the balance, so `/economy → Caravans → Dispatch` was dead from the fourth realm upward.

**This is the same bug the teleport arrays already had**, and the fix the arrays got did not reach
here because a caravan's currency lives in Go rather than in content:
`test_every_array_charges_the_world_it_departs_from` says three of the four crossings charged the
*destination* world's currency, *"which no reward path grants and no exchange converts — so they
could only be paid by someone who had already arrived"*. `worldBaseCurrency` is that rule for the one
price that is code: the road is paid for in the tier-1 money of the world it departs from, looked up
off sorted ids so a map range cannot make it differ between runs.

**One door for money (schema 53).** A player's stones live in two places:
`currency_wallets`, the purse, and `characters.spirit_stones`, which is a *mirror* the sheet and a
dozen readers use. `walletDeltaTx` is the one function that keeps them in step — it writes the purse
and sets the mirror from the new balance. **Eleven other places wrote one of the two directly.**
`trade.accept` was the worst: it moved stones between two players with two bare
`UPDATE characters SET spirit_stones=spirit_stones-?+?` statements and named `currency_wallets`
nowhere in the file — so every trade left the two disagreeing, and because the mirror is written
*from* the purse, the next shop purchase silently overwrote the traded stones out of existence. Five
spends and a reward moved the sheet without the purse (the road toll, the ghost rites, mending a
channel, a find at a grave); two spends moved the purse without the sheet (tribulation preparation, a
crime's restitution); and seven paths wrote both by hand, which held only as long as they started
equal. The simulation package kept a byte-for-byte copy of the door as well (`walletDeltaSim`) — the
same "four copies of one rule" the world clock was fixed for in rc.39 — and it calls the exported
`game.WalletDeltaTx` now.

**Nothing caught it because no fixture could.** Most seeded `characters.spirit_stones` and never made
a `currency_wallets` row at all, and the shared `batch4` fixture's `characters` table did not even
carry the `spirit_stones` column — tests that needed it added it with their own `ALTER`. A fixture
that models one of two stores cannot fail the way production fails, which is the rule CLAUDE.md
already states; `syncPurse` gives a fixture both, and `TestThePurseHasOneDoor` is the gate: a write to
either store outside `walletDeltaTx` must be named in `purseWritersAllowed` with its reason. Five
entries, each a deliberate absolute write — creation, the starting stones, an undo restoring its
snapshot, and the GM's grant, which clamps at zero rather than refusing an overdraft.

**Migration 53 settles the drift upwards, deliberately.** Neither store is the complete record — the
mirror caught the trades and the tolls, the purse caught the shops and the fines — so the wallet is
set to the greater of the two and the mirror is then set from the wallet. Of the two ways to be
wrong, handing somebody stones they might not have earned is the one that does not take a fortune off
a player who did nothing wrong.

**The ladder is kept and deliberately not spent.** `CurrencyDefinition` parsed `name` and nothing
else, so the file's own `world`, `tier` and `base_ratio` — what one unit is worth in tier-1 units:
100, 10,000, 1,000,000 — were dropped by the parser and read by nothing. They are parsed now and held
to their shape by `TestTheStoneLadderIsWholeInEveryWorld` (four worlds × four tiers, one tier-1 each,
the ratio exact). No exchange between tiers is built: that is a mechanic rather than a parse, nothing
needs it while every price in the content file is tier 1, and a helper with no caller would be the
very thing this release exists to remove.

### The money of the world you are standing in, and the door out of it (schema 54, v1.0.0-rc.44)

Three faults at the world boundary, and they are one omission seen from three sides.

**Which money a world uses was said five times.** `content/world.json` declares all sixteen
currencies with the `world` each belongs to, and four switches in Go (`tribulationCurrency`,
`simulation.worldCurrency`, an inline map in `lifecycle_actions.go`, another in `simulation/world.go`)
plus a tuple in `app/dashboard/server.py` restated it. None could be wrong in an interesting way until
a fifth world is added or a currency renamed, at which point four are silently stale. It is
`worldBaseCurrency` now (exported as `game.WorldBaseCurrency` for the simulation package, and
`_base_currencies()` / `World.world_base_currency` on the Python side) and
`tests/python/contracts/test_one_world_currency_rule.py` is the gate: a production file naming three
of the four base currencies is restating the mapping, and `RESTATES_THE_MAPPING` is empty.

**Every reward paid in Mortal stones wherever it was earned.** `characterWalletDeltaTx` denominates a
credit by the world the character stands in, so a cultivator in the Spiritual World is paid in spirit
crystals — which is what the shops, the arrays and the black market up there have always charged. The
sheet's mirror (`characters.spirit_stones`) follows the same rule: `walletDeltaTx` writes it only when
the currency moved is the base currency of where they are, so the one number a sheet shows is local
money.

**And nothing converted when they crossed.** Each tier above the first carries a `base_ratio` — a
hundred of the rung below — and a world is that same ladder seen from further up, so
`crossWorldsPurseTx` divides by the rung going up and multiplies coming down. **The remainder stays in
the money it was already in**: 12,345 Mortal stones become 123 spirit crystals and 45 stones that are
still there when you go home. The credit is written even when it converts to zero, because that write
is what re-points the sheet's mirror at the new world.

**`moveCharacterTx` is the one door out of a world**, and `TestAWorldIsLeftByOneDoor` holds it the way
`TestThePurseHasOneDoor` holds the purse. Fourteen statements wrote `characters.location` and thirteen
could cross a world — the ascension breakthrough, an array, a GM's relocate, a personal world whose
`leave` lands in Greenriver Town, a Hearth-Return Talisman that carries you home *from anywhere* — so
without one door, which half of a fortune survived would depend on how you travelled. `admin_undo.go`
is the one allowed exception: restoring a snapshot is not a journey, and the money it snapshotted was
never converted.

**The gate you tear open (`ascension.gate`, `world_crossings`).** Clearing a world-crossing tribulation
wrote `tribulation_state.cleared`, paid +8 Heavenly Recognition and a fate point, and stopped — the
heavens opened over one named place and left nothing there, while the only anchored road up was one
authored array in one capital. `ascension.gate` anchors the seam where the lightning fell: a permanent
crossing at the character's own location, into the world the gate they survived opens onto.

- **It borrows the authored road.** `authoredCrossing(from, to)` gives the terminus, the fare and the
  realm floor, so nobody can tear open a cheaper road than the world already has; anchoring costs that
  fare times `raise_cost_multiplier` (10). The gate is refused inside a private place, somewhere the
  catalogue does not carry, where one already stands, and a second time out of the same world — one
  storm, one seam.
- **It is an ordinary array from then on.** `array.use` looks in the catalogue first and then at
  `crossing:<location>`, so a raised gate can never shadow an authored one, and both go through the same
  fare, the same `moveCharacterTx` and the same conversion.
- **The world's own people walk through it — the ones cut to its measure.** This is the only road in
  the game that leaves a world: `WhereAnNPCCanWalk` refuses a destination in another world by
  construction and still does, because content roads are content. `npcTravel` loads every open
  crossing **once per tick** (single figures of gates against 574 people) and offers the far side at
  `npc_crossing_chance_percent` — but only to somebody `game.NPCMayCross` admits. A seam is cut to the
  cultivation of whoever survived the storm that made it (`opened_realm_index` on the row), and
  `npc_crossing_realm_reach` (2) is how many realms either side of them still fit, on top of the
  authored road's own floor. A village smith does not walk into the Spiritual World because an
  Ascension-realm cultivator once tore the sky open over their town. A crosser's `world_name` is
  deliberately unchanged, so the far side offers them no onward neighbours and the existing
  going-home roll brings them back: a visit through the gate and out again.
- **Not everybody can climb, and most who can stop early** (`npc_talent.go`). `npcBreakthroughs`
  asked a candidate two questions - had they the wealth, had they the health - and both are things a
  porter can have, so every one of the 574 people in the world was on the same ladder as the sect
  elders with `realm >= 31` at the top of it. Talent is asked first now: a band off
  `hash64(name, "talent")` weighted by what the profession implies, added to the origin realm the
  *catalogue* gives them (not their current one, or the ceiling would rise every time somebody
  crossed it). Most mundane lives draw nothing and never leave the realm they began in; a sect
  disciple usually stops a few realms up; one in ten of them can go twelve. Nothing is stored -
  same seed, same answer, forever - and somebody at their limit says so in `activity`.
- **A seam opens the gate for the world, not just the road.** `npcBreakthroughs` crossed an NPC out
  of the Mortal World at realm 7 on wealth and health alone, so the tick walked the world's own
  people through an ascension gate a player has to survive three waves of heavenly lightning to
  pass - the heavens holding two standards. An NPC is refused at a gate realm now
  (`game.IsWorldCrossingRealm`), and there is nothing for them to answer with, because a tribulation
  is three rolls against a named character's attributes. What opens it is a player going first:
  once a seam is anchored out of that world, the people within `npc_crossing_realm_reach` of the
  cultivation it was cut at follow them up it, and **everybody else stays stuck** - their `activity`
  says "Stalled at the ascension gate", because `/civilization` and the GM's NPC card read that
  column and a world waiting at its own ceiling should look like one.
- **Keyed on `location_key`, never on who opened it** — `opened_by_user_id` anonymises on erasure
  (`erasureAnonymise`), exactly as `npc_graves.claimed_by_user_id` does, so an erasure cannot unmake a
  gate that other players and NPCs are using.
- **The quest is content.** `world_crossing_system.quests` is keyed by the departing world; a cleared
  tribulation hands the matching one over through `grantOrdinaryQuestTx`. There is still no second quest
  mechanism — a giver would make it a commission and the grant refuses one — and its two objective types
  (`ascension_gate`, `world_cross`) are reported after their commands answer, the rule
  `test_quest_objective_reporters.py` holds.

### The quests nobody could be given, and the rank worth examining (schema 55, v1.0.0-rc.45)

**The same fault, found and half fixed.** rc.26's own section above names it: `first_steps` was
seeded into `quest_definitions` on every boot, listed in `/quests`, and handed to nobody, because the
only writers of a `character_quests` row are `commissionAcceptAction` (which wants a `giver_npc` the
static quests deliberately do not have) and `grantOrdinaryQuestTx`. It built `beginner_path` to fix
that - for the beginner path. **`road_to_a_sect` was seeded by the same call and reached nobody for
nineteen more releases** (the string appeared in exactly one place in the tree: its own definition),
and `first_steps` was left seeded beside `beginner_household`, the stage that replaced it. `/city
board` lists only commissions whose giver lives in the city, so neither had a Discord door either.

- **The sect road is `beginner_lesson`'s `follow_on`**, which needs no new mechanism: `questFollowOnTx`
  already hands over any giver-less definition, wherever it was seeded from. It lands where the odds
  are worth taking - the trial rolls `body + realm×2 + phase/3` against `max(10, 15 - rep/25)`, so a
  cultivator who has finished the first hour is a far better candidate than a newborn one.
- **`sync_commission_pool` is insert-only on purpose**, so the content change reaches new worlds only.
  That is what **migration 55** is for, and it re-points only a stage whose chain is still empty, so
  a GM who chained it in the workbench is obeyed - the whole reason the chain lives in `seed_json`
  rather than in the file. It also retires `first_steps`, except a row somebody is somehow holding:
  retiring a definition must never take a quest out of a player's hands.
- **`tests/python/unit/test_quests_reach_a_player.py` is the gate for the class**, the quest-side
  twin of rc.43's `test_commands_reach_a_player.py`. Every seeded key must be reachable by a giver, a
  roster that grants (beginner path, household errands, ascension, examinations) or another quest's
  `follow_on`. `UNGRANTABLE_QUESTS` is empty and was empty the day it was written.

**The examination (`profession_exam.go`).** A trade's rank rose on XP alone - `60 + level*40` a step,
six silent steps from Novice to Saint - and nothing marked it. The hundred and twenty hall keepers
who sell a trade's slips had no opinion of anybody, and `character_recipes` had exactly two writers,
a bought slip and the household's one trade of entry methods, so twenty-six of the thirty-three
recipes were a shop transaction and nothing else.

- **It never blocks a level.** `advanceProfessionTx` is untouched and still raises a rank on XP, so
  the feature is safe on a world already running: no live crafter loses a rank, nothing needs
  grandfathering. The examination is what the rank is *worth*, and what it is worth is that rank's
  recipes (`teachRankRecipesTx`, exactly the rank, idempotent).
- **The examiner already existed.** All 120 shops carry a `keeper` and all 120 keepers are in
  `world.npcs`, so `hall_kind` names a shop kind (`weaponsmith` → Forging, `apothecary` → Alchemy,
  `talisman` → Inscription, `array` → Formation) and the keeper of the hall the candidate walked into
  examines them. Inventing an examiner would have meant a registry row, a schedule and a location for
  somebody the world already had.
- **The offer is made where a rank can rise**, in `craftResolveAction` rather than inside
  `advanceProfessionTx` - that helper has nine callers (beast taming, artifact refining, appraisal,
  foraging) and only crafting has examinations, and only the craft holds the catalogue and the
  canonical minute. A craft generous enough to cross two ranks offers the higher one, because the
  action only ever sits the rank the candidate currently holds.
- **The trade's attribute is said once.** `householdLessonAttribute` became `tradeAttribute`, read by
  the head of the house and by the hall alike; the content block deliberately carries no `attribute`
  field, because two statements of what forging is would be free to disagree.
- **The record is the event log, per life**, exactly as the household lesson's is - so samsara, which
  wipes `profession_progress` and `character_recipes`, lets a new life sit the same examination
  without deleting any history. A failure costs the fee and one world day; the fee is charged in the
  money of the world the hall stands in, which is the rc.44 rule and which the first version of
  `profession_exam_test.go` learned by picking an Immortal World hall for a Mortal candidate.

### The journal offers what nothing hands over (v1.0.0-rc.46)

rc.45's own fault, seen from the other side. It gave five rosters the power to hand a quest over and
left `QuestService.available` — the `/quests` "Available" block, the `/character → Quests` page, and
the accept select built from the same list — offering every one of their quests from minute one. The
first Discord sweep after the merge printed the proof: a character seconds old, shown ten
examinations, `The Expert's Toxicity` among them, at Novice, holding no trade.

**Accepting one is what stops its roster ever offering it.** `grantOrdinaryQuestTx` reads an
already-held quest as `(false, nil)` — a "no", not an error — so `offerProfessionExamTx` returns the
empty string, `exam_offered` is absent from the craft result, and the hall never says the examination
is open. `family.errand` hands over "the next unheld one", so a player could take all twelve from the
journal and empty the errand system; the beginner chain hands over a stage that has been sitting in
the journal since creation.

The rule is one frozenset of `source_key` **families** — the part before the first colon, which is
the shape the seeders in `app/rules/quests.py` already write (`household_errand:<trade>`,
`profession_exam:<trade>`, `world_crossing:<world>`, plus the bare `beginner_path` and
`sect_recruitment`) — stated once as `HANDED_OVER_BY_A_ROSTER` beside `visible_to`, in `ops` rather
than in `rules` because the layering puts those two side by side and neither may import the other.
`test_quests_reach_a_player.py` holds the frozenset **equal** to what the seeders write, so a sixth
roster fails the gate rather than quietly putting its quests back on the list, and it holds that no
seeded quest arrives two ways at once (a giver and a roster would be two doors, one of which
`grantOrdinaryQuestTx` refuses by design).

What is left under "Available" for most players is the Quest Forge's approved drafts and nothing
else, so where the journal used to print a list it now names where quests do come from. Nothing but
presentation changed: no engine action, no schema, no content.

**The harness had a stale assertion of its own**, and it is worth knowing which kind. The `/quests`
step looked for "First Steps" — `first_steps`, which was never held by anybody and only ever appeared
*under "Available"* — so rc.45 retiring that orphan broke the one step that reads the page. It reads
`beginner_household`'s real title now ("Before the Door"), which is a quest the player actually
holds, and it holds the finding: no roster's quest may appear under "Available".

### The checklist says what the sweep proved (v1.0.0-rc.47)

`docs/playtest/v1.0.0.md` is generated from the tree, and until now every one of its 248 actions
carried three live checkboxes — *reachable from the hub*, *error text actionable*, *narration or
fallback fired*. They were right in v0.34.0, when nothing in the repo could press a button. Then
rc.33 built `playtest_discord.py`, which presses every leaf the hubs register, and rc.35 built
`test_playtest_coverage.py`, which holds it to the live definitions so a new leaf is covered the
day it is registered — and the checklist went on asking a person for the first column outright and
for the wiring half of the second, for fourteen more releases. **Seven hundred and forty-four
boxes, not one ever ticked**, across twelve regenerations.

- **One machine column.** *Swept* is `sim` or `deferred`, and it is read off the harness's own
  `DEFERRED_LEAVES` by AST — the same read the coverage gate makes — so the column can never claim
  more than the sweep drives. The deferrals are listed under the hubs with the harness's own reason,
  and `TheChecklistSaysWhatTheSweepProved` holds the two equal: a deferral added to the harness and
  not regenerated into the file fails, because that is the one way this column can lie.
- **What a person is left is 27 rows**, under *What only a live server can show*: real Discord, the
  auction channels, `/vote`, a mute expiring on its own, `update.sh` against a real release — and
  the two the sweep *structurally* cannot do, named so they were not lost with the columns that
  used to ask for them. It cannot reach a live AI route (it runs `NARRATOR_PROVIDER=procedural`),
  and it cannot judge whether a refusal *reads* helpfully to a human — which is now one row against
  the sweep's log, an artifact that exists, rather than 248 empty boxes nobody walks.
- **`merge_ticks` was preserving the wrong half.** It looked only at lines starting with `` | ` ``
  and only at rows of six cells or more, so it carefully carried the per-action boxes across every
  regeneration and silently dropped the live-table ticks — the only ticks in the file that were ever
  a person's. It keys on the shape of a two-cell row with a checkbox now. Nobody had noticed because
  nobody had ticked one, which is the same fact that retired the columns.

**The gate had the same blind spot the code did, and the drill is what found it.** The first version
of `TheChecklistSaysWhatTheSweepProved` selected action rows by their shape — four cells — so
restoring the three old checkboxes made a row of six that the filter did not see, and the drill
passed when it should have failed. It selects by *where a row is* (above `## Loops beyond the hubs`)
and then asserts the shape. Same for the live table: `NARRATOR_PROVIDER=procedural` also appears in
the preamble that explains it, so a whole-file search passed while the row a person ticks was gone —
it is searched for inside the table now. A gate that cannot see the thing it forbids is decoration,
and only running it against the broken tree says which kind you have.

### What time it is was never the caller's to say (v1.0.0-rc.48)

rc.39 took the world clock's arithmetic away from Python. This is the last thing a caller could
still *tell* the engine about time, and it is the one `docs/TODO.md` had carried as a
deferred Authority item, sized there as "a Go change of its own".

`RunDueRequest.GameMinute` has been accepted-and-ignored since the v0.22.2 review, with the reason
written on the field: **"a scheduled tick must not be able to tell the world what time it is."**
`ForceRequest` and `BootstrapRequest` carried the same field and *used* it, for twenty-six more
releases — and `runSystem`'s own doc comment, two hundred lines below that field, said it stamped
the anchor "at a **caller-chosen** minute". One rule, two answers, both written down.

**What the number does is why it matters.** It is not a label on a log line: every system under
`applySystem` reads it as *now*. It is the minute an NPC's age is measured against, the birth minute
`seedHouseholds` stamps on ~88 households, the anchor `world_simulation_state.last_game_minute`
carries, and the founding of every clan. A caller sending a number a year out does not mis-title a
run — it buries people.

Both derive `game.CanonicalWorldGameMinute` now, the one door `RunDue` already reads.

- **The wire field stays, on all three, deliberately.** An older bot mid-upgrade still POSTs
  `game_minute`, and a request *refused* for carrying one would turn a rolling deploy into an
  outage. It is the value that is ignored, not the request, and `TestTheWireStillAcceptsAMinuteItIgnores`
  holds that — including a negative one and `1<<60`.
- **Python stopped computing it.** The three client methods take no minute, and nine call sites
  stopped deriving one to ship and have discarded. `WorldSimulator.initialize` still *takes* a
  `game_minute` and that is correct: it is a **read**, asking which black markets are open, and it
  no longer passes one on.
- **Two gates, because neither half can see the other.** `caller_minute_test.go` sends a wild minute
  and asserts the canonical one landed — against the old code it fails with `Force stamped 9999999`
  and `Bootstrap anchored at -4000000`, the production symptom exactly.
  `tests/python/contracts/test_simulation_minute.py` holds that nothing in `app/` or `scripts/` sends
  one, reading the client's *signatures* by AST so a parameter cannot creep back, and holds the wire
  field and the Go gate still present.

**The assertion that encoded the fault was two tests below the one that refuses it.**
`test_game_engine.py` has held since v0.22.2 that `authoritative_action` rejects a client
`game_minute` ("Go owns current world time") — and directly under it sat
`test_simulation_endpoints_keep_explicit_scheduler_time`, asserting `payload["game_minute"] == 12345`.
A file can hold a rule and its opposite a dozen lines apart and stay green for twenty-six releases;
that is worth knowing before trusting that a rule is enforced because a test near it says so.

**The fixtures needed the clock, and that is the rule this repo already states.** Two bootstrap
fixtures had no `world_state` table, so the canonical read failed on them — production always has it
(Python's migration makes it before the engine is ever asked to bootstrap), so the fixture was the
thing that could not fail the way production fails. The ten test call sites that used to pass a
minute now pin the clock with `setSimulationGameMinute`, a helper written for `RunDue`'s own fix and
sitting unused by these paths ever since.

### What the first hour is allowed to meet (v1.0.0-rc.49)

`UnexpectedEvent` has carried `min_realm_index` and `max_realm_index` since the roster was written,
and `eligibleUnexpectedEvents` has filtered on both — so the filter ran on every draw and **excluded
nobody**, because all forty-four events left both unset. A cultivator three minutes old, with
attributes of 1 to 3, drew from the same pool as a Nascent Soul elder: fifteen of the eighteen world
events are severity 4 or higher, and `A Dragon Appears` was as drawable at Body Tempering as the
village festival. The mechanism was built, complete and correct, and no content used it — the same
fault as `/learn` and the quest journal, and the fix is content because the code was never the
problem.

**A floor, by severity.** How bad a thing is decides how far along you have to be for it to turn up
in front of you: severity ≤3 from realm 0, 4–5 from 1, 6 from 2, 7–8 from 3, 9 from 4, 10 from 5.
One rule, stated once, and `test_beginner_world_events.py` holds every event's floor against it.

**A band to draw from, because gating alone leaves three.** `Local Trouble` is five village-scale
events — a caravan over the bank, an irrigation break before harvest, lantern night, something in the
granary, a travelling physician's free clinic — at severity 1–2 with `max_realm_index: 2`, so they
fade once a cultivator could end them by standing still. Their site's nodes are TN 10–12 against the
`tn + max(0, severity-2)/2` the engage roll uses, which a fresh character clears 55–79% of the time;
the gate computes that from the content rather than trusting the numbers look small.

**The floor gates the player-triggered draw only.** The autonomous batch still puts a Demon Invasion
wherever the world wants one, and a beginner can walk into it — being caught in something is not the
same as being handed it. That asymmetry is the design, not an oversight.

**The mechanism check had to be behavioural, and the drill is what proved it.** The Python gate first
asserted that `eligibleUnexpectedEvents`'s body contained `c.RealmIndex < e.MinRealmIndex`; changing
that line to `if false && c.RealmIndex < e.MinRealmIndex` left the substring in place and the check
passed. A grep cannot see a disabled condition. `beginner_events_test.go` hands a realm-0 character a
severity-10 event and asserts it is not offered, and fails with the whole map when the condition is
disabled; Python keeps only the one thing it can honestly check, that the Go half still exists.

### The peach that nothing grew (`rare_items`, v1.0.0-rc.50)

`hundred_year_peach` was the one item of 287 that the world could not produce: no shop sold it, no
recipe made it, no realm room held it, no event granted it, and no production file named it. It is
authored complete and expensive — `use.lifespan_years: 50`, `base_price: 12000`,
`auction_interest: legendary`, `door_event_chance: 65` — and **both halves already worked**.
`item_use_actions.go` grants the fifty years; `advanced_maintenance.go` reads `door_event_chance` to
write an `auction_door_risks` row when a legendary lot is struck. That second system had therefore
never fired either: you cannot auction a fruit that does not exist. One missing wire kept two
authored systems dark, the shape `/learn` (rc.43), the quest journal (rc.46) and the event bands
(rc.49) all had.

The lifespan ladder said where it belonged — `jade_life_herb` (5 years) is a secret-realm room
reward, `longevity_pill` (10) is an apothecary line, and the 50-year fruit was nothing.

**Why a chance and not just a placement.** A realm's rooms are walked again on every run:
`secret_realm_runs` keeps one row per *user* and `enter` does `ON CONFLICT(user_id) DO UPDATE SET …
room_index=0`. Nothing records that a realm was looted. And three of the eight realms have a key on
sale (448–672 stones, array shops at Ashenwall and Stoneback) — which are also the three low-floor
realms. So anything in a room's `items` is a guaranteed, repeatable payout, right for two spirit
herbs and wrong for a thing the world should have few of.

`rare_items` is what a room *might* hold, borrowing `ForageMaterial`'s shape (`chance`, `max`, minus
the richness floor a realm has no equivalent of) so the tree has one idea of what a find chance looks
like. It is merged into the room's own payout before the single `applyCanonicalRewardTx` call, so a
find cannot be paid twice or half-paid, and a miss is silent — a rare find that announced its own
absence would tell a player the roll had happened, which is most of knowing it exists.

**The home is the Salt King's Throne**, `salt_kings_barrow`'s last room at TN 18, which granted
nothing before. Keyless, opening on one weight-2 event — *"a barrow beneath the salt ruin that opens
when the marsh floods"*, about 1.8% of a realm-2 character's draws. Floor 2, because fifty years is
enormous low down and worthless high up, so every deeper keyless realm (8/9/16/24) is the wrong
audience. And **salt preserves**: "salt-preserved soldiers stand in ranks" is the reason a whole
hundred-year fruit is still sound in there.

**The sweep found a second orphan the moment it stopped counting tests as sources.**
`test_every_item_has_a_source.py` greps production Go and Python for every item id, and its first
version had no `--exclude=*_test.go` — so the peach looked sourced *by the very test written to prove
it had none*. With tests excluded, `living_world_ring` surfaced: the top of the storage ladder
(Immortal grade, 500 slots, the only `living_space`), 40,000, `door_event_chance: 75`, named only in
`support_storage_test.go`. It went into `SOURCELESS_ITEMS` with that reason rather than being
quietly placed — where it belongs was a content decision.

**v1.0.0-rc.51 makes it, and `SOURCELESS_ITEMS` is empty again** — emptied by placing the one entry
it ever held, so an entry there is a new decision rather than a backlog inherited from rc.50's. The
ring is found in **The Array's Heart**, `weeping_wall_sanctum`'s last room at TN 25, which granted
nothing before. The item named the world itself: its `storage_upgrade.grade` is `"Immortal"`, and
that realm is the Immortal World's (`min_realm_index: 16`), keyless, opening on one weight-2 event —
1.6% of a draw. **Chance 4, against the peach's 6**, and that ordering is a gate of its own now
(`test_a_rare_find_is_rarer_the_more_it_is_worth`): the peach is consumed and the ring is permanent
and tradeable at 40,000, so the dearer find must be the rarer one. No Go changed — the mechanism was
built, tested and shipped a release earlier, and all that was ever missing was where.

### A room of their own, and the door that never closed (v1.0.0-rc.51)

`content/world.json` authors **48 auction houses** that collapse onto **nine channels** — five grand
houses with a channel each and forty-three local floors sharing one per world, because
`auction_house_channel_name` reads the `channel_name` content gives them and many share it. All nine
were created in `SERVER_REALM_CATEGORY`, the category named for the four realm capitals, which
therefore held thirteen channels of which the capitals were the minority. `SERVER_AUCTION_CATEGORY`
(`🏮 Auction Houses`) is the third category, and `auction_house_channels.category_id` already
existed and was already written, so **no schema**.

Two halves come with it, and without either the split is cosmetic.

- **An existing server is moved, not merely rebound.** `ensure_auction_house_channels` resolves a
  channel by binding, then by name, and only ever passed `category=` to `create_text_channel` — so a
  channel that already existed kept whatever parent it had, and the change would have reached a fresh
  guild and no other. That is the failure mode of `/learn` (rc.43), the quest journal (rc.46), the
  event bands (rc.49) and the peach (rc.50) wearing a different hat. The re-parent sits behind
  `can_create`, because Discord layout is dashboard-owned and the `/admin` slash path still only
  binds, and it is issued **once per channel** (`moved`), because forty-eight houses share nine of
  them and `channel.category_id` is read from a cache the edit updates by gateway event.
- **Teardown deletes them, which it never has.** `clear_discord_bindings` has always `DELETE`d from
  `auction_house_channels`; `teardown_managed_discord_layout` built its targets from the base
  bindings, the realm hubs and `#bugs` and **named no auction channel at all**. So Teardown forgot
  the bindings and left nine channels standing — and because they sat inside it, `🌌 Realm
  Capitals` could never be emptied and was never once deleted by the action whose whole job is to
  delete it. Half the wire had been there since v0.33.1. The `seen_ids` guard already in the delete
  loop is what makes nine bindings on four shared channels one delete apiece; it was written for
  exactly this and had never had a case.

**The gate that could not see it is the lesson.** `test_the_helper_touches_only_what_a_binding_names`
asserted that the three sources it already knew about were named — a test shaped so that the thing it
forbids is invisible to it, which is the rc.47 finding again.
`test_every_category_setup_makes_is_a_category_teardown_can_empty` counts instead: every
`SERVER_*CATEGORY` constant in the file must be one the teardown loop walks (read off the source,
not copied), and every provisioning table must be one it deletes from. A fourth category or a fifth
provisioning helper fails it the day it is added.

### A world's news is that world's (v1.0.0-rc.52, schema 56)

`world-events` carried all four. A Demon Invasion in the Celestial World and a caravan over the bank
in a Mortal village landed in one feed, in front of everybody, whatever they could reach — while the
capitals have been split per world since schema 4 and the auction floors since schema 35.
`SERVER_EVENT_CATEGORY` (`🌠 World Events`) is the fourth category, and `world_event_channels` is
`realm_hub_channels`' shape minus `location`, because an events channel belongs to a world rather
than to a place in it. Gated by the realm **access** role, deliberately not the presence role: a
world's news is for everyone who has reached that world, not only whoever stands in its capital this
minute.

**The data was there the whole time, and half the wire with it.** `world_events.location` is
`NOT NULL` on every row, and `_event_scene_location` — the resolver that turns an event key into a
place — already existed and was **already called by both announcement writers**, about fifty lines
*after* each had posted. Moving that call above the send is the whole routing change. And
`event_threads.announcement_channel_id` is written at announcement time, so the "event closed"
notice lands wherever the announcement went and **needed no change at all**.

**The global channel stays, and that is the design, not a leftover.** Four writers have no world and
never will — the weekend gift, a GM's world-reset notice, the dashboard's test post, and the
channel's own blurb — and `BASE_CHANNEL_SPECS`' string for it already said *"Global
cultivation-world announcements"*. It is also the fallback, which is what makes this incapable of
breaking a writer: the worst case is the channel an announcement already used.

**`world_of_location` returns None rather than "Mortal World", and that is the one line worth
reading twice.** Every other site in the tree resolves a world with `or "Mortal World"`
(`discovery.py:30`, `channels.py`'s auction lookup). A private residence (`birth_family:<id>`), an
inner world (`personal_world:<uid>`), an abode, or the literal `Unknown` two writers can still
produce is not in `WORLD.locations` — so that default would file somebody's household news as that
world's public news. "No world" routes to the global feed, which is where all of it went before.

**Every channel's text was rewritten and `#xianxia-info` with it**, because the blurbs described the
v0.19 server and the guide said *"main realm-capital channels remain shared social spaces"*, which
stopped being true in v0.21.6. The guide is eleven topics, three new: **The Server** (the four
categories and which is gated by what), **World Events** (a scene's site is finite, so arriving
first is worth something) and **Crafts & Professions**. The four new channels get GM-editable
message slots resolved through `world_event_channels`, exactly as the `realm:` slots resolve through
`realm_hub_channels` — a prefixed key needed no new mechanism.

**The dashboard's World Events table counts toward `setup_ready`**, unlike `auction_halls`, which is
reported but excluded and has no stat card. A missing auction channel costs a lot card; a missing
events channel loses a world's news outright, so this takes the capitals' side of that asymmetry
deliberately.

**Both harnesses reach a `create_category` call now**, which rc.51 recorded as deferred. Every
provisioning helper defaults to `create_missing=False` and the one caller passing True is the GM
dashboard's Full Setup, so no slash command and no hub button could reach it — the categories, the
four capitals, the nine auction channels and these four feeds were provisioned by code no test had
ever run. **The bot's control plane is a surface, it is just not a Discord one**: section 2b of
`playtest_discord.py` posts `{"action": "setup"}` to `POST /control/discord` with
`X-Xianxia-Control`, exactly as the dashboard does, and holds that the four categories exist, that
each capital and each world feed sits in the right one, that nine auction channels were made, and
that a second Repair creates nothing new.

**A fourth gate this session passed its own drill, from both directions.** The harness check first
asserted the substring `_control("setup")` — commenting the call out left the string in place and it
passed, which is rc.49's disabled-condition finding in Python; it reads *call expressions* by AST
now. And the router's gate failed on *correct* code, because `world_of_location`'s docstring quotes
the `or "Mortal World"` default it exists to refuse; it reads the function's statements without its
docstring. A gate that cannot tell prose from code, or a call from a comment, is decoration — and
only running it against the broken tree says which kind you have.

### Where a secret realm's band belongs (v1.0.0-rc.53)

`eligibleUnexpectedEvents` has a **second branch** for `kind: "secret_realm"`, and it is the reason
none of the twelve such events carries a `min_realm_index`:

```go
if e.Kind == "secret_realm" {
    realm, ok := catalog.SecretRealms[e.SecretRealmID]
    if !ok || realm.Location != c.Location || c.RealmIndex < realm.MinRealmIndex { continue }
}
```

The floor and the place are read off the **realm**, so a band on the event would be a second
statement of a rule the realm already owns — the fault rc.39 removed for the world clock and rc.44
for the world currencies. rc.49 banded the eighteen `world_event` entries and scoped its gate to
`kind == "world_event"` deliberately; this is the other half of that decision, written down.

**Nothing drove the branch until rc.53.** rc.49's fixture is all `world_event`, so any of the three
conditions could be deleted with the suite green — in the file whose whole lesson is that a grep
cannot see a disabled condition. Three tests drive it now, each drilled. **`!ok` turned out to be
belt-and-braces**: a missing realm yields the zero value, whose `Location` is `""`, and nobody stands
at `""`, so the entrance check already excludes a typo'd id and disabling `!ok` alone leaves the test
passing. A typo is still worth a gate, because it is silently undrawable for ever rather than an
error anywhere — the class `/learn` and the peach were.

**The weights compete per realm, not globally**, because the draw only offers a realm to somebody at
its entrance. They were lopsided: seven of the twelve events opened the three Mortal-floor realms —
the only three that sell a key — at 4/5/4, while every realm from floor 2 up had one event at 2, so
the deep realms were harder to reach *and* opened half as often. Each realm totals 3 now, split among
its fictions where it has several.

**A realm fades once you have outgrown its world** (7 / 15 / 23, none for the Celestial). rc.50 is
why: rooms are walked again on every run and `rare_items` put a 12,000 peach at floor 2 and a 40,000
ring at floor 16, so an uncapped low realm is the repeatable payout rc.50 exists to avoid. It gates
the draw only — a key and a GM spawn still work, the rc.49 asymmetry. The Salt King's Barrow sells no
key, so above realm 7 the peach needs a GM; that follows from rc.50 calling fifty years worthless
high up, and it is a narrowing worth knowing about.

### Somewhere to go above the Mortal World (v1.0.0-rc.54)

Eight realms covered thirty-two realms of cultivation, four of them in the Mortal World and **one
each in the Immortal and Celestial**. Five new ones make it 4/3/3/3, and **no new location was
written**: every world carries three `road_site: "ruin"` legs and only one or two had anything under
them, so Last Lantern, Ash Gate, Cracked Altar, Buried Court and Nine Pillar were authored ground
with nothing on it. The ladders are read off each world's existing realms rather than invented
(Spiritual 16-22, Immortal 19-25, Celestial 22-28, +2 a room), the drops are existing tier items,
and the floors mirror the Mortal spread so rc.53's ceiling leaves a wide window.

**The treasures are the first permanent effects in the game.**
`ItemUse.DurationGameMinutes` has meant "0 does not expire" since v0.21.0 - the writer leaves `ends`
nil so the column is NULL, and every reader is `ends_game_minute IS NULL OR ends_game_minute > ?` -
and no item had ever set it. Each new realm's last room can yield one find granting **+1 to the
attribute that room's own trial tested**, for good: what the realm asked of you is what it leaves you
better at, stated once so the prize and the trial cannot drift. One point rather than three, because
it never wears off, and it lands in `canonicalAttribute`, the basis of every scene check, craft roll
and trial.

They sit in the rc.50 `rare_items` slot, ordered against the peach (12,000 → 6%) and the ring
(40,000 → 4%) so the dearer is the rarer, and all five are legendary with a door risk, so selling
one rather than drinking it feeds the auction-door system.

**The trap the field invites is worth knowing**: a writer that stored `0` rather than NULL would make
every treasure expire the instant it was used, and nothing would error.
`permanent_treasure_test.go` drives a real use, asserts the column is NULL, and reads the modifier
back a world-year later; its drill fails with `ends_game_minute is 1000, not NULL`. And the content
gate caught its own author - the authoring script wrote `max_realm_index: null` on the two Celestial
events instead of omitting the key, and rc.53's ceiling test refused it.

### What a spiritual root is worth (v1.0.0-rc.55)

`spiritual_root_system.grades` is a six-rung ladder, and every rung has carried two mechanical
numbers since it was written: a `cultivation_mult` (Mortal 0.88 → Immortal 1.34) and a
`breakthrough_bonus` (−1 → +3). Both are parsed into `worlddata.RootGrade`. **Neither was ever
read.** `BreakthroughBonus` appeared exactly once in all of `go_core` — its own declaration;
`CultivationMult` twice, its declaration and a fallback literal in `gradeDef` whose value nobody
read back. `RootGrade` has eight fields, and the six that decide **how a root is made** (the roll
band, the two element chances, the mutation chance, the realm a grade may evolve at) were all read.
The only two that decide **what having it is worth** were the two that were not: the grade decided
everything about how a cultivator was made and nothing about what they were.

Nor was it only a creation roll. `aptitude.evolve` (`/cultivation → Path → Evolve`) lets a player
**climb** the ladder — 2d10 against `13 + idx`, gated by `min_realm_to_evolve`, costing stability on
a failure and risking a forced mutation at margin ≤ −7. A whole progression system whose entire
payoff was the two fields nothing read.

**What the grade was worth instead** was one flatter restatement in a different system:
`rootAbsorptionBonus` added `elemental_qi_system.grade_bonus_per_rank` (0.02) a rung, plus a purity
term, and folded the result into `absorptionFor(...).Mult` — the **element** multiplier. So the
authored 1.52× spread was live as 1.10×, under another system's name, and two things followed that
no reading of the content file would show. It was **hidden**: `/cultivate` prints the element line
only when the relation is not indifferent (*"an indifferent element is not worth a line"*), so for
most cultivators the one thing their root did was applied and never shown. And it **switched off
when no method was practised**: `absorptionFor` returns `Mult: 1` early on an empty element, before
the root term, so a cultivator with no method got no root bonus at all and one with a method got up
to ×1.20. Elemental qi is the relation between a root's *elements* and a *method's* — which is
exactly why what a root is worth on its own never belonged in it.

`rootWorthMultiplier` is the one statement now: the grade's own multiplier, deepened by purity,
which moved to `spiritual_root_system` with it. The breakthrough half rides `mods` beside the root
mutation `loadEffectModifiers` already read, because `breakthroughModifier` reads no other channel
and the key it feeds — `innate_breakthrough_bonus` — is already named *Innate aptitude modifier* on
the surface. The cultivation half is a **named term** beside the method's and the ground's rather
than a `cultivation_gain` modifier, because the bot prints that stat as *"Active effects"* and a
root is not an effect; it sits **outside** the body carve-out, since that carve-out is about
elements and qi-path weather and a grade is neither, and `breakthroughModifier` already adds the
grade's other half on both paths. `element_mult` is the relation and nothing else now, so an
indifferent element really is ×1.00 and the suppression hides nothing.

**A grade the ladder does not carry is worth 1, never the bottom rung.** `gradeIndex` answers 0 for
a name it does not know, so the careless reading hands an unknown grade Mortal's 0.88 and −1 — and
`admin.player.set_spiritual_root` writes that column with no check against the ladder. It is the
`seller_user_id=0` lesson again: a fallback that looks like a value is not a sentinel. The real case
was in this repo's own fixtures, where the canonical character was seeded `'Heavenly'` — a name no
rung has — for releases, harmless only because nothing read a grade for anything.
`TestEveryFixtureRootStandsOnTheLadder` is the gate on that, and it is the rule CLAUDE.md already
states: a fixture must carry the constraints production carries.

**Python stopped inventing.** `aptitude_effects` multiplied the grade by invented purity,
mixed-element, compatibility and stability factors and published the product as an
`innate_spiritual_root` effect — and every one of the eight callers of `current_effect_modifiers`
discards the aggregate, and the rows never reach `active_effects`, so that arithmetic was the only
statement of the rule in the tree and it reached no mechanic. `root_cultivation_mult` is the display
twin of the engine's formula now. The honest claim is not "stated once" but **one authored number,
two readers that agree** — Python cannot call Go — and `test_root_grade_is_worth_something.py` holds
them equal across every rung and purity.

### A retreat carries what holds for its whole length (v1.0.0-rc.55)

`seclusionDailyGainGo` calls itself *"the one copy of the background-cultivation rate"* and applied
six of the ten terms a hand-sat session applies: pace, sessions a day, attribute quality,
environment, soul and world. It ignored **the effect multiplier, the era, the method practised and
what the root makes of its qi** — so a cultivator gathered at one rate sitting down and another
behind a closed door, and nothing said which was right. That is the rc.39 / rc.44 class: one rule,
two copies, quietly disagreeing, and the root this release wires would have been a fifth term the
two disagreed about.

`loadSeclusionCarried` reads those four and the root, and **never fails**: seclusion has never
touched the aptitude tables, and a retreat must not become refusable because a row is missing, so
every term defaults to 1 the way `soulCultivationMultGo` already answers 1 for a soul nobody
recorded (`loadAptitudes` errors outright on a character with no root row, which is why it is
tolerated rather than propagated). One `carriedMult` parameter carries them, so the start
projection and the settle payment cannot drift, and each call site computes it at its own moment —
the era can turn and a method can be changed while the door is shut.

What is still left out is left out on a rule: **a retreat carries what holds for its whole length.**
The hour of the day averages across days, a qi storm is momentary, and seclusion has no stance. The
manor array is the one arguable case and stays out because `environment_mult` is already the
function's statement of where the cultivator sat, and stacking the manor on it would price the site
twice.

Deferred out of rc.55 and delivered in rc.56 below: seclusion's **duration and its lockout**.
(**rc.55's own version of this paragraph said the lockout was "already promised and not enforced",
and that overstated it**: the engine enforced nothing, but Python held half a gate in
`serialized_user_action`. The whole of that half is written up in the next section, because what it
covered and what it missed is the finding.)

### What the doors are worth, and how long they stay shut (v1.0.0-rc.56)

rc.55 gave a retreat the multipliers a hand-sat session has and deliberately left its duration and
its lockout alone. Pulling on those two uncovered four more faults in the same system, and they are
one feature: the cap exists to bound the lockout, the settlement unit exists because of the cap, and
the rate can only be stated once the cooldown it is a share of belongs to the engine.

They also share one shape, which is worth naming before the six paragraphs that follow. Each is a
**number or a bound that the caller supplied and the engine accepted** — the wait between actions,
the length of a retreat, the minutes in a day — and rc.48 already wrote the rule down for the
clock: *a bound that lives in the client is not a bound*. The rate is the same fault turned inward:
a constant named for a rule, spent in a way that applied nothing, while the rule really lived in a
second constant that only meant what it said at one setting of a third.

**The wait was the caller's, in fourteen places.** `cultivation.train` read `cooldown_seconds` off
the request payload with a `<= 0 → 300` fallback, and thirteen other actions did the same — **seven
of them with no floor at all**, so a caller sending `0` served no wait whatsoever. The value lived in
`app/ops/config.py` and was mailed to the engine on every request. That is `rejectCallerGameMinute`'s
fault in a second place, and rc.48 already wrote the rule down for the clock: *a bound that lives in
the client is not a bound*. Both harnesses proved it was reachable — they sent `cooldown_seconds: 1`
to drive their loops, a legitimate use of an illegitimate door, and they ask
`admin.player.reset_cooldowns` now. `actionCooldowns` in `cooldown_rules.go` is the one statement,
read through `cooldownSecondsFor`, with the six `*_COOLDOWN_MINUTES` keys as the `.env` baseline
exactly as `WORLD_TIME_SCALE` is since rc.39 — **and compose had to be given them**, because the
engine service takes an explicit `environment:` allowlist and a key it is not given is a key it
cannot read. An action this table forgets waits an hour rather than nothing: a fallback that looks
like a value is not a sentinel, which is the `seller_user_id=0` lesson. `minutes_per_day` joined
`callerOwnedNothing` for the same reason — a unit of account is the same kind of number as a wait —
and the caravan's own copy of that refusal (v0.28.0) went with it.

**The rate was stated in the constant that did nothing.** `seclusionDailyShare = 0.60` is *named*
for the rule — "around 60% of an active cultivation day" — and was spent as
`daily * seclusionDailyShare / 0.6`, which is **exactly 1.0**. The rule really lived in an
uncommented `seclusionSessionsPerDay = 1.2` one file away, and **a count of sessions only means a
share of active play at one world time scale**: at the shipped `WORLD_TIME_SCALE=4` it happened to be
60%, at 2 it was 30%, and at 8 it was **120%** — an operator who sped their world up made
closed-door cultivation strictly better than playing, while it asked nothing of the player, and
nothing anywhere said so. `seclusionShareOfActive = 1.25` is the share now, and
`seclusionSessionsPerGameDay` derives the count from the cooldown it is a share *of*, so it holds at
every scale. A **premium**, not a discount: the doors are shut, and that is the trade.

**There were two settles, paying different rates.** `advanceSeclusions` in the simulation package was
a second implementation with a **pre-rc.5 flat rate** (`8 + will + insight/2 + realm/2`, times a
hardcoded `.60`), its own `minutesPerDay`, its own `.5`/`1.75` clamps, and none of the multipliers
rc.55 added — so which rate a retreat was paid at depended on whether the background sweep reached it
before the player came back. It had **zero tests**, which is how the two drifted twenty releases
apart. `game.SettleSeclusionTx` is the one settle now (`game.WalletDeltaTx` is the precedent for the
simulation package calling into `game` for a rule it must not copy), and `soulMultSim` and
`phaseCapSim` went with it — they existed only for the copy.

**There was no cap, and the one that existed was the client's.** `duration_game_minutes` was floored
at 1 and bounded by nothing; the only limit in the game was `days: Range[int, 1, 365]` on the slash
command. A retreat lasts **two real hours** now, and the deadline is a **real** one
(`ends_real_ts`, schema 57): stored in game minutes, a GM changing the world's rate would silently
re-size every retreat already under way. What a rate change *does* move is how many game minutes that
wall-clock covers, which is what a rate change means. It is **refused, not clamped** — house style
splits on intent, and a player who asked for a year and was silently given two hours would be told
twice over that they had what they asked for. A stale client's `duration_game_minutes` is still read,
converted at the world's rate and then held to the same cap, so a rolling deploy is not an outage and
the refusal is honest for it too. A NULL `ends_real_ts` is a retreat started before the column
existed: it keeps the length it was given, because a new rule must never shorten something a player
already committed to.

**Which forced the settlement unit.** Gain was paid per completed world-*day*. Two real hours at the
shipped scale is 480 game minutes — a third of a day — so **a retreat run to its own cap would have
paid exactly nothing**. It is per completed game **hour** now, through
`seclusionGainForSpan`, the one statement the start's projection and the settle's payment both use.
The two behaviours the day-based code documented are re-derived rather than ported: the remainder is
carried mid-flight and discarded at completion, and completion keys off the clock rather than off the
accounting (v0.23.1 — unit-accounting can never reach an end that is not a whole number of units).

**And the lockout the panel had promised since v0.30.0.** *"Any state-changing command will remain
locked until you use /cultivation → Cultivate → End"* — while the engine blocked exactly one thing
behind a closed door, a Hearth-Return talisman, and Python held **half a gate** in
`runtime.py`'s `serialized_user_action`. It is worth reading what that half was, because each part of
it is a different way for a gate to be decoration: it covered only the ~141 handlers wearing that
decorator, so **every read passed**; its exemption was a **function-name prefix**,
`func.__name__.startswith("seclusion_")`, which nothing structural held; a **hub press** was already
deferred by `_acknowledge_hub_action` before the wrapper ran; **typed play and free narration never
reached it at all**; and it **settled first and checked second**, so the engine action ran ahead of
its own refusal.

`checkPlayerSeclusionTx` is the engine half, one check in `applyAuthoritative` in
`checkPlayerModerationTx`'s shape, and `app/bot/seclusion.py` is the other, at the four doors
`app/bot/maintenance.py` already holds — because a read never reaches the authoritative path. Three
rules hold it:

- **It self-clears, unconditionally.** A gate that refuses every action, on state that only an
  action can clear, is a deadlock. The engine gate settles, pays and completes an expired retreat and
  *then* lets the action through, the way `ensureRoadTransitReadyTx` clears a finished journey. It
  **must not** depend on the `background_seclusion` automation flag — a GM switching that off would
  otherwise lock every secluded player out for good — which is why the flag-gated sweep cannot be the
  only end.
- **The way out is always open.** `seclusion.settle` is the one exempt operation, so
  `/cultivation → Cultivate → End` works whatever else is refused, including for a retreat
  grandfathered from before schema 57 whose deadline is a game minute a frozen clock may never reach.
- **The leading slash is load-bearing.** `_invoke_action` passes a hub leaf's `path`, which always
  starts with one, and the command tree passes a bare `qualified_name` — and they collide: `/craft`
  is the leaf that resolves a craft roll *and* `craft` is a hub whose panel is a read. Normalising
  the slash away would have opened every leaf whose name matches a hub. A hub's panel opens (it is
  where the way out is drawn, so refusing it would hide the only door) and each leaf inside it is
  checked again on the press. What stays open is an explicit list of names held equal to the live
  hub definitions and to the tree tuple, because *which* doors stay open is a decision — the old
  gate's exemption was which decorator a handler happened to wear, which is nobody's decision and is
  exactly how every read got through.
- **An administrator is not exempt, but `/admin` is.** Maintenance exempts the person, because they
  are how the world reopens; a retreat is the player's own state, so a GM in seclusion is in
  seclusion and what stays open is the `/admin` tree. Every `admin.*` lever falls through to the
  switch in `ApplyWithWorld` rather than reaching this gate, so that half is free by construction —
  the same asymmetry maintenance relies on.

**And the promise named a button that does not exist.** The panel has said *"use /cultivation →
Cultivate → End"* since v0.30.0; the leaf is labelled **Seclusion End** (`/seclusion end`, beside
`Seclusion Start` and `Seclusion Status`). Nobody noticed in twenty-six releases because nothing was
ever locked, so nobody followed the instruction under pressure. Every player-facing copy of it —
the start reply, the status card, the engine's own refusal and the new cultivation-card line — names
the real leaf now.

**Two gates renamed, because one of them had become a lie.** `_panel_maintenance_gate`,
`register_maintenance_gate`, `_maintenance_refusal` and `MaintenanceAwareTree` each now carry two
rules, and a gate named for one of them is the same class of lie as a `sync_world_catalog` that
syncs no catalogue. They are `_panel_gate`, `register_panel_gate`, `_panel_refusal` and
`GatedCommandTree`, each with the old name in its docstring.

**No hidden-actions provider, deliberately.** `_household_hidden_actions` and
`_progression_hidden_actions` hide a door the engine would refuse outright; this would hide ~290 of
them and print ~290 lock lines, which is a worse panel than a refusal on the press. Maintenance
mode, which is the same kind of whole-player state, hides nothing for the same reason. What the
cultivation card gained instead is a **Seclusion field** — it rendered nothing about a retreat at
all, so a secluded cultivator saw the ordinary sheet and no sign that every other command was about
to refuse them, which was survivable while the lockout was half a gate and is not now.

**The gates, and what each drill prints.** `seclusion_rate_test.go` drives the share at scales 2, 4,
8, 12 and 60 and asserts 125% at each — the one thing `1.2` could never be asked; its drill restores
the constant and prints *"a retreat is worth 0.3000 of active play"* at scale 2 and *"1.8000"* at 12,
which is the finding in the test's own output. `seclusion_cap_test.go` holds the refusal, the stored
real deadline, that a scale change does not move it, and that a **stopped clock** still admits a
retreat (scale 0 is a supported state, and a cap computed in game minutes would be `120 × 0 = 0` and
refuse everybody). `seclusion_lockout_test.go`'s two load-bearing tests are not "is a secluded player
refused" but **can one ever get out** — on the deadline and before it. `seclusion_one_settle_test.go`
holds that the sweep reaches `game.SettleSeclusionTx` and **does no arithmetic of its own**, read by
AST: a rate is made of numbers, so a function with none cannot have one.

### The column that was written twice (v1.0.0-rc.57)

rc.56 could not bootstrap a fresh database. `seclusion_sessions.ends_real_ts` was added in **two**
places - the base `executescript` DDL in `Database.init`, and migration 57's
`ALTER TABLE ... ADD COLUMN`. The base script runs on every boot, *before* the migrations, so on a
new world it created the table already carrying the column and migration 57 then died on
`duplicate column name`. **Upgrading worked and installing did not**, which is the opposite of the
usual way round: on an existing world the `CREATE TABLE IF NOT EXISTS` is a no-op and the ALTER has
something to do.

The convention it broke is unanimous and was never written down: **all 59 `ADD COLUMN` migrations
before it name a column the base DDL does not.** A column a migration adds is the migration's alone.

**Why the whole suite passed anyway is the finding.** The migration runner *has* a guard for a
duplicate-column ALTER - SQLite has no `ADD COLUMN IF NOT EXISTS`, so one is needed for the
recovery case its comment describes. It named `sqlite3.OperationalError`, which is what local
aiosqlite raises, and **every pytest fixture uses the local transport**. Production reaches SQLite
through the Go engine, which wraps the identical SQLite message in a `RemoteDatabaseError`. The
guard was live in every test and dead in every deployment. `test_startup_health.py` bootstraps a
fresh database and passed, over the transport that cannot fail the way production fails - this
repo's own rule, found this time inside the migration runner itself. It catches both types now,
which is the recovery case actually working rather than a licence: the convention is the rule and
`test_a_migrations_column_is_its_own.py` is what holds it.

**The playtest is what found it**, on the first run anybody had ever given rc.56 - which is the whole
argument for the two harnesses. It also found the second half: `walk_perfection`'s quest and trial
loops still called `act` where rc.56 closed the door they depended on. A quest is prepared up to
eight times in a row against a real hour of `PERFECT_QUEST_COOLDOWN_MINUTES`, and the loop used to
send its own `quest_cooldown_seconds`; it asks the GM lever through `act_free` now, like every other
bounded loop in that file. Twenty-seven call sites were converted in rc.56 and these two were missed,
because nothing ran them.

**The Discord half found a third one, in rc.56's own new step.** Section 4c pressed *Seclusion End*
and then asserted `"closed-door" not in` the reply - and a leaf press redraws the panel, whose
cultivation card grew a **Seclusion** field in that same release reading *"closed-door, every other
command is locked"*. The step failed on its own feature. Asserting the **absence** of a string is the
fragile shape whenever anything else may legitimately emit it; it asserts *"emerge from seclusion"*
is present now, which cannot collide.

**And the gate's own first version passed against the broken tree**, which is worth more than the fix.
It split the module at `CORE[:start]` on `SCHEMA_MIGRATIONS` - but that tuple is declared at line 232
and `Database.init`'s DDL is 2,500 lines *below* it, so `base` was the import block and the check saw
no tables at all. A second version searched for the column name across the whole file and reported
six clashes that were not clashes, because `tier` and `location` are columns on plenty of other
tables. It reads the columns of *that table's* `CREATE TABLE` now, and asserts a known column comes
back before it trusts the answer - a reader that silently finds nothing makes every assertion after
it vacuous. Three drills: restore the column and it names `seclusion_sessions.ends_real_ts`, narrow
the guard and it names the transport, restore the bad split and the self-check fails first.


### The numbers that reach no rule (v1.0.0-rc.58)

`active_effects` modifiers are a vocabulary **nothing was holding**. Any string may be written as a
`stat`; `loadEffectModifiers` sums it into `mods`; and no gate ever asked whether a rule reads it
back. Content authored twenty distinct stats and production Go wrote twelve. **Seven reached no
rule.** That is the mirror of the scar `property_storage_actions.go` has carried since rc.19, where
`formation_bonus` was *"a stat no rule could ever grant"* — here it was a stat every rule could
grant and none read.

**Two of the seven were near-miss names, and the tree shows why.** `sense_actions.go` reads *both*
`c.SensePrecisionBonus` — the real `characters` column, at `:33` and `:144` — and
`senseExtraModifier(…, "sense_precision")`, the modifier, at `:168`. Two channels, one suffix apart,
in one function. `special_effects.space_domain` authored `sense_power_bonus` and this package's own
`conditionEffectGo` authored `sense_precision_bonus`, both reaching for the column name. They are
provably mistakes rather than decisions: content spells `sense_precision` correctly twelve times
across mutations, bloodlines and physiques, and `senseExtraModifier` is called with exactly three
literals, none carrying the suffix. The consequence was that a **Soul Wound had never dulled
anybody's spiritual sense** — the one thing a soul injury is for.

**The other five were never wired**, and what it cost a player is that the item's own description
was the promise. `/specialeffects` prints name, category, severity and remaining — never the
modifiers — so nothing ever showed the numbers were dead. `heart_calming_pill`, the treatment item
for three of the four conditions and the thing `family.support` hands out, says *"Settles the mind,
aids insight and suppresses heart-demon disturbances"*: one of three was real. `purging_phoenix_pill`'s
`use` effect was **entirely dead**, both modifiers unread, so its whole stated function was
unimplemented.

`escape_bonus` rides the flee roll now (`combat_actions.go`, the one site — pvp has an unconditional
`surrender` and group combat has no escape). `heart_demon_resistance` rides the **Heart Tribulation
wave**, the wave the heart demon comes from, at `resistance/heartDemonResistanceScale` — 25 on the
pill is +2 on a 2d10 check, a real share of the wave a cultivator most often loses and not a way to
buy the gate. `detox_power` and `fire_resistance` are the purge, below. And `insight_gain` needed a
door first.

**Insight XP had eight writers.** A multiplier applied at one of eight is rc.56's two settles and
rc.43's two stores. `grantInsightXPTx` is the one door; six grant sites route through it, and
`TestInsightXPHasOneDoor` holds the rest to a named reason — the GM lever, the master's reward paid
out of a *disciple's* breakthrough, the four spends (a multiplier must never touch a debit) and the
two absolute writes at creation and samsara. It **derives the canonical minute itself** rather than
taking one: four of the six sites have no game minute in scope, and threading one through four
signatures to reach a clock the engine owns is the caller stating what the engine already knows
(rc.48). Neither the clock nor the multiplier can refuse a reward — an unreadable clock means no
multiplier, the way `loadSeclusionCarried` answers 1 — and a positive grant never rounds to zero,
because the one thing a bonus must never do is take a reward away.

### The capstone that named an effect nobody wrote (v1.0.0-rc.58)

`law_system.techniques.world_collapse` declared `"effect": "world_collapse"` at `requires_stage: 5`
and `min_realm_index: 30` — the deepest thing on the game's ladder — and `special_effects` carried
seven entries, none of them that one. `law_technique_actions.go:104` refuses an effect the catalogue
does not carry, quite correctly. So a realm-30 Dao Saint, with Space Law at Essence/Origin and a
stabilized personal world, pressed the capstone and was told *"law technique world_collapse names an
unknown effect"*. Twelve releases.

**Why the Go test missed it: the fixture rewrote the link.** `lawTechniqueCatalog()` declared its
*own* `world_collapse` with `Effect: "spatial_step_echo"` — an effect the fixture provides — at
`RequiresStage: 2, MinRealmIndex: 3` instead of 5/30. `TestWorldCollapseNeedsAPersonalWorld`
therefore asserted the capstone **succeeds**, against a technique the fixture invented. That is the
`npc_consignments` lesson arriving through the front door: a fixture that cannot fail the way
production fails is not testing production. The fixture keeps the mechanism tests — pinning a stage
gate to production content would break it on every content edit — but its keys are `fixture_step`
and `fixture_echo` now, so nothing in it can be mistaken for a shipped id, and every rule about a
*named* production technique moved to `special_effects_content_test.go`, which drives the shipped
catalogue.

**Why the playtest missed it: it walked past.** `playtest_engine.py` climbs Space Law to
comprehension 100, stands at Dao Saint realm 30 and creates a personal world — **every precondition,
in consecutive lines** — and never pressed it. Its only `law.technique` call expected a refusal, and
rc.35's coverage rule counts an operation as driven when it is a driver call's first string
argument. That rule is exactly right for catching an operation nothing calls and not enough for one
called only into a designed refusal, so **driven now means resolved**: `REFUSAL_ONLY_OPERATIONS` in
`test_playtest_coverage.py` is the new allowlist, and it is **not empty on the day it was written**
— unlike `SOURCELESS_ITEMS`, `UNGRANTABLE_QUESTS` and `ALLOWED_UNREACHABLE` — because tightening a
loose rule reveals the backlog the loose wording created. Two entries: `artifact.awaken` wants Bond
3 and no lever sets it; `meridian.heal` wants a rupture nothing can stage. Its drill is the finding
itself: revert the capstone step and the gate reports `law.technique`.

The effect is authored as a Domain of severity 6, strictly above `space_domain` on every stat it
shares and a superset of them, using only stats a rule reads — including `escape_bonus +4`, the
positive counterpart of `spatial_lockdown`'s −5, so that stat is no longer one only a debuff could
write. Inside a world you folded yourself, leaving is your decision.

**And the engine never refused a control technique out of battle.** `combat.technique` resolves
`spatial_lockdown` as suppression turns and `spatial_strangulation` as damage, and writes **no
effect row at all** — so `lawTechniqueAction` is the engine's only writer of a law effect, and it
writes on the *user*. Nothing stopped a cultivator applying `agility −3, escape_bonus −5` to
themselves for two hours with no target anywhere in the world; the only guard was
`app/bot/commands/law.py`, which is rc.48's rule in a third place: **a bound that lives in the
client is not a bound**. It was latent only because `escape_bonus` was dead, so wiring the stat is
what armed it, which is why the refusal ships in the same release. The engine reads
`special_effects.<id>.category` against `lawControlCategory`; `World.law_technique_targets_another`
asks the same field, so the panel's set of ids is gone and
`test_control_techniques_are_the_contents.py` holds that no production file keeps another. The two
control effects are still never applied to anybody — a battle opponent is a name on `battles`, not a
row anything can modify, and there are no PvP techniques — so `combat.technique` at least *names*
what landed now. Applying them is a mechanic, not a wiring, and is deferred with that reason.

**One door for the catalogue.** There were two `catalog.SpecialEffects[...]` lookups and they gave
different answers to "unknown": the law path took the comma-ok and refused; the abode path indexed
the map bare, got a nil value, wrote the literal `null` into `effect_json`, and **succeeded applying
nothing** — the same sentence `property_storage_actions.go`'s own comment already has to write about
rc.19, reached by a different cause. `specialEffectPayload` is the one door and
`TestTheSpecialEffectsCatalogueHasOneDoor` holds it in `TestThePurseHasOneDoor`'s shape.

### The flame the pill always warned about (`alchemy.purge`, v1.0.0-rc.58)

There is no fire or elemental harm anywhere in production Go. But two pieces of content describe the
same unbuilt mechanic and **both name a condition the engine already has**:
`items.purging_phoenix_pill` — *"dangerous without cooling medicine"*, and, on its effect, *"without
cooling support it may scorch meridians"* — and `physiques.nine_yang_solar_body.drawback`, *"Excess
yang scorches the meridians."* `meridian_damage` is real: defined in `conditionDefinitionGo`,
treated with `jade_life_herb`, written by `applyCombatCondition`. Meanwhile `alchemy.purge` had **no
risk at all** — it spent qi and removed toxicity and that was the entire action.

So the mechanic is built from what the content specifies rather than invented, and both orphan stats
land in the one action they belong to. `detox_power` raises `alchemyPurgeAmount`: the pill's authored
40 is +10 toxicity burned off, which roughly doubles a mid cultivator's purge and is what it costs 26
stones for. Above `pillToxicitySaturated` — 40, which was the bare literal in the two readers of the
shared penalty row and is stated once now — the purge rolls a **scorch**, `body/2 + will/2 +
fire_resistance/5` against `8 + (toxicity−40)/6`, and a failure applies `meridian_damage` at severity
1, or 2 on a margin of −5 or worse, the tribulation's own shape.

Measured at body 8 / will 8, the scorch chance is **1% at toxicity 60, 10% at 80 and 36% at 100**
without the pill, and 0% / 3% / 21% with it. A light purge is exactly as free as it has always been —
below saturation there is no roll — and a heavy one is genuinely dangerous, which is what the item
has said for its whole life. The harsh corner is self-selecting: the only way to toxicity 100 is
refining a great many pills, which is an alchemist, who can make the pill that halves it.

`nine_yang_solar_body` gains `fire_resistance −5`, the one line of new authored content in the
release. Its `drawback` prose has always said this and the modifier it carried (`sense_precision −1`)
said neither half of it; and a resistance stat with only positive writers collapses the roll to *did
you drink the pill*, which is a switch rather than a risk. The cost is bounded — a voluntary action,
on an hour's cooldown, only above saturation, and −5/5 is −1 on the roll.

**The gates, and what each drill prints.** `modifier_vocabulary_test.go` walks the vocabulary from
both sides — the content file as raw JSON, so a block no Go struct parses still counts, and
production Go's own composite literals by AST — and requires every stat to be *fetched* by a rule.
**Fetched, not named**, and the tree is why: `sense_precision_bonus` occurs three times in production
Go, once as a modifier and twice as the `characters` column inside SQL strings. A substring search
finds all three and calls the stat read; so does a scan for the identifier. Only an argument
position, a `mods.Add[…]` key, `craftEffectStat`'s return, or a name in `canonicalAttribute`'s own
`allowed` map can distinguish them. `unreadModifierStats` is empty. Its drills restore each typo and
it names them (`sense_power_bonus (written by content/world.json)`,
`sense_precision_bonus (written by combat_actions.go)`); removing the flee term names
`escape_bonus`; disabling `applyStatModifiers`' stat comparison — the rc.49 shape a grep cannot see —
names six at once.

**Its own drill found the last fault in it.** Pointing the content path at nothing made the whole
test **SKIP**, green and useless, because the walk copied `shippedCatalog`'s defensive `t.Skipf`.
The content file is in the repository and always present, so a read that fails means the gate cannot
do its job — it is a `t.Fatalf` now. A gate that can go quiet instead of red is the decoration rc.47
and rc.52 each caught, and only running it against a broken tree says which kind you have.

### A server you can read at a glance (v1.0.0-rc.59)

Four categories, and one of them held all eight base channels plus the `#bugs` forum, which had
nothing to do with each other: `#begin-here` where a new player starts, `#world-events` for global
notices, `#player-homes` and `#expeditions` (read-only thread anchors nobody posts in), `#bot-logs`
for the operator, `#playtest` and `#bugs` for feedback. And **categories
were never positioned** — no `position=`, no `.edit(position=`, no `.move(` anywhere under `app/` —
so their order was the call order of `_run_complete_server_setup`, appended at the bottom of the
guild by Discord. Nothing in the tree said what the order should be, which means
nothing could be wrong about it and nothing could be right either.

Eight now, in one stated order (`CATEGORY_ORDER`): 🚪 Start Here, 📣 Announcements, 🌌 Realm
Capitals, 🌠 World Events, 🏮 Auction Houses, 🗺️ Cultivation World, 🛠️ Feedback, 🔒 Admin. The
newcomer's path, then what is announced, then the world itself — where you go, its news, its
markets — then your own threads, then feedback, then the operator's.

**The finding is the half that would have reached nobody.** `ensure_base_xianxia_channels` computed
its category *and* its read-only overwrite only inside `if channel is None and can_create:`. A
channel that already existed — pre-existing, name-matched, or bound by a GM — got neither, ever. So
three things were true at once and none of them was visible from a source read:

- A category split written the obvious way would reach a fresh guild and **no server anybody is
  running**. That is `/learn` (rc.43), the quest journal (rc.46), the event bands (rc.49), the peach
  (rc.50) and the auction channels (rc.51) wearing a sixth hat — and rc.51 is the same bug in the
  same file, found once and fixed for one helper out of five.
- `#xianxia-info`, `#expeditions` and `#player-homes` were read-only **only where the bot had made
  them**. `READ_ONLY_BASE_CHANNELS` was consumed at exactly one place in the tree: the `overwrites=`
  argument of `create_text_channel`.
- `ensure_base_xianxia_channels` returned `"repaired": []` as a **hardcoded empty list**, which
  propagated into the audit row and the slash reply — a field that had shown nothing since the
  function was written, because there was nothing it could show.

`ensure_realm_hub_channels` and `ensure_bugs_forum_channel` had the same hole.
`test_the_layout_reaches_an_existing_server.py` holds all five helpers now: each must compare
`channel.category_id != category.id` and issue `channel.edit(category=`, behind `can_create`
because Discord layout is the dashboard's to own. Its allowlist is empty. The read-only half is read
by **AST rather than substring** — the set's name appearing in the function proves nothing about
*where*, and where was the entire bug — and the gate asserts a reference to it exists outside the
`channel is None` branch. `channel_messages.py`'s own guide text has claimed since rc.52 that Repair
"moves existing ones into the category they belong in"; it is true now.

**The category that must not be deleted.** `SERVER_BASE_CATEGORY` (📜 Xianxia RP) is created by
nothing and is deliberately still declared, still in the teardown tuple, and carries a comment
saying why. `test_discord_teardown.py` asserts the constant set **equals** the teardown tuple — right
for rc.51's bug, where a category Setup made was not one teardown could empty. Read the other way it
is a trap: a category Setup *stops* making is one every existing server still has, and set-equality
pushes you to delete the constant, which would orphan the category on every server in existence.
`CREATED_CATEGORIES` is what Setup makes; the tuple is what teardown can remove; they are
deliberately not the same set, and `test_category_order.py` holds both halves.

**`BASE_CHANNEL_SPECS` learned where each channel belongs.** It was `name -> topic`, with the
category a single argument every base channel shared. It is a `BaseChannel(topic, category)` now —
one statement per channel, not a parallel dict free to drift. The `category` is a **bucket key**
rather than a name, because the names are `SERVER_*CATEGORY` in `server_setup.py`, which imports
`channel_messages.py` and so cannot be imported back; `BASE_CATEGORY_NAMES` is the one place the
bucket meets the string, and it lives where the teardown gate can see it. `/admin server
basechannels` lost its `category_name` argument in the same move: one name could no longer mean
anything, and a parameter that does nothing is the class of thing this release exists to remove.

**`#updates`, and the bot posts its own release notes.** Every release's notes were already written
in the form a player can read — `VERSIONS.md`'s changelog, one entry per release, already held to
the stamped version by `test_release_version.py` — and nothing had ever shown them to anybody. A GM
who upgraded had to go and read the file. `app/bot/admin/release_notes.py` parses the entry for
`INSTALLED_VERSION` and posts it into `#updates` at `on_ready`, chunked under Discord's 2,000
characters (rc.58's entry is 3,801). Three rules keep it from being annoying, and the first is the
one that makes it safe: **the row is the memory.** `server_config.announced_release` is the release
this guild has been told about, so the post is idempotent across restarts by construction rather
than by a flag somebody has to reset — the same thing `(user_id, quest_key)` does for the beginner
path. A guild whose marker is NULL **records the running release and says nothing**, because a
server being set up today does not want forty paragraphs of history. And an unbound `#updates` is
not an error and does not advance the marker, so binding it a week later still gets the notes.

A base channel is column-per-channel, not generic — **ten** places, from a `server_config` column
and a migration through four edits inside one `set_server_channels` to a form field in
`dashboard/app.js`. `#playtest`, the eighth channel, got a test naming itself nine times, which
proves that channel is wired and says nothing about the next one.
`test_every_base_channel_is_registered.py` walks `BASE_CHANNEL_SPECS` instead, so the tenth channel
cannot be half-wired.

**The tenth place is the one this release found by being the ninth channel.** `#updates` shipped in
the first draft created, locked, bound — and **blank**, because `DEFAULT_CHANNEL_MESSAGES` had no
entry for it and `resolve_channel_message_content` answers `""` for a key it does not carry. That
answer is correct: it is also how a GM turns a message off, which is the one distinction v0.33.1 went
to trouble to preserve. So a channel nobody wrote a blurb for is indistinguishable from one somebody
deliberately emptied, nothing errors, and nothing is posted. Two channels are exempt because
something else fills them — `#xianxia-info` gets the guide view, `#playtest` its own board — and each
says which, in the gate.

**`#event-scenes` is retired**, and this is the one place the release removes something. rc.52 split
an event's *announcement* per world and left its *scene* hanging in a shared channel, so one event
used two channels for no reason anybody could state. `event_scene_parent` is the one door:
the world's own feed, which `world_event_channel` already falls back to the global feed for, so the
worst case is the channel the announcement was going to anyway. It leaves `BASE_CHANNEL_SPECS`, so
nothing creates or requires one — and **stays in `_base_channel_bindings`**, because teardown builds
its targets from that dict and a server that already has the channel must still be able to lose it.
That is the same rule as the retired category, one level down. The cost is stated rather than
discovered: the per-world feeds are gated by the realm **access** role, so a scene in a world a
player has not reached is now invisible to them, which is in `docs/TODO.md`.

**The gates, and what each drill prints.** Deleting the base re-parent prints
`ensure_base_xianxia_channels: compares=False moves=False`; restoring create-only read-only prints
*"so a channel the bot did not create is never made read-only"*; removing `SERVER_BASE_CATEGORY`
from the teardown tuple fails **two** gates at once; dropping `updates_channel_id` from
`clear_discord_bindings` prints *"updates (updates_channel_id): clear_discord_bindings does not
name it"*; and the three release-notes rules each fail with their own sentence — *"a restart
announced the same release twice"*, a fresh install that posted, and *"the marker advanced with
nowhere to post"*.

**Three of the drills caught the gates rather than the code, and they are one lesson.** The
base-channel registration gate first sliced its blocks on indentation from a header string — and a
multi-line `def` defeats that, because the closing `) -> None:` sits at the function's own indent,
so every block ended one line in and every check passed vacuously. It reads functions by AST now.
Then its self-check asserted the persist-block reader had found `updates_channel_id` — which
`_base_channel_bindings` also contains, so pointing the reader at the wrong function *still passed*.
It asserts `_bound_id(` instead, a string only the right function can hold.

**And the third is the plainest one in the release.** `test_release_notes.py`'s own docstring said
it held *"the three rules that keep it from being annoying"*, and it tested the changelog parser and
the Discord chunker and **never called `announce_release_if_new` at all** — so deleting the marker
write, which is the one line that makes the announcement happen once, left the suite green.
Idempotence had been asserted in prose, in two places, and driven in none: exactly the shape of the
thing this release exists to fix, written into the gate written for it, and only the drill said so.
Six tests drive the function now, against a fake `DB` and a fake channel, and the drill fails on the
sentence the rule is written in. A reader is asserted before it is trusted (rc.57), and a gate that
cannot see the thing it forbids is decoration (rc.47) — and only running it against the broken tree
says which kind you have.

### The partner who was nobody (`clan_diplomacy.go`, v1.0.1)

`martial_clan_relations.partner_family_id` is foreign-keyed to `birth_families` and nullable, and the
one statement that had ever inserted a row wrote it `nil`. Not as an oversight — because the partner
it named was **invented**: `clanPartnerSurnames[roll] + " Martial Clan"`, a house with no members, no
town, no wealth and no opinion about anything. So each household held exactly one relation, with
somebody who does not exist, seeded once behind a `COUNT(*)==0` guard, and the only thing that could
ever happen to it was the ±1 a tick that `clans` applies. **Four households meant four relations on
the day the world opened and four relations for ever after**, which is how it was reported.

The whole of what could create a row at runtime was `combat_aftermath.go:172`, and it writes
`blood_feud` only, when a player kills a family head. So in every world that has ever run, **no
alliance, marriage pact or trade pact has been formed since bootstrap.**

**The comment naming the fault is in the tree, and it fixed the neighbouring table.**
`npc_romance.go:88` says *"Bootstrap has always written clan `marriage_pact` rows and nothing has
ever made one since, so the idea existed in this world and only ever described its own past"* — and
`npcPoliticalMarriages`, directly beneath it, writes **`sect_relations`**, as an `UPDATE`, re-typing
a pair bootstrap already wrote. That half has worked since rc.24. The clan half it is named after was
never built. Same shape as `/learn` (rc.43) and the peach (rc.50), reached from the other side: not
a mechanism nothing points at, but a fault someone wrote down and then fixed one table over.

**The gate is the world, not the street, and that is the decision worth knowing.** Every one of the
thirteen archetypes has exactly one city per world (`birthFamilyHomelands`), so a same-location rule
would let a house treat only with houses of its own archetype, and a one-road-step rule would hand
each archetype a fixed set of partners it could never grow out of — rc.24's geography fault in a new
hat, where 392 people could never marry anybody because of where they stood. A clan does not walk
anywhere; it sends somebody, and the roads between two cities of one world already exist. So the
world is the gate and being within one step is a **bonus to the roll** (`clanNeighbourBonus`), read
through the runner's own `neighbours` wrapper so there is still one idea of reach in the tree.

Four more rules hold it.

- **Written from both sides or not at all.** Every reader is `WHERE family_id=?` — `/family clan`,
  `world_status_queries.go`, the standing term in `family.support` — so a single row is a treaty one
  of the two houses has never heard of.
- **A blood feud is not signed here.** It comes from a body, and `combat_aftermath` owns it.
  `TestABloodFeudIsNotSignedHere` walks the whole plausible input space rather than trusting the
  `switch` reads right.
- **A missing opening score is a refusal, not a zero.** `clanRelationOpeningScore[relation]` answers
  0 for a key it does not carry, and 0 is not a sentinel here: `family.support` counts
  `relation_score>0`, and nothing in the game re-types a row, so a treaty opened at 0 is worthless
  for ever. The `seller_user_id=0` lesson. Bootstrap reads the same map, so an ancestral pact and a
  fresh one cannot silently be worth different amounts; what tells them apart is
  `started_game_minute`, the only thing about a relation's age the table has ever carried.
- **The invented partners are left where they are**, and so is `recordClanRelationHistory` — renamed
  from `recordClanBootstrapHistory`, because a helper named for the one caller it happened to have is
  the `sync_world_catalog` lie again. On a world with one household the invented partner is the only
  relation there can be, which is also why a lone house signing nothing is a test rather than a bug.

**`trade_pact` was in the `ELSE`.** The drift `CASE` named `alliance`, `marriage_pact`, `blood_feud`
and `rivalry` — four of the five seeded types — so a trade pact sat at exactly its opening 25 from
the day the world started. One word, and the test that holds it fails with *"a trade pact is worth
25 after a tick, want 26 — it is in the ELSE again"*.

**The fixture is the reason this is a new test file.** `bootstrap_test.go` declares
`martial_clan_relations` with **no foreign keys at all** and a `birth_families` missing every column
diplomacy reads, so it accepts exactly what production refuses — the `npc_consignments` lesson, in
the file next door. `clanDiplomacySchema` carries both keys, and `storage.Open` sets
`foreign_keys=ON`, so a partner id pointing at nobody is refused by SQLite rather than stored.

Nothing ends a relation, deliberately: `active` is written 1 by every INSERT, read by every SELECT
and set to 0 by nothing at all, and what a broken alliance leaves behind — a rivalry, or simply
nothing — is a decision rather than a default.

### The pass the next release would have spent (v1.0.1)

`docs/playtest/v<version>.md` is named after `RELEASE_VERSION`, which strips the `-rc.N` suffix — so
across all **fifty-nine** release candidates of 1.0.0 the filename never changed, `merge_ticks` was
handed the file it was about to overwrite, and every tick was carried. v1.0.0 → v1.0.1 is the first
bump in this project's history that renames it, and there `target.exists()` is false, `old` is the
empty string, and the freshly generated checklist is written with every box blank. **The first real
live pass this repo has ever had would have been deleted by the release that followed it**, silently,
by the generator, on a tree where everything was green.

It is the shape rc.47 already found in the same function — `merge_ticks` carefully preserving the
per-action boxes and dropping the loop rows — one level up: not the wrong rows carried, but the
right rows carried only while the filename happens to hold still. Nobody had met it in twelve
regenerations because nothing had ever been ticked, and nobody met it in twelve more because the
name could not change inside a version.

`_superseded` is the newest other checklist in the directory, and `main` inherits its ticks when the
target does not exist yet. The superseded file is then removed: `docs/playtest/` is one checklist,
its only human content is the ticks, and those have just been carried — what would be left behind is
a generated copy of an older tree, which git already keeps.

**A carried tick is dated, and that is the half that keeps it honest.** Moving `[x]` forward
unstamped would say the new release was walked when it was not, which is the one thing a checklist
must not do. So the **person ticks and the generator dates it**: a bare `[x]` belongs to the release
being written, a stamped one keeps the release it already names. `[x] v1.0.0` on a 1.0.1 checklist
is a row nobody has walked since 1.0.0, and re-walking it is writing `[x]` over the stamp — one
character, no version to type.

**Versions sort as integers**, because `v1.0.10` is newer than `v1.0.9` and sorts before it as text.

Three things about the gate are worth more than the fix.

- **`main` takes its `argv` now, so the test can drive `main` rather than its helpers.** Asserting
  that `merge_ticks` *can* carry a tick says nothing about whether `main` ever hands it the previous
  release's file, and that wire is precisely what was missing. This is rc.59's `test_release_notes.py`
  lesson applied before the fact rather than after it.
- **The drill found a fault in the fix.** `_stamp`'s first version asked whether the cell contained a
  space to decide "already stamped" — and `[ ]` contains a space, so an empty box was left alone: the
  right answer for the wrong reason. Disabling the empty-box branch changed nothing and the drill
  **passed**, which is the rc.47 shape inside the gate written for it. It reads the text *after* the
  box now, and the re-drill fails with *"an unwalked row was dated as though it had been"*.
- **The other four drills print the finding.** Making `main` ignore the superseded file gives
  *"the live pass was blanked by the release that followed it"*; leaving the file behind gives
  `['v1.0.0.md', 'v1.0.1.md'] != ['v1.0.1.md']`; sorting the versions as text inherits from
  `v1.0.9` over `v1.0.10`.

### The first bump that renames a file (v1.0.1)

`docs/playtest/v<version>.md` is named after the stamped release, and across all **fifty-nine**
release candidates of 1.0.0 that name never moved — `RELEASE_VERSION` strips the `-rc.N` suffix. So
1.0.0 → 1.0.1 is the first bump in this project's history that renames it, and **two** things had
quietly depended on the name holding still:

- `merge_ticks`, handed the file it was about to overwrite, so a rename meant `target.exists()` was
  false and the freshly generated checklist was written with every box blank. That one was found and
  fixed while building the tick-carrying, *before* the bump — and it worked on the day: the bump
  printed *"carried the live pass from v1.0.0.md and removed it"*, all 38 ticks preserved and stamped
  `[x] v1.0.0`.
- `test_hidden_actions.py`, which opened `v1.0.0.md` **by literal** and made the whole module error
  with `FileNotFoundError` the moment the bump landed. Nothing found that one in advance, because
  there was nothing to find until the filename actually moved.

Two instances is a class, so the gate exists now: outside the generator and the gate file's own
throwaway trees, no Python source may spell a checklist filename — build it from `VERSION`.

**Its first run flagged the file it had just been written for**, because the *comment* explaining
the fix names the old filename. That is rc.52's rule (*a gate that cannot tell prose from code is
decoration*) arriving immediately rather than a release later, and the scan blanks comments and
docstrings before it looks. The pattern is stated **once** and used by both the scan and the
self-check, because a second copy in the self-check would be free to stay right while the one that
matters drifted — which is the exact failure this file catches elsewhere. Three drills: the literal
restored in code, the pattern broken (the self-check fires first), and the comment-stripping removed
(it flags `test_hidden_actions.py` again, by its comment).

**The version itself is stamped in seven places**, and `test_release_version.py` has held them equal
since the day `VERSION` drifted to 0.19 while `app/version.py`, the Dockerfile and compose stayed on
0.18. A bump is `app/version.py`, `VERSION`, the `Dockerfile` label, `docker-compose.yml`, the README
title and **both** of `VERSIONS.md`'s stamps — the `## Release status — v…` heading *and* the
`- Current release: v…` line under it, which are two separate assertions — plus the one literal in
that test, which is the only place the number is spelled out in the suite and is what makes the rest
reviewable. (v1.0.8's own bump said six and missed the `Current release:` line, which is what the
test then caught: a count written down once and never re-counted drifts exactly like any other
restated fact.)

### What a server is told is derived, so the entry has to parse (v1.0.1)

rc.59 made the bot post its own release notes into `#updates`, and made the line **derived, never
authored twice** — `release_headline` takes the opening sentence of the entry `release_notes_for`
found, precisely so a second short blurb per release cannot drift from the changelog. That puts two
properties of `VERSIONS.md` on the critical path, and **nothing was holding either**. v1.0.1 broke
both at once, and only rendering the post by hand showed it.

**An entry is one header.** `_ENTRY` matches any line opening with a version stamp, so a second
`**1.0.1**` *inside* the entry starts a new one. Three paragraphs of this release were written that
way, and `release_notes_for("1.0.1")` therefore stopped at the first — the notes truncated to one
paragraph and everything after it would never have been announced anywhere. The convention it broke
is visible in every earlier entry and was written down nowhere: continuation paragraphs begin
*"It also…"* or *"And…"*.

**The first paragraph leads.** Because the headline is the entry's opening sentence, paragraph order
decides what a server is told. Prepending the newest work put a *test assertion* first, so the live
post read:

> 📣 **Xianxia RP v1.0.1** *also fixes an assertion that was only ever green by luck.*

— opening mid-thought, about the least player-facing thing in the release, with the two things
players actually got unmentioned. It reads correctly now (*"makes a craft say what it needs, and
lets a player start over without a GM"*), which is what the channel is for.

Neither fault is visible from a source read of the bot, and neither shows in any suite: the
changelog is prose, and the only thing ever held about it was that the stamped version has an entry
at all. `test_release_headline.py` holds both — no version may open two entries, and no entry may
open with *also/and/too* — plus that every headline is a sentence that fits one Discord message. It
reads `_ENTRY` and `MESSAGE_LIMIT` off `release_notes.py` rather than copying them, and asserts the
parse found a real changelog before trusting it. Its drills print the truncation, the *"v1.0.1
also fixes…"* line verbatim, and *"the reader is broken, not the tree"*.

**The lesson is the one rc.59 already stated about itself, arriving from the other side.** That
release wrote that a derived headline cannot drift from the changelog — true, and the reason it is
right. What it did not say is that deriving it makes the changelog's *shape* load-bearing, so prose
nobody thought of as code now needs a gate like any other.

### The step that only passed when the number was not zero (v1.0.1)

`scripts/playtest_engine.py` went red on a step nothing in the release had touched:

```
FAIL the crossing carries the player and converts the purse at the ladder
     24200 low_spirit_stone -> 242 low_spirit_crystal at 100:1, -1 left behind
```

24,200 divides by a hundred exactly, so the remainder is **0** - and the step read it as
`int(exchange.get("remainder") or -1)`. Zero is falsy, so `or` replaced the correct answer with the
missing-value sentinel, and the step then failed its own `0 <= remainder < rate` check. It had passed
every previous run because the accumulated wealth had never landed on a round number.

That is **"a fallback that looks like a value is not a sentinel"** - the `seller_user_id=0` lesson
(rc.28), `gradeIndex` (rc.55) and `clanRelationOpeningScore` (v1.0.1) - met inside an *assertion*
rather than inside a rule. The failure mode is different and worse: a rule that mistakes 0 for
missing is wrong every time and gets found, while an assertion that does it is **green until the
value happens to be zero**, which makes it read as a flake.

Three more instances were waiting in the same file, and one of them is on a case the design
explicitly promises: `int(exchange.get("converted") or -1)` compares against the sheet's mirror, and
`crossWorldsPurseTx` is documented to write the credit **even when it converts to zero**, because
that write is what re-points the mirror at the new world. A purse smaller than the ladder's rung
would have failed that step every time.

`present(value, default=-1)` is the one statement now - `default if value is None else int(value)` -
and all four sites use it. The rule it encodes is the one this file already states about production
code, applied to the tests: **ask whether the field is absent, never whether it is falsy.**

### A method that could not say what it needed (v1.0.1)

**Found by playing, not by reading.** A player bought an Inscription slip, read it, and then had no
way to discover that a Swift-Wind Talisman wants one `talisman_paper` and one `spirit_ink`. Both are
in the content file, both are sold (67 shops and 23), and both are foragable since rc.21. Three
separate facts would each have told them, and **not one reached a player**:

1. **`character_recipes` had no Python reader at all.** Four writers - a bought slip, the household
   lesson, the trade examination and a grandfathering migration - and the only readers in the tree
   are in Go, deciding whether a craft is allowed. Nothing could tell you what you had learned.
2. **`get_recipe_definition` parses a recipe's `cost` and no command read it.** It is decoded out of
   `cost_json` in `core.py` and every caller reads `profession` beside it and stops. That is rc.55's
   root grade exactly - a field parsed and read by nothing - except this one is the difference
   between being able to use the crafting system and not.
3. **The engine computed the shortfall and threw it away.** `consumeInventoryTx` returns
   `map[item]shortfall` - which materials are short and by how much - and the craft refused with
   `errors.New("missing materials")`. The bot then caught that and replaced even those two words
   with *"Missing materials for that recipe."* **Two layers each discarding the one fact the player
   needed.**

All three are fixed, and none of it is content: `describeMaterials` names the shortfall through
`itemDisplayName` (the one statement of what an item is called, already in `shop_actions.go`) off
sorted ids, because a map range would make one refusal read two ways; `DB.get_known_recipes` is the
missing reader; and `profession status` prints every method you know, per trade, with each input's
cost beside **what you are carrying** - `✅` you can make it now, `❌` short of materials, `🔴` your
rank is too low. It also stopped returning early on an empty `profession_progress`, which had meant a
cultivator who bought a slip and read it was told they had no profession experience and shown nothing
they had learned.

**The other three callers of `consumeInventoryTx` were already right**, which is worth recording
because it says the fault was the craft's and not the helper's: the slip, the beast food and the shop
sale each name the one item they wanted. And all four *trades* share `craft.resolve`, so Alchemy,
Forging, Formation and Inscription are one fix, not four.

**And the new reader raised on every call, which the playtest found and no gate could.** The first
`get_known_recipes` did `dict(row)` without setting `db.row_factory`, so a row came back a bare tuple,
`dict` walked the first string instead, and the page died with *"dictionary update sequence element
#0 has length 19"* — 19 being the length of `Swift-Wind Talisman`. `test_authority_boundary` requires
a state function to have a production caller and it had one; the gate below requires the page to call
it and it did. **Neither asks whether the call works**, and nothing else executed it, so a leaf that
had passed for as long as it existed went red in the Discord sweep. `test_known_recipes.py` calls it
against a real database, and its drill prints the production error.

`test_a_recipe_tells_you_what_it_needs.py` is the gate, behavioural where it can be and reading
functions **by AST** (rc.59: a multi-line `def` defeats an indentation slice). Its drills each print
the finding - the bare refusal restored, the handler replacing it again, the status page no longer
reading what you know - and the fourth breaks the reader itself and fails with *"the reader is
broken, not the tree"* before it can make anything vacuous.

**Its own first version shipped a worse copy of an existing rule, and its first run said so.** It
swept `sells`, `forage_materials` and the recipe outputs to prove every cost was obtainable, and
failed naming `jade_life_herb` and `twin_extremes_fruit` - both of which *are* sourced, one a
secret-realm room reward and one resolved off the world tier in `crafting_actions.go`. That rule is
already stated in `test_every_item_has_a_source.py`, which greps production for every item id and
keeps `SOURCELESS_ITEMS` empty. Two statements of one rule are free to disagree and the weaker one
produces the false findings, so the sweep was deleted and the test now holds that the real gate is
still there - the same call v1.0.1 made about its table-level sweep. **v1.0.15 found the fruit was
not a false finding**: it is foraged only from the Spiritual World up, and the one method needing it
was sold only in the Mortal World. Deleting the world-flat sweep was right; dismissing what it named
was not. See "A method can be made where it is sold" below.

### Starting over without a GM (`character_reset.go`, v1.0.1)

There was no way for a player to reset a character, and the three things that looked like one were
not. `/begin` refuses outright when a `characters` row exists. **Dying is not a reset**:
`lifecycle.true_death` has exactly three callers and none is voluntary - old age fired automatically
inside `require_character`, losing a battle at 0 HP with no fate point left, and the GM - and what it
opens is Samsara, which deliberately carries the memory seed, the talent/law/insight echoes, the
legacy points, the craft echo and a family lineage rolled off the dead life's karma. And the one true
wipe, `admin.player.erase`, is the data-protection lever: **using a legal-erasure tool as a restart
button is the same class of lie as a `sync_world_catalog` that syncs no catalogue** - an action's name
is what it is for, and "somebody asked for their data back" is not "I picked the wrong path".

`character.reset` is the restart button. It is allowlisted through `applyAuthoritative`, so the
moderation, maintenance and seclusion gates apply to it for free, and it takes **no payload** - every
input it has is a row the engine already owns.

- **It reuses erasure's own sweep.** `applyErasureTargets` is the one statement of how a person's
  rows are removed, walking the targets `erasureTargets` reads off the *live schema*. Writing a
  second list of tables here would be the hand-written list that function exists to avoid, one file
  over.
- **The gate is the anonymise disposition, not a clock.** A time window says nothing about what the
  reset would cost anybody else. The question that matters is whether this character has left a mark
  on a world other players share, and erasure had already answered it: `erasureAnonymise` is
  precisely the set of columns where a person's id sits on a row belonging to everybody. A reset is
  refused the moment any of them names the character, because **those rows survive an erasure and so
  cannot honestly survive a reset** - the world would go on referring to a cultivator who was never
  there. The refusal names which. **v1.0.14 reversed this, on the owner's call** - see "Nothing the
  world keeps stops a reset" below.
- **Three per account, ever** - not three per character and not three per life. `rollRootGrade` is
  `Intn(1000)` against thresholds putting Immortal in the top 0.7% of a tier-1 household's draw, and
  rc.55 is what made that grade worth 0.88x-1.34x cultivation and -1 to +3 on every breakthrough for
  the character's whole life, so an unbounded reset is a free re-roll of exactly that number. The
  count is `event_log` rows the sweep is told to keep - because **a bound that the bounded action
  erases is not a bound**, which is rc.48's rule turned inward. The row is the memory, as
  `(user_id, quest_key)` is for the beginner path.
- **A reset is not a small samsara, and the difference is the whole point of having both.** Samsara
  is what **death** opens, and it deliberately *remembers*: the memory seed, the talent, law and
  insight echoes, the legacy points, the craft echo and a family lineage rolled off the dead life's
  karma all ride into the next life. A reset keeps **none** of it - `soul_legacy` carries no
  anonymise disposition and no keep, so the sweep deletes it with everything else and the account
  begins again at incarnation 1 with nothing behind it. That property is real today but **invisible**:
  it holds only because nobody has added a keep to that table, and a keep added later would quietly
  turn a reset into a cut-price samsara with no test going red. `TestAResetIsNotASmallSamsara` is the
  test that goes red; its drill adds exactly that keep and prints *"a reset left 1 soul_legacy row(s);
  that is samsara's memory, and a reset keeps none of it"*.
- **"Keep what you drew, change what you chose" was rejected on a fact, not on taste.**
  `rollFamilyRoot` weights the root off the household's archetype, location, bloodline affinity and
  tier, and `rollRootGrade` adds `(familyTier-1)*24`. The family is a choice and the draw depends on
  it, so there is no line to draw there.
- **The household's welcome line comes back out.** Creation appends one to a *shared* starter
  household's `history_json`; without this a house would remember a cultivator who does not exist,
  once per abandoned attempt. `householdWelcomeLine` is a function because two places need the exact
  same sentence and a second copy of a format string is free to drift. A line that is not found is
  not an error.

**The finding is one step further in, and no source read would have produced it.** A reset is the
first action in this tree whose **actor erases itself**. `applyAuthoritative` reads the actor's state
version *before* the switch and calls `eventledger.AdvanceActorVersion` with it *after*, so a sweep
that deleted `authoritative_actor_versions.actor_id` left the framework unable to record the action
that had just succeeded: `stale expected_version: expected 2 current 0`, thrown after all the work
was done. `admin.player.erase` never met it because an `admin.*` lever falls through to the switch in
`ApplyWithWorld` rather than through this bookkeeping, and because it erases somebody other than the
actor. The version row and the action receipts are kept out of the sweep for exactly `erasureKeep`'s
reason one level down - **the engine's record of a request cannot be the thing the request deletes** -
and they are also right on their own terms: the version is optimistic-concurrency state about a
Discord account's in-flight requests, not about the character, and zeroing it would let a client
still holding the old version win a race it should lose. `domain_events` is deliberately *not* kept:
it is the ledger of what the character did, which is the thing a reset is for.

A GM erasure still takes the reset rows with everything else, and that is right: **erasure removes a
person, a reset removes a character**, and somebody who has been erased is new to this bot.

**Two of the gates caught themselves, and both are the recurring shape.** The mark refusal first
asserted only that the error *named* the marked table - and disabling the disposition check makes
every target a mark, so a refusal listing all sixteen still contained it and the drill **passed**
against a broken gate (rc.47's shape, rc.49's disabled condition). It parses the refusal's
parenthesised list and requires exactly one entry now. And the test helper that staged those marks
wrote them with `conn.Execute` and then `Close`d - so every row was rolled back by the implicit
transaction, and all six refusal tests passed as *successes*. That is rc.38's `npcFound` finding met
in a fixture, where it made the tests vacuous rather than the feature broken.

`tests/python/contracts/test_character_reset.py` is the real-schema half, for
`test_privacy_erasure.py`'s reason: the Go fixtures build a schema by hand and would carry an old
table name for ever. A migration renaming `event_log` breaks no build, errors nowhere, fails no Go
test - and silently unbounds the allowance. The contract holds every kept "table.column" against a
real bootstrap, holds each to being a column the sweep *would* otherwise delete (a keep on a column
erasure never touches is decoration), and holds the table the allowance is counted from to be one the
reset keeps.

### Nothing the world keeps stops a reset (v1.0.14)

Reported from play, as the refusal itself: *"Xie Kormaq has already left a mark the world keeps
(world_history_events.related_user_id)"*. v1.0.1 made every `erasureAnonymise` column a mark, and
`world_history_events.related_user_id` is written by nearly everything a new cultivator does - the
first discovery, an inn trade, a world event their explore set off - so the way to start over stopped
working minutes into a life. It was reported as a bug in the message rather than the rule because the
message named a column and gave no way to tell a rule from a wait.

**`characterResetReleased` is the one list of anonymise columns a reset settles instead of refusing
over**, each with what the thing is called in the reply, and on the owner's call it holds all six.
`characterResetReleaseTx` does the settling: a private history row (`participant`, `hidden`) goes with
the life; `characterResetForgetNameTx` rewrites the character's name out of every kept history row
and out of a gate named after them (`"{character}'s Ascension Gate"`, which other cultivators' rows
quote, matched on the whole gate name); a player family goes through `playerFamilyDepartTx`, the rule
a founder walking out already had - the most senior who stays, or dissolved if nobody does; and every
other released column is unlinked. An anonymise column **not** on the list still refuses, so a new
one added to erasure is a mark until somebody decides what a reset does with it, and the refusal
(`characterResetMarkRefusal`) says so in words and that **there is no timer** - the gate was never a
clock.

**The release runs before the sweep, and the order is the finding.** `erasureTargets` sorts tables
alphabetically, so `characters` is deleted first, and with `foreign_keys=ON` that delete fires
`player_families`' ON DELETE CASCADE and the SET NULLs on the rest. Released afterwards, a founder's
whole house - other players' memberships included - was already gone. The reset's fixture declared
none of those foreign keys and would have passed either way (the `npc_consignments` lesson again); it
carries production's now, and `TestAFounderWhoResetsIsSucceeded`'s drill - move the release after the
sweep - prints *"the house did not pass to its most senior remaining member (0 rows)"*.

**`admin.player.erase` had the same cascade and nobody had seen it**, because its fixture carries no
foreign keys either: `erasureAnonymise` says a family *"outlives whoever founded it (NOT NULL, so it
takes the sentinel)"*, and in production the anonymise UPDATE never met a row, because the cascade had
already deleted it. It calls `playerFamilyDepartTx` before its sweep now, and
`TestErasingAFounderLeavesTheHouseToItsHeir` builds its own fixture with the real keys; its drill
prints *"(0 rows) - the cascade took it"*.

### The fields nothing reads (`field_readers_test.go`, v1.0.1)

rc.55 found `RootGrade.CultivationMult` and `RootGrade.BreakthroughBonus` parsed out of the content
file and read by nothing — the grade decided everything about how a cultivator was made and nothing
about what they were. **It was found by hand.** rc.58 then built exactly this gate one level down,
for modifier *stats*, and nobody built it for the fields themselves, so the class went on producing
findings by inspection: 439 parsed fields across 63 structs, **seven** read by no selector anywhere
in production Go.

Four are read by Python for display, and that distinction is the reason a naive gate would be noise:
Python reads `content/world.json` directly through `WORLD`, so the *content* is live even where the
Go struct field parsed from it is not. `fieldsReadByPresentation` names each with the file that
prints it, and the entry is worth reading as what it is — a Go field that could be deleted without
changing any behaviour. The three that survive are `Path.Skill` and the two auction-house door
fields, each an open decision in `docs/TODO.md` rather than a shrug.
`unreadContentFields` is therefore **not empty on the day it was written**, which is rc.58's
`REFUSAL_ONLY_OPERATIONS` precedent: tightening a rule nothing held reveals the backlog that the
absence of the rule created.

**A read is an `*ast.SelectorExpr`, and a composite-literal key deliberately is not.**
`Path{Skill: "Sword"}` *writes* the field; whether any rule reads it back is the whole question
rc.55 turned on, and a substring or identifier scan cannot tell the two apart — rc.52's "by AST, not
by substring", one level down, exactly as rc.58 needed for `sense_precision_bonus`.

**It is a floor, not a proof, and says so.** Without `go/types` it cannot tell
`PhysiqueDefinition.Name` from the forty other structs carrying a `Name`, so a field sharing its
name with one anything reads passes unexamined. That makes it honest in one direction only: it never
calls a read field unread, and it catches the uniquely-named orphan — which is what every finding of
this class has been, rc.55's included. `TestATestThatAssertsARollLandedLendsTheDice` already
describes itself as a shape detector for the same reason.

**Five drills, and two of them are about the gate rather than the tree.** Dropping `Path.Skill` from
the allowlist prints `1 content field(s) are parsed and read by no rule: Path.Skill (catalog.go)`;
dropping a presentation entry prints the same for `PhysiqueDefinition.Drawback`; and disabling the
selector collection prints *"the production walk did not find "Grade" read anywhere; the sweep is
broken, not the tree"* — the rc.57 rule, a reader asserted before it is trusted, firing **before**
the assertion it would have made vacuous.

The fourth drill caught itself. It added `Path.Name` to the allowlist expecting *"production Go
reads .Name; drop the entry"*, and got *"worlddata no longer parses it"* — because `Path` has no
`Name` field, so the drill proved the neighbouring branch and looked like it had worked. Redone with
`RootGrade.CultivationMult`, it prints the right sentence — and that field is the one rc.55 found by
hand, so the drill's own output is the proof that this gate would have caught it.

**A table-level sweep was run beside this one and is deliberately not recorded.** Its regex missed
`INSERT OR IGNORE`, dynamically named tables (`table = "body_realm_perfection"`) and the
read-by-`RowsAffected` idiom, so all 27 candidates were false positives — one of them a table called
`does`, from the words *"CREATE TABLE IF NOT EXISTS does not add"* inside a comment. A sweep whose
every hit needs hand-checking is not a gate, and shipping it as one would be the decoration this
file spends its length naming.

### The reply that raised after the cost was paid (v1.0.3)

Reported from live play as **"it doesn't let you craft but also takes your items"** - by a player
whose bag held six of the pills they had been told they never made.

`craftResolveAction` built its result by hand and shipped the flattened `d1`/`d2`, no `degree` and
no `roll` map; `roll_line` in the bot reads `die1`, `die2` and `degree`, and `_run_crafting` passed
it `SimpleNamespace(**resolved)`, the whole result. So **every craft that got past the materials
check raised `AttributeError: no attribute 'die1'`** - and it raised on the *reply*, which is after
`applyAuthoritative` has committed. The materials were spent, the output granted, the profession XP
credited, the examination offered, and the player was shown one of the three wiring-failure strings
and told nothing had happened.

**v1.0.1 found and fixed exactly this for `forageResolveAction`, in the same file, forty lines
below.** Its own note says the forage result *"flattened `d1`/`d2` and dropped the degree while
`roll_line` reads `die1`/`die2`/`degree`"* and that the result now carries the roll map whole. The
craft above it kept the fault for two more releases.

**Neither harness could see it, and the reason is the seam.** The engine half drives `craft.resolve`
and asserts on the result *map*, never rendering a reply. `scripts/playtest_discord.py` names
`craft` nowhere, so `/craft` is reached only by the generic leaf sweep, which fills the recipe modal
with a canned value and gets the **designed** "missing materials" refusal - which the coverage gate
accepts, correctly. Each half proved its own side and the key names between them were proved by
neither. That is why the forage version *was* caught by the sweep and this one was not: a forage
leaf can succeed with nothing in the bags and a craft cannot.

**There are two right ways to ship a roll and craft did neither.** An action may merge the roll map
into its result (`for k, v := range roll`, which `beastTameAction` and `pvpActAction` do) or carry
it nested under `"roll"` (`forageResolveAction`, and now the craft). `TestAResultThatReportsARollReportsTheWholeRoll`
is the rule, stated once, in Go: a result carrying `"d1"` must carry `"roll"` beside it.

**The Python gate had to be narrowed to stay true, and its own first run said so.** It began as a
sweep over every `roll_line` call site forbidding the whole result as an argument, and reported
three findings that were not findings - `beast_tame` and `duel_act`, which merge the roll and are
correct. Two statements of one rule are free to disagree and the weaker one produces the false
findings, which is the call v1.0.1 made about its own table-level sweep; the sweep is gone and what
is left is the regression and the reader it depends on. Its drills print the finding, the reader
failing first, and - for the Go half - `crafting_actions.go:550` by position.

### What losing a fight leaves you (v1.0.3)

`fatalChance` is `min(75, 8+gap*3)`, so against a same-realm opponent a defeat is fatal eight times
in a hundred. The other ninety-two took the non-fatal branch, which applied a `flesh_wound` and
wrote no vitality at all - leaving `characters.vitality` on whatever `MAX(0, vitality-dmg)` had
reached, which at the end of a losing fight is **0**. Meanwhile the two *fate-rescue* branches - the
rarer and strictly worse outcome, where the blow killed you and a fate point was burned to undo it -
each wrote `vitality=1` outright. Four copies of "you lost and lived", and two of them disagreed
with the other two about whether surviving means having a heartbeat.

**Nothing in this tree regenerates vitality with time.** Twelve `SET vitality` statements in
`go_core`, four of them damage, and not one keyed on rest, cultivation, seclusion or the tick: four
pills and one technique are the whole of it. So zero was not a state anybody waited their way out
of, while `/battle` printed *"You survive but are incapacitated"* over it - a word the engine never
enforced anywhere.

`survivedDefeatTx` is the one door, and it uses `MAX` rather than `=` because it is a floor under a
survivor, not a number to be set.

**And the treatment spent the medicine.** `recovery_pill` is the named treatment for both combat
injuries *and* the cheapest thing in the game carrying `use.instant.vitality_restore` - and
`condition.treat` consumed it for the roll and restored nothing, while `item.use` restored 8 and
cleared no condition. One pill did one of two jobs and a player needed two to get back where they
started, at the end of the losing fight that had just put them on zero. The treatment does what the
treatment item does now, applied whether or not the roll landed, because the pill was swallowed
either way. Only `recovery_pill` carries an instant restore, so this reaches exactly the two
conditions that leave a cultivator at zero and `jade_life_herb`, `heart_calming_pill` and
`purging_phoenix_pill` are untouched by construction.

**The behavioural test passed against the broken tree, and its drill is what said so.**
`TestSurvivingADefeatLeavesAHeartbeat` calls `survivedDefeatTx` directly, so restoring the bare
`applyCombatCondition` at a call site left it green: it proves the helper works and says nothing
about whether the four branches use it - which is the whole fault. `TestEveryDefeatBranchGoesThroughTheDoor`
is the missing half, reading the call sites by AST and keyed on the `sourceType` argument
(`"battle"` or `"fate_rescue"`), and it names both sites when either is reverted. That is v1.0.1's
`test_release_notes.py` lesson arriving one release later in Go.

**What is deliberately not built is rest.** That the game has no passive vitality recovery at all is
a mechanic rather than a wiring, and inventing one unasked is the thing this file exists to refuse.
It is in `docs/TODO.md` with that reason.

### The seventh path (v1.0.3)

`content/world.json` offers **seven** cultivation paths. `app/rules/advanced_catalog.py` line 5
names **six**, and `path = PATHS[i % len(PATHS)]` builds the whole generated catalogue off it - so
of 160 manuals, **none** named the Ghost Cultivator. Not one. The proof is arithmetic: all 142
generated ids satisfy `((n-1) % 6)` against that six-tuple with zero mismatches.

Meanwhile `death_qi_system` opens with `"path": "Ghost Cultivator"`, names two of the thirteen
households as its own, and carries ground multipliers, hour multipliers, a corruption ladder and six
ghost forms - served by `death_qi.go`, three hundred lines with its own harvest action. **The
deepest path-specific subsystem in the game belonged to the one path that could not practise a
method.** A hardcoded copy of a content vocabulary, missing a member, which is the class rc.39 and
rc.44 each removed for a different vocabulary.

**The tuple is pinned rather than widened, and that is the decision worth knowing.** A generated
manual id embeds its path name (`advanced_demonic_007_sword_cultivator`), so adding a seventh entry
changes the modulus and re-points **every id in the catalogue at once** - dangling every
`character_manuals` row, every inventory item and every stored `cultivation_manual:<user_id>` choice
a live character holds. `_uncovered_paths` asks the content file which paths exist and appends the
ones the cycle never reached, in their own id space, where the slug makes a collision impossible.
Every number it uses is read off what the catalogue already gives the six: how many manuals, how
many techniques each, and the realm floors, which are the element-wise minimum of the covered paths'
ladders - the gentlest ladder already on offer, so the floor starts at 0.

**And the hidden sect said nothing when it had nothing.** `shadowInitiationManual` filters on
alignment *and* path *and* realm, and `shadowAction` then omitted `manual_id` from its result while
`sect.py`'s `if initiation.get("manual_name")` dropped its line - so an initiate walked through a
-200 karma gate and was never told why no inheritance came with it. It reports `manual_absent` now
and the cell says so.

**The fallback this release first added was wrong, and CI is what said so.** The obvious fix looked
like `sectEntryManual`'s `best(true)` then `best(false)`, twenty lines away - and
`TestTheManualIsChosenByAlignmentPathAndReach` has stated in as many words since it was written that
*"a path with no demonic manual gets nothing rather than someone else's"*. That is a documented
decision about what a demonic cell is, and the fallback overruled it to satisfy a claim invented one
file away: a new gate of mine asserting every path is served **at realm 0**. The per-path demonic
floors are 0/1/2/3/4/5, so five of seven paths are unserved at realm 0 *by design* - your path's art
or none, and cultivate further if it is not yet in reach.

The Ghost Cultivator's bug was never that gap. It was having **no demonic manual at any realm at
all**, so the cell could never serve that path however far its initiate climbed - and the content
fix alone closes it. `TestTheHiddenSectCanServeEveryPath` asks the honest question now (served
somewhere on the ladder), and `TestTheCellStillRefusesSomeoneElsesArt` guards the decision from the
other side. The lesson is the one this file keeps recording, met from a new direction: **a gate that
encodes a claim rather than a rule will happily make you change the rule.** Two existing tests were
the only thing standing between that and a merged release.

`sectEntryManual`'s own path filter is, separately, reached for one sect in thirteen: the other
twelve carry a manual of their own, which its first loop prefers. That one is the Heaven-Devouring
Demon Sect - the hidden sect - so `shadowInitiationManual` is effectively the only live reader of
`manual.path` that decides whether a player gets a method, which is why nothing orthodox was
authored for the ghost: a manual no door hands out is the `/learn` fault again.

### A household teaches its own (v1.0.3)

Thirteen birth households, and **eight of them handed a child another organisation's canon as the
family's own tradition**: a fallen martial clan teaching the Azure Cloud Sect's foundation sword
canon, a tomb-watch clan the Jade Meridian Sect's, a nether-market house the Black Serpent Clan's
venom primer. `sect_actions.go:199` is the one reader of `manual.Sect` and it is the sect-inheritance
redemption list, so those ids are literally that sect's teaching material. The other five handed out
a procedurally generated manual with its catalogue index in its own title - *"Starfall Scripture -
Sword Cultivator 25"*, to the noble martial clan, the wealthiest house in the game - and two of
those named a **Sword Cultivator** manual to a household whose trade is Formation.

**There was nowhere correct to point them.** Of 160 manuals only 18 were authored; 12 carry a `sect`
and the other 6 are path-locked dark arts that `manualForbidden` refuses at the lesson outright. So
**no authored, sect-less, non-forbidden manual existed in the game at all**, and the content had two
options that were both wrong.

**The split was also a silent, permanent mechanical difference.** The eight sect canons are Mortal
grade and the five generated ones Spirit - 1.03 against 1.12 in `manualGradeMultiplier` - and
`manualCultivationMultiplier` rides every cultivation session for the whole of a character's life.
Which household you were born into was worth nine percent of your cultivation for ever, decided by
which of two wrong options an author reached for, and stated nowhere.

All thirteen are authored now, Mortal grade, `path: "Any"`, carrying no `sect`, three techniques
each, named out of the household's own `story` - the Left-Hand Forge Canon for the swordsman who
lost his right hand, the Stick-in-the-Mud Array Primer for the widow who drew the border array with
a stick. `path: "Any"` because creation chooses a path *before* the send-off, so a path-locked
family manual would be wrong for six children in seven. Mortal for all of them because rc.31's
tutoring band is already what varies by a household's wealth, and two ladders varying on one axis
would price the family's money twice - the reason rc.55 kept the manor array out of seclusion's
environment term.

**Nothing is retired and nothing is grandfathered, and those are the same decision.** The sect
canons stay: they are the redemption pool, and a live character holds one in `character_manuals` and
in their bags, so removing one would dangle both and silently drop their practised-method choice to
the fallback. Because they stay, a character who already passed the lesson simply keeps what they
were given - and it costs them nothing, since the eight sect canons and the thirteen new manuals are
the same grade. The five Spirit-grade households run the *right* way round: an existing character
keeps 1.12 and only new ones start on the 1.03 floor, which is rc.56's rule that a new rule must
never shorten something a player already committed to.

### An unknown item is answered with the item (v1.0.3)

The Admin Console's Inventory card refuses a display name and offers the closest ids, so a GM can
correct the field instead of the database. Typing **"Qi Nourishment Pills"** answered with five
`advanced_demonic_*_qi_refiner_manual`s - demonic cultivation manuals, for a pill.

Two faults in one expression. The filter took `any()` token hit, so the single token "qi" was
enough; and it then **sorted the survivors by id**, so the 142 generated `advanced_*` ids win every
time on the letter 'a'. `qi_pill`, which is the item, matched exactly as well and was never shown.
And the near-misses a GM actually types are inflections - "Nourishment" for "Nourishing", "Pills"
for "Pill" - which a substring test cannot see at all. It is a similarity score over both the id and
the display name now, and the suggestion carries the name, because one of the two is what they were
reading. The drill restores the old expression and prints the user's own error message back,
verbatim.

### A body mends on its own (`vitality_recovery.go`, schema 59, v1.0.4)

Twelve `SET vitality` statements in `go_core`, four of them damage, and **not one keyed on rest,
cultivation, seclusion or the scheduled tick**: four pills and one technique were the whole of it.
So a cultivator who lost a fight - nine defeats in ten, `fatalChance` being `min(75, 8+gap*3)` - sat
on the number the fight left them with until they bought their way off it, and somebody with no
stones and no pill had no way up at all. v1.0.3 closed the loop on a purchase and deferred this with
that reason written down; this is the half that costs nothing but time.

**The share is of the cultivator's own maximum**, so the same wound costs the same four world days
at every realm and what changes with cultivation is what a quarter is worth. The rate is content
(`vitality_recovery`), and an unauthored one **heals nobody** rather than falling back on a number
of the engine's invention - a healing rate nobody wrote is exactly the kind that would then be tuned
by editing Go, and a fallback that looks like a value is not a sentinel.

**`updated_at` could not be the anchor**, which is the whole reason schema 59 exists: it moves on
every write, so a player who did anything at all would reset their own healing. NULL is "never
settled" and banks nothing, because there is no honest way to say how long somebody has already been
hurt; an upgraded world starts each character's clock on their next action.

**The leftover minutes are carried.** The anchor moves only by the minutes that actually bought a
whole point, so resting in pieces is worth exactly what resting in one span is - rc.56's
`seclusionGainForSpan` rule, one system over. Time spent already whole does not bank into the next
wound, and a settle at full only keeps the clock current.

**It settles lazily and is deliberately not a simulation step.** `orderedSystems` are the world's
own batches, daily and behind an automation flag a GM can switch off, and rc.56 already wrote down
that a flag-gated sweep must not be the only end for state a player is sitting behind. So it sits in
`applyAuthoritative` beside `ensureRoadTransitReadyTx`, and it returns no error by construction:
being hurt must never be the reason a command refuses. The accepted cost is the one
`ensureRoadTransitReadyTx` already pays - a pure read like `/sheet` shows the last settled value
until the player does anything at all.

**A fight is not rest.** `combatTurnAction` keeps `battles.player_hp` and `characters.vitality` in
lockstep, so mending behind an active battle's back would silently desync them and the next turn
would write the stale number back.

**Two of the drills caught the gate rather than the tree, and one caught the code.**
`TestTheRemainderIsCarried` first settled on multiples of 120 - and at 25% of 12 a point costs
exactly 480 minutes, so every settle landed on a point boundary, `anchor + consumed` and
`gameMinute` coincided, and discarding the remainder changed nothing. It settles *between*
boundaries now, which is the only place the two differ. And writing the tests found a real bug
first: the guard read `anchor <= 0`, which rejects world-minute zero - a real minute, the moment a
fresh world's clock starts - because `i64(nil)` is also 0 and the value was doing work the
separate NULL check already did.

### A quest is recorded before it is told (v1.0.5)

Seventeen call sites wrote `await announce_quest_progress(interaction, await QUESTS.progress(...))`,
and **eight** placed that one statement *after* the command's reply - each with a comment citing
rc.28.

That rule is real, and it is about the **announcement**: `announce_quest_progress` falls back to
`interaction.response.send_message` when the interaction has not been answered, so a reporter ahead
of a command's only reply spends it on the quest line and the player never sees their craft roll. It
says nothing about the **record** - and nesting the two inside one statement made the record inherit
the announcement's position.

v1.0.3's `/craft` is what that cost. The reply raised on a missing key *after* `applyAuthoritative`
had committed, so the materials were spent, the pills granted, the profession XP credited, and the
quest never advanced. The player reported it as two bugs a day apart - *"it takes your items"* and
*"crafting did not update the quest"* - and they were one.

`record_quest_progress` is the record on its own. It never raises, because it runs before the reply
now and an exception escaping it would take the command's answer down with it - the same promise
`announce_quest_progress` already made about the other half.

**All seventeen are split, not only the eight that were wrong.** A tree where some sites nest and
some do not is what invites the next author to nest, and the nesting is precisely what hid the
ordering. With it gone, `test_a_quest_is_recorded_before_it_is_told.py` can state both rules
exactly: the record is never an argument to the telling, and the record precedes the command's
answer. A reply that also returns is a refusal path, not an answer, so it does not count.

**Three measurements, and the first two were wrong.** A first sweep asked "is there any reply call
at a lower line number" and reported nine sites, seven at risk - it was counting early-return
refusals. A second scoped to sibling statements and reported twenty-two, because at module level the
function *definitions* are siblings. Only the third - this function's own top-level statements, with
returning replies excluded - gave the eight, and each was then read by eye before being touched. A
sweep whose every hit needs hand-checking is not a gate (v1.0.1), and that applies while you are
still deciding what the finding *is*.

**The gate's own first run found a seventeenth site and then a blind spot.** `_report_trade` in
`economy.py` was written `announce_quest_progress(interaction,await QUESTS.progress(` with no space,
so every grep had missed it. Worse, it both records and tells, and its two callers reach it by name
- so a gate that only saw direct calls would have let that helper be moved after a reply without a
word. It closes over a module's recording helpers now.

### The craft menu offered what the engine would refuse (v1.0.5)

`recipe_autocomplete` was `DB.search_catalog("recipe", current, 25)` - the whole 33-recipe
catalogue, capped at Discord's 25 - while `craft.resolve` refuses any method the player has not
learned. A fresh character knows about three. And `hubs._autocomplete_provider` deliberately reuses a
slash command's autocomplete as a panel's option source *"without duplicating game lookup logic"*, so
the same list is what a hub press renders as a drop-down: the question *"why is crafting a drop-down
menu"* was really *"why is the menu full of things I cannot make"*.

rc.46 settled this one surface over - **a surface must not offer what the engine will refuse** - when
the quest journal stopped listing what no roster would hand over. The reader it needs has existed
since v1.0.1: `DB.get_known_recipes`, built for `profession status`.

Knowing a method and being equal to it are two different refusals, so a recipe above the player's
rank stays on the list. An empty picker is not a dead end either: the hub already prints a
registered hint for one, and craft now has one naming the slip and the status page.

**The gate failed against correct code on its own first run**, because the docstring explaining the
fix *names* `search_catalog` - rc.52's rule arriving immediately rather than a release later. It
reads the function's statements without its docstring now.

### The protection only the bot believed in (`violence_suppression.go`, v1.0.6)

`app/ai/narrator_context.py` tells the narrator, in these words, in two places: *"PROTECTED; violence
cannot mechanically begin here"*. Three things enforced it - the duel invariant in
`pvp_invariants.go` ("local formations suppress PvP here"), `/duel` in `duel.py`, and
`/battle challenge` in `battle.py`. **`combat_actions.go` named `SafeZone` zero times**, so for PvE
the rule lived entirely in Discord and `combat.start` would have begun a fight anywhere for any
caller that asked. That is rc.48's rule for the fourth time in this tree - *a bound that lives in
the client is not a bound* - and what hid it is the shape rc.48 itself warned about: the
**neighbouring** kind of violence really was engine-held, so the file next door read as proof the
rule was enforced.

**And the bounty hunter did not care where you stood.** `advanceHunters` raises pressure, engages
and **captures** - and the words `location` and `Location` appeared nowhere in it or in
`spawnHunters`. So a fugitive was taken off the floor of a hall whose own description reads
*"Violence inside is forbidden; the protection ends at the front doors"*, while `protected_interior`
sat on all 48 auction houses read by nothing, one of the three entries `field_readers_test.go` (v1.0.1)
had to open its allowlist with.

**The two protections are deliberately two predicates, and the measurement is why.** `safe_zone` is
true on **446 of 477** locations - every town, gate, shop and shrine - and false on the 31 that are
hunting grounds, ruins, open country and **Greenriver Town**, the starting town, deliberately rough.
So it is a statement about settlement, not sanctuary, and all it may buy is that nobody *starts* a
fight there. What the 48 auction floors claim is stronger and rarer and had its own field. Gating
the hunter on `safe_zone` would not give the bounty system a sanctuary - it would end it, because
players live in towns; gating it on `protected_interior` gives a fugitive 48 rooms, each of which
must be entered by an action and left to do anything at all.

**Capture is what a sanctuary stops. Pressure is not.** The hunter is at the doors either way, and a
floor that froze a pursuit outright would be somewhere to park a fugitive for ever. The two halves
are asserted apart for exactly that reason: a test that only said "nothing happened inside" would
pass just as well for the wrong rule, which is the rc.47 shape.

**What a safe zone refuses is a fight somebody chose to start**, and that one sentence is why two
things that look like exceptions are not. A world event that lands in a town is still fought -
rc.49's own asymmetry, *being caught in something is not the same as being handed it* - and so is
the ambush `auctionLeaveAction` stands at `EntranceLocation`, which is a safe zone for 47 of the 48
houses. A gate that refused those would delete the event battle in 446 places and the door risk in
47, which is not enforcing a rule but deleting two systems the content describes.

**`door_rule` is retired rather than read**, and this is the one place the release removes
something. It said *"the protection ends at the doors"* - the second half of the sentence
`protected_interior` opens - all 48 houses set it `true`, no house's prose can differ (every one of
the 48 descriptions says both halves), and the engine already ends the protection at the door by
standing the ambush outside. **A switch content cannot turn off is not a switch**, which is rc.59's
`#event-scenes` call. `unreadContentFields` is down to one entry, `Path.Skill`, still deferred with
its reason.

**`battle.py`'s refusal is kept, and is now an anticipation rather than the rule.** Presentation may
predict a refusal the engine will make - `_progression_hidden_actions` does it for every late door -
and what it may not be is the only place the rule lives.

**The fixture could not fail the way production fails, again.** `tracking_pursuit_test.go` declared
no `characters` table at all, so the pursuit sweep reading a quarry's location broke it outright -
which is the rule this file already states, caught this time by the change rather than by a release.
It carries the table now, and both new fixtures carry an auction floor with `protected_interior:
false`, a combination the shipped content does not contain: without it every assertion would pass
just as well for a rule that answered "sanctuary" to any auction interior, and the field would be
decoration again.

**The gates, and what each drill prints.** Disabling the `combat.start` check gives *"a challenge in
a safe zone was allowed; the engine had no such rule before v1.0.6"*; extending it to events gives
*"an event battle in a safe zone must still be allowed"*; ignoring `ProtectedInterior` gives
*"Unguarded Stalls is not a protected interior but answered sanctuary Open Yard"*; letting capture
through gives *"a hunter took a fugitive off a protected auction floor: capture_progress is 12"*;
freezing pressure as well gives *"a floor that freezes a pursuit is somewhere to park a fugitive for
ever"*; giving the tick its own `ProtectedInterior` lookup gives *"the tick no longer reaches
game.LocationIsSanctuary"*; restoring `door_rule` to the struct, and separately un-wiring
`ProtectedInterior`, each fail `TestEveryParsedContentFieldHasAReader`; and blanking the content
reader fails with *"the content parse is broken, not the tree"* **before** any assertion it would
have made vacuous.

### An era belongs to one world (`world_eras.go`, schema 60, v1.0.7)

There was **one era for the whole game**. Every reader asked
`WHERE active=1 ORDER BY era_id DESC LIMIT 1` and got the same row whether it was pricing a siege
among the immortal courts or a cultivation session in a Mortal hill village - while the realm
capitals have been per world since schema 4, the auction floors since schema 35 and a world's *news*
since schema 56. The era was the last thing in this tree still pretending the four worlds were one
place.

**What made the split small rather than structural is that every reader was already about something
with a location.** A cultivator stands somewhere, a territory is somewhere, a war is over somewhere,
a caravan runs between two somewheres - and v1.0.6 had just taught the bounty sweep to read its
quarry's location, so that loop already held what it needed. Each one resolves the world it was
already talking about instead of taking the only row there was. `game.ActiveEra` is the one door,
and `EraWorldOf` is the one statement of which world a question is about.

**A cycle is a world year now, and the roster is content.** It ran 540 world days, which matched
nothing; `minutesPerYear` is 12 months of 30 days, so a year is 360 and each world has **six eras of
sixty**. `world_era_cycles` in `content/world.json` holds all twenty-four, because the roster is
content in this tree (`event_sites`, `forage_materials`, `beginner_path`) and because four Go
literals would have been four places to forget. Three copies of that roster existed before this -
the Go literal, a `world_eras` row, and `ERA_CYCLE` in `app/rules/advanced_runtime.py` - which is the
fault rc.39 removed for the world clock and rc.44 for the world currencies; there is one now, and
`describe_era` takes it **injected** because `WORLD` is built in `app/bot/runtime.py` and
`test_app_layout.py` puts `rules` at the bottom (the reason `narrator.py` takes a duck-typed
`npc_resolver`, rc.27).

**And the counting is the finding.** Of the eight modifier keys the old cycle authored, production
Go fetched **four**. `secret_realm_frequency`, `market_volatility`, `beast_encounter_rate` and
`recovery_rate` each occurred exactly once in all of `go_core` - their own declaration in `eraCycle`.
So every era carried one live modifier and one dead one, and **the Beast Tide Era, whose entire
identity is beasts, did nothing whatever to beasts**: mechanically it was "caravans are fifteen
percent riskier". A Quiet Heaven's `recovery_rate` healed nobody, which is a promise it had been
making since before anything in the game recovered at all.

Authoring twenty-four eras on that vocabulary would have been manufacturing decoration at scale, so
the wiring came first. `beast_encounter_rate` divides the margin a hunt must clear (a 2d10 margin,
not a percentage, so it divides rather than multiplies, floored at 1 so no era makes an encounter
automatic). `recovery_rate` scales v1.0.4's `percent` - **not its `gain`**, which is the whole reason
it is one line: `newAnchor` divides by the same `percent`, so the remainder the anchor carries stays
exactly consistent with the gain it paid for. The two that cannot be wired honestly are authored by
no era at all and sit in `unreadEraModifiers` with their reasons, which
`TestNoEraAuthorsAModifierNothingReads` holds shut from the other side: the allowlist names what is
*deferred*, and a deferred key may not be authored.

**The gate found a gap in its own release on its first run**, and the fix is better code.
`eraCultivationMultiplier` pulled its key out of the map by index, so `cultivation_gain` - the one
modifier every cultivator feels - had no argument position for the sweep to see and was reported
unread. `eraTerm` is the named accessor now, so **every** read of an era modifier in this tree is an
argument position. That is not style: it is what lets the gate tell a rule *fetching* a key from the
content file *declaring* one, which is rc.58's "fetched, not named" distinction.

**A missing key is `def`, never zero**, and here that matters in a way it would not elsewhere: every
one of these keys is a multiplier, so a key read as 0 would not soften a rule, it would delete it -
no cultivation gain at all, no siege power, no caravan ever arriving. The `seller_user_id=0` lesson
in a place where the damage would be invisible.

**Schema 60 defaults to the Mortal World rather than NULL.** Every row that existed when it runs was
written when there was one era, and that era was seeded "Jade Meridian Awakening Era", the Mortal
cycle's first entry - so a live world carries on from exactly where it stands (`advanceWorldEra`
finds a world's place by matching the active row's name), and the three worlds above it open their
own cycle on the next tick rather than inheriting somebody else's history. `DefaultEraWorld` is also
the answer for a place the catalogue does not carry - a household, an inner world, an abode -
deliberately **unlike** `world_of_location` on the Python side, whose `None` is what keeps a
household's news out of a world's public feed (rc.52): being indoors is not being outside history.

**Six fixtures declared `world_eras` without the column production now has**, and every one of them
broke the moment a reader asked for it - the rule this file already states, caught by the change
rather than by a release. `FakeWorld` and `FakeDB` in `test_narrator_context.py` needed the same.

**The gates, and what each drill prints.** Authoring a deferred key fails **two** gates at once, the
second naming it and its reason; removing the beast wire prints `beast_encounter_rate (authored by
Celestial World/Primordial Beast Waking Era, …)`; removing the recovery wire names its seven eras;
shortening one world's cycle prints `Immortal World runs 390 world days, not one world year (360)`;
giving two worlds one era name prints *"the cycle position is found by name"*; and pointing the
sweep at a reader that does not exist prints *"the production walk did not find war_pressure handed
to any era reader; the sweep is broken, not the tree"* - **before** the assertion it would have made
vacuous.

### The picker offered who the card refused (v1.0.8)

**Found by playing.** A cultivator stood at Cloudblade City East Gate, whose scene card names the
person in the room - *"Here the East Gate of Cloudblade City, facing Ironbanner City · Gate Captain
Yue Dong"* - and both `/talk` and `/npcinfo` answered *"nothing to choose from right now."*

`local_npc_autocomplete` asked `DB.search_catalog("npc", current, 25)` and filtered the answer by
location. That query is `SELECT name FROM content_npcs WHERE name LIKE ? ORDER BY name LIMIT 25`, and
an empty box makes the needle `%%` - so it returns **the alphabetically first twenty-five of all 574
catalogue NPCs** and the location filter then runs on *those*. The picker could therefore offer
somebody only if their name sorts near the front of the world **and** they are in the room; for most
rooms that is nobody, and a gate captain called **Y**ue Dong could never appear anywhere at all.

**rc.28 wrote the resolver for exactly this question and stated the rule the split broke**: *"a
picker that offers somebody `/talk` then refuses them is worse than either being wrong alone."* The
inverse is what shipped - the card named people the picker would not offer - because
`/scene status` and `/sense` ask `npcs_present` and this did not. It is also v1.0.5's craft-picker
finding one command over: that release pointed `recipe_autocomplete` at what the player has learned
and did not look for siblings. This is the sibling, and it is the fourth surface (`/talk`,
`/npcinfo`, `/sense`) off one function.

**What it actually cost is not conversation.** `beginner_town` ("Out of the Gate") is
`explore` → **`talk`** → `trade` and `beginner_home` is `return_home` → **`talk`** → `cultivate`, so
the beginner path stalled at its second stage - which is why `scene_action` kept working and the
player reported the first stage fine. And the commission ladder runs **inside `/talk`**
(`commission_offer_for` at `scene.py:181`, `commission_reply_extras` at `:283`), so a player could
be neither offered work nor able to hand it in: completion happens in `quest.progress`, and **137 of
the 140 authored commissions carry at least one `talk` objective** (198 `talk`, 93 `scene_action`,
84 `explore`). `/city board` and `/city accept` are a separate picker and were unaffected, so a
commission could be taken and then never advanced.

**The fix narrows the picker, deliberately.** An NPC `current_npc_location` answers `None` for -
nothing knows where they are - used to be offered *everywhere*, because `/talk` reads `None` as "do
not filter by location" and lets them through. `npcs_present` does not list them anywhere. That is
the safe direction of the rc.28 rule: never offering somebody `/talk` would allow loses
discoverability, while offering somebody it refuses is the fault the rule exists for - and the
picker now agrees with the card exactly, which is the point.

**The dead-filter gate went red against correct code, and that is the second finding.**
`test_the_picker_skips_the_dead` asserted three substrings of the picker's own tail
(`if npc_location == DEAD:` and the location filter beside it). Refusing the dead still holds on all
three of `npcs_present`'s paths - the engine's `status IN ('alive','missing')`, the registry's
hardcoded `'alive'`, and `current_npc_location` answering `DEAD` - so the rule survived and only its
spelling moved. **A gate that pins where a rule is written rather than that it holds fails exactly
when the rule is moved, which is the one time it should stay green**, and nothing anywhere drove the
behaviour: `WhoIsHereRefusesTheDead` is the missing half, and it drives the catalogue path, which is
the one Python owns.

**Writing it found a fixture that accepted what production refuses.** rc.28's `_CountingSim` returned
every row at a location regardless of `status`, while `npcsAtLocationGo` filters
`status IN ('alive','missing')` - so the new test failed against *correct* code, reporting that the
resolver stands corpses in the room. The fake carries production's filter now. It is the
`npc_consignments` rule met in the permissive direction, where it produces a false finding rather
than a missed one.

### A reset takes the private rooms with it (v1.0.8)

Reported from live play, in four words: *"Reset should delete the threads."*

`character.reset` sweeps a player's rows across ~104 tables, and four of those rows are the only
record anywhere of a Discord thread the bot made for that player - the expedition journal, a cave
abode, a sect residence, a battle thread. The rows went and the threads stayed: an abandoned life's
private journal was left standing with its whole scene log in it, and **nothing left in the database
that could ever name it again**, so no later cleanup could find it either.

**The rule was already written down, for the other case.** `DB.all_managed_thread_ids`' own docstring
says the threads must be collected *"before the rows that reference them are wiped, since once the
database is gone there is no other way to find them again"* - and that was written for the
world-wide `reset_database.sh` wipe. The per-player lever that wipes exactly those rows never applied
it. Same shape as v1.0.1's clan diplomacy, where the comment naming the fault sat in the tree and the
fix had been made one table over.

- **The ordering is the feature**, and it is the one thing a reader cannot get from both calls merely
  being present: the ids are read *before* the engine call and the threads deleted *after* it
  succeeds, so a refused reset destroys nothing and a committed one leaves nothing.
  `test_a_reset_takes_the_threads_with_it.py` holds both directions by line number.
- **`admin.player.erase` had the same hole and is fixed in the same release.** There it is not
  clutter: an erasure that left the person's own private thread standing would have removed them from
  the database and not from the server.
- **Only what one player owns.** `birth_family_household_threads` is keyed by `family_id` and a
  starter household is shared by everybody born into it; an event scene belongs to the event.
  Deleting either because one cultivator started over would take a room other players are standing
  in - the same line the reset already draws when it puts the household's welcome line back and
  leaves the house itself alone. The gate refuses both by name.
- **`PLAYER_OWNED_THREAD_SOURCES` lives beside `all_managed_thread_ids`**, and the gate holds it to
  being a **subset** of it, because two enumerations of "which tables hold a thread id" in two files
  would drift silently: a new thread table added to one and not the other leaves a thread nothing can
  ever delete.
- **Deleting a thread never costs the reset.** The engine has already committed by then, so the
  character is gone either way and the only thing an exception could change is whether the player is
  told so. Already-gone, refused and deleted are counted apart, because a GM asked to finish the job
  by hand needs to know which.

### The status line that was one view's leftovers (v1.0.8)

Reported as what the GM dashboard's sidebar footer actually read: **`SQLite`** and **`engine —`**.
Those are the literal placeholders in `dashboard/index.html`.

Both spans were painted **only** inside `loadOverview`, so on any other view they sat on that text
for as long as the tab stayed open - and landing on a deep link (`#admin`, a bookmark, a reload
anywhere but Overview) never painted them at all, because `switchView` runs one loader and
`loadOverview` was not it. An Overview whose own load threw did the same, since that catch is
per view.

**Half of the wire was already there**, which is what makes this the shape this file keeps recording
rather than an oversight: the boot path fetched `/api/overview` when it started on another view and
used the response to set the **world clock and nothing else**, three lines above the two spans made
from the same response. Somebody hit this, fixed the clock, and left the footer below it.

**And a placeholder that looks like a value is not a sentinel** - the `seller_user_id=0` lesson in a
readout. *"engine —"* reads as an engine that answered and had nothing to say; a GM cannot tell it
from an unreachable one, which is why this went unreported for as long as it did: the footer did not
look broken. `paintShellStatus` is the one painter, `refreshShellStatus` is how anything without a
view behind it asks, and `shellStatusUnknown` says plainly when the engine cannot be reached. The
shell keeps ticking off the Overview too, because a frozen *"simulation current"* is worse than no
line at all - it is the line a GM reads to know the world is still running.

**The gate's own first version dumped the whole file.** `assertRegex` prints its haystack, and the
haystack is 1,100 lines of `app.js`; a gate whose message has to be scrolled past is one nobody
reads, so it is an `assertTrue` over a search. Its brace-matching reader asserts it found a balanced
body before anything is asserted on it (rc.57), and the drill that breaks the reader prints *"the
brace reader did not return loadOverview's body; the gate is broken, not the tree"*.

### The game introduces itself a realm at a time (`feature_unlocks`, v1.0.9)

**Found by playing**, and reported in one sentence: *"it's become complex and overwhelming."*

A character three minutes old met **249 leaves across 67 pages in 16 hubs** - every system the game
has, at once. `cultivation` alone carries 34 leaves over seven pages, `sect` 29, `economy` 27,
`character` 25, `family` 24, `combat` 24. **None of it was refused**: sect politics, territory war,
caravan dispatch, boss raids and the auction floor all *work* at Body Tempering. They are simply not
what the first hour is about, and nothing anywhere said so.

**This is a different rule from rc.32's, and that is the whole thing to understand before touching
it.** `PROGRESSION_GATES` hides a door the engine **would refuse outright** - a Law before the realm
that can hold one, a sect's rooms to somebody in no sect - and its limit is stated in this file:
*"never a status read or the door into the system, because a road nobody can see is a road nobody
learns exists."* A pacing curriculum hides doors that would have worked. It has to earn that limit
back, and three properties are what do it.

- **Nothing vanishes.** A page holding doors back prints **one collapsed line** naming the count and
  the nearest realm; `/locked` lists every door with what it needs. A padlock each was rejected on
  the finding itself: the complaint was a wall of rows, and a wall of grey rows is the same wall.
  The line is why the two kinds of hiding are **two registries** rather than more entries in one -
  `_HIDDEN_ACTIONS` means "the engine would refuse this where you stand" and earns a padlock naming
  the reason, `_NOT_YET_UNLOCKED` means "not introduced yet" and earns the collapsed line. Merging
  them would make one line type mean two things and put the page straight back.
- **Gating is advertising, never a bound.** Nothing is consulted on a press: `/auction` typed
  directly still runs, and the engine's own rules stay the only refusal. **A bound that lives in the
  client is not a bound** (rc.48) - already found four times in this tree, and a realm floor written
  for *pacing* becoming a refusal the engine never agreed to would be the fifth.
  `test_the_curriculum_opens_as_you_cultivate.py` reads `_panel_gate`'s statements and the two
  engine clients to hold it.
- **The roster is content**, so a GM retunes it without a code change - the rule this tree already
  follows for `event_sites`, `forage_materials`, `beginner_path` and `world_era_cycles`. It is
  authored by **page** with per-leaf overrides, because a page is the natural unit of "a system" and
  a 67-entry roster is reviewable where a 249-entry one is not. The overrides exist because several
  pages mix the two ends: `character / Overview` holds `sheet` beside `soul` and `inheritances`, and
  `cultivation / Path` holds `aptitude root` - what you were born as, which is identity rather than
  a system - beside `aptitude evolve`.

**Every status read stays open at realm 0**, deliberately and against the temptation to count them
as noise. `sect status`, `beast status`, `abode status`, `innerworld status`, `secretrealm status`
and the rest are how a player learns a system exists at all, which is precisely what rc.32's limit
protects. What waits is the levers inside.

**Two floors are absolute and each has its own test.** Anything the beginner path or a household
errand needs stays at realm 0 - gating a leaf the opening *requires* would make it illegible
instead, and silently, because the quest would still be held and simply have no visible way to
advance. And **`reset` is never held back**: the player most likely to want it is the one who has
just decided this game is too much, which is the player this release is for.

**`law` and `tribulation` are deliberately left to the engine's gate** rather than restated here.
They are already hidden by `_progression_hidden_actions` at their real floors, and a second
statement of one rule is the fault rc.39 removed for the world clock and rc.44 for the world
currencies.

**The rules half takes its roster injected** (`app/rules/feature_unlocks.py`), because `WORLD` is
built in `app/bot/runtime.py` and `test_app_layout.py` puts `rules` at the bottom - the reason
`describe_era` (v1.0.7) and `narrator.py`'s `npc_resolver` (rc.27) are shaped the same way. An
absent, empty or unreadable roster **locks nothing**: a presentation filter that failed towards
hiding would leave a player looking at an empty game with no way to tell that from a correct one,
while failing towards showing is merely the busy surface this release started from. That is the same
call `maintenance.py` makes about its own flag.

**Three of the suite's exact lists caught the new command and made it a decision rather than a
default**, which is what they are for. `test_app_layout` refused `feature_unlocks` until the module
was registered; `test_bot_package`'s surface table refused `commands/locked.py` until it was named;
and `test_seclusion_lockout` refused `/locked` in the command tree until it was placed on one side
or the other - it is a **read**, so it joins `/cooldowns` and `/quests` in `OPEN_COMMANDS`, and a
secluded cultivator can still see what opens next.

**The gate's own drill found the gate broken, immediately.** The check that the collapsed line names
`/locked` read the whole function *including its docstring* - and the docstring explains the rule,
so deleting the sentence from the returned string left the substring in the prose and the drill
**passed** against a broken tree. That is rc.52's rule (*a gate that cannot tell prose from code is
decoration*) arriving in the same session it was written, exactly as v1.0.1's checklist gate and
v1.0.5's craft-picker gate each did. It reads the function's statements without its docstring now,
and the re-drill fails.

**And a fourth exact list broke on correct code, which is a finding of its own.**
`test_live_auctions.py`'s menu test asserted the tree tuple's **exact literal text**, so adding
`/locked` to it failed a gate that is about whether `/menu` is registered - a question the added
member does not touch. rc.43 had already made this call for `test_commands_reach_a_player.py`: *the
tree tuple is read out of `surface.py` by AST rather than copied*, because a copy is free to drift
and a spelling is not the rule. It reads it now, asserts the read found something before trusting
it, and its drills print *"'menu' not found in {…}"* and *"the tree tuple could not be read off
surface.py"*.

**What each drill prints.** Gating a leaf the beginner path needs gives
`["'talk' (reports 'talk', opens at realm 3)"] != []`; holding back the way out names `reset`;
a floor past the ladder gives *"'sect roster' opens at realm 99, which is not a rung of the
32-realm ladder"*; emptying the roster fires the self-check first (*"the gate is broken, not the
tree"*); letting `_panel_gate` see the curriculum names `feature_unlocks` in its body; merging the
two registries names `_NOT_YET_UNLOCKED`; and replacing the collapsed line with a padlock each
names `_unlock_summary`.

### A city's gate is that city (v1.0.9)

**Found by playing**, and the report is the whole finding:

> Could not enter the household: the Shen Family household stands in **Cloudblade City** and you are
> in **Cloudblade City East Gate** — travel there first, or use a Hearth-Return Talisman

A refusal naming, as somewhere else, the city the player was standing in.

`familyHouseholdEnterAction` compared the character's location to the household's town with **bare
string equality** (`here != town`). `cityOf` - the engine's one statement of which city a place is
part of, `outside_location` plus a `district`/`shop`/`auction_house` - has been read by
`explorationTravelAction` and by `WhereAnNPCCanWalk` since they were written. **This door asked
nobody.**

**317 of the catalogue's 477 locations are parts of a household town**, 92 of them gates, so that is
how much of the world refused it - and it fell hardest on the player least able to work around it.
`beginner_home` ("The Road Home") is the beginner path's fourth stage, its `return_home` objective is
reported by this very action, and **walking home from the road arrives at a gate**: the road's own
arrival rule (`gateFacing`) puts you at the gate that faces where you came from. So the stage the
path ends on could not be finished by walking, only by burning the talisman the send-off happens to
include.

**The fix is one rule asked twice, not two rules**, which is why both halves ship together.
`_household_hidden_actions` *anticipates* the engine's refusal to decide whether the panel draws the
door - which v1.0.6 states is allowed as long as presentation is never the only place the rule
lives - and it carried the same bare comparison, so the panel printed a lock line ("🔒 Enter — the
household stands in Cloudblade City; travel there") in the 317 places the engine would now open.
`test_a_gate_is_its_city.py` holds the two to the same answer **over the whole catalogue**,
computing the rule a third time off the raw content file so two wrong halves cannot agree with each
other and pass.

**And the suite caught this release writing the very fault it is about.** The first version added a
`_city_of` helper to `surface.py` - a *second* Python copy of a rule `commands/exploration.py` has
had all along. `test_every_top_level_name_is_defined_exactly_once` refused it by name
(`_city_of is defined in ['commands/exploration.py', 'surface.py']`), so the import is the existing
one. One rule with two spellings is what caused the bug; two would have been three.

**The Go half is behavioural and drives the shipped catalogue**, not a fixture - it finds a real
city with a real gate and drives the real action, because a fixture city invented for the test is
exactly the shape that cannot fail the way production fails. Its drill prints the user's own message
back: *"standing at "Adamant Body Immortal City East Gate", which is a gate of "Adamant Body
Immortal City", the household refused with: …travel there first"*. The second test is the other
direction - somewhere else entirely is still refused - because a test that only proved the gate
opens would pass just as well for a door that had stopped checking anything, and rc.32's rule is
presence: the door is not a teleport. Its drill prints *"standing at "Ash Gate Ruin", nowhere near
"Adamant Body Immortal City", the household let the player in"*.

### A line that cannot know who is here does not claim to (v1.0.10)

**Found by playing**, and the report was two lines of one panel disagreeing in the same breath:

> **Here** the East Gate of Cloudblade City, facing Ironbanner City · **Gate Captain Yue Dong**

and `/talk`, opened at that same gate, offering **Drillmaster Zhai Kang** - whom `content/world.json`
places at Cloudblade Blade Yards in all five periods. The simulation had walked him to the gate.

**Three readers, one question, and this was the third.** rc.28 wrote `npcs_present` and fixed the
*cards* (`/scene status`, `/sense`); v1.0.8 fixed the *picker*. The **panel header** is
`here_summary`, and it appended
`sorted(n for n, npc in WORLD.npcs.items() if npc["location"] == name)` - the content file's
**residents**, read with no schedule and no simulation - so it was answering "who lives here" to a
line that reads as "who is here". Both were right by their own definition and they disagreed, which
is precisely the state rc.28 named: *"a picker that offers somebody `/talk` then refuses them is
worse than either being wrong alone."*

**The fix is that it stops guessing, because it cannot know.** Who is standing somewhere is a
simulation row, one engine round trip away, and `here_summary` is synchronous - it is drawn inside
panel headers. So it takes `present` and names people only when a caller hands them over; a caller
with nothing to give gets the place described and nobody named, which is the honest half of what a
pure function knows. Both production callers were **already inside async status builders**
(`menu_facts_line`, and `_here_field`'s two call sites), so each pays one `await` and nothing was
restructured.

`_who_is_here` is the one helper both use, and it **never raises**: the Here line is drawn beside
everything else a panel shows, so a lookup that threw would cost the whole card rather than one line
of it - the same call `hidden_actions` and `not_yet_unlocked` already make, and an empty answer is
exactly what the line said before anybody could be resolved at all.

**What this deliberately does not do is make `here_summary` async.** It stays pure and testable, and
the knowledge enters as an argument. A function that quietly grew an engine call would put a round
trip inside every caller that ever renders a location, including the four tests that render one.

**The gate needs rc.52's rule against itself**, and says so: its own docstring quotes the expression
it forbids, so a scan of the whole function body would find the fault in the prose explaining it and
pass. It reads statements without the docstring. Its four drills print the finding - restoring the
catalogue read prints the reported header verbatim (*"'Yue Dong' unexpectedly found in 'the East
Gate of Cloudblade City, facing Ironbanner City · Gate Captain Yue Dong'"*), dropping `present=` at
either call site names that header, swapping the helper off `npcs_present` names it, and removing
its `except` prints *"one unavailable lookup would cost the whole panel rather than one line of
it"*.

**A third hand-copy of the tree tuple broke, and this one is retired rather than extended.**
`test_hint_paths.py` keeps `ROOT_COMMANDS` - the roots that are commands rather than hubs, so
`**/quests**` in a reply is not a broken hub path - and it was written out by hand, so `/locked`
failed it the day v1.0.9 added that command: a gate about whether a *printed path resolves* going
red over a root it had never been told about. `test_live_auctions.py` was the second this session
and rc.43 made the call for the first: **the tuple is read off `surface.py` by AST, never copied.**
Three copies is a class, and all three are now readers.

**And the v1.0.8 entry in `docs/TODO.md` had to be corrected**, because it claimed the picker now
asks *"what the card has always used"*. True of `/scene status` and `/sense`; false of the panel
header, which is this finding. A note that is right about two readers and wrong about the third is
how the third goes unlooked-at.

### The writer a human drives is the one nothing held (v1.0.11)

Two levers, one sentence. Both are `admin.player.*`, both are the only door a GM has to a thing the
engine otherwise writes once at birth, and both were the one writer in their family that nothing
checked.

**`admin.player.set_spiritual_root` held its grade to a hand-written copy of the ladder.** Six names
in a map literal - `{"Mortal", "Common", "Refined", "Earth", "Heaven", "Immortal"}` - beside the six
`spiritual_root_system.grades` the content file carries. It could not be wrong in an interesting way,
because the copy agreed with the file the day it was written and agrees with it now; the day a rung
is renamed or added it refuses the real grade and accepts a stale one, silently. That is the shape
rc.44 removed for the world currencies and v1.0.7 for the era roster, and this session had already
retired three hand-copies of the command tree's own tuple for it. What makes a wrong grade quiet
rather than loud is `gradeIndex`, which answers 0 for a name it does not know: since rc.55 that is
Mortal's 0.88x cultivation and -1 on every breakthrough, for the character's whole life. **A fallback
that looks like a value is not a sentinel** - so the check has to be here, at the writer a human
types into. rc.55 found exactly this shape and gated the *fixtures*
(`TestEveryFixtureRootStandsOnTheLadder`); the lever was left ungated.

**`admin.player.set_physique` moved three numbers and never the identity.** `evolution_stage`,
`progress` and `stability`, and nothing else - while the only two statements in the whole engine
that have ever written `physique_id` are character creation and samsara (`aptitude.awaken` and
`aptitude.evolve` both pass the loaded bundle back through `savePhysique`, so they move the state
and never the name). This is **not** dead content: all eight non-ordinary physiques are drawable at
birth, because `generatePhysique` gives every one weight at least 1. So it was a missing lever
rather than a `/learn`-class orphan - a GM could not hand somebody `nine_yang_solar_body`, correct
one rolled wrong, or stage one for a playtest. `physique_id` is optional on the payload, so every
existing caller still edits the three numbers; when it is given it is held to the catalogue, because
an id the catalogue does not carry contributes no modifiers at all and would be a physique that
exists only as a string on the sheet. **The identity goes into the undo snapshot whether or not the
call changes it**, since a snapshot of three numbers would leave a granted physique standing and
call itself an undo; a snapshot from before this release carries none, and the three-number
statement is kept for it, because an old audit row must stay undoable on the terms it was written.

**The browser kept a third copy of the ladder.** `gradeOpts` in `dashboard/app.js` was
`['Mortal','Common','Refined','Earth','Heaven','Immortal']`, and the physique card offered no
picker at all. Both come off the content file with the row now (`_aptitude_catalogue`, sent as part
of `player_detail`), which is rc.37's whole rationale for the Player Editor - the ids a lever needs
are picked, not typed - and rc.46's rule seen from the wrong side of the counter: a picker built
from a copy offers what the engine will refuse the day the two disagree. The rungs keep the ladder's
own order, because a grade *is* an order and sorting it alphabetically would put Common above Earth.
An unreadable content file answers empty lists and each card says it has nothing to offer; a
fallback list would be the hand-written copy this release removes, wearing an exception's hat.

**The fixture could not fail the way production fails, and finding that is most of the work.**
`Apply(databasePath, req)` calls `ApplyWithWorld(databasePath, "", req)` - an **empty world path** -
and every admin test in the tree went through it, while production only ever calls
`game.ApplyWithWorld(s.databasePath, s.worldPath, ...)` (`server.New`). Under an empty catalogue a
content-backed check answers "not in the catalogue" to everything, so the first run of the new gate
failed on correct code with *"the spiritual-root ladder is missing from the content file"*. Read the
other way, that is the finding: **a lever that refuses everything would have passed every admin test
in this repository.** `applyAdminRaw` hands the real file to the dispatch, and the seven pre-existing
call sites go through it too.

**The gate that matters is the one a copy cannot pass.** The obvious behavioural test - set a grade,
read it back - passes identically against the map literal, because the literal is currently right;
that is the rc.47 shape, a gate that cannot see the thing it forbids. `worldWithAnExtraRung` writes a
copy of the shipped content carrying a seventh rung and drives the lever against it, which is also
precisely the day the fault would first cost somebody something. Its drill prints
*"the content file carries a "Primordial" rung and the lever refused it: grade must be one of
Mortal, Common, Refined, Earth, Heaven, Immortal"*.

**And the Python half deliberately has no tree-wide sweep**, which its own first run is the reason
for. Written in `test_one_world_currency_rule.py`'s shape - a production file naming three or more
rungs is restating the ladder - it reported five offenders and every one was a false positive:
`Mortal`, `Earth`, `Heaven` and `Immortal` are also manual grades, qi-body grades, world names and
the generated catalogue's tiers. That is CLAUDE.md's own "a name is not a reader" one level out; the
currency ids are unique strings and these are four ordinary words four vocabularies share. **A sweep
whose every hit needs hand-checking is not a gate** (v1.0.1), so it was deleted rather than
allowlisted - five entries would have been five places for a real copy to hide. What is held instead
is the wire, each where it can be told apart: the engine's half behaviourally in Go, the browser's
half by reading `loadPlayerEditor`'s own body.

### A server is told what it missed (v1.0.11)

rc.59 made the bot post its own release notes into `#updates` and compared
`server_config.announced_release` to the running version for **equality**, then fetched that one
changelog entry. So a server upgrading 1.0.5 to 1.0.8 was told about 1.0.8 and never about 1.0.6 or
1.0.7: the marker jumped straight across and nothing recorded that two releases went past
unmentioned.

**Three neighbouring behaviours do work, and that is what hid it.** A NULL marker records silently
(a fresh install does not want forty paragraphs of history), and neither a missing changelog entry
nor an unbound channel advances the marker, so both get a later chance. A *skipped* version is in
neither category, because it was never looked up at all.

`releases_between` walks the gap. Three things about it are decisions rather than mechanics:

- **Versions sort as integers, and a candidate sorts below the release it is a candidate for**, so
  `1.0.0-rc.59 < 1.0.0 < 1.0.1 < 1.0.9 < 1.0.10`. The first half is v1.0.1's own lesson, where
  `playtest_checklist.py` sorted `v1.0.10` before `v1.0.9` as text and inherited the wrong
  checklist's ticks. The second is a trailing sentinel: an entry with no rc suffix is the final one
  of its base, so it takes a number no candidate can reach.
- **The marker stops at the last release actually posted, never past it.** A send that fails halfway
  through a gap must not make the releases it never reached look announced - *"exactly once"* has to
  survive a partial failure or it is only a claim about the happy path.
- **A capped catch-up says what it is not showing.** A server away a year gets
  `MAX_ANNOUNCED_RELEASES` sentences and one line naming the rest, rather than thirty messages or a
  silent drop - the same reason a fresh install is spared its history on purpose.

**The gate's fixture was standing in for the thing being changed.** rc.59's
`test_release_notes.py` stubbed `release_notes_for` with a lambda returning `"The notes."`, so the
parser and everything under it were never driven from `announce_release_if_new` at all - and this
release replaced that reader with `releases_between` under a green suite. It pins a **temporary
`VERSIONS.md`** now instead, so every test drives the real parse. Three drills: restoring the
equality-only fetch prints *"a server upgrading 1.0.5 -> 1.0.8 must hear about 1.0.6 and 1.0.7
too"*; marking the running version regardless of what was posted prints `'1.0.8' != '1.0.6'`;
sorting the versions as text prints `['1.0.0', '1.0.0-rc.59', '1.0.10', '1.0.5', '1.0.6']`.

### A room for people who have played (`Xianxia • Cultivator`, v1.0.11)

Every player-facing category on the server was gated except one. Realm Capitals sit behind the
presence role, the four World Events feeds behind the realm-access role, Admin behind administrator
- and 🗺️ Cultivation World, which holds `#player-homes` and `#expeditions`, was open to everybody.
So a newcomer's sidebar advertised read-only anchors for threads they cannot have, directly above
the `#begin-here` they are meant to go to.

**The deeper half is that no "has a character" role existed at all.** `_sync_realm_access_roles` and
`_sync_realm_presence_roles` both run from `require_character`, so both only ever fire for somebody
who already has one - which means nothing in this server could be gated on having played. There is
one generated role now, and where it is written and taken off is the whole of it:

- **`require_character` keeps it in step**, in the same block as the two realm syncs and for the
  same reason: it is the only thing in the bot that fires often enough, and reaching it at all is
  what "has a character" means.
- **Creation grants it before the first private thread is opened.** Both anchors carry
  `private_thread`s and a member still needs to see a thread's parent, so the grant sits above
  `ensure_birth_family_household_thread` rather than below it.
- **An erasure is the one place it comes off**, and the asymmetry is deliberate: after an erasure
  `require_character` never fires for that account again, so nothing else can ever notice. It goes
  beside the threads v1.0.8 taught that path to delete.
- **`_sync_all_realm_access_roles` backfills it**, because that sweep is the one thing that walks
  every character already in the guild - so an upgrading server puts the role on people who made
  their cultivator before it existed. It also checks the new role against the bot's own hierarchy
  there, so a role above the bot fails once, loudly, instead of silently per member.

Three rules on the overwrite, and each is a scar this file already carries.

- **The bot allows itself before it denies anybody** (rc.52). A channel overwrite applies to the bot
  like anyone else unless it is Administrator, so denying `@everyone` first takes the bot's own
  access away and every call after it is refused 403 - leaving the room denied to everyone with no
  allow to put back.
- **It reaches the channels that already exist**, not only the ones a run creates (rc.59, found in
  the file that provisions them). A category overwrite is inherited only by a channel whose
  permissions are synced to it, and both anchors carry an `@everyone` overwrite of their own - so
  the category *and* each channel is gated in its own right.
- **The overwrite is merged, never replaced.** `set_permissions(target, **perms)` builds a fresh
  `PermissionOverwrite` from its kwargs, and the `@everyone` overwrite on both anchors is
  `send_messages=False` - the read-only anchor rule. Writing a bare `view_channel=False` over it
  would have left them hidden and, to everybody holding the role, **writable**: the gate quietly
  undoing the thing the channels are for. The Discord harness asserts that directly, beside the
  allow and the deny.

**A third generated name is when the name family has to be gated.** `realm_presence_role_name` falls
back to the bare `world_name` when a hub carries no `display_name`, and `_realm_access_role_name` is
that same string - so a fifth realm hub written without one would generate one name for two gates,
`discord.utils.get` would hand both the same role, and nothing would error: the access gate would
start following the character's location, and a cultivator who walked out of a capital would lose
sight of that world's news feed. `docs/TODO.md` recorded that trap when it planned this role and
said adding a third name is the moment to gate it.
`test_the_role_names_never_collide.py` walks whatever `REALM_HUBS` carries, so a fifth hub fails the
day it is added - and it drives the fallback itself, because a gate that asserts a collision cannot
happen without showing what one looks like is asserting a hope.

**This is advertising, not a bound.** The role decides what a sidebar shows and nothing else; the
engine's own refusals are still the only refusals, exactly as v1.0.9's curriculum states about
itself. Discord layout stays the dashboard's to own, so the gate runs only behind `create_missing`
and the `/admin` slash path is still validate-only.

### The curriculum the sweep could not see (v1.0.12)

**Found by running the playtest**, which had not been run since v1.0.8. It went red on **100 of 345
steps**, and every one of them was the same fault.

`playtest_discord.py`'s sweep presses every leaf of every hub and holds one thing about each: it was
drawn and answered, or the panel hid it and printed its own `🔒` lock line saying why. Two states.
v1.0.9 added a **third** - a door the curriculum has not introduced yet, which prints **one collapsed
line for the whole page** and no line of its own - and nothing told the harness. So `press_leaf`
found no button, looked for a lock line that does not exist, and failed: 97 leaves across 28 pages,
plus the final coverage step (`pressed ∪ locked ∪ deferred == live`), plus one earlier step that
depended on a door the curriculum now holds back (`travel / Realm Capitals`, floor 1).

**`test_playtest_coverage.py` was green throughout**, because it only ever asked about the
*deferral* set. A gate that proves coverage has to be able to see a leaf going uncovered, and this
one could not - rc.47's shape, in the gate whose whole job is that.

**The fix is not to teach the sweep to count a held-back leaf as covered.** That would trade a
blindness for a worse one: 97 leaves would stop being pressed and the run would go green saying so.
v1.0.9 states that gating is **advertising, never a bound**, so a harness proving *wiring* must not
be stopped by it. The player is raised past the curriculum's own ceiling before the sweep, and the
ceiling is **read off `feature_unlocks`** rather than written down, so a deeper floor authored later
raises the harness with it. Both halves ship together: the curriculum is asserted first, at realm 0,
on the page the roster says holds the most back - because a harness that gated past it and never met
it would be the same fault wearing the other hat.

The gate asks three things now, and the third is the one that would have caught this: the harness
must raise the realm, must read the number off the roster, and must do both **before** the sweep.

### An id too big for a float (v1.0.12)

The step that raises that realm is what found it. `admin.player.set_realm` answered **"character not
found"** about a character the panel three lines above had just drawn, with its name and realm on it.

`decodeMap` is `json.Unmarshal` into `map[string]any`, which turns every JSON number into a
**float64**. A Discord snowflake is about 1.4e18; float64 carries 2^53 ≈ 9.0e15 exactly. So
`1456074443989188610` decoded as `...608`, and **every id an action's payload named was off by a
digit or two**.

**Only the GM's console was affected, and that is why it survived.** `ActionRequest.ActorID` is a
typed `int64` field, and `encoding/json` parses a number straight into one with no float in between -
so every player action, which addresses the *actor*, was always exact. What goes through `decodeMap`
is the **payload**, and the operations that carry a `user_id` there are `admin.player.set_realm`,
`karma`, `teleport`, `grant`, `set_sect`, `adjust_item`, `erase` and the rest of the console: fourteen
call sites in `world_ops.py` and `inspect_sim.py`, every one of them sending `member.id`.

**The reader was already correct.** `storage.ParseInt` has carried a `case json.Number` since it was
written and **nothing in the tree could ever produce one** - the decoder never handed it the type it
was written for. `decoder.UseNumber()` is the whole fix, and no reader changed: `ParseInt` takes the
case it already had, and `stringField`'s `fmt.Sprint` prints a `json.Number` as its own digits. That
is `npc_consignments` (rc.28) exactly - *"Nothing downstream changed, because every reader was already
correct. Only the value it hinged on had to become one the table can hold."*

**Why nothing caught it.** The Discord sweep answers every member picker with a second member who has
*no character*, deliberately, so nothing mutes or erases the player the run walks - and a member with
no character is refused by Python before the engine is reached, with the same sentence a corrupted id
would produce. The engine's own admin tests seed **user 42**, which a float holds exactly. A fixture
that cannot fail the way production fails, one more time, and the thing that told the difference was
a harness asking the engine to act on a real snowflake.

`snowflake_payload_test.go` seeds a character at `1<<53 + 1` and at a real Discord id and drives the
lever through the production dispatch; its drill prints `character not found`. It also holds the
audit row's `target`, because an audit trail naming an id nobody holds is worse than a refusal - it
says the action landed on somebody.

### A panel stays open as long as it is told (v1.0.12)

Asked for in play: *"can we skip the 15min wait time for reopen"*. A hub panel went quiet after
fifteen minutes and swapped its controls for a **Reopen** button. The mechanism is right - discord.py
holds a live view in memory until it times out - and fifteen minutes is wrong: a long time to hold a
view open and a short time to read a page, go and do something, and come back to it.

`HUB_PANEL_IDLE_MINUTES` is the setting and `0` means a panel never expires. Zero is deliberately
not the default: it costs one held view per panel ever opened, for the life of the process, which is
fine on a small server and is the operator's call rather than presentation's. The module default is
the old fifteen, so a **missed registration is the behaviour this started from** rather than a panel
that never expires - a presentation default failing towards *never* would leak. It is injected,
because `test_bot_package` puts `hubs` and `runtime` in one tier and refuses an import between them,
which is the shape `feature_unlocks` and `describe_era` already use.

**The shipped default went out at 120 and was put back to 15 in v1.0.13**, on the owner's call; why
is under "The number that was not the number" below. What the gate holds is deliberately *not* which
number ships - that is a decision, and a gate pinning it would fail exactly when the decision is
taken again, which is the one time it should stay green - but that nothing restates it.

**The number was written out five times** (`hubs.py` twice, `surface.py`, `admin/world_ops.py`,
`commands/support.py`), and the gate forbids a panel view carrying its own.

### Five readers of a tuple that could have been a name (v1.0.12)

`register_command_surface` added ten roots to the command tree from an **inline tuple inside the
function body**. rc.43 made the right call about it - *never copy the tuple* - and being inline meant
the only way to obey was to parse this file's source, so **four places each did that their own way**:
`test_commands_reach_a_player.py` (rc.43), `test_live_auctions.py` (v1.0.9),
`test_hint_paths.py` (v1.0.10), each with its own "did the read find anything" self-check because a
silently-empty walk would make every assertion after it vacuous. And `playtest_discord.py` gave up on
reading it at all and asserted `9 + len(_HUB_COMMANDS)` - a count, which went stale the day v1.0.9
added `/locked`, and which nobody saw for three releases because a harness is a script and not CI.

`surface.TREE_COMMANDS` is a module constant now. Every reader is an import; none can come back
empty; a new root reaches all of them by construction. The harness asserts the **set** rather than a
number, so what the tree registers and what it expects cannot differ.

**There were five, and the fifth is the one worth the section.** This paragraph originally said four,
and the release shipped believing it - then the first full run of the suite on a real toolchain went
red on `test_chat_monitor_contract.py`, which held *"a monitor must not be a typable root command"* by
**regex over the concatenated bot package**, matching `for name in ("begin", …)`. Lifting the tuple
out of the `for` left that pattern matching nothing, and its own assertion said so:
*"could not find the root command registration tuple"*.

It is the perfect instance of what the section claims. It was invisible to every count because it is
the only reader that does not parse `surface.py` - it reads the package as *text*, from a file about
the chat monitor, so nothing pointed at it and no search for "readers of the tuple" would have found
it. **A fifth way of implementing "never copy" existed precisely because there was no name to
import**, and the release that finally gave it one is what surfaced it. Its drill is the release
itself: revert `TREE_COMMANDS` to the inline tuple and this test is the one that goes green again.

The lesson is narrow and worth stating: *never copy* has a cheaper answer than *parse the source*
whenever the thing being copied could simply have a name. Three releases spent implementing the
expensive answer five ways, and only the last one could be counted.

**And a gate reported the interpreter rather than the tree.** `test_bot_package`'s
`test_every_bot_module_resolves_every_global_it_reads` named `__conditional_annotations__` as a
global `app/bot/admin/bugs_forum.py` reads and does not define. That is **Python 3.14** (PEP
649/749): the interpreter synthesises it in any module whose annotations are deferred and
conditionally defined. Nothing in this tree is at fault, and the gate had simply never met a 3.14 -
the container ran 3.11 and the other machine 3.13. It is in `BUILTINS` beside `__file__` and
`__name__` now. A gate that enumerates what the language provides has to be told when the language
provides more, which is the same class as a fixture that cannot fail the way production fails: the
environment was never part of what it was checked against.

### A disabled control is not a question (v1.0.13)

Once v1.0.12 raised the player past the curriculum's ceiling, `/battle challenge` **resolved** for
the first time in the harness's life - every previous run's note for that leaf is the target picker's
own prompt and no run ever began a battle - and it immediately failed with SimCord's *"That component
is disabled - a real user could not interact with it"*. Which of the raised player's differences
reached it is not recorded and is not the finding; that a leaf can be pressed for releases without
ever resolving is.

A resolved challenge starts a battle and posts a `BattleView`, whose `BattleTechniqueSelect` and
`BattleRecoverySelect` are `disabled=not available`: a cultivator with no Law techniques and nothing
to drink gets two dead pickers carrying one explanatory option each. `answer_generically` walks an
action's input steps by taking the first select on the **result's** message - and a result may carry
controls of its own, which is precisely what it could not tell apart. SimCord's refusal was correct
in both directions: a real player could not click it either.

**The tell was in the payload all along**, as `disabled`, and the sweep never read it. The skip is in
`select_by_placeholder`, the one helper both answerers reach, rather than copied into each scan - and
it reaches the scripted answerer too, which would have failed one step later and less legibly, with
*"nothing to answer the picker 'Use a Law technique' with"*. A disabled **leaf button** is
deliberately still a failure: that one means the panel timed out, which is a finding rather than a
control explaining itself.

**And this is rc.58's lesson arriving on the Discord side.** That release tightened the engine
harness's coverage rule from *called* to **resolved**, because `law.technique` was driven only into a
designed refusal and its capstone had been broken for twelve releases. The leaf sweep counts a leaf
as covered when it was pressed and answered - and a leaf that has only ever been pressed into a
refusal has had only its refusal proved. Nothing gates this: raising the realm is what reached it,
and what the next such leaf needs is the same thing, a player who can actually do the thing.

### The number that was not the number (v1.0.13)

v1.0.12 took a bare `timeout=900` out of five production files and gated a panel view against
carrying its own. Its drill then found a **sixth**, a literal pinned as a *string* in
`test_gui_integrity.py`, which that gate had walked past because it swept production only - a gate
that cannot see the thing it forbids (rc.47), one directory over. Both are in that release. The
seventh and eighth are here, and they say different things.

**The seventh was never spelled.** `scripts/playtest_discord.py`'s quiet step jumped the clock
**901 seconds** - not the number at all but an *encoding* of it, one second past a deadline stated
somewhere else - so no search for `900`, and no gate reading production for a literal, could ever
have found it. Raising the default to 120 minutes left it moving a panel an eighth of the way to its
deadline and then reporting that the panel would not expire.

**The window is a setting, so the harness pins one and the step reads it back.** `PANEL_IDLE_MINUTES`
sits at the top of `playtest_discord.py` with `HEALTH_PORT` and the workers' off switches - the
environment block that is set before the bot is imported - and `quiet()` asks `panel_timeout()` what
it came out as. That is the whole rule: the run states the window once, in the place a run states
things, and no step restates it.

**Both bounds on that number were measured, not chosen, and each is a rule of its own.** Too long and
the *jump* is the cost: waiting a two-hour window out wakes every periodic worker for two hours
of virtual time, and the step went from instant to minutes still running - for no proof the unit gate
does not already give about the shipped default. Too short and the panel never settles: at one minute
the run printed `BaseView.__timeout_task_impl: unknown wait (Future)` with *163 recognized waits
parked*, which is rc.35's finding exactly - **a bot-owned wake near enough to count as runnable is a
settle that never completes** - so the failure is not even in the step, it is in the `open_hub` before
it. In between, the sweep's own constraint: **long enough that a page is never expired out from under
itself**, because the leaf sweep opens a page once and presses every leaf on it, and an expired panel
disables its controls - which the sweep reports as a failure, correctly, and which is the neighbouring
finding in this same release. Fifteen is what the harness in fact ran against for twenty-six releases
before the setting existed.

**The eighth was spelled, and it was in a gate.** `test_playtest_gate.py` proves the harness still
drives each loop by pinning a marker string per loop, and its marker for this one was the literal
`advance_time(901)` - so correcting the step turned that gate red. Which is the v1.0.8 lesson
exactly: **a gate that pins how a rule is *written* rather than that it holds fails precisely when
the rule is corrected, which is the one time it should stay green** - the same call v1.0.9 and
v1.0.10 each made for a hand-copy of the command tree's tuple, and v1.0.12 for the fifth. The marker
is `advance_time(` now, which is what that list is actually for; whether the jump is read off the
configured window belongs to `TheHarnessWaitsTheConfiguredWindowOut`, and a fact asserted in two
places is free to disagree.

**The ninth and tenth were in one line, and they are the only two a player ever read.**
`ExpiredPanelView`'s card says *"This panel went quiet for fifteen minutes. Reopen it here; any tap
keeps a panel alive another fifteen."* Every gate written for this number swept **code** - the
`timeout=` keyword a view is built with - and this is prose inside an f-string, so none of them could
see it. So v1.0.12 made the window configurable and, in the same release, made that card wrong for
everybody: at the 120 it shipped, a panel waited two hours and told its owner it had waited fifteen.
That is rc.56's finding exactly - a panel promising something the tree does not do - and the
mechanism is the same one this file keeps recording: **a promise a setting can falsify is a promise
nobody is holding.** The card reads `panel_idle_minutes()` now, and
`test_the_expired_card_never_restates_the_window` walks `__init__`'s statements without its
docstring (rc.52) for a spelled-out number.

**Which is also why the default went back to 15** (on the owner's call, and this is the honest
account of it): fifteen is what the card said, what the README said, and what the harness had run
against for twenty-six releases, and 120 was one number changed against three restatements nobody
had found yet. With the card derived, the default is free to be whatever an operator wants - so the
gate holds the restating and deliberately not the number, and `docs/CONFIGURATION.md` says plainly
that 120 is a comfortable page-and-come-back window for anyone who wants it.

**A restated constant need not be spelled to be a copy**, and that is why the seventh was invisible
while the eighth fell out of an ordinary run. The gates are narrow on purpose - `advance_time` may
not take a numeric literal, the harness must call `panel_timeout()`, and the expired card may not
spell a number of minutes - because the class is wider than any gate: what they can catch is a
number written down where a window should be read.

### The rule was checked against its own examples (v1.0.13)

**Found by playing**, and reported as *"I can view only like half the menu"* - then, precisely:
*"I cant see the options for temper"*, *"Same for beast"*, *"Same for dantian refine"*. The panel
pasted with it is the proof: `/cultivation → Path` printing **2 actions** and
*"🔒 6 more doors here open as you cultivate"*.

At Body Tempering a character saw **109 of 248 leaves**. Half the menu was not a figure of speech.

**Nineteen status reads were held back**, and all three statements of the rule forbidding that are
in the tree. rc.32 set the limit the curriculum inherits - *"never a status read or the door into
the system, because a road nobody can see is a road nobody learns exists"*. v1.0.9's CLAUDE.md
section said **"Every status read stays open at realm 0, deliberately and against the temptation to
count them as noise"**. `test_the_curriculum_opens_as_you_cultivate.py` quoted rc.32's limit **in
its own docstring** and tested eleven other things.

**And the fourth statement is the one worth seeing.** `author_feature_unlocks.py`'s `LEAVES` table
carries the comment *"A status read is never the thing that overwhelms anybody… Every page's own
status stays"* - and then, **in that same block, under that same comment**, eleven entries set one
to 1 or 2: `territory status`, `war status`, `caravan status`, `boss status`, `hunter status`,
`blackmarket status`, `fate status`, `bond status`, `crime status`, `family house status`,
`artifact status`. Eight more - `dantian status`, `party status`, `formation status`,
`merchant status`, `duel status`, `realmhub status`, `sect discipleship status`,
`sect manor status` - were never listed at all and inherited a page floor of 2. The rule and its
violation were adjacent lines.

**Why nobody saw it is the whole finding.** The five status reads CLAUDE.md names as examples -
`sect status`, `beast status`, `abode status`, `innerworld status`, `secretrealm status` - are
**exactly the five that were written at 0**. The claim was checked against the cases it cites and
never against *"and the rest"*, which is where all nineteen lived. That is rc.47's shape reached
from a new direction: not a gate that cannot see what it forbids, but a *rule whose examples are
drawn from the half that holds*.

**The fix is a rule, not nineteen zeroes**, because a list is precisely what drifted.
`is_status_read` is one predicate and `build()` forces 0 through it, so a number written beside a
status read in the table is now ignored rather than obeyed - the twentieth one somebody adds cannot
repeat this. `test_the_generator_forces_it_rather_than_listing_it` drives that behaviourally: it
sets `dantian status` to 2 in the table and requires the built roster not to carry it.

**Three pages open at realm 0**, on the owner's call, and each for its own reason rather than as a
band. `cultivation / Path` is what you were born with - this tree already called that *identity,
not a system*, and `aptitude root` was sitting at 0 beside six locked siblings, so the page said
"here is your Common root and your Moon Serpent bloodline" and hid Temper, Harmonize, Evolve and
Awaken. `cultivation / Qi Body` is **named on the card a realm-0 player reads every session** -
`🩸 20/108 meridians` - so the curriculum advertised a number and hid the one lever that changes
it. And `beast / Companions` was the one system the first hour had to be told about rather than
shown: `beast status` was open and reported nothing, because everything that makes a beast exist
was behind Qi Refining.

Body Tempering now shows **142 of 248**. The deep end is untouched - the black market, caravans,
boss raids, territory, war, a house, a personal world, Samsara and Perfection still wait at 3/4/5 -
because the complaint was never that the game had too much in it.

**What is deliberately not gated is which pages are open.** That is a decision the owner may take
again, and a gate pinning `cultivation / Path` to realm 0 would fail exactly when it is taken -
v1.0.8's lesson, and the same call the panel-idle window's gate makes one release over. What is
held is the rule the examples hid: no leaf whose path ends in `status` may wait for a realm.

**The drills.** Restoring `dantian status` and `war status` to realm 2 in the shipped content names
both with their realms; letting `build()` read the table again prints *"the authoring script let a
status read be gated by writing a number beside it"*; and blanking `is_status_read` prints *"the
generator no longer recognises a status read; the gate is broken, not the tree"* - **before** the
assertion it would have made vacuous.

### The same rumour, once per room (v1.0.13)

**Found by playing**, and the page is the report:

> 🗣️ **Rumours in Ashenwall City** — as Landlady Bo Tan tells them
> • **Xie Kormaq discovered Ironbanner City** — …charted a route to Ironbanner City.
> • **Xie Kormaq discovered Ironbanner City** — …
> • *(three more of the same)*

**The writer is idempotent and was never the problem.** The discovery is recorded with
`source_key=f"location_discovery:{user}:{location}"` and `record_world_history_event` is an
`INSERT … ON CONFLICT(source_key) DO UPDATE`, so there is exactly **one row**. It was printed five
times.

`get_structured_world_history`'s relevance clause is an **OR** —
`location=? OR related_user_id=? OR actor_key=? OR target_key=?` — and `city_rumours` called it
**once per place**, with `user_id=` filled in:

```python
for place in [city, *parts]:
    events.extend(await DB.get_structured_world_history(location=place, user_id=…, limit=6))
```

Ashenwall City has seven parts, so that is eight queries, and every row about the asking player
came back from **all eight**. The row's own `location` is *Ironbanner City* — nowhere near
Ashenwall — which is why it could appear at all: it was never matched on the place, only on the
player.

**The second half is why the fix is not a `set()`.** That function's docstring says it
*"deliberately returns a superset. The RAG retriever performs the final viewpoint/visibility check
so one code path owns knowledge safety"* — and the rumours page was a **second consumer that
performed neither check**. `rag.py` merges into a dict keyed on `history_id` and drops `hidden` at
its line 265; this page did neither. So the landlady could repeat a **`participant`** row the
player alone was party to — which is exactly what was reported — and a **`hidden`** one:
`npc_deeds` writes an unwitnessed robbery and an unwitnessed contraband drop at an NPC's own
location, which is a city, and the tree's rule for those is that *the world really does not know*.
Their prose is already anonymised, so what leaks is the event's existence rather than a culprit's
name; it is still a row nothing was ever meant to surface.

`rumours_a_city_has_heard` is the one selection now — public only, distinct by `history_id`, newest
first, bounded — and the call stops naming the player, which is the whole of why the duplication
existed. **A rumour is what the city has heard**, so a row about somewhere else, or one only the
player was party to, is not one.

**The gate holds the rule rather than the page.** It compiles the helper from its own source rather
than booting the bot for one pure function, and asserts a plain public row survives before
asserting anything else (rc.57). Four drills: asking about the player again prints
`['line 1423: user_id=']` with the reason; dropping the visibility filter names the `participant`
row; replacing the dedupe prints *"one row came back 8 times… which is exactly what a player saw
printed five times"*; and blanking the helper fails the self-check first.

**What is deliberately not changed is `get_structured_world_history`.** The OR is correct for RAG,
which is what it was written for and which filters afterwards. Narrowing it there to fix a page
would be a rule moved out of the one path that owns it — the opposite of what its docstring asks
for.

**And an existing gate was holding the fault in place, which the full suite is what found.**
`test_city_life.py`'s `test_rumours_come_through_the_one_viewpoint_gate` asserted the call's exact
literal text — **including the `user_id=interaction.user.id` that was the bug** — so correcting the
page turned it red. It is the v1.0.8 lesson at its sharpest: a gate that pins how a rule is
*written* rather than that it holds fails precisely when the rule is corrected, which is the one
time it should stay green, and here the spelling it pinned was itself the defect. Its **name** made
it worse: *"come through the one viewpoint gate"*, over a call that performs no viewpoint check at
all — the one path that owns that check is RAG, which this page was not. It is
`test_the_unfiltered_reader_never_feeds_rumours` now and keeps only the narrow thing it really
held, because two statements of one rule are free to disagree and the weaker one is what produces
the false verdict.

### The quest named the one command that could not advance it (v1.0.13)

**Found by playing**: *"Even though I've done multiple successful hunts after getting the quest
it's not getting completed."* The journal read

> ▫️ Come out of one fight standing - **/world → Act → Hunt** 0/1

The objective's type is `combat_win`, and its **only** reporter in the tree was `_finish_battle` in
`battle.py`. `/hunt` recorded **no quest progress at all** — not `combat_win`, not anything — so the
beginner path's fourth stage named the one command that could not advance it.

**Three gates stood here already and none could see it.**
`test_quest_objective_reporters.py` holds that every type in `OBJECTIVE_TYPES` has a reporter — and
`combat_win` had one, in `/battle`. It holds that no reporter names a type the vocabulary lacks, and
that no reporter speaks before its command answers. **Every one of those is about the type.** The
label is the only part a player ever reads, and the fact that decides whether they can finish the
quest — that the command named and the command reporting are the same command — was held by nothing.
That is rc.47's shape with the emphasis moved: not a gate blind to what it forbids, but a rule
nobody wrote down, sitting beside three that look like they cover it.

**The hunt reports `combat_win` now, rather than the label being re-pointed at `/battle`.** The
stage's own description is *"find out what happens when something does not want you there"*; a
failed hunt costs nothing (*"No permanent injury or item loss is applied"*) while a lost battle
leaves a cultivator on zero vitality; and the first hour is not where that belongs. The report is
written after the engine has decided the hunt landed (rc.28) and before the command answers, with
the announcement after the reply (v1.0.5).

**The drill found two more quests the same missing wire had stopped.** Taking the report back out
names `beginner_road`, `errand_forging_cores` and `errand_formation_ward` — two household errands
also asked for a hunt, so three quests were unfinishable, not one.

**And the gate's first run found a second instance, authored the same way.** `beginner_town`'s
`trade` objective was labelled **/economy → City Shops → Browse** — and Browse is a *read*.
`shop_buy` and `shop_sell` are what report `trade`. The label is `Buy` now; that one is content,
because a read must not claim to have traded.

**Three of this gate's own runs were resolver mistakes, and each is worth the line.** A leaf name is
ambiguous across hubs — **Enter** belongs to both `family` and `realm`, so a label-only key sent
`/family → Enter` to the secret realm's door. It is ambiguous *within* a hub — `cultivation` carries
**Cultivate** on its `Cultivate` page and again on `Body` — so the value is a set and any handler of
that name reporting the type satisfies it. And a reporter need not sit in the handler: `/craft`'s
lives in `_run_crafting`, so the scan closes over the module's own calls, which is v1.0.5's
`_report_trade` lesson arriving from the other side.

### The pace is written three times, and only one of them is served (v1.0.13)

**Asked for**: the wait between cultivation sessions cut to thirty minutes. It was **180**, not the
two hours it was remembered as, and rc.56 had already made it the engine's - so this should have
been one number in one table. It is three, and the third is the one that decides what a running
server actually serves.

`actionCooldowns` in `cooldown_rules.go` is the statement a reader finds. `.env.example` carries a
second, which `migrate_env.sh` copies into a new `.env`. And **`docker-compose.yml` carries a third
as its own fallback** - `${CULTIVATE_COOLDOWN_MINUTES:-180}` - which exists because the engine
service takes an explicit `environment:` allowlist and no `env_file`, the rc.39 finding that made
`WORLD_TIME_SCALE` dead on arrival. Compose therefore always passes *something*, so the engine's own
default is never reached on a composed stack: a pace changed in Go alone would have reached a
`go run` and **no server anybody is running**, which is rc.43, rc.46, rc.49, rc.50, rc.51 and rc.59
wearing a seventh hat. All three move together now, and
`test_the_three_statements_of_a_default_agree` holds them equal for every wait rather than for this
one.

**Three gates pinned the value rather than the rule, and all three went red on a retune.**
`test_the_defaults_are_what_python_used_to_send` said exactly what it was for - *"a live world must
not change pace because ownership moved"* - and that reason was spent the release it was written in:
ownership moved in rc.56 and the numbers have been the engine's ever since, leaving a gate whose
only remaining effect was to fail when the owner exercised the ownership it was celebrating. The Go
half did it too, asserting `cooldownSecondsFor(cooldownCultivate) == 180*60` inside a test about
*ownership*; what it is really guarding is that the served wait is the table's rather than a
handler's old five-minute fallback, which holds at any value, so it keeps the floor and drops the
number. That is v1.0.8's rule, which v1.0.13 had already applied twice in this release - to the
panel-idle window and to the rotation's allowlist entry - and this is the third and fourth.

**The third is the one worth reading, because it had already been fixed once for this exact
reason.** `TestSeclusionIsPacedLikeTheStageItFills` bounded a game day of seclusion at
`1 <= sessions <= 5` of a hand-sat session, and the comment above it explains at length that the
*previous* bound was wrong because *"a number that only held because the count it bounded, 1.2
sessions a game day, happened to be 60% of active play at the shipped time scale"*. Its replacement
made the same mistake one layer up: a session count is a statement about the cooldown, so
`<= 5` only held while a session cost three hours. At thirty minutes six times as many sessions fit
in the same real time and a day behind the door is worth seventeen of them - **the share unchanged
and the count six times larger**, which is rc.56's derivation working rather than breaking. The
band is computed from `seclusionSessionsPerGameDay` now, so what is held is that the payout really
is the pace times that count; the share itself stays held across every scale next door, and is
deliberately not restated here. A rule can be fixed, have the fix explained in a comment, and be
broken again in the same statement by the same reasoning one level out.

**And two of the three waits deliberately did not follow it down.** `cooldownAptitude` and
`cooldownDaoDual` were *paced with* cultivation - three entries sharing `CULTIVATE_COOLDOWN_MINUTES`
because Python had sent `max(300, cultivate)` for them since before rc.56 moved ownership - and on
the owner's call they keep **180** while ordinary cultivation drops to 30. The reason is what each
one is: an `aptitude.evolve` is a climb up the six-rung root ladder, 2d10 against `13 + idx`, costing
stability on a failure and risking a forced mutation at margin <= -7, and since rc.55 the rung
reached prices 0.88x-1.34x cultivation and -1 to +3 on every breakthrough for the rest of that life.
A dao partnership is the same shape at two people's expense. Ordinary cultivation getting faster is
not a reason for the rare, costly things to.

**Unsharing the number meant unsharing the key, and that half cannot be skipped.** Three defaults
behind one environment key is fine while they agree; the moment they do not, an operator who sets
that key silently moves all three back together and **no value of it restores what shipped**. So
`APTITUDE_COOLDOWN_MINUTES` and `DAO_DUAL_COOLDOWN_MINUTES` are keys of their own, with their own
`.env.example` lines and their own compose passthroughs - and the gate refuses the bad shape by
name: entries sharing one key must ship one default. Its drill prints
`CULTIVATE_COOLDOWN_MINUTES is shipped as both 30 and 180 minutes`.

**What moves with it, deliberately.** `seclusionSessionsPerGameDay` divides by this wait, because
rc.56 made a retreat a *share* of active play rather than a count of sessions. So a retreat stays
125% of active cultivation at thirty minutes exactly as at 180, and its absolute rate rises sixfold
with the pace - which is the derivation working, not a second thing to tune. The calendar it turns
into is in `docs/CONFIGURATION.md`: about a hundred sessions a realm, so roughly two real days of
active cultivation where it used to be ten.

**And the one thing this cannot reach is a server already running.** `.env` is never edited by an
upgrade - that is what `migrate_env.sh` exists for, and it keeps values already set - so an operator
whose `.env` carries the old `CULTIVATE_COOLDOWN_MINUTES=180` keeps three hours until they change it
themselves. The new default is for a fresh install and for anyone who removes the line.

### The allowance nobody could look up (`character.reset_status`, v1.0.13)

**Asked for**, after a question this file could not answer: where a GM sees how many times a player
has reset their character.

`character.reset` has reported `resets_used` and `resets_remaining` in its own reply since v1.0.1,
and **that reply was the only place either number has ever appeared**. A player learned how many
chances were left by spending one - the confirm step is the generic red "Are you sure?" from
`_DANGER_ACTION_WORDS` and names no count - and a GM could not look it up at all. The record was
never the problem: `event_log` carries one `character_reset` row per reset, kept out of the sweep by
`characterResetKeep` precisely so the bound survives the action it bounds, and its payload already
held the abandoned life's name, path, root, realm and phase. It had **no Python reader anywhere in
the tree** - the string occurs twice in `app/`, in the table list and the DDL that creates it - no
dashboard view, no `/admin` panel and no API field. The thing was recorded, complete and legible,
and nothing looked at it: the shape `/learn` (rc.43), the quest journal (rc.46) and the peach
(rc.50) each had.

**Why it is an engine query and not two SELECTs.** The obvious fix is a `COUNT(*)` in the bot's `DB`
and another in the dashboard's own query session, which is how both planes read every other table.
It is refused because of what the count is made of: the row it filters on is `characterResetEvent`
and the bound it is read against is `characterResetAllowance`, **a Go string and a Go constant**, so
that fix would put four new copies of two engine facts into presentation. A surface holding its own
`3` reads correctly the day it is written and tells a GM *"one left"* on the day the engine refuses
- rc.46's rule seen from behind the counter, and exactly what v1.0.11 took the spiritual-root ladder
out of the browser to stop. The precedent is `secret_realm.rotation`, added for this same dashboard
with this same reasoning written on it: *"three copies of one rule, and they had already parted
company."*

So `character.reset_status` is one door on `authoritativeQueries`, and neither surface knows how the
answer is made. Three things about it are decisions rather than mechanics:

- **The subject rides the payload, never the actor.** Every other read on that allowlist answers
  about the caller; both callers here are asking about somebody else - Discord's actor is the GM who
  typed the command, and the dashboard asks as actor 0, which is not a cultivator. An absent
  `user_id` is therefore a refusal rather than a quiet answer about the wrong person.
- **It reads no `characters` row**, and that is the case the lever exists for: an account that reset
  and has not begun again has none, so a read joined to the sheet would answer "nobody" about
  precisely the person a GM is looking up. The Discord side carries the same rule one level out -
  `/admin player inspect` used to stop at *"That member has no cultivation character"*, and now
  prints the restarts on that branch too.
- **An engine that does not answer says so.** Both surfaces degrade to *"unknown"* and an em dash
  rather than to a zero, because a zero here reads as *"never reset"* and a GM cannot tell it from
  *"nobody replied"* - the `engine —` footer v1.0.8 found in this same dashboard shell.

**The gate's own first run found a fault in the fix.** The rule it holds is that neither surface may
supply a number of its own, and the Discord line was written
`int(status.get("reset_allowance", 0))` - so a partial block would have printed **"2 of 0"**, a
bound nothing enforces, from the very function written to stop that. It asks whether the field is
absent now, which is v1.0.1's rule and is also the only correct question here, since `resets_used`
of 0 is the commonest real answer there is.

**And the gate's first run flagged the file it was written for**, because the docstring explaining
why nothing may read `event_log` names `event_log`. That is rc.52 arriving in the same session
again, and the reader that fixes it already existed in `test_playtest_gate.py` - so rather than a
second copy, `code_only` moved to `tests/support.py` and both gates import it. v1.0.12's lesson:
*never copy* has a cheaper answer than *parse the source* whenever the thing being copied could
simply have a name.

**What the load-bearing test measures is not the count.** A test asserting "2 of 3" passes just as
well against a surface printing a 3 of its own, which is the rc.47 shape; so the Go gate holds the
status against **a real reset's own reply** (`reset_allowance == resets_used + resets_remaining`, as
the action accounted for them) and the Python gate makes a fake engine answer an allowance of
**five**, a number this tree does not contain. Its drill prints *"the panel did not report the
allowance the engine gave it, so it is carrying a copy"*.

The harness drives it where it matters rather than anywhere: section 21c already resets QUITTER, so
the read is taken at the one moment that account has no `characters` row, and it compares the read
with that reset's own reply instead of with a number written down in the script. Its drill prints
`['character.reset_status'] != []` from the coverage gate.

**What is deliberately not built is the player's own view.** `/reset`'s confirm step still names no
count, so a player still learns the number by spending one. That is a decision about how much a
warning should say, not a wiring, and it is in `docs/TODO.md` with that reason.

### Zero hops is a distance, not a missing value (v1.0.13)

**Found by playing**, inside an apothecary: `/travel` refused with *"Travel failed: the shop door
opens onto Azure Crown Imperial City"* — naming the one destination the engine allows from inside a
shop, which the picker did not offer.

**The engine was right and had the city all along.** `knownLocationsTx` says so in its own comment:
standing in a shop *"the city is known, its roads, and every gate and district of it"*. What dropped
it was one operand in the picker's ordering:

```python
rows.append((name, "🌀", …, 20 + (n or 50)))
```

`n` is the hop count; the city a player is standing **inside** is 0 hops away; `0 or 50` is 50. So
the only legal way out of the shop sorted at 70, behind every road city, and fell off the end of a
25-option Discord select. Measured from the Azure Crown Apothecary it was position 12 of 13 — and on
a character who has discovered more of the world, off the list entirely.

**The rule was known and broken in the same expression.** One operand to the left, the *label* asks
`if n is not None` — the correct question. That is v1.0.1's own lesson, *"ask whether the field is
absent, never whether it is falsy"*, which that release fixed in `playtest_engine.py` and recorded
as a rule about **assertions**; the failure mode it named there was "green until the value happens
to be zero". Here the same mistake was in production, deciding what a player is shown, and it was
only ever wrong for the one place they were standing in.

**The gate reads the operand, not the line**, and its own first run is why: looking for `"hops"`
anywhere in the expression flagged `WORLD.shops.get(…) or {}`, because *shops* contains it. A needle
is not a reader (rc.58). It reads statements without the docstring or the comment that explain the
fix, since both quote the expression they forbid (rc.52), and it drives the real picker from a real
shop in a real capital rather than a fixture city.

### A method can be made where it is sold (v1.0.15)

**Found by playing**, by a player who had just passed the Apprentice examination in Jadewood:

> 🧰 missing materials: Twin Extremes Ice-Fire Fruit x1
> Buy them at a hall of the trade (**/economy → City Shops → Here**) or gather them (**/craft →
> Alchemy → Forage**).

Neither half was true anywhere they could stand. The fruit sits on **no shelf in the game**, and
correctly: it is `auction_interest: special`, and `test_every_shop_is_a_kept_interior_with_a_real_shelf`
refuses auction-grade shelf stock. The forage roll offers it only at `worldTier >= 1`, the Spiritual
World and up. Meanwhile `heart_calming_pill_method` is shelved **only in the Mortal World**, and
`teachRankRecipesTx` hands the recipe to anybody passing the Apprentice examination wherever the
hall stands. **The one world that sold the method was the one world that could never make it.** The
price said the recipe was never meant either: 1,050 stones of fruit into a pill thirteen shops sell
for 13 to 26, while every other Apprentice pill costs about what it makes.

**v1.0.1 looked straight at it and let it go.** Its recipe-cost sweep failed on its first run naming
`twin_extremes_fruit`, and was deleted because the fruit *"is sourced ... resolved off the world
tier"* (see "A method that could not say what it needed" above). Deleting a world-flat copy of
`test_every_item_has_a_source.py` was right; dismissing the finding was not. Three things answered
"sourced" and all three were asking a question with no world in it: that gate greps production for
the id and finds it in the rare pool; `_gatherable_items` in the content gate reads the rare pool
off the Go source and flattens it across all four worlds; and the sweep itself. A source is a
place, and what matters to the person holding the slip is whether it is *their* place.

**The recipe is re-authored rather than the fruit placed**, and both halves of that are forced.
Shelving the fruit breaks the shelf rule above; putting it in the Mortal forage pool hands every
Mortal forager a fifteen-percent shot at a 1,050-stone treasure; and either leaves a pill that costs
fifty times what it sells for. `Heart Calming Pill` asks for `spirit_herb ×3, moonveil_herb ×1` now -
29 stones in, 21 out, the band `Qi Nourishing Pill` already sits in - and the four Mortal tier-1
apothecaries (Greenriver, Jadewood, Moonfen, Riverguard) shelve `moonveil_herb` at the capital's
price scaled a tier down and buy it back, so the hall that sells the slip sells both of its herbs.
Moonveil is the Spiritual World's tier herb, a step above a town's common one, and the Apprentice
examination already teaches the Moonveil Recovery Pill beside this. The fruit keeps its forage slot
and its auction interest; it is simply no recipe's input any more, like `ice_spirit_blazing_grass`.

**The gate counts only sources whose world the content states**: a shelf in that world, a
guaranteed item in a room of a realm standing in that world, the world's own `tier_materials`, and
the tier-flat `forage_materials`. **The forage rare pool is deliberately not counted** - it is a
chance, so a method hanging on it is a lottery, and its world gate is Go code, so reading it here
would be a second copy of an engine rule, which is exactly how `_gatherable_items` came to see one
world where there are four. `test_a_method_can_be_made_where_it_is_sold.py` holds every world that
shelves a method's slip to offering what the method needs, and holds the **first** examination to
the worlds it is sat in. Only the first: Journeyman in the Mortal World teaches the Dawn Lotus
Vitality Pill, which is knowledge ahead of the road rather than a dead end, and whether a hall
should teach only what its own world can make is in `docs/TODO.md` as a decision.

**The drill is kept inside the gate.** `test_the_gate_names_the_recipe_that_found_it` runs the
checker against a copy of the content with the fruit put back and requires the exact finding, so the
gate proves it can see the fault it was written for on every run rather than once (rc.47). Putting
the fruit back in the real content file fails **two** tests - the slip rule and the examination rule
(`['twin_extremes_fruit'] != []`) - and the reader is asserted before it is trusted (rc.57): the
Mortal World must offer `spirit_herb`, `beast_core` and the grotto's `jade_life_herb`.

**And the refusal reads the shelves.** `where_it_is_sold` names, for each material a player is
short of, the hall of their own city that sells it, else the cities of their world that do, else -
when their world sells it nowhere - that it does not and which worlds do; a thing no hall sells
names the realm room that holds it. A household or an inner world belongs to no world, so there it
lists worlds rather than assuming the Mortal one (rc.52's `world_of_location`). The lines are
drawn by a helper that **never raises**, because it runs inside the reply to a refused craft and a
failed inventory read must not cost the player the refusal itself (v1.0.10). The generic sentence
under it gained the hunt, because both entry alchemy recipes want a beast core and no forage has
ever turned one up. The drill removes the lines from the reply and the gate prints the reply
without them.

### A treatment always mends (v1.0.16)

**Found by playing**, and the report is six screenshots of the same line: *"2d10 (3+5) +2 = 10 vs TN
16 — Hard Failure · 28% chance. The treatment fails. The medicine is consumed, but the condition does
not worsen"*, then *"I consumed 5 pills. Failure, Failure and Failure. I can't heal injuries."*

`condition.treat` rolled Insight + Spirit against `10 + 2 × severity`, and the attribute came through
`canonicalAttribute` - which sums **every** active effect, including the row the condition being
treated had written itself. Qi Deviation, Meridian Damage and Dantian Damage each take their severity
off Spirit, and a Soul Wound takes it off Insight and Spirit both. So the stat the cure rolled was the
one the ailment had already lowered, and the TN climbed two a level on top: a fresh cultivator
(Insight + Spirit of 3 to 5) had 15-28% against a severity-3 deviation and 0-3% at severity 5, a
Soul Wound was worse, and each Force deviation raises the one they hold a level. The `+2` in the
screenshot is exactly the sheet's 5 less the deviation's own 3 - and it is what the drill prints
when the skip is taken back out. A failure mended nothing and the pill was spent anyway, so a
condition was a sink with no floor.

**Nothing else lowers a condition's severity** - no rest, no tick, no healer; only this action and
the GM's Clear - which is why a treatment that can fail forever is a condition a player keeps
forever.

**The roll decides how much, never whether.** `conditionTreatReduction` is one level on a failure,
two on a success, three on a strong success (the degree `rollCheck` already names), so a condition
costs at most its severity in pills and the worst dice still mend. The TN is `10 + severity`
(`conditionTreatTN`). And the ailment is left out of its own cure: `canonicalAttribute` takes an
optional `skip ...effectSource`, and the treatment skips `("condition", <key>)`, the same pair its
resolve branch deletes by. **Only that row** - a Soul Wound still dulls the mind treating a deviation,
because a cultivator carrying two injuries should find the second harder to mend.

**A trailing variadic rather than a second function, and the gate is why.**
`modifier_vocabulary_test.go` reads the `allowed` map inside the function literally named
`canonicalAttribute` to know which attributes are fetched; moving the body into a
`canonicalAttributeExcept` would have made `presence` and `heart` look unread and turned a refactor
into a red gate. A variadic keeps all twenty-four callers and the gate exactly as they were.

**The Python gate caught itself on its first drill.** `test_a_treatment_always_mends.py` refuses a
reply that branches on the roll, and its first version looked for the text `"success"` in
`ast.unparse(node.test)` - which quotes with `'`, so against the broken reply it matched nothing and
passed. It reads the string constants in the test now. That is rc.52's rule in its most literal
form: a gate that reads spelling rather than structure passes on the spelling it did not expect.

**The same report carried a second question, and the answer was not a change.** The Qi Nourishing
Pill sells for 11 and buys back for 4. Across all 831 things a shop both sells and buys, the buy-back
is a median third and never above 40%, and no item can be bought in one shop and sold in another
for a profit - so it is the authored rule, and on the owner's call it stays. What the check did find
is recorded in `docs/TODO.md`: made from shop-bought materials, crafting always costs more than
buying the result, and three Mortal recipes sell back for less than their own ingredients. The
owner's answer to that came the same day, as v1.0.17, below.

### A keeper pays a craftsman by rank (`trade_rank_price.go`, v1.0.17)

The owner's call on v1.0.16's second question: the third a keeper pays stays, and a rank in the trade
that **makes** an item adds `tradeRankSellStep` (2) of the shop's own coin per rank above Novice to
it. The trade is read off the recipe that outputs the item (`itemTrade`), so raw materials - which no
recipe makes - keep the third, and each of the thirty-three crafted outputs belongs to exactly one
trade. Only the sell side moves; shelf prices are untouched, so buying stays the money sink it is.

**The ceiling is the whole safety of it.** A sell price that reaches a buy price is a mint: buy off one
shelf, sell to the next counter, repeat. `tradeRankSellPrice` stops one coin short of the cheapest
shelf price for that item anywhere in the same currency - *anywhere*, not this shop, because the loop
runs between two shops - and `TestNoRankTurnsAShopIntoAMint` walks the whole shipped catalogue at
Saint to hold it. Its drill prints *"celestial_mandate_forge pays a Saint 22 for spirit_iron_sword,
and it sells for 21 on a shelf"*.

**What the gate deliberately does not refuse is a craft that pays**, and its first version did. It
also forbade a Saint selling a recipe's output for more than the inputs cost on the shelves, and
failed on the Hearth-Return Talisman: paper and ink for 8, sold for 17. That is not a mint - it costs
a craft action, the roll, trade XP and the shelf's own finite stock - and making crafting pay is the
reason the rank exists. A gate encoding a claim rather than a rule will happily make you change the
rule (v1.0.3's lesson), so that half was cut. Measured instead: four talismans turn a stone or two
from Journeyman up out of shop-bought materials, and one recipe - the Starfall Talisman - already
turned a profit at Novice before this release, which is in `docs/TODO.md` as a content decision.

**The board and the sale are one price.** `shopBuysRows` now quotes per player through the same
`tradeSellQuoteTx` the sale uses, because a board showing what *anybody* gets would disagree with the
sale it advertises - rc.46's rule. Both carry `base_price`, `trade` and `trade_rank`, and the bot only
says which rank lifted the price (`_rank_lift`); it decides nothing.

### The door into a sect (`sect_doors.go`, schema 61, v1.1.0)

Reported in Discord: at a **Major Sect Recruitment** world event a player talked to the Visiting
Elder, pressed him, and was told they were impatient and would not be taken. The owner answered
*"get a recommendation, do quests for an elder of a sect, then travel to the sect for intake"* - and
the next player asked **"What menu?"**. Every link of that road was broken, and each one is a shape
this file already names.

- **The event recruited nobody.** It named no sect; its elder had no `sect_affiliation` and a role
  reading *"Decides who is taken"*, under a panel saying "Use **Talk**". The refusal was the routine
  narrator improvising, and nothing told it that no conversation can admit anybody.
- **No road reaches a sect gate.** All twelve are authored with no `roads` and nothing's roads lead
  to them, so `/explore` - which charts one road out from what is known - can never find one. The
  envoys' hall recorded the sects, said *"their routes are on your map now"*, and wrote nothing a
  route could be read from; `/travel` then refused the gate it had just named. The single path that
  wrote a gate onto a travel list was a successful NPC recommendation.
- **A realm-0 player could not join at all.** Recommendation and Trial opened at realm 1, and both
  take an argument, so a hub press is the only way to reach them - the typed shorthand reads two
  words and these are three. v1.0.9 says *"gating is advertising, never a bound"*, and **a
  curriculum floor on a leaf nothing else can reach is a bound.** Meanwhile `road_to_a_sect`, which
  the beginner path hands everybody as `beginner_lesson`'s `follow_on`, asked for exactly that; its
  only `sect_discovery` reporter was the impossible explore path, so it could never complete.
- **The owner's route did not exist.** All twenty-four elder commissions were members-only and
  nothing raised standing with a sect before joining it; and the recommendation's "+N", shown in four
  places, was never added to a trial roll - `sectTrialActionGo`'s comment said it only unlocked the
  conditional pass.

**The gate was the caller's, which is why this could not be fixed in Python.** The recommendation
wrote whatever `location` its payload named into `character_location_discoveries`, and the trial took
its gate from the payload too - and a known, road-less location is an instant jump on `/travel`. So a
forged payload could put any place in the world on a player's map; making the event a prominent door
would have made that door prominent. `revealSectRouteTx` is the one statement of the knowledge, and
the gate it writes is always read off the catalogue (`sectGate`). An older bot's payload is still
accepted, and a sect it names is heard only to refuse a mismatch - rc.48's rolling-deploy rule.

**Which sect a delegation speaks for is a hash, not a roll.** `recruitingSectFor`: the sect whose gate
the event stands on, else the public, non-hidden sects whose gate is in the event's world, sorted and
picked by `hashString(eventKey+":recruiting")`. Gates carry no coordinates, so there is no "nearest"
for the content to measure. It is **stamped at spawn** into `world_event_npcs.sect_name` /
`can_recommend` and `world_event_nodes.reveals_sect`, so a content edit mid-event cannot change whom a
delegation speaks for, and every insert and read guards on the columns: in the compose stack the
engine is healthy before db-init migrates, and a site spawned in that window is left generic rather
than failing the tick. The trial node's count went from `[1,3]` to `[3,6]` - at severity 3 it held
one unit, so only the first player per event could ever have been shown the gate.

**Outsider standing is 25, and that number is the smallest that does anything.** The trial's TN is
`max(10, 15 - rep/25)` and the recommendation adds `rep/20`, so 25 moves both one step. It is declared
as `seed_json` `{"outsider_standing": 25}` on the twelve tier-1 sect commissions, capped at 25 by the
engine, paid only on a `commission_` key with a `requires_sect` to somebody in no sect, and each is
taken once (`commission.accept` refuses a key held before), so it cannot snowball. The Forge cannot
draft it: the validator builds no `seed` and Forge keys are `forge_`/`quest_` - the `household_standing`
precedent. `rules.open_to_outsiders` mirrors the prefix so the offer ladder never offers what the
engine refuses (rc.46), and `test_the_door_into_a_sect.py` reads the Go constant to hold them equal.
A disciple of another sect is still refused: that is a rival asking to do sect business.

**The rest follows from those.** A running event's cast answers `current_npc_location` - `None` there
reads as "do not filter by location", so a militia captain was talkable from across the world and a
sponsor's presence check could never be met. The recommendation picker lists the cast, because it is
the only way the hub can ask the elder. Both system prompts say no NPC grants, promises or refuses
membership, a recommendation, a commission or a reward, and `/talk` passes `NPC SECT TIES:`. And
migration 61 rewrites the quest's two labels in a running world - in the definition *and* in terms a
player already pinned - leaving a label a GM has edited alone.

**The v1.0.9 floor now reaches one quest further.** `test_the_quest_the_beginner_path_hands_over_is_not_held_back`
walks the beginner path's `follow_on` chain out of the path and requires every objective of what it
hands over to have one reporter open at realm 0; its drill puts the trial back at realm 1 and names
it. `test_a_quest_names_a_command_that_can_advance_it.py` reads `QUEST_DEFINITIONS` now too, which is
how the new labels are held to commands that report them.

**Three of the Go drills broke the build instead of failing a test**, each by leaving a variable
unused - and a drill that does not compile proves nothing about the test it was aimed at. They were
rewritten as a disabled condition (`if member == nil && false`), which is the shape rc.49 says a grep
cannot see and only behaviour can. The harness drives the whole road as its own fifth cultivator,
`APPLICANT`: the hall refused from the street, every public gate named and no other, the road-less
jump landing at the gate, the entry-level work taken and finished for +25, the deeper work refused,
the way in taken once.

### A button is drawn where it works (`LOCATION_GATES`, v1.1.0)

Reported from play: in the birth household, `/economy → City Shops → Browse` answered *"not inside a
shop; find one by exploring a city"*. Asked to hide every such button, the owner chose all of them,
and to keep City Shops **Here** - it never refuses, and it is the door that says where a city's shops
are.

**No new machinery.** rc.32's hidden-actions provider gained a third member, `_location_hidden_actions`,
driven by `LOCATION_GATES` in `PROGRESSION_GATES`' shape, and each hide prints the existing
`🔒 Label — reason` line. `_hidden_actions` reads the character once and hands it to all three - each
used to read its own. What it hides is the leaves refused **only because of where the player stands**:
a shop's counter (`shopAt`), an auction floor (`catalogHouseAt`) and its door (`cityOf`, blanked in a
shop), an inn's long table, protected ground, a shrine, a road-side site, a realm entrance, a sect
gate, a boss lair, an examination hall, the four private prefixes, your own world and property, and
the five seclusion sites. Each asks the engine's own question, found by reading each handler rather
than by guessing, and the engine stays the refusal: a leaf typed directly still reaches it.

**What is not hidden is a decision each time.** A read or the door into a system (`shop here`, the
city's reads, `auction appraise`); a refusal that depends on state rather than place (a merchant's
stock, a beast to tame, a trade to accept); and the three place rules that would need a copy of an
engine formula or a read per refresh - the ghost ground's multiplier, the black market's post, an
array's departure - each hidden only inside a private room, where the answer needs no read.

**The twins are computed a third time.** `test_a_button_is_drawn_where_it_works.py` rewrites
`cityOf`, `shopAt`, the auction door and `sectGate` from the Go, off the raw content file, and holds
the Python twins to them over all 477 locations - v1.0.9's rule, because two wrong halves agreeing is
exactly what a test against the Python alone would pass. **One of its drills stayed green**: dropping
`sectGate`'s hidden check changed nothing, because the one hidden sect carries no recruitment at all -
rc.53's `!ok`, belt-and-braces against today's content. A planted hidden sect with a gate on a real
street is what holds it now.

**Reading every handler found four faults the hides would have inherited.** `caravan.dispatch` planned
from the raw location while travel uses `cityOf`, so it refused at every gate and district of the city
it stood in - v1.0.9's household door in a second handler, fixed in the engine with a Go test from a
real gate. `PRIVATE_LOCATION_EXITS` named `/abode → Leave` as the way out of a sect residence, and
`abode.leave` reads `cave_abodes` while a residence is a `sect_abodes` row. `PROGRESSION_GATES["abode"]`
hid `abode leave` and `abode focus` from anybody owning no property - every invited guest standing in
somebody else's; they are asked by place now, and `abode enter` says where the property stands. And
the Nine-Echo Sword Wraith's lair, in both boss tables, is the name of a secret realm rather than a
place, so that raid can never be started; the two tables had no parity test and have one now, and the
lair is a content decision in `docs/TODO.md`.

### The first hour is a short list (v1.2.0)

**Found by playing**, three reports in a week: *"we need to simplify interface ... Cultivation,
Breakthrough, Explore, Shop, Craft, Forge, Gather, Hunt, Mine, Quest until Foundation Establishment
- these things are enough"*, *"Interface is overwhelming ... I still forget where to go what to do"*,
*"instant travels ... I don't have to wait half hour"*, and *"Journeyman sounds medieval"*.

**The curriculum is retuned to the owner's list, not to a theory of pacing.** v1.0.9 built the
mechanism and v1.0.13 opened three pages back up; this release's roster in
`scripts/author_feature_unlocks.py` puts every page outside that list at Foundation Establishment
(realm 2) at the earliest and leaves the deeper floors where they were, with two exceptions the owner
took: `beast / Companions` stays at 0 because a companion is the one system the first hour has to be
told about (v1.0.13's own call), and `cultivation / Body` stays at 0 because the body path is a way
of cultivating and not a system beside it. Body Tempering shows **120 of 250** leaves (142 of 248
before). The per-leaf overrides say why each stays: the identity reads, the manual the lesson hands
over, the board and the city's reads (the list says Quest, and the board is where they are), the
household's doors (Leave as well as Enter - the path starts *inside*, and the first Discord run
of this release stalled at "Leave is not drawn while inside", with every step after it a cascade),
`alchemy forage`/`purge`/`condition treat`/`learn`/`profession exam` because the first hour gathers,
mends, reads a slip and sits an examination, and nothing on `cultivation / Cultivate` at all - a
closed-door retreat is a way of cultivating, and `insight` is load-bearing besides: every qi-ladder
crossing from 0 to 1 onward needs a banked insight or a Perfection, and hiding the one lever that
opens the gate would leave a Body Tempering 9 with no visible way through. **`sect / Recruitment` stays at 0** for v1.1.0's reason: a floor on a leaf
nothing but a hub press can reach is a bound.

**The meridians and the Laws are the Spiritual World's game**, and they are gated two different
ways on purpose. `cultivation / Qi Body` is a roster entry at 8, and the cultivation card's Qi Body
field prints where the page opens rather than `20/108 meridians` below it - v1.0.13's finding turned
round, a card advertising a number whose lever the curriculum hides (read off the same roster the
panel reads, no literal realm). The Laws are **not** a roster entry: `law_system.normal_min_realm_index`
moved from 6 to 8 and `_progression_hidden_actions` already hides the page at that floor, so a second
statement here would be the rc.39 fault. Two of the suite's Go tests seeded a realm-7 cultivator to
comprehend a Law and went red on the content change, which is the content floor doing its job.

**The menu leaves off what has nothing to do in it.** `hidden_hubs` in `app/rules/feature_unlocks.py`
hides a hub only when *every* leaf on *every* page of it is a status read or locked - one open lever
keeps it on the board - and `collapsed_menu_line` names the hubs left off, the nearest realm, and
that their slash commands still work. At Body Tempering that is Combat, Abode, Inner World and
Secret Realms. `is_status_read` moved into the rules module for it, so the generator and the menu
hold one idea of what a status read is (the authoring script imports it; v1.0.13's drill still
drives it through the script). It is asked through one registered provider, `menu_shape`, by **both**
doors into the menu - `/menu` and a panel's Back button - because two builders would draw two menus
for one cultivator. It fails the way the curriculum fails: an unreadable roster hides nothing, and a
provider that raises collapses nothing.

**And the menu says what to do next.** The header carries `🧭 Next: **<stage>** — <objective>`, the
first objective still short on the active beginner-path stage, read off the pinned terms the way the
journal reads them. Somebody with no character is shown everything.

**The seam (`mining.go`).** Foraging brought back herbs and the tier-flat makings and never ore, so
spirit iron - three of which every Forging entry method wants - came only from a shop counter or an
Iron-Horn Boar, and Forging was the one trade a cultivator could learn at the household's table and
then not practise without money. `exploration.mine` is forage's shape and not forage's code: the
world's `@ore` through `EventSites.Material`, the same resolver the event sites and the send-off
use; a rare vein of the *next* world's ore (a Celestial seam has nothing above it and invents
nothing); `mine_materials`, the tier-flat sibling of `forage_materials` in the same struct; body and
insight on the roll where forage rolls insight and spirit; the Forging houses' tradition through
`householdTradeBonusTx`; and a few stones on a rich dig through `applyCanonicalRewardTx`, so they
land in the money of the world the seam is in (rc.44). The wait is `actionCooldowns`' (`mine`,
`MINE_COOLDOWN_MINUTES`, compose passthrough - rc.56's rule), the result carries the roll whole
(v1.0.3's rule), and it refuses indoors and on a shrine the way the hunt does; `LOCATION_GATES`
hides it in both. Mining is a profession on `PROFESSIONS`, so the content gate counts it.

**The tutorial ends at the first gate, and a graduate is caught up anywhere.** Two stages follow the
lesson: `beginner_iron` ("Iron from the Seam" - mine spirit iron, come out of a hunt standing, forge
a Spirit-Iron Sword, sell to a keeper; the targets are stored raw and matched `EqualFold`) and
`beginner_gate` ("The First Gate" - the first breakthrough, `breakthrough` being a new objective type
reported by `/breakthrough` on a success, the qi ladder only). The sect road is `beginner_gate`'s
`follow_on`, so v1.1.0's gate that walks the chain out of the path still holds. Migration 62
re-points the lesson's `follow_on` on a running world only where it still names the sect road
(migration 55's rule). And rc.34's catch-up - which handed an added stage over only at the lesson's
door - runs at the end of every ordinary `questProgress` now, whether or not the report completes
anything; its first version sat inside the completion branch and the test written for it said so
(`caught_up=[]` on a report that only advanced an objective). Two caveats are in `docs/TODO.md`: it
walks the file's adjacency rather than `seed_json`, and a graduate holding no quest at all is never
reached.

**A road is walked in the telling (`travel_pace.go`).** `TRAVEL_TIME_PERCENT`, default 0, is the
share of a road's length a traveller actually waits, read the way `clockScaleFromEnv` reads the
clock's rate and passed through compose's allowlist (rc.39). The road's length is still computed and
still reported as `travel_minutes` - the toll is priced on it and the harness advances the clock by
it - and what the setting scales is `wait_minutes`, which is what the transit row and `traveling`
key off. Encounters resolve before the wait is computed, so a road is dangerous at every pace. The
two tests about what a wait *does* pin the old pace with `withTheOldPace(t)`; the new ones hold that
the default writes no transit row and refuses nothing after, that 50 halves the wait, and that an
unreadable value is the default rather than a refusal.

**The ranks are tiers (v1.2.2 revised v1.2.0's grades on the owner's call).** `PROFESSION_TIERS` is
the nine titles, Apprentice to Sovereign, and `PROFESSION_TIER_WORDS` the word each trade puts in
front (Pill, Forge, Talisman, Array, Herb, Ore, Beast, Artifact, Treasure); `profession_rank(level,
trade)` is the one statement, answering "Unranked" at 0, "Tier N <word> <title>" from 1, the top
tier past 9, and the bare tier for a trade it does not know - never a wrong word. The examinations'
`rank_name`, titles and labels quote it ("The Tier 1 Forge Apprentice's Billet"), `_rank_lift`, the
household lines and every profession surface pass the trade, and the engine's one literal ("has not
reached Apprentice") became "the first rank". Nothing stored changes: a rank has always been a level.
The Eight-Grade alternative (Common … Divine) was set aside because Common and Saint are already a
root grade and a realm - the v1.0.11 vocabulary collision - and the four General Titles would leave
levels 4 to 6 unnamed.

**What the suite caught, in order.** The surface table refused `/mine` until it was named; the
curriculum gate's `needed` map named `battle challenge` for `combat_win` and `trade offer` for
`trade` - every reporter, where the path's labels name the hunt and the shops - so it reads what the
labels name now; two card tests seeded a realm-3 and a realm-4 cultivator and asserted the qi body's
numbers, which the card no longer prints there; and the two law fixtures at realm 7 met the new
content floor. Each is the rule this file already states: a test pinned to a number the owner may
retune goes red exactly when the owner retunes it.

### What a deep review found (v1.2.1)

Twenty-five reviewers, one pass each over the whole tree, then the owner's *"Fix all"*. Two things
are worth keeping beside the fixes.

**The beast bug is a promise a cap could falsify.** `beast.evolve` wanted `60 + 10 × stage` and the
two clamps on loyalty said `MIN(100, …)`, three lines apart, and nothing held the requirement under
the cap - so stage five asked for 110 and told the player so. `beastLoyaltyCap` is the one number
now, with a SQL twin the two clamps spell, and `TestTheEvolutionRequirementNeverNamesANumberAboveTheCap`
holds the refusal to the cap. It is rc.56's *"a promise a setting can falsify is a promise nobody
is holding"*, with a constant for the setting.

**Most of the rest is one rule found in a fifteenth place.** `abode.upgrade` charging the Mortal
stone in every world is rc.43's caravan fare; the GM grant mirroring on the stone's name is rc.44's
mirror rule; the household's support wait riding the payload is rc.48 and rc.56; two death paths
writing `status='dead'` without `ReleaseNPCBondsTx` is rc.24's widowing; the restore's plain safety
copy is v0.32.0's sealing; `ENGINE_SHUTDOWN_GRACE_SECONDS` passed by nothing is rc.39's compose
allowlist. A rule stated in this file and enforced at the sites known when it was written does not
reach the site written next, which is why `test_every_engine_key_reaches_the_engine.py` reads the
keys off the Go source rather than off a list: the next one fails the day it is read.

### Six rules the bot was holding (v1.3.1)

The first group of what was left in `docs/TODO.md`, each a bound that lived in the client (rc.48)
or a literal beside its own table. Two are worth the paragraph.

**Where a catalogue NPC stands is the engine's answer now** (`npcWhereaboutsTx`). rc.28 wrote
`current_npc_location` in Python - circuit first, then the simulation row, then the schedule while
at home, then the registry and a running event's cast - and the engine read only the simulation
row, so a sponsor's presence could not be a bound. The Go resolver keeps that order exactly, and
`NPCDefinition` gained `schedule`, `circuit_months` and `circuit_offset` for it; `periodForHour`
and `circuitStop` are the Python twins' arithmetic, held by `TestPeriodsAndCircuitsAreTheClocks`
against the same table `app/rules/sense.py` implies. The sponsor test drives the shipped content:
Elder Xue Hong keeps Moonfen Marsh and walks to Greenriver Town of an evening, so the ask is refused
at nine in the morning and heard at six.

**A client's list can narrow a discovery and never widen it.** `sect.discover` derives the sects
from `knownLocationsTx` and `sectGate`, so the trial's "discovered" check cannot be satisfied by
assertion; the bot's reconcile sends no names at all. The seven tests that drove the old action
named sects the catalogue does not carry ("Iron Peak Sect", "Jade Fern Sect"), which was exactly
the shape that could not fail the way production fails - they name real gates now, made known first.

**The catch-up runs on any action**, beside the vitality settle, and reads `seed_json.follow_on`
off every completed quest rather than the content file's order. The lesson-door test seeded its two
stages with an empty chain and went red, because a fixture with no chain is a world with no chain.

### Ten decisions in one message (v1.3.0)

The owner answered the open decisions of the last four releases at once, and each is recorded in
`docs/TODO.md` as fixed. Four of them are worth a paragraph here, because each turned on something a
reader would not guess.

**A hall teaches what its world can make, and that leaves some ranks empty on purpose.**
`rankRecipesWhereTheyCanBeMade` splits a rank's recipes on `worldOffers` - a shelf in that world, a
guaranteed room item of a realm standing in it, the world's tier materials, the tier-flat forage
makings, and deliberately not the forage rare pool, which is a chance. Measured off the shipped
content, the Mortal World's third examinations teach nothing in three trades and the Spiritual
World's Tier 3 Inscription teaches nothing at all. That is the rule choosing a certificate with
nothing behind it over a method that cannot be made where its holder stands, which is what the
report in v1.0.15 was about; the reply names what was withheld and that a slip, or a hall in a world
that can, teaches it. The withheld methods are all sold as slips somewhere the makings are - that is
what `test_a_method_can_be_made_where_it_is_sold.py` has held since v1.0.15 - so nothing became
unlearnable.

**The lair that named a realm is a floor of it.** `bossLair` resolves a template whose location is a
secret realm's name to that realm's entrance, and opens it only to a leader holding the realm's
inheritance - `inheritances` is written in the last room and survives every later run, where
`secret_realm_runs` is one row per user overwritten on the next `enter`. The Python twin
(`boss_lair`) draws Start where the engine will start it; the two boss tables are untouched, so
v1.1.0's parity gate still holds them equal.

**Half back, rounded down.** A refund of half is a mechanic the owner chose over a full refund and
over none: a craft that could be retried for free would be a roll with no stake, and `craftFailureRefund`
drops the odd unit so one of anything is always spent. The reply reads the engine's `returned` map
rather than restating the share.

**The drill that took the fix with it.** The refund's drill reverted the line with `sed` and
restored the file with `git checkout` - which restores to HEAD, and the refund was uncommitted, so
the drill quietly deleted the feature it had just proved. The test caught it on the next run, and
the whole tree was staged before any further drill. A drill on an uncommitted change restores from
the index or from a copy, never from HEAD.

### The deferred eight (v1.2.3)

v1.2.1 recorded eight review claims its verifiers never reached rather than fixing them blind. Read
one at a time, six held and two did not, and the two are the ones worth writing down.

**A compaction is not a restore.** The maintenance barrier drains requests in flight, and a Python
write is two of them - an execute that opens the implicit `BEGIN` and a commit - so between the two
a session's connection holds SQLite's write lock with nothing in flight for the barrier to see.
VACUUM went in, sat out the whole ten-second `busy_timeout`, and failed *"database is locked"*, with
the commit queued behind the barrier the entire time; the reproduction is the test (10.05 s and a
409 against the old tree). Restore closes every session and VACUUM must not: a restore replaces the
world, so a half-written action is discarded either way, while a compaction shrinks a file and
rolling back somebody's action to do it would be a restore's cost for none of its reason. So
`SessionManager.InTransaction` counts the sessions holding a transaction and the vacuum branch
refuses at once with `sessions_busy` - an idle session holds no lock and is no reason to refuse -
and the Discord handler tells that refusal from a failure and writes no audit row for it.

**One rule at both cultivation doors.** A hand-sat session said *"the manor array and a qi storm are
qi-path weather; the ground counts for both"* and then read a deployed array inside its ground
helper, so a body session was priced on it; a retreat withheld the deployed array from the body path
(a decision `authority2_test.go` names) and applied the manor to it. Each door was the other's
opposite on one of the two arrays. The rule is one sentence now - a qi-gathering array, the manor's
or a deployed one, is qi-path weather; the site and the abode's own array are ground - and
`placeMultiplierForPath` takes the path so the ground helper can keep it. The test holds both
halves, because a test of one door passes against a tree where the other still disagrees.

**Two claims were read and left, and the reasons are the point.** `meridian.open` has no cooldown
because its pace is its cost - a quarter of the insight pool, rising each channel - and a wait added
beside a cost that already bounds it would be a mechanic invented to satisfy a symmetry. The Reopen
button *does* skip the panel gate, and so does the Menu button, on purpose: rc.56 states that a
hub's panel opens because it is where the way out is drawn, and each leaf inside it is checked
again on the press. A claim that reads a designed asymmetry as a hole is the shape rc.49 named from
the other side - being caught in something is not the same as being handed it.

**The rank ceiling had a shelf it could not see.** `cheapestShelfPrice` walked the shops, and a
travelling merchant's wares are a shelf that moves: Madam Wen sells a Spirit Focus Talisman at 15
against a cheapest shop shelf of 17, so a Saint could buy from her and sell to the next counter at
16, one coin a loop. Measured off the shipped content before it was believed, and the gate walks
every merchant ware against every keeper who buys it.

**The rest is one rule found in another place.** The undo chain was followed one link deep, so undo,
redo, undo refused; it is walked to the original now and the chain's length says which way.
`combat.turn`'s counter-attack TN had no `resonance` while `combat.technique`'s did, so an ordinary
strike was easier to be hit on than a technique - `counterDefenceTN` is the one statement, held at
both sites by AST. And `/auction sell` defaulted to the Mortal stone on every floor in the world,
rc.44's currency class on the Python side; it reads the house's `default_currency`.

**The second tier held almost entirely, and two of it are worth the paragraph.**
`commission.resolve` took `outcome` off the payload and paid it - so a client sending `completed`
collected the reward with nothing done. The only honest producer of a completion is
`quest.progress`, which lands in `resolveCommissionTx` once every objective is reported, and the
service's own docstring said so (*"Completion happens inside quest.progress, not here"*) while
the engine beneath it disagreed. It refuses `completed` now, and **five tests had to move**,
because they completed a commission by asking the action to - a fixture driving the door
production never uses, which is the `npc_consignments` rule met in a test's choice of door
rather than its schema. And a **market counter was a mint in both directions**, measured before
it was believed: it priced off sect value times a world factor with no eye on the shelves, so a
Wind Gourd cost 9 at a market while a provisioner paid 45 for it, and a Stygian Tomb Token sold
for 420 on a shelf while the market paid 840. `marketUnitPrice` holds both inside the shops'
band, the rule `tradeRankSellPrice` already states for a keeper's counter. The upper-world
send-off (`sendoffArchetypeFor`) was the one place this release read content into a rule rather
than off it; v1.3.0 authors all thirty-three entries and the reading is gone.

**And the sect trial reads its own tuning, on the owner's call.** `trial_modifier` in
`app/rules/sect_recruitment.py` printed a sect's `base_tn`, its path bonuses, its root affinities,
its household traditions and its karma preference in the notes a trial shows - and
`sectTrialActionGo` rolled `max(10, 15 - rep/25)` for every sect and read none of them. The notes
were a bound that lived in the client (rc.48), and this one was worse than most, because they
*looked* like the rule. `sectTrialTuningTx` is the engine's copy now: the sect's own base TN, the
bonus on both rolls, and the karma refusal that only a sponsor lifts. One test in
`sect_doors_test.go` had pinned the literal 15/14 with a fixture character who happened to be a
Sword Cultivator at karma 50 at the Azure Cloud gate, exactly the applicant the tuning favours;
it sits a path and root the sect has no opinion of now, so it holds the recommendation rule and
nothing else.

### The daily five are one step (v1.3.2)

**Found by playing**, with the `/cooldowns` reading pasted: *"For each command i have to go to 3
steps. Time consuming ... Hunt, Gather, Mine, Explore, Cultivate. With slash command or interface
button. Instead of a b then c."* Four of the five were root commands already - `explore`, `hunt`,
`mine` and `cultivate` are `registered_root_command`s - and **none was in `TREE_COMMANDS`**, so a
hub page was the only door: rc.43's `/learn` shape, where a command exists and reaches nobody, for
the five most-used commands in the game. Forage was a group leaf (`alchemy forage`) with no root.

`DAILY_ACTIONS` in `surface.py` is the five, spliced into `TREE_COMMANDS`, and `_DAILY_LEAVES` says
which hub leaf each one is. `/forage` is a root whose body is one call to the registry's binding of
`alchemy forage`, so the two doors are one handler and `test_the_daily_five_are_one_step.py` reads
the body by AST to hold it. The menu's **Daily** row (`MenuDailyButton`) opens the hub in place and
then calls `_start_hub_action`, in that order, so the result lands where a hub's own quick button
draws it and the press meets `_invoke_action`'s maintenance and seclusion check like every other.
Somebody with no character is not shown the row: `/begin` is all they can do.

Three neighbouring gates were pinned to the old shape and each moved to a rule. `test_command_cleanup`
forbade the string `**/explore**` in the bot package - right while no such slash command existed,
and it holds the root to `_DAILY_LEAVES` now. Two menu tests counted sixteen buttons; they count
`16 + len(DAILY_ACTIONS)`. And `test_seclusion_lockout`'s list of the tree commands that act grew by
the five, which is the decision written down: each is a hub leaf the panel gate already refuses on
the press, and `interaction_check` refuses the slash command by the same rule.

`_hint_action` learned that a printed `**/hunt**` names no hub: it resolves the leaf whose path is
`/hunt`, or the one group leaf carrying that bare name (`/forage` → `alchemy forage`), so a reply
keeps the next-step button the hub path used to earn. The Discord playtest presses the Daily row's
Cultivate and holds that the panel opened and ran something, then drives each of the five as a
slash command.

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
