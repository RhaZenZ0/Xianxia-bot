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
        # NULL (schema 50; it was 0 and refused); paying user 0 would write a
        # wallet for somebody who is not there. The payout lives in one place,
        # `game.PayLotSellerTx`, because the merchant settlement path carried
        # its own copy with no NPC branch and would have re-broken the pass the
        # first time a merchant won an NPC lot. Both callers must go through it.
        merchant = (GO / "game" / "merchant_actions.go").read_text(encoding="utf-8")
        self.assertIn("func PayLotSellerTx(", merchant)
        self.assertIn("UPDATE npc_civilization_state SET wealth=MIN(9999,wealth+?)", merchant)
        self.assertIn("if seller := i64(auction[\"seller_user_id\"]); seller > 0 {", merchant)
        takes = merchant[merchant.index("func merchantTakesLotTx("):]
        takes = takes[:takes.index("\nfunc ")]
        self.assertIn("PayLotSellerTx(conn, auction, price, now)", takes)
        self.assertNotIn("walletDeltaTx(", takes, "the merchant path must not pay a wallet directly")
        maintenance = (GO / "simulation" / "advanced_maintenance.go").read_text(encoding="utf-8")
        pay = maintenance[maintenance.index("func payAuctionSeller("):]
        pay = pay[:pay.index("\nfunc ")]
        self.assertIn("game.PayLotSellerTx(", pay)
        self.assertNotIn("UPDATE npc_civilization_state", pay, "one copy of the payout, not two that drift")

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


class ForgeriesAndTheDeadColumn(unittest.TestCase):
    """v1.0.0-rc.15: `item_provenance.authenticity` stops being a constant.

    Every one of its five writers passed the literal 100 and no rule read it,
    so the column recorded precisely that nothing in the world was ever fake.
    """

    def test_the_broker_rolls_authenticity_instead_of_asserting_it(self):
        economy = (GO / "game" / "economy_actions.go").read_text(encoding="utf-8")
        self.assertIn("authenticity, aerr := rollUnderworldAuthenticity()", economy)
        self.assertNotIn('"underworld broker", fmt.Sprint(stock["legal_status"]), 100,', economy)

    def test_a_keeper_prices_by_authenticity(self):
        shop = (GO / "game" / "shop_actions.go").read_text(encoding="utf-8")
        self.assertIn("itemAuthenticityTx(conn, userID, p.ItemID)", shop)
        self.assertIn("unit = authenticityPrice(unit, authenticity)", shop)

    def test_the_reading_is_what_reveals_it(self):
        appraisal = (GO / "game" / "appraisal_actions.go").read_text(encoding="utf-8")
        self.assertIn("itemAuthenticityTx(conn, userID, itemID)", appraisal)
        self.assertIn('out["forgery"] = authenticity < authenticityForgery', appraisal)
        # ...and the record keeps what was found rather than a hardcoded 100.
        self.assertNotIn('recordAppraisalTx(conn, userID, itemID, "steward", 100,', appraisal)
        self.assertNotIn('recordAppraisalTx(conn, userID, itemID, "insight", 100,', appraisal)


class TheBlackMarketInsertMatchesTheTable(unittest.TestCase):
    """The smuggle path wrote columns `black_market_stock` does not have.

    It passed because the test fixture beside it had invented a schema of its
    own - so the check asserts the fixture against production, which is the
    thing that actually failed here.
    """

    def test_the_smuggle_write_names_the_real_columns(self):
        finds = (GO / "simulation" / "npc_finds.go").read_text(encoding="utf-8")
        self.assertIn("INSERT INTO black_market_stock(world_name,item_id,currency_id,unit_price,quantity,legal_status,updated_at)", finds)
        for gone in ("black_market_stock(world_name,location", "price=excluded.price"):
            self.assertNotIn(gone, finds, gone)

    def test_the_fixture_matches_the_production_schema(self):
        import re

        core = (PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")
        fixture = (GO / "simulation" / "npc_finds_test.go").read_text(encoding="utf-8")

        def columns(sql: str, table: str) -> set[str]:
            body = sql.split(f"{table}(", 1)[1] if f"{table}(" in sql else sql.split(f"{table} (", 1)[1]
            depth, out = 1, []
            for ch in body:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        break
                out.append(ch)
            text = "".join(out)
            names = set()
            for part in text.split(","):
                word = part.strip().split()[0] if part.strip() else ""
                if word and re.fullmatch(r"[a-z_]+", word) and word not in {"primary", "foreign"}:
                    names.add(word)
            return names

        self.assertEqual(
            columns(core, "black_market_stock"),
            columns(fixture, "black_market_stock"),
            "the fixture's black_market_stock differs from production's",
        )
