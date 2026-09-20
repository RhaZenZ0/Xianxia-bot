"""Which Law techniques need a target is content's to say, not Python's
(v1.0.0-rc.58).

`law.py` carried `{'spatial_lockdown','spatial_strangulation'}` - a set of ids
restating something `special_effects.<id>.category` already says. That would
have been harmless drift if it were only a panel hint. It was not: the engine
special-cased `world_collapse` and nothing else, so this set was the *only*
thing in the tree stopping a cultivator applying a control technique to
themselves out of a battle. `lawTechniqueAction` writes the effect row on the
user, and `spatial_lockdown` carries `agility -3` and `escape_bonus -5`, so the
one guard on a self-debuff lived in the client - rc.48's rule in a third place:
a bound that lives in the client is not a bound.

It was latent only because `escape_bonus` was dead. Wiring it is what armed it,
which is why the refusal had to ship in the same release as the wiring.

This is rc.44's `RESTATES_THE_MAPPING` shape: a production file naming the ids
is restating a mapping content owns, and the set is empty.
"""
from __future__ import annotations

import ast
import json
import unittest

from app.rules.game import CONTROL_EFFECT_CATEGORY, World
from tests.support import PROJECT_ROOT

WORLD_DATA = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
WORLD = World(PROJECT_ROOT / "content" / "world.json")
LAW_PY = PROJECT_ROOT / "app" / "bot" / "commands" / "law.py"
LAW_GO = PROJECT_ROOT / "go_core" / "internal" / "game" / "law_technique_actions.go"

#: A production file allowed to name a control technique's id outright, with
#: the reason. Empty, and empty the day it was written.
RESTATES_THE_SET: dict[str, str] = {}


def _technique_ids() -> set[str]:
    return set(WORLD_DATA["law_system"]["techniques"])


class TheControlSetIsReadOffTheContent(unittest.TestCase):
    def test_the_question_answers_exactly_the_control_techniques(self):
        control = {key for key in _technique_ids() if WORLD.law_technique_targets_another(key)}
        self.assertEqual(control, {"spatial_lockdown", "spatial_strangulation"},
                         "the shipped content's control set changed; the engine reads the same field")

    def test_every_other_technique_is_not_one(self):
        for key in sorted(_technique_ids() - {"spatial_lockdown", "spatial_strangulation"}):
            with self.subTest(technique=key):
                self.assertFalse(WORLD.law_technique_targets_another(key))

    def test_no_production_file_keeps_its_own_set_of_ids(self):
        """Read as *literals in a collection*, not as a substring: `law.py`
        still mentions `world_collapse` by name for the personal-world check,
        which is a different rule and not a restatement of this one."""
        offenders = []
        for path in sorted((PROJECT_ROOT / "app").rglob("*.py")):
            relative = str(path.relative_to(PROJECT_ROOT))
            if relative in RESTATES_THE_SET:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Set, ast.List, ast.Tuple)):
                    continue
                named = {
                    element.value for element in node.elts
                    if isinstance(element, ast.Constant) and isinstance(element.value, str)
                }
                if len(named & {"spatial_lockdown", "spatial_strangulation"}) >= 2:
                    offenders.append(f"{relative}:{node.lineno}")
        self.assertEqual(offenders, [],
                         "a collection of control-technique ids is a second statement of a content rule")

    def test_the_category_is_the_same_string_on_both_sides(self):
        """Python keeps only what it can honestly check: that the Go half still
        exists and still spells it the same way (the rc.49 lesson - a grep
        cannot see a disabled condition, so the behaviour is Go's to prove in
        `special_effects_content_test.go`)."""
        self.assertEqual(CONTROL_EFFECT_CATEGORY, "Law Control")
        go = LAW_GO.read_text(encoding="utf-8")
        self.assertIn(f'lawControlCategory = "{CONTROL_EFFECT_CATEGORY}"', go,
                      "the engine no longer states the control category, or states a different one")
        self.assertIn("lawControlCategory {", go,
                      "the engine declares the category but no longer refuses on it")

    def test_the_panel_asks_the_world_rather_than_a_literal(self):
        self.assertIn("law_technique_targets_another", LAW_PY.read_text(encoding="utf-8"))

    def test_the_dead_python_copy_of_the_arrays_is_gone(self):
        """`app/rules/inscription.py` held `ARRAY_DEPLOYMENTS`, two of the six
        entries `deployedArrayDefs` carries in Go, with modifier stats of its
        own and not one caller anywhere. A release about numbers nothing reads
        does not get to leave one behind."""
        self.assertFalse((PROJECT_ROOT / "app" / "rules" / "inscription.py").exists())
        for path in sorted((PROJECT_ROOT / "app").rglob("*.py")):
            self.assertNotIn("ARRAY_DEPLOYMENTS", path.read_text(encoding="utf-8"),
                             f"{path.relative_to(PROJECT_ROOT)} resurrected the dead array copy")


if __name__ == "__main__":
    unittest.main()
