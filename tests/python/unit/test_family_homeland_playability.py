from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WORLD_PATH = ROOT / "content" / "world.json"

FAMILY_HOMELANDS = {
    # Mortal World
    "Riverguard City",
    "Four-Roads Caravan City",
    "Emberforge City",
    "Stoneback Mountain City",
    "Cloudblade City",
    "Ironbanner City",
    "Moonfen City",
    "Frostwatch City",
    "Ashenwall City",
    "Azure Crown Imperial City",
    "Jadewood Medicine City",
    # Spiritual World
    "Jadeflow Spirit City",
    "Galevein Spirit City",
    "Vermilion Furnace City",
    "Stoneheart Spirit City",
    "Cloudedge Spirit City",
    "Spearwall Spirit City",
    "Moonfrost Spirit City",
    "Northwind Spirit City",
    "Broken Halo Spirit City",
    "Jade Crown Spirit City",
    "Hundred Herb Spirit City",
    # Immortal World
    "Immortal River City",
    "Skyroad Immortal City",
    "Solar Furnace Immortal City",
    "Adamant Body Immortal City",
    "Heavenblade Immortal City",
    "Golden Spear Immortal City",
    "Lunar Veil Immortal City",
    "Polar Gate Immortal City",
    "Fallen Star Immortal City",
    "Ninefold Noble Immortal City",
    "Jade Cauldron Immortal City",
    # Celestial World
    "Celestial River City",
    "Starroad Celestial City",
    "Solar Crucible Celestial City",
    "Worldstone Celestial City",
    "Firmament Blade City",
    "Mandate Spear City",
    "Lunar Shadow Celestial City",
    "Froststar Border City",
    "Ruined Constellation City",
    "Mandate Crown Celestial City",
    "Divine Herb Celestial City",
}


def load_world() -> dict:
    return json.loads(WORLD_PATH.read_text(encoding="utf-8"))


def test_every_family_homeland_is_explorable() -> None:
    world = load_world()
    locations = world["locations"]

    assert len(FAMILY_HOMELANDS) == 44
    for city in FAMILY_HOMELANDS:
        assert city in locations, city
        assert locations[city]["encounters"], city
        assert locations[city]["private"] is False, city


def test_every_family_homeland_has_a_local_steward() -> None:
    world = load_world()
    steward_locations = {
        npc["location"]
        for name, npc in world["npcs"].items()
        if name.endswith(" Family Steward")
    }

    assert FAMILY_HOMELANDS <= steward_locations


def test_greenriver_is_not_a_family_homeland() -> None:
    assert "Greenriver Town" not in FAMILY_HOMELANDS
