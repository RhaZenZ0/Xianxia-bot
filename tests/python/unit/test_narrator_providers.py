import asyncio
import unittest
from types import SimpleNamespace

from tests.support import install_openai_shim

install_openai_shim()

from app.ai.ai_router import NarrationTier
from app.ai.narrator import Narrator


class NarratorProviderTests(unittest.TestCase):
    def test_procedural_provider_returns_safe_fallback(self):
        narrator = Narrator(world=None, provider="procedural")  # type: ignore[arg-type]
        text = asyncio.run(narrator._generate("ignored", fallback="safe fallback"))
        self.assertEqual(text, "safe fallback")

    def test_openrouter_provider_routes_routine_and_epic_tiers(self):
        class FakeRouter:
            enabled = True
            label = "openrouter[test]"
            def __init__(self):
                self.tiers = []
            async def generate(self, **kwargs):
                tier = NarrationTier(str(kwargs["tier"]))
                self.tiers.append(tier)
                return SimpleNamespace(
                    text="routed narration",
                    tier=tier,
                    model="free-test",
                    attempted_models=("free-test",),
                )

        router = FakeRouter()
        narrator = Narrator(world=None, provider="openrouter", ai_router=router)  # type: ignore[arg-type]
        first = asyncio.run(narrator._generate("talk", fallback="fallback"))
        second = asyncio.run(narrator._generate("epic", tier="epic", fallback="fallback"))
        self.assertEqual(first, "routed narration")
        self.assertEqual(second, "routed narration")
        self.assertEqual(router.tiers, [NarrationTier.ROUTINE, NarrationTier.EPIC])
        self.assertEqual(narrator.provider_label, "openrouter[test]")

    def test_openrouter_chain_failure_degrades_to_procedural_text(self):
        class FailingRouter:
            enabled = True
            label = "openrouter[test]"
            async def generate(self, **kwargs):
                raise RuntimeError("all free models unavailable")

        narrator = Narrator(world=None, provider="openrouter", ai_router=FailingRouter())  # type: ignore[arg-type]
        text = asyncio.run(narrator._generate("scene", fallback="safe offline narration"))
        self.assertEqual(text, "safe offline narration")

    def test_nas_build_has_no_local_llm_provider(self):
        narrator = Narrator(world=None, provider="procedural")  # type: ignore[arg-type]
        self.assertFalse(hasattr(narrator, "ollama_model"))
        self.assertFalse(hasattr(narrator, "_ollama_request"))


if __name__ == "__main__":
    unittest.main()
