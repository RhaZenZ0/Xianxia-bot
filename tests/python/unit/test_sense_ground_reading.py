"""The line a spiritual-sense sweep prints about the ground it is standing on.

The engine decides how much a sweep learned; this only chooses the words, so
what is pinned here is the ladder - a weak sweep gets the word for the ground,
a better one what is making it so, an overwhelming one the numbers - and that a
sweep which read nothing prints nothing rather than a confident "ordinary".

The numbers themselves are Go's: `placeCultivationMultiplier` has priced the
ground since v1.0.0-rc.4 and the sweep reads that same value, so a player is
never told one thing and paid another. That agreement is held in Go, by
TestSenseGroundReadingTracksTheCultivationMultiplier, not re-derived here.
"""
from __future__ import annotations

import json
import unittest

from tests.support import PROJECT_ROOT

from app.rules.sense import circuit_stop, ground_reading_line
from app.rules.worldtime import MINUTES_PER_MONTH


class TheGroundReadingFollowsWhatTheSweepEarned(unittest.TestCase):
    def test_nothing_read_prints_nothing(self):
        for empty in (None, {}):
            self.assertEqual(ground_reading_line(empty), "")

    def test_a_vague_sweep_names_the_quality_and_admits_it_cannot_say_why(self):
        line = ground_reading_line({"quality": "rich", "detail": "vague"})
        self.assertIn("rich", line)
        self.assertIn("cannot tell", line)
        self.assertNotIn("×", line)

    def test_a_named_sweep_says_what_is_gathering_the_qi(self):
        line = ground_reading_line(
            {"quality": "good", "detail": "named", "ground": "the shrine"}
        )
        self.assertIn("good", line)
        self.assertIn("the shrine", line)
        self.assertNotIn("×", line)

    def test_a_named_sweep_on_nameless_ground_still_reads_cleanly(self):
        # Most ground is nothing in particular, and the engine sends "" for it.
        line = ground_reading_line({"quality": "ordinary", "detail": "named", "ground": ""})
        self.assertTrue(line.endswith("."), line)
        self.assertNotIn("gathered by", line)

    def test_an_exact_sweep_gives_the_multiplier_and_the_world(self):
        line = ground_reading_line({
            "quality": "rich", "detail": "exact", "ground": "Cloudrest Cave",
            "multiplier": 1.45, "world_qi": 1.6,
        })
        self.assertIn("×1.45", line)
        self.assertIn("×1.6", line)
        self.assertIn("Cloudrest Cave", line)

    def test_the_multiplier_is_printed_without_trailing_zeroes(self):
        line = ground_reading_line({
            "quality": "ordinary", "detail": "exact", "ground": "", "multiplier": 1.0, "world_qi": 1.0,
        })
        self.assertIn("×1**", line)
        self.assertNotIn("1.0", line)


if __name__ == "__main__":
    unittest.main()


class AHiddenMasterWalksTheRoad(unittest.TestCase):
    """A recluse who never moves is a landmark, not a rumour.

    `circuit_stop` is a pure function of the canonical clock, so a wandering
    master needs no simulation tick and no stored state: the same minute always
    gives the same place, which is what lets this be tested at all.
    """

    def test_an_empty_circuit_puts_nobody_anywhere(self):
        self.assertIsNone(circuit_stop([], 10_000_000))
        self.assertIsNone(circuit_stop(["", "  "], 10_000_000))

    def test_a_stop_holds_for_its_months_and_then_changes(self):
        circuit = ["A", "B", "C"]
        month = MINUTES_PER_MONTH
        self.assertEqual(circuit_stop(circuit, 0, months=2), "A")
        self.assertEqual(circuit_stop(circuit, 2 * month - 1, months=2), "A")
        self.assertEqual(circuit_stop(circuit, 2 * month, months=2), "B")
        self.assertEqual(circuit_stop(circuit, 4 * month, months=2), "C")

    def test_the_circuit_comes_back_around(self):
        circuit = ["A", "B", "C"]
        self.assertEqual(circuit_stop(circuit, 6 * MINUTES_PER_MONTH, months=2), "A")

    def test_an_offset_staggers_two_masters_sharing_a_road(self):
        circuit = ["A", "B", "C"]
        self.assertNotEqual(
            circuit_stop(circuit, 0, months=2, offset=0),
            circuit_stop(circuit, 0, months=2, offset=1),
        )

    def test_a_negative_minute_is_the_start_of_the_world_not_a_crash(self):
        self.assertEqual(circuit_stop(["A", "B"], -5), "A")

    def test_months_below_one_do_not_divide_by_zero(self):
        self.assertIn(circuit_stop(["A", "B"], MINUTES_PER_MONTH, months=0), {"A", "B"})


class EveryCircuitNamesARealPlace(unittest.TestCase):
    """Content contract: a circuit stop that is not a location would strand a
    hidden master somewhere no player can stand, silently - `/sense` would
    simply never find them and nothing would say why."""

    def test_no_wandering_master_walks_to_a_place_that_does_not_exist(self):
        world = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
        locations, npcs = world["locations"], world["npcs"]
        walkers = {n: v for n, v in npcs.items() if v.get("circuit")}
        self.assertGreaterEqual(len(walkers), 8, "the wandering masters have gone missing")
        broken = [
            f"{name} -> {stop}"
            for name, npc in walkers.items()
            for stop in npc["circuit"]
            if stop not in locations
        ]
        self.assertEqual(broken, [], "circuit stops that are not locations: " + ", ".join(broken))

    def test_every_walker_is_a_hidden_master_with_somewhere_to_go(self):
        world = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
        for name, npc in world["npcs"].items():
            if not npc.get("circuit"):
                continue
            self.assertIsInstance(npc.get("hidden_master"), dict, f"{name} walks but is nobody")
            self.assertGreaterEqual(len(npc["circuit"]), 2, f"{name}'s circuit is one place")
            self.assertGreaterEqual(int(npc.get("circuit_months", 2)), 1, name)
