"""Every Discord overwrite the bot writes is merged, never replaced (v1.0.11, rc.52).

`set_permissions(target, **perms)` builds a fresh `PermissionOverwrite` from the keywords and
writes it over whatever the target carried, so a bare `view_channel=False` on a read-only anchor
that held `send_messages=False` leaves it hidden and, to everybody holding the role, writable.
`merge_overwrite` in `app/bot/channels.py` is the one statement of the merge, and four gates were
held to it one helper at a time (the cultivation-world gate, the stalls, the war fronts, the event
channels), each by its own instance test. The realm-capital gate was not one of them and still
wrote three overwrites bare, for a release-and-a-half after the rule was written; the v1.40 gate
ledger found it by asking the class.

This walks every `.set_permissions(` call under `app/bot` by AST. A call passes `overwrite=`
(a merged overwrite, or `None` to delete one) or it is an offender: bare permission keywords are
the replacing form. The only call allowed to pass bare keywords is none - `merge_overwrite` itself
passes `overwrite=overwrite`.
"""
from __future__ import annotations

import ast
import unittest

from tests.support import PROJECT_ROOT

BOT = PROJECT_ROOT / "app" / "bot"


def set_permissions_calls(tree: ast.AST) -> list[ast.Call]:
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "set_permissions"]


def bare_calls(tree: ast.AST) -> list[ast.Call]:
    """The calls that replace: no `overwrite=` keyword among their arguments."""
    return [call for call in set_permissions_calls(tree)
            if not any(kw.arg == "overwrite" for kw in call.keywords)]


class TheReaderWorks(unittest.TestCase):
    def test_it_sees_the_shape_it_forbids_and_passes_the_merged_one(self):
        planted = ast.parse(
            "async def f(channel, role, ow):\n"
            "    await channel.set_permissions(role, view_channel=False, reason='x')\n"
            "    await channel.set_permissions(role, overwrite=ow, reason='x')\n"
            "    await channel.set_permissions(role, overwrite=None)\n"
        )
        self.assertEqual(len(set_permissions_calls(planted)), 3)
        self.assertEqual([c.lineno for c in bare_calls(planted)], [2])


class EveryOverwriteIsMerged(unittest.TestCase):
    def test_no_writer_under_the_bot_replaces_an_overwrite(self):
        seen: dict[str, int] = {}
        offenders = []
        for path in sorted(BOT.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for call in set_permissions_calls(tree):
                seen[f"{path.relative_to(PROJECT_ROOT)}:{call.lineno}"] = 1
            for call in bare_calls(tree):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{call.lineno}")
        # Every gate writes through merge_overwrite, so the bot has few of these
        # calls at all: the helper's own, and the deletions. The reader is held
        # to seeing the helper's, which is the one call that may never go.
        self.assertTrue(any(key.startswith("app/bot/channels.py:") for key in seen),
                        "the walk saw no set_permissions call in channels.py; the reader is broken, not the tree")
        self.assertEqual(offenders, [], (
            "set_permissions(target, **perms) replaces the overwrite; go through merge_overwrite, "
            "or pass overwrite=None to delete one"))
