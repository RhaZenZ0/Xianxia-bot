from __future__ import annotations

import json
import math
import secrets
import time
from dataclasses import dataclass
from typing import Any

import aiosqlite

from ..database import Database
from ..forbidden_arts import effective_exposure, reaction_policy
from ..black_market import BLACK_MARKET_ROTATION_MINUTES, BLACK_MARKET_OPEN_MINUTES, black_market_price, candidate_black_market_items
from ..alchemy import forage_bonus_from_resources
from ..worldtime import MINUTES_PER_YEAR
from ..npc_life import (generated_child_name, initial_age_years, initial_rank, injury_for_damage, npc_age_years, npc_lifespan_years, rank_for_power, rank_index, relation_type)

MINUTES_PER_DAY = 24 * 60

SYSTEM_INTERVALS = {
    "npc_civilization": 1 * MINUTES_PER_DAY,
    "npc_life": 7 * MINUTES_PER_DAY,
    "dynamic_economy": 1 * MINUTES_PER_DAY,
    "black_markets": BLACK_MARKET_ROTATION_MINUTES,
    "sect_politics": 7 * MINUTES_PER_DAY,
    "clan_dynamics": 30 * MINUTES_PER_DAY,
}

WORLD_CURRENCY = {
    "Mortal World": "low_spirit_stone",
    "Spiritual World": "low_spirit_crystal",
    "Immortal World": "low_immortal_stone",
    "Celestial World": "low_celestial_crystal",
}

CIVILIZATION_EVENTS = [
    ("A caravan season increased trade through the region.", 3, 1, 2, 1),
    ("Local guards suppressed a bandit route and restored confidence.", 1, 4, 0, -1),
    ("A spirit-beast migration disrupted roads and outlying farms.", -2, -4, -2, 2),
    ("A medicinal-herb harvest drew cultivators and merchants.", 3, 0, 4, 0),
    ("A dispute between martial households unsettled the district.", -1, -3, 0, 4),
    ("A favorable season improved food stores and household births.", 2, 1, 0, -1),
    ("A minor spiritual disturbance damaged fields but enriched nearby spirit veins.", -1, -2, 5, 1),
]

SECT_EVENT_TEXT = [
    "A faction dispute over resource allocation reached the elders.",
    "A talented junior won support from several senior disciples.",
    "A failed expedition increased pressure on the sect treasury.",
    "A successful mission improved the sect's prestige among neighboring powers.",
    "An elder faction pushed for stricter recruitment standards.",
    "Merit-based promotions weakened the influence of an old bloodline faction.",
]

ECONOMY_EVENT_TEXT = [
    "Caravan traffic shifted regional supply.",
    "Cultivator demand changed after recent fighting.",
    "A harvest altered herb and pill availability.",
    "Forging workshops consumed unusual quantities of ore.",
    "Auction speculation distorted short-term demand.",
]

CLAN_RELATION_TYPES = ["alliance", "marriage_pact", "trade_pact", "rivalry", "blood_feud"]
RETAINER_ROLES = ["Estate Guards", "Caravan Guards", "Guest Elders", "Herb Gatherers", "Forge Retainers", "Information Brokers"]
BRANCH_TYPES = ["Main Branch", "First Cadet Branch", "Second Cadet Branch", "Outer Branch", "Distant Branch"]


def _clamp(value: int | float, low: int | float, high: int | float):
    return max(low, min(high, value))




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

def _stable_seed(text: str) -> int:
    return sum((i + 1) * ord(ch) for i, ch in enumerate(text))


def _realm_index_from_text(realm_name: str, realms: list[dict[str, Any]]) -> int:
    name = (realm_name or "").strip().lower()
    aliases = {"mortal": 0, "no detectable cultivation": 0, "suppressed aura": 0}
    if name in aliases:
        return aliases[name]
    for idx, realm in enumerate(realms):
        if str(realm.get("name", "")).lower() == name:
            return idx
    return 0


def _npc_activity_options(profile: dict[str, Any]) -> list[str]:
    role = str(profile.get("role") or "").casefold()
    want = str(profile.get("want") or "").casefold()
    if any(word in role for word in ("merchant", "broker", "auction")):
        return ["Trading", "Gathering information", "Negotiating contracts", "Reviewing market rumors", "Managing affairs"]
    if any(word in role for word in ("healer", "physician", "alchemist")):
        return ["Treating patients", "Gathering remedies", "Researching symptoms", "Cultivating", "Traveling"]
    if any(word in role for word in ("magistrate", "official", "governor")):
        return ["Hearing petitions", "Reviewing security reports", "Managing affairs", "Gathering information", "Traveling"]
    if any(word in role for word in ("elder", "sect", "hall", "master")):
        return ["Cultivating", "Managing disciples", "Reviewing sect affairs", "Gathering information", "Traveling"]
    if any(word in role for word in ("disciple", "rival")) or "become" in want:
        return ["Training", "Cultivating", "Seeking challenges", "Running sect errands", "Traveling"]
    if any(word in role for word in ("guard", "warden", "patrol")):
        return ["Patrolling", "Watching travelers", "Reviewing local threats", "Training", "Traveling"]
    if any(word in role for word in ("beggar", "wander", "hermit")):
        return ["Observing travelers", "Resting", "Wandering", "Gathering information", "Cultivating"]
    return ["Cultivating", "Managing affairs", "Gathering information", "Following established routine", "Traveling"]


def _npc_mood(profile: dict[str, Any], activity: str) -> str:
    personality = str(profile.get("personality") or "").casefold()
    role = str(profile.get("role") or "").casefold()
    if "exhausted" in personality or "weary" in personality:
        base = ["weary", "focused", "concerned"]
    elif any(word in personality for word in ("proud", "competitive", "blunt")):
        base = ["competitive", "restless", "focused"]
    elif any(word in personality for word in ("warm", "compassionate", "patient")):
        base = ["patient", "calm", "concerned"]
    elif any(word in personality for word in ("observant", "cautious", "restrained")):
        base = ["watchful", "guarded", "focused"]
    elif "merchant" in role or "broker" in role:
        base = ["businesslike", "curious", "guarded"]
    else:
        base = ["calm", "focused", "curious", "guarded"]
    if activity in {"Traveling", "Wandering", "Patrolling"}:
        base = base + ["watchful"]
    return secrets.choice(base)


@dataclass
class SimulationRun:
    system: str
    due_steps: int
    applied_steps: int
    summary: str


class WorldSimulator:
    """Persistent, bounded world simulation driven by canonical world time.

    The engine stores its own last-simulated anchors so bot restarts are safe.
    Large time gaps are summarized in bounded aggregate updates rather than
    replayed as millions of tiny ticks.
    """

    def __init__(self, db: Database, world_data: dict[str, Any], *, engine: Any | None = None):
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
        now = time.time()
        async with self.db._connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            for system, interval in SYSTEM_INTERVALS.items():
                await db.execute(
                    """INSERT INTO world_simulation_state(system,last_game_minute,interval_game_minutes,last_run_real,runs)
                       VALUES(?,?,?,?,0) ON CONFLICT(system) DO NOTHING""",
                    (system, int(game_minute), int(interval), now),
                )

            for location, data in self.locations.items():
                world_name = str(data.get("world", "Mortal World"))
                seed = _stable_seed(location)
                world_mult = {"Mortal World": 1, "Spiritual World": 3, "Immortal World": 10, "Celestial World": 30}.get(world_name, 1)
                safe = bool(data.get("safe_zone", False))
                population = (2500 + seed % 35000) * world_mult
                prosperity = int(_clamp(38 + seed % 36 + (8 if safe else 0), 10, 95))
                security = int(_clamp(35 + (seed // 7) % 42 + (12 if safe else 0), 10, 98))
                resources = int(_clamp(30 + (seed // 11) % 55, 5, 100))
                food = int(_clamp(45 + (seed // 13) % 45, 5, 100))
                await db.execute(
                    """INSERT INTO civilization_regions(location,world_name,population,prosperity,security,spirit_resources,food_supply,migration_pressure,unrest,last_game_minute,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(location) DO NOTHING""",
                    (location, world_name, population, prosperity, security, resources, food, 0, max(0, 50-security), int(game_minute), now),
                )

            for npc_name, data in self.npcs.items():
                loc = str(data.get("location", "Greenriver Town"))
                world_name = str(self.locations.get(loc, {}).get("world", "Mortal World"))
                realm_text = str(data.get("realm", "Mortal"))
                hidden = data.get("hidden_master") or {}
                if hidden.get("kind") == "real":
                    realm_index = int(hidden.get("true_realm_index", _realm_index_from_text(realm_text, self.realms)))
                    phase = int(hidden.get("true_stage", data.get("stage", 1) or 1))
                else:
                    realm_index = _realm_index_from_text(realm_text, self.realms)
                    phase = int(data.get("stage", 1) or 1)
                role = str(data.get("role", "Wandering cultivator"))[:160]
                faction = "Independent"
                for sect_name in self.sects:
                    if sect_name.lower() in role.lower():
                        faction = sect_name
                        break
                seed = _stable_seed(npc_name)
                await db.execute(
                    """INSERT INTO npc_civilization_state(npc_name,home_location,current_location,world_name,profession,faction,wealth,influence,ambition,realm_index,phase,status,activity,last_game_minute,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(npc_name) DO NOTHING""",
                    (
                        npc_name, loc, loc, world_name, role, faction,
                        10 + seed % 80, 5 + (seed // 5) % 90, 20 + (seed // 9) % 75,
                        realm_index, phase, "alive", "Following established routine", int(game_minute), now,
                    ),
                )
                goal = str(data.get("want") or f"Continue their work as {role}")[:500]
                await db.execute(
                    """INSERT INTO npc_mind_state(
                           npc_name,current_goal,mood,focus_target,recent_event,goal_progress,last_game_minute,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(npc_name) DO NOTHING""",
                    (npc_name, goal, _npc_mood(data, "Following established routine"), faction if faction != "Independent" else "",
                     "", seed % 21, int(game_minute), now),
                )
                natural_life = 70 + (seed % 11)
                starting_age = initial_age_years(realm_index, phase, natural_life, seed)
                sect_rank = initial_rank(data, faction, realm_index, 5 + (seed // 5) % 90)
                await db.execute(
                    """INSERT INTO npc_life_state(
                           npc_name,birth_game_minute,age_at_creation_years,natural_lifespan_years,health,injury,injury_severity,
                           sect_rank,career_progress,relationship_status,spouse_name,children_count,last_social_game_minute,
                           last_cultivation_game_minute,updated_at
                       ) VALUES(?,?,?,?,100,'',0,?,?, 'single','',0,?,?,?) ON CONFLICT(npc_name) DO NOTHING""",
                    (npc_name, int(game_minute), starting_age, natural_life, sect_rank, seed % 31, int(game_minute), int(game_minute), now),
                )

            for sect_name, data in self.sects.items():
                seed = _stable_seed(sect_name)
                alignment = str(data.get("alignment", "Neutral"))
                specialty = str(data.get("specialty", "General cultivation"))
                await db.execute(
                    """INSERT INTO sect_politics_state(sect_name,alignment,specialty,influence,cohesion,resources,recruitment_pressure,doctrine_pressure,leader_policy,last_game_minute,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(sect_name) DO NOTHING""",
                    (sect_name, alignment, specialty, 45 + seed % 35, 45 + (seed//3)%35, 40 + (seed//7)%40, 50, 50, "Balanced", int(game_minute), now),
                )
                faction_templates = [
                    ("Old Guard", "Preserve lineage, traditions and elder authority"),
                    ("Merit Hall", "Reward contribution, talent and battlefield merit"),
                    ("Expansion Bloc", "Acquire territory, disciples and external influence"),
                ]
                for idx, (name, agenda) in enumerate(faction_templates):
                    await db.execute(
                        """INSERT INTO sect_factions(sect_name,faction_name,agenda,power,loyalty,updated_at)
                           VALUES(?,?,?,?,?,?) ON CONFLICT(sect_name,faction_name) DO NOTHING""",
                        (sect_name, name, agenda, 25 + ((seed + idx*17) % 35), 45 + ((seed + idx*11) % 45), now),
                    )

            sect_names = list(self.sects)
            for i, a in enumerate(sect_names):
                for b in sect_names[i+1:]:
                    same_align = str(self.sects[a].get("alignment")) == str(self.sects[b].get("alignment"))
                    score = 15 if same_align else -5
                    await db.execute(
                        """INSERT INTO sect_relations(sect_a,sect_b,relation_score,relation_type,treaty_status,updated_at)
                           VALUES(?,?,?,?,?,?) ON CONFLICT(sect_a,sect_b) DO NOTHING""",
                        (a, b, score, "neutral", "none", now),
                    )

            for location, loc_data in self.locations.items():
                world_name = str(loc_data.get("world", "Mortal World"))
                world_factor = {"Mortal World": 1.0, "Spiritual World": 1.2, "Immortal World": 1.5, "Celestial World": 2.0}.get(world_name, 1.0)
                for item_id, item in self.items.items():
                    if not _market_tradeable(item):
                        # Existing databases may contain rows created by an older
                        # revision that incorrectly stocked progression items.
                        await db.execute("DELETE FROM economy_markets WHERE location=? AND item_id=?", (location, item_id))
                        continue
                    base = max(1, int(item.get("sect_value", 5)))
                    base_price = max(1, int(round(base * world_factor)))
                    rarity = max(1.0, math.sqrt(base))
                    supply = max(1, int(120 / rarity))
                    demand = max(5, int(40 + min(80, base / 4)))
                    await db.execute(
                        """INSERT INTO economy_markets(location,item_id,world_name,currency_id,base_price,supply,demand,price_index,last_game_minute,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(location,item_id) DO NOTHING""",
                        (location, item_id, world_name, WORLD_CURRENCY.get(world_name, "low_spirit_stone"), base_price, supply, demand, 1.0, int(game_minute), now),
                    )
            await db.commit()
        await self.ensure_all_clans(game_minute)
        # New installs should have an underworld market in every world immediately;
        # subsequent relocations are driven by the persistent simulation interval.
        if not await self.db.list_active_black_markets(int(game_minute)):
            await self._simulate_black_markets(1, int(game_minute))

    async def ensure_all_clans(self, game_minute: int) -> None:
        now = time.time()
        new_relations: list[dict[str, Any]] = []
        async with self.db._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM birth_families WHERE line_status='active'")
            families = [dict(r) for r in await cur.fetchall()]
            await db.execute("BEGIN IMMEDIATE")
            for fam in families:
                family_id = int(fam["family_id"])
                surname = str(fam.get("surname") or "Clan")
                cur2 = await db.execute("SELECT COUNT(*) FROM martial_clan_branches WHERE family_id=?", (family_id,))
                existing = int((await cur2.fetchone())[0])
                desired = max(1, min(12, int(fam.get("branch_count", 1))))
                if existing == 0:
                    await db.execute(
                        """INSERT INTO martial_clan_branches(family_id,branch_name,branch_type,leader_name,members_estimate,martial_strength,wealth_share,loyalty,status,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (family_id, f"{surname} Main Branch", "main", str(fam.get("head_name", "Family Head")), 18 + secrets.randbelow(45), 25 + int(fam.get("tier",1))*10, 55, 85, "active", now),
                    )
                    existing = 1
                for idx in range(existing, desired):
                    label = BRANCH_TYPES[min(idx, len(BRANCH_TYPES)-1)]
                    await db.execute(
                        """INSERT INTO martial_clan_branches(family_id,branch_name,branch_type,leader_name,members_estimate,martial_strength,wealth_share,loyalty,status,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (family_id, f"{surname} {label}", "cadet", f"{surname} Branch Elder", 8 + secrets.randbelow(30), 15 + secrets.randbelow(45), 8 + secrets.randbelow(18), 45 + secrets.randbelow(45), "active", now),
                    )

                cur2 = await db.execute("SELECT COALESCE(SUM(members),0) FROM martial_clan_retainers WHERE family_id=? AND status='active'", (family_id,))
                retained = int((await cur2.fetchone())[0])
                desired_ret = max(0, int(fam.get("retainer_count", 0)))
                if retained < desired_ret:
                    remaining = desired_ret - retained
                    groups = min(4, max(1, math.ceil(remaining / 8)))
                    for _ in range(groups):
                        members = max(1, math.ceil(remaining / groups))
                        role = RETAINER_ROLES[secrets.randbelow(len(RETAINER_ROLES))]
                        await db.execute(
                            """INSERT INTO martial_clan_retainers(family_id,group_name,leader_name,role,members,realm_index,loyalty,upkeep,status,updated_at)
                               VALUES(?,?,?,?,?,?,?,?,?,?)""",
                            (family_id, f"{surname} {role}", f"{surname} Retainer Captain", role, members, max(0, int(fam.get("head_realm_index",0))-1), 50+secrets.randbelow(45), max(1,members//3), "active", now),
                        )
                        remaining -= members
                        if remaining <= 0:
                            break

                cur2 = await db.execute("SELECT COUNT(*) FROM martial_clan_relations WHERE family_id=? AND active=1", (family_id,))
                if int((await cur2.fetchone())[0]) == 0:
                    partner = f"{['Han','Qin','Lu','Wei','Sun','Gu','Ning','Feng'][secrets.randbelow(8)]} Martial Clan"
                    relation = CLAN_RELATION_TYPES[secrets.randbelow(len(CLAN_RELATION_TYPES))]
                    score = {"alliance":45,"marriage_pact":35,"trade_pact":25,"rivalry":-30,"blood_feud":-65}[relation]
                    await db.execute(
                        """INSERT INTO martial_clan_relations(family_id,partner_family_id,partner_name,relation_type,relation_score,active,started_game_minute,updated_at)
                           VALUES(?,?,?,?,?,?,?,?)""",
                        (family_id, None, partner, relation, score, 1, int(game_minute), now),
                    )
                    new_relations.append({
                        "family_id": family_id, "family_name": str(fam.get("family_name") or f"{surname} Clan"),
                        "partner": partner, "relation": relation, "score": score,
                    })
            await db.commit()
        for rel in new_relations:
            relation = str(rel["relation"])
            if relation not in {"alliance", "marriage_pact", "trade_pact", "blood_feud"}:
                continue
            event_type = {
                "alliance": "alliance", "marriage_pact": "marriage",
                "trade_pact": "alliance", "blood_feud": "blood_feud",
            }[relation]
            label = relation.replace("_", " ")
            await self.db.record_world_history_event(
                event_type=event_type,
                title=f"{rel['family_name']} formed a {label} with {rel['partner']}",
                summary=f"{rel['family_name']} and {rel['partner']} entered a lasting {label} recorded by the martial world.",
                significance=68 if relation in {"alliance","marriage_pact"} else 74,
                visibility="public", faction=str(rel['family_name']),
                actor_type="clan", actor_key=str(rel['family_id']), actor_name=str(rel['family_name']),
                target_type="clan", target_key=str(rel['partner']), target_name=str(rel['partner']),
                tags=("clan", relation, "relationship"), game_minute=int(game_minute),
                metadata={"relation_score": int(rel['score'])},
                source_key=f"clan_relation:{int(rel['family_id'])}:{relation}:{rel['partner']}",
            )

    async def _simulate_black_markets(self, steps: int, game_minute: int) -> str:
        item_ids = candidate_black_market_items(self.items)
        if not item_ids:
            return "no eligible underworld goods exist in the content catalog"
        worlds = ("Mortal World", "Spiritual World", "Immortal World", "Celestial World")
        rotated = 0
        for world_name in worlds:
            locations = [
                name for name, data in self.locations.items()
                if str(data.get("world", "Mortal World")) == world_name
                and not data.get("auction_house")
                and "Rebirth" not in name and "Cradle" not in name
            ]
            if not locations:
                continue
            # Hidden posts prefer ordinary/unprotected districts when possible,
            # but higher worlds currently have capital-only seed geography.
            rough = [name for name in locations if not bool(self.locations.get(name, {}).get("safe_zone", False))]
            location = secrets.choice(rough or locations)
            heat = 20 + secrets.randbelow(61)
            currency = WORLD_CURRENCY.get(world_name, "low_spirit_stone")
            chosen = item_ids[:] if len(item_ids) <= 6 else [item_ids.pop(secrets.randbelow(len(item_ids))) for _ in range(6)]
            # candidate list is rebuilt for each world because pop above intentionally
            # avoids duplicates only within one post.
            item_ids = candidate_black_market_items(self.items)
            stock: list[dict[str, Any]] = []
            for item_id in chosen:
                item = self.items.get(item_id, {})
                base = int(item.get("base_price") or max(8, int(item.get("sect_value", 8)) * 8))
                qty = 1 + secrets.randbelow(4)
                stock.append({
                    "item_id": item_id,
                    "currency_id": currency,
                    "unit_price": black_market_price(base, heat=heat, stock=qty),
                    "quantity": qty,
                    "legal_status": str(item.get("legal_status") or "restricted"),
                })
            await self.db.rotate_black_market(
                world_name=world_name,
                location=location,
                heat=heat,
                opens_game_minute=int(game_minute),
                closes_game_minute=int(game_minute) + BLACK_MARKET_OPEN_MINUTES,
                stock=stock,
            )
            rotated += 1
        return f"rotated {rotated} underworld trading post(s) across the four realm worlds"

    async def run_due(self, game_minute: int, automation: dict[str, bool]) -> list[SimulationRun]:
        """Advance due simulation systems without silently discarding backlog.

        Each worker pass processes at most 120 intervals for a subsystem.  If a
        server was offline for longer than that, the simulation anchor advances
        only by the intervals actually processed.  The next worker pass then
        continues catching up.  This keeps each Discord worker iteration bounded
        while preserving canonical world-time history.
        """
        if self.engine is not None:
            rows = await self.engine.run_due_simulation(int(game_minute), automation)
            return [
                SimulationRun(
                    str(row.get("system", "")),
                    int(row.get("due_steps", 0)),
                    int(row.get("applied_steps", 0)),
                    str(row.get("summary", "")),
                )
                for row in rows
            ]
        runs: list[SimulationRun] = []
        for system in SYSTEM_INTERVALS:
            # Backward-compatible with older callers/tests that pass a partial
            # automation mapping: newly added systems must be explicitly enabled
            # by that mapping, while persisted settings include them by default.
            if system in {"black_markets", "npc_life"}:
                if not automation.get(system, False):
                    continue
            elif not automation.get(system, True):
                continue
            state = await self.get_system_state(system)
            if not state:
                continue
            interval = max(1, int(state["interval_game_minutes"]))
            last_game_minute = int(state["last_game_minute"])
            delta = max(0, int(game_minute) - last_game_minute)
            due = delta // interval
            if due <= 0:
                continue
            applied = min(120, int(due))
            processed_game_minute = last_game_minute + applied * interval
            if system == "npc_civilization":
                summary = await self._simulate_civilization(applied, processed_game_minute)
            elif system == "npc_life":
                summary = await self._simulate_npc_lives(applied, processed_game_minute)
            elif system == "dynamic_economy":
                summary = await self._simulate_economy(applied, processed_game_minute)
            elif system == "black_markets":
                summary = await self._simulate_black_markets(applied, processed_game_minute)
            elif system == "sect_politics":
                summary = await self._simulate_sects(applied, processed_game_minute)
            elif system == "clan_dynamics":
                summary = await self._simulate_clans(applied, processed_game_minute)
            else:
                continue
            await self._mark_system_run(system, processed_game_minute, applied)
            if due > applied:
                summary += f"; {due-applied} interval(s) remain queued for catch-up"
            runs.append(SimulationRun(system, int(due), applied, summary))
        return runs

    async def force_run(self, system: str, steps: int, game_minute: int) -> SimulationRun:
        if self.engine is not None:
            if system == "all":
                summaries: list[str] = []
                applied = max(1, min(120, int(steps)))
                for key in SYSTEM_INTERVALS:
                    row = await self.engine.force_simulation(key, applied, int(game_minute))
                    summaries.append(f"{key}: {row.get('summary', '')}")
                return SimulationRun("all", applied, applied, " | ".join(summaries))
            row = await self.engine.force_simulation(str(system), max(1, min(120, int(steps))), int(game_minute))
            return SimulationRun(
                str(row.get("system", system)),
                int(row.get("due_steps", steps)),
                int(row.get("applied_steps", steps)),
                str(row.get("summary", "")),
            )
        if system not in SYSTEM_INTERVALS and system != "all":
            raise ValueError("Unknown simulation system")
        steps = max(1, min(120, int(steps)))
        if system == "all":
            summaries = []
            for key in SYSTEM_INTERVALS:
                run = await self.force_run(key, steps, game_minute)
                summaries.append(f"{key}: {run.summary}")
            return SimulationRun("all", steps, steps, " | ".join(summaries))
        if system == "npc_civilization":
            summary = await self._simulate_civilization(steps, game_minute)
        elif system == "npc_life":
            summary = await self._simulate_npc_lives(steps, game_minute)
        elif system == "dynamic_economy":
            summary = await self._simulate_economy(steps, game_minute)
        elif system == "black_markets":
            summary = await self._simulate_black_markets(steps, game_minute)
        elif system == "sect_politics":
            summary = await self._simulate_sects(steps, game_minute)
        else:
            summary = await self._simulate_clans(steps, game_minute)
        await self._mark_system_run(system, game_minute, steps)
        return SimulationRun(system, steps, steps, summary)

    async def get_system_state(self, system: str) -> dict[str, Any] | None:
        async with self.db._connect() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM world_simulation_state WHERE system=?", (system,))
            row = await cur.fetchone()
            return dict(row) if row else None

    async def set_interval_days(self, system: str, days: int) -> dict[str, Any]:
        if system not in SYSTEM_INTERVALS:
            raise ValueError("Unknown simulation system")
        days = max(1, min(365, int(days)))
        async with self.db._connect() as db:
            await db.execute("UPDATE world_simulation_state SET interval_game_minutes=? WHERE system=?", (days*MINUTES_PER_DAY, system))
            await db.commit()
        return await self.get_system_state(system) or {}

    async def _mark_system_run(self, system: str, game_minute: int, due_steps: int) -> None:
        async with self.db._connect() as db:
            await db.execute(
                "UPDATE world_simulation_state SET last_game_minute=?,last_run_real=?,runs=runs+? WHERE system=?",
                (int(game_minute), time.time(), max(1,int(due_steps)), system),
            )
            await db.commit()

    async def _simulate_civilization(self, steps: int, game_minute: int) -> str:
        now = time.time()
        event_count = 0
        npc_history: list[dict[str, Any]] = []
        async with self.db._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute("SELECT * FROM civilization_regions")
            regions = [dict(r) for r in await cur.fetchall()]
            for r in regions:
                prosperity = int(r["prosperity"])
                security = int(r["security"])
                resources = int(r["spirit_resources"])
                food = int(r["food_supply"])
                unrest = int(r["unrest"])
                pop = int(r["population"])
                # Aggregate daily-scale growth/decline, bounded for long catch-up.
                growth_rate = (food - 45 + security - 45 - unrest) / 2_000_000
                pop = max(50, int(round(pop * (1 + growth_rate * steps))))
                prosperity = int(_clamp(prosperity + secrets.choice([-1,0,0,0,1]) * max(1,steps//15), 0, 100))
                security = int(_clamp(security + secrets.choice([-1,0,0,1]) * max(1,steps//20), 0, 100))
                food = int(_clamp(food + secrets.choice([-2,-1,0,1,2]) * max(1,steps//25), 0, 100))
                resources = int(_clamp(resources + secrets.choice([-1,0,0,1]) * max(1,steps//30), 0, 100))
                migration = int(_clamp((prosperity + security + food - 150)//3, -50, 50))
                unrest = int(_clamp(50 - security + max(0, 35-food)//2, 0, 100))
                if secrets.randbelow(100) < min(60, 4 + steps):
                    text, dp, ds, dr, du = CIVILIZATION_EVENTS[secrets.randbelow(len(CIVILIZATION_EVENTS))]
                    prosperity = int(_clamp(prosperity+dp,0,100)); security=int(_clamp(security+ds,0,100)); resources=int(_clamp(resources+dr,0,100)); unrest=int(_clamp(unrest+du,0,100))
                    await db.execute(
                        "INSERT INTO civilization_events(location,event_text,severity,game_minute,created_at) VALUES(?,?,?,?,?)",
                        (r["location"], text, 1+secrets.randbelow(4), int(game_minute), now),
                    )
                    event_count += 1
                await db.execute(
                    """UPDATE civilization_regions SET population=?,prosperity=?,security=?,spirit_resources=?,food_supply=?,migration_pressure=?,unrest=?,last_game_minute=?,updated_at=? WHERE location=?""",
                    (pop,prosperity,security,resources,food,migration,unrest,int(game_minute),now,r["location"]),
                )

            cur = await db.execute("SELECT * FROM npc_civilization_state WHERE status='alive'")
            npc_rows = [dict(r) for r in await cur.fetchall()]
            same_world_locations: dict[str,list[str]] = {}
            for loc, data in self.locations.items():
                same_world_locations.setdefault(str(data.get("world","Mortal World")),[]).append(loc)
            for npc in npc_rows:
                npc_name = str(npc["npc_name"])
                profile = dict(self.npcs.get(npc_name) or {})
                wealth = int(_clamp(int(npc["wealth"]) + secrets.choice([-2,-1,0,0,1,2]) * max(1,steps//10), 0, 1000))
                influence = int(_clamp(int(npc["influence"]) + secrets.choice([-1,0,0,1]) * max(1,steps//20), 0, 1000))
                realm = int(npc["realm_index"]); phase = int(npc["phase"]); ambition=int(npc["ambition"])
                old_realm, old_phase = realm, phase
                world_name = str(npc.get("world_name") or "Mortal World")
                world_floor = {"Mortal World": 0, "Spiritual World": 8, "Immortal World": 16, "Celestial World": 24}.get(world_name, 0)
                world_cap = min(len(self.realms)-1, world_floor + 7)
                # A deliberately concealed expert may already exceed the local
                # world's normal cap; never demote them, but ordinary autonomous
                # progression cannot cross a world boundary without an ascension event.
                promotion_cap = max(realm, world_cap)
                if realm < promotion_cap and secrets.randbelow(1000) < min(180, ambition * max(1,steps)//8):
                    if phase < 9:
                        phase += 1
                    elif secrets.randbelow(100) < 35 and realm < promotion_cap:
                        realm += 1; phase = 1

                activity = secrets.choice(_npc_activity_options(profile))
                previous_location = str(npc["current_location"])
                current = previous_location
                if activity in {"Traveling", "Wandering"} and secrets.randbelow(100) < min(50, 5+steps):
                    options = same_world_locations.get(str(npc["world_name"]), [current])
                    if options:
                        current = options[secrets.randbelow(len(options))]
                if (realm, phase) != (old_realm, old_phase):
                    realm_name = self.realms[realm].get("name", f"Realm {realm}") if 0 <= realm < len(self.realms) else f"Realm {realm}"
                    npc_history.append({
                        "event_type": "npc_breakthrough",
                        "title": f"{npc_name} advanced to {realm_name} Stage {phase}",
                        "summary": f"Through autonomous cultivation, {npc_name} advanced from realm {old_realm} stage {old_phase} to {realm_name} Stage {phase}.",
                        "significance": 52 if realm == old_realm else 68, "visibility": "public" if int(npc.get("influence") or 0) >= 35 else "participant",
                        "location": current, "world_name": world_name, "faction": str(npc.get("faction") or ""),
                        "actor_type": "npc", "actor_key": npc_name, "actor_name": npc_name, "related_npc_name": npc_name,
                        "tags": ("npc", "cultivation", "breakthrough"), "game_minute": int(game_minute),
                        "source_key": f"npc-civilization:breakthrough:{npc_name}:{realm}:{phase}:{int(game_minute)}",
                    })
                if current != previous_location:
                    npc_history.append({
                        "event_type": "npc_travel",
                        "title": f"{npc_name} traveled to {current}",
                        "summary": f"{npc_name} left {previous_location} and traveled to {current} while engaged in {activity.lower()}.",
                        "significance": 28, "visibility": "public", "location": current, "world_name": world_name, "faction": str(npc.get("faction") or ""),
                        "actor_type": "npc", "actor_key": npc_name, "actor_name": npc_name, "related_npc_name": npc_name,
                        "tags": ("npc", "travel", previous_location, current), "game_minute": int(game_minute),
                        "source_key": f"npc-civilization:travel:{npc_name}:{previous_location}:{current}:{int(game_minute)}",
                    })
                await db.execute(
                    """UPDATE npc_civilization_state SET current_location=?,wealth=?,influence=?,realm_index=?,phase=?,activity=?,last_game_minute=?,updated_at=? WHERE npc_name=?""",
                    (current,wealth,influence,realm,phase,activity,int(game_minute),now,npc_name),
                )
                await db.execute(
                    "UPDATE npc_life_state SET last_cultivation_game_minute=?,updated_at=? WHERE npc_name=?",
                    (int(game_minute), now, npc_name),
                )

                cur_mind = await db.execute("SELECT * FROM npc_mind_state WHERE npc_name=?", (npc_name,))
                mind_row = await cur_mind.fetchone()
                mind = dict(mind_row) if mind_row else {}
                goal = str(mind.get("current_goal") or profile.get("want") or f"Continue their work as {npc.get('profession','a cultivator')}")[:500]
                progress = int(mind.get("goal_progress") or 0)
                # Ambitious NPCs push their plans more aggressively, but this layer
                # never silently completes a named quest, kills a target, or reveals a secret.
                progress += max(1, ambition // 30) * max(1, steps // 4) + secrets.choice([-2, -1, 0, 1, 2, 3])
                recent_event = str(mind.get("recent_event") or "")
                if current != previous_location:
                    recent_event = f"Traveled from {previous_location} to {current} while pursuing ongoing affairs."
                if progress >= 100:
                    progress = 35
                    recent_event = f"Made meaningful preparations toward the long-term goal: {goal}"
                progress = int(_clamp(progress, 0, 100))
                mood = _npc_mood(profile, activity)
                focus_target = str(mind.get("focus_target") or npc.get("faction") or "")[:160]
                await db.execute(
                    """INSERT INTO npc_mind_state(
                           npc_name,current_goal,mood,focus_target,recent_event,goal_progress,last_game_minute,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?)
                       ON CONFLICT(npc_name) DO UPDATE SET
                           current_goal=excluded.current_goal,mood=excluded.mood,focus_target=excluded.focus_target,
                           recent_event=excluded.recent_event,goal_progress=excluded.goal_progress,
                           last_game_minute=excluded.last_game_minute,updated_at=excluded.updated_at""",
                    (npc_name, goal, mood, focus_target, recent_event[:600], progress, int(game_minute), now),
                )
            await db.commit()
        for event in npc_history:
            try:
                await self.db.record_world_history_event(**event)
            except Exception:
                pass
        return f"updated {len(regions)} regions, {len(npc_rows)} named NPCs, {event_count} regional incidents; {len(npc_history)} NPC history events"

    async def _simulate_npc_lives(self, steps: int, game_minute: int) -> str:
        """Advance the persistent personal lives of simulated NPCs.

        This layer is intentionally slower than the daily civilization tick. It
        turns long-running NPC behavior into canonical state: aging, health,
        relationships, grudges, marriages, children, discipleship, rank/faction
        changes, fights and death. Major outcomes are written to world history
        after the state transaction commits so narrator RAG can retrieve them.
        """
        steps = max(1, min(120, int(steps)))
        now = time.time()
        history: list[dict[str, Any]] = []
        stats = {"aged": 0, "social": 0, "fights": 0, "marriages": 0, "children": 0, "disciples": 0, "promotions": 0, "faction_changes": 0, "deaths": 0}

        def add_history(**event: Any) -> None:
            event.setdefault("game_minute", int(game_minute))
            history.append(event)

        async with self.db._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")

            # Existing schema-15 databases already have civilization rows. Ensure
            # every living simulated NPC gets a life row without resetting state.
            cur = await db.execute("SELECT * FROM npc_civilization_state")
            civ_rows = [dict(r) for r in await cur.fetchall()]
            for npc in civ_rows:
                npc_name = str(npc["npc_name"])
                profile = dict(self.npcs.get(npc_name) or {})
                seed = _stable_seed(npc_name)
                await db.execute(
                    """INSERT INTO npc_life_state(
                           npc_name,birth_game_minute,age_at_creation_years,natural_lifespan_years,health,injury,injury_severity,
                           sect_rank,career_progress,relationship_status,spouse_name,children_count,last_social_game_minute,
                           last_cultivation_game_minute,updated_at
                       ) VALUES(?,?,?,?,100,'',0,?,?, 'single','',0,?,?,?) ON CONFLICT(npc_name) DO NOTHING""",
                    (
                        npc_name, int(npc.get("last_game_minute") or game_minute), initial_age_years(int(npc.get("realm_index") or 0), int(npc.get("phase") or 1), 70 + seed % 11, seed), 70 + seed % 11,
                        initial_rank(profile, str(npc.get("faction") or "Independent"), int(npc.get("realm_index") or 0), int(npc.get("influence") or 0)),
                        seed % 31, int(game_minute), int(game_minute), now,
                    ),
                )

            cur = await db.execute(
                """SELECT c.*,l.birth_game_minute,l.age_at_creation_years,l.natural_lifespan_years,l.health,l.injury,
                          l.injury_severity,l.sect_rank,l.career_progress,l.relationship_status,l.spouse_name,l.children_count,
                          l.last_social_game_minute,l.last_cultivation_game_minute,l.death_game_minute,l.cause_of_death
                   FROM npc_civilization_state c JOIN npc_life_state l ON l.npc_name=c.npc_name
                   WHERE c.status='alive'"""
            )
            alive = [dict(r) for r in await cur.fetchall()]
            by_name = {str(r["npc_name"]): r for r in alive}

            # 1) Age, heal, progress careers, promote, and resolve natural death.
            for npc in alive:
                name = str(npc["npc_name"])
                stats["aged"] += 1
                age = npc_age_years(npc, game_minute)
                lifespan = npc_lifespan_years(npc, int(npc["realm_index"]), int(npc["phase"]))
                health = int(npc.get("health") or 100)
                severity = int(npc.get("injury_severity") or 0)
                injury = str(npc.get("injury") or "")
                if severity > 0:
                    heal = max(1, steps * (2 if "healer" in str(npc.get("profession") or "").casefold() else 1))
                    health = min(100, health + heal * 3)
                    severity = max(0, severity - max(1, steps // 2))
                    if severity == 0 or health >= 95:
                        injury = ""
                        severity = 0

                if lifespan is not None and age >= float(lifespan):
                    await db.execute(
                        "UPDATE npc_civilization_state SET status='dead',activity='Deceased',last_game_minute=?,updated_at=? WHERE npc_name=?",
                        (int(game_minute), now, name),
                    )
                    await db.execute(
                        "UPDATE npc_life_state SET health=0,death_game_minute=?,cause_of_death='natural lifespan exhausted',updated_at=? WHERE npc_name=?",
                        (int(game_minute), now, name),
                    )
                    await db.execute(
                        "UPDATE npc_disciple_bonds SET status='ended',ended_game_minute=?,reason='master or disciple died',updated_at=? WHERE status='active' AND (master_name=? OR disciple_name=?)",
                        (int(game_minute), now, name, name),
                    )
                    await db.execute(
                        "UPDATE npc_social_relations SET status='ended',updated_at=? WHERE status='active' AND (npc_a=? OR npc_b=?)",
                        (now, name, name),
                    )
                    await db.execute(
                        "UPDATE npc_life_state SET relationship_status='widowed',updated_at=? WHERE spouse_name=? AND npc_name<>?",
                        (now, name, name),
                    )
                    stats["deaths"] += 1
                    add_history(
                        event_type="death", title=f"{name} reached the end of their lifespan",
                        summary=f"{name} died naturally at roughly {age:.1f} years of age after reaching {self.realms[int(npc['realm_index'])]['name'] if 0 <= int(npc['realm_index']) < len(self.realms) else 'an unknown realm'} Stage {int(npc['phase'])}.",
                        significance=75 if int(npc.get("influence") or 0) >= 50 else 58, visibility="public",
                        location=str(npc.get("current_location") or ""), world_name=str(npc.get("world_name") or ""), faction=str(npc.get("faction") or ""),
                        actor_type="npc", actor_key=name, actor_name=name, related_npc_name=name,
                        tags=("npc", "death", "aging", "lifespan"), metadata={"age_years": round(age, 2), "cause": "natural"},
                        source_key=f"npc-life:death:natural:{name}:{int(game_minute)}",
                    )
                    continue

                career = int(npc.get("career_progress") or 0) + max(1, int(npc.get("ambition") or 50) // 25) * steps + secrets.randbelow(4)
                influence = int(npc.get("influence") or 0)
                wealth = int(npc.get("wealth") or 0)
                milestone = career >= 100
                if milestone:
                    career %= 100
                    influence = int(_clamp(influence + 2 + steps // 8, 0, 1000))
                    wealth = int(_clamp(wealth + 3 + steps // 6, 0, 1000))
                    add_history(
                        event_type="career_milestone", title=f"{name} advanced their worldly work",
                        summary=f"{name} gained influence through sustained work as {npc.get('profession') or 'a cultivator'}.",
                        significance=35, visibility="participant", location=str(npc.get("current_location") or ""), world_name=str(npc.get("world_name") or ""),
                        faction=str(npc.get("faction") or ""), actor_type="npc", actor_key=name, actor_name=name, related_npc_name=name,
                        tags=("npc", "career", "work"), source_key=f"npc-life:career:{name}:{int(game_minute)}",
                    )

                old_rank = str(npc.get("sect_rank") or "Independent Cultivator")
                new_rank = old_rank
                faction = str(npc.get("faction") or "Independent")
                if faction != "Independent":
                    candidate = rank_for_power(int(npc["realm_index"]), influence)
                    if rank_index(candidate) > rank_index(old_rank):
                        new_rank = candidate
                        stats["promotions"] += 1
                        add_history(
                            event_type="rank_promotion", title=f"{name} rose to {new_rank}",
                            summary=f"{name} was recognized as {new_rank} within {faction} after accumulating cultivation and influence.",
                            significance=58 if rank_index(new_rank) >= 4 else 44, visibility="faction", faction=faction,
                            location=str(npc.get("current_location") or ""), world_name=str(npc.get("world_name") or ""), actor_type="npc", actor_key=name, actor_name=name,
                            related_npc_name=name, tags=("npc", "promotion", "sect", new_rank), source_key=f"npc-life:rank:{name}:{new_rank}:{int(game_minute)}",
                        )

                await db.execute(
                    "UPDATE npc_life_state SET health=?,injury=?,injury_severity=?,career_progress=?,sect_rank=?,last_social_game_minute=?,updated_at=? WHERE npc_name=?",
                    (health, injury, severity, career, new_rank, int(game_minute), now, name),
                )
                await db.execute(
                    "UPDATE npc_civilization_state SET wealth=?,influence=?,updated_at=? WHERE npc_name=?",
                    (wealth, influence, now, name),
                )
                npc.update({"health": health, "injury": injury, "injury_severity": severity, "career_progress": career, "sect_rank": new_rank, "influence": influence, "wealth": wealth})

            # Refresh alive list after natural deaths.
            cur = await db.execute(
                """SELECT c.*,l.birth_game_minute,l.age_at_creation_years,l.natural_lifespan_years,l.health,l.injury,
                          l.injury_severity,l.sect_rank,l.career_progress,l.relationship_status,l.spouse_name,l.children_count
                   FROM npc_civilization_state c JOIN npc_life_state l ON l.npc_name=c.npc_name WHERE c.status='alive'"""
            )
            alive = [dict(r) for r in await cur.fetchall()]
            by_name = {str(r["npc_name"]): r for r in alive}
            groups: dict[str, list[dict[str, Any]]] = {}
            for npc in alive:
                groups.setdefault(str(npc.get("current_location") or "Unknown"), []).append(npc)

            # 2) Social encounters, friendships/grudges, fights and mentorship.
            for location, group in groups.items():
                if len(group) < 2:
                    continue
                max_pairs = min(8, max(1, steps), len(group) * (len(group) - 1) // 2)
                seen_pairs: set[tuple[str, str]] = set()
                for _ in range(max_pairs):
                    a = secrets.choice(group); b = secrets.choice(group)
                    if a["npc_name"] == b["npc_name"]:
                        continue
                    pair = tuple(sorted((str(a["npc_name"]), str(b["npc_name"]))))
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)
                    cur = await db.execute("SELECT * FROM npc_social_relations WHERE npc_a=? AND npc_b=?", pair)
                    row = await cur.fetchone()
                    old = dict(row) if row else None
                    affinity = int(old.get("affinity", 0) if old else 0)
                    trust = int(old.get("trust", 0) if old else 0)
                    grudge = int(old.get("grudge", 0) if old else 0)
                    old_type = str(old.get("relation_type") or "acquaintance") if old else "acquaintance"
                    same_faction = str(a.get("faction") or "Independent") == str(b.get("faction") or "Independent") and str(a.get("faction") or "Independent") != "Independent"
                    affinity += secrets.choice([-3, -1, 0, 1, 2, 3]) + (2 if same_faction else 0)
                    trust += secrets.choice([-2, -1, 0, 1, 2]) + (1 if same_faction else 0)
                    competitive = any(word in (str(self.npcs.get(str(x["npc_name"]), {}).get("personality") or "").casefold()) for word in ("proud", "competitive", "predatory") for x in (a,b))
                    grudge += secrets.choice([-2, -1, 0, 0, 1, 2, 3]) + (2 if competitive and not same_faction else 0)
                    affinity = int(_clamp(affinity, -100, 100)); trust = int(_clamp(trust, -100, 100)); grudge = int(_clamp(grudge, 0, 100))
                    married = old_type == "marriage"
                    new_type = relation_type(affinity, trust, grudge, married=married)
                    await db.execute(
                        """INSERT INTO npc_social_relations(npc_a,npc_b,affinity,trust,grudge,relation_type,status,started_game_minute,last_interaction_game_minute,updated_at)
                           VALUES(?,?,?,?,?,?, 'active',?,?,?) ON CONFLICT(npc_a,npc_b) DO UPDATE SET
                           affinity=excluded.affinity,trust=excluded.trust,grudge=excluded.grudge,relation_type=excluded.relation_type,
                           last_interaction_game_minute=excluded.last_interaction_game_minute,updated_at=excluded.updated_at""",
                        (pair[0], pair[1], affinity, trust, grudge, new_type, int(game_minute if old is None else old.get("started_game_minute") or game_minute), int(game_minute), now),
                    )
                    stats["social"] += 1
                    if new_type != old_type and new_type in {"friend", "close_friend", "grudge", "blood_feud"}:
                        etype = "friendship" if "friend" in new_type else ("blood_feud" if new_type == "blood_feud" else "grudge")
                        add_history(
                            event_type=etype, title=f"{pair[0]} and {pair[1]} became {new_type.replace('_',' ')}s" if "friend" in new_type else f"A {new_type.replace('_',' ')} grew between {pair[0]} and {pair[1]}",
                            summary=f"Repeated encounters at {location} changed the relationship between {pair[0]} and {pair[1]} (affinity {affinity:+d}, trust {trust:+d}, grudge {grudge}).",
                            significance=62 if new_type in {"close_friend", "blood_feud"} else 48, visibility="participant", location=location,
                            world_name=str(a.get("world_name") or ""), faction=str(a.get("faction") or "") if same_faction else "",
                            actor_type="npc", actor_key=pair[0], actor_name=pair[0], target_type="npc", target_key=pair[1], target_name=pair[1], related_npc_name=pair[0],
                            tags=("npc", "relationship", new_type), source_key=f"npc-life:relation:{new_type}:{pair[0]}:{pair[1]}:{int(game_minute)}",
                        )

                    # Marriage requires a mature, mutual, low-grudge bond. It is
                    # rare even when eligible, preventing pair churn in small casts.
                    life_a = a; life_b = b
                    if (
                        new_type in {"close_friend", "friend"} and affinity >= 82 and trust >= 70 and grudge <= 15
                        and str(life_a.get("relationship_status") or "single") == "single"
                        and str(life_b.get("relationship_status") or "single") == "single"
                        and npc_age_years(life_a, game_minute) >= 18 and npc_age_years(life_b, game_minute) >= 18
                        and secrets.randbelow(1000) < min(80, 8 * steps)
                    ):
                        await db.execute("UPDATE npc_life_state SET relationship_status='married',spouse_name=?,updated_at=? WHERE npc_name=?", (pair[1], now, pair[0]))
                        await db.execute("UPDATE npc_life_state SET relationship_status='married',spouse_name=?,updated_at=? WHERE npc_name=?", (pair[0], now, pair[1]))
                        await db.execute("UPDATE npc_social_relations SET relation_type='marriage',updated_at=? WHERE npc_a=? AND npc_b=?", (now, pair[0], pair[1]))
                        a["relationship_status"] = b["relationship_status"] = "married"; a["spouse_name"] = pair[1]; b["spouse_name"] = pair[0]
                        stats["marriages"] += 1
                        add_history(
                            event_type="marriage", title=f"{pair[0]} and {pair[1]} married",
                            summary=f"After a sustained bond formed through their shared lives, {pair[0]} and {pair[1]} entered a recognized marriage.",
                            significance=72, visibility="public", location=location, world_name=str(a.get("world_name") or ""),
                            faction=str(a.get("faction") or "") if same_faction else "", actor_type="npc", actor_key=pair[0], actor_name=pair[0],
                            target_type="npc", target_key=pair[1], target_name=pair[1], tags=("npc", "marriage", "family"),
                            source_key=f"npc-life:marriage:{pair[0]}:{pair[1]}:{int(game_minute)}",
                        )
                        new_type = "marriage"

                    # A persistent grudge or challenge can become a real fight.
                    challenge = any(str(x.get("activity") or "") == "Seeking challenges" for x in (a,b))
                    fight_chance = min(220, (grudge * 2 if grudge >= 45 else 0) + (35 + steps * 4 if challenge else (steps * 4 if grudge >= 45 else 0)))
                    if fight_chance > 0 and secrets.randbelow(1000) < fight_chance:
                        power_a = int(a["realm_index"]) * 18 + int(a["phase"]) * 2 + int(a.get("influence") or 0) // 12 + secrets.randbelow(18)
                        power_b = int(b["realm_index"]) * 18 + int(b["phase"]) * 2 + int(b.get("influence") or 0) // 12 + secrets.randbelow(18)
                        winner, loser = (a,b) if power_a >= power_b else (b,a)
                        damage = min(95, 16 + abs(power_a-power_b)//2 + secrets.randbelow(34) + grudge//8)
                        loser_name = str(loser["npc_name"]); winner_name = str(winner["npc_name"])
                        new_health = max(0, int(loser.get("health") or 100) - damage)
                        injury_name, injury_severity = injury_for_damage(damage)
                        lethal = new_health <= 0 or (damage >= 82 and secrets.randbelow(100) < 22)
                        if lethal:
                            await db.execute("UPDATE npc_civilization_state SET status='dead',activity='Killed in combat',last_game_minute=?,updated_at=? WHERE npc_name=?", (int(game_minute), now, loser_name))
                            await db.execute("UPDATE npc_life_state SET health=0,injury=?,injury_severity=10,death_game_minute=?,cause_of_death=?,updated_at=? WHERE npc_name=?", (injury_name, int(game_minute), f"killed by {winner_name}", now, loser_name))
                            await db.execute("UPDATE npc_disciple_bonds SET status='ended',ended_game_minute=?,reason='master or disciple died',updated_at=? WHERE status='active' AND (master_name=? OR disciple_name=?)", (int(game_minute), now, loser_name, loser_name))
                            await db.execute("UPDATE npc_social_relations SET status='ended',updated_at=? WHERE status='active' AND (npc_a=? OR npc_b=?)", (now, loser_name, loser_name))
                            await db.execute("UPDATE npc_life_state SET relationship_status='widowed',updated_at=? WHERE spouse_name=? AND npc_name<>?", (now, loser_name, loser_name))
                            stats["deaths"] += 1
                            add_history(
                                event_type="death", title=f"{loser_name} was killed by {winner_name}",
                                summary=f"A conflict at {location} ended with {winner_name} killing {loser_name}. The death permanently altered their personal and factional relationships.",
                                significance=82, visibility="public", location=location, world_name=str(loser.get("world_name") or ""), faction=str(loser.get("faction") or ""),
                                actor_type="npc", actor_key=winner_name, actor_name=winner_name, target_type="npc", target_key=loser_name, target_name=loser_name,
                                tags=("npc", "death", "combat", "grudge"), metadata={"damage": damage, "winner_power": max(power_a,power_b)},
                                source_key=f"npc-life:death:combat:{winner_name}:{loser_name}:{int(game_minute)}",
                            )
                        else:
                            await db.execute("UPDATE npc_life_state SET health=?,injury=?,injury_severity=?,updated_at=? WHERE npc_name=?", (new_health, injury_name, injury_severity, now, loser_name))
                            loser["health"] = new_health; loser["injury"] = injury_name; loser["injury_severity"] = injury_severity
                            add_history(
                                event_type="major_battle", title=f"{winner_name} defeated {loser_name}",
                                summary=f"{winner_name} defeated {loser_name} in a real clash at {location}; {loser_name} survived with {injury_name}.",
                                significance=58 + min(20, grudge//5), visibility="participant", location=location, world_name=str(a.get("world_name") or ""),
                                faction=str(a.get("faction") or "") if same_faction else "", actor_type="npc", actor_key=winner_name, actor_name=winner_name,
                                target_type="npc", target_key=loser_name, target_name=loser_name, related_npc_name=winner_name,
                                tags=("npc", "battle", "injury", "grudge"), metadata={"damage": damage, "injury": injury_name},
                                source_key=f"npc-life:battle:{winner_name}:{loser_name}:{int(game_minute)}",
                            )
                        stats["fights"] += 1

                    # Higher-realm trusted NPCs may establish a real master/disciple bond.
                    if same_faction and trust >= 55 and affinity >= 45 and grudge <= 20:
                        higher, lower = (a,b) if (int(a["realm_index"]),int(a["phase"])) > (int(b["realm_index"]),int(b["phase"])) else (b,a)
                        gap = int(higher["realm_index"]) - int(lower["realm_index"])
                        if gap >= 2 and rank_index(str(higher.get("sect_rank") or "")) >= 3 and secrets.randbelow(1000) < min(70, 5 * steps):
                            cur2 = await db.execute("SELECT 1 FROM npc_disciple_bonds WHERE disciple_name=? AND status='active' LIMIT 1", (str(lower["npc_name"]),))
                            if not await cur2.fetchone():
                                await db.execute(
                                    "INSERT OR IGNORE INTO npc_disciple_bonds(master_name,disciple_name,status,started_game_minute,updated_at) VALUES(?,?,'active',?,?)",
                                    (str(higher["npc_name"]), str(lower["npc_name"]), int(game_minute), now),
                                )
                                stats["disciples"] += 1
                                add_history(
                                    event_type="discipleship", title=f"{lower['npc_name']} became a disciple of {higher['npc_name']}",
                                    summary=f"Within {higher.get('faction')}, {higher['npc_name']} formally accepted {lower['npc_name']} as a disciple after repeated trusted contact.",
                                    significance=61, visibility="faction", faction=str(higher.get("faction") or ""), location=location, world_name=str(higher.get("world_name") or ""),
                                    actor_type="npc", actor_key=str(higher["npc_name"]), actor_name=str(higher["npc_name"]), target_type="npc", target_key=str(lower["npc_name"]), target_name=str(lower["npc_name"]),
                                    tags=("npc", "master", "disciple", "sect"), source_key=f"npc-life:disciple:{higher['npc_name']}:{lower['npc_name']}:{int(game_minute)}",
                                )

            # 3) Rare faction movement. Independent NPCs can join a co-located
            # sect; deeply embittered members can leave rather than teleporting
            # allegiance arbitrarily between unrelated organizations.
            for npc in alive:
                name = str(npc["npc_name"])
                faction = str(npc.get("faction") or "Independent")
                if faction == "Independent" and secrets.randbelow(1000) < min(45, 3 * steps):
                    local_factions = [str(x.get("faction")) for x in groups.get(str(npc.get("current_location")), []) if str(x.get("faction") or "Independent") != "Independent"]
                    if local_factions:
                        new_faction = secrets.choice(local_factions)
                        await db.execute("UPDATE npc_civilization_state SET faction=?,updated_at=? WHERE npc_name=?", (new_faction, now, name))
                        new_rank = rank_for_power(int(npc["realm_index"]), int(npc.get("influence") or 0))
                        await db.execute("UPDATE npc_life_state SET sect_rank=?,updated_at=? WHERE npc_name=?", (new_rank, now, name))
                        stats["faction_changes"] += 1
                        add_history(
                            event_type="faction_change", title=f"{name} joined {new_faction}",
                            summary=f"After repeated contact with cultivators of {new_faction}, {name} left independent life and entered the faction as {new_rank}.",
                            significance=58, visibility="public", location=str(npc.get("current_location") or ""), world_name=str(npc.get("world_name") or ""), faction=new_faction,
                            actor_type="npc", actor_key=name, actor_name=name, tags=("npc", "faction", "sect", "recruitment"), source_key=f"npc-life:faction:join:{name}:{new_faction}:{int(game_minute)}",
                        )
                elif faction != "Independent" and secrets.randbelow(1000) < min(28, 2 * steps):
                    cur2 = await db.execute(
                        "SELECT npc_a,npc_b,grudge FROM npc_social_relations WHERE status='active' AND grudge>=75 AND (npc_a=? OR npc_b=?)",
                        (name, name),
                    )
                    hostile = []
                    for rel in await cur2.fetchall():
                        other = str(rel[1] if str(rel[0]) == name else rel[0])
                        other_state = by_name.get(other)
                        if other_state and str(other_state.get("faction") or "Independent") == faction:
                            hostile.append(other)
                    if hostile:
                        await db.execute("UPDATE npc_civilization_state SET faction='Independent',updated_at=? WHERE npc_name=?", (now, name))
                        await db.execute("UPDATE npc_life_state SET sect_rank='Independent Cultivator',updated_at=? WHERE npc_name=?", (now, name))
                        stats["faction_changes"] += 1
                        add_history(
                            event_type="faction_change", title=f"{name} left {faction}",
                            summary=f"Long-running internal grudges culminated in {name} severing ties with {faction} and returning to independent cultivation.",
                            significance=66, visibility="public", location=str(npc.get("current_location") or ""), world_name=str(npc.get("world_name") or ""), faction=faction,
                            actor_type="npc", actor_key=name, actor_name=name, target_type="faction", target_key=faction, target_name=faction,
                            tags=("npc", "faction", "defection", "grudge"), source_key=f"npc-life:faction:leave:{name}:{faction}:{int(game_minute)}",
                        )

            # 4) Married NPCs can have persistent descendants. Births are rare
            # and rate-limited to at most one child per world-year per pair.
            cur = await db.execute(
                """SELECT a.*,c.current_location,c.world_name,c.faction,c.realm_index,c.phase
                   FROM npc_life_state a JOIN npc_civilization_state c ON c.npc_name=a.npc_name
                   JOIN npc_civilization_state c2 ON c2.npc_name=a.spouse_name AND c2.status='alive'
                   WHERE c.status='alive' AND a.relationship_status='married' AND a.spouse_name<>'' AND a.npc_name<a.spouse_name"""
            )
            couples = [dict(r) for r in await cur.fetchall()]
            for a in couples:
                partner = str(a["spouse_name"])
                b = by_name.get(partner)
                if not b:
                    continue
                cur2 = await db.execute("SELECT MAX(birth_game_minute) FROM npc_descendants WHERE (parent_a=? AND parent_b=?) OR (parent_a=? AND parent_b=?)", (str(a["npc_name"]), partner, partner, str(a["npc_name"])))
                row = await cur2.fetchone(); last_birth = int(row[0] or -MINUTES_PER_YEAR)
                if int(game_minute) - last_birth < MINUTES_PER_YEAR:
                    continue
                if secrets.randbelow(1000) >= min(90, 6 * steps):
                    continue
                seed = _stable_seed(f"{a['npc_name']}:{partner}:{game_minute}:{a.get('children_count',0)}")
                base_name = generated_child_name(str(a["npc_name"]), partner, seed)
                child_name = base_name
                suffix = 2
                while True:
                    cur2 = await db.execute("SELECT 1 FROM npc_descendants WHERE child_name=? UNION SELECT 1 FROM npc_civilization_state WHERE npc_name=? LIMIT 1", (child_name, child_name))
                    if not await cur2.fetchone():
                        break
                    child_name = f"{base_name} {suffix}"; suffix += 1
                roots = ("Mortal Root", "Wood Root", "Water Root", "Fire Root", "Earth Root", "Metal Root", "Wind Root")
                root = roots[seed % len(roots)]
                gender = ("male", "female", "neutral")[seed % 3]
                await db.execute(
                    """INSERT INTO npc_descendants(child_name,parent_a,parent_b,birth_game_minute,gender,spiritual_root,realm_index,phase,status,generated_as_npc,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,0,1,'alive',0,?,?)""",
                    (child_name, str(a["npc_name"]), partner, int(game_minute), gender, root, now, now),
                )
                await db.execute("UPDATE npc_life_state SET children_count=children_count+1,updated_at=? WHERE npc_name IN (?,?)", (now, str(a["npc_name"]), partner))
                stats["children"] += 1
                add_history(
                    event_type="descendant_birth", title=f"{child_name} was born to {a['npc_name']} and {partner}",
                    summary=f"The household of {a['npc_name']} and {partner} gained a new descendant, {child_name}, born with {root}.",
                    significance=57, visibility="public", location=str(a.get("current_location") or ""), world_name=str(a.get("world_name") or ""), faction=str(a.get("faction") or ""),
                    actor_type="npc", actor_key=str(a["npc_name"]), actor_name=str(a["npc_name"]), target_type="npc", target_key=child_name, target_name=child_name,
                    tags=("npc", "family", "birth", "descendant"), metadata={"parent_b": partner, "spiritual_root": root}, source_key=f"npc-life:birth:{child_name}:{int(game_minute)}",
                )

            # 5) Descendants become active simulated NPCs once they mature. They
            # inherit location/faction context but not hidden power or secrets.
            cur = await db.execute("SELECT * FROM npc_descendants WHERE status='alive' AND generated_as_npc=0")
            descendants = [dict(r) for r in await cur.fetchall()]
            for child in descendants:
                child_age = max(0, int(game_minute) - int(child["birth_game_minute"])) / MINUTES_PER_YEAR
                if child_age < 16:
                    continue
                parent = by_name.get(str(child["parent_a"])) or by_name.get(str(child["parent_b"]))
                if not parent:
                    continue
                name = str(child["child_name"])
                faction = str(parent.get("faction") or "Independent")
                location = str(parent.get("home_location") or parent.get("current_location") or "Greenriver Town")
                world_name = str(parent.get("world_name") or "Mortal World")
                seed = _stable_seed(name)
                await db.execute(
                    """INSERT OR IGNORE INTO npc_civilization_state(npc_name,home_location,current_location,world_name,profession,faction,wealth,influence,ambition,realm_index,phase,status,activity,last_game_minute,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,0,1,'alive','Beginning independent training',?,?)""",
                    (name, location, location, world_name, "Cultivator descendant", faction, 5 + seed % 25, 1 + seed % 12, 35 + seed % 55, int(game_minute), now),
                )
                await db.execute(
                    """INSERT OR IGNORE INTO npc_life_state(npc_name,birth_game_minute,age_at_creation_years,natural_lifespan_years,health,injury,injury_severity,sect_rank,career_progress,relationship_status,spouse_name,children_count,last_social_game_minute,last_cultivation_game_minute,updated_at)
                       VALUES(?, ?,0,75,100,'',0,?,0,'single','',0,?,?,?)""",
                    (name, int(child["birth_game_minute"]), "Outer Disciple" if faction != "Independent" else "Independent Cultivator", int(game_minute), int(game_minute), now),
                )
                await db.execute("UPDATE npc_descendants SET generated_as_npc=1,updated_at=? WHERE descendant_id=?", (now, int(child["descendant_id"])))
                add_history(
                    event_type="coming_of_age", title=f"{name} entered the active cultivation world",
                    summary=f"{name}, descendant of {child['parent_a']} and {child['parent_b']}, reached maturity and began an independent life within {faction}.",
                    significance=43, visibility="public", location=location, world_name=world_name, faction=faction,
                    actor_type="npc", actor_key=name, actor_name=name, tags=("npc", "descendant", "coming_of_age"), source_key=f"npc-life:adult:{name}:{int(game_minute)}",
                )

            await db.commit()

        # Historical writes occur after the life-state transaction commits to
        # avoid nested writer-lock contention in the observed SQLite wrapper.
        for event in history:
            try:
                await self.db.record_world_history_event(**event)
            except Exception:
                # Simulation state remains authoritative even if a single history
                # annotation fails; the next major event is still recorded.
                pass

        return (
            f"advanced {stats['aged']} NPC lives; {stats['social']} social encounters; "
            f"{stats['fights']} fights, {stats['marriages']} marriages, {stats['children']} births, "
            f"{stats['disciples']} discipleships, {stats['promotions']} promotions, "
            f"{stats['faction_changes']} faction changes, {stats['deaths']} deaths"
        )

    async def _simulate_economy(self, steps: int, game_minute: int) -> str:
        now=time.time(); changed=0; events=0
        async with self.db._connect() as db:
            db.row_factory=aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur=await db.execute("SELECT m.*,r.prosperity,r.security,r.spirit_resources,r.unrest FROM economy_markets m LEFT JOIN civilization_regions r ON r.location=m.location")
            rows=[dict(r) for r in await cur.fetchall()]
            for m in rows:
                supply=max(0,int(m["supply"])); demand=max(1,int(m["demand"])); base=int(m["base_price"])
                prosperity=int(m.get("prosperity") or 50); security=int(m.get("security") or 50); spirit=int(m.get("spirit_resources") or 50); unrest=int(m.get("unrest") or 0)
                # Supply slowly regenerates; demand mean-reverts but responds to unrest.
                regen=max(0, int((spirit+security)/80 * max(1,steps//3)))
                supply=min(9999, supply + regen + secrets.choice([-1,0,0,1])*max(1,steps//15))
                target=40 + unrest//2 + (60-prosperity)//3
                demand=int(_clamp(demand + (target-demand)*min(1.0,steps/30) + secrets.choice([-3,-1,0,1,3]), 1, 500))
                ratio=(demand+10)/(supply+10)
                index=float(_clamp(0.55 + math.sqrt(ratio)*0.55 + unrest/250 - prosperity/500, 0.35, 4.0))
                if abs(index-float(m["price_index"])) >= 0.12: changed += 1
                await db.execute("UPDATE economy_markets SET supply=?,demand=?,price_index=?,last_game_minute=?,updated_at=? WHERE location=? AND item_id=?",(supply,demand,index,int(game_minute),now,m["location"],m["item_id"]))
            if rows and secrets.randbelow(100) < min(70, 5+steps):
                row=rows[secrets.randbelow(len(rows))]
                text=ECONOMY_EVENT_TEXT[secrets.randbelow(len(ECONOMY_EVENT_TEXT))]
                await db.execute("INSERT INTO economy_events(location,item_id,event_text,game_minute,created_at) VALUES(?,?,?,?,?)",(row["location"],row["item_id"],text,int(game_minute),now)); events+=1
            await db.commit()
        return f"repriced {len(rows)} market entries; {changed} moved materially; {events} market events"

    async def _simulate_sects(self, steps: int, game_minute: int) -> str:
        now=time.time(); incidents=0
        async with self.db._connect() as db:
            db.row_factory=aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur=await db.execute("SELECT * FROM sect_politics_state")
            sects=[dict(r) for r in await cur.fetchall()]
            for s in sects:
                cur2=await db.execute("SELECT COUNT(*),COALESCE(SUM(influence),0),COALESCE(SUM(contribution_points),0) FROM sect_membership WHERE sect_name=?",(s["sect_name"],))
                members,total_inf,total_cp=await cur2.fetchone()
                cur2=await db.execute("SELECT COALESCE(SUM(quantity),0) FROM sect_treasury WHERE sect_name=?",(s["sect_name"],)); stock=int((await cur2.fetchone())[0])
                resources=int(_clamp(int(s["resources"]) + stock//20 + int(total_cp)//80 + secrets.choice([-3,-1,0,1,3])*max(1,steps//3),0,1000))
                influence=int(_clamp(int(s["influence"]) + int(total_inf)//100 + secrets.choice([-2,-1,0,1,2])*max(1,steps//4),0,1000))
                cohesion=int(_clamp(int(s["cohesion"]) + (2 if resources>60 else -2 if resources<20 else 0) + secrets.choice([-2,-1,0,1,2])*max(1,steps//5),0,100))
                recruit=int(_clamp(45 + max(0,60-cohesion)//2 + (10 if influence>80 else 0),0,100))
                doctrine=int(_clamp(int(s["doctrine_pressure"]) + secrets.choice([-3,-1,0,1,3])*max(1,steps//5),0,100))
                await db.execute("UPDATE sect_politics_state SET influence=?,cohesion=?,resources=?,recruitment_pressure=?,doctrine_pressure=?,last_game_minute=?,updated_at=? WHERE sect_name=?",(influence,cohesion,resources,recruit,doctrine,int(game_minute),now,s["sect_name"]))
                cur2=await db.execute("SELECT * FROM sect_factions WHERE sect_name=?",(s["sect_name"],)); factions=[dict(r) for r in await cur2.fetchall()]
                powers=[]
                for f in factions:
                    power=int(_clamp(int(f["power"])+secrets.choice([-4,-2,-1,0,1,2,4])*max(1,steps//3),5,90)); powers.append((f,power))
                total=sum(p for _,p in powers) or 1
                for f,power in powers:
                    norm=max(5,int(round(power*100/total)))
                    loyalty=int(_clamp(int(f["loyalty"])+(cohesion-50)//20+secrets.choice([-2,-1,0,1,2]),0,100))
                    await db.execute("UPDATE sect_factions SET power=?,loyalty=?,updated_at=? WHERE faction_id=?",(norm,loyalty,now,int(f["faction_id"])))
                if secrets.randbelow(100)<min(65,8+steps*3):
                    text=SECT_EVENT_TEXT[secrets.randbelow(len(SECT_EVENT_TEXT))]
                    await db.execute("INSERT INTO sect_politics_events(sect_name,event_text,severity,game_minute,created_at) VALUES(?,?,?,?,?)",(s["sect_name"],text,1+secrets.randbelow(4),int(game_minute),now)); incidents+=1
            cur=await db.execute("SELECT * FROM sect_relations"); relations=[dict(r) for r in await cur.fetchall()]
            for rel in relations:
                score=int(_clamp(int(rel["relation_score"])+secrets.choice([-3,-1,0,0,1,3])*max(1,steps//3),-100,100))
                rtype="allied" if score>=55 else "friendly" if score>=20 else "neutral" if score>-20 else "rival" if score>-60 else "hostile"
                treaty="mutual_aid" if score>=70 else "non_aggression" if score>=35 else "none"
                await db.execute("UPDATE sect_relations SET relation_score=?,relation_type=?,treaty_status=?,updated_at=? WHERE sect_a=? AND sect_b=?",(score,rtype,treaty,now,rel["sect_a"],rel["sect_b"]))
            await db.commit()
        return f"updated {len(sects)} sects, {len(relations)} inter-sect relations, {incidents} political incidents"

    async def _simulate_clans(self, steps: int, game_minute: int) -> str:
        await self.ensure_all_clans(game_minute)
        now=time.time(); family_events=0
        async with self.db._connect() as db:
            db.row_factory=aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur=await db.execute("SELECT * FROM birth_families WHERE line_status='active'"); families=[dict(r) for r in await cur.fetchall()]
            for fam in families:
                local_family_events = 0
                fid=int(fam["family_id"]); stability=int(fam["stability"]); influence=int(fam["influence"]); wealth=int(fam["wealth"]); purity=int(fam.get("bloodline_purity",0))
                cur2=await db.execute("SELECT * FROM martial_clan_branches WHERE family_id=? AND status='active'",(fid,)); branches=[dict(r) for r in await cur2.fetchall()]
                for b in branches:
                    loyalty=int(_clamp(int(b["loyalty"])+(stability-50)//15+secrets.choice([-4,-2,-1,0,1,2,4])*max(1,steps//4),0,100))
                    strength=int(_clamp(int(b["martial_strength"])+secrets.choice([-2,-1,0,1,2])*max(1,steps//5),0,500))
                    status="active"
                    if str(b["branch_type"])!="main" and loyalty<10 and secrets.randbelow(100)<min(55,5+steps): status="seceded"; stability=max(0,stability-6); influence=max(0,influence-3); family_events+=1; local_family_events+=1
                    await db.execute("UPDATE martial_clan_branches SET loyalty=?,martial_strength=?,status=?,updated_at=? WHERE branch_id=?",(loyalty,strength,status,now,int(b["branch_id"])))
                cur2=await db.execute("SELECT * FROM martial_clan_retainers WHERE family_id=? AND status='active'",(fid,)); groups=[dict(r) for r in await cur2.fetchall()]
                for g in groups:
                    loyalty=int(_clamp(int(g["loyalty"])+(wealth-40)//20+(stability-50)//20+secrets.choice([-3,-1,0,1,3])*max(1,steps//4),0,100))
                    members=max(0,int(g["members"])+secrets.choice([-2,-1,0,0,1,2])*max(1,steps//5))
                    status="active" if members>0 and loyalty>5 else "departed"
                    await db.execute("UPDATE martial_clan_retainers SET members=?,loyalty=?,status=?,updated_at=? WHERE retainer_id=?",(members,loyalty,status,now,int(g["retainer_id"])))
                cur2=await db.execute("SELECT * FROM martial_clan_relations WHERE family_id=? AND active=1",(fid,)); relations=[dict(r) for r in await cur2.fetchall()]
                relation_bonus=0
                for rel in relations:
                    score=int(_clamp(int(rel["relation_score"])+secrets.choice([-3,-1,0,0,1,3])*max(1,steps//4),-100,100))
                    rtype=str(rel["relation_type"])
                    if rtype in {"alliance","marriage_pact","trade_pact"}: relation_bonus+=1 if score>0 else -1
                    else: relation_bonus-=1 if score<0 else 0
                    await db.execute("UPDATE martial_clan_relations SET relation_score=?,updated_at=? WHERE relation_id=?",(score,now,int(rel["relation_id"])))
                if purity>0:
                    purity=int(_clamp(purity + secrets.choice([-1,0,0,0,1])*max(1,steps//8),1,100))
                wealth=int(_clamp(wealth+relation_bonus+secrets.choice([-2,-1,0,1,2])*max(1,steps//6),0,100))
                influence=int(_clamp(influence+relation_bonus+secrets.choice([-1,0,0,1])*max(1,steps//8),0,100))
                stability=int(_clamp(stability+relation_bonus+secrets.choice([-2,-1,0,1,2])*max(1,steps//7),0,100))
                cur2=await db.execute("SELECT COUNT(*) FROM martial_clan_branches WHERE family_id=? AND status='active'",(fid,)); branch_count=max(1,int((await cur2.fetchone())[0]))
                cur2=await db.execute("SELECT COALESCE(SUM(members),0) FROM martial_clan_retainers WHERE family_id=? AND status='active'",(fid,)); retainer_count=max(0,int((await cur2.fetchone())[0]))
                if influence>70 and stability>65 and branch_count<8 and secrets.randbelow(100)<min(35,3+steps):
                    surname=str(fam.get("surname","Clan")); await db.execute("INSERT INTO martial_clan_branches(family_id,branch_name,branch_type,leader_name,members_estimate,martial_strength,wealth_share,loyalty,status,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(fid,f"{surname} New Cadet Branch","cadet",f"{surname} Branch Elder",10+secrets.randbelow(25),20+secrets.randbelow(40),10,55+secrets.randbelow(30),"active",now)); branch_count+=1; family_events+=1; local_family_events+=1
                history=[]
                try: history=json.loads(fam.get("history_json") or "[]")
                except Exception: history=[]
                if local_family_events and secrets.randbelow(100)<25:
                    history.append("The clan's branches, retainers and alliances shifted as the wider martial world changed.")
                await db.execute("UPDATE birth_families SET wealth=?,influence=?,stability=?,bloodline_purity=?,branch_count=?,retainer_count=?,history_json=?,updated_at=? WHERE family_id=?",(wealth,influence,stability,purity,branch_count,retainer_count,json.dumps(history[-80:]),now,fid))
            await db.commit()
        return f"updated {len(families)} martial families/clans; {family_events} structural changes"

    async def apply_player_action(
        self,
        *,
        user_id: int,
        action_type: str,
        target_name: str,
        location: str,
        game_minute: int,
        severity: int = 1,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Apply persistent world consequences from a canonical player action.

        This is deliberately deterministic in *shape* even though the long-running
        simulation remains stochastic. A killed clan head always hurts that clan;
        an influential NPC death always destabilizes their region/faction. Future
        quests and event systems can call the same hook for rescues, donations,
        sabotage, robberies, diplomacy, and similar actions.
        """
        action = str(action_type).strip().lower()
        target = str(target_name).strip()
        loc = str(location).strip() or "Unknown"
        sev = int(_clamp(int(severity), 1, 10))
        details = dict(payload or {})
        impacts: list[str] = []
        leadership_change: dict[str, Any] | None = None
        blood_feud_name = ""
        npc_faction = ""
        now = time.time()

        async with self.db._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")

            # The attacker's family matters for blood feuds and alliance fallout.
            cur = await db.execute("SELECT family_id FROM character_birth_family WHERE user_id=?", (int(user_id),))
            attacker_row = await cur.fetchone()
            attacker_family_id = int(attacker_row[0]) if attacker_row else None

            victim_family = None
            victim_member = None
            cur = await db.execute("SELECT * FROM birth_families WHERE head_name=? ORDER BY family_id LIMIT 1", (target,))
            row = await cur.fetchone()
            if row:
                victim_family = dict(row)
            else:
                cur = await db.execute(
                    "SELECT * FROM birth_family_npcs WHERE name=? AND status='alive' ORDER BY npc_id LIMIT 1",
                    (target,),
                )
                member_row = await cur.fetchone()
                if member_row:
                    victim_member = dict(member_row)
                    cur = await db.execute("SELECT * FROM birth_families WHERE family_id=?", (int(victim_member["family_id"]),))
                    fam_row = await cur.fetchone()
                    victim_family = dict(fam_row) if fam_row else None

            if action == "npc_killed" and victim_family:
                fid = int(victim_family["family_id"])
                is_head = str(victim_family.get("head_name") or "") == target
                if victim_member:
                    await db.execute("UPDATE birth_family_npcs SET status='dead' WHERE npc_id=?", (int(victim_member["npc_id"]),))
                else:
                    await db.execute(
                        "UPDATE birth_family_npcs SET status='dead' WHERE family_id=? AND name=? AND status='alive'",
                        (fid, target),
                    )

                wealth_loss = sev * (2 if is_head else 1)
                influence_loss = sev * (4 if is_head else 2)
                stability_loss = sev * (6 if is_head else 3)
                wealth = max(0, int(victim_family.get("wealth", 0)) - wealth_loss)
                influence = max(0, int(victim_family.get("influence", 0)) - influence_loss)
                stability = max(0, int(victim_family.get("stability", 0)) - stability_loss)
                score = wealth + influence + stability
                tier = 1 if score < 70 else 2 if score < 125 else 3 if score < 185 else 4 if score < 245 else 5
                history = []
                try:
                    history = json.loads(victim_family.get("history_json") or "[]")
                except Exception:
                    history = []

                head_name = str(victim_family.get("head_name") or "Family Head")
                head_title = str(victim_family.get("head_title") or "Family Head")
                head_realm = int(victim_family.get("head_realm_index", 0))
                head_phase = int(victim_family.get("head_phase", 1))
                if is_head:
                    cur = await db.execute(
                        """SELECT name,realm_index,phase FROM birth_family_npcs
                           WHERE family_id=? AND status='alive'
                           ORDER BY realm_index DESC,phase DESC,npc_id ASC LIMIT 1""",
                        (fid,),
                    )
                    successor = await cur.fetchone()
                    if successor:
                        head_name = str(successor[0])
                        head_realm = int(successor[1])
                        head_phase = int(successor[2])
                        history.append(
                            f"{target}, the family leader, was killed by an outside cultivator. {head_name} inherited the seat amid severe instability."
                        )
                    else:
                        head_name = "Vacant Ancestral Seat"
                        head_title = "Acting Family Council"
                        head_realm = 0
                        head_phase = 1
                        history.append(
                            f"{target}, the family leader, was killed. No clear successor remained, and an acting council took control."
                        )
                    impacts.append(f"{victim_family['family_name']} lost its leader and suffered a succession crisis")
                    leadership_change = {
                        "family_id": fid, "family_name": str(victim_family['family_name']),
                        "old_leader": target, "new_leader": head_name, "new_title": head_title,
                    }
                else:
                    history.append(f"{target}, a member of the family, was killed by an outside cultivator, weakening the household.")
                    impacts.append(f"{victim_family['family_name']} suffered a bloodline casualty")

                await db.execute(
                    """UPDATE birth_families SET wealth=?,influence=?,stability=?,tier=?,head_name=?,head_title=?,
                       head_realm_index=?,head_phase=?,history_json=?,updated_at=? WHERE family_id=?""",
                    (wealth, influence, stability, tier, head_name, head_title, head_realm, head_phase, json.dumps(history[-100:]), now, fid),
                )
                await db.execute(
                    "UPDATE martial_clan_branches SET loyalty=MAX(0,loyalty-?),martial_strength=MAX(0,martial_strength-?),updated_at=? WHERE family_id=? AND status='active'",
                    (sev * (3 if is_head else 1), sev * (2 if is_head else 1), now, fid),
                )
                await db.execute(
                    "UPDATE martial_clan_retainers SET loyalty=MAX(0,loyalty-?),updated_at=? WHERE family_id=? AND status='active'",
                    (sev * (4 if is_head else 2), now, fid),
                )
                await db.execute(
                    "UPDATE martial_clan_retainers SET status='deserted',updated_at=? WHERE family_id=? AND status='active' AND loyalty<10",
                    (now, fid),
                )
                # Existing allies become less confident after a leadership decapitation.
                if is_head:
                    await db.execute(
                        "UPDATE martial_clan_relations SET relation_score=MAX(-100,relation_score-?),updated_at=? WHERE family_id=? AND active=1",
                        (sev * 3, now, fid),
                    )
                # Killing a clan member can create a persistent blood feud between families.
                if attacker_family_id and attacker_family_id != fid:
                    cur = await db.execute("SELECT family_name FROM birth_families WHERE family_id=?", (attacker_family_id,))
                    attacker_family_row = await cur.fetchone()
                    attacker_family_name = str(attacker_family_row[0]) if attacker_family_row else f"Family {attacker_family_id}"
                    cur = await db.execute(
                        "SELECT relation_id FROM martial_clan_relations WHERE family_id=? AND partner_family_id=? AND active=1 LIMIT 1",
                        (fid, attacker_family_id),
                    )
                    rel = await cur.fetchone()
                    feud_score = -min(100, 45 + sev * 5)
                    if rel:
                        await db.execute(
                            "UPDATE martial_clan_relations SET relation_type='blood_feud',relation_score=?,updated_at=? WHERE relation_id=?",
                            (feud_score, now, int(rel[0])),
                        )
                    else:
                        await db.execute(
                            """INSERT INTO martial_clan_relations(
                                   family_id,partner_family_id,partner_name,relation_type,relation_score,active,started_game_minute,updated_at
                               ) VALUES(?,?,?,'blood_feud',?,1,?,?)""",
                            (fid, attacker_family_id, attacker_family_name, feud_score, int(game_minute), now),
                        )
                    impacts.append(f"a blood feud formed against {attacker_family_name}")
                    blood_feud_name = attacker_family_name

            # Named civilization NPCs can be officials, merchants, sect members, or hidden experts.
            cur = await db.execute("SELECT * FROM npc_civilization_state WHERE npc_name=?", (target,))
            npc_row = await cur.fetchone()
            if npc_row:
                npc = dict(npc_row)
                if action == "npc_killed":
                    await db.execute(
                        "UPDATE npc_civilization_state SET status='dead',activity=?,last_game_minute=?,updated_at=? WHERE npc_name=?",
                        (f"Killed by player {user_id}", int(game_minute), now, target),
                    )
                    influence = max(0, int(npc.get("influence", 0)))
                    region_sev = max(sev, 1 + influence // 25)
                    await db.execute(
                        """UPDATE civilization_regions SET
                           security=MAX(0,security-?),unrest=MIN(100,unrest+?),prosperity=MAX(0,prosperity-?),
                           last_game_minute=?,updated_at=? WHERE location=?""",
                        (region_sev * 2, region_sev * 3, max(1, region_sev), int(game_minute), now, str(npc.get("current_location") or loc)),
                    )
                    await db.execute(
                        "INSERT INTO civilization_events(location,event_text,severity,game_minute,created_at) VALUES(?,?,?,?,?)",
                        (str(npc.get("current_location") or loc), f"{target} was killed by a cultivator, destabilizing local power networks.", region_sev, int(game_minute), now),
                    )
                    impacts.append(f"{target}'s death increased regional unrest")
                    trade_disruption = region_sev * (2 if any(k in str(npc.get("profession", "")).lower() for k in ("merchant", "caravan", "trader", "auction")) else 1)
                    market_location = str(npc.get("current_location") or loc)
                    await db.execute(
                        "UPDATE economy_markets SET supply=MAX(1,supply-?),demand=MIN(500,demand+?),updated_at=? WHERE location=?",
                        (trade_disruption, max(1, region_sev // 2), now, market_location),
                    )
                    await db.execute(
                        "INSERT INTO economy_events(location,item_id,event_text,game_minute,created_at) VALUES(?,NULL,?,?,?)",
                        (market_location, f"The death of {target} disrupted local confidence and short-term supply routes.", int(game_minute), now),
                    )
                    faction = str(npc.get("faction") or "Independent")
                    npc_faction = "" if faction == "Independent" else faction
                    if faction != "Independent":
                        await db.execute(
                            """UPDATE sect_politics_state SET influence=MAX(0,influence-?),cohesion=MAX(0,cohesion-?),resources=MAX(0,resources-?),updated_at=? WHERE sect_name=?""",
                            (region_sev * 2, region_sev * 3, region_sev, now, faction),
                        )
                        await db.execute(
                            "INSERT INTO sect_politics_events(sect_name,event_text,severity,game_minute,created_at) VALUES(?,?,?,?,?)",
                            (faction, f"The death of {target} damaged the sect's influence and triggered internal blame.", region_sev, int(game_minute), now),
                        )
                        impacts.append(f"{faction} lost influence and cohesion")
                elif action == "npc_spared":
                    await db.execute(
                        "UPDATE npc_civilization_state SET activity=?,last_game_minute=?,updated_at=? WHERE npc_name=?",
                        (f"Defeated and spared by player {user_id}", int(game_minute), now, target),
                    )
                    impacts.append(f"{target} survived and remembers being spared")

            details["impacts"] = impacts
            await db.execute(
                """INSERT INTO world_action_events(
                       user_id,action_type,target_type,target_key,location,severity,game_minute,payload_json,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (int(user_id), action, "npc", target, loc, sev, int(game_minute), json.dumps(details), now),
            )
            await db.commit()

        actor = await self.db.get_character(int(user_id)) or {}
        actor_name = str(actor.get("name") or f"Cultivator {user_id}")
        if action == "npc_killed":
            await self.db.record_world_history_event(
                event_type="death", title=f"Death of {target}",
                summary=(
                    f"{target} was killed by {actor_name} at {loc}. "
                    + ("Consequences: " + "; ".join(impacts) if impacts else "The death entered the permanent world record.")
                ),
                significance=min(100, 65 + sev * 4), visibility="public", location=loc, faction=npc_faction,
                actor_type="player", actor_key=str(int(user_id)), actor_name=actor_name,
                target_type="npc", target_key=target, target_name=target, related_user_id=int(user_id), related_npc_name=target,
                tags=("death","battle","npc",*(["major battle"] if sev >= 5 else [])), game_minute=int(game_minute),
                metadata={"severity": sev, "impacts": impacts, **details},
                source_key=f"npc_death:{target}:{int(game_minute)}:{int(user_id)}",
            )
            if sev >= 5:
                await self.db.record_world_history_event(
                    event_type="major_battle", title=f"Major battle: {actor_name} defeated {target}",
                    summary=f"A high-stakes battle at {loc} ended with {target}'s death at the hands of {actor_name}.",
                    significance=min(100, 72 + sev * 3), visibility="public", location=loc, faction=npc_faction,
                    actor_type="player", actor_key=str(int(user_id)), actor_name=actor_name,
                    target_type="npc", target_key=target, target_name=target, related_user_id=int(user_id), related_npc_name=target,
                    tags=("major battle","battle","victory","death"), game_minute=int(game_minute),
                    metadata={"severity": sev, **details}, source_key=f"major_battle:{details.get('battle_id','na')}:kill",
                )
        elif action == "npc_spared" and sev >= 4:
            await self.db.record_world_history_event(
                event_type="major_battle", title=f"{actor_name} defeated and spared {target}",
                summary=f"A major battle at {loc} ended when {actor_name} defeated {target} but chose to spare their life.",
                significance=min(94, 68 + sev * 3), visibility="public", location=loc, faction=npc_faction,
                actor_type="player", actor_key=str(int(user_id)), actor_name=actor_name,
                target_type="npc", target_key=target, target_name=target, related_user_id=int(user_id), related_npc_name=target,
                tags=("major battle","battle","mercy","spared"), game_minute=int(game_minute),
                metadata={"severity": sev, **details}, source_key=f"major_battle:{details.get('battle_id','na')}:spare",
            )

        if leadership_change:
            await self.db.record_world_history_event(
                event_type="leadership_change",
                title=f"Leadership changed in {leadership_change['family_name']}",
                summary=(
                    f"After {leadership_change['old_leader']} was killed, {leadership_change['new_leader']} "
                    f"assumed the leadership of {leadership_change['family_name']} as {leadership_change['new_title']}."
                ),
                significance=88, visibility="public", location=loc, faction=str(leadership_change['family_name']),
                actor_type="clan", actor_key=str(leadership_change['family_id']), actor_name=str(leadership_change['family_name']),
                target_type="leader", target_key=str(leadership_change['new_leader']), target_name=str(leadership_change['new_leader']),
                tags=("leadership","succession","clan","death"), game_minute=int(game_minute),
                metadata=leadership_change,
                source_key=f"leadership:{int(leadership_change['family_id'])}:{int(game_minute)}",
            )
        if blood_feud_name and victim_family:
            await self.db.record_world_history_event(
                event_type="blood_feud", title=f"Blood feud: {victim_family['family_name']} and {blood_feud_name}",
                summary=f"The death of {target} hardened relations into a blood feud between {victim_family['family_name']} and {blood_feud_name}.",
                significance=82, visibility="public", location=loc, faction=str(victim_family['family_name']),
                actor_type="clan", actor_key=str(victim_family['family_id']), actor_name=str(victim_family['family_name']),
                target_type="clan", target_key=blood_feud_name, target_name=blood_feud_name,
                tags=("blood feud","grudge","clan","death"), game_minute=int(game_minute),
                metadata={"victim": target, "severity": sev},
                source_key=f"blood_feud:{int(victim_family['family_id'])}:{blood_feud_name}:{int(game_minute)}",
            )
        return {"action_type": action, "target": target, "severity": sev, "impacts": impacts}

    async def apply_forbidden_art_use(
        self, *, user_id: int, technique_id: str, technique_name: str, location: str,
        game_minute: int, exposure: int, karma_cost: int, witnessed: bool,
    ) -> dict[str, Any]:
        """Apply persistent social consequences for predatory/forbidden cultivation.

        The technique itself is handled by combat. This hook makes its *use* part of
        the living world: orthodox sects lose cohesion around exposed disciples,
        families suffer reputation pressure, regions gain unrest, while demonic
        sects can instead gain a little influence from a feared display.
        """
        membership = await self.db.get_sect_membership(int(user_id))
        sect_name = str(membership.get("sect_name")) if membership else ""
        sect_alignment = str(self.sects.get(sect_name, {}).get("alignment", "Neutral")) if sect_name else "Neutral"
        world_rules = self.world.get("world_rules", {})
        policy = reaction_policy(world_rules, sect_alignment=sect_alignment)
        sev = effective_exposure(
            exposure,
            witnessed=bool(witnessed),
            world_rules=world_rules,
        )
        now = time.time()
        impacts: list[str] = []
        if not witnessed:
            impacts.append("the forbidden art was largely concealed, reducing political exposure")
        unrest_delta = int(policy["regional_unrest"]) * sev
        family_loss = int(policy["family_stability_loss"]) * sev
        sect_loss = int(policy["sect_cohesion_loss"]) * sev

        async with self.db._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            if witnessed:
                await db.execute(
                    "UPDATE civilization_regions SET unrest=MIN(100,unrest+?),security=MAX(0,security-?),updated_at=? WHERE location=?",
                    (unrest_delta, max(1, sev // 2), now, str(location)),
                )
                await db.execute(
                    "INSERT INTO civilization_events(location,event_text,severity,game_minute,created_at) VALUES(?,?,?,?,?)",
                    (str(location), f"Witnesses reported {technique_name}, a forbidden cultivation art.", sev, int(game_minute), now),
                )
                impacts.append(f"{location} gained unrest from reports of forbidden cultivation")

            cur = await db.execute("SELECT family_id FROM character_birth_family WHERE user_id=?", (int(user_id),))
            fam = await cur.fetchone()
            if fam and witnessed:
                fid = int(fam[0])
                await db.execute(
                    "UPDATE birth_families SET stability=MAX(0,stability-?),influence=MAX(0,influence-?),updated_at=? WHERE family_id=?",
                    (family_loss, max(1, family_loss // 2), now, fid),
                )
                cur2 = await db.execute("SELECT family_name FROM birth_families WHERE family_id=?", (fid,))
                famrow = await cur2.fetchone()
                impacts.append(f"{str(famrow[0]) if famrow else 'the birth family'} lost stability and influence from the scandal")

            if membership and witnessed:
                alignment = sect_alignment
                if alignment.casefold() == "demonic":
                    await db.execute(
                        "UPDATE sect_politics_state SET influence=MIN(100,influence+?),doctrine_pressure=MIN(100,doctrine_pressure+?),updated_at=? WHERE sect_name=?",
                        (max(1, sev // 2), sev, now, sect_name),
                    )
                    impacts.append(f"{sect_name} approved the ruthless display and gained a little feared influence")
                elif alignment.casefold() == "orthodox":
                    await db.execute(
                        "UPDATE sect_politics_state SET cohesion=MAX(0,cohesion-?),influence=MAX(0,influence-?),doctrine_pressure=MIN(100,doctrine_pressure+?),updated_at=? WHERE sect_name=?",
                        (sect_loss, max(1, sect_loss // 2), sev * 2, now, sect_name),
                    )
                    impacts.append(f"{sect_name} suffered internal pressure over a disciple exposing forbidden arts")
                else:
                    await db.execute(
                        "UPDATE sect_politics_state SET cohesion=MAX(0,cohesion-?),doctrine_pressure=MIN(100,doctrine_pressure+?),updated_at=? WHERE sect_name=?",
                        (max(1, sect_loss // 2), sev, now, sect_name),
                    )
                    impacts.append(f"{sect_name} became divided over the forbidden technique")
                await db.execute(
                    "INSERT INTO sect_politics_events(sect_name,event_text,severity,game_minute,created_at) VALUES(?,?,?,?,?)",
                    (sect_name, f"A member publicly used {technique_name}; elders and rivals reacted according to sect doctrine.", sev, int(game_minute), now),
                )

            await db.execute(
                """INSERT INTO world_action_events(user_id,action_type,target_type,target_key,location,severity,game_minute,payload_json,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (int(user_id), "forbidden_art_used", "technique", str(technique_id), str(location), sev, int(game_minute),
                 json.dumps({"technique": technique_name, "witnessed": bool(witnessed), "karma_cost": int(karma_cost), "impacts": impacts}), now),
            )
            await db.commit()
        return {"severity": sev, "witnessed": bool(witnessed), "impacts": impacts}

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

    async def apply_random_event(
        self, *, event_id: str, title: str, location: str, game_minute: int,
        severity: int, effect: dict[str, Any] | None,
    ) -> list[str]:
        """Apply bounded, persistent simulation changes for a shared random event.

        Content controls the direction and size of each delta, while this shared
        boundary clamps every stored value. Event expiry ends participation; it
        deliberately does not undo damage, shortages, unrest, or prosperity.
        """
        definition = dict(effect or {})
        if not definition:
            return []
        loc = str(location)
        sev = int(_clamp(int(severity), 1, 10))
        now = time.time()
        impacts: list[str] = []
        event_text = str(definition.get("history") or f"{title} changed the region.")[:500]

        async with self.db._connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")

            region_effect = dict(definition.get("region") or {})
            if region_effect:
                cur = await db.execute("SELECT * FROM civilization_regions WHERE location=?", (loc,))
                row = await cur.fetchone()
                if row:
                    region = dict(row)
                    population_percent = float(_clamp(float(region_effect.get("population_percent", 0)), -25, 25))
                    population = max(0, int(round(int(region["population"]) * (1 + population_percent / 100))))
                    values = {
                        "prosperity": int(_clamp(int(region["prosperity"]) + int(region_effect.get("prosperity", 0)), 0, 100)),
                        "security": int(_clamp(int(region["security"]) + int(region_effect.get("security", 0)), 0, 100)),
                        "spirit_resources": int(_clamp(int(region["spirit_resources"]) + int(region_effect.get("spirit_resources", 0)), 0, 100)),
                        "food_supply": int(_clamp(int(region["food_supply"]) + int(region_effect.get("food_supply", 0)), 0, 100)),
                        "migration_pressure": int(_clamp(int(region["migration_pressure"]) + int(region_effect.get("migration_pressure", 0)), 0, 100)),
                        "unrest": int(_clamp(int(region["unrest"]) + int(region_effect.get("unrest", 0)), 0, 100)),
                    }
                    await db.execute(
                        """UPDATE civilization_regions SET population=?,prosperity=?,security=?,spirit_resources=?,
                               food_supply=?,migration_pressure=?,unrest=?,last_game_minute=?,updated_at=? WHERE location=?""",
                        (population, values["prosperity"], values["security"], values["spirit_resources"],
                         values["food_supply"], values["migration_pressure"], values["unrest"], int(game_minute), now, loc),
                    )
                    await db.execute(
                        "INSERT INTO civilization_events(location,event_text,severity,game_minute,created_at) VALUES(?,?,?,?,?)",
                        (loc, event_text, sev, int(game_minute), now),
                    )
                    impacts.append("regional population, security, resources, or unrest changed")

            market_effect = dict(definition.get("market") or {})
            if market_effect:
                supply = int(_clamp(int(market_effect.get("supply", 0)), -200, 200))
                demand = int(_clamp(int(market_effect.get("demand", 0)), -200, 200))
                price_index = float(_clamp(float(market_effect.get("price_index", 0)), -1.5, 1.5))
                cur = await db.execute(
                    """UPDATE economy_markets SET supply=MIN(9999,MAX(1,supply+?)),
                           demand=MIN(500,MAX(1,demand+?)),price_index=MIN(5.0,MAX(0.25,price_index+?)),updated_at=?
                       WHERE location=?""",
                    (supply, demand, price_index, now, loc),
                )
                if int(cur.rowcount or 0)>0:
                    await db.execute(
                        "INSERT INTO economy_events(location,item_id,event_text,game_minute,created_at) VALUES(?,NULL,?,?,?)",
                        (loc, event_text, int(game_minute), now),
                    )
                    impacts.append("local supply, demand, and prices shifted")

            sect_effect = dict(definition.get("sect") or {})
            if sect_effect:
                cur = await db.execute("SELECT sect_name FROM sect_politics_state ORDER BY sect_name")
                sect_names = [str(row[0]) for row in await cur.fetchall()]
                await db.execute(
                    """UPDATE sect_politics_state SET influence=MIN(100,MAX(0,influence+?)),
                           cohesion=MIN(100,MAX(0,cohesion+?)),resources=MIN(100,MAX(0,resources+?)),
                           recruitment_pressure=MIN(100,MAX(0,recruitment_pressure+?)),
                           doctrine_pressure=MIN(100,MAX(0,doctrine_pressure+?)),updated_at=?""",
                    (int(sect_effect.get("influence", 0)), int(sect_effect.get("cohesion", 0)),
                     int(sect_effect.get("resources", 0)), int(sect_effect.get("recruitment_pressure", 0)),
                     int(sect_effect.get("doctrine_pressure", 0)), now),
                )
                for sect_name in sect_names:
                    await db.execute(
                        "INSERT INTO sect_politics_events(sect_name,event_text,severity,game_minute,created_at) VALUES(?,?,?,?,?)",
                        (sect_name, event_text, sev, int(game_minute), now),
                    )
                if sect_names:
                    impacts.append("sect influence, cohesion, resources, or recruitment pressure changed")

            await db.commit()
        return impacts

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

    async def alchemy_forage_profile(self, location: str, realm_index: int = 0) -> dict[str, Any]:
        """Generate a location-aware herb-gathering opportunity.

        The yield is tied to the persistent civilization simulation's
        ``spirit_resources`` value, so harvest booms, migration and regional
        disturbances directly affect the alchemy loop instead of gathering
        being a disconnected random command.
        """
        region = await self.civilization_status(str(location))
        loc_data = self.locations.get(str(location), {})
        world_name = str((region or {}).get("world_name") or loc_data.get("world") or "Mortal World")
        resources = int((region or {}).get("spirit_resources", 50))
        resource_bonus = forage_bonus_from_resources(resources)
        world_tier = {"Mortal World": 0, "Spiritual World": 1, "Immortal World": 2, "Celestial World": 3}.get(world_name, 0)
        tn = max(8, 12 + world_tier * 2 - resource_bonus)

        common_qty = max(1, 1 + resources // 35 + max(0, int(realm_index)) // 8)
        loot: dict[str, int] = {"spirit_herb": min(5, common_qty)}
        rare_pool: list[tuple[str, int]] = []
        if "fire_spirit_root_herb" in self.items:
            rare_pool.append(("fire_spirit_root_herb", 35))
        if "ice_spirit_blazing_grass" in self.items and world_tier >= 1:
            rare_pool.append(("ice_spirit_blazing_grass", 24))
        if "twin_extremes_fruit" in self.items and world_tier >= 1:
            rare_pool.append(("twin_extremes_fruit", 15))
        if "jade_life_herb" in self.items and world_tier >= 2:
            rare_pool.append(("jade_life_herb", 6))

        rare_found = ""
        if rare_pool:
            chance_bonus = max(0, resources - 50) // 3 + max(0, int(realm_index)) // 2
            item_id, base_chance = secrets.choice(rare_pool)
            if secrets.randbelow(100) < min(65, base_chance + chance_bonus):
                loot[item_id] = loot.get(item_id, 0) + 1
                rare_found = item_id
        return {
            "location": str(location),
            "world_name": world_name,
            "spirit_resources": resources,
            "resource_bonus": resource_bonus,
            "tn": tn,
            "loot": loot,
            "rare_found": rare_found,
        }

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
            out["age_years"] = npc_age_years(out, max(int(out.get("last_game_minute") or 0), int(out.get("last_social_game_minute") or 0), int(out.get("last_cultivation_game_minute") or 0))) if out.get("birth_game_minute") is not None else None
            out["lifespan_years"] = npc_lifespan_years(out, int(out.get("realm_index") or 0), int(out.get("phase") or 1)) if out.get("natural_lifespan_years") is not None else None
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

    async def market_trade(self, *, user_id: int, location: str, item_id: str, quantity: int, buy: bool) -> dict[str, Any]:
        quantity=max(1,min(100,int(quantity))); now=time.time()
        async with self.db._connect() as db:
            db.row_factory=aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur=await db.execute("SELECT * FROM economy_markets WHERE location=? AND item_id=?",(location,item_id)); row=await cur.fetchone()
            if not row: raise ValueError("That item is not traded in this market")
            m=dict(row); unit=max(1,int(round(int(m["base_price"])*float(m["price_index"])))); currency=str(m["currency_id"])
            if buy:
                if int(m["supply"])<quantity: raise ValueError("The market does not have that many in stock")
                cost=unit*quantity
                cur=await db.execute("SELECT balance FROM currency_wallets WHERE user_id=? AND currency_id=?",(user_id,currency)); w=await cur.fetchone(); bal=int(w[0]) if w else 0
                if bal<cost: raise ValueError(f"Not enough {currency.replace('_',' ')}")
                await db.execute("UPDATE currency_wallets SET balance=balance-? WHERE user_id=? AND currency_id=?",(cost,user_id,currency))
                if currency=="low_spirit_stone": await db.execute("UPDATE characters SET spirit_stones=MAX(0,spirit_stones-?),updated_at=? WHERE user_id=?",(cost,now,user_id))
                await db.execute("INSERT INTO inventory(user_id,item_id,quantity) VALUES(?,?,?) ON CONFLICT(user_id,item_id) DO UPDATE SET quantity=quantity+excluded.quantity",(user_id,item_id,quantity))
                await db.execute("UPDATE economy_markets SET supply=supply-?,demand=MIN(500,demand+?),updated_at=? WHERE location=? AND item_id=?",(quantity,max(1,quantity//2),now,location,item_id))
                total=cost
            else:
                cur=await db.execute("SELECT quantity FROM inventory WHERE user_id=? AND item_id=?",(user_id,item_id)); inv=await cur.fetchone(); owned=int(inv[0]) if inv else 0
                if owned<quantity: raise ValueError("You do not carry that many")
                sell_unit=max(1,int(round(unit*0.70))); total=sell_unit*quantity
                await db.execute("UPDATE inventory SET quantity=quantity-? WHERE user_id=? AND item_id=?",(quantity,user_id,item_id))
                await db.execute("INSERT INTO currency_wallets(user_id,currency_id,balance) VALUES(?,?,?) ON CONFLICT(user_id,currency_id) DO UPDATE SET balance=balance+excluded.balance",(user_id,currency,total))
                if currency=="low_spirit_stone": await db.execute("UPDATE characters SET spirit_stones=spirit_stones+?,updated_at=? WHERE user_id=?",(total,now,user_id))
                await db.execute("UPDATE economy_markets SET supply=MIN(9999,supply+?),demand=MAX(1,demand-?),updated_at=? WHERE location=? AND item_id=?",(quantity,max(1,quantity//2),now,location,item_id))
                unit=sell_unit
            await db.commit()
        return {"item_id":item_id,"quantity":quantity,"unit_price":unit,"total":total,"currency_id":currency,"buy":buy}

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
