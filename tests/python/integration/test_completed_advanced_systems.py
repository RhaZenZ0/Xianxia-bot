import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from tests.support import install_aiosqlite_shim, PROJECT_ROOT
install_aiosqlite_shim()

from app.advanced_runtime import ERA_CYCLE
from app.database import Database, SCHEMA_VERSION
from app.game import World
from app.operations import AlertDispatcher


class CompletedAdvancedSystemsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "completed.sqlite3")
        await self.db.init()
        attrs = {"body": 12, "agility": 12, "spirit": 12, "insight": 8, "will": 10, "presence": 6}
        for uid, name in ((801, "Azure"), (802, "Crimson"), (803, "Jade")):
            self.assertTrue(await self.db.create_character(
                user_id=uid, discord_name=name.lower(), name=name, origin="Greenriver Town",
                path="Sword Cultivator", spiritual_root="Fire", concept="completed systems test",
                location="Greenriver Town", attributes=attrs, qi_max=100, vitality_max=120,
                created_game_minute=0,
            ))
        async with self.db._connect() as db:
            await db.execute(
                "UPDATE characters SET realm_index=8,phase=9,vitality=120,vitality_max=120 WHERE user_id IN (801,802,803)"
            )
            await db.commit()
        self.world = World(PROJECT_ROOT / "content" / "world.json")
        await self.db.sync_world_catalog(self.world.data)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_01_equipment_loadout_durability_breakage_and_repair(self):
        await self.db.add_items(801, {"spirit_iron_sword": 1, "spirit_iron_armor": 1, "spirit_iron": 20})
        sword = await self.db.bind_equipment(801, "spirit_iron_sword")
        armor = await self.db.bind_equipment(801, "spirit_iron_armor")
        self.assertEqual(sword["slot"], "weapon")
        equipped = await self.db.equip_item(801, sword["equipment_id"])
        armor_equipped = await self.db.equip_item(801, armor["equipment_id"])
        self.assertTrue(equipped["equipped"])
        self.assertTrue(armor_equipped["equipped"])
        bonus = await self.db.equipment_bonus(801)
        self.assertGreaterEqual(bonus["attack"], 4)
        self.assertGreaterEqual(bonus["defense"], 5)
        await self.db.damage_equipment(801, equipped["max_durability"])
        broken = next(x for x in await self.db.get_equipment(801) if x["equipment_id"] == sword["equipment_id"])
        self.assertEqual(broken["durability"], 0)
        self.assertFalse(broken["equipped"])
        repaired = await self.db.repair_equipment(801, sword["equipment_id"])
        self.assertEqual(repaired["durability"], repaired["max_durability"])
        self.assertGreater(repaired["repair_cost"], 0)

    async def test_02_formation_positions_stance_and_cohesion_are_persistent(self):
        party_id = await self.db.create_party(801, "Azure Triangle")
        self.assertTrue(await self.db.join_party(802, party_id))
        self.assertTrue(await self.db.join_party(803, party_id))
        formation_id = await self.db.create_formation(party_id, "Three Talents Array")
        self.assertTrue(await self.db.assign_formation_position(party_id, formation_id, 801, "vanguard"))
        self.assertTrue(await self.db.assign_formation_position(party_id, formation_id, 802, "core"))
        self.assertTrue(await self.db.assign_formation_position(party_id, formation_id, 803, "support"))
        active = await self.db.activate_formation(party_id, formation_id, stance="aggressive")
        self.assertEqual(active["stance"], "aggressive")
        self.assertEqual(len(active["positions"]), 3)
        changed = await self.db.set_formation_stance(party_id, "defensive")
        self.assertEqual(changed["stance"], "defensive")

    async def test_03_multiphase_party_boss_engine_rounds_phase_changes_and_rewards(self):
        party_id = await self.db.create_party(801, "Raiders")
        await self.db.join_party(802, party_id)
        await self.db.join_party(803, party_id)
        formation_id = await self.db.create_formation(party_id, "Raid Array")
        await self.db.assign_formation_position(party_id, formation_id, 801, "vanguard")
        await self.db.assign_formation_position(party_id, formation_id, 802, "core")
        await self.db.assign_formation_position(party_id, formation_id, 803, "support")
        await self.db.activate_formation(party_id, formation_id, stance="aggressive")
        encounter = await self.db.start_boss_encounter(party_id, "iron_tusk_boar_king", game_minute=100)
        self.assertEqual(encounter["phase_index"], 0)
        max_phase = 0
        for _ in range(12):
            encounter = await self.db.get_boss_encounter(encounter_id=encounter["encounter_id"])
            if encounter["status"] != "active":
                break
            for participant in list(encounter["participants"]):
                current = await self.db.get_boss_encounter(encounter_id=encounter["encounter_id"])
                if current["status"] != "active":
                    break
                p = next(x for x in current["participants"] if x["user_id"] == participant["user_id"])
                if p["status"] != "active" or p["acted_round"] >= current["round_index"]:
                    continue
                current = await self.db.boss_action(
                    current["encounter_id"], participant["user_id"], style="technique",
                    expected_version=current["version"],
                )
                self.assertIsNotNone(current)
                max_phase = max(max_phase, int(current["phase_index"]))
        final = await self.db.get_boss_encounter(encounter_id=encounter["encounter_id"])
        self.assertEqual(final["status"], "victory")
        self.assertGreaterEqual(max_phase, 1)
        reward = await self.db.claim_boss_reward(final["encounter_id"], 801)
        self.assertEqual(reward["currency_amount"], 120)
        self.assertGreaterEqual((await self.db.get_inventory(801)).get("beast_core", 0), 2)

    async def test_04_autonomous_bounty_hunter_spawns_escalates_and_accepts_player_response(self):
        crime = await self.db.record_crime(
            801, jurisdiction="Greenriver Town", crime_type="forbidden_art", severity=6, evidence=90,
            description="Public forbidden technique", game_minute=10, witness_type="npc", witness_key="Gate Warden",
        )
        self.assertIsNotNone(crime["bounty_id"])
        created = await self.db.spawn_bounty_hunter_pursuits(10)
        self.assertEqual(len(created), 1)
        pursuit = created[0]
        self.assertEqual(pursuit["status"], "tracking")
        changed = await self.db.advance_bounty_hunters(10 + 4 * 1440)
        self.assertTrue(changed)
        pursuit = await self.db.get_bounty_hunter_pursuit(pursuit_id=pursuit["pursuit_id"], active_only=False)
        self.assertGreater(pursuit["pressure"], 10)
        if pursuit["status"] in {"tracking", "engaged"}:
            for i in range(8):
                pursuit = await self.db.bounty_hunter_action(801, pursuit["pursuit_id"], "evade", game_minute=6000 + i)
                if pursuit["status"] not in {"tracking", "engaged"}:
                    break
            self.assertIn(pursuit["status"], {"evaded", "defeated", "captured"})

        # Surrender/capture serves the warrant and prevents an endless stream of
        # new hunters for a bounty that has already been enforced.
        second = await self.db.record_crime(
            802, jurisdiction="Greenriver Town", crime_type="assault", severity=4, evidence=90,
            description="Second warrant", game_minute=7000, witness_type="npc", witness_key="Constable",
        )
        self.assertIsNotNone(second["bounty_id"])
        spawned = await self.db.spawn_bounty_hunter_pursuits(7000)
        pursuit2 = next(x for x in spawned if x["user_id"] == 802)
        surrendered = await self.db.bounty_hunter_action(802, pursuit2["pursuit_id"], "surrender", game_minute=7001)
        self.assertEqual(surrendered["status"], "surrendered")
        self.assertEqual(await self.db.get_bounties(802), [])
        self.assertEqual((await self.db.get_crimes(802, open_only=False))[0]["status"], "surrendered")

    async def test_05_territory_siege_armies_and_occupation_resolve_control(self):
        await self.db.claim_territory("Greenriver Town", controller_type="sect", controller_key="Azure Reed Sect", game_minute=10)
        war_id = await self.db.start_territory_war(
            attacker_key="Crimson Saber Hall", defender_key="Azure Reed Sect",
            territory_key="Greenriver Town", game_minute=20,
        )
        result = None
        for step in range(6):
            result = await self.db.territory_war_action(
                war_id, 802, side="attacker", tactic="siege", power=120, game_minute=30 + step,
            )
            if result and result["status"] != "active":
                break
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["operations"]["winner_key"], "Crimson Saber Hall")
        occupation_until = int(result["operations"]["occupation_until_game_minute"])
        self.assertGreater(occupation_until, 30)
        territory = (await self.db.get_territories("Greenriver Town"))[0]
        self.assertEqual(territory["controller_key"], "Crimson Saber Hall")
        consolidated = await self.db.advance_territory_occupations(occupation_until + 1)
        self.assertEqual(len(consolidated), 1)
        self.assertEqual(consolidated[0]["operations"]["resolution"], "attacker_annexed")
        self.assertEqual(consolidated[0]["operations"]["occupation_until_game_minute"], 0)
        actions = await self.db.get_territory_war_actions(war_id)
        self.assertGreaterEqual(len(actions), 1)

    async def test_06_caravan_escorts_interception_smuggling_taxes_and_event_history(self):
        smuggled = await self.db.create_caravan(
            owner_type="player", owner_key="801", origin="Greenriver Town", destination="Moonfen Marsh",
            cargo={"beast_core": 1, "_payout": 100, "_currency": "low_spirit_stone"}, risk=95,
            depart_game_minute=0, arrive_game_minute=100, escort_strength=0, concealment=0,
            smuggling=True, tax_rate=10,
        )
        resolved = await self.db.advance_caravans(100, owner_type="player", owner_key="801")
        first = next(x for x in resolved if x["caravan_id"] == smuggled)
        self.assertIn(first["outcome"], {"seized", "intercepted"})
        self.assertEqual(first["toll_paid"], 0)
        events = await self.db.get_caravan_events(smuggled)
        self.assertTrue(any(e["event_type"] in {"seized", "intercepted"} for e in events))

        guarded = await self.db.create_caravan(
            owner_type="player", owner_key="801", origin="Greenriver Town", destination="Moonfen Marsh",
            cargo={"spirit_herb": 1, "_payout": 100, "_currency": "low_spirit_stone"}, risk=60,
            depart_game_minute=0, arrive_game_minute=200, escort_strength=50, concealment=20,
            smuggling=False, tax_rate=10,
        )
        resolved = await self.db.advance_caravans(200, owner_type="player", owner_key="801")
        second = next(x for x in resolved if x["caravan_id"] == guarded)
        self.assertEqual(second["outcome"], "arrived")
        self.assertEqual(second["payout"], 90)
        self.assertEqual(second["toll_paid"], 10)

    async def test_07_world_eras_transition_automatically_and_record_events(self):
        current = await self.db.get_current_era()
        self.assertEqual(current["name"], ERA_CYCLE[0]["name"])
        self.assertEqual(current["modifiers"]["cultivation_gain"], 1.05)
        first_transition = ERA_CYCLE[0]["duration_days"] * 1440
        era = await self.db.advance_world_era(first_transition)
        self.assertEqual(era["name"], ERA_CYCLE[1]["name"])
        events = await self.db.get_world_era_events()
        self.assertTrue(events)
        self.assertEqual(events[0]["event_type"], "transition")
        second_transition = first_transition + ERA_CYCLE[1]["duration_days"] * 1440
        era = await self.db.advance_world_era(second_transition)
        self.assertEqual(era["name"], ERA_CYCLE[2]["name"])
        self.assertEqual(era["modifiers"]["caravan_risk"], 1.15)
        caravan = await self.db.create_caravan(
            owner_type="player", owner_key="801", origin="Greenriver Town", destination="Moonfen Marsh",
            cargo={"spirit_herb": 1, "_payout": 50, "_currency": "low_spirit_stone"}, risk=40,
            depart_game_minute=second_transition, arrive_game_minute=second_transition + 1,
            escort_strength=0, concealment=0, smuggling=False, tax_rate=8,
        )
        resolved = await self.db.advance_caravans(second_transition + 1, owner_type="player", owner_key="801")
        caravan_result = next(x for x in resolved if x["caravan_id"] == caravan)
        self.assertEqual(caravan_result["effective_risk"], 46)

    async def test_08_slow_query_observability_persistence_and_external_webhook_alerting(self):
        obs_db = Database(Path(self.tmp.name) / "observed.sqlite3", slow_query_ms=0)
        await obs_db.init()
        await obs_db.operational_health()
        flushed = await obs_db.flush_slow_query_log()
        self.assertGreater(flushed, 0)
        snapshot = await obs_db.observability_snapshot()
        self.assertGreater(snapshot["recent_slow_queries_1h"], 0)
        alert_id = await obs_db.record_operational_alert(
            "test_alert", severity="warning", message="test", detail={"slow": True}, delivered=False,
        )
        self.assertGreater(alert_id, 0)

        received = []
        async def handler(reader, writer):
            header = await reader.readuntil(b"\r\n\r\n")
            length = 0
            for line in header.decode("latin1").split("\r\n"):
                if line.lower().startswith("content-length:"):
                    length = int(line.split(":", 1)[1].strip())
            body = await reader.readexactly(length) if length else b""
            received.append(json.loads(body.decode("utf-8")))
            writer.write(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain(); writer.close(); await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            dispatcher = AlertDispatcher(f"http://127.0.0.1:{port}/alert", cooldown_seconds=0)
            self.assertTrue(await dispatcher.send("slow_query_pressure", "threshold exceeded", details={"count": 5}))
            await asyncio.sleep(0.02)
        finally:
            server.close(); await server.wait_closed()
        self.assertEqual(received[0]["key"], "slow_query_pressure")


if __name__ == "__main__":
    unittest.main()
