from __future__ import annotations

import asyncio
import logging
import re
import ssl
import time
from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable

from openai import AsyncOpenAI

from app.ai.google_route import (
    AISTUDIO_PREFIX,
    DEFAULT_GOOGLE_MODEL,
    GoogleAIStudioRoute,
    is_aistudio_route,
)

log = logging.getLogger("xianxia.ai_router")


class NarrationTier(StrEnum):
    ROUTINE = "routine"
    EPIC = "epic"


# v0.25.3 defaults. Gemma 4 31B stays primary on both tiers: its :free route
# is served by Google AI Studio alone, whose shared pool 429s per user, so it
# is only a good primary when the operator has added their own AI Studio key
# on OpenRouter's Integrations page (then it runs on the operator's own, far
# larger, quota). The fallbacks are the best multi-provider / high-uptime
# prose-capable models on the free catalogue (September 2026). Nemotron 3
# Super is gone: it narrated its own instructions 3 of 3 times in production.
# MiniMax M3 is gone for the same reason (v0.25.3): it is reasoning-native,
# ignores `reasoning.enabled=false`, and returned its scratchpad as `content`
# often enough that the routine chain's second hop was effectively dead
# ("ScratchpadResponse: AI response was reasoning scratchpad, not narration").
# GLM 5.2 already served the epic tier's second hop, so both tiers now share it.
# GLM 5.2 is gone too (v0.26.1), for a different reason: OpenRouter withdrew the
# `:free` variant and now answers it with `404 - This model is unavailable for
# free. The paid version is available now - use this slug instead: z-ai/glm-5.2`.
# The paid slug cannot take its place because OPENROUTER_REQUIRE_FREE rejects it,
# so the hop failed on every narration while still costing a daily free-tier
# slot. There is deliberately no replacement literal: a named free slug is only
# as good as OpenRouter's catalogue on the day it is written, and this is the
# second one to die under a shipped default. The second hop is now empty by
# default and `openrouter/free` - OpenRouter's own dynamic free-model router,
# which resolves to whatever is actually free at call time - carries the tier.
# An operator who wants a named second hop sets OPENROUTER_*_FALLBACK_MODEL.
DEFAULT_ROUTINE_MODEL = "google/gemma-4-31b-it:free"
DEFAULT_ROUTINE_FALLBACK_MODEL = ""
DEFAULT_EPIC_MODEL = DEFAULT_ROUTINE_MODEL
DEFAULT_EPIC_FALLBACK_MODEL = ""
DEFAULT_DYNAMIC_FREE_MODEL = "openrouter/free"

# Reasoning is switched OFF on every narration request by default. Both
# production failures of the free chain came from reasoning: models spending a
# 180-token budget on thinking and returning empty content (v0.19.20), and a
# model returning its thinking AS the content (v0.19.36). OpenRouter's unified
# `reasoning` parameter turns it off on models that have it and is ignored by
# models that do not, so this is safe to send to every route.
REASONING_OFF: dict[str, Any] = {"reasoning": {"enabled": False, "exclude": True}}

# v0.27.0: the daily liveness probe. The cheapest request the API will accept -
# one character in, one token out - because the probe never reads the reply. The
# failures it exists to catch (a withdrawn slug, a rejected key, an exhausted
# quota, an unreachable host) all arrive as exceptions, so "did the call return"
# is the whole verdict and content is irrelevant. That is also why a route that
# answers a probe is only known to be REACHABLE, not fit to narrate: MiniMax M3
# would have passed this every time. _validate_generated_text remains the only
# judge of whether a reply is usable prose.
PROBE_PROMPT = "."
PROBE_MAX_TOKENS = 1
PROBE_TIMEOUT_SECONDS = 20.0

# Only a durable rejection retires a route until the next audit. A 429 or a
# timeout says "not now" - the per-route cooldown and backoff already handle
# that, and standing a route down for a day over congestion would throw away a
# route that works fine an hour later. 401/403/404 say "not ever, as configured":
# that is the GLM 5.2 withdrawal and the rejected AI Studio key.
PROBE_DURABLE_STATUSES = frozenset({401, 403, 404})

# 400 is not a verdict. It is a family of causes - a rejected parameter, a
# provider minimum on `max_tokens`, a context overflow, a malformed body, a
# moderation filter - and exactly one of them ("this endpoint will not accept
# REASONING_OFF") is a reason to stand a route down. The probe differs from a
# narration call in two ways at once, so a single confirmation cannot say which
# one the provider objected to; `_classify_bad_request` therefore asks twice,
# changing ONE variable each time. See that method for the ladder.
#
# This is NOT a scratchpad detector. MiniMax M3 accepted REASONING_OFF, returned
# 200 and put its reasoning in `content` anyway (v0.25.3); no probe sees that.
# _validate_generated_text remains the only thing that catches it.
PROBE_REASONING_REJECTED_STATUS = 400
PROBE_CONFIRM_MAX_TOKENS = 64

# Classifications recorded on the model row for the admin panel. Only
# PROBE_400_REASONING retires anything; the rest exist so an operator can see
# WHY a 400 was left alone instead of having to guess from a raw error string.
PROBE_400_REASONING = "reasoning-rejected"
PROBE_400_TOKEN_BUDGET = "token-budget"
PROBE_400_OTHER_STATUS = "other-status"
PROBE_400_UNCLASSIFIED = "unclassified"
PROBE_400_UNCONFIRMED = "unconfirmed"
PROBE_400_NO_PARAMETER = "no-reasoning-parameter"

# Diagnostics never outrank play. The audit spends real free-tier slots, so it
# runs only while most of the day's budget is still unspent; below this it is
# skipped entirely and the chain keeps whatever verdicts it already has.
PROBE_BUDGET_HEADROOM = 0.5

_PROMPT_LEAK_PATTERNS = (
    re.compile(r"\bsystem prompt\b", re.I),
    re.compile(r"\bdeveloper message\b", re.I),
    re.compile(r"\bcanonical context block\b", re.I),
    re.compile(r"\bpython engine\b", re.I),
    re.compile(r"\bgo engine\b", re.I),
    re.compile(r"\bgame engine\b", re.I),
    re.compile(r"\bhidden instructions?\b", re.I),
)


# openai 3.x replaced httpx with HTTPX2, which verifies TLS against the operating
# system trust store rather than certifi.  If that store is thin or missing, every
# route fails - and because narration is descriptive only, nothing raises: play
# continues on procedural prose and the failure is invisible to an operator.  The
# Dockerfile installs and asserts the CA bundle so this should never happen, but
# "should never happen" is exactly the class of thing that deserves a named
# counter rather than a shrug.
_TLS_ERROR_PATTERN = re.compile(
    r"\bssl\b|\btls\b|certificate|CERTIFICATE_VERIFY_FAILED|self[- ]signed"
    r"|unable to get local issuer|CA bundle|trust store",
    re.I,
)


def _error_status(exc: BaseException | None) -> int | None:
    """Pull an HTTP status off a client exception, whichever SDK raised it.

    The openai SDK carries it on `status_code` (and again on `.response`).
    google-genai collapses EVERY 4xx into a bare `ClientError` - there is no
    BadRequestError, no AuthenticationError, no RateLimitError - and puts the
    number on `.code` instead. Reading only `status_code` therefore made a
    rejected AI Studio key look like "no status at all", which is precisely the
    case PROBE_DURABLE_STATUSES was written for.

    `.code` is checked last and only when it is a real int: the openai SDK also
    has a `.code`, but its value is a symbolic string ("invalid_api_key"), and
    bool is excluded because it is an int subclass.
    """
    for candidate in (
        getattr(exc, "status_code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
        getattr(exc, "code", None),
    ):
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            return candidate
    return None


def _looks_like_tls_failure(exc: BaseException | None) -> bool:
    """Walk the exception chain looking for a certificate/TLS problem.

    A timeout is never one. Cancelling a request that is mid-handshake leaves
    the half-finished SSL exception in the chain as context, which otherwise
    reads here as a broken trust store and sends the operator to check
    `ca-certificates` for what is actually an unreachable or slow upstream. A
    genuinely bad trust store raises the SSL error itself, not a timeout.
    """
    if isinstance(exc, (TimeoutError, asyncio.CancelledError)):
        return False
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ssl.SSLError):
            return True
        if _TLS_ERROR_PATTERN.search(type(current).__name__):
            return True
        if _TLS_ERROR_PATTERN.search(str(current)):
            return True
        current = current.__cause__ or current.__context__
    return False


@dataclass(frozen=True)
class RoutedAIResult:
    text: str
    tier: NarrationTier
    model: str
    attempted_models: tuple[str, ...]


class RouteLimiter:
    """Per-model rate window. The provider caps are per MODEL, not per account.

    The account-wide limiter below counts every route together, which cannot
    express what the providers actually enforce: Google allows Gemma 4 roughly
    15 requests/minute and 1500/day PER MODEL. One global ceiling of 20/min is
    therefore simultaneously too high for a single route (15) and too low for the
    fleet (two Gemma routes = 30), and production showed exactly that - both
    Gemma models cooling down independently while the account limiter had refused
    nothing at all.

    Defaults are the documented Gemma free-tier figures, which are correct in
    both regimes: on OpenRouter's shared free pool the account-wide daily cap
    binds first and these never fire, and on a BYOK provider key these are the
    real ceiling.
    """

    def __init__(self, model: str, *, per_minute: int = 15, per_day: int = 1500) -> None:
        self.model = str(model)
        self.per_minute = max(1, int(per_minute))
        self.per_day = max(1, int(per_day))
        self._minute: deque[float] = deque()
        self._day: deque[float] = deque()
        self.refused_minute = 0
        self.refused_day = 0

    def _trim(self, now: float) -> None:
        while self._minute and self._minute[0] <= now - 60.0:
            self._minute.popleft()
        while self._day and self._day[0] <= now - 86400.0:
            self._day.popleft()

    def try_acquire(self) -> bool:
        now = time.monotonic()
        self._trim(now)
        if len(self._day) >= self.per_day:
            self.refused_day += 1
            return False
        if len(self._minute) >= self.per_minute:
            self.refused_minute += 1
            return False
        self._minute.append(now)
        self._day.append(now)
        return True

    def snapshot(self) -> dict[str, Any]:
        self._trim(time.monotonic())
        used_minute, used_day = len(self._minute), len(self._day)
        # Which ceiling this route is closest to, so the panel can say so rather
        # than leaving an operator to work it out from two ratios.
        minute_share = used_minute / self.per_minute
        day_share = used_day / self.per_day
        return {
            "per_minute": self.per_minute,
            "per_day": self.per_day,
            "used_minute": used_minute,
            "used_day": used_day,
            "refused_minute": self.refused_minute,
            "refused_day": self.refused_day,
            "binding": "day" if day_share >= minute_share else "minute",
            "headroom_percent": round(100.0 * (1.0 - max(minute_share, day_share)), 1),
        }


class OpenRouterRequestLimiter:
    """Fail-fast rolling request limiter for the shared OpenRouter account.

    The narrator should never make Discord users wait for a local retry queue when
    the free account limit is already saturated. When the local cap is reached,
    narration falls through to the procedural safety net immediately.
    """

    def __init__(
        self, max_requests_per_minute: int = 20, max_requests_per_day: int = 50
    ) -> None:
        self.max_requests = max(1, int(max_requests_per_minute))
        self.window_seconds = 60.0
        # OpenRouter's free tier is 20 requests/minute AND 50 requests/day under
        # $10 of lifetime credits (1000/day at $10 or more). Only the per-minute
        # half was ever tracked, so in production 11 narrations quietly cost 15
        # upstream attempts against a 50/day allowance - every failed route walks
        # to the next one, and each walk spends a slot.
        #
        # This counter is a COST SAVER, not an authority: it lives in memory and
        # resets when the bot restarts, while the real limit does not. Upstream
        # remains the source of truth; this just stops us paying for requests we
        # already know will be refused.
        self.max_requests_per_day = max(1, int(max_requests_per_day))
        self.day_seconds = 86400.0
        self._timestamps: deque[float] = deque()
        self._daily: deque[float] = deque()
        self._lock = asyncio.Lock()
        # Monitoring counters.  These are the only honest signal that the free
        # account ceiling - rather than a model failure - is what pushed players
        # onto procedural prose, so /admin has to be able to read them.
        self.granted = 0
        self.rejected = 0
        self.daily_rejected = 0

    async def try_acquire(self) -> bool:
        now = time.monotonic()
        async with self._lock:
            cutoff = now - self.window_seconds
            while self._timestamps and self._timestamps[0] <= cutoff:
                self._timestamps.popleft()
            day_cutoff = now - self.day_seconds
            while self._daily and self._daily[0] <= day_cutoff:
                self._daily.popleft()
            if len(self._daily) >= self.max_requests_per_day:
                self.rejected += 1
                self.daily_rejected += 1
                return False
            if len(self._timestamps) >= self.max_requests:
                self.rejected += 1
                return False
            self._timestamps.append(now)
            self._daily.append(now)
            self.granted += 1
            return True

    def snapshot(self) -> dict[str, Any]:
        return {
            "max_requests_per_minute": self.max_requests,
            "in_window": len(self._timestamps),
            "granted": self.granted,
            "rejected": self.rejected,
            "max_requests_per_day": self.max_requests_per_day,
            "used_today": len(self._daily),
            "remaining_today": max(0, self.max_requests_per_day - len(self._daily)),
            "daily_rejected": self.daily_rejected,
            "daily_exhausted": len(self._daily) >= self.max_requests_per_day,
        }


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, str):
                chunks.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    chunks.append(str(text))
            else:
                text = getattr(item, "text", None)
                if text:
                    chunks.append(str(text))
        return "\n".join(chunks).strip()
    return str(content or "").strip()


# Free-model pools are increasingly reasoning models. Given a 180-token budget -
# what routine narration asks for - such a model spends the whole allowance on
# reasoning tokens and returns an EMPTY content field, which is exactly what
# openrouter/free did 6 times out of 6 in production. The primary fix is room to
# answer; reading the reasoning field is the salvage path, not the plan.
DYNAMIC_FREE_MIN_OUTPUT_TOKENS = 700

# Escalating cooldown for a route that keeps failing (see _mark_failure).
MAX_BACKOFF_DOUBLINGS = 5            # 60s x 2^5 = 32 minutes at the fifth miss
MAX_FAILURE_COOLDOWN_SECONDS = 1800.0

_REASONING_FIELDS = ("reasoning", "reasoning_content")

# Scratchpad tells on itself. Narration that starts "Okay, the user wants..." is
# worse than the deterministic procedural fallback, so text that reads as
# thinking-out-loud is discarded rather than shown to a player.
_SCRATCHPAD_PATTERNS = (
    re.compile(r"^\s*(okay|ok|alright|so|hmm|right)\b[,.]", re.I),
    re.compile(r"\bthe (user|player|prompt|request|instructions?)\b", re.I),
    re.compile(r"\b(i|we) (should|need to|must|will|can) \b", re.I),
    re.compile(r"\blet(?:'s| us| me)\b", re.I),
    re.compile(r"\bas an ai\b", re.I),
    re.compile(r"\b(system|developer) (prompt|message)\b", re.I),
    # v0.19.36: a model posted "Here's a thinking process: 1. Analyze User
    # Input: Scene type: ... Player action: "Discover roads" (untrusted
    # fictional action) ..." as the narration of a player's expedition. None
    # of the patterns above matched it, and until that release this check was
    # only run on the reasoning-salvage path, never on `content` itself.
    re.compile(r"\b(thinking|thought|reasoning) process\b", re.I),
    re.compile(r"\banaly[sz](e|ing) (the )?(user('s)? )?(input|request|prompt)\b", re.I),
    re.compile(r"\bidentify (the )?key elements\b", re.I),
    re.compile(r"\buntrusted fictional action\b", re.I),
    re.compile(r"\b(scene type|fixed roll information|canonical context)\s*:", re.I),
    re.compile(r"^\s*(here'?s|here is) (my|a|the) (thinking|reasoning|thought|plan|analysis)", re.I | re.M),
    re.compile(r"^\s*\d+\.\s*\*\*[^*\n]{3,60}\*\*\s*:", re.M),  # "1. **Analyze User Input:**"
)

_THINKING_BLOCK_RE = re.compile(r"<\s*(think|thinking|reasoning|scratchpad)\s*>.*?<\s*/\s*\1\s*>", re.I | re.S)
_UNCLOSED_THINKING_RE = re.compile(r"^\s*<\s*(think|thinking|reasoning|scratchpad)\s*>.*$", re.I | re.S)
# A model that thinks out loud and then labels its answer. The LAST such label
# wins; everything before it is discarded.
_FINAL_SEGMENT_RE = re.compile(
    r"(?im)^\s*(?:#{1,4}\s*|\*\*|__)?\s*(?:final (?:narration|answer|response|output|prose)|narration|response|output|answer)"
    r"\s*(?:\*\*|__)?\s*:\s*(?:\*\*|__)?\s*"
)


def _looks_like_scratchpad(text: str) -> bool:
    return any(pattern.search(text) for pattern in _SCRATCHPAD_PATTERNS)


def _strip_thinking_blocks(text: str) -> str:
    """Drop <think>...</think>-style blocks; an unclosed one swallows the rest."""
    value = _THINKING_BLOCK_RE.sub("", str(text or ""))
    value = _UNCLOSED_THINKING_RE.sub("", value)
    return value.strip()


def _salvage_narration(text: str) -> str:
    """Best effort at the prose inside a reply that reads as thinking-out-loud.

    Returns "" when nothing survives - the caller then treats the reply as a
    failed route (next model, then the procedural fallback), because a player
    reading the model's analysis of their own action is worse than no prose.
    """
    value = _strip_thinking_blocks(text)
    if value and not _looks_like_scratchpad(value):
        return value
    matches = list(_FINAL_SEGMENT_RE.finditer(value))
    if matches:
        tail = value[matches[-1].end():].strip()
        if tail and not _looks_like_scratchpad(tail):
            return tail
    return ""


def _extract_reasoning(message: Any) -> str:
    """Pull prose out of a reasoning field, or return "" if it reads as thinking."""
    for field in _REASONING_FIELDS:
        value = getattr(message, field, None)
        if value is None and isinstance(message, dict):
            value = message.get(field)
        text = _extract_text(value)
        if text and not _looks_like_scratchpad(text):
            return text
    details = getattr(message, "reasoning_details", None)
    if details is None and isinstance(message, dict):
        details = message.get("reasoning_details")
    if isinstance(details, (list, tuple)):
        chunks = []
        for item in details:
            piece = item.get("text") or item.get("summary") if isinstance(item, dict) else getattr(item, "text", None)
            if piece:
                chunks.append(str(piece))
        text = "\n".join(chunks).strip()
        if text and not _looks_like_scratchpad(text):
            return text
    return ""


def _response_provider(response: Any) -> str:
    """OpenRouter puts the upstream that served the request in a top-level
    `provider` field on the completion. The OpenAI SDK keeps unknown fields
    as extras, so it is reachable by attribute or via model_extra."""
    value = getattr(response, "provider", None)
    if value is None:
        extra = getattr(response, "model_extra", None)
        if isinstance(extra, dict):
            value = extra.get("provider")
    return str(value or "").strip()


BYOK_RECHECK_SECONDS = 3600.0


def _retry_after_seconds(exc: Exception) -> float | None:
    """Read the Retry-After hint OpenRouter sends when a provider gives one.

    A blanket cooldown is a guess. In production it parked both Gemma routes for
    60 seconds each and produced "skipped while cooling 6" on both, turning a
    transient provider blip into a minute of procedural prose.
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        raw = headers.get("retry-after") or headers.get("Retry-After")
    except Exception:
        return None
    if raw is None:
        return None
    try:
        seconds = float(str(raw).strip())
    except (TypeError, ValueError):
        # HTTP-date form. Not worth a date parser here; fall back to the default.
        return None
    if seconds <= 0:
        return None
    return min(300.0, seconds)


def _is_free_route(model: str) -> bool:
    chosen = str(model or "").strip()
    return chosen.endswith(":free") or chosen == DEFAULT_DYNAMIC_FREE_MODEL


def _safe_free_model(model: str, *, require_free: bool, allow_empty: bool = False) -> str:
    chosen = str(model or "").strip()
    if not chosen:
        # Empty is a real answer for a fallback slot - "this tier has no second
        # hop, go straight to the dynamic free router" - but never for a primary
        # or for the dynamic route itself, where it can only be a misconfiguration.
        if allow_empty:
            return ""
        raise ValueError("OpenRouter model cannot be empty")
    # OPENROUTER_REQUIRE_FREE is a statement about OpenRouter's catalogue: it
    # exists so a paid OpenRouter model cannot be configured by accident. A
    # direct AI Studio route does not go through OpenRouter at all and is
    # billed - or, on the free tier, not billed - by Google against the
    # operator's own key, so the guard has nothing to say about it.
    if is_aistudio_route(chosen):
        return chosen
    if require_free and not _is_free_route(chosen):
        raise ValueError(f"OpenRouter narrator requires a free route, got: {chosen}")
    return chosen


class ScratchpadResponse(ValueError):
    """The model answered with its own reasoning instead of narration."""


def _validate_generated_text(text: str, *, max_chars: int = 7000, leak_guard: bool = True) -> str:
    """Validate model output.

    ``leak_guard`` exists because the prompt-leak patterns below are a
    *player-facing* protection: narration must never mention the system prompt
    or the Go/Python engines.  Administrator-facing analysis (the chat monitor)
    is read only by someone who already has administrator on the guild, and it
    routinely has to say things like "the game engine returned an error" when
    summarising a bug report.  Running the player guard over that text made the
    monitor fail on exactly the reports it exists to surface.  Narration keeps
    the default; only the GM report opts out.
    """
    value = str(text or "").strip()
    if not value:
        raise ValueError("AI provider returned an empty response")
    if leak_guard and any(pattern.search(value) for pattern in _PROMPT_LEAK_PATTERNS):
        raise ValueError("AI response exposed implementation or prompt details")
    if value.startswith("```") and value.endswith("```"):
        value = re.sub(r"^```(?:text|markdown|md)?\s*", "", value, flags=re.I)
        value = re.sub(r"\s*```$", "", value).strip()
    if leak_guard:
        # The scratchpad check used to run only when `content` was empty and
        # the reasoning field was being salvaged. A reasoning model that puts
        # its thinking IN the content field sailed straight through here and
        # into a player's expedition thread (v0.19.36).
        stripped = _strip_thinking_blocks(value)
        if not stripped:
            raise ValueError("AI response was a reasoning block with no narration")
        if _looks_like_scratchpad(stripped):
            salvaged = _salvage_narration(stripped)
            if not salvaged:
                raise ScratchpadResponse("AI response was reasoning scratchpad, not narration")
            value = salvaged
        else:
            value = stripped
    if len(value) > max_chars:
        value = value[: max_chars - 1].rstrip() + "…"
    return value


def _google_validated(raw_text: str, leak_guard: bool, row: dict[str, Any]) -> str:
    """Run the shared output guard over a direct AI Studio reply.

    Broken out only so the counter bookkeeping on a rejected scratchpad is
    identical on both transports - `scratchpad_rejected` is what tells an
    administrator that a route is answering with its own reasoning, and a route
    that skipped it would look healthy while narrating nothing.
    """
    try:
        return _validate_generated_text(raw_text, leak_guard=leak_guard)
    except ScratchpadResponse:
        row["scratchpad_rejected"] += 1
        log.info("AI_SCRATCHPAD_REJECTED transport=aistudio")
        raise


def _dedupe_chain(models: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for model in models:
        value = str(model or "").strip()
        if value and value not in seen:
            seen.add(value)
            ordered.append(value)
    return tuple(ordered)


class AITaskRouter:
    """Read-only OpenRouter narrator router with cloud-only free fallbacks.

    Routine narration:
      Gemma 4 31B free -> Gemma 4 26B A4B free -> openrouter/free

    Epic narration:
      Nemotron 3 Super free -> Gemma 4 31B free -> openrouter/free

    The router never resolves mechanics and never writes canonical state. If every
    cloud route fails, Narrator catches the error and returns deterministic
    procedural prose.
    """

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str = "https://openrouter.ai/api/v1",
        routine_model: str = DEFAULT_ROUTINE_MODEL,
        routine_fallback_model: str = DEFAULT_ROUTINE_FALLBACK_MODEL,
        epic_model: str = DEFAULT_EPIC_MODEL,
        epic_fallback_model: str = DEFAULT_EPIC_FALLBACK_MODEL,
        dynamic_free_model: str = DEFAULT_DYNAMIC_FREE_MODEL,
        google_api_key: str | None = None,
        google_model: str = DEFAULT_GOOGLE_MODEL,
        require_free: bool = True,
        max_requests_per_minute: int = 20,
        max_requests_per_day: int = 50,
        route_requests_per_minute: int = 15,
        route_requests_per_day: int = 1500,
        routine_timeout_seconds: float = 30.0,
        epic_timeout_seconds: float = 60.0,
        failure_cooldown_seconds: float = 20.0,
        disable_reasoning: bool = True,
        app_url: str = "",
        app_name: str = "Xianxia RP",
    ) -> None:
        self.api_key = str(api_key or "").strip() or None
        self.base_url = str(base_url or "https://openrouter.ai/api/v1").rstrip("/")
        self.require_free = bool(require_free)
        self.routine_timeout_seconds = max(5.0, float(routine_timeout_seconds))
        self.epic_timeout_seconds = max(5.0, float(epic_timeout_seconds))
        self.failure_cooldown_seconds = max(1.0, float(failure_cooldown_seconds))
        self.disable_reasoning = bool(disable_reasoning)
        self.app_url = str(app_url or "").strip()
        self.app_name = str(app_name or "Xianxia RP").strip() or "Xianxia RP"

        routine = _safe_free_model(routine_model, require_free=self.require_free)
        routine_fallback = _safe_free_model(
            routine_fallback_model, require_free=self.require_free, allow_empty=True
        )
        epic = _safe_free_model(epic_model, require_free=self.require_free)
        epic_fallback = _safe_free_model(
            epic_fallback_model, require_free=self.require_free, allow_empty=True
        )
        dynamic = _safe_free_model(dynamic_free_model, require_free=self.require_free)
        self.dynamic_free_model = dynamic

        # v0.26.0: the operator's own Google AI Studio key, called directly.
        # It leads both chains when configured because it draws on Google's own
        # free-tier quota rather than OpenRouter's ~50-a-day budget, so a call
        # here is the one narration that costs the shared allowance nothing.
        # With no key - the default - nothing changes: the route is not built,
        # not in the chains, and not in ai_status.
        self.google_route = GoogleAIStudioRoute(google_api_key)
        google = ""
        if self.google_route.configured:
            # The prefix is checked BEFORE the free-route guard so a missing
            # prefix reports the actual mistake. Without this order the guard
            # gets there first and blames OpenRouter for a Google model id.
            google = str(google_model or "").strip()
            if not is_aistudio_route(google):
                raise ValueError(
                    f"The Google AI Studio model must start with {AISTUDIO_PREFIX!r}, got: {google!r}"
                )
            google = _safe_free_model(google, require_free=self.require_free)
        self.google_model = google

        lead = (google,) if google else ()
        self.chains = {
            NarrationTier.ROUTINE: _dedupe_chain((*lead, routine, routine_fallback, dynamic)),
            NarrationTier.EPIC: _dedupe_chain((*lead, epic, epic_fallback, dynamic)),
        }
        self.limiter = OpenRouterRequestLimiter(max_requests_per_minute, max_requests_per_day)
        self._cooldown_until: dict[str, float] = {}
        self._started_at = time.time()
        self.route_requests_per_minute = max(1, int(route_requests_per_minute))
        self.route_requests_per_day = max(1, int(route_requests_per_day))
        self._route_limiters: dict[str, RouteLimiter] = {}
        self._tls_failures = 0
        self._tls_warned = False
        self._model_stats: dict[str, dict[str, Any]] = {}
        self._last_audit: dict[str, Any] = {}
        self._tier_stats: dict[str, dict[str, int]] = {
            tier.value: {"requests": 0, "served": 0, "exhausted": 0, "rate_limited": 0}
            for tier in NarrationTier
        }
        self.client = (
            AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=max(self.routine_timeout_seconds, self.epic_timeout_seconds),
            )
            if self.api_key
            else None
        )

    @property
    def enabled(self) -> bool:
        # Either transport on its own is enough to narrate. An operator with a
        # Google AI Studio key and no OpenRouter key is a supported setup.
        return self.client is not None or self.google_route.available

    @property
    def label(self) -> str:
        routine = " -> ".join(self.chains[NarrationTier.ROUTINE])
        epic = " -> ".join(self.chains[NarrationTier.EPIC])
        return f"openrouter[routine={routine}; epic={epic}]"

    def models_for(self, tier: NarrationTier | str) -> tuple[str, ...]:
        try:
            tier_name = NarrationTier(str(tier))
        except ValueError:
            tier_name = NarrationTier.ROUTINE
        return self.chains[tier_name]

    def model_for(self, tier: NarrationTier | str) -> str:
        return self.models_for(tier)[0]

    def _headers(self) -> dict[str, str] | None:
        headers: dict[str, str] = {}
        if self.app_url:
            headers["HTTP-Referer"] = self.app_url
        if self.app_name:
            headers["X-OpenRouter-Title"] = self.app_name
        return headers or None

    async def _maybe_check_byok(self, model: str, response: Any) -> None:
        """Ask OpenRouter whether the operator's own provider key served this.

        GET /generation?id=... returns `is_byok` for a completed request. It is
        one extra call per model per hour, only after a success, and any
        failure is ignored - it is a diagnostic for /admin server ai_status,
        never on the narration path. An operator who has added a Google AI
        Studio key in OpenRouter's Integrations page can otherwise not tell
        from the bot whether it is being used: OpenRouter tries the key first
        and silently falls back to its shared pool on any error.
        """
        row = self._model_row(model)
        now = time.time()
        if row["byok_checked_at"] and now - row["byok_checked_at"] < BYOK_RECHECK_SECONDS:
            return
        generation_id = str(getattr(response, "id", "") or "").strip()
        if not generation_id or not self.api_key:
            return
        row["byok_checked_at"] = now
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5.0) as http:
                reply = await http.get(
                    f"{self.base_url}/generation",
                    params={"id": generation_id},
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
            payload = reply.json() if reply.status_code == 200 else {}
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, dict) and "is_byok" in data:
                row["byok"] = bool(data.get("is_byok"))
                if data.get("provider_name") and not row["last_provider"]:
                    row["last_provider"] = str(data["provider_name"])
                log.info("AI_BYOK model=%s byok=%s provider=%s", model, row["byok"], row["last_provider"])
        except Exception as exc:  # diagnostics never break narration
            log.debug("BYOK lookup failed for %s: %s", model, exc)

    def _cooling_down(self, model: str) -> bool:
        return self._cooldown_until.get(model, 0.0) > time.monotonic()

    def _route_limiter(self, model: str) -> RouteLimiter:
        limiter = self._route_limiters.get(model)
        if limiter is None:
            limiter = RouteLimiter(
                model,
                per_minute=self.route_requests_per_minute,
                per_day=self.route_requests_per_day,
            )
            self._route_limiters[model] = limiter
        return limiter

    def _model_row(self, model: str) -> dict[str, Any]:
        row = self._model_stats.get(model)
        if row is None:
            row = {
                "attempts": 0,
                "successes": 0,
                "failures": 0,
                "skipped_cooling": 0,
                "last_error": "",
                "last_error_at": 0.0,
                "last_success_at": 0.0,
                "last_error_looks_like_tls": False,
                "empty_responses": 0,
                "reasoning_salvaged": 0,
                "scratchpad_rejected": 0,
                "skipped_route_limit": 0,
                # None until the route has been probed at all: "not yet asked"
                # and "asked, answered" must not render the same way.
                "probe_ok": None,
                "probe_at": 0.0,
                "probe_error": "",
                "probe_retired": False,
                # Why the last 400 was or was not acted on; "" when the last
                # probe did not answer 400 at all.
                "probe_400_class": "",
                "skipped_probe_retired": 0,
                "consecutive_failures": 0,
                "cooldown_seconds": 0.0,
                # Which upstream actually served the last success, and whether
                # OpenRouter used the operator's own provider key (BYOK) for
                # it - the two facts a GM needs when a route "never responds".
                "last_provider": "",
                "byok": None,
                "byok_checked_at": 0.0,
            }
            self._model_stats[model] = row
        return row

    def _mark_failure(self, model: str, exc: Exception) -> None:
        status = getattr(exc, "status_code", None)
        if status is None:
            status = getattr(getattr(exc, "response", None), "status_code", None)
        row = self._model_row(model)
        row["failures"] += 1
        row["consecutive_failures"] += 1
        # Prefer the provider's own hint over our guess. Falling back to the
        # blanket multiplier only when no hint is offered.
        hinted = _retry_after_seconds(exc)
        if hinted is not None:
            cooldown = hinted
        else:
            multiplier = 3.0 if status == 429 else 1.0
            cooldown = self.failure_cooldown_seconds * multiplier
        # v0.19.37: a route that keeps failing backs off harder each time. Two
        # Gemma free routes sat at 0-for-15 all day on a fixed 60s cooldown, so
        # every narration re-tried both before reaching the route that worked -
        # and each retry spent one of the 50 daily free-tier slots. 23
        # narrations cost 50 slots. Doubling per consecutive failure, capped,
        # means a dead route costs a handful of slots a day instead of most of
        # them; the first success resets it.
        streak = row["consecutive_failures"]
        if streak > 1:
            cooldown = min(
                cooldown * (2 ** min(streak - 1, MAX_BACKOFF_DOUBLINGS)),
                MAX_FAILURE_COOLDOWN_SECONDS,
            )
        self._cooldown_until[model] = time.monotonic() + cooldown
        row["cooldown_seconds"] = float(cooldown)
        row["last_error"] = f"{type(exc).__name__}: {exc}"[:300]
        row["last_error_at"] = time.time()
        tls = _looks_like_tls_failure(exc)
        row["last_error_looks_like_tls"] = tls
        if tls:
            self._tls_failures += 1
            if not self._tls_warned:
                self._tls_warned = True
                # Loud, once, at error level: this is the failure that otherwise
                # only shows up as narration quietly going flat.
                log.error(
                    "AI_TLS_FAILURE model=%s: %s -- narration is falling back to "
                    "procedural prose. HTTPX2 (openai 3.x) verifies against the OS "
                    "trust store, so check that ca-certificates is installed in the "
                    "image and that SSL_CERT_FILE points at a real bundle.",
                    model,
                    exc,
                )

    async def probe_route(self, model: str) -> bool:
        """Ask one route whether it is reachable, as cheaply as the API allows.

        One character in, one token out, and the reply is discarded unread: the
        verdict is whether the call returned at all. A route that fails with a
        durable status (see PROBE_DURABLE_STATUSES) is retired from the chain
        until the next audit; anything else is recorded and left in place,
        because congestion is what the per-route cooldown is already for.
        """
        row = self._model_row(model)
        row["probe_at"] = time.time()
        try:
            if is_aistudio_route(model):
                # Not charged to the OpenRouter budget, for the same reason
                # narration through this route is not: it never reaches them.
                await self.google_route.complete(
                    model=model,
                    system_prompt="",
                    prompt=PROBE_PROMPT,
                    max_output_tokens=PROBE_MAX_TOKENS,
                    temperature=0.0,
                    timeout_seconds=PROBE_TIMEOUT_SECONDS,
                )
            else:
                if not self.client:
                    raise RuntimeError("OPENROUTER_API_KEY is not configured")
                request = self.client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": PROBE_PROMPT}],
                    max_tokens=PROBE_MAX_TOKENS,
                    extra_headers=self._headers(),
                    extra_body=dict(REASONING_OFF) if self.disable_reasoning else None,
                )
                await asyncio.wait_for(request, timeout=PROBE_TIMEOUT_SECONDS)
        except Exception as exc:
            status = _error_status(exc)
            durable = status in PROBE_DURABLE_STATUSES
            classification = ""
            if status == PROBE_REASONING_REJECTED_STATUS:
                durable, classification = await self._classify_bad_request(model)
            row["probe_ok"] = False
            row["probe_error"] = f"{type(exc).__name__}: {exc}"[:300]
            row["probe_retired"] = durable
            row["probe_400_class"] = classification
            log.warning(
                "AI_PROBE_FAILED model=%s durable=%s: %s",
                model,
                durable,
                exc,
            )
            return False
        row["probe_ok"] = True
        row["probe_error"] = ""
        row["probe_retired"] = False
        row["probe_400_class"] = ""
        return True

    async def _reprobe(self, model: str, *, disable_reasoning: bool) -> int | None:
        """Re-issue the probe at an ordinary token budget, one variable changed.

        Returns None when the route answered, otherwise the HTTP status it
        failed with (or -1 when the exception carried no readable status).
        Built through the same call shape narration uses, so the classification
        describes the request this bot actually sends.
        """
        try:
            request = self.client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": PROBE_PROMPT}],
                max_tokens=PROBE_CONFIRM_MAX_TOKENS,
                extra_headers=self._headers(),
                extra_body=dict(REASONING_OFF) if disable_reasoning else None,
            )
            await asyncio.wait_for(request, timeout=PROBE_TIMEOUT_SECONDS)
        except Exception as exc:
            status = _error_status(exc)
            return -1 if status is None else status
        return None

    async def _classify_bad_request(self, model: str) -> tuple[bool, str]:
        """Work out what a probe's 400 actually meant, one variable at a time.

        The probe differs from a narration call in two ways - `max_tokens=1`,
        and (when configured) the REASONING_OFF body - so asking again with
        both changed at once cannot say which the provider objected to. A
        provider that merely enforces a minimum token budget would then be
        misread as reasoning-mandatory and retired for a fault in the
        diagnostic. So the ladder changes ONE thing per call:

          A. ordinary token budget, REASONING_OFF still attached.
             It answers  -> the 400 was the one-token probe hitting a provider
                            minimum. An artifact of the diagnostic; nothing is
                            wrong with the route. Leave it alone.
             Other status -> a different failure entirely; not ours to judge.
          B. ordinary token budget, `reasoning` removed.
             It answers  -> the parameter WAS the cause. Every narration
                            request carries REASONING_OFF and that is not
                            negotiable (it exists because reasoning models
                            returned empty content in v0.19.20 and their
                            scratchpad AS content in v0.25.3), so this route
                            cannot serve narration as this bot calls it.
                            Retire it - until the next audit, which is the
                            right lifetime: OpenRouter fans one slug across
                            provider endpoints and the mix changes without
                            notice, so the verdict decays by construction.
             Still 400   -> not parameter-caused at all. Context length, a
                            malformed body, a moderation filter: nothing here
                            can tell which, and retiring on a cause we cannot
                            name is guessing. Record it and move on.

        Returns (retire, classification). Every path records a classification
        so the panel says WHY a 400 was left alone. An unfunded confirmation is
        not evidence either way, so it retires nothing.
        """
        if is_aistudio_route(model) or not self.client:
            # The Google route sends no reasoning/thinking parameter at all, so
            # there is no second variable to isolate and nothing to conclude.
            return False, PROBE_400_NO_PARAMETER
        if not await self.limiter.try_acquire():
            log.info("AI_PROBE_400_UNCONFIRMED model=%s: no budget to confirm", model)
            return False, PROBE_400_UNCONFIRMED

        status = await self._reprobe(model, disable_reasoning=self.disable_reasoning)
        if status is None:
            log.info("AI_PROBE_400_WAS_THE_PROBE model=%s", model)
            return False, PROBE_400_TOKEN_BUDGET
        if status != PROBE_REASONING_REJECTED_STATUS:
            log.info("AI_PROBE_400_MOVED model=%s status=%s", model, status)
            return False, PROBE_400_OTHER_STATUS
        if not self.disable_reasoning:
            # Nothing was added to remove: the 400 cannot be about a parameter
            # this bot never sent.
            return False, PROBE_400_UNCLASSIFIED
        if not await self.limiter.try_acquire():
            log.info("AI_PROBE_400_UNCONFIRMED model=%s: no budget to differentiate", model)
            return False, PROBE_400_UNCONFIRMED

        without_reasoning = await self._reprobe(model, disable_reasoning=False)
        if without_reasoning is None:
            log.warning(
                "AI_PROBE_400_REASONING model=%s: answers without REASONING_OFF "
                "and 400s with it - retiring until the next audit",
                model,
            )
            return True, PROBE_400_REASONING
        log.info(
            "AI_PROBE_400_UNCLASSIFIED model=%s status=%s: 400s with and without "
            "REASONING_OFF, so the parameter is not the cause - retiring nothing",
            model,
            without_reasoning,
        )
        return False, PROBE_400_UNCLASSIFIED

    async def audit_routes(self) -> dict[str, Any]:
        """Probe every configured route once and retire the ones that are gone.

        Returns a summary for the caller to log. Skipped entirely when most of
        the daily free-tier budget is already spent - narration is what the
        budget is for, and a diagnostic that starves it is worse than no
        diagnostic at all.
        """
        models = _dedupe_chain(
            model for tier in NarrationTier for model in self.chains[tier]
        )
        result: dict[str, Any] = {
            "checked": [],
            "retired": [],
            "failed": [],
            "skipped": "",
            "at": time.time(),
        }
        if not self.enabled:
            result["skipped"] = "no narration route is configured"
            self._last_audit = result
            return result

        budget = self.limiter.snapshot()
        per_day = max(1, int(budget["max_requests_per_day"]))
        remaining = per_day - int(budget["used_today"])
        # The audit costs one slot per OpenRouter route; the AI Studio route is
        # free of this budget by construction and is not counted here. A route
        # that answers 400 can cost up to two more while _classify_bad_request
        # isolates the cause, which is what the headroom below is for - those
        # are not counted here because a chain where every route 400s is not
        # the case worth sizing for.
        cost = sum(1 for model in models if not is_aistudio_route(model))
        if remaining - cost < per_day * PROBE_BUDGET_HEADROOM:
            result["skipped"] = (
                f"only {remaining}/{per_day} of the daily budget is left; "
                "narration keeps it"
            )
            log.info("AI_AUDIT_SKIPPED %s", result["skipped"])
            self._last_audit = result
            return result

        for model in models:
            if not is_aistudio_route(model) and not await self.limiter.try_acquire():
                result["skipped"] = "ran out of budget mid-audit"
                break
            result["checked"].append(model)
            if await self.probe_route(model):
                continue
            result["failed"].append(model)
            if self._model_row(model)["probe_retired"]:
                result["retired"].append(model)

        if result["checked"] and len(result["retired"]) == len(result["checked"]):
            # Every route rejected at once is not a catalogue that emptied
            # overnight; it is a proxy, a firewall or a revoked key in front of
            # all of them. Retiring the whole chain on that reading would keep
            # the bot on procedural prose long after the local fault cleared,
            # so the verdicts are kept for the panel and none are enforced.
            for model in result["retired"]:
                self._model_row(model)["probe_retired"] = False
            result["fail_open"] = True
            log.error(
                "AI_AUDIT every route (%d) failed durably - treating this as a "
                "local fault and retiring none of them",
                len(result["checked"]),
            )
        log.info(
            "AI_AUDIT checked=%d failed=%d retired=%s",
            len(result["checked"]),
            len(result["failed"]),
            ",".join(result["retired"]) if not result.get("fail_open") else "none (fail-open)",
        )
        self._last_audit = result
        return result

    def health_snapshot(self) -> dict[str, Any]:
        """Administrator-readable view of how the free fallback chain is doing.

        Deliberately contains no prompts, no player text and no API key - it is
        counters only, so it is safe to render into a Discord panel.
        """
        now_monotonic = time.monotonic()
        models: list[dict[str, Any]] = []
        for model, row in sorted(self._model_stats.items()):
            remaining = self._cooldown_until.get(model, 0.0) - now_monotonic
            models.append(
                {
                    "model": model,
                    "attempts": int(row["attempts"]),
                    "successes": int(row["successes"]),
                    "failures": int(row["failures"]),
                    "skipped_cooling": int(row["skipped_cooling"]),
                    "cooling_down": remaining > 0,
                    "cooldown_remaining_seconds": round(max(0.0, remaining), 1),
                    "last_error": str(row["last_error"]),
                    "last_error_looks_like_tls": bool(row["last_error_looks_like_tls"]),
                    "empty_responses": int(row["empty_responses"]),
                    "skipped_route_limit": int(row["skipped_route_limit"]),
                    "probe_ok": row["probe_ok"],
                    "probe_at": float(row["probe_at"]),
                    "probe_error": str(row["probe_error"]),
                    "probe_retired": bool(row["probe_retired"]),
                    "probe_400_class": str(row["probe_400_class"]),
                    "skipped_probe_retired": int(row["skipped_probe_retired"]),
                    "route_limits": (
                        self._route_limiters[model].snapshot()
                        if model in self._route_limiters
                        else None
                    ),
                    "reasoning_salvaged": int(row["reasoning_salvaged"]),
                    "scratchpad_rejected": int(row["scratchpad_rejected"]),
                    "consecutive_failures": int(row["consecutive_failures"]),
                    "cooldown_seconds": float(row["cooldown_seconds"]),
                    "last_provider": str(row["last_provider"]),
                    "byok": row["byok"],
                    "never_succeeded": int(row["attempts"]) > 0 and int(row["successes"]) == 0,
                    "last_error_at": float(row["last_error_at"]),
                    "last_success_at": float(row["last_success_at"]),
                }
            )
        return {
            "enabled": self.enabled,
            "require_free": self.require_free,
            "uptime_seconds": round(max(0.0, time.time() - self._started_at), 1),
            "tls_failures": int(self._tls_failures),
            "route_requests_per_minute": self.route_requests_per_minute,
            "route_requests_per_day": self.route_requests_per_day,
            "chains": {tier.value: list(self.chains[tier]) for tier in NarrationTier},
            # A key that is set but whose SDK will not load is the failure an
            # operator cannot otherwise see: the route is simply absent from the
            # chain and narration quietly stays on OpenRouter. Say it plainly.
            "google_route": {
                "configured": self.google_route.configured,
                "available": self.google_route.available,
                "model": self.google_model,
                "last_error": self.google_route.last_error,
            },
            "audit": dict(self._last_audit),
            "tiers": {name: dict(counts) for name, counts in self._tier_stats.items()},
            "limiter": self.limiter.snapshot(),
            "models": models,
        }

    async def generate(
        self,
        *,
        tier: NarrationTier | str,
        system_prompt: str,
        prompt: str,
        max_output_tokens: int,
        leak_guard: bool = True,
        temperature: float | None = None,
    ) -> RoutedAIResult:
        if not self.client:
            raise RuntimeError("OPENROUTER_API_KEY is not configured")
        try:
            tier_name = NarrationTier(str(tier))
        except ValueError:
            tier_name = NarrationTier.ROUTINE

        timeout_seconds = (
            self.epic_timeout_seconds if tier_name == NarrationTier.EPIC else self.routine_timeout_seconds
        )
        if temperature is None:
            temperature = 0.62 if tier_name == NarrationTier.EPIC else 0.78
        temperature = min(2.0, max(0.0, float(temperature)))
        attempted: list[str] = []
        errors: list[str] = []
        tier_counts = self._tier_stats.setdefault(
            tier_name.value, {"requests": 0, "served": 0, "exhausted": 0, "rate_limited": 0}
        )
        tier_counts["requests"] += 1

        for model in self.chains[tier_name]:
            if self._model_row(model)["probe_retired"]:
                # The daily audit got a 401/403/404 from this route. Spending a
                # narration attempt - and a free-tier slot - to be told again is
                # the exact waste that retired it.
                self._model_row(model)["skipped_probe_retired"] += 1
                continue
            if self._cooling_down(model):
                self._model_row(model)["skipped_cooling"] += 1
                continue
            if not self._route_limiter(model).try_acquire():
                # A route at ITS OWN provider ceiling is skipped, not failed: the
                # next model has a separate quota. Spending an upstream attempt to
                # be told 429 is the waste this replaces.
                self._model_row(model)["skipped_route_limit"] += 1
                continue
            google_call = is_aistudio_route(model)
            # The OpenRouter budget is not spent on a call that never reaches
            # OpenRouter. Charging the AI Studio route against it would defeat
            # the entire reason the route exists.
            if not google_call and not await self.limiter.try_acquire():
                tier_counts["rate_limited"] += 1
                snapshot = self.limiter.snapshot()
                if snapshot["daily_exhausted"]:
                    # Stopping here is the point: every request past the daily
                    # allowance is refused upstream anyway, and walking the whole
                    # chain to discover that costs three refusals instead of one.
                    raise RuntimeError(
                        "OpenRouter daily free-tier budget spent "
                        f"({snapshot['used_today']}/{snapshot['max_requests_per_day']} in 24h); "
                        "narration is procedural until it rolls over"
                    )
                raise RuntimeError("OpenRouter local request-rate ceiling reached")
            attempted.append(model)
            self._model_row(model)["attempts"] += 1
            try:
                # A reasoning model given 180 tokens spends them all on reasoning
                # and returns empty content. The dynamic router picks at random
                # from a free pool that is now mostly reasoning models, so it gets
                # room to actually answer.
                output_tokens = max(32, int(max_output_tokens))
                if model == self.dynamic_free_model:
                    output_tokens = max(output_tokens, DYNAMIC_FREE_MIN_OUTPUT_TOKENS)
                if google_call:
                    # Same guards, different transport: the text returned here
                    # goes through _validate_generated_text below exactly like
                    # an OpenRouter reply, so a scratchpad or a prompt leak is
                    # rejected and the chain falls through to OpenRouter.
                    raw_text = await self.google_route.complete(
                        model=model,
                        system_prompt=system_prompt,
                        prompt=prompt,
                        max_output_tokens=output_tokens,
                        temperature=temperature,
                        timeout_seconds=timeout_seconds,
                    )
                    text = _google_validated(raw_text, leak_guard, self._model_row(model))
                    success_row = self._model_row(model)
                    success_row["successes"] += 1
                    success_row["last_success_at"] = time.time()
                    success_row["consecutive_failures"] = 0
                    success_row["cooldown_seconds"] = 0.0
                    success_row["last_provider"] = "Google AI Studio"
                    # Not a BYOK question: this IS the operator's own key, by
                    # construction. Saying so keeps ai_status readable.
                    success_row["byok"] = True
                    tier_counts["served"] += 1
                    return RoutedAIResult(
                        text=text,
                        tier=tier_name,
                        model=model,
                        attempted_models=tuple(attempted),
                    )
                request = self.client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=output_tokens,
                    temperature=temperature,
                    extra_headers=self._headers(),
                    extra_body=dict(REASONING_OFF) if self.disable_reasoning else None,
                )
                response = await asyncio.wait_for(request, timeout=timeout_seconds)
                if not response.choices:
                    raise RuntimeError("OpenRouter returned no choices")
                message = response.choices[0].message
                raw_text = _extract_text(getattr(message, "content", None))
                if not raw_text:
                    # Empty content is a different failure from a 429 and is
                    # tracked separately: it is a shape mismatch, not congestion.
                    self._model_row(model)["empty_responses"] += 1
                    raw_text = _extract_reasoning(message)
                    if raw_text:
                        self._model_row(model)["reasoning_salvaged"] += 1
                        log.info("AI_REASONING_SALVAGED model=%s", model)
                try:
                    text = _validate_generated_text(raw_text, leak_guard=leak_guard)
                except ScratchpadResponse:
                    self._model_row(model)["scratchpad_rejected"] += 1
                    log.info("AI_SCRATCHPAD_REJECTED model=%s", model)
                    raise
                success_row = self._model_row(model)
                success_row["successes"] += 1
                success_row["last_success_at"] = time.time()
                success_row["consecutive_failures"] = 0
                success_row["cooldown_seconds"] = 0.0
                provider_name = _response_provider(response)
                if provider_name:
                    success_row["last_provider"] = provider_name
                await self._maybe_check_byok(model, response)
                tier_counts["served"] += 1
                return RoutedAIResult(
                    text=text,
                    tier=tier_name,
                    model=model,
                    attempted_models=tuple(attempted),
                )
            except Exception as exc:
                self._mark_failure(model, exc)
                errors.append(f"{model}: {type(exc).__name__}: {exc}")
                log.warning(
                    "OpenRouter route failed tier=%s model=%s; trying next free route: %s",
                    tier_name.value,
                    model,
                    exc,
                )

        tier_counts["exhausted"] += 1
        detail = " | ".join(errors[-3:]) if errors else "all configured routes are cooling down"
        raise RuntimeError(f"OpenRouter free fallback chain exhausted: {detail}")
