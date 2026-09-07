from __future__ import annotations

import time

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
    """Generic quest/objective engine shared by sect, NPC, family and world content.

    The catalog is the static definitions in app/rules/quests.py plus every
    Quest Forge definition a GM has approved (quest_definitions table, v0.20.6).
    Accepting is `commission.accept` and progress is `quest.progress`; since
    v0.22.2 the declared rewards travel with each progress report and the
    engine pays them **inside the transaction that completes the quest**. This
    class grants nothing itself, so a forged quest can never write a table and
    a completed quest can never be left unpaid by a call that went missing
    between the two.
    """

    CATALOG_TTL_SECONDS = 15.0

    def __init__(self, db: Any, definitions: dict[str, dict[str, Any]], *, engine: Any):
        self.db = db
        self.definitions = definitions
        self.engine = engine
        self._forged: dict[str, dict[str, Any]] = {}
        self._forged_loaded_at = 0.0

    async def catalog(self, *, refresh: bool = False) -> dict[str, dict[str, Any]]:
        """Static definitions plus approved forged ones, forged never shadowing static."""
        now = time.time()
        if refresh or now - self._forged_loaded_at > self.CATALOG_TTL_SECONDS:
            forged: dict[str, dict[str, Any]] = {}
            lister = getattr(self.db, "list_quest_definitions", None)
            if lister is not None:
                try:
                    for row in await lister("approved"):
                        forged[str(row["quest_key"])] = {
                            "title": row["title"], "description": row.get("description", ""),
                            "source_type": row.get("source_type", "forge"), "source_key": row.get("source_key", ""),
                            "objectives": list(row.get("objectives") or []), "rewards": dict(row.get("rewards") or {}),
                            # Commission fields (v0.22.0). `giver_npc` is what makes a
                            # definition a commission; `owner_user_id` is what makes it
                            # one player's, and is the only visibility rule there is.
                            "giver_npc": str(row.get("giver_npc") or ""),
                            "owner_user_id": row.get("owner_user_id"),
                            "tier": int(row.get("tier", 1) or 1),
                            "realm_band": str(row.get("realm_band") or ""),
                            "deadline_game_minutes": int(row.get("deadline_game_minutes", 0) or 0),
                            "variants": list(row.get("variants") or []),
                            "requires_sect": str(row.get("requires_sect") or ""),
                            "reward_visibility": str(row.get("reward_visibility") or "shown"),
                            "boast": str(row.get("boast") or ""),
                        }
                except Exception:
                    forged = self._forged  # keep the last good catalog rather than dropping quests mid-play
            self._forged = forged
            self._forged_loaded_at = now
        merged = dict(self._forged)
        merged.update(self.definitions)
        return merged

    async def definition(self, quest_key: str) -> dict[str, Any] | None:
        return (await self.catalog()).get(str(quest_key))

    async def accept(self, user_id: int, quest_key: str, *, action_id: str, variant_index: int = 0) -> dict[str, Any]:
        """Accepting is an engine write (v0.22.0, `commission.accept`).

        The one-at-a-time check, the deadline and the locked terms have to be
        decided in the same transaction as the insert, so this no longer writes
        the row from Python. Every quest goes through the same path - an
        ordinary catalog quest is simply a commission definition with no giver,
        which the engine accepts with no deadline and no standing effect."""
        if await self.definition(quest_key) is None:
            raise ValueError("Unknown quest.")
        envelope = await self.engine.authoritative_action(
            "commission.accept", int(user_id),
            {"quest_key": str(quest_key), "variant_index": int(variant_index)},
            action_id=str(action_id),
        )
        accepted = dict(envelope.get("result") or {})
        rows = await self.db.list_character_quests(int(user_id))
        row = next((r for r in rows if str(r["quest_key"]) == str(quest_key)), None)
        if row is None:  # the engine wrote it; a missing row here is a read race, not an accept failure
            return accepted
        merged = dict(row)
        merged.update({k: v for k, v in accepted.items() if k in ("title", "giver_npc", "variant_label", "rewards", "deadline_game_minutes")})
        return merged

    async def progress(
        self,
        user_id: int,
        objective_type: str,
        *,
        amount: int = 1,
        target: str | None = None,
        game_minute: int = 0,
    ) -> list[dict[str, Any]]:
        """Report one objective event. Returns the quest rows it touched; a row
        that just completed carries `just_completed=True` and `rewards_granted`."""
        changed: list[dict[str, Any]] = []
        catalog = await self.catalog()
        for row in await self.db.list_character_quests(int(user_id), status="active"):
            definition = catalog.get(str(row.get("quest_key")))
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
                    # v0.22.2: the engine pays on the same commit that
                    # completes, so the declared rewards travel with every
                    # report rather than in a second call that can be lost.
                    "rewards": dict(definition.get("rewards") or {}),
                },
            ))
            if not transition.get("touched"):
                continue
            refreshed = await self.db.list_character_quests(int(user_id))
            current = next((r for r in refreshed if str(r["quest_key"]) == str(row["quest_key"])), None)
            if current is None:
                continue
            current = dict(current)
            current["title"] = definition.get("title", current["quest_key"])
            if transition.get("complete"):
                current["just_completed"] = True
                resolved = dict(transition.get("commission") or {})
                if resolved:
                    # A commission resolves and pays inside the transaction
                    # that completed it (commission_actions.go).
                    current["commission"] = resolved
                    current["rewards_granted"] = dict(resolved.get("rewards_granted") or {})
                else:
                    # And so, since v0.22.2, does an ordinary quest. Python
                    # grants nothing here: completion and payment used to be
                    # two commits, and anything that interrupted the gap left
                    # a completed quest that could never be paid, because the
                    # next progress report only looks at active ones.
                    current["rewards_granted"] = dict(transition.get("rewards_granted") or {})
            changed.append(current)
        return changed

    @staticmethod
    def visible_to(definition: dict[str, Any], user_id: int) -> bool:
        """A pooled definition is public content; an invented one belongs to
        one player and is never listed, described or named for anyone else."""
        owner = definition.get("owner_user_id")
        return owner in (None, "") or int(owner) == int(user_id)

    async def visible_catalog(self, user_id: int) -> dict[str, dict[str, Any]]:
        return {k: v for k, v in (await self.catalog()).items() if self.visible_to(v, int(user_id))}

    async def available(self, user_id: int) -> list[dict[str, Any]]:
        """Quests the player may accept from the journal. Commissions are not
        among them: a commission is something a giver offers you in person, so
        it is reachable through him and through nothing else."""
        existing = {str(r["quest_key"]): r for r in await self.db.list_character_quests(int(user_id))}
        result = []
        for key, definition in (await self.visible_catalog(int(user_id))).items():
            if key in existing or str(definition.get("giver_npc") or ""):
                continue
            result.append({"quest_key": key, **definition})
        return result


class CommissionService:
    """The giver's side of a commission (v0.22.0, docs/COMMISSIONS_DESIGN.md).

    Reads engine facts, runs the pure ladder in app/rules/commissions.py, and
    calls one of two engine actions. It decides nothing: which commission is
    offered is a deterministic function of standing, realm and the pool; what
    an outcome costs is the engine's; what the Steward *says* is the narrator's
    and is handed the block this builds, never the other way round.
    """

    def __init__(self, db: Any, quests: "QuestService", *, engine: Any, world: Any) -> None:
        self.db = db
        self.quests = quests
        self.engine = engine
        self.world = world

    def givers(self) -> dict[str, dict[str, Any]]:
        return dict(getattr(self.world, "data", {}).get("commission_givers") or {})

    def is_giver(self, npc_name: str) -> bool:
        return str(npc_name) in self.givers()

    def item_names(self) -> dict[str, str]:
        return {str(k): str(v.get("name", k)) for k, v in dict(getattr(self.world, "items", {}) or {}).items()}

    async def held(self, user_id: int) -> dict[str, Any] | None:
        """The player's active commission, with progress counted off the
        definition's objectives. `None` when the slot is free."""
        for row in await self.db.list_character_quests(int(user_id), status="active"):
            if not int(row.get("commission", 0) or 0):
                continue
            definition = dict(await self.quests.definition(str(row["quest_key"])) or {})
            objectives = list(definition.get("objectives") or [])
            progress = dict(row.get("progress") or {})
            done = sum(1 for o in objectives if int(progress.get(str(o.get("id")), 0)) >= max(1, int(o.get("count", 1) or 1)))
            return {
                **row,
                "title": definition.get("title", row["quest_key"]),
                "giver_npc": str(definition.get("giver_npc") or ""),
                "objectives": objectives,
                "objectives_done": done,
                "objectives_total": len(objectives),
            }
        return None

    async def player_sect(self, user_id: int) -> str:
        membership = await self.db.get_sect_membership(int(user_id))
        return str((membership or {}).get("sect_name") or "")

    async def offer_inputs(self, user_id: int, giver: str, *, realm_index: int, game_minute: int) -> dict[str, Any]:
        """Everything the pure ladder needs, read once.

        The ladder itself lives in app/rules/commissions.py and is called by
        the bot layer, not from here: `ops` sits beside `rules` in the layering
        (tests/python/unit/test_app_layout.py) and may not import it. That is
        not a technicality - it is what keeps the decision testable without a
        database and this class free of game rules."""
        relationship = await self.db.get_npc_relationship(int(user_id), str(giver))
        held = await self.held(int(user_id))
        cooldown_until = int((relationship or {}).get("commission_cooldown_until_game_minute", 0) or 0)
        pool: list[dict[str, Any]] = []
        lister = getattr(self.db, "list_commission_definitions", None)
        if lister is not None:
            pool = await lister(str(giver), user_id=int(user_id))
        taken = {str(r["quest_key"]) for r in await self.db.list_character_quests(int(user_id))}
        if held is not None:
            remaining = 0
            if held.get("deadline_game_minute"):
                remaining = max(0, int(held["deadline_game_minute"]) - int(game_minute))
            held = {**held, "deadline_game_minutes_remaining": remaining}
        return {
            "giver": str(giver),
            "relationship": relationship,
            "held_commission": held,
            "cooldown_game_minutes": max(0, cooldown_until - int(game_minute)),
            "pool": pool,
            "taken_keys": taken,
            "realm_index": int(realm_index),
            "user_id": int(user_id),
            "game_minute": int(game_minute),
            "player_sect": await self.player_sect(int(user_id)),
        }

    async def accept(self, user_id: int, quest_key: str, *, variant_index: int, action_id: str) -> dict[str, Any]:
        return await self.quests.accept(int(user_id), str(quest_key), action_id=str(action_id), variant_index=int(variant_index))

    async def resolve(self, user_id: int, outcome: str, *, quest_key: str = "", action_id: str,
                      admin_retire: bool = False) -> dict[str, Any]:
        """Abandon, GM retire, or (rarely, from the dashboard) a manual close.
        Completion happens inside `quest.progress`, not here."""
        envelope = await self.engine.authoritative_action(
            "commission.resolve", int(user_id),
            {"quest_key": str(quest_key), "outcome": str(outcome), "admin_retire": bool(admin_retire)},
            action_id=str(action_id),
        )
        return dict(envelope.get("result") or {})


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
        # Legacy compatibility path. New battle exchanges use turn(), which owns the
        # counter-roll and damage mutation atomically in Go.
        return dict(await self.engine.action(
            "combat.apply_damage", int(user_id),
            {"battle_id": int(battle_id), "damage": max(0, int(damage))},
        ))

    async def start(
        self, user_id: int, *, kind: str, npc_name: str, source: str, action_id: str,
        npc_realm_index: int = 0, npc_stage: int = 1, severity: int = 0, target_key: str = "",
    ) -> dict[str, Any]:
        """Open a new battle. Go owns the opponent's starting HP/realm/stage curve
        and the player's HP snapshot (read from the caller's own canonical
        characters row) - Python only identifies *which* opponent and *why*.
        """
        return await self.engine.authoritative_action(
            "combat.start", int(user_id),
            {
                "kind": str(kind),
                "npc_name": str(npc_name),
                "npc_realm_index": int(npc_realm_index),
                "npc_stage": int(npc_stage),
                "severity": int(severity),
                "source": str(source),
                "target_key": str(target_key),
            },
            action_id=str(action_id),
        )

    async def turn(
        self, user_id: int, *, battle_id: int, style: str, game_minute: int,
        minutes_per_year: int, base_samsara_years: int, max_wait_seconds: int,
        action_id: str, action: str = "",
    ) -> dict[str, Any]:
        return await self.engine.authoritative_action(
            "combat.turn", int(user_id),
            {
                "battle_id": int(battle_id),
                "style": str(style),
                "action": str(action),
                
                "minutes_per_year": int(minutes_per_year),
                "base_samsara_years": int(base_samsara_years),
                "max_wait_seconds": int(max_wait_seconds),
            },
            action_id=str(action_id),
        )

    async def technique(
        self, user_id: int, *, battle_id: int, technique: str, game_minute: int,
        minutes_per_year: int, base_samsara_years: int, max_wait_seconds: int,
        action_id: str,
    ) -> dict[str, Any]:
        return await self.engine.authoritative_action(
            "combat.technique", int(user_id),
            {
                "battle_id": int(battle_id),
                "technique": str(technique),
                "minutes_per_year": int(minutes_per_year),
                "base_samsara_years": int(base_samsara_years),
                "max_wait_seconds": int(max_wait_seconds),
            },
            action_id=str(action_id),
        )

    async def recovery_item(
        self, user_id: int, *, battle_id: int, item_id: str, game_minute: int, action_id: str,
    ) -> dict[str, Any]:
        return await self.engine.authoritative_action(
            "combat.recovery_item", int(user_id),
            {"battle_id": int(battle_id), "item_id": str(item_id)},
            action_id=str(action_id),
        )

    async def finalize(
        self, user_id: int, *, battle_id: int, outcome: str, game_minute: int, action_id: str,
    ) -> dict[str, Any]:
        return await self.engine.authoritative_action(
            "combat.finalize", int(user_id),
            {"battle_id": int(battle_id), "outcome": str(outcome)},
            action_id=str(action_id),
        )



