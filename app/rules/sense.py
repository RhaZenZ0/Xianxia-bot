from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


RealmName = Callable[[int, str | None], str]
RealmWorld = Callable[[int], str]


def approximate_realm(
    realm_index: int,
    stage: int,
    *,
    precision_tier: str,
    realm_name: RealmName,
    realm_world: RealmWorld,
    gender: str | None = None,
) -> str:
    name = realm_name(int(realm_index), gender)
    if precision_tier == "overwhelming":
        return f"{name} — Stage {int(stage)}"
    if precision_tier == "strong":
        low = max(1, int(stage) - 1)
        high = min(9, int(stage) + 1)
        return f"{name} — approximately Stage {low}-{high}"
    if precision_tier == "success":
        return name
    return f"a cultivator of the {realm_world(int(realm_index))}"


def ground_reading_line(ground: Mapping[str, Any] | None) -> str:
    """The line a sweep prints about the ground it is standing on, or "" when
    the sweep read nothing.

    The engine decides *how much* is known - `detail` is the precision tier
    collapsed to three rungs - and this only chooses the words. A sweep that
    failed sends no ground at all, which is why an empty mapping is a normal
    answer rather than an error.
    """
    if not ground:
        return ""
    quality = str(ground.get("quality", "ordinary"))
    where = str(ground.get("ground") or "")
    detail = str(ground.get("detail", "vague"))
    if detail == "vague":
        return f"The qi of this ground feels **{quality}**, though you cannot tell what shapes it."
    if detail == "named":
        return f"The qi of this ground is **{quality}**" + (f", gathered by **{where}**." if where else ".")
    multiplier = float(ground.get("multiplier", 1.0) or 1.0)
    world_qi = float(ground.get("world_qi", 1.0) or 1.0)
    return (
        f"The qi of this ground is **{quality}** — cultivation here runs at **×{multiplier:g}**"
        + (f", gathered by **{where}**" if where else "")
        + f", in a world whose qi runs at **×{world_qi:g}**."
    )


def hidden_npc_names(
    hidden_masters: Mapping[str, Mapping[str, Any]],
    location: str | None = None,
) -> list[str]:
    return [
        str(name)
        for name, npc in hidden_masters.items()
        if not location or npc.get("location") == location
    ]


