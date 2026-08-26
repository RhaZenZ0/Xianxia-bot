import tempfile
import unittest
from pathlib import Path

from tests.support import PROJECT_ROOT, install_aiosqlite_shim

install_aiosqlite_shim()

from app.birthfamily import FAMILY_ARCHETYPES, generate_family_options, generate_samsara_family
from app.database import Database
from app.game import World
from app.simulation import MINUTES_PER_DAY, WorldSimulator
from app.seclusion import seclusion_daily_gain

ROOT = PROJECT_ROOT


class WorldSimulationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "worldsim.sqlite3")
        await self.db.init()
        self.world = World(ROOT / "content" / "world.json")
        self.sim = WorldSimulator(self.db, self.world.data)
        await self.sim.initialize(0)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def _create_player(self, user_id=1, profile=None):
        profile = profile or generate_family_options()[0]
        ok = await self.db.create_character(
            user_id=user_id,
            discord_name=f"tester-{user_id}",
            name=f"Tester {user_id}",
            origin="Greenriver Town",
            path="Sword Cultivator",
            spiritual_root="Fire",
            concept="integration test",
            location=self.world.starting_location,
            attributes={"body": 2, "agility": 2, "spirit": 2, "insight": 2, "will": 2, "presence": 2},
            qi_max=10,
            vitality_max=12,
            created_game_minute=0,
            birth_family_profile=profile,
        )
        self.assertTrue(ok)
        return profile

    async def test_initialization_builds_all_simulation_catalogs(self):
        snap = await self.db.admin_world_snapshot()
        self.assertEqual(snap["civilization_regions"], len(self.world.locations))
        self.assertEqual(snap["simulated_npcs"], len(self.world.npcs))
        self.assertEqual(snap["sect_factions"], len(self.world.data.get("sects", {})) * 3)
        tradeable_items = sum(1 for iid in self.world.items if self.sim.market_allows_item(iid))
        self.assertEqual(snap["market_entries"], len(self.world.locations) * tradeable_items)

    async def test_named_npcs_initialize_with_persistent_mind_state(self):
        state = await self.sim.npc_status("Elder Su Yan")
        self.assertIsNotNone(state)
        self.assertEqual(state["current_goal"], self.world.npcs["Elder Su Yan"]["want"])
        self.assertTrue(state["mood"])
        self.assertIn("activity", state)

    async def test_ordinary_market_excludes_secret_keys_and_auction_only_items(self):
        self.assertIsNone(await self.sim.market_quote("Greenriver Town", "verdant_grotto_key"))
        self.assertIsNone(await self.sim.market_quote("Greenriver Town", "hundred_year_peach"))
        self.assertIsNone(await self.sim.market_quote("Greenriver Town", "verdant_furnace_jade_slip"))
        self.assertIsNotNone(await self.sim.market_quote("Greenriver Town", "spirit_herb"))

    async def test_dynamic_market_trade_changes_wallet_inventory_and_supply(self):
        await self._create_player()
        quote = await self.sim.market_quote("Greenriver Town", "spirit_herb")
        self.assertIsNotNone(quote)
        before_supply = int(quote["supply"])
        before_inv = int((await self.db.get_inventory(1)).get("spirit_herb", 0))
        result = await self.sim.market_trade(
            user_id=1,
            location="Greenriver Town",
            item_id="spirit_herb",
            quantity=1,
            buy=True,
        )
        self.assertGreater(result["total"], 0)
        inv = await self.db.get_inventory(1)
        self.assertEqual(inv.get("spirit_herb"), before_inv + 1)
        after = await self.sim.market_quote("Greenriver Town", "spirit_herb")
        self.assertEqual(int(after["supply"]), before_supply - 1)

    async def test_martial_clan_records_exist_immediately_when_ensured(self):
        await self._create_player(profile=generate_family_options()[9])
        await self.sim.ensure_all_clans(0)
        family = await self.db.get_birth_family(1)
        clan = await self.sim.clan_status(int(family["family_id"]))
        self.assertGreaterEqual(len(clan["branches"]), 1)
        self.assertGreaterEqual(len(clan["retainers"]), 1)
        self.assertGreaterEqual(len(clan["relations"]), 1)


    async def test_reincarnation_resets_old_social_and_money_state_and_seeds_target_world_currency(self):
        await self._create_player(profile=generate_family_options()[9])
        await self.db.set_sect_membership(1, sect_name="Azure Cloud Sect", rank_name="Outer Disciple", rank_level=1)
        await self.db.learn_manual(1, "blood_sea_scripture")
        from app.progression_systems import condition_definition, condition_effect
        d=condition_definition("flesh_wound")
        await self.db.apply_condition(1,condition_key="flesh_wound",category=d["category"],name=d["name"],severity=1,source_type="test",source_id="old-life",effect=condition_effect("flesh_wound",1),game_minute=900)
        await self.db.add_tribulation_preparation(1,7,amount=2,game_minute=900)
        await self.db.record_profession_practice(1,"Alchemy",success=True,xp_gain=20,quality_points=1)
        await self.db.adjust_reputation(1,"Orthodox Society",-10,reason="old life")
        crime=await self.db.record_crime(1,jurisdiction="Greenriver Town",crime_type="forbidden_cultivation",severity=4,evidence=80,description="old life crime",game_minute=900,witness_type="public",witness_key="test")
        await self.db.add_grudge(1,holder_type="victim_lineage",holder_key="Old Enemy",intensity=3,reason="old life",game_minute=900)
        # Advanced incarnation-scoped state must die with the old body as well.
        await self.db.add_items(1, {"spirit_iron_sword": 1})
        equipment = await self.db.bind_equipment(1, "spirit_iron_sword")
        await self.db.equip_item(1, equipment["equipment_id"])
        party_id = await self.db.create_party(1, "Old Life Party")
        formation_id = await self.db.create_formation(party_id, "Old Life Array")
        await self.db.assign_formation_position(party_id, formation_id, 1, "vanguard")
        encounter = await self.db.start_boss_encounter(party_id, "iron_tusk_boar_king", game_minute=900)
        pursuits = await self.db.spawn_bounty_hunter_pursuits(900)
        self.assertTrue(pursuits)
        state = await self.db.record_true_death(
            1, current_game_minute=1000, reason="test", minutes_per_year=518400,
            base_samsara_years=1, max_wait_seconds=30,
        )
        self.assertIsNotNone(state)
        async with self.db._connect() as con:
            await con.execute("UPDATE reincarnation_state SET reincarnation_ready_at=1,target_world='Immortal World' WHERE user_id=1")
            await con.commit()
        profile = generate_samsara_family("Immortal World", 0)
        attrs, qi_max, vitality_max = self.world.starting_stats("Sword Cultivator")
        result = await self.db.reincarnate_character(
            user_id=1, name="New Life", gender="neutral", path="Sword Cultivator", spiritual_root="Fire",
            attributes=attrs, qi_max=qi_max, vitality_max=vitality_max, current_game_minute=1000,
            starting_age=12, natural_lifespan_years=75, new_family_profile=profile,
        )
        self.assertEqual(result["target_world"], "Immortal World")
        wallet = await self.db.get_wallet(1)
        self.assertEqual(wallet, {"low_immortal_stone": 25})
        character = await self.db.get_character(1)
        self.assertEqual(character["spirit_stones"], 0)
        self.assertIsNone(await self.db.get_sect_membership(1))
        storage = await self.db.get_storage(1)
        self.assertIsNotNone(storage)
        self.assertEqual(storage["container_id"], "common_spatial_pouch")
        self.assertEqual(await self.db.get_manuals(1), [])
        self.assertEqual(await self.db.get_conditions(1), [])
        self.assertIsNone(await self.db.get_tribulation_state(1,7))
        self.assertEqual(await self.db.get_profession_progress(1), [])
        self.assertEqual(await self.db.get_reputations(1), [])
        self.assertEqual(await self.db.get_crimes(1), [])
        self.assertEqual(await self.db.get_bounties(1), [])
        self.assertEqual(await self.db.get_grudges(1), [])
        self.assertEqual(await self.db.get_equipment(1), [])
        self.assertIsNone(await self.db.get_bounty_hunter_pursuit(user_id=1, active_only=False))
        async with self.db._connect() as con:
            for table in ("boss_participants", "boss_reward_claims", "formation_positions"):
                cur = await con.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id=?", (1,))
                self.assertEqual((await cur.fetchone())[0], 0, table)

    def test_eleven_distinct_xianxia_family_archetypes_exist(self):
        ids = [str(item["id"]) for item in FAMILY_ARCHETYPES]
        self.assertEqual(len(ids), 11)
        self.assertEqual(len(set(ids)), 11)
        self.assertIn("alchemy_family", ids)
        self.assertTrue(all(item.get("category") == "martial" for item in FAMILY_ARCHETYPES))

    async def test_alchemy_family_support_provides_medicinal_supplies(self):
        profile = next(item for item in generate_family_options() if item["id"] == "alchemy_family")
        await self._create_player(user_id=33, profile=profile)
        reward = await self.db.claim_birth_family_support(33, 100, 60)
        self.assertGreaterEqual(int(reward["items"].get("spirit_herb", 0)), 3)
        self.assertGreaterEqual(int(reward["items"].get("recovery_pill", 0)), 1)

    async def test_killing_family_head_causes_persistent_clan_setback_and_blood_feud(self):
        await self._create_player(user_id=1, profile=generate_family_options()[0])
        await self._create_player(user_id=2, profile=generate_family_options()[9])
        await self.sim.ensure_all_clans(0)
        attacker = await self.db.get_birth_family(1)
        victim = await self.db.get_birth_family(2)
        before_stability = int(victim["stability"])
        before_influence = int(victim["influence"])
        old_head = str(victim["head_name"])
        impact = await self.sim.apply_player_action(
            user_id=1, action_type="npc_killed", target_name=old_head,
            location=str(victim["location"]), game_minute=100, severity=3,
            payload={"test": True},
        )
        after = await self.db.get_birth_family(2)
        self.assertLess(int(after["stability"]), before_stability)
        self.assertLess(int(after["influence"]), before_influence)
        self.assertNotEqual(str(after["head_name"]), old_head)
        clan = await self.sim.clan_status(int(after["family_id"]))
        feud = [r for r in clan["relations"] if int(r.get("partner_family_id") or 0) == int(attacker["family_id"])]
        self.assertTrue(feud)
        self.assertEqual(feud[0]["relation_type"], "blood_feud")
        self.assertTrue(any("lost its leader" in text for text in impact["impacts"]))

    async def test_family_heads_are_real_local_battle_targets(self):
        await self._create_player(user_id=1, profile=generate_family_options()[3])
        family = await self.db.get_birth_family(1)
        targets = await self.sim.combat_targets(str(family["location"]))
        self.assertTrue(any(t["name"] == family["head_name"] and t["target_type"] == "family_head" for t in targets))

    async def test_seclusion_session_is_persistent_and_background_rate_is_bounded(self):
        await self._create_player(user_id=1, profile=generate_family_options()[0])
        character = await self.db.get_character(1)
        daily = seclusion_daily_gain(character, mode="qi", environment_mult=1.2, soul_cultivation_mult=1.0)
        self.assertGreater(daily, 0)
        self.assertLess(daily, 100)
        state = await self.db.start_seclusion(
            1, mode="qi", current_game_minute=0, duration_game_minutes=2*MINUTES_PER_DAY,
            location="Greenriver Town", environment_mult=1.2,
        )
        self.assertEqual(state["status"], "active")
        self.assertEqual(state["ends_game_minute"], 2*MINUTES_PER_DAY)
        await self.db.advance_seclusion(1, settled_game_minute=MINUTES_PER_DAY, awarded_gain=daily)
        mid = await self.db.get_seclusion(1)
        self.assertEqual(mid["accumulated_gain"], daily)
        await self.db.advance_seclusion(
            1, settled_game_minute=2*MINUTES_PER_DAY, awarded_gain=daily, completed=True, ended_reason="test complete"
        )
        final = await self.db.get_seclusion(1, active_only=False)
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["accumulated_gain"], daily*2)


if __name__ == "__main__":
    unittest.main()
