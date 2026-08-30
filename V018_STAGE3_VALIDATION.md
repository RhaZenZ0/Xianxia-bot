# v0.18 Stage 3 Validation

## Final validation

- `go test ./...` — PASS
- `go vet ./...` — PASS
- `python -m pytest -q` — 238 passed, 78 subtests passed

## Stage 3 authority checks

- Python `_run_crafting()` submits only `{"recipe": recipe}`.
- Go rejects forged `context_bonus`, `location`, `game_minute`, derived bonus fields, `modifier`, and
  `tn`.
- Go derives canonical location/time and all craft context.
- Alchemy toxicity is settled before `alchemy_bonus` derivation.
- Alchemy batch location/time are canonical.
- Forging and Formation facility/effect mappings have dedicated Go coverage.
- Deceased characters cannot craft through the authoritative endpoint.

## Stage 2 audit

`V018_STAGE2_AUDIT.md` records the pre-Stage-3 audit and baseline line map.
