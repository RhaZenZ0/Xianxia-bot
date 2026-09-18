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
`synchronous=NORMAL`. Current schema version is 53; historical migrations are kept so old databases
can upgrade in place — see `VERSIONS.md` for the full schema/release history.

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
command tree). `app/bot/maintenance.py` is the one rule all four call.

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

## Release delivery

- A release is handed over as the zip — `xianxia_rp_v<version>.zip` — and its `.zip.sha256`
  sidecar. No `..._to_..._code.patch`: a diff nobody applies is noise, not assurance.
  The sidecar is **not** optional and this file used to say it was: `update.sh --fetch`
  downloads it, verifies the archive against it, and refuses to install one that has no
  sidecar ("refusing an unverifiable archive"). The release job attaches both for that
  reason. A hand-unpacked zip still never needs it read by a person — the updater reads it.
- `RELEASE_MANIFEST.sha256` is a different thing and stays: it lives *inside* the tree, is
  regenerated by `scripts/release_manifest.py --write`, and is what proves the shipped tree is
  intact after unpacking. Keep running it before every release.
