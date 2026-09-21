"""A method you know must be able to tell you what it needs.

Found by playing, not by reading (v1.0.1): a player bought an Inscription slip,
read it, and then had no way to discover that a Swift-Wind Talisman wants one
`talisman_paper` and one `spirit_ink`. All three facts existed and none reached
them.

1. `character_recipes` had four writers - a bought slip, the household lesson,
   the trade examination and a grandfathering migration - and **no reader in
   Python at all**, so nothing could say what you had learned.
2. `get_recipe_definition` has parsed a recipe's `cost` out of `cost_json`
   since the content tables landed, and **no command read it**.
3. `consumeInventoryTx` computes the exact shortfall per item, and the craft
   threw it away to refuse with the bare words "missing materials" - which the
   bot then replaced with a sentence of its own.

That is the shape `/learn` (rc.43), the quest journal (rc.46) and the peach
(rc.50) all had, and it is what `test_commands_reach_a_player.py` holds for
commands. This holds it for the one thing a crafter has to know.

Every check here is behavioural where it can be, because "does this file
mention `cost`" is exactly the kind of question rc.58 showed a name cannot
answer.
"""
from __future__ import annotations

import ast
import json
import unittest

from tests.support import PROJECT_ROOT

CRAFTING_GO = (PROJECT_ROOT / "go_core" / "internal" / "game" / "crafting_actions.go").read_text(encoding="utf-8")
STATUS_PY = (PROJECT_ROOT / "app" / "bot" / "commands" / "law.py").read_text(encoding="utf-8")
CRAFT_PY = (PROJECT_ROOT / "app" / "bot" / "commands" / "exploration.py").read_text(encoding="utf-8")
CORE_PY = (PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")
WORLD = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))


def function_source(module_text: str, name: str) -> str:
    """The source of one function, by AST rather than by indentation.

    rc.59's lesson: a multi-line `def` defeats an indentation slice, because
    the closing `) -> None:` sits at the function's own indent and every block
    then ends one line in - which made a whole gate pass vacuously.
    """
    tree = ast.parse(module_text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(module_text, node) or ""
    raise AssertionError(f"{name} is not a function any more; the reader is broken, not the tree")


class ARecipeTellsYouWhatItNeeds(unittest.TestCase):
    def test_the_reader_finds_the_functions_it_is_about(self):
        # Asserted before it is trusted (rc.57): a reader that silently finds
        # nothing makes every assertion after it vacuous.
        self.assertIn("get_known_recipes", function_source(CORE_PY, "get_known_recipes"))
        self.assertIn("rows", function_source(STATUS_PY, "profession_status"))
        self.assertIn("craft.resolve", function_source(CRAFT_PY, "_run_crafting"))

    def test_the_engine_names_what_is_short(self):
        """The shortfall reaches the refusal instead of being discarded."""
        self.assertNotIn(
            'errors.New("missing materials")', CRAFTING_GO,
            "the craft refuses with the bare words again; consumeInventoryTx already knows which "
            "materials are short and by how much",
        )
        self.assertIn("describeMaterials(catalog, missing)", CRAFTING_GO)
        # Sorted, because a map range would make one refusal read two ways.
        self.assertIn("sort.Strings(ids)", CRAFTING_GO)
        # And through the one statement of what an item is called.
        self.assertIn("itemDisplayName(catalog, id)", CRAFTING_GO)

    def test_the_bot_does_not_replace_the_refusal_with_a_vaguer_one(self):
        source = function_source(CRAFT_PY, "_run_crafting")
        self.assertNotIn(
            'message = "Missing materials for that recipe."', source,
            "the handler is discarding the engine's list again",
        )
        self.assertIn("missing materials", source, "the handler no longer recognises the refusal")

    def test_what_you_know_has_a_reader(self):
        """`character_recipes` must be readable, and the read must be used."""
        self.assertIn("character_recipes", function_source(CORE_PY, "get_known_recipes"))
        status = function_source(STATUS_PY, "profession_status")
        self.assertIn("get_known_recipes", status, "nothing shows a player the methods they know")

    def test_the_status_page_reads_a_recipes_cost_and_what_is_carried(self):
        status = function_source(STATUS_PY, "profession_status")
        for needed in ("get_recipe_definition", "get_inventory", '"cost"'):
            self.assertIn(needed, status,
                          f"profession status no longer reads {needed}; a cost nobody prints is "
                          "the fault this test exists for")

    def test_a_recipes_materials_are_not_re_proven_here(self):
        """The obvious sixth test is deliberately absent, and this says why.

        A first version of this file swept `sells`, `forage_materials` and the
        recipe outputs and asserted every recipe cost was reachable. It failed
        on its first run naming `jade_life_herb` and `twin_extremes_fruit` -
        and both are *sourced*: the herb is a secret-realm room reward and the
        fruit is resolved in `crafting_actions.go` off the world tier. The
        sweep was simply a worse copy of a rule the tree already states, in
        `test_every_item_has_a_source.py`, which greps production Go and
        Python for every item id and keeps `SOURCELESS_ITEMS` empty.

        Two statements of one rule are free to disagree, and the weaker one is
        the one that produces false findings - the same reason v1.0.1 did not
        ship its table-level sweep. So the rule stays in the one place, and
        this test holds that it is still there rather than restating it.
        """
        gate = PROJECT_ROOT / "tests" / "python" / "unit" / "test_every_item_has_a_source.py"
        self.assertTrue(gate.exists(), "the item-source gate is gone; recipe costs are now unheld")
        text = gate.read_text(encoding="utf-8")
        self.assertIn("SOURCELESS_ITEMS", text, "the item-source gate no longer names its allowlist")


if __name__ == "__main__":
    unittest.main()
