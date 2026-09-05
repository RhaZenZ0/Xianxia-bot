"""Service singletons shared by every command module.

Phase 2 of the main.py split (v0.19.34, docs/MAIN_SPLIT_PLAN.md). These objects
were constructed at the top of main.py and read from nearly every command block
- SIM by nine of them, GUILD by fifteen - which is why the modules already split
out (family, sect) had to reach back into main.py with call-time imports. Now
they live below main.py, where any module can import them normally.

Why a separate module rather than growing runtime.py: constructing NARRATOR,
AI_ROUTER and SIM pulls in app.narrator, app.ai_router and app.simulation.
runtime.py's value is that it is light and cycle-free; this module imports it
and it never imports this one.

Definition order below is exactly the order these had in main.py (the
DefinitionOrderTests guard in tests/python/unit/test_bot_module_split.py
covers this file), and nothing here reads from any command module.
"""
from __future__ import annotations

import discord
from discord import app_commands

from ..ai.ai_router import AITaskRouter
from ..ops.core_services import (
    CombatService,
    ExplorationService,
    LocationSceneService,
    NPCRelationshipService,
    QuestService,
)
from ..ai.narrator import Narrator
from ..ai.narrator_context import NarratorContextBuilder
from ..ops.operations import AlertDispatcher
from ..ops.performance import AsyncWorkQueue
from ..rules.quests import QUEST_DEFINITIONS
from ..simulation import WorldSimulator
from .runtime import DB, ENGINE, PLAYER_PROPERTY_TYPES, SETTINGS, WORLD

GUILD = discord.Object(id=SETTINGS.guild_id)

SCENES = LocationSceneService(DB, engine=ENGINE)

NPC_RELATIONSHIPS = NPCRelationshipService(DB, engine=ENGINE)

QUESTS = QuestService(DB, QUEST_DEFINITIONS, engine=ENGINE)

EXPLORATION = ExplorationService(SCENES)

COMBAT = CombatService(DB, engine=ENGINE)

NARRATOR_QUEUE = AsyncWorkQueue(max_concurrency=2)

SIM = WorldSimulator(DB, WORLD.data, engine=ENGINE)

AI_ROUTER = AITaskRouter(
    api_key=SETTINGS.openrouter_api_key,
    base_url=SETTINGS.openrouter_base_url,
    routine_model=SETTINGS.openrouter_routine_model,
    routine_fallback_model=SETTINGS.openrouter_routine_fallback_model,
    epic_model=SETTINGS.openrouter_epic_model,
    epic_fallback_model=SETTINGS.openrouter_epic_fallback_model,
    dynamic_free_model=SETTINGS.openrouter_dynamic_free_model,
    require_free=SETTINGS.openrouter_require_free,
    max_requests_per_minute=SETTINGS.openrouter_max_requests_per_minute,
    max_requests_per_day=SETTINGS.openrouter_max_requests_per_day,
    route_requests_per_minute=SETTINGS.openrouter_route_requests_per_minute,
    route_requests_per_day=SETTINGS.openrouter_route_requests_per_day,
    routine_timeout_seconds=SETTINGS.openrouter_timeout_seconds,
    epic_timeout_seconds=SETTINGS.openrouter_epic_timeout_seconds,
    failure_cooldown_seconds=SETTINGS.openrouter_failure_cooldown_seconds,
    disable_reasoning=SETTINGS.openrouter_disable_reasoning,
    app_url=SETTINGS.openrouter_app_url,
    app_name=SETTINGS.openrouter_app_name,
)

NARRATOR = Narrator(
    world=WORLD,
    provider=SETTINGS.narrator_provider,
    api_key=SETTINGS.openai_api_key,
    model=SETTINGS.openai_model,
    ai_router=AI_ROUTER,
)

NARRATOR_CONTEXT = NarratorContextBuilder(
    db=DB,
    simulator=SIM,
    world=WORLD,
    world_time_scale=SETTINGS.world_time_scale,
    max_chars=SETTINGS.narrator_context_max_chars,
    epic_max_chars=max(SETTINGS.narrator_context_max_chars, 6500),
    rag_query_cache_seconds=SETTINGS.rag_context_cache_seconds,
    rag_canon_cache_seconds=SETTINGS.rag_canon_cache_seconds,
)

ALERTS = AlertDispatcher(SETTINGS.alert_webhook_url, cooldown_seconds=SETTINGS.alert_cooldown_seconds)

PLAYER_PROPERTY_TYPE_CHOICES = [
    app_commands.Choice(name=str(defn.get("name", key.replace("_", " ").title()))[:100], value=key)
    for key, defn in PLAYER_PROPERTY_TYPES.items()
][:25]

PLAYER_PROPERTY_FACILITY_KEYS = tuple(str(x) for x in WORLD.abode_system.get("facilities", (
    "cultivation", "alchemy", "forge", "formation", "defense", "storage", "herb_garden", "beast_pen", "merchant"
)))

PLAYER_PROPERTY_FACILITY_LABELS = {
    "cultivation": "Cultivation Chamber",
    "alchemy": "Alchemy Furnace",
    "forge": "Forge Workshop",
    "formation": "Formation Core",
    "defense": "Defensive Formation",
    "storage": "Storage",
    "herb_garden": "Spirit Herb Garden",
    "beast_pen": "Spirit Beast Pen",
    "merchant": "Merchant Pavilion",
}

