"""The bot's half of the party and the battle pill (v1.34.0).

The engine owns the rules (`party_and_battle_test.go` drives them): a party in
the middle of a raid takes nobody, the leader may put a member out, a raid
does not wait for ever on a raider who has gone quiet, and a pill drunk in a
battle leaves its toxicity and costs the turn. What Python can honestly hold is
that it asks the engine the right question and says what the engine answered:

- `/party kick` sends `party.kick` with the member's id and nothing else the
  engine would have to take on trust;
- the pill carries the same death scale the attack and the technique carry,
  because its turn can end a life;
- the pill and the Law technique read the opponent's answer through one
  function, so a defeat in either is worded once.
"""

from __future__ import annotations

import ast
import asyncio
import unittest

from app.ops.core_services import CombatService
from tests.support import PROJECT_ROOT

BATTLE = PROJECT_ROOT / "app" / "bot" / "commands" / "battle.py"
TERRITORY = PROJECT_ROOT / "app" / "bot" / "commands" / "territory.py"


class _Engine:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, dict]] = []

    async def authoritative_action(self, operation, user_id, payload, *, action_id):
        self.calls.append((operation, user_id, dict(payload)))
        return {"result": {}}


def _function(path, name: str) -> ast.AST:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    if not found:
        raise AssertionError(f"{name} is not in {path.name}: the reader is broken, not the tree")
    return found[0]


def _calls(node: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(node) if isinstance(n, ast.Call)]


class ThePillCarriesTheDeathScale(unittest.TestCase):
    def test_the_engine_is_told_how_a_life_that_ends_here_is_scaled(self):
        engine = _Engine()
        service = CombatService(None, engine=engine)
        asyncio.run(service.recovery_item(
            7, battle_id=3, item_id="recovery_pill", game_minute=10, action_id="a",
            minutes_per_year=518400, base_samsara_years=320, max_wait_seconds=300))
        operation, _, payload = engine.calls[0]
        self.assertEqual(operation, "combat.recovery_item")
        self.assertEqual(
            (payload["minutes_per_year"], payload["base_samsara_years"], payload["max_wait_seconds"]),
            (518400, 320, 300),
            "the pill's turn can end a life and the engine was not told how to scale it")

    def test_the_panel_hands_them_over(self):
        sends = {kw.arg for call in _calls(_function(BATTLE, "_use_battle_recovery_item"))
                 for kw in call.keywords}
        self.assertTrue({"minutes_per_year", "base_samsara_years", "max_wait_seconds"} <= sends,
                        "the battle panel's pill does not pass the death scale the attack and the technique pass")

    def test_the_pill_and_the_technique_read_the_answer_through_one_function(self):
        for name in ("_use_battle_recovery_item", "_execute_battle_law_technique"):
            names = {c.func.id for c in _calls(_function(BATTLE, name)) if isinstance(c.func, ast.Name)}
            self.assertIn("_answer_lines", names, f"{name} words the opponent's answer on its own")


class PartyKickAsksTheEngine(unittest.TestCase):
    def test_the_command_sends_party_kick_with_the_members_id(self):
        sends = [c for c in _calls(_function(TERRITORY, "party_kick"))
                 if isinstance(c.func, ast.Attribute) and c.func.attr == "authoritative_action"]
        self.assertEqual(len(sends), 1, "/party kick does not make its one engine call")
        args = sends[0].args
        self.assertEqual(args[0].value, "party.kick")
        payload = args[2]
        self.assertIsInstance(payload, ast.Dict)
        self.assertEqual([k.value for k in payload.keys], ["user_id"],
                         "the kick names the member and says nothing the engine would have to take on trust")


if __name__ == "__main__":
    unittest.main()
