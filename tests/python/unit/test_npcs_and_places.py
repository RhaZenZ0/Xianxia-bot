"""NPCs, places and the things players built.

Merged from:

test_an_explore_finds_the_missing.py — An ordinary explore finds the missing (v1.22.1).

The engine searches the ground around an explore - the explorer's whole city and the road
sites and wilds around it, never the next city - and reports who it found
(`found_npcs`) and which graves it reached (`found_graves`); the Go tests hold
that half (`explore_search_test.go`). This holds the reply: it prints both,
and it prints them where the discovery block cannot overwrite them - that
block assigns `discovery_text` rather than appending to it.


test_npc_memory.py — (no module docstring)

test_the_narrator_asks_the_registry.py — The narrator's public profile of an NPC asks the registry (v1.2.1).

`DB.get_npc_definition` resolves catalogue -> registry -> a running event's
cast (rc.27), and `NarratorContextBuilder._public_npc` resolved catalogue ->
cast, so a matured descendant, a household relative or a GM's NPC reached the
narrator with no personality, speech, want or fear.


test_location_discovery_images.py — (no module docstring)

test_world_access_scene_action.py — (no module docstring)

test_samsara_family_homeland.py — (no module docstring)

test_built_things_do_something.py — Things a player built that did nothing now do something (v1.28.0).

A property's Storage facility stored nothing, a beast's intelligence fought
with nothing, six reputations were read by no rule. The engine holds each rule;
where the bot shows the same number, its twin is held equal to the Go here,
read off the source rather than copied.

"""
from __future__ import annotations

import ast
import asyncio
import importlib
import json
import os
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.support import PROJECT_ROOT, bot_package_source, install_aiosqlite_shim

install_aiosqlite_shim()

from app.ai.narrator_context import NarratorContextBuilder  # noqa: E402
from app.database.core import PROPERTY_STORAGE_SLOTS_PER_LEVEL  # noqa: E402
from app.rules.advanced_runtime import BEAST_INTELLIGENCE_CAP, companion_bonus  # noqa: E402
from app.rules.birthfamily import FAMILY_HOMELANDS  # noqa: E402
from app.rules.npc_memory import classify_memory, exchange_memory_summary, format_memories, scene_memory_summary  # noqa: E402

pytestmark = pytest.mark.unit


# --- from test_an_explore_finds_the_missing.py ---

ENV = {
    "DISCORD_TOKEN": "test-token",
    "GUILD_ID": "123456789012345678",
    "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890",
    "DATABASE_PATH": "data/test.sqlite3",
}
SOURCE = PROJECT_ROOT / "app" / "bot" / "commands" / "exploration.py"


class TheReplySaysWhoWasFound(unittest.TestCase):
    def _lines(self, outcome: dict) -> str:
        with patch.dict(os.environ, ENV):
            return importlib.import_module("app.bot.commands.exploration").search_lines(outcome)

    def test_a_found_person_is_named(self):
        text = self._lines({"found_npcs": [
            {"npc_name": "Lost Herbalist Mei", "days_missing": 12, "home_location": "Moonfen City",
             "location": "Shrine of the Patient Ox"}]})
        self.assertIn("Lost Herbalist Mei", text)
        self.assertIn("Shrine of the Patient Ox", text, "a search reaches beyond where you stand, so say where")
        self.assertIn("12", text)
        self.assertIn("Moonfen City", text)

    def test_a_grave_names_what_was_carried(self):
        text = self._lines({"found_graves": [
            {"npc_name": "Buried Lu", "days_missing": 64, "home_location": "Moonfen City",
             "location": "Sunken Bell Ruin", "keepsake_item": "spirit_herb", "keepsake_stones": 7}]})
        self.assertIn("Buried Lu", text)
        self.assertIn("Sunken Bell Ruin", text)
        self.assertIn("7 spirit stones", text)

    def test_nothing_found_says_nothing(self):
        self.assertEqual(self._lines({}), "")
        self.assertEqual(self._lines({"found_npcs": None, "found_graves": None}), "")


class TheLinesAreNotOverwritten(unittest.TestCase):
    def test_the_lines_are_added_after_the_last_assignment(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        explore = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "explore")
        assigned, added = [], []
        for node in ast.walk(explore):
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "discovery_text" for t in node.targets):
                assigned.append(node.lineno)
            if (isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name)
                    and node.target.id == "discovery_text" and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name) and node.value.func.id == "search_lines"):
                added.append(node.lineno)
        self.assertTrue(assigned, "found no assignment to discovery_text; the reader is broken, not the tree")
        self.assertEqual(len(added), 1, "the explore reply does not print what the search found")
        self.assertGreater(added[0], max(assigned), (
            "the search's lines are added before a later `discovery_text = ...`, which drops them "
            "whenever the explore also charts a route"))


# --- from test_npc_memory.py ---

class NPCMemoryTests(unittest.TestCase):
    def test_vows_receive_higher_salience_than_small_talk(self):
        small_kind, small = classify_memory("Good morning.", "Morning.")
        vow_kind, vow = classify_memory("I swear I will return the jade token.", "Then I will remember your word.")
        self.assertEqual(small_kind, "conversation")
        self.assertEqual(vow_kind, "vow")
        self.assertGreater(vow, small)

    def test_memory_summaries_are_grounded_in_actual_exchange(self):
        summary = exchange_memory_summary("Where is the pass?", "Elder Su", "North of the river.")
        self.assertIn("Where is the pass?", summary)
        self.assertIn("North of the river.", summary)
        scene = scene_memory_summary("I bow to Elder Su.", "Elder Su", "Elder Su returns the bow.")
        self.assertIn("I bow to Elder Su.", scene)
        self.assertIn("Elder Su returns the bow.", scene)

    def test_format_memories_keeps_kind_and_salience(self):
        text = format_memories([
            {"memory_kind": "debt", "salience": 75, "summary": "The player repaid an old favor."}
        ])
        self.assertIn("debt", text)
        self.assertIn("75/100", text)
        self.assertIn("repaid an old favor", text)


# --- from test_the_narrator_asks_the_registry.py ---

class FakeDB:
    def __init__(self, registered=None, cast=None):
        self.registered = registered
        self.cast = cast
        self.asked = []

    async def get_registered_npc(self, name):
        self.asked.append(("registry", name))
        return self.registered

    async def get_event_npc_definition(self, name):
        self.asked.append(("cast", name))
        return self.cast


def builder(db):
    return NarratorContextBuilder(db=db, simulator=SimpleNamespace(), world=SimpleNamespace(npcs={"Yue Dong": {"personality": "stern"}}), engine=SimpleNamespace(), max_chars=7000)


class TheNarratorAsksTheRegistry(unittest.TestCase):
    def test_the_catalogue_still_answers_first(self):
        db = FakeDB(registered={"personality": "wrong"})
        self.assertEqual(asyncio.run(builder(db)._public_npc("Yue Dong")), {"personality": "stern"})
        self.assertEqual(db.asked, [])

    def test_a_registered_npc_reaches_the_narrator_with_their_traits(self):
        db = FakeDB(registered={"name": "Shen Wei", "personality": "wry", "speech": "clipped", "want": "peace", "fear": "debt"})
        self.assertEqual(asyncio.run(builder(db)._public_npc("Shen Wei"))["personality"], "wry")
        self.assertEqual(db.asked, [("registry", "Shen Wei")], "the registry was not the second door")

    def test_the_cast_is_still_the_last_door(self):
        db = FakeDB(registered=None, cast={"name": "Captain Ren", "role": "militia"})
        self.assertEqual(asyncio.run(builder(db)._public_npc("Captain Ren"))["role"], "militia")
        self.assertEqual([kind for kind, _ in db.asked], ["registry", "cast"])


# --- from test_location_discovery_images.py ---

ROOT = Path(__file__).resolve().parents[3]
CAPITAL_ART = ROOT / "assets" / "locations" / "azure_crown_imperial_city.png"


def test_mortal_capital_discovery_art_is_packaged() -> None:
    assert CAPITAL_ART.is_file()
    assert CAPITAL_ART.stat().st_size > 100_000


def test_mortal_capital_art_is_bound_to_azure_crown() -> None:
    source = bot_package_source()
    assert '"Azure Crown Imperial City": ROOT / "assets" / "locations" / "azure_crown_imperial_city.png"' in source
    assert 'title=f"🏙️ First Sight — {location}"' in source


def test_first_discovery_delivery_covers_creation_exploration_and_travel() -> None:
    source = bot_package_source()

    # Birthplace does not gate the feature. Starting in any illustrated location
    # counts as that character's first discovery, while other birthplaces reach
    # the same image through exploration/travel below.
    assert 'if location in LOCATION_DISCOVERY_IMAGES:' in source
    assert 'if location == "Azure Crown Imperial City":' not in source
    assert 'discovery_art = location_discovery_image_path(location)' in source
    assert 'file=discord.File(discovery_art, filename=filename)' in source

    # Exploration discovery is authoritative and only returns a newly inserted location.
    assert 'if discovered_location:' in source
    assert 'interaction, discovered_location, thread=expedition_thread' in source

    # Direct/hub/road travel checks canonical discovery state before travel so art is not repeated.
    assert 'previously_discovered = await DB.has_discovered_location' in source
    assert 'undiscovered_image_locations = {' in source
    assert 'def travel_first_discovers_location(' in source
    assert 'target in route' in source


# --- from test_world_access_scene_action.py ---

class WorldAccessAndSceneActionTests(unittest.TestCase):
    """Source scans of the /action command and the visibility guards. (Moved
    from integration/ in v0.20.3: the seeded database no test read is gone.)"""

    def test_scene_action_replaces_freeform_act(self):
        source = bot_package_source()
        self.assertIn('@registered_root_command(name="action", description="Open the guided Scene Action panel"', source)
        self.assertNotIn('@registered_root_command(name="act"', source)
        for key in ("observe", "investigate", "influence", "stealth", "physical", "qi", "resolve", "aid"):
            self.assertIn(f'"{key}":', source)
        self.assertIn("fixed_roll=fixed", source)
        self.assertIn("structured_scene_action", source)
        self.assertIn('name="🎲 Result"', source)
        self.assertIn('name="🧠 Attribute"', source)
        self.assertIn('name="🎯 Target"', source)
        self.assertIn('name="📝 Attempt"', source)
        self.assertIn("await interaction.followup.send(view=card_view(embed), ephemeral=False)", source)
        self.assertIn('\"scene.action\"', source)
        self.assertIn('self_action = bool(mechanics.get(\"automatic\", False))', source)
        self.assertIn("Outcome: Automatic success — self-directed action.", source)
        self.assertIn("Hidden canonical information remains concealed.", source)
        self.assertIn("This does not reveal hidden canonical information.", source)

    def test_visibility_guards_cover_world_npcs_and_realm_hubs(self):
        source = bot_package_source()
        self.assertIn("_known_locations", source)
        self.assertIn("_location_is_visible", source)
        self.assertIn("_world_is_unlocked", source)
        self.assertIn("realmhub_world_autocomplete", source)
        self.assertIn("You have no reliable knowledge of that cultivator yet", source)
        self.assertIn("Explore known regions to discover additional routes", source)
        self.assertIn("Higher worlds remain beyond perception", source)


# --- from test_samsara_family_homeland.py ---

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


# --- from test_built_things_do_something.py ---

GAME = Path(__file__).resolve().parents[3] / "go_core" / "internal" / "game"


def _go_const(file: str, name: str) -> int:
    text = (GAME / file).read_text(encoding="utf-8")
    match = re.search(rf"\b{name}\s*=\s*int64\((\d+)\)", text)
    assert match, f"{name} is not declared in {file}; the gate is broken, not the tree"
    return int(match.group(1))


class BuiltThingsDoSomething(unittest.TestCase):
    def test_the_storage_facility_adds_what_the_engine_adds(self) -> None:
        self.assertEqual(PROPERTY_STORAGE_SLOTS_PER_LEVEL, _go_const("property_storage_actions.go", "propertyStorageSlotsPerLevel"))

    def test_a_beasts_training_is_capped_where_the_engine_caps_it(self) -> None:
        self.assertEqual(BEAST_INTELLIGENCE_CAP, _go_const("combat_actions.go", "beastIntelligenceCap"))
        self.assertGreater(companion_bonus(0, 0, 0, 100), companion_bonus(0, 0, 0, 0))

    def test_the_beast_card_passes_the_intelligence(self) -> None:
        import ast
        source = (Path(__file__).resolve().parents[3] / "app" / "bot" / "commands" / "beast.py").read_text(encoding="utf-8")
        calls = [n for n in ast.walk(ast.parse(source))
                 if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "companion_bonus"]
        self.assertTrue(calls, "the beast card no longer asks companion_bonus")
        for call in calls:
            self.assertEqual(len(call.args), 4, f"a beast card leaves out what training is worth: {ast.unparse(call)}")


if __name__ == "__main__":
    unittest.main()
