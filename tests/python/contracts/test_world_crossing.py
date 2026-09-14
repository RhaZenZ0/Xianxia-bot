"""v1.0.0-rc.15: crossing between the worlds.

The hardest journey in the genre had three doors and two of them were shut.
Ascension (飞升) cleared its tribulation, passed its gate, wrote its history
row - and left the cultivator standing exactly where they had been. The
teleportation arrays charged the destination world's currency, which no
reward path grants and no exchange converts, so they could only be paid by
someone who had already arrived; and they ran one way only. Go owns the
crossing; this side asserts the Python boundary and the content.
"""
from __future__ import annotations

import ast
import json
import unittest

from tests.support import PROJECT_ROOT

BOT = PROJECT_ROOT / "app" / "bot"
CULTIVATION = (BOT / "commands" / "cultivation.py").read_text(encoding="utf-8")
ABODE = (BOT / "commands" / "abode.py").read_text(encoding="utf-8")
LOCATIONS = (BOT / "locations.py").read_text(encoding="utf-8")
GO = PROJECT_ROOT / "go_core" / "internal"
WORLD = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))


def _body(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef, ast.ClassDef)) and n.name == name)
    return ast.get_source_segment(source, node)


class TheEngineCarriesTheAscendant(unittest.TestCase):
    def test_the_crossing_sets_them_down_in_the_new_world(self):
        cultivation = (GO / "game" / "cultivation_actions.go").read_text(encoding="utf-8")
        for needle in (
            "func ascendToNewWorld(",
            "arrived, err := ascendToNewWorld(conn, catalog, userID, newWorld, p.GameMinute, now)",
            'result["ascended_to_location"] = arrived',
            # The gate itself is untouched: an uncleared tribulation still refuses.
            "world-crossing tribulation must be cleared before this breakthrough",
        ):
            self.assertIn(needle, cultivation, needle)
        self.assertIn("func realmHubOf(", (GO / "game" / "exploration_actions.go").read_text(encoding="utf-8"))

    def test_the_reply_says_where_they_came_down(self):
        self.assertIn('result.get("ascended_to_location")', CULTIVATION)
        self.assertIn("The heavens take you.", CULTIVATION)


class EitherLadderOpensAWorld(unittest.TestCase):
    """The world-crossing tribulation is gated on qi *or* body, so a body
    cultivator who crosses must not find the world shut against them."""

    def test_the_engine_measures_a_place_against_both_ladders(self):
        aptitude = (GO / "game" / "aptitude_actions.go").read_text(encoding="utf-8")
        self.assertIn("func (c mechanicsCharacter) accessRealmIndex() int64 {", aptitude)
        exploration = (GO / "game" / "exploration_actions.go").read_text(encoding="utf-8")
        for needle in (
            "if c.accessRealmIndex() < dest.MinRealmIndex {",
            "if c.accessRealmIndex() < worldMinRealm(catalog, dest.World) {",
            "if loc.RealmHub && c.accessRealmIndex() >= worldMinRealm(catalog, loc.World) {",
        ):
            self.assertIn(needle, exploration, needle)

    def test_python_mirrors_the_same_rule(self):
        self.assertIn("def access_realm_index(character: dict[str, Any]) -> int:", LOCATIONS)
        rule = _body(LOCATIONS, "access_realm_index")
        self.assertIn('character.get("body_realm_index", 0)', rule)
        self.assertIn("max(", rule)
        unlocked = _body(LOCATIONS, "_world_is_unlocked")
        self.assertIn("access_realm_index(character)", unlocked)


class TheArraysAreUsable(unittest.TestCase):
    def test_the_picker_and_the_listing_respect_the_realm_seal(self):
        listing = _body(ABODE, "array_list")
        self.assertIn("min_realm_index", listing)
        self.assertIn("sealed", listing)
        picker = _body(ABODE, "array_destination_autocomplete")
        self.assertIn("min_realm_index", picker)

    def test_the_arrival_names_the_destination(self):
        # The reply read `location`/`destination`, which the engine never
        # returns, so it always said "your destination".
        use = _body(ABODE, "array_use")
        self.assertIn("r.get('to')", use)
        self.assertNotIn("r.get('location'", use)
        array_go = (GO / "game" / "property_storage_actions.go").read_text(encoding="utf-8")
        self.assertIn('"to": d.To', array_go)


if __name__ == "__main__":
    unittest.main()
