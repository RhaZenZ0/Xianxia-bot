import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT, seed_character
install_aiosqlite_shim()

from app.advanced_runtime import ERA_CYCLE
from app.database import Database, SCHEMA_VERSION
from app.game import World
from app.operations import AlertDispatcher


class CompletedAdvancedSystemsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "completed.sqlite3")
        await self.db.init()
        attrs = {"body": 12, "agility": 12, "spirit": 12, "insight": 8, "will": 10, "presence": 6}
        for uid, name in ((801, "Azure"), (802, "Crimson"), (803, "Jade")):
            self.assertTrue(await seed_character(self.db, 
                user_id=uid, discord_name=name.lower(), name=name, origin="Greenriver Town",
                path="Sword Cultivator", spiritual_root="Fire", concept="completed systems test",
                location="Greenriver Town", attributes=attrs, qi_max=100, vitality_max=120,
                created_game_minute=0,
            ))
        async with self.db._connect() as db:
            await db.execute(
                "UPDATE characters SET realm_index=8,phase=9,vitality=120,vitality_max=120 WHERE user_id IN (801,802,803)"
            )
            await db.commit()
        self.world = World(PROJECT_ROOT / "content" / "world.json")
        await self.db.sync_world_catalog(self.world.data)

    async def asyncTearDown(self):
        self.tmp.cleanup()








    async def test_08_slow_query_observability_persistence_and_external_webhook_alerting(self):
        obs_db = Database(Path(self.tmp.name) / "observed.sqlite3", slow_query_ms=0)
        await obs_db.init()
        await obs_db.operational_health()
        flushed = await obs_db.flush_slow_query_log()
        self.assertGreater(flushed, 0)
        snapshot = await obs_db.observability_snapshot()
        self.assertGreater(snapshot["recent_slow_queries_1h"], 0)
        alert_id = await obs_db.record_operational_alert(
            "test_alert", severity="warning", message="test", detail={"slow": True}, delivered=False,
        )
        self.assertGreater(alert_id, 0)

        received = []
        async def handler(reader, writer):
            header = await reader.readuntil(b"\r\n\r\n")
            length = 0
            for line in header.decode("latin1").split("\r\n"):
                if line.lower().startswith("content-length:"):
                    length = int(line.split(":", 1)[1].strip())
            body = await reader.readexactly(length) if length else b""
            received.append(json.loads(body.decode("utf-8")))
            writer.write(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain(); writer.close(); await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            dispatcher = AlertDispatcher(f"http://127.0.0.1:{port}/alert", cooldown_seconds=0)
            self.assertTrue(await dispatcher.send("slow_query_pressure", "threshold exceeded", details={"count": 5}))
            await asyncio.sleep(0.02)
        finally:
            server.close(); await server.wait_closed()
        self.assertEqual(received[0]["key"], "slow_query_pressure")


if __name__ == "__main__":
    unittest.main()
