# v0.18 Stage 2 Line-by-Line Authority Audit

Audit performed before Stage 3 implementation.

## Scope

Stage 2 claims that `forage.resolve` accepts no mechanical payload from Python and that Go owns
canonical forage state, effect derivation, pill-toxicity settlement, cooldown enforcement, RNG,
loot, inventory mutation, and profession progress.

The audit traced every Stage 2 authority surface:

- `app/bot/main.py`: `current_effect_modifiers()` and `alchemy_forage()`;
- `app/database/core.py`: `get_active_effects()`;
- `go_core/internal/game/crafting_actions.go`: `forageResolveAction()`;
- `go_core/internal/game/effect_authority.go`: canonical world clock, toxicity preview/settlement,
  effect aggregation, aptitude modifiers, and deployed-array modifiers;
- `go_core/internal/game/authoritative.go`: transaction/query routing;
- `tests/python/contracts/test_authority_boundary.py`;
- `go_core/internal/game/batch4_authority_test.go`.

## Findings

### PASS — Python forage payload

`alchemy_forage()` submits `{}` to `forage.resolve`.

It does not construct or submit:

- `context_bonus`;
- `effect_bonus`;
- `location`;
- `realm_index`;
- `garden_level`;
- `cooldown_seconds`;
- `tn`;
- `spirit_resources`;
- `loot`;
- `rare_found`.

The Python cooldown check is presentation/fast-fail only. Go independently enforces the canonical
20-minute cooldown.

### PASS — Go rejects forged forage mechanics

`forageResolveAction()` decodes the raw object first and rejects every known mechanical field above
before resolving state.

Unknown unused JSON keys are ignored and cannot affect mechanics.

### PASS — Canonical identity/state

Go loads the actor's canonical character row, requires `life_status == "alive"`, derives physical
location and realm index, and resolves cave-abode access from persisted ownership/access rows.

An accessible cave abode may redirect forage resource lookup to its persisted `base_location`; an
inaccessible property does not grant its garden or base-location benefits.

### PASS — Family and property modifiers

Go derives the Alchemy Family forage bonus from `character_birth_family -> birth_families` and
derives the herb-garden bonus from the accessible property's persisted garden level.

No Python value participates in either calculation.

### PASS — Canonical game time

Forage obtains the current game minute from the persisted `world_clock` in Go. Negative real-time
drift and negative clock values are clamped.

The read-only `effects.current` query uses the same clock model without creating/mutating a missing
clock row.

### PASS — Pill-toxicity settlement

Inside the authoritative forage transaction, Go:

1. reads `alchemy_state`;
2. applies complete 12-hour decay steps;
3. advances `last_toxicity_game_minute` by complete steps only;
4. deletes the canonical toxicity effect below 40;
5. otherwise upserts a normalized `Pill Toxicity` effect;
6. includes the resulting additive `alchemy_bonus` in forage.

Because `applyAuthoritative()` executes mutations inside `BEGIN IMMEDIATE`, toxicity/effect writes
roll back if later forage resolution fails.

### PASS — Read-only effect view

`effects.current` previews decayed toxicity without updating `alchemy_state`, `active_effects`,
actor versions, receipts, or domain events.

`current_effect_modifiers()` removes any persisted toxicity row from its local read result and
substitutes the Go preview, preventing stale persisted toxicity from being double-counted.

### PASS — Active-effect filtering

`Database.get_active_effects()` is read-only and filters:

- `starts_game_minute <= now`;
- `ends_game_minute IS NULL OR ends_game_minute > now`.

Expired rows are ignored rather than deleted during a read.

### PASS — Effect aggregation parity for forage

`canonicalAdditiveEffectBonus()` reproduces the additive path used by Python for
`alchemy_bonus`:

- persisted active effects;
- effect stacks;
- active deployed-location array;
- spiritual-root mutation modifiers;
- active/evolved/mutated bloodline evolution modifiers;
- active/evolved physique evolution and drawback modifiers.

Only additive operations contribute, and the accumulated floating total is truncated to `int64`,
matching the former `int(aggregate_modifiers(...).get("alchemy_bonus", 0))` call.

### PASS — Canonical region/RNG/reward mutation

Go derives regional spirit resources, world tier, TN, common yield, rare pool/chance, profession
modifier, 2d10 roll, inventory mutation, profession progress, and the fixed cooldown.

The result/event records canonical game minute and derived context for presentation/audit only.

### PASS — Transaction and idempotency boundary

`forage.resolve` is routed through the authoritative mutation path:

- action ID replay is checked before execution;
- mutation uses `BEGIN IMMEDIATE`;
- actor version/event/receipt are committed atomically;
- errors roll back the transaction.

### PASS — Stage 2 tests

Existing Stage 2 tests cover:

- no-payload forage resolution;
- accessible garden/family derivation;
- cooldown enforcement;
- persisted effect + stack + array + aptitude + toxicity aggregation;
- toxicity settlement;
- read-only toxicity preview;
- Python caller boundary.

Baseline before Stage 3:

- Python: 238 passed, 78 subtests passed.
- Go: `go test ./...` PASS.

## Residual observations outside the Stage 2 forage boundary

These do not let a caller forge `forage.resolve`, but remain later cleanup targets:

1. `sync_pill_toxicity_effect()` still exists for non-forage Python paths such as item-use/purge
   flows.
2. `Database.get_alchemy_state()` still performs toxicity decay when called by legacy Python
   alchemy paths.
3. Malformed persisted effect JSON is ignored by Go additive aggregation rather than treated as a
   database-integrity error.

None of these observations blocks Stage 3 `craft.resolve` authority, but the first two should be
removed when the remaining alchemy/item mutation paths are migrated to Go.


## Stage 2 baseline line map reviewed

The following references are to the uploaded Stage 2 checkpoint before Stage 3 edits.

| File | Stage 2 lines | Review result |
|---|---:|---|
| `app/bot/main.py` | 346–388 | `current_effect_modifiers()` is read-only with respect to toxicity/effects; Go preview substituted for stale persisted toxicity. |
| `app/bot/main.py` | 5567–5583 | `alchemy_forage()` submits `{}`; Python cooldown read is non-authoritative presentation only. |
| `app/database/core.py` | 4335–4349 | `get_active_effects()` performs filtered reads only; no delete/update side effect. |
| `go_core/internal/game/effect_authority.go` | 24–99 | canonical/read-only world-clock derivation checked. |
| `go_core/internal/game/effect_authority.go` | 101–167 | toxicity preview and `effects.current` checked for zero writes. |
| `go_core/internal/game/effect_authority.go` | 169–250 | toxicity settlement/upsert/delete checked inside authoritative transaction semantics. |
| `go_core/internal/game/effect_authority.go` | 252–290 | Go toxicity payload checked against Python `medicine_toxicity_effect()` thresholds/modifiers. |
| `go_core/internal/game/effect_authority.go` | 293–430 | active effects, stacks, array, spiritual-root, bloodline, physique additive aggregation checked against Python semantics. |
| `go_core/internal/game/crafting_actions.go` | 264–527 | complete Stage 2 `forageResolveAction()` checked from payload rejection through event result. |
| `go_core/internal/game/authoritative.go` | 158–203 | replay, actor validation, API version, `BEGIN IMMEDIATE`, rollback path checked. |
| `go_core/internal/game/authoritative.go` | 247–390 | forage routing, event/version/receipt atomic commit path checked. |
| `go_core/internal/game/batch4_authority_test.go` | 455–608 | garden/family, cooldown, effect/toxicity, and read-only query tests checked. |
| `tests/python/contracts/test_authority_boundary.py` | 154–196 | Python forage/effect-reader forbidden-token contract checked. |

This line map is the evidence base for the audit conclusion above.

## Audit conclusion

**Stage 2 is sound for its stated `forage.resolve` authority boundary.**

No Stage 2 forage-authority repair was required before Stage 3.
