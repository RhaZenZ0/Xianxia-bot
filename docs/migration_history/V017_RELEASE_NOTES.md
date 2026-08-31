# v0.17 Recommended Release Notes

## Purpose

v0.17 is a consolidation release built from the strongest parts of v0.15 and v0.16. It keeps v0.16's Go-owned RNG/mechanics improvements while repairing the canonical-mutation regressions introduced in v0.16.

## Authority changes

- Restored full Go-owned simulation bootstrap.
- Restored Go-owned simulation interval changes.
- Restored Go-owned GM/admin operations for karma, currency, world-time advance, teleport, revive, clear-battle, and automation settings.
- Preserved Go-owned forage TN/resources/loot/rare RNG.
- Preserved Go-owned inherited child spiritual-root RNG.
- Preserved Go-owned NPC mood and clan bootstrap RNG.
- Python bootstrap now sends only `game_minute`; Go loads canonical world catalog data itself.

## Correctness and hardening

- Preserved exact retainer allocation from v0.16.
- Restored unique clan branch naming.
- Uses conservative NPC bootstrap ages until the lifespan/death models are unified.
- Restored bootstrap request-size protection with a 1 MiB cap.
- Preserved caravan settlement action-id idempotency/error handling.
- Recombined strict v0.15 authority guards with v0.16 forage/child payload guards.

## Compatibility

- Release version: **0.17**
- Database schema: **21**
- Database migration: **none**

## Validation

- Python: **237 passed, 78 subtests passed**
- Go: `go test ./...` **PASS**
- Go vet: **PASS**
- Python compile: **PASS**
- Shell syntax checks: **PASS**
- `content/world.json`: **valid JSON**
