"""Every quest that is seeded can be given to somebody (v1.0.0-rc.45).

rc.26 found `first_steps`: a quest seeded into `quest_definitions` on every
boot since v0.23.1, listed in `/quests`, with exactly the right objectives, and
handed to nobody - because the only two statements that wrote a
`character_quests` row both wanted a `giver_npc` the static quests deliberately
do not have. It built `beginner_path` to fix that.

It fixed it for the beginner path. `road_to_a_sect` was seeded from the same
dictionary, by the same call, and went on reaching nobody for nineteen more
releases - the string appeared in exactly one place in the whole tree, its own
definition - and `first_steps` itself was left seeded beside the stage that
replaced it. So the fault was found, named, and half fixed, and nothing said so.

That is the shape `tests/python/unit/test_commands_reach_a_player.py` gates on
the command side: a thing that exists, is complete, and has no door. This is the
same gate for quests. Every key that reaches `quest_definitions` must be
reachable by one of the paths that can put it in a player's hands:

  - a commission, which an NPC offers in person (it carries a `giver_npc`);
  - a `beginner_path` stage, granted at creation or as the previous stage's
    `follow_on`;
  - a household errand, handed over one at a time by `family.errand`;
  - an ascension quest, handed over by a cleared world-crossing tribulation;
  - a profession examination, offered when a trade's rank is reached;
  - anything named as some other quest's `follow_on`.

`UNGRANTABLE_QUESTS` is empty and was empty the day it was written, because
retiring `first_steps` and chaining `road_to_a_sect` left no orphan. An entry
here is a new decision, never a backlog inherited from this one.
"""
from __future__ import annotations

import json
import unittest

from app.rules.quests import (
    QUEST_DEFINITIONS,
    ascension_quest_seed_rows,
    beginner_path_seed_rows,
    household_errand_seed_rows,
    profession_exam_seed_rows,
    static_quest_seed_rows,
)
from tests.support import PROJECT_ROOT

WORLD_FILE = PROJECT_ROOT / "content" / "world.json"
BOT = PROJECT_ROOT / "app" / "bot" / "bot.py"

# A seeded quest that deliberately reaches nobody, with the reason it may. An
# entry naming a quest that is not seeded, or one that is in fact grantable,
# fails below - so this can only shrink honestly.
UNGRANTABLE_QUESTS: dict[str, str] = {}


def _world() -> dict:
    return json.loads(WORLD_FILE.read_text(encoding="utf-8"))


def _seeded_keys() -> dict[str, str]:
    """Every quest key the bot seeds, and which seeder put it there.

    Read through the seeders themselves rather than off the content file, so a
    roster the bot stops seeding stops being checked and a roster it starts
    seeding is checked the day it is added.
    """
    world = _world()
    shim = type("W", (), {"data": world})()
    seeded: dict[str, str] = {}
    for source, rows in (
        ("commission", list(world.get("commissions") or [])),
        ("static", static_quest_seed_rows(QUEST_DEFINITIONS)),
        ("beginner_path", beginner_path_seed_rows(shim)),
        ("household_errand", household_errand_seed_rows(shim)),
        ("ascension", ascension_quest_seed_rows(shim)),
        ("profession_exam", profession_exam_seed_rows(shim)),
    ):
        for row in rows:
            key = str(row.get("quest_key") or "").strip()
            if key:
                seeded[key] = source
    return seeded


def _grantable_keys() -> set[str]:
    """Every quest key some path can actually hand to a player."""
    world = _world()
    shim = type("W", (), {"data": world})()
    grantable: set[str] = set()
    # A commission is offered in person; the giver is what makes it offerable.
    for row in list(world.get("commissions") or []):
        if str(row.get("giver_npc") or "").strip():
            grantable.add(str(row.get("quest_key") or ""))
    # The rosters whose whole purpose is to be handed over.
    for rows in (beginner_path_seed_rows(shim), household_errand_seed_rows(shim),
                 ascension_quest_seed_rows(shim), profession_exam_seed_rows(shim)):
        for row in rows:
            grantable.add(str(row.get("quest_key") or ""))
    # And anything a chain points at, wherever the chain lives. `follow_on` is
    # read off `seed_json` at runtime, so a stage pointing at a static quest
    # gives that quest a door.
    for rows in (beginner_path_seed_rows(shim), household_errand_seed_rows(shim),
                 ascension_quest_seed_rows(shim), profession_exam_seed_rows(shim)):
        for row in rows:
            follow_on = str(dict(row.get("seed") or {}).get("follow_on") or "").strip()
            if follow_on:
                grantable.add(follow_on)
    grantable.discard("")
    return grantable


class EverySeededQuestHasADoor(unittest.TestCase):
    def test_the_seeders_are_still_where_this_test_thinks_they_are(self):
        # The load-bearing half: if the seeders silently returned nothing,
        # every quest would look grantable and the gate would be noise rather
        # than a gate.
        seeded = _seeded_keys()
        self.assertGreater(len(seeded), 20, "the seeders returned almost nothing")
        self.assertIn("beginner_household", seeded)
        self.assertIn("road_to_a_sect", seeded)
        bot = BOT.read_text(encoding="utf-8")
        for call in ("static_quest_seed_rows(QUEST_DEFINITIONS)", "beginner_path_seed_rows(WORLD)",
                     "household_errand_seed_rows(WORLD)", "ascension_quest_seed_rows(WORLD)",
                     "profession_exam_seed_rows(WORLD)"):
            with self.subTest(call=call):
                self.assertIn(call, bot, "a seeder nothing calls seeds nothing")

    def test_no_seeded_quest_reaches_nobody(self):
        seeded = _seeded_keys()
        grantable = _grantable_keys()
        orphans = sorted(key for key in seeded if key not in grantable and key not in UNGRANTABLE_QUESTS)
        self.assertEqual(orphans, [], (
            "these quests are seeded into quest_definitions and no path can hand them to a player - "
            "they carry no giver, they are on no roster that grants, and nothing chains to them. "
            "Give each one a door (a giver, a roster, or another quest's follow_on), or name it in "
            f"UNGRANTABLE_QUESTS with the reason it is allowed to reach nobody: {orphans}"))

    def test_the_sect_road_is_the_one_this_gate_exists_for(self):
        # Named outright rather than left to the sweep above, because this is
        # the quest the gate was written for and a regression is silent: the
        # definition goes on being seeded and nothing errors.
        world = _world()
        last = [s for s in list(world.get("beginner_path") or [])][-1]
        self.assertEqual(str(last.get("follow_on")), "road_to_a_sect",
                         "the last beginner stage is what hands over the sect road")
        self.assertIn("road_to_a_sect", QUEST_DEFINITIONS)

    def test_the_superseded_onboarding_quest_is_gone(self):
        # `first_steps` and `beginner_household` were two definitions for one
        # moment, and only one of them could be held.
        self.assertNotIn("first_steps", QUEST_DEFINITIONS)
        self.assertNotIn("first_steps", _seeded_keys())
        core = (PROJECT_ROOT / "app" / "database" / "core.py").read_text(encoding="utf-8")
        self.assertIn("point_the_last_beginner_stage_at_the_sect_road", core,
                      "a live world's rows need the migration; the content change is insert-only")

    def test_the_allowlist_names_seeded_quests_and_says_why(self):
        seeded = _seeded_keys()
        grantable = _grantable_keys()
        for key, reason in UNGRANTABLE_QUESTS.items():
            with self.subTest(quest=key):
                self.assertIn(key, seeded, f"UNGRANTABLE_QUESTS names {key!r}, which nothing seeds")
                self.assertNotIn(key, grantable, f"UNGRANTABLE_QUESTS names {key!r}, which is grantable")
                self.assertGreaterEqual(len(reason.strip()), 20,
                                        f"UNGRANTABLE_QUESTS[{key!r}] needs a reason worth reading")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
