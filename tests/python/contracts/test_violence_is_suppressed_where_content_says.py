"""Where a fight may not begin, and where it may not reach (v1.0.6).

`app/ai/narrator_context.py` tells the model, in these words, in two places:
*"PROTECTED; violence cannot mechanically begin here"*. Three things enforced
it - the duel invariant in `pvp_invariants.go`, `/duel` in `duel.py`, and
`/battle challenge` in `battle.py` - and `combat_actions.go` named `SafeZone`
zero times, so the one rule a player could feel for PvE lived entirely in the
bot. That is v1.0.0-rc.48's rule for the fourth time in this tree: **a bound
that lives in the client is not a bound**. What made it invisible is that the
neighbouring kind of violence *was* engine-held, so the file next door looked
like proof the rule was enforced.

And two systems act on a player without being asked. `auctionLeaveAction`
creates the door incident's battle at `EntranceLocation`, outside, which is
right and is what the houses' own prose describes. `advanceHunters` consulted
no location at all, so a fugitive was **captured** inside a hall whose
description reads *"Violence inside is forbidden; the protection ends at the
front doors"* - with `protected_interior` sitting on all 48 houses, read by
nothing.

This file holds the content half. The behaviour is held in Go, where it can be
driven rather than grepped (`violence_suppression_test.go`,
`sanctuary_pursuit_test.go`) - a grep cannot see a disabled condition, which is
rc.49's finding, so what is asserted here is only what a read can honestly
answer.
"""
from __future__ import annotations

import json
import subprocess
import unittest

from tests.support import PROJECT_ROOT

CONTENT = PROJECT_ROOT / "content" / "world.json"
GO = PROJECT_ROOT / "go_core"
APP = PROJECT_ROOT / "app"


class WhereViolenceIsSuppressed(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = json.loads(CONTENT.read_text(encoding="utf-8"))
        cls.houses = cls.raw["auction_houses"]
        cls.locations = cls.raw["locations"]
        # A reader is asserted before it is trusted (rc.57): a parse that came
        # back empty would make every assertion below vacuous.
        assert len(cls.houses) > 40 and len(cls.locations) > 400, "the content parse is broken, not the tree"

    def test_every_protected_floor_is_both_a_safe_zone_and_a_sanctuary(self):
        """The two claims are made in two vocabularies and a floor needs both.

        `safe_zone` on the interior location is what stops a fight being
        started there; `protected_interior` on the house is what stops a bounty
        hunter reaching in. A floor carrying one and not the other is half a
        sanctuary, and nothing would error.
        """
        for key, house in self.houses.items():
            with self.subTest(house=key):
                inside = house["location"]
                self.assertIn(inside, self.locations, f"{key}'s interior is not a catalogue location")
                self.assertTrue(
                    self.locations[inside].get("safe_zone"),
                    f"{key}'s floor is not a safe zone, so a fight may be started on it",
                )
                self.assertTrue(
                    house.get("protected_interior"),
                    f"{key} claims no protected interior, so a bounty hunter may take somebody off its floor",
                )
                self.assertEqual(
                    self.locations[inside].get("auction_house"), key,
                    f"{key}'s floor does not point back at it, and LocationIsSanctuary reads that pointer",
                )

    def test_a_sanctuary_is_rare_and_a_safe_zone_is_not(self):
        """The measurement the two-predicate design rests on.

        `safe_zone` is true on the large majority of the map - it means *not
        the wilds*, not sanctuary - which is exactly why the hunter is gated on
        `protected_interior` instead. Gating it on `safe_zone` would not give
        the bounty system a sanctuary, it would end it, because players live in
        towns. If someone ever marks the map protected, this is what says so.
        """
        safe = [k for k, v in self.locations.items() if v.get("safe_zone")]
        sanctuaries = {h["location"] for h in self.houses.values() if h.get("protected_interior")}
        self.assertGreater(
            len(safe), len(self.locations) // 2,
            "safe_zone is no longer the ordinary case; the premise that it means 'not the wilds' has moved",
        )
        self.assertLess(
            len(sanctuaries), len(safe) // 4,
            f"{len(sanctuaries)} sanctuaries against {len(safe)} safe zones: a sanctuary has stopped being "
            "rare, and gating a bounty hunter on one now hides fugitives across the map",
        )
        self.assertTrue(sanctuaries, "no protected interior survives; the hunter's gate reaches nothing")

    def test_the_retired_door_field_is_gone_from_content_and_from_the_tree(self):
        """`door_rule` was the third statement of the same sentence.

        It said "the protection ends at the doors", which is the second half of
        what `protected_interior` opens; all 48 houses set it `true` and no
        house's prose can differ; and the engine already ends the protection at
        the door by creating the incident battle at `EntranceLocation`. A
        switch content cannot turn off is not a switch.
        """
        for key, house in self.houses.items():
            self.assertNotIn("door_rule", house, f"{key} carries the retired door_rule field again")
        found = subprocess.run(
            ["grep", "-rn", "--include=*.go", "--include=*.py", "--include=*.js",
             "--exclude=*_test.go", "--exclude-dir=tests", "door_rule", "DoorRule",
             str(GO), str(APP), str(PROJECT_ROOT / "dashboard")],
            capture_output=True, text=True,
        )
        offenders = [
            line for line in found.stdout.splitlines()
            # This file's own explanation of the retirement is prose, not code
            # - rc.52's rule, and the reason that gate had to blank comments.
            if "violence_suppression" not in line
        ]
        self.assertEqual(offenders, [], "door_rule is retired but still named in production:\n" + "\n".join(offenders))

    def test_the_engine_holds_the_refusal_the_bot_anticipates(self):
        """`battle.py`'s refusal is kept and is no longer the only statement.

        Presentation is allowed to anticipate a refusal the engine will make -
        that is what `_progression_hidden_actions` does with every late door.
        What it may not be is the only place the rule lives, which is what it
        was. The behavioural half is `TestASafeZoneRefusesAFightSomebodyChoseToStart`;
        all this can honestly check is that the Go half still exists.
        """
        battle = (APP / "bot" / "commands" / "battle.py").read_text(encoding="utf-8")
        self.assertIn(
            "location_safe_zone", battle,
            "the bot stopped anticipating the refusal; the engine still makes it, but a player now "
            "spends a round trip to be told no",
        )
        combat = (GO / "internal" / "game" / "combat_actions.go").read_text(encoding="utf-8")
        self.assertIn(
            "violenceSuppressed(catalog", combat,
            "combat.start no longer asks whether violence is suppressed where it is about to start a "
            "fight; the bound is back in the client, which is not a bound",
        )
        rule = (GO / "internal" / "game" / "violence_suppression.go").read_text(encoding="utf-8")
        for half in ("func violenceSuppressed(", "func LocationIsSanctuary("):
            self.assertIn(half, rule, f"{half} is gone; this gate is guarding a rule that moved")


if __name__ == "__main__":
    unittest.main()
