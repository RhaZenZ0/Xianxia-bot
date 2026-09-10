from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Iterable

from ..rules.advanced_runtime import describe_era
from ..rules.birthfamily import family_tier_name, karma_label
from ..rules.realm_hubs import realm_hub_by_location
from ..rules.worldtime import from_game_minutes
from ..rules.npc_memory import format_memories, public_mood_hint
from .rag import MemoryRAGRetriever, RetrievalProfile, resolve_retrieval_profile


def _clip(value: Any, limit: int = 260) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _join(items: Iterable[str], *, empty: str = "None") -> str:
    values = [x for x in (str(v).strip() for v in items) if x]
    return ", ".join(values) if values else empty


def _fit_context_to_budget(
    lines: list[str], *, narration_contract: str, budget: int, rag_text: str = ""
) -> str:
    """Prefer scene-critical state and retrieved context when a prompt is tight.

    The old v1 behavior cut the packet from the tail, which could remove RAG
    precisely when a smaller context budget was selected. This stable priority
    pass keeps current scene facts, NPC/battle state and RAG ahead of lower-value
    background details while always preserving the game-engine authority contract.
    """
    full = "\n".join(lines)
    if len(full) <= budget:
        return full

    critical_prefixes = (
        "CANONICAL NARRATOR CONTEXT", "Scene type:", "World time:", "Location:",
        "Location description:", "Location protection:", "Character:",
        "Active effects:", "Active conditions:", "Active battle:",
        "Nearby publicly observable NPCs:", "NPC DIRECTOR NOTES",
        "Focused NPC:", "Relationship:", "NPC continuity:", "Affiliations:",
        "Relevant bounties:", "Relevant grudges:", "Active local events:",
        "Active canonical events at this location:",
    )
    body_lines = [line for line in lines if line != narration_contract]

    def priority(item: tuple[int, str]) -> tuple[int, int]:
        index, line = item
        is_rag = bool(rag_text and line == rag_text)
        is_critical = is_rag or line.startswith(critical_prefixes)
        return (0 if is_critical else 1, index)

    ordered = [line for _, line in sorted(enumerate(body_lines), key=priority)]
    marker = "[Lower-priority context omitted to fit this scene's narrator budget.]"
    body_budget = max(700, int(budget) - len(narration_contract) - len(marker) - 2)
    body = "\n".join(ordered)
    if len(body) > body_budget:
        body = body[:body_budget].rstrip()
    return body + "\n" + marker + "\n" + narration_contract


@dataclass(slots=True)
class NarratorSceneContext:
    """A player-safe slice of canonical state prepared for the narrator.

    The text is descriptive context only. It never authorizes the model to mutate
    state, and deliberately excludes simulator-only secrets such as hidden-master
    true realms.
    """

    text: str
    game_minute: int
    location: str
    world_name: str
    retrieval_profile: str = "default"
    context_budget_chars: int = 0
    rag_memory_count: int = 0
    rag_canon_count: int = 0
    rag_history_count: int = 0


class NarratorContextBuilder:
    """Build compact, scene-relevant, player-safe context for narration.

    The persistent database and world simulator remain authoritative. This class
    only reads already-canonical state and formats it for an LLM. Keep sensitive
    simulation fields out of this layer unless a dedicated game mechanic has
    explicitly revealed them to the player.
    """

    def __init__(
        self,
        *,
        db: Any,
        simulator: Any,
        world: Any,
        world_time_scale: int = 4,
        max_chars: int = 4000,
        epic_max_chars: int = 6500,
        rag_query_cache_seconds: float = 4.0,
        rag_canon_cache_seconds: float = 120.0,
    ):
        self.db = db
        self.simulator = simulator
        self.world = world
        self.world_time_scale = int(world_time_scale)
        self.max_chars = max(2500, int(max_chars))
        self.epic_max_chars = max(self.max_chars, int(epic_max_chars))
        self.rag = MemoryRAGRetriever(
            db=db, world=world,
            query_cache_seconds=rag_query_cache_seconds,
            canon_cache_seconds=rag_canon_cache_seconds,
        )

    def _profile(self, scene_type: str, *, focus_npc: str = "") -> RetrievalProfile:
        return resolve_retrieval_profile(
            scene_type, focus_npc=focus_npc,
            routine_cap=self.max_chars, epic_cap=self.epic_max_chars,
        )

    def cache_stats(self) -> dict[str, dict[str, int]]:
        return self.rag.cache_stats()

    async def build(
        self,
        character: dict[str, Any],
        *,
        scene_type: str = "roleplay",
        lineage_context: str | None = None,
        query_text: str = "",
        focus_npc: str = "",
    ) -> NarratorSceneContext:
        user_id = int(character.get("user_id") or 0)
        location = str(character.get("location") or "Unknown")
        profile = self._profile(scene_type, focus_npc=focus_npc)

        clock = await self.db.get_world_clock(scale=self.world_time_scale)
        game_minute = int(clock.get("game_minute", 0))
        wt = from_game_minutes(game_minute)

        async def maybe(coro, default=None):
            try:
                return await coro
            except Exception:
                return default

        async def maybe_sect_abode():
            getter = getattr(self.db, "get_sect_abode_by_location", None)
            if getter is None:
                return None
            return await maybe(getter(location))

        async def maybe_npc_relationships():
            getter = getattr(self.db, "list_npc_relationships", None)
            if getter is None:
                return []
            return await maybe(getter(user_id, 20), [])

        async def maybe_manuals():
            getter = getattr(self.db, "get_manuals", None)
            if getter is None:
                return []
            return await maybe(getter(user_id), [])

        @asynccontextmanager
        async def no_reuse():
            yield None

        reuse = getattr(self.db, "reuse_connection", None)
        session = reuse() if callable(reuse) else no_reuse()
        async with session:
            if user_id:
                # Keep this sequential: Database.reuse_connection deliberately
                # shares one SQLite connection for the complete narrator unit of
                # work, removing connection/PRAGMA churn while preserving simple
                # transaction ordering for legacy getters that perform cleanup.
                state = {
                    "location_def": await maybe(self.db.get_location_definition(location), {}),
                    "abode": await maybe(self.db.get_abode_by_location(location)),
                    "sect_abode": await maybe_sect_abode(),
                    "personal_world": await maybe(self.db.get_personal_world_by_location(location)),
                    "family": await maybe(self.db.get_birth_family(user_id)),
                    "fate": await maybe(self.db.get_fate(user_id), {}),
                    "membership": await maybe(self.db.get_sect_membership(user_id)),
                    "social": await maybe(self.db.get_social_state(user_id), {}),
                    "effects": await maybe(self.db.get_active_effects(user_id, game_minute), []),
                    "conditions": await maybe(self.db.get_conditions(user_id, active_only=True), []),
                    "events": await maybe(self.db.get_active_world_events(location), []),
                    "era": describe_era(await maybe(self.db.get_current_era())),
                    "beasts": await maybe(self.db.get_spirit_beasts(user_id), []),
                    "party": await maybe(self.db.get_party(user_id)),
                    "equipment": await maybe(self.db.get_equipment(user_id, equipped_only=True), []),
                    "battle": await maybe(self.db.get_active_battle(user_id)),
                    "bounties": await maybe(self.db.get_bounties(user_id, active_only=True), []),
                    "grudges": await maybe(self.db.get_grudges(user_id, active_only=True), []),
                    "aptitudes": await maybe(self.db.get_aptitudes(user_id), {}),
                    "npc_relationships": await maybe_npc_relationships(),
                    "manuals": await maybe_manuals(),
                    "region": await maybe(self.simulator.civilization_status(location)),
                }
            else:
                state = {
                    "location_def": await maybe(self.db.get_location_definition(location), {}),
                    "abode": None,
                    "sect_abode": None,
                    "personal_world": None,
                    "family": None,
                    "fate": {},
                    "membership": None,
                    "social": {},
                    "effects": [],
                    "conditions": [],
                    "events": await maybe(self.db.get_active_world_events(location), []),
                    "era": describe_era(await maybe(self.db.get_current_era())),
                    "beasts": [],
                    "party": None,
                    "equipment": [],
                    "battle": None,
                    "bounties": [],
                    "grudges": [],
                    "aptitudes": {},
                    "npc_relationships": [],
                    "manuals": [],
                    "region": await maybe(self.simulator.civilization_status(location)),
                }

            if lineage_context is None and user_id:
                lineage_context = await maybe(
                    self.db.describe_lineage_context(user_id),
                    "No master/disciple lineage recorded.",
                )
            membership_for_snapshot = state.get("membership")
            sect_name_for_snapshot = str((membership_for_snapshot or {}).get("sect_name") or "")
            state["sect_sim"] = (
                await maybe(self.simulator.sect_status(sect_name_for_snapshot), None)
                if sect_name_for_snapshot else None
            )
            state["deployed_array"] = await maybe(
                self.db.get_active_location_array(location, game_minute), None
            )
            state["npc_director_notes"] = {}
            if user_id:
                for npc_row in ((state.get("region") or {}).get("npcs") or [])[: profile.director_npc_limit]:
                    npc_name = str(npc_row.get("npc_name") or "").strip()
                    if not npc_name:
                        continue
                    memory_getter = getattr(self.db, "list_npc_player_memories", None)
                    mind_getter = getattr(self.db, "get_npc_mind_state", None)
                    memories = await maybe(memory_getter(user_id, npc_name, 2), []) if callable(memory_getter) else []
                    mind = (await maybe(mind_getter(npc_name), {}) or {}) if callable(mind_getter) else {}
                    state["npc_director_notes"][npc_name] = {
                        "memories": memories,
                        "mind": mind,
                        "life": {
                            "health": npc_row.get("health"), "injury": npc_row.get("injury"),
                            "injury_severity": npc_row.get("injury_severity"), "sect_rank": npc_row.get("sect_rank"),
                            "relationship_status": npc_row.get("relationship_status"), "spouse_name": npc_row.get("spouse_name"),
                            "children_count": npc_row.get("children_count"), "faction": npc_row.get("faction"),
                        },
                    }

            # Memory/RAG v1 is deliberately query-aware and permission-scoped.
            # It retrieves only this player's memories, the current location's
            # public description, and manuals the player has actually learned.
            effective_focus_npc = str(focus_npc or "").strip()
            if not effective_focus_npc and query_text:
                query_fold = " ".join(str(query_text).casefold().split())
                mentioned_npcs = []
                for row in ((state.get("region") or {}).get("npcs") or [])[: max(8, profile.nearby_npc_limit)]:
                    candidate = str(row.get("npc_name") or "").strip()
                    candidate_fold = " ".join(candidate.casefold().split())
                    if candidate_fold and len(candidate_fold) >= 4 and candidate_fold in query_fold:
                        mentioned_npcs.append(candidate)
                if len(mentioned_npcs) == 1:
                    effective_focus_npc = mentioned_npcs[0]
            if effective_focus_npc and not focus_npc:
                profile = self._profile(scene_type, focus_npc=effective_focus_npc)

            known_factions: list[str] = []
            if effective_focus_npc:
                for npc_row in ((state.get("region") or {}).get("npcs") or []):
                    if str(npc_row.get("npc_name") or "").casefold() != effective_focus_npc.casefold():
                        continue
                    for key in ("faction", "sect_affiliation"):
                        value = str(npc_row.get(key) or "").strip()
                        if value and value not in known_factions:
                            known_factions.append(value)
                    break
                npc_def = (getattr(self.world, "npcs", {}) or {}).get(effective_focus_npc, {})
                for key in ("faction", "sect_affiliation"):
                    value = str((npc_def or {}).get(key) or "").strip()
                    if value and value not in known_factions:
                        known_factions.append(value)
            else:
                membership = state.get("membership") or {}
                family = state.get("family") or {}
                for value in (membership.get("sect_name"), family.get("family_name")):
                    value = str(value or "").strip()
                    if value and value not in known_factions:
                        known_factions.append(value)

            # Focused dialogue should always carry the exact relationship and
            # NPC continuity record, even if that NPC was not among the first
            # generic nearby/director rows selected before focus detection.
            state["focus_relationship"] = None
            if effective_focus_npc and user_id:
                relation_getter = getattr(self.db, "get_npc_relationship", None)
                if callable(relation_getter):
                    state["focus_relationship"] = await maybe(
                        relation_getter(user_id, effective_focus_npc), None
                    )

                if effective_focus_npc not in (state.get("npc_director_notes") or {}):
                    focus_row = next((
                        row for row in ((state.get("region") or {}).get("npcs") or [])
                        if str(row.get("npc_name") or "").casefold() == effective_focus_npc.casefold()
                    ), None)
                    if focus_row:
                        memory_getter = getattr(self.db, "list_npc_player_memories", None)
                        mind_getter = getattr(self.db, "get_npc_mind_state", None)
                        memories = await maybe(memory_getter(user_id, effective_focus_npc, 2), []) if callable(memory_getter) else []
                        mind = (await maybe(mind_getter(effective_focus_npc), {}) or {}) if callable(mind_getter) else {}
                        state.setdefault("npc_director_notes", {})[effective_focus_npc] = {
                            "memories": memories,
                            "mind": mind,
                            "life": {
                                "health": focus_row.get("health"), "injury": focus_row.get("injury"),
                                "injury_severity": focus_row.get("injury_severity"), "sect_rank": focus_row.get("sect_rank"),
                                "relationship_status": focus_row.get("relationship_status"), "spouse_name": focus_row.get("spouse_name"),
                                "children_count": focus_row.get("children_count"), "faction": focus_row.get("faction"),
                            },
                        }

            state["rag"] = await maybe(
                self.rag.retrieve(
                    character, query_text=str(query_text or ""), game_minute=game_minute,
                    location=location, known_manuals=state.get("manuals") or [],
                    known_factions=known_factions, focus_npc=effective_focus_npc,
                    max_chars=profile.rag_chars, profile=profile,
                ),
                None,
            )

        loc = dict(state.get("location_def") or self.world.locations.get(location, {}) or {})
        abode = state.get("abode")
        sect_abode = state.get("sect_abode")
        personal_world = state.get("personal_world")
        if abode:
            property_type = str(abode.get("property_type") or "homestead").replace("_", " ").title()
            loc.update({"world": "Private Player Property", "safe_zone": True, "description": f"Private {property_type}: {abode.get('name', location)}"})
        elif sect_abode:
            loc.update({"world": "Sect Abode", "safe_zone": True, "description": f"Private sect residence: {sect_abode.get('name', location)} in {sect_abode.get('sect_name', 'Unknown Sect')}"})
        elif personal_world:
            loc.update({"world": "Personal World", "description": f"Stabilized personal world: {personal_world.get('name', location)}"})
        world_name = str(loc.get("world") or self.world.realm_world(int(character.get("realm_index", 0))) or "Mortal World")

        lineage_context = lineage_context or "No master/disciple lineage recorded."

        # Routine free-form/action dialogue gets a compact, query-aware packet.
        # Exploration, battle, cultivation and epic scenes retain the richer
        # legacy context below because those scenes genuinely need more state.
        if profile.name in {"roleplay", "dialogue"}:
            query_fold = " ".join(str(query_text or "").casefold().split())
            try:
                realm_name = self.world.realm_name(int(character.get("realm_index", 0)), character.get("gender"))
            except Exception:
                realm_name = "Unknown realm"

            lines: list[str] = [
                "CANONICAL NARRATOR CONTEXT — TRUSTED READ-ONLY GAME STATE",
                f"World time: {wt.display}",
                f"Location: {location} | world: {world_name}",
                f"Location description: {_clip(loc.get('description', 'No description recorded.'), 360)}",
                f"Protection: {'PROTECTED; violence cannot mechanically begin here' if loc.get('safe_zone') else 'not protected'}",
                f"Player: {_clip(character.get('name', 'Unnamed cultivator'), 100)} | {realm_name} stage {character.get('phase', 1)} | path {_clip(character.get('path', 'Unknown'), 80)} | aura concealment {'on' if character.get('concealment_active') else 'off'}",
            ]

            membership = state.get("membership") or {}
            family = state.get("family") or {}
            affiliation_bits: list[str] = []
            if membership.get("sect_name"):
                affiliation_bits.append(f"sect {_clip(membership.get('sect_name'), 100)} ({_clip(membership.get('rank_name', 'disciple'), 70)})")
            if family.get("family_name"):
                affiliation_bits.append(f"family {_clip(family.get('family_name'), 100)}")
            if affiliation_bits:
                lines.append("Affiliations: " + " | ".join(affiliation_bits))
            if lineage_context and "No master/disciple lineage recorded" not in lineage_context:
                lines.append("Lineage: " + _clip(lineage_context, 320))

            effects = state.get("effects") or []
            conditions = state.get("conditions") or []
            immediate_bits: list[str] = []
            effect_names = [_clip(e.get("name") or e.get("effect_key"), 75) for e in effects[:4] if (e.get("name") or e.get("effect_key"))]
            condition_names = [_clip(c.get("name") or c.get("condition_key"), 75) for c in conditions[:4] if (c.get("name") or c.get("condition_key"))]
            if effect_names:
                immediate_bits.append("effects " + _join(effect_names))
            if condition_names:
                immediate_bits.append("conditions " + _join(condition_names))
            battle = state.get("battle")
            if battle:
                immediate_bits.append(f"battle with {_clip(battle.get('npc_name') or battle.get('target_name') or 'opponent', 90)}")
            if immediate_bits:
                lines.append("Immediate state: " + " | ".join(immediate_bits))

            relationship_map = {str(r.get("npc_name") or ""): r for r in (state.get("npc_relationships") or [])}
            region_npcs = (state.get("region") or {}).get("npcs") or []
            focus_row = None
            if effective_focus_npc:
                focus_row = next((row for row in region_npcs if str(row.get("npc_name") or "").casefold() == effective_focus_npc.casefold()), None)
                public = (getattr(self.world, "npcs", {}) or {}).get(effective_focus_npc, {}) or {}
                role = public.get("role") or (focus_row or {}).get("profession") or "local cultivator"
                activity = (focus_row or {}).get("activity") or "present in the scene"
                faction = (focus_row or {}).get("faction") or public.get("faction") or public.get("sect_affiliation") or ""
                npc_line = f"Focused NPC: {_clip(effective_focus_npc, 90)} | {_clip(role, 110)} | {_clip(activity, 120)}"
                if faction:
                    npc_line += f" | faction {_clip(faction, 100)}"
                lines.append(npc_line)

                relationship = state.get("focus_relationship") or relationship_map.get(effective_focus_npc)
                if relationship:
                    lines.append(
                        "Relationship: "
                        f"trust {int(relationship.get('trust',0)):+d}, respect {int(relationship.get('respect',0)):+d}, "
                        f"fear {int(relationship.get('fear',0)):+d}, affection {int(relationship.get('affection',0)):+d}, "
                        f"debt {int(relationship.get('debt',0)):+d}, grudge {int(relationship.get('grudge',0)):+d}"
                        + (f" | prior: {_clip(relationship.get('last_summary'), 220)}" if relationship.get('last_summary') else "")
                    )

                note = (state.get("npc_director_notes") or {}).get(effective_focus_npc) or {}
                if note:
                    mind = dict(note.get("mind") or {})
                    public = (getattr(self.world, "npcs", {}) or {}).get(effective_focus_npc, {}) or {}
                    continuity: list[str] = []
                    if public.get("personality"):
                        continuity.append("personality " + _clip(public.get("personality"), 130))
                    if public.get("speech"):
                        continuity.append("speech " + _clip(public.get("speech"), 110))
                    mood = public_mood_hint(str(mind.get("mood") or ""))
                    if mood:
                        continuity.append("mood " + _clip(mood, 70))
                    goal = mind.get("current_goal") or public.get("want")
                    if goal and not public.get("hidden_master"):
                        continuity.append("goal " + _clip(goal, 150))
                    memories = format_memories(note.get("memories") or [], limit=2).replace("\n", " ")
                    if memories and "No established" not in memories:
                        continuity.append("memories " + _clip(memories, 300))
                    if continuity:
                        lines.append("NPC continuity: " + " | ".join(continuity))
            else:
                nearby_bits: list[str] = []
                for row in region_npcs[:3]:
                    name = str(row.get("npc_name") or "").strip()
                    if not name:
                        continue
                    public = (getattr(self.world, "npcs", {}) or {}).get(name, {}) or {}
                    role = public.get("role") or row.get("profession") or "local cultivator"
                    relationship = relationship_map.get(name)
                    rel = ""
                    if relationship and any(int(relationship.get(k, 0) or 0) for k in ("trust","respect","fear","affection","debt","grudge")):
                        rel = (
                            f"; rel T{int(relationship.get('trust',0)):+d}/R{int(relationship.get('respect',0)):+d}/"
                            f"F{int(relationship.get('fear',0)):+d}/A{int(relationship.get('affection',0)):+d}/"
                            f"D{int(relationship.get('debt',0)):+d}/G{int(relationship.get('grudge',0)):+d}"
                        )
                    nearby_bits.append(f"{_clip(name,75)} ({_clip(role,80)}{rel})")
                if nearby_bits:
                    lines.append("Nearby NPCs: " + " | ".join(nearby_bits))

            # Bounties/grudges are relationship pressure, not generic decoration.
            # Include them only when they touch the focused NPC/faction/current
            # jurisdiction, or the player's action explicitly asks about them.
            pressure_terms = [str(effective_focus_npc or "").casefold(), str(location).casefold()]
            pressure_terms.extend(str(v).casefold() for v in known_factions if str(v).strip())
            pressure_terms = [x for x in pressure_terms if x]
            asks_pressure = any(word in query_fold for word in ("bounty", "wanted", "crime", "grudge", "enemy", "hostile", "hunter"))

            relevant_bounties = []
            for b in (state.get("bounties") or []):
                hay = " ".join(str(b.get(k) or "").casefold() for k in ("jurisdiction", "reason"))
                if asks_pressure or (effective_focus_npc and any(term in hay for term in pressure_terms)):
                    relevant_bounties.append(b)
                if len(relevant_bounties) >= 2:
                    break
            if relevant_bounties:
                lines.append("Relevant bounties: " + " | ".join(
                    f"{int(b.get('amount',0))} in {_clip(b.get('jurisdiction','unknown jurisdiction'),90)} ({_clip(b.get('reason','reason undisclosed'),120)})"
                    for b in relevant_bounties
                ))

            relevant_grudges = []
            for g in (state.get("grudges") or []):
                hay = " ".join(str(g.get(k) or "").casefold() for k in ("holder_type", "holder_key", "reason"))
                if asks_pressure or (effective_focus_npc and any(term in hay for term in pressure_terms)):
                    relevant_grudges.append(g)
                if len(relevant_grudges) >= 2:
                    break
            if relevant_grudges:
                lines.append("Relevant grudges: " + " | ".join(
                    f"{_clip(g.get('holder_key'),90)} intensity {int(g.get('intensity',0))}/10"
                    + (f" ({_clip(g.get('reason'),120)})" if g.get('reason') else "")
                    for g in relevant_grudges
                ))

            events = state.get("events") or []
            if events:
                lines.append("Active local events: " + " | ".join(
                    f"{_clip(e.get('title') or e.get('event_type'),100)} ({_clip(e.get('event_type'),55)})"
                    for e in events[:2]
                ))

            # Pull in optional state only when the player's words make it relevant.
            if any(word in query_fold for word in ("sword", "weapon", "armor", "robe", "ring", "talisman", "equipment", "wield", "draw", "show")):
                equipped = [_clip(e.get("item_id") or e.get("name"), 80) for e in (state.get("equipment") or [])[:5] if (e.get("item_id") or e.get("name"))]
                if equipped:
                    lines.append("Relevant equipment: " + _join(equipped))
            if any(word in query_fold for word in ("manual", "technique", "scripture", "cultivation art", "skill")):
                manuals = [f"{_clip(m.get('manual_id'),80)} mastery {int(m.get('mastery',0))}/4" for m in (state.get("manuals") or [])[:5] if m.get("manual_id")]
                if manuals:
                    lines.append("Relevant manuals: " + " | ".join(manuals))
            if any(word in query_fold for word in ("beast", "pet", "mount", "companion")):
                beasts = [f"{_clip(b.get('name'),70)} ({_clip(b.get('species'),70)})" for b in (state.get("beasts") or [])[:4] if b.get("name")]
                if beasts:
                    lines.append("Relevant spirit beasts: " + " | ".join(beasts))

            deployed_array = state.get("deployed_array")
            if deployed_array:
                lines.append(f"Active formation: {_clip(deployed_array.get('name') or deployed_array.get('item_id'), 110)}; mechanics already applied by Python.")

            rag = state.get("rag")
            rag_text = str(getattr(rag, "text", "") or "")
            if rag_text:
                lines.append(rag_text)

            narration_contract = (
                "NARRATION CONTRACT: react only from supplied state/result. Do not mutate mechanics, invent rewards/relationships, "
                "move the player, or expose hidden simulator facts; the authoritative game engine remains authoritative."
            )
            lines.append(narration_contract)
            budget = profile.context_chars
            text = _fit_context_to_budget(lines, narration_contract=narration_contract, budget=budget, rag_text=rag_text)
            return NarratorSceneContext(
                text=text, game_minute=game_minute, location=location, world_name=world_name,
                retrieval_profile=profile.name, context_budget_chars=budget,
                rag_memory_count=int(getattr(rag, "memory_count", 0) or 0),
                rag_canon_count=int(getattr(rag, "canon_count", 0) or 0),
                rag_history_count=int(getattr(rag, "history_count", 0) or 0),
            )

        lines: list[str] = [
            "CANONICAL NARRATOR CONTEXT — TRUSTED READ-ONLY GAME STATE",
            f"Scene type: {_clip(scene_type, 80)}",
            f"World time: {wt.display} | canonical game-minute {game_minute}",
            f"Location: {location} | world: {world_name}",
            f"Location description: {_clip(loc.get('description', 'No description recorded.'), 520)}",
            f"Location protection: {'PROTECTED / violence cannot mechanically begin here' if loc.get('safe_zone') else 'not marked as a protected interior'}",
            f"Character: {_clip(character.get('name', 'Unnamed cultivator'), 100)} | cultivation style {_clip(character.get('path', 'Unknown'), 90)} | spiritual root {_clip(character.get('spiritual_root', 'Unknown'), 80)}",
            f"Public origin: {_clip(character.get('origin', 'No origin recorded.'), 260)}",
        ]
        if character.get('concept'):
            lines.append(f"Declared personal Dao / goal: {_clip(character.get('concept'), 360)}")

        try:
            life = await self.simulator.engine.action("character.lifespan", user_id, {})
            age_years = float(life.get("age_years", 0.0))
            ageless = bool(life.get("ageless", False))
            total_years = int(life.get("total_years", 0))
            life_text = (
                f"{age_years:.1f} years old; ageless by current cultivation"
                if ageless
                else f"{age_years:.1f} years old; lifespan ceiling {total_years} years"
            )
            lines.append(f"Life state: {life_text}")
        except Exception:
            pass

        lines.append(
            f"Karma: {karma_label(int(character.get('karma_score', 0)))} ({int(character.get('karma_score', 0))}) | "
            f"Fate reserve: {int((state.get('fate') or {}).get('points', 0))}/9"
        )

        family = state.get("family")
        if family:
            tradition = str(family.get("archetype", "family")).replace("_", " ").title()
            lines.append(
                "Birth family: "
                f"{_clip(family.get('family_name'), 100)} | tradition {tradition} | {family_tier_name(int(family.get('tier', 1)))} | "
                f"wealth {family.get('wealth', 0)}, influence {family.get('influence', 0)}, stability {family.get('stability', 0)} | "
                f"ancestral home {_clip(family.get('location', character.get('location', 'Unknown')), 100)} | "
                f"head {family.get('head_title', 'Family Head')} {_clip(family.get('head_name', 'Unknown'), 80)}"
            )

        aptitudes = state.get("aptitudes") or {}
        aptitude_bits: list[str] = []
        for key, label in (("root", "root"), ("bloodline", "bloodline"), ("physique", "physique")):
            value = aptitudes.get(key)
            if isinstance(value, dict):
                name = value.get("name") or value.get("id") or value.get("type")
            else:
                name = value
            if name:
                aptitude_bits.append(f"{label} {_clip(name, 90)}")
        if aptitude_bits:
            lines.append("Known innate profile: " + "; ".join(aptitude_bits))

        social = state.get("social") or {}
        if social:
            social_line = (
                f"Social/Dao state: face {social.get('face', 0)}, Dao Heart {social.get('dao_heart', 50)}/100, "
                f"stability {social.get('dao_stability', 100)}/100"
            )
            if social.get("vow"):
                social_line += f" | vow: {_clip(social.get('vow'), 180)}"
            if social.get("obsession"):
                social_line += f" | obsession: {_clip(social.get('obsession'), 180)}"
            lines.append(social_line)

        lines.append("Sect/lineage: " + _clip(lineage_context, 900))
        membership = state.get("membership")
        if membership:
            sect_name = str(membership.get("sect_name") or "")
            sect_sim = state.get("sect_sim")
            sect_line = f"Public sect membership: {sect_name} | rank {membership.get('rank_name', 'disciple')}"
            if sect_sim:
                sect_line += (
                    f" | influence {sect_sim.get('influence', 0)}/100, cohesion {sect_sim.get('cohesion', 0)}/100, "
                    f"resources {sect_sim.get('resources', 0)}/100, policy {_clip(sect_sim.get('leader_policy', 'Balanced'), 80)}"
                )
            lines.append(sect_line)

        effects = state.get("effects") or []
        effect_names = [_clip(e.get("name") or e.get("effect_key"), 90) for e in effects[:8] if (e.get("name") or e.get("effect_key"))]
        conditions = state.get("conditions") or []
        condition_names = [_clip(c.get("name") or c.get("condition_key"), 90) for c in conditions[:6] if (c.get("name") or c.get("condition_key"))]
        if effect_names:
            lines.append("Active effects: " + _join(effect_names))
        if condition_names:
            lines.append("Active conditions: " + _join(condition_names))

        equipment = state.get("equipment") or []
        equipped = [_clip(e.get("item_id") or e.get("name"), 90) for e in equipment[:8] if (e.get("item_id") or e.get("name"))]
        if equipped:
            lines.append("Equipped items: " + _join(equipped))

        manuals = state.get("manuals") or []
        if manuals:
            known_manuals = [
                f"{_clip(m.get('manual_id'), 90)} (mastery {int(m.get('mastery', 0))}/4)"
                for m in manuals[:8] if m.get("manual_id")
            ]
            if known_manuals:
                lines.append("Known cultivation manuals: " + " | ".join(known_manuals))

        beasts = state.get("beasts") or []
        visible_beasts: list[str] = []
        for beast in beasts[:5]:
            marker = "active companion" if beast.get("active") else "bonded"
            visible_beasts.append(
                f"{_clip(beast.get('name'), 70)} ({_clip(beast.get('species'), 70)}, {marker}, loyalty {beast.get('loyalty', 0)}/100)"
            )
        if visible_beasts:
            lines.append("Spirit beasts: " + " | ".join(visible_beasts))

        party = state.get("party")
        if party:
            lines.append(f"Party: {_clip(party.get('name'), 100)} | {len(party.get('members') or [])} canonical member(s)")

        battle = state.get("battle")
        if battle:
            lines.append(
                f"Active battle: {_clip(battle.get('npc_name') or battle.get('target_name') or 'opponent', 100)} | "
                f"status {_clip(battle.get('status', 'active'), 50)}. Do not resolve additional combat outside fixed rolls."
            )

        bounties = state.get("bounties") or []
        if bounties:
            items = [f"{b.get('amount', 0)} ({_clip(b.get('reason', 'reason undisclosed'), 90)})" for b in bounties[:3]]
            lines.append("Known active bounties on player: " + " | ".join(items))
        grudges = state.get("grudges") or []
        if grudges:
            items = [f"{_clip(g.get('holder_key'), 90)} intensity {g.get('intensity', 0)}/10" for g in grudges[:3]]
            lines.append("Known active grudges: " + " | ".join(items))

        era = state.get("era")
        if era:
            lines.append(f"Current world era: {_clip(era.get('name'), 100)} — {_clip(era.get('description'), 260)}")

        region = state.get("region")
        if region:
            lines.append(
                "Regional simulation: "
                f"population {int(region.get('population', 0)):,}; prosperity {region.get('prosperity', 0)}/100; "
                f"security {region.get('security', 0)}/100; unrest {region.get('unrest', 0)}/100; "
                f"spirit resources {region.get('spirit_resources', 0)}/100; food {region.get('food_supply', 0)}/100"
            )
            incidents = [_clip(e.get("event_text"), 180) for e in (region.get("events") or [])[:3] if e.get("event_text")]
            if incidents:
                lines.append("Recent regional incidents: " + " | ".join(incidents))

            # Player-safe NPC presence. Never expose realm_index/phase from the
            # civilization simulator: those fields can reveal true hidden-master
            # power that Spiritual Sense has not uncovered.
            npc_bits: list[str] = []
            relationship_map = {str(r.get("npc_name")): r for r in (state.get("npc_relationships") or [])}
            for row in (region.get("npcs") or [])[: profile.nearby_npc_limit]:
                name = str(row.get("npc_name") or "").strip()
                if not name:
                    continue
                public = self.world.npcs.get(name, {})
                role = public.get("role") or row.get("profession") or "local cultivator"
                activity = row.get("activity") or "present nearby"
                relationship = relationship_map.get(name)
                relation_text = ""
                if relationship:
                    relation_text = (
                        f"; relationship trust {int(relationship.get('trust',0)):+d}, "
                        f"respect {int(relationship.get('respect',0)):+d}, fear {int(relationship.get('fear',0)):+d}, "
                        f"debt {int(relationship.get('debt',0)):+d}, grudge {int(relationship.get('grudge',0)):+d}"
                    )
                life_bits=[]
                if row.get("sect_rank"): life_bits.append(f"rank {row.get('sect_rank')}")
                if int(row.get("injury_severity") or 0) >= 3 and row.get("injury"): life_bits.append(f"visibly affected by {row.get('injury')}")
                if row.get("spouse_name"): life_bits.append(f"married to {row.get('spouse_name')}")
                life_text=("; "+", ".join(life_bits)) if life_bits else ""
                npc_bits.append(f"{_clip(name, 80)} — {_clip(role, 100)}; {_clip(activity, 110)}{life_text}{relation_text}")
            if npc_bits:
                lines.append("Nearby publicly observable NPCs: " + " | ".join(npc_bits))

            director_bits: list[str] = []
            director_state = state.get("npc_director_notes") or {}
            for name, note in list(director_state.items())[: profile.director_npc_limit]:
                public = self.world.npcs.get(name, {})
                mind = dict(note.get("mind") or {})
                mood = public_mood_hint(str(mind.get("mood") or "")) or "not outwardly obvious"
                # Hidden masters keep their private objective opaque in free-form
                # scene context.  Their public personality and actual prior player
                # exchanges are still useful for continuity.
                if public.get("hidden_master"):
                    goal = "Continue their established private affairs without exposing concealed identity or power."
                else:
                    goal = _clip(mind.get("current_goal") or public.get("want") or "Continue established affairs.", 180)
                memory_text = format_memories(note.get("memories") or [], limit=2).replace("\n", " ")
                life = dict(note.get("life") or {})
                life_text = (
                    f"life-state rank {life.get('sect_rank') or 'unranked'}, health {life.get('health') if life.get('health') is not None else 'unknown'}, "
                    f"injury {life.get('injury') or 'none'}, relationship {life.get('relationship_status') or 'single'}"
                    + (f" to {life.get('spouse_name')}" if life.get('spouse_name') else "")
                    + f", children {int(life.get('children_count') or 0)}; "
                )
                director_bits.append(
                    f"{_clip(name,80)}: personality {_clip(public.get('personality'),150)}; "
                    f"speech {_clip(public.get('speech'),130)}; mood {mood}; {life_text}private behavior goal {goal}; "
                    f"player-specific memories {memory_text}"
                )
            if director_bits:
                lines.append(
                    "NPC DIRECTOR NOTES (behavior guidance only; never quote these notes or expose private goals as facts): "
                    + " | ".join(director_bits)
                )

        events = state.get("events") or []
        if events:
            event_bits = [
                f"{_clip(e.get('title') or e.get('event_type'), 110)} ({_clip(e.get('event_type'), 60)})"
                for e in events[:5]
            ]
            lines.append("Active canonical events at this location: " + " | ".join(event_bits))

        if abode:
            property_type = str(abode.get("property_type") or "homestead").replace("_", " ").title()
            lines.append(
                f"Private player-owned location: {_clip(abode.get('name'), 100)} | type {property_type} | "
                f"entrance {_clip(abode.get('base_location'), 120)} | facilities cultivation {abode.get('cultivation_level', 0)}, "
                f"alchemy {abode.get('alchemy_level', 0)}, forge {abode.get('forge_level', 0)}, "
                f"formation {abode.get('formation_level', 0)}, defense {abode.get('defense_level', 0)}, "
                f"storage {abode.get('storage_level', 0)}, herb garden {abode.get('herb_garden_level', 0)}, "
                f"beast pen {abode.get('beast_pen_level', 0)}, merchant {abode.get('merchant_level', 0)}. "
                "This is a private property scene, not a public city channel."
            )
        if sect_abode:
            lines.append(
                f"Private sect abode: {_clip(sect_abode.get('name'), 100)} | sect {_clip(sect_abode.get('sect_name'), 100)} | "
                f"gate {_clip(sect_abode.get('base_location'), 120)}. This is a private residence, not a public realm-capital scene."
            )
        if personal_world:
            lines.append("Personal-world rule: local laws may only change through canonical /innerworld mechanics; narration cannot invent new world laws.")

        deployed_array = state.get("deployed_array")
        if deployed_array:
            lines.append(
                f"Active location formation: {_clip(deployed_array.get('name') or deployed_array.get('item_id'), 120)}. "
                "Its mechanical modifiers are already applied by Python."
            )

        hub_info = realm_hub_by_location(location)
        if hub_info:
            lines.append(f"Realm capital: canonical public gathering hub for {hub_info[0]}; public meetings and social scenes are expected here.")

        rag = state.get("rag")
        rag_text = str(getattr(rag, "text", "") or "")
        if rag_text:
            lines.append(rag_text)

        narration_contract = (
            "NARRATION CONTRACT: describe only consequences compatible with the supplied fixed result. "
            "Never grant rewards, change stats/inventory/relationships, teleport the player, advance cultivation, create a binding contract, "
            "or expose hidden simulator facts. The authoritative game engine remains the sole authority for mechanical state changes."
        )
        lines.append(narration_contract)

        budget = profile.context_chars
        text = _fit_context_to_budget(
            lines, narration_contract=narration_contract, budget=budget, rag_text=rag_text,
        )
        return NarratorSceneContext(
            text=text, game_minute=game_minute, location=location, world_name=world_name,
            retrieval_profile=profile.name, context_budget_chars=budget,
            rag_memory_count=int(getattr(rag, "memory_count", 0) or 0),
            rag_canon_count=int(getattr(rag, "canon_count", 0) or 0),
            rag_history_count=int(getattr(rag, "history_count", 0) or 0),
        )
