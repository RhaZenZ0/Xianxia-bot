from tests.support import PROJECT_ROOT
import unittest
from pathlib import Path

from app.forbidden_arts import (
    ForbiddenArtsService,
    crime_evidence,
    effective_exposure,
    reaction_policy,
    technique_is_forbidden,
)
from app.game import World
from app.sense import concealment_power, sense_precision_check, spiritual_sense_stats


ROOT = PROJECT_ROOT
WORLD = World(ROOT / "content" / "world.json")


def _character(*, conceal=False):
    return {
        "realm_index": 3,
        "phase": 5,
        "attributes": {"body": 2, "agility": 2, "spirit": 5, "insight": 4, "will": 4, "presence": 2},
        "sense_power_bonus": 2,
        "sense_precision_bonus": 3,
        "sense_range_bonus": 10,
        "concealment_bonus": 1,
        "concealment_active": 1 if conceal else 0,
    }


class SenseModuleTests(unittest.TestCase):
    def test_core_sense_rules_are_used_directly(self):
        c = _character(conceal=True)
        self.assertGreater(spiritual_sense_stats(c)["range_m"], 0)
        self.assertGreaterEqual(concealment_power(c), 0)
        direct = sense_precision_check(c, die1=7, die2=8, target_realm_index=4, extra_tn=2)
        self.assertIn("tier", direct)


class _FakeDB:
    def __init__(self):
        self.karma = 0
        self.crimes = []
        self.reputation = []

    async def adjust_karma(self, user_id, delta, reason):
        self.karma += int(delta)
        return self.karma

    async def record_crime(self, user_id, **kwargs):
        row = {"crime_id": len(self.crimes) + 1, "bounty_id": 9, **kwargs}
        self.crimes.append(row)
        return row

    async def adjust_reputation(self, user_id, faction, delta, reason):
        self.reputation.append((faction, int(delta), reason))
        return int(delta)


class _FakeSimulator:
    def __init__(self):
        self.calls = []

    async def apply_forbidden_art_use(self, **kwargs):
        self.calls.append(kwargs)
        return {"severity": kwargs["exposure"], "witnessed": kwargs["witnessed"], "impacts": ["test impact"]}


class ForbiddenArtsCoreTests(unittest.IsolatedAsyncioTestCase):
    def test_content_driven_reaction_profiles_are_used(self):
        rules = {
            "forbidden_arts": {
                "orthodox_public_use": {
                    "regional_unrest": 4,
                    "family_stability_loss": 3,
                    "sect_cohesion_loss": 2,
                    "karma_multiplier": 1,
                },
                "demonic_public_use": {
                    "regional_unrest": 1,
                    "family_stability_loss": 1,
                    "sect_cohesion_loss": 0,
                    "karma_multiplier": 1,
                },
                "concealed_use_reduces_exposure": True,
            }
        }
        self.assertEqual(reaction_policy(rules, sect_alignment="Orthodox")["regional_unrest"], 4)
        self.assertEqual(reaction_policy(rules, sect_alignment="Demonic")["sect_cohesion_loss"], 0)
        self.assertEqual(effective_exposure(9, witnessed=False, world_rules=rules), 3)

    async def test_orthodox_technique_does_not_trigger_forbidden_consequences(self):
        db = _FakeDB()
        sim = _FakeSimulator()
        service = ForbiddenArtsService(db, sim)
        technique = {"name": "Azure Cloud Slash", "tags": ["sword"], "karma_cost": 0, "exposure": 2}
        manual = {"alignment": "Orthodox"}
        self.assertFalse(technique_is_forbidden(technique, manual))
        result = await service.resolve_technique_use(
            user_id=1,
            technique_id="azure_cloud_slash",
            technique=technique,
            manual=manual,
            location="Greenriver Town",
            game_minute=50,
            concealment_active=False,
            current_karma=7,
        )
        self.assertFalse(result.forbidden)
        self.assertEqual(result.karma_score, 7)
        self.assertEqual(sim.calls, [])
        self.assertEqual(db.crimes, [])
        self.assertEqual(db.reputation, [])

    async def test_forbidden_technique_centralizes_karma_world_crime_and_reputation(self):
        db = _FakeDB()
        sim = _FakeSimulator()
        service = ForbiddenArtsService(db, sim)
        technique = {"name": "Soul Rend", "tags": ["forbidden", "soul"], "karma_cost": 4, "exposure": 7}
        manual = {"alignment": "Demonic"}
        result = await service.resolve_technique_use(
            user_id=1,
            technique_id="soul_rend",
            technique=technique,
            manual=manual,
            location="Greenriver Town",
            game_minute=60,
            concealment_active=True,
            current_karma=0,
            randbelow=lambda n: 0,
        )
        self.assertTrue(result.forbidden)
        self.assertTrue(result.witnessed)
        self.assertEqual(result.karma_score, -4)
        self.assertEqual(len(sim.calls), 1)
        self.assertEqual(len(db.crimes), 1)
        self.assertEqual(db.crimes[0]["evidence"], crime_evidence(7))
        self.assertEqual({x[0] for x in db.reputation}, {"Orthodox Society", "Demonic Circles"})
        self.assertEqual(result.impacts, ["test impact"])


if __name__ == "__main__":
    unittest.main()
