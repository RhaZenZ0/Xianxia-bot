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

It found a second orphan the moment it stopped counting test files as sources -
`living_world_ring`, the top of the storage ladder, named only in
`support_storage_test.go`. rc.51 gives it the Weeping Wall Sanctum, and
`SOURCELESS_ITEMS` is empty again.
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
# Empty, and it was emptied by placing the one entry it ever held rather than by
# deleting it: an entry here is a new decision, never a backlog inherited from
# rc.50's.
SOURCELESS_ITEMS: dict[str, str] = {}


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

    def _only_room_holding(self, item: str) -> tuple[str, dict]:
        rooms = [(rid, room) for rid, realm in WORLD["secret_realms"].items()
                 for room in realm.get("rooms") or []
                 if item in (room.get("rare_items") or {})]
        self.assertEqual(len(rooms), 1, f"{item} is found in {len(rooms)} places, not one: {rooms}")
        return rooms[0]

    def _the_realm_takes_no_key(self, realm_id: str) -> None:
        # A realm is walked again on every run (`secret_realm_runs` is one row
        # per user and entering resets `room_index`), so a key on sale would
        # make a rare find a purchase, forever.
        keyed = {d["spatial_key"]["secret_realm_id"]
                 for d in ITEMS.values() if d.get("spatial_key")}
        self.assertNotIn(realm_id, keyed, f"{realm_id} sells a key, so its rare find is buyable")

    def test_the_peach_grows_somewhere(self):
        """Named outright, because it is the one this file exists for and a
        regression is silent: the item stays in the catalogue and nothing errors."""
        realm_id, room = self._only_room_holding("hundred_year_peach")
        realm = WORLD["secret_realms"][realm_id]
        self._the_realm_takes_no_key(realm_id)
        self.assertIs(room, realm["rooms"][-1], "it should be the last room, behind every other")
        self.assertEqual(room["tn"], max(r["tn"] for r in realm["rooms"]))
        # Fifty years is enormous low down and worthless high up.
        self.assertLess(int(realm["min_realm_index"]), 8, "the peach belongs in the Mortal World")

    def test_the_ring_is_found_in_the_immortal_world(self):
        """rc.51, and named for the same reason: the ring is the top of the
        storage ladder and the only container carrying `living_space`, so a
        placement quietly lost is a 40,000 treasure nothing can produce again."""
        realm_id, room = self._only_room_holding("living_world_ring")
        realm = WORLD["secret_realms"][realm_id]
        self._the_realm_takes_no_key(realm_id)
        self.assertIs(room, realm["rooms"][-1], "it should be the last room, behind every other")
        self.assertEqual(room["tn"], max(r["tn"] for r in realm["rooms"]))
        # The item's own `storage_upgrade.grade` names the world it belongs to,
        # and the Immortal World is realms 16-23 of the thirty-two.
        self.assertEqual(ITEMS["living_world_ring"]["storage_upgrade"]["grade"], "Immortal")
        self.assertTrue(16 <= int(realm["min_realm_index"]) <= 23,
                        f"{realm_id} opens at realm {realm['min_realm_index']}, not the Immortal World")

    def test_a_rare_find_is_rarer_the_more_it_is_worth(self):
        """Not a formula, a floor: the two placed finds are ordered by price, so
        a third dropped in at the peach's chance with the ring's price fails."""
        chances = {}
        for realm in WORLD["secret_realms"].values():
            for room in realm.get("rooms") or []:
                for item, spec in (room.get("rare_items") or {}).items():
                    chances[item] = int(spec["chance"])
        by_price = sorted(chances, key=lambda i: int(ITEMS[i].get("base_price") or 0))
        for cheaper, dearer in zip(by_price, by_price[1:]):
            self.assertGreaterEqual(chances[cheaper], chances[dearer], (
                f"{dearer} is worth more than {cheaper} and is no rarer "
                f"({chances[dearer]}% against {chances[cheaper]}%)"))


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


class ARealmsTreasureIsKept(unittest.TestCase):
    """v1.0.0-rc.54. The five upper-world realms each hold one find in their last
    room that grants **+1 to the attribute that room's own trial tests**, for
    good. `duration_game_minutes: 0` means "does not expire" - the writer stores
    NULL and every reader is `ends_game_minute IS NULL OR ends_game_minute > ?` -
    and until now **no item in the catalogue set it**: every effect the game
    shipped ran 120 to 360 minutes. The behavioural half is
    `permanent_treasure_test.go`, which drives a use and reads the row back a
    world-year later; this holds the shape of the content it reads.
    """

    def _treasures(self):
        for realm_id, realm in WORLD["secret_realms"].items():
            for item, spec in (realm["rooms"][-1].get("rare_items") or {}).items():
                use = ITEMS[item].get("use") or {}
                if use.get("effect"):
                    yield realm_id, realm, item, spec, use

    def test_there_are_treasures_to_check(self):
        self.assertGreaterEqual(len(list(self._treasures())), 5, "the realm treasures went missing")

    def test_each_is_permanent_rather_than_a_long_pill(self):
        for realm_id, _realm, item, _spec, use in self._treasures():
            with self.subTest(realm=realm_id, item=item):
                self.assertEqual(int(use.get("duration_game_minutes") or 0), 0,
                                 "a treasure that expires is a pill with a better price")

    def test_each_grants_one_point_of_the_attribute_its_trial_tested(self):
        """Stated once, so the prize and the trial cannot drift: what the last
        room asked of you is what the realm leaves you better at."""
        for realm_id, realm, item, _spec, use in self._treasures():
            with self.subTest(realm=realm_id, item=item):
                mods = use["effect"].get("modifiers") or []
                self.assertEqual(len(mods), 1, "one modifier, so the prize reads in one line")
                mod = mods[0]
                self.assertEqual(mod["stat"], realm["rooms"][-1]["attribute"])
                self.assertEqual(mod["operation"], "add")
                self.assertEqual(mod["value"], 1, "permanent, so it is one point and not three")

    def test_the_attribute_is_one_the_engine_actually_rolls(self):
        """A modifier on an invented stat is decoration: `applyStatModifiers` is
        reached with a canonical attribute name and with nothing else."""
        canonical = {"body", "agility", "spirit", "will", "insight", "presence"}
        for realm_id, _realm, item, _spec, use in self._treasures():
            with self.subTest(realm=realm_id, item=item):
                self.assertIn(use["effect"]["modifiers"][0]["stat"], canonical)

    def test_each_is_worth_taking_to_an_auction(self):
        for realm_id, _realm, item, _spec, _use in self._treasures():
            with self.subTest(realm=realm_id, item=item):
                self.assertEqual(ITEMS[item].get("auction_interest"), "legendary")
                self.assertGreater(int(ITEMS[item].get("door_event_chance") or 0), 0,
                                   "a legendary lot with no door risk never fires the system that reads it")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
