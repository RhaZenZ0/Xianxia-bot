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


def hidden_npc_names(
    hidden_masters: Mapping[str, Mapping[str, Any]],
    location: str | None = None,
) -> list[str]:
    return [
        str(name)
        for name, npc in hidden_masters.items()
        if not location or npc.get("location") == location
    ]


