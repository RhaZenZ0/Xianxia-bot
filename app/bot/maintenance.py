"""The world closed for maintenance, as the bot sees it (v1.0.0-rc.41).

The engine owns the flag and refuses every one of its ~150 authoritative
player operations while it is set (`checkMaintenanceTx`). That is the
backstop, and it does not depend on this module behaving. What it cannot do
is cover a read: `/sheet`, `/quests`, `/time` and every other card answer out
of the presentation layer's own SQL, never touching the engine's
authoritative path, so the engine gate would let them through mid-update.

This is the other half. It refuses at the four doors a player can reach the
bot by - a slash command, a hub panel button, a typed line, the shorthand -
before any handler runs, and it says why in the operator's own words.

Two rules hold it:

* **An administrator is never refused.** They are how the world reopens. The
  engine has the same asymmetry by construction (every `admin.*` lever
  bypasses the player dispatch path), and it is restated here rather than
  inherited, because this layer refuses reads the engine never sees.
* **The flag is read, never cached for long.** A GM who reopens the world
  expects the next command to work, and a bot that had cached "closed" for a
  minute would be a worse bug than the one this prevents. The cache exists
  only so a burst of interactions does not become a burst of queries, and the
  Discord lever clears it outright the moment it writes.
"""

from __future__ import annotations

import time
from typing import Any

# Long enough that a panel's rapid clicks cost one read, short enough that
# nobody notices the world reopening late.
CACHE_SECONDS = 3.0

_state: dict[str, Any] = {"enabled": False, "reason": "", "since": 0.0}
# None means "never read", not "read at time zero". A restart is the moment
# this matters: `time.monotonic()` counts from the host's boot, so on a NAS
# that has just come up it can be smaller than CACHE_SECONDS - and a numeric
# zero here would make the first few seconds after the bot starts serve the
# default (open) without ever asking the database. That is precisely the
# window an operator who closed the world and then ran `update.sh` would be
# standing in.
_read_at: float | None = None


def forget() -> None:
    """Drop the cache, so the next door reads the flag fresh.

    Called by the Discord lever after it writes, so an operator never waits on
    a TTL, and by the tests between cases.
    """
    global _read_at
    _read_at = None


def remember(state: dict[str, Any]) -> None:
    """Take a state the operator just set, without a round trip to confirm it."""
    global _state, _read_at
    _state = {
        "enabled": bool(state.get("enabled", False)),
        "reason": str(state.get("reason", "") or ""),
        "since": float(state.get("since", 0) or 0),
    }
    _read_at = time.monotonic()


async def current(db: Any) -> dict[str, Any]:
    """The flag, from cache when it is fresh and from the database when not."""
    global _state, _read_at
    now = time.monotonic()
    if _read_at is not None and now - _read_at < CACHE_SECONDS:
        return dict(_state)
    try:
        state = await db.get_maintenance_mode()
    except Exception:
        # Unreachable database mid-update is not a reason to lock the world:
        # the engine refuses anything that matters anyway.
        return dict(_state)
    _state = dict(state)
    _read_at = now
    return dict(_state)


def refusal_text(state: dict[str, Any]) -> str:
    reason = str(state.get("reason") or "").strip()
    tail = f"\n> {reason}" if reason else ""
    return (
        "🔧 **The world is closed for maintenance.**\n"
        "An administrator is working on the server. Your cultivator and everything "
        "you have done are safe; try again in a few minutes." + tail
    )


def is_admin(user: Any) -> bool:
    """Administrator by live Discord permission, the way require_admin decides.

    Deliberately not a database role: the operator must stay able to reopen
    the world even if the database is the thing being worked on.
    """
    permissions = getattr(user, "guild_permissions", None)
    return bool(getattr(permissions, "administrator", False))


def exempt_command(name: str) -> bool:
    """`/admin ...` and its hub leaves are how maintenance is turned off."""
    return str(name or "").strip().startswith("admin")


async def refuse(db: Any, user: Any, *, command: str = "") -> str | None:
    """The refusal to print, or None when the door is open."""
    if is_admin(user) or exempt_command(command):
        return None
    state = await current(db)
    if not state.get("enabled"):
        return None
    return refusal_text(state)
