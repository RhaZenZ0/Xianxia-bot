# v0.17 Remaining Authority / Model Gaps

This document records non-blocking follow-up work. These are not known release regressions in v0.17.

## Forage inputs

`forage.resolve` owns the roll, TN derivation, resource lookup, loot plan, rare RNG, awards, cooldown mutation, and profession progress in Go. It still accepts `context_bonus`, `realm_index`, `garden_level`, and `location` from orchestration.

A stricter future boundary could derive some or all of these from canonical Go-readable state.

## NPC lifespan model

The richer Python helper for starting age can produce ages appropriate to very long-lived high-realm cultivators. The current Go NPC-life death threshold is simpler and lower.

v0.17 therefore deliberately uses the conservative v0.15-style bootstrap age. A future release should unify:

- realm/stage lifespan extension;
- natural lifespan;
- starting age distribution;
- death/old-age checks.

Only after those use one model should richer starting ages be restored.

## Remaining Python writes

Python still contains database operations that are outside the specifically migrated canonical gameplay/admin paths. They should not be mechanically deleted.

Future migrations should use this sequence:

1. define a Go authority operation;
2. move validation + canonical mutation into Go;
3. migrate callers;
4. add a negative boundary test;
5. remove the old Python mutator only when no live caller depends on it.
