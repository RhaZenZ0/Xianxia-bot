"""`/vote`: the listing link, and the claim that is now checked before it pays.

A vote happens on someone else's website. This deployment still publishes
nothing an inbound webhook could reach, but Top.gg answers the same question on
an outbound call, so since v1.0.0 the claim is verified when `TOPGG_TOKEN` is
set and taken on trust when it is not (`app/ops/topgg.py`, and
`tests/python/unit/test_topgg.py` for the check itself).

That makes four Python-owned properties worth holding here, and none of them is
the reward arithmetic — that is the engine's (`support.vote_claim`,
`go_core/internal/game/support_actions_test.go`):

1. the operator's URL is validated before it is ever printed into a channel;
2. `/vote` is a *registered* root, because a command a player cannot type is
   no use as a link to a listing site;
3. the command reads the gift off the engine's receipt rather than computing
   one, and the claim button belongs to the cultivator who opened it;
4. the verification refuses only a confident "no vote", and refusing leaves the
   player able to try again.
"""
from __future__ import annotations

import ast
import asyncio
import importlib
import os
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
        self.assertEqual(_vote_site_name(None), "Top.gg")
        self.assertEqual(_vote_site_name("   "), "Top.gg")
        self.assertEqual(_vote_site_name(" DISBOARD "), "DISBOARD")
        self.assertEqual(_vote_site_name("Top\n.gg"), "Top .gg")
        self.assertEqual(len(_vote_site_name("x" * 90)), 40)


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


class TheClaimIsCheckedBeforeItPays(unittest.TestCase):
    """The verification, from the button's side.

    `test_topgg.py` pins what the check answers; this pins what the button does
    with each answer. The asymmetry is the same one, seen from the other end: a
    confident NOT_VOTED is the only thing that stops a claim, and stopping a
    claim must leave the player able to make it.
    """

    def setUp(self):
        with patch.dict(os.environ, ENV):
            self.support = importlib.import_module("app.bot.commands.support")

    def press(self, check, *, engine_result=None):
        """Press the claim button against a given Top.gg verdict.

        Returns (interaction, view, engine_calls) so a test can assert on what
        the player saw, what the button became, and what the engine was asked.
        """
        from unittest.mock import AsyncMock, MagicMock

        view = self.support.VoteClaimView(owner_id=7, site="Top.gg")
        button = view.children[0]
        interaction = MagicMock()
        interaction.id = 99
        interaction.user.id = 7
        interaction.response.defer = AsyncMock()
        interaction.edit_original_response = AsyncMock()
        interaction.followup.send = AsyncMock()
        engine_calls = []

        async def authoritative_action(operation, actor, payload, **kwargs):
            engine_calls.append((operation, actor, payload))
            return {"result": engine_result or {
                "amount": 20, "currency": "low_spirit_stone", "balance": 20,
            }}

        with patch.object(self.support, "_vote_check", AsyncMock(return_value=check)), \
             patch.object(self.support, "budget_refusal_line", return_value=None), \
             patch.object(self.support.ENGINE, "authoritative_action", side_effect=authoritative_action):
            asyncio.run(self.support.VoteClaimView.claim(view, interaction, button))
        return interaction, view, engine_calls

    def said(self, interaction) -> str:
        return "\n".join(str(call.args[0]) for call in interaction.followup.send.call_args_list)

    def test_a_confirmed_vote_pays_and_records_that_it_was_verified(self):
        from app.ops.topgg import VOTED, VoteCheck

        interaction, view, calls = self.press(VoteCheck(VOTED, expires_unix=1))
        self.assertEqual(len(calls), 1, "a verified claim must reach the engine")
        operation, actor, payload = calls[0]
        self.assertEqual(operation, "support.vote_claim")
        self.assertEqual(actor, 7)
        self.assertIs(payload["verified"], True)
        self.assertIn("Thank you", self.said(interaction))
        self.assertTrue(view.children[0].disabled, "a spent claim must not stay pressable")

    def test_no_live_vote_refuses_without_asking_the_engine(self):
        from app.ops.topgg import NOT_VOTED, VoteCheck

        interaction, view, calls = self.press(VoteCheck(NOT_VOTED))
        self.assertEqual(calls, [], "a refused claim must never reach the engine")
        self.assertFalse(
            view.children[0].disabled,
            "the button must stay live: a player who pressed a moment too early "
            "has to be able to press again",
        )
        said = self.said(interaction)
        self.assertIn("Top.gg", said)
        self.assertIn("again", said, "a refusal must say what to do next")

    def test_an_outage_pays_but_is_not_recorded_as_verified(self):
        # The whole design rests on this: Top.gg being unreachable costs the
        # player nothing, and the receipt still tells the truth about it.
        from app.ops.topgg import UNCONFIGURED, UNKNOWN, VoteCheck

        for state in (UNKNOWN, UNCONFIGURED):
            with self.subTest(state=state):
                interaction, view, calls = self.press(VoteCheck(state))
                self.assertEqual(len(calls), 1, f"{state} must still pay the gift")
                self.assertIs(calls[0][2]["verified"], False)
                self.assertIn("Thank you", self.said(interaction))

    def test_the_interaction_is_acknowledged_before_the_round_trip(self):
        # Discord drops an interaction that is not answered within three
        # seconds, and the check is a call to someone else's website. Deferring
        # after it would make a slow Top.gg look like a dead button.
        from app.ops.topgg import VOTED, VoteCheck

        interaction, _, _ = self.press(VoteCheck(VOTED, expires_unix=1))
        interaction.response.defer.assert_awaited_once()
        source = SUPPORT[SUPPORT.index("async def claim("):]
        self.assertLess(
            source.index("response.defer()"), source.index("_vote_check("),
            "the defer must come before the Top.gg round trip",
        )

    def switched(self, verify: bool):
        """`SETTINGS` with the verification switch flipped.

        Settings is frozen - deliberately, it is read at import time all over
        the bot - so a test replaces the whole object rather than a field.
        """
        import dataclasses

        return patch.object(
            self.support, "SETTINGS",
            dataclasses.replace(self.support.SETTINGS, topgg_verify_votes=verify),
        )

    def test_verification_is_live_only_with_both_a_token_and_the_switch(self):
        from unittest.mock import PropertyMock

        for token, switch, expected in ((True, True, True), (True, False, False),
                                        (False, True, False), (False, False, False)):
            with self.subTest(token=token, switch=switch):
                with patch.object(type(self.support.TOPGG), "enabled", PropertyMock(return_value=token)), \
                     self.switched(switch):
                    self.assertEqual(self.support._verification_is_live(), expected)

    def test_the_check_is_skipped_entirely_when_the_switch_is_off(self):
        from unittest.mock import AsyncMock
        from app.ops.topgg import UNCONFIGURED

        with self.switched(False), \
             patch.object(self.support.TOPGG, "vote_state", AsyncMock()) as asked:
            check = asyncio.run(self.support._vote_check(7))
        asked.assert_not_awaited()
        self.assertEqual(check.state, UNCONFIGURED)


class TheMetricsWorkerFollowsTheToken(unittest.TestCase):
    def test_it_runs_only_with_a_token_and_is_cancelled_on_close(self):
        bot_source = (PROJECT_ROOT / "app" / "bot" / "bot.py").read_text(encoding="utf-8")
        self.assertIn("if SETTINGS.topgg_post_metrics and TOPGG.enabled:", bot_source)
        self.assertIn("self.topgg_metrics_task = asyncio.create_task(self.topgg_metrics_worker())", bot_source)
        self.assertIn('"weekend_gift_task", "topgg_metrics_task"):', bot_source)
        # The client holds a keep-alive pool, so shutdown has to close it.
        self.assertIn("await TOPGG.aclose()", bot_source)

    def test_the_count_is_discords_and_its_result_is_a_health_check(self):
        # The server count is the one number the engine cannot supply - it only
        # exists in discord.py - which is why this worker lives in the bot.
        bot_source = (PROJECT_ROOT / "app" / "bot" / "bot.py").read_text(encoding="utf-8")
        worker = bot_source[bot_source.index("async def post_topgg_metrics"):bot_source.index("async def topgg_metrics_worker")]
        self.assertIn("len(self.guilds)", worker)
        self.assertIn("TOPGG.post_metrics(server_count=", worker)
        self.assertIn('set_check("topgg_metrics", posted', worker)


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
        self.assertIn('"weekend_gift_task", "topgg_metrics_task"):', bot_source)
        # The engine owns the window; the worker must not compute its own.
        worker = bot_source[bot_source.index("async def announce_weekend_gift"):bot_source.index("async def weekend_gift_worker")]
        self.assertIn('ENGINE.action("support.weekend"', worker)
        for forbidden in ("weekday()", "datetime.now", "Friday"):
            self.assertNotIn(forbidden, worker)
        # An unsent announcement is retried, not marked delivered.
        self.assertIn("return", worker.split("Could not announce the weekend gift")[1][:200])


if __name__ == "__main__":
    unittest.main()
