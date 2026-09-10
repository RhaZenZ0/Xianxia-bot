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

log = logging.getLogger("xianxia.database")


SCHEMA_VERSION = 33
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
        "character_creation_family_options",
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
    (
        18,
        "authoritative_domain_events_and_action_receipts",
        (
            """CREATE TABLE IF NOT EXISTS authoritative_actor_versions (
                actor_id INTEGER PRIMARY KEY,
                state_version INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS authoritative_action_receipts (
                action_id TEXT PRIMARY KEY,
                actor_id INTEGER NOT NULL,
                operation TEXT NOT NULL,
                state_version INTEGER NOT NULL,
                result_json TEXT NOT NULL,
                created_at REAL NOT NULL
            )""",
            """CREATE INDEX IF NOT EXISTS idx_authoritative_receipts_actor
               ON authoritative_action_receipts(actor_id,created_at DESC)""",
            """CREATE TABLE IF NOT EXISTS authoritative_entity_versions (
                domain TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                state_version INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL,
                PRIMARY KEY(domain,entity_type,entity_id)
            )""",
            """CREATE TABLE IF NOT EXISTS domain_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_uid TEXT NOT NULL UNIQUE,
                domain TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor_id INTEGER,
                entity_type TEXT NOT NULL DEFAULT '',
                entity_id TEXT NOT NULL DEFAULT '',
                subject_type TEXT NOT NULL DEFAULT '',
                subject_id TEXT NOT NULL DEFAULT '',
                game_minute INTEGER NOT NULL DEFAULT 0,
                state_version INTEGER NOT NULL DEFAULT 0,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL
            )""",
            """CREATE INDEX IF NOT EXISTS idx_domain_events_domain_entity
               ON domain_events(domain,entity_type,entity_id,event_id DESC)""",
            """CREATE INDEX IF NOT EXISTS idx_domain_events_actor
               ON domain_events(actor_id,event_id DESC)""",
            """CREATE INDEX IF NOT EXISTS idx_domain_events_subject
               ON domain_events(subject_type,subject_id,event_id DESC)""",
            """CREATE VIEW IF NOT EXISTS character_events AS
               SELECT * FROM domain_events
               WHERE domain='character' OR entity_type='character' OR subject_type='character'""",
            """CREATE VIEW IF NOT EXISTS npc_events AS
               SELECT * FROM domain_events
               WHERE domain='npc' OR entity_type='npc' OR subject_type='npc'""",
            """CREATE VIEW IF NOT EXISTS sect_events AS
               SELECT * FROM domain_events
               WHERE domain='sect' OR entity_type='sect' OR subject_type='sect'""",
        ),
    ),
    (
        19,
        "authoritative_character_creation_family_offers",
        (
            """CREATE TABLE IF NOT EXISTS character_creation_family_options (
                option_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                ordinal INTEGER NOT NULL,
                family_json TEXT NOT NULL,
                created_game_minute INTEGER NOT NULL DEFAULT 0,
                expires_at REAL NOT NULL,
                consumed_at REAL,
                created_at REAL NOT NULL
            )""",
            """CREATE INDEX IF NOT EXISTS idx_character_creation_family_options_user
               ON character_creation_family_options(user_id,ordinal)""",
        ),
    ),
    (
        20,
        "shared_starter_birth_households",
        (
            "ALTER TABLE birth_families ADD COLUMN starter_key TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE character_creation_family_options ADD COLUMN family_id INTEGER",
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_birth_families_starter_key
               ON birth_families(starter_key) WHERE starter_key<>''""",
            """CREATE TABLE IF NOT EXISTS birth_family_household_threads (
                guild_id INTEGER NOT NULL,
                family_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL,
                parent_channel_id INTEGER NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(guild_id,family_id),
                UNIQUE(thread_id),
                FOREIGN KEY(family_id) REFERENCES birth_families(family_id) ON DELETE CASCADE
            )""",
            """CREATE INDEX IF NOT EXISTS idx_birth_family_household_threads_family
               ON birth_family_household_threads(family_id,guild_id)""",
            "DELETE FROM character_creation_family_options",
        ),
    ),
    (
        21,
        "persistent_exploration_event_instances",
        (
            """CREATE TABLE IF NOT EXISTS exploration_events (
                event_id TEXT PRIMARY KEY,
                definition_id TEXT NOT NULL,
                title TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'Event',
                kind TEXT NOT NULL DEFAULT 'personal',
                visibility TEXT NOT NULL DEFAULT 'personal',
                location TEXT NOT NULL,
                severity INTEGER NOT NULL DEFAULT 1,
                state TEXT NOT NULL DEFAULT 'active',
                stage TEXT NOT NULL DEFAULT 'introduced',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_game_minute INTEGER NOT NULL DEFAULT 0,
                expires_at REAL NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )""",
            """CREATE INDEX IF NOT EXISTS idx_exploration_events_location_active
               ON exploration_events(location,state,expires_at)""",
            """CREATE TABLE IF NOT EXISTS exploration_event_participants (
                event_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                stage TEXT NOT NULL DEFAULT 'introduced',
                status TEXT NOT NULL DEFAULT 'active',
                joined_game_minute INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL,
                PRIMARY KEY(event_id,user_id),
                FOREIGN KEY(event_id) REFERENCES exploration_events(event_id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
            )""",
            """CREATE INDEX IF NOT EXISTS idx_exploration_event_participants_user
               ON exploration_event_participants(user_id,status,event_id)""",
            """CREATE TABLE IF NOT EXISTS exploration_event_actions (
                action_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                action_key TEXT NOT NULL,
                attribute TEXT NOT NULL DEFAULT '',
                total INTEGER NOT NULL DEFAULT 0,
                tn INTEGER NOT NULL DEFAULT 0,
                success INTEGER NOT NULL DEFAULT 0,
                detail TEXT NOT NULL DEFAULT '',
                game_minute INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                UNIQUE(event_id,user_id,action_key),
                FOREIGN KEY(event_id) REFERENCES exploration_events(event_id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE
            )""",
            """CREATE INDEX IF NOT EXISTS idx_exploration_event_actions_event
               ON exploration_event_actions(event_id,user_id,action_id)""",
        ),
    ),
    (
        22,
        "canonical_family_homeland_cities",
        (
            """UPDATE birth_families
               SET location = CASE starter_key
                       WHEN 'starter:mortal_world:martial_household' THEN 'Riverguard City'
                       WHEN 'starter:mortal_world:escort_martial_family' THEN 'Four-Roads Caravan City'
                       WHEN 'starter:mortal_world:weaponsmith_martial_family' THEN 'Emberforge City'
                       WHEN 'starter:mortal_world:body_tempering_family' THEN 'Stoneback Mountain City'
                       WHEN 'starter:mortal_world:sword_hall_family' THEN 'Cloudblade City'
                       WHEN 'starter:mortal_world:spear_guard_family' THEN 'Ironbanner City'
                       WHEN 'starter:mortal_world:hidden_weapon_family' THEN 'Moonfen City'
                       WHEN 'starter:mortal_world:border_garrison_family' THEN 'Frostwatch City'
                       WHEN 'starter:mortal_world:fallen_martial_clan' THEN 'Ashenwall City'
                       WHEN 'starter:mortal_world:noble_martial_clan' THEN 'Azure Crown Imperial City'
                       WHEN 'starter:mortal_world:alchemy_family' THEN 'Jadewood Medicine City'
                       WHEN 'starter:spiritual_world:martial_household' THEN 'Jadeflow Spirit City'
                       WHEN 'starter:spiritual_world:escort_martial_family' THEN 'Galevein Spirit City'
                       WHEN 'starter:spiritual_world:weaponsmith_martial_family' THEN 'Vermilion Furnace City'
                       WHEN 'starter:spiritual_world:body_tempering_family' THEN 'Stoneheart Spirit City'
                       WHEN 'starter:spiritual_world:sword_hall_family' THEN 'Cloudedge Spirit City'
                       WHEN 'starter:spiritual_world:spear_guard_family' THEN 'Spearwall Spirit City'
                       WHEN 'starter:spiritual_world:hidden_weapon_family' THEN 'Moonfrost Spirit City'
                       WHEN 'starter:spiritual_world:border_garrison_family' THEN 'Northwind Spirit City'
                       WHEN 'starter:spiritual_world:fallen_martial_clan' THEN 'Broken Halo Spirit City'
                       WHEN 'starter:spiritual_world:noble_martial_clan' THEN 'Jade Crown Spirit City'
                       WHEN 'starter:spiritual_world:alchemy_family' THEN 'Hundred Herb Spirit City'
                       WHEN 'starter:immortal_world:martial_household' THEN 'Immortal River City'
                       WHEN 'starter:immortal_world:escort_martial_family' THEN 'Skyroad Immortal City'
                       WHEN 'starter:immortal_world:weaponsmith_martial_family' THEN 'Solar Furnace Immortal City'
                       WHEN 'starter:immortal_world:body_tempering_family' THEN 'Adamant Body Immortal City'
                       WHEN 'starter:immortal_world:sword_hall_family' THEN 'Heavenblade Immortal City'
                       WHEN 'starter:immortal_world:spear_guard_family' THEN 'Golden Spear Immortal City'
                       WHEN 'starter:immortal_world:hidden_weapon_family' THEN 'Lunar Veil Immortal City'
                       WHEN 'starter:immortal_world:border_garrison_family' THEN 'Polar Gate Immortal City'
                       WHEN 'starter:immortal_world:fallen_martial_clan' THEN 'Fallen Star Immortal City'
                       WHEN 'starter:immortal_world:noble_martial_clan' THEN 'Ninefold Noble Immortal City'
                       WHEN 'starter:immortal_world:alchemy_family' THEN 'Jade Cauldron Immortal City'
                       WHEN 'starter:celestial_world:martial_household' THEN 'Celestial River City'
                       WHEN 'starter:celestial_world:escort_martial_family' THEN 'Starroad Celestial City'
                       WHEN 'starter:celestial_world:weaponsmith_martial_family' THEN 'Solar Crucible Celestial City'
                       WHEN 'starter:celestial_world:body_tempering_family' THEN 'Worldstone Celestial City'
                       WHEN 'starter:celestial_world:sword_hall_family' THEN 'Firmament Blade City'
                       WHEN 'starter:celestial_world:spear_guard_family' THEN 'Mandate Spear City'
                       WHEN 'starter:celestial_world:hidden_weapon_family' THEN 'Lunar Shadow Celestial City'
                       WHEN 'starter:celestial_world:border_garrison_family' THEN 'Froststar Border City'
                       WHEN 'starter:celestial_world:fallen_martial_clan' THEN 'Ruined Constellation City'
                       WHEN 'starter:celestial_world:noble_martial_clan' THEN 'Mandate Crown Celestial City'
                       WHEN 'starter:celestial_world:alchemy_family' THEN 'Divine Herb Celestial City'
                       ELSE location
                   END,
                   updated_at = CAST(strftime('%s','now') AS REAL)
               WHERE starter_key IN (
                   'starter:mortal_world:martial_household',
                   'starter:mortal_world:escort_martial_family',
                   'starter:mortal_world:weaponsmith_martial_family',
                   'starter:mortal_world:body_tempering_family',
                   'starter:mortal_world:sword_hall_family',
                   'starter:mortal_world:spear_guard_family',
                   'starter:mortal_world:hidden_weapon_family',
                   'starter:mortal_world:border_garrison_family',
                   'starter:mortal_world:fallen_martial_clan',
                   'starter:mortal_world:noble_martial_clan',
                   'starter:mortal_world:alchemy_family',
                   'starter:spiritual_world:martial_household',
                   'starter:spiritual_world:escort_martial_family',
                   'starter:spiritual_world:weaponsmith_martial_family',
                   'starter:spiritual_world:body_tempering_family',
                   'starter:spiritual_world:sword_hall_family',
                   'starter:spiritual_world:spear_guard_family',
                   'starter:spiritual_world:hidden_weapon_family',
                   'starter:spiritual_world:border_garrison_family',
                   'starter:spiritual_world:fallen_martial_clan',
                   'starter:spiritual_world:noble_martial_clan',
                   'starter:spiritual_world:alchemy_family',
                   'starter:immortal_world:martial_household',
                   'starter:immortal_world:escort_martial_family',
                   'starter:immortal_world:weaponsmith_martial_family',
                   'starter:immortal_world:body_tempering_family',
                   'starter:immortal_world:sword_hall_family',
                   'starter:immortal_world:spear_guard_family',
                   'starter:immortal_world:hidden_weapon_family',
                   'starter:immortal_world:border_garrison_family',
                   'starter:immortal_world:fallen_martial_clan',
                   'starter:immortal_world:noble_martial_clan',
                   'starter:immortal_world:alchemy_family',
                   'starter:celestial_world:martial_household',
                   'starter:celestial_world:escort_martial_family',
                   'starter:celestial_world:weaponsmith_martial_family',
                   'starter:celestial_world:body_tempering_family',
                   'starter:celestial_world:sword_hall_family',
                   'starter:celestial_world:spear_guard_family',
                   'starter:celestial_world:hidden_weapon_family',
                   'starter:celestial_world:border_garrison_family',
                   'starter:celestial_world:fallen_martial_clan',
                   'starter:celestial_world:noble_martial_clan',
                   'starter:celestial_world:alchemy_family'
               )""",
            """INSERT OR IGNORE INTO character_location_discoveries(
                   user_id,location,discovery_kind,discovered_game_minute,created_at
               )
               SELECT cbf.user_id,bf.location,'family_homeland_migration',
                      COALESCE(c.created_game_minute,0),CAST(strftime('%s','now') AS REAL)
               FROM character_birth_family AS cbf
               JOIN birth_families AS bf ON bf.family_id=cbf.family_id
               JOIN characters AS c ON c.user_id=cbf.user_id
               WHERE bf.starter_key LIKE 'starter:%'""",
            "DELETE FROM character_creation_family_options",
        ),
    ),

    (
        23,
        "persistent_samsara_dynasty_history",
        (
            """CREATE TABLE IF NOT EXISTS samsara_dynasty_history (
                history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                incarnation_number INTEGER NOT NULL,
                source_family_id INTEGER,
                source_family_name TEXT NOT NULL,
                source_family_archetype TEXT NOT NULL DEFAULT '',
                source_world TEXT NOT NULL,
                destination_family_id INTEGER,
                destination_family_name TEXT NOT NULL,
                destination_family_archetype TEXT NOT NULL DEFAULT '',
                destination_world TEXT NOT NULL,
                lineage_status TEXT NOT NULL,
                blood_continuity INTEGER NOT NULL DEFAULT 0,
                event_kind TEXT NOT NULL,
                summary TEXT NOT NULL,
                evidence_json TEXT NOT NULL DEFAULT '[]',
                investigation_level INTEGER NOT NULL DEFAULT 0,
                investigation_count INTEGER NOT NULL DEFAULT 0,
                first_discovered_game_minute INTEGER,
                last_investigated_game_minute INTEGER,
                created_game_minute INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(user_id, incarnation_number),
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE,
                FOREIGN KEY(source_family_id) REFERENCES birth_families(family_id) ON DELETE SET NULL,
                FOREIGN KEY(destination_family_id) REFERENCES birth_families(family_id) ON DELETE SET NULL
            )""",
            """CREATE INDEX IF NOT EXISTS idx_samsara_dynasty_history_user
               ON samsara_dynasty_history(user_id,incarnation_number DESC)""",
            """CREATE INDEX IF NOT EXISTS idx_samsara_dynasty_history_families
               ON samsara_dynasty_history(source_family_id,destination_family_id)""",
        ),
    ),


    (
        24,
        "ancestral_sites_dynasty_claims_conflicts",
        (
            """CREATE TABLE IF NOT EXISTS samsara_ancestral_leads (
                lead_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                history_id INTEGER NOT NULL,
                lead_kind TEXT NOT NULL,
                name TEXT NOT NULL,
                location TEXT NOT NULL,
                world_name TEXT NOT NULL,
                description TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'hidden',
                clue_required INTEGER NOT NULL DEFAULT 1,
                danger INTEGER NOT NULL DEFAULT 0,
                evidence_weight INTEGER NOT NULL DEFAULT 10,
                retainer_name TEXT NOT NULL DEFAULT '',
                retainer_relation TEXT NOT NULL DEFAULT '',
                discovered_game_minute INTEGER,
                resolved_game_minute INTEGER,
                created_game_minute INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(history_id,lead_kind),
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE,
                FOREIGN KEY(history_id) REFERENCES samsara_dynasty_history(history_id) ON DELETE CASCADE
            )""",
            """CREATE INDEX IF NOT EXISTS idx_samsara_ancestral_leads_user
               ON samsara_ancestral_leads(user_id,status,history_id)""",
            """CREATE TABLE IF NOT EXISTS samsara_investigation_quests (
                quest_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                history_id INTEGER NOT NULL,
                lead_id INTEGER NOT NULL,
                quest_kind TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'locked',
                progress INTEGER NOT NULL DEFAULT 0,
                target INTEGER NOT NULL DEFAULT 1,
                reward_evidence INTEGER NOT NULL DEFAULT 10,
                hostile_cause INTEGER NOT NULL DEFAULT 0,
                culprit_name TEXT NOT NULL DEFAULT '',
                created_game_minute INTEGER NOT NULL DEFAULT 0,
                completed_game_minute INTEGER,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(history_id,quest_kind),
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE,
                FOREIGN KEY(history_id) REFERENCES samsara_dynasty_history(history_id) ON DELETE CASCADE,
                FOREIGN KEY(lead_id) REFERENCES samsara_ancestral_leads(lead_id) ON DELETE CASCADE
            )""",
            """CREATE INDEX IF NOT EXISTS idx_samsara_investigation_quests_user
               ON samsara_investigation_quests(user_id,status,history_id)""",
            """CREATE TABLE IF NOT EXISTS samsara_dynasty_claims (
                claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                history_id INTEGER NOT NULL,
                claim_type TEXT NOT NULL,
                dynasty_name TEXT NOT NULL,
                target_family_name TEXT NOT NULL DEFAULT '',
                target_world TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                legitimacy INTEGER NOT NULL DEFAULT 0,
                support INTEGER NOT NULL DEFAULT 0,
                opposition INTEGER NOT NULL DEFAULT 0,
                blood_based INTEGER NOT NULL DEFAULT 0,
                resolution TEXT NOT NULL DEFAULT '',
                created_game_minute INTEGER NOT NULL DEFAULT 0,
                resolved_game_minute INTEGER,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(user_id,history_id,claim_type),
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE,
                FOREIGN KEY(history_id) REFERENCES samsara_dynasty_history(history_id) ON DELETE CASCADE
            )""",
            """CREATE INDEX IF NOT EXISTS idx_samsara_dynasty_claims_user
               ON samsara_dynasty_claims(user_id,status,history_id)""",
            """CREATE TABLE IF NOT EXISTS samsara_dynasty_conflicts (
                conflict_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                claim_id INTEGER NOT NULL UNIQUE,
                history_id INTEGER NOT NULL,
                conflict_type TEXT NOT NULL,
                opponent_name TEXT NOT NULL,
                opponent_family_name TEXT NOT NULL DEFAULT '',
                stakes TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                player_progress INTEGER NOT NULL DEFAULT 0,
                opponent_progress INTEGER NOT NULL DEFAULT 0,
                rounds INTEGER NOT NULL DEFAULT 0,
                last_tactic TEXT NOT NULL DEFAULT '',
                outcome TEXT NOT NULL DEFAULT '',
                created_game_minute INTEGER NOT NULL DEFAULT 0,
                resolved_game_minute INTEGER,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE,
                FOREIGN KEY(claim_id) REFERENCES samsara_dynasty_claims(claim_id) ON DELETE CASCADE,
                FOREIGN KEY(history_id) REFERENCES samsara_dynasty_history(history_id) ON DELETE CASCADE
            )""",
            """CREATE INDEX IF NOT EXISTS idx_samsara_dynasty_conflicts_user
               ON samsara_dynasty_conflicts(user_id,status,history_id)""",
        ),
    ),
    (
        25,
        "gm_authored_channel_messages",
        (
            """CREATE TABLE IF NOT EXISTS channel_messages (
                guild_id INTEGER NOT NULL,
                channel_key TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                message_id INTEGER,
                updated_at REAL NOT NULL,
                PRIMARY KEY(guild_id,channel_key)
            )""",
        ),
    ),
    (
        26,
        "bugs_forum_channel",
        (
            "ALTER TABLE server_config ADD COLUMN bugs_channel_id INTEGER",
        ),
    ),
    (
        27,
        "player_moderation_flags",
        (
            "ALTER TABLE characters ADD COLUMN is_muted INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE characters ADD COLUMN is_frozen INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE characters ADD COLUMN moderation_reason TEXT NOT NULL DEFAULT ''",
        ),
    ),
    (
        28,
        "quest_forge_definitions",
        (
            # Quest Forge (v0.20.6): quests drafted by the narrator's model from
            # a GM prompt or a world-history event, held as drafts until a GM
            # approves them, then served beside the static catalog in
            # app/rules/quests.py. Same objective/reward shape as the catalog.
            """CREATE TABLE IF NOT EXISTS quest_definitions (
                quest_key TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                source_type TEXT NOT NULL DEFAULT 'forge',
                source_key TEXT NOT NULL DEFAULT '',
                objectives_json TEXT NOT NULL DEFAULT '[]',
                rewards_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'draft',
                origin TEXT NOT NULL DEFAULT 'gm_prompt',
                story_prompt TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                created_by INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                reviewed_by INTEGER,
                reviewed_at REAL,
                updated_at REAL NOT NULL
            )""",
            "CREATE INDEX IF NOT EXISTS idx_quest_definitions_status ON quest_definitions(status, created_at)",
        ),
    ),
    (
        29,
        "commissions",
        (
            # Commissions (v0.22.0, docs/COMMISSIONS_DESIGN.md): a quest a giver
            # NPC offers in character, held one at a time, ending completed /
            # failed / abandoned. Nothing here is a new quest pipeline - a
            # commission is a quest_definitions row with a giver, and the
            # one-at-a-time rule keys on character_quests.commission so a
            # definition can be retired without freeing the player's slot.
            "ALTER TABLE quest_definitions ADD COLUMN giver_npc TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE quest_definitions ADD COLUMN realm_band TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE quest_definitions ADD COLUMN tier INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE quest_definitions ADD COLUMN owner_user_id INTEGER",
            "ALTER TABLE quest_definitions ADD COLUMN deadline_game_minutes INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE quest_definitions ADD COLUMN variants_json TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE quest_definitions ADD COLUMN seed_json TEXT NOT NULL DEFAULT '{}'",
            """CREATE INDEX IF NOT EXISTS idx_quest_definitions_giver
               ON quest_definitions(giver_npc, status, tier)""",
            # Absolute deadline and the accepted terms live on the player's row:
            # a later edit to the definition can never change what a held
            # commission pays or when it is due.
            "ALTER TABLE character_quests ADD COLUMN commission INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE character_quests ADD COLUMN deadline_game_minute INTEGER",
            "ALTER TABLE character_quests ADD COLUMN variant_index INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE character_quests ADD COLUMN resolved_game_minute INTEGER",
            """CREATE INDEX IF NOT EXISTS idx_character_quests_commission_due
               ON character_quests(status, commission, deadline_game_minute)""",
            # Standing is derived (trust+respect-grudge); only the refusal
            # cooldown and the outcome counters are stored.
            "ALTER TABLE npc_relationships ADD COLUMN commission_cooldown_until_game_minute INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE npc_relationships ADD COLUMN commissions_completed INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE npc_relationships ADD COLUMN commissions_failed INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE npc_relationships ADD COLUMN commissions_abandoned INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE npc_relationships ADD COLUMN last_commission_outcome TEXT NOT NULL DEFAULT ''",
        ),
    ),
    (
        30,
        "commission_givers_and_undisclosed_terms",
        (
            # v0.22.1. Three fields, all on the definition:
            #  - `requires_sect` makes a commission sect business. Membership is
            #    checked by the engine at accept, not only by the offer ladder.
            #  - `reward_visibility='hidden'` means the giver will not say what
            #    the work pays. Presentation only: the engine still locks exact
            #    terms and pays exactly those, and completion states them in
            #    full. An old beggar can be worth far more than he let on and a
            #    self-declared hidden master far less, and the offer card cannot
            #    tell you which - that is the mechanic.
            #  - `boast` is the authored line such a giver may claim about the
            #    work. Content, never a number, and the model may echo it but
            #    may not make it specific.
            "ALTER TABLE quest_definitions ADD COLUMN requires_sect TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE quest_definitions ADD COLUMN reward_visibility TEXT NOT NULL DEFAULT 'shown'",
            "ALTER TABLE quest_definitions ADD COLUMN boast TEXT NOT NULL DEFAULT ''",
        ),
    ),
    (
        31,
        "pvp_match_location",
        (
            # v0.22.3. A duel is fought somewhere, and until now nothing
            # recorded where. The engine re-checks a match's preconditions on
            # every action (go_core/internal/game/pvp_invariants.go), and
            # "are you both still here" needs a `here` - otherwise two people
            # who separately walked to the same distant city would still count
            # as duelling each other in the street they left.
            "ALTER TABLE pvp_matches ADD COLUMN location TEXT NOT NULL DEFAULT ''",
        ),
    ),
    (
        32,
        "pinned_quest_terms",
        (
            # v0.24.0. What a quest asks for and what it pays are recorded on
            # the player's row when they accept it, and `quest.progress` reads
            # them from there (go_core/internal/game/quest_terms.go).
            #
            # A commission already locked its variant and its deadline here for
            # exactly this reason; ordinary quests did not, so editing a
            # definition silently rewrote a deal somebody had already taken -
            # and the terms reached the engine inside the caller's payload,
            # which put Python in charge of what the work was worth.
            #
            # Rows accepted before this migration have no pin. They are
            # backfilled from the current definition the first time they are
            # touched, which is the deal they were already on.
            "ALTER TABLE character_quests ADD COLUMN terms_json TEXT NOT NULL DEFAULT ''",
        ),
    ),

    (
        33,
        "sect_abode_facilities",
        (
            # v0.30.1: the residence a public sect assigns grows the way a
            # homestead does - a cultivation chamber and a storeroom to begin
            # with, the rest built with contribution points and gated by
            # rank and stage (sect.abode.upgrade, content sect_abode_system).
            "ALTER TABLE sect_abodes ADD COLUMN cultivation_level INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE sect_abodes ADD COLUMN alchemy_level INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE sect_abodes ADD COLUMN forge_level INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE sect_abodes ADD COLUMN formation_level INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE sect_abodes ADD COLUMN storage_level INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE sect_abodes ADD COLUMN herb_garden_level INTEGER NOT NULL DEFAULT 0",
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

                CREATE TABLE IF NOT EXISTS samsara_dynasty_history (
                    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    incarnation_number INTEGER NOT NULL,
                    source_family_id INTEGER,
                    source_family_name TEXT NOT NULL,
                    source_family_archetype TEXT NOT NULL DEFAULT '',
                    source_world TEXT NOT NULL,
                    destination_family_id INTEGER,
                    destination_family_name TEXT NOT NULL,
                    destination_family_archetype TEXT NOT NULL DEFAULT '',
                    destination_world TEXT NOT NULL,
                    lineage_status TEXT NOT NULL,
                    blood_continuity INTEGER NOT NULL DEFAULT 0,
                    event_kind TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    investigation_level INTEGER NOT NULL DEFAULT 0,
                    investigation_count INTEGER NOT NULL DEFAULT 0,
                    first_discovered_game_minute INTEGER,
                    last_investigated_game_minute INTEGER,
                    created_game_minute INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(user_id, incarnation_number),
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(source_family_id) REFERENCES birth_families(family_id) ON DELETE SET NULL,
                    FOREIGN KEY(destination_family_id) REFERENCES birth_families(family_id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_samsara_dynasty_history_user
                    ON samsara_dynasty_history(user_id, incarnation_number DESC);
                CREATE INDEX IF NOT EXISTS idx_samsara_dynasty_history_families
                    ON samsara_dynasty_history(source_family_id, destination_family_id);


                CREATE TABLE IF NOT EXISTS samsara_ancestral_leads (
                    lead_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    history_id INTEGER NOT NULL,
                    lead_kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    location TEXT NOT NULL,
                    world_name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'hidden',
                    clue_required INTEGER NOT NULL DEFAULT 1,
                    danger INTEGER NOT NULL DEFAULT 0,
                    evidence_weight INTEGER NOT NULL DEFAULT 10,
                    retainer_name TEXT NOT NULL DEFAULT '',
                    retainer_relation TEXT NOT NULL DEFAULT '',
                    discovered_game_minute INTEGER,
                    resolved_game_minute INTEGER,
                    created_game_minute INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(history_id,lead_kind),
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(history_id) REFERENCES samsara_dynasty_history(history_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_samsara_ancestral_leads_user
                    ON samsara_ancestral_leads(user_id,status,history_id);

                CREATE TABLE IF NOT EXISTS samsara_investigation_quests (
                    quest_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    history_id INTEGER NOT NULL,
                    lead_id INTEGER NOT NULL,
                    quest_kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'locked',
                    progress INTEGER NOT NULL DEFAULT 0,
                    target INTEGER NOT NULL DEFAULT 1,
                    reward_evidence INTEGER NOT NULL DEFAULT 10,
                    hostile_cause INTEGER NOT NULL DEFAULT 0,
                    culprit_name TEXT NOT NULL DEFAULT '',
                    created_game_minute INTEGER NOT NULL DEFAULT 0,
                    completed_game_minute INTEGER,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(history_id,quest_kind),
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(history_id) REFERENCES samsara_dynasty_history(history_id) ON DELETE CASCADE,
                    FOREIGN KEY(lead_id) REFERENCES samsara_ancestral_leads(lead_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_samsara_investigation_quests_user
                    ON samsara_investigation_quests(user_id,status,history_id);

                CREATE TABLE IF NOT EXISTS samsara_dynasty_claims (
                    claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    history_id INTEGER NOT NULL,
                    claim_type TEXT NOT NULL,
                    dynasty_name TEXT NOT NULL,
                    target_family_name TEXT NOT NULL DEFAULT '',
                    target_world TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    legitimacy INTEGER NOT NULL DEFAULT 0,
                    support INTEGER NOT NULL DEFAULT 0,
                    opposition INTEGER NOT NULL DEFAULT 0,
                    blood_based INTEGER NOT NULL DEFAULT 0,
                    resolution TEXT NOT NULL DEFAULT '',
                    created_game_minute INTEGER NOT NULL DEFAULT 0,
                    resolved_game_minute INTEGER,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(user_id,history_id,claim_type),
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(history_id) REFERENCES samsara_dynasty_history(history_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_samsara_dynasty_claims_user
                    ON samsara_dynasty_claims(user_id,status,history_id);

                CREATE TABLE IF NOT EXISTS samsara_dynasty_conflicts (
                    conflict_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    claim_id INTEGER NOT NULL UNIQUE,
                    history_id INTEGER NOT NULL,
                    conflict_type TEXT NOT NULL,
                    opponent_name TEXT NOT NULL,
                    opponent_family_name TEXT NOT NULL DEFAULT '',
                    stakes TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    player_progress INTEGER NOT NULL DEFAULT 0,
                    opponent_progress INTEGER NOT NULL DEFAULT 0,
                    rounds INTEGER NOT NULL DEFAULT 0,
                    last_tactic TEXT NOT NULL DEFAULT '',
                    outcome TEXT NOT NULL DEFAULT '',
                    created_game_minute INTEGER NOT NULL DEFAULT 0,
                    resolved_game_minute INTEGER,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES characters(user_id) ON DELETE CASCADE,
                    FOREIGN KEY(claim_id) REFERENCES samsara_dynasty_claims(claim_id) ON DELETE CASCADE,
                    FOREIGN KEY(history_id) REFERENCES samsara_dynasty_history(history_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_samsara_dynasty_conflicts_user
                    ON samsara_dynasty_conflicts(user_id,status,history_id);

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
                        try:
                            await db.execute(statement)
                        except sqlite3.OperationalError as exc:
                            # Schema markers can be restored/backdated after a partial migration or
                            # recovery while the physical column is already present. SQLite has no
                            # portable ALTER TABLE ... ADD COLUMN IF NOT EXISTS, so treat this one
                            # idempotent condition as already applied and keep the migration atomic.
                            if statement.lstrip().upper().startswith("ALTER TABLE") and "duplicate column name" in str(exc).casefold():
                                continue
                            raise
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


    async def get_inventory(self, user_id: int) -> dict[str, int]:
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT item_id, quantity FROM inventory WHERE user_id = ? AND quantity > 0 ORDER BY item_id",
                (user_id,),
            )
            rows = await cur.fetchall()
            return {str(item_id): int(qty) for item_id, qty in rows}

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
            "commission_cooldown_until_game_minute": 0, "commissions_completed": 0,
            "commissions_failed": 0, "commissions_abandoned": 0, "last_commission_outcome": "",
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

    # Accepting a quest is an engine write (`commission.accept`, v0.22.0):
    # the one-at-a-time rule, the deadline and the locked variant have to be
    # decided in the same transaction as the insert, so there is no
    # Python-side accept_quest any more. See app/ops/core_services.py.

    # ---- Quest Forge definitions (v0.20.6) --------------------------------

    @staticmethod
    def _quest_definition_row(data: dict[str, Any]) -> dict[str, Any]:
        row = dict(data)
        for column, key, default in (("objectives_json", "objectives", []), ("rewards_json", "rewards", {}),
                                     ("variants_json", "variants", []), ("seed_json", "seed", {})):
            try:
                row[key] = json.loads(str(row.pop(column, "") or "") or json.dumps(default))
            except Exception:
                row[key] = default
        return row

    async def list_quest_definitions(self, status: str | None = None, *, limit: int = 200) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            if status is None:
                cur = await db.execute("SELECT * FROM quest_definitions ORDER BY created_at DESC LIMIT ?", (int(limit),))
            else:
                cur = await db.execute(
                    "SELECT * FROM quest_definitions WHERE status=? ORDER BY created_at DESC LIMIT ?", (str(status), int(limit)),
                )
            return [self._quest_definition_row(dict(r)) for r in await cur.fetchall()]

    async def get_quest_definition(self, quest_key: str) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM quest_definitions WHERE quest_key=?", (str(quest_key),))
            row = await cur.fetchone()
            return self._quest_definition_row(dict(row)) if row else None

    async def save_quest_definition(self, definition: dict[str, Any], *, status: str = "draft", origin: str = "gm_prompt",
                                    story_prompt: str = "", model: str = "", created_by: int = 0) -> dict[str, Any]:
        """Insert or replace a forged definition. `definition` is the catalog
        shape (title, description, source_type, source_key, objectives, rewards)
        plus quest_key; validation is the caller's (app/rules/quests.py).

        A commission carries the extra fields from migrations 29 and 30 -
        giver_npc, realm_band, tier, owner_user_id, deadline_game_minutes,
        variants, seed, requires_sect, reward_visibility and boast. They default
        to the non-commission shape, so every existing caller keeps writing
        exactly the row it wrote before."""
        now = time.time()
        owner = definition.get("owner_user_id")
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO quest_definitions(quest_key,title,description,source_type,source_key,objectives_json,rewards_json,
                       status,origin,story_prompt,model,created_by,created_at,updated_at,
                       giver_npc,realm_band,tier,owner_user_id,deadline_game_minutes,variants_json,seed_json,
                       requires_sect,reward_visibility,boast)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(quest_key) DO UPDATE SET title=excluded.title,description=excluded.description,
                       source_type=excluded.source_type,source_key=excluded.source_key,objectives_json=excluded.objectives_json,
                       rewards_json=excluded.rewards_json,status=excluded.status,origin=excluded.origin,
                       story_prompt=excluded.story_prompt,model=excluded.model,updated_at=excluded.updated_at,
                       giver_npc=excluded.giver_npc,realm_band=excluded.realm_band,tier=excluded.tier,
                       owner_user_id=excluded.owner_user_id,deadline_game_minutes=excluded.deadline_game_minutes,
                       variants_json=excluded.variants_json,seed_json=excluded.seed_json,
                       requires_sect=excluded.requires_sect,reward_visibility=excluded.reward_visibility,
                       boast=excluded.boast""",
                (
                    str(definition["quest_key"]), str(definition["title"]), str(definition.get("description", "")),
                    str(definition.get("source_type", "forge")), str(definition.get("source_key", "")),
                    json.dumps(list(definition.get("objectives", []))), json.dumps(dict(definition.get("rewards", {}))),
                    str(status), str(origin), str(story_prompt)[:2000], str(model)[:120], int(created_by), now, now,
                    str(definition.get("giver_npc", "") or ""), str(definition.get("realm_band", "") or ""),
                    int(definition.get("tier", 1) or 1), None if owner in (None, "") else int(owner),
                    int(definition.get("deadline_game_minutes", 0) or 0),
                    json.dumps(list(definition.get("variants", []))), json.dumps(dict(definition.get("seed", {}))),
                    str(definition.get("requires_sect", "") or ""),
                    str(definition.get("reward_visibility", "shown") or "shown"),
                    str(definition.get("boast", "") or ""),
                ),
            )
            await db.commit()
        return await self.get_quest_definition(str(definition["quest_key"]))

    async def sync_commission_pool(self, commissions: list[dict[str, Any]]) -> int:
        """Seed authored content into `quest_definitions` as approved rows, once.

        Two kinds of content come through here: the commission pool from
        `content/world.json`, and the static quests from `app/rules/quests.py`,
        which arrive with no `giver_npc` and are ordinary quests.

        The static ones are seeded for the engine's benefit (v0.23.1). Accepting
        a quest is `commission.accept`, and the engine told a legitimate static
        quest apart from a nonexistent key by the same test - neither had a row -
        so it accepted any string as an ordinary quest. Python validated the key
        before asking, which protected the Discord path and left the authority
        invariant wrong. A row here is what makes "no row" mean "no such quest".

        Insert-only on purpose: a GM who edits the reward, retires a
        commission, or approves a forged one keeps that decision across every
        restart. Content is the starting pool, not the running one."""
        inserted = 0
        now = time.time()
        async with self._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            for entry in commissions:
                key = str(entry.get("quest_key") or "").strip()
                if not key:
                    continue
                cur = await db.execute("SELECT 1 FROM quest_definitions WHERE quest_key=?", (key,))
                if await cur.fetchone():
                    continue
                await db.execute(
                    """INSERT INTO quest_definitions(quest_key,title,description,source_type,source_key,objectives_json,
                           rewards_json,status,origin,created_by,created_at,updated_at,
                           giver_npc,realm_band,tier,deadline_game_minutes,variants_json,
                           requires_sect,reward_visibility,boast)
                       VALUES(?,?,?,?,?,?,?,'approved','content',0,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        key, str(entry.get("title", "")), str(entry.get("description", "")),
                        str(entry.get("source_type", "commission")), str(entry.get("source_key", "")),
                        json.dumps(list(entry.get("objectives", []))), json.dumps(dict(entry.get("rewards", {}))),
                        now, now, str(entry.get("giver_npc", "")), str(entry.get("realm_band", "")),
                        int(entry.get("tier", 1) or 1), int(entry.get("deadline_game_minutes", 0) or 0),
                        json.dumps(list(entry.get("variants", []))),
                        str(entry.get("requires_sect", "") or ""),
                        str(entry.get("reward_visibility", "shown") or "shown"),
                        str(entry.get("boast", "") or ""),
                    ),
                )
                inserted += 1
            await db.commit()
        return inserted

    async def list_commission_definitions(self, giver_npc: str, *, status: str = "approved",
                                          user_id: int | None = None, limit: int = 60) -> list[dict[str, Any]]:
        """Commissions this giver offers, visible to this viewer.

        Pooled rows (`owner_user_id IS NULL`) are public content; an invented
        one belongs to a single player and is never listed for anyone else.
        The ordering is deterministic so the same player asking twice in a
        row is offered the same commission until it resolves."""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT * FROM quest_definitions
                   WHERE giver_npc=? AND status=? AND (owner_user_id IS NULL OR owner_user_id=?)
                   ORDER BY tier, created_at, quest_key LIMIT ?""",
                (str(giver_npc), str(status), -1 if user_id is None else int(user_id), max(1, min(200, int(limit)))),
            )
            return [self._quest_definition_row(dict(r)) for r in await cur.fetchall()]

    async def set_quest_definition_status(self, quest_key: str, status: str, *, reviewed_by: int = 0) -> bool:
        if status not in ("draft", "approved", "retired", "discarded"):
            raise ValueError("Unknown quest definition status.")
        now = time.time()
        async with self._connect() as db:
            cur = await db.execute(
                "UPDATE quest_definitions SET status=?,reviewed_by=?,reviewed_at=?,updated_at=? WHERE quest_key=?",
                (str(status), int(reviewed_by), now, now, str(quest_key)),
            )
            await db.commit()
            return bool(cur.rowcount)

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
                # v0.24.0: the terms this player accepted, pinned by the engine
                # at accept. Presentation must read these rather than today's
                # definition, or a GM's edit changes what a held quest says it
                # asks for and pays - which is the deal, not a display detail.
                try:
                    terms = json.loads(str(data.pop("terms_json", "") or "") or "{}")
                except Exception:
                    terms = {}
                data["terms"] = terms if isinstance(terms, dict) else {}
                result.append(data)
            return result


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


    async def has_completed_body_perfection(self, user_id: int) -> bool:
        """Return True once the character has perfected at least one Body realm."""
        async with self._connect() as db:
            cur = await db.execute(
                "SELECT 1 FROM body_realm_perfection WHERE user_id=? AND completed=1 LIMIT 1",
                (user_id,),
            )
            return await cur.fetchone() is not None

    # ---------- Shared world events ----------
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

    async def set_bugs_channel_id(self, guild_id: int, channel_id: int | None) -> None:
        now = time.time()
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO server_config(guild_id,bugs_channel_id,updated_at) VALUES(?,?,?)
                   ON CONFLICT(guild_id) DO UPDATE SET
                   bugs_channel_id=excluded.bugs_channel_id,updated_at=excluded.updated_at""",
                (int(guild_id), int(channel_id) if channel_id else None, now),
            )
            await db.commit()

    async def clear_discord_bindings(self, guild_id: int) -> dict[str, int]:
        """Forget every Discord channel and message id the bot holds for a guild.

        The Discord half of the dashboard's Teardown (v0.21.2): after the
        managed channels are deleted, the ids that pointed at them are cleared
        so the status readout says "missing" rather than "stale" and nothing
        tries to post into a channel that no longer exists. GM-authored channel
        message *text* is kept (only the posted message id is forgotten), so a
        later Full Setup reposts the GM's words, not the defaults. Thread rows
        are left alone on purpose - every thread owner recovers from a missing
        thread by creating a new one on next use, exactly as after Reset World.
        """
        async with self._connect() as db:
            cur = await db.execute(
                """UPDATE server_config SET
                       announcement_channel_id=NULL, event_scene_channel_id=NULL, home_scene_channel_id=NULL,
                       log_channel_id=NULL, begin_channel_id=NULL, info_channel_id=NULL, exploration_channel_id=NULL,
                       info_message_id=NULL, bugs_channel_id=NULL, updated_at=?
                   WHERE guild_id=?""",
                (time.time(), int(guild_id)),
            )
            config_rows = int(cur.rowcount or 0)
            cur = await db.execute("DELETE FROM realm_hub_channels WHERE guild_id=?", (int(guild_id),))
            hub_rows = int(cur.rowcount or 0)
            cur = await db.execute(
                "UPDATE channel_messages SET message_id=NULL, updated_at=? WHERE guild_id=? AND message_id IS NOT NULL",
                (time.time(), int(guild_id)),
            )
            message_rows = int(cur.rowcount or 0)
            await db.commit()
        return {"server_config": config_rows, "realm_hubs": hub_rows, "channel_messages": message_rows}

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

    async def get_birth_family_household_thread(self, guild_id: int, family_id: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM birth_family_household_threads WHERE guild_id=? AND family_id=?",
                (int(guild_id), int(family_id)),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_birth_family_household_thread_by_thread(self, thread_id: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM birth_family_household_threads WHERE thread_id=?",
                (int(thread_id),),
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def set_birth_family_household_thread(
        self, guild_id: int, family_id: int, *, thread_id: int, parent_channel_id: int
    ) -> None:
        now = time.time()
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO birth_family_household_threads(
                       guild_id,family_id,thread_id,parent_channel_id,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?)
                   ON CONFLICT(guild_id,family_id) DO UPDATE SET
                       thread_id=excluded.thread_id,parent_channel_id=excluded.parent_channel_id,updated_at=excluded.updated_at""",
                (int(guild_id), int(family_id), int(thread_id), int(parent_channel_id), now, now),
            )
            await db.commit()

    async def all_managed_thread_ids(self) -> list[dict[str, Any]]:
        """Every Discord thread ID this bot tracks, across every system that owns one.

        Used by the GM dashboard/`reset_database.sh` world reset to clean up every
        thread the bot created before the rows that reference them are wiped, since
        once the database is gone there is no other way to find them again.
        """
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            out: list[dict[str, Any]] = []
            for kind, sql in (
                ("expedition_journal", "SELECT thread_id FROM expedition_threads"),
                ("birth_family_household", "SELECT thread_id FROM birth_family_household_threads"),
                ("sect_abode", "SELECT thread_id FROM sect_abodes WHERE thread_id IS NOT NULL"),
                ("cave_abode", "SELECT thread_id FROM cave_abodes WHERE thread_id IS NOT NULL"),
                ("world_event_scene", "SELECT thread_id FROM event_threads"),
                ("battle", "SELECT thread_id FROM battles WHERE thread_id IS NOT NULL"),
            ):
                cur = await db.execute(sql)
                for row in await cur.fetchall():
                    if row["thread_id"] is not None:
                        out.append({"kind": kind, "thread_id": int(row["thread_id"])})
            return out

    async def get_characters_at_location(self, location: str, *, exclude_user_id: int | None = None) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            if exclude_user_id is None:
                cur = await db.execute(
                    "SELECT user_id,name,discord_name,location FROM characters WHERE location=? AND life_status='alive' ORDER BY name,user_id",
                    (str(location),),
                )
            else:
                cur = await db.execute(
                    "SELECT user_id,name,discord_name,location FROM characters WHERE location=? AND life_status='alive' AND user_id<>? ORDER BY name,user_id",
                    (str(location), int(exclude_user_id)),
                )
            return [dict(row) for row in await cur.fetchall()]

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


    # ---------- Secret realms and inheritances ----------

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


    async def get_inheritances(self, user_id: int) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row
            cur=await db.execute(
                "SELECT inheritance_id,source_realm_id,acquired_at FROM inheritances WHERE user_id=? ORDER BY acquired_at",
                (user_id,),
            )
            return [dict(r) for r in await cur.fetchall()]


    # ---------- Sect recruitment, discovery, and NPC recommendations ----------
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

    async def get_sect_membership(self, user_id: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM sect_membership WHERE user_id=?", (user_id,))
            row = await cur.fetchone()
            return dict(row) if row else None

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


    # Generic effects
    # ------------------------------------------------------------------
    async def get_active_effects(self, user_id: int, game_minute: int) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT * FROM active_effects
                   WHERE user_id=? AND starts_game_minute<=?
                     AND (ends_game_minute IS NULL OR ends_game_minute>?)
                   ORDER BY id""",
                (int(user_id), int(game_minute), int(game_minute)),
            )
            rows = await cur.fetchall()
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

    # ------------------------------------------------------------------
    # Sect hierarchy/resources
    # ------------------------------------------------------------------

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


    async def get_sect_treasury(self,sect_name:str)->dict[str,int]:
        async with self._connect() as db:
            cur=await db.execute("SELECT item_id,quantity FROM sect_treasury WHERE sect_name=? AND quantity>0 ORDER BY item_id",(sect_name,))
            return {str(k):int(v) for k,v in await cur.fetchall()}


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


    async def get_fate_ledger(self, user_id: int, *, limit: int = 10) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM fate_ledger WHERE user_id=? ORDER BY entry_id DESC LIMIT ?",
                (int(user_id), max(1, min(50, int(limit)))),
            )
            return [dict(row) for row in await cur.fetchall()]


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

    # ------------------------------------------------------------------
    # GM-authored per-channel messages (schema 25) - one persistent, editable
    # welcome/orientation message per base channel or realm-hub, edited in
    # place the same way the #xianxia-info guide is (see ensure_channel_message
    # in app/bot/main.py).
    # ------------------------------------------------------------------

    async def get_channel_messages(self, guild_id: int) -> dict[str, dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT channel_key,content,message_id,updated_at FROM channel_messages WHERE guild_id=?",
                (int(guild_id),),
            )
            return {
                str(row["channel_key"]): {
                    "content": str(row["content"] or ""),
                    "message_id": row["message_id"],
                    "updated_at": row["updated_at"],
                }
                for row in await cur.fetchall()
            }

    async def set_channel_message(self, guild_id: int, channel_key: str, *, content: str, message_id: int | None) -> None:
        now = time.time()
        async with self._connect() as db:
            await db.execute(
                """INSERT INTO channel_messages(guild_id,channel_key,content,message_id,updated_at)
                   VALUES(?,?,?,?,?) ON CONFLICT(guild_id,channel_key) DO UPDATE SET
                   content=excluded.content,message_id=excluded.message_id,updated_at=excluded.updated_at""",
                (int(guild_id), str(channel_key), str(content), int(message_id) if message_id else None, now),
            )
            await db.commit()

    # ------------------------------------------------------------------
    # Auction house with escrow bidding
    # ------------------------------------------------------------------

    async def list_active_auctions(self,house_id:str|None=None)->list[dict[str,Any]]:
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row
            if house_id:
                cur=await db.execute("SELECT * FROM auctions WHERE active=1 AND house_id=? ORDER BY ends_at",(house_id,))
            else:
                cur=await db.execute("SELECT * FROM auctions WHERE active=1 ORDER BY ends_at")
            return [dict(r) for r in await cur.fetchall()]



    # ------------------------------------------------------------------
    # Auction door risks, temporary battles, and player families
    # ------------------------------------------------------------------

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



    async def get_player_family(self,user_id:int)->dict[str,Any]|None:
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; cur=await db.execute("SELECT f.* FROM player_families f JOIN player_family_members m ON m.family_id=f.family_id WHERE m.user_id=?",(user_id,)); row=await cur.fetchone()
            if not row: return None
            data=dict(row); cur=await db.execute("SELECT m.user_id,m.seniority_order,m.joined_at,c.name,c.address_style,c.gender,c.realm_index,c.phase FROM player_family_members m JOIN characters c ON c.user_id=m.user_id WHERE m.family_id=? ORDER BY m.seniority_order",(int(data['family_id']),)); data['members']=[dict(r) for r in await cur.fetchall()]; return data


    # ------------------------------------------------------------------
    # Lifespan / descendants
    # ------------------------------------------------------------------

    async def get_family_children(self,family_id:int)->list[dict[str,Any]]:
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; cur=await db.execute("SELECT fc.*,c.name AS parent_name FROM family_children fc JOIN characters c ON c.user_id=fc.parent_user_id WHERE fc.family_id=? ORDER BY fc.birth_game_minute,fc.child_id",(int(family_id),)); return [dict(r) for r in await cur.fetchall()]


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


    async def get_dao_progress(self,user_id:int)->list[dict[str,Any]]:
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; cur=await db.execute("SELECT * FROM dao_progress WHERE user_id=? ORDER BY progress DESC,dao_id",(user_id,)); return [dict(r) for r in await cur.fetchall()]


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

    async def get_samsara_dynasty_history(self, user_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
        """Return persistent cross-incarnation family-history records for one soul."""
        safe_limit = max(1, min(100, int(limit)))
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT *
                   FROM samsara_dynasty_history
                   WHERE user_id=?
                   ORDER BY incarnation_number DESC,history_id DESC
                   LIMIT ?""",
                (int(user_id), safe_limit),
            )
            rows = await cur.fetchall()
        history: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["evidence"] = json.loads(item.get("evidence_json") or "[]")
            except Exception:
                item["evidence"] = []
            history.append(item)
        return history

    async def get_samsara_legacy_state(self, user_id: int, *, history_id: int = 0) -> dict[str, list[dict[str, Any]]]:
        """Return persistent ancestral leads, quests, claims and conflicts for one soul."""
        uid = int(user_id)
        hid = max(0, int(history_id))
        where = "user_id=?"
        params: tuple[Any, ...] = (uid,)
        if hid:
            where += " AND history_id=?"
            params = (uid, hid)
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            lead_cur = await db.execute(
                f"""SELECT * FROM samsara_ancestral_leads
                    WHERE {where} AND status!='hidden'
                    ORDER BY history_id DESC,clue_required,lead_id""",
                params,
            )
            quest_cur = await db.execute(
                f"""SELECT * FROM samsara_investigation_quests
                    WHERE {where} AND status!='locked'
                    ORDER BY history_id DESC,quest_id""",
                params,
            )
            claim_cur = await db.execute(
                f"""SELECT * FROM samsara_dynasty_claims
                    WHERE {where}
                    ORDER BY history_id DESC,claim_id""",
                params,
            )
            conflict_cur = await db.execute(
                f"""SELECT * FROM samsara_dynasty_conflicts
                    WHERE {where}
                    ORDER BY history_id DESC,conflict_id""",
                params,
            )
            leads = [dict(row) for row in await lead_cur.fetchall()]
            quests = [dict(row) for row in await quest_cur.fetchall()]
            claims = [dict(row) for row in await claim_cur.fetchall()]
            conflicts = [dict(row) for row in await conflict_cur.fetchall()]
        return {
            "leads": leads,
            "quests": quests,
            "claims": claims,
            "conflicts": conflicts,
        }


    # ------------------------------------------------------------------
    # Persistent conditions, tribulations, professions and social justice
    # ------------------------------------------------------------------

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


    async def get_tribulation_state(self, user_id: int, gate_realm_index: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM tribulation_state WHERE user_id=? AND gate_realm_index=?",
                (int(user_id), int(gate_realm_index)),
            )
            row = await cur.fetchone()
            return dict(row) if row else None


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

    async def get_alchemy_state(self, user_id: int) -> dict[str, Any]:
        """The stored alchemy counters, read as they are.

        Until v0.30.0 this read also settled natural pill-toxicity decay with
        a Python copy of the decay constants. The engine settles toxicity
        wherever it reads the table and `effects.current` previews the
        settled figure, so a presenter that wants the decayed value asks the
        engine; this returns what is stored, and writes nothing.
        """
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM alchemy_state WHERE user_id=?", (int(user_id),))
            row = await cur.fetchone()
            return dict(row) if row else {
                "user_id": int(user_id), "pill_toxicity": 0, "last_toxicity_game_minute": 0,
                "total_refinements": 0, "successful_refinements": 0, "flawless_refinements": 0,
                "best_margin": -99, "last_quality": "",
            }

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


    async def get_reputations(self, user_id: int) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM faction_reputation WHERE user_id=? ORDER BY ABS(score) DESC,faction_key", (int(user_id),)
            )
            return [dict(r) for r in await cur.fetchall()]


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


    async def get_wild_beast_encounters(
        self, user_id: int, *, game_minute: int, location: str | None = None, include_resolved: bool = False,
    ) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            sql = "SELECT * FROM wild_beast_encounters WHERE user_id=?"
            params: list[Any] = [int(user_id)]
            if not include_resolved:
                sql += " AND status='available' AND expires_game_minute>?"
                params.append(int(game_minute))
            if location is not None:
                sql += " AND location=?"
                params.append(str(location))
            sql += " ORDER BY encounter_id DESC LIMIT 20"
            cur = await db.execute(sql, tuple(params))
            return [dict(r) for r in await cur.fetchall()]


    async def get_artifact_bonds(self, user_id: int, item_id: str | None=None) -> list[dict[str, Any]]:
        sql="SELECT * FROM artifact_bonds WHERE user_id=?"; params:[Any]=[int(user_id)]
        if item_id is not None: sql+=" AND item_id=?"; params.append(str(item_id))
        sql+=" ORDER BY awakened DESC,resonance DESC,item_id"
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; cur=await db.execute(sql,tuple(params)); return [dict(r) for r in await cur.fetchall()]


    async def get_territories(self, region: str | None=None) -> list[dict[str, Any]]:
        sql="SELECT * FROM territory_state"; params:list[Any]=[]
        if region: sql+=" WHERE region=?"; params.append(str(region))
        sql+=" ORDER BY region,name"
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; cur=await db.execute(sql,tuple(params)); return [dict(r) for r in await cur.fetchall()]



    async def get_territory_wars(self, *, active_only: bool=True) -> list[dict[str, Any]]:
        sql="SELECT * FROM territory_wars" + (" WHERE status='active'" if active_only else "") + " ORDER BY war_id DESC"
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; cur=await db.execute(sql); rows=[dict(r) for r in await cur.fetchall()]
            for row in rows:
                cur=await db.execute("SELECT * FROM territory_war_operations WHERE war_id=?",(int(row['war_id']),))
                op=await cur.fetchone(); row['operations']=dict(op) if op else {}
            return rows


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


    async def get_current_era(self) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; cur=await db.execute("SELECT * FROM world_eras WHERE active=1 ORDER BY era_id DESC LIMIT 1"); row=await cur.fetchone()
        if not row: return None
        out=dict(row)
        try: out["modifiers"]=json.loads(out.pop("modifiers_json") or "{}")
        except Exception: out["modifiers"]={}
        # The cycle template (modifiers, duration) is folded in by
        # app.rules.advanced_runtime.describe_era at the presenter (v0.30.0).
        return out


    async def get_party(self, user_id: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row
            cur=await db.execute("""SELECT p.* FROM parties p JOIN party_members pm ON pm.party_id=p.party_id WHERE pm.user_id=? AND p.status='active' ORDER BY p.party_id DESC LIMIT 1""",(int(user_id),)); p=await cur.fetchone()
            if not p: return None
            out=dict(p); cur=await db.execute("SELECT * FROM party_members WHERE party_id=? ORDER BY CASE role WHEN 'leader' THEN 0 ELSE 1 END,joined_at",(int(out['party_id']),)); out['members']=[dict(r) for r in await cur.fetchall()]; return out



    async def get_hidden_sect_membership(self, user_id: int) -> dict[str, Any] | None:
        async with self._connect() as db:
            db.row_factory=aiosqlite.Row; cur=await db.execute("SELECT * FROM hidden_sect_membership WHERE user_id=?",(int(user_id),)); row=await cur.fetchone(); return dict(row) if row else None

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

    async def get_narration_chain(self) -> dict[str, str]:
        """The GM-chosen narration chain, or {} when none has ever been set.

        Written by the engine's admin.narration.set_chain (world_state, one
        JSON blob, audited); read here so the bot can apply it at startup
        instead of only ever seeing the .env defaults. An empty dict means
        "the dashboard has never chosen", which is different from a slot the
        GM deliberately cleared - that one is stored as "".
        """
        async with self._connect() as db:
            cur = await db.execute("SELECT value_json FROM world_state WHERE key='narration_chain'")
            row = await cur.fetchone()
        if not row:
            return {}
        try:
            stored = json.loads(row[0])
        except Exception:
            return {}
        if not isinstance(stored, dict):
            return {}
        return {str(key): str(value or "") for key, value in stored.items()}

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
            # The phase's name and numbers come from the boss template at
            # the presenter (boss_encounter_phase, v0.30.0); the row keeps
            # the engine's phase_index.
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



    async def get_territory_war_actions(self, war_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row; cur = await db.execute("SELECT * FROM territory_war_actions WHERE war_id=? ORDER BY action_id DESC LIMIT ?", (int(war_id), max(1,min(100,int(limit))))); return [dict(r) for r in await cur.fetchall()]


    async def get_caravan_events(self, caravan_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row; cur = await db.execute("SELECT * FROM caravan_events WHERE caravan_id=? ORDER BY event_id DESC LIMIT ?", (int(caravan_id), max(1,min(100,int(limit))))); rows=[dict(r) for r in await cur.fetchall()]
        for r in rows:
            try: r["detail"] = json.loads(r.pop("detail_json") or "{}")
            except Exception: r["detail"] = {}
        return rows


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
