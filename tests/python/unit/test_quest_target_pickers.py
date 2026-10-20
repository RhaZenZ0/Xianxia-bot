"""The quest editor offers, for every target kind, only what the validator
accepts (v1.35.0).

`renderQuestEditor` bound a list for `location` and `npc` targets and fell back
to the scene-action list for every other kind, so a `realm`, a `flame`, a
`boss`, a `secret_realm`, a `law`, a `law_technique`, a `profession_exam`, a
`recipe` or an `item` target was offered wrong values rather than none. The
lists are `objective_target_choices`, the validator's own lookups turned into
choices, and a picker per kind binds to them; every first choice is driven
through the validator here, so a list that drifted from the lookup it mirrors
fails by name.
"""
from __future__ import annotations

import re
import unittest

from app.rules.game import World
from app.rules.quests import OBJECTIVE_TYPES, objective_target_choices, validate_quest_definition
from tests.support import PROJECT_ROOT

APP_JS = (PROJECT_ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
SERVER = (PROJECT_ROOT / "app" / "dashboard" / "server.py").read_text(encoding="utf-8")
BUDGET = {"max_xp": 500, "max_stones": 2000, "max_items": 20}


class EveryTargetKindHasAPicker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World(PROJECT_ROOT / "content" / "world.json")
        cls.choices = objective_target_choices(cls.world)
        cls.kinds = {spec["target"] for spec in OBJECTIVE_TYPES.values() if spec["target"]}

    def test_the_lists_cover_every_kind_and_nothing_else(self):
        self.assertGreaterEqual(len(self.kinds), 10, "the objective types declare too few target kinds; the reader is broken")
        self.assertEqual(set(self.choices), self.kinds)
        for kind, options in self.choices.items():
            self.assertTrue(options, f"the {kind} picker offers nothing")
            for option in options:
                self.assertTrue(option["value"] and option["label"], f"a {kind} choice is blank: {option}")

    def test_every_first_choice_passes_the_validator(self):
        for objective_type, spec in OBJECTIVE_TYPES.items():
            kind = spec["target"]
            if not kind:
                continue
            value = self.choices[kind][0]["value"]
            draft = {"title": "Picker check", "description": "a draft the picker made",
                     "objectives": [{"type": objective_type, "target": value, "count": 1}],
                     "rewards": {"insight_xp": 1}}
            _definition, errors = validate_quest_definition(draft, self.world, BUDGET)
            unknown = [e for e in errors if "unknown" in e]
            self.assertEqual(unknown, [], f"the {kind} picker's first choice {value!r} is refused by the validator for {objective_type}")

    def test_the_editor_binds_a_list_per_kind_and_the_read_carries_them(self):
        self.assertIn('list="qeTarget-${esc(targetFor(o.type))}"', APP_JS, "the target input no longer binds a per-kind list")
        self.assertIn("Object.entries(v.targets||{})", APP_JS, "the editor no longer draws a datalist per kind from the read")
        self.assertNotIn("'qeScene'", APP_JS, "the scene-action list is offered for a kind it does not fit again")
        self.assertIn('"targets": objective_target_choices(', SERVER, "the dashboard read no longer carries the per-kind lists")
        # The ids a datalist takes: none spells a window property (v1.34.0).
        self.assertTrue(re.search(r'id="qeTarget-\$\{esc\(kind\)\}"', APP_JS))


if __name__ == "__main__":
    unittest.main()
