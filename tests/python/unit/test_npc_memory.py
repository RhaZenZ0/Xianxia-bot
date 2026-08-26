import unittest

from app.npc_memory import classify_memory, exchange_memory_summary, format_memories, scene_memory_summary


class NPCMemoryTests(unittest.TestCase):
    def test_vows_receive_higher_salience_than_small_talk(self):
        small_kind, small = classify_memory("Good morning.", "Morning.")
        vow_kind, vow = classify_memory("I swear I will return the jade token.", "Then I will remember your word.")
        self.assertEqual(small_kind, "conversation")
        self.assertEqual(vow_kind, "vow")
        self.assertGreater(vow, small)

    def test_memory_summaries_are_grounded_in_actual_exchange(self):
        summary = exchange_memory_summary("Where is the pass?", "Elder Su", "North of the river.")
        self.assertIn("Where is the pass?", summary)
        self.assertIn("North of the river.", summary)
        scene = scene_memory_summary("I bow to Elder Su.", "Elder Su", "Elder Su returns the bow.")
        self.assertIn("I bow to Elder Su.", scene)
        self.assertIn("Elder Su returns the bow.", scene)

    def test_format_memories_keeps_kind_and_salience(self):
        text = format_memories([
            {"memory_kind": "debt", "salience": 75, "summary": "The player repaid an old favor."}
        ])
        self.assertIn("debt", text)
        self.assertIn("75/100", text)
        self.assertIn("repaid an old favor", text)


if __name__ == "__main__":
    unittest.main()
