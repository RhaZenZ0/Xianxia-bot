"""A hidden master's aim is opaque in every prompt that names an NPC's aim (v1.34.0).

`talk_to_npc` redacted only the `secret` line and printed `want` and `current_goal`
as authored, while `NarratorContextBuilder` had always substituted the goal; the
fake beggar's want ("stop people checking his claims") gave the disguise away in
the one prompt a player talks to him through. Both builders now route the line
through `public_aim`.
"""
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from tests.support import PROJECT_ROOT, install_openai_shim

install_openai_shim()

from app.ai.narrator import Narrator  # noqa: E402
from app.rules.game import World  # noqa: E402
from app.rules.npc_memory import HIDDEN_MASTER_AIM, public_aim  # noqa: E402

WORLD = World(PROJECT_ROOT / "content" / "world.json")


class _CapturingRouter:
    enabled = True
    label = "openrouter[test]"

    def __init__(self):
        self.prompts: list[str] = []

    async def generate(self, **kwargs):
        self.prompts.append(" ".join(str(v) for v in kwargs.values()))
        return SimpleNamespace(text="ok", tier=kwargs["tier"], model="free-test", attempted_models=("free-test",))


def _dialogue_prompt(npc_name: str, *, npc_state: dict | None = None) -> str:
    router = _CapturingRouter()
    narrator = Narrator(world=WORLD, provider="openrouter", ai_router=router)
    character = {
        "name": "Li Wei", "realm_index": 1, "phase": 1, "gender": "male", "origin": "Village orphan",
        "path": "Orthodox", "spiritual_root": "Wood", "location": "Greenriver Town", "concept": "A patient farmer",
        "cultivation": 10, "qi": 20, "qi_max": 20, "vitality": 30, "vitality_max": 30,
    }
    asyncio.run(narrator.talk_to_npc(
        character=character, npc_name=npc_name, player_dialogue="Who are you, really?",
        memory="", history=[], npc_state=npc_state,
    ))
    if not router.prompts:
        raise AssertionError("the dialogue never reached the router, so every assertion below would be vacuous")
    return " ".join(router.prompts)


class HiddenMasterDialogueTests(unittest.TestCase):
    def test_the_shipped_hidden_masters_are_found(self):
        self.assertTrue(WORLD.hidden_masters, "the reader finds no hidden master, so the redaction test proves nothing")
        self.assertIn("Old Gou", WORLD.hidden_masters)

    def test_no_hidden_master_want_reaches_the_dialogue_prompt(self):
        for name, npc in WORLD.hidden_masters.items():
            with self.subTest(npc=name):
                prompt = _dialogue_prompt(name)
                want = str(npc.get("want") or "").strip()
                self.assertTrue(want, f"{name} has no want to hold absent")
                self.assertNotIn(want, prompt, f"the dialogue prompt for hidden master {name} leaks their want: {want!r}")
                self.assertNotIn(str(npc.get("secret") or "\0"), prompt)

    def test_a_director_goal_for_a_hidden_master_is_redacted_too(self):
        prompt = _dialogue_prompt("Old Gou", npc_state={"current_goal": "Sell the forged manual before the fraud is found out"})
        self.assertNotIn("forged manual", prompt)
        self.assertIn(HIDDEN_MASTER_AIM, prompt)

    def test_an_ordinary_npc_keeps_their_want_and_goal(self):
        name, npc = next((n, d) for n, d in WORLD.npcs.items() if not isinstance(d.get("hidden_master"), dict) and d.get("want"))
        prompt = _dialogue_prompt(name, npc_state={"current_goal": "Count the day's takings"})
        self.assertIn(str(npc["want"]), prompt)
        self.assertIn("Count the day's takings", prompt)

    def test_the_helper_asks_for_the_block_not_for_truthiness(self):
        self.assertEqual(public_aim({"hidden_master": {"kind": "real"}}, "a secret aim"), HIDDEN_MASTER_AIM)
        self.assertEqual(public_aim({"hidden_master": None}, "plain aim"), "plain aim")
        self.assertEqual(public_aim({}, "", "fallback"), "fallback")


if __name__ == "__main__":
    unittest.main()
