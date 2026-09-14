"""`/tribute`: the world's own gift, and the three things Python owns about it.

The gift began as `/vote`, a thank-you for voting the server up a listing site,
and was shaped around Top.gg's API and then Discadia's before both were dropped.
What is left involves nothing outside this deployment, which removes the whole
category of test this file used to carry - there is no URL to validate, no site
name to bound, and nothing taken on trust because there is nothing to verify.

Four properties are still Python's, and none of them is the reward arithmetic —
that is the engine's (`support.vote_claim`,
`go_core/internal/game/support_actions_test.go`):

1. `/tribute` is a *registered* root, because a daily gift nobody can reach is
   not a gift;
2. the command reads what was paid off the engine's receipt rather than
   computing it, and the claim button belongs to the cultivator who opened it;
3. the cadence players are told is the one the engine actually enforces;
4. the name it greets a player by is theirs, and cannot reach into the message.
"""
from __future__ import annotations

import ast
import importlib
import os
import re
import unittest
from unittest.mock import patch

from tests.support import PROJECT_ROOT

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}

SUPPORT = (PROJECT_ROOT / "app" / "bot" / "commands" / "support.py").read_text(encoding="utf-8")
SURFACE = (PROJECT_ROOT / "app" / "bot" / "surface.py").read_text(encoding="utf-8")
SUPPORT_GO = (PROJECT_ROOT / "go_core" / "internal" / "game" / "support_actions.go").read_text(encoding="utf-8")


class TributeIsARootAPlayerCanType(unittest.TestCase):
    def test_it_is_registered_with_discord_rather_than_buried_in_a_hub(self):
        # Only a handful of roots are on the tree (see register_command_surface);
        # everything else is hub metadata. A gift four taps into a panel is a
        # gift most players never find, so /tribute is one of the few.
        self.assertIn('"menu", "tribute"', SURFACE)
        self.assertIn("from .commands import support as _commands_support", SURFACE)
        with patch.dict(os.environ, ENV):
            surface = importlib.import_module("app.bot.surface")
            self.assertEqual(surface.ACTIONS.root("tribute").name, "tribute")


class NothingOutsideTheWorldIsInvolved(unittest.TestCase):
    """The whole point of the rewrite: no listing, no key, no endpoint.

    This is the test that fails if somebody reintroduces a site. The command
    took a URL and a site name from the operator's environment for most of its
    life, and a half-restored version of that is how it would come back.
    """

    def test_the_command_reads_no_listing_configuration(self):
        for gone in ("vote_site_url", "vote_site_name", "VOTE_SITE_URL", "VOTE_SITE_NAME"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, SUPPORT)

    def test_those_settings_no_longer_exist_at_all(self):
        with patch.dict(os.environ, ENV):
            from app.ops.config import Settings

            settings = Settings.from_env()
            for gone in ("vote_site_url", "vote_site_name"):
                with self.subTest(gone=gone):
                    self.assertFalse(hasattr(settings, gone), f"{gone} survived on Settings")

    def test_the_engine_is_still_asked_by_its_old_operation_names(self):
        # Deliberate: `support.vote_*` and the `support_vote` cooldown key are
        # written into domain_events and cooldowns. Renaming them to match the
        # command would orphan those rows, or hand every player a free claim.
        tree = ast.parse(SUPPORT)
        operations = {
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"action", "authoritative_action"}
            and node.args and isinstance(node.args[0], ast.Constant)
        }
        self.assertEqual(operations, {"support.vote_status", "support.vote_claim", "support.weekend"})
        self.assertIn('budget_refusal_line(interaction.user.id, "vote_claim")', SUPPORT)


class TheGiftIsTheEnginesToDecide(unittest.TestCase):
    def test_the_command_computes_no_reward_of_its_own(self):
        for read in ('result.get("amount")', 'result.get("currency")', 'result.get("balance")',
                     'result.get("item_id")', 'result.get("item_quantity")',
                     'status.get("remaining_seconds")', 'status.get("claimable")',
                     'status.get("amount")', 'status.get("item_id")',
                     'weekend.get("weekend")', 'weekend.get("closes_unix")'):
            self.assertIn(read, SUPPORT)

    def test_the_button_is_owner_gated_and_spends_the_shared_budget(self):
        self.assertIn("async def interaction_check", SUPPORT)
        self.assertIn("!= self.owner_id", SUPPORT)

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

    def test_someone_with_no_cultivator_yet_is_told_what_one_would_get_them(self):
        # The tribute is a reason to begin, so the person who has not begun is
        # exactly who should read about it rather than be turned away.
        body = SUPPORT[SUPPORT.index("async def tribute("):]
        self.assertIn("DB.get_character(interaction.user.id) is None", body)
        called = {
            node.func.id
            for node in ast.walk(ast.parse(SUPPORT))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertNotIn("require_character", called)


class ThePlayerIsGreetedByTheirOwnName(unittest.TestCase):
    """A nickname is text somebody else chose, arriving in our message."""

    def setUp(self):
        with patch.dict(os.environ, ENV):
            self.support = importlib.import_module("app.bot.commands.support")

    def test_the_server_nickname_is_what_is_used(self):
        from types import SimpleNamespace

        self.assertEqual(self.support._addressed(SimpleNamespace(display_name="Elder Li")), "Elder Li")

    def test_a_nickname_cannot_reach_into_the_rest_of_the_line(self):
        # Without escaping, a cultivator called "**" bolds everything after
        # their name in every message the gift is announced in.
        from types import SimpleNamespace

        escaped = self.support._addressed(SimpleNamespace(display_name="**Elder** _Li_"))
        self.assertNotIn("**Elder**", escaped)
        self.assertIn("Elder", escaped)

    def test_a_missing_name_still_addresses_somebody(self):
        from types import SimpleNamespace

        self.assertEqual(self.support._addressed(SimpleNamespace(display_name="")), "Cultivator")
        self.assertEqual(self.support._addressed(SimpleNamespace()), "Cultivator")

    def test_the_receipt_greets_them_when_there_is_a_name_and_not_when_there_is_not(self):
        paid = {"amount": 20, "currency": "low_spirit_stone", "balance": 20}
        self.assertIn("Good work, **Elder Li**", self.support._gift_line(paid, name="Elder Li"))
        self.assertNotIn("Good work", self.support._gift_line(paid))


class TheCadencePlayersAreToldIsTheOneTheEngineEnforces(unittest.TestCase):
    """The wait is a Go constant; the promise is Python copy. They can drift.

    Nothing but this test connects them, and a player told "every 12h" by a bot
    that refuses them for 24 has been lied to by the software.
    """

    def engine_cooldown_hours(self) -> int:
        match = re.search(r"supportVoteCooldownSeconds int64 = (\d+) \* 60 \* 60", SUPPORT_GO)
        self.assertIsNotNone(match, "supportVoteCooldownSeconds is no longer written as <hours> * 60 * 60")
        return int(match.group(1))

    def test_the_engine_meters_the_gift_twice_a_day(self):
        self.assertEqual(
            self.engine_cooldown_hours(), 12,
            "the tribute cooldown moved - docs/CONFIGURATION.md and README.md say the "
            "number out loud too, and so does the command",
        )

    def test_the_command_quotes_that_same_number_to_players(self):
        hours = self.engine_cooldown_hours()
        stated = {int(value) for value in re.findall(r"\*\*(\d+)h\*\*", SUPPORT)}
        self.assertTrue(stated, "/tribute no longer tells a player how often the gift renews")
        self.assertEqual(
            stated, {hours},
            f"/tribute promises {sorted(stated)}h but the engine enforces {hours}h",
        )


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
        self.assertIn("/tribute", text)

    def test_a_restart_inside_the_same_window_says_nothing(self):
        state, text = self.announce(self.OPEN, "2026-06-12:open")
        self.assertEqual(state, "2026-06-12:open")
        self.assertIsNone(text, "the weekend was announced twice")

    def test_the_close_is_only_said_to_a_server_that_heard_the_opening(self):
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

    def test_the_worker_always_runs_now_that_nothing_configures_it(self):
        bot_source = (PROJECT_ROOT / "app" / "bot" / "bot.py").read_text(encoding="utf-8")
        self.assertIn("self.weekend_gift_task = asyncio.create_task(self.weekend_gift_worker())", bot_source)
        # It used to start only when a listing URL was set. There is no listing.
        self.assertNotIn("if SETTINGS.vote_site_url:", bot_source)
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
