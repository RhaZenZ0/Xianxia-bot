"""How often each command is used (v1.3.5): counted, never consulted.

The owner asked to see the most used commands, and decided that the numbers
reorder nothing - the daily five stay first on the menu and every page keeps
its authored order. So this module is a counter and only a counter: one row
per command path per UTC day in `command_usage`, read by the GM's
observability card and by nothing that decides what a panel draws.

The path is the leaf path the hubs build (`/alchemy forage`, `/travel`), so a
slash command, a hub press and a typed line count as one thing. Three doors
record it - the command tree's `interaction_check`, `hubs._invoke_action` and
`typed_play.dispatch` - each after its own refusal has passed, so a press the
world refused is not a use.

Two rules hold it. **Recording never raises**: it runs beside a player's
action, and a counter that could cost somebody their reply would be a worse
bug than the one it measures. **It never waits**: the write is fired and the
handler goes on, because an audit of how often `/explore` is pressed is not
worth a round trip on every press of `/explore`.

The database handle is passed by value (`maintenance.refuse(DB, ...)`'s shape)
so this sits in the bottom tier beside it and typed play can call it without
naming the runtime's `DB`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable

_log = logging.getLogger("xianxia.bot.usage")

# A task with no reference can be collected mid-flight; the set holds each one
# until it finishes.
_inflight: set[asyncio.Task[Any]] = set()


def fire_and_forget(coro: Awaitable[Any]) -> None:
    """Run `coro` without waiting for it. Outside a running loop the write is
    dropped rather than raised: a counter is never the reason anything fails."""
    try:
        task = asyncio.ensure_future(coro)
    except RuntimeError:
        if hasattr(coro, "close"):
            coro.close()  # type: ignore[union-attr]
        return
    _inflight.add(task)
    task.add_done_callback(_inflight.discard)


async def record(db: Any, path: str) -> None:
    """One more use of `path`. Never raises."""
    key = str(path or "").strip()
    if not key:
        return
    try:
        await db.record_command_use(key)
    except Exception:
        _log.debug("Could not record a use of %s", key, exc_info=True)


def note(db: Any, path: str) -> None:
    """`record`, fired and not awaited - the form the three doors use."""
    fire_and_forget(record(db, path))
