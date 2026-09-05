import asyncio
import unittest

from app.ops.http_limits import (
    STREAM_LIMIT,
    ConnectionLimiter,
    EmptyRequest,
    HeaderLimits,
    RequestHeadRejected,
    read_request_head,
)


class _Clock:
    """Injectable monotonic clock so deadline behaviour is tested without sleeping."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _ScriptedReader:
    def __init__(self, lines):
        self.lines = list(lines)
        self.reads = 0

    async def readline(self) -> bytes:
        self.reads += 1
        if not self.lines:
            return b""
        item = self.lines.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class _SlowReader:
    """A client that sends one header line just before each timeout expires.

    This is the whole attack: with only a per-line timeout, every line resets the
    clock and the connection is held open forever. The reader itself never
    blocks - the injected clock does the waiting, so the test is instant.
    """

    def __init__(self, clock: _Clock, step: float, line: bytes = b"X-Pad: y\r\n") -> None:
        self.clock = clock
        self.step = step
        self.line = line
        self.reads = 0

    async def readline(self) -> bytes:
        self.reads += 1
        if self.reads == 1:
            return b"GET / HTTP/1.1\r\n"
        self.clock.advance(self.step)
        return self.line


def _read(reader, *, limits=None, clock=None):
    return asyncio.run(
        read_request_head(reader, limits=limits or HeaderLimits(), clock=clock or (lambda: 0.0))
    )


class NormalRequestTests(unittest.TestCase):
    def test_a_plain_request_parses(self):
        head = _read(
            _ScriptedReader([
                b"GET /healthz HTTP/1.1\r\n",
                b"Host: nas.local\r\n",
                b"User-Agent: curl/8\r\n",
                b"\r\n",
            ])
        )
        self.assertEqual(head.request_line, "GET /healthz HTTP/1.1")
        self.assertEqual(head.headers["host"], "nas.local")
        self.assertEqual(head.header_lines, 2)

    def test_header_names_are_lowercased_and_values_trimmed(self):
        head = _read(_ScriptedReader([b"GET / HTTP/1.1\r\n", b"X-Xianxia-Control:   abc  \r\n", b"\r\n"]))
        self.assertEqual(head.headers["x-xianxia-control"], "abc")

    def test_a_bare_lf_terminator_is_accepted(self):
        head = _read(_ScriptedReader([b"GET / HTTP/1.1\n", b"Host: x\n", b"\n"]))
        self.assertEqual(head.headers["host"], "x")

    def test_a_closed_connection_is_not_an_error(self):
        with self.assertRaises(EmptyRequest):
            _read(_ScriptedReader([b""]))

    def test_a_head_that_ends_at_eof_still_returns(self):
        head = _read(_ScriptedReader([b"GET / HTTP/1.1\r\n", b"Host: x\r\n"]))
        self.assertEqual(head.headers["host"], "x")


class SlowHeaderTests(unittest.TestCase):
    """The reported vulnerability: a per-line timeout that resets is not a limit."""

    def test_a_slow_client_is_cut_off_by_the_absolute_deadline(self):
        clock = _Clock()
        limits = HeaderLimits(header_deadline_seconds=10.0, line_timeout_seconds=5.0)
        # 4.9s per line: never trips the per-line timeout, which is exactly how
        # the old loop could be held open indefinitely.
        reader = _SlowReader(clock, step=4.9)
        with self.assertRaises(RequestHeadRejected) as caught:
            _read(reader, limits=limits, clock=clock)
        self.assertEqual(caught.exception.status, 408)
        self.assertEqual(caught.exception.error, "header_timeout")

    def test_the_slow_client_is_cut_off_after_a_bounded_number_of_lines(self):
        clock = _Clock()
        reader = _SlowReader(clock, step=4.9)
        with self.assertRaises(RequestHeadRejected):
            _read(reader, limits=HeaderLimits(header_deadline_seconds=10.0), clock=clock)
        # Without the deadline this loop never ends. Three or four reads is the
        # whole cost of the attack now.
        self.assertLess(reader.reads, 6)

    def test_the_deadline_is_absolute_not_per_line(self):
        clock = _Clock()
        limits = HeaderLimits(header_deadline_seconds=10.0, line_timeout_seconds=60.0)
        reader = _SlowReader(clock, step=3.0)
        with self.assertRaises(RequestHeadRejected) as caught:
            _read(reader, limits=limits, clock=clock)
        self.assertEqual(caught.exception.status, 408)
        self.assertLessEqual(clock.now - 1000.0, 15.0)

    def test_a_fast_client_within_the_deadline_is_untouched(self):
        clock = _Clock()
        head = _read(
            _ScriptedReader([b"GET / HTTP/1.1\r\n", b"Host: x\r\n", b"\r\n"]),
            limits=HeaderLimits(header_deadline_seconds=10.0),
            clock=clock,
        )
        self.assertEqual(head.headers["host"], "x")

    def test_a_stalled_read_hits_the_per_line_timeout(self):
        class _Stalled:
            async def readline(self):
                await asyncio.sleep(5)

        with self.assertRaises(RequestHeadRejected) as caught:
            _read(_Stalled(), limits=HeaderLimits(line_timeout_seconds=0.01, header_deadline_seconds=1.0))
        self.assertEqual(caught.exception.status, 408)


class HeaderVolumeTests(unittest.TestCase):
    """A fast client hits the count and byte caps; a slow one hits the deadline.

    The two are independent on purpose - closing only one of them leaves the
    other open.
    """

    def test_too_many_header_lines_are_refused(self):
        reader = _ScriptedReader([b"GET / HTTP/1.1\r\n"] + [b"X-A: b\r\n"] * 500 + [b"\r\n"])
        with self.assertRaises(RequestHeadRejected) as caught:
            _read(reader, limits=HeaderLimits(max_header_lines=10))
        self.assertEqual(caught.exception.status, 431)
        self.assertEqual(caught.exception.error, "too_many_headers")

    def test_the_header_count_cap_stops_a_fast_client_with_no_deadline_pressure(self):
        # Same attack, sent as fast as the socket allows: the clock never moves,
        # so only the count cap can stop it.
        reader = _ScriptedReader([b"GET / HTTP/1.1\r\n"] + [b"X-A: b\r\n"] * 100_000)
        with self.assertRaises(RequestHeadRejected):
            _read(reader, limits=HeaderLimits(max_header_lines=64, header_deadline_seconds=120.0))
        self.assertLess(reader.reads, 70)

    def test_cumulative_header_bytes_are_capped(self):
        big = b"X-Pad: " + b"y" * 900 + b"\r\n"
        reader = _ScriptedReader([b"GET / HTTP/1.1\r\n"] + [big] * 200 + [b"\r\n"])
        with self.assertRaises(RequestHeadRejected) as caught:
            _read(reader, limits=HeaderLimits(max_header_bytes=4096, max_header_lines=1000))
        self.assertEqual(caught.exception.status, 431)
        self.assertEqual(caught.exception.error, "header_fields_too_large")

    def test_unique_header_names_cannot_grow_the_dict_without_bound(self):
        # The dict, not the connection, is the memory vector: every distinct key
        # is a new entry.
        lines = [f"X-{i}: v\r\n".encode() for i in range(100_000)]
        reader = _ScriptedReader([b"GET / HTTP/1.1\r\n"] + lines)
        with self.assertRaises(RequestHeadRejected):
            _read(reader, limits=HeaderLimits(max_header_lines=50, max_header_bytes=1_000_000))
        self.assertLess(reader.reads, 60)

    def test_an_over_long_request_line_is_refused_with_414(self):
        reader = _ScriptedReader([b"GET /" + b"a" * 20000 + b" HTTP/1.1\r\n"])
        with self.assertRaises(RequestHeadRejected) as caught:
            _read(reader, limits=HeaderLimits(max_request_line_bytes=1024))
        self.assertEqual(caught.exception.status, 414)

    def test_a_stream_limit_overrun_answers_431_instead_of_logging_a_traceback(self):
        # asyncio's readline() raises ValueError past the stream limit. Letting
        # that reach log.exception writes a stack trace per oversized line, which
        # amplifies the same attack into log flooding.
        reader = _ScriptedReader([b"GET / HTTP/1.1\r\n", ValueError("Separator is not found")])
        with self.assertRaises(RequestHeadRejected) as caught:
            _read(reader)
        self.assertEqual(caught.exception.status, 431)
        self.assertEqual(caught.exception.error, "header_line_too_long")

    def test_default_limits_are_generous_for_a_real_browser(self):
        limits = HeaderLimits()
        self.assertGreaterEqual(limits.max_header_lines, 50)
        self.assertGreaterEqual(limits.max_header_bytes, 8192)
        self.assertGreaterEqual(limits.max_request_line_bytes, 4096)


class LimitValidationTests(unittest.TestCase):
    def test_absurdly_small_limits_are_rejected_at_construction(self):
        for kwargs in (
            {"max_request_line_bytes": 1},
            {"max_header_lines": 0},
            {"max_header_bytes": 8},
            {"header_deadline_seconds": 0},
            {"line_timeout_seconds": -1},
        ):
            with self.assertRaises(ValueError, msg=str(kwargs)):
                HeaderLimits(**kwargs).validated()

    def test_the_stream_limit_exceeds_the_largest_accepted_body(self):
        # Both servers cap request bodies at 65536 and read them with
        # readexactly(). A stream limit at or below that deadlocks the read,
        # because _maybe_resume_transport refuses to resume over the limit.
        self.assertGreater(STREAM_LIMIT, 65536)


class ConnectionLimiterTests(unittest.TestCase):
    def test_connections_beyond_the_cap_are_refused(self):
        limiter = ConnectionLimiter(max_connections=2)
        self.assertTrue(limiter.try_acquire())
        self.assertTrue(limiter.try_acquire())
        self.assertFalse(limiter.try_acquire())
        self.assertEqual(limiter.snapshot()["refused"], 1)

    def test_releasing_frees_a_slot(self):
        limiter = ConnectionLimiter(max_connections=1)
        self.assertTrue(limiter.try_acquire())
        limiter.release()
        self.assertTrue(limiter.try_acquire())

    def test_peak_is_recorded_for_the_admin_panel(self):
        limiter = ConnectionLimiter(max_connections=4)
        limiter.try_acquire(); limiter.try_acquire(); limiter.try_acquire()
        limiter.release()
        self.assertEqual(limiter.snapshot()["peak"], 3)
        self.assertEqual(limiter.snapshot()["active"], 2)

    def test_release_never_goes_negative(self):
        limiter = ConnectionLimiter(max_connections=1)
        limiter.release()
        limiter.release()
        self.assertEqual(limiter.snapshot()["active"], 0)

    def test_a_zero_cap_is_clamped_to_one_rather_than_wedging_the_server(self):
        self.assertEqual(ConnectionLimiter(max_connections=0).max_connections, 1)


if __name__ == "__main__":
    unittest.main()
