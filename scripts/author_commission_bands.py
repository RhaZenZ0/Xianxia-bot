"""Author the upper worlds' commission bands (v1.19.2): each city commission is
offered to a cultivator who can stand in its world.

A commission's `realm_band` is an inclusive realm range, and the offer an NPC
makes in conversation (`realm_band_allows` in `app/rules/commissions.py`) and
the city's board both read it. Every upper-world city commission was authored
with a Mortal band - "2-4" in the Spiritual World, "4-8" in the Immortal and
Celestial - while anybody who can stand there is past realm 8, 16 or 24, so in
conversation none of the seventy-eight was ever offered to anyone in the world
it belongs to. On the owner's call each world's city work sits inside its own
realms, the way the Mortal tiers sit inside realms 0 to 7.

A running world is carried onto the new bands by migration 76, whose pairs
are frozen in `app/database/core.py`.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "content" / "world.json"

# The Mortal band each world's city work was written with, and the band of
# that world's own realms it moves to.
BANDS = {
    "Spiritual World": ("2-4", "9-14"),
    "Immortal World": ("4-8", "18-23"),
    "Celestial World": ("4-8", "26-31"),
}


def commission_world(world: dict, commission: dict) -> str:
    """The world a commission's giver posts it in - the twin of
    `_commission_giver_home` and `cityOf`."""
    givers = world.get("commission_givers", {}) or {}
    giver = str(commission.get("giver_npc") or "")
    where = str((givers.get(giver) or {}).get("location") or (world["npcs"].get(giver) or {}).get("location") or "")
    loc = world["locations"].get(where) or {}
    return str(loc.get("world") or (world["locations"].get(str(loc.get("outside_location") or "")) or {}).get("world") or "")


def moved(world: dict) -> dict[str, tuple[str, str]]:
    """quest_key -> (old band, new band) for every commission this re-bands."""
    out: dict[str, tuple[str, str]] = {}
    for commission in world.get("commissions", []):
        bands = BANDS.get(commission_world(world, commission))
        if bands and str(commission.get("realm_band") or "") in (bands[0], bands[1]):
            out[str(commission["quest_key"])] = bands
    return out


def main() -> None:
    world = json.loads(PATH.read_text(encoding="utf-8"))
    changes = moved(world)
    for commission in world["commissions"]:
        if str(commission["quest_key"]) in changes:
            commission["realm_band"] = changes[str(commission["quest_key"])][1]
    PATH.write_text(json.dumps(world, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"re-banded {len(changes)} commissions")


if __name__ == "__main__":
    main()
