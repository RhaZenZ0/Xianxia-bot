"""What the first hour is allowed to meet (v1.0.0-rc.49).

The mechanism for this was built, complete and correct, and no content used
it. `UnexpectedEvent` has carried `min_realm_index` and `max_realm_index`
since the roster was written, `eligibleUnexpectedEvents` in
`exploration_actions.go` filters on both — and **all forty-four events left
both unset**, so every one was eligible for everybody. A cultivator three
minutes old, with attributes of 1 to 3, drew from the same pool as a Nascent
Soul elder: fifteen of the eighteen world events are severity 4 or higher, and
`A Dragon Appears` (severity 10) was as drawable at Body Tempering as the
village festival.

That is this repo's signature fault - the thing is authored, implemented and
one wire short - and it is the third instance found in as many releases, after
`/learn` and the quest journal. The fix is content, because the code was never
the problem.

Two halves, and this file holds both:

  - **A floor, by severity.** How bad a thing is decides how far along you have
    to be for it to turn up in front of you. One rule, stated once, in `FLOOR`.
  - **A gentle band to draw from.** Gating alone would have shown a beginner
    the same three events forever, so `Local Trouble` is five village-scale
    events with a site whose nodes a fresh character can actually work.

The floor gates the **player-triggered** draw only. The autonomous batch still
puts a Demon Invasion wherever the world wants one, and a beginner can walk
into it: being caught in something is not the same as being handed it.
"""
from __future__ import annotations

import json
import unittest

from tests.support import PROJECT_ROOT

WORLD = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
EVENTS = [e for e in WORLD["unexpected_events"] if e.get("kind") == "world_event"]
SITES = WORLD["event_sites"]["categories"]
GO_GATE = PROJECT_ROOT / "go_core" / "internal" / "game" / "beginner_events_test.go"

# Severity -> the realm index at which that event starts being drawable.
FLOOR = {1: 0, 2: 0, 3: 0, 4: 1, 5: 1, 6: 2, 7: 3, 8: 3, 9: 4, 10: 5}
# A fresh character's attributes are 1-3 (`paths` in the content file), and the
# engage roll is 2d10 + attribute against `tn + max(0, severity-2)//2`.
BEGINNER_ATTRIBUTE = 1
REALMS = len(WORLD["realms"])


def drawable_at(realm: int) -> list[dict]:
    """The filter `eligibleUnexpectedEvents` applies, in Python."""
    out = []
    for e in EVENTS:
        if realm < int(e.get("min_realm_index", 0) or 0):
            continue
        ceiling = e.get("max_realm_index")
        if ceiling is not None and realm > int(ceiling):
            continue
        out.append(e)
    return out


def p_success(tn: int, modifier: int) -> int:
    """2d10 + modifier >= tn, as a percentage."""
    return sum(1 for a in range(1, 11) for b in range(1, 11) if a + b + modifier >= tn)


class TheCodeAlreadyReadsTheseFields(unittest.TestCase):
    """The half that makes the rest of this file mean anything - and the half
    this file cannot actually test.

    Asserting on content nothing reads is how `base_ratio` sat dead for
    twenty-odd releases. So the draw has to be proven to honour the band, and
    the first version of this class tried it with a source check: assert the
    body of `eligibleUnexpectedEvents` contains `c.RealmIndex < e.MinRealmIndex`.
    Changing that line to `if false && c.RealmIndex < e.MinRealmIndex` left the
    substring in place and the check passed. **A grep cannot see a disabled
    condition**, so the behavioural half lives in Go, where the function can be
    called: `beginner_events_test.go` hands a realm-0 character a severity-10
    event and asserts it is not offered.

    What is left here is the one thing Python can honestly check - that the Go
    half still exists - so deleting it is a failure rather than a silence.
    """

    def test_the_behavioural_half_is_on_file(self):
        self.assertTrue(GO_GATE.exists(), f"{GO_GATE.name} is gone; nothing proves the draw honours the band")
        source = GO_GATE.read_text(encoding="utf-8")
        for marker in ("eligibleUnexpectedEvents(", "RealmIndex: 0", "RealmIndex: 3"):
            with self.subTest(marker=marker):
                self.assertIn(marker, source)


class EveryWorldEventKnowsWhoItIsFor(unittest.TestCase):
    def test_every_world_event_carries_a_floor(self):
        missing = sorted(e["id"] for e in EVENTS if "min_realm_index" not in e)
        self.assertEqual(missing, [], (
            "these world events are drawable by anybody, including a character three minutes old: "
            f"{missing}"))

    def test_the_floor_is_the_severity_rule_and_nothing_else(self):
        for e in EVENTS:
            with self.subTest(event=e["id"]):
                severity = int(e["severity"])
                self.assertIn(severity, FLOOR, f"severity {severity} has no floor in the rule")
                self.assertEqual(int(e["min_realm_index"]), FLOOR[severity],
                                 "the floor has drifted from the one rule that sets it")

    def test_a_beginner_is_never_handed_something_that_would_end_them(self):
        for e in drawable_at(0):
            with self.subTest(event=e["id"]):
                self.assertLessEqual(int(e["severity"]), 3,
                                     "drawable at Body Tempering and pitched well above it")

    def test_the_ladder_has_no_hole(self):
        """A band that empties at some realm is worse than no band."""
        for realm in range(REALMS):
            with self.subTest(realm=realm):
                self.assertTrue(drawable_at(realm),
                                f"no world event is drawable at realm {realm}")


class TheGentleBandIsWorthDrawing(unittest.TestCase):
    def test_a_beginner_has_several_to_meet_not_three(self):
        # Gating the old roster alone would have left exactly three.
        at_zero = drawable_at(0)
        self.assertGreaterEqual(len(at_zero), 6, (
            "a beginner draws from too small a pool and will see the same event repeatedly: "
            f"{sorted(e['id'] for e in at_zero)}"))

    def test_the_band_fades_rather_than_following_you_up(self):
        gentle = [e for e in EVENTS if e.get("max_realm_index") is not None]
        self.assertGreaterEqual(len(gentle), 5)
        for e in gentle:
            with self.subTest(event=e["id"]):
                self.assertLessEqual(int(e["severity"]), 2, "only the village-scale band fades out")
                self.assertNotIn(e, drawable_at(int(e["max_realm_index"]) + 1))

    def test_every_drawable_category_has_a_site(self):
        """A category with no template falls back to `default`, which is right
        for a stray but wrong for a band authored on purpose."""
        for e in EVENTS:
            if e.get("max_realm_index") is None:
                continue
            with self.subTest(event=e["id"]):
                self.assertIn(e["category"], SITES, "the gentle band has no site of its own")

    def test_its_nodes_are_inside_a_fresh_characters_reach(self):
        """The load-bearing one: a band a beginner cannot roll against is a
        band that only looks friendly."""
        for e in EVENTS:
            if e.get("max_realm_index") is None:
                continue
            severity = int(e["severity"])
            for node in SITES[e["category"]]["nodes"]:
                effective = int(node["tn"]) + max(0, severity - 2) // 2
                odds = p_success(effective, BEGINNER_ATTRIBUTE)
                with self.subTest(event=e["id"], node=node["key"]):
                    self.assertGreaterEqual(odds, 45, (
                        f"{node['key']} is TN {effective} against a fresh character's "
                        f"attribute of {BEGINNER_ATTRIBUTE}: {odds}% - the first hour should not "
                        "be a coin flip you lose"))

    def test_the_easiest_node_is_one_anybody_can_work(self):
        band = {e["category"] for e in EVENTS if e.get("max_realm_index") is not None}
        for category in band:
            tns = [int(n["tn"]) for n in SITES[category]["nodes"]]
            with self.subTest(category=category):
                self.assertLessEqual(min(tns), 10, "nothing here can be done without technique")
                self.assertGreaterEqual(len(tns), 4, "a site with nothing to choose between")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
