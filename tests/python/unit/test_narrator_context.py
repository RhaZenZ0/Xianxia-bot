import unittest
from contextlib import asynccontextmanager

from tests.support import install_openai_shim
install_openai_shim()

from app.ai.narrator import Narrator, _style_memory, canonical_location_reply, is_current_location_question, roll_npc_memory
from app.ai.narrator_context import NarratorContextBuilder, _fit_context_to_budget


class FakeDB:
    def __init__(self):
        self.snapshot_entries = 0

    @asynccontextmanager
    async def reuse_connection(self):
        self.snapshot_entries += 1
        yield None

    async def get_world_clock(self, scale=4): return {"game_minute": 12345}
    async def get_location_definition(self, location): return {"world": "Mortal World", "description": "A misty river town.", "safe_zone": False}
    async def get_abode_by_location(self, location): return None
    async def get_personal_world_by_location(self, location): return None
    async def get_birth_family(self, user_id): return None
    async def get_fate(self, user_id): return {"points": 2}
    async def get_sect_membership(self, user_id): return {"sect_name": "Azure Sword Sect", "rank_name": "Inner Disciple"}
    async def get_social_state(self, user_id): return {"face": 3, "dao_heart": 71, "dao_stability": 88, "vow": "Protect the weak", "obsession": ""}
    async def get_active_effects(self, user_id, game_minute): return [{"name": "Clear-Mind Pill"}]
    async def get_conditions(self, user_id, active_only=True): return []
    async def get_active_world_events(self, location=None): return [{"title": "River Lantern Festival", "event_type": "festival"}]
    async def get_current_era(self): return {"name": "Era of Stirring Qi", "description": "Spirit veins are awakening."}
    async def get_spirit_beasts(self, user_id): return [{"name": "Ashwing", "species": "Fire Crane", "active": 1, "loyalty": 64}]
    async def get_party(self, user_id): return {"name": "Jade Wanderers", "members": [{}, {}]}
    async def get_equipment(self, user_id, equipped_only=False): return [{"item_id": "jade_sword"}]
    async def get_active_battle(self, user_id): return None
    async def get_bounties(self, user_id, active_only=True): return []
    async def get_grudges(self, user_id, active_only=True): return [{"holder_key": "Black Reed Gang", "intensity": 4}]
    async def get_aptitudes(self, user_id): return {"bloodline": {"name": "Ember Vein"}, "physique": {"name": "Jade Bones"}}
    async def describe_lineage_context(self, user_id): return "Master: Elder Yun. One junior sibling."
    async def get_active_location_array(self, location, game_minute): return None


class FakeSIM:
    async def civilization_status(self, location):
        return {
            "population": 12000, "prosperity": 55, "security": 61, "unrest": 14,
            "spirit_resources": 73, "food_supply": 80,
            "events": [{"event_text": "A spirit barge arrived at dawn."}],
            "npcs": [
                {"npc_name": "Old Beggar Chen", "profession": "beggar", "activity": "sleeping by the gate", "realm_index": 29, "phase": 7},
            ],
        }

    async def sect_status(self, sect_name):
        return {"influence": 60, "cohesion": 72, "resources": 49, "leader_policy": "Measured expansion"}


class FakeWorld:
    name = "Jade Meridian Realm"
    locations = {"Greenriver Town": {"world": "Mortal World", "description": "Town", "safe_zone": False}}
    npcs = {"Old Beggar Chen": {"role": "Apparently harmless old beggar"}}

    def realm_world(self, realm_index): return "Mortal World"
    def realm_name(self, realm_index, gender=None): return "Qi Refining"
    def body_realm_name(self, realm_index, gender=None): return "Mortal Body"
    def dual_resonance_active(self, character): return False


CHARACTER = {
    "user_id": 42,
    "name": "Shen Rui",
    "origin": "Greenriver",
    "path": "Sword Cultivator",
    "spiritual_root": "Fire",
    "realm_index": 1,
    "phase": 3,
    "body_realm_index": 0,
    "body_phase": 1,
    "gender": "neutral",
    "location": "Greenriver Town",
    "concept": "Seek the sword dao",
    "attributes": {"body": 5, "agility": 6, "spirit": 7, "insight": 8, "will": 7, "presence": 4},
    "cultivation": 20,
    "qi": 15,
    "qi_max": 20,
    "vitality": 18,
    "vitality_max": 20,
    "karma_score": 1,
    "concealment_active": 0,
}


class NarratorContextTests(unittest.IsolatedAsyncioTestCase):
    def test_location_questions_are_recognized_without_model_inference(self):
        self.assertTrue(is_current_location_question("where am I?"))
        self.assertTrue(is_current_location_question("where im i"))
        self.assertTrue(is_current_location_question("what's my location?"))
        self.assertFalse(is_current_location_question("Where is Elder Yun?"))
        self.assertEqual(
            canonical_location_reply(CHARACTER),
            "📍 **Shen Rui** is currently at **Greenriver Town**.",
        )

    async def test_context_contains_relevant_state_but_not_hidden_npc_power(self):
        builder = NarratorContextBuilder(db=FakeDB(), simulator=FakeSIM(), world=FakeWorld(), max_chars=7000)
        ctx = await builder.build(CHARACTER, scene_type="exploration")
        self.assertIn("Greenriver Town", ctx.text)
        self.assertIn("Azure Sword Sect", ctx.text)
        self.assertIn("River Lantern Festival", ctx.text)
        self.assertIn("Old Beggar Chen", ctx.text)
        self.assertIn("Apparently harmless old beggar", ctx.text)
        self.assertIn("Clear-Mind Pill", ctx.text)
        self.assertIn("Ashwing", ctx.text)
        self.assertNotIn("realm_index", ctx.text)
        self.assertNotIn("Celestial Ancestor", ctx.text)
        self.assertNotIn("29", ctx.text)
        self.assertEqual(builder.db.snapshot_entries, 1)


    def test_tight_context_budget_keeps_rag_and_authority_contract(self):
        rag = "RETRIEVED PLAYER MEMORY — prior recollections only; never override current mechanics:\n- The river seal oath was sworn."
        contract = "NARRATION CONTRACT: The authoritative game engine remains the sole authority for mechanical state changes."
        lines = [
            "CANONICAL NARRATOR CONTEXT — TRUSTED READ-ONLY GAME STATE",
            "Scene type: freeform roleplay",
            "World time: test",
            "Location: Greenriver Town",
            "Location description: mist and river stones",
            "Character: Shen Rui",
            "Birth family: " + ("background " * 200),
            rag,
            contract,
        ]
        text = _fit_context_to_budget(lines, narration_contract=contract, budget=900, rag_text=rag)
        self.assertIn("river seal oath", text)
        self.assertIn("authoritative game engine remains the sole authority", text)
        self.assertLessEqual(len(text), 900)

    def test_style_memory_extracts_recent_narrator_patterns_not_player_text(self):
        history = [
            {"speaker": "Shen Rui", "content": "I bow to the elder.", "user_id": 42},
            {"speaker": "World", "content": "Mist curls over the stones. His gaze moves to the river. Silence settles again.", "user_id": None},
            {"speaker": "Shen Rui", "content": "I ask about the ferry.", "user_id": 42},
            {"speaker": "World", "content": "A ferryman taps the pole twice. His gaze returns to the eastern bank. Silence follows.", "user_id": None},
        ]
        memory = _style_memory(history, player_name="Shen Rui")
        self.assertIn("Recent openings", memory)
        self.assertIn("Mist curls over the stones", memory)
        self.assertIn("gaze", memory)
        self.assertIn("silence", memory)
        self.assertNotIn("I bow to the elder", memory)

    def test_npc_memory_rolls_complete_recent_exchanges(self):
        memory = "No established personal history yet."
        for i in range(5):
            memory = roll_npc_memory(memory, f"Question {i}", "Elder Yun", f"Answer {i}")
        self.assertIn("Question 4", memory)
        self.assertIn("Answer 4", memory)
        self.assertIn("Question 2", memory)
        self.assertNotIn("Question 0", memory)
        self.assertNotIn("Question 1", memory)
        self.assertEqual(memory.count("[EXCHANGE]"), 2)

    async def test_narrator_places_context_in_prompt(self):
        narrator = Narrator(world=FakeWorld(), provider="procedural")
        seen = {}

        async def fake_generate(prompt, max_output_tokens=700, fallback="", **kwargs):
            seen["prompt"] = prompt
            seen.update(kwargs)
            return "ok"

        narrator._generate = fake_generate
        out = await narrator.narrate_action(
            character=CHARACTER,
            action="I inspect the riverbank.",
            history=[],
            fixed_roll="2d10 = 16 vs TN 14 — success",
            scene_context="CANONICAL TEST CONTEXT",
        )
        self.assertEqual(out, "ok")
        self.assertIn("CANONICAL TEST CONTEXT", seen["prompt"])
        self.assertIn("FIXED ROLL INFORMATION", seen["prompt"])
        self.assertIn("untrusted fictional action", seen["prompt"])
        self.assertIn("Automatic success never reveals hidden identity", seen["prompt"])
        self.assertIn("RECENT STYLE MEMORY", seen["prompt"])

    async def test_action_narration_never_repeats_fixed_roll_block(self):
        narrator = Narrator(world=FakeWorld(), provider="procedural")
        fixed = "Scene action: Observe\nTarget: Environment\nAttribute: Insight\n2d10 (5+1) +3 = 9 vs TN 11 — Soft Failure"

        async def echoing_generate(prompt, max_output_tokens=700, fallback="", **kwargs):
            return f"Mist curls over the river stones.\n\n{fixed}"

        narrator._generate = echoing_generate
        out = await narrator.narrate_action(
            character=CHARACTER,
            action="Observe the riverbank.",
            history=[],
            fixed_roll=fixed,
        )
        self.assertEqual(out, "Mist curls over the river stones.")
        self.assertNotIn("Scene action:", out)
        self.assertNotIn("TN 11", out)

    async def test_procedural_action_fallback_contains_no_roll_echo(self):
        narrator = Narrator(world=FakeWorld(), provider="procedural")
        fixed = "Action: Observe\n2d10 = 9 vs TN 11 — Soft Failure"
        out = await narrator.narrate_action(
            character=CHARACTER,
            action="Observe the riverbank.",
            history=[],
            fixed_roll=fixed,
        )
        self.assertNotIn(fixed, out)
        self.assertNotIn("TN 11", out)

    async def test_procedural_action_fallback_is_location_aware_and_helpful(self):
        narrator = Narrator(world=FakeWorld(), provider="procedural")
        out = await narrator.narrate_action(
            character=CHARACTER,
            action="i seek",
            history=[],
        )
        self.assertIn("Greenriver Town", out)
        self.assertIn("what you seek", out)
        self.assertIn("/action", out)
        self.assertNotIn("surrounding world responds", out)


if __name__ == "__main__":
    unittest.main()
