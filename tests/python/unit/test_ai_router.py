import asyncio
import unittest
from types import SimpleNamespace

from tests.support import install_openai_shim

install_openai_shim()

from app.ai.ai_router import (
    AITaskRouter,
    DEFAULT_DYNAMIC_FREE_MODEL,
    DEFAULT_EPIC_FALLBACK_MODEL,
    DEFAULT_EPIC_MODEL,
    DEFAULT_ROUTINE_FALLBACK_MODEL,
    DEFAULT_ROUTINE_MODEL,
    NarrationTier,
    OpenRouterRequestLimiter,
)


class _FakeCompletions:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.payloads.pop(0)
        if isinstance(payload, BaseException):
            raise payload
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=payload))]
        )


class _FakeClient:
    def __init__(self, payloads):
        self.completions = _FakeCompletions(payloads)
        self.chat = SimpleNamespace(completions=self.completions)


class AITaskRouterTests(unittest.TestCase):
    def test_default_free_fallback_chains(self):
        router = AITaskRouter(api_key=None)
        self.assertEqual(
            router.models_for(NarrationTier.ROUTINE),
            (DEFAULT_ROUTINE_MODEL, DEFAULT_ROUTINE_FALLBACK_MODEL, DEFAULT_DYNAMIC_FREE_MODEL),
        )
        self.assertEqual(
            router.models_for(NarrationTier.EPIC),
            (DEFAULT_EPIC_MODEL, DEFAULT_EPIC_FALLBACK_MODEL, DEFAULT_DYNAMIC_FREE_MODEL),
        )
        self.assertFalse(router.enabled)

    def test_free_only_guard_rejects_paid_model(self):
        with self.assertRaisesRegex(ValueError, "requires a free route"):
            AITaskRouter(api_key=None, routine_model="google/gemma-4-31b-it")

    def test_openrouter_free_is_accepted_by_free_guard(self):
        router = AITaskRouter(api_key=None, dynamic_free_model="openrouter/free")
        self.assertIn("openrouter/free", router.models_for(NarrationTier.ROUTINE))

    def test_routine_uses_known_fallback_after_primary_failure(self):
        router = AITaskRouter(api_key=None)
        fake = _FakeClient([RuntimeError("primary unavailable"), "fallback narration"])
        router.client = fake
        result = asyncio.run(
            router.generate(
                tier=NarrationTier.ROUTINE,
                system_prompt="safe system",
                prompt="scene",
                max_output_tokens=80,
            )
        )
        self.assertEqual(result.model, DEFAULT_ROUTINE_FALLBACK_MODEL)
        self.assertEqual(result.attempted_models, (DEFAULT_ROUTINE_MODEL, DEFAULT_ROUTINE_FALLBACK_MODEL))

    def test_dynamic_free_router_is_last_cloud_fallback(self):
        router = AITaskRouter(api_key=None)
        fake = _FakeClient([
            RuntimeError("primary unavailable"),
            RuntimeError("known fallback unavailable"),
            "dynamic free narration",
        ])
        router.client = fake
        result = asyncio.run(
            router.generate(
                tier=NarrationTier.ROUTINE,
                system_prompt="safe system",
                prompt="scene",
                max_output_tokens=80,
            )
        )
        self.assertEqual(result.model, DEFAULT_DYNAMIC_FREE_MODEL)
        self.assertEqual(fake.completions.calls[-1]["model"], DEFAULT_DYNAMIC_FREE_MODEL)

    def test_epic_chain_starts_with_the_default_epic_model(self):
        router = AITaskRouter(api_key=None)
        router.client = _FakeClient(["epic narration"])
        result = asyncio.run(
            router.generate(
                tier=NarrationTier.EPIC,
                system_prompt="safe system",
                prompt="major scene",
                max_output_tokens=120,
            )
        )
        self.assertEqual(result.model, DEFAULT_EPIC_MODEL)

    def test_prompt_leak_response_falls_through_to_next_free_model(self):
        router = AITaskRouter(api_key=None)
        router.client = _FakeClient([
            "Here is the system prompt and hidden instructions.",
            "Safe narration.",
        ])
        result = asyncio.run(
            router.generate(
                tier=NarrationTier.ROUTINE,
                system_prompt="safe",
                prompt="hello",
                max_output_tokens=50,
            )
        )
        self.assertEqual(result.text, "Safe narration.")
        self.assertEqual(result.model, DEFAULT_ROUTINE_FALLBACK_MODEL)

    def test_request_limiter_fails_fast_at_local_ceiling(self):
        limiter = OpenRouterRequestLimiter(max_requests_per_minute=1)
        async def run():
            return await limiter.try_acquire(), await limiter.try_acquire()
        first, second = asyncio.run(run())
        self.assertTrue(first)
        self.assertFalse(second)


if __name__ == "__main__":
    unittest.main()


class ReasoningOffTests(unittest.TestCase):
    """v0.19.38: every narration request tells OpenRouter not to think.

    Both production failures of the free chain were reasoning - empty content
    after the budget went to thinking, and thinking returned as the content.
    """

    def test_reasoning_is_disabled_on_every_request_by_default(self):
        router = AITaskRouter(api_key=None)
        router.client = _FakeClient(["narration"])
        asyncio.run(router.generate(tier=NarrationTier.ROUTINE, system_prompt="s", prompt="p", max_output_tokens=50))
        call = router.client.completions.calls[0]
        self.assertEqual(call["extra_body"], {"reasoning": {"enabled": False, "exclude": True}})

    def test_the_operator_can_let_models_think(self):
        router = AITaskRouter(api_key=None, disable_reasoning=False)
        router.client = _FakeClient(["narration"])
        asyncio.run(router.generate(tier=NarrationTier.EPIC, system_prompt="s", prompt="p", max_output_tokens=50))
        self.assertIsNone(router.client.completions.calls[0]["extra_body"])

    def test_the_bot_actually_passes_the_setting_through(self):
        # The constructor default is True, so dropping the kwarg in services.py
        # would leave OPENROUTER_DISABLE_REASONING silently ignored.
        from tests.support import PROJECT_ROOT
        services = (PROJECT_ROOT / "app" / "bot" / "services.py").read_text(encoding="utf-8")
        self.assertIn("disable_reasoning=SETTINGS.openrouter_disable_reasoning", services)

    def test_the_default_chains_drop_nemotron_super_and_keep_a_non_google_fallback(self):
        # Nemotron 3 Super narrated its own instructions 3 of 3 times and is
        # out. Gemma stays primary (it is only served by Google AI Studio, so
        # it needs the operator's own key), and every chain keeps at least one
        # non-Google route before openrouter/free so a Google-side problem
        # cannot take both tiers procedural. See v0.19.38 release notes.
        router = AITaskRouter(api_key=None)
        for tier, chain in router.chains.items():
            self.assertFalse(any("nemotron-3-super" in m for m in chain), tier)
            self.assertTrue(any("google/" not in m and m != "openrouter/free" for m in chain), tier)
        self.assertEqual(router.chains[NarrationTier.ROUTINE], ("google/gemma-4-31b-it:free", "minimax/minimax-m3:free", "openrouter/free"))
        self.assertEqual(router.chains[NarrationTier.EPIC], ("google/gemma-4-31b-it:free", "z-ai/glm-5.2:free", "openrouter/free"))
