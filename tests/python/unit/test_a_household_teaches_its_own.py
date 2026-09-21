"""A household teaches its own tradition (v1.0.3).

Eight of the thirteen birth households handed a child **another sect's** entry
canon as the family's own teaching - a fallen martial clan teaching the Azure
Cloud Sect's sword canon, a tomb-watch clan teaching the Jade Meridian Sect's -
and `sect_actions.py`'s one reader of `manual.sect` is the sect-inheritance
redemption list, so those ids are literally that sect's material. The other five
handed out a procedurally generated manual with its catalogue index in the title
("Starfall Scripture - Sword Cultivator 25"), and two of those named a Sword
Cultivator manual while the household's trade was Formation.

It happened because there was nowhere correct to point them. Of 160 manuals only
18 were authored; 12 carry a `sect` and the other 6 are path-locked dark arts
that `manualForbidden` refuses at the lesson outright - so **no authored,
sect-less, non-forbidden manual existed anywhere in the game.**

The split was a silent mechanical difference too: the eight sect canons are
Mortal grade (x1.03 gathering) and the five generated ones Spirit (x1.12), and
`manualCultivationMultiplier` rides every cultivation session for the whole of a
character's life. Which household you were born into was worth 9% of your
cultivation for ever, decided by which of two wrong options the author reached
for, and stated nowhere.
"""
from __future__ import annotations

import json
import unittest

from tests.support import PROJECT_ROOT

WORLD = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
LESSON = WORLD["birth_family_lesson"]
MANUALS = WORLD["technique_system"]["manuals"]
ITEMS = WORLD["items"]

# `manualForbidden` in go_core/internal/game/manual_forbidden_actions.go.
FORBIDDEN_TAGS = {"forbidden", "demonic", "evil"}


class AHouseholdTeachesItsOwnTests(unittest.TestCase):
    def setUp(self) -> None:
        # A reader is asserted before it is trusted (rc.57): a lesson block that
        # silently came back empty would make every assertion below vacuous.
        self.assertGreaterEqual(len(LESSON), 13, "birth_family_lesson did not parse; the reader is broken, not the tree")
        self.assertGreaterEqual(len(MANUALS), 160, "the manual catalogue did not parse; the reader is broken, not the tree")

    def test_no_household_teaches_another_sects_canon(self):
        """The finding, stated as a rule."""
        borrowed = {
            household: (entry["manual"], MANUALS[entry["manual"]]["sect"])
            for household, entry in LESSON.items()
            if str(MANUALS.get(entry["manual"], {}).get("sect", "")).strip()
        }
        self.assertEqual(
            borrowed, {},
            "a household teaches a sect's own canon as its family tradition: "
            + ", ".join(f"{h} teaches the {s}'s {m}" for h, (m, s) in sorted(borrowed.items())),
        )

    def test_every_household_manual_is_one_the_lesson_can_actually_hand_over(self):
        """The three rules `household_lesson.go` enforces at run time, held at
        authoring time instead - so a bad edit fails the suite rather than the
        player standing in their own front room."""
        for household, entry in sorted(LESSON.items()):
            with self.subTest(household=household):
                manual_id = entry.get("manual")
                self.assertIn(manual_id, MANUALS, f"{household}'s manual is not in this world")
                manual = MANUALS[manual_id]
                self.assertEqual(
                    0, int(manual.get("min_realm_index", 0)),
                    f"{household} is taught at creation, so its manual must be reachable at realm 0",
                )
                self.assertNotEqual(
                    "demonic", str(manual.get("alignment", "")).casefold(),
                    f"{household}'s manual is Demonic; manualForbidden refuses it and the child would pay karma for it",
                )
                self.assertEqual(
                    set(), FORBIDDEN_TAGS & {str(t).casefold() for t in (manual.get("tags") or ())},
                    f"{household}'s manual carries a forbidden tag",
                )
                self.assertIn(manual["item_id"], ITEMS, f"{household}'s manual has no item to hand over")

    def test_no_two_households_share_a_tradition(self):
        """Two houses with one manual read identically at the hearth, which is
        how `azure_cloud_foundation_sword_canon` came to be four households'
        "own" tradition at once."""
        seen: dict[str, list[str]] = {}
        for household, entry in LESSON.items():
            seen.setdefault(entry["manual"], []).append(household)
        shared = {m: sorted(hs) for m, hs in seen.items() if len(hs) > 1}
        self.assertEqual(shared, {}, f"one manual is several households' own tradition: {shared}")

    def test_every_household_teaches_at_the_same_grade(self):
        """The silent balance half. The grade is read off the first household
        rather than pinned to a literal, so the content may move it - what it
        may not do is differ between houses, because `manualCultivationMultiplier`
        makes that a permanent difference nothing tells the player about."""
        grades = {household: MANUALS[entry["manual"]]["grade"] for household, entry in LESSON.items()}
        expected = grades[next(iter(LESSON))]
        odd = {h: g for h, g in grades.items() if g != expected}
        self.assertEqual(
            odd, {},
            f"households teach at different grades (most are {expected}): {odd} - "
            "that is a permanent cultivation difference decided by birth and stated nowhere",
        )

    def test_the_sect_canons_are_still_in_the_world(self):
        """Re-pointing the households must never have retired the canons: they
        are the sect-inheritance redemption pool, and live characters hold them
        in `character_manuals` and in their bags. Retiring one would dangle both
        and silently drop their practised-method choice to the fallback."""
        for manual_id, manual in MANUALS.items():
            if str(manual.get("sect", "")).strip():
                with self.subTest(manual=manual_id):
                    self.assertIn(manual["item_id"], ITEMS)
        sect_canons = {m for m, d in MANUALS.items() if str(d.get("sect", "")).strip()}
        self.assertGreaterEqual(
            len(sect_canons), 12,
            "a sect canon was retired; a manual somebody may be practising is never removed",
        )


if __name__ == "__main__":
    unittest.main()
