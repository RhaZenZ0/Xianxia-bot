"""The playtest runs in parts at once (v1.14.1).

`scripts/playtest_all.py` starts the engine half and the Discord half split
into parts, each sweeping a share of the hubs. Splitting a sweep is how a sweep
quietly stops covering something, so two things are held here: the split
itself (`shard_hubs`) puts every hub in exactly one part, and the runner's
union check refuses a run whose parts did not between them sweep every hub
exactly once. The third is what made running them together possible at all:
no harness binds a fixed port any more.
"""
from __future__ import annotations

import re
import sys
import unittest

from tests.support import PROJECT_ROOT, code_only

SCRIPTS = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import playtest_all  # noqa: E402 - the scripts directory is not a package
from playtest_common import parse_shard, shard_hubs  # noqa: E402

HUBS = [("admin", 40), ("cultivation", 34), ("sect", 29), ("economy", 27), ("character", 25),
        ("family", 24), ("combat", 24), ("craft", 12), ("world", 10), ("travel", 6), ("beast", 8)]


def shard_log(index: int, parts: int, hubs: list[str], total: int) -> str:
    return f"PASS  SHARD {index}/{parts} hubs: {','.join(sorted(hubs))} (of {total})\n"


class TheSweepSplitsWithoutLosingAHub(unittest.TestCase):
    def test_every_hub_lands_in_exactly_one_part(self):
        for parts in (1, 2, 3, 5, len(HUBS), len(HUBS) + 2):
            with self.subTest(parts=parts):
                split = shard_hubs(HUBS, parts)
                self.assertEqual(len(split), parts)
                every = [hub for part in split for hub in part]
                self.assertEqual(sorted(every), sorted(name for name, _ in HUBS))

    def test_one_part_is_the_whole_sweep_and_the_split_is_the_same_every_time(self):
        self.assertEqual(sorted(shard_hubs(HUBS, 1)[0]), sorted(name for name, _ in HUBS))
        self.assertEqual(shard_hubs(HUBS, 3), shard_hubs(list(reversed(HUBS)), 3))

    def test_the_parts_are_balanced_by_leaves(self):
        weight = dict(HUBS)
        loads = [sum(weight[h] for h in part) for part in shard_hubs(HUBS, 3)]
        self.assertLessEqual(max(loads) - min(loads), max(weight.values()), loads)

    def test_a_shard_is_one_based_and_inside_its_run(self):
        self.assertEqual(parse_shard("2/3"), (2, 3))
        for bad in ("0/3", "4/3", "x/3"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_shard(bad)


class TheUnionCheckRefusesAGap(unittest.TestCase):
    def logs(self, parts: int) -> dict[str, str]:
        split = shard_hubs(HUBS, parts)
        return {f"discord {i}/{parts}": shard_log(i, parts, hubs, len(HUBS)) for i, hubs in enumerate(split, 1)}

    def test_a_complete_split_passes(self):
        self.assertEqual(playtest_all.union_problems(self.logs(3), 3), [])

    def test_a_part_that_never_said_what_it_swept_fails(self):
        logs = self.logs(3)
        logs.pop("discord 2/3")
        self.assertIn("part 2/3 never said which hubs it swept", playtest_all.union_problems(logs, 3))

    def test_a_hub_swept_twice_or_dropped_fails(self):
        logs = self.logs(2)
        first = shard_hubs(HUBS, 2)[0]
        logs["discord 2/2"] = shard_log(2, 2, first, len(HUBS))
        problems = " ".join(playtest_all.union_problems(logs, 2))
        self.assertIn("swept by more than one part", problems)
        logs = self.logs(2)
        logs["discord 2/2"] = shard_log(2, 2, shard_hubs(HUBS, 2)[1][1:], len(HUBS))
        problems = " ".join(playtest_all.union_problems(logs, 2))
        self.assertIn(f"hubs of {len(HUBS)}", problems)

    def test_the_line_the_runner_reads_is_the_line_the_harness_writes(self):
        harness = (SCRIPTS / "playtest_discord.py").read_text(encoding="utf-8")
        written = 'f"SHARD {shard[0]}/{shard[1]} hubs: {\',\'.join(sorted(mine))} (of {len(weights)})"'
        self.assertIn(written, harness)
        self.assertTrue(playtest_all.SHARD_LINE.search(shard_log(1, 1, ["a", "b"], 2)))


class NoHarnessBindsAFixedPort(unittest.TestCase):
    def test_every_port_is_taken_free(self):
        for path in sorted(SCRIPTS.glob("playtest_*.py")):
            with self.subTest(script=path.name):
                # Code only: the docstring that explains the old address names it.
                text = code_only(path.read_text(encoding="utf-8"))
                fixed = re.findall(r"127\.0\.0\.1:1\d{4}\b", text)
                self.assertEqual(fixed, [], "a fixed address keeps two runs from sharing a machine")
                self.assertIsNone(re.search(r"^\s*(?:HEALTH_PORT|ENGINE_ADDR)\s*=\s*\d", text, re.M))
        common = (SCRIPTS / "playtest_common.py").read_text(encoding="utf-8")
        self.assertIn("free_port()", common.split("def launch_engine", 1)[1])


if __name__ == "__main__":
    unittest.main()
