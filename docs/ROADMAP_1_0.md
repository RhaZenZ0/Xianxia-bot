# Roadmap to v1.0.0

Revised at **v0.28.0** (2026-09-09), Authority II marked shipped at **v0.30.0** and the Narrator budget at **v0.31.0** (2026-09-10). The first version of this file was
written at v0.20.4 from an inventory of the tree and revised once at v0.21.6.
Since then seven minor releases shipped, and only two of them were milestones
this file had planned: Authority I closed (v0.23.0) and Commissions landed
(v0.22.0). The other five — the Quests workbench, the dashboard remake, and
three releases on narration-route resilience — took the version numbers this
file had reserved for Hardened I, Hardened II, the pickers and the playtest,
so its numbering no longer describes anything. This revision re-takes the
inventory item by item against the code as it is today, records what closed
on the way, renumbers what remains, and adds a content track for the work
that was proposed as "1.0" in September 2026 but is not one of 1.0's bars.

## What 1.0 means

Three bars, unchanged since v0.20.4. 1.0 is **not** "feature-complete"; it is
the release after which these three statements are true and guarded by tests:

1. **Engine authority is finished.** Python owns no game rule. Every gameplay
   mutation goes through `authoritative_action(...)` into the Go engine;
   every rule table Python still holds is presentation, or is a copy of one
   the engine owns and is deleted.
2. **Hardened.** The engine door is never open, the dashboard door has a
   lock that survives a brute-force attempt, a player cannot flood the bot
   or the narrator, a GM can act on abuse from inside Discord, and backups
   have a retention policy.
3. **Gameplay-complete.** Every hub reachable from `/character` has no known
   dead end: every id-taking parameter has a picker or an explanation, every
   explorable location can be explored, every NPC the narrator is handed has
   the fields it reads, a player can obtain and learn a manual, and a written
   playtest of all sixteen hubs finds nothing on the punch list.

A fourth statement is not a bar but is the reason the narrator exists: **the
AI does one thing only a model can do** — voice a person with an agenda — and
every call it makes is one the game could not have resolved
deterministically. v0.26–v0.28 spent three releases making the *routes*
survive (a route outside OpenRouter's budget, a daily liveness audit, route
selection from the dashboard). That was the right emergency; it is not the
same as spending fewer calls, which is still open below.

## Where we are

| Bar | State at v0.28.0 |
|---|---|
| Authority | **Player side closed** (v0.23.0): `PLAYER_MUTATIONS` is empty and the gate asserts it stays so. **Derived inputs and the DB layer closed** (v0.30.0): the engine derives the seclusion environment; market pricing and every `app/simulation/world.py` read are engine queries; `app/database/core.py` writes only the thirty-eight presentation writers listed in `PRESENTATION_WRITES`; no rules are imported below `bot`/`ai`/`dashboard`; every `app/rules` function has a production caller. **Still open:** `get_world_clock` re-anchors `world_state` on a scale change (a second copy of the clock arithmetic), and `current_world_time` reads the anchor with Python arithmetic rather than asking the engine - the last Python-side clock. |
| Hardened | **Hardened I shipped (v0.29.0):** the engine refuses to run or answer without a token on both sides, the dashboard locks a guessing address and refuses cross-origin mutations, both listeners default to loopback outside Docker, requirements are hash-locked and images digest-pinned, CI runs `-race`. Also: input fence and typed-play budget (v0.21.1, v0.21.5), `admin.audit.undo_last`, the P0 and second external reviews (v0.22.2, v0.23.1). **Still open (Hardened II):** no moderation from Discord and no moderation expiry; backups have no retention. |
| Gameplay | Catalog materialised (154 manuals); every location has encounters and sense hints; every NPC has narrator fields; the seven orphaned autocompletes are attached and `/battle challenge` has a picker. Every location has an NPC (the three samsara arrival grounds got keepers after v0.28.0). **Pickers closed (v0.33.0):** every id parameter has one, typed play fills `/travel` and `/use` from the line, `fate.adjust` is gone. **Open:** no `KNOWN_LIMITATIONS.md`, no playtest. |
| The AI | **Closed as a bar (v0.31.0):** a call fires only for dialogue, an epic beat, or an explicit ask - `/explore` and `/hunt` read from the procedural pool and offer a *Narrate it* button (or the GM's `ai_routine_narration` flag); one per-player bucket meters every door; the pool has 84 variants across seven scene kinds and four world tiers; the ten-dollar switch picks the 50 or 1000 a day allowance from the dashboard; the AI Routing page shows calls by purpose and refusals by door. The AI Studio route (v0.26.0) remains the way past the ceiling for an operator with a key. |

Test surface: ~960 Python test functions across unit, integration and
contract layers; 360 Go test functions in 52 files. CI runs the Go suite
under `-race` since v0.29.0; before that the v0.21.6 claim that it was green
under the race detector was a local run, enforced nowhere.

**The "never met a live server" caveat is retired.** v0.26.1 fixed two
findings from a production deployment of v0.26.0, and the route-audit and
route-selection releases were driven by what that deployment showed. The
code runs on the NAS. What has *not* happened is the written, hub-by-hub
playtest, which is still the last gate before rc.

Document drift worth naming because rc will have to fix it: `README.md`
still announces v0.21.0 in its title and "Current release" line;
`DEVELOPMENT.md` says Go 1.23 while `go_core/Dockerfile` builds on 1.26.

## The release channel

Unchanged: GitHub Releases on `RhaZenZ0/Xianxia-bot`, built by
the `release` job of `.github/workflows/ci.yml` from a tag, with a `.sha256` sidecar that
`update.sh --check/--fetch/--upgrade` verifies on the NAS
(`UPDATE_CHANNEL=stable` or `beta`). A hand-delivered release is the zip
alone, with `RELEASE_MANIFEST.sha256` inside the tree as the integrity
proof after unpacking. Each milestone ships as **one or more betas first**,
then stable. Point releases are fixes only — v0.21.x bent that six times in a
day and v0.25.x bent it four times; the pattern has not been broken, and
this file says again that it should be.

## Milestones

Each milestone names its **gate**: the test or check that must exist and
pass before the stable tag. Numbers from v0.29 are the intended order, not a
promise; if a milestone is overtaken again, this file is revised again
rather than left describing the wrong release.

### Authority I — the last player-side mutations — **shipped v0.23.0**

*Closed. `PLAYER_MUTATIONS` in `tests/python/contracts/test_authority_boundary.py`
is empty and `test_the_v0_21_backlog_stays_closed` asserts it stays that way.
The 21 rows became nine engine actions, each a single transaction with a
receipt; detail in `VERSIONS.md` under 0.23.0.*

### Commissions — **shipped v0.22.0** *(schema 29)*

*Shipped: `commission.accept/resolve/expire`, the giver roster (v0.22.1,
schema 30), the Quests workbench that edits them (v0.24.0, schema 32, with
pinned terms so an edit cannot rewrite an accepted deal). Step 5 of the
design — seeded invention, where a giver invents a personal commission from
RAG reads — is **deliberately unbuilt** and moves to the content track
below. `docs/COMMISSIONS_DESIGN.md` is the design; its "where it sits"
section refers to the old numbering and should be marked shipped at rc.*

### v0.29 — Hardened I: the doors *(was v0.25)* — **shipped v0.29.0**

*Shipped. Gate: `tests/python/contracts/test_security_defaults.py` and
`TestNewRefusesAnEngineWithoutAUsableToken` in `go_core/internal/server`.
Every item below landed as written; detail in `VERSIONS.md` under 0.29.0.*

Moved to the front. Every item is small, two external reviews have already
named the first one, and the code is now in production; there is no reason
left to leave the engine door open while the larger milestones run.

- **Engine token required.** `Server.authorized` returns `true` when
  `authToken` is empty; fail closed. `app/ops/config.py` validates
  `ENGINE_AUTH_TOKEN` the way `DASHBOARD_TOKEN` already is, so a blank value
  is a startup error on both sides and not only in `startup.sh`.
- **Dashboard lock.** Failed-attempt throttle and temporary lockout per
  source address in `_authorized`; Origin check on mutations alongside the
  `x-xianxia-admin` header.
- **Bind defaults.** Under compose, `DASHBOARD_BIND_ADDRESS=127.0.0.1`
  publishes the port and the bot's health port is `expose`-only, which is right
  and was documented in v0.25.2. Bare metal still defaults `DASHBOARD_HOST`
  and `HEALTH_HOST` to `0.0.0.0`: default both to loopback when not under
  Docker, with a documented reverse-proxy recipe for LAN access with TLS.
- **Supply chain.** `discord.py` and `openai` are pinned exactly;
  `aiosqlite`, `python-dotenv` and `httpx` are ranges. Pin them, add a
  hash-locked requirements file, pin `python:3.12-slim`, `golang` and
  `debian:bookworm-slim` by digest.
- **`-race` in CI.** Add it to `ci.yml`'s Go step so the claim is enforced.
- ~~Split the engine credential~~ — **declined 2026-09-07**, reasoning
  recorded here so it is not re-raised: one guild, one operator, all
  services on one NAS behind one trust boundary; a split adds three secrets
  for little isolation. The cheap middle, if ever wanted: refuse *writable*
  generic SQL for the bot once `app.database.bootstrap` has finished, keeping
  `/v1/db/*` writes for the migration entrypoint.

**Gate:** `test_deployment_hardening.py` plus a new
`test_security_defaults.py`: blank engine token fails startup (Python) and
`NewServer` (Go); N failed logins lock; bare-metal defaults are loopback;
requirements are hash-locked; CI runs `-race`.

### v0.30 — Authority II: derived inputs, pricing, the DB layer *(was v0.24)* — **shipped v0.30.0**

*Shipped. Gate: the `test_v0_30_gate_*` tests in
`tests/python/contracts/test_authority_boundary.py` and `authority2_test.go`.
Every item below landed as written, with two things named rather than
claimed: `test_equipment_stat_parity.py` never existed (the DB-layer copy it
was said to hold together is simply gone), and the world clock is the one
Python-side copy of an engine rule left in the DB layer - listed in
`PRESENTATION_WRITES` with that reason, and in the table above as open.
Detail in `VERSIONS.md` under 0.30.0.*

Where Python does not mutate but *computes the input* the engine then
trusts, or keeps a second copy of an engine rule. None of it moved between
v0.21.6 and v0.28.0.

- `seclusion.start` receives `environment_mult` from
  `app/bot/commands/cultivation.py` (abode level and safe zone via
  `seclusion_environment_multiplier`); Go only clamps it. The engine
  recomputes it from state it already holds. (`forage.resolve`, the other
  example the v0.21.6 file named, now sends an empty payload — that row is
  closed; a payload test should hold it there.)
- Market pricing lives twice: `app/simulation/world.py` derives
  `sell = buy × 0.70` and `_market_tradeable` duplicates
  `bootstrap.go:marketTradeable`. One copy, in Go; Python asks for a quote.
- `app/database/core.py` still carries ~150 INSERT/UPDATE/DELETE statements.
  Sort them once, by table, into three bins: **presentation** (RAG and FTS
  triggers, `channel_messages`, thread and hub channel ids, `slow_query_log`,
  `operational_alerts`, `server_config`, schema bookkeeping) which stay and
  are allowlisted by name; **dead** (a gameplay table with no remaining
  caller after v0.23 — `UPDATE characters`, `UPDATE battles`, `INSERT INTO
  inventory` and their like) which are deleted with their methods; and
  **live gameplay** (anything a dashboard or simulation path still calls)
  which becomes an engine action. The v0.21 gate only scans callers under
  `app/bot` and `app/ops`; this milestone's gate scans the layer itself.
- `app/simulation/world.py` becomes read-only orchestration: its 21 raw-SQL
  reads move to engine query sessions.
- `equipment_power` and `formation_bonus` (`advanced_runtime.py`) are still
  called from `core.py`; they leave with the mutators that use them, and the
  three-way equipment stat duplication that
  `test_equipment_stat_parity.py` holds together collapses to the Go copy.
- Dead rule code with zero production callers is deleted:
  `random_encounter`, `random_explore_rewards`, `random_hunt`,
  `roll_unexpected_event`, `craft_quality`, `tribulation_tns`,
  `condition_effect`, `boss_phase`, `stable_percent`, `manor_qi_multiplier`,
  `manor_defense_power_bonus` — all confirmed at zero callers on 2026-09-09.
- `docs/history/V018_AUTHORITY_CLEANUP_ROADMAP.md` joined the V015/V016 lists
  there (v0.30.0).

**Gate:** `test_authority_boundary.py` asserts (a) no INSERT/UPDATE/DELETE
against a gameplay table in `core.py` outside a named presentation allowlist,
(b) `app/rules/` is imported at runtime only by `app/bot/` formatting paths
and `app/ai/` (`test_app_layout.py`), (c) every `app/rules` function has a
caller, (d) `seclusion.start`'s payload carries no multiplier.

### v0.31 — The narrator budget *(was v0.23)* — **shipped v0.31.0**

*Shipped. Gate: `tests/python/contracts/test_narrator_budget.py`. Every item
below landed as written; the ten-dollar switch (OpenRouter's 50 versus 1000 a
day) was added beside it because the budget page is where an operator looks
for it. Detail in `VERSIONS.md` under 0.31.0.*

Reshaped by what v0.26–v0.28 shipped. The routes are now resilient; the
remaining problem is that two handlers spend calls they do not need, the
per-user budget guards one door of three, and the procedural floor is thin.

- **Route by tier.** `narrate_exploration` (`exploration.py:399`) and
  `narrate_hunt_result` (`exploration.py:473`) spend a routine call each on
  results the engine already decided. Make them procedural-first; the model
  narrates them only on an explicit upgrade (the typed-play picker's
  *Narrate it*, a GM scene flag). Live calls are then dialogue, epic beats
  and explicit asks — nothing else.
- **Budget on every door.** `TYPED_PLAY_BUDGET` is spent only in
  `typed_play.py`; extend the same bucket to `serialized_user_action` (slash
  and hub paths) and evict idle locks from `_USER_ACTION_LOCKS`.
- **A fallback pool.** Procedural prose is what players read when the budget
  is spent or every route is retired, and it is hand-written per scene kind.
  Author a pool of variants per scene kind × location tier, chosen
  deterministically, so the floor reads well. The content track below adds
  item-grade vocabulary to the same pool.
- **Calls by purpose.** The AI Routing page shows AI-served versus
  procedural share (v0.27.0). Add calls by purpose (dialogue / epic /
  narrate-it / forge) and refusals by the per-user bucket, so the effect of
  this milestone is visible on the page that already exists.

**Gate:** a contract test that no `_generate` call site outside `talk_to_npc`,
the epic tier and the explicit-upgrade path runs by default;
`test_user_budget.py` extended to the slash path; a content test for the
fallback pool.

### v0.32 — Hardened II: moderation and data *(was v0.26)* — **shipped v0.32.0**

*Shipped. Gate: `tests/python/contracts/test_hardened_moderation.py` and the
Go expiry and retention tests it names. Every item below landed as written;
`ban` is its own flag that never expires and leaves the mute/freeze pair as
it found them. Detail in `VERSIONS.md` under 0.32.0.*

- **Moderation from Discord.** `admin.player.set_moderation` exists in the
  engine and is reachable only from the dashboard. Add `/admin player
  mute|freeze|unmute|unfreeze` with duration and reason, and `ban`. Expiry
  runs in the engine's simulation tick — today nothing expires a mute.
  `teleport` and `clear_battle` already have Discord registrations
  (`inspect_sim.py`); bring `force_end_scene` over with them.
- **Backups.** `storage.BackupTo` is the whole backup story. Add retention
  (keep N daily / M weekly), a size cap, optional encryption with an
  operator key, and an off-box copy step in `update.sh`.
- ~~Audit undo~~ — `admin.audit.undo_last` shipped (`admin_undo.go`), with
  redo semantics. Moderation actions must land in `admin_audit_log` with
  actor, target, reason and expiry so undo covers them too.

**Gate:** Go tests for moderation expiry and backup retention; a Python scan
that every "GM needs it live" dashboard op has a Discord registration; a
source check that moderation commands audit.

### v0.33 — Gameplay-complete I: the pickers *(was v0.27)* — **shipped v0.33.0**

*Shipped. Gate: `tests/python/contracts/test_hub_pickers.py`, which walks every
registered command rather than the five named below - and found three more
(`caravan_dispatch(item)`, `provenance_command(item)`, `reincarnate(path)`),
now covered. `fate.adjust` was removed, not wired. Detail in `VERSIONS.md`
under 0.33.0.*

- ~~Attach the seven orphaned autocompletes~~ — done; all seven in
  `economy.py` are decorated.
- **Providers or hints for the five remaining id parameters:**
  `artifact_bond(item)`, `artifact_awaken(item)`, `battle_act(action)`,
  `boss_start(boss)`, `secret_enter(realm)`. (`battle_challenge(target)`
  has one.)
- **Typed play, roots with parameters.** `content/typed_play.json` covers
  roots without parameters, the eight scene actions and `/talk`; `/travel`,
  `/use` and the group commands fall to the picker. Let a candidate carry
  one resolved argument (a destination, an item) when entity resolution
  found it, growing the table from what the picker shows players choosing.
- ~~Three locations with no NPC~~ — done ahead of the milestone: the three
  samsara arrival grounds each have a keeper (Ledger Warden Wen Shuang,
  Terrace Matron Gu Yanli, Provincial Registrar Mo Qingyan), and
  `test_world_content_gate.py` now requires every location to have one.
- `fate.adjust` is allowlisted and implemented in the engine with no Discord
  caller: wire it or remove it.

**Gate:** a `test_hub_pickers.py` that walks every hub action's parameters
and requires each id-typed one to have a provider or a hint;
`test_world_content_gate.py` gains "every location has at least one NPC, or
is on the named exemption list".

### v0.34 — Gameplay-complete II: the playtest *(was v0.28)* — **shipped v0.34.0, live columns open**

*Shipped as far as a machine can take it. `scripts/playtest_engine.py` drives
every loop named below through the engine and found two defects, both fixed
(a commission could never be turned in; Reset Cooldowns missed the trial
retry). `docs/playtest/v0.34.0.md` lists all 218 actions with the static
columns filled; its three live columns are the pass on the live server and
are Mitchell's to tick. `docs/KNOWN_LIMITATIONS.md` is the punch list. Gate:
`tests/python/contracts/test_playtest_gate.py`.*

*v0.34.1 gave the live half its instrument: the `#playtest` board, one
message per hub page that testers mark ✅ ❌ 💡 and reply under, tallied by
`/admin → Server → Playtest → Report`. It also shipped the travelling
merchants, the first feature ask from the playtest: the floor's last bidder,
walking loops of cities, met in a city or on the road.*

A written pass over all sixteen player hubs on the live server, hub by hub,
page by page, with a checklist per action (reachable, picker present, error
text actionable, narration fallback fired, engine result keys read), plus
the loops added since v0.21: type `$ I explore`, join a sect and study the
gift, take a commission from Qiao and complete, fail and abandon one, change
a narration route from the dashboard and watch it apply, edit a live quest
under each hold policy. Findings go into `docs/KNOWN_LIMITATIONS.md` — the
punch list — and are fixed or explicitly deferred past 1.0 there.

**Gate:** `docs/KNOWN_LIMITATIONS.md` exists, every entry is fixed or marked
deferred with a reason; the checklist is checked in under `docs/playtest/`
with the release it was run against.

### v1.0.0-rc.N → v1.0.0

Release candidates go to the **beta channel** only.

- Migration drill: a database from every shipped schema (v1 through 32)
  migrates to current with no data loss. `test_startup_health` covers v1 and
  an unversioned database; extend to the set.
- Backup → restore drill on the NAS, documented.
- Clean install from the README on a machine that has never seen the
  project, timed, with the gaps fixed.
- Documentation consolidated: README title and "Current release" line
  match `app/version.py` and a test holds them there; `DEVELOPMENT.md`
  names the Go version `go.mod` and the Dockerfile agree on; `VERSIONS.md`
  trimmed to one paragraph per minor with the per-release notes files kept
  as history; `docs/COMMISSIONS_DESIGN.md` marked shipped.
- `gofmt` clean; `make check` green from a fresh clone.
- Two weeks on the NAS at rc without a P1.

`v1.0.0` is the rc that survived, re-tagged.

## Order and dependencies

```
v0.29 Hardened I ──► v0.30 Authority II ──► v0.31 Narrator budget ──► v0.32 Hardened II
                                                                              │
v0.33 Gameplay I ────────────────────────────────────────────► v0.34 Playtest ◄──┘
                                                                     │
                                                              v1.0.0-rc.1 … ► v1.0.0
                                                                     │
                                            content track (items & grades, seeded invention)
```

Hardened I goes first because it is the smallest milestone with the oldest
open finding, and the code is now in production. Authority II follows
because every later item that touches a handler is cheaper once the handler
is "call, then format", and because the narrator budget's slash-path bucket
sits in the same `runtime.py` the DB-layer work reshapes. Gameplay I is
independent and can interleave with anything. The playtest waits for all of
it, because it is the check that nothing a player can reach regressed. The
content track is scheduled after rc on purpose: every item in it adds
player-reachable surface, and the playtest is the gate that says the
existing surface is sound.

## The content track: after 1.0, or beside it as a beta

Proposed in September 2026 as a "v1.0.0 roadmap" for a six-tier item rarity
system, forging, salvage and family-linked storage. It is good work and it is
not a 1.0 bar: 1.0 is authority, hardening and no dead ends, and a
twelve-week content system in front of the playtest is exactly the untested
surface the bars exist to stop. It lives here so it is not lost, reshaped to
fit what the tree already has — the proposal's inventory of the current
systems was written without reading them.

What exists today and must be built on, not beside:

- **Crafting** (`crafting_actions.go`): twelve recipes across Alchemy,
  Forging and Formation; 2d10 against a recipe TN; quality tiers Ordinary /
  Fine / Superior / Masterwork (Crude to Flawless for pills); profession
  levels 0–6 with XP; bonuses from abode, manor, family and active effects;
  `alchemy_batches` and pill toxicity. `/craft` and `/alchemy refine`.
- **Storage**: three container grades in `world.json` (Mortal 24 slots,
  Earth 80, Immortal 500 with a living-space flag), enforced on deposit;
  `storage.deposit/withdraw/upgrade`; the downgrade exploit closed v0.23.1.
- **Equipment**: `equipment_instances` with per-instance durability and a
  `quality` column; `unique` and `indestructible` flags; the one-per-character
  rule enforced by `admin.player.adjust_item`; `item_provenance`.
- **Birth families**: eleven archetypes with wealth, influence, tier, home
  city and mechanical boons (the weaponsmith family's forge access, the
  alchemy family's crafting bonus). Every character starts with 25 low-grade
  spirit stones, two spirit herbs, one spirit iron and the common pouch,
  regardless of family.
- **Inventory** is `(user_id, item_id, quantity)` stacks. Only equipment has
  per-instance rows.

The shape that fits, in build order, each step one Go action with its own
Go test and a Python boundary test:

1. **Grade on the catalog.** A `grade` field on `content/world.json` items
   using the ladder the game already speaks — Mortal / Earth / Heaven /
   Immortal (the storage grades, extended by one) — not Common / Uncommon /
   Rare / Legendary / Mythical, which is another genre's vocabulary and would
   be the fourth quality ladder in the tree. Affixes, if any, are fixed per
   catalog entry. Rolled per-instance variation is allowed on
   `equipment_instances` only, because that is the one table with a row per
   item; instancing consumables would break stacking, slot counts, market and
   auction trades, provenance and the RAG corpus.
2. **Grade-weighted rewards** in the Go hunt and forage reward paths
   (`applyCanonicalRewardTx`), with a distribution test.
3. **Craft quality feeds grade.** A Masterwork forge or Flawless refinement
   outputs the next grade up. Reuses the margin system rather than adding a
   second success roll or a station gate.
4. **Salvage.** One `item.salvage` action: fixed material yields per grade,
   provenance row, `admin_audit_log` row above a grade threshold. The
   genuinely new piece, and the material sink the economy lacks.
5. **Family tier sets the start.** Starting container grade and stones from
   family tier and wealth in `lifecycle_actions.go`, expressed as the
   game already expresses advantage — TN modifiers and stones, not flat
   percentage bonuses.
6. **Vocabulary in the floor.** Grade words in the procedural narration pool
   (v0.31), so an AI outage still reads correctly.

Uniques stay GM-granted and one-per-character; they do not enter a drop
table. Recipes stay in `world.json`; there is no dashboard recipe upload.

Also on this track: **seeded invention** (Commissions step 5), brought back
once the playtest shows the giver pool reads well.

## Landed outside the milestones since v0.21.6

- **v0.22.1–v0.22.5** — the giver roster (schema 30), the P0 review fixes
  (atomic tick, idempotent duplicate requests, rewards paid by the
  completing transaction), the restore barrier, duel legitimacy (schema 31),
  graceful engine drain.
- **v0.23.1** — eight logic errors from a second external review, the
  serious one an asset transfer across reincarnation.
- **v0.23.2** — the updater could not install on a QNAP; one manifest
  helper, two call sites, a test that there is only one.
- **v0.24.0** — the Quests workbench and pinned terms (schema 32).
- **v0.25.0** — the dashboard remade; five nav groups, one table component,
  "Wants your attention".
- **v0.25.1–v0.25.2** — typed-play prefix `$`; `.env.example` reorganised.
- **v0.25.3–v0.28.0** — narration route resilience: two dead defaults
  removed, the AI Studio route, the daily liveness audit and its 400 ladder,
  route selection from the dashboard through `admin.narration.set_chain`,
  the direct OpenAI provider removed.
- **v0.30.1** — one home, built up (schema 33): the sect residence grows with
  contribution points under rank and stage gates, and a homestead of one's own
  is founded at Deacon or higher; the six archetypes stay only for existing rows.
- **v0.29.1** — one GitHub workflow: the release job runs behind the CI checks
  on the commit they proved, instead of re-running its own copy of them.

## Keeping this file honest

Each milestone's gate is a test. When a milestone ships, its section gains a
one-line "shipped in vX.Y.Z — gate: `tests/.../test_x.py`" and the table
below is updated. When a release takes a milestone's number for something
else, the milestone is renumbered here in the same release, not left to
drift for seven versions as happened between v0.21.6 and v0.28.0.

| Milestone | Release | Status |
|---|---|---|
| Authority I | v0.23.0 | shipped |
| Commissions | v0.22.0 / v0.24.0 | shipped, less seeded invention |
| v0.29 Hardened I | v0.29.0 | shipped |
| v0.30 Authority II | v0.30.0 | shipped; the world clock read-through is the one named leftover |
| v0.31 Narrator budget | v0.31.0 | shipped |
| v0.32 Hardened II | v0.32.0 | shipped |
| v0.33 Gameplay I | v0.33.0 | shipped |
| v0.34 Playtest | v0.34.2 | shipped; the engine loops run, the checklist is on file, and the `#playtest` board collects the live pass; travelling merchants shipped beside it |
| v1.0.0-rc | | |
| v1.0.0 | | |
| Content track | v0.38.0 | started early: shops (v0.35.0), gates and districts (v0.36.0), merchants that bid (v0.37.0), city life (v0.38.0) - boards, envoys, rumours, inns, prosperity |
