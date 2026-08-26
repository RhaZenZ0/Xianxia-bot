from __future__ import annotations

import asyncio
import base64
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import aiosqlite

from .worldtime import from_game_minutes
from .database.remote import GoDatabaseTransport, RemoteDatabaseError
from .game_engine import GameEngineClient, GameEngineError

log = logging.getLogger("xianxia.dashboard")

ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "dashboard"


@dataclass(frozen=True)
class DashboardSettings:
    database_path: Path
    host: str
    port: int
    username: str
    token: str
    admin_writes: bool = False
    engine_url: str = ""

    @classmethod
    def from_env(cls) -> "DashboardSettings":
        database_path = Path(os.getenv("DATABASE_PATH", "data/xianxia.sqlite3"))
        host = os.getenv("DASHBOARD_HOST", "0.0.0.0").strip() or "0.0.0.0"
        try:
            port = int(os.getenv("DASHBOARD_INTERNAL_PORT", "8090"))
        except ValueError as exc:
            raise RuntimeError("DASHBOARD_INTERNAL_PORT must be an integer") from exc
        if not 1 <= port <= 65535:
            raise RuntimeError("DASHBOARD_INTERNAL_PORT must be between 1 and 65535")
        username = os.getenv("DASHBOARD_USERNAME", "admin").strip() or "admin"
        token = os.getenv("DASHBOARD_TOKEN", "").strip()
        if len(token) < 20 or token in {"change-me", "CHANGE_ME", "CHANGE_ME_LONG_RANDOM_TOKEN"}:
            raise RuntimeError(
                "DASHBOARD_TOKEN must be set to a private random value of at least 20 characters before starting the dashboard"
            )
        admin_writes = os.getenv("DASHBOARD_ADMIN_WRITES", "true").strip().lower() in {"1", "true", "yes", "on"}
        engine_url = os.getenv("GAME_ENGINE_URL", "").strip().rstrip("/")
        if admin_writes and not engine_url:
            raise RuntimeError("GAME_ENGINE_URL is required when DASHBOARD_ADMIN_WRITES is enabled")
        return cls(database_path=database_path, host=host, port=port, username=username, token=token, admin_writes=admin_writes, engine_url=engine_url)


class ReadOnlyDashboardStore:
    """Read-only projection of canonical Xianxia RP state.

    Production reads use query-only sessions hosted by the authoritative Go engine,
    so the dashboard never opens the canonical SQLite file directly. Local tests may
    use SQLite ``mode=ro`` with ``PRAGMA query_only=ON`` when no engine is configured.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        engine_url = os.getenv("GAME_ENGINE_URL", "").strip()
        self._go_transport = GoDatabaseTransport(engine_url) if engine_url else None

    @asynccontextmanager
    async def _connect(self):
        # Production reads through a query-only session owned by the Go engine, so
        # even the dashboard never opens the canonical SQLite file itself.
        if self._go_transport is not None:
            db = await self._go_transport.open()
            db.row_factory = object()
            try:
                await db.execute("PRAGMA query_only=ON")
                await db.execute("PRAGMA busy_timeout=3000")
                yield db
            finally:
                await db.close()
            return

        # Local test/dev fallback when no engine is configured.
        uri = f"file:{self.path.resolve()}?mode=ro"
        try:
            connector = aiosqlite.connect(uri, uri=True)
        except TypeError:  # lightweight test shim has no uri= argument
            connector = aiosqlite.connect(str(self.path))
        async with connector as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA query_only=ON")
            await db.execute("PRAGMA busy_timeout=3000")
            yield db

    @staticmethod
    async def _fetchall(db: Any, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        cur = await db.execute(sql, params)
        return [dict(row) for row in await cur.fetchall()]

    @staticmethod
    async def _fetchone(db: Any, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        cur = await db.execute(sql, params)
        row = await cur.fetchone()
        return dict(row) if row else None

    @staticmethod
    async def _scalar(db: Any, sql: str, params: tuple[Any, ...] = (), default: int = 0) -> int:
        cur = await db.execute(sql, params)
        row = await cur.fetchone()
        return int(row[0]) if row and row[0] is not None else default

    @staticmethod
    async def _table_exists(db: Any, name: str) -> bool:
        cur = await db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
        return bool(await cur.fetchone())

    async def schema_version(self) -> int:
        async with self._connect() as db:
            if not await self._table_exists(db, "schema_version"):
                return 0
            return await self._scalar(db, "SELECT current_version FROM schema_version WHERE singleton=1")

    async def _world_clock(self, db: Any) -> dict[str, Any]:
        row = await self._fetchone(db, "SELECT value_json FROM world_state WHERE key='world_clock'")
        now = time.time()
        if not row:
            game_minute = 8 * 60
            scale = 0
        else:
            try:
                state = json.loads(str(row.get("value_json") or "{}"))
            except Exception:
                state = {}
            scale = max(0, int(state.get("scale", 0)))
            anchor_game = int(state.get("anchor_game_minute", 8 * 60))
            anchor_real = float(state.get("anchor_real_ts", now))
            elapsed = max(0.0, (now - anchor_real) / 60.0)
            game_minute = int(anchor_game + elapsed * scale)
        wt = from_game_minutes(game_minute)
        return {
            "game_minute": game_minute,
            "display": wt.display,
            "year": wt.year,
            "month": wt.month,
            "day": wt.day,
            "clock": wt.clock,
            "season": wt.season,
            "period": wt.period,
            "scale": scale,
        }

    async def overview(self) -> dict[str, Any]:
        async with self._connect() as db:
            clock = await self._world_clock(db)
            schema = 0
            if await self._table_exists(db, "schema_version"):
                schema = await self._scalar(db, "SELECT current_version FROM schema_version WHERE singleton=1")
            counts: dict[str, int] = {}
            count_queries = {
                "players": "SELECT COUNT(*) FROM characters",
                "players_alive": "SELECT COUNT(*) FROM characters WHERE life_status='alive'",
                "npcs_alive": "SELECT COUNT(*) FROM npc_civilization_state WHERE status='alive'",
                "npcs_injured": "SELECT COUNT(*) FROM npc_life_state l JOIN npc_civilization_state c ON c.npc_name=l.npc_name WHERE c.status='alive' AND l.injury_severity>0",
                "npc_marriages": "SELECT COUNT(*) FROM npc_life_state WHERE relationship_status='married'",
                "npc_descendants": "SELECT COUNT(*) FROM npc_descendants WHERE status='alive'",
                "world_history": "SELECT COUNT(*) FROM world_history_events",
                "rag_memories": "SELECT COUNT(*) FROM rag_memories",
                "active_world_events": "SELECT COUNT(*) FROM world_events WHERE active=1",
                "active_battles": "SELECT COUNT(*) FROM battles WHERE status='active'",
                "active_wars": "SELECT COUNT(*) FROM territory_wars WHERE status='active'",
                "sects": "SELECT COUNT(*) FROM sect_politics_state",
                "birth_families": "SELECT COUNT(*) FROM birth_families WHERE line_status='active'",
            }
            for key, sql in count_queries.items():
                try:
                    counts[key] = await self._scalar(db, sql)
                except Exception:
                    counts[key] = 0
            sims = await self._fetchall(
                db,
                "SELECT system,last_game_minute,interval_game_minutes,last_run_real,runs FROM world_simulation_state ORDER BY system",
            ) if await self._table_exists(db, "world_simulation_state") else []
            for row in sims:
                row["lag_game_minutes"] = max(0, int(clock["game_minute"]) - int(row.get("last_game_minute") or 0))
            era = None
            if await self._table_exists(db, "world_eras"):
                era = await self._fetchone(db, "SELECT * FROM world_eras WHERE active=1 ORDER BY era_id DESC LIMIT 1")
            recent = await self._fetchall(
                db,
                """SELECT history_id,event_type,title,summary,significance,visibility,location,faction,actor_name,target_name,game_minute
                   FROM world_history_events ORDER BY game_minute DESC,significance DESC,history_id DESC LIMIT 8""",
            ) if await self._table_exists(db, "world_history_events") else []
            return {"schema_version": schema, "clock": clock, "counts": counts, "simulations": sims, "active_era": era, "recent_history": recent}

    async def timeline(self, *, limit: int = 100, q: str = "", event_type: str = "", visibility: str = "") -> dict[str, Any]:
        limit = max(1, min(300, int(limit)))
        clauses = ["1=1"]
        params: list[Any] = []
        if q.strip():
            needle = f"%{q.strip()}%"
            clauses.append("(title LIKE ? COLLATE NOCASE OR summary LIKE ? COLLATE NOCASE OR actor_name LIKE ? COLLATE NOCASE OR target_name LIKE ? COLLATE NOCASE OR location LIKE ? COLLATE NOCASE OR faction LIKE ? COLLATE NOCASE)")
            params.extend([needle] * 6)
        if event_type.strip():
            clauses.append("event_type=?")
            params.append(event_type.strip())
        if visibility.strip():
            clauses.append("visibility=?")
            params.append(visibility.strip())
        params.append(limit)
        async with self._connect() as db:
            rows = await self._fetchall(
                db,
                "SELECT * FROM world_history_events WHERE " + " AND ".join(clauses) + " ORDER BY game_minute DESC,significance DESC,history_id DESC LIMIT ?",
                tuple(params),
            )
            types = [r["event_type"] for r in await self._fetchall(db, "SELECT DISTINCT event_type FROM world_history_events ORDER BY event_type")]
            return {"rows": rows, "event_types": types}

    async def npcs(self, *, limit: int = 200, q: str = "", location: str = "", faction: str = "", status: str = "alive") -> dict[str, Any]:
        limit = max(1, min(500, int(limit)))
        clauses = ["1=1"]
        params: list[Any] = []
        if q.strip():
            needle = f"%{q.strip()}%"
            clauses.append("(c.npc_name LIKE ? COLLATE NOCASE OR c.profession LIKE ? COLLATE NOCASE OR c.activity LIKE ? COLLATE NOCASE OR m.current_goal LIKE ? COLLATE NOCASE)")
            params.extend([needle] * 4)
        if location.strip():
            clauses.append("c.current_location=?")
            params.append(location.strip())
        if faction.strip():
            clauses.append("c.faction=?")
            params.append(faction.strip())
        if status.strip() and status != "all":
            clauses.append("c.status=?")
            params.append(status.strip())
        params.append(limit)
        sql = """SELECT c.npc_name,c.home_location,c.current_location,c.world_name,c.profession,c.faction,c.wealth,c.influence,c.ambition,
                        c.realm_index,c.phase,c.status,c.activity,c.last_game_minute,
                        l.birth_game_minute,l.age_at_creation_years,l.natural_lifespan_years,l.health,l.injury,l.injury_severity,l.sect_rank,
                        l.relationship_status,l.spouse_name,l.children_count,l.death_game_minute,l.cause_of_death,
                        m.current_goal,m.mood,m.focus_target,m.recent_event,m.goal_progress
                 FROM npc_civilization_state c
                 LEFT JOIN npc_life_state l ON l.npc_name=c.npc_name
                 LEFT JOIN npc_mind_state m ON m.npc_name=c.npc_name
                 WHERE """ + " AND ".join(clauses) + " ORDER BY c.status DESC,c.realm_index DESC,c.phase DESC,c.influence DESC,c.npc_name LIMIT ?"
        async with self._connect() as db:
            rows = await self._fetchall(db, sql, tuple(params))
            clock = await self._world_clock(db)
            for row in rows:
                if row.get("birth_game_minute") is not None:
                    elapsed = max(0, int(clock["game_minute"]) - int(row.get("birth_game_minute") or 0))
                    row["age_years"] = round(float(row.get("age_at_creation_years") or 18) + elapsed / (60 * 24 * 30 * 12), 1)
            locations = [r["current_location"] for r in await self._fetchall(db, "SELECT DISTINCT current_location FROM npc_civilization_state WHERE current_location<>'' ORDER BY current_location")]
            factions = [r["faction"] for r in await self._fetchall(db, "SELECT DISTINCT faction FROM npc_civilization_state WHERE faction<>'' ORDER BY faction")]
            return {"rows": rows, "locations": locations, "factions": factions}

    async def npc_detail(self, name: str) -> dict[str, Any]:
        async with self._connect() as db:
            row = await self._fetchone(
                db,
                """SELECT c.*,l.birth_game_minute,l.age_at_creation_years,l.natural_lifespan_years,l.health,l.injury,l.injury_severity,
                          l.sect_rank,l.career_progress,l.relationship_status,l.spouse_name,l.children_count,l.death_game_minute,l.cause_of_death,
                          m.current_goal,m.mood,m.focus_target,m.recent_event,m.goal_progress,m.last_game_minute AS mind_game_minute
                   FROM npc_civilization_state c
                   LEFT JOIN npc_life_state l ON l.npc_name=c.npc_name
                   LEFT JOIN npc_mind_state m ON m.npc_name=c.npc_name
                   WHERE c.npc_name=?""",
                (name,),
            )
            if not row:
                return {}
            social = await self._fetchall(db, "SELECT * FROM npc_social_relations WHERE npc_a=? OR npc_b=? ORDER BY grudge DESC,affinity DESC,trust DESC", (name, name))
            disciples = await self._fetchall(db, "SELECT * FROM npc_disciple_bonds WHERE master_name=? OR disciple_name=? ORDER BY started_game_minute DESC", (name, name))
            descendants = await self._fetchall(db, "SELECT * FROM npc_descendants WHERE parent_a=? OR parent_b=? ORDER BY birth_game_minute DESC", (name, name))
            history = await self._fetchall(db, "SELECT * FROM world_history_events WHERE actor_name=? OR target_name=? OR related_npc_name=? ORDER BY game_minute DESC,significance DESC LIMIT 40", (name, name, name))
            catalog = await self._fetchone(db, "SELECT data_json FROM catalog_npcs WHERE name=?", (name,))
            gm_definition: dict[str, Any] = {}
            if catalog:
                try:
                    gm_definition = json.loads(str(catalog.get("data_json") or "{}"))
                except Exception:
                    gm_definition = {"raw": str(catalog.get("data_json") or "")}
            return {"npc": row, "social": social, "disciples": disciples, "descendants": descendants, "history": history, "gm_definition": gm_definition}

    async def families(self) -> dict[str, Any]:
        async with self._connect() as db:
            clock = await self._world_clock(db)
            birth = await self._fetchall(db, "SELECT * FROM birth_families ORDER BY line_status DESC,influence DESC,wealth DESC,family_name")
            birth_npcs = await self._fetchall(db, "SELECT * FROM birth_family_npcs ORDER BY family_id,status DESC,relation,name")
            player_links = await self._fetchall(
                db,
                """SELECT cbf.family_id,cbf.user_id,cbf.birth_order,cbf.generation,c.name,c.discord_name,c.life_status,c.realm_index,c.phase
                   FROM character_birth_family cbf JOIN characters c ON c.user_id=cbf.user_id ORDER BY cbf.family_id,cbf.birth_order""",
            )
            branches = await self._fetchall(db, "SELECT * FROM martial_clan_branches ORDER BY family_id,status DESC,martial_strength DESC") if await self._table_exists(db, "martial_clan_branches") else []
            retainers = await self._fetchall(db, "SELECT * FROM martial_clan_retainers ORDER BY family_id,status DESC,loyalty DESC") if await self._table_exists(db, "martial_clan_retainers") else []
            custom = await self._fetchall(
                db,
                """SELECT pf.*,c.name AS founder_name FROM player_families pf JOIN characters c ON c.user_id=pf.founder_user_id ORDER BY pf.family_id""",
            )
            custom_members = await self._fetchall(
                db,
                """SELECT pfm.*,c.name,c.discord_name,c.realm_index,c.phase,c.life_status FROM player_family_members pfm JOIN characters c ON c.user_id=pfm.user_id ORDER BY pfm.family_id,pfm.seniority_order""",
            )
            custom_children = await self._fetchall(db, "SELECT * FROM family_children ORDER BY family_id,birth_game_minute")
            npc_desc = await self._fetchall(db, "SELECT * FROM npc_descendants ORDER BY birth_game_minute DESC LIMIT 500")
            npc_marriages = await self._fetchall(db, "SELECT npc_name,spouse_name,children_count,sect_rank FROM npc_life_state WHERE relationship_status='married' ORDER BY npc_name")
            return {
                "clock": clock,
                "birth_families": birth,
                "birth_family_npcs": birth_npcs,
                "birth_family_players": player_links,
                "clan_branches": branches,
                "clan_retainers": retainers,
                "player_families": custom,
                "player_family_members": custom_members,
                "player_family_children": custom_children,
                "npc_descendants": npc_desc,
                "npc_marriages": npc_marriages,
            }

    async def sects(self) -> dict[str, Any]:
        async with self._connect() as db:
            sects = await self._fetchall(
                db,
                """SELECT p.*,COALESCE(s.prestige,0) AS prestige,COALESCE(s.treasury_stones,0) AS treasury_stones,
                          (SELECT COUNT(*) FROM sect_membership sm WHERE sm.sect_name=p.sect_name) AS player_members,
                          (SELECT COUNT(*) FROM npc_civilization_state nc WHERE nc.faction=p.sect_name AND nc.status='alive') AS npc_members
                   FROM sect_politics_state p LEFT JOIN sects s ON s.sect_name=p.sect_name
                   ORDER BY p.influence DESC,p.resources DESC,p.sect_name""",
            )
            factions = await self._fetchall(db, "SELECT * FROM sect_factions ORDER BY sect_name,power DESC,faction_name")
            relations = await self._fetchall(db, "SELECT * FROM sect_relations ORDER BY ABS(relation_score) DESC,sect_a,sect_b")
            events = await self._fetchall(db, "SELECT * FROM sect_politics_events ORDER BY game_minute DESC,event_id DESC LIMIT 100")
            memberships = await self._fetchall(
                db,
                """SELECT sm.*,c.name,c.discord_name,c.realm_index,c.phase,c.life_status FROM sect_membership sm JOIN characters c ON c.user_id=sm.user_id ORDER BY sm.sect_name,sm.rank_level DESC,c.realm_index DESC""",
            )
            return {"sects": sects, "factions": factions, "relations": relations, "events": events, "memberships": memberships}

    async def conflicts(self) -> dict[str, Any]:
        async with self._connect() as db:
            wars = await self._fetchall(
                db,
                """SELECT w.*,t.name AS territory_name,t.region,t.controller_key,o.siege_progress,o.attacker_morale,o.defender_morale,
                          o.attacker_force,o.defender_force,o.winner_key,o.resolution,o.occupation_until_game_minute
                   FROM territory_wars w JOIN territory_state t ON t.territory_key=w.territory_key
                   LEFT JOIN territory_war_operations o ON o.war_id=w.war_id
                   ORDER BY (w.status='active') DESC,w.updated_game_minute DESC,w.war_id DESC LIMIT 100""",
            )
            battles = await self._fetchall(
                db,
                """SELECT b.*,c.name AS player_name,c.discord_name FROM battles b JOIN characters c ON c.user_id=b.user_id
                   ORDER BY (b.status='active') DESC,b.updated_at DESC LIMIT 100""",
            )
            npc_feuds = await self._fetchall(db, "SELECT * FROM npc_social_relations WHERE grudge>0 OR relation_type IN ('grudge','blood_feud','rival') ORDER BY grudge DESC,last_interaction_game_minute DESC LIMIT 150")
            player_grudges = await self._fetchall(
                db,
                """SELECT g.*,c.name AS player_name FROM grudges g JOIN characters c ON c.user_id=g.user_id WHERE g.status='active' ORDER BY g.intensity DESC,g.updated_at DESC LIMIT 150""",
            )
            bounties = await self._fetchall(
                db,
                """SELECT b.*,c.name AS player_name,p.pursuit_id,p.hunter_name,p.hunter_power,p.status AS pursuit_status,p.pressure,p.escape_progress,p.capture_progress
                   FROM bounties b JOIN characters c ON c.user_id=b.user_id LEFT JOIN bounty_hunter_pursuits p ON p.bounty_id=b.bounty_id
                   WHERE b.status='active' OR p.status IN ('tracking','engaged') ORDER BY b.amount DESC,b.bounty_id DESC LIMIT 100""",
            ) if await self._table_exists(db, "bounty_hunter_pursuits") else []
            bosses = await self._fetchall(db, "SELECT * FROM boss_encounters ORDER BY (status='active') DESC,updated_at DESC LIMIT 50") if await self._table_exists(db, "boss_encounters") else []
            return {"wars": wars, "battles": battles, "npc_feuds": npc_feuds, "player_grudges": player_grudges, "bounties": bounties, "bosses": bosses}

    async def events(self) -> dict[str, Any]:
        async with self._connect() as db:
            world_events = await self._fetchall(db, "SELECT * FROM world_events ORDER BY active DESC,ends_at DESC LIMIT 150")
            civilization = await self._fetchall(db, "SELECT * FROM civilization_regions ORDER BY unrest DESC,security ASC,population DESC")
            civilization_events = await self._fetchall(db, "SELECT * FROM civilization_events ORDER BY game_minute DESC,event_id DESC LIMIT 100")
            era = await self._fetchall(db, "SELECT * FROM world_eras ORDER BY active DESC,started_game_minute DESC") if await self._table_exists(db, "world_eras") else []
            era_events = await self._fetchall(db, "SELECT * FROM world_era_events ORDER BY game_minute DESC,event_id DESC LIMIT 100") if await self._table_exists(db, "world_era_events") else []
            return {"world_events": world_events, "regions": civilization, "civilization_events": civilization_events, "eras": era, "era_events": era_events}

    async def players(self, *, limit: int = 200) -> dict[str, Any]:
        limit = max(1, min(500, int(limit)))
        async with self._connect() as db:
            rows = await self._fetchall(
                db,
                """SELECT c.user_id,c.discord_name,c.name,c.origin,c.path,c.spiritual_root,c.gender,c.life_status,c.karma_score,
                          c.realm_index,c.phase,c.cultivation,c.body_realm_index,c.body_phase,c.qi,c.qi_max,c.vitality,c.vitality_max,
                          c.spirit_stones,c.location,c.updated_at,sm.sect_name,sm.rank_name,
                          (SELECT COUNT(*) FROM battles b WHERE b.user_id=c.user_id AND b.status='active') AS active_battles,
                          (SELECT COUNT(*) FROM rag_memories r WHERE r.user_id=c.user_id) AS memory_count
                   FROM characters c LEFT JOIN sect_membership sm ON sm.user_id=c.user_id
                   ORDER BY c.life_status DESC,c.realm_index DESC,c.phase DESC,c.updated_at DESC LIMIT ?""",
                (limit,),
            )
            actions = await self._fetchall(
                db,
                """SELECT a.*,c.name AS player_name,c.discord_name FROM world_action_events a LEFT JOIN characters c ON c.user_id=a.user_id
                   ORDER BY a.game_minute DESC,a.action_id DESC LIMIT 120""",
            )
            scenes = await self._fetchall(
                db,
                """SELECT s.user_id,c.name AS player_name,MAX(s.id) AS last_scene_id,MAX(s.created_at) AS last_scene_at,
                          SUM(CASE WHEN s.speaker='player' THEN 1 ELSE 0 END) AS player_messages
                   FROM scene_history s LEFT JOIN characters c ON c.user_id=s.user_id WHERE s.user_id IS NOT NULL
                   GROUP BY s.user_id,c.name ORDER BY last_scene_at DESC LIMIT 100""",
            )
            return {"players": rows, "recent_actions": actions, "scene_activity": scenes}

    async def rag(self, *, limit: int = 150, q: str = "", user_id: int | None = None, npc: str = "") -> dict[str, Any]:
        limit = max(1, min(400, int(limit)))
        clauses = ["1=1"]
        params: list[Any] = []
        if q.strip():
            needle = f"%{q.strip()}%"
            clauses.append("(r.summary LIKE ? COLLATE NOCASE OR r.memory_kind LIKE ? COLLATE NOCASE OR r.location LIKE ? COLLATE NOCASE OR r.npc_name LIKE ? COLLATE NOCASE OR r.faction LIKE ? COLLATE NOCASE)")
            params.extend([needle] * 5)
        if user_id is not None:
            clauses.append("r.user_id=?")
            params.append(int(user_id))
        if npc.strip():
            clauses.append("r.npc_name=?")
            params.append(npc.strip())
        params.append(limit)
        async with self._connect() as db:
            memories = await self._fetchall(
                db,
                """SELECT r.*,c.name AS player_name,c.discord_name FROM rag_memories r LEFT JOIN characters c ON c.user_id=r.user_id
                   WHERE """ + " AND ".join(clauses) + " ORDER BY r.salience DESC,r.game_minute DESC,r.memory_id DESC LIMIT ?",
                tuple(params),
            )
            canon_count = await self._scalar(db, "SELECT COUNT(*) FROM rag_canon_documents")
            history_count = await self._scalar(db, "SELECT COUNT(*) FROM world_history_events")
            players = await self._fetchall(db, "SELECT user_id,name,discord_name FROM characters ORDER BY name")
            return {"memories": memories, "canon_count": canon_count, "history_count": history_count, "players": players}

    async def decisions(self, *, limit: int = 150) -> dict[str, Any]:
        limit = max(1, min(400, int(limit)))
        async with self._connect() as db:
            minds = await self._fetchall(
                db,
                """SELECT m.*,c.current_location,c.faction,c.activity,c.realm_index,c.phase,c.status,l.health,l.injury,l.injury_severity,l.sect_rank
                   FROM npc_mind_state m JOIN npc_civilization_state c ON c.npc_name=m.npc_name
                   LEFT JOIN npc_life_state l ON l.npc_name=m.npc_name
                   ORDER BY m.last_game_minute DESC,m.goal_progress DESC LIMIT ?""",
                (limit,),
            )
            npc_history = await self._fetchall(
                db,
                """SELECT * FROM world_history_events
                   WHERE actor_type='npc' OR related_npc_name<>'' OR event_type IN ('marriage','descendant_birth','discipleship','major_battle','death','faction_change','sect_rank_change','breakthrough')
                   ORDER BY game_minute DESC,significance DESC,history_id DESC LIMIT ?""",
                (limit,),
            )
            sim = await self._fetchall(db, "SELECT * FROM world_simulation_state ORDER BY system")
            return {"minds": minds, "history": npc_history, "simulation": sim}


class AdminDashboardController:
    """Authenticated GM mutation surface backed by the authoritative Go engine."""

    ACTION_MAP = {
        "world.advance_time": "admin.world.advance_time",
        "player.grant_currency": "admin.player.grant_currency",
        "player.karma": "admin.player.karma",
        "player.teleport": "admin.player.teleport",
        "player.revive": "admin.player.revive",
        "player.clear_battle": "admin.player.clear_battle",
        "automation.set": "admin.automation.set",
        "simulation.interval": "admin.simulation.interval",
    }

    def __init__(self, store: ReadOnlyDashboardStore, engine_url: str, enabled: bool):
        self.store = store
        self.enabled = bool(enabled)
        self.engine_url = str(engine_url).strip().rstrip("/")
        self.engine = GameEngineClient(self.engine_url) if self.engine_url else None
        self.transport = GoDatabaseTransport(self.engine_url) if self.engine_url else None

    async def snapshot(self) -> dict[str, Any]:
        if not self.enabled or self.engine is None or self.transport is None:
            return {"enabled": False, "message": "Dashboard admin writes are disabled."}
        async with self.store._connect() as db:
            players = await self.store._fetchall(
                db,
                "SELECT user_id,name,discord_name,life_status,location,realm_index,phase,karma_score,vitality,vitality_max,qi,qi_max FROM characters ORDER BY name",
            )
            locations = [str(r["name"]) for r in await self.store._fetchall(db, "SELECT name FROM catalog_locations ORDER BY name")]
            if not locations:
                try:
                    world = json.loads((ROOT / "content" / "world.json").read_text(encoding="utf-8"))
                    locations = sorted(str(name) for name in (world.get("locations") or {}).keys())
                except Exception:
                    locations = []
            simulations = await self.store._fetchall(db, "SELECT system,last_game_minute,interval_game_minutes,runs FROM world_simulation_state ORDER BY system")
            audit = await self.store._fetchall(db, "SELECT * FROM admin_audit_log ORDER BY audit_id DESC LIMIT 40")
            automation = {
                "npc_civilization": True,
                "npc_life": True,
                "sect_politics": True,
                "dynamic_economy": True,
                "clan_dynamics": True,
                "background_seclusion": True,
                "black_markets": True,
                "autonomous_world_events": True,
            }
            row = await self.store._fetchone(db, "SELECT value_json FROM world_state WHERE key='automation_settings'")
            if row:
                try:
                    stored = json.loads(str(row.get("value_json") or "{}"))
                    automation = {k: bool(stored.get(k, v)) for k, v in automation.items()}
                except Exception:
                    pass
            clock = await self.store._world_clock(db)
            currencies = [str(r["currency_id"]) for r in await self.store._fetchall(db, "SELECT DISTINCT currency_id FROM currency_wallets ORDER BY currency_id")]
        for currency in ("low_spirit_stone", "low_spirit_crystal", "low_immortal_stone", "low_celestial_crystal"):
            if currency not in currencies:
                currencies.append(currency)
        return {
            "enabled": True,
            "players": players,
            "locations": locations,
            "currencies": sorted(currencies),
            "simulations": simulations,
            "automation": automation,
            "audit": audit,
            "clock": clock,
            "backups": await self.transport.list_backups(),
            "database": await self.transport.status(),
        }

    async def _audit(self, *, action: str, target: str = "", after: dict[str, Any] | None = None, reason: str = "") -> None:
        if self.engine is None:
            return
        await self.engine.action("admin.audit", 0, {"action": action, "target": target, "after": after or {}, "reason": reason})

    async def run(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled or self.engine is None or self.transport is None:
            raise PermissionError("Dashboard admin writes are disabled")
        action = str(action).strip()
        payload = dict(payload or {})
        reason = str(payload.get("reason") or "GM dashboard")[:500]
        if action in self.ACTION_MAP:
            result = await self.engine.action(self.ACTION_MAP[action], 0, payload)
            return {"ok": True, "action": action, "result": result}
        if action == "simulation.force":
            system = str(payload.get("system") or "").strip()
            steps = max(1, min(120, int(payload.get("steps") or 1)))
            async with self.store._connect() as db:
                clock = await self.store._world_clock(db)
            result = await self.engine.force_simulation(system, steps, int(clock["game_minute"]))
            await self._audit(action="dashboard.simulation.force", target=system, after={"steps": steps, "result": result}, reason=reason)
            return {"ok": True, "action": action, "result": result}
        if action == "backup.create":
            result = await self.transport.create_backup()
            await self._audit(action="dashboard.backup.create", target=str(result.get("name") or "backup"), after=result, reason=reason)
            return {"ok": True, "action": action, "result": result}
        if action in {"database.optimize", "database.vacuum"}:
            maintenance = "optimize" if action.endswith("optimize") else "vacuum"
            result = await self.transport.maintenance(maintenance)
            await self._audit(action=f"dashboard.{action}", target="sqlite", after=result, reason=reason)
            return {"ok": True, "action": action, "result": result}
        raise ValueError(f"Unsupported dashboard admin action: {action}")


class DashboardServer:
    def __init__(self, settings: DashboardSettings):
        self.settings = settings
        self.store = ReadOnlyDashboardStore(settings.database_path)
        self.admin = AdminDashboardController(self.store, settings.engine_url or os.getenv("GAME_ENGINE_URL", ""), settings.admin_writes)
        self._server: asyncio.AbstractServer | None = None

    def _authorized(self, headers: dict[str, str]) -> bool:
        value = headers.get("authorization", "")
        if not value.lower().startswith("basic "):
            return False
        try:
            raw = base64.b64decode(value.split(" ", 1)[1].strip(), validate=True).decode("utf-8")
            username, password = raw.split(":", 1)
        except Exception:
            return False
        return hmac.compare_digest(username, self.settings.username) and hmac.compare_digest(password, self.settings.token)

    async def serve(self) -> None:
        if self.store._go_transport is None and not self.settings.database_path.exists():
            raise RuntimeError(f"Dashboard database does not exist: {self.settings.database_path}")
        self._server = await asyncio.start_server(self._handle, self.settings.host, self.settings.port)
        addrs = ", ".join(str(sock.getsockname()) for sock in (self._server.sockets or []))
        log.info("Xianxia RP GM dashboard listening on %s", addrs)
        async with self._server:
            await self._server.serve_forever()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=5)
            if not request_line:
                return
            try:
                method, target, _version = request_line.decode("ascii", "replace").strip().split(" ", 2)
            except ValueError:
                await self._send_json(writer, 400, {"error": "bad_request"})
                return
            headers: dict[str, str] = {}
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5)
                if line in {b"\r\n", b"\n", b""}:
                    break
                text = line.decode("latin-1", "replace").strip()
                if ":" in text:
                    key, value = text.split(":", 1)
                    headers[key.strip().lower()] = value.strip()

            split = urlsplit(target)
            path = unquote(split.path)
            query = parse_qs(split.query, keep_blank_values=False)

            if path == "/livez":
                await self._send_json(writer, 200, {"ok": True, "service": "xianxia-rp-dashboard"})
                return

            if method not in {"GET", "POST"}:
                await self._send_json(writer, 405, {"error": "method_not_allowed"}, extra_headers={"Allow": "GET, POST"})
                return

            if not self._authorized(headers):
                await self._send_text(
                    writer,
                    401,
                    "Authentication required.\n",
                    "text/plain; charset=utf-8",
                    extra_headers={"WWW-Authenticate": 'Basic realm="Xianxia RP GM Dashboard", charset="UTF-8"'},
                )
                return

            if method == "POST":
                if path != "/api/admin/action":
                    await self._send_json(writer, 404, {"error": "not_found"}); return
                if not self.settings.admin_writes:
                    await self._send_json(writer, 403, {"error": "admin_writes_disabled"}); return
                if headers.get("x-xianxia-admin") != "1" or "application/json" not in headers.get("content-type", "").lower():
                    await self._send_json(writer, 403, {"error": "admin_header_required"}); return
                try:
                    length = int(headers.get("content-length", "0") or 0)
                except ValueError:
                    await self._send_json(writer, 400, {"error": "invalid_content_length"}); return
                if length < 2 or length > 65536:
                    await self._send_json(writer, 400, {"error": "invalid_body_size"}); return
                raw = await asyncio.wait_for(reader.readexactly(length), timeout=5)
                try:
                    body = json.loads(raw.decode("utf-8"))
                    action = str(body.get("action") or "")
                    payload = dict(body.get("payload") or {})
                    result = await self.admin.run(action, payload)
                except PermissionError as exc:
                    await self._send_json(writer, 403, {"error": "forbidden", "message": str(exc)}); return
                except (ValueError, TypeError, GameEngineError, RemoteDatabaseError) as exc:
                    await self._send_json(writer, 400, {"error": "admin_action_failed", "message": str(exc)}); return
                await self._send_json(writer, 200, result); return

            if path == "/" or path == "/index.html":
                await self._send_file(writer, STATIC_DIR / "index.html", "text/html; charset=utf-8")
                return
            if path == "/app.js":
                await self._send_file(writer, STATIC_DIR / "app.js", "application/javascript; charset=utf-8")
                return
            if path == "/styles.css":
                await self._send_file(writer, STATIC_DIR / "styles.css", "text/css; charset=utf-8")
                return
            if path == "/api/overview":
                await self._send_json(writer, 200, await self.store.overview()); return
            if path == "/api/timeline":
                await self._send_json(writer, 200, await self.store.timeline(
                    limit=_qint(query, "limit", 100), q=_q(query, "q"), event_type=_q(query, "event_type"), visibility=_q(query, "visibility")
                )); return
            if path == "/api/npcs":
                await self._send_json(writer, 200, await self.store.npcs(
                    limit=_qint(query, "limit", 200), q=_q(query, "q"), location=_q(query, "location"), faction=_q(query, "faction"), status=_q(query, "status", "alive")
                )); return
            if path == "/api/npc":
                name = _q(query, "name")
                await self._send_json(writer, 200 if name else 400, await self.store.npc_detail(name) if name else {"error": "name_required"}); return
            if path == "/api/families":
                await self._send_json(writer, 200, await self.store.families()); return
            if path == "/api/sects":
                await self._send_json(writer, 200, await self.store.sects()); return
            if path == "/api/conflicts":
                await self._send_json(writer, 200, await self.store.conflicts()); return
            if path == "/api/events":
                await self._send_json(writer, 200, await self.store.events()); return
            if path == "/api/players":
                await self._send_json(writer, 200, await self.store.players(limit=_qint(query, "limit", 200))); return
            if path == "/api/rag":
                uid_raw = _q(query, "user_id")
                uid = int(uid_raw) if uid_raw.isdigit() else None
                await self._send_json(writer, 200, await self.store.rag(limit=_qint(query, "limit", 150), q=_q(query, "q"), user_id=uid, npc=_q(query, "npc"))); return
            if path == "/api/decisions":
                await self._send_json(writer, 200, await self.store.decisions(limit=_qint(query, "limit", 150))); return
            if path == "/api/admin":
                await self._send_json(writer, 200, await self.admin.snapshot()); return
            if path == "/api/health":
                await self._send_json(writer, 200, {"ok": True, "schema_version": await self.store.schema_version(), "database": str(self.settings.database_path), "admin_writes": self.settings.admin_writes}); return
            await self._send_json(writer, 404, {"error": "not_found"})
        except (asyncio.TimeoutError, ConnectionError):
            pass
        except Exception as exc:
            log.exception("Dashboard request failed")
            try:
                await self._send_json(writer, 500, {"error": "internal_error", "message": str(exc)[:300]})
            except Exception:
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _send_file(self, writer: asyncio.StreamWriter, path: Path, content_type: str) -> None:
        if not path.exists() or not path.is_file():
            await self._send_json(writer, 404, {"error": "not_found"}); return
        body = path.read_bytes()
        await self._send_bytes(writer, 200, body, content_type)

    async def _send_json(self, writer: asyncio.StreamWriter, status: int, payload: Any, *, extra_headers: dict[str, str] | None = None) -> None:
        body = (json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str) + "\n").encode("utf-8")
        await self._send_bytes(writer, status, body, "application/json; charset=utf-8", extra_headers=extra_headers)

    async def _send_text(self, writer: asyncio.StreamWriter, status: int, text: str, content_type: str, *, extra_headers: dict[str, str] | None = None) -> None:
        await self._send_bytes(writer, status, text.encode("utf-8"), content_type, extra_headers=extra_headers)

    @staticmethod
    async def _send_bytes(writer: asyncio.StreamWriter, status: int, body: bytes, content_type: str, *, extra_headers: dict[str, str] | None = None) -> None:
        reason = {200:"OK",400:"Bad Request",401:"Unauthorized",403:"Forbidden",404:"Not Found",405:"Method Not Allowed",500:"Internal Server Error"}.get(status,"OK")
        headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
            "Cache-Control": "no-store",
            "Connection": "close",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        }
        if extra_headers:
            headers.update(extra_headers)
        raw = [f"HTTP/1.1 {status} {reason}\r\n"] + [f"{k}: {v}\r\n" for k,v in headers.items()] + ["\r\n"]
        writer.write("".join(raw).encode("latin-1") + body)
        await writer.drain()


def _q(query: dict[str, list[str]], key: str, default: str = "") -> str:
    values = query.get(key)
    return str(values[0]) if values else default


def _qint(query: dict[str, list[str]], key: str, default: int) -> int:
    raw = _q(query, key)
    try:
        return int(raw) if raw else int(default)
    except ValueError:
        return int(default)


async def _main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = DashboardSettings.from_env()
    server = DashboardServer(settings)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(_main())
