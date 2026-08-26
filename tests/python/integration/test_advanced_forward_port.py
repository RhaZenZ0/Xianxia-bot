import json
import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT
install_aiosqlite_shim()

from app.database import Database
from app.game import World


class AdvancedForwardPortTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "advanced.sqlite3")
        await self.db.init()
        attrs = {"body": 4, "agility": 4, "spirit": 5, "insight": 4, "will": 4, "presence": 3}
        for uid, name in ((701, "Azure"), (702, "Crimson"), (703, "Jade")):
            self.assertTrue(await self.db.create_character(
                user_id=uid, discord_name=name.lower(), name=name, origin="Greenriver Town",
                path="Beast Binder" if uid == 701 else "Sword Cultivator", spiritual_root="Fire",
                concept="advanced forward port test", location="Greenriver Town", attributes=attrs,
                qi_max=30, vitality_max=30, created_game_minute=0,
            ))
        self.world = World(PROJECT_ROOT / "content" / "world.json")

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_01_catalog_matches_advanced_branch_scale(self):
        self.assertEqual(len(self.world.manuals), 148)
        self.assertEqual(len(self.world.techniques), 528)
        evil_manuals = [m for m in self.world.manuals.values() if str(m.get("alignment", "")).lower() == "demonic"]
        evil_techniques = [t for t in self.world.techniques.values() if str((self.world.manuals.get(str(t.get("manual"))) or {}).get("alignment", "")).lower() == "demonic"]
        self.assertEqual(len(evil_manuals), 42)
        self.assertEqual(len(evil_techniques), 160)
        hidden = self.world.sects.get("Heaven-Devouring Demon Sect")
        self.assertTrue(hidden and hidden.get("hidden"))
        self.assertEqual(hidden.get("karma_initiation"), -200)
        self.assertIn("physical_laws", self.world.world_rules)
        self.assertIn("social_laws", self.world.world_rules)

    async def test_02_technique_executability_matrix_uses_39_subtests(self):
        # 39 deterministic samples exercise the generated content contract without
        # turning this into 528 nearly identical test methods.
        samples = sorted(self.world.techniques.items())[:39]
        self.assertEqual(len(samples), 39)
        for technique_id, technique in samples:
            with self.subTest(technique=technique_id):
                self.assertIn(str(technique.get("manual")), self.world.manuals)
                self.assertTrue(str(technique.get("name", "")).strip())
                self.assertGreaterEqual(int(technique.get("qi_cost", 0)), 0)
                self.assertTrue(
                    int(technique.get("damage", 0)) > 0
                    or int(technique.get("heal", 0)) > 0
                    or int(technique.get("suppress_turns", 0)) > 0
                )

    async def test_03_catalog_sync_seeds_sqlite_territories_and_world_era(self):
        await self.db.sync_world_catalog(self.world.data)
        async with self.db._connect() as conn:
            cur = await conn.execute("SELECT COUNT(*) FROM catalog_manuals")
            self.assertEqual((await cur.fetchone())[0], 148)
            cur = await conn.execute("SELECT COUNT(*) FROM catalog_techniques")
            self.assertEqual((await cur.fetchone())[0], 528)
        territories = await self.db.get_territories()
        self.assertEqual(len(territories), len(self.world.locations))
        era = await self.db.get_current_era()
        self.assertEqual(era["name"], "Jade Meridian Awakening Era")

    async def test_04_spirit_beast_contract_training_activation_and_evolution(self):
        wolf = await self.db.add_spirit_beast(701, name="Mistclaw", species="Mistclaw Wolf", rank=1, contract_type="equality", active=True)
        self.assertEqual(wolf["contract_type"], "equality")
        for _ in range(7):
            wolf = await self.db.train_spirit_beast(701, wolf["beast_id"], 6)
        self.assertGreaterEqual(wolf["loyalty"], 60)
        evolved = await self.db.evolve_spirit_beast(701, wolf["beast_id"])
        self.assertIsNotNone(evolved)
        self.assertEqual(evolved["evolution_stage"], 1)
        crane = await self.db.add_spirit_beast(701, name="Cloudwing", species="Spirit Crane", active=False)
        self.assertTrue(await self.db.set_active_spirit_beast(701, crane["beast_id"]))
        active = next(x for x in await self.db.get_spirit_beasts(701) if x["active"])
        self.assertEqual(active["beast_id"], crane["beast_id"])

    async def test_05_artifact_provenance_and_dao_heart_state_persist(self):
        await self.db.add_items(701, {"beast_core": 1})
        bond = None
        for _ in range(3):
            bond = await self.db.bond_artifact(701, "beast_core")
        self.assertGreaterEqual(bond["bond_level"], 3)
        awakened = await self.db.awaken_artifact(701, "beast_core", "Little Ember")
        self.assertTrue(awakened["awakened"])
        pid = await self.db.record_item_provenance(
            701, "beast_core", source_type="hunt", source_key="Mistclaw Wolf",
            ownership_mark="Azure", authenticity=100, tracking_strength=15, game_minute=100,
        )
        self.assertGreater(pid, 0)
        prov = await self.db.get_item_provenance(701, "beast_core")
        self.assertEqual(prov[0]["ownership_mark"], "Azure")
        social = await self.db.adjust_social_state(701, face_delta=12, dao_heart_delta=7, stability_delta=-3, vow="Protect the weak")
        hidden = await self.db.initiate_hidden_sect(701, sect_name="Heaven-Devouring Demon Sect", branch_name="Ashen Veil Cell", game_minute=120)
        self.assertEqual(hidden["status"], "active")
        self.assertEqual(hidden["branch_name"], "Ashen Veil Cell")
        self.assertEqual(social["face"], 12)
        self.assertEqual(social["dao_heart"], 57)
        self.assertEqual(social["vow"], "Protect the weak")

    async def test_06_territory_war_and_caravan_state_are_persistent(self):
        await self.db.sync_world_catalog(self.world.data)
        self.assertTrue(await self.db.claim_territory("Greenriver Town", controller_type="sect", controller_key="Azure Reed Sect", game_minute=100))
        war_id = await self.db.start_territory_war(
            attacker_key="Crimson Saber Hall", defender_key="Azure Reed Sect",
            territory_key="Greenriver Town", game_minute=120,
        )
        self.assertGreater(war_id, 0)
        self.assertEqual((await self.db.get_territory_wars())[0]["territory_key"], "Greenriver Town")
        cid = await self.db.create_caravan(
            owner_type="player", owner_key="701", origin="Greenriver Town", destination="Moonfen Marsh",
            cargo={"beast_core": 1, "_payout": 44, "_currency": "low_spirit_stone"}, risk=20,
            depart_game_minute=150, arrive_game_minute=200,
        )
        self.assertGreater(cid, 0)
        settled = await self.db.settle_player_caravans(701, 200)
        self.assertEqual(settled[0]["payout"], 44)
        self.assertEqual((await self.db.get_wallet(701))["low_spirit_stone"], 69)

    async def test_07_party_membership_and_leadership_transfer(self):
        party_id = await self.db.create_party(701, "Three Rivers")
        self.assertTrue(await self.db.join_party(702, party_id))
        self.assertTrue(await self.db.join_party(703, party_id))
        party = await self.db.get_party(702)
        self.assertEqual(len(party["members"]), 3)
        self.assertTrue(await self.db.leave_party(701))
        party = await self.db.get_party(702)
        self.assertNotEqual(int(party["leader_user_id"]), 701)
        self.assertEqual(len(party["members"]), 2)

    async def test_08_pvp_requires_consent_and_creates_nonlethal_turn_match(self):
        challenge = await self.db.create_pvp_challenge(701, 702, stakes="honor")
        self.assertIsNone(await self.db.get_pvp_match(701))
        accepted = await self.db.respond_pvp_challenge(challenge, 702, True)
        self.assertEqual(accepted["status"], "accepted")
        match = await self.db.get_pvp_match(701)
        self.assertEqual(match["turn_user_id"], 701)
        match = await self.db.apply_pvp_turn(match["match_id"], 701, damage=5, expected_version=match["version"])
        self.assertEqual(match["player2_hp"], 25)
        self.assertEqual(match["turn_user_id"], 702)
        match = await self.db.apply_pvp_turn(match["match_id"], 702, surrender=True, expected_version=match["version"])
        self.assertEqual(match["status"], "finished")
        self.assertEqual(match["winner_user_id"], 701)
        target = await self.db.get_character(702)
        self.assertEqual(target["life_status"], "alive")
        self.assertEqual(target["vitality"], target["vitality_max"])


if __name__ == "__main__":
    unittest.main()
