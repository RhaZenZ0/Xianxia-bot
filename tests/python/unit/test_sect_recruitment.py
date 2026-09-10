import unittest

from tests.support import PROJECT_ROOT

from app.rules.game import World
from app.rules.sect_recruitment import recruitment_definition, trial_modifier

ROOT = PROJECT_ROOT
ATTRS = {"body": 4, "agility": 4, "spirit": 5, "insight": 5, "will": 4, "presence": 5}


class SectRecruitmentTests(unittest.TestCase):
    """Recruitment content and the pure trial/recommendation modifiers.
    (Moved from integration/ in v0.20.3: the seeded database no test read
    is gone; ATTRS stays because the modifier test reads it.)"""

    @classmethod
    def setUpClass(cls):
        cls.world = World(ROOT / "content" / "world.json")

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

    def test_source_contains_story_and_npc_recommendation_gui_paths(self):
        # /sect -> Recruitment moved to app/bot/commands/sect.py in split stage 3
        # (v0.19.30). A check anchored to one file would quietly stop covering
        # some of these the moment the code moved - the exact mistake stage 2's
        # notes warn about - so this checks the whole app/bot package rather
        # than guessing which file each string still lives in.
        source = "".join(
            path.read_text(encoding="utf-8") for path in (ROOT / "app" / "bot").rglob("*.py")
        )
        self.assertIn('name="recruitment"', source)
        self.assertIn('name="recommendation"', source)
        self.assertIn('name="trial"', source)
        self.assertIn("sect_recommender_autocomplete", source)
        self.assertIn("sect.recruitment.trial", source)
        self.assertIn("Sect → Recruitment → Recommendation", source)


if __name__ == "__main__":
    unittest.main()
