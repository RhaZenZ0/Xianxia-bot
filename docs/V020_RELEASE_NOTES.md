# Xianxia RP Discord Bot — v0.20 release notes

Shipping as **v0.20.2**. The v0.19 line (v0.19 through v0.19.48) is in
`docs/V019_RELEASE_NOTES.md`; the staged-authority migration before it in
`docs/V018_RELEASE_NOTES.md`. The release is stamped 0.20.2 in `app/version.py`,
`VERSION`, the `Dockerfile` and `docker-compose.yml`, and carries schema 27,
unchanged since v0.19.29.

Release date: 2026-09-05 (v0.20.0, v0.20.1, v0.20.2).

## v0.20.0 — main.py split complete: phase 10, the final sweep

v0.20.0 closes `docs/MAIN_SPLIT_PLAN.md`. Sixteen releases (v0.19.33 through
v0.19.48) moved `app/bot/main.py` from 11,650 lines and 484 top-level
definitions into thirty-two modules under `app/bot/`; this release moves the
last of it and leaves `main.py` at 35 lines. No behaviour change, no schema
change. The minor-version bump marks the end of the decomposition, not a
gameplay change: a deployment that was on v0.19.48 upgrades exactly as any
v0.19.x point release did.

### What moved

- **`app/bot/surface.py`** (new, 517 lines) — the explicit Discord command
  surface: `_GROUP_ACTION_ROOTS`, `_MIGRATED_ROOTS`, `_ROOT_ACTIONS` and the
  import-time check that every migrated root registered, the sixteen hub
  definitions and their status providers, the `/admin` panel root,
  `on_app_command_error`, `register_command_surface()` and
  `register_event_handlers()`. Cut verbatim. It imports every command
  module directly — the twenty under `commands/` plus `admin/world_ops.py`
  and `admin/inspect_sim.py` for their registration side effect — so it is
  the one file that says what the bot is made of.
- **`app/bot/main.py`** is now the composition root the plan asked for:
  `logging.basicConfig`, `from .bot import XianxiaBot, bot`, the two wiring
  calls, `run()`. It imports exactly five names. `app/bot/__init__.py`
  still re-exports `XianxiaBot`, `bot`, `register_command_surface` and `run`
  from it, so `python -m app.bot` and every external import are unchanged.

### What the sweep dropped

`main.py` had accumulated imports nothing in it read - the rule for every
move was "prune only what the move orphaned, never reorganise while moving",
so they survived nine phases on purpose. The sweep is where they were
decided:

- Sixty-six unread imports (`app.aptitudes`, `app.creation_ui`,
  `app.sect_recruitment`, `app.sect_manor`, `app.inscription`,
  `app.advanced_runtime`, `app.game.World`, `GameEngineClient`,
  `Database`, the equipment hub-option providers, `register_hub_option_hint`,
  `PLAYER_PROPERTY_TYPES`, `_world_min_realm_index`, `admin_group`,
  `_admin_command_option_summary`, `log_admin_command_invocation`,
  `inventory`, ...). Every one was verified to have no reader in the wiring
  and no registration side effect that some other module does not already
  trigger (the phase-10 reachability guard below is what proves the second
  half).
- 646 blank lines left behind by the cuts.
- `_tribulation_currency` in `commands/cultivation.py`: no caller anywhere
  in the package since the Go engine took over tribulation costs; carried
  through phase 9a explicitly so that the decision would be made here.

### Guards

`surface.py` joins `MODULES` (name resolution, definition order, annotation
imports, relative-import targets). `Phase10SplitTests` (6): `main.py`
defines only `run`, imports exactly `SETTINGS`, `XianxiaBot`, `bot`,
`register_command_surface`, `register_event_handlers`, and ends with the
two wiring calls; `surface.py` owns the ten surface names and never imports
`main`; every module under `app/bot/` is reachable from `main.py` by
module-level imports (a command module nobody imports is a command that
silently vanishes - this is the guard the `# noqa` side-effect imports have
needed since phase 7); `surface.py` imports every `commands/*.py` directly;
the dead tribulation helper is gone; the package `__init__` still gets its
four names from `main`. Fourteen older guards of the form "main.py imports
X back" now read the wiring file through a single `WIRING` constant, and
`test_player_facing_command_hints.py` finds the hub tables with
`bot_module_defining("_HUB_DEFINITIONS")` instead of a pinned path. Six
mutants confirmed to fail: the `sense` side-effect import removed; the
`inspect_sim` side-effect import removed; `register_event_handlers()` call
dropped from `main.py`; `surface.py` taking the runtime from `main`; an
extra name imported into `main.py`; `aptitude_group` imported from the
wrong module.

### Verification

Go untouched. Python full suite against v0.19.48: 672 tests versus 666
(+6), identical failure set (the one stale-manifest failure fixed at
packaging and the nine `httpcore[asyncio]` sandbox errors that pass under
python3.11). `main.py`: 1,283 → 35 lines; 11,650 at v0.19.32.

### The split, end to end

| Phase | Release | Out of main.py |
|---|---|---|
| 1 | v0.19.33 | test scaffolding, package-wide scanners |
| 2 | v0.19.34 | `services.py`, `formatting.py` |
| 3 | v0.19.35 | `locations.py` |
| 4 | v0.19.39 | `channels.py`, `threads.py`, `discovery.py`, `character_state.py` |
| 5 | v0.19.40 | `admin/core.py` |
| 6 | v0.19.41 | `admin/channel_messages.py`, `bugs_forum.py`, `server_setup.py` |
| 7 | v0.19.42 | `admin/world_ops.py`, `inspect_sim.py`, `pickers.py` |
| 8 | v0.19.43 | `ui/event_scene.py`, `ui/creation.py`, `bot.py` |
| 9a–9e | v0.19.44–48 | the eighteen player-command domains under `commands/` |
| 10 | v0.20.0 | `surface.py`; `main.py` is the composition root |

Zero deferred `from ..main import` hooks remain (ten at the start); no
module below `main.py` imports it; the three back-edges the plan found
resolve through `EVENT_HANDLERS`, each binding living beside the panel it
names.

## v0.20.1 — app/ grouped into packages

The 43 flat modules beside `app/bot`, `app/database` and `app/simulation`
are now four packages, grouped by role. No module was renamed except the
two that would otherwise have been `app/dashboard/dashboard.py`; no
function moved between files; no behaviour change, no schema change.

| Package | Modules | Reads |
|---|---|---|
| `app/rules/` | advanced_catalog, advanced_runtime, alchemy, aptitudes, battle, birthfamily, black_market, creation_ui, effects, family, fate, game, inscription, npc_memory, progression_systems, quests, realm_hubs, samsara, seclusion, sect, sect_manor, sect_recruitment, sense, trade_receipt, worldtime | nothing above it; no Discord, HTTP or database import |
| `app/ops/` | config, core_services, game_engine, health, healthcheck, http_limits, operations, performance | nothing above it; not `rules` |
| `app/ai/` | ai_router, chat_monitor, narrator, narrator_context, rag | rules |
| `app/database/` | (as before) + **bootstrap** (was `app/database_bootstrap.py`) | rules |
| `app/dashboard/` | **server** (was `app/dashboard.py`), **contract** (was `app/dashboard_contract.py`), `__main__` | ops, rules, database |
| `app/version.py` | stays at the root: `app/__init__.py` exposes `__version__` from it | — |

`database_bootstrap` was first placed in `ops` and moved by the layering
guard: it imports `Database`, and `ops` sits below `database`.

### What changed for a deployment

- `docker-compose.yml`: the one-shot init runs `python -m app.database.bootstrap`
  (was `app.database_bootstrap`); the bot healthcheck runs
  `python -m app.ops.healthcheck` (was `app.healthcheck`), in the `Dockerfile`
  too. `python -m app.bot` and `python -m app.dashboard` are unchanged —
  `app/dashboard/__main__.py` starts the server.
- Nothing else. The two modules that locate the repository from their own
  path (`dashboard/server.py` for the static front-end and `content/`,
  `database/bootstrap.py` for `data/`) went one directory deeper and now
  use `parents[2]`; a guard resolves both against the tree.
- No compatibility stubs at the old paths (chosen at the time). Anything
  outside the repository that imported `app.<module>` needs the new path;
  the table above is the whole mapping.

### How it was done

One script: for every `ImportFrom`/`Import` under `app/`, `tests/` and
`scripts/`, resolve what it named, map it through the table, and re-emit it
relative to the importing file's *new* location — before moving the files,
so the resolution used the old tree. 84 files rewritten, then 36 moved. The
first attempt moved first and rewrote second, which resolved every
relative import in a moved file against its new directory and produced
lines like `from .server.http_limits import`; the tree was restored from
the v0.20.0 archive and the script reordered. Path strings in tests
(`"app" / "dashboard.py"`, `patch("app.samsara...")`) and comments were
updated by a second pass; the layout guard's last test is what found the
three the passes missed.

### Guards

`tests/python/unit/test_app_layout.py` (8): no flat module beside the
packages; each package holds exactly its modules and an `__init__`;
`app.dashboard` still starts as a module; the entrypoints the containers
run exist under the names the `Dockerfile`/compose file use; imports run
down the tiers only ({rules, ops} ← ai ← database ← simulation ← dashboard
← bot, and rules/ops never import each other); `rules` imports no Discord,
HTTP or database library; the two `__file__`-relative roots resolve to the
repository; and no source, test, script or container file names an old
path — as an import, as a path string, or inside a `patch("...")` string.
Six mutants confirmed to fail: `rules` importing `ai`; `ops` importing
`rules`; `parents[2]` → `parents[1]`; the compose file naming the old
bootstrap module; a `patch("app.samsara...")` string restored; a flat copy
of `fate.py` left beside the packages.

### Verification

Go untouched. Every module outside `app/bot` imports cleanly (with stubs
standing in for the three third-party libraries the sandbox lacks); the
bot package is covered by its own guards. Python full suite against
v0.20.0: 680 tests versus 672 (+8), identical failure set.

## v0.20.2 — top level: the release marker archived, the scripts stay

The second half of the tidy-up. The plan was to move `startup.sh`,
`stop.sh`, `update.sh` and `reset_database.sh` under `scripts/`; reading
`update.sh` first said no:

- the updater already installed on a NAS checks the *new* archive for
  `startup.sh` and `stop.sh` at its root and refuses the install otherwise
  (`for required in VERSION startup.sh stop.sh ...`);
- it runs from a detached copy and, at its commit point, replaces
  `$PROJECT_DIR/update.sh` — the root path — with the archive's copy;
- `reset_database.sh` calls `./stop.sh` and `./startup.sh` by root path.

So a release with the scripts moved could not be installed by the tool
that installs releases, and the fix inside the archive cannot reach the
installer that is refusing it (the same trap the v0.19.24 notes described
for a broken `update.sh`). The four scripts are the deployment's entry
points and stay at the root, where `sudo ./startup.sh` in the README
expects them. A guard in `test_app_layout.py` pins that, with the reason.

What did move: `RELEASE.txt`, a 57 KB release marker that still described
v0.19.24 and had not been updated since, is
`docs/migration_history/V019_24_RELEASE.txt` now. `VERSIONS.md` and the
per-line notes files are the release record; nothing read `RELEASE.txt`.

No behaviour change, no schema change. Full suite against v0.20.1: 681
tests versus 680 (+1), identical failure set.
