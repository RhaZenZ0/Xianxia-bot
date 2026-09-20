"""A retreat lasts at most two real hours (schema 57, v1.0.0-rc.56).

`duration_game_minutes` was floored at 1 and bounded by nothing. The only
limit in the game was `days: app_commands.Range[int, 1, 365]` on the slash
command - which is presentation, and which no other caller had - so the engine
would seclude anybody for a millennium if asked. That is the same fault the
fourteen caller-supplied cooldowns had: a bound that lives in the client is
not a bound.

The bound is the engine's now, and the behaviour is Go's to prove
(`seclusion_cap_test.go` drives the refusal, the real deadline, a GM's scale
change and a stopped clock). What Python can honestly check is that its own
picker does not disagree with it, and that nothing here still sends the day.
"""
from __future__ import annotations

import ast
import re
import unittest

from tests.support import PROJECT_ROOT

CULTIVATION = PROJECT_ROOT / "app" / "bot" / "commands" / "cultivation.py"
SECLUSION_GO = (PROJECT_ROOT / "go_core" / "internal" / "game" / "seclusion_environment.go").read_text(encoding="utf-8")


def _go_const(name: str) -> int:
    match = re.search(rf"^\t{name}\s*=\s*int64\((\d+)\)", SECLUSION_GO, re.M)
    assert match, f"{name} is not declared in seclusion_environment.go"
    return int(match.group(1))


class ThePickerDoesNotDisagreeWithTheEngine(unittest.TestCase):
    def test_the_slash_commands_ceiling_is_the_engines(self):
        """`Range[int, 10, 120]` has to be a literal - `@serialized_user_action`
        wraps the handler, so discord.py resolves the annotation against
        runtime.py's globals and a name there is a NameError at import. A
        literal that nothing holds is free to drift from the rule, so this is
        what holds it."""
        source = CULTIVATION.read_text(encoding="utf-8")
        tree = ast.parse(source)
        picker = None
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "seclusion_start":
                for argument in node.args.args:
                    if argument.arg == "minutes":
                        picker = ast.unparse(argument.annotation)
        self.assertIsNotNone(picker, "seclusion start no longer takes a `minutes` picker")
        bounds = [int(n) for n in re.findall(r"\b(\d+)\b", picker)]
        self.assertEqual(len(bounds), 2, f"expected Range[int, low, high]: {picker}")
        low, high = bounds
        self.assertEqual(high, _go_const("seclusionMaxRealMinutes"),
                         "the picker's ceiling is not the engine's cap")
        self.assertLess(low, high, "the picker's floor must leave room above it")

    def test_the_modules_own_constant_agrees_too(self):
        source = CULTIVATION.read_text(encoding="utf-8")
        match = re.search(r"^SECLUSION_MAX_REAL_MINUTES = (\d+)", source, re.M)
        self.assertIsNotNone(match, "SECLUSION_MAX_REAL_MINUTES is gone")
        self.assertEqual(int(match.group(1)), _go_const("seclusionMaxRealMinutes"))

    def test_the_settlement_unit_is_an_hour_and_the_cap_is_several_of_them(self):
        """The two are one decision: a retreat of two real hours is a third of
        a game day at the shipped time scale, so under whole-day accounting a
        retreat run to its cap would have paid exactly nothing."""
        unit = _go_const("seclusionSettleUnitGameMinutes")
        self.assertEqual(unit, 60, "the settlement unit is the game hour")
        self.assertGreater(_go_const("seclusionMaxRealMinutes"), unit,
                           "a retreat shorter than one settlement unit can never pay")


class NothingInPythonStillNamesTheDay(unittest.TestCase):
    def test_no_seclusion_call_sends_a_unit_of_account(self):
        """`minutes_per_day` is refused centrally now, beside the waits and the
        caller's `game_minute`: how long a day is is not the caller's to say
        either. A call still sending it would be refused outright."""
        offenders = []
        for path in sorted((PROJECT_ROOT / "app").rglob("*.py")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if '"minutes_per_day"' in line or "'minutes_per_day'" in line:
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{number}")
        self.assertEqual(offenders, [], "the engine owns the unit of account")

    def test_the_start_asks_in_real_minutes(self):
        source = CULTIVATION.read_text(encoding="utf-8")
        self.assertIn('"duration_real_minutes": int(minutes)', source)
        self.assertNotIn("duration_game_minutes", source,
                         "the legacy field is read by the engine for a stale client, not sent by this one")


if __name__ == "__main__":
    unittest.main()
