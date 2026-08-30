from __future__ import annotations

import asyncio
import logging
import os
import time
from functools import wraps
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from .registry import ACTIONS, EVENT_HANDLERS, registered_group_command, registered_root_command

from ..config import Settings
from ..version import RELEASE_VERSION
from ..aptitudes import (
    aptitude_effects,
    bloodline_definition,
    grade_index,
    progression_requirements,
    root_compatibility,
    unlocked_ancestral_techniques,
)
from ..database import Database, SCHEMA_VERSION
from ..health import HealthServer, HealthState
from ..operations import AlertDispatcher
from ..core_services import (
    CombatService,
    ExplorationService,
    LocationSceneService,
    NPCRelationshipService,
    QuestService,
)
from ..game_engine import GameEngineClient, GameEngineError
from ..quests import QUEST_DEFINITIONS
from ..performance import AsyncWorkQueue
from ..advanced_runtime import BOSS_TEMPLATES, EQUIPMENT_DEFINITIONS, FORMATION_POSITIONS, FORMATION_STANCES, ERA_CYCLE
from ..battle import matchup_label, suppression_label, vitality_band, vitality_bar
from ..effects import aggregate_modifiers, medicine_toxicity_effect, normalize_effect_payload
from ..alchemy import (
    alchemy_output, alchemy_quality, is_pill, pill_toxicity_value, toxicity_band,
)
from ..birthfamily import (
    family_tier_name,
    family_profession_bonus,
    karma_label, karma_description,
)
from ..game import World
from ..samsara import soul_legacy_modifiers
from ..sense import hidden_npc_names
from ..seclusion import seclusion_daily_gain, seclusion_environment_multiplier
from ..ai_router import AITaskRouter
from ..narrator import Narrator, canonical_location_reply, is_current_location_question, roll_npc_memory
from ..narrator_context import NarratorContextBuilder
from ..worldtime import cultivation_cycle_summary, cultivation_speed_modifiers, from_game_minutes, MINUTES_PER_YEAR, MINUTES_PER_MONTH
from ..simulation import WorldSimulator, MINUTES_PER_DAY
CHILD_CULTIVATION_AWAKENING_AGE = 12
from .hubs import (
    HubDefinition,
    HubDynamicOption,
    HubPage,
    HubStatusField,
    register_hub_option_provider,
    send_hub,
)
from ..creation_ui import (
    cultivation_style_profile, family_emoji, family_root_tendencies,
    family_status_summary, location_theme, origin_vignette, recommended_cultivation_styles,
)
from ..progression_systems import (
    ascension_gate,
    condition_definition,
    condition_effect,
    profession_rank,
    profession_xp_needed,
    craft_quality,
    tribulation_tns,
)
from ..sect_recruitment import (
    RECRUITMENT_RETRY_COOLDOWN_MINUTES, RECOMMENDATION_RETRY_COOLDOWN_MINUTES,
    recruitment_definition, trial_profile, trial_modifier, recommendation_modifier, trial_outcome,
)
from ..sect_manor import (
    MAX_MANOR_FACILITY_LEVEL, SECT_MANOR_ESTABLISHMENT_COST, SECT_MANOR_FACILITIES,
    manor_benefit_lines, manor_craft_bonus, manor_defense_power_bonus, manor_qi_multiplier,
    manor_seclusion_multiplier, manor_upgrade_cost,
)
from ..fate import fate_label
from ..black_market import access_reason as black_market_access_reason
from ..inscription import array_definition
from ..realm_hubs import REALM_HUBS, realm_hub, realm_hub_by_location
from ..npc_memory import classify_memory, exchange_memory_summary, scene_memory_summary, public_mood_hint

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("xianxia")

SETTINGS = Settings.from_env()
GUILD = discord.Object(id=SETTINGS.guild_id)
ROOT = Path(__file__).resolve().parents[2]
WORLD = World(ROOT / "content" / "world.json")
ENGINE = GameEngineClient(
    SETTINGS.game_engine_url, timeout_seconds=SETTINGS.game_engine_timeout_seconds
)
DB = Database(
    ROOT / SETTINGS.database_path,
    slow_query_ms=SETTINGS.slow_query_ms,
    engine_url=SETTINGS.game_engine_url,
)
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
    routine_timeout_seconds=SETTINGS.openrouter_timeout_seconds,
    epic_timeout_seconds=SETTINGS.openrouter_epic_timeout_seconds,
    failure_cooldown_seconds=SETTINGS.openrouter_failure_cooldown_seconds,
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

_USER_ACTION_LOCKS: dict[int, asyncio.Lock] = {}


def _player_property_types() -> dict[str, dict[str, Any]]:
    configured = WORLD.abode_system.get("property_types", {})
    if isinstance(configured, dict) and configured:
        return {str(key): dict(value) for key, value in configured.items() if isinstance(value, dict)}
    return {
        "cave_abode": {"name": "Cave Abode", "emoji": "🏡", "defaults": {"cultivation": 1, "storage": 1}},
    }


PLAYER_PROPERTY_TYPES = _player_property_types()
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


def player_property_definition(property_type: str | None) -> dict[str, Any]:
    key = str(property_type or "cave_abode")
    return PLAYER_PROPERTY_TYPES.get(key, PLAYER_PROPERTY_TYPES.get("cave_abode", {}))


def player_property_label(abode: dict[str, Any]) -> str:
    definition = player_property_definition(str(abode.get("property_type") or "cave_abode"))
    return str(definition.get("name") or "Player Property")


def player_property_emoji(abode: dict[str, Any]) -> str:
    definition = player_property_definition(str(abode.get("property_type") or "cave_abode"))
    return str(definition.get("emoji") or "🏡")


def player_property_facility_lines(abode: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for key in PLAYER_PROPERTY_FACILITY_KEYS:
        value = int(abode.get(f"{key}_level", 0) or 0)
        if value > 0 or key in {"cultivation", "storage"}:
            lines.append(f"{PLAYER_PROPERTY_FACILITY_LABELS.get(key, key.replace('_', ' ').title())} **Lv.{value}**")
    return lines


async def settle_seclusion_for_user(user_id: int, current_game_minute: int | None = None) -> dict | None:
    state = await DB.get_seclusion(int(user_id))
    if not state:
        return None
    automation = await DB.get_automation_settings()
    if not automation.get("background_seclusion", True):
        return state
    if current_game_minute is None:
        current_game_minute = (await current_world_time()).total_minutes
    try:
        await ENGINE.authoritative_action(
            "seclusion.settle",
            int(user_id),
            {"minutes_per_day": MINUTES_PER_DAY, "force_end": False, "end_reason": ""},
            action_id=f"seclusion:auto:{int(user_id)}:{int(current_game_minute)}",
        )
    except GameEngineError as exc:
        if "no active seclusion" not in str(exc).lower():
            raise
    return await DB.get_seclusion(int(user_id), active_only=False)


async def settle_all_seclusions(current_game_minute: int) -> int:
    # Background settlement is owned by Go's simulation runner. Retained as a read-only
    # compatibility helper for callers outside the production worker.
    return len(await DB.list_active_seclusions())


def serialized_user_action(func):
    """Serialize state-changing commands per Discord user in this bot process.

    Cooldown checks and game-state writes often span more than one SQLite call.
    Without this guard, two near-simultaneous slash commands from the same user
    could both pass a cooldown/read check before either write completed.
    """
    @wraps(func)
    async def wrapper(interaction: discord.Interaction, *args, **kwargs):
        lock = _USER_ACTION_LOCKS.setdefault(interaction.user.id, asyncio.Lock())
        async with lock:
            if not func.__name__.startswith("seclusion_"):
                seclusion = await settle_seclusion_for_user(interaction.user.id)
                if seclusion and str(seclusion.get("status")) == "active":
                    remaining = max(0, int(seclusion["ends_game_minute"]) - (await current_world_time()).total_minutes)
                    await interaction.response.send_message(
                        f"🔒 You are in **closed-door seclusion** ({str(seclusion['mode']).upper()}). "
                        f"About **{remaining / MINUTES_PER_DAY:.1f} world-days** remain. "
                        "Use **/cultivation → Seclusion → End** to leave early before taking other actions.",
                        ephemeral=False,
                    )
                    return None
            return await func(interaction, *args, **kwargs)

    return wrapper


def human_duration(seconds: int) -> str:
    minutes, sec = divmod(max(0, seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {sec}s"
    return f"{sec}s"


def roll_line(result) -> str:
    sign = "+" if result.modifier >= 0 else ""
    return (
        f"2d10 ({result.die1}+{result.die2}) {sign}{result.modifier} = "
        f"**{result.total}** vs TN **{result.tn}** — **{result.degree}**"
    )


def chunk_text(text: str, limit: int = 1950) -> list[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        split = text.rfind("\n", 0, limit)
        if split < limit // 2:
            split = text.rfind(" ", 0, limit)
        if split <= 0:
            split = limit
        chunks.append(text[:split].strip())
        text = text[split:].strip()
    return chunks


async def reply_long(interaction: discord.Interaction, text: str, *, ephemeral: bool = False) -> None:
    chunks = chunk_text(text)
    if not interaction.response.is_done():
        await interaction.response.send_message(chunks[0], ephemeral=False)
    else:
        await interaction.followup.send(chunks[0], ephemeral=False)
    for chunk in chunks[1:]:
        await interaction.followup.send(chunk, ephemeral=False)


async def current_world_time():
    state = await DB.get_world_clock(scale=SETTINGS.world_time_scale)
    return from_game_minutes(int(state["game_minute"]))


async def current_npc_location(npc_name: str, period: str | None = None) -> str | None:
    """Resolve the mechanical NPC location from initialized simulation state.

    Daily world.json schedules still shape an NPC's routine while they remain in
    their home region, but there is no legacy no-simulation fallback anymore.
    """
    sim_state = await SIM.npc_status(npc_name)
    if period is None:
        period = (await current_world_time()).period
    if sim_state and sim_state.get("status") == "alive" and sim_state.get("current_location"):
        current = str(sim_state["current_location"])
        home = str(sim_state.get("home_location") or current)
        # Normal daily schedules still apply while the NPC remains in their home
        # region. Autonomous civilization travel overrides the schedule only when
        # the NPC has actually moved away from that home region.
        if current == home:
            return WORLD.npc_location_at(npc_name, period) or current
        return current
    return None


async def current_effect_modifiers(user_id: int) -> tuple[list[dict], dict[str, float], object]:
    authority = dict(await ENGINE.action("effects.current", int(user_id), {}))
    wt = from_game_minutes(int(authority.get("game_minute", 0)))
    effects = await DB.get_active_effects(user_id, wt.total_minutes)
    effects = [
        effect for effect in effects
        if not (
            str(effect.get("effect_key", "")) == "pill_toxicity"
            and str(effect.get("source_type", "")) == "alchemy"
            and str(effect.get("source_id", "")) == "pill_toxicity"
        )
    ]
    toxicity_effect = authority.get("pill_toxicity_effect")
    if isinstance(toxicity_effect, dict):
        effects.append(dict(toxicity_effect))
    character = await DB.get_character(user_id)
    if character:
        aptitudes = await DB.get_aptitudes(user_id)
        effects.extend(aptitude_effects(
            aptitudes,
            path=str(character.get("path", "")),
            root_system=WORLD.spiritual_root_system,
            bloodline_definitions=WORLD.bloodlines,
            physique_definitions=WORLD.physiques,
        ))
        deployed = await DB.get_active_location_array(str(character.get("location", "")), wt.total_minutes)
        if deployed:
            payload = normalize_effect_payload({
                "effect_key": f"location_array:{deployed.get('item_id','array')}",
                "name": str(deployed.get("name", "Deployed Formation")),
                "special": True,
                **dict(deployed.get("effect") or {}),
            })
            payload.update({
                "effect_key": f"location_array:{deployed.get('item_id','array')}",
                "source_type": "location_array",
                "source_id": str(deployed.get("location", "")),
                "stacks": 1,
                "starts_game_minute": int(deployed.get("starts_game_minute", 0)),
                "ends_game_minute": int(deployed.get("ends_game_minute", 0)),
            })
            effects.append(payload)
    return effects, aggregate_modifiers(effects), wt


async def sync_pill_toxicity_effect(user_id: int, *, game_minute: int, state: dict | None = None) -> dict:
    state = state or await DB.get_alchemy_state(int(user_id), game_minute=int(game_minute))
    toxicity = int(state.get("pill_toxicity", 0))
    payload = medicine_toxicity_effect(toxicity)
    if payload is None:
        await DB.remove_effect(
            int(user_id), effect_key="pill_toxicity", source_type="alchemy", source_id="pill_toxicity",
        )
    else:
        await DB.apply_effect(
            int(user_id), effect_key="pill_toxicity", name="Pill Toxicity",
            source_type="alchemy", source_id="pill_toxicity", effect=normalize_effect_payload(payload),
            starts_game_minute=int(game_minute), duration_game_minutes=None,
        )
    return state


def effective_attribute(character: dict, modifiers: dict[str, float], attr: str) -> int:
    return int(round(character["attributes"].get(attr, 0) + modifiers.get(attr, 0.0)))


def _npc_name_mentioned(text: str, npc_name: str) -> bool:
    haystack = " ".join(str(text or "").casefold().split())
    words = [w for w in str(npc_name or "").casefold().split() if w]
    if not haystack or not words:
        return False
    aliases = {" ".join(words)}
    if len(words) >= 2:
        aliases.add(" ".join(words[:2]))
        aliases.add(" ".join(words[-2:]))
    return any(len(alias) >= 4 and alias in haystack for alias in aliases)


async def _remember_freeform_npc_scene(
    *, user_id: int, character: dict[str, Any], player_text: str, narration: str, game_minute: int,
) -> None:
    """Store player-specific episodic memories for NPCs explicitly involved in freeform RP.

    This does not alter trust or other mechanical relationship values. It only
    lets a present named NPC remember an exchange later instead of resetting.
    """
    region = await SIM.civilization_status(str(character.get("location") or ""))
    if not region:
        return
    for row in (region.get("npcs") or [])[:8]:
        npc_name = str(row.get("npc_name") or "").strip()
        if not npc_name or not _npc_name_mentioned(player_text, npc_name):
            continue
        memory_kind, salience = classify_memory(player_text, narration)
        memory_id = await DB.add_npc_player_memory(
            int(user_id), npc_name, memory_kind=memory_kind,
            summary=scene_memory_summary(player_text, npc_name, narration),
            salience=salience, source="freeform", game_minute=int(game_minute),
        )
        # Enrich the trigger-mirrored unified RAG row with structured locality.
        await DB.add_rag_memory(
            int(user_id), source_key=f"npc:{memory_id}", memory_kind=memory_kind,
            summary=scene_memory_summary(player_text, npc_name, narration), salience=salience,
            location=str(character.get("location") or ""), npc_name=npc_name,
            source="freeform", game_minute=int(game_minute),
        )


def _event_archive_minutes() -> int:
    allowed = {60, 1440, 4320, 10080}
    value = SETTINGS.event_thread_auto_archive_minutes
    return value if value in allowed else 1440


async def _resolve_text_channel(guild: discord.Guild, channel_id: int | None) -> discord.TextChannel | None:
    if not channel_id:
        return None
    channel = guild.get_channel(channel_id)
    if channel is None:
        try:
            channel = await guild.fetch_channel(channel_id)
        except discord.NotFound:
            # A configured channel may have been deleted or belong to an older
            # server layout. Treat that as a stale binding rather than an
            # application error; the setup/repair commands can replace it.
            log.warning("Configured text channel %s no longer exists; treating binding as stale", channel_id)
            return None
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Could not resolve text channel %s", channel_id)
            return None
    return channel if isinstance(channel, discord.TextChannel) else None


def _realm_access_role_name(world_name: str) -> str:
    return f"Xianxia • {world_name}"[:100]


async def _ensure_realm_access_roles(guild: discord.Guild) -> dict[str, discord.Role]:
    roles: dict[str, discord.Role] = {}
    me = guild.me
    if not me or not me.guild_permissions.manage_roles:
        return roles
    for world_name in REALM_HUBS:
        role_name = _realm_access_role_name(world_name)
        role = discord.utils.get(guild.roles, name=role_name)
        if role is None:
            try:
                role = await guild.create_role(
                    name=role_name, mentionable=False, reason="Xianxia realm-world visibility gate"
                )
            except discord.HTTPException:
                log.exception("Could not create realm access role for %s", world_name)
                continue
        roles[world_name] = role
    return roles


async def _sync_realm_access_roles(
    guild: discord.Guild | None, member: discord.Member | discord.User, character: dict[str, Any]
) -> None:
    if guild is None or not isinstance(member, discord.Member):
        return
    me = guild.me
    if not me or not me.guild_permissions.manage_roles:
        return
    role_map = {world: discord.utils.get(guild.roles, name=_realm_access_role_name(world)) for world in REALM_HUBS}
    available = {world: role for world, role in role_map.items() if role is not None}
    if not available:
        return
    unlocked = {
        world for world, hub in REALM_HUBS.items()
        if int(character.get("realm_index", 0)) >= int(hub.get("min_realm_index", 0))
    }
    current_ids = {role.id for role in member.roles}
    add_roles = [role for world, role in available.items() if world in unlocked and role.id not in current_ids]
    remove_roles = [role for world, role in available.items() if world not in unlocked and role.id in current_ids]
    try:
        if add_roles:
            await member.add_roles(*add_roles, reason="Xianxia cultivation unlocked realm-world access")
        if remove_roles:
            await member.remove_roles(*remove_roles, reason="Xianxia realm-world access resync")
    except discord.Forbidden:
        log.warning("Could not synchronize realm access roles for user %s; check bot role hierarchy", member.id)
    except discord.HTTPException:
        log.exception("Could not synchronize realm access roles for user %s", member.id)


async def ensure_realm_hub_channels(guild: discord.Guild, *, category_name: str = "🌌 Realm Capitals") -> list[dict[str, Any]]:
    """Bind existing realm-capital channels; Discord layout is admin-dashboard owned."""
    existing = {str(row["world_name"]): row for row in await DB.get_realm_hub_channels(guild.id)}
    category = next((item for item in guild.categories if item.name == category_name), None)
    for world, hub in REALM_HUBS.items():
        row = existing.get(world)
        channel = guild.get_channel(int(row["channel_id"])) if row else None
        if not isinstance(channel, discord.TextChannel):
            channel = next(
                (item for item in guild.text_channels if item.name == str(hub["channel_name"])),
                None,
            )
        if channel is None:
            continue
        await DB.set_realm_hub_channel(
            guild_id=guild.id,
            world_name=world,
            location=str(hub["location"]),
            channel_id=channel.id,
            category_id=channel.category_id if channel.category_id is not None else (category.id if category else None),
        )
    return await DB.get_realm_hub_channels(guild.id)


async def event_channels(interaction: discord.Interaction) -> tuple[discord.TextChannel | None, discord.TextChannel | None]:
    guild = interaction.guild
    if guild is None:
        return None, None

    config = await DB.get_server_config(guild.id)
    announcement_id = config.get("announcement_channel_id")
    scene_id = config.get("event_scene_channel_id")

    announcement_channel = await _resolve_text_channel(guild, announcement_id)
    scene_channel = await _resolve_text_channel(guild, scene_id)
    return announcement_channel, scene_channel


async def home_scene_channel(interaction: discord.Interaction) -> discord.TextChannel | None:
    guild = interaction.guild
    if guild is None:
        return None
    config = await DB.get_server_config(guild.id)
    channel_id = config.get("home_scene_channel_id") or config.get("event_scene_channel_id")
    return await _resolve_text_channel(guild, channel_id)


async def exploration_scene_channel(interaction: discord.Interaction) -> discord.TextChannel | None:
    guild = interaction.guild
    if guild is None:
        return None
    config = await DB.get_server_config(guild.id)
    return await _resolve_text_channel(guild, config.get("exploration_channel_id"))


async def configured_info_channel(guild: discord.Guild | None) -> discord.TextChannel | None:
    if guild is None:
        return None
    config = await DB.get_server_config(guild.id)
    return await _resolve_text_channel(guild, config.get("info_channel_id"))


async def _get_thread(guild: discord.Guild, thread_id: int | None) -> discord.Thread | None:
    if not thread_id:
        return None
    existing = guild.get_thread(int(thread_id))
    if existing is not None:
        try:
            if existing.archived:
                await existing.edit(archived=False, reason="Resume Xianxia private scene")
        except discord.HTTPException:
            pass
        return existing
    try:
        fetched = await guild.fetch_channel(int(thread_id))
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        return None
    if not isinstance(fetched, discord.Thread):
        return None
    try:
        if fetched.archived:
            await fetched.edit(archived=False, reason="Resume Xianxia private scene")
    except discord.HTTPException:
        pass
    return fetched


async def ensure_expedition_thread(interaction: discord.Interaction, character: dict[str, Any]) -> discord.Thread | None:
    """Create/recover one private expedition journal per player and guild."""
    guild = interaction.guild
    if guild is None:
        return None
    row = await DB.get_expedition_thread(guild.id, interaction.user.id)
    if row:
        thread = await _get_thread(guild, row.get("thread_id"))
        if thread is not None:
            try:
                await thread.add_user(interaction.user)
            except (discord.Forbidden, discord.HTTPException):
                pass
            await DB.update_expedition_location(guild.id, interaction.user.id, str(character.get("location") or "Unknown"))
            return thread
    parent = await exploration_scene_channel(interaction)
    if parent is None:
        return None
    try:
        thread = await parent.create_thread(
            name=(f"🧭 {character.get('name', interaction.user.display_name)} • Expedition")[:100],
            type=discord.ChannelType.private_thread,
            auto_archive_duration=_event_archive_minutes(),
            reason=f"Private Xianxia expedition journal for {interaction.user}",
        )
        await thread.add_user(interaction.user)
        await DB.set_expedition_thread(
            guild.id, interaction.user.id, thread_id=thread.id,
            parent_channel_id=parent.id, last_location=str(character.get("location") or "Unknown"),
        )
        await thread.send(
            f"🧭 **{character.get('name', interaction.user.display_name)} — Private Expedition Journal**\n"
            f"Current location: **{character.get('location','Unknown')}**\n"
            "Only you, invited administrators, and the bot can use this private scene. "
            "Exploration results and guided Scene Actions are written here so your choices remain visible as a continuing adventure log.\n\n"
            "Use **/world → Explore** to search the current location or **/action** for a guided in-scene action."
        )
        return thread
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Could not create private expedition thread for user %s", interaction.user.id)
        return None



async def ensure_birth_family_household_thread(
    interaction: discord.Interaction, family: dict[str, Any]
) -> discord.Thread | None:
    """Create/recover one shared private Discord scene for a canonical birth household."""
    guild = interaction.guild
    if guild is None:
        return None
    family_id = int(family.get("family_id") or 0)
    if family_id <= 0:
        return None
    row = await DB.get_birth_family_household_thread(guild.id, family_id)
    if row:
        thread = await _get_thread(guild, row.get("thread_id"))
        if thread is not None:
            try:
                await thread.add_user(interaction.user)
            except (discord.Forbidden, discord.HTTPException):
                pass
            return thread
    parent = await home_scene_channel(interaction)
    if parent is None:
        return None
    family_name = str(family.get("family_name") or "Birth Family")
    base_location = str(family.get("location") or "Unknown")
    try:
        thread = await parent.create_thread(
            name=(f"🏠 {family_name} Household")[:100],
            type=discord.ChannelType.private_thread,
            auto_archive_duration=_event_archive_minutes(),
            reason=f"Shared Xianxia birth household for family {family_id}",
        )
        await thread.add_user(interaction.user)
        await DB.set_birth_family_household_thread(
            guild.id, family_id, thread_id=thread.id, parent_channel_id=parent.id
        )
        await thread.send(
            f"🏠 **{family_name} — Shared Household**\n"
            f"Home region: **{base_location}**\n\n"
            "Every player born into this same canonical household uses this scene. "
            "When multiple household members are inside at the same time, they can talk, roleplay, and target one another with guided Scene Actions here. "
            "Use **/family leave** to return to the household's home region."
        )
        return thread
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Could not create shared birth-household thread for family %s", family_id)
        return None

def _sect_abode_name(character_name: str, sect_name: str, rank_name: str = "Outer Disciple") -> str:
    rank = str(rank_name or "Disciple")
    if "Ancestor" in rank or "Master" in rank:
        residence = "Peak Residence"
    elif "Core" in rank:
        residence = "Inner Peak Pavilion"
    elif "Inner" in rank:
        residence = "Spirit Courtyard"
    else:
        residence = "Disciple Courtyard"
    return f"{character_name}'s {residence}"


async def ensure_sect_abode_record(user_id: int, character: dict[str, Any], membership: dict[str, Any]) -> dict[str, Any]:
    sect_name = str(membership["sect_name"])
    rec = recruitment_definition(WORLD.sects, sect_name) or {}
    base_location = str(rec.get("location") or character.get("location") or "Unknown")
    return await DB.ensure_sect_abode(
        user_id,
        sect_name=sect_name,
        name=_sect_abode_name(str(character.get("name") or "Cultivator"), sect_name, str(membership.get("rank_name") or "Disciple")),
        base_location=base_location,
    )


async def ensure_sect_abode_thread_for(
    guild: discord.Guild, member: discord.Member | discord.User, abode: dict[str, Any]
) -> discord.Thread | None:
    thread = await _get_thread(guild, abode.get("thread_id"))
    if thread is not None:
        try:
            await thread.add_user(member)
        except (discord.Forbidden, discord.HTTPException):
            pass
        return thread
    cfg = await DB.get_server_config(guild.id)
    parent = await _resolve_text_channel(guild, cfg.get("home_scene_channel_id") or cfg.get("event_scene_channel_id"))
    if parent is None:
        return None
    try:
        thread = await parent.create_thread(
            name=(f"🏯 {abode['name']}")[:100],
            type=discord.ChannelType.private_thread,
            auto_archive_duration=_event_archive_minutes(),
            reason=f"Private sect abode for {member}",
        )
        await thread.add_user(member)
        await DB.set_sect_abode_thread(int(member.id), thread_id=thread.id, thread_channel_id=parent.id)
        await thread.send(
            f"🏯 **{abode['name']} — Sect Abode**\n"
            f"Sect: **{abode['sect_name']}**\n"
            f"Assigned to: **{getattr(member, 'display_name', str(member))}**\n"
            f"Sect gate: **{abode['base_location']}**\n\n"
            "This is your persistent private residence inside the sect. Cultivation RP, study, alchemy, "
            "private meetings and guided Scene Actions can continue here without cluttering a public city channel."
        )
        return thread
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Could not create private sect-abode thread for user %s", member.id)
        return None


async def _private_scene_for_thread(guild: discord.Guild, thread_id: int, user_id: int, character: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    expedition = await DB.get_expedition_thread_by_thread(thread_id)
    if expedition and int(expedition.get("user_id", 0)) == int(user_id):
        current_location = str(character.get("location") or "")
        if not current_location.startswith(("abode:", "sect_abode:", "personal_world:", "birth_family:")):
            return "expedition", expedition
    household = await DB.get_birth_family_household_thread_by_thread(thread_id)
    if household:
        family = await DB.get_birth_family(user_id)
        expected_location = f"birth_family:{int(household.get('family_id') or 0)}"
        if family and int(family.get("family_id") or 0) == int(household.get("family_id") or 0) and str(character.get("location") or "") == expected_location:
            return "birth_family_household", household
    abode = await DB.get_abode_by_thread(thread_id)
    if abode and str(character.get("location") or "") == str(abode.get("location_key") or ""):
        if await DB.can_access_abode(int(abode["user_id"]), int(user_id)):
            return "player_abode", abode
    sect_abode = await DB.get_sect_abode_by_thread(thread_id)
    if sect_abode and int(sect_abode.get("user_id", 0)) == int(user_id) and str(character.get("location") or "") == str(sect_abode.get("location_key") or ""):
        return "sect_abode", sect_abode
    return None


async def active_private_location_thread(interaction: discord.Interaction, character: dict[str, Any]) -> discord.Thread | None:
    guild = interaction.guild
    if guild is None:
        return None
    location = str(character.get("location") or "")
    if location.startswith("birth_family:"):
        family = await DB.get_birth_family(interaction.user.id)
        if family and location == f"birth_family:{int(family.get('family_id') or 0)}":
            return await ensure_birth_family_household_thread(interaction, family)
    if location.startswith("abode:"):
        abode = await DB.get_abode_by_location(location)
        if abode:
            thread = await _get_thread(guild, abode.get("thread_id"))
            if thread is not None and await DB.can_access_abode(int(abode["user_id"]), interaction.user.id):
                try: await thread.add_user(interaction.user)
                except (discord.Forbidden, discord.HTTPException): pass
                return thread
    if location.startswith("sect_abode:"):
        abode = await DB.get_sect_abode_by_location(location)
        if abode and int(abode.get("user_id", 0)) == interaction.user.id:
            return await ensure_sect_abode_thread_for(guild, interaction.user, abode)
    return None


async def send_long_to_thread(thread: discord.Thread, text: str) -> None:
    for chunk in chunk_text(text):
        await thread.send(chunk)


async def configured_log_channel(guild: discord.Guild | None) -> discord.TextChannel | None:
    if guild is None:
        return None
    config = await DB.get_server_config(guild.id)
    return await _resolve_text_channel(guild, config.get("log_channel_id"))


async def configured_begin_channel(guild: discord.Guild | None) -> discord.TextChannel | None:
    if guild is None:
        return None
    config = await DB.get_server_config(guild.id)
    return await _resolve_text_channel(guild, config.get("begin_channel_id"))


async def post_server_log(guild: discord.Guild | None, title: str, detail: str) -> None:
    """Best-effort operational Discord log without exposing full tracebacks or secrets."""
    channel = await configured_log_channel(guild)
    if channel is None:
        return
    body = str(detail).strip()
    if len(body) > 1600:
        body = body[:1597] + "..."
    try:
        await channel.send(f"🧾 **{str(title)[:200]}**\n{body}")
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Could not post to configured Discord log channel")


async def ensure_abode_thread(interaction: discord.Interaction, abode: dict) -> discord.Thread | None:
    if interaction.guild is None:
        return None
    thread_id = abode.get("thread_id")
    if thread_id:
        existing = interaction.guild.get_thread(int(thread_id))
        if existing is not None:
            try:
                await existing.add_user(interaction.user)
            except (discord.Forbidden, discord.HTTPException):
                pass
            return existing
        try:
            fetched = await interaction.guild.fetch_channel(int(thread_id))
            if isinstance(fetched, discord.Thread):
                try:
                    await fetched.add_user(interaction.user)
                except (discord.Forbidden, discord.HTTPException):
                    pass
                return fetched
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass
    channel = await home_scene_channel(interaction)
    if channel is None:
        return None
    property_label = player_property_label(abode)
    emoji = player_property_emoji(abode)
    try:
        thread = await channel.create_thread(
            name=(f"{emoji} {abode['name']}")[:100],
            type=discord.ChannelType.private_thread,
            auto_archive_duration=_event_archive_minutes(),
            reason=f"Private player-owned location for {interaction.user}",
        )
        await thread.add_user(interaction.user)
        await DB.set_abode_thread(
            interaction.user.id, thread_id=thread.id, thread_channel_id=channel.id
        )
        facilities = " • ".join(player_property_facility_lines(abode)) or "No developed facilities yet"
        await thread.send(
            f"{emoji} **{abode['name']} — {property_label}**\n"
            f"Owner: **{interaction.user.display_name}**\n"
            f"Entrance: **{abode.get('base_location','Unknown')}**\n"
            f"Facilities: {facilities}\n\n"
            "This private thread is the persistent scene for this player-owned location. "
            "Invited cultivators can be granted or revoked with **/abode → Invite** and **/abode → Revoke**.\n"
            "Physical travel still matters: guests must reach the property's entrance before they can enter. "
            "Use this scene for cultivation, crafting, beasts, family scenes, commerce, meetings and guided Scene Actions."
        )
        return thread
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Could not create private player-property thread for user %s", interaction.user.id)
        return None



EVENT_ACTION_RULES: dict[str, dict[str, Any]] = {
    "observe": {"label":"Observe", "emoji":"👁️", "attribute":"insight", "tn":10, "contribution":1},
    "investigate": {"label":"Investigate", "emoji":"🔎", "attribute":"insight", "tn":12, "contribution":2, "investigation":2},
    "aid": {"label":"Aid Locals", "emoji":"🤲", "attribute":"heart", "tn":12, "contribution":2, "support":2},
    "support": {"label":"Support Response", "emoji":"🛡️", "attribute":"heart", "tn":13, "contribution":3, "support":3},
    "interfere": {"label":"Interfere", "emoji":"🕸️", "attribute":"insight", "tn":14, "contribution":-1, "interference":3},
    "stabilize": {"label":"Stabilize", "emoji":"☯️", "attribute":"spirit", "tn":14, "contribution":3, "support":2},
    "evacuate": {"label":"Evacuate", "emoji":"🚶", "attribute":"heart", "tn":12, "contribution":2, "support":3},
    "defend": {"label":"Defend", "emoji":"⚔️", "attribute":"body", "tn":14, "contribution":3, "support":2},
    "gather": {"label":"Gather Resources", "emoji":"🌿", "attribute":"insight", "tn":12, "contribution":2},
    "compete": {"label":"Compete", "emoji":"🏆", "attribute":"body", "tn":14, "contribution":2},
    "negotiate": {"label":"Negotiate", "emoji":"🤝", "attribute":"heart", "tn":13, "contribution":2, "support":1},
    "infiltrate": {"label":"Infiltrate", "emoji":"🥷", "attribute":"insight", "tn":15, "contribution":2, "interference":2},
    "exploit": {"label":"Exploit Opportunity", "emoji":"💰", "attribute":"insight", "tn":15, "contribution":1, "interference":1},
    "endure": {"label":"Endure", "emoji":"🗿", "attribute":"body", "tn":13, "contribution":2},
    "withdraw": {"label":"Withdraw", "emoji":"↩️", "attribute":"heart", "tn":0, "contribution":0},
}


def _event_action_keys(category: str, event_type: str) -> tuple[str, ...]:
    text = f"{category} {event_type}".casefold()
    if any(x in text for x in ("beast", "demon", "invasion", "tide", "attack", "war", "siege")):
        return ("observe","investigate","defend","aid","evacuate","interfere","withdraw")
    if any(x in text for x in ("auction", "festival", "market", "merchant", "treasure")):
        return ("observe","investigate","negotiate","compete","exploit","aid","withdraw")
    if any(x in text for x in ("tribulation", "calamity", "storm", "lightning", "spatial", "phenomenon")):
        return ("observe","investigate","stabilize","endure","aid","interfere","withdraw")
    if any(x in text for x in ("secret", "ruin", "inheritance", "realm", "discovery")):
        return ("observe","investigate","gather","compete","infiltrate","aid","withdraw")
    if any(x in text for x in ("sect", "recruit", "politic", "alliance", "diplom")):
        return ("observe","investigate","negotiate","support","interfere","withdraw")
    return ("observe","investigate","aid","support","interfere","withdraw")


class EventActionSelect(discord.ui.Select):
    def __init__(self, owner: "EventSceneView") -> None:
        self.owner = owner
        options=[]
        for key in _event_action_keys(owner.category, owner.event_type):
            rule=EVENT_ACTION_RULES[key]
            options.append(discord.SelectOption(label=rule["label"], value=key, emoji=rule["emoji"]))
        super().__init__(placeholder="Choose an event action…", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.owner.run_event_action(interaction, self.values[0])


class EventNpcTalkModal(discord.ui.Modal):
    message = discord.ui.TextInput(label="What do you say?", style=discord.TextStyle.paragraph, max_length=1200)

    def __init__(self, npc_name: str) -> None:
        super().__init__(title=f"Speak with {npc_name}"[:45])
        self.npc_name=npc_name

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await EVENT_HANDLERS.invoke("talk", interaction, self.npc_name, str(self.message.value))


class EventNpcSelect(discord.ui.Select):
    def __init__(self, names: list[str]) -> None:
        self.names=names[:25]
        super().__init__(
            placeholder="Choose an NPC to speak with…", min_values=1, max_values=1,
            options=[discord.SelectOption(label=n[:100], value=n[:100], emoji="💬") for n in self.names],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(EventNpcTalkModal(self.values[0]))


class EventNpcSelectView(discord.ui.View):
    def __init__(self, names: list[str]) -> None:
        super().__init__(timeout=300)
        self.add_item(EventNpcSelect(names))


class EventSystemsSelect(discord.ui.Select):
    def __init__(self, systems: list[tuple[str,str,str]]) -> None:
        self.systems={key:(label,emoji) for key,label,emoji in systems}
        super().__init__(
            placeholder="Open a connected game system…", min_values=1, max_values=1,
            options=[discord.SelectOption(label=label[:100],value=key,emoji=emoji) for key,label,emoji in systems[:25]],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        key=self.values[0]
        if key=="battle": await EVENT_HANDLERS.invoke("battle", interaction)
        elif key=="secret": await EVENT_HANDLERS.invoke("secret", interaction)
        elif key=="war": await EVENT_HANDLERS.invoke("war", interaction)
        elif key=="auction": await EVENT_HANDLERS.invoke("auction", interaction)
        elif key=="party": await EVENT_HANDLERS.invoke("party", interaction)
        elif key=="boss": await EVENT_HANDLERS.invoke("boss", interaction)
        elif key=="formation": await EVENT_HANDLERS.invoke("formation", interaction)
        elif key=="hunter": await EVENT_HANDLERS.invoke("hunter", interaction)
        elif key=="blackmarket": await EVENT_HANDLERS.invoke("blackmarket", interaction)
        elif key=="civilization": await EVENT_HANDLERS.invoke("civilization", interaction, None)
        elif key=="scene": await EVENT_HANDLERS.invoke("scene", interaction)
        else: await interaction.response.send_message("That system is no longer available here.",ephemeral=False)


class EventSystemsView(discord.ui.View):
    def __init__(self, systems: list[tuple[str,str,str]]) -> None:
        super().__init__(timeout=300)
        self.add_item(EventSystemsSelect(systems))


class EventSceneView(discord.ui.View):
    """Persistent, event-specific play surface backed by canonical game state."""

    def __init__(
        self, *, title: str, event_type: str, expires_at: float, location: str | None = None,
        event_key: str | None = None, category: str = "Event", severity: int = 1,
    ) -> None:
        remaining=max(300,min(21600,int(expires_at-time.time())))
        super().__init__(timeout=remaining)
        self.title=str(title)[:160]; self.event_type=str(event_type or "event")[:80]
        self.expires_at=float(expires_at); self.location=str(location).strip()[:180] if location else None
        self.event_key=str(event_key or "")[:240]; self.category=str(category or "Event")[:80]
        self.severity=max(1,min(10,int(severity or 1)))
        self.add_item(EventActionSelect(self))

    def embed(self) -> discord.Embed:
        active=self.expires_at>time.time(); colour=0x9B59B6 if active else 0x5C6370
        mode=("Type normal RP messages here — the narrator reacts automatically." if SETTINGS.auto_narrate_event_threads
              else "Mention the bot for free-form RP, or use the controls below.")
        embed=discord.Embed(
            title=f"🌌 {self.title}",
            description=(f"**{self.category}** • Severity **{self.severity}/10** • {'🟢 Active' if active else '⚫ Closed'}\n"
                         +(f"📍 **{self.location}**\n" if self.location else "")+f"⏳ Closes <t:{int(self.expires_at)}:R>"),
            color=colour,
        )
        embed.add_field(name="🎮 Event Actions",value="Choose a context-specific action from the menu. Results use character attributes, event severity, and persistent participation state.",inline=False)
        embed.add_field(name="🧭 Connected Systems",value="**Systems** exposes real mechanics available here: combat, secret realms, territory wars, auctions, parties, bosses, formations, bounty hunters, black markets and regional state.",inline=False)
        embed.add_field(name="👥 Living Scene",value="**Participants** shows players and mechanically present NPCs. **Talk** opens direct NPC dialogue without remembering a slash command.",inline=False)
        embed.add_field(name="💬 Free-form RP",value=mode,inline=False)
        embed.set_footer(text="Go/SQLite remains authoritative • Event choices cannot bypass location, rolls, cooldowns or permissions")
        return embed

    async def _character_here(self, interaction: discord.Interaction) -> dict[str, Any] | None:
        character=await DB.get_character(interaction.user.id)
        if character is None:
            await interaction.response.send_message("Create a cultivator with **/begin** first.",ephemeral=False); return None
        if self.expires_at<=time.time():
            await interaction.response.send_message("This event scene has already closed.",ephemeral=False); return None
        if self.location and str(character.get("location") or "")!=self.location:
            await interaction.response.send_message(f"You are at **{character.get('location','Unknown')}**. Travel to **{self.location}** before acting in this event.",ephemeral=False); return None
        return character

    async def _event_record(self) -> dict[str, Any]:
        if self.event_key:
            row=await DB.get_world_event(self.event_key)
            if row: return row
        return {"event_key":self.event_key,"event_type":self.event_type,"title":self.title,"location":self.location or "","payload":{"category":self.category,"severity":self.severity}}

    async def run_event_action(self, interaction: discord.Interaction, action_key: str) -> None:
        c=await self._character_here(interaction)
        if not c:return
        rule=EVENT_ACTION_RULES.get(action_key,EVENT_ACTION_RULES["observe"])
        wt=await current_world_time()
        if not self.event_key:
            await interaction.response.send_message("This event scene is missing its canonical event key.",ephemeral=False); return
        try:
            envelope=await ENGINE.authoritative_action(
                "world_event.act",interaction.user.id,{"event_key":self.event_key,"action_key":action_key},
                action_id=f"discord:{interaction.id}:world_event.act:{self.event_key}:{action_key}",
            )
        except GameEngineError as exc:
            await interaction.response.send_message(f"Event action failed: {exc}",ephemeral=False); return
        outcome=dict(envelope.get("result") or {})
        if action_key=="withdraw":
            await interaction.response.send_message("↩️ You withdraw from active involvement. The event continues without forcing another action from you.",ephemeral=False); return
        success=bool(outcome.get("success")); state=dict(outcome.get("state") or {}); roll=dict(outcome.get("roll") or {})
        sign="+" if int(roll.get("modifier",0))>=0 else ""
        roll_text=(f"2d10 ({int(roll.get('die1',0))}+{int(roll.get('die2',0))}) {sign}{int(roll.get('modifier',0))} = "
                   f"**{int(roll.get('total',0))}** vs TN **{int(roll.get('tn',0))}** — **{roll.get('degree','Result')}**")
        lines=[f"{rule['emoji']} **{rule['label']} — {'SUCCESS' if success else 'FAILURE'}**",roll_text]
        if state:
            lines.append(f"Event contribution **{int(state.get('contribution',0)):+d}** • investigation **{int(state.get('investigation',0))}** • support **{int(state.get('support',0))}** • interference **{int(state.get('interference',0))}**")
        first=dict(outcome.get("first_participation") or {})
        reward_details=[]
        if int(first.get("cultivation_awarded",0)): reward_details.append(f"Cultivation +{int(first['cultivation_awarded'])}")
        if int(first.get("spirit_stones",0)): reward_details.append(f"Spirit Stones +{int(first['spirit_stones'])}")
        if int(first.get("insight_xp",0)): reward_details.append(f"Insight +{int(first['insight_xp'])}")
        items=dict(first.get("items") or {})
        if items: reward_details.append(WORLD.item_names(items))
        effect=dict(first.get("effect") or {})
        if effect: reward_details.append(f"Effect: {effect.get('name','Event effect')}")
        if int(first.get("karma_delta",0)): reward_details.append(f"Karma {int(first['karma_delta']):+d} → {int(first.get('karma_score',0)):+d}")
        if int(first.get("fate_delta",0)): reward_details.append(f"Fate {int(first['fate_delta']):+d} → {int(first.get('fate',0))}/9")
        if reward_details: lines.append("First-participation outcome: "+" • ".join(reward_details))
        await interaction.response.send_message("\n".join(lines),ephemeral=False)

    async def _open_scene_actions(self, interaction: discord.Interaction, *, default_action: str) -> None:
        character=await self._character_here(interaction)
        if character is None:return
        npcs=await _scene_action_targets(character); view=SceneActionView(interaction.user.id,character,npcs)
        if default_action in SCENE_ACTION_TYPES:
            view.action_key = default_action
            view.target = "Environment"
            view.refresh_components()
        await interaction.response.send_message(embed=view.embed(),view=view,ephemeral=False)

    @discord.ui.button(label="Systems",emoji="🧭",style=discord.ButtonStyle.primary,row=1)
    async def systems(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        c=await self._character_here(interaction)
        if c is None:return
        location=str(c.get("location") or ""); systems:list[tuple[str,str,str]]=[("scene","Scene Status","🎭"),("civilization","Regional State","🏙️")]
        if await DB.get_active_battle(interaction.user.id): systems.append(("battle","Active Battle","⚔️"))
        if any(e.get("event_type")=="secret_realm" for e in await DB.get_active_world_events(location)) or (await DB.get_secret_realm_run(interaction.user.id) or {}).get("active"): systems.append(("secret","Secret Realm","🌀"))
        wars=[w for w in await DB.get_territory_wars(active_only=True) if str(w.get("territory_key") or "")==location]
        if wars: systems.append(("war","Territory War","🏯"))
        if WORLD.auction_house_at(location): systems.append(("auction","Auction House","🏮"))
        party=await DB.get_party(interaction.user.id)
        if party:
            systems.append(("party","Cultivation Party","👥")); systems.append(("formation","Party Formation","☯️"))
        if await DB.get_boss_encounter(user_id=interaction.user.id): systems.append(("boss","Boss Raid","👹"))
        if await DB.get_bounty_hunter_pursuit(user_id=interaction.user.id): systems.append(("hunter","Bounty Pursuit","🎯"))
        wt=await current_world_time()
        if await DB.get_active_black_market(location,wt.total_minutes): systems.append(("blackmarket","Black Market","🌑"))
        text="\n".join(f"{emoji} **{label}**" for _,label,emoji in systems)
        await interaction.response.send_message(f"🧭 **Connected systems at {location}**\n{text}",view=EventSystemsView(systems),ephemeral=False)

    @discord.ui.button(label="Participants",emoji="👥",style=discord.ButtonStyle.secondary,row=1)
    async def participants(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        c=await self._character_here(interaction)
        if c is None:return
        lines=[f"👥 **Participants — {self.title}**"]
        if self.event_key:
            rows=await DB.list_world_event_participants(self.event_key,limit=15)
            if rows:
                lines.append("\n**Cultivators**")
                for r in rows: lines.append(f"• **{r.get('character_name','Cultivator')}** — contribution {int(r.get('contribution',0)):+d} • {int(r.get('actions_taken',0))} actions")
        regional=await SIM.civilization_status(str(c.get("location") or ""))
        npcs=list((regional or {}).get("npcs") or [])
        if npcs:
            lines.append("\n**Mechanically present NPCs**")
            for npc in npcs[:8]:
                life=await DB.get_npc_life_state(str(npc.get("npc_name") or ""))
                injury=str((life or {}).get("injury") or "").strip(); rank=str((life or {}).get("sect_rank") or npc.get("profession") or "")
                suffix=f" • {rank}" if rank else ""
                if injury:suffix+=f" • injured: {injury}"
                lines.append(f"• **{npc.get('npc_name')}**{suffix}")
        if len(lines)==1: lines.append("No persistent participants are recorded yet.")
        await reply_long(interaction,"\n".join(lines),ephemeral=False)

    @discord.ui.button(label="Consequences",emoji="📜",style=discord.ButtonStyle.secondary,row=1)
    async def consequences(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        c=await self._character_here(interaction)
        if c is None:return
        event=await self._event_record(); payload=dict(event.get("payload") or {})
        lines=[f"📜 **Consequences — {self.title}**",str(payload.get("consequence_text") or "The outcome depends on the world's mechanical state and participant actions.")]
        if self.event_key:
            rows=await DB.list_world_event_participants(self.event_key,limit=100)
            if rows:
                lines.append(f"\nRecorded cultivators: **{len(rows)}** • net contribution **{sum(int(x.get('contribution',0)) for x in rows):+d}** • support **{sum(int(x.get('support',0)) for x in rows)}** • interference **{sum(int(x.get('interference',0)) for x in rows)}**")
            actions=await DB.get_world_event_actions(self.event_key,limit=5)
            if actions:
                lines.append("\n**Recent actions**")
                lines.extend(f"• {a.get('character_name','Cultivator')}: {str(a.get('action_key','')).replace('_',' ').title()} — {'success' if int(a.get('success',0)) else 'failure'}" for a in actions)
        await reply_long(interaction,"\n".join(lines),ephemeral=False)

    @discord.ui.button(label="Talk",emoji="💬",style=discord.ButtonStyle.success,row=1)
    async def talk_to_npc(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        c=await self._character_here(interaction)
        if c is None:return
        regional=await SIM.civilization_status(str(c.get("location") or "")); names=[str(x.get("npc_name") or "") for x in (regional or {}).get("npcs",[]) if x.get("npc_name")]
        if not names:
            await interaction.response.send_message("No named persistent NPC is mechanically present here right now.",ephemeral=False);return
        await interaction.response.send_message("💬 Choose someone present in this event scene.",view=EventNpcSelectView(names),ephemeral=False)

    @discord.ui.button(label="Investigate",emoji="🔎",style=discord.ButtonStyle.primary,row=2)
    async def investigate(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.run_event_action(interaction,"investigate")

    @discord.ui.button(label="Battle",emoji="⚔️",style=discord.ButtonStyle.danger,row=2)
    async def battle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        c=await self._character_here(interaction)
        if c is None:return
        if await DB.get_active_battle(interaction.user.id):
            await EVENT_HANDLERS.invoke("battle", interaction); return
        text=f"{self.category} {self.event_type}".casefold()
        if not any(x in text for x in ("beast","demon","invasion","tide","attack","war","calamity")):
            await interaction.response.send_message("No event-specific hostile manifestation is currently forcing a battle here. Use **Systems** to inspect other combat mechanics.",ephemeral=False);return
        ri=max(0,int(c.get("realm_index",0))+max(0,self.severity-5)//3); stage=max(1,min(9,int(c.get("phase",1))+max(0,self.severity-4)//2))
        hp=max(12,14+ri*5+stage*2+self.severity*2); source=f"event:{self.event_key or self.title}"
        bid=await DB.create_battle(user_id=interaction.user.id,npc_name=f"{self.title} — hostile manifestation",npc_realm_index=ri,npc_stage=stage,player_hp=max(1,int(c.get("vitality",1))),player_hp_max=max(1,int(c.get("vitality_max",c.get("vitality",1)))),npc_hp=hp,location=str(c.get("location") or ""),source=source,target_key=f"{source}:{interaction.user.id}")
        battle=await DB.get_active_battle(interaction.user.id); embed,view=await _battle_panel(interaction.user.id,c,battle or {"battle_id":bid,"npc_name":self.title,"npc_realm_index":ri,"npc_stage":stage,"player_hp":c.get("vitality",1),"player_hp_max":c.get("vitality_max",1),"npc_hp":hp,"npc_hp_max":hp,"location":c.get("location","")})
        await interaction.response.send_message(content=f"⚔️ **Event confrontation #{bid} begins.** Defeating this manifestation contributes to the event; it is not a persistent NPC life.",embed=embed,view=view)

    @discord.ui.button(label="Scene Action",emoji="🎭",style=discord.ButtonStyle.success,row=2)
    async def scene_action(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._open_scene_actions(interaction,default_action="observe")

    @discord.ui.button(label="Refresh",emoji="🔄",style=discord.ButtonStyle.secondary,row=2)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=self.embed(),view=self)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
        await _report_game_ui_error(interaction,error,where=f"event-scene:{type(item).__name__}")

    async def on_timeout(self) -> None:
        for item in self.children:item.disabled=True


async def _event_scene_location(event_key: str | None, fallback_user_id: int | None = None) -> str | None:
    if event_key:
        try:
            for event in await DB.get_active_world_events():
                if str(event.get("event_key") or "") == str(event_key):
                    location = str(event.get("location") or "").strip()
                    if location:
                        return location
        except Exception:
            log.exception("Could not resolve location for event scene %s", event_key)
    if fallback_user_id is not None:
        character = await DB.get_character(int(fallback_user_id))
        if character:
            location = str(character.get("location") or "").strip()
            if location:
                return location
    return None


async def _event_scene_profile(event_key: str | None, event_type: str) -> tuple[str, int]:
    if event_key:
        try:
            row = await DB.get_world_event(str(event_key))
            if row:
                payload = dict(row.get("payload") or {})
                return (str(payload.get("category") or event_type or "Event"), max(1, min(10, int(payload.get("severity") or 1))))
        except Exception:
            log.exception("Could not resolve event profile for %s", event_key)
    return (str(event_type or "Event").replace("_", " ").title(), 1)


async def spawn_event_thread(
    interaction: discord.Interaction, *, title: str, announcement: str, event_type: str,
    expires_at: float, event_key: str | None = None
) -> discord.Thread | None:
    announcement_channel, scene_channel = await event_channels(interaction)
    if scene_channel is None:
        log.warning("No usable event-scenes channel found for event: %s", title)
        return None

    # Create the scene in the dedicated scene channel first so the announcement can link to it.
    try:
        scene_message = await scene_channel.send(
            f"🌌 **EVENT SCENE — {title}**\n"
            f"Triggered/discovered by {interaction.user.mention}. A dedicated thread is opening here."
        )
        thread = await scene_message.create_thread(
            name=(f"🌌 {title}")[:100],
            auto_archive_duration=_event_archive_minutes(),
            reason=f"Xianxia event scene: {title}",
        )
    except discord.Forbidden:
        log.exception("Missing permission to create event thread for %s", title)
        return None
    except discord.HTTPException:
        log.exception("Discord failed to create event thread for %s", title)
        return None

    announcement_message: discord.Message | None = None
    if announcement_channel is not None:
        try:
            announcement_message = await announcement_channel.send(
                f"{announcement}\n\n💬 **Event scene:** {thread.mention}"
            )
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Could not post event announcement for %s", title)

    try:
        await DB.register_event_thread(
            thread_id=thread.id,
            event_key=event_key,
            event_type=event_type,
            title=title,
            channel_id=scene_channel.id,
            message_id=scene_message.id,
            announcement_channel_id=announcement_channel.id if announcement_channel else None,
            announcement_message_id=announcement_message.id if announcement_message else None,
            triggered_by=interaction.user.id,
            expires_at=expires_at,
        )
    except Exception:
        log.exception("Could not persist event thread metadata for %s", title)

    try:
        event_location = await _event_scene_location(event_key, fallback_user_id=interaction.user.id)
        event_category, event_severity = await _event_scene_profile(event_key, event_type)
        event_view = EventSceneView(
            title=title, event_type=event_type, expires_at=expires_at, location=event_location,
            event_key=event_key, category=event_category, severity=event_severity,
        )
        await thread.send(
            content=f"{interaction.user.mention} opened this live event scene.",
            embed=event_view.embed(),
            view=event_view,
        )
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Could not send the event-thread starter panel for %s", title)

    return thread


async def spawn_system_event_thread(
    guild: discord.Guild, *, title: str, announcement: str, event_type: str, expires_at: float, event_key: str
) -> discord.Thread | None:
    config = await DB.get_server_config(guild.id)
    announcement_channel = await _resolve_text_channel(guild, config.get("announcement_channel_id"))
    scene_channel = await _resolve_text_channel(guild, config.get("event_scene_channel_id"))
    if scene_channel is None:
        return None
    try:
        scene_message = await scene_channel.send(f"🌌 **AUTONOMOUS WORLD EVENT — {title}**\nThe living world produced this event without a player trigger.")
        thread = await scene_message.create_thread(
            name=(f"🌌 {title}")[:100], auto_archive_duration=_event_archive_minutes(), reason=f"Autonomous Xianxia event: {title}"
        )
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Could not create autonomous event thread for %s", title); return None
    announcement_message=None
    if announcement_channel:
        try: announcement_message=await announcement_channel.send(f"{announcement}\n\n💬 **Event scene:** {thread.mention}")
        except (discord.Forbidden, discord.HTTPException): log.exception("Could not announce autonomous event %s",title)
    await DB.register_event_thread(
        thread_id=thread.id,event_key=event_key,event_type=event_type,title=title,channel_id=scene_channel.id,
        message_id=scene_message.id,announcement_channel_id=announcement_channel.id if announcement_channel else None,
        announcement_message_id=announcement_message.id if announcement_message else None,triggered_by=None,expires_at=expires_at,
    )
    try:
        event_location = await _event_scene_location(event_key)
        event_category, event_severity = await _event_scene_profile(event_key, event_type)
        event_view = EventSceneView(
            title=title, event_type=event_type, expires_at=expires_at, location=event_location,
            event_key=event_key, category=event_category, severity=event_severity,
        )
        await thread.send(
            content="**The world moves on its own.** Travel to the event location to participate.",
            embed=event_view.embed(),
            view=event_view,
        )
    except (discord.Forbidden,discord.HTTPException):
        log.exception("Could not send autonomous event starter panel for %s", title)
    return thread


async def authoritative_lifespan(user_id: int) -> SimpleNamespace:
    result = await ENGINE.action("character.lifespan", int(user_id), {})
    return SimpleNamespace(**dict(result or {}))


async def _record_true_death_history(user_id: int, death: dict[str, Any], game_minute: int) -> None:
    """Persist descriptive world history after Go commits the lifecycle transition."""
    name = str(death.get("name") or "A cultivator")
    location = str(death.get("location") or "")
    reason = str(death.get("reason") or "unknown")
    target_world = str(death.get("target_world") or "Mortal World")
    try:
        await DB.record_world_history_event(
            event_type="death",
            title=f"True death of {name}",
            summary=f"{name} suffered true death at {location or 'an unknown place'}. Recorded cause: {reason}. The soul entered Samsara toward {target_world}.",
            significance=92,
            visibility="participant",
            location=location,
            actor_type="player",
            actor_key=str(int(user_id)),
            actor_name=name,
            target_type="life",
            target_key=str(int(user_id)),
            target_name=name,
            related_user_id=int(user_id),
            tags=("death", "true death", "samsara", "reincarnation"),
            game_minute=int(game_minute),
            metadata={"reason": reason, "target_world": target_world, "previous_realm_index": int(death.get("previous_realm_index", 0))},
            source_key=f"player_death:{int(user_id)}:{int(game_minute)}",
        )
    except Exception:
        # History is descriptive; it must never roll back or counterfeit the already
        # committed authoritative life-state transition.
        log.exception("Could not mirror authoritative true death into world history")


async def require_character(interaction: discord.Interaction, *, allow_deceased: bool = False) -> dict | None:
    character = await DB.get_character(interaction.user.id)
    if character is None:
        await interaction.response.send_message(
            "You do not have a cultivator yet. Use **/begin** first.",
            ephemeral=False,
        )
        return None
    wt = await current_world_time()
    life = await authoritative_lifespan(interaction.user.id)
    if not life.ageless and life.total_years is not None and life.age_years >= life.total_years:
        if character.get("life_status") != "deceased":
            try:
                envelope = await ENGINE.authoritative_action(
                    "lifecycle.true_death",
                    interaction.user.id,
                    {
                        
                        "reason": "old_age",
                        "minutes_per_year": MINUTES_PER_YEAR,
                        "base_samsara_years": SETTINGS.reincarnation_base_samsara_years,
                        "max_wait_seconds": SETTINGS.reincarnation_max_wait_seconds,
                    },
                    action_id=f"lifecycle:old-age:{interaction.user.id}:{wt.total_minutes}",
                )
            except GameEngineError as exc:
                await interaction.response.send_message(f"❌ Lifecycle authority rejected the old-age transition: {exc}", ephemeral=False)
                return None
            death = dict(envelope.get("result") or {})
            # The descriptive history mirror is source-key idempotent, so replay it
            # too: this repairs a prior best-effort history write without duplicating
            # or changing the already-authoritative lifecycle transition.
            await _record_true_death_history(interaction.user.id, death, wt.total_minutes)
            character["life_status"] = "deceased"
    if character.get("life_status") == "deceased" and not allow_deceased:
        await interaction.response.send_message(
            f"🕯️ **{character['name']}** is dead. Their old family remains in world history, but the soul has entered Samsara. "
            "Use **/character → Samsara** to view the soul-cycle and **/character → Reincarnate** when rebirth opens.",
            ephemeral=False,
        )
        return None
    try:
        await _sync_realm_access_roles(interaction.guild, interaction.user, character)
    except Exception:
        log.exception("Realm access role synchronization failed")
    return character

async def _report_game_ui_error(
    interaction: discord.Interaction,
    error: Exception,
    *,
    where: str,
    ephemeral: bool = False,
) -> None:
    log.error(
        "Discord UI callback failed: %s", where,
        exc_info=(type(error), error, error.__traceback__),
    )
    await post_server_log(
        interaction.guild,
        "Discord UI callback failed",
        f"{where} — {type(error).__name__}: {str(error)[:900]}",
    )
    text = "❌ This interface hit an unexpected error. No extra UI-side game rule was applied."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(text, ephemeral=ephemeral)
    except discord.HTTPException:
        log.exception("Could not deliver Discord UI failure response")


class CharacterModal(discord.ui.Modal):
    """Final text-only form after family and cultivation path are chosen.

    Discord modals cannot contain select menus, so all categorical creation
    choices happen before this form. The player's Spiritual Root is never typed:
    it is rolled at submission from family archetype, homeland, family standing
    and hidden lineage data, then revealed on the completed character sheet.
    """

    def __init__(self, birth_family: dict[str, Any], selected_style: str, selected_gender: str, offer_state_version: int):
        self.birth_family = dict(birth_family)
        self.selected_style = selected_style if selected_style in WORLD.paths else next(iter(WORLD.paths))
        self.selected_gender = selected_gender if selected_gender in {"male", "female"} else "male"
        self.offer_state_version = int(offer_state_version)
        location = str(self.birth_family.get("location") or WORLD.starting_location)
        family_name = str(self.birth_family.get("family_name") or "Family")
        super().__init__(title=f"{family_name} • {location}"[:45])

        profile = cultivation_style_profile(self.selected_style)
        self.name_input = discord.ui.TextInput(
            label="Character name",
            placeholder="e.g. Shen Rui",
            min_length=1,
            max_length=40,
        )
        self.concept_input = discord.ui.TextInput(
            label="Personal Dao / goal",
            placeholder=f"What does this {profile['emoji']} {self.selected_style} seek, fear or protect?",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=300,
        )
        for field in (self.name_input, self.concept_input):
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        normalized_path = WORLD.normalize_path(self.selected_style)
        if not normalized_path:
            await interaction.response.send_message(
                "That cultivation path is no longer available. Use **/begin** again.", ephemeral=True
            )
            return

        family = self.birth_family
        wt = await current_world_time()
        creation_envelope = await ENGINE.authoritative_action(
            "character.create",
            interaction.user.id,
            {
                "discord_name": interaction.user.display_name,
                "name": str(self.name_input.value).strip(),
                "concept": str(self.concept_input.value).strip(),
                "gender": self.selected_gender,
                "path": normalized_path,
                "family_choice_id": str(family.get("choice_id") or ""),
                
                "age_at_creation_years": 18,
            },
            action_id=f"discord:{interaction.id}:character.create",
            expected_version=self.offer_state_version,
        )
        creation = dict(creation_envelope.get("result") or {})
        created = bool(creation.get("created"))
        if not created:
            await interaction.response.send_message(
                "You already have a character. Use **/character → Overview** to view it.",
                ephemeral=True,
            )
            return
        try:
            await ENGINE.bootstrap_simulation(wt.total_minutes)
        except Exception:
            log.exception("Could not initialize Go-owned simulation bootstrap after character creation")

        name = str(creation.get("name") or self.name_input.value).strip()
        location = str(creation.get("location") or family.get("location") or WORLD.starting_location)
        normalized_root = str(creation.get("spiritual_root") or "Mortal Root")
        natural_lifespan = int(creation.get("natural_lifespan_years") or 75)
        aptitude_profile = dict(creation.get("aptitudes") or {})
        theme = location_theme(location)
        style_profile = cultivation_style_profile(normalized_path)
        location_description = str(WORLD.locations.get(location, {}).get("description") or theme.get("mood") or "")
        embed = discord.Embed(
            title=f"{theme['emoji']} {name} — First Step on the Dao",
            description=origin_vignette(family, normalized_path, normalized_root, self.selected_gender),
            color=int(theme["color"]),
        )
        embed.add_field(
            name=f"{family_emoji(family)} Birth Family",
            value=(
                f"**{family['family_name']} — {family['name']}**\n"
                f"{family_tier_name(int(family['tier']))} • **{location}**\n"
                f"{family_status_summary(family)}\n"
                f"{location_description[:320]}"
            ),
            inline=False,
        )
        embed.add_field(
            name="🧬 Birth Sex",
            value=f"**{self.selected_gender.title()}**",
            inline=True,
        )
        embed.add_field(
            name="☯️ Chosen Cultivation Path",
            value=f"{style_profile['emoji']} **{normalized_path}**\n{style_profile['summary']}\nFocus: {style_profile['focus']}",
            inline=True,
        )
        root_elements = ", ".join(str(x) for x in aptitude_profile["root"].get("elements", [normalized_root]))
        embed.add_field(
            name="💠 Heaven-Rolled Spiritual Root",
            value=(
                f"**{aptitude_profile['root']['grade']} {normalized_root}**\n"
                f"Purity **{aptitude_profile['root']['purity']}%** • Elements: **{root_elements[:120]}**\n"
                "Rolled from family lineage and homeland; never player-selected."
            ),
            inline=True,
        )
        if str(self.concept_input.value).strip():
            embed.add_field(name="🧭 Personal Dao", value=str(self.concept_input.value).strip()[:900], inline=False)
        embed.add_field(
            name="🌱 Starting State",
            value=(
                f"**{WORLD.realm_name(0, self.selected_gender)}, Stage 1** • Age **18** • Natural mortal lifespan **{natural_lifespan} years**\n"
                "Your family, location, household standing, rolled root and chosen path are now part of the AI narrator context."
            ),
            inline=False,
        )
        embed.add_field(
            name="Next steps",
            value=(
                "**/character → Overview** • **/family → View** • **/cultivation → Meditation**\n"
                "**/world → Explore** • **/npc → Talk** • **/action**"
            ),
            inline=False,
        )
        embed.set_footer(text="The authoritative game engine owns mechanics; AI only narrates validated canonical results.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

        # Character creation is the natural point to establish the player's
        # persistent private scene. Previously this was delayed until the first
        # exploration or /action, which made a successful /begin look incomplete.
        expedition_thread: discord.Thread | None = None
        try:
            character = await DB.get_character(interaction.user.id)
            if character is not None:
                expedition_thread = await ensure_expedition_thread(interaction, character)
        except Exception:
            log.exception("Could not provision private expedition thread after character creation")
        if expedition_thread is not None:
            await interaction.followup.send(
                f"🧭 Your private expedition journal is ready: {expedition_thread.mention}\n"
                "Continue there with **/world → Explore** or **/action**.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "⚠️ Your cultivator was created, but Discord did not create the private expedition thread. "
                "Ask an administrator to run **/admin → Server → Setup Server** and confirm the bot has "
                "**Create Private Threads**, **Send Messages in Threads**, and **Manage Threads**.",
                ephemeral=True,
            )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await _report_game_ui_error(
            interaction,
            error,
            where="character-creation-modal",
            ephemeral=True,
        )


def _birth_family_preview_embed(
    family: dict[str, Any],
    *,
    index: int,
    total: int,
    selected_style: str | None = None,
    selected_gender: str | None = None,
    stage: str = "family",
) -> discord.Embed:
    """Render exactly one family card at a time."""
    location = str(family.get("location") or "Unknown")
    theme = location_theme(location)
    recommended = recommended_cultivation_styles(family)
    rec_text = " • ".join(
        f"{cultivation_style_profile(path)['emoji']} **{path}**" for path in recommended
    ) or "Any cultivation style can emerge from this household."
    tendencies = family_root_tendencies(family)
    root_text = " • ".join(f"**{root}**" for root in tendencies) or "No strong elemental tendency"
    location_description = str(WORLD.locations.get(location, {}).get("description") or theme.get("mood") or "Unknown homeland")

    embed = discord.Embed(
        title=f"{family_emoji(family)} Family {index + 1}/{total} — {family['family_name']}",
        description=(
            f"**{family['name']}**\n"
            f"{theme['emoji']} **{location}** — {location_description[:420]}"
        ),
        color=int(theme["color"]),
    )
    embed.add_field(
        name="Family standing",
        value=(
            f"**{family_tier_name(int(family.get('tier', 1)))}**\n"
            f"{family_status_summary(family)}\n"
            f"Wealth **{family.get('wealth', 0)}** • Influence **{family.get('influence', 0)}** • Stability **{family.get('stability', 0)}**"
        ),
        inline=False,
    )
    embed.add_field(name="Upbringing / help", value=str(family.get("boon", "None"))[:650], inline=False)
    embed.add_field(name="Pressure / risk", value=str(family.get("risk", "None"))[:650], inline=False)
    embed.add_field(
        name="💠 Spiritual-root tendencies",
        value=(
            f"Weighted toward {root_text}. **The actual root is rolled only when the character is created.** "
            "Every canonical root remains possible."
        )[:1000],
        inline=False,
    )
    embed.add_field(name="Recommended cultivation paths", value=rec_text[:1000], inline=False)
    if str(family.get("id", "")) == "alchemy_family":
        embed.add_field(
            name="⚗️ Inherited Alchemy Tradition",
            value="+2 Alchemy refinement checks and +2 medicinal-herb foraging checks.",
            inline=False,
        )
    if selected_style:
        profile = cultivation_style_profile(selected_style)
        embed.add_field(
            name="Selected cultivation path",
            value=f"{profile['emoji']} **{selected_style}** — {profile['summary']}",
            inline=False,
        )
    if selected_gender:
        embed.add_field(
            name="Birth sex",
            value=f"**{selected_gender.title()}**",
            inline=True,
        )

    if stage == "family":
        embed.set_footer(text="Browse one family at a time with Previous/Next, then press Choose Family.")
    else:
        embed.set_footer(text="Family locked. Choose a cultivation path and birth sex from the dropdowns, or go back to change family.")
    return embed


class BirthFamilyPreviousButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Previous", style=discord.ButtonStyle.secondary, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, BirthFamilyView):
            return
        view.current_index = (view.current_index - 1) % len(view.families)
        await interaction.response.edit_message(embed=view.current_embed(), view=view)


class BirthFamilyChooseButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Choose Family", style=discord.ButtonStyle.success, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, BirthFamilyView):
            return
        view.selected_index = view.current_index
        view.selected_style = None
        view.selected_gender = None
        view.stage = "style"
        view.rebuild_components()
        await interaction.response.edit_message(embed=view.current_embed(), view=view)


class BirthFamilyNextButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Next", style=discord.ButtonStyle.secondary, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, BirthFamilyView):
            return
        view.current_index = (view.current_index + 1) % len(view.families)
        await interaction.response.edit_message(embed=view.current_embed(), view=view)


class CultivationStyleSelect(discord.ui.Select):
    def __init__(self, family: dict[str, Any], selected_style: str | None):
        preferred = list(recommended_cultivation_styles(family))
        ordered = preferred + [path for path in WORLD.paths if path not in preferred]
        options = []
        for path in ordered:
            profile = cultivation_style_profile(path)
            marker = "Recommended • " if path in preferred else ""
            options.append(discord.SelectOption(
                label=path,
                value=path,
                description=(marker + profile["summary"])[:100],
                emoji=profile["emoji"],
                default=path == selected_style,
            ))
        super().__init__(placeholder="Choose your cultivation path", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, BirthFamilyView) or view.selected_index is None:
            await interaction.response.send_message("Choose a family first.", ephemeral=True)
            return
        view.selected_style = str(self.values[0])
        view.rebuild_components()
        await interaction.response.edit_message(embed=view.current_embed(), view=view)


class BirthSexSelect(discord.ui.Select):
    def __init__(self, selected_gender: str | None):
        options = [
            discord.SelectOption(label="Male", value="male", description="Born male", default=selected_gender == "male"),
            discord.SelectOption(label="Female", value="female", description="Born female", default=selected_gender == "female"),
        ]
        super().__init__(placeholder="Choose birth sex", min_values=1, max_values=1, options=options, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, BirthFamilyView) or view.selected_index is None:
            await interaction.response.send_message("Choose a family first.", ephemeral=True)
            return
        view.selected_gender = str(self.values[0])
        view.rebuild_components()
        await interaction.response.edit_message(embed=view.current_embed(), view=view)


class BirthFamilyBackButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Change Family", style=discord.ButtonStyle.secondary, row=2)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, BirthFamilyView):
            return
        if view.selected_index is not None:
            view.current_index = view.selected_index
        view.selected_index = None
        view.selected_style = None
        view.selected_gender = None
        view.stage = "family"
        view.rebuild_components()
        await interaction.response.edit_message(embed=view.current_embed(), view=view)


class BirthFamilyConfirmButton(discord.ui.Button):
    def __init__(self, *, disabled: bool = True) -> None:
        super().__init__(label="Open Character Form", style=discord.ButtonStyle.success, disabled=disabled, row=2)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, BirthFamilyView) or view.selected_index is None:
            await interaction.response.send_message("Choose a family first.", ephemeral=True)
            return
        if not view.selected_style:
            await interaction.response.send_message("Choose a cultivation path from the dropdown first.", ephemeral=True)
            return
        if view.selected_gender not in {"male", "female"}:
            await interaction.response.send_message("Choose Male or Female from the birth-sex dropdown first.", ephemeral=True)
            return
        await interaction.response.send_modal(CharacterModal(
            view.families[view.selected_index], view.selected_style, view.selected_gender, view.offer_state_version
        ))


class BirthFamilyView(discord.ui.View):
    def __init__(self, user_id: int, families: list[dict[str, Any]], offer_state_version: int):
        super().__init__(timeout=300)
        self.user_id = int(user_id)
        self.families = list(families)
        self.offer_state_version = int(offer_state_version)
        self.current_index = 0
        self.selected_index: int | None = None
        self.selected_style: str | None = None
        self.selected_gender: str | None = None
        self.stage = "family"
        self.rebuild_components()

    def current_embed(self) -> discord.Embed:
        if not self.families:
            return discord.Embed(title="No birth families available", color=0xAA0000)
        idx = self.selected_index if self.stage == "style" and self.selected_index is not None else self.current_index
        return _birth_family_preview_embed(
            self.families[idx],
            index=idx,
            total=len(self.families),
            selected_style=self.selected_style,
            selected_gender=self.selected_gender,
            stage=self.stage,
        )

    def rebuild_components(self) -> None:
        self.clear_items()
        if self.stage == "family":
            self.add_item(BirthFamilyPreviousButton())
            self.add_item(BirthFamilyChooseButton())
            self.add_item(BirthFamilyNextButton())
            return
        if self.selected_index is not None:
            self.add_item(CultivationStyleSelect(self.families[self.selected_index], self.selected_style))
            self.add_item(BirthSexSelect(self.selected_gender))
        self.add_item(BirthFamilyBackButton())
        self.add_item(BirthFamilyConfirmButton(
            disabled=not self.selected_style or self.selected_gender not in {"male", "female"}
        ))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("These character-creation choices belong to another player.", ephemeral=True)
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
        await _report_game_ui_error(
            interaction,
            error,
            where=f"character-creation:{type(item).__name__}",
            ephemeral=True,
        )


class XianxiaBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.messages = True
        intents.message_content = SETTINGS.message_content_intent
        super().__init__(
            command_prefix="!unused-",
            intents=intents,
            # User-supplied character names/RP text must never be able to turn
            # stored/generated text into real Discord notifications.
            allowed_mentions=discord.AllowedMentions(
                everyone=False,
                roles=False,
                users=False,
                replied_user=False,
            ),
        )
        self.health_state = HealthState(supported_schema_version=SCHEMA_VERSION)
        control_token = os.getenv("BOT_CONTROL_TOKEN", "").strip() or os.getenv("DASHBOARD_TOKEN", "").strip()
        self.health_server = HealthServer(
            self.health_state,
            host=SETTINGS.health_host,
            port=SETTINGS.health_port,
            control_handler=self._dashboard_discord_control if control_token else None,
            control_token=control_token,
        )
        self.operational_health_task: asyncio.Task | None = None

    async def _dashboard_discord_control(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        # The handler is intentionally hosted by the Discord process. The GM
        # dashboard can request Discord setup work, but only discord.py owns
        # guild/channel/role mutations. Game mechanics remain in Go.
        return await dashboard_discord_control(self, action, payload)

    async def _mark_startup_phase(self, phase: str, detail: dict | None = None) -> None:
        first_ready = self.health_state.mark_phase(phase, detail=detail or {})
        if first_ready:
            try:
                await DB.record_startup_event(
                    self.health_state.boot_id, phase, status="ready", detail=detail or {}
                )
            except Exception:
                # Health-state persistence must never invalidate an otherwise
                # healthy startup after the database itself has been verified.
                log.exception("Could not persist startup phase %s", phase)

    async def setup_hook(self) -> None:
        # Persistent read-only guide controls survive process/container restarts.
        self.add_view(XianxiaInfoView())
        log.info("XIANXIA_STARTUP version=%s schema_version=%s", RELEASE_VERSION, SCHEMA_VERSION)
        phase = "HEALTH_SERVER"
        try:
            await self.health_server.start()

            phase = "DATABASE_READY"
            await DB.init()
            schema = await DB.get_schema_status()
            if not schema.get("compatible"):
                raise RuntimeError(
                    f"Schema mismatch: current={schema.get('current')} supported={schema.get('supported')}"
                )
            self.health_state.set_schema_version(int(schema["current"]))
            db_probe = await DB.operational_health()
            self.health_state.set_check("database", bool(db_probe.get("ok")), **{k: v for k, v in db_probe.items() if k != "ok"})
            await self._mark_startup_phase(
                "DATABASE_READY",
                {
                    "release_version": RELEASE_VERSION,
                    "schema_version": int(schema["current"]),
                    "migrations": int(schema["migrations"]),
                    "journal_mode": db_probe.get("journal_mode", "unknown"),
                },
            )

            phase = "CATALOG_READY"
            await DB.sync_world_catalog(WORLD.data)
            await DB.sync_rag_canon(WORLD.data)
            catalog_counts = await DB.catalog_counts()
            rag_counts = await DB.rag_stats()
            await self._mark_startup_phase("CATALOG_READY", {**catalog_counts, **{f"rag_{k}": v for k, v in rag_counts.items()}})

            phase = "SIMULATION_READY"
            wt_state = await DB.get_world_clock(scale=SETTINGS.world_time_scale)
            await SIM.initialize(int(wt_state["game_minute"]))
            await self._mark_startup_phase(
                "SIMULATION_READY", {"game_minute": int(wt_state["game_minute"])}
            )

            phase = "COMMAND_SYNC"
            synced = await self.tree.sync(guild=GUILD)
            log.info("COMMANDS_READY count=%s guild=%s", len(synced), SETTINGS.guild_id)
            self.event_expiry_task = asyncio.create_task(self.event_expiry_worker())
            self.operational_health_task = asyncio.create_task(self.operational_health_worker())
        except Exception as exc:
            self.health_state.fail(phase, exc)
            failure_detail = {
                "phase": phase,
                "boot_id": self.health_state.boot_id,
                "type": type(exc).__name__,
                "message": str(exc),
            }
            delivered = await ALERTS.send(
                "startup_failed",
                f"Bot startup failed during {phase}: {exc}",
                severity="critical",
                details=failure_detail,
            )
            if self.health_state.phases["DATABASE_READY"].ready:
                try:
                    await DB.record_startup_event(
                        self.health_state.boot_id, phase, status="failed", detail=failure_detail,
                    )
                    await DB.record_operational_alert(
                        "startup_failed", severity="critical",
                        message=f"Bot startup failed during {phase}: {exc}",
                        detail=failure_detail, delivered=delivered,
                    )
                except Exception:
                    log.exception("Could not persist startup failure")
            raise

    async def operational_health_worker(self) -> None:
        try:
            while not self.is_closed():
                probe = await DB.operational_health()
                self.health_state.set_check(
                    "database", bool(probe.get("ok")),
                    **{k: v for k, v in probe.items() if k != "ok"},
                )
                flushed = await DB.flush_slow_query_log()
                obs = await DB.observability_snapshot()
                for key in (
                    "query_count", "slow_query_count", "recent_slow_queries_1h", "max_query_latency_ms",
                    "connections_opened", "connections_reused", "writer_wait_count", "writer_wait_ms",
                    "catalog_cache_entries", "catalog_cache_hits", "catalog_cache_misses",
                ):
                    self.health_state.set_metric(key, float(obs.get(key, 0)))
                try:
                    engine_status = await ENGINE.database_status()
                    self.health_state.set_check("game_engine", True, **engine_status)
                    self.health_state.set_metric("go_engine_requests", float(engine_status.get("requests", 0)))
                except Exception as exc:
                    self.health_state.set_check("game_engine", False, error=str(exc))
                if not probe.get("ok"):
                    detail = {k: v for k, v in probe.items() if k != "ok"}
                    delivered = await ALERTS.send("database_degraded", "SQLite operational health probe failed", severity="critical", details=detail)
                    if probe.get("schema_intact") is False:
                        # The most common cause is an operator removing the
                        # SQLite file while this process is still alive.  Stop
                        # cleanly so Docker's restart policy runs DB.init()
                        # before Discord commands can touch the replacement.
                        log.critical(
                            "DATABASE_SCHEMA_LOST missing_tables=%s; closing for automatic recovery",
                            ",".join(str(name) for name in probe.get("missing_tables", [])),
                        )
                        self.health_state.clear_phase(
                            "DATABASE_READY", reason="required SQLite tables disappeared"
                        )
                        asyncio.create_task(self.close())
                        return
                    await DB.record_operational_alert("database_degraded", severity="critical", message="SQLite operational health probe failed", detail=detail, delivered=delivered)
                if int(obs.get("recent_slow_queries_1h", 0)) >= 5:
                    detail = {"recent_slow_queries_1h": obs.get("recent_slow_queries_1h"), "max_query_latency_ms": obs.get("max_query_latency_ms"), "threshold_ms": obs.get("slow_query_threshold_ms"), "flushed": flushed}
                    delivered = await ALERTS.send("slow_query_pressure", "Slow-query pressure exceeded the operational threshold", severity="warning", details=detail)
                    await DB.record_operational_alert("slow_query_pressure", severity="warning", message="Slow-query pressure exceeded the operational threshold", detail=detail, delivered=delivered)
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.exception("Operational health worker failed")
            self.health_state.set_check("database", False, error="health worker failed")
            delivered = await ALERTS.send("health_worker_failed", str(exc), severity="critical")
            try:
                await DB.record_operational_alert("health_worker_failed", severity="critical", message=str(exc), delivered=delivered)
            except Exception:
                log.exception("Could not persist health-worker alert")

    async def close_event_scene(self, record: dict, *, manual: bool = False) -> None:
        guild = self.get_guild(SETTINGS.guild_id)
        if guild is None:
            return

        thread: discord.Thread | None = guild.get_thread(int(record["thread_id"]))
        if thread is None:
            try:
                fetched = await guild.fetch_channel(int(record["thread_id"]))
                if isinstance(fetched, discord.Thread):
                    thread = fetched
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                thread = None

        closing = (
            "🌀 **The secret realm is closing.** Spatial cracks seal one after another, and all remaining "
            "cultivators are expelled before the entrance vanishes."
            if record.get("event_type") == "secret_realm"
            else "🔒 **This event has ended.** The scene is now closed."
        )
        if manual:
            closing += "\n*Closed early by an administrator.*"

        if thread is not None:
            try:
                if thread.archived:
                    await thread.edit(archived=False, reason="Post Xianxia event closing message")
                await thread.send(closing)
            except (discord.Forbidden, discord.HTTPException):
                log.exception("Could not post closing message in thread %s", record["thread_id"])
            try:
                await thread.edit(locked=True, archived=True, reason="Xianxia event expired")
            except discord.Forbidden:
                # Locking requires Manage Threads. Archiving may still be allowed for a bot-owned thread.
                try:
                    await thread.edit(archived=True, reason="Xianxia event expired")
                except (discord.Forbidden, discord.HTTPException):
                    log.exception("Could not archive event thread %s", record["thread_id"])
            except discord.HTTPException:
                log.exception("Could not close event thread %s", record["thread_id"])

        event_key = record.get("event_key")
        if event_key:
            try:
                wt = await current_world_time()
                await DB.finalize_world_event_history(str(event_key), game_minute=wt.total_minutes)
            except Exception:
                log.exception("Could not finalize event participation history for %s", event_key)
        await DB.close_event_thread(int(record["thread_id"]), event_key)

        announcement_channel_id = record.get("announcement_channel_id")
        if announcement_channel_id:
            channel = guild.get_channel(int(announcement_channel_id))
            if isinstance(channel, discord.TextChannel):
                try:
                    label = "Secret realm" if record.get("event_type") == "secret_realm" else "World event"
                    await channel.send(f"🔒 **{label} closed — {record['title']}**")
                except (discord.Forbidden, discord.HTTPException):
                    pass

    async def event_expiry_worker(self) -> None:
        await self.wait_until_ready()
        try:
            while not self.is_closed():
                try:
                    automation = await DB.get_automation_settings()
                    if automation.get("event_expiry", True):
                        for record in await DB.get_expired_event_threads():
                            await self.close_event_scene(record)
                    wt = await current_world_time()
                    # Autonomous world-event selection, activation, RNG and persistent consequences are Go-owned.
                    simulation_runs = await SIM.run_due(wt.total_minutes, automation)
                    for sim_run in simulation_runs:
                        log.info("World simulation %s: %s", sim_run.system, sim_run.summary)
                        for event in sim_run.events:
                            guild=self.get_guild(SETTINGS.guild_id)
                            if not guild: continue
                            impacts=[str(x) for x in list(event.get("impacts") or [])]
                            consequence=str(event.get("consequence_text") or "").strip()
                            await spawn_system_event_thread(
                                guild,title=str(event.get("title") or "World Event"),event_type="random_event",
                                event_key=str(event.get("event_key") or ""),expires_at=float(event.get("expires_at") or time.time()+7200),
                                announcement=(
                                    f"⚡ **AUTONOMOUS WORLD EVENT — {event.get('title','World Event')}**\n📍 **{event.get('location','Unknown')}**\n{event.get('description','')}"
                                    + (f"\n\n**Persistent consequence:** {consequence}" if consequence else "")
                                    + (f"\n**Systems changed:** {'; '.join(impacts)}" if impacts else "")
                                ),
                            )
                    if automation.get("maintenance_cleanup", True) and int(time.time()) % 3600 < 30:
                        await DB.maintenance_cleanup(wt.total_minutes)
                except Exception:
                    log.exception("Event expiry worker failed")
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            pass

    async def close(self) -> None:
        for task_name in ("event_expiry_task", "operational_health_task"):
            task = getattr(self, task_name, None)
            if task:
                task.cancel()
        self.health_state.clear_phase("DISCORD_READY", reason="shutdown")
        self.health_state.set_check("discord_gateway", False, reason="shutdown")
        await self.health_server.stop()
        await super().close()

    async def on_ready(self) -> None:
        detail = {
            "user": str(self.user),
            "user_id": getattr(self.user, "id", None),
            "guild_id": SETTINGS.guild_id,
            "narrator": NARRATOR.provider_label if NARRATOR.enabled else "disabled",
        }
        await self._mark_startup_phase("DISCORD_READY", detail)
        self.health_state.set_check("discord_gateway", True, guild_id=SETTINGS.guild_id)
        log.info(
            "Logged in as %s (%s). Narrator: %s",
            self.user,
            getattr(self.user, "id", "?"),
            NARRATOR.provider_label if NARRATOR.enabled else "disabled",
        )

    async def on_disconnect(self) -> None:
        self.health_state.clear_phase("DISCORD_READY", reason="gateway disconnected")
        self.health_state.set_check("discord_gateway", False, reason="gateway disconnected")
        log.warning("DISCORD_NOT_READY reason=gateway_disconnected boot_id=%s", self.health_state.boot_id)

    async def on_resumed(self) -> None:
        detail = {
            "user": str(self.user),
            "user_id": getattr(self.user, "id", None),
            "guild_id": SETTINGS.guild_id,
            "resumed": True,
        }
        await self._mark_startup_phase("DISCORD_READY", detail)
        self.health_state.set_check("discord_gateway", True, guild_id=SETTINGS.guild_id, resumed=True)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        if message.guild.id != SETTINGS.guild_id:
            return
        if not SETTINGS.message_content_intent:
            return

        mentioned = self.user is not None and self.user in message.mentions
        parent_id = message.channel.parent_id if isinstance(message.channel, discord.Thread) else None
        hub_record = await DB.get_realm_hub_by_channel(message.guild.id, int(parent_id or message.channel.id))
        character = await DB.get_character(message.author.id)
        private_scene = None
        if character and isinstance(message.channel, discord.Thread):
            private_scene = await _private_scene_for_thread(
                message.guild, message.channel.id, message.author.id, character
            )
        auto_channel = (
            SETTINGS.auto_narrate
            and (
                bool(hub_record)
                or bool(private_scene)
                or (bool(SETTINGS.rp_channel_ids) and (message.channel.id in SETTINGS.rp_channel_ids or parent_id in SETTINGS.rp_channel_ids))
            )
        )
        active_event_thread = (
            SETTINGS.auto_narrate_event_threads
            and isinstance(message.channel, discord.Thread)
            and await DB.is_active_event_thread(message.channel.id)
        )
        if not mentioned and not auto_channel and not active_event_thread:
            return

        if not character:
            await message.reply("Create your cultivator first with **/begin**.")
            return
        if isinstance(message.channel, discord.Thread) and parent_id:
            cfg = await DB.get_server_config(message.guild.id)
            if int(parent_id) == int(cfg.get("exploration_channel_id") or 0) and not private_scene:
                await message.reply("This is not your active private expedition thread.")
                return
        if hub_record and str(character.get("location", "")) != str(hub_record.get("location", "")):
            await message.reply(
                f"🏙️ This channel represents **{hub_record['location']}** in **{hub_record['world_name']}**. "
                f"Your cultivator is currently at **{character.get('location','Unknown')}**. "
                "Use **/travel → Realm Capitals → Go** before roleplaying here."
            )
            return

        content = message.content
        if self.user:
            content = content.replace(f"<@{self.user.id}>", "").replace(f"<@!{self.user.id}>", "").strip()
        if not content:
            return

        await DB.add_history(
            message.channel.id,
            user_id=message.author.id,
            speaker=character["name"],
            content=content,
        )
        if is_current_location_question(content):
            narration = canonical_location_reply(character)
            await DB.add_history(
                message.channel.id,
                user_id=None,
                speaker="World",
                content=narration,
            )
            await message.reply(narration, mention_author=False)
            return
        history = await DB.get_history(message.channel.id, 24)

        lineage_context = await DB.describe_lineage_context(message.author.id)
        scene_context = await NARRATOR_CONTEXT.build(
            character,
            scene_type=(f"private_{private_scene[0]}" if private_scene else "public_roleplay"),
            lineage_context=lineage_context,
            query_text=content,
        )
        async with message.channel.typing():
            try:
                narration = await NARRATOR.narrate_action(
                    character=character,
                    action=content,
                    history=history,
                    social_context=lineage_context,
                    scene_context=scene_context.text,
                    epic=active_event_thread,
                )
            except Exception:
                log.exception("Narration failed")
                await message.reply("The spiritual currents are unstable; narration failed. Try again shortly.")
                return

        await DB.add_history(
            message.channel.id,
            user_id=None,
            speaker="World",
            content=narration,
        )
        try:
            memory_kind, memory_salience = classify_memory(content, narration)
            await DB.add_rag_memory(
                message.author.id, memory_kind=memory_kind, salience=memory_salience,
                location=str(character.get("location") or ""), source="freeform",
                game_minute=scene_context.game_minute,
                summary=(
                    f"At {character.get('location','Unknown')}, {character.get('name','the player')} acted/said: "
                    f"{content[:320]} | Observed response: {narration[:560]}"
                ),
            )
            await _remember_freeform_npc_scene(
                user_id=message.author.id, character=character, player_text=content,
                narration=narration, game_minute=scene_context.game_minute,
            )
        except Exception:
            log.exception("Failed to persist freeform RAG/NPC episodic memory")
        for i, chunk in enumerate(chunk_text(narration)):
            if i == 0:
                await message.reply(chunk, mention_author=False)
            else:
                await message.channel.send(chunk)


bot = XianxiaBot()
# Hubs report failures through the bot instance without importing main.py back
# into the hub module, keeping package dependencies acyclic.
bot.hub_error_reporter = post_server_log


def _admin_command_option_summary(data: object) -> str:
    """Return a compact, non-secret summary of slash-command options for Discord audit logs."""
    if not isinstance(data, dict):
        return ""
    parts: list[str] = []

    def walk(options: object) -> None:
        if not isinstance(options, list):
            return
        for option in options:
            if not isinstance(option, dict):
                continue
            nested = option.get("options")
            if isinstance(nested, list):
                walk(nested)
                continue
            name = str(option.get("name") or "option")
            if "value" not in option:
                continue
            value = str(option.get("value"))
            if len(value) > 120:
                value = value[:117] + "..."
            parts.append(f"`{name}`=`{value}`")

    walk(data.get("options"))
    return ", ".join(parts[:12])


async def log_admin_command_invocation(interaction: discord.Interaction) -> None:
    """Mirror every accepted /admin invocation to the configured private log channel."""
    try:
        command_name = interaction.command.qualified_name if interaction.command else "admin"
        options = _admin_command_option_summary(interaction.data)
        supplied = getattr(interaction, "hub_supplied_options", None)
        if isinstance(supplied, dict) and supplied:
            rendered: list[str] = []
            for key, value in list(supplied.items())[:12]:
                if isinstance(value, discord.Member):
                    display = f"{value} ({value.id})"
                elif isinstance(value, discord.abc.GuildChannel):
                    display = f"#{value.name} ({value.id})"
                elif isinstance(value, app_commands.Choice):
                    display = str(value.value)
                else:
                    display = str(value)
                if len(display) > 120:
                    display = display[:117] + "..."
                rendered.append(f"`{key}`=`{display}`")
            options = ", ".join(rendered)
        detail = (
            f"**{interaction.user}** (`{interaction.user.id}`) ran `/{command_name}`\n"
            f"Channel: <#{interaction.channel_id}>"
        )
        if options:
            detail += f"\nOptions: {options}"
        await post_server_log(interaction.guild, "Admin command", detail)
    except Exception:
        # Logging must never block an otherwise valid administrator command.
        log.exception("Could not mirror /admin invocation to Discord")


async def require_admin(interaction: discord.Interaction) -> bool:
    member = interaction.user
    if not isinstance(member, discord.Member) or not member.guild_permissions.administrator:
        await interaction.response.send_message(
            "This command requires the **Administrator** permission.", ephemeral=False
        )
        return False
    await log_admin_command_invocation(interaction)
    return True


async def audit_admin(
    interaction: discord.Interaction,
    action: str,
    *,
    target: str = "",
    before: dict | None = None,
    after: dict | None = None,
    reason: str = "",
    database_log: bool = True,
) -> None:
    if database_log:
        try:
            await DB.log_admin_action(
                admin_user_id=interaction.user.id, action=action, target=target,
                before=before, after=after, reason=reason,
            )
        except Exception:
            log.exception("Could not write admin audit entry for %s", action)
    try:
        await post_server_log(
            interaction.guild,
            "Admin action",
            f"**{interaction.user}** (`{interaction.user.id}`) ran `{action}`"
            + (f" on `{target}`" if target else "")
            + (f" — {reason}" if reason else ""),
        )
    except Exception:
        log.exception("Could not mirror admin audit entry to Discord for %s", action)


admin_group = app_commands.Group(
    name="admin",
    description="Configure and control the cultivation world",
    guild_only=True,
    default_permissions=discord.Permissions(administrator=True),
)

admin_server_group = app_commands.Group(
    name="server",
    description="Server setup, health, maintenance, backups and audit",
    parent=admin_group,
)
admin_world_group = app_commands.Group(
    name="world",
    description="World events, time and GM event controls",
    parent=admin_group,
)
admin_player_group = app_commands.Group(
    name="player",
    description="Inspect, restore and modify player state",
    parent=admin_group,
)
admin_sect_group = app_commands.Group(
    name="sect",
    description="Sect membership, ranks and master relationships",
    parent=admin_group,
)
admin_family_group = app_commands.Group(
    name="family",
    description="Inspect hidden birth-family state",
    parent=admin_group,
)
admin_npc_group = app_commands.Group(
    name="npc",
    description="Inspect hidden NPC state",
    parent=admin_group,
)
admin_sim_group = app_commands.Group(
    name="simulation",
    description="World simulation, economy, sect politics and clan automation",
    parent=admin_group,
)


@registered_group_command(admin_server_group, name="bind_channels", description="Manually bind existing Xianxia event, home, log and new-player channels")
async def admin_setup(
    interaction: discord.Interaction,
    announcements: discord.TextChannel,
    scenes: discord.TextChannel,
    homes: discord.TextChannel | None = None,
    logs: discord.TextChannel | None = None,
    begin: discord.TextChannel | None = None,
) -> None:
    if not await require_admin(interaction):
        return
    if interaction.guild is None:
        return
    existing = await DB.get_server_config(interaction.guild.id)
    await DB.set_server_channels(
        interaction.guild.id,
        announcement_channel_id=announcements.id,
        event_scene_channel_id=scenes.id,
        home_scene_channel_id=(homes or scenes).id,
        log_channel_id=(logs.id if logs else existing.get("log_channel_id")),
        begin_channel_id=(begin.id if begin else existing.get("begin_channel_id")),
    )
    await audit_admin(
        interaction, "server.bind_channels", target=f"guild:{interaction.guild.id}",
        after={
            "announcements": announcements.id, "scenes": scenes.id, "homes": (homes or scenes).id,
            "logs": logs.id if logs else existing.get("log_channel_id"),
            "begin": begin.id if begin else existing.get("begin_channel_id"),
        },
    )
    cfg = await DB.get_server_config(interaction.guild.id)
    log_ch = await _resolve_text_channel(interaction.guild, cfg.get("log_channel_id"))
    begin_ch = await _resolve_text_channel(interaction.guild, cfg.get("begin_channel_id"))
    await interaction.response.send_message(
        "✅ **Xianxia server setup saved.**\n"
        f"Announcements: {announcements.mention}\n"
        f"Event scenes/threads: {scenes.mention}\n"
        f"Private player-owned location threads: {(homes or scenes).mention}\n"
        f"Operational logs: {log_ch.mention if log_ch else '**not configured**'}\n"
        f"New-player /begin channel: {begin_ch.mention if begin_ch else '**not configured**'}\n\n"
        "Settings persist in SQLite across restarts.",
        ephemeral=False,
    )


@registered_group_command(admin_server_group, name="status", description="Show the current event-channel setup and permissions")
async def admin_status(interaction: discord.Interaction) -> None:
    if not await require_admin(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return
    cfg = await DB.get_server_config(guild.id)
    ann_id = cfg.get("announcement_channel_id")
    scene_id = cfg.get("event_scene_channel_id")
    home_id = cfg.get("home_scene_channel_id") or scene_id
    log_id = cfg.get("log_channel_id")
    begin_id = cfg.get("begin_channel_id")
    ann = await _resolve_text_channel(guild, ann_id)
    scene = await _resolve_text_channel(guild, scene_id)
    home = await _resolve_text_channel(guild, home_id)
    log_ch = await _resolve_text_channel(guild, log_id)
    begin_ch = await _resolve_text_channel(guild, begin_id)

    lines = [
        "⚙️ **Xianxia Server Setup**",
        f"\nAnnouncements: {ann.mention if ann else '**not configured**'}",
        f"\nEvent scenes: {scene.mention if scene else '**not configured**'}",
        f"\nPrivate homes: {home.mention if home else '**not configured**'}",
        f"\nOperational logs: {log_ch.mention if log_ch else '**not configured**'}",
        f"\nNew-player /begin: {begin_ch.mention if begin_ch else '**not configured**'}",
    ]
    if scene and guild.me:
        perms = scene.permissions_for(guild.me)
        checks = {
            "View Channel": perms.view_channel,
            "Send Messages": perms.send_messages,
            "Create Public Threads": perms.create_public_threads,
            "Send in Threads": perms.send_messages_in_threads,
            "Manage Threads": perms.manage_threads,
        }
        lines.append("\n\n**Event-scenes permissions**")
        for label, ok in checks.items():
            lines.append(f"\n{'✅' if ok else '❌'} {label}")
        if not perms.manage_threads:
            lines.append("\n\n⚠️ **Manage Threads** is needed to reliably lock/archive expired secret realms.")
    if home and guild.me:
        perms = home.permissions_for(guild.me)
        lines.append("\n\n**Private-home permissions**")
        for label, ok in {
            "View Channel": perms.view_channel,
            "Send Messages": perms.send_messages,
            "Create Private Threads": perms.create_private_threads,
            "Send in Threads": perms.send_messages_in_threads,
            "Manage Threads": perms.manage_threads,
        }.items():
            lines.append(f"\n{'✅' if ok else '❌'} {label}")
    hub_rows = await DB.get_realm_hub_channels(guild.id)
    lines.append("\n\n**Realm-capital meeting channels**")
    by_world={str(r['world_name']):r for r in hub_rows}
    for world,hub in REALM_HUBS.items():
        row=by_world.get(world); channel=guild.get_channel(int(row['channel_id'])) if row else None
        lines.append(f"\n{'✅' if isinstance(channel,discord.TextChannel) else '❌'} {world}: {channel.mention if isinstance(channel,discord.TextChannel) else 'not provisioned'}")
    await interaction.response.send_message("".join(lines), ephemeral=False)



BASE_CHANNEL_SETUP_CHOICES = [
    app_commands.Choice(name="Validate / bind existing base Xianxia channels", value="bind"),
    app_commands.Choice(name="Show base-channel status", value="status"),
]

BASE_CHANNEL_SPECS = {
    "world-events": "Global cultivation-world announcements, disasters, invasions and major events.",
    "event-scenes": "Event scene anchors and public roleplay threads created by the Xianxia bot.",
    "player-homes": "Read-only anchor for persistent private player-owned location and sect-residence threads.",
    "bot-logs": "Private operational and administrator-action logs for the Xianxia bot.",
    "begin-here": "New cultivators begin here with /begin before entering the wider cultivation world.",
    "xianxia-info": "Read-only game guide, onboarding and system information maintained by the Xianxia bot.",
    "expeditions": "Read-only anchor for private player expedition threads; normal roleplay happens inside the private threads, not this channel.",
}
READ_ONLY_BASE_CHANNELS = {"xianxia-info", "expeditions", "player-homes"}


def _base_channel_bindings(cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "world-events": cfg.get("announcement_channel_id"),
        "event-scenes": cfg.get("event_scene_channel_id"),
        "player-homes": cfg.get("home_scene_channel_id"),
        "bot-logs": cfg.get("log_channel_id"),
        "begin-here": cfg.get("begin_channel_id"),
        "xianxia-info": cfg.get("info_channel_id"),
        "expeditions": cfg.get("exploration_channel_id"),
    }


def _xianxia_info_guide_text() -> str:
    return (
        f"📖 **Xianxia Realm Guide — v{RELEASE_VERSION}**\n"
        "This channel is **read-only** and is **not** a game location. Use the guide menu below for detailed help.\n\n"
        "🌱 **Start** — `/begin` in `#begin-here`\n"
        "🧭 **Explore** — private expedition journal under `#expeditions`\n"
        "🏯 **Sects** — discovery → recommendation → entrance trial → sect life\n"
        "🏡 **Properties** — persistent private player/sect locations under `#player-homes`\n"
        "🧑 **Dashboard** — `/me` for scene, cultivation, relationships and quests\n"
        "📜 **Quests** — `/quests` for generic objective-driven story progress\n"
        "🔒 Unknown locations, future worlds and higher-world NPCs remain hidden until legitimately unlocked."
    )


XIANXIA_INFO_PAGES: dict[str, tuple[str, str]] = {
    "getting_started": ("🌱 Getting Started", "Use `/begin` in `#begin-here`, then `/me` to see your current state. Exploration happens in your private expedition thread; main realm-capital channels remain shared social spaces."),
    "character": ("🧬 Character & Cultivation", "Your family, spiritual root, cultivation path, realms, resources, Karma, Fate, Dao Heart and effects remain canonical game state. The AI router narrates results but cannot change mechanics."),
    "exploration": ("🧭 Exploration & Scenes", "The v0.18 scene engine separates **physical location** from **active scene**. Wilderness uses your expedition journal. Player properties and sect abodes use persistent private threads. Main cities remain shared channels."),
    "sects": ("🏯 Sects", "Discover a sect route, speak with affiliated NPCs, earn recommendations, take a sect-specific trial, and join only after a canonical success. Membership can grant a private sect residence."),
    "properties": ("🏡 Player-Owned Locations", "Properties are real database-backed locations with private threads, facilities and guest permissions. Guests must be invited and physically reach the entrance before gaining access."),
    "relationships": ("🤝 NPC Relationships", "Persistent NPC state tracks trust, respect, fear, affection, debt, grudge and encounter history. Narration may describe those relationships but never owns the underlying numbers."),
    "quests": ("📜 Quests", "The generic quest engine supports reusable objectives such as explore, talk, investigate, collect, craft, travel and sect milestones. `/quests` shows available and active quests."),
    "admin": ("🧰 Server Administration", "Use `/admin → Server → Setup` for Setup Server, Repair Server, Check Permissions, Show Configuration, Sync Realm Roles and Rebuild Info Guide."),
}


class XianxiaInfoSelect(discord.ui.Select):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Choose a Xianxia guide topic…",
            min_values=1, max_values=1, custom_id="xianxia:v07:info:topic",
            options=[discord.SelectOption(label=title.split(" ",1)[1] if " " in title else title, value=key, emoji=title.split(" ",1)[0]) for key,(title,_) in XIANXIA_INFO_PAGES.items()],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        title, body = XIANXIA_INFO_PAGES.get(self.values[0], ("📖 Xianxia Guide", "No guide page is available."))
        await interaction.response.send_message(f"**{title}**\n{body}", ephemeral=False)


class XianxiaInfoView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(XianxiaInfoSelect())


async def ensure_xianxia_info_guide(guild: discord.Guild, channel: discord.TextChannel) -> discord.Message | None:
    """Keep one bot-managed read-only guide message instead of duplicating it on every repair."""
    cfg = await DB.get_server_config(guild.id)
    message_id = cfg.get("info_message_id")
    message: discord.Message | None = None
    if message_id:
        try:
            message = await channel.fetch_message(int(message_id))
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            message = None
    try:
        if message is None:
            message = await channel.send(_xianxia_info_guide_text(), view=XianxiaInfoView())
            await DB.set_info_message_id(guild.id, message.id)
        elif message.content != _xianxia_info_guide_text():
            await message.edit(content=_xianxia_info_guide_text(), view=XianxiaInfoView())
        return message
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Could not create/update Xianxia info guide")
        return None


async def ensure_base_xianxia_channels(
    guild: discord.Guild, *, category_name: str = "📜 Xianxia RP"
) -> dict[str, Any]:
    """Validate and bind existing base channels without creating Discord channels."""
    cfg = await DB.get_server_config(guild.id)
    bindings = _base_channel_bindings(cfg)
    category = next((item for item in guild.categories if item.name == category_name), None)
    channels: dict[str, discord.TextChannel] = {}
    warnings: list[str] = []

    for name in BASE_CHANNEL_SPECS:
        configured = await _resolve_text_channel(guild, bindings.get(name))
        channel = configured or next((item for item in guild.text_channels if item.name == name), None)
        if channel is None:
            warnings.append(
                f"Missing **#{name}**. Create/bind it from the admin dashboard; the bot will not provision channels."
            )
            continue
        channels[name] = channel

    info_channel = channels.get("xianxia-info")
    if info_channel is not None:
        await ensure_xianxia_info_guide(guild, info_channel)

    return {
        "category": category,
        "channels": channels,
        "created": [],
        "repaired": [],
        "warnings": warnings,
        "dashboard_owned": True,
    }


@registered_group_command(admin_server_group, name="basechannels", description="Inspect dashboard-managed base Xianxia Discord channels")
@app_commands.choices(action=BASE_CHANNEL_SETUP_CHOICES)
async def admin_base_channels(
    interaction: discord.Interaction,
    action: app_commands.Choice[str],
    category_name: str = "📜 Xianxia RP",
) -> None:
    if not await require_admin(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return

    cfg = await DB.get_server_config(guild.id)
    bindings = _base_channel_bindings(cfg)
    if action.value == "status":
        lines = ["📜 **Base Xianxia Channels**"]
        for name, channel_id in bindings.items():
            channel = await _resolve_text_channel(guild, channel_id)
            lines.append(f"• `#{name}`: {channel.mention if channel else '*not configured*'}")
        if guild.me:
            lines.append(f"\nManage Channels permission: **{'yes' if guild.me.guild_permissions.manage_channels else 'NO'}**")
            lines.append(f"Manage Roles permission: **{'yes' if guild.me.guild_permissions.manage_roles else 'NO'}**")
        await interaction.response.send_message("\n".join(lines), ephemeral=False)
        return

    await interaction.response.defer(ephemeral=False)
    try:
        result = await ensure_base_xianxia_channels(guild, category_name=category_name)
    except (discord.Forbidden, discord.HTTPException) as exc:
        await interaction.followup.send(f"❌ Could not validate the configured base Discord layout: {exc}", ephemeral=False)
        return
    await audit_admin(
        interaction, "server.basechannels", target=f"guild:{guild.id}",
        after={
            "category_id": result["category"].id if result["category"] else None,
            "created": result["created"],
            "repaired": result["repaired"],
        },
    )
    warning_text = "".join(f"\n⚠️ {warning}" for warning in result["warnings"])
    await interaction.followup.send(
        "✅ **Base Xianxia channels are connected.**\n"
        + "\n".join(f"• `#{name}` → {channel.mention}" for name, channel in result["channels"].items())
        + warning_text,
        ephemeral=False,
    )


SERVER_SETUP_CHOICES = [
    app_commands.Choice(name="Setup Server", value="setup"),
    app_commands.Choice(name="Repair Server", value="repair"),
    app_commands.Choice(name="Check Permissions", value="permissions"),
    app_commands.Choice(name="Show Configuration", value="configuration"),
    app_commands.Choice(name="Sync Realm Roles", value="sync_roles"),
    app_commands.Choice(name="Rebuild Info Guide", value="rebuild_info"),
]

SERVER_BASE_CATEGORY = "📜 Xianxia RP"
SERVER_REALM_CATEGORY = "🌌 Realm Capitals"


def _server_permission_report(guild: discord.Guild) -> tuple[list[str], list[str]]:
    """Return human-readable permission diagnostics and actionable warnings."""
    me = guild.me
    if me is None:
        return ["❌ Bot member state is unavailable."], ["Discord did not expose the bot member for this guild."]

    perms = me.guild_permissions
    lines: list[str] = [
        f"{'✅' if perms.manage_channels else 'ℹ️'} **Manage Channels** — optional; channel creation and layout are owned by the admin dashboard."
    ]
    checks = (
        ("Manage Threads", perms.manage_threads, "Private expedition/property threads cannot be reliably recovered or archived."),
        ("Create Private Threads", perms.create_private_threads, "Private expedition and player-location threads cannot be created."),
        ("Send Messages in Threads", perms.send_messages_in_threads, "The bot cannot narrate or update private scene threads."),
        ("Manage Roles", perms.manage_roles, "Realm-role creation and synchronization will not work."),
        ("View Channels", perms.view_channel, "The bot cannot resolve configured game channels."),
        ("Send Messages", perms.send_messages, "The bot cannot post setup guides, events or normal game responses."),
        ("Read Message History", perms.read_message_history, "Persistent scene and guide recovery may be incomplete."),
        ("Embed Links", perms.embed_links, "Some game panels and rich status responses will be degraded."),
    )
    warnings: list[str] = []
    for label, ok, consequence in checks:
        lines.append(f"{'✅' if ok else '❌'} **{label}**" + ("" if ok else f"\n   {consequence}"))
        if not ok:
            warnings.append(f"{label}: {consequence}")

    realm_roles = [
        role for world in REALM_HUBS
        if (role := discord.utils.get(guild.roles, name=_realm_access_role_name(world))) is not None
    ]
    if not perms.manage_roles:
        lines.append("❌ **Realm Role Hierarchy**\n   Cannot validate/manage realm roles until **Manage Roles** is granted.")
    elif not realm_roles:
        lines.append("⚠️ **Realm Role Hierarchy**\n   Realm roles are not provisioned yet; **Setup Server** can create them.")
    else:
        blocked = [role for role in realm_roles if me.top_role.position <= role.position]
        if blocked:
            names = ", ".join(role.name for role in blocked[:4])
            lines.append(
                "❌ **Realm Role Hierarchy**\n"
                f"   Move the bot's highest role above: {names}. Realm-role syncing will fail while those roles are above/equal to the bot."
            )
            warnings.append("Bot role hierarchy is below one or more generated realm-access roles.")
        else:
            lines.append("✅ **Realm Role Hierarchy** — bot role is above all generated realm-access roles.")
    return lines, warnings


async def _server_configuration_report(guild: discord.Guild) -> str:
    cfg = await DB.get_server_config(guild.id)
    bindings = _base_channel_bindings(cfg)
    lines = [
        "⚙️ **Xianxia Server Configuration**",
        f"Guild: **{guild.name}** (`{guild.id}`)",
        f"Release: **{RELEASE_VERSION}** • Database schema: **v{SCHEMA_VERSION}**",
        "\n**Base channel bindings**",
    ]
    for name, channel_id in bindings.items():
        channel = await _resolve_text_channel(guild, channel_id)
        if channel is not None:
            lines.append(f"✅ `#{name}` → {channel.mention} (`{channel.id}`)")
        elif channel_id:
            lines.append(f"❌ `#{name}` → stale/missing (`{channel_id}`)")
        else:
            lines.append(f"⚪ `#{name}` → not configured")

    lines.append("\n**Realm-capital bindings**")
    rows = {str(row["world_name"]): row for row in await DB.get_realm_hub_channels(guild.id)}
    for world, hub in REALM_HUBS.items():
        row = rows.get(world)
        channel = guild.get_channel(int(row["channel_id"])) if row else None
        role = discord.utils.get(guild.roles, name=_realm_access_role_name(world))
        channel_text = channel.mention if isinstance(channel, discord.TextChannel) else "not configured"
        role_text = role.mention if role is not None else "not configured"
        lines.append(f"• **{world}** — channel: {channel_text} • role: {role_text}")

    info_id = cfg.get("info_message_id")
    lines.append(f"\nInfo guide message ID: `{info_id}`" if info_id else "\nInfo guide message ID: *not recorded*")
    updated_at = cfg.get("updated_at")
    if updated_at:
        lines.append(f"Configuration updated: <t:{int(float(updated_at))}:R>")
    return "\n".join(lines)


async def _sync_all_realm_access_roles(guild: discord.Guild) -> dict[str, int]:
    """Reconcile generated realm visibility roles for every character still in the guild."""
    me = guild.me
    if me is None or not me.guild_permissions.manage_roles:
        raise PermissionError("Manage Roles is required")

    role_map = await _ensure_realm_access_roles(guild)
    blocked = [role for role in role_map.values() if me.top_role.position <= role.position]
    if blocked:
        raise PermissionError(
            "Move the bot role above the generated realm roles before syncing: "
            + ", ".join(role.name for role in blocked[:4])
        )

    counts = {"characters": 0, "members": 0, "synced": 0, "not_in_guild": 0, "failed": 0}
    for user_id in await DB.list_character_user_ids():
        counts["characters"] += 1
        member = guild.get_member(int(user_id))
        if member is None:
            try:
                member = await guild.fetch_member(int(user_id))
            except discord.NotFound:
                counts["not_in_guild"] += 1
                continue
            except (discord.Forbidden, discord.HTTPException):
                counts["failed"] += 1
                continue
        counts["members"] += 1
        character = await DB.get_character(int(user_id))
        if character is None:
            continue
        try:
            await _sync_realm_access_roles(guild, member, character)
            counts["synced"] += 1
        except Exception:
            counts["failed"] += 1
            log.exception("Bulk realm-role sync failed for user %s", user_id)
    return counts


async def _run_complete_server_setup(guild: discord.Guild) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    base_result = await ensure_base_xianxia_channels(guild, category_name=SERVER_BASE_CATEGORY)
    realm_rows = await ensure_realm_hub_channels(guild, category_name=SERVER_REALM_CATEGORY)
    return base_result, realm_rows


async def _dashboard_discord_snapshot(client: XianxiaBot, guild: discord.Guild) -> dict[str, Any]:
    cfg = await DB.get_server_config(guild.id)
    bindings = _base_channel_bindings(cfg)
    base_channels: list[dict[str, Any]] = []
    for name in BASE_CHANNEL_SPECS:
        channel_id = bindings.get(name)
        channel = await _resolve_text_channel(guild, channel_id)
        base_channels.append({
            "key": name,
            "configured_id": int(channel_id) if channel_id else None,
            "channel_id": channel.id if channel else None,
            "name": channel.name if channel else None,
            "status": "ready" if channel else ("stale" if channel_id else "missing"),
        })

    realm_rows = {str(row["world_name"]): row for row in await DB.get_realm_hub_channels(guild.id)}
    realm_hubs: list[dict[str, Any]] = []
    for world, hub in REALM_HUBS.items():
        row = realm_rows.get(world)
        channel = guild.get_channel(int(row["channel_id"])) if row else None
        role = discord.utils.get(guild.roles, name=_realm_access_role_name(world))
        realm_hubs.append({
            "world": world,
            "display_name": str(hub.get("display_name") or world),
            "channel_id": channel.id if isinstance(channel, discord.TextChannel) else None,
            "channel_name": channel.name if isinstance(channel, discord.TextChannel) else None,
            "role_id": role.id if role else None,
            "role_name": role.name if role else None,
            "ready": isinstance(channel, discord.TextChannel) and role is not None,
        })

    me = guild.me
    permission_specs = (
        ("manage_channels", "Manage Channels", "Optional; channel creation and layout are admin-dashboard owned.", False),
        ("manage_threads", "Manage Threads", "Recover/archive private expedition and property threads.", True),
        ("create_private_threads", "Create Private Threads", "Create private expedition and player-location threads.", True),
        ("send_messages_in_threads", "Send Messages in Threads", "Narrate and update private scenes.", True),
        ("manage_roles", "Manage Roles", "Create/sync realm visibility roles.", True),
        ("view_channel", "View Channels", "Resolve configured game channels.", True),
        ("send_messages", "Send Messages", "Post guides, events and game responses.", True),
        ("read_message_history", "Read Message History", "Recover persistent scene and guide messages.", True),
        ("embed_links", "Embed Links", "Render rich game/status panels.", True),
    )
    permissions: list[dict[str, Any]] = []
    for attr, label, purpose, required in permission_specs:
        ok = bool(me and getattr(me.guild_permissions, attr, False))
        permissions.append({"key": attr, "label": label, "ok": ok, "purpose": purpose, "required": required})

    blocked_roles: list[str] = []
    if me and me.guild_permissions.manage_roles:
        for row in realm_hubs:
            role = guild.get_role(int(row["role_id"])) if row.get("role_id") else None
            if role is not None and me.top_role.position <= role.position:
                blocked_roles.append(role.name)

    all_channels = [
        {
            "id": channel.id,
            "name": channel.name,
            "category": channel.category.name if channel.category else "Uncategorized",
        }
        for channel in sorted(guild.text_channels, key=lambda c: ((c.category.position if c.category else -1), c.position, c.name))
    ]
    ready_base = sum(1 for row in base_channels if row["status"] == "ready")
    ready_realms = sum(1 for row in realm_hubs if row["ready"])
    warnings = [row["label"] for row in permissions if row["required"] and not row["ok"]]
    if blocked_roles:
        warnings.append("Bot role must be moved above: " + ", ".join(blocked_roles))

    return {
        "connected": True,
        "guild": {"id": guild.id, "name": guild.name, "member_count": guild.member_count},
        "bot": {
            "id": client.user.id if client.user else None,
            "name": str(client.user) if client.user else "Xianxia RP",
            "administrator": bool(me and me.guild_permissions.administrator),
            "top_role": me.top_role.name if me else None,
        },
        "permissions": permissions,
        "permission_warnings": warnings,
        "base_channels": base_channels,
        "base_ready": ready_base,
        "base_total": len(base_channels),
        "realm_hubs": realm_hubs,
        "realm_ready": ready_realms,
        "realm_total": len(realm_hubs),
        "text_channels": all_channels,
        "info_message_id": cfg.get("info_message_id"),
        "registered_commands": len(client.tree.get_commands(guild=GUILD)),
        "setup_ready": ready_base == len(base_channels) and ready_realms == len(realm_hubs) and not warnings,
    }


async def _audit_dashboard_discord(action: str, guild: discord.Guild, *, after: dict[str, Any] | None = None, reason: str = "GM dashboard") -> None:
    await DB.log_admin_action(
        admin_user_id=0,
        action=f"dashboard.discord.{action}",
        target=f"guild:{guild.id}",
        after=after or {},
        reason=reason[:500],
    )


async def dashboard_discord_control(client: XianxiaBot, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Private dashboard -> discord.py control surface.

    This endpoint never performs game mechanics. It validates dashboard-owned
    Discord layout, binds existing channels, and stores Discord IDs through the
    normal Go-owned database boundary.
    """
    action = str(action or "status").strip().lower()
    guild = client.get_guild(SETTINGS.guild_id)
    if guild is None:
        raise RuntimeError(f"Configured Discord guild {SETTINGS.guild_id} is not connected")
    reason = str(payload.get("reason") or "GM dashboard")[:500]

    if action in {"status", "permissions", "configuration"}:
        return {"ok": True, "action": action, "result": await _dashboard_discord_snapshot(client, guild)}

    if action in {"setup", "repair"}:
        base_result, realm_rows = await _run_complete_server_setup(guild)
        info_channel = base_result["channels"].get("xianxia-info")
        info_message = await ensure_xianxia_info_guide(guild, info_channel) if info_channel else None
        role_sync: dict[str, int] | None = None
        if guild.me.guild_permissions.manage_roles:
            role_sync = await _sync_all_realm_access_roles(guild)
        synced = await client.tree.sync(guild=GUILD)
        result = {
            "mode": action,
            "created": list(base_result["created"]),
            "repaired": list(base_result["repaired"]),
            "warnings": list(base_result["warnings"]),
            "realm_hubs": len(realm_rows),
            "info_message_id": info_message.id if info_message else None,
            "realm_role_sync": role_sync,
            "synced_commands": len(synced),
        }
        await _audit_dashboard_discord(action, guild, after=result, reason=reason)
        return {"ok": True, "action": action, "result": result, "status": await _dashboard_discord_snapshot(client, guild)}

    if action == "sync_roles":
        counts = await _sync_all_realm_access_roles(guild)
        await _audit_dashboard_discord(action, guild, after=counts, reason=reason)
        return {"ok": True, "action": action, "result": counts, "status": await _dashboard_discord_snapshot(client, guild)}

    if action == "rebuild_info":
        channel = await configured_info_channel(guild)
        if channel is None:
            raise ValueError("#xianxia-info is not configured. Run Full Setup or Repair first.")
        message = await ensure_xianxia_info_guide(guild, channel)
        if message is None:
            raise RuntimeError("Discord rejected the info-guide update")
        result = {"channel_id": channel.id, "message_id": message.id}
        await _audit_dashboard_discord(action, guild, after=result, reason=reason)
        return {"ok": True, "action": action, "result": result}

    if action == "sync_commands":
        synced = await client.tree.sync(guild=GUILD)
        result = {"synced_commands": len(synced), "guild_id": guild.id}
        await _audit_dashboard_discord(action, guild, after=result, reason=reason)
        return {"ok": True, "action": action, "result": result}

    if action == "bind_channels":
        cfg = await DB.get_server_config(guild.id)
        mapping = {
            "announcement_channel_id": "announcements",
            "event_scene_channel_id": "scenes",
            "home_scene_channel_id": "homes",
            "log_channel_id": "logs",
            "begin_channel_id": "begin",
            "info_channel_id": "info",
            "exploration_channel_id": "exploration",
        }
        values: dict[str, int | None] = {}
        for db_key, payload_key in mapping.items():
            raw = payload.get(payload_key)
            if raw in {None, ""}:
                values[db_key] = cfg.get(db_key)
                continue
            try:
                channel_id = int(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid channel id for {payload_key}") from exc
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                raise ValueError(f"Selected {payload_key} channel is not a text channel in {guild.name}")
            values[db_key] = channel_id
        if not values.get("announcement_channel_id") or not values.get("event_scene_channel_id"):
            raise ValueError("World-events and event-scenes channels must be selected before saving bindings")
        await DB.set_server_channels(guild.id, **values)
        await _audit_dashboard_discord(action, guild, after=values, reason=reason)
        return {"ok": True, "action": action, "result": values, "status": await _dashboard_discord_snapshot(client, guild)}

    if action == "test_announcement":
        cfg = await DB.get_server_config(guild.id)
        channel = await _resolve_text_channel(guild, cfg.get("announcement_channel_id"))
        if channel is None:
            raise ValueError("No announcement channel is configured")
        message = await channel.send("🧪 **Xianxia RP GM Dashboard test** — Discord server integration is working.")
        result = {"channel_id": channel.id, "message_id": message.id}
        await _audit_dashboard_discord(action, guild, after=result, reason=reason)
        return {"ok": True, "action": action, "result": result}

    raise ValueError(f"Unsupported Discord dashboard action: {action}")


@registered_group_command(admin_server_group, name="setup", description="Install, repair and diagnose the complete Xianxia Discord server")
@app_commands.choices(action=SERVER_SETUP_CHOICES)
async def admin_setup_server(
    interaction: discord.Interaction,
    action: app_commands.Choice[str],
) -> None:
    if not await require_admin(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return

    if action.value == "permissions":
        lines, warnings = _server_permission_report(guild)
        summary = "✅ **Ready for automatic setup.**" if not warnings else f"⚠️ **{len(warnings)} setup issue(s) found.**"
        await interaction.response.send_message(
            "🧰 **Xianxia Server Permission Check**\n" + summary + "\n\n" + "\n".join(lines),
            ephemeral=False,
        )
        await audit_admin(interaction, "server.setup.permissions", target=f"guild:{guild.id}", after={"issues": len(warnings)})
        return

    if action.value == "configuration":
        await interaction.response.send_message(await _server_configuration_report(guild), ephemeral=False)
        await audit_admin(interaction, "server.setup.configuration", target=f"guild:{guild.id}")
        return

    if action.value == "sync_roles":
        if not guild.me or not guild.me.guild_permissions.manage_roles:
            lines, _ = _server_permission_report(guild)
            await interaction.response.send_message(
                "❌ **Manage Roles** is required to synchronize cultivation-world visibility roles.\n\n" + "\n".join(lines),
                ephemeral=False,
            )
            return
        await interaction.response.defer(ephemeral=False)
        try:
            counts = await _sync_all_realm_access_roles(guild)
        except PermissionError as exc:
            await interaction.followup.send(f"❌ Realm-role sync could not start: {exc}", ephemeral=False)
            return
        await audit_admin(interaction, "server.setup.sync_roles", target=f"guild:{guild.id}", after=counts)
        await interaction.followup.send(
            "✅ **Realm access roles synchronized.**\n"
            f"Characters checked: **{counts['characters']}**\n"
            f"Guild members found: **{counts['members']}**\n"
            f"Members reconciled: **{counts['synced']}**\n"
            f"Characters no longer in this Discord: **{counts['not_in_guild']}**\n"
            f"Fetch/sync failures: **{counts['failed']}**",
            ephemeral=False,
        )
        return

    if action.value == "rebuild_info":
        info_channel = await configured_info_channel(guild)
        if info_channel is None:
            await interaction.response.send_message(
                "❌ `#xianxia-info` is not configured or was deleted. Run **Server → Setup → Repair Server** first.",
                ephemeral=False,
            )
            return
        await interaction.response.defer(ephemeral=False)
        message = await ensure_xianxia_info_guide(guild, info_channel)
        if message is None:
            await interaction.followup.send(
                "❌ Could not rebuild the information guide. Run **Check Permissions** and verify the bot can send messages in `#xianxia-info`.",
                ephemeral=False,
            )
            return
        await audit_admin(
            interaction, "server.setup.rebuild_info", target=f"guild:{guild.id}",
            after={"channel_id": info_channel.id, "message_id": message.id},
        )
        await interaction.followup.send(
            f"✅ **Xianxia information guide rebuilt.**\nChannel: {info_channel.mention}\nGuide message: `{message.id}`",
            ephemeral=False,
        )
        return

    # Discord channel creation is dashboard-owned. Bot setup only validates bindings
    # and refreshes game-side metadata for channels that already exist.
    await interaction.response.defer(ephemeral=False)
    try:
        base_result, realm_rows = await _run_complete_server_setup(guild)
    except (discord.Forbidden, discord.HTTPException, PermissionError) as exc:
        await interaction.followup.send(
            f"❌ **Server {'setup' if action.value == 'setup' else 'repair'} stopped.** Discord rejected a required operation: {exc}\n"
            "Run **Server → Setup → Check Permissions** for the exact missing capability.",
            ephemeral=False,
        )
        return

    # Setup/Repair refreshes the persistent guide and, when possible, reconciles
    # existing players so a fresh Discord install does not wait for each player
    # to trigger an interaction before receiving the correct realm visibility roles.
    info_channel = base_result["channels"].get("xianxia-info")
    info_message = await ensure_xianxia_info_guide(guild, info_channel) if info_channel else None
    _, permission_warnings = _server_permission_report(guild)
    warnings = list(base_result["warnings"]) + permission_warnings
    role_sync_counts: dict[str, int] | None = None
    if guild.me and guild.me.guild_permissions.manage_roles:
        try:
            role_sync_counts = await _sync_all_realm_access_roles(guild)
        except PermissionError as exc:
            warnings.append(str(exc))
    mode = "Setup" if action.value == "setup" else "Repair"
    audit_action = "server.setup.install" if action.value == "setup" else "server.setup.repair"
    await audit_admin(
        interaction, audit_action, target=f"guild:{guild.id}",
        after={
            "base_category_id": base_result["category"].id if base_result["category"] else None,
            "base_created": base_result["created"],
            "base_repaired": base_result["repaired"],
            "realm_hubs": {str(row["world_name"]): int(row["channel_id"]) for row in realm_rows},
            "info_message_id": info_message.id if info_message else None,
            "permission_issues": len(permission_warnings),
            "realm_role_sync": role_sync_counts,
        },
    )
    warning_text = "" if not warnings else "\n\n⚠️ **Follow-up diagnostics**\n" + "\n".join(f"• {warning}" for warning in dict.fromkeys(warnings))
    await interaction.followup.send(
        f"✅ **Xianxia Server {mode} complete.**\n"
        f"Base channels connected: **{len(base_result['channels'])}**\n"
        f"Bot-created base channels: **0 (disabled)**\n"
        f"Dashboard-owned bindings validated: **{len(base_result['channels'])}**\n"
        f"Realm-capital channels connected: **{len(realm_rows)}**\n"
        f"Info guide: **{'ready' if info_message else 'needs attention'}**\n"
        + (f"Realm-role reconciliation: **{role_sync_counts['synced']} member(s)**\n" if role_sync_counts is not None else "Realm-role reconciliation: **skipped**\n")
        + "No database/world reset was performed and unrelated Discord channels were not deleted."
        + warning_text,
        ephemeral=False,
    )


REALM_HUB_SETUP_CHOICES = [
    app_commands.Choice(name="Refresh dashboard bindings", value="refresh"),
    app_commands.Choice(name="Show realm-capital channel status", value="status"),
]


@registered_group_command(admin_server_group, name="realmhubs", description="Inspect dashboard-managed realm-capital channel bindings")
@app_commands.choices(action=REALM_HUB_SETUP_CHOICES)
async def admin_realm_hubs(interaction: discord.Interaction, action: app_commands.Choice[str], category_name: str = "🌌 Realm Capitals") -> None:
    if not await require_admin(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return
    if action.value == "refresh":
        await ensure_realm_hub_channels(guild, category_name=category_name)
    existing = {str(row["world_name"]): row for row in await DB.get_realm_hub_channels(guild.id)}
    lines = [
        "🏙️ **Realm-Capital Meeting Channels**",
        "Discord channel creation is disabled in the bot; configure channel IDs in the admin dashboard.",
    ]
    for world, hub in REALM_HUBS.items():
        row = existing.get(world)
        channel = guild.get_channel(int(row["channel_id"])) if row else None
        lines.append(
            f"• **{world} — {hub['display_name']}**: "
            f"{channel.mention if isinstance(channel, discord.TextChannel) else '*not bound*'}"
        )
    await interaction.response.send_message("\n".join(lines), ephemeral=False)


@registered_group_command(admin_server_group, name="observability", description="Inspect slow-query telemetry, runtime metrics and external-alert history")
async def admin_observability(interaction: discord.Interaction) -> None:
    if not await require_admin(interaction): return
    flushed=await DB.flush_slow_query_log(); obs=await DB.observability_snapshot(); alerts=await DB.get_operational_alerts(limit=8)
    try:
        engine_status = await ENGINE.database_status()
        engine_line = f"Go engine **online** • requests **{int(engine_status.get('requests', 0))}** • SQLite journal **{engine_status.get('pragmas', {}).get('journal_mode', 'unknown')}**"
    except Exception as exc:
        engine_line = f"Go engine **offline** — {type(exc).__name__}: {exc}"
    lines=["📈 **Operational Observability**",f"Schema **v{SCHEMA_VERSION}** • query count **{obs['query_count']}** • slow queries **{obs['slow_query_count']}** • recent 1h **{obs['recent_slow_queries_1h']}**",f"Max observed latency **{obs['max_query_latency_ms']:.3f} ms** • slow threshold **{obs['slow_query_threshold_ms']:.1f} ms** • flushed now **{flushed}**",f"Connections opened **{obs['connections_opened']}** • reused **{obs['connections_reused']}** • writer waits **{obs['writer_wait_count']}** ({obs['writer_wait_ms']:.3f} ms)",engine_line,f"External webhook alerts: **{'enabled' if ALERTS.enabled else 'disabled'}**"]
    if alerts:
        lines.append("\n**Recent Alerts**")
        for a in alerts: lines.append(f"• `{a['alert_key']}` • {a['severity']} • delivered={bool(a['delivered'])} — {a['message']}")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(admin_world_group, name="events", description="List categorized active world events with keys and locations")
async def admin_events(interaction: discord.Interaction) -> None:
    if not await require_admin(interaction):
        return
    events = await DB.get_active_world_events()
    if not events:
        await interaction.response.send_message("No server-wide events are active.", ephemeral=False)
        return
    now = time.time()
    lines = ["🌌 **Active Admin Event List**"]
    for event in events:
        thread_text = f"<#{event['thread_id']}>" if event.get("thread_id") else "none"
        category=str(event.get("payload",{}).get("category") or event["event_type"])
        lines.append(
            f"\n\n**{event['title']}**\n"
            f"Key: `{event['event_key']}`\n"
            f"Type: `{event['event_type']}` • Category: **{category}** • Location: **{event['location']}**\n"
            f"Closes in {human_duration(int(event['ends_at'] - now))}\n"
            f"Thread: {thread_text}"
        )
    await reply_long(interaction, "".join(lines), ephemeral=False)


@registered_group_command(admin_world_group, name="spawnrealm", description="Open a secret realm immediately for testing or GM events")
async def admin_spawnrealm(interaction: discord.Interaction, realm: str) -> None:
    if not await require_admin(interaction):
        return
    realm_id = None
    wanted = realm.strip().casefold()
    for rid, info in WORLD.secret_realms.items():
        if wanted in {rid.casefold(), str(info.get("name", "")).casefold()}:
            realm_id = rid
            break
    if realm_id is None:
        await interaction.response.send_message(
            "Unknown secret realm. Try one of: " + ", ".join(r["name"] for r in WORLD.secret_realms.values()),
            ephemeral=False,
        )
        return

    info = WORLD.secret_realms[realm_id]
    event_key = f"secret:{realm_id}:admin:{time.time_ns()}"
    ends_at = time.time() + int(info.get("open_hours", 8)) * 3600
    await DB.activate_world_event(
        event_key=event_key,
        event_type="secret_realm",
        title=info["name"],
        location=info["location"],
        payload={"definition_id": "admin_spawn", "realm_id": realm_id},
        ends_at=ends_at,
    )
    await audit_admin(interaction, "event.spawnrealm", target=event_key, after={"realm_id": realm_id, "ends_at": ends_at})
    await interaction.response.defer(ephemeral=False)
    thread = await spawn_event_thread(
        interaction,
        title=info["name"],
        event_type="secret_realm",
        event_key=event_key,
        expires_at=ends_at,
        announcement=(
            f"🌀 **SECRET REALM OPENED — {info['name']}**\n"
            f"📍 **{info['location']}**\n{info['description']}\n\n"
            f"Opened by an administrator. The entrance closes <t:{int(ends_at)}:R>."
        ),
    )
    await interaction.followup.send(
        f"✅ Opened **{info['name']}**. " + (f"Scene: {thread.mention}" if thread else "The realm opened, but no thread could be created."),
        ephemeral=False,
    )


@admin_spawnrealm.autocomplete("realm")
async def admin_spawnrealm_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    needle = current.casefold().strip()
    choices = []
    for rid, info in WORLD.secret_realms.items():
        name = str(info["name"])
        if not needle or needle in name.casefold() or needle in rid.casefold():
            choices.append(app_commands.Choice(name=name[:100], value=rid[:100]))
    return choices[:25]


@registered_group_command(admin_world_group, name="closeevent", description="Close an active event/secret realm immediately")
async def admin_closeevent(interaction: discord.Interaction, event_key: str) -> None:
    if not await require_admin(interaction):
        return
    record = await DB.get_event_thread_by_key(event_key.strip())
    if not record:
        await interaction.response.send_message(
            "No active event thread was found for that key. Use **/admin world events** first.", ephemeral=False
        )
        return
    await interaction.response.defer(ephemeral=False)
    await bot.close_event_scene(record, manual=True)
    await audit_admin(interaction, "event.close", target=event_key.strip(), before={"title": record.get("title"), "event_type": record.get("event_type")})
    await interaction.followup.send(f"🔒 Closed **{record['title']}** and archived its thread.", ephemeral=False)


@admin_closeevent.autocomplete("event_key")
async def admin_closeevent_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    events = await DB.get_active_world_events()
    needle = current.casefold().strip()
    choices: list[app_commands.Choice[str]] = []
    for event in events:
        if not event.get("thread_id"):
            continue
        key = str(event["event_key"])
        label = f"{event['title']} — {event['event_type']}"
        if not needle or needle in key.casefold() or needle in label.casefold():
            choices.append(app_commands.Choice(name=label[:100], value=key[:100]))
    return choices[:25]


async def admin_closeevent_hub_options(
    interaction: discord.Interaction, current: str
) -> list[HubDynamicOption]:
    """Rich live dropdown entries for Admin → World → Close Event."""
    events = await DB.get_active_world_events()
    needle = current.casefold().strip()
    now = time.time()
    options: list[HubDynamicOption] = []
    for event in events:
        if not event.get("thread_id"):
            continue
        key = str(event["event_key"])
        title = str(event["title"])
        event_type = str(event["event_type"])
        location = str(event.get("location") or "Unknown location")
        searchable = f"{title} {event_type} {location} {key}".casefold()
        if needle and needle not in searchable:
            continue
        remaining = max(0, int(float(event["ends_at"]) - now))
        options.append(
            HubDynamicOption(
                label=title[:100],
                value=key,
                description=f"{event_type} • {location} • closes in {human_duration(remaining)}"[:100],
                emoji="🌌" if event_type != "secret_realm" else "🌀",
            )
        )
    return options[:25]


register_hub_option_provider(admin_closeevent, "event_key", admin_closeevent_hub_options)


@registered_root_command(name="begin", description="Choose a family, cultivation style, and create your cultivator", guild=GUILD)
async def begin(interaction: discord.Interaction) -> None:
    existing = await DB.get_character(interaction.user.id)
    if existing:
        await interaction.response.send_message(
            "You already have a character. Use **/character → Overview**.", ephemeral=True
        )
        return

    begin_ch = await configured_begin_channel(interaction.guild)
    if begin_ch is not None:
        in_begin_channel = interaction.channel_id == begin_ch.id
        if isinstance(interaction.channel, discord.Thread):
            in_begin_channel = in_begin_channel or interaction.channel.parent_id == begin_ch.id
        if not in_begin_channel:
            await interaction.response.send_message(
                f"🌱 Character creation begins in {begin_ch.mention}. Use **/begin** there.", ephemeral=True
            )
            return

    wt = await current_world_time()
    try:
        offer_envelope = await ENGINE.authoritative_action(
            "character.family_options",
            interaction.user.id,
            {"world_name": "Mortal World"},
            action_id=f"discord:{interaction.id}:character.family_options",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ Could not generate canonical birth families: {exc}", ephemeral=True)
        return
    offer_result = dict(offer_envelope.get("result") or {})
    families = [dict(row) for row in offer_result.get("families", [])]
    if not families:
        await interaction.response.send_message("No canonical birth families are available. Try **/begin** again.", ephemeral=True)
        return
    view = BirthFamilyView(interaction.user.id, families, int(offer_envelope.get("state_version", 0)))
    await interaction.response.send_message(
        embed=view.current_embed(),
        view=view,
        ephemeral=True,
    )


GENDER_CHOICES = [
    app_commands.Choice(name="Male", value="male"),
    app_commands.Choice(name="Female", value="female"),
]


@registered_root_command(name="gender", description="Set Male or Female for gendered realm titles and forms of address", guild=GUILD)
@app_commands.choices(gender=GENDER_CHOICES)
async def set_gender(interaction: discord.Interaction, gender: app_commands.Choice[str]) -> None:
    c = await require_character(interaction)
    if not c:
        return
    await DB.set_gender(interaction.user.id, gender.value)
    main_name = WORLD.realm_name(c["realm_index"], gender.value)
    body_name = WORLD.body_realm_name(c.get("body_realm_index", 0), gender.value)
    await interaction.response.send_message(
        f"✅ Character sex set to **{gender.name}**.\n"
        f"Qi realm title: **{main_name}**\nBody realm title: **{body_name}**\n"
        "This changes titles/names only; it never changes stats, rolls, or progression.",
        ephemeral=False,
    )


@registered_root_command(name="sheet", description="View your cultivation character", guild=GUILD)
async def sheet(interaction: discord.Interaction) -> None:
    c = await require_character(interaction, allow_deceased=True)
    if not c:
        return
    realm = WORLD.realm_name(c["realm_index"], c.get("gender"))
    cost = WORLD.phase_cost(c["realm_index"], c["phase"])
    body_realm = WORLD.body_realm_name(c.get("body_realm_index", 0), c.get("gender"))
    body_cost = WORLD.body_phase_cost(c.get("body_realm_index", 0), c.get("body_phase", 1))
    perfection = await DB.get_perfection(interaction.user.id, c["realm_index"])
    body_perfection = await DB.get_body_perfection(interaction.user.id, c.get("body_realm_index", 0))
    inheritances = await DB.get_inheritances(interaction.user.id)
    membership = await DB.get_sect_membership(interaction.user.id)
    master = await DB.get_master(interaction.user.id)
    wallet = await DB.get_wallet(interaction.user.id)
    birth_family = await DB.get_birth_family(interaction.user.id)
    laws = await DB.get_law_progress(interaction.user.id)
    abode = await DB.get_abode(interaction.user.id)
    soul_legacy = await DB.get_soul_legacy(interaction.user.id)
    aptitudes = await DB.get_aptitudes(interaction.user.id)
    a = c["attributes"]
    wt = await current_world_time()
    life = await authoritative_lifespan(interaction.user.id)
    embed = discord.Embed(title=f"{c['name']} — {realm}", description=c["concept"][:4096])
    embed.add_field(
        name="Qi Cultivation",
        value=f"**{realm} • Stage {c['phase']}**\nEssence {c['cultivation']} / {cost}",
        inline=True,
    )
    embed.add_field(
        name="Body Cultivation",
        value=f"**{body_realm} • Stage {c.get('body_phase', 1)}**\nEssence {c.get('body_cultivation', 0)} / {body_cost}",
        inline=True,
    )
    root_profile = aptitudes.get("root") or {}
    root_elements = "/".join(str(x) for x in root_profile.get("elements", [c["spiritual_root"]]))
    root_mutation = str(root_profile.get("mutation") or "")
    mutation_name = WORLD.spiritual_root_system.get("mutations", {}).get(root_mutation, {}).get("name")
    root_text = (
        f"**{root_profile.get('grade','Common')}** • {root_elements}\n"
        f"Purity {int(root_profile.get('purity',50))}% • Stability {int(root_profile.get('stability',100))}%"
    )
    if mutation_name:
        root_text += f"\nMutation: **{mutation_name}**"
    embed.add_field(name="Spiritual Root", value=root_text, inline=True)
    embed.add_field(name="Path", value=c["path"], inline=True)
    location_display=c["location"]
    abode_location=await DB.get_abode_by_location(c["location"])
    personal_location=await DB.get_personal_world_by_location(c["location"])
    if abode_location: location_display=f"{abode_location['name']} ({player_property_label(abode_location)})"
    elif personal_location: location_display=f"{personal_location['name']} (Personal World)"
    embed.add_field(name="Location", value=location_display, inline=True)
    wallet_lines=[f"{WORLD.currency_name(cid)}: **{int(balance):,}**" for cid,balance in wallet.items() if int(balance)>0]
    embed.add_field(name="Wallet", value="\n".join(wallet_lines[:6]) if wallet_lines else "Empty", inline=True)
    embed.add_field(
        name="Resources",
        value=(
            f"❤️ Vitality **{c['vitality']}/{c['vitality_max']}**\n"
            f"💠 Qi **{c['qi']}/{c['qi_max']}**\n"
            f"✨ Insight XP **{c['insight_xp']}**"
        ),
        inline=True,
    )
    if life.ageless:
        life_text = f"Age **{life.age_years:.1f}** • **Ageless by cultivation**"
    else:
        life_text = (
            f"Age **{life.age_years:.1f}** / **{life.total_years} years**\n"
            f"Natural {life.natural_years} + cultivation {life.cultivation_bonus_years} + medicine {life.extension_years}"
        )
    if getattr(life, "aging_paused", False):
        paused_years = float(getattr(life, "paused_game_minutes", 0) or 0) / float(MINUTES_PER_YEAR)
        life_text += f"\n⏸️ **Inactive aging paused** • {paused_years:,.1f} game-years protected"
    if c.get("life_status") == "deceased":
        life_text += "\n🕯️ **Deceased — lifespan exhausted**"
    embed.add_field(name="Age & Lifespan", value=life_text, inline=False)
    embed.add_field(
        name="Identity & Legacy",
        value=(
            f"Sex **{(c.get('gender') if c.get('gender') in {'male','female'} else 'Not set').title()}**\n"
            f"Inheritances **{len(inheritances)}**\n"
            f"Karma **{karma_label(int(c.get('karma_score',0)))}** ({int(c.get('karma_score',0)):+d})"
        ),
        inline=True,
    )
    if c.get("life_status") == "alive":
        try:
            sense_stats = dict(await ENGINE.action(
                "sense.status", interaction.user.id, {},
            ) or {})
            sense_text = (
                f"Power **{int(sense_stats.get('power', 0))}** • Precision **{int(sense_stats.get('precision', 0))}**\n"
                f"Range **{int(sense_stats.get('range_m', 0)):,} m** • Concealment **{'Active' if sense_stats.get('concealment_active') else 'Off'}**"
            )
        except GameEngineError:
            sense_text = "Spiritual Sense data is temporarily unavailable."
    else:
        sense_text = "Dormant while the soul turns through Samsara."
    embed.add_field(name="Spiritual Sense", value=sense_text, inline=False)
    if WORLD.dual_resonance_active(c):
        embed.add_field(
            name="☯ Dual Cultivation Resonance",
            value="**Active** — Qi and Body cultivation are exactly aligned. +10% training gains and +1 breakthrough/combat checks.",
            inline=False,
        )
    if membership:
        sect_value = (
            f"{membership['sect_name']} • {membership['rank_name']}"
            f"\nContribution: {membership.get('contribution_points', 0)} • Influence: {membership.get('influence', 0)}"
        )
        if master:
            sect_value += f"\nMaster: {master['name']}"
        embed.add_field(name="Sect", value=sect_value, inline=False)
    if birth_family:
        embed.add_field(
            name="Birth Family",
            value=(
                f"{birth_family['family_name']} • **{family_tier_name(int(birth_family.get('tier',1)))}**\n"
                f"Wealth {birth_family.get('wealth',0)} • Influence {birth_family.get('influence',0)} • Stability {birth_family.get('stability',0)}\n"
                f"Family head: {birth_family.get('head_title','Family Head')} {birth_family.get('head_name','Unknown')}"
            ),
            inline=False,
        )
    bloodline = aptitudes.get("bloodline")
    if bloodline:
        techniques = list(bloodline.get("unlocked_techniques") or [])
        embed.add_field(
            name="Bloodline",
            value=(
                f"**{bloodline.get('name')}** • {str(bloodline.get('state','dormant')).title()}\n"
                f"Purity {int(bloodline.get('purity',0))}% • Stage {int(bloodline.get('evolution_stage',0))} • "
                f"Rejection {int(bloodline.get('rejection',0))}%"
                + (f"\nTechniques: {', '.join(str(x) for x in techniques[:3])}" if techniques else "")
            ),
            inline=False,
        )
    physique = aptitudes.get("physique") or {}
    embed.add_field(
        name="Physique",
        value=(
            f"**{physique.get('name','Ordinary Mortal Body')}** • {str(physique.get('state','ordinary')).title()}\n"
            f"Stage {int(physique.get('evolution_stage',0))} • Stability {int(physique.get('stability',100))}% • "
            f"Instability {int(physique.get('instability',0))}%"
        ),
        inline=False,
    )
    if int(soul_legacy.get("incarnation_count",1)) > 1 or int(soul_legacy.get("legacy_points",0)) > 0:
        trait=str(soul_legacy.get("special_trait") or "None")
        embed.add_field(
            name="☸️ Soul Legacy",
            value=(
                f"Incarnation **{int(soul_legacy.get('incarnation_count',1))}** • Legacy **{int(soul_legacy.get('legacy_points',0))}**\n"
                f"Memory {int(soul_legacy.get('awakened_memory',0))}/{int(soul_legacy.get('memory_seed',0))}% • Talent Echo {int(soul_legacy.get('talent_echo',0))}% • Law Echo {int(soul_legacy.get('law_echo',0))}%\n"
                f"Trait: **{trait}**"
            ),
            inline=False,
        )
    if laws:
        top=laws[0]; definition=WORLD.law_definition(str(top['law_id'])) or {}; stage=WORLD.law_stage(int(top['comprehension']))
        embed.add_field(name="Law Comprehension",value=f"{definition.get('name', top['law_id'])} • **{stage['name']}** • {top['comprehension']}%",inline=False)
    if abode:
        embed.add_field(name="Player Property",value=f"{player_property_emoji(abode)} {abode['name']} • {player_property_label(abode)} • Entrance: {abode['base_location']}\n" + " • ".join(player_property_facility_lines(abode)[:6]),inline=False)
    active_effects, _, _effect_wt = await current_effect_modifiers(interaction.user.id)
    if active_effects:
        effect_names = ", ".join(str(e.get("name", e.get("effect_key", "Effect"))) for e in active_effects[:5])
        if len(active_effects) > 5:
            effect_names += f" +{len(active_effects)-5} more"
        embed.add_field(name="Active Effects", value=effect_names, inline=False)
    embed.add_field(name="World Time", value=wt.display, inline=False)
    if c["phase"] == 9:
        if perfection and perfection["completed"]:
            perfect_text = "★ Perfect Realm achieved — your foundation carries permanent bonuses."
        elif perfection and perfection["active"]:
            perfect_text = (
                f"Perfection **{perfection['progress']}%** • Training {perfection['training_progress']}/{WORLD.perfection_training_cap()}\n"
                f"Quests {perfection['completed_quests']}/{WORLD.perfection_quest_count()} • use **/quest → Realm Perfection → Info**"
            )
        else:
            perfect_text = "Stage 9 choice unlocked: **/quest → Realm Perfection → Start** or **/quest → Main Progression → Breakthrough** to skip perfection."
        embed.add_field(name="Realm Perfection", value=perfect_text, inline=False)
    if c.get("body_phase", 1) == 9:
        if body_perfection and body_perfection["completed"]:
            body_perfect_text = "★ Perfect Body Realm achieved — your physical foundation carries permanent bonuses."
        elif body_perfection and body_perfection["active"]:
            body_perfect_text = (
                f"Perfection **{body_perfection['progress']}%** • Training {body_perfection['training_progress']}/{WORLD.body_perfection_training_cap()}\n"
                f"Quests {body_perfection['completed_quests']}/{WORLD.body_perfection_quest_count()} • use **/quest → Body Perfection → Info**"
            )
        else:
            body_perfect_text = "Body Stage 9 choice unlocked: **/quest → Body Perfection → Start** or **/cultivation → Body Cultivation → Breakthrough**."
        embed.add_field(name="Body Perfection", value=body_perfect_text, inline=False)

    embed.add_field(
        name="Attributes",
        value=(
            f"Body {a['body']} • Agility {a['agility']} • Spirit {a['spirit']}\n"
            f"Insight {a['insight']} • Will {a['will']} • Presence {a['presence']}"
        ),
        inline=False,
    )
    embed.set_footer(text=f"Origin: {c['origin']}")
    await interaction.response.send_message(embed=embed)


aptitude_group = app_commands.Group(
    name="aptitude",
    description="Spiritual roots, ancestral bloodlines and special physiques",
)
APTITUDE_TARGET_CHOICES = [
    app_commands.Choice(name="Spiritual Root", value="root"),
    app_commands.Choice(name="Bloodline", value="bloodline"),
    app_commands.Choice(name="Physique", value="physique"),
]
AWAKEN_TARGET_CHOICES = [
    app_commands.Choice(name="Bloodline", value="bloodline"),
    app_commands.Choice(name="Physique", value="physique"),
]


def _root_summary(root: dict, character: dict) -> str:
    elements = "/".join(str(value) for value in root.get("elements", [character.get("spiritual_root", "Mortal Root")]))
    mutation_key = str(root.get("mutation") or "")
    mutation = WORLD.spiritual_root_system.get("mutations", {}).get(mutation_key, {}).get("name", mutation_key or "None")
    compatibility = root_compatibility(
        root.get("elements", []), str(character.get("path", "")), WORLD.spiritual_root_system, mutation_key,
    )
    return (
        f"🌿 **Spiritual Root**\n"
        f"Grade: **{root.get('grade','Common')}** • Elements: **{elements}**\n"
        f"Purity: **{int(root.get('purity',50))}%** • Stability: **{int(root.get('stability',100))}%**\n"
        f"Path compatibility: **{compatibility}%** • Refinement: **{int(root.get('refinement_progress',0))}%**\n"
        f"Mutation: **{mutation}**"
    )


def _bloodline_summary(bloodline: dict | None) -> str:
    if not bloodline:
        return "🩸 **Bloodline**\nNo recognized ancestral bloodline is carried by this incarnation."
    _, definition = bloodline_definition(bloodline, WORLD.bloodlines)
    stage = int(bloodline.get("evolution_stage", 0))
    evolutions = list(definition.get("evolutions", []))
    stage_name = evolutions[min(len(evolutions), max(1, stage)) - 1].get("name") if evolutions and stage else "Dormant Lineage"
    techniques = list(bloodline.get("unlocked_techniques") or [])
    return (
        f"🩸 **{bloodline.get('name','Ancestral Bloodline')}**\n"
        f"State: **{str(bloodline.get('state','dormant')).title()}** • Evolution: **{stage_name}**\n"
        f"Affinity: **{bloodline.get('affinity','None')}** • Purity: **{int(bloodline.get('purity',0))}%**\n"
        f"Progress: **{int(bloodline.get('progress',0))}%** • Rejection: **{int(bloodline.get('rejection',0))}%**\n"
        f"Mutation: **{bloodline.get('mutation') or 'None'}**\n"
        f"Ancestral techniques: **{', '.join(str(x) for x in techniques) if techniques else 'None awakened'}**"
    )


def _physique_summary(physique: dict | None) -> str:
    physique = physique or {}
    definition = WORLD.physiques.get(str(physique.get("physique_id", "ordinary_mortal_body")), {})
    stage = int(physique.get("evolution_stage", 0))
    evolutions = list(definition.get("evolutions", []))
    stage_name = evolutions[min(len(evolutions), max(1, stage)) - 1].get("name") if evolutions and stage else "Unawakened"
    return (
        f"💠 **{physique.get('name','Ordinary Mortal Body')}**\n"
        f"State: **{str(physique.get('state','ordinary')).title()}** • Evolution: **{stage_name}**\n"
        f"Progress: **{int(physique.get('progress',0))}%** • Stability: **{int(physique.get('stability',100))}%** • "
        f"Instability: **{int(physique.get('instability',0))}%**\n"
        f"Advantage: {definition.get('advantage','No innate special-body advantage.')}\n"
        f"Drawback: {definition.get('drawback','No innate special-body burden.')}"
    )


@registered_group_command(aptitude_group, name="status", description="Show your root, bloodline and physique together")
async def aptitude_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction, allow_deceased=True)
    if not c:
        return
    bundle = await DB.get_aptitudes(interaction.user.id)
    await reply_long(
        interaction,
        "\n\n".join([
            _root_summary(bundle.get("root") or {}, c),
            _bloodline_summary(bundle.get("bloodline")),
            _physique_summary(bundle.get("physique")),
        ]),
        ephemeral=False,
    )


@registered_group_command(aptitude_group, name="root", description="Inspect spiritual-root grade, purity, elements and compatibility")
async def aptitude_root(interaction: discord.Interaction) -> None:
    c = await require_character(interaction, allow_deceased=True)
    if not c:
        return
    bundle = await DB.get_aptitudes(interaction.user.id)
    await interaction.response.send_message(_root_summary(bundle.get("root") or {}, c), ephemeral=False)


@registered_group_command(aptitude_group, name="bloodline", description="Inspect bloodline awakening, purity, rejection and techniques")
async def aptitude_bloodline(interaction: discord.Interaction) -> None:
    c = await require_character(interaction, allow_deceased=True)
    if not c:
        return
    bundle = await DB.get_aptitudes(interaction.user.id)
    await interaction.response.send_message(_bloodline_summary(bundle.get("bloodline")), ephemeral=False)


@registered_group_command(aptitude_group, name="physique", description="Inspect special-physique progression, advantages and drawbacks")
async def aptitude_physique(interaction: discord.Interaction) -> None:
    c = await require_character(interaction, allow_deceased=True)
    if not c:
        return
    bundle = await DB.get_aptitudes(interaction.user.id)
    await interaction.response.send_message(_physique_summary(bundle.get("physique")), ephemeral=False)


@registered_group_command(aptitude_group, name="temper", description="Spend cultivation essence to progress an innate aptitude")
@app_commands.choices(target=APTITUDE_TARGET_CHOICES)
@serialized_user_action
async def aptitude_temper(interaction: discord.Interaction, target: app_commands.Choice[str]) -> None:
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "aptitude.temper",
            interaction.user.id,
            {
                "target": target.value,
                
                "cooldown_seconds": max(300, SETTINGS.cultivate_cooldown_minutes * 60),
            },
            action_id=f"discord:{interaction.id}:aptitude.temper:{target.value}",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    bundle = dict(result.get("aptitudes") or {})
    record = dict(bundle.get(target.value) or {})
    progress_key = "refinement_progress" if target.value == "root" else "progress"
    progress = int(record.get(progress_key, 0))
    awarded = int(result.get("awarded", 0))
    cost = int(result.get("cost", 0))
    await interaction.response.send_message(
        f"🔥 **{target.name} Tempering**\nSpent **{cost}** essence and gained **+{awarded}%** progress.\n"
        f"Progress: **{progress}% / 100%**"
        + ("\n✨ The aptitude is ready for its next awakening/evolution attempt." if progress >= 100 else "")
    )

@registered_group_command(aptitude_group, name="awaken", description="Attempt to awaken a prepared bloodline or physique")
@app_commands.choices(target=AWAKEN_TARGET_CHOICES)
@serialized_user_action
async def aptitude_awaken(interaction: discord.Interaction, target: app_commands.Choice[str]) -> None:
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "aptitude.awaken",
            interaction.user.id,
            {"target": target.value},
            action_id=f"discord:{interaction.id}:aptitude.awaken:{target.value}",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    roll = SimpleNamespace(**dict(result.get("roll") or {}))
    state = str(result.get("state") or "unknown")
    await interaction.response.send_message(
        f"✨ **{target.name} Awakening**\n{roll_line(roll)}\n"
        + (
            f"The {target.name.lower()} awakens successfully. State: **{state.title()}**."
            if bool(getattr(roll, "success", False))
            else "Awakening failed. The persistent backlash has been recorded; use **/cultivation → Aptitudes → harmonize** before rejection or instability becomes severe."
        )
    )

@registered_group_command(aptitude_group, name="evolve", description="Attempt the next grade or ancestral evolution")
@app_commands.choices(target=APTITUDE_TARGET_CHOICES)
@serialized_user_action
async def aptitude_evolve(interaction: discord.Interaction, target: app_commands.Choice[str]) -> None:
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "aptitude.evolve",
            interaction.user.id,
            {"target": target.value},
            action_id=f"discord:{interaction.id}:aptitude.evolve:{target.value}",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    roll = SimpleNamespace(**dict(result.get("roll") or {}))
    outcome = dict(result.get("outcome") or {})
    if target.value == "root":
        outcome_text = f"Root grade: **{outcome.get('grade', 'Unknown')}** • Stability: **{int(outcome.get('stability', 0))}%**"
    elif target.value == "bloodline":
        outcome_text = (
            f"Stage: **{int(outcome.get('stage', 0))}** • Purity: **{int(outcome.get('purity', 0))}%** • "
            f"Rejection: **{int(outcome.get('rejection', 0))}%**"
        )
    else:
        outcome_text = (
            f"Stage: **{int(outcome.get('stage', 0))}** • Stability: **{int(outcome.get('stability', 0))}%** • "
            f"Instability: **{int(outcome.get('instability', 0))}%**"
        )
    await interaction.response.send_message(
        f"🌌 **{target.name} Evolution**\n{roll_line(roll)}\n{outcome_text}\n"
        + ("The evolution succeeds." if bool(getattr(roll, "success", False)) else "The failure caused a persistent setback that must be harmonized or overcome.")
    )

@registered_group_command(aptitude_group, name="harmonize", description="Spend essence to reduce rejection or instability and restore stability")
@app_commands.choices(target=APTITUDE_TARGET_CHOICES)
@serialized_user_action
async def aptitude_harmonize(interaction: discord.Interaction, target: app_commands.Choice[str]) -> None:
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "aptitude.harmonize",
            interaction.user.id,
            {
                "target": target.value,
                
                "cooldown_seconds": max(300, SETTINGS.cultivate_cooldown_minutes * 60),
            },
            action_id=f"discord:{interaction.id}:aptitude.harmonize:{target.value}",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    bundle = dict(result.get("aptitudes") or {})
    amount = int(result.get("amount", 0))
    cost = int(result.get("cost", 0))
    summary = (
        _root_summary(dict(bundle.get("root") or {}), c)
        if target.value == "root"
        else _bloodline_summary(bundle.get("bloodline"))
        if target.value == "bloodline"
        else _physique_summary(dict(bundle.get("physique") or {}))
    )
    await reply_long(
        interaction,
        f"☯️ Harmonization spent **{cost}** essence and restored **{amount}** points.\n\n{summary}",
        ephemeral=False,
    )

async def _player_dashboard_embed(user_id: int, *, guild_id: int | None, page: str = "overview") -> discord.Embed:
    c = await DB.get_character(int(user_id))
    if not c:
        return discord.Embed(title="🧑 Cultivator Dashboard", description="No character exists yet. Use `/begin`.")
    scene = await SCENES.current(int(user_id), guild_id=guild_id)
    membership = await DB.get_sect_membership(int(user_id))
    abode = await DB.get_abode(int(user_id))
    sect_abode = await DB.get_sect_abode(int(user_id))
    active_quests = await DB.list_character_quests(int(user_id), status="active")
    realm = WORLD.realm_name(int(c.get("realm_index", 0)), c.get("gender"))
    embed = discord.Embed(title=f"🧑 {c['name']} — Player Dashboard", description=f"**{realm} • Stage {c.get('phase',1)}**")

    if page == "scene":
        if scene:
            embed.add_field(name="Physical Location", value=scene.physical_location, inline=False)
            embed.add_field(name="Active Scene", value=f"**{scene.scene_label}**\nType: `{scene.scene_type}`", inline=False)
            embed.add_field(name="Discord Scene", value=(f"<#{scene.channel_id}>" if scene.channel_id else "No dedicated scene thread"), inline=False)
        embed.set_footer(text="Physical location controls travel/access. Active scene controls local RP routing.")
        return embed

    if page == "relationships":
        rows = await DB.list_npc_relationships(int(user_id), 8)
        if not rows:
            embed.description = "No persistent NPC relationships have developed yet. Talk to named NPCs in the world."
        else:
            for row in rows[:8]:
                label = NPC_RELATIONSHIPS.public_label(row)
                embed.add_field(
                    name=f"🤝 {row['npc_name']} — {label}",
                    value=(f"Trust {int(row['trust']):+d} • Respect {int(row['respect']):+d} • Fear {int(row['fear']):+d}\n"
                           f"Debt {int(row['debt']):+d} • Grudge {int(row['grudge']):+d} • Encounters {int(row['encounter_count'])}"),
                    inline=False,
                )
        return embed

    if page == "quests":
        available = await QUESTS.available(int(user_id))
        if active_quests:
            for row in active_quests[:8]:
                definition = QUEST_DEFINITIONS.get(str(row["quest_key"]), {})
                progress = row.get("progress") or {}
                parts = []
                for obj in definition.get("objectives", []):
                    cur = int(progress.get(str(obj["id"]), 0)); req = max(1, int(obj.get("count",1)))
                    parts.append(f"{'✅' if cur >= req else '▫️'} {obj.get('label',obj['id'])} **{cur}/{req}**")
                embed.add_field(name=f"📜 {definition.get('title', row['quest_key'])}", value="\n".join(parts) or "In progress", inline=False)
        else:
            embed.add_field(name="Active Quests", value="None", inline=False)
        embed.add_field(name="Available", value="\n".join(f"• {q['title']}" for q in available[:8]) or "None", inline=False)
        return embed

    scene_text = scene.scene_label if scene else str(c.get("location") or "Unknown")
    embed.add_field(name="📍 Current Scene", value=scene_text, inline=True)
    embed.add_field(name="✨ Resources", value=f"Vitality **{c.get('vitality',0)}/{c.get('vitality_max',0)}**\nQi **{c.get('qi',0)}/{c.get('qi_max',0)}**", inline=True)
    embed.add_field(name="🧭 Cultivation Path", value=str(c.get("path") or "Unknown"), inline=True)
    embed.add_field(name="🏯 Sect", value=(f"{membership['sect_name']} • {membership['rank_name']}" if membership else "Unaffiliated"), inline=False)
    home = abode or sect_abode
    embed.add_field(name="🏡 Residence", value=(str(home.get("name")) if home else "None established"), inline=True)
    embed.add_field(name="📜 Active Quests", value=str(len(active_quests)), inline=True)
    embed.set_footer(text="Use the buttons below for Scene, Relationships and Quests.")
    return embed


class PlayerDashboardView(discord.ui.View):
    def __init__(self, user_id: int, guild_id: int | None) -> None:
        super().__init__(timeout=300)
        self.user_id = int(user_id)
        self.guild_id = int(guild_id) if guild_id is not None else None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This dashboard belongs to another cultivator.", ephemeral=False)
            return False
        return True

    async def _show(self, interaction: discord.Interaction, page: str) -> None:
        await interaction.response.edit_message(embed=await _player_dashboard_embed(self.user_id, guild_id=self.guild_id, page=page), view=self)

    @discord.ui.button(label="Overview", emoji="🧑", style=discord.ButtonStyle.primary)
    async def overview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._show(interaction, "overview")

    @discord.ui.button(label="Scene", emoji="📍", style=discord.ButtonStyle.secondary)
    async def scene(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._show(interaction, "scene")

    @discord.ui.button(label="Relationships", emoji="🤝", style=discord.ButtonStyle.secondary)
    async def relationships(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._show(interaction, "relationships")

    @discord.ui.button(label="Quests", emoji="📜", style=discord.ButtonStyle.secondary)
    async def quests(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._show(interaction, "quests")


@registered_root_command(name="me", description="Open your v0.18 player dashboard", guild=GUILD)
async def player_dashboard(interaction: discord.Interaction) -> None:
    c = await require_character(interaction, allow_deceased=True)
    if not c:
        return
    view = PlayerDashboardView(interaction.user.id, interaction.guild_id)
    await interaction.response.send_message(
        embed=await _player_dashboard_embed(interaction.user.id, guild_id=interaction.guild_id),
        view=view,
        ephemeral=False,
    )


class QuestAcceptSelect(discord.ui.Select):
    def __init__(self, user_id: int, available: list[dict]) -> None:
        self.user_id = int(user_id)
        super().__init__(
            placeholder="Accept an available quest…", min_values=1, max_values=1,
            options=[discord.SelectOption(label=str(q["title"])[:100], value=str(q["quest_key"]), description=str(q.get("description", ""))[:100]) for q in available[:25]],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This quest panel belongs to another cultivator.", ephemeral=False); return
        wt = await current_world_time()
        row = await QUESTS.accept(self.user_id, self.values[0], game_minute=wt.total_minutes)
        definition = QUEST_DEFINITIONS.get(str(row["quest_key"]), {})
        await interaction.response.send_message(f"📜 Quest accepted: **{definition.get('title', row['quest_key'])}**", ephemeral=False)


class QuestDashboardView(discord.ui.View):
    def __init__(self, user_id: int, available: list[dict]) -> None:
        super().__init__(timeout=300)
        if available:
            self.add_item(QuestAcceptSelect(user_id, available))


@registered_root_command(name="quests", description="View and accept objective-driven quests", guild=GUILD)
async def quests_command(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    active = await DB.list_character_quests(interaction.user.id, status="active")
    available = await QUESTS.available(interaction.user.id)
    lines = ["📜 **Quest Journal**"]
    if active:
        for row in active[:10]:
            definition = QUEST_DEFINITIONS.get(str(row["quest_key"]), {})
            progress = row.get("progress") or {}
            objectives = []
            for obj in definition.get("objectives", []):
                cur=int(progress.get(str(obj["id"]),0)); req=max(1,int(obj.get("count",1)))
                objectives.append(f"{'✅' if cur >= req else '▫️'} {obj.get('label',obj['id'])} {cur}/{req}")
            lines.append(f"\n**{definition.get('title', row['quest_key'])}**\n" + "\n".join(objectives))
    else:
        lines.append("\nNo active quests.")
    if available:
        lines.append("\n**Available**\n" + "\n".join(f"• {q['title']}" for q in available[:10]))
    await interaction.response.send_message("\n".join(lines), view=QuestDashboardView(interaction.user.id, available), ephemeral=False)


@registered_root_command(name="inventory", description="View your items and materials", guild=GUILD)
async def inventory(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    inv = await DB.get_inventory(interaction.user.id)
    if not inv:
        text = "Your storage pouch is empty."
    else:
        lines = []
        for item_id, qty in inv.items():
            item = WORLD.items.get(item_id, {"name": item_id, "description": ""})
            lines.append(f"**{item['name']}** x{qty} — {item['description']}")
        text = "\n".join(lines)
    await interaction.response.send_message(
        f"**{c['name']}'s Carried Inventory**\nLow Spirit Stones: **{c['spirit_stones']}**\n\n{text}",
        ephemeral=False,
    )


@registered_root_command(name="cultivate", description="Meditate and gather cultivation essence", guild=GUILD)
@serialized_user_action
async def cultivate(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "cultivation.train",
            interaction.user.id,
            {"cooldown_seconds": SETTINGS.cultivate_cooldown_minutes * 60},
            action_id=f"discord:{interaction.id}:cultivation.train",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    gain, total, cost = int(result.get("gain", 0)), int(result.get("total", 0)), int(result.get("cost", 0))
    extra = ""
    if int(result.get("resonance_bonus", 0)):
        extra += f"\n☯ Dual Cultivation Resonance added **+{int(result['resonance_bonus'])}** to the attempted gain before the stage cap."
    extra += f"\n🕰️ {result.get('period','World-time')} cultivation flow: **x{float(result.get('time_mult',1)):.2f}** Qi efficiency."
    if result.get("root_resonance"):
        extra += f"\n🌿 **{c.get('spiritual_root')}** resonates with **{result.get('season','the season')}**: +10% seasonal Qi efficiency."
    if float(result.get("effect_mult", 1)) != 1.0:
        extra += f"\n💊 Active effects modified cultivation efficiency to **x{float(result['effect_mult']):.2f}**."
    if float(result.get("soul_mult", 1)) > 1.0:
        extra += f"\n☸️ Soul Legacy talent echo: **x{float(result['soul_mult']):.2f}** cultivation efficiency."
    if float(result.get("era_mult", 1)) != 1.0:
        extra += f"\n🌌 **{result.get('era_name') or 'World Era'}** modifies cultivation to **x{float(result['era_mult']):.2f}**."
    if float(result.get("manor_mult", 1)) != 1.0:
        extra += f"\n🏯 **{result.get('manor_name') or 'Sect Manor'}** Qi Gathering Array: **x{float(result['manor_mult']):.2f}** cultivation efficiency."
    if int(result.get("storm_bonus", 0)):
        extra += f"\n⚡ Active Qi Storm added **+{int(result['storm_bonus'])}** before the stage cap."
    if int(result.get("perfection_gain", 0)):
        extra += f"\n★ Realm refinement deepens by **+{int(result['perfection_gain'])}%**."
    ready = ""
    if result.get("ready"):
        ready = "\n✨ Stage 9 is full. Choose **/quest → Realm Perfection → Start** or **/quest → Main Progression → Breakthrough**." if int(c.get("phase", 1)) == 9 else "\n✨ You are ready to attempt **/quest → Main Progression → Breakthrough**."
    await interaction.response.send_message(
        f"🧘 **{c['name']} cultivates.**\nYou circulate qi through your meridians and gain **+{gain} cultivation essence**.\nProgress: **{total}/{cost}**{extra}{ready}"
    )
seclusion_group = app_commands.Group(
    name="seclusion",
    description="Closed-door background cultivation that progresses with world time",
)
SECLUSION_MODE_CHOICES = [
    app_commands.Choice(name="Qi Cultivation", value="qi"),
    app_commands.Choice(name="Body Cultivation", value="body"),
]


@registered_group_command(seclusion_group, name="start", description="Enter closed-door cultivation for a number of world-days")
@app_commands.choices(mode=SECLUSION_MODE_CHOICES)
@serialized_user_action
async def seclusion_start(
    interaction: discord.Interaction,
    mode: app_commands.Choice[str],
    days: app_commands.Range[int, 1, 365] = 7,
) -> None:
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    abode = await DB.get_abode_by_location(str(c.get("location", "")))
    member_manor = await DB.get_member_sect_manor(interaction.user.id)
    manor_here = (
        member_manor
        if member_manor and str(member_manor.get("base_location", "")) == str(c.get("location", ""))
        else None
    )
    loc_def = await DB.get_location_definition(str(c.get("location", ""))) or WORLD.locations.get(str(c.get("location", "")), {})
    safe = bool(loc_def.get("safe_zone", False))
    if not abode and not safe and not manor_here:
        await interaction.response.send_message(
            "Closed-door seclusion requires a **protected/safe location**, a **player-owned property with a cultivation chamber**, or your sect's **Manor**. "
            "You cannot safely disappear into meditation while exposed to ordinary danger.",
            ephemeral=False,
        )
        return
    abode_level = int(abode.get("cultivation_level", 0)) if abode else 0
    env_mult = seclusion_environment_multiplier(abode_cultivation_level=abode_level, safe_zone=safe)
    if manor_here:
        env_mult *= manor_seclusion_multiplier(manor_here)
    array_here = await DB.get_active_location_array(str(c.get("location", "")), wt.total_minutes)
    array_seclusion_mult = 1.0
    if array_here and mode.value == "qi":
        payload = normalize_effect_payload(dict(array_here.get("effect") or {}))
        array_seclusion_mult = float(aggregate_modifiers([payload]).get("cultivation_gain_mult", 1.0))
        env_mult *= array_seclusion_mult
    try:
        envelope = await ENGINE.authoritative_action(
            "seclusion.start", interaction.user.id,
            {"mode": mode.value, "duration_game_minutes": int(days) * MINUTES_PER_DAY,
             "location": str(c.get("location", "")), "environment_mult": env_mult},
            action_id=f"discord:{interaction.id}:seclusion.start",
        )
        state = dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}", ephemeral=False)
        return
    soul = await DB.get_soul_legacy(interaction.user.id)
    soul_mult = float(soul_legacy_modifiers(soul)["cultivation_mult"])
    daily = seclusion_daily_gain(c, mode=mode.value, environment_mult=env_mult, soul_cultivation_mult=soul_mult)
    if abode:
        env_label = f"{player_property_label(abode)} cultivation chamber Lv.{abode_level}"
    elif manor_here:
        env_label = f"{manor_here.get('name','Sect Manor')} • Qi Gathering Array Lv.{int(manor_here.get('qi_array_level',0))}"
    else:
        env_label = "protected meditation site"
    if array_seclusion_mult != 1.0 and array_here:
        env_label += f" • {array_here.get('name','Deployed Formation')} x{array_seclusion_mult:.2f}"
    await interaction.response.send_message(
        f"🔒 **Closed-Door Seclusion Begun**\n"
        f"Path: **{'Qi' if mode.value == 'qi' else 'Body'} Cultivation**\n"
        f"Duration: **{int(days)} world-days**\n"
        f"Location: **{c.get('location')}** ({env_label})\n"
        f"Environment efficiency: **x{env_mult:.2f}**\n"
        f"Projected background gain: about **{daily} essence per completed world-day**.\n\n"
        "Progress is settled automatically while the bot is online and catches up after restarts. "
        "Seclusion never auto-breaks through a stage. Any state-changing command will remain locked until you use **/cultivation → Seclusion → End** or the planned seclusion completes."
    )


@registered_group_command(seclusion_group, name="status", description="Check closed-door cultivation progress")
async def seclusion_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    state = await settle_seclusion_for_user(interaction.user.id, wt.total_minutes)
    if not state:
        state = await DB.get_seclusion(interaction.user.id, active_only=False)
    if not state:
        await interaction.response.send_message("You have no recorded seclusion session.", ephemeral=False)
        return
    remaining = max(0, int(state["ends_game_minute"]) - wt.total_minutes) if str(state.get("status")) == "active" else 0
    elapsed = max(0, min(wt.total_minutes, int(state["ends_game_minute"])) - int(state["started_game_minute"]))
    await interaction.response.send_message(
        f"🔒 **Seclusion Status**\n"
        f"State: **{str(state.get('status','unknown')).title()}**\n"
        f"Mode: **{str(state.get('mode','qi')).upper()}**\n"
        f"Location: **{state.get('start_location')}**\n"
        f"Elapsed: **{elapsed / MINUTES_PER_DAY:.1f} world-days**\n"
        f"Remaining: **{remaining / MINUTES_PER_DAY:.1f} world-days**\n"
        f"Cultivation awarded: **{int(state.get('accumulated_gain',0))}**\n"
        f"Environment: **x{float(state.get('environment_mult',1.0)):.2f}**\n"
        + ("Use **/cultivation → Seclusion → End** to emerge early." if str(state.get("status")) == "active" else f"Ended: **{state.get('ended_reason') or 'completed'}**"),
        ephemeral=False,
    )


@registered_group_command(seclusion_group, name="end", description="Leave seclusion early and settle completed background cultivation")
@serialized_user_action
async def seclusion_end(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    state = await DB.get_seclusion(interaction.user.id)
    if not state:
        await interaction.response.send_message("You are not in active seclusion.", ephemeral=False)
        return
    try:
        await ENGINE.authoritative_action(
            "seclusion.settle", interaction.user.id,
            {"minutes_per_day": MINUTES_PER_DAY, "force_end": True, "end_reason": "emerged early"},
            action_id=f"discord:{interaction.id}:seclusion.end",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}", ephemeral=False)
        return
    final = await DB.get_seclusion(interaction.user.id, active_only=False) or state
    await interaction.response.send_message(
        f"🚪 **You emerge from seclusion.**\n"
        f"Total background cultivation gained: **{int(final.get('accumulated_gain',0))}**.\n"
        "Any incomplete fraction of the current world-day produced no background gain."
    )


@registered_root_command(name="breakthrough", description="Attempt to advance your cultivation stage", guild=GUILD)
@serialized_user_action
async def breakthrough(interaction: discord.Interaction, confirm: bool = False) -> None:
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "cultivation.breakthrough",
            interaction.user.id,
            {"confirm": bool(confirm)},
            action_id=f"discord:{interaction.id}:cultivation.breakthrough",
        )
    except GameEngineError as exc:
        message = str(exc)
        if "perfection choice requires explicit confirmation" in message:
            message = "⚠️ **Stage 9 choice**\nYou can pursue **/quest → Realm Perfection → Start** for a stronger long-term foundation, or explicitly confirm this breakthrough to skip it."
        await interaction.response.send_message(f"❌ {message}" if not message.startswith("⚠️") else message, ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    roll = SimpleNamespace(**dict(result.get("roll") or {}))
    next_realm = str(result.get("to_realm") or "Unknown Realm")
    next_phase = int(result.get("to_stage", 1))
    success = bool(result.get("success"))
    await interaction.response.defer()
    breakthrough_context = await NARRATOR_CONTEXT.build(c, scene_type="cultivation breakthrough", query_text=f"breakthrough {next_realm} stage {next_phase}")
    try:
        narration = await NARRATOR.narrate_breakthrough(c, next_realm, next_phase, roll_line(roll), success, scene_context=breakthrough_context.text)
    except Exception:
        log.exception("Breakthrough narration failed")
        narration = "Qi surges through your meridians as the breakthrough reaches its fixed mechanical result."
    try:
        await DB.add_rag_memory(
            interaction.user.id, memory_kind="breakthrough", salience=88 if success else 66,
            location=str(c.get("location") or ""), source="breakthrough", game_minute=breakthrough_context.game_minute,
            summary=(f"{c.get('name','The cultivator')} attempted a breakthrough from {result.get('from_realm','Unknown')} Stage {result.get('from_stage',1)} to {next_realm} Stage {next_phase}. Outcome: {'success' if success else 'failure'}. Observed: {narration[:540]}"),
        )
    except Exception:
        log.exception("Could not persist breakthrough RAG memory")
    mechanical = roll_line(roll)
    if int(result.get("perfect_bonus", 0)):
        mechanical += "\n★ Perfect-foundation legacy bonus: **+2** to this breakthrough."
    if int(result.get("resonance_bonus", 0)):
        mechanical += "\n☯ Dual Cultivation Resonance: **+1** to this breakthrough."
    if int(result.get("innate_breakthrough_bonus", 0)):
        mechanical += f"\n🌿 Innate aptitude modifier: **{int(result['innate_breakthrough_bonus']):+d}** to this breakthrough."
    if success:
        mechanical += f"\n✨ Advanced to **{next_realm}, Stage {next_phase}**."
        master = dict(result.get("master_reward") or {})
        if master:
            mechanical += f"\n🎓 Your breakthrough feeds the master-disciple bond: **{master.get('master_name','Your master')}** receives **+{int(master.get('insight_xp',0))} Insight XP** and the lineage gains **+{int(master.get('attention',0))} Master Attention**."
        legacy = dict(result.get("soul_legacy") or {})
        if int(legacy.get("awakened_memory", 0)):
            mechanical += f"\n🕯️ Past-life memory awakened: **{int(legacy.get('awakened_memory',0))}% / {int(legacy.get('memory_seed',0))}%**."
    else:
        mechanical += f"\n⚠️ Breakthrough failed; **{int(result.get('failure_loss',0))} cultivation essence** was lost."
    await reply_long(interaction, f"{mechanical}\n\n{narration}")
# ---------------- Body Cultivation commands ----------------
body_group = app_commands.Group(name="body", description="Parallel body-cultivation progression")


@registered_group_command(body_group, name="sheet", description="View your body-cultivation realm and progress")
async def body_sheet(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    ri = int(c.get("body_realm_index", 0))
    ph = int(c.get("body_phase", 1))
    realm = WORLD.body_realm_name(ri, c.get("gender"))
    cost = WORLD.body_phase_cost(ri, ph)
    p = await DB.get_body_perfection(interaction.user.id, ri)
    lines = [
        f"💪 **{c['name']} — Body Cultivation**",
        f"Realm: **{realm} — Stage {ph}**",
        f"Body Essence: **{c.get('body_cultivation', 0)}/{cost}**",
        f"World: **{WORLD.body_realm_world(ri)}**",
    ]
    if WORLD.dual_resonance_active(c):
        lines.append("☯ **Dual Cultivation Resonance ACTIVE** — +10% training gains and +1 breakthrough/combat checks.")
    if ph == 9:
        if p and p["completed"]:
            lines.append("★ **Perfect Body Realm achieved.**")
        elif p and p["active"]:
            lines.append(
                f"★ Body Perfection: **{p['progress']}%** • Training {p['training_progress']}/{WORLD.body_perfection_training_cap()} • "
                f"Quests {p['completed_quests']}/{WORLD.body_perfection_quest_count()}"
            )
        else:
            lines.append("Stage 9 choice: **/quest → Body Perfection → Start** or **/cultivation → Body Cultivation → Breakthrough**.")
    await interaction.response.send_message("\n".join(lines), ephemeral=False)


@registered_group_command(body_group, name="cultivate", description="Temper your body and gather body-cultivation essence")
@serialized_user_action
async def body_cultivate(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "cultivation.body_train",
            interaction.user.id,
            {"cooldown_seconds": SETTINGS.cultivate_cooldown_minutes * 60},
            action_id=f"discord:{interaction.id}:cultivation.body_train",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    gain, total, cost = int(result.get("gain", 0)), int(result.get("total", 0)), int(result.get("cost", 0))
    extra = ""
    if int(result.get("resonance_bonus", 0)):
        extra += f"\n☯ Resonance added **+{int(result['resonance_bonus'])}** to the attempted body gain before the stage cap."
    extra += f"\n🕰️ {result.get('period','World-time')} body-tempering flow: **x{float(result.get('time_mult',1)):.2f}** efficiency."
    if float(result.get("soul_mult", 1)) > 1.0:
        extra += f"\n☸️ Soul Legacy talent echo: **x{float(result['soul_mult']):.2f}** body-cultivation efficiency."
    if float(result.get("era_mult", 1)) != 1.0:
        extra += f"\n🌌 **{result.get('era_name') or 'World Era'}** modifies cultivation to **x{float(result['era_mult']):.2f}**."
    if int(result.get("perfection_gain", 0)):
        extra += f"\n★ Body refinement deepens by **+{int(result['perfection_gain'])}%**."
    if result.get("ready"):
        extra += "\n✨ Body Stage 9 is full. Choose **/quest → Body Perfection → Start** or **/cultivation → Body Cultivation → Breakthrough**." if int(c.get("body_phase", 1)) == 9 else "\n✨ Your body is ready for **/cultivation → Body Cultivation → Breakthrough**."
    await interaction.response.send_message(f"💪 **{c['name']} tempers the body.**\nYou refine flesh, blood, bone, and meridians for **+{gain} body essence**.\nProgress: **{total}/{cost}**{extra}")

@registered_group_command(body_group, name="breakthrough", description="Attempt to advance your body-cultivation stage")
@serialized_user_action
async def body_breakthrough(interaction: discord.Interaction, confirm: bool = False) -> None:
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "cultivation.body_breakthrough",
            interaction.user.id,
            {"confirm": bool(confirm)},
            action_id=f"discord:{interaction.id}:cultivation.body_breakthrough",
        )
    except GameEngineError as exc:
        message = str(exc)
        if "perfection choice requires explicit confirmation" in message:
            message = "⚠️ **Body Stage 9 choice**\nPursue **/quest → Body Perfection → Start** for a stronger physical foundation, or explicitly confirm this breakthrough to skip it."
        await interaction.response.send_message(f"❌ {message}" if not message.startswith("⚠️") else message, ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    roll = SimpleNamespace(**dict(result.get("roll") or {}))
    success = bool(result.get("success"))
    text = f"💥 **Body Breakthrough**\n{roll_line(roll)}"
    if int(result.get("perfect_bonus", 0)):
        text += "\n★ Perfect-body legacy bonus: **+2**."
    if int(result.get("resonance_bonus", 0)):
        text += "\n☯ Dual Cultivation Resonance: **+1**."
    if int(result.get("innate_breakthrough_bonus", 0)):
        text += f"\n🌿 Innate aptitude modifier: **{int(result['innate_breakthrough_bonus']):+d}**."
    if success:
        text += f"\n✨ Advanced to **{result.get('to_realm','Unknown Realm')}, Stage {int(result.get('to_stage',1))}**."
        if int(result.get("vitality_gain", 0)):
            text += f"\n❤️ Physical advancement increases maximum Vitality by **+{int(result['vitality_gain'])}**."
        master = dict(result.get("master_reward") or {})
        if master:
            text += f"\n🎓 **{master.get('master_name','Your master')}** receives **+{int(master.get('insight_xp',0))} Insight XP** from your advancement; Master Attention rises by **+{int(master.get('attention',0))}**."
        legacy = dict(result.get("soul_legacy") or {})
        if int(legacy.get("awakened_memory", 0)):
            text += f"\n🕯️ Past-life memory awakened: **{int(legacy.get('awakened_memory',0))}% / {int(legacy.get('memory_seed',0))}%**."
    else:
        text += f"\n⚠️ The tempering fails; **{int(result.get('failure_loss',0))} body essence** is lost, with no permanent mutilation."
    await interaction.response.send_message(text)

bodyperfect_group = app_commands.Group(name="bodyperfect", description="Long-form Stage 9 Body Realm Perfection")


@registered_group_command(bodyperfect_group, name="start", description="Begin the optional Perfect Body Path at Body Stage 9")
@serialized_user_action
async def bodyperfect_start(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        await ENGINE.authoritative_action(
            "perfection.body_start", interaction.user.id,
            {},
            action_id=f"discord:{interaction.id}:perfection.body_start",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"Perfect Body Path could not begin: {exc}", ephemeral=False)
        return
    ri = int(c.get("body_realm_index", 0))
    q = WORLD.body_perfection_quest(ri, 0, c)
    await interaction.response.send_message(
        f"★ **Perfect Body Path begun: {WORLD.body_realm_name(ri, c.get('gender'))}**\n"
        f"Training can contribute **{WORLD.body_perfection_training_cap()}%**; the remaining progress comes from seven physical trials.\n\n"
        f"First quest: **{q['title']}**\n{q['description']}\nUse **/quest → Body Perfection → Quest**."
    )


@registered_group_command(bodyperfect_group, name="info", description="View your Body Realm Perfection progress")
async def bodyperfect_info(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    ri = int(c.get("body_realm_index", 0))
    p = await DB.get_body_perfection(interaction.user.id, ri)
    if not p:
        await interaction.response.send_message("No Body Perfect Path is recorded for this realm.", ephemeral=False)
        return
    status = "Completed" if p["completed"] else "Active" if p["active"] else "Inactive"
    text = (
        f"★ **{WORLD.body_realm_name(ri, c.get('gender'))} Body Perfection — {status}**\n"
        f"Progress: **{p['progress']}%**\nTraining: **{p['training_progress']}/{WORLD.body_perfection_training_cap()}**\n"
        f"Quests: **{p['completed_quests']}/{WORLD.body_perfection_quest_count()}**"
    )
    if p["active"] and p["quest_index"] < WORLD.body_perfection_quest_count():
        q = WORLD.body_perfection_quest(ri, p["quest_index"], c)
        text += f"\nCurrent quest: **{q['title']}** — preparation **{p['quest_preparation']}/{q['preparation_required']}**"
    await interaction.response.send_message(text, ephemeral=False)


BODY_PERFECT_ACTIONS = [
    app_commands.Choice(name="Info", value="info"),
    app_commands.Choice(name="Prepare", value="prepare"),
    app_commands.Choice(name="Attempt", value="attempt"),
]


@registered_group_command(bodyperfect_group, name="quest", description="Inspect, prepare for, or attempt your current Body Perfection quest")
@app_commands.choices(action=BODY_PERFECT_ACTIONS)
@serialized_user_action
async def bodyperfect_quest(interaction: discord.Interaction, action: app_commands.Choice[str]) -> None:
    c = await require_character(interaction)
    if not c:
        return
    ri = int(c.get("body_realm_index", 0))
    p = await DB.get_body_perfection(interaction.user.id, ri)
    if action.value == "info":
        if not p or not p["active"]:
            await interaction.response.send_message("No active Perfect Body Path.", ephemeral=False)
            return
        if p["quest_index"] >= WORLD.body_perfection_quest_count():
            await interaction.response.send_message("All Body Perfection quests are complete. Use **/quest → Body Perfection → Trial**.", ephemeral=False)
            return
        q = WORLD.body_perfection_quest(ri, p["quest_index"], c)
        await interaction.response.send_message(
            f"📜 **Body Perfection Quest {p['quest_index']+1}/{WORLD.body_perfection_quest_count()} — {q['title']}**\n"
            f"{q['description']}\nPreparation: **{p['quest_preparation']}/{q['preparation_required']}**\n"
            f"Trial: **{q['attribute'].title()} — TN {q['tn']}**\nClue: *{q['clue']}*\n"
            f"Reward: **+{q['progress_reward']}% Body Perfection**", ephemeral=False,
        )
        return
    _, _, wt = await current_effect_modifiers(interaction.user.id)
    try:
        envelope = await ENGINE.authoritative_action(
            "perfection.body_quest", interaction.user.id,
            {"mode": action.value, 
             "quest_cooldown_seconds": SETTINGS.perfect_quest_cooldown_minutes * 60},
            action_id=f"discord:{interaction.id}:perfection.body_quest:{action.value}",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"Body Perfection quest could not resolve: {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    if action.value == "prepare":
        prep = int(result.get("preparation", 0)); required = int(result.get("preparation_required", 0))
        await interaction.response.send_message(
            f"💪 **{result.get('title','Body Perfection')} — Preparation**\nProgress: **{min(prep, required)}/{required}**\n{result.get('clue','')}" +
            ("\n✨ The trial is available with **/quest → Body Perfection → Quest → Attempt**." if prep >= required else ""),
            ephemeral=False,
        )
        return
    roll = SimpleNamespace(**dict(result.get("roll") or {}))
    if bool(result.get("success")):
        text = f"{roll_line(roll)}\n✨ **{result.get('title','Body Perfection quest')} completed.** +{int(result.get('progress_reward',0))}% Body Perfection."
    else:
        text = f"{roll_line(roll)}\n⚠️ Your body cannot complete this tempering yet. Preparation is retained."
    await interaction.response.send_message(text, ephemeral=False)


@registered_group_command(bodyperfect_group, name="clues", description="Review Body Perfection clues you have discovered")
async def bodyperfect_clues(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    p = await DB.get_body_perfection(interaction.user.id, int(c.get("body_realm_index", 0)))
    clues = (p or {}).get("discovered", [])
    await interaction.response.send_message(
        "🔎 **Discovered Body Realm Clues**\n" + ("\n".join(f"• {x}" for x in clues) if clues else "None yet."),
        ephemeral=False,
    )


@registered_group_command(bodyperfect_group, name="trial", description="Attempt the final Perfect Body Realm trial")
@serialized_user_action
async def bodyperfect_trial(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    _, _, wt = await current_effect_modifiers(interaction.user.id)
    try:
        envelope = await ENGINE.authoritative_action(
            "perfection.body_trial", interaction.user.id,
            {"trial_cooldown_seconds": SETTINGS.perfect_trial_cooldown_minutes * 60},
            action_id=f"discord:{interaction.id}:perfection.body_trial",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"Final Body Perfection trial could not resolve: {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    lines = ["💥 **FINAL BODY REALM PERFECTION TRIAL**"]
    for row in result.get("rolls", []):
        lines.append(f"{row.get('name','Trial')}: {roll_line(SimpleNamespace(**dict(row)))}")
    if bool(result.get("success")):
        lines.append(
            f"\n★ **PERFECT {WORLD.body_realm_name(int(c.get('body_realm_index',0)), c.get('gender')).upper()} ACHIEVED**\n"
            "Your physical foundation permanently improves: Max Vitality +10%, Max Qi +5%, and future body breakthroughs gain +2."
        )
    else:
        lines.append(
            f"\n⚠️ The final body tempering fails. **{int(result.get('training_loss',0))}% recoverable Body Perfection training** is lost; "
            "your body realm remains stable. Restore it with **/cultivation → Body Cultivation → Cultivate** before trying again."
        )
    await reply_long(interaction, "\n".join(lines))


@registered_group_command(bodyperfect_group, name="abandon", description="Abandon the active Perfect Body Path")
@serialized_user_action
async def bodyperfect_abandon(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "perfection.body_abandon", interaction.user.id, {},
            action_id=f"discord:{interaction.id}:perfection.body_abandon",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"Perfect Body Path could not be abandoned: {exc}", ephemeral=False)
        return
    if not bool(dict(envelope.get("result") or {}).get("abandoned")):
        await interaction.response.send_message("No active Perfect Body Path to abandon.", ephemeral=False)
        return
    await interaction.response.send_message("The Perfect Body Path has been abandoned. You may now break through normally.", ephemeral=False)



class ExplorationEventView(discord.ui.View):
    """Personal exploration-event controls backed entirely by Go authority."""

    def __init__(self, owner_user_id: int, event: dict[str, Any]) -> None:
        self.owner_user_id = int(owner_user_id)
        self.event = dict(event or {})
        expires_at = float(self.event.get("expires_at") or time.time() + 7200)
        super().__init__(timeout=max(300, min(21600, int(expires_at - time.time()))))
        self._sync_buttons()

    def _available_keys(self) -> set[str]:
        return {
            str(row.get("key") or "")
            for row in list(self.event.get("available_actions") or [])
            if isinstance(row, dict)
        }

    def _sync_buttons(self) -> None:
        available = self._available_keys()
        active = bool(self.event.get("active"))
        for item in self.children:
            if not isinstance(item, discord.ui.Button):
                continue
            custom_id = str(item.custom_id or "")
            if custom_id == "exploration_event:refresh":
                item.disabled = False
                continue
            key = custom_id.rsplit(":", 1)[-1]
            item.disabled = (not active) or key not in available

    def embed(self) -> discord.Embed:
        active = bool(self.event.get("active"))
        title = str(self.event.get("title") or "Unexpected Event")
        description = str(self.event.get("description") or "Something unexpected interrupts your exploration.")
        location = str(self.event.get("location") or "Unknown")
        expires_at = int(float(self.event.get("expires_at") or time.time()))
        embed = discord.Embed(
            title=f"🌌 {title}",
            description=(
                f"**{self.event.get('category','Fate Encounter')}** • {'🟢 Active' if active else '⚫ Resolved'}\n"
                f"📍 **{location}** • ⏳ <t:{expires_at}:R>\n\n{description}"
            ),
            color=0x9B59B6 if active else 0x5C6370,
        )
        labels = [
            str(row.get("label") or row.get("key") or "Action")
            for row in list(self.event.get("available_actions") or [])
            if isinstance(row, dict)
        ]
        embed.add_field(name="Available actions", value=(" • ".join(labels) if labels else "This encounter has ended."), inline=False)
        embed.set_footer(text="Exploration is paused until this event is resolved or left • Go owns rolls, rewards, and state")
        return embed

    async def _owned(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.owner_user_id:
            return True
        await interaction.response.send_message("This is another cultivator's personal exploration event.", ephemeral=False)
        return False

    @staticmethod
    def _outcome_lines(outcome: dict[str, Any]) -> list[str]:
        lines: list[str] = []
        if int(outcome.get("cultivation_awarded", 0)):
            lines.append(f"Cultivation +{int(outcome['cultivation_awarded'])}")
        if int(outcome.get("spirit_stones", 0)):
            lines.append(f"Spirit Stones +{int(outcome['spirit_stones'])}")
        if int(outcome.get("insight_xp", 0)):
            lines.append(f"Insight +{int(outcome['insight_xp'])}")
        if outcome.get("items"):
            lines.append(WORLD.item_names({str(k): int(v) for k, v in dict(outcome["items"]).items()}))
        if outcome.get("effect"):
            lines.append(f"Effect: {dict(outcome['effect']).get('name','Special effect')}")
        if outcome.get("karma_delta"):
            lines.append(f"Karma {int(outcome['karma_delta']):+d} → {int(outcome.get('karma_score',0)):+d}")
        if outcome.get("fate_delta"):
            lines.append(f"Fate {int(outcome['fate_delta']):+d} → {int(outcome.get('fate',0))}/9")
        return lines

    async def run_action(self, interaction: discord.Interaction, action: str) -> None:
        if not await self._owned(interaction):
            return
        await interaction.response.defer(ephemeral=False)
        wt = await current_world_time()
        operation = "exploration.event.leave" if action == "leave" else "exploration.event.act"
        try:
            envelope = await ENGINE.authoritative_action(
                operation, interaction.user.id,
                {"event_id": str(self.event.get("event_id") or ""), "action": action},
                action_id=f"discord:{interaction.id}:{operation}:{action}",
            )
        except GameEngineError as exc:
            await interaction.followup.send(f"That event action could not proceed: {exc}", ephemeral=False)
            return
        result = dict(envelope.get("result") or {})
        self.event = dict(result.get("event") or self.event)
        self._sync_buttons()
        try:
            await interaction.edit_original_response(embed=self.embed(), view=self)
        except discord.HTTPException:
            pass
        lines = [f"**{str(result.get('label') or action.replace('_',' ').title())} — {'SUCCESS' if result.get('success') else 'FAILURE'}**"]
        roll = dict(result.get("roll") or {})
        if roll:
            lines.append(roll_line(SimpleNamespace(**roll)))
        outcome_lines = self._outcome_lines(dict(result.get("outcome") or {}))
        if outcome_lines:
            lines.append("**Outcome:** " + " • ".join(outcome_lines))
        if result.get("resolved"):
            lines.append("The encounter is resolved. Normal exploration is available again.")
        await interaction.followup.send("\n".join(lines), ephemeral=False)

    @discord.ui.button(label="Observe", emoji="👁️", style=discord.ButtonStyle.secondary, custom_id="exploration_event:observe", row=0)
    async def observe(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.run_action(interaction, "observe")

    @discord.ui.button(label="Approach", emoji="🚶", style=discord.ButtonStyle.primary, custom_id="exploration_event:approach", row=0)
    async def approach(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.run_action(interaction, "approach")

    @discord.ui.button(label="Help", emoji="🤲", style=discord.ButtonStyle.success, custom_id="exploration_event:help", row=0)
    async def help(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.run_action(interaction, "help")

    @discord.ui.button(label="Rob Them", emoji="🗡️", style=discord.ButtonStyle.danger, custom_id="exploration_event:rob", row=0)
    async def rob(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.run_action(interaction, "rob")

    @discord.ui.button(label="Leave", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="exploration_event:leave", row=1)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.run_action(interaction, "leave")

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="exploration_event:refresh", row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._owned(interaction):
            return
        try:
            status = await ENGINE.action(
                "exploration.event.status", interaction.user.id,
                {"event_id": str(self.event.get("event_id") or "")},
            )
        except GameEngineError as exc:
            await interaction.response.send_message(f"Could not refresh the event: {exc}", ephemeral=False)
            return
        self.event = dict(status or self.event)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)


@registered_root_command(name="explore", description="Explore your current location for events and discoveries", guild=GUILD)
@serialized_user_action
async def explore(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    await interaction.response.defer(ephemeral=False)
    wt_discovery = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "exploration.explore", interaction.user.id,
            {
                
                "cooldown_seconds": SETTINGS.explore_cooldown_minutes * 60,
                "unexpected_event_chance_percent": SETTINGS.unexpected_event_chance_percent,
                "event_key": f"discord:{interaction.id}:exploration:event",
            },
            action_id=f"discord:{interaction.id}:exploration.explore",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"Exploration could not proceed: {exc}", ephemeral=False)
        return
    outcome = dict(envelope.get("result") or {})
    if str(outcome.get("kind") or "") == "event_active":
        event = dict(outcome.get("event") or {})
        expedition_thread = await ensure_expedition_thread(interaction, c)
        view = ExplorationEventView(interaction.user.id, event)
        if expedition_thread is not None:
            await EXPLORATION.enter_expedition(
                interaction.user.id,
                physical_location=str(c.get("location") or "Unknown"),
                channel_id=expedition_thread.id,
                guild_id=interaction.guild_id,
            )
            try:
                await expedition_thread.send(
                    content=f"{interaction.user.mention}, your previous exploration is still interrupted by this encounter.",
                    embed=view.embed(),
                    view=view,
                )
                await interaction.followup.send(
                    f"🌌 You are still dealing with **{event.get('title','an unexpected event')}**. Controls reopened in {expedition_thread.mention}.",
                    ephemeral=False,
                )
            except discord.HTTPException:
                await interaction.followup.send(embed=view.embed(), view=view, ephemeral=False)
        else:
            await interaction.followup.send(embed=view.embed(), view=view, ephemeral=False)
        return
    encounter = str(outcome.get("encounter") or "The region is strangely quiet.")
    cultivation = int(outcome.get("cultivation_awarded", 0))
    stones = int(outcome.get("spirit_stones", 0))
    items = {str(k): int(v) for k, v in dict(outcome.get("items") or {}).items()}

    expedition_thread = await ensure_expedition_thread(interaction, c)
    if expedition_thread is not None:
        await EXPLORATION.enter_expedition(
            interaction.user.id,
            physical_location=str(c.get("location") or "Unknown"),
            channel_id=expedition_thread.id,
            guild_id=interaction.guild_id,
        )
    else:
        await EXPLORATION.reset_to_world(interaction.user.id, guild_id=interaction.guild_id)
    history_channel_id = expedition_thread.id if expedition_thread is not None else (interaction.channel_id or 0)
    if expedition_thread is not None:
        try:
            await expedition_thread.send(f"\n📍 **Exploration — {c['location']}**")
        except discord.HTTPException:
            pass

    shared_claims = [str(row.get("title") or "world event") for row in list(outcome.get("shared_claims") or [])]
    surprise = dict(outcome.get("surprise") or {})
    surprise_text = ""
    event_thread: discord.Thread | None = None
    personal_event_view: ExplorationEventView | None = None
    if surprise:
        kind = str(surprise.get("kind") or "")
        surprise_text = f"\n\n🌌 **UNEXPECTED EVENT — {surprise.get('title','Unknown Event')}**\n{surprise.get('description','')}"
        if kind == "personal":
            personal_event_view = ExplorationEventView(interaction.user.id, surprise)
            surprise_text += "\n**Your exploration is paused here.** Resolve the encounter or choose **Leave** before exploring, hunting, or travelling again."
        elif kind == "world_event":
            if not bool(surprise.get("activated")):
                surprise_text = (
                    f"\n\n🌌 **EVENT ECHO — {surprise.get('title','World Event')}**\n"
                    f"This event is already active at **{c['location']}**; its consequences do not stack."
                )
            else:
                impacts=[str(x) for x in list(surprise.get("impacts") or [])]
                consequence=str(surprise.get("consequence_text") or "").strip()
                if consequence:
                    surprise_text += f"\n**Persistent consequence:** {consequence}"
                if impacts:
                    surprise_text += f"\n**Systems changed:** {'; '.join(impacts)}."
                surprise_text += f"\nThis is now a **server-wide event** for about {int(surprise.get('duration_hours') or 2)}h. Use **/world → Events**."
                event_thread = await spawn_event_thread(
                    interaction,title=str(surprise.get("title") or "World Event"),event_type="random_event",
                    event_key=str(surprise.get("event_key") or ""),expires_at=float(surprise.get("expires_at") or time.time()+7200),
                    announcement=(
                        f"⚡ **SERVER-WIDE EVENT — {surprise.get('title','World Event')}**\n"
                        f"Category: **{surprise.get('category','Phenomenon')}** • Severity **{int(surprise.get('severity',1))}/10**\n"
                        f"📍 **{c['location']}**\n{surprise.get('description','')}"
                        + (f"\n\n**Persistent consequence:** {consequence}" if consequence else "")
                        + f"\n\nActive for about **{int(surprise.get('duration_hours') or 2)}h**. Use the thread below for the live scene."
                    ),
                )
        elif kind == "secret_realm":
            if not bool(surprise.get("activated")):
                surprise_text = (
                    f"\n\n🌀 **SPATIAL ECHO — {surprise.get('realm_name') or surprise.get('title','Secret Realm')}**\n"
                    f"That entrance is already open at **{surprise.get('location') or c['location']}**. Use **/world → Events**."
                )
            else:
                await DB.record_world_history_event(
                    event_type="discovery", title=f"Secret realm opened: {surprise.get('realm_name') or surprise.get('title','Secret Realm')}",
                    summary=f"The entrance to {surprise.get('realm_name') or surprise.get('title','Secret Realm')} opened at {surprise.get('location')}. {surprise.get('realm_description','')}",
                    significance=76, visibility="public", location=str(surprise.get("location") or c["location"]),
                    actor_type="world", actor_key=str(surprise.get("secret_realm_id") or ""), actor_name=str(surprise.get("realm_name") or "Secret Realm"),
                    target_type="secret_realm", target_key=str(surprise.get("secret_realm_id") or ""), target_name=str(surprise.get("realm_name") or "Secret Realm"),
                    tags=("discovery","secret realm","opening"), game_minute=wt_discovery.total_minutes,
                    metadata={"event_key": surprise.get("event_key"), "open_hours": int(surprise.get("open_hours") or 8)},
                    source_key=f"secret_realm_open:{surprise.get('event_key')}",
                )
                surprise_text += (
                    f"\n🌀 **Secret Realm Opened: {surprise.get('realm_name') or surprise.get('title','Secret Realm')}**\n"
                    f"{surprise.get('realm_description','')}\nThe entrance remains unstable for about **{int(surprise.get('open_hours') or 8)}h**. "
                    "Use **/realm → Secret Realms → Enter**."
                )
                event_thread = await spawn_event_thread(
                    interaction,title=str(surprise.get("realm_name") or surprise.get("title") or "Secret Realm"),event_type="secret_realm",
                    event_key=str(surprise.get("event_key") or ""),expires_at=float(surprise.get("expires_at") or time.time()+28800),
                    announcement=(
                        f"🌀 **{str(surprise.get('category') or 'SECRET REALM').upper()} — {surprise.get('realm_name') or surprise.get('title','Secret Realm')}**\n"
                        f"📍 Entrance: **{surprise.get('location') or c['location']}**\n{surprise.get('description','')}\n{surprise.get('realm_description','')}\n\n"
                        f"The entrance remains open for about **{int(surprise.get('open_hours') or 8)}h**. "
                        "Travel there, then use **/realm → Secret Realms → Enter**. The thread below is the shared expedition scene."
                    ),
                )
        if event_thread is not None:
            surprise_text += f"\n💬 **Live event thread:** {event_thread.mention}"

    discovery_text = ""
    discovered_location = str(outcome.get("discovered_location") or "")
    if discovered_location:
        discovery_text = (
            f"\n\n🧭 **New route discovered — {discovered_location}**\n"
            f"{WORLD.locations.get(discovered_location, {}).get('description', 'A newly charted route opens before you.')}"
        )
        discovered_sects = _sect_recruitment_at_location(discovered_location)
        if discovered_sects:
            for sect_name in discovered_sects:
                await DB.discover_sect(
                    interaction.user.id, sect_name, game_minute=wt_discovery.total_minutes,
                    discovery_kind="exploration", source_key=discovered_location,
                )
            discovery_text += f"\n🏯 **Sect route discovered:** {', '.join(discovered_sects)}. Open **Sect → Recruitment** to learn about the gate."
            try:
                await QUESTS.progress(interaction.user.id, "sect_discovery", amount=1, game_minute=wt_discovery.total_minutes)
            except Exception:
                log.exception("Quest progress update failed after sect discovery")
        try:
            await DB.record_world_history_event(
                event_type="discovery", title=f"{c.get('name','A cultivator')} discovered {discovered_location}",
                summary=(
                    f"While exploring from {c.get('location','Unknown')}, {c.get('name','the cultivator')} charted a route to "
                    f"{discovered_location}." + (f" Sect access revealed: {', '.join(discovered_sects)}." if discovered_sects else "")
                ),
                significance=58 if discovered_sects else 48, visibility="participant", location=str(discovered_location),
                actor_type="player", actor_key=str(interaction.user.id), actor_name=str(c.get('name') or ''),
                target_type="location", target_key=str(discovered_location), target_name=str(discovered_location), related_user_id=interaction.user.id,
                tags=("discovery","route",*tuple(discovered_sects or [])), game_minute=wt_discovery.total_minutes,
                metadata={"origin": str(c.get('location') or ''), "sects": list(discovered_sects or [])},
                source_key=f"location_discovery:{interaction.user.id}:{discovered_location}",
            )
        except Exception:
            log.exception("Could not persist structured world-history discovery")

    try:
        await QUESTS.progress(interaction.user.id, "explore", amount=1, target=str(c.get("location") or ""), game_minute=wt_discovery.total_minutes)
    except Exception:
        log.exception("Quest progress update failed after exploration")
    await DB.add_history(history_channel_id, user_id=interaction.user.id, speaker=c["name"], content=f"Explores {c['location']}")
    history = await DB.get_history(history_channel_id, 20)
    exploration_context = await NARRATOR_CONTEXT.build(c, scene_type="exploration", query_text=encounter)
    try:
        narration = await NARRATOR_QUEUE.run(
            "exploration", NARRATOR.narrate_exploration(c, encounter, history, scene_context=exploration_context.text),
        )
    except Exception:
        log.exception("Exploration narration failed")
        narration = encounter
    try:
        await DB.add_rag_memory(
            interaction.user.id, memory_kind="exploration", salience=38, location=str(c.get("location") or ""),
            source="exploration", game_minute=exploration_context.game_minute,
            summary=(
                f"While exploring {c.get('location','Unknown')}, {c.get('name','the player')} encountered: "
                f"{encounter[:360]} | Observed scene: {narration[:500]}"
            ),
        )
    except Exception:
        log.exception("Could not persist exploration RAG memory")
    reward_text = f"\n\n**Exploration gains:** +{cultivation} cultivation, +{stones} spirit stones" + (f", {WORLD.item_names(items)}" if items else "")
    if shared_claims:
        reward_text += "\n**Active event participation:** " + ", ".join(shared_claims)
    await DB.add_history(history_channel_id, user_id=None, speaker="World", content=narration + surprise_text + discovery_text)
    full_exploration = narration + reward_text + surprise_text + discovery_text
    if expedition_thread is not None:
        try:
            await send_long_to_thread(expedition_thread, full_exploration)
            if personal_event_view is not None:
                await expedition_thread.send(embed=personal_event_view.embed(), view=personal_event_view)
            await interaction.followup.send(f"🧭 Exploration recorded in your private expedition journal: {expedition_thread.mention}", ephemeral=False)
        except discord.HTTPException:
            log.exception("Could not write exploration result to private expedition thread")
            await reply_long(interaction, full_exploration, ephemeral=False)
            if personal_event_view is not None:
                await interaction.followup.send(embed=personal_event_view.embed(), view=personal_event_view, ephemeral=False)
    else:
        await reply_long(
            interaction, full_exploration + "\n\n⚠️ No private expedition thread is configured. Ask an admin to bind the existing channel in the admin dashboard, then run **/admin → Server → Base Channels → Validate / bind**.",
            ephemeral=False,
        )
        if personal_event_view is not None:
            await interaction.followup.send(embed=personal_event_view.embed(), view=personal_event_view, ephemeral=False)


@registered_root_command(name="hunt", description="Hunt a spirit beast for materials", guild=GUILD)
@serialized_user_action
async def hunt(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    await interaction.response.defer(ephemeral=False)
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "exploration.hunt", interaction.user.id,
            {"cooldown_seconds": SETTINGS.hunt_cooldown_minutes * 60},
            action_id=f"discord:{interaction.id}:exploration.hunt",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"The hunt could not proceed: {exc}", ephemeral=False)
        return
    outcome = dict(envelope.get("result") or {})
    beast = dict(outcome.get("beast") or {})
    roll = SimpleNamespace(**dict(outcome.get("roll") or {}))
    success = bool(outcome.get("success"))
    cultivation_awarded = int(outcome.get("cultivation_awarded", 0))

    hunt_context = await NARRATOR_CONTEXT.build(
        c, scene_type="spirit beast hunt", query_text=f"hunt {beast.get('name','spirit beast')} {beast.get('element','')}"
    )
    try:
        narration = await NARRATOR.narrate_hunt_result(c, beast, roll_line(roll), success, scene_context=hunt_context.text)
    except Exception:
        log.exception("Hunt narration failed")
        narration = f"You encounter a {beast.get('name','spirit beast')}."
    try:
        await DB.add_rag_memory(
            interaction.user.id, memory_kind="hunt", salience=48 if success else 32,
            location=str(c.get("location") or ""), source="hunt", game_minute=hunt_context.game_minute,
            summary=(
                f"{c.get('name','The player')} hunted {beast.get('name','a spirit beast')} at {c.get('location','Unknown')}. "
                f"Outcome: {'success' if success else 'the beast escaped'}. Observed: {narration[:520]}"
            ),
        )
    except Exception:
        log.exception("Could not persist hunt RAG memory")

    text = f"**Hunt: {beast.get('name','Spirit Beast')}**\n{roll_line(roll)}\n\n{narration}"
    if success:
        loot = {str(k): int(v) for k, v in dict(beast.get("loot") or {}).items()}
        text += (
            f"\n\n**Loot:** +{cultivation_awarded} cultivation, +{int(beast.get('stones',0))} spirit stones, "
            f"{WORLD.item_names(loot)}"
        )
        bonded = dict(outcome.get("bonded_beast") or {})
        wild = dict(outcome.get("wild_encounter") or {})
        if bonded:
            text += (
                f"\n\n🐉 **Beast bond:** your overwhelming Beast-Binder success earns the respect of **{bonded.get('name', beast.get('name','the beast'))}**. "
                f"An **equality contract** is formed at loyalty **{int(bonded.get('loyalty',25))}**"
                + (" and it becomes your active companion." if bonded.get("active") else ".")
            )
        elif wild:
            text += (
                f"\n\n🪢 **Subdued beast opportunity:** **{beast.get('name','The beast')}** survives the clash and watches you warily. "
                f"Encounter `#{int(wild.get('encounter_id',0))}` can be approached through **/beast → Tame** before it leaves."
            )
    else:
        text += "\n\nThe beast escapes. No permanent injury or item loss is applied."
    await reply_long(interaction, text)


async def recipe_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    names = await DB.search_catalog("recipe", current, 25)
    return [app_commands.Choice(name=name[:100], value=name[:100]) for name in names]


async def alchemy_recipe_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    needle = current.casefold().strip()
    names = [
        name for name, recipe in WORLD.recipes.items()
        if str(recipe.get("profession", "")).casefold() == "alchemy"
        and (not needle or needle in name.casefold())
    ]
    return [app_commands.Choice(name=name[:100], value=name[:100]) for name in sorted(names)[:25]]


async def _run_crafting(
    interaction: discord.Interaction, recipe: str, *, required_profession: str | None = None,
) -> None:
    c = await require_character(interaction)
    if not c:
        return
    r = await DB.get_recipe_definition(recipe)
    if not r:
        await interaction.response.send_message(
            "Unknown recipe. Start typing a recipe name and choose it from autocomplete.",
            ephemeral=False,
        )
        return
    profession = str(r["profession"])
    if required_profession and profession.casefold() != str(required_profession).casefold():
        await interaction.response.send_message(
            f"**{recipe}** is a **{profession}** recipe, not {required_profession}.", ephemeral=False,
        )
        return

    try:
        envelope = await ENGINE.authoritative_action(
            "craft.resolve",
            interaction.user.id,
            {"recipe": recipe},
            action_id=f"discord:{interaction.id}:craft.resolve",
        )
    except GameEngineError as exc:
        message = str(exc)
        if "missing materials" in message.casefold():
            message = "Missing materials for that recipe."
        await interaction.response.send_message(message, ephemeral=False)
        return

    resolved = dict(envelope.get("result") or {})
    result = SimpleNamespace(**resolved)
    facility_bonus = int(resolved.get("facility_bonus", 0))
    manor_facility_bonus = int(resolved.get("manor_facility_bonus", 0))
    inherited_family_bonus = int(resolved.get("family_bonus", 0))
    profession_bonus = int(resolved.get("profession_bonus", 0))
    output = {str(k): int(v) for k, v in dict(resolved.get("output") or {}).items()}
    success = bool(resolved.get("success"))
    outcome = f"Created **{WORLD.item_names(output)}**." if success else "The refinement fails and the ingredients are consumed."
    profession_row = dict(resolved.get("profession_progress") or {})
    level = int(profession_row.get("level", 0))
    mastery_line = (
        f"\n🛠️ Profession: **{profession_rank(level)}** (Level {level}) • "
        f"XP {profession_row.get('xp',0)}/{profession_xp_needed(level)}"
    )
    quality_label = str(resolved.get("quality_label") or "")
    quality_line = ""
    if quality_label:
        prefix = "⚗️ Batch quality" if profession == "Alchemy" else "✨ Craft quality"
        quality_line = f"\n{prefix}: **{quality_label}**"
        mult = int(resolved.get("output_multiplier", 1))
        if profession == "Alchemy" and success and mult > 1:
            quality_line += f" • output ×{mult}"

    await interaction.response.send_message(
        f"**{profession}: {recipe}**\n{roll_line(result)}\n"
        f"{('Player-property facility bonus: **+'+str(facility_bonus)+'**\n') if facility_bonus else ''}"
        f"{('Sect-manor facility bonus: **+'+str(manor_facility_bonus)+'**\n') if manor_facility_bonus else ''}"
        f"{('Birth-family Alchemy tradition: **+'+str(inherited_family_bonus)+'**\n') if inherited_family_bonus else ''}"
        f"{('Profession mastery bonus: **+'+str(profession_bonus)+'**\n') if profession_bonus else ''}"
        f"{outcome}{quality_line}{mastery_line}"
    )


@registered_root_command(name="craft", description="Practice alchemy, forging, or formation inscription from a known recipe", guild=GUILD)
@app_commands.autocomplete(recipe=recipe_autocomplete)
@serialized_user_action
async def craft(interaction: discord.Interaction, recipe: str) -> None:
    await _run_crafting(interaction, recipe)


alchemy_group = app_commands.Group(name="alchemy", description="Refine pills, gather medicinal herbs and manage pill toxicity")


@registered_group_command(alchemy_group, name="status", description="Inspect Alchemy mastery, recent refinements and medicinal toxicity")
async def alchemy_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    state = await sync_pill_toxicity_effect(interaction.user.id, game_minute=wt.total_minutes)
    profession = await DB.get_profession_progress(interaction.user.id, "Alchemy") or {}
    batches = await DB.get_alchemy_batches(interaction.user.id, limit=5)
    band, band_text = toxicity_band(int(state.get("pill_toxicity", 0)))
    member_manor = await DB.get_member_sect_manor(interaction.user.id)
    birth_family = await DB.get_birth_family(interaction.user.id)
    inherited_family_bonus = family_profession_bonus(birth_family, "Alchemy")
    manor_bonus = 0
    if member_manor and str(member_manor.get("base_location", "")) == str(c.get("location", "")):
        manor_bonus = manor_craft_bonus(member_manor, "Alchemy")
    abode = await DB.get_abode_by_location(str(c.get("location", "")))
    abode_bonus = 0
    if abode and await DB.can_access_abode(int(abode['user_id']), interaction.user.id):
        abode_bonus = int(abode.get("alchemy_level", 0)) * 2
    lines = [
        f"⚗️ **Alchemy — {c['name']}**",
        f"Mastery: **{profession_rank(int(profession.get('level', 0)))}** • Level **{int(profession.get('level', 0))}** • XP **{int(profession.get('xp', 0))}**",
        f"Refinements: **{int(state.get('successful_refinements',0))}/{int(state.get('total_refinements',0))}** successful • Flawless **{int(state.get('flawless_refinements',0))}**",
        f"Pill toxicity: **{int(state.get('pill_toxicity',0))}/100 — {band}**\n{band_text}",
        f"Local facilities: player property **+{abode_bonus}** • sect manor **+{manor_bonus}**",
        f"Birth-family tradition: **+{inherited_family_bonus} Alchemy**" if inherited_family_bonus else "Birth-family tradition: **none**",
    ]
    if batches:
        lines.append("\n**Recent batches**")
        for batch in batches:
            result_text = "success" if int(batch.get("success", 0)) else "failure"
            lines.append(
                f"`#{batch['batch_id']}` **{batch['recipe_name']}** • {str(batch['quality']).title()} • margin {int(batch['margin']):+d} • {result_text}"
            )
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(alchemy_group, name="refine", description="Refine a pill recipe using the connected Alchemy crafting system")
@app_commands.autocomplete(recipe=alchemy_recipe_autocomplete)
@serialized_user_action
async def alchemy_refine(interaction: discord.Interaction, recipe: str) -> None:
    await _run_crafting(interaction, recipe, required_profession="Alchemy")


@registered_group_command(alchemy_group, name="forage", description="Gather medicinal herbs using the current region's simulated spirit resources")
@serialized_user_action
async def alchemy_forage(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    remaining = await DB.cooldown_remaining(interaction.user.id, "alchemy_forage")
    if remaining:
        await interaction.response.send_message(
            f"The nearby herb beds need time to recover. Forage again in **{human_duration(remaining)}**.", ephemeral=False,
        )
        return
    try:
        envelope = await ENGINE.authoritative_action(
            "forage.resolve",
            interaction.user.id,
            {},
            action_id=f"discord:{interaction.id}:forage.resolve",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"Foraging could not resolve: {exc}", ephemeral=False)
        return
    resolved = dict(envelope.get("result") or {})
    result = SimpleNamespace(**resolved)
    success = bool(resolved.get("success"))
    forage_progress = dict(resolved.get("profession_progress") or {})
    level = int(forage_progress.get("level", 0))
    forage_location = str(resolved.get("location") or c.get("location", ""))
    inherited_forage_bonus = int(resolved.get("family_bonus", 0))
    garden_bonus = int(resolved.get("garden_bonus", 0))
    bonus_bits = (
        f"{(' • Alchemy-family herb lore **+'+str(inherited_forage_bonus)+'**') if inherited_forage_bonus else ''}"
        f"{(' • Property herb garden **+'+str(garden_bonus)+'**') if garden_bonus else ''}"
    )
    if not success:
        await interaction.response.send_message(
            f"🌿 **Medicinal Forage — {forage_location}**\n{roll_line(result)}\n"
            f"Regional spirit resources: **{int(resolved.get('spirit_resources',0))}/100**.{bonus_bits} "
            "You find no usable harvest this time."
            f"\n🧺 Foraging: **{profession_rank(level)}** Lv.{level} "
            f"• XP {int(forage_progress.get('xp',0))}/{profession_xp_needed(level)}"
        )
        return
    awarded = {str(k): int(v) for k, v in dict(resolved.get("loot") or {}).items()}
    rare = str(resolved.get("rare_found") or "")
    rare_line = f"\n✨ Rare find: **{WORLD.item_name(rare)}**." if rare else ""
    await interaction.response.send_message(
        f"🌿 **Medicinal Forage — {forage_location}**\n{roll_line(result)}\n"
        f"Regional spirit resources: **{int(resolved.get('spirit_resources',0))}/100**.{bonus_bits}\n"
        f"Harvested: **{WORLD.item_names(awarded)}**.{rare_line}\n"
        f"🧺 Foraging: **{profession_rank(level)}** Lv.{level} "
        f"• XP {int(forage_progress.get('xp',0))}/{profession_xp_needed(level)}"
    )


@registered_group_command(alchemy_group, name="purge", description="Slowly purge medicinal residue by spending Qi in controlled circulation")
@serialized_user_action
async def alchemy_purge(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    remaining = await DB.cooldown_remaining(interaction.user.id, "alchemy_purge")
    if remaining:
        await interaction.response.send_message(
            f"Your meridians need time before another purge cycle: **{human_duration(remaining)}**.", ephemeral=False,
        )
        return
    wt = await current_world_time()
    state = await DB.get_alchemy_state(interaction.user.id, game_minute=wt.total_minutes)
    current = int(state.get("pill_toxicity", 0))
    if current <= 0:
        await interaction.response.send_message("Your meridians contain no pill toxicity to purge.", ephemeral=False)
        return
    qi_cost = min(12, max(4, current // 8))
    if not await DB.spend_resources(interaction.user.id, qi=qi_cost):
        await interaction.response.send_message(f"You need **{qi_cost} Qi** for a controlled medicinal purge.", ephemeral=False)
        return
    purge = min(current, 8 + int(c['attributes'].get('will', 0)) // 2 + int(c['attributes'].get('spirit', 0)) // 3)
    state = await DB.reduce_pill_toxicity(interaction.user.id, purge, game_minute=wt.total_minutes)
    await sync_pill_toxicity_effect(interaction.user.id, game_minute=wt.total_minutes, state=state)
    await DB.set_cooldown(interaction.user.id, "alchemy_purge", 60 * 60)
    await interaction.response.send_message(
        f"🫧 You circulate **{qi_cost} Qi** through the meridians and purge **{purge}** toxicity. "
        f"Pill toxicity is now **{int(state.get('pill_toxicity',0))}/100**.", ephemeral=False,
    )


realmhub_group = app_commands.Group(name="realmhub", description="Meet other cultivators in the central city of each realm world")
async def realmhub_world_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    c = await DB.get_character(interaction.user.id)
    if not c:
        return []
    needle = current.casefold().strip()
    worlds = [
        world for world, hub in REALM_HUBS.items()
        if int(c.get("realm_index", 0)) >= int(hub.get("min_realm_index", 0))
        and (not needle or needle in world.casefold() or needle in str(hub.get("display_name", "")).casefold())
    ]
    return [app_commands.Choice(name=f"{world} — {REALM_HUBS[world]['display_name']}"[:100], value=world) for world in worlds[:25]]


@registered_group_command(realmhub_group, name="status", description="Show realm capitals your cultivation can currently perceive")
async def realmhub_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction, allow_deceased=True)
    if not c:
        return
    mappings = {str(r['world_name']): r for r in (await DB.get_realm_hub_channels(interaction.guild.id) if interaction.guild else [])}
    lines=["🏙️ **Known Realm Capitals**"]
    for world,hub in REALM_HUBS.items():
        if int(c.get('realm_index',0)) < int(hub['min_realm_index']):
            continue
        row=mappings.get(world); channel=f"<#{int(row['channel_id'])}>" if row else "*Discord channel not provisioned yet*"
        lines.append(f"• **{world} — {hub['display_name']}** • {channel}")
    lines.append("\nHigher worlds remain beyond perception until your cultivation can enter them.")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(realmhub_group, name="go", description="Travel directly to the central meeting city of a known realm world")
@app_commands.autocomplete(world=realmhub_world_autocomplete)
@serialized_user_action
async def realmhub_go(interaction:discord.Interaction,world:str)->None:
    c=await require_character(interaction)
    if not c:return
    hub=realm_hub(world)
    if not hub:
        await interaction.response.send_message("Unknown realm capital.",ephemeral=False);return
    try:
        envelope=await ENGINE.authoritative_action(
            "exploration.travel",interaction.user.id,
            {"destination":str(hub["location"]),"mode":"hub"},
            action_id=f"discord:{interaction.id}:exploration.travel.hub",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"Realm-capital travel failed: {exc}",ephemeral=False);return
    result=dict(envelope.get("result") or {})
    channel_line=""
    if interaction.guild:
        rows=await DB.get_realm_hub_channels(interaction.guild.id); row=next((r for r in rows if str(r['world_name'])==world),None)
        if row: channel_line=f"\n💬 Meet other cultivators in <#{int(row['channel_id'])}>."
    await interaction.response.send_message(f"🏙️ **{c['name']} arrives at {hub['display_name']} — {result.get('world') or world}.**{channel_line}")


def _world_min_realm_index(world_name: str) -> int:
    hub = REALM_HUBS.get(str(world_name))
    if hub is not None:
        return int(hub.get("min_realm_index", 0))
    candidates = [
        int(data.get("min_realm_index", 0))
        for data in WORLD.locations.values()
        if str(data.get("world")) == str(world_name)
    ]
    return min(candidates) if candidates else 0


def _world_is_unlocked(character: dict[str, Any], world_name: str) -> bool:
    return int(character.get("realm_index", 0)) >= _world_min_realm_index(str(world_name))


async def _known_locations(user_id: int, character: dict[str, Any]) -> set[str]:
    rows = await DB.get_discovered_locations(int(user_id))
    known = {str(row.get("location")) for row in rows if row.get("location")}
    current = str(character.get("location") or "")
    if current and not current.startswith(("abode:", "personal_world:")):
        known.add(current)
        current_data = WORLD.locations.get(current) or {}
        for neighbor in current_data.get("roads", []):
            neighbor_name = str(neighbor)
            neighbor_data = WORLD.locations.get(neighbor_name) or {}
            if not neighbor_data or bool(neighbor_data.get("private", False)):
                continue
            if int(character.get("realm_index", 0)) < int(neighbor_data.get("min_realm_index", 0)):
                continue
            if str(neighbor_data.get("world") or "") != str(current_data.get("world") or ""):
                continue
            known.add(neighbor_name)
    # Realm capitals become public knowledge only when the character can actually
    # survive in that world. Future worlds remain completely hidden.
    for world_name, hub in REALM_HUBS.items():
        if _world_is_unlocked(character, world_name):
            known.add(str(hub["location"]))
    return known


async def _location_is_visible(user_id: int, character: dict[str, Any], location: str) -> bool:
    data = WORLD.locations.get(str(location)) or await DB.get_location_definition(str(location))
    if not data:
        return False
    if not _world_is_unlocked(character, str(data.get("world") or WORLD.realm_world(int(character.get("realm_index", 0))))):
        return False
    return str(location) in await _known_locations(user_id, character)


async def location_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    c = await DB.get_character(interaction.user.id)
    if not c:
        return []
    known = await _known_locations(interaction.user.id, c)
    needle = current.casefold().strip()
    names = [
        name for name in known
        if name in WORLD.locations
        and (not needle or needle in name.casefold())
        and _world_is_unlocked(c, str(WORLD.locations[name].get("world") or "Mortal World"))
    ]
    return [app_commands.Choice(name=name[:100], value=name[:100]) for name in sorted(names)[:25]]


@registered_root_command(name="travel", description="Travel to another known location", guild=GUILD)
@app_commands.autocomplete(destination=location_autocomplete)
@serialized_user_action
async def travel(interaction: discord.Interaction, destination: str) -> None:
    c = await require_character(interaction)
    if not c:
        return
    try:
        envelope=await ENGINE.authoritative_action(
            "exploration.travel",interaction.user.id,
            {"destination":destination,"mode":"known"},
            action_id=f"discord:{interaction.id}:exploration.travel",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"Travel failed: {exc}",ephemeral=False)
        return
    result=dict(envelope.get("result") or {})
    desc=str(result.get("description") or "")
    safe="\n🛡️ This location is protected by laws or formations." if bool(result.get("safe_zone")) else ""
    road=""
    if bool(result.get("road_connection")):
        minutes=int(result.get("travel_minutes") or 0)
        danger=int(result.get("road_danger") or 0)
        chance=int(result.get("road_encounter_chance_percent") or 0)
        arrival=int(result.get("arrival_game_minute") or 0)
        road=(
            f"\n⏱️ Road time **{minutes} game-minutes** • Danger **{danger}/45** • "
            f"Encounter risk **{chance}%** • Arrival **game minute {arrival}**."
            "\n🚶 You remain in transit and cannot take authoritative actions until arrival."
        )
        encounter=result.get("road_encounter")
        if isinstance(encounter,dict):
            road+=f"\n⚠️ {str(encounter.get('detail') or 'A road encounter delays the journey')}"
            delay=int(encounter.get("delay_minutes") or 0)
            damage=int(encounter.get("vitality_damage") or 0)
            if delay or damage:
                road+=f" (**+{delay} min**, **-{damage} Vitality**)"
    meeting=""
    hub_match=realm_hub_by_location(str(result.get("destination") or destination))
    if hub_match and interaction.guild:
        world_name,_hub=hub_match
        rows=await DB.get_realm_hub_channels(interaction.guild.id)
        row=next((r for r in rows if str(r.get("world_name"))==world_name),None)
        if row: meeting=f"\n💬 Public meeting channel: <#{int(row['channel_id'])}>."
    await interaction.response.send_message(
        f"🗺️ **{c['name']} travels to {result.get('destination') or destination}.**\n{desc}{road}{safe}{meeting}"
    )


async def local_npc_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    c = await DB.get_character(interaction.user.id)
    location = c.get("location") if c else None
    wt = await current_world_time()
    needle = current.casefold().strip()
    names: list[str] = []
    for name in await DB.search_catalog("npc", current, 25):
        npc_location = await current_npc_location(name, wt.period)
        if location and npc_location and npc_location != location:
            continue
        if not needle or needle in name.casefold():
            names.append(name)
    return [app_commands.Choice(name=name[:100], value=name[:100]) for name in names[:25]]


@registered_root_command(name="talk", description="Speak with a persistent NPC", guild=GUILD)
@app_commands.autocomplete(npc=local_npc_autocomplete)
@serialized_user_action
async def talk(
    interaction: discord.Interaction,
    npc: str,
    message: str,
) -> None:
    c = await require_character(interaction)
    if not c:
        return
    npc_data = await DB.get_npc_definition(npc)
    if not npc_data:
        await interaction.response.send_message("Unknown NPC.", ephemeral=False)
        return
    wt = await current_world_time()
    npc_location = await current_npc_location(npc, wt.period)
    if npc_location and npc_location != c.get("location"):
        await interaction.response.send_message(
            f"**{npc}** is currently at **{npc_location}** during the **{wt.period}**, not **{c['location']}**.",
            ephemeral=False,
        )
        return
    channel_id = interaction.channel_id or 0
    await DB.add_history(
        channel_id,
        user_id=interaction.user.id,
        speaker=c["name"],
        content=f"To {npc}: {message}",
    )
    memory = await DB.get_npc_memory(interaction.user.id, npc)
    history = await DB.get_history(channel_id, 24)
    lineage_context = await DB.describe_lineage_context(interaction.user.id)
    social_context = (await NARRATOR_CONTEXT.build(
        c, scene_type="freeform roleplay", lineage_context=lineage_context,
        query_text=message, focus_npc=npc,
    )).text
    relationship = await NPC_RELATIONSHIPS.get(interaction.user.id, npc)
    relationship_context = (
        f"\nPersistent relationship: {NPC_RELATIONSHIPS.public_label(relationship)}; "
        f"trust {relationship.get('trust',0):+d}, respect {relationship.get('respect',0):+d}, "
        f"fear {relationship.get('fear',0):+d}, debt {relationship.get('debt',0):+d}, "
        f"grudge {relationship.get('grudge',0):+d}. "
        "These values are canonical; narrate them but do not change them."
    )
    social_context += relationship_context
    npc_state = await SIM.npc_status(npc) or {}
    salient_memories = await DB.list_npc_player_memories(
        interaction.user.id, npc, 6, mark_recalled=True
    )
    if npc_state:
        visible_mood = public_mood_hint(str(npc_state.get("mood") or ""))
        if visible_mood:
            social_context += f"\nCurrent outward demeanor: {visible_mood}."
        social_context += f"\nCurrent observable activity: {npc_state.get('activity','Following established routine')}."
    await interaction.response.defer()
    try:
        answer = await NARRATOR_QUEUE.run(
            "talk_to_npc",
            NARRATOR.talk_to_npc(
                character=c,
                npc_name=npc,
                player_dialogue=message,
                memory=memory,
                history=history,
                social_context=social_context,
                scene_context=social_context,
                npc_state=npc_state,
                salient_memories=salient_memories,
            ),
        )
    except Exception:
        log.exception("NPC narration failed")
        answer = f"{npc} studies you in silence; the narrator service failed to answer."

    await DB.add_history(
        channel_id,
        user_id=None,
        speaker=npc,
        content=answer,
    )
    new_memory = roll_npc_memory(memory, message, npc, answer)
    await DB.set_npc_memory(interaction.user.id, npc, new_memory)
    wt_now = await current_world_time()
    memory_kind, salience = classify_memory(message, answer)
    npc_memory_id = await DB.add_npc_player_memory(
        interaction.user.id, npc,
        memory_kind=memory_kind,
        summary=exchange_memory_summary(message, npc, answer),
        salience=salience,
        source="talk",
        game_minute=wt_now.total_minutes,
    )
    await DB.add_rag_memory(
        interaction.user.id, source_key=f"npc:{npc_memory_id}", memory_kind=memory_kind,
        summary=exchange_memory_summary(message, npc, answer), salience=salience,
        location=str(c.get("location") or ""), npc_name=npc,
        faction=str(npc_data.get("sect_affiliation") or ""), source="talk",
        game_minute=wt_now.total_minutes,
    )
    await NPC_RELATIONSHIPS.record_encounter(
        interaction.user.id, npc,
        summary=f"Player: {message[:220]} | {npc}: {answer[:360]}",
        deltas={"trust": 1},
    )
    try:
        await QUESTS.progress(interaction.user.id, "talk", target=npc, game_minute=wt_now.total_minutes)
    except Exception:
        log.exception("Quest progress update failed after NPC talk")
    recommendation_hint = ""
    if bool(npc_data.get("can_recommend")) and npc_data.get("sect_affiliation"):
        recommendation_hint = (
            f"\n\n🏯 **Sect connection:** {npc} is affiliated with **{npc_data.get('sect_affiliation')}**. "
            "After speaking with them, you may use **Sect → Recruitment → Recommendation** to ask for formal sponsorship."
        )
    await reply_long(interaction, f"**{npc}**\n{answer}{recommendation_hint}")


SCENE_ACTION_TYPES: dict[str, dict[str, Any]] = {
    "observe": {"label": "Observe", "emoji": "👁️", "attribute": "insight", "tn": 11, "description": "Read the scene, behavior, tracks or obvious details."},
    "investigate": {"label": "Investigate", "emoji": "🔎", "attribute": "insight", "tn": 14, "description": "Search for concealed clues, causes, mechanisms or evidence."},
    "influence": {"label": "Influence", "emoji": "🗣️", "attribute": "presence", "tn": 14, "description": "Persuade, intimidate, negotiate or shape someone's response."},
    "stealth": {"label": "Stealth", "emoji": "🌫️", "attribute": "agility", "tn": 14, "description": "Hide, shadow, infiltrate or move without drawing attention."},
    "physical": {"label": "Physical Feat", "emoji": "💪", "attribute": "body", "tn": 14, "description": "Climb, force, endure, lift, leap or overcome a physical obstacle."},
    "qi": {"label": "Qi Control", "emoji": "✨", "attribute": "spirit", "tn": 14, "description": "Manipulate qi carefully without using a formal combat technique."},
    "resolve": {"label": "Resolve", "emoji": "🧘", "attribute": "will", "tn": 14, "description": "Resist fear, pain, pressure, temptation or spiritual strain."},
    "aid": {"label": "Aid", "emoji": "🤝", "attribute": "presence", "tn": 11, "description": "Help, reassure, coordinate or support someone in the scene."},
}


def _scene_player_target_id(target: str) -> int | None:
    text = str(target or "")
    if not text.startswith("Player ") or ":" not in text:
        return None
    raw = text[7:].split(":", 1)[0].strip()
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


async def _scene_action_targets(character: dict[str, Any]) -> list[str]:
    wt = await current_world_time()
    targets: list[str] = []
    location = str(character.get("location") or "")
    for npc_name in WORLD.npcs:
        if await current_npc_location(npc_name, wt.period) == location:
            targets.append(npc_name)
    player_rows = await DB.get_characters_at_location(
        location, exclude_user_id=int(character.get("user_id") or 0) or None
    )
    for row in player_rows:
        targets.append(f"Player {int(row['user_id'])}: {str(row.get('name') or 'Cultivator')}"[:100])
    return targets[:20]


async def _resolve_scene_action(
    interaction: discord.Interaction, *, action_key: str, target: str, detail: str,
    character: dict[str, Any] | None = None,
) -> None:
    c = character or await require_character(interaction)
    if not c:
        return
    profile = SCENE_ACTION_TYPES.get(action_key)
    if not profile:
        await reply_long(interaction, "That scene action is no longer available.", ephemeral=False)
        return
    target = str(target or "Environment")
    player_target_id = _scene_player_target_id(target)
    if target not in {"Environment", "Self"}:
        if player_target_id is not None:
            rows = await DB.get_characters_at_location(
                str(c.get("location") or ""), exclude_user_id=interaction.user.id
            )
            if not any(int(row.get("user_id") or 0) == player_target_id for row in rows):
                await reply_long(interaction, "That cultivator is not present in your current scene.", ephemeral=False)
                return
        else:
            wt = await current_world_time()
            if await current_npc_location(target, wt.period) != c.get("location"):
                await reply_long(interaction, "That NPC is not present in your current scene.", ephemeral=False)
                return
    detail = str(detail).strip()
    if not detail:
        await reply_long(interaction, "Describe how your character attempts the action.", ephemeral=False)
        return

    # A self-directed action completes automatically because the character is
    # acting on their own known state. This is not an information-discovery
    # bypass: hidden canonical facts remain outside the narrator context.
    # Every external/scene target still uses a transparent canonical roll.
    # Settle time-based effects, then let Go derive the canonical attribute,
    # target number, RNG, and degree. Discord only validates presentation/world
    # presence and narrates the committed fixed result.
    _, _, wt = await current_effect_modifiers(interaction.user.id)
    try:
        envelope = await ENGINE.authoritative_action(
            "scene.action",
            interaction.user.id,
            {
                "action_key": action_key,
                "target": target,
                "detail": detail,
                
            },
            action_id=f"discord:{interaction.id}:scene.action:{action_key}",
        )
    except GameEngineError as exc:
        await reply_long(interaction, f"Scene action could not be resolved: {exc}", ephemeral=False)
        return
    mechanics = dict(envelope.get("result") or {})
    attribute = str(mechanics.get("attribute") or profile["attribute"])
    tn = int(mechanics.get("tn", profile["tn"]))
    self_action = bool(mechanics.get("automatic", False))
    result = None if self_action else SimpleNamespace(**mechanics)
    if self_action:
        fixed = (
            f"Scene action: {profile['label']}\nTarget: Self\n"
            f"Attribute: {attribute.title()}\n"
            "Outcome: Automatic success — self-directed action.\n"
            "Disclosure boundary: describe only the character's visible, felt, remembered, or otherwise known state. "
            "Hidden canonical information remains concealed."
        )
    else:
        fixed = (
            f"Scene action: {profile['label']}\nTarget: {target}\n"
            f"Attribute: {attribute.title()}\n{roll_line(result)}"
        )
    action_text = f"{profile['label']} — target: {target}. {detail}"
    channel_id = interaction.channel_id or 0
    await DB.add_history(channel_id, user_id=interaction.user.id, speaker=c["name"], content=action_text)
    history = await DB.get_history(channel_id, 20)
    context = await NARRATOR_CONTEXT.build(
        c, scene_type=f"structured_scene_action:{action_key}", query_text=action_text,
        focus_npc=(target if target not in {"Environment", "Self"} and player_target_id is None else ""),
    )
    private_scene = None
    if interaction.guild is not None and isinstance(interaction.channel, discord.Thread):
        private_scene = await _private_scene_for_thread(interaction.guild, interaction.channel.id, interaction.user.id, c)
    keep_in_scene = bool(private_scene)
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=False)
    try:
        narration = await NARRATOR_QUEUE.run(
            "scene_action",
            NARRATOR.narrate_action(
                character=c,
                action=action_text,
                history=history,
                fixed_roll=fixed,
                scene_context=context.text,
            ),
        )
    except Exception:
        log.exception("Structured scene-action narration failed")
        narration = (
            "The self-directed action succeeds, but reveals nothing beyond what the character can perceive or already knows."
            if self_action
            else "The fixed roll stands. No additional mechanical result was invented."
        )
    await DB.add_history(channel_id, user_id=None, speaker="World", content=narration)
    try:
        outcome_memory = "automatic self success" if self_action else ("success" if result.success else ("partial failure" if result.margin >= -3 else "failure"))
        await DB.add_rag_memory(
            interaction.user.id, memory_kind="scene_action",
            salience=42 if (self_action or (result and result.success)) else 34,
            location=str(c.get("location") or ""),
            npc_name=(target if target not in {"Environment", "Self"} and player_target_id is None else ""),
            source="scene_action", game_minute=context.game_minute,
            summary=(
                f"{c.get('name','The player')} attempted {profile['label']} at {c.get('location','Unknown')} "
                f"targeting {target}: {detail[:300]} | Outcome: {outcome_memory}. "
                f"Observed response: {narration[:520]}"
            ),
        )
        wt_now = await current_world_time()
        await QUESTS.progress(interaction.user.id, "scene_action", amount=1, target=action_key, game_minute=wt_now.total_minutes)
    except Exception:
        log.exception("Quest progress update failed after Scene Action")
    if self_action:
        card_colour = 0x57F287
        result_text = (
            "✅ **Automatic success — self-directed action.**\n"
            "This does not reveal hidden canonical information."
        )
        result_footer = "Automatic self-action • Narration remains limited to character-known information"
    elif result.success:
        card_colour = 0x57F287
        result_text = roll_line(result)
        result_footer = "Canonical roll resolved by game rules • AI narration cannot change mechanics"
    elif result.margin >= -3:
        card_colour = 0xF0A33E
        result_text = roll_line(result)
        result_footer = "Canonical roll resolved by game rules • AI narration cannot change mechanics"
    else:
        card_colour = 0xED4245
        result_text = roll_line(result)
        result_footer = "Canonical roll resolved by game rules • AI narration cannot change mechanics"
    embed = discord.Embed(
        title=f"{profile['emoji']} {profile['label']} → {target}"[:256],
        description=narration[:4096],
        color=card_colour,
    )
    embed.add_field(name="🎲 Result", value=result_text[:1024], inline=False)
    embed.add_field(name="🧠 Attribute", value=f"**{attribute.title()}**", inline=True)
    embed.add_field(name="🎯 Target", value=f"**{target[:180]}**", inline=True)
    embed.add_field(name="📝 Attempt", value=detail[:1024], inline=False)
    embed.set_footer(text=result_footer)
    await interaction.followup.send(embed=embed, ephemeral=False)


class SceneActionDetailModal(discord.ui.Modal):
    def __init__(self, *, action_key: str, target: str):
        profile = SCENE_ACTION_TYPES[action_key]
        super().__init__(title=f"{profile['label']} • {target}"[:45])
        self.action_key = action_key
        self.target = target
        self.detail = discord.ui.TextInput(
            label="How do you attempt it?",
            placeholder="Describe your method, words, movement or intent.",
            style=discord.TextStyle.paragraph,
            min_length=2,
            max_length=500,
        )
        self.add_item(self.detail)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _resolve_scene_action(
            interaction,
            action_key=self.action_key,
            target=self.target,
            detail=str(self.detail.value),
        )


class SceneActionTypeSelect(discord.ui.Select):
    def __init__(self, view: "SceneActionView"):
        self.scene_view = view
        super().__init__(
            placeholder="Choose what you are trying to do",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=str(profile["label"]),
                    value=key,
                    description=str(profile["description"])[:100],
                    emoji=str(profile["emoji"]),
                    default=key == view.action_key,
                )
                for key, profile in SCENE_ACTION_TYPES.items()
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.scene_view.action_key = self.values[0]
        self.scene_view.refresh_components()
        await interaction.response.edit_message(embed=self.scene_view.embed(), view=self.scene_view)


class SceneActionTargetSelect(discord.ui.Select):
    def __init__(self, view: "SceneActionView"):
        self.scene_view = view
        opts = [
            discord.SelectOption(label="Environment / Scene", value="Environment", emoji="🌍"),
            discord.SelectOption(label="Self", value="Self", emoji="🧘"),
        ]
        opts.extend(discord.SelectOption(label=name[:100], value=name[:100], emoji="👤") for name in view.npcs[:20])
        super().__init__(placeholder="Choose a target", min_values=1, max_values=1, options=opts[:25])

    async def callback(self, interaction: discord.Interaction) -> None:
        self.scene_view.target = self.values[0]
        self.scene_view.refresh_components()
        await interaction.response.edit_message(embed=self.scene_view.embed(), view=self.scene_view)


class SceneActionView(discord.ui.View):
    def __init__(self, owner_id: int, character: dict[str, Any], npcs: list[str]):
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)
        self.character = dict(character)
        self.npcs = list(npcs)
        self.action_key = "observe"
        self.target = "Environment"
        self.refresh_components()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This Scene Action panel belongs to another cultivator.", ephemeral=False)
            return False
        return True

    def refresh_components(self) -> None:
        self.clear_items()
        self.add_item(SceneActionTypeSelect(self))
        self.add_item(SceneActionTargetSelect(self))
        button = discord.ui.Button(label="Describe & Resolve", style=discord.ButtonStyle.primary, emoji="🎭")
        async def run(interaction: discord.Interaction) -> None:
            await interaction.response.send_modal(SceneActionDetailModal(action_key=self.action_key, target=self.target))
        button.callback = run
        self.add_item(button)

    def embed(self) -> discord.Embed:
        profile = SCENE_ACTION_TYPES[self.action_key]
        self_target = self.target == "Self"
        embed = discord.Embed(
            title="🎭 Scene Action",
            description=(
                "Choose the kind of action and a target. The game engine selects the stat and difficulty, "
                "then the read-only AI narrator describes the fixed outcome. Self-directed actions succeed automatically but cannot "
                "reveal hidden information; other targets use a canonical roll."
            ),
            color=0x6D78A8,
        )
        embed.add_field(name="Action", value=f"{profile['emoji']} **{profile['label']}**\n{profile['description']}", inline=False)
        embed.add_field(name="Target", value=f"**{self.target}**", inline=True)
        check_text = (
            "**Automatic success**\nHidden information stays concealed"
            if self_target
            else f"**{str(profile['attribute']).title()}** vs **TN {profile['tn']}**"
        )
        embed.add_field(name="Check", value=check_text, inline=True)
        embed.set_footer(text=f"Current location: {self.character.get('location','Unknown')} • no reward/state change is invented by narration")
        return embed


@registered_root_command(name="action", description="Open the guided Scene Action panel", guild=GUILD)
async def scene_action_command(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    npcs = await _scene_action_targets(c)
    view = SceneActionView(interaction.user.id, c, npcs)

    target_thread: discord.Thread | None = None
    if interaction.guild is not None and isinstance(interaction.channel, discord.Thread):
        scene = await _private_scene_for_thread(interaction.guild, interaction.channel.id, interaction.user.id, c)
        if scene:
            target_thread = interaction.channel
    if target_thread is None:
        target_thread = await active_private_location_thread(interaction, c)
    if target_thread is None and not str(c.get("location") or "").startswith("personal_world:"):
        target_thread = await ensure_expedition_thread(interaction, c)

    if target_thread is not None:
        try:
            panel = await target_thread.send(embed=view.embed(), view=view)
            await interaction.response.send_message(
                f"🎭 Your Scene Action panel is open in {target_thread.mention}: {panel.jump_url}", ephemeral=False
            )
            return
        except discord.HTTPException:
            log.exception("Could not post Scene Action panel to private scene thread")
    await interaction.response.send_message(
        embed=view.embed(), view=view, ephemeral=False
    )


ATTRIBUTE_CHOICES = [
    app_commands.Choice(name="Body", value="body"),
    app_commands.Choice(name="Agility", value="agility"),
    app_commands.Choice(name="Spirit", value="spirit"),
    app_commands.Choice(name="Insight", value="insight"),
    app_commands.Choice(name="Will", value="will"),
    app_commands.Choice(name="Presence", value="presence"),
]
DIFFICULTY_CHOICES = [
    app_commands.Choice(name="Easy (11)", value=11),
    app_commands.Choice(name="Standard (14)", value=14),
    app_commands.Choice(name="Hard (17)", value=17),
    app_commands.Choice(name="Severe (20)", value=20),
]



@registered_root_command(
    name="sense",
    description="Use Spiritual Sense on yourself, another cultivator, an NPC, or the surrounding area",
    guild=GUILD,
)
@app_commands.autocomplete(npc=local_npc_autocomplete)
async def sense_command(
    interaction: discord.Interaction,
    target: discord.Member | None = None,
    npc: str | None = None,
    area: bool = False,
) -> None:
    c = await require_character(interaction)
    if not c:
        return

    selected = int(target is not None) + int(bool(npc)) + int(area)
    if selected > 1:
        await interaction.response.send_message(
            "Choose only one sense target: another player, an NPC, or `area:true`.",
            ephemeral=False,
        )
        return

    wt = await current_world_time()

    # No target = personal Spiritual Sense status. Derived sense/concealment
    # values are calculated by Go from canonical state.
    if selected == 0 or (target is not None and target.id == interaction.user.id):
        try:
            status = dict(await ENGINE.action(
                "sense.status", interaction.user.id, {},
            ) or {})
        except GameEngineError as exc:
            await interaction.response.send_message(f"Spiritual Sense status could not resolve: {exc}", ephemeral=False)
            return
        await interaction.response.send_message(
            "🔍 **Spiritual Sense**\n"
            f"Power: **{int(status.get('power', 0))}**\n"
            f"Precision: **{int(status.get('precision', 0))}**\n"
            f"Range: **{int(status.get('range_m', 0)):,} m**\n"
            f"Aura concealment: **{'Active' if status.get('concealment_active') else 'Off'}**\n"
            f"Current concealment strength: **{int(status.get('concealment_strength', 0))}**\n\n"
            "Power penetrates concealment, Precision controls how much detail you can identify, and Range controls area scans.",
            ephemeral=False,
        )
        return

    if target is not None:
        tc = await DB.get_character(target.id)
        if not tc:
            await interaction.response.send_message(
                f"{target.mention} does not have a cultivation character yet.", ephemeral=False
            )
            return
        try:
            envelope = await ENGINE.authoritative_action(
                "sense.inspect", interaction.user.id,
                {"mode": "player", "target_user_id": target.id},
                action_id=f"discord:{interaction.id}:sense.inspect:player:{target.id}",
            )
        except GameEngineError as exc:
            await interaction.response.send_message(f"Spiritual Sense could not resolve: {exc}", ephemeral=False)
            return
        sensed = dict(envelope.get("result") or {})
        roll = SimpleNamespace(**dict(sensed.get("roll") or {}))
        precision = dict(sensed.get("precision") or {})
        precision_tier = str(precision.get("tier", "failure"))
        detection_tier = str(sensed.get("detection_tier", "failure"))
        reveal = str(sensed.get("reveal", "none"))
        if reveal == "none":
            reading = (
                "Their aura slips away from your probing sense; you cannot obtain a reliable cultivation reading."
                if sensed.get("target_concealed")
                else "You detect spiritual activity, but its depth remains unclear."
            )
        elif reveal == "world":
            reading = f"You estimate that this cultivator belongs to **{WORLD.realm_world(int(sensed.get('target_realm_index', 0)))}** power."
        elif reveal == "realm":
            reading = f"Main cultivation appears to be **{WORLD.realm_name(int(sensed.get('target_realm_index', 0)), tc.get('gender'))}**."
        elif reveal == "approx":
            reading = (
                "Main cultivation: **"
                + WORLD.approximate_realm(
                    int(sensed.get("target_realm_index", 0)), int(sensed.get("target_phase", 1)),
                    precision_tier=precision_tier, gender=tc.get("gender"),
                )
                + "**."
            )
        else:
            body_name = WORLD.body_realm_name(int(tc.get("body_realm_index", 0)), tc.get("gender"))
            reading = (
                f"Main cultivation: **{WORLD.realm_name(int(sensed.get('target_realm_index', 0)), tc.get('gender'))} — Stage {int(sensed.get('target_phase', 1))}**.\n"
                f"Body cultivation: **{body_name} — Stage {tc.get('body_phase', 1)}**."
            )
        await interaction.response.send_message(
            f"🔍 **Spiritual Sense — {tc['name']}**\n{roll_line(roll)}\n"
            f"Precision: **{int(precision.get('total', 0))}** vs detail TN **{int(precision.get('tn', 0))}** — **{precision_tier.replace('_', ' ').title()}**\n\n"
            f"{reading}",
            ephemeral=False,
        )
        return

    if npc:
        if npc not in WORLD.npcs:
            await interaction.response.send_message("Unknown NPC.", ephemeral=False)
            return
        npc_location = await current_npc_location(npc, wt.period)
        if npc_location and npc_location != c.get("location"):
            await interaction.response.send_message(
                f"**{npc}** is not currently present at **{c['location']}**.", ephemeral=False
            )
            return
        try:
            envelope = await ENGINE.authoritative_action(
                "sense.inspect", interaction.user.id,
                {"mode": "npc", "npc_name": npc},
                action_id=f"discord:{interaction.id}:sense.inspect:npc:{npc}",
            )
        except GameEngineError as exc:
            await interaction.response.send_message(f"Spiritual Sense could not resolve: {exc}", ephemeral=False)
            return
        sensed = dict(envelope.get("result") or {})
        roll = SimpleNamespace(**dict(sensed.get("roll") or {}))
        precision = dict(sensed.get("precision") or {})
        lines = [f"🔍 **Spiritual Sense — {npc}**", roll_line(roll)]
        if precision:
            precision_tier = str(precision.get("tier", "failure"))
            lines.append(
                f"Precision: **{int(precision.get('total', 0))}** vs detail TN **{int(precision.get('tn', 0))}** — "
                f"**{precision_tier.replace('_', ' ').title()}**"
            )
        lines.append("")
        lines.append(str(sensed.get("reading") or "You cannot obtain a stable reading from their aura."))
        await interaction.response.send_message("\n".join(lines), ephemeral=False)
        return

    # Area sweep. Python supplies only which hidden NPCs are physically present;
    # the still-unmigrated autonomous world simulation owns that movement. Go
    # owns perception RNG, thresholds, detail, hints, and secret-safe signals.
    present_hidden: list[str] = []
    for hidden_name in hidden_npc_names(WORLD.hidden_masters):
        if await current_npc_location(hidden_name, wt.period) == c["location"]:
            present_hidden.append(hidden_name)
    try:
        envelope = await ENGINE.authoritative_action(
            "sense.inspect", interaction.user.id,
            {
                "mode": "area",
                "present_hidden_npcs": present_hidden,
                
            },
            action_id=f"discord:{interaction.id}:sense.inspect:area",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"Spiritual Sense sweep could not resolve: {exc}", ephemeral=False)
        return
    sensed = dict(envelope.get("result") or {})
    roll = SimpleNamespace(**dict(sensed.get("roll") or {}))
    area_precision = dict(sensed.get("precision") or {})
    precision_tier = str(area_precision.get("tier", "failure"))
    lines = [
        f"🌌 **Spiritual Sense Sweep — {sensed.get('location', c['location'])}**",
        roll_line(roll),
        f"Precision: **{int(area_precision.get('total', 0))}** vs detail TN **{int(area_precision.get('tn', 0))}** — "
        f"**{precision_tier.replace('_', ' ').title()}**",
        f"Effective range: **{int(sensed.get('range_m', 0)):,} m**",
    ]
    hints = list(sensed.get("hints") or [])
    if hints:
        lines.extend(f"• {hint}" for hint in hints)
    elif not bool(getattr(roll, "success", False)):
        lines.append("• Environmental interference prevents a clean sweep.")

    if bool(sensed.get("event_visibility")):
        active_events = await DB.get_active_world_events(c["location"])
        for event in active_events[:3]:
            if event["event_type"] == "secret_realm":
                lines.append(f"• A strong **spatial distortion** is present: {event['title']}.")
            else:
                lines.append(f"• Abnormal spiritual currents match the active phenomenon **{event['title']}**.")

    for signal in sensed.get("hidden_signals", []):
        if signal == "artificial":
            lines.append("• One nearby aura feels **artificially projected or inconsistent**.")
        elif signal == "concealed":
            lines.append("• One nearby presence is **far too perfectly concealed to feel ordinary**.")

    await interaction.response.send_message("\n".join(lines), ephemeral=False)


@registered_root_command(name="conceal", description="Turn your cultivation-aura concealment on or off", guild=GUILD)
@serialized_user_action
async def conceal_command(interaction: discord.Interaction, active: bool) -> None:
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "sense.conceal", interaction.user.id,
            {"active": active},
            action_id=f"discord:{interaction.id}:sense.conceal",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"Aura concealment could not change: {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    strength = int(result.get("concealment_strength", 0))
    await interaction.response.send_message(
        f"{'🌑' if active else '✨'} Aura concealment **{'enabled' if active else 'disabled'}**.\n"
        f"Current concealment strength: **{strength}**.\n"
        "Concealment suppresses your readable aura; it does not make you physically invisible.",
        ephemeral=False,
    )


@registered_root_command(name="check", description="Make a transparent RP skill check with no automatic reward", guild=GUILD)
@app_commands.choices(attribute=ATTRIBUTE_CHOICES, difficulty=DIFFICULTY_CHOICES)
async def check(
    interaction: discord.Interaction,
    attribute: app_commands.Choice[str],
    difficulty: app_commands.Choice[int],
    action: str,
) -> None:
    c = await require_character(interaction)
    if not c:
        return
    # Effect settlement remains adapter orchestration for now; Go owns the
    # effective attribute calculation, d10 rolls, target comparison, and result.
    _, _, wt = await current_effect_modifiers(interaction.user.id)
    try:
        envelope = await ENGINE.authoritative_action(
            "check.resolve",
            interaction.user.id,
            {
                "attribute": attribute.value,
                "tn": difficulty.value,
                "label": action,
                
            },
            action_id=f"discord:{interaction.id}:check.resolve",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"Check could not be resolved: {exc}", ephemeral=False)
        return
    result = SimpleNamespace(**dict(envelope.get("result") or {}))
    fixed = f"Action: {action}\n{roll_line(result)}"
    channel_id = interaction.channel_id or 0
    await DB.add_history(
        channel_id,
        user_id=interaction.user.id,
        speaker=c["name"],
        content=action,
    )
    history = await DB.get_history(channel_id, 24)
    lineage_context = await DB.describe_lineage_context(interaction.user.id)
    social_context = (await NARRATOR_CONTEXT.build(
        c, scene_type="freeform roleplay", lineage_context=lineage_context,
    )).text
    await interaction.response.defer()
    try:
        narration = await NARRATOR.narrate_action(
            character=c,
            action=action,
            history=history,
            fixed_roll=fixed,
            social_context=social_context,
            scene_context=social_context,
        )
    except Exception:
        log.exception("Check narration failed")
        narration = "The roll stands as shown; no mechanical rewards or losses are applied."
    await DB.add_history(channel_id, user_id=None, speaker="World", content=narration)
    await reply_long(interaction, f"{roll_line(result)}\n\n{narration}")


@registered_root_command(name="npcinfo", description="Show public NPC information, current schedule location, and known family notes", guild=GUILD)
@app_commands.autocomplete(npc=local_npc_autocomplete)
async def npc_info_command(interaction: discord.Interaction, npc: str) -> None:
    data = await DB.get_npc_definition(npc)
    if not data:
        await interaction.response.send_message("Unknown NPC.", ephemeral=False)
        return
    c = await require_character(interaction, allow_deceased=True)
    if not c:
        return
    wt = await current_world_time()
    current_location = await current_npc_location(npc, wt.period) or data.get("location", "Unknown")
    if not await _location_is_visible(interaction.user.id, c, str(current_location)):
        await interaction.response.send_message("You have no reliable knowledge of that cultivator yet.", ephemeral=False)
        return
    lines = [
        f"👤 **{npc}**",
        f"Role: **{data.get('role', 'Unknown')}**",
        f"Public cultivation: **{data.get('realm', 'Unknown')}**" + (f" Stage {data.get('stage')}" if data.get('stage') else ""),
        f"Current location ({wt.period}): **{current_location}**",
    ]
    sim_state = await SIM.npc_status(npc) or {}
    if sim_state.get("activity"):
        lines.append(f"Current activity: **{sim_state.get('activity')}**")
    mood = public_mood_hint(str(sim_state.get("mood") or ""))
    if mood:
        lines.append(f"Outward demeanor: **{mood.title()}**")
    relationship = await NPC_RELATIONSHIPS.get(interaction.user.id, npc)
    if int(relationship.get("encounter_count", 0)) > 0:
        lines.append(
            f"Your relationship: **{NPC_RELATIONSHIPS.public_label(relationship)}** "
            f"({int(relationship.get('encounter_count',0))} remembered encounters)"
        )
    if data.get("title"):
        lines.append(f"Title: **{data['title']}**")
    if data.get("public_family"):
        lines.append(f"Public family/court notes: {data['public_family']}")
    if data.get("hidden_master"):
        lines.append("Hidden state: **Not publicly verifiable. Spiritual Sense may or may not reveal more.**")
    await interaction.response.send_message("\n".join(lines), ephemeral=False)


@registered_root_command(name="world", description="Show the current world and locations you have actually discovered", guild=GUILD)
async def world(interaction: discord.Interaction) -> None:
    c = await require_character(interaction, allow_deceased=True)
    if not c:
        return
    known = await _known_locations(interaction.user.id, c)
    current_world = WORLD.realm_world(int(c.get("realm_index", 0)))
    lines = [f"🌍 **Known World — {current_world}**", f"Current location: **{c.get('location','Unknown')}**"]
    visible = []
    for name in sorted(known):
        data = WORLD.locations.get(name)
        if not data or not _world_is_unlocked(c, str(data.get("world") or current_world)):
            continue
        visible.append((name, data))
    for name, data in visible:
        marker = "📍" if name == c.get("location") else "🧭"
        lines.append(f"\n{marker} **{name}** — {data['description']}")
    wt = await current_world_time()
    nearby = []
    for npc_name in WORLD.npcs:
        if await current_npc_location(npc_name, wt.period) == c.get("location"):
            nearby.append(npc_name)
    if nearby:
        lines.append("\n**NPCs currently here:** " + ", ".join(nearby[:20]))
    lines.append("\n\nExplore known regions to discover additional routes. Higher worlds and their inhabitants remain hidden until your cultivation reaches them.")
    await reply_long(interaction, "".join(lines), ephemeral=False)


@registered_root_command(name="worldevents", description="Show categorized active phenomena, consequences, and secret realms", guild=GUILD)
async def worldevents(interaction: discord.Interaction) -> None:
    c = await require_character(interaction, allow_deceased=True)
    if not c:
        return
    known = await _known_locations(interaction.user.id, c)
    events = [
        event for event in await DB.get_active_world_events()
        if str(event.get("location")) in known
        and _world_is_unlocked(c, str((WORLD.locations.get(str(event.get("location"))) or {}).get("world") or WORLD.realm_world(int(c.get("realm_index",0)))))
    ]
    if not events:
        await interaction.response.send_message("The world is unusually quiet. No major phenomena are active.", ephemeral=False)
        return
    now = time.time()
    lines = ["🌌 **Active World Events**"]
    for event in events:
        remain = human_duration(int(event["ends_at"] - now))
        thread_ref = f" — scene <#{event['thread_id']}>" if event.get("thread_id") else ""
        if event["event_type"] == "secret_realm":
            realm_id = event["payload"]["realm_id"]
            realm = WORLD.secret_realms[realm_id]
            category=str(event["payload"].get("category") or "Secret Realm")
            lines.append(
                f"\n🌀 **{realm['name']}** — {event['location']} — **{category}** — closes in **{remain}**{thread_ref}\n"
                f"{realm['description']}"
            )
        else:
            payload=dict(event.get("payload") or {})
            category=str(payload.get("category") or "Phenomenon")
            severity=max(1,min(10,int(payload.get("severity",1))))
            consequence=str(payload.get("consequence_text") or "").strip()
            lines.append(
                f"\n⚡ **{event['title']}** — {event['location']} — **{category}**, severity **{severity}/10** — ends in **{remain}**{thread_ref}"
                + (f"\n{consequence}" if consequence else "")
            )
    await reply_long(interaction, "".join(lines), ephemeral=False)


@registered_root_command(name="inheritances", description="View the ancient inheritances your character has obtained", guild=GUILD)
async def inheritances(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    owned = await DB.get_inheritances(interaction.user.id)
    if not owned:
        await interaction.response.send_message("You have not obtained an ancient inheritance yet.", ephemeral=False)
        return
    lines = [f"📜 **{c['name']}'s Inheritances**"]
    for row in owned:
        info = WORLD.inheritances.get(row["inheritance_id"], {})
        lines.append(f"\n**{info.get('name', row['inheritance_id'])}**\n{info.get('description', '')}")
    await reply_long(interaction, "".join(lines), ephemeral=False)


# ---------------- Perfect Realm commands ----------------
perfect_group = app_commands.Group(name="perfect", description="Long-form Stage 9 Realm Perfection")

@registered_group_command(perfect_group, name="start", description="Begin the optional Perfect Path at Stage 9")
@serialized_user_action
async def perfect_start(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        await ENGINE.authoritative_action(
            "perfection.start", interaction.user.id, {},
            action_id=f"discord:{interaction.id}:perfection.start",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"Perfect Path could not begin: {exc}", ephemeral=False)
        return
    quest = WORLD.perfection_quest(int(c["realm_index"]), 0, c)
    await interaction.response.send_message(
        f"★ **Perfect Path begun: {WORLD.realm_name(c['realm_index'], c.get('gender'))}**\n"
        f"Perfection starts at **0%**. Training contributes at most **{WORLD.perfection_training_cap()}%**; the rest comes from seven long quests.\n\n"
        f"**First Quest — {quest['title']}**\n{quest['description']}\nPreparation: 0/{quest['preparation_required']}\nUse **/quest → Realm Perfection → Quest**.",
        ephemeral=False,
    )

@registered_group_command(perfect_group, name="info", description="View your Realm Perfection progress")
async def perfect_info(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c: return
    p = await DB.get_perfection(interaction.user.id, c["realm_index"])
    if not p:
        await interaction.response.send_message("No Perfect Path is active. At Stage 9 use **/quest → Realm Perfection → Start**.", ephemeral=False); return
    status = "COMPLETED" if p["completed"] else ("ACTIVE" if p["active"] else "INACTIVE")
    text = (f"★ **{WORLD.realm_name(c['realm_index'], c.get("gender"))} Perfection — {status}**\n"
            f"Progress: **{p['progress']}%**\nTraining: **{p['training_progress']}/{WORLD.perfection_training_cap()}**\n"
            f"Quests: **{p['completed_quests']}/{WORLD.perfection_quest_count()}**")
    if p["active"] and p["quest_index"] < WORLD.perfection_quest_count():
        q = WORLD.perfection_quest(c["realm_index"], p["quest_index"], c)
        text += f"\n\nCurrent: **{q['title']}**\nPreparation: **{p['quest_preparation']}/{q['preparation_required']}**"
    await interaction.response.send_message(text, ephemeral=False)

PERFECT_ACTIONS=[app_commands.Choice(name="Info",value="info"),app_commands.Choice(name="Prepare",value="prepare"),app_commands.Choice(name="Attempt",value="attempt")]
@registered_group_command(perfect_group, name="quest", description="Inspect, prepare for, or attempt your current Perfection quest")
@app_commands.choices(action=PERFECT_ACTIONS)
@serialized_user_action
async def perfect_quest(interaction: discord.Interaction, action: app_commands.Choice[str]) -> None:
    c = await require_character(interaction)
    if not c:
        return
    p = await DB.get_perfection(interaction.user.id, c["realm_index"])
    if action.value == "info":
        if not p or not p["active"]:
            await interaction.response.send_message("Start the Perfect Path first with **/quest → Realm Perfection → Start**.", ephemeral=False)
            return
        if p["quest_index"] >= WORLD.perfection_quest_count():
            await interaction.response.send_message("All seven quests are complete. Reach 100% and use **/quest → Realm Perfection → Trial**.", ephemeral=False)
            return
        q = WORLD.perfection_quest(c["realm_index"], p["quest_index"], c)
        await interaction.response.send_message(
            f"📜 **Perfection Quest {p['quest_index']+1}/{WORLD.perfection_quest_count()} — {q['title']}**\n{q['description']}\n"
            f"Preparation: **{p['quest_preparation']}/{q['preparation_required']}**\nTrial: **{q['attribute'].title()} — TN {q['tn']}**\nClue: *{q['clue']}*\nReward: **+{q['progress_reward']}% Perfection**", ephemeral=False,
        )
        return
    _, _, wt = await current_effect_modifiers(interaction.user.id)
    try:
        envelope = await ENGINE.authoritative_action(
            "perfection.quest", interaction.user.id,
            {"mode": action.value, 
             "quest_cooldown_seconds": SETTINGS.perfect_quest_cooldown_minutes * 60},
            action_id=f"discord:{interaction.id}:perfection.quest:{action.value}",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"Perfection quest could not resolve: {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    if action.value == "prepare":
        prep = int(result.get("preparation", 0)); required = int(result.get("preparation_required", 0))
        await interaction.response.send_message(
            f"🧭 **{result.get('title','Perfection')} — Preparation**\nProgress: **{min(prep, required)}/{required}**\n{result.get('clue','')}" +
            ("\n✨ The trial is now available with **/quest → Realm Perfection → Quest → Attempt**." if prep >= required else ""), ephemeral=False,
        )
        return
    roll = SimpleNamespace(**dict(result.get("roll") or {}))
    if bool(result.get("success")):
        text = f"{roll_line(roll)}\n✨ **{result.get('title','Perfection quest')} completed.** +{int(result.get('progress_reward',0))}% Perfection."
    else:
        text = f"{roll_line(roll)}\n⚠️ The trial rejects your current understanding. Your preparation remains; try again later."
    await interaction.response.send_message(text, ephemeral=False)

@registered_group_command(perfect_group, name="clues", description="Review Perfection clues you have discovered")
async def perfect_clues(interaction: discord.Interaction) -> None:
    c=await require_character(interaction)
    if not c:return
    p=await DB.get_perfection(interaction.user.id,c["realm_index"])
    clues=(p or {}).get("discovered",[])
    await interaction.response.send_message("🔎 **Discovered Realm Clues**\n"+("\n".join(f"• {x}" for x in clues) if clues else "None yet."),ephemeral=False)

@registered_group_command(perfect_group, name="trial", description="Attempt the final Realm Perfection trial")
@serialized_user_action
async def perfect_trial(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    _, _, wt = await current_effect_modifiers(interaction.user.id)
    try:
        envelope = await ENGINE.authoritative_action(
            "perfection.trial", interaction.user.id,
            {"trial_cooldown_seconds": SETTINGS.perfect_trial_cooldown_minutes * 60},
            action_id=f"discord:{interaction.id}:perfection.trial",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"Final Perfection trial could not resolve: {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    lines = ["🌌 **FINAL REALM PERFECTION TRIAL**"]
    for row in result.get("rolls", []):
        lines.append(f"{row.get('name','Trial')}: {roll_line(SimpleNamespace(**dict(row)))}")
    if bool(result.get("success")):
        lines.append(f"\n★ **PERFECT {WORLD.realm_name(c['realm_index'], c.get('gender')).upper()} ACHIEVED**\nYour Max Qi and Vitality permanently increase, and future major breakthroughs gain a bonus.")
    else:
        lines.append(
            f"\n⚠️ The final compression fails. **{int(result.get('training_loss',0))}% recoverable Perfection training** is lost, but your realm remains stable. "
            "Restore it with **/cultivation → Meditation** before trying again."
        )
    await reply_long(interaction, "\n".join(lines))

@registered_group_command(perfect_group, name="abandon", description="Abandon the active Perfect Path and lose its progress")
@serialized_user_action
async def perfect_abandon(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "perfection.abandon", interaction.user.id, {},
            action_id=f"discord:{interaction.id}:perfection.abandon",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"Perfect Path could not be abandoned: {exc}", ephemeral=False)
        return
    if not bool(dict(envelope.get("result") or {}).get("abandoned")):
        await interaction.response.send_message("No active Perfect Path to abandon.", ephemeral=False)
        return
    await interaction.response.send_message("The Perfect Path has been abandoned. You may now break through normally.", ephemeral=False)


# ---------------- Secret Realm commands ----------------
secret_group = app_commands.Group(name="secretrealm", description="Enter and explore temporary hidden realms")

@registered_group_command(secret_group, name="status", description="Show secret realms available at your location or your active run")
async def secret_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    try:
        envelope = await ENGINE.action("secret_realm.status", interaction.user.id, {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"Secret-realm status could not be read: {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    if bool(result.get("active")):
        run = dict(result.get("run") or {})
        realm = dict(run.get("realm") or {})
        room = run.get("room")
        room_name = dict(room or {}).get("name") if room else "Inheritance Chamber Complete"
        remaining = max(0, int(float(run.get("expires_at") or 0) - time.time()))
        text = (
            f"🌀 **Inside {realm.get('name', run.get('realm_id', 'Secret Realm'))}**\n"
            f"Room: **{room_name}**\nDanger: **{int(run.get('danger') or 0)}/3**\n"
            f"Closes in: **{human_duration(remaining)}**"
        )
        await interaction.response.send_message(text, ephemeral=False)
        return
    available = list(result.get("available") or [])
    if not available:
        await interaction.response.send_message("No secret-realm entrance is currently open at your location.", ephemeral=False)
        return
    lines = ["🌀 **Open Secret Realms Here**"]
    for entry in available:
        entry = dict(entry or {})
        realm = dict(entry.get("realm") or {})
        thread_ref = f" — scene <#{entry['thread_id']}>" if entry.get("thread_id") else ""
        remaining = max(0, int(float(entry.get("ends_at") or 0) - time.time()))
        lines.append(
            f"\n**{realm.get('name', entry.get('realm_id', 'Secret Realm'))}** — minimum realm: "
            f"{WORLD.realm_name(int(realm.get('min_realm_index') or 0))} — closes in {human_duration(remaining)}{thread_ref}"
        )
    await interaction.response.send_message("".join(lines), ephemeral=False)

@registered_group_command(secret_group, name="enter", description="Enter an open secret realm by name")
@serialized_user_action
async def secret_enter(interaction: discord.Interaction, realm: str) -> None:
    c = await require_character(interaction)
    if not c:
        return
    await interaction.response.defer(ephemeral=False)
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "secret_realm.enter", interaction.user.id,
            {"realm_id": realm},
            action_id=f"discord:{interaction.id}:secret_realm.enter",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"You cannot enter that secret realm: {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    info = dict(result.get("realm") or {})
    first = dict(result.get("first_room") or {})
    thread_ref = f"\n\n💬 Shared expedition thread: <#{result['thread_id']}>" if result.get("thread_id") else ""
    await interaction.followup.send(
        f"🌀 **{c['name']} enters {info.get('name', result.get('realm_id', 'the secret realm'))}.**\n"
        f"{info.get('description', '')}\n\nFirst area: **{first.get('name', 'Unknown Chamber')}**\n"
        f"{first.get('description', '')}\nUse **/realm → Secret Realms → Explore**.{thread_ref}",
        ephemeral=False,
    )

@registered_group_command(secret_group, name="explore", description="Attempt the next area of your active secret realm")
@serialized_user_action
async def secret_explore(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    await interaction.response.defer(ephemeral=False)
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "secret_realm.explore", interaction.user.id,
            {
                
                "cooldown_seconds": SETTINGS.secret_realm_cooldown_minutes * 60,
            },
            action_id=f"discord:{interaction.id}:secret_realm.explore",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"The secret realm resists your attempt: {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    room = dict(result.get("room") or {})
    roll = SimpleNamespace(**dict(result.get("roll") or {}))
    text = (
        f"🌀 **{result.get('realm_name', 'Secret Realm')} — {room.get('name', 'Unknown Area')}**\n"
        f"{room.get('description', '')}\n\n{roll_line(roll)}"
    )
    if bool(result.get("success")):
        cultivation = int(result.get("cultivation_awarded") or 0)
        stones = int(result.get("spirit_stones") or 0)
        insight = int(result.get("insight_xp") or 0)
        items = dict(result.get("items") or {})
        text += f"\n✨ Area cleared. **+{cultivation} cultivation, +{stones} spirit stones, +{insight} insight**"
        if items:
            text += f", {WORLD.item_names(items)}"
        if bool(result.get("final_room")):
            inheritance = dict(result.get("inheritance") or {})
            if bool(inheritance.get("gained")):
                bonuses = dict(inheritance.get("bonuses") or {})
                text += (
                    f"\n\n📜 **INHERITANCE OBTAINED — {inheritance.get('name', 'Ancient Legacy')}**\n"
                    f"{inheritance.get('description', '')}\nPermanent gains: "
                    f"+{int(bonuses.get('qi_max') or 0)} Max Qi, "
                    f"+{int(bonuses.get('vitality_max') or 0)} Max Vitality, "
                    f"+{int(bonuses.get('insight_xp') or 0)} Insight XP."
                )
            else:
                text += "\n\nThe inheritance recognizes that you already carry this legacy and grants no duplicate permanent bonus."
        else:
            next_room = dict(result.get("next_room") or {})
            if next_room:
                text += f"\n\nNext: **{next_room.get('name', 'Unknown Area')}**"
    else:
        danger = int(result.get("danger") or 0)
        text += f"\n⚠️ The realm rejects your attempt. Danger rises to **{danger}/3**."
        if bool(result.get("ejected")):
            text += "\n💥 Space collapses around you and forcibly ejects you from the secret realm. You keep rewards already earned."
    await interaction.followup.send(text, ephemeral=False)

@registered_group_command(secret_group, name="leave", description="Leave your active secret realm voluntarily")
@serialized_user_action
async def secret_leave(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "secret_realm.leave", interaction.user.id, {},
            action_id=f"discord:{interaction.id}:secret_realm.leave",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"You cannot leave the secret realm cleanly: {exc}", ephemeral=False)
        return
    left = bool(dict(envelope.get("result") or {}).get("left"))
    message = (
        "You withdraw before the secret realm seals. Rewards already obtained are kept."
        if left else "You are not currently inside an active secret realm."
    )
    await interaction.response.send_message(message, ephemeral=False)



# ---------- Sect lineage / forms of address ----------
ADDRESS_STYLE_CHOICES = [
    app_commands.Choice(name="Masculine — Senior Brother / Junior Brother", value="masculine"),
    app_commands.Choice(name="Feminine — Senior Sister / Junior Sister", value="feminine"),
    app_commands.Choice(name="Neutral — Senior / Junior Martial Sibling", value="neutral"),
]



@registered_root_command(name="time", description="Show the canonical in-world cultivation calendar", guild=GUILD)
async def world_time_command(interaction: discord.Interaction) -> None:
    wt = await current_world_time()
    c = await DB.get_character(interaction.user.id)
    cycle = cultivation_cycle_summary(wt, c.get("spiritual_root") if c else None)
    age_line=""
    if c:
        life=await authoritative_lifespan(interaction.user.id)
        age_line = (f"\nYour age: **{life.age_years:.1f} years** • lifespan: **Ageless**" if life.ageless else f"\nYour age: **{life.age_years:.1f} years** • lifespan ceiling: **{life.total_years} years**")
    await interaction.response.send_message(
        f"🕰️ **World Time**\n{wt.display}\n"
        f"Automatic rate: **{SETTINGS.world_time_scale} game minutes per real minute**.{age_line}\n\n"
        f"**Current Cultivation Flow**\n{cycle}",
        ephemeral=False,
    )


@registered_root_command(name="rulers", description="Show rulers of realm worlds your cultivation can currently perceive", guild=GUILD)
async def rulers_command(interaction: discord.Interaction) -> None:
    c = await require_character(interaction, allow_deceased=True)
    if not c:
        return
    lines = ["👑 **Recognized Rulers of Known Worlds**"]
    for world, npc_name in WORLD.world_rulers.items():
        if not _world_is_unlocked(c, world):
            continue
        npc = WORLD.npcs.get(npc_name, {})
        lines.append(
            f"\n**{world}** — {npc_name}"
            f"\n{npc.get('title', npc.get('role', 'Ruler'))} • {npc.get('realm', 'Unknown')} Stage {npc.get('stage', '?')}"
            f"\nSeat: {npc.get('location', 'Unknown')}"
        )
    await interaction.response.send_message("\n".join(lines), ephemeral=False)


@registered_root_command(name="wallet", description="View all cultivation currencies you currently hold", guild=GUILD)
async def wallet_command(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    wallet = await DB.get_wallet(interaction.user.id)
    if not wallet:
        await interaction.response.send_message("Your wallet is empty.", ephemeral=False)
        return
    grouped: dict[str, list[str]] = {}
    for currency_id, balance in wallet.items():
        info = WORLD.currencies.get(currency_id, {})
        world = str(info.get("world", "Other"))
        grouped.setdefault(world, []).append(f"• {WORLD.currency_name(currency_id)}: **{balance:,}**")
    lines = [f"💎 **{c['name']}'s Wallet**"]
    for world in ("Mortal World", "Spiritual World", "Immortal World", "Celestial World", "Other"):
        if world in grouped:
            lines.append(f"\n**{world}**\n" + "\n".join(grouped[world]))
    await interaction.response.send_message("\n".join(lines), ephemeral=False)


@registered_root_command(name="effects", description="View active buffs, debuffs, curses and other generic effects", guild=GUILD)
async def effects_command(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    effects, _, wt = await current_effect_modifiers(interaction.user.id)
    if not effects:
        await interaction.response.send_message("You have no active timed or persistent effects.", ephemeral=False)
        return
    lines = [f"✨ **Active Effects — {c['name']}**"]
    for effect in effects:
        ends = effect.get("ends_game_minute")
        remaining = "Permanent" if ends is None else f"{max(0, int(ends)-wt.total_minutes)} game minutes remaining"
        lines.append(f"\n**{effect.get('name', effect.get('effect_key', 'Effect'))}** — {remaining}")
        if effect.get("description"):
            lines.append(str(effect["description"]))
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


async def usable_item_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    inv = await DB.get_inventory(interaction.user.id)
    needle = current.casefold().strip()
    choices=[]
    for item_id in inv:
        item=WORLD.items.get(item_id,{})
        if not item.get("use") and not item.get("storage_upgrade") and not item.get("array_deploy"):
            continue
        label=str(item.get("name",item_id))
        if not needle or needle in label.casefold() or needle in item_id.casefold():
            choices.append(app_commands.Choice(name=label[:100], value=item_id[:100]))
    return choices[:25]


@registered_root_command(name="use", description="Use a consumable, pill, or spatial-storage treasure", guild=GUILD)
@app_commands.autocomplete(item=usable_item_autocomplete)
@serialized_user_action
async def use_item_command(interaction: discord.Interaction, item: str) -> None:
    c = await require_character(interaction)
    if not c:
        return
    item_def = WORLD.items.get(item)
    if not item_def:
        await interaction.response.send_message("Unknown item.", ephemeral=False)
        return
    inv = await DB.get_inventory(interaction.user.id)
    if inv.get(item, 0) <= 0:
        await interaction.response.send_message("You do not carry that item.", ephemeral=False)
        return

    storage_upgrade = item_def.get("storage_upgrade")
    use = item_def.get("use", {})
    array_key = str(item_def.get("array_deploy") or "")
    if not storage_upgrade and not use and not array_key:
        await interaction.response.send_message("That item has no implemented active use yet.", ephemeral=False)
        return

    if array_key:
        wt=await current_world_time()
        try:
            e=await ENGINE.authoritative_action("array.deploy",interaction.user.id,{"item_id":item},action_id=f"discord:{interaction.id}:array.deploy")
            deployed=dict(e.get("result") or {})
        except GameEngineError as exc:
            await interaction.response.send_message(f"❌ {exc}",ephemeral=False);return
        await interaction.response.send_message(f"🧿 **{deployed.get('name',item_def.get('name',item))} deployed at {deployed.get('location',c.get('location'))}.**",ephemeral=False)
        return

    if storage_upgrade:
        wt=await current_world_time()
        try:
            e=await ENGINE.authoritative_action("storage.upgrade",interaction.user.id,{"item_id":item},action_id=f"discord:{interaction.id}:storage.upgrade")
            upgraded=dict(e.get("result") or {})
        except GameEngineError as exc:
            await interaction.response.send_message(f"❌ {exc}",ephemeral=False);return
        await interaction.response.send_message(f"✨ Spatial storage upgraded to **{item_def.get('name',item)}** — **{upgraded.get('slot_capacity',storage_upgrade.get('slot_capacity',24))} item stacks**.",ephemeral=False)
        return

    if not await DB.consume_item(interaction.user.id, item, 1):
        await interaction.response.send_message("The item is no longer in your carried inventory.", ephemeral=False)
        return

    lines=[f"✨ **Used {item_def.get('name', item)}**"]

    instant=use.get("instant", {})
    if instant:
        state=await DB.restore_resources(
            interaction.user.id,
            qi=int(instant.get("qi_restore",0)),
            vitality=int(instant.get("vitality_restore",0)),
        )
        if instant.get("qi_restore"):
            lines.append(f"Qi restored to **{state.get('qi',0)}/{state.get('qi_max',0)}**.")
        if instant.get("vitality_restore"):
            lines.append(f"Vitality restored to **{state.get('vitality',0)}/{state.get('vitality_max',0)}**.")

    life_years = max(0, int(use.get("lifespan_years", 0)))
    if life_years:
        total_extension = await DB.add_life_extension(interaction.user.id, life_years)
        lines.append(
            f"🌿 Lifespan permanently extended by **{life_years} years** "
            f"(medicine/herb extension total: **{total_extension} years**)."
        )

    if use.get("effect"):
        wt=await current_world_time()
        payload=normalize_effect_payload({"effect_key":use.get("effect_key",item),"name":use.get("name",item_def.get("name",item)),**use["effect"]})
        await DB.apply_effect(
            interaction.user.id,
            effect_key=str(use.get("effect_key",item)),
            name=str(use.get("name",item_def.get("name",item))),
            source_type="item", source_id=item, effect=payload,
            starts_game_minute=wt.total_minutes,
            duration_game_minutes=int(use.get("duration_game_minutes",0)) or None,
        )
        lines.append(f"Effect applied: **{use.get('name', item_def.get('name', item))}**.")

    toxicity_gain = pill_toxicity_value(item, item_def) if is_pill(item, item_def) else 0
    if toxicity_gain:
        wt = await current_world_time()
        alchemy_state = await DB.add_pill_toxicity(
            interaction.user.id, toxicity_gain, game_minute=wt.total_minutes,
        )
        await sync_pill_toxicity_effect(
            interaction.user.id, game_minute=wt.total_minutes, state=alchemy_state,
        )
        toxicity = int(alchemy_state.get("pill_toxicity", 0))
        band, _ = toxicity_band(toxicity)
        lines.append(f"⚗️ Medicinal residue **+{toxicity_gain}** → pill toxicity **{toxicity}/100 ({band})**.")
    await interaction.response.send_message("\n".join(lines))


scene_group = app_commands.Group(name="scene", description="Inspect the current roleplay scene and in-world time")


@registered_group_command(scene_group, name="status", description="Show scene time, location rules and NPCs currently present")
async def scene_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    wt=await current_world_time()
    loc=await DB.get_location_definition(c["location"]) or WORLD.locations.get(c["location"],{})
    local=[]
    for name in WORLD.npcs:
        if await current_npc_location(name,wt.period)==c["location"]:
            local.append(name)
    lines=[
        f"🎭 **Scene — {c['location']}**",
        f"Time: **{wt.display}**",
        f"World: **{loc.get('world','Mortal World')}**",
        f"Protection: **{'Protected / no violence' if loc.get('safe_zone') else 'No absolute protection'}**",
        f"NPCs present: {', '.join(local) if local else 'none currently visible'}",
    ]
    await interaction.response.send_message("\n".join(lines),ephemeral=False)


storage_group = app_commands.Group(name="storage", description="Manage your spatial pouch, ring, or inner-space treasure")


async def carried_item_autocomplete(interaction: discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    inv=await DB.get_inventory(interaction.user.id); needle=current.casefold().strip(); out=[]
    for item_id,qty in inv.items():
        name=WORLD.item_name(item_id)
        if not needle or needle in name.casefold() or needle in item_id.casefold():
            out.append(app_commands.Choice(name=f"{name} x{qty}"[:100],value=item_id[:100]))
    return out[:25]


async def stored_item_autocomplete(interaction: discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    storage=await DB.get_storage(interaction.user.id) or {}; needle=current.casefold().strip(); out=[]
    for item_id,qty in storage.get("items",{}).items():
        name=WORLD.item_name(item_id)
        if not needle or needle in name.casefold() or needle in item_id.casefold():
            out.append(app_commands.Choice(name=f"{name} x{qty}"[:100],value=item_id[:100]))
    return out[:25]


@registered_group_command(storage_group, name="status",description="Inspect your current spatial storage")
async def storage_status(interaction:discord.Interaction)->None:
    c=await require_character(interaction)
    if not c:return
    st=await DB.get_storage(interaction.user.id)
    if not st:
        await interaction.response.send_message("You do not possess spatial storage.",ephemeral=False);return
    lines=[f"💍 **{st['name']}** — {st['grade']}",f"Item stacks: **{st['used_slots']}/{st['slot_capacity']}**",f"Living space: **{'Yes' if st['living_space'] else 'No'}**"]
    for item_id,qty in st.get("items",{}).items(): lines.append(f"• {WORLD.item_name(item_id)} x{qty}")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(storage_group, name="deposit",description="Move carried items into spatial storage")
@app_commands.autocomplete(item=carried_item_autocomplete)
@serialized_user_action
async def storage_deposit(interaction:discord.Interaction,item:str,quantity:app_commands.Range[int,1,999999]=1)->None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("storage.deposit",interaction.user.id,{"item_id":item,"quantity":int(quantity)},action_id=f"discord:{interaction.id}:storage.deposit")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"📦 Stored **{WORLD.item_name(item)} x{quantity}**.",ephemeral=False)


@registered_group_command(storage_group, name="withdraw",description="Take items out of spatial storage")
@app_commands.autocomplete(item=stored_item_autocomplete)
@serialized_user_action
async def storage_withdraw(interaction:discord.Interaction,item:str,quantity:app_commands.Range[int,1,999999]=1)->None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("storage.withdraw",interaction.user.id,{"item_id":item,"quantity":int(quantity)},action_id=f"discord:{interaction.id}:storage.withdraw")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🎒 Withdrew **{WORLD.item_name(item)} x{quantity}**.",ephemeral=False)


auction_group = app_commands.Group(name="auction", description="Use protected Xianxia auction houses and competitive bidding")


def _house_for_character(c:dict):
    return WORLD.auction_house_at(c.get("location",""))


@registered_group_command(auction_group, name="enter",description="Enter the local protected auction hall")
@serialized_user_action
async def auction_enter(interaction:discord.Interaction)->None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("auction.enter",interaction.user.id,{},action_id=f"discord:{interaction.id}:auction.enter")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🏮 You enter **{result.get('name','the auction hall')}**. Hidden experts and formations suppress violence inside.\n🛡️ **Protection applies only inside the hall. The moment you leave through the doors, it ends.**",ephemeral=False)


@registered_group_command(auction_group, name="leave",description="Leave the auction hall; its protection ends at the door")
@serialized_user_action
async def auction_leave(interaction:discord.Interaction)->None:
    c=await require_character(interaction)
    if not c:return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("auction.leave",interaction.user.id,{},action_id=f"discord:{interaction.id}:auction.leave")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False);return
    lines=[f"🚪 You step out of **{result.get('name','the auction hall')}** into **{result.get('outside','outside')}**.","The Pavilion's protection ends at the door."]
    incident=dict(result.get('incident') or {})
    if incident.get('triggered'):
        if incident.get('battle_id'): lines.append(f"⚔️ A stronger pursuer ambushed you. Battle **#{incident['battle_id']}** has begun.")
        else: lines.append("🌑 Someone took an interest in your auction purchase after you left the Pavilion.")
    await interaction.response.send_message("\n".join(lines),ephemeral=False)


@registered_group_command(auction_group, name="browse",description="Browse active lots in the current auction house")
async def auction_browse(interaction:discord.Interaction)->None:
    c=await require_character(interaction)
    if not c:return
    found=_house_for_character(c)
    if not found:
        await interaction.response.send_message("Enter an auction house first with **/economy → Auction House → Enter**.",ephemeral=False);return
    house_id,house=found; lots=await DB.list_active_auctions(house_id)
    if not lots:
        await interaction.response.send_message("The auction board currently has no active player lots.",ephemeral=False);return
    now=time.time(); lines=[f"🏮 **{house['name']} — Active Lots**"]
    for lot in lots[:25]:
        item_name=WORLD.item_name(str(lot['item_id'])); bid=int(lot.get('current_bid') or 0); minimum=max(int(lot['starting_bid']),bid+1)
        bidder="Anonymous" if lot.get('anonymous') and lot.get('current_bidder_user_id') else "None"
        if lot.get('current_bidder_user_id') and not lot.get('anonymous'):
            bidder_c=await DB.get_character(int(lot['current_bidder_user_id'])); bidder=bidder_c['name'] if bidder_c else 'Unknown'
        lines.append(
            f"\n`#{lot['auction_id']}` **{item_name} x{lot['quantity']}**\n"
            f"Current: **{bid or 'No bids'} {WORLD.currency_name(str(lot['currency_id']))}** • next minimum **{minimum}**\n"
            f"High bidder: **{bidder}** • closes in **{human_duration(int(float(lot['ends_at'])-now))}**"
        )
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


async def auction_currency_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    needle=current.casefold().strip(); out=[]
    for cid,info in WORLD.currencies.items():
        name=str(info.get('name',cid))
        if not needle or needle in name.casefold() or needle in cid.casefold(): out.append(app_commands.Choice(name=name[:100],value=cid[:100]))
    return out[:25]


@registered_group_command(auction_group, name="sell",description="List a carried item for protected auction")
@app_commands.autocomplete(item=carried_item_autocomplete,currency=auction_currency_autocomplete)
@serialized_user_action
async def auction_sell(
    interaction:discord.Interaction,item:str,quantity:app_commands.Range[int,1,999999],
    starting_bid:app_commands.Range[int,1,2000000000],duration_minutes:app_commands.Range[int,5,1440]=60,
    anonymous:bool=False,currency:str="low_spirit_stone"
)->None:
    c=await require_character(interaction)
    if not c:return
    found=_house_for_character(c)
    if not found:
        await interaction.response.send_message("You must be inside an auction house to list a lot.",ephemeral=False);return
    house_id,_=found
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("auction.sell",interaction.user.id,{"house_id":house_id,"item_id":item,"quantity":int(quantity),"currency_id":currency,"starting_bid":int(starting_bid),"anonymous":anonymous,"ends_at":time.time()+int(duration_minutes)*60},action_id=f"discord:{interaction.id}:auction.sell")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🏮 Lot `#{result.get('auction_id')}` listed: **{WORLD.item_name(item)} x{quantity}** starting at **{starting_bid} {WORLD.currency_name(currency)}**.",ephemeral=False)


@registered_group_command(auction_group, name="bid",description="Place an escrowed bid on an active auction lot")
@serialized_user_action
async def auction_bid(interaction:discord.Interaction,auction_id:int,amount:app_commands.Range[int,1,2000000000])->None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("auction.bid",interaction.user.id,{"auction_id":int(auction_id),"amount":int(amount)},action_id=f"discord:{interaction.id}:auction.bid")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🔨 Bid accepted on lot `#{auction_id}`: **{amount} {WORLD.currency_name(str(result.get('currency_id','low_spirit_stone')))}**.",ephemeral=False)


battle_group = app_commands.Group(name="battle",description="Resolve active danger scenes such as auction-door ambushes")
BATTLE_STYLE_CHOICES=[app_commands.Choice(name="Attack",value="attack"),app_commands.Choice(name="Defend",value="defend"),app_commands.Choice(name="Flee",value="flee")]

async def _battle_reply(
    interaction:discord.Interaction, *, content:str|None=None, embed:discord.Embed|None=None,
    view:discord.ui.View|None=None, edit_panel:bool=False, ephemeral:bool=False,
)->None:
    if edit_panel:
        if interaction.response.is_done():
            await interaction.edit_original_response(content=content,embed=embed,view=view)
        else:
            await interaction.response.edit_message(content=content,embed=embed,view=view)
        return
    if interaction.response.is_done():
        await interaction.followup.send(content=content,embed=embed,view=view,ephemeral=False)
    else:
        await interaction.response.send_message(content=content,embed=embed,view=view,ephemeral=False)


async def _battle_available_options(user_id:int,c:dict)->tuple[list[tuple[str,str,str]],list[tuple[str,str,str]]]:
    law_rows={str(r['law_id']):r for r in await DB.get_law_progress(user_id)}
    techniques:list[tuple[str,str,str]]=[]
    for tid,t in WORLD.law_system.get('techniques',{}).items():
        row=law_rows.get(str(t.get('law')))
        if not row: continue
        stage=WORLD.law_stage(int(row.get('comprehension',0)))
        if int(stage.get('index',0))>=int(t.get('requires_stage',99)) and int(c.get('realm_index',0))>=int(t.get('min_realm_index',999)):
            techniques.append((str(tid),str(t.get('name',tid)),str(t.get('description','Law technique'))))
    inv=await DB.get_inventory(user_id); usable:list[tuple[str,str,str]]=[]
    for iid,qty in inv.items():
        idef=WORLD.items.get(iid,{})
        if qty>0 and idef.get('use',{}).get('instant'):
            instant=idef.get('use',{}).get('instant',{})
            recovery=[]
            if int(instant.get('vitality_restore',0)): recovery.append(f"Vitality +{int(instant['vitality_restore'])}")
            if int(instant.get('qi_restore',0)): recovery.append(f"Qi +{int(instant['qi_restore'])}")
            usable.append((str(iid),f"{idef.get('name',iid)} x{qty}"," • ".join(recovery) or "Instant recovery"))
    return techniques[:25],usable[:25]

def _battle_embed(c:dict,b:dict,techniques:list[tuple[str,str,str]],items:list[tuple[str,str,str]], *, result_text:str|None=None)->discord.Embed:
    defeated=int(b.get("npc_hp",0))<=0
    description=result_text or ("Your opponent is defeated. Decide their fate." if defeated else "Choose your next action.")
    player_max=max(1,int(b.get('player_hp_max',0)),int(c.get('vitality_max',0)),int(b.get('player_hp',0)))
    npc_max=max(1,int(b.get('npc_hp_max',0)),int(b.get('npc_hp',0)))
    _,embed_color=vitality_band(int(b.get('player_hp',0)),player_max)
    e=discord.Embed(title=f"⚔️ Battle #{b['battle_id']} — {b['npc_name']}",description=description,color=embed_color)
    e.add_field(name="Your Vitality",value=vitality_bar(int(b['player_hp']),player_max),inline=False)
    e.add_field(name="Opponent Vitality",value=vitality_bar(int(b['npc_hp']),npc_max),inline=False)
    e.add_field(
        name="Cultivation",
        value=(
            f"**You:** {WORLD.realm_name(int(c.get('realm_index',0)))} • Stage {int(c.get('phase',1))}\n"
            f"**Opponent:** {WORLD.realm_name(int(b['npc_realm_index']))} • Stage {int(b['npc_stage'])}"
        ),inline=False,
    )
    e.add_field(
        name="Matchup",
        value=matchup_label(int(c.get('realm_index',0)),int(c.get('phase',1)),int(b['npc_realm_index']),int(b['npc_stage'])),
        inline=True,
    )
    e.add_field(name="Location",value=str(b.get('location') or 'Unknown'),inline=True)
    e.add_field(name="Suppression",value=suppression_label(int(b.get('npc_suppressed_turns',0))),inline=False)
    if defeated:
        e.add_field(name="Final Decision",value="🤝 Spare — end the battle without killing\n☠️ Kill — true NPC death with persistent world consequences",inline=False)
    else:
        e.add_field(name="Core Actions",value="⚔️ Attack • 🛡️ Defend • 🏃 Flee • 🔄 Refresh",inline=False)
        e.add_field(name="Battle Menus",value=f"🌌 Law techniques: **{len(techniques)}**\n🧪 Recovery items: **{len(items)}**",inline=False)
    e.set_footer(text=f"Battle #{int(b['battle_id'])} • Owner locked • Panel updates in place")
    return e


class BattleTechniqueSelect(discord.ui.Select):
    def __init__(self,parent:"BattleView",techniques:list[tuple[str,str,str]]):
        self.parent_view=parent
        available=bool(techniques)
        options=[discord.SelectOption(label=name[:100],value=tid[:100],description=description[:100]) for tid,name,description in techniques]
        if not options: options=[discord.SelectOption(label="No Law techniques available",value="__none__")]
        super().__init__(placeholder="Use a Law technique",min_values=1,max_values=1,options=options,disabled=not available,row=1)
    async def callback(self,interaction:discord.Interaction)->None:
        await self.parent_view._dispatch(interaction,"technique",self.values[0])


class BattleRecoverySelect(discord.ui.Select):
    def __init__(self,parent:"BattleView",items:list[tuple[str,str,str]]):
        self.parent_view=parent
        available=bool(items)
        options=[discord.SelectOption(label=name[:100],value=iid[:100],description=description[:100]) for iid,name,description in items]
        if not options: options=[discord.SelectOption(label="No recovery items carried",value="__none__")]
        super().__init__(placeholder="Use a recovery item",min_values=1,max_values=1,options=options,disabled=not available,row=2)
    async def callback(self,interaction:discord.Interaction)->None:
        await self.parent_view._dispatch(interaction,"item",self.values[0])


class BattleView(discord.ui.View):
    def __init__(self,user_id:int,battle_id:int,techniques:list[tuple[str,str,str]],items:list[tuple[str,str,str]]):
        super().__init__(timeout=300); self.user_id=int(user_id); self.battle_id=int(battle_id)
        self.add_item(BattleTechniqueSelect(self,techniques)); self.add_item(BattleRecoverySelect(self,items))
    async def interaction_check(self,interaction:discord.Interaction)->bool:
        if interaction.user.id!=self.user_id:
            await interaction.response.send_message("This battle panel belongs to another cultivator.",ephemeral=False); return False
        return True
    async def _dispatch(self,interaction:discord.Interaction,kind:str,value:str="")->None:
        if not interaction.response.is_done(): await interaction.response.defer()
        lock=_USER_ACTION_LOCKS.setdefault(interaction.user.id,asyncio.Lock())
        async with lock:
            battle=await DB.get_battle(self.battle_id,user_id=self.user_id,active_only=True)
            active=await DB.get_active_battle(self.user_id)
            if not battle or not active or int(active['battle_id'])!=self.battle_id:
                await _battle_reply(interaction,content="⌛ This battle panel is stale. Use **/combat → Active Battle → Status** for the current battle.",view=None,edit_panel=True);return
            if kind in {"attack","defend","flee"}:
                await _resolve_battle_turn(interaction,kind,expected_battle_id=self.battle_id,edit_panel=True);return
            c=await DB.get_character(self.user_id)
            if not c:
                await _battle_reply(interaction,content="This incarnation no longer exists.",view=None,edit_panel=True);return
            if kind=="technique": result=await _execute_battle_law_technique(interaction,battle,value)
            elif kind=="item": result=await _use_battle_recovery_item(interaction,self.battle_id,value)
            else: result="🔄 Battle panel refreshed."
            updated=await DB.get_battle(self.battle_id,user_id=self.user_id,active_only=True)
            if not updated:
                await _battle_reply(interaction,content="⌛ This battle has already ended.",view=None,edit_panel=True);return
            c=await DB.get_character(self.user_id) or c
            embed,view=await _battle_panel(self.user_id,c,updated,result_text=result)
            await _battle_reply(interaction,embed=embed,view=view,edit_panel=True)
    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
        await _report_game_ui_error(interaction, error, where=f"battle:{self.battle_id}:{type(item).__name__}")
    @discord.ui.button(label="Attack",style=discord.ButtonStyle.danger,emoji="⚔️",row=0)
    async def attack(self,interaction:discord.Interaction,button:discord.ui.Button): await self._dispatch(interaction,"attack")
    @discord.ui.button(label="Defend",style=discord.ButtonStyle.primary,emoji="🛡️",row=0)
    async def defend(self,interaction:discord.Interaction,button:discord.ui.Button): await self._dispatch(interaction,"defend")
    @discord.ui.button(label="Flee",style=discord.ButtonStyle.secondary,emoji="🏃",row=0)
    async def flee(self,interaction:discord.Interaction,button:discord.ui.Button): await self._dispatch(interaction,"flee")
    @discord.ui.button(label="Refresh",style=discord.ButtonStyle.secondary,emoji="🔄",row=0)
    async def refresh(self,interaction:discord.Interaction,button:discord.ui.Button): await self._dispatch(interaction,"refresh")

class BattleFinishView(discord.ui.View):
    def __init__(self,user_id:int,battle_id:int):
        super().__init__(timeout=300); self.user_id=int(user_id); self.battle_id=int(battle_id)
    async def interaction_check(self,interaction:discord.Interaction)->bool:
        if interaction.user.id!=self.user_id:
            await interaction.response.send_message("This battle decision belongs to another cultivator.",ephemeral=False); return False
        return True
    async def _finish(self,interaction:discord.Interaction,outcome:str)->None:
        if not interaction.response.is_done(): await interaction.response.defer()
        lock=_USER_ACTION_LOCKS.setdefault(interaction.user.id,asyncio.Lock())
        async with lock: await _finish_battle(interaction,outcome,expected_battle_id=self.battle_id,edit_panel=True)
    async def _refresh(self,interaction:discord.Interaction)->None:
        if not interaction.response.is_done(): await interaction.response.defer()
        lock=_USER_ACTION_LOCKS.setdefault(interaction.user.id,asyncio.Lock())
        async with lock:
            b=await DB.get_battle(self.battle_id,user_id=self.user_id,active_only=True); c=await DB.get_character(self.user_id)
            if not b or not c or int(b.get('npc_hp',1))>0:
                await _battle_reply(interaction,content="⌛ This final-decision panel is stale.",view=None,edit_panel=True);return
            embed,view=await _battle_panel(self.user_id,c,b,result_text="🔄 Final decision refreshed. Choose the opponent's fate.")
            await _battle_reply(interaction,embed=embed,view=view,edit_panel=True)
    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
        await _report_game_ui_error(interaction, error, where=f"battle-finish:{self.battle_id}:{type(item).__name__}")
    @discord.ui.button(label="Spare",style=discord.ButtonStyle.success,emoji="🤝",row=0)
    async def spare(self,interaction:discord.Interaction,button:discord.ui.Button): await self._finish(interaction,"spare")
    @discord.ui.button(label="Kill",style=discord.ButtonStyle.danger,emoji="☠️",row=0)
    async def kill(self,interaction:discord.Interaction,button:discord.ui.Button): await self._finish(interaction,"kill")
    @discord.ui.button(label="Refresh",style=discord.ButtonStyle.secondary,emoji="🔄",row=0)
    async def refresh(self,interaction:discord.Interaction,button:discord.ui.Button): await self._refresh(interaction)


async def _battle_panel(user_id:int,c:dict,b:dict,*,result_text:str|None=None)->tuple[discord.Embed,discord.ui.View]:
    techniques,items=await _battle_available_options(user_id,c)
    embed=_battle_embed(c,b,techniques,items,result_text=result_text)
    beasts=await DB.get_spirit_beasts(user_id)
    active_beast=next((x for x in beasts if int(x.get('active',0))==1),None)
    bonds=await DB.get_artifact_bonds(user_id)
    if active_beast:
        embed.add_field(name="Spirit Beast",value=f"🐉 **{active_beast['name']}** • Rank {active_beast['rank']} • Loyalty {active_beast['loyalty']} • Evolution {active_beast['evolution_stage']}",inline=False)
    awakened=[x for x in bonds if int(x.get('awakened',0))==1]
    if awakened:
        embed.add_field(name="Artifact Resonance",value=" • ".join(f"{WORLD.item_name(x['item_id'])} ({x['resonance']}%)" for x in awakened[:3]),inline=False)
    if int(b.get('npc_hp',0))<=0: return embed,BattleFinishView(user_id,int(b['battle_id']))
    return embed,BattleView(user_id,int(b['battle_id']),techniques,items)


async def _use_battle_recovery_item(interaction: discord.Interaction, battle_id: int, item_id: str) -> str:
    wt = await current_world_time()
    try:
        envelope = await COMBAT.recovery_item(
            interaction.user.id,
            battle_id=int(battle_id),
            item_id=item_id,
            game_minute=wt.total_minutes,
            action_id=f"discord:{interaction.id}:combat.recovery_item:{int(battle_id)}:{item_id}",
        )
    except GameEngineError as exc:
        return f"❌ {exc}"
    state = dict(envelope.get("result") or {})
    lines = [f"🧪 **Used {state.get('item_name', item_id)}**"]
    if int(state.get("vitality_restore", 0)):
        lines.append(f"Vitality: **{int(state.get('vitality', 0))}/{int(state.get('vitality_max', 0))}**")
    if int(state.get("qi_restore", 0)):
        lines.append(f"Qi: **{int(state.get('qi', 0))}/{int(state.get('qi_max', 0))}**")
    return "\n".join(lines)

async def _execute_battle_law_technique(interaction: discord.Interaction, battle: dict, technique: str) -> str:
    wt = await current_world_time()
    try:
        envelope = await COMBAT.technique(
            interaction.user.id,
            battle_id=int(battle["battle_id"]),
            technique=technique,
            game_minute=wt.total_minutes,
            action_id=f"discord:{interaction.id}:combat.technique:{int(battle['battle_id'])}:{technique}",
        )
    except GameEngineError as exc:
        return f"❌ {exc}"
    result = dict(envelope.get("result") or {})
    roll = SimpleNamespace(**dict(result.get("roll") or {}))
    lines = [f"🌌 **{result.get('technique_name', technique)}**", roll_line(roll)]
    if bool(getattr(roll, "success", False)):
        if technique == "spatial_lockdown":
            lines.append(f"Space freezes around **{battle['npc_name']}**. Their counteractions are suppressed for **{int(result.get('suppressed_turns', 1))} turn(s)**.")
        elif technique == "spatial_strangulation":
            lines.append(f"Space compresses inward for **{int(result.get('damage_dealt', 0))} conceptual damage**. Opponent Vitality: **{int(result.get('npc_hp', 0))}**.")
            if result.get("opponent_defeated"):
                lines.append("🏆 **Opponent defeated.** The battle remains open for your explicit **Spare** or **Kill** decision.")
        else:
            lines.append("Your Law dominates the local rules of the exchange.")
    else:
        lines.append("The opponent resists or tears free of your spatial authority.")
    return "\n".join(lines)

async def _finish_battle(interaction:discord.Interaction,outcome:str,*,expected_battle_id:int|None=None,edit_panel:bool=False)->None:
    c=await DB.get_character(interaction.user.id)
    if not c:
        await _battle_reply(interaction,content="Create a character first.",ephemeral=False,edit_panel=edit_panel);return
    b=(await DB.get_battle(expected_battle_id,user_id=interaction.user.id,active_only=True)) if expected_battle_id is not None else await DB.get_active_battle(interaction.user.id)
    if not b:
        await _battle_reply(interaction,content="You have no defeated opponent awaiting a final decision.",ephemeral=False,edit_panel=edit_panel);return
    if int(b.get("npc_hp",0))>0:
        await _battle_reply(interaction,content="Your opponent is still fighting.",ephemeral=False,edit_panel=edit_panel);return
    outcome=str(outcome).lower()
    if outcome not in {"spare","kill"}:
        await _battle_reply(interaction,content="Choose **Spare** or **Kill**.",ephemeral=False,edit_panel=edit_panel);return
    wt=await current_world_time()
    try:
        envelope=await COMBAT.finalize(
            interaction.user.id,battle_id=int(b["battle_id"]),outcome=outcome,game_minute=wt.total_minutes,
            action_id=f"discord:{interaction.id}:combat.finalize:{int(b['battle_id'])}:{outcome}",
        )
    except GameEngineError as exc:
        await _battle_reply(interaction,content=f"❌ {exc}",view=None,ephemeral=False,edit_panel=edit_panel);return
    result=dict(envelope.get("result") or {})
    replayed=bool(envelope.get("replayed"))
    if result.get("event_manifestation"):
        verb="disperse" if outcome=="kill" else "drive off"
        await _battle_reply(
            interaction,
            content=(f"⚔️ **You {verb} the hostile manifestation.** It was part of the live event, not a persistent NPC life. "
                     "Your victory has been recorded as canonical event participation and will contribute to the event aftermath."),
            view=None,ephemeral=False,edit_panel=edit_panel,
        )
        return
    impact={"impacts":[str(x) for x in list(result.get("impacts") or [])]}
    lines=[]
    npc_name=str(result.get("npc_name") or b.get("npc_name") or "your opponent")
    if outcome=="kill":
        lines.append(f"☠️ **You kill {npc_name}.** This is a canonical NPC death.")
        if "karma_score" in result:
            lines.append(f"☯️ Karma shifts to **{int(result['karma_score']):+d}**.")
        if int(result.get("grudge_intensity_delta",0)):
            lines.append(f"🗡️ A lineage grudge is now tracked with **+{int(result['grudge_intensity_delta'])}** intensity from this death.")
    else:
        lines.append(f"🤝 **You spare {npc_name}.** The battle ends without a death.")
        if "karma_score" in result:
            lines.append(f"☯️ Karma shifts to **{int(result['karma_score']):+d}**.")
        if "fate_after" in result:
            lines.append(f"🌠 Meaningful mercy draws providence: **Fate {int(result['fate_after'])}/9**.")
    impacts=list(impact.get("impacts") or [])
    if impacts:
        lines.append("🌍 **World consequences:**")
        lines.extend(f"• {x}" for x in impacts[:8])
    elif replayed:
        lines.append("🌍 This final decision was replayed idempotently; its world consequence was not applied twice.")
    else:
        lines.append("🌍 No major faction or family consequence was attached to this opponent, but the action was recorded in world history.")
    await _battle_reply(interaction,content="\n".join(lines),view=None,edit_panel=edit_panel)

async def _resolve_battle_turn(interaction:discord.Interaction,style:str,action:str="",*,expected_battle_id:int|None=None,edit_panel:bool=False)->None:
    c=await DB.get_character(interaction.user.id)
    if not c:
        await _battle_reply(interaction,content="Create a character first.",ephemeral=False,edit_panel=edit_panel);return
    b=(await DB.get_battle(expected_battle_id,user_id=interaction.user.id,active_only=True)) if expected_battle_id is not None else await DB.get_active_battle(interaction.user.id)
    if not b:
        await _battle_reply(interaction,content="You are not in an active battle.",ephemeral=False,edit_panel=edit_panel); return
    if int(b.get("npc_hp",0))<=0:
        embed,view=await _battle_panel(interaction.user.id,c,b)
        await _battle_reply(interaction,embed=embed,view=view,ephemeral=False,edit_panel=edit_panel);return
    wt=await current_world_time()
    try:
        envelope=await COMBAT.turn(
            interaction.user.id,battle_id=int(b["battle_id"]),style=style,action=action,
            game_minute=wt.total_minutes,minutes_per_year=MINUTES_PER_YEAR,
            base_samsara_years=SETTINGS.reincarnation_base_samsara_years,
            max_wait_seconds=SETTINGS.reincarnation_max_wait_seconds,
            action_id=f"discord:{interaction.id}:combat.turn:{int(b['battle_id'])}:{style}",
        )
    except GameEngineError as exc:
        await _battle_reply(interaction,content=f"❌ {exc}",view=None,ephemeral=False,edit_panel=edit_panel);return
    result=dict(envelope.get("result") or {})
    lines=[]
    if str(result.get("action") or "").strip(): lines.append(f"Action: *{str(result['action'])[:300]}*")
    if int(result.get("companion_bonus",0)): lines.append(f"🐉 Artifact/companion support grants **+{int(result['companion_bonus'])}** to this exchange.")
    if int(result.get("equipment_attack",0)) or int(result.get("equipment_defense",0)):
        lines.append(f"🛡️ Equipment contributes **+{int(result.get('equipment_attack',0))} offense / +{int(result.get('equipment_defense',0))} defense**. Durability is consumed by combat exchanges.")
    if result.get("player_roll"):
        lines.append(roll_line(SimpleNamespace(**dict(result["player_roll"]))))
    if result.get("defending"): lines.append("🛡️ You brace and reinforce your defenses.")
    if int(result.get("damage_dealt",0)): lines.append(f"💥 You deal **{int(result['damage_dealt'])}** damage.")
    if result.get("escaped"):
        lines.append("🏃 **Escape successful.**")
        await _battle_reply(interaction,content="\n".join(lines),view=None,edit_panel=edit_panel);return
    if result.get("counter_suppressed"):
        lines.append("🌌 Opponent counter suppressed by spatial control.")
    elif result.get("counter_roll"):
        lines.append(f"**Opponent counter:** {roll_line(SimpleNamespace(**dict(result['counter_roll'])))}")
    if int(result.get("damage_taken",0)): lines.append(f"🩸 You take **{int(result['damage_taken'])}** damage.")
    if result.get("opponent_defeated"):
        updated=await DB.get_battle(int(b["battle_id"]),user_id=interaction.user.id,active_only=True) or {**b,"npc_hp":0,"player_hp":int(result.get("player_hp",b.get("player_hp",1)))}
        c=await DB.get_character(interaction.user.id) or c
        lines.append("🏆 **Opponent defeated.** Decide whether to **Spare** or **Kill** them. Killing named NPCs can permanently change families, sects, regions, alliances and the economy.")
        embed,view=await _battle_panel(interaction.user.id,c,updated,result_text="\n".join(lines))
        await _battle_reply(interaction,embed=embed,view=view,edit_panel=edit_panel);return
    if str(result.get("status"))=="lost":
        injury=dict(result.get("injury") or {})
        if result.get("fate_rescue"):
            lines.append(
                f"🌠 **FATE DEFIES DEATH.** A thread of providence snaps instead of your soul. You survive at **1 Vitality** with "
                f"**{injury.get('name','a grave injury')}** (severity **{int(injury.get('severity',1))}/5**). Fate remaining: **{int(result.get('fate_remaining',0))}/9**."
            )
        elif result.get("true_death"):
            death=dict(result.get("true_death") or {})
            await _record_true_death_history(interaction.user.id,death,wt.total_minutes)
            years=int(death.get("private_years",SETTINGS.reincarnation_base_samsara_years))
            wait=max(1,int(death.get("real_wait_seconds",SETTINGS.reincarnation_max_wait_seconds)))
            lines.append(
                f"☠️ **TRUE DEATH.** Your body and current incarnation are lost. Your soul enters **Samsara** for roughly **{years:,} private years**, "
                f"compressed into at most **{human_duration(wait)}** of real time. The wheel selected **{str(death.get('target_world') or 'Mortal World')}** as your possible rebirth world. "
                "The shared world and your old family are not fast-forwarded."
            )
        else:
            lines.append(
                f"💀 **Defeated.** You survive but are incapacitated and suffer **{injury.get('name','an injury')}** "
                f"(severity **{int(injury.get('severity',1))}/5**). True death was possible in this battle."
            )
        await _battle_reply(interaction,content="\n".join(lines),view=None,edit_panel=edit_panel);return
    updated=await DB.get_active_battle(interaction.user.id) or {**b,'player_hp':int(result.get('player_hp',b.get('player_hp',1))),'npc_hp':int(result.get('npc_hp',b.get('npc_hp',1)))}
    c=await DB.get_character(interaction.user.id) or c
    embed,view=await _battle_panel(interaction.user.id,c,updated,result_text="\n".join(lines))
    await _battle_reply(interaction,embed=embed,view=view,edit_panel=edit_panel)

@registered_group_command(battle_group, name="status",description="View your active battle and all currently available options")
async def battle_status(interaction:discord.Interaction)->None:
    c=await require_character(interaction)
    if not c:return
    b=await DB.get_active_battle(interaction.user.id)
    if not b: await interaction.response.send_message("You are not in an active battle.",ephemeral=False); return
    embed,view=await _battle_panel(interaction.user.id,c,b)
    await interaction.response.send_message(embed=embed,view=view,ephemeral=False)

@registered_group_command(battle_group, name="finish",description="Spare or kill an opponent you have already defeated")
@app_commands.choices(outcome=[app_commands.Choice(name="Spare",value="spare"),app_commands.Choice(name="Kill",value="kill")])
@serialized_user_action
async def battle_finish(interaction:discord.Interaction,outcome:app_commands.Choice[str])->None:
    await _finish_battle(interaction,outcome.value)


@registered_group_command(battle_group, name="challenge",description="Challenge a living NPC or martial-family head at your location")
@serialized_user_action
async def battle_challenge(interaction:discord.Interaction,target:str)->None:
    c=await require_character(interaction)
    if not c:return
    if WORLD.location_safe_zone(str(c.get("location",""))):
        await interaction.response.send_message("🛡️ Violence is suppressed in this protected location.",ephemeral=False);return
    if await DB.get_active_battle(interaction.user.id):
        await interaction.response.send_message("Finish your current battle first.",ephemeral=False);return
    info=await SIM.combat_target(str(c.get("location","")),target)
    if not info:
        await interaction.response.send_message("That living NPC/family head is not mechanically present here or cannot be openly challenged.",ephemeral=False);return
    ri=max(0,int(info.get("realm_index",0))); st=max(1,min(9,int(info.get("phase",1))))
    hp=max(10,12+ri*4+st*2)
    source=f"challenge:{info.get('target_type','npc')}:{info.get('family_id') or info.get('name')}"
    try:
        bid=await DB.create_battle(
            user_id=interaction.user.id,npc_name=str(info["name"]),npc_realm_index=ri,npc_stage=st,
            player_hp=max(1,int(c.get("vitality",1))),player_hp_max=max(1,int(c.get("vitality_max",c.get("vitality",1)))),
            npc_hp=hp,location=str(c.get("location","")),source=source,target_key=source,
        )
    except ValueError as exc:
        await interaction.response.send_message(f"⚔️ {exc}. Wait for that confrontation to end.",ephemeral=False);return
    battle=await DB.get_active_battle(interaction.user.id)
    embed,view=await _battle_panel(interaction.user.id,c,battle or {"battle_id":bid,"npc_name":info["name"],"npc_realm_index":ri,"npc_stage":st,"player_hp":c.get("vitality",1),"player_hp_max":c.get("vitality_max",1),"npc_hp":hp,"npc_hp_max":hp,"location":c.get("location","")})
    await interaction.response.send_message(
        content=f"⚔️ **Challenge accepted.** Battle `#{bid}` begins. If you win, you will explicitly choose whether the defeated NPC lives or dies.",
        embed=embed,view=view,
    )


@battle_challenge.autocomplete("target")
async def battle_challenge_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    c=await DB.get_character(interaction.user.id)
    if not c:return []
    needle=current.casefold().strip(); out=[]
    for row in await SIM.combat_targets(str(c.get("location",""))):
        name=str(row.get("name") or "")
        if name and (not needle or needle in name.casefold()):
            out.append(app_commands.Choice(name=name[:100],value=name[:100]))
    return out[:25]


@registered_group_command(battle_group, name="act",description="Take one action in your active battle")
@app_commands.choices(style=BATTLE_STYLE_CHOICES)
@serialized_user_action
async def battle_act(interaction:discord.Interaction,style:app_commands.Choice[str],action:str="")->None:
    await _resolve_battle_turn(interaction,style.value,action)


# ---------- Law / Dao cultivation ----------
law_group = app_commands.Group(name="law", description="Comprehend and wield the Laws that govern reality")

async def law_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    needle=current.casefold().strip(); out=[]
    for law_id,data in WORLD.law_system.get("laws",{}).items():
        name=str(data.get("name",law_id))
        if not needle or needle in law_id.casefold() or needle in name.casefold(): out.append(app_commands.Choice(name=name[:100],value=law_id[:100]))
    return out[:25]

@registered_group_command(law_group, name="status",description="View your Law and Dao comprehension")
async def law_status(interaction:discord.Interaction)->None:
    c=await require_character(interaction)
    if not c:return
    rows=await DB.get_law_progress(interaction.user.id); daos=await DB.get_dao_progress(interaction.user.id)
    if not rows: await interaction.response.send_message("You have not begun comprehending a Law. Use **/cultivation → Laws → Comprehend** when your realm is sufficient.",ephemeral=False); return
    lines=[f"⚖️ **Law Comprehension — {c['name']}**"]
    for row in rows[:12]:
        d=WORLD.law_definition(str(row['law_id'])) or {}; stage=WORLD.law_stage(int(row['comprehension']))
        lines.append(f"• **{d.get('name',row['law_id'])}** — {row['comprehension']}% • **{stage['name']}** • {d.get('category','Law')}")
    if daos:
        lines.append("\n☯️ **Dao Reconstruction**")
        for row in daos[:8]: lines.append(f"• {row['dao_id']}: **{row['progress']}%**")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)

@registered_group_command(law_group, name="comprehend",description="Meditate on a Law and increase genuine comprehension")
@app_commands.autocomplete(law=law_autocomplete)
@serialized_user_action
async def law_comprehend(interaction:discord.Interaction,law:str)->None:
    c=await require_character(interaction)
    if not c:return
    definition=WORLD.law_definition(law)
    if not definition:
        await interaction.response.send_message("Unknown Law.",ephemeral=False);return
    # Settle time-based effects; Go owns eligibility, affinity, legacy bonus,
    # cooldown, RNG, comprehension/Dao gains, and memory awakening.
    _,_,wt=await current_effect_modifiers(interaction.user.id)
    try:
        envelope=await ENGINE.authoritative_action(
            "law.comprehend",interaction.user.id,
            {"law":law},
            action_id=f"discord:{interaction.id}:law.comprehend:{law}",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"Law comprehension could not resolve: {exc}",ephemeral=False);return
    result=dict(envelope.get("result") or {})
    roll=SimpleNamespace(**dict(result.get("roll") or {}))
    legacy_note=f"\n☸️ Soul Legacy Law Echo: **+{int(result.get('legacy_bonus',0))}** to the comprehension check." if int(result.get('legacy_bonus',0)) else ""
    await interaction.response.send_message(
        f"⚖️ **{result.get('name',definition['name'])}**\n{roll_line(roll)}{legacy_note}\n"
        f"Comprehension **+{int(result.get('gain',0))}%** → **{int(result.get('comprehension',0))}%**\n"
        f"Stage: **{result.get('stage_name','Unawakened')}**\nDao reconstruction +{int(result.get('dao_gain',0))}%."
    )

async def law_technique_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    needle=current.casefold().strip();out=[]
    for tid,d in WORLD.law_system.get('techniques',{}).items():
        name=str(d.get('name',tid))
        if not needle or needle in name.casefold() or needle in tid.casefold():out.append(app_commands.Choice(name=name[:100],value=tid[:100]))
    return out[:25]

@registered_group_command(law_group, name="technique",description="Use an unlocked Law technique in the current battle or scene")
@app_commands.autocomplete(technique=law_technique_autocomplete)
@serialized_user_action
async def law_technique_command(interaction:discord.Interaction,technique:str)->None:
    c=await require_character(interaction)
    if not c:return
    t=WORLD.law_technique(technique)
    if not t: await interaction.response.send_message("Unknown Law technique.",ephemeral=False);return
    rows=await DB.get_law_progress(interaction.user.id,str(t['law'])); comp=int(rows[0]['comprehension']) if rows else 0; stage=WORLD.law_stage(comp)
    if int(stage['index'])<int(t.get('requires_stage',1)) or int(c['realm_index'])<int(t.get('min_realm_index',0)):
        await interaction.response.send_message(f"You have not met the requirements for **{t['name']}**. Needed: Law stage {t.get('requires_stage')} and realm **{WORLD.realm_name(int(t.get('min_realm_index',0)))}**.",ephemeral=False);return
    if technique=='world_collapse':
        pw=await DB.get_personal_world(interaction.user.id)
        if not pw: await interaction.response.send_message("World Collapse requires a stabilized personal world.",ephemeral=False);return
    battle=await DB.get_active_battle(interaction.user.id)
    if technique in {'spatial_lockdown','spatial_strangulation'} and not battle:
        await interaction.response.send_message("That control technique currently requires an active battle target.",ephemeral=False);return
    if battle:
        result=await _execute_battle_law_technique(interaction.user.id,c,battle,technique)
        updated=await DB.get_battle(int(battle['battle_id']),user_id=interaction.user.id,active_only=True)
        if not updated:
            await interaction.response.send_message("⌛ This battle has already ended.",ephemeral=False);return
        c=await DB.get_character(interaction.user.id) or c
        embed,view=await _battle_panel(interaction.user.id,c,updated,result_text=result)
        await interaction.response.send_message(embed=embed,view=view);return
    effect_id=str(t.get('effect',''))
    effect=WORLD.special_effect(effect_id) if effect_id else None
    if effect:
        wt=await current_world_time(); payload=normalize_effect_payload({'effect_key':effect_id,'special':True,**effect}); await DB.apply_effect(interaction.user.id,effect_key=effect_id,name=str(effect['name']),source_type='law',source_id=technique,effect=payload,starts_game_minute=wt.total_minutes,duration_game_minutes=120)
    await interaction.response.send_message(f"🌌 **{t['name']}** manifests.\n{t.get('description','')}")

# ---------- Manuals / forbidden cultivation ----------
manual_group = app_commands.Group(name="manual", description="Study cultivation manuals and use learned techniques")


def _mastery_name(index:int)->str:
    levels=list(WORLD.technique_system.get("mastery_levels", ["Learned","Practiced","Proficient","Mastered","Perfected"]))
    return str(levels[max(0,min(len(levels)-1,int(index)))]) if levels else f"Mastery {index}"


async def manual_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    needle=current.casefold().strip(); inv=await DB.get_inventory(interaction.user.id); learned={str(r['manual_id']) for r in await DB.get_manuals(interaction.user.id)}; out=[]
    for mid,m in WORLD.manuals.items():
        iid=str(m.get('item_id',''))
        if int(inv.get(iid,0))<=0 and mid not in learned: continue
        name=str(m.get('name',mid))
        if not needle or needle in name.casefold() or needle in mid.casefold(): out.append(app_commands.Choice(name=name[:100],value=mid[:100]))
    return out[:25]


async def learned_technique_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    needle=current.casefold().strip(); rows={str(r['manual_id']):r for r in await DB.get_manuals(interaction.user.id)}; out=[]
    for tid,t in WORLD.techniques.items():
        row=rows.get(str(t.get('manual')))
        if not row or int(row.get('mastery',0))<int(t.get('min_mastery',0)): continue
        name=str(t.get('name',tid))
        if not needle or needle in name.casefold() or needle in tid.casefold(): out.append(app_commands.Choice(name=name[:100],value=tid[:100]))
    return out[:25]


@registered_group_command(manual_group, name="list",description="View cultivation manuals you have learned")
async def manual_list(interaction:discord.Interaction)->None:
    c=await require_character(interaction)
    if not c:return
    rows=await DB.get_manuals(interaction.user.id)
    if not rows:
        await interaction.response.send_message("You have not learned a cultivation manual yet. Acquire a manual item, then use **/cultivation → Manuals & Techniques → Study**.",ephemeral=False);return
    lines=[f"📚 **Cultivation Manuals — {c['name']}**"]
    for row in rows:
        m=WORLD.manual_definition(str(row['manual_id'])) or {}
        unlocked=[]
        for tid in m.get('techniques',[]):
            t=WORLD.technique_definition(str(tid)) or {}
            if int(row.get('mastery',0))>=int(t.get('min_mastery',0)): unlocked.append(str(t.get('name',tid)))
        lines.append(f"\n**{m.get('name',row['manual_id'])}** • {m.get('alignment','Unknown')} • {m.get('grade','Unknown')}\nMastery: **{_mastery_name(int(row.get('mastery',0)))}** • Practice {row.get('practice',0)}\nTechniques: {', '.join(unlocked) if unlocked else 'None unlocked'}")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(manual_group, name="study",description="Learn or practice a cultivation manual you possess")
@app_commands.autocomplete(manual=manual_autocomplete)
@serialized_user_action
async def manual_study(interaction:discord.Interaction,manual:str)->None:
    c=await require_character(interaction)
    if not c:return
    m=WORLD.manual_definition(manual)
    if not m:
        await interaction.response.send_message("Unknown cultivation manual.",ephemeral=False);return
    if int(c.get('realm_index',0))<int(m.get('min_realm_index',0)):
        await interaction.response.send_message(f"Your cultivation cannot safely comprehend **{m['name']}** yet. Required realm: **{WORLD.realm_name(int(m.get('min_realm_index',0)))}**.",ephemeral=False);return
    inv=await DB.get_inventory(interaction.user.id); iid=str(m.get('item_id',''))
    learned={str(r['manual_id']):r for r in await DB.get_manuals(interaction.user.id)}
    if manual not in learned and int(inv.get(iid,0))<=0:
        await interaction.response.send_message(f"You do not possess **{WORLD.item_name(iid)}**.",ephemeral=False);return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action(
            "manual.study",interaction.user.id,
            {"manual_id":manual,"cooldown_seconds":45*60},
            action_id=f"discord:{interaction.id}:manual.study",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(str(exc),ephemeral=False);return
    resolved=dict(envelope.get("result") or {});state=dict(resolved.get("state") or {})
    first=bool(resolved.get("first_study"));forbidden=bool(resolved.get("forbidden"));karma_note=""
    if first and forbidden:
        karma_note=f"\n☯️ Merely accepting the inheritance leaves a faint karmic stain: **{int(resolved.get('karma_score',0)):+d}**."
    unlocked=[]
    for tid in m.get('techniques',[]):
        t=WORLD.technique_definition(str(tid)) or {}
        if int(state.get('mastery',0))>=int(t.get('min_mastery',0)): unlocked.append(str(t.get('name',tid)))
    await interaction.response.send_message(f"📖 **{m['name']}**\n{m.get('description','')}\nMastery: **{_mastery_name(int(state.get('mastery',0)))}** • Practice {state.get('practice',0)}\nUnlocked: **{', '.join(unlocked) if unlocked else 'none yet'}**{karma_note}",ephemeral=False)


@registered_group_command(manual_group, name="technique",description="Use a learned manual technique in your active battle")
@app_commands.autocomplete(technique=learned_technique_autocomplete)
@serialized_user_action
async def manual_technique(interaction:discord.Interaction,technique:str)->None:
    c=await require_character(interaction)
    if not c:return
    t=WORLD.technique_definition(technique)
    if not t:
        await interaction.response.send_message("Unknown manual technique.",ephemeral=False);return
    rows={str(r['manual_id']):r for r in await DB.get_manuals(interaction.user.id)}; row=rows.get(str(t.get('manual')))
    if not row or int(row.get('mastery',0))<int(t.get('min_mastery',0)):
        await interaction.response.send_message(f"You have not mastered **{t['name']}** enough to use it.",ephemeral=False);return
    battle=await DB.get_active_battle(interaction.user.id)
    if not battle:
        await interaction.response.send_message("That technique currently requires an active battle target.",ephemeral=False);return
    if int(battle.get('npc_hp',0))<=0:
        await interaction.response.send_message("The opponent is already defeated. Choose **Spare** or **Kill**.",ephemeral=False);return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action(
            "manual.technique",interaction.user.id,
            {"technique_id":technique},
            action_id=f"discord:{interaction.id}:manual.technique",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(str(exc),ephemeral=False);return
    resolved=dict(envelope.get("result") or {})
    qi_cost=int(resolved.get('qi_cost',0));vit_cost=int(resolved.get('vitality_cost',0));damage=int(resolved.get('damage',0));heal=int(resolved.get('heal',0));suppress=int(resolved.get('suppress_turns',0));nhp=int(resolved.get('npc_hp',0))
    crime=dict(resolved.get('crime') or {});social_note=""
    if crime:
        social_note=f"⚖️ Crime record **#{crime.get('crime_id')}** opened with **{crime.get('evidence',0)}% evidence**."
        if crime.get('bounty_id') is not None: social_note += " A bounty was issued."
    updated=await DB.get_battle(int(battle['battle_id']),user_id=interaction.user.id,active_only=True) or {**battle,'npc_hp':nhp}
    c=await DB.get_character(interaction.user.id) or c
    impacts=list(resolved.get('impacts') or [])
    result=[f"🌑 **{t['name']}** — {t.get('description','')}",f"Cost: **{qi_cost} Qi**"+(f" + **{vit_cost} Vitality**" if vit_cost else "")]
    if damage: result.append(f"💥 Damage: **{damage}** • Opponent Vitality: **{nhp}**")
    if heal: result.append(f"🩸 Forced recovery: **+{heal} Vitality**")
    if suppress: result.append(f"⛓️ Suppression: **{suppress} turn(s)**")
    if bool(resolved.get('forbidden')):
        result.append(f"☯️ Karma: **{int(resolved.get('karma_score',0)):+d}**")
        result.append("👁️ The forbidden art was **witnessed**." if bool(resolved.get('witnessed')) else "🌫️ The forbidden art was mostly **concealed**.")
    if impacts: result.extend(["🌍 **World reaction:**",*[f"• {x}" for x in impacts[:6]]])
    if social_note: result.append(social_note)
    if nhp<=0: result.append("🏆 **Opponent defeated.** You must still choose **Spare** or **Kill**.")
    embed,view=await _battle_panel(interaction.user.id,c,updated,result_text="\n".join(result))
    await interaction.response.send_message(embed=embed,view=view)


@registered_root_command(name="worldrules",description="Show the laws governing NPC, sect, family and forbidden-art reactions",guild=GUILD)
async def world_rules_command(interaction:discord.Interaction)->None:
    rules=WORLD.world_rules
    lines=["⚖️ **Living World Rules**",str(rules.get('forbidden_arts',{}).get('description','Forbidden arts carry social and karmic consequences.'))]
    for title,key in (("NPC rules","npc_principles"),("Sect rules","sect_principles"),("Family rules","family_principles")):
        lines.append(f"\n**{title}**")
        lines.extend(f"• {x}" for x in rules.get(key,[]))
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


# ---------- Persistent injuries / deviations ----------
condition_group = app_commands.Group(name="condition", description="Inspect and treat persistent injuries, poisons and cultivation deviations")


async def condition_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    needle = current.casefold().strip()
    rows = await DB.get_conditions(interaction.user.id)
    out: list[app_commands.Choice[str]] = []
    for row in rows:
        name = str(row.get("name") or row.get("condition_key"))
        key = str(row.get("condition_key"))
        if not needle or needle in name.casefold() or needle in key.casefold():
            out.append(app_commands.Choice(name=f"{name} (Severity {row.get('severity',1)})"[:100], value=key[:100]))
    return out[:25]


@registered_group_command(condition_group, name="status", description="View persistent injuries, poisons, qi deviation and heart demons")
async def condition_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    rows = await DB.get_conditions(interaction.user.id)
    if not rows:
        await interaction.response.send_message("🌿 Your body, meridians, dantian and soul have no recorded persistent conditions.", ephemeral=False)
        return
    lines = [f"🩺 **Persistent Conditions — {c['name']}**"]
    for row in rows:
        definition = condition_definition(str(row["condition_key"]))
        item = str(definition.get("treatment_item", "recovery_pill"))
        lines.append(
            f"\n**{row['name']}** • {row['category']} • Severity **{row['severity']}/5**\n"
            f"{definition.get('description','')}\nTreatment resource: **{WORLD.item_name(item)}**"
        )
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(condition_group, name="treat", description="Attempt treatment for a persistent condition")
@app_commands.autocomplete(condition=condition_autocomplete)
@serialized_user_action
async def condition_treat(interaction: discord.Interaction, condition: str) -> None:
    c = await require_character(interaction)
    if not c:
        return
    _, _, wt = await current_effect_modifiers(interaction.user.id)
    try:
        envelope = await ENGINE.authoritative_action(
            "condition.treat", interaction.user.id,
            {"condition": condition},
            action_id=f"discord:{interaction.id}:condition.treat:{condition}",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"Condition treatment could not resolve: {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    roll = SimpleNamespace(**dict(result.get("roll") or {}))
    if bool(result.get("success")):
        outcome = ("The condition is **resolved** and its mechanical penalties are removed."
                   if bool(result.get("resolved")) else f"Severity falls to **{int(result.get('severity_after',0))}/5**.")
    else:
        outcome = "The treatment fails. The medicine is consumed, but the condition does not worsen."
    await interaction.response.send_message(
        f"🩺 **Treat {result.get('name', condition)}**\n{roll_line(roll)}\n{outcome}", ephemeral=False,
    )


# ---------- Explicit heavenly tribulations / ascension gates ----------
tribulation_group = app_commands.Group(name="tribulation", description="Prepare for and survive world-crossing heavenly tribulations")


def _tribulation_currency(world_name: str) -> str:
    return {
        "Mortal World": "low_spirit_stone",
        "Spiritual World": "low_spirit_crystal",
        "Immortal World": "low_immortal_stone",
        "Celestial World": "low_celestial_crystal",
    }.get(str(world_name), "low_spirit_stone")


def _eligible_ascension_gate(c: dict) -> dict | None:
    qi_index=int(c.get("realm_index",-1)); qi_phase=int(c.get("phase",1))
    body_index=int(c.get("body_realm_index",-1)); body_phase=int(c.get("body_phase",1))
    if qi_phase==9 and ascension_gate(qi_index):
        return {**ascension_gate(qi_index),"gate_realm_index":qi_index,"path":"Qi"}
    if body_phase==9 and ascension_gate(body_index):
        return {**ascension_gate(body_index),"gate_realm_index":body_index,"path":"Body"}
    return None


@registered_group_command(tribulation_group, name="status", description="Inspect your current ascension-gate tribulation state")
async def tribulation_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    gate = _eligible_ascension_gate(c)
    if not gate:
        await interaction.response.send_message(
            "⚡ No world-crossing tribulation is currently at your cultivation threshold. These gates occur at the peak of Ascension Realm, Transcendence Realm and Immortal Sovereign.",
            ephemeral=False,
        )
        return
    state = await DB.get_tribulation_state(interaction.user.id, int(gate["gate_realm_index"])) or {}
    cleared = bool(int(state.get("cleared", 0)))
    await interaction.response.send_message(
        f"⚡ **{gate['name']}**\n{gate['from_world']} → **{gate['to_world']}**\n"
        f"Path: **{gate['path']}** • Preparation: **{int(state.get('preparation',0))}/5** • Attempts: **{int(state.get('attempts',0))}**\n"
        f"Clearance: **{'CLEARED — the cross-world breakthrough is unlocked' if cleared else 'NOT CLEARED'}**",
        ephemeral=False,
    )


@registered_group_command(tribulation_group, name="prepare", description="Spend realm-appropriate currency to prepare a tribulation defense")
@serialized_user_action
async def tribulation_prepare(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    try:
        envelope = await ENGINE.authoritative_action(
            "tribulation.prepare", interaction.user.id, {},
            action_id=f"discord:{interaction.id}:tribulation.prepare",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"Tribulation preparation could not resolve: {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    currency = str(result.get("currency", "low_spirit_stone"))
    await interaction.response.send_message(
        f"⚡ Tribulation preparation rises to **{int(result.get('preparation',0))}/5**. Remaining {WORLD.currency_name(currency)}: **{int(result.get('balance',0))}**.",
        ephemeral=False,
    )


@registered_group_command(tribulation_group, name="attempt", description="Face the three-wave heavenly tribulation at your current world gate")
@serialized_user_action
async def tribulation_attempt(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    _, _, wt = await current_effect_modifiers(interaction.user.id)
    try:
        envelope = await ENGINE.authoritative_action(
            "tribulation.attempt", interaction.user.id, {},
            action_id=f"discord:{interaction.id}:tribulation.attempt",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"Tribulation attempt could not resolve: {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    lines = [f"⚡ **{result.get('gate_name','Heavenly Tribulation')}** • Preparation **{int(result.get('preparation_used',0))}/5**"]
    for wave in result.get("waves", []):
        roll = SimpleNamespace(**dict(wave))
        lines.append(f"\n**{wave.get('name','Tribulation Wave')}:** {roll_line(roll)}")
        condition = wave.get("condition") or {}
        if condition:
            lines.append(f"↳ Persistent consequence: **{condition.get('name', condition.get('condition_key','Condition'))}**, severity **{int(condition.get('severity',1))}/5**.")
    if bool(result.get("success")):
        lines.append(
            f"\n🌌 **Tribulation cleared.** Heaven accepts your claim to enter **{result.get('to_world','the higher world')}**. "
            f"Your next world-crossing **{'/breakthrough' if result.get('path')=='Qi' else '/body breakthrough'}** may proceed."
        )
        if int(result.get("fate_after", -1)) >= 0:
            lines.append(f"🌠 Heaven leaves providence in your wake: **Fate {int(result.get('fate_after',0))}/9**.")
    else:
        lines.append("\n💥 **Tribulation failed.** Preparation is consumed. Heal persistent injuries and prepare before trying again.")
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


# ---------- Profession progression ----------
profession_group = app_commands.Group(name="profession", description="Track cultivation-profession mastery independent from realm")


@registered_group_command(profession_group, name="status", description="View your crafting and support-profession mastery")
async def profession_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    rows = await DB.get_profession_progress(interaction.user.id)
    if not rows:
        await interaction.response.send_message(
            "🛠️ You have no profession experience yet. Successful or failed **/craft** attempts now build profession mastery.", ephemeral=False
        )
        return
    lines = [f"🛠️ **Profession Mastery — {c['name']}**"]
    for row in rows:
        level = int(row.get("level", 0)); xp = int(row.get("xp", 0))
        lines.append(
            f"\n**{row['profession']} — {profession_rank(level)}** (Level {level})\n"
            f"XP **{xp}/{profession_xp_needed(level)}** • Successes {row.get('successes',0)} • Failures {row.get('failures',0)} • Quality {row.get('quality_points',0)}"
        )
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


# ---------- Reputation / crime / witnesses / bounties / grudges ----------
crime_group = app_commands.Group(name="crime", description="Inspect jurisdictional crimes and evidence recorded against your incarnation")


@registered_root_command(name="reputation", description="View persistent faction and social reputation", guild=GUILD)
async def reputation_command(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    rows = await DB.get_reputations(interaction.user.id)
    if not rows:
        await interaction.response.send_message("📜 No faction has formed a strong recorded opinion of this incarnation yet.", ephemeral=False)
        return
    lines = [f"📜 **Reputation — {c['name']}**"]
    for row in rows:
        score = int(row.get("score", 0))
        label = "Revered" if score >= 70 else "Trusted" if score >= 30 else "Known" if score > -30 else "Distrusted" if score > -70 else "Hated"
        lines.append(f"\n**{row['faction_key']}**: **{score:+d}** ({label})" + (f"\nLast cause: {row.get('last_reason')}" if row.get('last_reason') else ""))
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(crime_group, name="status", description="View open crimes, evidence and whether they generated bounties")
async def crime_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    crimes = await DB.get_crimes(interaction.user.id)
    if not crimes:
        await interaction.response.send_message("⚖️ No open jurisdictional crime record exists for this incarnation.", ephemeral=False)
        return
    lines = [f"⚖️ **Open Crime Records — {c['name']}**"]
    for row in crimes:
        witnesses = await DB.get_witnesses(int(row["crime_id"]))
        lines.append(
            f"\n`#{row['crime_id']}` **{row['crime_type'].replace('_',' ').title()}** • {row['jurisdiction']}\n"
            f"Severity **{row['severity']}/10** • Evidence **{row['evidence']}%** • Witness records **{len(witnesses)}**\n{row.get('description','')}"
        )
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(crime_group, name="atone", description="Pay local restitution to resolve one open crime and its bounty")
@serialized_user_action
async def crime_atone(interaction: discord.Interaction, crime_id: int) -> None:
    c=await require_character(interaction)
    if not c:return
    crimes=await DB.get_crimes(interaction.user.id)
    row=next((x for x in crimes if int(x.get("crime_id",0))==int(crime_id)),None)
    if not row:
        await interaction.response.send_message("That open crime record does not exist.",ephemeral=False);return
    if str(c.get("location"))!=str(row.get("jurisdiction")):
        await interaction.response.send_message(
            f"You must return to **{row.get('jurisdiction')}** to negotiate restitution for this jurisdictional record.",ephemeral=False
        );return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action(
            "crime.atone",interaction.user.id,
            {"crime_id":int(crime_id)},
            action_id=f"discord:{interaction.id}:crime.atone",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(str(exc),ephemeral=False);return
    resolved=dict(envelope.get("result") or {})
    fine=int(resolved.get("fine",0));currency=str(resolved.get("currency",''));balance=int(resolved.get("balance",0))
    await interaction.response.send_message(
        f"⚖️ Crime **#{crime_id}** is marked **atoned** and any bounty sourced only from it is resolved. "
        f"Paid **{fine} {WORLD.currency_name(currency)}** • remaining balance **{balance}**.",ephemeral=False
    )


@registered_root_command(name="bounty", description="View active capture/death bounties attached to your incarnation", guild=GUILD)
async def bounty_command(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    rows = await DB.get_bounties(interaction.user.id)
    if not rows:
        await interaction.response.send_message("🎯 No active bounty is recorded against you.", ephemeral=False)
        return
    lines = [f"🎯 **Active Bounties — {c['name']}**"]
    for row in rows:
        lines.append(f"\n**{row['jurisdiction']}** • **{int(row['amount']):,}** local-value bounty\n{row.get('reason','')}")
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_root_command(name="grudges", description="View persistent personal, family and faction grudges against you", guild=GUILD)
async def grudges_command(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    rows = await DB.get_grudges(interaction.user.id)
    if not rows:
        await interaction.response.send_message("🗡️ No active grudge record is attached to this incarnation.", ephemeral=False)
        return
    lines = [f"🗡️ **Active Grudges — {c['name']}**"]
    for row in rows:
        lines.append(f"\n**{row['holder_key']}** ({row['holder_type']}) • Intensity **{row['intensity']}/10**\n{row.get('reason','')}")
    await reply_long(interaction, "\n".join(lines), ephemeral=False)



# ---------- Advanced branch forward-port: beasts, artifacts, territory, caravans, parties, PvP, social state ----------
beast_group = app_commands.Group(name="beast", description="Manage contracted spirit beasts and active companions")
artifact_group = app_commands.Group(name="artifact", description="Bond, awaken and inspect personal artifacts")
territory_group = app_commands.Group(name="territory", description="Inspect or contest persistent territory control")
war_group = app_commands.Group(name="war", description="Inspect active sect and territory conflicts")
caravan_group = app_commands.Group(name="caravan", description="Dispatch and inspect persistent trade caravans")
party_group = app_commands.Group(name="party", description="Create voluntary cultivation parties")
duel_group = app_commands.Group(name="duel", description="Consent-gated nonlethal player-versus-player duels")
equipment_group = app_commands.Group(name="equipment", description="Bind, equip, repair and inspect durable combat equipment")
formation_group = app_commands.Group(name="formation", description="Assign party combat positions and formation stances")
boss_group = app_commands.Group(name="boss", description="Run persistent multi-phase party boss encounters")
hunter_group = app_commands.Group(name="hunter", description="Respond to autonomous bounty-hunter pursuits")



@registered_group_command(equipment_group, name="status", description="Inspect your persistent equipment loadout and durability")
async def equipment_status(interaction: discord.Interaction) -> None:
    c=await require_character(interaction)
    if not c:return
    rows=await DB.get_equipment(interaction.user.id)
    if not rows:
        await interaction.response.send_message("🛡️ No equipment has been bound yet. Use **/items → Equipment → Bind** on a supported carried item.",ephemeral=False);return
    bonus=await DB.equipment_bonus(interaction.user.id)
    lines=[f"🛡️ **Equipment — {c['name']}**",f"Active bonuses: ATK **+{bonus['attack']}** • DEF **+{bonus['defense']}** • Spirit **+{bonus['spirit']}** • Agility **+{bonus['agility']}**"]
    for row in rows:
        definition=EQUIPMENT_DEFINITIONS.get(str(row['item_id']),{})
        lines.append(f"\n{'✅' if row['equipped'] else '▫️'} `#{row['equipment_id']}` **{definition.get('name',WORLD.item_name(row['item_id']))}** • {row['slot']} • durability **{row['durability']}/{row['max_durability']}** • quality {row['quality']}%")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(equipment_group, name="bind", description="Convert one carried equipment item into a persistent durable instance")
@serialized_user_action
async def equipment_bind(interaction: discord.Interaction, item: str) -> None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("equipment.bind",interaction.user.id,{"item_id":item},action_id=f"discord:{interaction.id}:equipment.bind")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🧷 Bound **{WORLD.item_name(item)}** as equipment `#{result.get('equipment_id')}`.",ephemeral=False)


@registered_group_command(equipment_group, name="equip", description="Equip a bound item; another item in the same slot is automatically unequipped")
@serialized_user_action
async def equipment_equip(interaction: discord.Interaction, equipment_id: int) -> None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("equipment.equip",interaction.user.id,{"id":int(equipment_id)},action_id=f"discord:{interaction.id}:equipment.equip")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"⚔️ Equipped item `#{equipment_id}`.",ephemeral=False)


@registered_group_command(equipment_group, name="unequip", description="Remove a bound item from your active combat loadout")
@serialized_user_action
async def equipment_unequip(interaction: discord.Interaction, equipment_id: int) -> None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("equipment.unequip",interaction.user.id,{"id":int(equipment_id)},action_id=f"discord:{interaction.id}:equipment.unequip")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🎒 Unequipped item `#{equipment_id}`.",ephemeral=False)


@registered_group_command(equipment_group, name="repair", description="Restore equipment durability using Spirit Iron")
@serialized_user_action
async def equipment_repair(interaction: discord.Interaction, equipment_id: int) -> None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("equipment.repair",interaction.user.id,{"id":int(equipment_id)},action_id=f"discord:{interaction.id}:equipment.repair")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🔧 Repaired item `#{equipment_id}` for **{int(result.get('repair_cost',0))} Spirit Iron**.",ephemeral=False)


@registered_group_command(formation_group, name="create", description="Create a named combat formation for your active party")
@serialized_user_action
async def formation_create(interaction: discord.Interaction, name: str) -> None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("formation.create",interaction.user.id,{"name":name},action_id=f"discord:{interaction.id}:formation.create")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🧿 Formation `#{result.get('formation_id')}` created.",ephemeral=False)


@registered_group_command(formation_group, name="assign", description="Assign one party member to Vanguard, Core, Flank or Support")
@app_commands.choices(position=[app_commands.Choice(name=x.title(),value=x) for x in FORMATION_POSITIONS])
@serialized_user_action
async def formation_assign(interaction: discord.Interaction, formation_id: int, member: discord.Member, position: app_commands.Choice[str]) -> None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("formation.assign",interaction.user.id,{"formation_id":int(formation_id),"target_user_id":member.id,"position":position.value},action_id=f"discord:{interaction.id}:formation.assign")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🧿 Assigned {member.mention} to **{position.value}**.",ephemeral=False)


@registered_group_command(formation_group, name="activate", description="Activate a formation and choose its combat stance")
@app_commands.choices(stance=[app_commands.Choice(name=x.title(),value=x) for x in FORMATION_STANCES])
@serialized_user_action
async def formation_activate(interaction: discord.Interaction, formation_id: int, stance: app_commands.Choice[str]) -> None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("formation.activate",interaction.user.id,{"formation_id":int(formation_id),"stance":stance.value},action_id=f"discord:{interaction.id}:formation.activate")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🧿 Formation `#{formation_id}` activated in **{stance.value}** stance.",ephemeral=False)


@registered_group_command(formation_group, name="stance", description="Change the active formation stance between combat rounds")
@app_commands.choices(stance=[app_commands.Choice(name=x.title(),value=x) for x in FORMATION_STANCES])
@serialized_user_action
async def formation_stance(interaction: discord.Interaction, stance: app_commands.Choice[str]) -> None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("formation.stance",interaction.user.id,{"stance":stance.value},action_id=f"discord:{interaction.id}:formation.stance")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🧿 Active formation stance changed to **{stance.value}**.",ephemeral=False)


@registered_group_command(formation_group, name="status", description="Inspect your party formation, positions and remaining cohesion")
async def formation_status(interaction: discord.Interaction) -> None:
    c=await require_character(interaction)
    if not c:return
    party=await DB.get_party(interaction.user.id)
    if not party:
        await interaction.response.send_message("You are not in an active party.",ephemeral=False);return
    rows=await DB.get_formations(int(party['party_id']))
    if not rows:
        await interaction.response.send_message("Your party has no formations.",ephemeral=False);return
    lines=[f"☯️ **Party Formations — {party['name']}**"]
    for f in rows:
        assignments=", ".join(f"{p['position'].title()}: <@{p['user_id']}>" for p in f['positions']) or "unassigned"
        lines.append(f"\n{'⭐' if f['active'] else '▫️'} `#{f['formation_id']}` **{f['name']}** • {f['stance']} • cohesion **{f['cohesion']}%**\n{assignments}")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(boss_group, name="list", description="List persistent multi-phase bosses and their required locations")
async def boss_list(interaction: discord.Interaction) -> None:
    c=await require_character(interaction)
    if not c:return
    lines=["👹 **Boss Encounters**"]
    for key,b in BOSS_TEMPLATES.items():
        lines.append(f"\n`{key}` — **{b['name']}** • {b['location']} • {len(b['phases'])} phases • base HP {b['max_hp']}")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(boss_group, name="start", description="Party leader starts a persistent multi-phase boss encounter")
@serialized_user_action
async def boss_start(interaction: discord.Interaction, boss: str) -> None:
    if not await require_character(interaction):return
    wt=await current_world_time()
    try:
        e=await ENGINE.authoritative_action("boss.start",interaction.user.id,{"template_key":boss},action_id=f"discord:{interaction.id}:boss.start"); r=dict(e.get('result') or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False);return
    await interaction.response.send_message(f"👹 **Boss Encounter #{r.get('encounter_id')} — {r.get('boss_name','Boss')}** begins with **{r.get('boss_hp',0)}/{r.get('boss_hp_max',0)} HP**.",ephemeral=False)


@registered_group_command(boss_group, name="status", description="View the active party boss phase, HP, raid vitality and round")
async def boss_status(interaction: discord.Interaction) -> None:
    c=await require_character(interaction)
    if not c:return
    enc=await DB.get_boss_encounter(user_id=interaction.user.id)
    if not enc:
        await interaction.response.send_message("Your party has no active boss encounter.",ephemeral=False);return
    lines=[f"👹 **#{enc['encounter_id']} {enc['boss_name']}** • Round **{enc['round_index']}**",f"Boss HP **{enc['boss_hp']}/{enc['boss_hp_max']}** • Phase **{enc['phase'].get('name','Unknown')}**"]
    for p in enc['participants']:
        lines.append(f"• <@{p['user_id']}> — **{p['vitality']}/{p['vitality_max']}** • {p['status']} • damage {p['total_damage']}")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(boss_group, name="act", description="Take your once-per-round raid action; boss retaliates after the full party acts")
@app_commands.choices(style=[app_commands.Choice(name="Attack",value="attack"),app_commands.Choice(name="Technique",value="technique"),app_commands.Choice(name="Defend",value="defend"),app_commands.Choice(name="Support",value="support")])
@serialized_user_action
async def boss_act(interaction: discord.Interaction, style: app_commands.Choice[str]) -> None:
    if not await require_character(interaction):return
    enc=await DB.get_boss_encounter(user_id=interaction.user.id)
    if not enc:
        await interaction.response.send_message("Your party has no active boss encounter.",ephemeral=False);return
    wt=await current_world_time()
    try:
        e=await ENGINE.authoritative_action("boss.act",interaction.user.id,{"encounter_id":int(enc['encounter_id']),"style":style.value,"version":int(enc['version'])},action_id=f"discord:{interaction.id}:boss.act"); r=dict(e.get('result') or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False);return
    events="\n".join(f"• {x}" for x in list(r.get('events') or [])[:12])
    await interaction.response.send_message(f"👹 **Boss #{r.get('encounter_id',enc['encounter_id'])}** • {r.get('status','active')} • Round {r.get('round_index','?')} • HP **{r.get('boss_hp','?')}/{r.get('boss_hp_max','?')}**\n{events}",ephemeral=False)


@registered_group_command(boss_group, name="claim", description="Claim your reward from a completed boss encounter")
@serialized_user_action
async def boss_claim(interaction: discord.Interaction, encounter_id: int) -> None:
    if not await require_character(interaction):return
    try:
        e=await ENGINE.authoritative_action("boss.claim",interaction.user.id,{"id":int(encounter_id)},action_id=f"discord:{interaction.id}:boss.claim"); r=dict(e.get('result') or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False);return
    await interaction.response.send_message(f"🏆 Claimed **{r.get('currency_amount',0)} Low Spirit Stones** and **{WORLD.item_name(str(r.get('item_id','')))} x{r.get('item_quantity',0)}**.",ephemeral=False)


@registered_group_command(hunter_group, name="status", description="View the autonomous bounty hunter currently tracking this incarnation")
async def hunter_status(interaction: discord.Interaction) -> None:
    if not await require_character(interaction):return
    p=await DB.get_bounty_hunter_pursuit(user_id=interaction.user.id)
    if not p:
        await interaction.response.send_message("🎯 No autonomous bounty hunter is actively pursuing you.",ephemeral=False);return
    await interaction.response.send_message(f"🎯 **Pursuit #{p['pursuit_id']} — {p['hunter_name']}**\nJurisdiction **{p['jurisdiction']}** • bounty **{p['amount']}** • hunter power **{p['hunter_power']}**\nStatus **{p['status']}** • pressure **{p['pressure']}%** • escape **{p['escape_progress']}%** • capture **{p['capture_progress']}%**",ephemeral=False)


@registered_group_command(hunter_group, name="act", description="Evade, fight or surrender to an active bounty hunter")
@app_commands.choices(action=[app_commands.Choice(name="Evade",value="evade"),app_commands.Choice(name="Fight",value="fight"),app_commands.Choice(name="Surrender",value="surrender")])
@serialized_user_action
async def hunter_act(interaction: discord.Interaction, pursuit_id: int, action: app_commands.Choice[str]) -> None:
    if not await require_character(interaction):return
    wt=await current_world_time()
    try:
        e=await ENGINE.authoritative_action("bounty_hunter.act",interaction.user.id,{"pursuit_id":int(pursuit_id),"action":action.value},action_id=f"discord:{interaction.id}:bounty_hunter.act"); row=dict(e.get('result') or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False);return
    await interaction.response.send_message(f"🎯 **{row.get('hunter_name','Hunter')}** • status **{row.get('status','active')}** • pressure {row.get('pressure',0)}% • escape {row.get('escape_progress',0)}% • capture {row.get('capture_progress',0)}%",ephemeral=False)


@registered_group_command(beast_group, name="status", description="View your contracted spirit beasts")
async def beast_status(interaction: discord.Interaction) -> None:
    c=await require_character(interaction)
    if not c:return
    rows=await DB.get_spirit_beasts(interaction.user.id)
    if not rows:
        await interaction.response.send_message("🐾 You have no spirit-beast contract. Beast Binders can earn equality-contract opportunities from overwhelming **/world → Hunt** successes.",ephemeral=False);return
    lines=[f"🐉 **Spirit Beasts — {c['name']}**"]
    for row in rows:
        lines.append(f"\n{'⭐ ' if row.get('active') else ''}**#{row['beast_id']} {row['name']}** ({row['species']})\nRank **{row['rank']}** • {row['element']} • Loyalty **{row['loyalty']}** • Evolution **{row['evolution_stage']}** • Contract **{row['contract_type']}**")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(beast_group, name="encounters", description="View subdued wild beasts currently available for taming")
async def beast_encounters(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    wt = await current_world_time()
    rows = await DB.get_wild_beast_encounters(
        interaction.user.id, game_minute=wt.total_minutes, location=str(c.get("location", "")),
    )
    if not rows:
        await interaction.response.send_message(
            "🐾 No subdued wild beast is waiting here. Strong **/world → Hunt** victories can create taming opportunities.",
            ephemeral=False,
        )
        return
    lines = [f"🪢 **Taming Opportunities — {c['location']}**"]
    for row in rows:
        remaining = max(0, int(row["expires_game_minute"]) - wt.total_minutes)
        lines.append(
            f"\n`#{row['encounter_id']}` **{row['species']}** • Rank {row['rank']} • {row['element']} • "
            f"{row['temperament']} • taming TN **{row['taming_tn']}** • leaves in **{remaining} game minutes**"
        )
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(beast_group, name="tame", description="Attempt a consensual spirit-beast bond with a subdued wild beast")
@serialized_user_action
async def beast_tame(interaction: discord.Interaction, encounter_id: int) -> None:
    if not await require_character(interaction):
        return
    try:
        envelope = await ENGINE.authoritative_action(
            "beast.tame",
            interaction.user.id,
            {"encounter_id": int(encounter_id)},
            action_id=f"discord:{interaction.id}:beast.tame",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(str(exc), ephemeral=False)
        return

    resolved = dict(envelope.get("result") or {})
    encounter = dict(resolved.get("encounter") or {})
    result = SimpleNamespace(**resolved)
    species = str(encounter.get("species") or "spirit beast")
    if bool(resolved.get("success")):
        beast = dict(resolved.get("beast") or {})
        beast_progress = dict(resolved.get("profession_progress") or {})
        active_line = " It becomes your active companion." if beast.get("active") else ""
        await interaction.response.send_message(
            f"🐉 **Spirit-Beast Bond — {species}**\n{roll_line(result)}\n"
            f"The beast accepts an **equality contract** at loyalty **{beast.get('loyalty', 30)}**.{active_line}\n"
            f"🪢 Beast Taming: **{profession_rank(int(beast_progress.get('level', 0)))}** Lv.{int(beast_progress.get('level', 0))}."
        )
    else:
        await interaction.response.send_message(
            f"🐾 **Taming Failed — {species}**\n{roll_line(result)}\n"
            "The beast rejects the bond and escapes. No contract is forced."
        )

@registered_group_command(beast_group, name="feed", description="Feed a contracted beast Spirit Herbs or Beast Cores to strengthen loyalty")
@app_commands.choices(food=[
    app_commands.Choice(name="Spirit Herb", value="spirit_herb"),
    app_commands.Choice(name="Low Beast Core", value="beast_core"),
])
@serialized_user_action
async def beast_feed(interaction: discord.Interaction, beast_id: int, food: app_commands.Choice[str]) -> None:
    if not await require_character(interaction):
        return
    try:
        envelope = await ENGINE.authoritative_action(
            "beast.feed",
            interaction.user.id,
            {"beast_id": int(beast_id), "food": food.value},
            action_id=f"discord:{interaction.id}:beast.feed",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(str(exc), ephemeral=False)
        return

    updated = dict(envelope.get("result") or {})
    await interaction.response.send_message(
        f"🐉 **{updated['name']}** accepts the **{WORLD.item_name(food.value)}**. "
        f"Loyalty rises to **{updated['loyalty']}** and intelligence to **{updated['intelligence']}**.",
        ephemeral=False,
    )

@registered_group_command(beast_group, name="train", description="Train a contracted beast to raise loyalty and intelligence")
@serialized_user_action
async def beast_train(interaction: discord.Interaction, beast_id: int) -> None:
    if not await require_character(interaction):
        return
    try:
        envelope = await ENGINE.authoritative_action(
            "beast.train",
            interaction.user.id,
            {"beast_id": int(beast_id)},
            action_id=f"discord:{interaction.id}:beast.train",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(str(exc), ephemeral=False)
        return

    resolved = dict(envelope.get("result") or {})
    row = dict(resolved.get("beast") or {})
    gain = int(resolved.get("gain", 0))
    beast_progress = dict(resolved.get("profession_progress") or {})
    pen_level = int(resolved.get("beast_pen_level", 0))
    context_bonus = int(resolved.get("context_bonus", 0))
    pen_note = f" • Spirit Beast Pen Lv.{pen_level} training bonus +{context_bonus}" if pen_level else ""
    await interaction.response.send_message(
        f"🐉 **{row['name']}** completes a training cycle. Loyalty **{row['loyalty']}**, "
        f"intelligence **{row['intelligence']}**.{pen_note}\n"
        f"🪢 Beast Taming: **{profession_rank(int(beast_progress.get('level', 0)))}** "
        f"Lv.{int(beast_progress.get('level', 0))}.",
        ephemeral=False,
    )

@registered_group_command(beast_group, name="evolve", description="Attempt a bloodline/evolution step once loyalty is sufficient")
@serialized_user_action
async def beast_evolve(interaction: discord.Interaction, beast_id: int) -> None:
    c=await require_character(interaction)
    if not c:return
    try:
        envelope=await ENGINE.authoritative_action("beast.evolve",interaction.user.id,{"beast_id":int(beast_id)},action_id=f"discord:{interaction.id}:beast.evolve")
    except GameEngineError as exc:
        await interaction.response.send_message(f"Evolution failed: {exc}",ephemeral=False);return
    resolved=dict(envelope.get("result") or {});row=dict(resolved.get("beast") or {});beast_progress=dict(resolved.get("profession_progress") or {})
    await interaction.response.send_message(f"🧬 **{row['name']} evolves.** Evolution Stage **{row['evolution_stage']}**, Rank **{row['rank']}**. The strain reduces loyalty to **{row['loyalty']}**.\n🪢 Beast Taming: **{profession_rank(int(beast_progress.get('level',0)))}** Lv.{int(beast_progress.get('level',0))}.",ephemeral=False)


@registered_group_command(beast_group, name="active", description="Choose the spirit beast that supports you in battle")
@serialized_user_action
async def beast_active(interaction: discord.Interaction, beast_id: int) -> None:
    c=await require_character(interaction)
    if not c:return
    try:
        await ENGINE.authoritative_action("beast.active",interaction.user.id,{"beast_id":int(beast_id)},action_id=f"discord:{interaction.id}:beast.active")
    except GameEngineError as exc:
        await interaction.response.send_message(str(exc),ephemeral=False);return
    await interaction.response.send_message("🐉 Active companion changed. Its rank, evolution and loyalty now contribute to normal battle exchanges.",ephemeral=False)


@registered_group_command(artifact_group, name="status", description="View artifacts bonded to this incarnation")
async def artifact_status(interaction: discord.Interaction) -> None:
    c=await require_character(interaction)
    if not c:return
    rows=await DB.get_artifact_bonds(interaction.user.id)
    if not rows:
        await interaction.response.send_message("🗡️ You have not formed a persistent artifact bond yet.",ephemeral=False);return
    lines=[f"🗡️ **Artifact Bonds — {c['name']}**"]
    for row in rows:
        spirit=f" • Spirit **{row['spirit_name']}**" if row.get('spirit_name') else ""
        lines.append(f"\n**{WORLD.item_name(row['item_id'])}** • Bond {row['bond_level']}/10 • Resonance **{row['resonance']}%** • {'Awakened' if row['awakened'] else 'Dormant'}{spirit}")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(artifact_group, name="bond", description="Deepen a bond with a carried item")
@serialized_user_action
async def artifact_bond(interaction: discord.Interaction, item: str) -> None:
    if not await require_character(interaction):
        return
    try:
        envelope = await ENGINE.authoritative_action(
            "artifact.bond",
            interaction.user.id,
            {"item_id": item},
            action_id=f"discord:{interaction.id}:artifact.bond",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(str(exc), ephemeral=False)
        return

    resolved = dict(envelope.get("result") or {})
    row = dict(resolved.get("artifact") or {})
    art_progress = dict(resolved.get("profession_progress") or {})
    await interaction.response.send_message(
        f"🗡️ Bond with **{WORLD.item_name(item)}** deepens to **{row['bond_level']}**. "
        f"Resonance **{row['resonance']}%**.\n"
        f"🔨 Artifact Refining: **{profession_rank(int(art_progress.get('level', 0)))}** "
        f"Lv.{int(art_progress.get('level', 0))}.",
        ephemeral=False,
    )

@registered_group_command(artifact_group, name="awaken", description="Awaken a sufficiently bonded artifact spirit")
@serialized_user_action
async def artifact_awaken(interaction: discord.Interaction, item: str, spirit_name: str) -> None:
    c=await require_character(interaction)
    if not c:return
    try:
        envelope=await ENGINE.authoritative_action("artifact.awaken",interaction.user.id,{"item_id":item,"spirit_name":spirit_name},action_id=f"discord:{interaction.id}:artifact.awaken")
    except GameEngineError as exc:
        await interaction.response.send_message(str(exc),ephemeral=False);return
    resolved=dict(envelope.get("result") or {});row=dict(resolved.get("artifact") or {});art_progress=dict(resolved.get("profession_progress") or {})
    await interaction.response.send_message(f"✨ **{WORLD.item_name(item)} awakens.** Its spirit answers to **{row['spirit_name']}** at **{row['resonance']}% resonance**. Awakened artifacts contribute to battle checks.\n🔮 Artifact Refining: **{profession_rank(int(art_progress.get('level',0)))}** Lv.{int(art_progress.get('level',0))}.",ephemeral=False)


@registered_group_command(territory_group, name="status", description="Inspect persistent control and resource state at your location")
async def territory_status(interaction: discord.Interaction) -> None:
    c=await require_character(interaction)
    if not c:return
    rows=await DB.get_territories(str(c.get('location')))
    if not rows:
        await interaction.response.send_message("No persistent territory node exists here.",ephemeral=False);return
    t=rows[0]
    await interaction.response.send_message(
        f"🏯 **{t['name']}**\nController: **{t['controller_type']}:{t['controller_key'] or 'unclaimed'}** • Resource **{t['resource_type']}**\nProsperity **{t['prosperity']}** • Defense **{t['defense']}** • Unrest **{t['unrest']}**",ephemeral=False)


@registered_group_command(territory_group, name="claim", description="Claim an unheld territory for your sect or begin a territorial war")
@serialized_user_action
async def territory_claim(interaction: discord.Interaction) -> None:
    c=await require_character(interaction)
    if not c:return
    rows=await DB.get_territories(str(c.get('location')))
    if not rows:
        await interaction.response.send_message("No claimable territory node exists here.",ephemeral=False);return
    t=rows[0]; wt=await current_world_time()
    try:
        e=await ENGINE.authoritative_action("territory.claim",interaction.user.id,{"territory_key":str(t['territory_key'])},action_id=f"discord:{interaction.id}:territory.claim"); r=dict(e.get('result') or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False);return
    if r.get('war_id'): text=f"⚔️ **Territorial War #{r['war_id']}** begins for **{t['name']}**."
    else: text=f"🏯 **{r.get('sect_name','Your sect')}** establishes a recognized claim over **{t['name']}**."
    await interaction.response.send_message(text,ephemeral=False)


@registered_group_command(war_group, name="status", description="View siege, armies, morale and occupation state for territorial wars")
async def war_status(interaction: discord.Interaction) -> None:
    c=await require_character(interaction)
    if not c:return
    rows=await DB.get_territory_wars(active_only=False)
    if not rows:
        await interaction.response.send_message("⚔️ No formal territorial war has been recorded.",ephemeral=False);return
    lines=["⚔️ **Territory Wars**"]
    for w in rows[:20]:
        op=w.get('operations') or {}
        occupation=f" • occupation until {op.get('occupation_until_game_minute')}" if int(op.get('occupation_until_game_minute') or 0)>0 else ""
        lines.append(f"\n`#{w['war_id']}` **{w['attacker_key']}** vs **{w['defender_key']}** for **{w['territory_key']}** • **{w['status']}**\nSiege **{op.get('siege_progress',0)}%** • morale A/D **{op.get('attacker_morale',100)}/{op.get('defender_morale',100)}** • forces A/D **{op.get('attacker_force',0)}/{op.get('defender_force',0)}**{occupation}"+(f" • winner **{op.get('winner_key')}**" if op.get('winner_key') else ""))
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(war_group, name="act", description="Contribute cultivation power to a siege, defense, sabotage or counterattack")
@app_commands.choices(tactic=[app_commands.Choice(name=x.title(),value=x) for x in ("assault","siege","sabotage","fortify","repel")])
@serialized_user_action
async def war_act(interaction: discord.Interaction, war_id: int, tactic: app_commands.Choice[str]) -> None:
    if not await require_character(interaction):return
    wt=await current_world_time()
    try:
        e=await ENGINE.authoritative_action("war.act",interaction.user.id,{"war_id":int(war_id),"tactic":tactic.value},action_id=f"discord:{interaction.id}:war.act"); r=dict(e.get('result') or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False);return
    op=dict(r.get('operations') or r)
    text=f"⚔️ **War #{war_id} — {tactic.name}**\nSiege **{op.get('siege_progress',0)}%** • morale A/D **{op.get('attacker_morale',100)}/{op.get('defender_morale',100)}** • forces A/D **{op.get('attacker_force',0)}/{op.get('defender_force',0)}**"
    if r.get('status') and r.get('status')!='active': text+=f"\n🏯 War resolved: **{op.get('winner_key','unknown')}**."
    await interaction.response.send_message(text,ephemeral=False)


@registered_group_command(caravan_group, name="dispatch", description="Send goods with optional escorts, smuggling and destination tax exposure")
@app_commands.autocomplete(destination=location_autocomplete)
@serialized_user_action
async def caravan_dispatch(interaction: discord.Interaction, destination: str, item: str, quantity: app_commands.Range[int,1,50]=1, escort: app_commands.Range[int,0,20]=0, smuggle: bool=False) -> None:
    c=await require_character(interaction)
    if not c:return
    wt=await current_world_time()
    try:
        e=await ENGINE.authoritative_action("caravan.dispatch",interaction.user.id,{"destination":destination,"item_id":item,"quantity":int(quantity),"escort":int(escort),"smuggle":bool(smuggle)},action_id=f"discord:{interaction.id}:caravan.dispatch"); r=dict(e.get('result') or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False);return
    await interaction.response.send_message(f"🐫 Caravan **#{r.get('caravan_id')}** dispatched to **{destination}** carrying **{WORLD.item_name(item)} x{quantity}**.",ephemeral=False)


@registered_group_command(caravan_group, name="status", description="Settle due caravans and inspect escorts, interception, smuggling, tax and losses")
async def caravan_status(interaction: discord.Interaction) -> None:
    c=await require_character(interaction)
    if not c:return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action(
            "caravan.settle", interaction.user.id, {},
            action_id=f"discord:{interaction.id}:caravan.settle",
        )
        arrived=list(dict(envelope.get("result") or {}).get("resolved") or [])
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    rows=[x for x in await DB.get_caravans() if x.get('owner_type')=='player' and str(x.get('owner_key'))==str(interaction.user.id)]
    if not rows:
        await interaction.response.send_message("🐫 You have not dispatched a caravan.",ephemeral=False);return
    lines=[f"🐫 **Caravans — {c['name']}**"]
    if arrived: lines.append(f"\n✅ **{len(arrived)} caravan(s) resolved on this check.**")
    for row in rows[:15]:
        remaining=max(0,int(row['arrive_game_minute'])-wt.total_minutes)
        details=f"escort {int(row.get('escort_strength') or 0)} • {'smuggling' if int(row.get('smuggling') or 0) else f'tax {int(row.get("tax_rate") or 0)}%'}"
        if row['status']=='traveling': details+=f" • {remaining} game minutes"
        else: details+=f" • payout {int(row.get('payout_final') or 0)} • toll {int(row.get('toll_paid') or 0)} • losses {int((row.get('losses') or {}).get('percent',0))}%"
        lines.append(f"\n`#{row['caravan_id']}` {row['origin']} → **{row['destination']}** • **{row['status']}** • {details}")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(caravan_group, name="events", description="Inspect the route event history for one caravan")
async def caravan_events(interaction: discord.Interaction, caravan_id: int) -> None:
    c=await require_character(interaction)
    if not c:return
    rows=[x for x in await DB.get_caravans() if int(x['caravan_id'])==int(caravan_id) and x.get('owner_type')=='player' and str(x.get('owner_key'))==str(interaction.user.id)]
    if not rows:
        await interaction.response.send_message("That is not one of your caravans.",ephemeral=False);return
    events=await DB.get_caravan_events(caravan_id)
    lines=[f"🐫 **Caravan #{caravan_id} Route Events**"]
    for e in events: lines.append(f"\n• **{e['event_type']}** at game minute {e['game_minute']} — {e.get('detail',{})}")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(party_group, name="create", description="Create a voluntary cultivation party")
@serialized_user_action
async def party_create(interaction: discord.Interaction, name: str) -> None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("party.create",interaction.user.id,{"name":name},action_id=f"discord:{interaction.id}:party.create")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"👥 Party **{result.get('name',name)}** created.",ephemeral=False)


@registered_group_command(party_group, name="join", description="Join another cultivator's active party voluntarily")
@serialized_user_action
async def party_join(interaction: discord.Interaction, leader: discord.Member) -> None:
    if not await require_character(interaction):return
    target=await DB.get_party(leader.id)
    if not target:
        await interaction.response.send_message("That cultivator does not lead an active party.",ephemeral=False);return
    try: await ENGINE.authoritative_action("party.join",interaction.user.id,{"party_id":int(target['party_id'])},action_id=f"discord:{interaction.id}:party.join")
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False);return
    await interaction.response.send_message(f"👥 You joined {leader.mention}'s party.",ephemeral=False)


@registered_group_command(party_group, name="status", description="View your active cultivation party")
async def party_status(interaction: discord.Interaction) -> None:
    c=await require_character(interaction)
    if not c:return
    party=await DB.get_party(interaction.user.id)
    if not party:
        await interaction.response.send_message("You are not in an active party.",ephemeral=False);return
    members="\n".join(f"• <@{m['user_id']}> — {m['role']}" for m in party['members'])
    await interaction.response.send_message(f"👥 **{party['name']}** (`#{party['party_id']}`)\n{members}",ephemeral=False)


@registered_group_command(party_group, name="leave", description="Leave your current cultivation party")
@serialized_user_action
async def party_leave(interaction: discord.Interaction) -> None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("party.leave",interaction.user.id,{},action_id=f"discord:{interaction.id}:party.leave")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message("👋 You left your active party.",ephemeral=False)


@registered_group_command(duel_group, name="challenge", description="Offer a nonlethal PvP duel; combat cannot start without acceptance")
@serialized_user_action
async def duel_challenge(interaction: discord.Interaction, member: discord.Member, stakes: str="honor") -> None:
    c=await require_character(interaction)
    if not c:return
    target=await DB.get_character(member.id)
    if not target or str(target.get('life_status'))!='alive':
        await interaction.response.send_message("That member has no living cultivator to challenge.",ephemeral=False);return
    if str(target.get('location'))!=str(c.get('location')):
        await interaction.response.send_message("Consent PvP requires both cultivators to be mechanically present at the same location.",ephemeral=False);return
    if WORLD.location_safe_zone(str(c.get('location'))):
        await interaction.response.send_message("Local formations suppress PvP here.",ephemeral=False);return
    try:
        envelope=await ENGINE.authoritative_action("pvp.challenge",interaction.user.id,{"target_user_id":member.id,"stakes":stakes,"ttl_seconds":300},action_id=f"discord:{interaction.id}:pvp.challenge")
    except GameEngineError as exc:
        await interaction.response.send_message(str(exc),ephemeral=False);return
    result=dict(envelope.get("result") or {});cid=int(result.get("challenge_id",0))
    await interaction.response.send_message(f"⚔️ <@{member.id}> receives consent duel challenge `#{cid}` from <@{interaction.user.id}>. Stakes: **{stakes[:200]}**. It expires in **5 minutes**; use **/combat → Duels → Respond**.")


@registered_group_command(duel_group, name="respond", description="Accept or reject a pending consent duel")
@app_commands.choices(decision=[app_commands.Choice(name="Accept",value="accept"),app_commands.Choice(name="Reject",value="reject")])
@serialized_user_action
async def duel_respond(interaction: discord.Interaction, challenge_id: int, decision: app_commands.Choice[str]) -> None:
    c=await require_character(interaction)
    if not c:return
    try:
        envelope=await ENGINE.authoritative_action("pvp.respond",interaction.user.id,{"challenge_id":int(challenge_id),"accept":decision.value=='accept'},action_id=f"discord:{interaction.id}:pvp.respond")
    except GameEngineError as exc:
        await interaction.response.send_message(str(exc),ephemeral=False);return
    result=dict(envelope.get("result") or {})
    if decision.value=='accept':
        await interaction.response.send_message(f"⚔️ Duel accepted. Nonlethal PvP match **#{result['match_id']}** begins; challenger acts first. Use **/combat → Duels → Act**.")
    else:
        await interaction.response.send_message("🤝 Duel rejected. No combat state was created.")


@registered_group_command(duel_group, name="status", description="View your active consent PvP match or pending challenges")
async def duel_status(interaction: discord.Interaction) -> None:
    c=await require_character(interaction)
    if not c:return
    match=await DB.get_pvp_match(interaction.user.id)
    if match:
        await interaction.response.send_message(f"⚔️ **Duel #{match['match_id']}**\n<@{match['player1_user_id']}>: **{match['player1_hp']} Vitality**\n<@{match['player2_user_id']}>: **{match['player2_hp']} Vitality**\nTurn: <@{match['turn_user_id']}> • Status **{match['status']}**",ephemeral=False);return
    rows=await DB.get_pvp_challenges(interaction.user.id,pending_only=True)
    if not rows:
        await interaction.response.send_message("No active duel or pending consent challenge.",ephemeral=False);return
    await reply_long(interaction,"⚔️ **Pending Duel Challenges**\n"+"\n".join(f"`#{x['challenge_id']}` <@{x['challenger_user_id']}> → <@{x['target_user_id']}> • {x['stakes']}" for x in rows),ephemeral=False)


@registered_group_command(duel_group, name="act", description="Take a turn in an accepted nonlethal PvP duel")
@app_commands.choices(style=[app_commands.Choice(name="Attack",value="attack"),app_commands.Choice(name="Defend",value="defend"),app_commands.Choice(name="Surrender",value="surrender")])
@serialized_user_action
async def duel_act(interaction: discord.Interaction, style: app_commands.Choice[str]) -> None:
    c=await require_character(interaction)
    if not c:return
    match=await DB.get_pvp_match(interaction.user.id)
    if not match:
        await interaction.response.send_message("You have no active accepted duel.",ephemeral=False);return
    if int(match['turn_user_id'])!=interaction.user.id:
        await interaction.response.send_message("It is your opponent's turn.",ephemeral=False);return
    opponent_id=int(match['player2_user_id']) if int(match['player1_user_id'])==interaction.user.id else int(match['player1_user_id'])
    opponent=await DB.get_character(opponent_id)
    if not opponent:
        await interaction.response.send_message("Opponent state is unavailable.",ephemeral=False);return
    try:
        envelope=await ENGINE.authoritative_action(
            "pvp.act",interaction.user.id,
            {"match_id":int(match['match_id']),"style":style.value,"match_version":int(match['version'])},
            action_id=f"discord:{interaction.id}:pvp.act",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(str(exc),ephemeral=False);return
    resolved=dict(envelope.get("result") or {});updated=dict(resolved.get("match") or {})
    if style.value=='surrender':
        await interaction.response.send_message(f"🏳️ You surrender duel **#{match['match_id']}**. <@{opponent_id}> wins; no true-death or injury roll occurs. Martial Society reputation records the honorable result.");return
    if style.value=='defend':
        await interaction.response.send_message(f"🛡️ You take a guarded stance. Turn passes to <@{opponent_id}>.");return
    roll=SimpleNamespace(**resolved);damage=int(resolved.get('damage',0));finished=bool(resolved.get('finished'));result=f"\n💥 Damage **{damage}**." if damage else "\nThe attack fails to land cleanly."
    if finished:
        winner_id=int(resolved.get('winner_user_id',0));result+=f"\n🏆 <@{winner_id}> wins the **nonlethal** duel. Martial Society reputation records the result."
    await interaction.response.send_message(f"⚔️ **Duel #{match['match_id']}**\n{roll_line(roll)}{result}")


@registered_root_command(name="daoheart", description="View Dao-heart stability, public face and sworn internal commitments", guild=GUILD)
async def daoheart_command(interaction: discord.Interaction) -> None:
    c=await require_character(interaction)
    if not c:return
    state=await DB.get_social_state(interaction.user.id)
    await interaction.response.send_message(f"☯️ **Dao Heart — {c['name']}**\nDao heart **{state['dao_heart']}/100** • Stability **{state['dao_stability']}/100** • Face **{state['face']:+d}**\nVow: {state['vow'] or 'None'}\nObsession: {state['obsession'] or 'None'}",ephemeral=False)


@registered_root_command(name="provenance", description="Inspect ownership marks, legality and tracking on one carried item", guild=GUILD)
async def provenance_command(interaction: discord.Interaction, item: str) -> None:
    c=await require_character(interaction)
    if not c:return
    rows=await DB.get_item_provenance(interaction.user.id,item)
    if not rows:
        await interaction.response.send_message("No provenance record exists for that item stack yet.",ephemeral=False);return
    lines=[f"🔎 **Provenance — {WORLD.item_name(item)}**"]
    for r in rows[:10]: lines.append(f"\n`#{r['provenance_id']}` {r['source_type']}:{r['source_key']} • **{r['legal_status']}** • authenticity {r['authenticity']}% • tracking {r['tracking_strength']}%"+(f" • mark `{r['ownership_mark']}`" if r.get('ownership_mark') else ""))
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_root_command(name="era", description="View the active era, automatic cycle timing and recent transition events", guild=GUILD)
async def era_command(interaction: discord.Interaction) -> None:
    c=await require_character(interaction)
    if not c:return
    wt=await current_world_time(); era=await DB.get_current_era()
    if not era:
        await interaction.response.send_message("No active world era is recorded.",ephemeral=False);return
    mods=", ".join(f"{k}={v}" for k,v in (era.get('modifiers') or {}).items()) or "baseline laws"
    template=next((x for x in ERA_CYCLE if x['name']==era['name']),None)
    remaining=max(0,int(template['duration_days'])*MINUTES_PER_DAY-(wt.total_minutes-int(era['started_game_minute']))) if template else 0
    events=await DB.get_world_era_events(limit=5)
    lines=[f"🌌 **{era['name']}**",str(era.get('description','')),f"Started at game minute **{era['started_game_minute']}** • automatic transition in about **{remaining/MINUTES_PER_DAY:.1f} world-days**",f"Modifiers: **{mods}**"]
    if events:
        lines.append("\n**Recent Era Events**")
        for e in events: lines.append(f"• {e['title']} — game minute {e['game_minute']}")
    await interaction.response.send_message("\n".join(lines),ephemeral=False)


@registered_root_command(name="specialeffects",description="Inspect active special, Law, curse, control, and domain effects",guild=GUILD)
async def special_effects_command(interaction:discord.Interaction)->None:
    c=await require_character(interaction)
    if not c:return
    effects,_,wt=await current_effect_modifiers(interaction.user.id); special=[e for e in effects if e.get('special') or e.get('category') not in {None,'General'}]
    if not special: await interaction.response.send_message("No special effects are currently affecting you.",ephemeral=False);return
    lines=[f"✨ **Special Effects — {c['name']}**"]
    for e in special:
        remain='Permanent' if e.get('ends_game_minute') is None else f"{max(0,int(e['ends_game_minute'])-wt.total_minutes)} game minutes"
        lines.append(f"\n**{e.get('name')}** • {e.get('category','Special')} • Severity {e.get('severity',0)} • {remain}\n{e.get('description','')}")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)

# ---------- Player-owned locations / homes ----------
abode_group=app_commands.Group(name="abode",description="Own and develop a persistent private player-owned location")
ABODE_FACILITIES=[
    app_commands.Choice(name=PLAYER_PROPERTY_FACILITY_LABELS.get(x,x.replace('_',' ').title()),value=x)
    for x in PLAYER_PROPERTY_FACILITY_KEYS
][:25]


@registered_group_command(abode_group, name="establish",description="Establish your one persistent player-owned property at the current normal location")
@app_commands.choices(property_type=PLAYER_PROPERTY_TYPE_CHOICES)
@serialized_user_action
async def abode_establish(interaction:discord.Interaction,name:str,property_type:app_commands.Choice[str])->None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("abode.establish",interaction.user.id,{"name":name,"property_type":property_type.value},action_id=f"discord:{interaction.id}:abode.establish")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🏡 **{result.get('name',name)}** established.",ephemeral=False)


@registered_group_command(abode_group, name="status",description="Inspect your player-owned property, facilities and guest access")
async def abode_status(interaction:discord.Interaction)->None:
    c=await require_character(interaction)
    if not c:return
    a=await DB.get_abode(interaction.user.id)
    if not a:
        await interaction.response.send_message("You do not own a player property yet. Use **/abode → Establish**.",ephemeral=False);return
    guests=await DB.get_abode_guests(interaction.user.id)
    thread_text=f"<#{a['thread_id']}>" if a.get('thread_id') else "not created"
    facilities=" • ".join(player_property_facility_lines(a)) or "No developed facilities"
    await interaction.response.send_message(
        f"{player_property_emoji(a)} **{a['name']} — {player_property_label(a)}**\n"
        f"Entrance: **{a['base_location']}** • Grade: **{a['grade']}**\n"
        f"Facilities: {facilities}\n"
        f"Invited guests: **{len(guests)}**\n"
        f"Private location thread: {thread_text}\n\n"
        "The Discord thread is the scene for the property; the world location remains authoritative for entering and leaving.",
        ephemeral=False,
    )


@registered_group_command(abode_group, name="thread",description="Create or recover the private Discord thread for your player-owned property")
async def abode_thread_command(interaction:discord.Interaction)->None:
    c=await require_character(interaction)
    if not c:return
    a=await DB.get_abode(interaction.user.id)
    if not a:
        await interaction.response.send_message("You do not own a player property.",ephemeral=False);return
    thread=await ensure_abode_thread(interaction,a)
    if thread:
        await interaction.response.send_message(f"{player_property_emoji(a)} Private property scene: {thread.mention}",ephemeral=False)
    else:
        await interaction.response.send_message("Could not create a private property thread. Run **/admin → Server → Setup Server** and grant Create Private Threads / Send in Threads.",ephemeral=False)


@registered_group_command(abode_group, name="enter",description="Enter your player-owned property from its physical entrance location")
@serialized_user_action
async def abode_enter(interaction:discord.Interaction)->None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("abode.enter",interaction.user.id,{},action_id=f"discord:{interaction.id}:abode.enter")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🏡 You enter **{result.get('name','your property')}**.",ephemeral=False)


@registered_group_command(abode_group, name="visit",description="Enter another player's property if they invited you and you reached its entrance")
@serialized_user_action
async def abode_visit(interaction:discord.Interaction,owner:discord.Member)->None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("abode.visit",interaction.user.id,{"owner_user_id":owner.id},action_id=f"discord:{interaction.id}:abode.visit")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🏡 You visit **{result.get('name','the property')}**.",ephemeral=False)


@registered_group_command(abode_group, name="leave",description="Leave the current player-owned property and return to its entrance")
@serialized_user_action
async def abode_leave(interaction:discord.Interaction)->None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("abode.leave",interaction.user.id,{},action_id=f"discord:{interaction.id}:abode.leave")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🚪 You leave the property for **{result.get('outside','outside')}**.",ephemeral=False)


@registered_group_command(abode_group, name="invite",description="Invite another cultivator to your player-owned property")
async def abode_invite(interaction:discord.Interaction,member:discord.Member)->None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("abode.invite",interaction.user.id,{"guest_user_id":member.id},action_id=f"discord:{interaction.id}:abode.invite")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🔑 {member.mention} may now enter your property.",ephemeral=False)


@registered_group_command(abode_group, name="revoke",description="Revoke a guest's access to your player-owned property")
@serialized_user_action
async def abode_revoke(interaction:discord.Interaction,member:discord.Member)->None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("abode.revoke",interaction.user.id,{"guest_user_id":member.id},action_id=f"discord:{interaction.id}:abode.revoke")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🔒 Property access revoked for {member.mention}.",ephemeral=False)


@registered_group_command(abode_group, name="guests",description="List cultivators currently invited to your player-owned property")
async def abode_guests(interaction:discord.Interaction)->None:
    c=await require_character(interaction)
    if not c:return
    a=await DB.get_abode(interaction.user.id)
    if not a:
        await interaction.response.send_message("You do not own a player property.",ephemeral=False);return
    rows=await DB.get_abode_guests(interaction.user.id)
    if not rows:
        await interaction.response.send_message(f"🔐 **{a['name']}** has no invited guests.",ephemeral=False);return
    lines=[f"🔐 **Guest Access — {a['name']}**"]
    for row in rows[:25]:
        lines.append(f"• <@{int(row['guest_user_id'])}> — {row.get('character_name') or 'Cultivator'}")
    await interaction.response.send_message("\n".join(lines),ephemeral=False)


@registered_group_command(abode_group, name="upgrade",description="Upgrade one facility in your player-owned property")
@app_commands.choices(facility=ABODE_FACILITIES)
@serialized_user_action
async def abode_upgrade(interaction:discord.Interaction,facility:app_commands.Choice[str])->None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("abode.upgrade",interaction.user.id,{"facility":facility.value},action_id=f"discord:{interaction.id}:abode.upgrade")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🏡 **{facility.value}** upgraded to level **{result.get('level','?')}**.",ephemeral=False)


@registered_group_command(abode_group, name="focus",description="Use a developed property facility for a temporary specialization effect or scene benefit")
@app_commands.choices(facility=ABODE_FACILITIES)
@serialized_user_action
async def abode_focus(interaction:discord.Interaction,facility:app_commands.Choice[str])->None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("abode.focus",interaction.user.id,{"facility":facility.value},action_id=f"discord:{interaction.id}:abode.focus")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🏡 You focus within the **{facility.value}** facility.",ephemeral=False)

# ---------- Teleportation arrays / spatial keys ----------
array_group=app_commands.Group(name="array",description="Use public teleportation formations")

@registered_group_command(array_group, name="list",description="List teleportation arrays available from your current location")
async def array_list(interaction:discord.Interaction)->None:
    c=await require_character(interaction)
    if not c:return
    options=[d for d in WORLD.teleport_arrays.values() if d.get('from')==c['location']]
    if not options: await interaction.response.send_message("No public teleportation array is anchored at this location.",ephemeral=False);return
    lines=[f"🌀 **Teleportation Arrays — {c['location']}**"]
    for d in options: lines.append(f"• **{d['name']}** → {d['to']} • {d['cost']} {WORLD.currency_name(str(d['currency']))}")
    await interaction.response.send_message("\n".join(lines),ephemeral=False)

async def array_destination_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    c=await DB.get_character(interaction.user.id); needle=current.casefold().strip(); out=[]
    if c:
        for aid,d in WORLD.teleport_arrays.items():
            if d.get('from')==c['location'] and (not needle or needle in str(d['to']).casefold() or needle in str(d['name']).casefold()): out.append(app_commands.Choice(name=f"{d['name']} → {d['to']}"[:100],value=aid[:100]))
    return out[:25]

@registered_group_command(array_group, name="use",description="Travel through a public teleportation array")
@app_commands.autocomplete(array=array_destination_autocomplete)
@serialized_user_action
async def array_use(interaction:discord.Interaction,array:str)->None:
    if not await require_character(interaction):return
    wt=await current_world_time()
    try:
        e=await ENGINE.authoritative_action("array.use",interaction.user.id,{"array_id":array},action_id=f"discord:{interaction.id}:array.use"); r=dict(e.get('result') or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False);return
    await interaction.response.send_message(f"🌀 The formation ignites and folds the route beneath you. You arrive at **{r.get('location',r.get('destination','your destination'))}**.",ephemeral=False)

@registered_root_command(name="spatialkey",description="Use a spatial key/token to open its linked secret dimension",guild=GUILD)
@app_commands.autocomplete(item=usable_item_autocomplete)
@serialized_user_action
async def spatial_key_command(interaction:discord.Interaction,item:str)->None:
    if not await require_character(interaction):return
    wt=await current_world_time()
    try:
        e=await ENGINE.authoritative_action("spatial_key.use",interaction.user.id,{"item_id":item},action_id=f"discord:{interaction.id}:spatial_key.use"); r=dict(e.get('result') or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False);return
    await interaction.response.send_message(f"🗝️ The key tears open a temporary entrance to **{r.get('realm_name',r.get('realm_id','a secret realm'))}**.",ephemeral=False)

# ---------- Personal world creation ----------
innerworld_group=app_commands.Group(name="innerworld",description="Create and define a stabilized personal world at the peak of Space Law")

@registered_group_command(innerworld_group, name="create",description="Stabilize your own personal world")
@serialized_user_action
async def innerworld_create(interaction:discord.Interaction,name:str)->None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("personal_world.create",interaction.user.id,{"name":name},action_id=f"discord:{interaction.id}:personal_world.create")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🌌 Personal world **{result.get('name',name)}** created.",ephemeral=False)

@registered_group_command(innerworld_group, name="status",description="Inspect your stabilized personal world")
async def innerworld_status(interaction:discord.Interaction)->None:
    c=await require_character(interaction)
    if not c:return
    pw=await DB.get_personal_world(interaction.user.id)
    if not pw: await interaction.response.send_message("You have no stabilized personal world.",ephemeral=False);return
    rules='\n'.join(f"• **{k}:** {v}" for k,v in pw.get('laws',{}).items()) or 'No explicit local laws defined yet.'
    await reply_long(interaction,f"🌌 **{pw['name']}**\nStability: **{pw['stability']}**\nAccess: {pw['access_mode']}\n\n**Local Laws**\n{rules}",ephemeral=False)

@registered_group_command(innerworld_group, name="setrule",description="Define or refine one physical/conceptual rule inside your personal world")
@serialized_user_action
async def innerworld_setrule(interaction:discord.Interaction,rule:str,definition:str)->None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("personal_world.set_rule",interaction.user.id,{"rule":rule,"definition":definition},action_id=f"discord:{interaction.id}:personal_world.set_rule")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🌌 Inner-world rule **{rule}** updated.",ephemeral=False)

@registered_group_command(innerworld_group, name="enter",description="Enter your personal world")
@serialized_user_action
async def innerworld_enter(interaction:discord.Interaction)->None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("personal_world.enter",interaction.user.id,{},action_id=f"discord:{interaction.id}:personal_world.enter")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message("🌌 You enter your personal world.",ephemeral=False)

@registered_group_command(innerworld_group, name="leave",description="Leave your personal world and return to Greenriver Town")
@serialized_user_action
async def innerworld_leave(interaction:discord.Interaction)->None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("personal_world.leave",interaction.user.id,{},action_id=f"discord:{interaction.id}:personal_world.leave")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🚪 You leave the personal world for **{result.get('outside','outside')}**.",ephemeral=False)


sect_group = app_commands.Group(name="sect", description="Sect membership, lineage, resources, manor, and martial-family titles")
sect_manor_group = app_commands.Group(
    name="manor",
    description="Build and upgrade your sect's shared cultivation manor",
    parent=sect_group,
)
sect_disciple_group = app_commands.Group(
    name="discipleship",
    description="Request, accept and manage player master-disciple bonds",
    parent=sect_group,
)
sect_recruitment_group = app_commands.Group(
    name="recruitment",
    description="Discover sects, earn NPC recommendations and take story-driven entrance trials",
    parent=sect_group,
)
SECT_MANOR_FACILITY_CHOICES = [
    app_commands.Choice(name=str(definition["name"]), value=key)
    for key, definition in SECT_MANOR_FACILITIES.items()
]


def _relationship_label(rel: dict[str, Any] | None, *, show_chinese: bool) -> str:
    if not rel:
        return "Martial Sibling"
    english = rel.get("translation") or "Martial Sibling"
    if show_chinese and rel.get("pinyin") and rel.get("hanzi"):
        return f"{english} ({rel['pinyin']} {rel['hanzi']})"
    return english


async def _build_family_text(user_id: int, *, show_chinese: bool = False) -> str | None:
    c = await DB.get_character(user_id)
    if not c:
        return None
    snap = await DB.get_lineage_snapshot(user_id)
    if not snap:
        return None

    membership = snap.get("membership")
    lines = [f"🌿 **Martial Family — {c['name']}**"]
    if membership:
        lines.append(f"🏯 **{membership['sect_name']}** — {membership['rank_name']}")

    if snap.get("grandmaster"):
        label = "Grandmaster"
        if show_chinese:
            label += " (Shigong/Shiye 师公/师爷)"
        lines.append(f"**{label}:** {snap['grandmaster']['name']}")

    if snap.get("master"):
        label = "Master"
        if show_chinese:
            label += " (Shifu 师父)"
        lines.append(f"**{label}:** {snap['master']['name']}")

    if snap.get("master_siblings"):
        lines.append("\n**Your master's martial siblings:**")
        for row in snap["master_siblings"][:15]:
            rel = await DB.get_address_context(user_id, int(row["user_id"]))
            lines.append(f"• {_relationship_label(rel, show_chinese=show_chinese)}: {row['name']}")

    if snap.get("siblings"):
        lines.append("\n**Your martial siblings:**")
        for row in snap["siblings"][:20]:
            rel = await DB.get_address_context(user_id, int(row["user_id"]))
            lines.append(f"• {_relationship_label(rel, show_chinese=show_chinese)}: {row['name']}")

    if snap.get("disciples"):
        lines.append("\n**Your direct disciples:**")
        for row in snap["disciples"][:20]:
            lines.append(f"• Disciple: {row['name']}")

    if len(lines) <= 2 and not snap.get("master"):
        lines.append("*No master/disciple lineage has been recorded yet.*")
    return "\n".join(lines)



async def _sync_sect_discoveries(user_id: int, character: dict, *, game_minute: int | None = None) -> list[str]:
    """Promote already-discovered recruitment locations into public sect knowledge."""
    if game_minute is None:
        game_minute = (await current_world_time()).total_minutes
    known_locations = await _known_locations(user_id, character)
    newly_known: list[str] = []
    for sect_name, sect_def in WORLD.sects.items():
        rec = recruitment_definition(WORLD.sects, sect_name)
        if not rec:
            continue
        location = str(rec.get("location") or "")
        if location and location in known_locations:
            if await DB.discover_sect(
                user_id, sect_name, game_minute=int(game_minute),
                discovery_kind="recruitment_route", source_key=location,
            ):
                newly_known.append(sect_name)
    return newly_known


async def _known_sect_names(user_id: int, character: dict) -> list[str]:
    await _sync_sect_discoveries(user_id, character)
    rows = await DB.get_discovered_sects(user_id)
    names = [str(row.get("sect_name")) for row in rows if str(row.get("sect_name")) in WORLD.sects]
    return sorted(dict.fromkeys(names))


def _sect_recruitment_at_location(location: str) -> list[str]:
    out: list[str] = []
    for sect_name in WORLD.sects:
        rec = recruitment_definition(WORLD.sects, sect_name)
        if rec and str(rec.get("location") or "") == str(location):
            out.append(sect_name)
    return sorted(out)


async def sect_known_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    c = await DB.get_character(interaction.user.id)
    if not c:
        return []
    needle = current.casefold().strip()
    names = await _known_sect_names(interaction.user.id, c)
    return [
        app_commands.Choice(
            name=f"{name} — {WORLD.sects[name].get('specialty','Sect')}"[:100], value=name[:100]
        )
        for name in names if not needle or needle in name.casefold()
    ][:25]


async def sect_local_trial_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    c = await DB.get_character(interaction.user.id)
    if not c:
        return []
    known = set(await _known_sect_names(interaction.user.id, c))
    needle = current.casefold().strip()
    names = [name for name in _sect_recruitment_at_location(str(c.get("location") or "")) if name in known]
    return [
        app_commands.Choice(name=f"{name} — Entrance Trial"[:100], value=name[:100])
        for name in names if not needle or needle in name.casefold()
    ][:25]


async def sect_recommender_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    c = await DB.get_character(interaction.user.id)
    if not c:
        return []
    wt = await current_world_time()
    needle = current.casefold().strip()
    out: list[app_commands.Choice[str]] = []
    for name, npc in WORLD.npcs.items():
        if not bool(npc.get("can_recommend")) or not npc.get("sect_affiliation"):
            continue
        npc_location = await current_npc_location(name, wt.period)
        if npc_location != str(c.get("location") or ""):
            continue
        if needle and needle not in name.casefold() and needle not in str(npc.get("sect_affiliation")).casefold():
            continue
        out.append(app_commands.Choice(
            name=f"{name} — {npc.get('sect_affiliation')}"[:100], value=name[:100]
        ))
    return out[:25]


@registered_group_command(sect_recruitment_group, name="status", description="Show sects, recruitment gates and recommendations your character actually knows")
async def sect_recruitment_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    membership = await DB.get_sect_membership(interaction.user.id)
    wt = await current_world_time()
    newly = await _sync_sect_discoveries(interaction.user.id, c, game_minute=wt.total_minutes)
    known = await _known_sect_names(interaction.user.id, c)
    recommendations = await DB.get_active_sect_recommendations(interaction.user.id)
    lines = ["🏯 **Sect Recruitment Journal**"]
    if membership:
        lines.append(f"Current public sect: **{membership['sect_name']} — {membership['rank_name']}**")
    else:
        lines.append("Current public sect: **Unaffiliated**")
    if newly:
        lines.append(f"🧭 Newly recognized from discovered routes: **{', '.join(newly)}**")
    lines.append("\n**Known sects**")
    if not known:
        lines.append("• None yet. Explore the world, meet sect-affiliated NPCs, and listen for recruitment routes.")
    for sect_name in known:
        sect = WORLD.sects[sect_name]
        rec = recruitment_definition(WORLD.sects, sect_name) or {}
        at_gate = str(c.get("location") or "") == str(rec.get("location") or "")
        lines.append(
            f"• **{sect_name}** • {sect.get('alignment','Unknown')} • {sect.get('specialty','Unknown specialty')}\n"
            f"  Gate: **{rec.get('location','Unknown')}**{' • **You are here**' if at_gate else ''}"
        )
    lines.append("\n**Active NPC recommendations**")
    if recommendations:
        for row in recommendations:
            lines.append(
                f"• **{row['sect_name']}** via **{row['npc_name']}** • entrance bonus **+{int(row.get('bonus',0))}**"
            )
    else:
        lines.append("• None. Speak with a local sect-affiliated NPC before asking them to sponsor you.")
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(sect_recruitment_group, name="info", description="Inspect the public recruitment story for a sect you have discovered")
@app_commands.autocomplete(sect_name=sect_known_autocomplete)
async def sect_recruitment_info(interaction: discord.Interaction, sect_name: str) -> None:
    c = await require_character(interaction)
    if not c:
        return
    known = set(await _known_sect_names(interaction.user.id, c))
    if sect_name not in known or sect_name not in WORLD.sects:
        await interaction.response.send_message("That sect has not been discovered by this character.", ephemeral=False)
        return
    sect = WORLD.sects[sect_name]
    rec = recruitment_definition(WORLD.sects, sect_name) or {}
    recommendation = await DB.get_active_sect_recommendation(interaction.user.id, sect_name)
    lines = [
        f"🏯 **{sect_name} — Recruitment**",
        f"Alignment: **{sect.get('alignment','Unknown')}**",
        f"Specialty: **{sect.get('specialty','Unknown')}**",
        f"Recruitment gate: **{rec.get('location','Unknown')}**",
        f"Entrance trial: **{rec.get('trial_name','Entrance Examination')}**",
        str(rec.get("description") or "A formal sect entrance examination."),
        f"Examiner: **{rec.get('examiner','Sect Examiner')}**",
    ]
    if recommendation:
        lines.append(
            f"\n📜 **Recommendation:** {recommendation['npc_name']} has sponsored your approach (**+{int(recommendation.get('bonus',0))}** to the trial checks)."
        )
    elif not bool(rec.get("public_route", True)):
        lines.append("\n🌑 This is not a public recruitment route. An affiliated NPC recommendation is normally required to reveal the way.")
    else:
        lines.append("\nA recommendation is optional, but a trusted sponsor can improve the entrance examination.")
    if str(c.get("location") or "") != str(rec.get("location") or ""):
        lines.append("\n🗺️ You must physically travel to the recruitment gate before attempting the trial.")
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(sect_recruitment_group, name="recommendation", description="Ask a local sect-affiliated NPC to sponsor your entrance attempt")
@app_commands.autocomplete(npc=sect_recommender_autocomplete)
@serialized_user_action
async def sect_recruitment_recommendation(interaction: discord.Interaction, npc: str) -> None:
    c=await require_character(interaction)
    if not c:return
    npc_data=await DB.get_npc_definition(npc)
    if not npc_data or not bool(npc_data.get('can_recommend')) or not npc_data.get('sect_affiliation'):
        await interaction.response.send_message("That NPC cannot issue a sect recommendation.",ephemeral=False);return
    sect_name=str(npc_data.get('sect_affiliation')); rec=recruitment_definition(WORLD.sects,sect_name)
    if not rec:
        await interaction.response.send_message("That NPC's sect has no public recruitment path configured.",ephemeral=False);return
    wt=await current_world_time(); npc_location=await current_npc_location(npc,wt.period)
    if npc_location!=str(c.get('location') or ''):
        await interaction.response.send_message(f"**{npc}** is currently at **{npc_location or 'an unknown location'}**, not **{c['location']}**.",ephemeral=False);return
    memory=await DB.get_npc_memory(interaction.user.id,npc)
    if not memory.strip():
        await interaction.response.send_message(f"Speak with **{npc}** first; a recommendation requires established personal history.",ephemeral=False);return
    family=await DB.get_birth_family(interaction.user.id); reps=await DB.get_reputations(interaction.user.id); rep=next((int(x.get('score',0)) for x in reps if str(x.get('faction_key'))==sect_name),0)
    modifier,notes=recommendation_modifier(c,faction_reputation=rep,family=family,sect_alignment=str(WORLD.sects[sect_name].get('alignment','Neutral')))
    tn=max(8,int(npc_data.get('recommendation_tn',14))); bonus=max(0,int(npc_data.get('recommendation_bonus',rec.get('recommendation_bonus',2))))
    try:
        e=await ENGINE.authoritative_action("sect.recruitment.recommendation",interaction.user.id,{"npc_name":npc,"sect_name":sect_name,"modifier":modifier,"tn":tn,"bonus":bonus,"location":str(rec.get('location') or ''),"details":{"modifier_notes":notes}},action_id=f"discord:{interaction.id}:sect.recruitment.recommendation"); r=dict(e.get('result') or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False);return
    roll=dict(r.get('roll') or {}); roll_text=f"2d10 {int(roll.get('modifier',0)):+d} = **{int(roll.get('total',0))}** vs TN **{int(roll.get('tn',tn))}**"
    tail=f"📜 Recommendation secured: +{bonus}." if r.get('success') else "The recommendation was not granted."
    await interaction.response.send_message(f"{roll_text}\n{tail}",ephemeral=False)


@registered_group_command(sect_recruitment_group, name="recommendations", description="List active NPC sect recommendations")
async def sect_recruitment_recommendations(interaction: discord.Interaction) -> None:
    if not await require_character(interaction):
        return
    rows = await DB.get_active_sect_recommendations(interaction.user.id)
    if not rows:
        await interaction.response.send_message("You hold no active sect recommendations.", ephemeral=False)
        return
    lines = ["📜 **Active Sect Recommendations**"]
    for row in rows:
        lines.append(f"• **{row['sect_name']}** — sponsor **{row['npc_name']}** • trial bonus **+{int(row.get('bonus',0))}**")
    lines.append("\nA recommendation is consumed when you take that sect's entrance trial. It does not guarantee admission.")
    await interaction.response.send_message("\n".join(lines), ephemeral=False)


@registered_group_command(sect_recruitment_group, name="trial", description="Take the story-driven entrance examination at your current sect gate")
@app_commands.autocomplete(sect_name=sect_local_trial_autocomplete)
@serialized_user_action
async def sect_recruitment_trial(interaction: discord.Interaction, sect_name: str) -> None:
    c=await require_character(interaction)
    if not c:return
    if sect_name not in WORLD.sects:
        await interaction.response.send_message("Unknown sect.",ephemeral=False);return
    wt=await current_world_time(); rec=recruitment_definition(WORLD.sects,sect_name); profile=trial_profile(WORLD.sects,sect_name)
    if not rec or not profile:
        await interaction.response.send_message("That sect has no configured entrance trial.",ephemeral=False);return
    recommendation=await DB.get_active_sect_recommendation(interaction.user.id,sect_name); recommendation_bonus=int(recommendation.get('bonus',0)) if recommendation else 0; family=await DB.get_birth_family(interaction.user.id)
    primary_mod,primary_notes,rejection=trial_modifier(c,rec,attribute=profile.primary_attribute,family=family,recommendation_bonus=recommendation_bonus)
    secondary_mod,secondary_notes,rejection2=trial_modifier(c,rec,attribute=profile.secondary_attribute,family=family,recommendation_bonus=recommendation_bonus)
    if rejection or rejection2:
        await interaction.response.send_message(f"🚫 **Entrance refused before examination.** {rejection or rejection2}",ephemeral=False);return
    try:
        e=await ENGINE.authoritative_action("sect.recruitment.trial",interaction.user.id,{"sect_name":sect_name,"examiner":profile.examiner,"location":profile.location,"trial_name":profile.trial_name,"primary_modifier":primary_mod,"secondary_modifier":secondary_mod,"base_tn":profile.base_tn,"primary_details":primary_notes,"secondary_details":secondary_notes},action_id=f"discord:{interaction.id}:sect.recruitment.trial"); r=dict(e.get('result') or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False);return
    outcome=str(r.get('outcome','fail')); p=dict(r.get('primary') or {}); q=dict(r.get('secondary') or {})
    await interaction.response.send_message(f"**{profile.trial_name}** — {outcome.replace('_',' ').title()}\nPrimary: **{p.get('total','?')}** vs TN **{p.get('tn','?')}**\nSecondary: **{q.get('total','?')}** vs TN **{q.get('tn','?')}**",ephemeral=False)


@registered_group_command(sect_recruitment_group, name="history", description="Review your recent sect recommendation and entrance-trial history")
async def sect_recruitment_history(interaction: discord.Interaction) -> None:
    if not await require_character(interaction):
        return
    rows = await DB.get_recent_sect_recruitment_attempts(interaction.user.id, limit=12)
    if not rows:
        await interaction.response.send_message("You have no sect recruitment history yet.", ephemeral=False)
        return
    lines = ["📚 **Sect Recruitment History**"]
    for row in rows:
        kind = "Recommendation" if str(row.get("attempt_type")) == "recommendation" else "Entrance Trial"
        result = str(row.get("result", "unknown")).replace("_", " ").title()
        actor = f" • {row.get('npc_name')}" if row.get("npc_name") else ""
        lines.append(f"• **{row.get('sect_name')}** — {kind}: **{result}**{actor}")
    await interaction.response.send_message("\n".join(lines), ephemeral=False)


@registered_group_command(sect_group, name="form", description="Choose which English martial-sibling titles are used for your character")
@app_commands.choices(style=ADDRESS_STYLE_CHOICES)
async def sect_form(interaction: discord.Interaction, style: app_commands.Choice[str]) -> None:
    c = await require_character(interaction)
    if not c:
        return
    await DB.set_address_style(interaction.user.id, style.value)
    examples = {
        "masculine": "Senior Brother / Junior Brother",
        "feminine": "Senior Sister / Junior Sister",
        "neutral": "Senior / Junior Martial Sibling",
    }
    await interaction.response.send_message(
        f"✅ Your normal English sect address is now **{examples[style.value]}**.",
        ephemeral=False,
    )


@registered_group_command(sect_group, name="status", description="Show sect membership and direct lineage")
async def sect_status(interaction: discord.Interaction, member: discord.Member | None = None) -> None:
    target = member or interaction.user
    c = await DB.get_character(target.id)
    if not c:
        await interaction.response.send_message("That member has no cultivation character.", ephemeral=False)
        return
    membership = await DB.get_sect_membership(target.id)
    master = await DB.get_master(target.id)
    lines = [f"🏯 **Sect Record — {c['name']}**"]
    if membership:
        lines += [
            f"Sect: **{membership['sect_name']}**",
            f"Rank: **{membership['rank_name']}** (level {membership['rank_level']})",
            f"Contribution points: **{membership.get('contribution_points', 0)}**",
            f"Influence: **{membership.get('influence', 0)}**",
        ]
        snap = await DB.get_lineage_snapshot(target.id)
        if snap.get("person", {}).get("master_attention") is not None and master:
            lines.append(f"Master attention: **{snap['person'].get('master_attention', 0)}**")
    else:
        lines.append("Sect: **Unaffiliated / not recorded**")
    lines.append(f"Master: **{master['name']}**" if master else "Master: *none recorded*")
    style_names = {
        "masculine": "Senior Brother / Junior Brother",
        "feminine": "Senior Sister / Junior Sister",
        "neutral": "Senior / Junior Martial Sibling",
    }
    lines.append(f"Normal address form: **{style_names.get(c.get('address_style', 'neutral'), 'Senior / Junior Martial Sibling')}**")
    await interaction.response.send_message("\n".join(lines), ephemeral=False)




@registered_group_command(sect_disciple_group, name="status", description="View your master, disciples and pending player contracts")
async def sect_discipleship_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    snap = await DB.get_lineage_snapshot(interaction.user.id)
    incoming = await DB.get_disciple_requests(interaction.user.id, incoming=True)
    outgoing = await DB.get_disciple_requests(interaction.user.id, incoming=False)
    lines = [f"🎓 **Master-Disciple Record — {c['name']}**"]
    master = snap.get("master")
    lines.append(f"Master: **{master['name']}**" if master else "Master: *none*")
    disciples = list(snap.get("disciples") or [])
    lines.append("Direct disciples: " + (", ".join(f"**{x['name']}**" for x in disciples[:15]) if disciples else "*none*"))
    if incoming:
        lines.append("\n**Requests awaiting your answer**")
        for row in incoming[:15]:
            lines.append(f"• `#{row['request_id']}` — **{row['other_name']}**, realm {row['other_realm_index']} stage {row['other_phase']}")
    if outgoing:
        lines.append("\n**Requests you have sent**")
        for row in outgoing[:15]:
            lines.append(f"• `#{row['request_id']}` → **{row['other_name']}**")
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(sect_disciple_group, name="request", description="Ask a stronger cultivator to formally become your master")
@serialized_user_action
async def sect_discipleship_request(interaction: discord.Interaction, master: discord.Member) -> None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("discipleship.request",interaction.user.id,{"master_user_id":master.id},action_id=f"discord:{interaction.id}:discipleship.request")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🙏 Discipleship request `#{result.get('request_id')}` sent to {master.mention}.",ephemeral=False)


@registered_group_command(sect_disciple_group, name="accept", description="Accept a pending disciple request addressed to you")
@serialized_user_action
async def sect_discipleship_accept(interaction: discord.Interaction, request_id: int) -> None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("discipleship.resolve",interaction.user.id,{"request_id":int(request_id),"accept":True},action_id=f"discord:{interaction.id}:discipleship.resolve")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message("🙏 Discipleship request accepted.",ephemeral=False)


@registered_group_command(sect_disciple_group, name="reject", description="Reject a pending disciple request addressed to you")
@serialized_user_action
async def sect_discipleship_reject(interaction: discord.Interaction, request_id: int) -> None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("discipleship.resolve",interaction.user.id,{"request_id":int(request_id),"accept":False},action_id=f"discord:{interaction.id}:discipleship.resolve")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message("Discipleship request rejected.",ephemeral=False)


@registered_group_command(sect_disciple_group, name="leave", description="Sever your current master-disciple bond")
@serialized_user_action
async def sect_discipleship_leave(interaction: discord.Interaction, confirm: bool = False) -> None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("discipleship.leave",interaction.user.id,{},action_id=f"discord:{interaction.id}:discipleship.leave")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message("🧵 Your discipleship bond has been ended.",ephemeral=False)


SECT_ABODE_ACTIONS = [
    app_commands.Choice(name="Status / Open Thread", value="status"),
    app_commands.Choice(name="Enter Sect Abode", value="enter"),
    app_commands.Choice(name="Leave Sect Abode", value="leave"),
]


@registered_group_command(sect_group, name="abode", description="Open, enter or leave the private residence assigned by your public sect")
@app_commands.choices(action=SECT_ABODE_ACTIONS)
@serialized_user_action
async def sect_abode(interaction: discord.Interaction, action: app_commands.Choice[str]) -> None:
    c = await require_character(interaction)
    if not c:
        return
    membership = await DB.get_sect_membership(interaction.user.id)
    if not membership:
        await interaction.response.send_message("You are not a public sect member, so no sect abode is assigned.", ephemeral=False)
        return
    abode = await ensure_sect_abode_record(interaction.user.id, c, membership)
    thread = await ensure_sect_abode_thread_for(interaction.guild, interaction.user, abode) if interaction.guild else None
    if action.value == "status":
        await interaction.response.send_message(
            f"🏯 **{abode['name']}**\nSect: **{abode['sect_name']}**\nSect gate: **{abode['base_location']}**\n"
            f"Current location: **{c.get('location','Unknown')}**\n"
            + (f"Private scene: {thread.mention}" if thread else "⚠️ Private scene thread is unavailable; repair the base channels."),
            ephemeral=False,
        )
        return
    if action.value == "leave":
        if str(c.get("location") or "") != str(abode["location_key"]):
            await interaction.response.send_message("You are not inside your sect abode.", ephemeral=False)
            return
        await DB.set_location(interaction.user.id, str(abode["base_location"]))
        if thread:
            try: await thread.send(f"🚪 **{c['name']}** leaves the sect abode and returns to **{abode['base_location']}**.")
            except discord.HTTPException: pass
        await interaction.response.send_message(f"You leave your sect abode and return to **{abode['base_location']}**.", ephemeral=False)
        return
    if str(c.get("location") or "") != str(abode["base_location"]):
        await interaction.response.send_message(
            f"Travel to the sect gate at **{abode['base_location']}** before entering your assigned residence.", ephemeral=False
        )
        return
    await DB.set_location(interaction.user.id, str(abode["location_key"]))
    if thread:
        try: await thread.send(f"🏯 **{c['name']}** enters **{abode['name']}**. This private thread is now the active residence scene.")
        except discord.HTTPException: pass
    await interaction.response.send_message(
        f"🏯 You enter **{abode['name']}**." + (f" Continue in {thread.mention}." if thread else ""), ephemeral=False
    )


@registered_group_command(sect_group, name="shadow", description="Investigate the hidden Heaven-Devouring Demon Sect and its karma gates")
@app_commands.choices(action=[
    app_commands.Choice(name="Investigate", value="investigate"),
    app_commands.Choice(name="Status", value="status"),
    app_commands.Choice(name="Accept initiation", value="initiate"),
])
@serialized_user_action
async def sect_shadow(interaction: discord.Interaction, action: app_commands.Choice[str]) -> None:
    c=await require_character(interaction)
    if not c:return
    sect_def=WORLD.sects.get("Heaven-Devouring Demon Sect",{})
    karma=int(c.get('karma_score',0)); hidden=await DB.get_hidden_sect_membership(interaction.user.id)
    world_name=WORLD.realm_world(int(c.get('realm_index',0)))
    branch=str((sect_def.get('branches') or {}).get(world_name, 'Unknown Shadow Cell'))
    righteous_enemy=int(sect_def.get('righteous_enemy',50)); observe=int(sect_def.get('karma_observation',-50)); initiate=int(sect_def.get('karma_initiation',-200))
    if hidden and karma>=righteous_enemy and str(hidden.get('status'))!='enemy':
        hidden=await DB.set_hidden_sect_status(interaction.user.id,'enemy',standing_delta=-100)
    if action.value=='status':
        if hidden:
            await interaction.response.send_message(
                f"🌑 **Heaven-Devouring Demon Sect**\nBranch: **{hidden['branch_name']}** • Rank **{hidden['rank_name']}** • Status **{hidden['status']}** • Standing **{hidden['standing']:+d}**\nKarma: **{karma:+d}**. Reaching righteous karma (**+{righteous_enemy}**) turns the hidden sect hostile.",ephemeral=False);return
        if karma<=observe:
            await interaction.response.send_message(f"🌫️ Your karma **{karma:+d}** has attracted unseen observation from **{branch}**, but you are not initiated.",ephemeral=False);return
        await interaction.response.send_message("🌫️ You find rumors and contradictory signs, but no hidden-sect contact reveals itself to this incarnation.",ephemeral=False);return
    if action.value=='investigate':
        if karma>=righteous_enemy:
            await interaction.response.send_message(f"☀️ Your righteous karma **{karma:+d}** marks you as a probable enemy. Shadow messengers avoid open contact; concealed hostility is more likely than recruitment.",ephemeral=False);return
        if karma<=initiate:
            await interaction.response.send_message(f"🌑 **{branch}** stops merely observing you. Your karma **{karma:+d}** satisfies the initiation gate. You may use **/sect → Shadow / Special → Accept Initiation**.",ephemeral=False);return
        if karma<=observe:
            await interaction.response.send_message(f"👁️ You detect a watcher from **{branch}**. Your karma **{karma:+d}** is dark enough for observation, but initiation requires **{initiate}** or lower.",ephemeral=False);return
        await interaction.response.send_message(f"You uncover only dead drops and false trails. A karma stain of **{observe}** or lower is normally required before the sect takes interest.",ephemeral=False);return
    if hidden and str(hidden.get('status'))=='active':
        await interaction.response.send_message("You are already an active hidden-sect initiate.",ephemeral=False);return
    if karma>initiate:
        await interaction.response.send_message(f"The initiation seal remains cold. Required karma: **{initiate} or lower**; yours is **{karma:+d}**.",ephemeral=False);return
    if karma>=righteous_enemy:
        await interaction.response.send_message("The hidden sect recognizes you as a righteous enemy, not a recruit.",ephemeral=False);return
    wt=await current_world_time(); hidden=await DB.initiate_hidden_sect(interaction.user.id,sect_name="Heaven-Devouring Demon Sect",branch_name=branch,game_minute=wt.total_minutes)
    candidates=[]
    for mid,m in WORLD.manuals.items():
        if str(m.get('alignment','')).casefold()!='demonic': continue
        if str(m.get('path',''))!=str(c.get('path','')): continue
        if int(m.get('min_realm_index',0))<=int(c.get('realm_index',0)): candidates.append((int(m.get('min_realm_index',0)),mid,m))
    granted=None
    if candidates:
        _,mid,m=max(candidates,key=lambda x:(x[0],x[1])); item_id=str(m.get('item_id',''))
        if item_id:
            await DB.add_items(interaction.user.id,{item_id:1}); granted=m
            await DB.record_item_provenance(interaction.user.id,item_id,source_type='hidden_sect_initiation',source_key=branch,ownership_mark='Heaven-Devouring Seal',legal_status='forbidden',tracking_strength=70,game_minute=wt.total_minutes)
    text=f"🌑 You accept the **Heaven-Devouring Demon Sect** initiation in **{branch}**. Hidden rank: **{hidden['rank_name']}**. This affiliation is stored separately from your public sect lineage."
    if granted: text+=f"\n📕 Initiation inheritance: **{granted['name']}** was placed in your inventory; study it with **/cultivation → Manuals & Techniques → Study**."
    await interaction.response.send_message(text,ephemeral=False)


@registered_group_command(sect_group, name="roster", description="Show your sect hierarchy, ranks, contribution and influence")
async def sect_roster(interaction:discord.Interaction)->None:
    c=await require_character(interaction)
    if not c:return
    membership=await DB.get_sect_membership(interaction.user.id)
    if not membership:
        await interaction.response.send_message("You are not recorded as a sect member.",ephemeral=False);return
    roster=await DB.get_sect_roster(str(membership['sect_name']))
    lines=[f"🏯 **{membership['sect_name']} — Hierarchy**"]
    for row in roster[:40]:
        lines.append(
            f"\n**{row['rank_name']}** — {row['name']} • "
            f"{WORLD.realm_name(int(row['realm_index']))} Stage {row['phase']} • "
            f"CP {row.get('contribution_points',0)} • Influence {row.get('influence',0)}"
        )
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(sect_group, name="politics", description="Show current sect influence, master attention and resource pressure")
async def sect_politics(interaction: discord.Interaction) -> None:
    if not await require_character(interaction):
        return
    membership = await DB.get_sect_membership(interaction.user.id)
    if not membership:
        await interaction.response.send_message("You are not a sect member.", ephemeral=False)
        return
    roster = await DB.get_sect_roster(str(membership["sect_name"]))
    treasury = await DB.get_sect_treasury(str(membership["sect_name"]))
    snap = await DB.get_lineage_snapshot(interaction.user.id)
    attention = snap.get("person", {}).get("master_attention", 0)
    sim_politics = await SIM.sect_status(str(membership["sect_name"]))
    lines = [
        f"🏯 **{membership['sect_name']} — Internal Politics**",
        f"Your rank: **{membership['rank_name']}**",
        f"Contribution points: **{membership.get('contribution_points', 0)}**",
        f"Institutional influence: **{membership.get('influence', 0)}**",
        f"Master attention: **{attention if snap.get('master') else 'No recorded master'}**",
    ]
    if sim_politics:
        lines.extend([
            f"Sect influence: **{sim_politics.get('influence',0)}** • Cohesion: **{sim_politics.get('cohesion',0)}/100** • Resources: **{sim_politics.get('resources',0)}**",
            f"Recruitment pressure: **{sim_politics.get('recruitment_pressure',0)}/100** • Doctrine pressure: **{sim_politics.get('doctrine_pressure',0)}/100**",
        ])
        factions=list(sim_politics.get('factions') or [])[:3]
        if factions:
            lines.append("\n**Autonomous internal factions**")
            for faction in factions:
                lines.append(f"• **{faction['faction_name']}** — power {faction['power']}% • loyalty {faction['loyalty']}/100\n  {faction['agenda']}")
        relations=list(sim_politics.get('relations') or [])[:4]
        if relations:
            lines.append("\n**External relations**")
            for relation in relations:
                lines.append(f"• **{relation['other']}** — {str(relation.get('relation_type','neutral')).title()} ({int(relation.get('relation_score',0)):+d}) • treaty {relation.get('treaty_status','none')}")
        events=list(sim_politics.get('events') or [])[:3]
        if events:
            lines.append("\n**Recent political incidents**")
            lines.extend(f"• {event['event_text']}" for event in events)
    lines.append("\n**Most influential members**")
    for row in sorted(roster, key=lambda r: (int(r.get('influence', 0)), int(r.get('rank_level', 0))), reverse=True)[:5]:
        lines.append(f"• {row['name']} — {row['rank_name']} • Influence {row.get('influence', 0)}")
    if treasury:
        scarce = sorted(treasury.items(), key=lambda kv: kv[1])[:5]
        lines.append("\n**Scarce stocked resources**")
        for item_id, qty in scarce:
            lines.append(f"• {WORLD.item_name(item_id)} x{qty}")
    else:
        lines.append("\n**Resource pressure:** the sect treasury is empty.")
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(sect_group, name="treasury", description="Inspect resources currently available to your sect")
async def sect_treasury(interaction:discord.Interaction)->None:
    if not await require_character(interaction):return
    membership=await DB.get_sect_membership(interaction.user.id)
    if not membership:
        await interaction.response.send_message("You are not a sect member.",ephemeral=False);return
    treasury=await DB.get_sect_treasury(str(membership['sect_name']))
    lines=[f"📦 **{membership['sect_name']} Treasury**",f"Your contribution points: **{membership.get('contribution_points',0)}**"]
    if not treasury: lines.append("*No contributed materials are currently stocked.*")
    else:
        sim_state=await SIM.sect_status(str(membership['sect_name']))
        resources=int((sim_state or {}).get('resources',50))
        pressure_mult=1.60 if resources<25 else 1.35 if resources<50 else 1.20 if resources<80 else 1.00
        lines.append(f"Autonomous resource pressure: **{resources}** • redemption multiplier **x{pressure_mult:.2f}**")
        for item_id,qty in treasury.items():
            cost=max(1,int(round(WORLD.item_sect_value(item_id)*pressure_mult)))
            lines.append(f"• {WORLD.item_name(item_id)} x{qty} — **{cost} CP each**")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(sect_group, name="contribute", description="Donate materials to your sect for contribution points and influence")
@app_commands.autocomplete(item=carried_item_autocomplete)
@serialized_user_action
async def sect_contribute(interaction:discord.Interaction,item:str,quantity:app_commands.Range[int,1,999999]=1)->None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("sect.contribute",interaction.user.id,{"item_id":item,"quantity":int(quantity)},action_id=f"discord:{interaction.id}:sect.contribute")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🏯 Contributed **{WORLD.item_name(item)} x{quantity}** to the sect treasury.",ephemeral=False)


async def sect_treasury_item_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    membership=await DB.get_sect_membership(interaction.user.id)
    if not membership:return []
    treasury=await DB.get_sect_treasury(str(membership['sect_name']));needle=current.casefold().strip();out=[]
    for item_id,qty in treasury.items():
        name=WORLD.item_name(item_id)
        if not needle or needle in name.casefold() or needle in item_id.casefold():out.append(app_commands.Choice(name=f"{name} x{qty}"[:100],value=item_id[:100]))
    return out[:25]


@registered_group_command(sect_group, name="redeem", description="Exchange contribution points for stocked sect resources")
@app_commands.autocomplete(item=sect_treasury_item_autocomplete)
@serialized_user_action
async def sect_redeem(interaction:discord.Interaction,item:str,quantity:app_commands.Range[int,1,999999]=1)->None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("sect.redeem",interaction.user.id,{"item_id":item,"quantity":int(quantity)},action_id=f"discord:{interaction.id}:sect.redeem")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🏯 Redeemed **{WORLD.item_name(item)} x{quantity}** from the sect treasury.",ephemeral=False)


@registered_group_command(sect_manor_group, name="status", description="Inspect your sect's shared manor, facilities and recent construction")
async def sect_manor_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    membership = await DB.get_sect_membership(interaction.user.id)
    if not membership:
        await interaction.response.send_message("You are not a sect member.", ephemeral=False)
        return
    sect_name = str(membership["sect_name"])
    manor = await DB.get_sect_manor(sect_name)
    treasury = await DB.get_sect_treasury(sect_name)
    if not manor:
        lines = [
            f"🏯 **{sect_name} — No Sect Manor Yet**",
            "A Sect Master or Ancestor can establish one at a normal world location once the shared treasury holds the foundation materials.",
            f"Foundation cost: **{WORLD.item_names(SECT_MANOR_ESTABLISHMENT_COST)}**",
            f"Current treasury toward foundation: **{WORLD.item_names({k: min(v, treasury.get(k,0)) for k,v in SECT_MANOR_ESTABLISHMENT_COST.items()})}**",
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=False)
        return
    lines = [
        f"🏯 **{manor['name']} — {sect_name}**",
        f"Seat: **{manor['base_location']}**",
        f"Your rank: **{membership['rank_name']}** • Contribution Points: **{membership.get('contribution_points',0)}**",
        "",
        "**Facilities & active benefits**",
    ]
    lines.extend(f"• {line}" for line in manor_benefit_lines(manor))
    lines.append("\n**Next upgrades**")
    for key, definition in SECT_MANOR_FACILITIES.items():
        level = int(manor.get(str(definition["column"]), 0))
        if level >= MAX_MANOR_FACILITY_LEVEL:
            lines.append(f"• **{definition['name']}** — MAX Lv.{MAX_MANOR_FACILITY_LEVEL}")
        else:
            lines.append(f"• **{definition['name']}** Lv.{level} → Lv.{level+1}: {WORLD.item_names(manor_upgrade_cost(key, level))}")
    projects = await DB.get_sect_manor_projects(sect_name, limit=5)
    if projects:
        lines.append("\n**Recent construction**")
        for project in projects:
            if str(project.get("project_type")) == "establish":
                lines.append(f"• Foundation established • {WORLD.item_names(project.get('cost',{}))}")
            else:
                facility = SECT_MANOR_FACILITIES.get(str(project.get("facility_key")), {})
                lines.append(
                    f"• {facility.get('name', project.get('facility_key','Facility'))} "
                    f"Lv.{project.get('from_level',0)} → Lv.{project.get('to_level',0)} • {WORLD.item_names(project.get('cost',{}))}"
                )
    await reply_long(interaction, "\n".join(lines), ephemeral=False)


@registered_group_command(sect_manor_group, name="establish", description="Spend shared treasury materials to establish the sect's one persistent manor")
@serialized_user_action
async def sect_manor_establish(interaction: discord.Interaction, name: str, confirm: bool = False) -> None:
    c=await require_character(interaction)
    if not c:return
    if not confirm:
        await interaction.response.send_message(f"🏯 Establish **{name[:80]}** here? Repeat with **confirm:true** to lay the foundation.",ephemeral=False);return
    wt=await current_world_time()
    try:
        e=await ENGINE.authoritative_action("sect.manor.establish",interaction.user.id,{"name":name},action_id=f"discord:{interaction.id}:sect.manor.establish"); r=dict(e.get('result') or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False);return
    await interaction.response.send_message(f"🏯 **{r.get('name',name)}** is established at **{r.get('base_location',c.get('location'))}**.",ephemeral=False)


@registered_group_command(sect_manor_group, name="upgrade", description="Upgrade a shared manor facility using materials from the sect treasury")
@app_commands.choices(facility=SECT_MANOR_FACILITY_CHOICES)
@serialized_user_action
async def sect_manor_upgrade(
    interaction: discord.Interaction, facility: app_commands.Choice[str], confirm: bool = False
) -> None:
    if not await require_character(interaction):return
    if not confirm:
        await interaction.response.send_message(f"🏗️ Upgrade **{facility.name}**? Repeat with **confirm:true** to spend shared treasury materials.",ephemeral=False);return
    wt=await current_world_time()
    try:
        e=await ENGINE.authoritative_action("sect.manor.upgrade",interaction.user.id,{"facility":facility.value},action_id=f"discord:{interaction.id}:sect.manor.upgrade"); r=dict(e.get('result') or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False);return
    await interaction.response.send_message(f"🏗️ **{facility.name} upgraded to Lv.{r.get('level','?')}**.",ephemeral=False)


@registered_group_command(sect_group, name="address", description="Show the proper English martial-family title for another cultivator")
async def sect_address(
    interaction: discord.Interaction, member: discord.Member, show_chinese: bool = False
) -> None:
    observer = await require_character(interaction)
    if not observer:
        return
    target = await DB.get_character(member.id)
    if not target:
        await interaction.response.send_message("That member has no cultivation character.", ephemeral=False)
        return
    result = await DB.get_address_context(interaction.user.id, member.id)
    if not result:
        await interaction.response.send_message("No relationship information could be resolved.", ephemeral=False)
        return
    display = result["display_chinese"] if show_chinese else result["display"]
    await interaction.response.send_message(
        f"🪷 **{observer['name']} → {target['name']}**\n{display}", ephemeral=False
    )


@registered_group_command(sect_group, name="family", description="Check your martial-family tree")
async def sect_family(interaction: discord.Interaction, show_chinese: bool = False) -> None:
    c = await require_character(interaction)
    if not c:
        return
    text = await _build_family_text(interaction.user.id, show_chinese=show_chinese)
    if not text:
        await interaction.response.send_message("No sect lineage is recorded for your character.", ephemeral=False)
        return
    await reply_long(interaction, text, ephemeral=False)


family_group=app_commands.Group(name="family",description="Your NPC birth family, relatives, descendants, support and family fortunes")

async def _current_birth_family(user_id:int, *, simulate:bool=True)->dict|None:
    fam=await DB.get_birth_family(user_id)
    if not fam or not simulate:
        return fam
    c=await DB.get_character(user_id)
    wt=await current_world_time()
    if c and c.get("life_status")=="deceased":
        return fam
    try:
        await ENGINE.authoritative_action("family.simulate",int(user_id),{"family_id":int(fam['family_id']),"minutes_per_year":MINUTES_PER_YEAR},action_id=f"family:auto:{int(user_id)}:{wt.total_minutes}")
    except GameEngineError:
        log.exception("Go family simulation failed")
    return await DB.get_birth_family(user_id) or fam

@registered_group_command(family_group, name="view",description="View the NPC family you were born into")
async def birth_family_view(interaction:discord.Interaction)->None:
    c=await require_character(interaction,allow_deceased=True)
    if not c:return
    fam=await _current_birth_family(interaction.user.id)
    if not fam:
        await interaction.response.send_message("No birth family is recorded for this character.",ephemeral=False);return
    lines=[
        f"🏠 **{fam['family_name']} — {family_tier_name(int(fam.get('tier',1)))}**",
        f"Background: **{str(fam.get('archetype','family')).replace('_',' ').title()}**",
        f"Home: **{fam.get('location','Unknown')}**",
        f"Generation: **{fam.get('member_generation',1)}** • Your birth order: **#{fam.get('birth_order',1)}**",
        f"Family head: **{fam.get('head_title','Family Head')} {fam.get('head_name','Unknown')}** — {WORLD.realm_name(int(fam.get('head_realm_index',0)))} Stage {fam.get('head_phase',1)}",
        f"Wealth **{fam.get('wealth',0)}/100** • Influence **{fam.get('influence',0)}/100** • Stability **{fam.get('stability',0)}/100**",
        f"Bloodline status: **{str(fam.get('line_status','active')).title()}**",
        (f"Clan structure: **{str(fam.get('clan_structure','extended_household')).replace('_',' ').title()}**"),
        (f"Ancestral bloodline: **{fam.get('bloodline_name','None')}** • Purity **{fam.get('bloodline_purity',0)}%**" if int(fam.get('bloodline_purity',0)) > 0 else "Ancestral bloodline: **None awakened**"),
        "\n**Close relatives**",
    ]
    for npc in fam.get('npcs',[])[:12]:
        status_note = " ☠️" if npc.get("status")=="deceased" else ""
        lines.append(f"• **{npc['relation']}** — {npc['name']} • {WORLD.realm_name(int(npc.get('realm_index',0)))} Stage {npc.get('phase',1)}{status_note}")
    if c.get("life_status")=="deceased":
        state=await DB.get_reincarnation_state(interaction.user.id)
        lines.extend([
            "",
            "🕯️ **Past-Life Family**",
            "This household is **not** accelerated by your death. It continues only with normal shared world time.",
        ])
        if state:
            lines.append("Your soul is currently in **Samsara**; use **/character → Samsara** for the reincarnation clock.")
    household_key = f"birth_family:{int(fam.get('family_id') or 0)}"
    if str(c.get("location") or "") == household_key:
        lines.extend(["", "🏠 **You are currently inside this shared household.** Other player members who enter are present in the same family scene."])
    else:
        lines.extend(["", "🏠 Use **/family enter** to visit the shared household. Players born into this same starter family meet in the same scene."])
    await reply_long(interaction,"\n".join(lines),ephemeral=False)

@registered_group_command(family_group, name="enter", description="Enter your shared birth-family household")
@serialized_user_action
async def birth_family_enter(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    fam = await DB.get_birth_family(interaction.user.id)
    if not fam:
        await interaction.response.send_message("No birth family is recorded.", ephemeral=False)
        return
    wt = await current_world_time()
    await interaction.response.defer(ephemeral=False)
    try:
        envelope = await ENGINE.authoritative_action(
            "family.household.enter",
            interaction.user.id,
            {},
            action_id=f"discord:{interaction.id}:family.household.enter",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ Could not enter the household: {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    thread = await ensure_birth_family_household_thread(interaction, fam)
    players = [str(row.get("name") or "Cultivator") for row in (result.get("players_present") or [])]
    others = [name for name in players if name != str(c.get("name") or "")]
    presence = f"\nPresent with you: **{', '.join(others)}**" if others else "\nYou are currently the only player member inside."
    if thread is not None:
        await interaction.followup.send(
            f"🏠 Entered **{result.get('family_name') or fam.get('family_name')}**. Shared household scene: {thread.mention}{presence}",
            ephemeral=False,
        )
    else:
        await interaction.followup.send(
            f"🏠 Entered **{result.get('family_name') or fam.get('family_name')}**.{presence}\n"
            "The canonical shared location is active, but Discord could not create/recover its household thread.",
            ephemeral=False,
        )

@registered_group_command(family_group, name="leave", description="Leave your shared birth-family household")
@serialized_user_action
async def birth_family_leave(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    fam = await DB.get_birth_family(interaction.user.id)
    if not fam:
        await interaction.response.send_message("No birth family is recorded.", ephemeral=False)
        return
    wt = await current_world_time()
    household_row = None
    if interaction.guild is not None:
        household_row = await DB.get_birth_family_household_thread(interaction.guild.id, int(fam["family_id"]))
    await interaction.response.defer(ephemeral=False)
    try:
        envelope = await ENGINE.authoritative_action(
            "family.household.leave",
            interaction.user.id,
            {},
            action_id=f"discord:{interaction.id}:family.household.leave",
        )
    except GameEngineError as exc:
        await interaction.followup.send(f"❌ Could not leave the household: {exc}", ephemeral=False)
        return
    result = dict(envelope.get("result") or {})
    if interaction.guild is not None and household_row:
        thread = await _get_thread(interaction.guild, household_row.get("thread_id"))
        if thread is not None:
            try:
                await thread.remove_user(interaction.user)
            except (discord.Forbidden, discord.HTTPException):
                pass
    await interaction.followup.send(
        f"🚪 Left **{result.get('family_name') or fam.get('family_name')}** and returned to **{result.get('location') or fam.get('location')}**.",
        ephemeral=False,
    )

@registered_group_command(family_group, name="clan",description="View bloodline, branches, retainers and martial-clan alliance ties")
async def birth_family_clan(interaction:discord.Interaction)->None:
    if not await require_character(interaction,allow_deceased=True):return
    fam=await _current_birth_family(interaction.user.id)
    if not fam:
        await interaction.response.send_message("No birth family is recorded.",ephemeral=False);return
    purity=max(0,int(fam.get('bloodline_purity',0)))
    if purity <= 0:
        text=(f"🪶 **{fam['family_name']} — Extended Household**\n"
              "No awakened ancestral bloodline is currently recorded. A household can still rise into clan status through wealth, cultivation, marriage, inheritance, or major world events.")
    else:
        text=(f"🩸 **{fam['family_name']} — Clan Record**\n"
              f"Structure: **{str(fam.get('clan_structure','bloodline_clan')).replace('_',' ').title()}**\n"
              f"Bloodline: **{fam.get('bloodline_name','Unknown')}**\n"
              f"Affinity: **{fam.get('bloodline_affinity','Unknown')}**\n"
              f"Purity: **{purity}%**\n"
              f"Inherited tendency: {fam.get('bloodline_trait','Unknown')}\n\n"
              f"Branches: **{fam.get('branch_count',1)}** • Retainers/adopted household members: **{fam.get('retainer_count',0)}**\n"
              f"Martial alliance: **{fam.get('confederacy_name','Independent')}**\n\n"
              "Bloodline purity improves the chance that descendants inherit family cultivation potential, but it never guarantees a Spiritual Root or successful breakthrough.")
    clan = await SIM.clan_status(int(fam['family_id']))
    details=[]
    branches=list(clan.get('branches') or [])
    retainers=list(clan.get('retainers') or [])
    relations=list(clan.get('relations') or [])
    if branches:
        details.append("\n\n**Mechanical branches**")
        for branch in branches[:8]:
            details.append(f"• **{branch['branch_name']}** — {branch['status']} • strength {branch['martial_strength']} • loyalty {branch['loyalty']}/100 • ~{branch['members_estimate']} members")
    if retainers:
        details.append("\n**Retainer groups**")
        for group in retainers[:8]:
            details.append(f"• **{group['group_name']}** — {group['role']} • {group['members']} members • loyalty {group['loyalty']}/100 • {group['status']}")
    if relations:
        details.append("\n**Clan alliances / rivalries**")
        for relation in relations[:8]:
            details.append(f"• **{relation['partner_name']}** — {str(relation['relation_type']).replace('_',' ').title()} ({int(relation['relation_score']):+d})")
    await reply_long(interaction,text+"\n".join(details),ephemeral=False)


@registered_group_command(family_group, name="support",description="Ask your birth family for resources or emergency support")
@serialized_user_action
async def birth_family_support(interaction:discord.Interaction)->None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("family.support",interaction.user.id,{"cooldown_game_minutes":3*MINUTES_PER_MONTH},action_id=f"discord:{interaction.id}:family.support")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🏠 **{result.get('family_name','Your family')} supports you.**\nReceived: **{int(result.get('stones',0))} Low-Grade Spirit Stones**",ephemeral=False)

@registered_group_command(family_group, name="history",description="View recent rises, setbacks and political changes in your family")
async def birth_family_history(interaction:discord.Interaction)->None:
    if not await require_character(interaction,allow_deceased=True):return
    fam=await _current_birth_family(interaction.user.id)
    if not fam:
        await interaction.response.send_message("No birth family is recorded.",ephemeral=False);return
    history=list(fam.get('history',[]))[-15:]
    text=f"📜 **{fam['family_name']} — Recent History**\n"+("\n".join(f"• {x}" for x in history) if history else "No major family events have been recorded yet.")
    await reply_long(interaction,text,ephemeral=False)

@registered_group_command(family_group, name="child",description="Add a child to your family branch; descendants may awaken cultivation talent")
@app_commands.choices(gender=GENDER_CHOICES)
@serialized_user_action
async def birth_family_child(interaction:discord.Interaction,name:str,gender:app_commands.Choice[str])->None:
    c=await require_character(interaction)
    if not c:return
    wt=await current_world_time(); life=await authoritative_lifespan(interaction.user.id)
    if life.age_years<18:
        await interaction.response.send_message("Your character must be at least **18 years old** to have a recorded child.",ephemeral=False);return
    fam=await _current_birth_family(interaction.user.id)
    if not fam:
        await interaction.response.send_message("No birth family is recorded.",ephemeral=False);return
    try:
        e=await ENGINE.authoritative_action("family.add_child",interaction.user.id,{"name":name,"gender":gender.value},action_id=f"discord:{interaction.id}:family.add_child"); result=dict(e.get('result') or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False);return
    await interaction.response.send_message(f"👶 **{name.strip()}** is born as descendant `#{result.get('child_id')}`.",ephemeral=False)

@registered_group_command(family_group, name="descendants",description="View descendants in your family branch and whether they can cultivate")
async def birth_family_descendants(interaction:discord.Interaction)->None:
    if not await require_character(interaction,allow_deceased=True):return
    fam=await _current_birth_family(interaction.user.id)
    if not fam:
        await interaction.response.send_message("No birth family is recorded.",ephemeral=False);return
    wt=await current_world_time(); rows=[n for n in fam.get('npcs',[]) if str(n.get('relation','')).startswith('Child of user')]
    if not rows:
        await interaction.response.send_message(f"🌿 **{fam['family_name']}** has no recorded descendants in your branch yet.",ephemeral=False);return
    lines=[f"🌿 **{fam['family_name']} — Your Descendants**"]
    for child in rows[:30]:
        age=max(0,wt.total_minutes-int(child.get('birth_game_minute',wt.total_minutes)))/MINUTES_PER_YEAR
        if age>=CHILD_CULTIVATION_AWAKENING_AGE:
            talent=(f"Awakened **{child['spiritual_root']}**" if child.get('spiritual_root')!='Mortal Root' else "No usable spiritual root awakened; currently walking a mortal path")
        else:
            talent=f"Cultivation talent unawakened until around age {CHILD_CULTIVATION_AWAKENING_AGE}"
        lines.append(f"• **{child['name']}** — age **{age:.1f}**\n  {talent}")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)



civilization_group=app_commands.Group(name="civilization",description="Inspect the living population, security and activity of world regions")
market_group=app_commands.Group(name="market",description="Use the dynamic local cultivation economy")
blackmarket_group=app_commands.Group(name="blackmarket",description="Find and trade with rotating underworld cultivation posts")


async def _black_market_access(user_id:int, character:dict[str,Any]) -> tuple[str|None,int,str]:
    reps = await DB.get_reputations(user_id)
    underworld = next((int(r.get("score",0)) for r in reps if str(r.get("faction_key","")).casefold()=="underworld contacts"), 0)
    membership = await DB.get_sect_membership(user_id)
    alignment = ""
    if membership:
        alignment = str((WORLD.sects.get(str(membership.get("sect_name"))) or {}).get("alignment", ""))
    reason = black_market_access_reason(
        karma=int(character.get("karma_score",0)), underworld_reputation=underworld, sect_alignment=alignment
    )
    return reason, underworld, alignment


@registered_group_command(civilization_group, name="status",description="View population, prosperity, security and recent incidents in a region")
async def civilization_status_command(interaction:discord.Interaction,location:str|None=None)->None:
    c=await require_character(interaction,allow_deceased=True)
    if not c:return
    target=(location or str(c.get('location') or '')).strip()
    if not await _location_is_visible(interaction.user.id, c, target):
        await interaction.response.send_message("That region has not been discovered by your character.", ephemeral=False); return
    data=await SIM.civilization_status(target)
    if not data:
        await interaction.response.send_message("That location has no civilization simulation record.",ephemeral=False);return
    lines=[
        f"🏙️ **Civilization — {target}**",
        f"World: **{data.get('world_name','Unknown')}**",
        f"Population: **{int(data.get('population',0)):,}**",
        f"Prosperity **{data.get('prosperity',0)}/100** • Security **{data.get('security',0)}/100** • Unrest **{data.get('unrest',0)}/100**",
        f"Spirit resources **{data.get('spirit_resources',0)}/100** • Food supply **{data.get('food_supply',0)}/100** • Migration **{int(data.get('migration_pressure',0)):+d}**",
    ]
    if data.get('npcs'):
        lines.append("\n**Notable active NPCs**")
        for npc in data['npcs'][:8]:
            public=WORLD.npcs.get(str(npc['npc_name']),{})
            public_power=str(public.get('realm','Unknown'))
            if public.get('stage'): public_power+=f" Stage {public.get('stage')}"
            lines.append(f"• **{npc['npc_name']}** — {npc.get('activity','active')} • {public_power}")
    if data.get('events'):
        lines.append("\n**Recent regional incidents**")
        lines.extend(f"• {e['event_text']}" for e in data['events'][:5])
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@civilization_status_command.autocomplete("location")
async def civilization_location_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    return await location_autocomplete(interaction, current)


@registered_group_command(civilization_group, name="npcs",description="View the named NPCs currently active in a region")
async def civilization_npcs_command(interaction:discord.Interaction,location:str|None=None)->None:
    c=await require_character(interaction,allow_deceased=True)
    if not c:return
    target=(location or str(c.get('location') or '')).strip()
    if not await _location_is_visible(interaction.user.id, c, target):
        await interaction.response.send_message("That region has not been discovered by your character.", ephemeral=False); return
    data=await SIM.civilization_status(target)
    if not data:
        await interaction.response.send_message("That location has no civilization simulation record.",ephemeral=False);return
    npcs=list(data.get('npcs') or [])
    if not npcs:
        await interaction.response.send_message(f"No named simulated NPCs are currently recorded in **{target}**.",ephemeral=False);return
    lines=[f"🧑‍🤝‍🧑 **Named NPC activity — {target}**"]
    for npc in npcs:
        public=WORLD.npcs.get(str(npc['npc_name']),{})
        public_power=str(public.get('realm','Unknown'))
        if public.get('stage'): public_power+=f" Stage {public.get('stage')}"
        lines.append(f"• **{npc['npc_name']}** — {npc.get('profession','Unknown role')}\n  {npc.get('activity','Following routine')} • {public_power}")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@civilization_npcs_command.autocomplete("location")
async def civilization_npcs_location_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    return await civilization_location_autocomplete(interaction,current)


def _market_item_matches(current:str)->list[app_commands.Choice[str]]:
    q=current.lower().strip(); out=[]
    for iid,item in WORLD.items.items():
        if not SIM.market_allows_item(iid):
            continue
        name=str(item.get('name',iid))
        if q and q not in name.lower() and q not in iid.lower(): continue
        out.append(app_commands.Choice(name=name[:100],value=iid[:100]))
        if len(out)>=25:break
    return out


@registered_group_command(blackmarket_group, name="rumors", description="Use underworld contacts to locate the current hidden posts in each realm world")
async def blackmarket_rumors(interaction: discord.Interaction) -> None:
    c = await require_character(interaction)
    if not c:
        return
    reason, rep, _ = await _black_market_access(interaction.user.id, c)
    if not reason:
        await interaction.response.send_message(
            f"🌑 The underworld does not trust you yet. Access requires dark Karma, a demonic-sect introduction, "
            f"or **Underworld Contacts {15}+** (yours: **{rep:+d}**).", ephemeral=False
        )
        return
    wt = await current_world_time()
    known = await _known_locations(interaction.user.id, c)
    posts = [
        post for post in await DB.list_active_black_markets(wt.total_minutes)
        if str(post.get("location")) in known and _world_is_unlocked(c, str(post.get("world_name")))
    ]
    if not posts:
        await interaction.response.send_message("The underworld routes are quiet right now.", ephemeral=False); return
    lines=[f"🌑 **Black-Market Rumors** — Access: {reason}."]
    for post in posts:
        lines.append(
            f"• **{post['world_name']}** — **{post['location']}** "
            f"• heat **{post['heat']}/100** • closes in **{max(0,int(post['closes_game_minute'])-wt.total_minutes)} game min**"
        )
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(blackmarket_group, name="status", description="Inspect an accessible black-market post at your current location")
async def blackmarket_status(interaction: discord.Interaction) -> None:
    c=await require_character(interaction)
    if not c:return
    reason,rep,_=await _black_market_access(interaction.user.id,c)
    if not reason:
        await interaction.response.send_message(f"🌑 No broker will deal with you yet. Underworld Contacts: **{rep:+d}**.",ephemeral=False);return
    wt=await current_world_time(); post=await DB.get_active_black_market(str(c.get('location','')),wt.total_minutes)
    if not post:
        await interaction.response.send_message("No active underworld post is hidden at your current location. Use **Economy → Black Market → Rumors**.",ephemeral=False);return
    lines=[f"🌑 **Hidden Trading Post — {post['location']}**",f"Heat: **{post['heat']}/100** • closes in **{max(0,int(post['closes_game_minute'])-wt.total_minutes)} game min**"]
    for row in post.get('stock',[]): lines.append(f"• **{WORLD.item_name(str(row['item_id']))}** x{row['quantity']} — **{row['unit_price']:,} {WORLD.currency_name(str(row['currency_id']))}** • {row['legal_status']}")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


async def _black_market_item_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    c=await DB.get_character(interaction.user.id)
    if not c:return []
    reason,_,_=await _black_market_access(interaction.user.id,c)
    if not reason:return []
    wt=await current_world_time(); post=await DB.get_active_black_market(str(c.get('location','')),wt.total_minutes)
    if not post:return []
    q=current.casefold().strip(); out=[]
    for row in post.get('stock',[]):
        iid=str(row['item_id']); name=WORLD.item_name(iid)
        if q and q not in iid.casefold() and q not in name.casefold():continue
        out.append(app_commands.Choice(name=f"{name} ({row['quantity']} left)"[:100],value=iid[:100]))
    return out[:25]


@registered_group_command(blackmarket_group, name="buy", description="Buy forbidden or scarce goods from the hidden post at your location")
@serialized_user_action
async def blackmarket_buy(interaction:discord.Interaction,item:str,quantity:app_commands.Range[int,1,20]=1)->None:
    c=await require_character(interaction)
    if not c:return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("black_market.trade",interaction.user.id,{"location":str(c.get('location','')),"item_id":item,"quantity":int(quantity),"buy":True},action_id=f"discord:{interaction.id}:black_market.trade")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🌑 Bought **{WORLD.item_name(item)} x{quantity}** for **{result.get('total_price',0)}** stones.",ephemeral=False)


@blackmarket_buy.autocomplete("item")
async def blackmarket_buy_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    return await _black_market_item_autocomplete(interaction,current)


@registered_group_command(blackmarket_group, name="sell", description="Fence a carried item through the hidden post at your location")
@serialized_user_action
async def blackmarket_sell(interaction:discord.Interaction,item:str,quantity:app_commands.Range[int,1,20]=1)->None:
    c=await require_character(interaction)
    if not c:return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("black_market.trade",interaction.user.id,{"location":str(c.get('location','')),"item_id":item,"quantity":int(quantity),"buy":False},action_id=f"discord:{interaction.id}:black_market.trade")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🌑 Sold **{WORLD.item_name(item)} x{quantity}** for **{result.get('total_price',0)}** stones.",ephemeral=False)


@blackmarket_sell.autocomplete("item")
async def blackmarket_sell_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    return await _black_market_item_autocomplete(interaction,current)


@registered_group_command(market_group, name="prices",description="View dynamic local prices, supply and demand")
async def market_prices_command(interaction:discord.Interaction,item:str|None=None)->None:
    c=await require_character(interaction,allow_deceased=True)
    if not c:return
    location=str(c.get('location') or '')
    if item:
        quote=await SIM.market_quote(location,item)
        if not quote:
            await interaction.response.send_message("That item is not traded in your current local market.",ephemeral=False);return
        await interaction.response.send_message(
            f"💹 **{WORLD.item_name(item)} — {location}**\n"
            f"Buy: **{quote['buy_price']:,} {WORLD.currency_name(str(quote['currency_id']))}** • Sell: **{quote['sell_price']:,}**\n"
            f"Supply **{quote['supply']}** • Demand **{quote['demand']}** • Price index **x{float(quote['price_index']):.2f}**",
            ephemeral=False,
        );return
    rows=await SIM.market_rows(location,18)
    if not rows:
        await interaction.response.send_message("No public market is simulated at your current location.",ephemeral=False);return
    lines=[f"💹 **Dynamic Market — {location}**"]
    for row in rows:
        price=max(1,int(round(int(row['base_price'])*float(row['price_index']))))
        lines.append(f"• **{WORLD.item_name(str(row['item_id']))}** — {price:,} {WORLD.currency_name(str(row['currency_id']))} • supply {row['supply']} / demand {row['demand']} • x{float(row['price_index']):.2f}")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@market_prices_command.autocomplete("item")
async def market_prices_item_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    return _market_item_matches(current)


@registered_group_command(market_group, name="buy",description="Buy an item from the current dynamic market")
@serialized_user_action
async def market_buy_command(interaction:discord.Interaction,item:str,quantity:app_commands.Range[int,1,100]=1)->None:
    c=await require_character(interaction)
    if not c:return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("market.trade",interaction.user.id,{"location":str(c.get('location','')),"item_id":item,"quantity":int(quantity),"buy":True},action_id=f"discord:{interaction.id}:market.trade")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🪙 Bought **{WORLD.item_name(item)} x{quantity}** for **{result.get('total_price',0)}** stones.",ephemeral=False)


@market_buy_command.autocomplete("item")
async def market_buy_item_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    return _market_item_matches(current)


@registered_group_command(market_group, name="sell",description="Sell carried items into the current dynamic market")
@serialized_user_action
async def market_sell_command(interaction:discord.Interaction,item:str,quantity:app_commands.Range[int,1,100]=1)->None:
    c=await require_character(interaction)
    if not c:return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("market.trade",interaction.user.id,{"location":str(c.get('location','')),"item_id":item,"quantity":int(quantity),"buy":False},action_id=f"discord:{interaction.id}:market.trade")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"🪙 Sold **{WORLD.item_name(item)} x{quantity}** for **{result.get('total_price',0)}** stones.",ephemeral=False)


@market_sell_command.autocomplete("item")
async def market_sell_item_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    return _market_item_matches(current)

@registered_root_command(name="lifespan",description="View your age, realm lifespan range and remaining longevity",guild=GUILD)
async def lifespan_command(interaction:discord.Interaction)->None:
    c=await require_character(interaction,allow_deceased=True)
    if not c:return
    wt=await current_world_time(); life=await authoritative_lifespan(interaction.user.id)
    realm=WORLD.realm_name(int(c.get('realm_index',0))); phase=int(c.get('phase',1))
    if life.ageless:
        ceiling="**Ageless / no natural-aging death**"
        range_line="True Immortal-tier longevity has transcended ordinary mortal aging."
    else:
        ceiling=f"**{int(life.total_years or 0):,} years**"
        if life.realm_floor_years is not None:
            range_line=f"Realm range: **{life.realm_floor_years:,}–{life.realm_ceiling_years:,} years**; Stage {phase} determines where you sit inside that range."
        else:
            range_line="Realm range: mortal baseline."
    remain=("∞" if life.remaining_years is None else f"{life.remaining_years:,.1f} years")
    pause_line = ""
    if getattr(life, "aging_paused", False):
        paused_years = float(getattr(life, "paused_game_minutes", 0) or 0) / float(MINUTES_PER_YEAR)
        pause_line = (
            f"\n⏸️ Inactivity protection is active. **{paused_years:,.1f} game-years** "
            "of biological aging are currently excluded."
        )
    await interaction.response.send_message(
        f"🕯️ **Lifespan — {c['name']}**\n"
        f"Realm: **{realm}, Stage {phase}**\n"
        f"Age: **{life.age_years:,.1f} years**\n"
        f"Natural mortal lifespan: **{life.natural_years:,} years**\n"
        f"Cultivation/body longevity contribution: **+{life.cultivation_bonus_years:,} years**\n"
        f"Life-extension medicines/herbs: **+{life.extension_years:,} years**\n"
        f"Current lifespan ceiling: {ceiling}\n"
        f"Remaining: **{remain}**{pause_line}\n\n{range_line}\n\n"
        "Natural longevity does not protect against combat death, tribulations, curses, soul destruction, or explicit life-draining techniques.",
        ephemeral=False,
    )


@registered_root_command(name="karma",description="View your Good/Evil alignment and karmic reputation",guild=GUILD)
async def karma_status_command(interaction:discord.Interaction)->None:
    c=await require_character(interaction,allow_deceased=True)
    if not c:return
    score=int(c.get('karma_score',0))
    await interaction.response.send_message(
        f"☯️ **Karmic Alignment — {c['name']}**\n"
        f"Path: **{karma_label(score)}**\nScore: **{score:+d} / 1000**\n\n{karma_description(score)}\n\n"
        "Karma is changed by canonical quests, choices, crimes, mercy, betrayals and GM/world events — not by repeatedly self-reporting actions.",
        ephemeral=False,
    )

fate_group = app_commands.Group(name="fate", description="Inspect the providence you can spend to resist a fatal turn")


@registered_group_command(fate_group, name="status", description="View your spendable Fate reserve and lifetime fortune")
async def fate_status(interaction: discord.Interaction) -> None:
    c = await require_character(interaction, allow_deceased=True)
    if not c:
        return
    state = await DB.get_fate(interaction.user.id)
    points = int(state.get("points", 0))
    await interaction.response.send_message(
        f"🧧 **Fate — {c['name']}**\n"
        f"Reserve: **{points}/9** • {fate_label(points)}\n"
        f"Lifetime earned: **{int(state.get('lifetime_earned',0))}** • spent: **{int(state.get('lifetime_spent',0))}**\n\n"
        "Fate is distinct from Karma. Rare fortuitous events, meaningful mercy and cleared heavenly tribulations can grant it. "
        "If an ordinary battle would cause **true death**, one Fate is automatically burned to twist the fatal outcome into survival with a severe injury.",
        ephemeral=False,
    )


@registered_group_command(fate_group, name="history", description="Review recent Fate gains and expenditures")
async def fate_history(interaction: discord.Interaction) -> None:
    c = await require_character(interaction, allow_deceased=True)
    if not c:
        return
    rows = await DB.get_fate_ledger(interaction.user.id, limit=12)
    if not rows:
        await interaction.response.send_message("🧧 No Fate has been gained or spent by this incarnation yet.", ephemeral=False)
        return
    lines = [f"🧧 **Recent Fate — {c['name']}**"]
    for row in rows:
        lines.append(
            f"\n• **{int(row['delta']):+d}** → {int(row['balance_after'])}/9"
            + (f" — {row.get('reason')}" if row.get('reason') else "")
        )
    await reply_long(interaction, "".join(lines), ephemeral=False)


bond_group = app_commands.Group(name="bond", description="Form a consensual Dao partnership and cultivate together")


@registered_group_command(bond_group, name="status", description="View your pending or active Dao partnership")
async def bond_status(interaction:discord.Interaction)->None:
    c=await require_character(interaction,allow_deceased=True)
    if not c:return
    bond=await DB.get_dao_partnership(interaction.user.id)
    if not bond:
        await interaction.response.send_message("💞 You have no pending or active Dao partnership.",ephemeral=False);return
    await interaction.response.send_message(
        f"💞 **Dao Partnership — {c['name']}**\nPartner: **{bond['partner_name']}** • Status: **{str(bond['status']).title()}**\n"
        f"Dual-Cultivation Resonance: **{int(bond.get('resonance',0))}/100** • Sessions: **{int(bond.get('dual_sessions',0))}**\n"
        "An active high-resonance bond leaves a small **Partner Echo** in Samsara if this incarnation dies.",ephemeral=False
    )


@registered_group_command(bond_group, name="propose", description="Invite another cultivator into a consensual Dao partnership")
@serialized_user_action
async def bond_propose(interaction:discord.Interaction,partner:discord.Member)->None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("dao.propose",interaction.user.id,{"partner_user_id":partner.id},action_id=f"discord:{interaction.id}:dao.propose")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"💞 Dao-partnership proposal **#{result.get('partnership_id')}** sent to {partner.mention}.",ephemeral=False)


@registered_group_command(bond_group, name="respond", description="Accept or reject a Dao-partnership proposal addressed to you")
@app_commands.choices(decision=[app_commands.Choice(name="Accept",value="accept"),app_commands.Choice(name="Reject",value="reject")])
@serialized_user_action
async def bond_respond(interaction:discord.Interaction,partnership_id:int,decision:app_commands.Choice[str])->None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("dao.respond",interaction.user.id,{"partnership_id":int(partnership_id),"accept":decision.value=='accept'},action_id=f"discord:{interaction.id}:dao.respond")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message("💞 Dao partnership response recorded.",ephemeral=False)


@registered_group_command(bond_group, name="dual_cultivate", description="Cultivate with your accepted Dao partner while both are at the same location")
@serialized_user_action
async def bond_dual_cultivate(interaction:discord.Interaction)->None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("dao.dual_cultivate",interaction.user.id,{"cooldown_seconds":SETTINGS.cultivate_cooldown_minutes*60},action_id=f"discord:{interaction.id}:dao.dual_cultivate")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message(f"☯️ Paired meridians resonate at **{result.get('location','your shared location')}**.\nBond Resonance: **{int(result.get('resonance',0))}/100**",ephemeral=False)


@registered_group_command(bond_group, name="sever", description="End your active Dao partnership")
@serialized_user_action
async def bond_sever(interaction:discord.Interaction,confirm:bool=False)->None:
    if not await require_character(interaction): return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action("dao.sever",interaction.user.id,{},action_id=f"discord:{interaction.id}:dao.sever")
        result=dict(envelope.get("result") or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False); return
    await interaction.response.send_message("🧵 Your Dao partnership is severed.",ephemeral=False)


@registered_root_command(name="soul",description="View your reincarnation history and persistent Soul Legacy",guild=GUILD)
async def soul_status(interaction:discord.Interaction)->None:
    c=await require_character(interaction,allow_deceased=True)
    if not c:return
    legacy=await DB.get_soul_legacy(interaction.user.id)
    trait=str(legacy.get("special_trait") or "None")
    past=list(legacy.get("past_lives") or [])
    awakened=int(legacy.get("awakened_memory",0))
    memory=int(legacy.get("memory_seed",0))
    visible_count=0 if memory<=0 or awakened<=0 else min(len(past), max(1, (awakened * len(past)) // max(1,memory)))
    lines=[
        f"☸️ **Soul Record — {c['name']}**",
        f"Incarnation: **{int(legacy.get('incarnation_count',1))}**",
        f"Soul Legacy Points: **{int(legacy.get('legacy_points',0))}**",
        f"Memory Seed: **{memory}%** • Awakened: **{awakened}%**",
        f"Talent Echo: **{int(legacy.get('talent_echo',0))}%**",
        f"Law Echo: **{int(legacy.get('law_echo',0))}%**",
        f"Karmic Fortune: **{int(legacy.get('karmic_fortune',0)):+d}**",
        f"Samsara Trait: **{trait}**",
        f"Past lives recorded: **{len(past)}**",
    ]
    if visible_count>0:
        lines.append("\n**Awakened past-life echoes**")
        for life in past[-visible_count:]:
            lines.append(f"• **{life.get('name','Unknown')}** — {life.get('path') or 'Unknown Path'}; karma {int(life.get('karma',0)):+d}; death: {life.get('death_reason','unknown')}")
    elif past:
        lines.append("\nYour previous incarnations remain sealed. Breakthroughs and deep Law insights can awaken fragments.")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)

@registered_root_command(name="afterlife",description="View your Samsara cycle after true death",guild=GUILD)
async def afterlife_status(interaction:discord.Interaction)->None:
    c=await require_character(interaction,allow_deceased=True)
    if not c:return
    if c.get("life_status")!="deceased":
        await interaction.response.send_message("You are alive; no Samsara cycle is active.",ephemeral=False);return
    try:
        state=dict(await ENGINE.action(
            "lifecycle.samsara_status",interaction.user.id,{"minutes_per_year":MINUTES_PER_YEAR},
        ) or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"No reincarnation path is currently recorded for this soul. ({exc})",ephemeral=False);return
    old_family=await DB.get_birth_family(interaction.user.id) or {}
    progress=min(1.0,float(state.get('samsara_years_elapsed',0))/max(1.0,float(state.get('samsara_years_target',1))))
    passed=int(int(state.get('samsara_lives_count',0))*progress)
    lines=[
        f"☸️ **Samsara — {c['name']}**",
        f"Previous family: **{old_family.get('family_name','Unknown')}** (historical only; not fast-forwarded)",
        f"Private soul-time: **{float(state.get('samsara_years_elapsed',0)):.2f} / {float(state.get('samsara_years_target',0)):.2f} years**",
        f"Lives passed through the wheel: **{passed:,} / {int(state.get('samsara_lives_count',0)):,}**",
        ("Reincarnation: **READY — use /reincarnate**" if state.get("ready") else f"Real time remaining: **{human_duration(int(state.get('seconds_remaining',0)))}**"),
        f"Playable rebirth destination: **{str(state.get('target_world') or 'Mortal World')}**",
        "Shared world time advanced: **0 extra minutes** because of your death.",
        f"Memory Seed: **{int(state.get('memory_retention',0))}%**",
        f"Talent Echo: **{int(state.get('talent_retention',0))}%**",
        f"Partner Echo: **{int(state.get('partner_echo',0))}%**" + (f" from **{state.get('partner_name')}**" if state.get('partner_name') else ""),
        f"Law Echo: **{int(state.get('comprehension_retention',0))}%**",
        f"Soul Legacy Points from this life: **{int(state.get('legacy_points',0))}**",
    ]
    if state.get("special_trait"):
        lines.append(f"Potential Samsara Trait: **{state.get('special_trait')}**")
    echoes=list(state.get("samsara_history") or [])[-5:]
    if echoes:
        lines.append("\n**Notable Samsara echoes**")
        lines.extend(f"• {entry}" for entry in echoes)
    await reply_long(interaction,"\n".join(lines),ephemeral=False)

@registered_root_command(name="reincarnate",description="Complete Samsara and be born again in the world chosen by the wheel",guild=GUILD)
@app_commands.choices(gender=GENDER_CHOICES)
@serialized_user_action
async def reincarnate(interaction:discord.Interaction,name:str,path:str,gender:app_commands.Choice[str])->None:
    c=await DB.get_character(interaction.user.id)
    if not c:
        await interaction.response.send_message("Create your first incarnation with **/begin**.",ephemeral=False);return
    if c.get('life_status')!='deceased':
        await interaction.response.send_message("Your current incarnation is still alive.",ephemeral=False);return
    try:
        state=dict(await ENGINE.action(
            "lifecycle.samsara_status",interaction.user.id,{"minutes_per_year":MINUTES_PER_YEAR},
        ) or {})
    except GameEngineError as exc:
        await interaction.response.send_message(f"No Samsara cycle is recorded for this soul. ({exc})",ephemeral=False);return
    if not state.get("ready"):
        await interaction.response.send_message(
            f"☸️ Your soul is still turning through Samsara.\n"
            f"Private soul-time: **{float(state.get('samsara_years_elapsed',0)):.2f} / {float(state.get('samsara_years_target',0)):.2f} years**\n"
            f"Real time remaining: **{human_duration(int(state.get('seconds_remaining',0)))}**\n"
            "Your old family and the shared world are not being fast-forwarded.",ephemeral=False);return
    normalized_path=WORLD.normalize_path(path)
    if not normalized_path:
        await interaction.response.send_message("Unknown path. Choose: "+", ".join(WORLD.paths.keys()),ephemeral=False);return
    wt=await current_world_time()
    try:
        envelope=await ENGINE.authoritative_action(
            "lifecycle.reincarnate",interaction.user.id,
            {"name":name.strip(),"path":normalized_path,"gender":gender.value},
            action_id=f"discord:{interaction.id}:lifecycle.reincarnate",
        )
    except GameEngineError as exc:
        await interaction.response.send_message(f"❌ {exc}",ephemeral=False);return
    result=dict(envelope.get("result") or {})
    partner_echo=max(0,min(25,int(result.get("partner_echo",0))))
    partner_name=str(result.get("partner_name") or "")
    lines=[
        "☸️ **Samsara turns. A new life begins.**",
        f"You awaken as **{name.strip()}**, generation **1** of **{result['family_name']}**.",
        f"World: **{result.get('target_world', state.get('target_world','Mortal World'))}**",
        f"Home: **{result.get('physical_location','Unknown')}** • Opening scene: **family household**",
        f"Age: **12** • Spiritual Root: **{result['spiritual_root']}** • Path: **{normalized_path}**",
        f"Incarnation: **{int(result.get('incarnation_count',2))}**",
        f"Soul Legacy Points: **{int(result.get('legacy_points',0))}**",
        f"Memory Seed: **{int(result.get('memory_retention',0))}%**",
        f"Talent Echo: **{int(result.get('talent_retention',0))}%**" + (f" + **{partner_echo}% Partner Echo** from {partner_name}" if partner_echo and partner_name else ""),
        f"Law Echo: **{int(result.get('comprehension_retention',0))}%**",
        "Your previous realm, Qi/Body cultivation, inventory, money, and direct Law progress are gone.",
        "Past-life memories are sealed and can awaken gradually through breakthroughs and deep comprehension.",
    ]
    if result.get("lineage_status"):
        lines.append(f"Lineage outcome: **{str(result['lineage_status']).replace('_',' ').title()}**")
    if result.get("lineage_summary"):
        lines.append(str(result["lineage_summary"]))
    if result.get("special_trait"):
        lines.append(f"🌌 Samsara Trait: **{result['special_trait']}**")
    try:
        await ENGINE.bootstrap_simulation(wt.total_minutes)
    except Exception:
        log.exception("Could not initialize Go-owned simulation bootstrap after reincarnation")
    await interaction.response.send_message("\n".join(lines))

@registered_group_command(admin_player_group, name="karma",description="Adjust a cultivator's canonical Good/Evil karma score")
async def admin_karma(interaction:discord.Interaction,member:discord.Member,amount:app_commands.Range[int,-1000,1000],reason:str="GM/world decision")->None:
    if not await require_admin(interaction):return
    if not await DB.get_character(member.id):
        await interaction.response.send_message("That member has no cultivation character.",ephemeral=False);return
    before_c = await DB.get_character(member.id)
    karma_result = dict(
        await ENGINE.action(
            "admin.player.karma",
            interaction.user.id,
            {"user_id": member.id, "delta": int(amount), "reason": reason[:200]},
        )
        or {}
    )
    score = int(karma_result.get("karma_score", 0))
    await audit_admin(
        interaction,
        "player.karma",
        target=f"user:{member.id}",
        before={"karma": int((before_c or {}).get("karma_score", 0))},
        after={"karma": score},
        reason=reason,
        database_log=False,
    )
    await interaction.response.send_message(f"☯️ {member.mention}: karma changed by **{int(amount):+d}** → **{score:+d} ({karma_label(score)})**.",ephemeral=False)

@registered_group_command(admin_sect_group, name="setsect", description="Assign a cultivator to a sect using the canonical hierarchy")
async def admin_setsect(
    interaction: discord.Interaction,
    member: discord.Member,
    sect_name: str,
    rank_name: str = "Outer Disciple",
) -> None:
    if not await require_admin(interaction):
        return
    c = await DB.get_character(member.id)
    if not c:
        await interaction.response.send_message("That member must create a character first with /begin.", ephemeral=False)
        return
    rank = WORLD.sect_rank(rank_name)
    if not rank:
        await interaction.response.send_message(
            "Unknown rank. Use the canonical ladder: " + ", ".join(r["name"] for r in WORLD.sect_system.get("ranks", [])),
            ephemeral=False,
        )
        return
    old_membership = await DB.get_sect_membership(member.id)
    await DB.set_sect_membership(
        member.id, sect_name=sect_name, rank_name=str(rank["name"]), rank_level=int(rank["level"])
    )
    abode = await ensure_sect_abode_record(member.id, c, await DB.get_sect_membership(member.id) or {"sect_name": sect_name, "rank_name": str(rank["name"])})
    abode_thread = await ensure_sect_abode_thread_for(interaction.guild, member, abode) if interaction.guild else None
    await audit_admin(interaction, "sect.assign", target=f"user:{member.id}", before=old_membership or {}, after={"sect_name": sect_name, "rank_name": str(rank["name"]), "rank_level": int(rank["level"])})
    await interaction.response.send_message(
        f"✅ **{c['name']}** is now recorded in **{sect_name}** as **{rank['name']}** (rank level {rank['level']})."
        + (f"\n🏯 Sect abode: {abode_thread.mention}" if abode_thread else ""),
        ephemeral=False,
    )


async def admin_sect_name_hub_options(
    interaction: discord.Interaction, current: str
) -> list[HubDynamicOption]:
    needle = current.casefold().strip()
    options: list[HubDynamicOption] = []
    for sect_name, data in dict(WORLD.data.get("sects", {})).items():
        label = str(sect_name)
        searchable = f"{label} {data.get('alignment','')} {data.get('specialty','')}".casefold()
        if needle and needle not in searchable:
            continue
        details = " • ".join(
            part for part in (str(data.get("alignment") or ""), str(data.get("specialty") or "")) if part
        )
        options.append(HubDynamicOption(label=label[:100], value=label, description=details[:100], emoji="🏯"))
    return options[:25]


register_hub_option_provider(admin_setsect, "sect_name", admin_sect_name_hub_options)


@registered_group_command(admin_sect_group, name="removesect", description="Remove a cultivator from their recorded sect and clear their lineage links")
async def admin_removesect(interaction: discord.Interaction, member: discord.Member) -> None:
    if not await require_admin(interaction):
        return
    c = await DB.get_character(member.id)
    if not c:
        await interaction.response.send_message("That member has no character.", ephemeral=False)
        return
    old_membership = await DB.get_sect_membership(member.id)
    await DB.clear_sect_membership(member.id)
    await audit_admin(interaction, "sect.remove", target=f"user:{member.id}", before=old_membership or {})
    await interaction.response.send_message(
        f"✅ Removed **{c['name']}** from their recorded sect and cleared attached lineage links.", ephemeral=False
    )


@registered_group_command(admin_sect_group, name="setmaster", description="Set one player character as another character's Shifu")
async def admin_setmaster(
    interaction: discord.Interaction, disciple: discord.Member, master: discord.Member
) -> None:
    if not await require_admin(interaction):
        return
    dc = await DB.get_character(disciple.id)
    mc = await DB.get_character(master.id)
    if not dc or not mc:
        await interaction.response.send_message("Both members must have cultivation characters.", ephemeral=False)
        return
    try:
        await DB.set_master(disciple.id, master.id)
        await audit_admin(interaction, "sect.setmaster", target=f"user:{disciple.id}", after={"master_user_id": master.id})
    except ValueError as exc:
        await interaction.response.send_message(f"❌ {exc}", ephemeral=False)
        return
    await interaction.response.send_message(
        f"✅ **{mc['name']}** is now the recorded **Master** of **{dc['name']}**.", ephemeral=False
    )


@registered_group_command(admin_sect_group, name="clearmaster", description="Remove a cultivator's direct master relationship")
async def admin_clearmaster(interaction: discord.Interaction, disciple: discord.Member) -> None:
    if not await require_admin(interaction):
        return
    c = await DB.get_character(disciple.id)
    if not c:
        await interaction.response.send_message("That member has no cultivation character.", ephemeral=False)
        return
    await DB.clear_master(disciple.id)
    await audit_admin(interaction, "sect.clearmaster", target=f"user:{disciple.id}")
    await interaction.response.send_message(
        f"✅ Cleared the direct Master relationship for **{c['name']}**.", ephemeral=False
    )




async def sect_rank_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    needle=current.casefold().strip();out=[]
    for rank in WORLD.sect_system.get("ranks",[]):
        name=str(rank.get("name","Disciple"))
        if not needle or needle in name.casefold():out.append(app_commands.Choice(name=name[:100],value=name[:100]))
    return out[:25]


register_hub_option_provider(admin_setsect, "rank_name", sect_rank_autocomplete)


@registered_group_command(admin_sect_group, name="sectrank",description="Promote or demote a recorded sect member within the rigid hierarchy")
@app_commands.autocomplete(rank=sect_rank_autocomplete)
async def admin_sect_rank(interaction:discord.Interaction,member:discord.Member,rank:str)->None:
    if not await require_admin(interaction):return
    membership=await DB.get_sect_membership(member.id)
    if not membership:
        await interaction.response.send_message("That cultivator is not in a recorded sect.",ephemeral=False);return
    rank_def=WORLD.sect_rank(rank)
    if not rank_def:
        await interaction.response.send_message("Unknown canonical sect rank.",ephemeral=False);return
    before_rank = await DB.get_sect_membership(member.id)
    await DB.set_sect_rank(member.id,str(rank_def['name']),int(rank_def['level']))
    await audit_admin(interaction, "sect.rank", target=f"user:{member.id}", before=before_rank or {}, after={"rank_name": str(rank_def['name']), "rank_level": int(rank_def['level'])})
    await interaction.response.send_message(f"✅ {member.mention} is now **{rank_def['name']}** (level {rank_def['level']}).",ephemeral=False)


@registered_group_command(admin_sect_group, name="masterattention",description="Adjust how much attention a master currently gives a disciple")
async def admin_master_attention(interaction:discord.Interaction,disciple:discord.Member,amount:app_commands.Range[int,-100,100])->None:
    if not await require_admin(interaction):return
    value=await DB.adjust_master_attention(disciple.id,int(amount))
    await audit_admin(interaction, "sect.masterattention", target=f"user:{disciple.id}", after={"attention": value, "delta": int(amount)})
    await interaction.response.send_message(f"✅ Master attention for {disciple.mention}: **{value}**.",ephemeral=False)


@registered_group_command(admin_player_group, name="grantstorage",description="Grant or replace a cultivator's spatial storage container")
async def admin_grant_storage(
    interaction:discord.Interaction,member:discord.Member,name:str,grade:str="Earth",
    slots:app_commands.Range[int,1,5000]=80,living_space:bool=False
)->None:
    if not await require_admin(interaction):return
    if not await DB.get_character(member.id):
        await interaction.response.send_message("That member has no character.",ephemeral=False);return
    await DB.set_storage_container(member.id,container_id=name.casefold().replace(' ','_'),name=name,grade=grade,slot_capacity=int(slots),living_space=living_space)
    await audit_admin(interaction, "player.grantstorage", target=f"user:{member.id}", after={"name": name, "grade": grade, "slots": int(slots), "living_space": living_space})
    await interaction.response.send_message(f"✅ Granted **{name}** ({grade}, {slots} stacks, living space: {living_space}) to {member.mention}.",ephemeral=False)


@registered_group_command(admin_player_group, name="grantcurrency",description="Grant cultivation currency for events, testing or GM rewards")
@app_commands.autocomplete(currency=auction_currency_autocomplete)
async def admin_grant_currency(interaction:discord.Interaction,member:discord.Member,currency:str,amount:app_commands.Range[int,1,2000000000])->None:
    if not await require_admin(interaction):return
    if currency not in WORLD.currencies:
        await interaction.response.send_message("Unknown currency.",ephemeral=False);return
    currency_result = dict(
        await ENGINE.action(
            "admin.player.grant_currency",
            interaction.user.id,
            {
                "user_id": member.id,
                "currency_id": currency,
                "amount": int(amount),
                "reason": "discord admin",
            },
        )
        or {}
    )
    balance = int(currency_result.get("balance", 0))
    await audit_admin(
        interaction,
        "player.grantcurrency",
        target=f"user:{member.id}",
        after={"currency": currency, "amount": int(amount), "balance": balance},
        database_log=False,
    )
    await interaction.response.send_message(f"✅ Granted **{amount:,} {WORLD.currency_name(currency)}**. New balance: **{balance:,}**.",ephemeral=False)


@registered_group_command(admin_world_group, name="advancetime",description="Advance the canonical in-world clock")
async def admin_advance_time(interaction:discord.Interaction,minutes:app_commands.Range[int,1,525600])->None:
    if not await require_admin(interaction):return
    time_result = dict(
        await ENGINE.action(
            "admin.world.advance_time",
            interaction.user.id,
            {
                "minutes": int(minutes),
                "scale": SETTINGS.world_time_scale,
                "reason": "discord admin",
            },
        )
        or {}
    )
    new_minute = int(time_result.get("game_minute", 0))
    await audit_admin(
        interaction,
        "world.advancetime",
        target="world_clock",
        after={"minutes": int(minutes), "new_game_minute": new_minute},
        database_log=False,
    )
    wt=from_game_minutes(new_minute)
    await interaction.response.send_message(f"🕰️ Advanced world time by **{minutes:,} minutes**.\nNow: **{wt.display}**",ephemeral=False)


MAINTENANCE_CHOICES=[
    app_commands.Choice(name="Cleanup expired data",value="cleanup"),
    app_commands.Choice(name="Vacuum database",value="vacuum"),
    app_commands.Choice(name="Sync world catalog",value="sync"),
]


@registered_group_command(admin_server_group, name="maintenance",description="Run safe database/content maintenance")
@app_commands.choices(action=MAINTENANCE_CHOICES)
async def admin_maintenance(interaction:discord.Interaction,action:app_commands.Choice[str])->None:
    if not await require_admin(interaction):return
    await interaction.response.defer(ephemeral=False)
    if action.value=="sync":
        await DB.sync_world_catalog(WORLD.data)
        await interaction.followup.send("✅ Locations, NPCs and recipes were resynced from world.json into SQLite.",ephemeral=False);return
    if action.value=="vacuum":
        await DB.vacuum();await interaction.followup.send("✅ SQLite VACUUM completed.",ephemeral=False);return
    wt=await current_world_time(); counts=await DB.maintenance_cleanup(wt.total_minutes)
    await interaction.followup.send(
        "🧹 **Maintenance cleanup complete**\n"+"\n".join(f"• {k}: {v} rows" for k,v in counts.items()),ephemeral=False
    )




ADMIN_AUTOMATION_CHOICES = [
    app_commands.Choice(name="Event expiry/scene closing", value="event_expiry"),
    app_commands.Choice(name="Auction settlement", value="auction_settlement"),
    app_commands.Choice(name="Unexpected exploration events", value="unexpected_events"),
    app_commands.Choice(name="Autonomous world event spawning", value="autonomous_world_events"),
    app_commands.Choice(name="Automatic maintenance cleanup", value="maintenance_cleanup"),
    app_commands.Choice(name="NPC civilization", value="npc_civilization"),
    app_commands.Choice(name="NPC autonomous lives", value="npc_life"),
    app_commands.Choice(name="Autonomous sect politics", value="sect_politics"),
    app_commands.Choice(name="Dynamic economy", value="dynamic_economy"),
    app_commands.Choice(name="Rotating black markets", value="black_markets"),
    app_commands.Choice(name="Martial clan dynamics", value="clan_dynamics"),
    app_commands.Choice(name="Background cultivation / seclusion", value="background_seclusion"),
]

SIMULATION_SYSTEM_CHOICES = [
    app_commands.Choice(name="All simulation systems", value="all"),
    app_commands.Choice(name="NPC civilization", value="npc_civilization"),
    app_commands.Choice(name="NPC autonomous lives", value="npc_life"),
    app_commands.Choice(name="Autonomous sect politics", value="sect_politics"),
    app_commands.Choice(name="Dynamic economy", value="dynamic_economy"),
    app_commands.Choice(name="Rotating black markets", value="black_markets"),
    app_commands.Choice(name="Martial clan dynamics", value="clan_dynamics"),
    app_commands.Choice(name="Background cultivation / seclusion", value="background_seclusion"),
]
SIMULATION_SINGLE_SYSTEM_CHOICES = SIMULATION_SYSTEM_CHOICES[1:]

BACKUP_ACTIONS = [
    app_commands.Choice(name="Create backup", value="create"),
    app_commands.Choice(name="List backups", value="list"),
    app_commands.Choice(name="Backup status", value="status"),
]


@registered_group_command(admin_player_group, name="inspect", description="Inspect a player's hidden canonical game state")
async def admin_inspect(interaction: discord.Interaction, member: discord.Member) -> None:
    if not await require_admin(interaction): return
    c = await DB.get_character(member.id)
    if not c:
        await interaction.response.send_message("That member has no cultivation character.", ephemeral=False); return
    family = await DB.get_birth_family(member.id)
    sect = await DB.get_sect_membership(member.id)
    battle = await DB.get_active_battle(member.id)
    abode = await DB.get_abode(member.id)
    laws = await DB.get_law_progress(member.id)
    wallet = await DB.get_wallet(member.id)
    inventory = await DB.get_inventory(member.id)
    soul = await DB.get_soul_legacy(member.id)
    reinc = await DB.get_reincarnation_state(member.id)
    seclusion = await DB.get_seclusion(member.id)
    wt = await current_world_time()
    effects = await DB.get_active_effects(member.id, wt.total_minutes)
    lines = [
        f"🛡️ **Admin Inspect — {c['name']}** (`{member.id}`)",
        f"\nLife: **{c.get('life_status','alive')}** • Realm: **{WORLD.realm_name(int(c['realm_index']), c.get('gender'))} Stage {c['phase']}**",
        f"\nBody: **{WORLD.body_realm_name(int(c.get('body_realm_index',0)), c.get('gender'))} Stage {c.get('body_phase',1)}**",
        f"\nLocation: **{c.get('location')}** • Karma: **{int(c.get('karma_score',0)):+d}**",
        f"\nQi/Vitality: **{c.get('qi')}/{c.get('qi_max')} • {c.get('vitality')}/{c.get('vitality_max')}**",
        f"\nFamily: **{family.get('family_name') if family else 'None'}** • Sect: **{sect.get('sect_name') if sect else 'None'}**",
        f"\nBattle: **{battle.get('npc_name') if battle else 'None'}** • Abode: **{abode.get('name') if abode else 'None'}**",
        f"\nSoul incarnation: **{soul.get('incarnation_count',1)}** • Legacy: **{soul.get('legacy_points',0)}**",
        f"\nReincarnation pending: **{'yes' if reinc else 'no'}** • Seclusion: **{seclusion.get('mode').upper() if seclusion else 'none'}** • Active effects: **{len(effects)}** • Laws: **{len(laws)}**",
        f"\nWallet entries: **{len([v for v in wallet.values() if int(v)!=0])}** • Inventory stacks: **{len([v for v in inventory.values() if int(v)>0])}**",
    ]
    await audit_admin(interaction, "player.inspect", target=f"user:{member.id}")
    await interaction.response.send_message("".join(lines), ephemeral=False)


@registered_group_command(admin_player_group, name="teleport", description="Teleport a player to a canonical location")
async def admin_teleport(interaction: discord.Interaction, member: discord.Member, location: str, reason: str = "GM teleport") -> None:
    if not await require_admin(interaction): return
    c = await DB.get_character(member.id)
    if not c:
        await interaction.response.send_message("That member has no cultivation character.", ephemeral=False); return
    loc = await DB.get_location_definition(location)
    if not loc:
        await interaction.response.send_message("Unknown canonical location.", ephemeral=False); return
    before = {"location": c.get("location")}
    await ENGINE.action(
        "admin.player.teleport",
        interaction.user.id,
        {"user_id": member.id, "location": location, "reason": reason},
    )
    await audit_admin(
        interaction,
        "player.teleport",
        target=f"user:{member.id}",
        before=before,
        after={"location": location},
        reason=reason,
        database_log=False,
    )
    await interaction.response.send_message(f"✅ Teleported {member.mention} to **{location}**.", ephemeral=False)


@admin_teleport.autocomplete("location")
async def admin_teleport_location_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    names = await DB.search_catalog("location", current, 25)
    return [app_commands.Choice(name=n[:100], value=n[:100]) for n in names]


@registered_group_command(admin_player_group, name="revive", description="Revive a dead character and cancel pending Samsara")
async def admin_revive(interaction: discord.Interaction, member: discord.Member, reason: str = "GM intervention") -> None:
    if not await require_admin(interaction): return
    c = await DB.get_character(member.id)
    if not c:
        await interaction.response.send_message("That member has no cultivation character.", ephemeral=False); return
    before = {"life_status": c.get("life_status"), "vitality": c.get("vitality")}
    revive_result = dict(
        await ENGINE.action(
            "admin.player.revive",
            interaction.user.id,
            {"user_id": member.id, "reason": reason},
        )
        or {}
    )
    ok = bool(revive_result)
    if not ok:
        await interaction.response.send_message("Could not revive that character.", ephemeral=False); return
    await audit_admin(
        interaction,
        "player.revive",
        target=f"user:{member.id}",
        before=before,
        after={"life_status": "alive", "vitality": "full"},
        reason=reason,
        database_log=False,
    )
    await interaction.response.send_message(f"✨ Revived {member.mention}. Pending Samsara was cancelled.", ephemeral=False)


@registered_group_command(admin_player_group, name="clearbattle", description="Force-clear a player's active battle state")
async def admin_clearbattle(interaction: discord.Interaction, member: discord.Member, reason: str = "GM recovery") -> None:
    if not await require_admin(interaction): return
    clear_result = dict(
        await ENGINE.action(
            "admin.player.clear_battle",
            interaction.user.id,
            {"user_id": member.id, "reason": reason},
        )
        or {}
    )
    count = int(clear_result.get("cleared", 0))
    await audit_admin(
        interaction,
        "player.clearbattle",
        target=f"user:{member.id}",
        after={"cleared": count},
        reason=reason,
        database_log=False,
    )
    await interaction.response.send_message(f"✅ Cleared **{count}** active battle state(s) for {member.mention}.", ephemeral=False)


@registered_group_command(admin_family_group, name="familyinspect", description="Inspect a player's NPC birth family")
async def admin_familyinspect(interaction: discord.Interaction, member: discord.Member) -> None:
    if not await require_admin(interaction): return
    family = await DB.get_birth_family(member.id)
    if not family:
        await interaction.response.send_message("That member has no recorded birth family.", ephemeral=False); return
    alive = [n for n in family.get("npcs", []) if n.get("status") == "alive"]
    lines = [
        f"🏯 **Admin Family Inspect — {family['family_name']}**",
        f"\nArchetype: `{family.get('archetype')}` • Tier: **{family.get('tier')}** • Status: **{family.get('line_status','active')}**",
        f"\nWealth **{family.get('wealth')}** • Influence **{family.get('influence')}** • Stability **{family.get('stability')}**",
        f"\nHead: **{family.get('head_title')} {family.get('head_name')}** • Location: **{family.get('location')}**",
        f"\nBloodline: **{family.get('bloodline_name','None')}** • Purity **{family.get('bloodline_purity',0)}%** • Affinity **{family.get('bloodline_affinity','None')}**",
        f"\nBranches **{family.get('branch_count',1)}** • Retainers **{family.get('retainer_count',0)}** • Martial alliance **{family.get('confederacy_name','None')}**",
        f"\nRecorded NPC relatives: **{len(family.get('npcs', []))}** • Alive: **{len(alive)}**",
    ]
    history = list(family.get("history", []))[-5:]
    if history:
        lines.append("\n\n**Recent history**\n" + "\n".join(f"• {h}" for h in history))
    await audit_admin(interaction, "family.inspect", target=f"family:{family['family_id']}")
    await reply_long(interaction, "".join(lines), ephemeral=False)


@registered_group_command(admin_npc_group, name="npcinspect", description="Inspect a canonical NPC including hidden GM-only fields")
async def admin_npcinspect(interaction: discord.Interaction, npc: str) -> None:
    if not await require_admin(interaction): return
    data = await DB.get_npc_definition(npc)
    if not data:
        await interaction.response.send_message("Unknown NPC.", ephemeral=False); return
    lines = [f"🧿 **Admin NPC Inspect — {npc}**"]
    for key in ("title","location","world","realm_index","phase","role","personality","schedule"):
        if key in data:
            lines.append(f"\n**{key.replace('_',' ').title()}:** `{data[key]}`")
    if data.get("hidden_master"):
        lines.append(f"\n\n🔒 **Hidden-master data:** `{data['hidden_master']}`")
    if data.get("fake_hidden_master"):
        lines.append(f"\n\n🎭 **Fake-master data:** `{data['fake_hidden_master']}`")
    sim_state=await SIM.npc_status(npc)
    if sim_state:
        lines.append(f"\n\n⚙️ **Simulation:** location `{sim_state.get('current_location')}` • activity `{sim_state.get('activity')}` • mood `{sim_state.get('mood')}` • wealth {sim_state.get('wealth')} • influence {sim_state.get('influence')} • realm {sim_state.get('realm_index')}/{sim_state.get('phase')}")
        lines.append(f"\n🎯 **Current goal:** {sim_state.get('current_goal') or 'No active goal recorded.'} ({sim_state.get('goal_progress',0)}%)")
        if sim_state.get('recent_event'):
            lines.append(f"\n🧠 **Recent autonomous development:** {sim_state.get('recent_event')}")
    await audit_admin(interaction, "npc.inspect", target=f"npc:{npc}")
    await reply_long(interaction, "".join(lines), ephemeral=False)


@admin_npcinspect.autocomplete("npc")
async def admin_npcinspect_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    names = await DB.search_catalog("npc", current, 25)
    return [app_commands.Choice(name=n[:100], value=n[:100]) for n in names]


@registered_group_command(admin_sim_group, name="toggle", description="Enable/disable an automatic world system")
@app_commands.choices(system=ADMIN_AUTOMATION_CHOICES)
async def admin_automation(interaction: discord.Interaction, system: app_commands.Choice[str], enabled: bool) -> None:
    if not await require_admin(interaction): return
    before = await DB.get_automation_settings()
    automation_result = dict(
        await ENGINE.action(
            "admin.automation.set",
            interaction.user.id,
            {"system": system.value, "enabled": enabled, "reason": "discord admin"},
        )
        or {}
    )
    after = dict(automation_result.get("settings") or {})
    await audit_admin(
        interaction,
        "automation.set",
        target=system.value,
        before={system.value: before.get(system.value)},
        after={system.value: after.get(system.value)},
        database_log=False,
    )
    await interaction.response.send_message(f"⚙️ **{system.name}** is now **{'ON' if enabled else 'OFF'}**.", ephemeral=False)


@registered_group_command(admin_sim_group, name="automation", description="Show all automatic world-system switches")
async def admin_automation_status(interaction: discord.Interaction) -> None:
    if not await require_admin(interaction): return
    settings = await DB.get_automation_settings()
    labels = {c.value:c.name for c in ADMIN_AUTOMATION_CHOICES}
    lines = ["⚙️ **Automation Status**"]
    for key, enabled in settings.items():
        lines.append(f"\n{'✅' if enabled else '⛔'} **{labels.get(key,key)}:** {'ON' if enabled else 'OFF'}")
    lines.append("\n\nSimulation systems are world-time driven, persisted in SQLite, and catch up safely after restarts.")
    await interaction.response.send_message("".join(lines), ephemeral=False)


@registered_group_command(admin_sim_group, name="status", description="Show simulation intervals, lag and completed runs")
async def admin_simulation_status(interaction: discord.Interaction) -> None:
    if not await require_admin(interaction): return
    wt=await current_world_time()
    rows=await SIM.simulation_status(wt.total_minutes)
    if not rows:
        await interaction.response.send_message("No simulation state has been initialized yet.",ephemeral=False);return
    labels={c.value:c.name for c in SIMULATION_SINGLE_SYSTEM_CHOICES}
    lines=[f"🧭 **World Simulation Status**\nCanonical time: **{wt.display}**"]
    for row in rows:
        interval=max(1,int(row['interval_game_minutes']))
        lag=max(0,int(row.get('lag_game_minutes',0)))
        lines.append(f"• **{labels.get(row['system'],row['system'])}** — every {interval/MINUTES_PER_DAY:g} day(s) • lag {lag:,} game min • runs {row['runs']}")
    await interaction.response.send_message("\n".join(lines),ephemeral=False)


@registered_group_command(admin_sim_group, name="run", description="Force one or all world-simulation systems to advance")
@app_commands.choices(system=SIMULATION_SYSTEM_CHOICES)
async def admin_simulation_run(interaction: discord.Interaction, system: app_commands.Choice[str], steps: app_commands.Range[int,1,120]=1) -> None:
    if not await require_admin(interaction): return
    wt=await current_world_time()
    await interaction.response.defer(ephemeral=False)
    run=await SIM.force_run(system.value,int(steps),wt.total_minutes)
    await audit_admin(interaction,"simulation.run",target=system.value,after={"steps":int(steps),"summary":run.summary})
    await interaction.followup.send(f"⚙️ **{system.name}** forced for **{int(steps)}** step(s).\n{run.summary}",ephemeral=False)


@registered_group_command(admin_sim_group, name="interval", description="Set a simulation subsystem interval in world-days")
@app_commands.choices(system=SIMULATION_SINGLE_SYSTEM_CHOICES)
async def admin_simulation_interval(interaction: discord.Interaction, system: app_commands.Choice[str], days: app_commands.Range[int,1,365]) -> None:
    if not await require_admin(interaction): return
    before=await SIM.get_system_state(system.value)
    after=await SIM.set_interval_days(system.value,int(days))
    await audit_admin(interaction,"simulation.interval",target=system.value,before=before or {},after=after)
    await interaction.response.send_message(f"⏱️ **{system.name}** now runs every **{int(days)} world-day(s)**.",ephemeral=False)


@registered_group_command(admin_sim_group, name="region", description="Inspect a civilization region and recent autonomous incidents")
async def admin_simulation_region(interaction: discord.Interaction, location: str) -> None:
    if not await require_admin(interaction): return
    data=await SIM.civilization_status(location)
    if not data:
        await interaction.response.send_message("Unknown or unsimulated region.",ephemeral=False);return
    lines=[f"🏙️ **Admin Region — {location}**",f"Population **{int(data['population']):,}** • prosperity {data['prosperity']} • security {data['security']} • unrest {data['unrest']}",f"Spirit resources {data['spirit_resources']} • food {data['food_supply']} • migration {int(data['migration_pressure']):+d}"]
    for event in list(data.get('events') or [])[:5]: lines.append(f"• {event['event_text']}")
    await interaction.response.send_message("\n".join(lines),ephemeral=False)


@admin_simulation_region.autocomplete("location")
async def admin_simulation_region_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    names=await DB.search_catalog("location",current,25)
    return [app_commands.Choice(name=n[:100],value=n[:100]) for n in names]


@registered_group_command(admin_sim_group, name="npc", description="Inspect a named NPC's autonomous civilization and life state")
async def admin_simulation_npc(interaction: discord.Interaction, npc: str) -> None:
    if not await require_admin(interaction): return
    data=await SIM.npc_status(npc)
    if not data:
        await interaction.response.send_message("That NPC has no simulation state.",ephemeral=False);return
    age=data.get("age_years")
    life=data.get("lifespan_years")
    life_text="ageless" if life is None and int(data.get("realm_index") or 0)>=16 else (f"{float(life):.0f}y ceiling" if life else "unknown")
    injury=str(data.get("injury") or "None")
    relation_lines=[]
    for r in list(data.get("relationships") or [])[:5]:
        other=r['npc_b'] if r['npc_a']==npc else r['npc_a']
        relation_lines.append(f"• {other}: {r['relation_type']} (aff {int(r['affinity']):+d}, trust {int(r['trust']):+d}, grudge {int(r['grudge'])})")
    bond_lines=[]
    for b in list(data.get("discipleship") or [])[:5]:
        bond_lines.append(f"• Disciple: {b['disciple_name']}" if b['master_name']==npc else f"• Master: {b['master_name']}")
    lines=[
        f"🧿 **NPC Simulation — {npc}**",
        f"Location: **{data['current_location']}** (home {data['home_location']})",
        f"Activity: **{data['activity']}** • Faction: **{data['faction']}** • Rank: **{data.get('sect_rank') or 'Independent Cultivator'}**",
        f"Cultivation: **{WORLD.realm_name(int(data['realm_index']))} Stage {data['phase']}**",
        f"Age: **{float(age):.1f}y** • Lifespan: **{life_text}** • Health: **{int(data.get('health') or 0)}/100** • Injury: **{injury}**" if age is not None else f"Health: **{int(data.get('health') or 0)}/100** • Injury: **{injury}**",
        f"Family: **{data.get('relationship_status') or 'single'}**" + (f" • spouse **{data.get('spouse_name')}**" if data.get('spouse_name') else "") + f" • children **{int(data.get('children_count') or 0)}**",
        f"Wealth {data['wealth']} • Influence {data['influence']} • Ambition {data['ambition']} • Career {int(data.get('career_progress') or 0)}%",
    ]
    if relation_lines: lines.append("\n**Relationships**\n"+"\n".join(relation_lines))
    if bond_lines: lines.append("\n**Master / Disciple**\n"+"\n".join(bond_lines))
    await interaction.response.send_message("\n".join(lines),ephemeral=False)


@admin_simulation_npc.autocomplete("npc")
async def admin_simulation_npc_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    names=await DB.search_catalog("npc",current,25)
    return [app_commands.Choice(name=n[:100],value=n[:100]) for n in names]


@registered_group_command(admin_sim_group, name="sect", description="Inspect autonomous sect factions, resources and relations")
async def admin_simulation_sect(interaction: discord.Interaction, sect: str) -> None:
    if not await require_admin(interaction): return
    data=await SIM.sect_status(sect)
    if not data:
        await interaction.response.send_message("Unknown or unsimulated sect.",ephemeral=False);return
    lines=[f"🏯 **Sect Simulation — {sect}**",f"Influence **{data['influence']}** • cohesion **{data['cohesion']}/100** • resources **{data['resources']}**",f"Recruitment **{data['recruitment_pressure']}/100** • doctrine **{data['doctrine_pressure']}/100** • policy **{data['leader_policy']}**"]
    if data.get('factions'):
        lines.append("\n**Factions**"); lines.extend(f"• {f['faction_name']} — power {f['power']}% / loyalty {f['loyalty']}" for f in data['factions'])
    if data.get('relations'):
        lines.append("\n**Relations**"); lines.extend(f"• {r['other']} — {r['relation_type']} ({int(r['relation_score']):+d})" for r in data['relations'][:8])
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


register_hub_option_provider(admin_simulation_sect, "sect", admin_sect_name_hub_options)


@registered_group_command(admin_sim_group, name="market", description="Inspect a local dynamic market")
async def admin_simulation_market(interaction: discord.Interaction, location: str, item: str | None=None) -> None:
    if not await require_admin(interaction): return
    if item:
        q=await SIM.market_quote(location,item)
        if not q:
            await interaction.response.send_message("That item has no market quote there.",ephemeral=False);return
        await interaction.response.send_message(f"💹 **{WORLD.item_name(item)} — {location}**\nBase {q['base_price']} • index x{float(q['price_index']):.2f} • buy {q['buy_price']} • sell {q['sell_price']} {WORLD.currency_name(q['currency_id'])}\nSupply {q['supply']} • demand {q['demand']}",ephemeral=False);return
    rows=await SIM.market_rows(location,15)
    if not rows:
        await interaction.response.send_message("No market is simulated at that location.",ephemeral=False);return
    lines=[f"💹 **Admin Market — {location}**"]
    for q in rows: lines.append(f"• {WORLD.item_name(q['item_id'])} — {max(1,int(round(q['base_price']*q['price_index'])))} {WORLD.currency_name(q['currency_id'])} • S{q['supply']}/D{q['demand']} • x{float(q['price_index']):.2f}")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@admin_simulation_market.autocomplete("location")
async def admin_simulation_market_location_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    return await admin_simulation_region_autocomplete(interaction,current)


@admin_simulation_market.autocomplete("item")
async def admin_simulation_market_item_autocomplete(interaction:discord.Interaction,current:str)->list[app_commands.Choice[str]]:
    return _market_item_matches(current)


@registered_group_command(admin_sim_group, name="clan", description="Inspect mechanical clan branches, retainers and alliances for a player")
async def admin_simulation_clan(interaction: discord.Interaction, member: discord.Member) -> None:
    if not await require_admin(interaction): return
    fam=await DB.get_birth_family(member.id)
    if not fam:
        await interaction.response.send_message("That member has no birth family.",ephemeral=False);return
    data=await SIM.clan_status(int(fam['family_id']))
    lines=[f"🩸 **Clan Simulation — {fam['family_name']}**",f"Bloodline: **{fam.get('bloodline_name','None')}** • purity **{fam.get('bloodline_purity',0)}%**"]
    lines.append(f"Branches: **{len(data['branches'])}** • retainer groups: **{len(data['retainers'])}** • active relations: **{len(data['relations'])}**")
    for branch in data['branches'][:6]: lines.append(f"• Branch: {branch['branch_name']} — {branch['status']} / loyalty {branch['loyalty']}")
    for relation in data['relations'][:6]: lines.append(f"• Relation: {relation['partner_name']} — {relation['relation_type']} ({int(relation['relation_score']):+d})")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(admin_sim_group, name="actions", description="Show recent player actions that changed persistent world state")
async def admin_simulation_actions(interaction: discord.Interaction, limit: app_commands.Range[int,1,50] = 15) -> None:
    if not await require_admin(interaction): return
    rows=await SIM.recent_player_actions(int(limit))
    if not rows:
        await interaction.response.send_message("No player-driven world consequences have been recorded yet.",ephemeral=False);return
    lines=["🌍 **Recent Player-Driven World Changes**"]
    for row in rows:
        impacts=list((row.get('payload') or {}).get('impacts') or [])
        summary="; ".join(str(x) for x in impacts[:3]) if impacts else "recorded without a major faction shift"
        lines.append(f"• <@{row.get('user_id')}> `{row.get('action_type')}` → **{row.get('target_key')}** at **{row.get('location')}** — {summary}")
    await reply_long(interaction,"\n".join(lines),ephemeral=False)


@registered_group_command(admin_sim_group, name="world", description="Show a compact canonical world/database status snapshot")
async def admin_simulation_world(interaction: discord.Interaction) -> None:
    if not await require_admin(interaction): return
    snap = await DB.admin_world_snapshot()
    wt = await current_world_time()
    automation = await DB.get_automation_settings()
    text = (
        f"🌍 **Admin World Status**\nWorld time: **{wt.display}**\n"
        f"Characters: **{snap['characters']}** ({snap['alive_characters']} alive / {snap['deceased_characters']} deceased)\n"
        f"Birth families: **{snap['birth_families']}**\nActive battles: **{snap['active_battles']}**\n"
        f"Active auctions: **{snap['active_auctions']}**\nActive world events: **{snap['active_events']}**\n"
        f"Active effects: **{snap['active_effects']}**\n"
        f"Civilization regions: **{snap.get('civilization_regions',0)}** • Simulated named NPCs: **{snap.get('simulated_npcs',0)}**\n"
        f"Sect factions: **{snap.get('sect_factions',0)}** • Market entries: **{snap.get('market_entries',0)}**\n"
        f"Clan branches: **{snap.get('clan_branches',0)}** • Retainer groups: **{snap.get('retainer_groups',0)}** • Clan relations: **{snap.get('clan_relations',0)}**\n"
        f"Active seclusions: **{snap.get('active_seclusions',0)}** • Recorded player world-actions: **{snap.get('world_action_events',0)}**\n"
        f"Persistent conditions: **{snap.get('active_conditions',0)}** • Cleared tribulations: **{snap.get('cleared_tribulations',0)}** • Profession records: **{snap.get('profession_records',0)}**\n"
        f"Open crimes: **{snap.get('open_crimes',0)}** • Active bounties: **{snap.get('active_bounties',0)}** • Active grudges: **{snap.get('active_grudges',0)}**\n"
        f"Automation: **{sum(1 for v in automation.values() if v)}/{len(automation)} enabled**"
    )
    await interaction.response.send_message(text, ephemeral=False)


@registered_group_command(admin_server_group, name="backup", description="Create or inspect safe SQLite backups")
@app_commands.choices(action=BACKUP_ACTIONS)
async def admin_backup(interaction: discord.Interaction, action: app_commands.Choice[str]) -> None:
    if not await require_admin(interaction): return
    backup_dir = ROOT / "data" / "backups"
    if action.value == "create":
        await interaction.response.defer(ephemeral=False)
        path = await DB.create_backup(backup_dir)
        await audit_admin(interaction, "backup.create", target=path.name, after={"path": str(path)})
        await interaction.followup.send(f"💾 Backup created: `{path.name}`", ephemeral=False)
        return
    backups = await DB.list_backups(backup_dir, 20)
    if action.value == "status":
        if not backups:
            await interaction.response.send_message("💾 No backups exist yet.", ephemeral=False); return
        latest = backups[0]
        age = max(0, int(time.time() - float(latest['modified_at'])))
        await interaction.response.send_message(f"💾 Latest backup: `{latest['name']}` • {latest['size']:,} bytes • {human_duration(age)} old", ephemeral=False)
        return
    if not backups:
        await interaction.response.send_message("💾 No backups exist yet.", ephemeral=False); return
    lines = ["💾 **Recent SQLite Backups**"]
    for item in backups:
        lines.append(f"\n• `{item['name']}` — {item['size']:,} bytes")
    await reply_long(interaction, "".join(lines), ephemeral=False)


@registered_group_command(admin_server_group, name="audit", description="Show recent admin actions")
async def admin_audit_log(interaction: discord.Interaction, limit: app_commands.Range[int,1,50] = 15) -> None:
    if not await require_admin(interaction): return
    rows = await DB.get_admin_audit_log(int(limit))
    if not rows:
        await interaction.response.send_message("No admin audit entries exist yet.", ephemeral=False); return
    lines = ["📜 **Admin Audit Log**"]
    for row in rows:
        when = f"<t:{int(row['created_at'])}:R>"
        lines.append(f"\n• {when} <@{row['admin_user_id']}> — `{row['action']}` → `{row.get('target','')}`" + (f" — {row['reason']}" if row.get('reason') else ""))
    await reply_long(interaction, "".join(lines), ephemeral=False)


# ---------------------------------------------------------------------------
# Explicit Discord command surface
# ---------------------------------------------------------------------------
# Internal action objects provide slash metadata for the GUI without becoming
# public Discord roots. Gameplay validation and transactions stay in one handler.

_GROUP_ACTION_ROOTS = {
    "seclusion": seclusion_group,
    "aptitude": aptitude_group,
    "perfect": perfect_group,
    "body": body_group,
    "bodyperfect": bodyperfect_group,
    "secretrealm": secret_group,
    "scene": scene_group,
    "storage": storage_group,
    "auction": auction_group,
    "battle": battle_group,
    "law": law_group,
    "manual": manual_group,
    "condition": condition_group,
    "tribulation": tribulation_group,
    "profession": profession_group,
    "crime": crime_group,
    "beast": beast_group,
    "artifact": artifact_group,
    "territory": territory_group,
    "war": war_group,
    "caravan": caravan_group,
    "party": party_group,
    "duel": duel_group,
    "equipment": equipment_group,
    "formation": formation_group,
    "boss": boss_group,
    "hunter": hunter_group,
    "abode": abode_group,
    "alchemy": alchemy_group,
    "array": array_group,
    "innerworld": innerworld_group,
    "sect": sect_group,
    "family": family_group,
    "civilization": civilization_group,
    "market": market_group,
    "blackmarket": blackmarket_group,
    "realmhub": realmhub_group,
    "fate": fate_group,
    "bond": bond_group,
}

_MIGRATED_ROOTS = {
    "abode", "afterlife", "alchemy", "aptitude", "array", "artifact", "auction", "battle", "beast",
    "body", "bodyperfect", "bond", "boss", "bounty", "breakthrough", "caravan",
    "civilization", "conceal", "condition", "craft", "crime", "cultivate",
    "daoheart", "duel", "effects", "equipment", "era", "explore", "family",
    "formation", "gender", "grudges", "hunt", "hunter", "inheritances", "fate",
    "innerworld", "inventory", "karma", "law", "lifespan", "manual", "market", "blackmarket",
    "npcinfo", "party", "perfect", "profession", "provenance", "reincarnate",
    "reputation", "rulers", "scene", "seclusion", "secretrealm", "sect", "sense",
    "sheet", "soul", "spatialkey", "specialeffects", "storage", "talk", "territory",
    "time", "travel", "realmhub", "tribulation", "use", "wallet", "war", "world",
    "worldevents", "worldrules",
}

_ROOT_ACTIONS: dict[str, object] = dict(_GROUP_ACTION_ROOTS)
_ROOT_ACTIONS.update({
    name: command for name, command in ACTIONS.roots().items()
    if name in _MIGRATED_ROOTS
})

_missing_action_roots = sorted(_MIGRATED_ROOTS - set(_ROOT_ACTIONS))
if _missing_action_roots:
    raise RuntimeError(f"Command registry lost action roots: {_missing_action_roots}")

def _hub_page(root: str, label: str, description: str) -> HubPage:
    return HubPage(key=root, label=label, description=description, command=_ROOT_ACTIONS[root])


_HUB_DEFINITIONS = (
    HubDefinition(
        name="character",
        title="🧑 Cultivator — Character Hub",
        description="Identity, public character state, consequences, relationships and Samsara.",
        pages=(
            _hub_page("sheet", "Overview", "Your main character sheet and public cultivation overview."),
            _hub_page("gender", "Sex", "Male/Female identity used for gendered titles and forms of address."),
            _hub_page("lifespan", "Lifespan", "Age, lifespan and mortality state."),
            _hub_page("karma", "Karma", "Metaphysical karma and its known consequences."),
            _hub_page("fate", "Fate", "Spendable providence that can avert true death and grows through major fortunate deeds."),
            _hub_page("bond", "Dao Partnership", "Consensual partnership, paired cultivation resonance and Samsara partner echoes."),
            _hub_page("daoheart", "Dao Heart", "Dao-heart stability and sworn commitments."),
            _hub_page("reputation", "Reputation", "Persistent faction and social reputation."),
            _hub_page("grudges", "Grudges", "Personal, family and faction grudges."),
            _hub_page("crime", "Crimes", "Jurisdictional crimes, evidence and atonement."),
            _hub_page("bounty", "Bounties", "Active capture/death bounties."),
            _hub_page("inheritances", "Inheritances", "Ancient inheritances you have obtained."),
            _hub_page("effects", "Conditions & Effects", "Generic buffs, debuffs, curses and conditions."),
            _hub_page("specialeffects", "Special Effects", "Law, domain, curse and control effects."),
            _hub_page("condition", "Treatment", "Inspect and treat persistent injuries and deviations."),
            _hub_page("soul", "Soul", "Soul state and legacy across incarnations."),
            _hub_page("afterlife", "Samsara", "Afterlife state and reincarnation timing."),
            _hub_page("reincarnate", "Reincarnate", "Begin the next incarnation when Samsara permits it."),
        ),
    ),
    HubDefinition(
        name="quest",
        title="☯ Quest Journal",
        description="Progression objectives, Perfection paths and ascension gates.",
        pages=(
            _hub_page("breakthrough", "Main Progression", "Normal realm breakthrough and Stage 9 progression."),
            _hub_page("perfect", "Realm Perfection", "Optional Stage 9 Realm Perfection path."),
            _hub_page("bodyperfect", "Body Perfection", "Optional Stage 9 Body Realm Perfection path."),
            _hub_page("tribulation", "Tribulation / Ascension", "Prepare for and attempt heavenly tribulations."),
            _hub_page("bounty", "Bounties", "Active bounty objectives and consequences."),
        ),
    ),
    HubDefinition(
        name="cultivation",
        title="🧘 Cultivation Hub",
        description="Meditation, seclusion, body cultivation, aptitudes, Laws, manuals and concealment.",
        pages=(
            _hub_page("cultivate", "Meditation", "Gather cultivation essence."),
            _hub_page("seclusion", "Seclusion", "Start, inspect or end closed-door cultivation."),
            _hub_page("body", "Body Cultivation", "Parallel body-cultivation progression."),
            _hub_page("aptitude", "Aptitudes", "Roots, bloodlines, physiques and aptitude progression."),
            _hub_page("law", "Laws", "Comprehend and wield Laws."),
            _hub_page("manual", "Manuals & Techniques", "Study manuals and use learned techniques."),
            _hub_page("conceal", "Concealment", "Toggle cultivation-aura concealment."),
            _hub_page("profession", "Profession", "Cultivation-profession mastery status."),
        ),
    ),
    HubDefinition(
        name="items",
        title="🎒 Items Hub",
        description="Inventory, storage, equipment, artifacts, consumables and provenance.",
        pages=(
            _hub_page("inventory", "Inventory", "View carried items and materials."),
            _hub_page("storage", "Storage", "Inspect, deposit into and withdraw from spatial storage."),
            _hub_page("use", "Use Item", "Consume or activate a carried item."),
            _hub_page("equipment", "Equipment", "Bind, equip, unequip and repair durable equipment."),
            _hub_page("artifact", "Artifacts", "Bond and awaken personal artifacts."),
            _hub_page("provenance", "Provenance", "Inspect ownership marks, legality and tracking."),
        ),
    ),
    HubDefinition(
        name="npc",
        title="👥 NPC Hub",
        description="Public, location-aware NPC inspection and interaction.",
        pages=(
            _hub_page("npcinfo", "Inspect", "View public information for a known NPC."),
            _hub_page("talk", "Talk", "Speak with a persistent NPC."),
            _hub_page("sense", "Sense", "Use Spiritual Sense on NPCs, players or the area."),
        ),
    ),
    HubDefinition(
        name="world",
        title="🌍 World Hub",
        description="Your location, local actions, current events, civilization and world laws.",
        pages=(
            _hub_page("world", "Current Location", "Show the current world and known locations."),
            _hub_page("explore", "Explore", "Explore the current location for events and discoveries."),
            _hub_page("hunt", "Hunt", "Hunt a spirit beast at the current location."),
            _hub_page("worldevents", "Events", "Active phenomena, consequences and realm openings."),
            _hub_page("civilization", "Civilization", "Population, security and named regional NPC activity."),
            _hub_page("scene", "Scene", "Current roleplay scene and in-world time."),
            _hub_page("era", "Era", "The active era and cycle transitions."),
            _hub_page("time", "Time", "Canonical cultivation calendar."),
            _hub_page("rulers", "Rulers", "Publicly recognized rulers."),
            _hub_page("worldrules", "World Laws", "Rules governing NPCs, sects, families and forbidden arts."),
        ),
    ),
    HubDefinition(
        name="travel",
        title="🗺 Travel Hub",
        description="Choose destinations, teleportation arrays and special movement options.",
        pages=(
            _hub_page("travel", "Destinations", "Travel to another known normal destination."),
            _hub_page("realmhub", "Realm Capitals", "Travel to and inspect the public meeting city for every realm world."),
            _hub_page("array", "Teleportation Arrays", "List and use public teleportation formations."),
        ),
    ),
    HubDefinition(
        name="combat",
        title="⚔️ Combat Hub",
        description="Battles, duels, parties, formations, bosses and bounty-hunter pursuits.",
        pages=(
            _hub_page("battle", "Active Battle", "Current battle, challenges and battle actions."),
            _hub_page("duel", "Duels", "Consent-gated nonlethal PvP duels."),
            _hub_page("party", "Party", "Create, join, inspect or leave cultivation parties."),
            _hub_page("formation", "Formations", "Assign party positions and formation stances."),
            _hub_page("boss", "Boss Raids", "Persistent multi-phase party boss encounters."),
            _hub_page("hunter", "Bounty Hunter", "Respond to autonomous bounty-hunter pursuits."),
        ),
    ),
    HubDefinition(
        name="economy",
        title="💰 Economy Hub",
        description="Wallet, local markets, black markets, protected auctions and trade caravans.",
        pages=(
            _hub_page("wallet", "Wallet", "View cultivation currencies."),
            _hub_page("market", "Local Market", "Buy and sell in the dynamic local economy."),
            _hub_page("blackmarket", "Black Market", "Locate rotating underworld posts and trade forbidden goods."),
            _hub_page("auction", "Auction House", "Browse, list and bid in protected auctions."),
            _hub_page("caravan", "Caravans", "Dispatch and inspect persistent trade caravans."),
        ),
    ),
    HubDefinition(
        name="craft",
        title="🛠 Craft Hub",
        description="Craft alchemy, forging and inscription recipes; deploy shared location arrays through Items → Use Item.",
        pages=(
            _hub_page("alchemy", "Alchemy", "Refine pills, forage simulated herb resources, track toxicity and purge medicinal residue."),
            _hub_page("craft", "General Crafting", "Practice alchemy, forging or Formation inscription from known recipes."),
            _hub_page("profession", "Profession", "View crafting and support-profession mastery."),
        ),
    ),
    HubDefinition(
        name="beast",
        title="🐉 Beast Hub",
        description="Manage contracted spirit beasts and the active companion.",
        pages=(_hub_page("beast", "Companions", "Inspect, train, evolve and activate contracted beasts."),),
    ),
    HubDefinition(
        name="sect",
        title="🏯 Sect Hub",
        description="Sect membership, player discipleship, shared manor, resources, politics, territory and war.",
        pages=(
            _hub_page("sect", "Sect", "Membership, player discipleship, shared manor, treasury and martial family."),
            _hub_page("territory", "Territory", "Persistent territory control and claims."),
            _hub_page("war", "War", "Sieges, defenses and territorial conflict actions."),
        ),
    ),
    HubDefinition(
        name="family",
        title="🏠 Family Hub",
        description="Birth family, clan structure, descendants, support and family history.",
        pages=(_hub_page("family", "Family", "View and manage your persistent birth-family branch."),),
    ),
    HubDefinition(
        name="abode",
        title="🏡 Abode Hub",
        description="Establish, enter, upgrade and manage access to your private player-owned location.",
        pages=(_hub_page("abode", "Player Property", "Private property type, facilities, visitors and location-scene access."),),
    ),
    HubDefinition(
        name="innerworld",
        title="🌌 Inner World Hub",
        description="Create, enter and define rules for a stabilized personal world.",
        pages=(_hub_page("innerworld", "Personal World", "Personal-world creation, rules and travel."),),
    ),
    HubDefinition(
        name="realm",
        title="🌀 Secret Realm Hub",
        description="Secret realms, active expeditions and spatial keys.",
        pages=(
            _hub_page("secretrealm", "Secret Realms", "Available realms and active realm exploration."),
            _hub_page("spatialkey", "Spatial Keys", "Use a key/token to open its linked dimension."),
        ),
    ),
)

_HUB_BY_NAME = {definition.name: definition for definition in _HUB_DEFINITIONS}


def _hub_resource_value(current: object, maximum: object) -> str:
    current_value = max(0, int(current or 0))
    maximum_value = max(1, int(maximum or 1))
    percentage = max(0, min(100, round(current_value * 100 / maximum_value)))
    filled = max(0, min(10, round(percentage / 10)))
    bar = "▰" * filled + "▱" * (10 - filled)
    return f"`{bar}` **{percentage}%**\n{current_value:,} / {maximum_value:,}"


async def _player_hub_status(interaction: discord.Interaction) -> list[HubStatusField]:
    character = await DB.get_character(interaction.user.id)
    if character is None:
        return [
            HubStatusField("🌱 Character", "Not created — use **/begin**", inline=False),
        ]
    inventory = await DB.get_inventory(interaction.user.id)
    item_stacks = sum(max(0, int(quantity)) for quantity in inventory.values())
    realm = WORLD.realm_name(int(character.get("realm_index", 0)), character.get("gender"))
    return [
        HubStatusField(
            "☯️ Realm",
            f"**{realm}** • Stage **{int(character.get('phase', 1))}**",
        ),
        HubStatusField(
            "❤️ Vitality",
            _hub_resource_value(character.get("vitality"), character.get("vitality_max")),
        ),
        HubStatusField(
            "💠 Qi",
            _hub_resource_value(character.get("qi"), character.get("qi_max")),
        ),
        HubStatusField(
            "🎒 Items",
            f"**{item_stacks:,}** total • {len(inventory)} types",
        ),
        HubStatusField(
            "📍 Location",
            f"**{str(character.get('location') or 'Unknown')[:180]}**",
            inline=False,
        ),
    ]


async def _admin_hub_status(interaction: discord.Interaction) -> list[HubStatusField]:
    guild = interaction.guild
    return [
        HubStatusField("🌐 Server", f"**{getattr(guild, 'name', 'Unknown server')}**"),
        HubStatusField("👥 Members", f"**{int(getattr(guild, 'member_count', 0) or 0):,}**"),
        HubStatusField("🗂️ Channels", f"**{len(getattr(guild, 'channels', ()) or ()):,}**"),
        HubStatusField("🗃️ Database", f"Schema **{SCHEMA_VERSION}**"),
    ]


def _build_hub_command(definition: HubDefinition) -> app_commands.Command:
    async def hub_command(interaction: discord.Interaction) -> None:
        await send_hub(interaction, definition, status_provider=_player_hub_status)

    hub_command.__name__ = f"{definition.name}_hub_command"
    return app_commands.command(
        name=definition.name,
        description=definition.description[:100],
    )(hub_command)


_HUB_COMMANDS = tuple(_build_hub_command(definition) for definition in _HUB_DEFINITIONS)

# ---------------------------------------------------------------------------
# Administrator control panel
# ---------------------------------------------------------------------------
# Admin action groups are internal hub metadata. Only the single /admin surface
# is registered with Discord; its actions resolve through ACTIONS explicitly.
_ADMIN_HUB_DEFINITION = HubDefinition(
    name="admin",
    title="🛡️ Xianxia — Administrator Control Panel",
    description=(
        "Server configuration, GM controls and world simulation in one interactive panel. "
        "Choose a section, then choose an action. Actions use the explicitly registered canonical handlers and audit logging."
    ),
    pages=(
        HubPage(key="server", label="Server", description="Channels, health, maintenance, backups and audit logs.", command=admin_server_group),
        HubPage(key="world", label="World", description="Events, secret realms and canonical world time.", command=admin_world_group),
        HubPage(key="player", label="Players", description="Inspect, restore, reward or move cultivators.", command=admin_player_group),
        HubPage(key="sect", label="Sects", description="Membership, ranks and master/disciple administration.", command=admin_sect_group),
        HubPage(key="family", label="Families", description="Inspect hidden birth-family state.", command=admin_family_group),
        HubPage(key="npc", label="NPCs", description="Inspect hidden canonical NPC state.", command=admin_npc_group),
        HubPage(key="simulation", label="Simulation", description="Automation, regions, markets, factions and world simulation.", command=admin_sim_group),
    ),
)


@registered_root_command(
    name="admin",
    description="Open the Xianxia administrator control panel",
    guild=GUILD,
)
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def admin_panel(interaction: discord.Interaction) -> None:
    if not await require_admin(interaction):
        return
    await send_hub(interaction, _ADMIN_HUB_DEFINITION, status_provider=_admin_hub_status)

async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
) -> None:
    log.exception("Application command error", exc_info=error)
    command_name = interaction.command.qualified_name if interaction.command else "unknown"
    original = getattr(error, "original", error)
    await post_server_log(
        interaction.guild,
        "Application command error",
        f"Command: `/{command_name}`\n"
        f"User: **{interaction.user}** (`{interaction.user.id}`)\n"
        f"Channel: <#{interaction.channel_id}>\n"
        f"Error: `{type(original).__name__}: {str(original)[:900]}`",
    )
    message = "Something went wrong, but no game-state change was intentionally applied. An administrator can check the configured bot log channel."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=False)
    else:
        await interaction.response.send_message(message, ephemeral=False)


def register_command_surface(client: XianxiaBot) -> None:
    """Register only public Discord commands; gameplay actions stay internal."""
    for name in ("begin", "me", "quests", "action", "act", "check", "admin"):
        client.tree.add_command(ACTIONS.root(name), guild=GUILD)
    for command in _HUB_COMMANDS:
        client.tree.add_command(command, guild=GUILD)
    client.tree.error(on_app_command_error)


def register_event_handlers() -> None:
    bindings = {
        "talk": talk,
        "battle": battle_status,
        "secret": secret_status,
        "war": war_status,
        "auction": auction_browse,
        "party": party_status,
        "boss": boss_status,
        "formation": formation_status,
        "hunter": hunter_status,
        "blackmarket": blackmarket_status,
        "civilization": civilization_status_command,
        "scene": scene_status,
    }
    for name, command in bindings.items():
        EVENT_HANDLERS.register(name, ACTIONS.handler_for(command))


register_event_handlers()
register_command_surface(bot)


def run() -> None:
    try:
        bot.run(SETTINGS.discord_token, log_handler=None)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
