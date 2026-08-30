# v0.15 Final Release-Readiness Audit

Audit date: 2026-08-29

## Verdict

**Python gameplay implementation purge: PASS.** The requested no-engine simulation fallback, stale Go-duplicated services/mutators, direct mutator pytest, and production `Database.create_character()` fixture path are removed and guarded against reintroduction.

**Automated code verification: PASS.** Final Python suite: **223 passed + 39 subtests**. Authority/simulation boundary suite: **5 passed**. `go test ./...`: PASS. `go vet ./...`: PASS. `bash -n startup.sh stop.sh update.sh`: PASS. `content/world.json`: valid JSON. Docker Compose validation was not run because Docker is not installed in this sandbox.

**Final v0.15 release sign-off: HOLD.** The code is clean for this purge milestone, but the tree is not yet a truthful final v0.15 release and strict “all gameplay RNG/authority in Go” is not yet achieved.

## Release blockers

1. **Release identity is still v0.14.** `VERSION`, `app/version.py`, Docker image labels, Compose labels, the release-version contract test, and the top-level README still identify the release as 0.14. `PARTIAL_BUILD.txt` explicitly says “NOT A FINAL RELEASE.” These markers must be deliberately promoted together when v0.15 is actually cut.
2. **Production-reachable Python gameplay RNG still exists.** `WorldSimulator._npc_mood()` randomizes seeded NPC mood during `initialize()`; `WorldSimulator.ensure_all_clans()` randomizes clan branch/retainer/relationship data; `WorldSimulator.alchemy_forage_profile()` chooses the rare forage item/chance in Python before calling Go `forage.resolve`; `birthfamily.inherited_root()` rolls a child root in Python before Go `family.add_child`. These are the clearest remaining strict-authority blockers.
3. **Live Python `Database` gameplay/admin mutators remain.** The purge removed every statically unreachable write-like public method, but 68 write-like methods remain production/internal-reachable. They are catalogued in `V015_REMAINING_DATABASE_MUTATORS.md` for future Go migration rather than unsafe deletion.

## Requested purge outcomes

- `WorldSimulator` no-engine gameplay fallback: **removed**; `engine` is mandatory and `run_due()` / `force_run()` delegate to Go.
- Simulation pytest: **converted** to Python↔Go boundary coverage; authoritative mechanics remain covered by native Go simulation tests (`go_core/internal/simulation/world_test.go`).
- Test-only `CultivationService` / `SectService`: **removed**.
- Dead migrated `ForbiddenArtsService`: **removed** with its stale implementation tests.
- Test use of `Database.create_character()`: **replaced** by test-only `seed_character()`.
- Unreachable Go-owned `Database` mutators: **removed**; final static audit reports **0** unreachable write-like public methods.
- Pytest that directly exercised purged mutators: **deleted or converted**.
- `test_authority_boundary.py`: **strengthened** to make the purged methods/services/fallbacks a negative contract.
- Caravan settlement production caller: migrated from `DB.settle_player_caravans()` to Go `caravan.settle`.

## Test delta

The uploaded pre-cleanup tree had **25 failing pytest cases** (275 passed) concentrated in stale Python gameplay-implementation tests. The final purge tree has **223 passing tests + 39 passing subtests** with no failures; the lower test count is intentional removal of tests whose subject was deleted Python authority, not loss of Go mechanic coverage.

## Recommended next authority pass

Move the four production RNG paths above first, then route Priority-1/2 methods from `V015_REMAINING_DATABASE_MUTATORS.md` through authoritative Go operations. Only after that should the version/release markers be promoted to 0.15 and the final package be labeled a release.
