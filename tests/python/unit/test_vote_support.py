"""`/vote`: the listing link, and the one grant the engine cannot verify.

A vote on Top.gg or DISBOARD happens on someone else's website and this
deployment publishes nothing an inbound webhook could reach, so the claim is
taken on trust. That makes three Python-owned properties worth holding, and
none of them is the reward arithmetic — that is the engine's (`support.vote_claim`,
`go_core/internal/game/support_actions_test.go`):

1. the operator's URL is validated before it is ever printed into a channel;
2. `/vote` is a *registered* root, because a command a player cannot type is
   no use as a link to a listing site;
3. the command reads the gift off the engine's receipt rather than computing
   one, and the claim button belongs to the cultivator who opened it.
"""
from __future__ import annotations

import ast
import importlib
import os
import re
import unittest
from unittest.mock import patch

from tests.support import PROJECT_ROOT

from app.ops.config import _vote_site_name, _vote_site_url

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}

SUPPORT = (PROJECT_ROOT / "app" / "bot" / "commands" / "support.py").read_text(encoding="utf-8")
SURFACE = (PROJECT_ROOT / "app" / "bot" / "surface.py").read_text(encoding="utf-8")


class TheOperatorsListingURLIsValidated(unittest.TestCase):
    def test_an_https_url_is_kept_and_an_absent_one_disables_the_command(self):
        self.assertEqual(_vote_site_url("https://top.gg/servers/123"), "https://top.gg/servers/123")
        self.assertEqual(_vote_site_url("  https://disboard.org/server/123  "), "https://disboard.org/server/123")
        for absent in (None, "", "   "):
            with self.subTest(absent=absent):
                self.assertEqual(_vote_site_url(absent), "")

    def test_anything_that_is_not_one_https_url_fails_at_startup(self):
        # The value is printed into a public channel, so a typo is caught at
        # boot rather than posted to every player.
        for bad in ("top.gg/servers/123", "http://top.gg", "javascript:alert(1)",
                    "https://top.gg/x https://evil.example", "https://" + "a" * 300):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    _vote_site_url(bad)

    def test_the_site_name_is_trimmed_bounded_and_defaulted(self):
        # Deliberately names no site: a default that ships one brand labels
        # every operator's listing with it until they notice.
        self.assertEqual(_vote_site_name(None), "the server listing")
        self.assertEqual(_vote_site_name("   "), "the server listing")
        self.assertEqual(_vote_site_name(" DISBOARD "), "DISBOARD")
        self.assertEqual(_vote_site_name("Top\n.gg"), "Top .gg")
        self.assertEqual(len(_vote_site_name("x" * 90)), 40)


class TheCadencePlayersAreToldIsTheOneTheEngineEnforces(unittest.TestCase):
    """The wait is a Go constant; the promise is Python copy. They can drift.

    The cadence is not a balance dial - it is whatever the listing site resets
    a vote on (Discadia, a day). So it moves when the operator changes listing,
    and when it moves the three places /vote states it in have to move with it.
    Nothing but this test connects them, and a player told "every 24h" by a bot
    that refuses them for 48 has been lied to by the software.
    """

    SUPPORT_GO = (PROJECT_ROOT / "go_core" / "internal" / "game" / "support_actions.go").read_text(encoding="utf-8")

    def engine_cooldown_hours(self) -> int:
        match = re.search(r"supportVoteCooldownSeconds int64 = (\d+) \* 60 \* 60", self.SUPPORT_GO)
        self.assertIsNotNone(match, "supportVoteCooldownSeconds is no longer written as <hours> * 60 * 60")
        return int(match.group(1))

    def test_the_engine_meters_the_gift_at_discadias_cadence(self):
        self.assertEqual(
            self.engine_cooldown_hours(), 24,
            "the vote cooldown no longer matches Discadia's 24h vote reset - if the "
            "server has moved to another listing this is the right place to change it, "
            "and docs/CONFIGURATION.md and README.md say the number out loud too",
        )

    def test_the_command_quotes_that_same_number_to_players(self):
        hours = self.engine_cooldown_hours()
        promised = set(re.findall(r"renews every \*\*(\d+)h\*\*|pays every \*\*(\d+)h\*\*", SUPPORT))
        stated = {int(value) for pair in promised for value in pair if value}
        self.assertTrue(stated, "/vote no longer tells a player how often the gift renews")
        self.assertEqual(
            stated, {hours},
            f"/vote promises {sorted(stated)}h but the engine enforces {hours}h",
        )


class VoteIsARootAPlayerCanType(unittest.TestCase):
    def test_it_is_registered_with_discord_rather_than_buried_in_a_hub(self):
        # Only a handful of roots are on the tree (see register_command_surface);
        # everything else is hub metadata. A listing link is worthless if the
        # way to it is four taps into a panel, so /vote is one of the few.
        self.assertIn('"menu", "vote"', SURFACE)
        self.assertIn("from .commands import support as _commands_support", SURFACE)
        with patch.dict(os.environ, ENV):
            surface = importlib.import_module("app.bot.surface")
            self.assertEqual(surface.ACTIONS.root("vote").name, "vote")


class TheClaimIsTheEnginesToDecide(unittest.TestCase):
    def test_the_command_computes_no_reward_of_its_own(self):
        tree = ast.parse(SUPPORT)
        operations = {
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"action", "authoritative_action"}
            and node.args and isinstance(node.args[0], ast.Constant)
        }
        self.assertEqual(operations, {"support.vote_status", "support.vote_claim", "support.weekend"})
        # The gift's size, its currency and the twelve-hour wait are all read
        # back off the engine's result; nothing here multiplies anything.
        for read in ('result.get("amount")', 'result.get("currency")', 'result.get("balance")',
                     'result.get("item_id")', 'result.get("item_quantity")',
                     'status.get("remaining_seconds")', 'status.get("claimable")',
                     'status.get("amount")', 'status.get("item_id")',
                     'weekend.get("weekend")', 'weekend.get("closes_unix")'):
            self.assertIn(read, SUPPORT)

    def test_the_button_is_owner_gated_and_spends_the_shared_budget(self):
        self.assertIn("async def interaction_check", SUPPORT)
        self.assertIn("!= self.owner_id", SUPPORT)
        self.assertIn('budget_refusal_line(interaction.user.id, "vote_claim")', SUPPORT)

    def test_a_claim_carries_a_unique_action_id_so_a_retry_cannot_pay_twice(self):
        self.assertIn('action_id=f"discord:{interaction.id}:support.vote_claim"', SUPPORT)

    def test_the_gift_line_names_the_item_only_when_there_is_one(self):
        # A tier material the catalogue does not carry is dropped by the engine
        # rather than granted as a phantom, so the receipt must read correctly
        # with no item at all - and must not invent a name for one.
        from app.bot.commands.support import _gift_line

        with_item = _gift_line({"amount": 20, "currency": "low_spirit_stone", "balance": 20,
                                "item_id": "spirit_iron", "item_quantity": 1})
        self.assertIn("Spirit Iron", with_item)
        without = _gift_line({"amount": 20, "currency": "low_spirit_stone", "balance": 20,
                              "item_id": "", "item_quantity": 0})
        self.assertNotIn("×", without.split("reaches you")[0].split("Low-Grade Spirit Stone")[-1])
        self.assertIn("20", without)

    def test_the_weekend_line_is_the_engines_to_declare(self):
        from app.bot.commands.support import _gift_line

        plain = _gift_line({"amount": 10, "currency": "low_spirit_stone", "balance": 10})
        self.assertNotIn("Doubled", plain)
        doubled = _gift_line({"amount": 20, "currency": "low_spirit_stone", "balance": 20,
                              "weekend": True, "multiplier": 2})
        self.assertIn("Doubled", doubled)

    def test_the_link_is_shown_to_someone_who_has_no_cultivator_yet(self):
        # The person a listing site just brought in has no character. Turning
        # them away before they have read the link would be the command
        # defeating its own purpose, so only the *gift* needs a cultivator.
        body = SUPPORT[SUPPORT.index("async def vote("):]
        self.assertIn("DB.get_character(interaction.user.id) is None", body)
        self.assertLess(body.index("Vote here:"), body.index("DB.get_character"))
        called = {
            node.func.id
            for node in ast.walk(ast.parse(SUPPORT))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertNotIn("require_character", called)


class TheWeekendAnnouncesItselfOnce(unittest.TestCase):
    """The doubled weekend is only worth running if players hear about it.

    The window is the engine's; what is tested here is the half Python owns -
    saying it once, saying the end only to a server that heard the beginning,
    and surviving a restart without repeating itself.
    """

    def setUp(self):
        with patch.dict(os.environ, ENV):
            self.announce = importlib.import_module("app.bot.bot").weekend_announcement

    OPEN = {"weekend": True, "window_key": "2026-06-12", "closes_unix": 1781308800}
    SHUT = {"weekend": False, "window_key": "2026-06-19", "closes_unix": 1781913600}

    def test_an_open_window_is_announced_and_remembered(self):
        state, text = self.announce(self.OPEN, "")
        self.assertEqual(state, "2026-06-12:open")
        self.assertIn("doubled", text)
        self.assertIn("<t:1781308800:R>", text)
        self.assertIn("/vote", text)

    def test_a_restart_inside_the_same_window_says_nothing(self):
        state, text = self.announce(self.OPEN, "2026-06-12:open")
        self.assertEqual(state, "2026-06-12:open")
        self.assertIsNone(text, "the weekend was announced twice")

    def test_the_close_is_only_said_to_a_server_that_heard_the_opening(self):
        # The server was told this window opened, so it is told it ended.
        state, text = self.announce({**self.SHUT, "window_key": "2026-06-12"}, "2026-06-12:open")
        self.assertEqual(state, "2026-06-12:closed")
        self.assertIn("ended", text)
        # A first run on a quiet Tuesday must not announce the end of a
        # weekend nobody was told about.
        state, text = self.announce(self.SHUT, "")
        self.assertEqual(state, "2026-06-19:closed")
        self.assertIsNone(text)

    def test_the_next_weekend_is_a_new_window(self):
        _, text = self.announce({**self.OPEN, "window_key": "2026-06-19"}, "2026-06-12:closed")
        self.assertIsNotNone(text, "the following weekend was swallowed by the last one's marker")

    def test_an_unreadable_window_changes_nothing(self):
        self.assertEqual(self.announce({}, "2026-06-12:open"), ("", None))

    def test_the_worker_is_started_only_with_a_listing_and_cancelled_on_close(self):
        bot_source = (PROJECT_ROOT / "app" / "bot" / "bot.py").read_text(encoding="utf-8")
        self.assertIn("if SETTINGS.vote_site_url:", bot_source)
        self.assertIn("self.weekend_gift_task = asyncio.create_task(self.weekend_gift_worker())", bot_source)
        self.assertIn('"route_audit_task", "weekend_gift_task"):', bot_source)
        # The engine owns the window; the worker must not compute its own.
        worker = bot_source[bot_source.index("async def announce_weekend_gift"):bot_source.index("async def weekend_gift_worker")]
        self.assertIn('ENGINE.action("support.weekend"', worker)
        for forbidden in ("weekday()", "datetime.now", "Friday"):
            self.assertNotIn(forbidden, worker)
        # An unsent announcement is retried, not marked delivered.
        self.assertIn("return", worker.split("Could not announce the weekend gift")[1][:200])


if __name__ == "__main__":
    unittest.main()
