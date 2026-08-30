# v0.18 Stages 6–8 Adversarial Authority / Dead-Code Audit

Audit run only after a green pre-audit checkpoint was frozen.

## Findings closed

### HIGH — Caravan escort bounds were UI-only

**Before:** Discord command typing constrained escort strength to 0–20, but Go accepted arbitrary
`int64` values. Direct authoritative clients could bypass the UI range. Extreme values also reached
escort-cost and risk arithmetic, creating overflow/manipulation risk.

**Fix:** `caravan.dispatch` now rejects escort values outside 0–20 in Go before route/cost mutation.

**Regression:** `TestStage6CaravanDispatchRejectsOutOfRangeMechanicalInputs`.

### MEDIUM — Caravan quantity bounds were UI-only

**Before:** the UI constrained quantity to 1–50, while Go coerced/accepted values outside that
mechanical contract.

**Fix:** Go now requires quantity 1–50.

**Regression:** the same forged-input table covers quantity 0 and 51.

### MEDIUM — Dead incarnations could dispatch caravans

**Before:** dispatch loaded character realm/location/attributes but did not gate on `life_status`.

**Fix:** dispatch loads `life_status` and rejects any actor that is not `alive` before cargo/route/cost
mutation.

**Regression:** `TestStage6CaravanDispatchRejectsDeadActor`.

### LOW — Stage 7 retained dead channel-permission/setup remnants

**Before:** unused bot helpers still contained channel `set_permissions` mutation logic; startup still
called realm-hub setup/binding code and used auto-provisioning wording even though channel creation had
already moved to the dashboard. Some diagnostics still described Manage Channels as required for
channel repair.

**Fix:** removed the dead permission-mutation helpers and startup setup call; setup/status wording now
describes dashboard ownership; Manage Channels is informational/optional for the bot; stale
`Create / repair` guidance is gone.

**Regression/static gates:** setup contracts assert no guild text/category creation, no
`set_permissions(`, and no `auto-provision` code remains.

### LOW — Test support retained a deleted Python lifespan dependency

**Before:** a rarely exercised birth-family fixture still referenced `realm_lifespan_ceiling` after
the Python lifespan module was deleted. The primary suite did not execute that branch during the
first migration run.

**Fix:** the fixture now stores explicit natural-lifespan test data and does not reproduce the
mechanical realm lifespan model.

**Static gate:** repository scan returns no old lifespan module/helper references.

## Authority checks verified

### Stage 5 time boundary

- authoritative gameplay mutations reject caller `game_minute`;
- authoritative queries reject caller `game_minute`;
- canonical current time is Go-owned;
- simulation scheduler/import time remains explicit protocol data.

### Stage 6 lifespan

- one Go package defines effective lifespan and old-age expiry;
- player lifespan projection and NPC lifespan query consume it;
- NPC bootstrap ages are bounded by it;
- NPC natural-death simulation consumes the same old-age rule.

### Stage 6 roads

- route selection is server-owned and multi-hop;
- per-leg duration/danger/cost are derived from canonical world data;
- route discovery and transit timing are persisted authoritatively;
- insufficient funds reject travel atomically;
- road encounter outcomes are server-owned.

### Stage 6 caravans

- route, ETA, operating cost, risk, payout basis, and settlement are Go-owned;
- caller duration is rejected;
- quantity/escort limits are enforced at the Go boundary;
- dead actors cannot dispatch;
- cargo/currency mutation is authoritative.

### Stage 7 setup

- no bot `create_text_channel` / `create_category` calls remain;
- no bot channel `set_permissions` calls remain;
- startup does not auto-provision realm channels;
- configured Discord channels are dashboard-owned and bot-validated;
- private gameplay threads are intentionally retained.

### Stage 8 Python cleanup

- deleted lifespan authority modules remain absent;
- no imports/references to their helpers remain;
- Python authority-boundary contract passes;
- Python remains orchestration/read/UI code where Go owns migrated mechanics.

## Remaining observations

No unresolved Stage 6–8 caller-authority bypass was found after these fixes.

Optional design work remains outside this audit: richer caravan road events/supplies, route-choice UI,
and explicit artifact-capable item classes.

## Final audit result

The post-fix full regression matrix is green:
- `go test ./...` PASS;
- `go vet ./...` PASS;
- `python -m pytest -q`: 239 passed, 78 subtests passed;
- focused authority/setup/dead-code contracts: 24 passed.

Schema remains 22. Runtime release metadata remains v0.17 because this is still a v0.18 development
checkpoint.
