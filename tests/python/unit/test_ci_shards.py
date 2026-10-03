"""The python CI job runs as shards at once, and every test still runs once.

`.github/workflows/ci.yml` runs the suite as a matrix of jobs side by side, each
with `PYTEST_SHARD="<i>/<n>"`, and `tests/conftest.py` keeps the files dealt to
that shard. Splitting a suite is how a suite quietly stops running part of
itself - a file dealt to no shard, a shard count the workflow and the env
disagree on, a deck that is not what pytest collects - and every one of those
would leave CI green. This holds each of them shut.
"""
from __future__ import annotations

import configparser
import fnmatch
import re
import unittest

import tests.conftest as conftest
from tests.support import PROJECT_ROOT, parse_shard, shard_assignment, suite_test_files

WORKFLOW = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def _python_job() -> str:
    return WORKFLOW[WORKFLOW.index("\n  python:"):WORKFLOW.index("\n  go:")]


class TheShardsSplitTheSuite(unittest.TestCase):
    def test_the_deck_is_what_pytest_collects(self):
        """The files the shards are dealt from are read off pytest.ini's own
        testpaths and pattern, so a file pytest collects is never one no shard
        was dealt."""
        ini = configparser.ConfigParser()
        ini.read(PROJECT_ROOT / "pytest.ini")
        root = PROJECT_ROOT / ini["pytest"]["testpaths"].strip()
        pattern = ini["pytest"]["python_files"].strip()
        collected = sorted(p.relative_to(PROJECT_ROOT).as_posix() for p in root.rglob("*.py")
                           if fnmatch.fnmatch(p.name, pattern))
        self.assertGreater(len(collected), 100, "the deck reader found almost nothing; it is broken, not the tree")
        self.assertEqual(suite_test_files(), collected)

    def test_every_file_runs_in_exactly_one_shard(self):
        files = suite_test_files()
        for total in range(1, 9):
            dealt = shard_assignment(files, total)
            self.assertEqual(sorted(dealt), files, f"{total} shards: a file was dealt to none")
            self.assertTrue(all(1 <= s <= total for s in dealt.values()))
            self.assertEqual({s for s in dealt.values()}, set(range(1, total + 1)),
                             f"{total} shards: one shard runs nothing")

    def test_the_hook_keeps_its_shard_and_only_its_shard(self):
        """Driven through the conftest hook itself: across the shards each
        file is collected exactly once."""
        files = suite_test_files()
        total = 4
        saved = conftest._SHARD, conftest._SHARD_OF
        try:
            conftest._SHARD_OF = shard_assignment(files, total)
            runs = {name: 0 for name in files}
            for index in range(1, total + 1):
                conftest._SHARD = (index, total)
                for name in files:
                    if not conftest.pytest_ignore_collect(PROJECT_ROOT / name, None):
                        runs[name] += 1
            self.assertEqual({n: c for n, c in runs.items() if c != 1}, {})
            # Anything that is not a test file - a directory, conftest, a helper -
            # is left to pytest, or a shard would skip the folder holding its files.
            conftest._SHARD = (2, total)
            for path in (PROJECT_ROOT / "tests" / "python", PROJECT_ROOT / "tests" / "conftest.py"):
                self.assertIsNone(conftest.pytest_ignore_collect(path, None), path)
        finally:
            conftest._SHARD, conftest._SHARD_OF = saved

    def test_an_unset_shard_is_the_whole_suite_and_a_bad_one_is_refused(self):
        self.assertIsNone(parse_shard(""))
        self.assertEqual(parse_shard(" 3/4 "), (3, 4))
        for bad in ("4", "0/4", "5/4", "a/b", "1/0"):
            with self.assertRaises(ValueError, msg=bad):
                parse_shard(bad)


class TheWorkflowRunsEveryShard(unittest.TestCase):
    def test_the_matrix_counts_from_one_and_the_env_reads_its_size(self):
        """The shard count is the matrix list and nothing else: the env names
        `strategy.job-total`, so a fifth entry is a fifth shard rather than a
        fifth job running shard 5 of 4."""
        job = _python_job()
        shards = re.search(r"shard: \[([\d, ]+)\]", job)
        self.assertIsNotNone(shards, "the python job no longer has a shard matrix")
        listed = [int(s) for s in shards.group(1).split(",")]
        self.assertEqual(listed, list(range(1, len(listed) + 1)))
        self.assertIn("PYTEST_SHARD: ${{ matrix.shard }}/${{ strategy.job-total }}", job)
        self.assertIn("fail-fast: false", job,
                      "one red shard must not cancel the others and hide their failures")

    def test_every_check_starts_at_once_and_the_release_waits_for_all(self):
        for name in ("python", "go", "containers"):
            start = WORKFLOW.index(f"\n  {name}:")
            header = WORKFLOW[start:WORKFLOW.index("steps:", start)]
            self.assertNotIn("needs:", header, f"{name} waits for another check")
        for name in ("pages", "release"):
            job = WORKFLOW[WORKFLOW.index(f"\n  {name}:"):]
            self.assertIn("needs: [python, go, containers]", job.split("steps:")[0])


if __name__ == "__main__":
    unittest.main()
