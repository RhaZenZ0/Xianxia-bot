"""Shared runtime for the Discord bot: the singletons and the session helpers.

Extracted from ``main.py`` as the first stage of its decomposition. Every command
module needs the same small core - the database and engine handles, the
"does this user have a living character" gate, the reply chunker, the
per-user action lock, canonical world time - and none of it is Discord command
surface, so it lives here where a command module can import it without importing
``main`` and creating a cycle.

Nothing in this module imports ``main``. That is the property that makes the rest
of the split possible, and it is asserted by tests/python/unit/test_bot_package.py.
"""
from __future__ import annotations

import asyncio
import time
import logging
import re
from functools import wraps
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord

# NOT UNUSED, and it must never be trimmed as such.
#
# serialized_user_action below wraps ~119 command callbacks that live in other
# modules. functools.wraps copies __name__ and __doc__ but CANNOT copy
# __globals__ - that belongs to the code object's defining module - so the
# wrapper discord.py receives carries THIS module's globals. main.py uses
# `from __future__ import annotations`, so a signature like
#     async def aptitude_temper(interaction, target: app_commands.Choice[str])
# reaches discord.py as the *string* "app_commands.Choice[str]", and
# discord.utils.resolve_annotation evals it against callback.__globals__ - that
# is, against this file. Without this import the bot dies at startup with
# `NameError: name 'app_commands' is not defined`, which is exactly what
# happened when a static "unused import" pass removed it in v0.19.13.
#
# The rule: this module must import every name that appears in the ANNOTATIONS
# of any function it decorates, not merely the names its own code executes.
# tests/python/unit/test_bot_package.py enforces that.
from discord import app_commands

# Shared by /begin, /family -> Child and the admin gender override. A literal
# list with no dependency beyond app_commands above, so it carries no ordering
# risk - the failure mode that killed v0.19.12.
GENDER_CHOICES = [
    app_commands.Choice(name="Male", value="male"),
    app_commands.Choice(name="Female", value="female"),
]

from ..ops.config import Settings
from ..database import Database
from ..rules.game import World
from ..ops.game_engine import GameEngineClient, GameEngineError
from ..ops.user_budget import UserBudget
from ..rules.realm_hubs import REALM_HUBS, presence_world_for, realm_presence_role_name
from ..simulation import MINUTES_PER_DAY
from ..rules.worldtime import from_game_minutes, MINUTES_PER_YEAR


log = logging.getLogger("xianxia")
SETTINGS = Settings.from_env()
ROOT = Path(__file__).resolve().parents[2]
WORLD = World(ROOT / "content" / "world.json")
ENGINE = GameEngineClient(
    SETTINGS.game_engine_url,
    timeout_seconds=SETTINGS.game_engine_timeout_seconds,
    auth_token=SETTINGS.game_engine_auth_token,
)
DB = Database(
    ROOT / SETTINGS.database_path,
    slow_query_ms=SETTINGS.slow_query_ms,
    engine_url=SETTINGS.game_engine_url,
)
_USER_ACTION_LOCKS: dict[int, asyncio.Lock] = {}
# When each lock was last taken. A lock is evicted once its holder has been
# idle this long and nothing is waiting on it, so the table follows the
# players who are actually playing rather than everyone who ever has.
_USER_ACTION_LOCK_SEEN: dict[int, float] = {}
USER_ACTION_LOCK_IDLE_SECONDS = 1800.0
# Per-player token bucket for typed play (v0.21.1). Every typed line that can
# reach the engine or the narrator spends a token; speech-only lines are free.
# See app/ops/user_budget.py for why this exists and what it protects.
TYPED_PLAY_BUDGET = UserBudget(burst=SETTINGS.typed_play_burst, per_minute=SETTINGS.typed_play_per_minute)
def _player_property_types() -> dict[str, dict[str, Any]]:
    configured = WORLD.abode_system.get("property_types", {})
    if isinstance(configured, dict) and configured:
        return {str(key): dict(value) for key, value in configured.items() if isinstance(value, dict)}
    return {
        "homestead": {"name": "Homestead", "emoji": "🏡", "defaults": {"cultivation": 1, "storage": 1}},
    }
PLAYER_PROPERTY_TYPES = _player_property_types()
def player_property_definition(property_type: str | None) -> dict[str, Any]:
    key = str(property_type or "homestead")
    return PLAYER_PROPERTY_TYPES.get(key, PLAYER_PROPERTY_TYPES.get("homestead", {}))
def player_property_label(abode: dict[str, Any]) -> str:
    definition = player_property_definition(str(abode.get("property_type") or "homestead"))
    return str(definition.get("name") or "Player Property")
async def character_location_display(character: dict[str, Any]) -> str:
    """Resolve a character's raw `location` column into a player-facing label.

    `location` holds either a real world-catalog location name (already
    display-ready) or one of four internal sentinel prefixes for a private
    scene - `abode:`, `sect_abode:`, `personal_world:`, `birth_family:` -
    which need a DB lookup to turn into a readable place name. Falls back to
    the raw value unchanged if none of the lookups resolve (e.g. a plain
    world-catalog location, or a stale/orphaned private-location key).
    """
    location = str(character.get("location") or "Unknown")
    abode_location = await DB.get_abode_by_location(location)
    if abode_location:
        return f"{abode_location['name']} ({player_property_label(abode_location)})"
    personal_location = await DB.get_personal_world_by_location(location)
    if personal_location:
        return f"{personal_location['name']} (Personal World)"
    sect_abode_location = await DB.get_sect_abode_by_location(location)
    if sect_abode_location:
        return f"{sect_abode_location['name']} (Sect Abode)"
    if location.startswith("birth_family:"):
        family = await DB.get_birth_family(int(character.get("user_id") or 0))
        if family and location == f"birth_family:{int(family.get('family_id') or 0)}":
            return f"{family.get('family_name') or 'Birth Family'} Household"
    return location
# Shared by /storage (main.py) and /sect -> Contribute/Redeem (commands/sect.py).
# It is referenced as a bare `@app_commands.autocomplete(item=carried_item_autocomplete)`
# argument in both, which evaluates at module-IMPORT time rather than call time -
# a deferred `from ..main import` (the pattern used for names like SIM below)
# cannot reach it, so it lives here instead of being duplicated.
async def carried_item_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    inv = await DB.get_inventory(interaction.user.id); needle = current.casefold().strip(); out = []
    for item_id, qty in inv.items():
        name = WORLD.item_name(item_id)
        if not needle or needle in name.casefold() or needle in item_id.casefold():
            out.append(app_commands.Choice(name=f"{name} x{qty}"[:100], value=item_id[:100]))
    return out[:25]
PRIVATE_LOCATION_EXITS: tuple[tuple[str, str, str], ...] = (
    ("birth_family:", "**/family → Leave**", "your birth household"),
    ("sect_abode:", "**/abode → Leave**", "your sect residence"),
    ("abode:", "**/abode → Leave**", "your own property"),
    ("personal_world:", "**/innerworld → Leave**", "your personal world"),
)
def private_location_exit(location: object) -> tuple[str, str] | None:
    """The command that steps a character back out into the shared world.

    Exploring and hunting are blocked inside every private location (the prefix
    guards in the Go engine's exploration_actions.go). A player who does not know
    which command gets them out is simply stuck, so wherever we can tell which
    private location they are in, we name the exact way out rather than listing
    all of them. Returns (command, human description), or None out in the world.

    Note the ordering: "sect_abode:" is checked before "abode:" because the
    latter is a prefix of the former and would otherwise swallow it.
    """
    text = str(location or "")
    for prefix, command, description in PRIVATE_LOCATION_EXITS:
        if text.startswith(prefix):
            return command, description
    return None
# The engine's three cooldown shapes: "cultivation cooldown remaining: 10520",
# "cooldown active: 90 seconds" (explore/hunt), "manual study cooldown: 5 seconds".
_COOLDOWN_REMAINING_RE = re.compile(
    r"^(?P<what>[A-Za-z][A-Za-z /-]*?)?\s*cooldown(?: remaining| active)?:\s*(?P<seconds>\d+)(?:\s*seconds?)?\s*$"
)


def format_wait(seconds: int) -> str:
    """10520 -> '2h 55m'; 90 -> '1m 30s'; 0 -> 'a moment'. Never a bare number."""
    seconds = max(0, int(seconds))
    if seconds == 0:
        return "a moment"
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    if minutes:
        return f"{minutes}m {secs}s" if secs else f"{minutes}m"
    return f"{secs}s"


def _explain_engine_error(exc: Exception) -> str:
    """Append an actionable hint to specific known engine errors that otherwise
    leave the player stuck with no indication of what to do next - most notably
    the "world exploration/hunting is unavailable inside a private residence or
    personal world" error every freshly-created character used to hit immediately
    (they start inside their birth household - see character.create in the Go
    engine - with no way back out surfaced anywhere in the UI). Falls through to
    the raw engine message unchanged for everything else.
    """
    text = str(exc)
    # "cultivation cooldown remaining: 10520" is the engine's exact truth in raw
    # seconds and no help to a player. Keep the truth, say it as a wait
    # (v0.21.5). The number is real time: cooldowns are wall-clock, set from
    # *_COOLDOWN_MINUTES, not world time.
    cooldown = _COOLDOWN_REMAINING_RE.search(text)
    if cooldown:
        what = (cooldown.group("what") or "").strip() or "This action"
        seconds = int(cooldown.group("seconds"))
        text = f"⏳ {what[:1].upper()}{what[1:]} is still on cooldown — ready in **{format_wait(seconds)}**."
    if "private residence or personal world" in text:
        # Spell these the way a player can actually reach them. The individual
        # gameplay commands are not registered with Discord - only the 16 hub
        # commands are (see register_command_surface) - so "/family leave" is a
        # dead end: it does not exist to type. The route is the hub, then the
        # action inside it.
        text += (
            "\n\nYou're **indoors** — exploring and hunting only work out in the shared world. "
            "Step outside first, then try again:\n"
            "**/family → Leave** (birth household) · "
            "**/abode → Leave** (your property or sect residence) · "
            "**/innerworld → Leave** (personal world)"
        )
    return text
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
def budget_refusal_line(user_id: int, door: str) -> str | None:
    """None if the player may spend a token on this door now; else the reply.

    One bucket, every door (v0.31.0): a player who is refused a typed line
    is refused the same action as a slash command or a hub button, and the
    per-door counts on the AI Routing page say which door it was.
    """
    if TYPED_PLAY_BUDGET.try_acquire(int(user_id), door=door):
        return None
    wait = TYPED_PLAY_BUDGET.seconds_until_token(int(user_id))
    return (
        f"⏳ Actions are limited to about {SETTINGS.typed_play_per_minute:g} a minute. "
        f"Try again in {max(1, int(round(wait)))}s — or just say it in character, that's free."
    )


def _user_action_lock(user_id: int) -> asyncio.Lock:
    now = time.monotonic()
    cutoff = now - USER_ACTION_LOCK_IDLE_SECONDS
    for stale_id in [uid for uid, seen in _USER_ACTION_LOCK_SEEN.items() if seen <= cutoff]:
        lock = _USER_ACTION_LOCKS.get(stale_id)
        if lock is None or not lock.locked():
            _USER_ACTION_LOCKS.pop(stale_id, None)
            _USER_ACTION_LOCK_SEEN.pop(stale_id, None)
    _USER_ACTION_LOCK_SEEN[int(user_id)] = now
    return _USER_ACTION_LOCKS.setdefault(int(user_id), asyncio.Lock())


def serialized_user_action(func):
    """Serialize state-changing commands per Discord user in this bot process.

    Cooldown checks and game-state writes often span more than one SQLite call.
    Without this guard, two near-simultaneous slash commands from the same user
    could both pass a cooldown/read check before either write completed.

    Since v0.31.0 the wrapper is also a door of the per-player budget: every
    state-changing command spends a token from the same bucket typed play
    spends, and a refused one is answered rather than queued.
    """
    @wraps(func)
    async def wrapper(interaction: discord.Interaction, *args, **kwargs):
        refusal = budget_refusal_line(interaction.user.id, "slash")
        if refusal:
            if interaction.response.is_done():
                await interaction.followup.send(refusal, ephemeral=False)
            else:
                await interaction.response.send_message(refusal, ephemeral=False)
            return None
        lock = _user_action_lock(interaction.user.id)
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
async def reply_long(
    interaction: discord.Interaction,
    text: str,
    *,
    ephemeral: bool = False,
) -> None:
    """Send long responses while preserving the caller's visibility choice."""
    chunks = chunk_text(text)
    if not interaction.response.is_done():
        await interaction.response.send_message(chunks[0], ephemeral=ephemeral)
    else:
        await interaction.followup.send(chunks[0], ephemeral=ephemeral)
    for chunk in chunks[1:]:
        await interaction.followup.send(chunk, ephemeral=ephemeral)
async def current_world_time():
    state = await DB.get_world_clock(scale=SETTINGS.world_time_scale)
    return from_game_minutes(int(state["game_minute"]))
def _realm_access_role_name(world_name: str) -> str:
    return f"Xianxia • {world_name}"[:100]
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
async def _sync_realm_presence_roles(
    guild: discord.Guild | None, member: discord.Member | discord.User, character: dict[str, Any]
) -> None:
    """Give the member exactly the presence role of the capital they stand in (v0.21.6).

    Realm capitals are hidden until you are in the city: the channel allows
    only "Xianxia • <capital>", and this puts that role on when the
    character's location is the capital and takes it off the moment it is
    not. Runs from require_character (every command), on_message and right
    after hub travel, and makes no API call when nothing changed.
    """
    if guild is None or not isinstance(member, discord.Member):
        return
    me = guild.me
    if not me or not me.guild_permissions.manage_roles:
        return
    role_map = {world: discord.utils.get(guild.roles, name=realm_presence_role_name(world)) for world in REALM_HUBS}
    available = {world: role for world, role in role_map.items() if role is not None}
    if not available:
        return
    here = presence_world_for(character.get("location"))
    current_ids = {role.id for role in member.roles}
    add_roles = [role for world, role in available.items() if world == here and role.id not in current_ids]
    remove_roles = [role for world, role in available.items() if world != here and role.id in current_ids]
    try:
        if add_roles:
            await member.add_roles(*add_roles, reason="Xianxia: arrived in a realm capital")
        if remove_roles:
            await member.remove_roles(*remove_roles, reason="Xianxia: left a realm capital")
    except discord.Forbidden:
        log.warning("Could not synchronize realm presence roles for user %s; check bot role hierarchy", member.id)
    except discord.HTTPException:
        log.exception("Could not synchronize realm presence roles for user %s", member.id)
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
async def respond(interaction: discord.Interaction, content: str | None = None, **kwargs: Any) -> None:
    """Send an interaction reply whether or not it was already acked.

    Any handler that may defer (or otherwise respond) before reaching its
    own reply needs this instead of a bare ``interaction.response.send_message``,
    which raises ``InteractionResponded`` the moment the response has already
    been used. Centralizing the is_done() check here means a caller never has
    to know whether IT deferred, a shared helper (like require_character
    below) deferred on its behalf, or nothing has responded yet.
    """
    if interaction.response.is_done():
        await interaction.followup.send(content, **kwargs)
    else:
        await interaction.response.send_message(content, **kwargs)


async def require_character(interaction: discord.Interaction, *, allow_deceased: bool = False) -> dict | None:
    character = await DB.get_character(interaction.user.id)
    if character is None:
        await respond(
            interaction,
            "You do not have a cultivator yet. Use **/begin** first.",
            ephemeral=False,
        )
        return None
    wt = await current_world_time()
    life = await authoritative_lifespan(interaction.user.id)
    if not life.ageless and life.total_years is not None and life.age_years >= life.total_years:
        if character.get("life_status") != "deceased":
            # This authoritative call can be slow, and unlike every other
            # caller of require_character, nothing has necessarily acked the
            # interaction yet at this point (require_character is usually
            # the very first thing a handler does). Defer defensively before
            # it so a slow old-age transition can never cost the interaction
            # token before ANY response goes out - the same failure mode the
            # 79-handler late-ack fix closes at each call site.
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=False)
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
                await respond(interaction, f"❌ Lifecycle authority rejected the old-age transition: {exc}", ephemeral=False)
                return None
            death = dict(envelope.get("result") or {})
            # The descriptive history mirror is source-key idempotent, so replay it
            # too: this repairs a prior best-effort history write without duplicating
            # or changing the already-authoritative lifecycle transition.
            await _record_true_death_history(interaction.user.id, death, wt.total_minutes)
            character["life_status"] = "deceased"
    if character.get("life_status") == "deceased" and not allow_deceased:
        await respond(
            interaction,
            f"🕯️ **{character['name']}** is dead. Their old family remains in world history, but the soul has entered Samsara. "
            "Use **/character → Samsara** to view the soul-cycle and **/character → Reincarnate** when rebirth opens.",
            ephemeral=False,
        )
        return None
    try:
        await _sync_realm_access_roles(interaction.guild, interaction.user, character)
        await _sync_realm_presence_roles(interaction.guild, interaction.user, character)
    except Exception:
        log.exception("Realm access role synchronization failed")
    return character
