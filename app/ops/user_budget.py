"""Per-user token bucket for player-typed input (typed play, v0.21.1).

The narrator's daily allowance is shared by the whole server (README, "Free-tier
budget"): 50 free requests a day under $10 of credits. ``serialized_user_action``
in ``app/bot/runtime.py`` serialises one player's commands but never throttles
them, and ``on_message`` had no guard at all - so with ``AUTO_NARRATE=true`` one
player pasting paragraphs could spend everyone's allowance before noon.

This is the "per-user command budget" the 1.0 roadmap files under v0.23
Hardened I, pulled forward because typed play multiplies the number of lines
that can reach the engine or a prompt. It is deliberately dependency-free (no
discord, no settings import) so it can be unit-tested on its own and reused by
the slash and hub paths later.

Semantics: a classic token bucket. ``burst`` tokens are available immediately;
they refill at ``per_minute`` tokens a minute up to ``burst``. ``try_acquire``
never waits - a refused line is answered, not queued, because a player should
not be left waiting on a queue that the upstream limit will refuse anyway.

Idle users are evicted so the table does not grow without bound (the same
unbounded-growth problem ``_USER_ACTION_LOCKS`` has and v0.23 lists).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class _Bucket:
    tokens: float
    updated: float


@dataclass
class UserBudget:
    burst: int = 4
    per_minute: float = 6.0
    idle_evict_seconds: float = 1800.0
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    _buckets: dict[int, _Bucket] = field(default_factory=dict, repr=False)
    granted: int = 0
    refused: int = 0

    def __post_init__(self) -> None:
        self.burst = max(1, int(self.burst))
        self.per_minute = float(self.per_minute)
        if self.per_minute <= 0:
            raise ValueError("per_minute must be positive")

    def _refill(self, bucket: _Bucket, now: float) -> None:
        elapsed = max(0.0, now - bucket.updated)
        bucket.tokens = min(float(self.burst), bucket.tokens + elapsed * (self.per_minute / 60.0))
        bucket.updated = now

    def _evict_idle(self, now: float) -> None:
        cutoff = now - self.idle_evict_seconds
        stale = [uid for uid, b in self._buckets.items() if b.updated <= cutoff]
        for uid in stale:
            del self._buckets[uid]

    def try_acquire(self, user_id: int) -> bool:
        """Spend one token for ``user_id`` if one is available. Never blocks."""
        now = self.clock()
        self._evict_idle(now)
        bucket = self._buckets.get(int(user_id))
        if bucket is None:
            bucket = _Bucket(tokens=float(self.burst), updated=now)
            self._buckets[int(user_id)] = bucket
        else:
            self._refill(bucket, now)
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            self.granted += 1
            return True
        self.refused += 1
        return False

    def seconds_until_token(self, user_id: int) -> float:
        """How long until ``user_id`` could acquire again; 0 if they can now."""
        bucket = self._buckets.get(int(user_id))
        if bucket is None:
            return 0.0
        now = self.clock()
        self._refill(bucket, now)
        if bucket.tokens >= 1.0:
            return 0.0
        return (1.0 - bucket.tokens) / (self.per_minute / 60.0)

    def snapshot(self) -> dict[str, Any]:
        return {
            "burst": self.burst,
            "per_minute": self.per_minute,
            "tracked_users": len(self._buckets),
            "granted": self.granted,
            "refused": self.refused,
        }
