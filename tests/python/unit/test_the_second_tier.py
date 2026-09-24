"""The review's second tier of claims, each read and held on the Python side (v1.2.3).

Every gate here reads the function it is about by AST with the docstrings and
comments blanked (rc.52), because each fix's own comment names the thing it
forbids.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from tests.support import bot_function_source, code_only

ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "app"


def _function(path: Path, name: str, cls: str | None = None) -> ast.AST:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if cls is not None and isinstance(node, ast.ClassDef) and node.name == cls:
            for inner in node.body:
                if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)) and inner.name == name:
                    return inner
        elif cls is None and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{path.name}: {cls or ''}.{name} not found; the reader is broken, not the tree")


def _calls(node: ast.AST) -> list[str]:
    return [ast.unparse(n.func) for n in ast.walk(node) if isinstance(n, ast.Call)]


class TheSecondTier(unittest.TestCase):
    def test_a_duel_turn_is_the_engines_to_refuse(self):
        # The engine's breach check runs before its turn check (v0.23.1) so the
        # living player can end a duel whose turn holder has died; a Python
        # refusal in front of it undid that.
        body = code_only(bot_function_source("duel_act"))
        self.assertNotIn("turn_user_id", body, "duel_act refuses on whose turn it is before the engine can end a breached duel")

    def test_narrate_it_meets_the_two_gates(self):
        fn = _function(APP / "bot" / "typed_play.py", "callback", cls="_NarrateButton")
        calls = _calls(fn)
        self.assertIn("maintenance.refuse", calls, "Narrate it skips the maintenance gate a picker click meets")
        self.assertIn("seclusion.refuse", calls, "Narrate it skips the seclusion gate a picker click meets")

    def test_a_refused_creation_is_told_in_the_engines_words(self):
        fn = _function(APP / "bot" / "ui" / "creation.py", "on_submit", cls="CharacterModal")
        handlers = {ast.unparse(h.type) for n in ast.walk(fn) if isinstance(n, ast.Try) for h in n.handlers if h.type is not None}
        self.assertIn("GameEngineError", handlers, "character.create's refusal is printed as a wiring failure")

    def test_nothing_tells_sect_discover_the_time(self):
        offenders = []
        for path in APP.rglob("*.py"):
            text = code_only(path.read_text(encoding="utf-8"))
            for m in re.finditer(r'"sect\.discover"', text):
                window = text[m.start(): m.start() + 600]
                if "game_minute" in window.split("})")[0]:
                    offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [], "a sect.discover payload still carries game_minute (rc.48)")
        self.assertTrue(any('"sect.discover"' in p.read_text(encoding="utf-8") for p in APP.rglob("*.py")), "no caller found; the reader is broken, not the tree")

    def test_the_shared_budget_is_asked_before_a_routes_own_slot(self):
        fn = _function(APP / "ai" / "ai_router.py", "generate")
        src = code_only(ast.unparse(fn))
        shared = src.find("self.limiter.try_acquire()")
        route = src.find("self._route_limiter(model).try_acquire()")
        self.assertTrue(shared > 0 and route > 0, "the two limiter calls were not both found; the reader is broken, not the tree")
        self.assertLess(shared, route, "a route's slot is spent before the shared budget can end the walk")

    def test_a_control_body_that_never_arrives_is_answered(self):
        text = code_only((APP / "ops" / "health.py").read_text(encoding="utf-8"))
        self.assertIn("IncompleteReadError", text, "a short control body escapes to the catch-all and the socket closes with no status")


if __name__ == "__main__":
    unittest.main()
