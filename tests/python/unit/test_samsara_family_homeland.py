import json

from tests.support import PROJECT_ROOT

from app.rules.birthfamily import FAMILY_HOMELANDS

WORLDS = ("Mortal World", "Spiritual World", "Immortal World", "Celestial World")
# Every city a birth family can call home, straight from the rules table
# (11 archetypes x 4 worlds). test_family_homeland_playability.py used to
# hand-copy the 44 names; merged here in v0.20.3.
HOMELAND_CITIES = {profile[world] for profile in FAMILY_HOMELANDS.values() for world in WORLDS}


EXPECTED_STARTERS = {
    "martial_household": ("Han", "Riverguard City"),
    "escort_martial_family": ("Chen", "Four-Roads Caravan City"),
    "weaponsmith_martial_family": ("Wei", "Emberforge City"),
    "body_tempering_family": ("Zhao", "Stoneback Mountain City"),
    "sword_hall_family": ("Shen", "Cloudblade City"),
    "spear_guard_family": ("Lin", "Ironbanner City"),
    "hidden_weapon_family": ("Su", "Moonfen City"),
    "border_garrison_family": ("Gu", "Frostwatch City"),
    "fallen_martial_clan": ("Luo", "Ashenwall City"),
    "noble_martial_clan": ("Qin", "Azure Crown Imperial City"),
    "alchemy_family": ("Bai", "Jadewood Medicine City"),
}


def test_the_homeland_table_is_eleven_archetypes_by_four_worlds() -> None:
    assert len(FAMILY_HOMELANDS) == 11
    for archetype, profile in FAMILY_HOMELANDS.items():
        for world in WORLDS:
            assert profile[world], f"{archetype} has no {world} city"
    assert len(HOMELAND_CITIES) == 44, "two archetypes share a city"


def test_every_family_homeland_is_explorable() -> None:
    locations = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))["locations"]
    for city in sorted(HOMELAND_CITIES):
        assert city in locations, city
        assert locations[city]["encounters"], city
        assert locations[city]["private"] is False, city


def test_every_family_homeland_has_a_local_steward() -> None:
    world = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
    steward_locations = {
        npc["location"] for name, npc in world["npcs"].items() if name.endswith(" Family Steward")
    }
    assert HOMELAND_CITIES <= steward_locations
