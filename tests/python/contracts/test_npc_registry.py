"""People this world made for itself reach the surfaces (v1.0.0-rc.27, schema 49).

Go decides who exists; this asserts the Python boundary - that the three
places a person can live are all resolved, that the narrator stopped crashing
on the two it did not know about, and that `/sense` stopped refusing what its
own picker offers.
"""

from __future__ import annotations

import ast
import unittest

from app.database import SCHEMA_VERSION
from tests.support import PROJECT_ROOT

APP = PROJECT_ROOT / "app"
GO = PROJECT_ROOT / "go_core" / "internal"
CORE = (APP / "database" / "core.py").read_text(encoding="utf-8")
NARRATOR = (APP / "ai" / "narrator.py").read_text(encoding="utf-8")
LOCATIONS = (APP / "bot" / "locations.py").read_text(encoding="utf-8")
SENSE = (APP / "bot" / "commands" / "sense.py").read_text(encoding="utf-8")
SERVICES = (APP / "bot" / "services.py").read_text(encoding="utf-8")


def body(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == name
    )
    return ast.get_source_segment(source, node) or ""


class TheSchemaIsThere(unittest.TestCase):
    def test_the_registry_arrived_with_its_own_migration(self):
        self.assertGreaterEqual(SCHEMA_VERSION, 49)
        self.assertIn("npcs_the_world_made_itself", CORE)
        self.assertIn("CREATE TABLE IF NOT EXISTS npc_registry", CORE)

    def test_it_is_not_the_catalogue_mirror(self):
        """The safety property. A rebuild from `content/world.json` is an
        unconditional DELETE over the derived tables; the registry must never
        be named in one, or a wrong predicate wipes the world's own people on
        every boot, forever."""
        sync = body(CORE, "sync_world_catalog")
        self.assertNotIn("npc_registry", sync)


class AllThreeKindsOfPersonResolve(unittest.TestCase):
    def test_the_resolver_tries_catalogue_then_registry_then_event_cast(self):
        resolver = body(CORE, "get_npc_definition")
        catalogue = resolver.index("catalog_npcs")
        registry = resolver.index("get_registered_npc")
        event = resolver.index("get_event_npc_definition")
        self.assertLess(catalogue, registry, "the registry must not shadow the content file")
        self.assertLess(registry, event)

    def test_the_registry_lookup_is_uncached(self):
        """Unlike the catalogue mirror. These rows are written while the game
        runs - a descendant comes of age inside a simulation tick - so a cache
        would hide a person for as long as it lived."""
        self.assertNotIn("_catalog_get", body(CORE, "get_registered_npc"))

    def test_origin_never_reaches_a_narrator(self):
        """How somebody came to exist is the GM's business. It must not be
        handed to a model as though it were a trait."""
        self.assertIn('person.pop("origin", None)', body(CORE, "get_registered_npc"))


class TheNarratorStoppedCrashing(unittest.TestCase):
    def test_no_bare_subscript_into_the_content_file_is_left(self):
        """`self.world.npcs[npc_name]` plus six bare field subscripts. The gate
        upstream resolves through `DB.get_npc_definition`, which has fallen
        back to a running event's cast since schema 43 - so a militia captain
        passed the gate and raised KeyError, and `/talk`'s `except Exception`
        turned it into "the narrator service failed to answer."."""
        # Read off the syntax tree, not by searching the text: the comment
        # above the replacement quotes the line it replaced, and a substring
        # search cannot tell a subscript from prose describing one.
        subscripts = [
            node for node in ast.walk(ast.parse(NARRATOR))
            if isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute) and node.value.attr == "npcs"
        ]
        self.assertEqual(
            subscripts, [],
            "a bare subscript into the parsed content file is back; "
            "anybody it does not carry raises KeyError inside a narration call",
        )
        for field in ("role", "realm", "personality", "speech", "want", "fear"):
            with self.subTest(field=field):
                self.assertNotIn(f"npc['{field}']", NARRATOR)
                self.assertIn(f"npc.get('{field}')", NARRATOR)

    def test_the_resolver_is_injected_rather_than_imported(self):
        """`test_app_layout` puts `ai` below `database`, so this module may not
        reach a repository. NarratorContextBuilder already solves it this way."""
        self.assertIn("npc_resolver", body(NARRATOR, "__init__"))
        self.assertNotIn("from ..database", NARRATOR)
        self.assertIn("npc_resolver=DB", SERVICES)

    def test_it_falls_back_to_the_content_file_first(self):
        profile = body(NARRATOR, "_npc_profile")
        self.assertLess(profile.index("world"), profile.index("npc_resolver"))
        # And never raises: a narration failure must not be how a player finds
        # out about an unknown NPC.
        self.assertIn("except Exception", profile)


class TheSurfacesOfferThem(unittest.TestCase):
    def test_the_talk_picker_lists_the_registry(self):
        picker = body(LOCATIONS, "local_npc_autocomplete")
        self.assertIn("list_registered_npcs_at", picker)

    def test_a_relative_is_only_addressable_where_they_stand(self):
        """They have no simulation row, so `current_npc_location` used to
        answer None - which every caller reads as "do not filter by location",
        making somebody else's uncle talkable from across the world."""
        where = body(LOCATIONS, "current_npc_location")
        self.assertIn("get_registered_npc", where)
        self.assertLess(where.index("SIM.npc_status"), where.index("get_registered_npc"),
                        "the simulation is authoritative about anybody who has a row there")

    def test_sense_uses_the_same_resolver_as_talk(self):
        """It refused with "Unknown NPC." anybody not in the parsed content
        file, while its own picker had been offering event cast since schema
        43 - an NPC you could talk to but not sense."""
        self.assertNotIn("if npc not in WORLD.npcs:", SENSE)
        self.assertIn("await DB.get_npc_definition(npc) is None", SENSE)


class GoOwnsWhoExists(unittest.TestCase):
    def test_nothing_python_side_writes_the_registry(self):
        for path in ("app/database/core.py", "app/bot/locations.py", "app/ai/narrator.py"):
            source = (PROJECT_ROOT / path).read_text(encoding="utf-8")
            with self.subTest(path=path):
                self.assertNotIn("INSERT INTO npc_registry", source)

    def test_the_three_doors_into_a_household_all_register_its_relatives(self):
        """`grantBirthFamilySendoffTx` is the one helper creation, the
        dao-family path and a samsara return all call, and the relatives are
        registered there - ahead of its early returns, because a household
        with no heirloom still has a family in it."""
        birth = (GO / "game" / "birth_family_actions.go").read_text(encoding="utf-8")
        grant = birth[birth.index("func grantBirthFamilySendoffTx("):]
        grant = grant[:grant.index("\nfunc ")]
        self.assertIn("registerHouseholdRelativesTx(conn, catalog, familyID, gameMinute)", grant)
        self.assertLess(grant.index("registerHouseholdRelativesTx"), grant.index("sendoff, ok :="))

    def test_growing_up_is_a_step_of_the_life_tick(self):
        world = (GO / "simulation" / "world.go").read_text(encoding="utf-8")
        self.assertIn("r.npcMaturation(conn, gm)", world)
        self.assertIn("came of age", world)


if __name__ == "__main__":
    unittest.main()
