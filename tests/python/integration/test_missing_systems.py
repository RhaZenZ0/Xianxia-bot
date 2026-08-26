import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim
install_aiosqlite_shim()

from app.database import Database
from app.progression_systems import condition_effect, condition_definition, ascension_gate, profession_rank


class MissingSystemsDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "systems.sqlite3")
        await self.db.init()
        ok = await self.db.create_character(
            user_id=501, discord_name="systems", name="Systems Test", origin="Greenriver Town",
            path="Sword Cultivator", spiritual_root="Fire", concept="integration test",
            location="Greenriver Town",
            attributes={"body":4,"agility":3,"spirit":4,"insight":4,"will":4,"presence":2},
            qi_max=30, vitality_max=30, created_game_minute=0,
        )
        self.assertTrue(ok)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_persistent_condition_uses_generic_effect_pipeline(self):
        definition = condition_definition("meridian_damage")
        applied = await self.db.apply_condition(
            501, condition_key="meridian_damage", category=definition["category"],
            name=definition["name"], severity=2, source_type="test", source_id="1",
            effect=condition_effect("meridian_damage", 2), game_minute=100,
        )
        self.assertEqual(applied["severity"], 2)
        effects = await self.db.get_active_effects(501, 100)
        self.assertTrue(any(e.get("effect_key") == "condition:meridian_damage" for e in effects))
        still = await self.db.set_condition_severity(
            501, "meridian_damage", severity=1,
            effect=condition_effect("meridian_damage", 1), game_minute=120,
        )
        self.assertEqual(still["severity"], 1)
        resolved = await self.db.set_condition_severity(
            501, "meridian_damage", severity=0, effect=None, game_minute=140,
        )
        self.assertIsNone(resolved)
        effects = await self.db.get_active_effects(501, 140)
        self.assertFalse(any(e.get("effect_key") == "condition:meridian_damage" for e in effects))

    async def test_tribulation_preparation_and_clearance_persist(self):
        self.assertEqual(ascension_gate(7)["to_world"], "Spiritual World")
        state = await self.db.add_tribulation_preparation(501, 7, amount=2, game_minute=200)
        self.assertEqual(state["preparation"], 2)
        state = await self.db.record_tribulation_attempt(
            501, 7, preparation_used=2,
            waves=[{"name":"Lightning","success":True},{"name":"Heart","success":True},{"name":"Void","success":False}],
            success=True, game_minute=220,
        )
        self.assertEqual(state["preparation"], 0)
        self.assertEqual(state["attempts"], 1)
        self.assertEqual(state["cleared"], 1)
        history = await self.db.get_tribulation_attempts(501)
        self.assertEqual(len(history), 1)
        self.assertEqual(len(history[0]["waves"]), 3)

    async def test_profession_mastery_is_separate_from_realm(self):
        state = None
        for _ in range(7):
            state = await self.db.record_profession_practice(
                501, "Alchemy", success=True, xp_gain=12, quality_points=2,
            )
        self.assertIsNotNone(state)
        self.assertGreaterEqual(int(state["level"]), 1)
        self.assertEqual(profession_rank(int(state["level"])), "Apprentice")
        self.assertEqual(int(state["successes"]), 7)
        character = await self.db.get_character(501)
        self.assertEqual(int(character["realm_index"]), 0)

    async def test_crime_evidence_can_create_bounty_and_grudge(self):
        rep = await self.db.adjust_reputation(501, "Orthodox Society", -20, reason="forbidden art")
        self.assertEqual(rep, -20)
        crime = await self.db.record_crime(
            501, jurisdiction="Greenriver Town", crime_type="forbidden_cultivation",
            severity=5, evidence=80, description="public soul art", game_minute=300,
            witness_type="public", witness_key="town_square",
        )
        self.assertIsNotNone(crime["bounty_id"])
        witnesses = await self.db.get_witnesses(crime["crime_id"])
        self.assertEqual(len(witnesses), 1)
        bounties = await self.db.get_bounties(501)
        self.assertEqual(len(bounties), 1)
        resolved = await self.db.resolve_crime(501, crime["crime_id"], status="atoned")
        self.assertEqual(resolved["status"], "atoned")
        self.assertEqual(await self.db.get_bounties(501), [])
        grudge = await self.db.add_grudge(
            501, holder_type="victim_lineage", holder_key="Lin Clan", intensity=4,
            reason="blood debt", game_minute=300,
        )
        self.assertEqual(grudge["intensity"], 4)
        grudge = await self.db.add_grudge(
            501, holder_type="victim_lineage", holder_key="Lin Clan", intensity=3,
            reason="blood debt deepened", game_minute=310,
        )
        self.assertEqual(grudge["intensity"], 7)


if __name__ == "__main__":
    unittest.main()
