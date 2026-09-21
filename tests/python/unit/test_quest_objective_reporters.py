"""Every objective type has a reporter, every reporter has a type, and no
reporter speaks before the command it rides on has answered.

`OBJECTIVE_TYPES` is the ceiling on everything the quest system can ask a
player for - static quests, commissions and the Quest Forge alike - because
`quest.progress` only ever advances an objective whose type matches an event
somebody actually reported. The engine is no help here: `progressQuest`
(go_core/internal/core/contracts.go) matches `objective.Type` against no
whitelist at all, so a vocabulary entry with no `QUESTS.progress(...)` call
behind it is not an error anywhere - it is a quest nobody can ever finish,
and a GM can draft one in the Forge without being told.

Which is how the first five sat for several releases with cultivation,
combat, crafting, travel, the shops and the hills reporting nothing. This is
a source scan, matching the convention in this directory for `app/bot`, which
cannot be imported without discord.
"""

import ast
import unittest

from app.rules.quests import OBJECTIVE_TYPES
from tests.support import PROJECT_ROOT

COMMANDS = PROJECT_ROOT / "app" / "bot" / "commands"
BOT = PROJECT_ROOT / "app" / "bot"


def reported_objective_types() -> dict[str, set[str]]:
    """objective type -> the modules that report it.

    Reads the first positional argument after the user id of every call that
    records progress in the bot's command modules.

    There are two spellings and both count (v1.0.5). `record_quest_progress`
    is the door a command uses; `QUESTS.progress` is what that door calls, and
    is still what anything outside the commands would use. They were one
    spelling until the record was split from the announcement - and this gate
    went red on the split, correctly: it could no longer see a single reporter,
    which is exactly what it exists to notice.
    """
    found: dict[str, set[str]] = {}
    for path in sorted(COMMANDS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "progress" \
                    and isinstance(func.value, ast.Name) and func.value.id == "QUESTS":
                pass
            elif isinstance(func, ast.Name) and func.id == "record_quest_progress":
                pass
            else:
                continue
            # (user_id, "<type>", ...) - the type is second and is always a
            # literal, because a computed one could not be pinned.
            if len(node.args) < 2 or not isinstance(node.args[1], ast.Constant):
                raise AssertionError(
                    f"{path.name}: a quest report must name its objective type literally")
            found.setdefault(str(node.args[1].value), set()).add(path.name)
    return found


class QuestObjectiveReporterTests(unittest.TestCase):
    def test_every_objective_type_is_reported_by_something(self):
        reported = reported_objective_types()
        unreported = sorted(set(OBJECTIVE_TYPES) - set(reported))
        self.assertEqual(
            unreported, [],
            "these objective types can be drafted into a quest and can never be progressed, "
            "because nothing in app/bot/commands reports them: " + ", ".join(unreported),
        )

    def test_nothing_reports_a_type_the_vocabulary_does_not_have(self):
        reported = reported_objective_types()
        unknown = sorted(set(reported) - set(OBJECTIVE_TYPES))
        self.assertEqual(
            unknown, [],
            "these are reported by the bot but are not in OBJECTIVE_TYPES, so the Forge and the "
            "dashboard cannot author them and the report is wasted: " + ", ".join(unknown),
        )

    def test_the_seven_day_one_systems_each_have_a_door(self):
        """The systems a character can reach on their first day, and the type
        that observes each. Named rather than counted, so that removing one
        fails here with the system's name rather than an off-by-one."""
        reported = reported_objective_types()
        for system, objective in (
            ("cultivation", "cultivate"),
            ("travel", "travel"),
            ("combat", "combat_win"),
            ("craft", "craft"),
            ("economy", "trade"),
            ("items", "gather"),
            ("NPCs", "talk"),
            ("the world", "explore"),
        ):
            with self.subTest(system=system):
                self.assertIn(
                    objective, reported,
                    f"nothing reports {objective!r}, so no quest can ask a beginner to touch {system}",
                )


def _enclosing_functions(tree: ast.AST) -> list[ast.AST]:
    return [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _response_calls(func: ast.AST, method: str) -> list[int]:
    """Line numbers of `<something>.response.<method>(...)` inside `func`,
    skipping any nested function - a view callback owns its own interaction."""
    lines: list[int] = []
    for node in ast.walk(func):
        if node is not func and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not isinstance(node, ast.Call):
            continue
        attr = node.func
        if not isinstance(attr, ast.Attribute) or attr.attr != method:
            continue
        inner = attr.value
        if isinstance(inner, ast.Attribute) and inner.attr == "response":
            lines.append(node.lineno)
    return lines


def _reporter_calls(func: ast.AST) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(func):
        if node is not func and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "announce_quest_progress":
            lines.append(node.lineno)
    return lines


class ReporterOrderingTests(unittest.TestCase):
    """A reporter must never spend the interaction's one response.

    `announce_quest_progress` (app/bot/character_state.py) posts through
    `interaction.followup.send` when the interaction has already been answered
    and through `interaction.response.send_message` when it has not - it has
    to, because a command that defers has no other way to be heard. The
    consequence is that a reporter placed *before* a command's only
    `interaction.response.send_message` consumes it: the player is told their
    quest advanced and never sees the craft roll or the harvest, while the
    engine has already granted the items, and the real reply raises
    `InteractionResponded` outside any `except`.

    So within one function body, every `announce_quest_progress(...)` must
    either sit behind a `.response.defer(...)`, or have no
    `.response.send_message(...)` after it. Nothing else in the suite drives a
    slash-command callback with an interaction double, so this source scan is
    the only thing holding the rule.
    """

    def test_no_reporter_speaks_before_its_command_answers(self):
        offenders: list[str] = []
        for path in sorted(BOT.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for func in _enclosing_functions(tree):
                reports = _reporter_calls(func)
                if not reports:
                    continue
                defers = _response_calls(func, "defer")
                sends = _response_calls(func, "send_message")
                for line in reports:
                    if any(d < line for d in defers):
                        continue
                    late = [s for s in sends if s > line]
                    if late:
                        offenders.append(
                            f"{path.relative_to(PROJECT_ROOT)}:{line} in {func.name}() reports before "
                            f"interaction.response.send_message at line {late[0]}"
                        )
        self.assertEqual(
            offenders, [],
            "these quest reporters consume the interaction's one response, so the command's own "
            "result is never shown and raises InteractionResponded - move the report after the "
            "reply, or defer first:\n  " + "\n  ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
