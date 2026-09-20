"""The order a member reads down the channel list, stated once (v1.0.0-rc.59).

Categories were never positioned - no `position=`, no `.edit(position=`, no
`.move(` anywhere under `app/` - so they landed in the call order of the
`ensure_*` helpers, appended at the bottom of the guild by Discord. Nothing in
the tree said what the order should be, which means nothing could be wrong
about it and nothing could be right either.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _setup():
    with patch.dict(os.environ, ENV):
        import importlib

        return importlib.import_module("app.bot.admin.server_setup")


class TheCategoriesHaveOneOrder(unittest.TestCase):
    def setUp(self):
        self.setup = _setup()

    def test_the_order_is_every_category_setup_makes(self):
        self.assertEqual(set(self.setup.CATEGORY_ORDER), set(self.setup.CREATED_CATEGORIES))
        self.assertEqual(len(self.setup.CATEGORY_ORDER), len(set(self.setup.CATEGORY_ORDER)),
                         "a category listed twice would fight itself for a position")
        self.assertGreaterEqual(len(self.setup.CATEGORY_ORDER), 8)

    def test_the_retired_category_is_ordered_by_nothing(self):
        """`SERVER_BASE_CATEGORY` is not created any more, so positioning it
        would be positioning something that should be being emptied."""
        self.assertNotIn(self.setup.SERVER_BASE_CATEGORY, self.setup.CATEGORY_ORDER)
        self.assertNotIn(self.setup.SERVER_BASE_CATEGORY, self.setup.CREATED_CATEGORIES)

    def test_the_retired_category_is_still_torn_down(self):
        """And that is the teardown gate read backwards: a category Setup
        *stops* making is one every existing server still has, and only
        teardown can remove it."""
        source = (self.setup.__file__ and __import__("pathlib").Path(self.setup.__file__).read_text(encoding="utf-8"))
        loop = source.split("for name in (")[1].split("):")[0]
        self.assertIn("SERVER_BASE_CATEGORY", loop)

    def test_every_base_channel_names_a_bucket_that_has_a_category(self):
        with patch.dict(os.environ, ENV):
            import importlib

            messages = importlib.import_module("app.bot.admin.channel_messages")
        for name, spec in messages.BASE_CHANNEL_SPECS.items():
            with self.subTest(channel=name):
                self.assertIn(spec.category, self.setup.BASE_CATEGORY_NAMES,
                              f"#{name} names a bucket no category answers to")
                self.assertIn(self.setup.BASE_CATEGORY_NAMES[spec.category], self.setup.CATEGORY_ORDER)

    def test_every_bucket_is_used(self):
        """A bucket nothing sits in is a category Setup would make and leave
        empty, which teardown would then refuse to delete."""
        with patch.dict(os.environ, ENV):
            import importlib

            messages = importlib.import_module("app.bot.admin.channel_messages")
        used = {spec.category for spec in messages.BASE_CHANNEL_SPECS.values()}
        self.assertEqual(used, set(self.setup.BASE_CATEGORY_NAMES),
                         "a bucket with no channels in it")


if __name__ == "__main__":
    unittest.main()
