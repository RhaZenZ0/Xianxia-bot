"""Attributes grow every qi stage (v1.14.0): the Python half.

The engine computes growth from the stage and holds the rules
(``attribute_growth_test.go``). What is held here is the presentation's twin -
the sheet must read what the engine would - and migration 73, which rewrote
every stored value to the base the engine now grows from.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unittest

from app.database.core import SCHEMA_MIGRATIONS
from app.rules.attribute_growth import growth_line, growth_rules, path_pair, qi_stages_crossed, sheet_attributes
from tests.support import PROJECT_ROOT

CONTENT = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
GO = PROJECT_ROOT / "go_core" / "internal"
CATALOG_GO = (GO / "worlddata" / "catalog.go").read_text(encoding="utf-8")
GROWTH_GO = (GO / "game" / "attribute_growth.go").read_text(encoding="utf-8")


class TheSheetReadsWhatTheEngineWould(unittest.TestCase):
    def test_the_content_keys_are_the_ones_the_engine_parses(self):
        block = CONTENT.get("attribute_growth") or {}
        self.assertTrue(block, "content/world.json carries no attribute_growth; the reader is broken, not the tree")
        for key in block:
            self.assertIn(f'json:"{key}"', CATALOG_GO, f"attribute_growth.{key} is authored and the engine does not parse it")
        self.assertIn("characterSheetAttributes", GROWTH_GO)

    def test_a_sword_cultivator_at_realm_one(self):
        """The Go test's own numbers: nine stages, +9 to all, +18 to agility
        and will."""
        c = {"path": "Sword Cultivator", "realm_index": 1, "phase": 1,
             "attributes": {"body": 2, "agility": 3, "spirit": 2, "insight": 1, "will": 3, "presence": 1}}
        self.assertEqual(sheet_attributes(CONTENT, c),
                         {"body": 11, "agility": 21, "spirit": 11, "insight": 10, "will": 21, "presence": 10})
        self.assertEqual(qi_stages_crossed(CONTENT, 0, 1), 0)
        self.assertEqual(qi_stages_crossed(CONTENT, 1, 1), 9)

    def test_every_path_grows_the_two_attributes_the_engine_names(self):
        for name in CONTENT["paths"]:
            with self.subTest(path=name):
                self.assertEqual(len(path_pair(CONTENT, name)), 2, f"{name} grows {path_pair(CONTENT, name)}")
        self.assertEqual(path_pair(CONTENT, "Rogue Cultivator"), ())

    def test_the_line_names_the_cap_the_engine_applies(self):
        c = {"path": "Sword Cultivator", "realm_index": 2, "phase": 4, "attributes": {}}
        line = growth_line(CONTENT, c)
        self.assertIn(f"+{growth_rules(CONTENT)['path_edge_cap']}", line)
        self.assertIn("Agility and Will", line)
        self.assertEqual(growth_line(CONTENT, {"path": "Sword Cultivator", "realm_index": 0, "phase": 1}), "",
                         "a cultivator who has crossed no stage has nothing to be told")


class MigrationSeventyThree(unittest.TestCase):
    """Drill: drop the body term and the veteran's body loses its body-ladder
    crossings; drop a path's WHEN and that path keeps its old qi gains."""

    @staticmethod
    def migration():
        for version, name, statements in SCHEMA_MIGRATIONS:
            if version == 73:
                return name, statements
        raise AssertionError("schema 73 is not in SCHEMA_MIGRATIONS")

    def test_every_path_has_its_spread(self):
        _name, statements = self.migration()
        sql = " ".join(statements)
        named = set(re.findall(r"WHEN '([^']+)' THEN", sql))
        self.assertEqual(named, set(CONTENT["paths"]), "a path the migration does not name keeps its old stored gains")

    def test_a_veteran_is_rebuilt_to_the_base(self):
        _name, statements = self.migration()
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE characters(user_id INTEGER PRIMARY KEY, path TEXT, body_realm_index INTEGER, attributes_json TEXT)")
        # A Sword Cultivator at qi realm 10 under the old rule: +1 will and +1
        # agility and will a realm, and three body realms of +1 body.
        db.execute("INSERT INTO characters VALUES(1,'Sword Cultivator',3,?)",
                   (json.dumps({"body": 5, "agility": 13, "spirit": 2, "insight": 1, "will": 23, "presence": 1}),))
        db.execute("INSERT INTO characters VALUES(2,'Somebody Else',0,'{\"will\":7}')")
        for statement in statements:
            db.execute(statement)
        rebuilt = json.loads(db.execute("SELECT attributes_json FROM characters WHERE user_id=1").fetchone()[0])
        spread = CONTENT["paths"]["Sword Cultivator"]
        self.assertEqual(rebuilt["will"], spread["will"])
        self.assertEqual(rebuilt["agility"], spread["agility"])
        self.assertEqual(rebuilt["body"], spread["body"] + 3, "the body ladder's stored crossings are kept")
        self.assertEqual(json.loads(db.execute("SELECT attributes_json FROM characters WHERE user_id=2").fetchone()[0]),
                         {"will": 7}, "a path the migration does not name is left as it is")


if __name__ == "__main__":
    unittest.main()
