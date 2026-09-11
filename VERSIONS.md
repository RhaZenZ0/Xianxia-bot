# Xianxia RP Discord Bot — Version History

This is the release-by-release changelog for the Xianxia RP Discord Bot, split out of `README.md`
so the README can stay focused on architecture, setup, and current operational documentation. See
`README.md` for that; see `docs/V021_RELEASE_NOTES.md`, `docs/V020_RELEASE_NOTES.md`, `docs/V019_RELEASE_NOTES.md`, `docs/V018_RELEASE_NOTES.md` and
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

**0.20.3** audits the Python test suite: 70 files become 59 (eight merges, three deletions, four moved from
integration to unit), the eleven per-phase split-guard classes fold into one `test_bot_package.py` organised
by invariant, and the stale `SCHEMA_VERSION == 27` copies leave seven files. The audit found one bug: the
dashboard implementation gate had looked for `app/dashboard.py` since v0.20.1 (fixed, guarded). No schema
change.

**0.20.4** adds the release channel: releases are GitHub Releases built by CI from a tag (`v0.21.0` stable,
`v0.21.0-beta.1` beta); the bot announces a newer release in the log channel; `update.sh --check/--fetch/--upgrade`
download and SHA-256-verify an archive before the unchanged transactional install. `docs/ROADMAP_1_0.md` is
the roadmap to v1.0.0. No behaviour or schema change.

**0.20.5** makes `update.sh` validate the installed `.env` against the new release's requirements
(`startup.sh --check-env`) before it stops the stack: a missing key such as `ENGINE_AUTH_TOKEN` is now a
message instead of a stop / fail / roll back / restart cycle. No schema change.

**0.20.6 (build A)** adds the Quest Forge: `/admin world questforge <story>` drafts a quest from a story through the
narrator's free chain, validated against the world and a GM reward budget, held as a draft until approved
(`/admin world quests`, or the dashboard); optionally one draft per notable world-history event. Rewards are
granted by the engine on completion. Two dead ends fixed: `sect_trial` was never reported (the shipped sect
quest could not complete) and quest rewards were never granted. Schema **28** (`quest_definitions`).

**0.20.6 (build B)** fixes the dashboard's Adjust Inventory card storing whatever was typed as an inventory
`item_id`: a display name such as `Bugslayer Sword` became a row no catalog lookup could match, so the
sword showed with no description and could never be bound or equipped. The dashboard now resolves the
id or the item name (case-insensitively) and refuses anything else with suggestions, and the Go engine's
`admin.player.adjust_item` enforces the one-per-character rule for unique reward equipment, carried or
bound. Existing bad rows are repaired through the same card: `-1` of the misspelt id, `+1` of the real
one. No schema change.

**0.20.7** is the merge of the two 0.20.6 builds above, which were produced in parallel from 0.20.5 and
never saw each other: Quest Forge (schema 28) and the dashboard item-grant fix ship together, with no
further code change. Either 0.20.6 zip is superseded by it.

**0.20.8** fixes `update.sh` silently discarding a release's `.sha256` sidecar asset. `resolve_release()`
isolated the current release object from the GitHub API listing by truncating at the next `{"url":...}`,
but every entry in the release's own `assets` array also starts with its own `"url"` field; when the
sidecar wasn't the first asset (as with v0.20.7), that truncation cut it out of the isolated object, so
`fetch_release` refused a perfectly good, fully-published release as unverifiable. The truncation now
anchors on `"assets_url"`, which is always the release object's second top-level key and never appears
on an asset. No schema change, no gameplay change.

**0.20.9** makes `update.sh` send the engine token with its pre-update backup request (every update from a
0.20 engine had failed there), abort into rollback when a delete or copy fails, and refuse to start a tree
that does not match `RELEASE_MANIFEST.sha256`. No schema change.

**0.21.0** opens the roadmap's Authority I milestone. The authority-boundary contract gains the v0.21 gate: an
allowlist of every DB write reachable from `app/bot/` and `app/ops/` (27 gameplay rows at the start, plus the
bookkeeping writes that stay in Python), which must be empty before v0.21 is tagged stable. The first row
lands: the non-battle `/use` path is the engine action `item.use` (consume, restore, life extension,
effect, toxicity in one transaction), and the handler only formats. 22 rows remain. No schema change.

**0.21.1** is typed play. With `AUTO_NARRATE=true` every line in a hub channel or
scene thread used to be one narration call that decided nothing. Now a line that
starts with `TYPED_PLAY_PREFIX` (default `>`) is routed — deterministically, no
model — to the handler a hub button would run (`/explore`, `/hunt`, `/cultivate`,
`/breakthrough`, the eight scene actions, `/talk`); an un-prefixed line that
addresses a present NPC by name, or @mentions the bot, is dialogue; everything
else is speech, recorded and free. Ambiguity becomes a picker, never a guess.
Every line that can reach the engine or the narrator spends a token from a new
per-player bucket (`TYPED_PLAY_BURST` / `TYPED_PLAY_PER_MINUTE`). Typed play adds
no handler, no engine action and no database write, so the v0.21 authority gate
is unchanged. No schema change. See `docs/V021_RELEASE_NOTES.md` and
`docs/COMMISSIONS_DESIGN.md`.

**0.21.2** adds **Teardown** to the dashboard's Discord tab: delete every thread the bot
tracks, every bound channel (all seven base channels, the realm hubs, `#bugs`) and the two
Xianxia categories when nothing else is left in them, then forget the ids - typed `DELETE`
to confirm, audited, nothing recreated, database untouched. It also fixes the realm-capital
**visibility gate**: the `Xianxia • <world>` roles were created and assigned but no hub
channel ever received a permission overwrite, so every capital was visible to everyone.
Setup/Repair now denies `@everyone` and allows the world's role on each hub, the dashboard
shows per-hub visibility, and a visible hub is not "ready". No schema change.

**0.21.3** answers "how does a player get a manual?" - which, it turned out, they mostly
could not. The 142 generated "Jade Manual" inheritances existed only in Python's memory
(`augment_advanced_catalog`); the Go engine read `content/world.json` raw, so `manual.study`
refused them as unknown and no righteous manual was obtainable. `scripts/materialize_world_catalog.py`
writes the expansion into the file once (148 manuals, 528 techniques, 187 items; the hidden
Heaven-Devouring Demon Sect flagged `hidden` and skipped by bootstrap; manual items
`market_excluded` so town markets do not list them), and a test fails if the file ever drifts.
On that footing, **passing a sect's entrance trial bestows one manual**, chosen by the engine
inside the trial transaction: the sect's alignment (a righteous sect never hands out a forbidden
art), the character's own path, the lowest tier - within reach, or the lowest there is to grow
into - never a duplicate, recorded in `item_provenance` as `sect_entry`. No schema change.

**0.21.4** gives every public sect a genuine tier-0 entry manual - six authored inheritances in
`content/world.json` (Azure Cloud Foundation Sword Canon, Crimson Furnace Ember-Tempering Record,
Frozen Moon First-Frost Sutra, Black Serpent Venom-Fang Primer, Blood River Crimson Tide Initiation,
Corpse Lantern Pale-Flame Initiation), each with three techniques unlocking at mastery 0/1/2,
aligned with its sect, path-agnostic, studyable the day a disciple joins. The engine's entry-manual
selector takes the sect's own manual first; the v0.21.3 rules apply only once a character already
has it. Catalog is 154 manuals / 546 techniques. No schema change.

**0.21.5** is the first two of the AI changes: the **input fence** (every player-authored slot in a
narrator prompt - dialogue, action, recent history, NPC memories - is wrapped in BEGIN/END markers,
length-capped, and marker-safe, and both system prompts say what a fence means; v0.23's item pulled
forward) and the **content batch** (encounters for the ten locations that had none - six of which
hard-errored `/explore` - sense hints for seven, and real personality/speech/want/fear/secret for the
four world rulers and Steward Qiao; v0.25's content gate landed as tests). Also: every engine
cooldown error reaches the player as a wait ("ready in **2h 55m**"), never "cultivation cooldown
remaining: 10520". No schema change.

**0.21.6** hides each realm capital until you are in the city. Setup/Repair creates one presence
role per capital (`Xianxia • <capital name>`, no guild permissions) and gates the capital channel on
it: `@everyone` denied, the presence role granted the full member set (view, send, threads, history,
reactions, embeds, files, slash commands), the bot allowed, and the v0.21.2 realm-access allow removed
so an unlocked-but-absent cultivator no longer sees the room. The bot puts the role on when a
character's location is that capital and takes it off when it is not - on every command, on every
line in a scene channel, and immediately after hub travel - making no Discord call when nothing
changed. The realm-access roles stay (Sync Realm Roles, diagnostics) but gate nothing. No schema change.

**0.22.0** is the roadmap's **Commissions** milestone. A commission is a quest a giver NPC offers
you in character: you hold one at a time, its terms and deadline are fixed at accept, and it ends
completed, failed or abandoned - with **failed and abandoned costing exactly the same**, so
abandoning is allowed without being a cheap reroll. `commission.accept` and `commission.resolve`
are new authoritative engine actions and the first replaces `DB.accept_quest`, closing the last
v0.21 row on the DB-write allowlist; the world tick fails past-deadline commissions, so a deadline
survives a restart. Selection runs a pure ladder (held / on cooldown / a pool match within the
standing-derived tier ceiling / nothing) before any model call, and the narrator is handed the
result as canon with the standing as a band rather than a number - the accept buttons are built
from that block, never from the reply. Ships with three givers and nine authored commissions, an
abandon confirmation that states its cost, and a GM Commissions dashboard tab (approve / retire /
discard, both audited). Schema **29** adds the giver/tier/variant/deadline columns to
`quest_definitions`, the commission columns to `character_quests`, and the cooldown and outcome
counters to `npc_relationships`. Seeded invention - the design's second producer - is deliberately
not built yet. See `docs/V022_RELEASE_NOTES.md` and `docs/COMMISSIONS_DESIGN.md`.

**0.22.1** finishes the giver roster. Eleven givers instead of three: the two old men who sleep and
posture in Greenriver, and a quest board for every public sect. A commission may now keep its terms
to itself (`reward_visibility: hidden`) - a presentation rule only, since the engine still locks
exact rewards at accept and states the payout in full on completion, while the offer card says
*undisclosed* and warns that it may be worth far more or far less. Old Beggar Chen, who is quietly
the Void Sword Venerable, pays extravagantly for errands that look like nothing; Old Gou, the
self-declared hidden expert, promises an emperor's inheritance and pays eight spirit stones - and
from the offer card you cannot tell them apart. The narrator is explicitly forbidden from naming a
figure for undisclosed work in either direction. Sect work (`requires_sect`) is checked in the engine
at accept as well as by the offer ladder, and never appears in the quest journal: the board is a
person, and you have to be a disciple. Schema **30** adds `requires_sect`, `reward_visibility` and
`boast` to `quest_definitions`.

**0.22.2** fixes the P0 findings from an external review. The world simulation could apply the same
interval several times when `/run-due` calls overlapped - the anchor was read before the write
transaction, so two callers both decided one interval was due (measured at 7-14 applications of one
interval under 32 concurrent callers); the whole decide-and-apply cycle now runs inside one
`BEGIN IMMEDIATE` per system, with the anchor update guarded on the value it decided from. A
duplicate authoritative request that arrived while the original was still in flight failed on a
unique constraint instead of replaying; the receipt is now re-checked inside the write transaction,
so 32 concurrent identical requests produce one mutation, one event, one version bump and 32
identical results. An ordinary quest could complete and never pay, because completion and reward
were separate commits and no retry could reach a quest that was no longer active; rewards now travel
with the progress report and are granted by the transaction that completes it, with an engine-side
cap independent of the caller. And `RunDue` no longer accepts the world clock as a request
parameter. Both concurrency fixes ship with regression tests verified against the defect. No schema
change.

**0.22.3** closes the review's finding #4: a restore could lose a write it had already acknowledged.
The safety backup was taken before traffic was quiesced, so a mutation committing in the gap was
reported as successful, overwritten by the restore, and absent from the safety backup meant to undo
it - the only copy of an acknowledged change was gone. A process-wide maintenance barrier now runs
in the request middleware: every write path takes it shared (so ordinary traffic is as concurrent as
before), restore and VACUUM take it exclusively, and health checks stay outside it so a readiness
probe does not fail for the length of a restore. Restore's order becomes barrier, close db sessions,
*then* safety backup, then restore. The regression test fires 24 concurrent writes through the real
handler with a restore landing in the middle and asserts that every acknowledged write is in the
live database or the safety backup; against the old ordering it loses 2-8 of them. No schema change.

**0.22.4** closes the review's finding #5: a duel was only checked when it was proposed. Location,
life status and safe-zone were verified at challenge time and never again, so in the five minutes a
challenge lives a target could walk into a city and still accept - starting a duel between two places,
one of them inside formations meant to suppress PvP. One validator now runs at challenge, at accept,
and on every action. The interesting part is the mid-duel answer: refusing an action would strand the
match as permanently active and lock both players out of ever duelling again, so a breached duel is
resolved instead - whoever left forfeits (walking away from a duel you are losing must not be cheaper
than losing it), a death resolves to the living participant, and when nobody is at fault it is void.
Neither pays reputation. Schema **31** adds `location` to `pvp_matches`, because "are you both still
here" needs a here.

**0.22.5** closes the review's finding #9. The engine answered a stop signal by severing every open
connection, including one whose transaction had committed but whose response was not yet written -
leaving the caller unable to tell "it did not happen" from "it happened and I did not hear". It now
drains: stop accepting, wait for the requests already running (bounded, default 20s via
`ENGINE_SHUTDOWN_GRACE_SECONDS`, an expired grace logged rather than swallowed), and only then close
storage. That ordering is the subtle half - `Shutdown` makes `ListenAndServe` return as soon as it is
*called*, so the old `defer engine.Close()` would have pulled the database out from under handlers
still using it. `docker-compose.yml` gives the engine a 30s stop grace so Docker's 10s default cannot
SIGKILL a drain, or a restore, part-way through. No schema change.

**0.23.0** closes **Authority I**. `PLAYER_MUTATIONS` in
`tests/python/contracts/test_authority_boundary.py` is empty: nothing under `app/bot` or `app/ops`
writes a gameplay table any more. The last 21 rows became nine engine actions - `alchemy.purge`,
`admin.player.set_master` / `set_sect_rank` / `master_attention` / `grant_storage`,
`admin.world.spawn_realm`, `character.set_gender`, `sect.discover`, `sect.abode.enter` /
`sect.abode.leave`, `law.technique` and `sect.shadow` - plus wiring for `admin.player.set_sect`,
which already existed in Go and had simply never been called.

The point was never tidiness. Each of these was a sequence of separate writes that could half-happen:
`/alchemy purge` spent Qi, reduced toxicity, rewrote a shared effect row and set a cooldown as four
round trips; `/sect shadow` wrote a membership, an item and its provenance as three; every admin
command changed the world and then, separately, wrote the audit row that says who did it. Each is now
one transaction, and the player-facing ones carry a receipt, so a retried click replays instead of
charging twice.

Behaviour changed in five places where the old code was simply wrong, each noted at the call site:
`set_gender` refused nothing and coerced a typo to "neutral"; `adjust_master_attention` reported
success for a disciple with no master; `set_master`'s cycle check gave up silently after 64 links;
the pill-toxicity penalty only stopped applying if the player happened to open `/alchemy status`;
and `sect.discover` could not tell a caller which sects were actually new, so screens announced
sects the player already knew.

Two defects older than this release turned up while writing its tests. `fmt.Sprint` on a missing map
key yields the four characters `<nil>`, so 25 required-field guards written as
`strings.TrimSpace(fmt.Sprint(p[key])) == ""` passed on exactly the payloads they existed to reject;
they now go through `stringField`. And `test_ack_before_mutation` accepted an ack anywhere earlier in
a handler, including inside a guard branch that returns - three handlers acked on every path except
the one that mutated. The guard now walks the blocks enclosing the mutation and requires an ack that
dominates it. No schema change.

**0.23.1** answers a second external review: eight logic errors, all confirmed against the code
before anything was changed.

The serious one was an asset transfer across reincarnation. Reincarnation wipes inventory and wallets
because they belong to one incarnation, but auctions, bids and caravans are asynchronous records keyed
by the persistent Discord id and settled later by the maintenance sweep, which pays whoever that id
names *at settlement time*. List a rare item on a long auction, die, reincarnate, and the item or its
proceeds arrive in the new body. Bids were the same in reverse, since a bid escrows currency and a
refund follows the id. Rather than stamping a generation on every asynchronous record and hoping every
settlement path remembers to check it, true death now resolves the escrow: listings end and refund
their bidder, the dead player's own bids are released and their lots revert to no bid, caravans are
lost with their owner, and an active seclusion ends. There is one place a life ends, and it runs there.

The other seven: a storage "upgrade" could trade a 500-slot ring with a living space for a 24-slot
pouch, consuming the pouch on the way, because nothing compared the new container to the held one; a
family with 1 stone paid for a 20-stone support package, because `wealth <= 0` guarded a subtraction
that floored at zero; a seclusion whose duration was not a whole number of days could never complete,
because completion required whole-day accounting to reach an end it could not reach; a PvP match
deadlocked when the player holding the turn died, because v0.22.4's breach check sat *behind* the turn
check and so was unreachable by the only player who could still call it; a quest event with no target
progressed every targeted objective of its type, because a nil target short-circuited the comparison;
and the engine accepted any string as an ordinary quest, because a legitimate static quest and an
invented key both had no `quest_definitions` row — static quests are now seeded so that "no row" means
"no such quest".

Four of these had no Go tests at all before this release (`family.support`, `storage.upgrade`,
`seclusion.start`, `seclusion.settle`), and the quest suite tested a *wrong* target but never a
missing one. Every fix ships with tests verified against the defect. No schema change.

**0.23.2** fixes the updater, which could not install v0.23.0 or v0.23.1 on a QNAP.

`update.sh` verifies the release manifest twice: once against the downloaded archive before anything
is touched, and once against the installed tree before starting it. Those were two separate copies of
the same check. The first was made BusyBox-safe some releases ago - `--quiet` and `--strict` are GNU
coreutils extensions, and BusyBox answers "unrecognized option" and exits non-zero - and the second
was not, because that is where the failure had been noticed and the other copy was never touched.

So the update downloaded cleanly, verified cleanly, installed cleanly, and then failed a check on a
byte-perfect tree and rolled itself back. The install was never damaged; it just never upgraded.

There is now one `verify_manifest_tree` helper with two call sites, and a contract test that asserts
there is only one - the duplication is the actual root cause, not the flag. A second test refuses
any GNU-only checksum flag anywhere in the script.

Worth recording: the suite already had a test for the post-install check, and it asserted the literal
string `sha256sum -c --quiet RELEASE_MANIFEST.sha256`. It was holding the bug in place. It now asserts
that the check happens and leaves the spelling to the helper.

**0.24.0** is the Quests workbench, and the fix underneath it.

Until now a quest definition could be created and its status could be changed, and that was the
whole vocabulary. There was no edit. A drafted quest that was ninety per cent right had to be
discarded and re-rolled, because nothing anywhere could change a word of it. The same quest was
also reviewable on two different screens depending on whether it had a giver: the Commissions page
queried `WHERE q.giver_npc<>''`, so forged drafts never appeared there, and the Exploration page
listed them read-only, so the only way to approve one was the embed the Forge replied with in
Discord. The two disjoint tables were the symptom; the missing verb was the disease.

The **Quests** page is one view of every definition a player can be given - static, forged,
hand-written, or a commission an NPC hands out - with a review inbox, the live pool, an editor, a
player-facing preview, approve/retire/discard, and a coverage panel that says which realm bands,
objective types and locations the pool leaves quiet. Quests can now be written by hand
(`admin.quest.save`, keyed `quest_...` rather than `forge_...`), and every save is held to
`validate_quest_definition` - the Forge's own gate - so a GM typing by hand and a model drafting
cannot disagree about what a valid quest is.

**Schema 32** is the fix underneath. Completion rewards were read from the *current* definition at
the moment a player finished, and they arrived in the engine as fields of the caller's payload.
Two faults in one coat: editing a definition silently rewrote a deal somebody had already accepted,
and Python, not the engine, was deciding what the work asked for and what it paid. A commission
never had the first fault - it locks its variant and deadline onto `character_quests` at accept.
That lock is now general: `character_quests.terms_json` records the terms every quest was accepted
under, `quest.progress` reads them off the row, and the `objectives`/`rewards` fields are gone from
its payload. Rows accepted before this release are backfilled from the current definition the first
time they are touched, which is the deal they were already on.

That makes editing a live quest a real decision, so `admin.quest.save` takes a **hold policy**:
`keep` (holders stay on the terms they took - the default, and the only one that cannot cost a
player anything), `migrate` (holders move to the new terms, progress carrying across objective by
objective wherever the objective still means the same thing, with the count of what was lost
returned so the GM is told), or `revoke` (the quest is taken back and can be accepted again fresh).
A migrated commission keeps the payment its giver agreed to; only the work moves. The holders are
read *before* the definition is written, so "keep them as they are" also captures the old terms for
anybody who accepted before pinning existed - otherwise it would have quietly done the opposite of
what it says.

Two consequences worth naming. Retiring a definition no longer strands the people carrying it:
Python used to send progress only for quests in the approved catalog, so a retired quest froze
every holder - unable to finish it, unable to be paid, holding it for good. And `admin.quest.review`
is the general name for the status change `admin.commission.review` was always performing; the old
name keeps working.

Also in this release: the test suite runs about 150 more tests on a minimal machine. `httpx` is
constructed at import time by the transport modules, so without it `from app.database import
Database` raised during collection and took roughly twenty files - every integration test above the
transport - out of the run. `tests/conftest.py` installs a stub that refuses to send a request; the
two files that drive `httpx.MockTransport` skip with a reason instead of erroring.


No application change; no schema change.

See `docs/V025_RELEASE_NOTES.md` for v0.25.0, `docs/V024_RELEASE_NOTES.md` for v0.24.0, `docs/V021_RELEASE_NOTES.md` for v0.21.x, `docs/V020_RELEASE_NOTES.md` for v0.20.0, `docs/V019_RELEASE_NOTES.md` for the full detail on every v0.19.x release above, `docs/V018_RELEASE_NOTES.md` and
`docs/V018_BUILD_HISTORY.md` (consolidated validation/audit record) for the prior staged-authority migration.

**0.25.0** remakes the dashboard, from the design canvas approved before the release. No endpoint
changed, no query changed, and no data was added or removed except one small block on
`/api/overview`.

Two numbers describe what was wrong. There were **23 navigation tabs in one flat row**, and that row
was sticky - between about 900px and 1400px it wrapped onto three lines and ate roughly 120px of
every screen, permanently, before any content was drawn. And there were **102 stacked full-width
tables** across the views: Cultivation was eleven tables one under another, Crafting eleven more,
Exploration nine, Samsara eight, Economy seven. Reaching the last one meant scrolling past the other
ten every time, with nothing on the page saying what was down there.

The nav is now five groups with a jump-to filter, collapsing to an icon rail below 1280px and a
drawer below 900px. Every view gets one page template: header, metric strip, section tabs when there
are three or more sections, then panels. `sectionize` does that by MOVING the nodes a loader already
rendered rather than re-serialising them, so every click handler survives and not one of the
twenty-three loaders had to be rewritten to gain tabs.

One table component now, with a header that sticks - it never did before, `position:sticky` having
been applied inside a wrapper with no height - a row count under every table, and empty states that
say what would put something there. Plus a density toggle, deep links (`#cultivation/4`), and a view
that fails in its own panel with a retry instead of replacing the page.

The one genuinely new thing is Overview's **"Wants your attention"**: quest drafts waiting, a
simulation system that has missed its own tick, commissions past their deadline, players frozen with
no reason recorded. Nothing in it is new data - every row already existed on another page, and what
was missing was any reason to visit that page today. Lag is only reported when a system is further
behind than its own interval (a 4,320-minute system 500 minutes behind is early, not late), and a
frozen player only when no reason was recorded.

The palette, the serif headings, the drawer, the authority split and the coverage gate are all
unchanged. See `docs/V025_RELEASE_NOTES.md`.


**0.25.1** changes the typed-play prefix default from `>` to `$`.

`>` was chosen because Discord renders `> text` as a blockquote, which set an action line apart from
speech in the channel. `$` gives that up; it is a house preference, and a server that wants the
blockquote back sets `TYPED_PLAY_PREFIX=>` explicitly.

Worth recording: the router matches the prefix with `str.startswith`, not a pattern, so `$` - an
end-of-string anchor in a regular expression - is matched as the character it is. A prefix like `*`
or `+` would have been just as safe, and there is now a test that says so, because the day someone
"optimises" that into a compiled pattern the failure is silent: every action line stops being an
action and becomes speech.


**0.25.2** reorganises `.env.example` and fixes what the first run tells you.

The five values you must supply were scattered: `DISCORD_TOKEN` and `GUILD_ID` at the top,
`ENGINE_AUTH_TOKEN` filed under "Discord" beside a commented-out engine shutdown knob,
`OPENROUTER_API_KEY` sixty lines down in the AI section, and `DASHBOARD_TOKEN` at the very bottom of
a 226-line file. They are now one block at the top, above everything that has a working default, and
each says how to generate it. Nothing else moved except into the section it belonged in - the chat
monitor and typed play out of "Discord", the engine shutdown grace into the engine section - and no
key was added, removed or duplicated.

`startup.sh` had the same fault as a bug it already carried a test for. Its first-run message named
`DISCORD_TOKEN`, `GUILD_ID`, `OPENROUTER_API_KEY` and `DASHBOARD_TOKEN`, and then the script
hard-failed on `ENGINE_AUTH_TOKEN` three lines later - a requirement the operator had never been
told about. The message now names all five, and the test that was written for the first instance is
generalised: it reads the required values out of the script's own `fail` lines and asserts each one
is announced, so a new one fails the build until it is. It also looks only at what is echoed, since
a comment in the same block mentioning the name would otherwise satisfy it.

Also documented: the five `DASHBOARD_*` host and port keys, which look interchangeable and are not.
`docker-compose.yml` publishes `<BIND_ADDRESS>:<PORT>:8090`, so under Docker the container side is
fixed and only the first two should be changed.


**0.25.3** drops MiniMax M3 from the routine narration chain.

`minimax/minimax-m3:free` was the second hop on the routine tier. It is reasoning-native, it ignores
OpenRouter's unified `reasoning.enabled=false` (which every narration request already sends), and it
returns its own analysis in `content` rather than in a reasoning field. The guard added in v0.19.36
catches that and refuses the reply - `ScratchpadResponse: AI response was reasoning scratchpad, not
narration` - which is the correct outcome for a player, but it means the hop never served a single
narration while still spending one of the 50 daily free-tier slots on every attempt: with Gemma
throttled on the shared pool, routine narration paid two upstream calls to reach `openrouter/free`.

The routine fallback is now `z-ai/glm-5.2:free`, which already served the same position on the epic
tier. Both tiers now share it. Nothing else about the router changed: the scratchpad guard, the
salvage path and the escalating per-route backoff are all as they were, and a reasoning route still
gets rejected rather than shown to a player if one appears via `openrouter/free`.

Three regression tests cover it. One pins both dropped routes - MiniMax M3 and Nemotron 3 Super - out
of every default chain. One gates `README.md` and `.env.example` against `app/ai/ai_router.py`, so a
default that moves only in the code cannot leave an operator copying the dead route back in by hand.
The third is the existing chain-shape test, unchanged.

No schema change, no game-rule change. Operators who set `OPENROUTER_ROUTINE_FALLBACK_MODEL`
explicitly in `.env` keep whatever they set; to take the new default, remove the line or set it to
`z-ai/glm-5.2:free`.


**0.25.4** fixes the Google AI Studio key instructions, and a comment v0.25.3 missed.

`.env.example` and `README.md` both explained where to *paste* an AI Studio key - OpenRouter's
Integrations page - and never where to get one. That is the step an operator is actually missing, so
both now name `https://aistudio.google.com/api-keys` first: AI Studio creates a project and a key for
a new account by itself, so there is usually already one there to copy. Also stated, because both
were reasonable things to assume and both are wrong: the free tier is enough (nothing here needs
Cloud Billing), and no Gemini SDK is involved (OpenRouter makes the calls).

The second fix is a v0.25.3 miss. That release changed `OPENROUTER_ROUTINE_FALLBACK_MODEL` in
`.env.example` but left the chain summary in the comment three lines above it reading
"Gemma 4 31B Free -> MiniMax M3 Free -> OpenRouter Free Models Router" - the line an operator reads
before the assignment they copy. The v0.25.3 doc gate only compared `NAME=value` lines, so it passed.
It now also refuses a dropped route's name in any `->` chain summary in the file, and a second test
gates both key URLs.

Docs and tests only. No schema change, no game-rule change, no router change.


**0.26.0** adds an optional direct Google AI Studio narration route.

Until now every narration route went through OpenRouter, whose free tier is capped at about 50
requests a day across all free models. Once that budget is spent the router stops locally and play
continues on procedural prose - correct behaviour, and the most common reason narration goes flat.

Set `GOOGLE_AI_STUDIO_API_KEY` (or `GEMINI_API_KEY`, the name Google's own quickstart exports) and a
new hop appears at the front of both chains: `aistudio/gemini-3.8-flash`, called directly with the
`google-genai` SDK. It is the only route in the bot that does not go through OpenRouter, and that is
the entire point - it is billed against the operator's own AI Studio key, so it deliberately does not
spend `AITaskRouter.limiter`, and an exhausted OpenRouter budget no longer stops it. The model is
configurable through `GOOGLE_AI_STUDIO_MODEL` and must carry the `aistudio/` prefix, which is how the
router tells the two transports apart.

Nothing changes for an operator who does not set the key: the route is not constructed, not in the
chains and not in `ai_status`, and the OpenRouter chain behind it is byte-for-byte what v0.25.4
shipped.

The route is not trusted more than any other. Its reply goes through the same
`_validate_generated_text` guard, so a reasoning scratchpad, a prompt leak or an empty reply is
rejected and the chain falls through to OpenRouter and then to procedural prose, with the same
per-route cooldown, escalating backoff and health counters as every other hop. `OPENROUTER_REQUIRE_FREE`
does not apply to it, because it is a statement about OpenRouter's catalogue and this route has no
OpenRouter catalogue entry; the prefix check runs first so a missing prefix reports the actual
mistake rather than blaming the free-route guard.

`google-genai` is imported lazily and is the one dependency in `requirements.txt` without an upper
bound. If it is missing, too old, or fails to construct a client, the route is left out of the chain
and the bot narrates through OpenRouter exactly as before - and `/admin server ai_status` says so in
as many words, because a key that is set while the route is silently off is otherwise invisible.
Google is mid-migration between the Interactions API and `models.generate_content`, so both call
shapes are supported and whichever the installed SDK exposes is used; an unknown keyword is dropped
and the call retried rather than presenting as a dead route.

**Verification caveat, stated plainly.** `google-genai` could not be installed in the environment
this release was built in, so every test here runs against a shim of the SDK, not the SDK. The
router-side behaviour - chain placement, budget isolation, guard enforcement, fall-through, the
opt-in default - is fully covered and proven. What is *not* proven is that the SDK's real keyword
names match the ones sent. That is why the adapter drops unknown keywords and feature-detects both
call shapes, and why the failure mode is "route off, OpenRouter narrates, ai_status explains" rather
than an exception. Try it with `/admin server ai_status` open before relying on it.

20 new tests in `tests/python/unit/test_google_aistudio_route.py`, plus four in `test_config.py`.
No schema change. No game-rule change. AI remains narration-only.


**0.26.1** drops the dead GLM 5.2 hop, and stops calling a timeout a certificate failure.

Two production findings from the first v0.26.0 deployment, both visible in one `ai_status` panel that
showed every route at "NEVER SUCCEEDED" and narration 100% procedural.

*The second hop was gone from OpenRouter's catalogue.* `z-ai/glm-5.2:free` now answers
`404 - This model is unavailable for free. The paid version is available now - use this slug instead:
z-ai/glm-5.2`. It was the second hop on BOTH tiers as of v0.25.3, so every narration paid an upstream
call, and one of the 50 daily free-tier slots, to be told the route no longer exists. The paid slug
cannot take its place: `OPENROUTER_REQUIRE_FREE` rejects it, correctly.

There is deliberately no replacement literal. This is the second shipped default to die in the slot —
MiniMax M3 for scratchpadding in v0.25.3, GLM 5.2 for leaving the free tier here — and a named free
slug is only ever as good as OpenRouter's catalogue on the day it was written. Both fallback slots
are now empty by default and `openrouter/free`, the dynamic router that resolves to whatever is
actually free when the call is made, carries the tier. The capability is unchanged: set
`OPENROUTER_ROUTINE_FALLBACK_MODEL` or `OPENROUTER_EPIC_FALLBACK_MODEL` to put a named hop back.

This does retire an invariant from v0.19.38, which required a named non-Google route ahead of
`openrouter/free` so a Google-side problem could not take both tiers procedural. It is no longer
expressible without pinning a slug that may be withdrawn in its turn, and a hop that 404s is not a
working non-Google route either — it is the same outage plus a wasted daily slot. The diversity
guarantee now rests on `openrouter/free` being itself a router across many providers. The test that
encoded the old rule says all of this rather than having quietly lost an assertion.

*A timeout was being reported as a broken trust store.* `asyncio.wait_for` cancels the in-flight
request, and a request cancelled mid-TLS-handshake leaves the half-finished SSL exception in the
context chain — so `_looks_like_tls_failure` walked into it and flagged `TimeoutError` with the
🚨 TLS/certificate banner, sending the operator to check `ca-certificates` and `SSL_CERT_FILE` for an
upstream they simply could not reach in time. A timeout is now never a TLS failure; a genuinely bad
trust store raises the SSL error itself, which is still detected exactly as before.

Docs and defaults only, plus the one classifier line. No schema change, no game-rule change, AI
remains narration-only.


**0.27.0** adds a daily liveness check that retires dead routes before a player finds them.

Two of the last three releases were spent removing a shipped default after it died upstream, and both
times the bot found out the same way: a player got procedural prose, and the failure was only visible
afterwards in `/admin server ai_status`. MiniMax M3 was withdrawn in v0.25.3 for returning its
scratchpad; `z-ai/glm-5.2:free` in v0.26.1 for leaving the free tier and 404ing every call. In each
case the dead hop kept costing one of the ~50 daily free-tier slots per narration until a human
noticed and shipped a release.

Every `ROUTE_AUDIT_HOURS` (default 24, `0` disables) the bot now pings each configured route with the
cheapest request the API will take: one character in, `max_tokens=1` out, no system prompt. It never
reads the reply. That is the whole design — the failures worth catching (a withdrawn slug, a rejected
key, a refusing proxy, an unreachable host) all arrive as exceptions, so "did the call return" is the
entire verdict, and an empty reply from a reasoning model that spent its one token thinking still
proves the route answers.

Only `401`, `403` and `404` retire a route outright, and only until the next pass. A `429` or a
timeout says "not now", which is what the per-route cooldown and the escalating backoff are already
for; standing a route down for a day over congestion would throw away a route that works again within
the hour. A retired route is skipped by `generate()` entirely, so the chain stops paying an upstream
call — and a daily slot — to be told the same thing it was told yesterday.

`400` is the fourth case and the one that needs care. Every narration request carries
`reasoning.enabled=false`, so a provider that *rejects* that parameter can never serve narration as
this bot calls it, and retiring it is right. But `400` is also what a provider with a minimum token
budget returns for `max_tokens=1`, which would be an artifact of the probe rather than a fault in the
route. So a `400` is confirmed with one ordinary-sized call before anything happens — same request,
same `REASONING_OFF`, only the token budget changed. A second `400` retires the route; a success means
the probe shape was at fault and the route is left alone. If the budget will not fund the
confirmation, nothing is retired: an unconfirmed `400` is not evidence.

The Google AI Studio route is never retired, whatever it answers. It is the operator's own key on its
own quota, outside OpenRouter's daily budget, and standing it down for a day would push every
narration back onto the ~50-a-day allowance it was added to escape. Its verdict is still recorded and
shown in `ai_status`, and the ordinary per-route cooldown still applies - it simply stays in the chain.

Three guards keep the diagnostic subordinate to play. The audit spends the shared budget it uses, so
the panel's gauge stays honest. It stands down entirely when less than half the daily budget is left:
narration is what the budget is for. And if *every* route fails durably in one pass, that reads as a
proxy, a firewall or a revoked key rather than a catalogue that emptied overnight — the verdicts are
kept for the panel, none are enforced, and `ai_status` says so in as many words.

What this deliberately does **not** do is judge quality. A route that answers a probe is known to be
reachable and nothing more; MiniMax M3 would have passed this every time. `_validate_generated_text`
remains the only judge of whether a reply is usable prose, and it still runs on every narration.
Choosing a *replacement* slug is likewise still the operator's call: an empty fallback slot stays
empty, because "it answered a ping" is not evidence that a model writes decent xianxia.

**A dashboard page for it.** `Systems -> AI Routing` shows the chains as they are actually ordered,
how many narrations were served by AI versus fell back to procedural prose, what the daily check
retired, whether the Google route is on, and a per-route table carrying attempts, successes,
scratchpad rejections, probe verdict, which upstream served it and whether it went via the operator's
own provider key. It reads through the bot control plane rather than SQLite, because none of that
lives in the database - it is the router's own in-process state. It is strictly read-only, so it
writes no `admin_audit_log` row, and it sits under Systems rather than Admin for the same reason: the
Admin group is for the two views that can change the world.

16 new tests in `test_ai_router_health.py`, 4 for the Google route's exemption, 4 for the dashboard
page. No schema change, no game-rule change, AI remains narration-only.


**0.28.0** corrects what a `400` is allowed to prove, and moves route selection into the dashboard.

The v0.27.0 audit read a `400` that repeated at `max_tokens=64` as proof that the route rejects
`REASONING_OFF`, and retired it. That inference does not hold. The probe differs from a narration
call in *two* ways at once — `max_tokens=1` and the `REASONING_OFF` body — and the confirmation
changed only the first, so a reasoning-mandatory endpoint, a context-length overflow, a malformed
body and a moderation block all reproduced identically. Every one of them retired the route.

`_classify_bad_request` now changes one variable per call. First an ordinary token budget with
`REASONING_OFF` still attached: if it answers, the `400` was the one-token probe hitting a provider
minimum — an artifact of the diagnostic, and the route is left alone. Then the same ordinary-budget
call with the `reasoning` object removed: if *that* answers, the parameter was the cause, and since
`REASONING_OFF` is on every narration request and is not negotiable, the route cannot serve
narration as this bot calls it and is retired until the next audit. A `400` that survives both is
not parameter-caused at all, and a cause that cannot be named is not a cause to act on: it is
recorded and ignored. Every path now records a verdict — `reasoning-rejected`, `token-budget`,
`other-status`, `unclassified`, `unconfirmed`, `no-reasoning-parameter` — surfaced as
`probe_400_class` so the panel says *why* a `400` was left alone.

The single-call version of this — drop `reasoning` and raise `max_tokens` together — is deliberately
not what shipped. It reintroduces the same conflation in the other direction, retiring a healthy
route whose only fault is a provider minimum on `max_tokens`.

Two fixes fell out of the same review. `google-genai` collapses *every* 4xx into a bare
`ClientError` — there is no `BadRequestError`, no `AuthenticationError` — and carries the number on
`.code`, not `status_code`. The probe read only the openai SDK's attribute, so a rejected AI Studio
key produced no status at all and was never retired: the exact case `PROBE_DURABLE_STATUSES` names
in its own comment. And a `200` with an empty `choices` array — how OpenRouter relays an upstream
failure without an HTTP status — recorded only "OpenRouter returned no choices", which is true and
identical for a provider outage, a moderation block, an upstream rate limit and a gateway
rejection. The `error` object beside the empty array is now read and reported, including whether
`metadata` was there at all: its absence means OpenRouter rejected the call itself and it never
reached a provider, which is a different problem to chase.

The other half of the release is the **Narration Routes** dashboard panel. Editing `.env` and
restarting the container was the only way to change a route, which fits badly with a catalogue that
moves without notice — `z-ai/glm-5.2:free` and `minimax/minimax-m3:free` both died in place as
shipped defaults. The five chain slots are now picked from OpenRouter's *live* free catalogue
(`GET /api/v1/models`, cached 15 minutes) rather than from a list kept in this repo, because a list
kept in this repo is how those two rotted. Beside the pickers is a read-only view of the daily
probe: per-route verdict, `probe_400_class`, attempts, cooldown and last error, so a GM can see why
a route is retired before choosing its replacement.

The write path crosses three processes and the ordering is the design. The dashboard writes through
the engine — `admin.narration.set_chain` stores the chain in `world_state` and an `admin_audit_log`
row in one transaction, following `admin.automation.set` closely enough to need **no schema
change** — and only then pokes the bot over the existing control channel to apply it live. The
engine write is what makes the choice durable and audited, so it happens first and independently: an
unreachable bot reports "stored, applies at next restart" rather than failing, because telling a GM
their change did not happen when it did invites them to make it twice. The bot also applies the
stored chain at startup, so `.env` is the baseline rather than the last word.
`OPENROUTER_REQUIRE_FREE` still applies — a dashboard is an easier place to pick a paid slug by
accident than a `.env` file, not a harder one — every slot is validated before any is assigned, and
a newly chosen route has its probe verdict cleared so it does not inherit the previous occupant's
retirement.

Also removed: the direct OpenAI narration provider (`NARRATOR_PROVIDER=openai`). It was a second,
paid path parallel to the OpenRouter chain, bypassing the free-tier limiter, the route audit and the
tiered fallback entirely — the one way to spend real money by editing a single line of `.env`.
Nothing in the shipped configuration used it. A `.env` still naming it now fails loudly at startup
rather than silently narrating procedurally. The `openai` package itself stays: it is the client
`AITaskRouter` points at OpenRouter's `base_url`, not a provider.

This sits beside the read-only `Systems -> AI Routing` page rather than replacing it: that one is
telemetry any GM can open, this one is the write half and lives under Admin because it changes what
every player reads. The Google route's exemption from retirement holds across the new 400 ladder as
well as 401/403/404 — the ladder declines to classify an AI Studio route on its own account, since
there is no reasoning parameter there to differentiate on.

38 new tests across `test_ai_router_health.py`, a new `test_narration_control.py` contract module
and four Go tests for the engine action. No schema change (still 32), no game-rule change, AI
remains narration-only.


**0.29.0** is the roadmap's **Hardened I** milestone: the doors. Five changes, none to gameplay, all
to what stands between the LAN and the world.

*The engine door fails closed.* Since the token was introduced, `Server.authorized` answered `true`
when `ENGINE_AUTH_TOKEN` was blank, so an engine started without one accepted every request.
`cmd/xianxia-core` refused to start without a token and `startup.sh` refused to start the stack,
which is why nobody met it in production - but a Server built any other way (a test, `go run` with
the variable unset, an embedder) stood open, and twelve of the package's own tests were quietly
running through that door. `server.New` now refuses a token shorter than 20 characters, `authorized`
denies when it somehow has none, and the Python side matches: `Settings.from_env` holds
`ENGINE_AUTH_TOKEN` to the rule `DASHBOARD_TOKEN` already had, so a bare-metal `python -m app.bot`
with no token stops at startup instead of sending unauthenticated requests. The dashboard checks it
too whenever `GAME_ENGINE_URL` is set.

*The dashboard door has a lock.* Basic Auth has no session; every request is a login attempt, and
until now the only cost of a wrong password was the round trip. `LoginThrottle` in
`app/ops/http_limits.py` counts failures per source address in a sliding window (five in five
minutes by default) and answers a locked address `429` with `Retry-After` for fifteen minutes
*before* its credentials are read, so a locked address learns nothing from its next guess. The table
is bounded at 4,096 addresses and prunes expired entries on every call, so an attacker rotating
sources pays for the cap, not the attack. Behind a reverse proxy the lock is per proxy, which the
README says plainly rather than trusting a forwarded-for header from an unknown peer. Alongside it,
a browser `POST` whose `Origin` does not match the `Host` it was sent to is refused
`403 origin_mismatch` - the `x-xianxia-admin` header already stopped a plain HTML form; this stops a
script on another origin from riding a logged-in tab. `DASHBOARD_ALLOWED_ORIGINS` exists for a proxy
that rewrites `Host`.

*Loopback by default outside Docker.* `DASHBOARD_HOST` and `HEALTH_HOST` defaulted to `0.0.0.0`,
which under compose was harmless (the dashboard port is published on `DASHBOARD_BIND_ADDRESS`, the
bot's health port is `expose`-only) and on bare metal offered Basic Auth in plaintext, `/metrics` and the
bot control channel to the LAN. The health port itself moves from 8080 to **8082**: on a QNAP, 8080
is the QTS web admin, and a bare-metal listener on all interfaces collided with it. `HEALTH_PORT`
still overrides it; compose pins 8082 inside the bot container beside the dashboard's
`BOT_CONTROL_URL` so an older `.env` cannot pull the two apart. Both now default to `127.0.0.1` when unset; `docker-compose.yml` sets `0.0.0.0`
explicitly on the two services whose neighbours need it, with the reason beside each line, and the
README carries a reverse-proxy recipe for LAN access with TLS. The shipped `.env.example` - which
`startup.sh` copies to `.env` on first run - sets `DASHBOARD_BIND_ADDRESS`, `DASHBOARD_HOST` and
`HEALTH_HOST` to `0.0.0.0` on purpose, because a headless NAS whose dashboard answers only itself
is not reachable from the PC that runs the browser; the change is that all-interfaces is now a
choice written in the file beside its reasons, not a default nobody chose.

*What ships is exactly what was reviewed.* `aiosqlite`, `python-dotenv` and `httpx` were ranges and
are pinned. `requirements.lock` (`make lock`, generated by uv) carries a SHA-256 for every wheel and
sdist the resolution touches, transitive dependencies included, and the Dockerfile installs it under
`--require-hashes`, so a package that changes on the index without a version bump fails the build
rather than shipping. `google-genai` stays unbounded in `requirements.txt` for the reason recorded
there, but the lock pins the version it was resolved against. All three base images are pinned by
digest as well as tag.

*`-race` in CI.* The v0.21.6 roadmap said the Go suite was green under the race detector; that was
a local run. `ci.yml` now runs it.

*`.env.example` is keys and separators only.* At 368 lines, 250 of them comments, the file an
operator copies to `.env` had become the manual; the operator asked for the manual to be a manual.
Every explanation moved, section for section, to `docs/CONFIGURATION.md`, and the file keeps the
keys, their defaults and the section rules, with its header naming where the words went. The tests
that gated prose in the file - the AI Studio key URLs, the arrow chain summaries, the loopback
alternative beside each bind key - now gate the reference instead, and a new one holds the file to
the rule so a comment cannot creep back in.

Worth recording: twelve Go tests in `internal/server` had been constructing engines with no token and
issuing requests with no header, passing only because the door was open. They now run under a token
supplied by `TestMain` and send it, and one of them - the restore-never-loses-a-write test - was
answered `401` the moment the door closed, which is the test doing its job.

Gate: `tests/python/contracts/test_security_defaults.py` and `TestNewRefusesAnEngineWithoutAUsableToken`.
No schema change (still 32), no game-rule change, AI remains narration-only.


**0.36.1** is a point release: **Greenriver Town joins the roads, and the engine playtest covers the
city.** No schema change.

*Greenriver Town has roads.* The starting town was the one settlement outside the road graph: it
could be reached only by direct travel, had no gates, and a fresh character's first road out was
the capital's. It now has roads to Azure Crown Imperial City (by its East Gate) and Riverguard City
(by its North Gate), so the first journey is a real one - out by the gate, along the road, in by the
gate facing it - and Old Hu's loop from Greenriver walks a road rather than a fixed four hours.

*The engine playtest walks the city.* `scripts/playtest_engine.py --launch` gained a tenth section
(ninety-six steps in all): a road journey from Riverguard to the capital that must end
at the gate facing the road, the walk to a district, `shop.here` from there, walking the city until
a shop is found, entering it, browsing, buying at the shelf price, selling what the keeper wants,
the door refusing to open anywhere but the street, the eight merchants on `merchant.status`, an
unsold lot at the capital's house taken by a merchant on the tick, the lot in that merchant's pack
as a floor find, and buying it back from him in his city. The run passed clean on the first full
pass of the new systems; the one failure it produced was the script's own (a lot of spirit herb
merges into Old Hu's wares line by design) and the lot is a curio now.

**0.36.0** is a feature release: **gates and districts.** No schema change.

*It matters which way you arrived.* Every walled city has a gate on each compass side that has a
road - the road graph of each world is laid out deterministically and both ends of a road agree on
the compass, so a road that leaves Riverguard City by its East Gate arrives at Azure Crown's West
Gate. A road journey now ends at the gate facing the road you came by, not in the centre, and the
travel reply says which gate you left by and what lies inside the walls. A direct journey, or a town
with no walls, lands where it always did.

*Bigger cities have districts.* Each capital has four compass districts behind its gates - the noble
quarter to the north, the temple quarter east, the lower town south, ministry row west - and every
other city has one district drawn from its terrain: forge terraces, herb gardens, blade yards, a
garrison ward, mist docks, a frost market, a ruin quarter, caravan yards, river landings, ore
terraces. A hundred and fifty-six gates and districts in all, each a location with four encounters
and sense hints, and each with its own people - a gate captain at every gate, two named NPCs in
every district, two hundred and fifteen in all - so a city is lively where you stand and only the
people of that part are in the scene. `/world → City → Look` shows the gates, what each faces, the
districts, where you are and who is here.

*A walk apart.* Inside the walls the gates, the districts, the centre and the shops are all a step
from each other: any part is walked to from any other, none needs discovering, and the picker offers
them the moment you are in the city. Leaving by road works from any part and goes by the gate facing
the first leg. A shop, an auction hall or a dwelling merchant is reached from any part of the city;
a shop door still opens onto the street. The Quest Forge sets quests in the city, not in its parts.

*The capitals charge more.* A capital's shops are a tier better than their world - tier two in the
Mortal capital, tier five in the Celestial - and a quarter dearer, buying and selling.

**0.35.0** is a feature release: **the city shops.** Schema **37**.

A city was one place: you arrived, you explored, you left. Every city now has its shops - a
hundred and four across the forty-eight cities, four in each capital and two everywhere else -
and they differ by city. The kind follows the city's character (Emberforge's smithy, Jadewood's
apothecary, Moonfen's talisman hall, Ashenwall's array workshop, Four-Roads' provisions,
Riverguard's beast hall), the tier follows the world (mortal-grade in the Mortal World up to
celestial-grade in the Celestial), and the shelf follows both: a tier-one smithy sells spirit-iron
swords of its own making and the ore; a tier-three one adds lamellar and beast cores. Every shop is
an interior location with a keeper NPC of its own - a want, a fear, a secret - four encounters and
sense hints, generated from the city's terrain and climate and held to the content gate like any
other place.

*Finding and entering.* A shop is found by walking the city: each `/world → Explore` in a city
has a fair chance of turning up one of its shops the player has not found, recorded as a location
discovery of kind `shop`, so `/travel` offers it and enters it - instantly, from the city's street
or from another shop of the same city - and the door opens back onto the street and nowhere else.
`/economy → City Shops → Here` says how many of the city's shops you have found and which kinds
remain. Inside, `Browse` shows the shelf (the keeper's own craft first, marked *made here*) and the
board of what the keeper buys; `Buy` and `Sell` trade against them, and what a shop also sells goes
straight back onto its shelf. The shelf refills to the content quantities on the shop's own clock
(twelve game hours), written by the next trade after it is due. Go owns all of it
(`shop_actions.go`); Python never writes `shop_state` or `shop_stock`. The Quest Forge leaves
shopfronts and keepers off its capped lists, and NPC life and world events do not wander into them.

**0.34.2** is a feature point release: **the merchant's own shop.** No schema change.

A merchant carried only what the auction floors could not sell, so a pack was empty until a lot
went unsold. Each merchant now keeps a shop of its own beside the floor finds: three to five lines
of ordinary tradeable goods in content (`wares` - Old Hu's herbs and pills, Madam Wen's talismans
and ink, Brother Lan's spirit iron and beast cores, Elder Fang's array disks), each with a quantity
and a price in the merchant's currency, stocked when the merchant is first seeded and restocked to
the content quantity every time it comes home. Every stock line now says which it is (`source`:
`wares` or `auction`); the status lists the shop first with 🛒 and the floor finds after with 🏮,
the buy reply says "from the shop" or "from the floor finds", and the item picker tags each line.
A floor find of an item the shop also sells takes the shop's price. Gate: the wares rule in
`test_world_content_gate.py` and the Go `TestAMerchantsOwnShopIsStockedAtSeedAndRestockedAtHome`.

**0.34.1** is a feature point release: **the playtest board and the travelling merchants.** Schema **36**.

*The playtest board.* A `#playtest` base channel beside `#bugs`, created by Setup/Repair like the
others and bound by the slash path or the dashboard. `/admin → Server → Playtest → Post` puts one
message per hub page in it, each pre-reacted ✅ ❌ 💡 - works, fails, change wanted - and testers
react and reply under the page with what they saw. `Report` tallies the reactions with the names of
who left them and links every flagged page, so the GM reads the changes wanted where they were
written rather than in a summary of them. The board stores message ids and nothing else
(`playtest_items`), and `Clear` empties it. Every one of the three is audited.

*Travelling merchants.* Eight merchants - two a world, each a named NPC with a want, a fear and a
secret - walk fixed loops of cities: Old Hu the Peddler from Greenriver Town round the Mortal
capitals, Madam Wen of the Silk Road, Brother Lan the jade trader and the rest. They are the auction
floor's last bidder: when a lot ends with no bid, a merchant whose loop passes the house's city and
whose purse covers the starting bid takes it at that price, the seller is paid, and the item goes
into the merchant's pack at a markup (never below base price). The pack is sold back to any player
who can reach the merchant: in the same city while it dwells there, or on the same stretch of road
while both are travelling it - `merchant.buy` is the one action the engine allows mid-journey, and
the travel reply names who is on the road ahead. `/economy → Merchants` shows every merchant's
whereabouts, pack and prices and who is within reach; the pickers on `Buy` list reachable merchants
first and the chosen merchant's stock. The tick walks them (`merchants` automation switch, on by
default; leg time from the road planner, four hours where the loop skips a road), the NPC moves with
the merchant so `/npcinfo` and the region panels agree, and a merchant's purchase reads **Struck to**
on the live auction card. Go owns all of it (`merchant_actions.go`); Python never writes
`merchant_state` or `merchant_stock`.

**0.34.0** is the roadmap's **Gameplay-complete II** milestone: the playtest, in the half of it a
machine can run, with the live half laid out for the person who can. No schema change.

*The engine half.* `scripts/playtest_engine.py --launch` builds and starts a scratch engine,
bootstraps it, and drives every loop the roadmap names through the engine's HTTP API the way the
bot's handlers do: two characters created from the family offers, `$ I explore`, a sect entrance
trial sat and the gift manual studied, a commission from Steward Qiao taken and turned in, another
abandoned and its cooldown felt, a third failed by the tick past its deadline, a live quest edited
under keep, migrate and revoke with a holder on it, a narration route stored and read back, a timed
mute enforced and undone, a lot listed, bid on and struck, a local floor refusing its seventh lot,
and a backup taken, listed and restored - one line per step, and a non-zero exit if any fails.

*What it found.* Two defects, both fixed here. A commission's last objective could never be turned
in: `quest.progress` wrote the row `completed` and then asked the commission resolution for an
`active` row, which refused and rolled the progress back - every commission since v0.24.0 could be
taken and worked but not finished. And the GM's Reset Cooldowns did not reset the sect trial's
retry wait, which lives on the last failed attempt rather than in the cooldowns table; a full reset
ages it out now. `TestACommissionCompletesThroughProgressAlone` and
`TestResetCooldownsClearsTheTrialRetryWait` pin both.

*The checklist.* `scripts/playtest_checklist.py` writes `docs/playtest/v<version>.md`: every hub,
page and action - 218 actions across sixteen hubs and the admin panel - with each parameter's
picker and each handler's acknowledgement filled from the tree, and three live columns (reachable
from the hub, error text actionable, narration or fallback fired) as checkboxes for the pass on
the live server. Regeneration keeps what was ticked.

*The punch list.* `docs/KNOWN_LIMITATIONS.md`: every finding fixed in a named release or deferred
with its reason, and nothing open without one of those words.

Gate: `tests/python/contracts/test_playtest_gate.py` (the punch list is honest, the checklist for
this release names every registered command and every hub, the engine script drives every loop
and the two findings have their Go tests).

**0.33.1** is a feature point release: **an auction house in every city, live auction channels, and
the main menu.** Schema **35**.

*An auction house in every city.* The Golden Pavilion, entered from Greenriver Town, was the only
auction house in the world. Every city has one now - forty-eight houses across the four worlds, each
a protected interior in its world's currency with its own steward, entered and left through the
same `auction.enter` / `auction.leave` doors, protection ending at them. A capital's house is
**grand** - the Azure Crown Treasure Exchange, the Spirit Jade Auction Pavilion, the Nine-Heavens
Treasure Hall and the Celestial Mandate Auction Hall, beside the Golden Pavilion - and takes
twenty-five lots at once for up to a day. A smaller city has a smaller house: a **local** floor
holds six lots at once and none for longer than six hours, and the engine refuses the seventh lot
or the seven-hour sale with the house's limit named and the capital's house suggested
(`max_active_lots`, `max_lot_minutes` in content; a house without them is uncapped). The floors are
written from their city's character - a riverside hall on stilts over the martial landings, a
furnace gallery cut into the forge terraces, a relic court on the old battlefield walls - each with
its own steward, want, fear and secret. The Quest Forge's prompt leaves the floors and their
stewards off its capped target lists - a floor is a door, not a destination - so the town a story
is set in is still offered; validation accepts them regardless.

*Live channels.* Beside the realm capitals the dashboard's Setup/Repair now creates the auction
channels (`channel_name` in content; the `/admin server realmhubs` path binds only, as it does for
the capitals): a grand house has a channel of its own, and the local floors of a world share one -
nine channels for forty-eight houses, not forty-eight - visible to the cultivators whose realm can
reach that world. A lot listed with `/economy → Auction House → Sell` is posted there the moment
the engine writes it;
every bid refreshes its card (current bid, high bidder - anonymous stays anonymous - next minimum,
closing time); and the tick that settles auctions (`finalizeAuctions`) is followed by the bot
striking the card SOLD, with hammer price and buyer, or Unsold. The feed (`app/bot/auction_feed.py`)
writes nothing but Discord message ids (`auction_lot_messages`): the lot is the engine's row, and a
missing card is never a missing lot. Teardown forgets the channels and cards with everything else;
the Discord Setup page lists the houses with their channel and open-lot count.

*The main menu.* Sixteen hub commands is a lot to remember. `/menu` opens one panel that lists every
hub - and Admin, for an administrator - and picking one opens that hub exactly as its own slash
command does. The hub commands remain.

Gate: `tests/python/contracts/test_live_auctions.py` (the engine write precedes the card; the feed
reaches no engine action and no gameplay table; settlement follows the tick; the channels are
dashboard-created and slash-bound; the menu lists every hub and gates admin) and
`test_world_content_gate.py` (a house per city, grand in every capital, local floors sharing one
channel per world, each a protected interior in a currency the world defines) and the Go
`TestALocalFloorHoldsSixLotsAndNoneLongerThanSixHours`.

**0.33.0** is the roadmap's **Gameplay-complete I** milestone: the pickers. No schema change.

*Every id has a picker.* Five parameters took a typed id with nothing to choose from - `/artifact
bond` and `awaken` (item), `/boss start` (boss), `/secretrealm enter` (realm), `/battle act`
(action) - and the walk that found them found three more beside them: `/caravan dispatch` (item),
`/provenance` (item) and `/reincarnate` (path). Each has a slash autocomplete now, which the hub
reads as its dropdown: bond offers what is carried with the bond it already has, awaken the bonds
still dormant with their resonance, boss every boss with the one at your location first and the
others' places named, secret realm the entrances open where you stand from the same engine query
the status line reads, caravan and provenance what is carried, reincarnate the world's paths.
`/battle act`'s action was prose all along and now says so on the parameter. The pickers that can
be empty explain themselves in hub terms - what to do first, and where. `/admin player grantstorage`
gained grade choices on the way.

*Typed play, roots with parameters.* `content/typed_play.json` covered roots without parameters,
the eight scene actions and `/talk`; `/travel` and `/use` fell to the picker. A root may now declare
one argument - a handler parameter and a source, `location` or `item` - and the router fills it from
the line against what the player knows or carries: `$ I travel to Greenriver Town`, `$ go to
greenriver`, `$ I drink a healing pill`. The rule is resolve_entities' - a full name, then one
distinctive token; the longest full name wins, a token two names share names neither. Unresolved,
the action is not offered and the empty picker says what it needed ("travel needs a place you
know"); the router never guesses a destination. Group leaves (`travel go`) are reached through the
registry by qualified name. "I go to" is a verb now, not throat-clearing, so it left the leading
phrases.

*`fate.adjust` removed.* Implemented in Go with no caller anywhere since v0.18, a player writing
their own Fate was never a feature; the GM path (`admin.player.fate`) remains.

Gate: `tests/python/contracts/test_hub_pickers.py` walks every registered command's `str`
parameters and requires each to be guided (choices, autocomplete, hub provider) or declared prose by
name - a new id parameter fails until its picker exists. `test_typed_play_router.py` pins the
argument rules. `test_world_content_gate.py` already required every location to have an NPC with
no exemption list, which is stricter than the roadmap asked; it stays so.

**0.32.0** is the roadmap's **Hardened II** milestone: moderation from Discord, with expiry; backups
that are bounded, sealed and copied off the box. Schema **34**.

*Moderation from Discord.* `admin.player.set_moderation` existed in the engine and was reachable only
from the dashboard, and nothing ever expired a mute. `/admin player mute|freeze` now take a duration
(`30m`, `2h`, `1d`, `1w`, `1h30m`; empty for until lifted) and a reason; `unmute`, `unfreeze`, `ban`
and `unban` sit beside them, and `forceendscene` joins `teleport` and `clearbattle` so every op a GM
needs live has a Discord door. The engine stores the expiry (`muted_until`, `frozen_until`) and a
ban is its own flag (`is_banned`) that blocks every authoritative action and never expires; lifting
it restores whatever mute or freeze it sat on. Expiry is read two ways on purpose: the check ahead
of every authoritative action treats a lapsed flag as over the moment the clock passes it, and the
simulation tick clears the row so the dashboard and `/admin player inspect` agree with it. The one
engine action writes the `admin_audit_log` row - actor, target, reason, all six columns before and
after - so `admin.audit.undo_last` reverses a mute from Discord exactly as it reverses one from the
dashboard, expiry and ban included. The dashboard's Moderation card gained the ban toggle, an
expires-after field and a standing line. Moderation remains what it was: a nudge on the engine's
dispatch layer, not anti-cheat.

*Backups.* `storage.BackupTo` was the whole story: every backup stayed forever, in the clear, on the
disk that holds the database. Retention (`XIANXIA_BACKUP_KEEP_DAILY` 14 / `KEEP_WEEKLY` 8) keeps
every backup from the newest N days that have one and the newest of each of the next M weeks,
counted among the backups that exist rather than against the calendar, and runs after every backup.
`XIANXIA_BACKUP_MAX_MB` sheds the oldest survivors to fit and never the newest. `XIANXIA_BACKUP_KEY`
seals every new backup with AES-256-GCM (`go_core/internal/backupcrypt`, PBKDF2 key, whole file in
one piece); restore opens it with the same key and refuses without it before anything is quiesced,
and `xianxia-engine decrypt-backup` opens one off the box. `update.sh` copies its pre-update backup
to `XIANXIA_OFFBOX_BACKUP_DIR` when set and can roll back from a sealed one. Only files the engine
itself named are ever pruned.

Gate: `tests/python/contracts/test_hardened_moderation.py` (every live GM op has a Discord
registration, the moderation commands audit, the engine owns expiry and the backup policy), Go
`TestExpireDueModerationsClearsOnlyLapsedFlags`, `TestModerationLapsedMuteNoLongerBlocks`,
`TestModerationBannedPlayerBlockedFromEverything`, `TestAdminUndoLastRestoresModerationExpiryAndBan`,
`TestRetainBackupsKeepsDailyWholeThenOnePerWeek`, `TestSizeCapShedsOldestButNeverTheNewest`,
`TestEncryptedBackupIsSealedListedAndRestorable`. AI remains narration-only.

**0.31.0** is the roadmap's **Narrator budget** milestone. No schema change. The routes were made
resilient in v0.26–v0.28; this release spends fewer calls on them, meters every door a player can
spend them through, and makes the procedural floor read well.

*Route by tier.* An exploration opening and a hunt result are decided by the engine, and the model
was spending a routine call describing each. They are procedural by default now: the pool describes
the scene at once, and a **Narrate it** button under the result asks the model only when the player
presses it - an explicit ask, metered on its own door. A GM who wants model prose on every such
scene turns on the new `ai_routine_narration` automation flag (`/admin → Simulation → Automation`),
and the button is not shown. The two sect narrations nothing called are deleted. Every live call
now declares why it is made - `dialogue`, `epic`, `narrate_it`, `forge`, `monitor` - and the router
counts them; the narrator counts the scenes it served procedurally by design, apart from the
fallbacks a model failed.

*Budget on every door.* The per-player bucket (`TYPED_PLAY_BURST` / `TYPED_PLAY_PER_MINUTE`) used to
meter typed lines only. `serialized_user_action` - every state-changing slash command and hub
button - spends the same bucket now and refuses with the same line, so a player cannot route around
it by changing doors, and the bucket reports grants and refusals per door. The per-player action
locks the wrapper takes are evicted once idle for half an hour, which closes the unbounded-growth
note the v0.23 roadmap carried.

*A fallback pool.* `content/world.json` carries `narration_pool`: seven scene kinds (an exploration
opening, a hunt won and lost, an action without a roll, an NPC's reply, a breakthrough made and
missed) by four world tiers (Mortal, Spiritual, Immortal, Celestial), three variants each, 84 in
all. `app/rules/narration_pool.py` picks one deterministically from the scene's seed - a retry does
not reshuffle what a player already read - and fills it; the tier is the location's world, or in a
private place the world the character's realm has reached. Every narrator fallback reads from it,
so the floor a player sees when the allowance is spent or every route is retired varies and fits
the place. A content test holds every cell filled and every line free of rewards.

*The ten-dollar switch.* OpenRouter's free allowance is 50 requests a day until ten dollars of
credit are on the account, then 1000. `OPENROUTER_CREDITS_TOPPED_UP` names the regime as a
baseline, and the dashboard's **Narration Routes** panel has the same switch: it is stored by the
engine beside the chain (`admin.narration.set_chain` accepts the key, so one save is one audit row)
and applied live through the router's `set_slots`, the way the routes are. An explicit
`OPENROUTER_MAX_REQUESTS_PER_DAY` still overrides both. The **AI Routing** page shows calls by
purpose, scenes served procedurally by design, which regime the allowance is in, and the
per-player refusals by door, so the effect of this release is visible where the routes already were.

Gate: `tests/python/contracts/test_narrator_budget.py` - no `_generate` call site outside dialogue,
the epic tier and the explicit-upgrade path runs by default, and the two procedural-first narrators
return before the model unless upgraded; the slash and hub path spends the bucket before it takes
the lock; the pool has three variants in every cell.

**0.30.1** makes the player property one home, built up. No schema change, no mechanical change
to any property that already exists.

`/abode establish` used to open with a choice of six archetypes - cave abode, alchemy estate,
spirit herb estate, spirit beast ranch, merchant pavilion, clan estate - each a preset of the same
nine facilities. A public sect already assigns each disciple an abode (the `sect_abodes` residence
behind `/sect abode`), so the cave abode was the same idea twice, and the estates were a choice
made at the door about a place the player then developed anyway. Now there is one shape: the
**homestead**, founded with a name and nothing else, as a cultivation chamber and a storeroom. The
herb garden, alchemy furnace, forge, formation core, beast pen, merchant hall and defensive
formation start at nothing and are built with `/abode → Upgrade` - level 0 to 1 is the build, at
the base cost, and each level after costs the square. `/abode status` and the founding reply list
what is not yet built so the next step is never a guess.

The six archetypes stay defined in the content, marked `"buildable": false`, so a property founded
before this release keeps its type, label and facilities; nothing is migrated. The engine holds the
rule, not only the picker: `abode.establish` founds the one buildable type when none is named and
refuses a retired or unknown one by name, where before an unknown type silently became a cave abode.
Gate: `property_types_test.go` (the buildable set, the bare founding, the refusals, a build and a
refused second upgrade that spends nothing) and `test_player_property_system.py`.

*The sect residence grows, and the homestead is earned.* The residence a public sect assigns
(`/sect abode`) was a thread and a door: a name from the disciple's rank, enter, leave, and no way
to seclude in it, because the seclusion site check knew founded properties, safe zones and a manor
at a world location and the residence is none of those. **Schema 33** gives `sect_abodes` the six
facilities a courtyard can hold - cultivation chamber, alchemy furnace, forge, formation core,
storeroom, herb garden - starting as a chamber and a storeroom like a homestead. `/sect → Abode →
Build or raise a facility` pays in **sect contribution points** (`sect.abode.upgrade`; the base
times the square of the level, 40/160/360… by default), and the sect holds two gates the content
names in `sect_abode_system`: each rank caps the level a facility may reach (Outer Disciple 1,
Inner 2, Core 3, Deacon 4, Elder 5, Grand Elder 6, Sect Master 8, Ancestor 9), and each level asks
a cultivation stage one realm below it. A refusal names the rank or stage it wants. Inside the
residence, seclusion uses its chamber (and the sect manor's array, since the residence stands at
the sect's seat), crafting uses its workshops and foraging its garden, for the disciple it belongs
to - a sect residence has no guest list.

With the residence a real starter home, founding a homestead of one's own is earned part-way up
the ladder: `abode.establish` asks standing in a public sect of **Deacon** (rank 40) or higher
(`abode_system.founding_rank_level`), and says so. An unaffiliated cultivator has the sect's
courtyard to earn first. Gate: `sect_abode_upgrade_test.go` (a build paid in points, the stage gate
and the rank cap refusing without spending, the unaffiliated and the unopened refused, seclusion
inside the residence at its chamber's rate, the founding gate at Deacon) and
`test_player_property_system.py` (the columns, the content, the gate's place on the ladder).

**0.30.0** is the roadmap's **Authority II** milestone: derived inputs, market pricing and the
Python DB layer. No schema change (still 32); no new content. Where Python did not mutate but
*computed the input* the engine then trusted, or kept a second copy of an engine rule, the copy is
gone and the engine computes.

*The engine derives the seclusion environment.* `seclusion.start` used to receive an
`environment_mult` the Discord handler had computed from the abode chamber level, the safe-zone
flag, the sect manor's qi array and any deployed formation, and the engine only clamped it. The
engine holds every one of those facts, so it derives the multiplier itself, enforces the
protected-site rule the handler used to enforce, and returns the parts (`environment`) and a
`projected_daily_gain` computed by the same helper `seclusion.settle` pays through - the rate has one
copy now. A payload that still carries `environment_mult` is refused, not clamped.
`app/rules/seclusion.py` is deleted with `manor_seclusion_multiplier` and `soul_legacy_modifiers`.

*Market pricing lives once.* Which items an ordinary market may stock is `worlddata.MarketTradeable`,
asked by the simulation bootstrap and by the new `market.catalog` query; the sell share is the
figure `market.trade` pays. `app/simulation/world.py` runs no SQL of any kind: the reads that stayed
there as raw SQL through a Go-hosted session - market rows and quotes, challengeable targets (with
the hidden-master filter), simulation state and lag against the engine's own clock, the
recent-action ledger, the NPC/sect/clan/region status panels - are twelve read-only engine queries
(`market.rows/quote/catalog`, `combat.targets`, `simulation.state/status`, `world.recent_actions`,
`civilization/npc/sect/clan.status`, `equipment.power`), each with a Go test.

*The DB layer writes only presentation.* The ~121 write statements in `app/database/core.py` were
sorted once, by method. Twenty-seven writers with no caller left in the tree - `add_items`,
`set_location`, `set_cooldown`, `activate_world_event`, `create_battle`, `apply_effect`,
`consume_item`, `set_sect_membership`, `set_gender`, `set_master` and their kin - are deleted with
their methods and the tests that only they served. The two rule computations the layer hosted
(`equipment_bonus` over `equipment_power`, the formation bonus) are the engine's `equipment.power`;
the address rule is applied beside its only caller in `app/bot` from a lineage snapshot; the
Python copy of pill-toxicity decay that `get_alchemy_state` applied on every read is gone, and
`/alchemy status` reads the settled figure from `effects.current`. The era and boss template merges
became rules helpers (`describe_era`, `boss_encounter_phase`) used by the presenters, so the layer
imports nothing from `app/rules`. What remains is thirty-eight writers, every one listed with the
tables it may touch in `PRESENTATION_WRITES`: schema bookkeeping and startup seeding, narration
history and RAG memory, Discord ids, the audit log, ops telemetry, and three reads that expire or
seed a row. One of them is named as still open: `get_world_clock` re-anchors `world_state` when
the configured scale changes and is a second copy of the clock arithmetic; it is on the roadmap's
remaining-authority list rather than quietly allowlisted.

*Dead rule code is deleted.* The eleven functions the roadmap named at zero callers went, and so
did everything only they or their tests reached: eighty-one functions and `World` methods in all
(the Python `roll_2d10`, `random_hunt`, `breakthrough_tn`, the secret-realm tables, the samsara and
aptitude generators, the sense checks, the manor maths). Tests count as callers of nothing.
`docs/V018_AUTHORITY_CLEANUP_ROADMAP.md` joins the V015/V016 lists under `docs/migration_history/`.

Gate: `test_authority_boundary.py` - every DB-layer writer is in `PRESENTATION_WRITES` with its
tables and nothing else may write; `database`, `simulation` and `ops` import no rules and the
dashboard keeps exactly two presentation imports (game-time formatting, quest-draft validation);
every public `app/rules` function and `World` method has a production caller; `seclusion.start`
and `forage.resolve` carry no derived input; no Python copy of the sell share or the tradeable
rule remains. Go: `authority2_test.go`.

**0.29.1** is a release-channel point release: one GitHub workflow instead of two. No gameplay
change, no schema change.

`ci.yml` and `release.yml` were two copies of the same check list. A `v*` tag ran ruff, pytest,
`go vet` and `go test` a second time in the second workflow - without the `gofmt` check and without
the container builds that stand in front of every pull request - and the archive was built from
whatever that second run happened to see. Now there is one workflow. Every push to `main` and every
pull request runs the `python`, `go` and `containers` jobs as before; a `v*` tag runs those same
three jobs and then a `release` job that `needs` all of them, so the archive is only ever built
from a commit CI has just proven on every check, and the checks run once. The file keeps the
`ci.yml` name so the README badge keeps resolving; the `contents: write` permission lives on the
release job alone and the workflow default stays `contents: read`. The release steps themselves
(the tag must match `VERSION`, `release_manifest.py --verify`, the zip and its `.sha256` sidecar,
notes from this file) are unchanged, and the archive now excludes `.claude/` beside the other
non-shipped trees.

Also in this release: `.claude/settings.json` allowlists the project's own check commands so a
Claude Code session does not stall on the tooling the repo asks for. It is development tooling,
not release content - the manifest script and the release zip both leave it out.

Gate: `WorkflowTests` in `tests/python/unit/test_release_channel.py` holds the workflows
directory to one file, requires each check to appear before the release job and not inside it,
and requires the release job to wait on all three.


## Release status — v0.36.1

- Current release: v0.36.1: Greenriver Town joins the roads (gates east to the capital and north to
  Riverguard), and the engine playtest walks the city - the gate, a district, a shop found, bought
  from and sold to, and a merchant taking an unsold lot and reselling it - ninety-six steps clean.
- v0.36.0: gates and districts - a road journey ends at the gate facing the road
  you came by, the capitals have four compass districts and every city one, each with its own
  people, and the capitals' shops are a tier better and dearer.
- v0.35.0 (schema 37): the city shops - a hundred and four across the forty-eight
  cities, differing by city in kind, tier and shelf, found by exploring the city, entered by
  travelling to them, with a keeper to buy from and sell to inside.
- v0.34.2: the merchant's own shop - three to five lines of ordinary goods per
  merchant in content, restocked every time it comes home, listed beside the floor finds.
- v0.34.1 (schema 36): the `#playtest` board - one message per hub page,
  testers react ✅ ❌ 💡 and the GM reads the tally - and the travelling merchants, who buy what an
  auction floor could not sell, walk fixed loops of cities and resell it to whoever meets them in a
  city or on the road.
- v0.34.0: Gameplay-complete II - the engine half of the playtest as a script,
  the checklist for the live half on file, the punch list, and the two defects the run found
  fixed (a commission could never be turned in; Reset Cooldowns missed the trial retry).
- v0.33.1 (schema 35): an auction house in every city - grand in the capitals,
  local and smaller elsewhere - live channels where lots are posted, bid on and struck as it
  happens, and `/menu`, one panel that opens any hub.
- v0.33.0: Gameplay-complete I - every id parameter has a picker, typed play
  fills one argument for `/travel` and `/use` from the line, and the callerless `fate.adjust` is gone.
- v0.32.0 (schema 34): Hardened II - mute, freeze and ban from Discord with a
  duration the engine expires, force-end-scene beside them, every moderation audited so undo covers
  it, and backups that are pruned, capped, sealed with an operator key and copied off the box.
- v0.31.0: the narrator budget - explore and hunt read from a procedural pool
  unless a player presses Narrate it, one per-player bucket meters every door, and the ten-dollar
  switch picks the 50 or 1000 a day allowance from the dashboard.
- v0.30.1 (schema 33): one home built up facility by facility - the sect
  residence grows with contribution points under rank and stage gates, and a homestead of one's own
  is founded at Deacon or higher.
- v0.30.0: Authority II - the engine derives the seclusion environment,
  market pricing and the world-status reads are engine queries, the DB layer writes only
  presentation tables, and eighty-one dead rule functions are gone.
- v0.29.1: one GitHub workflow - the release job runs behind the same CI
  checks every pull request gets, on the commit they just proved.
- v0.29.0: the doors fail closed - the engine refuses to run or answer without
  a token, the dashboard locks an address that keeps guessing and refuses cross-origin mutations,
  both listeners default to loopback outside Docker, dependencies are hash-locked and images
  digest-pinned, and CI runs the Go suite under `-race`.
- v0.28.0: a `400` no longer retires a route unless removing the reasoning
  parameter is what fixes it, and narration routes are chosen from the dashboard against
  OpenRouter's live free catalogue.
- v0.27.0: a daily one-token liveness check retires routes that answer
  401/403/404, so a withdrawn slug stops costing a free-tier slot per narration.
- v0.26.1: the withdrawn `z-ai/glm-5.2:free` hop is out of both chains, and a
  narration timeout is no longer misreported as a TLS/certificate failure.
- v0.26.0: an optional direct Google AI Studio route leads both chains when a key is
  set, outside OpenRouter's daily free budget.
- v0.25.4: the AI Studio key instructions say where to get the key, and the routine
  chain summary in `.env.example` matches the defaults under it.
- v0.25.3: MiniMax M3 is out of the routine chain; GLM 5.2 is the routine fallback
  as well as the epic one.
- v0.25.2: `.env.example` reorganised so the five required values are the first
  thing in it, and the first-run message names all five.
- v0.25.1: the typed-play prefix default is `$` rather than `>`.
- v0.25.0: the dashboard remade - 23 flat tabs become five grouped ones, and the
  worst page goes from eleven stacked tables to eleven tabs. No schema change.
- v0.24.0: the Quests workbench - one page for every definition a player can be
  given, an editor where there was none, and pinned terms so an edit cannot rewrite a deal a player
  already accepted. Schema 32.
- v0.23.2: the updater could not install v0.23.0 or v0.23.1 on a QNAP; the post-install manifest
  check used GNU-only checksum flags that BusyBox refuses.
- v0.23.1: eight logic errors from a second external review, each with a regression test verified
  against the defect.
- v0.23.0: Authority I closes - the last 21 Python writer methods are engine actions.
- v0.22.5: the engine drains in-flight requests on shutdown instead of cutting them,
  and closes storage only once the drain has finished.
- v0.22.4: a duel's preconditions are re-checked at accept and on every action, and
  a duel that stops being legitimate ends rather than erroring forever. Schema 31.
- v0.22.3: a restore can no longer lose a write it already acknowledged - a
  maintenance barrier quiesces traffic before the safety backup is taken.
- v0.22.2: the P0 review findings - an atomic simulation tick, idempotent duplicate
  authoritative requests, and quest rewards paid by the transaction that completes the quest.
- v0.22.1: the rest of the giver roster - two old men whose terms are undisclosed
  (one pays far above what he implies, one far below) and a disciples-only board for every public
  sect. Schema 30.
- v0.22.0: commissions - a giver NPC offers work in character, one at a time,
  with terms fixed at accept and four engine-owned outcomes in which failing and abandoning cost
  the same. Closes the last v0.21 authority row (`accept_quest`). Schema 29.
- v0.21.6: realm capitals are visible only while you stand in them - a presence
  role per capital the bot adds and removes by player location, with the channel permissions to match.
- v0.21.5: the narrator's input fence, the v0.25 content batch (every location
  has hints and encounters, every NPC has narrator fields), and cooldown errors as a wait.
- v0.21.4: every public sect has an authored tier-0 entry manual, and it is the
  one bestowed on joining.
- v0.21.3: one manual on joining a sect (engine-owned), and the 148-manual
  catalog materialised into `content/world.json` so Go and Python read the same content.
- v0.21.2: Teardown (delete everything the bot owns on Discord, from the
  dashboard, typed confirmation) and the realm-capital visibility gate actually gating.
- v0.21.1: typed play - `> action` lines route to existing handlers, speech is free,
  per-player budget on every line that can reach the engine or the narrator.
- v0.21.0: Authority I begins - the v0.21 gate (DB-write allowlist) and `item.use`.
- v0.20.9: the updater authenticates its backup call to the engine, aborts on a
  failed copy, and verifies the installed tree against the manifest before starting.
- v0.20.8: fixes `update.sh` dropping the `.sha256` sidecar asset when it
  wasn't the first asset on the release.
- v0.20.7: the two v0.20.6 builds merged - Quest Forge (schema 28) and the
  dashboard item-grant fix.
- v0.20.6: shipped twice from two sessions (build A: Quest Forge; build B: dashboard item grants).
- v0.20.5: the updater preflights `.env` before stopping anything.
- v0.20.4: release channel (GitHub Releases, bot announcement,
  `update.sh --fetch/--upgrade`) and the roadmap to v1.0.0.
- v0.20.3: test suite audited (70 → 59 files); dashboard gate path fixed.
- v0.20.2: top-level tidy-up; `RELEASE.txt` archived, deployment
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
- **Schema 28** added the Quest Forge definition table (`quest_definitions`).
- **Schema 37** added the city shops' shelves (`shop_state`, `shop_stock`) (v0.35.0).
- **Schema 36** added the playtest board (`playtest_channel_id`, `playtest_items`) and the travelling
  merchants (`merchant_state`, `merchant_stock`, `auctions.merchant_buyer`) (v0.34.1).
- **Schema 35** added the live-auction channel per house (`auction_house_channels`) and the card per
  open lot (`auction_lot_messages`), Discord ids only (v0.33.1).
- **Schema 34** added the moderation expiry pair (`muted_until`, `frozen_until`) and `is_banned` to
  `characters` (v0.32.0).
- **Schema 33** gave the sect residence (`sect_abodes`) its six facility levels (v0.30.1).
- **Schema 32** added `terms_json` to `character_quests`: the objectives and rewards each player
  accepted, so an edit to a definition cannot rewrite a deal that was already struck.
- **Schema 31** added `location` to `pvp_matches`, so a duel in progress knows where it is fought.
- **Schema 30** added the giver refinements: `requires_sect`, `reward_visibility` and `boast` on
  `quest_definitions`.
- **Schema 29** added commissions: giver, realm band, tier, owner, deadline, variants and seed on
  `quest_definitions`; the commission flag, absolute deadline, accepted variant and resolved minute
  on `character_quests`; and the refusal cooldown, per-outcome counters and last outcome on
  `npc_relationships`.

See `docs/V018_RELEASE_NOTES.md` and `docs/V019_RELEASE_NOTES.md` for the per-release detail.

## Release notes

See `docs/V019_RELEASE_NOTES.md` for the current release's cultivation-depth audit, dashboard coverage gaps, and
combat authority-migration fixes. See `docs/V018_RELEASE_NOTES.md` for the complete staged-authority, road/caravan,
setup, cleanup, migration, security, and upgrade summary that v0.19 builds on.
