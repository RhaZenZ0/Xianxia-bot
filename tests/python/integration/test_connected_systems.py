import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT
install_aiosqlite_shim()

from app.database import Database, SCHEMA_VERSION
from app.fate import fate_label
from app.realm_hubs import REALM_HUBS, realm_hub_by_location
from app.game import World


ATTRS = {"body": 4, "agility": 3, "spirit": 5, "insight": 4, "will": 5, "presence": 2}
ROOT = PROJECT_ROOT


class ConnectedSystemsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "connected.sqlite3"
        self.db = Database(self.path)
        await self.db.init()
        for uid, name in ((101, "Junior"), (202, "Senior")):
            ok = await self.db.create_character(
                user_id=uid, discord_name=name.lower(), name=name, origin="Greenriver Town",
                path="Formation Adept", spiritual_root="Wind", concept="connected systems test",
                location="Greenriver Town", attributes=ATTRS, qi_max=30, vitality_max=30,
            )
            self.assertTrue(ok)
        async with self.db._connect() as conn:
            await conn.execute("UPDATE characters SET realm_index=1,phase=2 WHERE user_id=202")
            await conn.commit()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_schema_v7_contains_connected_system_tables_and_partner_echo_columns(self):
        self.assertEqual(SCHEMA_VERSION, 17)
        with sqlite3.connect(self.path) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for name in {
                "character_fate", "fate_ledger", "disciple_requests", "deployed_location_arrays",
                "black_market_posts", "black_market_stock", "realm_hub_channels", "dao_partnerships",
            }:
                self.assertIn(name, tables)
            reinc_cols = {row[1] for row in conn.execute("PRAGMA table_info(reincarnation_state)")}
            self.assertTrue({"partner_echo", "partner_name"}.issubset(reinc_cols))

    async def test_fate_is_capped_logged_and_spendable(self):
        balance = await self.db.adjust_fate(101, 20, reason="test fortune", game_minute=100)
        self.assertEqual(balance, 9)
        self.assertIn("Fate", fate_label(balance))
        balance = await self.db.spend_fate(101, 1, reason="test rescue", game_minute=110)
        self.assertEqual(balance, 8)
        ledger = await self.db.get_fate_ledger(101)
        self.assertEqual([ledger[0]["delta"], ledger[1]["delta"]], [-1, 9])

    async def test_player_disciple_contract_and_breakthrough_reward_connect(self):
        request = await self.db.create_disciple_request(101, 202)
        accepted = await self.db.resolve_disciple_request(202, request["request_id"], accept=True)
        self.assertEqual(accepted["status"], "accepted")
        master = await self.db.get_master(101)
        self.assertEqual(master["user_id"], 202)
        before = await self.db.get_character(202)
        reward = await self.db.reward_master_for_disciple_breakthrough(101, realm_changed=True)
        after = await self.db.get_character(202)
        self.assertEqual(reward["insight_xp"], 8)
        self.assertEqual(after["insight_xp"], before["insight_xp"] + 8)

    async def test_dao_partnership_dual_cultivation_builds_resonance_for_samsara(self):
        proposal = await self.db.create_dao_partnership_request(101, 202)
        result = await self.db.resolve_dao_partnership(202, proposal["partnership_id"], accept=True)
        self.assertEqual(result["status"], "active")
        session = await self.db.record_dual_cultivation(101, cultivation_caps={101: 100, 202: 100}, cooldown_seconds=60)
        self.assertEqual(session["resonance"], 4)
        self.assertGreater(session["awarded"][101], 0)
        bond = await self.db.get_dao_partnership(101, active_only=True)
        self.assertEqual(bond["partner_user_id"], 202)
        self.assertEqual(bond["dual_sessions"], 1)

    async def test_location_array_consumes_item_and_is_shared_by_location(self):
        await self.db.add_items(101, {"minor_qi_gathering_array_disk": 1})
        deployed = await self.db.deploy_location_array(
            101, location="Greenriver Town", item_id="minor_qi_gathering_array_disk",
            name="Minor Qi Gathering Array",
            effect={"modifiers": [{"stat": "cultivation_gain", "operation": "mul", "value": 1.10}]},
            starts_game_minute=200, duration_game_minutes=360,
        )
        self.assertEqual(deployed["location"], "Greenriver Town")
        inv = await self.db.get_inventory(101)
        self.assertEqual(inv.get("minor_qi_gathering_array_disk", 0), 0)
        active = await self.db.get_active_location_array("Greenriver Town", 300)
        self.assertEqual(active["owner_user_id"], 101)
        self.assertAlmostEqual(active["effect"]["modifiers"][0]["value"], 1.10)

    async def test_black_market_trade_connects_wallet_inventory_and_provenance(self):
        await self.db.add_currency(101, "low_spirit_stone", 1000)
        await self.db.rotate_black_market(
            world_name="Mortal World", location="Greenriver Town", heat=60,
            opens_game_minute=0, closes_game_minute=5000,
            stock=[{"item_id": "swift_wind_talisman", "currency_id": "low_spirit_stone", "unit_price": 50, "quantity": 3, "legal_status": "restricted"}],
        )
        trade = await self.db.black_market_trade(
            user_id=101, location="Greenriver Town", item_id="swift_wind_talisman", quantity=1, buy=True, game_minute=300,
        )
        self.assertEqual(trade["total"], 50)
        self.assertEqual((await self.db.get_inventory(101))["swift_wind_talisman"], 1)
        provenance = await self.db.get_item_provenance(101, "swift_wind_talisman")
        self.assertEqual(provenance[0]["source_type"], "black_market")
        self.assertEqual(provenance[0]["legal_status"], "restricted")

    async def test_four_realm_hubs_map_discord_channels_to_canonical_locations(self):
        self.assertEqual(set(REALM_HUBS), {"Mortal World", "Spiritual World", "Immortal World", "Celestial World"})
        for index, (world, hub) in enumerate(REALM_HUBS.items(), 1):
            matched = realm_hub_by_location(hub["location"])
            self.assertEqual(matched[0], world)
            await self.db.set_realm_hub_channel(
                guild_id=999, world_name=world, location=hub["location"], channel_id=1000 + index, category_id=777,
            )
        rows = await self.db.get_realm_hub_channels(999)
        self.assertEqual(len(rows), 4)
        mapped = await self.db.get_realm_hub_by_channel(999, 1001)
        self.assertEqual(mapped["location"], REALM_HUBS["Mortal World"]["location"])

    def test_content_catalog_connects_realm_hubs_and_formation_inscription(self):
        world = World(ROOT / "content" / "world.json")
        for hub in REALM_HUBS.values():
            loc = world.locations[hub["location"]]
            self.assertTrue(loc.get("realm_hub"))
            self.assertGreaterEqual(len(loc.get("encounters", [])), 5)
        formation_recipes = [r for r in world.recipes.values() if r.get("profession") == "Formation"]
        self.assertGreaterEqual(len(formation_recipes), 5)
        self.assertIn("minor_qi_gathering_array_disk", world.items)
        self.assertIn("swift_wind_talisman", world.items)


if __name__ == "__main__":
    unittest.main()
