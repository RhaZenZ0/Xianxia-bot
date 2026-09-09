# Xianxia RP Discord Bot v0.21.0

[![CI](https://github.com/RhaZenZ0/Xianxia-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/RhaZenZ0/Xianxia-bot/actions/workflows/ci.yml)

A persistent Xianxia role-playing Discord bot designed for CPU-only QNAP/NAS deployment. Python owns
Discord, RAG, dashboard, and presentation orchestration; Go owns canonical gameplay rules, current
game time, simulation mutations, and SQLite WAL state.


See `VERSIONS.md` for the full release-by-release changelog, and
`docs/V021_RELEASE_NOTES.md`, `docs/V020_RELEASE_NOTES.md`, `docs/V019_RELEASE_NOTES.md`, `docs/V018_RELEASE_NOTES.md` and `docs/V018_BUILD_HISTORY.md`
(consolidated validation/audit record) for full per-release and staged-authority migration detail.

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
- **Gameplay survives AI outages.** When all OpenRouter routes fail or quota is exhausted, procedural narration is returned and canonical play continues.

## Narration routes

### Direct Google AI Studio route (optional, v0.26.0)

Set `GOOGLE_AI_STUDIO_API_KEY` (or `GEMINI_API_KEY`) and one more hop appears at
the **front of both chains**:

```text
aistudio/gemini-3.8-flash       (your own AI Studio key, called directly)
        | fail / timeout / empty / scratchpad
        v
   ... the OpenRouter chain below, unchanged ...
```

This is the only route that does not go through OpenRouter, and that is the
whole point: it is not charged against OpenRouter's ~50-request daily free
budget, so it is the most effective single change against procedural fallbacks.
Set the key and nothing else changes; leave it empty and the router is exactly
what v0.25.x shipped.

It is narration only, and it is not trusted more than any other route — the
reply goes through the same scratchpad, prompt-leak and length guards, and a
rejected reply falls through to the OpenRouter chain. The SDK (`google-genai`)
is imported lazily: if it is missing or incompatible the route is left out of
the chain, `/admin → Server → Ai Status` says why, and narration carries on.

### Routine (OpenRouter)

```text
google/gemma-4-31b-it:free      (Google AI Studio only - add your own AI Studio key on OpenRouter)
        | fail / timeout / 429
        v
openrouter/free
        | fail / account quota exhausted
        v
procedural narration
```

Used for `/talk`, guided `/action` outcomes, exploration, hunts, ordinary events and normal NPC/sect scenes.

Every narration request tells OpenRouter to switch model reasoning off
(`OPENROUTER_DISABLE_REASONING=true`): the two production failures of the free
chain were a model spending the whole budget thinking and returning nothing, and
a model returning its thinking as the narration. A model that is reasoning-native
ignores the switch, so a route that keeps answering with its scratchpad is dropped
from the defaults rather than kept and filtered: MiniMax M3 was the routine
fallback until v0.25.3 and went this way, as Nemotron 3 Super did before it. `/admin server ai_status` shows,
per route, which upstream served it, whether it went through your own provider
key or OpenRouter's shared pool, and how long a repeatedly failing route is
backing off.

### Epic (OpenRouter)

```text
google/gemma-4-31b-it:free
        | fail / timeout / 429
        v
openrouter/free
        | fail / account quota exhausted
        v
procedural narration
```

Used for major breakthroughs, sect trials, major event scenes and other explicitly epic narration. The LLM does not decide world simulation, combat, advancement, rewards, karma, NPC deaths or faction state.

`OPENROUTER_REQUIRE_FREE=true` rejects paid model IDs. `openrouter/free` is explicitly allowed even though its ID does not end in `:free`. The local request limiter is fail-fast: it does not queue Discord users behind repeated retries.

## HTTP request limits

Both the health listener and the GM dashboard parse HTTP by hand, and both read
the request head **before any authentication runs**. A per-line timeout on its
own is not a limit: a client sending one header just under it holds the
connection open indefinitely, and a client sending them quickly grows the header
dictionary without bound.

Every request head is therefore bounded (`app/ops/http_limits.py`), on both servers,
with the same env knobs:

| Setting | Default | Rejected with |
| --- | --- | --- |
| `HTTP_MAX_REQUEST_LINE_BYTES` | 8192 | 414 URI Too Long |
| `HTTP_MAX_HEADER_LINES` | 100 | 431 Request Header Fields Too Large |
| `HTTP_MAX_HEADER_BYTES` | 16384 | 431 |
| `HTTP_HEADER_DEADLINE_SECONDS` | 10 | 408 Request Timeout |
| `HTTP_HEADER_LINE_TIMEOUT_SECONDS` | 5 | 408 |
| `HTTP_MAX_CONNECTIONS` | 64 | 503 |

The deadline is **absolute**: each read gets whichever is smaller, the per-line
timeout or the time remaining for the whole head. Defaults are generous for a
browser (Chrome sends roughly 15 headers, 1–2 KiB) and mean for an attacker.

> The GM dashboard publishes to `${DASHBOARD_BIND_ADDRESS:-127.0.0.1}:8090` —
> loopback-only by default. Setting `DASHBOARD_BIND_ADDRESS=0.0.0.0` exposes it
> to your whole network; everything above then matters a great deal more.

## Free-tier budget

OpenRouter free models (`:free`) allow **20 requests per minute and 50 requests
per day** while the account has under $10 of lifetime credits — **1000/day at $10
or more**. Every failed route walks to the next one and each walk spends a daily
slot, so the allowance drains faster than the narration count suggests.

```env
OPENROUTER_MAX_REQUESTS_PER_MINUTE=20
OPENROUTER_MAX_REQUESTS_PER_DAY=50      # raise to 1000 once credits are added
```

Once the daily budget is spent the router stops locally rather than making
requests it knows will be refused, and `/admin → Server → Ai Status` says so.
The counter is in memory and resets on restart, so it is a cost saver rather
than an authority — upstream remains the source of truth.

If narration keeps falling back to procedural prose, the two highest-leverage
actions are outside this codebase: add $10 of credits, or add your own provider
key at [openrouter.ai/settings/integrations](https://openrouter.ai/settings/integrations)
so the free models draw on your own provider quota instead of the shared pool.

For the Gemma primary that means a Google AI Studio key, which is two steps and
neither of them is in this repo. Copy the key from
[aistudio.google.com/api-keys](https://aistudio.google.com/api-keys) — AI Studio
creates a project and a key for a new account by itself, so it is usually already
sitting there — then paste it into OpenRouter's **Google AI Studio** integration
(not Vertex) and save. Google's free tier is enough; the key does not need Cloud
Billing, and the bot never calls Google directly, so no Gemini SDK is involved.
On that key, set "shared capacity fallback" to *never use shared capacity for
models this key applies to*, so a failure of your key shows up in `ai_status` as
Google's own error rather than the shared pool's 429.

## Administrator AI monitor

Two GM-only actions under `/admin → Server`. Neither is a typable slash command.

| Action | What it does |
| --- | --- |
| `ai_status` | Narrator health from counters only: AI-served vs procedural fallbacks, per-route attempts/successes/failures, which routes are cooling down and why, and how often the local rate ceiling refused a request. No prompts, no player text, no API key. |
| `chat_digest` | Reads a channel (optionally its threads) over a window and reports what players did, where they got stuck, possible bugs, mood, and what needs attention. Options: `channel`, `hours`, `include_threads`. |

The digest runs on the **same free route chain as narration** — it never uses a
paid model and never uses OpenRouter's paid `openrouter:fusion` server tool, so
it cannot start spending money. The transcript is chunked and analysed
map-reduce style to fit free-model context windows, and overflow keeps the
newest parts. Every AI failure degrades rather than raises: if all routes fail
you still get the deterministic counts.

**`chat_digest` needs the Message Content intent.** Two steps, both required:

1. `MESSAGE_CONTENT_INTENT=true` in `.env` (this is now the default).
2. *Message Content Intent* enabled in the [Discord Developer Portal](https://discord.com/developers/applications) under **Bot → Privileged Gateway Intents**. Under 100 servers this needs no verification.

Without step 2 Discord returns empty text for every message the bot was not
mentioned in, and the digest tells you so rather than reporting an empty channel.
Turning the intent on also activates the existing `on_message` path: RP messages
start being written to `scene_history`, and the bot replies to @-mentions from
users without a character. `AUTO_NARRATE` stays `false`, so it does not begin
listening for typed play on its own (see below).

> The bot's own `scene_history` table cannot answer this question — it keeps only
> the newest 60 rows per channel as narrator context, and deletes the rest on
> every insert. The digest reads Discord's message history instead.

## Typed play (v0.21.1)

With `AUTO_NARRATE=true`, the bot listens in realm hub channels, private scene
threads and `RP_CHANNEL_IDS`. Before v0.21.1 every line there was one narration
call that decided nothing: "I explore the ravine" produced a paragraph and no
exploration. Now a line is one of three things:

| You type | What happens | Narrator calls |
| --- | --- | --- |
| `> I explore the ravine` | The **prefix** marks an action. A deterministic router turns it into the same handler the hub button runs — `/explore`, `/hunt`, `/cultivate`, `/breakthrough`, a scene action (observe, investigate, influence, stealth, physical, qi, resolve, aid) or `/talk` — and the engine resolves it. | whatever that action already spends (routine actions: none) |
| `Qiao, what is the caravan carrying?` | An un-prefixed line that **addresses an NPC who is present** (name in the first two words, or a question naming them), or @mentions the bot, is dialogue: `/talk` for the former, free narration for the latter. | one |
| anything else | **Speech.** Recorded as history so the narrator sees it as context later. No reply, no call. This is most lines in a roleplay channel. | none |

The router is three deterministic stages and never calls a model: a verb table
(`content/typed_play.json` — aliases per action, grow it from what players type),
entity resolution against who is actually present (naming an absent NPC is a
refusal, never a guess), and a picker when two readings tie or nothing matches
(the top candidates, **Narrate it**, and **Just say it in character**). Typed
play defines no handler of its own and makes no engine call or database write;
`tests/python/contracts/test_typed_play_surface.py` reads the source to hold
that, and `tests/python/unit/test_typed_play_router.py` pins what each kind of
line becomes.

Every typed line that can reach the engine or the narrator first spends a token
from a **per-player bucket** (`TYPED_PLAY_BURST` immediately, refilling at
`TYPED_PLAY_PER_MINUTE`) — the v0.23 "per-user command budget" pulled forward,
because before it one player pasting paragraphs could drain the shared 50/day
allowance for everyone. Speech is free. A refused line is answered with the wait,
not queued.

```env
TYPED_PLAY_PREFIX=$        # exactly one character; not a letter, digit or space
TYPED_PLAY_BURST=4
TYPED_PLAY_PER_MINUTE=6
TYPED_PLAY_HINT=true       # once a day, tell a player how when their speech looked like an action
```

Design: `docs/COMMISSIONS_DESIGN.md` ("Typed play").

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

The current schema is **27**. Historical migrations remain in the repository and upgrades run in place.

## Requirements

Recommended QNAP/NAS deployment:

- QNAP Container Station / Docker
- Docker Compose V2 (`docker compose`)
- Discord bot token and guild ID
- OpenRouter API key
- persistent `./data` directory
- **no GPU required**

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
OPENROUTER_ROUTINE_FALLBACK_MODEL=
OPENROUTER_EPIC_MODEL=google/gemma-4-31b-it:free
OPENROUTER_EPIC_FALLBACK_MODEL=
OPENROUTER_DYNAMIC_FREE_FALLBACK=openrouter/free
OPENROUTER_DISABLE_REASONING=true
OPENROUTER_REQUIRE_FREE=true
OPENROUTER_MAX_REQUESTS_PER_MINUTE=20
OPENROUTER_TIMEOUT_SECONDS=30
OPENROUTER_EPIC_TIMEOUT_SECONDS=60
```

> **Dependency note (v0.19.16).** `openai` is pinned at `3.7.0`. openai 3.x uses
> HTTPX2, which verifies TLS against the **operating system** trust store rather
> than certifi — the Dockerfile installs `ca-certificates`, asserts the bundle is
> present and sets `SSL_CERT_FILE`. `httpx` stays pinned in `requirements.txt` in
> its own right because openai 3.x no longer installs it and the Go engine
> transport imports it directly. If narration ever goes flat after an image
> rebuild, check `/admin → Server → ai_status` for a TLS banner first.

The administrator chat monitor is configured with:

```env
MESSAGE_CONTENT_INTENT=true
MONITOR_MAX_MESSAGES=400
MONITOR_LOOKBACK_HOURS=24
MONITOR_CHUNK_CHARS=6000
MONITOR_MAX_CHUNKS=6
```

The ceilings exist because the monitor shares the narrator's
`OPENROUTER_MAX_REQUESTS_PER_MINUTE` limiter — an unbounded transcript would
starve narration for a whole minute.

### 2. Start on QNAP

```bash
chmod +x startup.sh stop.sh
sudo ./startup.sh
```

`startup.sh` validates Docker/Compose, checks required secrets, creates `./data`, builds the Go engine + Python bot, and starts the GM dashboard when `DASHBOARD_ENABLED=true`.

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

### Reset the database

```bash
chmod +x reset_database.sh
./reset_database.sh
```

Wipes every character, NPC, family, sect, war, event and world-history entry and starts a
brand-new game. Discord channels/threads are left alone - run **Server Setup → Repair**
afterward if you want the bot to reconcile stale bindings. It always takes a safety backup
first (through the same engine backup API described below when the stack is running, or a
plain file copy when it is already stopped) and requires typing `RESET` to confirm unless
you pass `--yes`. See `./reset_database.sh --help` for `--no-backup` and `--no-restart`.

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

Since v0.29.0 the dashboard has two more locks. After five wrong passwords
from one source address in five minutes, that address is answered `429` for
fifteen minutes before its credentials are read
(`DASHBOARD_LOGIN_MAX_FAILURES`, `DASHBOARD_LOGIN_WINDOW_SECONDS`,
`DASHBOARD_LOGIN_LOCKOUT_SECONDS`). And a browser `POST` whose `Origin` does
not match the `Host` it was sent to is refused with `403 origin_mismatch`, so
another tab cannot post admin actions with your session.

**Outside Docker** the process binds `127.0.0.1` (`DASHBOARD_HOST`), as does
the bot's health/control listener (`HEALTH_HOST`). To reach either from
another machine, put a TLS reverse proxy in front rather than binding to a
LAN address - Basic Auth is plaintext without it. The proxy must pass the
original `Host` through, or you must list the public origin in
`DASHBOARD_ALLOWED_ORIGINS`; note that behind a proxy every visitor shares
the proxy's address and therefore its login lock. A minimal nginx site:

```nginx
server {
    listen 443 ssl;
    server_name gm.example.lan;
    ssl_certificate     /etc/ssl/gm.example.lan.crt;
    ssl_certificate_key /etc/ssl/gm.example.lan.key;
    location / {
        proxy_pass http://127.0.0.1:8090;
        proxy_set_header Host $host;
    }
}
```

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
9. **Cultivation** — spiritual-root grade/purity/elements/refinement, bloodlines, physiques, Dao/law progress, tribulations, realm perfection and seclusion.
10. **Crafting & Assets** — profession progress, alchemy/toxicity/batches, spirit beasts, artifact bonds, player/sect properties, personal worlds, formations and equipment.
11. **Exploration** — exploration events/participants, secret-realm runs, location discoveries, wild-beast encounters, caravans and expedition threads.
12. **Economy** — dynamic markets, economy events, auctions, black markets/stock and crime records.
13. **Samsara Dynasties** — reincarnation state, soul legacy, dynasty history, ancestral leads, investigation quests, claims and persistent dynasty conflicts.
14. **RAG Memory** — memories, salience, recall counts and metadata.
15. **Autonomous Decisions** — NPC goals/mood/activity plus simulation-generated historical outcomes.
16. **Admin Console** — authoritative world/player/simulation/database controls.

`/api/capabilities` publishes the dashboard API/schema coverage contract. The regression suite compares browser API references, navigation loaders and the backend endpoint registry, and performs authenticated HTTP smoke tests for the newer-system endpoints so frontend/backend drift fails CI instead of appearing as a broken dashboard tab.

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

The RAG-relevant schema versions are 14-17 (see `VERSIONS.md` for the full schema history across
every version, 14 through the current schema 27).

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

## Manuals and inheritances

A manual is an item (`<manual_id>_manual`) that has to be in your inventory; **/cultivation →
Manuals & Techniques → Study** then learns it through the engine's `manual.study`, which enforces the
manual's realm requirement. Since v0.21.3 the whole 148-manual catalog is in `content/world.json`
(`scripts/materialize_world_catalog.py`; a test fails if the file drifts), so the Go engine and Python
read the same content. How players get one:

- **Joining a sect.** Passing an entrance trial bestows the sect's own entry manual - every public
  sect has an authored tier-0 one (v0.21.4), studyable the day you join - chosen and written by the
  engine inside the trial transaction. A disciple who already holds it gets the next manual by the
  sect's alignment (a righteous sect never gives a forbidden art), their own path and the lowest
  tier, never a duplicate. Recorded in `item_provenance` as `sect_entry`.
- **Hidden-sect initiation** (`/sect shadow`): one demonic manual matching your path and realm.
- **The black market**: the forbidden, contraband and demonic stock.

Manual items are `market_excluded`: town markets never list them, and Quest Forge's `rewardable_items`
excludes them too (a deliberate follow-up decision for commissions, see `docs/COMMISSIONS_DESIGN.md`).

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

### Quest Forge

`/admin world questforge <story>` turns a few sentences of story into a quest. The narrator's
free model chain drafts it in the game's own quest shape - objectives from the small vocabulary
the engine tracks (`explore <location>`, `talk <NPC>`, `scene_action <kind>`, `sect_discovery`,
`sect_trial`) and rewards inside your budget - and every location, NPC, scene action and item it
names is checked against `content/world.json` before you see it. You get the draft with
**Approve** / **Discard** buttons; only an approved quest appears in players' `/quests`.
If the model is down or keeps producing something invalid, a procedural draft built from the
same story is offered instead, marked as such. `/admin world quests` lists drafts (with the
same buttons) and approved quests, and retires an approved one by key; the dashboard's
Exploration view shows them too.

With `QUEST_FORGE_AUTO=true` the bot also drafts one quest per notable world-history event
(`QUEST_FORGE_MIN_SIGNIFICANCE`, default 80) every `QUEST_FORGE_INTERVAL_HOURS` and posts
"Quest drafts ready" to the log channel - drafts only, never auto-approved. Rewards are capped
by `QUEST_REWARD_MAX_XP` / `_STONES` / `_ITEMS` and granted by the Go engine when the quest
completes; the player is told what they earned.

## Backups and maintenance

Backups are created by the Go engine using the SQLite backup API and stored under the data backup directory.

From Discord or the Admin Console you can:

- create/list backups
- inspect engine/database health
- optimize SQLite
- VACUUM SQLite when appropriate

Do not copy a live WAL database file by hand as your primary backup strategy.

To wipe the world and start over, use `./reset_database.sh` (see above) rather than
deleting `data/xianxia.sqlite3` by hand - it takes a safety backup first and restarts
the stack so a fresh schema is created automatically.

## Updates and the release channel

Releases are GitHub Releases on `RhaZenZ0/Xianxia-bot`, built by CI from a tag
(`.github/workflows/release.yml`): `v0.21.0` is a **stable** release,
`v0.21.0-beta.1` a **beta** (pre-release). Each carries
`xianxia_rp_v<version>.zip` and its `.sha256`.

Two things read that channel:

- **The bot** checks it once after startup and then every `UPDATE_CHECK_HOURS`
  (default 24) and posts "Update available" to the bot log channel once per
  newer release. `UPDATE_CHANNEL=stable|beta` picks the channel;
  `UPDATE_CHECK_ENABLED=false` turns the check off. The bot never downloads or
  installs anything.
- **`update.sh` on the NAS**, which stays offline unless you ask:

```bash
./update.sh                     # offline: look in ./updates for a ZIP you placed there
./update.sh --check             # ask the channel whether something newer exists
./update.sh --fetch             # download the newest ZIP into ./updates, verify its SHA-256
./update.sh --upgrade           # fetch, then install (backup, stop, swap, start, rollback on failure)
./update.sh --fetch --channel beta   # this run only; .env's UPDATE_CHANNEL otherwise
```

A download whose SHA-256 does not match the release's sidecar, or whose
archive `VERSION` does not match the tag, is discarded. Installing a fetched
ZIP is exactly the same transactional path as installing a hand-placed one,
release manifest check included.

The roadmap to v1.0.0 — what each milestone ships and the test that gates it —
is `docs/ROADMAP_1_0.md`.

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
python -m app.database.bootstrap
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
- The **Discord Setup** page talks only to the private Python-bot control endpoint inside the Docker network. It never sends Discord mutations through Go.
- The bot control endpoint requires `BOT_CONTROL_TOKEN`; when that value is blank both bot and dashboard reuse `DASHBOARD_TOKEN`.

If the dashboard rejects startup, verify `DASHBOARD_TOKEN` is at least 20 characters and not a placeholder. If Admin Console controls are disabled, verify `DASHBOARD_ADMIN_WRITES=true` and `GAME_ENGINE_URL` is reachable.

### Discord Server Setup from the GM Dashboard

Open **Discord Setup** in the GM dashboard after the bot has joined the configured `GUILD_ID`. The page can:

- inspect the connected Xianxia RP guild and bot identity
- diagnose required Discord permissions and realm-role hierarchy problems
- run an idempotent **Full Setup** that creates/reuses/repairs the canonical Xianxia RP base channels and realm-capital channels
- run **Repair Server** without deleting unrelated Discord channels or resetting game/world data
- gate every realm-capital hub behind its **presence role** `Xianxia • <capital name>` (v0.21.6:
  `@everyone` denied, the presence role granted view/send/history/threads/reactions/files/slash commands,
  the bot allowed; applied on every Setup/Repair). The bot puts the role on when a character's
  location is that capital and takes it off when it is not, so a capital is visible only while you
  are in the city. The Realm Capitals table shows a hub as **VISIBLE TO ALL** until it is gated.
- synchronize guild slash commands
- synchronize existing cultivators' generated realm-access roles
- rebuild the persistent `#xianxia-info` guide
- send a test message to the configured world-events channel
- bind existing text channels manually for world events, event scenes, player homes, logs, onboarding, info and expeditions
- **Fresh Start** (type nothing, confirm `CLEAR`): delete and recreate the message-safe channels
- **Teardown** (v0.21.2, type `DELETE`): delete every thread, bound channel, `#bugs` and the two Xianxia
  categories when empty, and forget their ids — nothing is recreated (run Full Setup after) and the
  database is untouched; `RP_CHANNEL_IDS`, the realm roles and any channel Setup did not bind are left alone
- **Reset World** (confirm `RESET`): delete every tracked thread and post the world-reset announcement

Discord provisioning is intentionally owned by the Python `discord.py` process. The dashboard calls a private authenticated bot-control endpoint, and every state-changing dashboard Discord operation writes an `admin_audit_log` entry through the normal Go-owned database boundary.

Recommended first install:

1. Create or choose the Discord server.
2. Invite the Xianxia RP bot with the required permissions.
3. Set `GUILD_ID` and start the stack.
4. Open **GM Dashboard -> Discord Setup**.
5. Run **Full Setup**.
6. Resolve any permission warnings shown by the dashboard, then run **Repair Server** if needed.

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

Dashboard implementation is part of the standard release gate. To run that contract directly:

```bash
python scripts/check_dashboard_implementation.py
```

This check fails on frontend/backend API drift, missing dashboard loaders/views/routes, or a schema version that has not been explicitly reviewed for dashboard coverage.

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
  bot/                 Discord frontend: runtime, services, commands/, admin/, ui/, surface wiring
  rules/               gameplay rules and content helpers (pure: alchemy, aptitudes, birthfamily,
                       samsara, sect*, worldtime, game/World ...) - imports nothing above it
  ai/                  ai_router (OpenRouter routing), narrator + narrator_context, rag, chat_monitor
  ops/                 config, health/http_limits, game_engine (Go client), core_services,
                       the healthcheck entrypoint
  dashboard/           authenticated GM web control plane (server.py) + front-end contract
  database/            Python repository API, Go remote DB transport, bootstrap entrypoint
  simulation/          Python orchestration/compatibility during migration
  version.py           the release stamp

Layering (tests/python/unit/test_app_layout.py): {rules, ops} <- ai <- database <- simulation <- dashboard <- bot;
rules and ops do not import each other.

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

## Release status

- Current release: v0.21.0. See `VERSIONS.md` for the full release-by-release history.
- Go owns canonical gameplay time, migrated gameplay mechanics, lifespan/death authority, road travel,
  caravan settlement, simulation mutation, and SQLite WAL.
- Python owns Discord/RAG/dashboard/presentation orchestration and does not duplicate the removed
  lifespan/mechanical authority paths.
- Discord channel/category provisioning is admin-dashboard-owned: the web GM dashboard's Full Setup/Repair
  action can create the missing base and realm-hub channels/categories itself (when the bot has Manage
  Channels); the `/admin` Discord slash command's own setup action reuses the same helper but stays
  validate-and-bind-only, so channel layout still can't drift out from under the dashboard via Discord itself.
- Database schema is **27**.

## Design rules for future work

1. **Do not reintroduce a Go shadow mode.** New migrated mechanics should execute once in Go.
2. **Do not open production SQLite from Python.** Add a Go action, Go batch, or Go-hosted repository session.
3. **Batch world work.** Do not make one HTTP/database operation per NPC when a native Go batch can process a complete simulation step.
4. **Keep AI non-authoritative.** The guided `/action` UI and deterministic mechanics resolve intent/results before narration. AI only describes supplied canonical outcomes and never directly writes rewards, deaths, relationships, travel or history.
5. **Keep viewpoint permissions deterministic.** RAG must not leak hidden/participant/faction information.
6. **Audit GM mutations.** New Admin Console actions should write `admin_audit_log`.
7. **Prefer native Go tests for Go-owned rules.** Pytest should test Python-owned behavior and integration boundaries rather than duplicate engine formulas.

## Version history

See `VERSIONS.md` for the full v0.19.x (and v0.18) release-by-release changelog, schema history, and
release-notes pointers.
