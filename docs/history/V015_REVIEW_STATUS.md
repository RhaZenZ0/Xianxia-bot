# Xianxia RP v0.15 Partial Authority Migration — Batch 5 Hardened + Shared Starter Households

This is a **partial development checkpoint**, not the final v0.15 release. It preserves the verified Batch 4 authority boundary and moves the next requested family in order: exploration + hunting + location discovery first, followed immediately by secret realms.

## Implemented in this checkpoint

### Exploration, travel, hunting, and location discovery

Added Go-authoritative operations:

- `exploration.explore`
- `exploration.travel`
- `exploration.hunt`

Go now owns the canonical exploration transaction: cooldown validation/mutation, encounter selection, base reward RNG and reward application, active world-event participation claims/rewards, the 45% route-discovery roll, discovered-location persistence, unexpected-event selection, and unexpected-event activation.

Normal `/travel` and realm-hub travel now submit `exploration.travel`; Go validates known/discovered routes, hub eligibility, world/realm restrictions, auction-house bypass restrictions, and updates canonical character location. The old Python `_discover_next_location` RNG/mutation helper has been removed.

Hunting now uses Go for beast generation/scaling, canonical attribute modifiers, 2d10 resolution, loot/cultivation rewards, cooldowns, Beast Binder automatic equality contracts, and wild-beast encounter creation. Python renders the authoritative receipt only.

Unexpected **personal** exploration-event rewards/effects/Karma/Fate are applied in the same Go transaction. Unexpected **world-event** and **secret-realm** activations are selected and persisted by Go. As of Partial 05B, world-event persistent civilization/economy/sect consequences and world-history recording are also applied inside Go; production Discord no longer calls `SIM.apply_random_event`.

### Secret realms

Added:

- `secret_realm.status` authoritative query
- `secret_realm.enter`
- `secret_realm.explore`
- `secret_realm.leave`

Go now owns open-entrance lookup, realm/name normalization, location/minimum-realm validation, run creation/expiry, canonical room attributes and modifiers, room RNG/TNs, cooldowns, cultivation/stone/insight/item rewards, danger progression/recovery, room advancement, forced ejection, voluntary exit, one-time inheritance grants, permanent inheritance bonuses/items, and final-run shutdown.

Python no longer calls the old secret-realm run mutation/reward methods or rolls `roll_2d10` for secret-realm rooms.

### World catalog support

The Go world catalog now loads the location fields needed for travel/exploration authority plus unexpected events, secret realms, rooms, and inheritances directly from `content/world.json`.


### Character-creation authority leak closed

First-life birth-family generation is now canonical Go authority. `/begin` calls `character.family_options`, which resolves the fixed starter-household catalog in Go, persists actor-scoped opaque `choice_id` offers, and returns the canonical shared household identity for presentation. Starter names are stable: for example the Martial Household is always **Han Family** rather than receiving a fresh surname per player.

Starter archetypes are now persistent shared world entities. The first request seeds one canonical `birth_families` row per starter archetype; later players choosing that archetype join the same `family_id`, share the same household resources/history/NPC relatives, and do not create duplicate family rows. `character.create` still rejects client-supplied family payloads and consumes only the Go-issued choice ID.

Schema 20 adds shared starter-household identity plus per-guild household-thread metadata. Go-authoritative `family.household.enter` / `family.household.leave` operations move members into and out of a canonical `birth_family:<family_id>` location. Discord reuses one private household thread per family/server, adding members when they enter and removing them when they leave. Guided Scene Actions now include co-located player cultivators as valid targets, so multiple players inside the same household can interact mechanically as well as chat/RP in the shared scene.

The legacy Python `generate_family_options()` helper remains only for older test/simulation fixtures and is explicitly not used by production `/begin`. Samsara reincarnation was already Go-generated and remains Go-authoritative.

### Persistent exploration-event continuation

Normal `exploration.explore` is now the authoritative doorway into personal random events. A selected personal event is persisted as an `exploration_events` instance with an `exploration_event_participants` row instead of immediately granting its reward. The explore response reports `kind=event_started`; while that encounter remains active, another `/explore` reports `kind=event_active` and reopens the same event instead of producing unrelated rewards or another encounter. Normal travel and hunting are also blocked until the personal event is resolved or left.

`exploration.event.status`, `exploration.event.act`, and `exploration.event.leave` are Go-authoritative. The Discord expedition journal exposes **Observe / Approach / Help / Rob Them / Leave** controls, but Python only presents the result. Rolls, one-attempt-per-action state, reward/effect application, Karma/Fate changes, resolution, abandonment, action receipts, and domain events remain inside the Go transaction. Schema 21 adds the persistent event, participant, and event-action tables.

## Authority/RNG audit

- `app/bot/main.py`: **12,546 lines** in this checkpoint.
- Direct `DB.*` references remain numerous because reads, presentation/history, admin operations, and deferred domains still live in Python; the Discord layer is not yet a thin adapter.
- Direct gameplay `secrets.*` calls in production `bot/main.py`: **7 calls across 5 source lines**, down from **9** before 05B. Autonomous world-event selection no longer contributes Python RNG.
- `roll_2d10(...)` calls in `bot/main.py`: **3**, now confined to later sect/economy-style authority work.
- The remaining direct production RNG is concentrated in auction pursuit/door complications, sect recommendation/trials, and black-market heat consequences. Legacy Python world-simulation fallback code still contains RNG but production simulation runs through Go.

## Tested

- Full Python suite: **300 passed + 78 subtests**.
- Character-creation authority regression tests prove Go-generated/persisted offers, forged-family rejection, actor-version handoff, stable starter names, shared family IDs across players, no duplicate starter households, co-location inside the household, and offer consumption.
- Schema migration advanced through **21** for canonical creation offers, shared starter households, and persistent exploration-event instances; fresh-database, backdated/partial-migration recovery, and upgrade-path tests pass.
- Python contract suite: **33 passed**. The previous 8 Batch 4/5 authority pytest checks are now **1 compact Python↔Go boundary test**.
- Go: **all `go test ./...` packages passed**.
- Added direct Xianxia core hardening tests for request-envelope validation, contract error payloads, check degree/dice boundaries, relationship bounds/defaults/Unicode truncation, quest progression edge cases, and scene normalization/limits.
- Core hardening fixed three integer-overflow paths: state-version advancement now rejects `MaxInt64`, relationship/quest additions saturate before clamping, and check total/margin arithmetic saturates instead of wrapping.
- `go test -count=20 ./internal/core`, `go test -race ./internal/core`, and `go vet ./...` all passed.
- Python compile checks passed.
- Added direct native Go Batch 5 authority tests for exploration rewards/cooldowns/receipts, deterministic location-discovery persistence, discovered-route and realm-hub travel, hunting rewards/cooldowns/wild encounters, and the complete secret-realm status/enter/room/inheritance/re-enter/leave lifecycle.
- The single Python authority-boundary test now checks only handler-to-operation delegation and guards against reintroducing Python-side RNG/canonical mutation for migrated mechanics.
- Real schema-18 SQLite + HTTP Go-engine smoke testing passed for the earlier Batch 5 domain paths; current schema is **21** after the persistent exploration-event migration:
  - `exploration.explore`, including canonical rewards and route discovery
  - forced personal unexpected-event activation, including persistent active-effect insertion
  - forced server-wide unexpected-event activation and canonical `world_events` persistence
  - `exploration.hunt`, including Beast Binder auto-bond creation
  - `exploration.travel` across a discovered route in both directions
  - `secret_realm.status`
  - `secret_realm.enter`
  - repeated `secret_realm.explore` room resolution
  - `secret_realm.leave`
  - complete four-room secret-realm run, final shutdown, one-time inheritance row, permanent bonuses, and inheritance item grant

## Preserved from earlier authority batches

- Character creation, Go-generated birth-family offers, and aptitude mechanics.
- Qi/body cultivation and breakthrough authority.
- `/check` and structured scene-roll authority.
- Qi/body Perfection.
- Law comprehension and battle Law techniques.
- Spiritual Sense and concealment.
- Persistent condition treatment and heavenly tribulations.
- True death / Samsara / reincarnation.
- Combat turns, recovery items, fatality/Fate handling, persistent combat injuries, and battle finalization.
- Schema 21, including the schema-18 event ledger/actor/entity versions/action receipts, schema-19 canonical character-creation family offers, schema-20 shared starter-household identity/thread metadata, persistent exploration-event state/actions, replay/idempotency, and cryptographic Go RNG.

## Still intentionally incomplete

- `VERSION` is not promoted to final 0.15 yet.
- Autonomous world-event selection/activation/consequence persistence is now Go-owned. Event-expiry/Discord thread scheduling remains Python orchestration; the older Python autonomous NPC/world/sect/clan/economy implementations remain as fallback/legacy code while production scheduled simulation uses Go.
- Crafting, alchemy/foraging, forging, inscription, profession progression, spirit-beast lifecycle, artifact progression, PvP, manual study/techniques, forbidden-art consequences, crime atonement, and their scoped reputation/bounty mutations are now Go-authoritative.
- Sect recommendation/recruitment trials and broader sect/economy mechanics remain Python-authoritative.
- Black market, auction pursuit/door complications, and related economy/crime encounter mechanics remain Python-authoritative.
- Other special-location movement owned by later domains (for example auction/property/teleport/personal-world flows) remains with those domains; normal discovered-route and realm-hub travel is now Go-owned.
- The remaining **7 direct gameplay `secrets.*` calls across 5 lines** are expected to disappear as auction/sect/economy domains migrate.
- `bot/main.py` / database decomposition and final release/config/content-layout cleanup remain after authority migration.

## Next migration batch

The next clean mechanics slice, after the first-four migration and P0 dispatcher hardening, is:

1. Auction + black market + public economy + bounty hunters.
2. Equipment + party + formations + bosses.
3. Territories + wars + caravans.
4. Sect recruitment + sect economy + discipleship.
5. Family/clan + seclusion + Dao/Fate, then special properties/storage/arrays/personal-world authority and final Python cleanup.

## 2026-08-29 cross-loop profession / social progression pass

This build closes several player-facing progression gaps while preserving the existing authority boundary documented above:

- Non-Alchemy Forging / Formation / Inscription crafts now receive shared visible craftsmanship grades (Ordinary, Fine, Superior, Masterwork), quality points, and margin-based profession XP without duplicating durable finished items.
- Medicinal foraging now advances a persistent **Foraging** profession on both success and failure and reports the resulting rank/XP in the command receipt.
- Spirit-beast taming, training, and evolution now advance persistent **Beast Taming** mastery.
- Artifact bonding and awakening now advance persistent **Artifact Refining** mastery.
- Consent-gated nonlethal PvP now records **Martial Society** reputation for honorable completion/wins while remaining distinct from jurisdictional crime.
- Forbidden manual/technique behavior remains connected to karma, witnesses, world reaction, jurisdictional crime/evidence, bounties, and orthodox/demonic reputation consequences.

Authority note (superseded by the subsequent Go migration): crafting/foraging, spirit-beast lifecycle, artifact progression, PvP, manuals/forbidden arts, and scoped crime/reputation mutations are now Go-authoritative. Python remains orchestration/rendering for those paths.

Validation: full suite passes with **300 tests + 78 subtests**.

## 2026-08-29 P0 authoritative dispatcher hardening

- Fixed the first-four migration registry defect: the Go handlers existed in the authoritative switch but several operation names were absent from `authoritativeMutations`, causing production requests to bypass the authoritative dispatcher.
- Registered all migrated operations: `beast.tame`, `beast.feed`, `beast.train`, `beast.evolve`, `beast.active`, `artifact.bond`, `artifact.awaken`, `pvp.challenge`, `pvp.respond`, `pvp.act`, `manual.study`, `manual.technique`, and `crime.atone`.
- Added `TestFirstFourMigrationOperationsRegisteredAuthoritative` to permanently cover the complete first-four operation set, including `craft.resolve` and `forage.resolve`.
- Targeted Python boundary/feature regression: **23 passed**. Full Python output completed at **300 tests + 78 subtests passed**; Go `./...` passes.

## 2026-08-29 Partial 05A checkpoint

The corrected first-four registry/hardening state is preserved as the formal Partial 05A migration checkpoint. No world-event/combat-aftermath or later-domain authority changes are included in 05A.


## 2026-08-29 Partial 05B — world events + combat aftermath

- Added Go-authoritative `world_event.act`; canonical attributes, severity-adjusted TNs, cryptographic 2d10, participation/action records, contribution/support/investigation/interference, first-participation claims, and rewards/effects/Karma/Fate are resolved in one authoritative transaction.
- Exploration-triggered global events now apply persistent civilization/economy/sect consequences and world-history records inside Go when activated. Python only renders returned impact text.
- Event-manifestation battle victories now update world-event participation in the Go combat-finalization transaction.
- Normal NPC battle finalization now applies player-triggered NPC/family/clan/civilization/economy/sect aftermath and world-history consequences inside Go. Production Python no longer calls `SIM.apply_player_action`.
- Autonomous world-event spawn chance, eligible event/location selection, deduplication, activation, persistent world consequences, and history are now executed by the Go simulation system. Python receives spawned-event metadata only to create Discord threads.
- Added a direct Go regression proving `world_event.act` persists participation/action state and emits an authoritative action receipt.
- Validation: full Go `go test ./...` passed; targeted Python 05B tests passed **37 + 39 subtests**; full Python suite passed **300 + 78 subtests**.
