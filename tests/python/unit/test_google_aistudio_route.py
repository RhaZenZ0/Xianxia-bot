"""The direct Google AI Studio narration route (v0.26.0).

Every other route goes through OpenRouter and is capped by its ~50-a-day free
budget. This one calls Google with the operator's own key, so the point of the
whole feature is that it does NOT spend that budget - which is the single most
important assertion in this file.

The route is optional in both directions: with no key it must not exist at all,
and with a key but no SDK it must degrade to "OpenRouter only" rather than
taking narration down with it.
"""

import asyncio
import unittest

from tests.support import install_genai_shim, install_openai_shim, uninstall_genai_shim

install_openai_shim()

from app.ai.ai_router import AITaskRouter, NarrationTier  # noqa: E402
from app.ai.google_route import (  # noqa: E402
    AISTUDIO_PREFIX,
    DEFAULT_GOOGLE_MODEL,
    GoogleAIStudioRoute,
    is_aistudio_route,
    strip_prefix,
)

NARRATION = "Rain finds the courtyard first, then the shoulders of the seated disciples."


def _generate(router, tier=NarrationTier.ROUTINE):
    return asyncio.run(
        router.generate(tier=tier, system_prompt="s", prompt="p", max_output_tokens=200)
    )


class RouteNamingTests(unittest.TestCase):
    def test_the_prefix_marks_the_transport(self):
        self.assertTrue(is_aistudio_route("aistudio/gemini-3.8-flash"))
        self.assertFalse(is_aistudio_route("google/gemma-4-31b-it:free"))
        self.assertFalse(is_aistudio_route("openrouter/free"))

    def test_google_is_sent_the_bare_model_name(self):
        # `aistudio/` is this codebase's marker, not part of Google's model id.
        self.assertEqual(strip_prefix("aistudio/gemini-3.8-flash"), "gemini-3.8-flash")
        self.assertEqual(strip_prefix("gemini-3.8-flash"), "gemini-3.8-flash")

    def test_the_default_model_carries_the_prefix(self):
        self.assertTrue(DEFAULT_GOOGLE_MODEL.startswith(AISTUDIO_PREFIX))


class ChainPlacementTests(unittest.TestCase):
    def tearDown(self):
        uninstall_genai_shim()

    def test_no_key_means_the_route_does_not_exist(self):
        router = AITaskRouter(api_key="sk-or-test")
        for tier, chain in router.chains.items():
            self.assertFalse(any(is_aistudio_route(m) for m in chain), tier)
        self.assertFalse(router.google_route.configured)
        self.assertFalse(router.health_snapshot()["google_route"]["configured"])

    def test_a_key_puts_it_first_on_both_tiers(self):
        install_genai_shim()
        router = AITaskRouter(api_key="sk-or-test", google_api_key="ai-studio-key")
        for tier, chain in router.chains.items():
            self.assertTrue(is_aistudio_route(chain[0]), f"{tier}: {chain}")
            self.assertEqual(len(chain), 4, tier)
        # The OpenRouter chain is unchanged behind it - this adds a hop, it does
        # not replace the fallbacks that already work.
        self.assertEqual(router.chains[NarrationTier.ROUTINE][1:], ("google/gemma-4-31b-it:free", "z-ai/glm-5.2:free", "openrouter/free"))

    def test_the_free_route_guard_does_not_apply_to_it(self):
        # OPENROUTER_REQUIRE_FREE is about OpenRouter's catalogue. An aistudio/
        # id has no ":free" suffix and must not be rejected for lacking one.
        install_genai_shim()
        router = AITaskRouter(api_key="sk-or-test", google_api_key="k", require_free=True)
        self.assertTrue(is_aistudio_route(router.chains[NarrationTier.ROUTINE][0]))

    def test_a_model_without_the_prefix_is_refused_at_construction(self):
        install_genai_shim()
        with self.assertRaisesRegex(ValueError, "aistudio/"):
            AITaskRouter(api_key="sk-or-test", google_api_key="k", google_model="gemini-3.8-flash")

    def test_a_google_key_alone_is_enough_to_be_enabled(self):
        install_genai_shim()
        router = AITaskRouter(api_key=None, google_api_key="k")
        self.assertTrue(router.enabled)


class BudgetTests(unittest.TestCase):
    """The reason the feature exists."""

    def tearDown(self):
        uninstall_genai_shim()

    def test_a_google_narration_does_not_spend_the_openrouter_daily_budget(self):
        install_genai_shim([NARRATION])
        router = AITaskRouter(api_key="sk-or-test", google_api_key="k")
        before = router.limiter.snapshot()["used_today"]
        result = _generate(router)
        self.assertEqual(result.text, NARRATION)
        self.assertTrue(is_aistudio_route(result.model))
        self.assertEqual(router.limiter.snapshot()["used_today"], before)

    def test_an_exhausted_openrouter_budget_does_not_stop_the_google_route(self):
        # Previously the daily ceiling raised before any route was tried. A
        # route that costs OpenRouter nothing must still run.
        install_genai_shim([NARRATION])
        router = AITaskRouter(api_key="sk-or-test", google_api_key="k", max_requests_per_day=1)
        router.limiter._day_count = 99  # spent
        result = _generate(router)
        self.assertEqual(result.text, NARRATION)

    def test_the_openrouter_chain_still_spends_it(self):
        install_genai_shim([RuntimeError("google down")])
        router = _router_with_openrouter_reply(NARRATION, google_key="k")
        before = router.limiter.snapshot()["used_today"]
        result = _generate(router)
        self.assertEqual(result.text, NARRATION)
        self.assertFalse(is_aistudio_route(result.model))
        self.assertEqual(router.limiter.snapshot()["used_today"], before + 1)


class GuardTests(unittest.TestCase):
    """A direct route is not a trusted route."""

    def tearDown(self):
        uninstall_genai_shim()

    def test_a_scratchpad_from_google_is_rejected_and_counted(self):
        install_genai_shim(["Okay, the user wants a rain scene. I should keep it short."])
        router = _router_with_openrouter_reply(NARRATION, google_key="k")
        result = _generate(router)
        self.assertEqual(result.text, NARRATION)
        self.assertFalse(is_aistudio_route(result.model))
        row = next(r for r in router.health_snapshot()["models"] if is_aistudio_route(r["model"]))
        self.assertEqual(row["scratchpad_rejected"], 1)

    def test_a_prompt_leak_from_google_is_rejected(self):
        install_genai_shim(["The game engine returned an error, so the rain stops."])
        router = _router_with_openrouter_reply(NARRATION, google_key="k")
        result = _generate(router)
        self.assertEqual(result.text, NARRATION)
        self.assertFalse(is_aistudio_route(result.model))

    def test_an_empty_google_reply_falls_through(self):
        install_genai_shim([""])
        router = _router_with_openrouter_reply(NARRATION, google_key="k")
        self.assertEqual(_generate(router).text, NARRATION)

    def test_a_google_failure_falls_through_and_backs_off(self):
        install_genai_shim([RuntimeError("google down")])
        router = _router_with_openrouter_reply(NARRATION, google_key="k")
        self.assertEqual(_generate(router).text, NARRATION)
        row = next(r for r in router.health_snapshot()["models"] if is_aistudio_route(r["model"]))
        self.assertEqual(row["failures"], 1)
        self.assertTrue(row["cooling_down"])


class SdkShapeTests(unittest.TestCase):
    """Google is mid-migration; both call shapes have to work."""

    def tearDown(self):
        uninstall_genai_shim()

    def test_the_interactions_api_is_used_when_present(self):
        calls = install_genai_shim([NARRATION], shape="interactions")
        route = GoogleAIStudioRoute("k")
        text = asyncio.run(route.complete(
            model=DEFAULT_GOOGLE_MODEL, system_prompt="sys", prompt="user",
            max_output_tokens=200, temperature=0.7, timeout_seconds=5,
        ))
        self.assertEqual(text, NARRATION)
        self.assertEqual(calls[0]["surface"], "interactions")
        self.assertEqual(calls[0]["model"], "gemini-3.8-flash")
        self.assertEqual(calls[0]["input"], "user")

    def test_generate_content_is_used_when_interactions_is_absent(self):
        calls = install_genai_shim([NARRATION], shape="generate_content")
        route = GoogleAIStudioRoute("k")
        text = asyncio.run(route.complete(
            model=DEFAULT_GOOGLE_MODEL, system_prompt="sys", prompt="user",
            max_output_tokens=200, temperature=0.7, timeout_seconds=5,
        ))
        self.assertEqual(text, NARRATION)
        self.assertEqual(calls[0]["surface"], "generate_content")
        self.assertEqual(calls[0]["contents"], "user")

    def test_the_system_prompt_is_actually_sent(self):
        # Narration without the system prompt is not narration; it is a chatbot
        # answering a question about a cultivator.
        calls = install_genai_shim([NARRATION], shape="interactions")
        route = GoogleAIStudioRoute("k")
        asyncio.run(route.complete(
            model=DEFAULT_GOOGLE_MODEL, system_prompt="you are a narrator", prompt="p",
            max_output_tokens=200, temperature=0.7, timeout_seconds=5,
        ))
        self.assertEqual(calls[0]["system_instruction"], "you are a narrator")

    def test_a_missing_sdk_leaves_the_route_unavailable_rather_than_raising(self):
        uninstall_genai_shim()
        route = GoogleAIStudioRoute("k")
        self.assertTrue(route.configured)
        self.assertFalse(route.available)
        self.assertIn("google-genai", route.last_error)

    def test_a_missing_sdk_keeps_the_bot_narrating_on_openrouter(self):
        uninstall_genai_shim()
        router = _router_with_openrouter_reply(NARRATION, google_key="k")
        self.assertEqual(_generate(router).text, NARRATION)
        snapshot = router.health_snapshot()["google_route"]
        self.assertTrue(snapshot["configured"])
        self.assertFalse(snapshot["available"])


def _router_with_openrouter_reply(text, *, google_key=None):
    """A router whose OpenRouter side answers with `text` on every route."""
    from types import SimpleNamespace

    class _Completions:
        def __init__(self):
            self.calls = []

        async def create(self, **kwargs):
            self.calls.append(kwargs)
            message = SimpleNamespace(content=text)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)], id="gen-1")

    class _Client:
        def __init__(self):
            self.chat = SimpleNamespace(completions=_Completions())

    router = AITaskRouter(api_key="sk-or-test", google_api_key=google_key)
    router.client = _Client()
    return router


if __name__ == "__main__":
    unittest.main()
