import json
import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT, seed_character
install_aiosqlite_shim()

from app.database import Database, SCHEMA_VERSION
from app.game import World
from app.sect_recruitment import (
    recruitment_definition,
    trial_modifier,
    recommendation_modifier,
    trial_outcome,
)

ROOT = PROJECT_ROOT
ATTRS = {"body": 4, "agility": 4, "spirit": 5, "insight": 5, "will": 4, "presence": 5}


class SectRecruitmentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "sect.sqlite3")
        await self.db.init()
        ok = await seed_character(self.db, 
            user_id=1701, discord_name="candidate", name="Mei Lan",
            origin="Greenriver Town", path="Qi Refiner", spiritual_root="Fire",
            concept="become a great alchemist", location="Greenriver Town",
            attributes=ATTRS, qi_max=20, vitality_max=20, created_game_minute=10,
        )
        self.assertTrue(ok)
        self.world = World(ROOT / "content" / "world.json")

    async def asyncTearDown(self):
        self.tmp.cleanup()


    def test_all_public_sects_have_story_recruitment_configuration(self):
        configured = {name: recruitment_definition(self.world.sects, name) for name in self.world.sects}
        configured = {name: rec for name, rec in configured.items() if rec is not None}
        self.assertGreaterEqual(len(configured), 6)
        for name, rec in configured.items():
            self.assertIn(rec["location"], self.world.locations)
            self.assertTrue(rec.get("trial_name"))
            self.assertTrue(rec.get("examiner"))
            self.assertTrue(rec.get("description"))

    def test_recommender_npcs_exist_for_every_sect(self):
        recommended = {str(npc.get("sect_affiliation")) for npc in self.world.npcs.values() if npc.get("can_recommend")}
        configured = {name for name in self.world.sects if recruitment_definition(self.world.sects, name)}
        self.assertTrue(configured.issubset(recommended))

    def test_trial_modifiers_reward_matching_path_family_and_recommendation(self):
        char = {
            "attributes": ATTRS, "path": "Qi Refiner", "spiritual_root": "Fire", "karma_score": 20,
        }
        rec = recruitment_definition(self.world.sects, "Crimson Furnace Sect")
        mod, notes, rejection = trial_modifier(
            char, rec, attribute="insight", family={"archetype": "alchemy_family"}, recommendation_bonus=2
        )
        self.assertIsNone(rejection)
        self.assertGreater(mod, ATTRS["insight"])
        text = " ".join(notes)
        self.assertIn("family tradition", text)
        self.assertIn("NPC recommendation", text)

    def test_trial_outcome_allows_sponsor_backed_conditional_pass(self):
        self.assertEqual(trial_outcome(1, -3, has_recommendation=True), "conditional_pass")
        self.assertEqual(trial_outcome(1, -3, has_recommendation=False), "fail")
        self.assertEqual(trial_outcome(2, 1, has_recommendation=False), "pass")

    def test_source_contains_story_and_npc_recommendation_gui_paths(self):
        source = (ROOT / "app" / "bot" / "main.py").read_text(encoding="utf-8")
        self.assertIn('name="recruitment"', source)
        self.assertIn('name="recommendation"', source)
        self.assertIn('name="trial"', source)
        self.assertIn("sect_recommender_autocomplete", source)
        self.assertIn("sect.recruitment.trial", source)
        self.assertIn("Sect → Recruitment → Recommendation", source)


if __name__ == "__main__":
    unittest.main()
