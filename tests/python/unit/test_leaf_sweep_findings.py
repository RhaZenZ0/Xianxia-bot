"""What the leaf sweep found on its first run (v1.0.0-rc.35), held.

`scripts/playtest_discord.py` presses every leaf of every hub and fails a leaf
whose reply is the hub's own failure text - the string printed when a handler
raised. Its first green run was preceded by four red ones, each a handler that
had never been pressed by anything: no trade had ever left `/trade offer`, the
forage reply raised on every forage, the event scene called a property, and a
GM's grant to a member with no character crashed instead of refusing. Each is
held here by the shape that failed, so the fix cannot quietly come undone.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tests.support import PROJECT_ROOT

BOT = PROJECT_ROOT / "app" / "bot"


def _functions(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            yield node


def _authoritative_payloads(fn: ast.AST):
    """Every dict literal handed to `ENGINE.authoritative_action` in `fn`: the
    third argument itself, or the value assigned to the name passed there."""
    assigned: dict[str, ast.Dict] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned[target.id] = node.value
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "authoritative_action"):
            continue
        if len(node.args) < 3:
            continue
        payload = node.args[2]
        if isinstance(payload, ast.Dict):
            yield node.lineno, payload
        elif isinstance(payload, ast.Name) and payload.id in assigned:
            yield node.lineno, assigned[payload.id]


class NoAuthoritativePayloadCarriesAMinute(unittest.TestCase):
    def test_the_client_would_refuse_it_before_sending(self):
        """`GameEngineClient.authoritative_action` raises on a `game_minute`
        key and the engine refuses one too: Go stamps the canonical minute on
        every authoritative action. Three trade payloads carried one, so no
        trade had ever left Discord."""
        offenders = []
        for path in sorted(BOT.rglob("*.py")):
            for fn in _functions(path):
                for lineno, payload in _authoritative_payloads(fn):
                    keys = {k.value for k in payload.keys if isinstance(k, ast.Constant)}
                    if "game_minute" in keys:
                        offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{lineno} {fn.name}")
        self.assertEqual(offenders, [])

    def test_a_switch_operation_goes_through_action_not_the_authoritative_client(self):
        """`npc.found` is dispatched by the engine's switch, not its allowlist,
        and takes the minute the bot read off the engine the way
        `quest.progress` does."""
        scene = (BOT / "commands" / "scene.py").read_text(encoding="utf-8")
        self.assertNotIn('authoritative_action(\n            "npc.found"', scene)
        self.assertNotIn('authoritative_action(\n                "npc.found"', scene)
        self.assertEqual(scene.count('ENGINE.action(\n            "npc.found"') + scene.count('ENGINE.action(\n                "npc.found"'), 2)


class TheForageReplyPrintsTheEnginesRoll(unittest.TestCase):
    def test_roll_line_reads_the_roll_map_not_the_flat_dice(self):
        source = (BOT / "commands" / "exploration.py").read_text(encoding="utf-8")
        body = source.split("async def alchemy_forage(", 1)[1].split("\n@", 1)[0]
        self.assertIn('roll = SimpleNamespace(**dict(resolved.get("roll") or {}))', body)
        self.assertEqual(body.count("roll_line(roll)"), 2)
        self.assertNotIn("roll_line(result)", body)
        go = (PROJECT_ROOT / "go_core" / "internal" / "game" / "crafting_actions.go").read_text(encoding="utf-8")
        self.assertIn('"roll":                roll,', go)


class TheEventSceneReadsAProperty(unittest.TestCase):
    def test_unexpected_events_is_not_called(self):
        from pathlib import Path as _P

        from app.rules.game import World

        self.assertIsInstance(World.unexpected_events, property)
        for path in sorted(BOT.rglob("*.py")):
            with self.subTest(path=str(path.relative_to(PROJECT_ROOT))):
                self.assertNotIn("unexpected_events()", path.read_text(encoding="utf-8"))
        del _P


class AGrantToNobodyIsRefusedNotCrashed(unittest.TestCase):
    def test_every_admin_lever_on_a_member_checks_the_character_first(self):
        """The GM levers that take a member and call the engine either check
        the member has a character or catch the engine's refusal; the currency
        grant did neither and printed the hub's failure text."""
        offenders = []
        for path in sorted((BOT / "admin").rglob("*.py")):
            for fn in _functions(path):
                if not isinstance(fn, ast.AsyncFunctionDef) or "member" not in {a.arg for a in fn.args.args}:
                    continue
                source = ast.get_source_segment(path.read_text(encoding="utf-8"), fn) or ""
                calls_engine = "ENGINE.action(" in source or "ENGINE.authoritative_action(" in source
                guarded = "get_character(member.id)" in source or "except GameEngineError" in source
                if calls_engine and not guarded:
                    offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{fn.lineno} {fn.name}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
