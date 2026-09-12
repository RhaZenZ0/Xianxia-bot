# Xianxia RP Discord Bot v1.0.0

[![CI](https://github.com/RhaZenZ0/Xianxia-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/RhaZenZ0/Xianxia-bot/actions/workflows/ci.yml)

A persistent Xianxia role-playing game that lives in a Discord server and runs on a CPU-only
QNAP/NAS. Forty-eight cities across four worlds, each with gates, districts, shops and an inn; sects
with entrance trials and manuals; travelling merchants and live auction floors; NPCs with lives,
injuries, marriages and deaths that go on whether players are there or not. A Go engine owns every
rule and the database. Python owns Discord, the GM dashboard and the narrator. The AI only ever
describes what the engine has already decided, and the game keeps running when the AI is down.

- `VERSIONS.md` — the changelog, release by release, with the schema history.
- `docs/CONFIGURATION.md` — every `.env` key explained; `.env.example` is the keys and defaults only.
- `docs/ROADMAP_1_0.md` — what is left before 1.0 and the test that gates each milestone.
- `docs/KNOWN_LIMITATIONS.md` — the punch list, every entry fixed or deferred with a reason.
- `docs/playtest/` — the live-server checklist for the current release.
- `docs/COMMISSIONS_DESIGN.md` — the design of commissions and typed play.
- `docs/history/` — the record of how the tree got here; nothing in it describes the current release.

---

## Contents

1. [Architecture](#architecture)
2. [Quick start on QNAP](#quick-start-on-qnap)
3. [Running without Docker](#running-without-docker)
4. [The narrator](#the-narrator)
5. [Playing](#playing)
6. [The world](#the-world)
7. [Running the game as GM](#running-the-game-as-gm)
8. [Operations](#operations)
9. [Development](#development)

---

## Architecture

```text
PLAYER
  |
  v
Discord / Python interaction layer
  |
  +-- hubs, guided /action UI, typed play
  +-- RAG / NPC memory / canonical context
  |
  +------------------------+
  |                        |
  v                        v
Go game engine         AI narrator (read only)
AUTHORITATIVE              |
  |                  routine / epic chains
  |                  -> openrouter/free
  |                  -> procedural fallback
  |                        |
  +------------+-----------+
               v
            Discord

Go -> SQLite WAL + batched transactions
```

Three services, one database:

| Service | Owns |
| --- | --- |
| `xianxia-engine` (Go) | every game and admin action, the canonical clock, native batched world simulation, the only connection to `data/xianxia.sqlite3`, backups, health |
| `xianxia-bot` (Python) | slash commands, hubs and panels, RAG and NPC context, permissions, presentation, the narrator chains |
| `xianxia-dashboard` (Python, optional) | the authenticated GM control plane and Discord server setup |

The rules that keep it that way:

- **Go owns canonical mechanics and production SQLite access.** Python never opens the production
  database; it talks to the engine over HTTP.
- **AI is narration-only.** Intent is chosen through a guided UI, deterministic mechanics resolve the
  result, and the model describes it afterwards. It cannot write rewards, deaths, relationships,
  travel or history.
- **Gameplay survives AI outages.** When every route fails or the day's free budget is spent,
  procedural narration steps in and play continues.
- **RAG never creates truth.** Retrieval is deterministic and SQLite-first, permission-filtered before
  scoring, and hidden or faction-only knowledge never reaches a viewpoint that should not have it.
- **Every GM mutation is audited** in `admin_audit_log`, from Discord and from the dashboard alike.

## Quick start on QNAP

Requirements: QNAP Container Station or Docker with Compose V2, a Discord bot token and guild id,
an OpenRouter API key, and a persistent `./data` directory. No GPU.

### 1. Configure `.env`

Copy `.env.example` to `.env` and fill the five required values:

```env
DISCORD_TOKEN=<discord bot token>
GUILD_ID=<discord server id>
OPENROUTER_API_KEY=<OpenRouter API key>
ENGINE_AUTH_TOKEN=<random secret shared by bot, dashboard and engine>
DASHBOARD_TOKEN=<random secret, at least 20 characters>
```

Generate a secret with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`. Everything
else has a working default; `docs/CONFIGURATION.md` explains each key in the order the file lists them.

The default narrator configuration is:

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
OPENROUTER_MAX_REQUESTS_PER_DAY=50
OPENROUTER_TIMEOUT_SECONDS=30
OPENROUTER_EPIC_TIMEOUT_SECONDS=60
```

### 2. Start, watch, stop

```bash
chmod +x startup.sh stop.sh
sudo ./startup.sh                                         # validates Docker, checks secrets, builds, starts
docker compose logs -f --tail=150 xianxia-engine
docker compose logs -f --tail=150 xianxia-bot
docker compose --profile dashboard logs -f --tail=150 xianxia-dashboard
sudo ./stop.sh                                            # removes containers, keeps .env and ./data
```

`startup.sh` starts the dashboard when `DASHBOARD_ENABLED=true`.

### 3. Set up the Discord server

Open the GM dashboard at `http://<NAS LAN address>:8090`, go to **Discord Setup** and run
**Full Setup**. It creates or repairs the base channels, the realm-capital channels gated behind
presence roles, the live-auction channels, `#bugs`, `#playtest` and the `#xianxia-info` guide, and it
is idempotent. See [Discord server setup](#discord-server-setup).

### Reset the world

```bash
./reset_database.sh
```

Wipes every character, NPC, family, sect, war, event and history entry and starts a new game.
It takes a safety backup first, asks you to type `RESET`, leaves Discord channels alone (run
**Repair** afterwards), and restarts the stack. `--help` lists `--yes`, `--no-backup`, `--no-restart`.

## Running without Docker

Python 3.12+, Go 1.23+ and the SQLite development library (`libsqlite3-dev` on Debian/Ubuntu) for
the Go CGO binding.

```bash
cd go_core && go run ./cmd/xianxia-core        # the engine, on 127.0.0.1:8081 by default
# in another shell:
export GAME_ENGINE_URL=http://127.0.0.1:8081
python -m app.database.bootstrap
python -m app.bot
python -m app.dashboard                        # optional
```

Outside Docker the dashboard binds `127.0.0.1` when `DASHBOARD_HOST` is unset, as does the bot's
health listener when `HEALTH_HOST` is; the shipped `.env.example` sets both to `0.0.0.0` so a
bare-metal run behaves like the Docker one.

## The narrator

### Routes

Two chains, **routine** (talk, guided actions, exploration, hunts, ordinary scenes) and **epic**
(breakthroughs, sect trials, major events). Each walks its primary model, then its fallback, then
`openrouter/free`, then procedural narration:

```text
google/gemma-4-31b-it:free
        | fail / timeout / 429
        v
openrouter/free
        | fail / account quota exhausted
        v
procedural narration
```

The five chain slots are chosen on the dashboard's **Narration Routes** panel from OpenRouter's live
free catalogue, stored through the engine and audited, and applied to the running bot without a
restart; `.env` is the baseline. `OPENROUTER_REQUIRE_FREE=true` rejects paid model ids, and every
request switches model reasoning off (`OPENROUTER_DISABLE_REASONING=true`) because the two ways the
free chain has failed in production are a model spending the budget thinking and returning nothing,
and a model returning its thinking as the story.

Every day (`ROUTE_AUDIT_HOURS`) the bot pings each route with the cheapest call the API takes and
retires the ones that answer `401`, `403` or `404` until the next pass; `429`s and timeouts retire
nothing. **Systems → AI Routing** on the dashboard shows the chains in their real order, what was
served by AI versus fallen back, and each route's verdict.

### Your own Google key (optional)

Set `GOOGLE_AI_STUDIO_API_KEY` and one more hop appears at the front of both chains, called
directly rather than through OpenRouter, so it is not charged against OpenRouter's daily free budget.
It is the most effective single change against procedural fallbacks. Get the key at
[aistudio.google.com/api-keys](https://aistudio.google.com/api-keys); it is narration-only and goes
through the same guards as every other route. You can also paste that key into OpenRouter's
**Google AI Studio** integration at
[openrouter.ai/settings/integrations](https://openrouter.ai/settings/integrations) so the free Gemma
route draws on your own quota instead of the shared pool.

### The budget

OpenRouter's free models allow 20 requests a minute and **50 a day** while the account has under $10
of lifetime credits, **1000 a day** at $10 or more. The dashboard's ten-dollar switch
(`credits_topped_up`, `OPENROUTER_CREDITS_TOPPED_UP` as baseline) picks which allowance the router
budgets against. Once the day's budget is spent the router stops locally rather than making requests
it knows will be refused, and **/admin → Server → Ai Status** says so.

Since v0.31.0 a live call is made for three reasons only: an NPC answering a player, an epic beat,
or an explicit ask — the **Narrate it** button under an exploration or hunt result, an @mention, or
the GM's `ai_routine_narration` automation flag. Everything else reads from a procedural pool in
`content/world.json`, so a quiet day costs nothing.

### Rate limits and the chat monitor

One per-player bucket (`TYPED_PLAY_BURST`, `TYPED_PLAY_PER_MINUTE`) meters every door to the
narrator, so one player cannot drain the shared allowance. The limiter is fail-fast: a refused line
is answered with the wait, never queued.

**/admin → Server** carries two GM-only AI actions: `ai_status` (narrator health from counters, no
prompts, no player text) and `chat_digest` (reads a channel over a window and reports what players
did, where they got stuck and what needs attention, on the same free chain, map-reduce style, capped
by `MONITOR_*`). The digest needs the Message Content intent: `MESSAGE_CONTENT_INTENT=true` in `.env`
and the intent enabled in the Discord Developer Portal.

Both HTTP listeners bound every request head before authentication runs
(`HTTP_MAX_REQUEST_LINE_BYTES`, `HTTP_MAX_HEADER_LINES`, `HTTP_MAX_HEADER_BYTES`,
`HTTP_HEADER_DEADLINE_SECONDS`, `HTTP_HEADER_LINE_TIMEOUT_SECONDS`, `HTTP_MAX_CONNECTIONS`) — generous
for a browser, mean for an attacker.

## Playing

### Hubs and panels

`/menu` opens one panel of every hub, four rows of four - You, World, Doing, Home - under a header
that says where you stand, your realm and stage with the essence, and what is waiting (a trade
offer at the inn), with Begin when there is no character yet and Back to the hub you left
(v1.0.0-rc.3); `/me` opens the player dashboard. Each hub — character,
cultivation, world, travel, craft, realm, items, combat, economy, inner world, beast, abode, family,
quest, sect, NPCs — is a live panel with one visible, tappable row per action. A hub's pages are
named after the work rather than the commands: the cultivation hub is Cultivate, Body, Path and
Arts (v1.0.0-rc.4), and a page that gathers several commands names its rows in full, guided inputs for
every parameter (every id has a picker), owner locking and a refresh. One message is the whole GUI
(v0.40.0): every panel's Menu button swaps it into the menu in place and the menu opens any hub in
the same message. A plain result is shown inside the panel, in a result block above the actions,
paged when long, with the next steps it names as buttons under it; Refresh clears it. An embed, a
reply with its own buttons or a file lands beside the panel, which stays live underneath. The
header carries a Here line saying what the place you stand in is and who is about; a chain of
pickers is one message that changes; a red button asks once before it runs; a panel that goes quiet
for fifteen minutes keeps a Reopen button. Battles and events have their own in-place panels.

### Typed play

With `AUTO_NARRATE=true` the bot listens in realm-hub channels, private scene threads and
`RP_CHANNEL_IDS`. A line is one of three things:

| You type | What happens |
| --- | --- |
| `$ I explore the ravine` | The prefix marks an action. A deterministic router turns it into the same handler the hub button runs and the engine resolves it; a root may take its arguments from the line (`$ I travel to Greenriver Town`, `$ I drink a healing pill`, `$ I give the pill to Li Feng` - a trade offer to a player who is here, `$ I sell the sword to the smith`). |
| `Qiao, what is the caravan carrying?` | A line addressing an NPC who is present is dialogue: `/talk`. |
| anything else | Speech, recorded as context. No reply, no call. |

The router never calls a model: a verb table (`content/typed_play.json`), entity resolution against
who is actually present, and a picker when readings tie. `TYPED_PLAY_PREFIX` is one character;
`$` by default.

### Cultivation, sects and manuals

Realms and stages, breakthroughs and tribulations, spiritual roots, bloodlines and physiques, laws
and techniques, aptitudes, seclusion with background cultivation, and a lifespan that ends in
Samsara and a new incarnation. The cultivation hub opens on a sheet the engine computes
(v1.0.0-rc.3): the essence bar, the stance and when the next session is ready, the odds of the
next breakthrough and what moves them, today's multipliers, the body path and the Insight XP.
Meditation has a stance the engine keeps and applies to every session - Circulate for the full
gain, Refine for a fifth less and two Insight XP banked a session, Force for a third more and a
fifteen-in-a-hundred qi deviation that is a real condition to treat. A breakthrough shows its odds
before the roll. Stage 9 into a new realm is a gate: it opens to an insight banked from Insight XP
(**/cultivation → Cultivate → Insight**; five at the mortal gate, five more a realm) or to a completed Realm
Perfection, and the insight is spent on the crossing. Where you sit is worth something too
(v1.0.0-rc.4): a road-side shrine, a temple quarter, a sect gate, your own chamber or a deployed
array all gather faster, and the Here line, the result and the sheet name the ground and its rate.
Insight XP buys more than the gate: a seized moment is one more roll after a failed breakthrough at
the same stage, once a stage, and `spend_insight` puts it into a Law comprehension. Every 2d10 roll
prints the chance it had beside what fell. A session is a share of the stage it fills rather than a
flat number (v1.0.0-rc.5), so a realm takes about the same hundred sessions at Divine Transformation
as at Body Tempering and the cultivate cooldown sets the calendar; crossing a realm raises your
attributes, one point of will and one of your path's own; and the worlds above the mortal one are
thick with qi, which is what ascending is for. A sect is joined through an entrance trial before its examiner; every
public sect bestows its own entry manual on passing, chosen inside the trial transaction, and the
sect residence grows facility by facility with contribution points. A manual is an item, studied
through the engine, which enforces its realm requirement; the whole 160-manual catalogue is content.

### Quests and commissions

Quests come from three places: the content's static quests, the **Quest Forge** (a GM turns a few
sentences of story into a checked, approved quest), and **commissions** — work offered by a named
NPC with terms, a deadline the engine enforces on its tick, and a cooldown for abandoning it. Since
v0.38.0 every city posts its own: a quest pavilion in each capital, a notice board at each gate.
Objectives are the small vocabulary the engine tracks — explore a place, talk to a person, take a
scene action, discover or pass a sect trial — so progress is mechanical, never narrated.

## The world

Four worlds — Mortal, Spiritual, Immortal, Celestial — each with a realm capital and eleven cities,
joined by roads with travel time, danger and encounters, and a place on every road.

### Cities

- **Arriving.** Every walled city has a gate on each compass side that has a road, and both ends of
  a road agree on the compass. A road journey ends at the gate facing the road you came by. The
  capitals have four compass districts behind their gates (noble quarter, temple quarter, lower
  town, ministry row); every other city has one drawn from its terrain. Every gate and district has
  its own named people, and only the people of the part you stand in are in the scene. Inside the
  walls everything is a walk apart with **/travel**.
- **Shops.** A hundred and four, differing by city: the kind follows the city's character (a smithy
  in Emberforge, an apothecary in Jadewood, a talisman hall in Moonfen, an array workshop in
  Ashenwall), the tier follows the world, and a capital's shops are a tier better and a quarter
  dearer. Found by walking the city with **/world → Explore**, entered with **/travel**, traded in
  with **/economy → City Shops**; shelves refill on the shop's clock, fuller in a thriving city.
- **City life**, under **/world → City**: **Look** (gates, districts, who is here, whether the city
  is thriving), **Board** and **Accept** (the city's commissions and, in a capital, the wanted list),
  **Envoys** (the sect envoys' hall in a capital's temple quarter puts every sect gate in the world
  on your map), **Rumours** (the city's history, through the same viewpoint gate the narrator uses),
  **Inn** (who is in town, which merchants are at the corner table, and the inn's common-room
  thread). Every trade moves the city's prosperity, and prosperity shows on the shelves and at the gate.
- **Auction houses.** Every city has one, entered through its warded door; a capital's is grand,
  a smaller city's a local floor with a paragraph of its own. Each grand house has a live Discord channel and a world's local
  floors share one, where lots are posted, bid on and struck as it happens.
- **Travelling merchants.** Eight, two a world, each a named NPC walking a fixed loop of cities and
  sitting at the inn when in town. They keep a shop of their own, buy what an auction floor could not
  sell, bid on the floors within the market's valuation with their purse as escrow, and resell it
  all to whoever meets them — in a city, on the same stretch of road mid-journey, or at a waystation.
- **Trade at the inn.** Two cultivators at the same inn trade directly under **/economy → Trade**:
  one offers what they give and what they want, the other accepts or declines. Nothing moves until
  the accept, and the accept checks both hands again.

### The roads

Fifty-three roads join the cities, and every one of them has a place on it (v0.39.0): a
**waystation** with a stall and a walled yard, a **hunting ground**, a **ruin** or a wayside
**shrine**, each with someone who lives there. A waystation or a shrine stands on the road itself
and is found by whoever walks the leg; a hunting ground or a ruin lies off it and is found one
time in two, or by exploring from either city. A site is reached with **/travel** as half the leg
from either end, and from it the road leads on to either end and nowhere else. A waystation sells
what the road takes out of you and stands on the merchants' road; a hunting ground makes the hunt
easier and the spoils richer; a shrine forbids the hunt and steadies the mind; a ruin gives up
twice what open ground would, and is where the secret realms open.

### Sects, goods and realms in every world

Each higher world has two sects of its own — a gate, an examiner, a trial, an entry manual — and the
envoys' hall of its capital names them: the Jade Meridian Sect and Thousand Beast Valley
(Spiritual), the Heavenblade Immortal Sect and the Ashen Lotus Pavilion (Immortal), the Celestial
Mandate Academy and the Void Serpent Cult (Celestial). Each world has its own ore, herb, weapon,
armour, pills and talisman, with recipes; a world's shops stock its own goods and its merchants
carry them. Eight secret realms, five of them at ruins by the roads, open in turn on the world tick
— one every three game days, at its own entrance, for its own hours — as well as when a player at
the entrance stumbles on the opening; each holds an inheritance and a relic.

### The economy

Dynamic regional markets with supply, demand and a price index, rotating black markets, caravans
you dispatch and settle, protected auctions with escrowed bids, currencies per world, and a
storage system from pouch to spatial ring. Item provenance is kept, and manuals never reach a market.

### NPCs with lives

NPCs cultivate, travel, work, marry, raise descendants who mature into simulated NPCs, take masters
and disciples, join and defect from factions, are injured, recover, age and die — on the engine's
native batched tick (`npc_civilization` daily, `npc_life` weekly, economy, black markets, sect
politics and clan dynamics on their own intervals), with bounded catch-up after downtime. Each has
a personality, a want, a fear and a secret for the narrator, and a memory of you. The narrator can
describe all of it and change none of it.

### History and memory

`world_history_events` records what mechanically happened — deaths, battles, succession, discoveries,
openings, marriages, promotions — separately from what is true now, which always wins. Rows carry
visibility levels (`public`, `participant`, `faction`, `hidden`); hidden rows never reach the
narrator, and a focused NPC does not inherit the player's participant-only knowledge. RAG is
deterministic SQLite FTS5 with no embeddings: live structured state, permission-filtered candidates,
deterministic scoring, a small scene packet. Raw player text is tokenised before it touches `MATCH`.

## Running the game as GM

### From Discord

`/admin` is a hub for administrators: server setup and diagnostics, the `#playtest` board, world
events, player inspection, teleport, revive, currency, karma and cooldowns, moderation (mute, freeze
and ban with an expiry the engine enforces, force-end-scene, all undoable from the audit log), sect
and NPC management, simulation automation and forced runs, the Quest Forge, backups and audit logs.

**The playtest board.** **/admin → Server → Playtest → Post** puts one message per hub page in
`#playtest`, pre-reacted ✅ ❌ 💡; testers react and reply under the page, and **Report** tallies the
reactions with names and links every flagged page.

**Quest Forge.** `/admin world questforge <story>` drafts a quest in the game's own shape from a few
sentences, checks every place, person and item it names against the content, and offers it with
**Approve** / **Discard**. `QUEST_FORGE_AUTO=true` drafts one per notable world event, never
auto-approved. Rewards are capped and granted by the engine on completion.

### The GM dashboard

Set `DASHBOARD_USERNAME`, `DASHBOARD_TOKEN` (at least 20 characters), `DASHBOARD_BIND_ADDRESS`,
`DASHBOARD_PORT` and `DASHBOARD_ADMIN_WRITES=true`, then open `http://<NAS LAN address>:8090` from
the LAN. Do not port-forward it to the internet; put a TLS reverse proxy in front if it must be
reached from elsewhere, passing the original `Host` through or listing the public origin in
`DASHBOARD_ALLOWED_ORIGINS`.

Security: HTTP Basic auth on everything but `/livez`; five wrong passwords from one address lock it
out for fifteen minutes; a `POST` whose `Origin` does not match its `Host` is refused; state changes
need a JSON `POST` with the `X-Xianxia-Admin: 1` header; reads go through Go-owned query sessions,
never SQLite directly; every mutation runs in the engine and lands in `admin_audit_log`;
`DASHBOARD_ADMIN_WRITES=false` makes the console read-only.

Views: Overview, Timeline, NPCs, Families, Sect Politics, Conflicts, World Events, Player Activity,
Cultivation, Crafting & Assets, Exploration, Economy, Samsara Dynasties, RAG Memory, Autonomous
Decisions, AI Routing, and the Admin Console (time, forced simulation, intervals, automation
switches, teleport, currency, karma, revive and recovery, backups, `PRAGMA optimize`, `VACUUM`,
the audit log) and Narration Routes. `/api/capabilities` is the frontend/backend coverage contract,
and CI fails on drift.

### Discord server setup

**Discord Setup** on the dashboard talks to the bot's private control endpoint (it never sends
Discord mutations through Go) and can inspect the guild, diagnose permissions and role hierarchy,
run **Full Setup** or **Repair Server** without touching game data, gate every realm capital behind
its presence role (a capital is visible only while your character is in it), synchronise slash
commands and realm roles, rebuild `#xianxia-info`, bind existing channels, and run **Fresh Start**,
**Teardown** or **Reset World** behind typed confirmations. Every state-changing operation writes an
audit entry.

## Operations

### Backups

Backups are made by the engine through the SQLite backup API, from Discord or the dashboard:
retention, a size cap, optional sealing with an operator key and an off-box copy since v0.32.0
(`docs/CONFIGURATION.md`, "Backups"). Never copy a live WAL database by hand as your backup.

### Updates

Releases are GitHub Releases built by CI from a tag; `vX.Y.Z` is stable, `vX.Y.Z-beta.N` a beta.
The bot checks the channel every `UPDATE_CHECK_HOURS` and posts "Update available" once per newer
release; it never installs anything. On the NAS:

```bash
./update.sh                     # offline: install a ZIP you placed in ./updates
./update.sh --check             # is there something newer?
./update.sh --fetch             # download the newest ZIP and verify its SHA-256
./update.sh --upgrade           # fetch, then install: backup, stop, swap, start, roll back on failure
```

`RELEASE_MANIFEST.sha256` inside the tree proves an unpacked release is intact; `update.sh` checks it.

### Health

- Engine: `GET /livez`, `GET /readyz`, `GET /v1/db/status`.
- Dashboard: `GET /livez` is unauthenticated for container health checks.
- If narration goes flat after an image rebuild, check **/admin → Server → Ai Status** for a TLS
  banner first. If SQLite reports contention, verify only the engine opens the database.

Keep the whole `data/` directory persistent across rebuilds. The current schema is **39**; every
historical migration is kept so an old database upgrades in place.

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
make install-dev
make check              # lint + format-check + test-python + test-go, what CI runs
```

| Command | What it runs |
| --- | --- |
| `make test-python` | `pytest -q` — `unit/` (Python-only), `integration/` (still Python-owned orchestration), `contracts/` (the Python↔Go, startup, deployment and release boundaries) |
| `make test-go` | `cd go_core && go test ./...` — every Go-owned rule and state transition |
| `make lint` | `ruff check app scripts` and `go vet ./...` |
| `python scripts/check_dashboard_implementation.py` | the dashboard drift and coverage gate |
| `python scripts/playtest_engine.py --launch` | the engine half of the playtest: every roadmap loop driven through a scratch engine |
| `python scripts/playtest_checklist.py` | regenerates `docs/playtest/v<version>.md` |
| `python scripts/release_manifest.py --write` | regenerates `RELEASE_MANIFEST.sha256`; run it last before a release |

Layout: `app/bot` (Discord), `app/rules` (pure content helpers), `app/ai` (router, narrator, RAG),
`app/ops` (config, engine client, health), `app/dashboard`, `app/database` (repository API over the
Go transport), `app/simulation`; `go_core/internal/{game,simulation,storage,server}`;
`content/world.json` is the world. `CLAUDE.md` holds the working rules for the codebase:
do not reintroduce a Go shadow mode, do not open production SQLite from Python, batch world work,
keep AI non-authoritative, keep viewpoint permissions deterministic, audit GM mutations, and test
Go-owned rules in Go.

## Version history

`VERSIONS.md` is the changelog, every release since 0.18 with its schema; `docs/history/` keeps the
per-release notes and the migration record from before that.
