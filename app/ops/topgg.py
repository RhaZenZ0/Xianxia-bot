"""Top.gg: the one listing the engine can actually check.

`/vote` used to take the claim on trust, and said so in as many words - a vote
happens on someone else's website, and this deployment publishes nothing an
inbound webhook could reach. Top.gg's v1 API closes that gap without opening a
door: `GET /v1/projects/@me/votes/<user id>` is an *outbound* call, so the NAS
still publishes nothing and the twelve-hour claim stops being a guess.

Three facts about that API are load-bearing here and all three come from
top.gg's own Go SDK (`github.com/top-gg-community/go-sdk`, which is what the
published `go get github.com/top-gg/go-dbl` line actually resolves to) rather
than from memory:

1. the base is `https://top.gg/api` and the credential is a **project token**
   sent as `Authorization: Bearer <token>` - not the bare token the old v0 API
   took, and not an OAuth access token (OAuth is for applications that manage
   *other people's* projects, and it needs a public https redirect URI this
   deployment has not got);
2. the project is addressed as `@me` - the token names it - so there is no bot
   id to configure and no way to ask about a listing that is not yours;
3. a vote answer is a flat object with `expires_at`, and top.gg expires a vote
   at the same twelve hours the gift already renews on, which is why that field
   alone decides the verdict.

**The check fails open.** An unreachable top.gg, a rate limit, a timeout or a
token the operator has just rotated all return `UNKNOWN`, and `UNKNOWN` pays
the gift on trust exactly as it did before any of this existed. That is the
same ethic the narrator chain already runs on: a third party being down is not
a reason to take something away from a player who did nothing wrong, and the
engine's twelve-hour cooldown still bounds what an outage can cost. Only a
confident `NOT_VOTED` - top.gg answering, and saying there is no live vote -
refuses a claim.

Nothing here touches game state. The size of the gift, the cadence and the
receipt are the engine's (`support.vote_claim`); this module answers one
question and posts one number.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from ..version import RELEASE_VERSION

log = logging.getLogger("xianxia")

# Top.gg's own SDK constant. Kept here rather than in .env: an operator has no
# reason to point this somewhere else, and a mistyped base would turn every
# check into a silent UNKNOWN rather than a visible failure.
API_BASE = "https://top.gg/api"
USER_AGENT = f"xianxia-rp-bot/{RELEASE_VERSION} (+https://top.gg)"

# The four answers a check can give. UNCONFIGURED and UNKNOWN both mean "pay on
# trust"; they are kept apart so the panel and the log can tell an operator who
# has set no token from a top.gg that would not answer.
VOTED = "voted"
NOT_VOTED = "not_voted"
UNKNOWN = "unknown"
UNCONFIGURED = "unconfigured"

# Long enough for a slow round trip, short enough that a player pressing a
# button does not sit watching a spinner. A claim that times out is paid.
DEFAULT_TIMEOUT = 8.0


@dataclass(frozen=True)
class VoteCheck:
    """What top.gg said, in the shape the claim path needs.

    `trusted` is the question `/vote` actually asks: may this claim proceed?
    Everything except a confident NOT_VOTED says yes, because the module fails
    open. `verified` is the narrower one the receipt records - whether top.gg
    positively confirmed the vote - so the event ledger can tell a checked
    claim from one that was paid through an outage.
    """

    state: str
    expires_unix: int = 0
    weight: int = 0
    detail: str = ""

    @property
    def verified(self) -> bool:
        return self.state == VOTED

    @property
    def trusted(self) -> bool:
        return self.state != NOT_VOTED


def _expires_unix(payload: dict) -> int:
    """`expires_at` as a unix second, or 0 when it is absent or unreadable.

    Top.gg sends RFC 3339 with a `Z`; `fromisoformat` has accepted that since
    3.11, but a shape it cannot read must not take a claim down - an unreadable
    timestamp is simply no expiry, which the caller reads as "no live vote"
    rather than as an error.
    """
    raw = str(payload.get("expires_at") or "").strip()
    if not raw:
        return 0
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        log.warning("Top.gg sent an expires_at this bot cannot read: %r", raw[:60])
        return 0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp())


class TopggClient:
    """One project token, two calls: has this user voted, and how big are we.

    The client owns no state beyond its httpx session, and every public method
    swallows its own failures - a caller never has to wrap one in a try. That
    is deliberate: both callers are in paths (a player's button, a background
    worker) where a third party's bad day must not surface as an error.
    """

    def __init__(
        self,
        token: str,
        *,
        base_url: str = API_BASE,
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = (token or "").strip()
        self._base_url = base_url.rstrip("/")
        self._timeout = float(timeout)
        self._client = client
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self._token)

    async def _session(self) -> httpx.AsyncClient:
        # Built on first use rather than in __init__: Settings are read at
        # import time, and an AsyncClient made outside a running loop binds to
        # the wrong one.
        async with self._lock:
            if self._client is None:
                self._client = httpx.AsyncClient(
                    base_url=self._base_url,
                    timeout=httpx.Timeout(self._timeout),
                    headers={
                        "Authorization": f"Bearer {self._token}",
                        "User-Agent": USER_AGENT,
                        "Accept": "application/json",
                    },
                )
            return self._client

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                log.debug("Closing the Top.gg client failed", exc_info=True)

    async def vote_state(self, user_id: int) -> VoteCheck:
        """Whether `user_id` has a live vote on the configured project.

        Never raises. The verdict rests on `expires_at`: top.gg expires a vote
        twelve hours after it is cast, so a future expiry is a vote that still
        counts and a past one is a vote that has already been spent.
        """
        if not self.enabled:
            return VoteCheck(UNCONFIGURED, detail="no TOPGG_TOKEN is set")
        try:
            client = await self._session()
            response = await client.get(f"/v1/projects/@me/votes/{int(user_id)}")
        except Exception as exc:
            # Network, DNS, TLS, timeout: top.gg is not answering, so the claim
            # is paid on trust rather than refused for the player's ISP.
            log.warning("Top.gg vote check did not complete: %s: %s", type(exc).__name__, exc)
            return VoteCheck(UNKNOWN, detail=f"{type(exc).__name__}: {exc}"[:200])
        if response.status_code == 404:
            # The documented "no vote on record" answer, and a real verdict.
            return VoteCheck(NOT_VOTED, detail="top.gg has no vote on record")
        if response.status_code in (401, 403):
            # A bad, rotated or unscoped token. Loud, because it is the one
            # failure an operator must act on - and still fails open.
            log.error(
                "Top.gg refused the project token (%s). Check TOPGG_TOKEN; votes are "
                "being paid unverified until it is fixed.", response.status_code,
            )
            return VoteCheck(UNKNOWN, detail=f"token refused ({response.status_code})")
        if response.status_code >= 400:
            log.warning("Top.gg answered %s to a vote check", response.status_code)
            return VoteCheck(UNKNOWN, detail=f"HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            log.warning("Top.gg sent a vote check answer that is not JSON")
            return VoteCheck(UNKNOWN, detail="unreadable answer")
        if not isinstance(payload, dict):
            return VoteCheck(UNKNOWN, detail="unreadable answer")
        # Some answers nest the object; the SDK reads it flat, so flat wins and
        # the wrapper is only a fallback.
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        expires = _expires_unix(data)
        if expires <= 0:
            return VoteCheck(NOT_VOTED, detail="no live vote")
        if expires <= int(datetime.now(tz=timezone.utc).timestamp()):
            return VoteCheck(NOT_VOTED, expires_unix=expires, detail="the last vote has expired")
        try:
            weight = int(data.get("weight") or 1)
        except (TypeError, ValueError):
            weight = 1
        return VoteCheck(VOTED, expires_unix=expires, weight=weight)

    async def post_metrics(self, *, server_count: int, shard_count: int | None = None) -> bool:
        """Tell the listing how big the bot is. True when top.gg took it.

        Only the counts this bot actually knows are sent. `MetricsPayload`
        carries member/online/player counts too, but they belong to server and
        game listings; sending them as zeros would publish a zero over
        whatever the listing already shows.
        """
        if not self.enabled:
            return False
        body: dict[str, int] = {"server_count": max(0, int(server_count))}
        if shard_count is not None:
            body["shard_count"] = max(1, int(shard_count))
        try:
            client = await self._session()
            response = await client.patch("/v1/projects/@me/metrics", json=body)
        except Exception as exc:
            log.warning("Top.gg metrics post did not complete: %s: %s", type(exc).__name__, exc)
            return False
        if response.status_code in (401, 403):
            log.error("Top.gg refused the project token (%s) on a metrics post.", response.status_code)
            return False
        if response.status_code >= 400:
            log.warning("Top.gg answered %s to a metrics post", response.status_code)
            return False
        return True
