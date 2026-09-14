"""Every hub path a reply prints in bold must resolve to an action.

Since v0.40.0 a reply that names a hub path (`**/world → Act → Explore**`)
earns that path as a tappable button underneath. `_hint_action` resolves it,
and when it cannot it returns None and the button simply is not drawn - no
error, nothing in a log. So a path that stops resolving is invisible until
somebody notices a button missing, which is why this is a test and not a
convention.

Thirty of a hundred and fifty-four were in that state: the author wrote
`**/family → Leave**`, naming the action directly, while the resolver required
`hub → page → action` and gave up when the first step matched no page. The
resolver now falls through to the action, and this holds the whole tree to it.
"""
from __future__ import annotations

import importlib
import os
import re
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support import PROJECT_ROOT

ENV = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
       "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890", "DATABASE_PATH": "data/test.sqlite3"}

# Roots that are commands rather than hubs: `**/quests**` names one and is not
# a hub path at all.
ROOT_COMMANDS = {"quests", "me", "begin", "action", "check", "menu", "tribute", "cooldowns"}


def _modules():
    with patch.dict(os.environ, ENV):
        return importlib.import_module("app.bot.hubs")


def _tracked_sources() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "app", "content"], cwd=PROJECT_ROOT,
                         capture_output=True, text=True, check=True).stdout.split()
    return [PROJECT_ROOT / f for f in out if f.endswith((".py", ".json"))]


class EveryPrintedHubPathResolves(unittest.TestCase):
    def test_no_reply_promises_a_button_the_resolver_cannot_find(self):
        hubs = _modules()
        names = {d.name for d in hubs.REGISTERED_HUBS}
        broken: list[str] = []
        checked = 0
        for path in _tracked_sources():
            for match in hubs._HINT_PATH_RE.finditer(path.read_text(encoding="utf-8")):
                hub = match.group(1)
                if hub in ROOT_COMMANDS:
                    continue
                self.assertIn(hub, names, f"{path.name}: {match.group(0)} names no hub")
                steps = [s for s in re.split(r"\s*→\s*", match.group(2) or "") if s.strip()]
                checked += 1
                if hubs._hint_action(hub, steps) is None:
                    broken.append(f"{path.relative_to(PROJECT_ROOT)}: {match.group(0)}")
        self.assertEqual(broken, [], "these printed paths resolve to no action")
        # A floor, so deleting the corpus cannot make this pass vacuously.
        self.assertGreater(checked, 100, "the hint corpus went missing")


class AStepMayNameAnActionOrAPage(unittest.TestCase):
    def test_a_path_that_names_a_page_still_resolves_through_it(self):
        hubs = _modules()
        action = hubs._hint_action("world", ["Act", "Explore"])
        self.assertIsNotNone(action)
        self.assertEqual(action.path, "/explore")

    def test_a_path_that_names_only_an_action_finds_it_across_the_pages(self):
        hubs = _modules()
        for hub, step, expected in (
            ("family", "Leave", "/family leave"),
            ("family", "Investigate", "/family investigate"),  # a different page
            ("abode", "Revoke", "/abode revoke"),
            ("beast", "Tame", "/beast tame"),
        ):
            with self.subTest(hub=hub, step=step):
                action = hubs._hint_action(hub, [step])
                self.assertIsNotNone(action, f"/{hub} → {step}")
                self.assertEqual(action.path, expected)

    def test_a_step_that_names_neither_still_resolves_to_nothing(self):
        # The fall-through must not start inventing buttons.
        hubs = _modules()
        self.assertIsNone(hubs._hint_action("family", ["Nonsense"]))

    def test_a_bare_hub_still_means_the_thing_you_do_there(self):
        hubs = _modules()
        self.assertIsNotNone(hubs._hint_action("travel", []))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
