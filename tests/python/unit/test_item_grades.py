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

Merged from:

test_the_recovery_picker_reads_the_grade.py — The battle's recovery picker prints a pill at its grade (v1.12.3).

`_battle_available_options` described a recovery item from
`WORLD.item_definition(iid)`, which is the *base* entry's use with its name and
price graded - so a Mid Recovery Pill read "Vitality +8" in the picker while
the engine restored 10 (`gradedAmount`, the grade's `effect_mult`). The number
a player chooses by was the Low pill's.

`effect_mult` and `graded_amount` in `app/rules/item_grades.py` are the display
twins of the engine's `itemEffectMult` and `gradedAmount`. The twins are held to
the ladder in the content file and to the engine's rounding (half away from
zero, never below the base), and the picker is driven with a carried graded pill
rather than read, because the source reads correctly in both versions.

test_what_a_grade_and_a_domain_say.py — What a grade does is printed, and a Domain is not the opponent's (v1.12.3).

Three presentation halves of engine fixes, each held where only Python can see:

- **The recovery picker printed base figures.** ``_battle_available_options`` built
  the line under a pill from the content's own ``instant`` numbers, while the
  engine heals at the carried grade. That half is held by
  the recovery-picker section of this file, which tested the same twin.
- **The opponent card printed the engine's own bookkeeping.** The battle row's
  ``opponent_modifiers_json`` records which effects have landed as
  ``effect:<id>`` beside the stats (a control effect lands once a battle), and
  ``opponent_debuff_label`` is the one thing that prints it.
- **The capped-grade reply named Tier 7.** A trade stops rising at rank 6, so the
  reply says the road the engine reports: a rank *and* an opener.

test_the_item_hint_names_the_item.py — An unknown item is answered with the item (v1.0.3).

The Admin Console's Inventory card refuses a display name and offers the closest
ids, so a GM can correct the field instead of the database. It offered the wrong
ones. Typing **"Qi Nourishment Pills"** answered:

    Did you mean: advanced_demonic_002_qi_refiner_manual,
    advanced_demonic_008_qi_refiner_manual, ...

Five demonic cultivation manuals, for a pill. The filter took `any()` token hit -
so the single token "qi" was enough - and then **sorted the survivors by id**, so
the 142 generated `advanced_*` ids win every time on the letter 'a'. `qi_pill`,
which is the item, matched exactly as well and was never shown.

And the near-misses a GM actually types are inflections - "Nourishment" for
"Nourishing", "Pills" for "Pill" - which a substring test cannot see at all.

test_the_mid_shelves_are_authored.py — The capitals' Mid-grade shelf lines are what the generator would write (v1.12.3).

`scripts/author_mid_shelves.py` adds, beside every crafted item a capital shop
sells or buys, a `@mid` line at the grade's price. It is idempotent, and it
was run once (v1.7.0). Then v1.8.0 gave the four capital apothecaries a buy
line for the Marrow-Tempering Pill and nobody ran it again: a keeper bought the
pill at Low for 40 and refused a Mid one, because the shelf carried no `@mid`
line for it.

A generator that has to be remembered is a rule nobody holds. So the content is
held to the generator's own answer: running it on the shipped file must change
nothing, and the day a crafted item is half-shelved this names the lines.

test_a_rank_raises_what_the_keeper_pays.py — A rank in a trade raises what a keeper pays for its goods (v1.0.17).

The engine owns the price: `tradeSellQuoteTx` in `go_core/internal/game/
trade_rank_price.go` adds two of the shop's coin per rank above Novice and
never reaches the cheapest shelf price, and `trade_rank_price_test.go` holds
that. This holds the presentation half: the Browse board and the Sell reply
say which rank raised the price, and say nothing when none did.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import importlib
import importlib.util
import json
import os
import pathlib
import unittest
from typing import Any
from unittest.mock import patch

import pytest

from tests.support import PROJECT_ROOT

from app.rules.battle import opponent_debuff_label
from app.rules.game import World
from app.rules.item_grades import (
    base_item_id,
    effect_mult,
    grade_cap_note,
    grade_label,
    graded_amount,
    split_item_grade,
)
from app.rules.progression_systems import profession_rank

pytestmark = pytest.mark.unit

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

# --- from test_the_recovery_picker_reads_the_grade.py ---


ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


GO = PROJECT_ROOT / "go_core" / "internal" / "game" / "item_grade.go"


def _battle():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.commands.battle")


class TheTwinsAgreeWithTheEngine(unittest.TestCase):
    def setUp(self):
        self.battle = _battle()
        self.ladder = self.battle.WORLD.item_grades
        self.assertTrue(self.ladder.get("grades"), "the ladder read found no grades; the gate is broken, not the tree")

    def test_every_rung_of_the_ladder_is_its_effect_mult(self):
        for index, rung in enumerate(self.ladder["grades"]):
            item_id = "recovery_pill" if index == 0 else f"recovery_pill@{rung['key']}"
            with self.subTest(rung=rung["key"]):
                self.assertEqual(effect_mult(self.ladder, item_id), float(rung.get("effect_mult") or 1))

    def test_a_bare_unknown_or_unusable_rung_is_one(self):
        self.assertEqual(effect_mult(self.ladder, "recovery_pill"), 1.0)
        self.assertEqual(effect_mult(self.ladder, "recovery_pill@not_a_rung"), 1.0)
        self.assertEqual(effect_mult({}, "recovery_pill@high"), 1.0)
        self.assertEqual(effect_mult({"grades": [{"key": "high", "effect_mult": 0}]}, "x@high"), 1.0)

    def test_rounding_is_the_engines_half_away_from_zero_and_never_below_the_base(self):
        # 15 x 1.5 = 22.5: the engine's math.Round gives 23, Python's round() gives 22.
        self.assertEqual(graded_amount(15, 1.5), 23)
        self.assertEqual(graded_amount(5, 1.5), 8)
        self.assertEqual(graded_amount(8, 1.25), 10)
        self.assertEqual(graded_amount(8, 1.0), 8)
        self.assertEqual(graded_amount(8, 0.5), 8, "a grade only ever adds")
        self.assertEqual(graded_amount(0, 3.0), 0)
        self.assertEqual(graded_amount(-4, 3.0), 0)


class TheBattlePickerPrintsTheGrade(unittest.TestCase):
    def setUp(self):
        self.battle = _battle()
        self.world = self.battle.WORLD
        self.vit = int(self.world.items["recovery_pill"]["use"]["instant"]["vitality_restore"])
        self.qi = int(self.world.items["qi_replenishment_pill"]["use"]["instant"]["qi_restore"])
        self.ladder = {r["key"]: float(r.get("effect_mult") or 1) for r in self.world.item_grades["grades"]}

    def _options(self, inventory):
        async def nothing(*_a, **_k):
            return []

        async def get_inventory(_uid):
            return inventory

        with patch.object(self.battle.DB, "get_law_progress", nothing), \
                patch.object(self.battle.DB, "get_manuals", nothing), \
                patch.object(self.battle.DB, "get_inventory", get_inventory):
            _techniques, usable = asyncio.run(self.battle._battle_available_options(7, {"realm_index": 0}))
        return {item_id: description for item_id, _name, description in usable}

    def test_a_graded_pill_is_described_at_its_grade(self):
        inventory = {"recovery_pill": 1, "recovery_pill@mid": 1, "recovery_pill@high": 1, "qi_replenishment_pill@high": 1}
        shown = self._options(inventory)
        self.assertEqual(set(shown), set(inventory), "a carried pill is missing from the picker; the drive is broken, not the tree")
        self.assertEqual(shown["recovery_pill"], f"Vitality +{self.vit}")
        self.assertEqual(shown["recovery_pill@mid"], f"Vitality +{graded_amount(self.vit, self.ladder['mid'])}")
        self.assertEqual(shown["recovery_pill@high"], f"Vitality +{graded_amount(self.vit, self.ladder['high'])}")
        self.assertEqual(shown["qi_replenishment_pill@high"], f"Qi +{graded_amount(self.qi, self.ladder['high'])}")
        self.assertNotEqual(shown["recovery_pill@mid"], shown["recovery_pill"],
                            "a Mid pill is described as the Low pill: its grade changes the number")

# --- from test_what_a_grade_and_a_domain_say.py ---


class TheOpponentCardSkipsTheEnginesRecord(unittest.TestCase):
    def test_the_mark_of_a_landed_effect_is_not_a_stat(self):
        raw = {"agility": -3, "escape_bonus": -5, "effect:spatial_lockdown": 1}
        self.assertEqual(opponent_debuff_label(raw), "agility -3 · escape -5")
        self.assertEqual(opponent_debuff_label({"effect:world_collapse": 1}), "")


class TheCappedGradeReplySaysTheRealRoad(unittest.TestCase):
    def test_the_note_names_the_rank_and_the_opener(self):
        rank = profession_rank(6, "Forging")
        note = grade_cap_note("Transcendent", "Mid", rank, "a fully refined flame")
        self.assertIn(f"Transcendent needs **{rank}** and **a fully refined flame**.", note)
        self.assertNotIn("Tier 7", note, "the note names a rank no trade reaches")

    def test_a_rung_with_no_opener_names_the_rank_alone(self):
        note = grade_cap_note("High", "Mid", profession_rank(3, "Alchemy"), "")
        self.assertTrue(note.endswith("**."), note)
        self.assertNotIn(" and **", note)

    def test_the_reply_hands_the_engines_answer_to_the_note(self):
        # The wire: the craft reply reads both fields the engine reports and
        # builds its note through the one helper.
        source = (PROJECT_ROOT / "app" / "bot" / "commands" / "exploration.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "grade_cap_note"]
        self.assertEqual(len(calls), 1, "the capped-grade reply no longer builds its note through grade_cap_note")
        read = {n.value for n in ast.walk(calls[0]) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        self.assertIn("grade_reached_opener", read, "the reply does not hand the engine's opener to the note")

# --- from test_the_item_hint_names_the_item.py ---


WORLD_hint = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))


def _suggest(typed: str) -> list[str]:
    """The dashboard's ranking, driven through the real server module."""
    from app.dashboard.server import AdminDashboardController

    catalog = {item_id: str(entry.get("name") or item_id) for item_id, entry in WORLD_hint["items"].items()}
    server = AdminDashboardController.__new__(AdminDashboardController)
    server.item_catalog = lambda: catalog  # type: ignore[method-assign]
    try:
        server.resolve_item_id(typed)
    except ValueError as exc:
        message = str(exc)
        if "Did you mean: " not in message:
            return []
        return [s.strip() for s in message.split("Did you mean: ", 1)[1].rstrip("?").split(", ")]
    return []


class TheItemHintNamesTheItemTests(unittest.TestCase):
    def test_the_reported_case_answers_with_the_pill(self):
        suggestions = _suggest("Qi Nourishment Pills")
        self.assertTrue(suggestions, "an unknown item must still be answered with the closest ids")
        self.assertTrue(
            suggestions[0].startswith("qi_pill"),
            f"'Qi Nourishment Pills' should first suggest qi_pill; it offered {suggestions}",
        )

    def test_a_suggestion_carries_the_name_a_gm_was_reading(self):
        """The id alone does not tell a GM which of five near-identical ids is
        the thing they typed the display name of."""
        self.assertIn("(Qi Nourishing Pill)", " ".join(_suggest("Qi Nourishment Pills")))

    def test_a_generated_manual_no_longer_wins_on_the_letter_a(self):
        for typed in ("Qi Nourishment Pills", "Recovery Pills", "Spirit Herbs"):
            with self.subTest(typed=typed):
                first = _suggest(typed)[0]
                self.assertFalse(
                    first.startswith("advanced_"),
                    f"{typed!r} still answers with a generated manual first: {first}",
                )

    def test_an_exact_id_and_an_exact_name_still_resolve(self):
        """The hint is the last resort; the two things that should just work
        must not have been broken to get it."""
        from app.dashboard.server import AdminDashboardController

        catalog = {item_id: str(entry.get("name") or item_id) for item_id, entry in WORLD_hint["items"].items()}
        server = AdminDashboardController.__new__(AdminDashboardController)
        server.item_catalog = lambda: catalog  # type: ignore[method-assign]
        self.assertEqual("qi_pill", server.resolve_item_id("qi_pill"))
        self.assertEqual("qi_pill", server.resolve_item_id("Qi Nourishing Pill"))

# --- from test_the_mid_shelves_are_authored.py ---


SCRIPT = PROJECT_ROOT / "scripts" / "author_mid_shelves.py"


CONTENT = PROJECT_ROOT / "content" / "world.json"


def _generator():
    spec = importlib.util.spec_from_file_location("author_mid_shelves", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lines_added(module, world: dict) -> list[str]:
    before = copy.deepcopy(world)
    module.author(world)
    found = []
    for key, shop in world["shops"].items():
        old = before["shops"][key]
        for line in shop["sells"]:
            if line not in old["sells"]:
                found.append(f"{key} sells {line['item_id']}")
        for item in shop["buys"]:
            if item not in old["buys"]:
                found.append(f"{key} buys {item}")
    return found


class TheGeneratorHasNothingLeftToDo(unittest.TestCase):
    def setUp(self) -> None:
        self.module = _generator()
        self.world = json.loads(CONTENT.read_text(encoding="utf-8"))

    def test_the_reader_finds_capitals_and_crafted_items(self):
        # A reader is asserted before it is trusted (rc.57): a generator that
        # saw no capital and no recipe would be a no-op on anything.
        self.assertGreaterEqual(len(self.module.crafted_items(self.world)), 30)
        capitals = [s for s in self.world["shops"].values() if self.world["locations"].get(s["city"], {}).get("realm_hub")]
        self.assertGreaterEqual(len(capitals), 4)
        self.assertGreaterEqual(
            sum(1 for s in capitals for item in s["buys"] if item.endswith("@mid")), 4,
            "no capital carries a Mid line at all; the gate is broken, not the tree",
        )

    def test_running_it_on_the_shipped_content_changes_nothing(self):
        added = _lines_added(self.module, self.world)
        self.assertEqual(
            added, [],
            "a crafted item is half-shelved at a capital - run scripts/author_mid_shelves.py: " + ", ".join(added),
        )

    def test_the_gate_names_the_line_that_went_missing(self):
        """The drill, kept inside the gate: the four apothecaries' marrow buy
        lines are taken out and the check must name them."""
        stale = copy.deepcopy(self.world)
        removed = []
        for key, shop in stale["shops"].items():
            if "marrow_tempering_pill@mid" in shop["buys"]:
                del shop["buys"]["marrow_tempering_pill@mid"]
                removed.append(key)
        self.assertEqual(len(removed), 4, "the four capital apothecaries no longer buy a Mid marrow pill")
        found = _lines_added(self.module, stale)
        self.assertEqual(sorted(found), sorted(f"{key} buys marrow_tempering_pill@mid" for key in removed))

# --- from test_a_rank_raises_what_the_keeper_pays.py ---


ROOT = pathlib.Path(__file__).resolve().parents[3]


ECONOMY = ROOT / "app" / "bot" / "commands" / "economy.py"


def _functions() -> dict[str, ast.AST]:
    tree = ast.parse(ECONOMY.read_text(encoding="utf-8"))
    return {node.name: node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _rank_lift():
    """The helper compiled from its own source, without booting the bot."""
    node = _functions().get("_rank_lift")
    if node is None:
        return None
    namespace: dict[str, Any] = {"Any": Any, "profession_rank": profession_rank}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(ECONOMY), "exec"), namespace)
    return namespace["_rank_lift"]


class TheKeeperSaysWhyItPaidMore(unittest.TestCase):
    def test_the_reader_finds_the_helper(self) -> None:
        self.assertIsNotNone(_rank_lift(), "_rank_lift is gone from economy.py; the gate is broken, not the tree")

    def test_a_rank_is_named_with_what_anybody_gets(self) -> None:
        lift = _rank_lift()
        row = {"trade": "Alchemy", "trade_rank": 2, "base_price": 4, "price": 8}
        board = lift(row)
        self.assertIn("Tier 2 Pill Adept", board)
        self.assertIn("4", board)
        sale = lift(row, sentence=True)
        self.assertIn("Tier 2 Pill Adept", sale)
        self.assertIn("lifts it from 4", sale)

    def test_nothing_is_said_when_no_rank_raised_it(self) -> None:
        lift = _rank_lift()
        self.assertEqual(lift({"trade": "", "trade_rank": 0, "base_price": 1}), "")
        self.assertEqual(lift({}), "")

    def test_the_board_and_the_sale_both_say_it(self) -> None:
        functions = _functions()
        for name in ("shop_browse", "shop_sell"):
            calls = {
                getattr(node.func, "id", "")
                for node in ast.walk(functions[name])
                if isinstance(node, ast.Call)
            }
            self.assertIn("_rank_lift", calls, f"{name} no longer says which rank raised the keeper's price")


if __name__ == "__main__":
    unittest.main()
