"""Raids and battle: the solo raid, the Defend guard, and the treatment that always mends.

Merged from:

test_a_solo_raid_needs_no_party.py — A lone cultivator raids without making a party first (v1.7.8).

The engine makes a party of one for the raid when the caller has none, marks it
``raid_only`` and closes it when the raid ends (``boss_solo_test.go`` holds all
of that). This holds the bot's half: the start reply tells the player it
happened, from the engine's own ``solo_party`` flag rather than a guess of its
own, and the flag is one the engine really sends.

test_boss_defend_is_a_guard.py — `/boss act` → Defend sends the style the engine accepts (v1.7.6).

The button was labelled Defend and sent ``"defend"``, while ``bossActActionGo``
accepts ``attack``, ``technique``, ``guard`` and ``support`` - so every Defend
was refused with "style must be attack, technique, guard, or support". The
engine now also reads ``"defend"`` as a guard for an older bot mid-upgrade
(``TestDefendIsAGuard``); this holds the bot's half: every choice the command
offers is a style the engine's switch names.

test_a_treatment_always_mends.py — A treatment always mends (v1.0.16).

Reported from play as six Heart-Calming Pills, six failures and "I can't heal
injuries". The engine now mends at least one level of severity on every
treatment - the roll decides how much, never whether - and the Go tests in
`condition_treat_test.go` hold that. This holds the reply: it must read the
severity the engine wrote rather than decide the outcome off `success`, because
a failed roll that still mended must never be told "the treatment fails".
"""

from __future__ import annotations

import ast
import pathlib
import re
import unittest

import pytest

from tests.support import PROJECT_ROOT, code_only

pytestmark = pytest.mark.unit

BOSS = PROJECT_ROOT / "app" / "bot" / "commands" / "boss.py"
GO = PROJECT_ROOT / "go_core" / "internal" / "game" / "group_combat_actions.go"
ROOT = pathlib.Path(__file__).resolve().parents[3]
LAW = ROOT / "app" / "bot" / "commands" / "law.py"


# --- from test_a_solo_raid_needs_no_party.py ---

def _boss_start_reads() -> set[str]:
    tree = ast.parse(BOSS.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "boss_start":
            keys = set()
            for call in ast.walk(node):
                if (
                    isinstance(call, ast.Call)
                    and getattr(call.func, "attr", "") == "get"
                    and call.args
                    and isinstance(call.args[0], ast.Constant)
                ):
                    keys.add(str(call.args[0].value))
            return keys
    return set()


class ASoloRaidNeedsNoParty(unittest.TestCase):
    def test_the_reader_finds_the_reply(self):
        self.assertIn("encounter_id", _boss_start_reads(), "boss_start could not be read; the gate is broken, not the tree")

    def test_the_reply_says_a_party_was_formed(self):
        self.assertIn("solo_party", _boss_start_reads(), "the start reply never tells a lone cultivator a party was made for them")

    def test_the_engine_sends_the_flag_it_reads(self):
        self.assertIn('"solo_party": soloParty', GO.read_text(encoding="utf-8"))


# --- from test_boss_defend_is_a_guard.py ---

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


# --- from test_a_treatment_always_mends.py ---

def _treat_body() -> str:
    source = LAW.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "condition_treat":
            return code_only(ast.get_source_segment(source, node) or "")
    return ""


class ATreatmentAlwaysMends(unittest.TestCase):
    def test_the_reader_finds_the_handler(self) -> None:
        body = _treat_body()
        self.assertIn('"condition.treat"', body, "the reader did not find condition_treat; the gate is broken, not the tree")

    def test_the_reply_never_says_the_treatment_failed(self) -> None:
        body = _treat_body()
        self.assertNotIn("does not worsen", body, "a treatment that mended a level was told it failed")
        self.assertNotIn("treatment fails", body.lower())

    def test_the_outcome_is_read_off_the_severity_not_the_roll(self) -> None:
        body = _treat_body()
        tree = ast.parse(body.strip() or "pass")
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                # The string constant, not its spelling: `ast.unparse` quotes
                # with ' and the source with ", and the first version of this
                # check matched neither and passed against the broken reply.
                keys = {c.value for c in ast.walk(node.test) if isinstance(c, ast.Constant)}
                self.assertNotIn(
                    "success", keys,
                    f"the reply branches on the roll ({ast.unparse(node.test)}); "
                    "a failed roll still mends, so read severity_after/resolved",
                )
        self.assertIn("severity_before", body, "the reply should say where the severity fell from")
        self.assertIn("severity_after", body)


if __name__ == "__main__":
    unittest.main()
