"""The dashboard's stand-in world carries recipes (v1.2.1).

`validate_quest_definition` reads `world.recipes` to resolve a `craft`
objective, and `_QuestWorld` handed it locations, npcs and items - so every
craft objective the Quest Editor saved or the coverage report checked was "an
unknown recipe", whatever was typed.
"""

from __future__ import annotations

import unittest

from tests.support import install_aiosqlite_shim

install_aiosqlite_shim()

from app.dashboard.server import _QuestWorld  # noqa: E402
from app.rules.quests import validate_quest_definition  # noqa: E402

DRAFT = {
    "title": "Forge Something",
    "description": "Make a Recovery Pill for the hall.",
    "objectives": [{"type": "craft", "target": "Recovery Pill", "count": 1}],
    "rewards": {"insight_xp": 5},
}
BUDGET = {"max_xp": 100, "max_stones": 100, "max_items": 5}


class TheQuestEditorKnowsRecipes(unittest.TestCase):
    def test_a_craft_objective_resolves_against_the_recipes_it_names(self):
        world = _QuestWorld({"locations": {}, "npcs": {}, "items": {}, "recipes": {"Recovery Pill": {"profession": "Alchemy"}}})
        definition, errors = validate_quest_definition(DRAFT, world, BUDGET)
        self.assertEqual(errors, [], "a recipe the world carries was refused")
        self.assertIsNotNone(definition)

    def test_a_recipe_the_world_lacks_is_still_refused(self):
        world = _QuestWorld({"locations": {}, "npcs": {}, "items": {}, "recipes": {}})
        _, errors = validate_quest_definition(DRAFT, world, BUDGET)
        self.assertTrue(errors, "a recipe nothing carries was accepted")


if __name__ == "__main__":
    unittest.main()
