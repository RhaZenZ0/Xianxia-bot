import base64
import json
import unittest

import httpx
import pytest

from tests.support import httpx_is_shimmed

pytestmark = pytest.mark.skipif(
    httpx_is_shimmed(),
    reason="needs the real httpx: these drive httpx.MockTransport, which the stub in tests/support.py cannot provide",
)

from app.database.remote import GoDatabaseTransport, RemoteCursor, RemoteRow


class RemoteRowTests(unittest.TestCase):
    def test_sqlite_row_compatible_access(self):
        row = RemoteRow(["user_id", "name"], [42, "Lin"])
        self.assertEqual(row[0], 42)
        self.assertEqual(row["name"], "Lin")
        self.assertEqual(row.keys(), ["user_id", "name"])
        self.assertEqual(list(row), [42, "Lin"])

    def test_blob_envelope_is_decoded(self):
        payload = base64.b64encode(b"jade-slip").decode("ascii")
        row = RemoteRow(["blob"], [{"__blob_b64": payload}])
        self.assertEqual(row["blob"], b"jade-slip")


class RemoteCursorTests(unittest.IsolatedAsyncioTestCase):
    async def test_row_factory_mode_matches_repository_expectations(self):
        cursor = RemoteCursor(
            columns=["id", "value"],
            rows=[[1, "a"], [2, "b"]],
            lastrowid=2,
            rowcount=2,
            row_mode=True,
        )
        first = await cursor.fetchone()
        rest = await cursor.fetchall()
        self.assertEqual(first["value"], "a")
        self.assertEqual(rest[0]["id"], 2)
        self.assertEqual(await cursor.fetchone(), None)


class GoDatabaseTransportContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if request.url.path == "/v1/db/session" and request.method == "POST":
                return httpx.Response(201, json={"session_id": "session-1"})
            if request.url.path == "/v1/db/session/session-1/execute":
                return httpx.Response(200, json={
                    "columns": ["value"], "rows": [[7]], "lastrowid": 0, "rowcount": 0,
                })
            if request.url.path == "/v1/db/session/session-1/commit":
                return httpx.Response(200, json={"ok": True})
            if request.url.path == "/v1/db/session/session-1" and request.method == "DELETE":
                return httpx.Response(200, json={"closed": True})
            if request.url.path == "/v1/db/batch":
                return httpx.Response(200, json={"results": [{"rowcount": 1}]})
            return httpx.Response(404, json={"error": "not_found"})

        self.transport = GoDatabaseTransport("http://engine.invalid")
        await self.transport._client.aclose()
        self.transport._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://engine.invalid"
        )

    async def asyncTearDown(self):
        await self.transport._client.aclose()

    async def test_session_execute_commit_and_close_use_go_database_boundary(self):
        session = await self.transport.open()
        cursor = await session.execute("SELECT ? AS value", (7,))
        self.assertEqual((await cursor.fetchone())[0], 7)
        await session.commit()
        await session.close()
        self.assertEqual(
            [r.url.path for r in self.requests],
            [
                "/v1/db/session",
                "/v1/db/session/session-1/execute",
                "/v1/db/session/session-1/commit",
                "/v1/db/session/session-1",
            ],
        )

    async def test_batch_encodes_binary_parameters_and_requests_atomic_transaction(self):
        result = await self.transport.batch(
            [{"sql": "INSERT INTO t(blob) VALUES(?)", "params": (b"abc",)}],
            transaction=True,
        )
        self.assertEqual(result, [{"rowcount": 1}])
        body = json.loads(self.requests[-1].content)
        self.assertTrue(body["transaction"])
        self.assertEqual(
            body["statements"][0]["params"][0],
            {"__blob_b64": base64.b64encode(b"abc").decode("ascii")},
        )


if __name__ == "__main__":
    unittest.main()
