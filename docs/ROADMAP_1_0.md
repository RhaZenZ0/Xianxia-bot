# Roadmap to v1.0.0

Written at v0.20.4 (2026-09-06), after the `main.py` split (v0.19.33–v0.20.0),
the `app/` re-layout (v0.20.1) and the test-suite audit (v0.20.3). Every item
below points at code as it is today; the inventory behind it was taken by
reading `app/`, `go_core/` and `content/` — not from earlier roadmaps, which
had drifted (see "Why the old roadmap is not the backlog").

## What 1.0 means

Three bars, chosen deliberately. 1.0 is **not** "feature-complete"; it is the
release after which these three statements are true and guarded by tests:

1. **Engine authority is finished.** Python owns no game rule. Every gameplay
   mutation goes through `authoritative_action(...)` into the Go engine; every
   rule table Python still holds is presentation (labels, bands, descriptions)
   or is a copy of one the engine owns and is deleted.
2. **Hardened.** The engine door is never open, the dashboard door has a
   lock that survives a brute-force attempt, a player cannot flood the bot, a
   GM can act on abuse from inside Discord, and backups have a retention
   policy.
3. **Gameplay-complete.** Every hub reachable from `/character` has no known
   dead end: every id-taking parameter has a picker or an explanation, every
   explorable location can be explored, every NPC the narrator is handed has
   the fields it reads, and a written playtest of all sixteen hubs finds
   nothing on the punch list.

Operational polish (clean-install guide, doc consolidation) rides along in the
release-candidate milestone rather than gating 1.0 on its own.

## The release channel

From v0.20.4 releases are GitHub Releases on `RhaZenZ0/Xianxia-bot`, built by
`.github/workflows/release.yml` from a tag:

| Tag | Channel | VERSION inside | Who sees it |
|---|---|---|---|
| `v0.21.0` | stable | `0.21.0` | everyone (`UPDATE_CHANNEL=stable`, the default) |
| `v0.21.0-beta.1` | beta | `0.21.0` | `UPDATE_CHANNEL=beta` only |

The bot announces a newer release in the bot log channel once per version;
`./update.sh --check`, `--fetch` and `--upgrade` do the rest on the NAS,
verifying the SHA-256 sidecar before an archive is accepted. Each milestone
below ships as **one or more betas first**, then stable. Point releases
(`0.21.1`) are fixes only.

## Milestones

Each milestone names its **gate**: the test or check that must exist and pass
before the stable tag. Items inside a milestone can ship as separate point
releases; the gate is per milestone.

### v0.21 — Authority I: the last player-side mutations

Python still decides gameplay outcomes in a handful of handlers, then writes
the tables itself. Each gets an engine action, with the Python handler reduced
to "call, then format". The battle-side branch of `/item use`
(`COMBAT.recovery_item`) is the template — the non-battle branch beside it
never got the same treatment.

| Item | Where it is now | Engine action |
|---|---|---|
| ~~`/item use` outside battle: consume → restore → life extension → effect → toxicity, five unguarded writes~~ **done in v0.21.0** | `go_core/internal/game/item_use_actions.go` | `item.use` |
| `/alchemy purge`: Qi cost, purge amount from `will`/`spirit`, cooldown | `app/bot/commands/exploration.py:721-734` | `alchemy.purge` |
| `/sect shadow`: karma gates, initiation, hostility flip, demonic-manual pick | `app/bot/commands/sect.py:595-648` | `sect.shadow` |
| `/law technique` self-applied effect with a hard-coded 120-minute duration | `app/bot/commands/law.py:114` | fold into the existing technique action |
| toxicity → penalty curve (`medicine_toxicity_effect`) writing `active_effects` | `app/bot/character_state.py:69-84`, `app/rules/effects.py:94-131` | engine owns the curve; Python reads the band label |
| eight admin writes that bypass Go admin ops that already exist (`set_sect_membership`, `clear_sect_membership`, `set_master`, `clear_master`, `set_sect_rank`, `adjust_master_attention`, `set_storage_container`, `activate_world_event`) | `app/bot/admin/world_ops.py` | `admin.player.set_sect` and siblings |
| the small ones: `set_gender`, abode `set_location`, `discover_sect`, `accept_quest` | `character.py:95`, `sect.py:574,585`, `exploration.py:359`, `core_services.py:212` | one action each, or the adjacent existing one |

**Gate:** `tests/python/contracts/test_authority_boundary.py` gains an
allowlist of `DB.<mutator>(` call sites permitted from `app/bot/` and
`app/ops/`; this milestone empties it for player commands. *(Landed in
v0.21.0 as `PLAYER_MUTATIONS`: 27 rows at the start, 22 after `item.use`.)* Go tests per new
action, mutation-tested as every admin op was.

### v0.22 — Authority II: derived inputs, pricing, the DB layer

Where Python does not mutate but *computes the input* the engine then trusts,
or keeps a second copy of an engine rule.

- `seclusion.start` receives `environment_mult` computed in Python from
  abode level, manor multiplier and a deployed array
  (`app/bot/commands/cultivation.py:120-141`); Go only clamps it
  (`family_dao_actions.go:495-498`). Same shape as `forage.resolve`'s
  `context_bonus`, which V017 flagged and which has since replicated. The
  engine recomputes both from state it already holds.
- Market pricing lives twice: `app/simulation/world.py:273-279` derives
  `sell = buy × 0.70`, and `_market_tradeable` (`world.py:25-38`) duplicates
  `MarketExcluded` (`go_core/internal/simulation/world.go:66`). One copy, in
  Go; Python asks for a quote.
- `app/database/core.py` writes ten gameplay tables directly (the 21
  Priority-1 mutators the V015/V016 documents listed are 21/21 still present
  with live callers). Each goes one of three ways: deleted with its caller
  (after v0.21), moved behind an engine action, or kept and marked
  presentation-only (thread ids, channel bindings, RAG rows).
- `app/simulation/` becomes read-only orchestration: its twelve raw-SQL
  reads move to engine query sessions; the package keeps `run_due`/`force_run`
  delegation only.
- Rule tables reached from the DB layer (`core.py:6018` `equipment_power`,
  `:6046` `formation_bonus`, and nine imported constants) leave with the
  mutators that use them.
- Dead rule code with zero production callers is deleted:
  `app/rules/game.py:447-561` (`random_encounter`, `random_explore_rewards`,
  `random_hunt`, `roll_unexpected_event`, `d10`, `check_result`), the
  `secrets.randbelow` generation path in `birthfamily.py:238-594`,
  `craft_quality`/`tribulation_tns`/`condition_effect`,
  `boss_phase`/`stable_percent`, `manor_qi_multiplier`/`manor_defense_power_bonus`.
  Their tests go with them or move to the Go side.
- `docs/V018_AUTHORITY_CLEANUP_ROADMAP.md` and the V015/V016 mutator lists
  are archived under `docs/migration_history/` with a note that this file
  supersedes them.

**Gate:** `test_authority_boundary.py` asserts (a) no `INSERT`/`UPDATE`/
`DELETE` against a gameplay table in `app/database/core.py` outside a named
presentation allowlist, (b) `app/rules/` is imported at runtime only by
`app/bot/` formatting paths and `app/ai/` — a static import-graph check in
`test_app_layout.py` — and (c) every `app/rules` function has a caller
(the dead-code scan becomes a test).

### v0.23 — Hardened I: the doors

- **Engine token required.** `go_core/internal/server/server.go:121` returns
  `true` when the token is empty; `cmd/xianxia-core/main.go` enforces ≥20
  chars but the server type does not. Fail closed in the server, and make
  `app/ops/config.py:338` validate `ENGINE_AUTH_TOKEN` the way
  `DASHBOARD_TOKEN` already is (length, placeholder rejection). A blank token
  becomes a startup error on both sides.
- **One bind variable, safe default.** `DASHBOARD_HOST` defaults to
  `0.0.0.0` (`dashboard/server.py:108`) while `.env.example` sets
  `DASHBOARD_BIND_ADDRESS=127.0.0.1`, which the app does not read. One
  variable, default loopback, documented reverse-proxy recipe for LAN access
  with TLS (the two listeners will not grow their own TLS).
- **Dashboard lock.** Failed-attempt throttle and temporary lockout per
  source address on Basic Auth (`server.py:1342-1351`); Origin check on
  mutations alongside the `x-xianxia-admin` header (`server.py:1425`).
- **Health endpoints.** `/healthz`, `/readyz`, `/metrics` on `0.0.0.0:8080`
  are unauthenticated (`app/ops/health.py:254-259`): bind them to the compose
  network only, or gate `/metrics` with the control token.
- **Per-user command budget.** `serialized_user_action`
  (`app/bot/runtime.py:193-217`) serialises but never throttles, and
  `_USER_ACTION_LOCKS` grows without bound. A token bucket per user (burst +
  sustained rate, configurable) and lock eviction.
- **Input fence for the narrator.** Player free text reaches the model
  labelled "(untrusted ...)" but not delimited (`app/ai/narrator.py:462,570,
  576,705,711`); the output-side leak guard (`ai_router.py:44-52`) is the only
  defence. Delimit and length-cap player text the way `chat_monitor.py:27,92`
  already does.
- **Supply chain.** Pin `aiosqlite`, `python-dotenv`, `httpx` exactly, add a
  hash-locked requirements file, pin the `python:3.12-slim` base image by
  digest in both Dockerfiles.

**Gate:** `test_deployment_hardening.py` and a new
`test_security_defaults.py`: blank engine token fails startup (Python) and
`NewServer` (Go); dashboard default bind is loopback; N failed logins lock;
the rate limiter is exercised behaviourally; requirements are hash-locked.

### v0.24 — Hardened II: moderation and data

- **Moderation from Discord.** Mute/freeze exist only as the dashboard's
  `player.set_moderation` (`server.py:1137`); there is no `/admin` command.
  Add `/admin player mute|freeze|unmute|unfreeze` with an optional duration
  and reason, and `ban` (freeze + hide from public surfaces). Expiry runs in
  the engine's simulation tick so it survives restarts. 24 of the 34 dashboard
  admin ops have no Discord equivalent; bring over the ones a GM needs
  mid-session (the moderation set, `force_end_scene`, `clear_battle`,
  `teleport`).
- **Backups.** `server.go:482-535` and `core.py:6185-6210` write plaintext
  SQLite files with no pruning. Retention (keep N daily / M weekly), size cap,
  optional encryption with an operator key, and a documented off-box copy
  step in `update.sh --backup`.
- **Audit.** Every moderation action lands in `admin_audit_log` with actor,
  target, reason and expiry; `admin.audit.undo_last` learns to reverse them.

**Gate:** Go tests for moderation expiry and backup retention; a Python
scan that every dashboard admin op in the "GM needs it live" set has a
Discord registration; `test_scene_action_surface`-style source check that
moderation commands audit.

### v0.25 — Gameplay-complete I: the pickers and the content

- **Attach the seven orphaned autocompletes** in
  `app/bot/commands/economy.py` (`blackmarket_buy_autocomplete:510`,
  `blackmarket_sell_autocomplete:530`, `market_prices_item_autocomplete:560`,
  `market_buy_item_autocomplete:580`, `market_sell_item_autocomplete:600`,
  `civilization_location_autocomplete:376`,
  `civilization_npcs_location_autocomplete:403`). They are written and
  referenced nowhere, so `/market buy|sell|prices`, `/blackmarket buy|sell`
  and `/civilization` take exact ids as free text in both the slash and hub
  paths. One decorator each.
- **Providers or hints for the six remaining id parameters:**
  `artifact_bond(item)`, `artifact_awaken(item)`, `battle_challenge(target)`,
  `battle_act(action)`, `boss_start(boss)`, `secret_enter(realm)`.
  `register_hub_option_hint` has four call sites in the whole game, all in
  `equipment.py`; every other unguided parameter falls through to a bare
  free-text modal.
- **Content.** Six locations `/explore` hard-errors on (`content/world.json`
  entries with empty `encounters`: Black Serpent Ravine, Blood River Gorge,
  Corpse Lantern Necropolis, Azure Cloud Mountain Gate, Crimson Furnace
  Valley, Frozen Moon Terrace); seven locations without `sense_hints`; five
  NPCs missing `personality` and `secret` (the narrator-context fields:
  Emperor Zhao Tianming, Jade Sovereign Lian Xue, Immortal Emperor Shen Wuji,
  Celestial Empress Ji Yue, Steward Qiao); three locations with no NPC.
- `fate.adjust` is allowlisted and implemented in the engine with no Python
  caller: wire it or remove it.

**Gate:** new content tests — every non-safe location has encounters, every
location has `sense_hints`, every NPC has the narrator fields — and a
`test_hub_pickers.py` that walks every hub action's parameters and requires
each id-typed one to have a provider or a hint (the scan
`test_gui_integrity.py` does not do today).

### v0.26 — Gameplay-complete II: the playtest

A written pass over all sixteen player hubs on the test server, hub by hub,
page by page, with a checklist per action (reachable, picker present, error
text actionable, narration fallback fired, engine result keys read). Findings
go into `docs/KNOWN_LIMITATIONS.md` — the punch list this project has never
had — and are fixed or explicitly deferred past 1.0 there. Narration
fallbacks per scene kind are already complete (every `_generate` call site
passes one); the playtest confirms they read well.

**Gate:** `docs/KNOWN_LIMITATIONS.md` exists, every entry is either fixed
(with the release) or marked deferred with a reason; the checklist is checked
in under `docs/playtest/` with the release it was run against.

### v1.0.0-rc.N → v1.0.0

Release candidates go to the **beta channel** only.

- Migration drill: a database from every shipped schema (v1 through 27)
  migrates to current with no data loss (`test_startup_health` covers v1;
  extend to the set).
- Backup → restore drill on the NAS, documented.
- Clean install from the README on a machine that has never seen the
  project, timed, with the gaps fixed.
- Documentation consolidated: README quickstart; `VERSIONS.md` trimmed to
  one paragraph per minor; the per-line notes files kept as history.
- Two weeks on the NAS at rc without a P1.

`v1.0.0` is the rc that survived, re-tagged.

## Landed outside the milestones

- **v0.20.6 — Quest Forge.** AI-drafted quests from a GM prompt or world
  history, GM-approved, rewards granted through the engine's
  `cultivation.reward` (in the spirit of Authority I: no Python-side
  writes). It also closed two Gameplay dead ends found on the way: the
  shipped sect quest could never complete (`sect_trial` was never reported)
  and quest rewards were never granted.

## Order and dependencies

```
v0.21 Authority I ──► v0.22 Authority II ──► v0.23 Hardened I ──► v0.24 Hardened II
                                                                        │
v0.25 Gameplay I ──────────────────────────► v0.26 Playtest ◄───────────┘
                                                   │
                                            v1.0.0-rc.1 ... ► v1.0.0
```

Authority first: every later item that touches a handler is cheaper once
handlers are "call, then format". Gameplay I is independent of the authority
work and can interleave. The playtest waits for both, because it is the
check that they did not regress anything a player can reach.

## Why the old roadmap is not the backlog

`docs/V018_AUTHORITY_CLEANUP_ROADMAP.md` reports every stage "BUILT +
AUDITED" and lists no remaining work; the V015/V016 "remaining mutators"
lists name 21 Priority-1 mutators, all of which still exist with callers.
The two documents disagree with the tree in opposite directions. The
inventory in v0.21/v0.22 above was taken from the code and is the backlog;
those files are history.

## Keeping this file honest

Each milestone's gate is a test. When a milestone ships, its section here
gains a one-line "shipped in vX.Y.Z — gate: `tests/.../test_x.py`" and the
progress table below is updated, the way `docs/MAIN_SPLIT_PLAN.md` was.

| Milestone | Release | Status |
|---|---|---|
| v0.21 Authority I | | |
| v0.22 Authority II | | |
| v0.23 Hardened I | | |
| v0.24 Hardened II | | |
| v0.25 Gameplay I | | |
| v0.26 Playtest | | |
| v1.0.0-rc | | |
| v1.0.0 | | |
