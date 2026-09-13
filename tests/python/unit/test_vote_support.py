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
        self.assertEqual(operations, {"support.vote_status", "support.vote_claim"})
        # The gift's size, its currency and the twelve-hour wait are all read
        # back off the engine's result; nothing here multiplies anything.
        for read in ('result.get("amount")', 'result.get("currency")', 'result.get("balance")',
                     'status.get("remaining_seconds")', 'status.get("claimable")'):
            self.assertIn(read, SUPPORT)

    def test_the_button_is_owner_gated_and_spends_the_shared_budget(self):
        self.assertIn("async def interaction_check", SUPPORT)
        self.assertIn("!= self.owner_id", SUPPORT)
        self.assertIn('budget_refusal_line(interaction.user.id, "vote_claim")', SUPPORT)

    def test_a_claim_carries_a_unique_action_id_so_a_retry_cannot_pay_twice(self):
        self.assertIn('action_id=f"discord:{interaction.id}:support.vote_claim"', SUPPORT)

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


if __name__ == "__main__":
    unittest.main()
