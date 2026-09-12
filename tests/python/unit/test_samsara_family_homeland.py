import json

from tests.support import PROJECT_ROOT

from app.rules.birthfamily import FAMILY_HOMELANDS

WORLDS = ("Mortal World", "Spiritual World", "Immortal World", "Celestial World")
# Every city a birth family can call home, straight from the rules table.
# test_family_homeland_playability.py used to hand-copy the 44 names; merged
# here in v0.20.3. Eleven households founded a city each, in four worlds; the
# two ghost households (v1.0.0-rc.8) founded none and live in a cousin's, so
# thirteen archetypes still come to forty-four cities.
HOMELAND_CITIES = {profile[world] for profile in FAMILY_HOMELANDS.values() for world in WORLDS}
GHOST_HOUSEHOLDS = {"nether_market_house": "hidden_weapon_family", "tomb_watch_clan": "fallen_martial_clan"}


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


def test_the_homeland_table_is_thirteen_archetypes_by_four_worlds() -> None:
    assert len(FAMILY_HOMELANDS) == 13
    for archetype, profile in FAMILY_HOMELANDS.items():
        for world in WORLDS:
            assert profile[world], f"{archetype} has no {world} city"
    assert len(HOMELAND_CITIES) == 44, "a founding household shares its city"
    # The one permitted sharing, and only with the named cousin.
    for ghost, host in GHOST_HOUSEHOLDS.items():
        for world in WORLDS:
            assert FAMILY_HOMELANDS[ghost][world] == FAMILY_HOMELANDS[host][world], (ghost, world)
    founding = [a for a in FAMILY_HOMELANDS if a not in GHOST_HOUSEHOLDS]
    for world in WORLDS:
        cities = [FAMILY_HOMELANDS[a][world] for a in founding]
        assert len(set(cities)) == len(cities) == 11, (world, cities)


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
