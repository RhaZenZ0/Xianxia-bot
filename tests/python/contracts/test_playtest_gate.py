"""v0.34.0 Gameplay-complete II gate: the playtest is on file.

- `docs/KNOWN_LIMITATIONS.md` exists and every finding is fixed or deferred
  with a reason - never left open without one of those words;
- the checklist under `docs/playtest/` is checked in for the current
  release and names every registered command;
- the engine half of the playtest is a script in the tree that drives every
  loop the roadmap names, and the two defects it found have regression tests;
- the Discord half (v1.0.0-rc.33) is a script beside it that boots the real
  bot against a simulated Discord and drives the loops a player touches, sets
  its environment before it imports the bot, and needs a dependency that never
  reaches the production install.
"""
from __future__ import annotations

import re
import subprocess
import pathlib
import shutil
import sys
import unittest

from tests.support import PROJECT_ROOT

LIMITATIONS = PROJECT_ROOT / "docs" / "KNOWN_LIMITATIONS.md"
VERSION = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
CHECKLIST = PROJECT_ROOT / "docs" / "playtest" / f"v{VERSION}.md"
ENGINE_SCRIPT = (PROJECT_ROOT / "scripts" / "playtest_engine.py").read_text(encoding="utf-8")
DISCORD_SCRIPT = (PROJECT_ROOT / "scripts" / "playtest_discord.py").read_text(encoding="utf-8")


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


class TheChecklistSaysWhatTheSweepProved(unittest.TestCase):
    """The checklist stopped asking a person for what a machine now does
    (v1.0.0-rc.47).

    It was written in v0.34.0, when nothing in the tree could press a button,
    so every action carried three live checkboxes - reachable from the hub,
    error text actionable, narration or fallback fired - for a pass on the
    live server. `scripts/playtest_discord.py` has pressed every leaf since
    rc.33 and `test_playtest_coverage.py` holds it to the live definitions, so
    those boxes were asking for work already done: 248 actions times three,
    seven hundred and forty-four of them, and **not one ever ticked** across
    twelve regenerations.

    The `Swept` column is the sweep's answer instead, and this is what keeps
    it honest - it may claim only what `DEFERRED_LEAVES` leaves unclaimed.
    """

    def setUp(self):
        self.text = CHECKLIST.read_text(encoding="utf-8")
        # Selected by where they are, not by their shape. Selecting on the
        # shape is how the first version of this class let the drill through:
        # it took rows of four cells, so restoring the three old checkboxes
        # made a row of six that the filter simply did not see, and a gate
        # that cannot see the thing it forbids is decoration. The live tables
        # below the heading are a person's and carry boxes by design.
        above = self.text.split("## Loops beyond the hubs", 1)[0]
        self.rows = [line for line in above.splitlines() if line.startswith("| `/")]

    def test_every_action_row_carries_the_sweeps_verdict(self):
        self.assertGreaterEqual(len(self.rows), 240, "the checklist lost its action rows")
        for line in self.rows:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            with self.subTest(row=cells[0]):
                self.assertEqual(len(cells), 4, "an action row is Action | Params | Acked | Swept")
                self.assertIn(cells[3], ("sim", "deferred"))

    def test_no_action_row_asks_a_person_for_what_the_sweep_does(self):
        # The regression this class exists for: a checkbox back on an action
        # row means somebody re-added a column the sweep already fills.
        offenders = [line.split("|")[1].strip() for line in self.rows if "[ ]" in line or "[x]" in line]
        self.assertEqual(offenders, [], f"an action row carries a checkbox again: {offenders}")

    def test_the_deferred_leaves_are_the_harness_own(self):
        """Read off `playtest_discord.py`, never restated here or in the doc.

        A deferral added to the harness and not regenerated into the checklist
        would leave the file claiming the sweep drives a leaf it skips, which
        is the one way this column can lie.
        """
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "playtest_checklist", PROJECT_ROOT / "scripts" / "playtest_checklist.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        deferred = module.deferred_leaves()
        self.assertTrue(deferred, "the harness defers at least the lockdown leaf")
        self.assertIn("### Deferred leaves", self.text, "the checklist stopped listing them")
        section = self.text.split("### Deferred leaves", 1)[1]
        listed = dict(re.findall(r"^- `(/[^`]+)` — (.+)$", section, re.M))
        self.assertEqual(sorted(listed), sorted(deferred),
                         "the checklist and the harness disagree on what the sweep skips - regenerate it")
        for path, reason in deferred.items():
            with self.subTest(path=path):
                # Compared as the generator writes it, so a reflowed reason in
                # the harness still matches.
                self.assertEqual(listed[path], " ".join(str(reason).split()),
                                 "listed without the harness's own reason")

    def test_what_is_left_for_a_person_is_short_and_is_what_only_a_server_shows(self):
        self.assertIn("### What only a live server can show", self.text)
        boxes = self.text.count("[ ]") + self.text.count("[x]")
        self.assertLess(boxes, 60, "the manual pass grew back into a list nobody walks")
        # The two the sweep structurally cannot do, named so they are not lost
        # with the columns that used to ask for them. Looked for in the table
        # itself rather than anywhere in the file: the preamble explains both,
        # so a whole-text search passes on the explanation while the row a
        # person is meant to tick has quietly gone.
        table = "\n".join(line for line in self.text.split("### What only a live server can show", 1)[1].splitlines()
                          if line.startswith("| ") and line.rstrip().endswith("|"))
        for row in ("NARRATOR_PROVIDER=procedural", "every refusal names what is missing"):
            with self.subTest(row=row):
                self.assertIn(row, table, "the live table lost the row only a real server can answer")

    def test_a_tick_survives_regeneration(self):
        """`merge_ticks` kept the per-action cells and dropped the loop rows.

        It looked only at lines starting with `` | ` `` and only at rows of six
        cells or more, so the live tables - the only ticks in the file that
        were ever a person's - were lost on every regeneration. Nobody noticed
        because nobody had ticked one.
        """
        module = _checklist_module()
        loop = "| `/menu` opens every hub; Admin only for an administrator |"
        self.assertIn(loop, self.text, "the loop this test ticks is no longer on the checklist")
        # Drive the merge from a ticked old file to a blank fresh one, rather
        # than reading the checked-in state: since v1.0.0 the live pass is
        # walked and every row of it is `[x]`, and a test that assumed `[ ]`
        # would have started passing vacuously the day somebody ticked it.
        blank = f"{loop} [ ] |"
        old = f"| Loop | Live |\n|---|---|\n{loop} [x] |\n"
        merged = module.merge_ticks(old, blank, "9.9.9")
        self.assertIn(f"{loop} [x] v9.9.9 |", merged,
                      "a tick was lost, or the generator did not date it")


def _checklist_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "playtest_checklist", PROJECT_ROOT / "scripts" / "playtest_checklist.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TheLivePassOutlivesTheReleaseItWasWalkedOn(unittest.TestCase):
    """The live pass is a person's work; a version bump must not spend it.

    `docs/playtest/v<version>.md` is named after `RELEASE_VERSION`, which
    strips the `-rc.N` suffix - so across all fifty-nine release candidates of
    1.0.0 the filename never changed and `merge_ticks` carried every tick.
    v1.0.0 -> v1.0.1 is the first bump in this project's history that renames
    the file, and until now that meant the target did not exist, `old` was the
    empty string, and the freshly generated checklist was written with every
    box blank. The first real live pass would have been deleted by the release
    that followed it.
    """

    LOOP = "| `/menu` opens every hub; Admin only for an administrator |"

    def _tree(self, files: dict[str, str]):
        import tempfile

        root = pathlib.Path(tempfile.mkdtemp())
        (root / "docs" / "playtest").mkdir(parents=True)
        for name, text in files.items():
            (root / "docs" / "playtest" / name).write_text(text, encoding="utf-8")
        self.addCleanup(shutil.rmtree, root, True)
        return root

    def _run(self, root, release: str, fresh: str) -> pathlib.Path:
        """Drive the generator's own `main`, not its helpers.

        Asserting that `merge_ticks` can carry a tick says nothing about
        whether `main` ever hands it the previous release's file - which is
        precisely the wire that was missing. `build` and `version` are stubbed
        because the real ones walk the whole bot; everything under test -
        `_superseded`, `merge_ticks`, `_stamp` and main's own plumbing - is
        the shipped code.
        """
        module = _checklist_module()
        module.ROOT = root
        module.version = lambda: release
        module.build = lambda: fresh
        self.assertEqual(module.main([]), 0)
        return root / "docs" / "playtest" / f"v{release}.md"

    def _fresh(self) -> str:
        return f"| Loop | Live |\n|---|---|\n{self.LOOP} [ ] |\n"

    def test_a_tick_survives_a_release_bump(self):
        root = self._tree({"v1.0.0.md": f"| Loop | Live |\n|---|---|\n{self.LOOP} [x] v1.0.0 |\n"})
        written = self._run(root, "1.0.1", self._fresh())
        self.assertIn(f"{self.LOOP} [x] v1.0.0 |", written.read_text(encoding="utf-8"),
                      "the live pass was blanked by the release that followed it")

    def test_a_carried_tick_says_which_release_walked_it(self):
        """Carrying a tick unstamped would claim a pass that never happened."""
        root = self._tree({"v1.0.0.md": f"| Loop | Live |\n|---|---|\n{self.LOOP} [x] v1.0.0 |\n"})
        written = self._run(root, "1.0.1", self._fresh()).read_text(encoding="utf-8")
        self.assertNotIn(f"{self.LOOP} [x] |", written,
                         "a tick carried across a bump must keep the release it was walked on")

    def test_a_bare_tick_is_dated_with_the_release_being_written(self):
        """The person ticks, the generator dates it - so re-walking is one `[x]`."""
        root = self._tree({"v1.0.1.md": f"| Loop | Live |\n|---|---|\n{self.LOOP} [x] |\n"})
        written = self._run(root, "1.0.1", self._fresh()).read_text(encoding="utf-8")
        self.assertIn(f"{self.LOOP} [x] v1.0.1 |", written)

    def test_an_unticked_box_is_never_dated(self):
        root = self._tree({"v1.0.0.md": self._fresh()})
        written = self._run(root, "1.0.1", self._fresh()).read_text(encoding="utf-8")
        self.assertIn(f"{self.LOOP} [ ] |", written, "an unwalked row was dated as though it had been")

    def test_the_superseded_checklist_is_not_left_behind(self):
        root = self._tree({"v1.0.0.md": f"| Loop | Live |\n|---|---|\n{self.LOOP} [x] v1.0.0 |\n"})
        self._run(root, "1.0.1", self._fresh())
        names = sorted(p.name for p in (root / "docs" / "playtest").glob("v*.md"))
        self.assertEqual(names, ["v1.0.1.md"], "docs/playtest/ is one checklist, not one per release")

    def test_the_newest_checklist_is_the_one_inherited_from(self):
        """Sorted as integers: `v1.0.10` is newer than `v1.0.9` and sorts before it as text."""
        root = self._tree({
            "v1.0.9.md": f"| Loop | Live |\n|---|---|\n{self.LOOP} [x] v1.0.9 |\n",
            "v1.0.10.md": f"| Loop | Live |\n|---|---|\n{self.LOOP} [x] v1.0.10 |\n",
        })
        written = self._run(root, "1.0.11", self._fresh()).read_text(encoding="utf-8")
        self.assertIn(f"{self.LOOP} [x] v1.0.10 |", written,
                      "the ticks were inherited from an older checklist than the newest one")


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


class TheDiscordHalfIsAScriptInTheTree(unittest.TestCase):
    def test_it_drives_the_loops_the_checklist_names(self):
        for marker in ("tree.get_commands", "**Administrator** permission", '"Basechannels"', 'slash(channels["begin-here"], "begin")',
                       '"Open Character Form"', '"player-homes"', '"quests"', '"menu"', "🔒 Enter — you are already inside",
                       '"Errand"', "typed_play_prefix", "typed_play_shorthand", '"cooldowns"', '"Realm Capitals"',
                       "Hearth-Return Talisman", "📜 Quest progress", '"Support"', '"Contribute"', '"Enter"', "Yes, Leave",
                       "Choose destination", "advance_time(901)", '"Reopen"', "env.errors",
                       # v1.0.0-rc.59: the layout half. The sweep is what proves the
                       # re-parent reaches a server that already exists, so the three
                       # assertions that read it are pinned here by name.
                       "CATEGORY_ORDER", "categories are out of order", "BASE_CATEGORY_NAMES[spec.category]",
                       "the retired"):
            with self.subTest(marker=marker):
                self.assertIn(marker, DISCORD_SCRIPT)

    def test_it_never_targets_production_by_accident(self):
        self.assertIn("Never point it at the production database", DISCORD_SCRIPT)
        self.assertIn("--launch", DISCORD_SCRIPT)

    def test_it_sets_the_environment_before_it_imports_the_bot(self):
        """`app/bot/runtime.py` builds the settings, the engine client and the
        database at import, so the bot may only be imported inside `run()`,
        after `_configure` has put the scratch engine into the environment."""
        for line in DISCORD_SCRIPT.splitlines():
            self.assertFalse(line.startswith(("from app", "import app")), f"a module-level bot import: {line!r}")
        body = DISCORD_SCRIPT.split("async def run(", 1)[1]
        self.assertLess(body.index("_configure(url, token, db_path)"), body.index("from app.bot import main"))

    def test_simcord_is_a_dev_dependency_only(self):
        dev = (PROJECT_ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
        self.assertIsNotNone(re.search(r"^simcord==\d", dev, re.M), "pinned, like everything else that is installed")
        for name in ("requirements.txt", "requirements.lock"):
            with self.subTest(file=name):
                self.assertNotIn("simcord", (PROJECT_ROOT / name).read_text(encoding="utf-8"))
        self.assertFalse(list((PROJECT_ROOT / "app").rglob("*.py")) and any(
            "simcord" in path.read_text(encoding="utf-8") for path in (PROJECT_ROOT / "app").rglob("*.py")),
            "nothing under app/ may import the simulated Discord")


if __name__ == "__main__":
    unittest.main()
