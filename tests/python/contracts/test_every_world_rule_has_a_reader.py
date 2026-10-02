"""Every world rule has a reader (v1.19.3).

`content/world.json` -> `world_rules` carried two blocks that read like rules
and that nothing read: `physical_laws` ("higher worlds are stricter", a time
flow of 1/3/9/27 per world) and `social_laws` ("npc ambition", a "world state
driven" conflict multiplier). `/worldrules` printed neither, the engine never
indexed either, and the one test naming them only held that they existed. The
idea `local_time_flow` stated - higher worlds are faster - is a real rule, but
it is `world_qi_density`, read by `worldQiMultiplier` in every session; a
second number on the same axis would have priced it twice.

Both blocks are gone, with their defaults in `app/rules/advanced_catalog.py`.
What is held here is the rule that would have caught them: every top-level key
of `world_rules` is fetched by a reader - a key the engine indexes on
`catalog.WorldRules`, or a string a Python function reading `world_rules`
names - or it is named in `UNREAD_RULES` with its reason.

It is a floor, not a proof: a Python function that touches `world_rules` and
names a string anywhere counts as a reader of that string. That makes it
honest in one direction only - it never calls a read key unread.
"""
from __future__ import annotations

import ast
import copy
import json
import re
import unittest
from pathlib import Path

from tests.support import code_only

ROOT = Path(__file__).resolve().parents[3]
CONTENT = ROOT / "content" / "world.json"

# A key left unread on purpose, each with why. Not empty on the day it was
# written: `faction_attitudes` carries numbers per alignment that no rule reads,
# and whether a rule should is the owner's decision (docs/TODO.md).
UNREAD_RULES: dict[str, str] = {
    "faction_attitudes": "per-alignment reactions read by no rule; deferred to the owner (docs/TODO.md)",
}

GO_INDEX = re.compile(r'WorldRules\[\s*"([A-Za-z0-9_]+)"\s*\]')


def go_readers() -> set[str]:
    keys: set[str] = set()
    for path in (ROOT / "go_core").rglob("*.go"):
        if path.name.endswith("_test.go"):
            continue
        text = "\n".join(line.split("//", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())
        keys.update(GO_INDEX.findall(text))
    return keys


def _names_world_rules(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr == "world_rules":
            return True
        if isinstance(sub, ast.Name) and sub.id == "world_rules":
            return True
    return False


def python_readers() -> set[str]:
    keys: set[str] = set()
    for path in (ROOT / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not _names_world_rules(node):
                continue
            docstrings = {
                id(sub.body[0].value) for sub in ast.walk(node)
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and sub.body
                and isinstance(sub.body[0], ast.Expr) and isinstance(sub.body[0].value, ast.Constant)
            }
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and id(sub) not in docstrings:
                    keys.add(sub.value)
    return keys


def unread_rules(world_rules: dict) -> list[str]:
    read = go_readers() | python_readers()
    return sorted(key for key in world_rules if key not in read and key not in UNREAD_RULES)


def shipped_rules() -> dict:
    return json.loads(CONTENT.read_text(encoding="utf-8"))["world_rules"]


class EveryWorldRuleHasAReader(unittest.TestCase):
    def test_the_readers_are_found_before_they_are_trusted(self):
        self.assertIn("forbidden_arts", go_readers(), "the Go walk found no WorldRules index; the gate is broken, not the tree")
        self.assertIn("npc_principles", python_readers(), "the Python walk found no /worldrules reader; the gate is broken, not the tree")

    def test_every_world_rule_is_read(self):
        self.assertEqual(unread_rules(shipped_rules()), [], "a world rule nothing reads is a rule the world does not have")

    def test_the_gate_names_the_blocks_that_found_it(self):
        planted = copy.deepcopy(shipped_rules())
        planted["physical_laws"] = {"local_time_flow": {"Mortal World": 1, "Celestial World": 27}}
        planted["social_laws"] = {"npc_ambition": True}
        self.assertEqual(unread_rules(planted), ["physical_laws", "social_laws"])

    def test_an_allowlisted_rule_still_exists_and_is_still_unread(self):
        rules = shipped_rules()
        read = go_readers() | python_readers()
        for key, reason in UNREAD_RULES.items():
            self.assertIn(key, rules, f"{key} is allowlisted and the content no longer carries it; drop the entry")
            self.assertNotIn(key, read, f"{key} has a reader now; drop the entry ({reason})")

    def test_the_defaults_do_not_put_them_back(self):
        source = code_only((ROOT / "app" / "rules" / "advanced_catalog.py").read_text(encoding="utf-8"))
        for key in ("physical_laws", "social_laws", "local_time_flow"):
            self.assertFalse(f'"{key}"' in source, f"advanced_catalog.py still defaults {key}")


if __name__ == "__main__":
    unittest.main()
