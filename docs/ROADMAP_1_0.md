# Roadmap to v1.0.0

Revised at **v0.21.6** (2026-09-06). The first version of this file was
written at v0.20.4 from an inventory of the tree; this one re-takes that
inventory after the v0.21 point releases (typed play, Teardown, the
realm-capital gate, the materialised catalog, sect entry manuals, the input
fence, the content batch, presence roles) and adds what those releases made
possible. Every item below points at code as it is today. Milestones are
renumbered; the old numbers appear in brackets where a release note used
them.

## What 1.0 means

Three bars, unchanged. 1.0 is **not** "feature-complete"; it is the release
after which these three statements are true and guarded by tests:

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

A fourth statement is not a bar but is the reason the narrator exists, and
it is scheduled early because everything it needs now exists: **the AI does
one thing only a model can do** — voice a person with an agenda — and every
call it makes is one the game could not have resolved deterministically.

## Where we are

| Bar | State at v0.23.0 |
|---|---|
| Authority | **Closed.** `PLAYER_MUTATIONS` is empty (was 27 at v0.21.0, 21 at v0.22.0) and the gate now asserts empty rather than shrinking. Python writes no gameplay table. Remaining authority work is v0.24's: derived inputs Python still computes, and the 136 read-only `Database` methods that reach the engine as raw SQL. |
| Hardened | Input fence and per-user budget (typed play only) shipped; engine token, dashboard lock, bind default, health endpoints, supply chain still open. |
| Gameplay | Catalog materialised (154 manuals, all obtainable); every location has encounters and sense hints; every NPC has narrator fields; capitals gated by presence. Open: the seven orphaned autocompletes, six id parameters without pickers, three locations with no NPC, the playtest. |
| The AI | Typed play means a call fires only for dialogue, an epic beat, or an explicit "Narrate it". Routine explore/hunt still spend a call each. Commissions shipped (v0.22.0) and spend nothing extra: the ladder runs before any call and the offer rides the dialogue call the player was already paying for. |

Test surface: 842 Python tests, Go suite green including `-race`. Every v0.23.0
action carries Go tests verified against the defect they guard - the fix was
temporarily reverted and the test confirmed to fail - rather than merely
against the fix.

Not yet proven by play: v0.22.0 through v0.23.0 added a dozen engine actions,
three schema migrations, a request middleware and a changed shutdown path, and
none of it has met a live Discord server. A test-server pass is the next thing
this code needs, ahead of any further milestone.

## The release channel

Unchanged from v0.20.4: GitHub Releases on `RhaZenZ0/Xianxia-bot`, built by
`.github/workflows/release.yml` from a tag; `UPDATE_CHANNEL=stable` (default)
or `beta`; `./update.sh --check/--fetch/--upgrade` on the NAS with the
SHA-256 sidecar verified. Each milestone ships as **one or more betas first**,
then stable. Point releases are fixes only — the v0.21.x series bent that
rule six times in a day and should not be the pattern for the rest.

**Before any of the milestones below: a test-server pass on v0.21.6.**
Everything since v0.21.1 that touches Discord itself — `MessageInteraction`
replies, the picker, Teardown, hub overwrites, presence roles — is
compile- and contract-tested but has never run against discord.py. Upgrade,
Repair, Sync Realm Roles, travel between two capitals, `> I explore`, sit a
sect trial, Teardown a throwaway guild. Findings are v0.21.7.

## Milestones

Each milestone names its **gate**: the test or check that must exist and
pass before the stable tag.

### v0.21 — Authority I: the last player-side mutations — **shipped v0.23.0**

*Closed. `PLAYER_MUTATIONS` in `tests/python/contracts/test_authority_boundary.py`
is empty and the gate now asserts it stays that way: a new Python-side gameplay
write fails the scan rather than joining a backlog.*

The 21 rows, and what replaced them:

| Rows | Where | Engine action |
|---|---|---|
| 4 | `/sect shadow` (`sect.py:sect_shadow`): hidden-sect status, initiation, manual grant, provenance | `sect.shadow`, with its own manual selector — the shadow cell picks by alignment and the character's path, not by sect |
| 2 | `/alchemy purge` (`exploration.py:alchemy_purge`): resources, cooldown | `alchemy.purge` |
| 2 | toxicity sync (`character_state.py:sync_pill_toxicity_effect`) | deleted; the engine settles the curve wherever it reads the effect table, so decay applies without the player opening a screen |
| 2 | `discover_sect` from `/explore` and `_sync_sect_discoveries` | `sect.discover`, batched, reporting which sects were actually new |
| 8 | admin writes in `world_ops.py` (set/clear sect, set/clear master, rank, attention, storage, spawn realm) | `admin.player.*` / `admin.world.spawn_realm`, each auditing inside its own transaction |
| 1 | `/law technique` self-applied effect | `law.technique` — the out-of-battle half, with the stage/realm/battle gates moved with it |
| 1 | `set_gender` | `character.set_gender` |
| 1 | abode `set_location` | `sect.abode.enter` / `sect.abode.leave` |
| ~~1~~ | ~~`accept_quest` (`core_services.py:accept`)~~ | **closed in v0.22.0** by `commission.accept` |

**Gate:** `PLAYER_MUTATIONS == {}`, enforced by
`test_the_v0_21_backlog_stays_closed`. Go tests per action, each verified
against the defect it guards.
**Stable tag:** `v0.23.0`.

### v0.22 — Commissions — **shipped v0.22.0** *(schema 29)*

*Shipped v0.22.0 — gates: `go_core/internal/game/commission_actions_test.go`,
`tests/python/unit/test_commissions.py`,
`tests/python/contracts/test_commission_surface.py`. Steps 1–4 landed; step 5
(seeded invention) is deliberately not built. Full detail in
`docs/V022_RELEASE_NOTES.md`.*

It shipped ahead of the narrator budget, which was supposed to precede it, and
that turned out to cost nothing: the ladder decides every branch before any
model call, and an offer rides the dialogue call the player was already
spending. The budget milestone is now v0.23 and is unblocked either way.

The design is `docs/COMMISSIONS_DESIGN.md`; this is the build order from it.

1. **Schema 29**: the `quest_definitions`, `character_quests` and
   `npc_relationships` columns the design lists; `status` gains `failed` and
   `abandoned`.
2. **Engine actions** `commission.accept` (replaces `accept_quest` — the
   last v0.21 row), `commission.resolve` (completed / failed / abandoned;
   standing deltas via the `relationship.update` path; rewards only on
   completion through `cultivation.reward`), `commission.expire` on the
   simulation tick.
3. **The pool**: the batch forge gains a giver, a realm band, a tier and
   reward variants; `/admin world quests` shows them; approved rows carry
   `owner_user_id IS NULL`.
4. **The voice**: `talk_to_npc` gains the `commission_context` block; the
   dialogue view gains Accept / Decline / variant buttons whose payload comes
   from the block, never from narration text. Steward Qiao is the first
   giver.
5. **Seeded invention**, last, once the pool and the voice call are seen to
   read well: the seed builder over existing RAG/memory reads, the invention
   budget, auto-approval under the smaller budget, GM review after the fact.
   **Not built in v0.22.0.** The columns, the visibility rule and the GM
   review surface are in place; nothing writes a personal commission yet.
   Bring it back after a test-server pass shows the pool reads well.

Decisions the operator owed before step 1 are recorded, with the values
v0.22.0 shipped, at the end of `docs/COMMISSIONS_DESIGN.md`. The three
invention rows (calls per day, interval per player, invention budget) are
still open, because invention is still unbuilt.

**Gate:** the tests listed in the design doc — `PLAYER_MUTATIONS` loses
`accept_quest`; Go tests for the three actions with failed ≡ abandoned in
consequence; the seed builder contains no player text and no non-public
memory (mutation-tested); `/quests` never lists another player's
`owner_user_id`; the Accept button's payload comes from the context block.

### v0.23 — The narrator budget *(was v0.22; commissions overtook it)*

- **Route by tier.** `narrate_exploration`, `narrate_hunt_result` and
  `narrate_sect_recommendation` spend a routine call each on results the
  engine already decided. Make them procedural-first; the model narrates
  them only on an explicit upgrade (the typed-play picker's *Narrate it*, a
  GM scene flag). Live calls are then dialogue, epic beats, and explicit
  asks — nothing else.
- **Budget on every door.** `TYPED_PLAY_BUDGET` covers `on_message`; extend
  the same bucket to `serialized_user_action` (slash and hub paths) and evict
  idle locks from `_USER_ACTION_LOCKS`. Closes the v0.25 "per-user
  command budget" item entirely.
- **A fallback pool.** The procedural fallback prose is what players read
  when the budget is spent, and it is hand-written per scene kind. Author a
  pool of variants per scene kind × location tier (content, reviewed once),
  chosen deterministically, so the floor reads well.
- **The AI status page tells the truth.** `/admin → Server → Ai Status`
  gains: calls by purpose (dialogue / epic / narrate-it / forge), refused by
  the per-user bucket, and procedural-served share — so the effect of this
  milestone is visible.

**Gate:** a contract test that no `_generate` call site outside
`talk_to_npc`, the epic tier and the explicit-upgrade path runs by default;
`test_user_budget.py` extended to the slash path; a content test for the
fallback pool.

### v0.24 — Authority II: derived inputs, pricing, the DB layer *(was v0.22)*

Where Python does not mutate but *computes the input* the engine then
trusts, or keeps a second copy of an engine rule.

- `seclusion.start` receives `environment_mult` computed in Python from
  abode level, manor multiplier and a deployed array
  (`app/bot/commands/cultivation.py`); Go only clamps it. Same shape as
  `forage.resolve`'s `context_bonus`. The engine recomputes both from state
  it already holds.
- Market pricing lives twice: `app/simulation/world.py` derives
  `sell = buy × 0.70`, and `_market_tradeable` duplicates `MarketExcluded`
  (`go_core/internal/simulation/bootstrap.go:marketTradeable`). One copy, in
  Go; Python asks for a quote.
- `app/database/core.py` still writes gameplay tables directly. Each goes
  one of three ways: deleted with its caller (after v0.21), moved behind an
  engine action, or kept and marked presentation-only (thread ids, channel
  bindings, RAG rows, `clear_discord_bindings`).
- `app/simulation/` becomes read-only orchestration: its raw-SQL reads move
  to engine query sessions.
- Rule tables reached from the DB layer (`equipment_power`,
  `formation_bonus`, nine imported constants) leave with the mutators that
  use them.
- Dead rule code with zero production callers is deleted
  (`app/rules/game.py` `random_encounter`, `random_explore_rewards`,
  `random_hunt`, `roll_unexpected_event`, `d10`, `check_result`; the
  `secrets.randbelow` path in `birthfamily.py`; `craft_quality`,
  `tribulation_tns`, `condition_effect`, `boss_phase`, `stable_percent`,
  `manor_qi_multiplier`, `manor_defense_power_bonus`).
- `docs/V018_AUTHORITY_CLEANUP_ROADMAP.md` and the V015/V016 mutator lists
  are archived under `docs/migration_history/`.

**Gate:** `test_authority_boundary.py` asserts (a) no INSERT/UPDATE/DELETE
against a gameplay table in `core.py` outside a named presentation
allowlist, (b) `app/rules/` is imported at runtime only by `app/bot/`
formatting paths and `app/ai/` (`test_app_layout.py`), (c) every
`app/rules` function has a caller.

### v0.25 — Hardened I: the doors *(was v0.23)*

- **Engine token required.** `go_core/internal/server/server.go` returns
  `true` when the token is empty; fail closed, and make
  `app/ops/config.py` validate `ENGINE_AUTH_TOKEN` the way
  `DASHBOARD_TOKEN` already is. A blank token is a startup error on both
  sides.
- **One bind variable, safe default.** `DASHBOARD_HOST` defaults to
  `0.0.0.0` while `.env.example` sets `DASHBOARD_BIND_ADDRESS=127.0.0.1`,
  which the app does not read. One variable, default loopback, a documented
  reverse-proxy recipe for LAN access with TLS.
- **Dashboard lock.** Failed-attempt throttle and temporary lockout per
  source address on Basic Auth; Origin check on mutations alongside the
  `x-xianxia-admin` header.
- **Health endpoints.** `/healthz`, `/readyz`, `/metrics` are
  unauthenticated on `0.0.0.0:8080`: bind to the compose network, or gate
  `/metrics` with the control token.
- ~~Per-user command budget~~ — v0.21.1 (typed play); v0.23 extends it to every door.
- ~~Input fence for the narrator~~ — v0.21.5.
- **Supply chain.** Pin `aiosqlite`, `python-dotenv`, `httpx` exactly, add
  a hash-locked requirements file, pin `python:3.12-slim` by digest in both
  Dockerfiles.
- ~~**Split the engine credential.**~~ **Declined, 2026-09-07.** An external
  review (finding #8) noted that one `ENGINE_AUTH_TOKEN` grants gameplay
  actions, arbitrary SQL, migrations, backup and restore, so compromising any
  token-bearing service is full database compromise rather than "can call game
  actions". The operator's decision is not to split it. Recorded here so it is
  not re-raised as an oversight, with the reasoning both ways:
  - *For living with it:* one guild, one operator, all services on one NAS
    behind the same trust boundary. An attacker who reaches the token has
    almost certainly reached the SQLite file it protects, and a split would
    add three secrets to manage for little real isolation.
  - *Against:* the bot process is the one that handles untrusted Discord input
    and untrusted model output, and it is the one that would most benefit from
    holding a credential that cannot run arbitrary SQL.
  - *The cheap middle, if it is ever wanted:* leave the token alone and refuse
    **writable** generic SQL for the bot once `app.database.bootstrap` has
    finished, keeping `/v1/db/*` writes for the migration entrypoint. That is
    a one-flag change rather than a deployment-wide one and captures most of
    the blast-radius reduction.

**Gate:** `test_deployment_hardening.py` and a new
`test_security_defaults.py`: blank engine token fails startup (Python) and
`NewServer` (Go); dashboard default bind is loopback; N failed logins lock;
requirements are hash-locked.

### v0.26 — Hardened II: moderation and data *(was v0.24)*

- **Moderation from Discord.** Mute/freeze exist only as the dashboard's
  `player.set_moderation`; add `/admin player mute|freeze|unmute|unfreeze`
  with duration and reason, and `ban`. Expiry runs in the engine's
  simulation tick. Bring over the dashboard admin ops a GM needs
  mid-session (`force_end_scene`, `clear_battle`, `teleport`).
- **Backups.** Retention (keep N daily / M weekly), size cap, optional
  encryption with an operator key, and an off-box copy step in
  `update.sh --backup`.
- **Audit.** Every moderation action lands in `admin_audit_log` with actor,
  target, reason and expiry; `admin.audit.undo_last` reverses them. (Teardown
  already audits, v0.21.2.)

**Gate:** Go tests for moderation expiry and backup retention; a Python scan
that every "GM needs it live" dashboard op has a Discord registration; a
source check that moderation commands audit.

### v0.27 — Gameplay-complete I: the pickers *(was v0.25)*

- **Attach the seven orphaned autocompletes** in
  `app/bot/commands/economy.py` (`blackmarket_buy/sell`,
  `market_prices_item`, `market_buy/sell_item`, `civilization_location`,
  `civilization_npcs_location`): written, referenced nowhere, so those
  commands take exact ids as free text. One decorator each.
- **Providers or hints for the six remaining id parameters:**
  `artifact_bond(item)`, `artifact_awaken(item)`, `battle_challenge(target)`,
  `battle_act(action)`, `boss_start(boss)`, `secret_enter(realm)`.
- **Typed play, roots with parameters.** The verb table covers only roots
  without parameters and the eight scene actions; `/travel`, `/use` and the
  group commands fall to the picker. Grow the table from what the picker
  shows players choosing, and let a candidate carry one resolved argument
  (a destination, an item) when entity resolution found it.
- ~~Content: encounters, sense hints, NPC narrator fields~~ — v0.21.5.
  Still open: **three locations with no NPC.**
- `fate.adjust` is allowlisted and implemented in the engine with no Python
  caller: wire it or remove it.

**Gate:** a `test_hub_pickers.py` that walks every hub action's parameters
and requires each id-typed one to have a provider or a hint;
`test_world_content_gate.py` gains "every location has at least one NPC".

### v0.28 — Gameplay-complete II: the playtest *(was v0.26)*

A written pass over all sixteen player hubs on the test server, hub by hub,
page by page, with a checklist per action (reachable, picker present, error
text actionable, narration fallback fired, engine result keys read), plus
the three loops this year's releases added: type `> I explore`, join a sect
and study the gift, take a commission from Qiao and complete, fail and
abandon one. Findings go into `docs/KNOWN_LIMITATIONS.md` — the punch list
— and are fixed or explicitly deferred past 1.0 there.

**Gate:** `docs/KNOWN_LIMITATIONS.md` exists, every entry is fixed or marked
deferred with a reason; the checklist is checked in under `docs/playtest/`
with the release it was run against.

### v1.0.0-rc.N → v1.0.0

Release candidates go to the **beta channel** only.

- Migration drill: a database from every shipped schema (v1 through 29)
  migrates to current with no data loss (`test_startup_health` covers v1;
  extend to the set).
- Backup → restore drill on the NAS, documented.
- Clean install from the README on a machine that has never seen the
  project, timed, with the gaps fixed.
- Documentation consolidated: README quickstart; `VERSIONS.md` trimmed to
  one paragraph per minor; the per-line notes files kept as history;
  `docs/COMMISSIONS_DESIGN.md` marked shipped or archived.
- `gofmt` clean; `make check` green from a fresh clone.
- Two weeks on the NAS at rc without a P1.

`v1.0.0` is the rc that survived, re-tagged.

## Order and dependencies

```
test-server pass on v0.22.0
        │
v0.21 Authority I ─────┬──► v0.22 Commissions ✔ ──► v0.23 Narrator budget
   (accept_quest row   │      (seeded invention pending)
    closed in v0.22)   │
                       └──► v0.24 Authority II ──► v0.25 Hardened I ──► v0.26 Hardened II
                                                                              │
v0.27 Gameplay I ─────────────────────────────────────────────► v0.28 Playtest ◄──┘
                                                                     │
                                                              v1.0.0-rc.1 … ► v1.0.0
```

Authority I stays first because every later item that touches a handler is
cheaper once the handler is "call, then format" — and because Commissions
must not add a Python write while the allowlist is still shrinking; it did
not, and it closed a row. The narrator budget was meant to come first so the
new calls landed on an honest budget; commissions turned out to add no calls,
so the order swapped without cost. Gameplay I is independent and can
interleave with anything. The playtest waits for all of it, because it is the
check that nothing a player can reach regressed.

## Landed outside the milestones

- **v0.20.6 — Quest Forge.** AI-drafted quests from a GM prompt or world
  history, GM-approved, rewards through the engine.
- **v0.21.1 — Typed play.** `> action` lines routed deterministically to
  existing handlers; speech is free; per-player token bucket.
- **v0.21.2 — Teardown**, and the realm-capital gate actually gating.
- **v0.21.3 / v0.21.4 — Manuals.** The 148-manual catalog materialised into
  `world.json` (Go and Python read one file; a test holds it); one manual on
  joining a sect; six authored tier-0 entry manuals.
- **v0.21.5 — The input fence; the content batch; cooldowns as a wait.**
- **v0.21.6 — Presence roles.** Capitals visible only while you stand in
  them.
- **v0.22.0 — Commissions.** A milestone rather than an aside; listed here
  only because it landed a release earlier than the numbering planned.

## Keeping this file honest

Each milestone's gate is a test. When a milestone ships, its section gains
a one-line "shipped in vX.Y.Z — gate: `tests/.../test_x.py`" and the table
below is updated.

| Milestone | Release | Status |
|---|---|---|
| test-server pass on v0.22.0 | | |
| v0.21 Authority I | v0.21.0 opened the gate | 21 rows remain |
| v0.22 Commissions | v0.22.0 | shipped, less seeded invention |
| v0.23 Narrator budget | | |
| v0.24 Authority II | | |
| v0.25 Hardened I | | fence + typed-play budget shipped (v0.21.1, v0.21.5) |
| v0.26 Hardened II | | |
| v0.27 Gameplay I | | content items shipped (v0.21.5) |
| v0.28 Playtest | | |
| v1.0.0-rc | | |
| v1.0.0 | | |
