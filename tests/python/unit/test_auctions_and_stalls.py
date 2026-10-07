"""Auctions and stalls: the house's coin, the unread lot's card, and /stall as a slash command.

Merged from:

test_a_lot_is_listed_in_the_houses_coin.py — `/auction sell` lists in the house's own coin unless told otherwise (v1.2.3).

The command's default currency was the Mortal stone in every world, so a lot
listed on a Spiritual, Immortal or Celestial floor asked bidders for a coin
nobody in that world is paid in (rc.44). The default reads the house's
`default_currency`, which every one of the 48 houses in the content declares.

test_a_public_lot_card_does_not_name_an_unread_lot.py — A lot's public card names only what its consignor could read (v1.12.3).

An NPC's find is consigned to the house unappraised (schema 45), and
`/auction browse` titles such a lot "Unidentified Lot" with the house's grade
band to anybody who has not read one before - the whole point of
`/economy → Auction House → Appraise`. Since v1.7.0 the tick posts a card for
every open lot the world listed, and the card titled it with
`WORLD.item_name(item_id)` and never checked `appraised`: every reader of the
channel was handed the answer the board withholds.

A card is public, so it asks the one rule (`lot_identity`) with nobody known.
Driven through the card rather than read, because the title is one expression
and the source reads correctly in both versions.

test_the_stalls_have_a_slash_command.py — `/stall` is a slash command of its own (v1.7.4).

Asked for the stall commands, the answer was `/stall board`, `/stall buy` and
the rest - and the owner said "not there". They were right: `stall_group` had
backed the `/economy → Market Stalls` page since v1.5.0 and was never added to
the command tree, so the name a player types reached nothing. That is `/learn`
(rc.43) again: a command that exists, answered by the engine, reached by no
door a player uses.

The tree tuple named only bound roots, because registration resolved each name
through `ACTIONS.root`, which knows no groups. It resolves through
`_tree_command` now, which answers a group from `_GROUP_ACTION_ROOTS` - the map
the hub pages are built from - so the group registered is the very object the
hub page's leaves belong to.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from discord import app_commands

from tests.support import PROJECT_ROOT, bot_function_source, code_only

ROOT = Path(__file__).resolve().parents[3]

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


# --- from test_a_lot_is_listed_in_the_houses_coin.py ---

class ALotIsListedInTheHousesCoin(unittest.TestCase):
    def test_the_default_is_not_a_currency_id(self):
        tree = ast.parse(bot_function_source("auction_sell"))
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "auction_sell")
        names = [a.arg for a in fn.args.args + fn.args.kwonlyargs]
        defaults = dict(zip(names[-len(fn.args.defaults):], fn.args.defaults)) if fn.args.defaults else {}
        default = defaults.get("currency")
        self.assertIsNotNone(default, "the currency parameter could not be read; the reader is broken, not the tree")
        self.assertEqual(default.value, "", f"the default currency is {default.value!r}, a coin that is wrong in three worlds of four")
        body = code_only(ast.unparse(fn))
        self.assertIn("default_currency", body, "the handler never reads the house's default_currency")

    def test_every_house_declares_its_coin(self):
        world = json.loads((ROOT / "content" / "world.json").read_text(encoding="utf-8"))
        houses = world["auction_houses"]
        houses = houses if isinstance(houses, list) else list(houses.values())
        self.assertGreater(len(houses), 40)
        missing = [h["name"] for h in houses if not str(h.get("default_currency") or "").strip()]
        self.assertEqual(missing, [], "a house with no default_currency would list in nothing")


# --- from test_a_public_lot_card_does_not_name_an_unread_lot.py ---

def _feed():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.auction_feed")


def _lot(**extra) -> dict:
    lot = {"auction_id": 7, "house_id": "", "item_id": "qi_pill", "quantity": 1, "currency_id": "low_spirit_stone",
           "starting_bid": 10, "current_bid": 0, "active": 1, "ends_at": 2_000_000_000,
           "seller_user_id": None, "seller_npc_name": "Digger Wu", "appraised": 0, "grade_band": "of middling grade"}
    lot.update(extra)
    return lot


class AnUnreadLotIsNotNamedOnItsCard(unittest.TestCase):
    def setUp(self):
        self.feed = _feed()
        self.house = next(iter(sorted(self.feed.WORLD.auction_houses)))
        self.name = self.feed.WORLD.item_name("qi_pill")
        self.assertTrue(self.name and self.name != "Unidentified Lot", "the fixture item has no name; the gate is broken, not the tree")

    def _card_text(self, lot, state="open") -> str:
        card = asyncio.run(self.feed.lot_card(self.house, lot, state=state))
        return card.text()

    def test_an_unappraised_lot_is_an_unidentified_lot_with_its_grade_band(self):
        text = self._card_text(_lot())
        self.assertIn("Unidentified Lot", text)
        self.assertNotIn(self.name, text, "the public card named a lot its consignor could not read: " + text[:200])
        self.assertIn("of middling grade", text, "the house's word on the lot is missing from the card")
        self.assertIn("Appraise", text, "the card does not say how to read it")

    def test_the_settled_cards_keep_it_unnamed_too(self):
        for state in ("sold", "unsold"):
            with self.subTest(state=state):
                self.assertNotIn(self.name, self._card_text(_lot(current_bid=40, merchant_buyer="madam_wen"), state=state))

    def test_an_appraised_lot_is_named(self):
        text = self._card_text(_lot(appraised=1))
        self.assertIn(self.name, text)
        self.assertNotIn("Unidentified", text)

    def test_the_card_and_the_board_read_one_rule(self):
        # The board's per-player `known` set is the only thing a card lacks:
        # both ask the same function, which lives below the commands.
        with patch.dict(os.environ, ENV):
            economy = importlib.import_module("app.bot.commands.economy")
        self.assertIs(economy.lot_identity, self.feed.lot_identity)
        self.assertEqual(self.feed.lot_identity(_lot(), {"qi_pill"})[0], self.name,
                         "somebody who has read one before still names it on sight")


# --- from test_the_stalls_have_a_slash_command.py ---

def _surface():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.surface")


class TheStallsHaveASlashCommand(unittest.TestCase):
    def test_stall_is_a_tree_command(self):
        surface = _surface()
        self.assertIn("stall", surface.TREE_COMMANDS, "/stall is not registered with Discord")

    def test_it_registers_the_group_the_hub_page_uses(self):
        surface = _surface()
        group = surface._tree_command("stall")
        self.assertIs(group, surface._GROUP_ACTION_ROOTS["stall"])
        self.assertIsInstance(group, app_commands.Group)
        leaves = {c.name for c in group.commands}
        self.assertTrue({"board", "buy", "status", "open", "list", "withdraw", "close"} <= leaves, sorted(leaves))

    def test_registration_resolves_every_tree_name_through_a_resolver_that_knows_groups(self):
        source = (PROJECT_ROOT / "app" / "bot" / "surface.py").read_text(encoding="utf-8")
        fn = next(n for n in ast.walk(ast.parse(source))
                  if isinstance(n, ast.FunctionDef) and n.name == "register_command_surface")
        added = [ast.unparse(n.args[0]) for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and ast.unparse(n.func).endswith("tree.add_command") and n.args]
        self.assertIn("_tree_command(name)", added,
                      "the tree tuple is resolved through ACTIONS.root again, which knows no groups")
        surface = _surface()
        for name in surface.TREE_COMMANDS:
            self.assertIsNotNone(surface._tree_command(name), name)


class TheRaidsHaveSlashCommands(unittest.TestCase):
    """`/boss` and `/party` (v1.7.10): asked "where is the boss command
    located?", the answer was a hub page, because neither group had ever been
    registered - the `/stall` fault twice over."""

    GROUPS = {"boss": {"list", "start", "status", "act", "claim"}, "party": {"create", "join", "leave"},
              # v1.9.1: body cultivation is a slash command of its own too.
              "body": {"cultivate", "sheet", "breakthrough"},
              "profession": {"status", "exam"}}

    def test_each_is_a_tree_command_resolving_to_the_hub_pages_group(self):
        surface = _surface()
        for name, wanted in self.GROUPS.items():
            with self.subTest(name=name):
                self.assertIn(name, surface.TREE_COMMANDS, f"/{name} is not registered with Discord")
                group = surface._tree_command(name)
                self.assertIs(group, surface._GROUP_ACTION_ROOTS[name])
                leaves = {c.name for c in group.commands}
                self.assertTrue(wanted <= leaves, sorted(leaves))


if __name__ == "__main__":
    unittest.main()
