"""v0.34.0 Gameplay-complete II gate: the playtest is on file.

- `docs/KNOWN_LIMITATIONS.md` exists and every finding is fixed or deferred
  with a reason - never left open without one of those words;
- the checklist under `docs/playtest/` is checked in for the current
  release and names every registered command;
- the engine half of the playtest is a script in the tree that drives every
  loop the roadmap names, and the two defects it found have regression tests.
"""
from __future__ import annotations

import re
import subprocess
import sys
import unittest

from tests.support import PROJECT_ROOT

LIMITATIONS = PROJECT_ROOT / "docs" / "KNOWN_LIMITATIONS.md"
VERSION = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
CHECKLIST = PROJECT_ROOT / "docs" / "playtest" / f"v{VERSION}.md"
ENGINE_SCRIPT = (PROJECT_ROOT / "scripts" / "playtest_engine.py").read_text(encoding="utf-8")


class ThePunchListIsHonest(unittest.TestCase):
    def test_it_exists_and_every_entry_is_fixed_or_deferred_with_a_reason(self):
        self.assertTrue(LIMITATIONS.exists())
        text = LIMITATIONS.read_text(encoding="utf-8")
        entries = re.findall(r"^- \*\*(fixed|deferred)(?: \(([^)]+)\))?\*\* — ", text, re.M)
        self.assertGreaterEqual(len(entries), 5)
        bullets = [line for line in text.splitlines() if line.startswith("- ")]
        self.assertEqual(len(bullets), len(entries), "every bullet starts with **fixed** or **deferred**")
        for status, reason in entries:
            with self.subTest(status=status, reason=reason):
                self.assertTrue(reason, "a status carries its release or its reason in parentheses")

    def test_the_two_engine_findings_are_pinned_by_go_tests(self):
        game = PROJECT_ROOT / "go_core" / "internal" / "game"
        tests = "\n".join(p.read_text(encoding="utf-8") for p in game.glob("*_test.go"))
        self.assertIn("func TestACommissionCompletesThroughProgressAlone(", tests)
        self.assertIn("func TestResetCooldownsClearsTheTrialRetryWait(", tests)
        actions = (game / "actions.go").read_text(encoding="utf-8")
        self.assertIn("if !isCommission {\n\t\t\tstatus = \"completed\"", actions)
        self.assertIn("trial_retries_cleared", actions)


class TheChecklistIsOnFile(unittest.TestCase):
    def test_the_checklist_for_this_release_names_every_command(self):
        self.assertTrue(CHECKLIST.exists(), f"run scripts/playtest_checklist.py to write {CHECKLIST.name}")
        completed = subprocess.run([sys.executable, "scripts/playtest_checklist.py", "--check"], cwd=PROJECT_ROOT,
                                   capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        text = CHECKLIST.read_text(encoding="utf-8")
        self.assertIn(f"v{VERSION}", text.splitlines()[0])
        self.assertNotIn(":TYPED", text, "a typed id parameter with no picker is on the checklist")

    def test_every_action_the_hubs_can_reach_is_a_row_on_it(self):
        """The checklist and the live surface name the same actions.

        They did not. The generator read hub pages by counting quoted strings
        and never followed `parent=`, so the whole of `/sect recruitment`,
        `/sect discipleship` and `/sect manor` was absent, and a page that
        names the leaves it takes (`only=`, v1.0.0-rc.13) listed its entire
        group instead. A checklist that is missing a command is worse than no
        checklist: it reads as coverage.
        """
        import os
        import re
        from unittest.mock import patch

        env = {"DISCORD_TOKEN": "test-token", "GUILD_ID": "123456789012345678",
               "ENGINE_AUTH_TOKEN": "test-engine-token-1234567890",
               "DATABASE_PATH": "data/test.sqlite3"}
        with patch.dict(os.environ, env):
            import importlib
            hubs = importlib.import_module("app.bot.hubs")
        # Admin is deliberately off the board (GM-only, audited, and nothing a
        # tester can open), so it is excluded here rather than silently absent
        # the way it used to be.
        live = {action.path for definition in hubs.REGISTERED_HUBS if definition.name != "admin"
                for page in definition.pages for action in hubs._leaf_actions(page)}
        text = CHECKLIST.read_text(encoding="utf-8")
        rows = {m.group(1) for m in re.finditer(r"^\| `(/[^`]+)`", text, re.M)}
        self.assertEqual(live - rows, set(), "these actions are reachable from a hub but not on the checklist")

    def test_it_covers_every_hub(self):
        from tests.support import declared_hub_names

        text = CHECKLIST.read_text(encoding="utf-8")
        for hub in declared_hub_names():
            with self.subTest(hub=hub):
                self.assertIn(f"## /{hub} — ", text)


class TheEngineHalfIsAScriptInTheTree(unittest.TestCase):
    def test_it_drives_every_loop_the_roadmap_names(self):
        for op in ("character.create", "exploration.explore", "sect.recruitment.trial", "manual.study",
                   "commission.accept", "quest.progress", "commission.resolve", "admin.quest.save",
                   "admin.quest.review", "admin.narration.set_chain", "admin.player.set_moderation",
                   "admin.audit.undo_last", "auction.sell", "auction.bid", "create_backup(", "restore_backup("):
            with self.subTest(op=op):
                self.assertIn(op, ENGINE_SCRIPT)
        for policy in ("keep", "migrate", "revoke"):
            self.assertIn(f'"hold_policy": "{policy}"', ENGINE_SCRIPT)

    def test_it_never_targets_production_by_accident(self):
        self.assertIn("Never point it at the production database", ENGINE_SCRIPT)
        self.assertIn("--launch", ENGINE_SCRIPT)


if __name__ == "__main__":
    unittest.main()
