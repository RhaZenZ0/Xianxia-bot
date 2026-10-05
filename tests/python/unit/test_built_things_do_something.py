"""Things a player built that did nothing now do something (v1.28.0).

A property's Storage facility stored nothing, a beast's intelligence fought
with nothing, six reputations were read by no rule. The engine holds each rule;
where the bot shows the same number, its twin is held equal to the Go here,
read off the source rather than copied.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

import pytest

from app.database.core import PROPERTY_STORAGE_SLOTS_PER_LEVEL
from app.rules.advanced_runtime import BEAST_INTELLIGENCE_CAP, companion_bonus

pytestmark = pytest.mark.unit

GAME = Path(__file__).resolve().parents[3] / "go_core" / "internal" / "game"


def _go_const(file: str, name: str) -> int:
    text = (GAME / file).read_text(encoding="utf-8")
    match = re.search(rf"\b{name}\s*=\s*int64\((\d+)\)", text)
    assert match, f"{name} is not declared in {file}; the gate is broken, not the tree"
    return int(match.group(1))


class BuiltThingsDoSomething(unittest.TestCase):
    def test_the_storage_facility_adds_what_the_engine_adds(self) -> None:
        self.assertEqual(PROPERTY_STORAGE_SLOTS_PER_LEVEL, _go_const("property_storage_actions.go", "propertyStorageSlotsPerLevel"))

    def test_a_beasts_training_is_capped_where_the_engine_caps_it(self) -> None:
        self.assertEqual(BEAST_INTELLIGENCE_CAP, _go_const("combat_actions.go", "beastIntelligenceCap"))
        self.assertGreater(companion_bonus(0, 0, 0, 100), companion_bonus(0, 0, 0, 0))

    def test_the_beast_card_passes_the_intelligence(self) -> None:
        import ast
        source = (Path(__file__).resolve().parents[3] / "app" / "bot" / "commands" / "beast.py").read_text(encoding="utf-8")
        calls = [n for n in ast.walk(ast.parse(source))
                 if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "companion_bonus"]
        self.assertTrue(calls, "the beast card no longer asks companion_bonus")
        for call in calls:
            self.assertEqual(len(call.args), 4, f"a beast card leaves out what training is worth: {ast.unparse(call)}")
