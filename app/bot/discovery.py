"""Landmark discovery art: which locations have a picture, and showing it once.

Phase 4 of the main.py split (v0.19.39, docs/MAIN_SPLIT_PLAN.md). Leaf module:
reads runtime (ROOT, WORLD, log) and discord, nothing else. Definition order is
the order these had in main.py.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import discord

from .runtime import ROOT, WORLD, log

# Optional landmark art shown exactly when a character first reaches/discovers
# the associated location. Keep this mapping small and explicit so adding art
# never changes canonical world/discovery mechanics.
LOCATION_DISCOVERY_IMAGES: dict[str, Path] = {
    "Azure Crown Imperial City": ROOT / "assets" / "locations" / "azure_crown_imperial_city.png",
}


def location_discovery_image_path(location: str) -> Path | None:
    path = LOCATION_DISCOVERY_IMAGES.get(str(location))
    return path if path is not None and path.is_file() else None


def location_discovery_embed(location: str, *, filename: str) -> discord.Embed:
    world_name = str((WORLD.locations.get(str(location)) or {}).get("world") or "Mortal World")
    embed = discord.Embed(
        title=f"🏙️ First Sight — {location}",
        description=(
            f"For the first time, **{location}** opens before you — the central city of the **{world_name}**."
            if str(location) == "Azure Crown Imperial City"
            else f"For the first time, **{location}** opens before you."
        ),
    )
    embed.set_image(url=f"attachment://{filename}")
    return embed


async def send_location_discovery_image(
    interaction: discord.Interaction,
    location: str,
    *,
    thread: discord.Thread | None = None,
) -> bool:
    """Send configured landmark art without mutating discovery state.

    The caller is responsible for proving this is the character's first
    canonical discovery/arrival. That keeps image delivery separate from the
    authoritative Go-owned mechanics.
    """
    path = location_discovery_image_path(location)
    if path is None:
        return False
    filename = path.name
    file = discord.File(path, filename=filename)
    embed = location_discovery_embed(location, filename=filename)
    try:
        if thread is not None:
            await thread.send(embed=embed, file=file)
        elif interaction.response.is_done():
            await interaction.followup.send(embed=embed, file=file, ephemeral=False)
        else:
            await interaction.response.send_message(embed=embed, file=file, ephemeral=False)
        return True
    except discord.HTTPException:
        log.exception("Could not send discovery image for %s", location)
        return False


def travel_first_discovers_location(
    result: dict[str, Any],
    location: str,
    *,
    previously_discovered: bool,
) -> bool:
    """True when a successful authoritative travel first records location.

    Road travel records every traversed node, while direct/hub travel records
    its destination. The pre-action DB check prevents repeat image sends.
    """
    if previously_discovered:
        return False
    target = str(location)
    route = [str(x) for x in list(result.get("road_route") or [])]
    if target in route:
        return True
    return str(result.get("destination") or "") == target


