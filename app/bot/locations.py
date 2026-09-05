"""Where things are: known/visible locations, world gating, NPC whereabouts.

Phase 3 of the main.py split (v0.19.35, docs/MAIN_SPLIT_PLAN.md). The scope-
accurate dependency scan behind the plan found that 15 of the 19 edges between
the player-command sub-domains still in main.py all pointed at these helpers -
they were buried in the travel section but read by perception, market, scene,
territory, storage and sect code. Pulling them below main.py is what lets those
domains be cut out one at a time without cross-imports.

`current_npc_location` came along too (the plan had it in a later module): it
is the one dependency of `local_npc_autocomplete`, reads only `SIM`/`WORLD`/
`current_world_time`, and is about where an NPC is - the same question as the
rest of this file.

Two of these (`location_autocomplete`, `local_npc_autocomplete`) are referenced
as bare `@app_commands.autocomplete(...)` arguments, which evaluate when the
decorated command's module is imported - so they must be importable at module
level, never behind a deferred import. Nothing here imports main.py, and the
definition order is the order these had in main.py.
"""
from __future__ import annotations

from typing import Any

import discord
from discord import app_commands

from ..rules.realm_hubs import REALM_HUBS
from .runtime import DB, WORLD, current_world_time
from .services import SIM

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


