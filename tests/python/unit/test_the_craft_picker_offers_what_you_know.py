"""The craft picker offers what you know (v1.0.5).

`recipe_autocomplete` was `DB.search_catalog("recipe", current, 25)` — the whole
33-recipe catalogue, capped at Discord's 25 — while `craft.resolve` refuses any
method the player has not learned:

    you do not know the method for X; it is carried on a jade slip

So a fresh character who knows about three methods was offered twenty-five. And
because `hubs._autocomplete_provider` deliberately reuses a slash command's own
autocomplete as a panel's option source *"without duplicating game lookup
logic"*, the same list is rendered as a drop-down — which is how a player asking
"why is crafting a drop-down menu" was really asking why the menu was full of
things they could not make.

rc.46 settled this rule one surface over: **a surface must not offer what the
engine will refuse.** The quest journal stopped listing what no roster would
hand over for exactly this reason.

Knowing a method and being equal to it are two different refusals, so a recipe
above the player's rank stays on the list; `/craft → Profession → Profession
Status` is where ranks and materials are spelled out.
"""
from __future__ import annotations

import ast
import asyncio
import importlib
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import PROJECT_ROOT

ENV = {
    "DISCORD_TOKEN": "test-token",
    "GUILD_ID": "123456789012345678",
    "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890",
    "DATABASE_PATH": "data/test.sqlite3",
}

EXPLORATION = PROJECT_ROOT / "app" / "bot" / "commands" / "exploration.py"


def _function(source: str, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} is gone; this gate guards a function that moved")


def _code(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """The function's statements without its docstring.

    rc.52's rule, met by this gate on its own first run: the docstring below
    *explains* that `search_catalog` was the old reader, so a whole-function
    search found the very string the rule forbids and failed against correct
    code. A gate that cannot tell prose from code is decoration.
    """
    body = fn.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    return "\n".join(ast.unparse(stmt) for stmt in body)


class TheCraftPickerOffersWhatYouKnowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = EXPLORATION.read_text(encoding="utf-8")
        self.picker = _function(self.source, "recipe_autocomplete")

    def test_it_reads_what_the_cultivator_has_learned(self):
        body = _code(self.picker)
        self.assertIn(
            "get_known_recipes", body,
            "the craft picker does not read what the player has learned, so it is offering the "
            "catalogue and `craft.resolve` will refuse most of it",
        )

    def test_it_no_longer_offers_the_whole_catalogue(self):
        body = _code(self.picker)
        self.assertNotIn(
            "search_catalog", body,
            "the craft picker searches the whole recipe catalogue again; rc.46's rule is that a "
            "surface must not offer what the engine will refuse",
        )

    def test_the_picker_returns_only_learned_methods(self):
        """Behavioural: a cultivator who knows two methods is offered two."""
        with patch.dict(os.environ, ENV):
            exploration = importlib.import_module("app.bot.commands.exploration")

            known = [
                {"recipe": "Swift-Wind Talisman", "source": "slip"},
                {"recipe": "Qi Nourishing Pill", "source": "household"},
            ]

            async def fake_known(_user_id):
                return known

            interaction = SimpleNamespace(user=SimpleNamespace(id=42))
            with patch.object(exploration.DB, "get_known_recipes", fake_known):
                offered = asyncio.run(exploration.recipe_autocomplete(interaction, ""))
                self.assertEqual(
                    sorted(choice.value for choice in offered),
                    ["Qi Nourishing Pill", "Swift-Wind Talisman"],
                )
                # And it still narrows as the player types.
                narrowed = asyncio.run(exploration.recipe_autocomplete(interaction, "talis"))
                self.assertEqual([choice.value for choice in narrowed], ["Swift-Wind Talisman"])

    def test_a_cultivator_who_knows_nothing_is_offered_nothing_and_told_why(self):
        """An empty picker is not a dead end: the hub prints the registered
        hint, which must name the two doors that end it."""
        with patch.dict(os.environ, ENV):
            exploration = importlib.import_module("app.bot.commands.exploration")
            hubs = importlib.import_module("app.bot.hubs")

            async def knows_nothing(_user_id):
                return []

            interaction = SimpleNamespace(user=SimpleNamespace(id=42))
            with patch.object(exploration.DB, "get_known_recipes", knows_nothing):
                self.assertEqual(asyncio.run(exploration.recipe_autocomplete(interaction, "")), [])

            hint = hubs._HUB_OPTION_HINTS.get(("craft", "recipe"), "")
            self.assertTrue(hint, "an empty craft picker explains nothing")
            for door in ("slip", "Profession Status"):
                self.assertIn(door, hint)

    def test_a_failed_read_offers_nothing_rather_than_raising(self):
        """An autocomplete that raises is a picker that never opens, and this
        one now touches the database on every keystroke."""
        with patch.dict(os.environ, ENV):
            exploration = importlib.import_module("app.bot.commands.exploration")

            async def boom(_user_id):
                raise RuntimeError("no database")

            interaction = SimpleNamespace(user=SimpleNamespace(id=42))
            with patch.object(exploration.DB, "get_known_recipes", boom):
                self.assertEqual(asyncio.run(exploration.recipe_autocomplete(interaction, "")), [])


if __name__ == "__main__":
    unittest.main()
