"""The procedural floor (v0.31.0): what a player reads when no model answers.

Procedural prose is not an error path. It is what the narrator returns when
the daily allowance is spent, when every route is retired, when the
provider is `procedural`, and - since v0.31.0 - by default for the scenes
the engine has already decided (an exploration opening, a hunt result), where
a model would only be describing a result that is already fixed. So the
floor has to read well, and it has to vary.

`content/world.json` holds a `narration_pool`: for each scene kind, a few
variants per world tier (Mortal, Spiritual, Immortal, Celestial), written
with `{placeholder}` fields. This module picks one deterministically - the
same seed always yields the same line, so a retry does not reshuffle the
prose a player already read - and fills it. It decides nothing: every line
describes a situation or a fixed result and leaves the next move to the
player.
"""
from __future__ import annotations

import hashlib
from typing import Any, Iterable

TIERS: tuple[str, ...] = ("Mortal World", "Spiritual World", "Immortal World", "Celestial World")
SCENE_KINDS: tuple[str, ...] = (
    "exploration", "hunt_success", "hunt_failure", "action", "dialogue",
    "breakthrough_success", "breakthrough_failure",
)


class _Blank(dict):
    """Leave an unknown placeholder visible rather than raising mid-narration."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def narration_tier(world_data: dict[str, Any], location: str | None, realm_index: int | None = None) -> str:
    """The world tier a scene belongs to.

    A catalogue location names its world. A private location (an abode, a
    sect residence, a personal world, a household) does not, so the
    character's realm decides: the tier of the world their cultivation has
    reached, which is where such a place exists. Anything else is Mortal.
    """
    locations = dict(world_data.get("locations") or {})
    definition = locations.get(str(location or ""))
    if isinstance(definition, dict) and str(definition.get("world") or "") in TIERS:
        return str(definition["world"])
    if realm_index is not None:
        realms = list(world_data.get("realms") or [])
        index = max(0, min(int(realm_index), len(realms) - 1)) if realms else -1
        if index >= 0:
            world = str(dict(realms[index]).get("world") or "")
            if world in TIERS:
                return world
    return TIERS[0]


def pool_variants(world_data: dict[str, Any], kind: str, tier: str) -> list[str]:
    scenes = dict((world_data.get("narration_pool") or {}).get("scenes") or {})
    scene = dict(scenes.get(str(kind)) or {})
    variants = dict(scene.get("variants") or {})
    rows = variants.get(str(tier)) or variants.get(TIERS[0]) or []
    return [str(row) for row in rows if str(row).strip()]


def _choose(rows: list[str], seed: Iterable[Any]) -> str:
    digest = hashlib.sha256("|".join(str(part) for part in seed).encode("utf-8")).digest()
    return rows[int.from_bytes(digest[:4], "big") % len(rows)]


def procedural_narration(
    world_data: dict[str, Any],
    kind: str,
    *,
    tier: str,
    seed: Iterable[Any],
    fallback: str,
    **fields: Any,
) -> str:
    """One filled line from the pool for `kind` at `tier`, chosen by `seed`.

    `fallback` is returned when the content has no pool for the kind, so a
    content gap reads as the old single line rather than as nothing.
    """
    rows = pool_variants(world_data, kind, tier)
    if not rows:
        return fallback
    return _choose(rows, seed).format_map(_Blank({k: str(v) for k, v in fields.items()}))
