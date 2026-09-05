"""End-to-end proof over a real socket that a slow-header client cannot hold on.

The unit tests in test_http_limits.py drive an injected clock. This one runs the
actual HealthServer on a real port and dribbles headers at it the way an attacker
would, because the thing being fixed is a property of the running server, not of
a parsing function.

Timings are deliberately small (a one-second head deadline) so the whole file
runs in a couple of seconds.
"""

import asyncio
import unittest

from tests.support import PROJECT_ROOT  # noqa: F401  (ensures sys.path is set)

from app.ops.health import HealthServer, HealthState
from app.ops.http_limits import HeaderLimits


async def _start(**kwargs) -> HealthServer:
    state = HealthState(supported_schema_version=1)
    server = HealthServer(
        state,
        host="127.0.0.1",
        port=0,
        header_limits=HeaderLimits(
            header_deadline_seconds=1.0,
            line_timeout_seconds=0.5,
            max_header_lines=kwargs.pop("max_header_lines", 100),
            max_header_bytes=kwargs.pop("max_header_bytes", 16384),
        ),
        **kwargs,
    )
    await server.start()
    return server


async def _read_all(reader, timeout: float) -> bytes:
    try:
        return await asyncio.wait_for(reader.read(4096), timeout=timeout)
    except asyncio.TimeoutError:
        return b""


class SlowHeaderIntegrationTests(unittest.TestCase):
    def test_a_dribbling_client_is_closed_by_the_deadline(self):
        async def scenario():
            server = await _start()
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", server.bound_port)
                writer.write(b"GET /livez HTTP/1.1\r\n")
                await writer.drain()
                # One header every 0.4s: under the 0.5s per-line timeout, so the
                # old loop would have been held open for as long as we cared to
                # keep typing.
                async def dribble():
                    for _ in range(50):
                        writer.write(b"X-Pad: y\r\n")
                        await writer.drain()
                        await asyncio.sleep(0.4)

                task = asyncio.create_task(dribble())
                response = await _read_all(reader, timeout=4.0)
                task.cancel()
                writer.close()
                return response
            finally:
                await server.stop()

        response = asyncio.run(scenario())
        self.assertTrue(response, "the server never answered; the connection was held open")
        self.assertIn(b"408", response.split(b"\r\n", 1)[0])

    def test_a_normal_request_is_unaffected(self):
        async def scenario():
            server = await _start()
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", server.bound_port)
                writer.write(b"GET /livez HTTP/1.1\r\nHost: localhost\r\nUser-Agent: test\r\n\r\n")
                await writer.drain()
                response = await _read_all(reader, timeout=3.0)
                writer.close()
                return response
            finally:
                await server.stop()

        response = asyncio.run(scenario())
        self.assertIn(b"200", response.split(b"\r\n", 1)[0])
        self.assertIn(b"alive", response)

    def test_a_header_flood_is_refused_with_431(self):
        async def scenario():
            server = await _start(max_header_lines=16)
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", server.bound_port)
                writer.write(b"GET /livez HTTP/1.1\r\n")
                writer.write(b"X-Pad: y\r\n" * 400)
                await writer.drain()
                response = await _read_all(reader, timeout=3.0)
                writer.close()
                return response
            finally:
                await server.stop()

        response = asyncio.run(scenario())
        self.assertIn(b"431", response.split(b"\r\n", 1)[0])

    def test_connections_beyond_the_cap_get_503(self):
        async def scenario():
            server = await _start(max_connections=2)
            try:
                held = []
                for _ in range(2):
                    reader, writer = await asyncio.open_connection("127.0.0.1", server.bound_port)
                    # A partial head: the handler holds its slot until the deadline.
                    writer.write(b"GET /livez HTTP/1.1\r\n")
                    await writer.drain()
                    held.append((reader, writer))
                await asyncio.sleep(0.1)

                reader, writer = await asyncio.open_connection("127.0.0.1", server.bound_port)
                writer.write(b"GET /livez HTTP/1.1\r\nHost: x\r\n\r\n")
                await writer.drain()
                response = await _read_all(reader, timeout=2.0)
                writer.close()
                for _, held_writer in held:
                    held_writer.close()
                return response
            finally:
                await server.stop()

        response = asyncio.run(scenario())
        self.assertIn(b"503", response.split(b"\r\n", 1)[0])
        self.assertIn(b"too_many_connections", response)

    def test_the_server_recovers_after_the_slow_clients_are_cut_off(self):
        """A DoS that leaves the server unusable afterwards is only half fixed."""

        async def scenario():
            server = await _start(max_connections=2)
            try:
                held = []
                for _ in range(2):
                    _r, writer = await asyncio.open_connection("127.0.0.1", server.bound_port)
                    writer.write(b"GET /livez HTTP/1.1\r\n")
                    await writer.drain()
                    held.append(writer)
                # Wait past the head deadline; both slots must come back.
                await asyncio.sleep(1.6)
                for writer in held:
                    writer.close()

                reader, writer = await asyncio.open_connection("127.0.0.1", server.bound_port)
                writer.write(b"GET /livez HTTP/1.1\r\nHost: x\r\n\r\n")
                await writer.drain()
                response = await _read_all(reader, timeout=3.0)
                writer.close()
                return response
            finally:
                await server.stop()

        response = asyncio.run(scenario())
        self.assertIn(b"200", response.split(b"\r\n", 1)[0])


if __name__ == "__main__":
    unittest.main()
