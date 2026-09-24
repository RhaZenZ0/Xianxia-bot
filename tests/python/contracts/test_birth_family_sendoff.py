"""v1.0.0-rc.15: what the household puts in your hands on the way out.

Thirteen birth families with a hand-tuned Wealth from 26 to 82, and every one
of them handed a new cultivator the same two spirit herbs and one spirit iron
- so what a family was worth bought their child nothing at the door. Each
sends its own flying artifact now, no two the same. Go decides who gets what
and enforces once-per-household; this side asserts the Python boundary.
"""
from __future__ import annotations

import ast
import json
import unittest

from tests.support import PROJECT_ROOT

BOT = PROJECT_ROOT / "app" / "bot"
FAMILY = (BOT / "commands" / "family.py").read_text(encoding="utf-8")
CREATION = (BOT / "ui" / "creation.py").read_text(encoding="utf-8")
GO = PROJECT_ROOT / "go_core" / "internal"
WORLD = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))


def _body(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef, ast.ClassDef)) and n.name == name)
    return ast.get_source_segment(source, node)


class TheEngineHandsItOver(unittest.TestCase):
    def test_the_grant_is_one_helper_used_by_all_three_doors(self):
        birth = (GO / "game" / "birth_family_actions.go").read_text(encoding="utf-8")
        self.assertIn("func grantBirthFamilySendoffTx(", birth)
        # Guarded on item_provenance, keyed on the family rather than the user
        # - which is what lets a samsara rebirth into a new household earn a
        # new heirloom while asking the same household twice earns nothing.
        self.assertIn("source_type='birth_family_sendoff' AND source_key=?", birth)
        for path in ("authoritative.go", "lifecycle_actions.go", "family_dao_actions.go"):
            source = (GO / "game" / path).read_text(encoding="utf-8")
            self.assertIn("grantBirthFamilySendoffTx(conn, catalog", source, path)
            self.assertIn("family_sendoff", source, path)

    def test_the_roster_is_content_not_code(self):
        catalog = (GO / "worlddata" / "catalog.go").read_text(encoding="utf-8")
        self.assertIn("type BirthFamilySendoff struct {", catalog)
        self.assertIn('`json:"birth_family_sendoff"`', catalog)
        # Nothing in Python decides who gets what.
        self.assertNotIn("birth_family_sendoff", FAMILY)

    def test_nothing_python_side_writes_the_inventory(self):
        for source in (FAMILY, CREATION):
            self.assertNotIn("INSERT INTO inventory", source)


class TheReplySaysWhatTheyWereGiven(unittest.TestCase):
    def test_asking_the_family_names_the_heirloom(self):
        line = _body(FAMILY, "family_sendoff_line")
        self.assertIn('result.get("family_sendoff")', line)
        self.assertIn("The household sends you out with", line)
        self.assertIn("family_sendoff_line(result)", _body(FAMILY, "birth_family_support"))
        # The support package's goods were computed by the engine and never
        # rendered; they are now.
        self.assertIn('result.get("items")', _body(FAMILY, "birth_family_support"))

    def test_the_creation_card_shows_it(self):
        self.assertIn('creation.get("family_sendoff")', CREATION)
        self.assertIn("Sent Out With", CREATION)


class TheGiftFitsTheHousehold(unittest.TestCase):
    def test_a_richer_house_sends_a_better_artifact_and_none_repeat(self):
        from app.rules.birthfamily import FAMILY_ARCHETYPES, UPPER_SAMSARA_FAMILIES

        wealth_of = {str(a["id"]): int(a["wealth"]) for a in FAMILY_ARCHETYPES}
        # The upper-world houses are keyed by archetype (v1.3.0).
        for houses in UPPER_SAMSARA_FAMILIES.values():
            wealth_of.update({str(h["archetype"]): int(h["wealth"]) for h in houses})
        sendoff = WORLD["birth_family_sendoff"]
        self.assertEqual(set(sendoff), set(wealth_of), "a household with no send-off")
        items = [str(e["item"]) for e in sendoff.values()]
        self.assertEqual(len(set(items)), len(items), "two households hand out the same object")
        for archetype, entry in sendoff.items():
            item = WORLD["items"][str(entry["item"])]
            with self.subTest(archetype=archetype):
                self.assertEqual(int(item["flight"]), 5 if wealth_of[archetype] >= 60 else 3)

    def test_an_heirloom_sword_never_outclasses_the_shop_ladder(self):
        # The gift is the flight, not the edge: a send-off blade must stay
        # under what a cultivator can buy for themselves.
        from app.rules.advanced_runtime import EQUIPMENT_DEFINITIONS

        inherited = {str(e["item"]) for e in WORLD["birth_family_sendoff"].values()}
        shop_floor = int(EQUIPMENT_DEFINITIONS["spirit_iron_sword"]["attack"])
        ceiling = int(EQUIPMENT_DEFINITIONS["spirit_crystal_sword"]["attack"])
        swords = [k for k in inherited if k in EQUIPMENT_DEFINITIONS]
        self.assertGreaterEqual(len(swords), 3, "the sword households send swords")
        for key in swords:
            with self.subTest(item=key):
                attack = int(EQUIPMENT_DEFINITIONS[key]["attack"])
                self.assertGreater(attack, 0, "a sword that cannot cut")
                self.assertLess(attack, ceiling, f"{key} outclasses a bought spirit-crystal sword")
                if key != "qin_ancestral_sword":
                    self.assertLess(attack, shop_floor, f"{key} outclasses the starter shop blade")


if __name__ == "__main__":
    unittest.main()
