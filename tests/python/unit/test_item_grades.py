"""Item grades, the presentation half (v1.7.0).

A crafted item carries its grade as a suffix on its id (``qi_pill@high``), and
the bare id is the first grade. The engine owns what a grade does; the bot
only names it. Two things are held here.

The first is that a graded id is named and found, not printed raw or answered
"Unknown item": ``WORLD.items`` is keyed on base ids, so a bare
``WORLD.items.get(item_id)`` on a carried id answers nothing for every graded
item - ``/use`` refusing a High pill as unknown, the bag printing
``qi_pill@high``. ``World.item_definition`` is the door, and the second test
holds the bot to it by AST, the way the engine's
``TestTheCatalogueIsReadByOneDoor`` holds production Go to ``itemDef``.
"""

from __future__ import annotations

import ast
import unittest

from app.rules.item_grades import base_item_id, grade_label, split_item_grade
from tests.support import PROJECT_ROOT

from app.rules.game import World

WORLD = World(PROJECT_ROOT / "content" / "world.json")


class ItemGradeNamingTests(unittest.TestCase):
    def test_a_graded_id_splits_and_a_bare_one_is_the_first_grade(self):
        self.assertEqual(split_item_grade("qi_pill@high"), ("qi_pill", "high"))
        self.assertEqual(split_item_grade("qi_pill"), ("qi_pill", ""))
        self.assertEqual(base_item_id("qi_pill@mid"), "qi_pill")
        self.assertEqual(grade_label(WORLD.item_grades, "qi_pill"), "")

    def test_a_graded_item_is_named_and_priced_at_its_grade(self):
        base = WORLD.item_definition("qi_pill")
        high = WORLD.item_definition("qi_pill@high")
        self.assertTrue(base, "the reader found no qi_pill; the gate is broken, not the tree")
        self.assertEqual(high.get("name"), f"{base['name']} (High)")
        self.assertEqual(int(high["base_price"]), int(base["base_price"]) * 4)
        self.assertEqual(WORLD.item_name("qi_pill@high"), high["name"])

    def test_what_the_engine_refuses_is_not_a_thing_here_either(self):
        # The first rung is written bare; a material has no grade; an unknown
        # rung is nothing - itemDef's three refusals.
        for item_id in ("qi_pill@low", "spirit_herb@high", "qi_pill@legendary"):
            with self.subTest(item_id=item_id):
                self.assertEqual(WORLD.item_definition(item_id), {})


class TheBotReadsItemsThroughOneDoorTests(unittest.TestCase):
    def test_no_bot_module_looks_a_carried_id_up_in_the_bare_map(self):
        found: list[str] = []
        read = 0
        for path in sorted((PROJECT_ROOT / "app" / "bot").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                target = None
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
                    target = node.func.value
                elif isinstance(node, ast.Subscript):
                    target = node.value
                elif isinstance(node, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
                    target = node.comparators[0]
                if isinstance(target, ast.Attribute) and target.attr == "items" and isinstance(target.value, ast.Name) and target.value.id == "WORLD":
                    found.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "item_definition":
                    read += 1
        self.assertGreater(read, 5, "the walk found no item_definition call; the gate is broken, not the tree")
        self.assertEqual(found, [], "a graded id is not a key of WORLD.items; read it through WORLD.item_definition")


if __name__ == "__main__":
    unittest.main()
