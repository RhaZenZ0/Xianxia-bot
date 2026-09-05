import unittest

import httpx

from app.ops.game_engine import GameEngineClient, GameEngineError


class GameEngineClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []

        async def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            path = request.url.path
            if path == "/livez":
                return httpx.Response(200, json={"status": "ok", "role": "authoritative"})
            if path == "/v1/db/status":
                return httpx.Response(200, json={"journal_mode": "wal", "owner": "go"})
            if path == "/v1/game/action":
                payload = __import__("json").loads(request.content)
                return httpx.Response(200, json={"result": {"operation": payload["operation"], "actor_id": payload["actor_id"]}})
            if path == "/v1/simulation/run-due":
                return httpx.Response(200, json={"runs": [{"system": "npc_life", "updated": 12}]})
            if path == "/v1/simulation/force":
                return httpx.Response(200, json={"system": "economy", "steps": 2, "updated": 5})
            if path == "/v1/game/fail":
                return httpx.Response(500, json={"error": "boom"})
            return httpx.Response(404, json={"error": "not found"})

        self.client = GameEngineClient("http://engine.invalid", timeout_seconds=5)
        await self.client._client.aclose()
        self.client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://engine.invalid")

    async def asyncTearDown(self):
        await self.client._client.aclose()

    async def test_action_routes_to_authoritative_game_endpoint(self):
        result = await self.client.action("combat.damage", 42, {"amount": 9})
        self.assertEqual(result, {"operation": "combat.damage", "actor_id": 42})
        self.assertEqual(self.requests[-1].url.path, "/v1/game/action")

    async def test_authoritative_action_rejects_client_game_minute(self):
        with self.assertRaisesRegex(ValueError, "Go owns current world time"):
            await self.client.authoritative_action(
                "exploration.travel",
                42,
                {"destination": "Riverguard City", "game_minute": 999999},
                action_id="forged-time",
            )
        self.assertFalse(self.requests)

    async def test_simulation_endpoints_keep_explicit_scheduler_time(self):
        await self.client.run_due_simulation(12345, {"npc_life": True})
        payload = __import__("json").loads(self.requests[-1].content)
        self.assertEqual(payload["game_minute"], 12345)

    async def test_run_due_simulation_is_one_coarse_grained_call(self):
        result = await self.client.run_due_simulation(12345, {"npc_life": True, "economy": True})
        self.assertEqual(result, [{"system": "npc_life", "updated": 12}])
        self.assertEqual(self.requests[-1].url.path, "/v1/simulation/run-due")

    async def test_force_simulation_returns_engine_batch_result(self):
        result = await self.client.force_simulation("economy", 2, 200)
        self.assertEqual(result["updated"], 5)

    async def test_live_and_database_status_report_go_engine(self):
        self.assertEqual((await self.client.live())["role"], "authoritative")
        status = await self.client.database_status()
        self.assertEqual(status["journal_mode"], "wal")
        self.assertEqual(status["owner"], "go")

    async def test_http_error_raises_game_engine_error(self):
        with self.assertRaises(GameEngineError):
            await self.client._post("/v1/game/fail", {})


if __name__ == "__main__":
    unittest.main()
