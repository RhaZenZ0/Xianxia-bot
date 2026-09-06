import json

from tests.support import PROJECT_ROOT

from app.rules.birthfamily import FAMILY_HOMELANDS, generate_samsara_family

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


def test_mortal_samsara_uses_starting_family_homelands() -> None:
    for karma in (-1000, -250, 0, 250, 1000):
        for _ in range(100):
            family = generate_samsara_family("Mortal World", karma)
            surname, location = EXPECTED_STARTERS[family["id"]]
            assert family["surname"] == surname
            assert family["location"] == location
            assert family["location"] != "Greenriver Town"


def test_upper_samsara_uses_realm_local_families_and_lineage() -> None:
    mortal_ids = set(EXPECTED_STARTERS)
    deprecated_locations = {
        "Greenriver Town",
        "Spirit Jade Rebirth Enclave",
        "Nine-Heavens Rebirth Terrace",
        "Celestial Cradle Province",
    }

    for world in ("Spiritual World", "Immortal World", "Celestial World"):
        for karma in (-1000, 0, 1000):
            for _ in range(50):
                family = generate_samsara_family(
                    world,
                    karma,
                    previous_family_name="Qin Family",
                    previous_archetype="noble_martial_clan",
                )
                assert family["id"] not in mortal_ids
                assert family["rebirth_world"] == world
                assert family["location"] not in deprecated_locations
                assert family["family_name"] != "Qin Family"
                assert family["lineage_status"] in {
                    "distant_surviving_branch",
                    "fallen_severed_branch",
                    "extinct_branch_replaced",
                    "no_known_connection",
                }
                assert family["lineage_summary"]


def test_upper_samsara_can_describe_extinction_and_replacement(monkeypatch) -> None:
    import app.rules.birthfamily as birthfamily

    rolls = iter((0, 50))
    monkeypatch.setattr(birthfamily.secrets, "randbelow", lambda limit: next(rolls) % limit)
    status, summary = birthfamily._samsara_lineage(
        "Luo Clan",
        "fallen_martial_clan",
        "Immortal World",
        "Cang Tide-Listening House",
        0,
    )
    assert status == "distant_surviving_branch"

    status, summary = birthfamily._samsara_lineage(
        "Luo Clan",
        "fallen_martial_clan",
        "Immortal World",
        "Cang Tide-Listening House",
        0,
    )
    assert status == "extinct_branch_replaced"
    assert "no blood continuity" in summary


def test_lower_world_nobility_does_not_guarantee_upper_world_rank(monkeypatch) -> None:
    import app.rules.birthfamily as birthfamily

    monkeypatch.setattr(birthfamily.secrets, "randbelow", lambda limit: 0)
    status, summary = birthfamily._samsara_lineage(
        "Qin Clan",
        "noble_martial_clan",
        "Immortal World",
        "Lu Tide-Listening House",
        0,
    )
    assert status == "distant_surviving_branch"
    assert "never inherited its lower-world rank" in summary
    assert "not a royal continuation" in summary


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
