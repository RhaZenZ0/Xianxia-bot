# v0.18 Stage 2 Notes — Forage Effect Authority

## Before

Stage 1 removed forged forage location/realm/property/family/cooldown inputs, but Python still
computed an `effect_bonus` and submitted it to `forage.resolve`.

That remaining value came from `current_effect_modifiers()`, which also settled pill-toxicity decay
and synchronized the `pill_toxicity` active-effect row during what appeared to be a read operation.

## After

The Discord forage handler submits:

```json
{}
```

Go now calculates the complete forage effect contribution at canonical world time.

### Effect sources resolved by Go

1. persisted active effects whose time window is currently active;
2. spiritual-root mutation modifiers;
3. active/evolved bloodline modifiers;
4. active/evolved physique modifiers and drawbacks;
5. the currently deployed location array at the character's physical location;
6. pill toxicity after canonical world-time decay.

Only additive `alchemy_bonus` values are used for forage, matching the prior Python
`aggregate_modifiers(...).get("alchemy_bonus", 0)` behavior, including stack multiplication and
integer truncation semantics.

### Pill-toxicity authority

`forage.resolve` now:
- reads canonical world time from `world_state`;
- decays `alchemy_state.pill_toxicity` using the existing 12-hour/1-point rule;
- advances `last_toxicity_game_minute` by complete decay steps;
- removes the canonical toxicity effect below 40 toxicity;
- otherwise upserts the normalized `Pill Toxicity` active effect;
- includes the resulting `alchemy_bonus` penalty in the forage modifier.

The new read-only `effects.current` authoritative query previews current toxicity at canonical world
time without changing `alchemy_state`, `active_effects`, actor versions, or event receipts. Python's
shared effect reader uses that preview instead of mutating SQLite itself.

### Active-effect reads

`Database.get_active_effects()` now:
- selects only effects with `starts_game_minute <= now`;
- excludes effects whose end minute has passed;
- does not delete expired rows during a read.

This makes `current_effect_modifiers()` read-only from Python's perspective while preserving a
canonical current effect view.

## Boundary enforcement

Go rejects client-supplied:
- `effect_bonus`;
- `context_bonus`;
- `location`;
- `realm_index`;
- `garden_level`;
- `cooldown_seconds`;
- `tn`;
- `spirit_resources`;
- `loot`;
- `rare_found`.

Python contract tests require the forage handler to avoid `current_effect_modifiers()` and forbid
`effect_bonus` construction.

## Validation

- Python: 238 passed, 78 subtests passed.
- Go: `go test ./...` PASS.
- Go: `go vet ./...` PASS.
- Database schema: 21, no migration.
