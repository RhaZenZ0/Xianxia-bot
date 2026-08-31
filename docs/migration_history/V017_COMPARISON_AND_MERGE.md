# v0.15 → v0.16 Functional Comparison and v0.17 Merge

## Executive summary

v0.16 is a mixed authority-hardening release. It correctly moves several remaining outcome/RNG paths into Go, but it also reintroduces Python-owned canonical SQL mutation paths that v0.15 had already removed.

v0.17 keeps the v0.16 improvements while restoring the stronger v0.15 authority boundary.

### Release-stage verdict

- **v0.15:** release-ready Go-authority migration checkpoint.
- **v0.16:** release-ready RNG/mechanics hardening checkpoint with authority regressions.
- **v0.17 recommended:** consolidation of the strongest behavior from both.

No schema migration is required; schema remains **21**.

## Archive comparison

After excluding `__pycache__`, `.pytest_cache`, and `.pyc` artifacts:

| Metric | v0.15 | v0.16 |
|---|---:|---:|
| Meaningful files | 177 | 177 |
| Shared paths | 166 | 166 |
| Byte-identical shared files | 125 | 125 |
| Changed shared files | 41 | 41 |
| Release-only paths | 11 | 11 |
| Python tests | 236 passed + 78 subtests | 222 passed + 39 subtests |
| Go tests | pass | pass |

The raw v0.15 archive contains additional generated cache files; those are not source/content differences.

## Complete runtime/configuration change ledger

| Area / file | v0.15 → v0.16 functional change | Classification | v0.17 decision |
|---|---|---|---|
| `app/birthfamily.py` | Deletes Python `inherited_root()` RNG implementation. | **Improvement** | Keep deletion. |
| `app/bot/main.py` – forage | Stops Python from selecting forage TN, regional resources, loot, and rare result; sends contextual inputs to Go. | **Improvement** | Keep v0.16 path. |
| `go_core/internal/game/crafting_actions.go` | Go calculates region resources, world tier, TN, common quantity, rare pool/chance/RNG, roll, loot mutation, and profession progress; rejects client `tn`, `spirit_resources`, `loot`, `rare_found`. | **Improvement** | Keep. |
| `app/bot/main.py` – child | Stops calculating/sending child spiritual root/realm/phase. | **Improvement** | Keep. |
| `go_core/internal/game/family_dao_actions.go` | Go selects inherited root and lifespan roll; rejects client `spiritual_root`, `realm_index`, `phase`. | **Improvement** | Keep. |
| `app/bot/main.py` – caravan | Changes settlement to `authoritative_action()` with a Discord action id and explicit `GameEngineError` handling. | **Improvement** | Keep. |
| `app/game_engine.py` | Bootstrap API shrinks from `game_minute + npcs + sects` to `game_minute`. | **Improvement in API shape** | Keep the small request, but make Go load canonical world catalog itself. |
| `app/simulation/world.py` – constructor | `engine` becomes a required keyword argument instead of optional. | **Improvement** | Keep. |
| `app/simulation/world.py` – bootstrap | Reintroduces direct Python SQL writes for simulation systems, civilization regions, NPC civilization/mind/life, sect politics/factions/relations, and economy markets, then invokes Go only for mood/clan completion. | **Major regression** | Restore full Go bootstrap. Python sends only `game_minute`. |
| `go_core/internal/simulation/bootstrap.go` | Removes v0.15 full simulation bootstrap writes and narrows Go bootstrap to pending moods/clans. | **Major regression despite RNG improvements** | Recombine v0.15 full bootstrap with v0.16 Go RNG/clan logic. |
| `app/simulation/world.py` – interval | Replaces Go `admin.simulation.interval` mutation with direct Python SQL. | **Major regression** | Restore Go operation. |
| `app/database/core.py` + `app/bot/main.py` – karma | Reintroduces `Database.adjust_karma()` and routes admin command away from Go. | **Major regression** | Remove Python mutator; restore `admin.player.karma`. |
| same – currency | Reintroduces `Database.add_currency()` and routes grant away from Go. | **Major regression** | Remove; restore `admin.player.grant_currency`. |
| same – world clock | Reintroduces `Database.advance_world_clock()` and routes time advance away from Go. | **Major regression** | Remove; restore `admin.world.advance_time`. |
| same – teleport | Reintroduces `Database.admin_teleport_character()`. | **Major regression** | Remove; restore `admin.player.teleport`. |
| same – revive | Reintroduces `Database.admin_revive_character()`. | **Major regression** | Remove; restore `admin.player.revive`. |
| same – clear battle | Reintroduces `Database.admin_clear_battle()`. | **Major regression** | Remove; restore `admin.player.clear_battle`. |
| same – automation | Reintroduces `Database.set_automation_setting()`. | **Major regression** | Remove; restore `admin.automation.set`. |
| `app/database/core.py` – black market | Deletes unused `rotate_black_market()`. | **Improvement / dead-code purge** | Keep deleted. |
| `app/database/core.py` – auction risk | Deletes unused `get_pending_auction_door_risks()`. | **Improvement / dead-code purge** | Keep deleted. |
| `app/simulation/world.py` – forage helper | Deletes Python `alchemy_forage_profile()` and its `secrets` RNG. | **Improvement** | Keep deleted. |
| `app/simulation/world.py` – NPC mood | Deletes Python `_npc_mood()` and `secrets.choice`. | **Improvement** | Keep deleted; use Go. |
| `go_core/internal/simulation/world.go` | Adds NPC role/personality to the Go simulation catalog. | **Improvement** | Extend further in v0.17 so Go can bootstrap canonical NPC/sect state from `world.json`. |
| `go_core/internal/simulation/bootstrap.go` – mood | Uses Go `gamerng` for pending NPC moods and activity-sensitive mood options. | **Improvement** | Keep. |
| same – clans | Uses Go RNG for branch stats, retainer roles, and clan relations; fills retainer target exactly. | **Improvement** | Keep exact allocation and RNG. |
| same – branch names | v0.16 has only five branch labels and reuses `"Distant Branch"` when more branches are needed. | **Regression** | Restore unique cadet labels through the supported branch count. |
| v0.16 Python NPC bootstrap age | Uses rich Xianxia lifespan-based `initial_age_years()`, while current Go death simulation still uses a much smaller realm-adjusted threshold. High-realm NPCs can therefore start above the Go death threshold. | **Cross-model regression** | Keep v0.15's conservative Go bootstrap age until lifespan/death models are unified. |
| `go_core/internal/server/server.go` | Removes bootstrap `http.MaxBytesReader`; returns bootstrap counters instead of only `{"status":"ok"}`. | **Mixed** | Keep result counters; restore a 1 MiB request cap. |
| `.env.example`, `Dockerfile`, `VERSION`, `app/version.py`, `docker-compose.yml`, `update.sh` | Release marker 0.15 → 0.16. | **Expected metadata** | Promote to 0.17. |
| `README.md` | Updates release description/status; contains a stale “No Ollama ... v0.14” line inherited/introduced in the edit and mixed authority claims. | **Documentation issue** | Normalize current release text and schema 21 wording. |
| `app/core_services.py` | Whitespace-only difference. | **No functional change** | No behavioral merge needed. |

## Authority regressions in v0.16

### 1. Python regained full simulation-bootstrap SQL ownership

v0.15 delegated simulation bootstrap to Go. v0.16's `WorldSimulator.initialize()` directly writes canonical state tables before calling Go.

Affected tables include:

- `world_simulation_state`
- `civilization_regions`
- `npc_civilization_state`
- `npc_mind_state`
- `npc_life_state`
- `sect_politics_state`
- `sect_factions`
- `sect_relations`
- `economy_markets`

This contradicts the intended one-way authority boundary.

### 2. Python regained simulation-interval mutation

v0.15 used `admin.simulation.interval`. v0.16 directly updates `world_simulation_state.interval_game_minutes`.

### 3. Seven supported GM/admin mutations moved back to Python SQL

The v0.16 bot uses new/reintroduced `Database` mutation methods for karma, currency, time advance, teleport, revive, battle clearing, and automation settings. v0.15 already had Go operations for all seven.

### 4. Boundary tests weakened around exactly those paths

v0.15 explicitly asserted that the deleted admin mutators were absent and that simulation bootstrap/interval code could not call `._connect()`. v0.16 removed those guards while adding good forage/child authority checks. The removed guards allowed the authority regressions above to return.

### 5. Coverage decreased

v0.16 removed multiple older integration suites. Some removals were stale tests for deleted Python authority APIs, but several sect/content/observability-style tests represented real coverage. v0.17 restores compatible useful suites and keeps the new v0.16 boundary tests.

## v0.17 merge design

v0.17 starts from v0.16 so it retains the newer Go mechanics, then restores the stronger v0.15 authority paths.

### Kept from v0.16

- Go-owned forage profile/outcome RNG and forbidden payload fields.
- Go-owned child root inheritance and forbidden payload fields.
- Go-owned NPC mood RNG.
- Go-owned clan branch/retainer/relation RNG.
- Exact retainer allocation.
- Caravan settlement action idempotency/error handling.
- Mandatory `WorldSimulator` engine injection.
- Dead Python black-market and auction-risk helpers remain removed.
- Small bootstrap client request (`game_minute` only).

### Restored or strengthened from v0.15

- Full canonical simulation bootstrap is Go-owned.
- Simulation interval mutation is Go-owned.
- Seven supported Discord GM/admin mutation paths are Go-owned.
- Strict Python authority-boundary tests return.
- Useful compatible integration tests return.
- Go caravan authority test returns.
- Full bootstrap Go test coverage returns.
- Request-size protection returns.

### New v0.17 synthesis

- Go's simulation catalog now loads the NPC/sect/location/realm metadata required for full bootstrap directly from canonical `content/world.json`.
- The bootstrap request no longer accepts caller-provided NPC/sect maps.
- Full bootstrap, mood initialization, and clan initialization run in one Go transaction.
- Bootstrap returns counters for systems, regions, NPCs, sect states, markets, moods, clan branches, retainer groups, and relations.
- Clan branch names remain unique rather than repeating `Distant Branch`.
- Conservative NPC bootstrap ages avoid the current lifespan/death-model mismatch.
- Bootstrap HTTP body is capped at 1 MiB.
- Current docs are versioned as v0.17; v0.15/v0.16 release audits live under `docs/migration_history/`.

## Test-file delta notes

v0.16 adds `tests/python/contracts/test_simulation_boundary.py`, which is retained.

v0.16 removes these v0.15 test files:

- `tests/python/integration/test_advanced_forward_port.py`
- `tests/python/integration/test_completed_advanced_systems.py`
- `tests/python/integration/test_connected_systems.py`
- `tests/python/integration/test_missing_systems.py` (contained no tests)
- `tests/python/integration/test_sect_manor.py`
- `tests/python/integration/test_sect_recruitment.py`
- `go_core/internal/game/caravan_authority_test.go`
- `go_core/internal/simulation/bootstrap_test.go`

v0.17 restores the compatible useful tests while retaining v0.16's new authority tests.

## v0.17 validation

Validation performed on the assembled tree:

| Check | Result |
|---|---|
| `python -m pytest -q` | **237 passed, 78 subtests passed** |
| `go test ./...` | **PASS** |
| `go vet ./...` | **PASS** |
| Python source/test compile | **PASS** |
| `bash -n startup.sh stop.sh update.sh` | **PASS** |
| `content/world.json` JSON parse | **PASS** |
| Schema | **21**, no migration |

## Remaining design gaps

These are not v0.15→v0.16 regressions, but are sensible future hardening targets:

1. `forage.resolve` still accepts caller-provided context/mechanical inputs such as `context_bonus`, `realm_index`, `garden_level`, and `location`. Go owns the random outcome and canonical mutation, but a future version could derive more of those values from canonical state.
2. NPC creation/bootstrap lifespan logic and ongoing Go NPC-death thresholds should be unified before restoring rich high-realm starting ages.
3. Continue migrating any remaining intentionally scoped Python database writes only when a Go canonical authority operation exists and tests can enforce the one-way boundary.
