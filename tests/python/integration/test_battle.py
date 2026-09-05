import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, seed_character

install_aiosqlite_shim()

from app.rules.battle import matchup_label, suppression_label, vitality_band, vitality_bar, vitality_percentage
from app.database import Database


class BattlePresentationTests(unittest.TestCase):
    def test_vitality_bars_include_color_counts_and_percentages(self):
        self.assertEqual(vitality_percentage(8, 10), 80)
        self.assertIn("🟩", vitality_bar(8, 10))
        self.assertIn("8/10", vitality_bar(8, 10))
        self.assertIn("80%", vitality_bar(8, 10))
        self.assertEqual(vitality_band(5, 10)[0], "🟨")
        self.assertEqual(vitality_band(1, 10)[0], "🟥")

    def test_matchup_and_suppression_labels_cover_battle_context(self):
        self.assertIn("advantage", matchup_label(3, 5, 1, 5).lower())
        self.assertIn("Evenly", matchup_label(2, 4, 2, 4))
        self.assertEqual(suppression_label(0), "None")
        self.assertIn("2 counters", suppression_label(2))


class BattleDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "battle.sqlite3")
        await self.db.init()
        for user_id in (101, 202):
            created = await seed_character(self.db,
                user_id=user_id, discord_name=f"tester-{user_id}", name=f"Tester {user_id}",
                origin="Greenriver Town", path="Sword Cultivator", spiritual_root="Fire",
                concept="battle test", location="Greenriver Town",
                attributes={"body": 2, "agility": 2, "spirit": 2, "insight": 2, "will": 2, "presence": 2},
                qi_max=10, vitality_max=20,
            )
            self.assertTrue(created)

    async def asyncTearDown(self):
        self.tmp.cleanup()


    async def _battle(self, user_id: int, *, target: str = "npc:one") -> int:
        return await self.db.create_battle(
            user_id=user_id, npc_name="Named Opponent", npc_realm_index=1, npc_stage=2,
            player_hp=12, player_hp_max=20, npc_hp=15, location="Greenriver Town",
            source=target, target_key=target,
        )

    async def test_named_target_can_only_have_one_active_challenger(self):
        battle_id = await self._battle(101, target="challenge:npc:Named Opponent")
        with self.assertRaisesRegex(ValueError, "already locked"):
            await self._battle(202, target="challenge:npc:Named Opponent")
        self.assertEqual((await self.db.get_active_battle(101))["battle_id"], battle_id)
        self.assertIsNone(await self.db.get_active_battle(202))


if __name__ == "__main__":
    unittest.main()
