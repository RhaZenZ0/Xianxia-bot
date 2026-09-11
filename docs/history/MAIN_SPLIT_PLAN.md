# Splitting `app/bot/main.py`

## Progress

| Phase | Release | Status |
|---|---|---|
| 1 — test scaffolding | v0.19.33 | done |
| 2 — `services.py` + `formatting.py` | v0.19.34 | done (also removed the `roll_line` copies stage 4 had made in `beast.py`/`duel.py`) |
| 3 — `locations.py` | v0.19.35 | done (took `current_npc_location` with it, see below) |
| 4 — `channels.py`, `threads.py`, `discovery.py`, `character_state.py` | v0.19.39 | done — last deferred hook gone |
| 5 — `admin/core.py` | v0.19.40 | done |
| 6 — `admin/channel_messages.py`, `bugs_forum.py`, `server_setup.py` | v0.19.41 | done (`clear_managed_channel_messages` went to `server_setup`, see §4) |
| 7 — `admin/world_ops.py`, `inspect_sim.py` | v0.19.42 | done (40 of 43 admin commands out; `events`/`spawnrealm`/`closeevent` wait for phase 8; new `pickers.py`) |
| 8 — `ui/event_scene.py`, `ui/creation.py`, `bot.py` (+ the three `/admin world` event commands) | v0.19.43 | done — all 43 admin commands out; back-edges routed via `EVENT_HANDLERS` |
| 9 — command domains | 9a: v0.19.44, 9b: v0.19.45, 9c: v0.19.46, 9d: v0.19.47, 9e: v0.19.48 | done — 9a: aptitude, territory, secretrealm, cultivation (1,284 lines); 9b: character, economy, abode (1,553 lines; `usable_item_autocomplete` → `pickers.py`); 9c: exploration+craft+alchemy (852 lines, one module); 9d: battle + law (850 lines; `battle_panel` registry binding moved with the panel); 9e: scene + sense (1,056 lines; the two scene registry bindings moved with the panel helpers). No player command is defined in `main.py` any more; it is 1,283 lines of hub wiring, the `/admin` hub root, `register_event_handlers()` and the entrypoint |
| 10 — final sweep | v0.20.0 | done — `surface.py` holds the wiring; `main.py` is a 35-line composition root; unread imports and `_tribulation_currency` dropped; reachability guard added. **Plan complete.** |

Deferred `from ..main import` hooks remaining: **0** (down from 10). `ImportGraphTests.test_no_module_below_main_imports_it_anywhere` (test_bot_package.py) keeps it there.

Measured against v0.19.32: `main.py` is 11,650 lines, 484 top-level definitions (545 nodes
counting imports). The dependency graph below is scope-accurate (symtable). A grep- or
plain-AST-based scan produces false coupling — local variables named `world`, `run`,
`inventory` and a parameter named `begin` shadow module-level names — so the numbers here
supersede any earlier rough count.

## 1. The headline finding

**The file is already a DAG apart from five references.** Reading top-to-bottom, only five
places use a definition that appears later:

| Consumer | Uses | Where |
|---|---|---|
| `EventSceneView` | `_battle_panel` | line 1319, inside a callback |
| `EventSceneView` | `_scene_action_targets` | line 1221, inside a callback |
| `EventSceneView` | `scene_action_panel` | line 1222, inside a callback |
| `XianxiaBot.setup_hook` | `XianxiaInfoView` | line 2064, inside a method |
| `XianxiaBot._dashboard_discord_control` | `dashboard_discord_control` | line 2048, inside a method |

All five are inside function bodies and resolve at call time. Each is fixable with a
function-local import or by routing through the existing `EVENT_HANDLERS` registry, and
all five disappear on their own once the things they point at have moved into modules
that the consumer can import normally. There is no genuine import cycle to untangle. The
split is mechanical; the risk is entirely in the import-time machinery (§6).

## 2. What is in the file

| Kind | Count | Lines |
|---|---|---|
| Slash-command handlers (153 group + 40 root) | 193 | 4,787 |
| `discord.ui` views / modals / selects / buttons | 30 classes | ~1,070 |
| `XianxiaBot` | 1 class | 461 |
| Service singletons (`SIM`, `SCENES`, `COMBAT`, `QUESTS`, `EXPLORATION`, `NPC_RELATIONSHIPS`, `NARRATOR`, `NARRATOR_CONTEXT`, `NARRATOR_QUEUE`, `AI_ROUTER`, `ALERTS`, `GUILD`) | 12 | ~60 |
| Channel / thread / role plumbing | ~30 functions | ~570 |
| Server setup, channel messages, `#bugs` forum, info guide | ~25 functions + constants | ~750 |
| Dashboard → Discord bridge | 3 functions | ~370 |
| Autocompletes, choice lists, small formatters | ~60 | ~600 |
| Hub definitions + command-surface registration | | ~415 |
| Imports | 61 statements | ~150 |

Cut at natural boundaries, with every arrow pointing backward except the five above:

```
A_plumbing      153–1020    868 lines   singletons, channels, threads, discovery, small helpers
B_event_ui     1021–1539    519         event-scene views + thread spawning
C_creation     1540–2006    467         CharacterModal, birth-family views
D_botclass     2007–2475    469         XianxiaBot + bot instance
E_admin_setup  2476–4418  1,943         admin core, channel messages, bugs forum, server setup, dashboard bridge
F_player       4419–10335  5,917        all player commands (18 sub-domains)
G_admin_ops   10336–11191    856        admin player/sect/inspect/simulation commands
H_wiring      11192–11650    459        hub definitions, registration, entrypoint
```

The names most widely read across blocks are all in `A` or the admin core: `GUILD` (15
blocks), `roll_line` (21 consumers), `_resolve_text_channel` (17), `require_admin` (44),
`audit_admin` (27), `current_effect_modifiers` (12), `human_duration` (12), `SIM` (9 blocks).
Nothing in `A` or the admin core reads from any command block.

`F_player` breaks into 18 sub-domains with only **19** cross-domain edges — and 15 of those
19 point at the same six location helpers currently buried in the travel section. Hoisting
those six first makes most of `F` fall apart on its own.

| Sub-domain | Lines | Depends on |
|---|---|---|
| character_core (begin, gender, sheet) | 266 | — |
| aptitude | 255 | — |
| dashboard / quests / inventory | 178 | — |
| cultivation (cultivate, seclusion, breakthrough, body) | 535 | — |
| exploration + craft + alchemy | 679 | — |
| travel + realmhub | 252 | — |
| talk + scene actions | 538 | travel (1) |
| perception + world info | 421 | travel (7) |
| perfection + secret realm | 306 | — |
| items + storage | 287 | travel (1) |
| auction | 110 | — |
| battle | 463 | — |
| law + manual | 233 | battle (3) |
| condition + tribulation + crime | 305 | — |
| territory / war / caravan / party | 227 | travel (1) |
| abode + array + inner world | 284 | items_storage (1) |
| civilization + market + black market | 289 | travel (5) |
| samsara + fate + bond | 289 | — |

## 3. Why the order matters

Every module that has left `main.py` so far carries call-time `from ..main import …`
hooks inside functions: `family.py` has four, `sect.py` six. The stage-4 modules
(`equipment`, `boss`, `duel`, `beast`, `artifact`, `formation`) avoided them only because
they happen to talk to the engine alone. The hooks exist because the shared pieces —
singletons, channel/thread helpers — still sit *above* the commands in `main.py`, which
imports the command modules at module level. Move the shared layer down first and every
later command move needs zero deferred imports; the ten existing hooks get deleted as a
side effect.

## 4. Target layout

Following the convention already set by `runtime.py`, `hubs.py`, `registry.py`, `commands/`.

**Shared layer** (no command surface, imported by everything; imports `runtime`, never `main`):

- `bot/services.py` — the 12 singletons and the player-property constants (~90). Kept
  separate from `runtime.py` on purpose: constructing `NARRATOR`/`AI_ROUTER`/`SIM` pulls in
  `app.narrator`, `app.ai_router`, `app.simulation`, and `runtime.py` staying light is why
  the package has no cycles.
- `bot/formatting.py` — `roll_line`, `human_duration`, `player_property_emoji`,
  `player_property_facility_lines`, `effective_attribute` (~60)
- `bot/channels.py` — `_resolve_text_channel`, `_get_thread`, `post_server_log`, the
  `configured_*_channel` accessors, `ensure_realm_hub_channels`, `_ensure_realm_access_roles` (~160)
- `bot/threads.py` — expedition / abode / household / sect-abode thread management (~360)
- `bot/discovery.py` — location discovery images and embeds (~72)
- `bot/character_state.py` — `current_effect_modifiers`, `sync_pill_toxicity_effect`,
  `settle_all_seclusions`, `_remember_freeform_npc_scene` (~115)
- `bot/pickers.py` — (added in phase 7) shared autocomplete/option providers that need
  `services` as well as `runtime`: `auction_currency_autocomplete`, `_market_item_matches`.
  Named as decorator arguments in two modules each, so they must live below both.
- `bot/locations.py` — **the highest-leverage extraction**: `_known_locations`,
  `_world_is_unlocked`, `_world_min_realm_index`, `_location_is_visible`,
  `location_autocomplete`, `local_npc_autocomplete`, and `current_npc_location` (moved here in
  phase 3 rather than to `character_state.py`: it is `local_npc_autocomplete`'s one dependency
  and reads only `SIM`/`WORLD`). (`usable_item_autocomplete` is an item picker, not a location
  helper — it goes to `commands/items.py`.)

**Admin layer:**

- `bot/admin/core.py` — `require_admin`, `audit_admin`, `log_admin_command_invocation`,
  the `admin_*_group` objects (~253)
- `bot/admin/channel_messages.py` — channel-message state machine, base channel specs,
  info guide, `XianxiaInfoView` (~450). `clear_managed_channel_messages` sits with this
  code in `main.py` but reads `_run_complete_server_setup`, so putting it here would make
  this module import `server_setup`, which imports this module — it went to `server_setup`
  instead (its only caller is `dashboard_discord_control`, which is there too).
- `bot/admin/bugs_forum.py` — bugs forum channel and tags (~170)
- `bot/admin/server_setup.py` — server setup, permission/configuration reports,
  `_run_complete_server_setup`, the dashboard bridge (`_dashboard_discord_snapshot`,
  `dashboard_discord_control`) and the `/admin server` commands (~1,044)
- `bot/admin/world_ops.py` — `/admin world`, `/admin player` karma/grant/grantstorage,
  `/admin sect`, maintenance (~405)
- `bot/admin/inspect_sim.py` — inspect, teleport, revive, clearbattle, family/npc inspect,
  `/admin simulation`, backup (~451)

**UI layer:** `bot/ui/event_scene.py` (519), `bot/ui/creation.py` (467). Battle, scene-action
and dashboard views stay with their command modules.

**Command layer:** `bot/commands/<domain>.py`, one per sub-domain in the §2 table, joining
the eight already there.

**Bot class:** `bot/bot.py` — `XianxiaBot` + the `bot` instance. Its two forward references
(`dashboard_discord_control`, `XianxiaInfoView`) are resolved by phase 6, so it moves in
phase 8. Keeping it in `main.py` is defensible as a deferral but not as a finished state:
"composition root and nothing else" cannot include a 461-line class.

**`main.py` at the end** (~150 lines): `logging.basicConfig`, the imports (every command
module, for their registration side effects), `bot`, `_HUB_DEFINITIONS`/`_ROOT_ACTIONS`
wiring or a `surface.py` holding it, `register_command_surface`, `register_event_handlers`,
`on_app_command_error`, `run()`. `app/bot/__init__.py` keeps re-exporting `XianxiaBot`,
`bot`, `register_command_surface`, `run` from `main`, so `main` re-imports whatever moved.

## 5. Phases

One release per phase, same standard as stages 2–4: measure the block's free-name
footprint before moving, preserve definition order verbatim, extend `MODULES`, full
baseline diff, manifest regenerated. Phases 9 can be split across several releases;
"one domain per commit" inside a release is fine, but the release is the verification unit.

1. **Test scaffolding (no code moves).** (a) Convert every `main.py`-pinned scanner in §6.4
   to a package-wide scan. (b) Add the six stage-4 modules to `MODULES` — they have had no
   definition-order, name-resolution or annotation guard since they were created. (c) Add
   a `tests/support.py` helper `bot_function_source(name)` that finds `async def <name>(`
   anywhere under `app/bot/`, so locator tests survive later moves without edits.
2. **`services.py` + `formatting.py`.** Pure leaves. Deletes the `SIM` hooks in `family.py`
   and `sect.py` (three of the ten).
3. **`locations.py`.** Removes 15 of the 19 intra-`F` edges before `F` is touched. Deletes
   `sect.py`'s `_known_locations` hook.
4. **`channels.py`, `threads.py`, `discovery.py`, `character_state.py`.** Rest of `A`.
   Deletes the remaining six hooks; `family.py`/`sect.py` no longer import `main` at all.
5. **`admin/core.py`.** 85 of the `G→E` edges are just `require_admin`/`audit_admin`/the
   group objects.
6. **`admin/channel_messages.py`, `admin/bugs_forum.py`, `admin/server_setup.py`.**
7. **`admin/world_ops.py`, `admin/inspect_sim.py`.** All 43 `/admin` leaves out.
8. **`ui/event_scene.py`, `ui/creation.py`, `bot.py`.** The three `EventSceneView` back-edges
   resolve as normal imports of `commands/battle.py`/`commands/scene.py` once those exist,
   or through `EVENT_HANDLERS` until then.
9. **Command domains**, easiest first: auction (110) → dashboard/quests/inventory (178) →
   territory/party (227) → law+manual (233, after battle) → … → exploration+craft (679).
10. **Final sweep:** `surface.py` if wanted; `main.py` reduced to the composition root.

## 6. Risks and rules (each has already cost a failed deploy)

**1. `serialized_user_action` and deferred annotations.** The file uses
`from __future__ import annotations`, so `discord.utils.resolve_annotation` evals annotation
*strings* against `callback.__globals__` — for a wrapped callback that is `runtime.py`, not
the module defining the command. Every annotation type used by a decorated handler must be
importable in `runtime.py` (v0.19.13). Ruff will call such imports unused; they are not.
`WrappingDecoratorAnnotationTests` enforces this — only for modules in `MODULES`.

**2. Definition order.** Stage 1's first cut of `runtime.py` had `DB = Database(ROOT / ...)`
above `ROOT = Path(...)` - fine as a set of names, `NameError` on import (v0.19.12; the
`DefinitionOrderTests` docstring records it). `DefinitionOrderTests` checks it — only for modules
in `MODULES`. Extend the list in the same release that creates the module.

**3. Import-time registration.** `register_event_handlers()` and
`register_command_surface(bot)` run at import; `ACTIONS.bind` raises on duplicates;
`_missing_action_roots` raises if `_MIGRATED_ROOTS` loses an entry. That is a good safety
net — do not weaken it. Two consequences: `main.py` must keep importing every command
module for side effects, and every handler `register_event_handlers()` names must be
imported into `main.py` — this is exactly what stage 4 missed (`boss_status`,
`hunter_status`, `formation_status` → `NameError` at startup, fixed in v0.19.32).
Autocomplete and `choices` references also evaluate at decoration time, so a shared
autocomplete lives in the lower module that owns its data, never behind a deferred import.

**4. Tests anchored to `main.py`.** These read `main.py` by path and slice it with
`.index()`/`.count()`. They do not fail when guarded code moves out — they silently stop
guarding it. Convert them to package-wide scans in phase 1:

```
tests/python/unit/test_project.py
tests/python/unit/test_gui_integrity.py
tests/python/unit/test_command_cleanup.py          (the ephemeral=True allowlist)
tests/python/unit/test_character_creation_ui.py
tests/python/unit/test_channel_message_state.py
tests/python/unit/test_hub_layout_rollout.py
tests/python/unit/test_location_discovery_images.py
tests/python/unit/test_bugslayer_reward.py          (locator: admin_grant in main.py)
tests/python/integration/test_world_access_scene_action.py
tests/python/integration/test_core_services.py
tests/python/contracts/test_scene_action_surface.py
tests/python/contracts/test_admin_server_setup_tool.py
```

Already package-wide, no change needed: `test_authority_boundary.py`,
`test_chat_monitor_contract.py`, `test_player_facing_command_hints.py`,
`test_private_location_exits.py` (which documents going red the day `family` moved),
and `test_engine_result_keys.py`'s handler scan (its `EquipmentOptionTests` was pinned
until v0.19.32).

**5. No reorganising while moving.** Stage 4 renamed a local (`row`→`result`) and reflowed
`register_hub_option_hint(` calls in the same change as the move; both produced test
failures unrelated to the move. Each phase is cut-and-paste with imports fixed, so the
review diff reads "same code, new file". Style passes are their own releases.

**6. `RELEASE_MANIFEST.sha256`.** Regenerate via `scripts/release_manifest.py --write`
every phase; `test_release_manifest` fails otherwise.

## 7. Verification per phase

`make check` (ruff E9 + pytest + Go). The guard that matters most is
`tests/python/unit/test_bot_package.py` (named `test_bot_module_split.py` until v0.20.3): the bot cannot be imported in the sandbox (no
discord.py), so its symtable-based checks are the only thing standing between a bad move and
a `NameError` at startup — and they only cover modules in `MODULES`. Each phase also adds
its own `<Stage>SplitTests` class in the style of `FamilySplitTests`/`SectSplitTests`
(module exists and owns its group(s); `main.py` no longer defines the moved names; deferred
`main` imports counted, and driven to zero by phase 4; `main.py` shrank by roughly the
block), and updates the deferred-import counts in the earlier classes as hooks are deleted.
