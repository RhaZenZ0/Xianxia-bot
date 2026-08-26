from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable

from openai import AsyncOpenAI

log = logging.getLogger("xianxia.ai_router")


class NarrationTier(StrEnum):
    ROUTINE = "routine"
    EPIC = "epic"


DEFAULT_ROUTINE_MODEL = "google/gemma-4-31b-it:free"
DEFAULT_ROUTINE_FALLBACK_MODEL = "google/gemma-4-26b-a4b-it:free"
DEFAULT_EPIC_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
DEFAULT_EPIC_FALLBACK_MODEL = DEFAULT_ROUTINE_MODEL
DEFAULT_DYNAMIC_FREE_MODEL = "openrouter/free"

_PROMPT_LEAK_PATTERNS = (
    re.compile(r"\bsystem prompt\b", re.I),
    re.compile(r"\bdeveloper message\b", re.I),
    re.compile(r"\bcanonical context block\b", re.I),
    re.compile(r"\bpython engine\b", re.I),
    re.compile(r"\bgo engine\b", re.I),
    re.compile(r"\bgame engine\b", re.I),
    re.compile(r"\bhidden instructions?\b", re.I),
)


@dataclass(frozen=True)
class RoutedAIResult:
    text: str
    tier: NarrationTier
    model: str
    attempted_models: tuple[str, ...]


class OpenRouterRequestLimiter:
    """Fail-fast rolling request limiter for the shared OpenRouter account.

    The narrator should never make Discord users wait for a local retry queue when
    the free account limit is already saturated. When the local cap is reached,
    narration falls through to the procedural safety net immediately.
    """

    def __init__(self, max_requests_per_minute: int = 20) -> None:
        self.max_requests = max(1, int(max_requests_per_minute))
        self.window_seconds = 60.0
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def try_acquire(self) -> bool:
        now = time.monotonic()
        async with self._lock:
            cutoff = now - self.window_seconds
            while self._timestamps and self._timestamps[0] <= cutoff:
                self._timestamps.popleft()
            if len(self._timestamps) >= self.max_requests:
                return False
            self._timestamps.append(now)
            return True


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


def _is_free_route(model: str) -> bool:
    chosen = str(model or "").strip()
    return chosen.endswith(":free") or chosen == DEFAULT_DYNAMIC_FREE_MODEL


def _safe_free_model(model: str, *, require_free: bool) -> str:
    chosen = str(model or "").strip()
    if not chosen:
        raise ValueError("OpenRouter model cannot be empty")
    if require_free and not _is_free_route(chosen):
        raise ValueError(f"OpenRouter narrator requires a free route, got: {chosen}")
    return chosen


def _validate_generated_text(text: str, *, max_chars: int = 7000) -> str:
    value = str(text or "").strip()
    if not value:
        raise ValueError("AI provider returned an empty response")
    if any(pattern.search(value) for pattern in _PROMPT_LEAK_PATTERNS):
        raise ValueError("AI response exposed implementation or prompt details")
    if value.startswith("```") and value.endswith("```"):
        value = re.sub(r"^```(?:text|markdown|md)?\s*", "", value, flags=re.I)
        value = re.sub(r"\s*```$", "", value).strip()
    if len(value) > max_chars:
        value = value[: max_chars - 1].rstrip() + "…"
    return value


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
        require_free: bool = True,
        max_requests_per_minute: int = 20,
        routine_timeout_seconds: float = 30.0,
        epic_timeout_seconds: float = 60.0,
        failure_cooldown_seconds: float = 20.0,
        app_url: str = "",
        app_name: str = "Xianxia RP",
    ) -> None:
        self.api_key = str(api_key or "").strip() or None
        self.base_url = str(base_url or "https://openrouter.ai/api/v1").rstrip("/")
        self.require_free = bool(require_free)
        self.routine_timeout_seconds = max(5.0, float(routine_timeout_seconds))
        self.epic_timeout_seconds = max(5.0, float(epic_timeout_seconds))
        self.failure_cooldown_seconds = max(1.0, float(failure_cooldown_seconds))
        self.app_url = str(app_url or "").strip()
        self.app_name = str(app_name or "Xianxia RP").strip() or "Xianxia RP"

        routine = _safe_free_model(routine_model, require_free=self.require_free)
        routine_fallback = _safe_free_model(routine_fallback_model, require_free=self.require_free)
        epic = _safe_free_model(epic_model, require_free=self.require_free)
        epic_fallback = _safe_free_model(epic_fallback_model, require_free=self.require_free)
        dynamic = _safe_free_model(dynamic_free_model, require_free=self.require_free)

        self.chains = {
            NarrationTier.ROUTINE: _dedupe_chain((routine, routine_fallback, dynamic)),
            NarrationTier.EPIC: _dedupe_chain((epic, epic_fallback, dynamic)),
        }
        self.limiter = OpenRouterRequestLimiter(max_requests_per_minute)
        self._cooldown_until: dict[str, float] = {}
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
        return self.client is not None

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

    def _cooling_down(self, model: str) -> bool:
        return self._cooldown_until.get(model, 0.0) > time.monotonic()

    def _mark_failure(self, model: str, exc: Exception) -> None:
        status = getattr(exc, "status_code", None)
        if status is None:
            status = getattr(getattr(exc, "response", None), "status_code", None)
        multiplier = 3.0 if status == 429 else 1.0
        self._cooldown_until[model] = time.monotonic() + self.failure_cooldown_seconds * multiplier

    async def generate(
        self,
        *,
        tier: NarrationTier | str,
        system_prompt: str,
        prompt: str,
        max_output_tokens: int,
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
        temperature = 0.62 if tier_name == NarrationTier.EPIC else 0.78
        attempted: list[str] = []
        errors: list[str] = []

        for model in self.chains[tier_name]:
            if self._cooling_down(model):
                continue
            if not await self.limiter.try_acquire():
                raise RuntimeError("OpenRouter local request-rate ceiling reached")
            attempted.append(model)
            try:
                request = self.client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=max(32, int(max_output_tokens)),
                    temperature=temperature,
                    extra_headers=self._headers(),
                )
                response = await asyncio.wait_for(request, timeout=timeout_seconds)
                if not response.choices:
                    raise RuntimeError("OpenRouter returned no choices")
                text = _validate_generated_text(_extract_text(response.choices[0].message.content))
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

        detail = " | ".join(errors[-3:]) if errors else "all configured routes are cooling down"
        raise RuntimeError(f"OpenRouter free fallback chain exhausted: {detail}")
