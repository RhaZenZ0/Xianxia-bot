"""A city's gate is that city (v1.0.9).

**Found by playing**, and the report is the whole finding:

    Could not enter the household: the Shen Family household stands in
    Cloudblade City and you are in Cloudblade City East Gate — travel there
    first, or use a Hearth-Return Talisman

A refusal naming, as somewhere else, the city the player was standing in.

`familyHouseholdEnterAction` compared the character's location to the
household's town by **bare string equality**, while `cityOf` - the engine's one
statement of which city a place is part of - had been read by
`explorationTravelAction` and `WhereAnNPCCanWalk` all along. This door asked
nobody. **317 of the catalogue's 477 locations are parts of a household town**,
92 of them gates, so that is how much of the world refused it.

It fell hardest on exactly the player least able to work around it:
`beginner_home` ("The Road Home") is the beginner path's fourth stage, its
`return_home` objective is reported by this very action, and **walking home
from the road arrives at a gate**.

Both halves are held here, because the fault was one rule with two spellings
and the fix is one rule asked twice. `_household_hidden_actions` *anticipates*
the engine's refusal to decide whether the panel draws the door - which v1.0.6
states is allowed, as long as presentation is never the only place the rule
lives - so it has to reach the same answer the engine reaches, over the whole
catalogue, or the panel hides a door that would have opened.
"""
from __future__ import annotations

import json
import re
import unittest

from tests.support import PROJECT_ROOT

CONTENT = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
LOCATIONS: dict[str, dict] = CONTENT["locations"]
GAME = PROJECT_ROOT / "go_core" / "internal" / "game"
HOUSEHOLD_GO = GAME / "family_household_actions.go"
BIRTH_GO = GAME / "birth_family_actions.go"
SURFACE = PROJECT_ROOT / "app" / "bot" / "surface.py"


def _household_towns() -> set[str]:
    """The 44 towns the thirteen archetypes live in, read off the Go map."""
    source = BIRTH_GO.read_text(encoding="utf-8")
    start = source.index("var birthFamilyHomelands")
    block = source[start : source.index("\n}\n\n", start)]
    return set(re.findall(r'"(?:Mortal|Spiritual|Immortal|Celestial) World":\s*"([^"]+)"', block))


def _city_of_by_content(location: str) -> str:
    """`cityOf`'s rule, applied to the raw content file.

    A third statement, deliberately, and only inside this gate: it is what lets
    the test say the engine and the bot agree *with the content*, rather than
    merely with each other - two wrong halves would otherwise pass.
    """
    entry = LOCATIONS.get(location) or {}
    outside = str(entry.get("outside_location") or "").strip()
    if outside and (entry.get("district") or entry.get("shop") or entry.get("auction_house")):
        return outside
    return location


class TheDoorAsksWhichCityYouAreIn(unittest.TestCase):
    def test_the_catalogue_really_has_parts_of_household_towns(self):
        """Asserted before it is trusted (rc.57). A catalogue with no city
        parts would make every assertion below vacuous."""
        towns = _household_towns()
        self.assertGreaterEqual(len(towns), 40, "the homeland map could not be read off the Go source")
        parts = [n for n in LOCATIONS if _city_of_by_content(n) in towns and n not in towns]
        self.assertGreater(
            len(parts), 200,
            f"only {len(parts)} locations are parts of a household town; the reader is broken, "
            "not the tree",
        )

    def test_the_engine_resolves_the_city_rather_than_comparing_strings(self):
        source = HOUSEHOLD_GO.read_text(encoding="utf-8")
        self.assertIn(
            "cityOf(catalog, here) != town", source,
            "familyHouseholdEnterAction compares the character's location to the household's town "
            "by bare string equality again. Standing at your own city's gate then refuses you, "
            "naming the city you are standing in as somewhere else — and the beginner path's "
            "fourth stage reports return_home from this action.",
        )

    def test_the_panel_asks_the_same_question(self):
        source = SURFACE.read_text(encoding="utf-8")
        self.assertIn(
            "_city_of(here) != town", source,
            "_household_hidden_actions is back to bare equality, so the panel hides the door home "
            "in the 317 places the engine would now open it",
        )

    def test_the_two_halves_agree_over_the_whole_catalogue(self):
        """The fault was one rule with two spellings; this is what stops it
        being one rule with two spellings again."""
        import os
        from unittest.mock import patch

        env = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
               "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890",
               "DATABASE_PATH": "data/test.sqlite3", "HEALTH_PORT": "18099"}
        with patch.dict(os.environ, env):
            import importlib

            surface = importlib.import_module("app.bot.surface")
        disagreed = [
            name for name in LOCATIONS
            if surface._city_of(name) != _city_of_by_content(name)
        ]
        self.assertEqual(
            disagreed[:10], [],
            f"{len(disagreed)} locations resolve to a different city in the bot than the content "
            "says, so the panel and the engine disagree about where the player is standing",
        )

    def test_a_gate_of_a_household_town_resolves_to_that_town(self):
        """The reported case, and every one like it."""
        towns = _household_towns()
        gates = [
            name for name, entry in LOCATIONS.items()
            if entry.get("district") == "gate" and str(entry.get("outside_location") or "") in towns
        ]
        self.assertGreater(len(gates), 50, "no gates of household towns; the reader is broken")
        self.assertIn("Cloudblade City East Gate", LOCATIONS)
        self.assertEqual(_city_of_by_content("Cloudblade City East Gate"), "Cloudblade City")
        for gate in gates:
            self.assertEqual(
                _city_of_by_content(gate), LOCATIONS[gate]["outside_location"],
                f"{gate!r} does not resolve to its own city",
            )


if __name__ == "__main__":
    unittest.main()
