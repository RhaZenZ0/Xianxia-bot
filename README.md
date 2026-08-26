# Xianxia RP Discord Bot v0.13

A persistent Xianxia role-playing Discord bot designed for a CPU-only QNAP/NAS deployment: Python owns Discord/RAG/presentation, Go owns canonical game rules and SQLite WAL, and OpenRouter provides read-only cloud narration with a fully free fallback chain.

Version **0.13** removes the local Ollama/Qwen stack and restores the guided **`/action`-only** interaction design. Routine narration uses Gemma 4 31B Free, epic narration uses Nemotron 3 Super Free, known free cloud fallbacks are tried next, and `openrouter/free` is the final cloud fallback before deterministic procedural narration.

## Release architecture

```text
PLAYER
  |
  v
Discord / Python interaction layer
  |
  +-- guided /action UI + deterministic checks
  +-- RAG / NPC memory / canonical context
  |
  +------------------------+
  |                        |
  v                        v
Go game engine         AI narrator (read only)
AUTHORITATIVE              |
  |                  +-----+-----+
  |                  |           |
  |               ROUTINE      EPIC
  |                  |           |
  |           Gemma 4 31B   Nemotron 3 Super
  |               :free         :free
  |                  |           |
  |           Gemma 4 26B   Gemma 4 31B
  |             A4B :free      :free
  |                  +-----+-----+
  |                        |
  |                  openrouter/free
  |                        |
  |                procedural fallback
  |                        |
  +------------+-----------+
               v
            Discord

Go -> SQLite WAL + batched transactions
```

Ownership rules:

- **Go owns canonical mechanics and production SQLite access.**
- **Python owns Discord commands/views, RAG/context assembly, permissions and presentation.**
- **AI is narration-only.** `/action` selects intent through the UI; deterministic mechanics fix the result before narration.
- **No local LLM runs on the NAS.** There is no Ollama service or Qwen model download.
- **Gameplay survives AI outages.** When all OpenRouter routes fail or quota is exhausted, procedural narration is returned and canonical play continues.

## OpenRouter narration routes

### Routine

```text
google/gemma-4-31b-it:free
        | fail / timeout / 429
        v
google/gemma-4-26b-a4b-it:free
        | fail
        v
openrouter/free
        | fail / account quota exhausted
        v
procedural narration
```

Used for `/talk`, guided `/action` outcomes, exploration, hunts, ordinary events and normal NPC/sect scenes.

### Epic

```text
nvidia/nemotron-3-super-120b-a12b:free
        | fail / timeout / 429
        v
google/gemma-4-31b-it:free
        | fail
        v
openrouter/free
        | fail / account quota exhausted
        v
procedural narration
```

Used for major breakthroughs, sect trials, major event scenes and other explicitly epic narration. The LLM does not decide world simulation, combat, advancement, rewards, karma, NPC deaths or faction state.

`OPENROUTER_REQUIRE_FREE=true` rejects paid model IDs. `openrouter/free` is explicitly allowed even though its ID does not end in `:free`. The local request limiter is fail-fast: it does not queue Discord users behind repeated retries.

## Current database configuration

Every Go SQLite connection applies:

```text
journal_mode=WAL
foreign_keys=ON
busy_timeout=10000
synchronous=NORMAL
cache_size=-32768
wal_autocheckpoint=1000
```

The current schema is **17**. Historical migrations remain in the repository and upgrades run in place.

## Requirements

Recommended QNAP/NAS deployment:

- QNAP Container Station / Docker
- Docker Compose V2 (`docker compose`)
- Discord bot token and guild ID
- OpenRouter API key
- persistent `./data` directory
- **no GPU and no local LLM required**

For development without Docker:

- Python 3.11+
- Go 1.23+
- SQLite development/runtime library for the Go CGO binding

## QNAP quick start

### 1. Configure `.env`

The release includes both `.env` and `.env.example`. Fill in at least:

```env
DISCORD_TOKEN=<discord bot token>
GUILD_ID=<discord server id>
OPENROUTER_API_KEY=<OpenRouter API key>
```

The default cloud-only narrator configuration is:

```env
NARRATOR_PROVIDER=openrouter
OPENROUTER_ROUTINE_MODEL=google/gemma-4-31b-it:free
OPENROUTER_ROUTINE_FALLBACK_MODEL=google/gemma-4-26b-a4b-it:free
OPENROUTER_EPIC_MODEL=nvidia/nemotron-3-super-120b-a12b:free
OPENROUTER_EPIC_FALLBACK_MODEL=google/gemma-4-31b-it:free
OPENROUTER_DYNAMIC_FREE_FALLBACK=openrouter/free
OPENROUTER_REQUIRE_FREE=true
OPENROUTER_MAX_REQUESTS_PER_MINUTE=20
OPENROUTER_TIMEOUT_SECONDS=30
OPENROUTER_EPIC_TIMEOUT_SECONDS=60
```

### 2. Start on QNAP

```bash
chmod +x startup.sh stop.sh
sudo ./startup.sh
```

`startup.sh` validates Docker/Compose, checks required secrets, creates `./data`, builds the Go engine + Python bot, and starts the GM dashboard when `DASHBOARD_ENABLED=true`. It never starts or downloads a local model.

Useful logs:

```bash
docker compose logs -f --tail=150 xianxia-engine
docker compose logs -f --tail=150 xianxia-bot
docker compose --profile dashboard logs -f --tail=150 xianxia-dashboard
```

### 3. Stop on QNAP

```bash
sudo ./stop.sh
```

The stop script removes running containers/networks but preserves `.env` and `./data`.

## Services

### `xianxia-engine`

Authoritative Go service providing game/admin actions, native batched world simulation, Go-owned SQLite sessions, backups/maintenance, and health endpoints.

### `xianxia-bot`

Python Discord application providing slash commands, guided UI, RAG/NPC context, and read-only OpenRouter narration.

### `xianxia-dashboard`

Optional authenticated GM control plane. `startup.sh` starts it when:

```env
DASHBOARD_ENABLED=true
```

No Ollama/local-LLM service exists in v0.13.

## Player interface and Discord GUI

The bot uses interactive panels rather than requiring players to memorize every command.

### Player dashboard

`/me` opens the player dashboard with current character state and guided actions. System panels provide:

- clear active-page hierarchy
- Vitality and Qi bars with percentages and exact values
- quick actions and complete action lists
- selectors and guided inputs
- in-place refresh
- owner locking and timeout protection

### Battle interface

Battles use an interactive in-place panel with:

- color-coded Vitality bars
- realm/stage matchup context
- location and suppression state
- Attack, Defend, Flee and Refresh
- law-technique and recovery-item selectors
- click serialization/owner locking
- Spare/Kill decision controls after victory

### Event-specific GUI

Persistent world events can open playable event threads. Event panels support category-aware actions and connect directly to canonical systems.

Typical controls include:

- **Investigate** — opens the Scene Action system focused on the environment.
- **Scene Action** — opens the complete action/target interface.
- **Battle** — opens canonical battle status when combat exists.
- **Participants** — shows persistent participation and public NPC life state.
- **Consequences** — shows mechanical event consequences and recent contribution.
- **Talk** — speaks to mechanically present persistent NPCs.
- **Refresh** — refreshes event state and remaining time.

Event controls do not bypass travel, permissions, cooldowns, rolls, hidden information or combat rules. Dangerous events may spawn event-specific hostile manifestations without silently killing persistent NPCs. Event closure writes an idempotent structured world-history aftermath entry.

## GM Admin Console

Version 0.10 upgrades the former observational dashboard into a real administrative control plane.

### Configure dashboard access

Generate a strong private token:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Set:

```env
DASHBOARD_USERNAME=admin
DASHBOARD_TOKEN=<long random secret>
DASHBOARD_BIND_ADDRESS=127.0.0.1
DASHBOARD_PORT=8090
DASHBOARD_ADMIN_WRITES=true
```

Open:

```text
http://127.0.0.1:8090
```

For LAN use, bind to the QNAP/server's exact LAN address where possible. Do **not** port-forward the GM dashboard directly to the public internet.

### Security model

- HTTP Basic authentication protects UI/API routes except `/livez`.
- Dashboard traffic binds to localhost by default.
- Production dashboard reads use query-only sessions owned by the Go engine; the dashboard does not open SQLite directly.
- State-changing requests require authenticated JSON `POST` plus the dashboard-specific `X-Xianxia-Admin: 1` header.
- Browser CSP disables external scripts/forms and cross-origin API access is not enabled.
- Every game-state mutation is executed by the authoritative Go engine.
- GM state changes are written to `admin_audit_log`.
- `DASHBOARD_ADMIN_WRITES=false` turns the Admin Console back into read-only mode.

### Observability views

The dashboard contains:

1. **Overview** — canonical clock, totals, active era, simulation lag and recent history.
2. **Timeline** — searchable Structured World-History RAG timeline including GM-visible visibility levels.
3. **NPCs** — autonomous state, injury, family, goals, social graph, disciples, descendants and GM-only catalog details.
4. **Families** — birth families, branches, retainers, player-founded families, marriages and descendants.
5. **Sect Politics** — resources, cohesion, influence, internal factions, inter-sect relations and political events.
6. **Conflicts** — wars, battles, feuds, grudges, bounties/hunters and boss encounters.
7. **World Events** — active phenomena, civilization regions/incidents and eras.
8. **Player Activity** — player state, persistent world actions and scene activity.
9. **RAG Memory** — memories, salience, recall counts and metadata.
10. **Autonomous Decisions** — NPC goals/mood/activity plus simulation-generated historical outcomes.
11. **Admin Console** — authoritative world/player/simulation/database controls.

### Real admin actions

The Admin Console currently supports:

- advance or rewind canonical world time
- force one native Go simulation system for 1–120 steps
- change native simulation intervals
- enable/disable persisted automation systems
- teleport a player to a canonical location
- grant cultivation currency
- adjust canonical karma
- revive a character, restore Vitality/Qi, cancel pending Samsara and abandon active battles
- force-clear a player's active battle state
- create safe SQLite backups
- run `PRAGMA optimize`
- run `VACUUM`
- inspect recent admin audit records

Dangerous actions use explicit browser confirmation where appropriate.

## Authoritative Go engine

The original Go shadow/parity experiment is gone. Go is now a production service rather than a duplicate calculation path.

### Native heavy simulation

The world tick is deliberately coarse-grained. Python submits a world/simulation operation; Go processes large sets internally and commits them in transactions.

Current native batch systems include:

- `npc_civilization`
- `npc_life`
- `dynamic_economy`
- `black_markets`
- `sect_politics`
- `clan_dynamics`

This avoids the slow pattern of Python calling Go/SQLite once per NPC.

### Authoritative game actions

Migrated Go action handlers include scene transitions, NPC relationship updates, quest progression, combat damage, cultivation rewards and the GM admin mutations listed above.

Python can continue using the repository API while remaining game-engine/database agnostic because the production database connection is hosted by Go.

## Autonomous NPC life simulation

NPCs have persistent mechanical lives rather than being only narrator characters.

### Simulation clocks

- `npc_civilization` normally advances daily.
- `npc_life` normally advances every seven world-days.
- economy, black-market, sect and clan systems have their own persisted intervals.
- simulation anchors survive restarts and support bounded catch-up.

### NPC state

Persistent systems track:

- cultivation and breakthroughs
- travel and current activity
- career progression and social rank
- health and injuries
- affinity, trust and grudges
- friendships, rivalries and blood feuds
- marriage and descendants
- master/disciple lineage
- faction membership and defection
- aging, lifespan and death
- autonomous goals, mood and focus

Sect career progression can move through Outer Disciple, Inner Disciple, Core Disciple, Deacon, Elder, Hall Master and Grand Elder.

### Injuries and death

NPC conflicts can create persistent flesh wounds, fractures, meridian damage, internal injuries or foundation wounds. Injuries recover with world time. Severe clashes can mechanically kill an NPC, update family/social/discipleship state and write canonical history.

### Marriage and descendants

Compatible mature NPCs can marry mechanically. Descendants record both parents, birth game-minute, gender style, spiritual root and cultivation state. Surviving descendants can mature into actively simulated NPCs and continue the lineage.

### Narrator boundary

The narrator can describe mechanical life state but cannot create it. AI text cannot independently marry NPCs, kill them, promote them, create children, change faction membership or heal injuries.

## NPC memory and autonomous mind

Persistent NPC/player memory and mind-state systems give dialogue continuity without transferring authority to the LLM.

The database stores relevant encounters, relationship facts and autonomous mind state. Narrator/director context may use those facts, but mechanical state always wins.

## Memory / RAG v1

The RAG architecture is deterministic and SQLite-first. It does not need an embedding model or vector database.

### Core rule

RAG retrieves **known canonical information**. It never creates game truth.

The retrieval chain is conceptually:

```text
live structured SQL
  -> permission-filtered SQLite FTS5 candidates
  -> deterministic scoring
  -> small scene-specific context packet
  -> narrator
```

### Schema history

- **Schema 14** introduced player memory and narrator-safe canon FTS.
- **Schema 15** added structured permanent world history and `world_history_fts`.
- **Schema 16** added persistent NPC life/social/descendant systems.
- **Schema 17** adds the current event participation/GUI persistence layer and associated current schema updates.

### Safe canon indexing

The RAG corpus can include safe public/current information such as current-location descriptions and known manuals/techniques. It deliberately excludes:

- NPC secrets and hidden-master identities
- unrevealed schedules/encounter seeds
- undiscovered locations
- unknown manuals/techniques
- raw database dumps
- GM-only state

Unknown manuals do not become visible simply because player text matches their name. Current-location documents are restricted to the player's physical location.

### Query safety

Raw player text is never passed directly to SQLite `MATCH`. Text is tokenized/sanitized and the application builds bounded FTS5 queries, preventing player RP from becoming FTS operators.

### Scene retrieval profiles

Narrator context is budgeted by scene type. Routine dialogue/battle scenes receive compact memory/canon/history retrieval while exploration and epic/world-event scenes receive a larger budget. The authority contract is kept even when context must be truncated.

Short permission-scoped caches reduce repeated FTS work. Live structured state is not replaced by cached RAG.

No additional AI request is required for RAG.

## Structured world history

`world_history_events` stores events that actually happened mechanically. History answers **what happened**; current structured state answers **what is true now**.

Typical historical event types include:

- true deaths and Samsara
- NPC deaths
- major battles and mercy outcomes
- territory wars and control changes
- family leadership/succession changes
- clan feuds and alliances
- Dao partnerships
- discoveries
- secret-realm openings
- server-wide phenomena
- ancient inheritances
- ascensions
- NPC breakthroughs, travel, marriage, descendants, discipleship, promotions and faction changes

### Knowledge boundaries

History rows use visibility levels:

- `public`
- `participant`
- `faction`
- `hidden`

A focused NPC does not inherit the player's participant-only knowledge. Hidden history is never supplied to narrator RAG.

### Retrieval

History retrieval merges structured context and FTS5 matches, checks viewpoint permissions, removes duplicate history IDs and ranks by relevance, significance, slow recency decay, location/faction relationship and actor/target mentions.

Current structured state always overrides old historical state.

## Economy, sects, clans and world consequences

The simulation maintains dynamic regional markets, black-market rotations, sect politics and clan dynamics. Player actions can write persistent world actions/history, and autonomous changes continue while players are absent or in seclusion.

The design goal is that progression and world simulation never pause each other.

## Administration in Discord

Discord Administrator commands remain available alongside the web dashboard. They cover areas such as:

- server/channel setup and diagnostics
- world-event management
- player inspection, teleport, revive, battle recovery, currency and karma
- sect/master/rank management
- family/NPC inspection
- simulation automation, intervals and forced runs
- backups, maintenance and audit logs

The web Admin Console is an additional local control surface, not a replacement for Discord permissions.

## Backups and maintenance

Backups are created by the Go engine using the SQLite backup API and stored under the data backup directory.

From Discord or the Admin Console you can:

- create/list backups
- inspect engine/database health
- optimize SQLite
- VACUUM SQLite when appropriate

Do not copy a live WAL database file by hand as your primary backup strategy.

## Data ownership and migrations

Canonical persistent data lives in:

```text
data/xianxia.sqlite3
```

Keep the entire `data/` directory persistent across container rebuilds.

Although development currently allows architectural/database changes, the migration history remains preserved so existing test/development databases can still upgrade through the known schema chain.

## Configuration highlights

See `.env.example` for the complete set. Important groups include:

- Discord token/guild/channel behavior
- OpenRouter routine/epic free fallback chains, rate limiting and timeouts
- context/RAG budgets and cache TTLs
- game-engine URL/timeouts
- dashboard bind/auth/admin-write settings
- operational logging/health settings

Never commit `.env`, Discord tokens or dashboard secrets.

## Run without Docker

Start the Go engine first:

```bash
cd go_core
go run ./cmd/xianxia-core
```

Configure Python to point at it, then initialize and start the bot:

```bash
export GAME_ENGINE_URL=http://127.0.0.1:8081
python -m app.database_bootstrap
python -m app.bot
```

Optional dashboard:

```bash
python -m app.dashboard
```

You still need either an OpenRouter API key or an intentionally configured alternative narrator provider for generated roleplay text.

## Health and troubleshooting

### Engine

- `GET /livez` — process liveness
- `GET /readyz` — database/service readiness
- `GET /v1/db/status` — SQLite pragmas and engine request count

### Bot

The Python service exposes its configured health/metrics behavior and records startup/catalog readiness.

### Dashboard

- `GET /livez` is unauthenticated for container health checks.
- UI/API routes require Basic authentication.

If the dashboard rejects startup, verify `DASHBOARD_TOKEN` is at least 20 characters and not a placeholder. If Admin Console controls are disabled, verify `DASHBOARD_ADMIN_WRITES=true` and `GAME_ENGINE_URL` is reachable.

If SQLite reports contention, verify only the Go service is opening the production database and inspect `journal_mode`, `busy_timeout` and current engine sessions rather than adding direct Python SQLite writers.

## Development and testing

### Python regression suite

The Python suite is organized by ownership under `tests/python/`:

- `unit/` — fast Python-only presentation, content, narrator and helper behavior
- `integration/` — repository/orchestration features that still genuinely live in Python
- `contracts/` — Python↔Go HTTP/RPC, startup, deployment and release boundaries

Run everything:

```bash
pytest -q
```

Or target one ownership layer:

```bash
pytest -q -m unit
pytest -q -m integration
pytest -q -m contract
```

Do not duplicate authoritative Go formulas or SQLite semantics in pytest. When a mechanic moves to Go, move its rule/state-transition coverage to native Go tests and keep only the Python boundary assertion here.

### Go tests

```bash
cd go_core
go test ./...
```

Native Go tests cover authoritative game actions, SQLite WAL/transaction/backup behavior, simulation batching/backlog handling, engine contracts and GM mutations.

### Compile production Python

```bash
python -m compileall -q app
```

### Build Go engine

```bash
cd go_core
go build ./cmd/xianxia-core
```

### Validate world content

```bash
python -m json.tool content/world.json >/dev/null
```

## Repository layout

```text
app/
  bot/                 Discord frontend and hubs
  database/            Python repository API + Go remote DB transport
  simulation/          Python orchestration/compatibility during migration
  dashboard.py         authenticated GM web control plane
  game_engine.py       authoritative Go client
  rag.py               deterministic memory/canon/history retrieval
  ai_router.py         OpenRouter routine/epic free-fallback routing
  narrator*.py         narration/context layer

go_core/
  cmd/xianxia-core/    Go service entry point
  internal/game/       authoritative game/admin actions
  internal/simulation/ native batched world simulation
  internal/storage/    SQLite WAL ownership, sessions and backup support
  internal/server/     HTTP control/data plane

dashboard/             static GM dashboard UI
content/world.json     canonical content catalog
data/                   runtime SQLite/backups (not shipped as source state)
tests/python/           Python-owned unit/integration/contract suite
tests/support.py         shared dependency shims and test path helpers
```

## Design rules for future work

1. **Do not reintroduce a Go shadow mode.** New migrated mechanics should execute once in Go.
2. **Do not open production SQLite from Python.** Add a Go action, Go batch, or Go-hosted repository session.
3. **Batch world work.** Do not make one HTTP/database operation per NPC when a native Go batch can process a complete simulation step.
4. **Keep AI non-authoritative.** The guided `/action` UI and deterministic mechanics resolve intent/results before narration. AI only describes supplied canonical outcomes and never directly writes rewards, deaths, relationships, travel or history.
5. **Keep viewpoint permissions deterministic.** RAG must not leak hidden/participant/faction information.
6. **Audit GM mutations.** New Admin Console actions should write `admin_audit_log`.
7. **Prefer native Go tests for Go-owned rules.** Pytest should test Python-owned behavior and integration boundaries rather than duplicate engine formulas.

## Release notes — v0.13

- removed Ollama, Qwen model downloads and the `local-llm` Docker profile for CPU-only NAS deployment
- restored `/action` as the only freeform scene-action entry point; removed the AI-classified root `/act` shortcut
- changed AI to two narration tiers only: routine and epic
- routine chain: Gemma 4 31B Free -> Gemma 4 26B A4B Free -> `openrouter/free` -> procedural
- epic chain: Nemotron 3 Super Free -> Gemma 4 31B Free -> `openrouter/free` -> procedural
- added fail-fast OpenRouter request-rate limiting and per-route cooldowns
- added separate routine/epic OpenRouter timeouts
- added QNAP-focused `startup.sh` and `stop.sh` scripts
- added a ready-to-fill `.env` for QNAP deployment
- removed `START_FULL_DOCKER.sh`, `STOP_FULL_DOCKER.sh` and `QNAP_QWEN_SETUP.sh`

## Release notes — v0.12

- introduced the OpenRouter routing layer and free-only model enforcement
- this release is superseded by v0.13 for the CPU-only NAS architecture

## Release notes — v0.11

- reorganized pytest into `tests/python/unit`, `tests/python/integration` and `tests/python/contracts`
- added pytest ownership markers for targeted `unit`, `integration` and `contract` runs
- centralized shared artifact-only dependency shims in `tests/support.py`
- removed Python tests for Go-owned SQLite concurrency/WAL internals and replaced them with native Go storage tests
- moved simulation backlog/batch assertions and authoritative relationship/scene/quest/combat/cultivation action coverage into native Go tests
- removed the unused shadow-era Python core write ledger helper while preserving its historical migration tables
- removed in-process Python replicas of Go relationship and quest rules from the service tests
- fixed authoritative Go scene transitions so nullable `channel_id` values bind correctly to SQLite

## Release notes — v0.10

- upgraded the local GM dashboard from read-only observability to an authenticated Admin Console
- added authoritative Go admin actions for time, player teleport/revive/battle recovery, currency, karma, automation and simulation intervals
- exposed native Go forced-simulation controls through the dashboard
- exposed Go-owned backups, optimize and VACUUM through the dashboard
- added dashboard admin audit visibility
- kept read-only mode available with `DASHBOARD_ADMIN_WRITES=false`
- fixed dashboard startup so production query-only Go sessions do not require a local SQLite mount
- added native Go tests for admin-state mutations and auditing
- consolidated the former GUI, event GUI, local dashboard, NPC autonomous life, NPC memory, RAG performance, RAG v1 and world-history markdown notes into this README

