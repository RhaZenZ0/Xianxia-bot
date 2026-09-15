"""Every objective type has a reporter, and every reporter has a type.

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


def reported_objective_types() -> dict[str, set[str]]:
    """objective type -> the modules that report it.

    Reads the first positional argument after the user id of every
    `QUESTS.progress(...)` call in the bot's command modules.
    """
    found: dict[str, set[str]] = {}
    for path in sorted(COMMANDS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "progress":
                continue
            if not isinstance(func.value, ast.Name) or func.value.id != "QUESTS":
                continue
            # QUESTS.progress(user_id, "<type>", ...) - the type is second and
            # is always a literal, because a computed one could not be pinned.
            if len(node.args) < 2 or not isinstance(node.args[1], ast.Constant):
                raise AssertionError(f"{path.name}: QUESTS.progress must name its objective type literally")
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


if __name__ == "__main__":
    unittest.main()
