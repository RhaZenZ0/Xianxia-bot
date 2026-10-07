"""A panel refresh costs one database session (v1.31.2).

`LayoutHubView.refresh_status` runs after every hub press and on every open:
the hidden-action providers, the curriculum, the status card. Every `DB.get_x()`
each of them made opened its own engine session - open, one statement, close -
so a press on most hubs cost thirty-two sessions for thirty-two one-row reads,
three HTTP round trips apiece, before the Here line asked the engine what time
it was twelve times over. Measured, not read off the source: the calls are one
level down, inside `Database._connect`, and the source of every provider reads
correctly in both the fast and the slow version (rc.28's lesson).

What is held, per hub and for the menu's two reads:

- one connection for the whole refresh (`hubs.register_read_scope` hands
  `Database.reuse_connection` down; drop the registration and this prints 32);
- no commit inside it, because the memo of point reads the scope keeps is only
  honest while the scope writes nothing;
- the clock read at most twice - once for the black-market hide, once for the
  Here line - never once per circuit walker;
- a bounded number of statements and engine actions, so the per-NPC and
  per-table loops cannot creep back under a different name.

The counting proxy sits on `Database._open_connection`, the one door every read
opens through, and the engine is a fake answering canned rows: a found
`npc.status` row, so the registry fallbacks a missing row would fire do not
inflate the count the way they did in the first measurement.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import seed_character

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}
UID = 1456074443989188610

CANNED = {
    "world.clock": {"game_minute": 600, "scale": 4, "anchor_game_minute": 0, "anchor_real_ts": 0, "real_ts": 0, "seeded": True},
    "npc.at_location": {"rows": []},
    "npc.status": {"found": True, "npc_name": "Somebody", "status": "alive",
                   "current_location": "Greenriver Town", "home_location": "Greenriver Town"},
    "market.rows": {"rows": []},
    "trade.status": {},
    "cooldown.status": {},
    "cultivation.status": {"realm_index": 0, "stage": 1, "cultivation": 10, "cost": 100,
                           "odds": {"probability": 40, "modifier": 2, "tn": 12, "movers": []},
                           "insight_xp": 0, "pace": 10, "sessions_per_stage": 12, "multipliers": {}},
}

# The ceilings. Measured at 26 statements and 8 engine actions on the shipped
# content with the fake above; the slack is for a fuller character, not for a
# second loop.
MAX_STATEMENTS = 34
MAX_ENGINE_ACTIONS = 12
MAX_CLOCK_READS = 2


class _Counts:
    def __init__(self):
        self.connections = 0
        self.statements = 0
        self.commits = 0
        self.engine: dict[str, int] = {}

    def reset(self):
        self.__init__()


class _CountingConn:
    def __init__(self, inner, counts: _Counts):
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_counts", counts)

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def __setattr__(self, name, value):
        setattr(self._inner, name, value)

    async def execute(self, sql, params=()):
        self._counts.statements += 1
        return await self._inner.execute(sql, params)

    async def commit(self):
        self._counts.commits += 1
        return await self._inner.commit()


def _modules():
    with patch.dict(os.environ, ENV):
        surface = importlib.import_module("app.bot.surface")
        hubs = importlib.import_module("app.bot.hubs")
        runtime = importlib.import_module("app.bot.runtime")
        core = importlib.import_module("app.database.core")
    return surface, hubs, runtime, core


def _interaction():
    user = SimpleNamespace(id=UID, display_name="Tester", mention="@t",
                           guild_permissions=SimpleNamespace(administrator=False))
    return SimpleNamespace(user=user, guild=None, type=None, message=None, client=SimpleNamespace(),
                           command=None, channel_id=0, response=SimpleNamespace(is_done=lambda: False))


@contextlib.contextmanager
def _measured():
    """A real local bootstrap with one seeded character, the counting proxy on
    the connection and the fake engine; yields (surface, hubs, counts)."""
    surface, hubs, runtime, core = _modules()
    counts = _Counts()
    original_open = core.Database._open_connection

    @contextlib.asynccontextmanager
    async def counting_open(self):
        counts.connections += 1
        async with original_open(self) as db:
            yield _CountingConn(db, counts)

    async def fake_action(operation, actor_id, payload, **kwargs):
        counts.engine[operation] = counts.engine.get(operation, 0) + 1
        return json.loads(json.dumps(CANNED.get(operation, {})))

    with tempfile.TemporaryDirectory() as tmp:
        db = runtime.DB
        with patch.object(core.Database, "_open_connection", counting_open), \
             patch.object(db, "path", Path(tmp) / "panel.sqlite3"), \
             patch.object(db, "_go_transport", None), \
             patch.object(runtime.ENGINE, "action", fake_action), \
             patch.object(runtime.ENGINE, "authoritative_action", fake_action):
            async def boot():
                await db.init()
                await seed_character(
                    db, user_id=UID, discord_name="tester", name="Mu Tester", origin="Greenriver Town",
                    path="Sword Cultivator", spiritual_root="Common", concept="A measurement",
                    location="Greenriver Town", qi_max=50, vitality_max=30,
                    attributes={"body": 3, "spirit": 3, "insight": 3, "will": 3, "agility": 3, "presence": 3},
                )
            asyncio.run(boot())
            counts.reset()
            yield surface, hubs, counts


class APanelRefreshCostsOneSession(unittest.TestCase):
    def test_every_hub_refreshes_inside_one_connection(self):
        with _measured() as (surface, hubs, counts):
            measured = 0
            for definition in surface._HUB_DEFINITIONS:
                counts.reset()
                view = hubs.LayoutHubView(UID, definition, status_provider=surface._status_provider_for(definition))
                asyncio.run(view.refresh_status(_interaction()))
                measured += 1
                self.assertGreater(counts.statements, 0,
                                   f"the {definition.name} refresh ran no statement; the proxy is broken, not the tree")
                self.assertEqual(
                    counts.connections, 1,
                    f"a refresh of the {definition.name} hub opened {counts.connections} sessions; "
                    "the one-session scope (hubs.register_read_scope) is gone",
                )
                self.assertEqual(counts.commits, 0,
                                 f"a refresh of the {definition.name} hub committed; a refresh only reads")
                self.assertLessEqual(
                    counts.engine.get("world.clock", 0), MAX_CLOCK_READS,
                    f"a refresh of the {definition.name} hub read the clock {counts.engine.get('world.clock')} times; "
                    "a circuit walker is reading it again",
                )
                self.assertLessEqual(
                    counts.statements, MAX_STATEMENTS,
                    f"a refresh of the {definition.name} hub ran {counts.statements} statements",
                )
                self.assertLessEqual(
                    sum(counts.engine.values()), MAX_ENGINE_ACTIONS,
                    f"a refresh of the {definition.name} hub made {sum(counts.engine.values())} engine actions: {counts.engine}",
                )
            self.assertGreaterEqual(measured, 10, "fewer than ten hubs were measured; the surface read is broken")

    def test_the_menu_reads_inside_one_connection(self):
        with _measured() as (surface, hubs, counts):
            async def open_menu():
                # The two reads `surface.menu` and the hub's Menu button make,
                # inside the scope they both enter.
                async with surface.DB.reuse_connection():
                    await surface._menu_facts(_interaction())
                    await hubs.menu_shape(_interaction())
            asyncio.run(open_menu())
            self.assertGreater(counts.statements, 0, "the menu ran no statement; the proxy is broken, not the tree")
            self.assertEqual(counts.connections, 1, f"the menu opened {counts.connections} sessions")
            self.assertLessEqual(counts.engine.get("world.clock", 0), MAX_CLOCK_READS)

    def test_a_point_read_is_remembered_for_the_scope_and_forgotten_outside_it(self):
        with _measured() as (surface, hubs, counts):
            db = surface.DB

            async def inside():
                async with db.reuse_connection():
                    first = await db.get_character(UID)
                    second = await db.get_character(UID)
                    await db.get_sect_membership(UID)
                    await db.get_sect_membership(UID)
                    return first, second

            first, second = asyncio.run(inside())
            self.assertEqual(first, second)
            self.assertEqual(counts.statements, 2,
                             f"four point reads inside one scope ran {counts.statements} statements; the memo is gone")
            counts.reset()
            asyncio.run(db.get_character(UID))
            asyncio.run(db.get_character(UID))
            self.assertEqual(counts.statements, 2, "a read outside a scope must be exactly what it always was")

    def test_a_write_inside_the_scope_forgets_the_memo(self):
        with _measured() as (surface, hubs, counts):
            db = surface.DB

            async def read_write_read():
                async with db.reuse_connection() as conn:
                    before = await db.get_character(UID)
                    await conn.execute("UPDATE characters SET spirit_stones=spirit_stones+7 WHERE user_id=?", (UID,))
                    await conn.commit()
                    after = await db.get_character(UID)
                    return before, after

            before, after = asyncio.run(read_write_read())
            self.assertEqual(int(after["spirit_stones"]), int(before["spirit_stones"]) + 7,
                             "a read after a write inside the scope answered from the memo")

    def test_the_scope_is_registered_from_the_surface(self):
        surface, hubs, _runtime, core = _modules()
        registered = hubs._READ_SCOPE
        # A bound method is a new object on every attribute access, so the
        # identity that matters is the function and the handle it is bound to.
        self.assertIs(getattr(registered, "__func__", None), core.Database.reuse_connection,
                      "surface.py no longer hands hubs the database's reuse_connection")
        self.assertIs(getattr(registered, "__self__", None), surface.DB)
