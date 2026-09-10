import unittest

from tests.support import PROJECT_ROOT

from app.rules.aptitudes import aptitude_effects, root_compatibility
from app.rules.game import World


ROOT = PROJECT_ROOT


class SequenceRoll:
    def __init__(self, *values: int):
        self.values = list(values)

    def __call__(self, ceiling: int) -> int:
        value = self.values.pop(0) if self.values else 0
        return int(value) % ceiling


class AptitudeRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World(ROOT / "content" / "world.json")

    def test_root_compatibility_uses_path_and_multiple_elements(self):
        sword = root_compatibility(["Metal", "Wind"], "Sword Cultivator", self.world.spiritual_root_system)
        soul = root_compatibility(["Metal", "Wind"], "Soul Cultivator", self.world.spiritual_root_system)
        self.assertGreater(sword, soul)

    def test_awakened_traits_share_the_generic_effect_pipeline(self):
        bundle = {
            "root": {
                "grade": "Earth", "purity": 80, "elements": ["Earth"], "mutation": "",
                "stability": 90, "refinement_progress": 0,
            },
            "bloodline": {
                "bloodline_id": "stone_bear", "name": "Stone Bear Ancestry", "state": "awakened",
                "evolution_stage": 1, "purity": 60, "rejection": 0,
            },
            "physique": {
                "physique_id": "vajra_bone_body", "name": "Vajra Bone Body", "state": "awakened",
                "evolution_stage": 1, "stability": 90, "instability": 0,
            },
        }
        effects = aptitude_effects(
            bundle,
            path="Body Refiner",
            root_system=self.world.spiritual_root_system,
            bloodline_definitions=self.world.bloodlines,
            physique_definitions=self.world.physiques,
        )
        self.assertEqual({effect["category"] for effect in effects}, {"Spiritual Root", "Bloodline", "Physique"})
        self.assertTrue(any(mod["stat"] == "agility" and mod["value"] < 0 for effect in effects for mod in effect["modifiers"]))

if __name__ == "__main__":
    unittest.main()
