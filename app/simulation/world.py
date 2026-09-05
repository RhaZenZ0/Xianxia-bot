from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import aiosqlite

from ..database import Database
from ..rules.black_market import BLACK_MARKET_ROTATION_MINUTES

MINUTES_PER_DAY = 24 * 60

SYSTEM_INTERVALS = {
    "npc_civilization": 1 * MINUTES_PER_DAY,
    "npc_life": 7 * MINUTES_PER_DAY,
    "dynamic_economy": 1 * MINUTES_PER_DAY,
    "black_markets": BLACK_MARKET_ROTATION_MINUTES,
    "sect_politics": 7 * MINUTES_PER_DAY,
    "clan_dynamics": 30 * MINUTES_PER_DAY,
    "autonomous_world_events": 1 * MINUTES_PER_DAY,
}

def _market_tradeable(item: dict[str, Any]) -> bool:
    """Return whether an item belongs in ordinary regional markets.

    Secret-realm keys and auction-interest special/legendary items are progression
    rewards, auction lots, or event objects rather than infinite shop stock. An
    explicit ``market_excluded`` content flag can also remove future items.
    """
    if bool(item.get("market_excluded", False)):
        return False
    if item.get("spatial_key"):
        return False
    if str(item.get("auction_interest") or "").lower() in {"special", "legendary"}:
        return False
    return True

@dataclass
class SimulationRun:
    system: str
    due_steps: int
    applied_steps: int
    summary: str
    events: tuple[dict[str, Any], ...] = ()


class WorldSimulator:
    """Persistent, bounded world simulation driven by canonical world time.

    The engine stores its own last-simulated anchors so bot restarts are safe.
    Large time gaps are summarized in bounded aggregate updates rather than
    replayed as millions of tiny ticks.
    """

    def __init__(self, db: Database, world_data: dict[str, Any], *, engine: Any):
        self.db = db
        self.engine = engine
        self.world = world_data
        self.realms = list(world_data.get("realms", []))
        self.locations = dict(world_data.get("locations", {}))
        self.npcs = dict(world_data.get("npcs", {}))
        self.sects = dict(world_data.get("sects", {}))
        self.items = dict(world_data.get("items", {}))

    def market_allows_item(self, item_id: str) -> bool:
        item = self.items.get(str(item_id))
        return bool(item and _market_tradeable(item))

    async def initialize(self, game_minute: int) -> None:
        """Bootstrap simulation state through the authoritative Go engine."""
        if self.engine is None:
            raise RuntimeError("WorldSimulator requires the Go engine for simulation mutations")
        await self.engine.bootstrap_simulation(int(game_minute))
        if not await self.db.list_active_black_markets(int(game_minute)):
            await self.engine.force_simulation("black_markets", 1, int(game_minute))



    async def run_due(self, game_minute: int, automation: dict[str, bool]) -> list[SimulationRun]:
        """Delegate scheduled simulation exclusively to the authoritative Go engine."""
        if self.engine is None:
            raise RuntimeError("WorldSimulator requires the Go engine for simulation mutations")
        rows = await self.engine.run_due_simulation(int(game_minute), automation)
        return [
            SimulationRun(
                str(row.get("system", "")),
                int(row.get("due_steps", 0)),
                int(row.get("applied_steps", 0)),
                str(row.get("summary", "")),
                tuple(dict(event) for event in list(row.get("events") or [])),
            )
            for row in rows
        ]

    async def force_run(self, system: str, steps: int, game_minute: int) -> SimulationRun:
        """Delegate forced simulation exclusively to the authoritative Go engine."""
        if self.engine is None:
            raise RuntimeError("WorldSimulator requires the Go engine for simulation mutations")
        applied = max(1, min(120, int(steps)))
        if system == "all":
            summaries: list[str] = []
            for key in SYSTEM_INTERVALS:
                row = await self.engine.force_simulation(key, applied, int(game_minute))
                summaries.append(f"{key}: {row.get('summary', '')}")
            return SimulationRun("all", applied, applied, " | ".join(summaries))
        row = await self.engine.force_simulation(str(system), applied, int(game_minute))
        return SimulationRun(
            str(row.get("system", system)),
            int(row.get("due_steps", steps)),
            int(row.get("applied_steps", steps)),
            str(row.get("summary", "")),
        )

    async def get_system_state(self, system: str) -> dict[str, Any] | None:
        async with self.db._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM world_simulation_state WHERE system=?", (system,))
            row = await cur.fetchone()
            return dict(row) if row else None

    async def set_interval_days(self, system: str, days: int) -> dict[str, Any]:
        """Delegate simulation interval mutation to the authoritative Go engine."""
        if self.engine is None:
            raise RuntimeError("WorldSimulator requires the Go engine for simulation mutations")
        if system not in SYSTEM_INTERVALS:
            raise ValueError("Unknown simulation system")
        bounded_days = max(1, min(365, int(days)))
        result = await self.engine.action(
            "admin.simulation.interval",
            0,
            {"system": str(system), "days": bounded_days, "reason": "discord admin"},
        )
        return dict(result or {})


    async def recent_player_actions(self, limit: int = 20) -> list[dict[str, Any]]:
        async with self.db._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM world_action_events ORDER BY action_id DESC LIMIT ?",
                (max(1, min(100, int(limit))),),
            )
            rows = []
            for r in await cur.fetchall():
                d = dict(r)
                try:
                    d["payload"] = json.loads(d.pop("payload_json") or "{}")
                except Exception:
                    d["payload"] = {}
                rows.append(d)
            return rows


    async def combat_targets(self, location: str) -> list[dict[str, Any]]:
        """Return living NPC/family-head targets mechanically present in a location.

        Real hidden masters are omitted from casual challenge discovery so this
        helper cannot become a hidden-power detector. They can still enter battles
        through explicit events or GM actions.
        """
        loc = str(location)
        hidden_real = {
            name for name, data in self.npcs.items()
            if isinstance(data.get("hidden_master"), dict) and data.get("hidden_master", {}).get("kind") == "real"
        }
        rows: list[dict[str, Any]] = []
        async with self.db._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """SELECT npc_name AS name,realm_index,phase,'npc' AS target_type
                   FROM npc_civilization_state WHERE current_location=? AND status='alive'
                   ORDER BY influence DESC""",
                (loc,),
            )
            for r in await cur.fetchall():
                d = dict(r)
                if str(d["name"]) not in hidden_real:
                    rows.append(d)
            cur = await db.execute(
                """SELECT head_name AS name,head_realm_index AS realm_index,head_phase AS phase,'family_head' AS target_type,
                          family_id,family_name
                   FROM birth_families WHERE location=? AND line_status='active' AND head_name!='Vacant Ancestral Seat'
                   ORDER BY influence DESC LIMIT 25""",
                (loc,),
            )
            rows.extend(dict(r) for r in await cur.fetchall())
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for row in rows:
            name = str(row.get("name") or "")
            if not name or name in seen:
                continue
            seen.add(name); unique.append(row)
        return unique[:50]

    async def combat_target(self, location: str, target_name: str) -> dict[str, Any] | None:
        for row in await self.combat_targets(location):
            if str(row.get("name", "")).casefold() == str(target_name).casefold():
                return row
        return None


    async def civilization_status(self, location: str) -> dict[str, Any] | None:
        async with self.db._connect() as db:
            db.row_factory=aiosqlite.Row
            cur=await db.execute("SELECT * FROM civilization_regions WHERE location=?",(location,)); row=await cur.fetchone()
            if not row: return None
            out=dict(row)
            cur=await db.execute("SELECT event_text,severity,game_minute FROM civilization_events WHERE location=? ORDER BY event_id DESC LIMIT 5",(location,)); out["events"]=[dict(r) for r in await cur.fetchall()]
            cur=await db.execute("""SELECT c.npc_name,c.profession,c.activity,c.realm_index,c.phase,c.faction,c.influence, l.health,l.injury,l.injury_severity,l.sect_rank,l.relationship_status,l.spouse_name,l.children_count FROM npc_civilization_state c LEFT JOIN npc_life_state l ON l.npc_name=c.npc_name WHERE c.current_location=? AND c.status='alive' ORDER BY c.influence DESC LIMIT 8""",(location,)); out["npcs"]=[dict(r) for r in await cur.fetchall()]
            return out

    async def npc_status(self, npc_name: str) -> dict[str, Any] | None:
        async with self.db._connect() as db:
            db.row_factory=aiosqlite.Row
            cur=await db.execute(
                """SELECT c.*,m.current_goal,m.mood,m.focus_target,m.recent_event,m.goal_progress,
                          l.birth_game_minute,l.age_at_creation_years,l.natural_lifespan_years,l.health,l.injury,l.injury_severity,
                          l.sect_rank,l.career_progress,l.relationship_status,l.spouse_name,l.children_count,l.last_social_game_minute,l.last_cultivation_game_minute,l.death_game_minute,l.cause_of_death
                   FROM npc_civilization_state c
                   LEFT JOIN npc_mind_state m ON m.npc_name=c.npc_name
                   LEFT JOIN npc_life_state l ON l.npc_name=c.npc_name
                   WHERE c.npc_name=?""",
                (npc_name,),
            )
            row=await cur.fetchone()
            if not row: return None
            out=dict(row)
            cur=await db.execute(
                """SELECT * FROM npc_social_relations WHERE status='active' AND (npc_a=? OR npc_b=?)
                   ORDER BY MAX(ABS(affinity),grudge,trust) DESC LIMIT 12""", (npc_name,npc_name)
            ); out["relationships"]=[dict(r) for r in await cur.fetchall()]
            cur=await db.execute(
                """SELECT * FROM npc_disciple_bonds WHERE status='active' AND (master_name=? OR disciple_name=?)
                   ORDER BY started_game_minute DESC LIMIT 12""", (npc_name,npc_name)
            ); out["discipleship"]=[dict(r) for r in await cur.fetchall()]
            cur=await db.execute(
                """SELECT * FROM npc_descendants WHERE status='alive' AND (parent_a=? OR parent_b=?)
                   ORDER BY birth_game_minute DESC LIMIT 20""", (npc_name,npc_name)
            ); out["descendants"]=[dict(r) for r in await cur.fetchall()]
            out["age_years"] = None
            out["lifespan_years"] = None
            if self.engine is not None and out.get("birth_game_minute") is not None:
                life = await self.engine.action("npc.lifespan", 0, {"npc_name": npc_name})
                if isinstance(life, dict):
                    out["age_years"] = life.get("age_years")
                    out["lifespan_years"] = life.get("total_years")
                    out["ageless"] = bool(life.get("ageless", False))
                    out["remaining_years"] = life.get("remaining_years")
            return out

    async def sect_status(self, sect_name: str) -> dict[str, Any] | None:
        async with self.db._connect() as db:
            db.row_factory=aiosqlite.Row
            cur=await db.execute("SELECT * FROM sect_politics_state WHERE sect_name=?",(sect_name,)); row=await cur.fetchone()
            if not row:return None
            out=dict(row)
            cur=await db.execute("SELECT faction_name,agenda,power,loyalty FROM sect_factions WHERE sect_name=? ORDER BY power DESC",(sect_name,)); out["factions"]=[dict(r) for r in await cur.fetchall()]
            cur=await db.execute("SELECT * FROM sect_relations WHERE sect_a=? OR sect_b=? ORDER BY ABS(relation_score) DESC",(sect_name,sect_name)); rels=[]
            for r in await cur.fetchall():
                d=dict(r); d["other"]=d["sect_b"] if d["sect_a"]==sect_name else d["sect_a"]; rels.append(d)
            out["relations"]=rels
            cur=await db.execute("SELECT event_text,severity,game_minute FROM sect_politics_events WHERE sect_name=? ORDER BY event_id DESC LIMIT 5",(sect_name,)); out["events"]=[dict(r) for r in await cur.fetchall()]
            return out

    async def market_rows(self, location: str, limit: int = 25) -> list[dict[str, Any]]:
        async with self.db._connect() as db:
            db.row_factory=aiosqlite.Row
            cur=await db.execute("SELECT * FROM economy_markets WHERE location=? ORDER BY (base_price*price_index) DESC LIMIT ?",(location,max(1,min(100,int(limit))))); return [dict(r) for r in await cur.fetchall()]

    async def market_quote(self, location: str, item_id: str) -> dict[str, Any] | None:
        async with self.db._connect() as db:
            db.row_factory=aiosqlite.Row
            cur=await db.execute("SELECT * FROM economy_markets WHERE location=? AND item_id=?",(location,item_id)); row=await cur.fetchone()
            if not row:return None
            out=dict(row); out["buy_price"]=max(1,int(round(out["base_price"]*out["price_index"]))); out["sell_price"]=max(1,int(round(out["buy_price"]*0.70))); return out


    async def clan_status(self, family_id: int) -> dict[str, Any]:
        async with self.db._connect() as db:
            db.row_factory=aiosqlite.Row
            cur=await db.execute("SELECT * FROM martial_clan_branches WHERE family_id=? ORDER BY CASE branch_type WHEN 'main' THEN 0 ELSE 1 END, martial_strength DESC",(int(family_id),)); branches=[dict(r) for r in await cur.fetchall()]
            cur=await db.execute("SELECT * FROM martial_clan_retainers WHERE family_id=? ORDER BY status,loyalty DESC",(int(family_id),)); retainers=[dict(r) for r in await cur.fetchall()]
            cur=await db.execute("SELECT * FROM martial_clan_relations WHERE family_id=? AND active=1 ORDER BY ABS(relation_score) DESC",(int(family_id),)); relations=[dict(r) for r in await cur.fetchall()]
            return {"branches":branches,"retainers":retainers,"relations":relations}

    async def simulation_status(self, game_minute: int) -> list[dict[str, Any]]:
        async with self.db._connect() as db:
            db.row_factory=aiosqlite.Row
            cur=await db.execute("SELECT * FROM world_simulation_state ORDER BY system"); rows=[]
            for r in await cur.fetchall():
                d=dict(r); d["lag_game_minutes"]=max(0,int(game_minute)-int(d["last_game_minute"])); rows.append(d)
            return rows
