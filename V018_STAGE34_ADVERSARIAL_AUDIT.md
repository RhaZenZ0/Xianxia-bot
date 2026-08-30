# v0.18 Stage 3 + Stage 4 Adversarial Authority Audit

Scope: `craft.resolve`, companion/artifact mutations, their Python orchestration surfaces, and the
family-city travel integration added to the Stage 4 checkpoint.

## Findings closed

### HIGH — Repeat artifact awakening could farm progression

**Before:** `artifact.awaken` checked Bond/Resonance but did not reject an already-awakened artifact.
A caller could repeatedly invoke awakening with fresh action IDs, repeatedly receiving Artifact
Refining XP and resonance increments.

**Fix:** `artifactAwakenAction` now rejects `awakened != 0` before mutation
(`go_core/internal/game/beast_artifact_actions.go:559-615`, guard near line 588).

**Regression:** `TestStage45ArtifactAwakenIsOneWay` confirms the second awakening fails and profession
XP does not change.

### HIGH — Higher-world hub unlock could inherit a newborn-city realm floor

**Before:** `worldMinRealm` selected the minimum realm across every location in a world. Higher-world
family cities are intentionally realm-0-safe for births, so a direct Go `mode=hub` request could
incorrectly see the world as realm-0 unlocked.

**Fix:** `worldMinRealm` now uses the world's realm-hub threshold when a hub exists
(`go_core/internal/game/exploration_actions.go:147-168`).

**Regression:** `TestRealmHubUnlockUsesHubThresholdNotNewbornFamilyCityFloor`.

### MEDIUM — Secondary companion actions lacked a canonical living-actor gate

**Before:** tame/train loaded character state, but feed/evolve/active/bond/awaken did not consistently
require an alive character.

**Fix:** every companion/artifact mutation goes through `loadLivingCompanionActor`
(`beast_artifact_actions.go:60-69`).

**Regression:** `TestStage45AllCompanionMutationsRejectDeadActors` covers all seven mutations.

### MEDIUM — Companion payload filtering was blacklist-based/incomplete

**Before:** tame/feed/train/bond rejected known mechanical fields, while evolve/active/awaken could
silently ignore arbitrary extra fields. Future fields could accidentally become trusted.

**Fix:** strict per-operation allowlists via `validateCompanionPayload`
(`beast_artifact_actions.go:40-58`).

**Regression:** `TestStage4CompanionActionsRejectCallerMechanicalInputs` includes arbitrary
`unexpected` payload keys for all seven operations.

### MEDIUM — Python encounter read could mutate expiry from caller time

**Before:** `Database.get_wild_beast_encounters` updated encounter status to `expired` using its
`game_minute` argument during a read.

**Fix:** the helper is now SELECT-only and filters expiry without mutation
(`app/database/core.py:5262+`). Canonical taming checks expiry using Go-owned world time.

**Regression:** `test_wild_beast_encounter_reader_is_read_only_and_filters_expiry`.

### LOW/DEFENSE-IN-DEPTH — Stage 3 craft payload used a mechanical blacklist

**Before:** known craft context fields were rejected, but unrelated extra keys were silently ignored.

**Fix:** `craft.resolve` accepts exactly `recipe`
(`go_core/internal/game/crafting_actions.go:267-292`).

**Regression:** Stage 3 forged-input coverage includes profession/cost/output/quality/roll plus
arbitrary non-recipe fields through the strict allowlist.

## Authority checks verified

### Stage 3 craft

- living character loaded from DB (`crafting_actions.go:294-300`);
- canonical world clock (`302-305`);
- canonical current location (`306-309`);
- profession/TN/cost/output from world catalog;
- profession mastery from DB;
- Alchemy toxicity settlement before effect calculation;
- active/aptitude/array effect bonus derived in Go (`335-341`);
- property facility gated by exact current location plus owner/guest access;
- sect-manor facility gated by membership and exact base location;
- Alchemy family tradition derived from persisted birth family;
- roll, inventory consumption/output, quality and profession progression all mutated in the same
  `BEGIN IMMEDIATE` authoritative transaction;
- Python `_run_crafting` submits only `recipe`;
- no legacy Python craft/alchemy progression mutators remain.

### Stage 4/4.5 companion

- taming encounter is scoped to actor ID, available status, canonical expiry, and canonical location;
- maximum contracted-beast count is DB-derived;
- taming roll/TN/path/experience and cooldown are Go-owned;
- feed food type/gain/inventory/cooldown are Go-owned;
- train actor location, Beast Pen access/level/bonus, gain and cooldown are Go-owned;
- evolve requirement/rank/loyalty mutation is DB-derived;
- active beast is scoped to actor ownership;
- artifact bond requires a canonical carried item and uses fixed Go cooldown;
- artifact awakening threshold is DB-derived and awakening is now one-way;
- all seven mutations reject dead actors and extra payload fields;
- action IDs are protected by the authoritative replay ledger and mutations execute under
  `BEGIN IMMEDIATE`.

## Remaining risks outside Stage 3/4 scope

1. **Stage 5 canonical game time:** many older authoritative operations still accept a caller
   `game_minute`. `exploration.travel` is one example; road discovery uses that timestamp as metadata.
   Access/road/hub authorization does not depend on that supplied timestamp, but the timestamp itself
   should move to canonical Go time in Stage 5.
2. **Travel simulation depth:** roads currently control direct route visibility/discovery, not
   duration, cost, danger, or multi-hop pathfinding.
3. **Artifact eligibility is intentionally broad today:** `/artifact bond` is described as bonding a
   carried canonical item. If only designated artifact-capable item classes should bond, the item
   catalog needs an explicit eligibility field and Go validation. This is a design rule to build,
   not a caller-authority bypass under the current command semantics.

## Audit result

No unresolved Stage 3 or Stage 4 caller-controlled mechanical authority bypass was found after the
fixes above. Remaining authority cleanup is primarily Stage 5's cross-system canonical-time work.
