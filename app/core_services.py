from __future__ import annotations

"""Core service layer introduced for the 0.7 architecture.

Discord callbacks should increasingly delegate state decisions to these services instead
of duplicating game rules.  The module intentionally has no discord.py dependency so
it can later move behind a Go/Rust service boundary without changing game semantics.
"""

from dataclasses import dataclass
from typing import Any



@dataclass(frozen=True, slots=True)
class SceneState:
    user_id: int
    physical_location: str
    scene_type: str
    scene_key: str
    scene_label: str
    channel_id: int | None = None
    metadata: dict[str, Any] | None = None


class LocationSceneService:
    """Authoritative physical-location + active-scene resolver.

    Physical location stays on the character.  Active scene is persisted separately so
    private properties, sect residences and expeditions never masquerade as map nodes.
    """

    def __init__(self, db: Any, *, engine: Any):
        self.db = db
        self.engine = engine

    async def current(self, user_id: int, *, guild_id: int | None = None) -> SceneState | None:
        character = await self.db.get_character(int(user_id))
        if not character:
            return None
        raw_location = str(character.get("location") or "Unknown")

        # Private interiors are represented by canonical location keys in the legacy
        # character row, but v0.7 exposes their *entrance/base location* as physical
        # location and the interior itself as the active scene. This preserves all
        # existing mechanics while giving new code the correct two-layer model.
        abode = await self.db.get_abode_by_location(raw_location)
        if abode:
            physical = str(abode.get("base_location") or raw_location)
            state = SceneState(
                int(user_id), physical, "player_property", raw_location,
                str(abode.get("name") or "Player Property"),
                int(abode["thread_id"]) if abode.get("thread_id") else None,
                {"owner_user_id": int(abode.get("user_id", user_id)), "property_type": abode.get("property_type")},
            )
            await self.set(state)
            return state

        sect_abode = await self.db.get_sect_abode_by_location(raw_location)
        if sect_abode:
            physical = str(sect_abode.get("base_location") or raw_location)
            state = SceneState(
                int(user_id), physical, "sect_abode", raw_location,
                str(sect_abode.get("name") or "Sect Residence"),
                int(sect_abode["thread_id"]) if sect_abode.get("thread_id") else None,
                {"sect_name": sect_abode.get("sect_name")},
            )
            await self.set(state)
            return state

        physical = raw_location
        stored = await self.db.get_player_scene_state(int(user_id))
        if stored and str(stored.get("physical_location") or "") == physical:
            return SceneState(
                user_id=int(user_id),
                physical_location=physical,
                scene_type=str(stored.get("scene_type") or "world"),
                scene_key=str(stored.get("scene_key") or physical),
                scene_label=str(stored.get("scene_label") or physical),
                channel_id=int(stored["channel_id"]) if stored.get("channel_id") is not None else None,
                metadata=dict(stored.get("metadata") or {}),
            )

        # The character is simply in a world map node. An expedition thread is a
        # journal/scene transport, not a separate physical destination.
        thread_id = None
        if guild_id is not None:
            expedition = await self.db.get_expedition_thread(int(guild_id), int(user_id))
            if expedition and str(expedition.get("last_location") or "") == physical:
                thread_id = int(expedition.get("thread_id") or 0) or None
        state = SceneState(int(user_id), physical, "world", physical, physical, thread_id, {})
        await self.set(state)
        return state

    async def set(self, state: SceneState) -> None:
        await self.engine.action(
            "scene.transition",
            int(state.user_id),
            {
                "physical_location": state.physical_location,
                "scene_type": state.scene_type,
                "scene_key": state.scene_key,
                "scene_label": state.scene_label,
                "channel_id": state.channel_id,
                "metadata": state.metadata or {},
            },
        )

    async def enter_scene(
        self,
        user_id: int,
        *,
        physical_location: str,
        scene_type: str,
        scene_key: str,
        scene_label: str,
        channel_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SceneState:
        payload = await self.engine.action(
            "scene.transition",
            int(user_id),
            {
                "physical_location": str(physical_location),
                "scene_type": str(scene_type),
                "scene_key": str(scene_key),
                "scene_label": str(scene_label),
                "channel_id": int(channel_id) if channel_id is not None else None,
                "metadata": metadata or {},
            },
        )
        return SceneState(
            int(user_id),
            str(payload["physical_location"]),
            str(payload["scene_type"]),
            str(payload["scene_key"]),
            str(payload["scene_label"]),
            int(payload["channel_id"]) if payload.get("channel_id") is not None else None,
            dict(payload.get("metadata") or {}),
        )

    async def reset_to_world(self, user_id: int, *, guild_id: int | None = None) -> SceneState | None:
        character = await self.db.get_character(int(user_id))
        if not character:
            return None
        physical = str(character.get("location") or "Unknown")
        thread_id = None
        if guild_id is not None:
            expedition = await self.db.get_expedition_thread(int(guild_id), int(user_id))
            if expedition:
                thread_id = int(expedition.get("thread_id") or 0) or None
        state = SceneState(int(user_id), physical, "world", physical, physical, thread_id, {})
        await self.set(state)
        return state


class NPCRelationshipService:
    """Bounded mechanical relationship state; narration never owns these numbers."""

    DIMENSIONS = ("trust", "respect", "fear", "affection", "debt", "grudge")

    def __init__(self, db: Any, *, engine: Any):
        self.db = db
        self.engine = engine

    async def get(self, user_id: int, npc_name: str) -> dict[str, Any]:
        return await self.db.get_npc_relationship(int(user_id), str(npc_name))

    async def record_encounter(
        self,
        user_id: int,
        npc_name: str,
        *,
        summary: str,
        deltas: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        safe = {key: int((deltas or {}).get(key, 0)) for key in self.DIMENSIONS}
        return dict(await self.engine.action(
            "relationship.update",
            int(user_id),
            {"npc_name": str(npc_name), "summary": str(summary)[:800], **safe},
        ))

    @staticmethod
    def public_label(row: dict[str, Any]) -> str:
        trust = int(row.get("trust", 0)); respect = int(row.get("respect", 0))
        fear = int(row.get("fear", 0)); grudge = int(row.get("grudge", 0))
        if grudge >= 50:
            return "Hostile"
        if fear >= 50:
            return "Fearful"
        if trust >= 60 and respect >= 40:
            return "Trusted ally"
        if trust >= 25 or respect >= 25:
            return "Favorable"
        if trust <= -30:
            return "Distrustful"
        return "Uncertain"


class QuestService:
    """Generic quest/objective engine shared by sect, NPC, family and world content."""

    def __init__(self, db: Any, definitions: dict[str, dict[str, Any]], *, engine: Any):
        self.db = db
        self.definitions = definitions
        self.engine = engine

    async def accept(self, user_id: int, quest_key: str, *, game_minute: int = 0) -> dict[str, Any]:
        if quest_key not in self.definitions:
            raise ValueError("Unknown quest.")
        return await self.db.accept_quest(int(user_id), quest_key, game_minute=int(game_minute))

    async def progress(
        self,
        user_id: int,
        objective_type: str,
        *,
        amount: int = 1,
        target: str | None = None,
        game_minute: int = 0,
    ) -> list[dict[str, Any]]:
        changed: list[dict[str, Any]] = []
        for row in await self.db.list_character_quests(int(user_id), status="active"):
            definition = self.definitions.get(str(row.get("quest_key")))
            if not definition:
                continue
            transition = dict(await self.engine.action(
                "quest.progress",
                int(user_id),
                {
                    "quest_key": str(row["quest_key"]),
                    "objectives": list(definition.get("objectives", [])),
                    "objective_type": str(objective_type),
                    "amount": int(amount),
                    "target": target,
                    "game_minute": int(game_minute),
                },
            ))
            if transition.get("touched"):
                refreshed = await self.db.list_character_quests(int(user_id))
                current = next((r for r in refreshed if str(r["quest_key"]) == str(row["quest_key"])), None)
                if current is not None:
                    changed.append(current)
        return changed

    async def available(self, user_id: int) -> list[dict[str, Any]]:
        existing = {str(r["quest_key"]): r for r in await self.db.list_character_quests(int(user_id))}
        result = []
        for key, definition in self.definitions.items():
            if key in existing:
                continue
            result.append({"quest_key": key, **definition})
        return result


class ExplorationService:
    """Discord-free exploration coordinator.

    The adapter decides which channel/thread to use; this service owns the persisted
    scene transition so callbacks do not need to understand scene-state storage.
    """

    def __init__(self, scenes: LocationSceneService):
        self.scenes = scenes

    async def enter_expedition(
        self,
        user_id: int,
        *,
        physical_location: str,
        channel_id: int,
        guild_id: int | None = None,
    ) -> SceneState:
        return await self.scenes.enter_scene(
            int(user_id),
            physical_location=str(physical_location),
            scene_type="expedition",
            scene_key=f"expedition:{int(user_id)}",
            scene_label=f"{physical_location} Expedition",
            channel_id=int(channel_id),
            metadata={"guild_id": int(guild_id) if guild_id is not None else None},
        )

    async def reset_to_world(self, user_id: int, *, guild_id: int | None = None) -> SceneState | None:
        return await self.scenes.reset_to_world(int(user_id), guild_id=guild_id)


class CombatService:
    """Mechanical combat writes owned by the authoritative Go engine."""

    def __init__(self, db: Any, *, engine: Any):
        self.db = db
        self.engine = engine

    async def apply_damage(self, battle_id: int, user_id: int, damage: int) -> dict[str, int]:
        return dict(await self.engine.action(
            "combat.apply_damage", int(user_id),
            {"battle_id": int(battle_id), "damage": max(0, int(damage))},
        ))


class CultivationService:
    """Cultivation/reward mutations owned by the authoritative Go engine."""

    def __init__(self, db: Any, *, engine: Any):
        self.db = db
        self.engine = engine

    async def reward(self, user_id: int, **changes: Any) -> int:
        result = dict(await self.engine.action("cultivation.reward", int(user_id), changes))
        return int(result.get("cultivation_awarded", 0))


class SectService:
    """Sect membership orchestration without Discord-specific objects."""

    def __init__(self, db: Any):
        self.db = db

    async def admit_outer_disciple(self, user_id: int, sect_name: str) -> None:
        await self.db.set_sect_membership(
            int(user_id), sect_name=str(sect_name), rank_name="Outer Disciple", rank_level=10,
        )
        await self.db.adjust_reputation(
            int(user_id), str(sect_name), 5, reason="Passed sect entrance trial",
        )
