"""A quest is recorded before it is told (v1.0.5).

Sixteen call sites wrote `await announce_quest_progress(interaction, await
QUESTS.progress(...))`, and eight of them placed that one statement **after**
the command's reply — each with a comment citing the rule from v1.0.0-rc.28.

That rule is real and it is about the *announcement*: `announce_quest_progress`
falls back to `interaction.response.send_message` when the interaction has not
been answered, so a reporter ahead of a command's only reply spends it on the
quest line and the player never sees their craft roll. It says nothing about the
**record** — and nesting the two made the record inherit the announcement's
position.

v1.0.3's `/craft` is what that cost. The reply raised on a missing key *after*
the engine had committed, so the materials were spent, the pills granted, and
the quest never advanced: six pills made and an errand still reading zero. The
player reported it as two separate bugs.

Two rules, and the first is what makes the second checkable:

1. **The record is never nested inside the telling.** `record_quest_progress`
   is its own statement, so where it sits is visible in the diff.
2. **The record comes before the command answers.** A reply that also returns is
   a refusal path, not the command's answer, so it does not count.
"""
from __future__ import annotations

import ast
import unittest

from tests.support import PROJECT_ROOT

APP = PROJECT_ROOT / "app"

# The ways a command answers. `announce_quest_progress` is deliberately not
# here: it is the telling, and the telling is what must come last.
REPLY_MARKS = ("send_message", "followup.send", "reply_long", "_battle_reply")


def _functions(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _calls(node: ast.AST, predicate) -> bool:
    return any(predicate(n) for n in ast.walk(node))


def _is_reply(n: ast.AST) -> bool:
    return isinstance(n, ast.Call) and any(m in ast.unparse(n.func) for m in REPLY_MARKS)


def _is_record(n: ast.AST, through: frozenset[str] = frozenset()) -> bool:
    """A call that records quest progress, directly or through a helper.

    `through` closes the blind spot the gate's own first run exposed:
    `_report_trade` in `economy.py` records *and* tells, and its two callers
    reach it by name. A gate that only saw direct calls would have let a helper
    be moved after a reply without a word, which is the shape this file exists
    to refuse.
    """
    if not isinstance(n, ast.Call):
        return False
    if isinstance(n.func, ast.Name) and (n.func.id == "record_quest_progress" or n.func.id in through):
        return True
    return isinstance(n.func, ast.Attribute) and n.func.attr == "progress" and "QUESTS" in ast.unparse(n.func)


def _recording_helpers(tree: ast.AST) -> frozenset[str]:
    """Functions in this module that record, so a call to one counts as a record."""
    return frozenset(fn.name for fn in _functions(tree) if _calls(fn, _is_record))


def _returns(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Return) for n in ast.walk(node))


class AQuestIsRecordedBeforeItIsToldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.trees = {
            path.relative_to(PROJECT_ROOT): ast.parse(path.read_text(encoding="utf-8"))
            for path in sorted(APP.rglob("*.py"))
        }
        # A reader is asserted before it is trusted (rc.57): a sweep that parsed
        # nothing would make every assertion below vacuous.
        recorded = sum(
            1 for tree in self.trees.values() for fn in _functions(tree) if _calls(fn, _is_record)
        )
        self.assertGreaterEqual(
            recorded, 10,
            f"the sweep found quest records in only {recorded} functions; it is broken, not the tree",
        )

    def test_the_record_is_never_nested_inside_the_telling(self):
        """`announce_quest_progress(interaction, await QUESTS.progress(...))` is
        the shape that hid the ordering. One statement each, so a reviewer can
        see where the record sits."""
        offenders = []
        for path, tree in self.trees.items():
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "announce_quest_progress"):
                    continue
                for arg in node.args:
                    if _calls(arg, _is_record):
                        offenders.append(f"{path}:{node.lineno}")
        self.assertEqual(
            offenders, [],
            "the record is nested inside the telling, so its position is the telling's: "
            + ", ".join(offenders),
        )

    def test_the_record_comes_before_the_command_answers(self):
        """The fault itself. A presentation failure must not be able to cost a
        player progress the engine has already granted."""
        offenders = []
        for path, tree in self.trees.items():
            helpers = _recording_helpers(tree)
            records = lambda n: _is_record(n, helpers)  # noqa: E731
            for fn in _functions(tree):
                if fn.name in helpers and not _calls(fn, _is_record):
                    continue
                answered_at = recorded_at = None
                for index, stmt in enumerate(fn.body):
                    # A statement that replies and then returns is a refusal,
                    # not the command's answer.
                    if answered_at is None and _calls(stmt, _is_reply) and not _returns(stmt):
                        answered_at = (index, stmt.lineno)
                    if recorded_at is None and _calls(stmt, records):
                        recorded_at = (index, stmt.lineno)
                if answered_at and recorded_at and answered_at[0] < recorded_at[0]:
                    offenders.append(
                        f"{path}:{recorded_at[1]} {fn.name}() answers at line {answered_at[1]} "
                        "and records afterwards"
                    )
        self.assertEqual(
            offenders, [],
            "a quest is recorded only after the command has answered, so a failure while drawing "
            "the reply loses progress the engine already granted:\n" + "\n".join(offenders),
        )

    def test_the_record_helper_never_raises(self):
        """It is called before the reply now, so an exception escaping it would
        take the command's answer with it — turning a quest-service blip into a
        wiring failure on a craft that succeeded."""
        source = (APP / "bot" / "character_state.py").read_text(encoding="utf-8")
        fn = next(
            (f for f in _functions(ast.parse(source)) if f.name == "record_quest_progress"),
            None,
        )
        self.assertIsNotNone(fn, "record_quest_progress is gone; this gate guards a function that moved")
        handlers = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
        self.assertTrue(handlers, "record_quest_progress does not catch anything")
        self.assertTrue(
            any(
                any(h.type is None or "Exception" in ast.unparse(h.type) for h in t.handlers)
                for t in handlers
            ),
            "record_quest_progress must swallow everything: it runs before the reply now",
        )


if __name__ == "__main__":
    unittest.main()
