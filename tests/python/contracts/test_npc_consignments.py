"""v1.0.0-rc.15: the world's own people put things under the hammer.

Forty-eight auction houses, a steward standing in every one of them, and the
only lot that ever appeared on any floor was one a player walked in and
listed. Merchants were wired to the auctions in the buy direction only, so the
world could consume treasure and never produce any.

Go decides who finds what, where it goes and what it is worth; this side
asserts the Python boundary - a blind lot reads as blind, the board and the GM
can both see a consignment, and the appraisal door exists.
"""
from __future__ import annotations

import ast
import json
import unittest

from tests.support import PROJECT_ROOT

BOT = PROJECT_ROOT / "app" / "bot"
ECONOMY = (BOT / "commands" / "economy.py").read_text(encoding="utf-8")
CORE = (PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")
DASHBOARD = (PROJECT_ROOT / "app" / "dashboard" / "server.py").read_text(encoding="utf-8")
GO = PROJECT_ROOT / "go_core" / "internal"
WORLD = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))


def _body(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef, ast.ClassDef)) and n.name == name)
    return ast.get_source_segment(source, node)


class TheEngineOwnsTheFinding(unittest.TestCase):
    def test_the_batch_is_registered_like_every_other_system(self):
        world = (GO / "simulation" / "world.go").read_text(encoding="utf-8")
        self.assertIn('"npc_consignments": minutesPerDay,', world)
        self.assertIn('"npc_consignments"', world.split("orderedSystems")[1][:400])
        self.assertIn('case "npc_consignments":', world)
        finds = (GO / "simulation" / "npc_finds.go").read_text(encoding="utf-8")
        for needle in ("func npcFindChance(", "func knowsWhatTheyFound(", "func auctionReserve(",
                       "func (r *Runner) npcConsignments(", "func (r *Runner) smuggleToNightMarket(",
                       "func (r *Runner) recordFind("):
            self.assertIn(needle, finds, needle)

    def test_the_python_registries_all_know_it(self):
        from app.simulation.world import SIMULATION_SYSTEMS

        self.assertIn("npc_consignments", SIMULATION_SYSTEMS)
        self.assertIn('"npc_consignments": True,', CORE)
        self.assertIn('"npc_consignments": True,', DASHBOARD)
        inspect = (BOT / "admin" / "inspect_sim.py").read_text(encoding="utf-8")
        self.assertEqual(inspect.count('value="npc_consignments"'), 2, "automation toggle and force-run choice")

    def test_a_consignment_is_paid_into_a_purse_not_a_wallet(self):
        # `seller_user_id` is foreign-keyed to `characters`, so a finder's is
        # 0; paying user 0 would write a wallet for somebody who is not there.
        maintenance = (GO / "simulation" / "advanced_maintenance.go").read_text(encoding="utf-8")
        self.assertIn("func payAuctionSeller(", maintenance)
        self.assertIn("UPDATE npc_civilization_state SET wealth=MIN(9999,wealth+?)", maintenance)
        self.assertIn("if seller := i64(a[\"seller_user_id\"]); seller > 0 {", maintenance)

    def test_python_writes_no_lot_of_its_own(self):
        for source in (ECONOMY, CORE):
            self.assertNotIn("INSERT INTO auctions", source)


class ABlindLotReadsAsBlind(unittest.TestCase):
    def test_the_board_hides_what_the_consignor_could_not_read(self):
        line = _body(ECONOMY, "lot_identity")
        self.assertIn('lot.get("appraised")', line)
        self.assertIn('lot.get("grade_band")', line)
        self.assertIn("Unidentified Lot", line)
        # ...but not from someone who has read one before.
        self.assertIn("item_id in known", line)
        browse = _body(ECONOMY, "auction_browse")
        self.assertIn("DB.get_appraised_items(interaction.user.id)", browse)
        self.assertIn("lot_identity(lot,known)", browse)

    def test_the_known_set_comes_from_the_appraisal_table(self):
        self.assertIn("async def get_appraised_items", CORE)
        self.assertIn("FROM character_item_appraisals WHERE user_id=?", CORE)

    def test_the_gm_can_still_see_an_npc_lot(self):
        # An inner join on the seller would have hidden every consignment.
        self.assertIn("FROM auctions a LEFT JOIN characters seller", DASHBOARD)
        self.assertIn("COALESCE(seller.name,NULLIF(a.seller_npc_name,''))", DASHBOARD)


class TheAppraisalDoor(unittest.TestCase):
    def test_the_leaf_is_on_the_auction_hub_and_calls_the_engine(self):
        self.assertIn('@registered_group_command(auction_group, name="appraise"', ECONOMY)
        body = _body(ECONOMY, "auction_appraise")
        self.assertIn('ENGINE.authoritative_action("appraisal.read"', body)
        self.assertIn("@serialized_user_action", ECONOMY.split('name="appraise"')[1][:400])

    def test_the_engine_owns_the_reading(self):
        appraisal = (GO / "game" / "appraisal_actions.go").read_text(encoding="utf-8")
        for needle in ("func appraisalAction(", "func appraisalTN(", "func appraisalFee(",
                       'canonicalAttribute(conn, catalog, userID, gameMinute, "insight")',
                       'advanceProfessionTx(conn, userID, appraisalProfession'):
            self.assertIn(needle, appraisal, needle)
        self.assertIn('"appraisal.read":                  true,', (GO / "game" / "authoritative.go").read_text(encoding="utf-8"))
        self.assertIn('case "appraisal.read":', (GO / "game" / "late_migration_registry.go").read_text(encoding="utf-8"))

    def test_appraisal_is_no_longer_a_dead_profession(self):
        from app.rules.progression_systems import PROFESSIONS

        self.assertIn("Appraisal", PROFESSIONS)
        appraisal = (GO / "game" / "appraisal_actions.go").read_text(encoding="utf-8")
        self.assertIn('const appraisalProfession = "Appraisal"', appraisal)


class TheTreasuresArePricedLikeTreasures(unittest.TestCase):
    def test_nothing_auction_grade_is_priced_like_a_recovery_pill(self):
        # Nine of the nineteen sat at sect_value 8 with no base_price - the
        # same as a low beast core - and every valuation in the game derives
        # from those two numbers.
        ordinary = int(WORLD["items"]["recovery_pill"]["sect_value"])
        for key, item in WORLD["items"].items():
            if not item.get("auction_interest"):
                continue
            with self.subTest(item=key):
                self.assertGreater(int(item["sect_value"]), ordinary * 8, f"{key} is priced like ordinary stock")
                self.assertGreater(int(item.get("base_price") or 0), 0, f"{key} has no price of its own")

    def test_a_legendary_outprices_a_special(self):
        grades = {"special": [], "legendary": []}
        for item in WORLD["items"].values():
            interest = str(item.get("auction_interest") or "")
            if interest in grades:
                grades[interest].append(int(item["sect_value"]))
        self.assertTrue(grades["special"] and grades["legendary"])
        self.assertGreater(min(grades["legendary"]), max(grades["special"]) // 4)


if __name__ == "__main__":
    unittest.main()
