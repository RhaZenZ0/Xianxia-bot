import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT, seed_character

install_aiosqlite_shim()

from app.aptitudes import (
    aptitude_effects,
    generate_aptitude_bundle,
    generate_root_profile,
    progression_requirements,
    root_compatibility,
    unlocked_ancestral_techniques,
)
from app.database import Database
from app.game import World


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

    def test_high_grade_root_can_be_mixed_and_mutated(self):
        profile = generate_root_profile(
            base_root="Fire",
            path="Qi Refiner",
            system=self.world.spiritual_root_system,
            family_tier=5,
            talent_echo=100,
            randbelow=SequenceRoll(999, 20, 0, 0, 0, 0, 0),
        )
        self.assertEqual(profile["grade"], "Immortal")
        self.assertGreaterEqual(len(profile["elements"]), 2)
        self.assertTrue(profile["mutation"])
        self.assertTrue(1 <= profile["purity"] <= 100)

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

    def test_bloodline_unlocks_ancestral_techniques_by_stage_and_purity(self):
        bloodline = {"state": "evolved", "evolution_stage": 2, "purity": 60}
        names = unlocked_ancestral_techniques(bloodline, self.world.bloodlines["azure_wolf"])
        self.assertEqual(names, ["Hundred-Li Scent", "Pack Sovereign Howl"])

    def test_progression_requirements_block_unprepared_awakenings(self):
        bundle = {
            "bloodline": {
                "bloodline_id": "azure_wolf", "state": "dormant", "progress": 20,
                "purity": 50, "rejection": 0,
            }
        }
        problems = progression_requirements(
            "bloodline", bundle, {"realm_index": 0},
            root_system=self.world.spiritual_root_system,
            bloodline_definitions=self.world.bloodlines,
            physique_definitions=self.world.physiques,
            action="awaken",
        )
        self.assertIn("Bloodline tempering must reach 100%.", problems)



if __name__ == "__main__":
    unittest.main()
