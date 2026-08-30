# v0.16 Remaining Python `Database` Mutators — Future Go Migration Review

Revalidated static call-site review at the v0.16 release cut. The v0.16 authority changes do not make any additional member of this retained list unreachable. The audited write-like set contains **68** remaining methods and **0 unreachable methods**. These should not be deleted without replacing their live production/internal callers.

## Priority 1 — live gameplay authority candidates

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

## Priority 2 — read APIs with write side-effects

- `get_active_effects()`
- `get_active_world_events()`
- `get_alchemy_state()`
- `get_secret_realm_run()`
- `get_social_state()`
- `get_wild_beast_encounters()`
- `get_world_clock()`
- `list_npc_player_memories()`

These look like reads but mutate expiry/decay/initialization/recall state. Future migration should split pure reads from settlement/expiry writes, or make the side effect explicitly authoritative.

## Priority 3 — admin mutations that should converge on Go admin operations

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

## Python-side support / infrastructure writes (lower migration priority)

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

## Reachability conclusion

- Unreachable write-like public methods after purge: **0**.
- `Database.create_character()` is gone; tests seed fixture rows via `tests.support.seed_character()`.
- `adjust_reputation()` and `record_crime()` were deleted in this pass after the stale Python forbidden-art service was removed and their last Python callers disappeared.

## v0.16 release disposition

This list is retained as an explicit post-v0.16 migration backlog. These methods are live/non-duplicated support, admin, side-effecting-read, or gameplay mutation paths; deleting them without first replacing their callers would be unsafe. They therefore do not belong to the v0.16 dead-code purge.
