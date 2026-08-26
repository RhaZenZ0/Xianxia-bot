import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT

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


class AptitudeDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "aptitudes.sqlite3")
        await self.db.init()
        self.world = World(ROOT / "content" / "world.json")
        self.family = {
            "id": "body_tempering_family", "family_name": "Han Family", "surname": "Han",
            "tier": 3, "wealth": 50, "influence": 40, "stability": 70, "alignment_bias": 0,
            "location": "Greenriver Town", "head_name": "Han Wei", "head_gender": "male",
            "head_title": "Patriarch", "head_realm_index": 2, "head_phase": 3,
            "bloodline_name": "Stone Bear Ancestry", "bloodline_affinity": "Earth",
            "bloodline_trait": "Powerful physique", "bloodline_purity": 70,
            "clan_structure": "martial_household", "branch_count": 1, "retainer_count": 4,
            "confederacy_name": "None", "birth_order": 1, "relatives": [],
        }
        self.profile = generate_aptitude_bundle(
            base_root="Earth",
            path="Body Refiner",
            family=self.family,
            root_system=self.world.spiritual_root_system,
            bloodline_definitions=self.world.bloodlines,
            physique_definitions=self.world.physiques,
            randbelow=SequenceRoll(700, 10, 99, 99, 0, 0, 0),
        )
        created = await self.db.create_character(
            user_id=7, discord_name="Tester", name="Han Rui", origin="Han Family",
            path="Body Refiner", spiritual_root="Earth", concept="Test aptitudes",
            location="Greenriver Town", attributes={"body": 3, "agility": 1, "spirit": 1, "insight": 2, "will": 3, "presence": 2},
            qi_max=10, vitality_max=16, birth_family_profile=self.family, aptitude_profile=self.profile,
        )
        self.assertTrue(created)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_profile_round_trip_and_atomic_tempering(self):
        bundle = await self.db.get_aptitudes(7)
        self.assertEqual(bundle["root"]["elements"], self.profile["root"]["elements"])
        self.assertEqual(bundle["bloodline"]["name"], "Stone Bear Ancestry")
        await self.db.reward(7, cultivation=50)
        trained = await self.db.train_aptitude(7, target="bloodline", essence_cost=10, progress_gain=14)
        self.assertEqual(trained["bloodline"]["progress"], 14)
        character = await self.db.get_character(7)
        self.assertEqual(character["cultivation"], 40)

    async def test_harmonization_reduces_persistent_rejection(self):
        bloodline = dict((await self.db.get_aptitudes(7))["bloodline"])
        bloodline["rejection"] = 65
        await self.db.save_bloodline_profile(7, bloodline)
        await self.db.reward(7, cultivation=30)
        updated = await self.db.harmonize_aptitude(7, target="bloodline", essence_cost=5, amount=12)
        self.assertEqual(updated["bloodline"]["rejection"], 53)


if __name__ == "__main__":
    unittest.main()
