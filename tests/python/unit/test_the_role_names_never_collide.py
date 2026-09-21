"""Three generated role names, and no two may ever be the same (v1.0.11).

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

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
