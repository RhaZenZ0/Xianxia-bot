"""The ghost road's ground, as the panel needs it (v1.3.1).

The twin of `deathQiGroundMultiplier` in `go_core/internal/game/death_qi.go`,
read off the same `death_qi_system.ground` block, so the panel can hide the
harvest where the engine would refuse it and the rites where they would be
refused. The engine stays the refusal; this is advertising, never a bound.
`tests/python/unit/test_the_ghost_ground_twin.py` holds the two floors to the
Go constants and the multiplier to the Go rule over every catalogue place.
"""
from __future__ import annotations

from typing import Any, Mapping

# `ghostHarvestGroundFloor` and `ghostAppeaseGroundCeiling` in death_qi.go.
GHOST_HARVEST_GROUND_FLOOR = 1.15
GHOST_APPEASE_GROUND_CEILING = 0.75


def death_qi_ground_multiplier(system: Mapping[str, Any], locations: Mapping[str, Mapping[str, Any]], location: str) -> float:
    """What this ground is worth to a ghost cultivator, in the engine's order:
    a road-side site's kind, a district, a living city's streets, the default."""
    ground = dict(system.get("ground") or {})
    base = float(ground.get("default") or 0)
    if base <= 0:
        base = 1.0
    place = locations.get(location)
    if not place:
        return round(base, 4)
    road_site = str(place.get("road_site") or "")
    mult = float((ground.get("road_sites") or {}).get(road_site) or 0)
    if road_site and mult > 0:
        return round(mult, 4)
    district = str(place.get("district") or "")
    mult = float((ground.get("districts") or {}).get(district) or 0)
    if district and mult > 0:
        return round(mult, 4)
    penalty = float(ground.get("city_penalty") or 0)
    if str(place.get("settlement_type") or "") and penalty > 0:
        return round(penalty, 4)
    return round(base, 4)
