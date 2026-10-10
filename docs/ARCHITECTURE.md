# Architecture notes

How the subsystems of this repository work, moved out of `CLAUDE.md` unchanged so that the file loaded
into every session stays small. `CLAUDE.md` keeps the authority split, the design rules and the
layouts, and names each of these in one line; read the one that matches the subsystem you are about
to change. The history of how each came to be, and the faults found along the way, is in
`docs/FINDINGS.md` (look a release up with `grep -n 'rc.48' docs/FINDINGS.md`).

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
rather than taking `/talk`'s word for it. Since v1.22.1 an ordinary `/explore` runs the same find (`searchHereTx`; `docs/FINDINGS.md`, "An explore finds the missing").

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
`docs/FINDINGS.md`, "The playtest touches everything" (its rc.38 paragraph, "What only the world makes"), for why it writes the tick's own row.

The Player Editor's **Quests** card (v1.4.1) is the only lever on a player's `character_quests`. An
objective is counted only while its quest is `active`, so a report made too early (the household
lesson passed before "The Last Lesson" was handed over) is lost for good. `admin.player.quest_progress`
replays one report and `admin.player.quest_complete` fills every objective; both go through
`questProgressTx`, the body `quest.progress` itself runs, so reward, commission, standing, follow-on
and the beginner catch-up are one statement. Neither is in `reversibleAdminActions`: the reward is paid.
