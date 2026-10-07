"""Cultivation: attribute growth, aptitudes, practising a method, the qi breakthrough command, the underworld and perfection.

Merged from:

test_attribute_growth.py — Attributes grow every qi stage (v1.14.0): the Python half.

The engine computes growth from the stage and holds the rules
(``attribute_growth_test.go``). What is held here is the presentation's twin -
the sheet must read what the engine would - and migration 73, which rewrote
every stored value to the base the engine now grows from.

test_aptitudes.py — (no docstring)

test_cultivating_practises_the_method.py — Cultivating by a method practises it, and the button says what it is (v1.11.0).

Asked as *"How can we practice a manual"*, and the answer had two faults in it.
The leaf called **Practise** (`/manual practise`) chose which manual a
cultivator gathers by and added no practice at all; and gathering by a manual
never raised its mastery, so weeks of sessions left a method exactly as
mastered as the day it was read. Mastery rose only by studying the manual again
or by fighting with its techniques.

The engine half - a session that gathers adds a point to the manual it was
cultivated by, and a full stage adds none - is held in Go by
`TestCultivatingByAMethodPractisesIt`.

test_the_qi_breakthrough_has_a_slash_command.py — The qi breakthrough is a slash command (v1.19.5).

Asked for as "a command for cultivation breakthrough for qi". `/breakthrough`
has been a registered root since before the hubs, and it was never put in the
command tree, so the only door was `/cultivation → Main Progression →
Breakthrough` - the shape `/learn` had in rc.43 and `/stall` in v1.7.4. The
body's twin has been `/body breakthrough` since v1.9.1.

Held here: the tree registers it, the command Discord registers is the very
root the hub leaf presses (one handler, two doors), and that root is the qi
ladder's, not the body's.

test_the_underworld_and_perfection_can_be_reached.py — Two doors a player could not find (v1.11.1).

*"Are the black market discoverable?"* - the rumours refused anybody the
brokers did not already trust, and Underworld Contacts had one source, a trade
at a post, which needed that trust. So the reputation door was shut for good,
and the Hidden-Weapon family's "discreet underworld contacts" were prose. The
engine half (a broker buys from a stranger; five households send a child out
known) is held in Go by `underworld_contacts_test.go`.

*"And where is the perfect stage"* - the engine lets a Perfection begin at
stage 9 of any realm, and the panel hid the page until Soul Formation.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import os
import re
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.support import PROJECT_ROOT

from app.database.core import SCHEMA_MIGRATIONS
from app.rules.advanced_runtime import manual_practice_line
from app.rules.aptitudes import aptitude_effects, root_compatibility
from app.rules.attribute_growth import growth_line, growth_rules, path_pair, qi_stages_crossed, sheet_attributes
from app.rules.birthfamily import family_connections_line
from app.rules.black_market import BLACK_MARKET_ACCESS_REPUTATION, underworld_trust_line
from app.rules.game import World

pytestmark = pytest.mark.unit

# --- from test_attribute_growth.py ---


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

# --- from test_aptitudes.py ---


ROOT = PROJECT_ROOT


class SequenceRoll:
    def __init__(self, *values: int):
        self.values = list(values)

    def __call__(self, ceiling: int) -> int:
        value = self.values.pop(0) if self.values else 0
        return int(value) % ceiling


class AptitudeRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World(ROOT / "content" / "world.json")

    def test_root_compatibility_uses_path_and_multiple_elements(self):
        sword = root_compatibility(["Metal", "Wind"], "Sword Cultivator", self.world.spiritual_root_system)
        soul = root_compatibility(["Metal", "Wind"], "Soul Cultivator", self.world.spiritual_root_system)
        self.assertGreater(sword, soul)

    def test_awakened_traits_share_the_generic_effect_pipeline(self):
        bundle = {
            "root": {
                "grade": "Earth", "purity": 80, "elements": ["Earth"], "mutation": "",
                "stability": 90, "refinement_progress": 0,
            },
            "bloodline": {
                "bloodline_id": "stone_bear", "name": "Stone Bear Ancestry", "state": "awakened",
                "evolution_stage": 1, "purity": 60, "rejection": 0,
            },
            "physique": {
                "physique_id": "vajra_bone_body", "name": "Vajra Bone Body", "state": "awakened",
                "evolution_stage": 1, "stability": 90, "instability": 0,
            },
        }
        effects = aptitude_effects(
            bundle,
            root_system=self.world.spiritual_root_system,
            bloodline_definitions=self.world.bloodlines,
            physique_definitions=self.world.physiques,
        )
        self.assertEqual({effect["category"] for effect in effects}, {"Spiritual Root", "Bloodline", "Physique"})
        self.assertTrue(any(mod["stat"] == "agility" and mod["value"] < 0 for effect in effects for mod in effect["modifiers"]))

# --- from test_cultivating_practises_the_method.py ---


ROOT_practise = Path(__file__).resolve().parents[3]


LAW = ROOT_practise / "app" / "bot" / "commands" / "law.py"


CULTIVATION = ROOT_practise / "app" / "bot" / "commands" / "cultivation.py"


GO_TRAIN = ROOT_practise / "go_core" / "internal" / "game" / "cultivation_actions.go"


LEVELS = ("Learned", "Practiced", "Proficient", "Mastered", "Perfected")


class TheSessionSaysWhatItPractised(unittest.TestCase):
    def test_a_practised_session_prints_the_new_total(self):
        line = manual_practice_line({"manual_name": "Iron Canon", "gain": 1, "practice": 5, "mastery": 1}, LEVELS)
        self.assertEqual(line, "📖 Practice in **Iron Canon** +1 → 5")

    def test_a_level_reached_is_named(self):
        line = manual_practice_line({"manual_name": "Iron Canon", "gain": 1, "practice": 8, "mastery": 2,
                                     "mastery_rose": True}, LEVELS)
        self.assertTrue(line.endswith("• it reaches **Proficient**"), line)

    def test_nothing_practised_prints_nothing(self):
        self.assertEqual(manual_practice_line(None, LEVELS), "")
        self.assertEqual(manual_practice_line({}, LEVELS), "")

    def test_the_cultivate_reply_reads_it(self):
        tree = ast.parse(CULTIVATION.read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and ast.unparse(n.func) == "manual_practice_line"]
        self.assertTrue(calls, "the /cultivate reply no longer prints the practice a session added")
        self.assertIn("manual_practice", ast.unparse(calls[0].args[0]))

    def test_the_engine_still_reports_it(self):
        go = GO_TRAIN.read_text(encoding="utf-8")
        self.assertIn('payload["manual_practice"] = practised', go)


class TheButtonSaysWhatItDoes(unittest.TestCase):
    def test_the_leaf_is_cultivate_by_and_practise_is_gone(self):
        tree = ast.parse(LAW.read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "registered_group_command":
                if node.args and ast.unparse(node.args[0]) == "manual_group":
                    names |= {k.value.value for k in node.keywords if k.arg == "name" and isinstance(k.value, ast.Constant)}
        self.assertIn("study", names, "the reader found no manual commands; it is broken, not the tree")
        self.assertIn("cultivate_by", names)
        self.assertNotIn("practise", names, "a button called Practise that practises nothing is back")

# --- from test_the_qi_breakthrough_has_a_slash_command.py ---


ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}


def _surface():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.surface")


class TheQiBreakthroughHasASlashCommand(unittest.TestCase):
    def test_the_tree_registers_it(self):
        self.assertIn("breakthrough", _surface().TREE_COMMANDS)

    def test_the_slash_command_is_the_hub_leaf(self):
        surface = _surface()
        from app.bot.hubs import REGISTERED_HUBS, _leaf_actions

        leaves = [action for definition in REGISTERED_HUBS if definition.name == "cultivation"
                  for page in definition.pages for action in _leaf_actions(page)
                  if action.path == "/breakthrough"]
        self.assertEqual(len(leaves), 1, "the cultivation hub no longer carries the qi breakthrough leaf")
        self.assertIs(surface._tree_command("breakthrough"), leaves[0].command,
                      "the slash command and the hub leaf are two commands, free to drift apart")

    def test_it_is_the_qi_ladder(self):
        surface = _surface()
        callback = surface._tree_command("breakthrough").callback
        operations = {node.args[0].value for node in ast.walk(ast.parse(inspect.getsource(callback)))
                      if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "authoritative_action"
                      and node.args and isinstance(node.args[0], ast.Constant)}
        self.assertEqual(operations, {"cultivation.breakthrough"})

# --- from test_the_underworld_and_perfection_can_be_reached.py ---


ROOT_underworld = Path(__file__).resolve().parents[3]


ECONOMY = ROOT_underworld / "app" / "bot" / "commands" / "economy.py"


SURFACE = ROOT_underworld / "app" / "bot" / "surface.py"


GO_TRADE = ROOT_underworld / "go_core" / "internal" / "game" / "economy_actions.go"


WORLD = json.loads((ROOT_underworld / "content" / "world.json").read_text(encoding="utf-8"))


def _function(tree: ast.AST, name: str) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found; the reader is broken, not the tree")


class TheUnderworldCanBeReached(unittest.TestCase):
    def test_the_threshold_is_the_engine_s(self):
        self.assertIn(f"blackMarketTrustReputation = int64({BLACK_MARKET_ACCESS_REPUTATION})",
                      GO_TRADE.read_text(encoding="utf-8"))

    def test_the_rumours_refuse_nobody(self):
        tree = ast.parse(ECONOMY.read_text(encoding="utf-8"))
        rumours = _function(tree, "blackmarket_rumors")
        texts = [n.value for n in ast.walk(rumours) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        self.assertTrue(any("will buy" in t for t in texts), "the rumours no longer tell a stranger the way in")
        self.assertFalse(any("does not trust you yet" in t for t in texts),
                         "the rumours refuse a stranger again, so a post can never be found")

    def test_the_sell_picker_needs_no_trust(self):
        tree = ast.parse(ECONOMY.read_text(encoding="utf-8"))
        sell = _function(tree, "blackmarket_sell_autocomplete")
        self.assertIn("selling=True", ast.unparse(sell))

    def test_a_fence_says_how_far_trust_is(self):
        line = underworld_trust_line({"access": "fencing as a stranger", "underworld_reputation": 4, "trust_reputation": 15})
        self.assertIn("11 more", line)
        trusted = underworld_trust_line({"access": "fencing as a stranger", "underworld_reputation": 15, "trust_reputation": 15})
        self.assertIn("will sell to you", trusted)
        self.assertEqual(underworld_trust_line({"access": "dark karma", "underworld_reputation": 3}), "")

    def test_the_hidden_weapon_family_is_known(self):
        rep = WORLD["birth_family_sendoff"]["hidden_weapon_family"].get("reputation") or {}
        self.assertGreaterEqual(int(rep.get("Underworld Contacts") or 0), BLACK_MARKET_ACCESS_REPUTATION)
        line = family_connections_line({"reputation": {"Underworld Contacts": 15}})
        self.assertIn("Underworld Contacts +15", line)
        self.assertEqual(family_connections_line({}), "")


class PerfectionIsOnThePanelAtStageNine(unittest.TestCase):
    def test_the_page_opens_from_realm_zero(self):
        unlocks = WORLD["feature_unlocks"]
        flat = json.dumps(unlocks)
        self.assertIn('"cultivation / Arts"', flat, "the roster read is broken, not the tree")
        self.assertNotIn('"ascend / Perfection"', flat, "the Perfection page waits for a realm again")
        self.assertNotIn('"perfect start"', flat)

    def test_start_is_drawn_at_stage_nine_and_the_rest_while_a_path_runs(self):
        tree = ast.parse(SURFACE.read_text(encoding="utf-8"))
        body = ast.unparse(_function(tree, "_progression_hidden_actions"))
        self.assertIn("body_phase != 9", body, "Start ignores the body path's stage 9 again")
        self.assertIn("shut['perfection_path']", body)
        gates = next(n for n in ast.walk(tree) if isinstance(n, ast.AnnAssign)
                     and ast.unparse(n.target) == "PROGRESSION_GATES")
        self.assertIn("'perfect trial'", ast.unparse(gates.value))


if __name__ == "__main__":
    unittest.main()
