"""An active event the tick spawned always gets a scene (v1.34.0).

`simulationRunDue` answered 400 when a later system failed after an earlier one
had committed an event, so the bot never saw the run and the event sat active
with no thread until it expired. The engine now answers 200 with the runs and the
error, and the worker sweeps every cycle for an active tick-spawned event that has
no `event_threads` row, so a restart or a lost reply heals the same way.
"""
from __future__ import annotations

import ast
import importlib
import json
import logging
import os
import re
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from tests.support import PROJECT_ROOT, install_aiosqlite_shim, install_openai_shim

install_aiosqlite_shim()
install_openai_shim()

from app.database import Database  # noqa: E402
from app.ops.game_engine import GameEngineClient  # noqa: E402

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}

BOT_PY = (PROJECT_ROOT / "app" / "bot" / "bot.py").read_text(encoding="utf-8")
GO_SIM = (PROJECT_ROOT / "go_core" / "internal" / "simulation" / "world.go").read_text(encoding="utf-8")
GO_ROTATION = (PROJECT_ROOT / "go_core" / "internal" / "game" / "secret_realm_rotation.go").read_text(encoding="utf-8")


async def _insert_event(db: Database, key: str, *, started: float, ends: float, active: int = 1,
                        thread: int | None = None, payload: dict | None = None) -> None:
    async with db._connect() as conn:
        await conn.execute(
            "INSERT INTO world_events(event_key,dedupe_key,event_type,title,location,payload_json,active,starts_at,ends_at,thread_id)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (key, "d:" + key, "random_event", "T " + key, "Greenriver Town", json.dumps(payload or {"description": "d"}),
             active, started, ends, thread),
        )
        await conn.commit()


class TheReaderFindsOnlyTheSceneLessTickEvents(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "sweep.sqlite3")
        await self.db.init()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_an_active_tick_event_with_no_thread_is_found(self):
        now = time.time()
        await _insert_event(self.db, "auto:orphan:1:2", started=now - 600, ends=now + 3600)
        await _insert_event(self.db, "secret_realm_rotation:azure:9", started=now - 600, ends=now + 3600)
        rows = await self.db.get_active_events_without_a_scene(started_before=now - 60)
        self.assertEqual({r["event_key"] for r in rows}, {"auto:orphan:1:2", "secret_realm_rotation:azure:9"},
                         "an active event has no thread and the sweep's reader does not see it")
        self.assertEqual(rows[0]["payload"], {"description": "d"})

    async def test_everything_that_already_has_or_never_needs_a_scene_is_left_alone(self):
        now = time.time()
        await _insert_event(self.db, "auto:threaded:1:2", started=now - 600, ends=now + 3600)
        await self.db.register_event_thread(
            thread_id=77, event_key="auto:threaded:1:2", event_type="random_event", title="t", channel_id=1,
            message_id=2, announcement_channel_id=None, announcement_message_id=None, triggered_by=None,
            expires_at=now + 3600)
        await _insert_event(self.db, "auto:expired:1:2", started=now - 7200, ends=now - 10)
        await _insert_event(self.db, "auto:closed:1:2", started=now - 600, ends=now + 3600, active=0)
        await _insert_event(self.db, "auto:fresh:1:2", started=now - 5, ends=now + 3600)
        await _insert_event(self.db, "secret:azure:admin:123", started=now - 600, ends=now + 3600)
        await _insert_event(self.db, "player:event:1", started=now - 600, ends=now + 3600)
        rows = await self.db.get_active_events_without_a_scene(started_before=now - 60)
        self.assertEqual(rows, [], f"the sweep would open a scene nobody lost: {[r['event_key'] for r in rows]}")

    async def test_the_prefixes_are_the_ones_the_engine_mints(self):
        self.assertIn('"auto:%s:%d:%d"', GO_SIM)
        self.assertIn('"secret_realm_rotation:%s:%d"', GO_ROTATION)
        self.assertEqual(Database.ENGINE_EVENT_KEY_PREFIXES, ("auto:", "secret_realm_rotation:"))


class TheWorkerSweeps(unittest.IsolatedAsyncioTestCase):
    def _bot_class(self):
        with patch.dict(os.environ, ENV):
            bot_module = importlib.import_module("app.bot.bot")
        return bot_module, bot_module.XianxiaBot

    async def test_a_scene_less_event_gets_its_scene_once_per_five_minutes(self):
        bot_module, cls = self._bot_class()
        opened: list[dict] = []

        async def opener(event):
            opened.append(dict(event))

        fake = SimpleNamespace(open_engine_event_scene=opener)
        row = {"event_key": "auto:orphan:1:2", "event_type": "random_event", "title": "Lost Event",
               "location": "Greenriver Town", "ends_at": time.time() + 3600,
               "payload": {"description": "Rain of ash.", "consequence_text": "Crops fail."}}
        with patch.object(bot_module.DB, "get_active_events_without_a_scene", AsyncMock(return_value=[row])):
            await cls.sweep_events_without_a_scene(fake)
            await cls.sweep_events_without_a_scene(fake)
        self.assertEqual(len(opened), 1, "an active event has no thread, and the sweep did not open one exactly once")
        self.assertEqual(opened[0]["event_key"], "auto:orphan:1:2")
        self.assertEqual(opened[0]["description"], "Rain of ash.")
        self.assertEqual(opened[0]["consequence_text"], "Crops fail.")

    async def test_one_bad_scene_does_not_stop_the_next(self):
        bot_module, cls = self._bot_class()
        seen: list[str] = []

        async def opener(event):
            seen.append(event["event_key"])
            if event["event_key"] == "auto:a:1:1":
                raise RuntimeError("discord said no")

        fake = SimpleNamespace(open_engine_event_scene=opener)
        rows = [{"event_key": k, "event_type": "random_event", "title": k, "location": "x", "ends_at": 1.0, "payload": {}}
                for k in ("auto:a:1:1", "auto:b:1:1")]
        with patch.object(bot_module.DB, "get_active_events_without_a_scene", AsyncMock(return_value=rows)):
            with self.assertLogs(bot_module.log, level="ERROR"):
                await cls.sweep_events_without_a_scene(fake)
        self.assertEqual(seen, ["auto:a:1:1", "auto:b:1:1"])

    def test_the_worker_sweeps_outside_the_tick_s_own_try(self):
        tree = ast.parse(BOT_PY)
        worker = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "event_expiry_worker")
        loop = next(n for n in ast.walk(worker) if isinstance(n, ast.While))
        tick_try = next(n for n in loop.body if isinstance(n, ast.Try))
        inside = ast.get_source_segment(BOT_PY, tick_try) or ""
        self.assertNotIn("sweep_events_without_a_scene", inside,
                         "the sweep sits inside the tick's try, so a tick that failed outright skips it")
        outside = "\n".join(ast.get_source_segment(BOT_PY, n) or "" for n in loop.body if n is not tick_try)
        self.assertIn("sweep_events_without_a_scene", outside)


class TheClientKeepsTheRunsOfATickThatFailed(unittest.IsolatedAsyncioTestCase):
    async def _client(self, status: int, body: dict) -> GameEngineClient:
        client = GameEngineClient("http://engine.invalid", timeout_seconds=5)
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body)), base_url="http://engine.invalid")
        self.addAsyncCleanup(client._client.aclose)
        return client

    async def test_runs_beside_an_error_are_returned_and_the_error_is_logged(self):
        client = await self._client(200, {"runs": [{"system": "autonomous_world_events", "events": [{"event_key": "auto:x"}]}],
                                          "error": "advanced_world: boom"})
        with self.assertLogs("app.ops.game_engine", level=logging.ERROR) as logs:
            runs = await client.run_due_simulation({})
        self.assertEqual(runs[0]["events"][0]["event_key"], "auto:x")
        self.assertTrue(any("advanced_world: boom" in line for line in logs.output))

    async def test_a_request_error_still_raises(self):
        client = await self._client(400, {"error": "simulation_failed", "message": "could not open"})
        with self.assertRaisesRegex(Exception, "could not open"):
            await client.run_due_simulation({})

    async def test_the_engine_answers_both_at_once(self):
        go = (PROJECT_ROOT / "go_core" / "internal" / "server" / "server.go").read_text(encoding="utf-8")
        answer = re.search(r"func runDueAnswer\(.*?\n}\n", go, re.S)
        self.assertIsNotNone(answer, "runDueAnswer is no longer where the gate looks")
        self.assertIn('"runs": runs, "error": err.Error()', answer.group(0))


if __name__ == "__main__":
    unittest.main()
