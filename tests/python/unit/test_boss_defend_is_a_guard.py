"""`/boss act` → Defend sends the style the engine accepts (v1.7.6).

The button was labelled Defend and sent ``"defend"``, while ``bossActActionGo``
accepts ``attack``, ``technique``, ``guard`` and ``support`` - so every Defend
was refused with "style must be attack, technique, guard, or support". The
engine now also reads ``"defend"`` as a guard for an older bot mid-upgrade
(``TestDefendIsAGuard``); this holds the bot's half: every choice the command
offers is a style the engine's switch names.
"""

from __future__ import annotations

import ast
import re
import unittest

from tests.support import PROJECT_ROOT

BOSS = PROJECT_ROOT / "app" / "bot" / "commands" / "boss.py"
GO = PROJECT_ROOT / "go_core" / "internal" / "game" / "group_combat_actions.go"


def _offered_styles() -> dict[str, str]:
    tree = ast.parse(BOSS.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "boss_act":
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and getattr(decorator.func, "attr", "") == "choices":
                    styles = {}
                    for choice in decorator.keywords[0].value.elts:
                        kwargs = {kw.arg: kw.value.value for kw in choice.keywords}
                        styles[kwargs["name"]] = kwargs["value"]
                    return styles
    return {}


def _accepted_styles() -> set[str]:
    source = GO.read_text(encoding="utf-8")
    body = source[source.index("func bossActActionGo"):]
    guard = re.search(r"if (p\.Style != \"\w+\"(?: && p\.Style != \"\w+\")*) \{", body)
    return set(re.findall(r'"(\w+)"', guard.group(1))) if guard else set()


class DefendIsAGuard(unittest.TestCase):
    def test_the_readers_find_something(self):
        self.assertIn("Attack", _offered_styles(), "the choices could not be read off boss_act; the gate is broken, not the tree")
        self.assertIn("attack", _accepted_styles(), "the engine's style switch could not be read; the gate is broken, not the tree")

    def test_every_offered_style_is_one_the_engine_accepts(self):
        accepted = _accepted_styles()
        refused = {label: value for label, value in _offered_styles().items() if value not in accepted}
        self.assertEqual(refused, {}, "these buttons send a style the engine refuses")

    def test_defend_is_the_guard(self):
        self.assertEqual(_offered_styles().get("Defend"), "guard")


if __name__ == "__main__":
    unittest.main()
