"""Direct Google AI Studio narration route.

Every other route in this bot goes through OpenRouter. This one does not: it
calls Google's Gemini API with the operator's own AI Studio key, using the
`google-genai` SDK.

Why it exists
-------------
OpenRouter's free tier is capped at ~50 requests a day across every free route,
and once that budget is spent narration goes procedural until it rolls over.
An AI Studio key has its own, much larger, free-tier quota that has nothing to
do with that budget. So when a key is configured this route is tried FIRST on
both tiers, and it deliberately does not touch `AITaskRouter.limiter` - the
OpenRouter daily counter - because a call made here costs OpenRouter nothing.

What it is not
--------------
It is not a second narrator. It returns text to the same
`_validate_generated_text` guard as every OpenRouter route, so a reply that is
a reasoning scratchpad or leaks the prompt is rejected here exactly as it is
there, and the router falls through to the OpenRouter chain and finally to
procedural prose. Authority is unchanged: this is narration only.

SDK surface
-----------
`google-genai` is imported lazily and is NOT a hard requirement. If the package
is missing, too old, or raises on client construction, `available` is False and
the route is simply not in the chain - the bot starts and narrates exactly as
it did before. Two call shapes are supported because Google is mid-migration:
the newer Interactions API (`client.interactions.create`) and the long-standing
`client.models.generate_content`. Whichever the installed SDK actually exposes
is used; if neither is there, that is a clear error, not a crash at import.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger("xianxia.ai_router.google")

# Chain entries for this route carry this prefix so AITaskRouter can tell at a
# glance which transport serves a model, and so OPENROUTER_REQUIRE_FREE - which
# is a statement about OpenRouter's catalogue - does not apply to it.
AISTUDIO_PREFIX = "aistudio/"

# The default is a Flash model: narration is short, latency matters more than
# depth, and Flash is the tier AI Studio's free quota is most generous with.
DEFAULT_GOOGLE_MODEL = f"{AISTUDIO_PREFIX}gemini-3.8-flash"


def is_aistudio_route(model: str) -> bool:
    return str(model or "").strip().startswith(AISTUDIO_PREFIX)


def strip_prefix(model: str) -> str:
    """`aistudio/gemini-3.8-flash` -> `gemini-3.8-flash` (what Google expects)."""
    value = str(model or "").strip()
    return value[len(AISTUDIO_PREFIX):] if value.startswith(AISTUDIO_PREFIX) else value


class GoogleRouteUnavailable(RuntimeError):
    """The SDK is missing or exposes neither supported call shape."""


def _first_text(value: Any) -> str:
    """Pull plain text out of whatever shape the SDK handed back.

    Both call shapes offer a convenience property (`output_text` on an
    interaction, `text` on a generate_content response). Those are tried first;
    the structured walk below is the fallback for an SDK version that drops or
    renames them, so a cosmetic SDK change cannot silently blank narration.
    """
    for attribute in ("output_text", "text"):
        text = getattr(value, attribute, None)
        if isinstance(text, str) and text.strip():
            return text.strip()

    chunks: list[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, str):
            if node.strip():
                chunks.append(node)
            return
        if isinstance(node, (list, tuple)):
            for item in node:
                _walk(item)
            return
        if isinstance(node, dict):
            if isinstance(node.get("text"), str):
                _walk(node["text"])
            return
        text = getattr(node, "text", None)
        if isinstance(text, str):
            _walk(text)

    for attribute in ("steps", "candidates", "content", "parts", "output"):
        node = getattr(value, attribute, None)
        if node is not None:
            _walk(node)
        if chunks:
            break
    return "\n".join(chunks).strip()


class GoogleAIStudioRoute:
    """Thin async adapter over google-genai for one narration call."""

    def __init__(self, api_key: str | None) -> None:
        self.api_key = str(api_key or "").strip() or None
        self._client: Any = None
        self._client_error = ""
        self._resolved_shape = ""

    @property
    def configured(self) -> bool:
        return self.api_key is not None

    @property
    def available(self) -> bool:
        """True when a key is set AND the SDK could be loaded."""
        if not self.configured:
            return False
        try:
            self._ensure_client()
        except Exception:
            return False
        return self._client is not None

    @property
    def last_error(self) -> str:
        return self._client_error

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.configured:
            raise GoogleRouteUnavailable("No Google AI Studio API key is configured")
        try:
            from google import genai  # noqa: PLC0415 - optional dependency, resolved at call time
        except Exception as exc:  # ImportError, but a broken install can raise anything
            self._client_error = f"google-genai is not installed: {exc}"
            raise GoogleRouteUnavailable(self._client_error) from exc
        try:
            # The key is passed explicitly rather than left to the SDK's own
            # GEMINI_API_KEY lookup, so the bot uses the key the operator put in
            # .env and not whatever happens to be in the process environment.
            self._client = genai.Client(api_key=self.api_key)
        except Exception as exc:
            self._client_error = f"google-genai client construction failed: {exc}"
            raise GoogleRouteUnavailable(self._client_error) from exc
        return self._client

    async def complete(
        self,
        *,
        model: str,
        system_prompt: str,
        prompt: str,
        max_output_tokens: int,
        temperature: float,
        timeout_seconds: float,
    ) -> str:
        client = self._ensure_client()
        name = strip_prefix(model)
        call = self._interactions_call(client, name, system_prompt, prompt, max_output_tokens, temperature)
        if call is None:
            call = self._generate_content_call(client, name, system_prompt, prompt, max_output_tokens, temperature)
        if call is None:
            raise GoogleRouteUnavailable(
                "google-genai exposes neither interactions.create nor models.generate_content"
            )
        response = await asyncio.wait_for(call, timeout=timeout_seconds)
        text = _first_text(response)
        if not text:
            raise RuntimeError("Google AI Studio returned an empty response")
        return text

    # -- call shapes ------------------------------------------------------
    #
    # Each returns an awaitable, or None when the installed SDK does not have
    # that surface. `client.aio` is the SDK's async namespace; when it is
    # absent the sync method is run in a worker thread so the event loop - which
    # is also serving Discord - is never blocked on a network call.

    def _interactions_call(self, client, name, system_prompt, prompt, max_output_tokens, temperature):
        kwargs: dict[str, Any] = {
            "model": name,
            "input": prompt,
            "system_instruction": system_prompt,
            "max_output_tokens": int(max_output_tokens),
            "temperature": float(temperature),
        }
        aio = getattr(getattr(client, "aio", None), "interactions", None)
        if aio is not None and hasattr(aio, "create"):
            self._resolved_shape = "interactions.aio"
            return self._invoke(aio.create, kwargs, threaded=False)
        sync = getattr(client, "interactions", None)
        if sync is not None and hasattr(sync, "create"):
            self._resolved_shape = "interactions"
            return self._invoke(sync.create, kwargs, threaded=True)
        return None

    def _generate_content_call(self, client, name, system_prompt, prompt, max_output_tokens, temperature):
        config = {
            "system_instruction": system_prompt,
            "max_output_tokens": int(max_output_tokens),
            "temperature": float(temperature),
        }
        kwargs = {"model": name, "contents": prompt, "config": config}
        aio = getattr(getattr(client, "aio", None), "models", None)
        if aio is not None and hasattr(aio, "generate_content"):
            self._resolved_shape = "generate_content.aio"
            return self._invoke(aio.generate_content, kwargs, threaded=False)
        sync = getattr(client, "models", None)
        if sync is not None and hasattr(sync, "generate_content"):
            self._resolved_shape = "generate_content"
            return self._invoke(sync.generate_content, kwargs, threaded=True)
        return None

    def _invoke(self, method, kwargs: dict[str, Any], *, threaded: bool):
        """Call `method`, dropping kwargs it does not accept.

        The SDK is in beta and the exact keyword names move between releases.
        A TypeError about an unexpected keyword would otherwise present as a
        dead route rather than as "this SDK spells that argument differently",
        so an unknown keyword is dropped and the call retried - down to the two
        arguments every version has always taken.
        """

        async def _run() -> Any:
            attempt = dict(kwargs)
            required = ("model", "input", "contents")
            while True:
                try:
                    if threaded:
                        return await asyncio.to_thread(lambda: method(**attempt))
                    return await method(**attempt)
                except TypeError as exc:
                    dropped = None
                    for key in list(attempt):
                        if key not in required and key in str(exc):
                            dropped = key
                            break
                    if dropped is None:
                        raise
                    attempt.pop(dropped)
                    log.debug("google-genai rejected %r; retrying without it", dropped)

        return _run()
