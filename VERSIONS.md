# Xianxia RP Discord Bot — Version History

This is the release-by-release changelog for the Xianxia RP Discord Bot, split out of `README.md`
so the README can stay focused on architecture, setup, and current operational documentation. See
`README.md` for that; see `docs/V020_RELEASE_NOTES.md`, `docs/V019_RELEASE_NOTES.md`, `docs/V018_RELEASE_NOTES.md` and
`docs/V018_BUILD_HISTORY.md` for full per-release detail beyond the summaries below.

## Changelog

Version **0.18** completed the staged authority cleanup: forage/crafting/companions, canonical time,
unified lifespan, multi-hop road travel, caravan mechanics, dashboard-owned Discord setup, and removal
of obsolete Python mechanical authority paths. **0.19** is a cultivation-depth consistency/coverage pass
on top of that release, plus a further authority-migration pass for 1v1 battle start and mid-battle item
recovery, plus GM-authored per-channel welcome messages and a #bugs forum channel for
player bug reports. The release uses schema **26**.

**0.19.5** is a GUI release on top of that. The interactive hub panel gains a Components V2
"action list" layout: every action on a system is visible as its own row with its own button,
instead of being hidden behind an action dropdown, and running an action no longer overwrites
the panel it was launched from. It is piloted on `/character`; the other 15 player hubs and the
admin panel keep the classic embed panel, and `LAYOUT_HUB_NAMES` in `app/bot/hubs.py` is the
whole rollout switch. 0.19.5 changes no schema and no game rules. It also corrects the release
stamp itself, which had drifted: `VERSION` said 0.19 while `app/version.py`, the `Dockerfile`
and `docker-compose.yml` all still said 0.18.

**0.19.6** is a correctness release answering an external audit. It fixes a silent data-loss
path in the Go engine (`/v1/db/batch` with `transaction:false` returned HTTP 200 and then rolled
the writes back), Discord snowflake ids being rounded to nothing by `JSON.parse` on the way to
the dashboard, a cleared channel message coming back on the next Repair, backups taken in the
same second overwriting each other, `#bugs` forum tags never being applied to an adopted
channel, and a `RELEASE_MANIFEST.sha256` that had drifted out of date and was never verified by
`update.sh`. Every fix carries a regression test that fails without it. No schema change.

**0.19.7** corrects the hub layout against how it actually renders in Discord: no generic
glyph on action rows, one status per line with shorter bars, danger buttons that name
their own verb, and the system dropdown removed as a duplicate of Prev/Next.

**0.19.8** fixes onboarding copy that told players to type commands that do not exist
(`/family leave` is not registered — only the hub `/family` is), rewrites the household and
expedition thread openers to lead with how to step outside, and adds a test that gates the
whole shape.

**0.19.9** fixes the release integrity check, which used GNU-only `sha256sum` flags and so
refused every package on BusyBox-based NAS hardware. See RELEASE.txt — upgrading from
0.19.6–0.19.8 needs a one-line patch to the *installed* `update.sh` first.

**0.19.10** rolls the Components V2 layout out to every hub — all 16 player hubs and
`/admin` — in four staged groups, and ports the administrator re-check into the layout view
so `/admin` keeps the guard the classic panel always had.

**0.19.11** orders hub actions by relevance rather than alphabetically (so `/family`'s
Leave is visible without paging), and answers an external review: the Python containers now
run as uid 10001 instead of root, and first-run setup names `DASHBOARD_TOKEN`.

**0.19.12** starts the `main.py` decomposition: `app/bot/runtime.py` now holds the shared
singletons and session helpers, and `main.py` drops from 13,177 to 12,923 lines. Stage 1 of
several; no behaviour change.

**0.19.13** fixes the ordering bug that stopped 0.19.12 starting (`runtime.py` used `ROOT`
before assigning it) and adds the module-level use-before-assignment check that would have
caught it.

**0.19.14** fixes the second stage-1 failure: `serialized_user_action` moved to
`runtime.py`, and because `functools.wraps` cannot copy `__globals__`, discord.py resolved
119 callbacks' string annotations against `runtime.py` — where an "unused import" pass had
removed `app_commands`.

**0.19.15** adds the administrator AI monitor: `ai_status` (narrator health from
counters only) and `chat_digest` (map-reduce channel summaries on the same free
route chain as narration).

**0.19.16** bumps `openai` from 2.52.1 to 3.7.0. openai 3.x uses HTTPX2, which
verifies TLS against the OS trust store rather than certifi, so the Dockerfile now
installs `ca-certificates`, asserts the bundle is present and sets `SSL_CERT_FILE`.

**0.19.17** fixes the "0 stones" bug and four others found alongside it.

**0.19.18** reworks Scene Action so it needs no dropdowns and its state travels
with the player between scenes.

**0.19.19** closes an unauthenticated slow-header denial-of-service affecting both
HTTP listeners (health and dashboard) — the ancestor of the bounded-request-head
work described under "HTTP request limits" in the README.

**0.19.20** acts on what `ai_status` found in production: narration was landing on
procedural fallback 90.9% of the time, and this release fixes the causes.

**0.19.21** gives Bind a live picker to match the Equip/Unequip/Repair pickers
added in 0.19.17, and fixes a wrong-path bug a new test surfaced along the way.

**0.19.22** makes the expedition journal open automatically when a player steps
back into the world.

**0.19.23** is stage 2 of the `main.py` decomposition: `/family` moves out of
`main.py` into its own module.

**0.19.24** gives every OpenRouter route its own per-model rate window (RPM + RPD)
alongside the existing account-wide ceiling, so a route at its own cap is skipped
locally instead of spending an upstream attempt, a daily slot, and a cooldown to
discover it.

**0.19.25** is a production-review pass: ack-before-mutate for slow Discord
interactions, persistent event-scene timeouts, `/explore`'s road-discovery/frontier
logic, and live Discord-timestamp travel countdowns.

**0.19.26** adds nine new GM controls to the web Admin Console: character-sheet
editing, NPC/world-state editing, player moderation groundwork, and bulk/
server-wide actions.

**0.19.27** adds six more Admin Console controls, for sect membership/rank and
character progression detail (realm/body-realm perfection, spiritual root,
bloodline, physique, and tribulation state).

**0.19.28** adds a database restore command — the Go engine's first online-restore
capability, always taking its own safety backup first — and a debuff/condition-clear
admin control.

**0.19.29** fixes dashboard write attribution and a public-channel privacy leak in
the `#xianxia-info` guide, wires up world-time scale from the dashboard, adds a
dynasty/samsara unstick control, four crafting-adjacent admin controls, a
mute/freeze moderation system (schema 27), and an "undo the most recent admin
action" control.

**0.19.30** is split stage 3: `/sect` (25 leaf commands across the base group
plus manor/discipleship/recruitment subgroups) moves out of `main.py` into
`app/bot/commands/sect.py`, the same way `/family` moved in stage 2 (0.19.23).
`main.py` 12,980 → 12,174 lines; the new module is 895. This release also
splits the top-level `README.md` itself into a leaner `README.md` (architecture,
setup, operations) and a new `VERSIONS.md` (this file) holding the full
changelog, release status and schema history.

**0.19.31** fixes a real ephemeral-visibility bug and adds several deliberate
privacy improvements to the hub UI, merged in from a reviewed community patch
rather than applied as-is. `reply_long()` (`app/bot/runtime.py`) previously
hardcoded every chunk of a long reply to `ephemeral=False` regardless of what
the caller asked for - `/world`'s long output and anything else routed through
it could never actually be sent privately. It now forwards the caller's own
`ephemeral` choice. Hub UI feedback that is inherently single-user - bad modal
input, a guided multi-step picker continuing a player's own in-progress
action, and (this was the sharper find) the "this panel belongs to another
player" / "requires Administrator" rejection notices on `interaction_check` -
is now sent privately instead of `ephemeral=False`, which used to broadcast
those notices to the whole channel on every mis-click. Two parts of the
originally-proposed patch were deliberately rejected rather than merged: it
would have rewritten a failed hub action's error handling to reply privately
instead of restoring the public hub panel (players other than the one whose
action failed would be left looking at a stale, unresponsive card), and it
collapsed `_HubResponseProxy.edit_message`'s Components V2 panel-preservation
branch, which would have broken every layout-hub panel's ability to route its
own output beside the panel instead of over it. Both are guarded by new
regression tests (`HubFailurePublicSurfaceTests` in
`tests/python/unit/test_hub_layout_rollout.py`) so a future patch touching
this code gets caught the same way. See `docs/V019_RELEASE_NOTES.md` for the
full before/after and the reasoning behind each accepted and rejected change.

**0.19.32** adds the Bugslayer Sword - a one-of-a-kind, indestructible GM reward
blade with a "Heavenly Flawfinder" passive (+2 damage and a disrupted counter on a
strong normal attack, in both 1v1 and boss-raid combat) - and hardens the
`/admin player grant` command that hands it out (unique items grant once, checked
against inventory and bound equipment; Discord-side audit row; passive named in the
confirmation). Both ideas came from a community patch that turned out to be written
against a codebase that is not this one (every "existing" thing it extended had never
existed here), so they were built from scratch instead of merged; doing so surfaced
that equipment stats are duplicated in three places (Python plus two separate Go maps,
now guarded by a cross-language parity test), and consolidated all combat durability
wear into one `damageEquipmentGo` choke point. The release also fixes a startup crash
carried in with split stage 4 (`main.py` read `boss_status`/`hunter_status`/
`formation_status` at import time without importing them - `NameError` before the bot
connected) and repairs the source-scanning tests that split had broken. No schema
change.

**0.19.33** is phase 1 of the `main.py` split (`docs/MAIN_SPLIT_PLAN.md`): test scaffolding
only, no change under `app/`. Nineteen test files read `app/bot/main.py` by path and would
silently stop guarding code as it moved out (as `EquipmentOptionTests` did in stage 4); they
now read the whole `app/bot` package or locate a definition by name through new helpers in
`tests/support.py`. The six stage-4 command modules join the `MODULES` list in
`test_bot_module_split.py` (they had no definition-order, name-resolution or annotation guard
until now), and a package-wide "nothing below `main.py` imports it at module level" guard is
added. The widened `ephemeral=True` scan surfaced `reply_long`'s deliberate pass-through
(allowlisted with its reason). No schema change.

**0.19.34** is phase 2 of the `main.py` split: the twelve service singletons and three
player-property constants move to `app/bot/services.py`, and the five shared formatters
(`roll_line`, `human_duration`, …) to `app/bot/formatting.py`, both below `main.py` so any
command module can import them normally. Three of the ten call-time `from ..main import`
hooks in `family.py`/`sect.py` are deleted as a result, and the two byte-identical copies
of `roll_line` that split stage 4 had made in `beast.py`/`duel.py` are replaced by one
import. Guarded by `Phase2SplitTests`; no behaviour change, no schema change.

**0.19.35** is phase 3 of the `main.py` split: the six location helpers (`_known_locations`,
`_world_is_unlocked`, `location_autocomplete`, …) plus `current_npc_location` move to
`app/bot/locations.py`. Fifteen of the nineteen dependency edges between the player-command
blocks still in `main.py` pointed at these, so this is the move that lets the rest come out
cleanly. Three more of `sect.py`'s call-time `from ..main import` hooks are gone (five of the
original ten remain, all thread helpers for phase 4). Guarded by `Phase3SplitTests`; no
behaviour change, no schema change.

**0.19.36** fixes the narrator posting a reasoning model's scratchpad ("Here's a thinking
process: 1. Analyze User Input: …") as a player's expedition narration. The existing
scratchpad guard in `app/ai_router.py` only ran when the model's `content` was empty, and
none of its patterns matched this shape anyway. The guard now runs on every player-facing
reply, recognises this family of thinking-out-loud, salvages real prose from `<think>` blocks
or after a "Final narration:" label when it can, and otherwise rejects the reply so the
router moves to the next free model and finally the procedural fallback. Rejections are
counted per model and shown in `/admin server ai_status`. No schema change.

**0.19.37** stops dead OpenRouter free routes from eating the 50/day budget - a route that keeps
failing now backs off harder each time (60s, 120s, … capped at 30 minutes) instead of being
retried every minute, which is how 23 narrations had cost 50 slots - and makes
`/admin server ai_status` say which upstream served each route and whether the operator's own
provider key (OpenRouter BYOK) was used or the request fell back to the shared pool. Documents
in `.env.example` how to make a failing BYOK key surface its real error. No schema change.

**0.19.38** switches model reasoning off on every narration request (`OPENROUTER_DISABLE_REASONING`,
default true - both production failures of the free chain were reasoning) and replaces the
fallback routes: routine is now Gemma 4 31B → MiniMax M3 → openrouter/free, epic is Gemma 4 31B →
GLM 5.2 → openrouter/free. Nemotron 3 Super (rejected 3 of 3 for narrating its instructions) and
the second Gemma (same single Google pool as the first) are gone. No schema change.

**0.19.39** is phase 4 of the `main.py` split: discovery art, per-character derived state,
guild channels/roles/log and persistent player threads move to `app/bot/discovery.py`,
`character_state.py`, `channels.py` and `threads.py` (644 lines). The last five call-time
`from ..main import` hooks in `family.py`/`sect.py` are gone - ten at the start of the split,
zero now, and a test keeps it there. Guarded by `Phase4SplitTests`; no behaviour change, no
schema change.

**0.19.40** is phase 5 of the `main.py` split: the admin permission gate, audit trails and the
eight `/admin` group objects move into a new `app/bot/admin/` package (`core.py`), guarded so the
`/admin` root cannot lose its administrator-only and guild-only flags in the move. No behaviour
change, no schema change.

**0.19.41** is phase 6 of the `main.py` split: managed channel messages, the `#bugs` forum,
server setup/repair, the dashboard's Discord bridge, the chat monitor and eight `/admin server`
commands move into `app/bot/admin/` (`channel_messages.py`, `bugs_forum.py`, `server_setup.py`;
1,563 lines). No behaviour change, no schema change.

**0.19.42** is phase 7 of the `main.py` split: 32 more `/admin` commands (player grants and
karma, sect, simulation, inspect/teleport/revive, backup, audit, maintenance, advancetime) move
into `app/bot/admin/world_ops.py` and `inspect_sim.py`, with two shared autocompletes into a new
`app/bot/pickers.py`. 40 of 43 admin commands are now out of `main.py`; the three that open or
close event scenes follow the event-scene UI in phase 8. No behaviour change, no schema change.

**0.19.43** is phase 8 of the `main.py` split: the event-scene panel and thread spawners
(`app/bot/ui/event_scene.py`), the `/begin` character-creation flow (`ui/creation.py`), the bot
class and instance (`bot.py`), and the last three `/admin world` commands leave `main.py` (1,534
lines). The event panel reaches the two helpers still in `main.py` through the existing
`EVENT_HANDLERS` registry rather than an import. No behaviour change, no schema change.

**0.19.44** is phase 9a of the `main.py` split: `/aptitude`, `/territory`+`/war`+`/caravan`+`/party`,
`/secretrealm`, and the cultivation progression commands (`cultivate`, `breakthrough`, `/seclusion`,
`/body`, `/bodyperfect`, `/perfect`, `/tribulation`) move to `app/bot/commands/` (1,284 lines) - the
four domains nothing else in `main.py` reads. No behaviour change, no schema change.

**0.19.45** is phase 9b of the `main.py` split: the character commands (`begin`, `sheet`, the player and
quest dashboards, `/fate`, `/bond`, samsara), the economy (`wallet`, `use`, `/storage`, `/auction`,
`/civilization`, `/market`, `/blackmarket`) and the abode commands (`/abode`, `/array`, `spatial_key`,
`/innerworld`) move to `app/bot/commands/` (1,553 lines); `usable_item_autocomplete` joins `pickers.py`.
`main.py` is down to 4,064 lines. No behaviour change, no schema change.

**0.19.46** is phase 9c of the `main.py` split: `explore`, `hunt`, `craft`, `/alchemy`, `/realmhub` and
`/travel` move to `app/bot/commands/exploration.py` (852 lines, one contiguous block). A new guard resolves
every relative import in the bot package against the files on disk. `main.py` is down to 3,199 lines. No
behaviour change, no schema change.

**0.19.47** is phase 9d of the `main.py` split: `/battle` (with its panel views and the `bounty` command) and
`/law`, `/manual`, `/condition`, `/profession`, `/crime` move to `app/bot/commands/battle.py` and `law.py`
(850 lines); the `battle_panel` registry binding moves with the panel. `main.py` is down to 2,339 lines. No
behaviour change, no schema change.

**0.19.48** is phase 9e of the `main.py` split and completes phase 9: `talk`, `action`, `npcinfo`, `/scene`
and `sense`, `conceal`, `check`, `world`, `worldevents`, `time`, `rulers`, `worldrules` move to
`app/bot/commands/scene.py` and `sense.py` (1,056 lines); the two scene registry bindings move with the
panel helpers. No player command is defined in `main.py` any more (1,283 lines, from 11,650). No behaviour
change, no schema change.

**0.20.0** completes the `main.py` split (phase 10, the final sweep): the command-surface wiring moves to
`app/bot/surface.py` and `main.py` becomes a 35-line composition root (11,650 lines at v0.19.32). The
sweep drops the imports `main.py` no longer read and the dead `_tribulation_currency` helper, and adds a
guard that every module under `app/bot/` is loaded at startup. The minor-version bump marks the end of the
decomposition; no behaviour change, no schema change. See `docs/V020_RELEASE_NOTES.md`.

**0.20.1** groups the 43 flat modules under `app/` into `app/rules/` (gameplay rules and content helpers),
`app/ops/` (plumbing), `app/ai/` (routing, narration, retrieval, chat monitor) and `app/dashboard/`
(`server`, `contract`); `database_bootstrap` becomes `app/database/bootstrap.py`. A layering guard pins the
tiers. Two container entrypoints change (`app.database.bootstrap`, `app.ops.healthcheck`); `app.bot` and
`app.dashboard` do not. No behaviour change, no schema change.

**0.20.2** archives the stale `RELEASE.txt` (it described v0.19.24) to `docs/migration_history/` and keeps
the four deployment scripts at the root on purpose: the installed `update.sh` requires `startup.sh`/`stop.sh`
there and replaces itself only there. No behaviour change, no schema change.

See `docs/V020_RELEASE_NOTES.md` for v0.20.0, `docs/V019_RELEASE_NOTES.md` for the full detail on every v0.19.x release above, `docs/V018_RELEASE_NOTES.md` and
`docs/V018_BUILD_HISTORY.md` (consolidated validation/audit record) for the prior staged-authority migration.

## Release status — v0.20.2

- Current release: v0.20.2: top-level tidy-up; `RELEASE.txt` archived, deployment
  scripts deliberately kept at the root.
- v0.20.1: `app/` grouped into `rules/`, `ops/`, `ai/`, `dashboard/`
  packages with a layering guard.
- v0.20.0: the `main.py` split complete: the wiring moves to
  `surface.py`, `main.py` is the composition root.
- v0.19.48: phase 9e of the `main.py` split: scene and sense
  commands leave `main.py`; no player command is defined there any more.
- v0.19.47: phase 9d of the `main.py` split: battle and law
  commands leave `main.py`.
- v0.19.46: phase 9c of the `main.py` split: exploration, craft
  and alchemy commands leave `main.py`.
- v0.19.45: phase 9b of the `main.py` split: character, economy
  and abode commands leave `main.py`.
- v0.19.44: phase 9a of the `main.py` split: aptitude, territory,
  secret-realm and cultivation commands leave `main.py`.
- v0.19.43: phase 8 of the `main.py` split: the shared UI, the bot
  class and the last `/admin` commands leave `main.py`.
- v0.19.42: phase 7 of the `main.py` split: the admin operations
  (`world_ops.py`, `inspect_sim.py`) and shared `pickers.py` leave `main.py`.
- v0.19.41: phase 6 of the `main.py` split: the admin server layer
  (channel messages, bugs forum, server setup, dashboard bridge) leaves `main.py`.
- v0.19.40: phase 5 of the `main.py` split: the admin core moves to
  `app/bot/admin/core.py`.
- v0.19.39: phase 4 of the `main.py` split: the rest of the shared
  plumbing leaves `main.py`; zero deferred `main` imports remain.
- v0.19.38: reasoning off on every narration request and new
  fallback chains (MiniMax M3, GLM 5.2; Nemotron Super dropped).
- v0.19.37: escalating backoff for failing OpenRouter routes and
  provider/BYOK visibility in `ai_status`.
- v0.19.36: narrator no longer posts a reasoning model's scratchpad as
  narration; the guard runs on every player-facing reply and salvages or rejects.
- v0.19.35: phase 3 of the `main.py` split: `locations.py` leaves
  `main.py`; three more deferred hooks gone (five of ten remain, all for phase 4).
- v0.19.34: phase 2 of the `main.py` split: `services.py` and
  `formatting.py` leave `main.py`; three deferred hooks and two `roll_line` copies gone.
- v0.19.33: phase 1 of the `main.py` split: test scaffolding
  (package-wide source scans, stage-4 modules guarded), no `app/` change.
- v0.19.32: the Bugslayer Sword reward item and its passive, `/admin player
  grant` hardening, a split-stage-4 startup crash fix, and the tests that
  split had broken repaired.
- v0.19.31: a hub-UI ephemeral-visibility fix and privacy pass merged in (with
  two parts deliberately rejected) from a reviewed community patch.
- v0.19.30: split stage 3 - `/sect` leaves `main.py` for
  `app/bot/commands/sect.py`, and this `README.md`/`VERSIONS.md` split.
- v0.19.29: fixed dashboard write attribution and a public-channel privacy
  leak in the `#xianxia-info` guide, wired up world-time scale from the dashboard, added a
  dynasty/samsara unstick control, four crafting-adjacent admin controls, a mute/freeze moderation
  system (schema 27), and an "undo the most recent admin action" control.
- v0.19.28: a database restore command (the Go engine's first online-restore capability) and a
  debuff/condition-clear admin control.
- v0.19.27: six more Admin Console controls for sect membership/rank and character progression
  detail.
- v0.19.26: nine new Admin Console controls (character-sheet editing, NPC/world-state editing,
  player moderation groundwork, bulk/server-wide actions).
- v0.19.25: a production-review pass — ack-before-mutate, persistent event-scene timeouts,
  `/explore` road-discovery/frontier logic, and live Discord-timestamp travel countdowns.
- v0.19.24: per-route OpenRouter rate limits (RPM + RPD per model) alongside the account-wide pair.
- v0.19.23: split stage 2 - `/family` leaves `main.py`.
- v0.19.22: the expedition journal opens automatically on returning to the world.
- v0.19.21: a live picker for Bind, matching Equip/Unequip/Repair, plus a wrong-path fix.
- v0.19.20: fixes for the 90.9% procedural-fallback rate `ai_status` surfaced in production.
- v0.19.19: closed an unauthenticated slow-header DoS in both HTTP listeners.
- v0.19.18: Scene Action reworked to need no dropdowns; state travels with the player.
- v0.19.17: the "0 stones" bug and four others found alongside it.
- v0.19.16: `openai` dependency bump (2.52.1 → 3.7.0) and the OS-trust-store TLS fix it required.
- v0.19.15: the administrator AI monitor (`ai_status`, `chat_digest`).
- v0.19.14: split stage 1 corrected again (annotation-namespace fix + import harness).
- v0.19.13: definition-order fix - still did not start.
- v0.19.12: stage 1 of the main.py decomposition (shared runtime extracted) - did not start.
- v0.19.11: relevance-ordered actions and deployment hardening.
- v0.19.10: the Components V2 layout on all 17 hubs.
- v0.19.9: portable release-integrity check (BusyBox-safe).
- v0.19.8: onboarding copy pointing at reachable commands, gated by a test.
- v0.19.7: hub-layout corrections made after seeing the panel render live.
- v0.19.6: a correctness release fixing the findings of an external audit
  (engine batch durability, dashboard snowflake precision, cleared-message persistence, backup
  naming, `#bugs` tag sync, release-manifest integrity). No schema change.
- v0.19.5: a GUI release adding the Components V2 hub layout, piloted on `/character`.
- v0.19: a cultivation-depth consistency/coverage pass plus a further authority-migration pass for
  1v1 battle start and mid-battle item recovery, built on top of the v0.18 staged-authority migration.
- The v0.18 release gate added loopback-by-default standalone engine binding plus strict bounded JSON
  request handling; v0.19 added three schema migrations (schema 25 for GM-authored per-channel messages,
  schema 26 for the #bugs forum channel, schema 27 for v0.19.29's mute/freeze moderation columns),
  otherwise only mechanics/logic and documentation fixes.

## Schema history

- **Schema 14** introduced player memory and narrator-safe canon FTS.
- **Schema 15** added structured permanent world history and `world_history_fts`.
- **Schema 16** added persistent NPC life/social/descendant systems.
- **Schema 17** adds the current event participation/GUI persistence layer and associated current schema updates.
- **Schemas 18-24** carried the v0.18 staged-authority migration (forage/crafting/companions, canonical time,
  unified lifespan, multi-hop road travel, caravan mechanics, dashboard-owned Discord setup) through to its
  final state.
- **Schema 25** added GM-authored per-channel welcome messages (`channel_messages`).
- **Schema 26** added the `#bugs` forum channel (`bugs_channel_id`).
- **Schema 27** added the v0.19.29 mute/freeze moderation columns on `characters`
  (`is_muted`, `is_frozen`, `moderation_reason`).

See `docs/V018_RELEASE_NOTES.md` and `docs/V019_RELEASE_NOTES.md` for the per-release detail.

## Release notes

See `docs/V019_RELEASE_NOTES.md` for the current release's cultivation-depth audit, dashboard coverage gaps, and
combat authority-migration fixes. See `docs/V018_RELEASE_NOTES.md` for the complete staged-authority, road/caravan,
setup, cleanup, migration, security, and upgrade summary that v0.19 builds on.
