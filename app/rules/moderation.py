"""Moderation strings (v0.32.0): how a GM writes a duration and how a hold
reads back. Pure, so the Discord commands and their gate test share one
reading of ``2h`` and one line for "what holds on this character".

The rule itself - what a flag with an expiry means, when it lapses - is the
engine's (go_core/internal/game/moderation.go). This module only formats
and parses at the Discord edge; it decides nothing.
"""
from __future__ import annotations

import re
import time
from typing import Any

_DURATION_UNIT_SECONDS = {"w": 7 * 86400, "d": 86400, "h": 3600, "m": 60}
_DURATION_PART = re.compile(r"(\d+)\s*([wdhm])")
MAX_DURATION_SECONDS = 366 * 86400


def parse_duration_seconds(text: str | None) -> int:
    """Read a GM's duration - ``30m``, ``2h``, ``1d``, ``1w`` or a run of
    them such as ``1h30m`` - as seconds. Empty, ``0`` and ``forever`` mean no
    expiry. Anything else raises ValueError, so a typo never silently becomes
    an indefinite mute.
    """
    raw = (text or "").strip().lower().replace(" ", "")
    if raw in {"", "0", "forever", "indefinite", "none"}:
        return 0
    parts = _DURATION_PART.findall(raw)
    if not parts or "".join(f"{n}{u}" for n, u in parts) != raw:
        raise ValueError("duration must look like 30m, 2h, 1d, 1w or 1h30m (leave it empty for no expiry)")
    total = sum(int(n) * _DURATION_UNIT_SECONDS[u] for n, u in parts)
    if total <= 0:
        return 0
    if total > MAX_DURATION_SECONDS:
        raise ValueError("duration is limited to one year; leave it empty for an indefinite moderation")
    return total


def moderation_summary(character: dict[str, Any] | None, *, now: float | None = None) -> str:
    """One line for /admin player inspect: what holds on this character and
    until when. A lapsed flag the tick has not cleared yet reads as over, the
    same way the engine reads it."""
    c = dict(character or {})
    at = time.time() if now is None else float(now)
    parts: list[str] = []
    if int(c.get("is_banned") or 0):
        parts.append("**banned**")
    for flag, until, label in (("is_frozen", "frozen_until", "frozen"), ("is_muted", "muted_until", "muted")):
        if not int(c.get(flag) or 0):
            continue
        expiry = float(c.get(until) or 0)
        if 0 < expiry <= at:
            continue
        parts.append(f"**{label}**" + (f" until <t:{int(expiry)}:R>" if expiry > 0 else " until lifted"))
    if not parts:
        return "none"
    reason = str(c.get("moderation_reason") or "").strip()
    return " • ".join(parts) + (f" — {reason}" if reason else "")
