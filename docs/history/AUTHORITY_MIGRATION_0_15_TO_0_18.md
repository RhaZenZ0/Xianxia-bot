# The authority migration, v0.15 to v0.18

The staged move of every game rule from Python into the Go engine, v0.15 to v0.18, audit by audit.
Twenty-two files were written along the way - checkpoint reviews, deletion lists, readiness audits,
per-stage notes, a consolidated build history and the release notes of each step - and they were
merged into this one file after v1.39.0, each kept whole as a section below under its original
filename, in the order the work happened. Nothing here describes the current release; `VERSIONS.md`
is the changelog and `docs/ARCHITECTURE.md` says how the engine works now.

## Contents

- `V015_REVIEW_STATUS.md`
- `PRE_FINAL_AUTHORITY_CLEANUP_STATUS.md`
- `V015_REMAINING_DATABASE_MUTATORS.md`
- `FINAL_AUTHORITY_PURGE_DELETION_LIST.md`
- `V015_FINAL_RELEASE_READINESS_AUDIT.md`
- `V016_AUTHORITY_DELTA.md`
- `V016_REMAINING_DATABASE_MUTATORS.md`
- `V016_FINAL_RELEASE_READINESS_AUDIT.md`
- `V016_RELEASE_NOTES.md`
- `V017_COMPARISON_AND_MERGE.md`
- `V017_REMAINING_AUTHORITY_GAPS.md`
- `V017_RELEASE_NOTES.md`
- `V018_AUTHORITY_CLEANUP_ROADMAP.md`
- `V018_STAGE1_NOTES.md`
- `V018_STAGE2_NOTES.md`
- `V018_STAGE3_NOTES.md`
- `V018_STAGE45_NOTES.md`
- `V018_STAGE5_NOTES.md`
- `V018_STAGE678_NOTES.md`
- `V018_BUILD_HISTORY.md`
- `STAGE.md`
- `V018_RELEASE_NOTES.md`

---

## `V015_REVIEW_STATUS.md`

## Xianxia RP v0.15 Partial Authority Migration — Batch 5 Hardened + Shared Starter Households

This is a **partial development checkpoint**, not the final v0.15 release. It preserves the verified Batch 4 authority boundary and moves the next requested family in order: exploration + hunting + location discovery first, followed immediately by secret realms.

### Implemented in this checkpoint

#### Exploration, travel, hunting, and location discovery

Added Go-authoritative operations:

- `exploration.explore`
- `exploration.travel`
- `exploration.hunt`

Go now owns the canonical exploration transaction: cooldown validation/mutation, encounter selection, base reward RNG and reward application, active world-event participation claims/rewards, the 45% route-discovery roll, discovered-location persistence, unexpected-event selection, and unexpected-event activation.

Normal `/travel` and realm-hub travel now submit `exploration.travel`; Go validates known/discovered routes, hub eligibility, world/realm restrictions, auction-house bypass restrictions, and updates canonical character location. The old Python `_discover_next_location` RNG/mutation helper has been removed.

Hunting now uses Go for beast generation/scaling, canonical attribute modifiers, 2d10 resolution, loot/cultivation rewards, cooldowns, Beast Binder automatic equality contracts, and wild-beast encounter creation. Python renders the authoritative receipt only.

Unexpected **personal** exploration-event rewards/effects/Karma/Fate are applied in the same Go transaction. Unexpected **world-event** and **secret-realm** activations are selected and persisted by Go. As of Partial 05B, world-event persistent civilization/economy/sect consequences and world-history recording are also applied inside Go; production Discord no longer calls `SIM.apply_random_event`.

#### Secret realms

Added:

- `secret_realm.status` authoritative query
- `secret_realm.enter`
- `secret_realm.explore`
- `secret_realm.leave`

Go now owns open-entrance lookup, realm/name normalization, location/minimum-realm validation, run creation/expiry, canonical room attributes and modifiers, room RNG/TNs, cooldowns, cultivation/stone/insight/item rewards, danger progression/recovery, room advancement, forced ejection, voluntary exit, one-time inheritance grants, permanent inheritance bonuses/items, and final-run shutdown.

Python no longer calls the old secret-realm run mutation/reward methods or rolls `roll_2d10` for secret-realm rooms.

#### World catalog support

The Go world catalog now loads the location fields needed for travel/exploration authority plus unexpected events, secret realms, rooms, and inheritances directly from `content/world.json`.


#### Character-creation authority leak closed

First-life birth-family generation is now canonical Go authority. `/begin` calls `character.family_options`, which resolves the fixed starter-household catalog in Go, persists actor-scoped opaque `choice_id` offers, and returns the canonical shared household identity for presentation. Starter names are stable: for example the Martial Household is always **Han Family** rather than receiving a fresh surname per player.

Starter archetypes are now persistent shared world entities. The first request seeds one canonical `birth_families` row per starter archetype; later players choosing that archetype join the same `family_id`, share the same household resources/history/NPC relatives, and do not create duplicate family rows. `character.create` still rejects client-supplied family payloads and consumes only the Go-issued choice ID.

Schema 20 adds shared starter-household identity plus per-guild household-thread metadata. Go-authoritative `family.household.enter` / `family.household.leave` operations move members into and out of a canonical `birth_family:<family_id>` location. Discord reuses one private household thread per family/server, adding members when they enter and removing them when they leave. Guided Scene Actions now include co-located player cultivators as valid targets, so multiple players inside the same household can interact mechanically as well as chat/RP in the shared scene.

The legacy Python `generate_family_options()` helper remains only for older test/simulation fixtures and is explicitly not used by production `/begin`. Samsara reincarnation was already Go-generated and remains Go-authoritative.

#### Persistent exploration-event continuation

Normal `exploration.explore` is now the authoritative doorway into personal random events. A selected personal event is persisted as an `exploration_events` instance with an `exploration_event_participants` row instead of immediately granting its reward. The explore response reports `kind=event_started`; while that encounter remains active, another `/explore` reports `kind=event_active` and reopens the same event instead of producing unrelated rewards or another encounter. Normal travel and hunting are also blocked until the personal event is resolved or left.

`exploration.event.status`, `exploration.event.act`, and `exploration.event.leave` are Go-authoritative. The Discord expedition journal exposes **Observe / Approach / Help / Rob Them / Leave** controls, but Python only presents the result. Rolls, one-attempt-per-action state, reward/effect application, Karma/Fate changes, resolution, abandonment, action receipts, and domain events remain inside the Go transaction. Schema 21 adds the persistent event, participant, and event-action tables.

### Authority/RNG audit

- `app/bot/main.py`: **12,546 lines** in this checkpoint.
- Direct `DB.*` references remain numerous because reads, presentation/history, admin operations, and deferred domains still live in Python; the Discord layer is not yet a thin adapter.
- Direct gameplay `secrets.*` calls in production `bot/main.py`: **7 calls across 5 source lines**, down from **9** before 05B. Autonomous world-event selection no longer contributes Python RNG.
- `roll_2d10(...)` calls in `bot/main.py`: **3**, now confined to later sect/economy-style authority work.
- The remaining direct production RNG is concentrated in auction pursuit/door complications, sect recommendation/trials, and black-market heat consequences. Legacy Python world-simulation fallback code still contains RNG but production simulation runs through Go.

### Tested

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

### Preserved from earlier authority batches

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

### Still intentionally incomplete

- `VERSION` is not promoted to final 0.15 yet.
- Autonomous world-event selection/activation/consequence persistence is now Go-owned. Event-expiry/Discord thread scheduling remains Python orchestration; the older Python autonomous NPC/world/sect/clan/economy implementations remain as fallback/legacy code while production scheduled simulation uses Go.
- Crafting, alchemy/foraging, forging, inscription, profession progression, spirit-beast lifecycle, artifact progression, PvP, manual study/techniques, forbidden-art consequences, crime atonement, and their scoped reputation/bounty mutations are now Go-authoritative.
- Sect recommendation/recruitment trials and broader sect/economy mechanics remain Python-authoritative.
- Black market, auction pursuit/door complications, and related economy/crime encounter mechanics remain Python-authoritative.
- Other special-location movement owned by later domains (for example auction/property/teleport/personal-world flows) remains with those domains; normal discovered-route and realm-hub travel is now Go-owned.
- The remaining **7 direct gameplay `secrets.*` calls across 5 lines** are expected to disappear as auction/sect/economy domains migrate.
- `bot/main.py` / database decomposition and final release/config/content-layout cleanup remain after authority migration.

### Next migration batch

The next clean mechanics slice, after the first-four migration and P0 dispatcher hardening, is:

1. Auction + black market + public economy + bounty hunters.
2. Equipment + party + formations + bosses.
3. Territories + wars + caravans.
4. Sect recruitment + sect economy + discipleship.
5. Family/clan + seclusion + Dao/Fate, then special properties/storage/arrays/personal-world authority and final Python cleanup.

### 2026-08-29 cross-loop profession / social progression pass

This build closes several player-facing progression gaps while preserving the existing authority boundary documented above:

- Non-Alchemy Forging / Formation / Inscription crafts now receive shared visible craftsmanship grades (Ordinary, Fine, Superior, Masterwork), quality points, and margin-based profession XP without duplicating durable finished items.
- Medicinal foraging now advances a persistent **Foraging** profession on both success and failure and reports the resulting rank/XP in the command receipt.
- Spirit-beast taming, training, and evolution now advance persistent **Beast Taming** mastery.
- Artifact bonding and awakening now advance persistent **Artifact Refining** mastery.
- Consent-gated nonlethal PvP now records **Martial Society** reputation for honorable completion/wins while remaining distinct from jurisdictional crime.
- Forbidden manual/technique behavior remains connected to karma, witnesses, world reaction, jurisdictional crime/evidence, bounties, and orthodox/demonic reputation consequences.

Authority note (superseded by the subsequent Go migration): crafting/foraging, spirit-beast lifecycle, artifact progression, PvP, manuals/forbidden arts, and scoped crime/reputation mutations are now Go-authoritative. Python remains orchestration/rendering for those paths.

Validation: full suite passes with **300 tests + 78 subtests**.

### 2026-08-29 P0 authoritative dispatcher hardening

- Fixed the first-four migration registry defect: the Go handlers existed in the authoritative switch but several operation names were absent from `authoritativeMutations`, causing production requests to bypass the authoritative dispatcher.
- Registered all migrated operations: `beast.tame`, `beast.feed`, `beast.train`, `beast.evolve`, `beast.active`, `artifact.bond`, `artifact.awaken`, `pvp.challenge`, `pvp.respond`, `pvp.act`, `manual.study`, `manual.technique`, and `crime.atone`.
- Added `TestFirstFourMigrationOperationsRegisteredAuthoritative` to permanently cover the complete first-four operation set, including `craft.resolve` and `forage.resolve`.
- Targeted Python boundary/feature regression: **23 passed**. Full Python output completed at **300 tests + 78 subtests passed**; Go `./...` passes.

### 2026-08-29 Partial 05A checkpoint

The corrected first-four registry/hardening state is preserved as the formal Partial 05A migration checkpoint. No world-event/combat-aftermath or later-domain authority changes are included in 05A.


### 2026-08-29 Partial 05B — world events + combat aftermath

- Added Go-authoritative `world_event.act`; canonical attributes, severity-adjusted TNs, cryptographic 2d10, participation/action records, contribution/support/investigation/interference, first-participation claims, and rewards/effects/Karma/Fate are resolved in one authoritative transaction.
- Exploration-triggered global events now apply persistent civilization/economy/sect consequences and world-history records inside Go when activated. Python only renders returned impact text.
- Event-manifestation battle victories now update world-event participation in the Go combat-finalization transaction.
- Normal NPC battle finalization now applies player-triggered NPC/family/clan/civilization/economy/sect aftermath and world-history consequences inside Go. Production Python no longer calls `SIM.apply_player_action`.
- Autonomous world-event spawn chance, eligible event/location selection, deduplication, activation, persistent world consequences, and history are now executed by the Go simulation system. Python receives spawned-event metadata only to create Discord threads.
- Added a direct Go regression proving `world_event.act` persists participation/action state and emits an authoritative action receipt.
- Validation: full Go `go test ./...` passed; targeted Python 05B tests passed **37 + 39 subtests**; full Python suite passed **300 + 78 subtests**.

---

## `PRE_FINAL_AUTHORITY_CLEANUP_STATUS.md`

## Pre-Final Authority Cleanup Status

Completed after Partials 06–11:

- Discord mutation handlers for auction/black market/public market/bounty hunters, equipment/party/formations/bosses, territory/war/caravans, sect recruitment/economy/discipleship/manor, family support/children/simulation, seclusion, Dao partnership, storage/properties/arrays/spatial keys/personal worlds now delegate to authoritative Go operations.
- Removed the Python auction-door gameplay RNG helpers and black-market/sect recruitment Python roll paths from the Discord layer. These rolls now occur in Go.
- Removed obsolete Python `Database` mutation methods for the migrated domains, including their bounty-hunter and birth-family simulation fallbacks.
- Scheduled advanced maintenance remains Go-owned.
- Corrected sect-manor defensive war power to `defense_array_level * 6` plus the existing Qi-array contribution.
- Scheduled Go seclusion settlement now loads canonical Qi/body phase costs from `content/world.json` rather than using the temporary approximation.

Validation performed:

- `python -m py_compile app/bot/main.py app/database/core.py`
- `pytest -q tests/python/contracts/test_authority_boundary.py` — pass
- `cd go_core && go test ./...` — pass

Note: older Python integration tests that directly invoke the deliberately removed `Database` gameplay mutators are now stale by design; authoritative mechanics are exercised through the Go operation layer going forward.

---

## `V015_REMAINING_DATABASE_MUTATORS.md`

## v0.15 Remaining Python `Database` Mutators — Future Go Migration Review

Static call-site review after the purge. The audited write-like set contains **68** remaining methods and **0 unreachable methods**. These should not be deleted without replacing their live production/internal callers.

### Priority 1 — live gameplay authority candidates

- `set_gender()`
- `add_items()`
- `set_location()`
- `set_cooldown()`
- `accept_quest()`
- `activate_world_event()`
- `discover_sect()`
- `set_sect_membership()`
- `clear_sect_membership()`
- `set_master()`
- `clear_master()`
- `consume_item()`
- `spend_resources()`
- `restore_resources()`
- `apply_effect()`
- `remove_effect()`
- `create_battle()`
- `add_life_extension()`
- `add_pill_toxicity()`
- `initiate_hidden_sect()`
- `set_hidden_sect_status()`

These are the first future-Go candidates because they change player/world mechanical state or gate gameplay. Several already have adjacent Go operations and should be routed through those operations rather than retained as Python mutation authority.

### Priority 2 — read APIs with write side-effects

- `get_active_effects()`
- `get_active_world_events()`
- `get_alchemy_state()`
- `get_secret_realm_run()`
- `get_social_state()`
- `get_wild_beast_encounters()`
- `get_world_clock()`
- `list_npc_player_memories()`

These look like reads but mutate expiry/decay/initialization/recall state. Future migration should split pure reads from settlement/expiry writes, or make the side effect explicitly authoritative.

### Priority 3 — admin mutations that should converge on Go admin operations

- `add_currency()`
- `adjust_karma()`
- `adjust_master_attention()`
- `admin_clear_battle()`
- `admin_revive_character()`
- `admin_teleport_character()`
- `advance_world_clock()`
- `set_sect_rank()`
- `set_storage_container()`

Where Go admin operations already exist (for example world-time advance, currency/karma/player recovery/teleport), Python should become transport/orchestration only.

### Python-side support / infrastructure writes (lower migration priority)

- `add_history()`
- `add_npc_player_memory()`
- `add_rag_memory()`
- `admin_world_snapshot()`
- `close_event_thread()`
- `create_backup()`
- `ensure_sect_abode()`
- `flush_slow_query_log()`
- `init()`
- `log_admin_action()`
- `maintenance_cleanup()`
- `record_item_provenance()`
- `record_operational_alert()`
- `record_startup_event()`
- `record_world_history_event()`
- `register_event_thread()`
- `search_rag_memories()`
- `set_abode_thread()`
- `set_address_style()`
- `set_automation_setting()`
- `set_birth_family_household_thread()`
- `set_expedition_thread()`
- `set_info_message_id()`
- `set_npc_memory()`
- `set_realm_hub_channel()`
- `set_sect_abode_thread()`
- `set_server_channels()`
- `sync_rag_canon()`
- `sync_world_catalog()`
- `update_expedition_location()`

These are primarily Discord topology, RAG/narrative history, observability, backups, or support metadata. They are not current gameplay-authority blockers unless the project adopts an “all writes in Go” rule.

### Reachability conclusion

- Unreachable write-like public methods after purge: **0**.
- `Database.create_character()` is gone; tests seed fixture rows via `tests.support.seed_character()`.
- `adjust_reputation()` and `record_crime()` were deleted in this pass after the stale Python forbidden-art service was removed and their last Python callers disappeared.

---

## `FINAL_AUTHORITY_PURGE_DELETION_LIST.md`

## v0.15 Final Python Gameplay Purge — Exact Deletion List

Generated by AST-diffing the uploaded pre-cleanup tree against the final purge tree. Original line numbers are from the uploaded archive.

### Production code deletions

#### `app/database/core.py` — definitions removed

- L2572: `Database.create_character`
- L2752: `Database.discover_location`
- L2810: `Database.learn_manual`
- L2822: `Database.practice_manual`
- L2854: `Database.set_concealment`
- L2862: `Database.add_spiritual_sense_bonus`
- L2881: `Database.reward_body_cultivation`
- L2949: `Database.reward`
- L3022: `Database.consume_and_create`
- L3111: `Database.apply_breakthrough`
- L3149: `Database.apply_body_breakthrough`
- L3259: `Database.upsert_npc_mind_state`
- L3354: `Database.set_player_scene_state`
- L3389: `Database.update_npc_relationship`
- L3479: `Database.update_quest_progress`
- L3510: `Database.start_perfection`
- L3535: `Database.add_perfection_training`
- L3557: `Database.add_perfection_preparation`
- L3572: `Database.complete_perfection_quest`
- L3598: `Database.reduce_perfection_progress`
- L3629: `Database.complete_perfection`
- L3649: `Database.abandon_perfection`
- L3687: `Database.start_body_perfection`
- L3712: `Database.add_body_perfection_training`
- L3734: `Database.add_body_perfection_preparation`
- L3749: `Database.complete_body_perfection_quest`
- L3775: `Database.reduce_body_perfection_progress`
- L3799: `Database.complete_body_perfection`
- L3818: `Database.abandon_body_perfection`
- L3906: `Database.record_world_event_action`
- L4283: `Database.claim_world_event`
- L4296: `Database.start_secret_realm_run`
- L4326: `Database.advance_secret_realm_run`
- L4334: `Database.set_secret_realm_danger`
- L4342: `Database.leave_secret_realm`
- L4347: `Database.grant_inheritance`
- L4828: `Database.apply_vitality_damage`
- L4836: `Database.apply_battle_damage`
- L5367: `Database.save_root_profile`
- L5389: `Database.save_bloodline_profile`
- L5413: `Database.save_physique_profile`
- L5435: `Database.train_aptitude`
- L5498: `Database.harmonize_aptitude`
- L5676: `Database.ensure_sect`
- L5761: `Database.adjust_fate`
- L5792: `Database.spend_fate`
- L5844: `Database.reward_master_for_disciple_breakthrough`
- L5911: `Database.rotate_black_market`
- L5983: `Database.claim_periodic_bucket`
- L6019: `Database.finalize_expired_auctions`
- L6046: `Database.add_auction_door_risk`
- L6050: `Database.get_pending_auction_door_risks`
- L6054: `Database.consume_auction_door_risk`
- L6104: `Database.update_battle`
- L6115: `Database.claim_battle_finalization`
- L6137: `Database.create_player_family`
- L6156: `Database.invite_player_family_member`
- L6168: `Database.accept_player_family_invite`
- L6177: `Database.decline_player_family_invite`
- L6181: `Database.reorder_player_family_member`
- L6193: `Database.leave_player_family`
- L6209: `Database.set_life_status`
- L6220: `Database.create_family_child`
- L6235: `Database.set_child_awakening`
- L6255: `Database.add_law_comprehension`
- L6271: `Database.spend_currency`
- L6344: `Database.start_seclusion`
- L6397: `Database.advance_seclusion`
- L6430: `Database.end_seclusion`
- L6534: `Database.advance_samsara_time`
- L6592: `Database.record_true_death`
- L6745: `Database.awaken_soul_memory`
- L6759: `Database.reincarnate_character`
- L7060: `Database.apply_condition`
- L7145: `Database.set_condition_severity`
- L7197: `Database.add_tribulation_preparation`
- L7214: `Database.record_tribulation_attempt`
- L7258: `Database.record_profession_practice`
- L7375: `Database.record_alchemy_batch`
- L7429: `Database.adjust_reputation`
- L7456: `Database.record_crime`
- L7503: `Database.resolve_crime`
- L7543: `Database.add_grudge`
- L7601: `Database.add_spirit_beast`
- L7638: `Database.train_spirit_beast`
- L7650: `Database.evolve_spirit_beast`
- L7665: `Database.set_active_spirit_beast`
- L7674: `Database.create_wild_beast_encounter`
- L7728: `Database.resolve_wild_beast_taming`
- L7789: `Database.bond_artifact`
- L7808: `Database.awaken_artifact`
- L7821: `Database.ensure_territory`
- L7866: `Database.settle_player_caravans`
- L7894: `Database.adjust_social_state`
- L7923: `Database.start_world_era`
- L7964: `Database.create_pvp_challenge`
- L7974: `Database.respond_pvp_challenge`
- L8017: `Database.apply_pvp_turn`
- L8200: `Database.damage_equipment`
- L8301: `Database.advance_territory_wars`
- L8316: `Database.advance_territory_occupations`
- L8354: `Database.advance_caravans`
- L8409: `Database.advance_world_era`
- L8498: `Database.advance_advanced_world_systems`

#### `app/simulation/world.py` — definitions removed

- L393: `WorldSimulator._simulate_black_markets`
- L559: `WorldSimulator._mark_system_run`
- L567: `WorldSimulator._simulate_civilization`
- L703: `WorldSimulator._simulate_npc_lives`
- L1137: `WorldSimulator._simulate_economy`
- L1163: `WorldSimulator._simulate_sects`
- L1201: `WorldSimulator._simulate_clans`
- L1250: `WorldSimulator.apply_player_action`
- L1543: `WorldSimulator.apply_forbidden_art_use`
- L1649: `WorldSimulator.apply_random_event`
- L1903: `WorldSimulator.market_trade`

#### `app/core_services.py` — definitions removed

- L351: `class CultivationService`
- L354: `CultivationService.__init__`
- L358: `CultivationService.reward`
- L363: `class SectService`
- L366: `SectService.__init__`
- L369: `SectService.admit_outer_disciple`

#### `app/bot/main.py` — definitions removed

- L4945: `_apply_event_participation`

#### `app/forbidden_arts.py` — whole file deleted

- L11: `_clamp`
- L15: `manual_is_forbidden`
- L22: `technique_is_forbidden`
- L41: `manual_study_karma_cost`
- L45: `forbidden_use_karma_cost`
- L49: `exposure_score`
- L53: `witness_chance_percent`
- L59: `resolve_witnessed`
- L71: `effective_exposure`
- L85: `reaction_policy`
- L109: `crime_evidence`
- L113: `crime_severity`
- L117: `reputation_deltas`
- L126: `class ForbiddenUseResult`
- L136: `ForbiddenUseResult.impacts`
- L140: `class ForbiddenArtsService`
- L148: `ForbiddenArtsService.__init__`
- L152: `ForbiddenArtsService.record_manual_study`
- L172: `ForbiddenArtsService.resolve_technique_use`

### Pytest deletions / conversions

#### `tests/python/integration/test_advanced_forward_port.py` — whole file deleted

- L13: `class AdvancedForwardPortTests`
- L14: `AdvancedForwardPortTests.asyncSetUp`
- L28: `AdvancedForwardPortTests.asyncTearDown`
- L31: `AdvancedForwardPortTests.test_01_catalog_matches_advanced_branch_scale`
- L44: `AdvancedForwardPortTests.test_02_technique_executability_matrix_uses_39_subtests`
- L60: `AdvancedForwardPortTests.test_03_catalog_sync_seeds_sqlite_territories_and_world_era`
- L72: `AdvancedForwardPortTests.test_04_spirit_beast_contract_training_activation_and_evolution`
- L86: `AdvancedForwardPortTests.test_05_artifact_provenance_and_dao_heart_state_persist`
- L109: `AdvancedForwardPortTests.test_06_territory_war_and_caravan_state_are_persistent`
- L128: `AdvancedForwardPortTests.test_07_party_membership_and_leadership_transfer`
- L139: `AdvancedForwardPortTests.test_08_pvp_requires_consent_and_creates_nonlethal_turn_match`

#### `tests/python/integration/test_alchemy_beast_expansion.py` — definitions removed from retained test file

- L111: `AlchemyBeastExpansionTests.test_alchemy_batches_track_quality_and_outputs`
- L130: `AlchemyBeastExpansionTests.test_wild_beast_encounter_can_become_equality_contract`

#### `tests/python/integration/test_aptitudes.py` — definitions removed from retained test file

- L104: `class AptitudeDatabaseTests`
- L105: `AptitudeDatabaseTests.asyncSetUp`
- L137: `AptitudeDatabaseTests.asyncTearDown`
- L140: `AptitudeDatabaseTests.test_profile_round_trip_and_atomic_tempering`
- L150: `AptitudeDatabaseTests.test_harmonization_reduces_persistent_rejection`

#### `tests/python/integration/test_battle.py` — definitions removed from retained test file

- L47: `BattleDatabaseTests.test_character_creation_persists_male_or_female_birth_sex`
- L73: `BattleDatabaseTests.test_exact_battle_binding_rejects_stale_or_foreign_panels`
- L80: `BattleDatabaseTests.test_damage_and_recovery_keep_character_and_panel_vitality_in_sync`
- L95: `BattleDatabaseTests.test_spare_or_kill_finalization_can_only_be_claimed_once`

#### `tests/python/integration/test_completed_advanced_systems.py` — whole file deleted

- L16: `class CompletedAdvancedSystemsTests`
- L17: `CompletedAdvancedSystemsTests.asyncSetUp`
- L37: `CompletedAdvancedSystemsTests.asyncTearDown`
- L40: `CompletedAdvancedSystemsTests.test_01_equipment_loadout_durability_breakage_and_repair`
- L60: `CompletedAdvancedSystemsTests.test_02_formation_positions_stance_and_cohesion_are_persistent`
- L74: `CompletedAdvancedSystemsTests.test_03_multiphase_party_boss_engine_rounds_phase_changes_and_rewards`
- L110: `CompletedAdvancedSystemsTests.test_04_autonomous_bounty_hunter_spawns_escalates_and_accepts_player_response`
- L145: `CompletedAdvancedSystemsTests.test_05_territory_siege_armies_and_occupation_resolve_control`
- L172: `CompletedAdvancedSystemsTests.test_06_caravan_escorts_interception_smuggling_taxes_and_event_history`
- L198: `CompletedAdvancedSystemsTests.test_07_world_eras_transition_automatically_and_record_events`
- L222: `CompletedAdvancedSystemsTests.test_08_slow_query_observability_persistence_and_external_webhook_alerting`

#### `tests/python/integration/test_connected_systems.py` — whole file deleted

- L19: `class ConnectedSystemsTests`
- L20: `ConnectedSystemsTests.asyncSetUp`
- L36: `ConnectedSystemsTests.asyncTearDown`
- L39: `ConnectedSystemsTests.test_schema_v7_contains_connected_system_tables_and_partner_echo_columns`
- L51: `ConnectedSystemsTests.test_fate_is_capped_logged_and_spendable`
- L60: `ConnectedSystemsTests.test_player_disciple_contract_and_breakthrough_reward_connect`
- L72: `ConnectedSystemsTests.test_dao_partnership_dual_cultivation_builds_resonance_for_samsara`
- L83: `ConnectedSystemsTests.test_location_array_consumes_item_and_is_shared_by_location`
- L98: `ConnectedSystemsTests.test_black_market_trade_connects_wallet_inventory_and_provenance`
- L114: `ConnectedSystemsTests.test_four_realm_hubs_map_discord_channels_to_canonical_locations`
- L127: `ConnectedSystemsTests.test_content_catalog_connects_realm_hubs_and_formation_inscription`

#### `tests/python/integration/test_core_services.py` — definitions removed from retained test file

- L81: `CoreServiceTests.test_persistent_npc_mind_and_salient_player_memories`
- L126: `CoreServiceTests.test_property_scene_keeps_entrance_as_physical_location`

#### `tests/python/integration/test_event_specific_gui.py` — definitions removed from retained test file

- L38: `EventSpecificGuiPersistenceTests.test_event_actions_persist_and_aggregate`
- L60: `EventSpecificGuiPersistenceTests.test_event_aftermath_becomes_world_history`

#### `tests/python/integration/test_events.py` — definitions removed from retained test file

- L209: `RandomEventPersistenceTests.test_beast_tide_persists_regional_and_market_damage`

#### `tests/python/integration/test_forbidden_arts.py` — definitions removed from retained test file

- L15: `class ForbiddenArtsTests`
- L16: `ForbiddenArtsTests.asyncSetUp`
- L31: `ForbiddenArtsTests.asyncTearDown`
- L33: `ForbiddenArtsTests.test_content_contains_demonic_manuals_sects_and_world_rules`
- L39: `ForbiddenArtsTests.test_manual_learning_and_mastery_persist`
- L47: `ForbiddenArtsTests.test_witnessed_forbidden_art_hurts_region_and_family`

#### `tests/python/integration/test_missing_systems.py` — whole file deleted

- L12: `class MissingSystemsDatabaseTests`
- L13: `MissingSystemsDatabaseTests.asyncSetUp`
- L26: `MissingSystemsDatabaseTests.asyncTearDown`
- L29: `MissingSystemsDatabaseTests.test_persistent_condition_uses_generic_effect_pipeline`
- L51: `MissingSystemsDatabaseTests.test_tribulation_preparation_and_clearance_persist`
- L67: `MissingSystemsDatabaseTests.test_profession_mastery_is_separate_from_realm`
- L80: `MissingSystemsDatabaseTests.test_crime_evidence_can_create_bounty_and_grudge`

#### `tests/python/integration/test_npc_autonomous_life.py` — whole file deleted

- L17: `class NPCAutonomousLifeTests`
- L18: `NPCAutonomousLifeTests.asyncSetUp`
- L26: `NPCAutonomousLifeTests.asyncTearDown`
- L29: `NPCAutonomousLifeTests.test_schema_is_v16`
- L32: `NPCAutonomousLifeTests.test_life_tables_and_initial_state_exist`
- L43: `NPCAutonomousLifeTests.test_natural_aging_can_kill_and_write_history`
- L55: `NPCAutonomousLifeTests.test_friendship_can_become_marriage_and_create_descendant`
- L95: `NPCAutonomousLifeTests.test_grudge_can_cause_real_fight_and_injury`
- L129: `NPCAutonomousLifeTests.test_trusted_higher_rank_npc_can_take_disciple`

#### `tests/python/integration/test_player_property_system.py` — definitions removed from retained test file

- L43: `PlayerPropertySystemTests.test_property_type_facilities_guests_and_upgrades_persist`
- L84: `PlayerPropertySystemTests.test_discord_setup_and_private_property_routing_are_wired`

#### `tests/python/integration/test_private_scene_routing.py` — definitions removed from retained test file

- L71: `PrivateSceneRoutingTests.test_discord_gui_routes_explore_action_and_read_only_info`

#### `tests/python/integration/test_sect_manor.py` — whole file deleted

- L22: `class SectManorTests`
- L23: `SectManorTests.asyncSetUp`
- L53: `SectManorTests.asyncTearDown`
- L56: `SectManorTests.test_schema_migration_includes_manor_tables`
- L64: `SectManorTests.test_establishment_spends_shared_treasury_and_is_visible_to_members`
- L80: `SectManorTests.test_only_sect_master_or_ancestor_can_establish`
- L86: `SectManorTests.test_elder_can_upgrade_but_disciple_cannot`
- L100: `SectManorTests.test_facility_benefit_scaling`

#### `tests/python/integration/test_sect_recruitment.py` — whole file deleted

- L22: `class SectRecruitmentTests`
- L23: `SectRecruitmentTests.asyncSetUp`
- L36: `SectRecruitmentTests.asyncTearDown`
- L39: `SectRecruitmentTests.test_schema_v8_persists_discovery_recommendations_and_attempts`
- L58: `SectRecruitmentTests.test_all_public_sects_have_story_recruitment_configuration`
- L68: `SectRecruitmentTests.test_recommender_npcs_exist_for_every_sect`
- L73: `SectRecruitmentTests.test_trial_modifiers_reward_matching_path_family_and_recommendation`
- L87: `SectRecruitmentTests.test_trial_outcome_allows_sponsor_backed_conditional_pass`
- L92: `SectRecruitmentTests.test_source_contains_story_and_npc_recommendation_gui_paths`

#### `tests/python/integration/test_world_access_scene_action.py` — definitions removed from retained test file

- L30: `WorldAccessAndSceneActionTests.test_schema_v7_tracks_location_discovery`

#### `tests/python/integration/test_world_history_rag.py` — definitions removed from retained test file

- L140: `WorldHistoryRagTests.test_territory_war_start_writes_history`

#### `tests/python/integration/test_worldsim.py` — whole file deleted

- L18: `class WorldSimulationIntegrationTests`
- L19: `WorldSimulationIntegrationTests.asyncSetUp`
- L27: `WorldSimulationIntegrationTests.asyncTearDown`
- L30: `WorldSimulationIntegrationTests._create_player`
- L50: `WorldSimulationIntegrationTests.test_initialization_builds_all_simulation_catalogs`
- L58: `WorldSimulationIntegrationTests.test_named_npcs_initialize_with_persistent_mind_state`
- L65: `WorldSimulationIntegrationTests.test_ordinary_market_excludes_secret_keys_and_auction_only_items`
- L71: `WorldSimulationIntegrationTests.test_dynamic_market_trade_changes_wallet_inventory_and_supply`
- L90: `WorldSimulationIntegrationTests.test_martial_clan_records_exist_immediately_when_ensured`
- L100: `WorldSimulationIntegrationTests.test_reincarnation_resets_old_social_and_money_state_and_seeds_target_world_currency`
- L161: `WorldSimulationIntegrationTests.test_eleven_distinct_xianxia_family_archetypes_exist`
- L168: `WorldSimulationIntegrationTests.test_alchemy_family_support_provides_medicinal_supplies`
- L175: `WorldSimulationIntegrationTests.test_killing_family_head_causes_persistent_clan_setback_and_blood_feud`
- L199: `WorldSimulationIntegrationTests.test_family_heads_are_real_local_battle_targets`
- L205: `WorldSimulationIntegrationTests.test_seclusion_session_is_persistent_and_background_rate_is_bounded`

#### `tests/python/unit/test_core_mechanics_modules.py` — definitions removed from retained test file

- L42: `class _FakeDB`
- L43: `_FakeDB.__init__`
- L48: `_FakeDB.adjust_karma`
- L52: `_FakeDB.record_crime`
- L57: `_FakeDB.adjust_reputation`
- L62: `class _FakeSimulator`
- L63: `_FakeSimulator.__init__`
- L66: `_FakeSimulator.apply_forbidden_art_use`
- L71: `class ForbiddenArtsCoreTests`
- L72: `ForbiddenArtsCoreTests.test_content_driven_reaction_profiles_are_used`
- L94: `ForbiddenArtsCoreTests.test_orthodox_technique_does_not_trigger_forbidden_consequences`
- L117: `ForbiddenArtsCoreTests.test_forbidden_technique_centralizes_karma_world_crime_and_reputation`

#### `tests/python/unit/test_profession_crossloops.py` — definitions removed from retained test file

- L44: `ProfessionCrossLoopTests.test_cross_loop_profession_progress_persists_and_levels`
- L53: `ProfessionCrossLoopTests.test_consent_duel_reputation_can_record_honor_without_crime`

### Non-deletion conversions that replace removed implementation tests

- `tests/support.py`: added test-only `seed_character(...)`; all surviving pytest character fixtures use it instead of production `Database.create_character()`.
- `tests/python/contracts/test_simulation_boundary.py`: added engine-delegation tests for `WorldSimulator.run_due()` / `force_run()` and mandatory engine construction.
- `tests/python/contracts/test_authority_boundary.py`: expanded negative authority guards for purged `Database` mutators, purged `WorldSimulator` methods, removed services, the deleted forbidden-art service module, and Go `caravan.settle` delegation.
- `tests/python/integration/test_rag_v1.py`: manual-learning retrieval fixture now seeds the read-model row directly instead of calling the removed gameplay mutator.

**Totals:** 263 top-level/class definitions removed across 26 Python files; production `Database` methods removed: 104.

---

## `V015_FINAL_RELEASE_READINESS_AUDIT.md`

## v0.15 Final Release-Readiness Audit

Audit date: 2026-08-29

### Verdict

**Python gameplay implementation purge: PASS.** The requested no-engine simulation fallback, stale Go-duplicated services/mutators, direct mutator pytest, and production `Database.create_character()` fixture path are removed and guarded against reintroduction.

**Automated code verification: PASS.** Final Python suite: **223 passed + 39 subtests**. Authority/simulation boundary suite: **5 passed**. `go test ./...`: PASS. `go vet ./...`: PASS. `bash -n startup.sh stop.sh update.sh`: PASS. `content/world.json`: valid JSON. Docker Compose validation was not run because Docker is not installed in this sandbox.

**Final v0.15 release sign-off: HOLD.** The code is clean for this purge milestone, but the tree is not yet a truthful final v0.15 release and strict “all gameplay RNG/authority in Go” is not yet achieved.

### Release blockers

1. **Release identity is still v0.14.** `VERSION`, `app/version.py`, Docker image labels, Compose labels, the release-version contract test, and the top-level README still identify the release as 0.14. `PARTIAL_BUILD.txt` explicitly says “NOT A FINAL RELEASE.” These markers must be deliberately promoted together when v0.15 is actually cut.
2. **Production-reachable Python gameplay RNG still exists.** `WorldSimulator._npc_mood()` randomizes seeded NPC mood during `initialize()`; `WorldSimulator.ensure_all_clans()` randomizes clan branch/retainer/relationship data; `WorldSimulator.alchemy_forage_profile()` chooses the rare forage item/chance in Python before calling Go `forage.resolve`; `birthfamily.inherited_root()` rolls a child root in Python before Go `family.add_child`. These are the clearest remaining strict-authority blockers.
3. **Live Python `Database` gameplay/admin mutators remain.** The purge removed every statically unreachable write-like public method, but 68 write-like methods remain production/internal-reachable. They are catalogued in `V015_REMAINING_DATABASE_MUTATORS.md` for future Go migration rather than unsafe deletion.

### Requested purge outcomes

- `WorldSimulator` no-engine gameplay fallback: **removed**; `engine` is mandatory and `run_due()` / `force_run()` delegate to Go.
- Simulation pytest: **converted** to Python↔Go boundary coverage; authoritative mechanics remain covered by native Go simulation tests (`go_core/internal/simulation/world_test.go`).
- Test-only `CultivationService` / `SectService`: **removed**.
- Dead migrated `ForbiddenArtsService`: **removed** with its stale implementation tests.
- Test use of `Database.create_character()`: **replaced** by test-only `seed_character()`.
- Unreachable Go-owned `Database` mutators: **removed**; final static audit reports **0** unreachable write-like public methods.
- Pytest that directly exercised purged mutators: **deleted or converted**.
- `test_authority_boundary.py`: **strengthened** to make the purged methods/services/fallbacks a negative contract.
- Caravan settlement production caller: migrated from `DB.settle_player_caravans()` to Go `caravan.settle`.

### Test delta

The uploaded pre-cleanup tree had **25 failing pytest cases** (275 passed) concentrated in stale Python gameplay-implementation tests. The final purge tree has **223 passing tests + 39 passing subtests** with no failures; the lower test count is intentional removal of tests whose subject was deleted Python authority, not loss of Go mechanic coverage.

### Recommended next authority pass

Move the four production RNG paths above first, then route Priority-1/2 methods from `V015_REMAINING_DATABASE_MUTATORS.md` through authoritative Go operations. Only after that should the version/release markers be promoted to 0.15 and the final package be labeled a release.

---

## `V016_AUTHORITY_DELTA.md`

## v0.16 Authority Delta from the v0.15 Final-Purge Tree

### Python removals / caller conversions

#### `app/simulation/world.py`
- removed `_npc_mood()`
- removed `WorldSimulator.ensure_all_clans()`
- removed `WorldSimulator.alchemy_forage_profile()`
- removed gameplay use of `secrets`
- NPC seed rows now use `mood='pending'`; `initialize()` requires `engine.bootstrap_simulation()` to resolve Go-owned bootstrap randomness

#### `app/birthfamily.py`
- removed `inherited_root()`

#### `app/bot/main.py`
- character creation: replaced `SIM.ensure_all_clans(...)` with `ENGINE.bootstrap_simulation(...)`
- reincarnation: replaced `SIM.ensure_all_clans(...)` with `ENGINE.bootstrap_simulation(...)`
- `/alchemy forage`: stopped computing/sending TN, regional resources, rare selection, or loot outcome
- family child creation: stopped computing/sending spiritual root, realm index, or phase
- promoted user-facing release references to v0.16

#### `app/game_engine.py`
- added mandatory `bootstrap_simulation(game_minute)` client call to `/v1/simulation/bootstrap`

### Go authority additions

#### `go_core/internal/simulation/bootstrap.go`
- added Go-owned NPC mood bootstrap
- added Go-owned martial-clan branch generation
- added Go-owned retainer group generation
- added Go-owned clan relation generation/history recording
- bootstrap is idempotent
- retainer group allocation fills the canonical requested total exactly

#### `go_core/internal/simulation/world.go`
- added NPC role/personality data to the native simulation catalog

#### `go_core/internal/server/server.go`
- added `POST /v1/simulation/bootstrap`

#### `go_core/internal/game/crafting_actions.go`
- `forage.resolve` now owns regional resource lookup, world tier, TN, resource bonus, common yield, rare pool/chance, 2d10 roll, final loot award, cooldown write, and profession progression
- rejects client-supplied `tn`, `spirit_resources`, `loot`, and `rare_found`

#### `go_core/internal/game/family_dao_actions.go`
- `family.add_child` now owns inherited-root RNG and child lifespan roll
- rejects client-supplied `spiritual_root`, `realm_index`, and `phase`

### Test conversions / additions

#### Python
- strengthened `tests/python/contracts/test_authority_boundary.py` negative contracts for deleted methods and forbidden payload ownership
- updated simulation-engine test doubles to implement the mandatory bootstrap boundary
- removed the stale pytest that directly tested Python forage-profile implementation
- release-version contract now requires v0.16 everywhere

#### Go
- added bootstrap ownership/idempotence coverage, including exact retainer totals
- added forage authority rejection/result ownership coverage
- added family-child inherited-root authority rejection/result ownership coverage

### Release metadata
- `VERSION`: 0.16
- `app/version.py`: 0.16
- Docker/Compose release labels: 0.16
- README/environment/user-facing release references: 0.16
- obsolete top-level partial-build marker removed
- schema remains 21

---

## `V016_REMAINING_DATABASE_MUTATORS.md`

## v0.16 Remaining Python `Database` Mutators — Future Go Migration Review

Revalidated static call-site review at the v0.16 release cut. The v0.16 authority changes do not make any additional member of this retained list unreachable. The audited write-like set contains **68** remaining methods and **0 unreachable methods**. These should not be deleted without replacing their live production/internal callers.

### Priority 1 — live gameplay authority candidates

- `set_gender()`
- `add_items()`
- `set_location()`
- `set_cooldown()`
- `accept_quest()`
- `activate_world_event()`
- `discover_sect()`
- `set_sect_membership()`
- `clear_sect_membership()`
- `set_master()`
- `clear_master()`
- `consume_item()`
- `spend_resources()`
- `restore_resources()`
- `apply_effect()`
- `remove_effect()`
- `create_battle()`
- `add_life_extension()`
- `add_pill_toxicity()`
- `initiate_hidden_sect()`
- `set_hidden_sect_status()`

These are the first future-Go candidates because they change player/world mechanical state or gate gameplay. Several already have adjacent Go operations and should be routed through those operations rather than retained as Python mutation authority.

### Priority 2 — read APIs with write side-effects

- `get_active_effects()`
- `get_active_world_events()`
- `get_alchemy_state()`
- `get_secret_realm_run()`
- `get_social_state()`
- `get_wild_beast_encounters()`
- `get_world_clock()`
- `list_npc_player_memories()`

These look like reads but mutate expiry/decay/initialization/recall state. Future migration should split pure reads from settlement/expiry writes, or make the side effect explicitly authoritative.

### Priority 3 — admin mutations that should converge on Go admin operations

- `add_currency()`
- `adjust_karma()`
- `adjust_master_attention()`
- `admin_clear_battle()`
- `admin_revive_character()`
- `admin_teleport_character()`
- `advance_world_clock()`
- `set_sect_rank()`
- `set_storage_container()`

Where Go admin operations already exist (for example world-time advance, currency/karma/player recovery/teleport), Python should become transport/orchestration only.

### Python-side support / infrastructure writes (lower migration priority)

- `add_history()`
- `add_npc_player_memory()`
- `add_rag_memory()`
- `admin_world_snapshot()`
- `close_event_thread()`
- `create_backup()`
- `ensure_sect_abode()`
- `flush_slow_query_log()`
- `init()`
- `log_admin_action()`
- `maintenance_cleanup()`
- `record_item_provenance()`
- `record_operational_alert()`
- `record_startup_event()`
- `record_world_history_event()`
- `register_event_thread()`
- `search_rag_memories()`
- `set_abode_thread()`
- `set_address_style()`
- `set_automation_setting()`
- `set_birth_family_household_thread()`
- `set_expedition_thread()`
- `set_info_message_id()`
- `set_npc_memory()`
- `set_realm_hub_channel()`
- `set_sect_abode_thread()`
- `set_server_channels()`
- `sync_rag_canon()`
- `sync_world_catalog()`
- `update_expedition_location()`

These are primarily Discord topology, RAG/narrative history, observability, backups, or support metadata. They are not current gameplay-authority blockers unless the project adopts an “all writes in Go” rule.

### Reachability conclusion

- Unreachable write-like public methods after purge: **0**.
- `Database.create_character()` is gone; tests seed fixture rows via `tests.support.seed_character()`.
- `adjust_reputation()` and `record_crime()` were deleted in this pass after the stale Python forbidden-art service was removed and their last Python callers disappeared.

### v0.16 release disposition

This list is retained as an explicit post-v0.16 migration backlog. These methods are live/non-duplicated support, admin, side-effecting-read, or gameplay mutation paths; deleting them without first replacing their callers would be unsafe. They therefore do not belong to the v0.16 dead-code purge.

---

## `V016_FINAL_RELEASE_READINESS_AUDIT.md`

## v0.16 Final Release-Readiness Audit

Audit date: 2026-08-29

### Verdict

**v0.16 release sign-off: PASS.** The release markers are promoted consistently to 0.16, the obsolete partial-build marker is removed from the shipping root, all four production-reachable Python RNG/authority blockers identified by the v0.15 final audit have been moved behind Go authority, and the requested Python gameplay implementation purge remains enforced by negative boundary contracts.

The remaining live Python `Database` mutators are an explicitly documented future-migration backlog. The v0.16 release criterion is that no confirmed Go-duplicated or statically unreachable Python gameplay mutator remains from this purge, not that every infrastructure/admin/support write has already been moved to Go.

### Blockers from the v0.15 audit

1. **Release identity was v0.14 / partial:** **CLOSED.** `VERSION`, `app/version.py`, Docker image label, Compose labels, health metadata, release tests, README, environment example, and user-facing version strings are now 0.16. `PARTIAL_BUILD.txt` has been removed from the release package; the v0.15 review documents remain under `docs/history/`.
2. **Four production-reachable Python gameplay RNG paths:** **CLOSED.** NPC mood and clan bootstrap now execute in Go; forage outcome/profile RNG now executes in Go; child-root inheritance now executes in Go.
3. **Remaining Python `Database` mutators:** **RECLASSIFIED AS DOCUMENTED FUTURE MIGRATION.** The retained set is live/non-duplicated and is reviewed in `V016_REMAINING_DATABASE_MUTATORS.md`; zero statically unreachable write-like methods from the audited set remain.

### Authority checks

- `WorldSimulator` requires an engine; there is no Python no-engine gameplay fallback.
- Production has no `WorldSimulator._npc_mood`, `ensure_all_clans`, or `alchemy_forage_profile` implementation.
- Production has no `birthfamily.inherited_root` implementation.
- Character creation/reincarnation invoke the Go simulation bootstrap instead of a Python clan mutator.
- Python `/alchemy forage` does not supply TN, regional spirit resources, loot, or rare result to Go.
- Python family-child creation does not supply spiritual root, realm index, or phase to Go.
- Go rejects those forbidden client-supplied mechanical/outcome fields.
- New Go tests cover bootstrap ownership/idempotence, exact retainer totals, child-root authority rejection, and forage authority rejection/result ownership.
- The v0.15 purge targets (`CultivationService`, `SectService`, `ForbiddenArtsService`, direct `Database.create_character()` fixture path, deleted Go-owned simulation methods) remain absent.

### Production Python RNG review

Python modules still contain RNG helpers used by legacy/test-only content utilities (`birthfamily`, `samsara`, aptitude generation, creation UI rolls, and `World` random helper methods), plus non-gameplay uniqueness tokens in the database support layer. Static call-site review found no production callers for the legacy/test-only gameplay RNG entry points. The production-reachable RNG blockers identified in the prior audit are gone.

### Automated verification

- Python full suite: **222 passed + 39 subtests**.
- Go full suite: **PASS**.
- `go vet ./...`: **PASS**.
- Shell syntax (`startup.sh`, `stop.sh`, `update.sh`): **PASS**.
- `content/world.json`: **valid JSON**.
- Python compilation (`app`, `tests`): **PASS**.
- Docker Compose rendering: **SKIPPED** because Docker is not installed in the audit sandbox.

### Release identity

- Release: **0.16**
- Schema: **21**
- Schema migration in this release: **none**
- Release marker: `RELEASE.txt`

### Conclusion

The v0.16 source tree is internally consistent with the requested migration boundary and is ready to package as the final v0.16 release. Future authority work should start from the live/non-duplicated mutator list rather than deleting those methods without replacement.

---

## `V016_RELEASE_NOTES.md`

## Xianxia RP v0.16 — Final Release Notes

Release date: 2026-08-29

### Release theme

v0.16 completes the final authority-cleanup pass requested after the v0.15 migration checkpoint. The release removes the remaining production-reachable Python gameplay RNG blockers identified by the v0.15 audit and promotes the package from a partial checkpoint to a truthful final release marker.

### Authority changes

- Added a native Go `/v1/simulation/bootstrap` boundary for initial NPC moods and martial-clan branch/retainer/relation construction.
- `WorldSimulator.initialize()` now seeds deterministic base rows and then requires the Go bootstrap; Python no longer rolls NPC moods or clan topology.
- Character creation and reincarnation call the Go simulation bootstrap directly after creating/attaching the new family state.
- Moved inherited child spiritual-root selection into Go `family.add_child`.
- `family.add_child` rejects client-supplied `spiritual_root`, `realm_index`, and `phase` mechanical fields.
- Moved foraging region profile, TN, resource bonus, rare candidate selection, rare chance, roll, and awarded loot into Go `forage.resolve`.
- `forage.resolve` rejects client-supplied `tn`, `spirit_resources`, `loot`, and `rare_found` outcome fields.
- Fixed a clan-bootstrap under-fill bug discovered by the new Go test: retainer groups now fill the canonical requested total exactly.

### Python purge delta

Removed or kept absent from production Python:

- `WorldSimulator._npc_mood()`
- `WorldSimulator.ensure_all_clans()`
- `WorldSimulator.alchemy_forage_profile()`
- `birthfamily.inherited_root()`
- the old no-engine `WorldSimulator` gameplay fallback
- test-only `CultivationService` and `SectService`
- dead `ForbiddenArtsService`
- `Database.create_character()` test fixture usage
- previously identified unreachable Go-owned `Database` mutators

Boundary tests assert that these implementations cannot silently return.

### Compatibility

- Release version: **0.16**
- Database schema: **21**
- No schema migration is introduced by v0.16.
- Existing schema-21 saves remain the target format.

### Validation

- `python -m pytest -q`: **222 passed, 39 subtests passed**
- `go test ./...`: **PASS**
- `go vet ./...`: **PASS**
- `bash -n startup.sh stop.sh update.sh`: **PASS**
- `python -m json.tool content/world.json`: **PASS**
- `python -m compileall -q app tests`: **PASS**
- Docker Compose rendering: **not run** because Docker is unavailable in the release sandbox.

---

## `V017_COMPARISON_AND_MERGE.md`

## v0.15 → v0.16 Functional Comparison and v0.17 Merge

### Executive summary

v0.16 is a mixed authority-hardening release. It correctly moves several remaining outcome/RNG paths into Go, but it also reintroduces Python-owned canonical SQL mutation paths that v0.15 had already removed.

v0.17 keeps the v0.16 improvements while restoring the stronger v0.15 authority boundary.

#### Release-stage verdict

- **v0.15:** release-ready Go-authority migration checkpoint.
- **v0.16:** release-ready RNG/mechanics hardening checkpoint with authority regressions.
- **v0.17 recommended:** consolidation of the strongest behavior from both.

No schema migration is required; schema remains **21**.

### Archive comparison

After excluding `__pycache__`, `.pytest_cache`, and `.pyc` artifacts:

| Metric | v0.15 | v0.16 |
|---|---:|---:|
| Meaningful files | 177 | 177 |
| Shared paths | 166 | 166 |
| Byte-identical shared files | 125 | 125 |
| Changed shared files | 41 | 41 |
| Release-only paths | 11 | 11 |
| Python tests | 236 passed + 78 subtests | 222 passed + 39 subtests |
| Go tests | pass | pass |

The raw v0.15 archive contains additional generated cache files; those are not source/content differences.

### Complete runtime/configuration change ledger

| Area / file | v0.15 → v0.16 functional change | Classification | v0.17 decision |
|---|---|---|---|
| `app/birthfamily.py` | Deletes Python `inherited_root()` RNG implementation. | **Improvement** | Keep deletion. |
| `app/bot/main.py` – forage | Stops Python from selecting forage TN, regional resources, loot, and rare result; sends contextual inputs to Go. | **Improvement** | Keep v0.16 path. |
| `go_core/internal/game/crafting_actions.go` | Go calculates region resources, world tier, TN, common quantity, rare pool/chance/RNG, roll, loot mutation, and profession progress; rejects client `tn`, `spirit_resources`, `loot`, `rare_found`. | **Improvement** | Keep. |
| `app/bot/main.py` – child | Stops calculating/sending child spiritual root/realm/phase. | **Improvement** | Keep. |
| `go_core/internal/game/family_dao_actions.go` | Go selects inherited root and lifespan roll; rejects client `spiritual_root`, `realm_index`, `phase`. | **Improvement** | Keep. |
| `app/bot/main.py` – caravan | Changes settlement to `authoritative_action()` with a Discord action id and explicit `GameEngineError` handling. | **Improvement** | Keep. |
| `app/game_engine.py` | Bootstrap API shrinks from `game_minute + npcs + sects` to `game_minute`. | **Improvement in API shape** | Keep the small request, but make Go load canonical world catalog itself. |
| `app/simulation/world.py` – constructor | `engine` becomes a required keyword argument instead of optional. | **Improvement** | Keep. |
| `app/simulation/world.py` – bootstrap | Reintroduces direct Python SQL writes for simulation systems, civilization regions, NPC civilization/mind/life, sect politics/factions/relations, and economy markets, then invokes Go only for mood/clan completion. | **Major regression** | Restore full Go bootstrap. Python sends only `game_minute`. |
| `go_core/internal/simulation/bootstrap.go` | Removes v0.15 full simulation bootstrap writes and narrows Go bootstrap to pending moods/clans. | **Major regression despite RNG improvements** | Recombine v0.15 full bootstrap with v0.16 Go RNG/clan logic. |
| `app/simulation/world.py` – interval | Replaces Go `admin.simulation.interval` mutation with direct Python SQL. | **Major regression** | Restore Go operation. |
| `app/database/core.py` + `app/bot/main.py` – karma | Reintroduces `Database.adjust_karma()` and routes admin command away from Go. | **Major regression** | Remove Python mutator; restore `admin.player.karma`. |
| same – currency | Reintroduces `Database.add_currency()` and routes grant away from Go. | **Major regression** | Remove; restore `admin.player.grant_currency`. |
| same – world clock | Reintroduces `Database.advance_world_clock()` and routes time advance away from Go. | **Major regression** | Remove; restore `admin.world.advance_time`. |
| same – teleport | Reintroduces `Database.admin_teleport_character()`. | **Major regression** | Remove; restore `admin.player.teleport`. |
| same – revive | Reintroduces `Database.admin_revive_character()`. | **Major regression** | Remove; restore `admin.player.revive`. |
| same – clear battle | Reintroduces `Database.admin_clear_battle()`. | **Major regression** | Remove; restore `admin.player.clear_battle`. |
| same – automation | Reintroduces `Database.set_automation_setting()`. | **Major regression** | Remove; restore `admin.automation.set`. |
| `app/database/core.py` – black market | Deletes unused `rotate_black_market()`. | **Improvement / dead-code purge** | Keep deleted. |
| `app/database/core.py` – auction risk | Deletes unused `get_pending_auction_door_risks()`. | **Improvement / dead-code purge** | Keep deleted. |
| `app/simulation/world.py` – forage helper | Deletes Python `alchemy_forage_profile()` and its `secrets` RNG. | **Improvement** | Keep deleted. |
| `app/simulation/world.py` – NPC mood | Deletes Python `_npc_mood()` and `secrets.choice`. | **Improvement** | Keep deleted; use Go. |
| `go_core/internal/simulation/world.go` | Adds NPC role/personality to the Go simulation catalog. | **Improvement** | Extend further in v0.17 so Go can bootstrap canonical NPC/sect state from `world.json`. |
| `go_core/internal/simulation/bootstrap.go` – mood | Uses Go `gamerng` for pending NPC moods and activity-sensitive mood options. | **Improvement** | Keep. |
| same – clans | Uses Go RNG for branch stats, retainer roles, and clan relations; fills retainer target exactly. | **Improvement** | Keep exact allocation and RNG. |
| same – branch names | v0.16 has only five branch labels and reuses `"Distant Branch"` when more branches are needed. | **Regression** | Restore unique cadet labels through the supported branch count. |
| v0.16 Python NPC bootstrap age | Uses rich Xianxia lifespan-based `initial_age_years()`, while current Go death simulation still uses a much smaller realm-adjusted threshold. High-realm NPCs can therefore start above the Go death threshold. | **Cross-model regression** | Keep v0.15's conservative Go bootstrap age until lifespan/death models are unified. |
| `go_core/internal/server/server.go` | Removes bootstrap `http.MaxBytesReader`; returns bootstrap counters instead of only `{"status":"ok"}`. | **Mixed** | Keep result counters; restore a 1 MiB request cap. |
| `.env.example`, `Dockerfile`, `VERSION`, `app/version.py`, `docker-compose.yml`, `update.sh` | Release marker 0.15 → 0.16. | **Expected metadata** | Promote to 0.17. |
| `README.md` | Updates release description/status; contains a stale “No Ollama ... v0.14” line inherited/introduced in the edit and mixed authority claims. | **Documentation issue** | Normalize current release text and schema 21 wording. |
| `app/core_services.py` | Whitespace-only difference. | **No functional change** | No behavioral merge needed. |

### Authority regressions in v0.16

#### 1. Python regained full simulation-bootstrap SQL ownership

v0.15 delegated simulation bootstrap to Go. v0.16's `WorldSimulator.initialize()` directly writes canonical state tables before calling Go.

Affected tables include:

- `world_simulation_state`
- `civilization_regions`
- `npc_civilization_state`
- `npc_mind_state`
- `npc_life_state`
- `sect_politics_state`
- `sect_factions`
- `sect_relations`
- `economy_markets`

This contradicts the intended one-way authority boundary.

#### 2. Python regained simulation-interval mutation

v0.15 used `admin.simulation.interval`. v0.16 directly updates `world_simulation_state.interval_game_minutes`.

#### 3. Seven supported GM/admin mutations moved back to Python SQL

The v0.16 bot uses new/reintroduced `Database` mutation methods for karma, currency, time advance, teleport, revive, battle clearing, and automation settings. v0.15 already had Go operations for all seven.

#### 4. Boundary tests weakened around exactly those paths

v0.15 explicitly asserted that the deleted admin mutators were absent and that simulation bootstrap/interval code could not call `._connect()`. v0.16 removed those guards while adding good forage/child authority checks. The removed guards allowed the authority regressions above to return.

#### 5. Coverage decreased

v0.16 removed multiple older integration suites. Some removals were stale tests for deleted Python authority APIs, but several sect/content/observability-style tests represented real coverage. v0.17 restores compatible useful suites and keeps the new v0.16 boundary tests.

### v0.17 merge design

v0.17 starts from v0.16 so it retains the newer Go mechanics, then restores the stronger v0.15 authority paths.

#### Kept from v0.16

- Go-owned forage profile/outcome RNG and forbidden payload fields.
- Go-owned child root inheritance and forbidden payload fields.
- Go-owned NPC mood RNG.
- Go-owned clan branch/retainer/relation RNG.
- Exact retainer allocation.
- Caravan settlement action idempotency/error handling.
- Mandatory `WorldSimulator` engine injection.
- Dead Python black-market and auction-risk helpers remain removed.
- Small bootstrap client request (`game_minute` only).

#### Restored or strengthened from v0.15

- Full canonical simulation bootstrap is Go-owned.
- Simulation interval mutation is Go-owned.
- Seven supported Discord GM/admin mutation paths are Go-owned.
- Strict Python authority-boundary tests return.
- Useful compatible integration tests return.
- Go caravan authority test returns.
- Full bootstrap Go test coverage returns.
- Request-size protection returns.

#### New v0.17 synthesis

- Go's simulation catalog now loads the NPC/sect/location/realm metadata required for full bootstrap directly from canonical `content/world.json`.
- The bootstrap request no longer accepts caller-provided NPC/sect maps.
- Full bootstrap, mood initialization, and clan initialization run in one Go transaction.
- Bootstrap returns counters for systems, regions, NPCs, sect states, markets, moods, clan branches, retainer groups, and relations.
- Clan branch names remain unique rather than repeating `Distant Branch`.
- Conservative NPC bootstrap ages avoid the current lifespan/death-model mismatch.
- Bootstrap HTTP body is capped at 1 MiB.
- Current docs are versioned as v0.17; v0.15/v0.16 release audits live under `docs/history/`.

### Test-file delta notes

v0.16 adds `tests/python/contracts/test_simulation_boundary.py`, which is retained.

v0.16 removes these v0.15 test files:

- `tests/python/integration/test_advanced_forward_port.py`
- `tests/python/integration/test_completed_advanced_systems.py`
- `tests/python/integration/test_connected_systems.py`
- `tests/python/integration/test_missing_systems.py` (contained no tests)
- `tests/python/integration/test_sect_manor.py`
- `tests/python/integration/test_sect_recruitment.py`
- `go_core/internal/game/caravan_authority_test.go`
- `go_core/internal/simulation/bootstrap_test.go`

v0.17 restores the compatible useful tests while retaining v0.16's new authority tests.

### v0.17 validation

Validation performed on the assembled tree:

| Check | Result |
|---|---|
| `python -m pytest -q` | **237 passed, 78 subtests passed** |
| `go test ./...` | **PASS** |
| `go vet ./...` | **PASS** |
| Python source/test compile | **PASS** |
| `bash -n startup.sh stop.sh update.sh` | **PASS** |
| `content/world.json` JSON parse | **PASS** |
| Schema | **21**, no migration |

### Remaining design gaps

These are not v0.15→v0.16 regressions, but are sensible future hardening targets:

1. `forage.resolve` still accepts caller-provided context/mechanical inputs such as `context_bonus`, `realm_index`, `garden_level`, and `location`. Go owns the random outcome and canonical mutation, but a future version could derive more of those values from canonical state.
2. NPC creation/bootstrap lifespan logic and ongoing Go NPC-death thresholds should be unified before restoring rich high-realm starting ages.
3. Continue migrating any remaining intentionally scoped Python database writes only when a Go canonical authority operation exists and tests can enforce the one-way boundary.

---

## `V017_REMAINING_AUTHORITY_GAPS.md`

## v0.17 Remaining Authority / Model Gaps

This document records non-blocking follow-up work. These are not known release regressions in v0.17.

### Forage inputs

`forage.resolve` owns the roll, TN derivation, resource lookup, loot plan, rare RNG, awards, cooldown mutation, and profession progress in Go. It still accepts `context_bonus`, `realm_index`, `garden_level`, and `location` from orchestration.

A stricter future boundary could derive some or all of these from canonical Go-readable state.

### NPC lifespan model

The richer Python helper for starting age can produce ages appropriate to very long-lived high-realm cultivators. The current Go NPC-life death threshold is simpler and lower.

v0.17 therefore deliberately uses the conservative v0.15-style bootstrap age. A future release should unify:

- realm/stage lifespan extension;
- natural lifespan;
- starting age distribution;
- death/old-age checks.

Only after those use one model should richer starting ages be restored.

### Remaining Python writes

Python still contains database operations that are outside the specifically migrated canonical gameplay/admin paths. They should not be mechanically deleted.

Future migrations should use this sequence:

1. define a Go authority operation;
2. move validation + canonical mutation into Go;
3. migrate callers;
4. add a negative boundary test;
5. remove the old Python mutator only when no live caller depends on it.

---

## `V017_RELEASE_NOTES.md`

## v0.17 Recommended Release Notes

### Purpose

v0.17 is a consolidation release built from the strongest parts of v0.15 and v0.16. It keeps v0.16's Go-owned RNG/mechanics improvements while repairing the canonical-mutation regressions introduced in v0.16.

### Authority changes

- Restored full Go-owned simulation bootstrap.
- Restored Go-owned simulation interval changes.
- Restored Go-owned GM/admin operations for karma, currency, world-time advance, teleport, revive, clear-battle, and automation settings.
- Preserved Go-owned forage TN/resources/loot/rare RNG.
- Preserved Go-owned inherited child spiritual-root RNG.
- Preserved Go-owned NPC mood and clan bootstrap RNG.
- Python bootstrap now sends only `game_minute`; Go loads canonical world catalog data itself.

### Correctness and hardening

- Preserved exact retainer allocation from v0.16.
- Restored unique clan branch naming.
- Uses conservative NPC bootstrap ages until the lifespan/death models are unified.
- Restored bootstrap request-size protection with a 1 MiB cap.
- Preserved caravan settlement action-id idempotency/error handling.
- Recombined strict v0.15 authority guards with v0.16 forage/child payload guards.

### Compatibility

- Release version: **0.17**
- Database schema: **21**
- Database migration: **none**

### Validation

- Python: **237 passed, 78 subtests passed**
- Go: `go test ./...` **PASS**
- Go vet: **PASS**
- Python compile: **PASS**
- Shell syntax checks: **PASS**
- `content/world.json`: **valid JSON**

---

## `V018_AUTHORITY_CLEANUP_ROADMAP.md`

## v0.18 Staged Authority Cleanup Roadmap

### Stage 1 — Forage canonical inputs — BUILT

Go owns canonical forage context.

### Stage 2 — Forage effect authority — BUILT + AUDITED

`forage.resolve` derives persisted aptitude/effect/toxicity context in Go.

### Stage 3 — Craft authority — BUILT + ADVERSARIALLY AUDITED

`craft.resolve` accepts only `recipe`; Go owns context, RNG, inventory, quality, and progression.

### Stage 4 / 4.5 — Companion authority — BUILT + ADVERSARIALLY AUDITED

All companion/artifact mutations use canonical actor/context/cooldowns, strict payload allowlists,
living-actor checks, replay protection, and Go-owned mutation.

### Stage 5 — Canonical game-time authority — BUILT + VALIDATED

Authoritative gameplay mutations and queries reject caller `game_minute`. Go derives canonical
current time before dispatch/replay; scheduler/import simulation time remains explicit protocol data.

### Stage 6 — Unified NPC lifespan authority — BUILT + AUDITED

One Go lifespan package now owns:
- natural lifespan input;
- realm/stage lifespan extension;
- effective lifespan ceilings;
- starting/bootstrap age bounds;
- old-age expiry/death checks.

Player lifespan projection, NPC lifespan query, NPC bootstrap, and NPC natural-death simulation use
the shared model. Richer high-realm bootstrap ages are therefore bounded by the same model that later
evaluates natural death.

### Stage 6 — Multi-hop roads, costs, and caravans — BUILT + AUDITED

Canonical road routing uses server-owned pathfinding and per-leg travel profiles. Go derives:
- route nodes and hops;
- travel duration;
- danger and encounter resolution;
- spirit-stone route cost;
- discovery of traversed locations;
- canonical departure/arrival and transit enforcement.

Caravan dispatch uses the canonical road graph for route, ETA, operating cost, and risk. Dispatch
quantity/escort bounds are enforced in Go, caller duration is rejected, and only living incarnations
may dispatch.

### Stage 7 — Admin-dashboard-owned Discord setup — BUILT + AUDITED

The bot no longer creates guild text channels/categories or repairs channel permission overwrites.
Configured channels are validated/bound from dashboard-owned setup. Startup no longer runs realm-hub
channel provisioning. Private gameplay threads inside configured channels remain supported.

### Stage 8 — Remove obsolete Python authority code — BUILT + AUDITED

Deleted Python lifespan authority modules and migrated live lifespan callers to Go. Contract/static
scans ensure removed lifespan helpers are not retained by production or test support and existing
authority-boundary tests verify migrated gameplay mutators are absent from Python.

### Migration rule used for each stage

1. strengthen the Go authority operation;
2. derive canonical validation/context in Go;
3. migrate Python callers;
4. add forged-input/identity/state negative tests;
5. remove Python helpers only after repository scans prove them dead;
6. run Go test/vet plus the complete Python suite.

---

## `V018_STAGE1_NOTES.md`

## v0.18 Stage 1 Notes — Forage Input Authority

### Before

The v0.17 `alchemy_forage` orchestration supplied five mechanical inputs to `forage.resolve`:

- `context_bonus`
- `location`
- `realm_index`
- `garden_level`
- `cooldown_seconds`

Go owned the roll, TN/resource lookup, loot, rare RNG, awards, profession progress, and cooldown write,
but a direct caller could still forge several upstream inputs. Go also did not reject a second forage
attempt based on the existing cooldown; Python performed that pre-check.

### After

The Discord caller sends only:

```json
{"effect_bonus": <active-effect contribution>}
```

Go now:
1. loads the canonical character;
2. rejects non-living characters;
3. enforces the existing `alchemy_forage` cooldown;
4. derives the physical location and realm index;
5. resolves an accessible cave abode and canonical base region;
6. derives herb-garden level;
7. derives Alchemy Family herb-lore bonus;
8. computes the combined context bonus;
9. performs the existing regional resource, TN, RNG, loot, inventory, and profession mutations;
10. writes a fixed 20-minute cooldown.

The result returns the derived authority inputs so Python can present them without recomputing mechanics.

### New negative boundary

Go rejects client-supplied:
- `context_bonus`
- `location`
- `realm_index`
- `garden_level`
- `cooldown_seconds`
- existing forged outcome fields (`tn`, `spirit_resources`, `loot`, `rare_found`)

Python contract tests also prohibit the old forage input construction.

### Validation

- Python: `237 passed, 78 subtests passed`
- Go: `go test ./...` PASS
- Go: `go vet ./...` PASS

---

## `V018_STAGE2_NOTES.md`

## v0.18 Stage 2 Notes — Forage Effect Authority

### Before

Stage 1 removed forged forage location/realm/property/family/cooldown inputs, but Python still
computed an `effect_bonus` and submitted it to `forage.resolve`.

That remaining value came from `current_effect_modifiers()`, which also settled pill-toxicity decay
and synchronized the `pill_toxicity` active-effect row during what appeared to be a read operation.

### After

The Discord forage handler submits:

```json
{}
```

Go now calculates the complete forage effect contribution at canonical world time.

#### Effect sources resolved by Go

1. persisted active effects whose time window is currently active;
2. spiritual-root mutation modifiers;
3. active/evolved bloodline modifiers;
4. active/evolved physique modifiers and drawbacks;
5. the currently deployed location array at the character's physical location;
6. pill toxicity after canonical world-time decay.

Only additive `alchemy_bonus` values are used for forage, matching the prior Python
`aggregate_modifiers(...).get("alchemy_bonus", 0)` behavior, including stack multiplication and
integer truncation semantics.

#### Pill-toxicity authority

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

#### Active-effect reads

`Database.get_active_effects()` now:
- selects only effects with `starts_game_minute <= now`;
- excludes effects whose end minute has passed;
- does not delete expired rows during a read.

This makes `current_effect_modifiers()` read-only from Python's perspective while preserving a
canonical current effect view.

### Boundary enforcement

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

### Validation

- Python: 238 passed, 78 subtests passed.
- Go: `go test ./...` PASS.
- Go: `go vet ./...` PASS.
- Database schema: 21, no migration.

---

## `V018_STAGE3_NOTES.md`

## v0.18 Stage 3 Notes — Craft Resolve Authority

### Before

`craft.resolve` owned the roll, materials, outputs, quality, Alchemy batch bookkeeping, and
profession progression, but Python still supplied three canonical/mechanical inputs:

- `context_bonus`;
- `location`;
- `game_minute`.

Python built `context_bonus` from active effects, aptitude effects, a deployed location array,
player-property access/facilities, sect-manor facilities, and the Alchemy Family tradition.

That allowed a direct caller to forge the crafting modifier, crafting location, and event time.

### After

The Discord crafting handler submits only:

```json
{"recipe": "<catalog recipe name>"}
```

Go now derives all crafting context inside the authoritative transaction.

#### Canonical inputs derived by Go

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

#### Client fields now forbidden

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

#### Canonical Alchemy bookkeeping

`alchemy_batches.location`, `alchemy_batches.game_minute`,
`alchemy_state.last_toxicity_game_minute`, and the crafting domain event now use Go-derived
canonical values.

#### Python cleanup

`_run_crafting()` no longer calls:

- `current_effect_modifiers()`;
- `DB.get_abode_by_location()`;
- `DB.can_access_abode()`;
- `DB.get_birth_family()`;
- `DB.get_member_sect_manor()`;
- `DB.get_profession_progress()`.

The handler renders facility/family/mastery breakdowns from the authoritative result.

### Boundary tests

Stage 3 adds tests that:

- reject each forged mechanical/context field;
- derive Alchemy effect, property, manor, family, profession, location, and time from persisted Go
  state;
- settle toxicity before Alchemy context calculation;
- write canonical Alchemy batch location/time;
- reject crafting for a deceased canonical character;
- enforce that the Python craft handler no longer constructs the removed payload fields.

### Schema/version

- Database schema remains 21.
- No migration is required.
- Runtime release label remains v0.17 for this cumulative v0.18 development checkpoint.

---

## `V018_STAGE45_NOTES.md`

## v0.18 Stage 4.5 Notes

### Companion authority contract

`go_core/internal/game/beast_artifact_actions.go` now uses strict per-operation payload allowlists
(`validateCompanionPayload`, line 40) and a shared canonical living-actor loader
(`loadLivingCompanionActor`, line 60).

Current accepted payloads:

| Operation | Client fields |
|---|---|
| `beast.tame` | `encounter_id` |
| `beast.feed` | `beast_id`, `food` |
| `beast.train` | `beast_id` |
| `beast.evolve` | `beast_id` |
| `beast.active` | `beast_id` |
| `artifact.bond` | `item_id` |
| `artifact.awaken` | `item_id`, `spirit_name` |

All other fields are rejected.

Tame verifies canonical encounter owner, status, expiry against canonical world time, and current
character location. Feed/train/bond use server-defined cooldown constants. Training derives Beast Pen
level only from the cave abode at the character's actual current location and only with owner/guest
access.

Evolve/active/awaken now derive current actor/time/location too. Repeat artifact awakening is rejected
(line 588) so awakening cannot be farmed repeatedly for profession XP/resonance.

### Read-side hardening

`Database.get_wild_beast_encounters` (`app/database/core.py`, line 5262) no longer changes encounter
status. It filters `expires_game_minute` in the SELECT. The authoritative `beast.tame` mutation is
responsible for deciding whether an encounter is expired.

### Stage 3 hardening

`craft.resolve` (`go_core/internal/game/crafting_actions.go`, line 267) now treats `recipe` as its
entire payload allowlist (line 275). This prevents new/unknown caller fields from slipping through a
mechanical-field blacklist.

### Family city roads

`LocationDefinition` now loads a `roads` list from `content/world.json`.

Each current world's 11 family cities form one connected bidirectional road network. Higher-world
realm capitals also connect into their world's family-city network. Road references are same-world
and point only to canonical catalog locations.

`knownLocationsTx` exposes direct road neighbors as reachable known routes. Successful
`exploration.travel` persists the destination with discovery kind `road_travel`.

Python autocomplete mirrors the canonical road-neighbor visibility for UI presentation.

### Hub threshold hardening

Newborn-safe family cities intentionally have `min_realm_index = 0`, including higher worlds.
`worldMinRealm` now prefers that world's realm-hub threshold, preventing those cities from lowering
the direct `mode=hub` unlock threshold.

### Schema

- Schema version: 22.
- Schema 22 remains the family-homeland migration.
- No additional schema migration is needed for Stage 4.5 roads or companion hardening.

---

## `V018_STAGE5_NOTES.md`

## v0.18 Stage 5 Notes — Canonical Time + Road Authority

### Authority boundary

Stage 5 is implemented at the common authoritative Go boundary instead of duplicating clock reads in
every legacy handler.

For every authoritative mutation:
1. reject caller `game_minute`;
2. derive the canonical world minute under the authoritative transaction;
3. internally inject that minute only for legacy handlers whose structs still carry
   `json:"game_minute"`;
4. execute the mutation;
5. stamp the domain event with the same canonical action minute.

Stage 3/4 operations that already own all context keep their strict payload validators and receive no
injected mechanical fields.

Every authoritative query rejects caller `game_minute`. Current-time queries such as
`sense.status`, `character.lifespan`, and `effects.current` derive/read canonical state in Go.

`quest.progress` is a legacy non-authoritative action endpoint but represented one remaining
caller-time path. It now rejects caller time and reads the canonical clock.

Simulation bootstrap/run-due/force retain explicit time because that value is an authoritative
scheduler/simulation input, not a player-provided current-time claim.

### Replay behavior

Caller time is rejected before replay lookup. Reusing a valid action ID with a forged `game_minute`
therefore fails instead of returning the stored receipt.

### Direct-road profile

A direct road has a server-derived travel/danger profile. Terrain sets the base duration and danger;
higher worlds add both duration and danger; cultivation realm reduces them within capped bounds.
Safe-zone-to-safe-zone roads receive a danger reduction.

Current bounds:
- minimum travel duration: 30 game-minutes;
- danger/encounter chance: 5–45 percent.

### Transit enforcement

Road travel does not advance the shared world clock. Instead, Go stores
`world_state["road_transit:<actor_id>"]` with origin, destination, departure minute, and arrival
minute.

Until canonical time reaches arrival, every authoritative mutation for that actor fails with the
remaining journey time. On the first mutation at or after arrival, the transit state is deleted and
normal dispatch continues.

This makes duration mechanical without letting one player's journey fast-forward the world for
everyone else and without adding a schema migration.

### Encounters

The road encounter roll is server-owned and uses the derived danger percentage. Encounter selection
is also server-owned.

- `blocked_road`: delay only.
- `qi_weather`: delay and nonlethal Vitality damage.
- `spirit_beast`: delay and nonlethal Vitality damage.
- `road_ambush`: delay and nonlethal Vitality damage.

Encounter delay extends the canonical arrival minute. Damage is clamped so a road encounter cannot
reduce Vitality below 1; true death remains exclusively lifecycle-authoritative.

Each triggered road encounter is persisted as an `event_log` row with `event_type=road_encounter`.

### Python orchestration

Direct `authoritative_action` payloads in `app/` no longer contain `game_minute`. The
`GameEngineClient.authoritative_action` method also rejects such payloads client-side as a defense in
depth.

Legacy query calls for `character.lifespan` and `sense.status` now send empty payloads. The quest
service no longer sends time to `quest.progress`.

The `/travel` command displays the canonical travel duration, danger, encounter probability,
arrival minute, transit state, and encounter consequences.

### Schema

Schema remains 22.

---

## `V018_STAGE678_NOTES.md`

## v0.18 Stages 6–8 Implementation Notes

### Stage 6

- Added shared Go lifespan authority used by player/NPC projection, NPC bootstrap, and old-age death.
- Restored bounded richer high-realm NPC bootstrap ages.
- Added multi-hop canonical road routing with server-derived duration, danger, encounters, costs,
  discovery, and transit enforcement.
- Added canonical caravan routing/ETA/operating costs.
- Hardened caravan dispatch with Go-side quantity/escort bounds and living-actor enforcement.

### Stage 7

- Discord guild channel/category creation is dashboard-owned.
- Bot setup validates/binds existing configured channels.
- Removed channel permission-overwrite repair and startup realm-channel auto-provision behavior.
- Private gameplay threads remain supported.

### Stage 8

- Removed obsolete Python lifespan authority modules.
- Migrated lifespan consumers to Go.
- Removed stale test-support dependency on the deleted Python lifespan model.
- Kept Python orchestration/read surfaces where they do not duplicate Go mechanical authority.

See `V018_BUILD_HISTORY.md` (consolidated Stage 6-8 validation/audit record).

---

## `V018_BUILD_HISTORY.md`

## v0.18 Build History (consolidated)

This file replaces 19 separate per-stage AUDIT/VALIDATION/CHECKPOINT files that
accumulated during the v0.18 build. Those files were largely redundant with
each other (multiple validation passes reporting on the same stage) and with
the six `V018_STAGE*_NOTES.md` files, which are kept as-is — they document
*why* each authority boundary was built the way it was and remain the
higher-value record. This file keeps only what those NOTES files don't
already cover: what each stage's own test run claimed, and a note on which
claims have since actually been independently re-verified.

**Important caveat:** the "reported" lines below are what the original build
process's own docs claimed (`go test`, `go vet`, `pytest` pass counts). They
were not independently re-run before this consolidation. Where something
*was* independently verified in this repository (by running the actual code,
not reading it), that is called out explicitly and separately.

### Stage-by-stage (design rationale in the matching `*_NOTES.md`)

- **Stage 1 — Forage input authority.** Removed forgeable `context_bonus`/
  `location`/`realm_index`/`garden_level`/`cooldown_seconds` from the client
  payload; Go derives all of it. Reported: 237 passed, 78 subtests; `go
  test`/`go vet` PASS.
- **Stage 2 — Forage effect authority.** Moved `effect_bonus` computation
  (active effects, bloodline/physique modifiers, deployed arrays, pill
  toxicity decay) fully into Go; added the read-only `effects.current` query.
  Reported: 238 passed, 78 subtests; PASS. Audited line-by-line before Stage 3
  (`V018_STAGE2_AUDIT.md`, folded in here) with no unresolved findings noted.
- **Stage 3 — Craft resolve authority.** Same treatment as Stage 2, applied to
  `craft.resolve`: location, game_minute, and every facility/family/mastery
  bonus now Go-derived. Client sends only the recipe name.
- **Stage 4 / 4.5 — Companion & artifact authority + family-city roads.**
  Introduced per-operation payload allowlists (`validateCompanionPayload`)
  and a shared canonical living-actor loader for beast/artifact actions; added
  family-city travel integration. An adversarial audit against Stages 3+4
  together (`V018_STAGE34_ADVERSARIAL_AUDIT.md`) and a dedicated checkpoint
  doc were folded into this entry; both reported a clean pass with no
  unresolved findings.
- **Stage 5 — Canonical time + road authority.** Centralized canonical-time
  enforcement at the shared authoritative boundary (`rejectCallerGameMinute`
  + `stage5CanonicalizeMutationPayload`) instead of duplicating clock reads
  per handler; added direct-road duration/danger/encounter integration.
  **This boundary was independently re-verified this session** — traced the
  full `applyAuthoritative` dispatch path directly and confirmed every
  non-exempt mutation is forced onto canonical `game_minute`, and separately
  confirmed by exhaustive check (not sampling) that all 9 operations in the
  `stage5CanonicalTimeNativeOperations` exemption list correctly resolve
  their own canonical time internally via `canonicalWorldGameMinute` rather
  than silently reading a zero-value field.
- **Stages 6–8 — Lifespan authority, road/caravan/dashboard, Python
  cleanup.** Added the shared Go lifespan model (later extended this session
  with the activity-pause clock and actual old-age death enforcement, since
  the original Stage 6 work covered NPC natural-death only — see
  `V018_LIFESPAN_PAUSE_FIX.md`'s intent, now implemented). Added
  dashboard-owned Discord setup and began Python dead-code removal for
  migrated authority. An adversarial audit and two validation passes for
  Stages 6–8 were folded into this entry; all reported clean.

### Release-level

- **`V018_RELEASE_VALIDATION.md`** (folded in): final regression pass
  promoting the audited Stage 6–8 checkpoint to release, reported clean.
- **`V018_RELEASE_SECURITY_LOAD_AUDIT.md`** (folded in): a security/load
  pass dated 2026-08-30, reported no findings. Not independently re-run.
- **`V018_REMAINING_BUILD.md`** (folded in, now superseded): originally
  stated the required staged authority work was complete as of Stage 8. That
  is still true for the *migration* work; it does not reflect the dashboard
  coverage, fate-system, and lifespan-death work added in this session (see
  below).

### Feature validation docs folded in (post-release additions)

These described real, implemented features (each checked and confirmed
actually present in the code, not just claimed) but their own "N passed"
test claims are, again, not independently re-run:

- `V018_ANCESTRAL_DYNASTY_CONFLICT_VALIDATION.md`, `V018_SAMSARA_DYNASTY_FOUR_WORLD_VALIDATION.md`,
  `V018_UPPER_SAMSARA_LINEAGE_VALIDATION.md` — the samsara dynasty/lineage system.
- `V018_DYNASTY_RISK_RESOLUTION_VALIDATION.md` — dynasty quest/conflict roll-based resolution
  (independently reviewed and fixed this session — see the conversation record for the
  rounds-bias balance fix; the roll mechanics themselves checked out sound).
- `V018_DASHBOARD_IMPLEMENTATION_GATE_VALIDATION.md`, `V018_DASHBOARD_SYSTEM_COVERAGE_VALIDATION.md` —
  the `dashboard_contract.py` coverage gate. **Independently re-verified this session** by
  actually running `dashboard_implementation_issues()` (pure-stdlib Python, runs directly),
  repeatedly, after each dashboard change — this one's claims are the most solidly confirmed
  of anything in this file, because it's the one part of the stack this environment could
  actually execute rather than only read.
- `V018_LIFESPAN_PAUSE_FIX.md` — superseded by the actual implementation now in
  `lifespan.go`/`authoritative.go` (activity-pause clock plus real old-age death enforcement
  routed through `recordTrueDeathAuthoritative`), which goes further than this doc's original
  scope (it only covered the pause display, not death enforcement).
- `V018_MORTAL_CAPITAL_DISCOVERY_IMAGE_VALIDATION.md` — confirmed real: both the packaged
  asset (`assets/locations/azure_crown_imperial_city.png`) and the matching code in
  `app/bot/main.py` (`LOCATION_DISCOVERY_IMAGES`) exist as described.

### What this session added on top of the above (not covered by any prior doc)

- Fixed the forage rare-find bug (result reported a rare item on a failed roll).
- Implemented player old-age death enforcement (was previously display-only), routed through
  the existing true-death/samsara pipeline.
- Renamed GM→Admin dashboard terminology throughout.
- Fixed the dynasty conflict system's rounds-based balance bias (opponent-only escalation).
- Added the `admin.player.fate` GM grant path (`fate.adjust` had zero callers anywhere).
- Closed five dashboard coverage gaps: birth-family household threads/current occupants, and
  new Party & Formations / PvP / Conditions views.
- Wired `npc.lifespan` into the existing `/admin npc npcinspect` command (was an orphaned query).
- Ran a targeted RNG audit (found and fixed the dynasty "danger" display gap; found no other
  instance of a displayed-but-unconsumed risk value in `go_core/internal/game`), an IDOR
  sample audit (caravan/dynasty/pvp/party all clean via consistent safe patterns), and an
  exhaustive canonical-time audit (all 9 time-exempt operations correctly self-resolve).

---

## `STAGE.md`

## v0.18 — Final Release

The staged v0.18 authority roadmap is complete and this tree is promoted to the final v0.18 release.

### Completed authority stages

- Stage 1: canonical forage inputs.
- Stage 2: forage effect authority.
- Stage 3: `craft.resolve` recipe-only authority.
- Stage 4/4.5: companion/artifact cooldown, context, and adversarial hardening.
- Stage 5: canonical Go-owned game-time authority.
- Stage 6: unified player/NPC lifespan authority plus multi-hop roads and caravans.
- Stage 7: Discord channel/category setup moved to the admin dashboard; bot auto-provisioning removed.
- Stage 8: obsolete Python authority code removed after Go migration.

### Release hardening

The final release gate additionally:
- defaults standalone Go-engine binding to `127.0.0.1:8081`;
- keeps Docker's Go engine on the private Compose network via explicit `ENGINE_ADDR=:8081`;
- enforces bounded JSON request bodies on all Go JSON mutation/data endpoints;
- rejects trailing extra JSON values instead of accepting an ambiguous request body.

### Validation

See `V019_RELEASE_NOTES.md` for the current release and `V018_BUILD_HISTORY.md`
for the consolidated prior build/validation record.

Release version: 0.19.
Schema version: 24 (unchanged from v0.18).

### Samsara dynasty extension

The v0.18 patch line now includes persistent cross-incarnation ancestry, ancestral investigation
sites/quests, evidence-gated inheritance/restoration/revenge claims, and replacement-house legacy
conflicts. Replacement families remain blood-unrelated when the historical branch is extinct.

### v0.19 — Stage 18 consistency/coverage pass, folded into this release

On top of the v0.18 release above, v0.19 (see `V019_RELEASE_NOTES.md`) adds: real old-age
death enforcement (previously display-only), a fixed dynasty-quest RNG gap and a
dynasty-conflict rounds-based balance fix, a GM fate-grant path (`admin.player.fate`), five
closed dashboard coverage gaps (household threads/occupants, Party & Formations, PvP,
Conditions), the `npc.lifespan` query wired into Discord's NPC inspect command, a
package-wide RNG/IDOR/canonical-time audit, and a full cultivation-depth audit (realm
breakthroughs, tribulations, aptitude, techniques) that found and fixed five further bugs.

---

## `V018_RELEASE_NOTES.md`

## Xianxia RP v0.18 Release Notes

Release date: 2026-08-30  
Schema: 24

### Authority completion

v0.18 completes the staged migration of gameplay authority into Go.

- Forage resolves from canonical actor/location/world data.
- Crafting accepts the canonical recipe contract and derives profession, facility, cost, roll,
  output, quality, toxicity, and progression in Go.
- Companion/artifact mutations use canonical living-actor, location, cooldown, inventory, encounter,
  access, and progression state.
- Authoritative gameplay mutations and queries reject caller-owned current `game_minute`; Go derives
  canonical current time. Explicit scheduler/import/simulation event time remains protocol data where
  historically required.
- Player and NPC lifespan projection, NPC bootstrap age, and natural old-age death share one Go model.

### Roads, travel, and caravans

- Direct and multi-hop routing is server-owned.
- Per-leg duration, danger, encounters, travel costs, discovery, and transit timing are canonical.
- Transit locks prevent acting at a destination before canonical arrival.
- Caravans use canonical route, ETA, operating cost, risk, payout basis, and settlement.
- Caravan quantity/escort bounds and living-actor checks are enforced at the Go boundary.

### Discord setup and Python cleanup

- Discord guild channel/category setup is owned by the admin dashboard.
- The bot no longer creates guild text channels/categories or repairs channel permission overwrites.
- Private gameplay threads inside configured channels remain supported.
- Obsolete Python lifespan authority modules and their hidden test-support fallback were removed.

### Release security hardening

- Standalone Go engine defaults to `127.0.0.1:8081` instead of all interfaces.
- Docker continues to bind the engine inside the private Compose network.
- Every Go JSON mutation/data endpoint has a request-body size limit.
- JSON decoding rejects trailing extra values.
- SQLite busy timeout is configured before journal-mode setup, preventing false readiness failures
  during concurrent connection pressure.
- `python-dotenv` now requires 1.2.2+, excluding the patched 2026 symlink-overwrite advisory range.
- Existing dashboard/control tokens remain constant-time compared and dashboard publishing remains
  loopback-only by default.

### Samsara dynasty investigation and claims patch

Schema 23 persists cross-incarnation dynasty history. Schema 24 makes that history playable:

- archive, ancestral-ruin, last-heir-tomb, and surviving-retainer leads are generated per investigated transition;
- persistent investigation quests corroborate documentary, physical, tomb, and witness evidence;
- confirmed surviving bloodlines can assert inheritance without copying old rank or resources into the new realm;
- fallen or extinct historical houses can pursue dynasty restoration;
- revenge is evidence-gated and requires a corroborated hostile culprit;
- extinct replacement houses can be challenged over legacy, archives, property, or recognition without becoming blood descendants;
- contested restoration, revenge, and replacement claims use persistent multi-round conflict state.


### GM dashboard system coverage

The GM dashboard now exposes the newer v0.18 systems instead of limiting observability to the older NPC/sect/event views. Dedicated read-only endpoints and tabs cover cultivation aptitudes, crafting/assets, exploration/travel, dynamic economy, and Samsara dynasty investigation/claims.

`/api/capabilities` publishes the backend view/API contract and table-coverage state. Regression tests cross-check browser API references against the backend registry, verify every navigation item has a loader, and perform authenticated HTTP smoke requests against every dashboard view endpoint. This turns frontend/backend drift into a test failure rather than a broken production tab.

### Upgrade

The application migrates supported older schemas through schema 24 automatically. Schema 22 canonicalizes
starter-family homelands, schema 23 adds persistent Samsara dynasty history, and schema 24 adds ancestral
investigation sites/quests plus dynasty claims and conflicts. Existing player locations are not teleported.

Back up the database before upgrading. The bundled local updater performs a live authoritative backup
before replacing code and rolls back code/database on failed installation.

### Dynasty risk-resolution follow-up (2026-08-31)

The ancestral dynasty system now uses authoritative server-side 2d10 resolution for investigation
quests and conflict rounds. Lead danger is a real difficulty input rather than display-only flavor;
physical high-danger failures can cause investigation setbacks and non-lethal vitality loss.
Contested dynasty rounds now resolve player and opposition checks before awarding pressure, and
additional completed ancestral quests reduce the opposition baseline. No schema migration is
required.
