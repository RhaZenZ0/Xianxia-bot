# v0.18 Stage 3 Notes — Craft Resolve Authority

## Before

`craft.resolve` owned the roll, materials, outputs, quality, Alchemy batch bookkeeping, and
profession progression, but Python still supplied three canonical/mechanical inputs:

- `context_bonus`;
- `location`;
- `game_minute`.

Python built `context_bonus` from active effects, aptitude effects, a deployed location array,
player-property access/facilities, sect-manor facilities, and the Alchemy Family tradition.

That allowed a direct caller to forge the crafting modifier, crafting location, and event time.

## After

The Discord crafting handler submits only:

```json
{"recipe": "<catalog recipe name>"}
```

Go now derives all crafting context inside the authoritative transaction.

### Canonical inputs derived by Go

1. actor character and living state;
2. canonical physical location;
3. canonical world game minute;
4. recipe profession and TN from the world catalog;
5. profession mastery level from `profession_progress`;
6. active persisted effect modifiers;
7. spiritual-root, bloodline, and physique additive profession modifiers;
8. the active deployed-location-array modifier;
9. accessible player-property Alchemy/Forge/Formation facility bonus;
10. current sect-manor Alchemy Hall / Forge Pavilion / Defensive Array craft bonus;
11. Alchemy Family +2 tradition bonus for Alchemy only.

For Alchemy recipes, Go also settles pill toxicity first so `alchemy_bonus` uses the same current
toxicity semantics established in Stage 2.

### Client fields now forbidden

`craft.resolve` rejects client-supplied:

- `context_bonus`;
- `location`;
- `game_minute`;
- `effect_bonus`;
- `facility_bonus`;
- `manor_facility_bonus`;
- `family_bonus`;
- `modifier`;
- `tn`.

Derived breakdown values are returned only for rendering/audit.

### Canonical Alchemy bookkeeping

`alchemy_batches.location`, `alchemy_batches.game_minute`,
`alchemy_state.last_toxicity_game_minute`, and the crafting domain event now use Go-derived
canonical values.

### Python cleanup

`_run_crafting()` no longer calls:

- `current_effect_modifiers()`;
- `DB.get_abode_by_location()`;
- `DB.can_access_abode()`;
- `DB.get_birth_family()`;
- `DB.get_member_sect_manor()`;
- `DB.get_profession_progress()`.

The handler renders facility/family/mastery breakdowns from the authoritative result.

## Boundary tests

Stage 3 adds tests that:

- reject each forged mechanical/context field;
- derive Alchemy effect, property, manor, family, profession, location, and time from persisted Go
  state;
- settle toxicity before Alchemy context calculation;
- write canonical Alchemy batch location/time;
- reject crafting for a deceased canonical character;
- enforce that the Python craft handler no longer constructs the removed payload fields.

## Schema/version

- Database schema remains 21.
- No migration is required.
- Runtime release label remains v0.17 for this cumulative v0.18 development checkpoint.
