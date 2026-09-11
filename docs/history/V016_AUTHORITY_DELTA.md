# v0.16 Authority Delta from the v0.15 Final-Purge Tree

## Python removals / caller conversions

### `app/simulation/world.py`
- removed `_npc_mood()`
- removed `WorldSimulator.ensure_all_clans()`
- removed `WorldSimulator.alchemy_forage_profile()`
- removed gameplay use of `secrets`
- NPC seed rows now use `mood='pending'`; `initialize()` requires `engine.bootstrap_simulation()` to resolve Go-owned bootstrap randomness

### `app/birthfamily.py`
- removed `inherited_root()`

### `app/bot/main.py`
- character creation: replaced `SIM.ensure_all_clans(...)` with `ENGINE.bootstrap_simulation(...)`
- reincarnation: replaced `SIM.ensure_all_clans(...)` with `ENGINE.bootstrap_simulation(...)`
- `/alchemy forage`: stopped computing/sending TN, regional resources, rare selection, or loot outcome
- family child creation: stopped computing/sending spiritual root, realm index, or phase
- promoted user-facing release references to v0.16

### `app/game_engine.py`
- added mandatory `bootstrap_simulation(game_minute)` client call to `/v1/simulation/bootstrap`

## Go authority additions

### `go_core/internal/simulation/bootstrap.go`
- added Go-owned NPC mood bootstrap
- added Go-owned martial-clan branch generation
- added Go-owned retainer group generation
- added Go-owned clan relation generation/history recording
- bootstrap is idempotent
- retainer group allocation fills the canonical requested total exactly

### `go_core/internal/simulation/world.go`
- added NPC role/personality data to the native simulation catalog

### `go_core/internal/server/server.go`
- added `POST /v1/simulation/bootstrap`

### `go_core/internal/game/crafting_actions.go`
- `forage.resolve` now owns regional resource lookup, world tier, TN, resource bonus, common yield, rare pool/chance, 2d10 roll, final loot award, cooldown write, and profession progression
- rejects client-supplied `tn`, `spirit_resources`, `loot`, and `rare_found`

### `go_core/internal/game/family_dao_actions.go`
- `family.add_child` now owns inherited-root RNG and child lifespan roll
- rejects client-supplied `spiritual_root`, `realm_index`, and `phase`

## Test conversions / additions

### Python
- strengthened `tests/python/contracts/test_authority_boundary.py` negative contracts for deleted methods and forbidden payload ownership
- updated simulation-engine test doubles to implement the mandatory bootstrap boundary
- removed the stale pytest that directly tested Python forage-profile implementation
- release-version contract now requires v0.16 everywhere

### Go
- added bootstrap ownership/idempotence coverage, including exact retainer totals
- added forage authority rejection/result ownership coverage
- added family-child inherited-root authority rejection/result ownership coverage

## Release metadata
- `VERSION`: 0.16
- `app/version.py`: 0.16
- Docker/Compose release labels: 0.16
- README/environment/user-facing release references: 0.16
- obsolete top-level partial-build marker removed
- schema remains 21
