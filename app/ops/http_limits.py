"""Bounded HTTP request-head reading for the two hand-rolled listeners.

The hole this closes
--------------------
``app/ops/health.py`` and ``app/dashboard/server.py`` both parse HTTP by hand with a loop of
``await asyncio.wait_for(reader.readline(), timeout=N)``. There was a timeout per
line and nothing else, which leaves four separate ways to hurt the process, all
of them reachable **before any authentication runs**:

1. **The per-line timeout resets.** A client that sends one header line every
   N-0.1 seconds holds a connection open forever. That is Slowloris, and the
   dashboard's own timeout of 5s made each connection cost one header line per
   five seconds to maintain.
2. **Unbounded header count.** ``headers[key] = value`` grows a dict with no cap.
   Millions of distinct short header names are a memory-exhaustion vector that
   has nothing to do with holding connections open.
3. **Unbounded cumulative header bytes.** Individual lines were bounded only by
   asyncio's *default* 64 KiB stream limit - an accident of the library, not a
   decision - and nothing bounded their sum.
4. **No absolute deadline.** Nothing capped total time spent reading one head.

A fifth problem sat underneath: neither server capped concurrent connections, so
every one of the above multiplied by however many sockets an attacker opened.

Design notes worth keeping
--------------------------
*Cumulative bytes are checked after each line, not before.* One line can
therefore overshoot the budget by at most the stream limit before it is
rejected. Checking first would mean capping each ``readline`` individually, and
the only way to do that is to lower asyncio's stream limit - which breaks
``readexactly`` for request bodies (``_maybe_resume_transport`` refuses to resume
while the buffer exceeds the limit, so a 64 KiB body read behind an 8 KiB limit
deadlocks until its timeout). Bounded overshoot is the cheaper correct answer.

*The deadline is absolute and monotonic.* Each read gets whichever is smaller,
the per-line timeout or the time left on the whole head. That is the part that
actually kills a slow-header attack; the byte and count caps kill the memory
half.

This module has no project imports so it can be unit tested without the rest of
the app, and takes an injectable clock so deadline behaviour can be tested
without sleeping.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

#: Explicit stream limit for asyncio.start_server. The default is 64 KiB and was
#: never chosen; this states it, and stays comfortably above the 64 KiB request
#: body cap both servers enforce so readexactly() cannot stall against it.
STREAM_LIMIT = 131072

_LINE_TERMINATORS = frozenset({b"\r\n", b"\n", b""})


class _Reader(Protocol):
    def readline(self) -> Awaitable[bytes]: ...


@dataclass(frozen=True)
class HeaderLimits:
    """Everything that bounds one request head.

    Defaults are generous for a browser (Chrome sends roughly 15 headers and
    1-2 KiB) and mean for an attacker.
    """

    max_request_line_bytes: int = 8192
    max_header_lines: int = 100
    max_header_bytes: int = 16384
    header_deadline_seconds: float = 10.0
    line_timeout_seconds: float = 5.0

    def validated(self) -> "HeaderLimits":
        for name, value, low in (
            ("max_request_line_bytes", self.max_request_line_bytes, 64),
            ("max_header_lines", self.max_header_lines, 1),
            ("max_header_bytes", self.max_header_bytes, 256),
        ):
            if int(value) < low:
                raise ValueError(f"{name} must be at least {low}")
        if self.header_deadline_seconds <= 0 or self.line_timeout_seconds <= 0:
            raise ValueError("header timeouts must be positive")
        return self


class RequestHeadRejected(Exception):
    """A request head that broke a limit. Carries the status to answer with."""

    def __init__(self, status: int, error: str, detail: str = "") -> None:
        super().__init__(detail or error)
        self.status = int(status)
        self.error = str(error)
        self.detail = str(detail)


class EmptyRequest(Exception):
    """The peer closed without sending anything. Not an error, not a response."""


@dataclass(frozen=True)
class RequestHead:
    request_line: str
    headers: dict[str, str]
    header_bytes: int
    header_lines: int


async def read_request_head(
    reader: _Reader,
    *,
    limits: HeaderLimits | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> RequestHead:
    """Read one request line plus its headers, or raise.

    Raises :class:`EmptyRequest` when the peer sent nothing, and
    :class:`RequestHeadRejected` with an HTTP status for every limit breach:
    414 for an over-long request line, 431 for too many or too large headers,
    408 when the absolute deadline passes.
    """
    limits = (limits or HeaderLimits()).validated()
    deadline = clock() + limits.header_deadline_seconds

    raw_request_line = await _read_line(reader, limits, deadline, clock)
    if not raw_request_line:
        raise EmptyRequest()
    if len(raw_request_line) > limits.max_request_line_bytes:
        raise RequestHeadRejected(
            414,
            "request_line_too_long",
            f"request line is {len(raw_request_line)} bytes, limit {limits.max_request_line_bytes}",
        )

    headers: dict[str, str] = {}
    header_bytes = 0
    header_lines = 0
    while True:
        line = await _read_line(reader, limits, deadline, clock)
        if line in _LINE_TERMINATORS:
            break

        header_lines += 1
        if header_lines > limits.max_header_lines:
            raise RequestHeadRejected(
                431,
                "too_many_headers",
                f"more than {limits.max_header_lines} header lines",
            )

        # Checked after the read: one line may overshoot by at most the stream
        # limit, which is bounded and far cheaper than lowering that limit.
        header_bytes += len(line)
        if header_bytes > limits.max_header_bytes:
            raise RequestHeadRejected(
                431,
                "header_fields_too_large",
                f"header block exceeded {limits.max_header_bytes} bytes",
            )

        text = line.decode("latin-1", "replace").strip()
        if ":" in text:
            key, value = text.split(":", 1)
            headers[key.strip().lower()] = value.strip()

    return RequestHead(
        request_line=raw_request_line.decode("ascii", "replace").strip(),
        headers=headers,
        header_bytes=header_bytes,
        header_lines=header_lines,
    )


async def _read_line(
    reader: _Reader,
    limits: HeaderLimits,
    deadline: float,
    clock: Callable[[], float],
) -> bytes:
    remaining = deadline - clock()
    if remaining <= 0:
        raise RequestHeadRejected(408, "header_timeout", "request head deadline exceeded")
    timeout = min(limits.line_timeout_seconds, remaining)
    try:
        return await asyncio.wait_for(_await_line(reader), timeout=timeout)
    except asyncio.TimeoutError:
        # Whether this was the per-line timeout or the last slice of the absolute
        # deadline, the connection has had its budget.
        raise RequestHeadRejected(408, "header_timeout", "timed out reading request head") from None
    except ValueError as exc:
        # asyncio.StreamReader.readline() raises ValueError when a line exceeds
        # the stream limit. Answering 431 keeps it out of the exception log,
        # where it would otherwise write a stack trace per oversized line - an
        # amplification of the same attack.
        raise RequestHeadRejected(431, "header_line_too_long", str(exc)[:200]) from None


async def _await_line(reader: _Reader) -> bytes:
    return await reader.readline()


class ConnectionLimiter:
    """Hard cap on concurrent connections, with a counter for the health panel.

    The deadline above bounds how long one connection can cost. This bounds how
    many can cost it at once, which is the other half of a slow-header attack.
    """

    def __init__(self, max_connections: int = 64) -> None:
        self.max_connections = max(1, int(max_connections))
        self.active = 0
        self.peak = 0
        self.refused = 0

    def try_acquire(self) -> bool:
        if self.active >= self.max_connections:
            self.refused += 1
            return False
        self.active += 1
        self.peak = max(self.peak, self.active)
        return True

    def release(self) -> None:
        self.active = max(0, self.active - 1)

    def snapshot(self) -> dict[str, Any]:
        return {
            "max_connections": self.max_connections,
            "active": self.active,
            "peak": self.peak,
            "refused": self.refused,
        }
