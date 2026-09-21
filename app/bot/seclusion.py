"""The doors stay shut, as the bot sees it (v1.0.0-rc.56).

`/cultivation → Cultivate → Seclusion` has told the player since v0.30.0 that
*"any state-changing command will remain locked until you use /cultivation →
Cultivate → End"* - a leaf actually labelled **Seclusion End**, which nobody
noticed because nothing was ever locked. What enforced it was half a gate, in
`runtime.py`'s `serialized_user_action`:

* it covered only the ~141 handlers wearing that decorator, so every **read**
  passed;
* its exemption was a **function-name prefix**, `seclusion_`, which is nothing
  structural - a future `seclusion_anything` would have inherited it;
* a **hub press** was already deferred by `_acknowledge_hub_action` before the
  wrapper ran, so the refusal took a different path from every other gate's;
* **typed play** and free narration never reached it at all;
* and it **settled first and checked second**, so the engine action ran before
  the refusal.

The engine now refuses every one of its ~150 authoritative player operations
behind a closed door (`checkPlayerSeclusionTx`), which is the backstop and
does not depend on this module behaving. What it cannot do is cover a read:
`/sheet`, `/quests` and every other card answer out of the presentation
layer's own SQL and never touch the engine's authoritative path.

This is the other half, and it is `maintenance.py`'s shape deliberately - one
rule, called at the four doors a player has. Three things differ, and each is
a decision:

* **An administrator is not exempt, but `/admin` is.** Maintenance exempts the
  person, because they are how the world reopens. A retreat is the player's
  own state, not the server's, so a GM in seclusion is in seclusion; what
  stays open is the `/admin` tree itself, so the server is still operable.
* **The allowlist is explicit.** Which reads stay open must be a decision
  written down, not an accident of which decorator a handler happens to wear -
  that accident is precisely what the old gate was.
* **Nothing is cached.** Maintenance caches a world-wide flag for three
  seconds; this is one player's own state and changes the moment they emerge,
  so a stale "still secluded" would be a worse bug than the one it prevents.
  The read is one indexed row by primary key.

The retreat ending itself is the engine's job, not this module's: the gate in
`checkPlayerSeclusionTx` settles and completes an expired retreat before
letting the action through, so a player is never locked out by a retreat whose
time is up. This module does not settle at all - the old gate did, ahead of
its own check, which is how an action could run before its refusal.
"""

from __future__ import annotations

import time
from typing import Any

# What a secluded cultivator may still reach, at both doors. The tree check is
# handed a slash command's qualified name and the panel gate a hub leaf's
# path, and the leading slash tells them apart - `is_open` below says why it
# must.
#
# **It is an explicit list on purpose.** The old gate's exemption was whether
# a handler happened to wear the `serialized_user_action` decorator, which is
# not a decision anybody made - it is the accident that let every read
# through. Which doors stay open behind a closed door is a decision, so it is
# written down and `test_seclusion_lockout.py` holds the hub half of it equal
# to the live definitions.

# A hub command draws a panel, which is a read - and every leaf inside it is
# checked again on the press, so the panel is where a secluded player finds
# the way out. Refusing the panel itself would hide that door.
HUB_ROOTS = frozenset({
    "abode", "admin", "ascend", "beast", "character", "combat", "craft", "cultivation",
    "economy", "family", "innerworld", "items", "npc", "realm", "sect", "travel", "world",
})

# The slash commands that only read. `begin` (creation), `action` (the guided
# intent picker) and `tribute` are deliberately absent: they act.
OPEN_COMMANDS = frozenset({"me", "menu", "quests", "cooldowns", "check", "locked"})

# The hub leaves that stay open: the way out, and the cards that only read.
# `/seclusion end` is a leaf rather than a slash command, so this is the name
# the panel gate sees; each is held to a live leaf by
# `test_seclusion_lockout.py`, so a rename cannot quietly close the only exit.
#
# Everything here shows the player their own state and changes nothing - which
# is the decision the old gate never made, because it exempted by decorator
# and let every read through by accident.
ALWAYS_OPEN = (
    "seclusion end",     # the way out
    "seclusion status",  # how much longer
    "sheet",             # their own cultivation sheet
    "inventory",
    "wallet",
    "effects",           # what is running on them
    "time",              # the world clock
    "lifespan",
)

# `/admin ...` and its hub leaves, so a GM who happens to be secluded can
# still operate the server. The engine has the same asymmetry by
# construction: every `admin.*` lever falls through to the switch in
# `ApplyWithWorld` rather than reaching the player gate.
ADMIN_PREFIX = "admin"


def _command_name(name: str) -> str:
    return " ".join(str(name or "").lstrip("/").split()).lower()


def is_open(command: str) -> bool:
    """Whether this door stays open behind a closed one.

    The leading slash is load-bearing, not decoration. `_invoke_action` passes
    a hub leaf's `path`, which always starts with one; the command tree passes
    a bare `qualified_name`. They collide - `/craft` is a leaf that resolves
    the craft roll *and* `craft` is a hub whose panel is a read - so the
    distinction has to be kept rather than normalised away.
    """
    raw = str(command or "").strip()
    if not raw:
        # A door that cannot say what it is opening is refused: an unnamed
        # action is a typed line or a free-narration message, which is play.
        return False
    leaf = raw.startswith("/")
    name = _command_name(raw)
    if name == ADMIN_PREFIX or name.startswith(ADMIN_PREFIX + " "):
        return True
    if any(name == allowed or name.startswith(allowed + " ") for allowed in ALWAYS_OPEN):
        return True
    if leaf:
        # Every other leaf acts. The panel holding it opened, which is where
        # the way out is drawn; the leaf itself does not.
        return False
    return name in OPEN_COMMANDS or name in HUB_ROOTS


def refusal_text(state: dict[str, Any], *, real_minutes_left: int | None = None) -> str:
    mode = str(state.get("mode") or "qi").upper()
    if real_minutes_left is None:
        tail = "The doors open when the planned retreat completes."
    elif real_minutes_left >= 60:
        tail = f"The doors open in about **{real_minutes_left // 60}h {real_minutes_left % 60}m**."
    else:
        tail = f"The doors open in about **{max(1, real_minutes_left)} minutes**."
    return (
        f"🚪 **You are in closed-door {mode} seclusion.**\n"
        f"{tail} Cultivation accrues while the doors are shut and is paid every completed "
        "world-hour.\n"
        "Everything under **/cultivation → Cultivate** is still open: **Seclusion End** "
        "to emerge early, **Seclusion Status** to see how far along you are."
    )


def _real_minutes_left(state: dict[str, Any]) -> int | None:
    """Minutes of wall clock remaining, or None for a retreat that has none.

    `ends_real_ts` is NULL for a retreat started before schema 57, which runs
    on the game clock instead; saying nothing is better than converting a
    world minute into a real one behind a rate the GM may have changed.
    """
    ends = state.get("ends_real_ts")
    if not ends:
        return None
    return max(0, int((float(ends) - time.time()) // 60))


async def active_session(db: Any, user_id: int) -> dict[str, Any] | None:
    """The player's open retreat, or None - including when the read fails.

    Fails open, for the same reason `maintenance.current` does: a gate that
    locks all play must fail towards play, and the engine refuses anything
    that matters anyway.
    """
    try:
        return await db.get_seclusion(int(user_id))
    except Exception:
        return None


async def refuse(db: Any, user_id: int, *, command: str = "") -> str | None:
    """The refusal to print, or None when the door is open."""
    if is_open(command):
        return None
    state = await active_session(db, user_id)
    if not state or str(state.get("status")) != "active":
        return None
    left = _real_minutes_left(state)
    if left is not None and left <= 0:
        # The deadline has passed; the engine's own gate settles and ends it
        # on the next action, so refusing here would be refusing a retreat
        # that is over. Let it through and let the engine close the books.
        return None
    return refusal_text(state, real_minutes_left=left)
