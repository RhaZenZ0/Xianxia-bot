import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim

install_aiosqlite_shim()

from app.battle import matchup_label, suppression_label, vitality_band, vitality_bar, vitality_percentage
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
            created = await self.db.create_character(
                user_id=user_id, discord_name=f"tester-{user_id}", name=f"Tester {user_id}",
                origin="Greenriver Town", path="Sword Cultivator", spiritual_root="Fire",
                concept="battle test", location="Greenriver Town",
                attributes={"body": 2, "agility": 2, "spirit": 2, "insight": 2, "will": 2, "presence": 2},
                qi_max=10, vitality_max=20,
            )
            self.assertTrue(created)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_character_creation_persists_male_or_female_birth_sex(self):
        created = await self.db.create_character(
            user_id=303, discord_name="tester-303", name="Daughter of the Su Clan",
            origin="Su Family — Alchemy Family, Greenriver Town", path="Qi Refiner", spiritual_root="Wood",
            concept="alchemy heir", gender="female", location="Greenriver Town",
            attributes={"body": 2, "agility": 2, "spirit": 2, "insight": 2, "will": 2, "presence": 2},
            qi_max=10, vitality_max=20,
        )
        self.assertTrue(created)
        character = await self.db.get_character(303)
        self.assertEqual(character["gender"], "female")

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

    async def test_exact_battle_binding_rejects_stale_or_foreign_panels(self):
        old_id = await self._battle(101, target="npc:old")
        current_id = await self._battle(101, target="npc:current")
        self.assertIsNone(await self.db.get_battle(old_id, user_id=101, active_only=True))
        self.assertIsNone(await self.db.get_battle(current_id, user_id=202, active_only=True))
        self.assertEqual((await self.db.get_battle(current_id, user_id=101, active_only=True))["battle_id"], current_id)

    async def test_damage_and_recovery_keep_character_and_panel_vitality_in_sync(self):
        battle_id = await self._battle(101)
        damaged = await self.db.apply_battle_damage(battle_id, 101, 5)
        self.assertEqual(damaged["vitality"], 7)
        battle = await self.db.get_active_battle(101)
        character = await self.db.get_character(101)
        self.assertEqual(battle["player_hp"], character["vitality"])
        self.assertEqual(battle["player_hp_max"], 20)

        restored = await self.db.restore_resources(101, vitality=4)
        battle = await self.db.get_active_battle(101)
        self.assertEqual(restored["vitality"], 11)
        self.assertEqual(battle["player_hp"], restored["vitality"])
        self.assertEqual(battle["player_hp_max"], restored["vitality_max"])

    async def test_spare_or_kill_finalization_can_only_be_claimed_once(self):
        battle_id = await self._battle(101)
        await self.db.update_battle(battle_id, npc_hp=0)
        first = await self.db.claim_battle_finalization(101, battle_id, "spare")
        second = await self.db.claim_battle_finalization(101, battle_id, "kill")
        self.assertEqual(first["final_outcome"], "spare")
        self.assertIsNone(second)
        stored = await self.db.get_battle(battle_id, user_id=101)
        self.assertEqual(stored["status"], "won")
        self.assertEqual(stored["final_outcome"], "spare")


if __name__ == "__main__":
    unittest.main()
