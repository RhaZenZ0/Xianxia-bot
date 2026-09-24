"""An objective's label names the command that advances it (v1.0.13).

**Found by playing**: *"Even though I've done multiple successful hunts after
getting the quest it's not getting completed."* The journal read

    ▫️ Come out of one fight standing - **/world → Act → Hunt** 0/1

and the objective's type is `combat_win`, whose **only** reporter in the tree
was `_finish_battle` in `battle.py`. `/hunt` recorded no quest progress at all,
so the beginner path's fourth stage named the one command that could not
advance it, and a player hunted all day at 0/1.

**Three gates already stood here and none could see it.**
`test_quest_objective_reporters.py` holds that every type in `OBJECTIVE_TYPES`
has a reporter - and `combat_win` had one, in `/battle`. It holds that no
reporter names a type the vocabulary lacks, and that no reporter speaks before
its command answers. Every one of those is about the *type*. **The label is the
only part a player reads, and it was held by nothing** - so the one fact that
mattered, that the named command and the reporting command are the same
command, had no gate at all.

That is rc.47's shape once more: not a gate blind to what it forbids, but a
rule nobody had written down, beside three that look like they cover it.

The fix was to make the hunt report `combat_win`, not to re-point the label:
the stage's own description is *"find out what happens when something does not
want you there"*, a failed hunt costs nothing, and sending a brand-new
cultivator to win a real battle - where defeat leaves them on zero vitality -
is not what the first hour is for.
"""
from __future__ import annotations

import ast
import json
import re
import unittest

from tests.support import PROJECT_ROOT

CONTENT = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
COMMANDS = PROJECT_ROOT / "app" / "bot" / "commands"
# `**/world → Act → Hunt**`, as an objective label spells a command.
LABEL_PATH = re.compile(r"\*\*(/[^*]+)\*\*")


def authored_objectives() -> list[tuple[str, str, str]]:
    """(objective type, the path its label names, the quest it belongs to)."""
    out: list[tuple[str, str, str]] = []

    def walk(node: object, quest: str) -> None:
        if isinstance(node, dict):
            quest = str(node.get("quest_key") or node.get("key") or quest)
            if isinstance(node.get("type"), str) and isinstance(node.get("label"), str):
                for path in LABEL_PATH.findall(node["label"]):
                    out.append((str(node["type"]), path.strip(), quest))
            for value in node.values():
                walk(value, quest)
        elif isinstance(node, list):
            for value in node:
                walk(value, quest)

    walk(CONTENT, "")
    # The static quests too (v1.1.0). `road_to_a_sect` is handed to everybody
    # who finishes the beginner path and lives in `app/rules/quests.py`, not in
    # the content file - so this gate never read its labels, which named no
    # command at all until they were given hub paths.
    from app.rules.quests import QUEST_DEFINITIONS

    for key, definition in QUEST_DEFINITIONS.items():
        walk(definition, key)
    return out


def reporters_by_function() -> dict[str, set[str]]:
    """function name -> the objective types it records.

    Both spellings count, as they do in `test_quest_objective_reporters.py`:
    `record_quest_progress` is the door a command uses and `QUESTS.progress` is
    what that door calls.

    **It closes over the module's own calls**, which the gate's own second run
    is the reason for: `/craft`'s reporter lives in `_run_crafting`, not in
    `craft()`, so a scan of the handler alone reported a finding that was not
    one. That is v1.0.5's lesson about `_report_trade` arriving from the other
    side - a reporter reached by name is still that command's reporter.
    """
    found: dict[str, set[str]] = {}
    for path in sorted(COMMANDS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        direct: dict[str, set[str]] = {}
        calls: dict[str, set[str]] = {}
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(func):
                if not isinstance(node, ast.Call):
                    continue
                name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                calls.setdefault(func.name, set()).add(name)
                if name not in {"record_quest_progress", "progress"}:
                    continue
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        direct.setdefault(func.name, set()).add(arg.value)
        for name in set(direct) | set(calls):
            seen, stack, types = set(), [name], set(direct.get(name, ()))
            while stack:
                current = stack.pop()
                if current in seen:
                    continue
                seen.add(current)
                types |= direct.get(current, set())
                stack.extend(calls.get(current, set()) - seen)
            if types:
                found.setdefault(name, set()).update(types)
    return found


def leaf_handlers() -> dict[tuple[str, ...], set[str]]:
    """The live hubs' `(hub[, page], leaf) -> the handlers with that label`.

    Two resolver mistakes, each caught by this gate's own run, are the reason
    for the shape. A bare leaf name is ambiguous across hubs - **Enter** is a
    leaf of both `family` and `realm`, so a label-only key sent `/family →
    Enter` to the secret realm's door. And a leaf name is ambiguous *within* a
    hub: `cultivation` carries **Cultivate** on its `Cultivate` page and again
    on `Body`, so a single-handler value silently kept whichever came last.

    So the value is a set and a label satisfies the gate when **any** handler
    of that name reports its type: the label names a command, and if the name
    resolves to more than one, one of them advancing the quest means the label
    is not a lie. A finding names every candidate.

    The registry is imported rather than parsed because the leaf carries the
    real `handler`, and resolving a label to a function by name would have to
    guess between `use`, `enter` and the rest.
    """
    import os
    import sys

    os.environ.setdefault("DISCORD_TOKEN", "gate")
    os.environ.setdefault("GUILD_ID", "123456789012345678")
    os.environ.setdefault("ENGINE_AUTH_TOKEN", "gate-token-1234567890")
    os.environ.setdefault("DATABASE_PATH", "data/gate.sqlite3")
    os.environ.setdefault("HEALTH_PORT", "18097")
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    import app.bot.surface  # noqa: F401  (registers the hubs)
    from app.bot.hubs import REGISTERED_HUBS, _leaf_actions

    out: dict[tuple[str, ...], set[str]] = {}
    for hub in REGISTERED_HUBS:
        for page in hub.pages:
            for action in _leaf_actions(page):
                handler = getattr(action, "handler", None)
                if handler is None:
                    continue
                name, leaf = str(hub.name).casefold(), str(action.label).casefold()
                out.setdefault((name, leaf), set()).add(handler.__name__)
                out.setdefault((name, str(page.label).casefold(), leaf), set()).add(handler.__name__)
    return out


class AQuestNamesACommandThatCanAdvanceIt(unittest.TestCase):
    def setUp(self):
        self.objectives = authored_objectives()
        self.reporters = reporters_by_function()
        self.leaves = leaf_handlers()
        # Three readers, each asserted before it is trusted (rc.57): any one
        # coming back empty would make the assertion below vacuously true.
        self.assertGreater(len(self.objectives), 10,
                           "no authored objective labels name a command; the gate is broken, not the tree")
        self.assertGreater(len(self.reporters), 5,
                           "no quest reporters found in app/bot/commands; the gate is broken, not the tree")
        self.assertGreater(len(self.leaves), 100,
                           "the hub registry came back with no leaves; the gate is broken, not the tree")

    def test_the_static_quests_are_read(self):
        """Asserted before it is trusted (rc.57): the road into a sect is the
        quest this reader was widened for."""
        quests = {quest for _, _, quest in self.objectives}
        self.assertIn("road_to_a_sect", quests, "QUEST_DEFINITIONS was not walked; the gate is broken, not the tree")

    def test_the_command_a_label_names_reports_the_objectives_type(self):
        offenders, checked = [], 0
        for objective_type, path, quest in self.objectives:
            segments = tuple(seg.strip().lstrip("/").casefold()
                              for seg in path.split("→") if seg.strip())
            # The most specific spelling the label gives, then the loose one.
            handlers = self.leaves.get(segments) or self.leaves.get((segments[0], segments[-1]))
            if not handlers:
                continue  # a root command rather than a hub leaf; nothing to resolve
            checked += 1
            if not any(objective_type in self.reporters.get(h, set()) for h in handlers):
                offenders.append(
                    f"{quest or '?'}: a {objective_type!r} objective labelled {path!r}, but "
                    + "; ".join(f"{h}() reports {sorted(self.reporters.get(h, set())) or 'nothing'}"
                                for h in sorted(handlers)))
        self.assertGreater(checked, 5,
                           "no label resolved to a live hub leaf; the gate is broken, not the tree")
        self.assertFalse(offenders, (
            "a quest tells a player to use a command that cannot advance it. The reporters gate "
            "beside this one only asks whether the *type* has a reporter somewhere, so a label "
            "naming the wrong command is invisible to it - which is how a player hunted all day "
            "at 0/1:\n  " + "\n  ".join(offenders)))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
