"""The server's channel layout and the generated role names.

Merged from:

test_category_order.py — The order a member reads down the channel list, stated once (v1.0.0-rc.59).

Categories were never positioned - no `position=`, no `.edit(position=`, no
`.move(` anywhere under `app/` - so they landed in the call order of the
`ensure_*` helpers, appended at the bottom of the guild by Discord. Nothing in
the tree said what the order should be, which means nothing could be wrong
about it and nothing could be right either.


test_the_role_names_never_collide.py — Three generated role names, and no two may ever be the same (v1.0.11).

The bot generates one role name per gate:

    _realm_access_role_name(world)      "Xianxia • <world>"          reached that world
    realm_presence_role_name(world)     "Xianxia • <display_name>"   standing in its capital
    CULTIVATOR_ROLE_NAME                "Xianxia • Cultivator"       has ever played

The first two **can already collide by construction**: `realm_presence_role_name`
falls back to the bare `world_name` when a hub carries no `display_name`, so a
fifth realm hub written without one would generate the same string twice, and
`discord.utils.get(guild.roles, name=...)` would hand both gates the same role.
Nothing would error. The access gate would start following the character's
location, and a cultivator who walked out of a capital would lose sight of that
world's news feed - a channel they had earned.

`docs/TODO.md` recorded that trap when it planned the third name and said
adding one is the moment to gate it. This is that gate, and it is written to
grow: it walks whatever `REALM_HUBS` carries, so a fifth hub fails here the day
it is added rather than the day somebody notices a feed disappearing.

`Cultivator` is deliberately not a world, and the reverse direction is held too:
no hub may be named or displayed as one.

"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch


# --- from test_category_order.py ---

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


# --- from test_the_role_names_never_collide.py ---

def _runtime():
    with patch.dict(os.environ, ENV):
        import importlib

        return importlib.import_module("app.bot.runtime")


class EveryGeneratedRoleNameIsItsOwn(unittest.TestCase):
    def setUp(self):
        from app.rules.realm_hubs import REALM_HUBS, realm_presence_role_name

        self.runtime = _runtime()
        self.hubs = REALM_HUBS
        self.presence = realm_presence_role_name
        self.access = self.runtime._realm_access_role_name

    def test_there_are_hubs_to_check(self):
        """Every assertion below is vacuous on an empty roster."""
        self.assertGreaterEqual(len(self.hubs), 4, "the realm hubs are missing; the gate is broken, not the tree")

    def test_no_two_generated_names_are_the_same(self):
        names: dict[str, str] = {self.runtime.CULTIVATOR_ROLE_NAME: "the cultivator role"}
        for world in self.hubs:
            for label, name in ((f"{world} access", self.access(world)),
                                (f"{world} presence", self.presence(world))):
                owner = names.get(name)
                self.assertIsNone(owner, (
                    f"{label} generates {name!r}, which is already {owner}. Two gates resolving to "
                    "one role is silent: discord.utils.get hands both the same object, so the "
                    "access gate starts following the character's location and a cultivator who "
                    "leaves a capital loses that world's news feed"))
                names[name] = label

    def test_a_hub_with_no_display_name_is_what_would_collide(self):
        """The mechanism, driven rather than described.

        A hub whose `display_name` is missing makes `realm_presence_role_name`
        fall back to `world_name` - which is exactly what
        `_realm_access_role_name` returns. This is the shape the gate above
        exists to catch, and asserting it here is what says the gate can see it.
        """
        from app.rules import realm_hubs

        with patch.dict(realm_hubs.REALM_HUBS, {"Unnamed World": {"min_realm_index": 0}}, clear=False):
            self.assertEqual(
                realm_hubs.realm_presence_role_name("Unnamed World"),
                self.access("Unnamed World"),
                "the fallback no longer collides, so the gate above is guarding nothing")

    def test_no_hub_is_called_cultivator(self):
        reserved = self.runtime.CULTIVATOR_ROLE_NAME.split("•")[-1].strip().casefold()
        for world, hub in self.hubs.items():
            for value in (world, str(hub.get("display_name") or "")):
                self.assertNotEqual(value.strip().casefold(), reserved, (
                    f"{world!r} is named after the reserved role {reserved!r}"))


if __name__ == "__main__":
    unittest.main()
