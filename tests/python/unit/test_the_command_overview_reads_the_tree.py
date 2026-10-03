"""`/commands` lists what the command tree holds, and nothing it does not (v1.20.1).

Asked for as *"A command overview"*. The card is built from
`tree.get_commands(...)`, never from a list of its own, so a command added
tomorrow is on it the day it is registered. The tests drive that rather than
read it: a command the tree holds and this repository has never heard of must
appear, and every command the real surface registers must appear when the
handler is pointed at a tree holding them.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import install_aiosqlite_shim

install_aiosqlite_shim()

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}

with patch.dict(os.environ, ENV):
    surface = importlib.import_module("app.bot.surface")
    overview = importlib.import_module("app.bot.commands.overview")
    cards = importlib.import_module("app.bot.cards")
    hubs = importlib.import_module("app.bot.hubs")


def _command(name, description=""):
    return SimpleNamespace(name=name, description=description)


def _group(name, description, *leaves):
    subs = [SimpleNamespace(name=leaf, qualified_name=f"{name} {leaf}", description="") for leaf in leaves]
    return SimpleNamespace(name=name, description=description, walk_commands=lambda: iter(subs))


def _live_commands():
    """Every command the real surface puts in the tree."""
    return [surface._tree_command(name) for name in surface.TREE_COMMANDS] + list(surface._HUB_COMMANDS)


class _Response:
    def __init__(self):
        self.sent = None

    async def send_message(self, **kwargs):
        self.sent = kwargs


def _interaction(commands, *, admin):
    tree = SimpleNamespace(get_commands=lambda guild=None: list(commands))
    user = SimpleNamespace(id=1, guild_permissions=SimpleNamespace(administrator=admin))
    return SimpleNamespace(client=SimpleNamespace(tree=tree), user=user, response=_Response())


class _DB:
    def __init__(self, character):
        self.character = character

    async def get_character(self, user_id):
        if isinstance(self.character, Exception):
            raise self.character
        return self.character


def _answer(value):
    async def provider(interaction):
        if isinstance(value, Exception):
            raise value
        return value
    return provider


def _handler_text(commands, *, admin, character=None, hidden_hubs=None, held=None, hidden=None,
                  shape=None):
    """Run the real handler against `commands`, with the character read and
    the panels' three providers answered by the test. The providers are the
    `hubs` wrappers, which already fail towards showing everything; a test
    that wants a provider to raise patches the registered callable instead."""
    interaction = _interaction(commands, admin=admin)
    handler = surface.ACTIONS.handler_for(surface.ACTIONS.root("commands"))
    character = {"user_id": 1, "realm_index": 0} if character is None else character
    shape = {"hidden_hubs": dict(hidden_hubs or {})} if shape is None else shape
    with patch.object(overview, "DB", _DB(character)), \
         patch.object(overview, "menu_shape", _answer(shape)), \
         patch.object(overview, "not_yet_unlocked", _answer(dict(held or {}))), \
         patch.object(overview, "hidden_actions", _answer(dict(hidden or {}))):
        asyncio.run(handler(interaction))
    sent = interaction.response.sent
    if sent is None:
        raise AssertionError("/commands sent nothing")
    return cards.card_text(sent["view"]), sent


class TheCardIsTheTree(unittest.TestCase):
    def test_a_command_nobody_wrote_down_is_listed(self):
        """The one check a card holding its own list cannot pass."""
        text = overview.overview_card(
            [_command("zzz_unheard_of", "A command this repository has never named")],
            hubs=(), is_admin=False,
        ).text()
        self.assertIn("`/zzz_unheard_of` — A command this repository has never named", text)

    def test_a_group_lists_its_leaves(self):
        text = overview.overview_card(
            [_group("stall", "Your market stall", "board", "buy")], hubs=(), is_admin=False,
        ).text()
        self.assertIn("📂 Command groups", text)
        self.assertIn("board · buy", text)

    def test_a_hub_is_a_panel_and_the_daily_five_come_first(self):
        commands = [_command("menu", "Every hub"), _command("world", "The world"),
                    _command("hunt", "Hunt"), _command("explore", "Explore")]
        text = overview.overview_card(commands, hubs=("world",), daily=("explore", "hunt"),
                                      is_admin=False).text()
        self.assertLess(text.index("⚡ Every day"), text.index("🧭 Panels"))
        self.assertLess(text.index("`/explore`"), text.index("`/hunt`"),
                        "the daily five are in the menu's order, not the tree's")
        panels = text[text.index("🧭 Panels"):text.index("✳️ Commands")]
        self.assertIn("`/world`", panels)
        self.assertNotIn("`/menu`", panels)

    def test_a_description_discord_cut_mid_word_ends_on_a_word(self):
        cut = ("The cultivation sheet, and four pages for what you are doing: meditate, temper the body, walk the pa")
        self.assertEqual(len(cut), overview.DESCRIPTION_LIMIT)
        text = overview.overview_card([_command("cultivation", cut)], hubs=("cultivation",)).text()
        self.assertIn("temper the body, walk the…", text)
        self.assertNotIn("walk the pa", text)

    def test_admin_is_shown_only_to_an_administrator(self):
        commands = [_command("admin", "The GM's panel"), _command("menu", "Every hub")]
        self.assertNotIn("/admin", overview.overview_card(commands, hubs=(), is_admin=False).text())
        self.assertIn("/admin", overview.overview_card(commands, hubs=(), is_admin=True).text())


class TheHandlerReadsTheRealSurface(unittest.TestCase):
    def test_the_live_tree_is_non_empty(self):
        self.assertGreater(len(_live_commands()), 30, "the surface read found nothing; the gate is broken, not the tree")

    def test_every_registered_command_is_on_the_card(self):
        commands = _live_commands()
        text, _ = _handler_text(commands, admin=True, character={})
        for command in commands:
            with self.subTest(command=command.name):
                # assertTrue rather than assertIn: the haystack is the whole card.
                self.assertTrue(f"`/{command.name}`" in text, f"/{command.name} is registered and not on the card")

    def test_the_daily_five_are_registered_with_it(self):
        text, _ = _handler_text(_live_commands(), admin=False)
        every_day = text[text.index("⚡ Every day"):text.index("🧭 Panels")]
        for name in surface.DAILY_ACTIONS:
            with self.subTest(name=name):
                self.assertIn(f"`/{name}`", every_day)

    def test_a_player_is_not_shown_admin(self):
        text, _ = _handler_text(_live_commands(), admin=False)
        self.assertNotIn("`/admin`", text)

    def test_every_hub_is_listed_as_a_panel(self):
        text, _ = _handler_text(_live_commands(), admin=False)
        panels = text[text.index("🧭 Panels"):text.index("✳️ Commands")]
        for definition in hubs.REGISTERED_HUBS:
            if definition.name == "admin":
                continue
            with self.subTest(hub=definition.name):
                self.assertIn(f"`/{definition.name}`", panels)


class EachPlayerSeesWhatIsTheirs(unittest.TestCase):
    """The four per-player rules, each asking the panels' own answer."""

    def test_begin_heads_the_card_for_somebody_with_no_character(self):
        text, _ = _handler_text(_live_commands(), admin=False, character={})
        self.assertIn("🌱 Start here", text)
        self.assertLess(text.index("`/begin`"), text.index("⚡ Every day"))

    def test_begin_is_never_shown_to_somebody_with_a_character(self):
        text, _ = _handler_text(_live_commands(), admin=False)
        self.assertNotIn("`/begin`", text)
        self.assertNotIn("Start here", text)

    def test_a_hub_the_menu_leaves_off_is_left_off_and_named(self):
        text, _ = _handler_text(_live_commands(), admin=False, hidden_hubs={"combat": 1, "abode": 4})
        panels = text[text.index("🧭 Panels"):text.index("✳️ Commands")]
        self.assertNotIn("`/combat`", panels)
        self.assertNotIn("`/abode`", panels)
        self.assertIn("`/world`", panels)
        not_yet = text[text.index("🔒 Not yet"):]
        self.assertIn("/combat", not_yet)
        self.assertIn("/abode", not_yet)

    def test_a_held_back_subcommand_is_left_off_and_counted(self):
        text, _ = _handler_text(_live_commands(), admin=False,
                                held={"/stall open": 1, "/stall list": 1, "/stall close": 1})
        groups = text[text.index("📂 Command groups"):text.index("🔒 Not yet")]
        stall = groups[groups.index("`/stall`"):].split("\n", 2)[1]
        self.assertNotIn("open", stall.split(" · "))
        self.assertIn("board", stall)
        self.assertIn("3 subcommands", text[text.index("🔒 Not yet"):])

    def test_a_group_with_nothing_open_is_left_off_and_named(self):
        held = {"/flame status": 1, "/flame capture": 1, "/flame refine": 1, "/flame bind": 1}
        text, _ = _handler_text(_live_commands(), admin=False, held=held)
        self.assertNotIn("`/flame`", text)
        self.assertIn("/flame", text[text.index("🔒 Not yet"):])

    def test_another_paths_command_is_absent_and_uncounted(self):
        text, _ = _handler_text(_live_commands(), admin=False,
                                hidden={"/stall open": hubs.NOT_YOUR_PATH, "/stall board": "not here"})
        groups = text[text.index("📂 Command groups"):]
        stall = groups[groups.index("`/stall`"):].split("\n", 2)[1]
        self.assertNotIn("open", stall.split(" · "))
        self.assertIn("board", stall, "a padlock for where you stand is not this card's business")
        self.assertNotIn("🔒 Not yet", text, "a road nobody can walk to is not counted")

    def test_a_provider_that_fails_hides_nothing(self):
        def broken(interaction):
            raise RuntimeError("the curriculum is away")
        with patch.object(hubs, "_MENU_SHAPE", broken), \
             patch.object(hubs, "_NOT_YET_UNLOCKED", broken), \
             patch.object(hubs, "_HIDDEN_ACTIONS", broken), \
             patch.object(overview, "DB", _DB({"user_id": 1})):
            interaction = _interaction(_live_commands(), admin=False)
            asyncio.run(surface.ACTIONS.handler_for(surface.ACTIONS.root("commands"))(interaction))
        text = cards.card_text(interaction.response.sent["view"])
        for definition in hubs.REGISTERED_HUBS:
            if definition.name != "admin":
                self.assertTrue(f"`/{definition.name}`" in text, f"/{definition.name} was hidden by a failed lookup")
        self.assertNotIn("🔒 Not yet", text)

    def test_a_character_read_that_fails_does_not_offer_begin(self):
        text, _ = _handler_text(_live_commands(), admin=False, character=RuntimeError("database is away"))
        self.assertNotIn("`/begin`", text)


if __name__ == "__main__":
    unittest.main()
