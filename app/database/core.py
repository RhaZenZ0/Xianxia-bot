from __future__ import annotations

import json
import time
import secrets
import sqlite3
import asyncio
import copy
import logging
import os
from collections import deque
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

try:
    import aiosqlite
except ModuleNotFoundError:  # Production remote-DB mode does not import a SQLite driver.
    class _AioSQLiteRemoteOnly:
        Row = sqlite3.Row
    aiosqlite = _AioSQLiteRemoteOnly()  # type: ignore[assignment]

from .remote import GoDatabaseTransport
from ..lifespan import realm_lifespan_ceiling
from ..advanced_runtime import (
    BOSS_TEMPLATES, BOUNTY_HUNTER_TITLES, ERA_CYCLE,
    FORMATION_POSITIONS, FORMATION_STANCES, equipment_definition,
    equipment_power, era_index, formation_bonus, stable_percent,
)
from ..sect_manor import (
    MAX_MANOR_FACILITY_LEVEL, SECT_MANOR_ESTABLISHMENT_COST, SECT_MANOR_ESTABLISH_RANK_LEVEL,
    SECT_MANOR_FACILITIES, SECT_MANOR_UPGRADE_RANK_LEVEL, manor_upgrade_cost,
)

log = logging.getLogger("xianxia.database")


SCHEMA_VERSION = 17
# A readiness probe must validate more than the schema-version marker.  If the
# SQLite file is removed or replaced while the bot is running, SQLite will
# happily create a new empty file at the same path.  Checking these tables lets
# the runtime distinguish that condition from a healthy versioned database.
OPERATIONAL_REQUIRED_TABLES = frozenset(
    {
        "admin_audit_log",
        "catalog_locations",
        "catalog_manuals",
        "catalog_npcs",
        "catalog_recipes",
        "catalog_techniques",
        "characters",
        "npc_mind_state",
        "npc_player_memories",
        "npc_life_state",
        "npc_social_relations",
        "rag_canon_documents",
        "rag_memories",
        "operational_alerts",
        "realm_hub_channels",
        "schema_migrations",
        "schema_version",
        "server_config",
        "slow_query_log",
        "startup_events",
        "world_events",
        "world_event_participation",
        "world_history_events",
        "world_state",
    }
)
SCHEMA_MIGRATIONS: tuple[tuple[int, str, tuple[str, ...]], ...] = (
    (
        1,
        "formal_startup_health_observability",
        (
            """CREATE TABLE IF NOT EXISTS startup_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                boot_id TEXT NOT NULL,
                phase TEXT NOT NULL,
                status TEXT NOT NULL,
                detail_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL
            )""",
            """CREATE INDEX IF NOT EXISTS idx_startup_events_boot
               ON startup_events(boot_id,event_id)""",
        ),
    ),
    (
        2,
        "advanced_world_combat_operations",
        (
            """CREATE TABLE IF NOT EXISTS equipment_instances (
                equipment_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL, item_id TEXT NOT NULL, slot TEXT NOT NULL,
                durability INTEGER NOT NULL, max_durability INTEGER NOT NULL, quality INTEGER NOT NULL DEFAULT 100,
                equipped INTEGER NOT NULL DEFAULT 0, bound_at REAL NOT NULL, updated_at REAL NOT NULL,
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
            )""",
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_equipment_one_slot
               ON equipment_instances(user_id,slot) WHERE equipped=1""",
            """CREATE INDEX IF NOT EXISTS idx_equipment_user ON equipment_instances(user_id,equipped DESC,equipment_id)""",
            """CREATE TABLE IF NOT EXISTS boss_encounters (
                encounter_id INTEGER PRIMARY KEY AUTOINCREMENT, party_id INTEGER NOT NULL, template_key TEXT NOT NULL,
                location TEXT NOT NULL, boss_name TEXT NOT NULL, boss_hp INTEGER NOT NULL, boss_hp_max INTEGER NOT NULL,
                phase_index INTEGER NOT NULL DEFAULT 0, round_index INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'active',
                winner_party_id INTEGER, version INTEGER NOT NULL DEFAULT 0, started_game_minute INTEGER NOT NULL DEFAULT 0,
                finished_game_minute INTEGER, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                FOREIGN KEY(party_id) REFERENCES parties(party_id) ON DELETE CASCADE
            )""",
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_boss_party_active
               ON boss_encounters(party_id) WHERE status='active'""",
            """CREATE TABLE IF NOT EXISTS boss_participants (
                encounter_id INTEGER NOT NULL, user_id INTEGER NOT NULL, vitality INTEGER NOT NULL, vitality_max INTEGER NOT NULL,
                acted_round INTEGER NOT NULL DEFAULT 0, total_damage INTEGER NOT NULL DEFAULT 0, guard INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active', updated_at REAL NOT NULL, PRIMARY KEY(encounter_id,user_id),
                FOREIGN KEY(encounter_id) REFERENCES boss_encounters(encounter_id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS boss_reward_claims (
                encounter_id INTEGER NOT NULL, user_id INTEGER NOT NULL, currency_amount INTEGER NOT NULL DEFAULT 0,
                item_id TEXT NOT NULL DEFAULT '', item_quantity INTEGER NOT NULL DEFAULT 0, claimed INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL, claimed_at REAL, PRIMARY KEY(encounter_id,user_id),
                FOREIGN KEY(encounter_id) REFERENCES boss_encounters(encounter_id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS bounty_hunter_pursuits (
                pursuit_id INTEGER PRIMARY KEY AUTOINCREMENT, bounty_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                hunter_name TEXT NOT NULL, hunter_power INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'tracking',
                pressure INTEGER NOT NULL DEFAULT 0, escape_progress INTEGER NOT NULL DEFAULT 0, capture_progress INTEGER NOT NULL DEFAULT 0,
                next_action_game_minute INTEGER NOT NULL DEFAULT 0, created_game_minute INTEGER NOT NULL DEFAULT 0,
                updated_game_minute INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                FOREIGN KEY(bounty_id) REFERENCES bounties(bounty_id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
            )""",
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_bounty_hunter_active
               ON bounty_hunter_pursuits(bounty_id) WHERE status IN ('tracking','engaged')""",
            """CREATE TABLE IF NOT EXISTS party_formations (
                formation_id INTEGER PRIMARY KEY AUTOINCREMENT, party_id INTEGER NOT NULL, name TEXT NOT NULL,
                stance TEXT NOT NULL DEFAULT 'balanced', cohesion INTEGER NOT NULL DEFAULT 100, active INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                FOREIGN KEY(party_id) REFERENCES parties(party_id) ON DELETE CASCADE
            )""",
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_party_active_formation
               ON party_formations(party_id) WHERE active=1""",
            """CREATE TABLE IF NOT EXISTS formation_positions (
                formation_id INTEGER NOT NULL, user_id INTEGER NOT NULL, position TEXT NOT NULL, assigned_at REAL NOT NULL,
                PRIMARY KEY(formation_id,user_id), UNIQUE(formation_id,position),
                FOREIGN KEY(formation_id) REFERENCES party_formations(formation_id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS territory_war_operations (
                war_id INTEGER PRIMARY KEY, siege_progress INTEGER NOT NULL DEFAULT 0, attacker_morale INTEGER NOT NULL DEFAULT 100,
                defender_morale INTEGER NOT NULL DEFAULT 100, attacker_force INTEGER NOT NULL DEFAULT 0, defender_force INTEGER NOT NULL DEFAULT 0,
                last_tick_game_minute INTEGER NOT NULL DEFAULT 0, winner_key TEXT NOT NULL DEFAULT '', resolution TEXT NOT NULL DEFAULT '',
                occupation_until_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL,
                FOREIGN KEY(war_id) REFERENCES territory_wars(war_id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS territory_war_actions (
                action_id INTEGER PRIMARY KEY AUTOINCREMENT, war_id INTEGER NOT NULL, user_id INTEGER, side TEXT NOT NULL, tactic TEXT NOT NULL,
                power INTEGER NOT NULL DEFAULT 0, siege_delta INTEGER NOT NULL DEFAULT 0, morale_delta INTEGER NOT NULL DEFAULT 0,
                game_minute INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
                FOREIGN KEY(war_id) REFERENCES territory_wars(war_id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE SET NULL
            )""",
            """CREATE TABLE IF NOT EXISTS caravan_operations (
                caravan_id INTEGER PRIMARY KEY, escort_strength INTEGER NOT NULL DEFAULT 0, concealment INTEGER NOT NULL DEFAULT 0,
                smuggling INTEGER NOT NULL DEFAULT 0, tax_rate INTEGER NOT NULL DEFAULT 8, toll_paid INTEGER NOT NULL DEFAULT 0,
                intercepted INTEGER NOT NULL DEFAULT 0, seized INTEGER NOT NULL DEFAULT 0, payout_final INTEGER NOT NULL DEFAULT 0,
                losses_json TEXT NOT NULL DEFAULT '{}', outcome TEXT NOT NULL DEFAULT 'traveling', resolved_game_minute INTEGER,
                updated_at REAL NOT NULL, FOREIGN KEY(caravan_id) REFERENCES caravans(caravan_id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS caravan_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT, caravan_id INTEGER NOT NULL, event_type TEXT NOT NULL,
                detail_json TEXT NOT NULL DEFAULT '{}', game_minute INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
                FOREIGN KEY(caravan_id) REFERENCES caravans(caravan_id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS world_era_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT, era_id INTEGER NOT NULL, event_type TEXT NOT NULL,
                title TEXT NOT NULL, detail_json TEXT NOT NULL DEFAULT '{}', game_minute INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
                FOREIGN KEY(era_id) REFERENCES world_eras(era_id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS slow_query_log (
                query_id INTEGER PRIMARY KEY AUTOINCREMENT, sql_text TEXT NOT NULL, latency_ms REAL NOT NULL,
                operation TEXT NOT NULL, created_at REAL NOT NULL
            )""",
            """CREATE INDEX IF NOT EXISTS idx_slow_query_created ON slow_query_log(created_at DESC)""",
            """CREATE TABLE IF NOT EXISTS operational_alerts (
                alert_id INTEGER PRIMARY KEY AUTOINCREMENT, alert_key TEXT NOT NULL, severity TEXT NOT NULL,
                message TEXT NOT NULL, detail_json TEXT NOT NULL DEFAULT '{}', delivered INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            )""",
        ),
    ),
    (
        3,
        "sect_manor_system",
        (
            """CREATE TABLE IF NOT EXISTS sect_manors (
                sect_name TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                base_location TEXT NOT NULL,
                qi_array_level INTEGER NOT NULL DEFAULT 0,
                alchemy_hall_level INTEGER NOT NULL DEFAULT 0,
                forge_pavilion_level INTEGER NOT NULL DEFAULT 0,
                defense_array_level INTEGER NOT NULL DEFAULT 0,
                founded_by_user_id INTEGER,
                created_game_minute INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(sect_name) REFERENCES sects(sect_name) ON DELETE CASCADE,
                FOREIGN KEY(founded_by_user_id) REFERENCES characters(user_id) ON DELETE SET NULL
            )""",
            """CREATE INDEX IF NOT EXISTS idx_sect_manors_location
               ON sect_manors(base_location)""",
            """CREATE TABLE IF NOT EXISTS sect_manor_projects (
                project_id INTEGER PRIMARY KEY AUTOINCREMENT,
                sect_name TEXT NOT NULL,
                user_id INTEGER,
                project_type TEXT NOT NULL,
                facility_key TEXT NOT NULL DEFAULT '',
                from_level INTEGER NOT NULL DEFAULT 0,
                to_level INTEGER NOT NULL DEFAULT 0,
                cost_json TEXT NOT NULL DEFAULT '{}',
                game_minute INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                FOREIGN KEY(sect_name) REFERENCES sects(sect_name) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE SET NULL
            )""",
            """CREATE INDEX IF NOT EXISTS idx_sect_manor_projects
               ON sect_manor_projects(sect_name,project_id DESC)""",
        ),
    ),
    (
        4,
        "connected_missing_systems_and_realm_hubs",
        (
            """CREATE TABLE IF NOT EXISTS character_fate (
                user_id INTEGER PRIMARY KEY,
                points INTEGER NOT NULL DEFAULT 0,
                lifetime_earned INTEGER NOT NULL DEFAULT 0,
                lifetime_spent INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL,
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS fate_ledger (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                delta INTEGER NOT NULL,
                balance_after INTEGER NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                game_minute INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
            )""",
            """CREATE INDEX IF NOT EXISTS idx_fate_ledger_user ON fate_ledger(user_id,entry_id DESC)""",
            """CREATE TABLE IF NOT EXISTS disciple_requests (
                request_id INTEGER PRIMARY KEY AUTOINCREMENT,
                disciple_user_id INTEGER NOT NULL,
                master_user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at REAL NOT NULL,
                resolved_at REAL,
                FOREIGN KEY(disciple_user_id) REFERENCES characters(user_id) ON DELETE CASCADE,
                FOREIGN KEY(master_user_id) REFERENCES characters(user_id) ON DELETE CASCADE
            )""",
            """CREATE INDEX IF NOT EXISTS idx_disciple_requests_master ON disciple_requests(master_user_id,status,request_id DESC)""",
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_disciple_requests_one_pending ON disciple_requests(disciple_user_id) WHERE status='pending'""",
            """CREATE TABLE IF NOT EXISTS deployed_location_arrays (
                location TEXT PRIMARY KEY,
                item_id TEXT NOT NULL,
                name TEXT NOT NULL,
                owner_user_id INTEGER,
                sect_name TEXT NOT NULL DEFAULT '',
                effect_json TEXT NOT NULL DEFAULT '{}',
                starts_game_minute INTEGER NOT NULL,
                ends_game_minute INTEGER NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(owner_user_id) REFERENCES characters(user_id) ON DELETE SET NULL
            )""",
            """CREATE INDEX IF NOT EXISTS idx_deployed_arrays_expiry ON deployed_location_arrays(ends_game_minute)""",
            """CREATE TABLE IF NOT EXISTS black_market_posts (
                world_name TEXT PRIMARY KEY,
                location TEXT NOT NULL,
                heat INTEGER NOT NULL DEFAULT 0,
                opens_game_minute INTEGER NOT NULL,
                closes_game_minute INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )""",
            """CREATE INDEX IF NOT EXISTS idx_black_market_location ON black_market_posts(location,active)""",
            """CREATE TABLE IF NOT EXISTS black_market_stock (
                world_name TEXT NOT NULL,
                item_id TEXT NOT NULL,
                currency_id TEXT NOT NULL,
                unit_price INTEGER NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 0,
                legal_status TEXT NOT NULL DEFAULT 'forbidden',
                updated_at REAL NOT NULL,
                PRIMARY KEY(world_name,item_id),
                FOREIGN KEY(world_name) REFERENCES black_market_posts(world_name) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS dao_partnerships (
                partnership_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_a INTEGER NOT NULL,
                user_b INTEGER NOT NULL,
                requested_by INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                resonance INTEGER NOT NULL DEFAULT 0,
                dual_sessions INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(user_a,user_b),
                FOREIGN KEY(user_a) REFERENCES characters(user_id) ON DELETE CASCADE,
                FOREIGN KEY(user_b) REFERENCES characters(user_id) ON DELETE CASCADE,
                FOREIGN KEY(requested_by) REFERENCES characters(user_id) ON DELETE CASCADE
            )""",
            """CREATE INDEX IF NOT EXISTS idx_dao_partnership_status ON dao_partnerships(status,user_a,user_b)""",
            """ALTER TABLE reincarnation_state ADD COLUMN partner_echo INTEGER NOT NULL DEFAULT 0""",
            """ALTER TABLE reincarnation_state ADD COLUMN partner_name TEXT NOT NULL DEFAULT ''""",
            """CREATE TABLE IF NOT EXISTS realm_hub_channels (
                guild_id INTEGER NOT NULL,
                world_name TEXT NOT NULL,
                location TEXT NOT NULL,
                channel_id INTEGER NOT NULL,
                category_id INTEGER,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(guild_id,world_name),
                UNIQUE(guild_id,channel_id)
            )""",
            """CREATE INDEX IF NOT EXISTS idx_realm_hub_channel ON realm_hub_channels(guild_id,channel_id)""",
        ),
    ),
    (
        5,
        "alchemy_and_spirit_beast_expansion",
        (
            """CREATE TABLE IF NOT EXISTS alchemy_state (
                user_id INTEGER PRIMARY KEY,
                pill_toxicity INTEGER NOT NULL DEFAULT 0,
                last_toxicity_game_minute INTEGER NOT NULL DEFAULT 0,
                total_refinements INTEGER NOT NULL DEFAULT 0,
                successful_refinements INTEGER NOT NULL DEFAULT 0,
                flawless_refinements INTEGER NOT NULL DEFAULT 0,
                best_margin INTEGER NOT NULL DEFAULT -99,
                last_quality TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL,
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS alchemy_batches (
                batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                recipe_name TEXT NOT NULL,
                quality TEXT NOT NULL,
                margin INTEGER NOT NULL DEFAULT 0,
                success INTEGER NOT NULL DEFAULT 0,
                output_json TEXT NOT NULL DEFAULT '{}',
                location TEXT NOT NULL DEFAULT '',
                game_minute INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
            )""",
            """CREATE INDEX IF NOT EXISTS idx_alchemy_batches_user
               ON alchemy_batches(user_id,batch_id DESC)""",
            """CREATE TABLE IF NOT EXISTS wild_beast_encounters (
                encounter_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                species TEXT NOT NULL,
                rank INTEGER NOT NULL DEFAULT 0,
                element TEXT NOT NULL DEFAULT 'Wild',
                intelligence INTEGER NOT NULL DEFAULT 10,
                temperament TEXT NOT NULL DEFAULT 'wary',
                bloodline TEXT NOT NULL DEFAULT 'Wild Spirit',
                taming_tn INTEGER NOT NULL DEFAULT 14,
                location TEXT NOT NULL,
                expires_game_minute INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'available',
                created_game_minute INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
            )""",
            """CREATE INDEX IF NOT EXISTS idx_wild_beast_encounters_user
               ON wild_beast_encounters(user_id,status,expires_game_minute DESC)""",
        ),
    ),
    (
        6,
        "discord_server_log_and_begin_channels",
        (
            "ALTER TABLE server_config ADD COLUMN log_channel_id INTEGER",
            "ALTER TABLE server_config ADD COLUMN begin_channel_id INTEGER",
        ),
    ),
    (
        7,
        "character_world_discovery",
        (
            """CREATE TABLE IF NOT EXISTS character_location_discoveries (
                user_id INTEGER NOT NULL,
                location TEXT NOT NULL,
                discovery_kind TEXT NOT NULL DEFAULT 'exploration',
                discovered_game_minute INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                PRIMARY KEY(user_id,location),
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
            )""",
            """CREATE INDEX IF NOT EXISTS idx_character_location_discoveries_user
               ON character_location_discoveries(user_id,discovered_game_minute,location)""",
            """INSERT OR IGNORE INTO character_location_discoveries(
                   user_id,location,discovery_kind,discovered_game_minute,created_at
               ) SELECT user_id,location,'legacy_current_location',created_game_minute,updated_at
                 FROM characters WHERE location NOT LIKE 'abode:%' AND location NOT LIKE 'personal_world:%'""",
        ),
    ),
    (
        8,
        "sect_recruitment_and_npc_recommendations",
        (
            """CREATE TABLE IF NOT EXISTS character_sect_discoveries (
                user_id INTEGER NOT NULL,
                sect_name TEXT NOT NULL,
                discovery_kind TEXT NOT NULL DEFAULT 'rumor',
                source_key TEXT NOT NULL DEFAULT '',
                discovered_game_minute INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                PRIMARY KEY(user_id,sect_name),
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
            )""",
            """CREATE INDEX IF NOT EXISTS idx_character_sect_discoveries_user
               ON character_sect_discoveries(user_id,discovered_game_minute,sect_name)""",
            """CREATE TABLE IF NOT EXISTS sect_recommendations (
                recommendation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                npc_name TEXT NOT NULL,
                sect_name TEXT NOT NULL,
                bonus INTEGER NOT NULL DEFAULT 2,
                status TEXT NOT NULL DEFAULT 'active',
                issued_game_minute INTEGER NOT NULL DEFAULT 0,
                used_game_minute INTEGER,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
            )""",
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_sect_recommendation_active
               ON sect_recommendations(user_id,sect_name) WHERE status='active'""",
            """CREATE INDEX IF NOT EXISTS idx_sect_recommendations_user
               ON sect_recommendations(user_id,status,recommendation_id DESC)""",
            """CREATE TABLE IF NOT EXISTS sect_recruitment_attempts (
                attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                sect_name TEXT NOT NULL,
                attempt_type TEXT NOT NULL,
                npc_name TEXT NOT NULL DEFAULT '',
                location TEXT NOT NULL DEFAULT '',
                result TEXT NOT NULL,
                score INTEGER NOT NULL DEFAULT 0,
                target INTEGER NOT NULL DEFAULT 0,
                recommendation_bonus INTEGER NOT NULL DEFAULT 0,
                details_json TEXT NOT NULL DEFAULT '{}',
                game_minute INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
            )""",
            """CREATE INDEX IF NOT EXISTS idx_sect_recruitment_attempts_user
               ON sect_recruitment_attempts(user_id,sect_name,attempt_type,attempt_id DESC)""",
        ),
    ),
    (
        9,
        "private_location_scenes_and_info_channel",
        (
            "ALTER TABLE server_config ADD COLUMN info_channel_id INTEGER",
            "ALTER TABLE server_config ADD COLUMN exploration_channel_id INTEGER",
            """CREATE TABLE IF NOT EXISTS expedition_threads (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL,
                parent_channel_id INTEGER NOT NULL,
                last_location TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(guild_id,user_id),
                UNIQUE(thread_id),
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
            )""",
            """CREATE INDEX IF NOT EXISTS idx_expedition_threads_thread
               ON expedition_threads(thread_id)""",
            """CREATE TABLE IF NOT EXISTS sect_abodes (
                user_id INTEGER PRIMARY KEY,
                sect_name TEXT NOT NULL,
                name TEXT NOT NULL,
                location_key TEXT NOT NULL UNIQUE,
                base_location TEXT NOT NULL,
                thread_id INTEGER,
                thread_channel_id INTEGER,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
            )""",
            """CREATE INDEX IF NOT EXISTS idx_sect_abodes_sect
               ON sect_abodes(sect_name,user_id)""",
        ),
    ),
    (
        10,
        "general_player_owned_properties",
        (
            "ALTER TABLE cave_abodes ADD COLUMN property_type TEXT NOT NULL DEFAULT 'cave_abode'",
            "ALTER TABLE cave_abodes ADD COLUMN storage_level INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE cave_abodes ADD COLUMN herb_garden_level INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE cave_abodes ADD COLUMN beast_pen_level INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE cave_abodes ADD COLUMN merchant_level INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE server_config ADD COLUMN info_message_id INTEGER",
        ),
    ),
    (
        11,
        "v07_core_scene_relationship_quest_foundation",
        (
            """CREATE TABLE IF NOT EXISTS player_scene_state (
                user_id INTEGER PRIMARY KEY,
                physical_location TEXT NOT NULL DEFAULT '',
                scene_type TEXT NOT NULL DEFAULT 'world',
                scene_key TEXT NOT NULL DEFAULT '',
                scene_label TEXT NOT NULL DEFAULT '',
                channel_id INTEGER,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                updated_at REAL NOT NULL,
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS npc_relationships (
                user_id INTEGER NOT NULL,
                npc_name TEXT NOT NULL,
                trust INTEGER NOT NULL DEFAULT 0,
                respect INTEGER NOT NULL DEFAULT 0,
                fear INTEGER NOT NULL DEFAULT 0,
                affection INTEGER NOT NULL DEFAULT 0,
                debt INTEGER NOT NULL DEFAULT 0,
                grudge INTEGER NOT NULL DEFAULT 0,
                encounter_count INTEGER NOT NULL DEFAULT 0,
                last_summary TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL,
                PRIMARY KEY(user_id,npc_name),
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
            )""",
            """CREATE INDEX IF NOT EXISTS idx_npc_relationships_user
               ON npc_relationships(user_id,updated_at DESC)""",
            """CREATE TABLE IF NOT EXISTS character_quests (
                user_id INTEGER NOT NULL,
                quest_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                progress_json TEXT NOT NULL DEFAULT '{}',
                accepted_game_minute INTEGER NOT NULL DEFAULT 0,
                completed_game_minute INTEGER,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(user_id,quest_key),
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
            )""",
            """CREATE INDEX IF NOT EXISTS idx_character_quests_user_status
               ON character_quests(user_id,status,updated_at DESC)""",
        ),
    ),
    (
        12,
        "versioned_core_write_ledger",
        (
            """CREATE TABLE IF NOT EXISTS core_state_versions (
                scope_key TEXT PRIMARY KEY,
                version INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS core_request_log (
                idempotency_key TEXT PRIMARY KEY,
                operation TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                expected_version INTEGER NOT NULL,
                state_version INTEGER NOT NULL,
                request_digest TEXT NOT NULL,
                response_json TEXT NOT NULL,
                created_at REAL NOT NULL
            )""",
            """CREATE INDEX IF NOT EXISTS idx_core_request_scope
               ON core_request_log(scope_key,state_version)""",
        ),
    ),
    (
        13,
        "persistent_npc_minds_and_player_memories",
        (
            """CREATE TABLE IF NOT EXISTS npc_mind_state (
                npc_name TEXT PRIMARY KEY,
                current_goal TEXT NOT NULL DEFAULT '',
                mood TEXT NOT NULL DEFAULT 'calm',
                focus_target TEXT NOT NULL DEFAULT '',
                recent_event TEXT NOT NULL DEFAULT '',
                goal_progress INTEGER NOT NULL DEFAULT 0,
                last_game_minute INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS npc_player_memories (
                memory_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                npc_name TEXT NOT NULL,
                memory_kind TEXT NOT NULL DEFAULT 'conversation',
                summary TEXT NOT NULL,
                salience INTEGER NOT NULL DEFAULT 25,
                source TEXT NOT NULL DEFAULT 'talk',
                game_minute INTEGER NOT NULL DEFAULT 0,
                recalled_count INTEGER NOT NULL DEFAULT 0,
                last_recalled_at REAL,
                created_at REAL NOT NULL,
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
            )""",
            """CREATE INDEX IF NOT EXISTS idx_npc_player_memories_pair
               ON npc_player_memories(user_id,npc_name,salience DESC,game_minute DESC,memory_id DESC)""",
        ),
    ),
    (
        14,
        "memory_rag_v1_fts5_and_structured_retrieval",
        (
            """CREATE TABLE IF NOT EXISTS rag_memories (
                memory_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_key TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL,
                memory_kind TEXT NOT NULL DEFAULT 'scene',
                summary TEXT NOT NULL,
                salience INTEGER NOT NULL DEFAULT 25,
                location TEXT NOT NULL DEFAULT '',
                npc_name TEXT NOT NULL DEFAULT '',
                faction TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'scene',
                game_minute INTEGER NOT NULL DEFAULT 0,
                recalled_count INTEGER NOT NULL DEFAULT 0,
                last_recalled_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
            )""",
            """CREATE INDEX IF NOT EXISTS idx_rag_memories_owner
               ON rag_memories(user_id,salience DESC,game_minute DESC,memory_id DESC)""",
            """CREATE INDEX IF NOT EXISTS idx_rag_memories_location
               ON rag_memories(user_id,location,game_minute DESC)""",
            """CREATE VIRTUAL TABLE IF NOT EXISTS rag_memories_fts USING fts5(
                summary, memory_kind, npc_name, faction, location,
                content='rag_memories', content_rowid='memory_id',
                tokenize='unicode61 remove_diacritics 2'
            )""",
            """CREATE TRIGGER IF NOT EXISTS rag_memories_ai AFTER INSERT ON rag_memories BEGIN
                INSERT INTO rag_memories_fts(rowid,summary,memory_kind,npc_name,faction,location)
                VALUES(new.memory_id,new.summary,new.memory_kind,new.npc_name,new.faction,new.location);
            END""",
            """CREATE TRIGGER IF NOT EXISTS rag_memories_ad AFTER DELETE ON rag_memories BEGIN
                INSERT INTO rag_memories_fts(rag_memories_fts,rowid,summary,memory_kind,npc_name,faction,location)
                VALUES('delete',old.memory_id,old.summary,old.memory_kind,old.npc_name,old.faction,old.location);
            END""",
            """CREATE TRIGGER IF NOT EXISTS rag_memories_au AFTER UPDATE ON rag_memories BEGIN
                INSERT INTO rag_memories_fts(rag_memories_fts,rowid,summary,memory_kind,npc_name,faction,location)
                VALUES('delete',old.memory_id,old.summary,old.memory_kind,old.npc_name,old.faction,old.location);
                INSERT INTO rag_memories_fts(rowid,summary,memory_kind,npc_name,faction,location)
                VALUES(new.memory_id,new.summary,new.memory_kind,new.npc_name,new.faction,new.location);
            END""",
            """CREATE TABLE IF NOT EXISTS rag_canon_documents (
                doc_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_key TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '',
                knowledge_scope TEXT NOT NULL DEFAULT 'global',
                location TEXT NOT NULL DEFAULT '',
                priority INTEGER NOT NULL DEFAULT 50,
                updated_at REAL NOT NULL
            )""",
            """CREATE INDEX IF NOT EXISTS idx_rag_canon_scope
               ON rag_canon_documents(knowledge_scope,location,priority DESC,doc_id)""",
            """CREATE VIRTUAL TABLE IF NOT EXISTS rag_canon_fts USING fts5(
                title, body, tags,
                content='rag_canon_documents', content_rowid='doc_id',
                tokenize='unicode61 remove_diacritics 2'
            )""",
            """CREATE TRIGGER IF NOT EXISTS rag_canon_ai AFTER INSERT ON rag_canon_documents BEGIN
                INSERT INTO rag_canon_fts(rowid,title,body,tags) VALUES(new.doc_id,new.title,new.body,new.tags);
            END""",
            """CREATE TRIGGER IF NOT EXISTS rag_canon_ad AFTER DELETE ON rag_canon_documents BEGIN
                INSERT INTO rag_canon_fts(rag_canon_fts,rowid,title,body,tags)
                VALUES('delete',old.doc_id,old.title,old.body,old.tags);
            END""",
            """CREATE TRIGGER IF NOT EXISTS rag_canon_au AFTER UPDATE ON rag_canon_documents BEGIN
                INSERT INTO rag_canon_fts(rag_canon_fts,rowid,title,body,tags)
                VALUES('delete',old.doc_id,old.title,old.body,old.tags);
                INSERT INTO rag_canon_fts(rowid,title,body,tags) VALUES(new.doc_id,new.title,new.body,new.tags);
            END""",
            """INSERT OR IGNORE INTO rag_memories(
                source_key,user_id,memory_kind,summary,salience,npc_name,source,game_minute,created_at,updated_at
            ) SELECT 'npc:' || memory_id,user_id,memory_kind,summary,salience,npc_name,source,game_minute,created_at,created_at
              FROM npc_player_memories""",
            """CREATE TRIGGER IF NOT EXISTS npc_memory_rag_ai AFTER INSERT ON npc_player_memories BEGIN
                INSERT OR REPLACE INTO rag_memories(
                    source_key,user_id,memory_kind,summary,salience,npc_name,source,game_minute,created_at,updated_at
                ) VALUES('npc:' || new.memory_id,new.user_id,new.memory_kind,new.summary,new.salience,new.npc_name,new.source,new.game_minute,new.created_at,new.created_at);
            END""",
            """CREATE TRIGGER IF NOT EXISTS npc_memory_rag_au AFTER UPDATE ON npc_player_memories BEGIN
                UPDATE rag_memories SET memory_kind=new.memory_kind,summary=new.summary,salience=new.salience,
                    npc_name=new.npc_name,source=new.source,game_minute=new.game_minute,updated_at=strftime('%s','now')
                WHERE source_key='npc:' || new.memory_id;
            END""",
            """CREATE TRIGGER IF NOT EXISTS npc_memory_rag_ad AFTER DELETE ON npc_player_memories BEGIN
                DELETE FROM rag_memories WHERE source_key='npc:' || old.memory_id;
            END""",
            """INSERT INTO rag_memories_fts(rag_memories_fts) VALUES('rebuild')""",
            """INSERT INTO rag_canon_fts(rag_canon_fts) VALUES('rebuild')""",
        ),
    ),
    (
        15,
        "structured_world_history_rag_v1",
        (
            """CREATE TABLE IF NOT EXISTS world_history_events (
                history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_key TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                significance INTEGER NOT NULL DEFAULT 50,
                visibility TEXT NOT NULL DEFAULT 'public',
                location TEXT NOT NULL DEFAULT '',
                world_name TEXT NOT NULL DEFAULT '',
                faction TEXT NOT NULL DEFAULT '',
                actor_type TEXT NOT NULL DEFAULT '',
                actor_key TEXT NOT NULL DEFAULT '',
                actor_name TEXT NOT NULL DEFAULT '',
                target_type TEXT NOT NULL DEFAULT '',
                target_key TEXT NOT NULL DEFAULT '',
                target_name TEXT NOT NULL DEFAULT '',
                related_user_id INTEGER,
                related_npc_name TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '',
                game_minute INTEGER NOT NULL DEFAULT 0,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )""",
            """CREATE INDEX IF NOT EXISTS idx_world_history_time
               ON world_history_events(game_minute DESC,significance DESC,history_id DESC)""",
            """CREATE INDEX IF NOT EXISTS idx_world_history_location
               ON world_history_events(location,game_minute DESC,significance DESC)""",
            """CREATE INDEX IF NOT EXISTS idx_world_history_faction
               ON world_history_events(faction,game_minute DESC,significance DESC)""",
            """CREATE INDEX IF NOT EXISTS idx_world_history_participant
               ON world_history_events(related_user_id,related_npc_name,game_minute DESC)""",
            """CREATE VIRTUAL TABLE IF NOT EXISTS world_history_fts USING fts5(
                title, summary, event_type, location, faction, actor_name, target_name, tags,
                content='world_history_events', content_rowid='history_id',
                tokenize='unicode61 remove_diacritics 2'
            )""",
            """CREATE TRIGGER IF NOT EXISTS world_history_ai AFTER INSERT ON world_history_events BEGIN
                INSERT INTO world_history_fts(rowid,title,summary,event_type,location,faction,actor_name,target_name,tags)
                VALUES(new.history_id,new.title,new.summary,new.event_type,new.location,new.faction,new.actor_name,new.target_name,new.tags);
            END""",
            """CREATE TRIGGER IF NOT EXISTS world_history_ad AFTER DELETE ON world_history_events BEGIN
                INSERT INTO world_history_fts(world_history_fts,rowid,title,summary,event_type,location,faction,actor_name,target_name,tags)
                VALUES('delete',old.history_id,old.title,old.summary,old.event_type,old.location,old.faction,old.actor_name,old.target_name,old.tags);
            END""",
            """CREATE TRIGGER IF NOT EXISTS world_history_au AFTER UPDATE ON world_history_events BEGIN
                INSERT INTO world_history_fts(world_history_fts,rowid,title,summary,event_type,location,faction,actor_name,target_name,tags)
                VALUES('delete',old.history_id,old.title,old.summary,old.event_type,old.location,old.faction,old.actor_name,old.target_name,old.tags);
                INSERT INTO world_history_fts(rowid,title,summary,event_type,location,faction,actor_name,target_name,tags)
                VALUES(new.history_id,new.title,new.summary,new.event_type,new.location,new.faction,new.actor_name,new.target_name,new.tags);
            END""",
            """INSERT INTO world_history_fts(world_history_fts) VALUES('rebuild')""",
        ),
    ),
    (
        16,
        "npc_autonomous_life_simulation_v1",
        (
            """CREATE TABLE IF NOT EXISTS npc_life_state (
                npc_name TEXT PRIMARY KEY,
                birth_game_minute INTEGER NOT NULL DEFAULT 0,
                age_at_creation_years INTEGER NOT NULL DEFAULT 18,
                natural_lifespan_years INTEGER NOT NULL DEFAULT 75,
                health INTEGER NOT NULL DEFAULT 100,
                injury TEXT NOT NULL DEFAULT '',
                injury_severity INTEGER NOT NULL DEFAULT 0,
                sect_rank TEXT NOT NULL DEFAULT 'Independent Cultivator',
                career_progress INTEGER NOT NULL DEFAULT 0,
                relationship_status TEXT NOT NULL DEFAULT 'single',
                spouse_name TEXT NOT NULL DEFAULT '',
                children_count INTEGER NOT NULL DEFAULT 0,
                last_social_game_minute INTEGER NOT NULL DEFAULT 0,
                last_cultivation_game_minute INTEGER NOT NULL DEFAULT 0,
                death_game_minute INTEGER,
                cause_of_death TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS npc_social_relations (
                npc_a TEXT NOT NULL,
                npc_b TEXT NOT NULL,
                affinity INTEGER NOT NULL DEFAULT 0,
                trust INTEGER NOT NULL DEFAULT 0,
                grudge INTEGER NOT NULL DEFAULT 0,
                relation_type TEXT NOT NULL DEFAULT 'acquaintance',
                status TEXT NOT NULL DEFAULT 'active',
                started_game_minute INTEGER NOT NULL DEFAULT 0,
                last_interaction_game_minute INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL,
                PRIMARY KEY(npc_a,npc_b),
                CHECK(npc_a < npc_b)
            )""",
            """CREATE INDEX IF NOT EXISTS idx_npc_social_relations_a
               ON npc_social_relations(npc_a,status,relation_type)""",
            """CREATE INDEX IF NOT EXISTS idx_npc_social_relations_b
               ON npc_social_relations(npc_b,status,relation_type)""",
            """CREATE TABLE IF NOT EXISTS npc_disciple_bonds (
                master_name TEXT NOT NULL,
                disciple_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                started_game_minute INTEGER NOT NULL DEFAULT 0,
                ended_game_minute INTEGER,
                reason TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL,
                PRIMARY KEY(master_name,disciple_name)
            )""",
            """CREATE INDEX IF NOT EXISTS idx_npc_disciple_active
               ON npc_disciple_bonds(disciple_name,status)""",
            """CREATE TABLE IF NOT EXISTS npc_descendants (
                descendant_id INTEGER PRIMARY KEY AUTOINCREMENT,
                child_name TEXT NOT NULL UNIQUE,
                parent_a TEXT NOT NULL,
                parent_b TEXT NOT NULL,
                birth_game_minute INTEGER NOT NULL,
                gender TEXT NOT NULL DEFAULT 'neutral',
                spiritual_root TEXT NOT NULL DEFAULT 'Mortal Root',
                realm_index INTEGER NOT NULL DEFAULT 0,
                phase INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'alive',
                generated_as_npc INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )""",
            """CREATE INDEX IF NOT EXISTS idx_npc_descendants_parents
               ON npc_descendants(parent_a,parent_b,birth_game_minute DESC)""",
        ),
    ),
    (
        17,
        "event_specific_discord_gui_v1",
        (
            """CREATE TABLE IF NOT EXISTS world_event_participation (
                event_key TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                stance TEXT NOT NULL DEFAULT 'observing',
                contribution INTEGER NOT NULL DEFAULT 0,
                investigation INTEGER NOT NULL DEFAULT 0,
                support INTEGER NOT NULL DEFAULT 0,
                interference INTEGER NOT NULL DEFAULT 0,
                combat_victories INTEGER NOT NULL DEFAULT 0,
                actions_taken INTEGER NOT NULL DEFAULT 0,
                successes INTEGER NOT NULL DEFAULT 0,
                failures INTEGER NOT NULL DEFAULT 0,
                last_action TEXT NOT NULL DEFAULT '',
                last_target TEXT NOT NULL DEFAULT '',
                first_game_minute INTEGER NOT NULL DEFAULT 0,
                last_game_minute INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL,
                PRIMARY KEY(event_key,user_id),
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
            )""",
            """CREATE INDEX IF NOT EXISTS idx_world_event_participation_event
               ON world_event_participation(event_key,contribution DESC,actions_taken DESC)""",
            """CREATE TABLE IF NOT EXISTS world_event_actions (
                action_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                action_key TEXT NOT NULL,
                stance TEXT NOT NULL DEFAULT '',
                target TEXT NOT NULL DEFAULT '',
                attribute TEXT NOT NULL DEFAULT '',
                total INTEGER NOT NULL DEFAULT 0,
                tn INTEGER NOT NULL DEFAULT 0,
                success INTEGER NOT NULL DEFAULT 0,
                contribution_delta INTEGER NOT NULL DEFAULT 0,
                detail TEXT NOT NULL DEFAULT '',
                game_minute INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
            )""",
            """CREATE INDEX IF NOT EXISTS idx_world_event_actions_event
               ON world_event_actions(event_key,action_id DESC)""",
        ),
    ),

)


class _ObservedCursor:
    def __init__(self, cursor: Any):
        self._cursor = cursor

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    @property
    def rowcount(self):
        return self._cursor.rowcount

    async def fetchone(self):
        return await self._cursor.fetchone()

    async def fetchall(self):
        return await self._cursor.fetchall()


class _ObservedConnection:
    _WRITE_OPERATIONS = {
        "ALTER", "BEGIN", "CREATE", "DELETE", "DROP", "INSERT", "REINDEX",
        "REPLACE", "UPDATE", "VACUUM",
    }

    def __init__(self, owner: "Database", raw: Any):
        self._owner = owner
        self._raw = raw
        self._writer_lock_held = False

    @property
    def row_factory(self):
        return self._raw.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._raw.row_factory = value

    async def execute(self, sql: str, params: Any = ()):
        await self._acquire_writer_if_needed(sql)
        started = time.perf_counter()
        try:
            cursor = await self._raw.execute(sql, params)
            return _ObservedCursor(cursor)
        finally:
            self._owner._observe_query(sql, (time.perf_counter() - started) * 1000.0)

    async def executescript(self, sql: str):
        await self._acquire_writer()
        started = time.perf_counter()
        try:
            return await self._raw.executescript(sql)
        finally:
            self._owner._observe_query("<executescript>", (time.perf_counter() - started) * 1000.0)

    async def commit(self):
        try:
            return await self._raw.commit()
        finally:
            self.release_writer()

    async def rollback(self):
        try:
            return await self._raw.rollback()
        finally:
            self.release_writer()

    async def _acquire_writer_if_needed(self, sql: str) -> None:
        operation = str(sql).lstrip().split(None, 1)[0].upper() if str(sql).strip() else ""
        if operation in self._WRITE_OPERATIONS:
            await self._acquire_writer()

    async def _acquire_writer(self) -> None:
        if self._writer_lock_held:
            return
        started = time.perf_counter()
        contended = self._owner._writer_lock.locked()
        await self._owner._writer_lock.acquire()
        waited_ms = (time.perf_counter() - started) * 1000.0
        self._writer_lock_held = True
        if contended:
            self._owner._writer_wait_count += 1
        self._owner._writer_wait_ms += waited_ms

    def release_writer(self) -> None:
        if not self._writer_lock_held:
            return
        self._writer_lock_held = False
        self._owner._writer_lock.release()


class Database:
    def __init__(
        self,
        path: Path,
        *,
        slow_query_ms: float | None = None,
        engine_url: str | None = None,
    ):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        configured_engine = engine_url if engine_url is not None else os.getenv("GAME_ENGINE_URL", "")
        self.engine_url = str(configured_engine or "").strip().rstrip("/") or None
        self._go_transport = GoDatabaseTransport(self.engine_url) if self.engine_url else None
        self.slow_query_ms = float(slow_query_ms if slow_query_ms is not None else os.getenv("SLOW_QUERY_MS", "100"))
        self._query_count = 0
        self._slow_query_count = 0
        self._max_query_latency_ms = 0.0
        self._pending_slow_queries: deque[dict[str, Any]] = deque(maxlen=1000)
        self._writer_lock = asyncio.Lock()
        self._writer_wait_count = 0
        self._writer_wait_ms = 0.0
        self._connections_opened = 0
        self._connections_reused = 0
        self._shared_connection: ContextVar[_ObservedConnection | None] = ContextVar(
            f"xianxia_db_shared_connection_{id(self)}", default=None
        )
        self._catalog_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._catalog_cache_hits = 0
        self._catalog_cache_misses = 0

    def _observe_query(self, sql: str, latency_ms: float) -> None:
        self._query_count += 1
        self._max_query_latency_ms = max(self._max_query_latency_ms, float(latency_ms))
        if float(latency_ms) < self.slow_query_ms:
            return
        self._slow_query_count += 1
        compact = " ".join(str(sql).split())[:2000]
        entry = {
            "sql_text": compact,
            "latency_ms": round(float(latency_ms), 3),
            "operation": (compact.split(" ", 1)[0].upper() if compact else "UNKNOWN"),
            "created_at": time.time(),
        }
        self._pending_slow_queries.append(entry)
        log.warning("SLOW_QUERY latency_ms=%.3f operation=%s sql=%s", latency_ms, entry["operation"], compact[:300])

    @asynccontextmanager
    async def _open_connection(self):
        # Production uses the authoritative Go process as the only SQLite owner.
        # Local SQLite remains available when GAME_ENGINE_URL is unset so isolated
        # unit tests and one-off maintenance scripts can run without a daemon.
        if self._go_transport is not None:
            raw = await self._go_transport.open()
            self._connections_opened += 1
            observed = _ObservedConnection(self, raw)
            try:
                yield observed
            finally:
                observed.release_writer()
                await raw.close()
            return

        # Local development/test fallback. Production docker-compose always sets
        # GAME_ENGINE_URL and therefore never opens SQLite from Python.
        async with aiosqlite.connect(self.path, timeout=10) as db:
            self._connections_opened += 1
            await db.execute("PRAGMA foreign_keys=ON;")
            await db.execute("PRAGMA busy_timeout=10000;")
            await db.execute("PRAGMA synchronous=NORMAL;")
            await db.execute("PRAGMA cache_size=-32768;")
            await db.execute("PRAGMA wal_autocheckpoint=1000;")
            observed = _ObservedConnection(self, db)
            try:
                yield observed
            finally:
                observed.release_writer()

    @asynccontextmanager
    async def _connect(self):
        shared = self._shared_connection.get()
        if shared is not None:
            self._connections_reused += 1
            yield shared
            return
        async with self._open_connection() as db:
            yield db

    @asynccontextmanager
    async def reuse_connection(self):
        """Reuse one configured connection for a bounded sequential unit of work.

        This is intentionally opt-in. Callers must await database operations in
        sequence rather than sharing the connection between concurrent tasks.
        Individual methods may still commit small maintenance writes.
        """
        shared = self._shared_connection.get()
        if shared is not None:
            self._connections_reused += 1
            yield shared
            return
        async with self._open_connection() as db:
            token = self._shared_connection.set(db)
            try:
                yield db
            finally:
                self._shared_connection.reset(token)

    async def _preflight_schema_version(self) -> None:
        """Reject a newer versioned database before running any bootstrap DDL."""
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
            )
            if not await cur.fetchone():
                return
            cur = await db.execute(
                "SELECT current_version FROM schema_version WHERE singleton=1"
            )
            row = await cur.fetchone()
            if row and int(row[0]) > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema version {int(row[0])} is newer than this bot supports ({SCHEMA_VERSION}); "
                    "refusing an unsafe downgrade"
                )

    async def init(self) -> None:
        await self._preflight_schema_version()
        async with self._connect() as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA foreign_keys=ON;")
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS characters (
                    user_id INTEGER PRIMARY KEY,
                    discord_name TEXT NOT NULL,
                    name TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    path TEXT NOT NULL,
                    spiritual_root TEXT NOT NULL,
                    concept TEXT NOT NULL,
                    gender TEXT NOT NULL DEFAULT 'neutral',
                    age_at_creation_years INTEGER NOT NULL DEFAULT 18,
                    created_game_minute INTEGER NOT NULL DEFAULT 0,
                    natural_lifespan_years INTEGER NOT NULL DEFAULT 75,
                    life_extension_years INTEGER NOT NULL DEFAULT 0,
                    life_status TEXT NOT NULL DEFAULT 'alive',
                    karma_score INTEGER NOT NULL DEFAULT 0,
                    true_death_count INTEGER NOT NULL DEFAULT 0,
                    death_game_minute INTEGER,
                    reincarnation_ready_game_minute INTEGER,
                    realm_index INTEGER NOT NULL DEFAULT 0,
                    phase INTEGER NOT NULL DEFAULT 1,
                    cultivation INTEGER NOT NULL DEFAULT 0,
                    body_realm_index INTEGER NOT NULL DEFAULT 0,
                    body_phase INTEGER NOT NULL DEFAULT 1,
                    body_cultivation INTEGER NOT NULL DEFAULT 0,
                    sense_power_bonus INTEGER NOT NULL DEFAULT 0,
                    sense_precision_bonus INTEGER NOT NULL DEFAULT 0,
                    sense_range_bonus INTEGER NOT NULL DEFAULT 0,
                    concealment_bonus INTEGER NOT NULL DEFAULT 0,
                    concealment_active INTEGER NOT NULL DEFAULT 0,
                    qi INTEGER NOT NULL DEFAULT 10,
                    qi_max INTEGER NOT NULL DEFAULT 10,
                    vitality INTEGER NOT NULL DEFAULT 12,
                    vitality_max INTEGER NOT NULL DEFAULT 12,
                    spirit_stones INTEGER NOT NULL DEFAULT 25,
                    insight_xp INTEGER NOT NULL DEFAULT 0,
                    location TEXT NOT NULL,
                    attributes_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS inventory (
                    user_id INTEGER NOT NULL,
                    item_id TEXT NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, item_id),
                    FOREIGN KEY (user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS cooldowns (
                    user_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    available_at REAL NOT NULL,
                    PRIMARY KEY (user_id, action)
                );

                CREATE TABLE IF NOT EXISTS character_manuals (
                    user_id INTEGER NOT NULL,
                    manual_id TEXT NOT NULL,
                    mastery INTEGER NOT NULL DEFAULT 0,
                    practice INTEGER NOT NULL DEFAULT 0,
                    learned_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (user_id, manual_id),
                    FOREIGN KEY (user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS scene_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER NOT NULL,
                    user_id INTEGER,
                    speaker TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_scene_history_channel
                    ON scene_history(channel_id, id DESC);

                CREATE TABLE IF NOT EXISTS npc_memory (
                    user_id INTEGER NOT NULL,
                    npc_name TEXT NOT NULL,
                    memory TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(user_id, npc_name)
                );

                CREATE TABLE IF NOT EXISTS world_state (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS event_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS realm_perfection (
                    user_id INTEGER NOT NULL,
                    realm_index INTEGER NOT NULL,
                    active INTEGER NOT NULL DEFAULT 0,
                    completed INTEGER NOT NULL DEFAULT 0,
                    progress INTEGER NOT NULL DEFAULT 0,
                    training_progress INTEGER NOT NULL DEFAULT 0,
                    quest_index INTEGER NOT NULL DEFAULT 0,
                    quest_preparation INTEGER NOT NULL DEFAULT 0,
                    completed_quests INTEGER NOT NULL DEFAULT 0,
                    discovered_json TEXT NOT NULL DEFAULT '[]',
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(user_id, realm_index),
                    FOREIGN KEY (user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS body_realm_perfection (
                    user_id INTEGER NOT NULL,
                    realm_index INTEGER NOT NULL,
                    active INTEGER NOT NULL DEFAULT 0,
                    completed INTEGER NOT NULL DEFAULT 0,
                    progress INTEGER NOT NULL DEFAULT 0,
                    training_progress INTEGER NOT NULL DEFAULT 0,
                    quest_index INTEGER NOT NULL DEFAULT 0,
                    quest_preparation INTEGER NOT NULL DEFAULT 0,
                    completed_quests INTEGER NOT NULL DEFAULT 0,
                    discovered_json TEXT NOT NULL DEFAULT '[]',
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(user_id, realm_index),
                    FOREIGN KEY (user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS world_events (
                    event_key TEXT PRIMARY KEY,
                    dedupe_key TEXT NOT NULL DEFAULT '',
                    event_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    location TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    starts_at REAL NOT NULL,
                    ends_at REAL NOT NULL,
                    thread_id INTEGER,
                    announcement_channel_id INTEGER,
                    announcement_message_id INTEGER
                );

                CREATE TABLE IF NOT EXISTS event_threads (
                    thread_id INTEGER PRIMARY KEY,
                    event_key TEXT,
                    event_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    channel_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    announcement_channel_id INTEGER,
                    announcement_message_id INTEGER,
                    triggered_by INTEGER,
                    starts_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS server_config (
                    guild_id INTEGER PRIMARY KEY,
                    announcement_channel_id INTEGER,
                    event_scene_channel_id INTEGER,
                    home_scene_channel_id INTEGER,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS event_claims (
                    user_id INTEGER NOT NULL,
                    event_key TEXT NOT NULL,
                    claimed_at REAL NOT NULL,
                    PRIMARY KEY(user_id, event_key),
                    FOREIGN KEY (user_id) REFERENCES characters(user_id) ON DELETE CASCADE,
                    FOREIGN KEY (event_key) REFERENCES world_events(event_key) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS secret_realm_runs (
                    user_id INTEGER PRIMARY KEY,
                    realm_id TEXT NOT NULL,
                    event_key TEXT NOT NULL,
                    room_index INTEGER NOT NULL DEFAULT 0,
                    danger INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    entered_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS sect_membership (
                    user_id INTEGER PRIMARY KEY,
                    sect_name TEXT NOT NULL,
                    rank_name TEXT NOT NULL DEFAULT 'Disciple',
                    rank_level INTEGER NOT NULL DEFAULT 0,
                    joined_at REAL NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS sect_lineage (
                    disciple_user_id INTEGER PRIMARY KEY,
                    master_user_id INTEGER NOT NULL,
                    accepted_at REAL NOT NULL,
                    FOREIGN KEY (disciple_user_id) REFERENCES characters(user_id) ON DELETE CASCADE,
                    FOREIGN KEY (master_user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_sect_lineage_master
                    ON sect_lineage(master_user_id, accepted_at);

                CREATE TABLE IF NOT EXISTS inheritances (
                    user_id INTEGER NOT NULL,
                    inheritance_id TEXT NOT NULL,
                    source_realm_id TEXT NOT NULL,
                    acquired_at REAL NOT NULL,
                    PRIMARY KEY(user_id, inheritance_id),
                    FOREIGN KEY (user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS catalog_locations (
                    name TEXT PRIMARY KEY,
                    data_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS catalog_npcs (
                    name TEXT PRIMARY KEY,
                    data_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS catalog_recipes (
                    name TEXT PRIMARY KEY,
                    data_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS currency_wallets (
                    user_id INTEGER NOT NULL,
                    currency_id TEXT NOT NULL,
                    balance INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(user_id, currency_id),
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS storage_containers (
                    user_id INTEGER PRIMARY KEY,
                    container_id TEXT NOT NULL DEFAULT 'common_spatial_pouch',
                    name TEXT NOT NULL DEFAULT 'Common Spatial Pouch',
                    grade TEXT NOT NULL DEFAULT 'Mortal',
                    slot_capacity INTEGER NOT NULL DEFAULT 24,
                    living_space INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS storage_inventory (
                    user_id INTEGER NOT NULL,
                    item_id TEXT NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(user_id, item_id),
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS active_effects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    effect_key TEXT NOT NULL,
                    name TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    effect_json TEXT NOT NULL,
                    stacks INTEGER NOT NULL DEFAULT 1,
                    starts_game_minute INTEGER NOT NULL,
                    ends_game_minute INTEGER,
                    created_at REAL NOT NULL,
                    UNIQUE(user_id, effect_key, source_type, source_id),
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_active_effects_user
                    ON active_effects(user_id, ends_game_minute);

                CREATE TABLE IF NOT EXISTS character_conditions (
                    condition_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    condition_key TEXT NOT NULL,
                    category TEXT NOT NULL,
                    name TEXT NOT NULL,
                    severity INTEGER NOT NULL DEFAULT 1,
                    state TEXT NOT NULL DEFAULT 'active',
                    source_type TEXT NOT NULL DEFAULT 'system',
                    source_id TEXT NOT NULL DEFAULT '',
                    effect_json TEXT NOT NULL DEFAULT '{}',
                    created_game_minute INTEGER NOT NULL DEFAULT 0,
                    updated_game_minute INTEGER NOT NULL DEFAULT 0,
                    resolved_game_minute INTEGER,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_conditions_user_state
                    ON character_conditions(user_id,state,severity DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_conditions_active_key
                    ON character_conditions(user_id,condition_key) WHERE state='active';

                CREATE TABLE IF NOT EXISTS tribulation_state (
                    user_id INTEGER NOT NULL,
                    gate_realm_index INTEGER NOT NULL,
                    preparation INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    cleared INTEGER NOT NULL DEFAULT 0,
                    last_result TEXT NOT NULL DEFAULT '',
                    updated_game_minute INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(user_id,gate_realm_index),
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS tribulation_attempts (
                    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    gate_realm_index INTEGER NOT NULL,
                    preparation_used INTEGER NOT NULL DEFAULT 0,
                    waves_json TEXT NOT NULL DEFAULT '[]',
                    success INTEGER NOT NULL DEFAULT 0,
                    created_game_minute INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_tribulation_attempts_user
                    ON tribulation_attempts(user_id,attempt_id DESC);

                CREATE TABLE IF NOT EXISTS profession_progress (
                    user_id INTEGER NOT NULL,
                    profession TEXT NOT NULL,
                    level INTEGER NOT NULL DEFAULT 0,
                    xp INTEGER NOT NULL DEFAULT 0,
                    successes INTEGER NOT NULL DEFAULT 0,
                    failures INTEGER NOT NULL DEFAULT 0,
                    quality_points INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(user_id,profession),
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS faction_reputation (
                    user_id INTEGER NOT NULL,
                    faction_key TEXT NOT NULL,
                    score INTEGER NOT NULL DEFAULT 0,
                    last_reason TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(user_id,faction_key),
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS crime_records (
                    crime_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    jurisdiction TEXT NOT NULL,
                    crime_type TEXT NOT NULL,
                    severity INTEGER NOT NULL DEFAULT 1,
                    evidence INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'open',
                    description TEXT NOT NULL DEFAULT '',
                    created_game_minute INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_crimes_user_status ON crime_records(user_id,status,crime_id DESC);
                CREATE TABLE IF NOT EXISTS witness_records (
                    witness_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    crime_id INTEGER NOT NULL,
                    witness_type TEXT NOT NULL,
                    witness_key TEXT NOT NULL,
                    reliability INTEGER NOT NULL DEFAULT 50,
                    statement TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    FOREIGN KEY(crime_id) REFERENCES crime_records(crime_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS bounties (
                    bounty_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    jurisdiction TEXT NOT NULL,
                    amount INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'active',
                    reason TEXT NOT NULL DEFAULT '',
                    source_crime_id INTEGER,
                    created_game_minute INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(source_crime_id) REFERENCES crime_records(crime_id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_bounties_user_status ON bounties(user_id,status,bounty_id DESC);
                CREATE TABLE IF NOT EXISTS grudges (
                    grudge_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    holder_type TEXT NOT NULL,
                    holder_key TEXT NOT NULL,
                    intensity INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'active',
                    reason TEXT NOT NULL DEFAULT '',
                    created_game_minute INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_grudges_active_holder
                    ON grudges(user_id,holder_type,holder_key) WHERE status='active';

                CREATE TABLE IF NOT EXISTS sects (
                    sect_name TEXT PRIMARY KEY,
                    prestige INTEGER NOT NULL DEFAULT 0,
                    treasury_stones INTEGER NOT NULL DEFAULT 0,
                    policy_json TEXT NOT NULL DEFAULT '{}',
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sect_treasury (
                    sect_name TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(sect_name, item_id),
                    FOREIGN KEY(sect_name) REFERENCES sects(sect_name) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS auctions (
                    auction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    house_id TEXT NOT NULL,
                    seller_user_id INTEGER NOT NULL,
                    item_id TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    currency_id TEXT NOT NULL,
                    starting_bid INTEGER NOT NULL,
                    current_bid INTEGER NOT NULL DEFAULT 0,
                    current_bidder_user_id INTEGER,
                    anonymous INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    ends_at REAL NOT NULL,
                    FOREIGN KEY(seller_user_id) REFERENCES characters(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(current_bidder_user_id) REFERENCES characters(user_id) ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_auctions_active_end
                    ON auctions(active, ends_at);

                CREATE TABLE IF NOT EXISTS auction_bids (
                    bid_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    auction_id INTEGER NOT NULL,
                    bidder_user_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(auction_id) REFERENCES auctions(auction_id) ON DELETE CASCADE,
                    FOREIGN KEY(bidder_user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS auction_door_risks (
                    risk_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, auction_id INTEGER NOT NULL,
                    item_id TEXT NOT NULL, risk_level TEXT NOT NULL, chance_percent INTEGER NOT NULL,
                    created_at REAL NOT NULL, consumed_at REAL, UNIQUE(user_id,auction_id),
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_auction_door_risks_pending ON auction_door_risks(user_id,consumed_at,created_at);

                CREATE TABLE IF NOT EXISTS battles (
                    battle_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, npc_name TEXT NOT NULL,
                    npc_realm_index INTEGER NOT NULL, npc_stage INTEGER NOT NULL,
                    player_hp INTEGER NOT NULL, player_hp_max INTEGER NOT NULL DEFAULT 1,
                    npc_hp INTEGER NOT NULL, npc_hp_max INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'active', location TEXT NOT NULL, source TEXT NOT NULL,
                    target_key TEXT NOT NULL DEFAULT '', thread_id INTEGER,
                    npc_suppressed_turns INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 0,
                    final_outcome TEXT NOT NULL DEFAULT '', finalized_at REAL,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_battles_user_active ON battles(user_id,status,updated_at);

                CREATE TABLE IF NOT EXISTS seclusion_sessions (
                    user_id INTEGER PRIMARY KEY,
                    mode TEXT NOT NULL,
                    started_game_minute INTEGER NOT NULL,
                    ends_game_minute INTEGER NOT NULL,
                    last_settled_game_minute INTEGER NOT NULL,
                    start_location TEXT NOT NULL,
                    environment_mult REAL NOT NULL DEFAULT 1.0,
                    accumulated_gain INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'active',
                    ended_reason TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_seclusion_active_end ON seclusion_sessions(status,ends_game_minute);

                CREATE TABLE IF NOT EXISTS world_action_events (
                    action_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    action_type TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_key TEXT NOT NULL,
                    location TEXT NOT NULL,
                    severity INTEGER NOT NULL DEFAULT 1,
                    game_minute INTEGER NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_world_action_events_recent ON world_action_events(game_minute DESC,action_id DESC);
                CREATE INDEX IF NOT EXISTS idx_world_action_target ON world_action_events(target_type,target_key,action_id DESC);

                CREATE TABLE IF NOT EXISTS player_families (
                    family_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, founder_user_id INTEGER NOT NULL, created_at REAL NOT NULL,
                    FOREIGN KEY(founder_user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS player_family_members (
                    family_id INTEGER NOT NULL, user_id INTEGER NOT NULL UNIQUE, seniority_order INTEGER NOT NULL, joined_at REAL NOT NULL,
                    PRIMARY KEY(family_id,user_id), FOREIGN KEY(family_id) REFERENCES player_families(family_id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_player_family_order ON player_family_members(family_id,seniority_order);
                CREATE TABLE IF NOT EXISTS player_family_invites (
                    family_id INTEGER NOT NULL, inviter_user_id INTEGER NOT NULL, invitee_user_id INTEGER NOT NULL UNIQUE, requested_order INTEGER NOT NULL,
                    created_at REAL NOT NULL, expires_at REAL NOT NULL, PRIMARY KEY(family_id,invitee_user_id),
                    FOREIGN KEY(family_id) REFERENCES player_families(family_id) ON DELETE CASCADE,
                    FOREIGN KEY(inviter_user_id) REFERENCES characters(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(invitee_user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS family_children (
                    child_id INTEGER PRIMARY KEY AUTOINCREMENT, family_id INTEGER NOT NULL, parent_user_id INTEGER NOT NULL,
                    name TEXT NOT NULL, gender TEXT NOT NULL DEFAULT 'neutral', birth_game_minute INTEGER NOT NULL,
                    spiritual_root TEXT NOT NULL, cultivation_potential INTEGER NOT NULL DEFAULT 0, can_cultivate INTEGER NOT NULL DEFAULT 0,
                    awakening_state INTEGER NOT NULL DEFAULT 0, natural_lifespan_years INTEGER NOT NULL DEFAULT 75, status TEXT NOT NULL DEFAULT 'alive',
                    created_at REAL NOT NULL,
                    FOREIGN KEY(family_id) REFERENCES player_families(family_id) ON DELETE CASCADE,
                    FOREIGN KEY(parent_user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_family_children_family ON family_children(family_id,birth_game_minute);

                CREATE TABLE IF NOT EXISTS birth_families (
                    family_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    family_name TEXT NOT NULL, surname TEXT NOT NULL, archetype TEXT NOT NULL,
                    tier INTEGER NOT NULL DEFAULT 1, wealth INTEGER NOT NULL DEFAULT 20,
                    influence INTEGER NOT NULL DEFAULT 10, stability INTEGER NOT NULL DEFAULT 60,
                    alignment_bias INTEGER NOT NULL DEFAULT 0, location TEXT NOT NULL,
                    head_name TEXT NOT NULL, head_gender TEXT NOT NULL DEFAULT 'neutral',
                    head_title TEXT NOT NULL DEFAULT 'Family Head', head_realm_index INTEGER NOT NULL DEFAULT 0,
                    head_phase INTEGER NOT NULL DEFAULT 1, treasury_balance INTEGER NOT NULL DEFAULT 0,
                    generation INTEGER NOT NULL DEFAULT 1, created_game_minute INTEGER NOT NULL DEFAULT 0,
                    last_simulated_game_minute INTEGER NOT NULL DEFAULT 0, history_json TEXT NOT NULL DEFAULT '[]',
                    line_status TEXT NOT NULL DEFAULT 'active', extinct_afterlife_minute INTEGER,
                    clan_structure TEXT NOT NULL DEFAULT 'extended_household', bloodline_name TEXT NOT NULL DEFAULT 'None',
                    bloodline_affinity TEXT NOT NULL DEFAULT 'None', bloodline_trait TEXT NOT NULL DEFAULT 'No awakened ancestral bloodline',
                    bloodline_purity INTEGER NOT NULL DEFAULT 0, branch_count INTEGER NOT NULL DEFAULT 1,
                    retainer_count INTEGER NOT NULL DEFAULT 0, confederacy_name TEXT NOT NULL DEFAULT 'None',
                    created_at REAL NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS character_birth_family (
                    user_id INTEGER PRIMARY KEY, family_id INTEGER NOT NULL, birth_order INTEGER NOT NULL DEFAULT 1,
                    generation INTEGER NOT NULL DEFAULT 1, last_support_game_minute INTEGER NOT NULL DEFAULT -999999999,
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(family_id) REFERENCES birth_families(family_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS birth_family_npcs (
                    npc_id INTEGER PRIMARY KEY AUTOINCREMENT, family_id INTEGER NOT NULL, name TEXT NOT NULL,
                    relation TEXT NOT NULL, gender TEXT NOT NULL DEFAULT 'neutral', age_at_creation INTEGER NOT NULL DEFAULT 18,
                    birth_game_minute INTEGER NOT NULL DEFAULT 0, natural_lifespan_years INTEGER NOT NULL DEFAULT 75,
                    status TEXT NOT NULL DEFAULT 'alive', spiritual_root TEXT NOT NULL DEFAULT 'Mortal Root',
                    realm_index INTEGER NOT NULL DEFAULT 0, phase INTEGER NOT NULL DEFAULT 1, personality TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    FOREIGN KEY(family_id) REFERENCES birth_families(family_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_birth_family_npcs_family ON birth_family_npcs(family_id,status,relation);

                CREATE TABLE IF NOT EXISTS world_simulation_state (
                    system TEXT PRIMARY KEY,
                    last_game_minute INTEGER NOT NULL DEFAULT 0,
                    interval_game_minutes INTEGER NOT NULL DEFAULT 1440,
                    last_run_real REAL NOT NULL DEFAULT 0,
                    runs INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS civilization_regions (
                    location TEXT PRIMARY KEY, world_name TEXT NOT NULL, population INTEGER NOT NULL DEFAULT 1000,
                    prosperity INTEGER NOT NULL DEFAULT 50, security INTEGER NOT NULL DEFAULT 50,
                    spirit_resources INTEGER NOT NULL DEFAULT 50, food_supply INTEGER NOT NULL DEFAULT 50,
                    migration_pressure INTEGER NOT NULL DEFAULT 0, unrest INTEGER NOT NULL DEFAULT 0,
                    last_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS civilization_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT, location TEXT NOT NULL, event_text TEXT NOT NULL,
                    severity INTEGER NOT NULL DEFAULT 1, game_minute INTEGER NOT NULL, created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_civilization_events_location ON civilization_events(location,event_id DESC);

                CREATE TABLE IF NOT EXISTS npc_civilization_state (
                    npc_name TEXT PRIMARY KEY, home_location TEXT NOT NULL, current_location TEXT NOT NULL, world_name TEXT NOT NULL,
                    profession TEXT NOT NULL, faction TEXT NOT NULL DEFAULT 'Independent', wealth INTEGER NOT NULL DEFAULT 20,
                    influence INTEGER NOT NULL DEFAULT 10, ambition INTEGER NOT NULL DEFAULT 50, realm_index INTEGER NOT NULL DEFAULT 0,
                    phase INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'alive', activity TEXT NOT NULL DEFAULT 'Following established routine',
                    last_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sect_politics_state (
                    sect_name TEXT PRIMARY KEY, alignment TEXT NOT NULL DEFAULT 'Neutral', specialty TEXT NOT NULL DEFAULT '',
                    influence INTEGER NOT NULL DEFAULT 50, cohesion INTEGER NOT NULL DEFAULT 50, resources INTEGER NOT NULL DEFAULT 50,
                    recruitment_pressure INTEGER NOT NULL DEFAULT 50, doctrine_pressure INTEGER NOT NULL DEFAULT 50,
                    leader_policy TEXT NOT NULL DEFAULT 'Balanced', last_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sect_factions (
                    faction_id INTEGER PRIMARY KEY AUTOINCREMENT, sect_name TEXT NOT NULL, faction_name TEXT NOT NULL, agenda TEXT NOT NULL,
                    power INTEGER NOT NULL DEFAULT 33, loyalty INTEGER NOT NULL DEFAULT 50, updated_at REAL NOT NULL,
                    UNIQUE(sect_name,faction_name)
                );
                CREATE TABLE IF NOT EXISTS sect_relations (
                    sect_a TEXT NOT NULL, sect_b TEXT NOT NULL, relation_score INTEGER NOT NULL DEFAULT 0, relation_type TEXT NOT NULL DEFAULT 'neutral',
                    treaty_status TEXT NOT NULL DEFAULT 'none', updated_at REAL NOT NULL, PRIMARY KEY(sect_a,sect_b)
                );
                CREATE TABLE IF NOT EXISTS sect_politics_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT, sect_name TEXT NOT NULL, event_text TEXT NOT NULL,
                    severity INTEGER NOT NULL DEFAULT 1, game_minute INTEGER NOT NULL, created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sect_politics_events ON sect_politics_events(sect_name,event_id DESC);

                CREATE TABLE IF NOT EXISTS economy_markets (
                    location TEXT NOT NULL, item_id TEXT NOT NULL, world_name TEXT NOT NULL, currency_id TEXT NOT NULL,
                    base_price INTEGER NOT NULL DEFAULT 1, supply INTEGER NOT NULL DEFAULT 10, demand INTEGER NOT NULL DEFAULT 40,
                    price_index REAL NOT NULL DEFAULT 1.0, last_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL,
                    PRIMARY KEY(location,item_id)
                );
                CREATE TABLE IF NOT EXISTS economy_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT, location TEXT NOT NULL, item_id TEXT, event_text TEXT NOT NULL,
                    game_minute INTEGER NOT NULL, created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS martial_clan_branches (
                    branch_id INTEGER PRIMARY KEY AUTOINCREMENT, family_id INTEGER NOT NULL, branch_name TEXT NOT NULL,
                    branch_type TEXT NOT NULL DEFAULT 'cadet', leader_name TEXT NOT NULL, members_estimate INTEGER NOT NULL DEFAULT 10,
                    martial_strength INTEGER NOT NULL DEFAULT 20, wealth_share INTEGER NOT NULL DEFAULT 10, loyalty INTEGER NOT NULL DEFAULT 60,
                    status TEXT NOT NULL DEFAULT 'active', updated_at REAL NOT NULL,
                    FOREIGN KEY(family_id) REFERENCES birth_families(family_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_martial_clan_branches ON martial_clan_branches(family_id,status);
                CREATE TABLE IF NOT EXISTS martial_clan_retainers (
                    retainer_id INTEGER PRIMARY KEY AUTOINCREMENT, family_id INTEGER NOT NULL, group_name TEXT NOT NULL, leader_name TEXT NOT NULL,
                    role TEXT NOT NULL, members INTEGER NOT NULL DEFAULT 1, realm_index INTEGER NOT NULL DEFAULT 0, loyalty INTEGER NOT NULL DEFAULT 60,
                    upkeep INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'active', updated_at REAL NOT NULL,
                    FOREIGN KEY(family_id) REFERENCES birth_families(family_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_martial_clan_retainers ON martial_clan_retainers(family_id,status);
                CREATE TABLE IF NOT EXISTS martial_clan_relations (
                    relation_id INTEGER PRIMARY KEY AUTOINCREMENT, family_id INTEGER NOT NULL, partner_family_id INTEGER, partner_name TEXT NOT NULL,
                    relation_type TEXT NOT NULL, relation_score INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
                    started_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL,
                    FOREIGN KEY(family_id) REFERENCES birth_families(family_id) ON DELETE CASCADE,
                    FOREIGN KEY(partner_family_id) REFERENCES birth_families(family_id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_martial_clan_relations ON martial_clan_relations(family_id,active);
                CREATE TABLE IF NOT EXISTS reincarnation_state (
                    user_id INTEGER PRIMARY KEY, family_id INTEGER NOT NULL, death_game_minute INTEGER NOT NULL,
                    ready_game_minute INTEGER NOT NULL, death_reason TEXT NOT NULL, previous_name TEXT NOT NULL,
                    previous_generation INTEGER NOT NULL DEFAULT 1, karma_at_death INTEGER NOT NULL DEFAULT 0,
                    family_target_minutes INTEGER NOT NULL DEFAULT 0, family_simulated_minutes INTEGER NOT NULL DEFAULT 0,
                    afterlife_started_at REAL NOT NULL DEFAULT 0, reincarnation_ready_at REAL NOT NULL DEFAULT 0,
                    rebirth_mode TEXT NOT NULL DEFAULT 'samsara', target_world TEXT NOT NULL DEFAULT 'Mortal World',
                    samsara_lives_count INTEGER NOT NULL DEFAULT 0, samsara_history_json TEXT NOT NULL DEFAULT '[]',
                    memory_retention INTEGER NOT NULL DEFAULT 0, talent_retention INTEGER NOT NULL DEFAULT 0,
                    comprehension_retention INTEGER NOT NULL DEFAULT 0, insight_retention INTEGER NOT NULL DEFAULT 0,
                    legacy_points INTEGER NOT NULL DEFAULT 0, special_trait TEXT NOT NULL DEFAULT '',
                    karmic_fortune INTEGER NOT NULL DEFAULT 0, previous_realm_index INTEGER NOT NULL DEFAULT 0,
                    previous_phase INTEGER NOT NULL DEFAULT 1, previous_body_realm_index INTEGER NOT NULL DEFAULT 0,
                    previous_body_phase INTEGER NOT NULL DEFAULT 1, previous_spiritual_root TEXT, previous_path TEXT,
                    previous_insight_xp INTEGER NOT NULL DEFAULT 0, law_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    active INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(family_id) REFERENCES birth_families(family_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS soul_legacy (
                    user_id INTEGER PRIMARY KEY, incarnation_count INTEGER NOT NULL DEFAULT 1,
                    legacy_points INTEGER NOT NULL DEFAULT 0, memory_seed INTEGER NOT NULL DEFAULT 0,
                    talent_echo INTEGER NOT NULL DEFAULT 0, law_echo INTEGER NOT NULL DEFAULT 0,
                    insight_echo INTEGER NOT NULL DEFAULT 0, karmic_fortune INTEGER NOT NULL DEFAULT 0,
                    special_trait TEXT NOT NULL DEFAULT '', awakened_memory INTEGER NOT NULL DEFAULT 0,
                    past_lives_json TEXT NOT NULL DEFAULT '[]', updated_at REAL NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS admin_audit_log (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_user_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL DEFAULT '',
                    before_json TEXT NOT NULL DEFAULT '{}',
                    after_json TEXT NOT NULL DEFAULT '{}',
                    reason TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_admin_audit_created
                    ON admin_audit_log(created_at DESC);

                CREATE TABLE IF NOT EXISTS law_progress (
                    user_id INTEGER NOT NULL, law_id TEXT NOT NULL, comprehension INTEGER NOT NULL DEFAULT 0,
                    insights INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL,
                    PRIMARY KEY(user_id, law_id), FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS dao_progress (
                    user_id INTEGER NOT NULL, dao_id TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL, PRIMARY KEY(user_id, dao_id),
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS cave_abodes (
                    user_id INTEGER PRIMARY KEY, location_key TEXT NOT NULL UNIQUE, name TEXT NOT NULL, base_location TEXT NOT NULL,
                    grade TEXT NOT NULL DEFAULT 'Mortal', cultivation_level INTEGER NOT NULL DEFAULT 1, alchemy_level INTEGER NOT NULL DEFAULT 0,
                    forge_level INTEGER NOT NULL DEFAULT 0, formation_level INTEGER NOT NULL DEFAULT 0, defense_level INTEGER NOT NULL DEFAULT 0,
                    thread_id INTEGER, thread_channel_id INTEGER,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS cave_abode_access (
                    owner_user_id INTEGER NOT NULL, guest_user_id INTEGER NOT NULL, access_role TEXT NOT NULL DEFAULT 'guest', created_at REAL NOT NULL,
                    PRIMARY KEY(owner_user_id, guest_user_id),
                    FOREIGN KEY(owner_user_id) REFERENCES cave_abodes(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(guest_user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS personal_worlds (
                    user_id INTEGER PRIMARY KEY, location_key TEXT NOT NULL UNIQUE, name TEXT NOT NULL, stability INTEGER NOT NULL DEFAULT 1,
                    laws_json TEXT NOT NULL DEFAULT '{}', access_mode TEXT NOT NULL DEFAULT 'private', created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS character_spiritual_roots (
                    user_id INTEGER PRIMARY KEY,
                    grade TEXT NOT NULL DEFAULT 'Common',
                    purity INTEGER NOT NULL DEFAULT 50,
                    elements_json TEXT NOT NULL DEFAULT '[]',
                    mutation TEXT NOT NULL DEFAULT '',
                    stability INTEGER NOT NULL DEFAULT 100,
                    refinement_progress INTEGER NOT NULL DEFAULT 0,
                    compatibility INTEGER NOT NULL DEFAULT 50,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS character_bloodlines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    bloodline_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    affinity TEXT NOT NULL DEFAULT 'None',
                    purity INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL DEFAULT 'dormant',
                    evolution_stage INTEGER NOT NULL DEFAULT 0,
                    progress INTEGER NOT NULL DEFAULT 0,
                    rejection INTEGER NOT NULL DEFAULT 0,
                    mutation TEXT NOT NULL DEFAULT '',
                    primary_lineage INTEGER NOT NULL DEFAULT 0,
                    source_family_id INTEGER,
                    unlocked_techniques_json TEXT NOT NULL DEFAULT '[]',
                    updated_at REAL NOT NULL,
                    UNIQUE(user_id,bloodline_id),
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(source_family_id) REFERENCES birth_families(family_id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_character_bloodlines_user
                    ON character_bloodlines(user_id,primary_lineage DESC,id);

                CREATE TABLE IF NOT EXISTS character_physiques (
                    user_id INTEGER PRIMARY KEY,
                    physique_id TEXT NOT NULL DEFAULT 'ordinary_mortal_body',
                    name TEXT NOT NULL DEFAULT 'Ordinary Mortal Body',
                    state TEXT NOT NULL DEFAULT 'ordinary',
                    evolution_stage INTEGER NOT NULL DEFAULT 0,
                    progress INTEGER NOT NULL DEFAULT 0,
                    stability INTEGER NOT NULL DEFAULT 100,
                    instability INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS catalog_manuals (
                    name TEXT PRIMARY KEY, data_json TEXT NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS catalog_techniques (
                    name TEXT PRIMARY KEY, data_json TEXT NOT NULL, updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS spirit_beasts (
                    beast_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL, name TEXT NOT NULL, species TEXT NOT NULL,
                    rank INTEGER NOT NULL DEFAULT 0, element TEXT NOT NULL DEFAULT 'None',
                    intelligence INTEGER NOT NULL DEFAULT 10, temperament TEXT NOT NULL DEFAULT 'wary',
                    bloodline TEXT NOT NULL DEFAULT 'Common', evolution_stage INTEGER NOT NULL DEFAULT 0,
                    loyalty INTEGER NOT NULL DEFAULT 25, contract_type TEXT NOT NULL DEFAULT 'temporary',
                    active INTEGER NOT NULL DEFAULT 0, techniques_json TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_spirit_beasts_user ON spirit_beasts(user_id,active DESC,loyalty DESC);

                CREATE TABLE IF NOT EXISTS artifact_bonds (
                    user_id INTEGER NOT NULL, item_id TEXT NOT NULL, bond_level INTEGER NOT NULL DEFAULT 0,
                    resonance INTEGER NOT NULL DEFAULT 0, awakened INTEGER NOT NULL DEFAULT 0,
                    spirit_name TEXT NOT NULL DEFAULT '', temperament TEXT NOT NULL DEFAULT 'dormant',
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    PRIMARY KEY(user_id,item_id),
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS territory_state (
                    territory_key TEXT PRIMARY KEY, name TEXT NOT NULL, region TEXT NOT NULL,
                    controller_type TEXT NOT NULL DEFAULT 'neutral', controller_key TEXT NOT NULL DEFAULT '',
                    resource_type TEXT NOT NULL DEFAULT 'mixed', prosperity INTEGER NOT NULL DEFAULT 50,
                    defense INTEGER NOT NULL DEFAULT 50, unrest INTEGER NOT NULL DEFAULT 0,
                    updated_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS territory_wars (
                    war_id INTEGER PRIMARY KEY AUTOINCREMENT, attacker_key TEXT NOT NULL, defender_key TEXT NOT NULL,
                    territory_key TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
                    attacker_score INTEGER NOT NULL DEFAULT 0, defender_score INTEGER NOT NULL DEFAULT 0,
                    created_game_minute INTEGER NOT NULL DEFAULT 0, updated_game_minute INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    FOREIGN KEY(territory_key) REFERENCES territory_state(territory_key) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS caravans (
                    caravan_id INTEGER PRIMARY KEY AUTOINCREMENT, owner_type TEXT NOT NULL DEFAULT 'npc',
                    owner_key TEXT NOT NULL, origin TEXT NOT NULL, destination TEXT NOT NULL,
                    cargo_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'traveling',
                    risk INTEGER NOT NULL DEFAULT 10, depart_game_minute INTEGER NOT NULL DEFAULT 0,
                    arrive_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS item_provenance (
                    provenance_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, item_id TEXT NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 1, source_type TEXT NOT NULL DEFAULT 'unknown',
                    source_key TEXT NOT NULL DEFAULT '', ownership_mark TEXT NOT NULL DEFAULT '',
                    legal_status TEXT NOT NULL DEFAULT 'clean', authenticity INTEGER NOT NULL DEFAULT 100,
                    tracking_strength INTEGER NOT NULL DEFAULT 0, acquired_game_minute INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_item_provenance_user_item ON item_provenance(user_id,item_id,provenance_id DESC);

                CREATE TABLE IF NOT EXISTS character_social_state (
                    user_id INTEGER PRIMARY KEY, face INTEGER NOT NULL DEFAULT 0, dao_heart INTEGER NOT NULL DEFAULT 50,
                    dao_stability INTEGER NOT NULL DEFAULT 100, vow TEXT NOT NULL DEFAULT '', obsession TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL, FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS world_eras (
                    era_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                    started_game_minute INTEGER NOT NULL DEFAULT 0, ended_game_minute INTEGER, active INTEGER NOT NULL DEFAULT 1,
                    modifiers_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS parties (
                    party_id INTEGER PRIMARY KEY AUTOINCREMENT, leader_user_id INTEGER NOT NULL, name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active', created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    FOREIGN KEY(leader_user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS party_members (
                    party_id INTEGER NOT NULL, user_id INTEGER NOT NULL, role TEXT NOT NULL DEFAULT 'member', joined_at REAL NOT NULL,
                    PRIMARY KEY(party_id,user_id),
                    FOREIGN KEY(party_id) REFERENCES parties(party_id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS pvp_challenges (
                    challenge_id INTEGER PRIMARY KEY AUTOINCREMENT, challenger_user_id INTEGER NOT NULL, target_user_id INTEGER NOT NULL,
                    stakes TEXT NOT NULL DEFAULT 'honor', status TEXT NOT NULL DEFAULT 'pending', created_at REAL NOT NULL, expires_at REAL NOT NULL,
                    FOREIGN KEY(challenger_user_id) REFERENCES characters(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(target_user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS hidden_sect_membership (
                    user_id INTEGER PRIMARY KEY, sect_name TEXT NOT NULL, rank_name TEXT NOT NULL DEFAULT 'Shadow Initiate',
                    branch_name TEXT NOT NULL DEFAULT '', standing INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active',
                    joined_game_minute INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS pvp_matches (
                    match_id INTEGER PRIMARY KEY AUTOINCREMENT, challenge_id INTEGER NOT NULL UNIQUE,
                    player1_user_id INTEGER NOT NULL, player2_user_id INTEGER NOT NULL,
                    player1_hp INTEGER NOT NULL, player2_hp INTEGER NOT NULL, turn_user_id INTEGER NOT NULL,
                    player1_guard INTEGER NOT NULL DEFAULT 0, player2_guard INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'active', winner_user_id INTEGER, version INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    FOREIGN KEY(challenge_id) REFERENCES pvp_challenges(challenge_id) ON DELETE CASCADE,
                    FOREIGN KEY(player1_user_id) REFERENCES characters(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(player2_user_id) REFERENCES characters(user_id) ON DELETE CASCADE
                );
                """
            )
            # Character aging/lifespan migrations.
            cur = await db.execute("PRAGMA table_info(characters)")
            character_cols = {row[1] for row in await cur.fetchall()}
            for name, ddl in (
                ("age_at_creation_years", "INTEGER NOT NULL DEFAULT 18"),
                ("created_game_minute", "INTEGER NOT NULL DEFAULT 0"),
                ("natural_lifespan_years", "INTEGER NOT NULL DEFAULT 75"),
                ("life_extension_years", "INTEGER NOT NULL DEFAULT 0"),
                ("life_status", "TEXT NOT NULL DEFAULT 'alive'"),
                ("karma_score", "INTEGER NOT NULL DEFAULT 0"),
                ("true_death_count", "INTEGER NOT NULL DEFAULT 0"),
                ("death_game_minute", "INTEGER"),
                ("reincarnation_ready_game_minute", "INTEGER"),
            ):
                if name not in character_cols:
                    await db.execute(f"ALTER TABLE characters ADD COLUMN {name} {ddl}")

            cur = await db.execute("PRAGMA table_info(birth_families)")
            family_cols = {row[1] for row in await cur.fetchall()}
            for name, ddl in (
                ("line_status", "TEXT NOT NULL DEFAULT 'active'"),
                ("extinct_afterlife_minute", "INTEGER"),
                ("clan_structure", "TEXT NOT NULL DEFAULT 'extended_household'"),
                ("bloodline_name", "TEXT NOT NULL DEFAULT 'None'"),
                ("bloodline_affinity", "TEXT NOT NULL DEFAULT 'None'"),
                ("bloodline_trait", "TEXT NOT NULL DEFAULT 'No awakened ancestral bloodline'"),
                ("bloodline_purity", "INTEGER NOT NULL DEFAULT 0"),
                ("branch_count", "INTEGER NOT NULL DEFAULT 1"),
                ("retainer_count", "INTEGER NOT NULL DEFAULT 0"),
                ("confederacy_name", "TEXT NOT NULL DEFAULT 'None'"),
            ):
                if name not in family_cols:
                    await db.execute(f"ALTER TABLE birth_families ADD COLUMN {name} {ddl}")

            cur = await db.execute("PRAGMA table_info(reincarnation_state)")
            reincarnation_cols = {row[1] for row in await cur.fetchall()}
            for name, ddl in (
                ("family_target_minutes", "INTEGER NOT NULL DEFAULT 0"),
                ("family_simulated_minutes", "INTEGER NOT NULL DEFAULT 0"),
                ("afterlife_started_at", "REAL NOT NULL DEFAULT 0"),
                ("reincarnation_ready_at", "REAL NOT NULL DEFAULT 0"),
                ("rebirth_mode", "TEXT NOT NULL DEFAULT 'samsara'"),
                ("target_world", "TEXT NOT NULL DEFAULT 'Mortal World'"),
                ("samsara_lives_count", "INTEGER NOT NULL DEFAULT 0"),
                ("samsara_history_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("memory_retention", "INTEGER NOT NULL DEFAULT 0"),
                ("talent_retention", "INTEGER NOT NULL DEFAULT 0"),
                ("comprehension_retention", "INTEGER NOT NULL DEFAULT 0"),
                ("insight_retention", "INTEGER NOT NULL DEFAULT 0"),
                ("legacy_points", "INTEGER NOT NULL DEFAULT 0"),
                ("special_trait", "TEXT NOT NULL DEFAULT ''"),
                ("karmic_fortune", "INTEGER NOT NULL DEFAULT 0"),
                ("previous_realm_index", "INTEGER NOT NULL DEFAULT 0"),
                ("previous_phase", "INTEGER NOT NULL DEFAULT 1"),
                ("previous_body_realm_index", "INTEGER NOT NULL DEFAULT 0"),
                ("previous_body_phase", "INTEGER NOT NULL DEFAULT 1"),
                ("previous_spiritual_root", "TEXT"),
                ("previous_path", "TEXT"),
                ("previous_insight_xp", "INTEGER NOT NULL DEFAULT 0"),
                ("law_snapshot_json", "TEXT NOT NULL DEFAULT '{}'"),
            ):
                if name not in reincarnation_cols:
                    await db.execute(f"ALTER TABLE reincarnation_state ADD COLUMN {name} {ddl}")

            cur = await db.execute("PRAGMA table_info(server_config)")
            server_cols = {row[1] for row in await cur.fetchall()}
            if "home_scene_channel_id" not in server_cols:
                await db.execute("ALTER TABLE server_config ADD COLUMN home_scene_channel_id INTEGER")

            cur = await db.execute("PRAGMA table_info(cave_abodes)")
            abode_cols = {row[1] for row in await cur.fetchall()}
            for name in ("thread_id", "thread_channel_id"):
                if name not in abode_cols:
                    await db.execute(f"ALTER TABLE cave_abodes ADD COLUMN {name} INTEGER")

            # Lightweight migrations for existing single-server databases.
            cur = await db.execute("PRAGMA table_info(world_events)")
            cols = {row[1] for row in await cur.fetchall()}
            if "dedupe_key" not in cols:
                await db.execute("ALTER TABLE world_events ADD COLUMN dedupe_key TEXT NOT NULL DEFAULT ''")
            for name in ("thread_id", "announcement_channel_id", "announcement_message_id"):
                if name not in cols:
                    await db.execute(f"ALTER TABLE world_events ADD COLUMN {name} INTEGER")
            await db.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_world_events_active_dedupe
                   ON world_events(dedupe_key,location) WHERE active=1 AND dedupe_key<>''"""
            )

            cur = await db.execute("PRAGMA table_info(event_threads)")
            thread_cols = {row[1] for row in await cur.fetchall()}
            for name in ("announcement_channel_id", "announcement_message_id"):
                if name not in thread_cols:
                    await db.execute(f"ALTER TABLE event_threads ADD COLUMN {name} INTEGER")

            cur = await db.execute("PRAGMA table_info(battles)")
            battle_cols = {row[1] for row in await cur.fetchall()}
            for name, ddl in (
                ("player_hp_max", "INTEGER NOT NULL DEFAULT 1"),
                ("npc_hp_max", "INTEGER NOT NULL DEFAULT 1"),
                ("target_key", "TEXT NOT NULL DEFAULT ''"),
                ("npc_suppressed_turns", "INTEGER NOT NULL DEFAULT 0"),
                ("version", "INTEGER NOT NULL DEFAULT 0"),
                ("final_outcome", "TEXT NOT NULL DEFAULT ''"),
                ("finalized_at", "REAL"),
            ):
                if name not in battle_cols:
                    await db.execute(f"ALTER TABLE battles ADD COLUMN {name} {ddl}")
            await db.execute(
                "UPDATE battles SET player_hp_max=MAX(1,player_hp_max,player_hp),npc_hp_max=MAX(1,npc_hp_max,npc_hp)"
            )
            await db.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_battles_target_active
                   ON battles(target_key) WHERE status='active' AND target_key<>''"""
            )

            # Older Perfect-Realm builds created these tables before multi-step
            # quest preparation existed. CREATE TABLE IF NOT EXISTS does not add
            # later columns, so migrate them explicitly for upgrade safety.
            for table in ("realm_perfection", "body_realm_perfection"):
                cur = await db.execute(f"PRAGMA table_info({table})")
                perfection_cols = {row[1] for row in await cur.fetchall()}
                if "quest_preparation" not in perfection_cols:
                    await db.execute(
                        f"ALTER TABLE {table} ADD COLUMN quest_preparation INTEGER NOT NULL DEFAULT 0"
                    )

            cur = await db.execute("PRAGMA table_info(characters)")
            character_cols = {row[1] for row in await cur.fetchall()}
            if "address_style" not in character_cols:
                await db.execute(
                    "ALTER TABLE characters ADD COLUMN address_style TEXT NOT NULL DEFAULT 'neutral'"
                )
            if "gender" not in character_cols:
                await db.execute(
                    "ALTER TABLE characters ADD COLUMN gender TEXT NOT NULL DEFAULT 'neutral'"
                )
            if "body_realm_index" not in character_cols:
                await db.execute(
                    "ALTER TABLE characters ADD COLUMN body_realm_index INTEGER NOT NULL DEFAULT 0"
                )
            if "body_phase" not in character_cols:
                await db.execute(
                    "ALTER TABLE characters ADD COLUMN body_phase INTEGER NOT NULL DEFAULT 1"
                )
            if "body_cultivation" not in character_cols:
                await db.execute(
                    "ALTER TABLE characters ADD COLUMN body_cultivation INTEGER NOT NULL DEFAULT 0"
                )
            for column_name in ("sense_power_bonus", "sense_precision_bonus", "sense_range_bonus", "concealment_bonus"):
                if column_name not in character_cols:
                    await db.execute(
                        f"ALTER TABLE characters ADD COLUMN {column_name} INTEGER NOT NULL DEFAULT 0"
                    )
            if "concealment_active" not in character_cols:
                await db.execute(
                    "ALTER TABLE characters ADD COLUMN concealment_active INTEGER NOT NULL DEFAULT 0"
                )

            cur = await db.execute("PRAGMA table_info(sect_membership)")
            membership_cols = {row[1] for row in await cur.fetchall()}
            if "contribution_points" not in membership_cols:
                await db.execute(
                    "ALTER TABLE sect_membership ADD COLUMN contribution_points INTEGER NOT NULL DEFAULT 0"
                )
            if "influence" not in membership_cols:
                await db.execute(
                    "ALTER TABLE sect_membership ADD COLUMN influence INTEGER NOT NULL DEFAULT 0"
                )

            cur = await db.execute("PRAGMA table_info(sect_lineage)")
            lineage_cols = {row[1] for row in await cur.fetchall()}
            if "attention" not in lineage_cols:
                await db.execute(
                    "ALTER TABLE sect_lineage ADD COLUMN attention INTEGER NOT NULL DEFAULT 0"
                )

            # Seed the generic wallet and starter spatial pouch for existing characters.
            await db.execute(
                """INSERT INTO currency_wallets(user_id,currency_id,balance)
                   SELECT user_id,'low_spirit_stone',spirit_stones FROM characters
                   WHERE NOT EXISTS (
                       SELECT 1 FROM currency_wallets w
                       WHERE w.user_id=characters.user_id AND w.currency_id='low_spirit_stone'
                   )"""
            )
            await db.execute(
                """INSERT INTO storage_containers(user_id,container_id,name,grade,slot_capacity,living_space,updated_at)
                   SELECT user_id,'common_spatial_pouch','Common Spatial Pouch','Mortal',24,0,? FROM characters
                   WHERE NOT EXISTS (SELECT 1 FROM storage_containers s WHERE s.user_id=characters.user_id)""",
                (time.time(),),
            )
            # Existing characters receive a conservative, lossless aptitude
            # baseline. New characters use the full rolled profile supplied by
            # the rules engine. Family bloodlines remain dormant until trained.
            now = time.time()
            await db.execute(
                """INSERT INTO character_spiritual_roots(
                       user_id,grade,purity,elements_json,mutation,stability,refinement_progress,compatibility,updated_at
                   )
                   SELECT user_id,'Common',50,'["' || REPLACE(spiritual_root,'"','') || '"]','',100,0,50,?
                   FROM characters
                   WHERE NOT EXISTS (
                       SELECT 1 FROM character_spiritual_roots r WHERE r.user_id=characters.user_id
                   )""",
                (now,),
            )
            await db.execute(
                """INSERT INTO character_bloodlines(
                       user_id,bloodline_id,name,affinity,purity,state,evolution_stage,progress,rejection,mutation,
                       primary_lineage,source_family_id,unlocked_techniques_json,updated_at
                   )
                   SELECT cb.user_id,'legacy_family_bloodline',f.bloodline_name,f.bloodline_affinity,
                          MAX(5,f.bloodline_purity-10),'dormant',0,0,0,'',1,f.family_id,'[]',?
                   FROM character_birth_family cb JOIN birth_families f ON f.family_id=cb.family_id
                   WHERE f.bloodline_name<>'None' AND f.bloodline_purity>0
                     AND NOT EXISTS (SELECT 1 FROM character_bloodlines b WHERE b.user_id=cb.user_id)""",
                (now,),
            )
            await db.execute(
                """INSERT INTO character_physiques(
                       user_id,physique_id,name,state,evolution_stage,progress,stability,instability,updated_at
                   )
                   SELECT user_id,'ordinary_mortal_body','Ordinary Mortal Body','ordinary',0,0,100,0,?
                   FROM characters
                   WHERE NOT EXISTS (SELECT 1 FROM character_physiques p WHERE p.user_id=characters.user_id)""",
                (now,),
            )
            await db.commit()

        # The historical schema bootstrap above stays idempotent so databases
        # from pre-versioned releases can still upgrade in place. From schema
        # version 1 onward, every structural change must be registered in
        # SCHEMA_MIGRATIONS and is applied atomically here.
        await self._run_schema_migrations()

    async def _run_schema_migrations(self) -> None:
        async with self._connect() as db:
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    current_version INTEGER NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at REAL NOT NULL
                );
                """
            )
            await db.execute(
                "INSERT INTO schema_version(singleton,current_version,updated_at) VALUES(1,0,?) "
                "ON CONFLICT(singleton) DO NOTHING",
                (time.time(),),
            )
            await db.commit()

            cur = await db.execute("SELECT current_version FROM schema_version WHERE singleton=1")
            row = await cur.fetchone()
            current = int(row[0]) if row else 0
            if current > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema version {current} is newer than this bot supports ({SCHEMA_VERSION}); "
                    "refusing an unsafe downgrade"
                )

            for version, name, statements in SCHEMA_MIGRATIONS:
                if version <= current:
                    continue
                await db.execute("BEGIN IMMEDIATE")
                try:
                    for statement in statements:
                        await db.execute(statement)
                    now = time.time()
                    await db.execute(
                        "INSERT INTO schema_migrations(version,name,applied_at) VALUES(?,?,?)",
                        (int(version), str(name), now),
                    )
                    await db.execute(
                        "UPDATE schema_version SET current_version=?,updated_at=? WHERE singleton=1",
                        (int(version), now),
                    )
                    await db.commit()
                except Exception:
                    await db.rollback()
                    raise
                current = int(version)

            if current != SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema migration incomplete: current={current}, supported={SCHEMA_VERSION}"
                )

    async def get_schema_status(self) -> dict[str, Any]:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT current_version,updated_at FROM schema_version WHERE singleton=1"
            )
            row = await cur.fetchone()
            if not row:
                return {"current": 0, "supported": SCHEMA_VERSION, "compatible": False, "migrations": 0}
            cur = await db.execute("SELECT COUNT(*) FROM schema_migrations")
            migration_count = int((await cur.fetchone())[0])
            current = int(row[0])
            return {
                "current": current,
                "supported": SCHEMA_VERSION,
                "compatible": current == SCHEMA_VERSION,
                "migrations": migration_count,
                "updated_at": float(row[1]),
            }

    async def record_startup_event(
        self, boot_id: str, phase: str, *, status: str = "ready", detail: dict[str, Any] | None = None
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                "INSERT INTO startup_events(boot_id,phase,status,detail_json,created_at) VALUES(?,?,?,?,?)",
                (str(boot_id), str(phase), str(status), json.dumps(detail or {}, ensure_ascii=False), time.time()),
            )
            await db.commit()

    async def catalog_counts(self) -> dict[str, int]:
        tables = {
            "locations": "catalog_locations",
            "npcs": "catalog_npcs",
            "recipes": "catalog_recipes",
            "manuals": "catalog_manuals",
            "techniques": "catalog_techniques",
        }
        out: dict[str, int] = {}
        async with self._connect() as db:
            for key, table in tables.items():
                cur = await db.execute(f"SELECT COUNT(*) FROM {table}")
                out[key] = int((await cur.fetchone())[0])
        return out

    async def operational_health(self) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            async with self._connect() as db:
                cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                present_tables = {str(row[0]) for row in await cur.fetchall()}
                missing_tables = sorted(OPERATIONAL_REQUIRED_TABLES - present_tables)
                current = 0
                if "schema_version" in present_tables:
                    cur = await db.execute("SELECT current_version FROM schema_version WHERE singleton=1")
                    row = await cur.fetchone()
                    current = int(row[0]) if row else 0
                cur = await db.execute("PRAGMA journal_mode")
                journal_mode = str((await cur.fetchone())[0]).lower()
                await db.execute("SELECT 1")
            schema_intact = not missing_tables
            return {
                "ok": current == SCHEMA_VERSION and journal_mode == "wal" and schema_intact,
                "schema_intact": schema_intact,
                "missing_tables": missing_tables,
                "schema_version": current,
                "supported_schema_version": SCHEMA_VERSION,
                "journal_mode": journal_mode,
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
            }

    @staticmethod
    def _row_to_character(row: aiosqlite.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        data["attributes"] = json.loads(data.pop("attributes_json"))
        return data

    async def create_character(
        self,
        *,
        user_id: int,
        discord_name: str,
        name: str,
        origin: str,
        path: str,
        spiritual_root: str,
        concept: str,
        location: str,
        attributes: dict[str, int],
        qi_max: int,
        vitality_max: int,
        created_game_minute: int = 0,
        age_at_creation_years: int = 18,
        natural_lifespan_years: int = 75,
        gender: str = "neutral",
        birth_family_profile: dict[str, Any] | None = None,
        aptitude_profile: dict[str, Any] | None = None,
    ) -> bool:
        gender = str(gender or "neutral").strip().lower()
        if gender not in {"male", "female", "neutral"}:
            gender = "neutral"
        now = time.time()
        try:
            async with self._connect() as db:
                await db.execute("PRAGMA foreign_keys=ON;")
                await db.execute(
                    """
                    INSERT INTO characters (
                        user_id, discord_name, name, origin, path, spiritual_root, concept, gender,
                        age_at_creation_years, created_game_minute, natural_lifespan_years, life_extension_years, life_status,
                        realm_index, phase, cultivation, qi, qi_max, vitality, vitality_max,
                        spirit_stones, insight_xp, location, attributes_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'alive', 0, 1, 0, ?, ?, ?, ?, 25, 0, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        discord_name,
                        name,
                        origin,
                        path,
                        spiritual_root,
                        concept,
                        gender,
                        max(0, int(age_at_creation_years)),
                        max(0, int(created_game_minute)),
                        max(1, int(natural_lifespan_years)),
                        qi_max,
                        qi_max,
                        vitality_max,
                        vitality_max,
                        location,
                        json.dumps(attributes),
                        now,
                        now,
                    ),
                )
                # Small starter crafting kit so the systems are usable immediately.
                for item_id, qty in {"spirit_herb": 2, "spirit_iron": 1}.items():
                    await db.execute(
                        "INSERT INTO inventory(user_id, item_id, quantity) VALUES (?, ?, ?)",
                        (user_id, item_id, qty),
                    )
                await db.execute(
                    "INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(?,?,?)",
                    (user_id, "low_spirit_stone", 25),
                )
                await db.execute(
                    """INSERT INTO storage_containers(
                           user_id,container_id,name,grade,slot_capacity,living_space,updated_at
                       ) VALUES(?,?,?,?,?,?,?)""",
                    (user_id, "common_spatial_pouch", "Common Spatial Pouch", "Mortal", 24, 0, now),
                )
                await db.execute(
                    """INSERT OR IGNORE INTO character_location_discoveries(
                           user_id,location,discovery_kind,discovered_game_minute,created_at
                       ) VALUES(?,?,?,?,?)""",
                    (user_id, str(location), "birthplace", max(0, int(created_game_minute)), now),
                )
                await db.execute(
                    "INSERT INTO event_log(user_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
                    (user_id, "character_created", json.dumps({
                        "name": name, "path": path, "spiritual_root": spiritual_root, "gender": gender,
                        "origin": origin, "location": location,
                        "family_archetype": str((birth_family_profile or {}).get("id") or (birth_family_profile or {}).get("archetype") or ""),
                    }), now),
                )
                family_id: int | None = None
                if birth_family_profile:
                    fp = birth_family_profile
                    cur = await db.execute(
                        """INSERT INTO birth_families(
                               family_name,surname,archetype,tier,wealth,influence,stability,alignment_bias,location,
                               head_name,head_gender,head_title,head_realm_index,head_phase,treasury_balance,generation,
                               created_game_minute,last_simulated_game_minute,history_json,
                               clan_structure,bloodline_name,bloodline_affinity,bloodline_trait,bloodline_purity,branch_count,retainer_count,confederacy_name,
                               created_at,updated_at
                           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (str(fp['family_name']),str(fp['surname']),str(fp['id']),int(fp.get('tier',1)),int(fp.get('wealth',20)),
                         int(fp.get('influence',10)),int(fp.get('stability',60)),int(fp.get('alignment_bias',0)),str(fp.get('location',location)),
                         str(fp.get('head_name','Family Head')),str(fp.get('head_gender','neutral')),str(fp.get('head_title','Family Head')),
                         int(fp.get('head_realm_index',0)),int(fp.get('head_phase',1)),max(0,int(fp.get('wealth',20))*4),1,
                         max(0,int(created_game_minute)),max(0,int(created_game_minute)),json.dumps([f"{fp['family_name']} welcomed {name} into the household."]),
                         str(fp.get('clan_structure','extended_household')),str(fp.get('bloodline_name','None')),str(fp.get('bloodline_affinity','None')),
                         str(fp.get('bloodline_trait','No awakened ancestral bloodline')),max(0,min(100,int(fp.get('bloodline_purity',0)))),
                         max(1,int(fp.get('branch_count',1))),max(0,int(fp.get('retainer_count',0))),str(fp.get('confederacy_name','None')),now,now)
                    )
                    family_id=int(cur.lastrowid)
                    await db.execute(
                        "INSERT INTO character_birth_family(user_id,family_id,birth_order,generation,last_support_game_minute) VALUES(?,?,?,?,?)",
                        (user_id,family_id,max(1,int(fp.get('birth_order',1))),1,-999999999)
                    )
                    for rel in list(fp.get('relatives',[])):
                        age=max(1,int(rel.get('age',30)))
                        birth_min=max(0,int(created_game_minute)-age*518400)
                        rel_realm=int(rel.get('realm_index',0)); rel_phase=int(rel.get('phase',1))
                        rel_natural=70+secrets.randbelow(11)
                        rel_life=realm_lifespan_ceiling(rel_realm,rel_phase,rel_natural) or 2_000_000_000
                        await db.execute(
                            """INSERT INTO birth_family_npcs(family_id,name,relation,gender,age_at_creation,birth_game_minute,natural_lifespan_years,status,spiritual_root,realm_index,phase,personality,created_at)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (family_id,str(rel.get('name','Relative')),str(rel.get('relation','Relative')),str(rel.get('gender','neutral')),age,birth_min,int(rel_life),'alive','Mortal Root',rel_realm,rel_phase,'Family member',now)
                        )
                aptitude = dict(aptitude_profile or {})
                root = dict(aptitude.get("root") or {
                    "grade": "Common", "purity": 50, "elements": [spiritual_root],
                    "mutation": "", "stability": 100, "refinement_progress": 0, "compatibility": 50,
                })
                await db.execute(
                    """INSERT INTO character_spiritual_roots(
                           user_id,grade,purity,elements_json,mutation,stability,refinement_progress,compatibility,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        user_id, str(root.get("grade", "Common")), max(1, min(100, int(root.get("purity", 50)))),
                        json.dumps(list(root.get("elements") or [spiritual_root])), str(root.get("mutation", "")),
                        max(0, min(100, int(root.get("stability", 100)))),
                        max(0, min(100, int(root.get("refinement_progress", 0)))),
                        max(0, min(100, int(root.get("compatibility", 50)))), now,
                    ),
                )
                bloodline = aptitude.get("bloodline")
                if bloodline:
                    await db.execute(
                        """INSERT INTO character_bloodlines(
                               user_id,bloodline_id,name,affinity,purity,state,evolution_stage,progress,rejection,mutation,
                               primary_lineage,source_family_id,unlocked_techniques_json,updated_at
                           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            user_id, str(bloodline.get("bloodline_id", "legacy_family_bloodline")),
                            str(bloodline.get("name", "Ancestral Bloodline")), str(bloodline.get("affinity", "None")),
                            max(0, min(100, int(bloodline.get("purity", 0)))), str(bloodline.get("state", "dormant")),
                            max(0, int(bloodline.get("evolution_stage", 0))), max(0, min(100, int(bloodline.get("progress", 0)))),
                            max(0, min(100, int(bloodline.get("rejection", 0)))), str(bloodline.get("mutation", "")),
                            1 if bloodline.get("primary_lineage", 1) else 0, family_id,
                            json.dumps(list(bloodline.get("unlocked_techniques") or [])), now,
                        ),
                    )
                physique = dict(aptitude.get("physique") or {
                    "physique_id": "ordinary_mortal_body", "name": "Ordinary Mortal Body", "state": "ordinary",
                    "evolution_stage": 0, "progress": 0, "stability": 100, "instability": 0,
                })
                await db.execute(
                    """INSERT INTO character_physiques(
                           user_id,physique_id,name,state,evolution_stage,progress,stability,instability,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        user_id, str(physique.get("physique_id", "ordinary_mortal_body")),
                        str(physique.get("name", "Ordinary Mortal Body")), str(physique.get("state", "ordinary")),
                        max(0, int(physique.get("evolution_stage", 0))), max(0, min(100, int(physique.get("progress", 0)))),
                        max(0, min(100, int(physique.get("stability", 100)))),
                        max(0, min(100, int(physique.get("instability", 0)))), now,
                    ),
                )
                await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

    async def discover_location(
        self, user_id: int, location: str, *, game_minute: int = 0, discovery_kind: str = "exploration"
    ) -> bool:
        location = str(location).strip()
        if not location:
            return False
        now = time.time()
        async with self._connect() as db:
            cur = await db.execute(
                """INSERT OR IGNORE INTO character_location_discoveries(
                       user_id,location,discovery_kind,discovered_game_minute,created_at
                   ) VALUES(?,?,?,?,?)""",
                (int(user_id), location, str(discovery_kind)[:40], max(0, int(game_minute)), now),
            )
            await db.commit()
            return bool(cur.rowcount)

    async def get_discovered_locations(self, user_id: int) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT location,discovery_kind,discovered_game_minute,created_at
                   FROM character_location_discoveries WHERE user_id=?
                   ORDER BY discovered_game_minute,location""",
                (int(user_id),),
            )
            return [dict(row) for row in await cur.fetchall()]

    async def has_discovered_location(self, user_id: int, location: str) -> bool:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT 1 FROM character_location_discoveries WHERE user_id=? AND location=?",
                (int(user_id), str(location)),
            )
            return await cur.fetchone() is not None

    async def get_character(self, user_id: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM characters WHERE user_id = ?", (user_id,))
            row = await cur.fetchone()
            return self._row_to_character(row)

    async def list_character_user_ids(self) -> list[int]:
        """Return every canonical character owner for administrative reconciliation jobs."""
        async with self._connect() as db:
            cur = await db.execute("SELECT user_id FROM characters ORDER BY user_id")
            return [int(row[0]) for row in await cur.fetchall()]

    async def get_manuals(self, user_id: int) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM character_manuals WHERE user_id=? ORDER BY mastery DESC, manual_id",
                (int(user_id),),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def learn_manual(self, user_id: int, manual_id: str) -> dict[str, Any]:
        now = time.time()
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO character_manuals(user_id,manual_id,mastery,practice,learned_at,updated_at)
                   VALUES(?,?,0,0,?,?) ON CONFLICT(user_id,manual_id) DO NOTHING""",
                (int(user_id), str(manual_id), now, now),
            )
            await db.commit()
        rows = await self.get_manuals(user_id)
        return next(r for r in rows if r["manual_id"] == manual_id)

    async def practice_manual(self, user_id: int, manual_id: str, amount: int = 1) -> dict[str, Any] | None:
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT mastery,practice FROM character_manuals WHERE user_id=? AND manual_id=?",
                (int(user_id), str(manual_id)),
            )
            row = await cur.fetchone()
            if not row:
                await db.rollback()
                return None
            mastery, practice = int(row[0]), int(row[1]) + max(1, int(amount))
            thresholds = [3, 8, 16, 28]
            while mastery < 4 and practice >= thresholds[mastery]:
                mastery += 1
            await db.execute(
                "UPDATE character_manuals SET mastery=?,practice=?,updated_at=? WHERE user_id=? AND manual_id=?",
                (mastery, practice, now, int(user_id), str(manual_id)),
            )
            await db.commit()
            return {"user_id": int(user_id), "manual_id": str(manual_id), "mastery": mastery, "practice": practice}

    async def set_gender(self, user_id: int, gender: str) -> None:
        gender = gender if gender in {"male", "female", "neutral"} else "neutral"
        async with self._connect() as db:
            await db.execute(
                "UPDATE characters SET gender=?, updated_at=? WHERE user_id=?",
                (gender, time.time(), user_id),
            )
            await db.commit()

    async def set_concealment(self, user_id: int, active: bool) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE characters SET concealment_active=?, updated_at=? WHERE user_id=?",
                (1 if active else 0, time.time(), user_id),
            )
            await db.commit()

    async def add_spiritual_sense_bonus(
        self, user_id: int, *, power: int = 0, precision: int = 0, range_m: int = 0, concealment: int = 0
    ) -> None:
        """GM/reward hook for future manuals, inheritances, pills and treasures."""
        async with self._connect() as db:
            await db.execute(
                """
                UPDATE characters SET
                    sense_power_bonus=sense_power_bonus+?,
                    sense_precision_bonus=sense_precision_bonus+?,
                    sense_range_bonus=sense_range_bonus+?,
                    concealment_bonus=concealment_bonus+?,
                    updated_at=?
                WHERE user_id=?
                """,
                (power, precision, range_m, concealment, time.time(), user_id),
            )
            await db.commit()

    async def reward_body_cultivation(
        self,
        user_id: int,
        amount: int,
        *,
        cultivation_cap: int | None = None,
        event_type: str = "body_cultivate",
    ) -> int:
        """Award body essence without allowing it to overflow the current stage cap.

        Returning the actual awarded amount keeps Discord output accurate when a
        character is already close to (or exactly at) the stage requirement.
        """
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            awarded = max(0, int(amount))
            if cultivation_cap is not None:
                cur = await db.execute(
                    "SELECT body_cultivation FROM characters WHERE user_id=?", (user_id,)
                )
                row = await cur.fetchone()
                current = int(row[0]) if row else 0
                awarded = min(awarded, max(0, int(cultivation_cap) - current))
            await db.execute(
                "UPDATE characters SET body_cultivation=body_cultivation+?, updated_at=? WHERE user_id=?",
                (awarded, now, user_id),
            )
            await db.execute(
                "INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                (user_id, event_type, json.dumps({"body_cultivation": awarded}), now),
            )
            await db.commit()
            return awarded

    async def get_inventory(self, user_id: int) -> dict[str, int]:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT item_id, quantity FROM inventory WHERE user_id = ? AND quantity > 0 ORDER BY item_id",
                (user_id,),
            )
            rows = await cur.fetchall()
            return {str(item_id): int(qty) for item_id, qty in rows}

    async def add_items(self, user_id: int, rewards: dict[str, int]) -> None:
        if not rewards:
            return
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            for item_id, qty in rewards.items():
                if qty <= 0:
                    continue
                await db.execute(
                    """
                    INSERT INTO inventory(user_id, item_id, quantity)
                    VALUES (?, ?, ?)
                    ON CONFLICT(user_id, item_id)
                    DO UPDATE SET quantity = quantity + excluded.quantity
                    """,
                    (user_id, item_id, qty),
                )
            await db.execute(
                "INSERT INTO event_log(user_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (user_id, "items_added", json.dumps(rewards), now),
            )
            await db.commit()

    async def reward(
        self,
        user_id: int,
        *,
        cultivation: int = 0,
        cultivation_cap: int | None = None,
        spirit_stones: int = 0,
        insight_xp: int = 0,
        items: dict[str, int] | None = None,
        event_type: str = "reward",
    ) -> int:
        """Apply rewards atomically and return the cultivation actually awarded.

        `cultivation_cap` is normally the requirement for the character's current
        stage. Capping here prevents players from banking huge amounts of essence
        at Stage 9 (especially while working on a long Perfect Path) and then
        skipping several later stages with carried overflow.
        """
        items = items or {}
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cultivation_awarded = max(0, int(cultivation))
            if cultivation_cap is not None:
                cur = await db.execute(
                    "SELECT cultivation FROM characters WHERE user_id=?", (user_id,)
                )
                row = await cur.fetchone()
                current = int(row[0]) if row else 0
                cultivation_awarded = min(
                    cultivation_awarded,
                    max(0, int(cultivation_cap) - current),
                )
            await db.execute(
                """
                UPDATE characters
                SET cultivation = cultivation + ?,
                    spirit_stones = spirit_stones + ?,
                    insight_xp = insight_xp + ?,
                    updated_at = ?
                WHERE user_id = ?
                """,
                (cultivation_awarded, spirit_stones, insight_xp, now, user_id),
            )
            if spirit_stones:
                await db.execute(
                    """INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(?,?,?)
                       ON CONFLICT(user_id,currency_id) DO UPDATE SET balance=balance+excluded.balance""",
                    (user_id, "low_spirit_stone", int(spirit_stones)),
                )
            for item_id, qty in items.items():
                await db.execute(
                    """
                    INSERT INTO inventory(user_id, item_id, quantity)
                    VALUES (?, ?, ?)
                    ON CONFLICT(user_id, item_id)
                    DO UPDATE SET quantity = quantity + excluded.quantity
                    """,
                    (user_id, item_id, qty),
                )
            payload = {
                "cultivation": cultivation_awarded,
                "spirit_stones": spirit_stones,
                "insight_xp": insight_xp,
                "items": items,
            }
            await db.execute(
                "INSERT INTO event_log(user_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (user_id, event_type, json.dumps(payload), now),
            )
            await db.commit()
            return cultivation_awarded

    async def consume_and_create(
        self,
        user_id: int,
        *,
        costs: dict[str, int],
        outputs: dict[str, int],
        event_type: str,
        success: bool,
    ) -> tuple[bool, dict[str, int]]:
        """Consume recipe materials atomically. Outputs are added only on success."""
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT item_id, quantity FROM inventory WHERE user_id = ?",
                (user_id,),
            )
            owned = {str(item): int(qty) for item, qty in await cur.fetchall()}
            missing = {
                item: qty - owned.get(item, 0)
                for item, qty in costs.items()
                if owned.get(item, 0) < qty
            }
            if missing:
                await db.rollback()
                return False, missing

            for item_id, qty in costs.items():
                await db.execute(
                    "UPDATE inventory SET quantity = quantity - ? WHERE user_id = ? AND item_id = ?",
                    (qty, user_id, item_id),
                )
            if success:
                for item_id, qty in outputs.items():
                    await db.execute(
                        """
                        INSERT INTO inventory(user_id, item_id, quantity)
                        VALUES (?, ?, ?)
                        ON CONFLICT(user_id, item_id)
                        DO UPDATE SET quantity = quantity + excluded.quantity
                        """,
                        (user_id, item_id, qty),
                    )

            await db.execute(
                "INSERT INTO event_log(user_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (
                    user_id,
                    event_type,
                    json.dumps({"costs": costs, "outputs": outputs if success else {}, "success": success}),
                    now,
                ),
            )
            await db.commit()
            return True, {}

    async def set_location(self, user_id: int, location: str) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE characters SET location = ?, updated_at = ? WHERE user_id = ?",
                (location, time.time(), user_id),
            )
            await db.commit()

    async def set_cooldown(self, user_id: int, action: str, seconds: int) -> None:
        available_at = time.time() + seconds
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO cooldowns(user_id, action, available_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, action)
                DO UPDATE SET available_at = excluded.available_at
                """,
                (user_id, action, available_at),
            )
            await db.commit()

    async def cooldown_remaining(self, user_id: int, action: str) -> int:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT available_at FROM cooldowns WHERE user_id = ? AND action = ?",
                (user_id, action),
            )
            row = await cur.fetchone()
            if not row:
                return 0
            return max(0, int(row[0] - time.time()))

    async def apply_breakthrough(
        self,
        user_id: int,
        *,
        new_realm_index: int,
        new_phase: int,
        cultivation_cost: int,
        success: bool,
        failure_loss: int,
        roll_payload: dict[str, Any],
    ) -> None:
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            if success:
                await db.execute(
                    """
                    UPDATE characters
                    SET realm_index = ?, phase = ?, cultivation = cultivation - ?, updated_at = ?
                    WHERE user_id = ?
                    """,
                    (new_realm_index, new_phase, cultivation_cost, now, user_id),
                )
            else:
                await db.execute(
                    """
                    UPDATE characters
                    SET cultivation = MAX(0, cultivation - ?), updated_at = ?
                    WHERE user_id = ?
                    """,
                    (failure_loss, now, user_id),
                )
            await db.execute(
                "INSERT INTO event_log(user_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (user_id, "breakthrough_attempt", json.dumps(roll_payload), now),
            )
            await db.commit()

    async def apply_body_breakthrough(
        self,
        user_id: int,
        *,
        new_realm_index: int,
        new_phase: int,
        cultivation_cost: int,
        success: bool,
        failure_loss: int,
        vitality_gain: int,
        roll_payload: dict[str, Any],
    ) -> None:
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            if success:
                await db.execute(
                    """
                    UPDATE characters
                    SET body_realm_index=?, body_phase=?, body_cultivation=body_cultivation-?,
                        vitality_max=vitality_max+?, vitality=vitality_max+?, updated_at=?
                    WHERE user_id=?
                    """,
                    (new_realm_index, new_phase, cultivation_cost, vitality_gain, vitality_gain, now, user_id),
                )
            else:
                await db.execute(
                    "UPDATE characters SET body_cultivation=MAX(0, body_cultivation-?), updated_at=? WHERE user_id=?",
                    (failure_loss, now, user_id),
                )
            await db.execute(
                "INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                (user_id, "body_breakthrough_attempt", json.dumps(roll_payload), now),
            )
            await db.commit()

    async def add_history(
        self,
        channel_id: int,
        *,
        user_id: int | None,
        speaker: str,
        content: str,
        keep: int = 60,
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                "INSERT INTO scene_history(channel_id, user_id, speaker, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (channel_id, user_id, speaker, content[:4000], time.time()),
            )
            # Keep the DB small: only retain recent RP context per channel.
            await db.execute(
                """
                DELETE FROM scene_history
                WHERE channel_id = ? AND id NOT IN (
                    SELECT id FROM scene_history WHERE channel_id = ? ORDER BY id DESC LIMIT ?
                )
                """,
                (channel_id, channel_id, keep),
            )
            await db.commit()

    async def get_history(self, channel_id: int, limit: int = 12) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """
                SELECT speaker, content, user_id
                FROM scene_history
                WHERE channel_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (channel_id, limit),
            )
            rows = [dict(row) for row in await cur.fetchall()]
            rows.reverse()
            return rows

    async def get_npc_memory(self, user_id: int, npc_name: str) -> str:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT memory FROM npc_memory WHERE user_id = ? AND npc_name = ?",
                (user_id, npc_name),
            )
            row = await cur.fetchone()
            return str(row[0]) if row else "No established personal history yet."

    async def set_npc_memory(self, user_id: int, npc_name: str, memory: str) -> None:
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO npc_memory(user_id, npc_name, memory, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, npc_name)
                DO UPDATE SET memory = excluded.memory, updated_at = excluded.updated_at
                """,
                (user_id, npc_name, memory[:2000], time.time()),
            )
            await db.commit()

    async def get_npc_mind_state(self, npc_name: str) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM npc_mind_state WHERE npc_name=?", (str(npc_name),)
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def upsert_npc_mind_state(
        self, npc_name: str, *, current_goal: str, mood: str = "calm",
        focus_target: str = "", recent_event: str = "", goal_progress: int = 0,
        game_minute: int = 0,
    ) -> dict[str, Any]:
        now = time.time()
        progress = max(0, min(100, int(goal_progress)))
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO npc_mind_state(
                       npc_name,current_goal,mood,focus_target,recent_event,goal_progress,last_game_minute,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(npc_name) DO UPDATE SET
                       current_goal=excluded.current_goal,mood=excluded.mood,focus_target=excluded.focus_target,
                       recent_event=excluded.recent_event,goal_progress=excluded.goal_progress,
                       last_game_minute=excluded.last_game_minute,updated_at=excluded.updated_at""",
                (str(npc_name), str(current_goal)[:500], str(mood)[:80], str(focus_target)[:160],
                 str(recent_event)[:600], progress, int(game_minute), now),
            )
            await db.commit()
        return {
            "npc_name": str(npc_name), "current_goal": str(current_goal)[:500], "mood": str(mood)[:80],
            "focus_target": str(focus_target)[:160], "recent_event": str(recent_event)[:600],
            "goal_progress": progress, "last_game_minute": int(game_minute), "updated_at": now,
        }

    async def add_npc_player_memory(
        self, user_id: int, npc_name: str, *, memory_kind: str, summary: str,
        salience: int = 25, source: str = "talk", game_minute: int = 0,
    ) -> int:
        now = time.time()
        safe_salience = max(0, min(100, int(salience)))
        async with self._connect() as db:
            cur = await db.execute(
                """INSERT INTO npc_player_memories(
                       user_id,npc_name,memory_kind,summary,salience,source,game_minute,created_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (int(user_id), str(npc_name), str(memory_kind)[:60], str(summary)[:900],
                 safe_salience, str(source)[:60], int(game_minute), now),
            )
            memory_id = int(cur.lastrowid)
            # Keep a bounded long-term memory bank per player/NPC pair.  Low-salience
            # old exchanges are discarded first; important vows and major conflicts survive.
            await db.execute(
                """DELETE FROM npc_player_memories
                   WHERE user_id=? AND npc_name=? AND memory_id NOT IN (
                       SELECT memory_id FROM npc_player_memories
                       WHERE user_id=? AND npc_name=?
                       ORDER BY salience DESC, game_minute DESC, memory_id DESC LIMIT 48
                   )""",
                (int(user_id), str(npc_name), int(user_id), str(npc_name)),
            )
            await db.commit()
        return memory_id

    async def list_npc_player_memories(
        self, user_id: int, npc_name: str, limit: int = 6, *, mark_recalled: bool = False,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(24, int(limit)))
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT * FROM npc_player_memories
                   WHERE user_id=? AND npc_name=?
                   ORDER BY salience DESC, game_minute DESC, memory_id DESC LIMIT ?""",
                (int(user_id), str(npc_name), safe_limit),
            )
            rows = [dict(r) for r in await cur.fetchall()]
            if rows and mark_recalled:
                ids = [int(r["memory_id"]) for r in rows]
                placeholders = ",".join("?" for _ in ids)
                await db.execute(
                    f"UPDATE npc_player_memories SET recalled_count=recalled_count+1,last_recalled_at=? WHERE memory_id IN ({placeholders})",
                    (time.time(), *ids),
                )
                await db.commit()
            return rows

    # ---------- v0.7 core scene / relationship / quest state ----------
    async def get_player_scene_state(self, user_id: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM player_scene_state WHERE user_id=?", (int(user_id),)
            )
            row = await cur.fetchone()
            if not row:
                return None
            data = dict(row)
            try:
                data["metadata"] = json.loads(str(data.pop("metadata_json", "{}")))
            except Exception:
                data["metadata"] = {}
            return data

    async def set_player_scene_state(
        self, user_id: int, *, physical_location: str, scene_type: str, scene_key: str,
        scene_label: str, channel_id: int | None = None, metadata: dict[str, Any] | None = None,
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO player_scene_state(
                       user_id,physical_location,scene_type,scene_key,scene_label,channel_id,metadata_json,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       physical_location=excluded.physical_location,scene_type=excluded.scene_type,
                       scene_key=excluded.scene_key,scene_label=excluded.scene_label,channel_id=excluded.channel_id,
                       metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                (int(user_id), str(physical_location), str(scene_type), str(scene_key), str(scene_label),
                 int(channel_id) if channel_id is not None else None,
                 json.dumps(metadata or {}, separators=(",", ":")), time.time()),
            )
            await db.commit()

    async def get_npc_relationship(self, user_id: int, npc_name: str) -> dict[str, Any]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM npc_relationships WHERE user_id=? AND npc_name=?",
                (int(user_id), str(npc_name)),
            )
            row = await cur.fetchone()
            if row:
                return dict(row)
        return {
            "user_id": int(user_id), "npc_name": str(npc_name), "trust": 0, "respect": 0,
            "fear": 0, "affection": 0, "debt": 0, "grudge": 0, "encounter_count": 0,
            "last_summary": "", "updated_at": 0.0,
        }

    async def update_npc_relationship(
        self, user_id: int, npc_name: str, *, trust: int = 0, respect: int = 0, fear: int = 0,
        affection: int = 0, debt: int = 0, grudge: int = 0, summary: str = "",
    ) -> dict[str, Any]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT * FROM npc_relationships WHERE user_id=? AND npc_name=?",
                (int(user_id), str(npc_name)),
            )
            row = await cur.fetchone()
            current = dict(row) if row else {
                "trust": 0, "respect": 0, "fear": 0, "affection": 0,
                "debt": 0, "grudge": 0, "encounter_count": 0,
            }
            values = {}
            for key, delta in {
                "trust": trust, "respect": respect, "fear": fear,
                "affection": affection, "debt": debt, "grudge": grudge,
            }.items():
                values[key] = max(-100, min(100, int(current.get(key, 0)) + int(delta)))
            encounter_count = int(current.get("encounter_count", 0)) + 1
            last_summary = str(summary)[:800]
            updated_at = time.time()
            await db.execute(
                """INSERT INTO npc_relationships(
                       user_id,npc_name,trust,respect,fear,affection,debt,grudge,encounter_count,last_summary,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(user_id,npc_name) DO UPDATE SET
                       trust=excluded.trust,respect=excluded.respect,fear=excluded.fear,affection=excluded.affection,
                       debt=excluded.debt,grudge=excluded.grudge,encounter_count=excluded.encounter_count,
                       last_summary=excluded.last_summary,updated_at=excluded.updated_at""",
                (int(user_id), str(npc_name), values["trust"], values["respect"], values["fear"],
                 values["affection"], values["debt"], values["grudge"], encounter_count,
                 last_summary, updated_at),
            )
            await db.commit()
        return {
            "user_id": int(user_id), "npc_name": str(npc_name), **values,
            "encounter_count": encounter_count, "last_summary": last_summary,
            "updated_at": updated_at,
        }

    async def list_npc_relationships(self, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT * FROM npc_relationships WHERE user_id=?
                   ORDER BY encounter_count DESC, updated_at DESC LIMIT ?""",
                (int(user_id), max(1, min(100, int(limit)))),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def accept_quest(self, user_id: int, quest_key: str, *, game_minute: int = 0) -> dict[str, Any]:
        now = time.time()
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO character_quests(
                       user_id,quest_key,status,progress_json,accepted_game_minute,created_at,updated_at
                   ) VALUES(?,?,'active','{}',?,?,?)
                   ON CONFLICT(user_id,quest_key) DO NOTHING""",
                (int(user_id), str(quest_key), int(game_minute), now, now),
            )
            await db.commit()
        rows = await self.list_character_quests(int(user_id))
        return next(r for r in rows if str(r["quest_key"]) == str(quest_key))

    async def list_character_quests(self, user_id: int, status: str | None = None) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            if status is None:
                cur = await db.execute(
                    "SELECT * FROM character_quests WHERE user_id=? ORDER BY updated_at DESC", (int(user_id),)
                )
            else:
                cur = await db.execute(
                    "SELECT * FROM character_quests WHERE user_id=? AND status=? ORDER BY updated_at DESC",
                    (int(user_id), str(status)),
                )
            result = []
            for row in await cur.fetchall():
                data = dict(row)
                try:
                    data["progress"] = json.loads(str(data.pop("progress_json", "{}")))
                except Exception:
                    data["progress"] = {}
                result.append(data)
            return result

    async def update_quest_progress(
        self, user_id: int, quest_key: str, progress: dict[str, Any], *, complete: bool = False, game_minute: int = 0,
    ) -> dict[str, Any]:
        async with self._connect() as db:
            await db.execute(
                """UPDATE character_quests SET progress_json=?,status=?,completed_game_minute=?,updated_at=?
                   WHERE user_id=? AND quest_key=?""",
                (json.dumps(progress, separators=(",", ":")), "completed" if complete else "active",
                 int(game_minute) if complete else None, time.time(), int(user_id), str(quest_key)),
            )
            await db.commit()
        rows = await self.list_character_quests(int(user_id))
        return next(r for r in rows if str(r["quest_key"]) == str(quest_key))

    # ---------- Realm Perfection ----------
    async def get_perfection(self, user_id: int, realm_index: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM realm_perfection WHERE user_id = ? AND realm_index = ?",
                (user_id, realm_index),
            )
            row = await cur.fetchone()
            if not row:
                return None
            data = dict(row)
            data["active"] = bool(data["active"])
            data["completed"] = bool(data["completed"])
            data["discovered"] = json.loads(data.pop("discovered_json"))
            return data

    async def start_perfection(self, user_id: int, realm_index: int) -> bool:
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT completed FROM realm_perfection WHERE user_id = ? AND realm_index = ?",
                (user_id, realm_index),
            )
            row = await cur.fetchone()
            if row and int(row[0]) == 1:
                await db.rollback()
                return False
            await db.execute(
                """
                INSERT INTO realm_perfection(
                    user_id, realm_index, active, completed, progress, training_progress,
                    quest_index, quest_preparation, completed_quests, discovered_json, updated_at
                ) VALUES (?, ?, 1, 0, 0, 0, 0, 0, 0, '[]', ?)
                ON CONFLICT(user_id, realm_index) DO UPDATE SET active = 1, updated_at = excluded.updated_at
                """,
                (user_id, realm_index, now),
            )
            await db.commit()
            return True

    async def add_perfection_training(self, user_id: int, realm_index: int, amount: int, cap: int) -> int:
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT training_progress, progress, active FROM realm_perfection WHERE user_id=? AND realm_index=?",
                (user_id, realm_index),
            )
            row = await cur.fetchone()
            if not row or not int(row[2]):
                await db.rollback()
                return 0
            old_training = int(row[0])
            new_training = min(cap, old_training + max(0, amount))
            delta = new_training - old_training
            await db.execute(
                "UPDATE realm_perfection SET training_progress=?, progress=MIN(100, progress+?), updated_at=? WHERE user_id=? AND realm_index=?",
                (new_training, delta, now, user_id, realm_index),
            )
            await db.commit()
            return delta

    async def add_perfection_preparation(self, user_id: int, realm_index: int, amount: int = 1) -> int:
        now = time.time()
        async with self._connect() as db:
            await db.execute(
                "UPDATE realm_perfection SET quest_preparation=quest_preparation+?, updated_at=? WHERE user_id=? AND realm_index=? AND active=1",
                (max(0, amount), now, user_id, realm_index),
            )
            await db.commit()
            cur = await db.execute(
                "SELECT quest_preparation FROM realm_perfection WHERE user_id=? AND realm_index=?",
                (user_id, realm_index),
            )
            row = await cur.fetchone()
            return int(row[0]) if row else 0

    async def complete_perfection_quest(
        self, user_id: int, realm_index: int, *, progress_reward: int, clue: str
    ) -> None:
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT discovered_json FROM realm_perfection WHERE user_id=? AND realm_index=?",
                (user_id, realm_index),
            )
            row = await cur.fetchone()
            discovered = json.loads(row[0]) if row else []
            if clue and clue not in discovered:
                discovered.append(clue)
            await db.execute(
                """
                UPDATE realm_perfection
                SET progress=MIN(100, progress+?), quest_index=quest_index+1,
                    completed_quests=completed_quests+1, quest_preparation=0,
                    discovered_json=?, updated_at=?
                WHERE user_id=? AND realm_index=? AND active=1
                """,
                (progress_reward, json.dumps(discovered), now, user_id, realm_index),
            )
            await db.commit()

    async def reduce_perfection_progress(self, user_id: int, realm_index: int, amount: int) -> int:
        """Remove recoverable Perfection progress after a failed final trial.

        Quest progress is permanent once earned, so the loss comes out of the
        meditation/training portion. Reducing `training_progress` as well as the
        displayed total is essential; otherwise a character at the training cap
        could be stuck below 100% forever with no legal way to regain the loss.
        """
        requested = max(0, int(amount))
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT training_progress FROM realm_perfection WHERE user_id=? AND realm_index=?",
                (user_id, realm_index),
            )
            row = await cur.fetchone()
            if not row:
                await db.rollback()
                return 0
            loss = min(requested, max(0, int(row[0])))
            await db.execute(
                """UPDATE realm_perfection
                   SET progress=MAX(0, progress-?),
                       training_progress=MAX(0, training_progress-?),
                       updated_at=?
                   WHERE user_id=? AND realm_index=?""",
                (loss, loss, time.time(), user_id, realm_index),
            )
            await db.commit()
            return loss

    async def complete_perfection(self, user_id: int, realm_index: int) -> None:
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                "UPDATE realm_perfection SET progress=100, active=0, completed=1, updated_at=? WHERE user_id=? AND realm_index=?",
                (now, user_id, realm_index),
            )
            await db.execute(
                """UPDATE characters
                   SET qi_max = qi_max + MAX(1, CAST(qi_max * 0.10 AS INTEGER)),
                       qi = qi_max + MAX(1, CAST(qi_max * 0.10 AS INTEGER)),
                       vitality_max = vitality_max + MAX(1, CAST(vitality_max * 0.05 AS INTEGER)),
                       vitality = vitality_max + MAX(1, CAST(vitality_max * 0.05 AS INTEGER)),
                       updated_at=?
                   WHERE user_id=?""",
                (now, user_id),
            )
            await db.commit()

    async def abandon_perfection(self, user_id: int, realm_index: int) -> None:
        async with self._connect() as db:
            await db.execute(
                "DELETE FROM realm_perfection WHERE user_id=? AND realm_index=? AND completed=0",
                (user_id, realm_index),
            )
            await db.commit()

    async def has_completed_perfection(self, user_id: int) -> bool:
        """Return True once the character has perfected at least one Qi realm.

        This makes the +2 Perfect-foundation breakthrough benefit genuinely permanent
        instead of disappearing immediately after entering the next realm.
        """
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT 1 FROM realm_perfection WHERE user_id=? AND completed=1 LIMIT 1",
                (user_id,),
            )
            return await cur.fetchone() is not None

    # ---------- Body Realm Perfection ----------
    async def get_body_perfection(self, user_id: int, realm_index: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM body_realm_perfection WHERE user_id=? AND realm_index=?",
                (user_id, realm_index),
            )
            row = await cur.fetchone()
            if not row:
                return None
            data = dict(row)
            data["active"] = bool(data["active"])
            data["completed"] = bool(data["completed"])
            data["discovered"] = json.loads(data.pop("discovered_json"))
            return data

    async def start_body_perfection(self, user_id: int, realm_index: int) -> bool:
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT completed FROM body_realm_perfection WHERE user_id=? AND realm_index=?",
                (user_id, realm_index),
            )
            row = await cur.fetchone()
            if row and int(row[0]) == 1:
                await db.rollback()
                return False
            await db.execute(
                """
                INSERT INTO body_realm_perfection(
                    user_id,realm_index,active,completed,progress,training_progress,quest_index,
                    quest_preparation,completed_quests,discovered_json,updated_at
                ) VALUES(?,?,1,0,0,0,0,0,0,'[]',?)
                ON CONFLICT(user_id,realm_index) DO UPDATE SET active=1, updated_at=excluded.updated_at
                """,
                (user_id, realm_index, now),
            )
            await db.commit()
            return True

    async def add_body_perfection_training(self, user_id: int, realm_index: int, amount: int, cap: int) -> int:
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT training_progress,progress,active FROM body_realm_perfection WHERE user_id=? AND realm_index=?",
                (user_id, realm_index),
            )
            row = await cur.fetchone()
            if not row or not int(row[2]):
                await db.rollback()
                return 0
            old = int(row[0])
            new = min(cap, old + max(0, amount))
            delta = new - old
            await db.execute(
                "UPDATE body_realm_perfection SET training_progress=?, progress=MIN(100,progress+?), updated_at=? WHERE user_id=? AND realm_index=?",
                (new, delta, now, user_id, realm_index),
            )
            await db.commit()
            return delta

    async def add_body_perfection_preparation(self, user_id: int, realm_index: int, amount: int = 1) -> int:
        now = time.time()
        async with self._connect() as db:
            await db.execute(
                "UPDATE body_realm_perfection SET quest_preparation=quest_preparation+?, updated_at=? WHERE user_id=? AND realm_index=? AND active=1",
                (max(0, amount), now, user_id, realm_index),
            )
            await db.commit()
            cur = await db.execute(
                "SELECT quest_preparation FROM body_realm_perfection WHERE user_id=? AND realm_index=?",
                (user_id, realm_index),
            )
            row = await cur.fetchone()
            return int(row[0]) if row else 0

    async def complete_body_perfection_quest(
        self, user_id: int, realm_index: int, *, progress_reward: int, clue: str
    ) -> None:
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT discovered_json FROM body_realm_perfection WHERE user_id=? AND realm_index=?",
                (user_id, realm_index),
            )
            row = await cur.fetchone()
            discovered = json.loads(row[0]) if row else []
            if clue and clue not in discovered:
                discovered.append(clue)
            await db.execute(
                """
                UPDATE body_realm_perfection
                SET progress=MIN(100,progress+?), quest_index=quest_index+1,
                    completed_quests=completed_quests+1, quest_preparation=0,
                    discovered_json=?, updated_at=?
                WHERE user_id=? AND realm_index=? AND active=1
                """,
                (progress_reward, json.dumps(discovered), now, user_id, realm_index),
            )
            await db.commit()

    async def reduce_body_perfection_progress(self, user_id: int, realm_index: int, amount: int) -> int:
        requested = max(0, int(amount))
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT training_progress FROM body_realm_perfection WHERE user_id=? AND realm_index=?",
                (user_id, realm_index),
            )
            row = await cur.fetchone()
            if not row:
                await db.rollback()
                return 0
            loss = min(requested, max(0, int(row[0])))
            await db.execute(
                """UPDATE body_realm_perfection
                   SET progress=MAX(0,progress-?),
                       training_progress=MAX(0,training_progress-?),
                       updated_at=?
                   WHERE user_id=? AND realm_index=?""",
                (loss, loss, time.time(), user_id, realm_index),
            )
            await db.commit()
            return loss

    async def complete_body_perfection(self, user_id: int, realm_index: int) -> None:
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                "UPDATE body_realm_perfection SET progress=100,active=0,completed=1,updated_at=? WHERE user_id=? AND realm_index=?",
                (now, user_id, realm_index),
            )
            await db.execute(
                """UPDATE characters
                   SET vitality_max=vitality_max+MAX(1,CAST(vitality_max*0.10 AS INTEGER)),
                       vitality=vitality_max+MAX(1,CAST(vitality_max*0.10 AS INTEGER)),
                       qi_max=qi_max+MAX(1,CAST(qi_max*0.05 AS INTEGER)),
                       qi=qi_max+MAX(1,CAST(qi_max*0.05 AS INTEGER)),
                       updated_at=? WHERE user_id=?""",
                (now, user_id),
            )
            await db.commit()

    async def abandon_body_perfection(self, user_id: int, realm_index: int) -> None:
        async with self._connect() as db:
            await db.execute(
                "DELETE FROM body_realm_perfection WHERE user_id=? AND realm_index=? AND completed=0",
                (user_id, realm_index),
            )
            await db.commit()

    async def has_completed_body_perfection(self, user_id: int) -> bool:
        """Return True once the character has perfected at least one Body realm."""
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT 1 FROM body_realm_perfection WHERE user_id=? AND completed=1 LIMIT 1",
                (user_id,),
            )
            return await cur.fetchone() is not None

    # ---------- Shared world events ----------
    async def activate_world_event(
        self, *, event_key: str, event_type: str, title: str, location: str, payload: dict[str, Any], ends_at: float,
        dedupe_key: str = "",
    ) -> bool:
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute("UPDATE world_events SET active=0 WHERE active=1 AND ends_at<=?", (now,))
            key = str(dedupe_key).strip()
            if key:
                cur = await db.execute(
                    "SELECT 1 FROM world_events WHERE dedupe_key=? AND location=? AND active=1 AND ends_at>? LIMIT 1",
                    (key, str(location), now),
                )
                if await cur.fetchone():
                    await db.rollback()
                    return False
            try:
                await db.execute(
                    """
                    INSERT INTO world_events(event_key,dedupe_key,event_type,title,location,payload_json,active,starts_at,ends_at)
                    VALUES(?,?,?,?,?,?,1,?,?)
                    ON CONFLICT(event_key) DO UPDATE SET active=1,dedupe_key=excluded.dedupe_key,
                        payload_json=excluded.payload_json,ends_at=excluded.ends_at
                    """,
                    (event_key, key, event_type, title, location, json.dumps(payload), now, ends_at),
                )
            except (aiosqlite.IntegrityError, sqlite3.IntegrityError):
                await db.rollback()
                return False
            await db.commit()
            return True

    async def get_active_world_events(self, location: str | None = None) -> list[dict[str, Any]]:
        now = time.time()
        async with self._connect() as db:
            await db.execute("UPDATE world_events SET active=0 WHERE active=1 AND ends_at<=?", (now,))
            await db.commit()
            db.row_factory = aiosqlite.Row
            if location:
                cur = await db.execute(
                    "SELECT * FROM world_events WHERE active=1 AND ends_at>? AND location=? ORDER BY ends_at",
                    (now, location),
                )
            else:
                cur = await db.execute(
                    "SELECT * FROM world_events WHERE active=1 AND ends_at>? ORDER BY ends_at",
                    (now,),
                )
            rows=[]
            for row in await cur.fetchall():
                d=dict(row); d['payload']=json.loads(d.pop('payload_json')); rows.append(d)
            return rows


    async def get_world_event(self, event_key: str) -> dict[str, Any] | None:
        """Return one active-or-closed world event with its decoded payload."""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM world_events WHERE event_key=? LIMIT 1", (str(event_key),))
            row = await cur.fetchone()
        if not row:
            return None
        out = dict(row)
        try:
            out["payload"] = json.loads(out.pop("payload_json") or "{}")
        except Exception:
            out["payload"] = {}
        return out

    async def record_world_event_action(
        self, *, event_key: str, user_id: int, action_key: str, stance: str = "", target: str = "",
        attribute: str = "", total: int = 0, tn: int = 0, success: bool = False,
        contribution_delta: int = 0, investigation_delta: int = 0, support_delta: int = 0,
        interference_delta: int = 0, combat_victory: bool = False, detail: str = "", game_minute: int = 0,
    ) -> dict[str, Any]:
        """Persist one player response to a live event and its aggregate participation state."""
        now = time.time()
        safe_action = str(action_key or "observe")[:80]
        safe_stance = str(stance or safe_action)[:80]
        success_i = 1 if success else 0
        failure_i = 0 if success else 1
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """INSERT INTO world_event_participation(
                       event_key,user_id,stance,contribution,investigation,support,interference,combat_victories,
                       actions_taken,successes,failures,last_action,last_target,first_game_minute,last_game_minute,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,1,?,?, ?,?,?,?,?)
                   ON CONFLICT(event_key,user_id) DO UPDATE SET
                       stance=excluded.stance,
                       contribution=world_event_participation.contribution+excluded.contribution,
                       investigation=world_event_participation.investigation+excluded.investigation,
                       support=world_event_participation.support+excluded.support,
                       interference=world_event_participation.interference+excluded.interference,
                       combat_victories=world_event_participation.combat_victories+excluded.combat_victories,
                       actions_taken=world_event_participation.actions_taken+1,
                       successes=world_event_participation.successes+excluded.successes,
                       failures=world_event_participation.failures+excluded.failures,
                       last_action=excluded.last_action,last_target=excluded.last_target,last_game_minute=excluded.last_game_minute,updated_at=excluded.updated_at""",
                (str(event_key), int(user_id), safe_stance, int(contribution_delta), int(investigation_delta),
                 int(support_delta), int(interference_delta), 1 if combat_victory else 0, success_i, failure_i,
                 safe_action, str(target)[:180], int(game_minute), int(game_minute), now),
            )
            await db.execute(
                """INSERT INTO world_event_actions(
                       event_key,user_id,action_key,stance,target,attribute,total,tn,success,contribution_delta,detail,game_minute,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (str(event_key), int(user_id), safe_action, safe_stance, str(target)[:180], str(attribute)[:60],
                 int(total), int(tn), success_i, int(contribution_delta), str(detail)[:1000], int(game_minute), now),
            )
            cur = await db.execute(
                "SELECT * FROM world_event_participation WHERE event_key=? AND user_id=?",
                (str(event_key), int(user_id)),
            )
            row = await cur.fetchone()
            await db.commit()
        return dict(row) if row else {}

    async def get_world_event_participation(self, event_key: str, user_id: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM world_event_participation WHERE event_key=? AND user_id=?",
                (str(event_key), int(user_id)),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def list_world_event_participants(self, event_key: str, *, limit: int = 25) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT p.*,c.name AS character_name,c.location,c.realm_index,c.phase
                   FROM world_event_participation p JOIN characters c ON c.user_id=p.user_id
                   WHERE p.event_key=? ORDER BY p.contribution DESC,p.actions_taken DESC,p.updated_at ASC LIMIT ?""",
                (str(event_key), max(1, min(100, int(limit)))),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def get_world_event_actions(self, event_key: str, *, limit: int = 20) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT a.*,c.name AS character_name FROM world_event_actions a
                   JOIN characters c ON c.user_id=a.user_id WHERE a.event_key=?
                   ORDER BY a.action_id DESC LIMIT ?""",
                (str(event_key), max(1, min(100, int(limit)))),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def finalize_world_event_history(self, event_key: str, *, game_minute: int = 0) -> dict[str, Any] | None:
        """Write an idempotent public history entry summarizing mechanical player response to an event."""
        event = await self.get_world_event(str(event_key))
        if not event:
            return None
        participants = await self.list_world_event_participants(str(event_key), limit=100)
        if not participants:
            return None
        contribution = sum(int(x.get("contribution") or 0) for x in participants)
        support = sum(int(x.get("support") or 0) for x in participants)
        interference = sum(int(x.get("interference") or 0) for x in participants)
        victories = sum(int(x.get("combat_victories") or 0) for x in participants)
        actions = sum(int(x.get("actions_taken") or 0) for x in participants)
        if contribution >= 12 or support >= 8:
            outcome = "Cultivator intervention strongly improved the region's response."
        elif interference >= max(6, support + 3):
            outcome = "Cultivator interference significantly worsened the crisis."
        elif contribution > 0:
            outcome = "Cultivators made a measurable but limited difference."
        else:
            outcome = "Cultivators observed or contested the event without decisively changing its course."
        title = str(event.get("title") or "World Event")
        names = [str(x.get("character_name") or "Cultivator") for x in participants[:5]]
        summary = (
            f"{title} ended at {event.get('location') or 'an unknown location'}. "
            f"{len(participants)} cultivator(s) took {actions} recorded action(s); "
            f"support={support}, interference={interference}, combat victories={victories}. {outcome}"
        )
        return await self.record_world_history_event(
            event_type="world_event_response", title=f"Aftermath: {title}", summary=summary,
            significance=max(35, min(90, 40 + abs(contribution) + victories * 4)), visibility="public",
            location=str(event.get("location") or ""), actor_type="group", actor_name=", ".join(names)[:160],
            target_type="world_event", target_key=str(event_key), target_name=title,
            tags=("world event","player response","aftermath",str(event.get("event_type") or "event")),
            game_minute=int(game_minute),
            metadata={"participants": len(participants), "actions": actions, "contribution": contribution,
                      "support": support, "interference": interference, "combat_victories": victories},
            source_key=f"world_event_aftermath:{str(event_key)}",
        )

    async def get_npc_life_state(self, npc_name: str) -> dict[str, Any] | None:
        """Small compatibility API for Discord event/player surfaces that need public NPC life state."""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT c.npc_name,c.current_location,c.realm_index,c.phase,c.faction,c.profession,c.activity,c.status,
                          l.health,l.injury,l.injury_severity,l.sect_rank,l.relationship_status,l.spouse_name,l.children_count
                   FROM npc_civilization_state c LEFT JOIN npc_life_state l ON l.npc_name=c.npc_name
                   WHERE c.npc_name=? LIMIT 1""",
                (str(npc_name),),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_server_config(self, guild_id: int) -> dict[str, Any]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM server_config WHERE guild_id=?", (guild_id,))
            row = await cur.fetchone()
            return dict(row) if row else {}

    async def set_server_channels(
        self, guild_id: int, *, announcement_channel_id: int, event_scene_channel_id: int,
        home_scene_channel_id: int | None = None, log_channel_id: int | None = None,
        begin_channel_id: int | None = None, info_channel_id: int | None = None,
        exploration_channel_id: int | None = None,
    ) -> None:
        now = time.time()
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO server_config(
                       guild_id,announcement_channel_id,event_scene_channel_id,home_scene_channel_id,
                       log_channel_id,begin_channel_id,info_channel_id,exploration_channel_id,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(guild_id) DO UPDATE SET
                       announcement_channel_id=excluded.announcement_channel_id,
                       event_scene_channel_id=excluded.event_scene_channel_id,
                       home_scene_channel_id=excluded.home_scene_channel_id,
                       log_channel_id=COALESCE(excluded.log_channel_id,server_config.log_channel_id),
                       begin_channel_id=COALESCE(excluded.begin_channel_id,server_config.begin_channel_id),
                       info_channel_id=COALESCE(excluded.info_channel_id,server_config.info_channel_id),
                       exploration_channel_id=COALESCE(excluded.exploration_channel_id,server_config.exploration_channel_id),
                       updated_at=excluded.updated_at""",
                (
                    guild_id, announcement_channel_id, event_scene_channel_id, home_scene_channel_id,
                    log_channel_id, begin_channel_id, info_channel_id, exploration_channel_id, now,
                ),
            )
            await db.commit()

    async def set_info_message_id(self, guild_id: int, message_id: int | None) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE server_config SET info_message_id=?,updated_at=? WHERE guild_id=?",
                (int(message_id) if message_id else None, time.time(), int(guild_id)),
            )
            await db.commit()

    async def get_expedition_thread(self, guild_id: int, user_id: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM expedition_threads WHERE guild_id=? AND user_id=?",
                (int(guild_id), int(user_id)),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_expedition_thread_by_thread(self, thread_id: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM expedition_threads WHERE thread_id=?", (int(thread_id),))
            row = await cur.fetchone()
            return dict(row) if row else None

    async def set_expedition_thread(
        self, guild_id: int, user_id: int, *, thread_id: int, parent_channel_id: int, last_location: str
    ) -> None:
        now = time.time()
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO expedition_threads(guild_id,user_id,thread_id,parent_channel_id,last_location,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(guild_id,user_id) DO UPDATE SET
                       thread_id=excluded.thread_id,parent_channel_id=excluded.parent_channel_id,
                       last_location=excluded.last_location,updated_at=excluded.updated_at""",
                (int(guild_id), int(user_id), int(thread_id), int(parent_channel_id), str(last_location), now, now),
            )
            await db.commit()

    async def update_expedition_location(self, guild_id: int, user_id: int, location: str) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE expedition_threads SET last_location=?,updated_at=? WHERE guild_id=? AND user_id=?",
                (str(location), time.time(), int(guild_id), int(user_id)),
            )
            await db.commit()

    async def ensure_sect_abode(
        self, user_id: int, *, sect_name: str, name: str, base_location: str
    ) -> dict[str, Any]:
        now = time.time(); key = f"sect_abode:{int(user_id)}"
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                """INSERT INTO sect_abodes(user_id,sect_name,name,location_key,base_location,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET sect_name=excluded.sect_name,name=excluded.name,
                       base_location=excluded.base_location,updated_at=excluded.updated_at""",
                (int(user_id), str(sect_name), str(name)[:100], key, str(base_location), now, now),
            )
            await db.commit()
            cur = await db.execute("SELECT * FROM sect_abodes WHERE user_id=?", (int(user_id),))
            row = await cur.fetchone()
            return dict(row)

    async def get_sect_abode(self, user_id: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM sect_abodes WHERE user_id=?", (int(user_id),))
            row = await cur.fetchone(); return dict(row) if row else None

    async def get_sect_abode_by_location(self, location_key: str) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM sect_abodes WHERE location_key=?", (str(location_key),))
            row = await cur.fetchone(); return dict(row) if row else None

    async def get_sect_abode_by_thread(self, thread_id: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM sect_abodes WHERE thread_id=?", (int(thread_id),))
            row = await cur.fetchone(); return dict(row) if row else None

    async def set_sect_abode_thread(self, user_id: int, *, thread_id: int, thread_channel_id: int) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE sect_abodes SET thread_id=?,thread_channel_id=?,updated_at=? WHERE user_id=?",
                (int(thread_id), int(thread_channel_id), time.time(), int(user_id)),
            )
            await db.commit()

    async def register_event_thread(
        self, *, thread_id: int, event_key: str | None, event_type: str, title: str,
        channel_id: int, message_id: int, announcement_channel_id: int | None,
        announcement_message_id: int | None, triggered_by: int | None, expires_at: float
    ) -> None:
        now = time.time()
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO event_threads(
                       thread_id,event_key,event_type,title,channel_id,message_id,announcement_channel_id,
                       announcement_message_id,triggered_by,starts_at,expires_at,active
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,1)
                   ON CONFLICT(thread_id) DO UPDATE SET event_key=excluded.event_key,event_type=excluded.event_type,
                       title=excluded.title,channel_id=excluded.channel_id,message_id=excluded.message_id,
                       announcement_channel_id=excluded.announcement_channel_id,
                       announcement_message_id=excluded.announcement_message_id,
                       triggered_by=excluded.triggered_by,expires_at=excluded.expires_at,active=1""",
                (thread_id,event_key,event_type,title,channel_id,message_id,announcement_channel_id,
                 announcement_message_id,triggered_by,now,expires_at),
            )
            if event_key:
                await db.execute(
                    "UPDATE world_events SET thread_id=?, announcement_channel_id=?, announcement_message_id=? WHERE event_key=?",
                    (thread_id, announcement_channel_id, announcement_message_id, event_key),
                )
            await db.commit()

    async def get_expired_event_threads(self) -> list[dict[str, Any]]:
        now = time.time()
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM event_threads WHERE active=1 AND expires_at<=? ORDER BY expires_at", (now,)
            )
            return [dict(row) for row in await cur.fetchall()]

    async def get_event_thread_by_key(self, event_key: str) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM event_threads WHERE event_key=? AND active=1 ORDER BY starts_at DESC LIMIT 1",
                (event_key,),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def close_event_thread(self, thread_id: int, event_key: str | None = None) -> None:
        async with self._connect() as db:
            await db.execute("UPDATE event_threads SET active=0 WHERE thread_id=?", (thread_id,))
            if event_key:
                await db.execute("UPDATE world_events SET active=0 WHERE event_key=?", (event_key,))
                await db.execute("UPDATE secret_realm_runs SET active=0 WHERE event_key=?", (event_key,))
            await db.commit()

    async def is_active_event_thread(self, thread_id: int) -> bool:
        now = time.time()
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT 1 FROM event_threads WHERE thread_id=? AND active=1 AND expires_at>?",
                (thread_id, now),
            )
            return await cur.fetchone() is not None

    async def claim_world_event(self, user_id: int, event_key: str) -> bool:
        try:
            async with self._connect() as db:
                await db.execute(
                    "INSERT INTO event_claims(user_id,event_key,claimed_at) VALUES(?,?,?)",
                    (user_id,event_key,time.time()),
                )
                await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

    # ---------- Secret realms and inheritances ----------
    async def start_secret_realm_run(
        self, user_id: int, *, realm_id: str, event_key: str, expires_at: float
    ) -> bool:
        now=time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur=await db.execute("SELECT active, expires_at FROM secret_realm_runs WHERE user_id=?",(user_id,))
            row=await cur.fetchone()
            if row and int(row[0]) == 1 and float(row[1]) > now:
                await db.rollback(); return False
            await db.execute(
                """INSERT INTO secret_realm_runs(user_id,realm_id,event_key,room_index,danger,active,entered_at,expires_at)
                   VALUES(?,?,?,0,0,1,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET realm_id=excluded.realm_id,event_key=excluded.event_key,room_index=0,danger=0,active=1,entered_at=excluded.entered_at,expires_at=excluded.expires_at""",
                (user_id,realm_id,event_key,now,expires_at),
            )
            await db.commit(); return True

    async def get_secret_realm_run(self, user_id: int) -> dict[str, Any] | None:
        now=time.time()
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row
            cur=await db.execute("SELECT * FROM secret_realm_runs WHERE user_id=?",(user_id,))
            row=await cur.fetchone()
            if not row: return None
            d=dict(row)
            if d['active'] and d['expires_at'] <= now:
                await db.execute("UPDATE secret_realm_runs SET active=0 WHERE user_id=?",(user_id,)); await db.commit(); d['active']=0
            d['active']=bool(d['active']); return d

    async def advance_secret_realm_run(self, user_id: int, *, danger_delta: int = 0) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE secret_realm_runs SET room_index=room_index+1,danger=MAX(0,danger+?) WHERE user_id=? AND active=1",
                (danger_delta,user_id),
            )
            await db.commit()

    async def set_secret_realm_danger(self, user_id: int, danger: int) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE secret_realm_runs SET danger=? WHERE user_id=? AND active=1",
                (max(0, danger), user_id),
            )
            await db.commit()

    async def leave_secret_realm(self, user_id: int) -> None:
        async with self._connect() as db:
            await db.execute("UPDATE secret_realm_runs SET active=0 WHERE user_id=?",(user_id,))
            await db.commit()

    async def grant_inheritance(
        self, user_id: int, *, inheritance_id: str, source_realm_id: str, bonuses: dict[str, int], item_id: str
    ) -> bool:
        now=time.time()
        try:
            async with self._connect() as db:
                await db.execute("BEGIN IMMEDIATE")
                await db.execute(
                    "INSERT INTO inheritances(user_id,inheritance_id,source_realm_id,acquired_at) VALUES(?,?,?,?)",
                    (user_id,inheritance_id,source_realm_id,now),
                )
                await db.execute(
                    """UPDATE characters SET qi_max=qi_max+?, qi=qi+?, vitality_max=vitality_max+?, vitality=vitality+?,
                       insight_xp=insight_xp+?, updated_at=? WHERE user_id=?""",
                    (int(bonuses.get('qi_max',0)),int(bonuses.get('qi_max',0)),int(bonuses.get('vitality_max',0)),int(bonuses.get('vitality_max',0)),int(bonuses.get('insight_xp',0)),now,user_id),
                )
                if item_id:
                    await db.execute(
                        "INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,1) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+1",
                        (user_id,item_id),
                    )
                await db.execute("UPDATE secret_realm_runs SET active=0 WHERE user_id=?",(user_id,))
                await db.commit()
            char = await self.get_character(user_id) or {}
            clock = await self.get_world_clock()
            await self.record_world_history_event(
                event_type="inheritance",
                title=f"Inheritance obtained: {str(inheritance_id).replace('_',' ').title()}",
                summary=(
                    f"{char.get('name','A cultivator')} obtained the inheritance {inheritance_id} "
                    f"from secret realm {source_realm_id}. The legacy permanently altered that cultivator's path."
                ),
                significance=82, visibility="participant", location=str(char.get("location") or ""),
                actor_type="player", actor_key=str(int(user_id)), actor_name=str(char.get("name") or ""),
                target_type="inheritance", target_key=str(inheritance_id), target_name=str(inheritance_id).replace('_',' ').title(),
                related_user_id=int(user_id), tags=("inheritance","secret realm","legacy","discovery"),
                game_minute=int(clock.get("game_minute",0)),
                metadata={"source_realm_id": source_realm_id, "bonuses": bonuses, "item_id": item_id},
                source_key=f"inheritance:{int(user_id)}:{inheritance_id}",
            )
            return True
        except aiosqlite.IntegrityError:
            return False

    async def get_inheritances(self, user_id: int) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row
            cur=await db.execute(
                "SELECT inheritance_id,source_realm_id,acquired_at FROM inheritances WHERE user_id=? ORDER BY acquired_at",
                (user_id,),
            )
            return [dict(r) for r in await cur.fetchall()]


    # ---------- Sect recruitment, discovery, and NPC recommendations ----------
    async def discover_sect(
        self, user_id: int, sect_name: str, *, game_minute: int = 0,
        discovery_kind: str = "rumor", source_key: str = ""
    ) -> bool:
        sect_name = str(sect_name).strip()
        if not sect_name:
            return False
        now = time.time()
        async with self._connect() as db:
            cur = await db.execute(
                """INSERT OR IGNORE INTO character_sect_discoveries(
                       user_id,sect_name,discovery_kind,source_key,discovered_game_minute,created_at
                   ) VALUES(?,?,?,?,?,?)""",
                (int(user_id), sect_name, str(discovery_kind)[:40], str(source_key)[:160], max(0, int(game_minute)), now),
            )
            await db.commit()
            return bool(cur.rowcount)

    async def get_discovered_sects(self, user_id: int) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT sect_name,discovery_kind,source_key,discovered_game_minute,created_at
                   FROM character_sect_discoveries WHERE user_id=?
                   ORDER BY discovered_game_minute,sect_name""", (int(user_id),)
            )
            return [dict(row) for row in await cur.fetchall()]

    async def has_discovered_sect(self, user_id: int, sect_name: str) -> bool:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT 1 FROM character_sect_discoveries WHERE user_id=? AND sect_name=?",
                (int(user_id), str(sect_name)),
            )
            return await cur.fetchone() is not None

    async def issue_sect_recommendation(
        self, user_id: int, *, npc_name: str, sect_name: str, bonus: int = 2, game_minute: int = 0
    ) -> dict[str, Any]:
        now = time.time()
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                "UPDATE sect_recommendations SET status='superseded',updated_at=? WHERE user_id=? AND sect_name=? AND status='active'",
                (now, int(user_id), str(sect_name)),
            )
            cur = await db.execute(
                """INSERT INTO sect_recommendations(
                       user_id,npc_name,sect_name,bonus,status,issued_game_minute,created_at,updated_at
                   ) VALUES(?,?,?,?, 'active',?,?,?)""",
                (int(user_id), str(npc_name), str(sect_name), max(0, int(bonus)), max(0, int(game_minute)), now, now),
            )
            rec_id = int(cur.lastrowid)
            await db.commit()
            cur = await db.execute("SELECT * FROM sect_recommendations WHERE recommendation_id=?", (rec_id,))
            return dict(await cur.fetchone())

    async def get_active_sect_recommendations(self, user_id: int) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM sect_recommendations WHERE user_id=? AND status='active' ORDER BY recommendation_id DESC",
                (int(user_id),),
            )
            return [dict(row) for row in await cur.fetchall()]

    async def get_active_sect_recommendation(self, user_id: int, sect_name: str) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT * FROM sect_recommendations
                   WHERE user_id=? AND sect_name=? AND status='active' ORDER BY recommendation_id DESC LIMIT 1""",
                (int(user_id), str(sect_name)),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def consume_sect_recommendation(self, recommendation_id: int, *, game_minute: int = 0) -> None:
        async with self._connect() as db:
            await db.execute(
                """UPDATE sect_recommendations SET status='used',used_game_minute=?,updated_at=?
                   WHERE recommendation_id=? AND status='active'""",
                (max(0, int(game_minute)), time.time(), int(recommendation_id)),
            )
            await db.commit()

    async def record_sect_recruitment_attempt(
        self, user_id: int, *, sect_name: str, attempt_type: str, result: str,
        score: int = 0, target: int = 0, npc_name: str = "", location: str = "",
        recommendation_bonus: int = 0, details: dict[str, Any] | None = None, game_minute: int = 0
    ) -> int:
        now = time.time()
        async with self._connect() as db:
            cur = await db.execute(
                """INSERT INTO sect_recruitment_attempts(
                       user_id,sect_name,attempt_type,npc_name,location,result,score,target,recommendation_bonus,details_json,game_minute,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (int(user_id), str(sect_name), str(attempt_type), str(npc_name), str(location), str(result),
                 int(score), int(target), int(recommendation_bonus), json.dumps(details or {}), max(0, int(game_minute)), now),
            )
            await db.commit()
            return int(cur.lastrowid)

    async def get_recent_sect_recruitment_attempts(self, user_id: int, *, limit: int = 10) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT * FROM sect_recruitment_attempts WHERE user_id=?
                   ORDER BY attempt_id DESC LIMIT ?""", (int(user_id), max(1, min(50, int(limit))))
            )
            rows = []
            for row in await cur.fetchall():
                item = dict(row)
                try: item["details"] = json.loads(item.pop("details_json", "{}") or "{}")
                except json.JSONDecodeError: item["details"] = {}
                rows.append(item)
            return rows

    async def get_latest_sect_recruitment_attempt(
        self, user_id: int, sect_name: str, attempt_type: str
    ) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT * FROM sect_recruitment_attempts
                   WHERE user_id=? AND sect_name=? AND attempt_type=?
                   ORDER BY attempt_id DESC LIMIT 1""",
                (int(user_id), str(sect_name), str(attempt_type)),
            )
            row = await cur.fetchone()
            if not row: return None
            item = dict(row)
            try: item["details"] = json.loads(item.pop("details_json", "{}") or "{}")
            except json.JSONDecodeError: item["details"] = {}
            return item


    # ---------- Sects, lineage, and forms of address ----------
    async def set_address_style(self, user_id: int, style: str) -> None:
        style = style.lower().strip()
        if style not in {"masculine", "feminine", "neutral"}:
            raise ValueError("address style must be masculine, feminine, or neutral")
        async with self._connect() as db:
            await db.execute(
                "UPDATE characters SET address_style=?, updated_at=? WHERE user_id=?",
                (style, time.time(), user_id),
            )
            await db.commit()

    async def set_sect_membership(
        self, user_id: int, *, sect_name: str, rank_name: str, rank_level: int = 0
    ) -> None:
        now = time.time()
        async with self._connect() as db:
            await db.execute("PRAGMA foreign_keys=ON;")
            await db.execute(
                "INSERT INTO sects(sect_name,updated_at) VALUES(?,?) ON CONFLICT(sect_name) DO NOTHING",
                (sect_name.strip(), now),
            )
            await db.execute(
                """
                INSERT INTO sect_membership(user_id,sect_name,rank_name,rank_level,joined_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET
                    sect_name=excluded.sect_name,
                    rank_name=excluded.rank_name,
                    rank_level=excluded.rank_level
                """,
                (user_id, sect_name.strip(), rank_name.strip(), int(rank_level), now),
            )
            # Public membership resolves any outstanding recruitment sponsorships;
            # stale recommendation tokens must never survive canonical admission.
            await db.execute(
                "UPDATE sect_recommendations SET status='resolved',updated_at=? WHERE user_id=? AND status='active'",
                (now, int(user_id)),
            )
            await db.commit()

    async def clear_sect_membership(self, user_id: int) -> None:
        async with self._connect() as db:
            await db.execute("DELETE FROM sect_lineage WHERE disciple_user_id=? OR master_user_id=?", (user_id, user_id))
            await db.execute("DELETE FROM dao_partnerships WHERE user_a=? OR user_b=?", (user_id, user_id))
            await db.execute("DELETE FROM sect_membership WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM sect_abodes WHERE user_id=?", (user_id,))
            await db.commit()

    async def get_sect_membership(self, user_id: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM sect_membership WHERE user_id=?", (user_id,))
            row = await cur.fetchone()
            return dict(row) if row else None

    async def set_master(self, disciple_user_id: int, master_user_id: int) -> None:
        if disciple_user_id == master_user_id:
            raise ValueError("A cultivator cannot be their own master.")
        now = time.time()
        async with self._connect() as db:
            await db.execute("PRAGMA foreign_keys=ON;")
            # Both must exist and should belong to the same sect when memberships are present.
            cur = await db.execute(
                "SELECT user_id FROM characters WHERE user_id IN (?,?)", (disciple_user_id, master_user_id)
            )
            if len(await cur.fetchall()) != 2:
                raise ValueError("Both master and disciple must have characters.")
            cur = await db.execute(
                "SELECT user_id, sect_name FROM sect_membership WHERE user_id IN (?,?)",
                (disciple_user_id, master_user_id),
            )
            memberships = {int(r[0]): str(r[1]) for r in await cur.fetchall()}
            if len(memberships) == 2 and memberships[disciple_user_id] != memberships[master_user_id]:
                raise ValueError("Master and disciple must belong to the same sect.")

            # Prevent direct or indirect lineage cycles.
            cursor = master_user_id
            seen = {disciple_user_id}
            for _ in range(64):
                if cursor in seen:
                    raise ValueError("That master assignment would create a lineage cycle.")
                seen.add(cursor)
                cur = await db.execute(
                    "SELECT master_user_id FROM sect_lineage WHERE disciple_user_id=?", (cursor,)
                )
                row = await cur.fetchone()
                if not row:
                    break
                cursor = int(row[0])

            await db.execute(
                """
                INSERT INTO sect_lineage(disciple_user_id,master_user_id,accepted_at)
                VALUES(?,?,?)
                ON CONFLICT(disciple_user_id) DO UPDATE SET
                    master_user_id=excluded.master_user_id,
                    accepted_at=excluded.accepted_at
                """,
                (disciple_user_id, master_user_id, now),
            )
            await db.commit()

    async def clear_master(self, disciple_user_id: int) -> None:
        async with self._connect() as db:
            await db.execute("DELETE FROM sect_lineage WHERE disciple_user_id=?", (disciple_user_id,))
            await db.commit()

    async def _lineage_person(self, db: aiosqlite.Connection, user_id: int) -> dict[str, Any] | None:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT c.*, sl.accepted_at AS lineage_accepted_at, sl.attention AS master_attention,
                   sm.sect_name, sm.rank_name, sm.rank_level, sm.contribution_points, sm.influence, sm.joined_at
            FROM characters c
            LEFT JOIN sect_lineage sl ON sl.disciple_user_id=c.user_id
            LEFT JOIN sect_membership sm ON sm.user_id=c.user_id
            WHERE c.user_id=?
            """,
            (user_id,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["attributes"] = json.loads(d.pop("attributes_json"))
        return d

    async def get_master(self, user_id: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT master_user_id FROM sect_lineage WHERE disciple_user_id=?", (user_id,)
            )
            row = await cur.fetchone()
            if not row:
                return None
            return await self._lineage_person(db, int(row[0]))

    async def get_lineage_snapshot(self, user_id: int) -> dict[str, Any]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            person = await self._lineage_person(db, user_id)
            if not person:
                return {}
            membership = None
            if person.get("sect_name"):
                membership = {
                    "sect_name": person["sect_name"],
                    "rank_name": person["rank_name"],
                    "rank_level": person["rank_level"],
                    "joined_at": person["joined_at"],
                }

            cur = await db.execute(
                "SELECT master_user_id, accepted_at FROM sect_lineage WHERE disciple_user_id=?", (user_id,)
            )
            own_line = await cur.fetchone()
            master = await self._lineage_person(db, int(own_line[0])) if own_line else None
            if own_line:
                person["lineage_accepted_at"] = float(own_line[1])

            grandmaster = None
            master_line = None
            if master:
                cur = await db.execute(
                    "SELECT master_user_id, accepted_at FROM sect_lineage WHERE disciple_user_id=?",
                    (master["user_id"],),
                )
                master_line = await cur.fetchone()
                if master_line:
                    master["lineage_accepted_at"] = float(master_line[1])
                    grandmaster = await self._lineage_person(db, int(master_line[0]))

            siblings: list[dict[str, Any]] = []
            if master:
                cur = await db.execute(
                    """
                    SELECT c.user_id,c.name,c.address_style,c.realm_index,c.phase,sl.accepted_at
                    FROM sect_lineage sl JOIN characters c ON c.user_id=sl.disciple_user_id
                    WHERE sl.master_user_id=? AND c.user_id<>?
                    ORDER BY sl.accepted_at
                    """,
                    (master["user_id"], user_id),
                )
                siblings = [dict(r) for r in await cur.fetchall()]

            master_siblings: list[dict[str, Any]] = []
            if grandmaster and master:
                cur = await db.execute(
                    """
                    SELECT c.user_id,c.name,c.address_style,c.realm_index,c.phase,sl.accepted_at
                    FROM sect_lineage sl JOIN characters c ON c.user_id=sl.disciple_user_id
                    WHERE sl.master_user_id=? AND c.user_id<>?
                    ORDER BY sl.accepted_at
                    """,
                    (grandmaster["user_id"], master["user_id"]),
                )
                master_siblings = [dict(r) for r in await cur.fetchall()]

            cur = await db.execute(
                """
                SELECT c.user_id,c.name,c.address_style,c.realm_index,c.phase,sl.accepted_at
                FROM sect_lineage sl JOIN characters c ON c.user_id=sl.disciple_user_id
                WHERE sl.master_user_id=? ORDER BY sl.accepted_at
                """,
                (user_id,),
            )
            disciples = [dict(r) for r in await cur.fetchall()]

            return {
                "person": person,
                "membership": membership,
                "master": master,
                "grandmaster": grandmaster,
                "siblings": siblings,
                "master_siblings": master_siblings,
                "disciples": disciples,
            }

    async def get_address_context(self, observer_user_id: int, target_user_id: int) -> dict[str, Any] | None:
        from ..sect import resolve_address

        observer_snapshot = await self.get_lineage_snapshot(observer_user_id)
        target_snapshot = await self.get_lineage_snapshot(target_user_id)
        if not observer_snapshot or not target_snapshot:
            return None
        observer = observer_snapshot["person"]
        target = target_snapshot["person"]
        result = resolve_address(
            observer,
            target,
            observer_membership=observer_snapshot.get("membership"),
            target_membership=target_snapshot.get("membership"),
            observer_master=observer_snapshot.get("master"),
            target_master=target_snapshot.get("master"),
            observer_grandmaster=observer_snapshot.get("grandmaster"),
            observer_master_master=observer_snapshot.get("grandmaster"),
            sibling_rows=observer_snapshot.get("siblings", []),
            master_sibling_rows=observer_snapshot.get("master_siblings", []),
        )
        return {
            "title": result.title,
            "pinyin": result.pinyin,
            "hanzi": result.hanzi,
            "translation": result.translation,
            "reason": result.reason,
            "display": result.display,
            "display_chinese": result.display_chinese,
        }

    async def describe_lineage_context(self, user_id: int) -> str:
        snap = await self.get_lineage_snapshot(user_id)
        if not snap:
            return "No canonical sect lineage recorded."
        p = snap["person"]
        lines = [f"Character: {p['name']}"]
        membership = snap.get("membership")
        if membership:
            lines.append(
                f"Sect: {membership['sect_name']} | Rank: {membership['rank_name']} (level {membership['rank_level']})"
            )
        else:
            lines.append("Sect: none recorded")
        if snap.get("master"):
            lines.append(f"Master: {snap['master']['name']}")
        if snap.get("grandmaster"):
            lines.append(f"Grandmaster: {snap['grandmaster']['name']}")
        if snap.get("siblings"):
            ordered = ", ".join(r["name"] for r in snap["siblings"][:12])
            lines.append(f"Same-master sect siblings: {ordered}")
        if snap.get("master_siblings"):
            ordered = ", ".join(r["name"] for r in snap["master_siblings"][:12])
            lines.append(f"Master's martial siblings: {ordered}")
        if snap.get("disciples"):
            ordered = ", ".join(r["name"] for r in snap["disciples"][:12])
            lines.append(f"Direct disciples: {ordered}")
        return "\n".join(lines)

    async def consume_item(self, user_id: int, item_id: str, quantity: int = 1) -> bool:
        quantity = max(1, int(quantity))
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT quantity FROM inventory WHERE user_id=? AND item_id=?", (user_id, item_id)
            )
            row = await cur.fetchone()
            if not row or int(row[0]) < quantity:
                await db.rollback()
                return False
            await db.execute(
                "UPDATE inventory SET quantity=quantity-? WHERE user_id=? AND item_id=?",
                (quantity, user_id, item_id),
            )
            await db.commit()
            return True

    async def spend_resources(self, user_id: int, *, qi: int = 0, vitality: int = 0) -> dict[str, int] | None:
        qi_cost=max(0,int(qi)); vit_cost=max(0,int(vitality))
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur=await db.execute("SELECT qi,qi_max,vitality,vitality_max FROM characters WHERE user_id=?",(int(user_id),))
            row=await cur.fetchone()
            if not row or int(row[0])<qi_cost or int(row[2])<=vit_cost:
                await db.rollback(); return None
            new_qi=int(row[0])-qi_cost; new_vit=int(row[2])-vit_cost; now=time.time()
            await db.execute("UPDATE characters SET qi=?,vitality=?,updated_at=? WHERE user_id=?",(new_qi,new_vit,now,int(user_id)))
            if vit_cost:
                await db.execute("UPDATE battles SET player_hp=MIN(player_hp,?),version=version+1,updated_at=? WHERE user_id=? AND status='active'",(new_vit,now,int(user_id)))
            await db.commit()
            return {"qi":new_qi,"qi_max":int(row[1]),"vitality":new_vit,"vitality_max":int(row[3])}

    async def restore_resources(self, user_id: int, *, qi: int = 0, vitality: int = 0) -> dict[str, int]:
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """UPDATE characters SET
                       qi=MIN(qi_max,qi+?), vitality=MIN(vitality_max,vitality+?), updated_at=?
                   WHERE user_id=?""",
                (max(0, int(qi)), max(0, int(vitality)), time.time(), user_id),
            )
            cur = await db.execute("SELECT qi,qi_max,vitality,vitality_max FROM characters WHERE user_id=?", (user_id,))
            row = await cur.fetchone()
            if row and int(vitality) > 0:
                await db.execute(
                    """UPDATE battles SET player_hp=?,player_hp_max=MAX(player_hp_max,?),
                           version=version+1,updated_at=?
                       WHERE user_id=? AND status='active'""",
                    (int(row[2]), int(row[3]), time.time(), int(user_id)),
                )
            await db.commit()
            return {"qi":int(row[0]),"qi_max":int(row[1]),"vitality":int(row[2]),"vitality_max":int(row[3])} if row else {}

    async def apply_vitality_damage(self, user_id: int, damage: int) -> dict[str, int]:
        amount=max(0,int(damage))
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute("UPDATE characters SET vitality=MAX(0,vitality-?),updated_at=? WHERE user_id=?",(amount,time.time(),user_id))
            cur=await db.execute("SELECT vitality,vitality_max FROM characters WHERE user_id=?",(user_id,)); row=await cur.fetchone(); await db.commit()
            return {"vitality":int(row[0]),"vitality_max":int(row[1])} if row else {}

    async def apply_battle_damage(self, battle_id: int, user_id: int, damage: int) -> dict[str, int]:
        """Damage the battle and character Vitality as one authoritative write."""
        amount = max(0, int(damage))
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                """SELECT b.player_hp,c.vitality,c.vitality_max
                   FROM battles b JOIN characters c ON c.user_id=b.user_id
                   WHERE b.battle_id=? AND b.user_id=? AND b.status='active'""",
                (int(battle_id), int(user_id)),
            )
            row = await cur.fetchone()
            if not row:
                await db.rollback()
                return {}
            current = min(int(row["player_hp"]), int(row["vitality"]))
            updated = max(0, current - amount)
            now = time.time()
            await db.execute(
                "UPDATE characters SET vitality=?,updated_at=? WHERE user_id=?",
                (updated, now, int(user_id)),
            )
            await db.execute(
                """UPDATE battles SET player_hp=?,player_hp_max=MAX(player_hp_max,?),
                       version=version+1,updated_at=? WHERE battle_id=? AND user_id=? AND status='active'""",
                (updated, int(row["vitality_max"]), now, int(battle_id), int(user_id)),
            )
            await db.commit()
            return {"vitality": updated, "vitality_max": int(row["vitality_max"])}

    # ------------------------------------------------------------------
    # Memory / RAG v1 (SQLite FTS5 + deterministic retrieval)
    # ------------------------------------------------------------------
    async def add_rag_memory(
        self, user_id: int, *, summary: str, memory_kind: str = "scene",
        salience: int = 25, location: str = "", npc_name: str = "",
        faction: str = "", source: str = "scene", game_minute: int = 0,
        source_key: str | None = None,
    ) -> int:
        """Persist a player-owned episodic memory for later FTS retrieval.

        RAG memories are descriptive recollections only. They never become
        mechanical authority and are always filtered by user_id before retrieval.
        """
        now = time.time()
        safe_salience = max(0, min(100, int(salience)))
        key = str(source_key or f"scene:{int(user_id)}:{time.time_ns()}:{secrets.token_hex(3)}")[:220]
        async with self._connect() as db:
            cur = await db.execute(
                """INSERT INTO rag_memories(
                       source_key,user_id,memory_kind,summary,salience,location,npc_name,faction,
                       source,game_minute,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(source_key) DO UPDATE SET
                       memory_kind=excluded.memory_kind,summary=excluded.summary,salience=excluded.salience,
                       location=excluded.location,npc_name=excluded.npc_name,faction=excluded.faction,
                       source=excluded.source,game_minute=excluded.game_minute,updated_at=excluded.updated_at
                   RETURNING memory_id""",
                (
                    key, int(user_id), str(memory_kind)[:60], str(summary)[:1200], safe_salience,
                    str(location)[:160], str(npc_name)[:120], str(faction)[:160], str(source)[:60],
                    int(game_minute), now, now,
                ),
            )
            row = await cur.fetchone()
            memory_id = int(row[0]) if row else 0
            # Keep broad episodic memory bounded. NPC-pair memories are already
            # bounded separately and mirror into this table through schema triggers.
            await db.execute(
                """DELETE FROM rag_memories
                   WHERE user_id=? AND source_key NOT LIKE 'npc:%' AND memory_id NOT IN (
                       SELECT memory_id FROM rag_memories
                       WHERE user_id=? AND source_key NOT LIKE 'npc:%'
                       ORDER BY salience DESC, game_minute DESC, memory_id DESC LIMIT 256
                   )""",
                (int(user_id), int(user_id)),
            )
            await db.commit()
            return memory_id

    async def search_rag_memories(
        self, user_id: int, match_query: str, *, limit: int = 12,
        mark_recalled: bool = False,
    ) -> list[dict[str, Any]]:
        if not str(match_query).strip():
            return []
        safe_limit = max(1, min(40, int(limit)))
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT m.*, bm25(rag_memories_fts, 1.0, 0.25, 0.7, 0.45, 0.5) AS fts_rank
                   FROM rag_memories_fts
                   JOIN rag_memories AS m ON m.memory_id=rag_memories_fts.rowid
                   WHERE rag_memories_fts MATCH ? AND m.user_id=?
                   ORDER BY fts_rank ASC, m.salience DESC, m.game_minute DESC
                   LIMIT ?""",
                (str(match_query), int(user_id), safe_limit),
            )
            rows = [dict(r) for r in await cur.fetchall()]
            if rows and mark_recalled:
                ids = [int(r["memory_id"]) for r in rows]
                placeholders = ",".join("?" for _ in ids)
                await db.execute(
                    f"UPDATE rag_memories SET recalled_count=recalled_count+1,last_recalled_at=? WHERE memory_id IN ({placeholders})",
                    (time.time(), *ids),
                )
                await db.commit()
            return rows

    async def search_rag_canon(self, match_query: str, *, limit: int = 30) -> list[dict[str, Any]]:
        if not str(match_query).strip():
            return []
        safe_limit = max(1, min(80, int(limit)))
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT d.*, bm25(rag_canon_fts, 1.2, 1.0, 0.45) AS fts_rank
                   FROM rag_canon_fts
                   JOIN rag_canon_documents AS d ON d.doc_id=rag_canon_fts.rowid
                   WHERE rag_canon_fts MATCH ?
                   ORDER BY fts_rank ASC, d.priority DESC, d.doc_id ASC
                   LIMIT ?""",
                (str(match_query), safe_limit),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def rag_stats(self) -> dict[str, int]:
        async with self._connect() as db:
            out: dict[str, int] = {}
            for key, table in (
                ("memories", "rag_memories"),
                ("canon_documents", "rag_canon_documents"),
                ("world_history", "world_history_events"),
            ):
                cur = await db.execute(f"SELECT COUNT(*) FROM {table}")
                out[key] = int((await cur.fetchone())[0])
            return out

    async def record_world_history_event(
        self, *, event_type: str, title: str, summary: str, significance: int = 50,
        visibility: str = "public", location: str = "", world_name: str = "",
        faction: str = "", actor_type: str = "", actor_key: str = "", actor_name: str = "",
        target_type: str = "", target_key: str = "", target_name: str = "",
        related_user_id: int | None = None, related_npc_name: str = "", tags: list[str] | tuple[str, ...] = (),
        game_minute: int = 0, metadata: dict[str, Any] | None = None, source_key: str | None = None,
    ) -> dict[str, Any]:
        """Append or idempotently update one canonical historical event.

        History is descriptive past-tense canon, not mutable mechanical state.
        ``visibility`` is intentionally narrow: ``public``, ``participant``,
        ``faction`` or ``hidden``. Hidden rows are never returned to narrator RAG.
        """
        now = time.time()
        safe_visibility = str(visibility or "public").strip().lower()
        if safe_visibility not in {"public", "participant", "faction", "hidden"}:
            safe_visibility = "participant"
        safe_significance = max(1, min(100, int(significance)))
        tag_text = " ".join(dict.fromkeys(str(x).strip() for x in tags if str(x).strip()))[:700]
        key = str(source_key or f"history:{str(event_type)[:50]}:{time.time_ns()}:{secrets.token_hex(3)}")[:240]
        payload = json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)
        if len(payload) > 5000:
            payload = json.dumps({"truncated": True, "preview": payload[:4500]}, ensure_ascii=False, sort_keys=True)
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """INSERT INTO world_history_events(
                       source_key,event_type,title,summary,significance,visibility,location,world_name,faction,
                       actor_type,actor_key,actor_name,target_type,target_key,target_name,related_user_id,
                       related_npc_name,tags,game_minute,metadata_json,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(source_key) DO UPDATE SET
                       event_type=excluded.event_type,title=excluded.title,summary=excluded.summary,
                       significance=excluded.significance,visibility=excluded.visibility,location=excluded.location,
                       world_name=excluded.world_name,faction=excluded.faction,actor_type=excluded.actor_type,
                       actor_key=excluded.actor_key,actor_name=excluded.actor_name,target_type=excluded.target_type,
                       target_key=excluded.target_key,target_name=excluded.target_name,
                       related_user_id=excluded.related_user_id,related_npc_name=excluded.related_npc_name,
                       tags=excluded.tags,game_minute=excluded.game_minute,metadata_json=excluded.metadata_json,
                       updated_at=excluded.updated_at
                   RETURNING *""",
                (
                    key, str(event_type)[:80], str(title)[:220], str(summary)[:1800], safe_significance,
                    safe_visibility, str(location)[:180], str(world_name)[:120], str(faction)[:180],
                    str(actor_type)[:60], str(actor_key)[:180], str(actor_name)[:160],
                    str(target_type)[:60], str(target_key)[:180], str(target_name)[:160],
                    int(related_user_id) if related_user_id is not None else None, str(related_npc_name)[:160],
                    tag_text, int(game_minute), payload, now, now,
                ),
            )
            row = await cur.fetchone()
            await db.commit()
        data = dict(row) if row else {}
        try:
            data["metadata"] = json.loads(data.get("metadata_json") or "{}")
        except Exception:
            data["metadata"] = {}
        return data

    async def search_world_history(self, match_query: str, *, limit: int = 40) -> list[dict[str, Any]]:
        """FTS candidate search; permission filtering happens in the RAG retriever."""
        if not str(match_query).strip():
            return []
        safe_limit = max(1, min(100, int(limit)))
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT h.*, bm25(world_history_fts,1.15,1.0,0.35,0.45,0.5,0.7,0.7,0.4) AS fts_rank
                   FROM world_history_fts
                   JOIN world_history_events AS h ON h.history_id=world_history_fts.rowid
                   WHERE world_history_fts MATCH ?
                   ORDER BY fts_rank ASC,h.significance DESC,h.game_minute DESC,h.history_id DESC
                   LIMIT ?""",
                (str(match_query), safe_limit),
            )
            rows = [dict(r) for r in await cur.fetchall()]
        for row in rows:
            try: row["metadata"] = json.loads(row.get("metadata_json") or "{}")
            except Exception: row["metadata"] = {}
        return rows

    async def get_structured_world_history(
        self, *, location: str = "", user_id: int | None = None, npc_name: str = "",
        factions: list[str] | tuple[str, ...] = (), min_significance: int = 35, limit: int = 24,
    ) -> list[dict[str, Any]]:
        """Return high-signal structured candidates before lexical ranking.

        This deliberately returns a superset. The RAG retriever performs the
        final viewpoint/visibility check so one code path owns knowledge safety.
        """
        clauses = ["significance>=?"]
        params: list[Any] = [max(1, min(100, int(min_significance)))]
        relevance: list[str] = []
        if location:
            relevance.append("location=?"); params.append(str(location))
        if user_id is not None:
            relevance.append("related_user_id=?"); params.append(int(user_id))
            relevance.append("actor_key=?"); params.append(str(int(user_id)))
            relevance.append("target_key=?"); params.append(str(int(user_id)))
        if npc_name:
            relevance.extend(["related_npc_name=?", "actor_name=?", "target_name=?"]); params.extend([str(npc_name)] * 3)
        clean_factions = [str(x) for x in factions if str(x).strip()]
        if clean_factions:
            placeholders = ",".join("?" for _ in clean_factions)
            relevance.append(f"faction IN ({placeholders})"); params.extend(clean_factions)
        if relevance:
            clauses.append("(" + " OR ".join(relevance) + ")")
        sql = (
            "SELECT * FROM world_history_events WHERE " + " AND ".join(clauses) +
            " ORDER BY significance DESC,game_minute DESC,history_id DESC LIMIT ?"
        )
        params.append(max(1, min(80, int(limit))))
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(sql, tuple(params))
            rows = [dict(r) for r in await cur.fetchall()]
        for row in rows:
            row["fts_rank"] = None
            try: row["metadata"] = json.loads(row.get("metadata_json") or "{}")
            except Exception: row["metadata"] = {}
        return rows

    async def list_world_history(
        self, *, event_type: str = "", location: str = "", faction: str = "", limit: int = 50
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM world_history_events WHERE 1=1"; params: list[Any] = []
        if event_type: sql += " AND event_type=?"; params.append(str(event_type))
        if location: sql += " AND location=?"; params.append(str(location))
        if faction: sql += " AND faction=?"; params.append(str(faction))
        sql += " ORDER BY game_minute DESC,significance DESC,history_id DESC LIMIT ?"
        params.append(max(1, min(200, int(limit))))
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(sql, tuple(params))
            rows = [dict(r) for r in await cur.fetchall()]
        for row in rows:
            try: row["metadata"] = json.loads(row.get("metadata_json") or "{}")
            except Exception: row["metadata"] = {}
        return rows

    async def sync_rag_canon(self, world_data: dict[str, Any]) -> None:
        """Index only narrator-safe game canon.

        Location descriptions are retrievable only while physically at that
        location. Manuals and techniques are tagged as known_manual and are
        filtered against the player's learned manual list by the retriever.
        NPC secrets, schedules, encounter seeds and unrevealed locations are not
        inserted into the public RAG corpus.
        """
        now = time.time()
        documents: list[tuple[str, str, str, str, str, str, str, int]] = []
        for name, data in (world_data.get("locations", {}) or {}).items():
            description = str(data.get("description") or "").strip()
            world_name = str(data.get("world") or "").strip()
            body = description
            if world_name:
                body += f" World: {world_name}."
            if bool(data.get("safe_zone")):
                body += " This is a protected location where ordinary violence cannot mechanically begin."
            documents.append((
                f"location:{name}", "location", str(name), body[:2400],
                f"location {world_name}".strip(), "location", str(name), 80,
            ))

        manuals = (world_data.get("technique_system", {}) or {}).get("manuals", {}) or {}
        techniques = (world_data.get("technique_system", {}) or {}).get("techniques", {}) or {}
        for manual_id, data in manuals.items():
            title = str(data.get("name") or manual_id)
            body = " ".join(
                x for x in (
                    str(data.get("description") or ""),
                    f"Path: {data.get('path')}." if data.get("path") else "",
                    f"Alignment: {data.get('alignment')}." if data.get("alignment") else "",
                    f"Grade: {data.get('grade')}." if data.get("grade") else "",
                ) if x
            )
            documents.append((
                f"manual:{manual_id}", "manual", title, body[:2400],
                f"manual {manual_id} {data.get('path','')} {data.get('alignment','')}",
                "known_manual", "", 75,
            ))
        for technique_id, data in techniques.items():
            manual_id = str(data.get("manual") or "")
            title = str(data.get("name") or technique_id)
            tags = " ".join(str(x) for x in (data.get("tags") or []))
            body = " ".join(
                x for x in (
                    str(data.get("description") or ""),
                    f"Technique of manual {manual_id}." if manual_id else "",
                    f"Tags: {tags}." if tags else "",
                ) if x
            )
            documents.append((
                f"technique:{technique_id}", "technique", title, body[:2400],
                f"technique {technique_id} manual:{manual_id} {tags}",
                "known_manual", "", 72,
            ))

        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            # These prefixes are wholly owned by the current content catalog.
            # Remove stale rows before re-seeding so deleted/renamed canon cannot
            # linger in FTS results after an application update.
            await db.execute(
                "DELETE FROM rag_canon_documents WHERE source_key LIKE 'location:%' OR source_key LIKE 'manual:%' OR source_key LIKE 'technique:%'"
            )
            for source_key, kind, title, body, tags, scope, location, priority in documents:
                await db.execute(
                    """INSERT INTO rag_canon_documents(
                           source_key,kind,title,body,tags,knowledge_scope,location,priority,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(source_key) DO UPDATE SET
                           kind=excluded.kind,title=excluded.title,body=excluded.body,tags=excluded.tags,
                           knowledge_scope=excluded.knowledge_scope,location=excluded.location,
                           priority=excluded.priority,updated_at=excluded.updated_at""",
                    (source_key, kind, title, body, tags, scope, location, int(priority), now),
                )
            await db.commit()

    # ------------------------------------------------------------------
    # Normalized world catalog
    # ------------------------------------------------------------------
    async def sync_world_catalog(self, world_data: dict[str, Any]) -> None:
        now = time.time()
        mappings = (
            ("catalog_locations", world_data.get("locations", {})),
            ("catalog_npcs", world_data.get("npcs", {})),
            ("catalog_recipes", world_data.get("recipes", {})),
            ("catalog_manuals", world_data.get("technique_system", {}).get("manuals", {})),
            ("catalog_techniques", world_data.get("technique_system", {}).get("techniques", {})),
        )
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            for table, mapping in mappings:
                for name, data in mapping.items():
                    await db.execute(
                        f"""INSERT INTO {table}(name,data_json,updated_at) VALUES(?,?,?)
                            ON CONFLICT(name) DO UPDATE SET data_json=excluded.data_json,updated_at=excluded.updated_at""",
                        (str(name), json.dumps(data, ensure_ascii=False), now),
                    )
            # Every normal location is also a persistent territory node. This
            # gives wars, resource control and caravans a canonical map without
            # requiring a destructive content migration.
            for location_name, location_data in world_data.get("locations", {}).items():
                text = (str(location_data.get("description", "")) + " " + " ".join(location_data.get("encounters", []))).casefold()
                resource = "spirit_herbs" if "herb" in text else ("ore" if "ore" in text or "mine" in text else ("beast_grounds" if "beast" in text else "mixed"))
                await db.execute(
                    """INSERT INTO territory_state(territory_key,name,region,resource_type,updated_game_minute,updated_at)
                       VALUES(?,?,?,?,0,?) ON CONFLICT(territory_key) DO UPDATE SET name=excluded.name,region=excluded.region,
                       resource_type=excluded.resource_type,updated_at=excluded.updated_at""",
                    (str(location_name), str(location_name), str(location_name), resource, now),
                )
            cur = await db.execute("SELECT 1 FROM world_eras WHERE active=1 LIMIT 1")
            if not await cur.fetchone():
                await db.execute(
                    "INSERT INTO world_eras(name,description,started_game_minute,active,modifiers_json,created_at) VALUES(?,?,0,1,'{}',?)",
                    ("Jade Meridian Awakening Era", "The baseline era of the shared cultivation world.", now),
                )
            await db.commit()
        self._catalog_cache.clear()

    async def _catalog_get(self, table: str, name: str) -> dict[str, Any] | None:
        if table not in {"catalog_locations", "catalog_npcs", "catalog_recipes", "catalog_manuals", "catalog_techniques"}:
            raise ValueError("Unknown catalog")
        key = (table, str(name))
        cached = self._catalog_cache.get(key)
        if cached is not None:
            self._catalog_cache_hits += 1
            return copy.deepcopy(cached)
        self._catalog_cache_misses += 1
        async with self._connect() as db:
            cur = await db.execute(f"SELECT data_json FROM {table} WHERE name=?", (name,))
            row = await cur.fetchone()
        if not row:
            return None
        value = json.loads(row[0])
        self._catalog_cache[key] = value
        return copy.deepcopy(value)

    async def get_location_definition(self, name: str) -> dict[str, Any] | None:
        return await self._catalog_get("catalog_locations", name)

    async def get_npc_definition(self, name: str) -> dict[str, Any] | None:
        return await self._catalog_get("catalog_npcs", name)

    async def get_recipe_definition(self, name: str) -> dict[str, Any] | None:
        return await self._catalog_get("catalog_recipes", name)

    async def search_catalog(self, kind: str, query: str = "", limit: int = 25) -> list[str]:
        table = {"location": "catalog_locations", "npc": "catalog_npcs", "recipe": "catalog_recipes", "manual": "catalog_manuals", "technique": "catalog_techniques"}.get(kind)
        if table is None:
            return []
        needle = f"%{query.strip()}%"
        async with self._connect() as db:
            cur = await db.execute(
                f"SELECT name FROM {table} WHERE name LIKE ? COLLATE NOCASE ORDER BY name LIMIT ?",
                (needle, max(1, min(int(limit), 25))),
            )
            return [str(r[0]) for r in await cur.fetchall()]

    # ------------------------------------------------------------------
    # World clock
    # ------------------------------------------------------------------
    async def get_world_clock(self, *, scale: int = 4) -> dict[str, Any]:
        now = time.time()
        requested_scale = max(0, int(scale))
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT value_json FROM world_state WHERE key='world_clock'")
            row = await cur.fetchone()
            if row:
                state = json.loads(row[0])
            else:
                state = {"anchor_game_minute": 8 * 60, "anchor_real_ts": now, "scale": requested_scale}
                await db.execute(
                    "INSERT INTO world_state(key,value_json,updated_at) VALUES('world_clock',?,?)",
                    (json.dumps(state), now),
                )
                await db.commit()
            old_scale = max(0, int(state.get("scale", requested_scale)))
            elapsed_real_minutes = max(0.0, (now - float(state.get("anchor_real_ts", now))) / 60.0)
            game_minute = int(state.get("anchor_game_minute", 0) + elapsed_real_minutes * old_scale)
            if old_scale != requested_scale:
                state = {"anchor_game_minute": game_minute, "anchor_real_ts": now, "scale": requested_scale}
                await db.execute(
                    "UPDATE world_state SET value_json=?,updated_at=? WHERE key='world_clock'",
                    (json.dumps(state), now),
                )
                await db.commit()
            return {**state, "game_minute": game_minute}

    async def advance_world_clock(self, minutes: int, *, scale: int = 4) -> int:
        state = await self.get_world_clock(scale=scale)
        now = time.time()
        new_minute = max(0, int(state["game_minute"]) + int(minutes))
        payload = {"anchor_game_minute": new_minute, "anchor_real_ts": now, "scale": int(state.get("scale", scale))}
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO world_state(key,value_json,updated_at) VALUES('world_clock',?,?)
                   ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
                (json.dumps(payload), now),
            )
            await db.commit()
        return new_minute

    # ------------------------------------------------------------------
    # Innate aptitudes: spiritual roots, bloodlines and physiques

    @staticmethod
    def _decode_root(row: aiosqlite.Row | None) -> dict[str, Any] | None:
        if not row:
            return None
        out = dict(row)
        try:
            out["elements"] = json.loads(out.pop("elements_json"))
        except Exception:
            out["elements"] = []
        return out

    @staticmethod
    def _decode_bloodline(row: aiosqlite.Row | None) -> dict[str, Any] | None:
        if not row:
            return None
        out = dict(row)
        try:
            out["unlocked_techniques"] = json.loads(out.pop("unlocked_techniques_json"))
        except Exception:
            out["unlocked_techniques"] = []
        return out

    async def get_aptitudes(self, user_id: int) -> dict[str, Any]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM character_spiritual_roots WHERE user_id=?", (user_id,))
            root = self._decode_root(await cur.fetchone())
            cur = await db.execute(
                "SELECT * FROM character_bloodlines WHERE user_id=? ORDER BY primary_lineage DESC,id",
                (user_id,),
            )
            bloodlines = [self._decode_bloodline(row) for row in await cur.fetchall()]
            cur = await db.execute("SELECT * FROM character_physiques WHERE user_id=?", (user_id,))
            physique_row = await cur.fetchone()
            physique = dict(physique_row) if physique_row else None
        return {
            "root": root,
            "bloodline": next((row for row in bloodlines if row and int(row.get("primary_lineage", 0))), None)
            or (bloodlines[0] if bloodlines else None),
            "bloodlines": [row for row in bloodlines if row],
            "physique": physique,
        }

    async def save_root_profile(self, user_id: int, root: dict[str, Any], *, event_type: str = "root_progression") -> None:
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """UPDATE character_spiritual_roots SET
                       grade=?,purity=?,elements_json=?,mutation=?,stability=?,refinement_progress=?,compatibility=?,updated_at=?
                   WHERE user_id=?""",
                (
                    str(root.get("grade", "Common")), max(1, min(100, int(root.get("purity", 50)))),
                    json.dumps(list(root.get("elements") or [])), str(root.get("mutation", "")),
                    max(0, min(100, int(root.get("stability", 100)))),
                    max(0, min(100, int(root.get("refinement_progress", 0)))),
                    max(0, min(100, int(root.get("compatibility", 50)))), now, user_id,
                ),
            )
            await db.execute(
                "INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                (user_id, event_type, json.dumps(root), now),
            )
            await db.commit()

    async def save_bloodline_profile(self, user_id: int, bloodline: dict[str, Any], *, event_type: str = "bloodline_progression") -> None:
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """UPDATE character_bloodlines SET
                       name=?,affinity=?,purity=?,state=?,evolution_stage=?,progress=?,rejection=?,mutation=?,
                       unlocked_techniques_json=?,updated_at=?
                   WHERE user_id=? AND bloodline_id=?""",
                (
                    str(bloodline.get("name", "Ancestral Bloodline")), str(bloodline.get("affinity", "None")),
                    max(0, min(100, int(bloodline.get("purity", 0)))), str(bloodline.get("state", "dormant")),
                    max(0, int(bloodline.get("evolution_stage", 0))), max(0, min(100, int(bloodline.get("progress", 0)))),
                    max(0, min(100, int(bloodline.get("rejection", 0)))), str(bloodline.get("mutation", "")),
                    json.dumps(list(bloodline.get("unlocked_techniques") or [])), now, user_id,
                    str(bloodline.get("bloodline_id", "legacy_family_bloodline")),
                ),
            )
            await db.execute(
                "INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                (user_id, event_type, json.dumps(bloodline), now),
            )
            await db.commit()

    async def save_physique_profile(self, user_id: int, physique: dict[str, Any], *, event_type: str = "physique_progression") -> None:
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """UPDATE character_physiques SET
                       physique_id=?,name=?,state=?,evolution_stage=?,progress=?,stability=?,instability=?,updated_at=?
                   WHERE user_id=?""",
                (
                    str(physique.get("physique_id", "ordinary_mortal_body")),
                    str(physique.get("name", "Ordinary Mortal Body")), str(physique.get("state", "ordinary")),
                    max(0, int(physique.get("evolution_stage", 0))), max(0, min(100, int(physique.get("progress", 0)))),
                    max(0, min(100, int(physique.get("stability", 100)))),
                    max(0, min(100, int(physique.get("instability", 0)))), now, user_id,
                ),
            )
            await db.execute(
                "INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                (user_id, event_type, json.dumps(physique), now),
            )
            await db.commit()

    async def train_aptitude(self, user_id: int, *, target: str, essence_cost: int, progress_gain: int) -> dict[str, Any]:
        """Atomically spend the appropriate essence pool and add progress."""
        target = str(target).lower()
        if target not in {"root", "bloodline", "physique"}:
            raise ValueError("Target must be root, bloodline, or physique")
        cost = max(1, int(essence_cost))
        gain = max(1, int(progress_gain))
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            pool = "body_cultivation" if target == "physique" else "cultivation"
            cur = await db.execute(f"SELECT {pool},life_status FROM characters WHERE user_id=?", (user_id,))
            row = await cur.fetchone()
            if not row:
                await db.rollback()
                raise ValueError("Create a cultivation character first")
            if str(row[1]) != "alive":
                await db.rollback()
                raise ValueError("A deceased incarnation cannot temper innate aptitudes")
            if int(row[0]) < cost:
                await db.rollback()
                raise ValueError(f"You need {cost} {'body ' if target == 'physique' else ''}cultivation essence")
            await db.execute(f"UPDATE characters SET {pool}={pool}-?,updated_at=? WHERE user_id=?", (cost, now, user_id))
            if target == "root":
                cur = await db.execute("SELECT refinement_progress FROM character_spiritual_roots WHERE user_id=?", (user_id,))
                current = await cur.fetchone()
                if not current:
                    await db.rollback()
                    raise ValueError("No spiritual-root profile exists")
                awarded = min(gain, max(0, 100 - int(current[0])))
                await db.execute(
                    "UPDATE character_spiritual_roots SET refinement_progress=refinement_progress+?,updated_at=? WHERE user_id=?",
                    (awarded, now, user_id),
                )
            elif target == "bloodline":
                cur = await db.execute(
                    "SELECT id,progress FROM character_bloodlines WHERE user_id=? ORDER BY primary_lineage DESC,id LIMIT 1",
                    (user_id,),
                )
                current = await cur.fetchone()
                if not current:
                    await db.rollback()
                    raise ValueError("You do not carry a recognized bloodline")
                awarded = min(gain, max(0, 100 - int(current[1])))
                await db.execute("UPDATE character_bloodlines SET progress=progress+?,updated_at=? WHERE id=?", (awarded, now, int(current[0])))
            else:
                cur = await db.execute("SELECT physique_id,progress FROM character_physiques WHERE user_id=?", (user_id,))
                current = await cur.fetchone()
                if not current or str(current[0]) == "ordinary_mortal_body":
                    await db.rollback()
                    raise ValueError("You do not possess a special physique to temper")
                awarded = min(gain, max(0, 100 - int(current[1])))
                await db.execute("UPDATE character_physiques SET progress=progress+?,updated_at=? WHERE user_id=?", (awarded, now, user_id))
            await db.execute(
                "INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                (user_id, "aptitude_temper", json.dumps({"target": target, "cost": cost, "progress": awarded}), now),
            )
            await db.commit()
        result = await self.get_aptitudes(user_id)
        result["awarded"] = awarded
        result["cost"] = cost
        return result

    async def harmonize_aptitude(self, user_id: int, *, target: str, essence_cost: int, amount: int) -> dict[str, Any]:
        target = str(target).lower()
        if target not in {"root", "bloodline", "physique"}:
            raise ValueError("Target must be root, bloodline, or physique")
        cost = max(1, int(essence_cost))
        amount = max(1, int(amount))
        pool = "body_cultivation" if target == "physique" else "cultivation"
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(f"SELECT {pool} FROM characters WHERE user_id=?", (user_id,))
            row = await cur.fetchone()
            if not row or int(row[0]) < cost:
                await db.rollback()
                raise ValueError(f"You need {cost} {'body ' if target == 'physique' else ''}cultivation essence")
            await db.execute(f"UPDATE characters SET {pool}={pool}-?,updated_at=? WHERE user_id=?", (cost, now, user_id))
            if target == "root":
                await db.execute(
                    "UPDATE character_spiritual_roots SET stability=MIN(100,stability+?),updated_at=? WHERE user_id=?",
                    (amount, now, user_id),
                )
            elif target == "bloodline":
                await db.execute(
                    """UPDATE character_bloodlines SET rejection=MAX(0,rejection-?),
                           state=CASE WHEN state='rejected' AND rejection-?<100 THEN 'dormant' ELSE state END,updated_at=?
                       WHERE id=(SELECT id FROM character_bloodlines WHERE user_id=? ORDER BY primary_lineage DESC,id LIMIT 1)""",
                    (amount, amount, now, user_id),
                )
            else:
                await db.execute(
                    "UPDATE character_physiques SET instability=MAX(0,instability-?),stability=MIN(100,stability+?),updated_at=? WHERE user_id=?",
                    (amount, max(1, amount // 2), now, user_id),
                )
            await db.execute(
                "INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                (user_id, "aptitude_harmonize", json.dumps({"target": target, "cost": cost, "amount": amount}), now),
            )
            await db.commit()
        return await self.get_aptitudes(user_id)

    # Generic effects
    # ------------------------------------------------------------------
    async def apply_effect(
        self, user_id: int, *, effect_key: str, name: str, source_type: str, source_id: str,
        effect: dict[str, Any], starts_game_minute: int, duration_game_minutes: int | None = None,
        stacks: int = 1,
    ) -> None:
        ends = None if duration_game_minutes is None else int(starts_game_minute) + max(0, int(duration_game_minutes))
        now = time.time()
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO active_effects(
                       user_id,effect_key,name,source_type,source_id,effect_json,stacks,starts_game_minute,ends_game_minute,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(user_id,effect_key,source_type,source_id) DO UPDATE SET
                       name=excluded.name,effect_json=excluded.effect_json,stacks=excluded.stacks,
                       starts_game_minute=excluded.starts_game_minute,ends_game_minute=excluded.ends_game_minute,
                       created_at=excluded.created_at""",
                (user_id,effect_key,name,source_type,source_id,json.dumps(effect),max(1,int(stacks)),int(starts_game_minute),ends,now),
            )
            await db.commit()

    async def remove_effect(self, user_id: int, *, effect_key: str, source_type: str, source_id: str) -> bool:
        async with self._connect() as db:
            cur = await db.execute(
                "DELETE FROM active_effects WHERE user_id=? AND effect_key=? AND source_type=? AND source_id=?",
                (int(user_id), str(effect_key), str(source_type), str(source_id)),
            )
            await db.commit()
            return bool(int(cur.rowcount or 0) > 0)

    async def get_active_effects(self, user_id: int, game_minute: int) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                "DELETE FROM active_effects WHERE user_id=? AND ends_game_minute IS NOT NULL AND ends_game_minute<=?",
                (user_id, int(game_minute)),
            )
            cur = await db.execute(
                "SELECT * FROM active_effects WHERE user_id=? ORDER BY id", (user_id,)
            )
            rows = await cur.fetchall()
            await db.commit()
            out=[]
            for row in rows:
                d=dict(row); payload=json.loads(d.pop("effect_json")); d.update(payload); out.append(d)
            return out

    # ------------------------------------------------------------------
    # Currency/wallet
    # ------------------------------------------------------------------
    async def get_wallet(self, user_id: int) -> dict[str, int]:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT currency_id,balance FROM currency_wallets WHERE user_id=? AND balance<>0 ORDER BY currency_id",
                (user_id,),
            )
            return {str(k): int(v) for k,v in await cur.fetchall()}

    async def add_currency(self, user_id: int, currency_id: str, amount: int) -> int:
        amount=int(amount)
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(?,?,?)
                   ON CONFLICT(user_id,currency_id) DO UPDATE SET balance=balance+excluded.balance""",
                (user_id,currency_id,amount),
            )
            if currency_id == "low_spirit_stone":
                await db.execute(
                    "UPDATE characters SET spirit_stones=MAX(0,spirit_stones+?),updated_at=? WHERE user_id=?",
                    (amount,time.time(),user_id),
                )
            cur=await db.execute(
                "SELECT balance FROM currency_wallets WHERE user_id=? AND currency_id=?", (user_id,currency_id)
            )
            row=await cur.fetchone(); balance=max(0,int(row[0]) if row else 0)
            if balance < 0:
                raise ValueError("Currency balance cannot become negative")
            await db.commit(); return balance

    async def _wallet_delta(self, db: aiosqlite.Connection, user_id: int, currency_id: str, delta: int) -> int:
        cur=await db.execute(
            "SELECT balance FROM currency_wallets WHERE user_id=? AND currency_id=?", (user_id,currency_id)
        )
        row=await cur.fetchone(); current=int(row[0]) if row else 0
        new=current+int(delta)
        if new < 0:
            raise ValueError("Insufficient currency")
        await db.execute(
            """INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(?,?,?)
               ON CONFLICT(user_id,currency_id) DO UPDATE SET balance=excluded.balance""",
            (user_id,currency_id,new),
        )
        if currency_id == "low_spirit_stone":
            await db.execute(
                "UPDATE characters SET spirit_stones=?,updated_at=? WHERE user_id=?", (new,time.time(),user_id)
            )
        return new

    # ------------------------------------------------------------------
    # Spatial storage
    # ------------------------------------------------------------------
    async def get_storage(self, user_id: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row
            cur=await db.execute("SELECT * FROM storage_containers WHERE user_id=?",(user_id,))
            row=await cur.fetchone()
            if not row: return None
            data=dict(row)
            cur=await db.execute(
                "SELECT item_id,quantity FROM storage_inventory WHERE user_id=? AND quantity>0 ORDER BY item_id",(user_id,)
            )
            data["items"]={str(k):int(v) for k,v in await cur.fetchall()}
            data["used_slots"]=len(data["items"]); return data

    async def set_storage_container(
        self,user_id:int,*,container_id:str,name:str,grade:str,slot_capacity:int,living_space:bool=False
    )->None:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM storage_inventory WHERE user_id=? AND quantity>0", (user_id,)
            )
            used = int((await cur.fetchone())[0])
            safe_capacity = max(used, max(1, int(slot_capacity)))
            await db.execute(
                """INSERT INTO storage_containers(user_id,container_id,name,grade,slot_capacity,living_space,updated_at)
                   VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
                   container_id=excluded.container_id,name=excluded.name,grade=excluded.grade,
                   slot_capacity=excluded.slot_capacity,living_space=excluded.living_space,updated_at=excluded.updated_at""",
                (user_id,container_id,name,grade,safe_capacity,1 if living_space else 0,time.time()),
            ); await db.commit()

    async def storage_deposit(self,user_id:int,item_id:str,quantity:int)->None:
        quantity=max(1,int(quantity))
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur=await db.execute("SELECT quantity FROM inventory WHERE user_id=? AND item_id=?",(user_id,item_id))
            row=await cur.fetchone(); owned=int(row[0]) if row else 0
            if owned < quantity: raise ValueError("Not enough of that item in carried inventory")
            cur=await db.execute("SELECT slot_capacity FROM storage_containers WHERE user_id=?",(user_id,)); container=await cur.fetchone()
            if not container: raise ValueError("No spatial storage container")
            cur=await db.execute("SELECT 1 FROM storage_inventory WHERE user_id=? AND item_id=? AND quantity>0",(user_id,item_id)); exists=await cur.fetchone()
            if not exists:
                cur=await db.execute("SELECT COUNT(*) FROM storage_inventory WHERE user_id=? AND quantity>0",(user_id,)); used=int((await cur.fetchone())[0])
                if used >= int(container[0]): raise ValueError("Spatial storage has no free item slots")
            await db.execute("UPDATE inventory SET quantity=quantity-? WHERE user_id=? AND item_id=?",(quantity,user_id,item_id))
            await db.execute(
                """INSERT INTO storage_inventory(user_id,item_id,quantity) VALUES(?,?,?)
                   ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity""",
                (user_id,item_id,quantity),
            ); await db.commit()

    async def storage_withdraw(self,user_id:int,item_id:str,quantity:int)->None:
        quantity=max(1,int(quantity))
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur=await db.execute("SELECT quantity FROM storage_inventory WHERE user_id=? AND item_id=?",(user_id,item_id))
            row=await cur.fetchone(); owned=int(row[0]) if row else 0
            if owned < quantity: raise ValueError("Not enough of that item in spatial storage")
            await db.execute("UPDATE storage_inventory SET quantity=quantity-? WHERE user_id=? AND item_id=?",(quantity,user_id,item_id))
            await db.execute(
                """INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,?)
                   ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity""",
                (user_id,item_id,quantity),
            ); await db.commit()

    # ------------------------------------------------------------------
    # Sect hierarchy/resources
    # ------------------------------------------------------------------
    async def ensure_sect(self,sect_name:str)->None:
        async with self._connect() as db:
            await db.execute(
                "INSERT INTO sects(sect_name,updated_at) VALUES(?,?) ON CONFLICT(sect_name) DO NOTHING",
                (sect_name.strip(),time.time()),
            ); await db.commit()

    async def get_sect_roster(self,sect_name:str)->list[dict[str,Any]]:
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row
            cur=await db.execute(
                """SELECT c.user_id,c.name,c.realm_index,c.phase,sm.rank_name,sm.rank_level,
                          sm.contribution_points,sm.influence
                   FROM sect_membership sm JOIN characters c ON c.user_id=sm.user_id
                   WHERE sm.sect_name=? ORDER BY sm.rank_level DESC,sm.contribution_points DESC,c.realm_index DESC,c.phase DESC""",
                (sect_name,),
            ); return [dict(r) for r in await cur.fetchall()]

    async def sect_contribute(self,user_id:int,item_id:str,quantity:int,point_value:int)->int:
        quantity=max(1,int(quantity)); point_value=max(1,int(point_value))
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur=await db.execute("SELECT sect_name FROM sect_membership WHERE user_id=?",(user_id,)); row=await cur.fetchone()
            if not row: raise ValueError("You are not a member of a sect")
            sect_name=str(row[0])
            cur=await db.execute("SELECT quantity FROM inventory WHERE user_id=? AND item_id=?",(user_id,item_id)); inv=await cur.fetchone()
            if not inv or int(inv[0])<quantity: raise ValueError("Not enough of that item")
            await db.execute("INSERT INTO sects(sect_name,updated_at) VALUES(?,?) ON CONFLICT(sect_name) DO NOTHING",(sect_name,time.time()))
            await db.execute("UPDATE inventory SET quantity=quantity-? WHERE user_id=? AND item_id=?",(quantity,user_id,item_id))
            await db.execute(
                """INSERT INTO sect_treasury(sect_name,item_id,quantity) VALUES(?,?,?)
                   ON CONFLICT(sect_name,item_id) DO UPDATE SET quantity=quantity+excluded.quantity""",
                (sect_name,item_id,quantity),
            )
            points=quantity*point_value
            await db.execute(
                "UPDATE sect_membership SET contribution_points=contribution_points+?,influence=influence+? WHERE user_id=?",
                (points,max(1,points//10),user_id),
            )
            await db.execute(
                "UPDATE sect_lineage SET attention=attention+? WHERE disciple_user_id=?",
                (max(1, points // 20), user_id),
            )
            await db.commit(); return points

    async def get_sect_treasury(self,sect_name:str)->dict[str,int]:
        async with self._connect() as db:
            cur=await db.execute("SELECT item_id,quantity FROM sect_treasury WHERE sect_name=? AND quantity>0 ORDER BY item_id",(sect_name,))
            return {str(k):int(v) for k,v in await cur.fetchall()}

    async def sect_redeem(self,user_id:int,item_id:str,quantity:int,point_cost:int)->int:
        quantity=max(1,int(quantity)); cost=max(1,int(point_cost))*quantity
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur=await db.execute("SELECT sect_name,contribution_points FROM sect_membership WHERE user_id=?",(user_id,)); member=await cur.fetchone()
            if not member: raise ValueError("You are not a member of a sect")
            sect_name,points=str(member[0]),int(member[1])
            if points<cost: raise ValueError("Not enough sect contribution points")
            cur=await db.execute("SELECT quantity FROM sect_treasury WHERE sect_name=? AND item_id=?",(sect_name,item_id)); row=await cur.fetchone()
            if not row or int(row[0])<quantity: raise ValueError("The sect treasury does not have enough of that item")
            await db.execute("UPDATE sect_treasury SET quantity=quantity-? WHERE sect_name=? AND item_id=?",(quantity,sect_name,item_id))
            await db.execute("UPDATE sect_membership SET contribution_points=contribution_points-? WHERE user_id=?",(cost,user_id))
            await db.execute(
                """INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,?)
                   ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity""",
                (user_id,item_id,quantity),
            ); await db.commit(); return points-cost

    async def get_sect_manor(self, sect_name: str) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM sect_manors WHERE sect_name=?", (str(sect_name),))
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_member_sect_manor(self, user_id: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT m.*, sm.rank_name AS member_rank_name, sm.rank_level AS member_rank_level,
                          sm.contribution_points AS member_contribution_points
                   FROM sect_membership sm JOIN sect_manors m ON m.sect_name=sm.sect_name
                   WHERE sm.user_id=?""",
                (int(user_id),),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_sect_manor_projects(self, sect_name: str, *, limit: int = 10) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM sect_manor_projects WHERE sect_name=? ORDER BY project_id DESC LIMIT ?",
                (str(sect_name), max(1, min(50, int(limit)))),
            )
            rows = []
            for row in await cur.fetchall():
                data = dict(row)
                try:
                    data["cost"] = json.loads(data.pop("cost_json", "{}") or "{}")
                except json.JSONDecodeError:
                    data["cost"] = {}
                rows.append(data)
            return rows

    async def establish_sect_manor(
        self, user_id: int, *, name: str, base_location: str, game_minute: int
    ) -> dict[str, Any]:
        now = time.time()
        clean_name = " ".join(str(name).strip().split())[:80]
        if len(clean_name) < 3:
            raise ValueError("The sect manor needs a name of at least three characters.")
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT sect_name,rank_name,rank_level FROM sect_membership WHERE user_id=?", (int(user_id),)
            )
            membership = await cur.fetchone()
            if not membership:
                raise ValueError("You are not a member of a sect.")
            sect_name = str(membership["sect_name"])
            if int(membership["rank_level"]) < SECT_MANOR_ESTABLISH_RANK_LEVEL:
                raise ValueError("Only a Sect Master or Ancestor can establish the sect manor.")
            cur = await db.execute("SELECT 1 FROM sect_manors WHERE sect_name=?", (sect_name,))
            if await cur.fetchone():
                raise ValueError("Your sect already has a persistent manor.")
            cur = await db.execute(
                "SELECT item_id,quantity FROM sect_treasury WHERE sect_name=?", (sect_name,)
            )
            owned = {str(item): int(qty) for item, qty in await cur.fetchall()}
            missing = {
                item: qty - owned.get(item, 0)
                for item, qty in SECT_MANOR_ESTABLISHMENT_COST.items()
                if owned.get(item, 0) < qty
            }
            if missing:
                pretty = ", ".join(f"{item} x{qty}" for item, qty in missing.items())
                raise ValueError(f"The sect treasury is missing foundation materials: {pretty}")
            for item_id, qty in SECT_MANOR_ESTABLISHMENT_COST.items():
                await db.execute(
                    "UPDATE sect_treasury SET quantity=quantity-? WHERE sect_name=? AND item_id=?",
                    (int(qty), sect_name, str(item_id)),
                )
            await db.execute(
                """INSERT INTO sect_manors(
                       sect_name,name,base_location,founded_by_user_id,created_game_minute,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (sect_name, clean_name, str(base_location), int(user_id), int(game_minute), now, now),
            )
            await db.execute(
                """INSERT INTO sect_manor_projects(
                       sect_name,user_id,project_type,facility_key,from_level,to_level,cost_json,game_minute,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (sect_name, int(user_id), "establish", "foundation", 0, 0,
                 json.dumps(SECT_MANOR_ESTABLISHMENT_COST, sort_keys=True), int(game_minute), now),
            )
            await db.commit()
        manor = await self.get_sect_manor(sect_name)
        if manor is None:
            raise RuntimeError("Sect manor establishment committed without a readable manor row.")
        return manor

    async def upgrade_sect_manor_facility(
        self, user_id: int, facility: str, *, game_minute: int
    ) -> dict[str, Any]:
        facility = str(facility)
        definition = SECT_MANOR_FACILITIES.get(facility)
        if definition is None:
            raise ValueError("Unknown sect-manor facility.")
        column = str(definition["column"])
        now = time.time()
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT sect_name,rank_name,rank_level FROM sect_membership WHERE user_id=?", (int(user_id),)
            )
            membership = await cur.fetchone()
            if not membership:
                raise ValueError("You are not a member of a sect.")
            if int(membership["rank_level"]) < SECT_MANOR_UPGRADE_RANK_LEVEL:
                raise ValueError("Only an Elder or higher-ranked sect member can direct manor construction.")
            sect_name = str(membership["sect_name"])
            cur = await db.execute("SELECT * FROM sect_manors WHERE sect_name=?", (sect_name,))
            manor = await cur.fetchone()
            if not manor:
                raise ValueError("Your sect has not established a manor yet.")
            current_level = int(manor[column])
            if current_level >= MAX_MANOR_FACILITY_LEVEL:
                raise ValueError("That sect-manor facility is already at the maximum level.")
            cost = manor_upgrade_cost(facility, current_level)
            cur = await db.execute(
                "SELECT item_id,quantity FROM sect_treasury WHERE sect_name=?", (sect_name,)
            )
            owned = {str(item): int(qty) for item, qty in await cur.fetchall()}
            missing = {
                item: qty - owned.get(item, 0) for item, qty in cost.items() if owned.get(item, 0) < qty
            }
            if missing:
                pretty = ", ".join(f"{item} x{qty}" for item, qty in missing.items())
                raise ValueError(f"The sect treasury is missing upgrade materials: {pretty}")
            for item_id, qty in cost.items():
                await db.execute(
                    "UPDATE sect_treasury SET quantity=quantity-? WHERE sect_name=? AND item_id=?",
                    (int(qty), sect_name, str(item_id)),
                )
            target_level = current_level + 1
            await db.execute(
                f"UPDATE sect_manors SET {column}=?,updated_at=? WHERE sect_name=?",
                (target_level, now, sect_name),
            )
            await db.execute(
                """INSERT INTO sect_manor_projects(
                       sect_name,user_id,project_type,facility_key,from_level,to_level,cost_json,game_minute,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (sect_name, int(user_id), "upgrade", facility, current_level, target_level,
                 json.dumps(cost, sort_keys=True), int(game_minute), now),
            )
            await db.commit()
        updated = await self.get_sect_manor(sect_name)
        if updated is None:
            raise RuntimeError("Sect manor upgrade committed without a readable manor row.")
        updated["last_upgrade_cost"] = cost
        updated["last_upgraded_facility"] = facility
        return updated

    async def set_sect_rank(self,user_id:int,rank_name:str,rank_level:int)->None:
        async with self._connect() as db:
            await db.execute("UPDATE sect_membership SET rank_name=?,rank_level=? WHERE user_id=?",(rank_name,int(rank_level),user_id)); await db.commit()

    async def adjust_master_attention(self,disciple_user_id:int,amount:int)->int:
        async with self._connect() as db:
            await db.execute("UPDATE sect_lineage SET attention=MAX(0,attention+?) WHERE disciple_user_id=?",(int(amount),disciple_user_id))
            cur=await db.execute("SELECT attention FROM sect_lineage WHERE disciple_user_id=?",(disciple_user_id,)); row=await cur.fetchone(); await db.commit(); return int(row[0]) if row else 0

    # ------------------------------------------------------------------
    # Fate, player discipleship, deployable arrays, black markets, realm hubs
    # ------------------------------------------------------------------
    async def get_fate(self, user_id: int) -> dict[str, Any]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM character_fate WHERE user_id=?", (int(user_id),))
            row = await cur.fetchone()
            if row:
                return dict(row)
            return {"user_id": int(user_id), "points": 0, "lifetime_earned": 0, "lifetime_spent": 0}

    async def adjust_fate(self, user_id: int, delta: int, *, reason: str = "", game_minute: int = 0) -> int:
        delta = int(delta)
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute("SELECT points,lifetime_earned,lifetime_spent FROM character_fate WHERE user_id=?", (int(user_id),))
            row = await cur.fetchone()
            current = int(row[0]) if row else 0
            earned = int(row[1]) if row else 0
            spent = int(row[2]) if row else 0
            target = max(0, min(9, current + delta))
            applied = target - current
            if applied > 0:
                earned += applied
            elif applied < 0:
                spent += -applied
            await db.execute(
                """INSERT INTO character_fate(user_id,points,lifetime_earned,lifetime_spent,updated_at)
                   VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
                   points=excluded.points,lifetime_earned=excluded.lifetime_earned,
                   lifetime_spent=excluded.lifetime_spent,updated_at=excluded.updated_at""",
                (int(user_id), target, earned, spent, now),
            )
            if applied:
                await db.execute(
                    "INSERT INTO fate_ledger(user_id,delta,balance_after,reason,game_minute,created_at) VALUES(?,?,?,?,?,?)",
                    (int(user_id), applied, target, str(reason)[:300], int(game_minute), now),
                )
            await db.commit()
            return target

    async def spend_fate(self, user_id: int, amount: int = 1, *, reason: str = "", game_minute: int = 0) -> int:
        amount = max(1, int(amount))
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute("SELECT points,lifetime_earned,lifetime_spent FROM character_fate WHERE user_id=?", (int(user_id),))
            row = await cur.fetchone()
            current = int(row[0]) if row else 0
            if current < amount:
                await db.rollback()
                raise ValueError("Not enough Fate")
            earned = int(row[1]) if row else 0
            spent = int(row[2]) if row else 0
            target = current - amount
            await db.execute(
                """INSERT INTO character_fate(user_id,points,lifetime_earned,lifetime_spent,updated_at)
                   VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
                   points=excluded.points,lifetime_earned=excluded.lifetime_earned,
                   lifetime_spent=excluded.lifetime_spent,updated_at=excluded.updated_at""",
                (int(user_id), target, earned, spent + amount, now),
            )
            await db.execute(
                "INSERT INTO fate_ledger(user_id,delta,balance_after,reason,game_minute,created_at) VALUES(?,?,?,?,?,?)",
                (int(user_id), -amount, target, str(reason)[:300], int(game_minute), now),
            )
            await db.commit()
            return target

    async def get_fate_ledger(self, user_id: int, *, limit: int = 10) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM fate_ledger WHERE user_id=? ORDER BY entry_id DESC LIMIT ?",
                (int(user_id), max(1, min(50, int(limit)))),
            )
            return [dict(row) for row in await cur.fetchall()]

    async def create_disciple_request(self, disciple_user_id: int, master_user_id: int) -> dict[str, Any]:
        disciple_user_id, master_user_id = int(disciple_user_id), int(master_user_id)
        if disciple_user_id == master_user_id:
            raise ValueError("A cultivator cannot request themselves as master.")
        now = time.time()
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT user_id,name,realm_index,phase,life_status FROM characters WHERE user_id IN (?,?)",
                (disciple_user_id, master_user_id),
            )
            chars = {int(row["user_id"]): dict(row) for row in await cur.fetchall()}
            if len(chars) != 2:
                await db.rollback(); raise ValueError("Both cultivators must have characters.")
            if any(str(chars[uid].get("life_status", "alive")) != "alive" for uid in chars):
                await db.rollback(); raise ValueError("Master-disciple contracts require living incarnations.")
            cur = await db.execute("SELECT 1 FROM sect_lineage WHERE disciple_user_id=?", (disciple_user_id,))
            if await cur.fetchone():
                await db.rollback(); raise ValueError("You already have a recorded master.")
            d_power = int(chars[disciple_user_id]["realm_index"]) * 10 + int(chars[disciple_user_id]["phase"])
            m_power = int(chars[master_user_id]["realm_index"]) * 10 + int(chars[master_user_id]["phase"])
            if m_power <= d_power:
                await db.rollback(); raise ValueError("A requested master must have a higher cultivation stage than the disciple.")
            cur = await db.execute(
                "SELECT user_id,sect_name FROM sect_membership WHERE user_id IN (?,?)",
                (disciple_user_id, master_user_id),
            )
            memberships = {int(r[0]): str(r[1]) for r in await cur.fetchall()}
            if len(memberships) == 2 and memberships[disciple_user_id] != memberships[master_user_id]:
                await db.rollback(); raise ValueError("Master and disciple must belong to the same sect when both have sect memberships.")
            await db.execute(
                "UPDATE disciple_requests SET status='superseded',resolved_at=? WHERE disciple_user_id=? AND status='pending'",
                (now, disciple_user_id),
            )
            cur = await db.execute(
                "INSERT INTO disciple_requests(disciple_user_id,master_user_id,status,created_at) VALUES(?,?,'pending',?)",
                (disciple_user_id, master_user_id, now),
            )
            request_id = int(cur.lastrowid)
            await db.commit()
            return {"request_id": request_id, "disciple_user_id": disciple_user_id, "master_user_id": master_user_id, "status": "pending"}

    async def get_disciple_requests(self, user_id: int, *, incoming: bool = True, status: str = "pending") -> list[dict[str, Any]]:
        field = "dr.master_user_id" if incoming else "dr.disciple_user_id"
        other_join = "dr.disciple_user_id" if incoming else "dr.master_user_id"
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"""SELECT dr.*, c.name AS other_name,c.realm_index AS other_realm_index,c.phase AS other_phase
                    FROM disciple_requests dr JOIN characters c ON c.user_id={other_join}
                    WHERE {field}=? AND dr.status=? ORDER BY dr.request_id DESC""",
                (int(user_id), str(status)),
            )
            return [dict(row) for row in await cur.fetchall()]

    async def resolve_disciple_request(self, master_user_id: int, request_id: int, *, accept: bool) -> dict[str, Any]:
        master_user_id, request_id = int(master_user_id), int(request_id)
        now = time.time()
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute("SELECT * FROM disciple_requests WHERE request_id=? AND status='pending'", (request_id,))
            req = await cur.fetchone()
            if not req:
                await db.rollback(); raise ValueError("That pending disciple request does not exist.")
            if int(req["master_user_id"]) != master_user_id:
                await db.rollback(); raise ValueError("Only the requested master can resolve this request.")
            disciple_user_id = int(req["disciple_user_id"])
            if not accept:
                await db.execute("UPDATE disciple_requests SET status='rejected',resolved_at=? WHERE request_id=?", (now, request_id))
                await db.commit()
                return {**dict(req), "status": "rejected"}
            cur = await db.execute("SELECT 1 FROM sect_lineage WHERE disciple_user_id=?", (disciple_user_id,))
            if await cur.fetchone():
                await db.rollback(); raise ValueError("That cultivator already has a recorded master.")
            cur = await db.execute(
                "SELECT user_id,realm_index,phase,life_status FROM characters WHERE user_id IN (?,?)",
                (disciple_user_id, master_user_id),
            )
            chars = {int(r[0]): {"realm_index": int(r[1]), "phase": int(r[2]), "life_status": str(r[3])} for r in await cur.fetchall()}
            if len(chars) != 2 or any(v["life_status"] != "alive" for v in chars.values()):
                await db.rollback(); raise ValueError("Both master and disciple must still be living incarnations.")
            d_power = chars[disciple_user_id]["realm_index"] * 10 + chars[disciple_user_id]["phase"]
            m_power = chars[master_user_id]["realm_index"] * 10 + chars[master_user_id]["phase"]
            if m_power <= d_power:
                await db.rollback(); raise ValueError("The requested master must still have a higher cultivation stage.")
            # Prevent direct/indirect lineage cycles before writing the accepted contract.
            cursor = master_user_id
            seen = {disciple_user_id}
            for _ in range(64):
                if cursor in seen:
                    await db.rollback(); raise ValueError("That contract would create a lineage cycle.")
                seen.add(cursor)
                cur = await db.execute("SELECT master_user_id FROM sect_lineage WHERE disciple_user_id=?", (cursor,))
                row = await cur.fetchone()
                if not row:
                    break
                cursor = int(row[0])
            cur = await db.execute(
                "SELECT user_id,sect_name FROM sect_membership WHERE user_id IN (?,?)", (disciple_user_id, master_user_id)
            )
            memberships = {int(r[0]): str(r[1]) for r in await cur.fetchall()}
            if len(memberships) == 2 and memberships[disciple_user_id] != memberships[master_user_id]:
                await db.rollback(); raise ValueError("Master and disciple are no longer members of the same sect.")
            await db.execute(
                "INSERT INTO sect_lineage(disciple_user_id,master_user_id,accepted_at,attention) VALUES(?,?,?,0)",
                (disciple_user_id, master_user_id, now),
            )
            await db.execute("UPDATE disciple_requests SET status='accepted',resolved_at=? WHERE request_id=?", (now, request_id))
            await db.execute(
                "UPDATE disciple_requests SET status='superseded',resolved_at=? WHERE disciple_user_id=? AND status='pending' AND request_id<>?",
                (now, disciple_user_id, request_id),
            )
            await db.commit()
            return {**dict(req), "status": "accepted"}

    async def reward_master_for_disciple_breakthrough(self, disciple_user_id: int, *, realm_changed: bool) -> dict[str, Any] | None:
        attention = 5 if realm_changed else 2
        contribution = 4 if realm_changed else 1
        influence = 2 if realm_changed else 0
        insight_xp = 8 if realm_changed else 3
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                """SELECT sl.master_user_id,c.name FROM sect_lineage sl
                   JOIN characters c ON c.user_id=sl.master_user_id WHERE sl.disciple_user_id=?""",
                (int(disciple_user_id),),
            )
            row = await cur.fetchone()
            if not row:
                await db.rollback(); return None
            master_id = int(row["master_user_id"])
            await db.execute("UPDATE sect_lineage SET attention=attention+? WHERE disciple_user_id=?", (attention, int(disciple_user_id)))
            await db.execute(
                "UPDATE sect_membership SET contribution_points=contribution_points+?,influence=influence+? WHERE user_id=?",
                (contribution, influence, master_id),
            )
            await db.execute("UPDATE characters SET insight_xp=insight_xp+?,updated_at=? WHERE user_id=?", (insight_xp, time.time(), master_id))
            await db.commit()
            return {"master_user_id": master_id, "master_name": str(row["name"]), "attention": attention, "contribution": contribution, "influence": influence, "insight_xp": insight_xp}

    async def get_dao_partnership(self, user_id: int, *, active_only: bool = False) -> dict[str, Any] | None:
        sql = """SELECT p.*, ca.name AS user_a_name, cb.name AS user_b_name
                 FROM dao_partnerships p JOIN characters ca ON ca.user_id=p.user_a JOIN characters cb ON cb.user_id=p.user_b
                 WHERE (p.user_a=? OR p.user_b=?)"""
        params: list[Any] = [int(user_id), int(user_id)]
        if active_only:
            sql += " AND p.status='active'"
        else:
            sql += " AND p.status IN ('pending','active')"
        sql += " ORDER BY p.partnership_id DESC LIMIT 1"
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row
            cur=await db.execute(sql,tuple(params)); row=await cur.fetchone()
            if not row:return None
            data=dict(row); other=int(data['user_b']) if int(data['user_a'])==int(user_id) else int(data['user_a'])
            data['partner_user_id']=other
            data['partner_name']=str(data['user_b_name'] if int(data['user_a'])==int(user_id) else data['user_a_name'])
            return data

    async def create_dao_partnership_request(self, requester_user_id:int, partner_user_id:int) -> dict[str,Any]:
        requester_user_id,partner_user_id=int(requester_user_id),int(partner_user_id)
        if requester_user_id==partner_user_id: raise ValueError("You cannot form a Dao partnership with yourself.")
        a,b=sorted((requester_user_id,partner_user_id)); now=time.time()
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; await db.execute("BEGIN IMMEDIATE")
            cur=await db.execute("SELECT user_id,life_status FROM characters WHERE user_id IN (?,?)",(a,b)); chars=await cur.fetchall()
            if len(chars)!=2 or any(str(r['life_status'])!='alive' for r in chars): await db.rollback(); raise ValueError("Both cultivators must be living incarnations.")
            cur=await db.execute("SELECT 1 FROM dao_partnerships WHERE status IN ('pending','active') AND (user_a IN (?,?) OR user_b IN (?,?)) LIMIT 1",(a,b,a,b))
            if await cur.fetchone(): await db.rollback(); raise ValueError("One of you already has a pending or active Dao partnership.")
            await db.execute("""INSERT INTO dao_partnerships(user_a,user_b,requested_by,status,resonance,dual_sessions,created_at,updated_at)
                                VALUES(?,?,?,'pending',0,0,?,?)
                                ON CONFLICT(user_a,user_b) DO UPDATE SET requested_by=excluded.requested_by,status='pending',resonance=0,dual_sessions=0,created_at=excluded.created_at,updated_at=excluded.updated_at""",(a,b,requester_user_id,now,now))
            cur=await db.execute("SELECT * FROM dao_partnerships WHERE user_a=? AND user_b=?",(a,b)); row=await cur.fetchone(); await db.commit(); return dict(row)

    async def resolve_dao_partnership(self, responder_user_id:int, partnership_id:int, *, accept:bool) -> dict[str,Any]:
        responder_user_id,partnership_id=int(responder_user_id),int(partnership_id); now=time.time()
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; await db.execute("BEGIN IMMEDIATE")
            cur=await db.execute("SELECT * FROM dao_partnerships WHERE partnership_id=? AND status='pending'",(partnership_id,)); row=await cur.fetchone()
            if not row: await db.rollback(); raise ValueError("That pending Dao-partnership proposal does not exist.")
            if responder_user_id not in {int(row['user_a']),int(row['user_b'])} or responder_user_id==int(row['requested_by']):
                await db.rollback(); raise ValueError("Only the invited cultivator can answer this proposal.")
            status='active' if accept else 'rejected'
            await db.execute("UPDATE dao_partnerships SET status=?,updated_at=? WHERE partnership_id=?",(status,now,partnership_id)); await db.commit()
            result = {**dict(row),'status':status}
        if accept:
            a = await self.get_character(int(result['user_a'])) or {}
            b = await self.get_character(int(result['user_b'])) or {}
            clock = await self.get_world_clock()
            await self.record_world_history_event(
                event_type="alliance", title=f"Dao partnership: {a.get('name','Cultivator')} and {b.get('name','Cultivator')}",
                summary=f"{a.get('name','One cultivator')} and {b.get('name','another cultivator')} formally entered an active Dao partnership.",
                significance=72, visibility="public", location=str(a.get('location') or b.get('location') or ''),
                actor_type="player", actor_key=str(int(result['user_a'])), actor_name=str(a.get('name') or ''),
                target_type="player", target_key=str(int(result['user_b'])), target_name=str(b.get('name') or ''),
                related_user_id=int(responder_user_id), tags=("alliance","dao partnership","relationship"),
                game_minute=int(clock.get('game_minute',0)), metadata={"partnership_id": int(partnership_id)},
                source_key=f"dao_partnership:{int(partnership_id)}:active",
            )
        return result

    async def sever_dao_partnership(self, user_id:int) -> dict[str,Any] | None:
        now=time.time()
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; await db.execute("BEGIN IMMEDIATE")
            cur=await db.execute("SELECT * FROM dao_partnerships WHERE status='active' AND (user_a=? OR user_b=?) LIMIT 1",(int(user_id),int(user_id))); row=await cur.fetchone()
            if not row: await db.rollback(); return None
            await db.execute("UPDATE dao_partnerships SET status='severed',updated_at=? WHERE partnership_id=?",(now,int(row['partnership_id']))); await db.commit(); result = dict(row)
        a = await self.get_character(int(result['user_a'])) or {}
        b = await self.get_character(int(result['user_b'])) or {}
        clock = await self.get_world_clock()
        await self.record_world_history_event(
            event_type="alliance_ended", title=f"Dao partnership severed: {a.get('name','Cultivator')} and {b.get('name','Cultivator')}",
            summary=f"The Dao partnership between {a.get('name','one cultivator')} and {b.get('name','another cultivator')} was formally severed.",
            significance=58, visibility="public", location=str(a.get('location') or b.get('location') or ''),
            actor_type="player", actor_key=str(int(user_id)), actor_name=str((a if int(a.get('user_id',0))==int(user_id) else b).get('name') or ''),
            target_type="partnership", target_key=str(int(result['partnership_id'])), target_name="Dao partnership",
            related_user_id=int(user_id), tags=("alliance","dao partnership","severed"),
            game_minute=int(clock.get('game_minute',0)), metadata={"partnership_id": int(result['partnership_id'])},
            source_key=f"dao_partnership:{int(result['partnership_id'])}:severed",
        )
        return result

    async def record_dual_cultivation(self, user_id:int, *, cultivation_caps:dict[int,int], cooldown_seconds:int=1800) -> dict[str,Any]:
        user_id=int(user_id); now=time.time()
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; await db.execute("BEGIN IMMEDIATE")
            cur=await db.execute("SELECT * FROM dao_partnerships WHERE status='active' AND (user_a=? OR user_b=?) LIMIT 1",(user_id,user_id)); p=await cur.fetchone()
            if not p: await db.rollback(); raise ValueError("You have no active Dao partnership.")
            a,b=int(p['user_a']),int(p['user_b']); partner=b if a==user_id else a
            cur=await db.execute("SELECT user_id,name,location,life_status,cultivation,attributes_json FROM characters WHERE user_id IN (?,?)",(a,b)); chars={int(r['user_id']):dict(r) for r in await cur.fetchall()}
            if len(chars)!=2 or any(str(chars[x].get('life_status'))!='alive' for x in (a,b)): await db.rollback(); raise ValueError("Both Dao partners must be living incarnations.")
            if str(chars[a]['location'])!=str(chars[b]['location']): await db.rollback(); raise ValueError("Dual cultivation requires both Dao partners at the same canonical location.")
            cur=await db.execute("SELECT 1 FROM battles WHERE status='active' AND user_id IN (?,?) LIMIT 1",(a,b))
            if await cur.fetchone(): await db.rollback(); raise ValueError("Dual cultivation cannot begin while either partner is in battle.")
            cur=await db.execute("SELECT user_id,available_at FROM cooldowns WHERE action='dao_dual_cultivation' AND user_id IN (?,?)",(a,b)); cds={int(r[0]):float(r[1]) for r in await cur.fetchall()}
            wait=max([0.0]+[cds.get(x,0.0)-now for x in (a,b)])
            if wait>0: await db.rollback(); raise ValueError(f"The paired meridian cycle needs another {int(wait)+1} seconds before it can be repeated.")
            attrs={x:json.loads(chars[x]['attributes_json']) for x in (a,b)}
            base=4+min(int(attrs[a].get('spirit',0))+int(attrs[a].get('will',0)),int(attrs[b].get('spirit',0))+int(attrs[b].get('will',0)))//4
            old_res=max(0,min(100,int(p['resonance']))); gain=max(3,base+old_res//25); new_res=min(100,old_res+4); milestone=(new_res//25)>(old_res//25)
            awarded={}
            for uid in (a,b):
                current=int(chars[uid]['cultivation']); cap=max(current,int(cultivation_caps.get(uid,current+gain))); actual=min(gain,max(0,cap-current)); awarded[uid]=actual
                await db.execute("UPDATE characters SET cultivation=cultivation+?,updated_at=? WHERE user_id=?",(actual,now,uid))
                await db.execute("""INSERT INTO cooldowns(user_id,action,available_at) VALUES(?,?,?) ON CONFLICT(user_id,action) DO UPDATE SET available_at=excluded.available_at""",(uid,'dao_dual_cultivation',now+max(60,int(cooldown_seconds))))
            await db.execute("UPDATE dao_partnerships SET resonance=?,dual_sessions=dual_sessions+1,updated_at=? WHERE partnership_id=?",(new_res,now,int(p['partnership_id'])))
            await db.commit()
            return {'partnership_id':int(p['partnership_id']),'partner_user_id':partner,'partner_name':str(chars[partner]['name']),'location':str(chars[a]['location']),'resonance':new_res,'previous_resonance':old_res,'milestone':milestone,'awarded':awarded}

    async def deploy_location_array(
        self, user_id: int, *, location: str, item_id: str, name: str, effect: dict[str, Any],
        starts_game_minute: int, duration_game_minutes: int,
    ) -> dict[str, Any]:
        now = time.time()
        starts = int(starts_game_minute)
        ends = starts + max(1, int(duration_game_minutes))
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute("SELECT quantity FROM inventory WHERE user_id=? AND item_id=?", (int(user_id), str(item_id)))
            row = await cur.fetchone()
            if not row or int(row[0]) <= 0:
                await db.rollback(); raise ValueError("You no longer carry that formation disk.")
            cur = await db.execute("SELECT * FROM deployed_location_arrays WHERE location=? AND ends_game_minute>?", (str(location), starts))
            active = await cur.fetchone()
            if active:
                await db.rollback(); raise ValueError(f"{active['name']} is already active at this location until game-minute {active['ends_game_minute']}.")
            cur = await db.execute("SELECT sect_name FROM sect_membership WHERE user_id=?", (int(user_id),))
            membership = await cur.fetchone()
            sect_name = str(membership[0]) if membership else ""
            await db.execute("UPDATE inventory SET quantity=quantity-1 WHERE user_id=? AND item_id=?", (int(user_id), str(item_id)))
            await db.execute(
                """INSERT INTO deployed_location_arrays(location,item_id,name,owner_user_id,sect_name,effect_json,starts_game_minute,ends_game_minute,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(location) DO UPDATE SET item_id=excluded.item_id,name=excluded.name,owner_user_id=excluded.owner_user_id,
                   sect_name=excluded.sect_name,effect_json=excluded.effect_json,starts_game_minute=excluded.starts_game_minute,
                   ends_game_minute=excluded.ends_game_minute,created_at=excluded.created_at,updated_at=excluded.updated_at""",
                (str(location), str(item_id), str(name), int(user_id), sect_name, json.dumps(effect), starts, ends, now, now),
            )
            await db.commit()
            return {"location": str(location), "item_id": str(item_id), "name": str(name), "owner_user_id": int(user_id), "sect_name": sect_name, "effect": dict(effect), "starts_game_minute": starts, "ends_game_minute": ends}

    async def get_active_location_array(self, location: str, game_minute: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM deployed_location_arrays WHERE location=? AND starts_game_minute<=? AND ends_game_minute>?",
                (str(location), int(game_minute), int(game_minute)),
            )
            row = await cur.fetchone()
            if not row:
                return None
            data = dict(row)
            try:
                data["effect"] = json.loads(data.pop("effect_json") or "{}")
            except Exception:
                data["effect"] = {}
            return data

    async def rotate_black_market(
        self, *, world_name: str, location: str, heat: int, opens_game_minute: int,
        closes_game_minute: int, stock: list[dict[str, Any]],
    ) -> None:
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """INSERT INTO black_market_posts(world_name,location,heat,opens_game_minute,closes_game_minute,active,created_at,updated_at)
                   VALUES(?,?,?,?,?,1,?,?) ON CONFLICT(world_name) DO UPDATE SET
                   location=excluded.location,heat=excluded.heat,opens_game_minute=excluded.opens_game_minute,
                   closes_game_minute=excluded.closes_game_minute,active=1,updated_at=excluded.updated_at""",
                (str(world_name), str(location), max(0, min(100, int(heat))), int(opens_game_minute), int(closes_game_minute), now, now),
            )
            await db.execute("DELETE FROM black_market_stock WHERE world_name=?", (str(world_name),))
            for row in stock:
                await db.execute(
                    """INSERT INTO black_market_stock(world_name,item_id,currency_id,unit_price,quantity,legal_status,updated_at)
                       VALUES(?,?,?,?,?,?,?)""",
                    (str(world_name), str(row["item_id"]), str(row["currency_id"]), max(1, int(row["unit_price"])), max(0, int(row["quantity"])), str(row.get("legal_status", "forbidden")), now),
                )
            await db.commit()

    async def get_active_black_market(self, location: str, game_minute: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM black_market_posts WHERE location=? AND active=1 AND opens_game_minute<=? AND closes_game_minute>?",
                (str(location), int(game_minute), int(game_minute)),
            )
            post = await cur.fetchone()
            if not post:
                return None
            data = dict(post)
            cur = await db.execute("SELECT * FROM black_market_stock WHERE world_name=? AND quantity>0 ORDER BY unit_price DESC", (str(data["world_name"]),))
            data["stock"] = [dict(row) for row in await cur.fetchall()]
            return data

    async def list_active_black_markets(self, game_minute: int) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM black_market_posts WHERE active=1 AND opens_game_minute<=? AND closes_game_minute>? ORDER BY world_name",
                (int(game_minute), int(game_minute)),
            )
            return [dict(row) for row in await cur.fetchall()]

    async def black_market_trade(self, *, user_id: int, location: str, item_id: str, quantity: int, buy: bool, game_minute: int) -> dict[str, Any]:
        quantity = max(1, min(20, int(quantity)))
        now = time.time()
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT * FROM black_market_posts WHERE location=? AND active=1 AND opens_game_minute<=? AND closes_game_minute>?",
                (str(location), int(game_minute), int(game_minute)),
            )
            post = await cur.fetchone()
            if not post:
                await db.rollback(); raise ValueError("No black-market trading post is active at this location.")
            world_name = str(post["world_name"])
            cur = await db.execute("SELECT * FROM black_market_stock WHERE world_name=? AND item_id=?", (world_name, str(item_id)))
            stock = await cur.fetchone()
            if not stock:
                await db.rollback(); raise ValueError("That item is not being traded by this underworld post.")
            currency = str(stock["currency_id"])
            unit_price = max(1, int(stock["unit_price"]))
            if buy:
                if int(stock["quantity"]) < quantity:
                    await db.rollback(); raise ValueError("The broker does not have that many remaining.")
                total = unit_price * quantity
                balance = await self._wallet_delta(db, int(user_id), currency, -total)
                await db.execute("UPDATE black_market_stock SET quantity=quantity-?,updated_at=? WHERE world_name=? AND item_id=?", (quantity, now, world_name, str(item_id)))
                await db.execute(
                    """INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,?)
                       ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity""",
                    (int(user_id), str(item_id), quantity),
                )
                await db.execute(
                    """INSERT INTO item_provenance(user_id,item_id,quantity,source_type,source_key,ownership_mark,legal_status,authenticity,tracking_strength,acquired_game_minute,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (int(user_id), str(item_id), quantity, "black_market", world_name, "underworld broker", str(stock["legal_status"]), 100, max(5, int(post["heat"]) // 4), int(game_minute), now, now),
                )
                await db.commit()
                return {"buy": True, "item_id": str(item_id), "quantity": quantity, "currency_id": currency, "unit_price": unit_price, "total": total, "balance": balance, "heat": int(post["heat"])}
            cur = await db.execute("SELECT quantity FROM inventory WHERE user_id=? AND item_id=?", (int(user_id), str(item_id)))
            inv = await cur.fetchone()
            if not inv or int(inv[0]) < quantity:
                await db.rollback(); raise ValueError("You do not carry enough of that item.")
            sale_unit = max(1, int(round(unit_price * 0.55)))
            total = sale_unit * quantity
            await db.execute("UPDATE inventory SET quantity=quantity-? WHERE user_id=? AND item_id=?", (quantity, int(user_id), str(item_id)))
            await db.execute("UPDATE black_market_stock SET quantity=quantity+?,updated_at=? WHERE world_name=? AND item_id=?", (quantity, now, world_name, str(item_id)))
            balance = await self._wallet_delta(db, int(user_id), currency, total)
            await db.commit()
            return {"buy": False, "item_id": str(item_id), "quantity": quantity, "currency_id": currency, "unit_price": sale_unit, "total": total, "balance": balance, "heat": int(post["heat"])}

    async def set_realm_hub_channel(self, *, guild_id: int, world_name: str, location: str, channel_id: int, category_id: int | None) -> None:
        now = time.time()
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO realm_hub_channels(guild_id,world_name,location,channel_id,category_id,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?) ON CONFLICT(guild_id,world_name) DO UPDATE SET
                   location=excluded.location,channel_id=excluded.channel_id,category_id=excluded.category_id,updated_at=excluded.updated_at""",
                (int(guild_id), str(world_name), str(location), int(channel_id), int(category_id) if category_id else None, now, now),
            )
            await db.commit()

    async def get_realm_hub_channels(self, guild_id: int) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM realm_hub_channels WHERE guild_id=? ORDER BY world_name", (int(guild_id),))
            return [dict(row) for row in await cur.fetchall()]

    async def get_realm_hub_by_channel(self, guild_id: int, channel_id: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM realm_hub_channels WHERE guild_id=? AND channel_id=?", (int(guild_id), int(channel_id)))
            row = await cur.fetchone()
            return dict(row) if row else None

    async def claim_periodic_bucket(self, key: str, bucket: int) -> bool:
        state_key = f"periodic:{str(key)}"
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute("SELECT value_json FROM world_state WHERE key=?", (state_key,))
            row = await cur.fetchone()
            previous = None
            if row:
                try:
                    previous = int(json.loads(row[0]).get("bucket", -1))
                except Exception:
                    previous = -1
            if previous == int(bucket):
                await db.rollback(); return False
            await db.execute(
                """INSERT INTO world_state(key,value_json,updated_at) VALUES(?,?,?)
                   ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
                (state_key, json.dumps({"bucket": int(bucket)}), now),
            )
            await db.commit(); return True

    # ------------------------------------------------------------------
    # Auction house with escrow bidding
    # ------------------------------------------------------------------
    async def create_auction(
        self,*,house_id:str,seller_user_id:int,item_id:str,quantity:int,currency_id:str,
        starting_bid:int,anonymous:bool,ends_at:float
    )->int:
        quantity=max(1,int(quantity)); starting_bid=max(1,int(starting_bid)); now=time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur=await db.execute("SELECT quantity FROM inventory WHERE user_id=? AND item_id=?",(seller_user_id,item_id)); row=await cur.fetchone()
            if not row or int(row[0])<quantity: raise ValueError("Not enough of that item to list")
            await db.execute("UPDATE inventory SET quantity=quantity-? WHERE user_id=? AND item_id=?",(quantity,seller_user_id,item_id))
            cur=await db.execute(
                """INSERT INTO auctions(house_id,seller_user_id,item_id,quantity,currency_id,starting_bid,current_bid,anonymous,active,created_at,ends_at)
                   VALUES(?,?,?,?,?,?,0,?,1,?,?)""",
                (house_id,seller_user_id,item_id,quantity,currency_id,starting_bid,1 if anonymous else 0,now,float(ends_at)),
            ); auction_id=int(cur.lastrowid); await db.commit(); return auction_id

    async def list_active_auctions(self,house_id:str|None=None)->list[dict[str,Any]]:
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row
            if house_id:
                cur=await db.execute("SELECT * FROM auctions WHERE active=1 AND house_id=? ORDER BY ends_at",(house_id,))
            else:
                cur=await db.execute("SELECT * FROM auctions WHERE active=1 ORDER BY ends_at")
            return [dict(r) for r in await cur.fetchall()]

    async def place_bid(self,auction_id:int,bidder_user_id:int,amount:int)->dict[str,Any]:
        amount=max(1,int(amount)); now=time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE"); db.row_factory=aiosqlite.Row
            cur=await db.execute("SELECT * FROM auctions WHERE auction_id=?",(int(auction_id),)); row=await cur.fetchone()
            if not row: raise ValueError("Auction not found")
            auction=dict(row)
            if not auction["active"] or float(auction["ends_at"])<=now: raise ValueError("That auction has ended")
            if int(auction["seller_user_id"])==bidder_user_id: raise ValueError("You cannot bid on your own lot")
            minimum=max(int(auction["starting_bid"]),int(auction["current_bid"])+1)
            if amount<minimum: raise ValueError(f"Minimum bid is {minimum}")
            old_bidder=auction.get("current_bidder_user_id"); old_amount=int(auction.get("current_bid") or 0)
            if old_bidder:
                await self._wallet_delta(db,int(old_bidder),str(auction["currency_id"]),old_amount)
            await self._wallet_delta(db,bidder_user_id,str(auction["currency_id"]),-amount)
            await db.execute("UPDATE auctions SET current_bid=?,current_bidder_user_id=? WHERE auction_id=?",(amount,bidder_user_id,int(auction_id)))
            await db.execute("INSERT INTO auction_bids(auction_id,bidder_user_id,amount,created_at) VALUES(?,?,?,?)",(int(auction_id),bidder_user_id,amount,now))
            await db.commit(); auction.update({"current_bid":amount,"current_bidder_user_id":bidder_user_id}); return auction

    async def finalize_expired_auctions(self)->list[dict[str,Any]]:
        now=time.time(); finalized=[]
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE"); db.row_factory=aiosqlite.Row
            cur=await db.execute("SELECT * FROM auctions WHERE active=1 AND ends_at<=? ORDER BY ends_at",(now,)); rows=await cur.fetchall()
            for row in rows:
                a=dict(row); winner=a.get("current_bidder_user_id")
                if winner:
                    await db.execute(
                        """INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,?)
                           ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity""",
                        (int(winner),str(a["item_id"]),int(a["quantity"])),
                    )
                    await self._wallet_delta(db,int(a["seller_user_id"]),str(a["currency_id"]),int(a["current_bid"]))
                else:
                    await db.execute(
                        """INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,?)
                           ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity""",
                        (int(a["seller_user_id"]),str(a["item_id"]),int(a["quantity"])),
                    )
                await db.execute("UPDATE auctions SET active=0 WHERE auction_id=?",(int(a["auction_id"]),))
                finalized.append(a)
            await db.commit(); return finalized

    # ------------------------------------------------------------------
    # Auction door risks, temporary battles, and player families
    # ------------------------------------------------------------------
    async def add_auction_door_risk(self, *, user_id:int, auction_id:int, item_id:str, risk_level:str, chance_percent:int)->None:
        async with self._connect() as db:
            await db.execute("""INSERT INTO auction_door_risks(user_id,auction_id,item_id,risk_level,chance_percent,created_at,consumed_at) VALUES(?,?,?,?,?,?,NULL) ON CONFLICT(user_id,auction_id) DO UPDATE SET item_id=excluded.item_id,risk_level=excluded.risk_level,chance_percent=excluded.chance_percent,created_at=excluded.created_at,consumed_at=NULL""",(user_id,int(auction_id),item_id,risk_level,max(0,min(100,int(chance_percent))),time.time())); await db.commit()

    async def get_pending_auction_door_risks(self,user_id:int,max_age_seconds:int=86400)->list[dict[str,Any]]:
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; cur=await db.execute("SELECT * FROM auction_door_risks WHERE user_id=? AND consumed_at IS NULL AND created_at>=? ORDER BY chance_percent DESC,created_at DESC",(user_id,time.time()-max(60,int(max_age_seconds)))); return [dict(r) for r in await cur.fetchall()]

    async def consume_auction_door_risk(self,risk_id:int)->None:
        async with self._connect() as db:
            await db.execute("UPDATE auction_door_risks SET consumed_at=? WHERE risk_id=? AND consumed_at IS NULL",(time.time(),int(risk_id))); await db.commit()

    async def create_battle(
        self, *, user_id:int, npc_name:str, npc_realm_index:int, npc_stage:int,
        player_hp:int, npc_hp:int, location:str, source:str, target_key:str="",
        player_hp_max:int|None=None,
    )->int:
        now=time.time(); player=max(1,int(player_hp)); player_max=max(player,int(player_hp_max or player)); opponent=max(1,int(npc_hp)); target=str(target_key).strip()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            if target:
                cur=await db.execute(
                    "SELECT battle_id FROM battles WHERE target_key=? AND status='active' AND user_id<>? LIMIT 1",
                    (target,int(user_id)),
                )
                if await cur.fetchone():
                    await db.rollback()
                    raise ValueError("That opponent is already locked in an unresolved battle")
            await db.execute(
                "UPDATE battles SET status='abandoned',version=version+1,updated_at=? WHERE user_id=? AND status='active'",
                (now,user_id),
            )
            try:
                cur=await db.execute(
                    """INSERT INTO battles(
                           user_id,npc_name,npc_realm_index,npc_stage,player_hp,player_hp_max,npc_hp,npc_hp_max,
                           status,location,source,target_key,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?, 'active',?,?,?,?,?)""",
                    (user_id,npc_name,int(npc_realm_index),int(npc_stage),player,player_max,opponent,opponent,location,source,target,now,now),
                )
            except (aiosqlite.IntegrityError, sqlite3.IntegrityError) as exc:
                await db.rollback()
                raise ValueError("That opponent is already locked in an unresolved battle") from exc
            bid=int(cur.lastrowid); await db.commit(); return bid

    async def get_active_battle(self,user_id:int)->dict[str,Any]|None:
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; cur=await db.execute("SELECT * FROM battles WHERE user_id=? AND status='active' ORDER BY battle_id DESC LIMIT 1",(user_id,)); row=await cur.fetchone(); return dict(row) if row else None

    async def get_battle(self,battle_id:int,*,user_id:int|None=None,active_only:bool=False)->dict[str,Any]|None:
        clauses=["battle_id=?"]; vals:[Any]=[int(battle_id)]
        if user_id is not None: clauses.append("user_id=?"); vals.append(int(user_id))
        if active_only: clauses.append("status='active'")
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row
            cur=await db.execute(f"SELECT * FROM battles WHERE {' AND '.join(clauses)} LIMIT 1",tuple(vals))
            row=await cur.fetchone(); return dict(row) if row else None

    async def update_battle(self,battle_id:int,*,player_hp:int|None=None,npc_hp:int|None=None,status:str|None=None,thread_id:int|None=None,npc_suppressed_turns:int|None=None)->None:
        fields=["updated_at=?","version=version+1"]; vals:[Any]=[time.time()]
        if player_hp is not None: fields.append("player_hp=?"); vals.append(max(0,int(player_hp)))
        if npc_hp is not None: fields.append("npc_hp=?"); vals.append(max(0,int(npc_hp)))
        if status is not None: fields.append("status=?"); vals.append(status)
        if thread_id is not None: fields.append("thread_id=?"); vals.append(int(thread_id))
        if npc_suppressed_turns is not None: fields.append("npc_suppressed_turns=?"); vals.append(max(0,int(npc_suppressed_turns)))
        vals.append(int(battle_id))
        async with self._connect() as db:
            await db.execute(f"UPDATE battles SET {', '.join(fields)} WHERE battle_id=?",tuple(vals)); await db.commit()

    async def claim_battle_finalization(self,user_id:int,battle_id:int,outcome:str)->dict[str,Any]|None:
        choice=str(outcome).lower()
        if choice not in {"spare","kill"}: raise ValueError("Outcome must be spare or kill")
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; await db.execute("BEGIN IMMEDIATE")
            cur=await db.execute(
                "SELECT * FROM battles WHERE battle_id=? AND user_id=? AND status='active' AND npc_hp<=0",
                (int(battle_id),int(user_id)),
            )
            row=await cur.fetchone()
            if not row:
                await db.rollback(); return None
            now=time.time()
            cur=await db.execute(
                """UPDATE battles SET status='won',final_outcome=?,finalized_at=?,version=version+1,updated_at=?
                   WHERE battle_id=? AND user_id=? AND status='active' AND npc_hp<=0""",
                (choice,now,now,int(battle_id),int(user_id)),
            )
            if int(cur.rowcount or 0)!=1:
                await db.rollback(); return None
            await db.commit(); result=dict(row); result["status"]="won"; result["final_outcome"]=choice; result["finalized_at"]=now; return result

    async def create_player_family(self,founder_user_id:int,name:str)->int:
        family_name=name.strip()
        if not family_name or len(family_name)>60: raise ValueError("Family name must be 1-60 characters")
        now=time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE"); cur=await db.execute("SELECT 1 FROM characters WHERE user_id=?",(founder_user_id,))
            if not await cur.fetchone(): raise ValueError("Create a cultivation character first")
            cur=await db.execute("SELECT 1 FROM player_family_members WHERE user_id=?",(founder_user_id,))
            if await cur.fetchone(): raise ValueError("You already belong to a player family")
            try: cur=await db.execute("INSERT INTO player_families(name,founder_user_id,created_at) VALUES(?,?,?)",(family_name,founder_user_id,now))
            except aiosqlite.IntegrityError as exc: raise ValueError("That family name is already in use") from exc
            fid=int(cur.lastrowid); await db.execute("INSERT INTO player_family_members(family_id,user_id,seniority_order,joined_at) VALUES(?,?,1,?)",(fid,founder_user_id,now)); await db.commit(); return fid

    async def get_player_family(self,user_id:int)->dict[str,Any]|None:
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; cur=await db.execute("SELECT f.* FROM player_families f JOIN player_family_members m ON m.family_id=f.family_id WHERE m.user_id=?",(user_id,)); row=await cur.fetchone()
            if not row: return None
            data=dict(row); cur=await db.execute("SELECT m.user_id,m.seniority_order,m.joined_at,c.name,c.address_style,c.gender,c.realm_index,c.phase FROM player_family_members m JOIN characters c ON c.user_id=m.user_id WHERE m.family_id=? ORDER BY m.seniority_order",(int(data['family_id']),)); data['members']=[dict(r) for r in await cur.fetchall()]; return data

    async def invite_player_family_member(self,inviter_user_id:int,invitee_user_id:int,requested_order:int)->dict[str,Any]:
        if inviter_user_id==invitee_user_id: raise ValueError("You cannot invite yourself")
        family=await self.get_player_family(inviter_user_id)
        if not family: raise ValueError("You do not belong to a player family")
        now=time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE"); cur=await db.execute("SELECT 1 FROM characters WHERE user_id=?",(invitee_user_id,))
            if not await cur.fetchone(): raise ValueError("That member has no cultivation character")
            cur=await db.execute("SELECT 1 FROM player_family_members WHERE user_id=?",(invitee_user_id,))
            if await cur.fetchone(): raise ValueError("That cultivator already belongs to a player family")
            order=max(1,min(int(requested_order),len(family['members'])+1)); await db.execute("DELETE FROM player_family_invites WHERE invitee_user_id=?",(invitee_user_id,)); await db.execute("INSERT INTO player_family_invites(family_id,inviter_user_id,invitee_user_id,requested_order,created_at,expires_at) VALUES(?,?,?,?,?,?)",(int(family['family_id']),inviter_user_id,invitee_user_id,order,now,now+86400)); await db.commit(); return {'family_id':int(family['family_id']),'family_name':family['name'],'requested_order':order}

    async def accept_player_family_invite(self,invitee_user_id:int)->dict[str,Any]:
        now=time.time()
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; await db.execute("BEGIN IMMEDIATE"); cur=await db.execute("SELECT i.*,f.name AS family_name FROM player_family_invites i JOIN player_families f ON f.family_id=i.family_id WHERE i.invitee_user_id=? AND i.expires_at>?",(invitee_user_id,now)); row=await cur.fetchone()
            if not row: raise ValueError("You have no active family invitation")
            inv=dict(row); cur=await db.execute("SELECT 1 FROM player_family_members WHERE user_id=?",(invitee_user_id,))
            if await cur.fetchone(): raise ValueError("You already belong to a player family")
            fid=int(inv['family_id']); order=int(inv['requested_order']); await db.execute("UPDATE player_family_members SET seniority_order=seniority_order+1000 WHERE family_id=? AND seniority_order>=?",(fid,order)); await db.execute("UPDATE player_family_members SET seniority_order=seniority_order-999 WHERE family_id=? AND seniority_order>=?",(fid,order+1000)); await db.execute("INSERT INTO player_family_members(family_id,user_id,seniority_order,joined_at) VALUES(?,?,?,?)",(fid,invitee_user_id,order,now)); await db.execute("DELETE FROM player_family_invites WHERE invitee_user_id=?",(invitee_user_id,)); await db.commit(); return {'family_id':fid,'family_name':inv['family_name'],'seniority_order':order}

    async def decline_player_family_invite(self,invitee_user_id:int)->bool:
        async with self._connect() as db:
            cur=await db.execute("DELETE FROM player_family_invites WHERE invitee_user_id=?",(invitee_user_id,)); await db.commit(); return int(cur.rowcount or 0)>0

    async def reorder_player_family_member(self,actor_user_id:int,member_user_id:int,new_order:int)->None:
        family=await self.get_player_family(actor_user_id)
        if not family: raise ValueError("You do not belong to a player family")
        if int(family['founder_user_id'])!=actor_user_id: raise ValueError("Only the family founder can change household seniority")
        members=list(family['members']); target=next((m for m in members if int(m['user_id'])==member_user_id),None)
        if not target: raise ValueError("That cultivator is not in your player family")
        ids=[int(m['user_id']) for m in members if int(m['user_id'])!=member_user_id]; pos=max(1,min(int(new_order),len(members))); ids.insert(pos-1,member_user_id)
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE"); await db.execute("UPDATE player_family_members SET seniority_order=seniority_order+1000 WHERE family_id=?",(int(family['family_id']),))
            for idx,uid in enumerate(ids,1): await db.execute("UPDATE player_family_members SET seniority_order=? WHERE family_id=? AND user_id=?",(idx,int(family['family_id']),uid))
            await db.commit()

    async def leave_player_family(self,user_id:int)->str|None:
        family=await self.get_player_family(user_id)
        if not family: return None
        fid=int(family['family_id']); remaining=[m for m in family['members'] if int(m['user_id'])!=user_id]
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE"); await db.execute("DELETE FROM player_family_members WHERE family_id=? AND user_id=?",(fid,user_id))
            if not remaining: await db.execute("DELETE FROM player_families WHERE family_id=?",(fid,)); await db.commit(); return str(family['name'])
            if int(family['founder_user_id'])==user_id:
                nf=min(remaining,key=lambda m:int(m['seniority_order'])); await db.execute("UPDATE player_families SET founder_user_id=? WHERE family_id=?",(int(nf['user_id']),fid))
            await db.execute("UPDATE player_family_members SET seniority_order=seniority_order+1000 WHERE family_id=?",(fid,))
            for idx,m in enumerate(sorted(remaining,key=lambda x:int(x['seniority_order'])),1): await db.execute("UPDATE player_family_members SET seniority_order=? WHERE family_id=? AND user_id=?",(idx,fid,int(m['user_id'])))
            await db.commit(); return str(family['name'])

    # ------------------------------------------------------------------
    # Lifespan / descendants
    # ------------------------------------------------------------------
    async def set_life_status(self, user_id:int, status:str)->None:
        async with self._connect() as db:
            await db.execute("UPDATE characters SET life_status=?,updated_at=? WHERE user_id=?",(status,time.time(),user_id)); await db.commit()

    async def add_life_extension(self,user_id:int,years:int)->int:
        years=max(0,int(years))
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute("UPDATE characters SET life_extension_years=life_extension_years+?,updated_at=? WHERE user_id=?",(years,time.time(),user_id))
            cur=await db.execute("SELECT life_extension_years FROM characters WHERE user_id=?",(user_id,)); row=await cur.fetchone(); await db.commit(); return int(row[0]) if row else 0

    async def create_family_child(self,*,family_id:int,parent_user_id:int,name:str,gender:str,birth_game_minute:int,spiritual_root:str,cultivation_potential:int,can_cultivate:bool,natural_lifespan_years:int)->int:
        clean=name.strip()[:40]
        if not clean: raise ValueError("Child name is required")
        gender=gender if gender in {"male","female","neutral"} else "neutral"
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur=await db.execute("SELECT 1 FROM player_family_members WHERE family_id=? AND user_id=?",(int(family_id),parent_user_id))
            if not await cur.fetchone(): raise ValueError("You are not a member of that family")
            cur=await db.execute("INSERT INTO family_children(family_id,parent_user_id,name,gender,birth_game_minute,spiritual_root,cultivation_potential,can_cultivate,awakening_state,natural_lifespan_years,status,created_at) VALUES(?,?,?,?,?,?,?,?,0,?,'alive',?)",(int(family_id),parent_user_id,clean,gender,int(birth_game_minute),spiritual_root,max(0,min(100,int(cultivation_potential))),1 if can_cultivate else 0,max(1,int(natural_lifespan_years)),time.time()))
            child_id=int(cur.lastrowid); await db.commit(); return child_id

    async def get_family_children(self,family_id:int)->list[dict[str,Any]]:
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; cur=await db.execute("SELECT fc.*,c.name AS parent_name FROM family_children fc JOIN characters c ON c.user_id=fc.parent_user_id WHERE fc.family_id=? ORDER BY fc.birth_game_minute,fc.child_id",(int(family_id),)); return [dict(r) for r in await cur.fetchall()]

    async def set_child_awakening(self,child_id:int,state:int)->None:
        async with self._connect() as db:
            await db.execute("UPDATE family_children SET awakening_state=? WHERE child_id=?",(int(state),int(child_id))); await db.commit()

    async def set_abode_thread(self,user_id:int,*,thread_id:int,thread_channel_id:int)->None:
        async with self._connect() as db:
            await db.execute("UPDATE cave_abodes SET thread_id=?,thread_channel_id=?,updated_at=? WHERE user_id=?",(int(thread_id),int(thread_channel_id),time.time(),user_id)); await db.commit()

    # ------------------------------------------------------------------
    # Laws / Daos / Cave Abodes / Personal Worlds
    # ------------------------------------------------------------------
    async def get_law_progress(self, user_id:int, law_id:str|None=None)->list[dict[str,Any]]:
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row
            if law_id:
                cur=await db.execute("SELECT * FROM law_progress WHERE user_id=? AND law_id=?",(user_id,law_id))
            else:
                cur=await db.execute("SELECT * FROM law_progress WHERE user_id=? ORDER BY comprehension DESC,law_id",(user_id,))
            return [dict(r) for r in await cur.fetchall()]

    async def add_law_comprehension(self,user_id:int,law_id:str,amount:int,dao_id:str)->dict[str,int]:
        now=time.time(); amount=max(0,int(amount))
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute("""INSERT INTO law_progress(user_id,law_id,comprehension,insights,updated_at) VALUES(?,?,?,?,?)
                ON CONFLICT(user_id,law_id) DO UPDATE SET comprehension=MIN(100,law_progress.comprehension+excluded.comprehension),insights=law_progress.insights+1,updated_at=excluded.updated_at""",(user_id,law_id,amount,1,now))
            cur=await db.execute("SELECT comprehension,insights FROM law_progress WHERE user_id=? AND law_id=?",(user_id,law_id)); row=await cur.fetchone()
            dao_gain=max(1,amount//4)
            await db.execute("""INSERT INTO dao_progress(user_id,dao_id,progress,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(user_id,dao_id) DO UPDATE SET progress=MIN(100,dao_progress.progress+excluded.progress),updated_at=excluded.updated_at""",(user_id,dao_id,dao_gain,now))
            await db.commit(); return {'comprehension':int(row[0]),'insights':int(row[1]),'dao_gain':dao_gain}

    async def get_dao_progress(self,user_id:int)->list[dict[str,Any]]:
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; cur=await db.execute("SELECT * FROM dao_progress WHERE user_id=? ORDER BY progress DESC,dao_id",(user_id,)); return [dict(r) for r in await cur.fetchall()]

    async def spend_currency(self,user_id:int,currency_id:str,amount:int)->int:
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE"); new=await self._wallet_delta(db,user_id,currency_id,-max(1,int(amount))); await db.commit(); return new

    async def establish_abode(
        self, user_id:int, name:str, base_location:str, *, property_type:str="cave_abode",
        facility_levels:dict[str,int]|None=None,
    )->dict[str,Any]:
        now=time.time(); clean=name.strip()[:60]
        if not clean: raise ValueError("Property name is required")
        key=f"abode:{user_id}"
        levels={
            "cultivation":1, "alchemy":0, "forge":0, "formation":0, "defense":0,
            "storage":1, "herb_garden":0, "beast_pen":0, "merchant":0,
        }
        if facility_levels:
            for facility,value in facility_levels.items():
                if facility in levels:
                    levels[facility]=max(0,min(9,int(value)))
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE"); cur=await db.execute("SELECT 1 FROM cave_abodes WHERE user_id=?",(user_id,))
            if await cur.fetchone(): raise ValueError("You already own a player property")
            await db.execute(
                """INSERT INTO cave_abodes(
                       user_id,location_key,name,base_location,property_type,
                       cultivation_level,alchemy_level,forge_level,formation_level,defense_level,
                       storage_level,herb_garden_level,beast_pen_level,merchant_level,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    user_id,key,clean,base_location,str(property_type or "cave_abode")[:40],
                    levels["cultivation"],levels["alchemy"],levels["forge"],levels["formation"],levels["defense"],
                    levels["storage"],levels["herb_garden"],levels["beast_pen"],levels["merchant"],now,now,
                ),
            ); await db.commit()
        return await self.get_abode(user_id)

    async def get_abode(self,owner_user_id:int)->dict[str,Any]|None:
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; cur=await db.execute("SELECT * FROM cave_abodes WHERE user_id=?",(owner_user_id,)); row=await cur.fetchone(); return dict(row) if row else None

    async def get_abode_by_location(self,location_key:str)->dict[str,Any]|None:
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; cur=await db.execute("SELECT * FROM cave_abodes WHERE location_key=?",(location_key,)); row=await cur.fetchone(); return dict(row) if row else None

    async def can_access_abode(self,owner_user_id:int,user_id:int)->bool:
        if owner_user_id==user_id: return True
        async with self._connect() as db:
            cur=await db.execute("SELECT 1 FROM cave_abode_access WHERE owner_user_id=? AND guest_user_id=?",(owner_user_id,user_id)); return bool(await cur.fetchone())

    async def get_abode_by_thread(self,thread_id:int)->dict[str,Any]|None:
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row
            cur=await db.execute("SELECT * FROM cave_abodes WHERE thread_id=?",(int(thread_id),))
            row=await cur.fetchone(); return dict(row) if row else None

    async def grant_abode_access(self,owner_user_id:int,guest_user_id:int)->None:
        now=time.time()
        async with self._connect() as db:
            await db.execute("INSERT INTO cave_abode_access(owner_user_id,guest_user_id,access_role,created_at) VALUES(?,?, 'guest',?) ON CONFLICT(owner_user_id,guest_user_id) DO NOTHING",(owner_user_id,guest_user_id,now)); await db.commit()

    async def revoke_abode_access(self,owner_user_id:int,guest_user_id:int)->None:
        async with self._connect() as db:
            await db.execute("DELETE FROM cave_abode_access WHERE owner_user_id=? AND guest_user_id=?",(owner_user_id,guest_user_id)); await db.commit()

    async def get_abode_guests(self,owner_user_id:int)->list[dict[str,Any]]:
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row
            cur=await db.execute(
                """SELECT a.owner_user_id,a.guest_user_id,a.access_role,a.created_at,c.name AS character_name
                   FROM cave_abode_access a
                   LEFT JOIN characters c ON c.user_id=a.guest_user_id
                   WHERE a.owner_user_id=? ORDER BY a.created_at,a.guest_user_id""",
                (owner_user_id,),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def upgrade_abode_facility(self,user_id:int,facility:str)->dict[str,Any]:
        col={
            'cultivation':'cultivation_level','alchemy':'alchemy_level','forge':'forge_level',
            'formation':'formation_level','defense':'defense_level','storage':'storage_level',
            'herb_garden':'herb_garden_level','beast_pen':'beast_pen_level','merchant':'merchant_level',
        }.get(facility)
        if not col: raise ValueError("Unknown player-property facility")
        async with self._connect() as db:
            await db.execute(f"UPDATE cave_abodes SET {col}=MIN(9,{col}+1),updated_at=? WHERE user_id=?",(time.time(),user_id)); await db.commit()
        return await self.get_abode(user_id)

    async def create_personal_world(self,user_id:int,name:str)->dict[str,Any]:
        now=time.time(); clean=name.strip()[:60]
        if not clean: raise ValueError("World name is required")
        key=f"personal_world:{user_id}"
        async with self._connect() as db:
            await db.execute("INSERT INTO personal_worlds(user_id,location_key,name,created_at,updated_at) VALUES(?,?,?,?,?)",(user_id,key,clean,now,now)); await db.commit()
        return await self.get_personal_world(user_id)

    async def get_personal_world(self,user_id:int)->dict[str,Any]|None:
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; cur=await db.execute("SELECT * FROM personal_worlds WHERE user_id=?",(user_id,)); row=await cur.fetchone()
            if not row:return None
            d=dict(row); d['laws']=json.loads(d.pop('laws_json')); return d

    async def get_personal_world_by_location(self,location_key:str)->dict[str,Any]|None:
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; cur=await db.execute("SELECT * FROM personal_worlds WHERE location_key=?",(location_key,)); row=await cur.fetchone()
            if not row:return None
            d=dict(row); d['laws']=json.loads(d.pop('laws_json')); return d

    async def set_personal_world_rule(self,user_id:int,rule_name:str,rule_text:str)->dict[str,Any]:
        world=await self.get_personal_world(user_id)
        if not world: raise ValueError("You have not created a personal world")
        rules=dict(world.get('laws',{})); rules[rule_name.strip()[:40]]=rule_text.strip()[:300]
        async with self._connect() as db:
            await db.execute("UPDATE personal_worlds SET laws_json=?,stability=MIN(100,stability+1),updated_at=? WHERE user_id=?",(json.dumps(rules),time.time(),user_id)); await db.commit()
        return await self.get_personal_world(user_id)

    # ------------------------------------------------------------------
    # Background cultivation / closed-door seclusion
    # ------------------------------------------------------------------
    async def get_seclusion(self, user_id: int, *, active_only: bool = True) -> dict[str, Any] | None:
        sql = "SELECT * FROM seclusion_sessions WHERE user_id=?"
        args: tuple[Any, ...] = (int(user_id),)
        if active_only:
            sql += " AND status='active'"
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(sql, args)
            row = await cur.fetchone()
            return dict(row) if row else None

    async def list_active_seclusions(self) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM seclusion_sessions WHERE status='active' ORDER BY ends_game_minute")
            return [dict(r) for r in await cur.fetchall()]

    async def start_seclusion(
        self,
        user_id: int,
        *,
        mode: str,
        current_game_minute: int,
        duration_game_minutes: int,
        location: str,
        environment_mult: float = 1.0,
    ) -> dict[str, Any]:
        mode = str(mode).strip().lower()
        if mode not in {"qi", "body"}:
            raise ValueError("Seclusion mode must be qi or body")
        duration_game_minutes = max(1, int(duration_game_minutes))
        now = time.time()
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute("SELECT life_status FROM characters WHERE user_id=?", (int(user_id),))
            c = await cur.fetchone()
            if not c:
                await db.rollback(); raise ValueError("Create a cultivation character first")
            if str(c[0]) != "alive":
                await db.rollback(); raise ValueError("A deceased incarnation cannot enter seclusion")
            cur = await db.execute("SELECT 1 FROM battles WHERE user_id=? AND status='active' LIMIT 1", (int(user_id),))
            if await cur.fetchone():
                await db.rollback(); raise ValueError("You cannot enter seclusion during an active battle")
            cur = await db.execute("SELECT status FROM seclusion_sessions WHERE user_id=?", (int(user_id),))
            row = await cur.fetchone()
            if row and str(row[0]) == "active":
                await db.rollback(); raise ValueError("You are already in seclusion")
            end_minute = int(current_game_minute) + duration_game_minutes
            await db.execute(
                """INSERT INTO seclusion_sessions(
                       user_id,mode,started_game_minute,ends_game_minute,last_settled_game_minute,start_location,
                       environment_mult,accumulated_gain,status,ended_reason,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,0,'active','',?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       mode=excluded.mode,started_game_minute=excluded.started_game_minute,ends_game_minute=excluded.ends_game_minute,
                       last_settled_game_minute=excluded.last_settled_game_minute,start_location=excluded.start_location,
                       environment_mult=excluded.environment_mult,accumulated_gain=0,status='active',ended_reason='',
                       created_at=excluded.created_at,updated_at=excluded.updated_at""",
                (int(user_id), mode, int(current_game_minute), end_minute, int(current_game_minute), str(location), float(environment_mult), now, now),
            )
            await db.execute(
                "INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                (int(user_id), "seclusion_started", json.dumps({"mode": mode, "location": location, "ends_game_minute": end_minute}), now),
            )
            await db.commit()
        state = await self.get_seclusion(int(user_id), active_only=False)
        assert state is not None
        return state

    async def advance_seclusion(
        self,
        user_id: int,
        *,
        settled_game_minute: int,
        awarded_gain: int,
        completed: bool = False,
        ended_reason: str = "completed",
    ) -> dict[str, Any] | None:
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute("SELECT status,last_settled_game_minute FROM seclusion_sessions WHERE user_id=?", (int(user_id),))
            row = await cur.fetchone()
            if not row or str(row[0]) != "active":
                await db.rollback(); return await self.get_seclusion(int(user_id), active_only=False)
            settled = max(int(row[1]), int(settled_game_minute))
            status = "completed" if completed else "active"
            reason = str(ended_reason)[:120] if completed else ""
            await db.execute(
                """UPDATE seclusion_sessions
                   SET last_settled_game_minute=?,accumulated_gain=accumulated_gain+?,status=?,ended_reason=?,updated_at=?
                   WHERE user_id=?""",
                (settled, max(0, int(awarded_gain)), status, reason, now, int(user_id)),
            )
            if completed:
                await db.execute(
                    "INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                    (int(user_id), "seclusion_ended", json.dumps({"reason": reason, "settled_game_minute": settled}), now),
                )
            await db.commit()
        return await self.get_seclusion(int(user_id), active_only=False)

    async def end_seclusion(self, user_id: int, *, current_game_minute: int, reason: str = "ended early") -> dict[str, Any] | None:
        state = await self.get_seclusion(int(user_id))
        if not state:
            return None
        return await self.advance_seclusion(
            int(user_id), settled_game_minute=min(int(current_game_minute), int(state["ends_game_minute"])),
            awarded_gain=0, completed=True, ended_reason=reason,
        )

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------
    async def maintenance_cleanup(self,game_minute:int)->dict[str,int]:
        now=time.time(); counts={}
        async with self._connect() as db:
            for label,sql,args in (
                ("cooldowns","DELETE FROM cooldowns WHERE available_at<?",(now-86400,)),
                ("effects","DELETE FROM active_effects WHERE ends_game_minute IS NOT NULL AND ends_game_minute<=?",(int(game_minute),)),
                ("event_threads","DELETE FROM event_threads WHERE active=0 AND expires_at<?",(now-30*86400,)),
                ("civilization_events","DELETE FROM civilization_events WHERE event_id NOT IN (SELECT event_id FROM civilization_events ORDER BY event_id DESC LIMIT 2000)",()),
                ("economy_events","DELETE FROM economy_events WHERE event_id NOT IN (SELECT event_id FROM economy_events ORDER BY event_id DESC LIMIT 2000)",()),
                ("sect_politics_events","DELETE FROM sect_politics_events WHERE event_id NOT IN (SELECT event_id FROM sect_politics_events ORDER BY event_id DESC LIMIT 2000)",()),
                ("world_action_events","DELETE FROM world_action_events WHERE action_id NOT IN (SELECT action_id FROM world_action_events ORDER BY action_id DESC LIMIT 5000)",()),
                ("wild_beast_encounters","DELETE FROM wild_beast_encounters WHERE status!='available' AND updated_at<?",(now-30*86400,)),
            ):
                cur=await db.execute(sql,args); counts[label]=max(0,int(cur.rowcount or 0))
            await db.execute("PRAGMA optimize"); await db.commit()
        return counts

    async def vacuum(self) -> None:
        """Compact the authoritative database.

        Production delegates maintenance to the Go engine so Python never opens
        the canonical SQLite file. The local path is retained only for isolated
        tests where no engine URL is configured.
        """
        if self._go_transport is not None:
            await self._go_transport.maintenance("vacuum")
            return

        def _run_vacuum() -> None:
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            try:
                conn.execute("PRAGMA busy_timeout=30000;").close()
                conn.execute("PRAGMA wal_checkpoint(PASSIVE);").fetchall()
                conn.execute("VACUUM").close()
                conn.execute("PRAGMA optimize").fetchall()
            finally:
                conn.close()

        async with self._writer_lock:
            await asyncio.to_thread(_run_vacuum)

    # ------------------------------------------------------------------
    # Birth family, karma, true death, and reincarnation
    # ------------------------------------------------------------------
    async def get_birth_family(self, user_id: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT f.*,m.birth_order,m.generation AS member_generation,m.last_support_game_minute
                   FROM birth_families f JOIN character_birth_family m ON m.family_id=f.family_id
                   WHERE m.user_id=?""", (user_id,)
            )
            row = await cur.fetchone()
            if not row:
                return None
            data = dict(row)
            try:
                data["history"] = json.loads(data.pop("history_json"))
            except Exception:
                data["history"] = []
            cur = await db.execute(
                "SELECT * FROM birth_family_npcs WHERE family_id=? ORDER BY relation,npc_id",
                (int(data["family_id"]),),
            )
            data["npcs"] = [dict(r) for r in await cur.fetchall()]
            return data

    async def simulate_birth_family(self, family_id: int, current_game_minute: int, minutes_per_year: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute("SELECT * FROM birth_families WHERE family_id=?", (int(family_id),))
            row = await cur.fetchone()
            if not row:
                await db.rollback(); return None
            f = dict(row)
            elapsed = max(0, int(current_game_minute) - int(f.get("last_simulated_game_minute", 0)))
            years = min(25, elapsed // max(1, int(minutes_per_year)))
            if years <= 0:
                await db.rollback()
                data = await self.get_birth_family_by_id(int(family_id))
                return data
            wealth, influence, stability = int(f["wealth"]), int(f["influence"]), int(f["stability"])
            purity=max(0,min(100,int(f.get("bloodline_purity",0))))
            branches=max(1,int(f.get("branch_count",1)))
            retainers=max(0,int(f.get("retainer_count",0)))
            history = json.loads(f.get("history_json") or "[]")
            events = (
                ("A successful trade season enriched the household.", 9, 2, 1),
                ("A marriage alliance improved the family's standing.", 2, 8, 6),
                ("A talented junior brought prestige to the family.", 1, 9, 2),
                ("A crop failure and bad contracts drained family stores.", -9, -1, -5),
                ("A feud with a neighboring clan damaged the family's position.", -3, -8, -7),
                ("A beast raid damaged property and frightened retainers.", -8, -2, -9),
                ("A sect elder took interest in one of the family's juniors.", 0, 11, 3),
                ("An internal inheritance dispute split several relatives into factions.", -2, -3, -12),
                ("A quiet year allowed the household to recover and consolidate.", 3, 2, 7),
                ("A scandal surrounding a senior relative harmed the family name.", -1, -10, -4),
            )
            for _ in range(int(years)):
                text, dw, di, ds = events[secrets.randbelow(len(events))]
                wealth = max(0, min(100, wealth + dw))
                influence = max(0, min(100, influence + di))
                stability = max(0, min(100, stability + ds))
                if purity > 0:
                    clan_roll=secrets.randbelow(100)
                    if clan_roll < 8:
                        purity=max(1,purity-1); history.append("The ancestral bloodline thinned slightly across a generation.")
                    elif clan_roll > 94 and stability >= 55:
                        purity=min(100,purity+1); history.append("A gifted marriage and careful lineage rites strengthened the ancestral bloodline.")
                    if secrets.randbelow(100) < 7 and stability >= 45:
                        branches=min(99,branches+1); history.append("A prosperous cadet branch formally established itself within the clan.")
                    if secrets.randbelow(100) < 6 and stability < 35 and branches > 1:
                        branches=max(1,branches-1); history.append("A cadet branch broke away after internal conflict.")
                    retainers=max(0,min(5000,retainers + (1 if influence >= 55 and secrets.randbelow(2)==0 else -1 if stability < 25 and secrets.randbelow(3)==0 else 0)))
                history.append(text)
            score = wealth + influence + stability
            tier = 1 if score < 70 else 2 if score < 125 else 3 if score < 185 else 4 if score < 245 else 5
            old_tier = int(f["tier"])
            if tier > old_tier:
                history.append(f"The family rose from tier {old_tier} to tier {tier} after years of growth.")
            elif tier < old_tier:
                history.append(f"The family declined from tier {old_tier} to tier {tier} after accumulated setbacks.")
            new_anchor = int(f["last_simulated_game_minute"]) + int(years) * int(minutes_per_year)
            await db.execute(
                """UPDATE birth_families SET wealth=?,influence=?,stability=?,tier=?,bloodline_purity=?,branch_count=?,retainer_count=?,last_simulated_game_minute=?,history_json=?,updated_at=? WHERE family_id=?""",
                (wealth,influence,stability,tier,purity,branches,retainers,new_anchor,json.dumps(history[-50:]),time.time(),int(family_id))
            )
            await db.commit()
        return await self.get_birth_family_by_id(int(family_id))

    async def get_birth_family_by_id(self, family_id: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM birth_families WHERE family_id=?", (int(family_id),))
            row = await cur.fetchone()
            if not row: return None
            data=dict(row)
            try: data["history"]=json.loads(data.pop("history_json"))
            except Exception: data["history"]=[]
            cur=await db.execute("SELECT * FROM birth_family_npcs WHERE family_id=? ORDER BY relation,npc_id",(int(family_id),))
            data["npcs"]=[dict(r) for r in await cur.fetchall()]
            return data

    async def claim_birth_family_support(self, user_id: int, current_game_minute: int, cooldown_game_minutes: int) -> dict[str, Any]:
        family = await self.get_birth_family(user_id)
        if not family: raise ValueError("No birth family is recorded for this character")
        last = int(family.get("last_support_game_minute", -999999999))
        remaining = int(cooldown_game_minutes) - max(0, int(current_game_minute) - last)
        if remaining > 0: raise ValueError(f"Your family cannot provide more support yet ({remaining} in-world minutes remaining)")
        archetype=str(family.get("archetype")); tier=max(1,int(family.get("tier",1))); wealth=max(0,int(family.get("wealth",0))); purity=max(0,int(family.get("bloodline_purity",0)))
        # Clan structure is mechanically relevant: loyal branches, retainers and positive alliances
        # improve how much practical support the household can mobilize.
        async with self._connect() as db:
            cur=await db.execute("SELECT COUNT(*) FROM martial_clan_branches WHERE family_id=? AND status='active' AND loyalty>=40",(int(family['family_id']),)); active_branches=int((await cur.fetchone())[0])
            cur=await db.execute("SELECT COALESCE(SUM(members),0) FROM martial_clan_retainers WHERE family_id=? AND status='active' AND loyalty>=35",(int(family['family_id']),)); loyal_retainers=int((await cur.fetchone())[0])
            cur=await db.execute("SELECT COUNT(*) FROM martial_clan_relations WHERE family_id=? AND active=1 AND relation_type IN ('alliance','marriage_pact','trade_pact') AND relation_score>0",(int(family['family_id']),)); helpful_relations=int((await cur.fetchone())[0])
        clan_support_bonus=min(60,active_branches*2+loyal_retainers//8+helpful_relations*4)
        stones=0; items:dict[str,int]={}
        # Every current birth-family start has a distinct
        # support identity.  Unknown/legacy archetypes still fall back safely.
        if archetype=="martial_household":
            stones=6+tier*4+purity//20; items={"recovery_pill":1,"spirit_iron":1}
        elif archetype=="escort_martial_family":
            stones=10+tier*6+wealth//12; items={"recovery_pill":1}
        elif archetype=="weaponsmith_martial_family":
            stones=7+tier*4+wealth//15; items={"spirit_iron":2}
        elif archetype=="body_tempering_family":
            stones=5+tier*4; items={"recovery_pill":1,"heart_calming_pill":1 if tier>=3 else 0}
        elif archetype=="sword_hall_family":
            stones=8+tier*5+purity//25; items={"qi_replenishment_pill":1 if tier>=3 else 0,"spirit_iron":1}
        elif archetype=="spear_guard_family":
            stones=9+tier*5+wealth//18; items={"recovery_pill":1,"formation_flags":1 if tier>=3 else 0}
        elif archetype=="hidden_weapon_family":
            stones=7+tier*5; items={"heart_calming_pill":1,"spirit_herb":1}
        elif archetype=="border_garrison_family":
            stones=9+tier*5+influence//20; items={"recovery_pill":2 if tier>=3 else 1,"spirit_iron":1}
        elif archetype=="fallen_martial_clan":
            stones=5+tier*4+purity//25; items={"qi_pill":1 if tier>=3 else 0,"spirit_herb":1}
        elif archetype=="noble_martial_clan":
            stones=18+tier*9+wealth//10+purity//20; items={"qi_pill":1,"qi_replenishment_pill":1 if tier>=4 else 0}
        elif archetype=="alchemy_family":
            stones=9+tier*5+wealth//16; items={"spirit_herb":3,"recovery_pill":1,"heart_calming_pill":1 if tier>=3 else 0}
        else:
            stones=6+tier*4+wealth//20; items={"recovery_pill":1}
        stones += clan_support_bonus
        if loyal_retainers >= 20 and archetype in {"martial_household","escort_martial_family","border_garrison_family","noble_martial_clan"}:
            items["recovery_pill"] = items.get("recovery_pill",0) + 1
        items={k:v for k,v in items.items() if v>0}
        cost=max(2,stones//8+sum(items.values())*2)
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur=await db.execute("SELECT wealth FROM birth_families WHERE family_id=?",(int(family['family_id']),)); row=await cur.fetchone()
            if not row or int(row[0])<=0: raise ValueError("Your family currently has no spare resources")
            await db.execute("UPDATE birth_families SET wealth=MAX(0,wealth-?),updated_at=? WHERE family_id=?",(cost,time.time(),int(family['family_id'])))
            await db.execute("UPDATE character_birth_family SET last_support_game_minute=? WHERE user_id=?",(int(current_game_minute),user_id))
            if stones:
                await db.execute("UPDATE characters SET spirit_stones=spirit_stones+?,updated_at=? WHERE user_id=?",(stones,time.time(),user_id))
                await db.execute("""INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(?,?,?) ON CONFLICT(user_id,currency_id) DO UPDATE SET balance=balance+excluded.balance""",(user_id,'low_spirit_stone',stones))
            for iid,qty in items.items():
                await db.execute("""INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,?) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity""",(user_id,iid,qty))
            await db.commit()
        return {"stones":stones,"items":items,"family_name":family['family_name'],"cost":cost,"clan_support_bonus":clan_support_bonus,"active_branches":active_branches,"loyal_retainers":loyal_retainers,"helpful_relations":helpful_relations}

    async def add_birth_family_child(self, *, user_id: int, name: str, gender: str, current_game_minute: int, spiritual_root: str, realm_index: int = 0, phase: int = 1) -> int:
        family=await self.get_birth_family(user_id)
        if not family: raise ValueError("No birth family is recorded")
        clean=name.strip()
        if not clean or len(clean)>40: raise ValueError("Child name must be 1-40 characters")
        now=time.time()
        async with self._connect() as db:
            cur=await db.execute("""INSERT INTO birth_family_npcs(family_id,name,relation,gender,age_at_creation,birth_game_minute,natural_lifespan_years,status,spiritual_root,realm_index,phase,personality,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (int(family['family_id']),clean,f"Child of user {user_id}",gender,0,int(current_game_minute),70+secrets.randbelow(11),'alive',spiritual_root,int(realm_index),int(phase),'Born into the player branch of the family',now))
            await db.commit(); return int(cur.lastrowid)

    async def adjust_karma(self, user_id: int, delta: int, *, reason: str = "karma_change") -> int:
        delta=max(-1000,min(1000,int(delta)))
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute("UPDATE characters SET karma_score=MAX(-1000,MIN(1000,karma_score+?)),updated_at=? WHERE user_id=?",(delta,time.time(),user_id))
            cur=await db.execute("SELECT karma_score FROM characters WHERE user_id=?",(user_id,)); row=await cur.fetchone(); score=int(row[0]) if row else 0
            await db.execute("INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",(user_id,'karma_change',json.dumps({'delta':delta,'reason':reason,'score':score}),time.time()))
            await db.commit(); return score

    async def advance_samsara_time(
        self,
        user_id: int,
        *,
        minutes_per_year: int,
        max_wait_seconds: int,
    ) -> dict[str, Any] | None:
        """Advance only the dead soul's compressed Samsara clock.

        The shared world and the old birth family are never fast-forwarded by a
        player's death. The legacy argument names are kept for compatibility.
        """
        state = await self.get_reincarnation_state(user_id)
        if not state:
            return None

        now = time.time()
        target = max(1, int(state.get("family_target_minutes") or 0))
        started = float(state.get("afterlife_started_at") or state.get("created_at") or now)
        ready_at = float(state.get("reincarnation_ready_at") or 0.0)
        if ready_at <= started:
            ready_at = started + max(30, int(max_wait_seconds))

        duration = max(1.0, ready_at - started)
        fraction = max(0.0, min(1.0, (now - started) / duration))
        desired = target if now >= ready_at else int(target * fraction)
        simulated = max(0, int(state.get("family_simulated_minutes") or 0))
        desired = max(simulated, min(target, desired))

        target_world = str(state.get("target_world") or "Mortal World")
        if desired != simulated or str(state.get("rebirth_mode") or "") != "samsara":
            async with self._connect() as db:
                await db.execute(
                    """UPDATE reincarnation_state
                       SET family_simulated_minutes=?,rebirth_mode='samsara',target_world=?,
                           afterlife_started_at=?,reincarnation_ready_at=?
                       WHERE user_id=?""",
                    (desired, target_world, started, ready_at, user_id),
                )
                await db.commit()

        refreshed = await self.get_reincarnation_state(user_id)
        if not refreshed:
            return None
        refreshed["seconds_remaining"] = max(0, int(round(ready_at - now)))
        refreshed["ready"] = bool(now >= ready_at)
        refreshed["samsara_years_elapsed"] = desired / max(1, int(minutes_per_year))
        refreshed["samsara_years_target"] = target / max(1, int(minutes_per_year))
        refreshed["rebirth_mode"] = "samsara"
        refreshed["target_world"] = str(refreshed.get("target_world") or target_world or "Mortal World")
        try:
            refreshed["samsara_history"] = json.loads(refreshed.get("samsara_history_json") or "[]")
        except Exception:
            refreshed["samsara_history"] = []
        previous_family = await self.get_birth_family_by_id(int(refreshed.get("family_id") or 0))
        refreshed["family"] = previous_family
        return refreshed

    async def record_true_death(
        self,
        user_id: int,
        *,
        current_game_minute: int,
        reason: str,
        minutes_per_year: int,
        base_samsara_years: int = 320,
        max_wait_seconds: int = 300,
    ) -> dict[str, Any] | None:
        family = await self.get_birth_family(user_id)
        c = await self.get_character(user_id)
        if not c:
            return None
        if not family:
            return None

        laws = await self.get_law_progress(user_id)
        law_snapshot = {str(row["law_id"]): {"comprehension": int(row.get("comprehension", 0)), "insights": int(row.get("insights", 0))} for row in laws}
        max_law = max((int(row.get("comprehension", 0)) for row in laws), default=0)
        perfect_realms = int(await self.has_completed_perfection(user_id)) + int(await self.has_completed_body_perfection(user_id))
        from ..samsara import choose_samsara_world, reincarnation_scale, samsara_echoes, soul_legacy_profile
        scale = reincarnation_scale(
            c,
            base_samsara_years=max(1, int(base_samsara_years)),
            max_wait_seconds=max_wait_seconds,
            max_law_comprehension=max_law,
        )
        years = int(scale["years"])
        target = years * int(minutes_per_year)
        now = time.time()
        real_wait = int(scale["wait_seconds"])
        ready_at = now + real_wait
        legacy_ready = int(current_game_minute) + target
        previous_realm = max(int(c.get("realm_index", 0)), int(c.get("body_realm_index", 0)))
        target_world = choose_samsara_world(previous_realm, int(c.get("karma_score", 0)))
        echoes = samsara_echoes(
            years=years,
            karma_score=int(c.get("karma_score", 0)),
            previous_realm_index=previous_realm,
        )
        legacy = soul_legacy_profile(
            c,
            max_law_comprehension=max_law,
            karma_score=int(c.get("karma_score", 0)),
            perfect_realms=perfect_realms,
        )
        partnership = await self.get_dao_partnership(user_id, active_only=True)
        partner_echo = min(25, int((partnership or {}).get("resonance", 0)) // 4)
        partner_name = str((partnership or {}).get("partner_name", ""))

        family_id = int(family["family_id"])
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                "UPDATE characters SET life_status='deceased',death_game_minute=?,reincarnation_ready_game_minute=?,true_death_count=true_death_count+1,updated_at=? WHERE user_id=?",
                (int(current_game_minute), legacy_ready, now, user_id),
            )
            await db.execute(
                """INSERT INTO reincarnation_state(
                       user_id,family_id,death_game_minute,ready_game_minute,death_reason,previous_name,
                       previous_generation,karma_at_death,family_target_minutes,family_simulated_minutes,
                       afterlife_started_at,reincarnation_ready_at,rebirth_mode,target_world,samsara_lives_count,
                       samsara_history_json,memory_retention,talent_retention,comprehension_retention,insight_retention,
                       legacy_points,special_trait,karmic_fortune,previous_realm_index,previous_phase,
                       previous_body_realm_index,previous_body_phase,previous_spiritual_root,previous_path,
                       previous_insight_xp,law_snapshot_json,active,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       family_id=excluded.family_id,death_game_minute=excluded.death_game_minute,ready_game_minute=excluded.ready_game_minute,
                       death_reason=excluded.death_reason,previous_name=excluded.previous_name,previous_generation=excluded.previous_generation,
                       karma_at_death=excluded.karma_at_death,family_target_minutes=excluded.family_target_minutes,family_simulated_minutes=0,
                       afterlife_started_at=excluded.afterlife_started_at,reincarnation_ready_at=excluded.reincarnation_ready_at,
                       rebirth_mode='samsara',target_world=excluded.target_world,samsara_lives_count=excluded.samsara_lives_count,
                       samsara_history_json=excluded.samsara_history_json,memory_retention=excluded.memory_retention,
                       talent_retention=excluded.talent_retention,comprehension_retention=excluded.comprehension_retention,
                       insight_retention=excluded.insight_retention,legacy_points=excluded.legacy_points,
                       special_trait=excluded.special_trait,karmic_fortune=excluded.karmic_fortune,
                       previous_realm_index=excluded.previous_realm_index,previous_phase=excluded.previous_phase,
                       previous_body_realm_index=excluded.previous_body_realm_index,previous_body_phase=excluded.previous_body_phase,
                       previous_spiritual_root=excluded.previous_spiritual_root,previous_path=excluded.previous_path,
                       previous_insight_xp=excluded.previous_insight_xp,law_snapshot_json=excluded.law_snapshot_json,
                       active=1,created_at=excluded.created_at""",
                (
                    user_id, family_id, int(current_game_minute), legacy_ready, str(reason), str(c["name"]),
                    int((family or {}).get("member_generation", 1)), int(c.get("karma_score", 0)), target, 0,
                    now, ready_at, "samsara", target_world, int(echoes["total_lives"]), json.dumps(echoes["notable"]),
                    int(legacy["memory_seed"]), int(legacy["talent_echo"]), int(legacy["law_echo"]),
                    int(legacy["insight_echo"]), int(legacy["legacy_points"]), str(legacy.get("special_trait") or ""),
                    int(legacy["karmic_fortune"]), int(c.get("realm_index", 0)), int(c.get("phase", 1)),
                    int(c.get("body_realm_index", 0)), int(c.get("body_phase", 1)),
                    str(c.get("spiritual_root") or "Mortal Root"), str(c.get("path") or ""),
                    int(c.get("insight_xp", 0)), json.dumps(law_snapshot), now,
                ),
            )
            await db.execute(
                "UPDATE reincarnation_state SET partner_echo=?,partner_name=? WHERE user_id=?",
                (partner_echo, partner_name, int(user_id)),
            )
            await db.execute(
                "INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                (
                    user_id,
                    "true_death",
                    json.dumps({
                        "reason": reason, "private_years": years, "reincarnation_ready_at": ready_at,
                        "real_wait_seconds": real_wait, "rebirth_mode": "samsara", "target_world": target_world,
                        "legacy_points": legacy["legacy_points"], "special_trait": legacy.get("special_trait") or "",
                    }),
                    now,
                ),
            )
            await db.commit()
        await self.record_world_history_event(
            event_type="death", title=f"True death of {c.get('name','a cultivator')}",
            summary=(
                f"{c.get('name','A cultivator')} suffered true death at {c.get('location','an unknown place')}. "
                f"Recorded cause: {reason}. The soul entered Samsara toward {target_world}."
            ),
            significance=92, visibility="participant", location=str(c.get("location") or ""),
            actor_type="player", actor_key=str(int(user_id)), actor_name=str(c.get("name") or ""),
            target_type="life", target_key=str(int(user_id)), target_name=str(c.get("name") or ""),
            related_user_id=int(user_id), tags=("death","true death","samsara","reincarnation"),
            game_minute=int(current_game_minute),
            metadata={"reason": reason, "target_world": target_world, "previous_realm_index": previous_realm},
            source_key=f"player_death:{int(user_id)}:{int(current_game_minute)}",
        )
        return await self.get_reincarnation_state(user_id)

    async def get_reincarnation_state(self, user_id: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row
            cur=await db.execute("SELECT * FROM reincarnation_state WHERE user_id=? AND active=1",(user_id,)); row=await cur.fetchone()
            return dict(row) if row else None

    async def get_soul_legacy(self, user_id: int) -> dict[str, Any]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM soul_legacy WHERE user_id=?", (user_id,))
            row = await cur.fetchone()
        if not row:
            return {
                "user_id": user_id, "incarnation_count": 1, "legacy_points": 0,
                "memory_seed": 0, "talent_echo": 0, "law_echo": 0, "insight_echo": 0,
                "karmic_fortune": 0, "special_trait": "", "awakened_memory": 0, "past_lives": [],
            }
        out = dict(row)
        try:
            out["past_lives"] = json.loads(out.get("past_lives_json") or "[]")
        except Exception:
            out["past_lives"] = []
        return out

    async def awaken_soul_memory(self, user_id: int, amount: int = 5) -> dict[str, Any]:
        legacy = await self.get_soul_legacy(user_id)
        if int(legacy.get("memory_seed", 0)) <= 0:
            return legacy
        new_value = min(int(legacy.get("memory_seed", 0)), int(legacy.get("awakened_memory", 0)) + max(1, int(amount)))
        async with self._connect() as db:
            await db.execute(
                "UPDATE soul_legacy SET awakened_memory=?,updated_at=? WHERE user_id=?",
                (new_value, time.time(), user_id),
            )
            await db.commit()
        legacy["awakened_memory"] = new_value
        return legacy

    async def reincarnate_character(
        self,
        *,
        user_id: int,
        name: str,
        gender: str,
        path: str,
        spiritual_root: str,
        attributes: dict[str, int],
        qi_max: int,
        vitality_max: int,
        current_game_minute: int,
        starting_age: int,
        natural_lifespan_years: int,
        new_family_profile: dict[str, Any] | None = None,
        aptitude_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = await self.get_reincarnation_state(user_id)
        if not state:
            raise ValueError("No active Samsara cycle is waiting for this soul")
        ready_at = float(state.get("reincarnation_ready_at") or 0.0)
        if ready_at <= 0:
            raise ValueError("This Samsara record has no valid real-time rebirth deadline")
        if time.time() < ready_at:
            raise ValueError("Your soul has not yet completed its Samsara cycle")
        if not new_family_profile:
            raise ValueError("Samsara requires a fresh birth family in the selected rebirth world")

        old_family = await self.get_birth_family_by_id(int(state.get("family_id") or 0))
        old_legacy = await self.get_soul_legacy(user_id)
        now = time.time()
        fp = dict(new_family_profile or {})
        target_world = str(state.get("target_world") or fp.get("rebirth_world") or "Mortal World")
        fp["location"] = str(fp.get("location") or "Greenriver Town")
        fp["rebirth_world"] = target_world

        memory_seed = max(0, min(100, int(state.get("memory_retention", 0))))
        talent_echo = max(0, min(100, int(state.get("talent_retention", 0))))
        law_echo = max(0, min(100, int(state.get("comprehension_retention", 0))))
        insight_echo = max(0, min(100, int(state.get("insight_retention", 0))))
        legacy_points = max(0, int(state.get("legacy_points", 0)))
        karmic_fortune = max(-100, min(100, int(state.get("karmic_fortune", 0))))
        special_trait = str(state.get("special_trait") or old_legacy.get("special_trait") or "")

        past_lives = list(old_legacy.get("past_lives") or [])
        past_lives.append({
            "name": str(state.get("previous_name") or "Unknown"),
            "family": str((old_family or {}).get("family_name") or "Unknown Family"),
            "realm_index": int(state.get("previous_realm_index", 0)),
            "phase": int(state.get("previous_phase", 1)),
            "body_realm_index": int(state.get("previous_body_realm_index", 0)),
            "body_phase": int(state.get("previous_body_phase", 1)),
            "path": str(state.get("previous_path") or ""),
            "spiritual_root": str(state.get("previous_spiritual_root") or "Mortal Root"),
            "karma": int(state.get("karma_at_death", 0)),
            "death_reason": str(state.get("death_reason") or "Unknown"),
        })
        past_lives = past_lives[-50:]

        incarnation_count = max(1, int(old_legacy.get("incarnation_count", 1))) + 1
        total_legacy_points = max(0, int(old_legacy.get("legacy_points", 0))) + legacy_points
        merged_memory = min(100, max(memory_seed, int(old_legacy.get("memory_seed", 0))) + min(10, incarnation_count // 3))
        merged_talent = min(100, max(talent_echo, int(old_legacy.get("talent_echo", 0))))
        merged_law = min(100, max(law_echo, int(old_legacy.get("law_echo", 0))))
        merged_insight = min(100, max(insight_echo, int(old_legacy.get("insight_echo", 0))))

        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                """INSERT INTO birth_families(
                       family_name,surname,archetype,tier,wealth,influence,stability,alignment_bias,location,
                       head_name,head_gender,head_title,head_realm_index,head_phase,treasury_balance,generation,
                       created_game_minute,last_simulated_game_minute,history_json,line_status,
                       clan_structure,bloodline_name,bloodline_affinity,bloodline_trait,bloodline_purity,branch_count,retainer_count,confederacy_name,
                       created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(fp["family_name"]), str(fp["surname"]), str(fp.get("id", "samsara_family")),
                    int(fp.get("tier", 1)), int(fp.get("wealth", 20)), int(fp.get("influence", 10)),
                    int(fp.get("stability", 60)), int(fp.get("alignment_bias", 0)), str(fp.get("location", "Greenriver Town")),
                    str(fp.get("head_name", "Family Head")), str(fp.get("head_gender", "neutral")),
                    str(fp.get("head_title", "Family Head")), int(fp.get("head_realm_index", 0)), int(fp.get("head_phase", 1)),
                    max(0, int(fp.get("wealth", 20)) * 4), 1, int(current_game_minute), int(current_game_minute),
                    json.dumps([f"Samsara turned, and {name} was born into {fp['family_name']} in the {target_world}."]),
                    "active", str(fp.get("clan_structure", "martial_household")), str(fp.get("bloodline_name", "None")),
                    str(fp.get("bloodline_affinity", "None")), str(fp.get("bloodline_trait", "No awakened ancestral bloodline")),
                    max(0, min(100, int(fp.get("bloodline_purity", 0)))), max(1, int(fp.get("branch_count", 1))),
                    max(0, int(fp.get("retainer_count", 0))), str(fp.get("confederacy_name", "None")), now, now,
                ),
            )
            family_id = int(cur.lastrowid)
            for rel in list(fp.get("relatives", [])):
                age = max(1, int(rel.get("age", 30)))
                await db.execute(
                    """INSERT INTO birth_family_npcs(
                           family_id,name,relation,gender,age_at_creation,birth_game_minute,natural_lifespan_years,status,
                           spiritual_root,realm_index,phase,personality,created_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        family_id, str(rel.get("name", "Relative")), str(rel.get("relation", "Relative")),
                        str(rel.get("gender", "neutral")), age, int(current_game_minute) - age * 518400,
                        int(realm_lifespan_ceiling(int(rel.get("realm_index", 0)), int(rel.get("phase", 1)), 70 + secrets.randbelow(21)) or 2_000_000_000),
                        "alive", "Mortal Root", int(rel.get("realm_index", 0)), int(rel.get("phase", 1)),
                        f"Family member of a {target_world} Samsara rebirth martial household", now,
                    ),
                )
            await db.execute(
                """INSERT INTO character_birth_family(user_id,family_id,birth_order,generation,last_support_game_minute)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET family_id=excluded.family_id,birth_order=excluded.birth_order,
                       generation=excluded.generation,last_support_game_minute=excluded.last_support_game_minute""",
                (user_id, family_id, 1 + secrets.randbelow(4), 1, -999999999),
            )

            await db.execute("DELETE FROM inventory WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM active_effects WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM character_conditions WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM tribulation_state WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM tribulation_attempts WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM profession_progress WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM faction_reputation WHERE user_id=?", (user_id,))
            # Incarnation-scoped advanced combat/world state must not leak into the new body.
            await db.execute("DELETE FROM bounty_hunter_pursuits WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM boss_reward_claims WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM boss_participants WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM formation_positions WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM equipment_instances WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM bounties WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM grudges WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM crime_records WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM character_manuals WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM spirit_beasts WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM artifact_bonds WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM item_provenance WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM character_social_state WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM hidden_sect_membership WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM party_members WHERE user_id=?", (user_id,))
            await db.execute("UPDATE parties SET status='disbanded',updated_at=? WHERE leader_user_id=? AND status='active'", (now, user_id))
            await db.execute("DELETE FROM pvp_challenges WHERE challenger_user_id=? OR target_user_id=?", (user_id, user_id))
            await db.execute("DELETE FROM character_bloodlines WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM character_physiques WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM character_spiritual_roots WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM cooldowns WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM realm_perfection WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM body_realm_perfection WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM law_progress WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM dao_progress WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM inheritances WHERE user_id=?", (user_id,))
            # A new incarnation is a new social/economic body. Old money, sect
            # office, active expedition state, storage, and directly owned home
            # are not inherited automatically through the soul record.
            await db.execute("DELETE FROM currency_wallets WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM storage_inventory WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM storage_containers WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM sect_lineage WHERE disciple_user_id=? OR master_user_id=?", (user_id, user_id))
            await db.execute("DELETE FROM sect_membership WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM secret_realm_runs WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM auction_door_risks WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM cave_abode_access WHERE owner_user_id=? OR guest_user_id=?", (user_id, user_id))
            await db.execute("DELETE FROM cave_abodes WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM personal_worlds WHERE user_id=?", (user_id,))
            await db.execute("DELETE FROM player_family_invites WHERE inviter_user_id=? OR invitee_user_id=?", (user_id, user_id))
            await db.execute("DELETE FROM family_children WHERE parent_user_id=?", (user_id,))
            await db.execute("DELETE FROM player_family_members WHERE user_id=?", (user_id,))

            location = str(fp.get("location", "Greenriver Town"))
            starter_currency = {
                "Mortal World": "low_spirit_stone",
                "Spiritual World": "low_spirit_crystal",
                "Immortal World": "low_immortal_stone",
                "Celestial World": "low_celestial_crystal",
            }.get(target_world, "low_spirit_stone")
            starter_balance = 25
            legacy_spirit_stones = starter_balance if starter_currency == "low_spirit_stone" else 0
            origin = f"{fp.get('family_name', 'Unknown Family')}, {location}"
            await db.execute(
                """UPDATE characters SET name=?,origin=?,path=?,spiritual_root=?,gender=?,age_at_creation_years=?,
                       created_game_minute=?,natural_lifespan_years=?,life_extension_years=0,life_status='alive',
                       death_game_minute=NULL,reincarnation_ready_game_minute=NULL,realm_index=0,phase=1,cultivation=0,
                       body_realm_index=0,body_phase=1,body_cultivation=0,sense_power_bonus=0,sense_precision_bonus=0,
                       sense_range_bonus=0,concealment_bonus=0,concealment_active=0,qi=?,qi_max=?,vitality=?,vitality_max=?,
                       spirit_stones=?,insight_xp=0,location=?,attributes_json=?,updated_at=? WHERE user_id=?""",
                (
                    name, origin, path, spiritual_root, gender, int(starting_age), int(current_game_minute),
                    int(natural_lifespan_years), qi_max, qi_max, vitality_max, vitality_max,
                    legacy_spirit_stones, location, json.dumps(attributes), now, user_id,
                ),
            )

            await db.execute(
                """INSERT INTO soul_legacy(
                       user_id,incarnation_count,legacy_points,memory_seed,talent_echo,law_echo,insight_echo,
                       karmic_fortune,special_trait,awakened_memory,past_lives_json,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       incarnation_count=excluded.incarnation_count,legacy_points=excluded.legacy_points,
                       memory_seed=excluded.memory_seed,talent_echo=excluded.talent_echo,law_echo=excluded.law_echo,
                       insight_echo=excluded.insight_echo,karmic_fortune=excluded.karmic_fortune,
                       special_trait=excluded.special_trait,awakened_memory=0,past_lives_json=excluded.past_lives_json,
                       updated_at=excluded.updated_at""",
                (
                    user_id, incarnation_count, total_legacy_points, merged_memory, merged_talent, merged_law,
                    merged_insight, karmic_fortune, special_trait, 0, json.dumps(past_lives), now,
                ),
            )

            aptitude = dict(aptitude_profile or {})
            root_profile = dict(aptitude.get("root") or {
                "grade": "Common", "purity": 50, "elements": [spiritual_root], "mutation": "",
                "stability": 100, "refinement_progress": 0, "compatibility": 50,
            })
            await db.execute(
                """INSERT INTO character_spiritual_roots(
                       user_id,grade,purity,elements_json,mutation,stability,refinement_progress,compatibility,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    user_id, str(root_profile.get("grade", "Common")),
                    max(1, min(100, int(root_profile.get("purity", 50)))),
                    json.dumps(list(root_profile.get("elements") or [spiritual_root])),
                    str(root_profile.get("mutation", "")), max(0, min(100, int(root_profile.get("stability", 100)))),
                    max(0, min(100, int(root_profile.get("refinement_progress", 0)))),
                    max(0, min(100, int(root_profile.get("compatibility", 50)))), now,
                ),
            )
            bloodline_profile = aptitude.get("bloodline")
            if bloodline_profile:
                await db.execute(
                    """INSERT INTO character_bloodlines(
                           user_id,bloodline_id,name,affinity,purity,state,evolution_stage,progress,rejection,mutation,
                           primary_lineage,source_family_id,unlocked_techniques_json,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        user_id, str(bloodline_profile.get("bloodline_id", "legacy_family_bloodline")),
                        str(bloodline_profile.get("name", "Ancestral Bloodline")),
                        str(bloodline_profile.get("affinity", "None")),
                        max(0, min(100, int(bloodline_profile.get("purity", 0)))),
                        str(bloodline_profile.get("state", "dormant")),
                        max(0, int(bloodline_profile.get("evolution_stage", 0))),
                        max(0, min(100, int(bloodline_profile.get("progress", 0)))),
                        max(0, min(100, int(bloodline_profile.get("rejection", 0)))),
                        str(bloodline_profile.get("mutation", "")), 1, family_id,
                        json.dumps(list(bloodline_profile.get("unlocked_techniques") or [])), now,
                    ),
                )
            physique_profile = dict(aptitude.get("physique") or {
                "physique_id": "ordinary_mortal_body", "name": "Ordinary Mortal Body", "state": "ordinary",
                "evolution_stage": 0, "progress": 0, "stability": 100, "instability": 0,
            })
            await db.execute(
                """INSERT INTO character_physiques(
                       user_id,physique_id,name,state,evolution_stage,progress,stability,instability,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    user_id, str(physique_profile.get("physique_id", "ordinary_mortal_body")),
                    str(physique_profile.get("name", "Ordinary Mortal Body")),
                    str(physique_profile.get("state", "ordinary")),
                    max(0, int(physique_profile.get("evolution_stage", 0))),
                    max(0, min(100, int(physique_profile.get("progress", 0)))),
                    max(0, min(100, int(physique_profile.get("stability", 100)))),
                    max(0, min(100, int(physique_profile.get("instability", 0)))), now,
                ),
            )

            for iid, qty in {"spirit_herb": 2, "spirit_iron": 1}.items():
                await db.execute("INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,?)", (user_id, iid, qty))
            await db.execute(
                "INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(?,?,?)",
                (user_id, starter_currency, starter_balance),
            )
            await db.execute(
                "INSERT INTO storage_containers(user_id,container_id,name,grade,slot_capacity,living_space,updated_at) VALUES(?,?,?,?,?,?,?)",
                (user_id, "common_spatial_pouch", "Common Spatial Pouch", "Mortal", 24, 0, now),
            )

            await db.execute("UPDATE reincarnation_state SET active=0 WHERE user_id=?", (user_id,))
            payload = {
                "name": name, "generation": 1, "root": spiritual_root, "mode": "samsara",
                "target_world": target_world, "memory_seed": merged_memory, "talent_echo": merged_talent,
                "law_echo": merged_law, "legacy_points": total_legacy_points, "special_trait": special_trait,
            }
            await db.execute(
                "INSERT INTO event_log(user_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                (user_id, "reincarnation", json.dumps(payload), now),
            )
            await db.commit()

        return {
            "family_name": str(fp.get("family_name", "Unknown Family")),
            "generation": 1, "spiritual_root": spiritual_root, "mode": "samsara",
            "target_world": target_world, "memory_retention": merged_memory,
            "talent_retention": merged_talent, "comprehension_retention": merged_law,
            "insight_retention": merged_insight, "retained_insight": 0,
            "legacy_points": total_legacy_points, "special_trait": special_trait,
            "incarnation_count": incarnation_count,
        }



    # ------------------------------------------------------------------
    # Persistent conditions, tribulations, professions and social justice
    # ------------------------------------------------------------------
    async def apply_condition(
        self, user_id: int, *, condition_key: str, category: str, name: str,
        severity: int, source_type: str, source_id: str, effect: dict[str, Any],
        game_minute: int,
    ) -> dict[str, Any]:
        severity = max(1, min(5, int(severity)))
        now = time.time()
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT * FROM character_conditions WHERE user_id=? AND condition_key=? AND state='active'",
                (int(user_id), str(condition_key)),
            )
            existing = await cur.fetchone()
            if existing:
                severity = max(severity, int(existing["severity"]))
                await db.execute(
                    """UPDATE character_conditions SET severity=?,category=?,name=?,source_type=?,source_id=?,
                       effect_json=?,updated_game_minute=?,updated_at=? WHERE condition_id=?""",
                    (severity, str(category), str(name), str(source_type), str(source_id), json.dumps(effect),
                     int(game_minute), now, int(existing["condition_id"])),
                )
                condition_id = int(existing["condition_id"])
            else:
                cur = await db.execute(
                    """INSERT INTO character_conditions(
                           user_id,condition_key,category,name,severity,state,source_type,source_id,effect_json,
                           created_game_minute,updated_game_minute,created_at,updated_at
                       ) VALUES(?,?,?,?,?,'active',?,?,?,?,?,?,?)""",
                    (int(user_id), str(condition_key), str(category), str(name), severity, str(source_type),
                     str(source_id), json.dumps(effect), int(game_minute), int(game_minute), now, now),
                )
                condition_id = int(cur.lastrowid)
            await db.execute(
                """INSERT INTO active_effects(
                       user_id,effect_key,name,source_type,source_id,effect_json,stacks,starts_game_minute,ends_game_minute,created_at
                   ) VALUES(?,?,?,?,?,?,1,?,NULL,?)
                   ON CONFLICT(user_id,effect_key,source_type,source_id) DO UPDATE SET
                       name=excluded.name,effect_json=excluded.effect_json,stacks=1,
                       starts_game_minute=excluded.starts_game_minute,ends_game_minute=NULL,created_at=excluded.created_at""",
                (int(user_id), f"condition:{condition_key}", str(name), "condition", str(condition_key),
                 json.dumps(effect), int(game_minute), now),
            )
            await db.commit()
        row = await self.get_condition(int(user_id), str(condition_key))
        return row or {"condition_id": condition_id, "condition_key": condition_key, "severity": severity}

    async def get_condition(self, user_id: int, condition_key: str) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM character_conditions WHERE user_id=? AND condition_key=? AND state='active'",
                (int(user_id), str(condition_key)),
            )
            row = await cur.fetchone()
        if not row:
            return None
        data = dict(row)
        try:
            data["effect"] = json.loads(data.pop("effect_json") or "{}")
        except Exception:
            data["effect"] = {}
        return data

    async def get_conditions(self, user_id: int, *, active_only: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM character_conditions WHERE user_id=?"
        params: list[Any] = [int(user_id)]
        if active_only:
            sql += " AND state='active'"
        sql += " ORDER BY CASE state WHEN 'active' THEN 0 ELSE 1 END,severity DESC,condition_id DESC"
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(sql, tuple(params))
            rows = await cur.fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            try:
                data["effect"] = json.loads(data.pop("effect_json") or "{}")
            except Exception:
                data["effect"] = {}
            out.append(data)
        return out

    async def set_condition_severity(
        self, user_id: int, condition_key: str, *, severity: int, effect: dict[str, Any] | None,
        game_minute: int,
    ) -> dict[str, Any] | None:
        severity = int(severity)
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT condition_id,name FROM character_conditions WHERE user_id=? AND condition_key=? AND state='active'",
                (int(user_id), str(condition_key)),
            )
            row = await cur.fetchone()
            if not row:
                await db.rollback()
                return None
            if severity <= 0:
                await db.execute(
                    """UPDATE character_conditions SET state='resolved',severity=0,resolved_game_minute=?,
                       updated_game_minute=?,updated_at=? WHERE condition_id=?""",
                    (int(game_minute), int(game_minute), now, int(row[0])),
                )
                await db.execute(
                    "DELETE FROM active_effects WHERE user_id=? AND source_type='condition' AND source_id=?",
                    (int(user_id), str(condition_key)),
                )
            else:
                severity = max(1, min(5, severity))
                payload = dict(effect or {})
                await db.execute(
                    """UPDATE character_conditions SET severity=?,effect_json=?,updated_game_minute=?,updated_at=?
                       WHERE condition_id=?""",
                    (severity, json.dumps(payload), int(game_minute), now, int(row[0])),
                )
                await db.execute(
                    """UPDATE active_effects SET effect_json=?,name=?,starts_game_minute=?,created_at=?
                       WHERE user_id=? AND source_type='condition' AND source_id=?""",
                    (json.dumps(payload), str(row[1]), int(game_minute), now, int(user_id), str(condition_key)),
                )
            await db.commit()
        return await self.get_condition(int(user_id), str(condition_key))

    async def get_tribulation_state(self, user_id: int, gate_realm_index: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM tribulation_state WHERE user_id=? AND gate_realm_index=?",
                (int(user_id), int(gate_realm_index)),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def add_tribulation_preparation(
        self, user_id: int, gate_realm_index: int, *, amount: int, game_minute: int,
    ) -> dict[str, Any]:
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """INSERT INTO tribulation_state(user_id,gate_realm_index,preparation,attempts,cleared,last_result,updated_game_minute,updated_at)
                   VALUES(?,?,?,0,0,'',?,?)
                   ON CONFLICT(user_id,gate_realm_index) DO UPDATE SET
                       preparation=MIN(5,tribulation_state.preparation+excluded.preparation),
                       updated_game_minute=excluded.updated_game_minute,updated_at=excluded.updated_at""",
                (int(user_id), int(gate_realm_index), max(0, int(amount)), int(game_minute), now),
            )
            await db.commit()
        return (await self.get_tribulation_state(user_id, gate_realm_index)) or {}

    async def record_tribulation_attempt(
        self, user_id: int, gate_realm_index: int, *, preparation_used: int,
        waves: list[dict[str, Any]], success: bool, game_minute: int,
    ) -> dict[str, Any]:
        now = time.time()
        result_text = "cleared" if success else "failed"
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """INSERT INTO tribulation_attempts(user_id,gate_realm_index,preparation_used,waves_json,success,created_game_minute,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (int(user_id), int(gate_realm_index), max(0, int(preparation_used)), json.dumps(waves),
                 1 if success else 0, int(game_minute), now),
            )
            await db.execute(
                """INSERT INTO tribulation_state(user_id,gate_realm_index,preparation,attempts,cleared,last_result,updated_game_minute,updated_at)
                   VALUES(?,?,0,1,?,?,?,?)
                   ON CONFLICT(user_id,gate_realm_index) DO UPDATE SET
                       preparation=0,attempts=tribulation_state.attempts+1,
                       cleared=MAX(tribulation_state.cleared,excluded.cleared),last_result=excluded.last_result,
                       updated_game_minute=excluded.updated_game_minute,updated_at=excluded.updated_at""",
                (int(user_id), int(gate_realm_index), 1 if success else 0, result_text, int(game_minute), now),
            )
            await db.commit()
        return (await self.get_tribulation_state(user_id, gate_realm_index)) or {}

    async def get_tribulation_attempts(self, user_id: int, limit: int = 5) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM tribulation_attempts WHERE user_id=? ORDER BY attempt_id DESC LIMIT ?",
                (int(user_id), max(1, min(20, int(limit)))),
            )
            rows = await cur.fetchall()
        out = []
        for row in rows:
            data = dict(row)
            try:
                data["waves"] = json.loads(data.pop("waves_json") or "[]")
            except Exception:
                data["waves"] = []
            out.append(data)
        return out

    async def record_profession_practice(
        self, user_id: int, profession: str, *, success: bool, xp_gain: int, quality_points: int = 0,
    ) -> dict[str, Any]:
        from ..progression_systems import profession_xp_needed
        now = time.time()
        profession = str(profession)
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """INSERT INTO profession_progress(user_id,profession,level,xp,successes,failures,quality_points,updated_at)
                   VALUES(?,?,0,?,?,?,?,?)
                   ON CONFLICT(user_id,profession) DO UPDATE SET
                       xp=profession_progress.xp+excluded.xp,
                       successes=profession_progress.successes+excluded.successes,
                       failures=profession_progress.failures+excluded.failures,
                       quality_points=profession_progress.quality_points+excluded.quality_points,
                       updated_at=excluded.updated_at""",
                (int(user_id), profession, max(0, int(xp_gain)), 1 if success else 0, 0 if success else 1,
                 max(0, int(quality_points)), now),
            )
            cur = await db.execute(
                "SELECT * FROM profession_progress WHERE user_id=? AND profession=?", (int(user_id), profession)
            )
            row = dict(await cur.fetchone())
            level = int(row["level"]); xp = int(row["xp"])
            while xp >= profession_xp_needed(level) and level < 6:
                xp -= profession_xp_needed(level); level += 1
            await db.execute(
                "UPDATE profession_progress SET level=?,xp=?,updated_at=? WHERE user_id=? AND profession=?",
                (level, xp, now, int(user_id), profession),
            )
            await db.commit()
        return (await self.get_profession_progress(user_id, profession)) or {}

    async def get_profession_progress(self, user_id: int, profession: str | None = None) -> dict[str, Any] | None | list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            if profession is not None:
                cur = await db.execute(
                    "SELECT * FROM profession_progress WHERE user_id=? AND profession=?", (int(user_id), str(profession))
                )
                row = await cur.fetchone()
                return dict(row) if row else None
            cur = await db.execute(
                "SELECT * FROM profession_progress WHERE user_id=? ORDER BY level DESC,xp DESC,profession",
                (int(user_id),),
            )
            return [dict(row) for row in await cur.fetchall()]

    async def get_alchemy_state(self, user_id: int, *, game_minute: int | None = None) -> dict[str, Any]:
        """Return persistent alchemy state, settling natural pill-toxicity decay."""
        from ..alchemy import PILL_TOXICITY_DECAY_AMOUNT, PILL_TOXICITY_DECAY_MINUTES

        now = time.time()
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute("SELECT * FROM alchemy_state WHERE user_id=?", (int(user_id),))
            row = await cur.fetchone()
            if row is None:
                anchor = max(0, int(game_minute or 0))
                await db.execute(
                    """INSERT INTO alchemy_state(
                           user_id,pill_toxicity,last_toxicity_game_minute,total_refinements,
                           successful_refinements,flawless_refinements,best_margin,last_quality,updated_at
                       ) VALUES(?,0,?,0,0,0,-99,'',?)""",
                    (int(user_id), anchor, now),
                )
            elif game_minute is not None:
                state = dict(row)
                last = int(state.get("last_toxicity_game_minute", 0))
                current = max(last, int(game_minute))
                if last <= 0:
                    await db.execute(
                        "UPDATE alchemy_state SET last_toxicity_game_minute=?,updated_at=? WHERE user_id=?",
                        (current, now, int(user_id)),
                    )
                else:
                    steps = max(0, (current - last) // PILL_TOXICITY_DECAY_MINUTES)
                    if steps:
                        decay = int(steps) * int(PILL_TOXICITY_DECAY_AMOUNT)
                        advanced = last + int(steps) * PILL_TOXICITY_DECAY_MINUTES
                        await db.execute(
                            """UPDATE alchemy_state
                               SET pill_toxicity=MAX(0,pill_toxicity-?),last_toxicity_game_minute=?,updated_at=?
                               WHERE user_id=?""",
                            (decay, advanced, now, int(user_id)),
                        )
            await db.commit()
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM alchemy_state WHERE user_id=?", (int(user_id),))
            row = await cur.fetchone()
            return dict(row) if row else {
                "user_id": int(user_id), "pill_toxicity": 0, "last_toxicity_game_minute": int(game_minute or 0),
                "total_refinements": 0, "successful_refinements": 0, "flawless_refinements": 0,
                "best_margin": -99, "last_quality": "",
            }

    async def add_pill_toxicity(self, user_id: int, amount: int, *, game_minute: int) -> dict[str, Any]:
        await self.get_alchemy_state(user_id, game_minute=int(game_minute))
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """UPDATE alchemy_state
                   SET pill_toxicity=MIN(100,MAX(0,pill_toxicity+?)),last_toxicity_game_minute=?,updated_at=?
                   WHERE user_id=?""",
                (int(amount), int(game_minute), now, int(user_id)),
            )
            await db.commit()
        return await self.get_alchemy_state(user_id, game_minute=int(game_minute))

    async def reduce_pill_toxicity(self, user_id: int, amount: int, *, game_minute: int) -> dict[str, Any]:
        return await self.add_pill_toxicity(user_id, -abs(int(amount)), game_minute=int(game_minute))

    async def record_alchemy_batch(
        self, user_id: int, *, recipe_name: str, quality: str, margin: int, success: bool,
        output: dict[str, int], location: str, game_minute: int,
    ) -> dict[str, Any]:
        now = time.time()
        quality_key = str(quality).strip().lower()
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """INSERT INTO alchemy_state(
                       user_id,pill_toxicity,last_toxicity_game_minute,total_refinements,
                       successful_refinements,flawless_refinements,best_margin,last_quality,updated_at
                   ) VALUES(?,0,?,1,?,?,?, ?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       total_refinements=alchemy_state.total_refinements+1,
                       successful_refinements=alchemy_state.successful_refinements+excluded.successful_refinements,
                       flawless_refinements=alchemy_state.flawless_refinements+excluded.flawless_refinements,
                       best_margin=MAX(alchemy_state.best_margin,excluded.best_margin),
                       last_quality=excluded.last_quality,updated_at=excluded.updated_at""",
                (
                    int(user_id), int(game_minute), 1 if success else 0,
                    1 if success and quality_key == "flawless" else 0, int(margin), quality_key, now,
                ),
            )
            cur = await db.execute(
                """INSERT INTO alchemy_batches(
                       user_id,recipe_name,quality,margin,success,output_json,location,game_minute,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    int(user_id), str(recipe_name)[:120], quality_key, int(margin), 1 if success else 0,
                    json.dumps({str(k): int(v) for k, v in output.items()}), str(location)[:160], int(game_minute), now,
                ),
            )
            batch_id = int(cur.lastrowid)
            await db.commit()
        rows = await self.get_alchemy_batches(user_id, limit=1)
        return rows[0] if rows else {"batch_id": batch_id}

    async def get_alchemy_batches(self, user_id: int, *, limit: int = 10) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM alchemy_batches WHERE user_id=? ORDER BY batch_id DESC LIMIT ?",
                (int(user_id), max(1, min(50, int(limit)))),
            )
            rows = [dict(r) for r in await cur.fetchall()]
        for row in rows:
            try:
                row["output"] = json.loads(row.pop("output_json") or "{}")
            except Exception:
                row["output"] = {}
        return rows

    async def adjust_reputation(self, user_id: int, faction_key: str, delta: int, *, reason: str = "") -> int:
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """INSERT INTO faction_reputation(user_id,faction_key,score,last_reason,updated_at) VALUES(?,?,?,?,?)
                   ON CONFLICT(user_id,faction_key) DO UPDATE SET
                       score=MAX(-100,MIN(100,faction_reputation.score+excluded.score)),
                       last_reason=excluded.last_reason,updated_at=excluded.updated_at""",
                (int(user_id), str(faction_key), max(-100, min(100, int(delta))), str(reason)[:500], now),
            )
            cur = await db.execute(
                "SELECT score FROM faction_reputation WHERE user_id=? AND faction_key=?",
                (int(user_id), str(faction_key)),
            )
            row = await cur.fetchone(); score = int(row[0]) if row else 0
            await db.commit()
            return score

    async def get_reputations(self, user_id: int) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM faction_reputation WHERE user_id=? ORDER BY ABS(score) DESC,faction_key", (int(user_id),)
            )
            return [dict(r) for r in await cur.fetchall()]

    async def record_crime(
        self, user_id: int, *, jurisdiction: str, crime_type: str, severity: int, evidence: int,
        description: str, game_minute: int, witness_type: str = "", witness_key: str = "",
    ) -> dict[str, Any]:
        severity = max(1, min(10, int(severity)))
        evidence = max(0, min(100, int(evidence)))
        now = time.time()
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                """INSERT INTO crime_records(user_id,jurisdiction,crime_type,severity,evidence,status,description,created_game_minute,created_at,updated_at)
                   VALUES(?,?,?,?,?,'open',?,?,?,?)""",
                (int(user_id), str(jurisdiction), str(crime_type), severity, evidence, str(description)[:1000],
                 int(game_minute), now, now),
            )
            crime_id = int(cur.lastrowid)
            if witness_type and witness_key:
                await db.execute(
                    """INSERT INTO witness_records(crime_id,witness_type,witness_key,reliability,statement,created_at)
                       VALUES(?,?,?,?,?,?)""",
                    (crime_id, str(witness_type), str(witness_key), evidence, str(description)[:1000], now),
                )
            bounty_id = None
            if severity >= 3 and evidence >= 50:
                amount = severity * 50 * (2 if evidence >= 85 else 1)
                cur = await db.execute(
                    """INSERT INTO bounties(user_id,jurisdiction,amount,status,reason,source_crime_id,created_game_minute,created_at,updated_at)
                       VALUES(?,?,?,'active',?,?,?,?,?)""",
                    (int(user_id), str(jurisdiction), amount, str(description)[:500], crime_id,
                     int(game_minute), now, now),
                )
                bounty_id = int(cur.lastrowid)
            await db.commit()
        return {"crime_id": crime_id, "bounty_id": bounty_id, "severity": severity, "evidence": evidence}

    async def get_crimes(self, user_id: int, *, open_only: bool = True, limit: int = 20) -> list[dict[str, Any]]:
        sql = "SELECT * FROM crime_records WHERE user_id=?"
        params: list[Any] = [int(user_id)]
        if open_only:
            sql += " AND status='open'"
        sql += " ORDER BY crime_id DESC LIMIT ?"; params.append(max(1, min(50, int(limit))))
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(sql, tuple(params))
            return [dict(r) for r in await cur.fetchall()]

    async def resolve_crime(self, user_id: int, crime_id: int, *, status: str = "atoned") -> dict[str, Any] | None:
        now=time.time()
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur=await db.execute(
                "SELECT * FROM crime_records WHERE crime_id=? AND user_id=? AND status='open'",
                (int(crime_id),int(user_id)),
            )
            row=await cur.fetchone()
            if not row:
                await db.rollback(); return None
            await db.execute(
                "UPDATE crime_records SET status=?,updated_at=? WHERE crime_id=?",
                (str(status)[:40],now,int(crime_id)),
            )
            await db.execute(
                "UPDATE bounties SET status='resolved',updated_at=? WHERE source_crime_id=? AND user_id=? AND status='active'",
                (now,int(crime_id),int(user_id)),
            )
            await db.commit()
            data=dict(row); data['status']=str(status)[:40]; return data

    async def get_witnesses(self, crime_id: int) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM witness_records WHERE crime_id=? ORDER BY witness_id", (int(crime_id),))
            return [dict(r) for r in await cur.fetchall()]

    async def get_bounties(self, user_id: int, *, active_only: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM bounties WHERE user_id=?"
        params: list[Any] = [int(user_id)]
        if active_only:
            sql += " AND status='active'"
        sql += " ORDER BY amount DESC,bounty_id DESC"
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(sql, tuple(params))
            return [dict(r) for r in await cur.fetchall()]

    async def add_grudge(
        self, user_id: int, *, holder_type: str, holder_key: str, intensity: int, reason: str,
        game_minute: int,
    ) -> dict[str, Any]:
        now = time.time(); intensity = max(1, min(10, int(intensity)))
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """INSERT INTO grudges(user_id,holder_type,holder_key,intensity,status,reason,created_game_minute,created_at,updated_at)
                   VALUES(?,?,?,?, 'active',?,?,?,?)
                   ON CONFLICT(user_id,holder_type,holder_key) WHERE status='active' DO UPDATE SET
                       intensity=MIN(10,grudges.intensity+excluded.intensity),reason=excluded.reason,updated_at=excluded.updated_at""",
                (int(user_id), str(holder_type), str(holder_key), intensity, str(reason)[:500], int(game_minute), now, now),
            )
            await db.commit()
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM grudges WHERE user_id=? AND holder_type=? AND holder_key=? AND status='active'",
                (int(user_id), str(holder_type), str(holder_key)),
            )
            row = await cur.fetchone()
            result = dict(row) if row else {}
        char = await self.get_character(user_id) or {}
        await self.record_world_history_event(
            event_type="grudge", title=f"Grudge formed: {holder_key}",
            summary=(
                f"{holder_type.title()} {holder_key} formed or intensified a grudge against "
                f"{char.get('name','the cultivator')}. Cause: {reason}"
            ),
            significance=min(90, 35 + int(result.get('intensity', intensity)) * 6),
            visibility="participant", location=str(char.get("location") or ""),
            faction=(str(holder_key) if str(holder_type).lower() in {"faction","sect","clan","family"} else ""),
            actor_type=str(holder_type), actor_key=str(holder_key), actor_name=str(holder_key),
            target_type="player", target_key=str(int(user_id)), target_name=str(char.get("name") or ""),
            related_user_id=int(user_id),
            related_npc_name=(str(holder_key) if str(holder_type).lower()=="npc" else ""),
            tags=("grudge","hostility",str(holder_type)), game_minute=int(game_minute),
            metadata={"intensity": int(result.get('intensity', intensity)), "reason": reason},
            source_key=f"grudge:{int(user_id)}:{holder_type}:{holder_key}",
        )
        return result

    async def get_grudges(self, user_id: int, *, active_only: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM grudges WHERE user_id=?"
        params: list[Any] = [int(user_id)]
        if active_only:
            sql += " AND status='active'"
        sql += " ORDER BY intensity DESC,grudge_id DESC"
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(sql, tuple(params))
            return [dict(r) for r in await cur.fetchall()]


    # ------------------------------------------------------------------
    # Advanced branch forward-port systems
    # ------------------------------------------------------------------
    async def add_spirit_beast(
        self, user_id: int, *, name: str, species: str, rank: int = 0, element: str = "None",
        intelligence: int = 10, temperament: str = "wary", bloodline: str = "Common",
        contract_type: str = "equality", techniques: list[str] | None = None, active: bool = False,
    ) -> dict[str, Any]:
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            if active:
                await db.execute("UPDATE spirit_beasts SET active=0 WHERE user_id=?", (int(user_id),))
            cur = await db.execute(
                """INSERT INTO spirit_beasts(
                       user_id,name,species,rank,element,intelligence,temperament,bloodline,evolution_stage,
                       loyalty,contract_type,active,techniques_json,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,0,25,?,?,?,?,?)""",
                (int(user_id), str(name)[:80], str(species)[:80], max(0,int(rank)), str(element)[:40],
                 max(0,min(100,int(intelligence))), str(temperament)[:60], str(bloodline)[:80],
                 str(contract_type)[:30], 1 if active else 0, json.dumps(techniques or []), now, now),
            )
            beast_id = int(cur.lastrowid)
            await db.commit()
        rows = await self.get_spirit_beasts(user_id)
        return next(r for r in rows if int(r["beast_id"]) == beast_id)

    async def get_spirit_beasts(self, user_id: int) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM spirit_beasts WHERE user_id=? ORDER BY active DESC,loyalty DESC,beast_id",
                (int(user_id),),
            )
            rows = [dict(r) for r in await cur.fetchall()]
        for row in rows:
            try: row["techniques"] = json.loads(row.pop("techniques_json") or "[]")
            except Exception: row["techniques"] = []
        return rows

    async def train_spirit_beast(self, user_id: int, beast_id: int, amount: int = 5) -> dict[str, Any] | None:
        now = time.time(); amount=max(1,min(25,int(amount)))
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """UPDATE spirit_beasts SET loyalty=MIN(100,loyalty+?),intelligence=MIN(100,intelligence+MAX(1,?/3)),updated_at=?
                   WHERE user_id=? AND beast_id=?""",
                (amount, amount, now, int(user_id), int(beast_id)),
            )
            await db.commit()
        return next((r for r in await self.get_spirit_beasts(user_id) if int(r["beast_id"])==int(beast_id)), None)

    async def evolve_spirit_beast(self, user_id: int, beast_id: int) -> dict[str, Any] | None:
        now=time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur=await db.execute("SELECT loyalty,evolution_stage FROM spirit_beasts WHERE user_id=? AND beast_id=?",(int(user_id),int(beast_id)))
            row=await cur.fetchone()
            if not row or int(row[0]) < 60 + int(row[1])*10:
                await db.rollback(); return None
            await db.execute(
                "UPDATE spirit_beasts SET evolution_stage=evolution_stage+1,rank=rank+1,loyalty=MAX(25,loyalty-20),updated_at=? WHERE user_id=? AND beast_id=?",
                (now,int(user_id),int(beast_id)),
            )
            await db.commit()
        return next((r for r in await self.get_spirit_beasts(user_id) if int(r["beast_id"])==int(beast_id)), None)

    async def set_active_spirit_beast(self, user_id: int, beast_id: int) -> bool:
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur=await db.execute("SELECT 1 FROM spirit_beasts WHERE user_id=? AND beast_id=?",(int(user_id),int(beast_id)))
            if not await cur.fetchone(): await db.rollback(); return False
            await db.execute("UPDATE spirit_beasts SET active=0 WHERE user_id=?",(int(user_id),))
            await db.execute("UPDATE spirit_beasts SET active=1,updated_at=? WHERE user_id=? AND beast_id=?",(time.time(),int(user_id),int(beast_id)))
            await db.commit(); return True

    async def create_wild_beast_encounter(
        self, user_id: int, *, species: str, rank: int, element: str, intelligence: int,
        temperament: str, bloodline: str, taming_tn: int, location: str,
        created_game_minute: int, duration_game_minutes: int = 180,
    ) -> dict[str, Any]:
        now = time.time()
        expires = int(created_game_minute) + max(30, int(duration_game_minutes))
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """UPDATE wild_beast_encounters SET status='expired',updated_at=?
                   WHERE user_id=? AND status='available' AND expires_game_minute<=?""",
                (now, int(user_id), int(created_game_minute)),
            )
            cur = await db.execute(
                """INSERT INTO wild_beast_encounters(
                       user_id,species,rank,element,intelligence,temperament,bloodline,taming_tn,
                       location,expires_game_minute,status,created_game_minute,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,'available',?,?,?)""",
                (
                    int(user_id), str(species)[:80], max(0, int(rank)), str(element)[:40],
                    max(1, min(100, int(intelligence))), str(temperament)[:60], str(bloodline)[:80],
                    max(5, int(taming_tn)), str(location)[:160], expires, int(created_game_minute), now, now,
                ),
            )
            encounter_id = int(cur.lastrowid)
            await db.commit()
        rows = await self.get_wild_beast_encounters(user_id, game_minute=int(created_game_minute), include_resolved=True)
        return next((r for r in rows if int(r["encounter_id"]) == encounter_id), {"encounter_id": encounter_id})

    async def get_wild_beast_encounters(
        self, user_id: int, *, game_minute: int, location: str | None = None, include_resolved: bool = False,
    ) -> list[dict[str, Any]]:
        now = time.time()
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                """UPDATE wild_beast_encounters SET status='expired',updated_at=?
                   WHERE user_id=? AND status='available' AND expires_game_minute<=?""",
                (now, int(user_id), int(game_minute)),
            )
            await db.commit()
            sql = "SELECT * FROM wild_beast_encounters WHERE user_id=?"
            params: list[Any] = [int(user_id)]
            if not include_resolved:
                sql += " AND status='available'"
            if location is not None:
                sql += " AND location=?"
                params.append(str(location))
            sql += " ORDER BY encounter_id DESC LIMIT 20"
            cur = await db.execute(sql, tuple(params))
            return [dict(r) for r in await cur.fetchall()]

    async def resolve_wild_beast_taming(
        self, user_id: int, encounter_id: int, *, success: bool, game_minute: int,
        contract_type: str = "equality", active_if_first: bool = True,
    ) -> dict[str, Any] | None:
        now = time.time()
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT * FROM wild_beast_encounters WHERE user_id=? AND encounter_id=?",
                (int(user_id), int(encounter_id)),
            )
            encounter = await cur.fetchone()
            if not encounter or str(encounter["status"]) != "available" or int(encounter["expires_game_minute"]) <= int(game_minute):
                if encounter and str(encounter["status"]) == "available":
                    await db.execute(
                        "UPDATE wild_beast_encounters SET status='expired',updated_at=? WHERE encounter_id=?",
                        (now, int(encounter_id)),
                    )
                    await db.commit()
                else:
                    await db.rollback()
                return None
            if not success:
                await db.execute(
                    "UPDATE wild_beast_encounters SET status='escaped',updated_at=? WHERE encounter_id=?",
                    (now, int(encounter_id)),
                )
                await db.commit()
                return {"encounter": dict(encounter), "status": "escaped", "beast": None}

            cur = await db.execute("SELECT COUNT(*) FROM spirit_beasts WHERE user_id=?", (int(user_id),))
            count = int((await cur.fetchone())[0])
            if count >= 5:
                await db.rollback()
                raise ValueError("You already maintain the maximum of five spirit-beast contracts.")
            active = 1 if active_if_first and count == 0 else 0
            if active:
                await db.execute("UPDATE spirit_beasts SET active=0 WHERE user_id=?", (int(user_id),))
            cur = await db.execute(
                """INSERT INTO spirit_beasts(
                       user_id,name,species,rank,element,intelligence,temperament,bloodline,evolution_stage,
                       loyalty,contract_type,active,techniques_json,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,0,30,?,?,?,?,?)""",
                (
                    int(user_id), str(encounter["species"])[:80], str(encounter["species"])[:80],
                    max(0, int(encounter["rank"])), str(encounter["element"])[:40],
                    max(1, min(100, int(encounter["intelligence"]))), "bonded", str(encounter["bloodline"])[:80],
                    str(contract_type)[:30], active, json.dumps([]), now, now,
                ),
            )
            beast_id = int(cur.lastrowid)
            await db.execute(
                "UPDATE wild_beast_encounters SET status='tamed',updated_at=? WHERE encounter_id=?",
                (now, int(encounter_id)),
            )
            await db.commit()
        beasts = await self.get_spirit_beasts(user_id)
        beast = next((r for r in beasts if int(r["beast_id"]) == beast_id), None)
        return {"encounter": dict(encounter), "status": "tamed", "beast": beast}

    async def bond_artifact(self, user_id: int, item_id: str) -> dict[str, Any]:
        now=time.time()
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO artifact_bonds(user_id,item_id,bond_level,resonance,awakened,spirit_name,temperament,created_at,updated_at)
                   VALUES(?,?,1,10,0,'','dormant',?,?)
                   ON CONFLICT(user_id,item_id) DO UPDATE SET bond_level=MIN(10,artifact_bonds.bond_level+1),
                       resonance=MIN(100,artifact_bonds.resonance+8),updated_at=excluded.updated_at""",
                (int(user_id),str(item_id),now,now),
            ); await db.commit()
        return (await self.get_artifact_bonds(user_id,item_id))[0]

    async def get_artifact_bonds(self, user_id: int, item_id: str | None=None) -> list[dict[str, Any]]:
        sql="SELECT * FROM artifact_bonds WHERE user_id=?"; params:[Any]=[int(user_id)]
        if item_id is not None: sql+=" AND item_id=?"; params.append(str(item_id))
        sql+=" ORDER BY awakened DESC,resonance DESC,item_id"
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; cur=await db.execute(sql,tuple(params)); return [dict(r) for r in await cur.fetchall()]

    async def awaken_artifact(self, user_id: int, item_id: str, spirit_name: str) -> dict[str, Any] | None:
        now=time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur=await db.execute("SELECT bond_level,resonance FROM artifact_bonds WHERE user_id=? AND item_id=?",(int(user_id),str(item_id)))
            row=await cur.fetchone()
            if not row or int(row[0]) < 3 or int(row[1]) < 25: await db.rollback(); return None
            await db.execute(
                "UPDATE artifact_bonds SET awakened=1,spirit_name=?,temperament='awakened',resonance=MIN(100,resonance+15),updated_at=? WHERE user_id=? AND item_id=?",
                (str(spirit_name)[:80],now,int(user_id),str(item_id)),
            ); await db.commit()
        rows=await self.get_artifact_bonds(user_id,item_id); return rows[0] if rows else None

    async def ensure_territory(self, territory_key: str, *, name: str, region: str, resource_type: str="mixed", game_minute: int=0) -> None:
        now=time.time()
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO territory_state(territory_key,name,region,resource_type,updated_game_minute,updated_at)
                   VALUES(?,?,?,?,?,?) ON CONFLICT(territory_key) DO UPDATE SET name=excluded.name,region=excluded.region,
                   resource_type=excluded.resource_type,updated_at=excluded.updated_at""",
                (str(territory_key),str(name),str(region),str(resource_type),int(game_minute),now),
            ); await db.commit()

    async def get_territories(self, region: str | None=None) -> list[dict[str, Any]]:
        sql="SELECT * FROM territory_state"; params:list[Any]=[]
        if region: sql+=" WHERE region=?"; params.append(str(region))
        sql+=" ORDER BY region,name"
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; cur=await db.execute(sql,tuple(params)); return [dict(r) for r in await cur.fetchall()]

    async def claim_territory(self, territory_key: str, *, controller_type: str, controller_key: str, game_minute: int) -> bool:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur0 = await db.execute("SELECT name,region,controller_key FROM territory_state WHERE territory_key=?", (str(territory_key),))
            before_row = await cur0.fetchone()
            before = dict(before_row) if before_row else {}
            cur=await db.execute(
                """UPDATE territory_state SET controller_type=?,controller_key=?,unrest=MIN(100,unrest+10),updated_game_minute=?,updated_at=?
                   WHERE territory_key=?""",
                (str(controller_type),str(controller_key),int(game_minute),time.time(),str(territory_key)),
            )
            await db.commit(); changed = int(cur.rowcount or 0)>0
        if changed:
            territory_name = str(before.get("name") or territory_key)
            previous = str(before.get("controller_key") or "")
            await self.record_world_history_event(
                event_type="territory_claim", title=f"{controller_key} claimed {territory_name}",
                summary=f"Control of {territory_name} passed from {previous or 'no recognized ruler'} to {controller_key}.",
                significance=70, visibility="public", location=str(territory_key), faction=str(controller_key),
                actor_type=str(controller_type), actor_key=str(controller_key), actor_name=str(controller_key),
                target_type="territory", target_key=str(territory_key), target_name=territory_name,
                tags=("territory","claim","control","leadership"), game_minute=int(game_minute),
                metadata={"previous_controller": previous},
                source_key=f"territory_claim:{territory_key}:{int(game_minute)}:{controller_key}",
            )
        return changed

    async def start_territory_war(self, *, attacker_key: str, defender_key: str, territory_key: str, game_minute: int) -> int:
        now=time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur=await db.execute(
                """INSERT INTO territory_wars(attacker_key,defender_key,territory_key,status,created_game_minute,updated_game_minute,created_at,updated_at)
                   VALUES(?,?,?,'active',?,?,?,?)""",
                (str(attacker_key),str(defender_key),str(territory_key),int(game_minute),int(game_minute),now,now),
            )
            war_id=int(cur.lastrowid)
            await self._ensure_war_operation_locked(db,war_id,int(game_minute))
            await db.commit()
        await self.record_world_history_event(
            event_type="war_started", title=f"War for {territory_key}",
            summary=f"{attacker_key} began a formal territorial war against {defender_key} for control of {territory_key}.",
            significance=86, visibility="public", location=str(territory_key), faction=str(attacker_key),
            actor_type="sect", actor_key=str(attacker_key), actor_name=str(attacker_key),
            target_type="sect", target_key=str(defender_key), target_name=str(defender_key),
            tags=("war","territory",str(attacker_key),str(defender_key)), game_minute=int(game_minute),
            metadata={"war_id": war_id, "territory_key": territory_key, "attacker": attacker_key, "defender": defender_key},
            source_key=f"territory_war:{war_id}:started",
        )
        return war_id

    async def get_territory_wars(self, *, active_only: bool=True) -> list[dict[str, Any]]:
        sql="SELECT * FROM territory_wars" + (" WHERE status='active'" if active_only else "") + " ORDER BY war_id DESC"
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; cur=await db.execute(sql); rows=[dict(r) for r in await cur.fetchall()]
            for row in rows:
                cur=await db.execute("SELECT * FROM territory_war_operations WHERE war_id=?",(int(row['war_id']),))
                op=await cur.fetchone(); row['operations']=dict(op) if op else {}
            return rows

    async def create_caravan(self, *, owner_type: str, owner_key: str, origin: str, destination: str, cargo: dict[str,int], risk: int, depart_game_minute: int, arrive_game_minute: int, escort_strength: int=0, concealment: int=0, smuggling: bool=False, tax_rate: int=0) -> int:
        now=time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur=await db.execute(
                """INSERT INTO caravans(owner_type,owner_key,origin,destination,cargo_json,status,risk,depart_game_minute,arrive_game_minute,updated_at)
                   VALUES(?,?,?,?,?,'traveling',?,?,?,?)""",
                (str(owner_type),str(owner_key),str(origin),str(destination),json.dumps(cargo),max(0,min(100,int(risk))),int(depart_game_minute),int(arrive_game_minute),now),
            )
            caravan_id=int(cur.lastrowid)
            await db.execute(
                """INSERT INTO caravan_operations(caravan_id,escort_strength,concealment,smuggling,tax_rate,toll_paid,intercepted,seized,payout_final,losses_json,outcome,resolved_game_minute,updated_at)
                   VALUES(?,?,?,?,?,0,0,0,0,'{}','traveling',NULL,?)""",
                (caravan_id,max(0,int(escort_strength)),max(0,int(concealment)),1 if smuggling else 0,max(0,min(50,int(tax_rate))),now),
            )
            await db.execute(
                "INSERT INTO caravan_events(caravan_id,event_type,detail_json,game_minute,created_at) VALUES(?, 'departed', ?, ?, ?)",
                (caravan_id,json.dumps({'escort_strength':max(0,int(escort_strength)),'concealment':max(0,int(concealment)),'smuggling':bool(smuggling),'tax_rate':max(0,min(50,int(tax_rate)))}),int(depart_game_minute),now),
            )
            await db.commit(); return caravan_id

    async def get_caravans(self, *, status: str | None=None, location: str | None=None) -> list[dict[str, Any]]:
        sql="""SELECT c.*,o.escort_strength,o.concealment,o.smuggling,o.tax_rate,o.toll_paid,o.intercepted,o.seized,o.payout_final,o.losses_json,o.outcome,o.resolved_game_minute
               FROM caravans c LEFT JOIN caravan_operations o ON o.caravan_id=c.caravan_id WHERE 1=1"""; params:list[Any]=[]
        if status: sql+=" AND c.status=?"; params.append(str(status))
        if location: sql+=" AND (c.origin=? OR c.destination=?)"; params.extend([str(location),str(location)])
        sql+=" ORDER BY c.caravan_id DESC"
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; cur=await db.execute(sql,tuple(params)); rows=[dict(r) for r in await cur.fetchall()]
        for row in rows:
            try: row["cargo"]=json.loads(row.pop("cargo_json") or "{}")
            except Exception: row["cargo"]={}
            try: row["losses"]=json.loads(row.pop("losses_json") or "{}")
            except Exception: row["losses"]={}
        return rows


    async def settle_player_caravans(self, user_id: int, current_game_minute: int) -> list[dict[str, Any]]:
        return await self.advance_caravans(int(current_game_minute), owner_type='player', owner_key=str(int(user_id)))

    async def record_item_provenance(self, user_id: int, item_id: str, *, quantity: int=1, source_type: str="unknown", source_key: str="", ownership_mark: str="", legal_status: str="clean", authenticity: int=100, tracking_strength: int=0, game_minute: int=0) -> int:
        now=time.time()
        async with self._connect() as db:
            cur=await db.execute(
                """INSERT INTO item_provenance(user_id,item_id,quantity,source_type,source_key,ownership_mark,legal_status,authenticity,tracking_strength,acquired_game_minute,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (int(user_id),str(item_id),max(1,int(quantity)),str(source_type),str(source_key),str(ownership_mark),str(legal_status),max(0,min(100,int(authenticity))),max(0,min(100,int(tracking_strength))),int(game_minute),now,now),
            ); await db.commit(); return int(cur.lastrowid)

    async def get_item_provenance(self, user_id: int, item_id: str | None=None) -> list[dict[str, Any]]:
        sql="SELECT * FROM item_provenance WHERE user_id=?"; params:list[Any]=[int(user_id)]
        if item_id: sql+=" AND item_id=?"; params.append(str(item_id))
        sql+=" ORDER BY provenance_id DESC"
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; cur=await db.execute(sql,tuple(params)); return [dict(r) for r in await cur.fetchall()]

    async def get_social_state(self, user_id: int) -> dict[str, Any]:
        now=time.time()
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO character_social_state(user_id,face,dao_heart,dao_stability,vow,obsession,updated_at)
                   VALUES(?,0,50,100,'','',?) ON CONFLICT(user_id) DO NOTHING""",(int(user_id),now))
            await db.commit(); db.row_factory=aiosqlite.Row
            cur=await db.execute("SELECT * FROM character_social_state WHERE user_id=?",(int(user_id),)); row=await cur.fetchone(); return dict(row)

    async def adjust_social_state(self, user_id: int, *, face_delta: int=0, dao_heart_delta: int=0, stability_delta: int=0, vow: str | None=None, obsession: str | None=None) -> dict[str, Any]:
        await self.get_social_state(user_id); now=time.time()
        sets=["face=MAX(-1000,MIN(1000,face+?))","dao_heart=MAX(0,MIN(100,dao_heart+?))","dao_stability=MAX(0,MIN(100,dao_stability+?))","updated_at=?"]
        params:list[Any]=[int(face_delta),int(dao_heart_delta),int(stability_delta),now]
        if vow is not None: sets.append("vow=?"); params.append(str(vow)[:240])
        if obsession is not None: sets.append("obsession=?"); params.append(str(obsession)[:240])
        params.append(int(user_id))
        async with self._connect() as db:
            await db.execute("UPDATE character_social_state SET "+",".join(sets)+" WHERE user_id=?",tuple(params)); await db.commit()
        return await self.get_social_state(user_id)

    async def get_current_era(self) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; cur=await db.execute("SELECT * FROM world_eras WHERE active=1 ORDER BY era_id DESC LIMIT 1"); row=await cur.fetchone()
        if not row: return None
        out=dict(row)
        try: out["modifiers"]=json.loads(out.pop("modifiers_json") or "{}")
        except Exception: out["modifiers"]={}
        # Old databases stored the baseline era before modifiers became
        # mechanical. Resolve canonical cycle defaults by name so upgrades
        # gain the mechanics without rewriting historical rows.
        template = next((era for era in ERA_CYCLE if era["name"] == str(out.get("name"))), None)
        if template:
            merged = dict(template.get("modifiers") or {})
            merged.update(out.get("modifiers") or {})
            out["modifiers"] = merged
            out["duration_days"] = int(template["duration_days"])
        return out

    async def start_world_era(self, name: str, *, description: str="", game_minute: int=0, modifiers: dict[str,Any] | None=None) -> int:
        now=time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute("UPDATE world_eras SET active=0,ended_game_minute=? WHERE active=1",(int(game_minute),))
            cur=await db.execute(
                "INSERT INTO world_eras(name,description,started_game_minute,active,modifiers_json,created_at) VALUES(?,?,?,1,?,?)",
                (str(name)[:120],str(description)[:1000],int(game_minute),json.dumps(modifiers or {}),now),
            ); await db.commit(); return int(cur.lastrowid)

    async def create_party(self, leader_user_id: int, name: str) -> int:
        now=time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur=await db.execute("SELECT 1 FROM party_members pm JOIN parties p ON p.party_id=pm.party_id WHERE pm.user_id=? AND p.status='active'",(int(leader_user_id),))
            if await cur.fetchone(): await db.rollback(); raise ValueError("Already in an active party")
            cur=await db.execute("INSERT INTO parties(leader_user_id,name,status,created_at,updated_at) VALUES(?,?,'active',?,?)",(int(leader_user_id),str(name)[:80],now,now))
            pid=int(cur.lastrowid); await db.execute("INSERT INTO party_members(party_id,user_id,role,joined_at) VALUES(?,?,'leader',?)",(pid,int(leader_user_id),now)); await db.commit(); return pid

    async def get_party(self, user_id: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row
            cur=await db.execute("""SELECT p.* FROM parties p JOIN party_members pm ON pm.party_id=p.party_id WHERE pm.user_id=? AND p.status='active' ORDER BY p.party_id DESC LIMIT 1""",(int(user_id),)); p=await cur.fetchone()
            if not p: return None
            out=dict(p); cur=await db.execute("SELECT * FROM party_members WHERE party_id=? ORDER BY CASE role WHEN 'leader' THEN 0 ELSE 1 END,joined_at",(int(out['party_id']),)); out['members']=[dict(r) for r in await cur.fetchall()]; return out

    async def join_party(self, user_id: int, party_id: int) -> bool:
        if await self.get_party(user_id): return False
        async with self._connect() as db:
            cur=await db.execute("SELECT 1 FROM parties WHERE party_id=? AND status='active'",(int(party_id),))
            if not await cur.fetchone(): return False
            await db.execute("INSERT INTO party_members(party_id,user_id,role,joined_at) VALUES(?,?,'member',?)",(int(party_id),int(user_id),time.time())); await db.commit(); return True

    async def leave_party(self, user_id: int) -> bool:
        party=await self.get_party(user_id)
        if not party: return False
        pid=int(party['party_id']); leader=int(party['leader_user_id'])==int(user_id)
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute("DELETE FROM party_members WHERE party_id=? AND user_id=?",(pid,int(user_id)))
            # Formation assignments are party-scoped; leaving must not leave a
            # stale combat position behind. Any array with fewer than two
            # remaining assigned members automatically drops out of combat.
            await db.execute(
                "DELETE FROM formation_positions WHERE user_id=? AND formation_id IN (SELECT formation_id FROM party_formations WHERE party_id=?)",
                (int(user_id), pid),
            )
            await db.execute(
                """UPDATE party_formations SET active=0,updated_at=? WHERE party_id=? AND active=1 AND
                   (SELECT COUNT(*) FROM formation_positions fp WHERE fp.formation_id=party_formations.formation_id)<2""",
                (time.time(), pid),
            )
            if leader:
                cur=await db.execute("SELECT user_id FROM party_members WHERE party_id=? ORDER BY joined_at LIMIT 1",(pid,)); row=await cur.fetchone()
                if row:
                    new_leader=int(row[0]); await db.execute("UPDATE parties SET leader_user_id=?,updated_at=? WHERE party_id=?",(new_leader,time.time(),pid)); await db.execute("UPDATE party_members SET role='leader' WHERE party_id=? AND user_id=?",(pid,new_leader))
                else: await db.execute("UPDATE parties SET status='disbanded',updated_at=? WHERE party_id=?",(time.time(),pid))
            await db.commit(); return True

    async def get_hidden_sect_membership(self, user_id: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; cur=await db.execute("SELECT * FROM hidden_sect_membership WHERE user_id=?",(int(user_id),)); row=await cur.fetchone(); return dict(row) if row else None

    async def initiate_hidden_sect(self, user_id: int, *, sect_name: str, branch_name: str, game_minute: int) -> dict[str, Any]:
        now=time.time()
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO hidden_sect_membership(user_id,sect_name,rank_name,branch_name,standing,status,joined_game_minute,updated_at)
                   VALUES(?,?,'Shadow Initiate',?,0,'active',?,?)
                   ON CONFLICT(user_id) DO UPDATE SET sect_name=excluded.sect_name,branch_name=excluded.branch_name,
                       status='active',updated_at=excluded.updated_at""",
                (int(user_id),str(sect_name),str(branch_name),int(game_minute),now),
            ); await db.commit()
        return (await self.get_hidden_sect_membership(user_id)) or {}

    async def set_hidden_sect_status(self, user_id: int, status: str, *, standing_delta: int=0) -> dict[str, Any] | None:
        async with self._connect() as db:
            await db.execute("UPDATE hidden_sect_membership SET status=?,standing=standing+?,updated_at=? WHERE user_id=?",(str(status),int(standing_delta),time.time(),int(user_id))); await db.commit()
        return await self.get_hidden_sect_membership(user_id)

    async def create_pvp_challenge(self, challenger_user_id: int, target_user_id: int, *, stakes: str="honor", ttl_seconds: int=300) -> int:
        if int(challenger_user_id)==int(target_user_id): raise ValueError("Cannot challenge yourself")
        now=time.time()
        async with self._connect() as db:
            cur=await db.execute(
                """INSERT INTO pvp_challenges(challenger_user_id,target_user_id,stakes,status,created_at,expires_at)
                   VALUES(?,?,?,'pending',?,?)""",
                (int(challenger_user_id),int(target_user_id),str(stakes)[:200],now,now+max(60,int(ttl_seconds))),
            ); await db.commit(); return int(cur.lastrowid)

    async def respond_pvp_challenge(self, challenge_id: int, target_user_id: int, accept: bool) -> dict[str, Any] | None:
        now=time.time(); status='accepted' if accept else 'rejected'
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE"); db.row_factory=aiosqlite.Row
            cur=await db.execute("SELECT * FROM pvp_challenges WHERE challenge_id=? AND target_user_id=? AND status='pending'",(int(challenge_id),int(target_user_id))); row=await cur.fetchone()
            if not row or float(row['expires_at'])<now: await db.rollback(); return None
            out=dict(row)
            if accept:
                cur_active=await db.execute(
                    """SELECT 1 FROM pvp_matches WHERE status='active' AND (player1_user_id IN (?,?) OR player2_user_id IN (?,?)) LIMIT 1""",
                    (int(out['challenger_user_id']),int(out['target_user_id']),int(out['challenger_user_id']),int(out['target_user_id'])),
                )
                if await cur_active.fetchone(): await db.rollback(); return None
            await db.execute("UPDATE pvp_challenges SET status=? WHERE challenge_id=?",(status,int(challenge_id)))
            if accept:
                cur1=await db.execute("SELECT vitality_max FROM characters WHERE user_id=?",(int(out['challenger_user_id']),)); p1=await cur1.fetchone()
                cur2=await db.execute("SELECT vitality_max FROM characters WHERE user_id=?",(int(out['target_user_id']),)); p2=await cur2.fetchone()
                if not p1 or not p2: await db.rollback(); return None
                curm=await db.execute(
                    """INSERT INTO pvp_matches(challenge_id,player1_user_id,player2_user_id,player1_hp,player2_hp,turn_user_id,status,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,'active',?,?)""",
                    (int(challenge_id),int(out['challenger_user_id']),int(out['target_user_id']),max(1,int(p1[0])),max(1,int(p2[0])),int(out['challenger_user_id']),now,now),
                )
                out['match_id']=int(curm.lastrowid)
            await db.commit(); out['status']=status; return out

    async def get_pvp_challenges(self, user_id: int, *, pending_only: bool=False) -> list[dict[str, Any]]:
        sql="SELECT * FROM pvp_challenges WHERE (challenger_user_id=? OR target_user_id=?)"
        params:list[Any]=[int(user_id),int(user_id)]
        if pending_only: sql+=" AND status='pending' AND expires_at>=?"; params.append(time.time())
        sql+=" ORDER BY challenge_id DESC"
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; cur=await db.execute(sql,tuple(params)); return [dict(r) for r in await cur.fetchall()]

    async def get_pvp_match(self, user_id: int, match_id: int | None=None) -> dict[str, Any] | None:
        sql="SELECT * FROM pvp_matches WHERE (player1_user_id=? OR player2_user_id=?)"
        params:list[Any]=[int(user_id),int(user_id)]
        if match_id is not None: sql+=" AND match_id=?"; params.append(int(match_id))
        else: sql+=" AND status='active'"
        sql+=" ORDER BY match_id DESC LIMIT 1"
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; cur=await db.execute(sql,tuple(params)); row=await cur.fetchone(); return dict(row) if row else None

    async def apply_pvp_turn(self, match_id: int, acting_user_id: int, *, damage: int=0, guard: bool=False, surrender: bool=False, expected_version: int | None=None) -> dict[str, Any] | None:
        now=time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE"); db.row_factory=aiosqlite.Row
            cur=await db.execute("SELECT * FROM pvp_matches WHERE match_id=? AND status='active'",(int(match_id),)); row=await cur.fetchone()
            if not row: await db.rollback(); return None
            m=dict(row)
            if int(m['turn_user_id']) != int(acting_user_id): await db.rollback(); raise ValueError("It is not your turn")
            if expected_version is not None and int(m['version']) != int(expected_version): await db.rollback(); raise ValueError("Stale duel state")
            p1=int(m['player1_user_id']); p2=int(m['player2_user_id']); actor_is_p1=int(acting_user_id)==p1; opponent=p2 if actor_is_p1 else p1
            if surrender:
                await db.execute("UPDATE pvp_matches SET status='finished',winner_user_id=?,version=version+1,updated_at=? WHERE match_id=?",(opponent,now,int(match_id)))
            else:
                target_guard=int(m['player2_guard'] if actor_is_p1 else m['player1_guard'])
                final_damage=max(0,int(damage)-(2 if target_guard else 0))
                hp_field='player2_hp' if actor_is_p1 else 'player1_hp'; actor_guard='player1_guard' if actor_is_p1 else 'player2_guard'; target_guard_field='player2_guard' if actor_is_p1 else 'player1_guard'
                new_hp=max(0,int(m[hp_field])-final_damage)
                if new_hp<=0:
                    await db.execute(f"UPDATE pvp_matches SET {hp_field}=0,{actor_guard}=?,{target_guard_field}=0,status='finished',winner_user_id=?,version=version+1,updated_at=? WHERE match_id=?",(1 if guard else 0,int(acting_user_id),now,int(match_id)))
                else:
                    await db.execute(f"UPDATE pvp_matches SET {hp_field}=?,{actor_guard}=?,{target_guard_field}=0,turn_user_id=?,version=version+1,updated_at=? WHERE match_id=?",(new_hp,1 if guard else 0,opponent,now,int(match_id)))
            await db.commit()
        return await self.get_pvp_match(acting_user_id,match_id)


    # ------------------------------------------------------------------
    # Admin operations / automation / backups
    # ------------------------------------------------------------------
    async def log_admin_action(
        self,
        *,
        admin_user_id: int,
        action: str,
        target: str = "",
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        reason: str = "",
    ) -> int:
        async with self._connect() as db:
            cur = await db.execute(
                """INSERT INTO admin_audit_log(admin_user_id,action,target,before_json,after_json,reason,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (
                    int(admin_user_id), str(action)[:120], str(target)[:240],
                    json.dumps(before or {}, ensure_ascii=False),
                    json.dumps(after or {}, ensure_ascii=False),
                    str(reason)[:500], time.time(),
                ),
            )
            await db.commit()
            return int(cur.lastrowid)

    async def get_admin_audit_log(self, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 50))
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM admin_audit_log ORDER BY audit_id DESC LIMIT ?", (limit,)
            )
            rows = await cur.fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            for key in ("before_json", "after_json"):
                try:
                    item[key[:-5]] = json.loads(item.pop(key) or "{}")
                except Exception:
                    item[key[:-5]] = {}
            out.append(item)
        return out

    async def get_automation_settings(self) -> dict[str, bool]:
        defaults = {
            "event_expiry": True,
            "auction_settlement": True,
            "unexpected_events": True,
            "maintenance_cleanup": True,
            "npc_civilization": True,
            "npc_life": True,
            "sect_politics": True,
            "dynamic_economy": True,
            "clan_dynamics": True,
            "background_seclusion": True,
            "black_markets": True,
            "autonomous_world_events": True,
        }
        async with self._connect() as db:
            cur = await db.execute("SELECT value_json FROM world_state WHERE key='automation_settings'")
            row = await cur.fetchone()
        if not row:
            return defaults
        try:
            stored = json.loads(row[0])
        except Exception:
            return defaults
        return {key: bool(stored.get(key, value)) for key, value in defaults.items()}

    async def set_automation_setting(self, name: str, enabled: bool) -> dict[str, bool]:
        settings = await self.get_automation_settings()
        if name not in settings:
            raise ValueError(f"Unknown automation system: {name}")
        settings[name] = bool(enabled)
        now = time.time()
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO world_state(key,value_json,updated_at) VALUES('automation_settings',?,?)
                   ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
                (json.dumps(settings), now),
            )
            await db.commit()
        return settings

    async def admin_world_snapshot(self) -> dict[str, int]:
        queries = {
            "characters": "SELECT COUNT(*) FROM characters",
            "alive_characters": "SELECT COUNT(*) FROM characters WHERE life_status='alive'",
            "deceased_characters": "SELECT COUNT(*) FROM characters WHERE life_status!='alive'",
            "birth_families": "SELECT COUNT(*) FROM birth_families",
            "active_battles": "SELECT COUNT(*) FROM battles WHERE status='active'",
            "active_auctions": "SELECT COUNT(*) FROM auctions WHERE active=1",
            "active_events": "SELECT COUNT(*) FROM world_events WHERE active=1",
            "active_effects": "SELECT COUNT(*) FROM active_effects",
            "civilization_regions": "SELECT COUNT(*) FROM civilization_regions",
            "simulated_npcs": "SELECT COUNT(*) FROM npc_civilization_state WHERE status='alive'",
            "npc_marriages": "SELECT COUNT(*) FROM npc_life_state WHERE relationship_status='married'",
            "npc_social_relations": "SELECT COUNT(*) FROM npc_social_relations WHERE status='active'",
            "npc_disciple_bonds": "SELECT COUNT(*) FROM npc_disciple_bonds WHERE status='active'",
            "npc_descendants": "SELECT COUNT(*) FROM npc_descendants WHERE status='alive'",
            "npc_injured": "SELECT COUNT(*) FROM npc_life_state l JOIN npc_civilization_state c ON c.npc_name=l.npc_name WHERE c.status='alive' AND l.injury_severity>0",
            "world_history_events": "SELECT COUNT(*) FROM world_history_events",
            "sect_factions": "SELECT COUNT(*) FROM sect_factions",
            "market_entries": "SELECT COUNT(*) FROM economy_markets",
            "clan_branches": "SELECT COUNT(*) FROM martial_clan_branches WHERE status='active'",
            "retainer_groups": "SELECT COUNT(*) FROM martial_clan_retainers WHERE status='active'",
            "clan_relations": "SELECT COUNT(*) FROM martial_clan_relations WHERE active=1",
            "active_seclusions": "SELECT COUNT(*) FROM seclusion_sessions WHERE status='active'",
            "world_action_events": "SELECT COUNT(*) FROM world_action_events",
            "active_conditions": "SELECT COUNT(*) FROM character_conditions WHERE state='active'",
            "cleared_tribulations": "SELECT COUNT(*) FROM tribulation_state WHERE cleared=1",
            "profession_records": "SELECT COUNT(*) FROM profession_progress",
            "open_crimes": "SELECT COUNT(*) FROM crime_records WHERE status='open'",
            "active_bounties": "SELECT COUNT(*) FROM bounties WHERE status='active'",
            "active_grudges": "SELECT COUNT(*) FROM grudges WHERE status='active'",
            "spirit_beasts": "SELECT COUNT(*) FROM spirit_beasts",
            "artifact_bonds": "SELECT COUNT(*) FROM artifact_bonds",
            "territories": "SELECT COUNT(*) FROM territory_state",
            "active_wars": "SELECT COUNT(*) FROM territory_wars WHERE status='active'",
            "traveling_caravans": "SELECT COUNT(*) FROM caravans WHERE status='traveling'",
            "provenance_records": "SELECT COUNT(*) FROM item_provenance",
            "active_parties": "SELECT COUNT(*) FROM parties WHERE status='active'",
            "equipped_items": "SELECT COUNT(*) FROM equipment_instances WHERE equipped=1",
            "active_boss_encounters": "SELECT COUNT(*) FROM boss_encounters WHERE status='active'",
            "active_hunter_pursuits": "SELECT COUNT(*) FROM bounty_hunter_pursuits WHERE status IN ('tracking','engaged')",
            "active_formations": "SELECT COUNT(*) FROM party_formations WHERE active=1",
            "slow_queries_1h": "SELECT COUNT(*) FROM slow_query_log WHERE created_at >= strftime('%s','now') - 3600",
            "operational_alerts": "SELECT COUNT(*) FROM operational_alerts",
        }
        out: dict[str, int] = {}
        async with self._connect() as db:
            for key, sql in queries.items():
                cur = await db.execute(sql)
                row = await cur.fetchone()
                out[key] = int(row[0]) if row else 0
        return out

    # ------------------------------------------------------------------
    # Advanced world/combat systems (schema v2)
    # ------------------------------------------------------------------
    async def get_equipment(self, user_id: int, *, equipped_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM equipment_instances WHERE user_id=?"
        params: list[Any] = [int(user_id)]
        if equipped_only:
            sql += " AND equipped=1"
        sql += " ORDER BY equipped DESC,slot,equipment_id"
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(sql, tuple(params))
            return [dict(r) for r in await cur.fetchall()]

    async def bind_equipment(self, user_id: int, item_id: str) -> dict[str, Any]:
        definition = equipment_definition(item_id)
        if not definition:
            raise ValueError("That item is not part of the equipment catalog")
        now = time.time()
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT quantity FROM inventory WHERE user_id=? AND item_id=?", (int(user_id), str(item_id))
            )
            row = await cur.fetchone()
            if not row or int(row[0]) <= 0:
                await db.rollback()
                raise ValueError("You do not carry that item")
            if int(row[0]) == 1:
                await db.execute("DELETE FROM inventory WHERE user_id=? AND item_id=?", (int(user_id), str(item_id)))
            else:
                await db.execute(
                    "UPDATE inventory SET quantity=quantity-1 WHERE user_id=? AND item_id=?",
                    (int(user_id), str(item_id)),
                )
            cur = await db.execute(
                """INSERT INTO equipment_instances(user_id,item_id,slot,durability,max_durability,quality,equipped,bound_at,updated_at)
                   VALUES(?,?,?,?,?,100,0,?,?)""",
                (
                    int(user_id), str(item_id), str(definition["slot"]), int(definition["max_durability"]),
                    int(definition["max_durability"]), now, now,
                ),
            )
            equipment_id = int(cur.lastrowid)
            await db.commit()
        return next(x for x in await self.get_equipment(user_id) if int(x["equipment_id"]) == equipment_id)

    async def equip_item(self, user_id: int, equipment_id: int) -> dict[str, Any] | None:
        now = time.time()
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT * FROM equipment_instances WHERE user_id=? AND equipment_id=?",
                (int(user_id), int(equipment_id)),
            )
            row = await cur.fetchone()
            if not row or int(row["durability"]) <= 0:
                await db.rollback()
                return None
            slot = str(row["slot"])
            await db.execute("UPDATE equipment_instances SET equipped=0,updated_at=? WHERE user_id=? AND slot=?", (now, int(user_id), slot))
            await db.execute("UPDATE equipment_instances SET equipped=1,updated_at=? WHERE user_id=? AND equipment_id=?", (now, int(user_id), int(equipment_id)))
            await db.commit()
        return next((x for x in await self.get_equipment(user_id) if int(x["equipment_id"]) == int(equipment_id)), None)

    async def unequip_item(self, user_id: int, equipment_id: int) -> bool:
        async with self._connect() as db:
            cur = await db.execute(
                "UPDATE equipment_instances SET equipped=0,updated_at=? WHERE user_id=? AND equipment_id=?",
                (time.time(), int(user_id), int(equipment_id)),
            )
            await db.commit()
            return int(cur.rowcount or 0) > 0

    async def repair_equipment(self, user_id: int, equipment_id: int) -> dict[str, Any] | None:
        now = time.time()
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                "SELECT * FROM equipment_instances WHERE user_id=? AND equipment_id=?",
                (int(user_id), int(equipment_id)),
            )
            row = await cur.fetchone()
            if not row:
                await db.rollback(); return None
            missing = max(0, int(row["max_durability"]) - int(row["durability"]))
            if missing <= 0:
                await db.rollback()
                out = dict(row); out["repair_cost"] = 0; return out
            cost = max(1, (missing + 19) // 20)
            cur = await db.execute("SELECT quantity FROM inventory WHERE user_id=? AND item_id='spirit_iron'", (int(user_id),))
            ore = await cur.fetchone()
            if not ore or int(ore[0]) < cost:
                await db.rollback()
                raise ValueError(f"Repair requires {cost} Spirit Iron")
            if int(ore[0]) == cost:
                await db.execute("DELETE FROM inventory WHERE user_id=? AND item_id='spirit_iron'", (int(user_id),))
            else:
                await db.execute("UPDATE inventory SET quantity=quantity-? WHERE user_id=? AND item_id='spirit_iron'", (cost, int(user_id)))
            await db.execute(
                "UPDATE equipment_instances SET durability=max_durability,updated_at=? WHERE user_id=? AND equipment_id=?",
                (now, int(user_id), int(equipment_id)),
            )
            await db.commit()
        out = next((x for x in await self.get_equipment(user_id) if int(x["equipment_id"]) == int(equipment_id)), None)
        if out is not None: out["repair_cost"] = cost
        return out

    async def damage_equipment(self, user_id: int, amount: int = 1) -> list[dict[str, Any]]:
        amount = max(0, int(amount))
        if amount <= 0:
            return await self.get_equipment(user_id, equipped_only=True)
        now = time.time()
        async with self._connect() as db:
            await db.execute(
                "UPDATE equipment_instances SET durability=MAX(0,durability-?),updated_at=? WHERE user_id=? AND equipped=1",
                (amount, now, int(user_id)),
            )
            await db.execute(
                "UPDATE equipment_instances SET equipped=0,updated_at=? WHERE user_id=? AND durability<=0",
                (now, int(user_id)),
            )
            await db.commit()
        return await self.get_equipment(user_id)

    async def equipment_bonus(self, user_id: int) -> dict[str, int]:
        return equipment_power(await self.get_equipment(user_id, equipped_only=True))

    async def create_formation(self, party_id: int, name: str) -> int:
        now = time.time()
        async with self._connect() as db:
            cur = await db.execute("SELECT 1 FROM parties WHERE party_id=? AND status='active'", (int(party_id),))
            if not await cur.fetchone():
                raise ValueError("Active party not found")
            cur = await db.execute(
                "INSERT INTO party_formations(party_id,name,stance,cohesion,active,created_at,updated_at) VALUES(?,?,'balanced',100,0,?,?)",
                (int(party_id), str(name)[:80], now, now),
            )
            await db.commit(); return int(cur.lastrowid)

    async def get_formations(self, party_id: int) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM party_formations WHERE party_id=? ORDER BY active DESC,formation_id", (int(party_id),))
            rows = [dict(r) for r in await cur.fetchall()]
            for row in rows:
                cur = await db.execute("SELECT * FROM formation_positions WHERE formation_id=? ORDER BY position", (int(row["formation_id"]),))
                row["positions"] = [dict(r) for r in await cur.fetchall()]
            return rows

    async def get_active_formation(self, party_id: int) -> dict[str, Any] | None:
        rows = await self.get_formations(party_id)
        return next((x for x in rows if int(x.get("active", 0)) == 1), None)

    async def assign_formation_position(self, party_id: int, formation_id: int, user_id: int, position: str) -> bool:
        position = str(position).lower()
        if position not in FORMATION_POSITIONS:
            raise ValueError("Unknown formation position")
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute("SELECT 1 FROM party_formations WHERE party_id=? AND formation_id=?", (int(party_id), int(formation_id)))
            if not await cur.fetchone(): await db.rollback(); return False
            cur = await db.execute("SELECT 1 FROM party_members WHERE party_id=? AND user_id=?", (int(party_id), int(user_id)))
            if not await cur.fetchone(): await db.rollback(); return False
            await db.execute("DELETE FROM formation_positions WHERE formation_id=? AND (user_id=? OR position=?)", (int(formation_id), int(user_id), position))
            await db.execute(
                "INSERT INTO formation_positions(formation_id,user_id,position,assigned_at) VALUES(?,?,?,?)",
                (int(formation_id), int(user_id), position, now),
            )
            await db.commit(); return True

    async def activate_formation(self, party_id: int, formation_id: int, *, stance: str = "balanced") -> dict[str, Any] | None:
        stance = str(stance).lower()
        if stance not in FORMATION_STANCES:
            raise ValueError("Unknown formation stance")
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute("SELECT COUNT(*) FROM formation_positions WHERE formation_id=?", (int(formation_id),))
            assigned = int((await cur.fetchone())[0])
            cur = await db.execute("SELECT 1 FROM party_formations WHERE party_id=? AND formation_id=?", (int(party_id), int(formation_id)))
            if not await cur.fetchone() or assigned < 2:
                await db.rollback(); return None
            await db.execute("UPDATE party_formations SET active=0,updated_at=? WHERE party_id=?", (now, int(party_id)))
            await db.execute(
                "UPDATE party_formations SET active=1,stance=?,cohesion=MAX(50,cohesion),updated_at=? WHERE party_id=? AND formation_id=?",
                (stance, now, int(party_id), int(formation_id)),
            )
            await db.commit()
        return await self.get_active_formation(party_id)

    async def set_formation_stance(self, party_id: int, stance: str) -> dict[str, Any] | None:
        stance = str(stance).lower()
        if stance not in FORMATION_STANCES:
            raise ValueError("Unknown formation stance")
        async with self._connect() as db:
            cur = await db.execute(
                "UPDATE party_formations SET stance=?,updated_at=? WHERE party_id=? AND active=1",
                (stance, time.time(), int(party_id)),
            )
            await db.commit()
            if int(cur.rowcount or 0) <= 0: return None
        return await self.get_active_formation(party_id)

    async def _formation_combat_bonus_locked(self, db: Any, party_id: int, user_id: int) -> dict[str, int]:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM party_formations WHERE party_id=? AND active=1 ORDER BY formation_id DESC LIMIT 1", (int(party_id),))
        formation = await cur.fetchone()
        if not formation:
            return {"attack": 0, "defense": 0, "support": 0, "cohesion_cost": 0}
        cur = await db.execute("SELECT position FROM formation_positions WHERE formation_id=? AND user_id=?", (int(formation["formation_id"]), int(user_id)))
        row = await cur.fetchone()
        return formation_bonus(str(row[0]) if row else None, str(formation["stance"]), int(formation["cohesion"]))

    async def start_boss_encounter(self, party_id: int, template_key: str, *, game_minute: int) -> dict[str, Any]:
        template = BOSS_TEMPLATES.get(str(template_key))
        if not template:
            raise ValueError("Unknown boss template")
        now = time.time()
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute("SELECT * FROM parties WHERE party_id=? AND status='active'", (int(party_id),))
            party = await cur.fetchone()
            if not party: await db.rollback(); raise ValueError("Active party not found")
            cur = await db.execute("SELECT 1 FROM boss_encounters WHERE party_id=? AND status='active'", (int(party_id),))
            if await cur.fetchone(): await db.rollback(); raise ValueError("Party already has an active boss encounter")
            cur = await db.execute(
                """SELECT pm.user_id,c.vitality_max,c.life_status,c.location FROM party_members pm
                   JOIN characters c ON c.user_id=pm.user_id WHERE pm.party_id=? ORDER BY pm.joined_at""",
                (int(party_id),),
            )
            members = await cur.fetchall()
            if not members: await db.rollback(); raise ValueError("Party has no members")
            if any(str(r["life_status"]) != "alive" for r in members): await db.rollback(); raise ValueError("Every party member must be alive")
            if any(str(r["location"]) != str(template["location"]) for r in members):
                await db.rollback(); raise ValueError(f"Every party member must be at {template['location']}")
            scale = 0.8 + 0.2 * len(members)
            boss_max = max(1, int(round(int(template["max_hp"]) * scale)))
            cur = await db.execute(
                """INSERT INTO boss_encounters(party_id,template_key,location,boss_name,boss_hp,boss_hp_max,phase_index,round_index,status,version,started_game_minute,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,0,1,'active',0,?,?,?)""",
                (int(party_id), str(template_key), str(template["location"]), str(template["name"]), boss_max, boss_max, int(game_minute), now, now),
            )
            encounter_id = int(cur.lastrowid)
            for member in members:
                hp = max(1, int(member["vitality_max"]))
                await db.execute(
                    "INSERT INTO boss_participants(encounter_id,user_id,vitality,vitality_max,acted_round,total_damage,guard,status,updated_at) VALUES(?,?,?,?,0,0,0,'active',?)",
                    (encounter_id, int(member["user_id"]), hp, hp, now),
                )
            await db.commit()
        return await self.get_boss_encounter(encounter_id=encounter_id)

    async def get_boss_encounter(self, *, user_id: int | None = None, party_id: int | None = None, encounter_id: int | None = None) -> dict[str, Any] | None:
        where = []
        params: list[Any] = []
        join = ""
        if encounter_id is not None:
            where.append("be.encounter_id=?"); params.append(int(encounter_id))
        elif party_id is not None:
            where.extend(["be.party_id=?", "be.status='active'"]); params.append(int(party_id))
        elif user_id is not None:
            join = " JOIN boss_participants bp0 ON bp0.encounter_id=be.encounter_id "
            where.extend(["bp0.user_id=?", "be.status='active'"]); params.append(int(user_id))
        else:
            raise ValueError("encounter lookup requires an id")
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT be.* FROM boss_encounters be" + join + " WHERE " + " AND ".join(where) + " ORDER BY be.encounter_id DESC LIMIT 1",
                tuple(params),
            )
            row = await cur.fetchone()
            if not row: return None
            out = dict(row)
            cur = await db.execute("SELECT * FROM boss_participants WHERE encounter_id=? ORDER BY user_id", (int(out["encounter_id"]),))
            out["participants"] = [dict(r) for r in await cur.fetchall()]
            template = BOSS_TEMPLATES.get(str(out["template_key"]), {})
            phases = list(template.get("phases") or [])
            out["phase"] = phases[min(max(0, int(out["phase_index"])), max(0, len(phases)-1))] if phases else {}
            return out

    async def boss_action(
        self, encounter_id: int, user_id: int, *, style: str = "attack",
        expected_version: int | None = None, game_minute: int | None = None,
    ) -> dict[str, Any] | None:
        style = str(style).lower()
        if style not in {"attack", "technique", "defend", "support"}:
            raise ValueError("Unknown boss action")
        now = time.time(); events: list[str] = []
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute("SELECT * FROM boss_encounters WHERE encounter_id=? AND status='active'", (int(encounter_id),))
            encounter = await cur.fetchone()
            if not encounter: await db.rollback(); return None
            if expected_version is not None and int(encounter["version"]) != int(expected_version): await db.rollback(); return None
            round_index = int(encounter["round_index"])
            cur = await db.execute("SELECT * FROM boss_participants WHERE encounter_id=? AND user_id=? AND status='active'", (int(encounter_id), int(user_id)))
            participant = await cur.fetchone()
            if not participant or int(participant["acted_round"]) >= round_index: await db.rollback(); return None
            cur = await db.execute("SELECT * FROM characters WHERE user_id=?", (int(user_id),))
            character = await cur.fetchone()
            if not character: await db.rollback(); return None
            try: attrs = json.loads(character["attributes_json"] or "{}")
            except Exception: attrs = {}
            cur = await db.execute("SELECT * FROM equipment_instances WHERE user_id=? AND equipped=1", (int(user_id),))
            equip_rows = [dict(r) for r in await cur.fetchall()]
            equip = equipment_power(equip_rows)
            formation = await self._formation_combat_bonus_locked(db, int(encounter["party_id"]), int(user_id))
            template = BOSS_TEMPLATES[str(encounter["template_key"])]
            phase_idx = int(encounter["phase_index"])
            phase = list(template["phases"])[phase_idx]
            base_attack = max(int(attrs.get("body", 0)), int(attrs.get("spirit", 0))) + int(character["realm_index"]) * 2 + int(character["phase"]) // 2 + equip["attack"] + formation["attack"]
            damage = 0
            if style == "defend":
                await db.execute("UPDATE boss_participants SET guard=1,acted_round=?,updated_at=? WHERE encounter_id=? AND user_id=?", (round_index, now, int(encounter_id), int(user_id)))
                events.append("You brace within the formation and prepare to absorb the boss counterattack.")
            elif style == "support":
                cur = await db.execute("SELECT user_id,vitality,vitality_max FROM boss_participants WHERE encounter_id=? AND status='active' ORDER BY vitality*1.0/vitality_max ASC,user_id LIMIT 1", (int(encounter_id),))
                target = await cur.fetchone()
                heal = max(2, 2 + int(attrs.get("spirit", 0)) // 2 + formation["support"])
                if target:
                    await db.execute("UPDATE boss_participants SET vitality=MIN(vitality_max,vitality+?),updated_at=? WHERE encounter_id=? AND user_id=?", (heal, now, int(encounter_id), int(target["user_id"])))
                    events.append(f"Support restores {heal} raid vitality to <@{int(target['user_id'])}>.")
                await db.execute("UPDATE boss_participants SET acted_round=?,updated_at=? WHERE encounter_id=? AND user_id=?", (round_index, now, int(encounter_id), int(user_id)))
            else:
                style_bonus = 5 if style == "technique" else 1
                roll = stable_percent(encounter_id, round_index, user_id, style, int(encounter["version"]))
                accuracy = 65 + int(attrs.get("agility", 0)) * 2 + equip["agility"] - int(phase.get("defense", 0)) * 2
                if roll < max(15, min(95, accuracy)):
                    damage = max(1, base_attack + style_bonus + (100 - roll) // 20 - int(phase.get("defense", 0)))
                await db.execute("UPDATE boss_encounters SET boss_hp=MAX(0,boss_hp-?),updated_at=? WHERE encounter_id=?", (damage, now, int(encounter_id)))
                await db.execute("UPDATE boss_participants SET acted_round=?,total_damage=total_damage+?,updated_at=? WHERE encounter_id=? AND user_id=?", (round_index, damage, now, int(encounter_id), int(user_id)))
                events.append(f"{style.title()} deals {damage} damage." if damage else f"{style.title()} fails to penetrate the boss defense.")
                await db.execute("UPDATE equipment_instances SET durability=MAX(0,durability-1),updated_at=? WHERE user_id=? AND equipped=1", (now, int(user_id)))
                await db.execute("UPDATE equipment_instances SET equipped=0,updated_at=? WHERE user_id=? AND durability<=0", (now, int(user_id)))

            cur = await db.execute("SELECT boss_hp,boss_hp_max FROM boss_encounters WHERE encounter_id=?", (int(encounter_id),))
            hp_row = await cur.fetchone(); boss_hp = int(hp_row[0]); boss_max = int(hp_row[1])
            if boss_hp <= 0:
                await db.execute("UPDATE boss_encounters SET status='victory',winner_party_id=party_id,finished_game_minute=?,version=version+1,updated_at=? WHERE encounter_id=?", (int(game_minute if game_minute is not None else encounter["started_game_minute"]), now, int(encounter_id)))
                cur = await db.execute("SELECT user_id FROM boss_participants WHERE encounter_id=?", (int(encounter_id),))
                for r in await cur.fetchall():
                    await db.execute(
                        """INSERT OR IGNORE INTO boss_reward_claims(encounter_id,user_id,currency_amount,item_id,item_quantity,claimed,created_at)
                           VALUES(?,?,?,?,?,0,?)""",
                        (int(encounter_id), int(r[0]), int(template["reward_currency"]), str(template["reward_item"]), int(template["reward_quantity"]), now),
                    )
                events.append(f"{template['name']} is defeated. Raid rewards are ready to claim.")
            else:
                ratio = boss_hp / max(1, boss_max)
                phases = list(template["phases"])
                new_phase = len(phases) - 1
                for idx, ph in enumerate(phases):
                    if ratio > float(ph["threshold"]):
                        new_phase = idx; break
                if new_phase != phase_idx:
                    await db.execute("UPDATE boss_encounters SET phase_index=?,version=version+1,updated_at=? WHERE encounter_id=?", (new_phase, now, int(encounter_id)))
                    phase_idx = new_phase; phase = phases[new_phase]
                    events.append(f"Boss phase shifts to **{phase['name']}**.")
                cur = await db.execute("SELECT COUNT(*) FROM boss_participants WHERE encounter_id=? AND status='active' AND acted_round<?", (int(encounter_id), round_index))
                pending = int((await cur.fetchone())[0])
                if pending == 0:
                    phase = phases[phase_idx]
                    cur = await db.execute("SELECT * FROM boss_participants WHERE encounter_id=? AND status='active'", (int(encounter_id),))
                    targets = await cur.fetchall()
                    for target in targets:
                        tid = int(target["user_id"])
                        cur2 = await db.execute("SELECT attributes_json,realm_index,phase FROM characters WHERE user_id=?", (tid,))
                        crow = await cur2.fetchone()
                        try: tattrs = json.loads(crow["attributes_json"] or "{}") if crow else {}
                        except Exception: tattrs = {}
                        cur2 = await db.execute("SELECT * FROM equipment_instances WHERE user_id=? AND equipped=1", (tid,))
                        t_equip = equipment_power([dict(r) for r in await cur2.fetchall()])
                        t_form = await self._formation_combat_bonus_locked(db, int(encounter["party_id"]), tid)
                        defense = int(tattrs.get("body", 0)) + int(crow["realm_index"] if crow else 0) + t_equip["defense"] + t_form["defense"]
                        incoming = max(1, int(phase["attack"]) + stable_percent(encounter_id, round_index, tid, "boss") // 20 - defense // 2)
                        if int(target["guard"]): incoming = max(1, incoming // 2)
                        new_vitality = max(0, int(target["vitality"]) - incoming)
                        status = "knocked_out" if new_vitality <= 0 else "active"
                        await db.execute("UPDATE boss_participants SET vitality=?,status=?,guard=0,updated_at=? WHERE encounter_id=? AND user_id=?", (new_vitality, status, now, int(encounter_id), tid))
                        await db.execute("UPDATE equipment_instances SET durability=MAX(0,durability-1),updated_at=? WHERE user_id=? AND equipped=1", (now, tid))
                        await db.execute("UPDATE equipment_instances SET equipped=0,updated_at=? WHERE user_id=? AND durability<=0", (now, tid))
                        events.append(f"{phase['name']} hits <@{tid}> for {incoming} raid vitality.")
                    cur = await db.execute("SELECT COUNT(*) FROM boss_participants WHERE encounter_id=? AND status='active'", (int(encounter_id),))
                    survivors = int((await cur.fetchone())[0])
                    cur = await db.execute("SELECT formation_id,cohesion,stance FROM party_formations WHERE party_id=? AND active=1", (int(encounter["party_id"]),))
                    frow = await cur.fetchone()
                    if frow:
                        stance_cost = int(FORMATION_STANCES.get(str(frow["stance"]), FORMATION_STANCES["balanced"])["cohesion_cost"])
                        loss = int(phase.get("cohesion_damage", 0)) + stance_cost
                        await db.execute("UPDATE party_formations SET cohesion=MAX(0,cohesion-?),updated_at=? WHERE formation_id=?", (loss, now, int(frow["formation_id"])))
                        events.append(f"Formation cohesion falls by {loss}.")
                    if survivors <= 0:
                        await db.execute("UPDATE boss_encounters SET status='defeat',finished_game_minute=?,version=version+1,updated_at=? WHERE encounter_id=?", (int(game_minute if game_minute is not None else encounter["started_game_minute"]), now, int(encounter_id)))
                        events.append("The raid is defeated; the boss encounter closes without true death.")
                    else:
                        await db.execute("UPDATE boss_encounters SET round_index=round_index+1,version=version+1,updated_at=? WHERE encounter_id=?", (now, int(encounter_id)))
                else:
                    await db.execute("UPDATE boss_encounters SET version=version+1,updated_at=? WHERE encounter_id=?", (now, int(encounter_id)))
            await db.commit()
        out = await self.get_boss_encounter(encounter_id=int(encounter_id))
        if out is not None: out["events"] = events
        return out

    async def claim_boss_reward(self, encounter_id: int, user_id: int) -> dict[str, Any] | None:
        now = time.time()
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute("SELECT * FROM boss_reward_claims WHERE encounter_id=? AND user_id=? AND claimed=0", (int(encounter_id), int(user_id)))
            row = await cur.fetchone()
            if not row: await db.rollback(); return None
            amount = int(row["currency_amount"]); item_id = str(row["item_id"]); qty = int(row["item_quantity"])
            if amount: await self._wallet_delta(db, int(user_id), "low_spirit_stone", amount)
            if item_id and qty:
                await db.execute(
                    """INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,?)
                       ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=inventory.quantity+excluded.quantity""",
                    (int(user_id), item_id, qty),
                )
            await db.execute("UPDATE boss_reward_claims SET claimed=1,claimed_at=? WHERE encounter_id=? AND user_id=?", (now, int(encounter_id), int(user_id)))
            await db.commit()
            return {"currency_amount": amount, "item_id": item_id, "item_quantity": qty}

    async def spawn_bounty_hunter_pursuits(self, game_minute: int) -> list[dict[str, Any]]:
        now = time.time(); created: list[int] = []
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                """SELECT b.*,c.realm_index FROM bounties b JOIN characters c ON c.user_id=b.user_id
                   WHERE b.status='active' AND NOT EXISTS(
                       SELECT 1 FROM bounty_hunter_pursuits p WHERE p.bounty_id=b.bounty_id
                         AND (p.status IN ('tracking','engaged') OR p.updated_game_minute>?)
                   )""",
                (int(game_minute) - 7 * 1440,),
            )
            for row in await cur.fetchall():
                power = max(1, int(row["realm_index"]) * 2 + max(1, int(row["amount"]) // 100))
                title = BOUNTY_HUNTER_TITLES[stable_percent(row["bounty_id"], row["jurisdiction"]) % len(BOUNTY_HUNTER_TITLES)]
                cur2 = await db.execute(
                    """INSERT INTO bounty_hunter_pursuits(bounty_id,user_id,hunter_name,hunter_power,status,pressure,escape_progress,capture_progress,next_action_game_minute,created_game_minute,updated_game_minute,created_at,updated_at)
                       VALUES(?,?,?,?,'tracking',10,0,0,?,?,?,?,?)""",
                    (int(row["bounty_id"]), int(row["user_id"]), title, power, int(game_minute)+1440, int(game_minute), int(game_minute), now, now),
                )
                created.append(int(cur2.lastrowid))
            await db.commit()
        out: list[dict[str, Any]] = []
        for pid in created:
            row = await self.get_bounty_hunter_pursuit(pursuit_id=pid)
            if row: out.append(row)
        return out

    async def get_bounty_hunter_pursuit(self, *, user_id: int | None = None, pursuit_id: int | None = None, active_only: bool = True) -> dict[str, Any] | None:
        sql = "SELECT p.*,b.jurisdiction,b.amount,b.reason FROM bounty_hunter_pursuits p JOIN bounties b ON b.bounty_id=p.bounty_id WHERE 1=1"
        params: list[Any] = []
        if pursuit_id is not None: sql += " AND p.pursuit_id=?"; params.append(int(pursuit_id))
        if user_id is not None: sql += " AND p.user_id=?"; params.append(int(user_id))
        if active_only: sql += " AND p.status IN ('tracking','engaged')"
        sql += " ORDER BY p.pursuit_id DESC LIMIT 1"
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row; cur = await db.execute(sql, tuple(params)); row = await cur.fetchone(); return dict(row) if row else None

    async def advance_bounty_hunters(self, game_minute: int) -> list[dict[str, Any]]:
        now = time.time(); changed: list[int] = []
        era = await self.get_current_era()
        crime_pressure = max(0.25, float((era or {}).get("modifiers", {}).get("crime_pressure", 1.0)))
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            await db.execute(
                """UPDATE bounty_hunter_pursuits SET status='withdrawn',updated_game_minute=?,updated_at=?
                   WHERE status IN ('tracking','engaged') AND bounty_id IN (SELECT bounty_id FROM bounties WHERE status!='active')""",
                (int(game_minute), now),
            )
            cur = await db.execute("SELECT * FROM bounty_hunter_pursuits WHERE status IN ('tracking','engaged') AND next_action_game_minute<=?", (int(game_minute),))
            for row in await cur.fetchall():
                elapsed = max(1, (int(game_minute) - int(row["next_action_game_minute"])) // 1440 + 1)
                pressure = min(100, int(row["pressure"]) + int(round(elapsed * (8 + int(row["hunter_power"])) * crime_pressure)))
                capture = int(row["capture_progress"])
                status = str(row["status"])
                if pressure >= 65: status = "engaged"; capture = min(100, capture + int(round(elapsed * max(5, int(row["hunter_power"])) * crime_pressure)))
                if capture >= 100: status = "captured"
                await db.execute(
                    "UPDATE bounty_hunter_pursuits SET status=?,pressure=?,capture_progress=?,next_action_game_minute=?,updated_game_minute=?,updated_at=? WHERE pursuit_id=?",
                    (status, pressure, capture, int(game_minute)+1440, int(game_minute), now, int(row["pursuit_id"])),
                )
                if status == "captured":
                    await db.execute(
                        "UPDATE bounties SET status='resolved',updated_at=? WHERE bounty_id=? AND status='active'",
                        (now, int(row["bounty_id"])),
                    )
                    await db.execute(
                        "UPDATE crime_records SET status='captured',updated_at=? WHERE crime_id=(SELECT source_crime_id FROM bounties WHERE bounty_id=?) AND status='open'",
                        (now, int(row["bounty_id"])),
                    )
                changed.append(int(row["pursuit_id"]))
            await db.commit()
        return [r for pid in changed if (r := await self.get_bounty_hunter_pursuit(pursuit_id=pid, active_only=False))]

    async def bounty_hunter_action(self, user_id: int, pursuit_id: int, action: str, *, game_minute: int) -> dict[str, Any] | None:
        action = str(action).lower()
        if action not in {"evade", "fight", "surrender"}: raise ValueError("Unknown hunter action")
        now = time.time()
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row; await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute("SELECT * FROM bounty_hunter_pursuits WHERE pursuit_id=? AND user_id=? AND status IN ('tracking','engaged')", (int(pursuit_id), int(user_id)))
            p = await cur.fetchone()
            if not p: await db.rollback(); return None
            if action == "surrender":
                await db.execute("UPDATE bounty_hunter_pursuits SET status='surrendered',capture_progress=100,updated_game_minute=?,updated_at=? WHERE pursuit_id=?", (int(game_minute), now, int(pursuit_id)))
                await db.execute(
                    "UPDATE bounties SET status='resolved',updated_at=? WHERE bounty_id=? AND status='active'",
                    (now, int(p["bounty_id"])),
                )
                await db.execute(
                    "UPDATE crime_records SET status='surrendered',updated_at=? WHERE crime_id=(SELECT source_crime_id FROM bounties WHERE bounty_id=?) AND status='open'",
                    (now, int(p["bounty_id"])),
                )
            else:
                cur = await db.execute("SELECT realm_index,phase,attributes_json FROM characters WHERE user_id=?", (int(user_id),)); c = await cur.fetchone()
                try: attrs = json.loads(c["attributes_json"] or "{}") if c else {}
                except Exception: attrs = {}
                cur = await db.execute("SELECT * FROM equipment_instances WHERE user_id=? AND equipped=1", (int(user_id),)); equip = equipment_power([dict(r) for r in await cur.fetchall()])
                if action == "evade":
                    power = int(attrs.get("agility", 0)) * 3 + int(c["realm_index"] if c else 0) * 2 + equip["agility"]
                else:
                    power = max(int(attrs.get("body", 0)), int(attrs.get("spirit", 0))) * 3 + int(c["realm_index"] if c else 0) * 2 + equip["attack"]
                gain = max(5, 20 + power - int(p["hunter_power"]) * 2 + stable_percent(pursuit_id, user_id, action, game_minute) // 10)
                escape = min(100, int(p["escape_progress"]) + gain)
                pressure = max(0, int(p["pressure"]) - gain // 2)
                status = "evaded" if escape >= 100 else str(p["status"])
                if action == "fight" and escape >= 100: status = "defeated"
                await db.execute("UPDATE bounty_hunter_pursuits SET status=?,escape_progress=?,pressure=?,next_action_game_minute=?,updated_game_minute=?,updated_at=? WHERE pursuit_id=?", (status, escape, pressure, int(game_minute)+1440, int(game_minute), now, int(pursuit_id)))
                if action == "fight":
                    await db.execute("UPDATE equipment_instances SET durability=MAX(0,durability-1),updated_at=? WHERE user_id=? AND equipped=1", (now, int(user_id)))
            await db.commit()
        return await self.get_bounty_hunter_pursuit(pursuit_id=int(pursuit_id), active_only=False)

    async def _ensure_war_operation_locked(self, db: Any, war_id: int, game_minute: int) -> None:
        await db.execute(
            """INSERT INTO territory_war_operations(war_id,siege_progress,attacker_morale,defender_morale,attacker_force,defender_force,last_tick_game_minute,winner_key,resolution,occupation_until_game_minute,updated_at)
               VALUES(?,0,100,100,0,0,?,'','',0,?) ON CONFLICT(war_id) DO NOTHING""",
            (int(war_id), int(game_minute), time.time()),
        )

    async def territory_war_action(self, war_id: int, user_id: int | None, *, side: str, tactic: str, power: int, game_minute: int) -> dict[str, Any] | None:
        side = str(side).lower(); tactic = str(tactic).lower(); power = max(1, int(power)); now = time.time()
        era = await self.get_current_era()
        power = max(1, int(round(power * max(0.25, float((era or {}).get("modifiers", {}).get("war_pressure", 1.0))))))
        if side not in {"attacker", "defender"}: raise ValueError("side must be attacker or defender")
        if tactic not in {"assault", "siege", "sabotage", "fortify", "repel"}: raise ValueError("Unknown war tactic")
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row; await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute("SELECT * FROM territory_wars WHERE war_id=? AND status='active'", (int(war_id),)); war = await cur.fetchone()
            if not war: await db.rollback(); return None
            await self._ensure_war_operation_locked(db, int(war_id), int(game_minute))
            cur = await db.execute("SELECT * FROM territory_war_operations WHERE war_id=?", (int(war_id),)); op = await cur.fetchone()
            siege = int(op["siege_progress"]); am = int(op["attacker_morale"]); dm = int(op["defender_morale"]); af = int(op["attacker_force"]); df = int(op["defender_force"])
            roll = stable_percent(war_id, user_id or 0, side, tactic, game_minute)
            impact = max(2, power // 4 + roll // 15)
            siege_delta = 0; morale_delta = 0
            if side == "attacker":
                af += power
                if tactic == "siege": siege_delta = impact + 5; dm -= impact // 2
                elif tactic == "assault": siege_delta = impact; dm -= impact
                elif tactic == "sabotage": siege_delta = impact // 2; dm -= impact + 4
                elif tactic == "fortify": am += impact
                elif tactic == "repel": siege_delta = impact // 2; dm -= impact // 2
                siege = min(100, siege + max(0, siege_delta)); morale_delta = -impact
            else:
                df += power
                if tactic == "fortify": siege_delta = -(impact + 4); dm += impact
                elif tactic == "repel": siege_delta = -impact; am -= impact
                elif tactic == "sabotage": siege_delta = -(impact // 2); am -= impact + 4
                elif tactic == "assault": am -= impact; siege_delta = -(impact // 3)
                else: siege_delta = -(impact // 2); am -= impact // 2
                siege = max(0, siege + siege_delta); morale_delta = -impact
            am = max(0, min(120, am)); dm = max(0, min(120, dm))
            winner = ""; resolution = ""; occupation_until = 0
            if siege >= 100 or dm <= 0:
                winner = str(war["attacker_key"]); resolution = "attacker_occupation"; occupation_until = int(game_minute) + 30*1440
            elif am <= 0:
                winner = str(war["defender_key"]); resolution = "defender_holds"
            await db.execute(
                """UPDATE territory_war_operations SET siege_progress=?,attacker_morale=?,defender_morale=?,attacker_force=?,defender_force=?,
                   last_tick_game_minute=?,winner_key=?,resolution=?,occupation_until_game_minute=?,updated_at=? WHERE war_id=?""",
                (siege, am, dm, af, df, int(game_minute), winner, resolution, occupation_until, now, int(war_id)),
            )
            await db.execute(
                "INSERT INTO territory_war_actions(war_id,user_id,side,tactic,power,siege_delta,morale_delta,game_minute,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (int(war_id), int(user_id) if user_id is not None else None, side, tactic, power, siege_delta, morale_delta, int(game_minute), now),
            )
            await db.execute("UPDATE territory_wars SET attacker_score=?,defender_score=?,updated_game_minute=?,updated_at=? WHERE war_id=?", (af, df, int(game_minute), now, int(war_id)))
            if winner:
                await db.execute("UPDATE territory_wars SET status='resolved',updated_game_minute=?,updated_at=? WHERE war_id=?", (int(game_minute), now, int(war_id)))
                if winner == str(war["attacker_key"]):
                    await db.execute("UPDATE territory_state SET controller_type='sect',controller_key=?,unrest=MIN(100,unrest+35),updated_game_minute=?,updated_at=? WHERE territory_key=?", (winner, int(game_minute), now, str(war["territory_key"])))
                else:
                    await db.execute("UPDATE territory_state SET unrest=MAX(0,unrest-10),updated_game_minute=?,updated_at=? WHERE territory_key=?", (int(game_minute), now, str(war["territory_key"])))
            await db.commit()
        if winner:
            await self.record_world_history_event(
                event_type="war_resolved", title=f"War for {war['territory_key']} resolved",
                summary=(
                    f"The territorial war between {war['attacker_key']} and {war['defender_key']} for "
                    f"{war['territory_key']} ended with {winner} victorious. Resolution: {resolution}."
                ),
                significance=92, visibility="public", location=str(war["territory_key"]), faction=str(winner),
                actor_type="sect", actor_key=str(winner), actor_name=str(winner),
                target_type="territory", target_key=str(war["territory_key"]), target_name=str(war["territory_key"]),
                tags=("war","major battle","territory","victory",str(war['attacker_key']),str(war['defender_key'])),
                game_minute=int(game_minute),
                metadata={
                    "war_id": int(war_id), "winner": winner, "resolution": resolution,
                    "attacker": str(war['attacker_key']), "defender": str(war['defender_key']),
                    "attacker_force": af, "defender_force": df, "siege_progress": siege,
                },
                source_key=f"territory_war:{int(war_id)}:resolved",
            )
        rows = await self.get_territory_wars(active_only=False)
        return next((x for x in rows if int(x["war_id"]) == int(war_id)), None)

    async def advance_territory_wars(self, game_minute: int) -> list[dict[str, Any]]:
        changed: list[dict[str, Any]] = []
        for war in await self.get_territory_wars():
            op = war.get("operations") or {}
            last = int(op.get("last_tick_game_minute") or war.get("updated_game_minute") or 0)
            days = max(0, (int(game_minute) - last) // 1440)
            if days <= 0: continue
            attacker_force = max(1, int(op.get("attacker_force", 0)))
            defender_force = max(1, int(op.get("defender_force", 0)))
            side = "attacker" if attacker_force >= defender_force else "defender"
            tactic = "siege" if side == "attacker" else "fortify"
            result = await self.territory_war_action(int(war["war_id"]), None, side=side, tactic=tactic, power=min(50, days + max(attacker_force, defender_force)//10), game_minute=int(game_minute))
            if result: changed.append(result)
        return changed

    async def advance_territory_occupations(self, game_minute: int) -> list[dict[str, Any]]:
        """Finalize occupations whose consolidation window has elapsed.

        An attacker victory first creates a 30-world-day occupation. When that
        period completes, control becomes annexed/stabilized instead of leaving
        a permanently-expired occupation marker in the war record.
        """
        now = time.time(); resolved: list[int] = []
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                """SELECT w.war_id,w.territory_key,w.attacker_key,o.occupation_until_game_minute
                   FROM territory_wars w JOIN territory_war_operations o ON o.war_id=w.war_id
                   WHERE w.status='resolved' AND o.resolution='attacker_occupation'
                     AND o.occupation_until_game_minute>0 AND o.occupation_until_game_minute<=?""",
                (int(game_minute),),
            )
            for row in await cur.fetchall():
                await db.execute(
                    """UPDATE territory_war_operations SET resolution='attacker_annexed',
                       occupation_until_game_minute=0,last_tick_game_minute=?,updated_at=? WHERE war_id=?""",
                    (int(game_minute), now, int(row["war_id"])),
                )
                await db.execute(
                    """UPDATE territory_state SET controller_type='sect',controller_key=?,
                       unrest=MAX(0,unrest-20),updated_game_minute=?,updated_at=? WHERE territory_key=?""",
                    (str(row["attacker_key"]), int(game_minute), now, str(row["territory_key"])),
                )
                resolved.append(int(row["war_id"]))
            await db.commit()
        rows = await self.get_territory_wars(active_only=False)
        return [war for war in rows if int(war["war_id"]) in resolved]

    async def get_territory_war_actions(self, war_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row; cur = await db.execute("SELECT * FROM territory_war_actions WHERE war_id=? ORDER BY action_id DESC LIMIT ?", (int(war_id), max(1,min(100,int(limit))))); return [dict(r) for r in await cur.fetchall()]

    async def advance_caravans(self, current_game_minute: int, *, owner_type: str | None = None, owner_key: str | None = None) -> list[dict[str, Any]]:
        now = time.time(); resolved: list[dict[str, Any]] = []
        era = await self.get_current_era()
        era_risk = max(0.25, float((era or {}).get("modifiers", {}).get("caravan_risk", 1.0)))
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row; await db.execute("BEGIN IMMEDIATE")
            sql = """SELECT c.*,o.escort_strength,o.concealment,o.smuggling,o.tax_rate FROM caravans c
                     LEFT JOIN caravan_operations o ON o.caravan_id=c.caravan_id
                     WHERE c.status='traveling' AND c.arrive_game_minute<=?"""
            params: list[Any] = [int(current_game_minute)]
            if owner_type is not None: sql += " AND c.owner_type=?"; params.append(str(owner_type))
            if owner_key is not None: sql += " AND c.owner_key=?"; params.append(str(owner_key))
            sql += " ORDER BY c.caravan_id"
            cur = await db.execute(sql, tuple(params))
            rows = await cur.fetchall()
            for row in rows:
                data = dict(row)
                try: cargo = json.loads(data.get("cargo_json") or "{}")
                except Exception: cargo = {}
                payout = max(0, int(cargo.get("_payout", 0))); currency = str(cargo.get("_currency", "low_spirit_stone"))
                escort = int(data.get("escort_strength") or 0); conceal = int(data.get("concealment") or 0); smuggling = int(data.get("smuggling") or 0); tax_rate = max(0, int(data.get("tax_rate") or 0))
                legacy_safe = (tax_rate == 0 and escort == 0 and conceal == 0 and not smuggling)
                effective_risk = 0 if legacy_safe else max(0, min(95, int(round((int(data.get("risk", 0)) + (20 if smuggling else 0) - escort*2 - conceal) * era_risk))))
                roll = stable_percent(data["caravan_id"], data["origin"], data["destination"], data["depart_game_minute"])
                intercepted = 1 if roll < effective_risk else 0; seized = 0; loss_percent = 0
                if intercepted:
                    if smuggling and roll < max(5, effective_risk // 3): seized = 1; loss_percent = 100
                    else: loss_percent = min(75, 20 + (effective_risk-roll)//2)
                after_loss = payout * (100-loss_percent) // 100
                toll = 0 if smuggling or seized else after_loss * tax_rate // 100
                final_payout = max(0, after_loss - toll)
                outcome = "seized" if seized else ("intercepted" if intercepted else "arrived")
                if str(data.get("owner_type")) == "player" and final_payout:
                    await self._wallet_delta(db, int(data["owner_key"]), currency, final_payout)
                await db.execute("UPDATE caravans SET status=?,updated_at=? WHERE caravan_id=?", (outcome, now, int(data["caravan_id"])))
                await db.execute(
                    """INSERT INTO caravan_operations(caravan_id,escort_strength,concealment,smuggling,tax_rate,toll_paid,intercepted,seized,payout_final,losses_json,outcome,resolved_game_minute,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(caravan_id) DO UPDATE SET toll_paid=excluded.toll_paid,intercepted=excluded.intercepted,seized=excluded.seized,
                       payout_final=excluded.payout_final,losses_json=excluded.losses_json,outcome=excluded.outcome,resolved_game_minute=excluded.resolved_game_minute,updated_at=excluded.updated_at""",
                    (int(data["caravan_id"]), escort, conceal, smuggling, tax_rate, toll, intercepted, seized, final_payout, json.dumps({"percent": loss_percent}), outcome, int(current_game_minute), now),
                )
                await db.execute("INSERT INTO caravan_events(caravan_id,event_type,detail_json,game_minute,created_at) VALUES(?,?,?,?,?)", (int(data["caravan_id"]), outcome, json.dumps({"risk":effective_risk,"roll":roll,"loss_percent":loss_percent,"tax":toll,"payout":final_payout}), int(current_game_minute), now))
                data.update({"cargo": {k:v for k,v in cargo.items() if not str(k).startswith("_")}, "payout": final_payout, "currency_id": currency, "status": outcome, "outcome": outcome, "toll_paid": toll, "loss_percent": loss_percent, "effective_risk": effective_risk})
                resolved.append(data)
            await db.commit()
        return resolved

    async def get_caravan_events(self, caravan_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row; cur = await db.execute("SELECT * FROM caravan_events WHERE caravan_id=? ORDER BY event_id DESC LIMIT ?", (int(caravan_id), max(1,min(100,int(limit))))); rows=[dict(r) for r in await cur.fetchall()]
        for r in rows:
            try: r["detail"] = json.loads(r.pop("detail_json") or "{}")
            except Exception: r["detail"] = {}
        return rows

    async def advance_world_era(self, game_minute: int) -> dict[str, Any] | None:
        current = await self.get_current_era()
        if not current:
            first = ERA_CYCLE[0]
            await self.start_world_era(first["name"], description=first["description"], game_minute=int(game_minute), modifiers=first["modifiers"])
            return await self.get_current_era()
        idx = era_index(str(current["name"])); changed = False; cursor = dict(current)
        # Bound catch-up in pathological admin time jumps while preserving order.
        for _ in range(12):
            template = ERA_CYCLE[idx]
            duration = int(template["duration_days"]) * 1440
            if int(game_minute) - int(cursor["started_game_minute"]) < duration: break
            transition_minute = int(cursor["started_game_minute"]) + duration
            next_idx = (idx + 1) % len(ERA_CYCLE); nxt = ERA_CYCLE[next_idx]; now = time.time()
            async with self._connect() as db:
                await db.execute("BEGIN IMMEDIATE")
                await db.execute("UPDATE world_eras SET active=0,ended_game_minute=? WHERE active=1", (transition_minute,))
                cur = await db.execute("INSERT INTO world_eras(name,description,started_game_minute,active,modifiers_json,created_at) VALUES(?,?,?,1,?,?)", (nxt["name"], nxt["description"], transition_minute, json.dumps(nxt["modifiers"]), now))
                era_id = int(cur.lastrowid)
                await db.execute("INSERT INTO world_era_events(era_id,event_type,title,detail_json,game_minute,created_at) VALUES(?, 'transition', ?, ?, ?, ?)", (era_id, f"{nxt['name']} begins", json.dumps({"previous": cursor["name"], "automatic": True}), transition_minute, now))
                await db.commit()
            cursor = await self.get_current_era() or cursor; idx = next_idx; changed = True
        return cursor if changed else current

    async def get_world_era_events(self, *, limit: int = 20) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row; cur = await db.execute("SELECT e.*,w.name AS era_name FROM world_era_events e JOIN world_eras w ON w.era_id=e.era_id ORDER BY e.event_id DESC LIMIT ?", (max(1,min(100,int(limit))),)); rows=[dict(r) for r in await cur.fetchall()]
        for r in rows:
            try: r["detail"] = json.loads(r.pop("detail_json") or "{}")
            except Exception: r["detail"] = {}
        return rows

    async def flush_slow_query_log(self, *, limit: int = 200) -> int:
        pending: list[dict[str, Any]] = []
        for _ in range(min(max(0, int(limit)), len(self._pending_slow_queries))):
            pending.append(self._pending_slow_queries.popleft())
        if not pending: return 0
        # Production persists telemetry through the Go-owned SQLite session too.
        # Suppress observation explicitly so this write does not recursively log itself.
        async with self._writer_lock:
            if self._go_transport is not None:
                await self._go_transport.batch(
                    [
                        {
                            "sql": "INSERT INTO slow_query_log(sql_text,latency_ms,operation,created_at) VALUES(?,?,?,?)",
                            "params": (row["sql_text"], float(row["latency_ms"]), row["operation"], float(row["created_at"])),
                        }
                        for row in pending
                    ],
                    transaction=True,
                )
            else:
                async with aiosqlite.connect(self.path, timeout=10) as db:
                    for row in pending:
                        await db.execute("INSERT INTO slow_query_log(sql_text,latency_ms,operation,created_at) VALUES(?,?,?,?)", (row["sql_text"], float(row["latency_ms"]), row["operation"], float(row["created_at"])))
                    await db.commit()
        return len(pending)

    async def observability_snapshot(self) -> dict[str, Any]:
        async with self._connect() as db:
            cur = await db.execute("SELECT COUNT(*) FROM slow_query_log WHERE created_at>=?", (time.time()-3600,)); recent = int((await cur.fetchone())[0])
            cur = await db.execute("SELECT COALESCE(MAX(latency_ms),0) FROM slow_query_log WHERE created_at>=?", (time.time()-3600,)); persistent_max = float((await cur.fetchone())[0])
        return {
            "query_count": int(self._query_count), "slow_query_count": int(self._slow_query_count),
            "pending_slow_queries": len(self._pending_slow_queries), "recent_slow_queries_1h": recent,
            "max_query_latency_ms": round(max(self._max_query_latency_ms, persistent_max), 3),
            "slow_query_threshold_ms": float(self.slow_query_ms),
            "connections_opened": int(self._connections_opened),
            "connections_reused": int(self._connections_reused),
            "writer_wait_count": int(self._writer_wait_count),
            "writer_wait_ms": round(float(self._writer_wait_ms), 3),
            "catalog_cache_entries": len(self._catalog_cache),
            "catalog_cache_hits": int(self._catalog_cache_hits),
            "catalog_cache_misses": int(self._catalog_cache_misses),
        }

    async def record_operational_alert(self, alert_key: str, *, severity: str, message: str, detail: dict[str, Any] | None = None, delivered: bool = False) -> int:
        async with self._connect() as db:
            cur = await db.execute("INSERT INTO operational_alerts(alert_key,severity,message,detail_json,delivered,created_at) VALUES(?,?,?,?,?,?)", (str(alert_key)[:120], str(severity)[:30], str(message)[:1000], json.dumps(detail or {}), 1 if delivered else 0, time.time()))
            await db.commit(); return int(cur.lastrowid)

    async def get_operational_alerts(self, *, limit: int = 20) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row; cur = await db.execute("SELECT * FROM operational_alerts ORDER BY alert_id DESC LIMIT ?", (max(1,min(100,int(limit))),)); rows=[dict(r) for r in await cur.fetchall()]
        for r in rows:
            try: r["detail"] = json.loads(r.pop("detail_json") or "{}")
            except Exception: r["detail"] = {}
        return rows

    async def advance_advanced_world_systems(self, game_minute: int) -> dict[str, Any]:
        spawned = await self.spawn_bounty_hunter_pursuits(int(game_minute))
        hunter_updates = await self.advance_bounty_hunters(int(game_minute))
        war_updates = await self.advance_territory_wars(int(game_minute))
        occupation_updates = await self.advance_territory_occupations(int(game_minute))
        caravan_updates = await self.advance_caravans(int(game_minute))
        before = await self.get_current_era(); era = await self.advance_world_era(int(game_minute))
        return {
            "bounty_hunters_spawned": len(spawned), "bounty_hunters_updated": len(hunter_updates),
            "wars_updated": len(war_updates), "occupations_resolved": len(occupation_updates),
            "caravans_resolved": len(caravan_updates),
            "era_changed": bool(before and era and int(before["era_id"]) != int(era["era_id"])),
            "era": str(era["name"]) if era else "",
        }


    async def admin_clear_battle(self, user_id: int) -> int:
        async with self._connect() as db:
            cur = await db.execute(
                "UPDATE battles SET status='abandoned',updated_at=? WHERE user_id=? AND status='active'",
                (time.time(), int(user_id)),
            )
            await db.commit()
            return int(cur.rowcount or 0)

    async def admin_revive_character(self, user_id: int) -> bool:
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute("SELECT 1 FROM characters WHERE user_id=?", (int(user_id),))
            if not await cur.fetchone():
                await db.rollback()
                return False
            await db.execute(
                """UPDATE characters SET life_status='alive',death_game_minute=NULL,
                   reincarnation_ready_game_minute=NULL,vitality=vitality_max,qi=qi_max,updated_at=?
                   WHERE user_id=?""",
                (time.time(), int(user_id)),
            )
            await db.execute("UPDATE reincarnation_state SET active=0 WHERE user_id=?", (int(user_id),))
            await db.execute(
                "UPDATE battles SET status='abandoned',updated_at=? WHERE user_id=? AND status='active'",
                (time.time(), int(user_id)),
            )
            await db.commit()
            return True

    async def admin_teleport_character(self, user_id: int, location: str) -> bool:
        async with self._connect() as db:
            cur = await db.execute(
                "UPDATE characters SET location=?,updated_at=? WHERE user_id=?",
                (str(location), time.time(), int(user_id)),
            )
            await db.commit()
            return int(cur.rowcount or 0) > 0

    async def create_backup(self, backup_dir: Path) -> Path:
        if self._go_transport is not None:
            info = await self._go_transport.create_backup()
            # The file lives in the engine's data/backups directory; callers use
            # the returned name for audit/UI rather than opening it from Python.
            return Path(str(info.get("name") or "xianxia-backup.sqlite3"))

        backup_dir = Path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime('%Y%m%d-%H%M%S', time.gmtime())
        destination = backup_dir / f"xianxia-{stamp}.sqlite3"
        source = self.path

        def _backup() -> None:
            src = sqlite3.connect(source)
            try:
                dst = sqlite3.connect(destination)
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            finally:
                src.close()

        await asyncio.to_thread(_backup)
        return destination

    async def list_backups(self, backup_dir: Path, limit: int = 20) -> list[dict[str, Any]]:
        if self._go_transport is not None:
            rows = await self._go_transport.list_backups()
            return rows[:max(1, min(int(limit), 50))]

        backup_dir = Path(backup_dir)
        if not backup_dir.exists():
            return []
        files = sorted(backup_dir.glob('xianxia-*.sqlite3'), key=lambda x: x.stat().st_mtime, reverse=True)
        out = []
        for path in files[:max(1, min(int(limit), 50))]:
            stat = path.stat()
            out.append({"name": path.name, "size": stat.st_size, "modified_at": stat.st_mtime})
        return out
