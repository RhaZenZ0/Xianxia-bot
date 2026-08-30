# Xianxia RP v0.16 — Final Release Notes

Release date: 2026-08-29

## Release theme

v0.16 completes the final authority-cleanup pass requested after the v0.15 migration checkpoint. The release removes the remaining production-reachable Python gameplay RNG blockers identified by the v0.15 audit and promotes the package from a partial checkpoint to a truthful final release marker.

## Authority changes

- Added a native Go `/v1/simulation/bootstrap` boundary for initial NPC moods and martial-clan branch/retainer/relation construction.
- `WorldSimulator.initialize()` now seeds deterministic base rows and then requires the Go bootstrap; Python no longer rolls NPC moods or clan topology.
- Character creation and reincarnation call the Go simulation bootstrap directly after creating/attaching the new family state.
- Moved inherited child spiritual-root selection into Go `family.add_child`.
- `family.add_child` rejects client-supplied `spiritual_root`, `realm_index`, and `phase` mechanical fields.
- Moved foraging region profile, TN, resource bonus, rare candidate selection, rare chance, roll, and awarded loot into Go `forage.resolve`.
- `forage.resolve` rejects client-supplied `tn`, `spirit_resources`, `loot`, and `rare_found` outcome fields.
- Fixed a clan-bootstrap under-fill bug discovered by the new Go test: retainer groups now fill the canonical requested total exactly.

## Python purge delta

Removed or kept absent from production Python:

- `WorldSimulator._npc_mood()`
- `WorldSimulator.ensure_all_clans()`
- `WorldSimulator.alchemy_forage_profile()`
- `birthfamily.inherited_root()`
- the old no-engine `WorldSimulator` gameplay fallback
- test-only `CultivationService` and `SectService`
- dead `ForbiddenArtsService`
- `Database.create_character()` test fixture usage
- previously identified unreachable Go-owned `Database` mutators

Boundary tests assert that these implementations cannot silently return.

## Compatibility

- Release version: **0.16**
- Database schema: **21**
- No schema migration is introduced by v0.16.
- Existing schema-21 saves remain the target format.

## Validation

- `python -m pytest -q`: **222 passed, 39 subtests passed**
- `go test ./...`: **PASS**
- `go vet ./...`: **PASS**
- `bash -n startup.sh stop.sh update.sh`: **PASS**
- `python -m json.tool content/world.json`: **PASS**
- `python -m compileall -q app tests`: **PASS**
- Docker Compose rendering: **not run** because Docker is unavailable in the release sandbox.
