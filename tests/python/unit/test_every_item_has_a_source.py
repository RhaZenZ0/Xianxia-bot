"""Nothing in the catalogue is a thing the world cannot produce (v1.0.0-rc.50).

`hundred_year_peach` was authored complete and expensive - fifty years of
lifespan on `use.lifespan_years`, 12,000 base price, `auction_interest:
legendary`, `door_event_chance: 65` - and **no shop sold it, no recipe made it,
no realm room held it, no event granted it, and no line of Go or Python named
it**. One of 287 items.

Both halves of it worked. `item_use_actions.go` grants the fifty years;
`advanced_maintenance.go` reads `door_event_chance` to write an
`auction_door_risks` row when a legendary lot is struck - so *that* content had
never fired either, because you cannot auction a fruit that does not exist. One
missing wire kept two authored systems dark, which is the shape `/learn`
(rc.43), the quest journal (rc.46) and the event bands (rc.49) all had.

This is the standing sweep for the class. It is deliberately generous about
what counts as a source: anything that names the id at all, anywhere outside
the `items` block, or in code. A false negative here would be an item some
path grants in a way this file cannot see; a false *positive* - an orphan that
slips through - is the expensive direction, and naming a source is cheap.
"""
from __future__ import annotations

import json
import subprocess
import unittest

from tests.support import PROJECT_ROOT

WORLD = json.loads((PROJECT_ROOT / "content" / "world.json").read_text(encoding="utf-8"))
ITEMS = WORLD["items"]
# Everything in the content file except the item definitions themselves: a
# shop's `sells`, a recipe's output, a realm room's `items` or `rare_items`, an
# event's `player_reward`, an inheritance's token, a send-off's heirloom.
ELSEWHERE = json.dumps({k: v for k, v in WORLD.items() if k != "items"})

# An item that has no source, with the reason it is allowed none *for now*.
# One entry, and it is not a decision that anything is fine: the sweep found a
# second orphan the moment it stopped counting test files as sources, and where
# that one belongs is a content call rather than something to settle in a test.
SOURCELESS_ITEMS: dict[str, str] = {
    "living_world_ring": (
        "Found by this sweep at rc.50 and not yet placed. It is the top of the storage ladder - "
        "Immortal grade, 500 slots, the only container with `living_space` - worth 40,000, with a "
        "`door_event_chance` of 75 that has never fired for want of a ring to auction. Named only "
        "in `support_storage_test.go`, which is a test, not a way to obtain one. It wants a home "
        "of the same kind the peach got (a deep keyless realm's last room, at a low chance), and "
        "which realm is a content decision, not a test's to make."
    ),
}


def _named_in_code() -> set[str]:
    """Item ids production Go or Python mentions (a container, a hardcoded grant).

    `--include=*.go` with `--exclude=*_test.go` is load-bearing, and the drill
    is what found it: this file's own Go half names `hundred_year_peach`, so
    with tests in the grep the sweep called the peach sourced by the very test
    written to prove it had no source. A test naming an item is not a way to
    obtain one.
    """
    found = subprocess.run(
        ["grep", "-rhoF", "--include=*.go", "--include=*.py", "--exclude=*_test.go",
         "-f", "/dev/stdin", "go_core/internal", "app", "scripts"],
        input="\n".join(ITEMS), cwd=PROJECT_ROOT, capture_output=True, text=True)
    return set(found.stdout.split())


class EveryItemCanBeObtained(unittest.TestCase):
    def test_the_sweep_still_sees_the_catalogue(self):
        # The load-bearing half: if the content file moved or `items` were
        # renamed, every item would look sourced and this file would be noise.
        self.assertGreater(len(ITEMS), 250, "the item catalogue all but disappeared")
        self.assertIn("hundred_year_peach", ITEMS)
        self.assertIn("spirit_herb", ITEMS)

    def test_no_item_is_a_thing_the_world_cannot_produce(self):
        in_code = _named_in_code()
        orphans = sorted(
            item for item in ITEMS
            if ('"%s"' % item) not in ELSEWHERE
            and item not in in_code
            and item not in SOURCELESS_ITEMS)
        self.assertEqual(orphans, [], (
            "these items are authored and priced and nothing in the world can produce them - "
            "no shop, no recipe, no realm room, no event, no code. Give each one a source, or "
            f"name it in SOURCELESS_ITEMS with the reason it may have none: {orphans}"))

    def test_the_allowlist_names_real_items_and_says_why(self):
        for item, reason in SOURCELESS_ITEMS.items():
            with self.subTest(item=item):
                self.assertIn(item, ITEMS, f"SOURCELESS_ITEMS names {item!r}, which is not an item")
                self.assertGreaterEqual(len(reason.strip()), 20,
                                        f"SOURCELESS_ITEMS[{item!r}] needs a reason worth reading")

    def test_the_peach_grows_somewhere(self):
        """Named outright, because it is the one this file exists for and a
        regression is silent: the item stays in the catalogue and nothing errors."""
        rooms = [(rid, room) for rid, realm in WORLD["secret_realms"].items()
                 for room in realm.get("rooms") or []
                 if "hundred_year_peach" in (room.get("rare_items") or {})]
        self.assertEqual(len(rooms), 1, f"the peach grows in {len(rooms)} places, not one: {rooms}")
        realm_id, room = rooms[0]
        realm = WORLD["secret_realms"][realm_id]
        keyed = {d["spatial_key"]["secret_realm_id"]
                 for d in ITEMS.values() if d.get("spatial_key")}
        # A realm is walked again on every run, so a key on sale would make a
        # fifty-year fruit a 448-stone purchase, forever.
        self.assertNotIn(realm_id, keyed, "the peach grows in a realm whose key is on sale")
        self.assertIs(room, realm["rooms"][-1], "it should be the last room, behind every other")
        self.assertEqual(room["tn"], max(r["tn"] for r in realm["rooms"]))


class ARareFindIsShapedLikeTheRosterItCopies(unittest.TestCase):
    """`rare_items` borrows `forage_materials`' shape so the tree has one idea
    of what a find chance looks like. These hold that it stays that shape."""

    def _rare(self):
        for realm_id, realm in WORLD["secret_realms"].items():
            for room in realm.get("rooms") or []:
                for item, spec in (room.get("rare_items") or {}).items():
                    yield realm_id, room["name"], item, spec

    def test_every_rare_find_names_a_real_item_at_a_sane_chance(self):
        seen = 0
        for realm_id, room_name, item, spec in self._rare():
            seen += 1
            with self.subTest(realm=realm_id, room=room_name, item=item):
                self.assertIn(item, ITEMS, "a rare find nothing in the catalogue carries")
                self.assertTrue(1 <= int(spec["chance"]) <= 100, f"chance={spec['chance']}")
                self.assertGreaterEqual(int(spec["max"]), 1, "a find that can never be one of")
        self.assertGreater(seen, 0, "no room carries a rare find; the mechanism is dead content")

    def test_a_rare_find_is_rare(self):
        for realm_id, room_name, item, spec in self._rare():
            with self.subTest(realm=realm_id, room=room_name, item=item):
                self.assertLessEqual(int(spec["chance"]), 25, (
                    "at this chance it is an ordinary room item with extra steps - put it in "
                    "`items` instead, where a reader expects a guaranteed payout"))

    def test_the_engine_reads_the_field(self):
        """Content nothing reads is how `base_ratio` sat dead for twenty releases.
        The behavioural half is `salt_king_peach_test.go`, which drives the real
        action; this only holds that it is on file."""
        gate = PROJECT_ROOT / "go_core" / "internal" / "game" / "salt_king_peach_test.go"
        self.assertTrue(gate.exists(), "nothing proves the engine honours a rare find")
        source = gate.read_text(encoding="utf-8")
        self.assertIn("secret_realm.explore", source, "the gate stopped driving the real action")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
