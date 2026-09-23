"""A method can be made in the world that sells it (v1.0.15).

Found by playing. An Apprentice alchemist standing in the Jadewood Apothecary -
freshly examined, so the hall had just taught them the Heart Calming Pill -
pressed Craft and was told

    🧰 missing materials: Twin Extremes Ice-Fire Fruit x1
    Buy them at a hall of the trade (/economy → City Shops → Here) or gather
    them (/craft → Alchemy → Forage).

and neither half was true anywhere they could stand. No shelf in the game
carries the fruit - it is auction-grade, and
`test_every_shop_is_a_kept_interior_with_a_real_shelf` forbids auction-grade
stock, correctly - and the forage roll offers it only at `worldTier >= 1`, the
Spiritual World and up. The slip that teaches the method is sold in exactly one
world, the Mortal World, and the Apprentice examination teaches it wherever it
is sat. **The one world that sold the method was the one world that could
never make it.** The price said the recipe had never been meant either: a
1,050-stone fruit went into a pill the shops sell for 13 to 26.

**v1.0.1 looked straight at it and let it go.** `test_a_recipe_tells_you_what_
it_needs.py` records a first sweep that failed naming `twin_extremes_fruit`, and
deleted that sweep because the fruit "*is* sourced ... resolved in
`crafting_actions.go` off the world tier". Sourced, yes: one world up.
`test_every_item_has_a_source.py` asks whether production names an id at all,
and `_gatherable_items` in the content gate flattens the forage pool across
worlds, so both answered "yes" to a question that has a world in it. A source
is a place, and what matters to the person holding the method is whether it is
*their* place.

So this counts only sources whose world the content itself states:

- a shelf in that world;
- a guaranteed item in a room of a secret realm that stands in that world;
- that world's own tier materials (`event_sites.tier_materials` - the forage
  common drop, and every `@herb`/`@ore`/`@core` node an event site pays);
- a tier-flat forage material (`forage_materials`).

**The forage rare pool is deliberately not counted.** It is a chance - fifteen
percent for the fruit - so a method that hangs on it is a lottery rather than a
recipe; and its world gate is Go code, so a Python reading of it would be a
second copy of an engine rule, which is exactly how `_gatherable_items` came to
see one world where there are four.
"""
from __future__ import annotations

import asyncio
import copy
import importlib
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import PROJECT_ROOT

WORLD = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))

CORE = WORLD["items"]["beast_core"]["name"]
HERB = WORLD["items"]["spirit_herb"]["name"]

ENV = {
    "DISCORD_TOKEN": "test-token",
    "GUILD_ID": "123456789012345678",
    "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890",
    "DATABASE_PATH": "data/test.sqlite3",
}


def offered_in(world: dict, name: str) -> set[str]:
    """Everything the content says can be had inside one world without luck."""
    found: set[str] = set()
    for shop in world["shops"].values():
        if shop.get("world") == name:
            found |= {str(line["item_id"]) for line in shop.get("sells") or []}
    for realm in world["secret_realms"].values():
        if (world["locations"].get(realm.get("location")) or {}).get("world") == name:
            for room in realm.get("rooms") or []:
                found |= set(room.get("items") or {})
    found |= set((world["event_sites"]["tier_materials"].get(name) or {}).values())
    found |= set(world.get("forage_materials") or {})
    return found


def slip_worlds(world: dict) -> dict[str, set[str]]:
    """Each recipe, and the worlds whose shelves sell the slip that teaches it."""
    teaches = {item: spec["teaches_recipe"] for item, spec in world["items"].items()
               if spec.get("teaches_recipe")}
    where: dict[str, set[str]] = {}
    for shop in world["shops"].values():
        for line in shop.get("sells") or []:
            recipe = teaches.get(str(line["item_id"]))
            if recipe:
                where.setdefault(recipe, set()).add(str(shop["world"]))
    return where


def unmakeable(world: dict) -> list[str]:
    """Every (recipe, world) pair where the world sells the method and not its makings."""
    findings = []
    offers: dict[str, set[str]] = {}
    for recipe, worlds in sorted(slip_worlds(world).items()):
        for name in sorted(worlds):
            have = offers.setdefault(name, offered_in(world, name))
            for item in sorted(world["recipes"][recipe]["cost"]):
                if item not in have:
                    findings.append(f"{recipe} is sold in the {name} and needs {item}, which the {name} does not offer")
    return findings


class AMethodCanBeMadeWhereItIsSold(unittest.TestCase):
    def test_the_reader_sees_the_content(self):
        """Asserted before it is trusted (rc.57): a reader that finds nothing
        passes every assertion after it."""
        mortal = offered_in(WORLD, "Mortal World")
        for known in ("spirit_herb", "beast_core", "spirit_iron", "talisman_paper", "jade_life_herb"):
            self.assertIn(known, mortal, f"the Mortal World reader lost {known}; the reader is broken, not the tree")
        where = slip_worlds(WORLD)
        self.assertGreaterEqual(len(where), 30, "the slip reader found almost no methods")
        self.assertEqual(where.get("Heart Calming Pill"), {"Mortal World"})

    def test_every_method_can_be_made_in_every_world_that_sells_it(self):
        self.assertEqual(unmakeable(WORLD), [], (
            "a world sells a method whose makings it does not offer - on no shelf there, in no realm "
            "room there, and not its own tier material. The player who buys the slip is told to buy "
            "the rest at a hall that has none"))

    def test_the_gate_names_the_recipe_that_found_it(self):
        """The drill, kept: put the fruit back and the gate must say so.

        A gate that passes the tree it was written for proves nothing about
        whether it can see the fault (rc.47); this one is run against the
        exact content that shipped the bug.
        """
        broken = copy.deepcopy(WORLD)
        broken["recipes"]["Heart Calming Pill"]["cost"] = {"spirit_herb": 3, "twin_extremes_fruit": 1}
        self.assertIn(
            "Heart Calming Pill is sold in the Mortal World and needs twin_extremes_fruit, "
            "which the Mortal World does not offer",
            unmakeable(broken))

    def test_the_first_examination_teaches_only_what_can_be_made_where_it_is_sat(self):
        """The door the report came through: a hall teaches its whole rank.

        `teachRankRecipesTx` hands over every recipe of the trade at exactly the
        rank passed, wherever the hall stands. Only the first examination is held
        here, because it is the one a Mortal-World cultivator sits first and it
        is where the report came from. The higher ones teach past the world they
        are sat in - Journeyman in the Mortal World teaches the Dawn Lotus
        Vitality Pill, whose herb is shelved from the Spiritual World up - which
        is knowledge ahead of the road rather than a dead end, now that the
        refusal names the worlds that do sell it. Whether a hall should teach
        only what its own world can make is a decision, and it is in
        `docs/TODO.md` rather than decided here.
        """
        exams = WORLD["profession_exams"]
        self.assertTrue(exams, "the examination roster is gone; the reader is broken, not the tree")
        offers = {name: offered_in(WORLD, name) for name in {s["world"] for s in WORLD["shops"].values()}}
        held = 0
        for trade, ladder in sorted(exams.items()):
            first = min(ladder, key=lambda exam: int(exam["rank"]))
            worlds = sorted({s["world"] for s in WORLD["shops"].values() if s["kind"] == first["hall_kind"]})
            taught = [name for name, r in WORLD["recipes"].items()
                      if r["profession"] == trade and int(r["min_level"]) == int(first["rank"])]
            for recipe in taught:
                for world in worlds:
                    held += 1
                    missing = sorted(set(WORLD["recipes"][recipe]["cost"]) - offers[world])
                    with self.subTest(trade=trade, recipe=recipe, world=world):
                        self.assertEqual(missing, [], (
                            f"the {first['rank_name']} examination at a {first['hall_kind']} in the {world} "
                            f"teaches {recipe}, and the {world} does not offer {missing}"))
        self.assertGreater(held, 0, "no examination taught anything; the reader is broken, not the tree")


class TheRefusalSaysWhereItIsSold(unittest.TestCase):
    """The other half: "buy them at a hall of the trade" was said about a fruit
    no hall in the player's world had. The refusal reads the shelves now."""

    def _exploration(self):
        return importlib.import_module("app.bot.commands.exploration")

    def test_a_material_this_world_does_not_shelve_is_said_so(self):
        with patch.dict(os.environ, ENV):
            where = self._exploration().where_it_is_sold("dawnlotus_herb", "Jadewood Apothecary")
        self.assertIn("no hall in the Mortal World sells it", where)
        self.assertIn("Spiritual World", where, "it should name a world that does")

    def test_a_material_on_this_citys_shelf_is_sold_here(self):
        with patch.dict(os.environ, ENV):
            where = self._exploration().where_it_is_sold("spirit_herb", "Jadewood Apothecary")
        self.assertEqual(where, "sold here, at Jadewood Apothecary")

    def test_a_material_elsewhere_in_this_world_names_the_cities(self):
        mortal_cities = sorted({s["city"] for s in WORLD["shops"].values() if s["world"] == "Mortal World"
                                and any(line["item_id"] == "beast_core" for line in s["sells"])})
        self.assertTrue(mortal_cities, "no Mortal shelf sells beast cores; pick another material")
        with patch.dict(os.environ, ENV):
            where = self._exploration().where_it_is_sold("beast_core", "Jadewood Apothecary")
        self.assertTrue(where.startswith("sold in the Mortal World at "), where)
        self.assertIn(mortal_cities[0], where)

    def test_a_private_place_is_not_guessed_into_a_world(self):
        """rc.52: a household belongs to no world, so the Mortal World is not assumed."""
        with patch.dict(os.environ, ENV):
            where = self._exploration().where_it_is_sold("dawnlotus_herb", "birth_family:1")
        self.assertNotIn("no hall in the Mortal World", where)
        self.assertTrue(where.startswith("sold in halls of the "), where)

    def _refuse(self, inventory):
        """Drive `_run_crafting` into the engine's missing-materials refusal."""
        with patch.dict(os.environ, ENV):
            exploration = self._exploration()
            sent: list[str] = []

            async def character(_interaction):
                return {"name": "Tester", "location": "Jadewood Apothecary"}

            async def recipe(_name):
                return {"profession": "Alchemy", "cost": {"spirit_herb": 2, "beast_core": 1}, "tn": 12}

            async def refused(*_args, **_kwargs):
                raise exploration.GameEngineError(f"missing materials: {CORE} x1")

            async def send_message(content, **_kwargs):
                sent.append(content)

            interaction = SimpleNamespace(
                id=1, user=SimpleNamespace(id=42),
                response=SimpleNamespace(send_message=send_message))
            with patch.object(exploration, "require_character", character), \
                    patch.object(exploration.DB, "get_recipe_definition", recipe), \
                    patch.object(exploration.DB, "get_inventory", inventory), \
                    patch.object(exploration.ENGINE, "authoritative_action", refused):
                asyncio.run(exploration._run_crafting(interaction, "Recovery Pill"))
        self.assertEqual(len(sent), 1, "the refused craft did not answer exactly once")
        return sent[0]

    def test_the_refusal_names_where_each_short_material_is_sold(self):
        async def carrying(_user_id):
            return {"spirit_herb": 5}

        reply = self._refuse(carrying)
        self.assertIn(f"missing materials: {CORE} x1", reply, "the engine's own list must survive")
        self.assertIn(f"**{CORE}**: sold in the Mortal World at ", reply)
        self.assertNotIn(f"**{HERB}**", reply, "a material the player is carrying is not short")

    def test_an_unreadable_bag_still_gets_the_refusal(self):
        async def boom(_user_id):
            raise RuntimeError("no database")

        reply = self._refuse(boom)
        self.assertIn(f"missing materials: {CORE} x1", reply)
        self.assertNotIn(f"**{CORE}**:", reply)


if __name__ == "__main__":
    unittest.main()
