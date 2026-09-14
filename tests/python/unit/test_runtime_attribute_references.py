"""Every attribute a command reads off a runtime singleton must exist on it.

`DB`, `WORLD`, `SETTINGS` and `ENGINE` are four objects the whole command
surface reaches through, and a name misspelled on one of them is invisible
until a player walks the branch that reads it. Nothing catches it first:
ruff's F821 finds a name nothing defines, not an attribute nothing defines,
and the suite cannot drive every branch of every command.

That is not hypothetical. `/sense` on a player called
`WORLD.approximate_realm(...)` - a function that lives in `app/rules/sense.py`
and takes its realm lookups as arguments, never a method on World - and would
raise AttributeError on the reading the engine calls "approx". The three
branches beside it (`WORLD.realm_world`, `WORLD.realm_name`,
`WORLD.body_realm_name`) are real methods, which is exactly why the fourth was
written as one. It had never run: that branch needs detection to succeed while
precision is only marginal, and the two target numbers scale so differently
(2 a realm against 7) that the window is all but empty - which is a balance
fault in the sense system, not a reason the name was safe.

So this walks `app/` and resolves every `<singleton>.<attribute>` against the
real class: its methods, the annotations a dataclass declares, and whatever
`__init__` assigns to self. It is a ratchet, like the pyflakes rules in
pyproject.toml - clean today, and it stays clean.

The four cover 1176 of the 1287 singleton reads in the tree. The rest live on
`app.bot.services`, which cannot be imported without a Discord token and a
live engine URL; when that changes they belong here too.
"""
from __future__ import annotations

import ast
import inspect
import unittest
from pathlib import Path

from tests.support import PROJECT_ROOT

from app.database.core import Database
from app.ops.config import Settings
from app.ops.game_engine import GameEngineClient
from app.rules.game import World

APP = PROJECT_ROOT / "app"

SINGLETONS = {
    "DB": Database,
    "WORLD": World,
    "SETTINGS": Settings,
    "ENGINE": GameEngineClient,
}

# A read the checker cannot see through, with the reason it is sound.
EXPECTED_DYNAMIC: set[tuple[str, str]] = set()


def _attributes(cls: type) -> set[str]:
    """Everything an instance of `cls` answers to: its class attributes, the
    fields a dataclass declares as annotations, and the names `__init__` (or
    any method) assigns to self."""
    names = set(dir(cls))
    for base in cls.__mro__:
        names |= set(getattr(base, "__annotations__", {}) or {})
        try:
            source = inspect.getsource(base)
        except (OSError, TypeError):
            continue
        for node in ast.walk(ast.parse(source)):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id == "self"):
                names.add(node.attr)
    return names


class EveryRuntimeAttributeResolves(unittest.TestCase):
    def test_no_command_reads_a_name_its_singleton_does_not_have(self):
        known = {name: _attributes(cls) for name, cls in SINGLETONS.items()}
        unresolved: list[str] = []
        for path in sorted(APP.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)):
                    continue
                target = node.value.id
                if target not in known or node.attr in known[target]:
                    continue
                if (target, node.attr) in EXPECTED_DYNAMIC:
                    continue
                relative = path.relative_to(PROJECT_ROOT)
                unresolved.append(f"{relative}:{node.lineno} {target}.{node.attr}")
        self.assertEqual(
            unresolved, [],
            "These read an attribute the singleton's class does not define, which is an "
            "AttributeError the moment a player reaches the branch:\n  "
            + "\n  ".join(unresolved),
        )

    def test_the_checker_can_still_see_the_attributes_it_is_checking(self):
        """A guard on the guard: if `_attributes` ever returned everything, or
        the singletons stopped being read, the check above would pass by
        finding nothing to look at."""
        known = {name: _attributes(cls) for name, cls in SINGLETONS.items()}
        for name, names in known.items():
            self.assertNotIn(f"definitely_not_an_attribute_of_{name}", names)
        reads = 0
        for path in APP.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                        and node.value.id in SINGLETONS):
                    reads += 1
        self.assertGreater(reads, 900, "the singleton reads have moved or vanished")


if __name__ == "__main__":
    unittest.main()
