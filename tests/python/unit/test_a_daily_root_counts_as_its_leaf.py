"""A daily root is counted as the leaf it is (v1.12.3).

`/forage` is a root whose body is one call to `/alchemy forage`'s handler
(v1.3.2), so the two doors are one action. The command tree recorded the root
as "/forage" and a hub press recorded the leaf as "/alchemy forage" - one
action counted as two commands, against `usage.py`'s promise of one path per
action. The other four roots share their leaf's path and were always right.

Driven through the tree's own `interaction_check` and through a hub press's
recorder with a counting fake, because the source reads correctly in both the
split and the joined version: the path is decided one call down.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _modules():
    with patch.dict(os.environ, ENV):
        surface = importlib.import_module("app.bot.surface")
        bot = importlib.import_module("app.bot.bot")
        usage = importlib.import_module("app.bot.usage")
        hubs = importlib.import_module("app.bot.hubs")
    return surface, bot, usage, hubs


class _Counting:
    def __init__(self):
        self.paths: list[str] = []

    async def record_command_use(self, path):
        self.paths.append(path)


async def _slash(bot, name, db):
    import discord

    async def none(*_a, **_k):
        return None

    interaction = SimpleNamespace(
        command=SimpleNamespace(qualified_name=name),
        user=SimpleNamespace(id=1),
        type=discord.InteractionType.application_command,
    )
    with patch.object(bot, "DB", db), patch.object(bot.maintenance, "refuse", none), patch.object(bot.seclusion, "refuse", none):
        allowed = await bot.GatedCommandTree.interaction_check(SimpleNamespace(), interaction)
    await asyncio.sleep(0.01)  # the counter is fired, not awaited
    return allowed


class AForageIsOneCommandAtEveryDoor(unittest.TestCase):
    def test_the_slash_command_and_the_hub_press_count_one_path(self):
        surface, bot, usage, hubs = _modules()
        db = _Counting()

        async def scenario():
            self.assertTrue(await _slash(bot, "forage", db))
            with patch.object(bot, "DB", db):
                usage.note(db, "/alchemy forage")  # what a hub press records
                await asyncio.sleep(0.01)

        asyncio.run(scenario())
        self.assertEqual(db.paths, ["/alchemy forage", "/alchemy forage"],
                         "the same action was counted under two names: %s" % db.paths)

    def test_each_daily_root_counts_as_its_hub_leaf(self):
        surface, bot, usage, hubs = _modules()
        for root, _hub, leaf in surface._DAILY_LEAVES:
            with self.subTest(root=root):
                db = _Counting()
                asyncio.run(_slash(bot, root, db))
                self.assertEqual(db.paths, [leaf])

    def test_a_path_that_is_no_alias_is_left_alone(self):
        _surface, _bot, usage, _hubs = _modules()
        self.assertEqual(usage.canonical("/sheet"), "/sheet")
        self.assertEqual(usage.canonical("  /sheet "), "/sheet")


if __name__ == "__main__":
    unittest.main()
