"""The craft menu says what a method makes does, and a batch is the engine's (v1.21.0).

Reported from play: "Multi craft system for pills", then "we need a better menu
for selecting what you want to craft, and what the crafted items does is
needed". The picker named recipes and nothing else, the reply said "Created Qi
Nourishing Pill" and never what one is for, and making five pills was five
presses through the action meter.

Four things are held here:

- every recipe output says what it does, through `World.item_does`, the one
  door the picker, the reply and the status page all ask;
- the grade scaling `item_effects` prints is the engine's own, read out of the
  Go bodies rather than restated in a comment;
- the batch cap is stated once in Go and the bot's two spellings of it agree;
- the menu orders what you can make now first and says how many.
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

from app.rules.game import World
from app.rules.item_effects import craft_readiness, describe_item_use, describe_modifier
from tests.support import PROJECT_ROOT

ENV = {
    "DISCORD_TOKEN": "test-token",
    "GUILD_ID": "123456789012345678",
    "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890",
    "DATABASE_PATH": "data/test.sqlite3",
}

CONTENT = PROJECT_ROOT / "content" / "world.json"
EXPLORATION = PROJECT_ROOT / "app" / "bot" / "commands" / "exploration.py"
CRAFTING_GO = PROJECT_ROOT / "go_core" / "internal" / "game" / "crafting_actions.go"
GRADE_GO = PROJECT_ROOT / "go_core" / "internal" / "game" / "item_grade.go"


def _go_function(source: str, name: str) -> str:
    start = source.index(f"func {name}(")
    depth, i = 0, source.index("{", start)
    for j in range(i, len(source)):
        if source[j] == "{":
            depth += 1
        elif source[j] == "}":
            depth -= 1
            if depth == 0:
                return source[i:j + 1]
    raise AssertionError(f"{name} has no balanced body; the reader is broken, not the tree")


class EveryCraftedItemSaysWhatItDoesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world = World(CONTENT)
        self.data = json.loads(CONTENT.read_text(encoding="utf-8"))

    def test_the_reader_finds_recipes(self):
        self.assertGreater(len(self.data["recipes"]), 20, "the content parse is broken, not the tree")

    def test_every_recipe_output_says_what_it_does(self):
        silent = [
            f"{recipe} -> {item}"
            for recipe, definition in sorted(self.data["recipes"].items())
            for item in definition.get("output") or {}
            if not self.world.item_does(item)
        ]
        self.assertEqual(silent, [], "a method makes something the craft menu cannot describe")

    def test_a_pill_names_its_numbers(self):
        self.assertIn("+20% cultivation speed", self.world.item_does("qi_pill"))
        self.assertIn("for 4h", self.world.item_does("qi_pill"))
        self.assertIn("restores 8 vitality", self.world.item_does("recovery_pill"))
        self.assertIn("+10 years of lifespan", self.world.item_does("longevity_pill"))

    def test_a_grade_is_described_at_its_grade(self):
        # Mid is x1.25 on the shipped ladder: 8 -> 10, x1.2 -> x1.25.
        self.assertIn("restores 10 vitality", self.world.item_does("recovery_pill@mid"))
        self.assertIn("+25% cultivation speed", self.world.item_does("qi_pill@mid"))
        self.assertIn("for 5h", self.world.item_does("qi_pill@mid"))

    def test_gear_is_described_by_the_gear_line(self):
        self.assertIn("attack", self.world.item_does("spirit_iron_sword"))

    def test_an_unknown_shape_says_nothing_rather_than_raising(self):
        self.assertEqual(describe_item_use({}), "")
        self.assertEqual(describe_modifier({"stat": "will", "operation": "add", "value": "x"}), "")


class TheGradeScalingIsTheEnginesTests(unittest.TestCase):
    """`item_effects` is a display twin; the Go bodies are the rule."""

    def test_a_modifier_scales_as_graded_effect_payload_does(self):
        body = _go_function(GRADE_GO.read_text(encoding="utf-8"), "gradedEffectPayload")
        self.assertIn('m["value"] = v * mult', body)
        self.assertIn('m["value"] = 1 + (v-1)*mult', body)
        self.assertEqual(describe_modifier({"stat": "will", "operation": "add", "value": 2}, 1.5), "+3 will")
        self.assertEqual(
            describe_modifier({"stat": "cultivation_gain", "operation": "mul", "value": 1.2}, 1.5),
            "+30% cultivation speed",
        )

    def test_a_whole_number_scales_as_graded_amount_does(self):
        body = _go_function(GRADE_GO.read_text(encoding="utf-8"), "gradedAmount")
        self.assertIn("maxI64(base, int64(math.Round(float64(base)*mult)))", body)


class TheBatchIsTheEnginesTests(unittest.TestCase):
    def test_the_cap_is_stated_once_and_its_twin_agrees(self):
        go = CRAFTING_GO.read_text(encoding="utf-8")
        match = re.search(r"const craftBatchMax int64 = (\d+)", go)
        self.assertIsNotNone(match, "craftBatchMax is gone from crafting_actions.go")
        tree = ast.parse(EXPLORATION.read_text(encoding="utf-8"))
        constant = next(
            node.value.value for node in tree.body
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "CRAFT_BATCH_MAX" for t in node.targets)
        )
        self.assertEqual(constant, int(match.group(1)), "the bot's CRAFT_BATCH_MAX disagrees with the engine's craftBatchMax")

    def test_craft_is_one_and_craft_all_asks_the_engine_how_many(self):
        """Craft All (v1.21.0) sends no number: the engine counts what the bags
        pay for in the transaction that spends them. Craft sends neither, so it
        is the payload it always was, and it has no quantity step."""
        tree = ast.parse(EXPLORATION.read_text(encoding="utf-8"))
        functions = {
            node.name: node for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef) and node.name in {"craft", "craft_all", "_run_crafting"}
        }
        self.assertEqual(set(functions), {"craft", "craft_all", "_run_crafting"})
        self.assertEqual([a.arg for a in functions["craft"].args.args], ["interaction", "recipe"],
                         "/craft grew a parameter, which is a step on the panel before every craft")
        self.assertIn("craft_all=True", ast.unparse(functions["craft_all"]))
        calls = [
            node for node in ast.walk(functions["_run_crafting"])
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "authoritative_action"
        ]
        self.assertEqual(len(calls), 1, "the reader did not find the one craft.resolve call")
        payload = ast.unparse(calls[0].args[2])
        self.assertEqual(payload, "{'recipe': recipe, **({'all': True} if craft_all else {})}",
                         "the craft payload changed; the engine counts Craft All, the bot sends no number")
        body = ast.unparse(functions["_run_crafting"])
        self.assertTrue("amount=max(1, successes)" in body,
                        "a batch advances a craft quest by one however many it made")

    def test_craft_all_is_on_the_general_crafting_page(self):
        surface = (PROJECT_ROOT / "app" / "bot" / "surface.py").read_text(encoding="utf-8")
        self.assertRegex(surface, r'_hub_page\("craft", "General Crafting", "[^"]*", "craft_all"\)')


class TheCraftMenuTests(unittest.TestCase):
    def test_readiness_counts_what_the_bags_pay_for(self):
        cost = {"spirit_herb": 3, "beast_core": 1}
        self.assertEqual(craft_readiness(cost, {"spirit_herb": 7, "beast_core": 5}, 0, 0), ("✅", 2))
        self.assertEqual(craft_readiness(cost, {"spirit_herb": 2, "beast_core": 5}, 0, 0), ("❌", 0))
        self.assertEqual(craft_readiness(cost, {"spirit_herb": 9, "beast_core": 9}, 0, 2), ("🔴", 3))

    def test_the_menu_puts_what_you_can_make_first_and_says_what_it_does(self):
        with patch.dict(os.environ, ENV):
            exploration = importlib.import_module("app.bot.commands.exploration")

            async def known(_user_id):
                # Heart Calming sorts first by name and is above a fresh rank,
                # so an unsorted menu would put it first (the drill).
                return [{"recipe": "Recovery Pill"}, {"recipe": "Qi Nourishing Pill"},
                        {"recipe": "Swift-Wind Talisman"}, {"recipe": "Heart Calming Pill"}]

            async def carried(_user_id):
                return {"spirit_herb": 7, "beast_core": 5}

            async def ranks(_user_id):
                return []

            interaction = SimpleNamespace(user=SimpleNamespace(id=42))
            with patch.object(exploration.DB, "get_known_recipes", known), \
                    patch.object(exploration.DB, "get_inventory", carried), \
                    patch.object(exploration.DB, "get_profession_progress", ranks):
                options = asyncio.run(exploration.recipe_hub_options(interaction, ""))
                choices = asyncio.run(exploration.recipe_autocomplete(interaction, ""))
        self.assertEqual(
            [option.value for option in options],
            ["Qi Nourishing Pill", "Recovery Pill", "Swift-Wind Talisman", "Heart Calming Pill"],
            "ready first, then short of materials, then above the rank",
        )
        qi = options[0]
        self.assertIn("can make 2", qi.description)
        self.assertIn("+20% cultivation speed", qi.description)
        self.assertEqual(qi.emoji, "⚗️")
        self.assertIn("short of materials", options[2].description)
        self.assertIn("needs rank", options[3].description)
        self.assertTrue(all(len(choice.name) <= 100 for choice in choices))
        self.assertEqual(choices[0].value, "Qi Nourishing Pill", "the slash picker's value is the bare recipe")


if __name__ == "__main__":
    unittest.main()
