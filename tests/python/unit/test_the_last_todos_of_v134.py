"""The last five TODO decisions (v1.3.4), on the bot's side.

Two era keys deleted from the vocabulary, a path's skill on the sheet, the
ghost inheritance re-pointed at the Ghost Cultivator with its scripture the
path's high manual, and `preferred_paths` read by the inheritance grant. The
engine halves are held in Go against the shipped catalogue; these hold the
content and what the bot prints.
"""
from __future__ import annotations

import ast
import json
import unittest

from tests.support import PROJECT_ROOT, code_only

APP = PROJECT_ROOT / "app"
WORLD = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))


def _function_source(path, name: str) -> str:
    source = code_only(path.read_text(encoding="utf-8"))
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    return ast.get_source_segment(source, node) or ""


class TheSkillIsOnTheSheet(unittest.TestCase):
    def test_every_path_names_a_skill_and_the_sheet_prints_it(self):
        for name, path in WORLD["paths"].items():
            with self.subTest(path=name):
                self.assertTrue(str(path.get("skill") or "").strip(), "a path with no skill would print a blank line")
        source = _function_source(APP / "bot" / "commands" / "character.py", "sheet")
        self.assertIn('.get("skill")', source, "/sheet does not read the path's skill")
        self.assertIn('name="Path"', source)


class TheGhostInheritanceIsTheGhostCultivators(unittest.TestCase):
    def test_the_legacy_prefers_the_ghost_cultivator_and_its_scripture_is_a_manual(self):
        legacy = WORLD["inheritances"]["stygian_keeper_legacy"]
        self.assertEqual(legacy["preferred_paths"], ["Ghost Cultivator"])
        item = WORLD["items"][legacy["item"]]
        self.assertEqual(item.get("type"), "manual")
        manual = WORLD["technique_system"]["manuals"][item["manual_id"]]
        self.assertEqual(manual["path"], "Ghost Cultivator")
        self.assertEqual(manual["item_id"], legacy["item"])
        self.assertGreaterEqual(len(manual["techniques"]), 3)
        for tid in manual["techniques"]:
            self.assertEqual(WORLD["technique_system"]["techniques"][tid]["manual"], item["manual_id"])
        # The tomb's rooms still favour the soul path beside the ghost path: it
        # was theirs for twelve releases and the roll is the only thing a room
        # reads a path for.
        for room in WORLD["secret_realms"]["stygian_lantern_tomb"]["rooms"]:
            self.assertIn("Ghost Cultivator", room["preferred_paths"])

    def test_the_grant_reads_preferred_paths_and_the_reply_says_which(self):
        go = (PROJECT_ROOT / "go_core" / "internal" / "game" / "secret_realm_actions.go").read_text(encoding="utf-8")
        body = go[go.index("func grantInheritanceTx("):]
        body = body[: body.index("\n}\n")]
        self.assertIn("stringInList(inheritance.PreferredPaths, path)", body, "the grant does not read preferred_paths")
        self.assertIn("manualForItem(catalog, inheritance.Item)", body)
        reply = code_only((APP / "bot" / "commands" / "secretrealm.py").read_text(encoding="utf-8"))
        self.assertIn('inheritance.get("studied")', reply, "the reply does not say the scripture was studied")


class TheEraVocabularyLostTwoKeys(unittest.TestCase):
    def test_no_era_and_no_script_authors_the_deleted_keys(self):
        cycles = WORLD["world_era_cycles"]
        for world, eras in cycles.items():
            for era in eras:
                for key in ("secret_realm_frequency", "market_volatility"):
                    self.assertNotIn(key, era.get("modifiers") or {}, f"{world}/{era.get('name')} authors {key}")
        gate = (PROJECT_ROOT / "go_core" / "internal" / "worlddata" / "era_vocabulary_test.go").read_text(encoding="utf-8")
        allow = gate[gate.index("var unreadEraModifiers = map[string]string{"):]
        allow = allow[: allow.index("}") + 1]
        self.assertEqual(allow, "var unreadEraModifiers = map[string]string{}", "the era allowlist carries an entry again")


if __name__ == "__main__":
    unittest.main()
